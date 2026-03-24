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
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Set
from zipfile import ZipFile

import requests

sys.path.insert(0, os.path.abspath("."))

from src.ingestion import (
    CollectRequest,
    DocumentRecord,
    IngestionService,
    NaverThemeStockCollector,
    StockTarget,
)
from src.rag import BM25IndexManager
from src.rag.raw_layer2_builder import RawLayer2Builder



def _make_theme_key(theme: str, base_filename: str) -> str:
    raw = (theme or base_filename or "default").strip().lower()
    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r"[^0-9a-zA-Z가-힣_()-]+", "_", raw)
    return raw


def _download_corp_codes_csv(api_key: str, save_path: str = "./corp_codes.csv") -> Dict[str, str]:
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
    parser.add_argument("--max-general-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)
    parser.add_argument("--chart-pages", type=int, default=5)
    parser.add_argument(
        "--enabled-sources",
        default="news,dart,forum",
        help="수집 소스 목록(쉼표 구분). 예: news,dart,forum 또는 news,dart,forum,chart",
    )
    parser.add_argument(
        "--general-news-keywords",
        default="",
        help="쉼표(,)로 구분한 일반 뉴스 키워드. 예: 코스피,금리,원달러환율",
    )

    parser.add_argument(
        "--update-mode",
        choices=["overwrite", "append-new-stocks"],
        default="append-new-stocks",
        help="overwrite: 해당 테마 코퍼스 재생성 + vectorDB 내 해당 테마 문서 교체 / append-new-stocks: 기존 종목도 포함해 신규 기간/문서를 dedupe append",
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
    ingestion_service = IngestionService()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_output_dir = output_dir / "raw"
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    for source in ("news", "dart", "forum", "theme_targets", "chart"):
        source_dir = raw_output_dir / source
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / f"{theme_key}.jsonl").touch(exist_ok=True)

    corpus_dir = output_dir / "corpora" / theme_key
    corpus_dir.mkdir(parents=True, exist_ok=True)

    existing_records: List[Dict] = []
    existing_stock_codes: Set[str] = set()

    if args.update_mode == "append-new-stocks":
        existing_records = _load_jsonl(str(combined_jsonl))
        existing_stock_codes = _extract_stock_codes(existing_records)

    collected_docs: List[DocumentRecord] = []
    collected_stock_codes: Set[str] = set()
    run_reports: List[Dict] = []
    enabled_sources = [s.strip() for s in args.enabled_sources.split(",") if s.strip()]

    theme_target_raw_dir = raw_output_dir / "theme_targets"
    theme_target_raw_dir.mkdir(parents=True, exist_ok=True)
    with (theme_target_raw_dir / f"{theme_key}.jsonl").open("a", encoding="utf-8") as f:
        for t in targets:
            f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")

    for target in targets:
        # 기존 종목이어도 skip하지 않는다.
        # append-new-stocks는 이제 "새 기간/새 문서 dedupe append" 모드로 사용.
        target_docs = ingestion_service.collect_target_documents(
            CollectRequest(
                target=target,
                max_news=args.max_news,
                forum_pages=args.forum_pages,
                chart_pages=args.chart_pages,
                from_date=args.from_date,
                to_date=args.to_date,
                dart_api_key=dart_api_key,
                theme_key=theme_key,
                enabled_sources=enabled_sources,
                raw_output_dir=str(raw_output_dir),
            )
        )
        collected_docs.extend(target_docs.documents)
        if target_docs.report:
            run_reports.append(asdict(target_docs.report))
        collected_stock_codes.add(target.stock_code)
        print(f"[{target.stock_name}({target.stock_code})] 수집 문서 수: {len(target_docs.documents)}")

    general_news_keywords = [
        keyword.strip() for keyword in args.general_news_keywords.split(",") if keyword.strip()
    ]
    general_news_docs = ingestion_service.collect_general_news(
        keywords=general_news_keywords,
        max_items=args.max_general_news,
        from_date=args.from_date,
        to_date=args.to_date,
        theme_key=theme_key,
        raw_output_dir=str(raw_output_dir),
    )
    if general_news_docs:
        collected_docs.extend(general_news_docs)
        print(f"[GENERAL NEWS] 수집 문서 수: {len(general_news_docs)}")

    layer2_builder = RawLayer2Builder(data_dir=str(output_dir))
    layer2_result = layer2_builder.rebuild_theme(theme_key=theme_key, update_mode=args.update_mode)
    merged_records = layer2_result["records"]

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
            ensure_docs = ingestion_service.collect_target_documents(
                CollectRequest(
                    target=ensure_target,
                    max_news=args.max_news,
                    forum_pages=args.forum_pages,
                    chart_pages=args.chart_pages,
                    from_date=args.from_date,
                    to_date=args.to_date,
                    dart_api_key=dart_api_key,
                    theme_key=theme_key,
                    enabled_sources=enabled_sources,
                    raw_output_dir=str(raw_output_dir),
                )
            )
            if ensure_docs.report:
                run_reports.append(asdict(ensure_docs.report))

            layer2_result = layer2_builder.rebuild_theme(theme_key=theme_key, update_mode=args.update_mode)
            merged_records = layer2_result["records"]

            print(
                f"[QUERY BACKFILL] 결과 부족으로 "
                f"{ensure_target.stock_name}({ensure_target.stock_code}) 추가 수집: "
                f"{len(ensure_docs.documents)}개"
            )

    source_jsonl_stats: Dict[str, int] = layer2_result["document_source_counts"]
    vector_stats: Dict[str, int] = layer2_result["vector_stats"]
    market_data_stats: Dict[str, int] = layer2_result["market_stats"]
    written_combined = layer2_result["combined_count"]

    print(f"테마 키: {theme_key}")
    print(f"총 수집 대상 종목 수: {len(targets)}")
    print(f"기존 레코드 수: {len(existing_records)}")
    print(f"신규 수집 문서 수: {len(collected_docs)}")
    print(f"최종 통합 RAG 레코드 수: {written_combined}")
    print(f"통합 JSONL: {output_dir / 'corpora' / theme_key / 'combined.jsonl'}")

    print("소스별 JSONL 레코드 수:")
    for source_type, count in sorted(source_jsonl_stats.items()):
        print(f"  - {source_type}: {count}")

    print("소스별 VectorDB 레코드 수:")
    for source_type, count in sorted(vector_stats.items()):
        print(f"  - {source_type}: {count}")

    print("시계열 market_data 저장 건수:")
    for source_type, count in sorted(market_data_stats.items()):
        print(f"  - {source_type}: {count}")

    dedup_added = max(0, len(merged_records) - len(existing_records))
    missing_stocks = [
        r["stock_name"]
        for r in run_reports
        if sum(r.get("source_counts", {}).values()) == 0
    ]
    summary_report = {
        "theme_key": theme_key,
        "stock_count": len(targets),
        "enabled_sources": enabled_sources,
        "source_success": {
            source: sum(1 for r in run_reports if r.get("source_success", {}).get(source))
            for source in enabled_sources
        },
        "source_fail": {
            source: sum(1 for r in run_reports if source in r.get("failures", {}))
            for source in enabled_sources
        },
        "collected_docs_count": len(collected_docs),
        "raw_saved_count": sum(
            sum((r.get("raw_saved_counts") or {}).values())
            for r in run_reports
        ),
        "records_before_dedup": len(existing_records) + len(collected_docs),
        "records_after_dedup": len(merged_records),
        "dedup_added_count": dedup_added,
        "missing_stocks": missing_stocks,
        "per_stock_reports": run_reports,
    }

    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{theme_key}_ingestion_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)

    print("[INGESTION REPORT]")
    print(f"  - report_path: {report_path}")
    print(f"  - raw_saved_count: {summary_report['raw_saved_count']}")
    print(f"  - records_before_dedup: {summary_report['records_before_dedup']}")
    print(f"  - records_after_dedup: {summary_report['records_after_dedup']}")
    if missing_stocks:
        print(f"  - missing_stocks: {', '.join(missing_stocks)}")

    dedup_added = max(0, len(merged_records) - len(existing_records))
    missing_stocks = [
        r["stock_name"]
        for r in run_reports
        if sum(r.get("source_counts", {}).values()) == 0
    ]
    summary_report = {
        "theme_key": theme_key,
        "stock_count": len(targets),
        "enabled_sources": enabled_sources,
        "source_success": {
            source: sum(1 for r in run_reports if r.get("source_success", {}).get(source))
            for source in enabled_sources
        },
        "source_fail": {
            source: sum(1 for r in run_reports if source in r.get("failures", {}))
            for source in enabled_sources
        },
        "collected_docs_count": len(collected_docs),
        "raw_saved_count": sum(
            sum((r.get("raw_saved_counts") or {}).values())
            for r in run_reports
        ),
        "records_before_dedup": len(existing_records) + len(new_records),
        "records_after_dedup": len(merged_records),
        "dedup_added_count": dedup_added,
        "missing_stocks": missing_stocks,
        "per_stock_reports": run_reports,
    }

    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{theme_key}_ingestion_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)

    print("[INGESTION REPORT]")
    print(f"  - report_path: {report_path}")
    print(f"  - raw_saved_count: {summary_report['raw_saved_count']}")
    print(f"  - records_before_dedup: {summary_report['records_before_dedup']}")
    print(f"  - records_after_dedup: {summary_report['records_after_dedup']}")
    if missing_stocks:
        print(f"  - missing_stocks: {', '.join(missing_stocks)}")


if __name__ == "__main__":
    main()
