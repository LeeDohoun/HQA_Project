"""Pinned XKRX dates, fixed calendar-week slots and reviewed calendar changes."""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import exchange_calendars as calendars
import pandas as pd

from . import CALENDAR_VERSION, Refused
from .storage import KST, aware, digest, seal, verified

WEEKS = pd.period_range("2026-11-13", "2027-11-05", freq="W-FRI")


def exchange():
    if calendars.__version__ != CALENDAR_VERSION:
        raise Refused(f"LH003 requires exchange-calendars=={CALENDAR_VERSION}, found {calendars.__version__}")
    return calendars.get_calendar("XKRX", start="2025-01-01", end="2027-12-31")


def plan(sessions=None):
    days = exchange().sessions if sessions is None else pd.DatetimeIndex(sessions)
    days = days.tz_localize(None).normalize().sort_values().unique()
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
    days = exchange().sessions if sessions is None else pd.DatetimeIndex(sessions)
    days = days.tz_localize(None).normalize().sort_values().unique()
    window = days[(days >= WEEKS[0].start_time) & (days <= WEEKS[-1].end_time)]
    horizon = days[(days >= WEEKS[0].start_time) & (days <= pd.Timestamp("2027-12-17"))]
    return {"version": f"exchange-calendars:{CALENDAR_VERSION}:XKRX", "decisions": plan(days),
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
    calendar = exchange()
    days = calendar.sessions
    weekly = pd.Series(days, index=days.to_period("W-FRI")).groupby(level=0).max()
    eligible = [day for day in weekly if calendar.session_close(day) <= at]
    return eligible[-1].date().isoformat()
