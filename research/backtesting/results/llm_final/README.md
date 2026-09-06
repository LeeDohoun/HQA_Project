# LLM Theme Leader Backtest Results

생성일 기준: 2026-05-04

## 목적

기존 `W / top3 / hold5 / trailing15` 전략은 가격/문서 개수 기반 결정론적 점수로 주도주를 골랐습니다. 이번 보강은 실제 Ollama `qwen2.5:14b` LLM이 각 리밸런싱일의 과거 문서와 feature snapshot만 읽고 후보를 재점수화하도록 만든 것입니다.

## 방식

- 먼저 기존 규칙 기반 점수로 후보 상위 `K`개를 고릅니다.
- LLM은 각 후보별로 `as_of_date` 이전 문서만 읽습니다.
- LLM 출력은 `llm_score`, `confidence`, `theme_fit_score`, `catalyst_score`, `risk_score`, `summary`, `catalysts`, `risks`로 저장됩니다.
- `llm_weight=1.0`이면 최종 선택은 LLM 점수만 사용합니다.
- 결과와 캐시는 백엔드 호환 JSON에 보존됩니다.

멀티 에이전트 모드는 `--llm-mode multi_agent`로 실행합니다. 이 모드는 백테스트용 `Analyst`, `Quant`, `Chartist`, `RiskManager`를 사용합니다. 운영용 `ThemeLeaderOrchestrator`를 직접 호출하지 않는 이유는 운영 Chartist/후보 추출이 현재 전체 로컬 데이터를 읽을 수 있어 point-in-time 백테스트에 미래 누수가 생길 수 있기 때문입니다.

`temporal_theme_leader_multi_agent_v2`부터는 `RiskManager` 최종점수를 그대로 믿지 않고, 프로젝트의 `RiskManagerAgent.quick_decision` 가중치와 같은 Analyst 40%, Quant 35%, Chartist 25%로 최종 랭킹 점수를 보정합니다.

## 2026년 1월 파일럿

기간: 2026-01-01 ~ 2026-01-16

| 설정 | 설명 | 수익률 | 벤치마크 | 초과수익 | MDD | Sharpe |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| deterministic baseline | 기존 top3 규칙 기반 | 14.21% | 8.36% | 5.85% | -1.47% | 6.103 |
| LLM top3 rerank | top3 안에서 LLM 재정렬 | 14.21% | 8.36% | 5.85% | -1.47% | 6.103 |
| LLM top5 rerank | top5 후보에서 LLM이 최종 top3 선택 | -4.16% | 8.36% | -12.52% | -4.12% | -1.302 |
| hybrid top5, weight 0.4 | 규칙 60% + LLM 40% | -2.69% | 8.36% | -11.04% | -2.65% | -0.823 |
| hybrid top5, weight 0.2 | 규칙 80% + LLM 20% | -2.34% | 8.36% | -10.70% | -2.65% | -0.669 |

## 해석

`top3 rerank`는 후보군 크기가 `top_n`과 같아서 실제 선택 종목을 바꾸지 못했습니다. 의미 있는 LLM 백테스트는 `top_n`보다 큰 후보군을 LLM이 재선별해야 합니다.

`top5 rerank`에서는 LLM이 삼성전기, 고영처럼 AI 테마 적합도와 문서상 촉매가 뚜렷한 종목을 끌어올렸습니다. 다만 5거래일 단기 수익률 기준으로는 더블유에스아이, 에이팩트처럼 가격 모멘텀이 강했던 종목을 밀어내면서 성과가 악화되었습니다.

현재 결론은 “LLM을 넣은 구조는 구현됐지만, qwen2.5:14b 단일 프롬프트를 순수 랭커로 쓰는 방식은 아직 유의미하지 않다”입니다. 다음 개선은 LLM을 최종 랭커가 아니라 `테마 적합도/촉매/리스크` 보조 feature로 분리하고, 가격 모멘텀과 검증된 방식으로 결합하는 것입니다.

## 2026-01-02 멀티 에이전트 파일럿

기간: 2026-01-01 ~ 2026-01-02, 진입일 2026-01-02, 5거래일 보유

| 설정 | 설명 | 수익률 | 벤치마크 | 초과수익 | 선택 종목 |
| --- | --- | ---: | ---: | ---: | --- |
| deterministic baseline | 기존 규칙 기반 top3 | 6.63% | -4.34% | 10.97% | 로보티즈, 에이팩트, 더블유에스아이 |
| multi-agent v1, weight 1.0 | RiskManager LLM 최종점수 직접 사용 | -6.16% | -4.34% | -1.81% | 삼성전기, 고영, 로보티즈 |
| multi-agent v2, weight 1.0 | Analyst/Quant/Chartist 40/35/25 보정 | -6.16% | -4.34% | -1.81% | 삼성전기, 고영, 로보티즈 |
| multi-agent v2, weight 0.2 | 규칙 80% + 멀티 에이전트 20% | -6.16% | -4.34% | -1.81% | 로보티즈, 삼성전기, 고영 |
| multi-agent v2, weight 0.1 | 규칙 90% + 멀티 에이전트 10% | 6.63% | -4.34% | 10.97% | 로보티즈, 더블유에스아이, 에이팩트 |

멀티 에이전트는 실제로 반영되었습니다. 결과 JSON의 `metadata.llm.agents`에는 `analyst`, `quant`, `chartist`, `risk_manager`가 기록되고, 각 포지션에는 `llm_agent_scores`가 저장됩니다.

다만 성과 관점에서는 아직 순수 LLM/멀티 에이전트 랭커가 유의미하다고 보기 어렵습니다. qwen2.5:14b는 AI 테마 직접성이나 뉴스 촉매가 강한 삼성전기/고영을 끌어올렸지만, 이 짧은 5거래일 평가에서는 가격 모멘텀이 강했던 더블유에스아이/에이팩트를 밀어내면서 수익률이 악화되었습니다.

현재 권장값은 `--llm-mode multi_agent --llm-rerank-top-k 5 --llm-weight 0.1`입니다. 이 설정은 LLM 판단과 에이전트별 설명을 결과에 남기되, 검증된 가격/리스크 신호를 훼손하지 않습니다. 더 높은 LLM 가중치를 쓰려면 더 긴 기간의 walk-forward 검증, 재무 숫자 데이터 보강, 더 강한 모델 또는 프롬프트 재학습이 필요합니다.
