"""Shared source, date, and completion contracts for the collection CLIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_SOURCES = "news,dart,financials,chart"
# chart is KRX Open API daily prices (primary). kis_chart is KIS daily prices on the same
# unadjusted basis, opted into while the KRX key awaits approval; never both in one run.
SUPPORTED_SOURCES = ("news", "dart", "financials", "forum", "chart", "kis_chart")
KST = timezone(timedelta(hours=9))


def enabled_sources(raw: str) -> list[str]:
    values = list(dict.fromkeys(item.strip().lower() for item in raw.split(",") if item.strip()))
    if not values or any(value not in SUPPORTED_SOURCES for value in values):
        raise ValueError("unsupported or empty enabled sources")
    if {"chart", "kis_chart"} <= set(values):
        raise ValueError("chart (KRX) and kis_chart (KIS) are alternative price sources; enable one")
    return values


def resolve_dates(args) -> None:
    args.incremental = args.from_date is None and args.to_date is None
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    end = datetime.strptime(args.to_date, "%Y%m%d").date() if args.to_date else yesterday
    start = datetime.strptime(args.from_date, "%Y%m%d").date() if args.from_date else end - timedelta(days=400)
    if start > end:
        raise ValueError("from_date must not be after to_date")
    args.from_date, args.to_date = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def collection_status(reports: list[dict]) -> str:
    failed = False
    succeeded = False
    for report in reports:
        sources = report.get("enabled_sources", [])
        success = report.get("source_success", {})
        states = report.get("source_status", {})
        failed = failed or bool(report.get("failures")) or not sources
        for source in sources:
            passed = success.get(source) is True and states.get(source) in {"success", "no_data", "cached"}
            failed = failed or not passed
            succeeded = succeeded or passed
    return "partial" if failed and succeeded else "error" if failed or not reports else "done"
