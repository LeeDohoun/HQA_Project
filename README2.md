# HQA — Hegemony Quantitative Analyst

> 🤖 **AI 멀티 에이전트 기반 한국 주식 헤게모니 분석 · 투자 전략 · 자동매매 시스템**

---

## 📋 프로젝트 개요

### 왜 HQA인가?

주식 시장에서 장기적으로 높은 수익을 만드는 기업은 단순히 실적이 좋은 기업이 아니라 **산업의 헤게모니(Hegemony)를 장악한 기업**입니다.

- **Apple**은 스마트폰 생태계를, **NVIDIA**는 AI 반도체 시장을 지배하며 산업 패러다임을 변화시켰습니다.
- 이러한 기업을 발굴하려면 기업 공시, 글로벌 뉴스, 재무 데이터, 산업 구조 등 **다양한 비정형 정보를 종합적으로 분석**해야 합니다.
- 그러나 현대 금융 시장은 **정보 과잉 환경**이며, 개인 투자자가 이를 모두 처리하기란 현실적으로 불가능합니다.

**HQA**는 이 문제를 해결합니다.

6개의 전문 AI 에이전트가 **기업의 시장 지배력(Moat)과 성장성**을 중심으로 협업 분석하여, 개인 투자자에게 **주도주 발굴 → 투자 전략 생성 → 자동 주문 실행**까지 통합된 투자 시스템을 제공합니다.

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
│                (Next.js / Streamlit / CLI / FastAPI)             │
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
│  │네이버 종목   │ │ KIS REST API │ │FinanceData   │            │
│  │토론방(센티먼트│ │ (실시간시세) │ │Reader(주가)  │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│  ┌──────────────┐ ┌──────────────┐                              │
│  │ Tavily 검색  │ │ KRX 정보    │                              │
│  │ (웹/DuckDDG) │ │ (공매도 등) │                              │
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
| 1 | **네이버 뉴스** | 종목 관련 최신 뉴스 (제목, 요약, 본문, 언론사) | 네이버 검색 HTML 크롤링 (`BeautifulSoup`) | Researcher (뉴스 센티먼트 분석) |
| 2 | **DART 전자공시** | 사업보고서, 분기보고서, 주요사항보고서 등 | DART Open API (`REST`) | Researcher (기업 공시 분석) |
| 3 | **네이버 종목토론방** | 개인 투자자 의견, 시장 센티먼트, 루머 | 네이버 금융 토론방 HTML 크롤링 | Researcher (시장 심리 분석) |
| 4 | **네이버 금융 리포트** | 증권사 리포트 (PDF), 목표주가, 투자의견 | HTML 크롤링 + PDF 다운로드 | Researcher (리포트 분석) |

#### 📊 정형 데이터 (DB 직접 조회)

| # | 데이터 소스 | 수집 대상 | 수집 방식 | 활용 에이전트 |
|---|-----------|----------|---------|-------------|
| 5 | **KIS REST API** | 실시간 현재가, 호가 10단계, 체결내역, 일봉/분봉 | 한국투자증권 REST API 호출 | Chartist (기술분석), Quant (PER/PBR) |
| 6 | **KIS WebSocket** | 실시간 체결가 스트리밍 (H0STCNT0), 자동 재연결 | WebSocket 실시간 스트리밍 | 체결 봇 (Fast Brain) |
| 7 | **FinanceDataReader** | 과거 주가 (수년~수십 년), 한국/미국 주식 | `fdr.DataReader()` 라이브러리 | Chartist (이평선), 백테스트 |
| 8 | **네이버 금융 재무** | PER, PBR, ROE, 부채비율, 매출/영업이익 | HTML 크롤링 | Quant (밸류에이션 분석) |

### 주가 데이터: KIS API + FinanceDataReader 이중 구조

주가 데이터는 **용도에 따라 2개 소스를 병행**합니다:

| 소스 | 데이터 범위 | 용도 | 특징 |
|------|-----------|------|------|
| **FinanceDataReader** | 수년~수십 년 | 장기 이평선 (150일 SMA), 백테스트 | 무료, 일봉 위주, 대량 히스토리 |
| **KIS REST API** | 최근 100일 + 분봉 | 실시간 현재가, 일중 기술분석, 호가 | 정확한 수정주가, 분봉 지원 |
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

### 네이버 종목토론방 수집 상세

네이버 종목토론방은 **개인 투자자들의 실시간 시장 심리(센티먼트)**를 파악하는 데 핵심적인 소스입니다.

| 항목 | 상세 |
|------|------|
| **URL 패턴** | `https://finance.naver.com/item/board.naver?code={종목코드}` |
| **수집 데이터** | 글 제목, 작성자, 작성일, 조회수, 공감/비공감 수, 본문 |
| **활용 방법** | LLM 기반 센티먼트 분석 → 긍정/부정/중립 분류 → Researcher에게 시장 심리 지표 제공 |
| **RAG 저장** | 토론방 글 → 텍스트 임베딩 → ChromaDB 저장 → Hybrid Search로 검색 |
| **중복 처리** | 글 URL 기준 UNIQUE 제약 → 중복 수집 방지 |

### 🔵 추천 데이터 소스 (추가 구현 권장)

주식 분석의 정확도와 깊이를 높이기 위해, 아래 데이터 소스들의 추가 구현을 권장합니다:

#### 📊 정량 데이터 (Quant Agent 강화)

| # | 데이터 소스 | 수집 대상 | 난이도 | 가치 | 구현 방법 |
|---|-----------|----------|-------|------|----------|
| 1 | **KRX 정보데이터시스템** | 공매도 잔고, 대차거래, 외국인/기관 수급 | ⭐⭐ | 🔥🔥🔥 | `data.krx.co.kr` REST API 또는 HTML 크롤링 |
| 2 | **네이버 금융 투자자별 매매동향** | 외국인/기관/개인 순매수 추이 | ⭐ | 🔥🔥🔥 | 네이버 금융 HTML 크롤링 (`/item/frgn.naver`) |
| 3 | **한국은행 경제통계(ECOS)** | 기준금리, GDP, CPI, 환율 등 거시경제 지표 | ⭐⭐ | 🔥🔥 | ECOS Open API (무료, JSON/XML) |
| 4 | **FRED (미국 연방준비은행)** | 미국 금리, 달러 인덱스, 글로벌 경기 지표 | ⭐ | 🔥🔥 | `fredapi` Python 라이브러리 (무료 API 키) |
| 5 | **네이버 금융 ETF/업종 지수** | 코스피/코스닥 업종별 등락, 테마 ETF 수익률 | ⭐ | 🔥🔥 | 네이버 금융 HTML 크롤링 |

#### 📰 정성 데이터 (Researcher/Analyst Agent 강화)

| # | 데이터 소스 | 수집 대상 | 난이도 | 가치 | 구현 방법 |
|---|-----------|----------|-------|------|----------|
| 6 | **38커뮤니케이션** | IPO 공모주 정보, 수요예측 결과, 청약 일정 | ⭐⭐ | 🔥🔥 | HTML 크롤링 (`www.38.co.kr`) |
| 7 | **연합뉴스/한경/매경 RSS** | 경제 뉴스 실시간 피드 (네이버 뉴스보다 빠름) | ⭐ | 🔥🔥 | RSS/Atom 피드 파싱 (`feedparser`) |
| 8 | **증권사 텔레그램 채널** | 증권사 리서치센터 실시간 리포트 알림 | ⭐⭐⭐ | 🔥🔥🔥 | `telethon` 라이브러리 (Telegram API) |
| 9 | **Reddit/X(Twitter) 금융** | 글로벌 투자 심리, 밈 주식 트렌드 | ⭐⭐⭐ | 🔥 | Reddit API (`praw`) / X API |

#### ⚡ 실시간 데이터 (Chartist Agent 강화)

| # | 데이터 소스 | 수집 대상 | 난이도 | 가치 | 구현 방법 |
|---|-----------|----------|-------|------|----------|
| 10 | ~~KIS WebSocket API~~ | 실시간 체결가, 호가, 체결 강도 | — | 🔥🔥🔥 | ✅ **구현 완료** (`kis_websocket_client.py`) |
| 11 | **네이버 금융 실시간 차트** | 분봉/일봉 차트 데이터 (캔들, 거래량) | ⭐ | 🔥🔥 | HTML 크롤링 또는 내부 API 호출 |

#### 🏛️ 정책/거시경제 데이터 (Researcher Agent 강화)

| # | 데이터 소스 | 수집 대상 | 필요도 | 구현 방법 |
|---|-----------|----------|-------|----------|
| 12 | **한국 산업 정책** | 반도체 보조금, 전기차 정책, 바이오 규제 등 | 🔥🔥🔥 필수 | 정부 보도자료 RSS + 뉴스 파이프라인 활용 |
| 13 | **한국 금리/세제** | 금투세, 대출 규제, 한은 기준금리 | 🔥🔥🔥 필수 | ECOS API + 기재부 RSS |
| 14 | **미국 통상 정책** | IRA법, CHIPS법, 관세, 수출 규제 | 🔥🔥 높음 | 웹 검색 + USTR 보도자료 RSS |
| 15 | **미국 FOMC/금리** | 연방기금금리, 양적긴축, 점도표 | 🔥🔥 높음 | FRED API (`fredapi`) |
| 16 | **중국 정책** | 반도체 자급, 희토류 규제 (반도체/2차전지 한정) | 🔥 중간 | 웹 검색 (한국 영향 기사 위주) |

> **정책 데이터 전략**: 별도 크롤러 구축 대신, **기존 뉴스 수집 파이프라인에 정부 보도자료 RSS를 추가**하는 방식이 가장 효율적입니다. 정책 데이터는 본질적으로 "뉴스"이며, Researcher의 `_search_policy()` 산업 키워드 매핑만 확장하면 웹 검색으로도 충분히 커버됩니다.

### 📋 추천 구현 우선순위 로드맵

```
[높은 우선순위] ──────────────────────────────────────────────────
 ✅ 네이버 종목토론방 센티먼트 (시장 심리 핵심 지표)
 ✅ KIS REST/WebSocket 주가 데이터 (실시간 + 과거)
 ✅ FinanceDataReader 장기 히스토리 (이평선 + 백테스트)
 🔲 KRX 공매도/수급 데이터 (기관·외국인 동향 = 주가 선행 지표)
 🔲 네이버 투자자별 매매동향 (외국인·기관 순매수 → Quant 점수 보정)

[중간 우선순위] ──────────────────────────────────────────────────
 🔲 한국 정책 RSS (기재부·산자부·금융위 보도자료 → RAG 임베딩)
 🔲 한국은행 ECOS 거시경제 (금리·환율 → 산업별 영향도 분석)
 🔲 FRED 미국 금리/FOMC (외국인 매매 방향 예측)
 🔲 증권사 텔레그램 채널 (리포트 실시간 알림 → RAG 최신성 강화)

[낮은 우선순위] ──────────────────────────────────────────────────
 🔲 중국/EU/일본 정책 (업종 한정, 웹 검색으로 대체 가능)
 🔲 38커뮤니케이션 IPO 정보 (특수 이벤트)
 🔲 RSS 뉴스 피드 (네이버 뉴스와 중복 가능)
```

### 수집 파이프라인 CLI

```bash
# 종목별 전체 데이터 수집 (뉴스 + 리포트 + 공시 + 토론방 + 주가)
python pipeline_runner.py ingest --code 005930 --name 삼성전자

# 여러 종목 일괄 수집
python pipeline_runner.py ingest-batch --codes 005930,000660,035420

# 수집 상태 확인
python pipeline_runner.py status
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

### 멀티 에이전트의 이점

기존 단일 LLM 모델은 오류(할루시네이션)에 취약합니다. HQA는 **서로 다른 전문성**을 가진 에이전트들이 상호 검증을 거치게 하여, AI의 독단적 판단 실수를 줄이고 **논리적 근거가 명확한 투자 지시서**를 생성합니다.

### LangGraph 워크플로우

```
START → [Analyst | Quant | Chartist] (병렬 Fan-out)
    ↓
Quality Gate (Fan-in)
    ├── 품질 D등급 + 재시도 가능 → Retry Research (피드백 루프)
    └── 통과 → Risk Manager → END (투자 지시서 생성)
```

- **AnalysisState** TypedDict로 타입 안전한 상태 관리
- 3개 에이전트가 동시 Fan-out → Quality Gate에서 Fan-in
- D등급 데이터 자동 감지 → 최대 1회 리서치 재시도
- `langgraph` 미설치 시 `ThreadPoolExecutor` 폴백 (Graceful Degradation)

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

## 📁 프로젝트 구조

```
HQA_Project/
├── main.py                  # CLI 엔트리포인트
├── pipeline_runner.py       # 데이터 파이프라인 CLI
│
├── backend/                 # ★ FastAPI 프로덕션 백엔드
│   ├── app.py               #   메인 앱 + 미들웨어
│   ├── config.py            #   환경 설정 (Pydantic Settings)
│   ├── api/
│   │   ├── schemas.py       #   요청/응답 스키마
│   │   ├── dependencies.py  #   의존성 주입
│   │   └── routes/
│   │       ├── health.py    #   헬스체크
│   │       ├── stocks.py    #   종목 검색 / 실시간 시세
│   │       ├── analysis.py  #   분석 요청/결과/SSE/대화
│   │       └── charts.py    #   차트 데이터 (WebSocket)
│   ├── services/            #   비즈니스 로직 (인메모리 + Celery)
│   ├── tasks/               #   Celery 백그라운드 태스크
│   ├── database/            #   SQLAlchemy 비동기 ORM
│   └── middleware/          #   Rate Limiting, 에러 핸들링
│
├── frontend/                # ★ Next.js 프론트엔드 (React/TypeScript)
│   └── src/
│       ├── app/             #   페이지 (검색→분석→결과)
│       ├── components/      #   차트 컴포넌트
│       └── lib/             #   API 클라이언트 (SSE)
│
├── src/                     # ★ 핵심 비즈니스 로직
│   ├── agents/              #   AI 에이전트 (LangGraph 워크플로우)
│   │   ├── graph.py         #   StateGraph 상태 머신
│   │   ├── supervisor.py    #   Supervisor (의도분석/라우팅)
│   │   ├── analyst.py       #   Analyst (Researcher+Strategist)
│   │   ├── researcher.py    #   Researcher (정보수집/Plan A→B)
│   │   ├── strategist.py    #   Strategist (헤게모니 분석)
│   │   ├── quant.py         #   Quant (재무 분석)
│   │   ├── chartist.py      #   Chartist (기술적 분석)
│   │   └── risk_manager.py  #   Risk Manager (최종 판단)
│   │
│   ├── rag/                 #   RAG 파이프라인
│   │   ├── retriever.py     #   Vector+BM25 → RRF → Rerank
│   │   ├── bm25_index.py    #   금융 특화 BM25 인덱스
│   │   ├── embeddings.py    #   Snowflake Arctic Korean
│   │   ├── vector_store.py  #   ChromaDB 벡터 저장소
│   │   ├── reranker.py      #   Qwen3-Reranker-0.6B
│   │   └── ocr_processor.py #   PaddleOCR-VL-1.5 문서 OCR
│   │
│   ├── data_pipeline/       #   데이터 수집/가공
│   │   ├── crawler.py       #   네이버 금융 리포트 크롤러
│   │   ├── news_crawler.py  #   네이버 뉴스 크롤러
│   │   ├── discussion_crawler.py # 네이버 종목토론방 크롤러 (센티먼트)
│   │   ├── dart_collector.py#   DART 공시 수집
│   │   ├── price_loader.py  #   주가 데이터 (FinanceDataReader)
│   │   ├── investor_flow.py #   투자자별 매매동향 (예정)
│   │   └── krx_data.py      #   KRX 공매도/수급 데이터 (예정)
│   │
│   ├── tools/               #   에이전트 도구
│   │   ├── realtime_tool.py #   KIS 실시간 시세 API
│   │   ├── finance_tool.py  #   재무 데이터/종목 매핑
│   │   ├── web_search_tool.py# 웹 검색 (Tavily/DuckDuckGo)
│   │   └── rag_tool.py      #   RAG 검색 도구
│   │
│   └── utils/               #   유틸리티
│       ├── memory.py        #   대화 메모리 (10턴, LRU)
│       ├── parallel.py      #   병렬 실행 (ThreadPoolExecutor)
│       └── kis_auth.py      #   KIS API 인증
│
├── dashboard/               # Streamlit 대시보드
├── alembic/                 # DB 마이그레이션
├── Dockerfile               # Docker 이미지
├── docker-compose.yml       # 프로덕션 스택
└── requirements.txt         # 의존성
```

---

## 🛡️ 리스크 관리 & 안전장치 (3중 방어막)

자동매매 시스템의 특성상, 외부 리스크에 대한 다층 방어 체계를 구축합니다.

| 방어 계층 | 메커니즘 | 상세 |
|----------|---------|------|
| **1. AI 판단 검증** | Risk Manager 에이전트 | 팩트 데이터(Quant 점수, Tool 결과)만 기반으로 판단, JSON 파싱 실패 시 예외 처리 |
| **2. 서킷 브레이커** | 하드코딩 제한 | 일일 최대 매수 금액 제한, 동일 종목 연속 주문 쿨다운, 손절 라인 고정 |
| **3. LLMOps 모니터링** | LangSmith 트레이싱 | 에이전트 사고 과정 실시간 추적, 토큰 사용량 하드캡, 이상 징후 즉각 중단 |
| **4. 인프라 장애 대응** | Fallback 시스템 | LLM API 타임아웃 → 신규 매수 중단 + 알림, WebSocket 끊김 → REST 폴링 전환 |

### 데이터 품질 관리

```
ResearchResult.evaluate_quality()
    → A등급 (80+): 확신 있는 톤으로 분석
    → B등급 (60+): 기본 분석 + 주의 문구
    → C등급 (40+): 1단계 하향 조정
    → D등급 (40-): 2단계 하향 + Quality Gate에서 자동 재시도
```

---

## 🚀 실행 방법

### 환경 설정

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. 환경 변수 설정
cp .env.example .env
# → GOOGLE_API_KEY (필수), KIS/DART/Tavily API 키 (선택) 입력
```

### CLI 모드

```bash
python main.py                    # 대화형 모드 (Supervisor 기반)
python main.py --stock 삼성전자    # 전체 분석 (LangGraph 워크플로우)
python main.py --quick 현대차     # 빠른 분석 (Quant + Chartist)
python main.py --price SK하이닉스  # 실시간 시세 조회
```

### 데이터 파이프라인

```bash
python pipeline_runner.py ingest --code 005930 --name 삼성전자    # 종목 데이터 수집
python pipeline_runner.py ingest-batch --codes 005930,000660      # 일괄 수집
python pipeline_runner.py index-pdf --path ./reports/sample.pdf   # PDF 인덱싱
python pipeline_runner.py search --query "삼성전자 반도체 실적"    # RAG 검색 테스트
python pipeline_runner.py status                                   # 저장소 상태 확인
```

### 웹 서비스

```bash
# 옵션 A: 로컬 개발
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000   # FastAPI 백엔드
cd frontend && npm install && npm run dev                       # Next.js 프론트엔드

# 옵션 B: Docker Compose (프로덕션)
docker-compose up -d          # API + Redis + PostgreSQL + Worker
docker-compose exec api alembic upgrade head  # DB 마이그레이션
```

### API 사용 예시

```bash
# 종목 검색
curl http://localhost:8000/api/v1/stocks/search?q=삼성전자

# 분석 요청 (비동기)
curl -X POST http://localhost:8000/api/v1/analysis \
  -H "Content-Type: application/json" \
  -d '{"stock_name": "삼성전자", "stock_code": "005930", "mode": "full"}'

# SSE 스트리밍 (에이전트별 진행 상황 실시간)
curl -N http://localhost:8000/api/v1/analysis/{task_id}/stream

# 대화형 분석
curl -X POST http://localhost:8000/api/v1/analysis/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "삼성전자 분석해줘"}'
```

---

## 📚 기술 스택

| 분류 | 기술 | 용도 |
|------|------|------|
| **언어** | Python 3.10+ | 비동기 처리, AI/ML 생태계 |
| **LLM** | Google Gemini 2.5 Flash | Instruct(빠른 분석) / Thinking(깊은 추론) |
| **오케스트레이션** | LangGraph | 상태 머신 워크플로우, 조건부 라우팅 |
| **문서 OCR** | PaddleOCR-VL-1.5 (0.9B) | PDF → Markdown 텍스트 변환 |
| **임베딩** | Snowflake Arctic Korean | 1024dim, 8192토큰, 한국어 최적화 |
| **벡터 DB** | ChromaDB | 벡터 유사도 검색 |
| **키워드 검색** | rank-bm25 | BM25 Okapi 키워드 매칭 (금융 특화) |
| **리랭커** | Qwen3-Reranker-0.6B | 검색 결과 재순위 |
| **웹 검색** | Tavily / DuckDuckGo | 실시간 뉴스/정보 (Plan A→B 폴백) |
| **주식 데이터** | 한국투자증권 REST API | 실시간 시세, 일봉/분봉, 매매 체결 |
| **백엔드** | FastAPI + Celery + Redis | 비동기 REST API, 백그라운드 태스크 |
| **프론트엔드** | Next.js + TailwindCSS | React SPA, SSE 스트리밍 |
| **DB** | PostgreSQL + SQLAlchemy Async | 사용자/분석/주문 데이터 |
| **컨테이너** | Docker Compose | 원클릭 프로덕션 배포 |
| **모니터링** | LangSmith | 에이전트 트레이싱, 토큰 비용 추적 |

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
| **v1.0** | 2026-03 | FastAPI REST API, Next.js 프론트엔드, Docker Compose, SSE 스트리밍, Celery Task Queue |
| **v0.4** | 2026-02 | LangGraph 상태 머신, Hybrid Search (BM25 + Vector), Quality Gate 피드백 루프 |
| **v0.3** | 2026-02 | 병렬 실행, 대화 메모리(10턴), Plan A→B 폴백, 품질 등급 시스템 |
| **v0.2** | 2026-02 | PaddleOCR-VL-1.5 전환, Qwen3 Reranker, Snowflake Arctic 임베딩 |
| **v0.1** | 2025-01 | 초기 MVP — KIS API 연동, Streamlit 대시보드, 기본 에이전트 구조 |

---

## 📄 라이선스

MIT License

---

**⚠️ 면책조항**: 이 프로젝트는 교육 및 연구 목적으로 개발되었습니다. 실제 투자 결정에 사용하기 전에 전문가와 상담하시기 바랍니다. 자동매매 기능은 초기 단계에서 모의투자 환경에서만 테스트됩니다.
