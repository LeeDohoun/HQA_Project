# HQA Project (Source-wise RAG Data Pipeline)

이 저장소는 **뉴스 / DART 공시 / 종토방** 데이터를 각각 분리해서 RAG 자산으로 만드는 파이프라인입니다.

## 현재 구조 (최신)

```text
HQA_Project/
├── scripts_rag_pipeline.py          # 배치 수집 + 소스별 JSONL/벡터스토어 생성
├── requirements.txt
└── src/
    ├── data_pipeline/
    │   ├── __init__.py
    │   ├── collectors.py            # 네이버 뉴스 / DART / 종토방 수집기
    │   └── rag_builder.py           # 청크 + JSONL 빌더
    └── rag/
        ├── __init__.py              # RAG 모듈 export
        ├── bm25_index.py            # BM25 인덱스
        └── vector_store.py          # 소스별 경량 벡터 스토어
```

## 파이프라인 결과물

한 번 실행하면 아래가 생성됩니다.

1. 통합 코퍼스
- `data/rag_corpus.jsonl`

2. 소스별 코퍼스
- `data/rag_corpus_news.jsonl`
- `data/rag_corpus_dart.jsonl`
- `data/rag_corpus_forum.jsonl`

3. 소스별 벡터 스토어(JSON)
- `data/vector_stores/news_vector_store.json`
- `data/vector_stores/dart_vector_store.json`
- `data/vector_stores/forum_vector_store.json`

즉, 요청하신 방식처럼 **뉴스만의 RAG, DART만의 RAG, 종토방만의 RAG**를 각각 구성할 수 있습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행 방법

### 1) 단일 종목

```bash
python scripts_rag_pipeline.py \
  --stock-name 삼성전자 \
  --stock-code 005930 \
  --corp-code 00126380 \
  --output-dir ./data \
  --base-filename rag_corpus
```

### 2) 여러 종목 배치

`stocks.csv`:

```csv
stock_name,stock_code,corp_code
삼성전자,005930,00126380
SK하이닉스,000660,00164779
NAVER,035420,00266961
```

실행:

```bash
python scripts_rag_pipeline.py \
  --stocks-file ./stocks.csv \
  --from-date 20250101 \
  --to-date 20251231 \
  --max-news 20 \
  --forum-pages 3 \
  --output-dir ./data \
  --base-filename rag_corpus
```

## 옵션 요약

- 수집 대상
  - `--stock-name`, `--stock-code`, `--corp-code` (단일)
  - `--stocks-file` (배치 CSV)
- 수집 강도
  - `--max-news` (종목별 뉴스 수)
  - `--forum-pages` (종목별 종토방 페이지 수)
- 출력
  - `--output-dir` (결과 폴더)
  - `--base-filename` (파일명 prefix)

> `DART_API_KEY`를 설정하면 DART 공시도 수집됩니다.
> 네이버 뉴스/종토방은 API 키 없이 HTML 크롤링 방식으로 수집됩니다.
