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

> 📌 데이터 수집 파이프라인(`data_pipeline/`), 프론트엔드(`frontend/`), 메인 백엔드(`backend/`), 대시보드(`dashboard/`) 등은 **별도 브랜치**에서 관리됩니다.

### ✨ 주요 특징

- 🔄 **LangGraph 상태 머신**: Supervisor가 조율하는 6개 전문 에이전트 (병렬 실행 + 품질 게이트 + 피드백 루프)
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
| 🤖 **멀티 에이전트 분석** | 6개 전문 에이전트의 상호 검증 · 품질 게이트 · 피드백 루프 |
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

## 📁 프로젝트 구조

```
HQA_Project/                        # ai-main 브랜치
├── main.py                         # CLI 엔트리포인트 (대화형/종목분석/자율에이전트)
├── Dockerfile                      # Docker 이미지 빌드
├── docker-compose.yml              # AI 서버 스택
├── .env.example                    # 환경 변수 템플릿
│
├── config/                         # ★ 자율 에이전트 설정
│   └── watchlist.yaml              #   감시 종목 + 스케줄 + 매매 규칙
│
├── prompts/                        # ★ 에이전트 프롬프트 (외부 파일)
│   ├── supervisor/
│   │   └── routing.md              #   의도 분류 프롬프트
│   ├── researcher/
│   │   └── summary.md              #   리서치 요약 프롬프트
│   ├── strategist/
│   │   └── hegemony.md             #   헤게모니 분석 프롬프트
│   ├── quant/
│   │   └── web_fallback.md         #   재무 분석 (웹 폴백) 프롬프트
│   ├── chartist/
│   │   └── analysis.md             #   기술적 분석 프롬프트
│   └── risk_manager/
│       └── decision.md             #   최종 판단 프롬프트
│
├── ai_server/                      # ★ AI 전용 FastAPI 서버 (포트 8001)
│   ├── app.py                      #   FastAPI 메인 (분석/대화/쿼리제안 API)
│   ├── Dockerfile                  #   AI 서버 전용 Docker 이미지
│   └── requirements.txt            #   AI 서버 Python 의존성
│
├── src/                            # ★ 핵심 비즈니스 로직
│   ├── agents/                     #   AI 에이전트 (LangGraph 워크플로우)
│   │   ├── __init__.py             #   패키지 공개 API
│   │   ├── graph.py                #   StateGraph 상태 머신 + AgentTracer 계측
│   │   ├── llm_config.py           #   LLM 설정 (Gemini Instruct/Thinking/Vision)
│   │   ├── supervisor.py           #   Supervisor (의도분석/라우팅/메모리)
│   │   ├── analyst.py              #   Analyst (Researcher+Strategist 통합 래퍼)
│   │   ├── researcher.py           #   Researcher (정보수집/Plan A→B 폴백)
│   │   ├── strategist.py           #   Strategist (헤게모니 분석/품질 행동강령)
│   │   ├── quant.py                #   Quant (재무 분석/웹 폴백)
│   │   ├── chartist.py             #   Chartist (기술적 분석)
│   │   └── risk_manager.py         #   Risk Manager (최종 판단)
│   │
│   ├── runner/                     #   ★ 자율 에이전트 실행 모듈
│   │   ├── __init__.py             #   패키지 공개 API
│   │   ├── autonomous_runner.py    #   자율 실행기 (run_once / run_loop)
│   │   └── trade_executor.py       #   매매 실행기 (KIS API + 서킷 브레이커)
│   │
│   ├── tracing/                    #   ★ 에이전트 트레이싱 시스템
│   │   ├── __init__.py             #   패키지 공개 API
│   │   └── agent_tracer.py         #   AgentTracer (판단근거/결과/실행시간 JSON 기록)
│   │
│   ├── rag/                        #   RAG 파이프라인
│   │   ├── __init__.py             #   패키지 초기화
│   │   ├── retriever.py            #   통합 검색기 (Vector + BM25 + Rerank)
│   │   ├── bm25_index.py           #   BM25 키워드 검색 인덱스 (금융 특화)
│   │   ├── embeddings.py           #   Snowflake Arctic Korean (1024dim)
│   │   ├── vector_store.py         #   ChromaDB 벡터 저장소
│   │   ├── reranker.py             #   Qwen3-Reranker-0.6B
│   │   ├── reranker_provider.py    #   Reranker 프로바이더 (Local/Cohere API)
│   │   ├── ocr_processor.py        #   PaddleOCR-VL-1.5 문서 OCR
│   │   ├── ocr_provider.py         #   OCR 프로바이더 (Local/Upstage API)
│   │   ├── document_loader.py      #   문서 로딩 및 전처리
│   │   └── text_splitter.py        #   텍스트 청킹 (1000자/200 오버랩)
│   │
│   ├── database/                   #   데이터베이스
│   │   ├── __init__.py             #   패키지 초기화
│   │   ├── raw_data_store.py       #   PostgreSQL 원본 데이터 저장소
│   │   └── vector_store.py         #   벡터 스토어 헬퍼
│   │
│   ├── tools/                      #   에이전트 도구
│   │   ├── __init__.py             #   패키지 공개 API
│   │   ├── realtime_tool.py        #   KIS 실시간 시세 API (현재가/호가/일봉/분봉)
│   │   ├── web_search_tool.py      #   웹 검색 (Tavily/DuckDuckGo 폴백)
│   │   ├── finance_tool.py         #   종목 코드 ↔ 종목명 매핑 + 네이버 재무
│   │   ├── rag_tool.py             #   RAG 검색 도구 (Hybrid Search)
│   │   ├── charts_tools.py         #   차트 생성 도구
│   │   └── search_tool.py          #   검색 도구
│   │
│   └── utils/                      #   유틸리티
│       ├── __init__.py             #   패키지 초기화
│       ├── prompt_loader.py        #   ★ 프롬프트 파일 로더 (fallback 지원)
│       ├── stock_mapper.py         #   종목명 ↔ 코드 매핑
│       ├── memory.py               #   대화 메모리 (10턴, LRU 캐시)
│       ├── parallel.py             #   병렬 실행 (ThreadPoolExecutor)
│       └── kis_auth.py             #   KIS API 인증 모듈
│
├── tests/                          # ★ 테스트
│   └── test_tracer.py              #   AgentTracer 단위 테스트 (8개 테스트 그룹)
│
└── data/                           # 런타임 생성 데이터
    ├── traces/                     #   ★ 에이전트 트레이스 JSON 저장
    │   └── {date}/{stock_code}/{trace_id}.json
    └── orders/                     #   ★ 매매 주문 기록 (JSONL)
        └── {date}/orders.jsonl
```

---

## 🤖 멀티 에이전트 상세

### 에이전트 구성

| 에이전트 | LLM 모드 | 역할 | 점수 체계 |
|---------|---------|------|----------|
| **Supervisor** | Instruct | 사용자 의도 분석 · 에이전트 라우팅 · 10턴 대화 메모리 | — |
| **Researcher** | Instruct (빠른 수집) | 정보 수집 · Plan A→B 폴백 · 품질 평가(A~D등급) | 품질 0~100 |
| **Strategist** | Thinking (깊은 추론) | 헤게모니 분석 · 독점력/성장성 평가 · 품질 등급별 톤 조정 | Moat 0~40 + Growth 0~30 = **0~70** |
| **Quant** | Instruct | PER/PBR/ROE 등 재무 지표 분석 · 밸류에이션 | 4영역 각 25 = **0~100** |
| **Chartist** | Instruct | RSI/MACD/볼린저밴드 기술적 분석 | 4영역 합산 = **0~100** |
| **Risk Manager** | Thinking | 3개 에이전트 종합 → 최종 투자 판단 · 투자 지시서 생성 | 총 **270점 만점** |

### 에이전트 프롬프트 관리

각 에이전트의 프롬프트는 **코드 밖의 Markdown 파일**로 분리되어 있어, 코드를 수정하지 않고도 프롬프트를 수정할 수 있습니다.

| 에이전트 | 프롬프트 파일 | 주요 변수 |
|---------|-------------|----------|
| Supervisor | `prompts/supervisor/routing.md` | `{query}`, `{conversation_history}` |
| Researcher | `prompts/researcher/summary.md` | `{stock_name}`, `{stock_code}`, `{research_data}` |
| Strategist | `prompts/strategist/hegemony.md` | `{stock_name}`, `{research_summary}` |
| Quant | `prompts/quant/web_fallback.md` | `{stock_name}`, `{stock_code}`, `{search_results}` |
| Chartist | `prompts/chartist/analysis.md` | `{stock_name}`, `{stock_code}` |
| Risk Manager | `prompts/risk_manager/decision.md` | `{stock_name}`, `{stock_code}` + 12개 점수 변수 |

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

#### Researcher → Strategist (Analyst 통합)
- **Researcher**: 5개 카테고리별 Plan A→B 폴백 (리포트: RAG→웹 / 뉴스: 웹→RAG)
- **품질 평가**: `ResearchResult.evaluate_quality()` → A/B/C/D 등급 + 경고 목록
- **Strategist**: 데이터 품질 등급별 분석 톤 자동 조정 (D등급: 2단계 하향)
- **출력**: `HegemonyScore` (moat + growth = 0~70점)

#### Quant (퀀트)
- **전략**: Plan A (네이버 금융 크롤링) → Plan B (웹 검색 + LLM JSON 추출)
- **출력**: `QuantScore` (valuation/profitability/growth/stability, 총 0~100점)

#### Chartist (차티스트)
- **지표**: RSI, MACD, Bollinger Band, 이동평균선, 거래량
- **출력**: `ChartistScore` (trend/momentum/volatility/volume, 총 0~100점)

#### Risk Manager (리스크 관리자)
- **입력**: `AgentScores` (Analyst 70 + Quant 100 + Chartist 100 = 270점 만점)
- **출력**: `FinalDecision` (투자 행동, 리스크 레벨, 목표가, 손절가, 포지션 사이징)

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
| **5. 데이터 품질 관리** | Quality Gate | D등급 → 자동 재시도, Strategist 분석 톤 2단계 하향 |

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
| **LLM (Instruct)** | Gemini 2.5 Flash Lite | Supervisor, Researcher, Quant, Chartist |
| **LLM (Thinking)** | Gemini 2.5 Flash Preview | Strategist, Risk Manager |
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
