---
experiment_id: "REPLACE_ME"
sleeve: "전략군을 기입"
hypothesis: "검증할 가설을 기입"
counterparty: "수익의 원천과 돈을 주는 쪽을 기입"
data_periods:
  design: {from: "20230101", to: "20241231"}
  validation: {from: "20250101", to: "20251231"}
  holdout: {from: "20260101", to: "20260630"}
  forward: {from: "20261001", to: "종료일을 사전에 기입"}
metrics:
  primary: "핵심 지표, 계산법, 관측 단위를 기입"
  secondary: ["보조 지표를 기입"]
pass_criteria:
  design_validation:
    net_performance_gt: 0
    core_t_stat_gt: 2
    core_t_stat_gt_when_trials_gt_20: 3
    random_control_top_percent: 5
    minimum_observations: {events: 300, rebalances: 36, rule: "둘 중 해당 관측 단위 충족"}
    net_performance_at_cost_multiplier_1_5_gt: 0
  holdout:
    same_direction: true
    net_performance_gt: 0
    minimum_fraction_of_design_effect: 0.5
  llm:
    comparison: "같은 전략의 규칙 특징값만 쓴 버전 대비 개선이 위 기준 충족"
    evaluation_data: "학습 시점 이후·전진 데이터로만 판정"
variants_planned: 1
uses_llm: false
uses_holdout: false
---

# 사전 등록

실험 ID를 디렉터리 이름과 맞추고 안내용 문구를 구체적인 내용으로 바꾸세요.
실행 전에 커밋하며, 첫 커밋 이후 사전 등록은 변경할 수 없습니다. 수정 후 재커밋도
허용하지 않습니다. 계획을 바꾸려면 새 `experiment_id`로 새 사전 등록 파일을 만드세요.
로드맵 3.4절의 판정 수치는 그대로 유지합니다.

## 설계와 예상 비용

- 대상 종목군, 의사결정 시각, 체결 가정, 관측 단위와 표본 수 산출법을 적으세요.
- 계획한 변형을 모두 나열하고 `variants_planned`에 개수를 적으세요.
- 수수료, 연도별 매도세, 1틱 스프레드, 거래대금 구간 슬리피지와 예상 왕복 비용을 적으세요.
- 비용 1배·1.5배·2배 및 같은 분포의 무작위 대조군 30개 이상을 명시하세요.
  배수는 수수료·스프레드·슬리피지에만 적용하고 매도세는 그대로 더합니다.
- 모델·프롬프트·데이터 버전을 고정하세요. LLM을 쓰면 `uses_llm: true`와
  학습 시점 및 전진 평가 계획을 적으세요.
- 보류 구간을 사용할 때만 `uses_holdout: true`로 등록하고, 한 번의 평가에서
  확인할 결과를 미리 확정하세요. 실패와 중단도 대장에 기록합니다.
