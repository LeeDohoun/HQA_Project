"""네이버 뉴스/DART/종토방 수집 후 RAG JSONL 생성 스크립트."""

from __future__ import annotations

import argparse
import os

from src.data_pipeline import (
    NaverNewsCollector,
    DartDisclosureCollector,
    NaverStockForumCollector,
    RAGCorpusBuilder,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler -> RAG JSONL 빌더")
    parser.add_argument("--stock-name", required=True)
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--corp-code", default="")
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")
    parser.add_argument("--output", default="./data/rag_corpus.jsonl")
    args = parser.parse_args()

    docs = []

    news = NaverNewsCollector().collect(f"{args.stock_name} 주식", max_items=20)
    docs.extend(news)

    if args.corp_code:
        dart_api_key = os.getenv("DART_API_KEY", "")
        if dart_api_key:
            dart = DartDisclosureCollector(api_key=dart_api_key).collect(
                corp_code=args.corp_code,
                bgn_de=args.from_date,
                end_de=args.to_date,
            )
            docs.extend(dart)

    forum = NaverStockForumCollector().collect(stock_code=args.stock_code, pages=3)
    docs.extend(forum)

    builder = RAGCorpusBuilder(chunk_size=700, chunk_overlap=100)
    records = builder.build_records(docs)
    written = builder.save_jsonl(records, args.output)

    print(f"수집 문서 수: {len(docs)}")
    print(f"RAG 레코드 수: {written}")
    print(f"출력 파일: {args.output}")


if __name__ == "__main__":
    main()
