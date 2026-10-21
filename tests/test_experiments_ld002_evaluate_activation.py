from copy import deepcopy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments import ordering
from backtesting.experiments.ld002 import EXPERIMENT_ID, Refused
from backtesting.experiments.ld002 import activation
from backtesting.experiments.ld002 import evaluate as evaluation
from backtesting.experiments.ld002.calendar import Calendar
from backtesting.experiments.ld002.storage import aware, digest, file_hash, seal, verified
from backtesting.experiments.ld002.workflow import book_name
from ld002_fixtures import CODES, FakeCalendar, identity
from scripts.ld002.__main__ import main, next_finalization_day


def records():
    days = pd.bdate_range("2026-11-16", "2027-11-15").strftime("%Y-%m-%d").tolist()
    nav, result = 100e6, []
    for i, day in enumerate(days):
        nav *= 1.001 + .0004 * math.sin(i * 1.7)
        def book(value):
            return {"state": {"nav": value, "cash": value, "positions": {}}, "unresolved_value": 0,
                    "cost": 0, "turnover": 0, "turnover_scale": 1}
        ld, reference, random = book(nav), book(100e6), book(100e6)
        books = {book_name(kind, m, k): (ld if kind == "ld" else random if kind == "random" else reference)
            for m in (1.0, 1.5, 2.0) for kind in ("ld", "ew", "decomposition", "av", "random")
            for k in (range(200) if kind == "random" else [None])}
        result.append({"day": day, "books": books, "valid_decision": True, "required_files": {"KOSPI": True, "KOSDAQ": True},
                       "missing_reason": None})
    return result, days


def test_nw_bartlett_lag_five_no_small_sample_correction_normal_tail():
    values = np.array([.01, -.002, .008, -.006, .007, .003, .002, -.001])
    result = evaluation.statistics(values)
    centered = values - values.mean()
    variance = np.dot(centered, centered) / len(values)
    for lag in range(1, 6):
        variance += 2 * (1 - lag / 6) * np.dot(centered[lag:], centered[:-lag]) / len(values)
    se = math.sqrt(variance / len(values))
    assert result["lag"] == 5 and result["standard_error"] == pytest.approx(se)
    assert result["t_stat"] == pytest.approx(values.mean() / se)
    assert result["p_one_sided"] == pytest.approx(.5 * math.erfc(result["t_stat"] / math.sqrt(2)))
    assert evaluation.statistics([.1] * 150)["t_stat"] is None
    with pytest.raises(Refused, match="cannot be deleted"):
        evaluation.statistics([.1, float("nan")])


def test_completed_passing_path_with_all_daily_observations_and_reporting_lags():
    rows, days = records()
    result = evaluation.evaluate(rows, days, status="completed")
    assert result["status"] == "completed" and result["verdict"] == "pass"
    assert result["base"]["primary"]["count"] == len(days)
    assert result["base"]["scenarios"]["2.0"]["lag20"]["lag"] == 20


@pytest.mark.parametrize("change,expected", [
    (lambda r: r["primary"].update(mean=0), "fail"),
    (lambda r: r["primary"].update(t_stat=3), "fail"),
    (lambda r: r["primary"].update(t_stat=3.00001), "pass"),
    (lambda r: r["random_control"]["1.0"].update(share_at_least_actual=.05), "pass"),
    (lambda r: r["random_control"]["1.0"].update(share_at_least_actual=.05001), "fail"),
    (lambda r: r["scenarios"]["1.5"]["primary"].update(mean=0), "fail"),
    (lambda r: r["ld_net_return"].update({"1.0": 0}), "fail"),
    (lambda r: r["primary"].update(t_stat=None), "insufficient"),
])
def test_judgement_exact_boundaries(change, expected):
    result = {"primary": {"mean": .01, "t_stat": 4}, "random_control": {"1.0": {"share_at_least_actual": 0}},
        "scenarios": {"1.5": {"primary": {"mean": .01}}}, "ld_net_return": {"1.0": .1}}
    change(result)
    assert evaluation.judgement(result, 150, complete=True) == expected
    assert evaluation.judgement(result, 149, complete=True) == "insufficient"


@pytest.mark.parametrize("status", ["terminated_drawdown", "model_changed", "forward_aborted", "not_started"])
def test_operating_status_has_no_verdict_even_with_passing_statistics(status):
    result = evaluation.evaluate([], [], status=status)
    assert result["status"] == status and result["verdict"] is None


def test_insufficient_mandatory_daily_file_and_no_deletion_of_invalid_days():
    rows, days = records()
    for row in rows[:120]:
        row.update(valid_decision=False, missing_reason="usage_limit")
    rows[0]["required_files"]["KOSDAQ"] = False
    result = evaluation.evaluate(rows, days, status="completed")
    assert result["verdict"] == "insufficient" and result["base"]["primary"]["count"] == len(days)
    assert result["invalid_reasons"]["usage_limit"] == 120


def test_audit_file_recovery_never_promotes_incomplete_primary_ledger():
    rows, days = records()
    rows[0]["required_files"]["KOSDAQ"] = False
    original = evaluation.evaluate(rows, days, status="completed")
    recovered = evaluation.evaluate(rows, days, status="completed", required_file_coverage={days[0]: {"KOSDAQ": True}})
    assert original["verdict"] == recovered["verdict"] == "insufficient"
    assert original["base"] == recovered["base"]
    assert not recovered["required_data_complete"]
    assert recovered["recovered_file_days"] == [days[0]]


def test_minus_100_sensitivity_only_final_residual_not_past_execution():
    rows, days = records()
    before = rows[-2]["books"][book_name("ld", 1.0)]["state"]["nav"]
    rows[-1]["books"][book_name("ld", 1.0)]["unresolved_value"] = rows[-1]["books"][book_name("ld", 1.0)]["state"]["nav"] * .99
    result = evaluation.evaluate(rows, days, status="completed")
    assert result["base_verdict"] == "pass" and result["verdict"] == "delisting_sensitive"
    assert rows[-2]["books"][book_name("ld", 1.0)]["state"]["nav"] == before
    assert result["loss_sensitivity"]["ld_net_return"]["1.5"] < 0  # same rule for every cost scenario


def test_compact_streaming_summaries_match_full_ledger_statistics():
    rows, days = records()
    full = evaluation.evaluate(rows, days, status="completed")
    reduced = evaluation.evaluate([evaluation.compact(row) for row in rows], days, status="completed")
    assert full == reduced


def test_missing_fixed_window_day_does_not_compute_nw_on_a_shortened_sample():
    rows, days = records()
    result = evaluation.evaluate(rows[1:], days, status="completed")
    assert result["verdict"] == "insufficient" and "base" not in result
    assert result["missing_days"] == [days[0]]


def test_single_registry_row_idempotent_recovery_and_state_rollback_block(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(evaluation.hc003, "registry_rows", lambda root: captured)
    monkeypatch.setattr(evaluation, "canonical_repo", lambda root: tmp_path)
    def record(experiment, trials, **kwargs):
        assert experiment == EXPERIMENT_ID and len(trials) == 1
        variant, metrics, verdict = trials[0]
        captured.append({"experiment_id": experiment, "variant": variant, "metrics_json": json.dumps(metrics), "verdict": verdict})
    monkeypatch.setattr(evaluation.experiment_registry, "record_trials", record)
    for number, trial, day, attempt_try in ((1, False, "2026-11-16", 0), (2, False, "2026-11-16", 1), (3, True, "synthetic", 0)):
        seal(tmp_path / "attempts" / f"{number:06d}.started.json",
             {"number": number, "key": "fixture", "format_trial": trial, "day": day, "try": attempt_try})
    seal(tmp_path / "attempts/000001.result.json", {"state": "invalid", "error": "usage_limit", "seconds": 1})
    seal(tmp_path / "attempts/000003.result.json", {"state": "valid", "seconds": .1})
    workflow = SimpleNamespace(root=tmp_path, repo_root=tmp_path, calendar=FakeCalendar(), termination=lambda: None, sync_model_stop=lambda: None)
    value = evaluation.publish_evaluation(workflow, clock=lambda: aware("2027-11-23T00:00:00+09:00"))
    assert value["status"] == "not_started" and len(captured) == 1
    assert value["calls"]["attempts"] == 3 and value["calls"]["interrupted_attempts"] == 1
    assert value["calls"]["usage_limit_days"] == 1 and value["calls"]["format_attempts"] == 1
    assert evaluation.publish_evaluation(workflow, clock=lambda: aware("2027-11-23T01:00:00+09:00")) == value
    assert len(captured) == 1
    (tmp_path / "evaluation.json").unlink()
    with pytest.raises(Refused, match="rollback"):
        evaluation.publish_evaluation(workflow, clock=lambda: aware("2027-11-24T00:00:00+09:00"))


def test_evaluation_cannot_move_freeze_earlier(tmp_path):
    with pytest.raises(Refused, match="2027-11-23"):
        evaluation.publish_evaluation(SimpleNamespace(root=tmp_path), clock=lambda: aware("2027-11-22T23:59:59+09:00"))


def test_a_stop_recognized_after_freeze_cannot_rewrite_the_frozen_operating_status(tmp_path, monkeypatch):
    seal(tmp_path / "start.json", {"fixture": True})
    stop = {"status": "forward_aborted", "recognized_at": "2027-11-24T12:00:00+09:00", "reason": "after freeze"}
    monkeypatch.setattr(evaluation.hc003, "registry_rows", lambda root: [])
    monkeypatch.setattr(evaluation.experiment_registry, "record_trials", lambda *a, **k: None)
    workflow = SimpleNamespace(root=tmp_path, repo_root=tmp_path, calendar=FakeCalendar(), termination=lambda: stop, start=lambda: {}, sync_model_stop=lambda: None)
    result = evaluation.publish_evaluation(workflow, clock=lambda: aware("2027-11-25T00:00:00+09:00"))
    assert result["status"] == "completed" and result["verdict"] == "insufficient"
    assert result["termination"] is None and result["late_termination"] == stop


def evidence(tmp_path):
    log = tmp_path / "lab.log"
    log.write_text("synthetic fixture for gate logic only")
    return {"experiment_id": EXPERIMENT_ID, "at": "2026-11-12T09:00:00+09:00", "synthetic": False,
        "machine": {"host": "hqa-server", "account": "fixture-research"}, "research_account": True,
        "login_verified": True, "model_identity": identity(), "implementation_hashes": {"fixture": "hash"}, "committed": True,
        "official_execution_feed_verified": True,
        "memory": {"includes_children": True, "verified": True, "peak_rss_mib": 512},
        "timing": {"input_started_at": "2026-11-12T08:45:00+09:00", "input_seconds": 100, "overhead_seconds": 60, "real_path_verified": True},
        "no_seal_delays": dict.fromkeys(("FW001", "FW002", "shadow", "BB003"), True),
        "restart": {**dict.fromkeys(("reboot", "session_exit", "automatic_resume", "same_input_hash", "attempts_preserved", "no_duplicate_finalization"), True), "service_unit": "fixture.service"},
        "disclosure_enabled": False, "format_check": {"synthetic_inputs": True, "completed": True, "real_call_path": True, "attempts": 2},
        "evidence_files": {str(log): file_hash(log)}}


def test_activation_requires_all_six_gates_and_never_accepts_fixture_evidence(tmp_path, monkeypatch):
    value = evidence(tmp_path)
    seal(tmp_path / "holdout.json", {"snapshot_path": "fixture", "sha256": "hash"})
    monkeypatch.setattr(activation, "Snapshot", lambda *a, **k: None)
    def check(v, at="2026-11-13T23:59:59+09:00"):
        return activation.check(tmp_path, v, at=at, code_hashes={"fixture": "hash"}, machine=value["machine"])
    assert check(value)["ready"]
    assert not check(value, "2026-11-14T00:00:00+09:00")["ready"]
    for key, new in (("synthetic", True), ("login_verified", False), ("committed", False),
                     ("memory", {}), ("timing", {}), ("restart", {}), ("no_seal_delays", {}),
                     ("format_check", {}), ("evidence_files", {}), ("disclosure_enabled", None)):
        changed = {**value, key: new}
        assert not check(changed)["ready"], key
    assert not check({**value, "disclosure_enabled": True})["ready"]
    assert check({**value, "disclosure_enabled": True, "disclosure_full_list_verified": True})["ready"]


@pytest.mark.parametrize("command,arguments", [("input-seal", ["--fw001-dir", "/nonexistent"]), ("decide", []),
    ("ledger-finalize", ["--status-file", "/nonexistent"]), ("evaluate", []), ("activation-check", []),
    ("seal-holdout", ["--source-dir", "/nonexistent"]), ("distribute", ["--source-dir", "/nonexistent", "--sync-manifest", "/nonexistent"])])
def test_cli_is_dry_by_default_and_does_not_read_data_or_call_models(tmp_path, capsys, command, arguments):
    target = tmp_path / "does-not-exist"
    assert main([command, "--state-dir", str(target), *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True and not target.exists()


def test_shared_calendar_unknown_2027_and_special_session_fail_closed():
    with pytest.raises(ValueError, match="calendar_unverified"):
        Calendar().check("2027-01-04")
    with pytest.raises(ValueError, match="special_session"):
        Calendar().check("2026-11-19")


def test_ordering_extension_uses_exact_lh003_release_including_av002(monkeypatch, tmp_path):
    calls = []
    sentinel = {"AV002": "verified-terminal"}
    monkeypatch.setattr(ordering, "lh003_releases", lambda rows, root: calls.append((rows, root)) or sentinel)
    assert ordering.ld002_releases([], tmp_path) is sentinel and calls == [([], tmp_path)]
    assert EXPERIMENT_ID in ordering.AV002_SUCCESSORS


@pytest.mark.parametrize("registered", [False, True])
def test_direct_ld002_holdout_claim_uses_mandatory_releases_under_lock(tmp_path, monkeypatch, registered):
    calls = []
    monkeypatch.setattr(ordering, "_registered", lambda *a: registered)
    monkeypatch.setattr(ordering, "ld002_releases", lambda rows, root: calls.append("ld002") or {})
    from backtesting.experiments import hc003
    monkeypatch.setattr(hc003, "registry_rows", lambda root: [])
    with ordering.protected_claim(EXPERIMENT_ID, tmp_path):
        assert calls == ["ld002"] and ordering._lock_target(tmp_path)[1] in ordering._LOCKS.get()


def test_finalizer_resumes_oldest_unfinished_day_after_outage(tmp_path):
    workflow = SimpleNamespace(root=tmp_path, calendar=FakeCalendar(), sync_model_stop=lambda: None)
    at = aware("2026-11-20T08:35:00+09:00")
    assert next_finalization_day(workflow, at) == "2026-11-16"
    seal(tmp_path / "ledgers/2026-11-16.json", {"fixture": True})
    assert next_finalization_day(workflow, at) == "2026-11-16"  # pending receipt is not completed
    seal(tmp_path / "ledgers/2026-11-16.publication/complete.json", {"fixture": True})
    seal(tmp_path / "ledger_finalizations/2026-11-16.json", {"fixture": True})
    assert next_finalization_day(workflow, at) == "2026-11-17"
    assert next_finalization_day(workflow, at, "2026-11-19") == "2026-11-19"
    seal(tmp_path / "ledgers/2027-11-15.json", {"fixture": True})
    seal(tmp_path / "ledgers/2027-11-15.publication/complete.json", {"fixture": True})
    seal(tmp_path / "ledger_finalizations/2027-11-15.json", {"fixture": True})
    assert next_finalization_day(workflow, at) is None


def test_price_index_reporting_has_t0_close_entry_and_excludes_dividends():
    rows, days = records()
    for i, row in enumerate(rows):
        row["indices"] = {"KOSPI": 1000 + i, "KOSDAQ": 2000 + 2 * i}
    result = evaluation.evaluate(rows, days, status="completed")
    assert result["price_indices"]["KOSPI"]["net_return"] == pytest.approx((1000 + len(days) - 1) / 1000 - 1)
    assert result["price_indices"]["KOSDAQ"]["dividends_included"] is False


def test_published_evaluation_preserves_legacy_incomplete_primary_despite_audit(tmp_path, monkeypatch):
    from backtesting.experiments.ld002.storage import publish
    from backtesting.experiments.ld002.workflow import ForwardRunner
    from ld002_fixtures import Clock, publication_clock
    clock = Clock("2026-11-18T09:00:00+09:00")
    seal(tmp_path / "start.json", {"synthetic": True})
    workflow = ForwardRunner(tmp_path, calendar=FakeCalendar(), clock=clock, synthetic=True,
        publication_clock=publication_clock, code_verifier=lambda: {})
    rows, days = records()
    rows = deepcopy(rows[:2])
    rows[0]["required_files"]["KOSDAQ"] = False
    previous = None
    for row in rows:
        row.update(input_sha256=digest(None), decision_sha256=digest(None), previous_sha256=digest(previous),
            finalized_at=clock().isoformat(), prepared_at=clock().isoformat(), termination=None, pending_drawdown=False,
            valid_decision=False, missing_reason="missing_decision")
        path = workflow.path("ledgers", row["day"])
        publish(path, row, deadline=aware("2027-11-23T00:00:00+09:00"), clock=clock, publication_clock=publication_clock)
        seal(workflow.path("ledger_finalizations", row["day"]), {"record_sha256": digest(row), "finalized_at": clock().isoformat(), "termination": None})
        previous = workflow.ledger(row["day"], allow_incomplete=True)
    assert workflow.ledger(days[0]) is None
    with pytest.raises(Refused, match="incomplete primary"):
        workflow.finalize(days[0], {}, required_files={"KOSPI": True, "KOSDAQ": True})
    seal(tmp_path / "audits" / days[0] / "correction.json", {"day": days[0],
        "original_sha256": digest(workflow.ledger(days[0], allow_incomplete=True)), "finalized_at": clock().isoformat(),
        "required_files": {"KOSPI": True, "KOSDAQ": True}, "nav_differences": {}})
    monkeypatch.setattr(workflow.calendar, "sessions", lambda a, b: days[:2])
    monkeypatch.setattr(evaluation, "canonical_repo", lambda root: tmp_path)
    monkeypatch.setattr(evaluation.hc003, "registry_rows", lambda root: [])
    monkeypatch.setattr(evaluation.experiment_registry, "record_trials", lambda *a, **k: None)
    result = evaluation.publish_evaluation(workflow, clock=lambda: aware("2027-11-23T00:05:00+09:00"))
    assert result["verdict"] == "insufficient" and not result["required_data_complete"]
    assert result["recovered_file_days"] == [days[0]]
    assert result["days"] == 2 and result["missing_days"] == []
