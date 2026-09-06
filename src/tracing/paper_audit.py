"""Append-only local records for prospective PAPER evaluation."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PaperAudit:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS paper_events (id INTEGER PRIMARY KEY, "
                       "recorded_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, kind: str, payload: dict) -> int:
        if kind not in {"analysis", "monitor", "llm_request", "llm_response", "llm_failure",
                        "benchmark_context", "corporate_action_context"}:
            raise ValueError("Unknown PAPER audit event kind")
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._connect() as db:
            cursor = db.execute("INSERT INTO paper_events(recorded_at,kind,payload) VALUES(?,?,?)",
                                (datetime.now(timezone.utc).isoformat(), kind, body))
            return cursor.lastrowid

    def read(self, kind: str | None = None) -> list[dict]:
        with self._connect() as db:
            query = "SELECT id,recorded_at,kind,payload FROM paper_events"
            rows = db.execute(query + (" WHERE kind=?" if kind else "") + " ORDER BY id",
                              (kind,) if kind else ()).fetchall()
        return [{"id": row[0], "recorded_at": row[1], "kind": row[2], "payload": json.loads(row[3])} for row in rows]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summarize_runtime(events: list[dict]) -> dict:
    analyses = [event["payload"] for event in events if event["kind"] == "analysis"]
    monitors = [event["payload"] for event in events if event["kind"] == "monitor"]
    latencies = [row["timings_ms"]["total"] / 1000 for row in analyses]
    accounts = [account for row in analyses for account in row["accounts"].values()]
    selected = sum(row["specialist_stock_count"] for row in analyses)
    completed = sum(row["completed_stock_count"] for row in analyses)
    requests = [event for event in events if event["kind"] == "llm_request"]
    responses = [event for event in events if event["kind"] == "llm_response"]
    failures = [event for event in events if event["kind"] == "llm_failure"]
    return {"analysis_cycles": len(analyses), "account_reviews": len(accounts),
            "valid_account_rate": sum(row["status"] == "completed" for row in accounts) / len(accounts) if accounts else None,
            "selected_stock_analyses": selected, "completed_stock_analyses": completed,
            "stock_completion_rate": completed / selected if selected else None,
            "analysis_p50_seconds": percentile(latencies, .5), "analysis_p95_seconds": percentile(latencies, .95),
            "analysis_over_120_seconds": sum(value > 120 for value in latencies),
            "llm_requests": len(requests), "llm_schema_valid_responses": len(responses), "llm_failures": len(failures),
            "holding_priority_requests": sum(event["payload"].get("holding_priority") is True for event in requests),
            "budget_rejections": sum(event["payload"].get("error_type") == "LLMBudgetExceeded" for event in failures),
            "monitor_polls": len(monitors), "monitor_slo_failures": sum(not row["slo_met"] for row in monitors),
            "monitor_errors": sum(len(row["errors"]) for row in monitors),
            "monitor_p95_seconds": percentile([row["elapsed_seconds"] for row in monitors], .95),
            "investment_performance": "Requires observed fills and cost-adjusted equity; latency is not trading performance."}
