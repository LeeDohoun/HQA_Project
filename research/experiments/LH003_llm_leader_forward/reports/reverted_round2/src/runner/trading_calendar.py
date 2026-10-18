"""Versioned XKRX sessions, including exchange holidays and special closes."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
from exchange_calendars.exchange_calendar_xkrx import XKRXExchangeCalendar

from src.krx_calendar_data import (
    CALENDAR_VERSION, CALENDAR_VERIFIED_THROUGH, KRX_ADDITIONAL_CLOSED_DAYS,
    PENDING_SPECIAL_SESSIONS, SPECIAL_CLOSES,
)


def require_verified_calendar(day) -> None:
    if pd.Timestamp(day) > pd.Timestamp(CALENDAR_VERIFIED_THROUGH):
        raise ValueError(f"calendar_unverified:{pd.Timestamp(day).date()}:official_KRX_2027_holiday_notice_required")


class _ProjectXKRX(XKRXExchangeCalendar):
    @property
    def adhoc_holidays(self):
        return list(pd.DatetimeIndex(super().adhoc_holidays).union(pd.DatetimeIndex(KRX_ADDITIONAL_CLOSED_DAYS)))

    def _date_oob(self, day):
        require_verified_calendar(day)
        return super()._date_oob(day)

    def session_offset(self, session, count, _parse=True):
        require_verified_calendar(session)
        position = self.sessions.searchsorted(pd.Timestamp(session)) + count
        if position >= len(self.sessions) and self.last_session.year == pd.Timestamp(CALENDAR_VERIFIED_THROUGH).year:
            raise ValueError("calendar_unverified:session_offset:official_KRX_2027_holiday_notice_required")
        return super().session_offset(session, count, _parse=_parse)

    def date_to_session(self, date, direction="none", _parse=True):
        require_verified_calendar(date)
        if (direction == "next" and pd.Timestamp(date) > self.last_session
                and self.last_session.year == pd.Timestamp(CALENDAR_VERIFIED_THROUGH).year):
            raise ValueError("calendar_unverified:next_session:official_KRX_2027_holiday_notice_required")
        return super().date_to_session(date, direction, _parse=_parse)

    def next_session(self, session, _parse=True):
        return self.session_offset(session, 1, _parse=_parse)

    def sessions_window(self, session, count, _parse=True):
        if count:
            self.session_offset(session, count - (1 if count > 0 else -1), _parse=_parse)
        return super().sessions_window(session, count, _parse=_parse)


@lru_cache(maxsize=16)
def session_calendar(start, end) -> pd.DatetimeIndex:
    """Inclusive date range; never expose dependency guesses for 2027 onwards."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    require_verified_calendar(end)
    if start > end:
        raise ValueError("calendar start must not exceed end")
    return _ProjectXKRX(start=start, end=end).sessions


def preflight_price_sessions(data_dir, start, end, *, price_days=None):
    """Compare sessions to nonempty daily-file inventory, without opening prices.

    Metadata-only checks are safe before a protected ledger claim. Supplied
    price days support the same check for in-memory experiment fixtures.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    expected = session_calendar(start, end)
    if price_days is None:
        directory = Path(data_dir) / "market/krx_daily"
        price_days = [pd.Timestamp(p.stem) for p in directory.glob("*/*.jsonl")
                      if len(p.stem) == 8 and p.stem.isdigit() and p.stat().st_size]
    actual = pd.DatetimeIndex(price_days).sort_values().unique()
    actual = actual[(actual >= start) & (actual <= end)]
    missing, unexpected = expected.difference(actual), actual.difference(expected)
    report = {"start": start.date().isoformat(), "end": end.date().isoformat(),
              "calendar_version": CALENDAR_VERSION, "sessions": len(expected),
              "price_sessions": len(actual),
              "missing": missing.strftime("%Y-%m-%d").tolist(),
              "unexpected": unexpected.strftime("%Y-%m-%d").tolist()}
    if len(missing) or len(unexpected):
        raise ValueError(f"calendar_price_session_mismatch:missing={report['missing']}:unexpected={report['unexpected']}")
    return report




def _check_special_session_coverage(day: str) -> None:
    # The dependency's CSAT table ends in 2020. Pending days fail closed rather
    # than guessing regular hours; years after the verified range fail in
    # require_verified_calendar.
    if day in PENDING_SPECIAL_SESSIONS and day not in SPECIAL_CLOSES:
        raise ValueError(f"calendar_special_session_coverage_unverified:{day}:official_KRX_notice_required")


@lru_cache(maxsize=8)
def _calendar(year: int):
    require_verified_calendar(f"{year}-01-01")
    # Keep the dependency schedule bounded by the officially verified year.
    if not 1956 <= year <= 2050:
        raise ValueError("XKRX_calendar_year_out_of_supported_range")
    return _ProjectXKRX(start=f"{max(1956, year - 2)}-01-01",
                        end=f"{min(pd.Timestamp(CALENDAR_VERIFIED_THROUGH).year, year + 1)}-12-31")


@lru_cache(maxsize=2048)
def daily_session_close(day: str) -> datetime:
    parsed = date.fromisoformat(day)
    if parsed.isoformat() != day:
        raise ValueError("price trade date requires YYYY-MM-DD")
    calendar = _calendar(parsed.year)
    if not calendar.is_session(day):
        raise ValueError(f"nontrading_price_date:{day}")
    _check_special_session_coverage(day)
    if day in SPECIAL_CLOSES:
        return pd.Timestamp(SPECIAL_CLOSES[day]["close"]).tz_convert("UTC").to_pydatetime()
    return calendar.session_close(day).to_pydatetime()


def completed_daily_sessions(as_of: datetime, count: int = 300) -> list[tuple[str, datetime]]:
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("price as_of requires an aware timestamp")
    if type(count) is not int or not 1 <= count <= 300:
        raise ValueError("completed session count must be between 1 and 300")
    cutoff = pd.Timestamp(as_of)
    korean_day = cutoff.tz_convert("Asia/Seoul")
    require_verified_calendar(korean_day.date().isoformat())
    calendar = _calendar(korean_day.year)
    schedule = calendar.schedule.copy()
    for day, notice in SPECIAL_CLOSES.items():
        if pd.Timestamp(day) in schedule.index:
            schedule.loc[pd.Timestamp(day), "close"] = pd.Timestamp(notice["close"]).tz_convert("UTC")
    completed = schedule.loc[schedule["close"] <= cutoff].tail(count)
    for session in completed.index:
        _check_special_session_coverage(session.date().isoformat())
    return [(session.date().isoformat(), close.to_pydatetime())
            for session, close in completed["close"].items()]
