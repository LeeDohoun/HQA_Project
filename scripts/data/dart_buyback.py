"""Plan/fetch BB001 DART originals; fetch is dry-run unless --execute is given."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ingestion import dart_buyback


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "fetch"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=dart_buyback.DEFAULT_DATA_DIR)
        if name == "fetch":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--dry-run", action="store_true")
            mode.add_argument("--execute", action="store_true")
            command.add_argument("--max-requests", type=int, default=dart_buyback.DEFAULT_MAX_REQUESTS)
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = dart_buyback.plan(args.data_dir)
    else:
        # Read credentials only on an explicit fetch; never load .env in a plan.
        result = dart_buyback.fetch(data_dir=args.data_dir, execute=args.execute,
            api_key=os.getenv("DART_API_KEY") if args.execute else None, max_requests=args.max_requests)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
