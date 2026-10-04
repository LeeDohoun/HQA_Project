"""Sleeve D rule baseline, using corporate-action-aware ret_1d exclusively.

t is the latest usable supplied market session. Windows use shared session
dates, so missing stock rows never pull older returns into a window. Unverified
rows are masked only in the windows that use them (returns or 20-session ADV),
without compressing dates. An unverified current row cannot qualify; older
unverified history outside these windows does not exclude a stock. ADV requires
20 valid trading_value observations; missing values are not replaced by close
times volume. Monthly scheduling, theme/regime filtering and sizing are later
milestones; an unvalidated theme/regime filter is not applied here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import StrategySpec, _targets
from .data import PointInTimeData


def momentum_12_1(ret_1d: pd.Series | pd.DataFrame):
    """Compound inclusive t-252..t-21; require at least 200 finite returns."""
    window = ret_1d.iloc[-253:].iloc[:-21]
    window = window.where(np.isfinite(window))
    return (1 + window).prod(min_count=200) - 1


def low_volatility(ret_1d: pd.Series | pd.DataFrame):
    """Negative sample standard deviation over t-59..t, at least 40 days."""
    window = ret_1d.iloc[-60:].where(np.isfinite(ret_1d.iloc[-60:]))
    result = -window.std(ddof=1)
    if isinstance(window, pd.Series):
        return result if window.count() >= 40 else np.nan
    return result.where(window.count() >= 40)


def combined_score(factors: pd.DataFrame) -> pd.Series:
    """Average percentile ranks, tied values averaged, both factors required."""
    return factors.rank(method="average", pct=True).mean(axis=1, skipna=False)


class MomentumLowVolStrategy:
    spec = StrategySpec(
        strategy_id="d_momentum_lowvol", sleeve="D",
        thesis="12-1 momentum and low volatility provide a monthly rule baseline",
        counterparty="Investors paying a risk premium; this baseline is not a claim of alpha",
        universe="Latest usable session; close >= 1000 KRW; 20-session ADV >= configured minimum",
        inputs=("KRX ret_1d and trading_value, session close plus 30 minutes",),
        decision_time="16:30 Asia/Seoul, monthly", horizon_sessions=20, stage="candidate",
    )

    def __init__(self, min_trading_value_krw: float = 1e8):
        if not np.isfinite(min_trading_value_krw) or min_trading_value_krw < 0:
            raise ValueError("minimum trading value must be finite and nonnegative")
        self.min_trading_value_krw = min_trading_value_krw

    def score(self, as_of, data: PointInTimeData) -> pd.DataFrame:
        prices = data.prices_as_of(as_of, 253)
        if prices.empty:
            return pd.DataFrame({"stock_code": pd.Series(dtype=str), "score": pd.Series(dtype=float)})
        required = {"close", "trading_value", "ret_1d", "calendar_status"}
        if not required.issubset(prices.columns):
            raise ValueError(f"missing factor columns: {sorted(required - set(prices.columns))}")
        verified = prices.calendar_status.eq("verified")
        returns = prices.ret_1d.where(verified).unstack("stock_code")
        value = prices.trading_value.where(verified).unstack("stock_code")
        adv = value.iloc[-20:].mean().where(value.iloc[-20:].count() == 20)
        current = prices.xs(returns.index[-1], level="trade_date")
        eligible = current.index[(current.close >= 1000) & current.calendar_status.eq("verified")
                                 & (adv.reindex(current.index) >= self.min_trading_value_krw)]
        factors = pd.DataFrame({"momentum_12_1": momentum_12_1(returns),
                                "low_volatility": low_volatility(returns)}).reindex(eligible)
        # Rank only stocks with both measured features, not partial histories.
        factors = factors.loc[np.isfinite(factors).all(axis=1)]
        return combined_score(factors).rename("score").rename_axis("stock_code").reset_index().sort_values(
            "stock_code", kind="stable",
        ).reset_index(drop=True)

    def generate(self, as_of, data: PointInTimeData, top_n: int):
        return _targets(self.spec, as_of, self.score(as_of, data), top_n)
