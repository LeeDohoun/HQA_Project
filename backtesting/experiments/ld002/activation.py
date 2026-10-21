"""Section 10 evidence gates. Fixture measurements cannot authorize activation."""
from pathlib import Path
import socket
import getpass

from backtesting.experiments.lh003 import pins

from . import ACTIVATE_BY, EXPERIMENT_ID, MODEL, CODEX_VERSION, Refused
from .data import Snapshot
from .runner import check_committed, implementation_hashes
from .storage import aware, cutoff, digest, now, seal, verified


def check(root, evidence, *, at, code_hashes, machine=None):
    root = Path(root)
    blocked = []
    if (root / "model_changed.json").exists():
        blocked.append("model_changed_before_start")
    if aware(at) > aware(ACTIVATE_BY):
        blocked.append("activation_deadline")
    if evidence is None:
        return {"ready": False, "blocked": [*blocked, "section10_live_evidence_missing"], "disclosure_enabled": None}
    current = {"host": socket.gethostname(), "account": getpass.getuser()} if machine is None else machine
    if (evidence.get("experiment_id") != EXPERIMENT_ID or evidence.get("synthetic") is not False
            or aware(evidence["at"]) > aware(at) or evidence.get("machine") != current or current["host"] != "hqa-server"
            or evidence.get("research_account") is not True or evidence.get("login_verified") is not True):
        blocked.append("research_machine_login")
    identity = evidence.get("model_identity", {})
    if (identity.get("model") != MODEL or identity.get("codex_version") != CODEX_VERSION
            or not identity.get("executable_sha256") or not identity.get("artifacts")):
        blocked.append("model_identity")
    if evidence.get("implementation_hashes") != code_hashes or evidence.get("committed") is not True:
        blocked.append("committed_code_prompt_schema")
    if evidence.get("official_execution_feed_verified") is not True:
        blocked.append("official_limit_action_feed")
    performance = evidence.get("memory", {})
    if (performance.get("includes_children") is not True or performance.get("verified") is not True
            or not 0 < performance.get("peak_rss_mib", 0) <= 1536):
        blocked.append("measured_process_tree_memory")
    timing = evidence.get("timing", {})
    if (not timing.get("input_started_at") or not isinstance(timing.get("input_seconds"), (int, float))
            or timing.get("input_seconds", -1) < 0 or timing.get("real_path_verified") is not True
            or timing.get("overhead_seconds", -1) < 0):
        blocked.append("measured_worst_path_deadlines")
    else:
        from datetime import timedelta
        start = aware(timing["input_started_at"])
        built = start + timedelta(seconds=timing["input_seconds"])
        done = built + timedelta(seconds=2 * 480 + timing["overhead_seconds"])
        if built >= cutoff(start.date().isoformat(), 10) or done >= cutoff(start.date().isoformat(), 12):
            blocked.append("measured_worst_path_deadlines")
    for name in ("FW001", "FW002", "shadow", "BB003"):
        if evidence.get("no_seal_delays", {}).get(name) is not True:
            blocked.append("priority_" + name)
    restart = evidence.get("restart", {})
    if not all(restart.get(k) is True for k in ("reboot", "session_exit", "automatic_resume", "same_input_hash",
            "attempts_preserved", "no_duplicate_finalization")) or not restart.get("service_unit"):
        blocked.append("restart_resume_evidence")
    if type(evidence.get("disclosure_enabled")) is not bool or evidence.get("disclosure_enabled") and evidence.get("disclosure_full_list_verified") is not True:
        blocked.append("disclosure_policy")
    trial = evidence.get("format_check", {})
    if (trial.get("synthetic_inputs") is not True or trial.get("completed") is not True
            or trial.get("real_call_path") is not True or not 1 <= trial.get("attempts", 0) <= 10):
        blocked.append("synthetic_format_check")
    if not evidence.get("evidence_files"):
        blocked.append("verification_logs")
    else:
        from .storage import file_hash
        for path, checksum in evidence["evidence_files"].items():
            if not Path(path).is_file() or file_hash(path) != checksum:
                blocked.append("verification_log_hash")
    if not (root / "holdout.json").exists():
        blocked.append("single_holdout_snapshot")
    else:
        holdout = verified(root / "holdout.json")
        try:
            Snapshot(holdout["snapshot_path"], expected_hash=holdout["sha256"])
        except (ValueError, OSError):
            blocked.append("single_holdout_snapshot")
    return {"ready": not blocked, "blocked": sorted(set(blocked)), "disclosure_enabled": evidence.get("disclosure_enabled")}


def activate(root, evidence, *, repo_root, clock=now):
    from .storage import lock
    with lock(Path(root) / ".state.lock"):
        path = Path(root) / "start.json"
        if path.exists():
            return verified(path)
        if any((Path(root) / n).exists() for n in ("not_started.json", "holdout_failed.json")):
            raise Refused("not_started cannot be restarted")
        code_hashes = implementation_hashes(repo_root)
        result = check(root, evidence, at=clock(), code_hashes=code_hashes)
        if not result["ready"]:
            if aware(clock()) > aware(ACTIVATE_BY):
                seal(Path(root) / "not_started.json", {"status": "not_started", "at": aware(clock()).isoformat(), "blocked": result["blocked"]})
            return result
        check_committed(repo_root)
        from .runner import probe_identity
        if probe_identity() != evidence["model_identity"]:
            raise Refused("model identity changed since activation evidence")
        if aware(clock()) > aware(ACTIVATE_BY):
            result = {"status": "not_started", "at": aware(clock()).isoformat(), "blocked": ["activation_deadline"]}
            seal(Path(root) / "not_started.json", result)
            return result
        baseline = pins.capture(root, repo_root)
        value = {"experiment_id": EXPERIMENT_ID, "at": aware(clock()).isoformat(), "synthetic": False,
            "implementation_hashes": code_hashes, "code_baseline_sha256": baseline,
            "model_identity": evidence["model_identity"], "machine": evidence["machine"],
            "disclosure_enabled": evidence["disclosure_enabled"], "evidence_sha256": digest(evidence),
            "holdout_sha256": digest(verified(Path(root) / "holdout.json"))}
        seal(path, value)
        return value
