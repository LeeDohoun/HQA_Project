# HQA — Hegemony Quantitative Analyst

> 🤖 **AI 멀티 에이전트 기반 한국 주식 헤게모니 분석 · 투자 전략 · 자동매매 시스템**
>
> CLI · Streamlit · FastAPI REST API · Next.js

---

## 📋 프로젝트 개요

### 왜 HQA인가?

주식 시장에서 장기적으로 높은 수익을 만드는 기업은 단순히 실적이 좋은 기업이 아니라 **산업의 헤게모니(Hegemony)를 장악한 기업**입니다.

- **Apple**은 스마트폰 생태계를, **NVIDIA**는 AI 반도체 시장을 지배하며 산업 패러다임을 변화시켰습니다.
- 이러한 기업을 발굴하려면 기업 공시, 글로벌 뉴스, 재무 데이터, 산업 구조 등 **다양한 비정형 정보를 종합적으로 분석**해야 합니다.
- 그러나 현대 금융 시장은 **정보 과잉 환경**이며, 개인 투자자가 이를 모두 처리하기란 현실적으로 불가능합니다.

**HQA**는 이 문제를 해결합니다.

6개의 전문 AI 에이전트가 **기업의 시장 지배력(Moat)과 성장성**을 중심으로 협업 분석하여, 개인 투자자에게 **주도주 발굴 → 투자 전략 생성 → 자동 주문 실행**까지 통합된 투자 시스템을 제공합니다.

### ✨ 주요 특징

- 🔄 **LangGraph 상태 머신**: Supervisor가 조율하는 6개 전문 에이전트 (병렬 실행 + 품질 게이트 + 피드백 루프)
- 🔍 **Hybrid Search**: BM25 키워드 검색 + Vector 의미 검색 → RRF 병합 → Qwen3 리랭킹
- 📊 **실시간 시세**: 한국투자증권 REST API + WebSocket 실시간 체결가 스트리밍
- 📝 **PaddleOCR-VL-1.5**: 0.9B VLM 기반 문서 OCR (표/차트/수식/도장 인식)
- 🧠 **RAG 기반 분석**: ChromaDB + Snowflake Arctic Korean 임베딩 (1024차원)
- ⚡ **병렬 실행**: ThreadPoolExecutor로 Analyst/Quant/Chartist 동시 실행
- 💾 **대화 메모리**: 10턴 컨텍스트 유지, 후속 질문 자동 감지
- 🛡️ **데이터 품질 관리**: Plan A→B 폴백, 품질 등급(A~D) 기반 행동 강령
- 🌐 **FastAPI REST API**: 비동기 백엔드, SSE 실시간 스트리밍
- ⚛️ **Next.js 프론트엔드**: React 기반 SPA (SSE 스트리밍 연동)
- 📋 **Task Queue**: Celery + Redis 백그라운드 분석
- 🐳 **Docker Compose**: API + Worker + Redis + PostgreSQL 원클릭 배포
- 🔌 **GPU 의존성 제거**: OCR(Upstage API), Reranker(Cohere API) 프로바이더 패턴

### 핵심 목표

| 목표 | 설명 |
|------|------|
| 🔍 **헤게모니 기업 발굴** | 산업 지배력, 기술적 해자(Moat), 성장성을 정량/정성 분석 |
| 🤖 **멀티 에이전트 분석** | 6개 전문 에이전트의 상호 검증 · 품질 게이트 · 피드백 루프 |
| 📊 **RAG 기반 실시간 분석** | Hybrid Search (Vector + BM25 + Reranking)로 최신 정보 활용 |
| 💹 **자동매매 연동** | KIS API 연동, 투자 지시서(Order Ticket) 생성 및 체결 (모의투자) |
| 🎯 **초개인화 전략** | 사용자의 투자 성향(단타/장기/리스크 회피)에 맞춘 전략 설정 |

---

## 🔬 기존 연구의 한계 & HQA의 해결 방안

| 기존 연구의 한계 | HQA의 해결 방안 |
|----------------|---------------|
| **AI 할루시네이션** — 단일 LLM의 오판 | 멀티 에이전트 상호 검증 + Quality Gate + 품질 등급별 행동 강령 |
| **실시간 정보 부재** — 정적 학습 데이터 의존 | RAG (Hybrid Search) + 실시간 웹 검색 + KIS API 실시간 시세 |
| **자동매매 부재** — "분석→예측"까지만 수행 | Risk Manager → 투자 지시서 생성 → KIS API 체결 연동 |
| **리스크 통제 부재** — 대규모 손실 가능 | 3중 방어막 (Risk Manager + 서킷 브레이커 + LLMOps 모니터링) |

---

## 🏗️ 시스템 아키텍처 (4-Tier Architecture)

```
┌──────────────────────────────────────────────────────────────────┐
│              🖥️  Layer 4: Application & Execution               │
│         (Next.js / Streamlit / CLI / FastAPI + Celery)           │
│  사용자 인터페이스 · SSE 실시간 스트리밍 · 자동매매 체결 봇      │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│              🧠  Layer 3: Multi-Agent Core Engine               │
│                    (LangGraph 상태 머신)                         │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │ Analyst  │ │  Quant   │ │ Chartist │  ← 병렬 Fan-out        │
│  │(Research │ │(재무분석)│ │(기술분석)│                        │
│  │+Strategy)│ │          │ │          │                        │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘                        │
│       └────────────┼────────────┘                               │
│                    ▼                                             │
│            ┌──────────────┐                                     │
│            │ Quality Gate │ ← 품질 D등급 → 재시도 (피드백 루프) │
│            └──────┬───────┘                                     │
│                   ▼                                              │
│            ┌──────────────┐                                     │
│            │ Risk Manager │ → 최종 투자 판단 (270점 만점)       │
│            └──────────────┘                                     │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│              📚  Layer 2: RAG & Storage Layer                   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Hybrid Search Pipeline                                   │    │
│  │ Query ─┬─ Vector (Snowflake Arctic, k=20) ─┐            │    │
│  │        └─ BM25 Keyword (k=20) ──────────────┤→ RRF → Rerank │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ ChromaDB │  │BM25 Index│  │PostgreSQL│  │ Redis Cache  │   │
│  │ (벡터DB) │  │(키워드)  │  │ (원본DB) │  │ (세션/큐)    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘   │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│              🔌  Layer 1: Data & Integration Layer              │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │네이버 금융   │ │ DART 공시    │ │ 네이버 뉴스  │            │
│  │(리포트/재무) │ │ REST API     │ │ (뉴스 크롤링)│            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │네이버 종목   │ │ KIS REST API │ │ KIS WebSocket│            │
│  │토론방(센티먼트│ │ (시세/호가)  │ │ (실시간 틱)  │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│  ┌──────────────┐ ┌──────────────┐                              │
│  │FinanceData   │ │ Tavily 검색  │                              │
│  │Reader(주가)  │ │ (웹/DuckDDG) │                              │
│  └──────────────┘ └──────────────┘                              │
└──────────────────────────────────────────────────────────────────┘
```

### 4계층 분리의 이유

> 실시간 자동매매 시스템에서는 전략을 짜는 **'Slow Brain'**(Layer 3)과 실제 체결을 담당하는 **'Fast Brain'**(Layer 4)을 분리해야 합니다. AI의 복잡한 추론 과정(Latency) 때문에 매수 타이밍을 놓치는 것을 방지하고, 데이터 수집 → 분석 → 실행의 **병목 현상을 계층별로 격리**합니다.

---

## 📡 데이터 수집 파이프라인 (Layer 1 상세)

HQA의 분석 품질은 **수집 데이터의 다양성과 최신성**에 의해 결정됩니다. Layer 1에서는 다양한 외부 소스로부터 정형/비정형 데이터를 수집하여 PostgreSQL(원본 보존)과 ChromaDB(벡터 검색)에 저장합니다.

### 🟢 현재 구현된 데이터 소스

#### 📰 비정형 데이터 (RAG 벡터화 대상)

| # | 데이터 소스 | 수집 대상 | 수집 방식 | 활용 에이전트 |
|---|-----------|----------|---------|-------------|
| 1 | **네이버 뉴스** | 종목 관련 최신 뉴스 (제목, 요약, 본문, 언론사) | 네이버 검색 HTML 크롤링 (`BeautifulSoup`) | Researcher (뉴스 센티먼트) |
| 2 | **DART 전자공시** | 사업보고서, 분기보고서, 주요사항보고서 등 | DART Open API (`REST`) | Researcher (기업 공시) |
| 3 | **네이버 종목토론방** | 개인 투자자 의견, 시장 센티먼트, 루머 | 네이버 금융 토론방 HTML 크롤링 | Researcher (시장 심리) |
| 4 | **네이버 금융 리포트** | 증권사 리포트 (PDF), 목표주가, 투자의견 | HTML 크롤링 + PDF 다운로드 | Researcher (리포트) |

#### 📊 정형 데이터 (DB 직접 조회)

| # | 데이터 소스 | 수집 대상 | 수집 방식 | 활용 에이전트 |
|---|-----------|----------|---------|-------------|
| 5 | **KIS REST API** | 현재가, 호가 10단계, 체결내역, 일봉/분봉 | 한국투자증권 REST API | Chartist, Quant |
| 6 | **KIS WebSocket** | 실시간 체결가 스트리밍 (H0STCNT0) | WebSocket 실시간 스트리밍 | 체결 봇 (Fast Brain) |
| 7 | **FinanceDataReader** | 과거 주가 (수년~수십 년), 한국/미국 주식 | `fdr.DataReader()` 라이브러리 | Chartist (이평선), 백테스트 |
| 8 | **네이버 금융 재무** | PER, PBR, ROE, 부채비율, 매출/영업이익 | HTML 크롤링 | Quant (밸류에이션) |

#### 주가 데이터: KIS API + FinanceDataReader 이중 구조

| 소스 | 데이터 범위 | 용도 | 특징 |
|------|-----------|------|------|
| **FinanceDataReader** | 수년~수십 년 | 장기 이평선 (150일 SMA), 백테스트 | 무료, 일봉, 대량 히스토리 |
| **KIS REST API** | 최근 100일 + 분봉 | 실시간 현재가, 일중 기술분석, 호가 | 수정주가, 분봉 지원 |
| **KIS WebSocket** | 실시간 틱 | 체결 봇 (VWAP 매매 등) | 초당 수십 건 틱 데이터 |

### 수집 → 저장 → 벡터화 흐름

```
📡 수집                    💾 저장                     🧠 벡터화
─────────────────────── → ─────────────────────── → ───────────────────────
네이버 뉴스 크롤링         PostgreSQL (news)           Snowflake Arctic 임베딩
DART API 호출             PostgreSQL (disclosures)    → ChromaDB 저장
네이버 종목토론방 크롤링    PostgreSQL (discussions)     → BM25 인덱스 동시 갱신
네이버 금융 리포트 크롤링   PostgreSQL (reports)        PaddleOCR → 텍스트 → 임베딩
네이버 금융 재무 크롤링     PostgreSQL (재무 지표)       ─────────┐
KIS REST API 시세         PostgreSQL (price_data)        구조화 데이터
KIS WebSocket 틱         인메모리 (실시간 처리)           → DB 직접 조회
FinanceDataReader         PostgreSQL (price_data)      ─────────┘
```

### 🔵 추천 데이터 소스 (추가 구현 권장)

| 우선순위 | 데이터 소스 | 수집 대상 | 구현 방법 |
|---------|-----------|----------|---------|
| 🔴 높음 | **KRX 정보데이터시스템** | 공매도 잔고, 외국인/기관 수급 | `data.krx.co.kr` REST API |
| 🔴 높음 | **네이버 투자자별 매매동향** | 외국인/기관/개인 순매수 추이 | 네이버 금융 HTML 크롤링 |
| 🟡 중간 | **한국 산업 정책 (RSS)** | 반도체 보조금, 전기차 정책, 바이오 규제 | 정부 보도자료 RSS + 뉴스 파이프라인 |
| 🟡 중간 | **한국은행 ECOS** | 기준금리, GDP, CPI, 환율 | ECOS Open API (무료) |
| 🟡 중간 | **FRED** | 미국 금리, FOMC, 달러 인덱스 | `fredapi` Python 라이브러리 |
| 🟡 중간 | **증권사 텔레그램 채널** | 리서치센터 실시간 리포트 알림 | `telethon` (Telegram API) |
| 🟢 낮음 | **38커뮤니케이션** | IPO 공모주, 수요예측 결과 | HTML 크롤링 |
| 🟢 낮음 | **연합/한경 RSS** | 경제 뉴스 실시간 피드 | `feedparser` |

> **정책 데이터 전략**: 별도 크롤러 구축 대신, **기존 뉴스 수집 파이프라인에 정부 보도자료 RSS를 추가**하는 방식이 가장 효율적입니다. Researcher의 `_search_policy()` 산업 키워드 매핑만 확장하면 웹 검색으로도 충분히 커버됩니다.

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

| 기능 | 기존 (v0.3) | LangGraph (v0.4+) |
|------|-------------|-------------------|
| **실행 방식** | ThreadPoolExecutor 병렬 | StateGraph Fan-out/Fan-in |
| **에러 복구** | try/except 기본값 | 노드별 독립 에러 처리 |
| **품질 관리** | 없음 | Quality Gate + 재시도 루프 |
| **데이터 흐름** | 함수 리턴값 전달 | `AnalysisState` TypedDict |
| **확장성** | 코드 수정 필요 | 노드/엣지 추가로 확장 |

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

### BM25 토크나이저 특징 (금융 특화)
- 금융 약어 보호: `PER`, `ROE`, `EBITDA`, `EV/EBITDA` 등
- 숫자+단위 패턴: `12.5배`, `3.2%`, `1,200억`
- 한글 형태소 분리 (형태소 분석기 불필요)
- 종목코드 추출: 4자리 이상 숫자

---

## 📁 프로젝트 구조

```
HQA_Project/
├── main.py                     # CLI 엔트리포인트
├── pipeline_runner.py          # 데이터 파이프라인 CLI
├── requirements.txt            # 패키지 의존성 (개발)
├── requirements-prod.txt       # 패키지 의존성 (프로덕션, GPU 불필요)
├── Dockerfile                  # Docker 이미지 빌드
├── docker-compose.yml          # 프로덕션 스택 (API+Redis+PostgreSQL)
├── alembic.ini                 # DB 마이그레이션 설정
│
├── backend/                    # ★ FastAPI 프로덕션 백엔드
│   ├── app.py                  #   FastAPI 메인 + 미들웨어 (CORS, Rate Limit)
│   ├── config.py               #   환경 설정 (Pydantic Settings)
│   ├── api/
│   │   ├── schemas.py          #   요청/응답 Pydantic 스키마
│   │   ├── dependencies.py     #   의존성 주입 (API 키 검증 등)
│   │   └── routes/
│   │       ├── health.py       #   헬스체크 엔드포인트
│   │       ├── stocks.py       #   종목 검색 / 실시간 시세
│   │       ├── analysis.py     #   분석 요청/결과/SSE/대화/쿼리 제안
│   │       └── charts.py       #   차트 데이터 (WebSocket)
│   ├── services/
│   │   ├── analysis_service.py #   비즈니스 로직 (인메모리 + Celery)
│   │   └── kis_websocket_client.py # KIS WebSocket 실시간 체결가 클라이언트
│   ├── tasks/
│   │   ├── celery_app.py       #   Celery 설정
│   │   └── analysis_tasks.py   #   백그라운드 분석 태스크
│   ├── database/
│   │   ├── connection.py       #   SQLAlchemy Async 연결
│   │   └── models.py           #   DB 모델 (User, Analysis, Chat)
│   └── middleware/
│       ├── rate_limit.py       #   IP 기반 Rate Limiting
│       └── error_handler.py    #   전역 에러 핸들링
│
├── frontend/                   # ★ Next.js 프론트엔드 (React/TypeScript)
│   ├── package.json
│   ├── next.config.js
│   └── src/
│       ├── lib/api.ts          #   API 클라이언트 (SSE 포함)
│       └── app/
│           ├── layout.tsx
│           ├── globals.css
│           └── page.tsx        #   메인 페이지 (검색→분석→결과)
│
├── src/                        # ★ 핵심 비즈니스 로직
│   ├── agents/                 #   AI 에이전트 (LangGraph 워크플로우)
│   │   ├── graph.py            #   StateGraph 상태 머신
│   │   ├── llm_config.py       #   LLM 설정 (Gemini Instruct/Thinking/Vision)
│   │   ├── supervisor.py       #   Supervisor (의도분석/라우팅/메모리)
│   │   ├── analyst.py          #   Analyst (Researcher+Strategist 통합 래퍼)
│   │   ├── researcher.py       #   Researcher (정보수집/Plan A→B 폴백)
│   │   ├── strategist.py       #   Strategist (헤게모니 분석/품질 행동강령)
│   │   ├── quant.py            #   Quant (재무 분석/웹 폴백)
│   │   ├── chartist.py         #   Chartist (기술적 분석)
│   │   └── risk_manager.py     #   Risk Manager (최종 판단)
│   │
│   ├── rag/                    #   RAG 파이프라인
│   │   ├── retriever.py        #   통합 검색기 (Vector + BM25 + Rerank)
│   │   ├── bm25_index.py       #   BM25 키워드 검색 인덱스 (금융 특화)
│   │   ├── embeddings.py       #   Snowflake Arctic Korean (1024dim)
│   │   ├── vector_store.py     #   ChromaDB 벡터 저장소
│   │   ├── reranker.py         #   Qwen3-Reranker-0.6B
│   │   ├── reranker_provider.py#   Reranker 프로바이더 (Local/Cohere API)
│   │   ├── ocr_processor.py    #   PaddleOCR-VL-1.5 문서 OCR
│   │   ├── ocr_provider.py     #   OCR 프로바이더 (Local/Upstage API)
│   │   ├── document_loader.py  #   문서 로딩 및 전처리
│   │   └── text_splitter.py    #   텍스트 청킹 (1000자/200 오버랩)
│   │
│   ├── data_pipeline/          #   데이터 수집/가공
│   │   ├── data_ingestion.py   #   DataIngestionPipeline (수집→저장→벡터화 오케스트레이션)
│   │   ├── crawler.py          #   네이버 금융 리포트 크롤러
│   │   ├── news_crawler.py     #   네이버 뉴스 크롤러
│   │   ├── discussion_crawler.py # 네이버 종목토론방 크롤러 (센티먼트)
│   │   ├── dart_collector.py   #   DART 공시 수집
│   │   └── price_loader.py     #   주가 데이터 (FinanceDataReader)
│   │
│   ├── database/               #   데이터베이스
│   │   └── raw_data_store.py   #   PostgreSQL 원본 데이터 저장소
│   │
│   ├── tools/                  #   에이전트 도구
│   │   ├── realtime_tool.py    #   KIS 실시간 시세 API (현재가/호가/일봉/분봉)
│   │   ├── web_search_tool.py  #   웹 검색 (Tavily/DuckDuckGo 폴백)
│   │   ├── finance_tool.py     #   종목 코드 ↔ 종목명 매핑
│   │   ├── rag_tool.py         #   RAG 검색 도구 (Hybrid Search)
│   │   ├── charts_tools.py     #   차트 생성 도구
│   │   └── search_tool.py      #   검색 도구
│   │
│   └── utils/                  #   유틸리티
│       ├── stock_mapper.py     #   종목명 ↔ 코드 매핑
│       ├── memory.py           #   대화 메모리 (10턴, LRU 캐시)
│       ├── parallel.py         #   병렬 실행 (ThreadPoolExecutor)
│       └── kis_auth.py         #   KIS API 인증 모듈
│
├── dashboard/                  # Streamlit 대시보드 (내부용)
│   └── app.py
│
└── alembic/                    # DB 마이그레이션
    ├── env.py
    └── versions/
```

---

## 🛡️ 리스크 관리 & 안전장치 (3중 방어막)

자동매매 시스템의 특성상, 외부 리스크에 대한 다층 방어 체계를 구축합니다.

| 방어 계층 | 메커니즘 | 상세 |
|----------|---------|------|
| **1. AI 판단 검증** | Risk Manager 에이전트 | 팩트 데이터(Quant 점수, Tool 결과)만 기반으로 판단, JSON 파싱 실패 시 예외 처리 |
| **2. 서킷 브레이커** | 하드코딩 제한 | 일일 최대 매수 금액 제한, 동일 종목 연속 주문 쿨다운, 손절 라인 고정 |
| **3. LLMOps 모니터링** | LangSmith 트레이싱 | 에이전트 사고 과정 실시간 추적, 토큰 사용량 하드캡, 이상 징후 즉각 중단 |
| **4. 인프라 장애 대응** | Fallback 시스템 | LLM API 타임아웃 → 신규 매수 중단 + 알림, WebSocket 끊김 → REST 폴링 + 지수 백오프 재연결 |

### 데이터 품질 관리

```
ResearchResult.evaluate_quality()
    → A등급 (80+): 확신 있는 톤으로 분석
    → B등급 (60+): 기본 분석 + 주의 문구
    → C등급 (40+): 1단계 하향 조정
    → D등급 (40-): 2단계 하향 + Quality Gate에서 자동 재시도
```

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
| WebSocket 연결 끊김 | → REST API 폴링 전환 + 지수 백오프 재연결 |
| 개별 에이전트 오류 | → 기본값 대체 후 계속 진행 |

---

## 🚀 실행 방법

### 환경 설정

```bash
# 기본 패키지 설치
pip install -r requirements.txt

# LangGraph + BM25 (권장)
pip install langgraph rank-bm25

# PaddleOCR-VL-1.5 (PDF 문서 OCR)
# GPU 버전 (CUDA 12.6)
pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
# 또는 CPU 버전
pip install paddlepaddle==3.2.1
pip install -U "paddleocr[doc-parser]"

# 환경 변수 설정
cp .env.example .env
```

### CLI 모드

```bash
python main.py                    # 대화형 모드 (Supervisor 기반, 메모리 유지)
python main.py -s 삼성전자         # 전체 분석 (LangGraph 워크플로우)
python main.py --stock 005930      # 종목코드로 분석
python main.py -q 현대차           # 빠른 분석 (Quant + Chartist)
python main.py -p SK하이닉스       # 실시간 시세 조회
```

### 데이터 파이프라인

```bash
# 종목 전체 데이터 수집 (뉴스 + 리포트 + 공시 + 토론방 + 주가)
python pipeline_runner.py ingest --code 005930 --name 삼성전자

# 여러 종목 일괄 수집
python pipeline_runner.py ingest-batch --codes 005930,000660,035420

# PDF 문서 인덱싱
python pipeline_runner.py index-pdf --path ./reports/sample.pdf --stock-code 005930

# 디렉토리 일괄 인덱싱
python pipeline_runner.py index-dir --path ./data/reports --pattern "*.pdf"

# RAG 검색 테스트
python pipeline_runner.py search --query "삼성전자 반도체 실적"

# 저장소 상태 확인
python pipeline_runner.py status
```

### 대시보드

```bash
streamlit run dashboard/app.py
```

### 🌐 웹 서비스

#### 옵션 A: 로컬 개발 (Redis 없이)
```bash
# FastAPI 백엔드 (자동 리로드)
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
# → API 문서: http://localhost:8000/docs
# → 헬스체크: http://localhost:8000/health

# Next.js 프론트엔드
cd frontend && npm install && npm run dev
# → http://localhost:3000
```

#### 옵션 B: Docker Compose (프로덕션)
```bash
cp .env.example .env                          # 환경변수 설정
docker-compose up -d                          # API + Redis + PostgreSQL + Worker
docker-compose exec api alembic upgrade head  # DB 마이그레이션
docker-compose logs -f api                    # 로그 확인
```

#### 옵션 C: GPU 없이 프로덕션 (Upstage + Cohere)
```env
# .env에 추가
OCR_PROVIDER=upstage
UPSTAGE_API_KEY=your_key
RERANKER_PROVIDER=cohere
COHERE_API_KEY=your_key
```

### API 사용 예시

```bash
# 종목 검색
curl http://localhost:8000/api/v1/stocks/search?q=삼성전자

# 분석 요청 (비동기)
curl -X POST http://localhost:8000/api/v1/analysis \
  -H "Content-Type: application/json" \
  -d '{"stock_name": "삼성전자", "stock_code": "005930", "mode": "full"}'

# 결과 조회
curl http://localhost:8000/api/v1/analysis/{task_id}

# SSE 스트리밍 (에이전트별 진행 상황 실시간)
curl -N http://localhost:8000/api/v1/analysis/{task_id}/stream

# 대화형 분석
curl -X POST http://localhost:8000/api/v1/analysis/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "삼성전자 분석해줘"}'
```

---

## ⚙️ API 설정 상세

### Google Gemini API (필수)
```env
# https://aistudio.google.com/ 에서 발급
GOOGLE_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 한국투자증권 KIS API (선택 — 실시간 시세/자동매매)
```env
# https://apiportal.koreainvestment.com/ 에서 발급
KIS_APP_KEY=PSxxxxxxxxxxx
KIS_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_ACCOUNT_NO=12345678-01
# 실전투자: KIS_IS_REAL=true (기본값: 모의투자)
```

### DART API (선택 — 공시 검색)
```env
# https://opendart.fss.or.kr/ 에서 발급
DART_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
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
| **병렬 실행** | ThreadPoolExecutor (폴백) | 에이전트 동시 실행 |
| **문서 OCR** | PaddleOCR-VL-1.5 (0.9B) | PDF → Markdown 텍스트 변환 |
| **임베딩** | Snowflake Arctic Korean | 1024차원, 8192토큰, 한국어 최적화 |
| **벡터 DB** | ChromaDB | 벡터 유사도 검색 |
| **키워드 검색** | rank-bm25 (선택) | BM25 Okapi 금융 특화 키워드 매칭 |
| **리랭커** | Qwen3-Reranker-0.6B | 검색 결과 재순위 |
| **점수 병합** | RRF (Reciprocal Rank Fusion) | Vector + BM25 점수 통합 |
| **웹 검색** | Tavily (1차) / DuckDuckGo (2차) | 실시간 뉴스/정보 폴백 |
| **주식 시세** | 한국투자증권 REST + WebSocket API | 현재가, 호가, 체결가, 일봉/분봉 |
| **과거 주가** | FinanceDataReader | 장기 히스토리, 이평선 계산, 백테스트 |
| **재무 크롤링** | 네이버 금융 + 웹 폴백 | PER/PBR/ROE 수집 |
| **백엔드** | FastAPI + Celery + Redis | 비동기 REST API, SSE, 백그라운드 태스크 |
| **프론트엔드** | Next.js + TailwindCSS | React SPA, SSE 스트리밍 |
| **DB** | PostgreSQL + SQLAlchemy Async | 사용자/분석/주문/원본 데이터 |
| **컨테이너** | Docker Compose | 원클릭 프로덕션 배포 |
| **대화 메모리** | ConversationMemory | 10턴 히스토리, LRU 캐시 |
| **대시보드** | Streamlit + Plotly | 웹 UI, 인터랙티브 차트 |
| **모니터링** | LangSmith | 에이전트 트레이싱, 토큰 비용 추적 |

---

## 👥 팀 구성 & 역할 분담

| Layer | 담당 | 역할 |
|-------|------|------|
| **Layer 1** — Data & Integration | 이강록 | 데이터 파이프라인, 크롤러, KIS API 연동, 비동기 수집 |
| **Layer 2** — RAG & Storage | 이도훈 | RAG 엔진, Hybrid Search, 벡터 DB, OCR, 임베딩 |
| **Layer 3** — Multi-Agent Core | 하제학 | LangGraph 워크플로우, 에이전트 설계, 품질 관리, LLMOps |
| **Layer 4** — Application & Execution | 이호준 | FastAPI 백엔드, Next.js 프론트엔드, 체결 봇, 사용자 전략 |

---

## 📊 평가 방법

| 평가 항목 | 방법 |
|----------|------|
| **투자 전략 성능** | 모의투자 백테스트, 시장 지수 대비 수익률 비교 |
| **리스크 효율성** | Sharpe Ratio (리스크 대비 수익 효율) |
| **최대 손실 통제** | Maximum Drawdown (최대 낙폭) |
| **AI 분석 정확도** | 에이전트 판단 vs 실제 시장 결과 비교 |
| **시스템 안정성** | 장애 복구 시간, Fallback 성공률 |

---

## 📝 변경 이력

| 버전 | 날짜 | 주요 변경 |
|------|------|----------|
| **v1.0** | 2026-03 | FastAPI REST API, Next.js 프론트엔드, Docker Compose, SSE 스트리밍, Celery Task Queue, KIS WebSocket, GPU-free 프로바이더 |
| **v0.4** | 2026-02 | LangGraph 상태 머신, Hybrid Search (BM25 + Vector + RRF), Quality Gate 피드백 루프, BM25 금융 토크나이저 |
| **v0.3** | 2026-02 | ThreadPoolExecutor 병렬 실행, 대화 메모리(10턴 + LRU), Plan A→B 폴백, 품질 등급(A~D) 시스템 |
| **v0.2** | 2026-02 | PaddleOCR-VL-1.5 전환, Qwen3-Reranker-0.6B, Snowflake Arctic Korean 임베딩 (1024dim) |
| **v0.1** | 2025-01 | 초기 MVP — KIS REST API 연동, Streamlit 대시보드, 기본 멀티 에이전트 구조 |

---

## 📄 라이선스

MIT License

## 👥 기여

이슈 및 PR 환영합니다!

---

**⚠️ 면책조항**: 이 프로젝트는 교육 및 연구 목적으로 개발되었습니다. 실제 투자 결정에 사용하기 전에 전문가와 상담하시기 바랍니다. 자동매매 기능은 초기 단계에서 모의투자 환경에서만 테스트됩니다.
