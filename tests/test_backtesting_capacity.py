from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backtesting.capacity import strategy_capacity


def selections():
    return pd.DataFrame({"trade_date": ["2024-01-02"] * 2 + ["2024-01-03"] * 4,
                         "stock_code": ["A", "B", "A", "B", "C", "D"],
                         "avg_trading_value_20d": [1e8, 2e8, 3e8, 4e8, 5e8, 6e8]})


def test_capacity_arithmetic_and_explicit_positions():
    data = selections()
    result = strategy_capacity(data)
    assert result == {"per_position_p25": 2.25e6, "per_position_median": 3.5e6,
                      "positions": 3.0, "strategy_capacity": 6.75e6}
    assert strategy_capacity(data, positions=10, participation=0.02)["strategy_capacity"] == 45e6
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kwargs", [{"participation": 0}, {"participation": -1}, {"participation": 1.1},
                                   {"participation": np.nan}, {"positions": 0}, {"positions": 2.5}])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        strategy_capacity(selections(), **kwargs)


@pytest.mark.parametrize("value", [-1, np.nan, np.inf])
def test_invalid_adv(value):
    data = selections()
    data.loc[0, "avg_trading_value_20d"] = value
    with pytest.raises(ValueError):
        strategy_capacity(data)


def test_empty_or_duplicate_selections_rejected():
    data = selections()
    with pytest.raises(ValueError):
        strategy_capacity(data.iloc[:0])
    with pytest.raises(ValueError):
        strategy_capacity(pd.concat([data, data.iloc[:1]]))
