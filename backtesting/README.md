# 백테스트와 PAPER 평가

과거 전략 실험, 기간별 근거 준비, 기록된 PAPER 성과 평가의 실행 진입점입니다.
프로젝트 루트에서 `venv/bin/python -m backtesting <명령>`으로 실행합니다.
명령을 생략하면 실행하지 않고 오류를 반환합니다.

```bash
venv/bin/python -m backtesting --help
venv/bin/python -m backtesting run --help
```

## 명령 구분

| 명령 | 목적 | 모델 호출 |
| --- | --- | --- |
| `run` | 과거 테마 주도주 전략 1회 평가 | 기본 없음. LLM 옵션 사용 시 가능 |
| `sweep` | 수치 전략 파라미터 조합 비교 | 없음 |
| `validate` | 고정 실험군의 baseline/hybrid/LLM 비교 | 기본 가능. `--mock-llm`은 테스트용 |
| `build-evidence` | 기간별 근거 스냅샷 생성 | 생성형 LLM 없음 |
| `clean-evidence` | 원본을 보존한 별도 정제 스냅샷 생성 | 없음 |
| `build-membership` | 과거 테마 멤버십 근거 생성 | 없음 |
| `paper-runtime` | 저장된 처리 시간, 호출량, 예산 원장 평가 | 없음 |
| `paper-performance` | 저장된 순자산과 체결 기록의 성과 비교 | 없음 |

`build-evidence --build-vector`는 별도 벡터 인덱스를 생성하므로 관련 모델/환경 설정이 필요합니다.
`run --submit-url`은 결과를 지정한 서버에 전송합니다. PAPER 평가 명령은 읽기 전용이며
LLM, 데이터 공급자, 증권사 API를 호출하지 않습니다.

## 저장 위치

| 위치 | 내용 |
| --- | --- |
| `backtesting/` | 실행 코드와 이 안내서 |
| `src/tracing/paper_audit.py`, `paper_performance.py` | PAPER 관측 계약과 보고서 계산 |
| `data/raw/theme_membership/` | 테마 멤버십 근거 |
| `data/canonical_index/`, `data/market_data/` | 실험 입력 자료 |
| `data/period_rag/` | 기간별 스냅샷과 정제 결과 |
| `data/backtest_results/` | 새 실행 결과와 재사용 LLM 캐시 |
| `research/backtesting/` | 보존한 과거 실험 산출물과 보고서 |

과거 결과는 [연구 산출물 안내](../research/backtesting/results/README.md)를 참고합니다.
운영 데이터와 예산/주문 원장은 정리 목적으로 삭제하거나 덮어쓰지 않습니다.

## 수치 전략

```bash
venv/bin/python -m backtesting run \
  --theme AI --theme-key ai \
  --from-date 20250101 --to-date 20251231 \
  --rebalance W --top-n 5 --hold-days 5 \
  --task-id bt-ai-2025-w-top5-h5
```

기본 선택은 수치 전략입니다. `hold-days` 이후 종가 청산을 사용하며 `--stop-loss-pct`,
`--take-profit-pct`, `--trailing-stop-pct`로 조기 청산을 시험할 수 있습니다.
동일 일봉에서 손절/트레일링과 익절이 동시에 닿으면 손절/트레일링을 먼저 적용합니다.

```bash
venv/bin/python -m backtesting sweep \
  --theme AI --theme-key ai \
  --rebalances W --top-ns 3,5,7 --hold-days 3,5,7 \
  --output-dir data/backtest_results/validation/risk_sweep
```

## 근거 준비

```bash
venv/bin/python -m backtesting build-membership \
  --data-dir data --theme-key ai --theme-name AI

venv/bin/python -m backtesting build-evidence \
  --data-dir data --theme-key ai \
  --from-date 20250101 --to-date 20251231 \
  --source-types news,dart --output-name ai_2025_news_dart

venv/bin/python -m backtesting clean-evidence \
  --input-dir data/period_rag/ai_2025_news_dart \
  --output-dir data/period_rag/ai_2025_news_dart_clean
```

기간 스냅샷은 캐시이며, 기간 전체의 문서를 모든 리밸런싱일에 제공하는 용도가 아닙니다.
`TemporalEvidence`는 `as_of_date`로 공개 날짜를 제한하고, `TemporalPriceLoader`는
해당 날짜까지의 가격을 제공합니다. 기존 Python import는 유지합니다.

```python
from backtesting import TemporalEvidence, TemporalPriceLoader, run_leader_backtest

context = TemporalEvidence(data_dir="data", theme_key="ai").search_for_context(
    "AI 반도체 HBM 수혜", as_of_date="2025-06-30", source_types=["news", "dart"],
)
```

## 과거 LLM 실험

과거용 `llm_signal.py`는 공유 LLM 설정을 사용하지만, 현재 Luna 운영 분석 서비스와
동일한 흐름은 아닙니다. 과거 실험 점수를 현재 PAPER 성능으로 해석하면 안 됩니다.

- `--llm-rerank-top-k N`: 수치 후보 상위 N개를 재평가합니다. N이 `top-n`보다 커야 선택 구성이 바뀔 수 있습니다.
- `--llm-candidate-scope broad`: 위험 필터를 통과한 후보를 넓게 평가합니다. top-k 0은 전체 후보이므로 호출량에 주의합니다.
- `--llm-mode single|multi_agent`: 과거용 단일 점수화 또는 역할별 점수화입니다.
- `--llm-horizon auto|short|long`: auto는 보유 기간 10거래일 이하를 short로 분류합니다.
- `--llm-weight`: 수치 점수와 LLM 랭킹 점수의 혼합 비율입니다.

멀티 에이전트 랭킹의 역할 가중치는 short에서 Analyst/Quant/Chartist 30/15/55%,
long에서 45/40/15%입니다. `llm_score`와 보정 후 `llm_ranking_score`는 구분해서 읽습니다.
반복 실행 캐시는 `data/backtest_results/llm_cache/<theme_key>/`를 계속 사용합니다.

**현재 공유 설정의 기본 provider는 OpenAI입니다.** `OLLAMA_*` 모델 이름만 바꿔서는
로컬 모델로 전환되지 않습니다. 외부 호출 없이 흐름만 시험하는 명시적 명령은 다음과 같습니다.

```bash
venv/bin/python -m backtesting validate \
  --preset smoke --mock-llm --short-top-k 5 --long-top-k 5 \
  --output-dir data/backtest_results/proof/smoke_mock
```

mock 결과는 투자 성능의 근거가 아닙니다. 실제 LLM 실험에는 별도의 비용 승인이 필요합니다.

## PAPER 관측 평가

```bash
venv/bin/python -m backtesting paper-runtime \
  --audit data/paper_audit.sqlite3 --budget data/llm_budget.sqlite3

venv/bin/python -m backtesting paper-performance \
  --input data/paper-comparison.json
```

성과 비교에는 동일 투자대상, 기간, 비용 조건, 관측 시각을 가진 전략/수치 기준선/
buy-and-hold 기록을 직접 제공해야 합니다. 누락된 기준선이나 체결 기록을 생성하지 않습니다.
입력 계약과 해석은 [PAPER 운영 안내](../docs/luna-paper-runtime.md)를 참고합니다.

## 검증 경계

과거 도구는 공개일/봉 날짜를 필터링하지만, 새 수집 파이프라인의 `available_at`,
`observed_at`, 정정 버전 이력을 모두 반영하는 재생 엔진은 아닙니다.
특히 과거 가격 로더는 같은 날짜의 마지막 행을 선택합니다. 따라서 현재 수집한 정정 자료를
넣는 것만으로 당시 이용 가능했던 자료가 완전히 재현되지는 않습니다.

테마 멤버십 파일이 있으면 해당 시점의 활성 종목을 사용하지만, 파일이 없을 때의 현재
종목 목록에는 생존 편향이 남을 수 있습니다. LLM 사전학습 기억, 일봉 안의 가격 경로,
실제 주문 거절/부분 체결 역시 이 실험만으로 검증하지 못합니다.
날짜 필터 테스트 통과와 투자 성과 검증은 별개이며 전향적 PAPER 관측이 필요합니다.

## 이전 명령 대응

과거 엔진 모듈의 직접 실행/import는 유지합니다. 권장 실행 경로는 위 단일 진입점입니다.
흩어져 있던 PAPER 평가 스크립트는 중복 wrapper 없이 옮겼습니다.

| 이전 | 현재 |
| --- | --- |
| `backtesting/leader_backtest.py` | `python -m backtesting run` |
| `backtesting/sweep_leader_backtest.py` | `python -m backtesting sweep` |
| `backtesting/proof_validation.py` | `python -m backtesting validate` |
| `backtesting/build_period_evidence.py` | `python -m backtesting build-evidence` |
| `backtesting/clean_period_evidence.py` | `python -m backtesting clean-evidence` |
| `backtesting/build_theme_membership.py` | `python -m backtesting build-membership` |
| `python -m scripts.evaluate_paper_runtime` | `python -m backtesting paper-runtime` |
| `python -m scripts.evaluate_paper_performance` | `python -m backtesting paper-performance` |
