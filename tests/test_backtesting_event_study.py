from __future__ import annotations

import json
import hashlib
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, holdout
from backtesting.event_study import run_event_study
from backtesting.signal_eval import evaluate_signal


def sample(days=70, stocks=20, start="2024-01-02"):
    rng = np.random.default_rng(48)
    dates = pd.bdate_range(start, periods=days)
    codes = [f"{number:06d}" for number in range(stocks)]
    returns = rng.normal(scale=0.015, size=(days, stocks))
    returns[:, :2] += 0.04
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000.0, "high": 15000.0, "low": 5000.0,
                           "close": 10000 * (1 + returns.ravel()), "volume": 100000,
                           "market": ["KOSPI", "KOSDAQ"] * (len(index) // 2), "trading_value": 1e9}, index=index)
    benchmarks = pd.DataFrame({"trade_date": np.repeat(dates, 2), "market": ["KOSPI", "KOSDAQ"] * days,
                               "close": 1000.0})
    events = pd.DataFrame([{"event_id": f"{i}-{code}", "stock_code": code, "available_at": day.date(),
                            "category": "effect" if int(code) < 2 else "noise", "size": i * 2 + int(code)}
                           for i, day in enumerate(dates[1:-2]) for code in ("000000", "000002")])
    return prices, benchmarks, events


def test_planted_category_effect_and_clustered_t_stat():
    prices, benchmarks, events = sample()
    result = run_event_study(events, prices, benchmarks, feature="category", horizons=(1,), n_controls=100)["horizons"]["1"]
    effect = next(group for group in result["by_feature"] if group["value"] == "effect")
    assert effect["t_stat"] > 3
    assert effect["mean_net"] < effect["mean_abnormal"]
    assert result["overall"]["mean_abnormal"] > result["random_control"]["p95"]
    rows = pd.DataFrame(result["events"])
    per_date = rows.groupby("entry_date").abnormal.mean()
    expected = per_date.mean() / (per_date.std(ddof=1) / np.sqrt(len(per_date)))
    assert result["overall"]["t_stat"] == pytest.approx(expected)
    assert result["overall"]["date_count"] * 2 == result["overall"]["count"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("available,entry", [
    (date(2024, 1, 3), "2024-01-04"), ("2024-01-03", "2024-01-04"),
    ("2024-01-03T08:59:59+09:00", "2024-01-03"),
    ("2024-01-02T23:59:59+00:00", "2024-01-03"),
    ("2024-01-03T09:00:00+09:00", "2024-01-04"),
    ("2024-01-03T15:30:00+09:00", "2024-01-04"),
    (date(2024, 1, 6), "2024-01-08"),
    ("2024-01-06T08:00:00+09:00", "2024-01-08"),
])
def test_entry_timing_and_horizon(available, entry):
    prices, benchmarks, _ = sample(days=10)
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000000", "available_at": available}])
    dates = prices.index.get_level_values("trade_date").unique()
    entry_date = pd.Timestamp(entry)
    exit_date = dates[dates.get_loc(entry_date) + 2]
    prices.loc[(entry_date, "000000"), "open"] = 20000.0
    prices.loc[(exit_date, "000000"), "close"] = 24000.0
    # The synthetic 100% opening jump isolates timing from limit-fill assumptions.
    result = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=3,
                             limit_fill="assume_fill")["horizons"]["3"]
    row = result["events"][0]
    assert row["entry_date"] == entry
    assert row["exit_date"] == exit_date.date().isoformat()
    assert row["gross"] == pytest.approx(0.2)
    assert row["abnormal"] == pytest.approx(0.2)


def test_market_specific_benchmark_previous_close_and_known_adv_cost():
    prices, benchmarks, _ = sample(days=8)
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000001", "available_at": date(2024, 1, 3)}])
    benchmarks.loc[(benchmarks.trade_date == "2024-01-03") & (benchmarks.market == "KOSDAQ"), "close"] = 2000.0
    benchmarks.loc[(benchmarks.trade_date == "2024-01-04") & (benchmarks.market == "KOSDAQ"), "close"] = 2100.0
    prices.loc[(pd.Timestamp("2024-01-04"), "000001"), "trading_value"] = 1e15
    a = run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=2)["horizons"]["1"]["events"][0]
    b = run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=2, cost_multiplier=2)["horizons"]["1"]["events"][0]
    assert a["abnormal"] == pytest.approx(a["gross"] - 0.05)
    expected = cost_model.round_trip_cost(10000, "KOSDAQ", date(2024, 1, 4), 1e9)
    assert a["abnormal"] - a["net"] == pytest.approx(expected)
    assert b["net"] < a["net"]


def test_missing_entry_exit_and_benchmark_counted_without_shifting():
    prices, benchmarks, _ = sample(days=8)
    events = pd.DataFrame([
        {"event_id": "entry", "stock_code": "000000", "available_at": date(2024, 1, 3)},
        {"event_id": "exit", "stock_code": "000002", "available_at": date(2024, 1, 3)},
        {"event_id": "benchmark", "stock_code": "000001", "available_at": date(2024, 1, 3)},
        {"event_id": "past_end", "stock_code": "000002", "available_at": date(2024, 1, 12)},
        {"event_id": "exit_past_end", "stock_code": "000002", "available_at": date(2024, 1, 10)},
    ])
    prices = prices.drop([(pd.Timestamp("2024-01-04"), "000000"), (pd.Timestamp("2024-01-08"), "000002")])
    benchmarks = benchmarks.loc[~((benchmarks.trade_date == "2024-01-03") & (benchmarks.market == "KOSDAQ"))]
    result = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=2)["horizons"]["3"]
    assert result["excluded"] == {"missing_entry_price": 1, "missing_benchmark": 1,
                                   "missing_entry_session": 1, "missing_exit_session": 1}
    assert result["excluded_count"] == 4
    assert result["overall"]["count"] == result["stale_exit"] == 1
    assert result["events"][0]["exit_date"] == "2024-01-05"
    dropped = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=2,
                              missing_exit="drop")["horizons"]["3"]
    assert dropped["excluded_count"] == 5
    assert dropped["excluded"]["missing_exit_price"] == 1
    json.dumps(result, allow_nan=False)


def test_numeric_feature_buckets_and_seeded_controls():
    prices, benchmarks, events = sample(days=15)
    kwargs = dict(horizons=(1,), feature="size", n_buckets=4, n_controls=10, seed=3)
    result = run_event_study(events, prices, benchmarks, **kwargs)
    buckets = result["horizons"]["1"]["by_feature"]
    assert len(buckets) == 4
    assert sum(bucket["count"] for bucket in buckets) == len(events)
    assert result == run_event_study(events, prices, benchmarks, **kwargs)


def test_same_date_event_duplication_does_not_inflate_clustered_t():
    prices, benchmarks, events = sample(days=20)
    original = run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=2)["horizons"]["1"]
    duplicates = pd.concat([events.assign(event_id=events.event_id + f"-{i}") for i in range(10)])
    repeated = run_event_study(duplicates, prices, benchmarks, horizons=(1,), n_controls=2)["horizons"]["1"]
    assert repeated["overall"]["count"] == 10 * original["overall"]["count"]
    assert repeated["overall"]["t_stat"] == pytest.approx(original["overall"]["t_stat"])


def test_controls_only_use_entry_tradable_names_and_report_missing_exits():
    prices, benchmarks, _ = sample(days=8, stocks=4)
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000000", "available_at": date(2024, 1, 3)}])
    prices.loc[(pd.Timestamp("2024-01-04"), "000002"), "volume"] = 0
    prices.loc[(pd.Timestamp("2024-01-08"), "000002"), "close"] = 1e10
    prices = prices.drop((pd.Timestamp("2024-01-08"), "000001"))
    result = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=100)["horizons"]["3"]
    assert result["random_control"]["p95"] < 1
    assert result["random_control"]["stale_exit"] > 0
    assert sum(result["random_control"]["valid_events_per_draw"]) == 100
    dropped = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=100,
                              missing_exit="drop")["horizons"]["3"]
    assert dropped["random_control"]["excluded"]["missing_exit_price"] > 0
    assert sum(dropped["random_control"]["valid_events_per_draw"]) < 100


@pytest.mark.parametrize("available", [pd.Timestamp("2024-01-03"), "2024-01-03T08:00:00", None])
def test_naive_or_missing_timestamps_rejected(available):
    prices, benchmarks, _ = sample(days=8)
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000000", "available_at": available}])
    with pytest.raises(ValueError):
        run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=2)


def test_event_holdout_forward_span_blocked(tmp_path):
    prices, benchmarks, _ = sample(days=45, start="2025-12-01")
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000000", "available_at": date(2025, 12, 31)}])
    with pytest.raises(ValueError, match="experiment_id"):
        run_event_study(events, prices, benchmarks, horizons=(20,), repo_root=tmp_path)


def test_tools_share_one_m0_holdout_session_and_pass_custom_ledger(monkeypatch, tmp_path):
    # Only preregistration verification is stubbed; real M0 claims/guards/session run.
    monkeypatch.setattr(holdout, "verify_preregistration", lambda *_: SimpleNamespace(fields={"uses_holdout": True}))
    prices, benchmarks, _ = sample(days=45, start="2025-12-01")
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000000", "available_at": date(2025, 12, 31)}])
    scores = prices.loc[[pd.Timestamp("2025-12-31")]].reset_index()[["trade_date", "stock_code"]]
    scores["score"] = np.arange(len(scores))
    kwargs = dict(experiment_id="M2", repo_root=tmp_path, ledger_path="custom.jsonl", horizons=(20,), n_controls=2)
    with holdout.HoldoutSession("M2", repo_root=tmp_path, ledger_path="custom.jsonl"):
        assert run_event_study(events, prices, benchmarks, **kwargs)["horizons"]["20"]["overall"]["count"] == 1
        assert evaluate_signal(scores, prices, **kwargs)["horizons"]["20"]["ic"]["count"] == 1
    assert len((tmp_path / "custom.jsonl").read_text().splitlines()) == 1
    with pytest.raises(ValueError, match="already used"):
        run_event_study(events, prices, benchmarks, **kwargs)


def test_empty_events_return_empty_json_metrics():
    prices, benchmarks, events = sample(days=8)
    result = run_event_study(events.iloc[:0], prices, benchmarks, horizons=(1,), n_controls=2)
    assert result["horizons"]["1"]["overall"]["count"] == 0
    json.dumps(result, allow_nan=False)


def holding_sample():
    prices, benchmarks, _ = sample(days=8, stocks=4)
    prices[["open", "high", "low", "close"]] = 100.0
    prices["ret_1d"] = 0.0
    prices["base_price"] = 100.0
    dates = prices.index.get_level_values("trade_date").unique()
    events = pd.DataFrame([{"event_id": "E", "stock_code": "000000", "available_at": dates[0].date()}])
    return prices, benchmarks, events, dates


def test_split_adjustment_matches_signal_and_controls_use_adjusted_chain():
    prices, benchmarks, events, dates = holding_sample()
    # All controls share the split, so an unadjusted implementation cannot hide in noise.
    prices.loc[dates[1], ["close", "ret_1d"]] = [102, np.nan]
    prices.loc[dates[2], ["open", "close", "base_price", "ret_1d"]] = [51, 51.51, 51, 0.01]
    prices.loc[dates[3], ["close", "base_price", "ret_1d"]] = [52.5402, 51.51, 0.02]
    result = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=20)
    expected = 1.02 * 1.01 * 1.02 - 1
    metrics = result["horizons"]["3"]
    assert result["price_basis"] == "adjusted_chain"
    assert metrics["events"][0]["gross"] == pytest.approx(expected)
    assert metrics["events"][0]["price_basis"] == "adjusted_chain"
    assert metrics["unadjusted_fallback"] == 0
    assert metrics["random_control"]["p50"] == pytest.approx(expected)
    assert metrics["random_control"]["unadjusted_fallback"] == 0
    scores = pd.DataFrame({"trade_date": dates[0], "stock_code": [f"{i:06d}" for i in range(4)], "score": range(4)})
    signal = evaluate_signal(scores, prices, horizons=(3,), min_stocks=4, quantiles=2, n_controls=2)
    assert signal["horizons"]["3"]["quantiles"]["mean"]["1"]["gross"] == pytest.approx(expected)


@pytest.mark.parametrize("missing", ["column", "session", "nonfinite"])
def test_missing_ret_falls_back_and_is_counted_in_events_and_controls(missing):
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[dates[2], ["open", "close", "base_price", "ret_1d"]] = [50, 50.5, 50, 0.01]
    prices.loc[dates[3], ["close", "base_price", "ret_1d"]] = [51, 50.5, 0.01]
    if missing == "column":
        prices = prices.drop(columns="ret_1d")
    else:
        prices.loc[dates[2], "ret_1d"] = np.nan if missing == "session" else np.inf
    result = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=20)
    metrics = result["horizons"]["3"]
    assert result["price_basis"] == ("raw" if missing == "column" else "adjusted_chain")
    assert metrics["events"][0]["gross"] == pytest.approx(-0.49)
    assert metrics["events"][0]["price_basis"] == "raw"
    assert metrics["events"][0]["unadjusted_fallback"] is True
    assert metrics["overall"]["unadjusted_fallback"] == metrics["unadjusted_fallback"] == 1
    assert metrics["random_control"]["unadjusted_fallback"] == 20
    assert metrics["random_control"]["p50"] == pytest.approx(-0.49)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("exit_kind", ["missing", "zero_volume", "zero_close"])
def test_stale_exit_and_matching_benchmark_date_are_kept(exit_kind):
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[(dates[1], "000000"), "close"] = 105
    prices.loc[(dates[2], "000000"), ["close", "ret_1d"]] = [106, 106 / 105 - 1]
    prices.loc[(dates[3], "000000"), ["close", "ret_1d"]] = [107, 107 / 106 - 1]
    prices.loc[(dates[4], "000000"), ["close", "ret_1d"]] = [108, 108 / 107 - 1]
    benchmarks.loc[benchmarks.trade_date == dates[3], "close"] = 1010
    benchmarks.loc[benchmarks.trade_date == dates[4], "close"] = 1020
    if exit_kind == "missing":
        prices = prices.drop((dates[4], "000000"))
    else:
        prices.loc[(dates[4], "000000"), "volume" if exit_kind == "zero_volume" else "close"] = 0
    metrics = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=100)["horizons"]["4"]
    row = metrics["events"][0]
    expected = 0.08 if exit_kind == "zero_volume" else 0.07
    benchmark_return = 0.02 if exit_kind == "zero_volume" else 0.01
    assert row["gross"] == pytest.approx(expected)
    assert row["abnormal"] == pytest.approx(expected - benchmark_return)
    assert row["stale_exit"] is True
    assert row["scheduled_exit_date"] == dates[4].date().isoformat()
    assert row["exit_date"] == dates[4 if exit_kind == "zero_volume" else 3].date().isoformat()
    assert metrics["stale_exit"] == metrics["overall"]["stale_exit"] == 1
    assert metrics["unadjusted_fallback"] == metrics["halted_after_entry"] == metrics["no_later_price"] == 0
    assert metrics["random_control"]["stale_exit"] > 0
    dropped = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=2,
                              missing_exit="drop")["horizons"]["4"]
    assert dropped["overall"]["count"] == 0
    assert dropped["excluded"] == {"missing_exit_price" if exit_kind == "missing" else "untradable_exit": 1}


@pytest.mark.parametrize("halt_kind", ["missing_rows", "zero_volume"])
def test_halted_from_day_after_entry_uses_entry_close(halt_kind):
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[(dates[1], "000000"), ["close", "ret_1d"]] = [102, np.nan]
    if halt_kind == "missing_rows":
        prices = prices.drop([(day, "000000") for day in dates[2:5]])
    else:
        prices.loc[(dates[2:5], "000000"), ["volume", "close", "ret_1d"]] = [0, 0, np.nan]
    prices.loc[(dates[5], "000000"), "close"] = 1e6
    metrics = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=100)["horizons"]["4"]
    row = metrics["events"][0]
    assert row["gross"] == pytest.approx(0.02)
    assert row["exit_date"] == dates[1].date().isoformat()
    assert row["halted_after_entry"] is row["stale_exit"] is True
    assert metrics["halted_after_entry"] == metrics["stale_exit"] == 1
    assert metrics["unadjusted_fallback"] == metrics["no_later_price"] == 0
    assert metrics["random_control"]["halted_after_entry"] > 0


def test_positive_stale_closes_preserve_losses_in_events_and_controls():
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[dates[2:5], ["volume", "close", "ret_1d"]] = [0, 98, np.nan]
    metrics = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=20)["horizons"]["4"]
    assert metrics["events"][0]["gross"] == pytest.approx(-0.02)
    assert metrics["events"][0]["exit_date"] == dates[4].date().isoformat()
    assert metrics["stale_exit"] == metrics["unadjusted_fallback"] == 1
    assert metrics["halted_after_entry"] == 0
    assert metrics["random_control"]["p50"] == pytest.approx(-0.02)
    assert metrics["random_control"]["stale_exit"] == metrics["random_control"]["unadjusted_fallback"] == 20


@pytest.mark.parametrize("last_row", [1, 3])
def test_no_later_price_excluded_and_reported_in_summary_and_controls(last_row):
    prices, benchmarks, events, dates = holding_sample()
    events = pd.concat([events, events.assign(event_id="other", stock_code="000001")], ignore_index=True)
    prices = prices.drop([(day, "000000") for day in dates[last_row + 1:]])
    for policy in ("last_close", "drop"):
        metrics = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=100,
                                  missing_exit=policy)["horizons"]["4"]
        assert metrics["no_later_price"] == metrics["overall"]["no_later_price"] == 1
        assert metrics["excluded"] == {"no_later_price": 1}
        assert metrics["overall"]["count"] == 1
        assert metrics["random_control"]["no_later_price"] > 0
        assert metrics["random_control"]["excluded"]["no_later_price"] > 0
        assert metrics["stale_exit"] == metrics["halted_after_entry"] == 0


@pytest.mark.parametrize("reference", ["base_price", "previous_close", "missing_base"])
def test_limit_up_entry_exclusion_and_control_pool(reference):
    prices, benchmarks, events, dates = holding_sample()
    events = pd.concat([events, events.assign(event_id="other", stock_code="000001")], ignore_index=True)
    prices.loc[(dates[1], "000000"), ["open", "close"]] = [129.5, 1000]
    if reference == "base_price":
        prices.loc[(dates[0], "000000"), "close"] = 200
    elif reference == "previous_close":
        prices = prices.drop(columns="base_price")
    else:
        prices.loc[(dates[1], "000000"), "base_price"] = np.nan
    metrics = run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=100)["horizons"]["1"]
    assert metrics["excluded"] == {"limit_up_entry": 1}
    assert metrics["overall"]["count"] == 1
    assert metrics["random_control"]["p95"] == 0
    assert sum(metrics["random_control"]["valid_events_per_draw"]) == 100
    assumed = run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=100,
                              limit_fill="assume_fill")["horizons"]["1"]
    assert assumed["overall"]["count"] == 2
    assert assumed["random_control"]["p95"] > 1


def test_limit_down_exit_values_last_tradable_session_and_controls_agree():
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[dates[1], "close"] = 101
    prices.loc[dates[2], ["close", "ret_1d"]] = [102, 102 / 101 - 1]
    prices.loc[dates[3], ["close", "ret_1d"]] = [70.5, -0.295]
    metrics = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=20)["horizons"]["3"]
    row = metrics["events"][0]
    assert row["gross"] == pytest.approx(0.02)
    assert row["exit_date"] == dates[2].date().isoformat()
    assert row["limit_down_exit"] is row["stale_exit"] is True
    assert metrics["limit_down_exit"] == metrics["stale_exit"] == 1
    assert metrics["random_control"]["limit_down_exit"] == metrics["random_control"]["stale_exit"] == 20
    assert metrics["random_control"]["p50"] == pytest.approx(0.02)
    dropped = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=2,
                              missing_exit="drop")["horizons"]["3"]
    assert dropped["excluded"] == {"untradable_exit": 1}
    assumed = run_event_study(events, prices, benchmarks, horizons=(3,), n_controls=2,
                              limit_fill="assume_fill")["horizons"]["3"]
    assert assumed["events"][0]["gross"] == pytest.approx(1.02 * 0.705 - 1)


def test_limit_down_then_resumption_within_window_preserves_decline():
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[dates[2], ["close", "ret_1d"]] = [70.5, -0.295]
    prices.loc[dates[3], ["open", "close", "base_price", "ret_1d"]] = [70.5, 72, 70.5, 72 / 70.5 - 1]
    prices.loc[dates[4], ["close", "base_price"]] = [72, 72]
    prices = prices.drop((dates[4], "000000"))
    metrics = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=20)["horizons"]["4"]
    assert metrics["events"][0]["gross"] == pytest.approx(-0.28)
    assert metrics["events"][0]["exit_date"] == dates[3].date().isoformat()
    assert metrics["stale_exit"] == 1
    assert metrics["unadjusted_fallback"] == 0
    assert metrics["random_control"]["p50"] == pytest.approx(-0.28)


@pytest.mark.parametrize("kwargs", [{"missing_exit": "fill"}, {"limit_fill": "allow"}])
def test_unknown_execution_policies_fail(kwargs):
    prices, benchmarks, events, _ = holding_sample()
    with pytest.raises(ValueError):
        run_event_study(events, prices, benchmarks, horizons=(1,), **kwargs)


def test_event_study_uses_vector_cost_without_scalar_calls(monkeypatch):
    prices, benchmarks, events, _ = holding_sample()
    def scalar_call(*args, **kwargs):
        raise AssertionError("scalar cost loop")
    monkeypatch.setattr(cost_model, "round_trip_cost", scalar_call)
    result = run_event_study(events, prices, benchmarks, horizons=(1,), n_controls=2)
    assert result["horizons"]["1"]["overall"]["count"] == 1


def test_default_index_close_output_is_byte_identical():
    prices, benchmarks, events = sample(days=15)
    kwargs = dict(horizons=(1, 3, 5), feature="size", n_buckets=4, n_controls=10, seed=3)
    original = run_event_study(events, prices, benchmarks, **kwargs)
    explicit = run_event_study(events, prices, benchmarks, benchmark_mode="index_close", **kwargs)
    encoded = json.dumps(original, allow_nan=False).encode()
    assert json.dumps(explicit, allow_nan=False).encode() == encoded
    # Captured from the unmodified implementation with this seeded input.
    assert hashlib.sha256(encoded).hexdigest() == "40fc619164b95d0cf9ddc8736c4b905d693e51cc4bfaab1463b1c7ddb1ea3023"


def test_universe_open_benchmark_removes_entry_overnight_gap_in_events_and_controls():
    prices, benchmarks, events, dates = holding_sample()
    prices.loc[dates[1], ["open", "close"]] = [120, 126]
    prices.loc[dates[2], ["close", "ret_1d"]] = [128.52, 0.02]
    benchmarks.loc[benchmarks.trade_date == dates[1], "close"] = 1260
    benchmarks.loc[benchmarks.trade_date == dates[2], "close"] = 1285.2
    kwargs = dict(horizons=(1, 2), n_controls=20)
    legacy = run_event_study(events, prices, benchmarks, **kwargs)
    aligned = run_event_study(events, prices, benchmarks, benchmark_mode="universe_open_ew", **kwargs)
    assert aligned["benchmark_mode"] == "universe_open_ew"
    for h, gross, index_return in ((1, 0.05, 0.26), (2, 0.071, 0.2852)):
        old, new = legacy["horizons"][str(h)], aligned["horizons"][str(h)]
        assert old["events"][0]["abnormal"] == pytest.approx(gross - index_return)
        assert new["events"][0]["gross"] == pytest.approx(gross)
        assert new["events"][0]["abnormal"] == pytest.approx(0)
        assert new["random_control"]["p05"] == pytest.approx(0)
        assert new["random_control"]["p95"] == pytest.approx(0)
    # No index open or even index closes are needed for the new mode.
    assert aligned == run_event_study(events, prices, benchmarks.iloc[:0], benchmark_mode="universe_open_ew", **kwargs)


def naive_market_return(prices, entry, exit_date, market, missing_exit, limit_fill):
    """Scalar reference: entry-only membership, independent per-stock valuation."""
    values = []
    dates = prices.index.get_level_values("trade_date").unique()
    previous = dates[dates.get_loc(entry) - 1]
    for code in prices.index.get_level_values("stock_code").unique():
        stock = prices.xs(code, level="stock_code")
        if entry not in stock.index:
            continue
        start = stock.loc[entry]
        if start.market != market or not np.isfinite(start.open) or start.open <= 0 or not np.isfinite(start.volume) or start.volume <= 0:
            continue
        def base(day):
            value = stock.loc[day].get("base_price", np.nan)
            before = dates[dates.get_loc(day) - 1] if day != dates[0] else None
            return stock.loc[before, "close"] if pd.isna(value) and before in stock.index else value

        reference = base(entry)
        if limit_fill == "exclude" and np.isfinite(reference) and reference > 0 and start.open >= reference * 1.295:
            continue
        def positive_close(day):
            return np.isfinite(stock.loc[day, "close"]) and stock.loc[day, "close"] > 0

        def limit_down(day):
            reference = base(day)
            return limit_fill == "exclude" and np.isfinite(reference) and reference > 0 and stock.loc[day, "close"] <= reference * 0.705

        def tradable(day):
            volume = stock.loc[day, "volume"]
            return positive_close(day) and np.isfinite(volume) and volume > 0 and not limit_down(day)

        actual = exit_date
        if exit_date not in stock.index or not tradable(exit_date):
            if missing_exit == "drop" or exit_date not in stock.index and stock.index.max() < exit_date:
                continue
            candidates = [day for day in stock.index if entry <= day <= exit_date and positive_close(day) and not limit_down(day)
                          and (exit_date not in stock.index or not limit_down(exit_date) or tradable(day))]
            actual = max(candidates) if candidates else entry
            if not positive_close(actual):
                continue
        gross = stock.loc[actual, "close"] / start.open - 1
        chain_days = dates[(dates > entry) & (dates <= actual)]
        factors = stock.reindex(chain_days).get("ret_1d")
        if positive_close(entry) and "ret_1d" in stock and np.isfinite(factors).all():
            gross = start.close / start.open * np.prod(1 + factors) - 1
        values.append(gross)
    return float(np.mean(values))


@pytest.mark.parametrize("missing_exit", ["last_close", "drop"])
@pytest.mark.parametrize("limit_fill", ["exclude", "assume_fill"])
@pytest.mark.parametrize("adjusted", [True, False])
def test_vectorized_universe_benchmark_matches_naive_loop(missing_exit, limit_fill, adjusted):
    prices, benchmarks, _ = sample(days=10, stocks=10)
    dates = prices.index.get_level_values("trade_date").unique()
    prices[["open", "high", "low", "close"]] = 100.0
    prices["base_price"] = 100.0
    prices["ret_1d"] = 0.01
    # Distinct market returns and a split where chaining differs from raw ratios.
    for number in range(10):
        prices.loc[(dates[1:], f"{number:06d}"), "close"] = 101 + number
    prices.loc[(dates[2], "000002"), ["open", "close", "base_price"]] = [51, 51.51, 51]
    prices.loc[(dates[3], "000002"), ["close", "base_price"]] = [52, 51.51]
    prices.loc[(dates[2], "000003"), "ret_1d"] = np.nan
    # Entry-only exclusions must prevent these large future returns entering EW.
    prices.loc[(dates[1], "000004"), "volume"] = 0
    prices.loc[(dates[3], "000004"), "close"] = 10000
    prices.loc[(dates[1], "000005"), "open"] = np.nan
    prices.loc[(dates[1], "000006"), ["open", "close"]] = [129.5, 200]
    # Stale valuations, no-later-price exclusions, and a limit-down exit.
    prices.loc[(dates[3], "000007"), "volume"] = 0
    prices.loc[(dates[3], "000008"), ["close", "ret_1d"]] = [70, -0.3]
    prices = prices.drop([(dates[3], "000000"), *[(day, "000009") for day in dates[3:]]])
    if not adjusted:
        prices = prices.drop(columns="ret_1d")
    events = pd.DataFrame([{"event_id": f"{entry}-{code}", "stock_code": code, "available_at": day.date()}
                           for entry, day in enumerate(dates[:3]) for code in ("000000", "000001", "000002")])
    result = run_event_study(events, prices, benchmarks, horizons=(1, 3), n_controls=20,
                             missing_exit=missing_exit, limit_fill=limit_fill, benchmark_mode="universe_open_ew")
    rows = [row for horizon in result["horizons"].values() for row in horizon["events"]]
    assert rows
    for row in rows:
        entry, exit_date = pd.Timestamp(row["entry_date"]), pd.Timestamp(row["exit_date"])
        market = prices.loc[(entry, row["stock_code"]), "market"]
        expected = naive_market_return(prices, entry, exit_date, market, missing_exit, limit_fill)
        assert row["gross"] - row["abnormal"] == pytest.approx(expected)
    json.dumps(result, allow_nan=False)


def test_universe_benchmark_batches_repeated_events_and_stale_endpoints(monkeypatch):
    from backtesting import event_study

    prices, benchmarks, events, dates = holding_sample()
    prices = prices.drop((dates[4], "000000"))
    events = pd.concat([events.assign(event_id=str(i)) for i in range(1000)], ignore_index=True)
    original = event_study._holding_window
    lengths = []
    def counted(data, h, missing_exit):
        lengths.append(h)
        return original(data, h, missing_exit)

    monkeypatch.setattr(event_study, "_holding_window", counted)
    result = run_event_study(events, prices, benchmarks, horizons=(4,), n_controls=2,
                             benchmark_mode="universe_open_ew")
    assert result["horizons"]["4"]["overall"]["count"] == 1000
    assert lengths == [4, 3]


def test_unknown_benchmark_mode_fails():
    prices, benchmarks, events, _ = holding_sample()
    with pytest.raises(ValueError, match="benchmark_mode"):
        run_event_study(events, prices, benchmarks, benchmark_mode="index_open")


def test_empty_events_with_universe_benchmark_return_empty_json_metrics():
    prices, benchmarks, events, _ = holding_sample()
    result = run_event_study(events.iloc[:0], prices, benchmarks, horizons=(1,), n_controls=2,
                             benchmark_mode="universe_open_ew")
    assert result["benchmark_mode"] == "universe_open_ew"
    assert result["horizons"]["1"]["overall"]["count"] == 0
    json.dumps(result, allow_nan=False)
