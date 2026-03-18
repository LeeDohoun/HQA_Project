# 📊 Stock RAG Pipeline (뉴스 / DART / 종토방 기반 주도주 분석 시스템)

## 📌 프로젝트 개요

본 프로젝트는 네이버 금융 및 DART 데이터를 기반으로
**테마 → 종목 → 데이터 수집 → RAG 구성 → 에이전트 분석 → 주도주 도출**을 목표로 합니다.

### 🎯 목표

* 테마 기반 종목 자동 수집
* 뉴스 / 공시 / 종토방 데이터 통합
* 소스별 RAG 구축 (news / dart / forum)
* 시계열 기반 RAG 재구성 (반기/누적)
* 최종적으로 **주도주(hegemony stock) 탐색**

---

## 🧠 전체 구조

```
[테마 입력]
   ↓
테마 → 종목 리스트 수집
   ↓
각 종목별 데이터 수집
   - 뉴스
   - DART 공시
   - 종토방
   ↓
Raw Corpus 저장 (JSONL)
   ↓
VectorDB 생성 (소스별 3개)
   ↓
기간 필터 기반 RAG 재구성
   ↓
에이전트 분석 → 주도주 도출
```

---

## 📁 디렉토리 구조

```
HQA_Project/
│
├── src/
│   ├── data_pipeline/
│   │   ├── collectors.py        # 데이터 수집기
│   │   └── __init__.py
│   │
│   └── rag/
│       └── vector_store.py      # SimpleVectorStore
│
├── data/
│   ├── corpora/                # 테마별 raw 데이터
│   │   ├── 2차전지/
│   │   │   ├── combined.jsonl
│   │   │   ├── news.jsonl
│   │   │   ├── dart.jsonl
│   │   │   └── forum.jsonl
│   │
│   ├── vector_stores/          # 운영용 vectorDB (소스별 3개)
│   │   ├── news_vector_store.json
│   │   ├── dart_vector_store.json
│   │   └── forum_vector_store.json
│   │
│   └── period_rag/             # 기간별 RAG 생성 결과
│
├── scripts_rag_pipeline.py     # 수집 파이프라인
├── build_period_rag.py         # 기간 기반 RAG 재구성
└── README.md
```

---

## ⚙️ 설치

```bash
pip install -r requirements.txt
```

필수:

* requests
* beautifulsoup4
* selenium
* python-dotenv

---

## 🔑 환경 변수

`.env` 파일:

```
DART_API_KEY=your_api_key
```

---

## 🚀 사용 방법

---

### 1️⃣ 데이터 수집 (핵심 파이프라인)

```bash
python scripts_rag_pipeline.py `
  --theme "2차전지" `
  --theme-max-stocks 20 `
  --theme-max-pages 10 `
  --output-dir ./data `
  --base-filename rag_corpus_secondary_battery `
  --update-mode overwrite
```

---

### ✔ 수행 내용

* 테마 → 종목 자동 수집
* 종목별:

  * 뉴스
  * DART 공시
  * 종토방
* JSONL 저장
* vectorDB 자동 업데이트

---

## 📊 저장 구조

### Raw Corpus (누적 데이터)

```json
{
  "text": "...",
  "metadata": {
    "source_type": "news",
    "stock_name": "삼성SDI",
    "stock_code": "006400",
    "theme_key": "2차전지",
    "published_at": "2025-12-19",
    "url": "...",
    "title": "..."
  }
}
```

---

## 📦 VectorDB 구조

* news_vector_store.json
* dart_vector_store.json
* forum_vector_store.json

👉 소스별 3개만 유지

---

## 🧠 핵심 개념

### ✔ Raw Corpus vs RAG

| 구분         | 설명             |
| ---------- | -------------- |
| Raw Corpus | 원본 데이터 (누적 저장) |
| VectorDB   | 검색용 임베딩        |
| Period RAG | 특정 기간 데이터만 필터링 |

---

## 🕒 기간 기반 RAG 생성

### 2️⃣ 기간별 RAG 생성

```bash
python build_period_rag.py `
  --data-dir ./data `
  --theme-key 2차전지 `
  --from-date 20230101 `
  --to-date 20230630 `
  --output-name battery_2023H1 `
  --build-vector
```

---

### ✔ 수행 내용

* raw corpus 읽기
* 기간 필터 적용
* dedupe
* 새로운 RAG 생성
* (옵션) vectorDB 생성

---

## 📌 지원하는 RAG 유형

### ✔ 1. 기간 RAG

```
2023-01 ~ 2023-06
```

---

### ✔ 2. 누적 RAG

```
~ 2023-12 (2023 전체)
```

---

### ✔ 3. 테마 + 기간

```
2차전지 + 2023H1
```

---

### ✔ 4. 소스별 RAG

```
뉴스만 / DART만 / 종토방만
```

---

## 📈 데이터 수집 전략

### ✔ DART

* 기간 기반 수집 가능
* 최대 40,000건
* 과거부터 역순 수집 추천

---

### ✔ 뉴스

* 실제 기사 날짜 파싱 필수
* 키워드 다양화 필요

---

### ✔ 종토방

* 페이지 확장 (max_pages 증가)
* 날짜 기반 stop 처리 가능

---

## 🔥 추천 운영 전략

### ✔ 수집

```
scripts_rag_pipeline.py → 계속 누적
```

---

### ✔ 분석

```
build_period_rag.py → 원하는 시점 RAG 생성
```

---

## ❗ 중요 설계 원칙

### 1. Raw 데이터는 절대 삭제하지 않는다

→ 모든 RAG는 재생성 가능해야 함

---

### 2. VectorDB는 운영용 (3개만 유지)

---

### 3. 시계열 분석은 필터 기반으로 수행

---

## 🧪 예시 워크플로우

### STEP 1

```
2차전지 전체 데이터 수집
```

### STEP 2

```
2023H1 RAG 생성
```

### STEP 3

```
2023H2 누적 RAG 생성
```

### STEP 4

```
에이전트 분석 → 주도주 도출
```

---

## 🧩 향후 확장

* LLM 기반 분석 에이전트
* 멀티 RAG 비교 (뉴스 vs 공시 vs 종토방)
* 주도주 scoring 시스템
* 테마 간 경쟁 분석

---

## 🔥 핵심 요약

* 데이터는 **계속 누적**
* RAG는 **필요할 때 생성**
* 분석은 **기간 기준으로 수행**

---

## 💡 한 줄 결론

👉 **"수집은 넓게, 분석은 정밀하게"**
