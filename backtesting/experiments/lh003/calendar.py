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


def plan(sessions=None, *, decision_day=None):
    # Resolve one needed slot in production. Asking for the full plan explicitly
    # requires full coverage; it is never a start/weekly prerequisite.
    days = exchange().sessions if sessions is None else pd.DatetimeIndex(sessions)
    days = days.tz_localize(None).normalize().sort_values().unique()
    days = days[days < pd.Timestamp(EVALUATION_AT).tz_localize(None)]
    rows = []
    for index, week in enumerate(WEEKS):
        if decision_day is not None and pd.Timestamp(decision_day).to_period("W-FRI") != week:
            continue
        shared.require_verified_calendar(week.end_time.normalize())
        weekly = days[days.to_period("W-FRI") == week]
        if not len(weekly):
            raise Refused(f"no XKRX session in fixed week {week}")
        decision = weekly[-1]
        pos = days.get_loc(decision)
        if pos + 20 >= len(days):
            if sessions is None:
                shared.require_verified_calendar(pd.Timestamp(shared.CALENDAR_VERIFIED_THROUGH) + pd.Timedelta(days=1))
            raise Refused("calendar lacks the entry-inclusive twentieth session")
        entry, exit_day = days[pos + 1], days[pos + 20]
        shared.require_verified_calendar(exit_day)
        shared._check_special_session_coverage(decision.date().isoformat())
        rows.append({"index": index, "week": str(week), "decision_date": decision.date().isoformat(),
                     "entry_date": entry.date().isoformat(), "exit_date": exit_day.date().isoformat(),
                     "deadline": datetime.combine(entry.date(), time(8, 59), KST).isoformat()})
    return rows


def description(sessions=None, *, decision_day="2026-11-13"):
    days = exchange().sessions if sessions is None else pd.DatetimeIndex(sessions)
    days = days.tz_localize(None).normalize().sort_values().unique()
    decisions = plan(None if sessions is None else days, decision_day=decision_day)
    if len(decisions) != 1:
        raise Refused("decision day is outside the 52 fixed XKRX weeks")
    needed = decisions[0]
    week = WEEKS[needed["index"]]
    window = days[(days >= week.start_time) & (days <= week.end_time)]
    horizon = days[(days >= week.start_time) & (days <= pd.Timestamp(needed["exit_date"]))]
    return {"version": shared.CALENDAR_VERSION, "slots": [{"index": i, "week": str(w)} for i, w in enumerate(WEEKS)],
            "decision_scope": needed["index"], "decisions": decisions,
            "sessions": [x.date().isoformat() for x in window],
            "sessions_sha256": digest([x.date().isoformat() for x in window]),
            "execution_sessions_sha256": digest([x.date().isoformat() for x in horizon])}


def check(root, *, at, operator_note=None, sessions=None, decision_day="2026-11-13"):
    current = description(sessions, decision_day=decision_day)
    directory = Path(root) / "calendar"
    files = sorted(directory.glob("*.json"))
    previous = next((v for p in reversed(files) if (v := verified(p))["calendar"]["decision_scope"] == current["decision_scope"]), None)
    if not files and (Path(root) / "start.json").exists():
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
