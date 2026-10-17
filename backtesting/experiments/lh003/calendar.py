"""Pinned XKRX dates, fixed calendar-week slots and reviewed calendar changes."""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from functools import lru_cache

import exchange_calendars as calendars
import pandas as pd

from src.runner import trading_calendar as shared

from . import CALENDAR_VERSION, EVALUATION_AT, Refused
from .storage import KST, aware, digest, seal, verified

WEEKS = pd.period_range("2026-11-13", "2027-11-05", freq="W-FRI")


def exchange(*, end=None):
    if calendars.__version__ != CALENDAR_VERSION:
        raise Refused(f"LH003 requires exchange-calendars=={CALENDAR_VERSION}, found {calendars.__version__}")
    end = shared.CALENDAR_VERIFIED_THROUGH if end is None else end
    shared.require_verified_calendar(end)
    return _exchange(end, shared.CALENDAR_VERSION, digest(shared.KRX_ADDITIONAL_CLOSED_DAYS))


@lru_cache(maxsize=8)
def _exchange(end, version, notices_sha256):
    return shared._ProjectXKRX(start="2025-01-01", end=end)


def session_close(day):
    return shared.daily_session_close(pd.Timestamp(day).date().isoformat())


def plan(sessions=None):
    # Verify the entire fixed horizon before producing even the first week.
    days = exchange(end="2027-12-17").sessions if sessions is None else pd.DatetimeIndex(sessions)
    days = days.tz_localize(None).normalize().sort_values().unique()
    days = days[days < pd.Timestamp(EVALUATION_AT).tz_localize(None)]
    horizon = days[(days >= WEEKS[0].start_time) & (days <= pd.Timestamp("2027-12-17"))]
    for day in horizon:
        shared.require_verified_calendar(day)
        shared._check_special_session_coverage(day.date().isoformat())
    rows = []
    for index, week in enumerate(WEEKS):
        weekly = days[days.to_period("W-FRI") == week]
        if not len(weekly):
            raise Refused(f"no XKRX session in fixed week {week}")
        decision = weekly[-1]
        pos = days.get_loc(decision)
        if pos + 20 >= len(days):
            raise Refused("calendar lacks the entry-inclusive twentieth session")
        entry, exit_day = days[pos + 1], days[pos + 20]
        rows.append({"index": index, "week": str(week), "decision_date": decision.date().isoformat(),
                     "entry_date": entry.date().isoformat(), "exit_date": exit_day.date().isoformat(),
                     "deadline": datetime.combine(entry.date(), time(8, 59), KST).isoformat()})
    return rows


def description(sessions=None):
    days = exchange(end="2027-12-17").sessions if sessions is None else pd.DatetimeIndex(sessions)
    days = days.tz_localize(None).normalize().sort_values().unique()
    window = days[(days >= WEEKS[0].start_time) & (days <= WEEKS[-1].end_time)]
    horizon = days[(days >= WEEKS[0].start_time) & (days <= pd.Timestamp("2027-12-17"))]
    return {"version": shared.CALENDAR_VERSION, "decisions": plan(days),
            "sessions": [x.date().isoformat() for x in window],
            "sessions_sha256": digest([x.date().isoformat() for x in window]),
            "execution_sessions_sha256": digest([x.date().isoformat() for x in horizon])}


def check(root, *, at, operator_note=None, sessions=None):
    current = description(sessions)
    directory = Path(root) / "calendar"
    files = sorted(directory.glob("*.json"))
    previous = verified(files[-1]) if files else None
    if previous is None and (Path(root) / "start.json").exists():
        raise Refused("frozen calendar journal missing after start; refuse to recreate it")
    if previous is not None and previous["calendar"] == current:
        return current
    if previous is not None and not (operator_note and operator_note.strip()):
        raise Refused("calendar session list changed after start; explicit operator note required")
    seal(directory / f"{len(files):03d}.json", {"at": aware(at).isoformat(), "calendar": current,
         "previous_sha256": digest(previous["calendar"]) if previous else None, "operator_note": operator_note})
    return current


def latest_completed_week(at):
    at = pd.Timestamp(aware(at))
    shared.require_verified_calendar(at.date())
    calendar = exchange()
    days = calendar.sessions
    weekly = pd.Series(days, index=days.to_period("W-FRI")).groupby(level=0).max()
    eligible = [day for day in weekly if day <= at.tz_localize(None).normalize() and session_close(day) <= at]
    return eligible[-1].date().isoformat()
