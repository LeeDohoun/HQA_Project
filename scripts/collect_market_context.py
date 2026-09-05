"""Collect shared KRX price indices without broker credentials or LLM calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config.settings import get_data_dir, load_project_env
from src.ingestion.krx_benchmarks import KrxBenchmarkCollector, save_benchmark_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect dated KRX market/industry price indices")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Last requested date, before today in Korea")
    parser.add_argument("--series", nargs="+", choices=("KOSPI", "KOSDAQ"), default=["KOSPI", "KOSDAQ"])
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    load_project_env()
    data_dir = Path(args.data_dir) if args.data_dir else get_data_dir()
    rows = KrxBenchmarkCollector().collect_daily(args.from_date, args.to_date, tuple(args.series))
    path = data_dir / "market_context" / "benchmarks.jsonl"
    saved = save_benchmark_records(rows, path)
    print(json.dumps({"collected_records": len(rows), "saved_records": saved,
                      "path": str(path), "series": args.series}))


if __name__ == "__main__":
    main()
