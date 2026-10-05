# 실험 사전 등록과 기록

1. `TEMPLATE/preregistration.md`를 `<실험ID>/preregistration.md`로 복사하고
   가설, 데이터 구간, 변형 수, 예상 비용을 실험 전에 확정합니다. YAML의 실험 ID와
   디렉터리 이름은 같아야 하며 `uses_llm`, `uses_holdout`은 불리언입니다.
2. 사전 등록 파일을 실행 전에 Git에 커밋합니다. **첫 커밋 이후에는 변경할 수 없습니다.**
   작업 트리·스테이징의 수정뿐 아니라 수정 후 재커밋한 파일도 거부됩니다.
   `git log --follow`로 이름 변경 전 이력까지 추적하여 커밋이 정확히 하나인지 검사합니다.
   계획을 바꾸려면 새 `experiment_id`로 새 사전 등록 파일을 만드세요.
3. 성공·실패를 포함한 모든 시도를 `record_trial`로 추가합니다. 기존 행은 지우거나
   수정하지 않습니다. 같은 변형을 다시 실행해도 별도 시도로 셉니다.
   같은 `experiment_id`의 기존 행 중 `prereg_commit`이 현재 사전 등록 커밋과 다른
   행이 있으면 추가 기록을 거부합니다.

```python
from backtesting.experiment_registry import record_trial, required_t_stat, verify_preregistration

registration = verify_preregistration("D001")
record_trial("D001", "baseline", {"net_return": -0.01}, "fail", note="비용 차감 후 음수")
threshold = required_t_stat("D001")  # 누적 20회 이하는 2.0, 21회부터 3.0; t는 이를 초과해야 함
```

보류 구간은 **2026-01-01~2026-06-30, 양 끝 포함**입니다. `guard_period`를 데이터
조회 전에 호출합니다. 구간이 겹치지 않으면 사전 등록이나 원장 없이 통과합니다.
겹치면 커밋된 `uses_holdout: true` 사전 등록과 아직 사용하지 않은 실험 ID가 필요하며,
허용 즉시 `holdout_ledger.jsonl`에 구간·ID·UTC 시각을 추가합니다.

```python
from backtesting.holdout import HoldoutSession, guard_period

guard_period("20230101", "20251231")
with HoldoutSession("D001") as session:
    session.guard_period("20260101", "20260331")
    session.guard_period("20260401", "20260630")
```

세션 진입은 전체 보류 구간의 1회 사용을 기록합니다. 열린 세션 안에서는 같은 실험을
반복 조회할 수 있지만, 종료·예외 발생 후에는 다시 열 수 없습니다. 세션 없이 겹치는
구간을 조회하면 그 호출 자체가 1회를 소비합니다. 최초 데이터 조회 전에 세션을 여세요.
날짜 입력은 `YYYYMMDD` 문자열 또는 `datetime.date`입니다. 이 모듈은 접근 허가 장치이며
기존 데이터 로더에 자동 연결되지는 않습니다. 새 조회 경로가 반드시 호출해야 합니다.

`repo_root`, `registry_path`, `ledger_path`를 주입할 수 있습니다. 상대 경로는
`repo_root` 기준입니다. 테스트는 임시 저장소를 쓰고 실제 실험은 같은 대장과 원장을
계속 사용하세요. 새 대장은 첫 기록에 헤더를 생성하며, 손상된 기존 기록은 오류입니다.
잠금은 Linux/WSL의 `flock`을 사용합니다.

비용은 `backtesting.cost_model`의 `one_way_cost`, `round_trip_cost`가 비율로 반환합니다.
왕복은 동일 가격·거래일·유동성에서 매수와 매도를 합산합니다. `CostConfig`의
`commission_rate`, `slippage_rates`로 요율을 설정하며 슬리피지 네 값은 거래대금
1억 미만 / 1~10억 / 10~100억 / 100억 이상 순서입니다. `multiplier=1.5`는
수수료·스프레드·슬리피지에만 적용되고 매도세는 배수 없이 더합니다.
연도별 세율표 밖의 거래일은 거부됩니다.
`CostConfig.model_version` 또는 비용 함수의 `model_version`으로 버전을 명시합니다.
기본 `v1`은 모든 날짜에 2023-01-25 통합 호가표, 거래일 기준 매도세를 적용하며
기존 출력을 그대로 보존합니다. 현재 커밋된 HC001/HC002/HF001/R001/D001/B001
사전등록의 기본 비용과 판정은 계속 v1입니다.
`v2`는 시장(KOSPI/KOSDAQ)과 실제 거래일이 필수이며, 2023-01-25 이전의 시장별
호가표와 XKRX T+2 결제일 기준 매도세를 적용합니다. 벡터 입력도 전체 거래일을
사용해야 합니다. 왕복의 동일 거래일 가정은 유지하므로 실제 청산일 과세를 재현하는
별도 체결 모델은 아닙니다. HC002/HF001/R001의 `cost_model_v2`는 동일 포트폴리오를
비용 1/1.5/2배로 재계산하는 사후등록 보조 진단이며 판정·시도 수에 사용하지 않습니다.
v2를 주 비용으로 쓰는 커밋된 사전등록은 없으며, 새 주 분석에는 새 사전등록이 필요합니다.
`src.research.market_regimes`의 제도 구간 및 공매도 구간별 주 지표도 보고용 분할이며
전략 선정이나 판정에 사용하지 않습니다.

판정 수치는 [로드맵 3.4절](../../docs/strategy-research-roadmap.md#34-연구-판정-기준)을
따릅니다. 비용·보류 장치와 시도 수 임계값은 전체 성과 판정을 대신하지 않습니다.
