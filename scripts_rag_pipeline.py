"""네이버 뉴스/DART/종토방 수집 후 RAG JSONL 생성/업데이트 스크립트."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import argparse
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Set
from zipfile import ZipFile

import requests
import inspect

sys.path.insert(0, os.path.abspath("."))

from src.data_pipeline.collectors import (
    CrawledDocument,
    DartDisclosureCollector,
    NaverNewsCollector,
    NaverStockForumCollector,
    NaverThemeStockCollector,
)
from src.data_pipeline.rag_builder import RAGCorpusBuilder
from src.rag import BM25IndexManager
from src.rag.vector_store import SourceRAGBuilder


@dataclass
class StockTarget:
    stock_name: str
    stock_code: str
    corp_code: str = ""


def _make_theme_key(theme: str, base_filename: str) -> str:
    raw = (theme or base_filename or "default").strip().lower()
    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r"[^0-9a-zA-Z가-힣_()-]+", "_", raw)
    return raw


def _download_corp_codes_csv(api_key: str, save_path: str = "./corp_codes.csv") -> Dict[str, str]:
    """
    OpenDART 고유번호 ZIP(XML)에서 stock_code -> corp_code 매핑을 생성하고 CSV로 저장한다.
    """
    if not api_key:
        print("[WARN][DART] DART_API_KEY가 없어 corp_codes.csv를 자동 생성할 수 없습니다.")
        return {}

    url = "https://opendart.fss.or.kr/api/corpCode.xml"
    params = {"crtfc_key": api_key}

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        mapping: Dict[str, str] = {}

        with ZipFile(BytesIO(response.content)) as zf:
            names = zf.namelist()
            if not names:
                print("[WARN][DART] corpCode ZIP 내부에 파일이 없습니다.")
                return {}
            xml_name = names[0]
            xml_bytes = zf.read(xml_name)

        root = ET.fromstring(xml_bytes)

        with open(save_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["stock_code", "corp_code"])

            for item in root.findall("list"):
                stock_code = (item.findtext("stock_code") or "").strip()
                corp_code = (item.findtext("corp_code") or "").strip()

                if not stock_code or not corp_code:
                    continue

                mapping[stock_code] = corp_code
                writer.writerow([stock_code, corp_code])

        print(f"[INFO][DART] corp_codes.csv 자동 생성 완료: {save_path}, {len(mapping)}건")
        return mapping

    except Exception as e:
        print(f"[WARN][DART] corp_codes.csv 자동 생성 실패: {e}")
        return {}


def _load_corp_code_map(csv_path: str, dart_api_key: str = "") -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    if not os.path.exists(csv_path):
        print(f"[INFO][DART] {csv_path} 없음 -> 자동 생성 시도")
        mapping = _download_corp_codes_csv(dart_api_key, save_path=csv_path)
        if mapping:
            return mapping
        return {}

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stock_code = (row.get("stock_code") or "").strip()
            corp_code = (row.get("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code

    print(f"[INFO][DART] corp_codes.csv 로드 완료: {len(mapping)}건")
    return mapping


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

    dart_api_key = os.getenv("DART_API_KEY", "")
    corp_code_map = _load_corp_code_map("./corp_codes.csv", dart_api_key=dart_api_key)

    if args.theme:
        theme_collector = NaverThemeStockCollector()
        theme_stocks = theme_collector.collect(
            theme_keyword=args.theme,
            max_stocks=args.theme_max_stocks,
            max_pages=args.theme_max_pages,
        )

        print(f"[THEME] '{args.theme}' 테마에서 {len(theme_stocks)}개 종목을 자동 선택했습니다.")

        if not theme_stocks:
            raise ValueError(
                f"'{args.theme}' 테마에서 종목을 찾지 못했습니다. "
                "테마 키워드를 바꾸거나 테마 수집기 파싱 로직을 확인하세요."
            )

        for item in theme_stocks:
            mapped_corp_code = corp_code_map.get(item.stock_code, "")
            print(
                f"[DEBUG][CORP MAP] stock={item.stock_name} "
                f"code={item.stock_code} corp_code={mapped_corp_code}"
            )
            targets.append(
                StockTarget(
                    stock_name=item.stock_name,
                    stock_code=item.stock_code,
                    corp_code=mapped_corp_code,
                )
            )

    if args.stocks_file:
        with open(args.stocks_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stock_name = (row.get("stock_name") or "").strip()
                stock_code = (row.get("stock_code") or "").strip()
                corp_code = (row.get("corp_code") or "").strip()

                if not stock_name or not stock_code:
                    continue

                if not corp_code:
                    corp_code = corp_code_map.get(stock_code, "")

                targets.append(
                    StockTarget(
                        stock_name=stock_name,
                        stock_code=stock_code,
                        corp_code=corp_code,
                    )
                )

    if args.stock_name and args.stock_code:
        manual_corp_code = args.corp_code or corp_code_map.get(args.stock_code, "")
        targets.append(
            StockTarget(
                stock_name=args.stock_name,
                stock_code=args.stock_code,
                corp_code=manual_corp_code,
            )
        )

    if args.ensure_stock_name and args.ensure_stock_code:
        ensure_corp_code = args.ensure_corp_code or corp_code_map.get(args.ensure_stock_code, "")
        targets.append(
            StockTarget(
                stock_name=args.ensure_stock_name,
                stock_code=args.ensure_stock_code,
                corp_code=ensure_corp_code,
            )
        )

    if not targets:
        raise ValueError(
            "실행 대상 종목이 없습니다. "
            "--theme 또는 --stock-name/--stock-code 또는 --stocks-file을 제공하세요."
        )

    dedup: Dict[str, StockTarget] = {}
    for target in targets:
        if target.stock_code not in dedup:
            dedup[target.stock_code] = target

    return list(dedup.values())


def _build_news_keyword(stock_name: str, stock_code: str) -> str:
    name = stock_name.strip()

    if len(name) <= 2:
        return f"{name} 주가"

    return f"{stock_name} {stock_code} 주식"


def _attach_stock_info(
    docs: List[CrawledDocument],
    stock_name: str,
    stock_code: str,
    theme_key: str,
) -> List[CrawledDocument]:
    for doc in docs:
        if not doc.stock_name:
            doc.stock_name = stock_name
        if not doc.stock_code:
            doc.stock_code = stock_code

        doc.metadata = doc.metadata or {}
        doc.metadata["stock_name"] = stock_name
        doc.metadata["stock_code"] = stock_code
        doc.metadata["theme_key"] = theme_key
        doc.metadata["url"] = doc.url or ""
        doc.metadata["title"] = doc.title or ""
        doc.metadata["published_at"] = doc.published_at or ""

    return docs


def _collect_target_docs(
    target: StockTarget,
    max_news: int,
    forum_pages: int,
    from_date: str,
    to_date: str,
    dart_api_key: str,
    theme_key: str,
) -> List[CrawledDocument]:
    docs: List[CrawledDocument] = []

    news: List[CrawledDocument] = []
    dart: List[CrawledDocument] = []
    forum: List[CrawledDocument] = []

    news_keyword = _build_news_keyword(target.stock_name, target.stock_code)

    try:
        collector = NaverNewsCollector()
        sig = inspect.signature(collector.collect)

        kwargs = {"max_items": max_news}
        if "from_date" in sig.parameters:
            kwargs["from_date"] = from_date
        if "to_date" in sig.parameters:
            kwargs["to_date"] = to_date

        news = collector.collect(news_keyword, **kwargs)
        news = _attach_stock_info(news, target.stock_name, target.stock_code, theme_key)
    except Exception as e:
        print(f"[WARN][{target.stock_name}] news collect failed: {e}")

    if not target.corp_code:
        print(f"[WARN][DART] corp_code 없음: {target.stock_name}({target.stock_code})")
    elif not dart_api_key:
        print("[WARN][DART] DART_API_KEY 없음")
    else:
        try:
            dart = DartDisclosureCollector(api_key=dart_api_key).collect(
                corp_code=target.corp_code,
                bgn_de=from_date,
                end_de=to_date,
            )
            dart = _attach_stock_info(dart, target.stock_name, target.stock_code, theme_key)
        except Exception as e:
            print(f"[WARN][{target.stock_name}] dart collect failed: {e}")

    try:
        forum = NaverStockForumCollector().collect(
            stock_code=target.stock_code,
            pages=forum_pages,
        )
        forum = _attach_stock_info(forum, target.stock_name, target.stock_code, theme_key)
    except Exception as e:
        print(f"[WARN][{target.stock_name}] forum collect failed: {e}")

    docs.extend(news)
    docs.extend(dart)
    docs.extend(forum)

    return docs


def _build_bm25_index(records: List[Dict]) -> BM25IndexManager:
    bm25 = BM25IndexManager(
        persist_path="./data/_query_check_bm25.json",
        auto_save=False,
    )
    bm25.clear()
    bm25.add_texts(
        texts=[row.get("text", "") for row in records],
        metadatas=[row.get("metadata", {}) for row in records],
    )
    return bm25


def _split_by_source(records: List[Dict]) -> Dict[str, List[Dict]]:
    grouped: Dict[str, List[Dict]] = {
        "news": [],
        "dart": [],
        "forum": [],
    }
    for row in records:
        source_type = row.get("metadata", {}).get("source_type")
        if source_type in grouped:
            grouped[source_type].append(row)
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler -> RAG JSONL 빌더")
    parser.add_argument("--stock-name", default="")
    parser.add_argument("--stock-code", default="")
    parser.add_argument("--corp-code", default="")
    parser.add_argument("--theme", default="", help="테마 키워드 (예: 배터리, 반도체)")
    parser.add_argument("--theme-max-stocks", type=int, default=30)
    parser.add_argument("--theme-max-pages", type=int, default=10)
    parser.add_argument(
        "--stocks-file",
        default="",
        help="CSV 파일 경로 (stock_name,stock_code,corp_code)",
    )
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")

    parser.add_argument("--output-dir", default="./data")
    parser.add_argument("--base-filename", default="rag_corpus")

    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)

    parser.add_argument(
        "--update-mode",
        choices=["overwrite", "append-new-stocks"],
        default="append-new-stocks",
        help="overwrite: 해당 테마 코퍼스 재생성 + vectorDB 내 해당 테마 문서 교체 / append-new-stocks: 새 문서만 추가",
    )

    parser.add_argument("--ensure-query", default="")
    parser.add_argument("--ensure-min-results", type=int, default=1)
    parser.add_argument("--ensure-top-k", type=int, default=5)
    parser.add_argument("--ensure-stock-name", default="")
    parser.add_argument("--ensure-stock-code", default="")
    parser.add_argument("--ensure-corp-code", default="")

    args = parser.parse_args()

    
    theme_key = _make_theme_key(args.theme, args.base_filename)
    targets = _load_stock_targets(args)
    dart_api_key = os.getenv("DART_API_KEY", "")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus_dir = output_dir / "corpora" / theme_key
    corpus_dir.mkdir(parents=True, exist_ok=True)

    combined_jsonl = corpus_dir / "combined.jsonl"
    news_jsonl = corpus_dir / "news.jsonl"
    dart_jsonl = corpus_dir / "dart.jsonl"
    forum_jsonl = corpus_dir / "forum.jsonl"

    existing_records: List[Dict] = []
    existing_stock_codes: Set[str] = set()

    if args.update_mode == "append-new-stocks":
        existing_records = _load_jsonl(str(combined_jsonl))
        existing_stock_codes = _extract_stock_codes(existing_records)

    collected_docs: List[CrawledDocument] = []
    collected_stock_codes: Set[str] = set()

    for target in targets:
        if (
            args.update_mode == "append-new-stocks"
            and target.stock_code in existing_stock_codes
        ):
            print(f"[SKIP] 기존 테마 코퍼스에 이미 존재: {target.stock_name}({target.stock_code})")
            continue

        target_docs = _collect_target_docs(
            target=target,
            max_news=args.max_news,
            forum_pages=args.forum_pages,
            from_date=args.from_date,
            to_date=args.to_date,
            dart_api_key=dart_api_key,
            theme_key=theme_key,
        )
        collected_docs.extend(target_docs)
        collected_stock_codes.add(target.stock_code)
        print(f"[{target.stock_name}({target.stock_code})] 수집 문서 수: {len(target_docs)}")

    builder = RAGCorpusBuilder(chunk_size=700, chunk_overlap=100)
    new_records = builder.build_records(collected_docs)

    if args.update_mode == "overwrite":
        merged_records = new_records
    else:
        merged_records = existing_records + new_records

    if args.ensure_query:
        bm25 = _build_bm25_index(merged_records)
        search_results = bm25.search(args.ensure_query, k=args.ensure_top_k)
        print(f"[QUERY CHECK] '{args.ensure_query}' 검색 결과: {len(search_results)}개")

        needs_backfill = len(search_results) < args.ensure_min_results
        ensure_target_ready = bool(args.ensure_stock_name and args.ensure_stock_code)

        if needs_backfill and ensure_target_ready:
            ensure_target = StockTarget(
                stock_name=args.ensure_stock_name,
                stock_code=args.ensure_stock_code,
                corp_code=args.ensure_corp_code,
            )

            if (
                ensure_target.stock_code not in existing_stock_codes
                and ensure_target.stock_code not in collected_stock_codes
            ):
                ensure_docs = _collect_target_docs(
                    target=ensure_target,
                    max_news=args.max_news,
                    forum_pages=args.forum_pages,
                    from_date=args.from_date,
                    to_date=args.to_date,
                    dart_api_key=dart_api_key,
                    theme_key=theme_key,
                )
                ensure_records = builder.build_records(ensure_docs)
                merged_records.extend(ensure_records)
                print(
                    f"[QUERY BACKFILL] 결과 부족으로 "
                    f"{ensure_target.stock_name}({ensure_target.stock_code}) 추가 수집: "
                    f"{len(ensure_docs)}개"
                )
            else:
                print("[QUERY BACKFILL] 보강 대상 종목은 이미 코퍼스에 있어 추가 수집을 생략합니다.")
        elif needs_backfill and not ensure_target_ready:
            print(
                "[QUERY BACKFILL] 결과가 부족하지만 "
                "--ensure-stock-name/--ensure-stock-code가 없어 자동 보강을 생략합니다."
            )

    grouped_records = _split_by_source(merged_records)

    written_combined = builder.save_jsonl(merged_records, str(combined_jsonl))

    source_jsonl_stats: Dict[str, int] = {}
    source_to_path = {
        "news": news_jsonl,
        "dart": dart_jsonl,
        "forum": forum_jsonl,
    }

    for source_type, records in grouped_records.items():
        source_jsonl_stats[source_type] = builder.save_jsonl(
            records,
            str(source_to_path[source_type]),
        )

    vector_store_dir = output_dir / "vector_stores"
    vector_builder = SourceRAGBuilder()

    vector_stats = vector_builder.upsert_by_source(
        records=merged_records,
        output_dir=str(vector_store_dir),
        mode=args.update_mode,
        theme_key=theme_key,
    )

    print(f"테마 키: {theme_key}")
    print(f"총 수집 대상 종목 수: {len(targets)}")
    print(f"기존 레코드 수: {len(existing_records)}")
    print(f"신규 레코드 수: {len(new_records)}")
    print(f"최종 통합 RAG 레코드 수: {written_combined}")
    print(f"통합 JSONL: {combined_jsonl}")

    print("소스별 JSONL 레코드 수:")
    for source_type in ("news", "dart", "forum"):
        print(f"  - {source_type}: {source_jsonl_stats.get(source_type, 0)}")

    print("소스별 VectorDB 레코드 수:")
    for source_type in ("news", "dart", "forum"):
        print(f"  - {source_type}: {vector_stats.get(source_type, 0)}")


if __name__ == "__main__":
    main()