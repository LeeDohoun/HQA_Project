"""Strategy runtime CLI (M8). Read-only by default; files and network only with --execute.

    python -m scripts.ops.strategy_runtime status
    python -m scripts.ops.strategy_runtime plan --strategy hf001_f_both --session 2026-11-02
    python -m scripts.ops.strategy_runtime shadow-fill --session 2026-11-02 --execute
    python -m scripts.ops.strategy_runtime publish-status --execute
    python -m scripts.ops.strategy_runtime submit --strategy hf001_f_both --session 2026-11-02 --phase OPEN --execute
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.live import runtime  # noqa: E402


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--repo-root", default=str(REPO_ROOT))
    root.add_argument("--data-dir", default=None, help="default: $HQA_DATA_DIR or <repo>/data")
    # The same options are accepted after the subcommand; SUPPRESS keeps the root value otherwise.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-root", default=argparse.SUPPRESS)
    common.add_argument("--data-dir", default=argparse.SUPPRESS)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status", parents=[common], help="every strategy's status payload (read-only)")
    plan = commands.add_parser("plan", parents=[common], help="order list for one strategy and session")
    plan.add_argument("--strategy", required=True)
    plan.add_argument("--session", required=True)
    plan.add_argument("--phase", choices=("OPEN", "CLOSE"))
    plan.add_argument("--execute", action="store_true", help="write data/live/<id>/plans/<date>_<phase>.json")
    fill = commands.add_parser("shadow-fill", parents=[common], help="virtual fills and NAV for one session")
    fill.add_argument("--session", required=True)
    fill.add_argument("--strategy")
    fill.add_argument("--execute", action="store_true", help="write the immutable ledger entry")
    publish = commands.add_parser("publish-status", parents=[common], help="POST status payloads to the backend")
    publish.add_argument("--execute", action="store_true")
    submit = commands.add_parser("submit", parents=[common], help="PAPER batch order (effective stage paper only)")
    submit.add_argument("--strategy", required=True)
    submit.add_argument("--session", required=True)
    submit.add_argument("--phase", choices=("OPEN", "CLOSE"), required=True)
    submit.add_argument("--execute", action="store_true", help="fetch the account and send the batch")
    submit.add_argument("--validate", action="store_true", help="with --execute: send dryRun=true")
    submit.add_argument("--max-buy-to-deposit", action="store_true",
                        help="refuse (never resize) when buys exceed deposit cash before sells settle")
    daily = commands.add_parser("daily", parents=[common], help="scheduled morning/evening run")
    daily.add_argument("--session", help="YYYY-MM-DD (default: today KST)")
    daily.add_argument("--when", choices=("morning", "evening"),
                       help="default: evening at/after 16:30 KST, otherwise morning")
    daily.add_argument("--execute", action="store_true", help="write ledger/plans/run summary and send")
    alert = commands.add_parser("alerts", parents=[common], help="evaluate and send operator alerts")
    alert.add_argument("--execute", action="store_true", help="append to the alert file and configured channels")
    alert.add_argument("--failure-unit", help="record a critical alert for a failed systemd unit")
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    ctx = runtime.load_context(args.repo_root, args.data_dir)
    if args.command == "status":
        result = runtime.status_command(ctx)
        ok = True
    elif args.command == "plan":
        result = runtime.plan_command(ctx, args.strategy, args.session, args.phase, execute=args.execute)
        ok = result["status"] != "error"
    elif args.command == "shadow-fill":
        result = runtime.shadow_fill_command(ctx, args.session, args.strategy, execute=args.execute)
        ok = result["status"] == "ok"
    elif args.command == "publish-status":
        result = runtime.publish_status_command(ctx, execute=args.execute)
        ok = result["status"] != "error"
    elif args.command == "daily":
        result, code = runtime.daily_command(ctx, session=args.session, when=args.when, execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    elif args.command == "alerts":
        result = runtime.alerts_command(ctx, execute=args.execute, failure_unit=args.failure_unit)
        ok = result["status"] == "ok"
    else:
        if args.validate and not args.execute:
            print("--validate requires --execute", file=sys.stderr)
            return 2
        result = runtime.submit_command(ctx, args.strategy, args.session, args.phase, execute=args.execute,
                                        validate=args.validate, max_buy_to_deposit=args.max_buy_to_deposit)
        ok = result["status"] in ("preview", "submitted", "deduplicated")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
