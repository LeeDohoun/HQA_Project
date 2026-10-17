"""Shared research ordering lock and AV001/EV001 releases for LH002/LH003."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import traceback
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
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
AV002_ID = "AV002_avoidance_holdout_recovery"
AV002_TERMINAL = TERMINAL | {"sensitivity_unavailable"}
AV002_SUCCESSORS = {"EV001_earnings_filing_entry", "LH003_llm_leader_forward", "LH002_llm_hegemony_judge"}
_LOCKS = ContextVar("research_ordering_locks", default=())


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
    # Linked worktrees share this inode. Reentrant claims retain the outer
    # lock through the ledger append; no lock file or Git mutation is needed.
    directory, key = _lock_target(repo_root)
    if key in _LOCKS.get():
        raise ValueError("another research ordering/holdout execution is in progress")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another research ordering/holdout execution is in progress") from None
        try:
            token = _LOCKS.set((*_LOCKS.get(), key))
            try:
                yield
            finally:
                _LOCKS.reset(token)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _lock_target(repo_root):
    root = Path(repo_root).resolve()
    directory = root
    if (root / ".git").exists():
        directory = (root / experiment_registry._git(root, "rev-parse", "--git-common-dir")).resolve()
    stat = directory.stat()
    return directory, (os.getpid(), stat.st_dev, stat.st_ino)


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
    if _registered(AV002_ID, repo_root):
        releases[AV002_ID] = av002_release(rows, repo_root)
    return releases


def lh003_releases(rows, repo_root):
    """All predecessors are mandatory, including AV002's linked terminal rows."""
    releases = {}
    for experiment_id, variants in RELEASE_SPECS.items():
        experiment_registry.verify_preregistration(experiment_id, repo_root)
        try:
            releases[experiment_id] = release_state(rows, experiment_id, variants)
        except ValueError as exc:
            raise ValueError(str(exc).replace("LH002 final blocked", "LH003 start blocked")) from exc
    releases[AV002_ID] = av002_release(rows, repo_root)
    return releases


def _registered(experiment_id, repo_root):
    path = Path("research/experiments") / experiment_id / "preregistration.md"
    return ((Path(repo_root) / path).exists() or ((Path(repo_root) / ".git").exists()
            and bool(experiment_registry._git(Path(repo_root), "ls-files", "--", path.as_posix()))))


def av002_release(rows, repo_root):
    """Verify both terminal rows against one sealed result and its AV001 parent."""
    from backtesting.experiments import av002, common, hc003

    registration = av002._registration(repo_root)
    design, _ = av002.load_parent(repo_root)  # Never require AV002 design rows.
    terminal = [(row, json.loads(row["metrics_json"])) for row in rows
                if row["experiment_id"] == AV002_ID]
    variants = {"holdout_" + v for v in av002.VARIANTS}
    if (len(terminal) != 2 or {r["variant"] for r, _ in terminal} != variants
            or any(r["verdict"] not in AV002_TERMINAL or r["prereg_commit"] != registration.commit_hash
                   or m.get("stage") != "holdout" or m.get("parent") != av002.parent_link()
                   or m.get("design_result_file") != av002.PARENT_RESULT
                   or m.get("combined_verdict") != r["verdict"] for r, m in terminal)):
        raise ValueError("AV002 requires both verified terminal rows linked to its sealed AV001 parent")
    first = terminal[0][1]
    if (not first.get("run_id") or not first.get("result_file") or not first.get("result_sha256")
            or any(any(m.get(k) != first[k] for k in ("run_id", "result_file", "result_sha256")) for _, m in terminal)):
        raise ValueError("AV002 terminal rows must belong to the same execution/result file")
    root = Path(repo_root).resolve()
    path = root / first["result_file"]
    directory = root / "research/experiments" / AV002_ID / "results"
    if path.is_symlink() or not path.resolve().is_relative_to(directory) or hc003._digest(path) != first["result_sha256"]:
        raise ValueError("AV002 terminal result hash/path differs")
    payload = common.read_result_json(path)
    if (payload["experiment_id"] != AV002_ID or payload["stage"] != "holdout"
            or payload["prereg_commit"] != registration.commit_hash or payload["run_id"] != first["run_id"]
            or payload["parent"] != av002.parent_link() or payload["state_sha256"] != av002.STATE_SHA256
            or payload["design_effects"] != design["design_effects"] or set(payload["variants"]) != set(av002.VARIANTS)):
        raise ValueError("AV002 terminal result is not linked to the sealed parent/run")
    expected_metrics = av002.computation.registry_metrics(payload, design_result_file=av002.PARENT_RESULT)
    for row, metrics in terminal:
        variant = row["variant"].removeprefix("holdout_")
        combined = payload["variants"][variant]
        if (any(metrics.get(k) != v for k, v in expected_metrics[row["variant"]].items())
                or metrics.get("code_manifest") != payload.get("code_manifest")
                or combined["verdict"] != row["verdict"] or metrics.get("state_sha256") != av002.STATE_SHA256
                or any(metrics["rules"][r]["verdict"] != payload["rules"][r][variant]["verdict"]
                       or combined["rule_verdicts"][r] != payload["rules"][r][variant]["verdict"] for r in ("S", "O"))):
            raise ValueError("AV002 row/result verdicts differ")
        if row["verdict"] == "pass" and any(payload["rules"][r][variant]["verdict"] != "pass"
                or av002.computation.holdout_verdict(payload["rules"][r][variant], design["design_effects"][r][variant])[0] != "pass"
                for r in ("S", "O")):
            raise ValueError("AV002 pass requires both rule-specific section 8 passes")
    return {"result_file": first["result_file"], "result_sha256": first["result_sha256"],
            "run_id": first["run_id"], "parent": av002.parent_link(),
            "passing_variants": list(av002.VARIANTS), "terminal_holdout_variants": list(av002.VARIANTS)}


@contextmanager
def protected_claim(experiment_id, repo_root):
    """Also guard direct sessions/reads, including LH003's future seal runner."""
    if experiment_id not in AV002_SUCCESSORS or not _registered(AV002_ID, repo_root):
        yield
        return
    from backtesting.experiments import hc003

    with holdout_execution_lock(experiment_id, repo_root):
        rows = hc003.registry_rows(repo_root)
        if experiment_id == "EV001_earnings_filing_entry":
            av002_release(rows, repo_root)
        else:
            registered_releases(rows, repo_root)
        yield


def holdout_execution_lock(experiment_id, repo_root):
    """Keep a successor's lock through its entire session, including an LH003 seal."""
    if (experiment_id not in AV002_SUCCESSORS or not _registered(AV002_ID, repo_root)
            or _lock_target(repo_root)[1] in _LOCKS.get()):
        return nullcontext()
    return execution_lock(repo_root)
