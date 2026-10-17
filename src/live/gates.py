"""Allowed stage from the experiment registry, the shadow ledger and human approvals.

effective = min(requested, allowed). Every missing or garbled input fails closed
with a machine-readable reason; nothing is inferred in a strategy's favour.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import book, shadow

REGISTRY_PATH = Path("research/experiments/registry.csv")
REGISTRY_COLUMNS = ("recorded_at", "experiment_id", "prereg_commit", "variant", "verdict", "metrics_json", "note")
MIN_SHADOW_SESSIONS = 20
MIN_SHADOW_CALENDAR_DAYS = 28


@dataclass(frozen=True)
class StageDecision:
    strategy_id: str
    requested: str
    allowed: str
    effective: str
    reasons: tuple[str, ...]
    evidence: dict = field(default_factory=dict, compare=False)


def _stamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError(f"registry recorded_at lacks a timezone: {value}")
    return stamp


def read_registry(path) -> list[dict]:
    """Every registry row; raises ValueError on a missing or garbled file."""
    csv.field_size_limit(sys.maxsize)
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"registry missing: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if tuple(reader.fieldnames or ()) != REGISTRY_COLUMNS:
            raise ValueError("registry columns differ from the expected header")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"registry row {reader.line_num} has the wrong number of fields")
            _stamp(row["recorded_at"])
            rows.append(row)
    return rows


def latest_row(rows, experiment_ids, variants) -> dict | None:
    wanted = {(e, v) for e in experiment_ids for v in variants}
    found = [row for row in rows if (row["experiment_id"], row["variant"]) in wanted]
    return max(found, key=lambda row: _stamp(row["recorded_at"])) if found else None


def shadow_run(ledger: list[dict]) -> list[dict]:
    """Trailing run of shadow-mode entries on consecutive XKRX sessions."""
    run: list[dict] = []
    for entry in reversed(ledger):
        if entry["mode"] != "shadow":
            break
        if run:
            between = shadow.sessions_between(entry["date"], run[-1]["date"])
            if between != [entry["date"], run[-1]["date"]]:
                break
        run.append(entry)
    return list(reversed(run))


def shadow_evidence(ledger: list[dict], extra_incidents: list[dict] = ()) -> dict:
    run = shadow_run(ledger)
    first = run[0]["date"] if run else None
    last = run[-1]["date"] if run else None
    days = (date.fromisoformat(last) - date.fromisoformat(first)).days if run else 0
    incidents = sum(len(e.get("incidents") or []) for e in run)
    incidents += sum(1 for item in extra_incidents if first and first <= item.get("date", "") <= last)
    mismatches = sum(1 for e in run if not ((e.get("match") or {}).get("targets_match") is True
                                           and (e.get("match") or {}).get("fill_prices_match") is True))
    traded = [e for e in run if e.get("decision_hashes") or any(o.get("filled") for o in e.get("orders") or [])]
    checks = [(e.get("match") or {}).get("independent_check", "unavailable") for e in traded]
    checks = [c for c in checks if c != "not_applicable"]
    return {"sessions": len(run), "first_date": first, "last_date": last, "calendar_days": days,
            "incidents": incidents, "mismatched_entries": mismatches,
            "independent_failed": sum(1 for c in checks if c is False),
            "independent_unavailable": sum(1 for c in checks if c is not True and c is not False)}


def compute_stage(entry: book.StrategyEntry, *, registry_rows=None, registry_error=None, ledger=None,
                  ledger_error=None, extra_incidents=(), approvals=None, approvals_error=None) -> StageDecision:
    """Pure stage computation from already-loaded inputs (None + error -> fail closed)."""
    reasons: list[str] = []
    evidence: dict = {"design": None, "holdout": None, "shadow": None, "approval": None}
    shadow_ok = True
    if registry_rows is None:
        reasons.append(f"registry_unreadable:{registry_error or 'missing'}")
        shadow_ok = False
    else:
        design = latest_row(registry_rows, [entry.experiment_id], [entry.variant])
        evidence["design"] = None if design is None else {
            "experiment_id": design["experiment_id"], "variant": design["variant"],
            "verdict": design["verdict"], "recorded_at": design["recorded_at"]}
        if design is None or design["verdict"] != "pass":
            reasons.append(f"design_not_passed:{entry.experiment_id}/{entry.variant}:"
                           f"{design['verdict'] if design else 'none'}")
            shadow_ok = False
        holdout = latest_row(registry_rows, entry.holdout_experiment_ids, entry.holdout_variants)
        evidence["holdout"] = None if holdout is None else {
            "experiment_id": holdout["experiment_id"], "variant": holdout["variant"],
            "verdict": holdout["verdict"], "recorded_at": holdout["recorded_at"]}
        if holdout is None or holdout["verdict"] != "pass":
            name = (f"{holdout['experiment_id']}/{holdout['variant']}" if holdout else
                    f"{entry.holdout_experiment_ids[0]}/{entry.holdout_variants[0]}")
            reasons.append(f"holdout_not_passed:{name}:{holdout['verdict'] if holdout else 'none'}")
            shadow_ok = False

    paper_ok = shadow_ok
    paper_reasons: list[str] = []
    if ledger is None:
        paper_reasons.append(f"shadow_ledger_unreadable:{ledger_error or 'missing'}")
        paper_ok = False
    else:
        try:
            stats = shadow_evidence(ledger, list(extra_incidents))
        except ValueError as error:
            paper_reasons.append(f"shadow_ledger_unverifiable:{error}")
            paper_ok = False
        else:
            evidence["shadow"] = stats
            if stats["sessions"] < MIN_SHADOW_SESSIONS:
                paper_reasons.append(f"shadow_sessions_insufficient:{stats['sessions']}/{MIN_SHADOW_SESSIONS}")
                paper_ok = False
            if stats["calendar_days"] < MIN_SHADOW_CALENDAR_DAYS:
                paper_reasons.append(
                    f"shadow_calendar_days_insufficient:{stats['calendar_days']}/{MIN_SHADOW_CALENDAR_DAYS}")
                paper_ok = False
            if stats["incidents"]:
                paper_reasons.append(f"shadow_incidents:{stats['incidents']}")
                paper_ok = False
            if stats["mismatched_entries"]:
                paper_reasons.append(f"shadow_signal_mismatch:{stats['mismatched_entries']}")
                paper_ok = False
            if stats["independent_failed"]:
                paper_reasons.append(f"shadow_independent_check_failed:{stats['independent_failed']}")
                paper_ok = False
            if stats["independent_unavailable"]:
                paper_reasons.append(f"shadow_independent_check_unavailable:{stats['independent_unavailable']}")
                paper_ok = False
    if approvals is None:
        paper_reasons.append(f"approvals_unreadable:{approvals_error or 'missing'}")
        paper_ok = False
    else:
        approval = next((a for a in approvals if a.strategy_id == entry.strategy_id and a.stage == "paper"), None)
        evidence["approval"] = None if approval is None else {
            "approved_by": approval.approved_by, "approved_at": approval.approved_at.isoformat(),
            "evidence": approval.evidence}
        if approval is None:
            paper_reasons.append("paper_approval_missing")
            paper_ok = False

    allowed = "paper" if paper_ok else "shadow" if shadow_ok else "observe"
    requested = entry.requested_stage
    effective = book.STAGES[min(book.stage_rank(requested), book.stage_rank(allowed))]
    blocking = []
    if book.stage_rank(effective) < book.stage_rank(requested):
        if not shadow_ok:
            blocking.extend(reasons)
        if requested == "paper":
            blocking.extend(paper_reasons)
    return StageDecision(entry.strategy_id, requested, allowed, effective, tuple(blocking), evidence)


def evaluate_stage(entry: book.StrategyEntry, *, repo_root, data_dir, approvals=None,
                   approvals_error=None) -> StageDecision:
    """Load the registry, ledger and approvals; any failure fails closed."""
    registry_rows = registry_error = None
    try:
        registry_rows = read_registry(Path(repo_root) / REGISTRY_PATH)
    except (OSError, ValueError, csv.Error) as error:
        registry_error = str(error)
    ledger = ledger_error = None
    extra = []
    try:
        ledger = shadow.read_ledger(data_dir, entry.strategy_id)
        extra = shadow.read_incidents(data_dir, entry.strategy_id)
    except (OSError, ValueError) as error:
        ledger, ledger_error = None, str(error)
    if approvals is None and approvals_error is None:
        try:
            approvals = book.load_approvals(Path(repo_root) / book.APPROVALS_PATH)
        except (OSError, ValueError) as error:
            approvals_error = str(error)
    return compute_stage(entry, registry_rows=registry_rows, registry_error=registry_error, ledger=ledger,
                         ledger_error=ledger_error, extra_incidents=extra, approvals=approvals,
                         approvals_error=approvals_error)
