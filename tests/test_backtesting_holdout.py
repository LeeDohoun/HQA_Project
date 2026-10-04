from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

import pytest
import yaml

from backtesting.holdout import HOLDOUT_END, HOLDOUT_START, HoldoutSession, guard_period


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.name", "Offline Test")
    git("config", "user.email", "offline@example.invalid")
    template = Path(__file__).resolve().parents[1] / "research/experiments/TEMPLATE/preregistration.md"
    fields = yaml.safe_load(template.read_text(encoding="utf-8").split("---", 2)[1])
    for experiment_id, uses_holdout in (("D001", True), ("D002", True), ("NO_HOLDOUT", False)):
        path = tmp_path / "research/experiments" / experiment_id / "preregistration.md"
        path.parent.mkdir(parents=True)
        fields.update(experiment_id=experiment_id, uses_holdout=uses_holdout)
        path.write_text("---\n" + yaml.safe_dump(fields) + "---\n", encoding="utf-8")
        git("add", "--", str(path.relative_to(tmp_path)))
    git("commit", "-qm", "Register offline holdout experiments")
    return tmp_path


def _ledger(root):
    return root / "research/experiments/holdout_ledger.jsonl"


@pytest.mark.parametrize("start,end", [("20230101", "20251231"), ("20260701", "20261231")])
def test_nonoverlapping_period_needs_no_registration_or_ledger(tmp_path, start, end):
    guard_period(start, end, repo_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("start,end", [
    ("20251231", "20260101"), ("20260630", "20260701"), ("20260101", "20260630"),
    ("20250101", "20270101"), (date(2026, 3, 1), date(2026, 3, 1)),
])
def test_overlap_including_endpoints_requires_experiment(tmp_path, start, end):
    with pytest.raises(ValueError, match="experiment_id"):
        guard_period(start, end, repo_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("start,end", [
    ("20260102", "20260101"), ("20260230", "20260301"), ("2026-01-01", "20260630"),
    (None, "20260630"), ("202611", "20261231"),
])
def test_invalid_period_is_rejected(tmp_path, start, end):
    with pytest.raises(ValueError):
        guard_period(start, end, repo_root=tmp_path)


@pytest.mark.parametrize("experiment_id", ["UNKNOWN", "NO_HOLDOUT"])
def test_preregistration_is_required_for_both_entry_points(repo, experiment_id):
    with pytest.raises(ValueError):
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id=experiment_id, repo_root=repo)
    with pytest.raises(ValueError):
        with HoldoutSession(experiment_id, repo_root=repo):
            pytest.fail("session must not open")
    assert not _ledger(repo).exists()


def test_standalone_call_consumes_one_use_and_preserves_ledger(repo):
    guard_period("20251201", "20260102", experiment_id="D001", repo_root=repo)
    before = _ledger(repo).read_bytes()
    row = json.loads(before)
    assert row["experiment_id"] == "D001"
    assert row["from_date"] == "2025-12-01"
    assert row["to_date"] == "2026-01-02"
    assert row["recorded_at"].endswith("+00:00")
    with pytest.raises(ValueError, match="already used"):
        guard_period("20260601", "20260630", experiment_id="D001", repo_root=repo)
    with pytest.raises(ValueError, match="already used"):
        with HoldoutSession("D001", repo_root=repo):
            pytest.fail("already consumed")
    assert _ledger(repo).read_bytes() == before


def test_session_allows_repeated_calls_only_while_open(repo):
    session = HoldoutSession("D001", repo_root=repo)
    with pytest.raises(ValueError, match="not open"):
        session.guard_period(HOLDOUT_START, HOLDOUT_END)
    with session:
        session.guard_period("20260101", "20260331")
        session.guard_period(date(2026, 4, 1), HOLDOUT_END)
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo)
        with pytest.raises(ValueError, match="experiment_id"):
            guard_period(HOLDOUT_START, HOLDOUT_END, repo_root=repo)
        with pytest.raises(ValueError, match="already used"):
            with HoldoutSession("D001", repo_root=repo):
                pytest.fail("second session must not open")
    rows = [json.loads(line) for line in _ledger(repo).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert (rows[0]["from_date"], rows[0]["to_date"]) == ("2026-01-01", "2026-06-30")
    with pytest.raises(ValueError, match="not open"):
        session.guard_period(HOLDOUT_START, HOLDOUT_END)
    with pytest.raises(ValueError, match="cannot be reopened"):
        with session:
            pytest.fail("cannot reopen")
    with pytest.raises(ValueError, match="already used"):
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo)
    with pytest.raises(ValueError, match="already used"):
        with HoldoutSession("D001", repo_root=repo):
            pytest.fail("a new session object must not reopen a consumed experiment")


def test_failed_evaluation_still_consumes_holdout(repo):
    with pytest.raises(RuntimeError, match="evaluation failed"):
        with HoldoutSession("D001", repo_root=repo):
            raise RuntimeError("evaluation failed")
    with pytest.raises(ValueError, match="already used"):
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo)


def test_different_experiments_have_separate_uses(repo):
    with HoldoutSession("D001", repo_root=repo) as first:
        with pytest.raises(ValueError):
            guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="NO_HOLDOUT", repo_root=repo)
        with HoldoutSession("D002", repo_root=repo) as second:
            first.guard_period(HOLDOUT_START, HOLDOUT_END)
            second.guard_period(HOLDOUT_START, HOLDOUT_END)
        first.guard_period(HOLDOUT_START, HOLDOUT_END)
        with pytest.raises(ValueError, match="already used"):
            guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D002", repo_root=repo)
    assert len(_ledger(repo).read_text(encoding="utf-8").splitlines()) == 2


def test_custom_ledger_is_relative_to_repo_and_append_only(repo):
    ledger = repo / "custom/holdout.jsonl"
    with HoldoutSession("D001", repo_root=repo, ledger_path="custom/holdout.jsonl") as session:
        session.guard_period(HOLDOUT_START, HOLDOUT_END)
    before = ledger.read_bytes()
    guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D002", repo_root=repo, ledger_path=ledger)
    assert ledger.read_bytes().startswith(before)
    assert not _ledger(repo).exists()
    with pytest.raises(ValueError, match="already used"):
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo, ledger_path=ledger)


@pytest.mark.parametrize("content", ["{broken\n", "[]\n", '{}\n'])
def test_corrupt_ledger_fails_without_rewriting(repo, content):
    _ledger(repo).write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid holdout ledger"):
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo)
    assert _ledger(repo).read_text(encoding="utf-8") == content


def test_open_session_does_not_authorize_another_thread(repo):
    with HoldoutSession("D001", repo_root=repo):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(guard_period, HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo)
            with pytest.raises(ValueError, match="already used"):
                result.result()


def test_unterminated_ledger_cannot_merge_a_new_claim(repo):
    row = {"experiment_id": "D002", "recorded_at": "2026-10-04T00:00:00+00:00",
           "from_date": "2026-01-01", "to_date": "2026-06-30"}
    before = json.dumps(row)
    _ledger(repo).write_text(before, encoding="utf-8")
    with pytest.raises(ValueError, match="must end with a newline"):
        guard_period(HOLDOUT_START, HOLDOUT_END, experiment_id="D001", repo_root=repo)
    assert _ledger(repo).read_text(encoding="utf-8") == before


def test_concurrent_sessions_cannot_consume_same_experiment_twice(repo):
    barrier = Barrier(2)

    def attempt():
        barrier.wait(timeout=5)
        try:
            with HoldoutSession("D001", repo_root=repo):
                return "allowed"
        except ValueError as exc:
            assert "already used" in str(exc)
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(future.result() for future in futures) == ["allowed", "denied"]
    assert len(_ledger(repo).read_text(encoding="utf-8").splitlines()) == 1
