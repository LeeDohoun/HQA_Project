---
experiment_id: "HC002_operating_leverage_monthly"
sleeve: "헤게모니 ② 기업 헤게모니 (영업 레버리지 국면, 월 단위)"
hypothesis: "매월 말 시점에 공시된 최신 분기에서 매출이 전년 동기 대비 늘고 영업이익 증가율이 매출 증가율보다 높은 기업(2국면)은 이후 20거래일 동안 같은 유니버스의 동일가중 평균보다 수익률이 높다."
counterparty: "HC001과 같다. 가격 협상력이 이익에 반영되는 속도를 늦게 따라가는 투자자."
data_periods:
  design: {from: "20170501", to: "20241231"}
  validation: {from: "20250101", to: "20251130"}
  holdout: {from: "20260101", to: "20260630", used: false}
  forward: {from: "20261001", to: "사용하지 않음"}
metrics:
  primary: "월말마다 2국면 기업 동일가중 포트폴리오의 다음 20거래일 비용 차감 수익률 − 같은 기간 유니버스 동일가중 수익률(비용 차감 전). 관측 단위는 월. t-통계는 월별 초과수익으로 계산"
  secondary:
    - "연속 점수(영업이익 YoY − 매출 YoY, 매출 YoY > 0인 기업)의 월별 순위 IC"
    - "1·3·4국면 포트폴리오의 같은 초과수익"
    - "보유 종목 유지분을 반영한 실제 회전율 기준 비용 결과 (판정에는 쓰지 않음)"
    - "유동성 구간 4개별, 연도별, 비용 배수 1.5배·2배, 용량"
pass_criteria:
  design_validation:
    net_performance_gt: 0
    core_t_stat_gt: 2
    core_t_stat_gt_when_trials_gt_20: 3
    random_control_top_percent: 5
    minimum_observations: {events: 300, rebalances: 36, rule: "월별 관측 36회 이상 (약 100회 예상)"}
    net_performance_at_cost_multiplier_1_5_gt: 0
  holdout:
    same_direction: true
    net_performance_gt: 0
    minimum_fraction_of_design_effect: 0.5
  llm:
    comparison: "해당 없음 (LLM 미사용)"
    evaluation_data: "해당 없음"
variants_planned: 2
uses_llm: false
uses_holdout: false
---

# 사전 등록: HC002 기업 헤게모니 (월 단위)

## 등록 사유 (HC001 결과를 보기 전에 작성)

HC001은 분기 리밸런싱(연 4회)으로 설계했다. 그런데 2026-10-05에 확인한 결과, OpenDART의 주요계정 API(단일회사·다중회사 모두)가 2015년 1~3분기 보고서에 대해 "조회된 데이터가 없습니다"(013)를 돌려준다. 그래서 2016년 분기의 전년 대비 증가율과 2015년 4분기 단독 값을 만들 수 없고, 신호를 계산할 수 있는 HC001 리밸런싱은 약 34회로 최소 기준(36회)에 못 미칠 가능성이 높다.

같은 가설을 데이터 범위 안에서 충분한 관측으로 검증하기 위해 월 단위 버전을 HC001 실행 **전에** 등록한다. 이 등록은 HC001 결과와 무관하며, HC001도 원래 사전 등록대로 실행해 결과를 그대로 기록한다.

## HC001과 같은 것

신호 정의(분기 단독 값, YoY 관례 −200%/500%, 2국면 = 매출 YoY > 0 그리고 영업이익 YoY > 매출 YoY, 국면 우선순위 2 → 3 → 1 → 4), 시점 규칙(의사결정일 전 거래일까지 접수된 최신 버전), 대상 종목(보통주, 1,000원 이상, 20거래일 평균 거래대금 1억 원 이상, 금융업 제외 규칙), 비용 모델, 상한가·청산·상장폐지 처리, 대조군 방식(같은 수의 무작위 종목 200개), 판정 기준.

## HC001과 다른 것

- **의사결정일**: 매월 마지막 거래일 장 마감 후. 2017년 5월부터 2025년 11월까지 (2025년 12월은 20거래일 보유가 2026년에 닿아 제외).
- **진입·청산**: 다음 거래일 시가 진입, 진입일을 1일째로 세어 20번째 거래일 종가 청산.
- **비용**: 판정에는 매월 전부 교체한다고 보는 보수적 가정(왕복 비용 전액)을 쓴다. 이전 달에서 유지된 종목의 비용을 빼는 실제 회전율 기준 결과는 보조 지표로만 보고한다.

## 변형 (2개)

| ID | 내용 |
|---|---|
| `phase2_ew` | 2국면 기업 동일가중 vs 유니버스 동일가중 (판정 대상) |
| `score_ic` | 연속 점수의 월별 순위 IC (보조) |

## 판정

설계+검증(2017-05 ~ 2025-11) 합산으로 위 기준을 적용하고, 검증 기간(2025년) 월별 초과수익 평균이 설계 구간과 같은 부호여야 한다.

## 데이터 버전

- DART 분기 재무: `data/fundamentals/dart_quarterly/` (2015 ~ 2026, 2015년 1~3분기는 제공되지 않음)
- KRX 일봉: `data/market/krx_daily/`
- 코드: 이 문서를 커밋한 시점의 `src/ingestion/dart_quarterly.py`, `backtesting/experiments/hc001.py`
