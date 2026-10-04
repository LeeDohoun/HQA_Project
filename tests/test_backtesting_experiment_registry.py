from __future__ import annotations

import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from backtesting.experiment_registry import (
    REGISTRY_COLUMNS, REQUIRED_FIELDS, record_trial, required_t_stat, trial_count, verify_preregistration,
)


TEMPLATE = Path(__file__).resolve().parents[1] / "research/experiments/TEMPLATE/preregistration.md"


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Offline Test")
    _git(tmp_path, "config", "user.email", "offline@example.invalid")
    fields = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8").split("---", 2)[1])
    fields["experiment_id"] = "D001"
    path = tmp_path / "research/experiments/D001/preregistration.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\n" + yaml.safe_dump(fields, allow_unicode=True) + "---\n가설\n", encoding="utf-8")
    return tmp_path, path, fields


def _commit(root, path):
    _git(root, "add", "--", str(path.relative_to(root)))
    _git(root, "commit", "-qm", "Register offline experiment")


def test_untracked_and_staged_only_preregistrations_are_rejected(repo):
    root, path, _ = repo
    with pytest.raises(ValueError):
        verify_preregistration("D001", root)
    _git(root, "add", "--", str(path.relative_to(root)))
    with pytest.raises(ValueError):
        verify_preregistration("D001", root)


def test_committed_registration_returns_file_commit_and_fields(repo):
    root, path, fields = repo
    _commit(root, path)
    expected_hash = _git(root, "rev-parse", "HEAD")
    (root / "unrelated.txt").write_text("unrelated", encoding="utf-8")
    _commit(root, root / "unrelated.txt")
    result = verify_preregistration("D001", root)
    assert result.commit_hash == expected_hash
    assert result.commit_time.isoformat() == _git(root, "log", "-1", "--format=%cI", "--", str(path))
    assert result.commit_time.tzinfo is not None
    assert result.fields == fields


@pytest.mark.parametrize("staged", [False, True])
def test_modified_registration_is_rejected_without_recording(repo, staged):
    root, path, _ = repo
    _commit(root, path)
    path.write_text(path.read_text(encoding="utf-8") + "変更\n", encoding="utf-8")
    if staged:
        _git(root, "add", "--", str(path.relative_to(root)))
    with pytest.raises(ValueError):
        record_trial("D001", "v1", {}, "fail", repo_root=root)
    assert not (root / "research/experiments/registry.csv").exists()


def test_edited_and_recommitted_registration_is_rejected(repo):
    root, path, fields = repo
    _commit(root, path)
    fields["pass_criteria"]["design_validation"]["net_performance_gt"] = -1
    path.write_text("---\n" + yaml.safe_dump(fields) + "---\n", encoding="utf-8")
    _commit(root, path)
    with pytest.raises(ValueError, match="new experiment_id"):
        verify_preregistration("D001", root)
    with pytest.raises(ValueError, match="new experiment_id"):
        record_trial("D001", "v1", {}, "pass", repo_root=root)
    assert not (root / "research/experiments/registry.csv").exists()


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_missing_required_field(repo, missing):
    root, path, fields = repo
    del fields[missing]
    path.write_text("---\n" + yaml.safe_dump(fields) + "---\n", encoding="utf-8")
    _commit(root, path)
    with pytest.raises(ValueError, match="missing preregistration fields"):
        verify_preregistration("D001", root)


@pytest.mark.parametrize("text", ["no front matter", "---\n{}", "---\n[\n---\n", "---\n- item\n---\n"])
def test_bad_front_matter(repo, text):
    root, path, _ = repo
    path.write_text(text, encoding="utf-8")
    _commit(root, path)
    with pytest.raises(ValueError):
        verify_preregistration("D001", root)


@pytest.mark.parametrize("field,value", [
    ("experiment_id", "OTHER"), ("uses_holdout", "false"), ("uses_llm", 1),
    ("hypothesis", None), ("variants_planned", 0), ("variants_planned", True),
])
def test_invalid_preregistration_fields(repo, field, value):
    root, path, fields = repo
    fields[field] = value
    path.write_text("---\n" + yaml.safe_dump(fields) + "---\n", encoding="utf-8")
    _commit(root, path)
    with pytest.raises(ValueError):
        verify_preregistration("D001", root)


@pytest.mark.parametrize("experiment_id", ["", "..", "../outside", "/absolute", "a/b", "a\\b", None])
def test_invalid_experiment_id(tmp_path, experiment_id):
    with pytest.raises(ValueError):
        verify_preregistration(experiment_id, tmp_path)


def test_append_preserves_existing_bytes_and_records_failed_repeated_trials(repo):
    root, path, _ = repo
    _commit(root, path)
    registry = root / "custom.csv"
    registry.write_text(",".join(REGISTRY_COLUMNS) + "\n", encoding="utf-8")
    for verdict in ("fail", "pass"):
        before = registry.read_bytes()
        record_trial("D001", "same-variant", {"net": -0.1, "설명": "a,b"}, verdict,
                     note="첫째 줄\n둘째 줄", repo_root=root, registry_path="custom.csv")
        assert registry.read_bytes().startswith(before)
    with registry.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert [row["verdict"] for row in rows] == ["fail", "pass"]
    assert json.loads(rows[0]["metrics_json"]) == {"net": -0.1, "설명": "a,b"}
    assert rows[0]["note"] == "첫째 줄\n둘째 줄"
    assert rows[0]["prereg_commit"] == _git(root, "rev-parse", "HEAD")
    assert trial_count("D001", repo_root=root, registry_path=registry) == 2
    assert trial_count("OTHER", repo_root=root, registry_path=registry) == 0


@pytest.mark.parametrize("earlier_experiment_id", ["D001", "OTHER"])
def test_existing_prereg_commit_is_checked_per_experiment(repo, earlier_experiment_id):
    root, path, _ = repo
    _commit(root, path)
    registry = root / "research/experiments/registry.csv"
    with registry.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(REGISTRY_COLUMNS)
        writer.writerow(("2026-10-04T00:00:00+00:00", earlier_experiment_id, "0" * 40,
                         "earlier", "fail", "{}", ""))
        writer.writerow(("2026-10-04T00:00:00+00:00", "D001", _git(root, "rev-parse", "HEAD"),
                         "latest", "fail", "{}", ""))
    before = registry.read_bytes()
    if earlier_experiment_id == "D001":
        with pytest.raises(ValueError, match="prereg_commit.*new experiment_id"):
            record_trial("D001", "v1", {}, "fail", repo_root=root)
        assert registry.read_bytes() == before
    else:
        record_trial("D001", "v1", {}, "fail", repo_root=root)
        assert registry.read_bytes().startswith(before)
        assert trial_count("D001", repo_root=root) == 2


def test_trial_count_and_exact_threshold_boundary(repo):
    root, path, _ = repo
    _commit(root, path)
    assert trial_count("D001", repo_root=root) == 0
    assert required_t_stat("D001", repo_root=root) == 2.0
    for number in range(21):
        record_trial("D001", f"v{number}", {}, "fail", repo_root=root)
        assert trial_count("D001", repo_root=root) == number + 1
        assert required_t_stat("D001", repo_root=root) == (2.0 if number < 20 else 3.0)


@pytest.mark.parametrize("metrics", [{"bad": float("nan")}, {"bad": float("inf")}, {"bad": object()}, []])
def test_invalid_metrics_do_not_create_registry(repo, metrics):
    root, path, _ = repo
    _commit(root, path)
    with pytest.raises(ValueError):
        record_trial("D001", "v1", metrics, "fail", repo_root=root)
    assert not (root / "research/experiments/registry.csv").exists()


def test_corrupt_registry_is_not_rewritten(repo):
    root, path, _ = repo
    _commit(root, path)
    registry = root / "broken.csv"
    registry.write_text("wrong,header\n", encoding="utf-8")
    before = registry.read_bytes()
    with pytest.raises(ValueError):
        record_trial("D001", "v1", {}, "fail", repo_root=root, registry_path=registry)
    with pytest.raises(ValueError):
        trial_count("D001", repo_root=root, registry_path=registry)
    assert registry.read_bytes() == before


def test_unterminated_registry_cannot_merge_new_trial_into_existing_row(repo):
    root, path, _ = repo
    _commit(root, path)
    registry = root / "custom.csv"
    registry.write_text(",".join(REGISTRY_COLUMNS), encoding="utf-8")
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="must end with a newline"):
        record_trial("D001", "v1", {}, "fail", repo_root=root, registry_path=registry)
    assert registry.read_bytes() == before


def test_concurrent_appends_preserve_every_trial(repo):
    root, path, _ = repo
    _commit(root, path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(record_trial, "D001", f"v{i}", {"i": i}, "fail", repo_root=root) for i in range(8)]
        for future in futures:
            future.result()
    registry = root / "research/experiments/registry.csv"
    with registry.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["variant"] for row in rows} == {f"v{i}" for i in range(8)}
    assert trial_count("D001", repo_root=root) == 8
