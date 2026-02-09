# HQA Project (Hegemony Quantitative Analyst)

> 🤖 **AI 멀티 에이전트 기반 한국 주식 분석 시스템**

## 📋 개요

HQA(Hegemony Quantitative Analyst)는 Google Gemini AI와 LangGraph를 활용한 멀티 에이전트 주식 분석 시스템입니다. 6개의 전문 에이전트가 협력하여 기업의 **헤게모니(시장 지배력)** 를 중심으로 종합적인 투자 분석을 제공합니다.

### ✨ 주요 특징

- 🔄 **LangGraph 상태 머신**: Supervisor가 조율하는 6개 전문 에이전트 (병렬 실행 + 품질 게이트 + 피드백 루프)
- 🔍 **Hybrid Search**: BM25 키워드 검색 + Vector 의미 검색 → RRF 병합 → Qwen3 리랭킹
- 📊 **실시간 시세**: 한국투자증권 공식 REST API 연동
- 📝 **PaddleOCR-VL-1.5**: 0.9B VLM 기반 문서 OCR (표/차트/수식/도장 인식)
- 🧠 **RAG 기반 분석**: ChromaDB + Snowflake Arctic Korean 임베딩 (1024차원)
- ⚡ **병렬 실행**: ThreadPoolExecutor로 Analyst/Quant/Chartist 동시 실행
- 💾 **대화 메모리**: 10턴 컨텍스트 유지, 후속 질문 자동 감지
- 🛡️ **데이터 품질 관리**: Plan A→B 폴백, 품질 등급(A~D) 기반 행동 강령
- 💻 **다양한 인터페이스**: CLI + Streamlit 대시보드

## 🏗️ 시스템 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                          User Interface                          │
│               (CLI / Streamlit Dashboard / Chat)                 │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                    🎯 Supervisor (Instruct)                      │
│          자연어 의도 분석 · 라우팅 · 대화 메모리(10턴)           │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                    ┌───── LangGraph 워크플로우 ─────┐
                    │                                 │
        ┌───────────┼───────────────┐                 │
        ▼           ▼               ▼                 │
┌───────────┐ ┌───────────┐ ┌───────────┐            │
│  Analyst  │ │   Quant   │ │ Chartist  │  ← 병렬    │
│ (Instruct │ │ (Instruct)│ │ (Instruct)│    실행    │
│ +Thinking)│ │           │ │           │            │
├───────────┤ ├───────────┤ ├───────────┤            │
│Researcher │ │재무 크롤링│ │기술적 지표│            │
│→Strategist│ │→웹 폴백   │ │RSI/MACD/BB│            │
│(Plan A→B) │ │PER/PBR/ROE│ │Trend/Vol  │            │
└─────┬─────┘ └─────┬─────┘ └─────┬─────┘            │
      │             │             │                   │
      ▼             ▼             ▼                   │
┌──────────────────────────────────────────┐          │
│          🔍 Quality Gate                 │          │
│    품질 D등급 → RetryResearch (최대 1회) │          │
└─────────────────────┬────────────────────┘          │
                      ▼                               │
┌──────────────────────────────────────────┐          │
│         🎯 Risk Manager (Thinking)       │          │
│      3개 점수 종합 → 최종 투자 판단       │          │
└──────────────────────────────────────────┘          │
                    └─────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                          🔧 Tools Layer                          │
├──────────────┬───────────────┬──────────────┬───────────────────┤
│  KIS API     │  RAG Tool     │ Chart Tools  │  Search Tool      │
│  (실시간)    │ (Hybrid검색)  │  (차트생성)  │  (Tavily/DDG)     │
└──────────────┴───────────────┴──────────────┴───────────────────┘
                                │
┌──────────────────────────────────────────────────────────────────┐
│                      💾 Storage Layer                            │
├────────────────────┬─────────────────┬──────────────────────────┤
│   ChromaDB         │  BM25 Index     │  대화 메모리             │
│  (벡터 저장소)     │ (키워드 검색)   │  (10턴 + LRU 캐시)      │
└────────────────────┴─────────────────┴──────────────────────────┘
```

### Hybrid Search 파이프라인

```
Query ──┬── Vector Search (Snowflake Arctic, k=20) ──┐
        │                                             ├── RRF 병합 ── Qwen3 Rerank ── Top 3
        └── BM25 Keyword Search (k=20) ──────────────┘
```

### LangGraph 워크플로우

```
START → [Analyst | Quant | Chartist] (병렬 Fan-out)
    ↓
Quality Gate (Fan-in)
    ├── 품질 D등급 + 재시도 가능 → Retry Research → Quality Gate (피드백 루프)
    └── 통과 → Risk Manager → END
```

## 📁 프로젝트 구조

```
HQA_Project/
├── main.py                     # CLI 엔트리포인트
├── pipeline_runner.py          # 데이터 파이프라인 CLI
├── requirements.txt            # 패키지 의존성
├── README.md                   # 프로젝트 문서
├── Report.md                   # 분석 리포트 출력
│
├── dashboard/
│   └── app.py                  # Streamlit 대시보드
│
└── src/
    ├── __init__.py
    │
    ├── agents/                 # AI 에이전트
    │   ├── __init__.py
    │   ├── llm_config.py       # LLM 설정 (Gemini Instruct/Thinking/Vision)
    │   ├── supervisor.py       # Supervisor: 쿼리 분석 · 라우팅 · 메모리
    │   ├── analyst.py          # Analyst: Researcher + Strategist 통합 래퍼
    │   ├── researcher.py       # Researcher (Instruct): 정보 수집 · Plan A↔B 폴백
    │   ├── strategist.py       # Strategist (Thinking): 헤게모니 판단 · 품질 행동강령
    │   ├── quant.py            # Quant (Instruct): 재무 분석 · 웹 폴백
    │   ├── chartist.py         # Chartist (Instruct): 기술적 분석
    │   ├── risk_manager.py     # Risk Manager (Thinking): 최종 투자 판단
    │   └── graph.py            # LangGraph 상태 머신 워크플로우
    │
    ├── rag/                    # RAG 파이프라인
    │   ├── __init__.py
    │   ├── ocr_processor.py    # PaddleOCR-VL-1.5 문서 OCR
    │   ├── document_loader.py  # 문서 로딩 및 전처리
    │   ├── text_splitter.py    # 텍스트 청킹 (1000자/200 오버랩)
    │   ├── embeddings.py       # Snowflake Arctic Korean 임베딩 (1024dim)
    │   ├── vector_store.py     # ChromaDB 벡터 저장소
    │   ├── bm25_index.py       # BM25 키워드 검색 인덱스 (Hybrid Search)
    │   ├── retriever.py        # 통합 검색기 (Vector + BM25 + Rerank)
    │   └── reranker.py         # Qwen3-Reranker-0.6B 리랭커
    │
    ├── data_pipeline/          # 데이터 수집/가공
    │   ├── __init__.py
    │   ├── data_ingestion.py   # 텍스트 전용 RAG 인제스션
    │   ├── crawler.py          # 웹 크롤러 (네이버 금융)
    │   ├── dart_collector.py   # DART 공시 수집
    │   ├── news_crawler.py     # 뉴스 크롤러
    │   └── price_loader.py     # 가격 데이터 로더
    │
    ├── database/               # 데이터베이스
    │   ├── raw_data_store.py   # 원시 데이터 저장
    │   └── vector_store.py     # 레거시 벡터 스토어
    │
    ├── tools/                  # 에이전트 도구
    │   ├── __init__.py
    │   ├── realtime_tool.py    # KIS 실시간 시세 API
    │   ├── web_search_tool.py  # 웹 검색 (Tavily/DuckDuckGo)
    │   ├── finance_tool.py     # 종목 코드 매핑
    │   ├── charts_tools.py     # 차트 생성 도구
    │   ├── rag_tool.py         # RAG 검색 도구
    │   └── search_tool.py      # 검색 도구
    │
    └── utils/                  # 유틸리티
        ├── __init__.py
        ├── stock_mapper.py     # 종목명 ↔ 코드 매핑
        ├── memory.py           # 대화 메모리 (10턴, LRU 캐시)
        ├── parallel.py         # 병렬 실행 (ThreadPoolExecutor)
        └── kis_auth.py         # KIS API 인증 모듈
```

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
# PaddleOCR 설치
pip install -U "paddleocr[doc-parser]"
```

```env
# .env 파일 설정
GOOGLE_API_KEY=your_gemini_api_key          # 필수
DART_API_KEY=your_dart_api_key              # DART 공시 (선택)
TAVILY_API_KEY=your_tavily_api_key          # 웹 검색 (선택)
KIS_APP_KEY=your_kis_app_key                # 실시간 시세 (선택)
KIS_APP_SECRET=your_kis_app_secret
KIS_ACCOUNT_NO=your_account_number
KIS_ACCOUNT_PROD_CODE=01
```

### CLI 모드

```bash
# 대화형 모드 (Supervisor 기반, 메모리 유지)
python main.py

# 종목 분석 (전체 — LangGraph 워크플로우)
python main.py -s 삼성전자
python main.py --stock 005930

# 빠른 분석 (Quant + Chartist만)
python main.py -q 현대차

# 실시간 시세
python main.py -p SK하이닉스
python main.py --price 000660
```

### 데이터 파이프라인

```bash
# PDF 문서 인덱싱
python pipeline_runner.py ingest report.pdf --stock-code 005930

# 디렉토리 일괄 인덱싱
python pipeline_runner.py index-dir ./reports/ --stock-code 005930

# RAG 검색 테스트
python pipeline_runner.py search "삼성전자 반도체 실적"

# 인덱스 상태 확인
python pipeline_runner.py status
```

### 대시보드 모드

```bash
streamlit run dashboard/app.py
```

## 🤖 에이전트 상세

### Supervisor (조율자) — `Instruct`
- **역할**: 사용자 의도 분석 · 에이전트 라우팅 · 대화 메모리 관리
- **기능**: 규칙 기반 빠른 분석 → LLM 상세 분석 (2단계)
- **의도 분류**: 종목분석 · 빠른분석 · 산업분석 · 이슈분석 · 시세조회 · 종목비교 · 테마탐색 · 일반QA
- **메모리**: 최근 10턴 대화 컨텍스트 유지, 후속 질문 자동 감지 ("그럼 하이닉스는?")

### Analyst (분석가) — `Instruct` + `Thinking`
내부적으로 두 개의 하위 에이전트로 구성:

#### Researcher (Instruct — 빠른 수집)
- **역할**: 정보 수집 · 요약 · 품질 평가
- **전략**: 모든 검색에 Plan A → Plan B 폴백
  - 리포트: RAG → 웹검색 / 뉴스: 웹검색 → RAG / 정책: 웹검색 → RAG / 산업: RAG → 웹검색
- **품질 관리**: `ResearchResult.evaluate_quality()` → A/B/C/D 등급, 경고 목록
- **출력**: `ResearchResult` (report_summary, news, policy, industry, 품질 점수)

#### Strategist (Thinking — 깊은 추론)
- **역할**: 헤게모니(시장 지배력) 분석 · 독점력/성장성 평가
- **행동 강령**: 데이터 품질 등급별 분석 톤 조정
  - D등급: 2단계 하향, C등급: 1단계 하향, B등급: 기본+주의 문구, A등급: 확신 있는 톤
- **출력**: `HegemonyScore` (moat 0~40 + growth 0~30 = 총 0~70점)

### Quant (퀀트) — `Instruct`
- **역할**: 재무 지표 분석 · 밸류에이션
- **전략**: Plan A (네이버 금융 크롤링) → Plan B (웹 검색 + LLM JSON 추출)
- **지표**: PER/PBR/ROE/부채비율 등
- **출력**: `QuantScore` (valuation/profitability/growth/stability 각 0~25, 총 0~100점)

### Chartist (차티스트) — `Instruct`
- **역할**: 기술적 분석 (차트 지표 기반)
- **지표**: RSI, MACD, Bollinger Band, 이동평균선, 거래량
- **출력**: `ChartistScore` (trend/momentum/volatility/volume, 총 0~100점)

### Risk Manager (리스크 관리자) — `Thinking`
- **역할**: 3개 에이전트 결과 종합 → 최종 투자 판단
- **입력**: `AgentScores` (Analyst 70 + Quant 100 + Chartist 100 = 270점 만점)
- **출력**: `FinalDecision` (투자 행동, 리스크 레벨, 목표가, 손절가, 포지션 사이징)

## 🔍 Hybrid Search (BM25 + Vector)

벡터 검색만으로는 금융 용어(PER, EBITDA, YOY 등)나 숫자 매칭이 약합니다.
BM25 키워드 검색을 추가하여 두 검색 결과를 Reciprocal Rank Fusion(RRF)으로 병합합니다.

| 검색 방식 | 강점 | 예시 |
|-----------|------|------|
| **Vector (의미)** | 유의어·문맥 이해 | "수익성 좋은 기업" ↔ "ROE 높은 종목" |
| **BM25 (키워드)** | 정확한 용어 매칭 | "PER 12.5배", "EBITDA 3조" |
| **Hybrid (RRF)** | 둘 다 결합 | 의미적 유사도 + 키워드 정확도 |

### 토크나이저 특징 (금융 특화)
- 금융 약어 보호: `PER`, `ROE`, `EBITDA`, `EV/EBITDA` 등
- 숫자+단위 패턴: `12.5배`, `3.2%`, `1,200억`
- 한글 형태소 분리 (형태소 분석기 불필요)
- 종목코드 추출: 4자리 이상 숫자

## ⚡ LangGraph 워크플로우

기존의 순차적 에이전트 호출을 LangGraph 상태 머신으로 대체합니다.

### 핵심 개선점
| 기능 | 기존 (v0.3) | LangGraph (v0.4) |
|------|-------------|-------------------|
| **실행 방식** | ThreadPoolExecutor 병렬 | StateGraph Fan-out/Fan-in |
| **에러 복구** | try/except 기본값 | 노드별 독립 에러 처리 |
| **품질 관리** | 없음 | Quality Gate + 재시도 루프 |
| **데이터 흐름** | 함수 리턴값 전달 | `AnalysisState` TypedDict |
| **확장성** | 코드 수정 필요 | 노드/엣지 추가로 확장 |

### Graceful Degradation
- `langgraph` 미설치 → 자동으로 기존 병렬 실행 방식 폴백
- `rank-bm25` 미설치 → 자동으로 벡터 검색만 사용
- 각 에이전트 오류 → 기본값으로 대체 후 계속 진행

## 📚 기술 스택

| 분류 | 기술 | 용도 |
|------|------|------|
| **LLM (Instruct)** | Gemini 2.5 Flash Lite | Supervisor, Researcher, Quant, Chartist |
| **LLM (Thinking)** | Gemini 2.5 Flash Preview | Strategist, Risk Manager |
| **오케스트레이션** | LangGraph (선택) | 상태 머신 워크플로우, 조건부 라우팅 |
| **병렬 실행** | ThreadPoolExecutor (폴백) | 에이전트 동시 실행 |
| **문서 OCR** | PaddleOCR-VL-1.5 (0.9B) | PDF → Markdown 텍스트 변환 |
| **임베딩** | Snowflake Arctic Korean | 1024차원, 8192토큰, 한국어 최적화 |
| **벡터 DB** | ChromaDB | 벡터 유사도 검색 |
| **키워드 검색** | rank-bm25 (선택) | BM25 Okapi 키워드 매칭 |
| **리랭커** | Qwen3-Reranker-0.6B | 검색 결과 재순위 |
| **결과 병합** | RRF (Reciprocal Rank Fusion) | Vector + BM25 점수 통합 |
| **웹 검색** | Tavily (1차) / DuckDuckGo (2차) | 실시간 뉴스/정보 |
| **주식 데이터** | 한국투자증권 REST API | 실시간 시세, 일봉/분봉 |
| **재무 크롤링** | 네이버 금융 + 웹 폴백 | PER/PBR/ROE 수집 |
| **대시보드** | Streamlit + Plotly | 웹 UI, 인터랙티브 차트 |
| **대화 메모리** | ConversationMemory | 10턴 히스토리, LRU 캐시 |

## ⚙️ API 설정

### Google Gemini API (필수)
1. [Google AI Studio](https://aistudio.google.com/) 접속
2. API Key 발급
3. `.env` 파일에 설정

```env
GOOGLE_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 한국투자증권 API (선택 — 실시간 시세)
1. [KIS Developers](https://apiportal.koreainvestment.com/) 가입
2. 앱 생성 후 APP KEY, APP SECRET 발급

```env
KIS_APP_KEY=PSxxxxxxxxxxx
KIS_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_ACCOUNT_NO=12345678-01
KIS_ACCOUNT_PROD_CODE=01
```

### Tavily API (선택 — 웹 검색)
1. [Tavily](https://tavily.com/) 가입 (월 1,000건 무료)

```env
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### DART API (선택 — 공시)
1. [DART](https://opendart.fss.or.kr/) 가입

```env
DART_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## 📝 변경 이력

### v0.4.0 (2026-02) - LangGraph + Hybrid Search

#### 🆕 새로 생성된 파일
- `src/agents/graph.py` — LangGraph 상태 머신 워크플로우
- `src/rag/bm25_index.py` — BM25 키워드 검색 인덱스 (금융 특화 토크나이저)

#### 🔄 주요 변경사항

**LangGraph 워크플로우**
- `SupervisorAgent._execute_stock_analysis()` → `run_stock_analysis()` 위임
- `AnalysisState` TypedDict로 상태 관리
- 5개 노드: analyst, quant, chartist, quality_gate, risk_manager
- 조건부 엣지: 품질 D등급 → retry_research 피드백 루프
- `langgraph` 미설치 시 `_fallback_parallel_analysis()` 자동 폴백

**Hybrid Search (BM25 + Vector)**
- `RAGRetriever`에 `use_hybrid`, `bm25_weight`, `vector_weight` 파라미터 추가
- 문서 인덱싱 시 ChromaDB + BM25 인덱스 동시 업데이트
- `retrieve()`: Vector + BM25 → RRF 병합 → Qwen3 리랭킹
- `rank-bm25` 미설치 시 벡터 검색만 사용 (graceful degradation)
- BM25 인덱스 JSON 영속화

**변경된 파일**:
- `src/agents/supervisor.py` — LangGraph 통합, `_execute_stock_analysis()` 대체
- `src/agents/__init__.py` — graph 모듈 export 추가
- `src/rag/retriever.py` — Hybrid Search 통합, BM25 인덱스 연동
- `src/rag/__init__.py` — BM25 모듈 export 추가
- `main.py` — `_run_full_analysis()` → LangGraph 워크플로우 사용
- `dashboard/app.py` — 전체 분석 → LangGraph 워크플로우 사용
- `requirements.txt` — `langgraph>=0.2.0`, `rank-bm25>=0.2.2` 추가

---

### v0.3.0 (2026-02) - 병렬 실행 · 메모리 · 품질 관리

#### 🆕 새로 생성된 파일
- `src/utils/parallel.py` — ThreadPoolExecutor 병렬 실행 유틸리티
- `src/utils/memory.py` — ConversationMemory (10턴, LRU 캐시)

#### 🔄 주요 변경사항

**에이전트 개선**
- `Researcher`: 모든 검색 메서드에 Plan A → Plan B 폴백 로직 추가
- `ResearchResult`: 품질 평가 시스템 (`evaluate_quality()`, A~D 등급)
- `Strategist`: 데이터 품질 등급별 행동 강령 (D: 2단계 하향, A: 확신)
- `Quant`: 네이버 금융 크롤링 → 웹 검색 폴백 (`_web_search_fallback`)

**병렬 실행**
- Analyst + Quant + Chartist 동시 실행 (ThreadPoolExecutor)
- 에이전트 오류 시 기본값 대체 후 계속 진행

**대화 메모리**
- SupervisorAgent에 `ConversationMemory` 통합
- 후속 질문 자동 감지 ("그럼", "그것은", "아까" 등)
- 분석 결과 LRU 캐시

---

### v0.2.0 (2026-02) — PaddleOCR-VL 전환

- PaddleOCR-VL-1.5로 문서 OCR 전환 (0.9B 경량 VLM)
- 멀티모달 임베딩 제거, 텍스트 전용 RAG
- Qwen3-Reranker-0.6B 리랭킹 추가
- Snowflake Arctic Korean 임베딩 (1024dim, 8192 tokens)

---

### v0.1.0 (2025-01) — 초기 버전

- KIS REST API 연동 (실시간 시세)
- Streamlit 대시보드
- 기본 멀티 에이전트 구조

## 📄 라이선스

MIT License

## 👥 기여

이슈 및 PR 환영합니다!

---

**⚠️ 면책조항**: 이 프로젝트는 교육 및 연구 목적으로 개발되었습니다. 실제 투자 결정에 사용하기 전에 전문가와 상담하시기 바랍니다.
