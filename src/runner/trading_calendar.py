"""Versioned XKRX sessions, including exchange holidays and special closes."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache

import exchange_calendars as calendars
import pandas as pd

CALENDAR_VERSION = "exchange-calendars:" + calendars.__version__ + ":XKRX:krx-notices-2024-2025-v1"
SPECIAL_SESSION_REVIEW_REQUIRED_FROM = "2026-11-01"
SPECIAL_CLOSES = {
    "2024-11-14": {
        "close": "2024-11-14T16:30:00+09:00", "published_at": "2024-10-31T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2024/10/31/000086/20241031000185/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2024/10/31/000078/20241021000338/70780.htm",
        },
    },
    "2025-11-13": {
        "close": "2025-11-13T16:30:00+09:00", "published_at": "2025-10-30T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2025/10/30/000102/20251030000137/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2025/10/30/000121/20251021000455/70780.htm",
        },
    },
}


def _check_special_session_coverage(day: str) -> None:
    # The dependency's CSAT table ends in 2020. These are review boundaries,
    # not inferred exam dates or invented market hours.
    if day >= SPECIAL_SESSION_REVIEW_REQUIRED_FROM or (
            "2021" <= day[:4] <= "2023" and day[5:7] == "11"):
        raise ValueError(f"calendar_special_session_coverage_unverified:{day}:official_KRX_notice_required")


@lru_cache(maxsize=8)
def _calendar(year: int):
    # XKRX's bundled precomputed calendar explicitly covers 1956 through 2050.
    if not 1956 <= year <= 2050:
        raise ValueError("XKRX_calendar_year_out_of_supported_range")
    return calendars.get_calendar("XKRX", start=f"{max(1956, year - 2)}-01-01",
                                  end=f"{min(2050, year + 1)}-12-31")


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
    _check_special_session_coverage(korean_day.date().isoformat())
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
