#  Stock RAG Pipeline  
뉴스 / 일반뉴스 / DART / 종토방 / 차트 기반 주도주 분석 시스템

---

## 1. 프로젝트 개요

본 프로젝트는 네이버 금융 및 DART 데이터를 기반으로  
테마 → 종목 → 데이터 수집 → RAG 구성 → 분석을 통해  
주도주 (hegemony stock)를 도출하는 시스템입니다.

###  목표

- 테마 기반 종목 자동 수집  
- 뉴스 / 일반뉴스 / 공시 / 종토방 / 차트 데이터 통합  
- 소스별 RAG 구축 (news / general_news / dart / forum / chart)  
- 기간 기반 RAG 재구성  
- 주도주 탐색 및 분석  

---

## 2. 전체 구조

```
[테마 입력]
   ↓
테마 → 종목 리스트 수집
   ↓
각 종목별 데이터 수집
   - 종목 뉴스
   - 일반 뉴스
   - DART 공시
   - 종토방
   - 일봉 차트
   ↓
Raw Corpus 저장 (JSONL)
   ↓
VectorDB 생성 (소스별 3개)
   ↓
기간 필터 기반 RAG 재구성
   ↓
분석 → 주도주 도출
```

---

## 3. 디렉토리 구조

```
HQA_Project/
│
├── src/
│   ├── data_pipeline/
│   │   ├── collectors.py
|   |   ├── rag_builders.py
│   │   └── __init__.py
│   │
│   └── rag/
|       ├── bm25_index.py
│       └── vector_store.py
│
├── data/
│   ├── corpora/
│   │   ├── 2차전지/
│   │   │   ├── combined.jsonl
│   │   │   ├── news.jsonl
│   │   │   ├── general_news.jsonl
│   │   │   ├── dart.jsonl
│   │   │   └── forum.jsonl
│   │   │   └── chart.jsonl
│   │
│   ├── vector_stores/
│   │   ├── news_vector_store.json
│   │   │   ├── general_news_vector_store.json
│   │   │   ├── dart_vector_store.json
│   │   │   └── forum_vector_store.json
│   │   │   └── chart_vector_store.json
│   │
│
├── corp_codes.csv
├── scripts_rag_pipeline.py
├── build_period_rag.py
└── README.md
```

---

## 4. 설치

```
pip install -r requirements.txt
```

### 필수 패키지

- requests  
- beautifulsoup4  
- selenium  
- webdriver-manager  
- python-dotenv  

---

## 5. 환경 변수

```
DART_API_KEY=your_api_key
```

---

## 6. 데이터 수집

```
python scripts_rag_pipeline.py \
  --theme "2차전지" \
  --general-news-keywords "코스피,금리,원달러환율" \
  --theme-max-stocks 20 \
  --theme-max-pages 10 \
  --chart-pages 5 \
  --output-dir ./data \
  --base-filename rag_corpus_secondary_battery \
  --update-mode overwrite
```

---

## 7. 업데이트 모드

### overwrite
기존 데이터 삭제 후 재수집

### append-new-stocks
신규 종목만 추가

---

## 8. Raw Corpus 구조

```
{
  "text": "...",
  "metadata": {
    "source_type": "news",
    "stock_name": "삼성SDI",
    "stock_code": "006400",
    "theme_key": "2차전지",
    "published_at": "2025-12-19T00:00:00"
  }
}
```

---

## 9. VectorDB

- news_vector_store.json  
- general_news_vector_store.json  
- dart_vector_store.json  
- forum_vector_store.json  
- chart_vector_store.json  

---

## 10. 핵심 개념

- Raw Corpus: 원본 데이터  
- VectorDB: 검색용  
- Period RAG: 기간 필터 결과  

---

## 11. 기간 RAG 생성

```
python build_period_rag.py \
  --data-dir ./data \
  --theme-key 2차전지 \
  --from-date 20230101 \
  --to-date 20230630 \
  --output-name battery_2023H1 \
  --build-vector
```

---

## 12. 지원 RAG 유형

- 기간  
- 누적  
- 테마+기간  
- 소스별  

---

## 13. 데이터 특성

### DART
- 정확한 날짜 필터

### 뉴스
- 최신순 탐색

### 종토방
- 페이지 기반 역순 수집

---

## 14. 운영 전략

- 수집: scripts_rag_pipeline  
- 분석: build_period_rag  

---

## 15. 설계 원칙

- Raw는 삭제하지 않음  
- 분석은 기간 기반  

---

## 16. 워크플로우

STEP 1 수집  
STEP 2 기간 RAG  
STEP 3 누적 RAG  
STEP 4 분석  

---

## 17. 확장

- LLM 분석  
- scoring  

---

## 결론

수집은 넓게, 분석은 정밀하게
