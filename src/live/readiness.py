"""OP001 readiness test on the COMPETITION PAPER account (user approval 2026-10-21; 2026-10-27..30).

Steps (each with --execute only; records are immutable under data/live/op001_readiness/):
- prewarm (08:45): read-only account snapshot so the backend issues/caches its KIS token. No orders.
- buy (09:19:30): one CONT_BUY of 1 share of 000660, after the refusal checks below.
- flatten (15:22+): one CLOSE SELL of exactly the 000660 quantity our own buy batch filled.
- verify (15:35): balance + broker orders; any leftover raises readiness_leftover. An M8 or unknown
  leftover also writes blocked_YYYYMMDD.json, which makes the next buy refuse.
005930 belongs to another test on the same account (XO001): it is ignored by the buy checks, never
traded here, and labelled "XO001" in verify.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import rebalance, shadow

STRATEGY_ID = "op001_readiness"
STOCK = "000660"
FOREIGN_STOCKS = {"005930": "XO001"}
USER_ENV = "HQA_READINESS_USER_ID"
STEPS = ("prewarm", "buy", "flatten", "verify")
ALLOWED_ACCOUNTS = {"pf001", STRATEGY_ID}


def directory(data_dir) -> Path:
    return Path(data_dir) / shadow.LIVE_DIR / STRATEGY_ID


def step_path(data_dir, day, step) -> Path:
    return directory(data_dir) / f"{day.replace('-', '')}_{step}.json"


def marker_path(data_dir, day) -> Path:
    return directory(data_dir) / f"blocked_{day.replace('-', '')}.json"


def earlier_markers(data_dir, day) -> list[str]:
    folder = directory(data_dir)
    if not folder.is_dir():
        return []
    today = day.replace("-", "")
    return sorted(p.name for p in folder.glob("blocked_*.json") if p.stem.split("_", 1)[1] < today)


def _hash(text) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reference_price(data_dir, day):
    """Latest verified local KRX close of 000660 within ten sessions before today."""
    for session in reversed(shadow.last_sessions(shadow.previous_session(day), 10)):
        quote = (shadow.load_quotes(data_dir, session) or {}).get(STOCK)
        if quote is not None and quote.verified and quote.close:
            return quote.close
    return None


def _owner(stock_code, odno, own_odnos) -> str:
    if stock_code == STOCK or (odno and odno in own_odnos):
        return "M8"
    return FOREIGN_STOCKS.get(stock_code, "unknown")


def _open(order) -> bool:
    return not order.get("cancelled") and int(order.get("remainingQty") or 0) > 0


def _alert(ctx, now, execute, env, code, message, details) -> dict:
    from . import alerts

    inputs = alerts.AlertInputs(today=now.date(), when="readiness", session=now.date().isoformat(), summary={
        "runId": f"readiness_{now:%Y%m%d%H%M%S}", "extraAlerts": [{
            "severity": "critical", "code": code, "strategy_id": STRATEGY_ID, "message": message,
            "details": details, "dedupe_key": f"{code}:{now.date().isoformat()}:{details.get('step', '')}"}]})
    return alerts.dispatch(inputs, ctx.data_dir, now, execute=execute, env=env)


def _record(ctx, day, step, body, now) -> str:
    path = step_path(ctx.data_dir, day, step)
    shadow.write_immutable(path, {"strategyId": STRATEGY_ID, "step": step, "session": day, **body,
                                  "recorded_at": now.isoformat()})
    return str(path)


def _batch_details(client, batch_id) -> dict:
    batch = client.get_batch(batch_id) if batch_id else {}
    orders = [o for o in batch.get("orders") or [] if o.get("stockCode") == STOCK]
    return {"batchId": batch_id, "batchStatus": batch.get("status"), "sizing": batch.get("sizing"),
            "orders": [{k: o.get(k) for k in ("seq", "side", "quantity", "status", "brokerOrderId", "filledQuantity",
                                              "averageFillPrice", "rejectReason")} for o in orders]}


def buy_refusals(ctx, client, user, day) -> list[str]:
    reasons = [f"block_marker:{name}" for name in earlier_markers(ctx.data_dir, day)]
    accounts = client.accounts()
    for account in accounts:
        sid = account.get("strategyId")
        if sid not in ALLOWED_ACCOUNTS:
            reasons.append(f"unexpected_strategy_account:{sid}")
        elif sid == "pf001" and account.get("enabled") is not False:
            reasons.append("pf001_account_enabled")
    if not any(a.get("strategyId") == STRATEGY_ID and a.get("userId") == user for a in accounts):
        reasons.append("readiness_account_not_bound")
    snapshot = client.fetch_account(user)
    if snapshot.holdings.get(STOCK):
        reasons.append(f"own_stock_already_held:{STOCK}:{snapshot.holdings[STOCK]}")
    orders = client.broker_orders(user, day).get("orders") or []
    if any(o.get("stockCode") == STOCK and _open(o) for o in orders):
        reasons.append(f"own_stock_open_orders:{STOCK}")
    return reasons  # foreign 005930 holdings/orders are expected and ignored


def run_step(ctx, strategy_id, step, *, execute=False, user=None, client_factory=None, now=None, env=None) -> dict:
    from .backend_client import StrategyBackendClient

    if strategy_id != STRATEGY_ID:
        raise ValueError(f"readiness runs only for {STRATEGY_ID}")
    if step not in STEPS:
        raise ValueError(f"readiness step must be one of {', '.join(STEPS)}")
    env = os.environ if env is None else env
    now = now or shadow.now_kst()
    day = now.date().isoformat()
    user = user or (env.get(USER_ENV) or "").strip() or None
    if user is None:
        return {"status": "refused", "reasons": ["readiness_user_missing"]}
    if not execute:
        return {"status": "dry_run", "step": step, "user": user, "stock": STOCK,
                "note": {"prewarm": "read-only account snapshot", "buy": "one CONT_BUY of 1 share 000660",
                         "flatten": "one CLOSE SELL of the quantity our buy filled",
                         "verify": "balance + broker orders; leftovers alert, M8/unknown leftovers block"}[step]}
    client = (client_factory or StrategyBackendClient)()
    if step == "prewarm":
        snapshot = client.fetch_account(user)
        return {"status": "ok", "record": _record(ctx, day, step, {
            "capturedAt": snapshot.captured_at.isoformat(), "holdings": sorted(snapshot.holdings)}, now)}
    if step == "buy":
        reasons = buy_refusals(ctx, client, user, day)
        price = _reference_price(ctx.data_dir, day)
        if price is None:
            reasons.append("reference_price_missing")
        if reasons:
            alert = _alert(ctx, now, True, env, "readiness_blocked",
                           f"준비 시험 매수를 막았습니다: {', '.join(reasons)}", {"step": step, "reasons": reasons})
            return {"status": "refused", "reasons": reasons, "alerts": alert,
                    "record": _record(ctx, day, step, {"refused": reasons}, now)}
        plan = rebalance.RebalancePlan(
            STRATEGY_ID, "readiness", _hash(f"{STRATEGY_ID}|{day}|buy"), day, day, "CONT_BUY",
            (rebalance.Order(1, STOCK, "BUY", 1, float(price), float(price), None, False, "readiness_buy"),),
            (), (), 0.0, 0.0, 0.0, {"cost_reserve_rate": 0.0})
        payload = rebalance.batch_payload(plan, user_id=user, expected_nav_krw=0, dry_run=False)
        response = client.submit_batch(payload)
        from .runtime import classify_response

        return {"status": classify_response(response)[0], "response": response,
                "record": _record(ctx, day, step, {"payload": payload, "response": response}, now)}
    if step == "flatten":
        path = step_path(ctx.data_dir, day, "buy")
        bought = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        batch_id = ((bought or {}).get("response") or {}).get("batchId")
        if not batch_id:
            return {"status": "nothing_to_submit", "reason": "no_buy_batch_today",
                    "record": _record(ctx, day, step, {"note": "no_buy_batch_today"}, now)}
        details = _batch_details(client, batch_id)
        filled = sum(int(o.get("filledQuantity") or 0) for o in details["orders"] if o.get("side") == "BUY")
        if filled <= 0:
            return {"status": "nothing_to_submit", "reason": "buy_batch_filled_nothing",
                    "record": _record(ctx, day, step, {"buy": details, "note": "buy_batch_filled_nothing"}, now)}
        price = _reference_price(ctx.data_dir, day) or 1.0
        plan = rebalance.RebalancePlan(
            STRATEGY_ID, "readiness", _hash(f"{STRATEGY_ID}|{day}|flatten"), day, day, "CLOSE",
            (rebalance.Order(1, STOCK, "SELL", filled, float(price), float(price * filled), None, False,
                             "readiness_flatten"),), (), (), 0.0, 0.0, 0.0, {})
        payload = rebalance.batch_payload(plan, user_id=user, expected_nav_krw=0, dry_run=False)
        response = client.submit_batch(payload)
        from .runtime import classify_response

        return {"status": classify_response(response)[0], "quantity": filled, "response": response,
                "record": _record(ctx, day, step, {"buy": details, "payload": payload, "response": response}, now)}
    # verify
    own = {}
    for name in ("buy", "flatten"):
        path = step_path(ctx.data_dir, day, name)
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        batch_id = (record.get("response") or {}).get("batchId")
        if batch_id:
            own[name] = _batch_details(client, batch_id)
    own_odnos = {o.get("brokerOrderId") for d in own.values() for o in d["orders"] if o.get("brokerOrderId")}
    snapshot = client.fetch_account(user)
    broker = client.broker_orders(user, day)
    orders = broker.get("orders") or []
    leftovers = [{"kind": "holding", "stockCode": code, "quantity": qty, "owner": _owner(code, None, own_odnos)}
                 for code, qty in sorted(snapshot.holdings.items()) if qty]
    leftovers += [{"kind": "open_order", "stockCode": o.get("stockCode"), "odno": o.get("odno"),
                   "remainingQty": o.get("remainingQty"), "owner": _owner(o.get("stockCode"), o.get("odno"), own_odnos)}
                  for o in orders if _open(o)]
    if not leftovers and int(broker.get("openOrders") or 0) > 0:
        leftovers.append({"kind": "open_order", "stockCode": None, "count": broker.get("openOrders"),
                          "owner": "unknown"})
    body = {"own": own, "ownOdnos": sorted(own_odnos), "holdings": dict(sorted(snapshot.holdings.items())),
            "openOrders": broker.get("openOrders"), "leftovers": leftovers,
            "brokerOrders": [o for o in orders if o.get("odno") in own_odnos or o.get("stockCode") == STOCK]}
    result = {"status": "ok" if not leftovers else "leftover", "leftovers": leftovers}
    if leftovers:
        owners = sorted({item["owner"] for item in leftovers})
        blocking = [item for item in leftovers if item["owner"] in ("M8", "unknown")]
        if blocking:
            shadow.write_immutable(marker_path(ctx.data_dir, day), {
                "strategyId": STRATEGY_ID, "session": day, "leftovers": leftovers, "recorded_at": now.isoformat()})
            result["marker"] = str(marker_path(ctx.data_dir, day))
        names = ", ".join(f"{i['owner']} {i['kind']} {i.get('stockCode')}" for i in leftovers)
        result["alerts"] = _alert(ctx, now, True, env, "readiness_leftover", f"준비 시험 계좌에 남은 것: {names}",
                                  {"step": step, "owners": owners, "leftovers": leftovers,
                                   "blocksNextBuy": bool(blocking)})
    result["record"] = _record(ctx, day, step, body, now)
    return result
