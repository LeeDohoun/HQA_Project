# HQA Project -> DATA PIPELINE

테마 기반 주식 데이터 수집 및 하이브리드 검색 인프라

## 1. 소개

현재 구조는 다음 두 계층을 중심으로 구성됩니다.

- **Layer 1: Data & Integration**
- **Layer 2: RAG & Storage**

즉, 뉴스·공시·포럼·차트·시세 데이터를 모아 표준화하고,  
이후 멀티에이전트 분석에 활용할 수 있도록 기반 인프라를 구축하는 것이 목적.

---

## 2. 핵심 구조

```text
CollectRequest
→ IngestionService
→ Source Collectors
   - Theme
   - News
   - DART
   - Forum
   - KIS Chart
   - KIS Quote
→ Raw Storage
→ Corpus / Market Data Build
→ BM25 / Vector Build
→ RetrievalService
→ Period RAG Rebuild
```

---

## 3. 디렉토리 구조

```text
HQA_Project/
├── src/
│   ├── ingestion/
│   │   ├── types.py
│   │   ├── base.py
│   │   ├── services.py
│   │   ├── naver_theme.py
│   │   ├── naver_news.py
│   │   ├── dart.py
│   │   ├── naver_forum.py
│   │   ├── kis_client.py
│   │   ├── kis_chart.py
│   │   └── kis_quote.py
│   │
│   ├── storage/
│   │   ├── raw_storage.py
│   │   ├── corpus_builder.py
│   │   └── market_data_storage.py
│   │
│   ├── retrieval/
│   │   ├── bm25_index.py
│   │   ├── vector_store.py
│   │   └── services.py
│   │
│   └── rebuild/
│       └── build_period_rag.py
│
├── data/
│   ├── raw/
│   ├── corpora/
│   ├── market_data/
│   ├── vector_stores/
│   └── period_rag/
│
├── scripts_rag_pipeline.py
├── build_period_rag.py
└── README.md
```

---

## 4. Layer 1: Data & Integration

Layer 1은 외부 데이터 소스로부터 데이터를 수집하고, 내부 표준 구조로 변환한 뒤 raw 저장소에 적재하는 계층입니다.

### 수집 대상
- 네이버 테마 종목
- 네이버 뉴스
- DART 공시
- 네이버 종토방
- KIS 차트 데이터
- KIS 시세 데이터

### 주요 구성요소

#### `StockTarget`
수집 대상 종목 정보

- `stock_name`
- `stock_code`
- `corp_code`

#### `DocumentRecord`
문서형 데이터 구조

대상:
- 뉴스
- 공시
- 포럼

주요 필드:
- `source_type`
- `title`
- `content`
- `url`
- `stock_name`
- `stock_code`
- `published_at`
- `metadata`

#### `MarketRecord`
시계열형 데이터 구조

대상:
- 차트
- 시세
- 거래량

주요 필드:
- `source_type`
- `stock_name`
- `stock_code`
- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `metadata`

#### `CollectRequest`
수집 요청 입력 구조

예:
- `theme`
- `from_date`
- `to_date`
- `enabled_sources`
- `max_news`
- `forum_pages`

#### `IngestionService`
Layer 1의 오케스트레이터

역할:
- 입력 요청 해석
- 대상 종목 결정
- source별 collector 실행
- 결과 표준화
- raw 저장
- 수집 결과 리포트 생성

---

## 5. Raw Storage

수집 직후 데이터는 먼저 raw 저장소에 적재됩니다.

```text
data/raw/
├── theme_targets/
├── news/
├── dart/
├── forum/
├── chart/
└── quote/
```

### 장점
- 원본 데이터 보존
- 재수집 없이 재빌드 가능
- 품질 검증 및 디버깅 가능
- Layer 1과 Layer 2 분리 가능

---

## 6. Layer 2: RAG & Storage

Layer 2는 raw 데이터를 읽어 검색·분석 가능한 구조로 변환하는 계층입니다.

### 주요 역할
- 문서형 corpus 생성
- 시계열형 market data 저장
- BM25 인덱스 구축
- Vector Store 구축
- 하이브리드 검색 제공
- 기간 기반 RAG 재구성

---

## 7. 데이터 저장 구조

### 문서형 데이터
뉴스, 공시, 포럼 등 텍스트 데이터

```text
data/corpora/
```

### 시계열형 데이터
차트, 시세, 거래량 등 정형 데이터

```text
data/market_data/
```

즉,
- **문서형 데이터**는 RAG 검색용
- **시계열형 데이터**는 차트/정량 분석용

으로 분리됩니다.

---

## 8. RetrievalService

`RetrievalService`는 Layer 2의 통합 검색 진입점입니다.

### 기능
- Vector Search
- BM25 Search
- Hybrid Merge
- source_type 필터링
- 기간별 RAG 검색 연계

즉 외부에서는 BM25와 Vector Store를 직접 다루지 않고,  
항상 `RetrievalService`를 통해 검색합니다.

---

## 9. Dedupe 규칙

소스별 중복 제거 기준은 다음과 같습니다.

- `news`: URL
- `dart`: `rcept_no`
- `forum`: `stock_code + title + published_at`
- `chart`: `stock_code + timestamp + frequency`

이 규칙은 corpus 생성, 저장, 재구성 과정에서 공통 사용됩니다.

---

## 10. 실행 예시

### 데이터 수집

```bash
python scripts_rag_pipeline.py \
  --theme "풍력에너지" \
  --from-date 20240101 \
  --to-date 20241231 \
  --theme-max-stocks 20 \
  --max-news 100 \
  --forum-pages 500 \
  --enabled-sources news,dart,forum,chart,quote \
  --update-mode append-new-stocks
```

### 기간 RAG 재구성

```bash
python build_period_rag.py \
  --data-dir ./data \
  --theme-key 풍력에너지 \
  --from-date 20240101 \
  --to-date 20241231 \
  --output-name wind_2024 \
  --build-vector
```

---

## 11. 구조적 장점

- 수집과 검색 계층이 분리됨
- 문서형 데이터와 시계열형 데이터가 분리됨
- 새로운 데이터 소스 추가가 쉬움
- 이후 멀티에이전트 구조로 확장 가능
- 기간별 재구성이 가능해 분석 유연성이 높음

---

## 12. 향후 확장 방향

현재는 Layer 1과 Layer 2를 중심으로 구현되어 있으며,  
이후 다음 계층으로 확장할 수 있습니다.

- Analyst / Quant / Chartist Agent
- Quality Gate
- Risk Manager
- FastAPI / Streamlit / Next.js
- chart signal / quant feature 생성 파이프라인

---

## 13. 요약

HQA Project는 주식 분석에 필요한 비정형·정형 데이터를 통합 수집하고,  
이를 raw 저장소, 문서형 corpus, 시계열 market data, 하이브리드 검색 구조로 분리하여  
이후 멀티에이전트 분석의 기반이 되는 Layer 1·2 인프라를 제공하는 프로젝트입니다.
