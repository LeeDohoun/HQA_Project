# HQA Project 상태 정리

## 1. 이 문서의 목적

이 문서는 현재 `HQA_Project`에서 확인된 문제점, 그 원인, 지금까지 적용된 해결 방식, 그리고 전체 구조를 한 번에 이해할 수 있도록 정리한 문서다.  
코드 파일을 처음 열었을 때도 바로 맥락을 잡을 수 있도록, 주요 Python 파일 상단에는 구조 설명 주석도 함께 추가했다.

---

## 2. 현재까지 확인된 핵심 문제점

### 문제 1. 뉴스 원문이 아닌 placeholder 데이터가 raw 에 섞여 들어감

- 확인 근거: `data/reports/raw_quality.json`
- 관찰 내용:
  - `news.total_docs = 585`
  - `title_eq_content = 585`
  - `placeholder_suspects = 585`
  - `low_quality_suspects = 585`
- 의미:
  - 과거 raw 뉴스 데이터에는 실제 기사 본문이 아니라 placeholder 텍스트만 들어간 케이스가 누적되어 있었다.

### 문제 2. 종목토론방 데이터 중 본문 없이 제목만 저장되는 경우가 많았음

- 확인 근거: `data/reports/raw_quality.json`
- 관찰 내용:
  - `forum.total_docs = 21715`
  - `title_eq_content = 9679`
  - 비율로는 약 `44.57%`
- 의미:
  - 토론방 목록 페이지에서 제목은 수집되지만, 상세 본문 추출이 실패한 경우가 적지 않았다.
  - 이런 데이터는 검색 품질을 오염시키기 쉽다.

### 문제 3. DART 공시 본문 대신 wrapper 페이지나 인코딩 깨진 텍스트가 들어감

- 확인 근거: `data/reports/raw_quality.json`
- 관찰 내용:
  - `dart.total_docs = 2416`
  - `dart_wrapper_suspects = 390`
  - `dart_has_body_but_wrapper = 265`
  - `low_quality_suspects = 2157`
- 의미:
  - DART는 실제 본문 대신 안내 문구, 뷰어 wrapper, 깨진 문자열이 들어오면 품질이 급격히 떨어진다.

### 문제 4. 재수집 시 중복 데이터와 갱신 정책이 복잡해짐

- 원인:
  - 같은 테마를 여러 기간으로 수집하거나, 같은 종목을 다시 수집하면 raw / corpus / vector 자산에 중복이 생기기 쉬웠다.
  - 문서형 데이터와 시계열 데이터가 저장 방식이 다르기 때문에 중복 제거 기준도 달라야 했다.

### 문제 5. BM25 모듈 참조와 실제 파일 구조가 불일치했음

- 확인 내용:
  - `scripts_rag_pipeline.py`
  - `src/retrieval/services.py`
  - `src/rag/__init__.py`
  에서 `BM25IndexManager`를 사용하지만, 실제 `src/rag/bm25_index.py` 파일이 없었다.
- 영향:
  - import chain 이 깨져서 BM25 기반 검색/검증 흐름이 구조적으로 불완전했다.

### 문제 6. 문서화와 파일 단위 설명이 부족했음

- 원인:
  - 레이어 분리는 잘 되어 있지만, 처음 보는 사람이 파일을 열었을 때 역할과 책임을 즉시 파악하기 어려웠다.
  - 일부 기존 문서/문자열은 인코딩 흔적 때문에 가독성이 떨어졌다.

---

## 3. 문제 원인과 해결 방식

| 문제 | 원인 | 적용한 해결 방식 |
|---|---|---|
| 뉴스 placeholder | 검색 결과는 잡히지만 기사 본문 추출 실패 시 품질 검증이 약했음 | `src/ingestion/naver_news.py`에서 기사 상세 추출 후 `_is_valid_news_document()`로 길이/placeholder 검증 |
| 토론방 title-only 데이터 | 목록 API 또는 상세 페이지에서 본문 추출 실패 | `src/ingestion/naver_forum.py`에서 API 우선, 실패 시 Playwright fallback, `body_extracted`와 `content_source` 메타데이터 기록 |
| DART wrapper / mojibake | viewer 페이지, zip 내부 문서, 인코딩 차이, 안내 페이지 혼입 | `src/ingestion/dart.py`에서 structured API -> official zip 문서 -> viewer fallback 순으로 시도하고 wrapper / mojibake / too-short 규칙으로 걸러냄 |
| Layer 2 오염 | raw 에 남아 있는 저품질 데이터가 corpus/vector 로 올라감 | `src/rag/raw_layer2_builder.py`에서 source 별 유효성 검사 후 corpus/BM25/vector build |
| 중복 누적 | 반복 수집, append, overwrite 시 기존 자산 관리가 복잡 | `src/rag/dedupe.py`의 stable id 규칙과 `RawLayer2Builder`, `SourceRAGBuilder`의 dedupe / overwrite 정책 적용 |
| BM25 구조 불일치 | 참조는 있는데 실제 구현 파일이 없었음 | 이번 작업에서 `src/rag/bm25_index.py`를 추가해 BM25 index 저장/검색 흐름을 연결 |
| 이해 난이도 높음 | 파일별 역할 설명 부족 | 주요 Python 파일 상단에 구조 설명 주석 추가, 본 문서 생성 |

---

## 4. 최근 파이프라인 결과에서 보이는 상태

`data/reports/2차전지_ingestion_report.json` 기준:

- `stock_count = 2`
- `enabled_sources = news, dart, forum, chart`
- `raw_docs_count = 330`
- `built_records_count = 308`
- `final_records_count = 308`
- `skipped_invalid_count_by_source.forum = 84`

이 수치는 현재 파이프라인이 raw 데이터를 바로 다 올리는 것이 아니라, 일정 부분 필터링과 정제를 거친 뒤 Layer 2 자산을 만들고 있다는 뜻이다.

즉, 과거 raw 누적 데이터에는 품질 문제가 남아 있지만, 현재 구조는 그 문제를 Layer 2에서 완화하도록 설계되어 있다.

---

## 5. 전체 구조

### 디렉터리 구조

```text
HQA_Project/
├─ .gitignore
├─ README.md
├─ requirements.txt
├─ scripts_rag_pipeline.py
├─ build_period_rag.py
├─ debug.py
├─ PROJECT_STATUS_AND_STRUCTURE.md
├─ scripts/
│  └─ inspect_raw_quality.py
├─ data/
│  ├─ raw/
│  │  ├─ theme_targets/
│  │  ├─ news/
│  │  ├─ dart/
│  │  ├─ forum/
│  │  └─ chart/
│  ├─ corpora/
│  ├─ market_data/
│  ├─ bm25/
│  ├─ vector_stores/
│  ├─ period_rag/
│  └─ reports/
└─ src/
   ├─ ingestion/
   │  ├─ base.py
   │  ├─ types.py
   │  ├─ services.py
   │  ├─ naver_theme.py
   │  ├─ naver_news.py
   │  ├─ naver_forum.py
   │  ├─ dart.py
   │  ├─ kis_client.py
   │  └─ kis_chart.py
   ├─ data_pipeline/
   │  ├─ collectors.py
   │  ├─ rag_builder.py
   │  └─ __init__.py
   ├─ rag/
   │  ├─ bm25_index.py
   │  ├─ dedupe.py
   │  ├─ raw_layer2_builder.py
   │  ├─ source_registry.py
   │  ├─ vector_store.py
   │  └─ __init__.py
   └─ retrieval/
      ├─ services.py
      └─ __init__.py
```

### 레이어별 역할

#### Layer 1. Ingestion

- 외부 소스에서 데이터를 가져오는 단계
- 대상:
  - 테마 종목
  - 뉴스
  - DART 공시
  - 종목토론방
  - 차트/KIS 시세
- 출력:
  - `data/raw/...`

#### Layer 2. Build / RAG Asset

- raw 데이터를 정제해서 검색 가능한 자산으로 바꾸는 단계
- 출력:
  - `data/corpora/...`
  - `data/bm25/...`
  - `data/vector_stores/...`
  - `data/market_data/...`

#### Layer 3. Retrieval

- BM25 + vector 검색 결과를 합쳐서 최종 검색 결과를 만드는 단계
- 진입점:
  - `src/retrieval/services.py`

#### Layer 4. Period Snapshot

- 이미 만든 corpus 에서 특정 기간만 잘라 별도 RAG 세트를 만드는 단계
- 진입점:
  - `build_period_rag.py`

---

## 6. 실제 데이터 흐름

```text
CLI 입력
-> scripts_rag_pipeline.py
-> src.ingestion.services.IngestionService
-> source collectors (theme/news/dart/forum/chart)
-> data/raw/*
-> src.rag.raw_layer2_builder.RawLayer2Builder
-> corpora / bm25 / vector_stores / market_data
-> src.retrieval.services.RetrievalService
```

---

## 7. 파일군별 설명

### 루트 스크립트

- `scripts_rag_pipeline.py`
  - 메인 수집 스크립트
  - 종목 결정, source 선택, raw 저장, Layer 2 rebuild, 요약 report 작성까지 담당
- `build_period_rag.py`
  - 기존 corpus 에서 기간 조건으로 다시 잘라 별도 snapshot 생성
- `debug.py`
  - DART 수집 결과를 빠르게 눈으로 확인하는 수동 디버그 스크립트

### `src/ingestion`

- 수집기와 수집 orchestration 레이어
- `base.py`: 공통 HTTP session / retry / date helper
- `types.py`: 공통 dataclass
- `services.py`: source 별 수집 조합 및 raw 저장 담당
- `naver_theme.py`: 테마 -> 종목 변환
- `naver_news.py`: 뉴스 검색 / 상세 기사 본문 추출
- `naver_forum.py`: 종목토론방 / 네이버 차트 수집
- `dart.py`: 공시 본문 추출과 품질 검증
- `kis_client.py`, `kis_chart.py`: KIS 시세 수집

### `src/rag`

- 검색 자산 생성 레이어
- `dedupe.py`: 문서/시장데이터 dedupe id 생성
- `raw_layer2_builder.py`: raw -> corpus / bm25 / vector / market_data
- `bm25_index.py`: BM25 저장/검색 관리
- `vector_store.py`: 경량 vector store
- `source_registry.py`: source 분류 규칙

### `src/retrieval`

- 최종 검색 서비스
- `services.py`: vector + BM25 hybrid 검색, RRF merge

### `scripts/inspect_raw_quality.py`

- raw 나 corpus JSONL 을 스캔해서 품질 문제를 계량적으로 보여주는 진단 도구

---

## 8. 남아 있는 리스크

### 1. 기존 raw 데이터는 여전히 과거 품질 문제를 포함할 수 있음

- 현재 코드는 Layer 2에서 걸러주지만,
- 과거에 저장된 raw 자체를 완전히 깨끗하게 바꾸려면 재수집 또는 정리 작업이 필요하다.

### 2. 외부 사이트 구조 변경 리스크

- Naver, DART, KIS는 모두 외부 HTML/API 구조에 영향을 받는다.
- 특히 Naver forum / news / DART viewer 는 레이아웃 변경에 취약하다.

### 3. 문서 인코딩 정리 필요

- `README.md` 등 일부 기존 문서는 현재 콘솔 환경에서 인코딩이 깨져 보인다.
- 핵심 구조는 이번 문서로 보완했지만, 기존 문서 전체를 UTF-8 기준으로 재정리할 필요가 있다.

### 4. 테스트 코드 부재

- 현재는 구조가 잘 나뉘어 있지만 자동 테스트가 거의 없다.
- collector 단위와 Layer 2 validation 단위의 테스트가 있으면 유지보수가 훨씬 쉬워진다.

---

## 9. 이번 작업에서 추가/보완한 것

- 프로젝트 상태와 구조를 정리한 `PROJECT_STATUS_AND_STRUCTURE.md` 추가
- 주요 Python 파일 상단에 역할 설명 주석 추가
- 누락되어 있던 `src/rag/bm25_index.py` 추가

---

## 10. 한 줄 요약

이 프로젝트는 `수집(raw) -> 정제/색인(corpus, BM25, vector, market_data) -> 검색(retrieval)` 구조로 잘 나뉘어 있고, 지금까지의 핵심 이슈는 뉴스 placeholder, forum title-only, DART wrapper/인코딩 문제, 중복 관리, 문서화 부족이었다.  
현재 코드는 이 문제들을 Layer 1 수집기와 Layer 2 정제 규칙으로 상당 부분 완화하고 있으며, 이번 작업으로 구조 문서와 BM25 모듈 불일치까지 보완했다.
