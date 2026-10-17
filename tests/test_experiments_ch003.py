"""Hand-calculated CH003 MA, cash weights, joint shifts and NW fixtures."""
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import ch001, ch003, common, hf001
from src.ingestion.storage import write_rows
from test_experiments_ch001 import offline_repo, prepared_fixture, selection
from test_experiments_ch002 import runner_repo, protect_fixture, trap_price_opens


def index_frame(days, close=100.):
    index = pd.MultiIndex.from_product([days, ch003.MARKETS], names=["trade_date", "market"])
    return pd.DataFrame({"close": close, "available_at": "2026-10-17T00:00:00+00:00"}, index=index)


def flags_for(selections, flags=None):
    flags = flags or [[False, False] for _ in selections]
    return [{"month": s["month"], "decision_date": s["decision_date"], "markets": {
        m: {"bearish": bool(flags[i][col]), "computable": True} for col, m in enumerate(ch003.MARKETS)}} for i, s in enumerate(selections)]


def observations_for(selections, nets, *, markets=("KOSPI", "KOSDAQ"), filled=None):
    rows = []
    for i, s in enumerate(selections):
        for j, code in enumerate(sorted(s["universe"].index)):
            rows.append({"decision_date": pd.Timestamp(s["decision_date"]), "stock_code": code,
                "decision_market": markets[j], "gross": nets[i][j], "gross_delisting_loss": nets[i][j],
                "filled": True if filled is None else filled[j], **{f"cost_{m}": 0. for m in ch001.MULTIPLIERS}})
    return pd.DataFrame(rows)


def save_indices(data, days):
    write_rows(data / "market_context/benchmarks.jsonl", [{"series": m, "index_name": ch003.INDEX_NAMES[m],
        "trade_date": str(day.date()), "close": 100., "available_at": "2026-10-17T00:00:00+00:00"} for day in days for m in ch003.MARKETS])


def test_one_delisting_changes_real_shift_controls_and_final_verdict(runner_repo):
    selections = [selection(pd.Period(month).end_time.normalize(), ["a", "b"])
                  for month in ch003.MONTHS]
    flags = np.zeros((len(selections), 2), dtype=bool)
    flags[np.random.default_rng(7).choice(len(selections), size=60, replace=False), 0] = True
    regimes = flags_for(selections, flags.tolist())
    gross = np.where(flags[:, 0], -.002 * (1 + np.arange(len(selections)) % 3 / 10), .002)
    frame = observations_for(selections, [[value, .001] for value in gross])
    lost_month = np.flatnonzero(~flags[:, 0])[0]
    frame.loc[2 * lost_month, "gross_delisting_loss"] = -1.
    assert (frame.gross_delisting_loss != frame.gross).sum() == 1
    main, loss = ch003.evaluate_rule(frame, selections, regimes, ch003._registration(runner_repo).fields, 1)
    expected_d = np.where(flags[:, 0], -gross / 2, 0.)
    # The actual filter retains the delisted name in this month, so its loss
    # cancels from d. Some joint shifts move that slot to cash and avoid it.
    for result in (main, loss):
        assert [r["costs"]["1.0"]["difference"] for r in result["per_month"]] == pytest.approx(expected_d)
    expected_controls, expected_loss_controls = [], []
    for shift in range(1, len(selections)):
        shifted = np.roll(flags, shift, axis=0)[:, 0]
        mean = -np.mean(np.where(shifted, gross, 0.)) / 2
        expected_controls.append(mean)
        expected_loss_controls.append(mean + (1.002 / 2 / len(selections) if shifted[lost_month] else 0.))
    assert [p["mean"] for p in main["random_control"]["paths"]] == pytest.approx(expected_controls)
    assert [p["mean"] for p in loss["random_control"]["paths"]] == pytest.approx(expected_loss_controls)
    assert any(a == b for a, b in zip(expected_controls, expected_loss_controls))
    assert any(a < b for a, b in zip(expected_controls, expected_loss_controls))
    assert main["random_control"]["share_of_controls"] <= .05 < loss["random_control"]["share_of_controls"]
    assert main["auxiliary_control"]["draws_sha256"] == loss["auxiliary_control"]["draws_sha256"]
    assert all(r["p_one_sided"] <= .05 for r in main["nw"].values())
    assert main["base_verdict"] == "pass" and loss["verdict"] == "fail"
    assert main["verdict"] == ch001.av001.combine_verdict(main["verdict"], main["verdict"]) == "delisting_sensitive"


def test_index_replacement_after_preparation_aborts_without_publication(runner_repo, monkeypatch):
    registration = experiment_registry.verify_preregistration(ch003.EXPERIMENT_ID, runner_repo)
    data, prices, days = prepared_fixture(runner_repo)
    save_indices(data, days)
    monkeypatch.setattr(ch003, "MONTHS", ("2024-05", "2024-06"))
    trap_price_opens(monkeypatch)
    path = data / "market_context/benchmarks.jsonl"
    initial_hash = hf001._digest(path)
    original = ch003.load_indices
    def indices(path):
        result = original(path)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes() + b"\n")
        replacement.replace(path)
        return result
    monkeypatch.setattr(ch003, "load_indices", indices)
    monkeypatch.setattr(ch003, "publish", lambda *a, **k: pytest.fail("published changed indices"))
    with pytest.raises(ValueError, match="inputs or code changed"):
        ch003.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    rows = ch001.av001.hc003.registry_rows(runner_repo)
    assert len(rows) == 1 and rows[0]["verdict"] == "design_aborted"
    assert rows[0]["prereg_commit"] == registration.commit_hash
    expected = hashlib.sha256(json.dumps({"market_context/benchmarks.jsonl": initial_hash}, sort_keys=True).encode()).hexdigest()
    assert json.loads(rows[0]["metrics_json"])["input_hashes"]["index"] == expected


def test_reference_hash_failure_stops_design_and_dry_before_other_inputs(runner_repo, monkeypatch):
    path = runner_repo / "research/experiments" / ch001.EXPERIMENT_ID / "preregistration.md"
    path.write_text(path.read_text() + "\nchanged")
    monkeypatch.setattr(ch001, "_registration", lambda *a: pytest.fail("hash did not stop first"))
    for function in (ch003.run_experiment, ch003.dry_run):
        with pytest.raises(ValueError, match="reference SHA-256 mismatch"):
            function(repo_root=runner_repo)


def test_latest_saved_index_versions_ignore_contemporaneous_as_of_and_2026_inputs(tmp_path):
    path = tmp_path / "benchmarks.jsonl"
    base = {"series": "KOSPI", "index_name": "코스피", "trade_date": "2024-05-31", "close": 100., "available_at": "2026-10-16T00:00:00Z"}
    write_rows(path, [dict(base, close=90.), dict(base, close=95., available_at="2026-10-17T00:00:00Z"),
        dict(base, trade_date="2026-01-02", close=999.), dict(base, index_name="다른 지수", close=500.)])
    frame, inventory = ch003.load_indices(path)
    assert frame.loc[(pd.Timestamp("2024-05-31"), "KOSPI"), "close"] == 95.
    assert frame.index.get_level_values("trade_date").max().year == 2024
    assert inventory["ignored"] == {"after_final_decision": 1, "2026_rows_not_used": 1, "other_series_or_name": 1}
    assert inventory["sha256"] == hf001._digest(path)


@pytest.mark.parametrize("superseded", [False, True])
def test_index_version_conflict_including_superseded_timestamp_stops(tmp_path, superseded):
    path = tmp_path / "benchmarks.jsonl"
    base = {"series": "KOSDAQ", "index_name": "코스닥", "trade_date": "2024-05-31", "close": 100., "available_at": "2026-10-16T00:00:00Z"}
    rows = [base, dict(base, close=101., available_at="2026-10-16T09:00:00+09:00")]
    if superseded: rows.insert(1, dict(base, available_at="2026-10-17T00:00:00Z"))
    write_rows(path, rows)
    with pytest.raises(ValueError, match="index version conflict"):
        ch003.load_indices(path)


def test_duplicate_same_close_allowed_and_timestamp_requires_timezone(tmp_path):
    path = tmp_path / "benchmarks.jsonl"
    base = {"series": "KOSPI", "index_name": "코스피", "trade_date": "2024-05-31", "close": 100., "available_at": "2026-10-16T00:00:00Z"}
    write_rows(path, [base, base])
    assert len(ch003.load_indices(path)[0]) == 1
    write_rows(path, [dict(base, available_at="2026-10-16T00:00:00")])
    with pytest.raises(ValueError, match="aware timestamp"):
        ch003.load_indices(path)


def test_MA200_exact_contiguous_decision_inclusive_window_and_equality_not_bearish():
    days = pd.bdate_range("2023-01-02", periods=230)
    plan = [{"month": "2023-10", "decision_date": str(days[210].date())}]
    frame = index_frame(days)
    regimes = ch003.market_regimes(frame, plan, days)
    assert regimes[0]["markets"]["KOSPI"]["ma200"] == 100.
    assert not regimes[0]["markets"]["KOSPI"]["bearish"]
    frame.loc[(days[210], "KOSDAQ"), "close"] = 99.
    frame.loc[(days[211:], "KOSDAQ"), "close"] = 1.  # future is never in MA
    regimes = ch003.market_regimes(frame, plan, days)
    assert regimes[0]["markets"]["KOSDAQ"]["ma200"] == (199 * 100 + 99) / 200
    assert regimes[0]["markets"]["KOSDAQ"]["bearish"]
    frame.loc[(days[10], "KOSDAQ"), "close"] = np.nan  # just before the 200-session window
    assert ch003.market_regimes(frame, plan, days)[0]["markets"]["KOSDAQ"]["computable"]
    frame.loc[(days[11], "KOSDAQ"), "close"] = np.nan
    assert not ch003.market_regimes(frame, plan, days)[0]["markets"]["KOSDAQ"]["computable"]


@pytest.mark.parametrize("bad", [np.nan, np.inf, 0., -1.])
def test_invalid_close_disables_only_that_market_without_extending_window(bad):
    days = pd.bdate_range("2023-01-02", periods=220)
    frame = index_frame(days)
    frame.loc[(days[100], "KOSPI"), "close"] = bad
    result = ch003.market_regimes(frame, [{"month": "2023-10", "decision_date": str(days[210].date())}], days)[0]
    assert not result["markets"]["KOSPI"]["computable"] and not result["markets"]["KOSPI"]["bearish"]
    assert result["markets"]["KOSPI"]["invalid_sessions"] == 1
    assert result["markets"]["KOSDAQ"]["computable"]


def test_fixed_B_weights_cash_and_active_means_would_be_filled_name():
    s = selection("2024-05-31", ["a", "b", "c"])
    obs = observations_for([s], [[-.1, .2, 0.]], markets=("KOSPI", "KOSDAQ", "KOSDAQ"), filled=[True, True, False])
    regimes = flags_for([s], [[True, False]])
    row = ch003.monthly(obs, [s], regimes)[0]
    assert row["weights"] == {"a": 1/3, "b": 1/3, "c": 1/3}
    assert row["cash_weight"] == 2/3 and row["active"]
    assert row["costs"]["1.0"]["filtered_net"] == pytest.approx(.2 / 3)
    assert row["costs"]["1.0"]["difference"] == pytest.approx(.1 / 3)
    # Only an already-unfilled slot lies in the bearish market.
    obs.decision_market = ["KOSPI", "KOSPI", "KOSDAQ"]
    row = ch003.monthly(obs, [s], flags_for([s], [[False, True]]))[0]
    assert not row["active"] and row["costs"]["1.0"]["difference"] == 0.
    row = ch003.monthly(obs, [s], flags_for([s], [[True, True]]))[0]
    assert row["cash_weight"] == 1. and row["costs"]["1.0"]["filtered_net"] == 0.


def test_joint_circular_shift_hand_values_ties_rank_share_and_spells():
    selections = [selection(str(day.date()), ["a", "b"]) for day in pd.date_range("2024-01-31", periods=4, freq="ME")]
    obs = observations_for(selections, [[-.4, -.2], [.1, .5], [.3, -.4], [-.2, .1]])
    regimes = flags_for(selections, [[True, True], [True, False], [False, False], [False, True]])
    rows = ch003.monthly(obs, selections, regimes)
    result = ch003.circular_controls(rows, regimes)
    assert result["actual_mean"] == pytest.approx(.05)
    assert [r["mean"] for r in result["paths"]] == pytest.approx([-.0875, -.025, .1125])
    assert result["count_ge_actual"] == 1 and result["share_of_controls"] == 1/3
    assert result["self_inclusive_rank_share"] == .5
    assert all(p["months"] == 4 and all(nw["count"] == 4 for nw in p["nw"].values()) for p in result["paths"])
    summary = ch003.regime_summary(regimes)
    assert summary["KOSPI"]["spell_lengths"] == [2]
    assert summary["KOSDAQ"]["spell_lengths"] == [1, 1]
    assert summary["joint_bearish_months"] == 1
    all_off = flags_for(selections)
    result = ch003.circular_controls(ch003.monthly(obs, selections, all_off), all_off)
    assert result["share_of_controls"] == result["self_inclusive_rank_share"] == 1.


def test_auxiliary_control_golden_one_RNG_per_rule_path_market_order_zero_all(monkeypatch):
    monkeypatch.setattr(ch003, "N_CONTROLS", 4)
    selections = [selection(str(day.date()), ["a", "b"]) for day in pd.date_range("2024-01-31", periods=4, freq="ME")]
    obs = observations_for(selections, [[-.4, -.2], [.1, .5], [.3, -.4], [-.2, .1]])
    regimes = flags_for(selections, [[False, True]] * 4)
    rows = ch003.monthly(obs, selections, regimes)
    original, seeds, calls = np.random.default_rng, [], []
    def tracked(seed):
        seeds.append(seed)
        rng = original(seed)
        def choice(candidates, *, size, replace):
            calls.append((list(candidates), size, replace))
            return rng.choice(candidates, size=size, replace=replace)
        return SimpleNamespace(choice=choice)
    monkeypatch.setattr(np.random, "default_rng", tracked)
    actual = ch003.auxiliary_controls(rows, regimes)
    assert seeds == [0] and calls == [([0, 1, 2, 3], 0, False), ([0, 1, 2, 3], 4, False)] * 4
    rng, digest = original(0), hashlib.sha256()
    for path in range(4):
        for m, k in zip(ch003.MARKETS, (0, 4)):
            drawn = rng.choice([0, 1, 2, 3], size=k, replace=False)
            digest.update((json.dumps([path, m, drawn.tolist()], separators=(",", ":")) + "\n").encode())
    assert actual["draws_sha256"] == digest.hexdigest() and not actual["used_for_judgement"]
    assert actual["share_of_controls"] == 1.


@pytest.mark.parametrize("lag,variance", [(1, 25/16), (6, 17/28), (12, 17/52)])
def test_NW_hand_computed_Bartlett_all_lags_no_finite_sample_correction(lag, variance):
    result = ch001.newey_west([1., 2., 3., 4.], lag=lag)
    assert result["long_run_variance"] == pytest.approx(variance)
    assert result["standard_error"] == pytest.approx(math.sqrt(variance / 4))
    assert result["t_stat"] == pytest.approx(2.5 / math.sqrt(variance / 4))
    assert result["p_one_sided"] == pytest.approx(.5 * math.erfc(result["t_stat"] / math.sqrt(2)))


def metrics_fixture():
    nw = {"count": 103, "mean": .01, "std": .02, "standard_error": .002, "long_run_variance": .0004, "t_stat": 5., "p_one_sided": .000001}
    r = ch003.unavailable_metrics("fixture")
    r.pop("unavailable")
    r.update(nw={str(lag): copy.deepcopy(nw) for lag in ch003.LAGS}, primary=copy.deepcopy(nw),
        complete_T=True, filtered_months=40, random_control={"share_of_controls": .05},
        risk={"filtered": {"volatility": .02, "lower_5pct_mean": -.02, "max_drawdown": .1},
              "baseline": {"volatility": .03, "lower_5pct_mean": -.03, "max_drawdown": .2}})
    r["cost_sensitivity"]["1.5"]["difference"] = .005
    return r


@pytest.mark.parametrize("trials,t,verdict", [(20, 2., "fail"), (20, 2.01, "pass"), (21, 3., "fail"), (21, 3.01, "pass")])
def test_every_NW_lag_needs_strict_t_after_one_current_row(runner_repo, trials, t, verdict):
    r = metrics_fixture()
    r["nw"]["6"]["t_stat"] = t
    assert ch003.judge(r, ch003._registration(runner_repo).fields, trials)["verdict"] == verdict


@pytest.mark.parametrize("lag", [1, 6, 12])
@pytest.mark.parametrize("trials,threshold", [(20, 2.), (21, 3.)])
@pytest.mark.parametrize("failure,expected", [("t_below", "fail"), ("t_equal", "fail"),
    ("p", "fail"), ("SE", "insufficient")])
def test_each_NW_lag_independently_requires_t_p_and_defined_SE(runner_repo, lag, trials, threshold, failure, expected):
    fields = ch003._registration(runner_repo).fields
    assert ch003.judge(metrics_fixture(), fields, trials)["verdict"] == "pass"
    r = metrics_fixture()
    nw = r["nw"][str(lag)]
    if failure == "t_below": nw["t_stat"] = threshold - .01
    elif failure == "t_equal": nw["t_stat"] = threshold
    elif failure == "p": nw["p_one_sided"] = .05001
    else: nw["standard_error"] = None
    assert ch003.judge(r, fields, trials)["verdict"] == expected


@pytest.mark.parametrize("mean", [0., -.01])
@pytest.mark.parametrize("max_drawdown", [.2, .21])
def test_risk_only_requires_strict_max_drawdown_improvement(runner_repo, mean, max_drawdown):
    fields = ch003._registration(runner_repo).fields
    r = metrics_fixture()
    r["primary"]["mean"] = mean
    assert ch003.judge(copy.deepcopy(r), fields, 1)["verdict"] == "risk_only"
    r["risk"]["filtered"]["max_drawdown"] = max_drawdown
    assert ch003.judge(r, fields, 1)["verdict"] == "fail"


@pytest.mark.parametrize("failure,expected", [("T", "insufficient"), ("active", "insufficient"), ("SE", "insufficient"),
    ("p12", "fail"), ("cost", "fail"), ("control", "fail"), ("risk", "risk_only")])
def test_judgement_all_gates_and_all_three_risk_improvements(runner_repo, failure, expected):
    r = metrics_fixture()
    if failure == "T": r["complete_T"] = False
    elif failure == "active": r["filtered_months"] = 35
    elif failure == "SE": r["nw"]["12"].update(standard_error=0., t_stat=None)
    elif failure == "p12": r["nw"]["12"]["p_one_sided"] = .05001
    elif failure == "cost": r["cost_sensitivity"]["1.5"]["difference"] = 0.
    elif failure == "control": r["random_control"]["share_of_controls"] = .05001
    else: r["primary"]["mean"] = 0.
    assert ch003.judge(r, ch003._registration(runner_repo).fields, 1)["verdict"] == expected


def test_one_uncomputable_market_month_in_103_never_compresses_T(runner_repo):
    dates = pd.period_range("2017-05", "2025-11", freq="M").to_timestamp("M")
    selections = [selection(str(day.date()), ["a", "b"]) for day in dates]
    obs = observations_for(selections, [[-.1, .1]] * 103)
    regimes = flags_for(selections, [[True, False]] * 103)
    regimes[20]["markets"]["KOSDAQ"].update(computable=False, bearish=False)
    result = ch003.variant_metrics(obs, selections, regimes)
    assert len(result["per_month"]) == 103 and not result["complete_T"]
    assert result["primary"]["count"] == 0 and result["filter_off_diagnostic"]["primary"]["count"] == 103
    assert all(p["months"] == 103 for p in result["random_control"]["paths"])
    assert not result["random_control"]["used_for_judgement"]
    assert ch003.judge(result, ch003._registration(runner_repo).fields, 1)["verdict"] == "insufficient"
    assert len(ch003.MONTHS) == 103 and ch003.MONTHS[0] == "2017-05" and ch003.MONTHS[-1] == "2025-11"


@pytest.mark.parametrize("prior", [19, 20])
def test_fixture_execute_one_compact_row_HF001_costs_and_provenance(runner_repo, monkeypatch, prior):
    data, prices, days = prepared_fixture(runner_repo)
    save_indices(data, days)
    monkeypatch.setattr(ch003, "MONTHS", ("2024-05", "2024-06"))
    for _ in range(prior): experiment_registry.record_trial(ch003.EXPERIMENT_ID, "prior", {}, "fail", repo_root=runner_repo)
    result = ch003.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert experiment_registry.trial_count(ch003.EXPERIMENT_ID, repo_root=runner_repo) == prior + 1
    for rule in ch001.RULES:
        r = result["rules"][rule][ch003.VARIANT]
        assert r["judgement_inputs"]["trial_count_after_run"] == prior + 1
        assert r["judgement_inputs"]["t_threshold"] == (2. if prior == 19 else 3.)
        assert result["assertions"][rule]["exact_names_weights_cash_cost_net"]
        assert all(row["costs"]["1.0"]["difference"] == 0. for row in r["per_month"])
        assert r["auxiliary_control"]["draws_sha256"] == result["delisting_sensitivity"][rule][ch003.VARIANT]["auxiliary_control"]["draws_sha256"]
    assert result["source_hashes"]["index"]["market_context/benchmarks.jsonl"] == hf001._digest(data / "market_context/benchmarks.jsonl")
    assert result["source_hashes"]["preregistrations"][ch001.EXPERIMENT_ID] == result["reference_sha256"]


@pytest.mark.parametrize("mode", ["design", "dry_run"])
@pytest.mark.parametrize("frozen,refresh", [(False, True), (True, True), (True, False)])
def test_design_and_dry_run_never_open_protected_price_files(runner_repo, monkeypatch, mode, frozen, refresh):
    data, prices, days = protect_fixture(runner_repo, frozen=frozen, refresh=refresh)
    save_indices(data, days)
    trap_price_opens(monkeypatch)
    original = ch001.calendar_plan
    monkeypatch.setattr(ch001, "calendar_plan", lambda sessions=None: original(days if sessions is None else sessions))
    monkeypatch.setattr(ch003, "MONTHS", ("2024-05", "2024-06"))
    original_hashes = ch001.source_hashes
    def hashes(data_dir, paths, **kwargs):
        assert all(day.year < 2026 for day, _ in paths)
        if kwargs.get("prices") is not None: assert common._sessions(kwargs["prices"]).max().year < 2026
        return original_hashes(data_dir, paths, **kwargs)
    monkeypatch.setattr(ch001, "source_hashes", hashes)
    if mode == "dry_run":
        monkeypatch.setattr(ch003, "evaluate_rule", lambda *a, **k: pytest.fail("dry-run evaluation"))
        monkeypatch.setattr(ch003, "publish", lambda *a, **k: pytest.fail("dry-run publication"))
        result = ch003.dry_run(data_dir=data, repo_root=runner_repo)
    else:
        result = ch003.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert result["listing_status"]["supplement_complete"] == (frozen and not refresh)
    assert not (runner_repo / holdout.LEDGER_PATH).exists()


def test_cli_default_dry_and_no_holdout(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ch003, "dry_run", lambda: calls.append("dry") or {})
    monkeypatch.setattr(ch003, "run_experiment", lambda: calls.append("execute") or {"variants": {}})
    for argv in ([], ["--dry-run"], ["--execute"]): assert ch003.main(argv) == 0
    assert calls == ["dry", "dry", "execute"]
    with pytest.raises(SystemExit): ch003.main(["--holdout"])
    with pytest.raises(SystemExit): ch003.main(["--execute", "--dry-run"])


@pytest.mark.parametrize("day", ["2026-01-02", "2026-07-01"])
def test_design_rejects_any_2026_evaluation_price_before_forward_or_holdout(runner_repo, monkeypatch, day):
    data, prices, days = prepared_fixture(runner_repo)
    extra = prices.iloc[:1].copy()
    extra.index = pd.MultiIndex.from_tuples([(pd.Timestamp(day), "000010")], names=prices.index.names)
    prices = pd.concat([prices, extra]).sort_index()
    trap_price_opens(monkeypatch)
    monkeypatch.setattr(ch001, "forward_observations", lambda *a, **k: pytest.fail("2026 evaluation reached accounting"))
    with pytest.raises(ValueError, match="strictly before 2026"):
        ch003.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert not (runner_repo / holdout.LEDGER_PATH).exists()


def test_delisting_rejudges_circular_control_and_every_NW_lag(runner_repo, monkeypatch):
    calls = []
    def metrics(*args, loss=False, diagnostics=True):
        calls.append(loss)
        r = metrics_fixture()
        r["auxiliary_control"] = {"draws_sha256": "same-draws"}
        if loss: r["cost_sensitivity"]["1.5"]["difference"] = -.01
        return r
    monkeypatch.setattr(ch003, "variant_metrics", metrics)
    main, loss = ch003.evaluate_rule(None, None, None, ch003._registration(runner_repo).fields, 1)
    assert calls == [False, True] and main["verdict"] == "delisting_sensitive" and loss["verdict"] == "fail"


def test_registry_row_validated_before_any_result_write(runner_repo, monkeypatch):
    rules = {r: {ch003.VARIANT: ch003.judge(metrics_fixture(), ch003._registration(runner_repo).fields, 1)} for r in ch001.RULES}
    payload = {"rules": rules, "delisting_sensitivity": copy.deepcopy(rules), "variants": {ch003.VARIANT: {"verdict": "pass"}}}
    payload["rules"]["O"][ch003.VARIANT]["judgement_inputs"]["oversize"] = "x" * 32768
    monkeypatch.setattr(common, "write_results", lambda *a, **k: pytest.fail("published before row validation"))
    with pytest.raises(ValueError, match="32 KiB"):
        ch003.publish(payload, repo_root=runner_repo)
    assert not (runner_repo / experiment_registry.REGISTRY_PATH).exists()


def test_auxiliary_calls_choice_when_one_market_has_no_computable_months(monkeypatch):
    selections = [selection("2024-05-31", ["a", "b"]), selection("2024-06-28", ["a", "b"])]
    obs = observations_for(selections, [[.1, -.1], [.2, -.2]])
    regimes = flags_for(selections)
    for regime in regimes: regime["markets"]["KOSPI"]["computable"] = False
    rows = ch003.monthly(obs, selections, regimes)
    original, calls = np.random.default_rng, []
    def tracked(seed):
        rng = original(seed)
        def choice(candidates, *, size, replace):
            calls.append((list(candidates), size))
            return rng.choice(candidates, size=size, replace=replace)
        return SimpleNamespace(choice=choice)
    monkeypatch.setattr(np.random, "default_rng", tracked)
    result = ch003.auxiliary_controls(rows, regimes)
    assert calls == [([], 0), ([0, 1], 0)] * 200
    assert result["path_means"] == [0.] * 200 and not result["used_for_judgement"]
