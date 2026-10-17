"""Every AV001/EV001 release branch, plus shared lock and legacy HC003."""
import json

import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import av001, ev001, hc003, ordering
from backtesting.experiments.lh001 import __main__ as lh_cli
from backtesting.experiments.lh001.config import LH001, LH002
from backtesting.experiments.lh001.runner import StageStopped
from preregistration_fixtures import committed_preregistrations


def row(experiment_id, variant, verdict, file="design.json", stage="design_validation"):
    return {"experiment_id": experiment_id, "variant": variant, "verdict": verdict, "prereg_commit": "same",
            "metrics_json": json.dumps({"stage": stage, "result_file": file,
                                       "design_result_file": file if stage == "holdout" else None})}


@pytest.mark.parametrize("experiment,variants", list(ordering.RELEASE_SPECS.items()))
def test_missing_partial_mixed_duplicate_and_newer_partial_design_runs_block(experiment, variants):
    with pytest.raises(ValueError, match="no complete"):
        ordering.release_state([], experiment, variants)
    complete = [row(experiment, v, "fail") for v in variants]
    assert ordering.release_state(complete, experiment, variants)["passing_variants"] == []
    for broken in (complete[:2], [*complete[:2], row(experiment, variants[2], "fail", "different.json")],
                   [*complete, complete[0]], [*complete, row(experiment, variants[0], "fail", "new.json")]):
        with pytest.raises(ValueError, match="all three"):
            ordering.release_state(broken, experiment, variants)
    bad = [*complete[:2], row(experiment, variants[2], "diagnostic")]
    with pytest.raises(ValueError, match="all three"):
        ordering.release_state(bad, experiment, variants)


@pytest.mark.parametrize("terminal", ["holdout_aborted", "holdout_contaminated", "holdout_insufficient"])
def test_holdout_status_cannot_substitute_for_final_design_rows(terminal):
    experiment, variants = next(iter(ordering.RELEASE_SPECS.items()))
    designs = [row(experiment, v, terminal) for v in variants]
    with pytest.raises(ValueError, match="all three"):
        ordering.release_state(designs, experiment, variants)


@pytest.mark.parametrize("experiment,variants", list(ordering.RELEASE_SPECS.items()))
def test_aborted_design_never_releases_or_inherits_older_complete_results(experiment, variants):
    complete = [row(experiment, v, "fail") for v in variants]
    for verdict in ("design_aborted", "fail"):
        aborted = [row(experiment, v, verdict, "abort-run.json") for v in variants]
        for item in aborted:
            metrics = json.loads(item["metrics_json"])
            metrics.update(status="aborted", run_id="attempt-id")
            item["metrics_json"] = json.dumps(metrics)
        for history in (aborted, [*complete, *aborted[:1]], [*complete, *aborted]):
            with pytest.raises(ValueError, match="all three"):
                ordering.release_state(history, experiment, variants)
    assert ordering.release_state([*aborted, *complete], experiment, variants)["passing_variants"] == []


@pytest.mark.parametrize("runner", [av001, ev001])
def test_publication_abort_after_evaluation_records_compact_validated_trials(tmp_path, monkeypatch, runner):
    committed_preregistrations(tmp_path, [runner.EXPERIMENT_ID])
    def compute(**kwargs):
        kwargs["_attempt"]["source_hashes"] = {"prices": {"file": "hash"}}
        return {}
    def publish(*a, **k):
        raise OSError("fixture publication failed")
    monkeypatch.setattr(runner, "compute_design", compute)
    monkeypatch.setattr(runner, "publish", publish)
    original, validated = experiment_registry.encode_metrics, []
    def encode(metrics):
        validated.append(metrics)
        return original(metrics)
    monkeypatch.setattr(experiment_registry, "encode_metrics", encode)
    with pytest.raises(OSError, match="publication failed"):
        runner.run_experiment(repo_root=tmp_path, data_dir=tmp_path)
    rows = hc003.registry_rows(tmp_path)
    assert len(rows) == 3 and len(validated) == 4
    assert all(r["verdict"] == "design_aborted" for r in rows)
    assert json.loads(rows[0]["metrics_json"])["failure_location"]["function"] == "publish"
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("runner", [av001, ev001])
def test_runner_design_abort_records_changed_preregistration_in_real_git(tmp_path, monkeypatch, runner):
    registration = committed_preregistrations(tmp_path, [runner.EXPERIMENT_ID])[runner.EXPERIMENT_ID]
    prereg = tmp_path / registration.path
    original = prereg.read_bytes()

    def compute(**kwargs):
        kwargs["_attempt"]["source_hashes"] = {"preregistration": registration.blob_hash}
        prereg.write_bytes(original + b"changed mid-run\n")
        raise ValueError("inputs changed during computation")

    monkeypatch.setattr(runner, "compute_design", compute)
    for attempt in range(2):
        with pytest.raises(ValueError, match="inputs changed during computation"):
            runner.run_experiment(repo_root=tmp_path, data_dir=tmp_path)
        rows = hc003.registry_rows(tmp_path)
        assert len(rows) == 3 * (attempt + 1)
        assert all(row["prereg_commit"] == registration.commit_hash and row["verdict"] == "design_aborted"
                   for row in rows)
        prereg.write_bytes(original)
        assert experiment_registry.trial_count(runner.EXPERIMENT_ID, repo_root=tmp_path) == len(rows)
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("config", [LH001, LH002])
def test_cli_interruption_records_captured_plan_in_real_git(tmp_path, monkeypatch, capsys, config):
    registration = committed_preregistrations(tmp_path, [config.experiment_id])[config.experiment_id]
    prereg = tmp_path / registration.path
    original = prereg.read_bytes()
    monkeypatch.setattr(lh_cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(lh_cli, "check_committed", lambda *a: None)

    def interrupt(**kwargs):
        prereg.write_bytes(original + b"changed mid-run\n")
        raise StageStopped("inputs changed during computation")

    monkeypatch.setattr(lh_cli, "execute_probe", interrupt)
    assert lh_cli.main(["--experiment", config.name, "probe", "--execute", "--data-dir", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "inputs changed during computation"
    rows = hc003.registry_rows(tmp_path)
    assert len(rows) == 5
    assert all(row["experiment_id"] == config.experiment_id and row["prereg_commit"] == registration.commit_hash
               and row["verdict"] == "insufficient" for row in rows)
    prereg.write_bytes(original)
    assert experiment_registry.verify_preregistration(config.experiment_id, tmp_path) == registration
    assert experiment_registry.trial_count(config.experiment_id, repo_root=tmp_path) == 5


def test_deleted_committed_preregistration_cannot_bypass_release(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(experiment_registry, "_git", lambda *a: "tracked-preregistration")
    def verify(*a):
        raise ValueError("preregistration must be a regular file")
    monkeypatch.setattr(experiment_registry, "verify_preregistration", verify)
    with pytest.raises(ValueError, match="regular file"):
        ordering.registered_releases([], tmp_path)


@pytest.mark.parametrize("experiment,variants", list(ordering.RELEASE_SPECS.items()))
@pytest.mark.parametrize("terminal", sorted(ordering.TERMINAL))
def test_every_passing_variant_must_have_matching_terminal_holdout_row(experiment, variants, terminal):
    designs = [row(experiment, v, "pass" if i < 2 else "fail") for i, v in enumerate(variants)]
    one = row(experiment, f"holdout_{variants[0]}", terminal, stage="holdout")
    other = row(experiment, f"holdout_{variants[1]}", terminal, stage="holdout")
    for pending in (designs, [*designs, one], [*designs, one, row(experiment, f"holdout_{variants[1]}", terminal, "stale.json", "holdout")],
                    [*designs, one, row(experiment, f"holdout_{variants[1]}", "insufficient", stage="holdout")]):
        with pytest.raises(ValueError, match="unfinished"):
            ordering.release_state(pending, experiment, variants)
    released = ordering.release_state([*designs, one, other], experiment, variants)
    assert released["passing_variants"] == list(variants[:2])
    assert released["result_file"] == "design.json"


def test_committed_EV001_blocks_even_when_AV001_has_no_passing_variants(tmp_path, monkeypatch):
    for name in ordering.RELEASE_SPECS:
        path = tmp_path / "research/experiments" / name / "preregistration.md"
        path.parent.mkdir(parents=True)
        path.write_text("committed registration fixture")
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a: object())
    av, ev = ordering.RELEASE_SPECS
    rows = [row(av, v, "fail") for v in ordering.RELEASE_SPECS[av]]
    monkeypatch.setattr(hc003, "registry_rows", lambda *a: rows)
    assert lh_cli.guard_hc003_ordering(repo_root=tmp_path, config=LH001) is None
    with pytest.raises(ValueError, match="EV001.*no complete"):
        lh_cli.guard_hc003_ordering(repo_root=tmp_path, config=LH002)
    rows.extend(row(ev, v, "fail") for v in ordering.RELEASE_SPECS[ev])
    released = lh_cli.guard_hc003_ordering(repo_root=tmp_path, config=LH002)
    assert set(released) == {av, ev}
    assert all(not value["passing_variants"] for value in released.values())
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


def test_LH002_final_guard_runs_under_same_lock_even_before_cached_snapshot(tmp_path, monkeypatch):
    checks = []
    def guard(**kwargs):
        with pytest.raises(ValueError, match="in progress"):
            with ordering.execution_lock(tmp_path):
                pass
        checks.append("locked")
        raise ValueError("EV001 pending")
    monkeypatch.setattr(lh_cli, "guard_hc003_ordering", guard)
    monkeypatch.setattr(lh_cli, "_execute_final", lambda **k: pytest.fail("snapshot/execution bypassed ordering guard"))
    with pytest.raises(ValueError, match="EV001 pending"):
        lh_cli.execute_final(data_dir=tmp_path, repo_root=tmp_path, config=LH002)
    assert checks == ["locked"]
    with ordering.execution_lock(tmp_path):
        pass


def test_ordering_lock_excludes_concurrent_hc003_or_AV001_holdout(tmp_path, monkeypatch):
    monkeypatch.setattr(hc003, "_run_holdout", lambda **k: pytest.fail("parallel HC003 holdout"))
    with ordering.execution_lock(tmp_path):
        with pytest.raises(ValueError, match="in progress"):
            hc003.run_holdout(data_dir=tmp_path, repo_root=tmp_path)
    assert not (tmp_path / holdout.LEDGER_PATH).exists()
