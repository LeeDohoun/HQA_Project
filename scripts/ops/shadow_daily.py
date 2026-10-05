"""Record the latest verified month-end and evaluate HC002 locally; opt-in writes."""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.forward import hegemony_shadow as shadow
from src.ingestion import krx_market
from src.ingestion.storage import atomic_write, file_lock


def daily_shadow(data_dir, *, execute=False):
    """A refusal is visible in JSON; evaluations still run. Never reconstruct."""
    data_dir = Path(data_dir)
    directory = data_dir / shadow.RECORD_DIR
    summary = {"status": "ok" if execute else "dry_run", "dry_run": not execute,
               "started_at": shadow._now().isoformat(), "latest_krx_day": None,
               "decision": {"status": "skipped", "reason": "no_krx_data"}}
    lock = file_lock(directory / ".daily.lock") if execute else nullcontext()
    with lock:
        try:
            days = shadow._price_days(data_dir)
            if days:
                day = days[-1]
                summary["latest_krx_day"] = day.date().isoformat()
                path = directory / "decisions" / f"{day:%Y%m}.json"
                if path.exists():
                    summary["decision"] = {"status": "skipped", "reason": "already_recorded"}
                elif not shadow.is_decision_day(day):
                    summary["decision"] = {"status": "skipped", "reason": "not_month_end"}
                else:
                    rows = krx_market.load_universe(day.date().isoformat(), data_dir)
                    if not rows or any(row.get("calendar_status") != "verified"
                                       or row.get("trade_date") != day.date().isoformat() for row in rows):
                        summary["decision"] = {"status": "skipped", "reason": "incomplete_krx_day"}
                    else:
                        record = shadow.decide(day, data_dir, execute=execute, bounded_window=True)
                        summary["decision"] = {"status": "recorded" if execute else "planned",
                            "date": record["decision_date"], "recorded_at": record["recorded_at"],
                            "holdings_count": len(record["holdings"])}
        except (ValueError, OSError, KeyError) as error:
            summary.update(status="error", decision={"status": "refused", "reason": str(error)})
        try:
            evaluation = shadow.evaluate(data_dir, execute=execute, bounded_window=True)
            summary["evaluation"] = {"status": "ok", "evaluated_count": evaluation["summary"]["count"],
                                     "pending_count": len(evaluation["pending"])}
        except (ValueError, OSError, KeyError) as error:
            summary.update(status="error", evaluation={"status": "error", "reason": str(error)})
        summary.update(finished_at=shadow._now().isoformat(),
                       peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        if execute:
            atomic_write(directory / "_last_run.json", shadow._json(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("HQA_DATA_DIR", shadow.PROJECT_ROOT / "data")))
    parser.add_argument("--execute", action="store_true", help="Write shadow records and evaluation")
    args = parser.parse_args(argv)
    print(json.dumps(daily_shadow(args.data_dir, execute=args.execute), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
