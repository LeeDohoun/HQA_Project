"""Forward-only DART listing observations; never infer historical intraday times."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from src.utils.stock_codes import KRX_SHORT_CODE_PATTERN

from .dart_api import DartAPIError, read_dart_payload
from .storage import atomic_write, file_lock, read_rows, write_rows


KST = ZoneInfo("Asia/Seoul")
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
FIELDS = ("rcept_no", "corp_code", "corp_name", "stock_code", "corp_cls",
          "report_nm", "rcept_dt", "flr_nm", "rm")


def _kst(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("DART clock requires a timezone-aware timestamp")
    return now.astimezone(KST)


def listing_params(day: str, api_key: str, page_no: int = 1) -> dict:
    return {"crtfc_key": api_key, "bgn_de": day, "end_de": day,
            "sort": "date", "sort_mth": "desc", "page_count": 100,
            "page_no": page_no, "last_reprt_at": "N"}


def _validate_page(payload: dict, page_no: int, expected, day: str, items: dict) -> tuple:
    # Keep the collector's pagination contract without changing its historical path.
    counts = {}
    for key in ("page_no", "page_count", "total_count", "total_page"):
        value = payload.get(key)
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
            raise DartAPIError(f"DART invalid pagination field: {key}")
        counts[key] = int(value)
    total, pages = counts["total_count"], counts["total_page"]
    if (counts["page_no"] != page_no or counts["page_count"] != 100 or total < 1
            or pages != (total + 99) // 100):
        raise DartAPIError("DART inconsistent pagination metadata")
    if expected is not None and expected != (total, pages):
        raise DartAPIError("DART result set changed during pagination")
    rows = payload.get("list")
    if not isinstance(rows, list) or len(rows) != min(100, total - (page_no - 1) * 100):
        raise DartAPIError("DART incomplete pagination: unexpected page length")
    for row in rows:
        if (not isinstance(row, dict)
                or any(not isinstance(row.get(field), str) for field in FIELDS)
                or not re.fullmatch(r"[0-9]{14}", row["rcept_no"])
                or not re.fullmatch(r"[0-9]{8}", row["corp_code"])
                or not row["corp_name"].strip() or not row["report_nm"].strip()
                or row["rcept_dt"] != day
                or row["corp_cls"] not in {"Y", "K", "N", "E"}
                or (row["stock_code"] != "" and not KRX_SHORT_CODE_PATTERN.fullmatch(row["stock_code"]))):
            raise DartAPIError("DART malformed disclosure row")
        receipt = row["rcept_no"]
        if receipt in items and items[receipt] != row:
            raise DartAPIError("DART conflicting rows for the same receipt")
        items[receipt] = row
    return total, pages


class DartListingPoller:
    def __init__(self, api_key, session=None, clock=None, data_dir=DEFAULT_DATA_DIR, *,
                 sleeper=None, window_start=datetime_time(7), window_end=datetime_time(19, 30)):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("DART API key is required")
        if (not isinstance(window_start, datetime_time) or not isinstance(window_end, datetime_time)
                or window_start.tzinfo is not None or window_end.tzinfo is not None
                or window_start > window_end):
            raise ValueError("DART polling window requires ordered, naive KST times")
        self.api_key = api_key
        self.session = session if session is not None else requests.Session()
        self.clock = clock if clock is not None else lambda: datetime.now(KST)
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.data_dir = Path(data_dir)
        self.window_start = window_start
        self.window_end = window_end

    def should_poll(self, now: datetime) -> bool:
        now = _kst(now)
        # DART also publishes outside trading sessions, so extend beyond market hours.
        return now.weekday() < 5 and self.window_start <= now.time() <= self.window_end

    def poll_once(self) -> dict:
        directory = self.data_dir / "disclosures" / "first_seen"
        # Serialize the whole poll, including timestamp capture, so a slower writer
        # cannot replace an earlier observation or append stale metadata as a revision.
        with file_lock(directory / ".poll.lock"):
            started = _kst(self.clock())
            day = started.strftime("%Y%m%d")
            state_path = directory / "_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
            catch_up_dates = []
            if state is not None:
                # Today's successful poll must not move past unfinished catch-up.
                if "last_closed_date" in state:
                    next_date = datetime.strptime(state["last_closed_date"], "%Y%m%d").date() + timedelta(days=1)
                else:
                    next_date = datetime.strptime(state["last_completed_date"], "%Y%m%d").date()
                while next_date < started.date() and len(catch_up_dates) < 10:
                    catch_up_dates.append(next_date.strftime("%Y%m%d"))
                    next_date += timedelta(days=1)

            fetched = []
            pages = seen = 0
            for query_day in [*catch_up_dates, day]:
                path = directory / f"{query_day}.jsonl"
                stored = read_rows(path)
                latest = {row["rcept_no"]: row for row in stored}
                items, page_count, early_stop = self._fetch_date(query_day, latest,
                    catch_up=query_day != day)
                fetched.append((path, stored, latest, items, query_day != day))
                pages += page_count
                seen += len(items)

            completed = _kst(self.clock())
            if completed < started:
                raise ValueError("DART clock moved backwards during poll")
            new_count = revision_count = 0
            pending = []
            for path, stored, latest, items, catch_up in fetched:
                added = []
                for receipt, item in items.items():
                    metadata = {field: item[field].replace(self.api_key, "[REDACTED]") for field in FIELDS}
                    content_hash = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8")).hexdigest()
                    previous = latest.get(receipt)
                    if previous is not None and previous["content_hash"] == content_hash:
                        continue
                    first_seen = previous["first_seen_at"] if previous is not None else started.isoformat()
                    added.append({**metadata, "listed": bool(item["stock_code"]),
                        "first_seen_at": first_seen, "observed_at": started.isoformat(),
                        "poll_started_at": started.isoformat(), "poll_completed_at": completed.isoformat(),
                        # All pages of every date validated before publication.
                        "available_at": completed.isoformat(), "source": "dart_list_poll",
                        "catch_up": catch_up, "content_hash": content_hash,
                        "revision": previous["revision"] + 1 if previous is not None else 1})
                    if previous is None:
                        new_count += 1
                    else:
                        revision_count += 1
                if added:
                    pending.append((path, stored + added))
            for path, rows in pending:
                write_rows(path, rows)
            next_state = {**(state or {}), "last_completed_date": day}
            if catch_up_dates:
                next_state["last_closed_date"] = catch_up_dates[-1]
            atomic_write(state_path, json.dumps(next_state) + "\n")
            return {"status": "ok", "date": day, "pages": pages, "seen": seen,
                    "new": new_count, "revisions": revision_count, "early_stop": early_stop,
                    "catch_up_dates": catch_up_dates,
                    "poll_started_at": started.isoformat(), "poll_completed_at": completed.isoformat()}

    def _fetch_date(self, day: str, latest: dict, *, catch_up: bool) -> tuple:
        items = {}
        expected = None
        page_no = 1
        early_stop = False
        while True:
            try:
                response = self.session.get(LIST_URL,
                    params=listing_params(day, self.api_key, page_no), timeout=20)
                response.raise_for_status()
            except Exception:
                raise DartAPIError(f"DART list transport failure page={page_no}") from None
            payload = read_dart_payload(response)
            if payload["status"] == "013":
                if page_no != 1:
                    raise DartAPIError("DART incomplete pagination: later page has no data")
                break
            expected = _validate_page(payload, page_no, expected, day, items)
            early_stop = not catch_up and all(row["rcept_no"] in latest for row in payload["list"])
            if early_stop or page_no == expected[1]:
                break
            page_no += 1

        return items, page_no, early_stop

    def run_loop(self, interval_seconds=60, max_polls=None):
        """max_polls counts attempts, including failures; closed-window waits do not count."""
        if isinstance(interval_seconds, bool) or not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("DART interval must be positive and finite")
        if max_polls is not None and (type(max_polls) is not int or max_polls < 0):
            raise ValueError("DART max_polls must be a nonnegative integer")
        attempts = failures = 0
        delay = interval_seconds
        while max_polls is None or attempts < max_polls:
            if not self.should_poll(self.clock()):
                self.sleeper(interval_seconds)
                continue
            try:
                summary = self.poll_once()
            except Exception:
                failures += 1
                delay = min(600, interval_seconds if failures == 1 else delay * 2)
                # Never log exception text: request URLs and provider messages may contain keys.
                summary = {"status": "error", "error": "DART poll failed"}
            else:
                failures = 0
                delay = interval_seconds
            attempts += 1
            print(json.dumps({**summary, "consecutive_failures": failures,
                              "next_poll_seconds": delay}, ensure_ascii=False), flush=True)
            if max_polls is None or attempts < max_polls:
                self.sleeper(delay)
