from __future__ import annotations

import os
import time
import argparse
from typing import Any, Callable, Dict, List, Optional

import requests

from src.runner.multi_theme_leader_trading_runner import MultiThemeLeaderTradingRunner
from src.runner.trade_signal_submitter import submit_trade_signals


class BackendAutoTradeTargetClient:
    def __init__(self, base_url: Optional[str] = None, internal_token: Optional[str] = None, timeout: int = 10):
        self.base_url = (base_url or os.getenv("BACKEND_BASE_URL") or "http://localhost:8000").rstrip("/")
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
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return [item for item in payload.get("targets", []) if isinstance(item, dict)]

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
        submitter: Callable[..., Dict[str, Any]] = submit_trade_signals,
        interval_seconds: int = 900,
        candidate_limit: int = 5,
        per_theme_top_n: int = 3,
        top_n: int = 5,
        min_leader_score: Optional[int] = None,
        min_confidence: Optional[int] = None,
        max_risk_level: Optional[str] = None,
    ):
        self.backend_client = backend_client
        self.runner = runner or MultiThemeLeaderTradingRunner(
            dry_run_override=True,
            trading_enabled_override=True,
            account_type_override="paper",
        )
        self.submitter = submitter
        self.interval_seconds = interval_seconds
        self.candidate_limit = candidate_limit
        self.per_theme_top_n = per_theme_top_n
        self.top_n = top_n
        self.min_leader_score = min_leader_score
        self.min_confidence = min_confidence
        self.max_risk_level = max_risk_level

    def run_once(self) -> Dict[str, Any]:
        targets = self.backend_client.fetch_targets()
        summaries: List[Dict[str, Any]] = []
        submitted = 0
        failed = 0

        for target in targets:
            user_id = str(target.get("userId") or "").strip()
            if not user_id:
                continue
            result = self.runner.run_all(
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
            result = _filter_result_to_target_symbols(result, target)
            submit_result = self.submitter(user_id=user_id, result=result)
            submitted += int(submit_result.get("submitted") or 0)
            failed += int(submit_result.get("failed") or 0)
            summaries.append(
                {
                    "userId": user_id,
                    "selected": int(result.get("selected_count") or 0),
                    "submitted": int(submit_result.get("submitted") or 0),
                    "failed": int(submit_result.get("failed") or 0),
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
            self.run_once()
            time.sleep(self.interval_seconds)


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
    args = parser.parse_args()

    scheduler = AnalysisScheduler(
        backend_client=BackendAutoTradeTargetClient(),
        interval_seconds=args.interval_seconds,
        candidate_limit=args.candidate_limit,
        per_theme_top_n=args.per_theme_top_n,
        top_n=args.top_n,
    )
    if args.forever:
        scheduler.run_forever()
        return
    print(scheduler.run_once())


if __name__ == "__main__":
    main()
