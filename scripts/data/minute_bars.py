"""Plan historical/event minute bars; external calls require --execute."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import load_project_env
from src.ingestion.minute_bars import MinuteBarClient, plan_event_window


def _csv(path: str, fields: set[str]) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not fields.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV requires columns: {', '.join(sorted(fields))}")
        return list(reader)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect minute bars through the PAPER backend")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pairs-file", help="CSV with stock_code,date (YYYY-MM-DD)")
    source.add_argument("--events-file", help="CSV with stock_code,date; expands +/-1 trading session")
    parser.add_argument("--sessions-file", help="Actual trading sessions CSV with date; required for events")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--requests-per-second", type=float, default=2.0, help="Backend requests per second")
    parser.add_argument("--execute", action="store_true", help="Request and save bars; default only prints the plan")
    args = parser.parse_args()
    if args.events_file:
        if not args.sessions_file:
            parser.error("--events-file requires --sessions-file with actual trading dates")
        pairs = plan_event_window(_csv(args.events_file, {"stock_code", "date"}),
                                  [row["date"] for row in _csv(args.sessions_file, {"date"})])
    else:
        pairs = [(row["stock_code"], row["date"]) for row in _csv(args.pairs_file, {"stock_code", "date"})]
    if args.execute:
        load_project_env()
    summary = MinuteBarClient(data_dir=args.data_dir).backfill(
        pairs, args.user_id, execute=args.execute, requests_per_second=args.requests_per_second,
    )
    if args.execute:
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
