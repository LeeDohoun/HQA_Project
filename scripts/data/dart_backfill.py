"""Plan a historical DART backfill; only --execute loads environment settings."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root, load_project_env
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_backfill import DEFAULT_DATA_DIR, DEFAULT_MAX_REQUESTS, backfill


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill KOSPI and KOSDAQ DART listings and full bodies")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Last date, before today in Korea")
    parser.add_argument("--stage", choices=("all", "list", "details"), default="all")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help="Request cap per KST day, across runs (default: 18,999)")
    parser.add_argument("--data-dir", help="Data directory; dry-run default is the checkout's data directory")
    parser.add_argument("--execute", action="store_true", help="Load environment, call DART and save progress")
    args = parser.parse_args()
    api_key, data_dir = None, Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    if args.execute:
        load_project_env()
        api_key = os.getenv("DART_API_KEY")
        if args.data_dir is None:
            data_dir = Path(os.getenv("HQA_DATA_DIR", str(DEFAULT_DATA_DIR)))
        data_dir = data_dir.expanduser()
        if not data_dir.is_absolute():
            data_dir = get_project_root() / data_dir
    try:
        summary = backfill(args.from_date, args.to_date, stage=args.stage,
            max_requests=args.max_requests, execute=args.execute, api_key=api_key, data_dir=data_dir)
    except (ValueError, DartAPIError):
        # Exception text may originate in saved files or configuration; never echo it.
        print(json.dumps({"status": "error", "error": "DART backfill configuration or archive is invalid"}))
        raise SystemExit(1) from None
    if args.execute:
        print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
