"""QP001 raw DART multi-company financials; offline unless fetch --execute."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_data import dart_multi_raw as collector


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "fetch", "link-check"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=collector.DEFAULT_DATA_DIR)
        command.add_argument("--from-year", type=int, default=None if name == "link-check" else 2015)
        command.add_argument("--to-year", type=int, default=None if name == "link-check" else 2025)
        if name != "link-check":
            command.add_argument("--corp-codes", type=Path, default=collector.DEFAULT_CORP_CODES)
            command.add_argument("--listing-dir", type=Path)
        if name == "fetch":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--execute", action="store_true")
            mode.add_argument("--dry-run", action="store_true")
            command.add_argument("--max-requests", type=int, default=collector.DEFAULT_MAX_REQUESTS)
            command.add_argument("--daily-cap", type=int, default=collector.DEFAULT_DAILY_CAP)
    args = parser.parse_args(argv)
    kwargs = dict(data_dir=args.data_dir, from_year=args.from_year, to_year=args.to_year)
    try:
        if args.command == "link-check":
            result = collector.link_check(**kwargs)
        else:
            kwargs.update(corp_codes_path=args.corp_codes, listing_dir=args.listing_dir)
            if args.command == "plan":
                result = collector.plan(**kwargs)
            else:
                api_key = None
                if args.execute:
                    from src.config.settings import load_project_env
                    load_project_env()
                    api_key = os.getenv("DART_API_KEY")
                result = collector.fetch(**kwargs, execute=args.execute, api_key=api_key,
                    max_requests=args.max_requests, daily_cap=args.daily_cap)
    except (ValueError, OSError, KeyError, TypeError) as error:
        # Never print a transport exception, URL with credentials, or raw payload.
        message = str(error) if type(error) is ValueError else "QP001 configuration or archive is invalid; execution stopped."
        print(json.dumps({"status": "error", "error_type": type(error).__name__, "error": message}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
