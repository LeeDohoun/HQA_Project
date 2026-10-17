"""Synthetic-only protected snapshots, lifecycle, calendar and environment gates."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import hc003, ordering
from backtesting.experiments.lh003 import EXPERIMENT_ID, Refused
from backtesting.experiments.lh003 import calendar, environment, inputs, workflow
from backtesting.experiments.lh003.snapshot import Snapshot, input_view, publish_snapshot
from backtesting.experiments.lh003.simulation import synthetic_archive
from backtesting.experiments.lh003.storage import aware, digest, file_hash, read, seal, verified
from src.ingestion.storage import atomic_write
from test_experiments_lh003_runner import Clock, forbid_real_calls_and_holdout
from test_experiments_ordering import row


@pytest.fixture(scope="module")
def source_fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("lh003-synthetic-source")
    return synthetic_archive(root / "source", day="2026-11-13", stocks=40)


@pytest.fixture
def source(tmp_path, source_fixture):
    return Path(shutil.copytree(source_fixture, tmp_path / "source"))


def snapshot_for(source, tmp_path):
    path = tmp_path / "synthetic_snapshot"
    manifest = publish_snapshot(source, path, at=aware("2026-11-12T10:00:00+09:00"), synthetic=True)
    return Snapshot(path, expected_hash=digest(manifest), allow_synthetic=True)


def test_fixed_52_weeks_calendar_deadline_and_twentieth_session():
    plan = calendar.plan()
    assert len(plan) == 52 and plan[0]["decision_date"] == "2026-11-13" and plan[-1]["decision_date"] == "2027-11-05"
    assert plan[-1]["exit_date"] == "2027-12-03"
    assert plan[0]["deadline"] == "2026-11-16T08:59:00+09:00"
    sessions = calendar.exchange().sessions
    for decision in plan:
        pos = sessions.get_loc(pd.Timestamp(decision["decision_date"]))
        assert sessions[pos + 1] == pd.Timestamp(decision["entry_date"])
        assert sessions[pos + 20] == pd.Timestamp(decision["exit_date"])
        week = pd.Timestamp(decision["decision_date"]).to_period("W-FRI")
        assert pd.Timestamp(decision["decision_date"]) == sessions[sessions.to_period("W-FRI") == week][-1]
    assert calendar.latest_completed_week(aware("2026-10-17T12:00:00+09:00")) == "2026-10-16"


def test_pinned_calendar_refuses_dependency_drift(monkeypatch):
    monkeypatch.setattr(calendar.calendars, "__version__", "99.0.0")
    with pytest.raises(Refused, match="4.13.2"):
        calendar.exchange()


def test_calendar_session_hash_changes_require_an_explicit_operator_note(tmp_path):
    at = aware("2026-11-12T12:00:00+09:00")
    original = calendar.check(tmp_path, at=at)
    changed = calendar.exchange().sessions.drop(pd.Timestamp("2027-03-05"))
    with pytest.raises(Refused, match="operator note"):
        calendar.check(tmp_path, at=at, sessions=changed)
    reviewed = calendar.check(tmp_path, at=at, sessions=changed, operator_note="Fixture official holiday correction")
    assert reviewed["sessions_sha256"] != original["sessions_sha256"]
    assert len(reviewed["decisions"]) == 52 and reviewed["decisions"][-1]["decision_date"] == "2027-11-05"
    notes = sorted((tmp_path / "calendar").glob("*.json"))
    assert len(notes) == 2 and verified(notes[-1])["operator_note"]


def test_snapshot_acquisition_requires_a_session_and_synthetic_stays_outside_data(tmp_path, source):
    with pytest.raises(Refused, match="single LH003 session"):
        publish_snapshot(source, tmp_path / "official", at=aware("2026-11-12T12:00:00+09:00"))
    with pytest.raises(Refused, match="never be written under data"):
        publish_snapshot(source, tmp_path / "data/synthetic", at=aware("2026-11-12T12:00:00+09:00"), synthetic=True)
    snapshot = snapshot_for(source, tmp_path)
    with pytest.raises(Refused, match="synthetic"):
        Snapshot(snapshot.root, expected_hash=digest(snapshot.manifest))


def test_weekly_inputs_never_open_original_protected_files_or_stream_prefixes(source, tmp_path, monkeypatch):
    snapshot = snapshot_for(source, tmp_path)
    forbidden = {(source / relative).resolve() for relative in snapshot.manifest["files"]}
    original = Path.open
    def guarded(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "r" in mode and path.resolve() in forbidden:
            pytest.fail("protected original/prefix reread: " + str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    result = inputs.prepare(source, snapshot, "2026-11-13")
    assert len(result["bundle"]["a_codes"]) == 40
    assert result["financial_dates"]["rule"] == "S"
    assert all(s["listing_coverage"]["complete"] for s in result["bundle"]["stocks"])
    assert result["bundle"]["stocks"][0]["return_250"] is not None


def test_protected_source_corrections_cannot_change_frozen_inputs(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    before = inputs.prepare(source, snapshot, "2026-11-13")
    for relative in snapshot.manifest["files"]:
        if relative.startswith(("market/krx_daily/", "disclosures/")):
            (source / relative).write_text("not JSON; protected original must never be read\n")
    assert inputs.prepare(source, snapshot, "2026-11-13") == before


def test_snapshot_hash_and_read_only_mode_are_verified(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    path = next(p for p in snapshot.root.rglob("*.jsonl") if p.stat().st_size)
    path.chmod(0o644)
    with pytest.raises(Refused, match="writable"):
        Snapshot(snapshot.root, expected_hash=digest(snapshot.manifest), allow_synthetic=True)
    path.write_text("changed\n")
    path.chmod(0o444)
    with pytest.raises(Refused, match="changed"):
        Snapshot(snapshot.root, expected_hash=digest(snapshot.manifest), allow_synthetic=True)


def test_streams_seek_only_to_new_tail_and_refuse_protected_tail_or_truncation(source, tmp_path, monkeypatch):
    snapshot = snapshot_for(source, tmp_path)
    relative = next(p for p in snapshot.manifest["streams"] if "fundamentals" in p)
    path = source / relative
    baseline = snapshot.path(relative).read_bytes()
    tail = json.dumps({"available_date": "2026-11-16", "new_version": True}).encode() + b"\n"
    with path.open("ab") as handle:
        handle.write(tail)
    original = Path.open
    original_stat = Path.stat
    suffix_read = []
    mutate_on_read = []
    class SuffixOnly:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def seek(self, offset): return self.handle.seek(offset)
        def read(self):
            assert self.handle.tell() >= len(baseline)
            suffix_read.append(True)
            content = self.handle.read()
            if mutate_on_read:
                with original(path, "ab") as writer:
                    writer.write(b"\n")
            return content
    def guarded(p, *args, **kwargs):
        handle = original(p, *args, **kwargs)
        return SuffixOnly(handle) if p == path and args and args[0] == "rb" else handle
    monkeypatch.setattr(Path, "open", guarded)
    def changing_atime(p, *args, **kwargs):
        stat = original_stat(p, *args, **kwargs)
        if p != path:
            return stat
        values = {key: getattr(stat, key) for key in dir(stat) if key.startswith("st_")}
        values["st_atime_ns"] += len(suffix_read)
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path, "stat", changing_atime)
    assert snapshot.stream(source, relative) == baseline + tail
    mutate_on_read.append(True)
    with pytest.raises(Refused, match="changed during suffix"):
        snapshot.stream(source, relative)
    mutate_on_read.clear()
    path.write_bytes(baseline + b'{"available_date":"2026-06-01"}\n')
    with pytest.raises(Refused, match="protected"):
        snapshot.stream(source, relative)
    path.write_bytes(b"\n")
    with pytest.raises(Refused, match="truncated"):
        snapshot.stream(source, relative)


def test_s_date_uses_later_public_date_and_unlinked_since_2016_is_unusable(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    with input_view(source, snapshot, end="20261113") as view:
        known, report = inputs.financials(view, "2026-11-13")
        assert len(known) and report["L"] == 0
        assert all(a["date_reason"] == "linked" for a in report["annotations"])
    from src.ingestion.dart_financial_date_rules import FilingDates
    dates = FilingDates([{"rcept_no": "20260101000001", "rcept_dt": "20260103", "corp_code": "00000001",
                          "stock_code": "000010", "report_nm": "정정 분기보고서 (2025.09)"}])
    assert dates.annotate({"rcept_no": "20260101000001", "corp_code": "00000001"})["actual_public_date"] == "2026-01-03"
    assert dates.annotate({"rcept_no": "20260102000001", "corp_code": "00000001"})["actual_public_date"] is None


def test_incomplete_disclosure_window_is_unavailable_not_zero(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    state_path = source / "disclosures/dart_full/_state.json"
    state = read(state_path)
    state["completed_listing_days"].remove("20261020")
    state_path.write_text(json.dumps(state))
    bundle = inputs.prepare(source, snapshot, "2026-11-13")["bundle"]
    assert all(s["disclosures_contract"] is None and not s["listing_coverage"]["complete"] for s in bundle["stocks"])


def test_LH003_requires_both_releases_and_all_passing_holdouts(tmp_path, monkeypatch):
    checked = []
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda name, root: checked.append(name))
    av, ev = ordering.RELEASE_SPECS
    rows = [row(av, v, "fail") for v in ordering.RELEASE_SPECS[av]]
    with pytest.raises(ValueError, match="EV001.*LH003"):
        ordering.lh003_releases(rows, tmp_path)
    rows += [row(ev, v, "pass" if i == 0 else "fail") for i, v in enumerate(ordering.RELEASE_SPECS[ev])]
    with pytest.raises(ValueError, match="holdout unfinished.*LH003"):
        ordering.lh003_releases(rows, tmp_path)
    rows += [row(ev, "holdout_" + ordering.RELEASE_SPECS[ev][0], "holdout_aborted", stage="holdout")]
    assert set(ordering.lh003_releases(rows, tmp_path)) == {av, ev}


@pytest.fixture
def lifecycle(tmp_path, source, monkeypatch):
    root, clock, calls = tmp_path / "state", Clock("2026-11-12T10:00:00+09:00"), []
    registration = SimpleNamespace(commit_hash="fixture-only", fields={"uses_holdout": True})
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a, **k: registration)
    monkeypatch.setattr(ordering, "lh003_releases", lambda *a: {k: {"passing_variants": []} for k in ordering.RELEASE_SPECS})
    report = tmp_path / "environment.json"
    seal(report, {"experiment_id": EXPERIMENT_ID, "passed": True, "synthetic": False,
        "machine_id": workflow.machine_id(), "at": clock().isoformat(), "cycle": {"real_data": True, "completed": True},
        "timing": {"fits": True, "call_timeout_seconds": 300}, "restart_resume": {"verified": True}, "sync": {"verified": True}})
    class FakeSession:
        def __init__(self, experiment_id, *, repo_root):
            self.experiment_id, self.repo_root = experiment_id, Path(repo_root)
            self._pid, self._open = os.getpid(), False
        def __enter__(self):
            with pytest.raises(ValueError, match="in progress"):
                with ordering.execution_lock(self.repo_root):
                    pass
            calls.append("fake claim")
            atomic_write(self.repo_root / holdout.LEDGER_PATH, json.dumps({"experiment_id": self.experiment_id}) + "\n")
            self._open = True
            self.token = holdout._SESSIONS.set((*holdout._SESSIONS.get(), self))
            return self
        def __exit__(self, *args):
            self._open = False
            holdout._SESSIONS.reset(self.token)
    return {"data_dir": source, "root": root, "repo_root": tmp_path, "environment_report": report,
            "clock": clock, "session_factory": FakeSession, "verify_cycle": lambda **k: verified(report)}, calls


def test_single_fake_claim_seals_start_and_cannot_reopen(lifecycle):
    arguments, calls = lifecycle
    start = workflow.seal_holdout(**arguments)
    assert calls == ["fake claim"] and start["snapshot_sha256"]
    Snapshot(start["snapshot_path"], expected_hash=start["snapshot_sha256"])
    with pytest.raises(Refused, match="never reopen"):
        workflow.seal_holdout(**arguments)
    assert calls == ["fake claim"]


def test_preclaim_failure_allows_retry_without_not_started(lifecycle):
    arguments, calls = lifecycle
    good = arguments["environment_report"]
    bad = arguments["repo_root"] / "bad_environment.json"
    seal(bad, {**verified(good), "passed": False, "timing": {"fits": False}})
    arguments["environment_report"] = bad
    with pytest.raises(Refused, match="environment"):
        workflow.seal_holdout(**arguments)
    assert not calls and not (arguments["root"] / "not_started.json").exists()
    arguments["environment_report"] = good
    assert workflow.seal_holdout(**arguments)["snapshot_sha256"]


def test_postclaim_failure_records_not_started_and_never_retries(lifecycle):
    arguments, calls = lifecycle
    def broken(*a, **k): raise OSError("fake seal disk failure")
    with pytest.raises(OSError, match="disk failure"):
        workflow.seal_holdout(**arguments, acquire=broken)
    state = verified(arguments["root"] / "not_started.json")
    assert state["holdout_claimed"] and state["verdict"] == "not_started"
    rows = hc003.registry_rows(arguments["repo_root"])
    assert len(rows) == 1 and rows[0]["verdict"] == "not_started"
    with pytest.raises(Refused, match="cannot be restarted"):
        workflow.seal_holdout(**arguments)
    assert calls == ["fake claim"]


def test_postseal_real_cycle_failure_consumes_the_same_fake_claim(lifecycle):
    arguments, calls = lifecycle
    report = verified(arguments["environment_report"])
    arguments["verify_cycle"] = lambda **k: {**report, "passed": False}
    with pytest.raises(Refused, match="environment"):
        workflow.seal_holdout(**arguments)
    assert calls == ["fake claim"]
    assert verified(arguments["root"] / "not_started.json")["holdout_claimed"]


def test_missing_calendar_journal_cannot_be_recreated_after_start(tmp_path):
    seal(tmp_path / "start.json", {"synthetic": False})
    with pytest.raises(Refused, match="journal missing"):
        calendar.check(tmp_path, at=aware("2026-11-14T12:00:00+09:00"))


def test_machine_replacement_requires_and_records_reason_and_remaining_count(tmp_path, monkeypatch):
    start = {"machine_id": "original"}
    monkeypatch.setattr(workflow, "machine_id", lambda: "replacement")
    at = aware("2026-11-14T12:00:00+09:00")
    with pytest.raises(Refused, match="machine changed"):
        workflow.check_machine(tmp_path, start, at=at, operator_note=None)
    workflow.check_machine(tmp_path, start, at=at, operator_note="fixture hardware failure")
    change = verified(tmp_path / "machine_changes/000.json")
    assert change["reason"] == "fixture hardware failure" and change["subsequent_decisions"] == 52


def test_missing_machine_identity_fails_without_hostname_fallback(monkeypatch):
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: False if p == Path("/etc/machine-id") else original(p))
    with pytest.raises(Refused, match="requires /etc/machine-id"):
        workflow.machine_id()


def test_CLI_dry_run_default_and_synthetic_execution_rejection(tmp_path, monkeypatch, capsys):
    from backtesting.experiments.lh003 import __main__ as cli
    calls = []
    monkeypatch.setattr(cli, "verify_environment", lambda **k: calls.append(k) or {"fake_report": True})
    assert cli.main(["--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["fake_report"]
    assert not calls[0]["synthetic"] and calls[0]["report_path"] is None
    for mode in ("--run-week", "--seal-holdout", "--evaluate", "--record-executions"):
        with pytest.raises(SystemExit):
            cli.main([mode, "--synthetic"])
    assert "lh003" in (Path(__file__).resolve().parents[1] / "scripts/ops/research_run.sh").read_text()


def test_missed_start_is_not_started_without_a_claim(lifecycle):
    arguments, calls = lifecycle
    arguments["clock"].at = aware("2026-11-13T16:00:00+09:00")
    with pytest.raises(Refused, match="fixed start"):
        workflow.seal_holdout(**arguments)
    assert not calls and verified(arguments["root"] / "not_started.json")["holdout_claimed"] is False


def test_missing_week_dry_report_has_no_real_cycle_or_holdout_and_a_restart_plan(tmp_path):
    path = inputs.price_path(tmp_path / "source", "2026-10-08")
    path.parent.mkdir(parents=True)
    path.write_text("metadata-only inventory, must not be parsed\n")
    report = environment.verify_environment(data_dir=tmp_path / "source", report_path=tmp_path / "report.json",
        clock=Clock("2026-10-17T12:00:00+09:00"))
    assert report["archive_latest_day"] == "2026-10-08" and report["latest_completed_week"] == "2026-10-16"
    assert "missing" in report["cycle"]["reason"] and not report["cycle"]["completed"]
    assert not report["passed"] and not report["holdout_opened"] and report["synthetic_format_budget_spent"] == 0
    assert report["restart_resume"]["plan"] and report["timing"]["attempts"] == 6
    assert verified(tmp_path / "report.json") == report


def test_sync_hash_and_2026_future_data_refuse_invalid_trigger(tmp_path):
    path = inputs.price_path(tmp_path, "2026-11-13")
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps({"trade_date": "2026-11-13", "calendar_status": "verified", "market": m})
                              for m in ("KOSPI", "KOSDAQ")) + "\n")
    manifest = tmp_path / "sync.json"
    value = {"verified_at": "2026-11-13T18:00:00+09:00", "files": {path.relative_to(tmp_path).as_posix(): file_hash(path)}}
    manifest.write_text(json.dumps(value))
    assert inputs.verify_day(tmp_path, "2026-11-13", at=aware(value["verified_at"]), sync_manifest=manifest)["rows"] == 2
    path.write_text(path.read_text() + "\n")
    with pytest.raises(Refused, match="hash mismatch"):
        inputs.verify_day(tmp_path, "2026-11-13", at=aware(value["verified_at"]), sync_manifest=manifest)
    with pytest.raises(Refused, match="has not closed"):
        inputs.verify_day(tmp_path, "2026-11-13", at=aware("2026-11-13T10:00:00+09:00"), sync_manifest=manifest)
