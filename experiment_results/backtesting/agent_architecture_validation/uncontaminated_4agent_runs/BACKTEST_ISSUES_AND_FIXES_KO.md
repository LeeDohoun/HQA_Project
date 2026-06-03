# Backtest Issues And Fixes

- generated_at: `2026-05-29`
- 목적: 4-agent 구조 유효성 검증 중 발견한 오염 가능성, 보정, 실행 문제와 해결 과정을 따로 기록합니다.

## 현재 실험 목표

- 검증 대상: `AnalystAgent + QuantAgent + ChartistAgent + RiskManagerAgent` 구조.
- 대표 4-agent 방식: `four_agent_supervisor_final`.
- 비교 대상: 단일 agent, agent 하나씩 제거, 추가 agent 변형.
- 단타/장타를 분리해서 비교합니다.

## 발견한 오염 가능성과 조치

| 상태 | 문제 | 영향 | 조치 |
| --- | --- | --- | --- |
| 해결 | 최종 랭킹에 규칙기반 점수가 섞일 수 있음 | agent 성능이 아니라 규칙기반+agent 혼합 성능이 됨 | `short_llm_only` / `long_llm_only`, `llm_weight=1.0`만 사용 |
| 해결 | 단타 `Chartist floor`가 점수를 강제로 올림 | 단타에서 차티스트 효과가 과대평가됨 | `AGENT_DISABLE_SHORT_CHARTIST_FLOOR=1` 적용 |
| 해결 | 규칙기반 top10 후보군만 LLM이 평가 | 규칙기반이 먼저 후보를 걸러 agent 비교가 오염됨 | `short_top_k=0`, `long_top_k=0`, broad scope로 risk-filtered universe 전체 평가 |
| 해결 | agent prompt에 `deterministic_leader_score`가 들어감 | LLM agent가 규칙기반 점수를 보고 판단할 수 있음 | `AGENT_PURE_FEATURES=1`로 prompt feature에서 제거 |
| 해결 | RiskManager가 `recommended_final_score ±10` 제약을 받음 | 상위 agent가 독립적으로 판단하지 못함 | `AGENT_FREE_RISK_MANAGER=1`로 calibrated score band 제거 |
| 해결 | LLM 실패 시 fallback 점수나 규칙기반 점수로 조용히 대체 가능 | 실패를 정상 결과처럼 착각할 수 있음 | `AGENT_FAIL_ON_AGENT_FALLBACK=1`, `AGENT_FAIL_ON_LLM_ERROR=1` 적용 |
| 명시 | ChartistAgent는 LLM이 아니라 가격/거래량 공식 기반 agent | “4개 모두 LLM agent”라고 주장하면 부정확함 | 보고서에서 규칙형 차트 agent로 명시해야 함 |

## 실행 로그

| 시간 | 작업 | 결과 |
| --- | --- | --- |
| 2026-05-29 | 기존 pure agent 결과 점검 | 최종 랭킹 보정은 제거됐지만 top10 prefilter, deterministic prompt, RiskManager band가 남아 있음을 확인 |
| 2026-05-29 | 오염 제거 옵션 구현 | prompt에서 deterministic score 제거, RiskManager 자유 판단, fallback fail-fast 옵션 추가 |
| 2026-05-29 | 전용 러너 추가 | `scripts/run_uncontaminated_4agent_backtests.py` 생성 |
| 2026-05-29 | AI 2024 대표 구간 fresh 실행 시작 | `caffeinate -dimsu`로 절전 방지 상태에서 실행 |
| 2026-05-29 | AI 2024 후보 수 산정 | 단타 569개, 장타 122개, 총 691개 fresh LLM 평가 필요 |
| 2026-05-31 | 장타 fresh 평가 중 중단 | RiskManager가 빈/비정상 JSON을 반환해 `OUTPUT_PARSING_FAILURE` 발생. 캐시는 643개까지 저장됨 |
| 2026-05-31 | 재개 조치 | fallback은 계속 금지하고, 동일 스키마 호출만 최대 3회 재시도하도록 `LLM_SCHEMA_RETRIES=3` 추가 |
| 2026-05-31 | 재개 후 진행 정체 | 프로세스는 살아있었지만 캐시가 643개에서 약 2시간 증가하지 않음 |
| 2026-05-31 | timeout 조치 | 개별 LLM schema 호출에 `LLM_SCHEMA_TIMEOUT_SECONDS=900` 적용. timeout 후 같은 agent 호출을 재시도하도록 변경 |
| 2026-05-31 | 자동 감시 조치 | `scripts/supervise_uncontaminated_4agent_run.py` 추가. 캐시가 일정 시간 증가하지 않으면 현재 실행과 Ollama runner를 정리하고 캐시 기반으로 재시작 |
| 2026-06-01 | 추가 검증 시작 | AI 2023과 반도체 2024를 별도 output root에서 순차 실행하도록 준비. 기존 AI 2024 결과 덮어쓰기를 피함 |
| 2026-06-01 | 추가 검증 시작 직후 버그 발견 | supervisor가 child runner에 `--output-root`를 넘기지 않아 AI 2023 캐시 2건이 기존 AI 2024 cache에 기록됨. 즉시 중단 후 해당 2건 제거, cache 691건 복구 |
| 2026-06-01 | supervisor 수정 | child command에 `--output-root`를 전달하도록 수정해 AI 2023/반도체 2024 결과가 별도 폴더에 저장되게 함 |
| 2026-06-02 | 사용자 요청으로 AI 2023 일시 중단/재시작 | AI 2023 전용 cache 335건을 보존한 상태에서 screen, supervisor, caffeinate, proof_validation 실행을 종료 |
| 2026-06-02 | 오래된 잘못된 output root 프로세스 발견 | 기존 AI 2024 cache에 AI 2023 cache 336건이 추가로 붙어 1027건이 됨. 해당 프로세스 종료 후 `ai.pure4agent.jsonl.before-restart-cleanup-20260602-210811.bak`로 백업, 2023 key 제거, 2024 key 691건으로 복구 |
| 2026-06-02 | AI 2023 cache 기반 재시작 | `uncontaminated_4agent_runs_ai2023`에서 cache 335건부터 재개. 완료 후 반도체 2024로 넘어가지 않도록 stop guard 유지 |

## 예상 시간

| 범위 | 예상 시간 | 근거 |
| --- | --- | --- |
| AI 2024 대표 구간 | 약 30~45시간 | 총 691개 후보. 초반 관측 속도는 모델 로딩 포함 3개/약 10분 |
| AI 2023~2024 전체 | 약 3~4일 | 2024 대표 구간의 약 2배 후보 수로 추정 |
| AI + 반도체 전체 | 5일 이상 가능 | 반도체는 기존에도 AI보다 기간/후보가 더 많았음 |

## 남은 확인 항목

- fresh seed 실행 후 결과 JSON에서 `llm_rerank.top_k=0`, `top_k_meaning=all_risk_filtered` 확인.
- `llm_agent_scores`에 fallback 문구가 없는지 확인.
- `llm_raw_score == llm_ranking_score`인지 확인.
- 로그에 `cache miss`, `LLM scoring failed`, `Traceback`이 없는지 확인.
- 최종 산출물에서 4-agent 대비 단독/제거/추가 profile 차이를 단타/장타 별로 정리.
