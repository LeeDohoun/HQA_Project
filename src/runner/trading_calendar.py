"""Versioned XKRX sessions, including exchange holidays and special closes."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import exchange_calendars as calendars
import pandas as pd
from exchange_calendars.exchange_calendar_xkrx import XKRXExchangeCalendar

CALENDAR_VERSION = "exchange-calendars:" + calendars.__version__ + ":XKRX:krx-notices-2021-2026-merged-v4"
# Review record (2026-09-27): the regular 09:00-15:30 KRX session is unchanged. The
# KRX after-market that opened in September 2026 does not set the regular close,
# market value or index, so daily close times stay regular-session times; its volume
# is added to daily statistics, which the collector only reads for past sessions.
# The next announced structural change is the pre-market planned for late 2027, so
# every session after CALENDAR_REVIEWED_THROUGH must be reviewed again.
CALENDAR_REVIEWED_ON = "2026-09-27"
CALENDAR_REVIEWED_THROUGH = "2027-09-30"
# Dates with a known special schedule whose official KRX session-hour notice is not
# yet published. They stay blocked, not guessed, until the notice's open and close are
# added to SPECIAL_CLOSES (KRX usually publishes it about two weeks in advance).
PENDING_SPECIAL_SESSION_NOTICES = {
    "2026-11-19": {
        "reason": "2027 CSAT exam day (Ministry of Education schedule); KRX session-hour notice not yet published",
        "source_urls": ["https://eiec.kdi.re.kr/policy/materialView.do?num=255989"],
    },
}
CALENDAR_WARNING_DAYS = 21
# KRX announcement dated 2026-05-20: exchange holidays announced after the pinned
# exchange-calendars release. One override shared by research, forward, live analysis
# and price validation.
EXCHANGE_HOLIDAY_OVERRIDES = {
    "2026-06-03": {
        "name": "9th nationwide local elections", "announced_at": "2026-05-20",
        "reason": "Local election day; KRX all-market closure",
        "source": "https://news.bizwatch.co.kr/article/market/2026/05/20/0023",
        "source_urls": ["https://www.mt.co.kr/stock/2026/05/20/2026052010314634386",
                        "https://www.hankyung.com/article/2026052094456"],
    },
    "2026-07-17": {
        "name": "Constitution Day (public holiday again from 2026)", "announced_at": "2026-05-20",
        "reason": "Constitution Day restored as a statutory holiday in 2026; KRX all-market closure",
        "source": "https://news.bizwatch.co.kr/article/market/2026/05/20/0023",
        "source_urls": ["https://www.fnnews.com/news/202605201034458662",
                        "https://www.hankyung.com/article/2026052094456"],
    },
}
KRX_ADDITIONAL_CLOSED_DAYS = EXCHANGE_HOLIDAY_OVERRIDES
# Research, forward records and holdout preflight use only officially announced years.
CALENDAR_VERIFIED_THROUGH = "2026-12-31"


def require_verified_calendar(day) -> None:
    if pd.Timestamp(day) > pd.Timestamp(CALENDAR_VERIFIED_THROUGH):
        raise ValueError(f"calendar_unverified:{pd.Timestamp(day).date()}:official_KRX_2027_holiday_notice_required")


class _HolidayXKRX(XKRXExchangeCalendar):
    @property
    def adhoc_holidays(self):
        return list(pd.DatetimeIndex(super().adhoc_holidays).union(pd.DatetimeIndex(EXCHANGE_HOLIDAY_OVERRIDES)))


class _ProjectXKRX(_HolidayXKRX):

    def _date_oob(self, day):
        require_verified_calendar(day)
        return super()._date_oob(day)

    def session_offset(self, session, count, _parse=True):
        require_verified_calendar(session)
        position = self.sessions.searchsorted(pd.Timestamp(session)) + count
        if position >= len(self.sessions) and self.last_session.year == 2026:
            raise ValueError("calendar_unverified:session_offset:official_KRX_2027_holiday_notice_required")
        return super().session_offset(session, count, _parse=_parse)

    def date_to_session(self, date, direction="none", _parse=True):
        require_verified_calendar(date)
        if (direction == "next" and pd.Timestamp(date) > self.last_session
                and self.last_session.year == 2026):
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


# Days that may have special KRX hours but whose official notice is not yet
# recorded in SPECIAL_CLOSES. Only these fail closed; other verified-year days
# use the dependency's regular schedule. Derived from PENDING_SPECIAL_SESSION_NOTICES
# (2026-11-19, 2027 CSAT); move a day to SPECIAL_CLOSES with the official notice.
PENDING_SPECIAL_SESSIONS = {day: notice["reason"] for day, notice in PENDING_SPECIAL_SESSION_NOTICES.items()}
SPECIAL_CLOSES = {
    "2021-11-18": {
        "open": "2021-11-18T10:00:00+09:00", "close": "2021-11-18T16:30:00+09:00",
        "published_at": "2021-11-04T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2021/11/04/000107/20211104000121/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2021/11/04/000104/20211101000204/70780.htm",
        },
    },
    "2022-11-17": {
        "open": "2022-11-17T10:00:00+09:00", "close": "2022-11-17T16:30:00+09:00",
        "published_at": "2022-11-03T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2022/11/03/000103/20221102001061/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2022/11/03/000105/20221019000846/70780.htm",
        },
    },
    "2023-11-16": {
        "open": "2023-11-16T10:00:00+09:00", "close": "2023-11-16T16:30:00+09:00",
        "published_at": "2023-11-02T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2023/11/02/000068/20231102000001/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2023/11/02/000075/20231101001384/70780.htm",
        },
    },
    "2024-11-14": {
        "open": "2024-11-14T10:00:00+09:00", "close": "2024-11-14T16:30:00+09:00",
        "published_at": "2024-10-31T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2024/10/31/000086/20241031000185/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2024/10/31/000078/20241021000338/70780.htm",
        },
    },
    "2025-11-13": {
        "open": "2025-11-13T10:00:00+09:00", "close": "2025-11-13T16:30:00+09:00",
        "published_at": "2025-10-30T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2025/10/30/000102/20251030000137/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2025/10/30/000121/20251021000455/70780.htm",
        },
    },
}


def _check_special_session_coverage(day: str) -> None:
    # The dependency's CSAT table ends in 2020. Pending days fail closed rather
    # than guessing regular hours; live days after the reviewed horizon fail too.
    if day > CALENDAR_REVIEWED_THROUGH:
        raise ValueError(f"calendar_review_expired:{day}:reviewed_through={CALENDAR_REVIEWED_THROUGH}")
    if day in PENDING_SPECIAL_SESSIONS and day not in SPECIAL_CLOSES:
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
    # Research and forward records stop at CALENDAR_VERIFIED_THROUGH until the official
    # KRX holiday notice for the next year is added (a mechanical, sourced update).
    remaining = (date.fromisoformat(CALENDAR_VERIFIED_THROUGH) - today).days
    if remaining <= warning_days:
        state = "blocked" if remaining < 0 else f"blocks_in_{remaining}_days"
        warnings.append(f"calendar_notice_required:research_verified_through:{CALENDAR_VERIFIED_THROUGH}:{state}:"
                        "official KRX holiday notice for the next year required by research and forward records")
    remaining = (date.fromisoformat(CALENDAR_REVIEWED_THROUGH) - today).days
    if remaining <= warning_days:
        state = "expired" if remaining < 0 else f"expires_in_{remaining}_days"
        warnings.append(f"calendar_review_required:{CALENDAR_REVIEWED_THROUGH}:{state}")
    return warnings


@lru_cache(maxsize=8)
def _calendar(year: int):
    """Research calendar: officially verified years only."""
    require_verified_calendar(f"{year}-01-01")
    if not 1956 <= year <= 2050:
        raise ValueError("XKRX_calendar_year_out_of_supported_range")
    return _ProjectXKRX(start=f"{max(1956, year - 2)}-01-01",
                        end=f"{min(2026, year + 1)}-12-31")


@lru_cache(maxsize=8)
def _live_calendar(year: int):
    """Live analysis/monitor calendar: reviewed through CALENDAR_REVIEWED_THROUGH."""
    if not 1956 <= year <= 2050:
        raise ValueError("XKRX_calendar_year_out_of_supported_range")
    return _HolidayXKRX(start=f"{max(1956, year - 2)}-01-01", end=f"{min(2050, year + 1)}-12-31")


def is_trading_day(day: str) -> bool:
    """True when XKRX holds a regular or special session on this KST date."""
    parsed = date.fromisoformat(day)
    return bool(_live_calendar(parsed.year).is_session(day))


def _session_bound(day: str, bound: str) -> datetime:
    parsed = date.fromisoformat(day)
    if parsed.isoformat() != day:
        raise ValueError("price trade date requires YYYY-MM-DD")
    if day > CALENDAR_REVIEWED_THROUGH:
        raise ValueError(f"calendar_review_expired:{day}:reviewed_through={CALENDAR_REVIEWED_THROUGH}")
    calendar = _live_calendar(parsed.year)
    if not calendar.is_session(day):
        raise ValueError(f"nontrading_price_date:{day}")
    _check_special_session_coverage(day)
    if day in SPECIAL_CLOSES:
        return pd.Timestamp(SPECIAL_CLOSES[day][bound]).tz_convert("UTC").to_pydatetime()
    return (calendar.session_open if bound == "open" else calendar.session_close)(day).to_pydatetime()


@lru_cache(maxsize=2048)
def daily_session_open(day: str) -> datetime:
    return _session_bound(day, "open")


@lru_cache(maxsize=2048)
def daily_session_close(day: str) -> datetime:
    return _session_bound(day, "close")


def completed_daily_sessions(as_of: datetime, count: int = 300) -> list[tuple[str, datetime]]:
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("price as_of requires an aware timestamp")
    if type(count) is not int or not 1 <= count <= 300:
        raise ValueError("completed session count must be between 1 and 300")
    cutoff = pd.Timestamp(as_of)
    korean_day = cutoff.tz_convert("Asia/Seoul")
    # Only the sessions being returned are verified (each is checked below), so a
    # pending special day does not block the previous day's catch-up before it.
    if korean_day.date().isoformat() > CALENDAR_REVIEWED_THROUGH:
        raise ValueError(f"calendar_review_expired:{korean_day.date().isoformat()}:"
                         f"reviewed_through={CALENDAR_REVIEWED_THROUGH}")
    calendar = _live_calendar(korean_day.year)
    schedule = calendar.schedule.copy()
    for day, notice in SPECIAL_CLOSES.items():
        if pd.Timestamp(day) in schedule.index:
            schedule.loc[pd.Timestamp(day), "close"] = pd.Timestamp(notice["close"]).tz_convert("UTC")
    completed = schedule.loc[schedule["close"] <= cutoff].tail(count)
    for session in completed.index:
        _check_special_session_coverage(session.date().isoformat())
    return [(session.date().isoformat(), close.to_pydatetime())
            for session, close in completed["close"].items()]
