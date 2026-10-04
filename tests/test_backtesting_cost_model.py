from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import numpy as np
import pytest

from backtesting import cost_model
from backtesting.cost_model import CostConfig, one_way_cost, round_trip_cost, round_trip_cost_vectorized, tick_size


@pytest.mark.parametrize("price,expected", [
    (1, 1), (1999, 1), (2000, 5), (4999, 5), (5000, 10), (19999, 10),
    (20000, 50), (49999, 50), (50000, 100), (199999, 100),
    (200000, 500), (499999, 500), (500000, 1000), (1000000, 1000),
])
def test_tick_boundaries(price, expected):
    assert tick_size(price) == expected


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), "2000", None, True])
def test_invalid_price(price):
    with pytest.raises(ValueError):
        tick_size(price)


@pytest.mark.parametrize("value,slippage", [
    (0, 0.0030), (1e8 - 1, 0.0030), (1e8, 0.0015), (1e9 - 1, 0.0015),
    (1e9, 0.0005), (1e10 - 1, 0.0005), (1e10, 0.0002), (1e11, 0.0002),
])
def test_liquidity_boundaries(value, slippage):
    assert one_way_cost(10000, "KOSPI", "20260102", value, "buy") == pytest.approx(0.00015 + 0.001 + slippage)


@pytest.mark.parametrize("year,tax", [(2023, 0.0020), (2024, 0.0018), (2025, 0.0015), (2026, 0.0020)])
@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_sell_tax_by_year_and_market(year, tax, market):
    args = (10000, market, date(year, 7, 1), 1e10)
    buy = one_way_cost(*args, "buy")
    sell = one_way_cost(*args, "sell")
    assert buy == pytest.approx(0.00135)
    assert sell - buy == pytest.approx(tax)
    assert round_trip_cost(*args) == pytest.approx(2 * 0.00135 + tax)


@pytest.mark.parametrize("multiplier", [0.0, 1.0, 1.5, 2.0])
def test_config_and_multiplier_scale_only_uncertain_costs(multiplier):
    config = CostConfig(commission_rate=0.0004, slippage_rates=(0.004, 0.002, 0.001, 0.0003))
    assert one_way_cost(2000, "KOSDAQ", "20250601", 1e9, "buy", config, multiplier) == pytest.approx(
        (0.0004 + 5 / 2000 + 0.001) * multiplier)
    assert one_way_cost(2000, "KOSDAQ", "20250601", 1e9, "sell", config, multiplier) == pytest.approx(
        0.0015 + (0.0004 + 5 / 2000 + 0.001) * multiplier)
    assert round_trip_cost(2000, "KOSDAQ", "20250601", 1e9, config, multiplier) == pytest.approx(
        0.0015 + 2 * (0.0004 + 5 / 2000 + 0.001) * multiplier)


def test_config_is_frozen():
    with pytest.raises(FrozenInstanceError):
        CostConfig().commission_rate = 0


@pytest.mark.parametrize("kwargs", [
    {"commission_rate": -1}, {"commission_rate": float("nan")},
    {"slippage_rates": (0.1,)}, {"slippage_rates": [0.1] * 4},
    {"slippage_rates": (0.1, 0.2, -0.1, 0.0)}, {"slippage_rates": (0.1, 0.2, float("inf"), 0.0)},
])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        CostConfig(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"market": "NYSE"}, {"side": "hold"}, {"trade_date": "20270101"},
    {"trade_date": date(2022, 1, 1)}, {"trade_date": "20260230"}, {"trade_date": "2026-01-01"},
    {"trade_date": "202611"}, {"trade_date": None}, {"avg_trading_value_20d": -1},
    {"avg_trading_value_20d": float("nan")}, {"avg_trading_value_20d": float("inf")},
    {"multiplier": -1}, {"multiplier": float("inf")}, {"config": None},
])
def test_invalid_cost_input(kwargs):
    args = dict(price=10000, market="KOSPI", trade_date="20260101", avg_trading_value_20d=1e9, side="buy")
    with pytest.raises(ValueError):
        one_way_cost(**(args | kwargs))


@pytest.mark.parametrize("multiplier", [0.0, 1.0, 1.5, 2.0])
@pytest.mark.parametrize("config", [CostConfig(), CostConfig(commission_rate=0.0004,
                                                            slippage_rates=(0.004, 0.002, 0.001, 0.0003))])
def test_vectorized_matches_scalar_random_inputs_and_boundaries(config, multiplier):
    rng = np.random.default_rng(52)
    bounds = np.array([2000, 5000, 20000, 50000, 200000, 500000], dtype=float)
    prices = np.concatenate([rng.uniform(1, 1e6, 1000), bounds,
                             np.nextafter(bounds, 0), np.nextafter(bounds, np.inf)])
    adv = np.concatenate([rng.uniform(0, 2e10, 1000),
                          np.tile([0, 1e8 - 1, 1e8, 1e9 - 1, 1e9, 1e10], 3)])
    markets = rng.choice(["KOSPI", "KOSDAQ"], size=len(prices))
    years = rng.choice([2023, 2024, 2025, 2026], size=len(prices))
    expected = [round_trip_cost(float(price), market, date(int(year), 7, 1), float(value), config, multiplier)
                for price, market, year, value in zip(prices, markets, years, adv)]
    actual = round_trip_cost_vectorized(prices, markets, years, adv, config, multiplier)
    np.testing.assert_array_equal(actual, expected)


def test_vectorized_uses_market_tax_lookup_and_broadcasts(monkeypatch):
    monkeypatch.setitem(cost_model.SELL_TAX[2024], "KOSDAQ", 0.009)
    prices = np.array([[1999.0, 2000.0], [4999.0, 5000.0]])
    actual = round_trip_cost_vectorized(prices, ["KOSPI", "KOSDAQ"], 2024, 1e9)
    expected = [[round_trip_cost(float(price), market, date(2024, 7, 1), 1e9)
                 for price, market in zip(row, ["KOSPI", "KOSDAQ"])] for row in prices]
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("kwargs", [
    {"prices": [0]}, {"prices": [-1]}, {"prices": [np.nan]}, {"prices": [np.inf]},
    {"prices": [True]}, {"prices": ["10000"]}, {"markets": ["NYSE"]},
    {"years": [2022]}, {"years": [2027]}, {"years": [2024.5]}, {"years": [True]},
    {"adv": [-1]}, {"adv": [np.nan]}, {"adv": [np.inf]}, {"adv": [True]},
    {"multiplier": -1}, {"multiplier": np.inf}, {"multiplier": True}, {"config": None},
])
def test_invalid_vectorized_cost_inputs(kwargs):
    args = dict(prices=np.array([10000.0]), markets=["KOSPI"], years=[2024], adv=np.array([1e9]))
    with pytest.raises(ValueError):
        round_trip_cost_vectorized(**(args | kwargs))


def test_vectorized_unknown_year_raises_even_with_zero_multiplier():
    with pytest.raises(ValueError, match="unknown tax year"):
        round_trip_cost_vectorized(np.array([10000.0, 10000.0]), ["KOSPI", "KOSDAQ"],
                                   [2024, 2027], np.array([1e9, 1e9]), multiplier=0)


def test_vectorized_empty_arrays_and_mismatched_shapes():
    actual = round_trip_cost_vectorized(np.array([], dtype=float), [], np.array([], dtype=int),
                                       np.array([], dtype=float))
    assert actual.shape == (0,)
    with pytest.raises(ValueError):
        round_trip_cost_vectorized(np.array([10000.0, 10000.0]), ["KOSPI"] * 3, [2024], np.array([1e9]))
