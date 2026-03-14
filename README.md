# HQA Project (Crawling + RAG Only)

이 저장소는 **데이터 크롤링**과 **RAG 코퍼스 구축**에만 집중하도록 정리되었습니다.

## 포함된 기능

- 네이버 뉴스 수집 (`NaverNewsCollector`)
- DART 공시 목록 수집 (`DartDisclosureCollector`)
- 네이버 종토방 수집 (`NaverStockForumCollector`)
- 수집 문서를 청크 단위 JSONL로 변환 (`RAGCorpusBuilder`)
- BM25 인덱스 관리 (`BM25IndexManager`)

## 프로젝트 구조

```text
HQA_Project/
├── scripts_rag_pipeline.py
├── requirements.txt
└── src/
    ├── data_pipeline/
    │   ├── __init__.py
    │   ├── collectors.py
    │   └── rag_builder.py
    └── rag/
        ├── __init__.py
        └── bm25_index.py
```

## 빠른 실행

```bash
pip install -r requirements.txt
python scripts_rag_pipeline.py \
  --stock-name 삼성전자 \
  --stock-code 005930 \
  --corp-code 00126380 \
  --from-date 20250101 \
  --to-date 20251231 \
  --output ./data/rag_corpus.jsonl
```

> `DART_API_KEY` 환경변수를 설정하면 DART 데이터까지 함께 수집됩니다.
