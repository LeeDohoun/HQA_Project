---
experiment_id: "B001_disclosure_category"
sleeve: "B 이벤트 (공시 유형, 규칙 기준선)"
hypothesis: "코스피·코스닥 보통주의 DART 공시 유형별로 공시 다음 거래일 시가 진입 후 5거래일 시장 대비 초과수익이 0과 다르며, 아래에 미리 적은 방향을 따른다."
counterparty: "공시 정보를 늦게 반영하는 투자자. 양의 유형은 매수 신호 후보, 음의 유형은 매수 제외 필터 후보로 쓴다."
data_periods:
  design: {from: "20230101", to: "20241231"}
  validation: {from: "20250101", to: "20251231"}
  holdout: {from: "20260101", to: "20260630", used: false}
  forward: {from: "20261001", to: "사용하지 않음"}
metrics:
  primary: "유형별 이벤트 단위 5거래일 시장 대비 초과수익 (다음 거래일 시가 진입, 5번째 거래일 종가). 날짜 군집 t-통계 (진입일별 평균 후 t). 양의 유형은 비용 차감 순초과수익, 음의 유형은 비용 차감 전 초과수익"
  secondary:
    - "1, 3, 20거래일 초과수익 (판정에 쓰지 않음)"
    - "유동성 구간 4개별 결과"
    - "연도별 결과"
    - "비용 배수 1.5배, 2배에서의 순초과수익 (양의 유형)"
pass_criteria:
  design_validation:
    net_performance_gt: 0
    core_t_stat_gt: 2
    core_t_stat_gt_when_trials_gt_20: 3
    random_control_top_percent: 5
    minimum_observations: {events: 300, rebalances: 36, rule: "유형별 이벤트 300건 이상 (설계 구간). 미달 유형은 관측 부족으로 판정"}
    net_performance_at_cost_multiplier_1_5_gt: 0
  holdout:
    same_direction: true
    net_performance_gt: 0
    minimum_fraction_of_design_effect: 0.5
  llm:
    comparison: "해당 없음 (LLM 미사용)"
    evaluation_data: "해당 없음"
variants_planned: 8
uses_llm: false
uses_holdout: false
---

# 사전 등록: B001 공시 유형별 이벤트 연구

## 실행 전 필요 조건

1. KOSPI·KOSDAQ 지수 일별 종가 2022-12 ~ 2026-01 (KRX 지수 API 백필, 초과수익 기준)
2. 이벤트 표는 이미 수집한 전종목 DART 목록(`data/disclosures/dart_full/list/`)만으로 만든다. 본문은 필요 없다.

## 이벤트 정의

- **원천**: DART 공시 목록 (코스피 `Y`, 코스닥 `K`), 2023-01-01 ~ 2025-12-31 접수분
- **유형 분류**: `src/runner/event_evidence.py`의 `_category` 규칙을 공시 제목(`report_nm`)에 그대로 적용
- **제외**: 정정 공시(제목에 `정정`), 철회 공시, 스팩과 우선주, 종목코드가 없는 공시, 진입일에 종가 1,000원 미만 또는 20거래일 평균 거래대금 1억 원 미만인 종목
- **중복 제거**: 같은 종목·같은 접수일·같은 유형은 이벤트 1건으로 본다 (가장 이른 접수번호)
- **진입 시점**: 과거 공시는 날짜만 있으므로 **접수일 다음 거래일 시가** (`event_study`의 날짜 전용 규칙)
- **청산**: 진입일을 1일째로 세어 5번째 거래일 종가. 상한가 시가 진입 제외, 청산일 거래 없음은 마지막 거래 가격, 이후 가격 없음은 제외하고 건수 보고
- **초과수익**: 종목 수익률 − 소속 시장 지수 수익률 (`event_study` 근사식), 수정 수익률 사용

## 유형별 사전 방향 (8개 변형)

| 유형 | 예상 방향 | 쓰임 |
|---|---|---|
| `contract` 공급계약 | + | 매수 신호 후보 |
| `buyback` 자사주 | + | 매수 신호 후보 |
| `dividend` 배당 | + | 매수 신호 후보 |
| `earnings` 실적 | 방향 미정 (양쪽 검정) | 다음 실험(B4)의 기준 정보 |
| `capital_raise` 유상·무상증자 | − | 제외 필터 후보 |
| `convertible_bond` 전환사채 등 | − | 제외 필터 후보 |
| `regulatory_risk` 규제 위험 | − | 제외 필터 후보 |
| `merger` 합병·분할 | 방향 미정 (양쪽 검정) | 참고 |

## 유형별 판정

- **양의 유형**: 설계+검증 합산 순초과수익(비용 차감) > 0, 날짜 군집 t > 2, 무작위 대조군 상위 5%, 비용 1.5배에서도 > 0, 검증 연도 같은 부호 → **매수 신호 후보 통과**
- **음의 유형**: 설계+검증 합산 초과수익(비용 차감 전) < 0, t < −2, 무작위 대조군 하위 5%, 검증 연도 같은 부호 → **제외 필터 후보 통과**. 피하는 것이므로 거래비용은 차감하지 않는다.
- **방향 미정 유형**: |t| > 2와 대조군 기준을 충족할 때만 방향을 기록하고, 이 실험에서는 신호로 채택하지 않는다.
- 8개 유형 각각을 대장에 별도 시도로 기록한다 (변형 8개, 20개 이하이므로 t > 2 기준).

## 대조군

- 각 이벤트의 진입일을 유지하고, 그날 거래 가능한 종목에서 무작위로 고른 가짜 이벤트 200세트. 같은 청산·비용 규칙 적용.

## 비용

- D001과 같은 비용 모델 (2023~2025 세율 0.20%, 0.18%, 0.15%), 비용 배수 1배, 1.5배, 2배

## 데이터 버전

- DART 목록: `data/disclosures/dart_full/list/` (2026-10-05 수집, 1,373일, 약 45만 3천 건)
- KRX 일봉: `data/market/krx_daily/` (2026-10-04 ~ 05 수집)
- 코드: 이 문서를 커밋한 시점의 `backtesting/event_study.py`, `src/runner/event_evidence.py`
