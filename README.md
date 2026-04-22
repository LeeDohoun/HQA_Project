# HQA Project

HQA(Hegemony Quantitative Analyst)는 한국 주식 분석을 위한 데이터 수집, RAG 검색, 멀티 에이전트 분석, 테마 주도주 선별, 자율 감시/매매 시뮬레이션을 하나로 묶은 AI 분석 런타임입니다.

현재 프로젝트의 중심은 프론트엔드 UI가 아니라 다음 실행 계층입니다.

- `main.py`: 로컬 CLI 진입점
- `ai_server/app.py`: FastAPI 기반 AI 분석 서버
- `scripts/`: 데이터 검증, RAG 빌드, 수집/분석 파이프라인 실행 도구
- `src/agents/`: Analyst, Quant, Chartist, RiskManager, Theme Orchestrator
- `src/rag/`, `src/retrieval/`: canonical index 우선 RAG와 fallback retrieval
- `src/runner/`: 감시 목록 기반 자율 분석과 매매 실행/시뮬레이션

> 이 프로젝트는 투자 분석 보조 도구입니다. 분석 결과는 투자 권유나 수익 보장을 의미하지 않으며, 실제 매매 기능은 반드시 `dry_run`과 리스크 제한 설정을 확인한 뒤 사용해야 합니다.

## 현재 상태

- 기준 브랜치: `new-ai-data-main`
- 로컬 데이터 자산: `data/` 아래에 `2차전지` 테마 샘플/검증 데이터 포함
- 기본 LLM provider: Ollama
- smoke test용 provider: `LLM_PROVIDER=mock`
- 로컬 검증 Python: 3.12 계열
- Docker 베이스 이미지: Python 3.11 slim

검증과 예시는 현재 저장소에 포함된 `2차전지` 데이터를 중심으로 작성되어 있습니다. 다른 테마는 수집 또는 RAG 빌드가 먼저 필요할 수 있습니다.

## 빠른 시작

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

브라우저 기반 수집을 사용할 때만 Playwright 브라우저 런타임을 추가합니다.

```bash
playwright install chromium
```

Ollama 기반으로 실제 에이전트를 실행하려면 Ollama 서버와 모델이 필요합니다.

```bash
ollama serve
ollama pull qwen3.5:9b
ollama pull gemma4:e4b
```

외부 LLM 없이 배선만 확인하려면 mock provider를 사용합니다.

```bash
LLM_PROVIDER=mock python3 scripts/healthcheck.py
LLM_PROVIDER=mock python3 scripts/verify_data_connection.py --data-dir ./data --query "2차전지 시장 전망"
```

## 환경 변수

`.env.example`을 복사한 `.env`가 기본 설정 파일입니다. `.env-ai`가 있으면 `.env`보다 먼저 로드됩니다. 이미 셸에 설정된 환경 변수는 파일 값으로 덮어쓰지 않습니다.

| 변수 | 기본값/예시 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `gemini`, `mock` 중 하나 |
| `HQA_DATA_DIR` | `./data` | raw/corpus/index/report/cache 데이터 루트 |
| `HQA_TRACES_DIR` | `./data/traces` | 에이전트 trace 저장 경로 |
| `HQA_ORDERS_DIR` | `./data/orders` | 주문/시뮬레이션 로그 저장 경로 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `OLLAMA_INSTRUCT_MODEL` | `qwen3.5:9b` | Supervisor, Quant, Chartist 등 빠른 분석 모델 |
| `OLLAMA_THINKING_MODEL` | `gemma4:e4b` | RiskManager 등 깊은 판단 모델 |
| `OLLAMA_THINKING_VALIDATOR_MODEL` | 빈 값 | 최종 판단 교차 검증 모델, 비워두면 비활성 |
| `OLLAMA_VISION_MODEL` | `llava:13b` | 이미지/차트 비전 분석용 |
| `GOOGLE_API_KEY` | 빈 값 | `LLM_PROVIDER=gemini`일 때 필요 |
| `DART_API_KEY` | 빈 값 | DART 공시 신규 수집 시 필요 |
| `TAVILY_API_KEY` | 빈 값 | 웹 검색 품질 개선용, 없으면 가능한 경우 DuckDuckGo fallback |
| `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO` | 빈 값 | 실시간 시세/실주문 연동 시 필요 |
| `REDIS_URL` | `redis://localhost:6379/0` | 진행 상태 pub/sub과 결과 캐시, 없으면 인메모리 fallback |
| `ENABLE_OCR`, `OCR_PROVIDER` | `false`, `local` | OCR 경로 설정 |
| `RERANKER_PROVIDER` | `local` | reranker provider 설정 |

## 프로젝트 구조

```text
HQA_Project/
├── main.py                         # CLI 진입점
├── ai_server/
│   ├── app.py                      # FastAPI AI 서버
│   └── Dockerfile
├── config/
│   └── watchlist.yaml              # 자율 분석/매매 설정
├── data/                           # 로컬 데이터 자산
│   ├── raw/
│   ├── corpora/
│   ├── market_data/
│   ├── canonical_index/
│   ├── bm25/
│   ├── vector_stores/
│   ├── reports/
│   ├── cache/
│   ├── traces/
│   └── orders/
├── prompts/                        # 에이전트 프롬프트
├── scripts/                        # 운영/검증/수집 CLI
├── src/
│   ├── agents/                     # 멀티 에이전트와 LangGraph 워크플로우
│   ├── config/                     # 환경/경로 설정
│   ├── data_pipeline/              # 데이터 파이프라인 보존 계층
│   ├── ingestion/                  # 뉴스/공시/포럼/차트 수집
│   ├── rag/                        # canonical RAG 주 경로
│   ├── retrieval/                  # BM25/vector fallback 검색
│   ├── runner/                     # 자율 실행기와 매매 실행기
│   ├── tools/                      # 에이전트 도구 facade
│   ├── tracing/
│   └── utils/
└── tests/
```

## 데이터 흐름

```text
수집(raw)
  -> corpora / market_data
  -> canonical_index 또는 BM25/vector store
  -> RAG 검색
  -> 에이전트 컨텍스트
  -> 분석 응답 / 테마 주도주 선정 / 매매 판단
```

| 데이터 | 위치 | 설명 |
|---|---|---|
| raw 뉴스/공시/포럼/차트 | `data/raw/<source>/*.jsonl` | 수집 원천 데이터 |
| 테마 후보 | `data/raw/theme_targets/*.jsonl` | 테마별 후보 종목 저장소 |
| corpus | `data/corpora/<theme>/` | raw를 검색용 문서 집합으로 정리한 결과 |
| market data | `data/market_data/<theme>/` | Chartist와 price loader 입력 |
| canonical index | `data/canonical_index/<theme>/` | 현재 주 RAG 경로 |
| BM25/vector fallback | `data/bm25/`, `data/vector_stores/` | legacy/pipeline fallback 검색 자산 |
| reports | `data/reports/` | 수집/분석/파이프라인 결과 |
| cache | `data/cache/` | 에이전트와 계산 캐시 |
| traces | `data/traces/` | 디버그 trace 출력 |
| orders | `data/orders/` | 매매 preview/execute 로그 |

검색은 canonical index를 먼저 사용하고, canonical 자산이 없으면 pipeline BM25/vector 자산을 fallback으로 시도합니다. raw 데이터만 있고 retrieval 자산이 없으면 `scripts/build_rag.py`로 빌드해야 합니다.

## CLI 실행

대화형 모드:

```bash
python3 main.py
```

단일 종목 분석:

```bash
python3 main.py --stock 삼성전자
python3 main.py --stock 005930
python3 main.py --quick 005930
```

실시간 시세:

```bash
python3 main.py --price 005930
```

테마 주도주 선별:

```bash
python3 main.py --theme 2차전지 --candidate-limit 5 --top-n 3
```

자율 에이전트:

```bash
python3 main.py --auto
python3 main.py --auto --loop
python3 main.py --auto --dry-run
python3 main.py --auto --config config/watchlist.yaml
```

상세 도움말:

```bash
python3 main.py --help-full
```

## 운영 스크립트

환경과 데이터 상태 점검:

```bash
python3 scripts/healthcheck.py
python3 scripts/verify_data_connection.py --data-dir ./data --query "2차전지 시장 전망"
```

RAG 기반 답변 데모:

```bash
python3 scripts/run_agent_demo.py --query "2차전지 시장 전망 요약" --data-dir ./data
```

테마 주도주 오케스트레이션:

```bash
python3 scripts/run_theme_orchestrator.py --theme 2차전지 --data-dir ./data --candidate-limit 3 --top-n 2
```

raw 데이터를 canonical RAG 자산으로 빌드:

```bash
python3 scripts/build_rag.py --theme-key 2차전지 --data-dir ./data --stats
```

테마 후보 수집, raw 수집, Layer2/RAG 빌드:

```bash
python3 scripts/theme_pipeline.py --theme 2차전지 --data-dir ./data --reuse-saved-targets
```

수집부터 빌드, 감시 목록 분석까지 한 번에 실행:

```bash
python3 scripts/run_pipeline.py --theme 2차전지 --data-dir ./data --full
```

`theme_pipeline.py`와 `run_pipeline.py`에서 DART 수집을 제대로 사용하려면 `DART_API_KEY`와 `corp_codes.csv`의 종목코드-고유번호 매핑이 필요합니다. 매핑이 없으면 DART 수집은 건너뛰거나 일부만 동작할 수 있습니다.

## FastAPI 서버

로컬 실행:

```bash
uvicorn ai_server.app:app --host 0.0.0.0 --port 8001
```

주요 엔드포인트:

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/health` | 서버, 환경 파일, 데이터 디렉터리 상태 |
| `POST` | `/chat` | 자연어 질문을 SupervisorAgent로 처리 |
| `POST` | `/analyze` | 단일 종목 분석 비동기 시작 |
| `GET` | `/analyze/{task_id}` | 단일 종목 분석 결과 조회 |
| `POST` | `/theme/analyze` | 테마 주도주 선별 비동기 시작 |
| `GET` | `/theme/analyze/{task_id}` | 테마 분석 결과 조회 |
| `POST` | `/suggest` | 답변 가능성 판단과 질문 제안 |
| `GET` | `/trading/status` | 감시 목록과 매매 런타임 설정 조회 |
| `POST` | `/trading/decision/preview` | 최종 판단 기반 주문 preview |
| `POST` | `/trading/decision/execute` | 최종 판단 기반 주문 실행 또는 dry-run 로그 |
| `GET` | `/trading/orders` | 주문/시뮬레이션 로그 조회 |

비동기 분석 API는 클라이언트가 `task_id`를 생성해서 보냅니다. Redis가 설정되어 있으면 `hqa:progress:{task_id}` 채널과 `hqa:result:{task_id}` 키를 사용하고, Redis가 없어도 서버 프로세스 안의 인메모리 캐시로 fallback합니다.

예시:

```bash
curl http://localhost:8001/health
```

```bash
curl -X POST http://localhost:8001/theme/analyze \
  -H "Content-Type: application/json" \
  -d '{"task_id":"theme-2nd-battery-001","theme":"2차전지","candidate_limit":5,"top_n":3}'
```

```bash
curl http://localhost:8001/theme/analyze/theme-2nd-battery-001
```

## 자율 분석과 매매 설정

`config/watchlist.yaml`은 자율 에이전트의 감시 목록, 실행 주기, 매매 조건을 정의합니다.

핵심 안전 기본값:

- `trading.enabled: false`: 자동 매매 비활성
- `trading.dry_run: true`: 실제 주문 대신 시뮬레이션 로그만 기록
- 일일 최대 매수 금액, 종목당 최대 비중, 손절 기준, 동일 종목 쿨다운 적용

실제 주문 경로는 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`와 계좌/모의투자 설정이 필요합니다. 실주문을 켜기 전에는 `/trading/decision/preview`와 `--dry-run`으로 결과를 먼저 확인하세요.

## Docker / Compose

이미지 빌드:

```bash
docker compose build
```

전체 서비스 실행:

```bash
docker compose up
```

`docker-compose.yml`의 서비스:

- `api`: `Dockerfile` 기반, `ai_server.app`를 8000 포트로 실행하는 호환 엔트리포인트
- `ai`: `ai_server/Dockerfile` 기반, AI 서버를 8001 포트로 실행
- `redis`: 진행 상태 pub/sub과 결과 캐시
- `postgres`: 향후/선택 DB

주의할 점:

- `.dockerignore`가 `data/`, `tests/`, 문서 파일을 이미지 빌드 컨텍스트에서 제외합니다.
- Compose는 기본적으로 `hqa-data` named volume을 `/app/data`에 붙입니다. 로컬 `./data`가 컨테이너에 자동으로 보이는 구조가 아니므로, 로컬 검증 데이터를 그대로 쓰려면 compose volume을 `./data:/app/data` 형태의 bind mount로 바꾸거나 named volume에 데이터를 별도로 적재해야 합니다.
- Docker 런타임은 Python 3.11이고, 로컬 개발 환경은 3.12에서도 검증되었습니다.

## 테스트

테스트 실행에는 `pytest`가 필요합니다. 환경에 없으면 먼저 설치합니다.

```bash
python3 -m pip install pytest
```

외부 LLM 호출 없이 전체 테스트를 돌릴 때:

```bash
LLM_PROVIDER=mock python3 -m pytest tests/ -q --tb=short
```

일부 통합 테스트는 FastAPI, pandas, LangChain 계열 패키지가 설치된 환경을 전제로 합니다. `requirements.txt` 설치 후 실행하는 것을 권장합니다.

## 주요 구현 메모

- `src/rag/canonical_retriever.py`가 현재 RAG 검색의 중심입니다.
- `src/retrieval/services.py`는 pipeline BM25/vector fallback 계층입니다.
- `src/tools/__init__.py`는 외부로 노출할 tool facade를 제한합니다.
- `src/agents/graph.py`는 LangGraph가 있으면 전체 분석 워크플로우를 사용하고, 없으면 fallback 경로를 사용합니다.
- `src/agents/theme_orchestrator.py`는 테마 후보 추출, 후보별 병렬 평가, 최종 주도주 랭킹을 담당합니다.
- `src/runner/trade_executor.py`는 기본적으로 안전 우선입니다. `enabled=false` 또는 `dry_run=true` 상태에서는 실제 주문을 보내지 않습니다.

## 알려진 한계

- 현재 포함된 실데이터와 검증 경로는 `2차전지` 테마 중심입니다.
- RAG 계층은 canonical 경로와 legacy fallback 경로가 함께 남아 있습니다.
- Chartist는 market data가 부족하면 중립/관망 성격의 fallback 결과를 낼 수 있습니다.
- Docker Compose 구성은 존재하지만, 로컬 CLI와 `ai_server` 직접 실행 경로가 더 많이 검증되어 있습니다.
- `main.py --help-full`의 일부 안내 문구는 과거 Gemini 중심 설정을 언급할 수 있으며, 현재 권장 설정은 이 README와 `.env.example`의 Ollama 기본값입니다.

