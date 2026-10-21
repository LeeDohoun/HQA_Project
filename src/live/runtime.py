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
        phases.extend(rebalance.INTRADAY_PHASES if entry.execution == "continuous_intraday" else ["OPEN"])
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


def build_intraday_plans(ctx: Context, entry, session, *, account=None, risk=None, terminated=False,
                         block_buys=False):
    """continuous_intraday (CONT_SELL, CONT_BUY) plans.

    Account mode: holdings/cash from the pre-session snapshot; the reserved (non-trading
    sleeve) cash from the T0 NAV in activation.json (or the backend's activationNavKrw).
    Virtual mode: shadow ledger state and shadow_nav_krw. Terminated -> liquidation only.
    """
    from . import pf001

    session = _iso(session)
    book_state = shadow_state(ctx, entry, session)
    cash = account.cash if account is not None else book_state.cash
    holdings = dict(account.holdings) if account is not None else dict(book_state.holdings)
    terminated = terminated or bool((risk or {}).get("terminated"))
    if account is not None:
        activation = pf001.load_activation(ctx.data_dir, entry.strategy_id) or {}
        t0_nav = activation.get("t0_nav_krw") or (risk or {}).get("activationNavKrw")
        if isinstance(t0_nav, bool) or not isinstance(t0_nav, (int, float)) or t0_nav <= 0:
            raise rebalance.PlanError("activation_nav_missing")
    else:
        t0_nav = entry.shadow_nav_krw
    _, reserved = pf001.capital_parts(entry, t0_nav)
    common = dict(session_date=session, reserved_krw=reserved, holdings=holdings, cash=cash,
                  model_version=entry.cost_model_version, band=entry.band, block_buys=block_buys,
                  max_name_weight=entry.limits["max_name_weight"], max_adv_share=entry.limits["max_adv_share"])
    if terminated:
        previous = shadow.previous_session(session)
        quotes = shadow.load_quotes(ctx.data_dir, previous) or {}
        ref = _previous_closes(ctx, session, holdings, book_state.marks)
        markets = {c: quotes[c].market for c in holdings if c in quotes and quotes[c].market in ("KOSPI", "KOSDAQ")}
        adv = shadow.adv20_from_files(ctx.data_dir, previous, holdings)
        return rebalance.plan_intraday(entry.strategy_id, None, terminated=True, ref_prices=ref, markets=markets,
                                       adv20=adv, **common)
    decision = next((d for d in _forward(ctx, entry) if d.trade_date == session), None)
    if decision is None:
        raise rebalance.PlanError(f"no_forward_decision_for_session:{session}")
    ref, markets, adv = shadow.plan_inputs(ctx.data_dir, decision, extra_codes=holdings,
                                           fallback_marks=book_state.marks)
    for code in holdings:
        known = book_state.state.get("markets", {}).get(code)
        if code not in markets and known:
            markets[code] = known
    return rebalance.plan_intraday(entry.strategy_id, decision, ref_prices=ref, markets=markets, adv20=adv,
                                   **common)


def build_plan(ctx: Context, entry, session, phase, *, account=None, risk=None) -> rebalance.RebalancePlan:
    """Plan for one session/phase from the shadow ledger state, or from an account snapshot."""
    session = _iso(session)
    if phase not in rebalance.PHASES:
        raise rebalance.PlanError(f"unknown phase: {phase}")
    if (entry.execution == "continuous_intraday") != (phase in rebalance.INTRADAY_PHASES):
        raise rebalance.PlanError(f"phase {phase} does not apply to {entry.execution}")
    if entry.execution == "continuous_intraday":
        sell, buy = build_intraday_plans(ctx, entry, session, account=account, risk=risk)
        return sell if phase == "CONT_SELL" else buy
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
    def key(path):
        day, _, phase = path.stem.partition("_")
        return day, rebalance.PHASES.index(phase) if phase in rebalance.PHASES else -1

    files = sorted((p for p in directory.glob("*_*.json") if not p.name.startswith(".")), key=key)
    if not files:
        return None
    day = key(files[-1])[0]
    if key(files[-1])[1] < rebalance.PHASES.index("CONT_SELL"):
        files = files[-1:]  # auction kinds: the latest phase file only (unchanged behaviour)
    else:
        files = [f for f in files if key(f)[0] == day and f.stem.partition("_")[2] in rebalance.INTRADAY_PHASES]
    summaries = []
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(rebalance.summary(rebalance.RebalancePlan(
            record["strategy_id"], record["execution"], record["decision_hash"], record["decision_date"],
            record["session_date"], record["phase"],
            tuple(rebalance.Order(**o) for o in record["orders"]), tuple(record["skipped"]),
            tuple(record["targets"]), record["cash_before"], record["cash_after_estimate"], record["value_estimate"],
            record.get("meta") or {})))
    if len(summaries) == 1:
        return summaries[0]
    merged = {"sessionDate": summaries[0]["sessionDate"], "phase": "+".join(x["phase"] for x in summaries)}
    for name in ("buyCount", "sellCount", "buyNotionalKrw", "sellNotionalKrw", "advWarnings"):
        merged[name] = sum(x[name] for x in summaries)
    return merged


def termination_view(ctx, entry, risk) -> dict:
    """terminated / state (liquidating until no holdings, then terminated_drawdown) / source."""
    from . import pf001

    record = pf001.load_termination(ctx.data_dir, entry.strategy_id)
    backend = bool((risk or {}).get("terminated"))
    if record is None and not backend:
        return {"terminated": False, "state": None, "terminatedAt": None, "reason": None, "source": None}
    records = pf001.nav_records(ctx.data_dir, entry.strategy_id)
    holding = bool(records[-1].get("holdings")) if records else True
    return {"terminated": True, "state": "liquidating" if holding else "terminated_drawdown",
            "terminatedAt": (record or {}).get("recorded_at") or (risk or {}).get("terminatedAt"),
            "reason": (record or {}).get("reason") or (risk or {}).get("terminationReason"),
            "source": "python_nav_record" if record else "backend"}


INDEX_SERIES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


def index_levels(data_dir) -> dict | None:
    """Latest KOSPI/KOSDAQ closes from the local benchmark file (protected holdout rows ignored)."""
    path = Path(data_dir) / "market_context" / "benchmarks.jsonl"
    if not path.is_file():
        return None
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            series, day = row.get("series"), str(row.get("trade_date") or "")
            if (series not in INDEX_SERIES or row.get("index_name") != INDEX_SERIES[series]
                    or "2026-01-01" <= day <= "2026-06-30" or not isinstance(row.get("close"), (int, float))):
                continue
            if day > (latest.get(series) or {}).get("date", ""):
                latest[series] = {"date": day, "close": row["close"]}
    return latest or None


def portfolio_view(ctx, entry, ledger, risk) -> dict:
    """Total NAV and close-to-close drawdown vs the running maximum; no per-sleeve results.

    Bound account: the binding official-close NAV records (pf001 nav/); otherwise the shadow ledger.
    """
    from . import pf001

    records = pf001.nav_records(ctx.data_dir, entry.strategy_id)
    activation = pf001.load_activation(ctx.data_dir, entry.strategy_id)
    if records and activation:
        initial, navs, source = float(activation["t0_nav_krw"]), [r["navKrw"] for r in records], "krx_official_close"
    else:
        initial, navs, source = float(entry.shadow_nav_krw), [float(e["nav_close_krw"]) for e in ledger], \
            "shadow_ledger"
    nav = navs[-1] if navs else initial
    peak = max([initial, *navs])
    view = {"navKrw": round(nav), "runningMaxNavKrw": round(peak),
            "drawdownPct": round((peak - nav) / peak * 100, 4) if peak else 0.0, "source": source,
            "indices": index_levels(ctx.data_dir)}
    if risk and isinstance(risk.get("equityKrw"), (int, float)):
        view.update(navKrw=round(risk["equityKrw"]), source="backend")
        if isinstance(risk.get("closeNavMaxKrw"), (int, float)):
            view["runningMaxNavKrw"] = round(risk["closeNavMaxKrw"])
        if isinstance(risk.get("closeDrawdownPct"), (int, float)):
            view["drawdownPct"] = risk["closeDrawdownPct"]
    return view


def status_payload(ctx: Context, entry, *, generated_at: str, errors: list | None = None, risk=None) -> dict:
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
    extra = {}
    if entry.sleeves:
        # Sleeve attribution stays in the ledger and run records; only totals are shown.
        extra = {"sleeveDetailSealed": True, "portfolio": portfolio_view(ctx, entry, ledger, risk),
                 "termination": termination_view(ctx, entry, risk)}
    return {
        **extra,
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


def status_command(ctx: Context, *, generated_at=None, risk=None) -> dict:
    generated_at = generated_at or shadow.now_kst().isoformat()
    errors: list = []
    risk = risk or {}
    payloads = [status_payload(ctx, entry, generated_at=generated_at, errors=errors, risk=risk.get(entry.strategy_id))
                for entry in ctx.entries]
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


BACKEND_REFUSALS = ("BATCH_ALREADY_EXISTS_FOR_PHASE", "CALENDAR_UNVERIFIED", "NAV_CROSSCHECK_MISMATCH",
                    "NAV_CLOSE_MISSING", "STRATEGY_TERMINATED")


def classify_response(response) -> tuple[str, list]:
    reasons = list(response.get("reasons") or [])
    if any(reason in BACKEND_REFUSALS for reason in reasons):
        return "refused", reasons
    if response.get("status") == "REJECTED":
        return "rejected", reasons
    if response.get("deduplicated"):
        return "deduplicated", reasons
    return "submitted", reasons


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
            if not plan.orders:
                return {"status": "nothing_to_submit", "strategyId": strategy_id, "phase": phase,
                        "skipped": list(plan.skipped)}
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
    risk = None
    if entry.execution == "continuous_intraday":
        risk = client.risk(entry.account_user_id)  # activationNavKrw and termination state
        if risk.get("terminated") and phase == "CONT_BUY":
            return {"status": "refused", "strategyId": strategy_id, "reasons": ["strategy_terminated_no_buys"]}
    try:
        plan = build_plan(ctx, entry, session, phase, account=account, risk=risk)
        if not plan.orders:
            return {"status": "nothing_to_submit", "strategyId": strategy_id, "phase": phase,
                    "skipped": list(plan.skipped)}
        payload = rebalance.batch_payload(plan, user_id=entry.account_user_id, expected_nav_krw=account.equity,
                                          dry_run=validate)
    except (rebalance.PlanError, ValueError) as error:
        return {"status": "error", "strategyId": strategy_id, "error": str(error)}
    funding, refusal = _funding_refusal(plan, max_buy_to_deposit)
    if refusal:
        return {"status": "refused", "strategyId": strategy_id, "reasons": [refusal], "funding": funding}
    response = client.submit_batch(payload)
    status, reasons = classify_response(response)
    if not validate and response.get("batchId"):
        record_batch(ctx.data_dir, strategy_id, payload, response)
    return {"status": status, "strategyId": strategy_id, "dryRun": bool(validate), "batchStatus":
            response.get("status"), "reasons": reasons, "funding": funding, "payload": payload,
            "response": response, "plan": {"phase": plan.phase, "skipped": list(plan.skipped), "meta": plan.meta,
                                           "bindings": {o.stock_code: o.binding for o in plan.orders if o.binding}}}


SUBMIT_OK = ("preview", "submitted", "deduplicated", "nothing_to_submit", "skipped")


INPUT_RETRY_SECONDS = 20


def submit_due_command(ctx: Context, *, session=None, execute=False, inputs_only=False, now=None,
                       client_factory=None, env=None, clock=None, sleep=None) -> dict:
    """Submit today's due batches for every bound PAPER strategy (dry-run by default).

    The FW001 decision acting today must be final (fw001.completion_proof) right before
    building; otherwise nothing is submitted for it and a critical decision_not_final alert is
    sent. continuous_intraday strategies follow pf001 (input seal, binding close NAV, D=25%,
    liquidation); inputs_only seals the input reference without submitting.
    """
    from . import alerts

    import time as _time

    from . import pf001

    clock = clock or ((lambda: now) if now is not None else shadow.now_kst)
    sleep = sleep or _time.sleep
    now = clock()
    session = _iso(session or now.date().isoformat())
    alerts.configured_channel_names(env)
    result = {"session": session, "dryRun": not execute, "inputsOnly": inputs_only, "submissions": [],
              "skipped": [], "seals": [], "intraday": {}}
    if not shadow.is_session(session):
        result.update(status="ok", note="not_a_krx_session")
        return result
    month = _first_session_month(session)
    extra_alerts = []
    for entry in ctx.entries:
        sid = entry.strategy_id
        stage = stage_for(ctx, entry)
        if stage.effective != "paper" or not entry.account_user_id:
            result["skipped"].append({"strategyId": sid, "reason": "not_paper" if stage.effective != "paper"
                                      else "account_user_id_missing"})
            continue
        if entry.execution == "continuous_intraday":
            try:
                attempts = 0
                started = clock()
                window = (pf001.last_input_attempt(session, started) - started).total_seconds()
                max_attempts = 1 + max(0, int(window // INPUT_RETRY_SECONDS))
                while True:
                    attempts += 1
                    out = _submit_intraday(ctx, entry, session, execute=execute, inputs_only=inputs_only,
                                           client_factory=client_factory, now=clock())
                    # inputs-only: retry every 20 s while the decision is not final, until 08:58:59
                    # (bounded by both the clock and the attempt count).
                    if not (inputs_only and execute and out["seals"]) or attempts >= max_attempts:
                        break
                    if clock() + timedelta(seconds=INPUT_RETRY_SECONDS) > pf001.last_input_attempt(session, clock()):
                        break
                    sleep(INPUT_RETRY_SECONDS)
                out["info"]["attempts"] = attempts
            except Exception as error:  # noqa: BLE001 - one strategy must not hide the others
                out = {"submissions": [{"strategyId": sid, "phase": None, "status": "error",
                                        "error": f"{type(error).__name__}: {error}"}], "seals": [], "alerts": [],
                       "info": {}}
            result["submissions"].extend(out["submissions"])
            result["seals"].extend(out["seals"])
            extra_alerts.extend(out["alerts"])
            result["intraday"][sid] = out["info"]
            continue
        if inputs_only:
            continue
        due = []
        if month is not None:
            proof, reason, error = decisions.completion(ctx.data_dir, "decisions", month)
            if proof is None:
                result["seals"].append({"strategyId": sid, "month": month, "tradeDate": session, "state": reason,
                                        "error": error})
            else:
                due.append("OPEN")
        if entry.execution == "basket_20" and any(d.exit_date == session for d in _forward(ctx, entry)):
            due.append("CLOSE")
        for phase in due:
            outcome = submit_command(ctx, sid, session, phase, execute=execute, client_factory=client_factory)
            result["submissions"].append({"strategyId": sid, "phase": phase, **{
                k: outcome.get(k) for k in ("status", "reasons", "error", "batchStatus", "funding")}})
    if inputs_only and result["seals"]:
        # 08:57 may precede the FW001 seal (deadline 08:59): the 09:01 submit run decides and alerts.
        result["pendingSeals"], result["seals"] = result["seals"], []
    if result["seals"] or extra_alerts:
        inputs = alerts.AlertInputs(today=now.date(), when="submit", session=session, summary={
            "runId": f"submit_{session}", "session": session, "extraAlerts": extra_alerts,
            "steps": [{"step": "decision_seals", "status": "failed" if result["seals"] else "ok",
                       "result": {"strategies": result["seals"]}}]})
        result["alerts"] = alerts.dispatch(inputs, ctx.data_dir, now, execute=execute, env=env)
    failed = (result["seals"] or any(a["severity"] == "critical" for a in extra_alerts)
              or any(x["status"] not in SUBMIT_OK for x in result["submissions"]))
    result["status"] = "failed" if failed else "ok"
    return result


def _submit_intraday(ctx, entry, session, *, execute, inputs_only, client_factory, now) -> dict:
    """pf001 (OP001 contract section 2-3) for one session."""
    from . import pf001

    sid, uid = entry.strategy_id, entry.account_user_id
    out = {"submissions": [], "seals": [], "alerts": [], "info": {}}

    def alert(code, message, details):
        out["alerts"].append({"severity": "critical", "code": code, "strategy_id": sid, "message": message,
                              "details": details, "dedupe_key": f"{code}:{sid}:{session}"})

    def record(phase, status, **more):
        out["submissions"].append({"strategyId": sid, "phase": phase, "status": status, **more})

    if not execute:  # preview from the shadow ledger state; no network, no seal
        decision, seal = pf001.decision_today(ctx, entry, session)
        if seal:
            out["seals"].append(seal)
        elif decision is None:
            out["info"]["note"] = "no_decision_today"
        else:
            try:
                plans = build_intraday_plans(ctx, entry, session)
            except (rebalance.PlanError, ValueError) as error:
                record("CONT_SELL", "error", error=str(error))
                return out
            for plan in plans:
                record(plan.phase, "preview" if plan.orders else "nothing_to_submit", orders=len(plan.orders),
                       bindings=_binding_counts(plan))
        return out
    from .backend_client import StrategyBackendClient

    client = (client_factory or StrategyBackendClient)()
    activation = pf001.load_activation(ctx.data_dir, sid)
    if activation is None:
        record(None, "refused", reasons=["not_activated"])
        alert("not_activated", f"{sid} activation.json이 없어 주문하지 않았습니다", {})
        return out
    account = client.fetch_account(uid)
    risk = client.risk(uid)
    unsellable = sorted(code for code, qty in account.holdings.items() if account.sellable.get(code, 0) < qty)
    if unsellable:
        record(None, "refused", reasons=["unsellable_holdings:" + ",".join(unsellable)])
        return out
    nav = pf001.close_nav(ctx, entry, account, session, execute=True, risk=risk)
    buys_blocked, state = False, None
    if nav["valuationMissing"]:
        buys_blocked = True
        alert("valuation_missing", f"{sid} 평가 자료 결손으로 신규 매수를 막습니다: {', '.join(nav['valuationMissing'])}",
              {"missing": nav["valuationMissing"], "sessionDate": nav["sessionDate"]})
    else:
        posted = client.post_nav_close(uid, pf001.nav_close_body(nav))
        if posted.get("conflict"):
            buys_blocked = True
            alert("nav_close_conflict", f"{sid} {nav['sessionDate']} 종가 NAV가 백엔드 기록과 다릅니다",
                  {"reason": posted.get("reason"), "navKrw": nav["navKrw"]})
        state = pf001.drawdown_state(ctx, entry, nav, activation["t0_nav_krw"])
    termination = pf001.load_termination(ctx.data_dir, sid)
    if termination is None and state is not None and state["breach"] and not risk.get("terminated"):
        termination = pf001.record_breach(ctx, entry, nav, state, now=now)
        alert("drawdown_breach", f"{sid} 종가 NAV {round(nav['navKrw'])}가 고점의 75% 이하입니다. 전량 매도로 전환합니다",
              state)
    terminated = bool(risk.get("terminated")) or termination is not None
    out["info"] = {"navKrw": nav["navKrw"], "navSession": nav["sessionDate"], "drawdown": state,
                   "terminated": terminated, "buysBlocked": buys_blocked}
    decision = None
    if not terminated:
        decision, seal = pf001.decision_today(ctx, entry, session)
        if seal:
            out["seals"].append(seal)
            return out
        if decision is None:
            out["info"]["note"] = "no_decision_today"
            return out
    # A rebalance runs only from an input reference sealed strictly before 08:59:00 (preregistration
    # section 5: a late decision means no new rebalance that day). Termination liquidation does not
    # depend on a decision, so it is sealed at submit time under its own name.
    path = (pf001.liquidation_inputs_path(ctx.data_dir, sid, session) if terminated
            else pf001.inputs_path(ctx.data_dir, sid, session))
    sealed = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if sealed is not None and not terminated and not pf001.sealed_in_time(sealed):
        sealed = None
    if sealed is None:
        if not terminated and not pf001.before_input_deadline(now, session):
            out["info"]["note"] = "input_not_sealed_before_deadline"
            if not inputs_only:
                record(None, "skipped", reasons=["input_not_sealed_before_deadline"])
                alert("input_not_sealed_before_deadline",
                      f"{sid} 입력 기준이 08:59 전에 봉인되지 않아 오늘 리밸런싱을 하지 않습니다 (보유 유지)",
                      {"session": session})
            return out
        try:
            plans = build_intraday_plans(ctx, entry, session, account=account, risk=risk, terminated=terminated,
                                         block_buys=buys_blocked)
        except (rebalance.PlanError, ValueError) as error:
            record("CONT_SELL", "error", error=str(error))
            return out
        sealed = pf001.input_record(ctx, entry, session, decision=decision, account=account, nav_record=nav,
                                    plans=plans, terminated=terminated, buys_blocked=buys_blocked, now=now)
        sealed, _ = shadow.write_immutable(path, sealed, ignore=())
    out["info"]["inputs"] = str(path)
    if inputs_only:
        return out
    for phase in rebalance.INTRADAY_PHASES:
        data = sealed["plans"].get(phase)
        if data is None:
            continue
        if phase == "CONT_BUY" and (terminated or buys_blocked or sealed.get("buysBlocked")):
            record(phase, "skipped", reasons=["buys_blocked_terminated" if terminated else "buys_blocked"])
            continue
        plan = pf001.plan_from_dict(data)
        if not plan.orders:
            record(phase, "nothing_to_submit")
            continue
        payload = rebalance.batch_payload(plan, user_id=uid, expected_nav_krw=account.equity, dry_run=False)
        response = client.submit_batch(payload)
        status, reasons = classify_response(response)
        blocking = sorted(set(reasons) & set(pf001.NAV_BLOCKING_REASONS))
        if blocking:
            alert("nav_check_blocked", f"{sid} 백엔드가 {phase}를 막았습니다: {', '.join(blocking)}",
                  {"phase": phase, "reasons": reasons})
        if response.get("batchId"):
            record_batch(ctx.data_dir, sid, payload, response)
        record(phase, status, reasons=reasons, batchStatus=response.get("status"), orders=len(plan.orders))
    return out


def _binding_counts(plan) -> dict:
    counts = {}
    for order in plan.orders:
        counts[order.binding or "target"] = counts.get(order.binding or "target", 0) + 1
    return counts


def activate_command(ctx, strategy_id, *, execute=False, client_factory=None, now=None) -> dict:
    from . import pf001
    from .backend_client import StrategyBackendClient

    entry = find_entry(ctx, strategy_id)
    if entry.execution != "continuous_intraday":
        raise ValueError(f"activation applies to continuous_intraday strategies, not {entry.execution}")
    client = (client_factory or StrategyBackendClient)() if execute else None
    try:
        return pf001.activate(ctx, entry, stage_for(ctx, entry), execute=execute, client=client, now=now)
    except shadow.LedgerConflict as error:
        return {"status": "refused", "reasons": [f"activation_exists_with_different_content:{error}"]}


READINESS_STOCK = "005930"


def readiness_command(ctx, strategy_id, step, *, execute=False, client_factory=None, now=None) -> dict:
    """One-off OP001 readiness test (never scheduled): 'buy' = one CONT_BUY of 1 share 005930;
    'flatten' = one CLOSE SELL of exactly the quantity this test's own buy batch filled."""
    from .backend_client import StrategyBackendClient

    now = now or shadow.now_kst()
    session = now.date().isoformat()
    directory = Path(ctx.data_dir) / shadow.LIVE_DIR / strategy_id
    buy_path = directory / f"{session.replace('-', '')}_buy.json"
    if step not in ("buy", "flatten"):
        raise ValueError("readiness step must be buy or flatten")
    quotes = shadow.load_quotes(ctx.data_dir, shadow.previous_session(session)) or {}
    ref = quotes.get(READINESS_STOCK)
    ref_price = ref.close if ref is not None and ref.close else 1.0
    client = (client_factory or StrategyBackendClient)() if execute else None
    if step == "buy":
        quantity, phase = 1, "CONT_BUY"
    else:
        if not buy_path.is_file():
            return {"status": "refused", "reasons": ["readiness_buy_record_missing"]}
        bought = json.loads(buy_path.read_text(encoding="utf-8"))
        if not execute:
            return {"status": "dry_run", "note": "flatten sells exactly the buy batch's filled quantity",
                    "buyBatchId": bought.get("response", {}).get("batchId")}
        batch = client.get_batch(bought["response"]["batchId"])
        quantity = sum(int(o.get("filledQuantity") or 0) for o in batch.get("orders") or []
                       if o.get("stockCode") == READINESS_STOCK and o.get("side") == "BUY")
        if quantity <= 0:
            return {"status": "nothing_to_submit", "reason": "buy_batch_filled_nothing"}
        phase = "CLOSE"
    user = None
    if execute:
        user = next((a.get("userId") for a in client.accounts() if a.get("strategyId") == strategy_id), None)
        if user is None:
            return {"status": "refused", "reasons": ["readiness_account_not_bound"]}
    side = "BUY" if step == "buy" else "SELL"
    plan = rebalance.RebalancePlan(
        strategy_id, "readiness", hashlib_sha(f"op001_readiness|{session}|{step}"), session, session, phase,
        (rebalance.Order(1, READINESS_STOCK, side, quantity, float(ref_price), float(ref_price * quantity), None,
                         False, f"readiness_{step}"),), (), (), 0.0, 0.0, 0.0, {"cost_reserve_rate": 0.0})
    payload = rebalance.batch_payload(plan, user_id=user or "<bound-user>", expected_nav_krw=0, dry_run=not execute)
    if not execute:
        return {"status": "dry_run", "payload": payload}
    response = client.submit_batch(payload)
    record = {"strategyId": strategy_id, "step": step, "session": session, "payload": payload,
              "response": response, "recorded_at": now.isoformat()}
    path = buy_path if step == "buy" else directory / f"{session.replace('-', '')}_flatten.json"
    shadow.write_immutable(path, record)
    return {"status": classify_response(response)[0], "record": str(path), "response": response}


def hashlib_sha(text) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        backend = steps.run("backend_checks", lambda: backend_checks(ctx, session, client_factory, env))
        steps.run("plans", lambda: plans_for_today_and_next(ctx, session, execute=execute))
        status = steps.run("status", lambda: status_command(ctx, generated_at=now.isoformat(),
                                                             risk=(backend or {}).get("risk")))
        steps.run("publish_status", lambda: _publish_step(ctx, execute, client_factory, env))
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
