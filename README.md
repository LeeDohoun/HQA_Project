# HQA Project (Hegemony Quantitative Analyst)

> 🤖 **AI 멀티 에이전트 기반 한국 주식 분석 시스템**

## 📋 개요

HQA(Hegemony Quantitative Analyst)는 Google Gemini AI와 LangGraph를 활용한 멀티 에이전트 주식 분석 시스템입니다. 4개의 전문 에이전트가 협력하여 종합적인 투자 분석을 제공합니다.

### ✨ 주요 특징

- 🔄 **멀티 에이전트 아키텍처**: Supervisor가 조율하는 4개의 전문 에이전트
- 📊 **실시간 시세**: 한국투자증권 공식 REST API 연동
- 📝 **PaddleOCR-VL-1.5**: 0.9B VLM 기반 문서 OCR (표/차트/수식/도장 인식)
- 🧠 **RAG 기반 분석**: ChromaDB 벡터 스토어 + 텍스트 임베딩
- 💻 **다양한 인터페이스**: CLI + Streamlit 대시보드

## 🏗️ 시스템 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                          User Interface                          │
│                   (CLI / Streamlit Dashboard)                    │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                         🎯 Supervisor                            │
│              (자연어 의도 분석 및 에이전트 라우팅)                 │
└───────────────────────────────┬──────────────────────────────────┘
                                │
        ┌───────────────┬───────┴───────┬───────────────┐
        ▼               ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   Analyst     │ │    Quant      │ │   Chartist    │ │ Risk Manager  │
│  (분석가)     │ │  (퀀트)       │ │  (차티스트)   │ │ (리스크관리)  │
│               │ │               │ │               │ │               │
│ • 시장 조사   │ │ • 재무 분석   │ │ • 차트 분석   │ │ • 위험 평가   │
│ • 뉴스 검색   │ │ • 밸류에이션  │ │ • 기술적지표  │ │ • 포트폴리오  │
│ • 종합 리포트 │ │ • 재무제표    │ │ • 캔들 패턴   │ │ • 최종 의견   │
└───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘
        │               │               │               │
        └───────────────┴───────┬───────┴───────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                          🔧 Tools Layer                          │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  KIS API     │  RAG Tool    │ Chart Tools  │   Search Tool      │
│  (실시간)    │  (벡터검색)  │  (차트생성)  │   (웹검색)         │
└──────────────┴──────────────┴──────────────┴────────────────────┘
```

## 📁 프로젝트 구조

```
HQA_Project/
├── main.py                 # CLI 엔트리포인트
├── requirements.txt        # 패키지 의존성
├── README.md              # 프로젝트 문서
├── Report.md              # 분석 리포트 출력
│
├── dashboard/
│   └── app.py             # Streamlit 대시보드
│
└── src/
    ├── __init__.py        # 패키지 초기화
    │
    ├── agents/            # AI 에이전트
    │   ├── __init__.py
    │   ├── llm_config.py  # LLM 설정 (Gemini)
    │   ├── analyst.py     # Analyst 에이전트
    │   ├── quant.py       # Quant 에이전트
    │   ├── chartist.py    # Chartist 에이전트
    │   └── risk_manager.py # Risk Manager 에이전트
    │
    ├── data_pipeline/     # 데이터 수집
    │   ├── __init__.py
    │   ├── crawler.py     # 웹 크롤러 (뉴스/공시)
    │   └── price_loader.py # 가격 데이터 로더
    │
    ├── database/          # 데이터베이스
    │   └── vector_store.py # ChromaDB 벡터 스토어
    │
    ├── tools/             # 에이전트 도구
    │   ├── __init__.py
    │   ├── realtime_tool.py  # KIS 실시간 시세 API
    │   ├── finance_tool.py   # 주식 코드 매핑
    │   ├── charts_tools.py   # 차트 생성 도구
    │   ├── rag_tool.py       # RAG 검색 도구
    │   └── search_tool.py    # 웹 검색 도구
    │
    └── utils/             # 유틸리티
        ├── __init__.py
        └── kis_auth.py    # KIS API 인증 모듈
```

## 🚀 실행 방법

### 환경 설정

```bash
# 패키지 설치
pip install -r requirements.txt

# 환경 변수 설정 (.env 파일)
GOOGLE_API_KEY=your_gemini_api_key
KIS_APP_KEY=your_kis_app_key
KIS_APP_SECRET=your_kis_app_secret
KIS_ACCOUNT_NO=your_account_number
KIS_ACCOUNT_PROD_CODE=01
```

### CLI 모드

```bash
# 대화형 모드 (Supervisor 기반)
python main.py

# 주식 분석 모드
python main.py -s 삼성전자
python main.py --stock 005930

# 빠른 분석 모드
python main.py -q 현대차

# 실시간 시세 확인
python main.py -p SK하이닉스
python main.py --price 000660
```

### 대시보드 모드

```bash
# Streamlit 대시보드 실행
streamlit run dashboard/app.py
```

## 🤖 에이전트 상세

### Supervisor (조율자)
- **역할**: 사용자 의도 분석 및 적절한 에이전트로 라우팅
- **기능**: 자연어 질문 해석, 워크플로우 결정, 결과 통합

### Analyst (분석가)
- **역할**: 시장 조사 및 종합 리포트 작성
- **도구**: 웹 검색, RAG 검색, 뉴스 크롤링
- **출력**: 시장 동향, 기업 뉴스, 투자 환경 분석

### Quant (퀀트)
- **역할**: 재무 분석 및 밸류에이션
- **도구**: KIS API (재무제표), Finance Tool
- **출력**: PER/PBR/ROE 분석, 적정가치 산출, 재무 건전성 평가

### Chartist (차티스트)
- **역할**: 기술적 분석 및 차트 패턴 인식
- **도구**: KIS API (일봉/분봉), Chart Tools
- **출력**: 이동평균선 분석, RSI/MACD, 지지/저항선

### Risk Manager (리스크 관리자)
- **역할**: 위험 평가 및 최종 투자 의견 제시
- **도구**: 모든 에이전트 결과 종합
- **출력**: 리스크 점수, 투자등급, 포지션 사이징 권고

## 🔧 도구 (Tools)

### KIS Realtime Tool (`realtime_tool.py`)
한국투자증권 공식 REST API를 활용한 실시간 시세 조회

| 함수 | API ID | 설명 |
|------|--------|------|
| `inquire_price()` | FHKST01010100 | 현재가 조회 |
| `inquire_asking_price()` | FHKST01010200 | 호가 조회 |
| `inquire_daily_price()` | FHKST01010400 | 일봉 데이터 |
| `inquire_time_itemchartprice()` | FHKST03010200 | 분봉 데이터 |
| `inquire_ccnl()` | FHKST01010300 | 체결 내역 |

### Finance Tool (`finance_tool.py`)
종목 코드/이름 매핑 및 유효성 검증

| 함수 | 설명 |
|------|------|
| `StockMapper.load_from_kis_master()` | KIS 마스터 파일 로드 |
| `StockMapper.get_code()` | 종목명 → 코드 변환 |
| `StockMapper.get_name()` | 코드 → 종목명 변환 |
| `StockMapper.search()` | 종목 검색 |

### KIS Auth (`kis_auth.py`)
한국투자증권 API 인증 및 토큰 관리

| 클래스/함수 | 설명 |
|-------------|------|
| `KISConfig` | API 설정 (도메인, 키, 계좌) |
| `KISToken` | 토큰 발급/캐싱/갱신 |
| `call_api()` | 공통 REST API 호출 |
| `get_base_headers()` | 인증 헤더 생성 |

## 📚 기술 스택

| 분류 | 기술 |
|------|------|
| **LLM** | Google Gemini 2.5 Flash (Lite/Preview) |
| **에이전트 프레임워크** | LangGraph, LangChain |
| **문서 OCR** | PaddleOCR-VL-1.5 (0.9B VLM) |
| **벡터 DB** | ChromaDB + 텍스트 임베딩 |
| **주식 데이터** | 한국투자증권 공식 REST API |
| **대시보드** | Streamlit + Plotly |
| **웹 검색** | Tavily API |

## ⚙️ API 설정

### 한국투자증권 API
1. [KIS Developers](https://apiportal.koreainvestment.com/) 가입
2. 앱 생성 후 APP KEY, APP SECRET 발급
3. `.env` 파일에 설정

```env
KIS_APP_KEY=PSxxxxxxxxxxx
KIS_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KIS_ACCOUNT_NO=12345678-01
KIS_ACCOUNT_PROD_CODE=01
```

### Google Gemini API
1. [Google AI Studio](https://aistudio.google.com/) 접속
2. API Key 발급
3. `.env` 파일에 설정

```env
GOOGLE_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### PaddleOCR-VL-1.5 설치 (문서 OCR)

```bash
# GPU 버전 (CUDA 12.6 기준)
pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/

# CPU 버전
pip install paddlepaddle==3.2.1

# PaddleOCR 설치
pip install -U "paddleocr[doc-parser]"
```

> **참고**: PaddleOCR-VL-1.5는 requirements.txt와 별도로 설치해야 합니다.
> 공식 문서: https://www.paddleocr.ai/

## 📝 변경 이력

### v0.2.0 (2026-02) - PaddleOCR-VL 전환

#### 🆕 새로 생성된 파일
- `src/rag/ocr_processor.py` - PaddleOCR-VL-1.5 기반 OCR 프로세서

#### 🔄 주요 변경사항

**문서 처리 방식 전환: Qwen3-VL → PaddleOCR-VL-1.5**

| 항목 | 이전 (v0.1.0) | 현재 (v0.2.0) |
|------|--------------|---------------|
| OCR 엔진 | PyMuPDF 텍스트 추출 | PaddleOCR-VL-1.5 |
| 임베딩 | Qwen3-VL 멀티모달 (2B~8B) | 텍스트 임베딩 (경량) |
| 표/차트 인식 | 이미지로 처리 | VLM이 직접 인식 |
| 모델 크기 | 2B~8B | 0.9B |
| 출력 형식 | 텍스트/이미지 혼합 | Markdown 구조화 |

**변경된 파일**:
- `src/rag/document_loader.py` - PaddleOCR-VL 기반으로 재작성
- `src/rag/__init__.py` - OCR 프로세서 export 추가
- `requirements.txt` - PaddleOCR 설치 가이드 추가

**PaddleOCR-VL-1.5 특징**:
- ✅ 0.9B 경량 모델로 빠른 추론
- ✅ 표, 차트, 수식, 도장 인식 SOTA
- ✅ 스캔/기울기/왜곡/조명 왜곡 강건
- ✅ Markdown 구조화 출력
- ✅ 다국어 지원 (영/중/한 등)

---

### v0.1.0 (2025-01)

#### 🆕 새로 생성된 파일
- `src/utils/kis_auth.py` - KIS API 인증 모듈
- `dashboard/app.py` - Streamlit 웹 대시보드

#### 🔄 수정된 파일

**`src/tools/realtime_tool.py`**
- 비공식 `mojito2` 라이브러리 → 공식 KIS REST API로 전면 재작성
- 새로운 함수: `inquire_price()`, `inquire_asking_price()`, `inquire_daily_price()`, `inquire_time_itemchartprice()`, `inquire_ccnl()`
- 데이터 클래스: `StockPrice`, `OrderBook`, `OHLCV`, `TradeRecord`
- 하위 호환성 유지: `RealtimeQuote = StockPrice` 별칭

**`src/tools/finance_tool.py`**
- `import yfinance as yf` 제거 (requirements에서 삭제됨)
- 한국 주식 전용으로 정리

**`main.py`**
- argparse 기반 CLI 인터페이스로 전면 재설계
- 4가지 실행 모드: 대화형, 주식분석(-s), 빠른분석(-q), 시세확인(-p)
- 빠른 시작을 위한 lazy import 적용

**`requirements.txt`**
- `mojito2>=0.2.0` 제거
- `streamlit>=1.30.0`, `plotly>=5.18.0` 활성화
- KIS 공식 API 직접 사용 주석 추가

**`src/__init__.py`**
- 패키지 설명 문서화 추가

**`src/utils/__init__.py`**
- `kis_auth` 모듈 export 추가

## 📄 라이선스

MIT License

## 👥 기여

이슈 및 PR 환영합니다!

---

**⚠️ 면책조항**: 이 프로젝트는 교육 및 연구 목적으로 개발되었습니다. 실제 투자 결정에 사용하기 전에 전문가와 상담하시기 바랍니다.
