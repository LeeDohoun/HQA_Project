# HQA Project (Crawling + Source-wise RAG Data Pipeline)

이 저장소는 **뉴스 / DART 공시 / 종토방** 데이터를 수집하고  
이를 **RAG 코퍼스 및 검색 인덱스(BM25 + Vector Store)**로 구축하는 파이프라인입니다.

## 포함된 기능

- 네이버 뉴스 수집 (`NaverNewsCollector`)
- DART 공시 목록 수집 (`DartDisclosureCollector`)
- 네이버 종토방 수집 (`NaverStockForumCollector`)
- 수집 문서를 JSONL 코퍼스로 변환 (`RAGCorpusBuilder`)
- BM25 인덱스 생성 및 검색 (`BM25IndexManager`)
- 소스별 벡터 스토어 구축
- 단일 종목 및 여러 종목 배치 수집
- 기존 코퍼스 누적/업데이트

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
        ├── bm25_index.py
        └── vector_store.py