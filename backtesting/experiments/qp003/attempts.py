"""Immutable QP003 starts, paired with terminal rows in the research registry."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import uuid

from backtesting import experiment_registry
from backtesting.append_only import fsync_directory
from backtesting.experiments import hc003
from backtesting.experiments.qp001 import artifacts
from . import EXPERIMENT_ID, VARIANT


DIRECTORY = Path("research/experiments") / EXPERIMENT_ID / "attempts"


def load(repo_root, run_id):
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("invalid QP003 run_id")
    path = Path(repo_root) / DIRECTORY / (run_id + ".start.json")
    with path.open(encoding="utf-8") as handle:
        start = json.load(handle)
    if (start["run_id"] != run_id or start["experiment_id"] != EXPERIMENT_ID
            or start["variant"] != VARIANT):
        raise ValueError("QP003 start record identity differs")
    return start


def reference(repo_root, start):
    relative = DIRECTORY / (start["run_id"] + ".start.json")
    return {"file": relative.as_posix(), "sha256": artifacts.digest(Path(repo_root) / relative)}


def rows(repo_root, start):
    return [row for row in hc003.registry_rows(repo_root)
            if row["experiment_id"] == EXPERIMENT_ID and row["variant"] == VARIANT
            and json.loads(row["metrics_json"]).get("run_id") == start["run_id"]]


def refuse_pending(repo_root):
    for path in sorted((Path(repo_root) / DIRECTORY).glob("*.start.json")):
        start = load(repo_root, path.name.removesuffix(".start.json"))
        if not rows(repo_root, start):
            raise ValueError(f"unfinished QP003 start {start['run_id']}; recomputation refused; "
                             "use --recover-attempt or --abort-attempt with this run_id")


def create(repo_root, *, registration, specification, input_spec_sha256, registry_seal,
           precheck, code_commit, result_file):
    """Caller holds the ordering lock; publication and its directory are fsynced."""
    root = Path(repo_root).resolve()
    directory = root / DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    # Persist the directory entry too, before relying on the start within it.
    fsync_directory(directory.parent)
    captured = asdict(registration)
    captured["commit_time"] = registration.commit_time.isoformat()
    start = {"experiment_id": EXPERIMENT_ID, "variant": VARIANT, "run_id": uuid.uuid4().hex,
             "input_spec_sha256": input_spec_sha256, "code_hashes": specification["code"],
             "prereg_commit": registration.commit_hash, "preregistration": captured,
             "registry_seal": registry_seal, "precheck": precheck, "code_commit": code_commit,
             "result_file": Path(result_file).resolve().relative_to(root).as_posix()}
    artifacts.write_json(directory / (start["run_id"] + ".start.json"), start)
    return start


def abort(repo_root, start, reason):
    existing = rows(repo_root, start)
    if existing:
        if len(existing) == 1 and existing[0]["verdict"] == "design_aborted":
            return False
        raise ValueError("QP003 run_id already has a registered result")
    captured = dict(start["preregistration"])
    captured["commit_time"] = datetime.fromisoformat(captured["commit_time"])
    registration = experiment_registry.Preregistration(**captured)
    metrics = {"stage": "design_validation", "status": "aborted", "combined_verdict": "design_aborted",
               "run_id": start["run_id"], "input_spec_sha256": start["input_spec_sha256"],
               "start_record": reference(repo_root, start), "registry_seal": start["registry_seal"],
               "failure_reason": reason[:1024]}
    experiment_registry.record_aborted_trial(EXPERIMENT_ID, VARIANT, metrics, "design_aborted",
        preregistration=registration, repo_root=repo_root,
        note="QP003 interrupted durable start; same run_id; no recomputation")
    return True


def verify_result(repo_root, start, payload, output):
    root = Path(repo_root).resolve()
    if (Path(output).resolve() != root / start["result_file"]
            or payload["start_record"] != reference(root, start)
            or any(payload[key] != start[key] for key in
                   ("run_id", "prereg_commit", "code_commit", "input_spec_sha256", "registry_seal", "precheck"))
            or payload["input_spec"]["code"] != start["code_hashes"]):
        raise ValueError("saved result differs from its durable QP003 start")
    if any(row["verdict"] == "design_aborted" for row in rows(root, start)):
        raise ValueError("QP003 run_id was already aborted; result registration refused")
