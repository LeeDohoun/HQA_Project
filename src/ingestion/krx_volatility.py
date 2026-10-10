"""KRX VKOSPI (KOSPI200 implied-volatility index) with collection-time provenance.

VKOSPI is derived from KOSPI200 option prices, so it is the market's option-implied
fear gauge. It is market-wide, not per-stock: it belongs in a regime coefficient,
never as a component of a single stock's own-history percentile.
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
VKOSPI_URL = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd"
VKOSPI_INDEX_NAME = "코스피 200 변동성지수"
DEFAULT_PATH = Path("data/market_context/volatility_index.jsonl")
VOLATILITY_VERSION = "krx-vkospi-v1"
_FIELDS = {"schema_version", "index_name", "trade_date", "close", "bar_at",
           "available_at", "source_url", "source_id", "version"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("volatility timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _date(value: date | str) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("volatility dates must be YYYYMMDD or YYYY-MM-DD")
    return datetime.strptime(value, "%Y%m%d" if len(value) == 8 else "%Y-%m-%d").date()


def _close(value: object) -> float:
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text):
            raise ValueError("VKOSPI close must be an observed positive index level")
        value = float(text.replace(",", ""))
    if type(value) is bool or type(value) not in (int, float):
        raise ValueError("VKOSPI close must be an observed positive index level")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("VKOSPI close must be an observed positive index level")
    return number


def _record(name: str, day: date, close: float, observed: datetime) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("IDX_NM must be a nonempty exact provider index name")
    bar = datetime.combine(day, time(15, 45), KST)  # Derivatives close later than the cash market.
    observed = _aware(observed)
    if observed < bar:
        raise ValueError("volatility observation precedes the completed derivatives close")
    content = {"schema_version": 1, "index_name": name, "trade_date": day.isoformat(),
               "close": close, "bar_at": bar.isoformat(),
               "source_url": f"{VKOSPI_URL}?basDd={day:%Y%m%d}"}
    version = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {**content, "available_at": observed.isoformat(),
            "version": version, "source_id": "krx-vkospi:" + version}


def validate_volatility_record(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != _FIELDS or type(row["schema_version"]) is not int:
        raise ValueError("invalid volatility record schema")
    expected = _record(row["index_name"], _date(row["trade_date"]),
                       _close(row["close"]), _aware(row["available_at"]))
    if row != expected:
        raise ValueError("volatility record provenance or content hash mismatch")
    return expected


class KrxVolatilityCollector:
    """Collects the daily VKOSPI close for completed sessions."""

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 path: Path | str | None = None):
        key = api_key if api_key is not None else os.getenv("KRX_OPEN_API_KEY") or os.getenv("KRX_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("KRX_OPEN_API_KEY or KRX_API_KEY is required for volatility collection")
        self.api_key = key.strip()
        self.session = session if session is not None else requests.Session()
        self.path = Path(path) if path else DEFAULT_PATH

    def collect_daily(self, from_date: date | str, to_date: date | str) -> list[dict]:
        start, end = _date(from_date), _date(to_date)
        now = _aware(_now()).astimezone(KST)
        if start > end:
            raise ValueError("volatility range must be ordered")
        if end >= now.date():
            raise ValueError("volatility range must exclude current and future KST dates")
        if now.hour < 8:
            raise ValueError("volatility collection requires 08:00 KST or later")

        records, cursor = [], start
        while cursor <= end:
            try:
                response = self.session.get(VKOSPI_URL, params={"basDd": f"{cursor:%Y%m%d}"},
                                            headers={"AUTH_KEY": self.api_key}, timeout=20,
                                            allow_redirects=False)
                if not 200 <= response.status_code < 300:
                    raise requests.HTTPError(f"KRX volatility HTTP {response.status_code} for {cursor:%Y%m%d}")
            except requests.RequestException as exc:
                raise type(exc)(str(exc).replace(self.api_key, "[REDACTED]")) from None
            try:
                payload = response.json()
            except ValueError:
                raise ValueError("KRX volatility response is not valid JSON") from None
            if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
                raise ValueError("KRX volatility response requires an OutBlock_1 array")
            collected = _aware(_now())
            for raw in payload["OutBlock_1"]:
                if not isinstance(raw, dict) or not {"BAS_DD", "IDX_NM", "CLSPRC_IDX"} <= raw.keys():
                    continue
                name = raw["IDX_NM"]
                if not isinstance(name, str) or VKOSPI_INDEX_NAME not in name.replace(" ", " "):
                    continue
                if raw["BAS_DD"] != f"{cursor:%Y%m%d}":
                    raise ValueError("KRX volatility BAS_DD does not match requested date")
                records.append(_record(name, cursor, _close(raw["CLSPRC_IDX"]), collected))
            cursor += timedelta(days=1)
        return records

    def save(self, records: list[dict]) -> int:
        """Appends records, keeping the first observation of each trade date."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.with_suffix(".lock")):
            existing = {}
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        row = validate_volatility_record(json.loads(line))
                        existing[row["trade_date"]] = row
            added = 0
            for row in records:
                validate_volatility_record(row)
                prior = existing.get(row["trade_date"])
                if prior is not None:
                    if prior["version"] != row["version"]:
                        raise ValueError(f"conflicting volatility revision for {row['trade_date']}")
                    continue
                existing[row["trade_date"]] = row
                added += 1
            ordered = sorted(existing.values(), key=lambda row: row["trade_date"])
            atomic_write(self.path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False,
                                                       separators=(",", ":")) + "\n" for row in ordered))
            return added


def load_volatility_index(path: Path | str, as_of: datetime) -> list[dict]:
    """Point-in-time VKOSPI history: only observations available at as_of."""
    path = Path(path)
    if not path.exists():
        return []
    as_of = _aware(as_of)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if _aware(row["available_at"]) > as_of:
            continue
        rows.append(validate_volatility_record(row))
    return sorted(rows, key=lambda row: row["trade_date"])
