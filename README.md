# HQA — Hegemony Quantitative Analyst

> 🤖 **AI 멀티 에이전트 기반 한국 주식 헤게모니 분석 · 자율 투자 시스템**
>
> `ai-main` 브랜치 — AI 에이전트 코어 엔진 + RAG 파이프라인 + 자율 매매 에이전트

---

## 📋 브랜치 개요

`ai-main` 브랜치는 HQA 프로젝트의 **AI 핵심 엔진**을 담당합니다.

- **멀티 에이전트 코어** (LangGraph 상태 머신 기반)
- **RAG 파이프라인** (Hybrid Search: Vector + BM25 + Reranking)
- **AI 서버** (FastAPI — LLM 추론 전용, 포트 8001)
- **자율 에이전트** (설정 기반 자동 분석 + 조건부 매매)
- **CLI 도구** (대화형 분석 + 종목 분석 + 자율 실행)
- **에이전트 트레이싱** (판단 근거·결과·실행 시간 JSON 기록)
- **프롬프트 관리** (에이전트별 프롬프트 외부 파일 분리)

> 📌 데이터 수집 파이프라인(`data_pipeline/`), 프론트엔드(`frontend/`), 대시보드(`dashboard/`) 등은 **별도 브랜치**에서 관리됩니다.

### ✨ 주요 특징

- 🔄 **LangGraph 상태 머신**: Supervisor가 조율하는 병렬 분석 흐름(Analyst/Quant/Chartist + Risk Manager, 품질 게이트 및 피드백 루프)
- 🤖 **자율 에이전트**: YAML 설정 기반 감시 종목 자동 분석 · 장중 스케줄 반복 · 조건부 매매 실행
- 🔍 **Hybrid Search**: BM25 키워드 검색 + Vector 의미 검색 → RRF 병합 → Qwen3 리랭킹
- 📊 **실시간 시세**: 한국투자증권 REST API + WebSocket 실시간 체결가 스트리밍
- 📝 **PaddleOCR-VL-1.5**: 0.9B VLM 기반 문서 OCR (표/차트/수식/도장 인식)
- 🧠 **RAG 기반 분석**: ChromaDB + Snowflake Arctic Korean 임베딩 (1024차원)
- ⚡ **병렬 실행**: ThreadPoolExecutor로 Analyst/Quant/Chartist 동시 실행
- 💾 **대화 메모리**: 10턴 컨텍스트 유지, 후속 질문 자동 감지
- 🛡️ **데이터 품질 관리**: Plan A→B 폴백, 품질 등급(A~D) 기반 행동 강령
- 🔌 **GPU 의존성 제거**: OCR(Upstage API), Reranker(Cohere API) 프로바이더 패턴
- 🔬 **에이전트 트레이싱**: 각 에이전트 판단 근거·결과·실행 시간 JSON 기록 (Context Manager 기반, Thread-safe)
- 📄 **프롬프트 외부화**: 에이전트별 프롬프트를 Markdown 파일로 분리, 코드 수정 없이 튜닝 가능

### 핵심 목표

| 목표 | 설명 |
|------|------|
| 🔍 **헤게모니 기업 발굴** | 산업 지배력, 기술적 해자(Moat), 성장성을 정량/정성 분석 |
| 🤖 **멀티 에이전트 분석** | Supervisor + Analyst + Quant + Chartist + Risk Manager의 상호 검증 · 품질 게이트 · 피드백 루프 |
| 📊 **RAG 기반 실시간 분석** | Hybrid Search (Vector + BM25 + Reranking)로 최신 정보 활용 |
| 💹 **자율 매매 에이전트** | YAML 설정 기반 자동 분석 → 조건 충족 시 KIS API 매매 실행 (3중 서킷 브레이커) |

---

## 🏗️ 시스템 아키텍처

```
┌────────────────────────────────────────────────────────────────────┐
│        🖥️  Entry Points                                            │
│  main.py (CLI) · ai_server/app.py (REST API :8001)                 │
│  main.py --auto (자율 에이전트) · main.py --auto --loop (반복)      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────┐
│        🤖  Layer 4: Autonomous Runner (자율 에이전트)               │
│                                                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐               │
│  │  AutonomousRunner    │  │    TradeExecutor      │               │
│  │  (config/watchlist.  │  │  (매매 실행 + 서킷   │               │
│  │   yaml 기반 자동분석)│→ │   브레이커 3중 안전)  │               │
│  └──────────────────────┘  └──────────────────────┘               │
│       ↕ run_once / run_loop        ↕ dry_run / KIS API             │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────┐
│        🧠  Layer 3: Multi-Agent Core Engine                        │
│                   (LangGraph 상태 머신)                              │
│                                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                           │
│  │ Analyst  │ │  Quant   │ │ Chartist │  ← 병렬 Fan-out           │
│  │(Research │ │(재무분석)│ │(기술분석)│                           │
│  │+Strategy)│ │          │ │          │                           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘                           │
│       └────────────┼────────────┘                                  │
│                    ▼                                                │
│            ┌──────────────┐                                        │
│            │ Quality Gate │ ← 품질 D등급 → 재시도 (피드백 루프)    │
│            └──────┬───────┘                                        │
│                   ▼                                                 │
│            ┌──────────────┐                                        │
│            │ Risk Manager │ → 최종 투자 판단 (270점 만점)          │
│            └──────────────┘                                        │
│                                                                    │
│  ┌─────────────────────┐    ┌─────────────────────┐               │
│  │ 🔬 AgentTracer      │    │ 📄 PromptLoader     │               │
│  │ 판단근거·결과·시간  │    │ prompts/ .md 로드   │               │
│  └─────────────────────┘    └─────────────────────┘               │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────┐
│        📚  Layer 2: RAG & Storage Layer                            │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │ Hybrid Search Pipeline                                   │       │
│  │ Query ─┬─ Vector (Snowflake Arctic, k=20) ─┐            │       │
│  │        └─ BM25 Keyword (k=20) ──────────────┤→ RRF → Rerank    │
│  └─────────────────────────────────────────────────────────┘       │
│                                                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                        │
│  │ ChromaDB │  │BM25 Index│  │PostgreSQL│                        │
│  │ (벡터DB) │  │(키워드)  │  │ (원본DB) │                        │
│  └──────────┘  └──────────┘  └──────────┘                        │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────┐
│        🔌  Layer 1: Data & Integration (Tools)                     │
│                                                                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
│  │ KIS REST API │ │ KIS WebSocket│ │FinanceData   │               │
│  │ (시세/호가)  │ │ (실시간 틱)  │ │Reader(주가)  │               │
│  └──────────────┘ └──────────────┘ └──────────────┘               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
│  │ Tavily 검색  │ │네이버 금융   │ │ RAG 검색     │               │
│  │ (웹/DuckDDG) │ │(재무 크롤링) │ │ (벡터 DB)    │               │
│  └──────────────┘ └──────────────┘ └──────────────┘               │
└────────────────────────────────────────────────────────────────────┘
```

---

## 📁 상세 프로젝트 구조 및 코드 아키텍처

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
*   `quant/web_fallback.md`: 재무 데이터 스크래핑 실패 시 웹 검색 결과에서 재무 지표를 추출하는 프롬프트.
*   `chartist/analysis.md`: 기술적 지표를 바탕으로 차트를 분석하는 프롬프트.
*   `risk_manager/decision.md`: 모든 에이전트의 점수를 종합하여 최종 투자 결정을 내리는 프롬프트.

### 3️⃣ 핵심 비즈니스 로직 (`src/`)

이 폴더는 HQA 코어 엔진의 심장부입니다.

#### 🤖 `src/agents/` — AI 에이전트 및 워크플로우
개별 AI 전문가 객체들과 이들의 실행 순서를 정의합니다.

*   **`graph.py`**: **가장 중요한 파일 중 하나입니다.** LangGraph를 사용하여 여러 분석 단계(Analyst, Quant, Chartist, Risk Manager)가 병렬로 실행되고, Quality Gate를 거쳐 최종 결론에 도달하는 **상태 머신(Workflow)**을 정의합니다.
*   **`llm_config.py`**: Gemini, Ollama 등 다양한 LLM 제공자를 설정하고 초기화하는 팩토리 함수들을 모아둔 파일입니다. Instruct(빠른) 모델과 Thinking(추론용) 모델을 구분하여 제공합니다.
*   **`supervisor.py`**: 사용자의 입력을 받아 가장 먼저 처리하는 에이전트입니다. 대화 이력을 기억(Memory)하고, 질문의 의도(Intent)를 분석하여 다른 전문가 에이전트에게 업무를 할당(Routing)합니다.
*   **`analyst.py`**: 리서치 수집과 헤게모니 판단을 하나로 묶은 통합 분석기입니다. RAG, 웹 검색, Vision 분석 결과를 종합해 Analyst 점수를 산출합니다.
*   **`quant.py`**: 네이버 금융 등에서 재무제표 수치를 가져와 PER, PBR, ROE 등을 계산하고 가치, 수익성, 성장성, 안정성을 평가(100점 만점)합니다.
*   **`chartist.py`**: 이동평균선, RSI, MACD 등의 기술적 지표를 분석하여 매매 시점과 추세를 판단(100점 만점)합니다.
*   **`risk_manager.py`**: 앞선 Analyst, Quant, Chartist의 총 270점 만점 평가를 종합하여 리스크를 평가하고, 최종적인 투자 의견(적극 매수, 보유, 매도 등)과 포지션 크기(비중)를 결정합니다.

#### ⚙️ `src/runner/` — 자율 에이전트 시스템
사람의 개입 없이 설정된 종목을 자동으로 분석하고 매매하는 모듈입니다.

*   **`autonomous_runner.py`**: `config/watchlist.yaml`에 정의된 종목들을 스케줄(예: 60분 간격, 장 중에만)에 따라 순차적으로 LangGraph에 넣고 분석하는 **메인 엔진**입니다.
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
3.  **워크플로우**: `src/agents/graph.py` 호출. Analyst, Quant, Chartist를 병렬 실행하고 Risk Manager가 최종 판단을 내립니다.
4.  **정보 수집/분석**:
    *   **Analyst**는 `src/rag/retriever.py`와 `src/tools/web_search_tool.py`를 써서 뉴스를 읽고, `src/utils/prompt_loader.py`로 가져온 프롬프트로 평가합니다.
    *   **Quant**는 `src/tools/finance_tool.py`로 재무를 봅니다.
    *   **Chartist**는 `src/tools/realtime_tool.py`와 `src/tools/charts_tools.py`로 차트를 살핍니다.
5.  **기록**: 이 모든 과정은 `src/tracing/agent_tracer.py`에 의해 `data/traces/`에 꼼꼼히 기록됩니다.
6.  **최종 판단**: 각자의 결과(Score)가 `src/agents/risk_manager.py`로 모이고 종합 점수 산출.
7.  **매매 실행**: 그 결과가 다시 `autonomous_runner`로 반환되고, 점수가 높으면 `src/runner/trade_executor.py`가 `data/orders/`에 매수 기록을 남깁니다(설정에 따라 실제 주문도 가능).


## 🤖 멀티 에이전트 상세

### 에이전트 구성

| 에이전트 | LLM 모드 | 역할 | 점수 체계 |
|---------|---------|------|----------|
| **Supervisor** | Instruct | 사용자 의도 분석 · 에이전트 라우팅 · 10턴 대화 메모리 | — |
| **Analyst** | Instruct + Thinking | 정보 수집 · 헤게모니 분석 · 품질 평가(A~D등급) | Moat 0~40 + Growth 0~30 = **0~70** |
| **Quant** | Instruct | PER/PBR/ROE 등 재무 지표 분석 · 밸류에이션 | 4영역 각 25 = **0~100** |
| **Chartist** | Instruct | RSI/MACD/볼린저밴드 기술적 분석 | 4영역 합산 = **0~100** |
| **Risk Manager** | Thinking + Validator(선택) | 3개 에이전트 종합 → 최종 투자 판단 · 필요 시 이종 모델 교차 검증 | 총 **270점 만점** |

### 에이전트 프롬프트 관리

각 에이전트의 프롬프트는 **코드 밖의 Markdown 파일**로 분리되어 있어, 코드를 수정하지 않고도 프롬프트를 수정할 수 있습니다.

| 에이전트 | 프롬프트 파일 | 주요 변수 |
|---------|-------------|----------|
| Supervisor | `prompts/supervisor/routing.md` | `{query}`, `{conversation_history}` |
| Quant | `prompts/quant/web_fallback.md` | `{stock_name}`, `{stock_code}`, `{search_results}` |
| Chartist | `prompts/chartist/analysis.md` | `{stock_name}`, `{stock_code}` |
| Risk Manager | `prompts/risk_manager/decision.md` | `{stock_name}`, `{stock_code}` + 점수 변수 |

```python
# 프롬프트 로더 사용법 (코드에서 호출)
from src.utils.prompt_loader import load_prompt

prompt = load_prompt("risk_manager", "decision",
    stock_name="삼성전자", stock_code="005930", ...)
```

### 에이전트 상세 기능

#### Supervisor (조율자)
- **의도 분류**: 종목분석 · 빠른분석 · 산업분석 · 이슈분석 · 시세조회 · 종목비교 · 테마탐색 · 일반QA
- **메모리**: 최근 10턴 컨텍스트 유지, 후속 질문 자동 감지 ("그럼 하이닉스는?")
- **라우팅**: 규칙 기반 빠른 분석 → LLM 상세 분석 (2단계)

#### Analyst (리서치 + 헤게모니 통합)
- **리서치 수집**: RAG 검색, 웹 검색, Vision 분석을 조합해 시장 데이터와 정성 정보를 수집
- **품질 평가**: `ResearchResult.evaluate_quality()` → A/B/C/D 등급 + 경고 목록
- **헤게모니 판단**: 수집된 정보를 바탕으로 독점력(Moat)과 성장성(Growth)을 통합 평가
- **출력**: `AnalystScore` (moat + growth = 0~70점)

#### Quant (퀀트)
- **전략**: Plan A (네이버 금융 크롤링) → Plan B (웹 검색 + LLM JSON 추출)
- **출력**: `QuantScore` (valuation/profitability/growth/stability, 총 0~100점)

#### Chartist (차티스트)
- **지표**: RSI, MACD, Bollinger Band, 이동평균선, 거래량
- **출력**: `ChartistScore` (trend/momentum/volatility/volume, 총 0~100점)

#### Risk Manager (리스크 관리자)
- **입력**: `AgentScores` (Analyst 70 + Quant 100 + Chartist 100 = 270점 만점)
- **출력**: `FinalDecision` (투자 행동, 리스크 레벨, 목표가, 손절가, 포지션 사이징)
- **교차 검증**: `OLLAMA_THINKING_VALIDATOR_MODEL` 또는 `GEMINI_THINKING_VALIDATOR_MODEL` 설정 시 최종 판단만 보조 모델이 재검토

### LangGraph 워크플로우

```
START → [Analyst | Quant | Chartist] (병렬 Fan-out)
    ↓
Quality Gate (Fan-in)
    ├── 품질 D등급 + 재시도 가능 → Retry Research (피드백 루프)
    └── 통과 → Risk Manager → END (투자 지시서 생성)
```

| 기능 | ThreadPoolExecutor (폴백) | LangGraph |
|------|-----------------------------|-----------|
| **실행 방식** | 병렬 + 동기 리턴 | StateGraph Fan-out/Fan-in |
| **에러 복구** | try/except 기본값 | 노드별 독립 에러 처리 |
| **품질 관리** | 없음 | Quality Gate + 재시도 루프 |
| **데이터 흐름** | 함수 리턴값 전달 | `AnalysisState` TypedDict |

---

## 🤖 자율 에이전트 시스템

YAML 설정 파일 기반으로 **사람 개입 없이 자동 분석 + 매매**를 수행하는 자율 에이전트입니다.

### 동작 흐름

```
┌─────────────────┐     ┌──────────────────────┐     ┌────────────────┐
│  config/        │     │  AutonomousRunner     │     │  TradeExecutor │
│  watchlist.yaml │────→│  감시 종목 순회 분석  │────→│  매매 조건 판단│
│                 │     │  (run_once/run_loop)  │     │  + KIS API 주문│
└─────────────────┘     └──────────────────────┘     └────────────────┘
         │                        │                         │
    종목 리스트             LangGraph 분석              서킷 브레이커
    스케줄 설정          FinalDecision 생성           dry_run 시뮬레이션
    매매 규칙              트레이싱 기록               JSONL 주문 기록
```

### 설정 파일 (`config/watchlist.yaml`)

```yaml
# 분석 스케줄
schedule:
  enabled: true
  interval_minutes: 60          # 60분 간격
  market_hours_only: true       # 장중(09:00~15:30)에만 실행

# 감시 종목
watchlist:
  - name: "삼성전자"
    code: "005930"
    mode: "full"                # "full" | "quick"
    priority: 1

# 매매 설정 (⚠️ 기본값은 비활성)
trading:
  enabled: false                # true로 변경 시 실제 매매 실행
  dry_run: true                 # true면 시뮬레이션만

  # 서킷 브레이커
  max_daily_buy_amount: 1000000 # 일일 최대 매수 100만원
  cooldown_minutes: 30          # 동일 종목 30분 쿨다운
  stop_loss_pct: 10             # 손절 10%

  # 자동 매수 조건
  auto_buy_conditions:
    min_total_score: 70         # 종합 점수 70점 이상
    min_confidence: 60          # 확신도 60% 이상
    allowed_actions: ["STRONG_BUY", "BUY"]
    max_risk_level: "MEDIUM"
```

### 매매 안전장치 (서킷 브레이커 3중)

| 안전장치 | 기본값 | 설명 |
|---------|-------|------|
| **일일 최대 매수 금액** | 100만원 | 하루 총 매수 금액 제한 |
| **종목당 쿨다운** | 30분 | 동일 종목 연속 주문 방지 |
| **손절 기준** | 10% | 하드코딩 손절 라인 |
| **dry_run 기본값** | `true` | 실제 주문 없이 로그만 기록 |
| **trading.enabled 기본값** | `false` | 명시적 활성화 필요 |

### 주문 기록

매매 실행 시 `data/orders/{날짜}/orders.jsonl`에 주문 기록이 자동 저장됩니다:

```json
{"timestamp":"2026-03-27T10:00:00","stock_name":"삼성전자","stock_code":"005930",
 "action":"BUY","quantity":2,"price":50000,"amount":100000,
 "decision_score":80,"dry_run":true,"status":"simulated"}
```

---

## 🔬 에이전트 트레이싱 시스템

각 에이전트의 **판단 근거, 분석 결과, 실행 시간**을 구조화하여 JSON 파일로 기록하는 자체 트레이싱 시스템입니다. LangGraph 워크플로우와 Fallback 병렬 경로 모두에서 동작하며, 외부 서비스(LangSmith 등) 없이 독립적으로 운영됩니다.

### 핵심 설계

| 설계 원칙 | 구현 |
|----------|------|
| **Context Manager 패턴** | `with tracer.trace_agent("analyst") as span:` — 기존 코드 최소 침습 |
| **동시성 안전** | `agent_id` UUID + `threading.Lock` — 병렬 에이전트 실행 충돌 방지 |
| **reasoning 요약/원본 분리** | `debug=False`: 요약만 저장, `debug=True`: 원본도 저장 — 토큰 폭발 방지 |
| **이벤트 기반 타임라인** | `TraceEvent` 리스트 — LangSmith 수준의 흐름 추적 |
| **에러/스킵 구조** | `error_type`, `skip_reason`, `retry_from` — 디버깅 핵심 정보 |

### 데이터 구조

```
AnalysisTrace (전체 세션)
├── trace_id: UUID
├── stock_name / stock_code / query
├── workflow_type: "langgraph" | "fallback_parallel"
├── agent_traces: [                    ← 개별 에이전트 기록
│   ├── AgentTrace
│   │   ├── agent_id: UUID             ← 동시성/retry 구분
│   │   ├── agent_name: "analyst"
│   │   ├── duration_seconds: 25.3
│   │   ├── status: "success" | "error" | "skipped"
│   │   ├── output_summary: "A등급 (65/70)"
│   │   ├── reasoning_summary: "반도체 지배력..."  ← 항상 저장
│   │   └── reasoning_raw: Optional     ← debug 모드만
│ ]
├── events: [                          ← 이벤트 타임라인
│   ├── {"type": "trace_started"}
│   ├── {"type": "agent_started", "agent": "analyst"}
│   └── {"type": "trace_completed"}
│ ]
└── final_result_summary: "매수 (230/270) 리스크:medium"
```

### 사용법

```python
from src.tracing import AgentTracer

tracer = AgentTracer(debug=False)
tracer.start_trace("삼성전자", "005930", "langgraph")

with tracer.trace_agent("analyst", "삼성전자(005930)") as span:
    result = run_analyst()
    span.set_output("A등급 (65/70)")
    span.set_reasoning("반도체 시장 지배력 확인됨", raw=full_text)

tracer.finish_trace("매수, 230/270점")
```

---

## 🔍 Hybrid Search (BM25 + Vector)

벡터 검색만으로는 금융 용어(PER, EBITDA, YOY)나 숫자 매칭이 약합니다.
BM25 키워드 검색을 추가하여 **Reciprocal Rank Fusion(RRF)** 으로 병합합니다.

```
Query ──┬── Vector Search (Snowflake Arctic Korean, k=20) ──┐
        │                                                     ├── RRF 병합 ── Qwen3 Rerank ── Top 3
        └── BM25 Keyword Search (금융특화 토크나이저, k=20) ──┘
```

| 검색 방식 | 강점 | 예시 |
|-----------|------|------|
| **Vector** | 유의어·문맥 이해 | "수익성 좋은 기업" ↔ "ROE 높은 종목" |
| **BM25** | 정확한 용어 매칭 | "PER 12.5배", "EBITDA 3조" |
| **Hybrid** | 둘 다 결합 | 의미적 유사도 + 키워드 정확도 |

---

## 🛡️ 리스크 관리 & 안전장치

| 방어 계층 | 메커니즘 | 상세 |
|----------|---------|------|
| **1. AI 판단 검증** | Risk Manager 에이전트 | 팩트 데이터(Quant 점수, Tool 결과)만 기반으로 판단 |
| **2. 서킷 브레이커** | TradeExecutor | 일일 매수 한도, 쿨다운 30분, 손절 10%, dry_run 기본 |
| **3. 에이전트 트레이싱** | AgentTracer | 판단 근거·실행 시간·에러 추적, 사후 검증 가능 |
| **4. 인프라 장애 대응** | Fallback 시스템 | API 타임아웃 → 매수 중단 + 알림, WebSocket 끊김 → REST 폴링 |
| **5. 데이터 품질 관리** | Quality Gate | D등급 → 자동 재시도, Analyst 재수집 유도 |

---

## 🏛️ 설계 원칙 — Graceful Degradation

모든 외부 의존성에 대해 **우아한 성능 저하** 원칙을 적용합니다:

| 상황 | 자동 대응 |
|------|---------| 
| `langgraph` 미설치 | → ThreadPoolExecutor 병렬 폴백 |
| `rank-bm25` 미설치 | → Vector 검색만 사용 |
| GPU 없음 | → Upstage API (OCR) / Cohere API (Reranker) 전환 |
| Tavily API 없음 | → DuckDuckGo 검색 폴백 |
| 네이버 금융 크롤링 실패 | → 웹 검색 + LLM JSON 추출 폴백 |
| 개별 에이전트 오류 | → 기본값 대체 후 계속 진행 |

---

## 🖥️ AI 서버 (`ai_server/`)

LLM 추론과 RAG 파이프라인을 전담하는 **FastAPI 마이크로서비스** (포트 8001)입니다.

### 엔드포인트

| 메서드 | 경로 | 역할 |
|--------|------|------|
| `GET` | `/health` | 헬스체크 |
| `POST` | `/analyze` | 전체/빠른 분석 요청 (비동기, 즉시 `task_id` 반환) |
| `GET` | `/analyze/{task_id}` | 분석 결과 조회 |
| `POST` | `/chat` | 대화형 질문 (SupervisorAgent) |
| `POST` | `/suggest` | 쿼리 제안 (Answerability Check) |

---

## 🚀 실행 방법

### 환경 설정

```bash
cp .env.example .env
pip install langgraph rank-bm25    # LangGraph + BM25 (권장)
```

### CLI 모드

```bash
# 대화형 모드 (Supervisor 기반, 메모리 유지)
python main.py

# 전체 분석 (LangGraph 워크플로우)
python main.py -s 삼성전자
python main.py --stock 005930

# 빠른 분석 (Quant + Chartist만)
python main.py -q 현대차

# 실시간 시세 조회
python main.py -p SK하이닉스
```

### 자율 에이전트 모드

```bash
# 감시 종목 1회 분석
python main.py --auto

# 스케줄 반복 실행 (장중 60분 간격)
python main.py --auto --loop

# 매매 시뮬레이션 (실제 주문 없이 로그만)
python main.py --auto --dry-run

# 커스텀 설정 파일
python main.py --auto --config config/my_watchlist.yaml
```

### AI 서버 모드

```bash
uvicorn ai_server.app:app --reload --host 0.0.0.0 --port 8001
# → API 문서: http://localhost:8001/docs
```

---

## ⚙️ API 설정 상세

### Google Gemini API (필수)
```env
# https://aistudio.google.com/ 에서 발급
GOOGLE_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 한국투자증권 KIS API (선택 — 실시간 시세 + 자동매매)
```env
# https://apiportal.koreainvestment.com/ 에서 발급
KIS_APP_KEY=PSxxxxxxxxxxx
KIS_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_ACCOUNT_NO=12345678-01
```

### Tavily API (선택 — 웹 검색)
```env
# https://tavily.com/ (월 1,000건 무료, 미설정 시 DuckDuckGo 폴백)
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 📚 기술 스택

| 분류 | 기술 | 용도 |
|------|------|------|
| **언어** | Python 3.10+ | 비동기 처리, AI/ML 생태계 |
| **LLM (Instruct)** | Gemini 2.5 Flash Lite | Supervisor, Quant, Chartist |
| **LLM (Thinking)** | Gemini 2.5 Flash Preview | Analyst, Risk Manager |
| **오케스트레이션** | LangGraph (선택) | 상태 머신 워크플로우, 조건부 라우팅 |
| **자율 에이전트** | AutonomousRunner + TradeExecutor | YAML 기반 자동 분석 + 조건부 매매 |
| **병렬 실행** | ThreadPoolExecutor (폴백) | 에이전트 동시 실행 |
| **AI 서버** | FastAPI (포트 8001) | LLM 추론 전용 REST API |
| **문서 OCR** | PaddleOCR-VL-1.5 (0.9B) | PDF → Markdown 텍스트 변환 |
| **임베딩** | Snowflake Arctic Korean | 1024차원, 8192토큰, 한국어 최적화 |
| **벡터 DB** | ChromaDB | 벡터 유사도 검색 |
| **키워드 검색** | rank-bm25 (선택) | BM25 Okapi 금융 특화 키워드 매칭 |
| **리랭커** | Qwen3-Reranker-0.6B | 검색 결과 재순위 |
| **점수 병합** | RRF (Reciprocal Rank Fusion) | Vector + BM25 점수 통합 |
| **웹 검색** | Tavily (1차) / DuckDuckGo (2차) | 실시간 뉴스/정보 폴백 |
| **주식 시세** | 한국투자증권 REST + WebSocket API | 현재가, 호가, 체결가, 일봉/분봉 |
| **프롬프트 관리** | PromptLoader + Markdown 파일 | 코드 수정 없이 에이전트 프롬프트 튜닝 |
| **매매 실행** | TradeExecutor (KIS API 연동) | dry_run 시뮬레이션 + 서킷 브레이커 |
| **에이전트 트레이싱** | AgentTracer (자체 구현) | 판단 근거·결과·실행 시간 JSON 기록 |
| **설정 관리** | YAML (PyYAML) | 감시 종목·매매 규칙·스케줄 설정 |
| **테스트** | pytest | AgentTracer, TradeExecutor, PromptLoader 단위 테스트 |

---

## 👥 팀 구성 & 역할 분담

| Layer | 담당 | 역할 |
|-------|------|------|
| **Layer 1** — Data & Integration | 이강록 | 데이터 파이프라인, 크롤러, KIS API 연동, 비동기 수집 |
| **Layer 2** — RAG & Storage | 이도훈 | RAG 엔진, Hybrid Search, 벡터 DB, OCR, 임베딩 |
| **Layer 3** — Multi-Agent Core | 하제학 | LangGraph 워크플로우, 에이전트 설계, 품질 관리, 트레이싱 |
| **Layer 4** — Application & Execution | 이호준 | FastAPI 백엔드, 프론트엔드, 체결 봇, 사용자 전략 |

---

## 📝 변경 이력

| 버전 | 날짜 | 주요 변경 |
|------|------|----------|
| **v1.2** | 2026-03 | 자율 에이전트 시스템 (AutonomousRunner, TradeExecutor, 3중 서킷 브레이커, YAML 설정), 프롬프트 외부화 (6개 에이전트 → prompts/ Markdown), PromptLoader, CLI `--auto`/`--loop`/`--dry-run` |
| **v1.1** | 2026-03 | 에이전트 트레이싱 시스템 (AgentTracer, Context Manager, 이벤트 타임라인, reasoning 요약/원본 분리, Thread-safe, JSON 구조화 저장) |
| **v1.0** | 2026-03 | AI 서버 분리 (FastAPI :8001), LangGraph 상태 머신, Hybrid Search, Quality Gate, GPU-free 프로바이더 |
| **v0.3** | 2026-02 | ThreadPoolExecutor 병렬 실행, 대화 메모리(10턴 + LRU), Plan A→B 폴백, 품질 등급(A~D) 시스템 |
| **v0.2** | 2026-02 | PaddleOCR-VL-1.5 전환, Qwen3-Reranker-0.6B, Snowflake Arctic Korean 임베딩 (1024dim) |
| **v0.1** | 2025-01 | 초기 MVP — KIS REST API 연동, 기본 멀티 에이전트 구조 |

---

## 🎓 비전공자를 위한 프로젝트 구조 쉬운 설명

이 프로젝트가 어떻게 작동하는지, **회사 조직에 비유**해서 설명합니다.

### 이 프로젝트는 "AI 투자 회사"입니다

```
                        ┌─────────────────────────────┐
                        │  👤 사용자 (투자자)          │
                        │ "삼성전자 분석해줘"          │
                        └──────────┬──────────────────┘
                                   │ 질문
                        ┌──────────▼──────────────────┐
                        │  📋 접수 창구               │
                        │  main.py (CLI)              │
                        │  ai_server (웹 API)         │
                        └──────────┬──────────────────┘
                                   │ 전달
         ┌─────────────────────────▼─────────────────────────┐
         │              🤵 Supervisor (팀장)                  │
         │  "이 질문은 전체 분석이 필요하네. 3명에게 맡기자" │
         └──┬──────────────────┬──────────────────┬──────────┘
            │                  │                  │
    ┌───────▼───────┐ ┌───────▼───────┐ ┌───────▼───────┐
    │ 📰 Analyst    │ │ 📈 Quant      │ │ 📉 Chartist   │
    │ (산업 전문가) │ │ (재무 전문가) │ │ (차트 전문가) │
    │               │ │               │ │               │
    │ "이 회사는    │ │ "PER 15배,   │ │ "RSI 35,     │
    │  반도체 시장  │ │  ROE 12%,    │ │  MACD 상승,  │
    │  1위, 해자가  │ │  부채 안전"  │ │  반등 신호"  │
    │  두꺼움"      │ │              │ │              │
    │  (70점 만점)  │ │ (100점 만점) │ │ (100점 만점) │
    └───────┬───────┘ └───────┬──────┘ └───────┬──────┘
            └─────────────────┼────────────────┘
                              │ 3명의 리포트 취합
                    ┌─────────▼─────────┐
                    │ 🛡️ Risk Manager   │
                    │ (최종 결정자)      │
                    │                    │
                    │ "종합: 230/270점   │
                    │  → 매수 권고       │
                    │  → 비중 50% 추천   │
                    │  → 손절 -10%"      │
                    └────────────────────┘
```

### 폴더별 역할 (회사 부서로 이해하기)

| 폴더 | 비유 | 하는 일 |
|------|------|---------|
| **`main.py`** | 🏢 회사 현관 | 사용자의 질문을 받는 입구. "삼성전자 분석해줘"라고 치면 여기서 시작 |
| **`config/`** | 📋 업무 지시서 | "어떤 종목을 감시하고, 어떤 조건에서 매매할지" 적어둔 설정 파일 |
| **`prompts/`** | 📝 업무 매뉴얼 | 각 전문가(에이전트)에게 "이런 식으로 분석해"라고 알려주는 지시문. Markdown 파일이라 누구나 수정 가능 |
| **`src/agents/`** | 👔 전문가팀 | 6명의 AI 전문가. 각자 맡은 분야를 분석해서 리포트 작성 |
| **`src/runner/`** | 🤖 자동 운전 | 설정 파일대로 알아서 분석하고 매매까지 할 수 있는 자동 시스템 |
| **`src/rag/`** | 📚 자료실 | 리포트, 뉴스 등을 저장하고, 필요할 때 빠르게 찾아주는 검색 엔진 |
| **`src/tools/`** | 🔧 업무 도구 | 실시간 주가 조회, 웹 검색, 재무제표 가져오기 등 실제 데이터를 수집하는 도구 |
| **`src/tracing/`** | 📹 영상 녹화 | 각 전문가가 "왜 그런 판단을 내렸는지" 기록을 남기는 시스템. 나중에 되돌아볼 수 있음 |
| **`src/utils/`** | ⚙️ 지원팀 | 종목 코드 찾기, 대화 기억하기 등 뒤에서 도와주는 유틸리티 |
| **`ai_server/`** | 🌐 웹 서비스 | 웹이나 앱에서 접속할 수 있도록 만든 API 서버 |
| **`data/`** | 🗄️ 창고 | 분석 기록(traces/)과 매매 기록(orders/)이 쌓이는 곳 |

### 쉬운 용어 사전

| 전문 용어 | 쉬운 설명 |
|----------|---------|
| **에이전트 (Agent)** | AI 전문가 1명. 각자 다른 분야를 담당 |
| **LangGraph** | 에이전트들이 순서대로 일하도록 관리하는 "업무 흐름표" |
| **RAG** | 저장된 문서에서 필요한 정보를 찾아오는 "검색 비서" |
| **트레이싱** | "왜 그렇게 판단했는지" 기록을 남기는 것 (판단 일지) |
| **서킷 브레이커** | 자동매매가 폭주하지 않도록 막는 "안전 장치" (하루 한도, 쿨타임) |
| **dry_run** | 실제 돈은 안 쓰고 "이렇게 했으면 어땠을까" 시뮬레이션만 하는 것 |
| **프롬프트** | AI에게 주는 "지시문". "너는 20년 경력의 투자 전략가야..." 같은 것 |
| **벡터 검색** | 의미가 비슷한 문서를 찾는 것 ("수익 좋은 기업" → "ROE 높은 종목" 연결) |
| **BM25** | 정확히 같은 단어가 들어간 문서를 찾는 것 ("PER 12배" → 글자 그대로 검색) |
| **Hybrid Search** | 벡터 검색 + BM25를 **둘 다** 쓰는 것. 둘의 장점을 합침 |
| **폴백 (Fallback)** | Plan A가 실패하면 자동으로 Plan B로 전환하는 것 |
| **Quality Gate** | "분석 결과 품질이 너무 낮으면 다시 하라"고 되돌려 보내는 관문 |
| **YAML** | 설정을 적는 파일 형식. JSON보다 사람이 읽기 쉬움 |

### 데이터가 흐르는 과정 (한 종목 분석)

```
1️⃣ 사용자 입력
   "삼성전자 분석해줘"

2️⃣ Supervisor가 질문 분석
   "→ 전체 분석이 필요, 3명 전문가 투입"

3️⃣ 3명이 동시에 일 시작 (병렬 처리)
   Analyst: 뉴스/리포트 읽고 → 헤게모니 판단 (65/70점)
   Quant:   재무제표 분석 → 재무 등급 (75/100점)
   Chartist: 차트 분석 → 기술적 신호 (매수, 70/100점)

4️⃣ Quality Gate (품질 검사)
   "3명의 결과 품질 OK → 통과"

5️⃣ Risk Manager가 종합 판단
   "65 + 75 + 70 = 210/270점 → 매수"
   "확신도 75%, 리스크 보통, 비중 50% 권고"

6️⃣ 결과 출력 + 트레이스 저장
   사용자에게 보고서 출력
   data/traces/에 판단 과정 JSON 저장
```

---

## 📄 라이선스

MIT License

## 👥 기여

이슈 및 PR 환영합니다!

---

**⚠️ 면책조항**: 이 프로젝트는 교육 및 연구 목적으로 개발되었습니다. 실제 투자 결정에 사용하기 전에 전문가와 상담하시기 바랍니다.
