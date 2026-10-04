import numpy as np
import pandas as pd
import pytest

from src.strategies import PointInTimeData
from src.strategies.factors import MomentumLowVolStrategy, combined_score, low_volatility, momentum_12_1


def market(days=300):
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for offset, day in enumerate(dates):
        for code, value in (("000001", 0.002), ("000002", 0.004 + (-1) ** offset * 0.003),
                            ("000003", -0.001)):
            rows.append({"trade_date": day, "stock_code": code, "close": 5000,
                         "ret_1d": value, "trading_value": 2e8, "calendar_status": "verified",
                         "bar_at": day.strftime("%Y-%m-%d") + "T15:30:00+09:00"})
    return pd.DataFrame(rows).set_index(["trade_date", "stock_code"]), dates[-1].strftime("%Y-%m-%d") + "T17:00:00+09:00"


def test_momentum_inclusive_boundaries_and_recent_skip():
    returns = pd.Series(np.zeros(280))
    returns.iloc[-254] = 10  # Before t-252.
    returns.iloc[-253] = 0.1
    returns.iloc[-22] = 0.2  # t-21, included.
    returns.iloc[-21:] = 10
    assert momentum_12_1(returns) == pytest.approx(0.32)


@pytest.mark.parametrize("valid,expected", [(199, np.nan), (200, 1.01 ** 200 - 1)])
def test_momentum_minimum_valid_days(valid, expected):
    returns = pd.Series(np.nan, index=range(253))
    returns.iloc[:valid] = 0.01
    result = momentum_12_1(returns)
    assert pd.isna(result) if pd.isna(expected) else result == pytest.approx(expected)


@pytest.mark.parametrize("valid", [39, 40])
def test_low_volatility_minimum_and_sample_std(valid):
    returns = pd.Series(np.nan, index=range(80))
    returns.iloc[-valid:] = np.linspace(-0.03, 0.03, valid)
    returns.iloc[0] = 100  # Outside the 60-session window.
    result = low_volatility(returns)
    assert pd.isna(result) if valid < 40 else result == pytest.approx(-np.linspace(-0.03, 0.03, valid).std(ddof=1))


def test_combined_score_averages_tie_ranks_and_requires_both():
    factors = pd.DataFrame({"momentum": [2, 2, 1, np.nan], "lowvol": [3, 1, 2, np.nan]})
    scores = combined_score(factors)
    assert scores.iloc[:3].tolist() == pytest.approx([(2.5 / 3 + 1) / 2, (2.5 / 3 + 1 / 3) / 2,
                                                       (1 / 3 + 2 / 3) / 2])
    assert pd.isna(scores.iloc[3])


def test_raw_close_corporate_action_jump_does_not_change_factor_scores():
    prices, cutoff = market()
    changed = prices.copy()
    early = changed.index.get_level_values("trade_date") < pd.Timestamp(cutoff).tz_localize(None).normalize()
    changed.loc[early, "close"] *= 100  # Raw close split/discontinuity, ret_1d unchanged.
    strategy = MomentumLowVolStrategy()
    a = strategy.score(cutoff, PointInTimeData(prices, pd.DataFrame()))
    b = strategy.score(cutoff, PointInTimeData(changed, pd.DataFrame()))
    pd.testing.assert_frame_equal(a, b)
    assert not a.empty


def test_universe_current_row_price_and_liquidity_thresholds():
    prices, cutoff = market()
    day = prices.index.get_level_values("trade_date").max()
    prices.loc[(day, "000001"), "close"] = 999
    prices.loc[(slice(None), "000002"), "trading_value"] = 0.9e8
    prices = prices.drop((day, "000003"))
    assert MomentumLowVolStrategy().score(cutoff, PointInTimeData(prices, pd.DataFrame())).empty
    relaxed = MomentumLowVolStrategy(min_trading_value_krw=0.8e8).score(cutoff, PointInTimeData(prices, pd.DataFrame()))
    assert relaxed.stock_code.tolist() == ["000002"]


def test_missing_stock_session_stays_a_gap_instead_of_shifting_window():
    prices, cutoff = market(days=253)
    dates = prices.index.get_level_values("trade_date").unique()
    prices.loc[(slice(None), "000001"), "ret_1d"] = 0
    prices.loc[(dates[0], "000001"), "ret_1d"] = 0.5
    prices = prices.drop((dates[-22], "000001"))
    data = PointInTimeData(prices, pd.DataFrame())
    wide = data.prices_as_of(cutoff, 253).ret_1d.unstack("stock_code")
    assert momentum_12_1(wide)["000001"] == pytest.approx(0.5)
    assert np.isfinite(MomentumLowVolStrategy().score(cutoff, data).score).all()


def test_unverified_observation_masks_without_changing_session_positions():
    prices, cutoff = market()
    dates = prices.index.get_level_values("trade_date").unique()
    prices.loc[(dates[-30], "000001"), "ret_1d"] = 999
    prices.loc[(dates[-30], "000001"), "calendar_status"] = "unverified_special_session"
    equivalent = prices.copy()
    equivalent.loc[(dates[-30], "000001"), "ret_1d"] = np.nan
    equivalent.loc[(dates[-30], "000001"), "calendar_status"] = "verified"
    strategy = MomentumLowVolStrategy()
    pd.testing.assert_frame_equal(strategy.score(cutoff, PointInTimeData(prices, pd.DataFrame())),
                                  strategy.score(cutoff, PointInTimeData(equivalent, pd.DataFrame())))


def test_unverified_outside_windows_does_not_exclude_but_current_row_does():
    prices, cutoff = market()
    dates = prices.index.get_level_values("trade_date").unique()
    prices.loc[(dates[0], "000001"), "calendar_status"] = "unverified_special_session"
    prices.loc[(dates[-1], "000002"), "calendar_status"] = "unverified_special_session"
    scores = MomentumLowVolStrategy().score(cutoff, PointInTimeData(prices, pd.DataFrame()))
    assert scores.stock_code.tolist() == ["000001", "000003"]


def test_incomplete_adv_and_missing_factor_are_not_filled():
    prices, cutoff = market()
    dates = prices.index.get_level_values("trade_date").unique()
    prices.loc[(dates[-1], "000001"), "trading_value"] = np.nan
    prices.loc[(slice(None), "000002"), "ret_1d"] = np.nan
    prices.loc[(slice(None), "000003"), "calendar_status"] = "unverified_special_session"
    assert MomentumLowVolStrategy().score(cutoff, PointInTimeData(prices, pd.DataFrame())).empty


def test_future_shocks_never_change_current_scores_or_targets():
    prices, cutoff = market()
    future = prices.iloc[-3:].copy()
    future_day = prices.index.get_level_values("trade_date").max() + pd.Timedelta(days=4)
    future.index = pd.MultiIndex.from_product([[future_day], future.index.get_level_values("stock_code")],
                                             names=prices.index.names)
    future.loc[:, "bar_at"] = future_day.strftime("%Y-%m-%d") + "T15:30:00+09:00"
    future.loc[:, "ret_1d"] = [100, -0.99, 50]
    strategy = MomentumLowVolStrategy()
    before = PointInTimeData(prices, pd.DataFrame())
    with_future = PointInTimeData(pd.concat([future, prices.iloc[::-1]]), pd.DataFrame())
    pd.testing.assert_frame_equal(strategy.score(cutoff, before), strategy.score(cutoff, with_future))
    assert strategy.generate(cutoff, before, 2) == strategy.generate(cutoff, with_future, 2)


def test_short_history_is_not_a_factor_signal():
    prices, cutoff = market(days=210)
    assert MomentumLowVolStrategy().score(cutoff, PointInTimeData(prices, pd.DataFrame())).empty
