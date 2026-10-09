"""Run historical DART listing and priority details through yesterday KST."""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_backfill import SELECTION_PATTERNS, backfill

KST = ZoneInfo("Asia/Seoul")
DEFAULT_MAX_REQUESTS = 17_000
DEFAULT_PRIORITY_CATEGORIES = ("contract", "convertible_bond", "bond_subtype", "capital_raise")


def daily_backfill(data_dir: str | Path, *, execute=False, api_key=None,
                   max_requests=DEFAULT_MAX_REQUESTS,
                   priority_categories=DEFAULT_PRIORITY_CATEGORIES, clock=None) -> dict:
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("DART max_requests must be a positive integer")
    if not priority_categories or any(name not in dict(SELECTION_PATTERNS) for name in priority_categories):
        raise ValueError("DART priority categories must contain valid category names")
    now = clock if clock is not None else lambda: datetime.now(KST)
    yesterday = now().astimezone(KST).date() - timedelta(days=1)
    summary = {"status": "ok" if execute else "dry_run", "dry_run": not execute,
               "data_dir": str(data_dir), "from_date": "2023-01-01", "to_date": yesterday.isoformat(),
               "max_requests": max_requests, "requests_made": 0, "steps": {}}
    steps = (("listing", "list", None), ("priority_details", "details", priority_categories),
             ("all_details", "details", None))
    for name, stage, categories in steps:
        if summary["status"] in {"quota_reached", "provider_maintenance", "error"}:
            summary["steps"][name] = {"status": "not_run", "reason": summary["status"], "requests_made": 0}
            continue
        # Dry-run backfill prints its own plan; the ops entry point prints one combined summary.
        with redirect_stdout(StringIO()):
            part = backfill(summary["from_date"], summary["to_date"], stage=stage,
                data_dir=data_dir, execute=execute, api_key=api_key, clock=now,
                categories=categories, max_requests=max_requests)
        part.setdefault("requests_made", 0)
        summary["steps"][name] = part
        summary["requests_made"] += part.get("requests_made", 0)
        if part["status"] in {"quota_reached", "provider_maintenance", "error"}:
            summary["status"] = part["status"]
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily DART historical backfill through yesterday KST")
    parser.add_argument("--data-dir")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help="Shared request cap per KST day, across stages and runs (default: 17,000)")
    parser.add_argument("--priority-categories", default=",".join(DEFAULT_PRIORITY_CATEGORIES),
                        help="Comma-separated detail categories in priority order")
    parser.add_argument("--execute", action="store_true", help="Call DART and save progress")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir or os.getenv("HQA_DATA_DIR", "./data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = get_project_root() / data_dir
    api_key = os.getenv("DART_API_KEY") if args.execute else None
    try:
        summary = daily_backfill(data_dir, execute=args.execute, api_key=api_key,
            max_requests=args.max_requests,
            priority_categories=[name.strip() for name in args.priority_categories.split(",")])
    except (ValueError, DartAPIError, OSError):
        # Saved files and configuration may contain secrets; never echo exception text.
        summary = {"status": "error", "error": "DART backfill configuration or archive is invalid"}
    print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
