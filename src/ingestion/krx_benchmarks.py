"""KRX daily price-index observations with collection-time provenance."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
ENDPOINTS = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd",
}
DEFAULT_PATH = Path("data/market_context/benchmarks.jsonl")
_FIELDS = {"schema_version", "series", "index_name", "trade_date", "close", "bar_at",
           "available_at", "source_url", "source_id", "version", "price_basis"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("benchmark timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _date(value: date | str) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("benchmark dates must be YYYYMMDD or YYYY-MM-DD")
    return datetime.strptime(value, "%Y%m%d" if len(value) == 8 else "%Y-%m-%d").date()


def _close(value: object) -> float:
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", value):
            raise ValueError("CLSPRC_IDX must be an observed positive index close")
        value = float(value.replace(",", ""))
    if type(value) not in (int, float):
        raise ValueError("CLSPRC_IDX must be a finite positive index close")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("CLSPRC_IDX exceeds finite numeric range") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError("CLSPRC_IDX must be a finite positive index close")
    return number


def _record(series: str, name: str, day: date, close: float, observed: datetime) -> dict:
    if not isinstance(series, str) or series not in ENDPOINTS:
        raise ValueError("unsupported benchmark series")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("IDX_NM must be a nonempty exact provider index name")
    bar = datetime.combine(day, time(15, 30), KST)
    observed = _aware(observed)
    if observed < bar:
        raise ValueError("benchmark observation precedes the completed market close")
    content = {"schema_version": 1, "series": series, "index_name": name,
               "trade_date": day.isoformat(), "close": close, "bar_at": bar.isoformat(),
               "source_url": f"{ENDPOINTS[series]}?basDd={day:%Y%m%d}", "price_basis": "price_index"}
    version = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {**content, "available_at": observed.isoformat(),
            "version": version, "source_id": "krx-benchmark:" + version}


def validate_benchmark_record(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != _FIELDS or type(row["schema_version"]) is not int:
        raise ValueError("invalid benchmark record schema")
    if type(row["close"]) not in (int, float):
        raise ValueError("stored benchmark close must be numeric")
    observed, day = _aware(row["available_at"]), _date(row["trade_date"])
    expected = _record(row["series"], row["index_name"], day, _close(row["close"]), observed)
    if row != expected:
        raise ValueError("benchmark record provenance or content hash mismatch")
    return expected


class KrxBenchmarkCollector:
    """One request per series/date, retaining market and industry index rows."""

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        key = api_key if api_key is not None else os.getenv("KRX_OPEN_API_KEY") or os.getenv("KRX_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("KRX_OPEN_API_KEY or KRX_API_KEY is required for benchmark collection")
        self.api_key = key.strip()
        self.session = session if session is not None else requests.Session()

    def collect_daily(self, from_date: date | str, to_date: date | str,
                      series: tuple[str, ...] = ("KOSPI", "KOSDAQ")) -> list[dict]:
        start, end, now = _date(from_date), _date(to_date), _aware(_now()).astimezone(KST)
        if start > end or start < date(2010, 1, 4):
            raise ValueError("benchmark range must be ordered and begin on or after 2010-01-04")
        if end >= now.date():
            raise ValueError("benchmark range must exclude current and future KST dates")
        # The official specification gates the latest completed session until 08:00.
        # Without a holiday calendar, defer collection rather than label unpublished data a holiday.
        if now.hour < 8:
            raise ValueError("benchmark collection requires 08:00 KST or later")
        if (not isinstance(series, (tuple, list)) or not series
                or any(not isinstance(item, str) or item not in ENDPOINTS for item in series)
                or len(set(series)) != len(series)):
            raise ValueError("benchmark series must be unique KOSPI/KOSDAQ names")
        records = []
        cursor = start
        while cursor <= end:
            for market in series:
                try:
                    response = self.session.get(ENDPOINTS[market], params={"basDd": f"{cursor:%Y%m%d}"},
                                                headers={"AUTH_KEY": self.api_key}, timeout=20, allow_redirects=False)
                    if type(response.status_code) is not int:
                        raise ValueError("KRX benchmark response requires an HTTP status code")
                    if not 200 <= response.status_code < 300:
                        raise requests.HTTPError(f"KRX benchmark HTTP {response.status_code} for {market} {cursor:%Y%m%d}")
                    response.raise_for_status()
                except requests.RequestException as exc:
                    raise type(exc)(str(exc).replace(self.api_key, "[REDACTED]")) from None
                try:
                    payload = response.json()
                except ValueError:
                    raise ValueError("KRX benchmark response is not valid JSON") from None
                collected = _aware(_now())
                if (not isinstance(payload, dict) or set(payload) != {"OutBlock_1"}
                        or not isinstance(payload["OutBlock_1"], list)):
                    raise ValueError("KRX benchmark response requires only an OutBlock_1 array")
                daily = {}
                for raw in payload["OutBlock_1"]:
                    if not isinstance(raw, dict) or not {"BAS_DD", "IDX_NM", "CLSPRC_IDX"} <= raw.keys():
                        raise ValueError("KRX benchmark row is missing required fields")
                    if not isinstance(raw["BAS_DD"], str) or raw["BAS_DD"] != f"{cursor:%Y%m%d}":
                        raise ValueError("KRX benchmark BAS_DD does not match requested date")
                    if isinstance(raw["IDX_NM"], str) and self.api_key in raw["IDX_NM"]:
                        raise ValueError("KRX benchmark index name contains request credentials")
                    row = _record(market, raw["IDX_NM"], cursor, _close(raw["CLSPRC_IDX"]), collected)
                    previous = daily.setdefault(row["index_name"], row)
                    if previous != row:
                        raise ValueError("conflicting duplicate KRX benchmark index/date")
                records.extend(daily[name] for name in sorted(daily))
            cursor += timedelta(days=1)
        return records


def save_benchmark_records(records: list[dict], path: str | Path = DEFAULT_PATH) -> int:
    """Append changed observation episodes; unchanged rows keep their first availability."""
    if not isinstance(records, list):
        raise ValueError("benchmark records must be a list")
    validated = [validate_benchmark_record(row) for row in records]
    if not validated:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        latest = {}
        for line in handle:
            if not line.endswith("\n"):
                raise ValueError("stored benchmark JSONL has an unterminated record")
            row = validate_benchmark_record(json.loads(line))
            key = (row["series"], row["index_name"], row["trade_date"])
            if key in latest and _aware(row["available_at"]) < _aware(latest[key]["available_at"]):
                raise ValueError("stored benchmark observations are out of order")
            if (key in latest and row["available_at"] == latest[key]["available_at"]
                    and row["version"] != latest[key]["version"]):
                raise ValueError("stored benchmark revisions share an availability timestamp")
            latest[key] = row
        pending = []
        for row in validated:
            key = (row["series"], row["index_name"], row["trade_date"])
            prior = latest.get(key)
            if prior:
                if _aware(row["available_at"]) < _aware(prior["available_at"]):
                    raise ValueError("benchmark observation precedes the latest stored episode")
                if row["version"] == prior["version"]:
                    continue
                if row["available_at"] == prior["available_at"]:
                    raise ValueError("conflicting benchmark revisions share an availability timestamp")
            pending.append(row)
            latest[key] = row
        if pending:
            handle.write("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in pending))
            handle.flush()
            os.fsync(handle.fileno())
    return len(pending)
