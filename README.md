# HQA AI + Data Integration

HQA는 한국 주식 분석을 위한 AI + 데이터 통합 실행 프로젝트입니다. 이 브랜치는 데이터 수집/검색 자산과 멀티 에이전트 분석 로직을 한 실행 단위로 묶고, RAG 기반 질의응답, 종목 분석, 테마 주도주 선별, 백테스팅, dry-run 거래 판단을 제공합니다.

현재 `ai-data-main`의 핵심은 두 갈래입니다.

- 운영/데모 경로: `main.py`, `ai_server/app.py`, `src/agents/`, `src/tools/`, `src/rag/`
- 검증/연구 경로: `backtesting/`, `data/backtest_results/`, `data/raw/theme_membership/`

## 현재 상태

- RAG와 멀티 에이전트가 기존 데이터 자산을 읽어 종목/테마 분석을 수행합니다.
- AI 테마 주도주 백테스트가 추가되어 point-in-time 기준 검증을 수행할 수 있습니다.
- LLM rerank와 multi-agent scorer를 백테스트에 얹을 수 있습니다.
- 거래 API는 실제 주문 전 dry-run 시뮬레이션과 주문 로그 기록까지 검증되어 있습니다.
- KIS 실제 주문 API 연동은 아직 TODO 상태입니다. 실계좌/모의계좌 주문 전송으로 보면 안 됩니다.

## 프로젝트 구조

```text
HQA_Project/
├── main.py                    # CLI 진입점
├── ai_server/                 # FastAPI AI 서버
├── backtesting/               # point-in-time 백테스트 도구
├── config/                    # watchlist/trading 설정
├── data/                      # raw, index, market data, backtest results
├── prompts/                   # 에이전트 프롬프트
├── scripts/                   # 헬스체크, RAG 빌드, 데모 실행
├── src/
│   ├── agents/                # Analyst, Quant, Chartist, RiskManager, Supervisor
│   ├── config/                # .env 로드와 경로 설정
│   ├── data_pipeline/         # 데이터 가공/가격 로더
│   ├── ingestion/             # Naver, DART, KIS 등 수집 계층
│   ├── rag/                   # canonical retriever, BM25, vector store
│   ├── retrieval/             # fallback retrieval
│   ├── runner/                # autonomous runner, TradeExecutor
│   └── tools/                 # 에이전트가 사용하는 도구
└── tests/                     # 런타임, RAG, 거래, 백테스팅 테스트
```

## 주요 기능

### RAG 질의응답

- `src/rag/canonical_retriever.py`를 우선 사용합니다.
- canonical index가 없으면 기존 pipeline BM25/vector 자산으로 fallback할 수 있습니다.
- 단일 질문 데모는 `scripts/run_agent_demo.py`에서 실행합니다.

### 단일 종목 분석

- `AnalystAgent`, `QuantAgent`, `ChartistAgent`, `RiskManagerAgent`가 종목을 분석합니다.
- `quick` 모드는 Quant + Chartist 중심입니다.
- `full` 모드는 Analyst + Quant + Chartist + RiskManager 흐름입니다.

### 테마 주도주 선별

- `src/agents/theme_orchestrator.py`가 테마 후보를 추출하고 멀티 에이전트로 평가합니다.
- CLI는 `main.py --theme ...` 또는 `scripts/run_theme_orchestrator.py`를 사용합니다.
- API는 `POST /theme/analyze`를 사용합니다.

### 백테스팅

- `backtesting/leader_backtest.py`는 과거 시점 기준 테마 주도주를 고르고, 보유 기간 이후 성과를 평가합니다.
- `backtesting/temporal_rag.py`는 `as_of_date` 이전 문서와 차트만 사용하도록 제한합니다.
- `backtesting/build_theme_membership.py`는 point-in-time 테마 멤버십 데이터를 만듭니다.
- `backtesting/sweep_leader_backtest.py`는 여러 파라미터 조합을 비교합니다.
- 상세 사용법은 `backtesting/README.md`를 보세요.

### LLM 백테스트 모드

기본 백테스트는 규칙 기반 점수화로도 실행됩니다. LLM을 붙일 때는 다음 옵션을 사용합니다.

```bash
python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20260101 \
  --to-date 20260116 \
  --rebalance W \
  --top-n 3 \
  --hold-days 5 \
  --llm-mode multi_agent \
  --llm-rerank-top-k 5 \
  --llm-weight 0.1 \
  --llm-context-docs 3
```

`--llm-mode multi_agent`는 운영 오케스트레이터를 그대로 호출하지 않고, 백테스트용 feature와 문서를 `as_of_date` 기준으로 제한한 뒤 Analyst, Quant, Chartist, RiskManager 스타일의 평가를 수행합니다.

### Dry-run 거래 판단

- `src/runner/trade_executor.py`가 `FinalDecision`을 주문 판단으로 변환합니다.
- `dry_run=true`이면 실제 주문 없이 simulated 주문 로그만 저장합니다.
- 주문 로그는 기본적으로 `data/orders/<YYYY-MM-DD>/orders.jsonl`에 저장됩니다.
- 현재 KIS 실제 주문 호출은 구현 완료 상태가 아니므로, 자동매매 실주문 시스템으로 사용하면 안 됩니다.

## 데이터 구조

| 경로 | 용도 |
|---|---|
| `data/raw/` | 뉴스, 공시, 포럼, 차트, 테마 후보 원천 데이터 |
| `data/raw/theme_membership/` | point-in-time 테마 멤버십 데이터 |
| `data/corpora/` | 테마별 문서 corpus |
| `data/market_data/` | 차트/가격 데이터 |
| `data/canonical_index/` | RAG 우선 검색 인덱스 |
| `data/bm25/` | fallback BM25 인덱스 |
| `data/vector_stores/` | fallback vector store |
| `data/backtest_results/` | 백테스트 결과 JSON/CSV |
| `data/backtest_results/llm_cache/` | LLM 평가 캐시 |
| `data/backtest_results/llm_final/` | LLM/multi-agent 백테스트 최종 결과 |
| `data/backtest_results/validation/` | 리스크 필터/파라미터 검증 결과 |
| `data/orders/` | dry-run 또는 주문 실행 로그 |

## 데이터 수집 파이프라인

수집 파이프라인은 테마 후보 종목을 찾고, 종목별 raw 데이터를 수집한 뒤, RAG가 읽을 수 있는 Layer2/canonical index로 빌드합니다. 주요 진입점은 `scripts/theme_pipeline.py`, `scripts/run_pipeline.py`, `scripts/build_rag.py`입니다.

### 전체 흐름

```text
테마 키워드
  -> Naver theme에서 후보 종목 발견
  -> data/raw/theme_targets/<theme_key>.jsonl 저장
  -> news / dart / forum / chart 수집
  -> data/raw/<source>/<theme_key>.jsonl 저장
  -> RawLayer2Builder
  -> data/corpora/<theme_key>/
  -> data/market_data/<theme_key>/
  -> data/canonical_index/<theme_key>/
  -> RAG / 에이전트 / 백테스트에서 사용
```

### 수집 소스

| 소스 | 수집기 | 필요 조건 | 저장 위치 |
|---|---|---|---|
| Naver News | `NaverNewsCollector` | 네트워크 | `data/raw/news/<theme_key>.jsonl` |
| DART 공시 | `DartDisclosureCollector` | `DART_API_KEY`, `corp_code` | `data/raw/dart/<theme_key>.jsonl` |
| Naver 종토방 | `NaverStockForumCollector` | 네트워크 | `data/raw/forum/<theme_key>.jsonl` |
| Chart/OHLC | `NaverStockChartCollector`, optional KIS | 네트워크, 선택 KIS 키 | `data/raw/chart/<theme_key>.jsonl`, `data/market_data/<theme_key>/` |
| Theme targets | `NaverThemeStockCollector` | Selenium/Chromium | `data/raw/theme_targets/<theme_key>.jsonl` |

DART 수집은 `corp_codes.csv`에 `stock_code,corp_code` 매핑이 없거나 `DART_API_KEY`가 없으면 건너뜁니다. 차트 수집은 기본적으로 Naver 데이터를 쓰고, `KIS_APP_KEY`, `KIS_APP_SECRET`이 있으면 KIS 일봉 데이터도 보강할 수 있습니다.

### 테마 수집 + RAG 빌드

테마 후보를 찾고 raw 수집과 Layer2/RAG 빌드까지 실행합니다.

```bash
python scripts/theme_pipeline.py \
  --theme AI \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --theme-max-stocks 30 \
  --enabled-sources news,dart,forum,chart \
  --corp-codes-csv ./corp_codes.csv
```

주요 옵션:

- `--theme`: Naver 테마 검색 키워드
- `--theme-key`: 저장과 검색에 사용할 내부 키
- `--target-mode overwrite|append`: 후보 종목 저장 방식
- `--reuse-saved-targets`: 기존 `theme_targets`가 있으면 재사용
- `--save-only`: 후보 종목만 저장하고 실제 수집은 건너뜀
- `--enabled-sources`: `news,dart,forum,chart` 중 사용할 소스
- `--update-mode append-new-stocks|overwrite`: Layer2 빌드 방식

### 통합 배치 실행

`scripts/run_pipeline.py`는 수집, 빌드, 분석을 한 명령으로 묶은 배치 진입점입니다.

```bash
python scripts/run_pipeline.py --theme AI --full
```

모드:

- `--full`: 수집 + 빌드 + 분석
- `--collect-and-build`: 수집 + 빌드
- `--build-and-analyze`: 기존 raw 데이터로 빌드 + 분석
- `--analyze-only`: 기존 canonical index로 분석만 수행

운영에서는 수집 주기는 cron/systemd 같은 외부 스케줄러가 관리하고, 빌드는 수집 직후 또는 별도 주기로 실행하는 방식이 권장됩니다. 에이전트 분석 주기는 `config/watchlist.yaml`의 `schedule` 설정을 참고합니다.

### RAG 자산만 다시 빌드

이미 raw 데이터가 있으면 수집 없이 RAG 자산만 재생성할 수 있습니다.

```bash
python scripts/build_rag.py --theme AI --theme-key ai --mode append-new-stocks --stats
```

기존 데이터를 완전히 다시 만들고 싶을 때:

```bash
python scripts/build_rag.py --theme AI --theme-key ai --mode overwrite --stats
```

통계만 볼 때:

```bash
python scripts/build_rag.py --theme-key ai --stats-only
```

### 수집 파이프라인 주의사항

- 수집은 네트워크와 외부 사이트 구조에 의존하므로 실패한 소스가 있어도 나머지 소스는 계속 진행합니다.
- Selenium/Chromium이 필요한 테마 탐색은 로컬 브라우저/드라이버 환경에 영향을 받습니다. Docker에는 `chromium`과 `chromium-driver`가 포함됩니다.
- `overwrite` 모드는 해당 테마 raw 파일을 다시 만드는 흐름이므로, 실험 전 기존 결과 보존이 필요한지 확인하세요.
- raw 데이터와 canonical index는 분리되어 있습니다. raw를 수집했다고 해서 검색 품질이 바로 좋아지는 것이 아니라, Layer2/RAG 빌드가 필요합니다.
- 백테스트는 운영 RAG와 별개로 `as_of_date` 기준 필터를 적용합니다. 과거 검증용 데이터는 `backtesting/` 도구로 다루는 것이 안전합니다.

## 설치

로컬 검증 환경은 Python 3.12 계열에서 사용 중이고, Docker 이미지는 Python 3.11-slim을 기준으로 합니다.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

브라우저 기반 수집 기능을 쓸 경우:

```bash
playwright install chromium
```

Docker 환경에서는 `Dockerfile`과 `ai_server/Dockerfile`이 `chromium`, `chromium-driver`, `scripts/` 복사를 포함합니다.

## 환경 변수

`.env.example`을 복사해 `.env`를 만듭니다.

```bash
cp .env.example .env
```

이 프로젝트의 기본 LLM 실행 기준은 Ollama입니다.

```env
LLM_PROVIDER=ollama
HQA_DATA_DIR=./data
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_INSTRUCT_MODEL=qwen3.5:9b
OLLAMA_THINKING_MODEL=gemma4:e4b
OLLAMA_THINKING_VALIDATOR_MODEL=
OLLAMA_VISION_MODEL=llava:13b
```

위 값이 현재 문서와 `.env.example`의 기준입니다. 다만 `.env`가 전혀 없을 때 코드 내부 fallback 기본값은 `src/agents/llm_config.py`에 정의된 `qwen2.5:14b` 계열입니다. 실제 실행 모델을 명확히 고정하려면 `.env`에 위 값을 넣어두세요.

Ollama 모델 준비:

```bash
ollama serve
ollama pull qwen3.5:9b
ollama pull gemma4:e4b
ollama pull llava:13b
```

Gemini를 사용할 경우:

```env
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your_google_api_key_here
# 또는
GEMINI_API_KEY=your_google_api_key_here
GEMINI_INSTRUCT_MODEL=gemini-2.5-flash-lite
GEMINI_THINKING_MODEL=gemini-2.5-pro
GEMINI_THINKING_VALIDATOR_MODEL=gemini-2.5-flash
GEMINI_VISION_MODEL=gemini-2.5-flash
```

Smoke test만 할 때는 외부 LLM 없이 다음 값을 쓸 수 있습니다.

```env
LLM_PROVIDER=mock
```

환경 파일 로드 우선순위는 `.env-ai`가 있으면 `.env`보다 우선입니다.

## 빠른 실행

헬스체크:

```bash
python scripts/healthcheck.py
```

데이터 연결 확인:

```bash
python scripts/verify_data_connection.py --data-dir ./data
```

테마 데이터 수집 + RAG 빌드:

```bash
python scripts/theme_pipeline.py \
  --theme AI \
  --theme-key ai \
  --enabled-sources news,dart,forum,chart
```

기존 raw 데이터로 RAG만 다시 빌드:

```bash
python scripts/build_rag.py --theme AI --theme-key ai --stats
```

RAG 데모:

```bash
python scripts/run_agent_demo.py \
  --query "2차전지 시장 전망 요약" \
  --data-dir ./data
```

테마 주도주 선별:

```bash
python scripts/run_theme_orchestrator.py \
  --theme 2차전지 \
  --data-dir ./data \
  --candidate-limit 3 \
  --top-n 2
```

CLI 실행:

```bash
python main.py
python main.py --stock 삼성전자
python main.py --quick 005930
python main.py --theme 2차전지 --candidate-limit 5 --top-n 3
python main.py --price 005930
```

AI 서버 실행:

```bash
uvicorn ai_server.app:app --host 0.0.0.0 --port 8001
```

Docker Compose:

```bash
docker compose up --build
```

## API

기본 AI 서버 포트는 `8001`입니다. `PORT` 환경변수로 런타임 표시 포트를 바꿀 수 있습니다.

### 상태

- `GET /health`

### 채팅/질의

- `POST /chat`
- `POST /suggest`

### 종목 분석

- `POST /analyze`
- `GET /analyze/{task_id}`

예시:

```json
{
  "task_id": "demo-full-005930",
  "stock_name": "삼성전자",
  "stock_code": "005930",
  "mode": "full",
  "max_retries": 1
}
```

### 테마 분석

- `POST /theme/analyze`
- `GET /theme/analyze/{task_id}`

예시:

```json
{
  "task_id": "theme-ai-001",
  "theme": "AI",
  "theme_key": "ai",
  "candidate_limit": 5,
  "top_n": 3
}
```

### 백테스트 결과 저장/조회

- `POST /backtest/results`
- `GET /backtest/results/{task_id}`

`leader_backtest.py`에서 `--submit-url http://127.0.0.1:8001/backtest/results`를 주면 결과를 API 서버 저장소로 제출할 수 있습니다.

### Dry-run 거래 판단

- `GET /trading/status`
- `POST /trading/decision/preview`
- `POST /trading/decision/execute`
- `GET /trading/orders`

예시:

```json
{
  "stock_name": "삼성전자",
  "stock_code": "005930",
  "current_price": 100000,
  "dry_run_override": true,
  "trading_enabled_override": true,
  "final_decision": {
    "total_score": 88,
    "action": "매수",
    "action_code": "BUY",
    "confidence": 72,
    "risk_level": "낮음",
    "risk_level_code": "LOW",
    "summary": "dry-run 테스트용 매수 판단"
  }
}
```

`dry_run_override=true`이면 실제 주문을 보내지 않고 simulated 로그만 남깁니다.

## 백테스트 예시

AI 테마 point-in-time 멤버십 생성:

```bash
python backtesting/build_theme_membership.py \
  --data-dir data \
  --theme-key ai \
  --theme-name AI
```

AI 테마 리더 백테스트:

```bash
python backtesting/leader_backtest.py \
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

여러 조합 비교:

```bash
python backtesting/sweep_leader_backtest.py \
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

## 테스트

핵심 런타임 테스트:

```bash
python -m pytest tests/test_trade_api.py tests/test_trade_executor.py tests/test_llm_config.py -q
```

백테스팅/RAG 테스트:

```bash
python -m pytest tests/test_backtesting_temporal_rag.py tests/test_canonical_rag.py -q
```

전체 테스트:

```bash
python -m pytest
```

## 주의사항

- 현재 데이터 자산은 `2차전지` RAG/테마 데모 자산과 `AI` 백테스트 산출물이 함께 존재합니다.
- `data/backtest_results/`는 결과 파일이 많아 diff가 매우 큽니다.
- `config/watchlist.yaml`의 `trading.enabled=false`, `dry_run=true`가 안전 기본값입니다.
- `TradeExecutor`의 KIS 주문 함수는 아직 실제 주문 구현이 완료되지 않았습니다.
- Redis는 있으면 progress/result 저장에 사용하고, 없으면 인메모리 fallback으로 동작합니다.
- Docker의 `api` 서비스는 8000, `ai` 서비스는 8001을 사용합니다. 주요 AI API 진입점은 `ai_server/app.py`입니다.

## 문서 안내

- 백테스팅 상세: `backtesting/README.md`
- 백테스트 결과 해석: `data/backtest_results/README.md`
- 검증 결과 해석: `data/backtest_results/validation/README.md`
- LLM 최종 실험 결과: `data/backtest_results/llm_final/README.md`
