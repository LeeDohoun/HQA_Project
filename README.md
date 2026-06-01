# HQA Project

HQA는 한국 주식 테마 주도주를 분석하고, 사용자 투자 프로필을 반영한 매매 신호를 생성한 뒤, Spring 백엔드가 KIS 계좌/잔고/현재가를 검증해 주문 집행까지 담당하는 통합 투자 분석 프로젝트입니다.

현재 프로젝트는 세 개의 런타임으로 나뉩니다.

- `frontend`: Next.js 기반 사용자 화면
- `backend`: Spring Boot 기반 인증, 사용자 설정, KIS 연동, 신호 저장/집행 서버
- `ai_server`: FastAPI 기반 RAG, 멀티 에이전트 분석, 주도주 선별, 매매 신호 생성 서버

AI 서버는 주문을 직접 실행하지 않습니다. AI 서버는 분석과 신호 생성까지만 담당하고, 실제 주문 책임은 백엔드가 가집니다.

## 현재 핵심 흐름

```text
프론트엔드
  -> 회원가입/로그인
  -> 투자 프로필 저장
  -> KIS 키/계좌 설정
  -> 자동매매 ON/OFF 설정

백엔드
  -> 15분마다 자동매매 대상 사용자 조회
  -> AI 서버에 사용자 프로필 포함 주도주 분석 요청

AI 서버
  -> 수집 데이터/RAG/시장 데이터 기반 테마 주도주 후보 평가
  -> Analyst / Quant / Chartist는 공통 시장 평가 수행
  -> RiskManager가 사용자 투자 프로필을 반영해 최종 판단
  -> 백엔드 내부 API로 매매 신호 제출

백엔드
  -> trade_signals에 신호 저장
  -> PENDING 신호 조회
  -> 자동매매 설정, KIS 인증정보, 잔고, 보유수량, 현재가 확인
  -> 가격 괴리/만료/설정 오류는 개별 신호 거절로 기록
  -> 조건 통과 시 KisClient로 직접 주문
  -> trade_signal_executions에 결과 저장
```

## 프로젝트 구조

```text
HQA_Project/
├── frontend/                  # Next.js 프론트엔드
│   └── src/
│       ├── app/               # dashboard, login, signup 등 화면
│       ├── components/        # 공통 UI 컴포넌트
│       ├── lib/               # 백엔드 API 클라이언트
│       └── types/             # API 타입
├── backend/                   # Spring Boot 백엔드
│   ├── src/main/java/com/hqa/backend/
│   │   ├── controller/        # REST API
│   │   ├── entity/            # JPA 엔티티
│   │   ├── repository/        # JPA repository
│   │   ├── scheduler/         # TradingScheduler
│   │   └── service/           # KIS, AI 서버, 신호 집행 서비스
│   └── src/main/resources/
│       ├── application.yml
│       └── db/migration/      # Flyway migration
├── ai_server/                 # FastAPI AI 서버
├── src/
│   ├── agents/                # Analyst, Quant, Chartist, RiskManager
│   ├── rag/                   # canonical/BM25/vector retrieval
│   ├── runner/                # 테마 주도주 실행기, 신호 제출기
│   ├── ingestion/             # 뉴스, 공시, 포럼, 차트 수집
│   └── tools/                 # 에이전트 도구
├── prompts/                   # 에이전트 프롬프트
├── scripts/                   # 데이터 수집, RAG 빌드, 데모 실행
├── backtesting/               # point-in-time 주도주 백테스트
├── data/                      # raw/corpus/index/market/backtest 데이터
└── tests/                     # Python 테스트
```

## 주요 기능

### 프론트엔드

- 사용자 가입/로그인
- 대시보드
- 종목 검색, 가격, 차트, 뉴스, 공시 조회
- 사용자 투자 프로필 저장
- KIS 인증정보 저장/검증
- 자동매매 설정
- 분석 결과와 거래 관련 상태 조회

프론트엔드는 `NEXT_PUBLIC_API_BASE`로 Spring 백엔드를 호출합니다. 기본값은 `http://localhost:8000`입니다.

### 백엔드

백엔드는 사용자와 주문 실행의 기준 서버입니다.

- 인증/사용자 정보: `/api/v1/auth`
- 사용자 투자 프로필: `/api/v1/auth/me/preference`
- KIS 키/계좌 설정: `/api/v1/auth/me/kis`
- 종목/시세/뉴스/공시: `/api/v1/stocks`, `/api/v1/charts`
- AI 분석 프록시: `/api/v1/analysis`, `/api/v1/chat`
- 거래 상태/자동매매/잔고/주문: `/api/v1/trading`
- AI 서버 내부 신호 제출: `/api/v1/internal/trading/signals`

스키마 변경은 Flyway로 관리합니다. 현재 주요 migration은 다음과 같습니다.

- `V1__baseline.sql`
- `V2__align_schema_with_entities.sql`
- `V3__stocks.sql`
- `V4__user_secrets_text.sql`
- `V5__trade_signals.sql`

### AI 서버

AI 서버는 분석과 신호 생성을 담당합니다.

- RAG 기반 질의응답: `/chat`, `/suggest`
- 단일 종목 분석: `/analyze`
- 테마 주도주 분석: `/theme/analyze`
- 런타임 주도주 거래 분석: `/runtime/theme-trade`, `/runtime/multi-theme-trade`
- 백테스트 결과 저장/조회: `/backtest/results`
- legacy 거래 판단 API: `/trading/decision/preview`, `/trading/decision/execute`

새 자동매매 흐름에서 AI 서버는 직접 주문하지 않습니다. `/runtime/multi-theme-trade`가 `user_id`와 `investor_profile`을 받으면 RiskManager 판단 이후 백엔드 내부 API로 신호를 제출합니다.

### 주도주 분석

`src/agents/theme_orchestrator.py`가 테마 후보를 평가합니다.

- 후보군은 수집된 테마/문서/시장 데이터에서 가져옵니다.
- 문서와 시장 데이터가 모두 없는 후보는 제외합니다.
- `data_coverage`는 평가 신뢰도/데이터 충분성 용도로만 사용합니다.
- 최종 주도주 순위에는 데이터 존재량을 직접 가산하지 않습니다.
- `Analyst`, `Quant`, `Chartist`는 공통 시장 관점으로 평가합니다.
- `RiskManager`만 사용자 투자 프로필을 반영해 최종 `action`, `confidence`, `risk_level`, `position_size`, `stop_loss`, `risk_factors`를 조정합니다.

### 매매 신호와 백엔드 집행

AI 서버가 제출하는 신호는 백엔드의 `trade_signals`에 저장됩니다.

주요 상태:

- `PENDING`: 집행 대기
- `EXECUTED`: 주문 성공
- `REJECTED`: 설정, 만료, 현재가 실패, 가격 괴리 등으로 주문하지 않음
- `EXPIRED`: 신호 만료
- `FAILED`: KIS 토큰/잔고/주문 API 실패 등 실행 중 실패

실제 주문 결과는 `trade_signal_executions`에 저장됩니다. 현재가 조회 실패, 가격 괴리 초과, 자동매매 OFF, KIS 인증정보 누락은 전체 스케줄러 실패가 아니라 해당 신호의 거절/실패 결과로 기록됩니다.

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

로컬 PostgreSQL을 사용할 경우 기본 접속값은 `application.yml` 기준입니다.

```env
DATABASE_URL=jdbc:postgresql://localhost:5432/hqa
DATABASE_USERNAME=postgres
DATABASE_PASSWORD=
```

### 프론트엔드

```bash
cd frontend
pnpm install
pnpm dev
```

## 환경 변수

### AI 서버

```env
LLM_PROVIDER=ollama
HQA_DATA_DIR=./data
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_INSTRUCT_MODEL=qwen3.5:9b
OLLAMA_THINKING_MODEL=gemma4:e4b
OLLAMA_THINKING_VALIDATOR_MODEL=
OLLAMA_VISION_MODEL=llava:13b
```

Ollama를 사용할 경우:

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
GEMINI_INSTRUCT_MODEL=gemini-2.5-flash-lite
GEMINI_THINKING_MODEL=gemini-2.5-pro
GEMINI_THINKING_VALIDATOR_MODEL=gemini-2.5-flash
GEMINI_VISION_MODEL=gemini-2.5-flash
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
```

`HQA_INTERNAL_TOKEN`을 설정하면 AI 서버가 백엔드 내부 신호 API를 호출할 때 같은 값을 `X-HQA-Internal-Token`으로 보내야 합니다.

### KIS

백엔드 주문 집행에는 사용자별 KIS 키/계좌 설정이 필요합니다. 기본 전역 환경 변수도 지원합니다.

```env
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=
HQA_KIS_ENC_KEY=
```

AI 서버의 legacy `TradeExecutor` 경로에서는 모의투자 값을 별도로 사용할 수 있습니다.

```env
KIS_PAPER_APP_KEY=
KIS_PAPER_APP_SECRET=
KIS_PAPER_ACCOUNT_NO=
```

## 실행

### 백엔드

```bash
cd backend
mvn spring-boot:run
```

기본 포트는 `8000`입니다.

### AI 서버

```bash
uvicorn ai_server.app:app --host 0.0.0.0 --port 8001
```

### 프론트엔드

```bash
cd frontend
pnpm dev
```

기본 포트는 `3000`입니다.

### Docker Compose

```bash
docker compose up --build
```

현재 Compose 파일은 Python/FastAPI `api`, AI 서버 `ai`, Redis, PostgreSQL을 띄우는 구성입니다. Spring 백엔드와 Next.js 프론트엔드는 로컬 실행 기준으로 관리됩니다.

## 주요 API

### Spring 백엔드

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/health` | 백엔드 상태 |
| `POST` | `/api/v1/auth/signup` | 회원가입 |
| `POST` | `/api/v1/auth/login` | 로그인 |
| `GET` | `/api/v1/auth/me` | 현재 사용자 |
| `GET/PUT` | `/api/v1/auth/me/preference` | 투자 프로필 조회/저장 |
| `GET/PUT` | `/api/v1/auth/me/kis` | KIS 설정 조회/저장 |
| `POST` | `/api/v1/auth/me/kis/verify` | KIS 연결 검증 |
| `GET` | `/api/v1/stocks/search` | 종목 검색 |
| `GET` | `/api/v1/stocks/{stockCode}/price` | 현재가 조회 |
| `GET` | `/api/v1/charts/{stockCode}/history` | 차트 히스토리 |
| `POST` | `/api/v1/analysis` | AI 분석 요청 |
| `GET` | `/api/v1/analysis/{taskId}` | 분석 결과 조회 |
| `GET` | `/api/v1/analysis/{taskId}/stream` | 분석 진행 SSE |
| `GET` | `/api/v1/trading/status` | 거래 상태 |
| `POST` | `/api/v1/trading/auto` | 자동매매 설정 |
| `GET` | `/api/v1/trading/signals` | 최근 신호 |
| `GET` | `/api/v1/trading/balance` | KIS 잔고 |
| `POST` | `/api/v1/internal/trading/signals` | AI 서버 신호 제출 |

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
| `POST` | `/runtime/multi-theme-trade` | 다중 테마 주도주 신호 생성 |
| `GET` | `/runtime/tasks/{task_id}` | 런타임 작업 결과 |
| `POST` | `/backtest/results` | 백테스트 결과 저장 |
| `GET` | `/backtest/results/{task_id}` | 백테스트 결과 조회 |

`/runtime/multi-theme-trade` 요청에는 필요에 따라 `user_id`, `investor_profile`, `strategy_profile`을 포함할 수 있습니다.

## 데이터 수집과 RAG 빌드

수집 파이프라인은 테마 후보 종목을 찾고, 종목별 raw 데이터를 수집한 뒤, RAG가 사용할 수 있는 corpus/index/market data로 빌드합니다.

```text
테마 키워드
  -> Naver theme 후보 종목
  -> data/raw/theme_targets/<theme_key>.jsonl
  -> news / dart / forum / chart 수집
  -> data/raw/<source>/<theme_key>.jsonl
  -> data/corpora/<theme_key>/
  -> data/market_data/<theme_key>/
  -> data/canonical_index/<theme_key>/
  -> RAG / 에이전트 / 백테스트에서 사용
```

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

## 백테스팅

`backtesting/`은 과거 시점 기준으로 테마 주도주 선별 결과를 검증하는 도구입니다.

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

여러 파라미터 조합 비교:

```bash
python backtesting/sweep_leader_backtest.py \
  --theme AI \
  --theme-key ai \
  --rebalances W \
  --top-ns 3,5,7 \
  --hold-days 3,5,7
```

상세 내용은 `backtesting/README.md`를 참고하세요.

## 테스트

### Python

```bash
venv/bin/python -m pytest
```

최근 주도주 신호 생성 흐름의 핵심 테스트:

```bash
venv/bin/python -m pytest \
  tests/test_risk_manager_cross_validation.py \
  tests/test_trade_signal_submitter.py \
  tests/test_theme_orchestrator_json.py \
  tests/test_multi_theme_leader_trading_runner.py
```

### Java

```bash
cd backend
mvn test
```

### Frontend

```bash
cd frontend
pnpm lint
pnpm build
```

## 운영 주의사항

- 자동매매의 최종 책임은 백엔드에 있습니다.
- AI 서버가 생성한 신호는 주문 명령이 아니라 백엔드 검증 대상입니다.
- 현재가 조회 실패, 가격 괴리 초과, 신호 만료, 자동매매 OFF는 개별 신호 상태로 기록됩니다.
- KIS 키/계좌가 없거나 검증되지 않은 사용자는 주문 대상에서 제외됩니다.
- 실전 운영 전에는 모의투자, 소액 주문, 주문 로그, DB 상태 전이를 반드시 확인해야 합니다.
- `data/`와 `data/backtest_results/`는 산출물이 많아 diff가 커질 수 있습니다.
- `.understand-anything/`은 코드 이해 도구 산출물이며 일반 커밋 대상이 아닙니다.

## 관련 문서

- 백테스팅 상세: `backtesting/README.md`
- 백테스트 결과 해석: `data/backtest_results/README.md`
- 검증 결과 해석: `data/backtest_results/validation/README.md`
- LLM 최종 실험 결과: `data/backtest_results/llm_final/README.md`
