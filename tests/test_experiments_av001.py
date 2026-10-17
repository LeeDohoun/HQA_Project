"""AV001 accounting, inference, provenance and protected-stage fixtures.

Optional saved HC003 replay (local prices only, no holdout):
RUN_AV001_HC003_ARCHIVE_CHECK=1 venv/bin/python -m pytest -q \
    tests/test_experiments_av001.py -k optional_committed_HC003
It checks price hashes, three months with a full book and all 103 months through
AV001's quarterly streaming PriceBook.from_windows path. Only 2017-2025 is read.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import av001, common, hc003, hf001, ordering
from backtesting.experiments.lh001.config import LH002
from src.ingestion import dart_quarterly
from src.ingestion.storage import write_rows
from dart_listing_fixtures import checkpoint as listing_checkpoint
from test_dart_financial_date_rules import filing, save, version
from test_experiments_hc003 import prices_for, quote, selection, universe


@pytest.fixture
def repo(tmp_path, monkeypatch):
    ids = [av001.EXPERIMENT_ID, hc003.EXPERIMENT_ID, av001.hc002.EXPERIMENT_ID,
           hf001.EXPERIMENT_ID, "EV001_earnings_filing_entry"]
    registrations = {name: experiment_registry.verify_preregistration(name) for name in ids}
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda name, *a, **k: registrations.get(name, registrations[av001.EXPERIMENT_ID]))
    monkeypatch.setattr(holdout, "verify_preregistration", lambda name, *a, **k: registrations.get(name, registrations[av001.EXPERIMENT_ID]))
    monkeypatch.setattr(experiment_registry, "_git", lambda *a: "fixture-code-commit")
    monkeypatch.setattr(av001, "N_CONTROLS", 3)
    return tmp_path


def av_selection(*args, f_vol=(), f_raise=(), **kwargs):
    row = selection(*args, **kwargs)
    row.update(F_vol=set(f_vol), F_raise=set(f_raise), markets=pd.Series("KOSPI", index=row["universe"].index))
    return row


def rows_for_book():
    return [av_selection("2025-08-29", "2025-09-01", "2025-10-01", {"000010": 2, "000020": 4}),
            av_selection("2025-09-30", "2025-10-01", "2025-11-03", {"000010": 4, "000020": 2}),
            av_selection("2025-10-31", "2025-11-03", "2025-12-30", {"000010": 2, "000020": 1}, kind="close")]


@pytest.mark.parametrize("streamed", [False, True])
def test_parameterised_accounting_reproduces_HC003_t_hold_monthly_e(repo, streamed):
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    prices = prices_for(days)
    prices.loc[(pd.Timestamp("2025-10-01"), "000020"), ["open", "close", "ret_1d"]] = [11000, 11000, .1]
    selections = rows_for_book()
    legacy = hc003.PriceBook(prices, days, [r["trade_date"] for r in selections], days[-1], repo_root=repo)
    kwargs = dict(repo_root=repo, experiment_id=av001.EXPERIMENT_ID)
    if streamed:
        windows = (prices.loc[prices.index.get_level_values("trade_date").isin(part)] for part in np.array_split(days, 4))
        book = hc003.PriceBook.from_windows(windows, days, [r["trade_date"] for r in selections], days[-1], **kwargs)
    else:
        book = hc003.PriceBook(prices, days, [r["trade_date"] for r in selections], days[-1], **kwargs)
    expected = hc003.simulate(legacy, selections, "t_hold")
    actual = av001._simulate(book, selections, "av_conc")
    assert [r["excess"] for r in actual["per_month"]] == [r["excess"] for r in expected["per_month"]]
    assert actual["state"] == expected["state"]
    with pytest.raises(ValueError, match="ID or variant"):
        hc003.simulate(book, selections, "t_hold")


def test_streaming_quotes_marks_disappearance_and_counts_match_full_price_book(repo):
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    prices = prices_for(days, ("000010", "000020", "000030"))
    missing = (prices.index.get_level_values("stock_code") == "000030") & (prices.index.get_level_values("trade_date") > "2025-09-15")
    prices = prices.loc[~missing]
    prices.loc[(pd.Timestamp("2025-09-01"), "000010"), ["close", "open", "ret_1d"]] = [11000, 10500, .1]
    prices.loc[(pd.Timestamp("2025-10-01"), "000010"), ["close", "open", "base_price", "ret_1d"]] = [12000, 11500, 11000, np.nan]
    prices.loc[(pd.Timestamp("2025-11-03"), "000020"), "volume"] = 0
    rebalances = ["2025-09-01", "2025-10-01", "2025-11-03"]
    kwargs = dict(experiment_id=av001.EXPERIMENT_ID, repo_root=repo)
    full = hc003.PriceBook(prices, days, rebalances, days[-1], **kwargs)
    partitions = np.array_split(days, 4)
    windows = [prices.loc[prices.index.get_level_values("trade_date").isin(part)] for part in partitions]
    stream = hc003.PriceBook.from_windows(iter(windows), days, rebalances, days[-1], **kwargs)
    assert full.counts == stream.counts
    assert full.zero_from == stream.zero_from
    for day in rebalances:
        for code, expected in full.quotes(day).items():
            actual = stream.quotes(day)[code]
            assert actual.mark == pytest.approx(expected.mark, abs=1e-14)
            assert (actual.tradable, actual.limit_up, actual.limit_down) == (expected.tradable, expected.limit_up, expected.limit_down)
            if np.isfinite(expected.adv):
                assert actual.adv == pytest.approx(expected.adv)
            else:
                assert np.isnan(actual.adv)
    assert stream.closings == pytest.approx(full.closings)
    assert stream.marks("2025-11-03", loss=True) == pytest.approx(full.marks("2025-11-03", loss=True))
    broken = windows.copy()
    broken[1] = broken[1].loc[broken[1].index.get_level_values("trade_date") != partitions[1][0]]
    with pytest.raises(ValueError, match="incomplete"):
        hc003.PriceBook.from_windows(broken, days, rebalances, days[-1], **kwargs)


@pytest.mark.parametrize("allocation", [None, 4])
def test_vectorised_paths_match_scalar_frozen_band_limit_and_cost_accounting(allocation):
    rng = np.random.default_rng(4)
    codes = ["a", "b", "c", "d", "e", "f"]
    quotes = {code: quote(mark=1 + i / 10) for i, code in enumerate(codes)}
    quotes["b"], quotes["c"], quotes["d"] = quote(1.1, tradable=False), quote(1.2, limit_up=True), quote(1.3, limit_down=True)
    cash = np.full(8, .15)
    quantities = rng.uniform(0, .2, (8, len(codes)))
    quantities[::2, 2] = 0
    targets = rng.uniform(0, 1, quantities.shape) > .4
    new_cash, new_q, report = hc003.rebalance_paths(cash, quantities, codes, quotes, targets, "2025-05-02",
                            allocation_n=np.full(len(cash), allocation) if allocation is not None else None)
    for path in range(len(cash)):
        holdings = {code: q for code, q in zip(codes, quantities[path]) if q > 0}
        state, expected = hc003.rebalance(hc003.PortfolioState(cash[path], holdings),
            quotes, {codes[i] for i in np.flatnonzero(targets[path])}, "2025-05-02", allocation_n=allocation)
        assert new_cash[path] == pytest.approx(state.cash, abs=1e-14)
        assert new_q[path] == pytest.approx([state.quantities.get(code, 0) for code in codes], abs=1e-14)
        for key in ("pre_value", "n", "buy_turnover", "sell_turnover", "cost_fraction", "cash_weight", "frozen_weight", "retained_count"):
            assert report[key][path] == pytest.approx(expected[key], abs=1e-14)


def test_target_sets_keep_unclassified_phase_and_remove_filter_union():
    row = av_selection("2025-08-29", "2025-09-01", "2025-12-30", {"a": 2, "b": 3, "c": 4, "d": None, "e": 1, "f": 2},
                       kind="close", f_vol={"e", "f"}, f_raise={"c", "f"})
    assert av001.target_set("av_all", row, ()) == {"a", "d"}
    assert av001.target_set("av_phase", row, ()) == {"a", "d", "e", "f"}
    assert av001.target_set("av_conc", row, ()) == {"a"}


def test_literal_point_one_percent_band_includes_initial_buys_in_broad_portfolios():
    quotes = {str(i): quote() for i in range(1001)}
    state, row = hc003.rebalance(hc003.PortfolioState(), quotes, set(quotes), "2025-05-02")
    assert row["n"] == 1001 and not row["trades"] and state.cash == 1
    assert not state.quantities
    state, row = hc003.rebalance(hc003.PortfolioState(), quotes, set(list(quotes)[:1000]), "2025-05-02")
    assert len(row["trades"]) == 1000 and len(state.quantities) == 1000
    assert all(fill["amount"] < .001 for fill in row["trades"])  # Band tested before cash/cost scaling.


@pytest.mark.parametrize("size", [999, 1000, 1001])
def test_vectorised_initial_broad_portfolio_obeys_same_single_band_as_scalar(size):
    codes = [f"{i:06d}" for i in range(size)]
    quotes = {code: quote() for code in codes}
    actual_cash, quantities, report = hc003.rebalance_paths([1.0], np.zeros((1, size)), codes,
        quotes, np.ones((1, size), dtype=bool), "2025-05-02")
    expected, scalar = hc003.rebalance(hc003.PortfolioState(), quotes, codes, "2025-05-02")
    assert actual_cash[0] == pytest.approx(expected.cash, abs=1e-14)
    assert report["holdings_count"][0] == scalar["holdings_count"] == (size if size <= 1000 else 0)
    assert quantities[0] == pytest.approx([expected.quantities.get(code, 0) for code in codes], abs=1e-14)
    assert report["cost_fraction"][0] == pytest.approx(scalar["cost_fraction"])


def test_vectorised_paths_preserve_scalar_cash_dust_and_positive_holdings_across_months():
    codes = [f"{i:06d}" for i in range(1000)]
    quotes = {code: quote() for code in codes}
    state = hc003.PortfolioState()
    cash, quantities = np.ones(1), np.zeros((1, len(codes)))
    for size in (999, 1000, 997, 1000):
        targets = np.zeros((1, len(codes)), dtype=bool)
        targets[:, :size] = True
        cash, quantities, report = hc003.rebalance_paths(cash, quantities, codes, quotes, targets, "2025-05-02")
        state, scalar = hc003.rebalance(state, quotes, codes[:size], "2025-05-02")
        assert cash[0] == state.cash
        assert quantities[0].tolist() == [state.quantities.get(code, 0) for code in codes]
        assert report["holdings_count"][0] == scalar["holdings_count"]


def test_identical_strategy_and_control_paths_remain_exact_ties(repo):
    selections = [av_selection(row["decision_date"], row["trade_date"], row["end_date"],
                    {"a": 2, "b": 2}, month=row["month"], kind=row["end_kind"])
                  for row in hc003.rebalance_calendar(hc003.session_calendar())[0]]
    book = TinyBook(repo, selections)
    for pos, row in enumerate(selections):
        day = pd.Timestamp(row["trade_date"])
        book.openings[day] = {"a": quote(1 + pos * .015), "b": quote(1 + pos * .003)}
    actual = av001._simulate(book, selections, "av_phase")
    control = av001.controls(book, selections, "av_phase", actual)
    mean = av001.summary(actual["per_month"])["primary"]["mean"]
    assert control["path_means"] == [mean] * av001.N_CONTROLS
    assert control["share_of_controls"] == 1.0


def test_fill_first_cap_refill_adjustment_shortage_and_sorted_determinism():
    chosen, adjustment, missing = av001.random_target(list("abcdef"), list("abcde"), 5, 1, np.random.default_rng(0))
    assert len(chosen) == 5 and "f" in chosen and adjustment == 3 and missing == 0
    chosen, _, _ = av001.random_target(list("abcd"), list("abcd"), 2, 9, np.random.default_rng(0))
    assert len(chosen) == 2
    chosen, adjustment, missing = av001.random_target(list("ab"), list("ab"), 4, 0, np.random.default_rng(0))
    assert chosen == set("ab") and adjustment == 2 and missing == 2
    a = av001.random_target(list("abcde"), list("abc"), 3, 1, np.random.default_rng(0))
    b = av001.random_target(list("edcba"), list("cba"), 3, 1, np.random.default_rng(0))
    assert a == b


class TinyBook:
    def __init__(self, repo, selections):
        self.experiment_id, self.repo_root = av001.EXPERIMENT_ID, repo
        self.end = pd.Timestamp(selections[-1]["end_date"])
        self.closings = {"a": 1.9, "b": 1.0}
        self.openings = {pd.Timestamp(row["trade_date"]): {"a": quote(1.2 + pos * .1), "b": quote(1)}
                         for pos, row in enumerate(selections)}
    def quotes(self, day, *, loss=False):
        return self.openings[pd.Timestamp(day)]
    def marks(self, day, kind="open", *, loss=False):
        if kind == "close":
            return {"a": 1, "b": 1} if pd.Timestamp(day) == hc003.DESIGN_END else self.closings
        return {code: q.mark for code, q in self.quotes(day).items()}


def test_retention_excludes_frozen_non_target_and_each_control_has_own_denominator(repo):
    row = av_selection("2025-11-28", "2025-12-01", "2025-12-30", {"a": 4, "b": 2}, kind="close")
    book = TinyBook(repo, [row])
    book.openings[pd.Timestamp(row["trade_date"])]["a"] = quote(1, tradable=False)
    book.closings = {"a": 1, "b": 1}
    actual = av001._simulate(book, [row], "av_phase", initial_state={"cash": .4, "quantities": {"a": .6}})
    current = actual["per_month"][0]
    assert current["target_retained_count"] == 0 and current["retained_count"] == 1
    saved = {"codes": ["a", "b"], "cash": [.4] * av001.N_CONTROLS,
             "quantities": [[.6, 0]] * av001.N_CONTROLS, "terminal_values": [1] * av001.N_CONTROLS,
             "rng_state": np.random.default_rng(0).bit_generator.state}
    primary = av001.controls(book, [row], "av_phase", actual, saved=saved)
    secondary = av001.controls(book, [row], "av_phase", actual, saved=saved, secondary=True)
    assert primary["per_month"][0]["executable_denominators"] == [1] * av001.N_CONTROLS
    assert primary["checkpoint"]["quantities"][0][1] > 0
    assert secondary["checkpoint"]["quantities"][0][1] == 0
    assert secondary["used_for_judgement"] is False
    assert primary == av001.controls(book, [row], "av_phase", actual, saved=saved)


def test_calendar_NW_preserves_gaps_uses_valid_N_divisor_and_normal_upper_tail():
    months, values = ["2020-01", "2020-03", "2020-04", "2020-08"], np.array([.1, -.2, .4, .3])
    centered = values - values.mean()
    variance = np.dot(centered, centered) / 4 + 2 * (.75 * centered[2] * centered[1] + .5 * centered[1] * centered[0]
                + .25 * centered[2] * centered[0]) / 4
    result = av001.newey_west(months, values)
    assert result["lag_pair_counts"] == {"1": 1, "2": 1, "3": 1}
    assert result["long_run_variance"] == pytest.approx(variance)
    assert result["standard_error"] == pytest.approx(np.sqrt(variance/4))
    assert result["p_one_sided"] == pytest.approx(.5 * math.erfc(result["t_stat"] / np.sqrt(2)))
    assert av001.newey_west(["2020-01", "2020-02"], [1, 1])["t_stat"] is None
    with pytest.raises(ValueError, match="unique"):
        av001.newey_west(["2020-01", "2020-01"], [1, 2])


def judgement_fixture():
    rng = np.random.default_rng(0)
    months = pd.period_range("2017-05", periods=60, freq="M")
    excess = .02 + rng.normal(0, .01, len(months))
    result = {"primary": av001.newey_west(months, excess), "design": {"primary": {"mean": .02}},
        "cost_sensitivity": {"1.5": {"primary": {"mean": .01}}}, "random_control": {"share_of_controls": .05},
        "risk": {"strategy": {"volatility": .01, "lower_5pct_mean": -.01},
                 "benchmark": {"volatility": .02, "lower_5pct_mean": -.02}}}
    return {v: copy.deepcopy(result) for v in av001.VARIANTS}


@pytest.mark.parametrize("trials,t,expected", [(20, 2, "fail"), (20, 2.01, "pass"), (21, 3, "fail"), (21, 3.01, "pass")])
def test_trial_threshold_strictness_Holm_and_exact_5pct_random_limit(repo, trials, t, expected):
    variants = judgement_fixture()
    for result in variants.values():
        result["primary"].update(t_stat=t, p_one_sided=.001)
    av001.judge_variants(variants, av001._registration(repo).fields, trials)
    assert all(result["verdict"] == expected for result in variants.values())
    assert all(result["holm_p"] == .003 for result in variants.values())
    variants["av_conc"]["random_control"]["share_of_controls"] = .055
    av001.judge_variants(variants, av001._registration(repo).fields, trials)
    assert variants["av_conc"]["verdict"] == "fail"


def test_insufficient_p_one_Holm_family_and_exact_risk_only_definition(repo):
    variants = judgement_fixture()
    variants["av_conc"]["primary"].update(count=35, p_one_sided=.00001)
    for name in ("av_all", "av_phase"):
        variants[name]["primary"]["p_one_sided"] = .01
    av001.judge_variants(variants, av001._registration(repo).fields, 3)
    assert variants["av_conc"]["verdict"] == "insufficient"
    assert variants["av_conc"]["judgement_inputs"]["p_for_holm"] == 1
    assert variants["av_all"]["holm_p"] == .03
    variants = judgement_fixture()
    variants["av_all"]["primary"]["mean"] = variants["av_phase"]["primary"]["mean"] = 0
    variants["av_phase"]["risk"]["strategy"]["lower_5pct_mean"] = -.02
    av001.judge_variants(variants, av001._registration(repo).fields, 3)
    assert variants["av_all"]["verdict"] == "risk_only" and variants["av_phase"]["verdict"] == "fail"


@pytest.mark.parametrize("s,o,available,expected", [
    ("pass", "pass", True, "pass"), ("pass", "fail", True, "version_sensitive"),
    ("fail", "pass", True, "version_sensitive"), ("pass", "pass", False, "sensitivity_unavailable"),
    ("data_incomplete", "data_incomplete", False, "data_incomplete"),
    ("pass", "delisting_sensitive", True, "delisting_sensitive"), ("data_incomplete", "data_incomplete", True, "data_incomplete"),
    ("insufficient", "fail", True, "insufficient"), ("risk_only", "risk_only", True, "risk_only"),
    ("fail", "fail", True, "fail"), ("holdout_insufficient", "fail", True, "holdout_insufficient")])
def test_combined_verdicts(s, o, available, expected):
    assert av001.combine_verdict(s, o, sensitivity_available=available) == expected


def write_data(root):
    (root / "market/krx_daily").mkdir(parents=True, exist_ok=True)
    rows = []
    for year, revenue, income in ((2024, 100, 10), (2025, 120, 20)):
        listing = filing(f"{year}0515", title=f"분기보고서 ({year}.03)")
        rows.append(listing)
        save(root, version(listing["rcept_no"], year=year, revenue=revenue, income=income))
    for row in rows:
        write_rows(root / "disclosures/dart_full/list" / row["rcept_dt"][:4] / f"{row['rcept_dt']}.jsonl", [row])


@pytest.mark.parametrize("compressed", [False, True])
def test_runner_compact_three_rows_S_O_checkpoints_counts_provenance_and_coverage_stop(repo, monkeypatch, compressed):
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    planned = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {}, kind="close")]
    monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
    monkeypatch.setattr(common, "RESULT_GZIP_THRESHOLD_BYTES", 0 if compressed else 50 * 1024**2)
    write_data(repo / "data")
    result = av001.run_experiment(data_dir=repo / "data", repo_root=repo, prices=prices_for(days), sessions=days)
    rows = hc003.registry_rows(repo)
    assert len(rows) == 3 and [r["variant"] for r in rows] == list(av001.VARIANTS)
    assert {r["verdict"] for r in rows if r["variant"] != "av_phase"} == {"data_incomplete"}
    assert result["L"] == 0 and result["state_decision_month"] == "2025-11"
    assert result["listing_status"]["supplement_complete"] is False
    assert result["source_hashes"]["main_listing"] and result["source_hashes"]["financials"]
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        assert len(row["metrics_json"].encode()) < 32 * 1024 and set(metrics["rules"]) == {"S", "O"}
        assert metrics["combined_verdict"] == row["verdict"]
    files = {json.loads(r["metrics_json"])["result_file"] for r in rows}
    assert len(files) == 1
    path = repo / files.pop()
    assert path.name.endswith(".gz") is compressed
    assert common.read_result_json(path) == result
    assert not (repo / holdout.LEDGER_PATH).exists()
    assert result["design_states"]["S"]["av_phase"]["controls"]["rng_state"]
    assert result["rules"]["S"]["av_phase"]["capacity"]["initial_aum_krw"] == list(av001.AUM_SIZES)


def terminal_design(repo, *, passed=("av_all", "av_phase", "av_conc")):
    checkpoint = {key: {"state": {"cash": 0, "quantities": {"a": 1}}, "terminal_value": 1}
                  for key in hc003.SCENARIOS}
    controls = {"codes": ["a", "b"], "cash": [0] * av001.N_CONTROLS, "quantities": [[1, 0]] * av001.N_CONTROLS,
                "terminal_values": [1] * av001.N_CONTROLS, "rng_state": np.random.default_rng(0).bit_generator.state}
    states = {r: {v: {"scenarios": copy.deepcopy(checkpoint), "controls": copy.deepcopy(controls),
                      "secondary_controls": copy.deepcopy(controls)} for v in av001.VARIANTS} for r in av001.RULES}
    for variants in states.values():
        for state in variants.values():
            state["diagnostics"] = {name: copy.deepcopy(checkpoint["v2_1.0"] if name in ("market_KOSPI", "liquidity_2") else
                {"state": {"cash": 1, "quantities": {}}, "terminal_value": 1}) for name in
                ("market_KOSPI", "market_KOSDAQ", "liquidity_0", "liquidity_1", "liquidity_2", "liquidity_3")}
    rules = {r: {v: {**av001.unavailable_metrics("fail"), "verdict": "pass" if v in passed else "fail"}
                 for v in av001.VARIANTS} for r in av001.RULES}
    return {"experiment_id": av001.EXPERIMENT_ID, "stage": "design_validation", "selected_variant": "av_all",
        "verdict": "pass" if passed else "fail", "reasons": [], "L": 17,
        "prereg_commit": av001._registration(repo).commit_hash, "rules": rules,
        "variants": {v: {"verdict": "pass" if v in passed else "fail", "rule_verdicts": {r: "pass" if v in passed else "fail" for r in av001.RULES}}
                     for v in av001.VARIANTS},
        "design_states": states, "state_sha256": hc003._content_hash(states),
        "design_effects": {r: {v: .02 for v in av001.VARIANTS} for r in av001.RULES},
        "comparison_states": {r: checkpoint["v2_1.0"] for r in av001.RULES},
        "state_decision_month": "2025-11", "state_valuation_date": "2025-12-30", "replay_sha256": "saved-replay",
        "source_hashes": {}, "trial_count_after_run": 3, "code_commit": "fixture", "library_versions": {}}


def test_publish_validates_every_row_before_registry_append(repo, monkeypatch):
    result = terminal_design(repo)
    result["rules"]["O"]["av_conc"]["primary"]["mean"] = np.nan
    monkeypatch.setattr(common, "write_results", lambda *a, **k: pytest.fail("invalid payload was published"))
    with pytest.raises(ValueError, match="finite JSON"):
        av001.publish(result, repo_root=repo)
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("failure", ["prepare", "evaluate", "interrupt"])
def test_design_abort_records_each_variant_hashes_run_location_and_blocks_release(repo, monkeypatch, failure):
    av001.publish(terminal_design(repo, passed=()), repo_root=repo)
    hashes = {"prices": {str(i): "a" * 64 for i in range(2000)}, "financials": {"q": "b" * 64}}
    monkeypatch.setattr(av001, "source_hashes", lambda *a, **k: hashes)
    planned = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {}, month="2025-11", kind="close")]
    monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
    monkeypatch.setattr(hc003, "price_files", lambda *a, **k: [])
    def fail(*a, **kwargs):
        raise KeyboardInterrupt("fixture interrupt") if failure == "interrupt" else ValueError("fixture evaluation failed")
    prepared = {"versions": {"S": SimpleNamespace(sensitivity_available=True)}, "book": None,
                "selections": {r: [] for r in av001.RULES}, "raise_complete": True}
    monkeypatch.setattr(av001, "prepare", fail if failure == "prepare" else lambda **k: prepared)
    monkeypatch.setattr(av001, "variant_metrics", fail)
    for attempt in range(2):
        with pytest.raises(KeyboardInterrupt if failure == "interrupt" else ValueError):
            av001.run_experiment(data_dir=repo, repo_root=repo, sessions=hc003.session_calendar())
        rows = hc003.registry_rows(repo)
        assert experiment_registry.trial_count(av001.EXPERIMENT_ID, repo_root=repo) == 6 + 3 * attempt
        aborted = rows[-3:]
        assert [r["variant"] for r in aborted] == list(av001.VARIANTS)
        assert all(r["verdict"] == "design_aborted" for r in aborted)
        details = [json.loads(r["metrics_json"]) for r in aborted]
        assert len({r["run_id"] for r in details}) == 1
        assert details[0]["failure_location"]["function"] == "fail"
        assert details[0]["failure_location"]["line"] > 0
        assert set(details[0]["input_hashes"]) == set(hashes)
        assert details[0]["input_hashes"] == {key: hashlib.sha256(
            json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest() for key, value in hashes.items()}
        assert max(len(r["metrics_json"].encode()) for r in aborted) < 4096
        with pytest.raises(ValueError, match="all three"):
            ordering.release_state(rows, av001.EXPERIMENT_ID, av001.VARIANTS)
    assert len({json.loads(r["metrics_json"])["run_id"] for r in rows[3:]}) == 2
    assert not (repo / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("preflight", ["_registration", "source_hashes"])
def test_design_preflight_failure_does_not_record_evaluation(repo, monkeypatch, preflight):
    planned = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {}, month="2025-11", kind="close")]
    monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
    monkeypatch.setattr(hc003, "price_files", lambda *a, **k: [])
    def fail(*a, **k):
        raise ValueError("fixture preflight failed")
    monkeypatch.setattr(av001, preflight, fail)
    with pytest.raises(ValueError, match="preflight"):
        av001.run_experiment(data_dir=repo, repo_root=repo, sessions=hc003.session_calendar())
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()


def test_publish_refuses_partial_design_before_result_or_registry_writes(repo, monkeypatch):
    result = terminal_design(repo)
    del result["variants"]["av_conc"]
    monkeypatch.setattr(common, "write_results", lambda *a, **k: pytest.fail("partial design was published"))
    with pytest.raises(ValueError, match="all three design variants"):
        av001.publish(result, repo_root=repo)
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()


def test_design_rejects_protected_injected_span_before_any_holdout_claim(repo, monkeypatch):
    prices = prices_for(pd.DatetimeIndex(["2026-01-02"]))
    monkeypatch.setattr(holdout, "guard_period", lambda *a, **k: pytest.fail("design attempted a protected claim"))
    with pytest.raises(ValueError, match="design prices"):
        av001.compute_design(repo_root=repo, data_dir=repo / "data", prices=prices)
    assert not (repo / holdout.LEDGER_PATH).exists()


def test_corrections_with_zero_original_links_make_run_sensitivity_unavailable(repo, monkeypatch):
    write_data(repo / "data")
    corrected = filing("20250515", title="[정정]분기보고서 (2025.03)")
    write_rows(repo / "data/disclosures/dart_full/list/2025/20250515.jsonl", [corrected])
    planned = [{"month": "2025-11", "decision_date": "2025-11-28", "trade_date": "2025-12-01",
                "end_date": "2025-12-30", "end_kind": "close"}]
    monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
    monkeypatch.setattr(av001, "coverage_plan", lambda *a: ({"listing_version": {}},
        [{**planned[0], "listing": {"covered": True}}]))
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    result = av001.compute_design(repo_root=repo, data_dir=repo / "data", prices=prices_for(days), sessions=days)
    assert result["filing_rules"]["O"]["sample_correction_links"]["success"] == 0
    assert result["filing_rules"]["O"]["sample_correction_links"]["failure"] == 1
    assert all(v["verdict"] == "sensitivity_unavailable" for v in result["variants"].values())
    assert all(v["verdict"] == "sensitivity_unavailable" and not v["per_month"]
               and v["judgement_inputs"]["p_for_holm"] == 1 for v in result["rules"]["O"].values())


def test_peak_memory_budget_fails_clearly(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(av001.resource, "getrusage", lambda *a: SimpleNamespace(ru_maxrss=av001.MAX_RSS_BYTES // 1024 + 1))
    with pytest.raises(MemoryError, match="7 GB peak RSS"):
        av001._check_memory()


@pytest.mark.parametrize("n,mean,effect,expected", [(6,.01,.02,"pass"), (6,.009,.02,"fail"), (5,.02,.02,"holdout_insufficient"),
    (6,0,.02,"fail"), (6,.1,0,"fail"), (6,.1,None,"fail"), (6,-.1,.02,"fail")])
def test_holdout_judgement_requires_six_months_own_positive_design_effect_and_no_t_gate(n, mean, effect, expected):
    assert av001.holdout_verdict({"primary": {"count": n, "mean": mean, "t_stat": None}}, effect)[0] == expected


def test_holdout_no_pass_no_open_and_replay_mismatch_before_open(repo, monkeypatch):
    av001.publish(terminal_design(repo, passed=()), repo_root=repo)
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("protected evaluation opened"))
    assert av001.run_holdout(repo_root=repo, data_dir=repo / "data")["verdict"] == "not_applicable"
    av001.publish(terminal_design(repo, passed=("av_all",)), repo_root=repo)
    monkeypatch.setattr(av001, "compute_design", lambda **k: {"source_hashes": {}, "replay_sha256": "different"})
    with pytest.raises(ValueError, match="reproducibility mismatch"):
        av001.run_holdout(repo_root=repo, data_dir=repo / "data")
    assert not (repo / holdout.LEDGER_PATH).exists()


def test_LH002_contamination_terminal_for_each_passing_variant_without_open_or_replay(repo, monkeypatch):
    av001.publish(terminal_design(repo, passed=("av_all", "av_phase")), repo_root=repo)
    ledger = repo / holdout.LEDGER_PATH
    ledger.write_text(json.dumps({"experiment_id": LH002.experiment_id, "recorded_at": "fixture", "from_date": "2026-01-01", "to_date": "2026-06-30"}) + "\n")
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("protected evaluation opened"))
    monkeypatch.setattr(av001, "verify_non_holdout", lambda *a, **k: pytest.fail("replay after contamination"))
    assert av001.run_holdout(repo_root=repo, data_dir=repo / "data")["verdict"] == "holdout_contaminated"
    assert [r["variant"] for r in hc003.registry_rows(repo)[-2:]] == ["holdout_av_all", "holdout_av_phase"]
    assert hc003.ledger_experiments(repo) == {LH002.experiment_id}
    with pytest.raises(ValueError, match="cannot reopen"):
        av001.run_holdout(repo_root=repo, data_dir=repo / "data")


@pytest.mark.parametrize("failure", ["reproduction", "freeze", "enter_before_claim", "enter_after_claim"])
def test_holdout_failure_uses_durable_claim_for_terminal_rows_and_retry(repo, monkeypatch, failure):
    av001.publish(terminal_design(repo, passed=("av_all",)), repo_root=repo)
    design_rows = hc003.registry_rows(repo)
    monkeypatch.setattr(av001, "verify_non_holdout", lambda *a, **k: None)
    monkeypatch.setattr(av001, "coverage_plan", lambda data, planned, sessions: (
        {}, [{"listing": {"covered": True}} for _ in planned]))
    monkeypatch.setattr(av001, "holdout_input_hashes", lambda *a, **k: {"frozen": "a"})
    def computation_failed(**kwargs):
        raise RuntimeError("post-claim computation failed")
    monkeypatch.setattr(av001, "prepare", computation_failed)
    ledger = repo / holdout.LEDGER_PATH
    ledger.write_text(json.dumps(dict(experiment_id="unrelated", recorded_at="fixture",
                                     from_date="2026-01-01", to_date="2026-06-30")) + "\n")
    claim = holdout._claim
    def fail(*args, **kwargs):
        if failure == "enter_after_claim":
            claim(*args, **kwargs)
        raise RuntimeError("fixture failure: " + failure)
    with monkeypatch.context() as patch:
        if failure.startswith("enter_"):
            patch.setattr(holdout, "_claim", fail)
        else:
            patch.setattr(av001, "verify_non_holdout" if failure == "reproduction" else "holdout_input_hashes", fail)
        with pytest.raises(RuntimeError, match="fixture failure"):
            av001.run_holdout(data_dir=repo / "data", repo_root=repo)
    if failure != "enter_after_claim":
        assert hc003.registry_rows(repo) == design_rows
        assert hc003.ledger_experiments(repo) == {"unrelated"}
        with pytest.raises(RuntimeError, match="post-claim computation failed"):
            av001.run_holdout(data_dir=repo / "data", repo_root=repo)
    rows = hc003.registry_rows(repo)
    assert rows[:-1] == design_rows
    assert rows[-1]["variant"] == "holdout_av_all" and rows[-1]["verdict"] == "holdout_aborted"
    assert hc003.ledger_experiments(repo) == {"unrelated", av001.EXPERIMENT_ID}
    assert ordering.release_state(rows, av001.EXPERIMENT_ID, av001.VARIANTS)["terminal_holdout_variants"] == ["av_all"]
    with pytest.raises(ValueError, match="cannot reopen"):
        av001.run_holdout(data_dir=repo / "data", repo_root=repo)


@pytest.mark.parametrize("abort,sensitivity_available,change", [(False, True, None), (False, False, None), (True, True, None),
    (False, True, "entry_price"), (False, True, "price"), (False, True, "financial"),
    (False, True, "company"), (False, True, "listing"), (False, True, "added_price"), (False, True, "removed_price")])
def test_one_fixture_holdout_session_both_rules_close_start_gap_end_no_liquidation_and_abort(repo, monkeypatch, abort, sensitivity_available, change):
    av001.publish(terminal_design(repo), repo_root=repo)
    monkeypatch.setattr(av001, "verify_non_holdout", lambda *a, **k: None)
    monkeypatch.setattr(av001, "coverage_plan", lambda data, planned, sessions: ({}, [{"listing": {"covered": True}} for _ in planned]))
    data = repo / "data"
    protected = data / "market/krx_daily/2026/20260102.jsonl"
    write_rows(protected, [{"trade_date": "20260102", "stock_code": "000010"}])
    listing_checkpoint(data)
    def mutate():
        if change in ("price", "entry_price"):
            write_rows(protected, [{"trade_date": "20260102", "stock_code": "000020"}])
        elif change == "financial":
            write_rows(data / "fundamentals/dart_quarterly/2026_11013.jsonl", [{"changed": True}])
        elif change == "company":
            write_rows(data / "reference/dart_company/companies.jsonl", [{"changed": True}])
        elif change == "listing":
            write_rows(data / "disclosures/dart_full/list/2026/20260102.jsonl", [{"changed": True}])
        elif change == "added_price":
            write_rows(data / "market/krx_daily/2026/20260105.jsonl", [{"trade_date": "20260105"}])
        elif change == "removed_price":
            protected.unlink()
    counts, original_session = [], holdout.HoldoutSession
    def session(*a, **k):
        with pytest.raises(ValueError, match="in progress"):
            with ordering.execution_lock(repo):
                pass
        counts.append(a[0])
        if change == "entry_price":
            mutate()
        return original_session(*a, **k)
    monkeypatch.setattr(holdout, "HoldoutSession", session)
    def prepare(**kwargs):
        assert holdout._SESSIONS.get()[0].experiment_id == av001.EXPERIMENT_ID
        if abort:
            raise ValueError("fixture computation failed")
        if change and change != "entry_price":
            mutate()
        chosen = [av_selection(r["decision_date"], r["trade_date"], r["end_date"], {"a": 2, "b": 4},
                    month=r["month"], kind=r["end_kind"]) for r in kwargs["planned"]]
        chosen[0].update(benchmark_start_date="2025-12-30", benchmark_start_kind="close")
        for row in chosen:
            row["universe"] = row["universe"].assign(operating_income_yoy=30, rcept_no="20250515000001")
        book = TinyBook(repo, chosen)
        book.counts = {}
        class Versions:
            annotations = []
            stage = "holdout"
            def report(self):
                return {}
        Versions.sensitivity_available = sensitivity_available
        class Dates:
            L = 17
        return {"book": book, "selections": {r: chosen for r in av001.RULES}, "counts": {}, "event_counts": {},
                "dates": Dates(), "versions": {r: Versions() for r in av001.RULES}, "listing_status": {"listing_version": {}}}
    monkeypatch.setattr(av001, "prepare", prepare)
    monkeypatch.setattr(hc003, "price_files", lambda *a, **k: [])
    if abort or change:
        with pytest.raises(ValueError, match="computation failed" if abort else "holdout inputs changed"):
            av001.run_holdout(repo_root=repo, data_dir=repo / "data")
        assert all(r["verdict"] == "holdout_aborted" for r in hc003.registry_rows(repo)[-3:])
        details = [json.loads(r["metrics_json"]) for r in hc003.registry_rows(repo)[-3:]]
        assert len({r["run_id"] for r in details}) == 1
        assert all(r["input_hashes"] and r["failure_location"]["line"] > 0 for r in details)
    else:
        result = av001.run_holdout(repo_root=repo, data_dir=repo / "data")
        assert result["input_hash_verification"] == "pre_open == session_before == session_after"
        assert result["source_hashes"]["prices"]["market/krx_daily/2026/20260102.jsonl"] == hc003._digest(protected)
        assert result["verdict"] == ("pass" if sensitivity_available else "sensitivity_unavailable")
        for rule in av001.RULES:
            if rule == "O" and not sensitivity_available:
                assert "zero successful original-filing links" in result["rules"][rule]["av_all"]["reasons"][0]
                assert result["sensitivity_unavailable_reasons"][rule]
                with pytest.raises(ValueError, match="LH002 final blocked"):
                    ordering.release_state(hc003.registry_rows(repo), av001.EXPERIMENT_ID, av001.VARIANTS)
                assert result["rules"][rule]["av_all"]["verdict"] == "sensitivity_unavailable"
                assert result["rules"][rule]["av_all"]["per_month"] == []
                continue
            rows = result["rules"][rule]["av_all"]["per_month"]
            assert len(rows) == 6 and rows[0]["return_denominator"] == 1
            assert rows[0]["pre_value"] == pytest.approx(1.2) and rows[0]["benchmark"] == pytest.approx(.15)
            assert rows[0]["return"] == pytest.approx(.3)
            assert rows[-1]["end_kind"] == "close" and rows[-1]["end_date"] == "2026-06-30"
            assert all(not row["trades"] for row in rows)
    assert counts == [av001.EXPERIMENT_ID] and hc003.ledger_experiments(repo) == {av001.EXPERIMENT_ID}
    with pytest.raises(ValueError, match="cannot reopen"):
        av001.run_holdout(repo_root=repo, data_dir=repo / "data")


def test_holdout_strategy_controls_include_opening_gap_and_January_entry_costs(repo):
    row = av_selection("2025-12-30", "2026-01-02", "2026-02-02", {"a": 2, "b": 2})
    row.update(benchmark_start_date="2025-12-30", benchmark_start_kind="close")
    book = TinyBook(repo, [row])
    book.openings[pd.Timestamp("2026-02-02")] = {"a": quote(1.3), "b": quote(1)}
    initial = {"cash": 0, "quantities": {"a": 1}}
    saved = {"codes": ["a", "b"], "cash": [0] * av001.N_CONTROLS, "quantities": [[1, 0]] * av001.N_CONTROLS,
             "terminal_values": [1] * av001.N_CONTROLS, "rng_state": np.random.default_rng(0).bit_generator.state}
    with holdout.HoldoutSession(av001.EXPERIMENT_ID, repo_root=repo):
        actual = av001._simulate(book, [row], "av_phase", initial_state=initial, initial_value=1)
        control = av001.controls(book, [row], "av_phase", actual, saved=saved)
    first = actual["per_month"][0]
    assert first["pre_value"] == pytest.approx(1.2) and first["return_denominator"] == 1
    assert first["cost"] > 0 and first["benchmark"] == pytest.approx(.15)
    assert first["return"] == pytest.approx(first["end_value"] - 1)
    assert control["path_means"] == pytest.approx([first["excess"]] * av001.N_CONTROLS)
    assert control["per_month"][0]["control_mean"]["cost_fraction"] > 0


def test_all_price_guards_use_AV001_and_default_cli_dry_run_no_outputs(repo, monkeypatch, capsys):
    write_data(repo / "data")
    monkeypatch.setattr(av001, "_registration", lambda *a: experiment_registry.verify_preregistration(av001.EXPERIMENT_ID))
    monkeypatch.setattr(av001, "fixture_timing", lambda: {"seconds": 1, "months": 12, "stocks": 512, "paths": 200})
    def denied(*a, **k):
        pytest.fail("dry run evaluated a local portfolio or financial value")
    monkeypatch.setattr(av001, "compute_design", denied)
    monkeypatch.setattr(dart_quarterly, "_derive", denied)
    monkeypatch.setattr(holdout, "HoldoutSession", denied)
    original_guard, ids = holdout.guard_period, []
    def guard(*a, **k):
        ids.append(k.get("experiment_id"))
        return original_guard(*a, **k)
    monkeypatch.setattr(holdout, "guard_period", guard)
    assert av001.main(["--data-dir", str(repo / "data")]) == 0
    assert ids and set(ids) == {av001.EXPERIMENT_ID}
    output = capsys.readouterr().out
    assert "supplement complete=False" in output and "L=0 days" in output and "data_incomplete" in output
    assert not (repo / holdout.LEDGER_PATH).exists() and not (repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("mode", ["design", "dry_run"])
@pytest.mark.parametrize("frozen", [False, True])
def test_design_and_dry_run_never_open_protected_price_files(repo, monkeypatch, mode, frozen):
    import builtins
    data = repo / "data"
    write_data(data)
    protected = data / "market/krx_daily/2026/20260102.jsonl"
    write_rows(protected, [{"trade_date": "20260102", "stock_code": "000010"}])
    state = listing_checkpoint(data)
    if not frozen:
        del state["target_scan"]
        (data / "disclosures/dart_full/list_supplement/_state.json").write_text(json.dumps(state))
    # A refreshed reference introduces an uncovered delisted corporation. The
    # old reader would open 2026 prices to establish its historical trading.
    (data / "reference/corp_codes.csv").write_text("corp_code,stock_code\n00000002,000010\n")
    write_rows(data / "reference/dart_company/companies.jsonl", [{"corp_code": "00000002", "corp_cls": "E"}])
    path_open, builtin_open = Path.open, builtins.open
    def deny(path):
        if isinstance(path, (str, Path)) and Path(path) == protected:
            assert holdout._SESSIONS.get(), "protected price opened outside a HoldoutSession"
    def checked_path(path, *a, **k):
        deny(path)
        return path_open(path, *a, **k)
    def checked_builtin(path, *a, **k):
        deny(path)
        return builtin_open(path, *a, **k)
    monkeypatch.setattr(Path, "open", checked_path)
    monkeypatch.setattr(builtins, "open", checked_builtin)
    monkeypatch.setattr(av001, "fixture_timing", lambda: {"seconds": 1, "months": 12, "stocks": 512, "paths": 200})
    if mode == "dry_run":
        result = av001.dry_run(data_dir=data, repo_root=repo)
    else:
        planned = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {}, kind="close")]
        monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
        days = hc003.session_calendar()
        days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
        result = av001.compute_design(data_dir=data, repo_root=repo, prices=prices_for(days), sessions=days)
    status = result["listing_status"]
    assert not status["supplement_complete"]
    assert status["reason"] == ("supplement_targets_missing" if frozen else "supplement_reference_unavailable")
    assert not (repo / holdout.LEDGER_PATH).exists()


def test_replay_depends_on_content_and_results_but_not_HEAD(repo, monkeypatch):
    write_data(repo / "data")
    planned = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {}, kind="close")]
    monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    def compute():
        return av001.compute_design(data_dir=repo / "data", repo_root=repo, prices=prices_for(days), sessions=days)
    first = compute()
    with ordering.design_attempt(av001.EXPERIMENT_ID, av001.VARIANTS, repo) as attempt:
        protected = av001.compute_design(data_dir=repo / "data", repo_root=repo, sessions=days,
                                         prices=prices_for(days), _attempt=attempt)
    assert json.dumps({k: v for k, v in protected.items() if k != "performance"}, sort_keys=True).encode() == json.dumps(
        {k: v for k, v in first.items() if k != "performance"}, sort_keys=True).encode()
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()
    monkeypatch.setattr(experiment_registry, "_git", lambda *a: "a-result-only-commit")
    second = compute()
    assert first["code_commit"] != second["code_commit"]
    assert first["source_hashes"] == second["source_hashes"]
    assert first["replay_sha256"] == second["replay_sha256"]
    monkeypatch.setattr(av001, "compute_design", lambda **k: second)
    av001.verify_non_holdout(first, data_dir=repo / "data", repo_root=repo, sessions=days)
    for field in ("code", "financials"):
        changed = copy.deepcopy(second)
        changed["source_hashes"][field]["changed.py"] = "different-content"
        assert av001.replay_hash(changed) != first["replay_sha256"]
        monkeypatch.setattr(av001, "compute_design", lambda **k: changed)
        with pytest.raises(ValueError, match="reproducibility mismatch"):
            av001.verify_non_holdout(first, data_dir=repo / "data", repo_root=repo, sessions=days)
    changed = copy.deepcopy(second)
    changed["design_effects"]["S"]["av_phase"] = 123
    assert av001.replay_hash(changed) != first["replay_sha256"]


def test_new_design_trial_with_current_code_becomes_latest_reproduction_baseline(repo, monkeypatch):
    data = repo / "data"
    write_data(data)
    days = hc003.session_calendar()
    days = days[(days >= "2025-08-01") & (days <= "2025-12-30")]
    planned = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {}, kind="close")]
    monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: (planned, []))
    for day, frame in prices_for(days).reset_index().groupby("trade_date"):
        frame = frame.assign(trade_date=day.strftime("%Y%m%d"), change_rate_pct=frame.ret_1d * 100).drop(columns="ret_1d")
        write_rows(data / "market/krx_daily" / str(day.year) / (day.strftime("%Y%m%d") + ".jsonl"), frame.to_dict("records"))
    hashes = av001.source_hashes
    code_path = "backtesting/experiments/av001.py"
    def old_code(*args, **kwargs):
        result = hashes(*args, **kwargs)
        result["code"][code_path] = "previous-code"
        return result
    with monkeypatch.context() as patch:
        patch.setattr(av001, "source_hashes", old_code)
        av001.run_experiment(data_dir=data, repo_root=repo, sessions=days)
    old, old_run = av001.saved_design(hc003.registry_rows(repo), repo_root=repo, registration=av001._registration(repo))
    with pytest.raises(ValueError, match="code hashes differ; run a new design/validation trial with this code first"):
        av001.verify_non_holdout(old, data_dir=data, repo_root=repo, sessions=days)
    assert len(hc003.registry_rows(repo)) == 3 and not (repo / holdout.LEDGER_PATH).exists()
    current = av001.run_experiment(data_dir=data, repo_root=repo, sessions=days)
    saved, run = av001.saved_design(hc003.registry_rows(repo), repo_root=repo, registration=av001._registration(repo))
    assert saved == current and run["result_file"] != old_run["result_file"]
    assert saved["source_hashes"]["code"][code_path] == hc003._digest(Path(av001.__file__))
    assert saved["trial_count_after_run"] == experiment_registry.trial_count(av001.EXPERIMENT_ID, repo_root=repo) == 6
    av001.verify_non_holdout(saved, data_dir=data, repo_root=repo, sessions=days)
    assert len(hc003.registry_rows(repo)) == 6 and not (repo / holdout.LEDGER_PATH).exists()


def test_holdout_preopen_freezes_protected_hashes_without_opening_prices(repo, monkeypatch):
    data = repo / "data"
    path = data / "market/krx_daily/2026/20260102.jsonl"
    write_rows(path, [{"trade_date": "20260102", "stock_code": "000010"}])
    state = listing_checkpoint(data)
    original_open = Path.open
    def checked_open(p, *a, **k):
        assert p != path, "pre-open record opened protected prices"
        return original_open(p, *a, **k)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", checked_open)
        frozen = av001.holdout_input_hashes(data, repo, before_open=True)
        assert frozen["prices"] == state["target_scan"]["price_files"]
        with pytest.raises(ValueError, match="requires an open AV001 HoldoutSession"):
            av001.holdout_input_hashes(data, repo)
    with holdout.HoldoutSession(av001.EXPERIMENT_ID, repo_root=repo):
        assert av001.holdout_input_hashes(data, repo) == frozen


def test_holdout_unavailable_prints_user_decision_and_keeps_terminal_list(monkeypatch, capsys):
    result = {"stage": "holdout", "verdict": "sensitivity_unavailable",
              "variants": {"av_all": {"verdict": "sensitivity_unavailable"}},
              "sensitivity_unavailable_reasons": {"S": [], "O": ["zero original links"]}}
    monkeypatch.setattr(av001, "run_holdout", lambda **k: result)
    assert av001.main(["--holdout"]) == 0
    message = capsys.readouterr().out
    assert "USER DECISION REQUIRED" in message and "LH002 remains blocked" in message
    assert "zero original links" in message
    assert "sensitivity_unavailable" not in ordering.TERMINAL


def test_band_skipped_values_separate_buys_sells_and_match_each_control_path(repo):
    codes = ["a", "b"]
    quotes = dict.fromkeys(codes, quote(1))
    state = hc003.PortfolioState(cash=0, quantities={"a": .5004, "b": .4996})
    actual_state, actual = hc003.rebalance(state, quotes, codes, "2025-09-01")
    _, _, report = hc003.rebalance_paths([0], [[.5004, .4996]], codes, quotes, [[True, True]], "2025-09-01")
    assert not actual["trades"] and actual_state.to_dict() == state.to_dict()
    for side in ("buy", "sell"):
        key = "band_skipped_" + side + "_value"
        assert actual[key] == pytest.approx(.0004)
        assert report[key].tolist() == [actual[key]]
    # A full exit is exempt from the band; frozen positions and blocked buys
    # are not counted as orders skipped because of the band.
    _, exited = hc003.rebalance(state, quotes, ["a"], "2025-09-01")
    assert exited["band_skipped_sell_value"] == 0 and exited["sell_amount"] > 0
    _, blocked = hc003.rebalance(state, quotes, codes, "2025-09-01", blocked_buys=["b"])
    assert blocked["band_skipped_buy_value"] == 0
    row = av_selection("2025-08-29", "2025-09-01", "2025-10-01", {"a": 2, "b": 2})
    book = TinyBook(repo, [row])
    book.openings[pd.Timestamp(row["trade_date"])] = quotes
    book.openings[pd.Timestamp(row["end_date"])] = book.openings[pd.Timestamp(row["trade_date"])]
    simulation = av001._simulate(book, [row], "av_phase", initial_state=state.to_dict())
    saved = {"codes": codes, "cash": [0] * av001.N_CONTROLS,
             "quantities": [[.5004, .4996]] * av001.N_CONTROLS,
             "terminal_values": [1] * av001.N_CONTROLS,
             "rng_state": np.random.default_rng(0).bit_generator.state}
    controls = av001.controls(book, [row], "av_phase", simulation, saved=saved)
    month = controls["per_month"][0]
    for side in ("buy", "sell"):
        key = "band_skipped_" + side + "_value"
        assert month["strategy"][key] > 0
        assert month["control_mean"][key] == pytest.approx(month["strategy"][key])
        assert month["band_skipped_by_path"][side] == pytest.approx([month["strategy"][key]] * av001.N_CONTROLS)


@pytest.mark.skipif(os.getenv("RUN_AV001_HC003_ARCHIVE_CHECK") != "1", reason="optional historical HC003 accounting comparison; no holdout")
def test_optional_committed_HC003_parameterised_and_streaming_replay(monkeypatch):
    path = experiment_registry.PROJECT_ROOT / "research/experiments/HC003_operating_leverage_turnover/results/20261010T021152472126Z.json.gz"
    saved = common.read_result_json(path)
    sessions = hc003.session_calendar()
    data = experiment_registry.PROJECT_ROOT / "data"
    original_open = Path.open
    def checked_open(path, *a, **k):
        if path.is_relative_to(data / "market/krx_daily"):
            assert "20170101" <= path.stem <= "20251230", "archive replay opened out-of-range prices"
        return original_open(path, *a, **k)
    monkeypatch.setattr(Path, "open", checked_open)
    for streamed, count in ((False, 3), (True, len(saved["selections"]))):
        rows = saved["selections"][:count]
        end = pd.Timestamp(rows[-1]["end_date"])
        planned = []
        for row in rows:
            u = universe({code: 2 if code in row["P"] else 1 for code in row["U"]})
            planned.append({**row, "universe": u, "F_vol": set(), "F_raise": set(), "blocked_buys": set(), "filter_covered": True})
        rebalances = [r["trade_date"] for r in planned] + [r["end_date"] for r in planned if r["end_kind"] == "open"]
        def load(start, stop):
            prices, files = hc003.load_prices(data, start, stop, experiment_id=av001.EXPERIMENT_ID)
            assert all(hc003._digest(p) == saved["source_hashes"]["krx_daily"][p.relative_to(data).as_posix()]
                       for _, p in files), "HC003 input price snapshot has changed"
            return prices
        if streamed:
            days = sessions[(sessions >= "2017-01-01") & (sessions <= end)]
            def windows():
                for quarter in sorted(set(days.to_period("Q"))):
                    local = days[days.to_period("Q") == quarter]
                    yield load(local[0], local[-1])
            book = hc003.PriceBook.from_windows(windows(), days, rebalances, end, experiment_id=av001.EXPERIMENT_ID)
        else:
            book = hc003.PriceBook(load("20170101", end), sessions, rebalances, end, experiment_id=av001.EXPERIMENT_ID)
        actual = av001._simulate(book, planned, "av_conc")
        differences = [abs(r["excess"] - expected["excess"]) for r, expected in
                       zip(actual["per_month"], saved["variants"]["t_hold"]["per_month"][:count])]
        print(json.dumps({"path": "quarterly_streaming" if streamed else "full_book", "months_compared": count,
                          "max_abs_monthly_e_difference": max(differences)}))
        assert max(differences) <= 1e-12
