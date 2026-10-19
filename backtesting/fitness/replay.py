"""Run the unchanged PAPER analysis at a past decision time against a simulated account.

`SharedAnalysisService` takes its data, account client, role models and clock as arguments, so a
replay changes none of its code: the data is the point-in-time copy (`pit_data`), the account and
quotes come from the simulated portfolio, the clock is the decision time, and each role model is
the PAPER model behind `GuardedModel`, which

- answers a repeated request from a content-hash cache (role, model settings, system prompt,
  payload and schema), so no specialist or RiskManager call is ever paid twice;
- refuses to call outside the allowed hours (never 08:30-15:45 KST on a KRX trading day), beyond
  the run's call cap, or after any usage-limit error, which stops every later call.

`DryModel` answers without any LLM, to count the calls a schedule needs before spending anything.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time as clock_time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from src.runner.analysis_contracts import AccountDecision, AccountSnapshot, SpecialistResult

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
# PAPER's first full cycle after the open; the day's open stands in for the 09:15 KIS quote.
DECISION_TIME = time(9, 15)
# Never call while PAPER may be analysing: from the pre-open check to after the close.
BLOCKED_START, BLOCKED_END = time(8, 30), time(15, 45)
LIMIT_MARKERS = ("usage limit", "hit your limit", "limit reached", "limit will reset", "out of extra usage",
                 "rate limit", "429")

# The PAPER observation account's analysis target (user dohoon, strategy short, every theme).
DOHOON_TARGET = {
    "userId": "dohoon", "strategyProfile": "short", "themeKeys": [], "excludeThemeKeys": [], "constraints": {},
    "investorProfile": {"investment_experience": "INTERMEDIATE", "investment_goal": "ASSET_GROWTH",
                        "investment_period_months": 12, "investment_type": "AGGRESSIVE", "leverage_allowed": False,
                        "loss_action": "SEEK_ADVICE", "loss_tolerance": "LEVEL_2", "monthly_investment": 0,
                        "occupation_type": "EMPLOYEE", "target_return_rate": 8, "total_assets": 0,
                        "volatility_tolerance": "VERY_HIGH"},
}


def decision_time(day: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(day).date(), DECISION_TIME, tzinfo=KST)


class UsageLimitStop(RuntimeError):
    """A usage-limit error was seen; no further call may be made in this run."""


class CallRefused(RuntimeError):
    """The guard refused a call (hours, call cap or an earlier stop)."""


@dataclass
class CallGuard:
    """Admits paid calls within the run's limits. A refusal inside a cycle is swallowed by the live
    service like any failed call, so the replay checks `refused` after every cycle and discards a
    day that lost calls; `room_for_day` stops a run cleanly before a day it cannot finish."""
    max_calls: int
    allow_market_hours: bool = False
    deadline: datetime | None = None  # no new paid call after this time
    token_budget: int | None = None  # input + output tokens of paid calls
    calls: int = 0
    tokens: int = 0
    refused: int = 0
    stopped: str | None = None
    lock: Any = field(default_factory=threading.Lock)

    def admit(self, role: str) -> None:
        with self.lock:
            reason = self._refusal()
            if reason:
                self.refused += 1
                raise CallRefused(reason)
            self.calls += 1

    def _refusal(self) -> str | None:
        now = datetime.now(KST)
        if self.stopped:
            return f"run stopped: {self.stopped}"
        if not self.allow_market_hours and in_blocked_hours(now):
            self.stopped = "KRX session hours (08:30-15:45 KST on a trading day)"
            return self.stopped
        if self.deadline is not None and now >= self.deadline:
            return f"deadline {self.deadline.isoformat()} passed"
        if self.calls >= self.max_calls:
            return f"call cap {self.max_calls} reached"
        if self.token_budget is not None and self.tokens >= self.token_budget:
            return f"token budget {self.token_budget} spent"
        return None

    def record(self, tokens: int) -> None:
        with self.lock:
            self.tokens += int(tokens)

    def room_for_day(self, calls: int, tokens: int, minutes: float) -> str | None:
        """Why a day needing about this many calls, tokens and minutes should not start, or None."""
        with self.lock:
            if self.max_calls == 0:  # cache-only: any missing answer is refused and stops the day
                return self.stopped
            reason = self._refusal()
            if reason:
                return reason
            if self.calls + calls > self.max_calls:
                return f"call cap {self.max_calls} would be passed"
            if self.token_budget is not None and self.tokens + tokens > self.token_budget:
                return f"token budget {self.token_budget} would be passed"
            if self.deadline is not None and datetime.now(KST) + timedelta(minutes=minutes) > self.deadline:
                return f"deadline {self.deadline.isoformat()} would be passed"
            return None

    def stop(self, reason: str) -> None:
        with self.lock:
            self.stopped = self.stopped or reason


def in_blocked_hours(now: datetime) -> bool:
    from src.runner.trading_calendar import is_trading_day

    local = now.astimezone(KST)
    return is_trading_day(local.date().isoformat()) and BLOCKED_START <= local.time() < BLOCKED_END


class CallCache:
    """Structured outputs by request hash, in SQLite; written only after a schema-valid answer."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, role TEXT NOT NULL, "
                       "created_at TEXT NOT NULL, raw TEXT NOT NULL, meta TEXT NOT NULL)")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def get(self, key: str) -> str | None:
        with self.lock, self._db() as db:
            row = db.execute("SELECT raw FROM calls WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, role: str, raw: str, meta: dict) -> None:
        with self.lock, self._db() as db:
            db.execute("INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?)",
                       (key, role, datetime.now(UTC).isoformat(), raw, json.dumps(meta, ensure_ascii=False)))

    def rows(self) -> list[dict]:
        with self.lock, self._db() as db:
            return [{"key": k, "role": r, "created_at": c, "meta": json.loads(m)}
                    for k, r, c, m in db.execute("SELECT key, role, created_at, meta FROM calls ORDER BY created_at")]


def canonical_payload(text: str) -> str:
    """The payload with sorted keys. The live service fills each candidate's specialist results in
    the order the calls finish, so equal RiskManager payloads come in different key orders."""
    return json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True, allow_nan=False)


def request_key(role: str, settings: dict, schema: Any, messages: list, *, canonical: bool = True) -> str:
    """Content hash of one request. `canonical=False` gives the first keys, which hashed the text
    as sent; they are still read so earlier answers are reused."""
    body = {"role": role, "settings": settings, "schema": schema.model_json_schema(),
            "messages": [[kind, canonical_payload(text) if canonical and kind == "human" else text]
                         for kind, text in messages]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class CallLog:
    """What every role model did in this process: hits, paid calls, failures, timings."""
    events: list[dict] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock)

    def add(self, **event: Any) -> None:
        with self.lock:
            self.events.append({"at": datetime.now(UTC).isoformat(), **event})


class _RoleModelBase:
    """The attributes `SharedAnalysisService` reads for cache keys, budgets and manifests."""

    def __init__(self, role: str, *, model_name: str, effort: str, max_tokens: int, input_limit: int):
        self.role = role
        self.model_name = model_name
        self.model = model_name
        self.reasoning_effort = effort
        self.max_tokens = max_tokens
        self.hqa_input_limit = input_limit
        self.num_ctx = None

    @property
    def settings(self) -> dict:
        return {"model": self.model_name, "effort": self.reasoning_effort, "max_tokens": self.max_tokens,
                "input_limit": self.hqa_input_limit}

    def with_structured_output(self, schema: Any, *, method: str = "json_schema", strict: bool | None = None):
        model = self

        class _Runnable:
            def invoke(self, messages: list):
                return model._call(schema, [(kind, text) for kind, text in messages], method, strict)

        return _Runnable()

    def _call(self, schema, messages, method, strict):  # pragma: no cover - overridden
        raise NotImplementedError


class SkippedCall(RuntimeError):
    """A specialist call that cannot change any decision was not made (see `GuardedModel.skip`)."""


class GuardedModel(_RoleModelBase):
    """`salt` > 0 asks again for a request already answered (a replicate for nondeterminism);
    `skip(role, payload)` may decline a call whose answer no decision can use."""

    def __init__(self, inner: Any, role: str, cache: CallCache, guard: CallGuard, log: CallLog, *, salt: int = 0,
                 skip: Callable[[str, dict], bool] | None = None):
        super().__init__(role, model_name=inner.model_name, effort=inner.reasoning_effort,
                         max_tokens=inner.max_tokens, input_limit=inner.hqa_input_limit)
        self.inner, self.cache, self.guard, self.log, self.salt, self.skip = inner, cache, guard, log, salt, skip

    @property
    def settings(self) -> dict:
        return {**super().settings, **({"replicate": self.salt} if self.salt else {})}

    def _call(self, schema, messages, method, strict):
        key = request_key(self.role, self.settings, schema, messages)
        cached = self.cache.get(key)
        if cached is None:
            legacy = request_key(self.role, self.settings, schema, messages, canonical=False)
            cached = self.cache.get(legacy)
            if cached is not None:
                self.cache.put(key, self.role, cached, {"migrated_from": legacy})
        if cached is not None:
            self.log.add(role=self.role, key=key, kind="cache_hit")
            return schema.model_validate_json(cached)
        if self.skip is not None and self.skip(self.role, json.loads(messages[-1][1])):
            self.log.add(role=self.role, key=key, kind="skipped")
            raise SkippedCall(f"{self.role} answer cannot change a decision")
        self.guard.admit(self.role)
        started = clock_time.monotonic()
        try:
            result = self.inner.with_structured_output(schema, include_raw=True, method=method,
                                                       strict=strict).invoke(messages)
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            if type(exc).__name__ == "LLMBudgetExceeded" or any(marker in text.lower() for marker in LIMIT_MARKERS):
                self.guard.stop("usage limit: " + text[:200])
                self.log.add(role=self.role, key=key, kind="usage_limit", error=text[:300])
                raise UsageLimitStop(text[:300]) from exc
            self.log.add(role=self.role, key=key, kind="failure", error=text[:300],
                         seconds=round(clock_time.monotonic() - started, 2))
            raise
        raw = result["raw"]
        meta = {"usage": dict(getattr(raw, "usage_metadata", None) or {}),
                "response": {name: value for name, value in (getattr(raw, "response_metadata", None) or {}).items()
                             if name in {"model_name", "served_models", "stop_reason", "num_turns", "duration_ms",
                                         "api_equivalent_cost_usd"}},
                "seconds": round(clock_time.monotonic() - started, 2), "settings": self.settings}
        if result["parsing_error"] is not None:
            self.log.add(role=self.role, key=key, kind="failure", error=f"parsing: {result['parsing_error']}"[:300], **meta)
            raise result["parsing_error"]
        self.cache.put(key, self.role, raw.content, meta)
        self.guard.record((meta["usage"].get("input_tokens") or 0) + (meta["usage"].get("output_tokens") or 0))
        self.log.add(role=self.role, key=key, kind="paid", **meta)
        return result["parsed"]


class DryModel(_RoleModelBase):
    """Valid, uninformative answers without any LLM: every specialist scores 50 and the
    RiskManager holds every row. Used to count a schedule's calls and check the plumbing."""

    def __init__(self, role: str, settings: dict, log: CallLog, seen: set[str]):
        super().__init__(role, model_name=settings["model"], effort=settings["effort"],
                         max_tokens=settings["max_tokens"], input_limit=settings["input_limit"])
        self.log, self.seen = log, seen

    def _call(self, schema, messages, method, strict):
        key = request_key(self.role, self.settings, schema, messages)
        payload = json.loads(messages[-1][1])
        first = key not in self.seen
        self.seen.add(key)
        self.log.add(role=self.role, key=key, kind="dry_new" if first else "dry_repeat",
                     chars=len(messages[-1][1]) + len(messages[0][1]))
        if schema is SpecialistResult:
            return SpecialistResult(stock_code=payload["stock_code"], role=self.role, score=50.0, confidence=50,
                                    thesis="dry run", risks=[], data_gaps=[],
                                    citations=[{"source_id": payload["source_ids"][0], "claim": "dry run"}])
        if schema is AccountDecision:
            at = datetime.fromisoformat(payload["decision_as_of"])
            held = {h["stockCode"]: h for h in payload["account"]["holdings"]}
            plans = []
            for row in payload["candidates"]:
                holding = held.get(row["stock_code"])
                price = float(row["quote"]["current_price"])
                exits = ([{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": round(price * .9)}]}]
                         if holding else [])
                plans.append({"stock_code": row["stock_code"], "stock_name": row["stock_name"], "action": "HOLD",
                              "holding_quantity": holding["quantity"] if holding else 0, "confidence": 50,
                              "risk_level": "MEDIUM", "position_size_pct": 0.0, "entry_price": None,
                              "stop_loss_price": None, "take_profit_price": None,
                              "entry_valid_until": (at + timedelta(minutes=10)).isoformat(),
                              "planned_exit_at": (at + timedelta(days=1)).isoformat(),
                              "condition_payload": {"schema_version": 2, "entry_conditions": [], "exit_conditions": exits,
                                                    "reduce_conditions": [], "invalidation_conditions": []},
                              "citations": [{"source_id": row["source_ids"][0], "claim": "dry run"}],
                              "reasoning": "dry run"})
            return AccountDecision.model_validate({"plans": plans, "reasoning": "dry run"})
        raise ValueError(f"unexpected schema {schema}")


def paper_role_settings() -> dict[str, dict]:
    """The model settings PAPER's claude_plan roles use, read from the live configuration."""
    from src.agents.llm_config import _claude_effort, _claude_model, get_role_limits

    return {role: {"model": _claude_model(role), "effort": _claude_effort(role),
                   "max_tokens": get_role_limits(role, "claude_plan").output_tokens,
                   "input_limit": get_role_limits(role, "claude_plan").input_tokens}
            for role in ("analyst", "quant", "chartist", "risk_manager")}


def paper_role_models() -> dict[str, Any]:
    from src.agents.llm_config import get_analyst_llm, get_chartist_llm, get_quant_llm, get_risk_manager_llm

    return {"analyst": get_analyst_llm(), "quant": get_quant_llm(), "chartist": get_chartist_llm(),
            "risk_manager": get_risk_manager_llm()}


class SimAccounts:
    """The backend's account-snapshot and quote endpoints, answered from the simulated portfolio.

    `state(as_of)` returns (account fields, holdings, quotes by code); quotes are the decision day's
    open, standing in for the KIS current price PAPER reads at 09:15."""

    def __init__(self, user_id: str, state: Callable[[datetime], tuple[dict, list[dict], dict[str, float]]],
                 clock: Callable[[], datetime]):
        self.user_id, self.state, self.clock = user_id, state, clock

    def fetch_accounts(self, user_ids: list[str]) -> dict[str, AccountSnapshot]:
        if list(user_ids) != [self.user_id]:
            raise ValueError("the replay simulates one account")
        at = self.clock()
        fields, holdings, _ = self.state(at)
        snapshot = AccountSnapshot.model_validate({
            "userId": self.user_id, "accountMode": "PAPER", "success": True, "capturedAt": at.isoformat(),
            "source": "kis", "maxPositionPct": 20.0, "dailyBuyLimit": 20.0,
            "orderableCashSource": "deposit_upper_bound; per_order_purchasing_power_checked_at_entry",
            "reservedCash": 0.0, "dailyPnlBaselineSource": "kis_previous_day_assets", "entryEligible": True,
            "entryBlockReason": None, "monitorCapacity": 10, "monitorCapacityExceeded": False,
            "holdings": holdings, **fields})
        return {self.user_id: snapshot}

    def fetch_prices(self, user_id: str, stock_codes: list[str]) -> dict[str, dict]:
        at = self.clock()
        _, _, quotes = self.state(at)
        result = {}
        for code in stock_codes:
            price = quotes.get(code)
            if price is None:
                result[code] = {"error": f"price_snapshot_missing:{code}"}
                continue
            result[code] = {"source_id": f"quote:{code}:{at.astimezone(UTC).isoformat()}",
                            "current_price": int(price) if float(price).is_integer() else float(price),
                            "available_at": at.astimezone(UTC).isoformat(), "source": "kis"}
        return result


class ReplayRunner:
    """One PAPER analysis cycle per decision day, on the point-in-time data."""

    def __init__(self, *, data: Any, models: dict[str, Any], state: Callable, audit_path: Path | None = None,
                 target: dict | None = None, max_workers: int = 6):
        from src.runner.shared_analysis import SharedAnalysisService

        self.target = dict(target or DOHOON_TARGET)
        self.now: datetime | None = None
        self.accounts = SimAccounts(self.target["userId"], state, lambda: self.now)
        audit = None
        if audit_path is not None:
            from src.tracing.paper_audit import PaperAudit
            audit = PaperAudit(audit_path)
        self.service = SharedAnalysisService(data=data, accounts=self.accounts, models=models,
                                             max_workers=max_workers, clock=lambda: self.now, audit=audit)
        # Every specialist answer of a cycle by (stock, role), including answers the service's own
        # cache supplied, for the per-stock score study.
        self.specialists: dict[str, dict] = {}
        self._lock = threading.Lock()
        inner = self.service._specialist

        def recorded(role, payload, critical, priority=None):
            try:
                result = inner(role, payload, critical) if priority is None else inner(role, payload, critical, priority)
            except Exception as exc:
                with self._lock:
                    self.specialists.setdefault(payload["stock_code"], {})[role] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
                raise
            with self._lock:
                self.specialists.setdefault(payload["stock_code"], {})[role] = {
                    "score": result.score, "confidence": result.confidence}
            return result

        self.service._specialist = recorded

    def run(self, day: str) -> dict:
        self.now = decision_time(day)
        self.specialists = {}
        return self.service._run_cycle([self.target], self.now)


def import_audit_answers(audit_path: Path, cache: CallCache, roles: set[str]) -> dict:
    """Store a replay audit's answers under canonical keys (requests and responses are paired by
    request id). A RiskManager answer is rebuilt as the model returned it: its valid plans and the
    plans the contract set aside."""
    settings = paper_role_settings()
    db = sqlite3.connect(str(audit_path))
    requests = {rid: json.loads(body) for rid, body in
                db.execute("SELECT id, payload FROM paper_events WHERE kind='llm_request'")}
    counts = {"imported": 0, "present": 0}
    for (body,) in db.execute("SELECT payload FROM paper_events WHERE kind='llm_response' ORDER BY id"):
        response = json.loads(body)
        request = requests.get(response["request_id"])
        if request is None or request["role"] not in roles:
            continue
        role = request["role"]
        schema = AccountDecision if role == "risk_manager" else SpecialistResult
        messages = [("system", request["instructions"]),
                    ("human", json.dumps(request["payload"], ensure_ascii=False, allow_nan=False))]
        key = request_key(role, settings[role], schema, messages)
        output = dict(response["output"])
        invalid = output.pop("invalid_plans", [])
        if role == "risk_manager":
            output["plans"] = output["plans"] + [row["plan"] for row in invalid]
        raw = json.dumps(output, ensure_ascii=False)
        schema.model_validate_json(raw)
        if cache.get(key) is not None:
            counts["present"] += 1
            continue
        cache.put(key, role, raw, {"imported_from": str(audit_path), "request_id": response["request_id"]})
        counts["imported"] += 1
    return counts
