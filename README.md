# HQA Project

HQA(Hegemony Quantitative Analyst)는 한국 주식의 테마 주도주를 수집 데이터, RAG, 멀티 에이전트 분석, 사용자 투자 성향으로 평가하고, Spring 백엔드가 KIS 계좌/잔고/현재가를 검증한 뒤 매매 신호를 저장·집행하는 통합 투자 분석 프로젝트입니다.

핵심 원칙은 명확합니다.

- AI 서버는 분석과 신호 생성까지만 담당합니다.
- 실제 주문 책임과 최종 검증은 Spring 백엔드가 가집니다.
- 사용자별 자동매매 ON/OFF, KIS 인증정보, 현재가, 잔고, 보유수량, 가격 괴리, 신호 만료를 모두 확인한 뒤 주문합니다.
- 실전 운영 전에는 모의투자, 소액 주문, 주문 로그, DB 상태 전이를 반드시 확인해야 합니다.

## 시스템 구성

프로젝트는 세 개의 런타임과 보조 인프라로 구성됩니다.

| 구성 | 위치 | 역할 | 기본 포트 |
|---|---|---|---|
| Frontend | `frontend/` | Next.js 사용자 화면 | `3000` |
| Backend | `backend/` | Spring Boot 인증, 사용자 설정, KIS 연동, 주문 검증/집행 | `8000` |
| AI Server | `ai_server/`, `src/` | FastAPI RAG, 멀티 에이전트 분석, 신호 생성 | `8001` |
| Redis | Docker/local | 캐시와 작업 상태 보조 | `6379` |
| PostgreSQL | Docker/local | Spring 백엔드 영속 데이터 | `5432` |

프론트엔드는 `NEXT_PUBLIC_API_BASE`로 Spring 백엔드를 호출합니다. 기본 로컬 값은 `http://localhost:8000`입니다.

## 핵심 실행 흐름

```text
사용자
  -> 프론트엔드에서 회원가입/로그인
  -> 투자 프로필 저장
  -> KIS 앱키/시크릿/계좌 설정 및 검증
  -> 자동매매 ON/OFF 설정

Spring 백엔드
  -> 15분마다 자동매매 대상 사용자 조회
  -> 사용자 투자 프로필과 함께 AI 서버에 multi-theme 주도주 분석 요청

AI 서버
  -> 테마 후보 종목 수집 데이터, RAG 문서, 시장 데이터를 결합
  -> Analyst / Quant / Chartist가 공통 시장 관점 평가
  -> RiskManager가 사용자 투자 성향을 반영해 최종 action/confidence/risk 조정
  -> 백엔드 내부 API로 매매 신호 제출

Spring 백엔드
  -> trade_signals에 PENDING 신호 저장
  -> KIS 인증정보, 자동매매 상태, 잔고, 보유수량, 현재가, 가격 괴리, 만료 여부 확인
  -> 조건 통과 시 KisClient로 주문
  -> trade_signal_executions에 실행/거절/실패 결과 저장
```

## 프로젝트 구조

```text
HQA_Project/
├── frontend/                  # Next.js App Router 프론트엔드
│   └── src/
│       ├── app/               # home, dashboard, stocks, analysis, login/signup, KIS 설정
│       ├── components/        # 공통 UI 컴포넌트
│       ├── lib/               # Spring 백엔드 API 클라이언트, 포맷/옵션 유틸
│       └── types/             # API/백테스트 타입
├── backend/                   # Spring Boot 백엔드
│   ├── src/main/java/com/hqa/backend/
│   │   ├── config/            # CORS, HTTPS, API key, rate limit, WebSocket, WebClient
│   │   ├── controller/        # Auth, Stock, Chart, Analysis, Trading, Internal API
│   │   ├── dto/               # 요청/응답 contract
│   │   ├── entity/            # JPA entity
│   │   ├── repository/        # Spring Data repository
│   │   ├── scheduler/         # StockScheduler, TradingScheduler
│   │   └── service/           # Auth, KIS, AI proxy, 신호 저장/집행, 가격 스냅샷
│   └── src/main/resources/
│       ├── application.yml
│       └── db/migration/      # Flyway schema migration
├── ai_server/                 # FastAPI 엔드포인트와 런타임 task 상태
├── src/
│   ├── agents/                # Analyst, Quant, Chartist, RiskManager, Supervisor, Orchestrator
│   ├── rag/                   # canonical retriever, BM25, vector store, reranker, OCR
│   ├── retrieval/             # 경량 retrieval 서비스 계층
│   ├── ingestion/             # Naver news/forum/theme, DART, KIS chart 수집
│   ├── data_pipeline/         # raw 수집/price/RAG builder
│   ├── runner/                # 자동 분석, multi-theme runner, scheduler, trade signal submitter
│   ├── tools/                 # 에이전트용 finance/search/realtime/RAG/chart 도구
│   ├── tracing/               # agent trace와 token usage 기록
│   └── utils/                 # KIS auth, prompt loader, stock mapper, portfolio context
├── prompts/                   # Agent별 프롬프트 템플릿
├── scripts/                   # 데이터 수집, RAG 빌드, 데모/헬스체크/dev 실행 스크립트
├── backtesting/               # point-in-time 주도주 백테스트와 검증 도구
├── config/                    # watchlist, theme_trading 전략/스케줄/리스크 설정
├── data/                      # raw, corpus, index, market data, backtest 산출물
└── tests/                     # Python 테스트
```

## 주요 기능

### 프론트엔드

- 회원가입, 로그인, 로그아웃, 현재 사용자 조회
- 투자 성향 온보딩 및 저장
- KIS 인증정보 저장, 마스킹 상태 조회, 연결 검증
- 대시보드, 종목 검색, 현재가, 차트, 뉴스, 공시 조회
- 분석 요청, 분석 결과 조회, SSE 진행 상태 조회
- 자동매매 상태 ON/OFF, 잔고, 주문/신호 상태 조회
- 백테스트 결과 대시보드와 AI 전략 비교 화면

### Spring 백엔드

백엔드는 사용자 데이터와 주문 실행의 기준 서버입니다.

- 세션 기반 사용자 인증: `/api/v1/auth/**`
- 사용자 투자 프로필: `/api/v1/auth/me/preference`
- KIS 인증정보 저장/검증: `/api/v1/auth/me/kis`, `/api/v1/auth/me/kis/verify`
- 종목/시세/뉴스/공시/차트: `/api/v1/stocks/**`, `/api/v1/charts/**`
- AI 분석 프록시와 결과 저장: `/api/v1/analysis/**`, `/api/v1/chat`
- 자동매매, 잔고, 직접 주문, 신호 조회: `/api/v1/trading/**`
- AI 서버용 내부 API: `/api/v1/internal/trading/signals`, `/api/v1/internal/market/price-snapshots`

보안/운영 관련 설정:

- 일반 사용자 API는 세션 쿠키로 인증합니다.
- `/api/v1/internal/**`는 `HQA_INTERNAL_TOKEN`과 `X-HQA-Internal-Token`으로 보호할 수 있습니다.
- 운영 환경의 `/api/v1/admin/**`는 `SECRET_KEY`와 `X-API-Key`로 보호합니다.
- local/dev 환경에서는 개발 편의상 전역 API key 검증을 우회합니다.
- 운영 환경에서는 `HttpsEnforcementFilter`가 plain HTTP 요청을 거부하고 HSTS를 설정합니다.
- `RateLimitInterceptor`가 IP 기준 분당 요청 수를 제한합니다.
- KIS 앱키/시크릿/계좌번호는 `HQA_KIS_ENC_KEY` 기반 AES-GCM으로 암호화해 저장합니다.

### AI 서버

AI 서버는 RAG, 에이전트 분석, 주도주 선별, 신호 생성을 담당합니다.

- RAG 채팅: `POST /chat`
- 추천 질문: `POST /suggest`
- 단일 종목 분석: `POST /analyze`, `GET /analyze/{task_id}`
- 테마 주도주 분석: `POST /theme/analyze`, `GET /theme/analyze/{task_id}`
- 단일/다중 테마 런타임 분석: `POST /runtime/theme-trade`, `POST /runtime/multi-theme-trade`
- 다중 테마 루프 실행/중지/상태: `/runtime/multi-theme-trade/loop/*`
- 런타임 task 조회: `GET /runtime/tasks/{task_id}`
- 백테스트 결과 저장/조회: `POST /backtest/results`, `GET /backtest/results/{task_id}`
- legacy 거래 판단 preview/execute API 유지

새 자동매매 흐름에서는 AI 서버가 직접 주문하지 않습니다. `user_id`, `investor_profile`, `strategy_profile`을 받아 최종 신호를 만들고, 백엔드 내부 API로 제출합니다.

### SupervisorAgent 보류 상태

`src/agents/supervisor.py`는 자연어 질문을 분석해 종목 분석, 빠른 분석, 시세 조회, 비교, 테마 탐색으로 라우팅하기 위한 대화형 오케스트레이터입니다. 현재 백엔드 `/api/v1/chat`와 AI 서버 `POST /chat` 경로는 남아 있지만, 프론트엔드에는 챗봇/자연어 질의 화면이 없고 자동매매, 테마 주도주 선별, 단일 종목 분석의 핵심 실행 경로에서는 사용하지 않습니다.

따라서 `SupervisorAgent`는 삭제하지 않고 보류 상태로 유지합니다. 향후 챗봇 UI나 자연어 기반 분석 라우팅을 다시 제공할 때 재활성화할 수 있으며, 그 전까지 운영 판단은 `theme_orchestrator.py`, `graph.py`, `runner/` 계층을 기준으로 봅니다.

## 에이전트와 의사결정

`src/agents/theme_orchestrator.py`가 테마 후보를 평가하고 에이전트 결과를 결합합니다.

- 후보군은 테마 멤버십, raw 문서, corpus, market data에서 가져옵니다.
- 문서와 시장 데이터가 모두 없는 후보는 평가에서 제외합니다.
- `data_coverage`는 신뢰도와 데이터 충분성 판단에 쓰며, 최종 순위에 데이터 존재량을 직접 가산하지 않습니다.
- `Analyst`는 기업/산업/정책/뉴스 관점의 헤게모니를 평가합니다.
- `Quant`는 가격, 수급, 재무/정량 지표를 평가합니다.
- `Chartist`는 차트와 가격 스냅샷을 평가합니다.
- `RiskManager`만 사용자 투자 프로필을 반영해 최종 `action`, `confidence`, `risk_level`, `position_size`, `stop_loss`, `risk_factors`를 조정합니다.

## 자동매매와 안전장치

자동매매는 두 경로가 있습니다.

1. 백엔드 주도 흐름: `TradingScheduler`가 자동매매 사용자를 조회하고 AI 서버에 multi-theme 분석을 요청합니다.
2. Python runner 흐름: `src/runner/multi_theme_scheduler.py`가 short/long 전략 스케줄을 돌리고 필요하면 백엔드 내부 API에 신호를 제출합니다.

매매 신호 상태:

| 상태 | 의미 |
|---|---|
| `PENDING` | 집행 대기 |
| `EXECUTED` | 주문 성공 |
| `REJECTED` | 설정 누락, 자동매매 OFF, 현재가 실패, 가격 괴리, 보유수량 없음 등으로 주문하지 않음 |
| `EXPIRED` | 신호 만료 |
| `FAILED` | KIS 토큰, 잔고, 주문 API 등 실행 중 실패 |

주요 안전장치:

- 사용자별 `auto_trade_enabled`가 꺼져 있으면 집행하지 않습니다.
- KIS 인증정보가 없거나 검증되지 않으면 집행하지 않습니다.
- 신호가 만료되면 집행하지 않습니다.
- 현재가 조회 실패 시 개별 신호만 거절합니다.
- 신호 가격과 현재가 괴리가 `MAX_PRICE_DRIFT_PCT`를 넘으면 거절합니다.
- 매수는 현금 부족을 확인합니다.
- 매도는 보유수량이 없으면 거절합니다.
- 실전 주문은 설정에서 명시적으로 허용해야 합니다.
- `config/watchlist.yaml`과 `config/theme_trading.yaml`에서 스케줄, universe filter, 리스크 제한, signal quality filter, order guard를 조정합니다.

## 데이터와 RAG 파이프라인

데이터 파이프라인은 테마 후보 종목을 찾고, 종목별 raw 데이터를 수집한 뒤, RAG와 백테스트에서 사용할 corpus/index/market data를 생성합니다.

```text
테마 키워드
  -> Naver theme 후보 종목
  -> data/raw/theme_targets/<theme_key>.jsonl
  -> news / dart / forum / chart 수집
  -> data/raw/<source>/<theme_key>.jsonl
  -> data/corpora/<theme_key>/
  -> data/market_data/<theme_key>/
  -> data/canonical_index/<theme_key>/
  -> RAG / 에이전트 / 백테스트
```

주요 데이터 영역:

- `data/raw/`: 수집 원천 데이터
- `data/corpora/`: RAG용 통합 문서
- `data/market_data/`: 차트/가격 기반 시장 데이터
- `data/canonical_index/`: canonical retriever용 BM25/vector/corpus
- `data/vector_stores/`, `data/bm25/`: legacy 또는 보조 index
- `data/backtest_results/`: 백테스트/검증/LLM 실험 산출물
- `data/reports/`: 수집 품질 리포트
- `data/cache/`, `data/token/`, `data/orders/`: 로컬 런타임 산출물이며 커밋 대상이 아닙니다.

테마 수집과 RAG 빌드:

```bash
python scripts/theme_pipeline.py \
  --theme AI \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --theme-max-stocks 30 \
  --enabled-sources news,dart,forum,chart
```

기존 raw 데이터로 RAG 자산만 다시 빌드:

```bash
python scripts/build_rag.py --theme AI --theme-key ai --mode append-new-stocks --stats
```

데이터 연결 확인:

```bash
python scripts/verify_data_connection.py
```

## 백테스팅과 검증

`backtesting/`은 과거 시점 기준으로 테마 주도주 선별 결과를 검증합니다. point-in-time 데이터, 테마 멤버십, temporal RAG, proof validation 도구를 포함합니다.

단일 백테스트:

```bash
python backtesting/leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --from-date 20250101 \
  --to-date 20251231 \
  --rebalance W \
  --top-n 5 \
  --hold-days 5
```

파라미터 sweep:

```bash
python backtesting/sweep_leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --rebalances W \
  --top-ns 3,5,7 \
  --hold-days 3,5,7
```

주요 결과 문서:

- `backtesting/README.md`
- `data/backtest_results/README.md`
- `data/backtest_results/validation/README.md`
- `data/backtest_results/llm_final/README.md`

## 설치

### 공통

```bash
cp .env.example .env
```

### Python / AI 서버

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

브라우저 기반 수집을 사용할 경우:

```bash
playwright install chromium
```

### 백엔드

백엔드는 Java 17과 Maven이 필요합니다.

```bash
cd backend
mvn test
```

### 프론트엔드

프론트엔드는 Next.js 15, React 19 기반입니다. 현재 저장소에는 lockfile이 없으므로 사용하는 패키지 매니저를 팀 기준으로 하나 정해 고정하는 것이 좋습니다.

```bash
cd frontend
npm install
npm run dev
```

## 환경 변수

### AI 서버

```env
LLM_PROVIDER=ollama
HQA_DATA_DIR=./data
OLLAMA_BASE_URL=http://localhost:11435
OLLAMA_ANALYST_MODEL=gemma4:12b
OLLAMA_SUMMARY_MODEL=gemma4:e4b
OLLAMA_QUANT_MODEL=gemma4:12b
OLLAMA_CHARTIST_MODEL=qwen3.5:9b
OLLAMA_RISK_MANAGER_MODEL=gemma4:12b
```

Ollama 사용:

```bash
OLLAMA_HOST=127.0.0.1:11435 ollama serve
ollama pull qwen3.5:9b
ollama pull gemma4:12b
ollama pull gemma4:e4b
```

외부 LLM 없이 스모크 테스트만 할 때:

```env
LLM_PROVIDER=mock
```

### 백엔드

```env
PORT=8000
DATABASE_URL=jdbc:postgresql://localhost:5432/hqa
DATABASE_USERNAME=postgres
DATABASE_PASSWORD=
REDIS_HOST=localhost
REDIS_PORT=6379
AI_SERVER_URL=http://localhost:8001
HQA_INTERNAL_TOKEN=
SECRET_KEY=change-me-in-production
CORS_ORIGINS=http://localhost:3000,http://localhost:8501
HQA_KIS_ENC_KEY=
```

`HQA_INTERNAL_TOKEN`을 설정하면 AI 서버와 Python runner가 백엔드 내부 API를 호출할 때 같은 값을 `X-HQA-Internal-Token`으로 보냅니다. 일반 사용자 API는 세션 쿠키로 인증하며, `SECRET_KEY`/`X-API-Key`는 운영 환경의 `/api/v1/admin/**` 관리 API 보호에만 사용합니다.

### KIS

사용자별 KIS 키/계좌 설정은 프론트엔드에서 저장하고 백엔드가 암호화합니다. 일부 legacy runner와 직접 실행 경로에서는 전역 환경 변수도 지원합니다.

```env
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=

KIS_PAPER_APP_KEY=
KIS_PAPER_APP_SECRET=
KIS_PAPER_ACCOUNT_NO=
```

## 실행

### 통합 로컬 실행 스크립트

```bash
./scripts/dev.sh
```

기본 포트:

- AI 서버: `http://localhost:8001`
- 백엔드: `http://localhost:8000`
- 프론트엔드: `http://localhost:3000`

중지:

```bash
./scripts/kill-dev.sh
```

### 개별 실행

백엔드:

```bash
cd backend
mvn spring-boot:run
```

AI 서버:

```bash
uvicorn ai_server.app:app --host 0.0.0.0 --port 8001
```

프론트엔드:

```bash
cd frontend
npm run dev
```

### Docker Compose

```bash
docker compose up --build
```

현재 Compose 파일은 Spring 백엔드(`backend`), AI 서버(`ai`), Next.js 프론트엔드(`frontend`), Redis, PostgreSQL을 함께 띄우는 로컬 통합 개발 구성입니다.

## 주요 API

### Spring 백엔드

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/health` | 백엔드 상태 |
| `POST` | `/api/v1/auth/signup` | 회원가입 |
| `POST` | `/api/v1/auth/login` | 로그인 |
| `POST` | `/api/v1/auth/logout` | 로그아웃 |
| `GET` | `/api/v1/auth/me` | 현재 사용자 |
| `GET/PUT` | `/api/v1/auth/me/preference` | 투자 프로필 조회/저장 |
| `GET/PUT` | `/api/v1/auth/me/kis` | KIS 설정 조회/저장 |
| `POST` | `/api/v1/auth/me/kis/verify` | KIS 연결 검증 |
| `GET` | `/api/v1/stocks/search` | 종목 검색 |
| `GET` | `/api/v1/stocks/{stockCode}/price` | 현재가 조회 |
| `GET` | `/api/v1/stocks/{stockCode}/news` | 뉴스 조회 |
| `GET` | `/api/v1/stocks/{stockCode}/disclosures` | 공시 조회 |
| `GET` | `/api/v1/stocks/indices` | 시장지수 조회 |
| `GET` | `/api/v1/charts/{stockCode}/history` | 차트 히스토리 |
| `POST` | `/api/v1/analysis` | AI 분석 요청 |
| `POST` | `/api/v1/analysis/bulk` | bulk 분석 요청 |
| `GET` | `/api/v1/analysis/{taskId}` | 분석 결과 조회 |
| `GET` | `/api/v1/analysis/{taskId}/stream` | 분석 진행 SSE |
| `GET` | `/api/v1/analysis/history/list` | 분석 이력 |
| `POST` | `/api/v1/chat` | AI 채팅 프록시 |
| `GET` | `/api/v1/trading/status` | 자동매매 상태 |
| `POST` | `/api/v1/trading/auto` | 자동매매 설정 |
| `GET` | `/api/v1/trading/signals` | 최근 신호 |
| `GET` | `/api/v1/trading/orders` | 주문 조회 |
| `GET` | `/api/v1/trading/balance` | KIS 잔고 |
| `POST` | `/api/v1/trading/buy` | 직접 매수 |
| `POST` | `/api/v1/trading/sell` | 직접 매도 |
| `POST` | `/api/v1/internal/trading/signals` | AI 서버 신호 제출 |
| `POST` | `/api/v1/internal/market/price-snapshots` | 내부 현재가 스냅샷 조회 |

### AI 서버

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/health` | AI 서버 상태 |
| `POST` | `/chat` | RAG/에이전트 채팅 |
| `POST` | `/suggest` | 추천 질문 |
| `POST` | `/analyze` | 단일 종목 분석 |
| `GET` | `/analyze/{task_id}` | 단일 종목 분석 결과 |
| `POST` | `/theme/analyze` | 테마 주도주 분석 |
| `GET` | `/theme/analyze/{task_id}` | 테마 분석 결과 |
| `POST` | `/runtime/theme-trade` | 단일 테마 주도주 거래 신호 생성 |
| `POST` | `/runtime/multi-theme-trade` | 다중 테마 주도주 신호 생성 |
| `POST` | `/runtime/multi-theme-trade/loop/start` | 다중 테마 루프 시작 |
| `POST` | `/runtime/multi-theme-trade/loop/stop` | 다중 테마 루프 중지 |
| `GET` | `/runtime/multi-theme-trade/loop/status` | 다중 테마 루프 상태 |
| `GET` | `/runtime/tasks/{task_id}` | 런타임 작업 결과 |
| `POST` | `/backtest/results` | 백테스트 결과 저장 |
| `GET` | `/backtest/results/{task_id}` | 백테스트 결과 조회 |

## 데이터베이스와 마이그레이션

Spring 백엔드는 PostgreSQL과 Flyway를 사용합니다. `spring.jpa.hibernate.ddl-auto=validate`이므로 schema 변경은 반드시 migration으로 관리합니다.

현재 주요 migration:

- `V1__baseline.sql`: Flyway 도입 시점 baseline schema
- `V2__align_schema_with_entities.sql`: Hibernate validate에 맞춘 타입 정렬
- `V3__stocks.sql`: 종목 master table과 검색 index
- `V4__user_secrets_text.sql`: 암호화된 KIS credential 저장 컬럼 확장
- `V5__trade_signals.sql`: AI 매매 신호와 실행 결과 저장

주요 테이블:

- `users`, `user_preferences`, `user_secrets`
- `analysis_records`
- `stocks`, `stock_cache`
- `trade_signals`, `trade_signal_executions`
- `error_logs`

## 테스트와 검증

### Python

```bash
venv/bin/python -m pytest
```

최근 주도주/신호 생성 흐름의 핵심 테스트:

```bash
venv/bin/python -m pytest \
  tests/test_risk_manager_cross_validation.py \
  tests/test_trade_signal_submitter.py \
  tests/test_price_snapshot_client.py \
  tests/test_theme_orchestrator_json.py \
  tests/test_multi_theme_leader_trading_runner.py \
  tests/test_multi_theme_scheduler.py
```

### Java

```bash
cd backend
mvn test
```

### Frontend

```bash
cd frontend
NEXT_PUBLIC_API_BASE=https://localhost:8000 npm run build
```

`frontend/package.json`에는 `lint` script가 남아 있지만 Next.js 15에서는 `next lint`가 제거되었습니다. lint 체계를 쓰려면 ESLint 설정을 별도로 정리해야 합니다.

## 운영 주의사항

- 자동매매의 최종 책임은 백엔드에 있습니다.
- AI 서버가 생성한 신호는 주문 명령이 아니라 백엔드 검증 대상입니다.
- 현재가 조회 실패, 가격 괴리 초과, 신호 만료, 자동매매 OFF는 전체 작업 실패가 아니라 개별 신호 상태로 기록됩니다.
- KIS 키/계좌가 없거나 검증되지 않은 사용자는 주문 대상에서 제외됩니다.
- 실전 운영 전에는 모의투자, 소액 주문, 주문 로그, DB 상태 전이를 확인해야 합니다.
- `HQA_KIS_ENC_KEY`는 운영에서 반드시 안전하게 관리해야 합니다. 키를 잃으면 기존 KIS credential 복호화가 불가능합니다.
- `HQA_INTERNAL_TOKEN`을 비워두면 내부 API 토큰 검증이 비활성화됩니다. 운영에서는 반드시 설정하세요.
- AI 서버 CORS는 현재 전체 허용입니다. 외부 노출 시 reverse proxy 또는 코드 설정으로 origin 제한이 필요합니다.
- `data/`와 `data/backtest_results/`는 산출물이 많아 diff가 커질 수 있습니다.
- `.understand-anything/`, `frontend/.next/`, `frontend/node_modules/`, `backend/target/`, `backend/.m2/`, `logs/`는 일반 커밋 대상이 아닙니다.

## 유용한 스크립트

| 스크립트 | 용도 |
|---|---|
| `scripts/dev.sh` | AI 서버, Spring 백엔드, 프론트엔드 동시 실행 |
| `scripts/kill-dev.sh` | dev 서버 포트 정리 |
| `scripts/healthcheck.py` | 런타임 health check |
| `scripts/theme_pipeline.py` | 테마 후보 수집부터 RAG 자산 생성까지 실행 |
| `scripts/build_rag.py` | raw 데이터 기반 RAG 자산 재생성 |
| `scripts/run_theme_orchestrator.py` | 테마 주도주 오케스트레이터 실행 |
| `scripts/run_theme_batch.py` | 테마 batch 실행 |
| `scripts/run_theme_paper_trading.py` | multi-theme LLM paper trading 실행 |
| `scripts/download_dart_corp_codes.py` | DART 기업 코드 다운로드 |

## 관련 문서

- 백테스팅 상세: `backtesting/README.md`
- 테마 멤버십 데이터: `data/raw/theme_membership/README.md`
- 백테스트 결과 해석: `data/backtest_results/README.md`
- 검증 결과 해석: `data/backtest_results/validation/README.md`
- LLM 최종 실험 결과: `data/backtest_results/llm_final/README.md`
- 과거/초기 기획 참고: `README2.md`
