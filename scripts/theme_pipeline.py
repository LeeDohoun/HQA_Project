#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, os.path.abspath("."))

try:
    from src.config.settings import get_data_dir, load_project_env
except ImportError:
    load_project_env = None

if load_project_env is not None:
    load_project_env()

from src.ingestion import (
    CollectRequest,
    IngestionService,
    NaverThemeStockCollector,
    StockTarget,
    ThemeTargetStore,
    make_theme_key,
)
from src.rag.raw_layer2_builder import RawLayer2Builder


SUPPORTED_ENABLED_SOURCES = ("news", "dart", "forum", "chart", "report")


def _load_corp_code_map(csv_path: str) -> Dict[str, str]:
    if not csv_path or not os.path.exists(csv_path):
        return {}

    mapping: Dict[str, str] = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stock_code = (row.get("stock_code") or "").strip()
            corp_code = (row.get("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code
    return mapping


def _parse_enabled_sources(raw: str) -> List[str]:
    seen = set()
    enabled = []
    for item in raw.split(","):
        source = item.strip().lower()
        if not source or source in seen:
            continue
        seen.add(source)
        if source in SUPPORTED_ENABLED_SOURCES:
            enabled.append(source)
    if not enabled:
        raise ValueError(
            "enabled source가 비어 있습니다. "
            f"지원 소스: {', '.join(SUPPORTED_ENABLED_SOURCES)}"
        )
    return enabled


def _reset_theme_raw_files(data_dir: Path, theme_key: str) -> None:
    for source in SUPPORTED_ENABLED_SOURCES:
        path = data_dir / "raw" / source / f"{theme_key}.jsonl"
        if path.exists():
            path.unlink()


def _resolve_targets(args: argparse.Namespace, theme_key: str) -> List[StockTarget]:
    store = ThemeTargetStore(data_dir=args.data_dir)
    if args.reuse_saved_targets:
        saved = store.load_targets(theme_key)
        if saved:
            return saved

    collector = NaverThemeStockCollector()
    theme_stocks = collector.collect(
        theme_keyword=args.theme,
        max_stocks=args.theme_max_stocks,
        max_pages=args.theme_max_pages,
    )
    if not theme_stocks:
        raise ValueError(
            f"'{args.theme}' 테마에서 종목을 찾지 못했습니다. "
            "테마 키워드를 바꾸거나 파서 상태를 확인하세요."
        )

    corp_code_map = _load_corp_code_map(args.corp_codes_csv)
    targets = [
        StockTarget(
            stock_name=item.stock_name,
            stock_code=item.stock_code,
            corp_code=corp_code_map.get(item.stock_code, ""),
        )
        for item in theme_stocks
    ]
    return store.save_targets(
        theme_key=theme_key,
        targets=targets,
        theme_name=args.theme,
        mode=args.target_mode,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="테마 종목 수집 -> theme_targets 저장 -> raw/L2 빌드",
    )
    parser.add_argument("--theme", required=True, help="테마 키워드")
    parser.add_argument("--theme-key", default="", help="저장용 테마 키")
    parser.add_argument(
        "--data-dir",
        default=str(get_data_dir()) if "get_data_dir" in globals() else "./data",
    )
    parser.add_argument("--theme-max-stocks", type=int, default=30)
    parser.add_argument("--theme-max-pages", type=int, default=10)
    parser.add_argument(
        "--target-mode",
        choices=["overwrite", "append"],
        default="overwrite",
        help="theme_targets 저장 방식",
    )
    parser.add_argument(
        "--reuse-saved-targets",
        action="store_true",
        help="기존 theme_targets 파일이 있으면 재사용",
    )
    parser.add_argument(
        "--save-only",
        action="store_true",
        help="theme_targets 저장만 수행하고 실제 수집은 건너뜀",
    )
    parser.add_argument("--corp-codes-csv", default="./corp_codes.csv")
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")
    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--max-reports", type=int, default=10)
    parser.add_argument("--report-source", default="naver", choices=["naver"])
    parser.add_argument(
        "--report-days-back",
        type=int,
        default=180,
        help="리포트 수집을 최근 N일로 제한합니다. 0이면 --from-date만 사용합니다.",
    )
    parser.add_argument("--report-pages", type=int, default=20)
    parser.add_argument("--max-general-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)
    parser.add_argument("--chart-pages", type=int, default=5)
    parser.add_argument(
        "--enabled-sources",
        default="news,dart,forum",
        help="수집 소스 목록(쉼표 구분): news,dart,forum,chart,report",
    )
    parser.add_argument("--general-news-keywords", default="")
    parser.add_argument(
        "--update-mode",
        choices=["append-new-stocks", "overwrite"],
        default="append-new-stocks",
        help="Layer 2 빌드 방식",
    )
    args = parser.parse_args()

    theme_key = args.theme_key or make_theme_key(args.theme, args.theme)
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    targets = _resolve_targets(args, theme_key)
    store = ThemeTargetStore(data_dir=args.data_dir)

    print(f"[THEME] theme={args.theme} theme_key={theme_key} targets={len(targets)}")
    print(f"[THEME] target file: {store.get_path(theme_key)}")

    if args.save_only:
        return

    enabled_sources = _parse_enabled_sources(args.enabled_sources)
    if args.update_mode == "overwrite":
        _reset_theme_raw_files(data_dir, theme_key)

    raw_output_dir = data_dir / "raw"
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    ingestion_service = IngestionService()
    run_reports = []
    dart_api_key = os.getenv("DART_API_KEY", "")

    for target in targets:
        result = ingestion_service.collect_target_documents(
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
                max_reports=args.max_reports,
                report_source=args.report_source,
                report_days_back=args.report_days_back,
                report_pages=args.report_pages,
                raw_output_dir=str(raw_output_dir),
            )
        )
        if result.report:
            run_reports.append(asdict(result.report))
        print(
            f"[COLLECT] {target.stock_name}({target.stock_code}) "
            f"docs={len(result.documents)} market={len(result.market_records)}"
        )

    general_news_keywords = [
        keyword.strip()
        for keyword in args.general_news_keywords.split(",")
        if keyword.strip()
    ]
    if general_news_keywords:
        general_news_docs = ingestion_service.collect_general_news(
            keywords=general_news_keywords,
            max_items=args.max_general_news,
            from_date=args.from_date,
            to_date=args.to_date,
            theme_key=theme_key,
            raw_output_dir=str(raw_output_dir),
        )
        print(f"[GENERAL NEWS] docs={len(general_news_docs)}")

    layer2 = RawLayer2Builder(data_dir=str(data_dir)).rebuild_theme(
        theme_key=theme_key,
        update_mode=args.update_mode,
    )

    summary = {
        "theme_name": args.theme,
        "theme_key": theme_key,
        "target_count": len(targets),
        "enabled_sources": enabled_sources,
        "raw_docs_count": layer2.get("raw_docs_count", 0),
        "built_records_count": layer2.get("built_records_count", 0),
        "final_records_count": layer2.get("final_records_count", 0),
        "document_source_counts": layer2.get("document_source_counts", {}),
        "market_stats": layer2.get("market_stats", {}),
        "canonical_stats": layer2.get("canonical_stats", {}),
        "per_stock_reports": run_reports,
    }
    report_dir = data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{theme_key}_ingestion_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"[REPORT] {report_path}")
    print(
        f"[DONE] raw_docs={summary['raw_docs_count']} "
        f"records={summary['final_records_count']}"
    )


if __name__ == "__main__":
    main()
