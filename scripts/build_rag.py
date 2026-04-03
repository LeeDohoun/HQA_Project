#!/usr/bin/env python3
"""
HQA RAG Build CLI — 한 명령으로 raw → corpus → canonical RAG sync

Usage:
    python scripts/build_rag.py --theme 반도체
    python scripts/build_rag.py --theme 반도체 --mode overwrite
    python scripts/build_rag.py --theme 반도체 --stats

Output:
    - raw count
    - valid doc count
    - indexed count
    - source별 count
"""

from __future__ import annotations

import argparse
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main():
    parser = argparse.ArgumentParser(
        description="HQA RAG Build CLI — raw → corpus → canonical RAG sync",
    )
    parser.add_argument(
        "--theme",
        type=str,
        required=True,
        help="Theme key (e.g., '반도체', '전기차')",
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
        default="./data",
        help="Data directory (default: ./data)",
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

    from src.rag.raw_layer2_builder import RawLayer2Builder

    builder = RawLayer2Builder(data_dir=args.data_dir)

    if args.stats_only:
        _show_stats(args)
        return

    print("=" * 60)
    print(f"🔨 RAG Build: theme_key={args.theme}, mode={args.mode}")
    print("=" * 60)

    result = builder.rebuild_theme(
        theme_key=args.theme,
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
        _show_stats(args)


def _show_stats(args):
    """Show canonical index statistics."""
    from src.rag.canonical_retriever import CanonicalRetriever

    retriever = CanonicalRetriever(data_dir=args.data_dir)
    stats = retriever.get_stats()

    print("=" * 60)
    print("📊 Canonical Index Statistics")
    print("=" * 60)
    print(f"  Total themes: {stats['total_themes']}")

    for theme, theme_stats in stats.get("themes", {}).items():
        print(f"\n  📁 Theme: {theme}")
        print(f"    Total records: {theme_stats['total_records']}")
        for source, count in sorted(theme_stats.get("source_counts", {}).items()):
            print(f"    {source}: {count}")

    if not stats.get("themes"):
        print("  (No canonical indexes found)")

    print("=" * 60)


if __name__ == "__main__":
    main()
