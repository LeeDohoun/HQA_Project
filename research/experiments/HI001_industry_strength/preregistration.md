---
experiment_id: "HI001_industry_strength"
sleeve: "헤게모니 ① 산업 헤게모니 (가격 기반 상대강도·확산도)"
hypothesis: "월말 기준 시장 대비 상대강도와 확산도(구성 종목 중 20일 이동평균 위 비율)가 높은 업종은 다음 20거래일 동안 시장보다 높은 수익률을 낸다."
counterparty: "산업 단위의 수요·공급 변화가 가격에 퍼지는 속도를 늦게 따라가는 투자자. 산업 모멘텀이 종목 모멘텀보다 오래 지속된다는 기존 연구가 이 실험의 근거다."
data_periods:
  design: {from: "20160101", to: "20241231"}
  validation: {from: "20250101", to: "20251231"}
  holdout: {from: "20260101", to: "20260630", used: false}
  forward: {from: "20261001", to: "사용하지 않음"}
metrics:
  primary: "월말마다 점수 상위 3개 업종(시가총액가중 업종 지수, 3개 업종 동일가중)의 다음 20거래일 비용 차감 수익률 − 전체 시장 시가총액가중 지수 수익률. 관측 단위는 월. t-통계는 월별 초과수익으로 계산"
  secondary:
    - "업종 점수와 다음 20거래일 업종 초과수익의 월별 순위 IC"
    - "상위 1개, 상위 5개 업종 포트폴리오"
    - "동일가중 업종 지수 기준 결과"
    - "연도별 결과, 시장 약세 구간(전체 시장 지수가 120거래일 이동평균 아래)과 강세 구간 분리 결과"
    - "비용 배수 1.5배, 2배"
pass_criteria:
  design_validation:
    net_performance_gt: 0
    core_t_stat_gt: 2
    core_t_stat_gt_when_trials_gt_20: 3
    random_control_top_percent: 5
    minimum_observations: {events: 300, rebalances: 36, rule: "월별 관측 36회 이상 (설계+검증 합산 120회 예상)"}
    net_performance_at_cost_multiplier_1_5_gt: 0
  holdout:
    same_direction: true
    net_performance_gt: 0
    minimum_fraction_of_design_effect: 0.5
  llm:
    comparison: "해당 없음 (LLM 미사용)"
    evaluation_data: "해당 없음"
variants_planned: 3
uses_llm: false
uses_holdout: false
---

# 사전 등록: HI001 산업 헤게모니 (상대강도·확산도)

아이디어 출처: [투자의 정석 2편](../../hegemony_reference/vol2_2016_03.md)의 2국면 체크포인트 "업종 지수 수익률 > 시장, 업종 내 시가총액 상위 기업의 상승". 그 문서에는 체계적 검증이 없어 그대로 따르지 않고 아래 규칙으로 고정한다.

## 실행 전 필요 조건

1. 업종 분류: DART 기업개황의 표준산업분류(KSIC) 코드 → `src/research/industry_map.py`의 투자용 업종 묶음
2. KRX 전종목 일봉 2015-01 ~ 2026-01
3. 업종 지수: `src/research/industry_index.py` (전일 시가총액 가중, `ret_1d` 연쇄, 보통주만, 20거래일 평균 거래대금 1억 원 이상)

## 업종 특징값 (월 마지막 거래일 장 마감 기준, 그날까지의 데이터만)

- `rs60`: 업종 지수 60거래일 수익률 − 전체 시장 지수 60거래일 수익률
- `breadth`: 구성 종목 중 종가가 20거래일 이동평균 위인 비율
- `topcap`: 구성 종목 중 시가총액 상위 5개의 60거래일 수익률 중앙값 − 전체 시장 지수 60거래일 수익률
- 구성 종목이 10개 미만이거나 "미분류", "지주회사" 업종은 제외

## 변형 (3개, 사전 고정)

| ID | 점수 |
|---|---|
| `rs` | `rs60` 순위 |
| `rs_breadth` | `rs60` 순위와 `breadth` 순위의 평균 |
| `rs_breadth_topcap` | `rs60`, `breadth`, `topcap` 순위의 평균 |

**선택 규칙**: 설계 구간에서 주 지표 t-통계가 가장 높은 변형 하나로 판정한다. 세 변형 모두 대장에 기록한다.

## 포트폴리오

- **진입**: 월말 다음 거래일 시가. **보유**: 진입일 포함 20거래일, 종가 청산. 업종 지수 수익률로 계산한다 (업종 바스켓을 시가총액 비중으로 보유한다고 가정).
- **비용**: 업종을 바꿀 때만 비용을 낸다. 상위 3개에서 빠지거나 새로 들어온 업종의 비중만큼, 그 업종 구성 종목의 시가총액가중 평균 왕복 비용(비용 모델)을 차감한다. 업종이 유지되면 업종 안 비중 조정 비용은 무시한다 (기록해 둔 단순화).
- **수익률**: 수정 수익률, 상장폐지 종목은 마지막 거래 가격까지 반영.

## 판정

1. 설계 구간(2016~2024)에서 세 변형 계산 → 선택 규칙 적용
2. 선택된 변형을 설계+검증(2016~2025) 합산으로 평가해서 위 기준 적용
3. 검증 연도(2025) 월별 초과수익 평균이 설계와 같은 부호

## 대조군

- 매월 업종 3개를 무작위로 뽑은 포트폴리오 200개 (같은 비용 규칙). 실제 초과수익 평균이 대조군 상위 5% 안이어야 한다.

## 알려진 제한

- 업종 분류는 현재 시점 기준이다. 과거에 업종을 바꾼 기업은 현재 업종으로 분류된다.
- 업종 수가 20~30개라 순위 IC의 관측 폭이 좁다. 주 판정은 상위 3개 업종 포트폴리오로 한다.
- 시장 국면 분리 결과는 보조 지표이며, 국면 필터 자체의 효과는 별도 실험으로 검증한다.

## 데이터 버전

- 업종 분류: `data/reference/dart_company/companies.jsonl` (2026-10-05부터 수집)
- KRX 일봉: `data/market/krx_daily/`
- 코드: 이 문서를 커밋한 시점의 `src/research/industry_map.py`, `src/research/industry_index.py`, `backtesting/cost_model.py`
