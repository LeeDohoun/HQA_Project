"""R001 rules on synthetic prices, filings and local archives; no provider calls."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry
from backtesting.experiments import common, d001, r001
from src.ingestion import dart_quarterly
from src.research import industry_map


@pytest.fixture
def fields():
    return common.load_preregistration(r001.EXPERIMENT_ID)


@pytest.fixture
def registration(monkeypatch):
    registration = experiment_registry.verify_preregistration(r001.EXPERIMENT_ID)
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *args, **kwargs: registration)
    return registration


def market(start="2023-10-02", end="2024-04-30", size=20):
    days = pd.bdate_range(start, end)
    codes = [f"{(i + 1) * 10:06d}" for i in range(size)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000.0, "close": 10000.0, "base_price": 10000.0,
                           "volume": 10000.0, "trading_value": 2e9, "ret_1d": 0.0,
                           "stock_name": "합성보통주", "market": "KOSPI", "calendar_status": "verified"}, index=index)
    return prices, days, codes


def profiles(data_dir, codes):
    path = Path(data_dir) / "reference/dart_company/companies.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"stock_code": code, "induty_code": "261" if i % 2 else "30"}) + "\n"
                            for i, code in enumerate(codes)), encoding="utf-8")
    return path


def filing(year, quarter, day, *, code="000010", income=10, division="CFS", number=1):
    report = next(report for report, q in dart_quarterly.REPORTS.items() if q == quarter)
    # Q4 is annual; other quarters have a documented standalone and YTD amount.
    amount = None if income is None else income * (4 if quarter == 4 else 1)
    accounts = {} if income is None else {"operating_income": {"thstrm_amount": str(amount),
                                                              "thstrm_add_amount": str(income * quarter),
                                                              "thstrm_dt": None, "currency": "KRW"}}
    return {"stock_codes": [code], "corp_code": f"{int(code):08d}", "bsns_year": year,
            "reprt_code": report, "fiscal_quarter": f"{year}Q{quarter}", "fs_div": division,
            "available_date": day, "rcept_no": day.replace("-", "") + f"{number:06d}",
            "currency": "KRW", "raw_accounts": accounts}


def archives(data_dir, rows):
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True, exist_ok=True)
    groups = {}
    for row in rows:
        groups.setdefault(f"{row['bsns_year']}_{row['reprt_code']}.jsonl", []).append(row)
    for name, records in groups.items():
        (directory / name).write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return directory


def decision_universe(prices, days, *, day="2024-01-31", mapping=None, profitable=()):
    universe = r001.universe_on_decision(prices, day, mapping or {}, sessions=days, profitable=profitable)
    entry = days.get_loc(pd.Timestamp(day)) + 1
    return universe.assign(decision_date=pd.Timestamp(day), entry_date=days[entry], exit_date=days[entry + 19]).reset_index()


def metric_frame(months=40):
    rows = []
    dates = pd.period_range("2022-01", periods=months, freq="M").to_timestamp(how="end").normalize()
    for month, day in enumerate(dates):
        for stock in range(20):
            selected = stock < 10
            rows.append({"decision_date": day, "entry_date": day + pd.Timedelta(days=1), "exit_date": day + pd.Timedelta(days=28),
                         "stock_code": f"{(stock + 1) * 10:06d}", "avg_trading_value_20d": 2e9,
                         "gross": (0.04 + 0.01 * (month % 4)) if selected else 0.005 * (month % 3),
                         "gap": 0.01 + 0.001 * stock, "valuation_date": (day + pd.Timedelta(days=28)).date().isoformat(),
                         "entry_reason": None, "cash_entry": False, "delisted": False, "stale_exit": False,
                         "trading_halt": False, "limit_down_exit": False,
                         "x21": stock / 100, "x42": stock / 50, "raw21": -stock / 100, "raw42": -stock / 50,
                         **{f"cost_{m}": 0.003 * m for m in (1.0, 1.5, 2.0)},
                         **{f"eligible_{variant}": True for variant in (*r001.VARIANTS, "raw21", "raw42", "raw21_profitable")},
                         **{f"selected_{variant}": selected if not variant.startswith("raw") else not selected
                            for variant in (*r001.VARIANTS, "raw21", "raw42", "raw21_profitable")}})
    return pd.DataFrame(rows)


def judged_result(*, t=2.5, mean=0.01, stressed=0.008, count=36, control=0.05, p=None):
    return {"excess": {"count": count, "mean": mean, "t_stat": t,
                       "p_value": p if p is not None else 0.5 * math.erfc(t / math.sqrt(2)) if t is not None else 1.0},
            "cost_sensitivity": {"1.5": {"top_net_excess": stressed}},
            "random_control": {"share_of_controls": control},
            "per_rebalance": [{"decision_date": "2024-11-29", "excess_1.0": 0.02},
                              {"decision_date": "2025-01-31", "excess_1.0": -0.001}]}


def test_calendar_is_month_end_next_open_entry_inclusive_and_never_holdout():
    days = r001.hc001._session_calendar()
    planned, excluded = r001.rebalance_calendar(days)
    assert len(planned) == len(r001.MONTHS) == 118 and not excluded
    lookup = {row["month"]: row for row in planned}
    assert lookup["2016-02"]["decision_date"] == "2016-02-29"
    assert lookup["2016-02"]["entry_date"] == "2016-03-02"  # holiday, not weekday guessing
    for row in planned:
        entry = days.get_loc(pd.Timestamp(row["entry_date"]))
        assert days[entry - 1] == pd.Timestamp(row["decision_date"])
        assert days[entry + 19] == pd.Timestamp(row["exit_date"])
        assert pd.Timestamp(row["exit_date"]) < r001.CUTOFF
    # A sparse synthetic calendar may put even November's exit in 2026.
    sparse = days[(days < "2025-12-01") | (days >= "2026-01-01")]
    planned, excluded = r001.rebalance_calendar(sparse)
    assert any(row["month"] == "2025-11" and row["reason"] == "holdout_horizon" for row in excluded)
    assert not any(row["month"] == "2025-11" for row in planned)


def test_same_day_b_is_equal_weight_u_includes_self_and_unknown_uses_all_u():
    prices, days, codes = market(size=5)
    values = [-0.02, 0.01, -0.05, 0.02, 0.03]
    prices["ret_1d"] = np.tile(values, len(days))
    mapping = dict(zip(codes[:4], ["반도체", "반도체", "자동차·부품", "자동차·부품"]))
    day = pd.Timestamp("2024-01-31")
    # Zero volume and decision limit-down exclude E, but do not shrink U or b.
    prices.loc[(day, codes[1]), "volume"] = 0
    prices.loc[(day, codes[3]), "close"] = 7000
    universe = r001.universe_on_decision(prices, day, mapping, sessions=days)
    assert len(universe) == 5
    assert universe.loc[codes[0], "x21"] == pytest.approx(21 * (math.log(0.98) - math.log(0.995)))
    assert universe.loc[codes[2], "x42"] == pytest.approx(42 * (math.log(0.95) - math.log(0.985)))
    assert universe.loc[codes[4], "x21"] == pytest.approx(21 * (math.log(1.03) - math.log(1 + np.mean(values))))
    assert not universe.loc[codes[1], "eligible_r21"] and not universe.loc[codes[3], "eligible_r21"]
    # Same-day membership change must affect today's b, not tomorrow's.
    prices.loc[(day, codes[1]), "close"] = 999
    updated = r001.universe_on_decision(prices, day, mapping, sessions=days)
    assert updated.loc[codes[0], "x21"] == pytest.approx(20 * (math.log(0.98) - math.log(0.995)))


def test_daily_u_matches_common_filter_and_missing_peer_is_not_dropped():
    prices, days, codes = market(size=7)
    prices["ret_1d"] = np.tile(np.linspace(-0.02, 0.02, 7), len(days))
    prices.loc[(slice(None), codes[1]), "stock_name"] = "합성스팩"
    prices.loc[(slice(None), codes[2]), "trading_value"] = 9e7
    prices.loc[(slice(None), codes[3]), "market"] = "OTHER"
    prices.loc[(days[-1], codes[4]), "calendar_status"] = "unverified"
    prices.loc[(days[-2], codes[5]), "trading_value"] = np.nan
    prices.loc[(days[-1], codes[6]), "close"] = 999
    mapping = dict.fromkeys(codes, "반도체")
    benchmark = r001.daily_benchmarks(prices, mapping, days)
    for day in (days[18], days[19], days[-3], days[-1]):
        history = prices.loc[:day]
        universe = common.universe_filter(history, day)
        expected = universe.ret_1d.mean() if len(universe) else np.nan
        assert benchmark.loc[day, "반도체"] == pytest.approx(expected, nan_ok=True)
    prices.loc[(days[-3], codes[0]), "ret_1d"] = np.nan
    benchmark = r001.daily_benchmarks(prices, mapping, days)
    assert pd.isna(benchmark.loc[days[-3], "반도체"])
    assert pd.isna(benchmark.loc[days[-3], industry_map.UNCLASSIFIED])


@pytest.mark.parametrize("size,expected", [(9, 0), (10, 10), (19, 10), (99, 10), (100, 10), (109, 10), (110, 11), (199, 19)])
def test_selection_minimum_floor_and_code_ties(size, expected):
    prices, days, codes = market(size=size)
    # Reverse input row order to ensure ties do not depend on arrival order.
    universe = r001.universe_on_decision(prices.iloc[::-1], "2024-01-31", {}, sessions=days)
    assert universe.loc[universe.selected_r21].index.tolist() == codes[:expected]
    assert int(universe.selected_r42.sum()) == expected
    if size < 10:
        frame = decision_universe(prices, days)
        observations = r001.forward_observations(prices, frame, sessions=days)
        result = r001.portfolio_metrics(observations, "r21")
        assert result["excess"]["count"] == 0 and result["skipped_months"][0]["reason"] == "insufficient_eligible"


def test_profit_and_execution_eligibility_precede_sorting_and_size():
    prices, days, codes = market(size=160)
    prices["ret_1d"] = np.tile(np.linspace(-0.015, 0.015, 160), len(days))
    day = pd.Timestamp("2024-01-31")
    prices.loc[(day, codes[0]), "volume"] = 0
    prices.loc[(day, codes[1]), "close"] = 7000
    universe = r001.universe_on_decision(prices, day, {}, sessions=days, profitable=codes[30:140])
    assert int(universe.eligible_r21.sum()) == 158
    assert int(universe.selected_r21.sum()) == 15
    assert universe.loc[universe.selected_r21].index.tolist() == codes[2:17]
    assert int(universe.eligible_r21_profitable.sum()) == 110
    assert universe.loc[universe.selected_r21_profitable].index.tolist() == codes[30:41]


def test_signal_requires_every_return_and_cannot_use_future_prices():
    prices, days, codes = market()
    day = pd.Timestamp("2024-01-31")
    history = prices.loc[:day]
    baseline = r001.universe_on_decision(history, day, {}, sessions=days)
    changed = prices.copy()
    changed.loc[(slice(day + pd.Timedelta(days=1), None), codes[0]), ["ret_1d", "trading_value", "close", "open"]] = [-0.99, 1.0, 1.0, 1.0]
    pd.testing.assert_frame_equal(baseline, r001.universe_on_decision(changed, day, {}, sessions=days))
    earlier = days[days <= day][-30]
    changed = prices.copy()
    changed.loc[(earlier, codes[0]), "ret_1d"] = np.nan
    universe = r001.universe_on_decision(changed, day, {}, sessions=days)
    assert np.isfinite(universe.loc[codes[0], "x21"])
    assert pd.isna(universe.loc[codes[0], "x42"]) and not universe.loc[codes[0], "eligible_r42"]
    changed.loc[(days[days <= day][-5], codes[0]), "ret_1d"] = -1
    universe = r001.universe_on_decision(changed, day, {}, sessions=days)
    assert pd.isna(universe.loc[codes[0], "x21"])


def test_unadjusted_comparison_has_its_own_computable_e():
    prices, days, codes = market()
    day = pd.Timestamp("2024-01-31")
    prices.loc[(days[days <= day][-5], codes[0]), "ret_1d"] = np.nan
    universe = r001.universe_on_decision(prices, day, dict.fromkeys(codes, "반도체"), sessions=days)
    assert universe.eligible_r21.sum() == 0
    assert universe.eligible_raw21.sum() == 19 and universe.selected_raw21.sum() == 10


def test_missing_holding_market_session_and_unknown_u_member_never_shrink_sets():
    prices, days, codes = market()
    universe = decision_universe(prices, days)
    target = universe.exit_date.iloc[0]
    missing_session = prices.drop(target, level="trade_date")
    observations = r001.forward_observations(missing_session, universe, sessions=days)
    assert observations.return_reason.eq("missing_price_sessions").all()
    result = r001.portfolio_metrics(observations, "r21")
    assert result["excess"]["count"] == 0 and len(result["skipped_months"][0]["selected_codes"]) == 10
    # A missing adjusted return outside the selected ten still makes U undefined.
    entry = days.get_loc(universe.entry_date.iloc[0])
    prices.loc[(days[entry + 2], codes[-1]), "ret_1d"] = np.nan
    observations = r001.forward_observations(prices, universe, sessions=days)
    assert not observations.loc[observations.gross.isna(), "selected_r21"].any()
    assert r001.portfolio_metrics(observations, "r21")["excess"]["count"] == 0


@pytest.mark.parametrize("decision,lower,blocked", [("2015-04-30", 8500, True), ("2015-06-30", 8500, False)])
def test_exit_lower_limit_uses_the_exit_date_table(decision, lower, blocked):
    prices, days, codes = market("2015-01-02", "2015-08-31")
    universe = decision_universe(prices, days, day=decision)
    exit_day = universe.exit_date.iloc[0]
    prices.loc[(exit_day, codes[0]), "close"] = lower
    row = r001.forward_observations(prices, universe, sessions=days).set_index("stock_code").loc[codes[0]]
    assert bool(row.limit_down_exit) == blocked
    expected = days[days.get_loc(exit_day) - 1] if blocked else exit_day
    assert row.valuation_date == expected.date().isoformat()


@pytest.mark.parametrize("decision,lower,entry,opening,cash", [
    ("2015-05-29", 8500, "2015-06-01", 11500, True),
    ("2015-06-12", 8500, "2015-06-15", 11500, False),
    ("2015-06-15", 7000, "2015-06-16", 13000, True),
])
def test_date_table_applies_separately_to_decision_and_entry(decision, lower, entry, opening, cash):
    prices, days, codes = market("2015-02-02", "2015-08-31")
    code = codes[0]
    prices.loc[(pd.Timestamp(decision), code), "close"] = lower
    universe = r001.universe_on_decision(prices, decision, {}, sessions=days)
    assert universe.loc[code, "decision_limit_down"]
    # A different selected stock can encounter the entry limit without changing E.
    code = codes[1]
    selected = decision_universe(prices, days, day=decision)
    assert selected.loc[selected.stock_code.eq(code), "selected_r21"].item()
    prices.loc[(pd.Timestamp(entry), code), "open"] = opening
    row = r001.forward_observations(prices, selected, sessions=days).set_index("stock_code").loc[code]
    assert bool(row.cash_entry) == cash
    if cash:
        assert row.gross == 0 and row["cost_1.0"] == 0 and row.entry_reason == "limit_up_entry"


@pytest.mark.parametrize("policy", ["normal", "split", "halt", "delisting", "limit_down", "zero_volume_exit", "missing_return", "upper_entry", "zero_volume_entry"])
def test_fixed_slots_adjusted_returns_exits_and_costs(policy):
    prices, days, codes = market()
    prices["ret_1d"] = np.tile(np.linspace(-0.002, 0.002, len(codes)), len(days))
    selected = decision_universe(prices, days)
    entry = days.get_loc(selected.entry_date.iloc[0])
    target = entry + 19
    code = codes[0]
    prices.loc[(days[entry], code), "open"] = 10500
    actual = target
    if policy == "split":
        prices.loc[(days[target], code), ["close", "base_price"]] = [5000, 5000]
    elif policy == "halt":
        prices = prices.drop([(day, code) for day in days[entry + 10:target + 1]])
        actual = entry + 9
    elif policy == "delisting":
        prices = prices.drop([(day, code) for day in days[entry + 7:]])
        actual = entry + 6
        # A liquidation-trading lower-limit price must still enter the terminal mark.
        prices.loc[(days[actual], code), ["close", "base_price", "ret_1d"]] = [7000, 10000, -0.3]
    elif policy == "limit_down":
        prices.loc[(days[target], code), ["close", "ret_1d"]] = [7000, -0.3]
        actual = target - 1
    elif policy == "zero_volume_exit":
        prices.loc[(days[target], code), "volume"] = 0
        actual = target - 1
    elif policy == "missing_return":
        prices.loc[(days[entry + 5], code), "ret_1d"] = np.nan
    elif policy == "upper_entry":
        prices.loc[(days[entry], code), "open"] = 13000
    elif policy == "zero_volume_entry":
        prices.loc[(days[entry], code), "volume"] = 0
    frame = r001.forward_observations(prices, selected, sessions=days)
    row = frame.set_index("stock_code").loc[code]
    result = r001.portfolio_metrics(frame, "r21")
    assert selected.loc[selected.stock_code.eq(code), "selected_r21"].item()
    if policy == "missing_return":
        assert pd.isna(row.gross) and row.return_reason == "missing_adjusted_return"
        assert result["excess"]["count"] == 0
        assert code in result["skipped_months"][0]["selected_codes"]
    elif policy in ("upper_entry", "zero_volume_entry"):
        assert row.cash_entry and row.gross == 0 and row["cost_1.0"] == 0
        assert result["per_rebalance"][0]["count"] == 10 and result["per_rebalance"][0]["selected_weight"] == 0.1
        assert len(result["per_rebalance"][0]["universe_codes"]) == 20
    else:
        factors = prices.loc[(slice(days[entry + 1], days[actual]), code), "ret_1d"]
        expected = 10000 / 10500 * (1 + factors).prod() - 1
        assert row.gross == pytest.approx(expected)
        assert row.valuation_date == days[actual].date().isoformat()
        assert bool(row.delisted) == (policy == "delisting")
        assert bool(row.trading_halt) == (policy in ("halt", "zero_volume_exit"))
        assert bool(row.limit_down_exit) == (policy == "limit_down")
        for multiplier in (1.0, 1.5, 2.0):
            assert row[f"cost_{multiplier}"] == cost_model.round_trip_cost(10500.0, "KOSPI", days[entry].date(), 2e9, multiplier=multiplier)
        if policy == "delisting":
            assert result["per_rebalance"][0]["count"] == 10
            assert code in result["per_rebalance"][0]["universe_codes"]
            sensitivity = r001.portfolio_metrics(frame, "r21", worthless=True)
            assert sensitivity["per_rebalance"][0]["universe_gross"] == pytest.approx((frame.gross.sum() - row.gross - 1) / 20)
            assert sensitivity["per_rebalance"][0]["gross"] == pytest.approx((frame.loc[frame.selected_r21, "gross"].sum() - row.gross - 1) / 10)


def test_costs_use_actual_entry_market_date_and_tax_is_unscaled():
    prices, days, codes = market("2019-01-02", "2019-08-31")
    universe = decision_universe(prices, days, day="2019-05-31")
    entry = universe.entry_date.iloc[0]
    prices.loc[(entry, codes[0]), "market"] = "KOSDAQ"
    row = r001.forward_observations(prices, universe, sessions=days).set_index("stock_code").loc[codes[0]]
    for multiplier in (1.0, 1.5, 2.0):
        assert row[f"cost_{multiplier}"] == cost_model.round_trip_cost(10000.0, "KOSDAQ", entry.date(), 2e9, multiplier=multiplier)
    assert row["cost_1.5"] != pytest.approx(row["cost_1.0"] * 1.5)


def test_random_controls_are_complete_path_major_code_sorted_draws_not_month_major():
    frame = metric_frame(3).sample(frac=1, random_state=17)
    result = r001.portfolio_metrics(frame, "r21")
    actual = np.array(result["random_control"]["paths"])
    rng = np.random.default_rng(0)
    expected = np.empty((200, 3))
    groups = list(frame.groupby("decision_date", sort=True))
    for path in range(200):
        for month, (_, group) in enumerate(groups):
            ordered = group.sort_values("stock_code")
            draw = rng.choice(20, size=10, replace=False)
            assert len(set(draw)) == 10
            expected[path, month] = (ordered.gross - ordered["cost_1.0"]).to_numpy()[draw].mean() - group.gross.mean()
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(result["random_control"]["mean_excess"], expected.mean(axis=1))
    assert result["random_control"]["share_of_controls"] == np.mean(expected.mean(axis=1) >= result["excess"]["mean"])
    # E, not U, supplies the draws; U still supplies the gross benchmark.
    frame.loc[frame.stock_code.eq("000200"), "eligible_r21"] = False
    groups = [(group.loc[group.eligible_r21], 10, float(group.gross.mean())) for _, group in frame.groupby("decision_date", sort=True)]
    np.testing.assert_array_equal(r001.portfolio_metrics(frame, "r21")["random_control"]["paths"], r001.random_controls(groups))
    tied = frame.copy()
    tied["gross"] = 0.01
    tied["cost_1.0"] = 0.0
    assert r001.portfolio_metrics(tied, "r21")["random_control"]["share_of_controls"] == 1.0


def test_newey_west_bartlett_lag_one_no_correction_one_sided_normal():
    values = np.array([0.01, 0.03, -0.02, 0.04, 0.02])
    residuals = values - values.mean()
    gamma0 = residuals @ residuals / len(values)
    gamma1 = residuals[1:] @ residuals[:-1] / len(values)
    error = math.sqrt((gamma0 + 2 * 0.5 * gamma1) / len(values))
    result = r001.newey_west(values)
    assert result["standard_error"] == pytest.approx(error)
    assert result["t_stat"] == pytest.approx(values.mean() / error)
    assert result["p_value"] == pytest.approx(0.5 * math.erfc(result["t_stat"] / math.sqrt(2)))
    assert result["small_sample_correction"] is False and result["lag"] == 1 and result["kernel"] == "Bartlett"
    assert r001.newey_west(-values)["p_value"] == pytest.approx(1 - result["p_value"])
    assert r001.newey_west([0.01] * 36)["t_stat"] is None
    assert r001.newey_west([None])["p_value"] == 1
    gap = r001.newey_west(values[:3], ["2024-01", "2024-03", "2024-04"])
    residuals = values[:3] - values[:3].mean()
    assert gap["standard_error"] == pytest.approx(math.sqrt((residuals @ residuals + residuals[1] * residuals[2]) / 9))


def test_holm_family_stays_three_with_uncomputable_p_one():
    assert r001.holm_adjust([0.01, 0.03, None]) == pytest.approx([0.03, 0.06, 1])
    assert r001.holm_adjust([0.03, 0.01, 0.02]) == pytest.approx([0.04, 0.03, 0.04])
    with pytest.raises(ValueError, match="three"):
        r001.holm_adjust([0.01, 0.02])


@pytest.mark.parametrize("trial_count,threshold,verdict", [(20, 2, "pass"), (21, 3, "fail")])
def test_judge_requires_both_holm_and_trial_threshold_and_sign_is_diagnostic(fields, trial_count, threshold, verdict):
    results = {variant: judged_result() for variant in r001.VARIANTS}
    r001.judge_variants(results, fields, trial_count)
    for result in results.values():
        assert result["verdict"] == verdict
        assert result["judgement_inputs"]["required_t_stat"] == threshold
        assert result["judgement_inputs"]["validation_year_same_sign"] is False
    results = {variant: judged_result(t=2.1, p=p) for variant, p in zip(r001.VARIANTS, [0.02, 0.021, 0.8])}
    r001.judge_variants(results, fields, 3)
    assert results["r21"]["verdict"] == "fail"
    assert results["r21"]["judgement_inputs"]["holm_p_value"] == pytest.approx(0.06)


@pytest.mark.parametrize("change,verdict", [
    ({"count": 35}, "insufficient"), ({"mean": 0}, "fail"), ({"stressed": 0}, "fail"),
    ({"t": 2}, "fail"), ({"control": 0.055}, "fail"), ({"t": None}, "insufficient"),
])
def test_every_pass_gate_and_strict_boundary(fields, change, verdict):
    results = {variant: judged_result(**change) for variant in r001.VARIANTS}
    r001.judge_variants(results, fields, 3)
    assert all(result["verdict"] == verdict for result in results.values())


def test_delisting_rerun_uses_same_paths_costs_benchmark_and_holm_then_blocks_pass(fields):
    frame = metric_frame()
    frame.loc[frame.stock_code.eq("000010"), "delisted"] = True
    base = {variant: r001.portfolio_metrics(frame, variant) for variant in r001.VARIANTS}
    stress = {variant: r001.portfolio_metrics(frame, variant, worthless=True) for variant in r001.VARIANTS}
    assert base["r21"]["random_control"]["months"] == stress["r21"]["random_control"]["months"]
    adjusted = frame.copy()
    adjusted.loc[adjusted.delisted, "gross"] = -1
    oracle = r001.portfolio_metrics(adjusted, "r21")
    assert stress["r21"] == oracle
    r001.judge_variants(base, fields, 3)
    r001.judge_variants(stress, fields, 3)
    assert base["r21"]["verdict"] == "pass" and stress["r21"]["verdict"] == "fail"
    r001.apply_delisting_sensitivity(base, stress)
    assert all(result["verdict"] == "delisting_sensitive" for result in base.values())
    assert base["r21"]["delisting_sensitivity"]["base_verdict"] == "pass"
    # Also recompute U/control delistings that are outside the actual selection.
    frame["delisted"] = frame.stock_code.eq("000200")
    stress = r001.portfolio_metrics(frame, "r21", worthless=True)
    assert stress["per_rebalance"][0]["gross"] == r001.portfolio_metrics(frame, "r21")["per_rebalance"][0]["gross"]
    assert stress["per_rebalance"][0]["universe_gross"] < 0
    assert stress["random_control"]["paths"] != r001.portfolio_metrics(frame, "r21")["random_control"]["paths"]


def test_decomposition_turnover_costs_ic_strata_and_capacity_are_secondary():
    frame = metric_frame()
    result = r001.portfolio_metrics(frame, "r21")
    for row in result["per_rebalance"]:
        assert row["close_to_exit"] == pytest.approx(row["close_to_open"] + row["open_to_exit"] + row["interaction"])
        assert row["net_1.0"] == pytest.approx(row["gross"] - 0.003)  # retained names still charged
    turnover = result["turnover_based"]
    assert turnover["used_for_judgement"] is False
    assert turnover["per_rebalance"][0]["retained_fraction"] == 0
    assert turnover["per_rebalance"][1]["retained_fraction"] == 1
    assert turnover["excess"]["mean"] > result["excess"]["mean"]
    assert result["capacity"]["strategy_capacity"] == 2e8
    assert set(result["capacity_by_rebalance"].values()) == {2e8}
    assert set(result["excess"]["by_year"]) == {"2022", "2023", "2024", "2025"}
    assert result["short_sale_ban"]["inside"]["count"] + result["short_sale_ban"]["outside"]["count"] == 40
    assert result["ic"]["count"] == 40
    assert result["liquidity_buckets"]["2"]["excess"]["count"] == 40
    assert result["bundle_drawdown"] == 0
    # Retention resets across a gap; actual entry cash is never treated as held.
    gap = frame.loc[~frame.decision_date.eq(frame.decision_date.unique()[1])].copy()
    gap.loc[gap.stock_code.eq("000010"), "cash_entry"] = True
    metrics = r001.portfolio_metrics(gap, "r21")["turnover_based"]["per_rebalance"]
    assert metrics[1]["retained_fraction"] == 0 and metrics[2]["retained"] == 9


def test_latest_standalone_four_quarters_and_receipt_corrections_are_point_in_time(tmp_path):
    rows = [filing(2023, quarter, day) for quarter, day in
            [(1, "2023-05-15"), (2, "2023-08-14"), (3, "2023-11-14"), (4, "2024-03-29")]]
    archives(tmp_path, rows + [filing(2023, 4, "2024-04-30", income=-100, number=2),
                               filing(2024, 1, "2024-05-15", income=None)])
    assert r001.profitable_as_of("2024-03-29", data_dir=tmp_path) == set()
    assert r001.profitable_as_of("2024-04-30", data_dir=tmp_path) == {"000010"}
    assert r001.profitable_as_of("2024-05-01", data_dir=tmp_path) == set()
    assert r001.profitable_as_of("2024-05-16", data_dir=tmp_path) == set()
    # A receipt on the decision date and a future correction cannot affect E.
    prices, days, codes = market(end="2024-06-30")
    before = r001.profitable_as_of("2024-04-30", data_dir=tmp_path)
    universe = r001.universe_on_decision(prices, "2024-04-30", {}, sessions=days, profitable=before)
    assert universe.loc["000010", "eligible_r21_profitable"]
    assert not universe.loc["000020", "eligible_r21_profitable"]


@pytest.mark.parametrize("defect", ["gap", "division", "currency", "missing", "zero", "future_quarter"])
def test_profit_rule_never_substitutes_an_older_window_or_mixes_basis(monkeypatch, defect):
    periods = pd.period_range("2022Q4", "2023Q4", freq="Q")
    known = pd.DataFrame({"stock_code": "000010", "corp_code": "00000010", "fiscal_quarter": periods.astype(str),
                          "fs_div": "CFS", "currency": "KRW", "operating_income": 10.0})
    if defect == "gap":
        known = known.drop(2)
    elif defect == "division":
        known.loc[3, "fs_div"] = "OFS"
    elif defect == "currency":
        known.loc[3, "currency"] = "USD"
    elif defect == "missing":
        known.loc[4, "operating_income"] = np.nan
    elif defect == "zero":
        known.loc[1:, "operating_income"] = 0
    elif defect == "future_quarter":
        known = known.iloc[1:].copy()
        known.loc[4, "fiscal_quarter"] = "2025Q1"
    monkeypatch.setattr(dart_quarterly, "load_quarterly", lambda *args, **kwargs: known)
    assert r001.profitable_as_of("2024-04-30", data_dir="unused") == set()


def test_cfs_default_is_not_replaced_with_complete_ofs(tmp_path):
    rows = [filing(2023, q, f"2024-01-{q + 1:02d}", division="OFS") for q in range(1, 5)]
    rows.append(filing(2023, 4, "2024-01-10", income=None))
    archives(tmp_path, rows)
    assert r001.profitable_as_of("2024-04-30", data_dir=tmp_path) == set()


def test_runner_records_exactly_three_rows_hashes_interpretations_and_secondary_comparison(tmp_path, monkeypatch, registration):
    prices, days, codes = market("2023-10-02", "2025-12-31")
    prices["ret_1d"] = np.tile(np.linspace(-0.002, 0.002, len(codes)), len(days))
    company = profiles(tmp_path / "data", codes)
    archive_dir = archives(tmp_path / "data", [])
    # Other experiment rows do not raise R001's threshold.
    registry = tmp_path / experiment_registry.REGISTRY_PATH
    registry.parent.mkdir(parents=True)
    with registry.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(experiment_registry.REGISTRY_COLUMNS)
        for _ in range(18):
            writer.writerow(("past", r001.EXPERIMENT_ID, registration.commit_hash, "r21", "fail", "{}", ""))
        for _ in range(30):
            writer.writerow(("past", "OTHER", "other", "v", "fail", "{}", ""))
    future = archive_dir / "2026_11013.jsonl"
    future.write_text("holdout quarterly file must not be opened")
    listing = tmp_path / "data/disclosures/dart_full/list/2026/future.jsonl"
    listing.parent.mkdir(parents=True)
    listing.write_text("future disclosure must not be opened or change selection")
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if path.resolve() in (future, listing):
            raise AssertionError("future evidence was read")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(common, "judge", lambda *args, **kwargs: pytest.fail("R001 must not call common.judge"))
    result = r001.run_experiment(prices=prices, sessions=pd.bdate_range(days[0], "2026-03-31"),
                                  data_dir=tmp_path / "data", repo_root=tmp_path)
    assert result["selected_variant"] == "r21" and result["verdict"] == "insufficient"
    assert result["coverage"]["planned_months"] == 118
    assert result["variants"]["r21_profitable"]["excess"]["count"] == 0
    assert result["variants"]["r21_profitable"]["judgement_inputs"]["p_value"] == 1
    assert result["source_hashes"]["company_profiles"][str(company)] == hashlib.sha256(company.read_bytes()).hexdigest()
    assert result["source_hashes"]["krx_daily"] == {} and result["source_hashes"]["dart_listing_files"] == {}
    assert len(result["source_hashes"]["supplied_price_frame"]) == 64
    for variant in r001.VARIANTS:
        metrics = result["variants"][variant]
        assert metrics["judgement_inputs"]["trial_count_after_run"] == 21
        assert metrics["judgement_inputs"]["required_t_stat"] == 3
        assert metrics["unadjusted_comparison"]["used_for_judgement"] is False
    assert result["variants"]["r21"]["unadjusted_comparison"]["difference"]["count"] > 0
    with registry.open() as handle:
        trials = list(csv.DictReader(handle))[-3:]
    assert [row["variant"] for row in trials] == list(r001.VARIANTS)
    assert json.loads(trials[0]["metrics_json"])["holm_p_value"] == result["variants"]["r21"]["judgement_inputs"]["holm_p_value"]
    files = list((tmp_path / "research/experiments" / r001.EXPERIMENT_ID / "results").glob("*"))
    assert len(files) == 2
    assert json.loads(next(path for path in files if path.suffix == ".json").read_text()) == result
    summary = next(path for path in files if path.suffix == ".md").read_text()
    assert "NW t" in summary and "Holm p" in summary and "monthly independent investment bundle" in summary
    assert all(metric in summary for metric in r001.INTERPRETATIONS["metrics"])
    assert not (tmp_path / "research/experiments/holdout_ledger.jsonl").exists()


def test_runner_profitable_variant_uses_financial_e_and_all_quarterly_hashes(tmp_path, registration):
    prices, days, codes = market(size=20)
    prices["ret_1d"] = np.tile(np.linspace(-0.005, 0.005, len(codes)), len(days))
    profiles(tmp_path / "data", codes)
    financial_rows = [filing(2023, quarter, f"2024-01-{quarter + 1:02d}", code=code)
                      for code in codes[5:17] for quarter in range(1, 5)]
    archive_dir = archives(tmp_path / "data", financial_rows)
    result = r001.run_experiment(prices=prices, sessions=days, data_dir=tmp_path / "data", repo_root=tmp_path)
    profitable = result["variants"]["r21_profitable"]
    assert profitable["excess"]["count"] == 3
    for row in profitable["per_rebalance"]:
        assert row["eligible_codes"] == codes[5:17] and row["eligible_count"] == 12
        assert row["count"] == 10 and row["universe_count"] == 20
    assert result["source_hashes"]["quarterly_archives"] == {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in archive_dir.glob("*.jsonl")}
    expected = r001.holm_adjust([result["variants"][variant]["excess"]["p_value"] for variant in r001.VARIANTS])
    assert [result["variants"][variant]["judgement_inputs"]["holm_p_value"] for variant in r001.VARIANTS] == expected


def test_writer_reports_delisting_sensitive_without_mapping_it_to_pass(tmp_path, fields, registration):
    frame = metric_frame()
    frame.loc[frame.stock_code.eq("000010"), "delisted"] = True
    base = {variant: r001.portfolio_metrics(frame, variant) for variant in r001.VARIANTS}
    stress = {variant: r001.portfolio_metrics(frame, variant, worthless=True) for variant in r001.VARIANTS}
    r001.judge_variants(base, fields, 3)
    r001.judge_variants(stress, fields, 3)
    r001.apply_delisting_sensitivity(base, stress)
    payload = {"verdict": base["r21"]["verdict"], "reasons": base["r21"]["reasons"], "variants": base, "assumptions": r001.ASSUMPTIONS}
    json_path, summary = r001.write_results(payload, repo_root=tmp_path)
    assert json.loads(json_path.read_text())["verdict"] == "delisting_sensitive"
    assert "verdict: delisting_sensitive" in summary.read_text()


def test_local_latest_episodes_are_loaded_and_all_read_sources_are_hashed(tmp_path, monkeypatch, registration):
    prices, days, codes = market("2023-10-02", "2024-03-31", size=12)
    profiles(tmp_path / "data", codes)
    archive_dir = archives(tmp_path / "data", [filing(2023, 1, "2023-05-15")])
    price_dir = tmp_path / "data/market/krx_daily/2023"
    price_dir.mkdir(parents=True)
    source_files = []
    for day, group in prices.groupby("trade_date"):
        path = tmp_path / f"data/market/krx_daily/{day.year}/{day:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for (_, code), row in group.iterrows():
            records.append({**row.to_dict(), "trade_date": day.date().isoformat(), "stock_code": code, "change_rate_pct": 0.0})
        records.append({**records[0], "change_rate_pct": -1.0})  # latest daily episode
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        source_files.append(path)
    forbidden = tmp_path / "data/market/krx_daily/2026/20260102.jsonl"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_text("must not load holdout prices")
    loaded = d001._load_prices(tmp_path / "data/market/krx_daily", r001.PRICE_START, r001.PRICE_END)
    assert loaded.xs(codes[0], level="stock_code").ret_1d.eq(-0.01).all()
    monkeypatch.setattr(r001.hc001, "_session_calendar", lambda: pd.bdate_range(days[0], "2026-03-31"))
    result = r001.run_experiment(data_dir=tmp_path / "data", repo_root=tmp_path)
    expected = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    assert result["source_hashes"]["krx_daily"] == expected
    assert result["source_hashes"]["quarterly_archives"] == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in archive_dir.glob("*.jsonl")}
    assert "supplied_price_frame" not in result["source_hashes"]
    assert str(forbidden) not in result["source_hashes"]["krx_daily"]


def test_source_mutation_fails_before_any_trial_is_written(tmp_path, monkeypatch, registration):
    prices, days, codes = market()
    company = profiles(tmp_path / "data", codes)
    original = r001.portfolio_metrics
    def mutate(*args, **kwargs):
        company.write_text(company.read_text() + "\n")
        return original(*args, **kwargs)
    monkeypatch.setattr(r001, "portfolio_metrics", mutate)
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *args, **kwargs: pytest.fail("mutated sources recorded"))
    with pytest.raises(ValueError, match="source files changed"):
        r001.run_experiment(prices=prices, sessions=days, data_dir=tmp_path / "data", repo_root=tmp_path)


def test_holdout_is_rejected_before_signal_loading_or_trial_writes(tmp_path, monkeypatch):
    prices, days, codes = market()
    extra = prices.iloc[:1].copy()
    extra.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-02"), codes[0])], names=prices.index.names)
    profiles(tmp_path, codes)
    monkeypatch.setattr(r001, "build_universe", lambda *args, **kwargs: pytest.fail("holdout reached signals"))
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *args, **kwargs: pytest.fail("holdout recorded"))
    with pytest.raises(ValueError, match="holdout"):
        r001.run_experiment(prices=pd.concat([prices, extra]), data_dir=tmp_path)


def test_dry_run_and_default_cli_report_plan_counts_without_evaluating_or_writing(tmp_path, monkeypatch, capsys):
    profiles(tmp_path, ["000010"])
    price = tmp_path / "market/krx_daily/2025/20251230.jsonl"
    price.parent.mkdir(parents=True)
    price.write_text("coverage only; prices must not be opened")
    archive_dir = archives(tmp_path, [])
    (archive_dir / "2025_11013.jsonl").write_text("coverage only; financials must not be opened")
    (archive_dir / "2026_11013.jsonl").write_text("holdout archive must not be opened")
    def denied(*args, **kwargs):
        pytest.fail("dry-run evaluated or wrote output")
    monkeypatch.setattr(dart_quarterly, "load_quarterly", denied)
    monkeypatch.setattr(d001, "_load_prices", denied)
    monkeypatch.setattr(r001, "run_experiment", denied)
    monkeypatch.setattr(r001, "write_results", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = r001.dry_run(data_dir=tmp_path)
    assert plan["planned_months"] == plan["guarded_months"] == 118
    assert plan["price_files"] == 1 and plan["quarterly_archives"][0]["file"] == "2025_11013.jsonl"
    assert len(plan["quarterly_archives"]) == 1
    assert plan["coverage"]["r42"]["covered_months"] == []
    assert r001.main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
    assert r001.main(["--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "118" in output and "no returns evaluated" in output and "no registry rows" in output
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    called = []
    monkeypatch.setattr(r001, "run_experiment", lambda **kwargs: called.append(kwargs) or {"verdict": "insufficient", "variants": {"r21": {"excess": {"count": 0}}}})
    assert r001.main(["--execute", "--data-dir", str(tmp_path)]) == 0
    assert called == [{"data_dir": tmp_path}]


def test_missing_reference_fails_clearly_and_immutable_registration_is_required(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        industry_map.load_industry_map(tmp_path / "missing")
    with pytest.raises(ValueError, match="preregistration"):
        r001.dry_run(data_dir=tmp_path, repo_root=tmp_path)
    fields = common.load_preregistration(r001.EXPERIMENT_ID)
    bad = copy.deepcopy(fields)
    bad["variants_planned"] = 2
    monkeypatch.setattr(common, "load_preregistration", lambda *args, **kwargs: bad)
    with pytest.raises(ValueError, match="three fixed"):
        r001._registration(tmp_path)
