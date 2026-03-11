# HQA (Hegemony Quantitative Analyst)

**AI 기반 멀티 에이전트 주식 분석 시스템 & RAG 파이프라인**

## 🚀 기획 방향 및 아키텍처

HQA는 단순히 주가를 보여주는 것을 넘어, 실시간 뉴스와 DART 공시, 네이버 종목토론방 데이터를 수집하여 3-Tier RAG 아키텍처를 기반으로 깊이 있는 분석과 답변을 제공하는 프로덕션 레벨의 멀티 에이전트 시스템입니다.

### 1. 기술 스택 (Tech Stack)
- **Language**: Python 3.10+
- **API Framework**: FastAPI (비동기 REST API 지원)
- **AI/Agent Engine**: LangChain, LangGraph (다중 에이전트 워크플로우 제어)
- **LLMOps**: LangSmith (에이전트 추적 및 성능 통제)
- **Database**:
  - **Vector DB**: ChromaDB (RAG 검색)
  - **RDBMS**: PostgreSQL (URL 수집 상태, 사용자 트래킹)
  - **Cache/Queue**: Redis (비동기 Message Queue 및 캐시)
- **Crawling/Async**: `aiohttp`, `asyncio`

### 2. 3-Tier RAG 데이터 수집 및 저장 아키텍처
RAG 기반의 검색 안정성과 확장성을 위해 목적에 따라 저장소를 세분화합니다.

* **Tier 1. 원천 데이터 (Data Lake - S3/File)**
  - 크롤링한 원본을 날짜별 파티셔닝 구조로 `JSONL` 형태로 다이렉트 저장.
  - 임베딩 모델 변경 혹은 Chunking 전략 수정 시 원본 복구를 위한 최후의 보루.

* **Tier 2. RAG 서빙용 (ChromaDB)**
  - Tier 1의 데이터를 Chunking 및 Embedding 하여 인덱싱.
  - LangChain 기반 하이브리드 검색의 실질적 뇌 역할 수행.
  - `content_hash` 메타데이터를 필수 적용하여 내용 중복 임베딩 원천 차단.

* **Tier 3. 파이프라인 상태 관리 (PostgreSQL + SQLAlchemy)**
  - URL 수집 성공/실패, 재시도 횟수 등 크롤러의 가벼운 메타데이터 관리를 수행.
  - Fail-safe 매커니즘을 통하여 크롤러 재시작 시 데이터 무결성 보장.

### 3. 완전 비동기 데이터 파이프라인 흐름
크롤링(I/O 바운드)과 VectorDB 임베딩(CPU/GPU 바운드) 사이의 완전한 격리를 구현했습니다.
1. **Crawling (`aiohttp`)**: 크롤러가 뉴스/공시 데이터를 가져와 Tier 1(Data Lake)에 파일로 저장합니다.
2. **Publish (`Redis`)**: 크롤러는 파일이 저장된 경로(URI)만 Redis Message Queue에 `Publish` 합니다.
3. **Mule/Worker**: 백그라운드 Worker 프로세스는 Redis Queue를 Listening(구독)하다가 이벤트를 받으면 파일을 읽습니다.
4. **Embedding (`LangChain`)**: 데이터를 알맞은 크기로 Chunking하고 모델을 통해 벡터화하여 ChromaDB에 Insert 합니다.

## 🛠️ 개발 시작하기

**1. 환경 변수 설정**
`.env.example` 파일을 복사하여 `.env` 파일을 만들고 키를 채워 넣으세요.
```bash
cp .env.example .env
```
필수 키: `POSTGRES_DB`, `REDIS_URL`, `OPENAI_API_KEY`, API 키(DART/NAVER)

**2. 서비스 구동 (FastAPI)**
```bash
uvicorn main:app --reload
```
API Docs는 `http://localhost:8000/docs` 에서 확인 가능합니다.

**3. 데이터 파이프라인 (Tier 1 크롤러) 단독 실행 테스트**
```bash
python -m src.data_pipeline.orchestration.pipeline
```
(사전에 `redis-server`가 실행 중이어야 합니다.)
