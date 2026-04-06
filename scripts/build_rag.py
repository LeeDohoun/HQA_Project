#!/usr/bin/env python3
"""
HQA RAG Build CLI — 한 명령으로 raw → corpus → canonical RAG sync

Usage:
    python scripts/build_rag.py --theme 반도체
    python scripts/build_rag.py --theme 반도체 --mode overwrite
    python scripts/build_rag.py --theme 반도체 --stats
    python scripts/build_rag.py --theme-key 반도체 --stats-only

theme_targets가 저장되어 있으면 자동으로 theme_key를 결정합니다.
"""

from __future__ import annotations

import argparse
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.settings import get_data_dir


def _resolve_theme_key(args) -> str:
    """theme_targets가 있으면 저장된 theme_key를 우선 사용."""
    if args.theme_key:
        return args.theme_key

    # Try to resolve via theme_targets
    try:
        from src.ingestion.theme_targets import ThemeTargetStore, make_theme_key

        derived_key = make_theme_key(args.theme, args.theme)
        store = ThemeTargetStore(data_dir=args.data_dir)
        stored_path = store.get_path(derived_key)

        if stored_path.exists():
            meta_path = store.get_meta_path(derived_key)
            if meta_path.exists():
                with meta_path.open("r", encoding="utf-8") as f:
                    meta = json.load(f)
                saved_key = meta.get("theme_key", derived_key)
                targets = store.load_targets(saved_key)
                print(
                    f"[BUILD] theme_targets 발견: "
                    f"key={saved_key}, targets={len(targets)}, "
                    f"path={stored_path}"
                )
                return saved_key

        return derived_key
    except ImportError:
        # Fallback if theme_targets module not available
        return args.theme


def main():
    parser = argparse.ArgumentParser(
        description="HQA RAG Build CLI — raw → corpus → canonical RAG sync",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="",
        help="Theme keyword (e.g., '반도체', '전기차')",
    )
    parser.add_argument(
        "--theme-key",
        type=str,
        default="",
        help="Explicit theme key (overrides --theme)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="append-new-stocks",
        choices=["append-new-stocks", "overwrite"],
        help="Update mode (default: append-new-stocks)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=str(get_data_dir()),
        help="Data directory (default: HQA_DATA_DIR or ./data)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show canonical index stats after build",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Show stats only (no build)",
    )

    args = parser.parse_args()

    if not args.theme and not args.theme_key:
        if args.stats_only:
            _show_stats(args, theme_key=None)
            return
        parser.error("--theme 또는 --theme-key 중 하나가 필요합니다.")

    theme_key = _resolve_theme_key(args)

    from src.rag.raw_layer2_builder import RawLayer2Builder

    builder = RawLayer2Builder(data_dir=args.data_dir)

    if args.stats_only:
        _show_stats(args, theme_key=theme_key)
        return

    print("=" * 60)
    print(f"🔨 RAG Build: theme_key={theme_key}, mode={args.mode}")
    print("=" * 60)

    result = builder.rebuild_theme(
        theme_key=theme_key,
        update_mode=args.mode,
    )

    # Print summary
    print("\n" + "─" * 60)
    print("📊 Build Summary")
    print("─" * 60)
    print(f"  Raw documents loaded:  {result['raw_docs_count']}")
    print(f"  Built records (pre-dedup): {result['built_records_count']}")
    print(f"  Final records (deduped):   {result['final_records_count']}")
    print(f"  Combined corpus count:     {result['combined_count']}")

    # Skipped invalid
    skipped = result.get("skipped_invalid_count_by_source", {})
    if any(v > 0 for v in skipped.values()):
        print("\n  ⚠️ Skipped invalid documents:")
        for source, count in skipped.items():
            if count > 0:
                print(f"    {source}: {count}")

    # Per-source counts
    doc_counts = result.get("document_source_counts", {})
    if doc_counts:
        print("\n  📁 Document source counts:")
        for source, count in sorted(doc_counts.items()):
            print(f"    {source}: {count}")

    # Vector stats
    vector_stats = result.get("vector_stats", {})
    if vector_stats:
        print("\n  🔢 Vector store sizes:")
        for source, size in sorted(vector_stats.items()):
            print(f"    {source}: {size}")

    # Canonical stats
    canonical = result.get("canonical_stats", {})
    if canonical:
        print("\n  ✨ Canonical RAG index:")
        print(f"    Corpus count:     {canonical.get('corpus_count', 0)}")
        print(f"    Vector store:     {canonical.get('vector_store_path', 'N/A')}")
        print(f"    BM25 index:       {canonical.get('bm25_path', 'N/A')}")

    # Market stats
    market_stats = result.get("market_stats", {})
    if market_stats:
        print("\n  📈 Market data:")
        for source, count in sorted(market_stats.items()):
            print(f"    {source}: {count}")

    print("\n" + "=" * 60)
    print("✅ Build complete!")
    print("=" * 60)

    if args.stats:
        print()
        _show_stats(args, theme_key=theme_key)


def _show_stats(args, theme_key=None):
    """Show canonical index statistics.

    Args:
        args: CLI arguments (must have data_dir)
        theme_key: If given, filter stats to this specific theme
    """
    from src.rag.canonical_retriever import CanonicalRetriever

    retriever = CanonicalRetriever(data_dir=args.data_dir)
    stats = retriever.get_stats()

    print("=" * 60)
    if theme_key:
        print(f"📊 Canonical Index Statistics (theme: {theme_key})")
    else:
        print("📊 Canonical Index Statistics (all themes)")
    print("=" * 60)

    themes_to_show = stats.get("themes", {})
    if theme_key:
        # Filter to specific theme
        if theme_key in themes_to_show:
            themes_to_show = {theme_key: themes_to_show[theme_key]}
        else:
            print(f"  ⚠️ Theme '{theme_key}' not found in canonical index")
            themes_to_show = {}
    else:
        print(f"  Total themes: {stats['total_themes']}")

    for theme, theme_stats in themes_to_show.items():
        print(f"\n  📁 Theme: {theme}")
        print(f"    Total records: {theme_stats['total_records']}")
        for source, count in sorted(theme_stats.get("source_counts", {}).items()):
            print(f"    {source}: {count}")

    # Show theme_targets info
    try:
        from src.ingestion.theme_targets import ThemeTargetStore
        from pathlib import Path

        store = ThemeTargetStore(data_dir=args.data_dir)
        targets_root = store.root
        if targets_root.exists():
            if theme_key:
                # Show only specific theme's targets
                meta_path = store.get_meta_path(theme_key)
                if meta_path.exists():
                    with meta_path.open("r", encoding="utf-8") as f:
                        meta = json.load(f)
                    targets = store.load_targets(theme_key)
                    print(f"\n  📋 Theme targets: {len(targets)} stocks")
                    print(f"    Updated: {meta.get('updated_at', '?')}")
            else:
                print(f"\n  📋 Theme targets directory: {targets_root}")
                for meta_file in targets_root.glob("*.meta.json"):
                    with meta_file.open("r", encoding="utf-8") as f:
                        meta = json.load(f)
                    print(
                        f"    {meta.get('theme_key', '?')}: "
                        f"{meta.get('target_count', 0)} targets, "
                        f"updated={meta.get('updated_at', '?')}"
                    )
    except ImportError:
        pass

    if not themes_to_show:
        print("  (No canonical indexes found)")

    print("=" * 60)


if __name__ == "__main__":
    main()
