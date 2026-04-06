# HQA AI + Data Integration Branch

이 브랜치는 `ai-main`의 에이전트 실행 로직과 `rag-data-pipeline`의 데이터 수집/가공/검색 자산을 합친 AI+데이터 통합 브랜치입니다.  
핵심 목적은 수집 파이프라인을 유지한 채, 이미 생성된 데이터 자산을 에이전트가 실제로 읽고 retrieval 기반 응답과 테마 주도주 선별에 활용하도록 만드는 것입니다.

## 1. 브랜치 한 줄 소개

기존 데이터 파이프라인 산출물을 그대로 재사용해, AI 에이전트가 retrieval와 멀티 에이전트 분석을 실제로 수행하는 통합 실행 브랜치입니다.

## 2. 이 브랜치가 왜 존재하는가

프로젝트는 원래 크게 네 영역으로 나뉘어 있었습니다.

- 백엔드
- 프론트엔드
- AI
- 데이터

이 중 AI 브랜치는 에이전트 실행, 프롬프트, LLM 호출, 최종 응답 생성에 강했고, 데이터 브랜치는 뉴스/공시/포럼/차트 수집과 raw/corpus/index 생성에 강했습니다.  
하지만 둘이 분리돼 있으면 다음 문제가 생깁니다.

- 데이터는 있는데 에이전트가 그 데이터를 바로 못 씀
- 에이전트는 있는데 retrieval 자산과 연결이 약함
- 실행 환경, 경로, `.env`, requirements가 따로 놀 가능성이 큼

이 브랜치는 그 사이를 메우기 위해 존재합니다.  
즉, “데이터를 생산하는 브랜치”와 “에이전트를 실행하는 브랜치”를 하나의 실제 실행 단위로 연결하는 역할입니다.

## 3. 이 브랜치의 책임 범위

### 담당하는 것

- 데이터 수집 파이프라인 자산 재사용
- raw / corpora / market_data / canonical_index / BM25 / vector store 관리
- canonical index 생성 또는 로드
- retrieval 수행
- RAG 컨텍스트 구성
- 에이전트 실행
- 프롬프트 처리
- LLM 호출
- 응답 생성
- 테마 주도주 자동 선별
- AI 서버 제공

### 담당하지 않는 것

- 프론트엔드 UI 구현
- 일반 백엔드 비즈니스 로직 전반
- 사용자 인증/권한/세션 정책
- 프로젝트 전체 오케스트레이션이나 배포 인프라 자체

### 외부와 연결되는 것

- 프론트엔드는 이 브랜치의 응답을 소비
- 백엔드는 이 브랜치를 AI 분석 서비스로 호출 가능
- 외부 LLM/Ollama, Redis, DART, Tavily, KIS 등과 선택적으로 연결

## 4. 백엔드/프론트엔드와의 경계

이 브랜치는 UI를 제공하는 프론트엔드 브랜치가 아닙니다.  
또한 일반 CRUD/API 중심의 백엔드 브랜치도 아닙니다.

이 브랜치는 다음 역할에 집중합니다.

- 데이터를 읽는 검색/추론 계층
- 에이전트 실행 계층
- 분석 결과를 텍스트 또는 JSON으로 반환하는 계층

연결 관점에서 보면:

- 프론트엔드: 이 브랜치의 결과를 화면에 렌더링하는 소비자
- 백엔드: 이 브랜치의 AI 분석 API를 호출하거나 중계하는 소비자
- 이 브랜치: 데이터 + 추론 + 검색 엔진

실제 연결 지점은 주로 아래입니다.

- `main.py`
- `ai_server/app.py`

## 5. 프로젝트 구조

```text
HQA_Project/
├── main.py
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── ai_server/
├── config/
├── data/
├── prompts/
├── scripts/
├── src/
└── tests/
```

### 주요 디렉터리와 역할

#### `main.py`

- CLI 진입점
- 대화형 질문
- 단일 종목 분석
- 빠른 분석
- 테마 주도주 자동 선별

#### `ai_server/`

- FastAPI 기반 AI 서버
- `ai_server/app.py`가 핵심 진입점
- `/health`, `/chat`, `/analyze` 등의 경로 제공

#### `scripts/`

- `healthcheck.py`: 환경 점검
- `verify_data_connection.py`: 데이터 연결 및 retrieval 가능 여부 확인
- `run_agent_demo.py`: 단일 질문 RAG 데모
- `run_theme_orchestrator.py`: 테마 전체를 스캔해 주도주 자동 선별
- `build_rag.py`: raw 데이터를 retrieval 자산으로 변환
- `run_pipeline.py`, `theme_pipeline.py`: 데이터 파이프라인 실행 경로

#### `src/agents/`

- `supervisor.py`: 사용자 질문의 의도를 해석하고 실행 경로 선택
- `analyst.py`: 뉴스/포럼/DART 기반 정성 분석
- `quant.py`: 재무/기초체력 분석
- `chartist.py`: 차트/기술 분석
- `risk_manager.py`: 에이전트 결과를 종합해 최종 판단 생성
- `theme_orchestrator.py`: 테마 후보 추출, 병렬 평가, 주도주 선정

#### `src/tools/`

- 에이전트가 직접 호출하는 도구 계층
- RAG 검색, 차트 분석, 재무 분석, 실시간 시세, 웹 검색 등을 포함

#### `src/rag/`

- canonical index 기반 주 검색 경로
- `canonical_retriever.py`가 핵심 검색 게이트웨이
- raw → canonical index 변환 로직 포함

#### `src/retrieval/`

- pipeline BM25/vector fallback 검색 계층
- canonical index가 없을 때 기존 pipeline 산출물을 읽는 경량 retrieval

#### `src/data_pipeline/`, `src/ingestion/`

- 데이터 수집/정제/가공 보존 영역
- 원칙적으로 유지 대상
- 에이전트 연결을 위해 최소 수정만 허용되는 영역

#### `src/config/`

- `.env` / `.env-ai` 로드
- `HQA_DATA_DIR`, traces, orders 디렉터리 설정

#### `data/`

- 실제 데이터 자산 저장소
- raw, corpora, canonical index, BM25, vector store, market data, reports 포함

#### `tests/`

- 런타임/통합 검증
- 환경 설정, retrieval fallback, RAG 데모, 테마 오케스트레이션 경로 검증

### 처음 보는 사람이 먼저 읽어야 하는 파일

1. `main.py`
2. `src/agents/supervisor.py`
3. `src/agents/theme_orchestrator.py`
4. `src/tools/rag_tool.py`
5. `src/rag/canonical_retriever.py`
6. `ai_server/app.py`
7. `data/canonical_index/<theme>/corpus.jsonl`

## 6. 데이터 구조와 활용 방식

### 데이터 종류

| 데이터 종류 | 위치 | 포맷 | 생성/사용 단계 |
|---|---|---|---|
| raw 뉴스 | `data/raw/news/*.jsonl` | JSONL | 수집 결과, 필요 시 build 입력 |
| raw 공시 | `data/raw/dart/*.jsonl` | JSONL | 수집 결과, 필요 시 build 입력 |
| raw 포럼 | `data/raw/forum/*.jsonl` | JSONL | 수집 결과, 필요 시 build 입력 |
| raw 차트 | `data/raw/chart/*.jsonl` | JSONL | 차트 fallback 입력 |
| 테마 후보 | `data/raw/theme_targets/*.jsonl` | JSONL | 주도주 후보 추출 힌트 |
| corpora | `data/corpora/<theme>/` | JSONL | 테마 문서 집합 |
| market data | `data/market_data/<theme>/*.jsonl` | JSONL | Chartist / price loader 입력 |
| canonical index | `data/canonical_index/<theme>/` | JSONL/JSON | 에이전트 1순위 retrieval 자산 |
| pipeline BM25 | `data/bm25/*.json` | JSON | fallback retrieval |
| pipeline vector store | `data/vector_stores/*.json` | JSON | fallback retrieval |
| reports | `data/reports/*.json` | JSON | 수집 진단/운영 참고 |

### 데이터 흐름

전체 흐름은 아래처럼 볼 수 있습니다.

```text
수집(raw)
  -> corpora / market_data
  -> canonical index 또는 pipeline BM25/vector
  -> retrieval
  -> 에이전트 컨텍스트
  -> LLM 응답 / 주도주 선정
```

### 에이전트가 데이터를 읽는 방식

- 일반 RAG 질의
  - `src/tools/rag_tool.py`
  - `src/rag/canonical_retriever.py`
  - canonical index 우선 검색
  - 없으면 `src/retrieval/services.py` fallback

- 테마 주도주 선별
  - `data/raw/theme_targets/<theme>.jsonl`
  - `data/canonical_index/<theme>/corpus.jsonl`
  - `data/market_data/<theme>/*.jsonl`
  - 위 자산을 함께 읽고 후보를 자동 추출

### 데이터 포함 여부와 재생성 규칙

- raw 데이터가 이미 있으면 그대로 사용 가능
- canonical index가 이미 있으면 재생성 없이 로드
- canonical index가 없고 raw만 있으면 `scripts/build_rag.py`로 선택 생성
- 수집 파이프라인 전체 재실행은 필수가 아님

## 7. 에이전트 동작 흐름

### 일반 질문 / RAG 응답

1. 사용자 질문 입력
2. `SupervisorAgent`가 질의 의도 분류
3. `RAGSearchTool`이 retrieval 수행
4. `CanonicalRetriever`가 canonical index 또는 fallback 검색 수행
5. 검색 결과를 컨텍스트로 정리
6. `AnalystAgent`가 컨텍스트 기반 답변 생성
7. 최종 응답 반환

관련 모듈:

- `main.py`
- `src/agents/supervisor.py`
- `src/tools/rag_tool.py`
- `src/rag/canonical_retriever.py`
- `src/agents/analyst.py`

### 단일 종목 멀티 에이전트 분석

1. 종목명/종목코드 입력
2. `Supervisor` 또는 `main.py --stock` 진입
3. `Analyst`, `Quant`, `Chartist` 실행
4. `RiskManager`가 최종 투자 판단 생성

관련 모듈:

- `main.py`
- `src/agents/graph.py`
- `src/agents/analyst.py`
- `src/agents/quant.py`
- `src/agents/chartist.py`
- `src/agents/risk_manager.py`

### 테마 주도주 자동 선별

1. 테마명 입력 또는 `--theme` 실행
2. `Supervisor` 또는 `run_theme_orchestrator.py` 진입
3. `theme_targets + corpus + market_data` 스캔
4. 후보군 자동 추출
5. 후보별 `Analyst`, `Quant`, `Chartist` 병렬 평가
6. `RiskManager`가 후보 순위와 최종 의견 종합
7. 주도주 `top_n` 반환

관련 모듈:

- `main.py`
- `src/agents/supervisor.py`
- `src/agents/theme_orchestrator.py`
- `src/agents/risk_manager.py`

### retrieval 기반 vs 순수 생성형

- 기본 의도는 retrieval 기반 동작
- 다만 일반 QA나 일부 fallback 경로에서는 순수 생성형 응답도 가능
- 성공 기준은 retrieval가 실제 수행된 경로를 우선으로 봐야 함

## 8. 설치 / 실행 / 환경설정

### Python 버전

- 실제 로컬 검증 기준: Python `3.12.x`
- Dockerfile 기준: Python `3.11-slim`

즉, 로컬 검증 환경과 Docker 베이스가 현재 다릅니다. 이 차이는 문서화가 필요하며, 장기적으로 맞추는 것이 좋습니다.

### 설치

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

추가 런타임:

```bash
playwright install chromium
```

### 환경변수

`.env.example`을 복사해 `.env`를 만듭니다.

```bash
cp .env.example .env
```

필수 또는 사실상 필수:

```env
LLM_PROVIDER=ollama
HQA_DATA_DIR=./data
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_INSTRUCT_MODEL=qwen3.5:9b
OLLAMA_THINKING_MODEL=gemma4:e4b
```

선택:

```env
OLLAMA_THINKING_VALIDATOR_MODEL=
OLLAMA_VISION_MODEL=llava:13b
GOOGLE_API_KEY=
DART_API_KEY=
TAVILY_API_KEY=
KIS_APP_KEY=
KIS_APP_SECRET=
REDIS_URL=redis://localhost:6379/0
OCR_PROVIDER=local
ENABLE_OCR=false
RERANKER_PROVIDER=local
```

참고:

- `.env-ai`가 있으면 `.env`보다 우선
- `LLM_PROVIDER=mock`은 smoke test용
- `.env`가 없어도 기본값으로 부팅되지만, 실제 분석 경로는 LLM 준비가 필요

### Ollama 준비

```bash
ollama serve
ollama pull qwen3.5:9b
ollama pull gemma4:e4b
```

### 최소 실행 시나리오

1. 환경 점검

```bash
python3 scripts/healthcheck.py
```

2. 데이터 연결 점검

```bash
python3 scripts/verify_data_connection.py --data-dir ./data
```

3. RAG 데모

```bash
python3 scripts/run_agent_demo.py --query "2차전지 시장 전망 요약" --data-dir ./data
```

4. 테마 주도주 선별

```bash
python3 scripts/run_theme_orchestrator.py --theme 2차전지 --data-dir ./data --candidate-limit 3 --top-n 2
```

### 사용자 실행 예시

대화형:

```bash
python3 main.py
```

예시 질문:

- `2차전지 시장 전망 요약`
- `에코프로 분석해줘`
- `2차전지 주도주 찾아줘`
- `에코프로와 에코프로비엠 비교해줘`

비대화형:

```bash
python3 main.py --stock 에코프로
python3 main.py --quick 086520
python3 main.py --theme 2차전지 --candidate-limit 5 --top-n 3
```

## 9. 외부 의존성

| 외부 요소 | 용도 | 필수 여부 |
|---|---|---|
| Ollama | 기본 LLM 런타임 | 사실상 필수 |
| Gemini API | Ollama 대체 LLM provider | 선택 |
| DART API | 새 공시 수집 | 선택 |
| Tavily API | 웹 검색 품질 개선 | 선택 |
| DuckDuckGo | 웹 검색 fallback | 선택 |
| Redis | 진행 상태 pub/sub, 결과 캐시 | 선택 |
| KIS API | 실시간 시세/주문 | 선택 |
| Playwright | 포럼/브라우저 기반 수집 | 선택 |
| Selenium | 수집 런타임 | 선택 |
| Chroma | 일부 레거시 RAG 경로 | 선택/레거시 |

## 10. 현재 상태 진단

### 실제 실행 근거가 있는 것

- `scripts/healthcheck.py` 동작 확인
- `scripts/verify_data_connection.py` 동작 확인
- `scripts/run_agent_demo.py`로 retrieval 기반 응답 생성 확인
- `main.py --theme 2차전지` 실행 확인
- 대화형 `2차전지 주도주 찾아줘` 라우팅 확인
- `ai_server/app.py` 기반 `/health`, `/chat`, `/analyze` 경로 검증 이력 존재

### 현재 미완성 또는 주의가 필요한 것

- RAG 계층이 `src/rag`와 `src/retrieval`로 나뉘어 있어 구조적으로 겹침
- `src/rag/retriever.py` 기반 레거시 Chroma 경로가 완전히 제거된 상태는 아님
- Chartist는 데이터 길이가 부족하면 중립/관망으로 폴백
- 현재 실데이터 검증은 `2차전지` 중심
- Docker/Compose 경로는 존재하지만 로컬 CLI 경로만큼 충분히 검증되었다고 보기는 어려움
- Docker는 Python 3.11, 로컬 검증은 3.12로 버전 차이 존재

### README와 코드 사이에서 문서화가 필요한 불일치

- 로컬 검증 기준 Python과 Dockerfile Python 버전이 다름
- `docker-compose.yml`에는 `api(8000)`와 `ai(8001)` 두 서비스가 정의돼 있지만, 현재 주요 검증 경로는 `ai_server`와 CLI 중심
- RAG는 주 경로가 canonical retriever이지만 레거시/폴백 계층이 함께 존재

## 11. 협업 시 필요한 정보

### 백엔드가 알아야 할 것

- 이 브랜치는 독립 실행형 AI 분석 모듈로 볼 수 있음
- CLI 또는 HTTP API로 호출 가능
- 핵심 API 진입점은 `ai_server/app.py`

### 프론트엔드가 알아야 할 것

- UI는 이 브랜치가 제공하지 않음
- 이 브랜치는 텍스트/JSON 응답을 반환하는 분석 엔진 역할
- 프론트엔드는 결과를 렌더링하는 소비자

### 입력 형식

- 자연어 질문
- 종목명 또는 종목코드
- 테마명

### 출력 형식

- 일반 답변 텍스트
- 단일 종목 분석 결과
- 테마 주도주 순위
- JSON 구조 결과

### API 경로

- `GET /health`
- `POST /chat`
- `POST /analyze`
- `GET /analyze/{task_id}`
- `POST /theme/analyze`
- `GET /theme/analyze/{task_id}`
- `POST /suggest`

### 연결 시 주의사항

- `.env`와 Ollama 준비가 선행돼야 함
- `data/`가 실제로 존재해야 함
- 테마 데이터 품질에 따라 결과가 크게 달라질 수 있음

## 12. 백엔드 연동 인터페이스

이 브랜치를 백엔드에서 붙일 때는 `ai_server/app.py`를 기준으로 보면 됩니다.  
기본 포트는 `8001`이며, `docker-compose.yml`에서는 `ai` 서비스가 이 서버를 담당합니다.

### 12.1 서버 기본 정보

- 기본 서버: `uvicorn ai_server.app:app --host 0.0.0.0 --port 8001`
- 기본 포트: `8001`
- 기본 응답 형식: JSON
- 비동기 분석 경로: `POST /analyze` → `GET /analyze/{task_id}`

### 12.2 `GET /health`

서버와 기본 런타임 상태를 확인합니다.

요청:

```http
GET /health
```

응답 예시:

```json
{
  "status": "ok",
  "service": "HQA AI Server",
  "port": 8001,
  "data_dir": "/app/data",
  "data_dir_exists": true,
  "env_loaded": true,
  "env_file": "/app/.env",
  "env_message": ".env 파일을 로드했습니다."
}
```

설명:

- `data_dir_exists=false`면 데이터 마운트 또는 경로 설정 문제 가능성이 큼
- `env_loaded=false`면 `.env` 또는 `.env-ai` 누락 가능성이 있음

### 12.3 `POST /chat`

사용자 자연어 질의를 바로 보내고, `SupervisorAgent`가 적절한 흐름으로 처리한 결과를 간략 응답으로 받습니다.

요청 바디:

```json
{
  "message": "2차전지 주도주 찾아줘",
  "session_id": "optional-session-id"
}
```

응답 예시:

```json
{
  "message": "1. 에코프로(086520) - leader_score 65, 보유/관망, 확신도 60%",
  "intent": null,
  "stocks": []
}
```

필드 설명:

- `message`: 사용자에게 바로 보여줄 수 있는 요약 응답
- `intent`: 현재 구현상 항상 풍부하게 채워진다고 보장할 수 없음
- `stocks`: 종목 추출 결과가 있으면 들어가지만, 테마 질문은 빈 배열일 수 있음

주의:

- `POST /chat`은 내부적으로 다양한 실행 흐름을 탈 수 있어서, 응답은 요약 텍스트 중심입니다.
- 백엔드가 구조화된 분석 결과를 안정적으로 원하면 `/analyze` 또는 별도 전용 API 확장을 고려하는 것이 좋습니다.

### 12.4 `POST /analyze`

단일 종목 분석을 비동기로 시작합니다.  
즉시 `task_id`와 `pending` 상태를 반환하고, 실제 결과는 `GET /analyze/{task_id}`로 조회합니다.

요청 바디:

```json
{
  "task_id": "demo-quick-086520",
  "stock_name": "에코프로",
  "stock_code": "086520",
  "mode": "quick",
  "max_retries": 1
}
```

필드 설명:

- `task_id`: 클라이언트가 생성해서 보내는 추적용 ID
- `stock_name`: 종목명
- `stock_code`: 6자리 종목코드
- `mode`: `"quick"` 또는 `"full"`
- `max_retries`: full 분석 재시도 횟수

즉시 응답 예시:

```json
{
  "task_id": "demo-quick-086520",
  "status": "pending"
}
```

`mode` 설명:

- `quick`
  - `Quant + Chartist` 중심 빠른 분석
- `full`
  - `Analyst + Quant + Chartist + RiskManager` 전체 분석

### 12.5 `GET /analyze/{task_id}`

비동기 분석 결과를 조회합니다.

요청:

```http
GET /analyze/demo-quick-086520
```

빠른 분석 응답 예시:

```json
{
  "task_id": "demo-quick-086520",
  "mode": "quick",
  "stock": {
    "name": "에코프로",
    "code": "086520"
  },
  "scores": {
    "quant": {
      "total_score": 15,
      "grade": "F",
      "opinion": "..."
    },
    "chartist": {
      "total_score": 50,
      "signal": "중립"
    }
  },
  "completed_at": "2026-04-07T12:00:00",
  "status": "completed"
}
```

전체 분석 응답 예시:

```json
{
  "task_id": "demo-full-086520",
  "mode": "full",
  "stock": {
    "name": "에코프로",
    "code": "086520"
  },
  "scores": {
    "analyst": {
      "total_score": 42,
      "hegemony_grade": "B"
    },
    "quant": {
      "total_score": 15,
      "grade": "F"
    },
    "chartist": {
      "total_score": 50,
      "signal": "중립"
    }
  },
  "final_decision": {
    "action": "보유/관망",
    "confidence": 60,
    "risk_level": "보통",
    "total_score": 50,
    "summary": "..."
  },
  "research_quality": null,
  "quality_warnings": [],
  "completed_at": "2026-04-07T12:00:00",
  "status": "completed"
}
```

실패 예시:

```json
{
  "task_id": "demo-full-086520",
  "status": "failed",
  "error": "..."
}
```

404 예시:

```json
{
  "detail": "작업을 찾을 수 없습니다: demo-full-086520"
}
```

### 12.6 `POST /theme/analyze`

테마 전체를 스캔해서 후보 종목을 자동 추출하고, 멀티 에이전트 평가 후 주도주를 선별합니다.

요청 바디:

```json
{
  "task_id": "theme-2nd-battery-001",
  "theme": "2차전지",
  "theme_key": "",
  "candidate_limit": 5,
  "top_n": 3
}
```

필드 설명:

- `task_id`: 클라이언트 추적용 ID
- `theme`: 사용자용 테마명
- `theme_key`: 선택값. 비우면 내부에서 정규화
- `candidate_limit`: 평가할 후보 수
- `top_n`: 최종 반환할 주도주 수

즉시 응답 예시:

```json
{
  "task_id": "theme-2nd-battery-001",
  "status": "pending",
  "mode": "theme"
}
```

### 12.7 `GET /theme/analyze/{task_id}`

테마 주도주 선별 결과를 조회합니다. 내부 저장소는 `GET /analyze/{task_id}`와 공유합니다.

응답 예시:

```json
{
  "task_id": "theme-2nd-battery-001",
  "mode": "theme",
  "theme": "2차전지",
  "theme_key": "2차전지",
  "candidate_limit": 5,
  "top_n": 3,
  "candidate_count": 5,
  "evaluated_count": 5,
  "leaders": [
    {
      "stock_name": "에코프로",
      "stock_code": "086520",
      "leader_score": 65,
      "seed_score": 100,
      "action": "보유/관망",
      "confidence": 60,
      "summary": "...",
      "risk_level": "보통",
      "key_catalysts": ["..."],
      "risk_factors": ["..."]
    }
  ],
  "summary": "1. 에코프로(086520) - leader_score 65, 보유/관망, 확신도 60%",
  "completed_at": "2026-04-07T12:00:00",
  "status": "completed"
}
```

실패 예시:

```json
{
  "task_id": "theme-2nd-battery-001",
  "mode": "theme",
  "theme": "2차전지",
  "theme_key": "",
  "status": "failed",
  "error": "테마 분석 실패",
  "completed_at": "2026-04-07T12:00:00"
}
```

### 12.8 `POST /suggest`

사용자 질문이 현재 시스템 범위 안에서 답변 가능한지 판단하고, 필요하면 교정된 질문이나 대안 질문을 제안합니다.

요청 바디:

```json
{
  "query": "2차전지 관련해서 뭐 물어볼 수 있어?"
}
```

응답 예시:

```json
{
  "original_query": "2차전지 관련해서 뭐 물어볼 수 있어?",
  "is_answerable": true,
  "corrected_query": null,
  "suggestions": [
    "2차전지 주도주 찾아줘",
    "2차전지 산업 전망 요약해줘",
    "에코프로 분석해줘"
  ],
  "reason": "현재 기능 범위 안의 질문으로 변환 가능"
}
```

### 12.9 Redis 진행 상태 이벤트

`/analyze`는 선택적으로 Redis pub/sub을 사용할 수 있습니다.

- 채널: `hqa:progress:{task_id}`
- 결과 키: `hqa:result:{task_id}`

진행 이벤트 예시:

```json
{
  "task_id": "demo-full-086520",
  "agent": "quant",
  "status": "started",
  "message": "재무 분석 중...",
  "progress": 0.1,
  "timestamp": "2026-04-07T12:00:00"
}
```

설명:

- Redis가 없으면 서버는 인메모리 결과 저장으로 폴백합니다.
- 따라서 Redis는 필수는 아니지만, 백엔드에서 실시간 진행 상태를 받고 싶다면 유용합니다.

### 12.10 백엔드 연동 권장 방식

권장 패턴은 아래와 같습니다.

1. 단순 챗 응답이 필요하면 `POST /chat`
2. 구조화된 종목 분석이 필요하면 `POST /analyze`
3. 구조화된 테마 주도주 선별이 필요하면 `POST /theme/analyze`
4. 결과 polling은 `GET /analyze/{task_id}` 또는 `GET /theme/analyze/{task_id}`
5. 질문 입력 가이드는 `POST /suggest`

현재 주의할 점:

- `POST /chat`은 요약 텍스트 중심 응답이고, 구조화된 결과 보장은 약합니다.
- 테마 주도주 데이터가 필요하면 `POST /theme/analyze` 경로를 사용하는 것이 가장 안정적입니다.

## 13. README 추천 목차

이 문서는 아래 순서를 기준으로 유지하는 것이 좋습니다.

1. 브랜치 소개
2. 브랜치 존재 이유
3. 책임 범위
4. 백엔드/프론트엔드와의 경계
5. 프로젝트 구조
6. 데이터 구조
7. 에이전트 동작 방식
8. 설치 방법
9. 환경변수
10. 실행 방법
11. 사용 예시
12. API/협업 포인트
13. 현재 상태 진단
14. 한계 / TODO

## 14. 반드시 문서화해야 할 핵심 포인트

- 수집 파이프라인은 유지 대상이라는 점
- canonical index 우선, BM25/vector fallback이라는 retrieval 규칙
- 이 브랜치는 UI가 아니라 AI 실행/검색 브랜치라는 점
- `main.py`와 `ai_server/app.py`가 주요 진입점이라는 점
- `.env` 최소값과 Ollama 준비 방법
- raw 데이터와 retrieval 자산은 구분해서 봐야 한다는 점
- 테마 주도주 선별은 사용자가 종목을 직접 지정하지 않아도 자동 후보 추출로 동작한다는 점
- 현재 확실히 검증된 실행 경로와 아직 불안정한 영역을 구분해서 봐야 한다는 점
- RAG 계층 중복은 현재 허용된 상태지만 장기적으로 정리 대상이라는 점

## 15. 빠른 시작

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
ollama serve
python3 scripts/healthcheck.py
python3 scripts/verify_data_connection.py --data-dir ./data
python3 scripts/run_theme_orchestrator.py --theme 2차전지 --data-dir ./data --candidate-limit 3 --top-n 2
```

## 16. 참고

이 브랜치는 “데이터가 존재하는 상태”와 “에이전트가 그 데이터를 실제로 활용하는 상태”를 연결하는 브랜치입니다.  
문서화와 협업의 핵심은 파일 목록보다 실행 경로, 데이터 흐름, 책임 경계, 검증 상태를 명확히 공유하는 데 있습니다.
