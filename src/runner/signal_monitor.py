from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as wall_time, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests


Snapshot = Dict[str, Any]
Condition = Dict[str, Any]
logger = logging.getLogger(__name__)
NUMERIC_FIELDS = {"current_price", "pnl_rate", "holding_quantity"}


def _timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Snapshot and plan timestamps must include a timezone")
    return parsed


def evaluate_condition(condition: Condition, snapshot: Snapshot) -> bool:
    field = str(condition.get("field") or "").strip()
    operator = str(condition.get("operator") or "").strip()
    if field not in NUMERIC_FIELDS | {"market_time"} or operator not in {">", ">=", "<", "<=", "==", "!="}:
        return False
    if field not in snapshot:
        return False

    left = snapshot.get(field)
    right = condition.get("value")
    if field == "market_time":
        try:
            return _compare(wall_time.fromisoformat(str(left)), wall_time.fromisoformat(str(right)), operator)
        except ValueError:
            return False
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        left_num = float(left)
        right_num = float(right)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(left_num) or not math.isfinite(right_num):
        return False
    return _compare(left_num, right_num, operator)


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
        self.base_url = (base_url or os.getenv("BACKEND_INTERNAL_BASE_URL") or os.getenv("BACKEND_BASE_URL") or "http://localhost:8000").rstrip("/")
        self.internal_token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "")
        if not self.internal_token.strip():
            raise ValueError("HQA_INTERNAL_TOKEN is required for the signal monitor")
        self.timeout = timeout

    def fetch_active_signals(self) -> List[Dict[str, Any]]:
        signals: List[Dict[str, Any]] = []
        page = 0
        while True:
            response = requests.get(
                f"{self.base_url}/api/v1/internal/trading/signals/active",
                params={"page": page, "size": 200},
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else payload["signals"]
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError("Invalid active-signals response")
            signals.extend(rows)
            if isinstance(payload, list) or not payload.get("hasMore", payload.get("nextPage") is not None):
                return signals
            next_page = payload.get("nextPage", page + 1)
            if not rows or not isinstance(next_page, int) or next_page <= page:
                raise ValueError("Invalid active-signals pagination")
            page = next_page

    def fetch_account_snapshot(self, user_id: str) -> Dict[str, Any]:
        payload = self._post("/api/v1/internal/trading/account-snapshots", {"userIds": [user_id]})
        rows = payload["snapshots"]
        if len(rows) != 1 or str(rows[0].get("userId")) != user_id:
            raise ValueError("Account snapshot user mismatch")
        account = rows[0]
        if not account.get("success") or account.get("accountMode") != "PAPER":
            raise ValueError(f"PAPER account snapshot unavailable: {account.get('error')}")
        return account

    def fetch_auto_trade_targets(self) -> List[Dict[str, Any]]:
        response = requests.get(f"{self.base_url}/api/v1/internal/trading/auto-trade-targets",
                                headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload["targets"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("userId") for row in rows):
            raise ValueError("Invalid auto-trade target response")
        return rows

    def fetch_price_snapshots(self, user_id: str, stock_codes: List[str]) -> List[Dict[str, Any]]:
        payload = self._post("/api/v1/internal/market/price-snapshots", {"userId": user_id, "stockCodes": stock_codes})
        return payload["snapshots"]

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(f"{self.base_url}{path}", json=payload, headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def trigger_signal(self, signal_id: str, trigger: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/api/v1/internal/trading/signals/{signal_id}/trigger",
            json=trigger,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("accepted") is not True:
            raise ValueError(f"Trigger not accepted: {result.get('rejectReason') or result.get('status')}")
        return result

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.internal_token:
            headers["X-HQA-Internal-Token"] = self.internal_token
        return headers


class SignalMonitor:
    def __init__(
        self,
        backend_client: Any,
        price_provider: Optional[Callable[[Dict[str, Any]], Snapshot]] = None,
        entry_poll_seconds: int = 20,
        open_poll_seconds: int = 20,
        snapshot_batch_provider: Optional[Any] = None,
        max_snapshot_age_seconds: float = 20,
        clock: Optional[Callable[[], datetime]] = None,
        audit: Optional[Any] = None,
    ):
        self.backend_client = backend_client
        self.price_provider = price_provider
        self.entry_poll_seconds = entry_poll_seconds
        self.open_poll_seconds = open_poll_seconds
        if min(entry_poll_seconds, open_poll_seconds, max_snapshot_age_seconds) <= 0:
            raise ValueError("Monitor intervals must be positive")
        if price_provider is None and snapshot_batch_provider is None:
            raise ValueError("A snapshot provider is required")
        self.snapshot_batch_provider = snapshot_batch_provider
        self.max_snapshot_age_seconds = max_snapshot_age_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.last_report: Dict[str, Any] = {}
        self.audit = audit

    def poll_once(self) -> int:
        triggered = 0
        started = time.monotonic()
        signals = self.backend_client.fetch_active_signals()
        errors: List[Dict[str, str]] = []
        if self.snapshot_batch_provider and hasattr(self.snapshot_batch_provider, "iter_prepared"):
            observations = self.snapshot_batch_provider.iter_prepared(signals)
        elif self.snapshot_batch_provider:
            snapshots = self.snapshot_batch_provider.prepare(signals)
            observations = ((signal, snapshots[(str(signal["userId"]), str(signal["stockCode"]))]) for signal in signals)
        else:
            observations = ((signal, None) for signal in signals)
        checked = 0
        deduplicated = 0
        max_age = 0.0
        for signal, prepared in observations:
            signal_id = str(signal.get("signalId") or signal.get("id") or "")
            try:
                if not signal_id:
                    raise ValueError("Missing signal ID")
                if self.snapshot_batch_provider:
                    if isinstance(prepared, Exception):
                        raise prepared
                    snapshot = prepared
                else:
                    snapshot = self.price_provider(signal)
                payload = signal.get("conditionPayload") or signal.get("condition_payload") or {}
                is_v2 = payload.get("schema_version") == 2
                if is_v2 or self.snapshot_batch_provider:
                    age = (self.clock() - _timestamp(snapshot["snapshot_at"])).total_seconds()
                    if age < -5 or age > self.max_snapshot_age_seconds:
                        raise ValueError(f"Stale or future price snapshot: age={age:.1f}s")
                    max_age = max(max_age, age)
                    for field in NUMERIC_FIELDS:
                        value = snapshot.get(field)
                        if value is None:
                            if field == "current_price":
                                raise ValueError("Missing current price")
                            continue
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                            raise ValueError(f"Invalid numeric snapshot field: {field}")
                        if field == "current_price" and value <= 0:
                            raise ValueError("Current price must be positive")
                        if field == "holding_quantity" and (value < 0 or int(value) != value):
                            raise ValueError("Holding quantity must be a nonnegative integer")
                    if "account_snapshot_at" in snapshot:
                        account_age = (self.clock() - _timestamp(snapshot["account_snapshot_at"])).total_seconds()
                        if not -5 <= account_age <= 30:
                            raise ValueError("Account snapshot expired during price retrieval")
                checked += 1
                match = self._matching_condition(signal, snapshot)
                if match is None:
                    continue
                trigger_type, condition = match
                trigger = {"triggerType": trigger_type, "matchedCondition": condition, "snapshot": snapshot}
                if is_v2:
                    trigger.update({"planVersion": signal["planVersion"], "groupId": condition["id"]})
                else:
                    condition_index = payload[trigger_type.lower() + "_conditions"].index(condition)
                    trigger["groupId"] = f"legacy-{trigger_type.lower()}-{condition_index}"
                    if "planVersion" in signal:
                        trigger["planVersion"] = signal["planVersion"]
                outcome = self.backend_client.trigger_signal(signal_id, trigger)
                if isinstance(outcome, dict) and outcome.get("deduplicated") is True:
                    deduplicated += 1
                else:
                    triggered += 1
            except (ValueError, TypeError, KeyError, requests.RequestException) as exc:
                errors.append({"signal_id": signal_id, "error": str(exc)})
                logger.error("Signal monitor failed for %s: %s", signal_id, exc)
        errors.extend(getattr(self.snapshot_batch_provider, "coverage_errors", []))
        uncovered = list(getattr(self.snapshot_batch_provider, "uncovered_holdings", []))
        elapsed = time.monotonic() - started
        self.last_report = {"signals": len(signals), "checked": checked, "triggered": triggered,
                            "deduplicated": deduplicated,
                            "errors": errors, "elapsed_seconds": elapsed,
                            "uncovered_holdings": uncovered,
                            "max_quote_age_seconds": max_age,
                            "slo_met": not errors and elapsed <= 30}
        if self.audit is not None:
            self.audit.append("monitor", self.last_report)
        return triggered

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            self.poll_once()
            logger.info("signal_monitor %s", json.dumps(self.last_report))
            time.sleep(max(0, min(self.open_poll_seconds, self.entry_poll_seconds) - (time.monotonic() - started)))

    def _matching_condition(self, signal: Dict[str, Any], snapshot: Snapshot) -> Optional[Tuple[str, Condition]]:
        status = str(signal.get("status") or "")
        payload = signal.get("conditionPayload") or signal.get("condition_payload") or {}
        if not isinstance(payload, dict):
            return None

        version = payload.get("schema_version", 1)
        if version not in {1, 2}:
            raise ValueError(f"Unsupported condition schema: {version}")
        if status == "WAITING_ENTRY":
            invalidation = _first_match("INVALIDATION", payload.get("invalidation_conditions"), snapshot, version)
            if invalidation:
                return invalidation
            entry_until = signal.get("entryValidUntil") or signal.get("expiresAt")
            if version == 2 and not entry_until:
                raise ValueError("Missing entry validity deadline")
            if entry_until and self.clock() >= _timestamp(entry_until):
                return None
            return _first_match("ENTRY", payload.get("entry_conditions"), snapshot, version)
        if status in {"OPEN", "WAITING_EXIT", "PARTIALLY_FILLED"}:
            planned_exit = signal.get("plannedExitAt")
            if version == 2 and planned_exit and self.clock() >= _timestamp(planned_exit):
                return "EXIT", {"id": "planned-exit"}
            missing_inputs: List[str] = []
            for trigger_type in ("EXIT", "INVALIDATION", "REDUCE"):
                match = _first_match(trigger_type, payload.get(trigger_type.lower() + "_conditions"),
                                     snapshot, version, missing_inputs=missing_inputs)
                if match:
                    return match
            if missing_inputs:
                raise ValueError("Missing condition inputs: " + ", ".join(dict.fromkeys(missing_inputs)))
        return None


def _first_match(trigger_type: str, conditions: Any, snapshot: Snapshot, version: int = 1,
                 *, missing_inputs: Optional[List[str]] = None) -> Optional[Tuple[str, Condition]]:
    if not isinstance(conditions, Iterable) or isinstance(conditions, (str, bytes, dict)):
        return None
    missing: List[str] = []
    for condition in conditions:
        if version == 2:
            if not isinstance(condition, dict) or not condition.get("id") or not isinstance(condition.get("all"), list) or not condition["all"]:
                raise ValueError("Invalid v2 condition group")
            group_missing = []
            for atom in condition["all"]:
                if not isinstance(atom, dict) or atom.get("field") not in NUMERIC_FIELDS | {"market_time"}:
                    raise ValueError("Unsupported condition field")
                if atom.get("operator") not in {">", ">=", "<", "<=", "==", "!="}:
                    raise ValueError("Unsupported condition operator")
                if atom["field"] not in snapshot or snapshot[atom["field"]] is None:
                    group_missing.append(atom["field"])
            if group_missing:
                missing.extend(group_missing)
                continue
            if all(evaluate_condition(atom, snapshot) for atom in condition["all"]):
                return trigger_type, condition
            continue
        if isinstance(condition, dict) and evaluate_condition(condition, snapshot):
            return trigger_type, condition
    if missing:
        if missing_inputs is None:
            raise ValueError("Missing condition inputs: " + ", ".join(dict.fromkeys(missing)))
        missing_inputs.extend(missing)
    return None


class BackendSnapshotProvider:
    """Fetch each account once per poll; parallelism never shares account credentials."""

    def __init__(self, backend_client: BackendSignalClient, max_workers: int = 10):
        self.backend_client = backend_client
        self.max_workers = max_workers
        self.coverage_errors: List[Dict[str, Any]] = []
        self.uncovered_holdings: List[Dict[str, Any]] = []

    def prepare(self, signals: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Any]:
        return {(str(signal["userId"]), str(signal["stockCode"])): value
                for signal, value in self.iter_prepared(signals)}

    def iter_prepared(self, signals: List[Dict[str, Any]]):
        self.coverage_errors = []
        self.uncovered_holdings = []
        by_user: Dict[str, List[Dict[str, Any]]] = {}
        for signal in signals:
            by_user.setdefault(str(signal["userId"]), []).append(signal)
        target_fetcher = getattr(self.backend_client, "fetch_auto_trade_targets", None)
        if target_fetcher is not None:
            try:
                for target in target_fetcher():
                    by_user.setdefault(str(target["userId"]), [])
            except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
                self.coverage_errors.append({"error": f"auto_trade_targets_unavailable:{exc}"})
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._for_account, user_id, rows): (user_id, rows)
                       for user_id, rows in by_user.items()}
            for future in as_completed(futures):
                user_id, rows = futures[future]
                try:
                    values, uncovered = future.result()
                except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
                    self.coverage_errors.append({"user_id": user_id, "error": f"holding_coverage_unavailable:{exc}"})
                    for signal in rows:
                        yield signal, exc
                    continue
                self.uncovered_holdings.extend(uncovered)
                for row in uncovered:
                    self.coverage_errors.append({"user_id": user_id, "stock_code": row["stock_code"],
                                                 "error": "missing_protection"})
                    if row.get("quote_error"):
                        self.coverage_errors.append({"user_id": user_id, "stock_code": row["stock_code"],
                                                     "error": row["quote_error"]})
                for signal in rows:
                    yield signal, values[(user_id, str(signal["stockCode"]))]

    def _for_account(self, user_id: str, plans: List[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str], Any], List[Dict[str, Any]]]:
        account = self.backend_client.fetch_account_snapshot(user_id)
        captured_at = _timestamp(account["capturedAt"])
        if not -5 <= (datetime.now(timezone.utc) - captured_at).total_seconds() <= 30:
            raise ValueError("Stale account snapshot")
        holdings = {str(row["stockCode"]): row for row in account["holdings"]}
        codes = sorted(set(holdings) | {str(row["stockCode"]) for row in plans})
        if not codes:
            return {}, []
        price_error = None
        try:
            prices = self.backend_client.fetch_price_snapshots(user_id, codes)
            by_code = {str(row["stockCode"]): row for row in prices}
        except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
            by_code = {}
            price_error = str(exc)
        result: Dict[Tuple[str, str], Any] = {}
        for code in codes:
            row = by_code.get(code)
            if row is None or not row.get("success"):
                result[(user_id, code)] = ValueError(f"Price snapshot unavailable: {code}: {row.get('failureReason') if row else price_error or 'missing'}")
                continue
            try:
                holding = holdings.get(code)
                price = row["currentPrice"]
                if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                    raise ValueError(f"Invalid current price: {code}")
                quantity = holding["quantity"] if holding else 0
                average = holding["avgPrice"] if holding else None
                if (isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                        or not math.isfinite(quantity) or quantity < 0 or int(quantity) != quantity):
                    raise ValueError(f"Invalid holding quantity: {code}")
                if holding and (isinstance(average, bool) or not isinstance(average, (int, float))
                                or not math.isfinite(average) or average < 0):
                    raise ValueError(f"Invalid average price: {code}")
                age = (datetime.now(timezone.utc) - _timestamp(row["snapshotAt"])).total_seconds()
                if not -5 <= age <= 20:
                    raise ValueError(f"Stale or future price snapshot: {code}:age={age:.1f}s")
                result[(user_id, code)] = {
                    "current_price": price, "snapshot_at": row["snapshotAt"], "holding_quantity": quantity,
                    "pnl_rate": (price / average - 1) * 100 if average and average > 0 else None,
                    "market_time": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%H:%M:%S"),
                    "account_snapshot_at": account["capturedAt"], "source": row["source"],
                }
            except (ValueError, KeyError, TypeError) as exc:
                result[(user_id, code)] = exc
        protected = set()
        for plan in plans:
            conditions = plan.get("conditionPayload") or plan.get("condition_payload") or {}
            if (plan.get("status") in {"OPEN", "WAITING_EXIT", "PARTIALLY_FILLED"}
                    and (plan.get("plannedExitAt") or any(conditions.get(name) for name in
                         ("exit_conditions", "invalidation_conditions", "reduce_conditions")))):
                protected.add(str(plan["stockCode"]))
        uncovered = []
        for code, holding in holdings.items():
            if code in protected:
                continue
            value = result[(user_id, code)]
            uncovered.append({"user_id": user_id, "stock_code": code, "holding_quantity": holding["quantity"],
                              "quote_available": not isinstance(value, Exception),
                              "quote_error": str(value) if isinstance(value, Exception) else None})
        return result, uncovered


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent PAPER plan monitor")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    from src.config.settings import get_data_dir
    from src.tracing.paper_audit import PaperAudit

    client = BackendSignalClient(timeout=25)
    audit = PaperAudit(os.getenv("HQA_PAPER_AUDIT_PATH") or get_data_dir() / "paper_audit.sqlite3")
    monitor = SignalMonitor(client, snapshot_batch_provider=BackendSnapshotProvider(client), audit=audit)
    if args.once:
        monitor.poll_once()
        print(json.dumps(monitor.last_report))
    else:
        monitor.run_forever()


if __name__ == "__main__":
    main()
