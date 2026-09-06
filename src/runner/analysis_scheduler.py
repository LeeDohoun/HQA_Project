from __future__ import annotations

import os
import time
import argparse
import logging
import threading
import uuid
from datetime import datetime, time as wall_time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests
import yaml

from src.runner.trade_signal_submitter import submit_trade_signals

logger = logging.getLogger(__name__)


class BackendAutoTradeTargetClient:
    def __init__(self, base_url: Optional[str] = None, internal_token: Optional[str] = None, timeout: int = 10):
        resolved_url = base_url or os.getenv("BACKEND_INTERNAL_BASE_URL") or os.getenv("BACKEND_BASE_URL")
        if not resolved_url:
            raise ValueError("BACKEND_INTERNAL_BASE_URL is required")
        self.base_url = resolved_url.rstrip("/")
        self.internal_token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "")
        self.timeout = timeout

    def fetch_targets(self) -> List[Dict[str, Any]]:
        response = requests.get(
            f"{self.base_url}/api/v1/internal/trading/auto-trade-targets",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload["targets"]
        if not isinstance(rows, list) or any(not isinstance(item, dict) or not item.get("userId") for item in rows):
            raise ValueError("invalid backend auto-trade target response")
        return rows

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.internal_token:
            headers["X-HQA-Internal-Token"] = self.internal_token
        return headers


class AnalysisScheduler:
    def __init__(
        self,
        *,
        backend_client: Any,
        runner: Any = None,
        analysis_service: Any = None,
        submitter: Callable[..., Dict[str, Any]] = submit_trade_signals,
        interval_seconds: int = 900,
        candidate_limit: int = 5,
        per_theme_top_n: int = 3,
        top_n: int = 5,
        min_leader_score: Optional[int] = None,
        min_confidence: Optional[int] = None,
        max_risk_level: Optional[str] = None,
        market_hours_only: Optional[bool] = None,
    ):
        self.backend_client = backend_client
        self.runner = runner
        self.analysis_service = analysis_service
        if runner is None and analysis_service is None:
            from src.runner.shared_analysis import get_runtime_analysis_service
            self.analysis_service = get_runtime_analysis_service()
        self.submitter = submitter
        self.interval_seconds = interval_seconds
        self.candidate_limit = candidate_limit
        self.per_theme_top_n = per_theme_top_n
        self.top_n = top_n
        self.min_leader_score = min_leader_score
        self.min_confidence = min_confidence
        self.max_risk_level = max_risk_level
        schedule = getattr(getattr(self.analysis_service, "data", None), "schedule", {})
        self.market_hours_only = bool(schedule.get("market_hours_only", True)) if market_hours_only is None else market_hours_only
        if interval_seconds != 900:
            raise ValueError("analysis schedule is fixed at 900 seconds")
        self._run_lock = threading.Lock()

    def run_once(self) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"status": "coalesced", "reason": "analysis_cycle_already_running"}
        try:
            return self._run_once()
        finally:
            self._run_lock.release()

    def _run_once(self) -> Dict[str, Any]:
        targets = self.backend_client.fetch_targets()
        if not targets:
            return {"status": "skipped", "reason": "no_auto_trade_targets", "no_paid_work": True,
                    "target_count": 0, "processed": 0, "submitted": 0, "failed": 0, "targets": []}
        summaries: List[Dict[str, Any]] = []
        submitted = 0
        failed = 0
        cycle = self.analysis_service.run_cycle(targets) if self.analysis_service is not None else None

        for target in targets:
            user_id = str(target.get("userId") or "").strip()
            if not user_id:
                continue
            result = cycle["accounts"][user_id] if cycle is not None else self.runner.run_all(
                candidate_limit=self.candidate_limit,
                per_theme_top_n=self.per_theme_top_n,
                top_n=self.top_n,
                execute=False,
                min_leader_score=self.min_leader_score,
                min_confidence=self.min_confidence,
                max_risk_level=self.max_risk_level,
                strategy_profile=str(target.get("strategyProfile") or "default"),
                buy_only=True,
                include_theme_keys=_theme_keys(target),
                save_report=True,
                investor_profile=dict(target.get("investorProfile") or {}),
                user_id=user_id,
            )
            if cycle is None:
                result = _filter_result_to_target_symbols(result, target)
            if result.get("status") == "failed":
                submit_result = {"submitted": 0, "failed": 1, "error": result.get("error")}
            else:
                try:
                    submit_result = self.submitter(user_id=user_id, result=result)
                except Exception as exc:
                    logger.exception("Signal submission failed for account %s", user_id)
                    submit_result = {"submitted": 0, "failed": 1, "error": f"{type(exc).__name__}: {exc}"}
            submitted += int(submit_result.get("submitted") or 0)
            failed += int(submit_result.get("failed") or 0)
            summaries.append(
                {
                    "userId": user_id,
                    "selected": int(result.get("selected_count") or 0),
                    "submitted": int(submit_result.get("submitted") or 0),
                    "failed": int(submit_result.get("failed") or 0),
                    "error": submit_result.get("error"),
                }
            )

        return {
            "target_count": len(targets),
            "processed": len(summaries),
            "submitted": submitted,
            "failed": failed,
            "targets": summaries,
        }

    def run_forever(self) -> None:
        while True:
            delay = seconds_until_next_slot(time.time(), self.interval_seconds)
            time.sleep(delay)
            if self.market_hours_only and not within_analysis_session(datetime.now(timezone.utc)):
                continue
            self.run_once()


def seconds_until_next_slot(timestamp: float, interval: int = 900) -> float:
    """Skip missed slots instead of drifting or replaying a backlog."""
    return interval - timestamp % interval


def within_analysis_session(at: datetime) -> bool:
    """Weekday session gate only; the broker remains authoritative for holidays."""
    if at.tzinfo is None:
        raise ValueError("schedule clock requires an aware timestamp")
    local = at.astimezone(timezone(timedelta(hours=9)))
    return local.weekday() < 5 and wall_time(9) <= local.time() < wall_time(15, 30)


class RemoteAnalysisClient:
    """CLI transport only: all LLM work and admission control stay in the AI server."""
    def __init__(self, base_url: Optional[str] = None, internal_token: Optional[str] = None,
                 timeout: int = 10, completion_timeout: int = 900):
        url = base_url or os.getenv("AI_SERVER_URL")
        token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN")
        if not url or not token:
            raise ValueError("AI_SERVER_URL and HQA_INTERNAL_TOKEN are required for remote analysis")
        self.base_url = url.rstrip("/")
        self.headers = {"X-HQA-Internal-Token": token}
        self.timeout = timeout
        self.completion_timeout = completion_timeout

    def run_once(self) -> Dict[str, Any]:
        return self.submit("/internal/runtime/analysis-cycle", {})

    def submit(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if path not in {"/internal/runtime/analysis-cycle", "/runtime/multi-theme-trade"}:
            raise ValueError("Unsupported remote analysis operation")
        response = requests.post(f"{self.base_url}{path}", json=payload, headers=self.headers, timeout=self.timeout)
        response.raise_for_status()
        task_id = str(uuid.UUID(response.json()["task_id"]))
        deadline = time.monotonic() + self.completion_timeout
        while time.monotonic() < deadline:
            response = requests.get(f"{self.base_url}/runtime/tasks/{task_id}", headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            task = response.json()
            if task["status"] == "completed":
                return task["result"]
            if task["status"] == "failed":
                raise RuntimeError(f"AI analysis task failed:{task.get('error')}")
            if task["status"] not in {"queued", "running"}:
                raise ValueError("Unknown AI analysis task status")
            time.sleep(1)
        raise TimeoutError(f"AI analysis task did not complete before the polling deadline:{task_id}")


def _theme_keys(target: Dict[str, Any]) -> Optional[List[str]]:
    value = target.get("themeKeys") or target.get("theme_keys")
    if not isinstance(value, list):
        return None
    keys = [str(item).strip() for item in value if str(item).strip()]
    return keys or None


def _filter_result_to_target_symbols(result: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        str(symbol.get("stockCode") or symbol.get("stock_code") or "").strip()
        for symbol in target.get("symbols", [])
        if isinstance(symbol, dict)
    }
    allowed.discard("")
    if not allowed:
        return result
    filtered = dict(result)
    filtered["global_ranked_leaders"] = [
        row
        for row in result.get("global_ranked_leaders", [])
        if isinstance(row, dict) and str(row.get("stock_code") or row.get("stockCode") or "").strip() in allowed
    ]
    filtered["selected_count"] = len(filtered["global_ranked_leaders"])
    return filtered


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backend-driven auto-trade analysis scheduling.")
    parser.add_argument("--forever", action="store_true", help="Run continuously instead of once.")
    parser.add_argument("--interval-seconds", type=int, default=900)
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--per-theme-top-n", type=int, default=3)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--config-path", default="config/watchlist.yaml")
    args = parser.parse_args()

    if (args.interval_seconds, args.candidate_limit, args.per_theme_top_n, args.top_n) != (900, 5, 3, 5):
        parser.error("Analysis cadence and selection limits are fixed by the shared AI runtime")
    client = RemoteAnalysisClient()
    if args.forever:
        with open(args.config_path, encoding="utf-8") as handle:
            schedule = yaml.safe_load(handle)["schedule"]
        if schedule.get("timezone", "Asia/Seoul") != "Asia/Seoul" or not schedule["enabled"]:
            raise ValueError("An enabled Asia/Seoul analysis schedule is required")
        while True:
            time.sleep(seconds_until_next_slot(time.time()))
            if schedule["market_hours_only"] and not within_analysis_session(datetime.now(timezone.utc)):
                continue
            print(client.run_once())
    print(client.run_once())


if __name__ == "__main__":
    main()
