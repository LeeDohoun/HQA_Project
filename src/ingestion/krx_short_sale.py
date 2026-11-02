"""KRX daily short-sale and securities-lending balances with collection-time provenance.

Mirrors krx_benchmarks: observed provider values only, one record per trade date,
content-hashed identity, and no interpolation of missing sessions.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .storage import atomic_write, file_lock

KST = ZoneInfo("Asia/Seoul")
SHORT_SALE_URL = "https://data-dbg.krx.co.kr/svc/apis/srt/sbd_trd"
LENDING_URL = "https://data-dbg.krx.co.kr/svc/apis/srt/ssr_bal"
DEFAULT_PATH = Path("data/market_context/short_sale.jsonl")
SHORT_SALE_VERSION = "krx-short-sale-v1"
_FIELDS = {"schema_version", "stock_code", "trade_date", "short_volume", "total_volume",
           "short_ratio", "lending_balance_quantity", "lending_balance_value",
           "bar_at", "available_at", "source_url", "source_id", "version"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("short-sale timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _date(value: date | str) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("short-sale dates must be YYYYMMDD or YYYY-MM-DD")
    return datetime.strptime(value, "%Y%m%d" if len(value) == 8 else "%Y-%m-%d").date()


def _quantity(value: object, field: str) -> int:
    """Observed nonnegative provider quantity. Absent is an error, never zero."""
    if value is None:
        raise ValueError(f"{field} is missing from the provider row")
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
            raise ValueError(f"{field} must be an observed nonnegative integer")
        value = int(text.replace(",", ""))
    if type(value) is bool or type(value) not in (int, float):
        raise ValueError(f"{field} must be an observed nonnegative integer")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number != math.trunc(number):
        raise ValueError(f"{field} must be an observed nonnegative integer")
    return int(number)


def _record(stock_code: str, day: date, short_volume: int, total_volume: int,
            lending_quantity: int, lending_value: int, observed: datetime) -> dict:
    if not isinstance(stock_code, str) or not re.fullmatch(r"[0-9]{6}", stock_code):
        raise ValueError("stock_code must contain six digits")
    if short_volume > total_volume:
        raise ValueError("short volume cannot exceed total traded volume")
    bar = datetime.combine(day, time(15, 30), KST)
    observed = _aware(observed)
    if observed < bar:
        raise ValueError("short-sale observation precedes the completed market close")
    # KRX publishes short-sale aggregates on T+1; ratio is observed, not modelled.
    content = {"schema_version": 1, "stock_code": stock_code, "trade_date": day.isoformat(),
               "short_volume": short_volume, "total_volume": total_volume,
               "short_ratio": round(short_volume / total_volume, 6) if total_volume else None,
               "lending_balance_quantity": lending_quantity, "lending_balance_value": lending_value,
               "bar_at": bar.isoformat(),
               "source_url": f"{SHORT_SALE_URL}?basDd={day:%Y%m%d}"}
    version = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {**content, "available_at": observed.isoformat(),
            "version": version, "source_id": "krx-short-sale:" + version}


def validate_short_sale_record(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != _FIELDS or type(row["schema_version"]) is not int:
        raise ValueError("invalid short-sale record schema")
    expected = _record(row["stock_code"], _date(row["trade_date"]),
                       _quantity(row["short_volume"], "short_volume"),
                       _quantity(row["total_volume"], "total_volume"),
                       _quantity(row["lending_balance_quantity"], "lending_balance_quantity"),
                       _quantity(row["lending_balance_value"], "lending_balance_value"),
                       _aware(row["available_at"]))
    if row != expected:
        raise ValueError("short-sale record provenance or content hash mismatch")
    return expected


class KrxShortSaleCollector:
    """Collects KRX short-sale trading and lending balances for one trade date."""

    def __init__(self, api_key: str | None = None, timeout: int = 20, path: Path | str | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("KRX_API_KEY", "")
        if not self.api_key.strip():
            raise ValueError("KRX_API_KEY is required for short-sale collection")
        self.timeout = timeout
        self.path = Path(path) if path else DEFAULT_PATH

    def _fetch(self, url: str, day: date) -> list[dict]:
        response = requests.get(url, params={"basDd": f"{day:%Y%m%d}"},
                                headers={"AUTH_KEY": self.api_key}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected KRX response envelope")
        rows = payload.get("OutBlock_1")
        if rows is None:
            raise ValueError("KRX response is missing OutBlock_1")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid KRX short-sale rows")
        return rows

    def collect(self, day: date | str, *, stock_codes: list[str] | None = None,
                observed: datetime | None = None) -> list[dict]:
        """Returns validated records for one completed trade date."""
        day = _date(day)
        observed = _aware(observed) if observed is not None else _now()
        wanted = {str(code) for code in stock_codes} if stock_codes else None

        lending: dict[str, tuple[int, int]] = {}
        for row in self._fetch(LENDING_URL, day):
            code = str(row.get("ISU_CD") or "").strip()
            if not re.fullmatch(r"[0-9]{6}", code):
                continue
            lending[code] = (_quantity(row.get("BAL_QTY"), "lending_balance_quantity"),
                             _quantity(row.get("BAL_AMT"), "lending_balance_value"))

        records = []
        for row in self._fetch(SHORT_SALE_URL, day):
            code = str(row.get("ISU_CD") or "").strip()
            if not re.fullmatch(r"[0-9]{6}", code) or (wanted is not None and code not in wanted):
                continue
            if code not in lending:
                # A short-sale row without its lending balance is incomplete, not zero.
                continue
            quantity, value = lending[code]
            records.append(_record(code, day, _quantity(row.get("CVSRTSELL_TRDVOL"), "short_volume"),
                                   _quantity(row.get("ACC_TRDVOL"), "total_volume"),
                                   quantity, value, observed))
        return records

    def collect_range(self, from_date: date | str, to_date: date | str, *,
                      stock_codes: list[str] | None = None) -> list[dict]:
        start, end = _date(from_date), _date(to_date)
        if start > end:
            raise ValueError("from_date must not be after to_date")
        records, day = [], start
        while day <= end:
            if day.weekday() < 5:  # Holidays simply return no rows; they are not synthesized.
                try:
                    records.extend(self.collect(day, stock_codes=stock_codes))
                except (requests.RequestException, ValueError) as exc:
                    print(f"[WARN][SHORT_SALE] {day:%Y%m%d} skipped: {type(exc).__name__}: {exc}")
            day += timedelta(days=1)
        return records

    def save(self, records: list[dict]) -> int:
        """Appends records, keeping the first observation of each (stock, date)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.with_suffix(".lock")):
            existing = {}
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        row = validate_short_sale_record(json.loads(line))
                        existing[(row["stock_code"], row["trade_date"])] = row
            added = 0
            for row in records:
                key = (row["stock_code"], row["trade_date"])
                if key in existing:
                    if existing[key]["version"] != row["version"]:
                        raise ValueError(f"conflicting short-sale revision for {key}")
                    continue
                existing[key] = validate_short_sale_record(row)
                added += 1
            ordered = sorted(existing.values(), key=lambda row: (row["trade_date"], row["stock_code"]))
            atomic_write(self.path, "\n".join(json.dumps(row, ensure_ascii=False,
                                                         allow_nan=False, separators=(",", ":"))
                                              for row in ordered) + ("\n" if ordered else ""))
            return added


def load_short_sale(path: Path | str, stock_code: str, as_of: datetime) -> list[dict]:
    """Point-in-time short-sale history: only rows available at as_of."""
    path = Path(path)
    if not path.exists():
        return []
    as_of = _aware(as_of)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("stock_code") != stock_code:
            continue
        if _aware(row["available_at"]) > as_of:
            continue
        rows.append(validate_short_sale_record(row))
    return sorted(rows, key=lambda row: row["trade_date"])
