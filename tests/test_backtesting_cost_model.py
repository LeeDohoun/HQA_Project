from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
import itertools
import json
from pathlib import Path

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


@pytest.mark.parametrize("year,tax", [
    (2015, 0.0030), (2018, 0.0030), (2019, 0.0025), (2020, 0.0025),
    (2021, 0.0023), (2022, 0.0023), (2023, 0.0020), (2024, 0.0018),
    (2025, 0.0015), (2026, 0.0020),
])
@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_sell_tax_by_year_and_market(year, tax, market):
    args = (10000, market, date(year, 7, 1), 1e10)
    buy = one_way_cost(*args, "buy")
    sell = one_way_cost(*args, "sell")
    assert buy == pytest.approx(0.00135)
    assert sell - buy == pytest.approx(tax)
    assert round_trip_cost(*args) == pytest.approx(2 * 0.00135 + tax)


@pytest.mark.parametrize("trade_date,tax", [
    (date(2015, 1, 1), 0.0030),
    (date(2019, 6, 2), 0.0030), (date(2019, 6, 3), 0.0025),
    (date(2020, 12, 31), 0.0025), (date(2021, 1, 1), 0.0023),
    (date(2022, 12, 31), 0.0023), (date(2026, 12, 31), 0.0020),
])
@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
@pytest.mark.parametrize("as_string", [False, True])
def test_sell_tax_date_boundaries(trade_date, tax, market, as_string):
    if as_string:
        trade_date = trade_date.strftime("%Y%m%d")
    assert one_way_cost(10000, market, trade_date, 1e10, "sell", multiplier=0) == tax
    expected = round_trip_cost(10000, market, trade_date, 1e10)
    actual = round_trip_cost_vectorized([10000.0], [market], [trade_date], [1e10])
    np.testing.assert_array_equal(actual, [expected])


@pytest.mark.parametrize("trade_date", [date(2014, 12, 31), "20141231",
                                            date(2027, 1, 1), "20270101"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_dates_outside_tax_table_rejected(trade_date, side):
    with pytest.raises(ValueError, match="unknown tax year"):
        one_way_cost(10000, "KOSPI", trade_date, 1e10, side, multiplier=0)
    with pytest.raises(ValueError, match="unknown tax year"):
        round_trip_cost_vectorized([10000.0], ["KOSPI"], [trade_date], [1e10], multiplier=0)


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
    {"trade_date": date(2014, 12, 31)}, {"trade_date": "20260230"}, {"trade_date": "2026-01-01"},
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
@pytest.mark.parametrize("date_format", ["years", "date", "string", "numpy"])
def test_vectorized_matches_scalar_random_inputs_and_boundaries(config, multiplier, date_format):
    rng = np.random.default_rng(52)
    bounds = np.array([2000, 5000, 20000, 50000, 200000, 500000], dtype=float)
    prices = np.concatenate([rng.uniform(1, 1e6, 1000), bounds,
                             np.nextafter(bounds, 0), np.nextafter(bounds, np.inf)])
    adv = np.concatenate([rng.uniform(0, 2e10, 1000),
                          np.tile([0, 1e8 - 1, 1e8, 1e9 - 1, 1e9, 1e10], 3)])
    markets = rng.choice(["KOSPI", "KOSDAQ"], size=len(prices))
    dates = np.datetime64("2015-01-01") + rng.integers(
        0, (date(2027, 1, 1) - date(2015, 1, 1)).days, size=len(prices)).astype("timedelta64[D]")
    dates[:6] = np.array(["2015-01-01", "2019-06-02", "2019-06-03",
                          "2020-12-31", "2021-01-01", "2026-12-31"], dtype="datetime64[D]")
    if date_format == "years":
        inputs = dates.astype("datetime64[Y]").astype(int) + 1970
        dates = dates.astype("datetime64[Y]").astype("datetime64[D]")
    elif date_format == "date":
        inputs = dates.astype(object)
    elif date_format == "string":
        inputs = [day.strftime("%Y%m%d") for day in dates.astype(object)]
    else:
        inputs = dates
    expected = [round_trip_cost(float(price), market, day, float(value), config, multiplier)
                for price, market, day, value in zip(prices, markets, dates.astype(object), adv)]
    actual = round_trip_cost_vectorized(prices, markets, inputs, adv, config, multiplier)
    np.testing.assert_array_equal(actual, expected)


def test_vectorized_uses_market_tax_lookup_and_broadcasts(monkeypatch):
    rates = next(rates for start, rates in cost_model.SELL_TAX if start == date(2024, 1, 1))
    monkeypatch.setitem(rates, "KOSDAQ", 0.009)
    prices = np.array([[1999.0, 2000.0], [4999.0, 5000.0]])
    actual = round_trip_cost_vectorized(prices, ["KOSPI", "KOSDAQ"], 2024, 1e9)
    expected = [[round_trip_cost(float(price), market, date(2024, 7, 1), 1e9)
                 for price, market in zip(row, ["KOSPI", "KOSDAQ"])] for row in prices]
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("kwargs", [
    {"prices": [0]}, {"prices": [-1]}, {"prices": [np.nan]}, {"prices": [np.inf]},
    {"prices": [True]}, {"prices": ["10000"]}, {"markets": ["NYSE"]},
    {"years": [2014]}, {"years": [2027]}, {"years": [2024.5]}, {"years": [True]},
    {"years": ["20260230"]}, {"years": ["2026-01-01"]}, {"years": [None]},
    {"years": np.array(["NaT"], dtype="datetime64[D]")},
    {"years": np.array(["2014-12-31"], dtype="datetime64[D]")},
    {"years": np.array(["2027-01-01"], dtype="datetime64[D]")},
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


def test_v1_exact_golden_grid_captured_before_v2():
    golden = json.loads((Path(__file__).parent / "fixtures/cost_model_v1_golden.json").read_text())
    inputs = list(itertools.product(*golden["grid"].values()))
    for (price, market, day, adv, multiplier), expected in zip(inputs, golden["expected"], strict=True):
        for version in (None, "v1"):
            actual = [one_way_cost(price, market, day, adv, side, multiplier=multiplier,
                                   model_version=version).hex() for side in ("buy", "sell")]
            actual.append(round_trip_cost(price, market, day, adv, multiplier=multiplier,
                                          model_version=version).hex())
            assert actual == expected
    for multiplier in golden["grid"]["multipliers"]:
        selected = [(args, expected) for args, expected in zip(inputs, golden["expected"])
                    if args[-1] == multiplier]
        prices, markets, days, adv, _ = zip(*(args for args, _ in selected))
        actual = round_trip_cost_vectorized(prices, markets, days, adv, multiplier=multiplier)
        assert [value.hex() for value in actual] == [expected[2] for _, expected in selected]


@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
@pytest.mark.parametrize("price,legacy_kospi,legacy_kosdaq,unified", [
    (999, 1, 1, 1), (1000, 5, 5, 1), (1999, 5, 5, 1), (2000, 5, 5, 5),
    (4999, 5, 5, 5), (5000, 10, 10, 10), (9999, 10, 10, 10),
    (10000, 50, 50, 10), (19999, 50, 50, 10), (20000, 50, 50, 50),
    (49999, 50, 50, 50), (50000, 100, 100, 100), (99999, 100, 100, 100),
    (100000, 500, 100, 100), (199999, 500, 100, 100), (200000, 500, 100, 500),
    (499999, 500, 100, 500), (500000, 1000, 100, 1000), (1000000, 1000, 100, 1000),
])
def test_v2_dated_market_tick_tables(market, price, legacy_kospi, legacy_kosdaq, unified):
    expected = legacy_kospi if market == "KOSPI" else legacy_kosdaq
    assert tick_size(price, market, "20230124", model_version="v2") == expected
    assert tick_size(price, market, "20230125", model_version="v2") == unified
    assert tick_size(price, market, "20230124") == unified


@pytest.mark.parametrize("day,settlement,tax", [
    ("20190529", "2019-05-31", 0.0030), ("20190530", "2019-06-03", 0.0025),
    ("20190531", "2019-06-04", 0.0025), ("20190603", "2019-06-05", 0.0025),
    ("20241227", "2025-01-02", 0.0015), ("20251229", "2026-01-02", 0.0020),
])
@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_v2_settlement_tax_uses_two_krx_sessions(day, settlement, tax, market):
    from src.runner.trading_calendar import _calendar

    parsed = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
    sessions = _calendar(parsed.year).sessions_in_range(parsed, settlement)
    assert len(sessions) == 3 and sessions[2].date().isoformat() == settlement
    assert cost_model._settlement_date(parsed).isoformat() == settlement
    assert one_way_cost(12000, market, day, 1e9, "sell", multiplier=0, model_version="v2") == tax
    assert one_way_cost(12000, market, day, 1e9, "buy", multiplier=0, model_version="v2") == 0


@pytest.mark.parametrize("multiplier", [0.0, 1.0, 1.5, 2.0])
@pytest.mark.parametrize("date_format", ["date", "string", "numpy"])
def test_v2_vectorized_scalar_exact_with_broadcasts(multiplier, date_format):
    dates = np.array(["2019-05-30", "2020-06-01", "2023-01-25", "2024-12-27", "2025-12-29"],
                     dtype="datetime64[D]")
    inputs = dates.astype(object) if date_format == "date" else dates
    if date_format == "string":
        inputs = [day.strftime("%Y%m%d") for day in dates.astype(object)]
    prices = np.array([[999, 12000, 150000, 500000, 1000000], [1000, 10000, 100000, 200000, 500000]])
    markets = np.array([["KOSPI"], ["KOSDAQ"]])
    config = CostConfig(commission_rate=0.0004, slippage_rates=(0.004, 0.002, 0.001, 0.0003), model_version="v2")
    expected = [[round_trip_cost(float(price), market, day, 1e9, config, multiplier)
                 for price, day in zip(row, dates.astype(object))] for row, [market] in zip(prices, markets)]
    actual = round_trip_cost_vectorized(prices, markets, inputs, 1e9, config, multiplier)
    np.testing.assert_array_equal(actual, expected)
    explicit = round_trip_cost_vectorized(prices, markets, inputs, 1e9,
        CostConfig(commission_rate=0.0004, slippage_rates=(0.004, 0.002, 0.001, 0.0003)), multiplier, model_version="v2")
    np.testing.assert_array_equal(actual, explicit)
    scalar_array = round_trip_cost_vectorized(12000.0, "KOSDAQ", "20190530", 1e9, config, multiplier)
    assert scalar_array == round_trip_cost(12000.0, "KOSDAQ", "20190530", 1e9, config, multiplier)


def test_v2_requires_market_and_full_session_date_and_known_tax_year():
    with pytest.raises(ValueError, match="model_version"):
        CostConfig(model_version="v3")
    with pytest.raises(ValueError, match="model_version"):
        tick_size(12000, model_version="v3")
    for market in (None, "NYSE"):
        with pytest.raises(ValueError, match="market"):
            tick_size(12000, market, "20240102", model_version="v2")
        with pytest.raises(ValueError, match="market"):
            round_trip_cost(12000, market, "20240102", 1e9, model_version="v2")
        with pytest.raises(ValueError, match="market"):
            round_trip_cost_vectorized([12000], market, ["20240102"], 1e9, model_version="v2")
    with pytest.raises(ValueError, match="trade_date"):
        tick_size(12000, "KOSDAQ", model_version="v2")
    assert tick_size(12000, "ignored", "ignored", model_version="v1") == 10
    for day in ("20250101", "20241228"):
        with pytest.raises(ValueError, match="KRX session"):
            round_trip_cost(12000, "KOSDAQ", day, 1e9, model_version="v2")
        with pytest.raises(ValueError, match="KRX session"):
            round_trip_cost_vectorized([12000], "KOSDAQ", [day], 1e9, model_version="v2")
    with pytest.raises(ValueError, match="full trade dates"):
        round_trip_cost_vectorized([12000], "KOSDAQ", [2024], 1e9, model_version="v2")
    for call in (round_trip_cost, round_trip_cost_vectorized):
        with pytest.raises(ValueError, match="unknown tax year"):
            call(12000, "KOSDAQ", "20261229", 1e9, model_version="v2")


def test_version_override_and_v2_empty_arrays():
    assert round_trip_cost(12000, "KOSDAQ", "20190530", 1e9, CostConfig(model_version="v2"),
                           model_version="v1") == round_trip_cost(12000, "KOSDAQ", "20190530", 1e9)
    actual = round_trip_cost_vectorized(np.array([], dtype=float), [], np.array([], dtype="datetime64[D]"),
                                       np.array([], dtype=float), model_version="v2")
    assert actual.shape == (0,)
