"""Shared research ordering lock and AV001/EV001 release contract for LH002."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path

from backtesting import experiment_registry


RELEASE_SPECS = {
    "AV001_avoidance_portfolio": ("av_all", "av_phase", "av_conc"),
    "EV001_earnings_filing_entry": ("ev_new20", "ev_new60", "ev_new20_f"),
}
TERMINAL = {"pass", "fail", "holdout_insufficient", "version_sensitive", "delisting_sensitive",
            "holdout_aborted", "holdout_contaminated"}
DESIGN_FINAL = {"pass", "fail", "insufficient", "risk_only", "version_sensitive",
                "delisting_sensitive", "sensitivity_unavailable", "data_incomplete"}


def abort_details(hashes, exc):
    """Category digests keep full-market inventories within the registry limit."""
    frame = traceback.extract_tb(exc.__traceback__)[-1]
    return {"run_id": uuid.uuid4().hex,
            "input_hashes": {key: hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
                             for key, value in hashes.items()},
            "failure_location": {"file": frame.filename, "line": frame.lineno, "function": frame.name},
            "failure_type": type(exc).__name__, "failure_reason": str(exc)[:1024]}


@contextmanager
def design_attempt(experiment_id, variants, repo_root):
    """Record failures after preflight hashes, without changing successful results."""
    registration = experiment_registry.verify_preregistration(experiment_id, repo_root)
    attempt = {}
    try:
        yield attempt
    except BaseException as exc:
        if "source_hashes" in attempt:
            metrics = {"stage": "design_validation", "status": "aborted", "combined_verdict": "design_aborted",
                       **abort_details(attempt["source_hashes"], exc)}
            experiment_registry.encode_metrics(metrics)
            for variant in variants:
                experiment_registry.record_aborted_trial(experiment_id, variant, metrics, "design_aborted",
                                                 preregistration=registration, repo_root=repo_root,
                                                 note="Aborted design attempt; input hashes are category SHA256 digests")
        raise


@contextmanager
def execution_lock(repo_root):
    # Linux/WSL flock on the stable checkout directory coordinates executions
    # without creating untracked files or changing read-only failure paths.
    descriptor = os.open(Path(repo_root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another research ordering/holdout execution is in progress") from None
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def design_run(rows, experiment_id, variants):
    """Latest design attempt must have all three final rows from one file.

    A partial newer attempt does not inherit completion from an older run.
    Terminal holdout rows must name this design file, preventing stale release.
    """
    designs = []
    for row in rows:
        if row["experiment_id"] != experiment_id or row["variant"] not in variants:
            continue
        metrics = json.loads(row["metrics_json"])
        if metrics.get("stage") == "design_validation":
            designs.append((row, metrics))
    if not designs:
        raise ValueError(f"{experiment_id}: no complete design rows; LH002 final blocked")
    result_file = designs[-1][1].get("result_file")
    same_run = [(row, metrics) for row, metrics in designs if metrics.get("result_file") == result_file]
    if (not isinstance(result_file, str) or not result_file or len(same_run) != len(variants)
            or {row["variant"] for row, _ in same_run} != set(variants)
            or any(row["verdict"] not in DESIGN_FINAL or metrics.get("status") == "aborted"
                   for row, metrics in same_run)
            or len({row["prereg_commit"] for row, _ in same_run}) != 1):
        raise ValueError(f"{experiment_id}: requires all three final design rows from one result file; LH002 final blocked")
    passed = [name for name in variants if any(row["variant"] == name and row["verdict"] == "pass" for row, _ in same_run)]
    return {"result_file": result_file, "passing_variants": passed}


def release_state(rows, experiment_id, variants):
    state = design_run(rows, experiment_id, variants)
    finished = set()
    for row in rows:
        if row["experiment_id"] != experiment_id or row["verdict"] not in TERMINAL:
            continue
        metrics = json.loads(row["metrics_json"])
        if metrics.get("stage") == "holdout" and metrics.get("design_result_file") == state["result_file"]:
            for name in state["passing_variants"]:
                if row["variant"] == f"holdout_{name}":
                    finished.add(name)
    pending = set(state["passing_variants"]) - finished
    if pending:
        raise ValueError(f"{experiment_id}: holdout unfinished for {','.join(sorted(pending))}; LH002 final blocked")
    return {**state, "terminal_holdout_variants": sorted(finished)}


def registered_releases(rows, repo_root):
    releases = {}
    for experiment_id, variants in RELEASE_SPECS.items():
        path = Path(repo_root) / "research/experiments" / experiment_id / "preregistration.md"
        tracked = (not path.exists() and (Path(repo_root) / ".git").exists()
                   and experiment_registry._git(Path(repo_root), "ls-files", "--", str(path.relative_to(repo_root))))
        if path.exists() or tracked:
            experiment_registry.verify_preregistration(experiment_id, repo_root)
            releases[experiment_id] = release_state(rows, experiment_id, variants)
    return releases
