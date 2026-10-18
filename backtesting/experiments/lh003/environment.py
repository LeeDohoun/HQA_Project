"""Section 8 gates, with real synthetic-format calls only on explicit opt-in."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pandas as pd

from backtesting.experiment_registry import PROJECT_ROOT

from . import EXPERIMENT_ID, Refused
from .calendar import description, exchange, latest_completed_week, session_close
from .inputs import price_path, verify_day, prepare, render
from .runner import LlmRunner, implementation_hashes, code_pins
from .distribution import latest as latest_distribution
from .resources import ProcessTreeMemory
from .simulation import fake_cycle, synthetic_archive, synthetic_distribution
from .snapshot import Snapshot, publish_snapshot
from .storage import attempts, aware, digest, file_hash, now, read, seal, verified
from .workflow import machine_id


def sync_check(data_dir, manifest, *, at, day):
    if manifest is None:
        return {"verified": False, "reason": "collector-to-research hash manifest not supplied"}
    try:
        receipt = verify_day(data_dir, day, at=at, sync_manifest=manifest)
        return {"verified": True, **receipt, "transfer_route": read(manifest).get("transfer_route")}
    except (ValueError, OSError, KeyError) as exc:
        return {"verified": False, "reason": str(exc)}


def restart_check(evidence):
    plan = ["Enable a research-account systemd service with Restart=on-failure and a persistent timer.",
            "Run the timer on weekends/holidays; trigger only after decision-day sync hash verification.",
            "Kill after attempt reservation; restart the same state directory and confirm it counts as interrupted.",
            "Reboot, suspend/resume and close the login session; record automatic resumption for each.",
            "Confirm unchanged input/A hashes, no duplicate decisions and no reset of the attempt counter."]
    if evidence is None:
        return {"verified": False, "plan": plan, "reason": "real reboot/suspend/logout evidence not supplied"}
    value = read(evidence)
    checks = ("reboot", "suspend_resume", "session_exit", "automatic_resume", "same_input_hash",
              "same_A_hash", "attempts_preserved", "no_duplicate_decisions", "weekend_holiday_sync")
    return {"verified": all(value.get(key) is True for key in checks) and bool(value.get("service_unit")),
            "checks": {key: value.get(key) for key in checks}, "service_unit": value.get("service_unit"),
            "evidence_sha256": file_hash(evidence), "plan": plan}


def synthetic_format_trial(*, root, day, timeout, clock=now, subprocess_call=None):
    """Six real-path attempts, synthetic inputs only, sharing the durable 320 cap."""
    root = Path(root)
    path = root / "synthetic_format_trial.json"
    identity = {"machine_id": machine_id(), "implementation_hashes": implementation_hashes(PROJECT_ROOT),
                "call_timeout_seconds": timeout, "fake": subprocess_call is not None}
    if path.exists():
        result = verified(path)
        if result["identity"] != identity:
            raise Refused("synthetic-format measurement identity changed")
        return result
    if any(a.get("synthetic_format") for a in attempts(root)):
        raise Refused("incomplete synthetic-format measurement; cannot reset or reuse unmeasured calls")
    started = time.perf_counter()
    with ProcessTreeMemory() as memory, tempfile.TemporaryDirectory(prefix="lh003-format-", dir="/tmp") as directory:
        scratch = Path(directory)
        archive = synthetic_archive(scratch / "source", day=day, stocks=40)
        manifest = publish_snapshot(archive, scratch / "snapshot", at=clock(), synthetic=True)
        snapshot = Snapshot(scratch / "snapshot", expected_hash=digest(manifest), allow_synthetic=True)
        snapshot.distribution, _ = synthetic_distribution(archive, scratch / "distributions", at=clock())
        payload = render(prepare(archive, snapshot, day)["bundle"])
        runner = LlmRunner(root, timeout=timeout, clock=clock, subprocess_call=subprocess_call, format_trial=True)
        deadline = aware(clock()) + pd.Timedelta(seconds=6 * (timeout + 20) + 60)
        calls_started = time.perf_counter()
        calls = [runner.call(payload, repeat, week="synthetic_format_trial", deadline=deadline.isoformat()) for repeat in range(6)]
        six_call_seconds = time.perf_counter() - calls_started
    reservations = [a for a in attempts(root) if a.get("synthetic_format")]
    results = [verified(root / "attempts" / f"{a['number']:06d}.result.json") for a in reservations]
    result = {"identity": identity, "at": aware(clock()).isoformat(), "synthetic_inputs": True,
              "real_call_path": not runner.fake, "completed": len(results) == 6 and all(c["valid"] for c in calls),
              "attempts": len(results), "six_call_seconds": six_call_seconds,
              "total_seconds": time.perf_counter() - started, "memory": memory.report(),
              "attempt_numbers": [a["number"] for a in reservations], "payload_sha256": digest(payload)}
    seal(path, result)
    return result


def verify_environment(*, data_dir, report_path=None, snapshot_path=None, synthetic=False,
                       sync_manifest=None, restart_evidence=None, timeout=300, clock=now,
                       real_format_trial=False, state_root=None, subprocess_call=None, distribution_dir=None):
    with ProcessTreeMemory() as memory:
        result = _verify_environment(data_dir=data_dir, snapshot_path=snapshot_path, synthetic=synthetic,
            sync_manifest=sync_manifest, restart_evidence=restart_evidence, timeout=timeout, clock=clock,
            real_format_trial=real_format_trial, state_root=state_root, subprocess_call=subprocess_call,
            distribution_dir=distribution_dir)
    performance = result["performance"]
    trial = result["synthetic_format_trial"]
    performance.update(memory.report())
    # When the real-input cycle reuses the pre-claim format measurement, this
    # bound also covers its real parent footprint plus measured CLI descendants.
    performance["required_capacity_mib"] = max(memory.peak_mib,
        memory.parent_peak_mib + trial.get("memory", {}).get("children_peak_rss_mib", 0))
    performance["limit_mib"] = 1536
    performance["fits"] = (performance["verified"] and trial.get("memory", {}).get("verified") is True
        and performance["required_capacity_mib"] < performance["limit_mib"]
        and trial["memory"]["peak_rss_mib"] < performance["limit_mib"])
    result["passed"] = bool(result["passed"] and performance["fits"])
    if report_path is not None:
        seal(report_path, result)
    return result


def _verify_environment(*, data_dir, snapshot_path, synthetic, sync_manifest, restart_evidence, timeout, clock,
                        real_format_trial, state_root, subprocess_call, distribution_dir):
    started, at = time.perf_counter(), aware(clock())
    # Check code before any A inputs, including environment measurements.
    applied = code_pins(Path(state_root) if state_root is not None else Path(data_dir) / "research/lh003",
                        PROJECT_ROOT, at=at)
    day = latest_completed_week(at)
    files = sorted(p for p in (Path(data_dir) / "market/krx_daily").glob("*/*.jsonl") if p.stem.isdigit())
    try:
        calendar = {"verified": True, **description()}
    except ValueError as exc:
        calendar = {"verified": False, "reason": str(exc), "decisions": []}
    result = {"experiment_id": EXPERIMENT_ID, "at": at.isoformat(), "machine_id": machine_id(),
              "synthetic": synthetic, "latest_completed_week": day, "data_dir": str(Path(data_dir).resolve()),
              "implementation_hashes": implementation_hashes(PROJECT_ROOT),
              "code_pins": applied,
              "archive_latest_day": pd.Timestamp(files[-1].stem).date().isoformat() if files else None,
              "calendar": calendar, "sync": {"verified": False, "reason": "completed distribution required"},
              "restart_resume": restart_check(restart_evidence), "real_model_calls": 0,
              "sync_manifest_path": str(Path(sync_manifest).resolve()) if sync_manifest is not None else None,
              "restart_evidence_path": str(Path(restart_evidence).resolve()) if restart_evidence is not None else None,
              "synthetic_format_budget_spent": 0, "holdout_opened": False}
    publication = None
    try:
        publication, receipt = latest_distribution(distribution_dir if distribution_dir is not None
            else Path(state_root) / "distributions" if state_root is not None else Path(data_dir) / "research/lh003/distributions",
            sync_manifest=sync_manifest, at=at)
        result["distribution"] = {"verified": True, **receipt}
        result["sync"] = sync_check(publication.root, sync_manifest, at=at, day=day)
    except (ValueError, OSError) as exc:
        result["distribution"] = {"verified": False, "reason": str(exc)}
    trial_path = Path(state_root) / "synthetic_format_trial.json" if state_root is not None else None
    if real_format_trial:
        if state_root is None:
            raise Refused("explicit real synthetic-format trial requires a durable --state-dir")
        before_calls = len(attempts(state_root))
        trial = synthetic_format_trial(root=state_root, day=day, timeout=timeout, clock=clock,
                                       subprocess_call=subprocess_call)
        result["real_model_calls"] = len(attempts(state_root)) - before_calls if trial["real_call_path"] else 0
    elif trial_path is not None and trial_path.exists():
        trial = verified(trial_path)
    else:
        trial = {"completed": False, "real_call_path": False, "reason": "explicit --real-format-trial not performed"}
    result["synthetic_format_trial"] = trial
    result["synthetic_format_budget_spent"] = trial.get("attempts", 0)
    result["state_root"] = str(Path(state_root).resolve()) if state_root is not None else None
    identity = trial.get("identity", {})
    trial_hashes = identity.get("implementation_hashes", {})
    # A later data-only amendment leaves the measured implementation identity
    # valid. Verify its original hashes against the same sealed history.
    if state_root is not None and (Path(state_root) / "start.json").exists() and trial.get("at"):
        from .pins import verify as verify_history
        measured = verify_history(state_root, PROJECT_ROOT, at=trial["at"], current={}, historical=True)
        identity_matches = trial_hashes == measured["implementation_hashes"]
    else:
        identity_matches = trial_hashes == result["implementation_hashes"]
    trial_verified = (trial.get("completed") is True and trial.get("real_call_path") is True
        and trial.get("attempts") == 6 and trial.get("synthetic_inputs") is True
        and identity.get("machine_id") == result["machine_id"]
        and identity_matches
        and identity.get("call_timeout_seconds") == timeout and identity.get("fake") is False
        and aware(trial["at"]) <= aware(clock()) and trial.get("memory", {}).get("verified") is True)
    cycle_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="lh003-synthetic-" if synthetic else "lh003-dry-", dir="/tmp") as temporary:
        scratch = Path(temporary)
        if synthetic:
            archive = synthetic_archive(scratch / "source", day=day)
            manifest = publish_snapshot(archive, scratch / "synthetic_snapshot", at=at, synthetic=True)
            snapshot = Snapshot(scratch / "synthetic_snapshot", expected_hash=digest(manifest), allow_synthetic=True)
            snapshot.distribution, _ = synthetic_distribution(archive, scratch / "distributions", at=at)
            # A fake historical clock exercises the deadline logic; it is not an observation.
            fake_at = session_close(day) + pd.Timedelta(minutes=1)
            cycle = fake_cycle(data_dir=archive, snapshot=snapshot, day=day, scratch=scratch / "cycle",
                               at=fake_at.to_pydatetime(), timeout=timeout)
            result["cycle"] = {**cycle, "real_data": False, "label": "SYNTHETIC archive + SYNTHETIC H1 snapshot + fake process"}
            result["real_data_cycle"] = {"completed": False, "reason": (
                "decision-day KRX data missing: " + day if not price_path(data_dir, day).is_file()
                else "synthetic verification explicitly selected; real data cycle not measured")}
        elif not price_path(data_dir, day).is_file():
            result["cycle"] = {"completed": False, "real_data": False,
                               "reason": "decision-day KRX data missing: " + day}
        elif snapshot_path is None:
            result["cycle"] = {"completed": False, "real_data": False,
                "reason": "verified LH003 snapshot not supplied; no protected source reads and no session opened"}
        elif publication is None:
            result["cycle"] = {"completed": False, "real_data": False, "reason": result["distribution"]["reason"]}
        elif not result["sync"]["verified"]:
            result["cycle"] = {"completed": False, "real_data": False, "reason": result["sync"]["reason"]}
        else:
            manifest = verified(Path(snapshot_path) / "manifest.json")
            snapshot = Snapshot(snapshot_path, expected_hash=digest(manifest), distribution=publication)
            # Model calls are faked; the input generation and source checks are real.
            fake_at = session_close(day) + pd.Timedelta(minutes=1)
            cycle = fake_cycle(data_dir=data_dir, snapshot=snapshot, day=day, scratch=scratch / "cycle",
                               at=fake_at.to_pydatetime(), timeout=timeout)
            result["cycle"] = {**cycle, "real_data": True, "label": "read-only real archives, fake process"}
    cycle_seconds = time.perf_counter() - cycle_started
    deadline = pd.Timestamp(day)
    next_day = exchange().sessions[exchange().sessions.get_loc(deadline) + 1]
    finalisation = next_day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=8, minutes=59)
    trigger = aware(result["sync"]["verified_at"]) if result["sync"]["verified"] else session_close(day)
    # Two identity checks per attempted call are each capped at ten seconds.
    worst = cycle_seconds + max(trial.get("six_call_seconds", 0), 6 * (timeout + 2 * min(timeout, 10)))
    available = (finalisation.to_pydatetime() - trigger).total_seconds()
    scheduled_windows = [(aware(row["deadline"]) - session_close(row["decision_date"])).total_seconds()
                         for row in result["calendar"]["decisions"]]
    result["timing"] = {"call_timeout_seconds": timeout, "attempts": 6, "identity_checks_per_attempt": 2,
                        "worst_case_seconds": worst, "available_seconds": available,
                        "trigger_at": trigger.isoformat(), "deadline": finalisation.isoformat(),
                        "trigger_measured": result["sync"]["verified"],
                        "six_call_seconds": trial.get("six_call_seconds"), "real_path_verified": trial_verified,
                        "minimum_scheduled_window_seconds": min(scheduled_windows) if scheduled_windows else None,
                        "fits": trial_verified and calendar["verified"] and worst < min(available, *scheduled_windows)}
    result["performance"] = {"runtime_seconds": time.perf_counter() - started, "cycle_seconds": cycle_seconds,
        "cycle_measured": result["cycle"]["completed"], "includes_synthetic_fixture_generation": synthetic}
    result["passed"] = bool(not synthetic and result["cycle"]["completed"] and result["cycle"]["real_data"]
                            and result["timing"]["fits"] and result["sync"]["verified"]
                            and result["restart_resume"]["verified"] and result["sync"]["transfer_route"])
    result["at"] = aware(clock()).isoformat()
    return result
