# 연구용 재현 스크립트

2026년 5~6월 `ai-data-main`에서 수행한 멀티 에이전트 구성 실험을 다시 실행하거나 집계하는 도구입니다. 모두 프로젝트 루트에서 `venv/bin/python scripts/research/<이름>.py --help`로 옵션을 확인할 수 있습니다. 주문이나 계좌 코드를 호출하지 않으며, `backtesting/proof_validation.py`와 `backtesting/technical_baseline.py`를 하위 프로세스로 반복 실행합니다.

원본 실험 결과(`experiment_results/backtesting/agent_architecture_validation/`, 약 730MB)는 실험을 수행한 컴퓨터에만 있으며 저장소에 넣지 않습니다. 이 스크립트는 연구용이며 현재 Luna PAPER 운영 흐름의 일부가 아닙니다.

## 비용과 전제

- `AGENT_SCORE_CACHE_ONLY=1`로 실행되는 단계는 저장된 `llm_cache/*.jsonl`만 읽고 새 LLM 호출을 하지 않습니다. 캐시 미스가 나면 오류로 중단합니다.
- `fresh_seed` 단계나 `--no-cache-only` 계열 옵션은 새 LLM 호출을 만듭니다. 실험 당시 설정은 `LLM_PROVIDER=ollama`, instruct `qwen3:14b`, thinking `gpt-oss:20b`였습니다. 스크립트는 이 두 이름을 현재 공유 설정의 역할별 변수(`OLLAMA_ANALYST_MODEL`, `OLLAMA_QUANT_MODEL`, `OLLAMA_CHARTIST_MODEL`, `OLLAMA_RISK_MANAGER_MODEL`)로 복사하되, 이미 설정된 역할별 값은 덮어쓰지 않습니다.
- 입력 데이터는 `--data-dir`(기본 `data/`)의 테마 목록, 가격, 코퍼스, 테마 멤버십입니다. 저장소에는 포함되어 있지 않으므로 먼저 수집·빌드가 필요합니다.
- 출력 기본 위치는 `experiment_results/backtesting/...`이며 Git이 추적하지 않습니다. 보존할 결과만 검토 후 `research/`로 옮깁니다.

## 스크립트

| 스크립트 | 역할 |
| --- | --- |
| `run_uncontaminated_4agent_backtests.py` | 수치 점수·규칙 사전 필터·RiskManager 보정을 모두 끈 4-agent 실행. `fresh_seed`로 새 캐시를 만든 뒤 여러 `AGENT_SCORE_PROFILE`을 캐시 전용으로 비교하고 `manifest.json`, 요약 CSV, 증거 문서를 씁니다. 같은 출력 위치에서 다시 실행하면 완료된 작업을 이어받되, 실행 모델 버전이나 프롬프트 버전이 다르거나 legacy 캐시를 쓴 결과는 이어받지 않고 다시 실행합니다. |
| `supervise_uncontaminated_4agent_run.py` | 위 실행을 감시하며 캐시 진행이 멈추면 재시작합니다. 장시간 로컬 LLM 실행용입니다. |
| `run_agent_architecture_profile_backtests.py` | 기존 멀티 에이전트 캐시만으로 점수 프로필별 백테스트를 실행합니다. |
| `run_agent_architecture_pure_agent_backtests.py` | 규칙 점수를 프롬프트에서 제거한 순수 에이전트 ablation을 캐시 전용으로 실행합니다. |
| `run_agent_architecture_fresh_representatives.py` | 대표 프로필 1~2개를 새 LLM 호출로 재검증합니다. |
| `build_agent_architecture_validation.py` | 저장된 실행 결과와 멀티 에이전트 캐시를 결합해 `AGENT_ABLATION_EVIDENCE_KO.md`와 요약 CSV를 만듭니다. 캐시는 프롬프트 버전·모델·테마·날짜·종목·점수까지 정확히 일치할 때만 결합하고, 점수가 없는 후보는 채우지 않고 제외합니다. 결합률이 `--min-agent-coverage`(기본 90%) 미만인 실행은 비교에서 빠지며, 에이전트 제거 비교의 기준선은 3-agent 조합입니다. 결론 문장은 계산 결과에서만 만듭니다. 입력이 없으면 종료 코드 2, 비교 가능한 실행이 없으면 3입니다. `--source-root`와 `--output-dir`로 원본을 덮어쓰지 않고 다른 곳에 결과를 쓸 수 있습니다. |
| `run_remaining_theme_backtests.py` | 여러 테마에 대해 멀티 에이전트 검증과 기술 기준선을 순차 실행합니다. `--mock-llm`이면 외부 호출 없이 흐름만 확인합니다. |
| `build_combined_theme_universe.py` | 여러 테마를 합친 가상 유니버스 데이터를 만듭니다. |
| `audit_theme_data.py` | 테마별 가격·코퍼스 커버리지를 점검하고 누락 구간을 보고합니다. |

## 이전 위치

`ai-data-main`에서는 `scripts/` 바로 아래에 있었습니다. 보관된 `manifest.json`의 `command` 항목은 그 당시 경로를 기록한 것입니다. 실행 컴퓨터 전용이던 `stop_after_*.py`, `*.sh` 큐 스크립트(screen 세션, `pkill` 의존)는 옮기지 않았습니다.
