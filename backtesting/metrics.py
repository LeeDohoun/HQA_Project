"""Numerical metrics shared by historical and observed PAPER reports."""

import math

import numpy as np

# Trading days between successive rebalance dates. As in the rebalance date selection,
# anything other than daily or weekly means month-end dates, about 21 trading days apart.
REBALANCE_SPACING_DAYS = {"D": 1, "DAILY": 1, "W": 5, "WEEKLY": 5}
MONTHLY_SPACING_DAYS = 21


def sleeve_count(rebalance: str, hold_days: int) -> int:
    """Equal capital sleeves needed so that holdings opened at successive rebalances never
    share capital: a cohort held hold_days still runs during the next ceil(hold/spacing) - 1
    rebalances. One sleeve when holdings do not overlap (e.g. weekly with 5-day holds)."""
    return max(1, math.ceil(hold_days / REBALANCE_SPACING_DAYS.get(rebalance.upper(), MONTHLY_SPACING_DAYS)))


class SleevedEquity:
    """Strategy equity when holding periods overlap (as in overlapping-portfolio studies):
    capital is split into equal sleeves and rebalance i funds sleeve i % K, so each sleeve
    runs non-overlapping cohorts. With one sleeve this is plain compounding. A holiday can
    shorten an interval so that a sleeve's next cohort starts a day or a few before its
    previous one ends (about 6% of sleeve-days for weekly rebalancing with 20-day holds on
    2026 KRX data); that overlap is tolerated, as it always was for single-sleeve schedules."""

    def __init__(self, sleeves: int):
        if sleeves < 1:
            raise ValueError("at least one capital sleeve is required")
        self.values = [1.0] * sleeves

    def add(self, rebalance_index: int, net_return: float) -> float:
        self.values[rebalance_index % len(self.values)] *= 1.0 + net_return
        return self.equity

    @property
    def equity(self) -> float:
        return sum(self.values) / len(self.values)


def max_drawdown(equity_values: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity_values)
    drawdowns = equity_values / peaks - 1.0
    return float(drawdowns.min()) if len(drawdowns) else 0.0
