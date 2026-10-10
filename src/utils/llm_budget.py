"""Durable, atomic reservations for the single AI service's model API spend."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator, Sequence

from src.config.settings import get_data_dir

MODEL = "gpt-5.6-luna"
PRICE_VERSION = "2026-10-10"
NANODOLLARS = 1_000_000_000


class LLMBudgetExceeded(RuntimeError):
    pass


class LLMBudgetAccountingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelPrice:
    """Nanodollars per token. A request whose prompt is above `long_context_tokens` bills at the long rates."""

    input: int
    cache_read: int
    cache_write: int
    output: int
    long_context_tokens: int | None = None
    long_input: int = 0
    long_cache_read: int = 0
    long_cache_write: int = 0
    long_output: int = 0

    def rates(self, prompt_tokens: int) -> tuple[int, int, int, int]:
        if self.long_context_tokens is not None and prompt_tokens > self.long_context_tokens:
            return self.long_input, self.long_cache_read, self.long_cache_write, self.long_output
        return self.input, self.cache_read, self.cache_write, self.output


# Claude list prices in USD per million tokens (2026-10): Opus 5.5 4 in / 20 out, cache reads 0.20,
# 5-minute cache writes 5; Opus 5 and Opus 4.8, the models a declined request falls back to, 5 / 25,
# 0.50, 6.25; Sonnet 5.5 2 / 10, 0.20, 2.50; Haiku 5.5 0.10 / 0.50 up to 100K prompt tokens and
# 0.50 / 2.50 above. HQA never asks Claude to cache, and Haiku's cache prices are not published, so
# its cache rates here are upper bounds (cache reads at the input rate, writes at twice it).
PRICES = {
    MODEL: ModelPrice(input=200, cache_read=20, cache_write=250, output=1200),
    "claude-opus-5-5": ModelPrice(input=4000, cache_read=200, cache_write=5000, output=20000),
    "claude-opus-5": ModelPrice(input=5000, cache_read=500, cache_write=6250, output=25000),
    "claude-opus-4-8": ModelPrice(input=5000, cache_read=500, cache_write=6250, output=25000),
    "claude-sonnet-5-5": ModelPrice(input=2000, cache_read=200, cache_write=2500, output=10000),
    "claude-haiku-5-5": ModelPrice(input=100, cache_read=100, cache_write=200, output=500,
                                   long_context_tokens=100_000, long_input=500, long_cache_read=500,
                                   long_cache_write=1000, long_output=2500),
}


def _tokens(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Token counts must be non-negative integers")
    return value


def model_price(model: str) -> ModelPrice:
    price = PRICES.get(model)
    if price is None:
        raise LLMBudgetAccountingError(f"No price is recorded for model {model!r}; update the price table first")
    return price


def maximum_cost(input_tokens: int, output_tokens: int, model: str = MODEL) -> int:
    # Cache writes can cost more than plain input. Output already includes reasoning tokens.
    inputs, outputs = _tokens(input_tokens), _tokens(output_tokens)
    rate_input, _, rate_write, rate_output = model_price(model).rates(inputs)
    return inputs * max(rate_input, rate_write) + outputs * rate_output


def actual_cost(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    model: str = MODEL,
) -> int:
    """`input_tokens` is the whole prompt, including cache reads and cache writes."""
    inputs, outputs = _tokens(input_tokens), _tokens(output_tokens)
    cached, writes = _tokens(cached_tokens), _tokens(cache_write_tokens)
    if cached + writes > inputs:
        raise LLMBudgetAccountingError("Cached and cache-written tokens exceed input usage")
    rate_input, rate_read, rate_write, rate_output = model_price(model).rates(inputs)
    return (inputs - cached - writes) * rate_input + cached * rate_read + writes * rate_write + outputs * rate_output


@dataclass(frozen=True)
class Attempt:
    """Usage of one billed attempt of a request; `input_tokens` include cache reads and writes."""

    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    def cost(self) -> int:
        return actual_cost(self.input_tokens, self.output_tokens, self.cached_tokens, self.cache_write_tokens, self.model)


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
            columns = {row[1] for row in db.execute("PRAGMA table_info(llm_spend)")}
            for column in ("reviewed_at", "review_note"):
                if column not in columns:
                    # Operator review of an overrun; existing ledgers are migrated in place.
                    db.execute(f"ALTER TABLE llm_spend ADD COLUMN {column} TEXT")

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
                COALESCE(MAX(CASE WHEN actual_nano > reserved_nano AND reviewed_at IS NULL THEN 1 ELSE 0 END),0) AS overrun
                FROM llm_spend""",
            (month,),
        ).fetchone()
        return row["spent"], row["reserved"], bool(row["overrun"])

    def reserve(self, role: str, input_tokens: int, output_tokens: int, *, critical: bool = False,
                model: str = MODEL, fallback_models: Sequence[str] = ()) -> str:
        """`fallback_models` are models the provider may re-run a declined request on inside the
        same call; a declined attempt can still bill, so each is reserved at its own maximum."""
        timestamp = self._timestamp()
        month = timestamp[:7]
        cost = sum(maximum_cost(input_tokens, output_tokens, name) for name in (model, *fallback_models))
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
                (request_id, month, role, model, PRICE_VERSION, cost, timestamp, timestamp),
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
        """Settle from provider usage priced at the reserved model's rates; `input_tokens` is the
        whole prompt, including cache reads and writes."""
        self._settle(request_id, lambda model: [Attempt(model, input_tokens, output_tokens, cached_tokens,
                                                        cache_write_tokens)], reasoning_tokens)

    def settle_attempts(self, request_id: str, attempts: Sequence[Attempt], *, reasoning_tokens: int = 0) -> None:
        """Settle a request the provider billed as several attempts, possibly on different models
        (a Claude server-side fallback); each attempt is priced at its own model's rates."""
        attempts = list(attempts)
        if not attempts:
            raise LLMBudgetAccountingError("Settlement requires at least one billed attempt")
        self._settle(request_id, lambda _model: attempts, reasoning_tokens)

    def _settle(self, request_id: str, attempts_for: Callable[[str], list[Attempt]], reasoning_tokens: int) -> None:
        timestamp = self._timestamp()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM llm_spend WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise LLMBudgetAccountingError("Unknown reservation")
            attempts = attempts_for(row["model"])
            cost = sum(attempt.cost() for attempt in attempts)
            input_tokens = sum(attempt.input_tokens for attempt in attempts)
            output_tokens = sum(attempt.output_tokens for attempt in attempts)
            cached_tokens = sum(attempt.cached_tokens for attempt in attempts)
            cache_write_tokens = sum(attempt.cache_write_tokens for attempt in attempts)
            if _tokens(reasoning_tokens) > output_tokens:
                raise LLMBudgetAccountingError("Reasoning usage exceeds total output usage")
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

    def unresolved(self) -> list[dict]:
        """Requests still holding budget: 'sent'/'unknown' ones are settled from provider
        usage; a 'reserved' one was never sent (its process stopped first) and is released."""
        with self._connection() as db:
            rows = db.execute("""SELECT request_id, month, role, model, state, reserved_nano, created_at, updated_at
                                 FROM llm_spend WHERE state IN ('reserved','sent','unknown') ORDER BY created_at""").fetchall()
        return [{**dict(row), "reserved_usd": row["reserved_nano"] / NANODOLLARS} for row in rows]

    def overruns(self) -> list[dict]:
        """Settled requests that cost more than their reservation and are not yet reviewed."""
        with self._connection() as db:
            rows = db.execute("""SELECT request_id, month, role, reserved_nano, actual_nano, updated_at FROM llm_spend
                                 WHERE actual_nano > reserved_nano AND reviewed_at IS NULL ORDER BY updated_at""").fetchall()
        return [{**dict(row), "reserved_usd": row["reserved_nano"] / NANODOLLARS,
                 "actual_usd": row["actual_nano"] / NANODOLLARS} for row in rows]

    def acknowledge_overrun(self, request_id: str, note: str) -> None:
        """Record operator review of an overrun (e.g. corrected pricing) and lift the accounting block."""
        if not note or not note.strip():
            raise ValueError("An overrun review requires a note explaining the correction")
        timestamp = self._timestamp()
        with self._transaction() as db:
            row = db.execute("SELECT actual_nano, reserved_nano, reviewed_at FROM llm_spend WHERE request_id=?",
                             (request_id,)).fetchone()
            if row is None or row["actual_nano"] is None or row["actual_nano"] <= row["reserved_nano"]:
                raise LLMBudgetAccountingError(f"No overrun recorded for {request_id}")
            if row["reviewed_at"] is not None:
                return
            db.execute("UPDATE llm_spend SET reviewed_at=?, review_note=?, updated_at=? WHERE request_id=?",
                       (timestamp, note.strip()[:500], timestamp, request_id))

    def snapshot(self) -> dict:
        month = self._timestamp()[:7]
        with self._connection() as db:
            spent, reserved, overrun = self._totals(db, month)
            unknown = db.execute("SELECT COUNT(*) FROM llm_spend WHERE state IN ('reserved','sent','unknown')").fetchone()[0]
            models = [row[0] for row in db.execute("SELECT DISTINCT model FROM llm_spend WHERE month=? ORDER BY model", (month,))]
        return {
            "month": month, "timezone": "UTC", "models": models, "price_version": PRICE_VERSION,
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
