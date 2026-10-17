"""FW001 decision/execution rehearsal, never a historical backtest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.forward import fw001


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--rehearsal", action="store_true", help="Allow an earlier month, strictly read-only")
    parser.add_argument("--with-execution", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--append-rerun", action="store_true")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    decision = fw001.decide(args.decision_date, args.data_dir, execute=args.execute,
                            rehearsal=args.rehearsal, append_rerun=args.append_rerun)
    executions = []
    if args.with_execution:
        for m in fw001.hf001.MULTIPLIERS:
            record = fw001.execution(decision, args.data_dir, multiplier=m, execute=args.execute,
                                      rehearsal=args.rehearsal)
            executions.append({"multiplier": m, "av_all": record["av_all"]["status"],
                               "f_both_costs_defined": all(row["round_trip_cost"] is not None
                                                         for row in record["f_both"]["slots"].values())})
    print(json.dumps({"status": "recorded" if args.execute else "dry_run", "decision_date": decision["decision_date"],
        "label": decision["label"], "financial_dates": decision["financial_dates"],
        "universe_counts": {name: len(rule["universe"]) for name, rule in decision["rules"].items()},
        "target_counts": {name: len(rule["targets"]) for name, rule in decision["rules"].items()},
        "vol_uncomputable": {name: rule["vol_uncomputable"] for name, rule in decision["rules"].items()},
        "missing_price_sessions": decision["missing_price_sessions"],
        "input_files": len(decision["input_hashes"]), "executions": executions,
        "runtime_seconds": time.perf_counter() - started,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
        ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
