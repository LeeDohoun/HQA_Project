# HQA Project (Layer 1 + Layer 2)

네이버/공시 기반 수집(Layer 1)과 raw 기반 RAG 재빌드/검색(Layer 2)을 분리한 프로젝트입니다.

---

## 현재 핵심 구조

```text
.
├── scripts_rag_pipeline.py          # Layer 1 수집 실행 + Layer 2 raw 재빌드 트리거
├── build_period_rag.py              # 기간 필터 RAG 재구성
├── requirements.txt
├── src/
│   ├── ingestion/                   # Layer 1 collectors/service
│   ├── data_pipeline/               # corpus builder + 호환 shim
│   ├── rag/                         # dedupe/raw builder/vector/bm25
│   └── retrieval/                   # RetrievalService(vector+BM25+RRF)
└── data/
    ├── raw/                         # 수집 원본 landing zone
    ├── corpora/                     # 문서형 RAG corpus
    ├── market_data/                 # 시계열(chart/quote/krx/fdr)
    ├── vector_stores/               # source별 vector store
    ├── bm25/                        # BM25 인덱스
    └── reports/                     # 실행 리포트
```

---

## 설치

```bash
pip install -r requirements.txt
```

---

## 환경변수

`.env` 예시:

```bash
DART_API_KEY=YOUR_DART_KEY

# (옵션) KIS chart 사용 시
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_BASE_URL=https://openapi.koreainvestment.com:9443
```

---

## 실행 방법

## 1) 수집 + raw 저장 + Layer 2 빌드

> 기본 소스: `news,dart,forum`

```bash
python scripts_rag_pipeline.py \
  --theme "풍력에너지" \
  --from-date 20240101 \
  --to-date 20241231 \
  --theme-max-stocks 20 \
  --max-news 100 \
  --forum-pages 500 \
  --update-mode append-new-stocks
```

chart까지 포함하려면:

```bash
python scripts_rag_pipeline.py \
  --theme "풍력에너지" \
  --from-date 20240101 \
  --to-date 20241231 \
  --enabled-sources news,dart,forum,chart \
  --chart-pages 5
```

### 산출물
- 문서형: `data/corpora/<theme_key>/*.jsonl`
- 시계열: `data/market_data/<theme_key>/*.jsonl`
- 벡터: `data/vector_stores/*_vector_store.json`
- BM25: `data/bm25/<theme_key>_bm25.json`

---

## 2) 기간 RAG 재구성

```bash
python build_period_rag.py \
  --data-dir ./data \
  --theme-key 풍력에너지 \
  --from-date 20240101 \
  --to-date 20240630 \
  --output-name wind_2024H1 \
  --build-vector
```

`--source-type`은 고정 목록이 아니라 문자열로 필터됩니다.

---

## 3) RetrievalService 사용 예시

```python
from src.retrieval import RetrievalService

svc = RetrievalService(data_dir="./data", theme_key="풍력에너지")
results = svc.search("풍력 수주와 실적", source_types=["news", "dart"], top_k=20)
for row in results[:3]:
    print(row["source_type"], row["rrf_score"], row["metadata"].get("title", ""))
```

---

## 참고

- `main.py`는 현재 코드베이스에서 사용되지 않아 제거되었습니다.
- Layer 2는 ingestion 결과 메모리를 직접 받지 않고 `data/raw`를 기준으로 재빌드합니다.
