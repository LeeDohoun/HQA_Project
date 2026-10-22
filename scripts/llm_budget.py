"""Operator tool for the LLM budget ledger: inspect, reconcile and review.

Reconcile unresolved requests only from the provider's usage records; the
ledger is never deleted or reset to resume work.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import get_data_dir, load_project_env
from src.utils.llm_budget import LLMBudgetLedger


def _ledger(path: str | None) -> LLMBudgetLedger:
    target = path or os.getenv("HQA_LLM_BUDGET_PATH") or str(get_data_dir() / "llm_budget.sqlite3")
    if not Path(target).exists():
        raise SystemExit(f"budget ledger not found: {target}")
    return LLMBudgetLedger(target, monthly_limit_usd=os.getenv("HQA_LLM_MONTHLY_BUDGET_USD", "100"),
                           operating_target_usd=os.getenv("HQA_LLM_OPERATING_TARGET_USD", "90"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", help="Ledger path (default: HQA_LLM_BUDGET_PATH or data/llm_budget.sqlite3)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Budget snapshot, unresolved requests and unreviewed overruns")
    settle = commands.add_parser("settle", help="Settle a sent/unknown request with provider-reported usage")
    settle.add_argument("request_id")
    for name in ("input-tokens", "output-tokens"):
        settle.add_argument(f"--{name}", type=int, required=True)
    for name in ("cached-tokens", "cache-write-tokens", "reasoning-tokens"):
        settle.add_argument(f"--{name}", type=int, default=0)
    release = commands.add_parser("release", help="Release a reservation that was never sent (state 'reserved')")
    release.add_argument("request_id")
    release.add_argument("--min-age-seconds", type=int, default=600,
                         help="Refuse younger reservations, which a running process may still send (default 600)")
    review = commands.add_parser("acknowledge-overrun", help="Record review of an overrun and lift the block")
    review.add_argument("request_id")
    review.add_argument("--note", required=True, help="What was corrected (e.g. updated price table)")
    args = parser.parse_args(argv)
    load_project_env()
    ledger = _ledger(args.path)
    if args.command == "settle":
        ledger.settle(args.request_id, input_tokens=args.input_tokens, output_tokens=args.output_tokens,
                      cached_tokens=args.cached_tokens, cache_write_tokens=args.cache_write_tokens,
                      reasoning_tokens=args.reasoning_tokens)
    elif args.command == "release":
        row = next((row for row in ledger.unresolved() if row["request_id"] == args.request_id), None)
        if row is None or row["state"] != "reserved":
            raise SystemExit(f"not an unsent reservation: {args.request_id} (settle sent/unknown requests instead)")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
        if age < args.min_age_seconds:
            raise SystemExit(f"reservation is {int(age)} s old; a running process may still send it")
        ledger.release_unsent(args.request_id)
    elif args.command == "acknowledge-overrun":
        ledger.acknowledge_overrun(args.request_id, args.note)
    report = {"snapshot": ledger.snapshot(), "unresolved": ledger.unresolved(), "overruns": ledger.overruns()}
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
