"""One day's strategy-runtime work: status, plan, shadow-fill, publish-status and submit.

Every command is read-only unless execute=True. Submitting needs the effective stage
``paper`` and a bound account; the backend re-validates everything independently.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from src.runner import trading_calendar

from . import book, decisions, gates, rebalance, shadow

HISTORY_LIMIT = 120


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_data_dir(repo_root=None) -> Path:
    configured = os.environ.get("HQA_DATA_DIR")
    return Path(configured) if configured else Path(repo_root or default_repo_root()) / "data"


@dataclass(frozen=True)
class Context:
    repo_root: Path
    data_dir: Path
    entries: tuple
    approvals: tuple | None
    approvals_error: str | None


def load_context(repo_root=None, data_dir=None) -> Context:
    repo_root = Path(repo_root or default_repo_root())
    data_dir = Path(data_dir) if data_dir else default_data_dir(repo_root)
    entries = book.load_book(repo_root / book.BOOK_PATH)
    try:
        approvals, error = book.load_approvals(repo_root / book.APPROVALS_PATH), None
    except (OSError, ValueError) as exc:
        approvals, error = None, str(exc)
    return Context(repo_root, data_dir, entries, approvals, error)


def find_entry(ctx: Context, strategy_id: str):
    for entry in ctx.entries:
        if entry.strategy_id == strategy_id:
            return entry
    raise ValueError(f"unknown strategy: {strategy_id}")


def stage_for(ctx: Context, entry) -> gates.StageDecision:
    return gates.evaluate_stage(entry, repo_root=ctx.repo_root, data_dir=ctx.data_dir,
                                approvals=ctx.approvals, approvals_error=ctx.approvals_error)


def mode_for(stage: gates.StageDecision) -> str:
    return "shadow" if book.stage_rank(stage.effective) >= book.stage_rank("shadow") else "observe"


def _iso(day) -> str:
    return date.fromisoformat(str(day)[:10]).isoformat()


# ---------------------------------------------------------------- shadow state and planning


@dataclass(frozen=True)
class BookState:
    cash: float
    holdings: dict
    marks: dict
    state: dict
    source: str


def shadow_state(ctx: Context, entry, session) -> BookState:
    """State at the start of a session: the latest ledger entry strictly before it."""
    prior = None
    for item in shadow.read_ledger(ctx.data_dir, entry.strategy_id):
        if item["date"] < session:
            prior = item
    if prior is None:
        initial = {"baskets": []} if entry.execution == "basket_20" else {"last_decision_hash": None, "markets": {}}
        return BookState(float(entry.shadow_nav_krw), {}, {}, initial, "shadow_initial")
    return BookState(float(prior["cash_after"]), {c: int(q) for c, q in prior["holdings_after"].items()},
                     dict(prior.get("marks_close") or {}), prior["state_after"], f"shadow_ledger:{prior['date']}")


def _forward(ctx, entry):
    return [d for d in decisions.list_decisions(ctx.data_dir, entry.decision_rule, execution=entry.execution)
            if d.forward]


def due_phases(ctx: Context, entry, session) -> list[str]:
    session = _iso(session)
    phases = []
    if any(d.trade_date == session for d in _forward(ctx, entry)):
        phases.append("OPEN")
    if entry.execution == "basket_20":
        state = shadow_state(ctx, entry, session).state
        if any(b["exit_date"] <= session for b in state.get("baskets", [])):
            phases.append("CLOSE")
    return phases


def _previous_closes(ctx, session, codes, fallback):
    quotes = shadow.load_quotes(ctx.data_dir, shadow.previous_session(session)) or {}
    ref = {}
    for code in codes:
        q = quotes.get(code)
        if q is not None and q.verified and q.close is not None and q.close > 0:
            ref[code] = q.close
        elif fallback.get(code):
            ref[code] = fallback[code]
    return ref


def build_plan(ctx: Context, entry, session, phase, *, account=None) -> rebalance.RebalancePlan:
    """Plan for one session/phase from the shadow ledger state, or from an account snapshot."""
    session = _iso(session)
    if phase not in rebalance.PHASES:
        raise rebalance.PlanError(f"unknown phase: {phase}")
    book_state = shadow_state(ctx, entry, session)
    cash = account.cash if account is not None else book_state.cash
    holdings = dict(account.holdings) if account is not None else dict(book_state.holdings)
    version = entry.cost_model_version
    forward = _forward(ctx, entry)
    if phase == "OPEN":
        decision = next((d for d in forward if d.trade_date == session), None)
        if decision is None:
            raise rebalance.PlanError(f"no_forward_decision_for_session:{session}")
        if entry.execution == "basket_20":
            ref, markets, adv = shadow.plan_inputs(ctx.data_dir, decision)
            return rebalance.plan_basket_entry(entry.strategy_id, decision, cash=cash, ref_prices=ref,
                                               markets=markets, adv20=adv, model_version=version)
        ref, markets, adv = shadow.plan_inputs(ctx.data_dir, decision, extra_codes=holdings,
                                               fallback_marks=book_state.marks)
        for code in holdings:
            known = book_state.state.get("markets", {}).get(code)
            if code not in markets and known:
                markets[code] = known
        return rebalance.plan_continuous(entry.strategy_id, decision, holdings=holdings, cash=cash,
                                         ref_prices=ref, markets=markets, adv20=adv, model_version=version,
                                         band=entry.band)
    if entry.execution != "basket_20":
        raise rebalance.PlanError("continuous execution trades only at the OPEN")
    if account is None:
        due = [b for b in book_state.state.get("baskets", []) if b["exit_date"] <= session]
        if not due:
            raise rebalance.PlanError(f"no_basket_exit_due:{session}")
        if len(due) > 1:
            raise rebalance.PlanError("multiple_baskets_due_requires_manual_review")
        basket = due[0]
        ref = _previous_closes(ctx, session, basket["holdings"], book_state.marks)
        return rebalance.plan_basket_exit(
            entry.strategy_id, decision_hash=basket["decision_hash"], decision_date=basket["decision_date"],
            session_date=session, holdings=basket["holdings"], cash=cash, ref_prices=ref,
            markets=basket["markets"], adv20=basket["adv20"], model_version=version)
    decision, basket_holdings = account_exit(forward, holdings, session)
    ref = _previous_closes(ctx, session, basket_holdings, {})
    _, markets, adv = shadow.plan_inputs(ctx.data_dir, decision)
    return rebalance.plan_basket_exit(
        entry.strategy_id, decision_hash=decision.record_hash, decision_date=decision.decision_date,
        session_date=session, holdings=basket_holdings, cash=cash, ref_prices=ref, markets=markets,
        adv20=adv, model_version=version, targets=decision.targets)


def account_exit(forward, holdings, session):
    """Which forward basket the account's holdings belong to at a CLOSE on/after its exit date.

    Each held code belongs to the most recently entered forward decision containing it.
    A code whose owner is past its exit date (exit_date <= session, the shadow's
    exit_retry rule) is sold. A code held by an active basket that was entered before an
    earlier containing basket's exit is ambiguous and refused for manual review, as is a
    session where codes of more than one due basket are still held.
    """
    entered = [d for d in forward if d.trade_date <= session]
    owners = {}
    for code in sorted(holdings):
        containing = [d for d in entered if code in d.weights]
        if not containing:
            continue
        latest = max(containing, key=lambda d: d.trade_date)
        if any(d is not latest and d.exit_date <= session and latest.trade_date <= d.exit_date
               for d in containing):
            raise rebalance.PlanError(f"basket_overlap_requires_manual_review:{code}")
        if latest.exit_date <= session:
            owners[code] = latest
    due = {d.record_hash: d for d in owners.values()}
    if not due:
        raise rebalance.PlanError(f"no_forward_basket_exit_due:{session}")
    if len(due) > 1:
        raise rebalance.PlanError("multiple_baskets_due_requires_manual_review")
    (decision,) = due.values()
    return decision, {code: holdings[code] for code in owners}


def plan_path(data_dir, strategy_id, session, phase) -> Path:
    return Path(data_dir) / shadow.LIVE_DIR / strategy_id / "plans" / f"{_iso(session).replace('-', '')}_{phase}.json"


def plan_command(ctx: Context, strategy_id, session, phase=None, *, execute=False) -> dict:
    entry = find_entry(ctx, strategy_id)
    session = _iso(session)
    phases = [phase] if phase else due_phases(ctx, entry, session)
    result = {"strategyId": strategy_id, "sessionDate": session, "dryRun": not execute, "plans": [],
              "errors": []}
    if not phases:
        result["status"] = "no_action_due"
        return result
    for item in phases:
        try:
            plan = build_plan(ctx, entry, session, item)
        except (rebalance.PlanError, ValueError) as error:
            result["errors"].append({"phase": item, "error": str(error)})
            continue
        record = {"strategy_id": strategy_id, "source": "shadow_ledger", **plan.to_dict()}
        if execute:
            record, written = shadow.write_immutable(plan_path(ctx.data_dir, strategy_id, session, item), record,
                                                     ignore=())
            record = {**record, "_written": written}
        result["plans"].append(record)
    result["status"] = "error" if result["errors"] else "ok"
    return result


# ---------------------------------------------------------------- shadow fill


def shadow_fill_command(ctx: Context, session, strategy_id=None, *, execute=False, now=None) -> dict:
    session = _iso(session)
    entries = [find_entry(ctx, strategy_id)] if strategy_id else list(ctx.entries)
    results = []
    for entry in entries:
        stage = stage_for(ctx, entry)
        mode = mode_for(stage)
        try:
            record = shadow.shadow_fill(entry, session, ctx.data_dir, mode=mode, execute=execute, now=now)
        except shadow.ShadowRefused as error:
            if execute and error.kind in ("price_file_missing", "price_file_unreadable"):
                shadow.record_incident(ctx.data_dir, entry.strategy_id, session, error.kind, error.detail)
            results.append({"strategyId": entry.strategy_id, "status": "refused", "reason": error.kind,
                            "error": str(error)})
            continue
        except shadow.LedgerConflict as error:
            if execute:
                shadow.record_incident(ctx.data_dir, entry.strategy_id, session, "ledger_conflict", error)
            results.append({"strategyId": entry.strategy_id, "status": "conflict", "error": str(error)})
            continue
        except (ValueError, OSError) as error:
            results.append({"strategyId": entry.strategy_id, "status": "error", "error": str(error)})
            continue
        written = record.pop("_written", False)
        filled = [o for o in record["orders"] if o.get("filled")]
        results.append({"strategyId": entry.strategy_id, "status": "ok", "mode": mode, "date": session,
                        "written": written, "navCloseKrw": record["nav_close_krw"],
                        "cashAfter": record["cash_after"], "holdingCount": len(record["holdings_after"]),
                        "orderCount": sum(1 for o in record["orders"] if not o.get("skipped")),
                        "filledCount": len(filled), "incidents": record["incidents"],
                        "warnings": record["warnings"], "match": record["match"],
                        **({} if execute else {"record": record})})
    status = "ok" if all(r["status"] == "ok" for r in results) else "error"
    return {"status": status, "dryRun": not execute, "sessionDate": session, "results": results}


# ---------------------------------------------------------------- status


def _latest_plan_summary(ctx, entry):
    directory = Path(ctx.data_dir) / shadow.LIVE_DIR / entry.strategy_id / "plans"
    if not directory.is_dir():
        return None
    files = sorted((p for p in directory.glob("*_*.json") if not p.name.startswith(".")),
                   key=lambda p: (p.stem.split("_")[0], rebalance.PHASES.index(p.stem.split("_")[1])
                                  if p.stem.split("_")[1] in rebalance.PHASES else -1))
    if not files:
        return None
    record = json.loads(files[-1].read_text(encoding="utf-8"))
    return rebalance.summary(rebalance.RebalancePlan(
        record["strategy_id"], record["execution"], record["decision_hash"], record["decision_date"],
        record["session_date"], record["phase"],
        tuple(rebalance.Order(**o) for o in record["orders"]), tuple(record["skipped"]), tuple(record["targets"]),
        record["cash_before"], record["cash_after_estimate"], record["value_estimate"], record.get("meta") or {}))


def status_payload(ctx: Context, entry, *, generated_at: str, errors: list | None = None) -> dict:
    errors = [] if errors is None else errors
    stage = stage_for(ctx, entry)
    design = stage.evidence.get("design") or {}
    holdout = stage.evidence.get("holdout") or {}
    decision = None
    try:
        found = decisions.list_decisions(ctx.data_dir, entry.decision_rule, execution=entry.execution)
    except (OSError, ValueError) as error:
        errors.append(f"{entry.strategy_id}:decision_unreadable:{error}")
        found = []
    decision_reasons = []
    latest = found[-1] if found else None
    if latest is not None and (latest.label == "forward" and not latest.sealed or latest.seal_reason):
        # Not final: never shown or traded as a decision; the reason is surfaced instead.
        decision_reasons.append(f"{latest.seal_reason}:{latest.month}")
        if latest.seal_error:
            errors.append(f"{entry.strategy_id}:{latest.seal_reason}:{latest.month}:{latest.seal_error}")
        latest = None
    if latest is not None:
        decision = {"month": latest.month, "decisionDate": latest.decision_date, "tradeDate": latest.trade_date,
                    "exitDate": latest.exit_date, "targetCount": len(latest.targets),
                    "recordHash": latest.record_hash, "label": latest.label}
    try:
        ledger = shadow.read_ledger(ctx.data_dir, entry.strategy_id)
        extra = shadow.read_incidents(ctx.data_dir, entry.strategy_id)
    except (OSError, ValueError) as error:
        errors.append(f"{entry.strategy_id}:shadow_ledger_unreadable:{error}")
        ledger, extra = [], []
    run = [e for e in ledger if e["mode"] == "shadow"]
    nav = float(ledger[-1]["nav_close_krw"]) if ledger else float(entry.shadow_nav_krw)
    initial = entry.shadow_nav_krw
    shadow_view = {
        "mode": mode_for(stage), "sessions": len(run),
        "firstDate": run[0]["date"] if run else None, "lastDate": run[-1]["date"] if run else None,
        "initialNavKrw": initial, "navKrw": round(nav), "returnPct": round((nav / initial - 1) * 100, 4),
        "incidents": sum(len(e.get("incidents") or []) for e in ledger) + len(extra),
        "history": [{"date": e["date"], "navKrw": round(float(e["nav_close_krw"]))} for e in ledger[-HISTORY_LIMIT:]],
    }
    try:
        plan = _latest_plan_summary(ctx, entry)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"{entry.strategy_id}:plan_unreadable:{error}")
        plan = None
    return {
        "strategyId": entry.strategy_id, "title": entry.title, "description": entry.description,
        "generatedAt": generated_at,
        "stage": {"requested": stage.requested, "effective": stage.effective,
                  "reasons": list(stage.reasons) + decision_reasons,
                  "earlyPaper": stage.evidence.get("early_paper")},
        "research": {
            "experimentId": entry.experiment_id, "variant": entry.variant,
            "design": {"verdict": design.get("verdict"), "recordedAt": design.get("recorded_at")},
            "holdout": {"experimentId": holdout.get("experiment_id"), "variant": holdout.get("variant"),
                        "verdict": holdout.get("verdict"), "recordedAt": holdout.get("recorded_at")},
        },
        "decision": decision,
        "shadow": shadow_view,
        "plan": plan,
        "account": {"userId": entry.account_user_id, "bound": entry.account_user_id is not None},
    }


def status_command(ctx: Context, *, generated_at=None) -> dict:
    generated_at = generated_at or shadow.now_kst().isoformat()
    errors: list = []
    payloads = [status_payload(ctx, entry, generated_at=generated_at, errors=errors) for entry in ctx.entries]
    return {"generatedAt": generated_at, "strategies": payloads, "errors": errors}


def publish_status_command(ctx: Context, *, execute=False, client_factory=None) -> dict:
    status = status_command(ctx)
    requests_ = [{"strategyId": p["strategyId"], "generatedAt": status["generatedAt"], "payload": p}
                 for p in status["strategies"]]
    if not execute:
        return {"status": "dry_run", "dryRun": True, "requests": requests_, "errors": status["errors"]}
    from .backend_client import StrategyBackendClient

    client = (client_factory or StrategyBackendClient)()
    results = []
    for body in requests_:
        try:
            client.publish_status(body["strategyId"], body["generatedAt"], body["payload"])
            results.append({"strategyId": body["strategyId"], "stored": True})
        except Exception as error:  # noqa: BLE001 - report every strategy's outcome
            results.append({"strategyId": body["strategyId"], "stored": False, "error": str(error)})
    ok = all(r["stored"] for r in results)
    return {"status": "ok" if ok else "error", "dryRun": False, "results": results, "errors": status["errors"]}


# ---------------------------------------------------------------- submit (PAPER only)


BACKEND_REFUSALS = ("BATCH_ALREADY_EXISTS_FOR_PHASE", "CALENDAR_UNVERIFIED")


def live_calendar_check(session) -> str | None:
    """Live session hours must be known (pending special sessions, review horizon fail closed)."""
    try:
        trading_calendar.daily_session_open(session)
        trading_calendar.daily_session_close(session)
    except ValueError as error:
        return f"live_calendar_refused:{error}"
    return None


def _funding_refusal(plan, max_buy_to_deposit):
    funding = rebalance.funding(plan)
    if max_buy_to_deposit and funding["requires_same_auction_sell_proceeds"]:
        return funding, "buys_exceed_deposit_cash"
    return funding, None


def submit_command(ctx: Context, strategy_id, session, phase, *, execute=False, validate=False,
                   max_buy_to_deposit=False, client_factory=None) -> dict:
    """Without execute: no network, payload preview from the shadow ledger state.

    With execute: account snapshot -> plan -> POST batch (dryRun = validate).
    max_buy_to_deposit refuses (never resizes) a plan whose buys exceed deposit cash.
    """
    entry = find_entry(ctx, strategy_id)
    session = _iso(session)
    stage = stage_for(ctx, entry)
    refusals = []
    if stage.effective != "paper":
        refusals.append(f"stage_not_paper:{stage.effective}")
        refusals.extend(stage.reasons)
    if not entry.account_user_id:
        refusals.append("account_user_id_missing")
    calendar_refusal = live_calendar_check(session)
    if calendar_refusal:
        refusals.append(calendar_refusal)
    if refusals:
        return {"status": "refused", "strategyId": strategy_id, "reasons": refusals}
    if not execute:
        try:
            plan = build_plan(ctx, entry, session, phase)
            payload = rebalance.batch_payload(plan, user_id=entry.account_user_id,
                                              expected_nav_krw=plan.value_estimate, dry_run=True)
        except (rebalance.PlanError, ValueError) as error:
            return {"status": "error", "strategyId": strategy_id, "error": str(error)}
        funding, refusal = _funding_refusal(plan, max_buy_to_deposit)
        if refusal:
            return {"status": "refused", "strategyId": strategy_id, "reasons": [refusal], "funding": funding}
        return {"status": "preview", "source": "shadow_ledger", "dryRun": True, "funding": funding,
                "payload": payload}
    from .backend_client import StrategyBackendClient

    client = (client_factory or StrategyBackendClient)()
    account = client.fetch_account(entry.account_user_id)
    unsellable = sorted(code for code, qty in account.holdings.items() if account.sellable.get(code, 0) < qty)
    if unsellable:
        return {"status": "refused", "strategyId": strategy_id,
                "reasons": ["unsellable_holdings:" + ",".join(unsellable)]}
    try:
        plan = build_plan(ctx, entry, session, phase, account=account)
        payload = rebalance.batch_payload(plan, user_id=entry.account_user_id, expected_nav_krw=account.equity,
                                          dry_run=validate)
    except (rebalance.PlanError, ValueError) as error:
        return {"status": "error", "strategyId": strategy_id, "error": str(error)}
    funding, refusal = _funding_refusal(plan, max_buy_to_deposit)
    if refusal:
        return {"status": "refused", "strategyId": strategy_id, "reasons": [refusal], "funding": funding}
    response = client.submit_batch(payload)
    reasons = list(response.get("reasons") or [])
    if any(reason in BACKEND_REFUSALS for reason in reasons):
        status = "refused"
    elif response.get("status") == "REJECTED":
        status = "rejected"
    elif response.get("deduplicated"):
        status = "deduplicated"
    else:
        status = "submitted"
    if not validate and response.get("batchId"):
        record_batch(ctx.data_dir, strategy_id, payload, response)
    return {"status": status, "strategyId": strategy_id, "dryRun": bool(validate), "batchStatus":
            response.get("status"), "reasons": reasons, "funding": funding, "payload": payload,
            "response": response}


# ---------------------------------------------------------------- batch records (for later status reads)


def batches_dir(data_dir, strategy_id) -> Path:
    return Path(data_dir) / shadow.LIVE_DIR / strategy_id / "batches"


def record_batch(data_dir, strategy_id, payload, response) -> Path:
    """Static facts of an accepted batch; its live status is read back from the backend."""
    record = {"strategyId": strategy_id, "batchId": response["batchId"], "userId": payload["userId"],
              "sessionDate": payload["sessionDate"], "phase": payload["phase"],
              "decisionHash": payload["decisionHash"], "idempotencyKey": payload["idempotencyKey"],
              "recorded_at": shadow.now_kst().isoformat()}
    path = batches_dir(data_dir, strategy_id) / (
        f"{payload['sessionDate'].replace('-', '')}_{payload['phase']}_{response['batchId']}.json")
    shadow.write_immutable(path, record)
    return path


def batch_records(data_dir, strategy_id, session) -> list[dict]:
    directory = batches_dir(data_dir, strategy_id)
    if not directory.is_dir():
        return []
    prefix = _iso(session).replace("-", "") + "_"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob(prefix + "*.json"))
            if not p.name.startswith(".")]


# ---------------------------------------------------------------- daily run and alerts

RUNS_DIR = Path("live/runs")
EVENING_AFTER = shadow.LEDGER_WRITE_AFTER


class _Steps:
    """Each step runs independently; a failure is recorded and later steps still run."""

    def __init__(self):
        self.items = []

    def run(self, name, fn):
        try:
            result = fn()
        except Exception as error:  # noqa: BLE001 - record and continue with independent steps
            self.items.append({"step": name, "status": "failed", "error": f"{type(error).__name__}: {error}"})
            return None
        status = "ok"
        if isinstance(result, dict) and result.get("_status"):
            status = result.pop("_status")
        item = {"step": name, "status": status, "result": result}
        if status == "failed" and isinstance(result, dict) and result.get("_error"):
            item["error"] = result.pop("_error")
        self.items.append(item)
        return result

    def skip(self, name, note):
        self.items.append({"step": name, "status": "skipped", "note": note})

    @property
    def failed(self):
        return any(item["status"] == "failed" for item in self.items)


def _first_session_month(session):
    """The decision month whose trade date is this session, or None."""
    previous = shadow.previous_session(session)
    return previous[:7] if previous[:7] != session[:7] else None


def decision_seal_check(ctx, session) -> dict:
    month = _first_session_month(session) if shadow.is_session(session) else None
    rows = []
    if month is not None:
        proof, reason, error = decisions.completion(ctx.data_dir, "decisions", month)
        state = "final" if proof is not None else reason
        for entry in ctx.entries:
            rows.append({"strategyId": entry.strategy_id, "month": month, "tradeDate": session, "state": state,
                         "sealedAt": (proof or {}).get("sealed_at"), "error": error})
    result = {"tradeDateMonth": month, "strategies": rows}
    if any(row["state"] != "final" for row in rows):
        result.update(_status="failed", _error="decision_not_final:" + ",".join(
            sorted({f"{row['month']}:{row['state']}" for row in rows if row["state"] != "final"})))
    return result


def backend_checks(ctx, session, client_factory=None, env=None) -> dict:
    from .backend_client import StrategyBackendClient, backend_configured

    if client_factory is None and not backend_configured(env):
        return {"configured": False, "note": "backend_not_configured"}
    client = (client_factory or StrategyBackendClient)()
    result = {"configured": True, "unknown_orders": client.unknown_orders(), "risk": {}, "batches": [],
              "notes": ["halts_read_via_risk_halted_flag", "batches_from_local_submit_records"]}
    for entry in ctx.entries:
        if entry.account_user_id:
            result["risk"][entry.strategy_id] = client.risk(entry.account_user_id)
        for record in batch_records(ctx.data_dir, entry.strategy_id, session):
            batch = client.get_batch(record["batchId"])
            result["batches"].append({**record, "status": batch.get("status")})
    return result


def _next_session(session):
    later = [d for d in shadow.sessions_between(session, date.fromisoformat(session) + timedelta(days=14))
             if d > session]
    if not later:
        raise ValueError(f"no next session after {session}")
    return later[0]


def _plan_rows(ctx, entry, target, *, execute, scope):
    rows, errors = [], []
    for phase in due_phases(ctx, entry, target):
        result = plan_command(ctx, entry.strategy_id, target, phase, execute=execute)
        errors.extend({"strategyId": entry.strategy_id, "sessionDate": target, **e} for e in result["errors"])
        for record in result["plans"]:
            rows.append({"strategyId": entry.strategy_id, "sessionDate": target, "phase": phase, "scope": scope,
                         "orders": len(record["orders"]), "advWarnings": record["summary"]["advWarnings"],
                         "funding": record.get("funding"), "written": record.get("_written", False)})
    return rows, errors


def plans_for_today_and_next(ctx, session, *, execute) -> dict:
    """Today's due phases (written with execute; the state before today is final after the
    catch-up) and the next session's phases (preview only: today's fills are not known yet)."""
    target = _next_session(session)
    plans, errors = [], []
    for entry in ctx.entries:
        for day, write, scope in ((session, execute, "today"), (target, False, "next")):
            rows, errs = _plan_rows(ctx, entry, day, execute=write, scope=scope)
            plans.extend(rows)
            errors.extend(errs)
    out = {"sessionDate": session, "nextSessionDate": target, "plans": plans, "errors": errors}
    if errors:
        out.update(_status="failed", _error="plan_errors:" + ";".join(f"{e['strategyId']}:{e['error']}" for e in errors))
    return out


def catch_up_shadow(ctx, today, *, execute, now) -> dict:
    """Fill every past session without a ledger entry, oldest first, per strategy.

    KRX publishes day D's file around 08:00 on the next business day, so a missing file for
    the previous session (or later) is only pending: no ledger write, no incident. A file
    still missing for an older session is an incident (price_file_missing, written by
    shadow_fill_command). A strategy's catch-up stops at its first pending or refused
    session: the ledger never skips a session and writes a newer one after it.
    A strategy without any ledger starts at the previous session.
    """
    previous = shadow.previous_session(today)
    filled, pending, problems = [], [], []
    for entry in ctx.entries:
        ledger = shadow.read_ledger(ctx.data_dir, entry.strategy_id)
        last = ledger[-1]["date"] if ledger else None
        candidates = [previous] if last is None else [
            d for d in shadow.sessions_between(last, today) if last < d < today]
        for day in candidates:
            if day >= previous and not shadow.krx_path(ctx.data_dir, day).is_file():
                pending.append({"strategyId": entry.strategy_id, "session": day, "reason": "price_file_pending"})
                break
            result = shadow_fill_command(ctx, day, entry.strategy_id, execute=execute, now=now)["results"][0]
            result["session"] = day
            if result["status"] != "ok":
                problems.append(result)
                break
            filled.append({k: result.get(k) for k in ("strategyId", "session", "mode", "written", "navCloseKrw",
                                                       "incidents", "filledCount")})
            if not execute:
                break  # a dry run cannot chain sessions: the next one would not see this entry
    out = {"previousSession": previous, "filled": filled, "pending": pending, "problems": problems}
    if problems:
        out.update(_status="failed", _error="shadow_fill:" + ";".join(
            f"{r['strategyId']}:{r['session']}:{r.get('reason') or r['status']}" for r in problems))
    return out


def _ledgers(ctx):
    return {e.strategy_id: shadow.read_ledger(ctx.data_dir, e.strategy_id) for e in ctx.entries}


def _incident_files(ctx):
    return {e.strategy_id: shadow.read_incidents(ctx.data_dir, e.strategy_id) for e in ctx.entries}


def run_path(data_dir, session, when, now) -> Path:
    base = Path(data_dir) / RUNS_DIR
    path = base / f"{_iso(session).replace('-', '')}_{when}.json"
    if path.exists():
        path = base / f"{_iso(session).replace('-', '')}_{when}_{now:%H%M%S}-{uuid.uuid4().hex[:8]}.json"
    return path


def latest_run(data_dir) -> dict | None:
    directory = Path(data_dir) / RUNS_DIR
    if not directory.is_dir():
        return None
    files = sorted((p for p in directory.glob("*.json") if not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime_ns)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def daily_command(ctx, *, session=None, when=None, execute=False, now=None, client_factory=None,
                  env=None) -> tuple[dict, int]:
    """One scheduled run. Returns (summary, exit code); non-zero when any step failed."""
    from . import alerts

    now = now or shadow.now_kst()
    session = _iso(session or now.date().isoformat())
    when = when or ("evening" if now.time() >= EVENING_AFTER else "morning")
    if when not in ("morning", "evening"):
        raise ValueError(f"unknown daily phase: {when}")
    alerts.configured_channel_names(env)  # an unknown channel is a startup error, before any work
    steps = _Steps()
    summary = {"runId": f"{session.replace('-', '')}_{when}_{now:%H%M%S}", "session": session, "when": when,
               "dryRun": not execute, "startedAt": now.isoformat(), "isSession": None}
    warnings = steps.run("calendar_warnings", lambda: {
        "warnings": trading_calendar.calendar_review_warnings(date.fromisoformat(session))}) or {}
    is_session = steps.run("session_check", lambda: {"isSession": shadow.is_session(session)})
    summary["isSession"] = None if is_session is None else is_session["isSession"]
    status = backend = None
    if not summary["isSession"]:
        if when == "morning":
            steps.run("decision_seals", lambda: decision_seal_check(ctx, session))
        steps.skip("shadow_catch_up", "not_a_krx_session")
    elif when == "morning":
        # Day D's KRX file arrives ~08:00 on D+1: the morning fills past sessions, never today.
        steps.run("shadow_catch_up", lambda: catch_up_shadow(ctx, session, execute=execute, now=now))
        steps.run("decision_seals", lambda: decision_seal_check(ctx, session))
        steps.run("plans", lambda: plans_for_today_and_next(ctx, session, execute=execute))
        status = steps.run("status", lambda: status_command(ctx, generated_at=now.isoformat()))
        steps.run("publish_status", lambda: _publish_step(ctx, execute, client_factory, env))
        backend = steps.run("backend_checks", lambda: backend_checks(ctx, session, client_factory, env))
    else:
        # Evening: the day's batch results (CLOSE after 15:30) and a retry of pending sessions;
        # today's own session is filled tomorrow morning.
        backend = steps.run("backend_checks", lambda: backend_checks(ctx, session, client_factory, env))
        steps.run("shadow_catch_up", lambda: catch_up_shadow(ctx, session, execute=execute, now=now))
    summary["steps"] = steps.items
    try:
        ledgers, incident_files = _ledgers(ctx), _incident_files(ctx)
    except (OSError, ValueError):
        ledgers, incident_files = {}, {}  # status/alerts report an unreadable ledger
    inputs = alerts.AlertInputs(today=now.date(), when=when, session=session, summary=summary, status=status,
                                ledgers=ledgers, incident_files=incident_files,
                                backend=backend, calendar_warnings=warnings.get("warnings") or [])
    steps.run("alerts", lambda: _alert_step(inputs, ctx, now, execute, env))
    summary.update(steps=steps.items, status="failed" if steps.failed else "ok",
                   finishedAt=shadow.now_kst().isoformat())
    if execute:
        path = run_path(ctx.data_dir, session, when, now)
        shadow.write_immutable(path, summary, ignore=())
        summary["runFile"] = str(path)
    return summary, 1 if steps.failed else 0


def _publish_step(ctx, execute, client_factory, env):
    from .backend_client import backend_configured

    if client_factory is None and not backend_configured(env):
        return {"note": "backend_not_configured", "_status": "skipped"}
    result = publish_status_command(ctx, execute=execute, client_factory=client_factory)
    if result["status"] == "error":
        result.update(_status="failed", _error="publish_status_failed")
    return result


def _alert_step(inputs, ctx, now, execute, env):
    from . import alerts

    result = alerts.dispatch(inputs, ctx.data_dir, now, execute=execute, env=env)
    if result["status"] == "failed":
        result.update(_status="failed", _error="alert_channel_failed")
    return result


def alerts_command(ctx, *, execute=False, failure_unit=None, now=None, client_factory=None, env=None) -> dict:
    """Evaluate alerts from the last daily summary plus a fresh status (and backend if configured)."""
    from . import alerts

    alerts.configured_channel_names(env)
    now = now or shadow.now_kst()
    summary = latest_run(ctx.data_dir)
    if summary is not None and not str(summary.get("startedAt", "")).startswith(now.date().isoformat()):
        summary = None  # only today's run feeds step/seal alerts; older ones were handled on their day
    errors, status, backend = [], None, None
    try:
        status = status_command(ctx, generated_at=now.isoformat())
    except Exception as error:  # noqa: BLE001 - an alert must still go out
        errors.append(f"status_failed:{type(error).__name__}: {error}")
    try:
        backend = backend_checks(ctx, now.date().isoformat(), client_factory, env)
    except Exception as error:  # noqa: BLE001
        errors.append(f"backend_checks_failed:{type(error).__name__}: {error}")
    try:
        warnings = trading_calendar.calendar_review_warnings(now.date())
    except ValueError as error:
        warnings, errors = [], errors + [f"calendar_warnings_failed:{error}"]
    if status is not None:
        status = {**status, "errors": list(status.get("errors") or []) + errors}
    else:
        status = {"strategies": [], "errors": errors}
    inputs = alerts.AlertInputs(today=now.date(), when=None, session=(summary or {}).get("session"),
                                summary=summary, status=status, ledgers=_ledgers(ctx),
                                incident_files=_incident_files(ctx), backend=backend,
                                calendar_warnings=warnings, failure_unit=failure_unit)
    result = alerts.dispatch(inputs, ctx.data_dir, now, execute=execute, env=env)
    return {**result, "dryRun": not execute, "summaryRunId": (summary or {}).get("runId")}
