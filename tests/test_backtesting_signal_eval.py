from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, holdout
from backtesting.signal_eval import avg_trading_value_20d, evaluate_signal, forward_returns


def sample_prices(days=100, stocks=40, start="2024-01-02", strength=0.7, seed=12):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=days)
    codes = [f"{number:06d}" for number in range(stocks)]
    score = rng.normal(size=(days, stocks))
    returns = rng.normal(scale=0.02, size=(days, stocks))
    returns[1:] += strength * 0.03 * score[:-1]
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000.0, "high": 15000.0, "low": 5000.0,
                           "close": (10000 * (1 + returns)).ravel(), "volume": 100000,
                           "market": np.tile(["KOSPI", "KOSDAQ"], len(index) // 2),
                           "trading_value": np.tile(np.resize([5e7, 1e8, 1e9, 1e10], stocks), days)}, index=index)
    scores = pd.DataFrame({"trade_date": np.repeat(dates[:-1], stocks), "stock_code": codes * (days - 1),
                           "score": score[:-1].ravel()})
    return prices, scores


def test_planted_signal_and_bucket_partition():
    prices, scores = sample_prices()
    result = evaluate_signal(scores, prices, horizons=(1,), n_controls=100)["horizons"]["1"]
    assert result["ic"]["t_stat"] > 3
    assert result["ic"]["mean"] > result["random_control"]["p95"]
    assert result["random_control"]["share_of_controls"] == 0
    assert result["ic"]["count"] == 99
    buckets = result["liquidity_buckets"]
    assert sum(bucket["observations"] for bucket in buckets.values()) == len(scores)
    assert [bucket["observations"] for bucket in buckets.values()] == [990] * 4
    assert buckets["0"]["upper_exclusive"] == 1e8
    assert buckets["1"]["lower_inclusive"] == 1e8
    assert buckets["3"]["upper_exclusive"] is None
    assert all(bucket["ic"]["t_stat"] > 3 for bucket in buckets.values())
    json.dumps(result, allow_nan=False)


def test_noise_control_is_not_extreme_and_reproducible():
    prices, scores = sample_prices(strength=0, seed=0)
    kwargs = dict(horizons=(1,), n_controls=100, seed=91)
    result = evaluate_signal(scores, prices, **kwargs)
    assert 0.05 < result["horizons"]["1"]["random_control"]["share_of_controls"] < 0.95
    assert result == evaluate_signal(scores, prices, **kwargs)


def test_forward_returns_next_open_and_exact_session_not_next_stock_row():
    prices, _ = sample_prices(days=8, stocks=4)
    dates = prices.index.get_level_values("trade_date").unique()
    prices.loc[(dates[0], "000000"), "close"] = 100.0
    prices.loc[(dates[1], "000000"), "open"] = 200.0
    prices.loc[(dates[1], "000000"), "close"] = 240.0
    prices.loc[(dates[5], "000000"), "close"] = 300.0
    # These arbitrary timing-test prices include a 100% overnight jump.
    result = forward_returns(prices, horizons=(1, 5), limit_fill="assume_fill")
    assert result.loc[(dates[0], "000000"), 1] == pytest.approx(0.2)
    assert result.loc[(dates[0], "000000"), 5] == pytest.approx(0.5)
    missing = prices.drop((dates[1], "000000"))
    assert pd.isna(forward_returns(missing, (1,), limit_fill="assume_fill").loc[(dates[0], "000000"), 1])
    missing_exit = prices.drop((dates[5], "000000"))
    stale = forward_returns(missing_exit, (5,), limit_fill="assume_fill")
    assert stale.loc[(dates[0], "000000"), 5] == pytest.approx(prices.loc[(dates[4], "000000"), "close"] / 200 - 1)
    dropped = forward_returns(missing_exit, (5,), missing_exit="drop", limit_fill="assume_fill")
    assert pd.isna(dropped.loc[(dates[0], "000000"), 5])


def test_adv_is_trailing_and_uses_explicit_value_or_fallback():
    prices, _ = sample_prices(days=25, stocks=4)
    dates = prices.index.get_level_values("trade_date").unique()
    prices["trading_value"] = np.repeat(np.arange(1, 26, dtype=float), 4)
    actual = avg_trading_value_20d(prices)
    assert actual.loc[(dates[0], "000000")] == 1
    assert actual.loc[(dates[20], "000000")] == pytest.approx(np.arange(2, 22).mean())
    changed = prices.copy()
    changed.loc[(dates[21], "000000"), "trading_value"] = 1e15
    pd.testing.assert_series_equal(actual.loc[:dates[20]], avg_trading_value_20d(changed).loc[:dates[20]])
    fallback = prices.drop(columns="trading_value")
    expected = (fallback.close * fallback.volume).unstack().rolling(20, min_periods=1).mean()
    assert avg_trading_value_20d(fallback).loc[(dates[20], "000000")] == pytest.approx(expected.loc[dates[20], "000000"])
    prices.loc[(dates[0], "000000"), "trading_value"] = np.nan
    assert avg_trading_value_20d(prices).loc[(dates[0], "000000")] == prices.loc[(dates[0], "000000"), "close"] * 100000


def test_cost_model_amount_and_multiplier():
    prices, scores = sample_prices(days=8, stocks=20)
    prices["market"] = "KOSPI"
    prices["trading_value"] = 1e8
    dates = prices.index.get_level_values("trade_date").unique()
    first = evaluate_signal(scores, prices, horizons=(1,), n_controls=5)["horizons"]["1"]
    second = evaluate_signal(scores, prices, horizons=(1,), n_controls=5, cost_multiplier=1.5)["horizons"]["1"]
    for a, b in zip(first["quantiles"]["daily"], second["quantiles"]["daily"]):
        for q in a["quantiles"]:
            gross, net = a["quantiles"][q]["gross"], a["quantiles"][q]["net"]
            expected = cost_model.round_trip_cost(10000, "KOSPI", dates[1].date(), 1e8)
            assert gross - net == pytest.approx(expected)
            assert net < gross
            assert b["quantiles"][q]["net"] < net
        assert a["top_net_excess"] == pytest.approx(a["quantiles"]["5"]["net"] - a["universe_gross"])


def test_t_stat_uses_every_hth_input_date_even_when_one_date_is_skipped():
    prices, scores = sample_prices(days=45, stocks=20)
    score_dates = pd.DatetimeIndex(scores.trade_date.unique())
    scores = scores.loc[~((scores.trade_date == score_dates[5]) & (scores.stock_code >= "000002"))]
    result = evaluate_signal(scores, prices, horizons=(5,), n_controls=5)["horizons"]["5"]
    sampled = [row["ic"] for row in result["ic"]["daily"] if pd.Timestamp(row["trade_date"]) in set(score_dates[::5])]
    expected = np.mean(sampled) / (np.std(sampled, ddof=1) / np.sqrt(len(sampled)))
    assert result["ic"]["t_stat"] == pytest.approx(expected)
    assert result["ic"]["non_overlapping"]["count"] == len(sampled)
    assert result["skipped_dates"] == 5  # thin date plus four unavailable exits
    assert result["ic"]["by_year"]["2024"]["t_stat"] == pytest.approx(expected)


def test_holdout_guard_includes_forward_prices_and_passes_2024(tmp_path):
    prices, scores = sample_prices(days=55, stocks=12, start="2025-12-01")
    scores = scores.loc[scores.trade_date <= "2025-12-31"]
    with pytest.raises(ValueError, match="experiment_id"):
        evaluate_signal(scores, prices, horizons=(20,), repo_root=tmp_path)
    with pytest.raises(ValueError, match="experiment_id"):
        forward_returns(prices, horizons=(20,), repo_root=tmp_path)
    with pytest.raises(ValueError, match="experiment_id"):
        avg_trading_value_20d(prices, repo_root=tmp_path)
    old_prices, old_scores = sample_prices(days=30, stocks=12)
    assert evaluate_signal(old_scores, old_prices, horizons=(20,), n_controls=2, repo_root=tmp_path)
    assert not list(tmp_path.iterdir())


def test_guard_is_first_calculation_and_passes_full_span_and_options(monkeypatch, tmp_path):
    prices, scores = sample_prices(days=55, stocks=12, start="2025-12-01")
    scores = scores.loc[scores.trade_date <= "2025-12-31"]
    calls = []
    def guard(start, end, **kwargs):
        calls.append((start, end, kwargs))
        raise RuntimeError("guard first")
    monkeypatch.setattr(holdout, "guard_period", guard)
    prices["close"] = "must not be read"
    with pytest.raises(RuntimeError, match="guard first"):
        evaluate_signal(scores, prices, horizons=(1, 20), experiment_id="TEST", repo_root=tmp_path, ledger_path="ledger.jsonl")
    sessions = prices.index.get_level_values("trade_date").unique()
    assert calls == [(date(2025, 12, 1), sessions[sessions.get_loc(pd.Timestamp("2025-12-31")) + 20].date(),
                      {"experiment_id": "TEST", "repo_root": tmp_path, "ledger_path": "ledger.jsonl"})]


def test_unused_future_prices_do_not_consume_holdout():
    prices, scores = sample_prices(days=60, stocks=12, start="2025-11-03")
    scores = scores.loc[scores.trade_date <= "2025-11-28"]
    result = evaluate_signal(scores, prices, horizons=(1,), n_controls=2)
    assert result["horizons"]["1"]["ic"]["count"] == 20


@pytest.mark.parametrize("horizons", [(0,), (-1,), (1.5,), (True,), (), (1, 1)])
def test_invalid_horizons_reject_temporal_or_ambiguous_windows(horizons):
    prices, _ = sample_prices(days=3, stocks=4)
    with pytest.raises(ValueError, match="entry must follow availability"):
        forward_returns(prices, horizons)


def test_intraday_score_dates_are_rejected():
    prices, scores = sample_prices(days=3, stocks=12)
    scores["trade_date"] += pd.Timedelta(hours=23)
    with pytest.raises(ValueError, match="date-only"):
        evaluate_signal(scores, prices, n_controls=2)


@pytest.mark.parametrize("column,value", [("open", 0), ("open", np.nan), ("volume", 0), ("volume", np.inf)])
def test_untradable_entry_is_excluded_and_thin_date_counted(column, value):
    prices, scores = sample_prices(days=4, stocks=12)
    dates = prices.index.get_level_values("trade_date").unique()
    prices[column] = prices[column].astype(float)
    prices.loc[(dates[1], "000000"), column] = value
    assert pd.isna(forward_returns(prices, (1,)).loc[(dates[0], "000000"), 1])
    result = evaluate_signal(scores, prices, horizons=(1,), min_stocks=12, n_controls=2)["horizons"]["1"]
    assert result["skipped_dates"] == 1


def test_spearman_average_ranks_and_deterministic_quantile_ties():
    prices, scores = sample_prices(days=3, stocks=4)
    dates = prices.index.get_level_values("trade_date").unique()
    scores = scores.loc[scores.trade_date == dates[0]].copy()
    scores["score"] = [1, 1, 3, 4]
    prices.loc[dates[1], "close"] = [14000, 13000, 12000, 11000]
    result = evaluate_signal(scores, prices, horizons=(1,), min_stocks=4, quantiles=2, n_controls=2)["horizons"]["1"]
    assert result["ic"]["mean"] == pytest.approx(-4.5 / np.sqrt(22.5))
    assert result["quantiles"]["mean"]["1"]["gross"] == pytest.approx(0.35)
    assert result["quantiles"]["mean"]["2"]["gross"] == pytest.approx(0.15)


def test_constant_scores_and_empty_data_are_json_serializable():
    prices, scores = sample_prices(days=4, stocks=12)
    scores["score"] = 1.0
    result = evaluate_signal(scores, prices, horizons=(1,), n_controls=2)
    assert result["horizons"]["1"]["ic"]["mean"] is None
    json.dumps(result, allow_nan=False)
    empty = evaluate_signal(scores.iloc[:0], prices.iloc[:0], horizons=(1,), n_controls=2)
    assert empty["horizons"]["1"]["ic"]["count"] == 0
    json.dumps(empty, allow_nan=False)


def holding_sample():
    prices, scores = sample_prices(days=8, stocks=4)
    prices[["open", "high", "low", "close"]] = 100.0
    prices["ret_1d"] = 0.0
    prices["base_price"] = 100.0
    dates = prices.index.get_level_values("trade_date").unique()
    scores = scores.loc[scores.trade_date == dates[0]].copy()
    scores["score"] = np.arange(4)
    return prices, scores, dates


def holding_metrics(scores, prices, h, **kwargs):
    return evaluate_signal(scores, prices, horizons=(h,), quantiles=2, min_stocks=4,
                           n_controls=10, **kwargs)["horizons"][str(h)]


def test_split_returns_chain_and_entry_ret_is_not_used():
    prices, scores, dates = holding_sample()
    prices.loc[(dates[1], "000000"), ["close", "ret_1d"]] = [102, np.nan]
    prices.loc[(dates[2], "000000"), ["open", "close", "base_price", "ret_1d"]] = [51, 51.51, 51, 0.01]
    prices.loc[(dates[3], "000000"), ["close", "base_price", "ret_1d"]] = [52.5402, 51.51, 0.02]
    result = forward_returns(prices, (3,))
    expected = 1.02 * 1.01 * 1.02 - 1
    assert result.loc[(dates[0], "000000"), 3] == pytest.approx(expected)
    assert result.attrs["price_basis"] == "adjusted_chain"
    evaluated = evaluate_signal(scores, prices, horizons=(3,), quantiles=2, min_stocks=4, n_controls=10)
    metrics = evaluated["horizons"]["3"]
    assert evaluated["price_basis"] == "adjusted_chain"
    assert metrics["unadjusted_fallback"] == 0
    assert metrics["quantiles"]["mean"]["1"]["gross"] == pytest.approx(expected / 2)


@pytest.mark.parametrize("missing", ["column", "session", "nonfinite"])
def test_missing_adjusted_return_falls_back_and_is_counted(missing):
    prices, scores, dates = holding_sample()
    prices.loc[(dates[2], "000000"), ["open", "close", "base_price", "ret_1d"]] = [50, 50.5, 50, 0.01]
    prices.loc[(dates[3], "000000"), ["close", "base_price", "ret_1d"]] = [51, 50.5, 0.01]
    if missing == "column":
        prices = prices.drop(columns="ret_1d")
    else:
        prices.loc[(dates[2], "000000"), "ret_1d"] = np.nan if missing == "session" else np.inf
    result = forward_returns(prices, (3,))
    assert result.loc[(dates[0], "000000"), 3] == pytest.approx(-0.49)
    assert result.attrs["price_basis"] == ("raw" if missing == "column" else "adjusted_chain")
    metrics = holding_metrics(scores, prices, 3)
    assert metrics["unadjusted_fallback"] == (4 if missing == "column" else 1)
    assert metrics["quantiles"]["mean"]["1"]["gross"] == pytest.approx(-0.49 / 2)
    json.dumps(metrics, allow_nan=False)


@pytest.mark.parametrize("exit_kind", ["missing", "zero_volume", "zero_close"])
def test_stale_exit_kept_inside_window_and_drop_remains_available(exit_kind):
    prices, scores, dates = holding_sample()
    prices.loc[(dates[1], "000000"), "close"] = 105
    prices.loc[(dates[2], "000000"), ["close", "ret_1d"]] = [106, 106 / 105 - 1]
    prices.loc[(dates[3], "000000"), ["close", "ret_1d"]] = [107, 107 / 106 - 1]
    prices.loc[(dates[4], "000000"), ["close", "ret_1d"]] = [108, 108 / 107 - 1]
    if exit_kind == "missing":
        prices = prices.drop((dates[4], "000000"))
    else:
        prices.loc[(dates[4], "000000"), "volume" if exit_kind == "zero_volume" else "close"] = 0
    result = forward_returns(prices, (4,))
    expected = 0.08 if exit_kind == "zero_volume" else 0.07
    assert result.loc[(dates[0], "000000"), 4] == pytest.approx(expected)
    metrics = holding_metrics(scores, prices, 4)
    assert metrics["observations"] == 4
    assert metrics["stale_exit"] == 1
    assert metrics["unadjusted_fallback"] == 0  # Missing ret after the valuation date is unnecessary.
    assert metrics["halted_after_entry"] == 0
    assert metrics["no_later_price"] == 0
    assert metrics["stale_exits"][0]["stale_exit"] is True
    assert metrics["stale_exits"][0]["exit_date"] == dates[4 if exit_kind == "zero_volume" else 3].date().isoformat()
    dropped = holding_metrics(scores, prices, 4, missing_exit="drop")
    assert dropped["observations"] == 3
    assert dropped["excluded"] == {"missing_exit_price" if exit_kind == "missing" else "untradable_exit": 1}


@pytest.mark.parametrize("halt_kind", ["missing_rows", "zero_volume"])
def test_halted_after_entry_uses_entry_close_without_future_prices(halt_kind):
    prices, scores, dates = holding_sample()
    prices.loc[(dates[1], "000000"), ["close", "ret_1d"]] = [102, np.nan]
    if halt_kind == "missing_rows":
        prices = prices.drop([(day, "000000") for day in dates[2:5]])
    else:
        prices.loc[(dates[2:5], "000000"), ["volume", "close", "ret_1d"]] = [0, 0, np.nan]
    # A resumption beyond the window supplies row coverage, but its price is unused.
    prices.loc[(dates[5], "000000"), "close"] = 1e6
    actual = forward_returns(prices, (4,)).loc[(dates[0], "000000"), 4]
    assert actual == pytest.approx(0.02)
    metrics = holding_metrics(scores, prices, 4)
    assert metrics["halted_after_entry"] == metrics["stale_exit"] == 1
    assert metrics["unadjusted_fallback"] == metrics["no_later_price"] == 0
    assert metrics["stale_exits"][0]["exit_date"] == dates[1].date().isoformat()


def test_positive_stale_closes_preserve_losses_even_without_trading():
    prices, scores, dates = holding_sample()
    prices.loc[(dates[2:5], "000000"), ["volume", "close", "ret_1d"]] = [0, 98, np.nan]
    result = forward_returns(prices, (4,))
    assert result.loc[(dates[0], "000000"), 4] == pytest.approx(-0.02)
    metrics = holding_metrics(scores, prices, 4)
    assert metrics["stale_exit"] == metrics["unadjusted_fallback"] == 1
    assert metrics["halted_after_entry"] == 0
    assert metrics["stale_exits"][0]["exit_date"] == dates[4].date().isoformat()


@pytest.mark.parametrize("last_row", [1, 3])
def test_delisted_stock_is_excluded_and_no_later_price_is_prominent(last_row):
    prices, scores, dates = holding_sample()
    prices = prices.drop([(day, "000000") for day in dates[last_row + 1:]])
    forward = forward_returns(prices, (4,))
    assert pd.isna(forward.loc[(dates[0], "000000"), 4])
    assert forward.attrs["horizons"]["4"]["no_later_price"] >= 1
    for policy in ("last_close", "drop"):
        metrics = holding_metrics(scores, prices, 4, missing_exit=policy)
        assert metrics["no_later_price"] == metrics["excluded_count"] == 1
        assert metrics["excluded"] == {"no_later_price": 1}
        assert metrics["observations"] == 3
        assert metrics["stale_exit"] == metrics["halted_after_entry"] == 0


@pytest.mark.parametrize("reference", ["base_price", "previous_close", "missing_base"])
def test_limit_up_entry_uses_base_or_previous_close_and_assume_fill(reference):
    prices, scores, dates = holding_sample()
    prices.loc[(dates[1], "000000"), "open"] = 129.5
    if reference == "base_price":
        prices.loc[(dates[0], "000000"), "close"] = 200  # Base must take precedence.
    elif reference == "previous_close":
        prices = prices.drop(columns="base_price")
    else:
        prices.loc[(dates[1], "000000"), "base_price"] = np.nan
    assert pd.isna(forward_returns(prices, (1,)).loc[(dates[0], "000000"), 1])
    metrics = holding_metrics(scores, prices, 1)
    assert metrics["excluded"] == {"limit_up_entry": 1}
    filled = holding_metrics(scores, prices, 1, limit_fill="assume_fill")
    assert filled["observations"] == 4
    assert filled["excluded_count"] == 0


def test_limit_down_exit_uses_last_tradable_close_and_counts_it():
    prices, scores, dates = holding_sample()
    prices.loc[(dates[1], "000000"), "close"] = 101
    prices.loc[(dates[2], "000000"), ["close", "ret_1d"]] = [102, 102 / 101 - 1]
    prices.loc[(dates[3], "000000"), ["close", "ret_1d"]] = [70.5, -0.295]
    assert forward_returns(prices, (3,)).loc[(dates[0], "000000"), 3] == pytest.approx(0.02)
    metrics = holding_metrics(scores, prices, 3)
    assert metrics["limit_down_exit"] == metrics["stale_exit"] == 1
    assert metrics["stale_exits"][0]["exit_date"] == dates[2].date().isoformat()
    assert holding_metrics(scores, prices, 3, missing_exit="drop")["excluded"] == {"untradable_exit": 1}
    assumed = forward_returns(prices, (3,), limit_fill="assume_fill")
    assert assumed.loc[(dates[0], "000000"), 3] == pytest.approx(1.02 * 0.705 - 1)


@pytest.mark.parametrize("kwargs", [{"missing_exit": "fill"}, {"limit_fill": "allow"}])
def test_unknown_execution_policies_fail(kwargs):
    prices, scores, _ = holding_sample()
    with pytest.raises(ValueError):
        forward_returns(prices, (1,), **kwargs)
    with pytest.raises(ValueError):
        holding_metrics(scores, prices, 1, **kwargs)


def test_evaluation_uses_vector_cost_without_scalar_row_calls(monkeypatch):
    prices, scores, _ = holding_sample()
    def scalar_call(*args, **kwargs):
        raise AssertionError("scalar cost loop")
    monkeypatch.setattr(cost_model, "round_trip_cost", scalar_call)
    assert holding_metrics(scores, prices, 3)["observations"] == 4
