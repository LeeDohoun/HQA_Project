"""Read recorded PAPER observations. This command never calls an LLM or a broker."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from src.tracing.paper_audit import summarize_runtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--baseline-audit", type=Path)
    parser.add_argument("--budget", type=Path)
    args = parser.parse_args(argv)

    def summarize(path):
        if not path.is_file():
            raise FileNotFoundError(path)
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT kind,payload FROM paper_events ORDER BY id").fetchall()
        return summarize_runtime([{"kind": row[0], "payload": json.loads(row[1])} for row in rows])

    report = {"candidate": summarize(args.audit)}
    if args.baseline_audit:
        report["baseline"] = summarize(args.baseline_audit)
    if args.budget:
        if not args.budget.is_file():
            raise FileNotFoundError(args.budget)
        with sqlite3.connect(args.budget.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT month,state,COUNT(*),SUM(actual_nano),SUM(reserved_nano),"
                              "SUM(input_tokens),SUM(output_tokens),SUM(reasoning_tokens) "
                              "FROM llm_spend GROUP BY month,state ORDER BY month,state").fetchall()
        report["budget"] = [{"utc_month": row[0], "state": row[1], "requests": row[2],
                             "actual_usd": row[3] / 1e9 if row[3] is not None else None,
                             "reserved_usd": row[4] / 1e9, "input_tokens": row[5],
                             "output_tokens": row[6], "reasoning_tokens": row[7]} for row in rows]
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
