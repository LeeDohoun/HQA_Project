from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests


Snapshot = Dict[str, Any]
Condition = Dict[str, Any]


def evaluate_condition(condition: Condition, snapshot: Snapshot) -> bool:
    field = str(condition.get("field") or "").strip()
    operator = str(condition.get("operator") or "").strip()
    if not field or operator not in {">", ">=", "<", "<=", "==", "!="}:
        return False
    if field not in snapshot:
        return False

    left = snapshot.get(field)
    right = condition.get("value")
    try:
        left_num = float(left)
        right_num = float(right)
    except (TypeError, ValueError):
        left_num = None
        right_num = None

    if left_num is not None and right_num is not None:
        return _compare(left_num, right_num, operator)
    return _compare(str(left), str(right), operator)


def _compare(left: Any, right: Any, operator: str) -> bool:
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    return False


class BackendSignalClient:
    def __init__(self, base_url: Optional[str] = None, internal_token: Optional[str] = None, timeout: int = 10):
        self.base_url = (base_url or os.getenv("BACKEND_BASE_URL") or "http://localhost:8000").rstrip("/")
        self.internal_token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "")
        self.timeout = timeout

    def fetch_active_signals(self) -> List[Dict[str, Any]]:
        response = requests.get(
            f"{self.base_url}/api/v1/internal/trading/signals/active",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return [item for item in payload.get("signals", []) if isinstance(item, dict)]

    def trigger_signal(self, signal_id: str, trigger: Dict[str, Any]) -> None:
        response = requests.post(
            f"{self.base_url}/api/v1/internal/trading/signals/{signal_id}/trigger",
            json=trigger,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.internal_token:
            headers["X-HQA-Internal-Token"] = self.internal_token
        return headers


class SignalMonitor:
    def __init__(
        self,
        backend_client: Any,
        price_provider: Callable[[Dict[str, Any]], Snapshot],
        entry_poll_seconds: int = 300,
        open_poll_seconds: int = 60,
    ):
        self.backend_client = backend_client
        self.price_provider = price_provider
        self.entry_poll_seconds = entry_poll_seconds
        self.open_poll_seconds = open_poll_seconds

    def poll_once(self) -> int:
        triggered = 0
        for signal in self.backend_client.fetch_active_signals():
            snapshot = self.price_provider(signal)
            match = self._matching_condition(signal, snapshot)
            if match is None:
                continue
            trigger_type, condition = match
            self.backend_client.trigger_signal(
                str(signal.get("signalId") or signal.get("id")),
                {
                    "triggerType": trigger_type,
                    "matchedCondition": condition,
                    "snapshot": snapshot,
                },
            )
            triggered += 1
        return triggered

    def run_forever(self) -> None:
        while True:
            signals = self.backend_client.fetch_active_signals()
            open_present = any(str(item.get("status")) == "OPEN" for item in signals)
            for signal in signals:
                snapshot = self.price_provider(signal)
                match = self._matching_condition(signal, snapshot)
                if match is None:
                    continue
                trigger_type, condition = match
                self.backend_client.trigger_signal(
                    str(signal.get("signalId") or signal.get("id")),
                    {"triggerType": trigger_type, "matchedCondition": condition, "snapshot": snapshot},
                )
            time.sleep(self.open_poll_seconds if open_present else self.entry_poll_seconds)

    def _matching_condition(self, signal: Dict[str, Any], snapshot: Snapshot) -> Optional[Tuple[str, Condition]]:
        status = str(signal.get("status") or "")
        payload = signal.get("conditionPayload") or signal.get("condition_payload") or {}
        if not isinstance(payload, dict):
            return None

        if status == "WAITING_ENTRY":
            return _first_match("ENTRY", payload.get("entry_conditions"), snapshot)
        if status in {"OPEN", "WAITING_EXIT"}:
            return (
                _first_match("EXIT", payload.get("exit_conditions"), snapshot)
                or _first_match("REDUCE", payload.get("reduce_conditions"), snapshot)
                or _first_match("INVALIDATION", payload.get("invalidation_conditions"), snapshot)
            )
        return None


def _first_match(trigger_type: str, conditions: Any, snapshot: Snapshot) -> Optional[Tuple[str, Condition]]:
    if not isinstance(conditions, Iterable) or isinstance(conditions, (str, bytes, dict)):
        return None
    for condition in conditions:
        if isinstance(condition, dict) and evaluate_condition(condition, snapshot):
            return trigger_type, condition
    return None
