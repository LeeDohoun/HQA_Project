"""Plan quarterly financial backfills; only --execute loads project env."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root, load_project_env
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_quarterly import DEFAULT_DATA_DIR, DEFAULT_MAX_REQUESTS, backfill


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill point-in-time quarterly DART financials")
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help="Request cap per KST day across runs (default: 3000)")
    parser.add_argument("--execute", action="store_true", help="Load environment, call DART and save progress")
    args = parser.parse_args()
    api_key, data_dir = None, DEFAULT_DATA_DIR
    if args.execute:
        load_project_env()
        api_key = os.getenv("DART_API_KEY")
        data_dir = Path(os.getenv("HQA_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
        if not data_dir.is_absolute():
            data_dir = get_project_root() / data_dir
    try:
        summary = backfill(args.from_year, args.to_year, execute=args.execute,
            max_requests=args.max_requests, api_key=api_key, data_dir=data_dir)
    except (ValueError, OSError, DartAPIError):
        print(json.dumps({"status": "error", "error": "DART quarterly configuration or archive is invalid"}))
        raise SystemExit(1) from None
    if args.execute:
        print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
