"""Section 8 reports: measured fake cycles and explicit unverified operations."""
from __future__ import annotations

import resource
import tempfile
import time
from pathlib import Path

import pandas as pd

from backtesting.experiment_registry import PROJECT_ROOT

from . import EXPERIMENT_ID
from .calendar import description, exchange, latest_completed_week
from .inputs import price_path, verify_day
from .runner import implementation_hashes
from .simulation import fake_cycle, synthetic_archive
from .snapshot import Snapshot, publish_snapshot
from .storage import aware, digest, file_hash, now, read, seal
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


def verify_environment(*, data_dir, report_path=None, snapshot_path=None, synthetic=False,
                       sync_manifest=None, restart_evidence=None, timeout=300, clock=now):
    started, at = time.perf_counter(), aware(clock())
    day = latest_completed_week(at)
    files = sorted(p for p in (Path(data_dir) / "market/krx_daily").glob("*/*.jsonl") if p.stem.isdigit())
    result = {"experiment_id": EXPERIMENT_ID, "at": at.isoformat(), "machine_id": machine_id(),
              "synthetic": synthetic, "latest_completed_week": day, "data_dir": str(Path(data_dir).resolve()),
              "implementation_hashes": implementation_hashes(PROJECT_ROOT),
              "archive_latest_day": pd.Timestamp(files[-1].stem).date().isoformat() if files else None,
              "calendar": description(), "sync": sync_check(data_dir, sync_manifest, at=at, day=day),
              "restart_resume": restart_check(restart_evidence), "real_model_calls": 0,
              "sync_manifest_path": str(Path(sync_manifest).resolve()) if sync_manifest is not None else None,
              "restart_evidence_path": str(Path(restart_evidence).resolve()) if restart_evidence is not None else None,
              "synthetic_format_budget_spent": 0, "holdout_opened": False}
    cycle_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="lh003-synthetic-" if synthetic else "lh003-dry-", dir="/tmp") as temporary:
        scratch = Path(temporary)
        if synthetic:
            archive = synthetic_archive(scratch / "source", day=day)
            manifest = publish_snapshot(archive, scratch / "synthetic_snapshot", at=at, synthetic=True)
            snapshot = Snapshot(scratch / "synthetic_snapshot", expected_hash=digest(manifest), allow_synthetic=True)
            # A fake historical clock exercises the deadline logic; it is not an observation.
            fake_at = exchange().session_close(day) + pd.Timedelta(minutes=1)
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
        elif not result["sync"]["verified"]:
            result["cycle"] = {"completed": False, "real_data": False, "reason": result["sync"]["reason"]}
        else:
            from .storage import verified
            manifest = verified(Path(snapshot_path) / "manifest.json")
            snapshot = Snapshot(snapshot_path, expected_hash=digest(manifest))
            # Model calls are faked; the input generation and source checks are real.
            fake_at = exchange().session_close(day) + pd.Timedelta(minutes=1)
            cycle = fake_cycle(data_dir=data_dir, snapshot=snapshot, day=day, scratch=scratch / "cycle",
                               at=fake_at.to_pydatetime(), timeout=timeout)
            result["cycle"] = {**cycle, "real_data": True, "label": "read-only real archives, fake process"}
    cycle_seconds = time.perf_counter() - cycle_started
    deadline = pd.Timestamp(day)
    next_day = exchange().sessions[exchange().sessions.get_loc(deadline) + 1]
    finalisation = next_day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=8, minutes=59)
    trigger = aware(result["sync"]["verified_at"]) if result["sync"]["verified"] else exchange().session_close(day).to_pydatetime()
    # Two identity checks per attempted call are each capped at ten seconds.
    worst = cycle_seconds + 6 * (timeout + 2 * min(timeout, 10))
    available = (finalisation.to_pydatetime() - trigger).total_seconds()
    scheduled_windows = [(aware(row["deadline"]) - exchange().session_close(row["decision_date"]).to_pydatetime()).total_seconds()
                         for row in result["calendar"]["decisions"]]
    result["timing"] = {"call_timeout_seconds": timeout, "attempts": 6, "identity_checks_per_attempt": 2,
                        "worst_case_seconds": worst, "available_seconds": available,
                        "trigger_at": trigger.isoformat(), "deadline": finalisation.isoformat(),
                        "trigger_measured": result["sync"]["verified"],
                        "minimum_scheduled_window_seconds": min(scheduled_windows),
                        "fits": worst < min(available, *scheduled_windows)}
    result["performance"] = {"runtime_seconds": time.perf_counter() - started, "cycle_seconds": cycle_seconds,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "cycle_measured": result["cycle"]["completed"], "includes_synthetic_fixture_generation": synthetic}
    result["passed"] = bool(not synthetic and result["cycle"]["completed"] and result["cycle"]["real_data"]
                            and result["timing"]["fits"] and result["sync"]["verified"]
                            and result["restart_resume"]["verified"] and result["sync"]["transfer_route"])
    if report_path is not None:
        seal(report_path, result)
    return result
