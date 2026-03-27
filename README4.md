# HQA — 코드 아키텍처 및 디렉토리 구조 상세 가이드 (`README4.md`)

이 문서는 HQA 프로젝트(`ai-main` 브랜치)의 전체 소스 코드 구조를 파악하고, 각 폴더와 개별 파이썬 파일이 어떤 역할을 수행하는지 상세히 설명하는 개발자 및 기여자용 가이드입니다.

---

## 🏗️ 1. 전체 디렉토리 트리

```text
HQA_Project/
├── main.py                         # 앱 진입점 (CLI 및 자율 실행)
├── .env.example                    # 환경 변수 설정 템플릿
├── config/                         # 자율 에이전트 설정 파일
├── prompts/                        # LLM 프롬프트 템플릿 파일
├── data/                           # 런타임 데이터 (트레이스, 주문 기록)
├── tests/                          # 단위 테스트
├── ai_server/                      # AI 전용 FastAPI 마이크로서비스
└── src/                            # 핵심 비즈니스 로직 (Core)
    ├── agents/                     # 멀티 에이전트 및 LangGraph 워크플로우
    ├── runner/                     # 자율 매매 실행기
    ├── rag/                        # 검색 증강 생성(RAG) 파이프라인
    ├── tools/                      # 외부 API 연동 도구 (시세, 검색 등)
    ├── tracing/                    # 에이전트 실행 기록(옵저버빌리티)
    ├── database/                   # DB 연동 헬퍼
    └── utils/                      # 공통 유틸리티 (메모리, 병렬처리 등)
```

---

## 📂 2. 주요 폴더 및 파일 상세 설명

### 1️⃣ 루트 디렉토리 (`/`)

*   **`main.py`**: HQA 시스템의 메인 실행 파일입니다.
    *   **역할**: 커맨드라인 인자(CLI)를 파싱하여 알맞은 모드(대화형, 단일 종목 분석, 빠른 분석, 자율 에이전트 모드 등)로 프로그램을 실행합니다.
*   **`.env.example`**: 프로젝트 구동에 필요한 환경 변수(API 키, 데이터베이스 주소 등)의 목록과 형식을 제공하는 샘플 파일입니다.

### 2️⃣ 설정 및 프롬프트 (`config/`, `prompts/`)

*   **`config/watchlist.yaml`**: 자율 에이전트(`AutonomousRunner`)가 주기적으로 감시하고 분석할 종목 리스트와 매매 조건(서킷 브레이커, 손절 기준 등)을 정의하는 파일입니다.
*   **`prompts/`**: 각 에이전트가 사용하는 LLM 지시문(프롬프트)을 Markdown 파일로 관리하는 폴더입니다. 소스 코드를 수정하지 않고 프롬프트 엔지니어링을 할 수 있게 해줍니다.
    *   `supervisor/routing.md`: 사용자 질문의 의도를 파악하는 프롬프트.
    *   `researcher/summary.md`: 수집된 데이터를 요약하는 프롬프트.
    *   `strategist/hegemony.md`: 회사의 독점력과 성장성을 분석하는 프롬프트.
    *   `quant/web_fallback.md`: 재무 데이터 스크래핑 실패 시 웹 검색 결과에서 재무 지표를 추출하는 프롬프트.
    *   `chartist/analysis.md`: 기술적 지표를 바탕으로 차트를 분석하는 프롬프트 (CrewAI 기반).
    *   `risk_manager/decision.md`: 모든 에이전트의 점수를 종합하여 최종 투자 결정을 내리는 프롬프트.

### 3️⃣ 핵심 비즈니스 로직 (`src/`)

이 폴더는 HQA 코어 엔진의 심장부입니다.

#### 🤖 `src/agents/` — AI 에이전트 및 워크플로우
개별 AI 전문가 객체들과 이들의 실행 순서를 정의합니다.

*   **`graph.py`**: **가장 중요한 파일 중 하나입니다.** LangGraph를 사용하여 여러 에이전트(Analyst, Quant, Chartist, Risk Manager)가 병렬로 실행되고, Quality Gate를 거쳐 최종 결론에 도달하는 **상태 머신(Workflow)**을 정의합니다.
*   **`llm_config.py`**: Gemini, Ollama 등 다양한 LLM 제공자를 설정하고 초기화하는 팩토리 함수들을 모아둔 파일입니다. Instruct(빠른) 모델과 Thinking(추론용) 모델을 구분하여 제공합니다.
*   **`supervisor.py`**: 사용자의 입력을 받아 가장 먼저 처리하는 에이전트입니다. 대화 이력을 기억(Memory)하고, 질문의 의도(Intent)를 분석하여 다른 전문가 에이전트에게 업무를 할당(Routing)합니다.
*   **`analyst.py`**: `Researcher`와 `Strategist`를 하나로 묶어 동작하게 하는 통합 관리자(Wrapper) 클래스입니다.
*   **`researcher.py`**: RAG 시스템이나 웹 검색을 통해 시장 데이터, 뉴스, 리포트 등 정성적 정보를 수집하고 요약합니다. 데이터 수집 실패 시 Plan B로 우회(Fallback)하는 로직이 핵심입니다.
*   **`strategist.py`**: `Researcher`가 모은 정보를 바탕으로 기업의 '경제적 해자(Moat)'와 '성장성'을 평가하여 점수(70점 만점)를 매깁니다.
*   **`quant.py`**: 네이버 금융 등에서 재무제표 수치를 가져와 PER, PBR, ROE 등을 계산하고 가치, 수익성, 성장성, 안정성을 평가(100점 만점)합니다.
*   **`chartist.py`**: 이동평균선, RSI, MACD 등의 기술적 지표를 분석하여 매매 시점과 추세를 판단(100점 만점)합니다.
*   **`risk_manager.py`**: 앞선 Analyst, Quant, Chartist의 총 270점 만점 평가를 종합하여 리스크를 평가하고, 최종적인 투자 의견(적극 매수, 보유, 매도 등)과 포지션 크기(비중)를 결정합니다.

#### ⚙️ `src/runner/` — 자율 에이전트 시스템
사람의 개입 없이 설정된 종목을 자동으로 분석하고 매매하는 모듈입니다.

*   **`autonomous_runner.py`**: 무한 루프(Loop)를 돌며 `config/watchlist.yaml`에 정의된 종목들을 스케줄(예: 60분 간격, 장 중에만)에 따라 순차적으로 LangGraph에 넣고 분석 돌리는 **메인 엔진**입니다.
*   **`trade_executor.py`**: `Risk Manager`가 내린 `FinalDecision`을 바탕으로 실제 증권사(KIS) API를 통해 주문을 넣는 클래스입니다. 일일 매수 한도 초과 방지, 종목 쿨다운, 모의투자(Dry Run) 지원 등 **서킷 브레이커(안전장치)** 역할을 합니다.

#### 🔍 `src/rag/` — 리서치 및 검색 증강 파이프라인
외부 문서를 읽고 벡터 스토어에 저장한 뒤, 스마트하게 검색해내는 시스템입니다.

*   **`retriever.py`**: 벡터 검색(의미 검색)과 BM25(키워드 검색) 결과를 합친 뒤(RRF 방식), Reranker를 사용해 가장 정확한 문서를 최상단으로 끌어올리는 통합 검색 엔진입니다 (Hybrid Search).
*   **`bm25_index.py`**: 금융 단어(예: PER, EBITDA)나 숫자(예: 12.5배)가 잘리지 않도록 보호하는 특수 토크나이저를 사용한 키워드 검색 인덱스입니다.
*   **`vector_store.py`**: 문서를 벡터(숫자 배열)로 변환해 저장하는 ChromaDB 연동 래퍼입니다.
*   **`embeddings.py`, `reranker.py`, `ocr_processor.py`**: 각각 문서 임베딩(Snowflake Arctic), 순위 재조정(Qwen3), PDF 이미지/표 인식(PaddleOCR)을 담당하는 처리 모듈입니다. `*_provider.py`는 로컬 모델 구동 또는 API 사용(Cohere, Upstage 등)을 전환해주는 어댑터입니다.
*   **`document_loader.py`, `text_splitter.py`**: 원본 문서(PDF 등)를 텍스트로 읽어오고 통째로 넣기엔 너무 크므로 LLM이 소화하기 좋게 알맞은 크기(청크)로 자르는 역할을 합니다.

#### 🛠️ `src/tools/` — 외부 연동 도구 (Tools)
에이전트들이 정보를 얻기 위해 호출하는 외부 API 함수들입니다.

*   **`realtime_tool.py`**: 한국투자증권(KIS) API를 호출하여 실시간 주가, 호가창을 가져옵니다.
*   **`finance_tool.py`**: 네이버 금융 페이지를 스크래핑하여 주요 재무제표 값을 긁어오며, 종목 이름 ↔ 6자리 코드 간의 변환 기능을 제공합니다.
*   **`web_search_tool.py`**: 구글링과 유사하게 Tavily API나 DuckDuckGo를 이용하여 최신 뉴스를 검색합니다.
*   **`rag_tool.py`**: `src/rag/retriever.py`를 에이전트가 도구의 형태로 호출할 수 있게 감싸놓은 래퍼입니다.
*   **`charts_tools.py`**: `chartist.py`가 사용하는 RSI, 볼린저밴드 등 특정 수식을 계산하는 단위 도구들입니다.

#### 🔬 `src/tracing/` — 옵저버빌리티
AI 에이전트의 사고 과정을 추적합니다.

*   **`agent_tracer.py`**: 에이전트가 **"왜 그런 판단을 했는지(Reasoning), 시간은 얼마나 걸렸는지, 에러가 나진 않았는지"**를 JSON 파일로 예쁘게 기록(Logging)합니다. 컨텍스트 매니저(`with` 구문)를 사용해 기존 분석 코드에 끼치는 영향을 최소화했습니다.

#### 🧰 `src/utils/` — 유틸리티
*   **`prompt_loader.py`**: `prompts/` 폴더에 있는 Markdown 파일들을 읽어와 파이썬 코드 변수(`{stock_name}` 등)를 주입해주는 로더입니다.
*   **`parallel.py`**: 파이썬의 `ThreadPoolExecutor`를 사용하여 Analyst, Quant, Chartist가 기다리지 않고 동시에 일할 수 있게 해주는 병렬 처리 헬퍼입니다.
*   **`memory.py`**: 사용자와의 이전 대화(최대 10턴)를 기억하여 문맥이 이어지는 대화를 가능하게 합니다.

### 4️⃣ `ai_server/`
AI 전용 웹 애플리케이션(API 서버)입니다. 사용자가 브라우저나 앱에서 AI 분석 결과를 요청할 때 응답해줍니다.

*   **`app.py`**: FastAPI 프레임워크를 사용해 분석 요청(`/analyze`), 대화 요청(`/chat`)을 받아 `src.agents`의 기능들을 비동기로 실행하고 결과를 JSON 형태로 반환해주는 엔드포인트입니다.

### 5️⃣ 생성되는 데이터 (`data/`)
*   **`traces/`**: `agent_tracer.py`가 기록한 에이전트별 분석 과정이 날짜 + 종목코드 하위에 `.json` 파일로 쌓이는 곳입니다.
*   **`orders/`**: `trade_executor.py`가 실행한 매수/매도 내역이 로깅되는 곳입니다. (시뮬레이션 포함)

---

## 🎯 요약: 데이터 흐름으로 보는 코드 연계

1.  **시작**: `main.py --auto` 실행.
2.  **스케줄링**: `src/runner/autonomous_runner.py`가 `config/watchlist.yaml`을 읽고 첫 번째 종목(예: 삼성전자)을 가져옵니다.
3.  **워크플로우**: `src/agents/graph.py` 호출. 3개 파트(Analyst, Quant, Chartist)로 작업 지시.
4.  **정보 수집/분석**:
    *   **Analyst**는 `src/rag/retriever.py`와 `src/tools/web_search_tool.py`를 써서 뉴스를 읽고, `src/utils/prompt_loader.py`로 가져온 프롬프트로 평가합니다.
    *   **Quant**는 `src/tools/finance_tool.py`로 재무를 봅니다.
    *   **Chartist**는 `src/tools/realtime_tool.py`와 `src/tools/charts_tools.py`로 차트를 살핍니다.
5.  **기록**: 이 모든 과정은 `src/tracing/agent_tracer.py`에 의해 `data/traces/`에 꼼꼼히 기록됩니다.
6.  **최종 판단**: 각자의 결과(Score)가 `src/agents/risk_manager.py`로 모이고 종합 점수 산출.
7.  **매매 실행**: 그 결과가 다시 `autonomous_runner`로 반환되고, 점수가 높으면 `src/runner/trade_executor.py`가 `data/orders/`에 매수 기록을 남깁니다(설정에 따라 실제 주문도 가능).
