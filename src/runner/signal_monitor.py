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
KST = ZoneInfo("Asia/Seoul")
PROTECTIVE_CONDITIONS = ("exit_conditions", "invalidation_conditions", "reduce_conditions")
# Backend rejections that stay true while the plan keeps its version and status, so the
# trigger is not re-sent until one of them changes; the plan's other groups still run.
SETTLED_REJECTIONS = {"STALE_PLAN_VERSION", "UNKNOWN_CONDITION_GROUP", "ACCOUNT_BINDING_CHANGED",
                      "PAPER_ACCOUNT_REQUIRED", "ENTRY_STATE_INVALID", "EXIT_STATE_INVALID"}
SETTLED_BY_TYPE = {"ENTRY": {"TRIGGER_ALREADY_CONSUMED", "ENTRY_EXPIRED", "HOLDING_ALREADY_EXISTS",
                             "SIGNAL_PRICE_REQUIRED", "DAILY_BUY_LIMIT_EXCEEDED"},
                   "REDUCE": {"TRIGGER_ALREADY_CONSUMED"}}
# A condition stays true poll after poll; an accepted order is left to work (and to the
# backend's 20-second reconciliation) this long before the same trigger is sent again.
ACCEPTED_TRIGGER_QUIET_SECONDS = 60
# Signal reject reasons under which the backend refuses the plan's sells until an operator
# reconciles it, so the plan does not count as protection for its holding.
BLOCKING_REJECTIONS = {"INVALID_STORED_CONDITIONS", "ACCOUNT_BINDING_CHANGED", "PAPER_ACCOUNT_REQUIRED", "USER_INACTIVE",
                       "BROKER_ORDER_ID_NOT_UNIQUE_OR_MISSING", "BROKER_ORDER_IDENTITY_MISMATCH",
                       "BROKER_FILL_STATE_INVALID", "BROKER_TERMINAL_FILL_CONFLICT",
                       "BROKER_FILL_EXCEEDS_MANAGED_POSITION", "ORDER_ACCEPTANCE_UNKNOWN",
                       "RESTART_REQUIRES_BROKER_RECONCILIATION"}
# A filled entry stays WAITING_ENTRY until the backend's reconciliation records the fill,
# normally within one or two polls; only a longer gap is reported as missing protection.
ENTRY_FILL_GRACE_SECONDS = 60


class TriggerRejected(ValueError):
    """The backend answered without accepting the trigger; reason is its rejectReason."""

    def __init__(self, reason: str):
        super().__init__(f"Trigger not accepted: {reason}")
        self.reason = reason


def market_session(at: datetime) -> str:
    """'open' inside the verified KRX session (special hours such as CSAT days included);
    'protect' on a weekday whose hours cannot be verified (a pending KRX notice or an
    expired calendar review), between 09:00 and 16:30 KST, which contains every known
    session; 'closed' otherwise. The backend's own session check still decides."""
    local = at.astimezone(KST)
    if local.weekday() >= 5:
        return "closed"
    day = local.date().isoformat()
    try:
        from src.runner.trading_calendar import daily_session_close, daily_session_open, is_trading_day
        if not is_trading_day(day):
            return "closed"
        try:
            return "open" if daily_session_open(day) <= at < daily_session_close(day) else "closed"
        except ValueError:
            pass
    except Exception:
        logger.exception("signal_monitor session calendar check failed")
    return "protect" if wall_time(9) <= local.time() < wall_time(16, 30) else "closed"


def _json(response: Any) -> Any:
    """The decoded body; an HTTP error keeps the start of the backend's explanation."""
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = str(getattr(response, "text", "") or "").strip()[:300]
        raise requests.HTTPError(f"{exc}: {body}" if body else str(exc), response=exc.response) from exc
    return response.json()


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
        self.internal_token = (internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "")).strip()
        if not self.internal_token:
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
            payload = _json(response)
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
        payload = _json(requests.get(f"{self.base_url}/api/v1/internal/trading/auto-trade-targets",
                                     headers=self._headers(), timeout=self.timeout))
        rows = payload if isinstance(payload, list) else payload["targets"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("userId") for row in rows):
            raise ValueError("Invalid auto-trade target response")
        return rows

    def fetch_price_snapshots(self, user_id: str, stock_codes: List[str]) -> List[Dict[str, Any]]:
        payload = self._post("/api/v1/internal/market/price-snapshots", {"userId": user_id, "stockCodes": stock_codes})
        return payload["snapshots"]

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return _json(requests.post(f"{self.base_url}{path}", json=payload, headers=self._headers(), timeout=self.timeout))

    def trigger_signal(self, signal_id: str, trigger: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/api/v1/internal/trading/signals/{signal_id}/trigger",
            json=trigger,
            headers=self._headers(),
            timeout=self.timeout,
        )
        result = _json(response)
        if result.get("accepted") is not True:
            raise TriggerRejected(str(result.get("rejectReason") or result.get("status")))
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
        session: Optional[Callable[[datetime], str]] = None,
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
        self.session = session or market_session
        self.last_report: Dict[str, Any] = {}
        self.audit = audit
        # Keyed by (signal, plan version, plan status, trigger type, group).
        self._settled: Dict[Tuple[Any, ...], str] = {}
        self._accepted_at: Dict[Tuple[Any, ...], datetime] = {}

    def poll_once(self) -> int:
        started = time.monotonic()
        now = self.clock()
        session = self.session(now)
        signals = self.backend_client.fetch_active_signals()
        self._forget_inactive(signals, now)
        errors: List[Dict[str, str]] = []
        rejections: List[Dict[str, str]] = []
        settled: List[Dict[str, str]] = []
        if self.snapshot_batch_provider and hasattr(self.snapshot_batch_provider, "iter_prepared"):
            observations = self.snapshot_batch_provider.iter_prepared(signals)
        elif self.snapshot_batch_provider:
            snapshots = self.snapshot_batch_provider.prepare(signals)
            observations = ((signal, snapshots[(str(signal["userId"]), str(signal["stockCode"]))]) for signal in signals)
        else:
            observations = ((signal, None) for signal in signals)
        checked = 0
        counts = {"triggered": 0, "deduplicated": 0}
        deferred = 0
        quiet = 0
        max_age = 0.0
        entries: List[Tuple[str, Dict[str, Any], Tuple[Any, ...]]] = []
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
            except (ValueError, TypeError, KeyError, requests.RequestException) as exc:
                errors.append({"signal_id": signal_id, "error": str(exc)})
                logger.error("Signal monitor failed for %s: %s", signal_id, exc)
                continue
            key = (signal_id, signal.get("planVersion"), str(signal.get("status") or ""), trigger_type, trigger["groupId"])
            if key in self._settled:
                settled.append({"signal_id": signal_id, "trigger_type": trigger_type, "group_id": trigger["groupId"],
                                "reason": self._settled[key]})
                continue
            accepted_at = self._accepted_at.get(key)
            if accepted_at is not None and (now - accepted_at).total_seconds() < ACCEPTED_TRIGGER_QUIET_SECONDS:
                quiet += 1
                continue
            if session == "closed" or (trigger_type == "ENTRY" and session != "open"):
                deferred += 1
                continue
            if trigger_type == "ENTRY":
                entries.append((signal_id, trigger, key))  # every protective trigger goes first
                continue
            outcome = self._send(signal_id, trigger, key, session, errors, rejections)
            if outcome:
                counts[outcome] += 1
        for signal_id, trigger, key in entries:
            outcome = self._send(signal_id, trigger, key, session, errors, rejections)
            if outcome:
                counts[outcome] += 1
        errors.extend(getattr(self.snapshot_batch_provider, "coverage_errors", []))
        uncovered = list(getattr(self.snapshot_batch_provider, "uncovered_holdings", []))
        elapsed = time.monotonic() - started
        self.last_report = {"checked_at": now.isoformat(), "session": session,
                            "signals": len(signals), "checked": checked, "triggered": counts["triggered"],
                            "deduplicated": counts["deduplicated"], "deferred": deferred, "quiet": quiet,
                            "rejections": rejections, "settled": settled,
                            "errors": errors, "elapsed_seconds": elapsed,
                            "uncovered_holdings": uncovered,
                            "max_quote_age_seconds": max_age,
                            "slo_met": not errors and elapsed <= 30}
        if self.audit is not None:
            try:
                self.audit.append("monitor", self.last_report)
            except Exception as exc:
                # The poll itself completed (orders may have gone out); only its record failed.
                logger.exception("signal_monitor could not record the poll")
                self.last_report["audit_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return counts["triggered"]

    def _send(self, signal_id: str, trigger: Dict[str, Any], key: Tuple[Any, ...], session: str,
              errors: List[Dict[str, str]], rejections: List[Dict[str, str]]) -> Optional[str]:
        trigger_type = trigger["triggerType"]
        try:
            outcome = self.backend_client.trigger_signal(signal_id, trigger)
        except TriggerRejected as exc:
            if exc.reason in SETTLED_REJECTIONS or exc.reason in SETTLED_BY_TYPE.get(trigger_type, ()):
                self._settled[key] = exc.reason
            rejections.append({"signal_id": signal_id, "trigger_type": trigger_type,
                               "group_id": trigger["groupId"], "reason": exc.reason})
            # A refused entry is a trading decision; a refused sell leaves the position exposed,
            # unless the price moved back or, on a day of unverified hours, the backend's
            # session had not yet opened or had already closed.
            expected = {"CONDITION_NO_LONGER_MATCHES"} | ({"MARKET_CLOSED"} if session == "protect" else set())
            if trigger_type != "ENTRY" and exc.reason not in expected:
                errors.append({"signal_id": signal_id, "error": str(exc)})
            logger.warning("Signal monitor trigger for %s refused: %s %s", signal_id, trigger_type, exc.reason)
            return None
        except (ValueError, TypeError, KeyError, requests.RequestException) as exc:
            errors.append({"signal_id": signal_id, "error": str(exc)})
            logger.error("Signal monitor failed for %s: %s", signal_id, exc)
            return None
        self._accepted_at[key] = self.clock()
        return "deduplicated" if isinstance(outcome, dict) and outcome.get("deduplicated") is True else "triggered"

    def _forget_inactive(self, signals: List[Dict[str, Any]], now: datetime) -> None:
        active = {(str(signal.get("signalId") or signal.get("id") or ""), signal.get("planVersion")) for signal in signals}
        self._settled = {key: reason for key, reason in self._settled.items() if key[:2] in active}
        self._accepted_at = {key: at for key, at in self._accepted_at.items() if key[:2] in active
                             and (now - at).total_seconds() < ACCEPTED_TRIGGER_QUIET_SECONDS}

    def run_forever(self) -> None:
        consecutive_failures = 0
        idle = False
        while True:
            started = time.monotonic()
            try:
                if self.session(self.clock()) == "closed":
                    # Outside the session there is nothing to act on: no quotes, account
                    # calls or triggers (the backend refuses orders then anyway).
                    if not idle:
                        logger.info("signal_monitor idle: KRX session closed")
                    idle = True
                else:
                    idle = False
                    self.poll_once()
                    consecutive_failures = 0
                    logger.info("signal_monitor %s", json.dumps(self.last_report))
            except Exception as exc:
                # A backend restart or network error must not end position protection:
                # record the failed poll and try again on the next interval.
                consecutive_failures += 1
                self._record_failed_poll(exc, time.monotonic() - started, consecutive_failures)
            time.sleep(max(0, min(self.open_poll_seconds, self.entry_poll_seconds) - (time.monotonic() - started)))

    def _record_failed_poll(self, exc: Exception, elapsed: float, consecutive_failures: int) -> None:
        self.last_report = {"checked_at": self.clock().isoformat(), "status": "failed", "checked": 0, "triggered": 0,
                            "errors": [{"stage": "poll", "error_type": type(exc).__name__, "error": str(exc)[:500]}],
                            "elapsed_seconds": round(elapsed, 3), "slo_met": False,
                            "consecutive_failures": consecutive_failures}
        logger.error("signal_monitor poll failed (%d consecutive): %s: %s",
                     consecutive_failures, type(exc).__name__, str(exc)[:500])
        if self.audit is not None:
            try:
                self.audit.append("monitor", self.last_report)
            except Exception:
                logger.exception("signal_monitor could not record the failed poll")

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
            held = snapshot.get("holding_quantity")
            if isinstance(held, (int, float)) and not isinstance(held, bool) and held > 0:
                # The shares are already held: the entry filled before the backend recorded it
                # (or they were bought outside HQA). Its stop applies now and no second entry
                # is sent; the backend reconciles the fill before it acts on the trigger.
                return _first_match("EXIT", payload.get("exit_conditions"), snapshot, version)
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
        self._unrecorded_since: Dict[Tuple[str, str], datetime] = {}

    def prepare(self, signals: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Any]:
        return {(str(signal["userId"]), str(signal["stockCode"])): value
                for signal, value in self.iter_prepared(signals)}

    def iter_prepared(self, signals: List[Dict[str, Any]]):
        self.coverage_errors = []
        self.uncovered_holdings = []
        now = datetime.now(timezone.utc)
        unrecorded: Dict[Tuple[str, str], datetime] = {}
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
                    values, uncovered, notes = future.result()
                except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
                    self.coverage_errors.append({"user_id": user_id, "error": f"holding_coverage_unavailable:{exc}"})
                    for signal in rows:
                        yield signal, exc
                    continue
                self.coverage_errors.extend(notes)
                self.uncovered_holdings.extend(uncovered)
                for row in uncovered:
                    if row["reason"] == "entry_fill_unrecorded":
                        since = unrecorded[(user_id, row["stock_code"])] = self._unrecorded_since.get(
                            (user_id, row["stock_code"]), now)
                        if (now - since).total_seconds() < ENTRY_FILL_GRACE_SECONDS:
                            row["grace"] = True
                            continue
                    self.coverage_errors.append({"user_id": user_id, "stock_code": row["stock_code"],
                                                 "error": "missing_protection", "reason": row["reason"]})
                    if row.get("quote_error"):
                        self.coverage_errors.append({"user_id": user_id, "stock_code": row["stock_code"],
                                                     "error": row["quote_error"]})
                for signal in rows:
                    yield signal, values[(user_id, str(signal["stockCode"]))]
        self._unrecorded_since = unrecorded

    def _for_account(self, user_id: str, plans: List[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str], Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        account = self.backend_client.fetch_account_snapshot(user_id)
        captured_at = _timestamp(account["capturedAt"])
        if not -5 <= (datetime.now(timezone.utc) - captured_at).total_seconds() <= 30:
            raise ValueError("Stale account snapshot")
        holdings = {str(row["stockCode"]): row for row in account["holdings"]}
        codes = sorted(set(holdings) | {str(row["stockCode"]) for row in plans})
        if not codes:
            return {}, [], []
        notes = []
        capacity = account.get("monitorCapacity")
        if account.get("monitorCapacityExceeded") is True or (type(capacity) is int and len(codes) > capacity):
            # Every holding is still quoted; the backend has stopped admitting new entries.
            notes.append({"user_id": user_id, "error": f"monitor_capacity_exceeded:{len(codes)}/{capacity}"})
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
        by_code: Dict[str, List[Dict[str, Any]]] = {}
        for plan in plans:
            by_code.setdefault(str(plan["stockCode"]), []).append(plan)
        uncovered = []
        for code, holding in holdings.items():
            if not holding.get("quantity"):
                continue
            reason = _protection_gap(by_code.get(code, []), holding)
            if reason is None:
                continue
            value = result[(user_id, code)]
            uncovered.append({"user_id": user_id, "stock_code": code, "holding_quantity": holding["quantity"],
                              "reason": reason, "quote_available": not isinstance(value, Exception),
                              "quote_error": str(value) if isinstance(value, Exception) else None})
        return result, uncovered, notes


def _protection_gap(plans: List[Dict[str, Any]], holding: Dict[str, Any]) -> Optional[str]:
    """Why none of a holding's listed plans can sell it now, or None when one can. Only an
    OPEN plan is accepted by the backend for protective sells."""
    gaps = []
    for plan in plans:
        payload = plan.get("conditionPayload", plan.get("condition_payload"))
        status = plan.get("status")
        reject_reason = plan.get("rejectReason")
        managed = plan.get("managedQuantity")
        quantity = holding.get("quantity")
        if status == "WAITING_ENTRY":
            gaps.append("entry_fill_unrecorded")
        elif status != "OPEN":
            gaps.append(f"plan_status:{status}")
        elif not isinstance(payload, dict):
            gaps.append("plan_conditions_unreadable")
        elif reject_reason in BLOCKING_REJECTIONS:
            gaps.append(f"protection_blocked:{reject_reason}")
        elif any(isinstance(order, dict) and order.get("status") == "UNKNOWN" for order in plan.get("unresolvedOrders") or []):
            # Every trigger waits for this order's reconciliation, which needs an operator.
            gaps.append("protection_blocked:order_without_broker_id")
        elif not (plan.get("plannedExitAt") or any(payload.get(name) for name in PROTECTIVE_CONDITIONS)):
            gaps.append("plan_without_exit")
        elif type(managed) is int and type(quantity) is int and 0 <= managed < quantity:
            gaps.append(f"partially_managed:{managed}/{quantity}")
        else:
            return None
    return gaps[0] if gaps else "no_active_plan"


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
