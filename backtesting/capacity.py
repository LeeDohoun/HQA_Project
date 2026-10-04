"""Capacity estimates from point-in-time daily selections."""
from __future__ import annotations

import numpy as np
import pandas as pd


def strategy_capacity(selections: pd.DataFrame, *, participation=0.01, positions=None) -> dict:
    """Use p25 position capacity to limit reliance on unusually liquid names.

    Each selected stock-date has equal weight in the capacity distribution. The
    conservative lower quartile is multiplied by explicit positions, or by the
    median number of distinct names per date; it is an estimate, not an order cap.
    """
    required = {"trade_date", "stock_code", "avg_trading_value_20d"}
    if not required.issubset(selections.columns) or selections.empty:
        raise ValueError("selections require nonempty trade_date, stock_code, avg_trading_value_20d")
    if selections[list(required)].isna().any().any() or selections.duplicated(["trade_date", "stock_code"]).any():
        raise ValueError("selections must have unique stock-date keys and no missing inputs")
    if isinstance(participation, bool) or not np.isfinite(participation) or not 0 < participation <= 1:
        raise ValueError("participation must be in (0, 1]")
    adv = selections.avg_trading_value_20d.to_numpy(dtype=float)
    if not np.isfinite(adv).all() or (adv < 0).any():
        raise ValueError("avg_trading_value_20d must be finite and nonnegative")
    if positions is None:
        positions = float(selections.groupby("trade_date").stock_code.nunique().median())
    elif isinstance(positions, bool) or not np.isfinite(positions) or positions <= 0 or int(positions) != positions:
        raise ValueError("positions must be a positive integer")
    p25, median = np.quantile(participation * adv, [0.25, 0.5])
    return {"per_position_p25": float(p25), "per_position_median": float(median),
            "positions": float(positions), "strategy_capacity": float(positions * p25)}
