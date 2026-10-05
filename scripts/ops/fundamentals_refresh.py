"""Weekly versioned DART financials and missing KRX company profiles; dry by default."""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ingestion import dart_company, dart_quarterly, krx_market
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_poller import KST, _kst
from src.ingestion.storage import atomic_write, file_lock

DEFAULT_MAX_REQUESTS = 400
COMPANY_WEEKLY_CAP = 200
# Backfill (17,000) plus refreshers must leave 2,000/day for the poller and margin.
BATCH_DAILY_CAP = 18_000
LAST_RUN = Path("fundamentals/_refresh_last_run.json")


def _budgets(data_dir, now, max_requests):
    day = now.date()
    start = day - timedelta(days=day.weekday())
    quarterly = dart_quarterly._quota(data_dir / "fundamentals/dart_quarterly")["days"]
    company = dart_company._quota(data_dir / "reference/dart_company/_quota.json")["days"]
    # dart_full and quarterly use the same date/counter schema; this validates both.
    backfill = dart_quarterly._quota(data_dir / "disclosures/dart_full")["days"]
    budgets = {}
    for name, days, cap in (("quarterly", quarterly, max_requests),
                             ("company", company, COMPANY_WEEKLY_CAP)):
        parse = (lambda value: datetime.strptime(value, "%Y%m%d").date()) if name == "company" else (
            lambda value: datetime.fromisoformat(value).date())
        used_week = sum(row["requests"] for key, row in days.items() if start <= parse(key) <= day)
        key = day.strftime("%Y%m%d") if name == "company" else day.isoformat()
        today = days.get(key, {})
        budgets[name] = {"used_week": used_week, "used_today": today.get("requests", 0),
                         "total_requests": sum(row["requests"] for row in days.values()),
                         "remaining": max(0, cap - used_week)}
    full_today = backfill.get(day.isoformat(), {})
    blocked = (full_today.get("provider_limited", False)
               or quarterly.get(day.isoformat(), {}).get("provider_limited", False)
               or company.get(day.strftime("%Y%m%d"), {}).get("provider_blocked", False))
    used = full_today.get("requests", 0) + sum(row["used_today"] for row in budgets.values())
    budgets["shared"] = {"requests_today": used, "provider_limited": blocked,
                         "remaining": 0 if blocked else max(0, BATCH_DAILY_CAP - used)}
    return budgets


def refresh_fundamentals(data_dir, *, execute=False, max_requests=DEFAULT_MAX_REQUESTS,
                         api_key=None, clock=None, session=None, sleeper=None):
    """Use module attempt ledgers, including failures, for caps across weekly reruns.

    The historical backfill lock serializes shared-key budget checks with that
    job. The poller is independent and retains the reserved daily allowance.
    Completed quarterly batches are explicitly refreshed to discover corrections.
    """
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("max_requests must be a positive integer")
    data_dir = Path(data_dir)
    clock = clock if clock is not None else lambda: datetime.now(KST)
    now = _kst(clock())

    def request_clock():
        current = _kst(clock())
        if current.date() != now.date():
            raise ValueError("refresh crossed KST midnight; stop before making further requests")
        return current
    summary = {"status": "ok" if execute else "dry_run", "dry_run": not execute,
               "started_at": now.isoformat(), "from_year": now.year - 1, "to_year": now.year,
               "max_requests": max_requests, "company_weekly_cap": COMPANY_WEEKLY_CAP,
               "next_batch_offset": None,
               "requests_used": 0, "quarterly_requests": 0, "company_requests": 0}
    lock = file_lock(data_dir / "ops/.fundamentals_refresh.lock") if execute else nullcontext()
    shared_lock = file_lock(data_dir / "disclosures/dart_full/.backfill.lock") if execute else nullcontext()
    with lock, shared_lock:
        before = _budgets(data_dir, now, max_requests)
        try:
            path = data_dir / LAST_RUN
            previous = dart_quarterly._read_json(path, {})
            offset = previous["next_batch_offset"] if path.exists() else 0
            summary["next_batch_offset"] = offset
            if execute and (not isinstance(api_key, str) or not api_key.strip()):
                raise ValueError("DART API key is required for execution")
            files = sorted((data_dir / "market/krx_daily").glob("*/*.jsonl"))
            if not files:
                raise ValueError("latest KRX day is required for profile refresh")
            latest = krx_market._date(files[-1].stem)
            if latest >= now.date():
                raise ValueError("KRX profile universe must precede today KST")
            stocks = {row["stock_code"] for row in krx_market.load_universe(latest.isoformat(), data_dir)}
            if not stocks:
                raise ValueError("latest KRX day has no stock codes")
            corp_codes = data_dir / "reference/corp_codes.csv"
            references = dart_company.load_corp_codes(corp_codes, stock_codes=stocks)
            stored = dart_company._stored(data_dir / "reference/dart_company/companies.jsonl")
            profiled = {row["reference_stock_code"] for row in stored.values()} | {
                row["stock_code"] for row in stored.values() if row["stock_code"]}
            summary.update(latest_krx_day=latest.isoformat(),
                unmapped_stock_codes=sorted(stocks - profiled - {row["stock_code"] for row in references}))
            cap = min(before["quarterly"]["remaining"], before["shared"]["remaining"])
            with redirect_stdout(StringIO()):
                quarterly = dart_quarterly.backfill(now.year - 1, now.year, data_dir=data_dir,
                    corp_codes_path=corp_codes, execute=execute and cap > 0, api_key=api_key,
                    max_requests=max(1, before["quarterly"]["used_today"] + cap),
                    retry_batches=["all"], batch_offset=offset, clock=request_clock, session=session)
            quarterly["planned_requests"] = min(quarterly["pending_requests"], cap)
            summary["quarterly"] = quarterly
            if execute and quarterly["requests"]:
                summary["next_batch_offset"] = (quarterly["batch_offset"]
                    + quarterly["requests"] - quarterly["pending_requests"]) % quarterly["requests"]
            if execute and cap == 0:
                quarterly.update(status="quota_reached", reason="weekly_or_shared_request_budget", dry_run=False)
            if quarterly["status"] == "error":
                summary["status"] = "error"
            else:
                after_quarterly = _budgets(data_dir, _kst(clock()), max_requests)
                cap = min(after_quarterly["company"]["remaining"], after_quarterly["shared"]["remaining"])
                collector = dart_company.DartCompanyCollector(api_key, data_dir=data_dir,
                    clock=request_clock, session=session, sleeper=sleeper)
                company = collector.collect(corp_codes, stock_codes=stocks - profiled,
                    execute=execute and cap > 0,
                    max_requests=max(1, after_quarterly["company"]["used_today"] + cap))
                company["planned_requests"] = min(company["pending"], cap)
                if execute and cap == 0:
                    company.update(dry_run=False, stop_reason="weekly_or_shared_request_budget")
                summary["company"] = company
                if execute and (quarterly["status"] == "quota_reached" or company["remaining"]):
                    summary["status"] = "quota_reached"
        except (ValueError, OSError, KeyError, DartAPIError):
            # Provider/configuration text can contain credentials; expose a fixed error.
            summary.update(status="error", error="DART refresh configuration, archive or provider request failed")
        after = _budgets(data_dir, _kst(clock()), max_requests)
        for name in ("quarterly", "company"):
            # Ledger deltas also count a request whose response or save failed.
            summary[f"{name}_requests"] = after[name]["total_requests"] - before[name]["total_requests"]
        summary.update(requests_used=summary["quarterly_requests"] + summary["company_requests"],
                       budgets=after, finished_at=_kst(clock()).isoformat())
        if execute:
            atomic_write(data_dir / LAST_RUN, json.dumps(summary, ensure_ascii=False, allow_nan=False) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("HQA_DATA_DIR", Path(__file__).resolve().parents[2] / "data")))
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help="Quarterly request cap per KST week, including earlier attempts (default: 400)")
    parser.add_argument("--execute", action="store_true", help="Call DART and save versions/progress")
    args = parser.parse_args(argv)
    try:
        summary = refresh_fundamentals(args.data_dir, execute=args.execute, max_requests=args.max_requests,
                                      api_key=os.getenv("DART_API_KEY") if args.execute else None)
    except (ValueError, OSError, KeyError):
        summary = {"status": "error", "error": "DART refresh configuration or archive is invalid"}
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return 1 if summary["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
