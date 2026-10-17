"""AV002's fixed parent, pre-open ordering, single attempt and release contract."""
import copy
import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting import append_only, experiment_registry, holdout
from backtesting.experiments import av001, av002, common, ev001, hc003, ordering
from backtesting.experiments.lh001 import __main__ as lh_cli
from backtesting.experiments.lh001.config import LH002
from preregistration_fixtures import committed_preregistrations
from test_experiments_av001 import TinyBook, av_selection, prices_for, write_data
from dart_listing_fixtures import checkpoint as listing_checkpoint
from src.ingestion import dart_listing


def no_claim(*args, **kwargs):
    pytest.fail("unexpected holdout claim/publication")


@pytest.fixture
def registered(tmp_path):
    committed_preregistrations(tmp_path, [av002.EXPERIMENT_ID, av001.EXPERIMENT_ID,
        av001.hc003.EXPERIMENT_ID, av001.hf001.EXPERIMENT_ID, av001.hc002.EXPERIMENT_ID])
    return tmp_path


@pytest.fixture
def sealed_parent(tmp_path, monkeypatch):
    states = {r: {v: {"cash": 1, "name": v} for v in av001.VARIANTS} for r in av001.RULES}
    state_hash = hc003._content_hash(states)
    monkeypatch.setattr(av002, "STATE_SHA256", state_hash)
    hashes = {k: {} for k in av002.INPUT_GROUPS | {"code"}}
    design = {"experiment_id": av001.EXPERIMENT_ID, "stage": "design_validation",
        "state_decision_month": "2025-11", "state_valuation_date": "2025-12-30", "state_sha256": state_hash,
        "design_states": states, "comparison_states": {}, "source_hashes": copy.deepcopy(hashes),
        "trial_count_after_run": 6, "performance": {"runtime_seconds": 1, "peak_memory_mib": 2},
        "variants": {v: {"verdict": "pass" if v in av002.VARIANTS else "fail"} for v in av001.VARIANTS},
        "rules": {r: {v: {"verdict": "pass" if v in av002.VARIANTS else "fail", "per_month": [
            {"month": "2025-10", "valid": True, "excess": .1},
            {"month": "2025-11", "valid": False, "excess": None}]} for v in av001.VARIANTS} for r in av001.RULES},
        "design_effects": {r: {**av002.DESIGN_EFFECTS[r], "av_phase": .001} for r in av001.RULES}}
    aborted = {"experiment_id": av001.EXPERIMENT_ID, "stage": "holdout", "verdict": "holdout_aborted",
        "variants": dict.fromkeys(av002.VARIANTS, {}), "state_sha256": state_hash,
        "input_hash_verification": "aborted; inputs not verified", "source_hashes": hashes,
        "abort": {"input_hashes": {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()
                                  for k, v in hashes.items()}}}

    def seal():
        path = tmp_path / av002.PARENT_RESULT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(design).encode()))
        (tmp_path / av002.ABORT_RESULT).write_text(json.dumps(aborted))
        log = tmp_path / list(av002.PARENT_FILES)[2]
        log.write_text("fixture aborted before returns\n")
        monkeypatch.setattr(av002, "PARENT_FILES", {p: hc003._digest(tmp_path / p) for p in av002.PARENT_FILES})
    seal()
    return SimpleNamespace(root=tmp_path, design=design, aborted=aborted, seal=seal)


def test_committed_parent_all_hashes_full_three_variant_state_and_passing_set():
    design, aborted = av002.load_parent()
    assert design["state_sha256"] == av002.STATE_SHA256
    assert all(set(design["design_states"][r]) == set(av001.VARIANTS) for r in av001.RULES)
    assert {v for v, m in design["variants"].items() if m["verdict"] == "pass"} == set(av002.VARIANTS)
    assert aborted["input_hash_verification"] == "aborted; inputs not verified"


def test_adapter_verifies_every_sealed_file_and_rejects_symlinks(sealed_parent):
    root = sealed_parent.root
    av002.load_parent(root)
    for relative in av002.PARENT_FILES:
        path = root / relative
        content = path.read_bytes()
        path.write_bytes(content + b"changed")
        with pytest.raises(ValueError, match="parent file hash/path"):
            av002.load_parent(root)
        path.write_bytes(content)
    path = root / list(av002.PARENT_FILES)[2]
    content = path.read_bytes()
    other = root / "log-copy"
    other.write_bytes(content)
    path.unlink()
    path.symlink_to(other)
    with pytest.raises(ValueError, match="parent file hash/path"):
        av002.load_parent(root)


@pytest.mark.parametrize("change", ["passing", "rule_pass", "effect", "state", "missing_variant", "month", "valuation",
                                    "abort_verdict", "abort_state", "verification", "groups", "digest", "path", "hash"])
def test_adapter_rejects_inconsistent_parent_contents_even_if_file_hash_is_valid(sealed_parent, change):
    design, aborted = sealed_parent.design, sealed_parent.aborted
    if change == "passing":
        design["variants"]["av_phase"]["verdict"] = "pass"
    elif change == "rule_pass":
        design["rules"]["O"]["av_conc"]["verdict"] = "fail"
    elif change == "effect":
        design["design_effects"]["S"]["av_all"] = .9
    elif change == "state":
        design["design_states"]["S"]["av_phase"]["cash"] = 2
    elif change == "missing_variant":
        del design["design_states"]["S"]["av_phase"]
    elif change == "month":
        design["state_decision_month"] = "2025-10"
    elif change == "valuation":
        design["state_valuation_date"] = "2025-12-31"
    elif change == "abort_verdict":
        aborted["verdict"] = "pass"
    elif change == "abort_state":
        aborted["state_sha256"] = "changed"
    elif change == "verification":
        aborted["input_hash_verification"] = "verified"
    elif change == "groups":
        del aborted["source_hashes"]["financials"]
    else:
        aborted["source_hashes"]["prices"] = {"../outside" if change == "path" else "price": "invalid" if change == "hash" else "a" * 64}
        if change != "digest":
            aborted["abort"]["input_hashes"]["prices"] = hashlib.sha256(
                json.dumps(aborted["source_hashes"]["prices"], sort_keys=True).encode()).hexdigest()
    sealed_parent.seal()
    with pytest.raises(ValueError):
        av002.load_parent(sealed_parent.root)


def test_registration_pins_committed_plan_and_rejects_working_tree_edit(registered):
    registration = av002._registration(registered)
    assert registration.commit_hash == av002.PREREG_COMMIT
    path = registered / registration.path
    path.write_text(path.read_text() + "changed\n")
    with pytest.raises(ValueError, match="git check"):
        av002._registration(registered)


@pytest.mark.parametrize("field,value", [("commit_hash", "another"), ("variants_planned", 3), ("uses_holdout", False), ("uses_llm", True)])
def test_registration_does_not_accept_another_plan(monkeypatch, field, value):
    registration = SimpleNamespace(commit_hash=av002.PREREG_COMMIT,
        fields={"variants_planned": 2, "uses_holdout": True, "uses_llm": False})
    if field == "commit_hash":
        registration.commit_hash = value
    else:
        registration.fields[field] = value
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a: registration)
    with pytest.raises(ValueError, match="immutable"):
        av002._registration(Path("unused"))


@pytest.mark.parametrize("change", [None, "code", "e", "valid", "month_order", "state", "comparison", "effects", "input", "abort_input"])
def test_reproduction_compares_all_three_variants_without_reselection_or_publication(sealed_parent, monkeypatch, change):
    monkeypatch.setattr(av002, "_registration", lambda *a: None)
    monkeypatch.setattr(holdout, "_claim", no_claim)
    monkeypatch.setattr(experiment_registry, "record_trial", no_claim)
    monkeypatch.setattr(experiment_registry, "record_trials", no_claim)
    replay = copy.deepcopy(sealed_parent.design)
    if change == "e":
        replay["rules"]["O"]["av_phase"]["per_month"][0]["excess"] += 1e-12
    elif change == "valid":
        replay["rules"]["S"]["av_conc"]["per_month"][1]["valid"] = True
    elif change == "month_order":
        replay["rules"]["S"]["av_all"]["per_month"].reverse()
    elif change == "state":
        replay["design_states"]["S"]["av_phase"]["cash"] = 2
    elif change == "comparison":
        replay["comparison_states"] = {"changed": True}
    elif change == "effects":
        replay["design_effects"]["S"]["av_phase"] = .5
    elif change == "code":
        replay["source_hashes"]["code"] = {"changed-code.py": "new-sha"}
    elif change == "input":
        replay["source_hashes"]["financials"] = {"different": "sha"}
    elif change == "abort_input":
        sealed_parent.aborted["source_hashes"]["companies"] = {"extra": "a" * 64}
        sealed_parent.seal()
        # Isolate the cross-record check from the adapter's category digest check.
        monkeypatch.setattr(av002, "load_parent", lambda *a: (sealed_parent.design, sealed_parent.aborted))
        replay["source_hashes"]["companies"] = {"company": "b" * 64}
        sealed_parent.design["source_hashes"]["companies"] = {"company": "b" * 64}
    def compute(**kwargs):
        assert kwargs["trials"] == 6 and "_attempt" not in kwargs
        return replay
    monkeypatch.setattr(av002.computation, "compute_design", compute)
    if change in (None, "code"):
        result = av002.reproduce(data_dir=sealed_parent.root, repo_root=sealed_parent.root)
        assert result["state_matches_parent"] and set(result["months_compared"]["S"]) == set(av001.VARIANTS)
    else:
        with pytest.raises(ValueError, match="mismatch|differs"):
            av002.reproduce(data_dir=sealed_parent.root, repo_root=sealed_parent.root)
    assert not (sealed_parent.root / experiment_registry.REGISTRY_PATH).exists()


def test_runner_code_change_during_reproduction_refuses_to_continue(sealed_parent, monkeypatch):
    source = sealed_parent.root / "runner.py"
    source.write_text("before\n")
    monkeypatch.setattr(av002, "__file__", str(source))
    monkeypatch.setattr(av002, "_registration", lambda *a: None)
    def changed(**kwargs):
        source.write_text("after\n")
        return copy.deepcopy(sealed_parent.design)
    monkeypatch.setattr(av002.computation, "compute_design", changed)
    with pytest.raises(ValueError, match="runner code changed"):
        av002.reproduce(data_dir=sealed_parent.root, repo_root=sealed_parent.root)


def test_computation_is_isolated_and_keeps_the_same_accounting_with_AV002_guards(tmp_path, monkeypatch):
    assert av001.EXPERIMENT_ID == "AV001_avoidance_portfolio"
    assert av002.computation.VARIANTS == av001.VARIANTS
    assert av002.computation._simulate.__code__.co_code == av001._simulate.__code__.co_code
    chosen = [av_selection("2025-11-28", "2025-12-01", "2025-12-30", {"a": 2, "b": 4}, kind="close")]
    first, second = TinyBook(tmp_path, chosen), TinyBook(tmp_path, chosen)
    second.experiment_id = av002.EXPERIMENT_ID
    expected = av001._simulate(first, chosen, "av_all")
    ids = []
    monkeypatch.setattr(holdout, "guard_period", lambda *a, **k: ids.append(k["experiment_id"]))
    actual = av002.computation._simulate(second, chosen, "av_all")
    assert actual == expected and set(ids) == {av002.EXPERIMENT_ID}


def test_listing_target_inventory_hash_reads_also_forward_AV002_ID(tmp_path, monkeypatch):
    listing_checkpoint(tmp_path)
    ids = []
    monkeypatch.setattr(holdout, "guard_period", lambda *a, **k: ids.append(k.get("experiment_id")))
    status, months = av002.computation.coverage_plan(tmp_path, [], hc003.session_calendar())
    assert status["supplement_complete"] and months == []
    assert ids and set(ids) == {av002.EXPERIMENT_ID}


def inventory(root):
    for day in av002.trading_calendar.session_calendar("2025-12-01", "2026-06-30"):
        path = root / "market/krx_daily" / str(day.year) / (day.strftime("%Y%m%d") + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unread price contents\n")


@pytest.mark.parametrize("change", [None, "missing", "unexpected", "empty", "duplicate", "misplaced"])
def test_preflight_names_sizes_only_no_price_opens_or_claims(tmp_path, monkeypatch, change):
    inventory(tmp_path)
    path = tmp_path / "market/krx_daily/2026/20260605.jsonl"
    if change == "missing":
        path.unlink()
    elif change == "empty":
        path.write_text("")
    elif change == "unexpected":
        path.with_name("20260603.jsonl").write_text("holiday")
    elif change in ("duplicate", "misplaced"):
        other = tmp_path / "market/krx_daily/2025/20260605.jsonl"
        other.write_bytes(path.read_bytes())
        if change == "misplaced":
            path.unlink()
    monkeypatch.setattr(av002, "_registration", lambda *a: None)
    monkeypatch.setattr(Path, "open", no_claim)
    monkeypatch.setattr(holdout, "_claim", no_claim)
    if change:
        with pytest.raises(ValueError, match="calendar_price_session_mismatch"):
            av002.preflight(data_dir=tmp_path, repo_root=tmp_path)
    else:
        result = av002.preflight(data_dir=tmp_path, repo_root=tmp_path)
        assert result["passed"] and not result["contents_read"]
        assert result["sessions"] == result["price_sessions"] == len(result["price_file_sizes"])


def test_frozen_inputs_take_protected_expectations_only_from_abort_and_require_active_AV002(tmp_path, monkeypatch):
    write_data(tmp_path)
    protected = tmp_path / "market/krx_daily/2026/20260102.jsonl"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("protected fixture\n")
    key = protected.relative_to(tmp_path).as_posix()
    expected = {key: hc003._digest(protected)}
    original = Path.open
    def checked(path, *a, **k):
        if path == protected:
            assert any(s.experiment_id == av002.EXPERIMENT_ID for s in holdout._SESSIONS.get())
        return original(path, *a, **k)
    hashes = av002.computation.source_hashes(tmp_path, av002.computation.input_price_paths(tmp_path, "holdout"),
        stage="holdout", frozen_prices=expected, repo_root=tmp_path)
    record = {"source_hashes": hashes}
    monkeypatch.setattr(Path, "open", checked)
    monkeypatch.setattr(holdout, "_claim", no_claim)
    av002.frozen_inputs(record, data_dir=tmp_path, repo_root=tmp_path, before_open=True)
    with pytest.raises(ValueError, match="requires an open"):
        av002.frozen_inputs(record, data_dir=tmp_path, repo_root=tmp_path, before_open=False)
    fake = SimpleNamespace(_open=True, _pid=os.getpid(), experiment_id=av002.EXPERIMENT_ID,
        repo_root=tmp_path.resolve(), ledger_path=(tmp_path / holdout.LEDGER_PATH).resolve(), guard_period=lambda *a: None)
    token = holdout._SESSIONS.set((fake,))
    try:
        av002.frozen_inputs(record, data_dir=tmp_path, repo_root=tmp_path, before_open=False)
        protected.write_text("changed\n")
        with pytest.raises(ValueError, match="frozen AV001 input"):
            av002.frozen_inputs(record, data_dir=tmp_path, repo_root=tmp_path, before_open=False)
    finally:
        holdout._SESSIONS.reset(token)
    # The same changed protected bytes remain unopened in the pre-open pass;
    # it preserves the recorded expectation and will abort on session entry.
    assert av002.frozen_inputs(record, data_dir=tmp_path, repo_root=tmp_path, before_open=True)["prices"] == expected
    record["source_hashes"]["prices"] = {}
    with pytest.raises(ValueError, match="absent from the frozen"):
        av002.frozen_inputs(record, data_dir=tmp_path, repo_root=tmp_path, before_open=True)


@pytest.fixture
def runner(registered, monkeypatch):
    root = registered
    design = {"L": 17, "state_sha256": av002.STATE_SHA256, "design_effects": copy.deepcopy(av002.DESIGN_EFFECTS),
              "library_versions": {"numpy": "fixture"}, "code_commit": "parent-code"}
    events, used = [], set()
    real_session, real_ledger_experiments = holdout.HoldoutSession, hc003.ledger_experiments
    monkeypatch.setattr(av002, "available_memory_bytes", lambda: 5_000_000_000)
    monkeypatch.setattr(av002, "load_parent", lambda *a: (design, {"source_hashes": {}}))
    monkeypatch.setattr(hc003, "ledger_experiments", lambda *a: set(used))
    def step(name, value):
        def run(*args, **kwargs):
            assert ordering._lock_target(root)[1] in ordering._LOCKS.get()
            events.append(name)
            return copy.deepcopy(value)
        return run
    for name in ("reproduce", "preflight", "rehearse"):
        monkeypatch.setattr(av002, name, step(name, {"passed": True, "code_hashes": {}}))
    def frozen(*args, before_open, **kwargs):
        events.append("freeze" if before_open else "verify_inputs")
        return {"prices": {"fixture": "sha"}}
    monkeypatch.setattr(av002, "frozen_inputs", frozen)
    manifest = {"file": "manifest.json", "sha256": "sha", "source_hashes": {}, "code_commit": "fixture", "committed": True}
    monkeypatch.setattr(av002, "code_manifest", step("manifest", manifest))
    monkeypatch.setattr(av002, "_verify_code", step("verify_code", None))
    coverage = [{"listing": {"covered": True}}] * 6
    monkeypatch.setattr(av002.computation, "coverage_plan", lambda *a: ({}, copy.deepcopy(coverage)))
    version = SimpleNamespace(sensitivity_available=True, annotations=[], report=lambda: {})
    prepared = {"dates": SimpleNamespace(L=17), "versions": {r: version for r in av001.RULES},
        "listing_status": {"listing_version": {}}, "counts": {}, "event_counts": {}, "book": SimpleNamespace(counts={})}
    monkeypatch.setattr(av002.computation, "prepare", step("prepare", prepared))
    monkeypatch.setattr(av002.computation, "_date_differences", lambda *a: {})
    rules = {r: {} for r in av001.RULES}
    variants = {}
    for v in av002.VARIANTS:
        variants[v] = {"verdict": "pass", "rule_verdicts": {r: "pass" for r in av001.RULES}}
        for r in av001.RULES:
            result = av002.computation.unavailable_metrics("pass")
            result["primary"].update(count=6, mean=.01)
            rules[r][v] = result
    evaluated = (rules, {}, variants)
    monkeypatch.setattr(av002.computation, "evaluate_holdout", step("evaluate", evaluated))
    class FakeSession:
        def __init__(self, experiment_id, **kwargs):
            assert experiment_id == av002.EXPERIMENT_ID and kwargs["repo_root"] == root
        def __enter__(self):
            events.append("claim")
            assert ordering._lock_target(root)[1] in ordering._LOCKS.get()
            used.add(av002.EXPERIMENT_ID)
        def __exit__(self, *args):
            events.append("exit")
    monkeypatch.setattr(holdout, "HoldoutSession", FakeSession)
    return SimpleNamespace(root=root, design=design, events=events, used=used, step=step,
                           manifest=manifest, coverage=coverage, evaluated=evaluated,
                           real_session=real_session, real_ledger_experiments=real_ledger_experiments)


def test_all_preopen_steps_are_mandatory_ordered_and_same_lock_then_one_row_per_variant(runner):
    result = av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert runner.events == ["reproduce", "preflight", "rehearse", "freeze", "manifest", "verify_code",
                             "claim", "verify_inputs", "verify_code", "prepare", "evaluate", "verify_inputs", "verify_code", "exit"]
    assert result["state_sha256"] == av002.STATE_SHA256
    rows = hc003.registry_rows(runner.root)
    assert [row["variant"] for row in rows] == ["holdout_av_all", "holdout_av_conc"]
    assert ordering.av002_release(rows, runner.root)["terminal_holdout_variants"] == list(av002.VARIANTS)
    with pytest.raises(ValueError, match="already used"):
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)


@pytest.mark.parametrize("step", ["reproduce", "preflight", "rehearse", "frozen_inputs", "code_manifest", "_verify_code"])
def test_any_preopen_failure_stops_without_claim_or_rows(runner, monkeypatch, step):
    def fail(*a, **k):
        raise ValueError("pre-open failure: " + step)
    monkeypatch.setattr(av002, step, fail)
    with pytest.raises(ValueError, match="pre-open failure"):
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert not runner.used and "claim" not in runner.events
    assert hc003.registry_rows(runner.root) == []


@pytest.mark.parametrize("step", ["reproduce", "preflight", "rehearse"])
def test_nonpassing_preopen_result_cannot_open_even_without_an_exception(runner, monkeypatch, step):
    monkeypatch.setattr(av002, step, runner.step(step, {"passed": False}))
    with pytest.raises(ValueError, match="did not pass"):
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert not runner.used and hc003.registry_rows(runner.root) == []


def test_existing_LH002_contamination_records_both_without_reproduction_or_open(runner):
    runner.used.add("LH002_llm_hegemony_judge")
    result = av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert result["verdict"] == "holdout_contaminated" and runner.events == []
    rows = hc003.registry_rows(runner.root)
    assert len(rows) == 2 and all(r["verdict"] == "holdout_contaminated" for r in rows)
    assert ordering.av002_release(rows, runner.root)["parent"] == av002.parent_link()


@pytest.mark.parametrize("failure", ["entry_before_claim", "entry_after_claim", "input_entry", "input_end", "prepare", "evaluate", "publication", "preregistration", "coverage"])
def test_postclaim_failures_abort_both_once_and_captured_plan_survives_edits(runner, monkeypatch, failure):
    original_registration = av002._registration(runner.root)
    if failure.startswith("entry_"):
        class FailedSession:
            def __init__(self, *a, **k):
                pass
            def __enter__(self):
                if failure == "entry_after_claim":
                    runner.used.add(av002.EXPERIMENT_ID)
                raise RuntimeError("injected failure")
            def __exit__(self, *a):
                pass
        monkeypatch.setattr(holdout, "HoldoutSession", FailedSession)
    elif failure in ("input_entry", "input_end"):
        original, checks = av002.frozen_inputs, []
        def verify(*a, **k):
            if not k["before_open"]:
                checks.append(1)
                if len(checks) == (1 if failure == "input_entry" else 2):
                    raise RuntimeError("injected failure")
            return original(*a, **k)
        monkeypatch.setattr(av002, "frozen_inputs", verify)
    elif failure == "publication":
        original = common.write_results
        def publish(*a, **k):
            if k.get("aborted_registration") is None:
                raise RuntimeError("injected failure")
            return original(*a, **k)
        monkeypatch.setattr(common, "write_results", publish)
    elif failure == "preregistration":
        def changed(*a, **k):
            path = runner.root / original_registration.path
            path.write_text(path.read_text() + "changed during evaluation\n")
            return runner.evaluated
        monkeypatch.setattr(av002.computation, "evaluate_holdout", changed)
    elif failure == "coverage":
        def changed(*a, **k):
            runner.coverage[0]["listing"]["covered"] = False
            return runner.evaluated
        monkeypatch.setattr(av002.computation, "evaluate_holdout", changed)
    else:
        def fail(*a, **k):
            raise RuntimeError("injected failure")
        monkeypatch.setattr(av002.computation, "prepare" if failure == "prepare" else "evaluate_holdout", fail)
    with pytest.raises((ValueError, RuntimeError)):
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    rows = hc003.registry_rows(runner.root)
    if failure == "entry_before_claim":
        assert rows == [] and not runner.used
    else:
        assert len(rows) == 2 and all(r["verdict"] == "holdout_aborted" for r in rows)
        assert all(r["prereg_commit"] == original_registration.commit_hash for r in rows)
        assert len({json.loads(r["metrics_json"])["run_id"] for r in rows}) == 1
        assert runner.used == {av002.EXPERIMENT_ID}


@pytest.mark.parametrize("successor", ["LH002_llm_hegemony_judge", "EV001_earnings_filing_entry", "LH003_llm_leader_forward"])
def test_final_order_check_catches_successor_appearing_during_preopen(runner, monkeypatch, successor):
    original = av002.code_manifest
    def manifest(**kwargs):
        runner.used.add(successor)
        return original(**kwargs)
    monkeypatch.setattr(av002, "code_manifest", manifest)
    if successor == "LH002_llm_hegemony_judge":
        assert av002.run_holdout(data_dir=runner.root, repo_root=runner.root)["verdict"] == "holdout_contaminated"
        assert len(hc003.registry_rows(runner.root)) == 2
    else:
        with pytest.raises(ValueError, match="ordering violation"):
            av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
        assert hc003.registry_rows(runner.root) == []
    assert "claim" not in runner.events and runner.used == {successor}


@pytest.mark.parametrize("bad", ["calendar", "coverage", "uncommitted", "reproduction_code"])
def test_remaining_preopen_checks_refuse_open(runner, monkeypatch, bad):
    if bad == "calendar":
        monkeypatch.setattr(hc003, "rebalance_calendar", lambda *a, **k: ([], []))
    elif bad == "coverage":
        runner.coverage[0]["listing"]["covered"] = False
    elif bad == "uncommitted":
        runner.manifest["committed"] = False
    else:
        monkeypatch.setattr(av002, "reproduce", runner.step("reproduce", {"passed": True, "code_hashes": {"changed.py": "sha"}}))
    with pytest.raises(ValueError):
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert "claim" not in runner.events and hc003.registry_rows(runner.root) == []


@pytest.mark.parametrize("status", sorted(ordering.AV002_TERMINAL))
def test_every_terminal_status_releases_only_both_verified_variants_from_one_parent_result(runner, status):
    for rule in av001.RULES:
        for variant in av002.VARIANTS:
            runner.evaluated[0][rule][variant]["verdict"] = status
            runner.evaluated[2][variant]["verdict"] = status
            runner.evaluated[2][variant]["rule_verdicts"][rule] = status
    av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    rows = hc003.registry_rows(runner.root)
    assert ordering.av002_release(rows, runner.root)["passing_variants"] == list(av002.VARIANTS)
    assert "sensitivity_unavailable" not in ordering.TERMINAL
    for broken in ([], rows[:1], [*rows, rows[0]]):
        with pytest.raises(ValueError, match="both verified"):
            ordering.av002_release(broken, runner.root)
    for field in ("parent", "run_id", "result_sha256", "design_result_file", "state_sha256"):
        changed = copy.deepcopy(rows)
        metrics = json.loads(changed[1]["metrics_json"])
        metrics[field] = "different"
        changed[1]["metrics_json"] = json.dumps(metrics)
        with pytest.raises(ValueError):
            ordering.av002_release(changed, runner.root)


@pytest.mark.parametrize("broken", ["missing", "partial", "parent"])
def test_LH003_seal_release_requires_both_AV002_variants_linked_to_parent(runner, monkeypatch, broken):
    av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    rows = hc003.registry_rows(runner.root)
    verify = experiment_registry.verify_preregistration
    monkeypatch.setattr(experiment_registry, "verify_preregistration",
        lambda name, root: SimpleNamespace() if name == "EV001_earnings_filing_entry" else verify(name, root))
    monkeypatch.setattr(ordering, "release_state", lambda *a: {})
    with ordering.execution_lock(runner.root):
        assert ordering.lh003_releases(rows, runner.root)[av002.EXPERIMENT_ID]["parent"] == av002.parent_link()
        damaged = copy.deepcopy(rows)
        if broken == "missing":
            damaged = []
        elif broken == "partial":
            damaged = damaged[:1]
        else:
            metrics = json.loads(damaged[1]["metrics_json"])
            metrics["parent"] = "different AV001 result"
            damaged[1]["metrics_json"] = json.dumps(metrics)
        with pytest.raises(ValueError):
            ordering.lh003_releases(damaged, runner.root)


@pytest.mark.parametrize("successor", ["EV001_earnings_filing_entry", "LH003_llm_leader_forward", "LH002_llm_hegemony_judge"])
def test_direct_successor_claim_is_blocked_before_ledger_write(registered, monkeypatch, successor):
    monkeypatch.setattr(av002, "load_parent", lambda *a: ({}, {}))
    monkeypatch.setattr(ordering, "release_state", lambda *a: {})
    monkeypatch.setattr(holdout, "_claim_ordered", no_claim)
    with pytest.raises(ValueError, match="both verified"):
        holdout._claim(successor, registered, registered / holdout.LEDGER_PATH, holdout.HOLDOUT_START, holdout.HOLDOUT_END)
    assert not (registered / holdout.LEDGER_PATH).exists()


def test_EV001_and_LH002_guards_run_before_evaluation_or_cached_results(registered, monkeypatch):
    monkeypatch.setattr(av002, "load_parent", lambda *a: ({}, {}))
    monkeypatch.setattr(ev001, "_registration", lambda *a: None)
    monkeypatch.setattr(ev001, "saved_design", no_claim)
    with pytest.raises(ValueError, match="both verified"):
        ev001.run_holdout(data_dir=registered, repo_root=registered)
    monkeypatch.setattr(ordering, "release_state", lambda *a: {})
    monkeypatch.setattr(lh_cli, "_execute_final", no_claim)
    with pytest.raises(ValueError, match="both verified"):
        lh_cli.execute_final(data_dir=registered, repo_root=registered, config=LH002)


def test_linked_worktrees_share_a_nonblocking_execution_lock_in_real_git(registered, tmp_path):
    other = tmp_path / "linked"
    subprocess.run(["git", "-C", str(registered), "worktree", "add", "--quiet", "--detach", "--no-checkout", str(other)],
                   check=True, capture_output=True)
    with ordering.execution_lock(registered):
        with pytest.raises(ValueError, match="in progress"):
            with ordering.execution_lock(other):
                pytest.fail("parallel worktree lock")
        child = subprocess.run([sys.executable, "-c", """
import sys
from backtesting.experiments import ordering
try:
    with ordering.execution_lock(sys.argv[1]):
        pass
except ValueError as exc:
    print(exc)
    sys.exit(3)
""", str(other)], cwd=av002.PROJECT_ROOT, capture_output=True, text=True, check=False)
        assert child.returncode == 3 and "in progress" in child.stdout
    with ordering.execution_lock(other):
        pass


@pytest.mark.parametrize("change", ["path", "file_hash", "run_id", "parent", "state", "effect", "rule_verdict", "rule_count"])
def test_release_verifies_actual_result_bytes_and_payload_not_just_row_labels(runner, change):
    av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    rows = hc003.registry_rows(runner.root)
    metrics = json.loads(rows[0]["metrics_json"])
    path = runner.root / metrics["result_file"]
    payload = common.read_result_json(path)
    if change == "run_id":
        payload["run_id"] = "another-run"
    elif change == "parent":
        payload["parent"]["passing_variants"] = ["av_phase"]
    elif change == "state":
        payload["state_sha256"] = "another-state"
    elif change == "effect":
        payload["design_effects"]["S"]["av_all"] = .9
    elif change == "rule_verdict":
        payload["rules"]["O"]["av_conc"]["verdict"] = "fail"
    elif change == "rule_count":
        payload["rules"]["O"]["av_conc"]["primary"]["count"] = 5
    path.write_text(json.dumps(payload))
    for row in rows:
        changed = json.loads(row["metrics_json"])
        if change != "file_hash":
            changed["result_sha256"] = hc003._digest(path)
        if change == "path":
            changed["result_file"] = "outside-results.json"
        row["metrics_json"] = json.dumps(changed)
    with pytest.raises(ValueError, match="AV002"):
        ordering.av002_release(rows, runner.root)


def test_deleted_tracked_AV002_preregistration_cannot_bypass_successor_claim(registered, monkeypatch):
    path = registered / "research/experiments" / av002.EXPERIMENT_ID / "preregistration.md"
    path.unlink()
    monkeypatch.setattr(holdout, "_claim_ordered", no_claim)
    with pytest.raises(ValueError, match="regular file"):
        holdout._claim("EV001_earnings_filing_entry", registered, registered / holdout.LEDGER_PATH,
                       holdout.HOLDOUT_START, holdout.HOLDOUT_END)


@pytest.mark.parametrize("successor", ["EV001_earnings_filing_entry", "LH003_llm_leader_forward", "LH002_llm_hegemony_judge"])
def test_successor_release_check_and_durable_claim_share_the_lock(runner, monkeypatch, successor):
    av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    monkeypatch.setattr(ordering, "release_state", lambda *a: {})
    checked = []
    def claim(*args):
        assert ordering._lock_target(runner.root)[1] in ordering._LOCKS.get()
        checked.append(args[0])
    monkeypatch.setattr(holdout, "_claim_ordered", claim)
    with ordering.execution_lock(runner.root):
        holdout._claim(successor, runner.root, runner.root / holdout.LEDGER_PATH,
                       holdout.HOLDOUT_START, holdout.HOLDOUT_END)
    assert checked == [successor]


@pytest.mark.parametrize("successor", sorted(ordering.AV002_SUCCESSORS))
@pytest.mark.parametrize("failure", [None, "claim", "body"])
def test_successor_session_holds_lock_until_context_exit_and_releases_on_failure(registered, monkeypatch, successor, failure):
    monkeypatch.setattr(ordering, "av002_release", lambda *a: {})
    monkeypatch.setattr(ordering, "registered_releases", lambda *a: {})
    def claim(*a):
        assert ordering._lock_target(registered)[1] in ordering._LOCKS.get()
        if failure == "claim":
            raise RuntimeError("claim failure")
    monkeypatch.setattr(holdout, "_claim_ordered", claim)  # No ledger is opened.
    def exercise():
        with holdout.HoldoutSession(successor, repo_root=registered):
            assert ordering._lock_target(registered)[1] in ordering._LOCKS.get()
            if failure == "body":
                raise RuntimeError("body failure")
    if failure:
        with pytest.raises(RuntimeError, match="failure"):
            exercise()
    else:
        exercise()
    assert holdout._SESSIONS.get() == ()
    with ordering.execution_lock(registered):
        pass
    assert not (registered / holdout.LEDGER_PATH).exists()


def test_release_rejects_registry_summary_tampering_even_with_valid_result_file(runner):
    av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    rows = hc003.registry_rows(runner.root)
    metrics = json.loads(rows[0]["metrics_json"])
    metrics["rules"]["S"]["primary"]["mean"] = .99
    rows[0]["metrics_json"] = json.dumps(metrics)
    with pytest.raises(ValueError, match="verdicts differ"):
        ordering.av002_release(rows, runner.root)


@pytest.mark.parametrize("change", ["experiment", "stage", "variant", "rules", "terminal"])
def test_publication_rejects_non_holdout_or_malformed_family_before_any_write(runner, monkeypatch, change):
    payload = av002.computation.terminal_payload("holdout_aborted", "fixture", av002.VARIANTS,
        design=runner.design, registration=av002._registration(runner.root))
    if change == "experiment":
        payload["experiment_id"] = av001.EXPERIMENT_ID
    elif change == "stage":
        payload["stage"] = "design_validation"
    elif change == "variant":
        del payload["variants"]["av_conc"]
    elif change == "rules":
        payload["rules"]["extra"] = {}
    else:
        payload["variants"]["av_all"]["verdict"] = "data_incomplete"
    monkeypatch.setattr(common, "write_results", no_claim)
    with pytest.raises(ValueError, match="exactly two"):
        av002.publish(payload, repo_root=runner.root, registration=av002._registration(runner.root))


@pytest.mark.parametrize("mode", ["--dry-run", "--preflight", "--reproduce", "--rehearse", "--holdout", None])
def test_CLI_all_modes_and_default_dispatch_data_dir_without_design_trials(monkeypatch, capsys, mode, tmp_path):
    calls = []
    for name in ("preflight", "reproduce", "rehearse", "run_holdout"):
        def run(name=name, **kwargs):
            calls.append((name, kwargs["data_dir"]))
            return {"stage": name, "verdict": "fail", "variants": {}}
        monkeypatch.setattr(av002, name, run)
    monkeypatch.setattr(av002, "load_parent", lambda *a: None)
    assert av002.main(([mode] if mode else []) + ["--data-dir", str(tmp_path)]) == 0
    assert calls == [({"--reproduce": "reproduce", "--rehearse": "rehearse", "--holdout": "run_holdout"}.get(mode, "preflight"), tmp_path)]
    assert json.loads(capsys.readouterr().out)["stage"]
    with pytest.raises(SystemExit):
        av002.main(["--reproduce", "--holdout"])


def test_rehearsal_memory_gate_refuses_before_any_computation_or_writes(registered, monkeypatch):
    monkeypatch.setattr(av002, "load_parent", lambda *a: ({}, {}))
    monkeypatch.setattr(av002, "available_memory_bytes", lambda: 4_999_999_999)
    monkeypatch.setattr(av002.computation, "rehearse_holdout", no_claim)
    with pytest.raises(MemoryError, match=">= 5 GB"):
        av002.rehearse(data_dir=registered, repo_root=registered)
    assert not (registered / "research/experiments" / av002.EXPERIMENT_ID / "rehearsal").exists()


def test_rehearsal_runs_real_shared_math_for_fixed_2025_window_and_writes_only_hashed_artifacts(registered, monkeypatch):
    monkeypatch.setattr(av002, "load_parent", lambda *a: ({"library_versions": {}}, {}))
    monkeypatch.setattr(av002, "available_memory_bytes", lambda: 5_000_000_000)
    monkeypatch.setattr(av002.computation, "N_CONTROLS", 2)
    monkeypatch.setattr(av002.computation, "source_hashes", lambda *a, **k: {"prices": {"fixture": "sha"}, "code": {}})
    monkeypatch.setattr(av002.trading_calendar, "preflight_price_sessions", lambda *a, **k: {})
    monkeypatch.setattr(holdout, "_claim", no_claim)
    monkeypatch.setattr(experiment_registry, "record_trials", no_claim)
    days = av002.trading_calendar.session_calendar("2015-01-01", "2025-06-30")
    prices = prices_for(days)
    calls = []
    def prepare(**kwargs):
        end, close = kwargs["end"], kwargs.get("checkpoint_close")
        calls.append((end.date().isoformat(), None if close is None else close.date().isoformat()))
        selections = [av_selection(r["decision_date"], r["trade_date"], r["end_date"],
            {"000010": 2, "000020": 4}, kind=r["end_kind"], month=r["month"]) for r in kwargs["planned"]]
        if close is not None:
            selections[0].update(benchmark_start_date=close.date().isoformat(), benchmark_start_kind="close")
        frame = prices.loc[prices.index.get_level_values("trade_date") <= end]
        book = hc003.PriceBook.from_windows([frame], days[days <= end],
            [r["trade_date"] for r in selections] + [r["end_date"] for r in selections if r["end_kind"] == "open"], end,
            close_dates=[] if close is None else [close], experiment_id=av002.EXPERIMENT_ID, repo_root=registered)
        version = SimpleNamespace(sensitivity_available=True)
        return {"book": book, "selections": {r: copy.deepcopy(selections) for r in av001.RULES},
            "raise_complete": True, "versions": {r: version for r in av001.RULES}}
    monkeypatch.setattr(av002.computation, "prepare", prepare)
    result = av002.rehearse(data_dir=registered, repo_root=registered)
    assert calls == [("2024-12-30", None), ("2025-06-30", "2024-12-30")]
    assert result["passed"] and result["start"] == "2025-01-01" and result["end"] == "2025-06-30"
    for name, sha in result["outputs"]["files"].items():
        path = Path(name)
        assert path.is_relative_to(registered / "research/experiments" / av002.EXPERIMENT_ID / "rehearsal")
        assert hc003._digest(path) == sha
        if ".json" in path.suffixes:
            payload = common.read_result_json(path)
            assert all(payload["rules"][r][v]["primary"]["count"] == 6 for r in av001.RULES for v in av001.VARIANTS)
    assert hc003.registry_rows(registered) == []
    assert not (registered / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("failure", ["changed_inputs", "missing_month"])
def test_rehearsal_must_complete_and_preserve_input_hashes_before_saving(registered, monkeypatch, failure):
    monkeypatch.setattr(av002, "load_parent", lambda *a: ({"library_versions": {}}, {}))
    monkeypatch.setattr(av002, "available_memory_bytes", lambda: 5_000_000_000)
    count = []
    def hashes(*a, **k):
        count.append(1)
        return {"prices": {"file": len(count) if failure == "changed_inputs" else 1}, "code": {}}
    monkeypatch.setattr(av002.computation, "source_hashes", hashes)
    monkeypatch.setattr(av002.computation, "rehearse_holdout", lambda **k: {
        "rules": {r: {v: {"primary": {"count": 5}} for v in av001.VARIANTS} for r in av001.RULES}})
    with pytest.raises(ValueError, match="changed|six observations"):
        av002.rehearse(data_dir=registered, repo_root=registered)
    assert not (registered / "research/experiments" / av002.EXPERIMENT_ID / "rehearsal").exists()


def test_manifest_seals_dependencies_and_allowed_changes_ignoring_unrelated_work(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "--quiet")
    (tmp_path / "code.py").write_text("original\n")
    runner_path = tmp_path / "backtesting/experiments/av002.py"
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text("fixture runner\n")
    (tmp_path / "parallel.py").write_text("unrelated original\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture code")
    parent = {"code_commit": git("rev-parse", "HEAD"), "source_hashes": {"code": {"code.py": hc003._digest(tmp_path / "code.py")}}}
    (tmp_path / "code.py").write_text("changed\n")
    (tmp_path / "new.py").write_text("new\n")
    unrelated = tmp_path / "parallel.py"
    unrelated.write_text("unrelated changed\n")
    (tmp_path / "parallel.sh").write_text("untracked unrelated\n")
    monkeypatch.setattr(av002, "ALLOWED_CODE_CHANGES", (av002.RUNNER_SOURCE, "new.py"))
    manifest = av002.code_manifest(repo_root=tmp_path, parent=parent, run_id="fixture-run")
    assert not manifest["committed"]
    payload = common.read_result_json(tmp_path / manifest["file"])
    assert set(payload["changed_code_files"]) == {"code.py", "new.py"}
    assert payload["run_id"] == "fixture-run"
    assert set(manifest["source_hashes"]) == {"code.py", "new.py", av002.RUNNER_SOURCE}
    assert set(payload["uncommitted_code_files"]) == {"code.py", "new.py"}
    unrelated.write_text("changed by parallel work\n")
    av002._verify_code(manifest, tmp_path)
    (tmp_path / "code.py").write_text("changed after seal\n")
    with pytest.raises(ValueError, match="code changed"):
        av002._verify_code(manifest, tmp_path)
    (tmp_path / manifest["file"]).write_text("changed manifest\n")
    with pytest.raises(ValueError, match="manifest changed"):
        av002._verify_code(manifest, tmp_path)


def test_rehearsal_output_cannot_escape_worktree_via_symlink(tmp_path):
    directory = tmp_path / "research/experiments" / av002.EXPERIMENT_ID
    directory.mkdir(parents=True)
    (directory / "rehearsal").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        av002._output_directory(tmp_path, "rehearsal")


def test_batch_validates_all_metrics_before_append_and_captured_abort_uses_original_commit(registered):
    registration = av002._registration(registered)
    trials = [("holdout_av_all", {"stage": "holdout"}, "holdout_aborted"),
              ("holdout_av_conc", {"invalid": float("nan")}, "holdout_aborted")]
    with pytest.raises(ValueError, match="finite"):
        experiment_registry.record_trials(av002.EXPERIMENT_ID, trials, repo_root=registered)
    assert not (registered / experiment_registry.REGISTRY_PATH).exists()
    trials[1] = ("holdout_av_conc", {"stage": "holdout"}, "holdout_aborted")
    prereg = registered / registration.path
    prereg.write_text(prereg.read_text() + "edited mid-attempt\n")
    experiment_registry.record_trials(av002.EXPERIMENT_ID, trials, repo_root=registered, aborted_registration=registration)
    rows = hc003.registry_rows(registered)
    assert len(rows) == 2 and all(r["prereg_commit"] == registration.commit_hash for r in rows)
    with pytest.raises(ValueError, match="already recorded"):
        experiment_registry.record_trials(av002.EXPERIMENT_ID, trials, repo_root=registered, aborted_registration=registration)


@pytest.mark.parametrize("text,expected", [("MemAvailable: 5000000 kB\n", 5_120_000_000), ("MemFree: 42 kB\n", None)])
def test_available_memory_requires_the_real_MemAvailable_field(monkeypatch, text, expected):
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: text)
    if expected is None:
        with pytest.raises(ValueError, match="MemAvailable is unavailable"):
            av002.available_memory_bytes()
    else:
        assert av002.available_memory_bytes() == expected


@pytest.mark.parametrize("trials", [[], [("v", {}, "fail"), ("v", {}, "fail")], [("", {}, "fail")]])
def test_invalid_trial_batch_has_no_registry_side_effects(registered, trials):
    with pytest.raises(ValueError):
        experiment_registry.record_trials(av002.EXPERIMENT_ID, trials, repo_root=registered)
    assert not (registered / experiment_registry.REGISTRY_PATH).exists()


def test_captured_batch_rejects_nonabort_and_foreign_registry_commit(registered):
    registration = av002._registration(registered)
    with pytest.raises(ValueError, match="requires holdout_aborted"):
        experiment_registry.record_trials(av002.EXPERIMENT_ID, [("v", {}, "pass")], repo_root=registered,
                                          aborted_registration=registration)
    path = registered / experiment_registry.REGISTRY_PATH
    path.write_text(",".join(experiment_registry.REGISTRY_COLUMNS) + "\nfixture," + av002.EXPERIMENT_ID + ",foreign,v,fail,{},note\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="prereg_commit differs"):
        experiment_registry.record_trials(av002.EXPERIMENT_ID, [("other", {}, "fail")], repo_root=registered)
    assert path.read_bytes() == before


@pytest.mark.parametrize("available", [4_999_999_999, 5_000_000_000])
def test_memory_is_rechecked_after_preopen_work_immediately_before_claim(runner, monkeypatch, available):
    def memory():
        assert runner.events[-1] == "verify_code"
        assert ordering._lock_target(runner.root)[1] in ordering._LOCKS.get()
        runner.events.append("claim_memory")
        return available
    monkeypatch.setattr(av002, "available_memory_bytes", memory)
    if available < 5_000_000_000:
        with pytest.raises(MemoryError, match="claim requires >= 5 GB"):
            av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
        assert not runner.used and hc003.registry_rows(runner.root) == []
    else:
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
        assert runner.events.index("claim_memory") + 1 == runner.events.index("claim")


@pytest.mark.parametrize("registry_state", ["absent", "empty", "header", "existing"])
@pytest.mark.parametrize("aborted", [False, True])
def test_terminal_batch_is_durable_atomic_and_releases_for_normal_and_aborted_runs(
        runner, monkeypatch, registry_state, aborted):
    # Exercise the merged durable ledger claim too, using only the temporary repo.
    monkeypatch.setattr(holdout, "HoldoutSession", runner.real_session)
    monkeypatch.setattr(hc003, "ledger_experiments", runner.real_ledger_experiments)
    path = runner.root / experiment_registry.REGISTRY_PATH
    if registry_state == "empty":
        path.write_bytes(b"")
    elif registry_state == "header":
        path.write_text(",".join(experiment_registry.REGISTRY_COLUMNS) + "\n")
    elif registry_state == "existing":
        experiment_registry.record_trial(av001.EXPERIMENT_ID, "fixture", {}, "fail", repo_root=runner.root)
    before = path.read_bytes() if path.exists() else None
    if aborted:
        def fail(*a, **k):
            raise RuntimeError("injected post-claim failure")
        monkeypatch.setattr(av002.computation, "evaluate_holdout", fail)
    synced, appended = set(), []
    original_sync, original_replace = os.fsync, os.replace
    def sync(fd):
        synced.add(Path(os.readlink(f"/proc/self/fd/{fd}")))
        original_sync(fd)
    def replace(source, destination):
        assert ordering._lock_target(runner.root)[1] in ordering._LOCKS.get()
        if destination == runner.root / holdout.LEDGER_PATH:
            return original_replace(source, destination)
        assert destination == path
        assert (path.read_bytes() if path.exists() else None) == before
        with append_only.text_buffer(Path(source).read_bytes(), path) as handle:
            rows = experiment_registry._registry_rows(handle)
        terminal = [r for r in rows if r["experiment_id"] == av002.EXPERIMENT_ID]
        assert len(terminal) == 2
        metrics = json.loads(terminal[0]["metrics_json"])
        result = runner.root / metrics["result_file"]
        assert {result, result.with_suffix(".md"), result.parent} <= synced
        assert Path(source) in synced  # Registry temp file is durable before replacement.
        appended.append(terminal)
        original_replace(source, destination)
    monkeypatch.setattr(append_only.os, "fsync", sync)
    monkeypatch.setattr(append_only.os, "replace", replace)
    if aborted:
        with pytest.raises(RuntimeError, match="injected post-claim"):
            av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    else:
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert len(appended) == 1 and path.parent in synced
    ledger = runner.root / holdout.LEDGER_PATH
    assert [json.loads(line)["experiment_id"] for line in ledger.read_text().splitlines()] == [av002.EXPERIMENT_ID]
    assert holdout._SESSIONS.get() == ()
    rows = hc003.registry_rows(runner.root)
    assert len(rows) == (3 if registry_state == "existing" else 2)
    assert all(r["verdict"] == ("holdout_aborted" if aborted else "pass") for r in appended[0])
    assert ordering.av002_release(rows, runner.root)["terminal_holdout_variants"] == list(av002.VARIANTS)
    with ordering.execution_lock(runner.root):
        pass


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("aborted", [False, True])
def test_terminal_batch_replace_failure_preserves_all_prior_bytes(registered, monkeypatch, existing, aborted):
    path = registered / experiment_registry.REGISTRY_PATH
    if existing:
        experiment_registry.record_trial(av001.EXPERIMENT_ID, "fixture", {}, "fail", repo_root=registered)
    before = path.read_bytes() if existing else None
    registration = av002._registration(registered)
    trials = [("holdout_" + v, {}, "holdout_aborted" if aborted else "pass") for v in av002.VARIANTS]
    kwargs = {"aborted_registration": registration} if aborted else {}
    def fail(*a):
        raise OSError("injected batch replacement failure")
    with monkeypatch.context() as patch:
        patch.setattr(append_only.os, "replace", fail)
        with pytest.raises(OSError, match="batch replacement"):
            experiment_registry.record_trials(av002.EXPERIMENT_ID, trials, repo_root=registered, **kwargs)
    assert (path.read_bytes() if path.exists() else None) == before
    assert not list(path.parent.glob(".registry.csv.*.tmp"))
    experiment_registry.record_trials(av002.EXPERIMENT_ID, trials, repo_root=registered, **kwargs)
    assert experiment_registry.trial_count(av002.EXPERIMENT_ID, repo_root=registered) == 2


def test_sealed_manifest_syncs_every_file_and_directory_before_return(tmp_path, monkeypatch):
    synced = []
    original = os.fsync
    def sync(fd):
        synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
        original(fd)
    monkeypatch.setattr(append_only.os, "fsync", sync)
    directory = tmp_path / "code_manifests"
    outputs = av002._save({"stage": "code_change_manifest"}, directory)
    files = {Path(p) for p in outputs["files"]} | {Path(outputs["hash_manifest"])}
    assert files | {directory} <= set(synced)
    assert synced[-1] == directory



def test_manifest_fsync_failure_refuses_before_claim(runner, monkeypatch):
    def fail(fd):
        raise OSError("injected artifact fsync failure")
    def manifest(**kwargs):
        return av002._save({"stage": "code_change_manifest"}, runner.root / "code_manifests")
    monkeypatch.setattr(av002, "code_manifest", manifest)
    monkeypatch.setattr(append_only.os, "fsync", fail)
    with pytest.raises(OSError, match="artifact fsync"):
        av002.run_holdout(data_dir=runner.root, repo_root=runner.root)
    assert not runner.used and hc003.registry_rows(runner.root) == []


def test_result_fsync_failure_refuses_before_registry_append(runner, monkeypatch):
    payload = av002.computation.terminal_payload("holdout_aborted", "fixture", av002.VARIANTS,
        design=runner.design, registration=av002._registration(runner.root))
    payload.update(parent=av002.parent_link(), run_id="fixture", code_manifest=None)
    def fail(fd):
        raise OSError("injected result fsync failure")
    monkeypatch.setattr(append_only.os, "fsync", fail)
    monkeypatch.setattr(experiment_registry, "record_trials", no_claim)
    with pytest.raises(OSError, match="result fsync"):
        av002.publish(payload, repo_root=runner.root, registration=av002._registration(runner.root), aborted=True)
    assert hc003.registry_rows(runner.root) == []
