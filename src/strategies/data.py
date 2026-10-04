"""Point-in-time views of supplied KRX daily prices and DART events.

Decision timestamps must be timezone aware. Daily rows become usable at their
bar_at (or verified exchange close) plus 30 minutes. For unverified calendar
rows, trade_date 16:30 KST is a conservative lower bound; a later supplied
bar_at plus buffer still wins. Historical collected_at/available_at are not
price availability: backfills collect years after the original session.

Events use first_seen_at when supplied by forward observations. Otherwise a
date-only rcept_dt becomes the next XKRX session date at 09:00 KST. Existing
event-study inputs with only available_at are also accepted (date or aware
timestamp). Views include available_at <= T. Execution must be strictly later
than availability, as in event_study: do not reinterpret the synthesized 09:00
as permission to fill at that same open. Pass the original date-only rcept_dt
to event_study for its next-session-open historical convention.

Lookbacks count shared supplied price sessions, not per-stock observations,
and elapsed days of effective event availability. Views and constructor inputs
are copied; raw future rows have no public accessor. Missing data are not filled.
"""
from __future__ import annotations

from datetime import date
import re

import pandas as pd

from . import _runner_module

KST = "Asia/Seoul"


def _timestamp(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("decision and observation timestamps must be timezone-aware")
    return stamp.tz_convert(KST)


def _date(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is not None or stamp != stamp.normalize():
        raise ValueError("trade_date and rcept_dt must be date-only")
    return stamp


def _next_open(value) -> pd.Timestamp:
    day = _date(value) + pd.Timedelta(days=1)
    calendar = _runner_module("trading_calendar")._calendar(day.year)
    session = calendar.date_to_session(day, direction="next")
    return session.tz_localize(KST) + pd.Timedelta(hours=9)


def _event_availability(row: dict) -> pd.Timestamp:
    if pd.notna(row.get("first_seen_at")):
        observed = _timestamp(row["first_seen_at"])
        if pd.notna(row.get("rcept_dt")) and observed < _date(row["rcept_dt"]).tz_localize(KST):
            raise ValueError("first_seen_at cannot precede the disclosure date")
        return observed
    if pd.notna(row.get("rcept_dt")):
        return _next_open(row["rcept_dt"])
    value = row.get("available_at")
    if type(value) is date or isinstance(value, str) and re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        return _next_open(value)
    return _timestamp(value)


def _lookback(value, name):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive integer or None for all usable history")


def _price_availability(day, bar_at, calendar_status) -> pd.Timestamp:
    verified = calendar_status == "verified"
    bound = day.tz_localize(KST) + pd.Timedelta(hours=16, minutes=30)
    if pd.notna(bar_at):
        close = _timestamp(bar_at)
        if close.tz_localize(None).normalize() != day:
            raise ValueError("bar_at must belong to its trade_date in KST")
        known = close + pd.Timedelta(minutes=30)
    elif verified:
        known = _timestamp(_runner_module("trading_calendar").daily_session_close(day.date().isoformat()))
        known += pd.Timedelta(minutes=30)
    else:
        known = bound
    # collected_at describes backfill collection, not historical knowledge.
    return known if verified else max(known, bound)


class PointInTimeData:
    def __init__(self, prices: pd.DataFrame, events: pd.DataFrame):
        if not isinstance(prices.index, pd.MultiIndex) or prices.index.names != ["trade_date", "stock_code"]:
            raise ValueError("prices must be indexed by (trade_date, stock_code)")
        self._prices = prices.copy(deep=True)
        raw_days = prices.index.get_level_values("trade_date")
        distinct_days = raw_days.unique()
        days = pd.DatetimeIndex([_date(value) for value in distinct_days]).take(distinct_days.get_indexer(raw_days))
        codes = prices.index.get_level_values("stock_code")
        self._prices.index = pd.MultiIndex.from_arrays([days, codes], names=prices.index.names)
        if self._prices.index.has_duplicates or codes.isna().any():
            raise ValueError("price keys must be unique and nonmissing")
        self._prices = self._prices.sort_index()
        keys = pd.DataFrame({"trade_date": self._prices.index.get_level_values("trade_date")})
        for name in ("bar_at", "calendar_status"):
            keys[name] = self._prices[name].array if name in self._prices else None
        positions, distinct_keys = pd.factorize(pd.MultiIndex.from_frame(keys), sort=False)
        availability = pd.array([_price_availability(*key) for key in distinct_keys],
                                dtype=f"datetime64[ns, {KST}]")
        self._prices["available_at"] = availability.take(positions)
        self._events = events.copy(deep=True).reset_index(drop=True)
        if not events.empty:
            if not {"event_id", "stock_code"}.issubset(events.columns):
                raise ValueError("events require event_id and stock_code")
            if events[["event_id", "stock_code"]].isna().any().any() or events.event_id.duplicated().any():
                raise ValueError("event keys must be unique and nonmissing")
        self._events["available_at"] = pd.Series(
            [_event_availability(row) for row in self._events.to_dict("records")],
            index=self._events.index, dtype=f"datetime64[ns, {KST}]",
        )

    def prices_as_of(self, as_of, lookback_sessions: int | None) -> pd.DataFrame:
        cutoff = _timestamp(as_of)
        _lookback(lookback_sessions, "lookback_sessions")
        days = self._prices.index.get_level_values("trade_date")
        usable = self._prices.loc[(self._prices.available_at <= cutoff)
                                  & (days <= cutoff.tz_localize(None).normalize())]
        if lookback_sessions is not None:
            sessions = usable.index.get_level_values("trade_date").unique()[-lookback_sessions:]
            usable = usable.loc[usable.index.get_level_values("trade_date").isin(sessions)]
        return usable.copy(deep=True)

    def events_as_of(self, as_of, lookback_days: int | None) -> pd.DataFrame:
        cutoff = _timestamp(as_of)
        _lookback(lookback_days, "lookback_days")
        mask = self._events.available_at <= cutoff
        if lookback_days is not None:
            mask &= self._events.available_at >= cutoff - pd.Timedelta(days=lookback_days)
        usable = self._events.loc[mask]
        if not usable.empty:
            usable = usable.sort_values(["available_at", "event_id"], kind="stable")
        return usable.copy(deep=True).reset_index(drop=True)
