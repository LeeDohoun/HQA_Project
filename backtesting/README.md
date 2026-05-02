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

```bash
.venv/bin/python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --rebalance W \
  --top-n 5 \
  --hold-days 5 \
  --task-id bt-ai-2025-w-top5-h5
```

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
  --top-ns 1,3,5 \
  --hold-days 5 \
  --output-dir data/backtest_results/sweeps_weekly_h5
```

현재 산출물과 해석은 `data/backtest_results/README.md`에 정리되어 있습니다.
