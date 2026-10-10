"""Synthetic, offline HC003 accounting, point-in-time and single-use tests."""
from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from backtesting import cost_model, experiment_registry, holdout
from backtesting.experiments import common, hc001, hc002, hc003, hf001
from backtesting.experiments.lh001 import __main__ as lh_cli
from backtesting.experiments.lh001.config import LH001, LH002


@pytest.fixture
def rates(monkeypatch):
    calls = []
    def one_way(price, market, day, adv, side, *, multiplier=1.0, model_version="v1"):
        calls.append((price, market, day, adv, side, multiplier, model_version))
        return (0.01 if side == "buy" else 0.02) * multiplier
    monkeypatch.setattr(cost_model, "one_way_cost", one_way)
    return calls


def quote(mark=1.0, **kwargs):
    values = dict(mark=mark, raw_open=10000.0, market="KOSPI", adv=2e9, tradable=True)
    return hc003.Quote(**{**values, **kwargs})


def trade(state, quotes, targets, **kwargs):
    return hc003.rebalance(state, quotes, targets, "2025-05-02", **kwargs)


def universe(phases):
    return pd.DataFrame({"phase": list(phases.values()), "avg_trading_value_20d": 2e9,
                         "revenue_yoy": 20.0, "score": 30.0}, index=pd.Index(phases, name="stock_code"))


def prices_for(days, codes=("000010", "000020")):
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    return pd.DataFrame({"open": 10000.0, "high": 10000.0, "low": 10000.0, "close": 10000.0,
                         "base_price": 10000.0, "ret_1d": 0.0, "volume": 1000.0,
                         "trading_value": 2e9, "market": "KOSPI", "stock_name": "보통주",
                         "calendar_status": "verified"}, index=index)


@pytest.fixture
def market():
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    return prices_for(days), days


def selection(decision, opening, ending, phases, *, month=None, kind="open", blocked=(), covered=True):
    return {"month": month or str(pd.Timestamp(decision).to_period("M")), "decision_date": decision,
            "trade_date": opening, "end_date": ending, "end_kind": kind,
            "universe": universe(phases), "blocked_buys": set(blocked), "filter_covered": covered}


def book_for(prices, days, selections, *, repo_root=experiment_registry.PROJECT_ROOT):
    return hc003._book(prices, selections, days, days[-1], repo_root)


def test_cash_conservation_sells_first_scaling_and_v2_inputs(rates):
    initial = hc003.PortfolioState(0.0, {"a": 1.0})
    state, row = trade(initial, {"a": quote(), "b": quote(), "c": quote()}, {"b", "c"})
    assert initial.to_dict() == {"cash": 0.0, "quantities": {"a": 1.0}}
    assert [fill["side"] for fill in row["trades"]] == ["sell", "buy", "buy"]
    assert state.cash == pytest.approx(0.0, abs=1e-14)
    assert state.quantities == pytest.approx({"b": 0.98 / 2 / 1.01, "c": 0.98 / 2 / 1.01})
    assert row["post_value"] + row["cost"] == pytest.approx(row["pre_value"])
    assert row["buy_scale"] == pytest.approx(0.98 / 1.01)
    assert all(call[0:4] == (10000.0, "KOSPI", pd.Timestamp("2025-05-02").date(), 2e9) for call in rates)
    assert all(call[-1] == "v2" for call in rates)


@pytest.mark.parametrize("cash,holdings,targets", [(1, {}, {"a", "b"}), (0.1, {"a": 0.9}, {"a", "b"}),
                                                   (0, {"a": 0.4, "b": 0.6}, {"a"}), (1, {}, set())])
def test_no_borrowing_and_cost_conservation(rates, cash, holdings, targets):
    state, row = trade(hc003.PortfolioState(cash, holdings), {"a": quote(), "b": quote()}, targets)
    assert state.cash >= 0
    assert row["post_value"] == pytest.approx(row["pre_value"] - row["cost"])
    assert row["buy_amount"] + row["buy_cost"] <= cash + row["sell_amount"] - row["sell_cost"] + 1e-12


@pytest.mark.parametrize("delta,expected", [(0.0009, 0), (0.0011, 2)])
def test_band_reductions_and_additions(rates, delta, expected):
    initial = hc003.PortfolioState(0, {"a": 0.5 + delta, "b": 0.5 - delta})
    _, row = trade(initial, {"a": quote(), "b": quote()}, {"a", "b"})
    assert len(row["trades"]) == expected


def test_band_applied_once_before_cash_scaling_and_full_exit_exempt(rates):
    state, row = trade(hc003.PortfolioState(0.001005, {"a": 0.998995}), {"a": quote()}, {"a"})
    assert 0 < row["trades"][-1]["amount"] < row["pre_value"] * 0.001
    assert state.cash == pytest.approx(0, abs=1e-14)
    _, row = trade(hc003.PortfolioState(0.9995, {"a": 0.0005}), {"a": quote()}, set())
    assert row["trades"][0]["full_exit"] and row["sell_amount"] == 0.0005


@pytest.mark.parametrize("in_H", [True, False])
@pytest.mark.parametrize("reason", ["missing", "volume", "down"])
def test_frozen_set_fixed_before_targets_and_never_traded(rates, in_H, reason):
    q = quote(tradable=reason == "down", limit_down=reason == "down")
    targets = {"b", "c", *({"a"} if in_H else set())}
    state, row = trade(hc003.PortfolioState(0.4, {"a": 0.6}), {"a": q, "b": quote(), "c": quote()}, targets)
    assert row["Z"] == ["a"] and row["T"] == ["b", "c"]
    assert row["W"] == pytest.approx(0.4) and row["target_amount"] == pytest.approx(0.2)
    assert state.quantities["a"] == 0.6 and all(fill["stock_code"] != "a" for fill in row["trades"])
    assert row["frozen_weight"] == pytest.approx(0.6)


def test_zero_targets_reserve_frozen_value_and_keep_cash(rates):
    state, row = trade(hc003.PortfolioState(0.4, {"a": 0.6}), {"a": quote(tradable=False)}, set())
    assert row["n"] == 0 and row["W"] == 0.4 and not row["trades"]
    assert state.cash == 0.4 and state.quantities == {"a": 0.6}


def test_limit_up_new_removed_from_denominator_existing_can_reduce_but_not_add(rates):
    upper = quote(limit_up=True)
    _, row = trade(hc003.PortfolioState(), {"a": upper, "b": quote()}, {"a", "b"})
    assert row["T"] == ["b"] and row["n"] == 1
    state, row = trade(hc003.PortfolioState(0.1, {"a": 0.9}), {"a": upper, "b": quote()}, {"a", "b"})
    assert row["T"] == ["a", "b"] and row["sell_amount"] == pytest.approx(0.4)
    assert state.quantities["a"] == pytest.approx(0.5)
    _, row = trade(hc003.PortfolioState(0.9, {"a": 0.1}), {"a": upper}, {"a"})
    assert row["n"] == 1 and not row["trades"]


def test_hysteresis_uses_only_held_computable_U_in_phases_1_2_3():
    u = universe({"a": 2, "b": 1, "c": 3, "d": 4, "e": None, "f": 3})
    assert hc003.target_set("t_hold", u, {"b", "c"}) == {"a"}
    assert hc003.target_set("t_hyst", u, {"b", "c", "d", "e", "gone"}) == {"a", "b", "c"}
    assert "f" not in hc003.target_set("t_hyst", u, {"b", "c"})


def test_filter_blocks_new_and_additional_buys_without_forced_sales(rates):
    state, row = trade(hc003.PortfolioState(0.9, {"a": 0.1}), {"a": quote(), "b": quote()},
                       {"a", "b"}, blocked_buys={"a", "b"})
    assert state.quantities == {"a": 0.1} and state.cash == 0.9 and not row["trades"]
    state, row = trade(hc003.PortfolioState(0.1, {"a": 0.9}), {"a": quote(), "b": quote()},
                       {"a", "b"}, blocked_buys={"a", "b"})
    assert row["trades"][0]["side"] == "sell" and state.quantities["a"] == pytest.approx(0.5)


@pytest.mark.parametrize("day,ratio,expected", [("2015-06-12", 1.145, True), ("2015-06-15", 1.145, False),
                                               ("2015-06-15", 1.295, True)])
def test_dated_limit_table(day, ratio, expected):
    days = pd.bdate_range("2015-05-01", "2015-06-16")
    prices = prices_for(days, ("a",))
    prices.loc[(pd.Timestamp(day), "a"), "open"] = 10000 * ratio
    book = hc003.PriceBook(prices, days, [day], days[-1])
    assert book.quotes(day)["a"].limit_up is expected


@pytest.mark.parametrize("side,open_price", [("up", 12950), ("down", 7050)])
@pytest.mark.parametrize("stored_base", [10000.0, np.nan])
def test_base_price_first_and_previous_session_fallback(market, side, open_price, stored_base):
    prices, days = market
    day = pd.Timestamp("2025-10-01")
    prices.loc[(day, "000010"), ["open", "base_price"]] = [open_price, stored_base]
    if np.isfinite(stored_base):
        previous = days[days.get_loc(day) - 1]
        prices.loc[(previous, "000010"), "close"] = 20000  # must not override stored base.
    book = hc003.PriceBook(prices, days, [day], days[-1])
    q = book.quotes(day)["000010"]
    assert getattr(q, "limit_" + side)


def test_missing_both_base_and_previous_session_close_is_untradable_and_unchanged(market):
    prices, days = market
    day = pd.Timestamp("2025-10-01")
    previous = days[days.get_loc(day) - 1]
    prices.loc[(previous, "000010"), "close"] = np.nan
    prices.loc[(day, "000010"), ["base_price", "ret_1d"]] = [np.nan, 0.5]
    book = hc003.PriceBook(prices, days, [day], days[-1])
    assert not book.quotes(day)["000010"].tradable
    assert book.quotes(day)["000010"].mark == 1
    assert book.counts["missing_base"] == 1


def test_adjusted_chain_fallback_counts_and_missing_close(market):
    prices, days = market
    day = pd.Timestamp("2025-10-01")
    next_day = days[days.get_loc(day) + 1]
    prices.loc[(day, "000010"), ["close", "open", "ret_1d"]] = [11000, 10500, 0.2]
    prices.loc[(next_day, "000010"), ["close", "base_price", "ret_1d"]] = [12100, 11000, np.nan]
    later = days[days.get_loc(next_day) + 1]
    prices.loc[(later, "000010"), ["close", "ret_1d"]] = [np.nan, 0.5]
    book = hc003.PriceBook(prices, days, [day, next_day, later], days[-1])
    assert book.quotes(day)["000010"].mark == pytest.approx(1.2 * 10500 / 11000)
    assert book.closings["000010"] == pytest.approx(1.32)
    assert book.counts["unadjusted_fallback"] == 1
    assert not book.quotes(later)["000010"].tradable and book.quotes(later)["000010"].mark == pytest.approx(1.32)


@pytest.mark.parametrize("column,value", [("open", np.nan), ("volume", 0.0), ("trading_value", np.nan), ("market", "UNKNOWN")])
def test_missing_execution_inputs_prevent_trade(market, column, value):
    prices, days = market
    day = pd.Timestamp("2025-10-01")
    affected = days[days.get_loc(day) - 1] if column == "trading_value" else day
    prices[column] = prices[column].astype(object) if column == "market" else prices[column]
    prices.loc[(affected, "000010"), column] = value
    book = hc003.PriceBook(prices, days, [day], days[-1])
    assert not book.quotes(day)["000010"].tradable


def test_first_month_from_cash_and_cost_not_charged_to_previous_month(market, rates):
    prices, days = market
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            selection("2025-09-30", "2025-10-01", "2025-12-30", {"000010": 4, "000020": 2}, kind="close")]
    book = book_for(prices, days, rows)
    run = hc003.simulate(book, rows, "t_hold")
    first, last = run["per_month"]
    assert first["pre_value"] == 1 and first["buy_amount"] == pytest.approx(1 / 1.01)
    assert first["return"] == pytest.approx(1 / 1.01 - 1)
    assert last["pre_value"] == pytest.approx(first["end_value"])
    assert last["return"] == pytest.approx(0.98 / 1.01 - 1)
    assert run["state"]["quantities"] == pytest.approx({"000020": (1 / 1.01) * 0.98 / 1.01})


def test_terminal_month_uses_close_no_liquidation_and_benchmark_full_U(market, rates):
    prices, days = market
    prices.loc[(days[-1], "000010"), ["open", "close", "ret_1d"]] = [10000, 12000, 0.2]
    rows = [selection("2025-11-28", "2025-12-01", "2025-12-30", {"000010": 2, "000020": 4}, kind="close")]
    run = hc003.simulate(book_for(prices, days, rows), rows, "t_hold")
    row = run["per_month"][0]
    assert row["return"] == pytest.approx(1.2 / 1.01 - 1)
    assert row["benchmark"] == pytest.approx(0.1)
    assert row["sell_cost"] == 0 and [call[4] for call in rates] == ["buy"]
    assert run["state"]["quantities"]["000010"] > 0


def test_empty_H_is_valid_cash_month_and_filter_coverage_is_not_fabricated(market, rates):
    prices, days = market
    rows = [selection("2025-11-28", "2025-12-01", "2025-12-30", {"000010": 4}, kind="close")]
    book = book_for(prices, days, rows)
    row = hc003.simulate(book, rows, "t_hold")["per_month"][0]
    assert row["valid"] and row["return"] == row["cost"] == 0 and row["holdings_count"] == 0
    rows[0]["filter_covered"] = False
    rows[0]["universe"] = universe({"000010": 2})
    row = hc003.simulate(book, rows, "t_filter")["per_month"][0]
    assert not row["valid"] and row["excess"] is None and row["cost"] == 0


def test_vanished_holdings_no_retroactive_sales_and_zero_only_at_next_rebalance(market, rates):
    prices, days = market
    last = pd.Timestamp("2025-09-15")
    prices = prices.loc[~((prices.index.get_level_values("stock_code") == "000010") &
                          (prices.index.get_level_values("trade_date") > last))]
    prices.loc[(last, "000010"), ["close", "ret_1d"]] = [12000, 0.2]
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            selection("2025-09-30", "2025-10-01", "2025-11-03", {"000020": 4}),
            selection("2025-10-31", "2025-11-03", "2025-12-30", {"000020": 4}, kind="close")]
    book = book_for(prices, days, rows)
    main = hc003.simulate(book, rows, "t_hold")
    loss = hc003.simulate(book, rows, "t_hold", loss=True)
    assert book.zero_from["000010"] == pd.Timestamp("2025-10-01")
    assert main["terminal_value"] == pytest.approx(1.2 / 1.01) and loss["terminal_value"] == 0
    assert main["state"]["quantities"] == loss["state"]["quantities"]
    assert all(not row["trades"] and row["Z"] == ["000010"] for row in main["per_month"][1:])
    assert main["per_month"][0]["benchmark"] == pytest.approx(0.1)
    assert loss["per_month"][0]["benchmark"] == pytest.approx(-0.5)
    assert loss["per_month"][0]["return"] == -1


def test_halted_stock_resumes_without_future_forced_sale(market, rates):
    prices, days = market
    missing = (prices.index.get_level_values("stock_code") == "000010") & (
        (prices.index.get_level_values("trade_date") >= "2025-09-15") & (prices.index.get_level_values("trade_date") < "2025-11-03"))
    prices = prices.loc[~missing]
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2}),
            selection("2025-09-30", "2025-10-01", "2025-11-03", {"000020": 4}),
            selection("2025-10-31", "2025-11-03", "2025-12-01", {"000020": 4}),
            selection("2025-11-28", "2025-12-01", "2025-12-30", {"000020": 4}, kind="close")]
    book = book_for(prices, days, rows)
    main = hc003.simulate(book, rows, "t_hold")
    loss = hc003.simulate(book, rows, "t_hold", loss=True)
    assert "000010" not in book.zero_from and main == loss
    assert not main["per_month"][1]["trades"] and not main["per_month"][2]["trades"]
    assert main["per_month"][3]["trades"][0]["side"] == "sell"


def test_last_trade_after_last_rebalance_never_zeroed_at_terminal_close(market):
    prices, days = market
    prices = prices.loc[~((prices.index.get_level_values("stock_code") == "000010") &
                          (prices.index.get_level_values("trade_date") > "2025-12-15"))]
    rows = [selection("2025-11-28", "2025-12-01", "2025-12-30", {"000010": 2}, kind="close")]
    book = book_for(prices, days, rows)
    assert "000010" not in book.zero_from
    assert book.marks("2025-12-30", "close", loss=True)["000010"] == 1


def test_control_candidates_retention_counts_no_previous_refills_and_cash_slots(rates):
    class Rng:
        def __init__(self): self.calls = []
        def choice(self, candidates, size, replace):
            self.calls.append((candidates, size, replace))
            return np.array(candidates[:size])
    rng = Rng()
    selected = hc003.random_target(["f", "d", "c", "b", "a"], {"a", "b", "gone"}, 4, 1, rng)
    assert selected == {"a", "c", "d", "f"}
    assert rng.calls == [(["a", "b"], 1, False), (["c", "d", "f"], 3, False)]
    selected = hc003.random_target(["a", "b"], {"a", "b"}, 4, 3, rng)
    assert selected == {"a", "b"}
    state, row = trade(hc003.PortfolioState(), {"a": quote(), "b": quote()}, selected, allocation_n=4)
    assert row["n"] == 4 and row["target_amount"] == 0.25
    assert state.cash == pytest.approx(1 - 0.5 * 1.01)


def test_controls_seed_paths_same_accounting_and_report_turnover(market, rates, monkeypatch):
    prices, days = market
    monkeypatch.setattr(hc003, "N_CONTROLS", 4)
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            selection("2025-09-30", "2025-10-01", "2025-12-30", {"000010": 2, "000020": 4}, kind="close")]
    book = book_for(prices, days, rows)
    actual = hc003.simulate(book, rows, "t_hold")
    first = hc003.control_metrics(book, rows, "t_hold", actual)
    second = hc003.control_metrics(book, rows, "t_hold", actual)
    assert first == second and first["seed"] == 0 and len(first["path_means"]) == 4
    assert all(value == pytest.approx(first["path_means"][0]) for value in first["path_means"])
    assert first["per_month"][0]["strategy"]["cost_fraction"] == actual["per_month"][0]["cost_fraction"]
    assert first["per_month"][1]["control_mean"]["sell_turnover"] == 0
    assert first["per_month"][1]["control_mean"]["buy_turnover"] == 0


@pytest.mark.parametrize("n", [2, 4, 36, 103])
def test_nw_lag3_against_independent_bartlett_matrix_and_normal_reference(n):
    values = np.random.default_rng(99).normal(0.01, 0.03, n)
    centered = values - values.mean()
    distance = abs(np.arange(n)[:, None] - np.arange(n)[None, :])
    kernel = np.maximum(0, 1 - distance / 4)
    reference_variance = float(centered @ kernel @ centered / n**2)
    stat = hc003.newey_west(values)
    assert stat["standard_error"] == pytest.approx(math.sqrt(reference_variance), rel=1e-12)
    assert stat["t_stat"] == pytest.approx(values.mean() / math.sqrt(reference_variance))
    assert stat["p_one_sided"] == pytest.approx(norm.sf(stat["t_stat"]))
    assert stat["small_sample_correction"] is False and stat["lag"] == 3


@pytest.mark.parametrize("values", [[], [0.1], [0.1] * 36])
def test_undefined_standard_error_has_p_one(values):
    stat = hc003.newey_west(values)
    assert stat["t_stat"] is None and stat["p_one_sided"] == 1


def result_for(t=4.0, count=36, mean=0.01, p=1e-5, mean15=0.005, share=0.05):
    return {"primary": {**hc003.newey_west([]), "count": count, "mean": mean, "standard_error": 0.01 / t,
                        "t_stat": t, "p_one_sided": p},
            "cost_sensitivity": {"1.5": {"primary": {"mean": mean15}}},
            "random_control": {"share_of_controls": share}, "design": {"primary": {"mean": mean}}}


@pytest.fixture
def fields():
    return experiment_registry.verify_preregistration(hc003.EXPERIMENT_ID).fields


def test_holm_three_fixed_family_and_insufficient_enters_as_one(fields):
    assert hc003.holm(dict(zip(hc003.VARIANTS, [0.01, 0.04, 0.03]))) == pytest.approx(
        {"t_hold": 0.03, "t_hyst": 0.06, "t_filter": 0.06})
    results = {name: result_for() for name in hc003.VARIANTS}
    results["t_filter"] = result_for(count=35, p=0)
    results = hc003.judge_variants(results, fields, 3)
    assert results["t_filter"]["verdict"] == "insufficient"
    assert results["t_filter"]["holm_p"] == results["t_filter"]["judgement_inputs"]["p_for_holm"] == 1
    assert results["t_hold"]["holm_p"] == pytest.approx(3e-5)
    with pytest.raises(ValueError, match="three"):
        hc003.holm({"t_hold": 0.01})


@pytest.mark.parametrize("trials,t,verdict", [(20, 2, "fail"), (20, 2.1, "pass"), (21, 3, "fail"), (21, 3.01, "pass")])
def test_and_t_threshold_includes_this_batch(fields, trials, t, verdict):
    results = hc003.judge_variants({name: result_for(t=t) for name in hc003.VARIANTS}, fields, trials)
    assert all(row["verdict"] == verdict for row in results.values())
    assert results["t_hold"]["judgement_inputs"]["trial_count_after_run"] == trials


@pytest.mark.parametrize("change", [{"mean": 0}, {"mean15": 0}, {"share": 0.05001}, {"p": 0.02}])
def test_all_design_criteria_are_required(fields, change):
    results = hc003.judge_variants({name: result_for(**change) for name in hc003.VARIANTS}, fields, 3)
    assert all(row["verdict"] == "fail" for row in results.values())


def test_delisting_sensitive_never_passes():
    main = {name: {"verdict": "pass", "reasons": []} for name in hc003.VARIANTS}
    loss = copy.deepcopy(main)
    loss["t_filter"]["verdict"] = "fail"
    hc003.apply_delisting_verdict(main, loss)
    assert main["t_filter"]["verdict"] == "delisting_sensitive" and main["t_hold"]["verdict"] == "pass"


@pytest.mark.parametrize("effect,mean,count,verdict", [(0.02, 0.01, 6, "pass"), (0.02, 0.0099, 6, "fail"),
    (0.02, -0.01, 6, "fail"), (0.02, 0, 6, "fail"), (0.02, 0.02, 5, "insufficient"), (-0.01, 0.01, 6, "insufficient")])
def test_holdout_criteria_use_design_period_not_pooled_effect(fields, effect, mean, count, verdict):
    assert hc003.holdout_verdict({"primary": {"mean": mean, "count": count}}, effect, fields)[0] == verdict


def test_hashed_state_detects_tampering():
    state = {"t_hold": hc003.PortfolioState().to_dict()}
    digest = hc003._content_hash(state)
    state["t_hold"]["cash"] = 0.999
    assert hc003._content_hash(state) != digest
    with pytest.raises(ValueError, match="state"):
        hc003.PortfolioState.from_dict({"cash": -0.1, "quantities": {}})


def write_financials(data_dir, codes=("000010", "000020")):
    directory = data_dir / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True, exist_ok=True)
    for year in (2024, 2025):
        rows = []
        for number, code in enumerate(codes):
            revenue = 100 if year == 2024 else 150
            income = 10 if year == 2024 else 30 if number == 0 else 12
            rows.append({"corp_code": f"{number + 1:08d}", "stock_codes": [code], "bsns_year": year,
                         "reprt_code": "11013", "fiscal_quarter": f"{year}Q1", "fs_div": "CFS",
                         "rcept_no": f"{year}0515000001", "available_date": f"{year}-05-15", "currency": "KRW",
                         "raw_accounts": {name: {"thstrm_amount": str(value), "currency": "KRW"}
                                          for name, value in (("revenue", revenue), ("operating_income", income), ("net_income", income))}})
        (directory / f"{year}_11013.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def offline_repo(tmp_path, monkeypatch):
    registration = experiment_registry.verify_preregistration(hc003.EXPERIMENT_ID)
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a, **k: registration)
    monkeypatch.setattr(holdout, "verify_preregistration", lambda *a, **k: registration)
    destination = tmp_path / "research/experiments" / hc003.EXPERIMENT_ID / "preregistration.md"
    destination.parent.mkdir(parents=True)
    source = experiment_registry.PROJECT_ROOT / "research/experiments" / hc003.EXPERIMENT_ID / "preregistration.md"
    destination.write_bytes(source.read_bytes())
    write_financials(tmp_path / "data")
    return tmp_path


def record(repo, variant="t_hold", verdict="pass", metrics=None):
    experiment_registry.record_trial(hc003.EXPERIMENT_ID, variant, metrics or {}, verdict, repo_root=repo)


def test_exact_hc002_universe_and_imported_hf001_filters(market, offline_repo, monkeypatch):
    prices, days = market
    planned = [selection("2025-09-30", "2025-10-01", "2025-12-30", {}, kind="close")]
    events, _ = hf001.rights_events(pd.DataFrame(columns=hf001.LISTING_COLUMNS), days)
    calls = []
    vol, coverage = hf001.volatility_filter, hf001.listing_coverage
    def track_vol(*args):
        calls.append("vol")
        return vol(*args)
    def track_coverage(*args):
        calls.append("coverage")
        return coverage(*args)
    monkeypatch.setattr(hf001, "volatility_filter", track_vol)
    monkeypatch.setattr(hf001, "listing_coverage", track_coverage)
    selected, counts = hc003.build_selections(prices, planned, data_dir=offline_repo / "data", repo_root=offline_repo,
                                             sessions=days, files={}, events=events)
    history = prices.loc[prices.index.get_level_values("trade_date").isin(days[days <= "2025-09-30"][-20:])]
    expected, _ = hc002.universe_on_decision(history, "2025-09-30", data_dir=offline_repo / "data", repo_root=offline_repo)
    pd.testing.assert_frame_equal(selected[0]["universe"], expected.sort_index())
    assert calls == ["vol", "coverage"] and counts[0]["price_eligible"] == 2
    assert hc003.target_set("t_hold", expected, ()) == {"000010"}


def test_financial_receipt_strictly_before_decision_and_latest_missing_not_backfilled(market, offline_repo):
    prices, days = market
    data_dir = offline_repo / "data"
    path = data_dir / "fundamentals/dart_quarterly/2025_11013.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    correction = copy.deepcopy(records[0])
    correction.update(rcept_no="20250930000002", available_date="2025-09-30")
    correction["raw_accounts"]["operating_income"]["thstrm_amount"] = "5"
    path.write_text(path.read_text() + json.dumps(correction) + "\n")
    history = prices.loc[prices.index.get_level_values("trade_date") <= "2025-09-30"]
    before, _ = hc002.universe_on_decision(history, "2025-09-30", data_dir=data_dir, repo_root=offline_repo)
    after, _ = hc002.universe_on_decision(prices, "2025-10-01", data_dir=data_dir, repo_root=offline_repo)
    assert before.loc["000010", "phase"] == 2 and after.loc["000010", "phase"] == 3
    latest = copy.deepcopy(correction)
    latest.update(reprt_code="11012", fiscal_quarter="2025Q2", rcept_no="20251002000001", available_date="2025-10-02")
    (path.parent / "2025_11012.jsonl").write_text(json.dumps(latest) + "\n")
    current, counts = hc002.universe_on_decision(prices, "2025-10-10", data_dir=data_dir, repo_root=offline_repo)
    assert "000010" not in current.index and counts["no_data"] == 1


def test_future_prices_do_not_affect_U_or_filter(market, offline_repo):
    prices, days = market
    row = selection("2025-09-30", "2025-10-01", "2025-12-30", {}, kind="close")
    events, _ = hf001.rights_events(pd.DataFrame(columns=hf001.LISTING_COLUMNS), days)
    kwargs = dict(data_dir=offline_repo / "data", repo_root=offline_repo, sessions=days, files={}, events=events)
    before, _ = hc003.build_selections(prices, [row], **kwargs)
    future = prices.index.get_level_values("trade_date") > "2025-09-30"
    prices.loc[future, ["close", "trading_value", "ret_1d"]] = [1.0, 1.0, 9.0]
    after, _ = hc003.build_selections(prices, [row], **kwargs)
    pd.testing.assert_frame_equal(before[0]["universe"], after[0]["universe"])
    assert before[0]["blocked_buys"] == after[0]["blocked_buys"]


def test_holdout_id_forwarding_nested_common_guards_and_context_cleanup(offline_repo):
    days = pd.bdate_range("2026-01-02", periods=22)
    prices = prices_for(days)
    with pytest.raises(ValueError, match="experiment_id"):
        common.guard_prices(prices, repo_root=offline_repo)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()
    with holdout.HoldoutSession(hc003.EXPERIMENT_ID, repo_root=offline_repo):
        common.guard_prices(prices, experiment_id=hc003.EXPERIMENT_ID, repo_root=offline_repo)
        with common.guarded_reads(hc003.EXPERIMENT_ID):
            assert len(common.universe_filter(prices, days[-1], repo_root=offline_repo)) == 2
            with pytest.raises(RuntimeError):
                with common.guarded_reads("other"):
                    raise RuntimeError("restore parent scope")
            common.guard_prices(prices, repo_root=offline_repo)
    with pytest.raises(ValueError, match="experiment_id"):
        common.guard_prices(prices, repo_root=offline_repo)
    assert len((offline_repo / holdout.LEDGER_PATH).read_text().splitlines()) == 1


def test_guarded_loaders_refuse_before_contents_read(offline_repo, monkeypatch):
    monkeypatch.setattr(hc003.krx_market, "load_prices", lambda *a, **k: pytest.fail("unprotected source read"))
    with pytest.raises(ValueError, match="experiment_id"):
        hc003.load_prices(offline_repo / "data", "20260101", "20260630", repo_root=offline_repo)
    with pytest.raises(ValueError, match="experiment_id"):
        hc003.listing_files(offline_repo / "data", "20260531", repo_root=offline_repo)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


def test_design_runner_records_three_trials_hashed_november_state_all_metrics(market, offline_repo, monkeypatch):
    prices, days = market
    monkeypatch.setattr(hc003, "N_CONTROLS", 3)
    for _ in range(18):
        record(offline_repo, verdict="insufficient")
    result = hc003.run_experiment(prices=prices, sessions=days, data_dir=offline_repo / "data", repo_root=offline_repo)
    assert result["stage"] == "design_validation" and result["state_decision_month"] == "2025-11"
    assert result["state_sha256"] == hc003._content_hash(result["design_states"])
    assert result["selected_variant"] == "t_hold" and set(result["variants"]) == set(hc003.VARIANTS)
    assert result["counts"]["financial_signal_missing_by_year"]["2025"]["price_eligible_stock_months"] > 0
    assert result["source_hashes"]["in_memory_prices"]
    for name, variant in result["variants"].items():
        assert variant["judgement_inputs"]["trial_count_after_run"] == 21
        assert variant["judgement_inputs"]["t_threshold"] == 3
        assert set(variant["cost_sensitivity"]) == {"1.0", "1.5", "2.0"}
        assert variant["cost_model_v1"]["used_for_judgement"] is False
        assert set(variant["liquidity_buckets"]) == {"0", "1", "2", "3"}
        assert "actual_trade_limits" in variant["capacity"] and "hc002_full_turnover" in variant
        assert sum(row["primary"]["count"] for row in variant["short_sale_regimes"].values()) == variant["primary"]["count"]
        assert set(result["design_states"][name]["scenarios"]) == set(hc003.SCENARIOS)
    rows = hc003.registry_rows(offline_repo)
    assert [row["variant"] for row in rows[-3:]] == list(hc003.VARIANTS)
    assert all(json.loads(row["metrics_json"])["stage"] == "design_validation" for row in rows[-3:])
    directory = offline_repo / "research/experiments" / hc003.EXPERIMENT_ID / "results"
    assert len(list(directory.iterdir())) == 2
    assert json.loads(next(directory.glob("*.json")).read_text()) == result
    assert "Control buy/sell turnover" in next(directory.glob("*.md")).read_text()
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


def test_design_guard_precedes_selection_no_holdout_claim(market, offline_repo, monkeypatch):
    prices, _ = market
    future = prices.iloc[:1].copy()
    future.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-02"), "000010")], names=prices.index.names)
    monkeypatch.setattr(hc003, "_prepare", lambda *a, **k: pytest.fail("selection preceded guard"))
    with pytest.raises(ValueError, match="experiment_id"):
        hc003.run_experiment(prices=future, data_dir=offline_repo / "data", repo_root=offline_repo)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


def test_holdout_refuses_without_pass_before_verification_or_session(offline_repo, monkeypatch):
    monkeypatch.setattr(hc003, "verify_non_holdout", lambda *a, **k: pytest.fail("verification opened"))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("holdout opened"))
    with pytest.raises(ValueError, match="pass"):
        hc003.run_holdout(data_dir=offline_repo / "data", repo_root=offline_repo)


def test_lh002_ledger_contamination_recorded_without_reading_any_returns(offline_repo, monkeypatch):
    record(offline_repo)
    with holdout.HoldoutSession(LH002.experiment_id, repo_root=offline_repo):
        pass
    monkeypatch.setattr(hc003, "_saved_design", lambda *a, **k: pytest.fail("design returns read"))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("HC003 holdout opened"))
    result = hc003.run_holdout(data_dir=offline_repo / "data", repo_root=offline_repo)
    assert result["verdict"] == "holdout_contaminated"
    assert hc003.registry_rows(offline_repo)[-1]["verdict"] == "holdout_contaminated"
    assert hc003.ledger_experiments(offline_repo) == {LH002.experiment_id}


@pytest.mark.parametrize("terminal", ["holdout_t_hold", "holdout_contaminated", "holdout_aborted"])
def test_terminal_holdout_rows_never_reopen(offline_repo, monkeypatch, terminal):
    record(offline_repo)
    record(offline_repo, terminal, "fail" if terminal == "holdout_t_hold" else terminal)
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("reopen attempted"))
    with pytest.raises(ValueError, match="cannot reopen"):
        hc003.run_holdout(data_dir=offline_repo / "data", repo_root=offline_repo)


def saved_design_fixture(repo):
    checkpoint = {key: {"state": hc003.PortfolioState().to_dict(), "terminal_value": 1.0} for key in hc003.SCENARIOS}
    controls = {"states": [hc003.PortfolioState().to_dict() for _ in range(hc003.N_CONTROLS)],
                "terminal_values": [1.0] * hc003.N_CONTROLS, "rng_state": np.random.default_rng(0).bit_generator.state}
    states = {name: {"scenarios": copy.deepcopy(checkpoint), "controls": copy.deepcopy(controls)} for name in hc003.VARIANTS}
    registration = experiment_registry.verify_preregistration(hc003.EXPERIMENT_ID, repo)
    return {"experiment_id": hc003.EXPERIMENT_ID, "stage": "design_validation", "prereg_commit": registration.commit_hash,
            "design_states": states, "state_sha256": hc003._content_hash(states), "state_decision_month": "2025-11",
            "state_valuation_date": "2025-12-30", "source_hashes": {},
            "variants": {name: {"verdict": "pass" if name == "t_hold" else "fail",
                                 "design": {"primary": {"mean": 0.01}}} for name in hc003.VARIANTS}}


def publish_design_fixture(repo, design):
    path = repo / "research/experiments" / hc003.EXPERIMENT_ID / "results/design.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(design))
    record(repo, metrics={"stage": "design_validation", "result_file": str(path.relative_to(repo)),
                         "result_sha256": hc003._digest(path), "state_sha256": design["state_sha256"]})
    return path


def test_saved_design_hash_and_pass_must_match_registered_bundle(offline_repo, monkeypatch):
    monkeypatch.setattr(hc003, "N_CONTROLS", 2)
    design = saved_design_fixture(offline_repo)
    path = publish_design_fixture(offline_repo, design)
    registration = experiment_registry.verify_preregistration(hc003.EXPERIMENT_ID, offline_repo)
    loaded, passed = hc003._saved_design(hc003.registry_rows(offline_repo), offline_repo, registration)
    assert loaded == design and passed == ["t_hold"]
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="hash"):
        hc003._saved_design(hc003.registry_rows(offline_repo), offline_repo, registration)


@pytest.mark.parametrize("break_sources,break_replay", [(True, False), (False, True), (False, False)])
def test_non_holdout_replay_verifies_sources_and_state_before_session(market, offline_repo, monkeypatch, break_sources, break_replay):
    prices, days = market
    monkeypatch.setattr(hc003, "N_CONTROLS", 2)
    design = saved_design_fixture(offline_repo)
    monkeypatch.setattr(hc003, "load_prices", lambda *a, **k: (prices, []))
    monkeypatch.setattr(hc003, "source_hashes", lambda *a, **k: {"changed": 1} if break_sources else {})
    state = copy.deepcopy(design["design_states"]["t_hold"])
    if break_replay:
        state["scenarios"]["v2_1.0"]["state"]["cash"] = 0.9
    monkeypatch.setattr(hc003, "strategy_metrics", lambda *a, **k: {
        "checkpoint": state["scenarios"], "random_control": {"checkpoint": state["controls"]}})
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("preflight opened holdout"))
    if break_sources or break_replay:
        with pytest.raises(ValueError, match="sources|replay"):
            hc003.verify_non_holdout(design, ["t_hold"], data_dir=offline_repo / "data", repo_root=offline_repo, sessions=days)
    else:
        assert hc003.verify_non_holdout(design, ["t_hold"], data_dir=offline_repo / "data", repo_root=offline_repo, sessions=days) is prices


@pytest.mark.parametrize("abort", [False, True])
def test_holdout_single_session_after_preflight_id_every_read_and_aborted_never_reopens(offline_repo, monkeypatch, abort):
    monkeypatch.setattr(hc003, "N_CONTROLS", 2)
    design = saved_design_fixture(offline_repo)
    publish_design_fixture(offline_repo, design)
    sessions = hc003.session_calendar()
    history = prices_for(sessions[(sessions >= "2025-08-01") & (sessions <= "2025-12-30")])
    future = prices_for(sessions[(sessions >= "2026-01-01") & (sessions <= "2026-06-30")])
    events = []
    def verify(*args, **kwargs):
        assert not holdout._SESSIONS.get()
        events.append("preflight")
        return history
    monkeypatch.setattr(hc003, "verify_non_holdout", verify)
    real_session = holdout.HoldoutSession
    class Session(real_session):
        def __enter__(self):
            events.append("session")
            assert events == ["preflight", "session"]
            return super().__enter__()
    monkeypatch.setattr(holdout, "HoldoutSession", Session)
    def read(*args, **kwargs):
        assert kwargs["experiment_id"] == hc003.EXPERIMENT_ID
        assert holdout._SESSIONS.get()[0].experiment_id == hc003.EXPERIMENT_ID
        events.append("protected_read")
        if abort:
            raise RuntimeError("synthetic protected failure")
        common.guard_prices(future, experiment_id=kwargs["experiment_id"], repo_root=offline_repo)
        return future, []
    monkeypatch.setattr(hc003, "load_prices", read)
    original_guard = holdout.guard_period
    def guard(start, end, **kwargs):
        if pd.Timestamp(end) >= pd.Timestamp("2026-01-01") and pd.Timestamp(start) <= pd.Timestamp("2026-06-30"):
            assert kwargs.get("experiment_id") == hc003.EXPERIMENT_ID
            assert holdout._SESSIONS.get()
        return original_guard(start, end, **kwargs)
    monkeypatch.setattr(holdout, "guard_period", guard)
    if abort:
        with pytest.raises(RuntimeError, match="synthetic"):
            hc003.run_holdout(data_dir=offline_repo / "data", repo_root=offline_repo)
        assert hc003.registry_rows(offline_repo)[-1]["verdict"] == "holdout_aborted"
    else:
        result = hc003.run_holdout(data_dir=offline_repo / "data", repo_root=offline_repo)
        assert result["stage"] == "holdout" and set(result["variants"]) == {"t_hold"}
        assert result["variants"]["t_hold"]["primary"]["count"] == 6
        assert result["variants"]["t_hold"]["per_month"][0]["return_denominator"] == 1
        assert hc003.registry_rows(offline_repo)[-1]["variant"] == "holdout_t_hold"
        assert result["variants"]["t_hold"]["per_month"][-1]["end_kind"] == "close"
    assert len((offline_repo / holdout.LEDGER_PATH).read_text().splitlines()) == 1
    assert events == ["preflight", "session", "protected_read"]
    with pytest.raises(ValueError, match="cannot reopen"):
        hc003.run_holdout(data_dir=offline_repo / "data", repo_root=offline_repo)


def test_holdout_first_month_includes_opening_gap_and_cost(offline_repo, rates):
    days = hc003.session_calendar()
    days = days[(days >= "2025-10-01") & (days <= "2026-06-30")]
    prices = prices_for(days)
    prices.loc[(pd.Timestamp("2026-01-02"), "000010"), ["open", "close", "ret_1d"]] = [12000, 12000, 0.2]
    planned = [selection("2025-12-30", "2026-01-02", "2026-06-30", {"000010": 2, "000020": 2}, kind="close")]
    with holdout.HoldoutSession(hc003.EXPERIMENT_ID, repo_root=offline_repo):
        book = book_for(prices, days, planned, repo_root=offline_repo)
        run = hc003.simulate(book, planned, "t_hold", initial_state={"cash": 0, "quantities": {"000010": 1}}, initial_value=1)
    row = run["per_month"][0]
    assert row["pre_value"] == pytest.approx(1.2) and row["return_denominator"] == 1
    assert row["cost"] > 0 and row["return"] == pytest.approx(0.2 - row["cost"])


@pytest.mark.parametrize("verdict", ["fail", "insufficient", "delisting_sensitive"])
def test_lh002_not_blocked_without_hc003_pass(offline_repo, verdict):
    record(offline_repo, verdict=verdict)
    lh_cli.guard_hc003_ordering(repo_root=offline_repo, config=LH002)


def test_lh002_only_ordering_guard_blocks_pending_pass(offline_repo):
    record(offline_repo)
    lh_cli.guard_hc003_ordering(repo_root=offline_repo, config=LH001)
    with pytest.raises(ValueError, match="HC003.*unfinished"):
        lh_cli.guard_hc003_ordering(repo_root=offline_repo, config=LH002)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("terminal", ["holdout_t_hold", "holdout_contaminated", "holdout_aborted"])
def test_lh002_can_proceed_after_any_terminal_hc003_holdout_row(offline_repo, terminal):
    record(offline_repo)
    record(offline_repo, terminal, "fail" if terminal == "holdout_t_hold" else terminal)
    lh_cli.guard_hc003_ordering(repo_root=offline_repo, config=LH002)


def test_lh002_final_ordering_check_happens_before_holdout_open(offline_repo, monkeypatch):
    from backtesting.experiments.lh001 import workspace
    from backtesting.experiments.lh001.runner import save_json
    record(offline_repo)
    root = workspace(offline_repo / "data", LH002)
    probe = {"verdict": "clean", "clean_start": "2026-01-01", "pooled_accuracy": 0.3,
             "pooled_p_value": 0.5, "positive_control": {}, "scores": []}
    save_json(root / "probe.json", probe)
    save_json(root / "screening.json", {"smoke": False})
    monkeypatch.setattr(lh_cli, "_registration", lambda *a, **k: {})
    monkeypatch.setattr(lh_cli.probe_v2, "clean_window", lambda *a: probe)
    monkeypatch.setattr(lh_cli, "price_days", lambda *a: pd.DatetimeIndex(["2026-10-30"]))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("ordering guard was late"))
    with pytest.raises(ValueError, match="HC003.*unfinished"):
        lh_cli.execute_final(data_dir=offline_repo / "data", repo_root=offline_repo, config=LH002,
                             runner=SimpleNamespace(), sessions=hc003.session_calendar())


def test_dry_run_default_read_only_no_returns_or_protected_reads(offline_repo, monkeypatch, capsys):
    prices_dir = offline_repo / "data/market/krx_daily/2025"
    prices_dir.mkdir(parents=True)
    (prices_dir / "20251230.jsonl").write_text("archive presence only")
    later = offline_repo / "data/market/krx_daily/2026"
    later.mkdir()
    (later / "20260102.jsonl").write_text("protected content must not be read")
    before = {str(path): path.read_bytes() for path in offline_repo.rglob("*") if path.is_file()}
    def denied(*args, **kwargs): pytest.fail("dry-run evaluated or wrote")
    for name in ("load_prices", "run_experiment", "run_holdout", "write_results"):
        monkeypatch.setattr(hc003, name, denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    monkeypatch.setattr(holdout, "HoldoutSession", denied)
    plan = hc003.dry_run(data_dir=offline_repo / "data", repo_root=offline_repo)
    assert plan["planned_months"] == plan["calendar_months"] == 103
    assert plan["price_files"] == 1 and plan["read_span"].endswith("2025-12-30")
    assert plan["months"][-1]["end_date"] == "2025-12-30" and plan["months"][-1]["end_kind"] == "close"
    monkeypatch.setattr(hc003, "dry_run", lambda **k: plan)
    assert hc003.main([]) == hc003.main(["--dry-run"]) == 0
    assert "No returns evaluated" in capsys.readouterr().out
    assert before == {str(path): path.read_bytes() for path in offline_repo.rglob("*") if path.is_file()}


def test_default_control_count_is_registered_200():
    assert hc003.N_CONTROLS == 200 and hc003.VARIANTS == ("t_hold", "t_hyst", "t_filter")


def test_preregistration_calendar_and_holdout_decision_ranges():
    days = hc003.session_calendar()
    design, excluded = hc003.rebalance_calendar(days)
    protected, omitted = hc003.rebalance_calendar(days, holdout_mode=True)
    assert len(design) == 103 and not excluded and design[0]["month"] == "2017-05"
    assert design[-1]["trade_date"] == "2025-12-01" and design[-1]["end_date"] == "2025-12-30"
    assert len(protected) == 6 and not omitted
    assert protected[0]["decision_date"] == "2025-12-30" and protected[0]["trade_date"] == "2026-01-02"
    assert protected[-1]["month"] == "2026-05" and protected[-1]["end_date"] == "2026-06-30"


def test_adjusted_index_first_observation_is_one(market):
    prices, days = market
    prices.loc[(days[0], "000010"), "ret_1d"] = 0.8
    book = hc003.PriceBook(prices, days, [days[0], days[21]], days[-1])
    assert book.quotes(days[0])["000010"].mark == 1
    assert book.closings["000010"] == 1


@pytest.mark.parametrize("invalid", [np.inf, -1.01])
def test_invalid_adjusted_returns_fail_clearly(market, invalid):
    prices, days = market
    prices.loc[(days[30], "000010"), "ret_1d"] = invalid
    with pytest.raises(ValueError, match="daily return"):
        hc003.PriceBook(prices, days, [days[40]], days[-1])


def test_control_retention_includes_frozen_names_outside_actual_H(market, rates, monkeypatch):
    prices, days = market
    monkeypatch.setattr(hc003, "N_CONTROLS", 1)
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            selection("2025-09-30", "2025-10-01", "2025-12-30", {"000020": 4}, kind="close")]
    prices.loc[(pd.Timestamp("2025-10-01"), "000010"), "volume"] = 0
    book = book_for(prices, days, rows)
    actual = hc003.simulate(book, rows, "t_hold")
    assert actual["per_month"][1]["H"] == [] and actual["per_month"][1]["retained_count"] == 1
    matched = []
    original = hc003.random_target
    def capture(u, previous, size, retained, rng):
        matched.append((size, retained))
        return original(u, previous, size, retained, rng)
    monkeypatch.setattr(hc003, "random_target", capture)
    hc003.control_metrics(book, rows, "t_hold", actual)
    assert matched == [(1, 0), (0, 1)]


def test_undefined_se_remains_insufficient_even_with_supplied_t(fields):
    results = {name: result_for() for name in hc003.VARIANTS}
    results["t_hyst"]["primary"]["standard_error"] = None
    hc003.judge_variants(results, fields, 3)
    assert results["t_hyst"]["verdict"] == "insufficient" and results["t_hyst"]["holm_p"] == 1


def test_filter_missing_coverage_still_executes_target_driven_sells(market, rates):
    prices, days = market
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            selection("2025-09-30", "2025-10-01", "2025-12-30", {"000010": 4, "000020": 2}, covered=False, kind="close")]
    run = hc003.simulate(book_for(prices, days, rows), rows, "t_filter")
    assert run["per_month"][1]["trades"][0]["side"] == "sell"
    assert run["per_month"][1]["buy_amount"] == 0 and run["state"]["cash"] > 0


def test_cost_multiplier_does_not_scale_v2_sell_tax():
    day = pd.Timestamp("2025-05-02").date()
    price, adv = 12000.0, 2e9
    buy = cost_model.one_way_cost(price, "KOSDAQ", day, adv, "buy", model_version="v2")
    sell = cost_model.one_way_cost(price, "KOSDAQ", day, adv, "sell", model_version="v2")
    buy15 = cost_model.one_way_cost(price, "KOSDAQ", day, adv, "buy", model_version="v2", multiplier=1.5)
    sell15 = cost_model.one_way_cost(price, "KOSDAQ", day, adv, "sell", model_version="v2", multiplier=1.5)
    assert buy15 == pytest.approx(buy * 1.5)
    assert sell15 - buy15 == pytest.approx(sell - buy)


def test_month_return_ignores_next_rebalance_targets_and_costs(market, rates):
    prices, days = market
    rows = [selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            selection("2025-09-30", "2025-10-01", "2025-12-30", {"000010": 2, "000020": 4}, kind="close")]
    book = book_for(prices, days, rows)
    first = hc003.simulate(book, rows, "t_hold")["per_month"][0]
    rows[1]["universe"] = universe({"000010": 4, "000020": 2})
    second = hc003.simulate(book, rows, "t_hold")["per_month"][0]
    assert first == second


def test_terminal_session_gap_is_data_failure_not_delisting(market, offline_repo):
    prices, days = market
    planned, _ = hc003.rebalance_calendar(days)
    prices = prices.loc[prices.index.get_level_values("trade_date") != days[-1]]
    with pytest.raises(ValueError, match="coverage"):
        hc003._prepare(prices, planned, data_dir=offline_repo / "data", repo_root=offline_repo, sessions=days, files={})


def test_non_holdout_financial_view_excludes_future_year_archives_without_changing_hc002(market, offline_repo):
    prices, days = market
    directory = offline_repo / "data/fundamentals/dart_quarterly"
    protected = directory / "2026_11013.jsonl"
    protected.write_text("future-year archive must not be opened or parsed")
    planned = [selection("2025-09-30", "2025-10-01", "2025-12-30", {}, kind="close")]
    events, _ = hf001.rights_events(pd.DataFrame(columns=hf001.LISTING_COLUMNS), days)
    result, _ = hc003.build_selections(prices, planned, data_dir=offline_repo / "data", repo_root=offline_repo,
                                       sessions=days, files={}, events=events)
    assert hc003.target_set("t_hold", result[0]["universe"], ()) == {"000010"}
    hashes = hc003.source_hashes(offline_repo / "data", [], {})
    assert all("2026_" not in path for path in hashes["quarterly"])
    assert protected.read_text() == "future-year archive must not be opened or parsed"
    with common.quarterly_view(offline_repo / "data", "2025-09-30", repo_root=offline_repo) as view:
        assert (view / "fundamentals/dart_quarterly/2025_11013.jsonl").is_symlink()
        assert not (view / "fundamentals/dart_quarterly/2026_11013.jsonl").exists()
    assert not view.exists()
