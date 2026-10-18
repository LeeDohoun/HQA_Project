"""Producer provenance survives code restoration; all archives are synthetic."""
import importlib.util
import shutil

import pytest

from backtesting import experiment_registry
from backtesting.experiments.lh003 import Refused, pins, runner
from backtesting.experiments.lh003.distribution import CODE_REFERENCE, Distribution, code_pins, latest, protected
from backtesting.experiments.lh003.simulation import synthetic_distribution
from backtesting.experiments.lh003.storage import aware, digest, seal, verified
from scripts.lh003 import distribute
from src.ingestion.storage import read_rows
from test_experiments_lh003_round23 import amend, calendar_update, pinned, tax_update
from test_experiments_lh003_runner import Clock, forbid_real_calls_and_holdout, verified_calendar
from test_experiments_lh003_snapshot import snapshot_for, source, source_fixture

AT = "2026-11-13T18:00:00+09:00"


def copy_reference(repo):
    target = repo / CODE_REFERENCE
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(experiment_registry.PROJECT_ROOT / CODE_REFERENCE, target)


def test_prestart_publication_uses_fixed_reference_without_model_manifest(source, tmp_path):
    publication, _ = synthetic_distribution(source, tmp_path / "distributions", at=aware(AT))
    reference = verified(experiment_registry.PROJECT_ROOT / CODE_REFERENCE)
    receipt = publication.manifest["code_pins"]
    assert receipt["implementation_hashes"] == reference["start"]["implementation_hashes"]
    assert receipt["code_baseline_sha256"] == digest(reference["sources"])
    assert receipt["amendment_hashes"] == []
    assert "scripts/lh003/distribute.py" in receipt["implementation_hashes"]
    assert "src/ingestion/dart_financial_date_rules.py" in receipt["implementation_hashes"]
    assert not (tmp_path / "model_manifest.json").exists()


def test_missing_prestart_reference_refuses_before_raw_inventory(pinned, source, tmp_path, monkeypatch):
    _, repo = pinned
    _, sync = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    assert not (repo / CODE_REFERENCE).exists()
    monkeypatch.setattr(distribute, "inventory", lambda *a: pytest.fail("raw inventory without approved reference"))
    directory = tmp_path / "refused"
    with pytest.raises(FileNotFoundError):
        distribute.publish(data_dir=source, directory=directory, sync_manifest=sync,
                           state_root=tmp_path / "prestart", repo_root=repo, clock=Clock(), synthetic=True)
    assert not list((directory / "publications").glob("*.complete.json"))


def test_prestart_publication_retains_fixed_reference_after_start_and_amendment(pinned, source, tmp_path):
    state, repo = pinned
    copy_reference(repo)
    before_start = aware("2026-11-11T18:00:00+09:00")
    publication, sync = synthetic_distribution(source, tmp_path / "prestart", at=before_start)
    amend(state, repo, tax_update(repo))
    historical, _ = latest(tmp_path / "prestart", sync_manifest=sync, at=aware(AT),
                           allow_synthetic=True, state_root=state, repo_root=repo)
    assert historical.manifest["code_pins"] == publication.manifest["code_pins"]
    assert historical.manifest["code_pins"]["amendment_hashes"] == []


@pytest.mark.parametrize("name", ["scripts/lh003/distribute.py", "src/ingestion/dart_financial_date_rules.py"])
@pytest.mark.parametrize("prestart", [False, True])
def test_modified_code_refuses_before_raw_inventory(pinned, source, tmp_path, monkeypatch, name, prestart):
    state, repo = pinned
    copy_reference(repo)
    if prestart:
        state = tmp_path / "not_started_yet"
    _, sync = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    target = repo / name
    target.write_text(target.read_text() + "\n# modified producer/dependency\n")
    monkeypatch.setattr(distribute, "inventory", lambda *a: pytest.fail("raw inventory before code check"))
    directory = tmp_path / "refused"
    with pytest.raises(Refused, match="pinned implementation"):
        distribute.publish(data_dir=source, directory=directory, sync_manifest=sync,
                           state_root=state, repo_root=repo, clock=Clock(), synthetic=True)
    assert not list((directory / "publications").glob("*.complete.json"))


def test_code_drift_during_generation_blocks_completion(pinned, source, tmp_path, monkeypatch):
    state, repo = pinned
    _, sync = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    original = distribute.financial_dates
    target = repo / "scripts/lh003/distribute.py"

    def changed(*args):
        result = original(*args)
        target.write_text(target.read_text() + "\n# changed during generation\n")
        return result

    monkeypatch.setattr(distribute, "financial_dates", changed)
    directory = tmp_path / "refused"
    with pytest.raises(Refused, match="pinned implementation"):
        distribute.publish(data_dir=source, directory=directory, sync_manifest=sync,
                           state_root=state, repo_root=repo, clock=Clock(), synthetic=True)
    assert list((directory / "snapshots").glob("*/manifest.json"))
    assert not list((directory / "publications").glob("*.complete.json"))
    with pytest.raises(Refused, match="failed or is incomplete"):
        latest(directory, sync_manifest=sync, at=aware(AT), state_root=state, repo_root=repo)


def test_copied_modified_script_cannot_borrow_approved_checkout_hashes(source, tmp_path, monkeypatch):
    _, sync = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    target = tmp_path / "copied_distributor.py"
    target.write_text((experiment_registry.PROJECT_ROOT / "scripts/lh003/distribute.py").read_text()
                      .replace("if not protected(day):", "if True:"))
    spec = importlib.util.spec_from_file_location("copied_lh003_distributor", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "inventory", lambda *a: pytest.fail("copied code read raw archives"))
    with pytest.raises(Refused, match="process-loaded code"):
        module.publish(data_dir=source, directory=tmp_path / "refused", sync_manifest=sync,
                       clock=Clock(), synthetic=True)


@pytest.mark.parametrize("inject_h1", [False, True])
def test_foreign_distributor_publication_refused_after_code_restored(pinned, source, tmp_path, inject_h1):
    approved, repo = pinned
    copy_reference(repo)
    _, sync = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    target = repo / "scripts/lh003/distribute.py"
    original = target.read_text()
    tampered = original.replace("if not protected(day):", "if True:") if inject_h1 else original + "\n# foreign code\n"
    assert tampered != original
    target.write_text(tampered)
    # A foreign checkout approved its own implementation. The research reader
    # must use its independent approved state/reference, never this producer's.
    foreign = tmp_path / "foreign_state"
    baseline = pins.capture(foreign, repo)
    seal(foreign / "start.json", {"at": "2026-11-12T10:00:00+09:00",
         "code_baseline_sha256": baseline, "implementation_hashes": runner.implementation_hashes(repo)})
    spec = importlib.util.spec_from_file_location("tampered_lh003_distributor", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._LOADED_HASHES == runner.implementation_hashes(repo)
    directory = tmp_path / "foreign_publications"
    receipt = module.publish(data_dir=source, directory=directory, sync_manifest=sync,
                             state_root=foreign, repo_root=repo, clock=Clock(), synthetic=True)
    manifest_path = directory / receipt["manifest_path"]
    manifest = verified(manifest_path)
    assert manifest["code_pins"]["implementation_hashes"]["scripts/lh003/distribute.py"] == pins.source_hash(tampered)
    Distribution(manifest_path, expected_hash=receipt["manifest_sha256"], at=AT, allow_synthetic=True,
                 state_root=foreign, repo_root=repo)
    if inject_h1:
        assert any(protected(r["trade_date"]) for r in read_rows(manifest_path.parent / "market_context/benchmarks.jsonl"))
    target.write_text(original)
    assert runner.implementation_hashes(repo) == verified(approved / "start.json")["implementation_hashes"]
    for state in (approved, tmp_path / "prestart_reader"):
        with pytest.raises(Refused, match="generating code/amendment"):
            latest(directory, sync_manifest=sync, at=aware(AT), allow_synthetic=True,
                   state_root=state, repo_root=repo)


@pytest.mark.parametrize("update", [tax_update, calendar_update])
def test_mechanical_amendment_producer_and_reader_share_chain(pinned, source, tmp_path, monkeypatch, update):
    state, repo = pinned
    _, sync = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    record = amend(state, repo, update(repo))
    monkeypatch.setattr(distribute, "_LOADED_HASHES", runner.implementation_hashes(repo))
    directory = tmp_path / "amended"
    distribute.publish(data_dir=source, directory=directory, sync_manifest=sync,
                       state_root=state, repo_root=repo, clock=Clock(), synthetic=True)
    publication, _ = latest(directory, sync_manifest=sync, at=aware(AT), allow_synthetic=True,
                            state_root=state, repo_root=repo)
    assert publication.manifest["code_pins"] == code_pins(state, repo, at=AT)
    assert publication.manifest["code_pins"]["amendment_hashes"] == [digest(record)]
    assert publication.manifest["code_pins"]["implementation_hashes"][record["path"]] == record["after_sha256"]
    publication.manifest["code_pins"]["amendment_hashes"] = []
    with pytest.raises(Refused, match="generating code/amendment"):
        publication.verify_code()


def test_legacy_manifest_without_producer_receipt_refused_even_with_valid_data_hashes(source, tmp_path):
    publication, _ = synthetic_distribution(source, tmp_path / "valid", at=aware(AT))
    value = dict(publication.manifest)
    del value["code_pins"]
    target = tmp_path / digest(value)
    shutil.copytree(publication.root, target)
    target.chmod(0o755)
    (target / "manifest.json").unlink()
    seal(target / "manifest.json", value)
    with pytest.raises(Refused, match="generating code/amendment"):
        Distribution(target / "manifest.json", expected_hash=digest(value), at=AT, allow_synthetic=True)


def test_snapshot_rechecks_producer_before_merging_rows(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    snapshot.distribution.manifest["code_pins"]["implementation_hashes"]["scripts/lh003/distribute.py"] = "0" * 64
    with pytest.raises(Refused, match="generating code/amendment"):
        snapshot.stream("market_context/benchmarks.jsonl")
