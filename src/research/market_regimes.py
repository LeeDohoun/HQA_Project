"""Dated market rules for reporting splits only; never strategy or verdict inputs."""
from __future__ import annotations

from datetime import date

import pandas as pd


# Source notes: FSC emergency short-sale measures effective 2020-03-16;
# FSC partial resumption effective 2021-05-03 (KOSPI200/KOSDAQ150 only);
# FSC full ban effective 2023-11-06 and full resumption effective 2025-03-31.
# Before these documented intervals no short-sale status is inferred.
# Intervals are [start, end), so the documented inclusive ends remain exact.
SHORT_SALE_REGIMES = (
    (None, "2020-03-16", "before_documented_intervals"),
    ("2020-03-16", "2021-05-03", "full_ban_2020"),
    ("2021-05-03", "2023-11-06", "partial_kospi200_kosdaq150"),
    ("2023-11-06", "2025-03-31", "full_ban_2023"),
    ("2025-03-31", None, "full_resumption"),
)
# FSC/NXT launch notice: 2025-03-04. KRX after-hours continuous-market
# commencement notice: 2026-09-14. These flags describe availability only.
# KRX price-limit expansion notice: +/-15% to +/-30% on 2015-06-15.
# KRX quotation-unit amendment: unified stock ticks effective 2023-01-25.
REGIME_INTERVALS = {
    "short_sale": SHORT_SALE_REGIMES,
    "nxt": ((None, "2025-03-04", "pre_launch"), ("2025-03-04", None, "launched")),
    "krx_after_hours_continuous": ((None, "2026-09-14", "pre_launch"),
                                    ("2026-09-14", None, "launched")),
    "price_limit": ((None, "2015-06-15", "15_percent"), ("2015-06-15", None, "30_percent")),
    "tick": ((None, "2023-01-25", "market_specific"), ("2023-01-25", None, "unified")),
}


def regime_label(day: str | date) -> dict[str, str]:
    """Return each documented regime dimension for a date-only observation."""
    day = pd.Timestamp(day)
    if pd.isna(day) or day.tz is not None or day != day.normalize():
        raise ValueError("regime date must be date-only and timezone-naive")
    return {name: next(label for start, end, label in intervals
                       if (start is None or day >= pd.Timestamp(start))
                       and (end is None or day < pd.Timestamp(end)))
            for name, intervals in REGIME_INTERVALS.items()}


def split_by_regime(series: pd.Series, regime: str = "short_sale") -> dict[str, pd.Series]:
    """Partition a dated series, preserving values, date order and duplicate dates."""
    if regime not in REGIME_INTERVALS:
        raise ValueError(f"unknown regime: {regime}")
    labels = [regime_label(day)[regime] for day in series.index]
    return {label: series.iloc[[i for i, value in enumerate(labels) if value == label]]
            for label in dict.fromkeys(labels)}
