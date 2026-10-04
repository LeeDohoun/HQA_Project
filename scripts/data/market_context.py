"""Collect shared KRX price indices without broker credentials or LLM calls."""
from __future__ import annotations

import argparse
from calendar import monthrange
from datetime import timedelta
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env
from src.ingestion.krx_benchmarks import KrxBenchmarkCollector, save_benchmark_records, validate_benchmark_range


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect dated KRX market/industry price indices")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Last requested date, before today in Korea")
    parser.add_argument("--series", nargs="+", choices=("KOSPI", "KOSDAQ"), default=["KOSPI", "KOSDAQ"])
    parser.add_argument("--index-names", nargs="+", help="Exact provider index names; omitted to keep all indices")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    load_project_env()
    data_dir = Path(args.data_dir) if args.data_dir else get_data_dir()
    start, end = validate_benchmark_range(args.from_date, args.to_date)
    collector = KrxBenchmarkCollector()
    collector.skipped_rows = 0
    path = data_dir / "market_context" / "benchmarks.jsonl"
    collected = saved = skipped = 0
    options = {"index_names": tuple(args.index_names)} if args.index_names is not None else {}
    while start <= end:
        chunk_end = min(end, start.replace(day=monthrange(start.year, start.month)[1]))
        rows = collector.collect_daily(f"{start:%Y%m%d}", f"{chunk_end:%Y%m%d}", tuple(args.series), **options)
        saved += save_benchmark_records(rows, path)
        collected += len(rows)
        skipped += collector.skipped_rows
        start = chunk_end + timedelta(days=1)
    print(json.dumps({"collected_records": collected, "saved_records": saved, "skipped_rows": skipped,
                      "path": str(path), "series": args.series}))


if __name__ == "__main__":
    main()
