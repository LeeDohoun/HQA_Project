#!/usr/bin/env python3
"""
HQA 통합 배치 파이프라인

수집 → Layer2 Build → Agent 분석을 한 명령으로 실행합니다.

Usage:
    # 전체 파이프라인 (수집 + 빌드 + 분석)
    python scripts/run_pipeline.py --theme 반도체 --full

    # 수집 + 빌드만 (분석 제외)
    python scripts/run_pipeline.py --theme 반도체 --collect-and-build

    # 빌드 + 분석만 (수집 제외 — 기존 raw 데이터 활용)
    python scripts/run_pipeline.py --theme 반도체 --build-and-analyze

    # 분석만 (기존 canonical index 활용)
    python scripts/run_pipeline.py --theme 반도체 --analyze-only

주기 설정:
  - 수집 주기: theme_pipeline.py의 ingestion 스케줄 (외부 cron/systemd로 관리 권장)
  - 빌드 주기: 수집 직후 자동 또는 별도 cron
  - 분석 주기: config/watchlist.yaml의 schedule 설정

이 스크립트는 위 3단계를 한 번에 묶어 실행합니다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _load_corp_code_map(csv_path: str) -> Dict[str, str]:
    """Load stock_code → corp_code mapping from CSV.

    Without corp_code, DART collection is silently skipped.
    """
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


def _step_collect(args, theme_key: str) -> dict:
    """Step 1: Data Collection via IngestionService.

    If no saved theme_targets exist, auto-discovers targets from Naver theme
    search and saves them — making --full truly self-contained.
    """
    print("\n" + "=" * 60)
    print("📡 Step 1: Data Collection")
    print("=" * 60)

    from src.ingestion import (
        CollectRequest,
        IngestionService,
        StockTarget,
    )
    from src.ingestion.theme_targets import ThemeTargetStore, make_theme_key

    store = ThemeTargetStore(data_dir=args.data_dir)
    targets = store.load_targets(theme_key)

    if targets:
        print(f"  theme_targets 재사용: {len(targets)} stocks")
    else:
        # Auto-discover targets via NaverThemeStockCollector
        print(f"  theme_targets 없음 → 테마 '{args.theme}' 자동 수집 시도")
        try:
            from src.ingestion import NaverThemeStockCollector

            collector = NaverThemeStockCollector()
            theme_stocks = collector.collect(
                theme_keyword=args.theme,
                max_stocks=args.theme_max_stocks,
                max_pages=args.theme_max_pages,
            )
            if not theme_stocks:
                print(
                    f"  ⚠️ '{args.theme}' 테마에서 종목을 찾지 못했습니다.\n"
                    "     테마 키워드를 확인하거나 theme_pipeline.py를 먼저 실행하세요."
                )
                return {"status": "skipped", "reason": "no_targets_discovered"}

            # Load corp_code mapping so DART collection works
            corp_code_map = _load_corp_code_map(
                getattr(args, "corp_codes_csv", "./corp_codes.csv")
            )
            corp_matched = 0
            targets_list = []
            for item in theme_stocks:
                cc = corp_code_map.get(item.stock_code, "")
                if cc:
                    corp_matched += 1
                else:
                    print(
                        f"  [WARN][CORP MAP] {item.stock_name}({item.stock_code}) "
                        f"corp_code 없음 → DART skip 예정"
                    )
                targets_list.append(
                    StockTarget(
                        stock_name=item.stock_name,
                        stock_code=item.stock_code,
                        corp_code=cc,
                    )
                )
            if corp_code_map:
                print(f"  corp_codes.csv: {corp_matched}/{len(targets_list)} 종목 매칭")
            else:
                print("  ⚠️ corp_codes.csv 없음 → DART 수집이 건너뛰어집니다")
            targets = store.save_targets(
                theme_key=theme_key,
                targets=targets_list,
                theme_name=args.theme,
                mode="overwrite",
            )
            print(f"  ✅ {len(targets)} 종목 자동 발견 → theme_targets 저장 완료")

        except Exception as e:
            print(f"  ⚠️ 테마 자동 수집 실패: {e}")
            print("     theme_pipeline.py를 먼저 실행하거나,")
            print("     --build-and-analyze / --analyze-only를 사용하세요.")
            return {"status": "skipped", "reason": f"auto_discover_failed: {e}"}

    for t in targets[:5]:
        print(f"    - {t.stock_name} ({t.stock_code})")
    if len(targets) > 5:
        print(f"    ... +{len(targets) - 5} more")

    enabled_sources = [s.strip() for s in args.enabled_sources.split(",") if s.strip()]
    raw_output_dir = Path(args.data_dir) / "raw"
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    ingestion_service = IngestionService()
    dart_api_key = os.getenv("DART_API_KEY", "")
    collect_stats = {"docs": 0, "market": 0}

    for target in targets:
        try:
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
                    raw_output_dir=str(raw_output_dir),
                )
            )
            collect_stats["docs"] += len(result.documents)
            collect_stats["market"] += len(result.market_records)
            print(
                f"  [COLLECT] {target.stock_name}({target.stock_code}) "
                f"docs={len(result.documents)} market={len(result.market_records)}"
            )
        except Exception as e:
            print(f"  [ERROR] {target.stock_name}: {e}")

    return {"status": "done", **collect_stats}


def _step_build(args, theme_key: str) -> dict:
    """Step 2: Layer2 Build + Canonical RAG Sync."""
    print("\n" + "=" * 60)
    print("🔨 Step 2: Layer2 Build + Canonical RAG Sync")
    print("=" * 60)

    from src.rag.raw_layer2_builder import RawLayer2Builder

    builder = RawLayer2Builder(data_dir=args.data_dir)
    result = builder.rebuild_theme(
        theme_key=theme_key,
        update_mode=args.mode,
    )

    print(f"  Raw docs:      {result['raw_docs_count']}")
    print(f"  Final records: {result['final_records_count']}")
    canonical = result.get("canonical_stats", {})
    print(f"  Canonical idx: {canonical.get('corpus_count', 0)} records")

    return {
        "status": "done",
        "raw_docs": result["raw_docs_count"],
        "final_records": result["final_records_count"],
        "canonical_count": canonical.get("corpus_count", 0),
    }


def _step_analyze(args, theme_key: str) -> dict:
    """Step 3: Agent Analysis (via AutonomousRunner or direct)."""
    print("\n" + "=" * 60)
    print("🧠 Step 3: Agent Analysis")
    print("=" * 60)

    # Check if canonical index exists
    from src.rag.canonical_retriever import CanonicalRetriever

    retriever = CanonicalRetriever(data_dir=args.data_dir)
    if not retriever.available_themes:
        print("  ⚠️ Canonical index가 없습니다. --build-and-analyze 사용 권장.")
        return {"status": "skipped", "reason": "no_canonical_index"}

    # Load watchlist
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"  ⚠️ Config file not found: {config_path}")
        return {"status": "skipped", "reason": "no_config"}

    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        watchlist = config.get("watchlist", [])
        if not watchlist:
            print("  ⚠️ Watchlist is empty")
            return {"status": "skipped", "reason": "empty_watchlist"}

        print(f"  Watchlist: {len(watchlist)} stocks")
        for item in watchlist:
            print(f"    - {item.get('name', '?')} ({item.get('code', '?')})")

        # Run analysis via AutonomousRunner
        from src.runner.autonomous_runner import AutonomousRunner

        runner = AutonomousRunner(config_path=str(config_path))
        results = runner.run_once()

        return {
            "status": "done",
            "analyzed_count": len(results) if results else 0,
        }

    except ImportError as e:
        print(f"  ⚠️ Analysis dependency missing: {e}")
        return {"status": "skipped", "reason": f"import_error: {e}"}
    except Exception as e:
        print(f"  ⚠️ Analysis error: {e}")
        return {"status": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="HQA 통합 배치 파이프라인: 수집 → 빌드 → 분석",
    )
    parser.add_argument("--theme", required=True, help="Theme keyword")
    parser.add_argument("--theme-key", default="", help="Explicit theme key")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--config", default="./config/watchlist.yaml", help="Watchlist config")

    # Pipeline scope
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--full", action="store_true", help="수집 + 빌드 + 분석")
    scope.add_argument("--collect-and-build", action="store_true", help="수집 + 빌드")
    scope.add_argument("--build-and-analyze", action="store_true", help="빌드 + 분석")
    scope.add_argument("--analyze-only", action="store_true", help="분석만")

    # Collection params
    parser.add_argument("--max-news", type=int, default=20)
    parser.add_argument("--forum-pages", type=int, default=3)
    parser.add_argument("--chart-pages", type=int, default=5)
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20261231")
    parser.add_argument("--enabled-sources", default="news,dart,forum")
    parser.add_argument("--corp-codes-csv", default="./corp_codes.csv",
                        help="stock_code→corp_code CSV (DART 수집에 필요)")
    parser.add_argument("--theme-max-stocks", type=int, default=30,
                        help="Auto-discover: max stocks per theme")
    parser.add_argument("--theme-max-pages", type=int, default=10,
                        help="Auto-discover: max pages to scan")

    # Build params
    parser.add_argument(
        "--mode",
        default="append-new-stocks",
        choices=["append-new-stocks", "overwrite"],
    )

    args = parser.parse_args()

    # Default to --full if no scope specified
    if not any([args.full, args.collect_and_build, args.build_and_analyze, args.analyze_only]):
        args.full = True

    # Resolve theme key
    if args.theme_key:
        theme_key = args.theme_key
    else:
        try:
            from src.ingestion.theme_targets import make_theme_key
            theme_key = make_theme_key(args.theme, args.theme)
        except ImportError:
            theme_key = args.theme

    print("=" * 60)
    print(f"🚀 HQA Pipeline: theme={args.theme}, key={theme_key}")
    mode = (
        "full" if args.full
        else "collect+build" if args.collect_and_build
        else "build+analyze" if args.build_and_analyze
        else "analyze-only"
    )
    print(f"   Mode: {mode}")
    print("=" * 60)

    t0 = time.time()
    report = {"theme_key": theme_key, "mode": mode, "steps": {}}

    # Step 1: Collect
    if args.full or args.collect_and_build:
        report["steps"]["collect"] = _step_collect(args, theme_key)

    # Step 2: Build
    if args.full or args.collect_and_build or args.build_and_analyze:
        report["steps"]["build"] = _step_build(args, theme_key)

    # Step 3: Analyze
    if args.full or args.build_and_analyze or args.analyze_only:
        report["steps"]["analyze"] = _step_analyze(args, theme_key)

    elapsed = time.time() - t0
    report["elapsed_seconds"] = round(elapsed, 2)

    # Save report
    report_dir = Path(args.data_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{theme_key}_pipeline_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"✅ Pipeline complete in {elapsed:.1f}s")
    print(f"📄 Report: {report_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
