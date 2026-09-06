"""Durable, atomic reservations for the single AI service's Luna API spend."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

from src.config.settings import get_data_dir

MODEL = "gpt-5.6-luna"
PRICE_VERSION = "2026-09-05"
NANODOLLARS = 1_000_000_000
INPUT_RATE = 200
CACHE_READ_RATE = 20
CACHE_WRITE_RATE = 250
OUTPUT_RATE = 1200


class LLMBudgetExceeded(RuntimeError):
    pass


class LLMBudgetAccountingError(RuntimeError):
    pass


def _tokens(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Token counts must be non-negative integers")
    return value


def maximum_cost(input_tokens: int, output_tokens: int) -> int:
    # Cache writes can cost 1.25x. Output already includes reasoning tokens.
    return _tokens(input_tokens) * CACHE_WRITE_RATE + _tokens(output_tokens) * OUTPUT_RATE


def actual_cost(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    inputs, outputs = _tokens(input_tokens), _tokens(output_tokens)
    cached, writes = _tokens(cached_tokens), _tokens(cache_write_tokens)
    if cached + writes > inputs:
        raise LLMBudgetAccountingError("Cached and cache-written tokens exceed input usage")
    return (inputs - cached - writes) * INPUT_RATE + cached * CACHE_READ_RATE + writes * CACHE_WRITE_RATE + outputs * OUTPUT_RATE


def _dollars(value: str | float | Decimal) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("LLM budget must be a positive finite USD amount")
    return int(amount * NANODOLLARS)


class LLMBudgetLedger:
    """UTC calendar-month ledger; unresolved old reservations remain committed."""

    def __init__(
        self,
        path: str | Path,
        *,
        monthly_limit_usd: str | float = "100",
        operating_target_usd: str | float = "90",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.monthly_limit = _dollars(monthly_limit_usd)
        self.operating_target = _dollars(operating_target_usd)
        if not self.operating_target <= self.monthly_limit <= 100 * NANODOLLARS:
            raise ValueError("Require operating target <= monthly budget <= USD 100")
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS llm_spend (
                    request_id TEXT PRIMARY KEY, month TEXT NOT NULL,
                    role TEXT NOT NULL, model TEXT NOT NULL, price_version TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','sent','unknown','settled','released')),
                    reserved_nano INTEGER NOT NULL, actual_nano INTEGER,
                    input_tokens INTEGER, output_tokens INTEGER,
                    cached_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat()

    def _totals(self, db: sqlite3.Connection, month: str) -> tuple[int, int, bool]:
        row = db.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN month=? AND state='settled' THEN actual_nano ELSE 0 END),0) AS spent,
                COALESCE(SUM(CASE WHEN state IN ('reserved','sent','unknown') THEN reserved_nano ELSE 0 END),0) AS reserved,
                COALESCE(MAX(CASE WHEN actual_nano > reserved_nano THEN 1 ELSE 0 END),0) AS overrun
                FROM llm_spend""",
            (month,),
        ).fetchone()
        return row["spent"], row["reserved"], bool(row["overrun"])

    def reserve(self, role: str, input_tokens: int, output_tokens: int, *, critical: bool = False) -> str:
        timestamp = self._timestamp()
        month = timestamp[:7]
        cost = maximum_cost(input_tokens, output_tokens)
        request_id = uuid.uuid4().hex
        with self._transaction() as db:
            spent, reserved, overrun = self._totals(db, month)
            if overrun:
                raise LLMBudgetAccountingError("Observed usage exceeded a reservation; reconcile the ledger before further calls")
            limit = self.monthly_limit if critical else self.operating_target
            if spent + reserved + cost > limit:
                raise LLMBudgetExceeded(f"LLM {month} budget exhausted: {(spent + reserved) / NANODOLLARS:.6f} committed USD, {cost / NANODOLLARS:.6f} requested, {limit / NANODOLLARS:.2f} limit")
            db.execute(
                """INSERT INTO llm_spend
                (request_id,month,role,model,price_version,state,reserved_nano,created_at,updated_at)
                VALUES (?,?,?,?,?,'reserved',?,?,?)""",
                (request_id, month, role, MODEL, PRICE_VERSION, cost, timestamp, timestamp),
            )
        return request_id

    def _transition(self, request_id: str, state: str, allowed: tuple[str, ...]) -> None:
        with self._transaction() as db:
            row = db.execute("SELECT state FROM llm_spend WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["state"] not in allowed:
                raise LLMBudgetAccountingError(f"Invalid spend transition to {state} for {request_id}")
            db.execute("UPDATE llm_spend SET state=?,updated_at=? WHERE request_id=?", (state, self._timestamp(), request_id))

    def mark_sent(self, request_id: str) -> None:
        self._transition(request_id, "sent", ("reserved",))

    def mark_unknown(self, request_id: str) -> None:
        self._transition(request_id, "unknown", ("sent",))

    def release_unsent(self, request_id: str) -> None:
        self._transition(request_id, "released", ("reserved",))

    def settle(
        self,
        request_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        cost = actual_cost(input_tokens, output_tokens, cached_tokens, cache_write_tokens)
        if _tokens(reasoning_tokens) > output_tokens:
            raise LLMBudgetAccountingError("Reasoning usage exceeds total output usage")
        timestamp = self._timestamp()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM llm_spend WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise LLMBudgetAccountingError("Unknown reservation")
            if row["state"] == "settled" and row["actual_nano"] == cost:
                return
            if row["state"] not in ("sent", "unknown"):
                raise LLMBudgetAccountingError("Only sent or unresolved requests can be settled")
            db.execute(
                """UPDATE llm_spend SET state='settled', month=?, actual_nano=?, input_tokens=?, output_tokens=?,
                cached_tokens=?,cache_write_tokens=?,reasoning_tokens=?,updated_at=? WHERE request_id=?""",
                (timestamp[:7],cost,input_tokens,output_tokens,cached_tokens,cache_write_tokens,reasoning_tokens,timestamp,request_id),
            )
        if cost > row["reserved_nano"]:
            raise LLMBudgetAccountingError("Observed usage exceeded the reserved maximum; further calls are blocked")

    def snapshot(self) -> dict:
        month = self._timestamp()[:7]
        with self._connection() as db:
            spent, reserved, overrun = self._totals(db, month)
            unknown = db.execute("SELECT COUNT(*) FROM llm_spend WHERE state IN ('sent','unknown')").fetchone()[0]
        return {
            "month": month, "timezone": "UTC", "model": MODEL, "price_version": PRICE_VERSION,
            "monthly_limit_usd": self.monthly_limit / NANODOLLARS,
            "operating_target_usd": self.operating_target / NANODOLLARS,
            "spent_usd": spent / NANODOLLARS, "reserved_usd": reserved / NANODOLLARS,
            "remaining_usd": max(0, self.monthly_limit - spent - reserved) / NANODOLLARS,
            "operating_remaining_usd": max(0, self.operating_target - spent - reserved) / NANODOLLARS,
            "unresolved_requests": unknown, "accounting_blocked": overrun,
        }


@lru_cache(maxsize=8)
def _ledger(path: str, monthly: str, operating: str) -> LLMBudgetLedger:
    return LLMBudgetLedger(path, monthly_limit_usd=monthly, operating_target_usd=operating)


def get_llm_budget() -> LLMBudgetLedger:
    path = os.getenv("HQA_LLM_BUDGET_PATH", str(get_data_dir() / "llm_budget.sqlite3"))
    if not path.strip():
        raise ValueError("HQA_LLM_BUDGET_PATH must not be blank")
    return _ledger(path, os.getenv("HQA_LLM_MONTHLY_BUDGET_USD", "100"), os.getenv("HQA_LLM_OPERATING_TARGET_USD", "90"))
