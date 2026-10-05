"""Full-market KRX daily observations, retained by historical trade date."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests

from . import krx_chart
from .storage import atomic_write, file_lock, read_rows, save_episodes

PRICE_FIELDS = ("open", "high", "low", "close", "volume")
OPTIONAL_FIELDS = {"trading_value": "ACC_TRDVAL", "market_cap": "MKTCAP", "listed_shares": "LIST_SHRS"}
CHANGE_FIELDS = {"change_rate_pct": "FLUC_RT", "change_vs_base": "CMPPREVDD_PRC"}


def _clean_signed_number(value) -> str:
    text = str(value).strip()
    sign = "-" if text.startswith("-") else ""
    return sign + krx_chart.KrxChartCollector._clean_number(text[len(sign):])


def _daily_metadata(raw: dict) -> dict:
    from src.runner.trading_calendar import daily_session_close

    day = _date(raw["BAS_DD"])
    observed = datetime.fromisoformat(raw["_collected_at"])
    metadata = {"trade_date": day.isoformat(), "price_basis": "unadjusted",
                "calendar_status": "verified", "collected_at": observed.isoformat(),
                "available_at": observed.isoformat()}
    try:
        bar_at = daily_session_close(day.isoformat()).astimezone(krx_chart.KST)
    except ValueError as exc:
        statuses = {"calendar_special_session_coverage_unverified": "unverified_special_session",
                    "nontrading_price_date": "exchange_calendar_mismatch"}
        status = statuses.get(str(exc).split(":", 1)[0])
        if (status is None or observed.tzinfo is None or observed.utcoffset() is None
                or observed.astimezone(krx_chart.KST).date() <= day):
            raise
        metadata["calendar_status"] = status
    else:
        if observed.tzinfo is None or observed < bar_at:
            raise ValueError("KRX chart observation must follow the completed market close and include timezone")
        metadata["bar_at"] = bar_at.isoformat()
    return metadata


def _date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("KRX market dates must be YYYYMMDD or YYYY-MM-DD")
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").date()


def _days(from_date: str, to_date: str) -> list[date]:
    start, end = _date(from_date), _date(to_date)
    if start > end:
        raise ValueError("KRX market date range must be ordered")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _path(day: date, data_dir: str | Path) -> Path:
    return Path(data_dir) / "market" / "krx_daily" / f"{day:%Y}" / f"{day:%Y%m%d}.jsonl"


def _empty_dates(data_dir: str | Path) -> list[str]:
    path = Path(data_dir) / "market" / "krx_daily" / "_state.json"
    return json.loads(path.read_text(encoding="utf-8"))["empty_dates"] if path.exists() else []


def _record_empty(day: str, data_dir: str | Path, *, empty: bool) -> None:
    path = Path(data_dir) / "market" / "krx_daily" / "_state.json"
    if not empty and not path.exists():
        return
    with file_lock(path.with_suffix(".json.lock")):
        previous = _empty_dates(data_dir)
        dates = set(previous)
        if empty:
            dates.add(day)
        else:
            dates.discard(day)
        if sorted(dates) != previous:
            atomic_write(path, json.dumps({"empty_dates": sorted(dates)}) + "\n")


class KrxMarketCollector:
    """Create the underlying chart collector only when a day is requested."""

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key, self.session = api_key, session

    def collect_day(self, bas_dd: str) -> list[dict]:
        day = _date(bas_dd)
        if day >= krx_chart._now().astimezone(krx_chart.KST).date():
            raise ValueError("KRX market collection must exclude current and future KST dates")
        # A fresh daily collector bounds the full-market cache and permits revisions.
        collector = krx_chart.KrxChartCollector(self.api_key, self.session)
        try:
            if not collector.api_key:
                raise ValueError("KRX_OPEN_API_KEY or KRX_API_KEY is required for market collection")
            rows = {}
            for market, url in (("KOSPI", collector.KOSPI_DAILY_URL), ("KOSDAQ", collector.KOSDAQ_DAILY_URL)):
                for raw in collector._fetch_market_rows(url, f"{day:%Y%m%d}"):
                    if not isinstance(raw.get("ISU_NM"), str) or not raw["ISU_NM"].strip():
                        raise ValueError("KRX market row requires a stock name")
                    metadata = _daily_metadata(raw)
                    row = {"stock_code": raw["ISU_CD"].strip(), "stock_name": raw["ISU_NM"].strip(),
                           "market": market,
                           **{field: collector._clean_number(raw[source]) for field, source in zip(
                               PRICE_FIELDS, ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL"))},
                           **{field: collector._clean_number(raw[source])
                              for field, source in OPTIONAL_FIELDS.items() if source in raw},
                           **{field: _clean_signed_number(raw[source])
                              for field, source in CHANGE_FIELDS.items() if source in raw},
                           **{field: value for field, value in metadata.items()
                              if field not in {"collected_at", "available_at"}},
                           "source_url": url + f"?basDd={day:%Y%m%d}"}
                    if "change_vs_base" in row:
                        row["base_price"] = str(Decimal(row["close"]) - Decimal(row["change_vs_base"]))
                    version = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    row.update(version=version, collected_at=metadata["collected_at"],
                               available_at=metadata["available_at"])
                    if row["stock_code"] in rows:
                        raise ValueError("duplicate normalized KRX market stock/date")
                    rows[row["stock_code"]] = row
            return [rows[code] for code in sorted(rows)]
        finally:
            if self.session is None:
                collector.session.close()

    def backfill(self, from_date: str, to_date: str, data_dir: str | Path, *, execute: bool = False) -> dict:
        """Report date lists for planned/skipped/empty; fetched counts days, saved_rows counts episodes.

        Empty lists include previously observed, still unresolved empty dates in the range.
        calendar_unverified lists fetched dates with unverified closes; request errors propagate
        immediately, preserving days already saved.
        Dry runs read existing paths/state only; they need neither credentials nor a session.
        """
        days = _days(from_date, to_date)
        if days[-1] >= krx_chart._now().astimezone(krx_chart.KST).date():
            raise ValueError("KRX market range must exclude current and future KST dates")
        weekdays = [day for day in days if day.weekday() < 5]
        existing = {day for day in weekdays if _path(day, data_dir).exists()}
        planned = [day for day in weekdays if day not in existing]
        empty = set(_empty_dates(data_dir)) & {f"{day:%Y%m%d}" for day in planned}
        summary = {"planned": [f"{day:%Y%m%d}" for day in planned],
                   "skipped_existing": [f"{day:%Y%m%d}" for day in sorted(existing)],
                   "fetched": 0, "empty": sorted(empty), "saved_rows": 0, "calendar_unverified": []}
        if execute:
            for day in planned:
                bas_dd = f"{day:%Y%m%d}"
                rows = self.collect_day(bas_dd)
                summary["fetched"] += 1
                if rows:
                    summary["saved_rows"] += save_day(rows, data_dir)
                    if any(row["calendar_status"] != "verified" for row in rows):
                        summary["calendar_unverified"].append(bas_dd)
                    empty.discard(bas_dd)
                else:
                    empty.add(bas_dd)
                    _record_empty(bas_dd, data_dir, empty=True)
            summary["empty"] = sorted(empty)
        return summary


def backfill(from_date: str, to_date: str, data_dir: str | Path, *, execute: bool = False) -> dict:
    return KrxMarketCollector().backfill(from_date, to_date, data_dir, execute=execute)


def save_day(rows: list[dict], data_dir: str | Path) -> int:
    """Append per-stock revisions (including A -> B -> A), preserving first availability."""
    if not rows:
        return 0
    day = _date(rows[0]["trade_date"])
    if any(_date(row["trade_date"]) != day for row in rows):
        raise ValueError("save_day requires rows from one trade date")
    saved = save_episodes(_path(day, data_dir), rows,
                          identity=lambda row: row["stock_code"], revision=lambda row: row["version"])
    _record_empty(f"{day:%Y%m%d}", data_dir, empty=False)
    return saved


def load_universe(as_of: str, data_dir: str | Path) -> list[dict]:
    """Return the latest stored episode per stock for exactly this trade date.

    This is a trade-date lookup, not an availability-time/as-of revision filter.
    """
    latest = {row["stock_code"]: row for row in read_rows(_path(_date(as_of), data_dir))}
    return [latest[code] for code in sorted(latest)]


def load_prices(codes: list[str] | None, from_date: str, to_date: str, data_dir: str | Path,
                *, columns=None) -> pd.DataFrame:
    """Load latest stored daily episodes; missing prices/optional fields are never filled.

    Multi-day returns must chain (1 + ret_1d), rather than divide raw closes.
    First-day open-to-close returns require open/base_price-adjusted logic in the
    evaluation layer.
    columns projects fields before accumulating days, bounding metadata memory.
    """
    selected = None if codes is None else set(codes)
    rows = [row if columns is None else {key: value for key, value in row.items() if key in columns}
            for day in _days(from_date, to_date) for row in load_universe(day.isoformat(), data_dir)
            if selected is None or row["stock_code"] in selected]
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["trade_date", "stock_code", *PRICE_FIELDS])
    del rows
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    for field in (*PRICE_FIELDS, *OPTIONAL_FIELDS, *CHANGE_FIELDS, "base_price"):
        if field in frame:
            frame[field] = pd.to_numeric(frame[field], errors="raise")
    frame["ret_1d"] = frame["change_rate_pct"] / 100 if "change_rate_pct" in frame else float("nan")
    if "base_price" not in frame:
        frame["base_price"] = float("nan")
    return frame.set_index(["trade_date", "stock_code"]).sort_index()
