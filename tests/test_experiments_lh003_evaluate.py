"""Offline execution accounting and fixed-52-week policy inference on fake quotes."""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
import shutil

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry
from backtesting.experiments import hc003
from backtesting.experiments.lh003 import EVALUATION_AT, EXPERIMENT_ID, Refused
from backtesting.experiments.lh003 import calendar, evaluate, executions
from backtesting.experiments.lh003.storage import digest, file_hash, seal, verified
from test_experiments_lh003_runner import Clock, forbid_real_calls_and_holdout, verified_calendar


def pinned_start(root, repo, **extra):
    from backtesting.experiments.lh003.runner import implementation_files, implementation_hashes, mechanical_data_hashes
    for path in [*implementation_files(experiment_registry.PROJECT_ROOT),
                 *(experiment_registry.PROJECT_ROOT / p for p in mechanical_data_hashes(experiment_registry.PROJECT_ROOT))]:
        target = repo / path.relative_to(experiment_registry.PROJECT_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    from backtesting.experiments.lh003.pins import capture
    baseline = capture(root, repo)
    seal(root / "start.json", {"synthetic": False, "at": "2026-11-12T10:00:00+09:00",
        "code_baseline_sha256": baseline, "implementation_hashes": implementation_hashes(repo), **extra})


def fake_cost(price, market, day, adv, side, *, multiplier, model_version):
    assert model_version == "v2"
    return 0.001 * multiplier


def experiment():
    codes = [f"{(i + 1) * 10:06d}" for i in range(40)]
    decisions, records = [], {}
    for schedule in calendar.plan():
        decision = {**schedule, "a_codes": codes, "candidate_codes": codes, "universe_codes": codes,
                    "policy_codes": codes[:10], "avoid": codes[30:40], "valid": True, "observation": True,
                    "reconstructed": False, "synthetic": False, "reasons": [], "jaccard": 1.0, "repeats": []}
        index = decision["index"]
        quotes = {}
        for pos, code in enumerate(codes):
            gross = 0.04 + 0.008 * math.sin(index) if pos < 10 else 0.0
            quotes[code] = {"stock_code": code, "filled": True, "reason": None,
                           "entry_date": decision["entry_date"], "exit_date": decision["exit_date"],
                           "entry_price": 10000.0, "exit_price": 10000 * (1 + gross),
                           "entry_market": "KOSPI", "exit_market": "KOSDAQ", "entry_adv_20": 2e9,
                           "exit_adv_20": 3e9, "gross": gross, "gross_delisting_loss": gross, "delisted": False}
        records[index] = {"decision_sha256": digest(decision), "quotes": quotes, "data_asof": EVALUATION_AT,
                          "audit_only": False}
        decisions.append(decision)
    return decisions, records


@pytest.fixture
def cohort():
    sessions = calendar.exchange().sessions
    sessions = sessions[(sessions >= "2026-10-01") & (sessions <= executions.PRICE_END)]
    decision = {**calendar.plan()[0], "universe_codes": ["000010", "000020", "000030"],
                "a_codes": ["000010", "000020", "000030"], "observation": True, "synthetic": False}
    index = pd.MultiIndex.from_product([sessions, decision["a_codes"]], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000.0, "high": 10100.0, "low": 9900.0, "close": 10000.0,
                           "base_price": 10000.0, "volume": 10000.0, "ret_1d": 0.0,
                           "market": "KOSPI", "calendar_status": "verified", "trading_value": 2e9}, index=index)
    return prices, sessions, decision


def test_raw_quotes_do_not_evaluate_costs_even_for_2027(cohort, monkeypatch):
    prices, sessions, decision = cohort
    decision = {**decision, **calendar.plan()[10]}
    assert decision["entry_date"].startswith("2027")
    monkeypatch.setattr(cost_model, "one_way_cost", lambda *a, **k: pytest.fail("cost evaluated while recording"))
    raw = executions.quote_cohort(prices, decision, sessions)
    assert all(q["filled"] and q["gross"] == 0 for q in raw["quotes"].values())
    assert "buy_rate" not in next(iter(raw["quotes"].values()))


def test_limit_up_exclusion_reweights_and_all_unfilled_is_cash(cohort):
    prices, sessions, decision = cohort
    prices.loc[(pd.Timestamp(decision["entry_date"]), "000010"), "open"] = 13000
    raw = executions.quote_cohort(prices, decision, sessions)
    assert raw["quotes"]["000010"]["reason"] == "limit_up_entry"
    priced = executions.priced_quotes(raw, 1, cost_fn=fake_cost)
    portfolio = executions.portfolio(priced, decision["a_codes"])
    assert portfolio["count"] == 2
    assert all(p["budget"] == 0.5 for p in portfolio["positions"])
    assert executions.portfolio(priced, ["000010"])["cash"] == 1
    assert executions.portfolio(priced, ["000010"])["net"] == 0


def test_HF001_delisting_temporary_halt_and_limit_down_exit_dates(cohort):
    prices, sessions, decision = cohort
    target = pd.Timestamp(decision["exit_date"])
    pos = sessions.get_loc(target)
    prices.loc[(slice(target, None), "000010"), "volume"] = 0
    prices.loc[(target, "000020"), "volume"] = 0
    prices.loc[(target, "000030"), "close"] = 7000
    raw = executions.quote_cohort(prices, decision, sessions)
    gone, halt, down = [raw["quotes"][c] for c in decision["a_codes"]]
    assert gone["delisted"] and gone["exit_date"] == sessions[pos - 1].date().isoformat() and gone["gross_delisting_loss"] == -1
    assert halt["halted_exit"] and not halt["delisted"] and halt["stale_exit"]
    assert down["limit_down_exit"] and down["exit_date"] == sessions[pos - 1].date().isoformat()


def test_adjusted_chain_and_HF001_raw_price_fallback_are_reported(cohort):
    prices, sessions, decision = cohort
    entry, target = pd.Timestamp(decision["entry_date"]), pd.Timestamp(decision["exit_date"])
    prices.loc[(entry, "000010"), "close"] = 11000
    prices.loc[(slice(entry, target), "000010"), "ret_1d"] = 0.01
    raw = executions.quote_cohort(prices, decision, sessions)
    quote = raw["quotes"]["000010"]
    assert quote["gross"] == pytest.approx(1.1 * 1.01 ** 19 - 1) and not quote["unadjusted_fallback"]
    prices.loc[(target, "000010"), "ret_1d"] = np.nan
    quote = executions.quote_cohort(prices, decision, sessions)["quotes"]["000010"]
    assert quote["gross"] == 0 and quote["unadjusted_fallback"]


def test_exit_cost_uses_actual_substitute_day_raw_price_market_and_prior20_ADV(cohort):
    prices, sessions, decision = cohort
    target, entry = pd.Timestamp(decision["exit_date"]), pd.Timestamp(decision["entry_date"])
    pos = sessions.get_loc(target)
    prices.loc[(target, "000010"), "close"] = 7000
    prices.loc[(sessions[pos - 1], "000010"), "market"] = "KOSDAQ"
    for i, day in enumerate(sessions):
        prices.loc[(day, "000010"), "trading_value"] = (i + 1) * 1e8
    quote = executions.quote_cohort(prices, decision, sessions)["quotes"]["000010"]
    calls = []
    def cost(*args, **kwargs):
        calls.append((args, kwargs))
        return 0.01 if args[4] == "buy" else 0.02
    priced = executions.priced_quotes({"quotes": {"000010": quote}}, 1.5, cost_fn=cost)
    assert calls[0][0][:3] == (10000.0, "KOSPI", decision["entry_date"].replace("-", ""))
    assert calls[1][0][:3] == (10000.0, "KOSDAQ", sessions[pos - 1].strftime("%Y%m%d"))
    assert calls[1][0][3] == pytest.approx(np.mean(np.arange(pos - 20, pos) * 1e8))
    assert all(kwargs == {"multiplier": 1.5, "model_version": "v2"} for _, kwargs in calls)
    value = executions.portfolio(priced, ["000010"])
    assert value["net"] == pytest.approx(0.98 / 1.01 - 1)
    position = value["positions"][0]
    assert position["quantity"] * 10000 + position["buy_cost"] == pytest.approx(1)
    assert value["cash"] == pytest.approx(1 + value["net"])


def test_execution_span_and_required_data_fail_closed(cohort):
    prices, sessions, decision = cohort
    prices.loc[(pd.Timestamp(decision["entry_date"]), "000010"), "base_price"] = np.nan
    previous = sessions[sessions.get_loc(pd.Timestamp(decision["entry_date"])) - 1]
    prices.loc[(previous, "000010"), "close"] = np.nan
    with pytest.raises(Refused, match="reference missing"):
        executions.quote_cohort(prices, decision, sessions)
    with pytest.raises(Refused, match="cannot extend"):
        executions.quote_cohort(prices, decision, sessions.append(pd.DatetimeIndex(["2027-12-06"])))


def test_cost_model_v2_exact_formula_at_2026_known_tax_dates(cohort):
    prices, sessions, decision = cohort
    raw = executions.quote_cohort(prices, decision, sessions)
    priced = executions.priced_quotes(raw, 1.5)
    quote = priced["000010"]
    b = cost_model.one_way_cost(quote["entry_price"], quote["entry_market"], quote["entry_date"].replace("-", ""),
                               quote["entry_adv_20"], "buy", multiplier=1.5, model_version="v2")
    s = cost_model.one_way_cost(quote["exit_price"], quote["exit_market"], quote["exit_date"].replace("-", ""),
                               quote["exit_adv_20"], "sell", multiplier=1.5, model_version="v2")
    assert executions.portfolio(priced, ["000010"])["net"] == pytest.approx((1 + quote["gross"]) * (1 - s) / (1 + b) - 1)


def test_2027_cost_refuses_with_legal_update_message_without_modifying_raw(cohort):
    prices, sessions, decision = cohort
    decision = {**decision, **calendar.plan()[10]}
    raw = executions.quote_cohort(prices, decision, sessions)
    before = digest(raw)
    with pytest.raises(Refused, match="legally sourced statutory rate"):
        executions.priced_quotes(raw, 1)
    assert digest(raw) == before


def test_calendar_week_NW_lag4_no_finite_sample_correction_and_normal_one_sided_p():
    stats = evaluate.statistics([1, 3, 2], positions=[0, 2, 9])
    expected_se = math.sqrt((2 / 3 - 2 * 0.6 / 3) / 3)
    assert stats["standard_error"] == pytest.approx(expected_se)
    assert stats["t_stat"] == pytest.approx(2 / expected_se)
    assert stats["p_one_sided"] == pytest.approx(0.5 * math.erfc(stats["t_stat"] / math.sqrt(2)))
    assert evaluate.statistics([1, 1, 1])["t_stat"] is None


def test_random_controls_draw_before_execution_filtering_no_refill_and_invalid_A():
    decisions, raw = experiment()
    paths = evaluate.random_paths(decisions)
    assert len(paths) == 52 and len(paths[0]) == 200 and all(len(set(p)) == 10 for p in paths[0])
    code = paths[0][0][0]
    raw[0]["quotes"][code].update(filled=False, reason="limit_up_entry")
    quoted = executions.priced_quotes(raw[0], 1, cost_fn=fake_cost)
    assert executions.portfolio(quoted, paths[0][0])["count"] == 9
    assert evaluate.random_paths(decisions) == paths
    decisions[0].update(valid=False, policy_codes=decisions[0]["a_codes"])
    assert evaluate.random_paths(decisions)[0] == {"same_as": "a", "paths": 200}


def test_all52_denominator_invalid_weeks_zero_and_30_valid_300_observations():
    decisions, raw = experiment()
    for decision in decisions[:22]:
        decision.update(valid=False, policy_codes=decision["a_codes"], avoid=[], reasons=["fake invalid"])
        raw[decision["index"]]["decision_sha256"] = digest(decision)
    result, _ = evaluate.evaluate(decisions, raw, cost_fn=fake_cost)
    assert result["primary"]["count"] == 52
    assert result["valid_decisions"] == 30 and result["bought_observations"] == 300
    assert all(r["d"] == 0 and r["policy_net"] == r["a_net"] for r in result["per_decision"][:22])
    assert result["primary"]["mean"] == pytest.approx(sum(r["d"] for r in result["per_decision"]) / 52)
    assert result["valid_only"]["count"] == 30 and result["screening_effect_fraction_applicable"] is False
    assert len(result["random_control"]["mean_excess"]) == 200
    assert result["invalid_reasons"] == {"fake invalid": 22}
    assert result["monthly"] and all(r["liquidity"] for r in result["per_decision"])


def test_complete_fake_evaluation_passes_and_disappeared_selection_changes_verdict():
    decisions, raw = experiment()
    normal, _ = evaluate.evaluate(decisions, raw, cost_fn=fake_cost)
    assert normal["verdict"] == "pass" and normal["primary"]["count"] == 52 and normal["bought_observations"] == 520
    assert normal["random_control"]["share_of_controls"] <= 0.05
    for record in list(raw.values())[:40]:
        for code in decisions[0]["policy_codes"]:
            record["quotes"][code].update(delisted=True, gross_delisting_loss=-1)
    sensitive, _ = evaluate.evaluate(decisions, raw, cost_fn=fake_cost)
    assert sensitive["verdict"] == "delisting_sensitive" and sensitive["base_verdict"] == "pass"
    assert sensitive["delisting_sensitivity"]["verdict"] == "fail"


def test_x1_5_is_judged_x2_is_reported_and_t_threshold_counts_only_LH003_trials():
    result = {"primary": {"mean": 0.01, "t_stat": 2.5}, "valid_decisions": 30, "bought_observations": 300,
              "random_control": {"share_of_controls": 0.05}, "cost_sensitivity": {"1.5": {"mean": 0.001}, "2.0": {"mean": -5}}}
    assert evaluate.verdict(result, trials=20)[0] == "pass"
    assert evaluate.verdict(result, trials=21)[0] == "fail"
    result["cost_sensitivity"]["1.5"]["mean"] = 0
    assert any("x1.5" in s for s in evaluate.verdict(result, trials=1)[1])
    result["bought_observations"] = 299
    assert evaluate.verdict(result, trials=1)[0] == "insufficient"
    result["bought_observations"], result["valid_decisions"] = 300, 29
    assert evaluate.verdict(result, trials=1)[0] == "insufficient"


def test_missing_or_reconstructed_A_never_becomes_an_invalid_zero_week():
    decisions, raw = experiment()
    decisions[3] = None
    assert evaluate.evaluate(decisions, raw, cost_fn=fake_cost)[0]["verdict"] == "insufficient"
    decisions, raw = experiment()
    decisions[3].update(observation=False, reconstructed=True)
    assert "never observations" in evaluate.evaluate(decisions, raw, cost_fn=fake_cost)[0]["reasons"][0]
    decisions, raw = experiment()
    del raw[3]
    assert evaluate.evaluate(decisions, raw, cost_fn=fake_cost)[0]["verdict"] == "insufficient"


def test_one_offline_final_verdict_compact_registry_and_idempotent_publication(tmp_path, monkeypatch):
    root = tmp_path / "state"
    pinned_start(root, tmp_path)
    monkeypatch.setattr(experiment_registry, "verify_preregistration",
                        lambda *a, **k: SimpleNamespace(commit_hash="fixture", fields={}))
    with pytest.raises(Refused, match="only after"):
        evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock("2027-12-16T23:59:59+09:00"))
    result = evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert result["verdict"] == "insufficient"
    again = evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert again == result
    evaluate.publish_evaluation(root=root, repo_root=tmp_path)
    rows = hc003.registry_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["experiment_id"] == EXPERIMENT_ID
    assert len(rows[0]["metrics_json"].encode()) < experiment_registry.MAX_METRICS_BYTES


def test_record_execution_seals_without_calling_costs_in_2027(tmp_path, cohort, monkeypatch):
    prices, sessions, decision = cohort
    decision = {**decision, **calendar.plan()[10]}
    source = tmp_path / "source"
    required = sessions[sessions >= sessions[sessions.get_loc(pd.Timestamp(decision["entry_date"])) - 20]]
    hashes = {}
    for day in required:
        path = executions.price_path(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake source; price parser injected\n")
        hashes[path.relative_to(source).as_posix()] = file_hash(path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"as_of": EVALUATION_AT, "files": hashes}))
    monkeypatch.setattr(executions, "prices_from_view", lambda *a: prices)
    monkeypatch.setattr(executions, "latest_distribution", lambda *a, **k: (SimpleNamespace(root=source), {"manifest_sha256": "fixture"}))
    monkeypatch.setattr(cost_model, "one_way_cost", lambda *a, **k: pytest.fail("cost evaluated while recording"))
    pinned_start(tmp_path / "state", tmp_path)
    records = executions.record_executions(data_dir=source, root=tmp_path / "state", decisions=[decision],
                                           sync_manifest=manifest, clock=Clock(EVALUATION_AT), repo_root=tmp_path)
    assert len(records) == 1 and verified(tmp_path / "state/executions/raw/10.json")["data_asof"] == EVALUATION_AT
    assert records[0]["quotes"]["000010"]["entry_date"].startswith("2027")


def test_call_audit_counts_first_cause_reasks_and_unfinished_reservations(tmp_path):
    cases = [("valid", None), ("invalid", "timeout"), ("invalid", "ValidationError: has non-unique elements; not unique"),
             ("invalid", "ValidationError: not one of candidate enums"), ("invalid", "ranked and avoid overlap"),
             ("invalid", "tool"), ("interrupted", "killed"), (None, None)]
    for n, (state, error) in enumerate(cases, 1):
        seal(tmp_path / "attempts" / f"{n:06d}.started.json", {"number": n, "try": n % 2})
        if state:
            seal(tmp_path / "attempts" / f"{n:06d}.result.json", {"state": state, "error": error,
                "event_summary": {"violations": ["forbidden_or_unknown_item:web_search"] if error == "tool" else []}})
    audit = evaluate.call_audit(tmp_path)
    assert audit["used"] == 8 and audit["states"] == {"valid": 1, "invalid": 5, "interrupted": 2}
    assert audit["reasks"] == 4 and audit["invalid_by_reason"]["interrupted"] == 2
    assert all(audit["invalid_by_reason"][name] == 1 for name in
               ("timeout", "duplicate_ids", "unknown_id", "ranked_avoid_overlap", "tool_or_unknown_item"))


def test_aborted_statistical_trial_is_recorded_without_a_final_verdict(tmp_path, monkeypatch):
    root = tmp_path / "state"
    pinned_start(root, tmp_path)
    monkeypatch.setattr(experiment_registry, "verify_preregistration",
                        lambda *a, **k: SimpleNamespace(commit_hash="fixture", fields={}))
    def failed(*args, **kwargs): raise ArithmeticError("fake inference failure")
    monkeypatch.setattr(evaluate, "evaluate", failed)
    with pytest.raises(ArithmeticError, match="inference failure"):
        evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert not (root / "evaluation.json").exists()
    rows = hc003.registry_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "evaluation_aborted"
    assert json.loads(rows[0]["metrics_json"])["status"] == "aborted"


def test_2027_cost_preflight_does_not_consume_the_final_evaluation_trial(tmp_path, monkeypatch):
    root = tmp_path / "state"
    pinned_start(root, tmp_path)
    decisions, records = experiment()
    from backtesting.experiments.lh003.runner import verify_pinned
    applied = verify_pinned(root, tmp_path)
    for index, decision in enumerate(decisions):
        decision.update(code_pins=applied, sealed_at=decision["decision_date"] + "T18:00:00+09:00")
        records[index]["decision_sha256"] = digest(decision)
    for index, record in records.items():
        seal(root / "executions/raw" / f"{index:02d}.json", record)
    monkeypatch.setattr(evaluate, "load_week", lambda root, index: decisions[index])
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a, **k: object())
    with pytest.raises(Refused, match="legally sourced"):
        evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert not (root / "evaluation.json").exists() and hc003.registry_rows(tmp_path) == []
