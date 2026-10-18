"""Backend internal API client for the strategy runtime (batches, status, account snapshot).

Configuration is read from the process environment at call time; .env files are never read.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

TOKEN_HEADER = "X-HQA-Internal-Token"
# A deduplicated response carries the stored batch's current status.
BATCH_STATUSES = ("VALIDATED", "QUEUED", "REJECTED", "SUBMITTING", "SUBMITTED", "PARTIAL", "COMPLETED", "HALTED")
SNAPSHOT_MAX_AGE = timedelta(seconds=60)


class BackendError(RuntimeError):
    """The backend is not configured or returned an unusable response."""


@dataclass(frozen=True)
class AccountState:
    user_id: str
    equity: float
    cash: float
    holdings: dict
    sellable: dict
    captured_at: datetime


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BackendError(f"account snapshot {name} must be a finite number")
    if value < 0 or positive and value == 0:
        raise BackendError(f"account snapshot {name} is out of range")
    return float(value)


def parse_account_snapshot(body, user_id, *, now=None) -> AccountState:
    rows = body.get("snapshots") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != 1:
        raise BackendError("account snapshot response must contain exactly one snapshot")
    row = rows[0]
    if not isinstance(row, dict) or row.get("userId") != user_id:
        raise BackendError("account snapshot response does not match the requested user")
    if row.get("success") is not True:
        raise BackendError(f"account_snapshot_failed:{user_id}:{row.get('error')}")
    if row.get("accountMode") != "PAPER":
        raise BackendError("strategy runtime accepts PAPER accounts only")
    try:
        captured = datetime.fromisoformat(str(row.get("capturedAt")).replace("Z", "+00:00"))
    except ValueError:
        raise BackendError("account snapshot capturedAt is not ISO 8601") from None
    if captured.tzinfo is None:
        raise BackendError("account snapshot capturedAt lacks a timezone")
    age = (now or datetime.now(UTC)) - captured
    if age < -timedelta(seconds=5) or age > SNAPSHOT_MAX_AGE:
        raise BackendError(f"stale_account_snapshot:{user_id}")
    holdings, sellable = {}, {}
    for item in row.get("holdings") or []:
        code = item.get("stockCode") if isinstance(item, dict) else None
        qty, free = (item.get("quantity"), item.get("sellableQuantity")) if isinstance(item, dict) else (None, None)
        if (not isinstance(code, str) or not code or code in holdings
                or isinstance(qty, bool) or not isinstance(qty, int) or qty < 0
                or isinstance(free, bool) or not isinstance(free, int) or not 0 <= free <= qty):
            raise BackendError(f"invalid holding in account snapshot: {code}")
        if qty:
            holdings[code], sellable[code] = qty, free
    return AccountState(user_id, _number(row.get("equity"), "equity", positive=True),
                        _number(row.get("orderableCash"), "orderableCash"), holdings, sellable, captured)


def backend_configured(env=None) -> bool:
    env = os.environ if env is None else env
    return bool((env.get("BACKEND_INTERNAL_BASE_URL") or env.get("BACKEND_BASE_URL"))
                and (env.get("HQA_INTERNAL_TOKEN") or "").strip())


class StrategyBackendClient:
    def __init__(self, base_url=None, token=None, *, timeout=30, session=None):
        url = base_url or os.environ.get("BACKEND_INTERNAL_BASE_URL") or os.environ.get("BACKEND_BASE_URL")
        if not url:
            raise BackendError("BACKEND_INTERNAL_BASE_URL (or BACKEND_BASE_URL) is required")
        secret = (token if token is not None else os.environ.get("HQA_INTERNAL_TOKEN", "")).strip()
        if not secret:
            raise BackendError("HQA_INTERNAL_TOKEN is required")
        self.base_url = url.rstrip("/")
        self._token = secret
        self.timeout = timeout
        self.session = session or requests

    def _headers(self):
        return {TOKEN_HEADER: self._token}

    def _json(self, response):
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            raise BackendError(f"backend HTTP error: {error}") from None
        try:
            return response.json()
        except ValueError:
            raise BackendError("backend response is not JSON") from None

    def _post(self, path, body):
        return self._json(self.session.post(self.base_url + path, json=body, headers=self._headers(),
                                            timeout=self.timeout))

    def submit_batch(self, payload: dict) -> dict:
        """A rejection may arrive as an HTTP error with a batch body; it is returned, not raised."""
        response = self.session.post(self.base_url + "/api/v1/internal/strategies/batches", json=payload,
                                     headers=self._headers(), timeout=self.timeout)
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400 and not (isinstance(body, dict) and body.get("status") == "REJECTED"):
            return self._json(response)  # raises BackendError with the HTTP status
        if not isinstance(body, dict) or body.get("status") not in BATCH_STATUSES:
            raise BackendError("unexpected batch response")
        return body

    def get_batch(self, batch_id: str) -> dict:
        return self._get(f"/api/v1/internal/strategies/batches/{batch_id}")

    def publish_status(self, strategy_id: str, generated_at: str, payload: dict) -> dict:
        body = self._post("/api/v1/internal/strategies/status",
                          {"strategyId": strategy_id, "generatedAt": generated_at, "payload": payload})
        if not isinstance(body, dict) or body.get("stored") is not True:
            raise BackendError("status was not stored")
        return body

    def _get(self, path):
        return self._json(self.session.get(self.base_url + path, headers=self._headers(), timeout=self.timeout))

    def unknown_orders(self) -> list:
        """Strategy orders whose broker acceptance is uncertain (never resent by the backend)."""
        body = self._get("/api/v1/internal/strategies/orders/unknown")
        if not isinstance(body, dict) or not isinstance(body.get("orders"), list):
            raise BackendError("unexpected unknown-orders response")
        return body["orders"]

    def risk(self, user_id: str) -> dict:
        """Risk state of a strategy account (the backend reads a fresh PAPER balance)."""
        body = self._get(f"/api/v1/internal/strategies/risk/{user_id}")
        if not isinstance(body, dict) or "buyAllowed" not in body:
            raise BackendError("unexpected risk response")
        return body

    def fetch_account(self, user_id: str) -> AccountState:
        body = self._post("/api/v1/internal/trading/account-snapshots", {"userIds": [user_id]})
        return parse_account_snapshot(body, user_id)
