#!/usr/bin/env python3
from __future__ import annotations

"""Build a cached period RAG snapshot for faster backtest loops."""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtesting.temporal_rag import build_period_snapshot


def _parse_sources(raw: str):
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a period-specific RAG snapshot.")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--theme-key", required=True)
    parser.add_argument("--from-date", required=True, help="YYYYMMDD")
    parser.add_argument("--to-date", required=True, help="YYYYMMDD")
    parser.add_argument("--source-types", default="", help="Comma-separated sources, e.g. news,dart")
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--build-vector", action="store_true")
    args = parser.parse_args()

    result = build_period_snapshot(
        data_dir=args.data_dir,
        theme_key=args.theme_key,
        from_date=args.from_date,
        to_date=args.to_date,
        output_name=args.output_name,
        source_types=_parse_sources(args.source_types),
        build_vector=args.build_vector,
    )

    print(f"[PERIOD RAG] output: {result['output_dir']}")
    print(f"[PERIOD RAG] combined: {result['combined_count']}")
    for source, count in sorted(result["source_counts"].items()):
        print(f"  - {source}: {count}")
    if result["vector_stats"]:
        print("[PERIOD RAG] vector stores:")
        for source, count in sorted(result["vector_stats"].items()):
            print(f"  - {source}: {count}")


if __name__ == "__main__":
    main()
