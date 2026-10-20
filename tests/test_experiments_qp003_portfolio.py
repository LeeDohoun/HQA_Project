"""Synthetic hold accounting, random continuity and loss recomputation."""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments import hc003
from backtesting.experiments.qp001 import portfolio as qp001
from backtesting.experiments.qp003 import EXPERIMENT_ID
from backtesting.experiments.qp003 import portfolio
from backtesting.experiments.qp003.selection import apply_permission


def fixture_book(tmp_path, permissions=(True, False, False, True)):
    codes = list("abcdefghij")
    dates = ["2023-11-01", "2023-12-01", "2024-01-02", "2024-02-01", "2024-03-04"]
    snapshots = {day: {code: 1 + i * (.1 if code == "a" else -.04 if code == "b" else .01)
                       for code in codes} for i, day in enumerate(dates)}
    book = SimpleNamespace(experiment_id=EXPERIMENT_ID, repo_root=tmp_path, end=pd.Timestamp(dates[-1]))
    book.quotes = lambda day, loss=False: {code: hc003.Quote(mark, 10000, "KOSPI", 2e9, True)
        for code, mark in snapshots[str(pd.Timestamp(day).date())].items()}
    book.marks = lambda day, kind="open", loss=False: dict(snapshots[str(pd.Timestamp(day).date())])
    rows = []
    for i, permitted in enumerate(permissions):
        month = str(pd.Period("2023-10", freq="M") + i)
        common = codes if permitted else codes[2:]
        key = ("KOSPI", False, 0)
        selected = {"a", "b", "c", "d", "e"} if i == 0 else set(common[-5:])
        rows.append(apply_permission({"month": month, "decision_date": str(pd.Period(month).end_time.date()),
            "trade_date": dates[i], "end_date": dates[i + 1], "end_kind": "open", "A": codes,
            "universe": pd.DataFrame({"phase": 1}, index=codes), "common": pd.DataFrame(index=common),
            "groups": {key: set(common)}, "selected": {key: selected}, "weights": {key: 1.0},
            "boundaries": {}, "roa_provenance": {},
            "precheck": {"month": month, "A_count": 10, "C_count": len(common), "C_over_A": len(common) / 10,
                "stage": "design" if month <= "2023-12" else "validation", "listing_complete": True,
                "data_complete": permitted, "fs_div": {}}}))
    return book, rows


@pytest.fixture
def zero_cost(monkeypatch):
    monkeypatch.setattr(qp001.cost_model, "one_way_cost", lambda *a, **k: 0)


def paired_rows(book, rows, **kwargs):
    runs = portfolio.paired_scenarios(book, rows, **kwargs)
    return runs, runs["v2_1.0"]["strategy"]["per_month"], runs["v2_1.0"]["baseline"]["per_month"]


def test_holds_keep_real_e_and_all_months_including_empty_A(tmp_path, zero_cost):
    book, rows = fixture_book(tmp_path)
    rows[2] = apply_permission({**rows[2], "common": pd.DataFrame(), "groups": {}, "selected": {}, "weights": {},
        "precheck": {**rows[2]["precheck"], "A_count": 0, "C_count": 0, "C_over_A": None}})
    _, actual, baseline = paired_rows(book, rows)
    for i in (1, 2):
        assert actual[i]["holdings"] == actual[0]["holdings"]
        assert baseline[i]["holdings"] == baseline[0]["holdings"]
        assert actual[i]["trades"] == baseline[i]["trades"] == []
        assert actual[i]["cost"] == baseline[i]["cost"] == 0
        assert actual[i]["valid"] and baseline[i]["valid"]
        assert actual[i]["excess"] == pytest.approx(actual[i]["return"] - baseline[i]["return"])
        assert actual[i]["excess"] != 0
    result = portfolio.summary(actual[:3])
    assert result["primary"]["count"] == 3
    assert result["primary"]["mean"] == pytest.approx(np.mean([r["excess"] for r in actual[:3]]))
    diagnostics = portfolio.subgroup_diagnostics(book, rows, actual, baseline)
    for diagnostic, row in zip(diagnostics["per_month"], actual):
        assert sum(g["e_contribution"] for g in diagnostic["groups"].values()) + diagnostic["outside_C_e_contribution"] == pytest.approx(row["excess"])


def test_first_hold_stays_cash_one_and_next_permission_rebalances(tmp_path, zero_cost):
    book, rows = fixture_book(tmp_path, (False, True, False, True))
    runs, actual, baseline = paired_rows(book, rows)
    for scenario in runs.values():
        for side in ("strategy", "baseline"):
            first = scenario[side]["per_month"][0]
            assert first["end_value"] == first["return_denominator"] == 1
            assert first["cash_weight"] == 1 and first["holdings_count"] == 0
            assert first["return"] == 0 and first["valid"]
    assert actual[1]["trades"] and baseline[1]["trades"]
    held = portfolio.simulate(book, rows[:1], experiment_id=EXPERIMENT_ID, variant="baseline", targets_fn=portfolio.qp_targets)
    assert held["state"] == {"cash": 1.0, "quantities": {}}
    controls = portfolio.controls(book, rows[:1], actual[:1], baseline[:1], paths=3)
    assert all(state == held["state"] for state in controls["states"])
    assert controls["rng_state"] == np.random.default_rng(0).bit_generator.state


def test_hold_preserves_exact_units_and_cash_and_resume_uses_carried_state(tmp_path):
    book, rows = fixture_book(tmp_path)
    initial = {"cash": .137, "quantities": {"a": .4, "j": .3}}
    def denied(*a):
        pytest.fail("hold evaluated fresh targets")
    held = portfolio.simulate(book, rows[1:3], experiment_id=EXPERIMENT_ID, variant="baseline",
                              targets_fn=denied, initial_state=initial)
    assert held["state"] == initial
    full = portfolio.simulate(book, rows, experiment_id=EXPERIMENT_ID, variant="baseline", targets_fn=portfolio.qp_targets)
    prior = portfolio.simulate(book, rows[:3], experiment_id=EXPERIMENT_ID, variant="baseline", targets_fn=portfolio.qp_targets)
    resume = portfolio.simulate(book, rows[3:], experiment_id=EXPERIMENT_ID, variant="baseline",
        targets_fn=portfolio.qp_targets, initial_state=prior["state"], initial_value=prior["per_month"][-1]["end_value"])
    assert full["state"] == resume["state"]
    assert full["per_month"][-1]["return"] == resume["per_month"][0]["return"]


@pytest.mark.parametrize("independent", [False, True])
def test_controls_never_draw_on_holds_and_resume_from_their_carried_holdings(tmp_path, monkeypatch, zero_cost, independent):
    book, rows = fixture_book(tmp_path)
    _, actual, baseline = paired_rows(book, rows)
    draw = portfolio.random_targets
    calls = []
    def tracked(selection, previous, reference, rng, **kwargs):
        assert selection["rebalance_permitted"]
        chosen, adjustments = draw(selection, previous, reference, rng, **kwargs)
        calls.append((selection["month"], set(previous), set(chosen), copy.deepcopy(rng.bit_generator.state)))
        return chosen, adjustments
    monkeypatch.setattr(portfolio, "random_targets", tracked)
    result = portfolio.controls(book, rows, actual, baseline, paths=3, independent=independent)
    assert len(calls) == 6
    for path in range(3):
        assert calls[path + 3][1] == calls[path][2]
    assert result["design_denominator"] == 3
    for path in range(3):
        values = [r["paths"][path]["excess"] for r in result["per_month"][:3]]
        assert values[1] != 0 or values[2] != 0
        assert result["path_means"][path] == pytest.approx(sum(values) / 3)
    for month in result["per_month"][1:3]:
        assert all(p["cost"] == p["buy_turnover"] == p["sell_turnover"] == 0 for p in month["paths"])
    # Removing held decisions cannot advance the random generator.
    indices = (0, 3)
    shortened = portfolio.controls(book, [rows[i] for i in indices], [actual[i] for i in indices],
        [baseline[i] for i in indices], paths=3, independent=independent)
    assert shortened["rng_state"] == result["rng_state"]
    assert shortened["states"] == result["states"]


@pytest.mark.parametrize("independent", [False, True])
def test_controls_missing_observation_or_path_is_never_removed_from_mean(tmp_path, zero_cost, independent):
    book, rows = fixture_book(tmp_path)
    _, actual, baseline = paired_rows(book, rows)
    baseline[1]["return"] = None
    result = portfolio.controls(book, rows, actual, baseline, paths=3, independent=independent)
    assert result["design_denominator"] == 3
    assert result["path_means"] == [None] * 3 and result["share_of_controls"] is None
    with pytest.raises(ValueError, match="every actual and baseline month"):
        portfolio.controls(book, rows, actual[:-1], baseline, paths=3)


def test_every_cost_and_minus_100_path_uses_same_hold_permissions(tmp_path, monkeypatch):
    book, rows = fixture_book(tmp_path)
    quotes, marks = book.quotes, book.marks
    def loss_quotes(day, loss=False):
        values = quotes(day)
        if loss and pd.Timestamp(day) >= pd.Timestamp("2024-01-02"):
            values["a"] = hc003.Quote(0, 10000, "KOSPI", 2e9, False)
        return values
    def loss_marks(day, kind="open", loss=False):
        values = marks(day, kind)
        if loss and pd.Timestamp(day) >= pd.Timestamp("2024-01-02"):
            values["a"] = 0
        return values
    book.quotes, book.marks = loss_quotes, loss_marks
    monkeypatch.setattr(qp001.cost_model, "one_way_cost", lambda *a, multiplier=1, model_version="v2", **k:
                        multiplier * (.01 if model_version == "v2" else .03))
    main, loss = [portfolio.paired_scenarios(book, rows, loss=vanished) for vanished in (False, True)]
    assert len({s["strategy"]["per_month"][0]["cost"] for s in main.values()}) == 4
    for name in main:
        for side in ("strategy", "baseline"):
            for runs in (main, loss):
                monthly = runs[name][side]["per_month"]
                assert [r["rebalance_permitted"] for r in monthly] == [True, False, False, True]
                assert all(r["cost"] == r["buy_turnover"] == r["sell_turnover"] == 0 for r in monthly[1:3])
            assert loss[name][side]["terminal_value"] < main[name][side]["terminal_value"]
            # The disappearance is valued during a held month, without a liquidation.
            assert loss[name][side]["per_month"][1]["return"] < main[name][side]["per_month"][1]["return"]
    assert loss["v2_1.0"]["strategy"]["per_month"][1]["holdings"] == main["v2_1.0"]["strategy"]["per_month"][0]["holdings"]
    for runs in (main, loss):
        actual, base = (runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline"))
        controls = portfolio.controls(book, rows, actual, base, loss=runs is loss, paths=3)
        assert [r["rebalance_permitted"] for r in controls["per_month"]] == [True, False, False, True]
        assert all(r["valid"] and r["excess"] == r["return"] - b["return"] for r, b in zip(actual, base))


def test_streamed_controls_match_in_memory_and_hash_terminal_states(tmp_path, zero_cost):
    book, rows = fixture_book(tmp_path)
    _, actual, baseline = paired_rows(book, rows)
    expected = portfolio.controls(book, rows, actual, baseline, paths=3)
    output = tmp_path / "controls.jsonl"
    streamed = portfolio.controls(book, rows, actual, baseline, paths=3, details_path=output)
    details = [json.loads(line) for line in output.read_text().splitlines()]
    assert details[:4] == expected.pop("per_month")
    assert [r["state"] for r in details[4:]] == expected.pop("states")
    assert streamed.pop("details")["sha256"] == portfolio.artifacts.digest(output)
    assert streamed == expected


def test_no_hold_path_matches_qp001_without_changing_its_behavior(tmp_path):
    book, rows = fixture_book(tmp_path, (True,) * 4)
    actual = portfolio.paired_scenarios(book, rows)
    book.experiment_id = qp001.EXPERIMENT_ID
    expected = qp001.paired_scenarios(book, rows)
    for name in expected:
        for side in ("strategy", "baseline"):
            assert actual[name][side]["state"] == expected[name][side]["state"]
            assert actual[name][side]["terminal_value"] == expected[name][side]["terminal_value"]
            for a, b in zip(actual[name][side]["per_month"], expected[name][side]["per_month"]):
                assert {k: v for k, v in a.items() if k not in ("rebalance_permitted", "decision_status")} == b
    with pytest.raises(ValueError, match="experiment ID"):
        portfolio.paired_scenarios(book, rows)
