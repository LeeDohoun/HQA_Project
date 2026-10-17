"""Versioned XKRX sessions, including exchange holidays and special closes."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import exchange_calendars as calendars
import pandas as pd
from exchange_calendars.exchange_calendar_xkrx import XKRXExchangeCalendar

CALENDAR_VERSION = "exchange-calendars:" + calendars.__version__ + ":XKRX:krx-notices-through-2026-v3"
# KRX announcement dated 2026-05-20, supplied and verified for this project.
# One project override shared by research, forward and price validation.
KRX_ADDITIONAL_CLOSED_DAYS = {
    "2026-06-03": {
        "reason": "Local election day; KRX all-market closure",
        "source": "https://news.bizwatch.co.kr/article/market/2026/05/20/0023",
    },
    "2026-07-17": {
        "reason": "Constitution Day restored as a statutory holiday in 2026; KRX all-market closure",
        "source": "https://news.bizwatch.co.kr/article/market/2026/05/20/0023",
    },
}
CALENDAR_VERIFIED_THROUGH = "2026-12-31"


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
# use the dependency's regular schedule. 2026-11-19 is the 2027 CSAT
# (Ministry of Education, announced 2024-08-16); KRX usually publishes the
# changed hours about two weeks before. Move it to SPECIAL_CLOSES with the
# official notice when published.
PENDING_SPECIAL_SESSIONS = {
    "2026-11-19": "2027학년도 대학수학능력시험; KRX special-hours notice pending",
}
SPECIAL_CLOSES = {
    "2021-11-18": {
        "close": "2021-11-18T16:30:00+09:00", "published_at": "2021-11-04T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2021/11/04/000107/20211104000121/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2021/11/04/000104/20211101000204/70780.htm",
        },
    },
    "2022-11-17": {
        "close": "2022-11-17T16:30:00+09:00", "published_at": "2022-11-03T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2022/11/03/000103/20221102001061/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2022/11/03/000105/20221019000846/70780.htm",
        },
    },
    "2023-11-16": {
        "close": "2023-11-16T16:30:00+09:00", "published_at": "2023-11-02T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2023/11/02/000068/20231102000001/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2023/11/02/000075/20231101001384/70780.htm",
        },
    },
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
                        end=f"{min(2026, year + 1)}-12-31")


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
