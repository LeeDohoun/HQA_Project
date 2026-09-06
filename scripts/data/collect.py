#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env

load_project_env()

from src.ingestion import (
    CollectRequest,
    IngestionService,
    NaverThemeStockCollector,
    StockTarget,
    ThemeTargetStore,
    make_theme_key,
)
from src.evidence.index_builder import EvidenceIndexBuilder
from src.ingestion.theme_targets import load_corp_code_map as _load_corp_code_map
from src.ingestion.storage import atomic_write
from scripts.data.common import DEFAULT_SOURCES, collection_status, enabled_sources, resolve_dates


SECONDS_PER_DAY = 24 * 60 * 60


def _refresh_corp_codes_csv(csv_path: str) -> None:
    from scripts.data.corp_codes import _write_csv, download_corp_codes

    api_key = (os.getenv("DART_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("DART_API_KEY가 없어 corp_codes.csv를 갱신할 수 없습니다.")

    rows = download_corp_codes(api_key)
    _write_csv(rows, Path(csv_path))


def _ensure_fresh_corp_codes_csv(csv_path: str, *, max_age_days: int = 7) -> bool:
    if not csv_path:
        return False

    path = Path(csv_path)
    exists = path.exists()
    age_seconds = time.time() - path.stat().st_mtime if exists else None
    stale = age_seconds is not None and age_seconds > max_age_days * SECONDS_PER_DAY

    if exists and not stale:
        return False

    reason = "없음" if not exists else f"{max_age_days}일 초과"
    if not (os.getenv("DART_API_KEY") or "").strip():
        print(f"[WARN][DART] corp_codes.csv {reason}, DART_API_KEY 없음 → 자동 갱신 건너뜀")
        return False

    try:
        _refresh_corp_codes_csv(str(path))
    except Exception as exc:
        if exists:
            print(f"[WARN][DART] corp_codes.csv refresh failed; existing file retained: {type(exc).__name__}")
        else:
            print(f"[WARN][DART] corp_codes.csv creation failed: {type(exc).__name__}")
        return False

    print(f"[DART] corp_codes.csv 자동 갱신 완료: {path}")
    return True


def _parse_enabled_sources(raw: str) -> List[str]:
    return enabled_sources(raw)


def _resolve_targets(args: argparse.Namespace, theme_key: str) -> List[StockTarget]:
    store = ThemeTargetStore(data_dir=args.data_dir)
    sources = _parse_enabled_sources(args.enabled_sources)
    needs_corp = bool({"dart", "financials"}.intersection(sources))
    corp_code_map = _load_corp_code_map(args.corp_codes_csv) if needs_corp else {}
    if args.reuse_saved_targets:
        saved = store.load_targets(theme_key)
        if saved:
            return store.backfill_corp_codes(theme_key, saved, corp_code_map,
                                              required=needs_corp, theme_name=args.theme)

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

    targets = [
        StockTarget(
            stock_name=item.stock_name,
            stock_code=item.stock_code,
            corp_code=corp_code_map.get(item.stock_code, ""),
        )
        for item in theme_stocks
    ]
    targets = store.backfill_corp_codes(theme_key, targets, corp_code_map,
                                        required=needs_corp, theme_name=args.theme)
    return store.save_targets(
        theme_key=theme_key,
        targets=targets,
        theme_name=args.theme,
        mode=args.target_mode,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="테마 데이터 수집 및 정제/인덱스 빌드 (LLM 분석과 주문 없음)",
    )
    parser.add_argument("--theme", required=True, help="테마 키워드")
    parser.add_argument("--theme-key", default="", help="저장용 테마 키")
    parser.add_argument(
        "--data-dir",
        default=str(get_data_dir()),
    )
    parser.add_argument("--theme-max-stocks", type=int, default=30)
    parser.add_argument("--theme-max-pages", type=int, default=10)
    parser.add_argument(
        "--target-mode",
        choices=["overwrite", "append"],
        default="overwrite",
        help="theme_targets 저장 방식",
    )
    target_source = parser.add_mutually_exclusive_group()
    target_source.add_argument(
        "--reuse-saved-targets",
        action="store_true",
        default=True,
        help="기존 theme_targets 파일이 있으면 재사용 (기본값)",
    )
    target_source.add_argument(
        "--refresh-targets",
        dest="reuse_saved_targets",
        action="store_false",
        help="저장된 종목 목록 대신 테마 구성 종목을 다시 조회",
    )
    parser.add_argument(
        "--save-only",
        action="store_true",
        help="theme_targets 저장만 수행하고 실제 수집은 건너뜀",
    )
    parser.add_argument("--corp-codes-csv", default="./corp_codes.csv")
    parser.add_argument(
        "--corp-codes-max-age-days",
        type=int,
        default=7,
        help="corp_codes.csv 자동 갱신 기준 일수. 기본값: 7",
    )
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--max-general-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)
    parser.add_argument("--chart-pages", type=int, default=5)
    parser.add_argument(
        "--enabled-sources",
        default=DEFAULT_SOURCES,
        help="수집 소스 목록(쉼표 구분): news,dart,financials,forum,chart. chart는 KRX OHLCV를 수집합니다.",
    )
    parser.add_argument("--general-news-keywords", default="")
    parser.add_argument(
        "--update-mode",
        choices=["append-new-stocks", "overwrite"],
        default="append-new-stocks",
        help="Layer 2 빌드 방식",
    )
    args = parser.parse_args()
    try:
        resolve_dates(args)
        sources = _parse_enabled_sources(args.enabled_sources)
    except ValueError as exc:
        parser.error(str(exc))

    theme_key = args.theme_key or make_theme_key(args.theme, args.theme)
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    report_path = data_dir / "reports" / f"{theme_key}_ingestion_report.json"
    try:
        if {"dart", "financials"}.intersection(sources):
            _ensure_fresh_corp_codes_csv(args.corp_codes_csv, max_age_days=args.corp_codes_max_age_days)
        targets = _resolve_targets(args, theme_key)
    except Exception as exc:
        summary = {"theme_key": theme_key, "status": "error", "reason": str(exc),
                   "enabled_sources": sources, "per_stock_reports": []}
        atomic_write(report_path, json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"[ERROR] target resolution failed; report: {report_path}")
        return 1
    store = ThemeTargetStore(data_dir=args.data_dir)

    print(f"[THEME] theme={args.theme} theme_key={theme_key} targets={len(targets)}")
    print(f"[THEME] target file: {store.get_path(theme_key)}")

    if args.save_only:
        return 0

    raw_output_dir = data_dir / "raw"
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    ingestion_service = IngestionService()
    run_reports = []
    dart_api_key = os.getenv("DART_API_KEY", "")

    for target in targets:
        try:
            result = ingestion_service.collect_target_documents(
                CollectRequest(
                    target=target, max_news=args.max_news, forum_pages=args.forum_pages, chart_pages=args.chart_pages,
                    from_date=args.from_date, to_date=args.to_date, dart_api_key=dart_api_key,
                    theme_key=theme_key, enabled_sources=sources, raw_output_dir=str(raw_output_dir),
                    incremental=args.incremental,
                )
            )
            if result.report is None:
                raise ValueError("collection report is missing")
            run_reports.append(asdict(result.report))
            print(f"[COLLECT] {target.stock_name}({target.stock_code}) "
                  f"docs={len(result.documents)} market={len(result.market_records)} "
                  f"financials={len(result.financial_snapshots)}")
        except Exception as exc:
            run_reports.append({"stock_code": target.stock_code, "enabled_sources": sources,
                                "source_success": {source: False for source in sources},
                                "source_status": {source: "error" for source in sources},
                                "failures": {"collection": type(exc).__name__}})

    general_news_keywords = [
        keyword.strip()
        for keyword in args.general_news_keywords.split(",")
        if keyword.strip()
    ]
    if general_news_keywords:
        try:
            general_news_docs = ingestion_service.collect_general_news(
                keywords=general_news_keywords, max_items=args.max_general_news,
                from_date=args.from_date, to_date=args.to_date, theme_key=theme_key,
                raw_output_dir=str(raw_output_dir),
            )
            print(f"[GENERAL NEWS] docs={len(general_news_docs)}")
        except Exception as exc:
            run_reports.append({"enabled_sources": ["general_news"],
                                "source_success": {"general_news": False},
                                "source_status": {"general_news": "error"},
                                "failures": {"general_news": type(exc).__name__}})

    status = collection_status(run_reports)
    layer2 = {}
    build_status = "blocked"
    if status == "done":
        try:
            layer2 = EvidenceIndexBuilder(data_dir=str(data_dir)).rebuild_theme(
                theme_key=theme_key, update_mode=args.update_mode,
            )
            build_status = "done"
        except Exception as exc:
            status = build_status = "error"
            layer2 = {"error": type(exc).__name__}

    summary = {
        "theme_name": args.theme,
        "theme_key": theme_key,
        "target_count": len(targets),
        "enabled_sources": sources,
        "status": status,
        "build_status": build_status,
        "from_date": args.from_date,
        "to_date": args.to_date,
        "incremental": args.incremental,
        "build_error": layer2.get("error"),
        "raw_docs_count": layer2.get("raw_docs_count", 0),
        "built_records_count": layer2.get("built_records_count", 0),
        "final_records_count": layer2.get("final_records_count", 0),
        "document_source_counts": layer2.get("document_source_counts", {}),
        "market_stats": layer2.get("market_stats", {}),
        "canonical_stats": layer2.get("canonical_stats", {}),
        "per_stock_reports": run_reports,
    }
    atomic_write(report_path, json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"[REPORT] {report_path}")
    print(
        f"[{status.upper()}] raw_docs={summary['raw_docs_count']} "
        f"records={summary['final_records_count']}"
    )
    return 0 if status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
