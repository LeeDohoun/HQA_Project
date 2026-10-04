"""Plan DART polling by default; external requests require --execute.

Without --data-dir, both modes use get_data_dir(), whose settings loader reads
the project .env file so HQA_DATA_DIR resolves identically to execution.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env
from src.ingestion.dart_poller import KST, LIST_URL, DartListingPoller, listing_params


def main() -> None:
    parser = argparse.ArgumentParser(description="Record first-observed DART disclosures")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Poll once (default)")
    mode.add_argument("--loop", action="store_true", help="Poll within the weekday KST window")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--data-dir", help="Override data directory; otherwise load project env settings, including in dry-run")
    parser.add_argument("--execute", action="store_true", help="Load environment and call DART")
    args = parser.parse_args()
    if args.execute:
        load_project_env()
    data_dir = Path(args.data_dir) if args.data_dir else get_data_dir()
    if not args.execute:
        print(json.dumps({"dry_run": True, "url": LIST_URL,
            "params": listing_params(datetime.now(KST).strftime("%Y%m%d"), "[REDACTED]"),
            "mode": "loop" if args.loop else "once", "interval_seconds": args.interval,
            "data_dir": str(data_dir)}, ensure_ascii=False))
        return
    poller = DartListingPoller(os.getenv("DART_API_KEY"), data_dir=data_dir)
    if args.loop:
        poller.run_loop(interval_seconds=args.interval)
    else:
        print(json.dumps(poller.poll_once(), ensure_ascii=False))


if __name__ == "__main__":
    main()
