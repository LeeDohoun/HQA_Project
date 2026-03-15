# HQA Project (RAG Data Pipeline)

이 저장소는 **데이터 크롤링 + RAG 코퍼스 관리**에 집중합니다.

핵심 목표:
1. 여러 종목을 한 번에 수집
2. 기존 코퍼스를 계속 누적/업데이트
3. 질문(쿼리) 기준으로 데이터가 부족하면 종목 데이터를 추가 보강

## 현재 구조 (최신)

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
```

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
  --output ./data/rag_corpus.jsonl
```

### 2) 여러 종목 배치

`stocks.csv` 예시:

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
  --update-mode append-new-stocks \
  --output ./data/rag_corpus_multi.jsonl
```

## 코퍼스 업데이트 전략

- `--update-mode overwrite`
  - 기존 파일을 무시하고 새로 생성
- `--update-mode append-new-stocks` (기본)
  - 기존 JSONL을 읽고, 이미 존재하는 `stock_code` 종목은 스킵 후 신규 종목만 추가

## "질문했는데 자료가 부족할 때" 자동 보강

질문(검색 쿼리) 기준으로 코퍼스에 결과가 부족하면, 지정한 종목을 추가 수집해 보강할 수 있습니다.

```bash
python scripts_rag_pipeline.py \
  --stocks-file ./stocks.csv \
  --output ./data/rag_corpus_multi.jsonl \
  --ensure-query "HBM 수혜주" \
  --ensure-min-results 2 \
  --ensure-top-k 5 \
  --ensure-stock-name SK하이닉스 \
  --ensure-stock-code 000660 \
  --ensure-corp-code 00164779
```

동작 방식:
1. 현재 코퍼스를 BM25로 검색
2. 결과가 `--ensure-min-results` 미만이면
3. `--ensure-stock-*`로 지정한 종목을 추가 수집 후 코퍼스에 반영

## 옵션 요약

- 수집 대상
  - `--stock-name`, `--stock-code`, `--corp-code` (단일)
  - `--stocks-file` (배치 CSV)
- 수집 강도
  - `--max-news` (종목별 뉴스 수)
  - `--forum-pages` (종목별 종토방 페이지 수)
- 코퍼스 업데이트
  - `--update-mode` (`overwrite` | `append-new-stocks`)
- 질의 보강
  - `--ensure-query`
  - `--ensure-min-results`
  - `--ensure-top-k`
  - `--ensure-stock-name`, `--ensure-stock-code`, `--ensure-corp-code`

> `DART_API_KEY`를 설정하면 DART 공시도 수집됩니다.
> 네이버 뉴스/종토방은 API 키 없이 HTML 크롤링 방식으로 수집됩니다.
