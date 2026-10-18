"""Round 21 regressions: offline fixtures only, no original protected reads."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import hc003, ordering
from backtesting.experiments.lh003 import EVALUATION_AT, EXPERIMENT_ID, Refused
from backtesting.experiments.lh003 import calendar, environment, evaluate, inputs, runner, workflow
from backtesting.experiments.lh003.resources import ProcessTreeMemory
from backtesting.experiments.lh003.simulation import FakeCodex, synthetic_distribution
from backtesting.experiments.lh003.storage import attempts, aware, digest, seal, verified
from src.ingestion import dart_quarterly
from src.ingestion.storage import read_rows, write_rows
from src.runner import trading_calendar as shared_calendar
from test_experiments_lh003_evaluate import experiment, fake_cost, pinned_start
from test_experiments_lh003_runner import Clock, forbid_real_calls_and_holdout, verified_calendar
from test_experiments_lh003_snapshot import lifecycle, source, source_fixture, snapshot_for

REAL_LH003_RELEASES = ordering.lh003_releases


def test_corrected_shared_calendar_excludes_election_and_constitution_holidays():
    sessions = calendar.exchange().sessions
    assert pd.Timestamp("2026-06-03") not in sessions
    assert pd.Timestamp("2026-07-17") not in sessions


def test_start_needs_only_first_slot_calendar_not_future_2027(lifecycle, monkeypatch):
    arguments, calls = lifecycle
    monkeypatch.setattr(shared_calendar, "CALENDAR_VERIFIED_THROUGH", "2026-12-31")
    monkeypatch.setattr(shared_calendar, "SPECIAL_CLOSES", {})
    assert workflow.seal_holdout(**arguments)["snapshot_sha256"]
    assert calls == ["fake claim"] and len(calendar.WEEKS) == 52


def test_special_close_used_and_unverified_price_day_refuses(monkeypatch):
    assert calendar.session_close("2026-11-19") == aware("2026-11-19T16:30:00+09:00")
    monkeypatch.setattr(shared_calendar, "SPECIAL_CLOSES", {})
    shared_calendar.daily_session_close.cache_clear()
    with pytest.raises(ValueError, match="special_session_coverage_unverified"):
        calendar.session_close("2026-11-19")


def test_entire_missing_fixed_week_refuses_instead_of_dropping_or_extending_it():
    days = calendar.exchange().sessions
    days = days[(days < "2027-03-01") | (days > "2027-03-05")]
    with pytest.raises(Refused, match="no XKRX session in fixed week"):
        calendar.plan(days)


def test_unverified_year_never_substitutes_last_completed_week_of_previous_year(monkeypatch):
    monkeypatch.setattr(shared_calendar, "CALENDAR_VERIFIED_THROUGH", "2026-12-31")
    with pytest.raises(ValueError, match="calendar_unverified"):
        calendar.latest_completed_week(aware("2027-01-10T12:00:00+09:00"))


def test_twentieth_session_cannot_extend_past_fixed_evaluation_cutoff():
    days = calendar.exchange().sessions
    # Keep every decision week, but remove enough later execution days that the
    # twentieth session would move beyond the fixed as-of date.
    days = days[(days < "2027-11-08") | (days > "2027-11-22")]
    with pytest.raises(Refused, match="twentieth session"):
        calendar.plan(days)


def test_required_h1_file_existence_checked_without_content_reads_before_claim(lifecycle, monkeypatch):
    arguments, calls = lifecycle
    inputs.price_path(arguments["data_dir"], "2026-06-02").unlink()
    original = Path.open
    def no_h1(path, *args, **kwargs):
        if path.stem.isdigit() and "20260101" <= path.stem <= "20260630":
            pytest.fail("protected contents opened before claim")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_h1)
    with pytest.raises(ValueError, match="calendar_price_session_mismatch.*2026-06-02"):
        workflow.seal_holdout(**arguments)
    assert not calls


def test_required_non_H1_cycle_history_is_checked_before_claim(lifecycle):
    arguments, calls = lifecycle
    inputs.price_path(arguments["data_dir"], "2025-11-04").unlink()
    with pytest.raises(ValueError, match="calendar_price_session_mismatch.*2025-11-04"):
        workflow.seal_holdout(**arguments)
    assert not calls


def test_lh003_av002_release_runs_inside_shared_lock_before_claim(lifecycle, monkeypatch):
    arguments, calls = lifecycle
    monkeypatch.setattr(ordering, "lh003_releases", REAL_LH003_RELEASES)
    monkeypatch.setattr(ordering, "release_state", lambda *a: {})
    checked = []
    def pending(rows, repo):
        assert ordering._lock_target(repo)[1] in ordering._LOCKS.get()
        checked.append("AV002")
        raise Refused("AV002 both verified terminal rows missing")
    monkeypatch.setattr(ordering, "av002_release", pending)
    with pytest.raises(Refused, match="AV002.*both"):
        workflow.seal_holdout(**arguments)
    assert checked == ["AV002"] and not calls


def test_full_distribution_provides_new_versions_after_sync_without_research_h1_reread(source, tmp_path, monkeypatch):
    snapshot = snapshot_for(source, tmp_path)
    archive = source / "fundamentals/dart_quarterly/2026_11012.jsonl"
    original = read_rows(archive)[0]
    added = {**original, "rcept_no": "20261112000001", "available_date": "2026-11-12"}
    dart_quarterly._publish(archive.parent, 2026, "11012", [added])
    listing = source / "disclosures/dart_full/list/2026/20261112.jsonl"
    write_rows(listing, [{"corp_code": added["corp_code"], "stock_code": added["stock_codes"][0],
                         "rcept_no": added["rcept_no"], "rcept_dt": "20261112", "report_nm": "정정 반기보고서 (2026.06)"}])
    snapshot.distribution, _ = synthetic_distribution(source, tmp_path / "updated", at=aware("2026-11-13T18:00:00+09:00"))
    forbidden = {(source / name).resolve() for name in snapshot.manifest["files"]}
    opened = Path.open
    def guard(path, *args, **kwargs):
        if path.resolve() in forbidden:
            pytest.fail("protected original read after full-file rewrite")
        return opened(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guard)
    prepared = inputs.prepare(source, snapshot, "2026-11-13")
    assert any(r["rcept_no"] == added["rcept_no"] and r["date_reason"] == "linked"
               for r in prepared["financial_dates"]["annotations"])


def test_new_captured_archive_does_not_require_raw_aggregate_after_publication(source, tmp_path):
    from backtesting.experiments.lh003.snapshot import input_view
    snapshot = snapshot_for(source, tmp_path)
    path = source / "fundamentals/dart_quarterly/2026_11014.jsonl"
    assert not path.exists()
    row = {**read_rows(source / "fundamentals/dart_quarterly/2026_11012.jsonl")[0],
           "reprt_code": "11014", "fiscal_quarter": "2026Q3", "available_date": "2026-11-12", "rcept_no": "20261112000001"}
    write_rows(path, [row])
    write_rows(source / "disclosures/dart_full/list/2026/20261112.jsonl", [{"corp_code": row["corp_code"],
        "stock_code": row["stock_codes"][0], "rcept_no": row["rcept_no"], "rcept_dt": "20261112",
        "report_nm": "분기보고서 (2026.09)"}])
    snapshot.distribution, _ = synthetic_distribution(source, tmp_path / "updated", at=aware("2026-11-13T18:00:00+09:00"))
    path.unlink()
    with input_view(source, snapshot, end="20261113") as view:
        assert read_rows(view / path.relative_to(source)) == [row]


def test_rule_S_excludes_merged_conflicts_and_retains_prior_date_scope(source, tmp_path):
    from backtesting.experiments.lh003.snapshot import input_view
    snapshot = snapshot_for(source, tmp_path)
    original = read_rows(source / "disclosures/dart_full/list/2026/20260815.jsonl")[0]
    path = source / "disclosures/dart_full/list_supplement/2026/20260815.jsonl"
    write_rows(path, [{**original, "stock_code": "999990"}])
    same_day = {**original, "rcept_no": "20261113000001", "rcept_dt": "20261113"}
    write_rows(source / "disclosures/dart_full/list/2026/20261113.jsonl", [same_day])
    write_rows(source / "disclosures/dart_full/list_supplement/2026/20261113.jsonl",
               [{**same_day, "corp_code": "99999999"}])
    snapshot.distribution, _ = synthetic_distribution(source, tmp_path / "updated", at=aware("2026-11-13T18:00:00+09:00"))
    with input_view(source, snapshot, end="20261113") as view:
        known, dates = inputs.financials(view, "2026-11-13")
    assert original["rcept_no"] not in set(known.rcept_no)
    assert not any(r["rcept_no"] == original["rcept_no"] for r in dates["annotations"])
    assert not any(r["rcept_no"] == same_day["rcept_no"] for r in dates["annotations"])


def test_dry_environment_never_enters_real_path_without_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "synthetic_format_trial", lambda **k: pytest.fail("real trial without explicit flag"))
    report = environment.verify_environment(data_dir=tmp_path, clock=Clock("2026-10-17T12:00:00+09:00"))
    assert report["real_model_calls"] == 0 and not report["timing"]["real_path_verified"] and not report["passed"]
    assert report["performance"]["includes_children"] and not report["performance"]["fits"]


def test_permitted_synthetic_trial_exercises_real_runner_path_with_fake_and_durable_six_calls(tmp_path, source_fixture, monkeypatch):
    monkeypatch.setattr(environment, "synthetic_archive", lambda *a, **k: source_fixture)
    process = FakeCodex()
    clock = Clock()
    report = environment.verify_environment(data_dir=tmp_path / "absent_source", state_root=tmp_path,
        timeout=30, clock=clock, real_format_trial=True, subprocess_call=process)
    result = report["synthetic_format_trial"]
    assert not report["passed"] and not report["timing"]["real_path_verified"] and report["real_model_calls"] == 0
    assert result["completed"] and result["attempts"] == process.execs == 6
    assert result["synthetic_inputs"] and not result["real_call_path"]
    assert result["six_call_seconds"] > 0 and result["memory"]["includes_children"]
    assert len(attempts(tmp_path)) == 6 and all(a["synthetic_format"] for a in attempts(tmp_path))
    assert environment.synthetic_format_trial(root=tmp_path, day="2026-11-13", timeout=30,
        clock=clock, subprocess_call=process) == result
    assert process.execs == 6


def test_real_format_trial_cli_requires_explicit_mode_and_durable_state(tmp_path):
    from backtesting.experiments.lh003 import __main__ as cli
    for arguments in (["--real-format-trial"], ["--real-format-trial", "--verify-environment"],
                      ["--real-format-trial", "--run-week", "--state-dir", str(tmp_path)]):
        with pytest.raises(SystemExit):
            cli.main(arguments)


def test_format_calls_cannot_reset_eight_attempt_cap(tmp_path):
    process = FakeCodex()
    model = runner.LlmRunner(tmp_path, subprocess_call=process, format_trial=True, clock=Clock())
    model.identity()
    for number in range(1, 9):
        seal(tmp_path / "attempts" / f"{number:06d}.started.json",
             {"number": number, "key": "prior_interrupted_format", "synthetic_format": True})
    result = model.call({"prompt": "synthetic", "schema": {}, "reask": "fixed"}, 0,
                        week="format", deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and result["reason"] == "synthetic_format_cap_8" and process.execs == 0
    assert len(attempts(tmp_path)) == 8


def test_process_tree_measurement_includes_grandchildren():
    child = "import subprocess,sys; subprocess.run([sys.executable, '-c', 'import time; payload=bytearray(64*1024*1024); time.sleep(0.3)'],check=True)"
    with ProcessTreeMemory() as memory:
        subprocess.run([sys.executable, "-c", child], check=True)
    report = memory.report()
    assert report["verified"] and report["children_peak_rss_mib"] > 60
    assert report["peak_rss_mib"] > report["parent_peak_rss_mib"] + 40


@pytest.mark.parametrize("missing", ["real_path", "children", "memory", "route"])
def test_environment_call_and_tree_memory_gates_precede_claim(lifecycle, missing):
    arguments, calls = lifecycle
    report = verified(arguments["environment_report"])
    if missing == "real_path":
        report["synthetic_format_trial"]["real_call_path"] = False
    elif missing == "children":
        report["performance"]["includes_children"] = False
    elif missing == "memory":
        report["performance"]["fits"] = False
    else:
        report["sync"]["transfer_route"] = None
    path = arguments["repo_root"] / "bad_gate.json"
    seal(path, report)
    arguments["environment_report"] = path
    with pytest.raises(Refused, match="environment"):
        workflow.seal_holdout(**arguments)
    assert not calls


def evaluation_setup(tmp_path, monkeypatch):
    root = tmp_path / "state"
    pinned_start(root, tmp_path)
    monkeypatch.setattr(experiment_registry, "verify_preregistration",
                        lambda *a, **k: SimpleNamespace(commit_hash="fixture", fields={}))
    return root


def test_completed_canonical_evaluation_blocks_rollback_before_any_computation(tmp_path, monkeypatch):
    root = evaluation_setup(tmp_path, monkeypatch)
    result = evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    path = root / "evaluation.json"
    saved = path.read_bytes()
    path.unlink()
    monkeypatch.setattr(evaluate, "load_week", lambda *a: pytest.fail("second evaluation after backup rollback"))
    with pytest.raises(Refused, match="restore only sealed result hash " + digest(result)):
        evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    seal(path, {**result, "verdict": "fail"})
    with pytest.raises(Refused, match="canonical"):
        evaluate.publish_evaluation(root=root, repo_root=tmp_path)
    path.unlink()
    path.write_bytes(saved)
    assert evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT)) == result
    assert len(hc003.registry_rows(tmp_path)) == 1


def test_sealed_evaluation_registry_crash_recovers_without_computing(tmp_path, monkeypatch):
    root = evaluation_setup(tmp_path, monkeypatch)
    original = experiment_registry.record_trial
    def crash(*a, **k):
        raise OSError("registry crash")
    monkeypatch.setattr(experiment_registry, "record_trial", crash)
    with pytest.raises(OSError, match="registry crash"):
        evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert (root / "evaluation.json").exists()
    monkeypatch.setattr(experiment_registry, "record_trial", original)
    monkeypatch.setattr(evaluate, "evaluate", lambda *a, **k: pytest.fail("recomputed sealed verdict"))
    result = evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert json.loads(hc003.registry_rows(tmp_path)[0]["metrics_json"])["result_sha256"] == digest(result)


@pytest.mark.parametrize("name", ["backtesting/experiments/lh001/evaluate.py", "backtesting/cost_model.py",
    "backtesting/experiments/lh003/__init__.py", "backtesting/experiments/lh001/__init__.py", "src/runner/trading_calendar.py",
    "src/ingestion/dart_filing_dates.py", "src/runner/event_evidence.py", "src/research/industry_map.py"])
def test_evaluation_verifies_all_pinned_algorithms_before_computing(tmp_path, monkeypatch, name):
    root = evaluation_setup(tmp_path, monkeypatch)
    path = tmp_path / name
    path.write_text(path.read_text() + "\n# changed algorithm\n")
    monkeypatch.setattr(evaluate, "load_week", lambda *a: pytest.fail("loaded weeks after algorithm drift"))
    with pytest.raises(Refused, match="pinned implementation"):
        evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert hc003.registry_rows(tmp_path) == []


def test_not_started_recovers_seal_to_registry_crash_under_existing_hash(lifecycle, monkeypatch):
    arguments, calls = lifecycle
    original = experiment_registry.record_trial
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: (_ for _ in ()).throw(OSError("append crash")))
    with pytest.raises(OSError, match="append crash"):
        workflow.not_started(arguments["root"], "sealed failure", repo_root=arguments["repo_root"], at=arguments["clock"]())
    sealed = verified(arguments["root"] / "not_started.json")
    monkeypatch.setattr(experiment_registry, "record_trial", original)
    for _ in range(2):
        with pytest.raises(Refused, match="cannot be restarted"):
            workflow.seal_holdout(**arguments)
    rows = hc003.registry_rows(arguments["repo_root"])
    assert len(rows) == 1 and json.loads(rows[0]["metrics_json"])["result_sha256"] == digest(sealed)
    assert verified(arguments["root"] / "not_started.json") == sealed and not calls


def test_500_stocks_22_invalid_weeks_stream_all_scenarios_with_bounded_memory(tmp_path, monkeypatch):
    decisions, raw = experiment()
    codes = [f"{10 * (i + 1):06d}" for i in range(500)]
    for decision in decisions:
        index = decision["index"]
        record = raw[index]
        template = next(iter(record["quotes"].values()))
        record["quotes"] = {code: {**template, "stock_code": code} for code in codes}
        decision.update(a_codes=codes, universe_codes=codes, candidate_codes=codes[:300])
        if index < 22:
            decision.update(valid=False, policy_codes=codes, avoid=[], reasons=["synthetic invalid"])
        record["decision_sha256"] = digest(decision)
    root = evaluation_setup(tmp_path, monkeypatch)
    applied = runner.verify_pinned(root, tmp_path)
    for decision in decisions:
        decision.update(code_pins=applied, sealed_at=decision["decision_date"] + "T18:00:00+09:00")
        raw[decision["index"]]["decision_sha256"] = digest(decision)
    for index, record in raw.items():
        seal(root / "executions/raw" / f"{index:02d}.json", record)
    monkeypatch.setattr(evaluate, "load_week", lambda root, index: decisions[index])
    monkeypatch.setattr(evaluate.cost_model, "one_way_cost", fake_cost)
    memory = ProcessTreeMemory()
    memory.sample()
    baseline = memory.peak_mib
    with memory:
        result = evaluate.evaluate_saved(root=root, repo_root=tmp_path, clock=Clock(EVALUATION_AT))
    assert result["primary"]["count"] == 52 and result["valid_decisions"] == 30
    records = result["execution_record_hashes"]
    assert len(records) == 52 * 6 and memory.peak_mib - baseline < 256
    for sensitivity in ("main", "delisting_loss"):
        for scenario in ("1.0", "1.5", "2.0"):
            name = f"{sensitivity}/v2_{scenario}/00.json"
            artifact = verified(root / "executions" / name)
            assert digest(artifact) == records[name]
            record = artifact["record"]
            assert record["policy"] == {"same_as": "a"}
            assert record["controls"] == {"same_as": "a", "paths": 200}
            assert record["a"]["count"] == 500
    seal(tmp_path / "memory.json", {**memory.report(), "increment_mib": memory.peak_mib - baseline,
                                   "stocks": 500, "invalid_weeks": 22, "weekly_scenario_records": len(records)})
