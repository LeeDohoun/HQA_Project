"""python -m backtesting.experiments.lh003 (offline dry-run by default)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import ordering

from . import EXPERIMENT_DIR
from .calendar import plan
from .environment import verify_environment
from .evaluate import evaluate_saved
from .executions import record_executions
from .runner import LlmRunner
from .storage import aware, now
from .workflow import load_week, run_week, seal_holdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    for name in ("dry-run", "verify-environment", "seal-holdout", "run-week", "record-executions", "evaluate"):
        mode.add_argument("--" + name, action="store_true")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--environment-report", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sync-manifest", type=Path)
    parser.add_argument("--restart-evidence", type=Path)
    parser.add_argument("--decision-day", help="YYYY-MM-DD; default latest fixed scheduled decision")
    parser.add_argument("--call-timeout", type=float, default=300)
    parser.add_argument("--operator-note")
    parser.add_argument("--synthetic", action="store_true", help="dry checks only: generated fixture outside data/")
    parser.add_argument("--real-format-trial", action="store_true",
                        help="explicitly spend six of the eight synthetic-format calls through the real Codex path")
    parser.add_argument("--audit-price-corrections", action="store_true", help="record-executions audit only; never affects verdict")
    args = parser.parse_args(argv)
    if args.call_timeout <= 0:
        parser.error("--call-timeout must be positive")
    if args.audit_price_corrections and not args.record_executions:
        parser.error("--audit-price-corrections requires --record-executions")
    if args.real_format_trial and (not args.verify_environment or args.state_dir is None):
        parser.error("--real-format-trial requires --verify-environment and a durable --state-dir")
    if args.synthetic and any((args.seal_holdout, args.run_week, args.record_executions, args.evaluate)):
        parser.error("--synthetic is restricted to dry/environment checks")
    root = args.state_dir if args.state_dir is not None else args.data_dir / "research/lh003"
    try:
        if args.seal_holdout:
            if args.environment_report is None:
                parser.error("--seal-holdout requires --environment-report")
            result = seal_holdout(data_dir=args.data_dir, root=root, snapshot_path=args.snapshot_dir,
                                  environment_report=args.environment_report)
        elif args.run_week:
            available = [r for r in plan() if aware(r["deadline"]).date() <= now().date()
                         or r["decision_date"] <= now().date().isoformat()]
            if args.decision_day is None and not available:
                raise ValueError("the fixed LH003 decision schedule has not started")
            day = args.decision_day or available[-1]["decision_date"]
            result = run_week(data_dir=args.data_dir, root=root, decision_day=day, sync_manifest=args.sync_manifest,
                              runner=LlmRunner(root, timeout=args.call_timeout), operator_note=args.operator_note)
        elif args.record_executions:
            if args.sync_manifest is None:
                parser.error("--record-executions requires --sync-manifest with the evaluation as_of and file hashes")
            with ordering.execution_lock(PROJECT_ROOT):
                decisions = [load_week(root, i) for i in range(52)]
                if args.decision_day:
                    decisions = [d for d in decisions if d and d["decision_date"] == args.decision_day]
                records = record_executions(data_dir=args.data_dir, root=root, decisions=decisions,
                                            sync_manifest=args.sync_manifest, audit=args.audit_price_corrections)
                result = {"raw_execution_cohorts": len(records), "costs_evaluated": False,
                          "audit_only": args.audit_price_corrections}
        elif args.evaluate:
            result = evaluate_saved(root=root)
        else:
            report = args.report
            if args.verify_environment and report is None:
                report = EXPERIMENT_DIR / "reports" / ("environment_" + now().strftime("%Y%m%dT%H%M%S%f") + ".json")
            result = verify_environment(data_dir=args.data_dir, report_path=report, snapshot_path=args.snapshot_dir,
                                        synthetic=args.synthetic, sync_manifest=args.sync_manifest,
                                        restart_evidence=args.restart_evidence, timeout=args.call_timeout,
                                        real_format_trial=args.real_format_trial, state_root=root)
            if report:
                result = {**result, "report_file": str(report)}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
