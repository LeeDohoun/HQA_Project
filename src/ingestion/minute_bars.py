"""Plan and collect KIS minute bars through the credential-owning backend."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from bisect import bisect_left
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from src.config.settings import get_project_root
from src.utils.stock_codes import KRX_SHORT_CODE_PATTERN
from .storage import file_lock, read_rows, write_rows

KST = ZoneInfo("Asia/Seoul")
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
ENDPOINT = "/api/v1/internal/market/minute-candles"


def _now() -> datetime:
    return datetime.now(KST)


def _date(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("minute bar dates must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _stock_code(value: str) -> str:
    if not isinstance(value, str) or not KRX_SHORT_CODE_PATTERN.fullmatch(value):
        raise ValueError("stock_code must contain exactly six ASCII digits or uppercase letters")
    return value


def _base_url() -> str:
    base = os.getenv("BACKEND_INTERNAL_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    signal = os.getenv("BACKEND_SIGNAL_URL", "").strip()
    suffix = "/api/v1/internal/trading/signals"
    if signal.endswith(suffix):
        return signal[:-len(suffix)]
    return "http://localhost:8000"


def _complete(rows: list[dict]) -> bool:
    # Boundary coverage only: zero-trade minutes may be absent in KIS output.
    return bool(rows) and all(row.get("complete") is True for row in rows)


class MinuteBarClient:
    def __init__(self, base_url: str | None = None, internal_token: str | None = None,
                 session: requests.Session | None = None, *, data_dir: str | Path | None = None):
        self.base_url = (base_url if base_url is not None else _base_url()).rstrip("/")
        self.internal_token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "").strip()
        self.session = session
        self.data_dir = Path(data_dir if data_dir is not None else os.getenv("HQA_DATA_DIR", "./data")).expanduser()
        if not self.data_dir.is_absolute():
            self.data_dir = get_project_root() / self.data_dir

    def _path(self, stock_code: str, day: date) -> Path:
        return self.data_dir / "market" / "minute" / f"{day:%Y%m%d}" / f"{stock_code}.jsonl"

    def collect(self, stock_code: str, date: str | date, user_id: str) -> list[dict]:
        stock_code, day = _stock_code(stock_code), _date(date)
        today = _now().astimezone(KST).date()
        if not today - timedelta(days=366) <= day <= today:
            raise ValueError("minute bar date must be within the past 366 days and not in the future")
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required")
        if not self.internal_token or not self.internal_token.strip():
            raise ValueError("HQA_INTERNAL_TOKEN is required")
        if not self.base_url:
            raise ValueError("backend base URL is required")
        session = self.session if self.session is not None else requests.Session()
        try:
            response = session.post(
                self.base_url + ENDPOINT,
                json={"userId": user_id, "stockCode": stock_code, "date": day.isoformat()},
                headers={"X-HQA-Internal-Token": self.internal_token, "Accept": "application/json"},
                timeout=30, allow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                raise ValueError(f"minute candle HTTP status: {response.status_code}")
            payload = response.json()
        finally:
            if self.session is None:
                session.close()
        if not isinstance(payload, dict) or payload.get("status") != "OK":
            status = payload.get("status") if isinstance(payload, dict) else "INVALID_RESPONSE"
            raise ValueError(f"minute candle status: {status}")
        if (payload.get("stockCode") != stock_code or payload.get("date") != day.isoformat()
                or payload.get("source") != "kis"):
            raise ValueError("minute candle response identity/source mismatch")
        candles = payload.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ValueError("minute candle response has no bars")
        collected_at = _now().astimezone(KST)
        rows, seen = [], set()
        for candle in candles:
            if not isinstance(candle, dict) or type(candle.get("time")) is not int:
                raise ValueError("minute candle time must be epoch seconds")
            stamp = datetime.fromtimestamp(candle["time"], KST)
            if (stamp.date() != day or stamp.second != 0 or not "09:00" <= stamp.strftime("%H:%M") <= "15:30"
                    or stamp > collected_at or stamp in seen):
                raise ValueError("minute candle time is outside the session, duplicated, or in the future")
            seen.add(stamp)
            for field in PRICE_FIELDS:
                value = candle.get(field)
                if (type(value) not in (int, float) or not math.isfinite(value)
                        or value < 0 or (field != "volume" and value == 0)):
                    raise ValueError(f"invalid minute candle {field}")
            if (type(candle["volume"]) is not int or candle["low"] > min(candle["open"], candle["close"])
                    or candle["high"] < max(candle["open"], candle["close"])):
                raise ValueError("invalid minute candle OHLCV")
            rows.append({"trade_date": day.isoformat(), "stock_code": stock_code, "time": stamp.isoformat(),
                         **{field: candle[field] for field in PRICE_FIELDS}, "source": "kis_minute"})
        rows.sort(key=lambda row: row["time"])
        complete = rows[0]["time"][11:16] == "09:00" and rows[-1]["time"][11:16] >= "15:20"
        for row in rows:
            row["complete"] = complete
            row["version"] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            row.update(collected_at=collected_at.isoformat(), available_at=collected_at.isoformat())
        return rows

    def backfill(self, pairs, user_id: str, *, execute: bool = False,
                 requests_per_second: float = 2.0, sleeper=time.sleep) -> dict:
        """Throttle backend requests; replace incomplete snapshots and skip complete ones.

        Failures and incomplete sessions are returned in the summary. A backend
        request can itself make up to six KIS calls; this rate is not a KIS quota.
        """
        if not math.isfinite(requests_per_second) or requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive and finite")
        pairs = list(dict.fromkeys(pairs))
        summary = {"planned": [], "skipped_existing": [], "fetched": 0, "saved_rows": 0,
                   "incomplete": [], "failures": [], "execute": execute}
        attempted = False
        for stock_code, date_value in pairs:
            pair = {"stock_code": stock_code, "date": str(date_value)}
            try:
                day = _date(date_value)
                path = self._path(_stock_code(stock_code), day)
                stored = read_rows(path)
                if _complete(stored):
                    summary["skipped_existing"].append(pair)
                    continue
                summary["planned"].append(pair)
                if not execute:
                    if stored:
                        summary["incomplete"].append(pair)
                    continue
                if attempted:
                    sleeper(1.0 / requests_per_second)
                attempted = True
                rows = self.collect(stock_code, day, user_id)
                summary["fetched"] += 1
                with file_lock(path.with_suffix(".jsonl.lock")):
                    if _complete(read_rows(path)):
                        summary["skipped_existing"].append(pair)
                        continue
                    summary["saved_rows"] += write_rows(path, rows)
                if not _complete(rows):
                    summary["incomplete"].append(pair)
            except Exception as exc:
                summary["failures"].append({**pair, "error": f"{type(exc).__name__}: {exc}"})
        if not execute:
            print(json.dumps(summary, ensure_ascii=False))
        return summary


def plan_event_window(events, sessions, days_before: int = 1, days_after: int = 1) -> list[tuple[str, str]]:
    """Events use stock_code/date; non-session events anchor to the next supplied session.

    sessions must contain actual trading dates, including holidays and special
    sessions correctly. Retain dates from today minus 365 days through today (KST).
    """
    if type(days_before) is not int or type(days_after) is not int or min(days_before, days_after) < 0:
        raise ValueError("event window widths must be nonnegative integers")
    sessions = sorted({_date(day) for day in sessions})
    today = _now().astimezone(KST).date()
    cutoff = today - timedelta(days=365)
    pairs = set()
    for event in events:
        code, day = _stock_code(event["stock_code"]), _date(event["date"])
        index = bisect_left(sessions, day)
        if index == len(sessions):
            raise ValueError(f"sessions do not cover event date: {day}")
        for session in sessions[max(0, index - days_before):index + days_after + 1]:
            if cutoff <= session <= today:
                pairs.add((code, session.isoformat()))
    return sorted(pairs)


def backfill(pairs, user_id: str, *, execute: bool = False,
             requests_per_second: float = 2.0, sleeper=time.sleep) -> dict:
    return MinuteBarClient().backfill(pairs, user_id, execute=execute,
                                     requests_per_second=requests_per_second, sleeper=sleeper)
