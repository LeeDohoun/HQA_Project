"""Export an already sealed PC trial to the authoritative research registry."""
import json
from pathlib import Path

from backtesting import experiment_registry
from . import EXPERIMENT_ID
from .storage import MAIN, sha
from .deployment import verified


@verified("evaluation")
def publish_trial(store, repo_root):
    root = Path(repo_root).resolve()
    if root.is_relative_to(MAIN):
        raise ValueError("BB003 never writes the shared main checkout")
    proof = store.proof("evaluation", "final")
    if proof and not proof["valid"]:
        raise ValueError("BB003 requires a valid sealed final evaluation")
    if proof:
        result = proof["record"]
        identity = {"evaluation_sha256": proof["record_sha256"]}
        metrics = {"evaluation_file": str(store.path(proof["record_path"])),
                   "manifest_sha256": result["manifest_sha256"], "freeze": result["freeze"],
                   "verdict": result["verdict"]}
    else:
        started = store.proof("evaluation_started", "final")
        if not started or store.proof("evaluation_computed", "final"):
            raise ValueError("BB003 requires a sealed evaluation or a terminal abort without a computation checkpoint; recover a checkpoint first")
        from .evaluate import abort_evaluation
        abort = abort_evaluation(store, started, RuntimeError("interrupted evaluation"), phase="publication")
        if (abort["evaluation_started_sha256"] != started["record_sha256"]
                or abort["manifest_sha256"] != started["record"]["manifest_sha256"]
                or abort["status"] != "forward_aborted" or abort["terminal"] is not True or abort["uses_holdout"] is not False
                or abort["recomputation_allowed"] is not False or abort["label"] != "forward"):
            raise ValueError("BB003 terminal abort differs from its consumed attempt")
        identity = {"evaluation_abort_sha256": sha(store.path("evaluation_abort.json").read_bytes())}
        metrics = {"evaluation_abort_file": str(store.path("evaluation_abort.json")),
                   "evaluation_started_sha256": started["record_sha256"],
                   "manifest_sha256": abort["manifest_sha256"], "freeze": started["record"]["freeze"],
                   "verdict": "forward_aborted", "evaluation_status": "forward_aborted", "uses_holdout": False,
                   "recomputation_allowed": False}
    registry = root / experiment_registry.REGISTRY_PATH
    if registry.is_file():
        with registry.open(newline="") as handle:
            for row in experiment_registry._csv_reader(handle):
                if row["experiment_id"] == EXPERIMENT_ID and row["variant"] == "forward_d21":
                    previous = json.loads(row["metrics_json"])
                    if any(previous.get(key) != value for key, value in identity.items()):
                        raise ValueError("BB003 authoritative registry refers to a different final result")
                    return {"status": "already_exported", **identity}
    from .deployment import verify_operation
    verification = verify_operation(store, mode="evaluation")
    experiment_registry.record_trials(EXPERIMENT_ID, [("forward_d21", {
        **identity, **metrics, "publication_manifest_sha256": verification["manifest_sha256"],
        "selected_variant": "d21"}, metrics["verdict"])],
        repo_root=root, note="BB003 fixed-period forward d21; one final evaluation; no Holm")
    return {"status": "exported", **identity}
