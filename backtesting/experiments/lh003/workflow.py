"""One protected acquisition, timely immutable decisions, crash-safe A fallback."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from backtesting import experiment_registry, holdout
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import hc003, ordering

from . import EXPERIMENT_ID, Refused
from . import calendar, inputs
from .snapshot import Snapshot, publish_snapshot
from .storage import aware, digest, now, seal, verified


def machine_id():
    path = Path("/etc/machine-id")
    if not path.is_file():
        raise Refused("execution machine requires /etc/machine-id; hostname substitution is forbidden")
    identity = path.read_text().strip()
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        raise Refused("execution machine has an invalid /etc/machine-id")
    return hashlib.sha256(identity.encode()).hexdigest()


def claimed(repo_root):
    path = Path(repo_root) / holdout.LEDGER_PATH
    if not path.exists():
        return False
    import json
    return any(json.loads(line)["experiment_id"] == EXPERIMENT_ID for line in path.read_text().splitlines() if line.strip())


def not_started(root, reason, *, repo_root, at):
    path = Path(root) / "not_started.json"
    if path.exists():
        value = verified(path)
    else:
        value = {"experiment_id": EXPERIMENT_ID, "verdict": "not_started", "reason": reason,
                 "at": aware(at).isoformat(), "holdout_claimed": claimed(repo_root)}
        seal(path, value)
    import json
    rows = [r for r in hc003.registry_rows(repo_root)
            if r["experiment_id"] == EXPERIMENT_ID and r["verdict"] == "not_started"]
    checksum = digest(value)
    if rows:
        if len(rows) != 1 or json.loads(rows[0]["metrics_json"]).get("result_sha256") != checksum:
            raise Refused("not_started registry row differs from the existing sealed hash")
    else:
        experiment_registry.record_trial(EXPERIMENT_ID, "policy", {"stage": "start", **value, "result_sha256": checksum},
                                         "not_started", repo_root=repo_root,
                                         note="Fixed LH003 schedule; cannot restart under this ID")
    return value


def validate_environment(path, *, at, identity, require_cycle=True):
    from .runner import implementation_hashes
    report = verified(path)
    cycle_missing = require_cycle and (report.get("passed") is not True or report.get("synthetic") is not False
            or report.get("cycle", {}).get("real_data") is not True or report.get("cycle", {}).get("completed") is not True)
    if (report.get("experiment_id") != EXPERIMENT_ID or cycle_missing
            or report.get("implementation_hashes") != implementation_hashes(PROJECT_ROOT)
            or report.get("machine_id") != identity
            or report.get("timing", {}).get("fits") is not True
            or report.get("timing", {}).get("real_path_verified") is not True
            or report.get("performance", {}).get("includes_children") is not True
            or report.get("performance", {}).get("fits") is not True
            or report.get("synthetic_format_trial", {}).get("real_call_path") is not True
            or report.get("synthetic_format_trial", {}).get("completed") is not True
            or report.get("restart_resume", {}).get("verified") is not True
            or report.get("sync", {}).get("verified") is not True
            or not report.get("sync", {}).get("transfer_route")
            or aware(report["at"]) > aware(at)):
        raise Refused("section 8 environment verification is incomplete, synthetic, stale in identity, or from the future")
    return report


def seal_holdout(*, data_dir, root, environment_report, snapshot_path=None, repo_root=PROJECT_ROOT,
                 clock=now, session_factory=None, acquire=None, verify_cycle=None):
    """An explicit CLI operation. Tests inject both the session and acquisition."""
    root, repo_root = Path(root), Path(repo_root)
    snapshot_path = Path(snapshot_path) if snapshot_path is not None else root / "holdout_snapshot"
    with ordering.execution_lock(repo_root):
        at = aware(clock())
        registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
        if (root / "not_started.json").exists():
            not_started(root, "recover sealed not_started", repo_root=repo_root, at=at)
            raise Refused("LH003 is not_started and cannot be restarted")
        if (root / "start.json").exists():
            raise Refused("LH003 was already sealed; never reopen its HoldoutSession")
        if claimed(repo_root):
            not_started(root, "claim exists without a completed start seal", repo_root=repo_root, at=at)
            raise Refused("LH003 claim was consumed; never reopen")
        # This first deadline is known even while the full 2027 calendar is blocked.
        first_close = calendar.session_close("2026-11-13")
        try:
            if at >= first_close:
                raise Refused("fixed start was missed; new dates require a new experiment ID")
            calendar.description()
            releases = ordering.lh003_releases(hc003.registry_rows(repo_root), repo_root)
            report = validate_environment(environment_report, at=at, identity=machine_id(), require_cycle=False)
            if verify_cycle is None and (not report.get("sync_manifest_path") or not report.get("restart_evidence_path")):
                raise Refused("environment preflight must name its sync manifest and restart evidence")
            if verify_cycle is None and report.get("state_root") != str(root.resolve()):
                raise Refused("environment format-trial calls must share the LH003 state directory and budget")
            if snapshot_path.exists():
                raise Refused("snapshot path already exists before claim")
            # Inventory is metadata only. No protected bytes are read pre-claim.
            from .snapshot import inventory
            inventory(data_dir)
            from src.runner.trading_calendar import preflight_price_sessions
            preflight_price_sessions(data_dir, "2026-01-01", "2026-06-30")
            # The post-seal measurement also needs its complete 252-session
            # window, including non-H1 history. Check those files before claim.
            cycle_day = pd.Timestamp(calendar.latest_completed_week(at))
            days = calendar.exchange().sessions
            position = days.get_loc(cycle_day)
            window = days[position - 251:position + 1]
            if len(window) != 252:
                raise Refused("environment cycle requires 252 price sessions before claim")
            preflight_price_sessions(data_dir, window[0], window[-1])
        except (ValueError, OSError) as exc:
            if at >= first_close:
                not_started(root, str(exc), repo_root=repo_root, at=at)
            raise
        factory = holdout.HoldoutSession if session_factory is None else session_factory
        acquire = publish_snapshot if acquire is None else acquire
        try:
            with factory(EXPERIMENT_ID, repo_root=repo_root):
                manifest = acquire(data_dir, snapshot_path, at=at)
                snapshot = Snapshot(snapshot_path, expected_hash=digest(manifest))
                sessions = calendar.exchange().sessions
                protected_days = sessions[(sessions >= "2026-01-01") & (sessions <= "2026-06-30")]
                for day in protected_days:
                    relative = inputs.price_path(Path("."), day).as_posix()
                    snapshot.path(relative)
                indices = read_rows_indices(snapshot.path("market_context/benchmarks.jsonl"))
                for series in ("KOSPI", "KOSDAQ"):
                    if set(x.date().isoformat() for x in protected_days) - indices.get(series, set()):
                        raise Refused("protected market-index snapshot has missing sessions: " + series)
                # The real input-cycle measurement needs the newly sealed H1
                # snapshot. Run it within this same claim, before publishing start;
                # an operational preflight alone never starts the experiment.
                if verify_cycle is None:
                    from .environment import verify_environment
                    verify_cycle = verify_environment
                report_path = root / "environment_at_start.json"
                final_report = verify_cycle(data_dir=data_dir, snapshot_path=snapshot_path,
                    sync_manifest=report.get("sync_manifest_path"), restart_evidence=report.get("restart_evidence_path"),
                    timeout=report["timing"]["call_timeout_seconds"], clock=clock, state_root=root)
                seal(report_path, final_report)
                report = validate_environment(report_path, at=clock(), identity=machine_id())
            frozen_calendar = calendar.check(root, at=at)
            if aware(clock()) >= first_close:
                raise Refused("snapshot sealing finished after the fixed start")
            value = {"experiment_id": EXPERIMENT_ID, "at": at.isoformat(), "machine_id": machine_id(),
                     "snapshot_path": str(snapshot_path.resolve()), "snapshot_sha256": digest(manifest),
                     "calendar_sha256": digest(frozen_calendar), "environment_sha256": digest(report),
                     "call_timeout_seconds": report["timing"]["call_timeout_seconds"],
                     "research_ordering": releases, "prereg_commit": registration.commit_hash, "synthetic": False}
            from .runner import implementation_hashes, mechanical_data_hashes
            value.update(implementation_hashes=implementation_hashes(repo_root),
                         mechanical_data_hashes=mechanical_data_hashes(repo_root))
            seal(root / "start.json", value)
            return value
        except BaseException as exc:
            if claimed(repo_root):
                not_started(root, f"post-claim seal failure: {type(exc).__name__}: {exc}", repo_root=repo_root, at=clock())
            raise


def read_rows_indices(path):
    from src.ingestion.storage import read_rows
    rows = {}
    for row in read_rows(path):
        if (row["series"], row["index_name"]) in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
            rows.setdefault(row["series"], set()).add(pd.Timestamp(row["trade_date"]).date().isoformat())
    return rows


def check_machine(root, start, *, at, operator_note):
    changes = sorted((Path(root) / "machine_changes").glob("*.json"))
    previous = verified(changes[-1])["machine_id"] if changes else start["machine_id"]
    current = machine_id()
    if current != previous:
        if not operator_note or not operator_note.strip():
            raise Refused("execution machine changed; explicit operator reason required")
        remaining = sum(aware(row["deadline"]) > aware(at) for row in calendar.plan())
        seal(Path(root) / "machine_changes" / f"{len(changes):03d}.json", {
            "at": aware(at).isoformat(), "previous_machine_id": previous, "machine_id": current,
            "reason": operator_note, "subsequent_decisions": remaining})


def load_week(root, index):
    directory = Path(root) / "weeks" / f"{index:02d}"
    input_path, fallback_path = directory / "input.json", directory / "fallback.json"
    if not input_path.exists() or not fallback_path.exists():
        return None
    prepared, fallback = verified(input_path), verified(fallback_path)
    if fallback["input_sha256"] != digest(prepared):
        raise Refused("fallback differs from sealed A/common inputs")
    start = verified(Path(root) / "start.json")
    if prepared["snapshot_sha256"] != start["snapshot_sha256"]:
        raise Refused("decision input differs from the single start snapshot")
    fallback_receipt = directory / "fallback_receipt.json"
    receipt = verified(fallback_receipt) if fallback_receipt.exists() else None
    if receipt is not None and receipt["fallback_sha256"] != digest(fallback):
        raise Refused("A fallback publication receipt differs from its sealed decision")
    timely = receipt is not None and aware(receipt["publication_finished_at"]) < aware(fallback["deadline"])
    proposal_path, receipt_path = directory / "proposal.json", directory / "proposal_receipt.json"
    if proposal_path.exists() and receipt_path.exists() and timely:
        proposal, receipt = verified(proposal_path), verified(receipt_path)
        if receipt["proposal_sha256"] != digest(proposal) or proposal["input_sha256"] != digest(prepared):
            raise Refused("final decision publication identity differs")
        if proposal.get("model_input_sha256") is not None and digest(verified(directory / "model_input.json")) != proposal["model_input_sha256"]:
            raise Refused("final decision model-input seal differs")
        if aware(receipt["publication_finished_at"]) < aware(fallback["deadline"]):
            return {**proposal, "observation": True, "finalized_at": receipt["publication_finished_at"]}
    reasons = list(fallback["reasons"])
    reasons.append("finalisation_deadline_or_interruption" if timely else "reconstructed_after_deadline")
    return {**fallback, "valid": False, "observation": timely, "reconstructed": not timely,
            "reasons": reasons, "finalized_at": verified(fallback_receipt)["publication_finished_at"] if fallback_receipt.exists() else None}


def run_week(*, data_dir, root, decision_day, sync_manifest, runner, repo_root=PROJECT_ROOT,
             clock=now, operator_note=None, simulation=False, prepared_loader=None):
    root = Path(root)
    if simulation and "data" in root.resolve().parts:
        raise Refused("simulations must never write under data/")
    if runner.fake and not simulation:
        raise Refused("fake calls are restricted to scratch simulations, never official observations")
    with ordering.execution_lock(repo_root):
        if (root / "not_started.json").exists():
            not_started(root, "recover sealed not_started", repo_root=repo_root, at=clock())
            raise Refused("LH003 is not_started")
        if not (root / "start.json").exists():
            if aware(clock()) >= calendar.session_close("2026-11-13") and not simulation:
                not_started(root, "no successful pre-start seal/environment gates", repo_root=repo_root, at=clock())
            raise Refused("LH003 has no successful start seal")
        start = verified(root / "start.json")
        if start["synthetic"] and not simulation:
            raise Refused("synthetic start cannot produce experiment observations")
        if not simulation and runner.timeout != start["call_timeout_seconds"]:
            raise Refused("call timeout differs from the verified worst-case deadline budget")
        if not simulation and experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root).commit_hash != start["prereg_commit"]:
            raise Refused("preregistration differs from the immutable start record")
        check_machine(root, start, at=clock(), operator_note=operator_note)
        current = calendar.check(root, at=clock(), operator_note=operator_note)
        matches = [row for row in current["decisions"] if row["decision_date"] == decision_day]
        if len(matches) != 1:
            raise Refused("decision day is outside the 52 fixed XKRX weeks")
        schedule = matches[0]
        directory = root / "weeks" / f"{schedule['index']:02d}"
        if (directory / "proposal_receipt.json").exists():
            return load_week(root, schedule["index"])
        input_path = directory / "input.json"
        if input_path.exists():
            prepared = verified(input_path)
            if prepared["schedule"] != schedule:
                raise Refused("calendar update conflicts with an already sealed decision; preserve it for audit")
        else:
            trigger = inputs.verify_day(data_dir, decision_day, at=clock(), sync_manifest=sync_manifest)
            snapshot = Snapshot(start["snapshot_path"], expected_hash=start["snapshot_sha256"], allow_synthetic=simulation)
            loader = inputs.prepare if prepared_loader is None else prepared_loader
            try:
                prepared = {**loader(data_dir, snapshot, decision_day), "schedule": schedule,
                            "trigger": trigger, "synthetic": simulation, "sealed_at": aware(clock()).isoformat(),
                            "calendar_sha256": digest(current)}
                seal(input_path, prepared)
            except (ValueError, OSError) as exc:
                failure = {"at": aware(clock()).isoformat(), "reason": str(exc), "a_reproducible": False}
                seal(directory / "input_failures" / f"{digest(failure)}.json", failure)
                raise
        bundle = prepared["bundle"]
        fallback_path = directory / "fallback.json"
        if not fallback_path.exists():
            fallback = {**schedule, "input_sha256": digest(prepared), "a_codes": bundle["a_codes"],
                        "candidate_codes": [s["stock_code"] for s in sorted(
                            (s for s in bundle["stocks"] if s["stock_code"] in set(bundle["a_codes"])),
                            key=lambda s: (-s["adv_20"], s["stock_code"]))[:300]],
                        "universe_codes": [s["stock_code"] for s in bundle["stocks"]],
                        "policy_codes": bundle["a_codes"], "valid": False, "avoid": [], "reasons": ["sealed_A_fallback"],
                        "synthetic": simulation, "reconstructed": False, "repeats": []}
            seal(fallback_path, fallback)
        if not (directory / "fallback_receipt.json").exists():
            seal(directory / "fallback_receipt.json", {"publication_finished_at": aware(clock()).isoformat(),
                                                       "fallback_sha256": digest(verified(fallback_path))})
        if aware(clock()) >= aware(schedule["deadline"]):
            return load_week(root, schedule["index"])
        try:
            model_path = directory / "model_input.json"
            if model_path.exists():
                payload = verified(model_path)
                if payload["prompt_files"] != inputs.prompt_hashes(runner.prompt_dir):
                    raise Refused(runner.stop("prompt/schema drift after input seal"))
            else:
                payload = inputs.render(bundle, runner.prompt_dir)
                seal(model_path, payload)
            selection = runner.decide(payload, week=schedule["index"], deadline=schedule["deadline"])
        except (ValueError, AssertionError, OSError) as exc:
            selection = {"valid": False, "reasons": [type(exc).__name__ + ": " + str(exc)], "repeats": [], "avoid": []}
        fallback = verified(fallback_path)
        proposal = {**fallback, **selection,
                    "policy_codes": selection["selected"] if selection["valid"] else bundle["a_codes"],
                    "model_input_sha256": digest(payload) if "payload" in locals() else None}
        if aware(clock()) >= aware(schedule["deadline"]):
            proposal.update(reconstructed=True, valid=False, llm_output_valid=selection["valid"], policy_codes=bundle["a_codes"])
        seal(directory / "proposal.json", proposal)
        # The proposal is the atomically published decision; the receipt measures
        # completion after its fsync. A late/missing receipt can only select A.
        seal(directory / "proposal_receipt.json", {"proposal_sha256": digest(proposal),
                                                  "publication_finished_at": aware(clock()).isoformat()})
        return load_week(root, schedule["index"])
