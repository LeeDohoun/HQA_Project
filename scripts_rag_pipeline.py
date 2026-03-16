"""뉴스/DART/종토방 데이터를 소스별 RAG 자산(JSONL + 벡터 스토어)으로 생성."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from src.data_pipeline import (
    DartDisclosureCollector,
    NaverNewsCollector,
    NaverStockForumCollector,
    RAGCorpusBuilder,
)
from src.rag import SourceRAGBuilder


@dataclass
class StockTarget:
    stock_name: str
    stock_code: str
    corp_code: str = ""


def _load_stock_targets(args: argparse.Namespace) -> List[StockTarget]:
    targets: List[StockTarget] = []

    if args.stocks_file:
        with open(args.stocks_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stock_name = (row.get("stock_name") or "").strip()
                stock_code = (row.get("stock_code") or "").strip()
                corp_code = (row.get("corp_code") or "").strip()
                if stock_name and stock_code:
                    targets.append(
                        StockTarget(
                            stock_name=stock_name,
                            stock_code=stock_code,
                            corp_code=corp_code,
                        )
                    )

    if args.stock_name and args.stock_code:
        targets.append(
            StockTarget(
                stock_name=args.stock_name,
                stock_code=args.stock_code,
                corp_code=args.corp_code,
            )
        )

    if not targets:
        raise ValueError(
            "실행 대상 종목이 없습니다. --stock-name/--stock-code 또는 --stocks-file을 제공하세요."
        )

    dedup: Dict[str, StockTarget] = {}
    for target in targets:
        if target.stock_code not in dedup:
            dedup[target.stock_code] = target
    return list(dedup.values())


def _collect_docs(target: StockTarget, max_news: int, forum_pages: int, from_date: str, to_date: str, dart_api_key: str):
    docs = []

    news = NaverNewsCollector().collect(f"{target.stock_name} 주식", max_items=max_news)
    for doc in news:
        doc.stock_name = target.stock_name
        doc.stock_code = target.stock_code
    docs.extend(news)

    if target.corp_code and dart_api_key:
        dart = DartDisclosureCollector(api_key=dart_api_key).collect(
            corp_code=target.corp_code,
            bgn_de=from_date,
            end_de=to_date,
        )
        for doc in dart:
            doc.stock_name = target.stock_name
            doc.stock_code = target.stock_code
        docs.extend(dart)

    forum = NaverStockForumCollector().collect(stock_code=target.stock_code, pages=forum_pages)
    for doc in forum:
        doc.stock_name = target.stock_name
        doc.stock_code = target.stock_code
    docs.extend(forum)

    return docs


def _split_by_source(records: List[Dict]) -> Dict[str, List[Dict]]:
    grouped = {"news": [], "dart": [], "forum": []}
    for row in records:
        source_type = row.get("metadata", {}).get("source_type")
        if source_type in grouped:
            grouped[source_type].append(row)
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description="소스별 RAG JSONL + Vector Store 생성기")
    parser.add_argument("--stock-name", default="")
    parser.add_argument("--stock-code", default="")
    parser.add_argument("--corp-code", default="")
    parser.add_argument("--stocks-file", default="", help="CSV 파일 경로 (stock_name,stock_code,corp_code)")
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")
    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)
    parser.add_argument("--output-dir", default="./data")
    parser.add_argument("--base-filename", default="rag_corpus")
    args = parser.parse_args()

    import os

    targets = _load_stock_targets(args)
    dart_api_key = os.getenv("DART_API_KEY", "")

    all_docs = []
    for target in targets:
        target_docs = _collect_docs(
            target=target,
            max_news=args.max_news,
            forum_pages=args.forum_pages,
            from_date=args.from_date,
            to_date=args.to_date,
            dart_api_key=dart_api_key,
        )
        all_docs.extend(target_docs)
        print(f"[{target.stock_name}({target.stock_code})] 수집 문서 수: {len(target_docs)}")

    builder = RAGCorpusBuilder(chunk_size=700, chunk_overlap=100)
    all_records = builder.build_records(all_docs)
    grouped_records = _split_by_source(all_records)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    combined_jsonl = output_dir / f"{args.base_filename}.jsonl"
    builder.save_jsonl(all_records, str(combined_jsonl))

    source_jsonl_stats: Dict[str, int] = {}
    for source_type, records in grouped_records.items():
        source_jsonl = output_dir / f"{args.base_filename}_{source_type}.jsonl"
        source_jsonl_stats[source_type] = builder.save_jsonl(records, str(source_jsonl))

    vector_builder = SourceRAGBuilder(dimension=256)
    vector_stats = vector_builder.build_and_save(
        records=all_records,
        output_dir=str(output_dir / "vector_stores"),
    )

    print(f"총 수집 문서 수: {len(all_docs)}")
    print(f"통합 RAG 레코드 수: {len(all_records)}")
    print(f"통합 JSONL: {combined_jsonl}")
    print("소스별 JSONL 레코드 수:")
    for source_type in ("news", "dart", "forum"):
        print(f"  - {source_type}: {source_jsonl_stats.get(source_type, 0)}")

    print("소스별 벡터 스토어 레코드 수:")
    for source_type in ("news", "dart", "forum"):
        print(f"  - {source_type}: {vector_stats.get(source_type, 0)}")


if __name__ == "__main__":
    main()
