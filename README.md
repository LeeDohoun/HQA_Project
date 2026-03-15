# HQA Project (Crawling + RAG Data Pipeline)

이 저장소는 **데이터 크롤링**과 **RAG 코퍼스 구축/업데이트**에 집중합니다.

## 포함된 기능

- 네이버 뉴스 수집 (`NaverNewsCollector`)
- DART 공시 목록 수집 (`DartDisclosureCollector`)
- 네이버 종토방 수집 (`NaverStockForumCollector`)
- 수집 문서를 JSONL 코퍼스로 변환 (`RAGCorpusBuilder`)
- BM25 인덱스 생성 및 검색 (`BM25IndexManager`)
- 단일 종목 및 여러 종목 배치 수집
- 기존 코퍼스 누적/업데이트
- 질의 결과가 부족할 때 종목 데이터를 추가 보강

## 프로젝트 구조

```text
HQA_Project/
├── scripts_rag_pipeline.py          # 배치 수집 + 코퍼스 업데이트 + 질의 보강
├── requirements.txt
└── src/
    ├── data_pipeline/
    │   ├── __init__.py
    │   ├── collectors.py            # 네이버 뉴스 / DART / 종토방 수집기
    │   └── rag_builder.py           # JSONL 청크 코퍼스 빌더
    └── rag/
        ├── __init__.py
        └── bm25_index.py            # BM25 인덱스 + 검색