from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.portfolio.position_sizing import atr_stop, size_positions, time_stop


def prices(vols=(0.01, 0.02, 0.04), *, days=25, close=10_000):
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = [(day, chr(65 + i), close, (-1 if j % 2 else 1) * vol, 1e10)
            for j, day in enumerate(dates) for i, vol in enumerate(vols)]
    return pd.DataFrame(rows, columns=["trade_date", "stock_code", "close", "ret_1d", "trading_value"])


def size(targets, data, capital=1_000_000, **kwargs):
    return size_positions(targets, data, capital, **({"max_weight": 1, "max_theme_weight": 1,
                                                   "min_order_krw": 0} | kwargs))


def test_inverse_volatility_uses_trailing_returns_not_scores_or_raw_close_moves():
    data = prices()
    data.loc[data.trade_date < data.trade_date.unique()[5], "ret_1d"] = 99
    orders, cash = size([("A", 1), ("B", 100), ("C", -2)], data, 700_000)
    amounts = orders.set_index("stock_code").notional
    assert amounts.to_dict() == {"B": 200_000, "A": 400_000, "C": 100_000}
    assert cash == 0
    assert orders.set_index("stock_code").weight.to_dict() == pytest.approx({"A": 4 / 7, "B": 2 / 7, "C": 1 / 7})


def test_stock_cap_redistributes_in_inverse_volatility_proportions():
    orders, cash = size([("A", 3), ("B", 2), ("C", 1)], prices(close=1_000), 600_000, max_weight=0.5)
    assert orders.notional.tolist() == [300_000, 200_000, 100_000]
    assert orders.binding_constraint.tolist() == ["max_weight", "none", "none"]
    assert cash == 0


def test_redistribution_can_reach_another_stock_cap():
    data = prices((1 / 7, 1 / 2, 1), close=1_000)
    orders, cash = size([("A", 3), ("B", 2), ("C", 1)], data, max_weight=0.4)
    expected = np.array([400_000, 400_000, 200_000])
    assert (orders.notional.to_numpy() <= expected).all()
    assert (expected - orders.notional.to_numpy() <= 1_000).all()
    assert orders.binding_constraint.iloc[1].startswith("max_weight")
    assert orders.weight.max() <= 0.4
    assert cash <= 2_000


def test_default_caps_leave_cash_when_too_few_names():
    orders, cash = size_positions([("A", 3), ("B", 2), ("C", 1)], prices(), 1_000_000)
    assert orders.notional.tolist() == [100_000] * 3
    assert cash == 700_000


def test_theme_cap_redistributes_to_other_themes_and_unclassified_names():
    targets = [("A", 4, "tech"), ("B", 3, "tech"), ("C", 2, "other"), ("D", 1)]
    orders, cash = size(targets, prices((0.01, 0.02, 0.02, 0.02), close=1_000), max_theme_weight=0.3)
    expected = np.array([200_000, 100_000, 300_000, 400_000])
    assert (orders.notional.to_numpy() <= expected).all()
    assert (expected - orders.notional.to_numpy() <= 1_000).all()
    assert orders.loc[orders.theme == "tech", "weight"].sum() <= 0.3
    assert "max_theme_weight" in orders.binding_constraint.iloc[0]
    assert cash <= 2_000


def test_stock_and_theme_caps_can_make_full_investment_impossible():
    targets = [("A", 3, "tech"), ("B", 2, "tech"), ("C", 1, "other")]
    orders, cash = size(targets, prices((1 / 3, 1, 1), close=1_000), max_weight=0.4, max_theme_weight=0.5)
    assert orders.weight.tolist() == pytest.approx([0.375, 0.125, 0.4], abs=0.001)
    assert orders.groupby("theme").weight.sum().max() <= 0.5
    assert cash == pytest.approx(100_000, abs=2_000)


def test_missing_themes_are_not_a_shared_theme():
    orders, cash = size([(chr(65 + i), 1) for i in range(5)], prices((0.01,) * 5), max_theme_weight=0.3)
    assert len(orders) == 5
    assert cash == 0
    assert orders.weight.tolist() == pytest.approx([0.2] * 5)


def test_adv_is_twenty_session_mean_and_excess_stays_in_cash():
    data = prices((0.01, 0.01))
    dates = data.trade_date.unique()
    data.loc[(data.stock_code == "A") & (data.trade_date < dates[5]), "trading_value"] = 1e15
    data.loc[(data.stock_code == "A") & (data.trade_date >= dates[5]), "trading_value"] = [1e7, 3e7] * 10
    orders, cash = size([("A", 1), ("B", 1)], data)
    assert orders.notional.tolist() == [200_000, 500_000]
    assert orders.binding_constraint.iloc[0] == "adv_cap"
    assert cash == 300_000


def test_share_flooring_and_reported_weight_use_executable_notional():
    orders, cash = size([("A", 1), ("B", 1)], prices((0.01, 0.01), close=30_000))
    assert orders.shares.tolist() == [16, 16]
    assert orders.notional.tolist() == [480_000, 480_000]
    assert orders.weight.tolist() == [0.48, 0.48]
    assert orders.binding_constraint.tolist() == ["share_rounding", "share_rounding"]
    assert cash == 40_000


def test_minimum_orders_are_dropped_without_redistribution():
    data = prices((0.01, 0.03))
    orders, cash = size([("A", 2), ("B", 1)], data, 300_000, min_order_krw=100_000)
    assert orders.stock_code.tolist() == ["A"]
    assert orders.notional.tolist() == [220_000]
    assert cash == 80_000


def test_minimum_order_equality_is_allowed_and_zero_adv_drops_order():
    data = prices((0.01,))
    orders, cash = size([("A", 1)], data, 100_000, min_order_krw=100_000)
    assert orders.shares.tolist() == [10]
    assert cash == 0
    data["trading_value"] = 0
    orders, cash = size([("A", 1)], data, 100_000)
    assert orders.empty
    assert cash == 100_000


def test_selection_ties_input_order_multiindex_and_purity():
    data = prices()
    before = data.copy(deep=True)
    targets = [("C", 1), ("A", 1), ("B", 2)]
    orders, cash = size(targets, data, max_positions=2)
    other, other_cash = size(list(reversed(targets)), data.sample(frac=1, random_state=7).set_index(["trade_date", "stock_code"]),
                             max_positions=2)
    assert orders.stock_code.tolist() == ["B", "A"]
    pd.testing.assert_frame_equal(orders, other)
    pd.testing.assert_frame_equal(data, before)
    assert cash == other_cash
    assert targets == [("C", 1), ("A", 1), ("B", 2)]


@pytest.mark.parametrize("targets,capital", [([], 1_000_000), ([("A", 1)], 0)])
def test_no_orders_need_no_price_data(targets, capital):
    orders, cash = size_positions(targets, pd.DataFrame(), capital)
    assert orders.empty
    assert cash == capital


@pytest.mark.parametrize("change", ["short", "missing_session", "stale", "zero_vol", "nan_return",
                                    "nan_adv", "negative_adv", "zero_close", "duplicate"])
def test_invalid_price_history_fails_without_defaults(change):
    data = prices((0.01, 0.02))
    if change == "short":
        data = data.loc[data.trade_date < data.trade_date.unique()[19]]
    elif change in ("missing_session", "stale"):
        day = data.trade_date.unique()[-1 if change == "stale" else -4]
        data = data.loc[~((data.stock_code == "A") & (data.trade_date == day))]
    elif change == "zero_vol":
        data.loc[data.stock_code == "A", "ret_1d"] = 0
    elif change == "duplicate":
        data = pd.concat([data, data.iloc[-1:]])
    else:
        field, value = {"nan_return": ("ret_1d", np.nan), "nan_adv": ("trading_value", np.nan),
                        "negative_adv": ("trading_value", -1), "zero_close": ("close", 0)}[change]
        data.loc[data.index[-2], field] = value
    with pytest.raises(ValueError):
        size([("A", 1)], data)


@pytest.mark.parametrize("kwargs", [{"vol_lookback": 1}, {"max_positions": 0}, {"max_weight": 0},
                                   {"max_theme_weight": 1.1}, {"adv_participation": np.nan},
                                   {"min_order_krw": -1}, {"capital_krw": True}])
def test_invalid_sizing_parameters(kwargs):
    with pytest.raises(ValueError):
        size_positions([("A", 1)], prices(), **({"capital_krw": 1_000_000} | kwargs))


@pytest.mark.parametrize("targets", [[("A", 1), ("A", 2)], [("A", np.nan)], [("A", 1, "")]])
def test_invalid_targets(targets):
    with pytest.raises(ValueError):
        size(targets, prices())


def atr_prices():
    return pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05", "2024-01-08"]),
                         "stock_code": "A", "high": [110, 125, 115, 1_000],
                         "low": [90, 120, 100, 1], "close": [100, 122, 110, 1_000]})


def test_atr_includes_gaps_and_ignores_future_sessions():
    data = atr_prices()
    before = data.copy(deep=True)
    assert atr_stop(data, "A", "2024-01-06", lookback=2) == 63  # TR = 25, 22.
    assert atr_stop(data.iloc[:3].set_index(["trade_date", "stock_code"]), "A", "2024-01-05",
                    multiple=1, lookback=2) == 86.5
    pd.testing.assert_frame_equal(data, before)


@pytest.mark.parametrize("change", ["short", "missing_stock", "nan", "inconsistent", "nonpositive_stop"])
def test_invalid_atr_inputs(change):
    data = atr_prices().iloc[:3].copy()
    code = "B" if change == "missing_stock" else "A"
    multiple = 10 if change == "nonpositive_stop" else 2
    if change == "short":
        data = data.iloc[:2]
    elif change == "nan":
        data.loc[1, "close"] = np.nan
    elif change == "inconsistent":
        data.loc[1, "high"] = 119
    with pytest.raises(ValueError):
        atr_stop(data, code, "2024-01-05", multiple=multiple, lookback=2)


def test_time_stop_counts_entry_and_only_supplied_exchange_sessions():
    sessions = ["2024-01-02", "2024-01-03", "2024-01-08"]
    assert not time_stop("2024-01-03", sessions, 3)
    assert time_stop("2024-01-03", sessions, 2)
    assert time_stop("2024-01-08", sessions, 1)


@pytest.mark.parametrize("sessions,limit", [(["2024-01-03", "2024-01-02"], 2),
                                          (["2024-01-02", "2024-01-02"], 2),
                                          (["2024-01-03"], 2), (["2024-01-02"], 0)])
def test_invalid_time_stop_calendar(sessions, limit):
    with pytest.raises(ValueError):
        time_stop("2024-01-02", sessions, limit)
