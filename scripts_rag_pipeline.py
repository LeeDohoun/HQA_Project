"""네이버 뉴스/DART/종토방 수집 후 RAG JSONL 생성/업데이트 스크립트."""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

from src.data_pipeline import (
    DartDisclosureCollector,
    NaverNewsCollector,
    NaverStockForumCollector,
    RAGCorpusBuilder,
)
from src.rag import BM25IndexManager


@dataclass
class StockTarget:
    stock_name: str
    stock_code: str
    corp_code: str = ""


def _load_jsonl(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []

    records: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _extract_stock_codes(records: Iterable[Dict]) -> Set[str]:
    stock_codes: Set[str] = set()
    for row in records:
        metadata = row.get("metadata", {})
        stock_code = metadata.get("stock_code")
        if stock_code:
            stock_codes.add(str(stock_code))
    return stock_codes


def _load_stock_targets(args: argparse.Namespace) -> List[StockTarget]:
    targets: List[StockTarget] = []

    if args.stocks_file:
        with open(args.stocks_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stock_name = (row.get("stock_name") or "").strip()
                stock_code = (row.get("stock_code") or "").strip()
                corp_code = (row.get("corp_code") or "").strip()

                if not stock_name or not stock_code:
                    continue

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

    if args.ensure_stock_name and args.ensure_stock_code:
        targets.append(
            StockTarget(
                stock_name=args.ensure_stock_name,
                stock_code=args.ensure_stock_code,
                corp_code=args.ensure_corp_code,
            )
        )

    if not targets:
        raise ValueError(
            "실행 대상 종목이 없습니다. --stock-name/--stock-code 또는 --stocks-file을 제공하세요."
        )

    # 중복 종목코드 제거 (앞에서 들어온 우선순위 유지)
    dedup: Dict[str, StockTarget] = {}
    for target in targets:
        if target.stock_code not in dedup:
            dedup[target.stock_code] = target

    return list(dedup.values())


def _collect_target_docs(
    target: StockTarget,
    max_news: int,
    forum_pages: int,
    from_date: str,
    to_date: str,
    dart_api_key: str,
) -> List:
    docs = []

    news = NaverNewsCollector().collect(
        f"{target.stock_name} 주식",
        max_items=max_news,
    )
    for doc in news:
        if not doc.stock_name:
            doc.stock_name = target.stock_name
        if not doc.stock_code:
            doc.stock_code = target.stock_code
    docs.extend(news)

    if target.corp_code and dart_api_key:
        dart = DartDisclosureCollector(api_key=dart_api_key).collect(
            corp_code=target.corp_code,
            bgn_de=from_date,
            end_de=to_date,
        )
        for doc in dart:
            if not doc.stock_name:
                doc.stock_name = target.stock_name
            if not doc.stock_code:
                doc.stock_code = target.stock_code
        docs.extend(dart)

    forum = NaverStockForumCollector().collect(
        stock_code=target.stock_code,
        pages=forum_pages,
    )
    for doc in forum:
        if not doc.stock_name:
            doc.stock_name = target.stock_name
    docs.extend(forum)

    return docs


def _build_bm25_index(records: List[Dict]) -> BM25IndexManager:
    bm25 = BM25IndexManager(persist_path="./data/_query_check_bm25.json", auto_save=False)
    bm25.clear()
    bm25.add_texts(
        texts=[row.get("text", "") for row in records],
        metadatas=[row.get("metadata", {}) for row in records],
    )
    return bm25


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler -> RAG JSONL 빌더")
    parser.add_argument("--stock-name", default="")
    parser.add_argument("--stock-code", default="")
    parser.add_argument("--corp-code", default="")
    parser.add_argument(
        "--stocks-file",
        default="",
        help="CSV 파일 경로 (stock_name,stock_code,corp_code)",
    )
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")
    parser.add_argument("--output", default="./data/rag_corpus.jsonl")
    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)

    # 기존 코퍼스 업데이트 전략
    parser.add_argument(
        "--update-mode",
        choices=["overwrite", "append-new-stocks"],
        default="append-new-stocks",
        help="overwrite: 매번 새로 생성 / append-new-stocks: 기존 코퍼스에 없는 종목만 추가",
    )

    # 질의 보강(없으면 추가 수집)
    parser.add_argument("--ensure-query", default="")
    parser.add_argument("--ensure-min-results", type=int, default=1)
    parser.add_argument("--ensure-top-k", type=int, default=5)
    parser.add_argument("--ensure-stock-name", default="")
    parser.add_argument("--ensure-stock-code", default="")
    parser.add_argument("--ensure-corp-code", default="")

    args = parser.parse_args()

    targets = _load_stock_targets(args)
    dart_api_key = os.getenv("DART_API_KEY", "")

    existing_records = []
    existing_stock_codes: Set[str] = set()

    if args.update_mode == "append-new-stocks":
        existing_records = _load_jsonl(args.output)
        existing_stock_codes = _extract_stock_codes(existing_records)

    collected_docs = []
    collected_stock_codes: Set[str] = set()

    for target in targets:
        if args.update_mode == "append-new-stocks" and target.stock_code in existing_stock_codes:
            print(f"[SKIP] 기존 코퍼스에 이미 존재: {target.stock_name}({target.stock_code})")
            continue

        target_docs = _collect_target_docs(
            target=target,
            max_news=args.max_news,
            forum_pages=args.forum_pages,
            from_date=args.from_date,
            to_date=args.to_date,
            dart_api_key=dart_api_key,
        )
        collected_docs.extend(target_docs)
        collected_stock_codes.add(target.stock_code)
        print(f"[{target.stock_name}({target.stock_code})] 수집 문서 수: {len(target_docs)}")

    builder = RAGCorpusBuilder(chunk_size=700, chunk_overlap=100)
    new_records = builder.build_records(collected_docs)

    merged_records = existing_records + new_records

    # 질의 대응 데이터 보강
    if args.ensure_query:
        bm25 = _build_bm25_index(merged_records)
        search_results = bm25.search(args.ensure_query, k=args.ensure_top_k)
        print(
            f"[QUERY CHECK] '{args.ensure_query}' 검색 결과: {len(search_results)}개"
        )

        needs_backfill = len(search_results) < args.ensure_min_results
        ensure_target_ready = bool(args.ensure_stock_name and args.ensure_stock_code)

        if needs_backfill and ensure_target_ready:
            ensure_target = StockTarget(
                stock_name=args.ensure_stock_name,
                stock_code=args.ensure_stock_code,
                corp_code=args.ensure_corp_code,
            )

            if ensure_target.stock_code not in existing_stock_codes and ensure_target.stock_code not in collected_stock_codes:
                ensure_docs = _collect_target_docs(
                    target=ensure_target,
                    max_news=args.max_news,
                    forum_pages=args.forum_pages,
                    from_date=args.from_date,
                    to_date=args.to_date,
                    dart_api_key=dart_api_key,
                )
                ensure_records = builder.build_records(ensure_docs)
                merged_records.extend(ensure_records)
                print(
                    f"[QUERY BACKFILL] 결과 부족으로 {ensure_target.stock_name}({ensure_target.stock_code}) 추가 수집: {len(ensure_docs)}개"
                )
            else:
                print("[QUERY BACKFILL] 보강 대상 종목은 이미 코퍼스에 있어 추가 수집을 생략합니다.")
        elif needs_backfill and not ensure_target_ready:
            print("[QUERY BACKFILL] 결과가 부족하지만 --ensure-stock-name/--ensure-stock-code가 없어 자동 보강을 생략합니다.")

    written = builder.save_jsonl(merged_records, args.output)

    print(f"기존 레코드 수: {len(existing_records)}")
    print(f"신규 레코드 수: {len(new_records)}")
    print(f"최종 RAG 레코드 수: {written}")
    print(f"출력 파일: {args.output}")


if __name__ == "__main__":
    main()
