"""Record or evaluate HC002 shadow portfolios using local archives only."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.forward import hegemony_shadow as shadow


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("decide", "evaluate", "status"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path,
                             default=Path(os.environ.get("HQA_DATA_DIR", PROJECT_ROOT / "data")))
        if name != "status":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--execute", action="store_true", help="Write local research records")
            mode.add_argument("--dry-run", action="store_true", help="Read-only (default)")
            command.add_argument("--bounded-window", action="store_true",
                                 help="Load only HC002 price columns and holding windows")
        if name == "decide":
            command.add_argument("--date", help="Month-end YYYY-MM-DD (default: today in KST)")
            command.add_argument("--backfill-label", action="store_true", help="Mark reconstructed; exclude from forward statistics")
            command.add_argument("--append-rerun", action="store_true", help="Keep a differing rerun in a separate audit archive")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            payload = shadow.status(args.data_dir)
        elif args.command == "evaluate":
            payload = {"dry_run": not args.execute, "evaluation": shadow.evaluate(
                args.data_dir, execute=args.execute, bounded_window=args.bounded_window)}
        else:
            day = args.date or shadow._now().date().isoformat()
            if args.date is None and not shadow.is_decision_day(day):
                print("not a decision day")
                return 0
            record = shadow.decide(day, args.data_dir, execute=args.execute,
                                   backfill_label=args.backfill_label, append_rerun=args.append_rerun,
                                   bounded_window=args.bounded_window)
            payload = {"dry_run": not args.execute, "record": record}
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
