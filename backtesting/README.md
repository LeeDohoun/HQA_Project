# Backtesting Temporal RAG

이 폴더는 운영용 RAG와 분리된 백테스트 전용 도구입니다. 핵심 원칙은 `as_of_date` 기준으로 그 날짜 이전에 공개된 문서와 차트만 모델/전략에 전달하는 것입니다.

## 권장 구조

- 원본 데이터는 기존 위치를 그대로 사용합니다.
- 백테스트에서는 `TemporalRAG`와 `TemporalPriceLoader`를 통해 날짜 필터를 적용합니다.
- 기간별 RAG 스냅샷은 반복 실행 속도를 위한 캐시로만 사용합니다.

```text
data/canonical_index/ai/corpus.jsonl
data/market_data/ai/chart.jsonl
        |
        v
backtesting.TemporalRAG(as_of_date="2025-06-30")
backtesting.TemporalPriceLoader(as_of_date="2025-06-30")
```

## 예시

```python
from backtesting import TemporalPriceLoader, TemporalRAG

rag = TemporalRAG(data_dir="./data", theme_key="ai")
context = rag.search_for_context(
    "AI 반도체 HBM 수혜",
    as_of_date="2025-06-30",
    source_types=["news", "dart"],
)

prices = TemporalPriceLoader(data_dir="./data", theme_key="ai").get_stock_data(
    "005930",
    as_of_date="2025-06-30",
    days=300,
)
```

## 종토방 사용 기준

종토방은 모의투자에서는 유용하지만 백테스트에서는 미래 누수 위험이 큽니다. 포함하려면 반드시 `as_of_date` 필터와 짧은 lookback을 같이 사용합니다.

```python
context = rag.search_for_context(
    "시장 심리",
    as_of_date="2026-04-01",
    source_types=["forum"],
    lookback_days={"forum": 60},
)
```

## 기간 캐시 생성

```bash
.venv/bin/python backtesting/build_period_rag.py \
  --data-dir ./data \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --source-types news,dart \
  --output-name ai_2025_news_dart \
  --build-vector
```

결과는 `data/period_rag/<output-name>/`에 저장됩니다. 이 캐시는 편의용이며, 실제 백테스트 판단에는 여전히 `as_of_date`를 기준으로 미래 문서를 제외해야 합니다.

## 중복/노이즈 정제

기간 캐시를 만든 뒤에는 원본을 보존하고 clean 스냅샷을 따로 만듭니다. DART chunk는 유지하고, 종토방의 짧은 링크성 글, exact 본문 중복, 뉴스/종토방의 과도한 동일 제목 chunk를 줄입니다.

```bash
.venv/bin/python backtesting/clean_period_rag.py \
  --input-dir data/period_rag/ai_2026_news_dart_forum \
  --output-dir data/period_rag/ai_2026_news_dart_forum_clean
```

정제 결과와 제거 사유는 `clean_report.json`에 저장됩니다.

## AI 테마 주도주 백테스트

`leader_backtest.py`는 AI 테마 종목군에서 과거 시점 기준 주도주를 고르고, 보유 기간 이후 수익률로 예측/선정 결과를 평가합니다.

백테스트 전에 point-in-time 테마 멤버십 근거를 만들 수 있습니다. 이 파일은 기존 raw/RAG corpus를 수정하지 않고 `data/raw/theme_membership/`에 별도로 저장됩니다.

```bash
.venv/bin/python backtesting/build_theme_membership.py \
  --data-dir data \
  --theme-key ai \
  --theme-name AI
```

`leader_backtest.py`와 `sweep_leader_backtest.py`는 `data/raw/theme_membership/<theme_key>.jsonl`이 있으면 매 리밸런싱일의 활성 종목만 후보군으로 사용합니다.

```bash
.venv/bin/python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --rebalance W \
  --top-n 5 \
  --hold-days 5 \
  --min-market-breadth-pct 40 \
  --max-volatility-20d 1.2 \
  --max-return-5d 0.35 \
  --max-return-20d 0.9 \
  --trailing-stop-pct 15 \
  --task-id bt-ai-2025-w-top5-h5
```

선택 종목은 기본적으로 `hold-days` 이후 종가로 청산합니다. 보유 기간 중 조기 청산 규칙을 시험하려면 다음 옵션을 추가할 수 있습니다.

- `--stop-loss-pct`: 진입가 대비 손절률
- `--take-profit-pct`: 진입가 대비 익절률
- `--trailing-stop-pct`: 고점 대비 트레일링 손절률

동일 일자 OHLC에서 손절/트레일링과 익절이 동시에 닿을 수 있으면 보수적으로 손절/트레일링을 먼저 적용합니다.

LLM이 과거 시점 문서와 feature snapshot을 읽고 후보를 재평가하게 하려면 `--llm-rerank-top-k`를 사용합니다. 이 값은 `top-n`보다 커야 실제 선택 종목이 바뀝니다. 예를 들어 `top-n 3`, `--llm-rerank-top-k 5`는 규칙 기반 상위 5개를 먼저 고른 뒤 LLM 점수로 최종 3개를 다시 선택합니다.

AI가 이미 좁혀진 후보만 검증하는 한계를 줄이려면 `--llm-candidate-scope broad`를 사용합니다. 이 모드는 리스크 필터를 통과한 후보군 전체를 LLM/멀티 에이전트가 평가합니다. 비용을 줄이고 싶으면 `--llm-rerank-top-k 30`처럼 상한을 줄 수 있고, `--llm-rerank-top-k 0`이면 해당 리밸런싱일의 리스크 통과 후보 전체를 평가합니다. `--llm-weight 1.0`은 LLM-only, `--llm-weight 0.3~0.7`은 정량/LLM hybrid 실험입니다.

기본 LLM 모드는 단일 LLM 점수화입니다. 프로젝트의 멀티 에이전트 구조를 백테스트에 반영하려면 `--llm-mode multi_agent`를 추가합니다. 이 모드는 `Analyst`, `Quant`, `Chartist`, `RiskManager` 흐름으로 후보를 평가하되, 운영 오케스트레이터를 그대로 호출하지는 않습니다. 운영 오케스트레이터는 현재 시점 데이터 접근 가능성이 있어 백테스트에는 맞지 않기 때문에, 백테스트용 구현은 모든 문서/feature를 `as_of_date` 이전으로 제한합니다.

LLM 평가는 `--llm-horizon auto|short|long`으로 단타/장타 프로필을 분리합니다. `auto`는 `hold-days <= 10`이면 `short`, 그보다 길면 `long`을 사용합니다. `short`는 3~10거래일 주도주 탐색에 맞춰 가격 모멘텀/거래량/단기 촉매를 더 크게 보고, `long`은 20~60거래일 관점에서 AI 테마 직접성/성장성/재무 안정성을 더 크게 봅니다.

```bash
.venv/bin/python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20260101 \
  --to-date 20260116 \
  --rebalance W \
  --top-n 3 \
  --hold-days 5 \
  --min-market-breadth-pct 40 \
  --max-volatility-20d 1.2 \
  --max-return-5d 0.35 \
  --max-return-20d 0.9 \
  --trailing-stop-pct 15 \
  --llm-mode multi_agent \
  --llm-horizon short \
  --llm-rerank-top-k 5 \
  --llm-weight 0.1 \
  --llm-context-docs 3
```

넓은 후보군을 대상으로 qwen3/gpt-oss 조합을 시험하려면 환경변수로 역할별 모델을 지정할 수 있습니다.

```bash
OLLAMA_INSTRUCT_MODEL=qwen3:14b \
OLLAMA_THINKING_MODEL=gpt-oss:20b \
.venv/bin/python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20260101 \
  --to-date 20260331 \
  --rebalance W \
  --top-n 3 \
  --hold-days 5 \
  --min-market-breadth-pct 40 \
  --max-volatility-20d 1.2 \
  --max-return-5d 0.35 \
  --max-return-20d 0.9 \
  --trailing-stop-pct 15 \
  --llm-mode multi_agent \
  --llm-horizon short \
  --llm-candidate-scope broad \
  --llm-rerank-top-k 30 \
  --llm-weight 0.5 \
  --llm-context-docs 3
```

멀티 에이전트 모드의 최종 점수는 보유기간 프로필별로 보정됩니다. `short`는 Analyst 30%, Quant 15%, Chartist 55%로 단기 가격/수급 리더십을 크게 보고, `long`은 Analyst 45%, Quant 40%, Chartist 15%로 테마 지속성/기초체력을 크게 봅니다. `RiskManager` LLM은 요약/행동/리스크 판단을 만들지만, 최종 랭킹 점수는 이 보정 가중치로 계산해 보유기간 목적과 다른 판단이 점수를 과도하게 덮어쓰지 않도록 했습니다.

`short` 프로필에서는 결과에 `llm_score`와 별도로 `llm_ranking_score`가 기록될 수 있습니다. `llm_score`는 RiskManager가 해석한 원점수이고, `llm_ranking_score`는 Chartist의 단기 가격/거래량 리더십을 바닥 점수로 삼되 테마 적합도 부족과 리스크 점수로만 제한적으로 감점한 실제 랭킹용 점수입니다. 이는 단타 백테스트에서 LLM이 장기 테마 검증자처럼 행동해 단기 주도주를 과도하게 탈락시키는 문제를 줄이기 위한 보정입니다.

LLM 평가는 `data/backtest_results/llm_cache/<theme_key>/`에 캐시됩니다. 같은 날짜/종목/feature 조합은 재실행 시 다시 호출하지 않습니다.

결과 JSON은 기본적으로 `data/backtest_results/`에 저장됩니다. AI 서버가 실행 중이면 같은 payload를 백엔드 호환 저장소로 보낼 수 있습니다.

```bash
.venv/bin/python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --rebalance W \
  --top-n 5 \
  --hold-days 5 \
  --submit-url http://127.0.0.1:8001/backtest/results
```

여러 조합을 비교하려면 sweep runner를 사용합니다.

```bash
.venv/bin/python backtesting/sweep_leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --rebalances W \
  --top-ns 3,5,7 \
  --hold-days 3,5,7 \
  --min-market-breadth-pct 40 \
  --max-volatility-20d 1.2 \
  --max-return-5d 0.35 \
  --max-return-20d 0.9 \
  --trailing-stop-pct 15 \
  --output-dir data/backtest_results/validation/risk_sweep_w_top357_h357
```

단타/장타 AI 유용성을 증명하기 위한 고정 프로토콜은 proof runner를 사용합니다. 이 runner는 단타/장타별 deterministic baseline, hybrid, LLM-only를 같은 기간에서 비교하고 `summary.json`, `summary.csv`, `report.md`를 함께 생성합니다.

먼저 runner 자체를 빠르게 확인하려면 mock LLM smoke를 실행합니다.

```bash
.venv/bin/python backtesting/proof_validation.py \
  --preset smoke \
  --mock-llm \
  --short-top-k 5 \
  --long-top-k 5 \
  --output-dir data/backtest_results/proof/smoke_mock
```

실제 모델로 검증 자료를 만들 때는 `qwen3:14b`와 `gpt-oss:20b`를 역할별로 지정하고 `--preset proof`를 사용합니다.

```bash
OLLAMA_INSTRUCT_MODEL=qwen3:14b \
OLLAMA_THINKING_MODEL=gpt-oss:20b \
.venv/bin/python backtesting/proof_validation.py \
  --preset proof \
  --short-top-k 10 \
  --long-top-k 10 \
  --output-dir data/backtest_results/proof/qwen3_gptoss
```

기본 proof 기간은 2025년 튜닝 참고 구간, 2026년 1분기 검증 구간, 2026년 4월 이후 최근 확인 구간입니다. 결과에서 핵심 판단은 `excess_delta_vs_baseline_pct`, `win_vs_baseline`, `scorecard.evidence_grade`입니다.

현재 산출물과 해석은 `data/backtest_results/README.md`에 정리되어 있습니다.
