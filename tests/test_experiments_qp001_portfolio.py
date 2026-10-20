"""Small synthetic accounting/control/replay fixtures."""
import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments import av001, hc003
from backtesting.experiments.qp001 import EXPERIMENT_ID
from backtesting.experiments.qp001 import portfolio


def quote(mark=1, **kwargs):
    return hc003.Quote(mark=mark, raw_open=10000, market="KOSPI", adv=2e9, tradable=True, **kwargs)


@pytest.fixture
def zero_cost(monkeypatch):
    monkeypatch.setattr(portfolio.cost_model, "one_way_cost", lambda *a, **k: 0)


def common_set():
    return pd.DataFrame({"market": ["KOSPI"] * 6 + ["KOSDAQ"] * 3,
        "phase": [1, 2, 2, 1, 1, 2, 2, 2, 1], "market_cap": [1, 2, 2, 4, 5, 6, 1, 2, 3],
        "roa": [1., 3., 3., 5., 4., 6., 3., 2., 1.]}, index=list("abcdefghi"))


def test_market_specific_inverted_cdf_lower_boundary_ties_and_stable_top_half():
    groups, selected, weights, edges = portfolio.group_targets(common_set())
    assert edges == {"KOSDAQ": [1, 2], "KOSPI": [2, 4]}
    assert groups["KOSPI", True, 0] == {"b", "c"}
    assert selected["KOSPI", True, 0] == {"b"}
    assert sum(weights.values()) == pytest.approx(1)
    assert weights["KOSPI", True, 0] == 2 / 9
    assert list(groups) == sorted(groups)


def test_group_alpha_and_empty_executable_group_reserves_cash(zero_cost):
    groups = {("KOSPI", False, 0): {"a", "b", "c"}, ("KOSPI", True, 0): {"d"}}
    quotes = {code: quote(limit_up=code == "d") for code in "abcd"}
    state, report = portfolio.rebalance(hc003.PortfolioState(), quotes, {"a", "d"}, "2023-06-01",
        groups=groups, weights={key: len(codes) / 4 for key, codes in groups.items()})
    assert state.quantities == {"a": .75}
    assert state.cash == .25
    assert report["empty_group_cash_target"] == .25
    assert report["target_amounts"] == {"a": .75}


def test_global_frozen_capital_deducted_before_group_targets(zero_cost):
    groups = {("KOSPI", False, 0): {"a"}, ("KOSPI", True, 0): {"b"}}
    frozen = hc003.Quote(1, 10000, "KOSPI", 2e9, False)
    state, report = portfolio.rebalance(hc003.PortfolioState(.6, {"z": .4}),
        {"a": quote(), "b": quote(), "z": frozen}, {"a", "b"}, "2023-06-01", groups=groups,
        weights={key: .5 for key in groups})
    assert state.quantities == {"a": .3, "b": .3, "z": .4}
    assert report["W"] == .6


@pytest.mark.parametrize("no_trade", [False, True])
def test_single_group_matches_hc003_freeze_band_limits_and_costs(no_trade):
    groups = {("KOSPI", False, 0): set("abcde")}
    quotes = {code: quote(mark=1 + i / 10, limit_up=code == "c", limit_down=code == "d")
              for i, code in enumerate("abcde")}
    state = hc003.PortfolioState(.2005, {"a": .2995, "b": .1, "c": .2, "d": .2})
    targets = set("acde")
    actual, report = portfolio.rebalance(state, quotes, targets, "2023-06-01", groups=groups,
        weights={next(iter(groups)): 1}, no_trade=no_trade)
    expected, old = hc003.rebalance(state, quotes, targets, "2023-06-01", no_trade=no_trade)
    assert actual.to_dict() == expected.to_dict()
    for key in ("pre_value", "W", "buy_scale", "trades", "buy_cost", "sell_cost", "cash_weight", "frozen_weight"):
        assert report[key] == old[key]


def test_full_exit_ignores_band_and_group_cash_is_not_redistributed(zero_cost):
    group = ("KOSPI", False, 0)
    state, report = portfolio.rebalance(hc003.PortfolioState(.9995, {"z": .0005}),
        {"a": quote(), "z": quote()}, {"a"}, "2023-06-01", groups={group: {"a"}}, weights={group: 1})
    assert state.quantities == {"a": 1}
    assert report["trades"][0]["full_exit"]


def fixture_book(tmp_path):
    snapshots = {"2023-12-01": dict(zip("abcd", [1., 1., 1., 1.])),
                 "2024-01-02": dict(zip("abcd", [1.2, .8, 1.1, 1.])),
                 "2024-02-01": dict(zip("abcd", [1.3, .7, 1.05, 1.2]))}
    book = SimpleNamespace(experiment_id=EXPERIMENT_ID, repo_root=tmp_path, end=pd.Timestamp("2024-02-01"))
    book.quotes = lambda day, loss=False: {code: quote(mark) for code, mark in snapshots[str(pd.Timestamp(day).date())].items()}
    book.marks = lambda day, kind="open", loss=False: snapshots[str(pd.Timestamp(day).date())]
    groups = {("KOSPI", False, 0): {"a", "b"}, ("KOSPI", True, 0): {"c", "d"}}
    rows = []
    for month, decision, opening, ending in (("2023-11", "2023-11-30", "2023-12-01", "2024-01-02"),
                                              ("2023-12", "2023-12-28", "2024-01-02", "2024-02-01")):
        rows.append({"month": month, "decision_date": decision, "trade_date": opening, "end_date": ending, "end_kind": "open",
            "groups": groups, "selected": {key: {sorted(codes)[0]} for key, codes in groups.items()},
            "weights": {key: .5 for key in groups}, "common": pd.DataFrame(index=list("abcd")),
            "universe": pd.DataFrame({"phase": 1}, index=list("abcd")), "no_trade": False, "data_complete": True})
    return book, rows


def test_cost_scenarios_have_independent_strategy_and_costed_baseline_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio.cost_model, "one_way_cost",
        lambda *a, multiplier=1, model_version="v2", **k: multiplier * (.01 if model_version == "v2" else .03))
    book, rows = fixture_book(tmp_path)
    runs = portfolio.paired_scenarios(book, rows)
    assert len({run["baseline"]["per_month"][0]["return"] for run in runs.values()}) == 4
    for key, (model, multiplier) in hc003.SCENARIOS.items():
        expected_baseline = portfolio.simulate(book, rows, experiment_id=EXPERIMENT_ID, variant="baseline",
            targets_fn=portfolio.qp_targets, model_version=model, multiplier=multiplier, retain_details=key == "v2_1.0")
        assert runs[key]["baseline"] == expected_baseline
        for actual, baseline in zip(runs[key]["strategy"]["per_month"], expected_baseline["per_month"]):
            assert actual["excess"] == actual["return"] - baseline["return"]
            assert baseline["cost"] > 0


def test_decision_month_design_end_includes_january_to_february_and_carries_state(tmp_path):
    book, rows = fixture_book(tmp_path)
    full = portfolio.simulate(book, rows, experiment_id=EXPERIMENT_ID, variant="q_roa50", targets_fn=portfolio.qp_targets)
    first = portfolio.simulate(book, rows[:1], experiment_id=EXPERIMENT_ID, variant="q_roa50", targets_fn=portfolio.qp_targets)
    next_part = portfolio.simulate(book, rows[1:], experiment_id=EXPERIMENT_ID, variant="q_roa50", targets_fn=portfolio.qp_targets,
                                  initial_state=first["state"], initial_value=first["per_month"][-1]["end_value"])
    assert full["state"] == next_part["state"]
    assert full["per_month"][1]["return"] == next_part["per_month"][0]["return"]
    assert full["per_month"][-1]["month"] == "2023-12"
    assert full["per_month"][-1]["end_date"] == "2024-02-01"


def test_empty_A_retains_held_quantities_and_cash_with_real_valuation(tmp_path):
    book, rows = fixture_book(tmp_path)
    row = {**rows[0], "no_trade": True, "groups": {}, "weights": {}, "selected": {}, "common": pd.DataFrame()}
    initial = {"cash": .4, "quantities": {"a": .6}}
    run = portfolio.simulate(book, [row], experiment_id=EXPERIMENT_ID, variant="q_roa50",
        targets_fn=portfolio.qp_targets, initial_state=initial)
    assert run["state"] == initial
    assert run["per_month"][0]["trades"] == []
    assert run["per_month"][0]["return"] == pytest.approx(.12)


def test_controls_prioritize_count_and_only_retain_current_group_members():
    groups = {("KOSPI", False, 0): set("abcd"), ("KOSPI", True, 0): set("efgh")}
    row = {"groups": groups, "selected": {key: set(sorted(codes)[:2]) for key, codes in groups.items()}}
    reference = {"target_retained_by_group": {str(key): 1 for key in groups}}
    previous = set("abe")
    chosen, adjustments = portfolio.random_targets(row, previous, reference, np.random.default_rng(0))
    assert len(chosen & set("abcd")) == len(chosen & set("efgh")) == 2
    assert len(chosen & previous & set("abcd")) == 1
    # Moving stock e can only be retained in its current group.
    assert "e" in chosen
    assert all(not value["shortage"] for value in adjustments.values())
    _, adjustments = portfolio.random_targets(row, set("abcdefgh"), reference, np.random.default_rng(0))
    assert all(value["adjustment"] == 1 for value in adjustments.values())


def test_controls_fixed_seed_order_and_independent_diagnostic(tmp_path):
    book, rows = fixture_book(tmp_path)
    runs = portfolio.paired_scenarios(book, rows)
    actual, baseline = [runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline")]
    first = portfolio.controls(book, rows, actual, baseline, paths=3)
    second = portfolio.controls(book, rows, actual, baseline, paths=3)
    assert first == second
    assert first["paths"] == 3 and first["seed"] == 0
    independent = portfolio.controls(book, rows, actual, baseline, paths=3, independent=True)
    assert not independent["used_for_judgement"]


def test_group_costed_contributions_sum_to_e_and_long_short_is_diagnostic(tmp_path):
    book, rows = fixture_book(tmp_path)
    runs = portfolio.paired_scenarios(book, rows)
    actual, baseline = [runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline")]
    report = portfolio.subgroup_diagnostics(book, rows, actual, baseline)
    assert not report["long_short_used_for_judgement"]
    for diagnostic, monthly in zip(report["per_month"], actual):
        assert sum(group["e_contribution"] for group in diagnostic["groups"].values()) + diagnostic["outside_C_e_contribution"] == pytest.approx(monthly["excess"])
        assert diagnostic["markets_e_contribution"]["KOSPI"] == pytest.approx(monthly["excess"])


def test_minus_100_recomputes_each_scenario_and_baseline_path(tmp_path):
    book, rows = fixture_book(tmp_path)
    original_quotes, original_marks = book.quotes, book.marks
    def quotes(day, loss=False):
        values = original_quotes(day)
        if loss and pd.Timestamp(day) >= pd.Timestamp("2024-01-02"):
            values["a"] = hc003.Quote(0, 10000, "KOSPI", 2e9, False)
        return values
    def marks(day, kind="open", loss=False):
        values = dict(original_marks(day, kind))
        if loss and pd.Timestamp(day) >= pd.Timestamp("2024-01-02"):
            values["a"] = 0
        return values
    book.quotes, book.marks = quotes, marks
    main = portfolio.paired_scenarios(book, rows)
    loss = portfolio.paired_scenarios(book, rows, loss=True)
    for scenario in main:
        for side in ("strategy", "baseline"):
            assert loss[scenario][side]["terminal_value"] < main[scenario][side]["terminal_value"]
            assert loss[scenario][side]["per_month"][1]["frozen_weight"] == 0


def test_reused_kernel_reproduces_av_all_monthly_e_on_synthetic_data(tmp_path):
    book, rows = fixture_book(tmp_path)
    book.experiment_id = av001.EXPERIMENT_ID
    for row in rows:
        row.pop("groups")
        row["F_vol"], row["F_raise"] = {"b"}, {"d"}
    expected = av001._simulate(book, rows, "av_all")
    actual = portfolio.simulate(book, rows, experiment_id=av001.EXPERIMENT_ID, variant="av_all", targets_fn=av001.target_set)
    assert [r["excess"] for r in actual["per_month"]] == [r["excess"] for r in expected["per_month"]]
    assert actual["state"] == expected["state"]
    with pytest.raises(ValueError, match="experiment ID"):
        portfolio.simulate(book, rows, experiment_id=EXPERIMENT_ID, variant="av_all", targets_fn=av001.target_set)


def test_new_group_branch_reproduces_av_without_hc003_delegation(tmp_path, monkeypatch):
    book, rows = fixture_book(tmp_path)
    book.experiment_id = av001.EXPERIMENT_ID
    for row in rows:
        row.pop("groups")
        row["F_vol"], row["F_raise"] = {"b"}, {"d"}
    expected = av001._simulate(book, rows, "av_all")
    for row in rows:
        row["groups"] = {("AV001", False, 0): set("abcd")}
        row["weights"] = {("AV001", False, 0): 1.}
    monkeypatch.setattr(hc003, "rebalance", lambda *a, **k: pytest.fail("group replay delegated to HC003"))
    actual = portfolio.simulate(book, rows, experiment_id=av001.EXPERIMENT_ID, variant="av_all", targets_fn=av001.target_set)
    assert [r["excess"] for r in actual["per_month"]] == [r["excess"] for r in expected["per_month"]]
    assert actual["state"] == expected["state"]


def test_streamed_controls_preserve_details_random_draws_and_states(tmp_path):
    import json
    book, rows = fixture_book(tmp_path)
    runs = portfolio.paired_scenarios(book, rows)
    actual, baseline = (runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline"))
    expected = portfolio.controls(book, rows, actual, baseline, paths=3)
    path = tmp_path / "control.jsonl"
    streamed = portfolio.controls(book, rows, actual, baseline, paths=3, details_path=path)
    details = [json.loads(line) for line in path.read_text().splitlines()]
    assert details[:2] == expected.pop("per_month")
    assert [row["state"] for row in details[2:]] == expected.pop("states")
    assert streamed.pop("details")["sha256"] == portfolio.artifacts.digest(path)
    assert streamed == expected


def test_month_local_control_cost_cache_preserves_every_result(tmp_path, monkeypatch):
    book, rows = fixture_book(tmp_path)
    runs = portfolio.paired_scenarios(book, rows)
    actual, baseline = (runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline"))
    calls = []
    cost = portfolio.cost_model.one_way_cost
    def counted(*a, **k):
        calls.append(True)
        return cost(*a, **k)
    monkeypatch.setattr(portfolio.cost_model, "one_way_cost", counted)
    cached = portfolio.controls(book, rows, actual, baseline, paths=10)
    cached_count = len(calls)
    calls.clear()
    rebalance = portfolio.rebalance
    def uncached(*a, **k):
        k.pop("cost_rates")
        return rebalance(*a, **k)
    monkeypatch.setattr(portfolio, "rebalance", uncached)
    assert portfolio.controls(book, rows, actual, baseline, paths=10) == cached
    assert cached_count < len(calls)
