"""Versioned XKRX sessions, including exchange holidays and special closes."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache

import exchange_calendars as calendars
import pandas as pd

CALENDAR_VERSION = "exchange-calendars:" + calendars.__version__ + ":XKRX:krx-notices-2024-2026-v2"
# Review record (2026-09-27): the regular 09:00-15:30 KRX session is unchanged. The
# KRX after-market that opened in September 2026 does not set the regular close,
# market value or index, so daily close times stay regular-session times; its volume
# is added to daily statistics, which the collector only reads for past sessions.
# The next announced structural change is the pre-market planned for late 2027, so
# every session after CALENDAR_REVIEWED_THROUGH must be reviewed again.
CALENDAR_REVIEWED_ON = "2026-09-27"
CALENDAR_REVIEWED_THROUGH = "2027-09-30"
# Dates with a known special schedule whose official KRX session-hour notice is not
# yet published. They stay blocked, not guessed, until the notice is added to
# SPECIAL_CLOSES (KRX usually publishes it about two weeks in advance).
PENDING_SPECIAL_SESSION_NOTICES = {
    "2026-11-19": {
        "reason": "2027 CSAT exam day (Ministry of Education schedule); KRX session-hour notice not yet published",
        "source_urls": ["https://eiec.kdi.re.kr/policy/materialView.do?num=255989"],
    },
}
CALENDAR_WARNING_DAYS = 21
# Exchange holidays announced after the pinned exchange-calendars release. KRX
# announced both closures on 2026-05-20; without them every price history since
# June 2026 looks incomplete ("missing sessions") and no stock can be analyzed.
EXCHANGE_HOLIDAY_OVERRIDES = {
    "2026-06-03": {"name": "9th nationwide local elections", "announced_at": "2026-05-20",
                   "source_urls": ["https://www.mt.co.kr/stock/2026/05/20/2026052010314634386",
                                   "https://www.hankyung.com/article/2026052094456"]},
    "2026-07-17": {"name": "Constitution Day (public holiday again from 2026)", "announced_at": "2026-05-20",
                   "source_urls": ["https://www.fnnews.com/news/202605201034458662",
                                   "https://www.hankyung.com/article/2026052094456"]},
}
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
    if day > CALENDAR_REVIEWED_THROUGH:
        raise ValueError(f"calendar_review_expired:{day}:reviewed_through={CALENDAR_REVIEWED_THROUGH}")
    if (day in PENDING_SPECIAL_SESSION_NOTICES and day not in SPECIAL_CLOSES) or (
            "2021" <= day[:4] <= "2023" and day[5:7] == "11"):
        raise ValueError(f"calendar_special_session_coverage_unverified:{day}:official_KRX_notice_required")


def calendar_review_warnings(today: date, warning_days: int = CALENDAR_WARNING_DAYS) -> list[str]:
    """Operator warnings for calendar reviews that will block price analysis soon."""
    if not isinstance(today, date) or type(warning_days) is not int or warning_days < 0:
        raise ValueError("calendar warnings require a date and a nonnegative day count")
    today = today if not isinstance(today, datetime) else today.date()
    warnings = []
    for day, pending in sorted(PENDING_SPECIAL_SESSION_NOTICES.items()):
        remaining = (date.fromisoformat(day) - today).days
        if day not in SPECIAL_CLOSES and remaining <= warning_days:
            state = "blocked" if remaining <= 0 else f"blocks_in_{remaining}_days"
            warnings.append(f"calendar_notice_required:{day}:{state}:{pending['reason']}")
    remaining = (date.fromisoformat(CALENDAR_REVIEWED_THROUGH) - today).days
    if remaining <= warning_days:
        state = "expired" if remaining < 0 else f"expires_in_{remaining}_days"
        warnings.append(f"calendar_review_required:{CALENDAR_REVIEWED_THROUGH}:{state}")
    return warnings


@lru_cache(maxsize=8)
def _calendar(year: int):
    # XKRX's bundled precomputed calendar explicitly covers 1956 through 2050.
    if not 1956 <= year <= 2050:
        raise ValueError("XKRX_calendar_year_out_of_supported_range")
    return calendars.get_calendar("XKRX", start=f"{max(1956, year - 2)}-01-01",
                                  end=f"{min(2050, year + 1)}-12-31")


def is_trading_day(day: str) -> bool:
    """True when XKRX holds a regular or special session on this KST date."""
    parsed = date.fromisoformat(day)
    return bool(_calendar(parsed.year).is_session(day)) and day not in EXCHANGE_HOLIDAY_OVERRIDES


@lru_cache(maxsize=2048)
def daily_session_close(day: str) -> datetime:
    parsed = date.fromisoformat(day)
    if parsed.isoformat() != day:
        raise ValueError("price trade date requires YYYY-MM-DD")
    calendar = _calendar(parsed.year)
    if not calendar.is_session(day) or day in EXCHANGE_HOLIDAY_OVERRIDES:
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
    holidays = [pd.Timestamp(day) for day in EXCHANGE_HOLIDAY_OVERRIDES if pd.Timestamp(day) in schedule.index]
    schedule = schedule.drop(index=holidays)
    for day, notice in SPECIAL_CLOSES.items():
        if pd.Timestamp(day) in schedule.index:
            schedule.loc[pd.Timestamp(day), "close"] = pd.Timestamp(notice["close"]).tz_convert("UTC")
    completed = schedule.loc[schedule["close"] <= cutoff].tail(count)
    for session in completed.index:
        _check_special_session_coverage(session.date().isoformat())
    return [(session.date().isoformat(), close.to_pydatetime())
            for session, close in completed["close"].items()]
