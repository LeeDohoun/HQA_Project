"""Sealed no-return precheck, execution ordering and durable save-only recovery."""
import copy
from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import signal
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.experiments.qp003 import EXPERIMENT_ID, M, runner
from backtesting.experiments.qp003.selection import apply_permission
from test_experiments_qp001_runner import monthly_inputs
from test_experiments_qp003_portfolio import fixture_book


def precheck_payload(specification):
    return {"experiment_id": EXPERIMENT_ID, "mode": "precheck", "status": "ok", "M": M,
        "returns_computed": False, "forward_prices_used": False, "source_snapshot_unchanged": True,
        "qp001_precheck": {"file": runner.QP001_PRECHECK, "sha256": runner.QP001_PRECHECK_SHA256},
        "input_spec": specification, "input_spec_sha256": runner.qp001.content_hash(specification)}


def test_precheck_has_no_forward_valuation_trading_or_performance_path(tmp_path, monkeypatch):
    sessions, history, versions, row, events, _ = monthly_inputs(monkeypatch, computable=8)
    # This fixture patches the imported, unchanged QP001 selection inputs only.
    specification = {"data": {}, "code": {}, "lineage": {}}
    monkeypatch.setattr(runner, "input_spec", lambda *a: specification)
    monkeypatch.setattr(runner, "seal_registry", lambda *a: {"M": M, "sha256": "registry"})
    monkeypatch.setattr(runner.qp001, "AssetArchive", lambda *a: SimpleNamespace(pending=0))
    monkeypatch.setattr(runner.qp001, "link_coverage", lambda *a: {"op_conflicts": 0})
    monkeypatch.setattr(runner.qp001, "calendar", lambda *a: (sessions, [row]))
    def denied(*a, **k):
        pytest.fail("precheck crossed the no-returns boundary")
    for module, names in ((runner, ("execute", "compute", "metrics", "reproduce_av001")),
        (runner.qp001, ("price_book", "compute")), (runner.portfolio, ("simulate", "controls", "rebalance", "summary")),
        (runner.hc003, ("load_prices", "PriceBook", "portfolio_value", "benchmark_return"))):
        for name in names:
            monkeypatch.setattr(module, name, denied)
    monkeypatch.setattr(runner.qp001.hc002, "forward_observations", denied)
    def prepare(*a, precheck_only, source_hashes, **k):
        assert precheck_only and source_hashes == specification
        selected = runner.qp001.select_month(history, row, versions, events, [], {"covered": True}, sessions,
                                            data_dir=tmp_path, repo_root=tmp_path)
        selected["precheck"].update(stage="design", rule="S")
        selected = apply_permission(selected)
        return {"selections": {r: [selected] for r in ("S", "O")}, "filing_rules": {}, "L": 17,
                "sensitivity_available": True, "sensitivity_by_stage": {"design": True}}
    monkeypatch.setattr(runner, "prepare_selections", prepare)
    result = runner.precheck(data_dir=tmp_path, repo_root=tmp_path)
    assert result["status"] == "ok" and result["returns_computed"] is False and result["forward_prices_used"] is False
    assert result["monthly_summary"]["S"]["hold_months"] == ["2023-12"]
    assert result["monthly_summary"]["S"]["permitted_months"] == []
    assert result["input_spec"] == specification and result["registry_seal"]["M"] == 168


def test_precheck_source_change_is_incomplete_not_a_hold(tmp_path, monkeypatch):
    book, rows = fixture_book(tmp_path)
    calls = []
    def specification(*a):
        calls.append(True)
        return {"data": {"file": str(len(calls))}, "code": {}}
    monkeypatch.setattr(runner, "input_spec", specification)
    monkeypatch.setattr(runner, "seal_registry", lambda *a: {"M": M})
    monkeypatch.setattr(runner.qp001, "AssetArchive", lambda *a: SimpleNamespace(pending=0))
    monkeypatch.setattr(runner.qp001, "link_coverage", lambda *a: {})
    monkeypatch.setattr(runner.qp001, "calendar", lambda *a: ([], rows))
    monkeypatch.setattr(runner, "prepare_selections", lambda *a, **k: {"selections": {r: rows for r in ("S", "O")},
        "filing_rules": {}, "L": 17, "sensitivity_available": True, "sensitivity_by_stage": {}})
    result = runner.precheck(data_dir=tmp_path, repo_root=tmp_path)
    assert result["status"] == "incomplete" and not result["source_snapshot_unchanged"]


def test_input_spec_covers_qp003_and_lineage_and_missing_sources_abort(tmp_path, monkeypatch):
    original = tmp_path / runner.QP001_PRECHECK
    original.parent.mkdir(parents=True)
    original.write_text(json.dumps({"source_hashes": {"data": {"source.jsonl": "old"}}}))
    monkeypatch.setattr(runner, "QP001_PRECHECK_SHA256", runner.digest(original))
    (tmp_path / "source.jsonl").write_text("source\n")
    code = tmp_path / "backtesting/experiments/qp003/runner.py"
    code.parent.mkdir(parents=True)
    code.write_text("source\n")
    prereg = tmp_path / f"research/experiments/{EXPERIMENT_ID}/preregistration.md"
    prereg.parent.mkdir(parents=True)
    prereg.write_text("registered\n")
    monkeypatch.setattr(runner.qp001, "input_hashes", lambda *a: {"data": {"source.jsonl": "new"}, "code": {}})
    spec = runner.input_spec(tmp_path, tmp_path)
    assert spec["lineage"] == {runner.QP001_PRECHECK: runner.digest(original)}
    assert spec["code"][code.relative_to(tmp_path).as_posix()] == runner.digest(code)
    assert spec["code"][prereg.relative_to(tmp_path).as_posix()] == runner.digest(prereg)
    saved = tmp_path / "precheck.json"
    saved.write_text(json.dumps(precheck_payload(spec)))
    assert runner.verify_precheck(saved, spec)["sha256"] == runner.digest(saved)
    code.write_text("changed\n")
    with pytest.raises(ValueError, match="input spec differs"):
        runner.verify_precheck(saved, runner.input_spec(tmp_path, tmp_path))
    (tmp_path / "source.jsonl").unlink()
    with pytest.raises(ValueError, match="missing QP001 source"):
        runner.input_spec(tmp_path, tmp_path)
    original.write_text("changed lineage")
    with pytest.raises(ValueError, match="QP001 precheck SHA-256"):
        runner.input_spec(tmp_path, tmp_path)


@pytest.fixture
def synthetic_execution(tmp_path, monkeypatch):
    book, rows = fixture_book(tmp_path)
    registration = runner.experiment_registry.Preregistration(commit_hash="synthetic", commit_time=datetime.now(timezone.utc),
        fields={"experiment_id": EXPERIMENT_ID, "variants_planned": 1, "uses_holdout": False, "uses_llm": False},
        blob_hash="synthetic", path=f"research/experiments/{EXPERIMENT_ID}/preregistration.md")
    monkeypatch.setattr(runner.experiment_registry, "verify_preregistration", lambda *a: registration)
    monkeypatch.setattr(runner.experiment_registry, "verify_captured_preregistration", lambda *a: None)
    monkeypatch.setattr(runner.experiment_registry, "_git", lambda *a: "synthetic")
    monkeypatch.setattr(runner, "seal_registry", lambda *a: {"M": M, "sha256": "registry"})
    specification = {"data": {"source": "data-hash"}, "code": {"qp003/runner.py": "code-hash"},
                     "lineage": {runner.QP001_PRECHECK: runner.QP001_PRECHECK_SHA256}}
    monkeypatch.setattr(runner, "input_spec", lambda *a: copy.deepcopy(specification))
    monkeypatch.setattr(runner.qp001, "calendar", lambda: ([], rows))
    monkeypatch.setattr(runner.qp001, "AssetArchive", lambda *a: SimpleNamespace(pending=0))
    monkeypatch.setattr(runner.qp001, "link_coverage", lambda *a: {"op_conflicts": 0})
    monkeypatch.setattr(runner, "prepare_selections", lambda *a, **k: {"selections": {r: rows for r in ("S", "O")},
        "filing_rules": {}, "L": 17, "sensitivity_available": True, "sensitivity_by_stage": {"design": True, "validation": True}})
    monkeypatch.setattr(runner.qp001, "price_book", lambda *a, **k: book)
    original_controls = runner.portfolio.controls
    calls = []
    def controls(*a, **k):
        calls.append((k["loss"], k.get("independent", False)))
        return original_controls(*a, **k, paths=3)
    monkeypatch.setattr(runner.portfolio, "controls", controls)
    precheck = tmp_path / "precheck.json"
    precheck.write_text(json.dumps(precheck_payload(specification)))
    return precheck, calls


def test_synthetic_run_all_rules_costs_loss_seals_and_months(tmp_path, synthetic_execution):
    precheck, calls = synthetic_execution
    output = tmp_path / "result.json"
    payload = runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    assert payload["judgement"]["verdict"] == "insufficient"  # Four synthetic months only.
    assert len(calls) == 8
    assert set(payload["rules"]) == set(payload["vanished"]) == {"S", "O"}
    assert payload["M"] == payload["registry_seal"]["M"] == 168
    assert payload["input_spec_sha256"] == runner.qp001.content_hash(payload["input_spec"])
    assert payload["precheck"]["sha256"] == runner.digest(precheck)
    assert payload["qp001_precheck"]["sha256"] == runner.QP001_PRECHECK_SHA256
    for section in ("rules", "vanished"):
        for rule in ("S", "O"):
            metrics = payload[section][rule]["metrics"]
            assert metrics["permitted_design_decisions"] == 1
            assert metrics["design"]["primary"]["count"] == 3
            assert metrics["evaluation_complete"]
    assert json.loads(output.read_text()) == payload
    assert len(list(output.with_suffix(".controls").glob("*.jsonl"))) == 8
    assert [r["verdict"] for r in runner.hc003.registry_rows(tmp_path)] == ["insufficient"]
    start = runner.attempts.load(tmp_path, payload["run_id"])
    assert start["input_spec_sha256"] == payload["input_spec_sha256"]
    assert start["code_hashes"] == payload["input_spec"]["code"]
    assert start["prereg_commit"] == payload["prereg_commit"]
    assert start["registry_seal"] == payload["registry_seal"]
    assert payload["start_record"] == runner.attempts.reference(tmp_path, start)


@pytest.mark.parametrize("change", ["hash", "added_file", "removed_file", "precheck_digest"])
def test_mismatched_formal_spec_stops_before_any_performance(tmp_path, monkeypatch, synthetic_execution, change):
    precheck, _ = synthetic_execution
    saved = json.loads(precheck.read_text())
    current = copy.deepcopy(saved["input_spec"])
    if change in ("hash", "added_file"):
        current["data"]["file"] = "changed"
    elif change == "removed_file":
        saved["input_spec"]["data"]["file"] = "removed"
    else:
        saved["input_spec_sha256"] = "tampered"
    precheck.write_text(json.dumps(saved))
    monkeypatch.setattr(runner, "input_spec", lambda *a: current)
    def denied(*a, **k):
        pytest.fail("mismatched inputs reached a performance computation")
    monkeypatch.setattr(runner.qp001, "price_book", denied)
    monkeypatch.setattr(runner, "prepare_selections", denied)
    with pytest.raises(ValueError, match="input spec differs"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=tmp_path / "result.json", precheck_path=precheck)
    assert runner.hc003.registry_rows(tmp_path) == []


@pytest.mark.parametrize("failure", ["hash", "memory", "serialization"])
def test_post_performance_failures_record_design_aborted(tmp_path, monkeypatch, synthetic_execution, failure):
    precheck, _ = synthetic_execution
    if failure == "hash":
        spec = runner.input_spec(tmp_path, tmp_path)
        calls = []
        def changed(*a):
            calls.append(True)
            return spec if len(calls) <= 2 else {**spec, "changed": {"file": "hash"}}
        monkeypatch.setattr(runner, "input_spec", changed)
    elif failure == "memory":
        def failed(*a, **k):
            raise MemoryError("synthetic allocation failure")
        monkeypatch.setattr(runner.qp001, "price_book", failed)
    else:
        compute = runner.compute
        def bad(**kwargs):
            return {**compute(**kwargs), "invalid": object()}
        monkeypatch.setattr(runner, "compute", bad)
    output = tmp_path / "result.json"
    with pytest.raises((ValueError, MemoryError, TypeError)):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    assert not output.exists() and not list(tmp_path.glob(".*.tmp"))
    assert [r["verdict"] for r in runner.hc003.registry_rows(tmp_path)] == ["design_aborted"]
    row = runner.hc003.registry_rows(tmp_path)[0]
    run_id = json.loads(row["metrics_json"])["run_id"]
    assert runner.attempts.load(tmp_path, run_id)["run_id"] == run_id


def test_registry_append_failure_recovers_saved_result_without_reads_or_recomputation(tmp_path, monkeypatch, synthetic_execution):
    precheck, _ = synthetic_execution
    record = runner.experiment_registry.record_trial
    def failed(*a, **k):
        raise OSError("synthetic registry append failure")
    monkeypatch.setattr(runner.experiment_registry, "record_trial", failed)
    output = tmp_path / "result.json"
    with pytest.raises(OSError, match="registry append"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    before = output.read_bytes()
    assert runner.hc003.registry_rows(tmp_path) == []
    with pytest.raises(ValueError, match="unfinished QP003 start"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=tmp_path / "retry.json", precheck_path=precheck)
    def denied(*a, **k):
        pytest.fail("save-only recovery recomputed or read input data")
    for module, names in ((runner, ("compute", "execute", "input_spec", "prepare_selections", "verify_precheck")),
                          (runner.qp001, ("price_book", "load_history", "AssetArchive"))):
        for name in names:
            monkeypatch.setattr(module, name, denied)
    monkeypatch.setattr(runner.experiment_registry, "record_trial", record)
    load = json.load
    def bounded_load(handle, *a, **k):
        assert handle.name != str(output), "recovery expanded the full result"
        return load(handle, *a, **k)
    monkeypatch.setattr(json, "load", bounded_load)
    assert runner.recover_registry(output, repo_root=tmp_path)["registry_appended"]
    assert not runner.recover_registry(output, repo_root=tmp_path)["registry_appended"]
    assert output.read_bytes() == before
    assert [r["verdict"] for r in runner.hc003.registry_rows(tmp_path)] == ["insufficient"]
    payload = json.loads(before)
    detail = tmp_path / payload["rules"]["S"]["random_control"]["details"]["file"]
    detail.write_text("tampered\n")
    with pytest.raises(ValueError, match="detail hash/path"):
        runner.recover_registry(output, repo_root=tmp_path)


def test_atomic_file_and_directory_fsync_happen_before_registry(tmp_path, monkeypatch, synthetic_execution):
    import stat
    precheck, _ = synthetic_execution
    events = []
    sync, link = runner.artifacts.os.fsync, runner.artifacts.os.link
    def fsync(fd):
        events.append("dir_fsync" if stat.S_ISDIR(runner.artifacts.os.fstat(fd).st_mode) else "file_fsync")
        sync(fd)
    def publish(src, dst):
        events.append("publish:" + dst.name)
        link(src, dst)
    monkeypatch.setattr(runner.artifacts.os, "fsync", fsync)
    monkeypatch.setattr(runner.artifacts.os, "link", publish)
    price_book = runner.qp001.price_book
    def prices(*a, **k):
        starts = list((tmp_path / runner.attempts.DIRECTORY).glob("*.start.json"))
        assert len(starts) == 1
        assert events[:4] == ["dir_fsync", "file_fsync", "publish:" + starts[0].name, "dir_fsync"]
        return price_book(*a, **k)
    monkeypatch.setattr(runner.qp001, "price_book", prices)
    record = runner.experiment_registry.record_trial
    output = tmp_path / "result.json"
    def append(*a, **k):
        assert events[-3:] == ["file_fsync", "publish:result.recovery.json", "dir_fsync"]
        result_publication = events.index("publish:result.json")
        assert events[result_publication - 1:result_publication + 2] == ["file_fsync", "publish:result.json", "dir_fsync"]
        json.loads(output.read_text())
        return record(*a, **k)
    monkeypatch.setattr(runner.experiment_registry, "record_trial", append)
    runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)


@pytest.mark.parametrize("kill_signal", [signal.SIGTERM, signal.SIGKILL], ids=["SIGTERM", "OOM-SIGKILL"])
@pytest.mark.parametrize("saved", [False, True], ids=["mid-performance", "after-save"])
def test_killed_subprocess_keeps_start_and_only_recovers_same_run_id(
        tmp_path, monkeypatch, synthetic_execution, kill_signal, saved):
    precheck, _ = synthetic_execution
    output = tmp_path / "result.json"
    ready, notify = multiprocessing.get_context("fork").Pipe(duplex=False)
    def child():
        def paused(*a, **k):
            notify.send("mid-performance-controls" if not saved else "result-saved")
            signal.pause()
            raise AssertionError("killed subprocess resumed")
        if saved:
            runner.experiment_registry.record_trial = paused
        else:
            runner.portfolio.controls = paused
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    try:
        assert ready.poll(20), "subprocess did not reach the kill point"
        ready.recv()
        os.kill(process.pid, kill_signal)
        process.join(10)
        assert process.exitcode == -kill_signal
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        ready.close()
        notify.close()
    starts = list((tmp_path / runner.attempts.DIRECTORY).glob("*.start.json"))
    assert len(starts) == 1
    start = json.loads(starts[0].read_text())
    before = output.read_bytes() if saved else None
    assert runner.hc003.registry_rows(tmp_path) == []
    def denied(*a, **k):
        pytest.fail("interrupted attempt read inputs or recomputed")
    for module, names in ((runner, ("compute", "input_spec", "prepare_selections", "verify_precheck")),
                          (runner.qp001, ("price_book", "load_history", "AssetArchive"))):
        for name in names:
            monkeypatch.setattr(module, name, denied)
    with pytest.raises(ValueError, match="unfinished QP003 start"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=tmp_path / "retry.json", precheck_path=precheck)
    assert len(list((tmp_path / runner.attempts.DIRECTORY).glob("*.start.json"))) == 1
    recovered = runner.recover_attempt(start["run_id"], repo_root=tmp_path)
    assert recovered["run_id"] == start["run_id"] and recovered["recomputed"] is False
    assert recovered["registry_appended"]
    assert not runner.recover_attempt(start["run_id"], repo_root=tmp_path)["registry_appended"]
    row, = runner.hc003.registry_rows(tmp_path)
    assert json.loads(row["metrics_json"])["run_id"] == start["run_id"]
    assert row["verdict"] == ("insufficient" if saved else "design_aborted")
    if saved:
        assert output.read_bytes() == before


def test_explicit_abort_of_saved_attempt_forbids_later_result_registration(tmp_path, monkeypatch, synthetic_execution):
    precheck, _ = synthetic_execution
    def failed(*a, **k):
        raise OSError("registry unavailable")
    monkeypatch.setattr(runner.experiment_registry, "record_trial", failed)
    output = tmp_path / "result.json"
    with pytest.raises(OSError):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    run_id = json.loads(output.read_text())["run_id"]
    assert runner.recover_attempt(run_id, repo_root=tmp_path, abort=True)["verdict"] == "design_aborted"
    with pytest.raises(ValueError, match="already aborted"):
        runner.recover_registry(output, repo_root=tmp_path)


def test_start_record_creation_is_exclusive_and_never_replaces_a_previous_start(tmp_path, monkeypatch, synthetic_execution):
    precheck, _ = synthetic_execution
    payload = runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=tmp_path / "result.json", precheck_path=precheck)
    start_path = tmp_path / payload["start_record"]["file"]
    before = start_path.read_bytes()
    monkeypatch.setattr(runner.attempts.uuid, "uuid4", lambda: SimpleNamespace(hex=payload["run_id"]))
    monkeypatch.setattr(runner, "prepare_selections", lambda *a, **k: pytest.fail("nonexclusive start reached selection"))
    with pytest.raises(FileExistsError):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=tmp_path / "retry.json", precheck_path=precheck)
    assert start_path.read_bytes() == before
    assert len(runner.hc003.registry_rows(tmp_path)) == 1


def test_attempt_recovery_rejects_a_result_with_another_run_id(tmp_path, monkeypatch, synthetic_execution):
    precheck, _ = synthetic_execution
    def failed(*a, **k):
        raise OSError("registry unavailable")
    monkeypatch.setattr(runner.experiment_registry, "record_trial", failed)
    output = tmp_path / "result.json"
    with pytest.raises(OSError):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    receipt = runner.saved.receipt_path(output)
    payload = json.loads(receipt.read_text())
    run_id = payload["payload"]["run_id"]
    payload["payload"]["run_id"] = "0" * 32
    receipt.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="run_id differs"):
        runner.recover_attempt(run_id, repo_root=tmp_path)
    assert runner.hc003.registry_rows(tmp_path) == []


def test_kill_gap_between_result_and_receipt_can_only_abort(tmp_path, monkeypatch, synthetic_execution):
    precheck, _ = synthetic_execution
    output = tmp_path / "result.json"
    write_json = runner.artifacts.write_json
    def interrupted(path, payload):
        if Path(path) == runner.saved.receipt_path(output):
            raise OSError("interrupted before recovery receipt")
        return write_json(path, payload)
    monkeypatch.setattr(runner.artifacts, "write_json", interrupted)
    with pytest.raises(OSError, match="before recovery receipt"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    run_id = json.loads(output.read_text())["run_id"]
    with pytest.raises(ValueError, match="unfinished QP003 start"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=tmp_path / "retry.json", precheck_path=precheck)
    with pytest.raises(ValueError, match="recovery receipt missing"):
        runner.recover_attempt(run_id, repo_root=tmp_path)
    assert runner.recover_attempt(run_id, repo_root=tmp_path, abort=True)["verdict"] == "design_aborted"


def test_result_hash_is_checked_without_expanding_monthly_json(tmp_path, synthetic_execution):
    precheck, _ = synthetic_execution
    output = tmp_path / "result.json"
    runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output, precheck_path=precheck)
    with output.open("a") as handle:
        handle.write("tampered\n")
    with pytest.raises(ValueError, match="result hash differs"):
        runner.recover_registry(output, repo_root=tmp_path)


def test_cli_modes_are_explicit_and_protect_worktree_and_data(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "execute", lambda **k: pytest.fail("unrequested execution"))
    for args in ([], ["--execute"], ["--execute", "--output", str(tmp_path / "out.json")],
                 ["--reproduce-av001", "--output", str(tmp_path / "out.json")]):
        with pytest.raises(SystemExit):
            runner.main(args)
    (tmp_path / "data").mkdir()
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    for path in (tmp_path / "data/out.json", tmp_path / "escape/out.json", tmp_path / ".env.out"):
        with pytest.raises(SystemExit):
            runner.main(["--precheck", "--output", str(path)])
    monkeypatch.setattr(runner.resource, "setrlimit", lambda *a: None)
    monkeypatch.setattr(runner, "precheck", lambda **k: {"status": "incomplete"})
    assert runner.main(["--precheck"]) == 2
