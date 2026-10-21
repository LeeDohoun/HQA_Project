"""Section 5, in order, on each book's own raw T-close pre-trade NAV."""
from copy import deepcopy
from datetime import date
import math

from backtesting.cost_model import one_way_cost
from . import INITIAL_CASH, Refused
from .storage import digest


def initial_state():
    return {"date": None, "cash": INITIAL_CASH, "positions": {}, "nav": INITIAL_CASH,
            "peak_nav": INITIAL_CASH, "applied_actions": []}


def targets(output, *, level=None, denominator=None):
    level = output["invest_level"] if level is None else level
    total = sum(h["conviction"] for h in output["holdings"]) if denominator is None else denominator
    raw = {h["code"]: level / 100 * h["conviction"] / total for h in output["holdings"]} if total else {}
    return {"raw": raw, "weights": {c: min(0.10, w) for c, w in raw.items()}}


def equal_weights(codes):
    return {code: min(0.10, 1 / len(codes)) for code in sorted(codes)} if codes else {}


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0 or positive and value == 0:
        raise Refused(name + " must be finite and " + ("positive" if positive else "nonnegative"))
    return value


def apply_actions(state, events, day):
    """Only explicit official raw-share/cash events; never multiply returns."""
    for event in events:
        if event["effective_day"] != day or not event.get("official_source"):
            raise Refused("corporate action requires its effective day and official source")
        if event["id"] in state["applied_actions"]:
            raise Refused("duplicate corporate action")
        code, kind = event["code"], event["type"]
        position = state["positions"].get(code)
        if kind == "split":
            factor = _number(event["factor"], "split factor", positive=True)
            if position:
                quantity = position["quantity"] * factor
                fraction = quantity - math.floor(quantity)
                if fraction and "fraction_cash_price" not in event:
                    raise Refused("fractional split entitlement requires official cash price")
                state["cash"] += fraction * _number(event.get("fraction_cash_price", 0), "fraction cash price")
                position.update(quantity=math.floor(quantity), avg_cost=position["avg_cost"] / factor,
                                last_close=position["last_close"] / factor)
        elif kind in {"dividend", "rights", "recovery"}:
            # Entitlements are supplied explicitly: payment can follow a sale.
            cash = event["cash_delta"]
            if isinstance(cash, bool) or not isinstance(cash, (int, float)) or not math.isfinite(cash):
                raise Refused("official cash entitlement must be finite")
            state["cash"] += cash
            if kind == "rights":
                quantity = event["quantity_delta"]
                if type(quantity) is not int or position is None and quantity < 0 or position and position["quantity"] + quantity < 0:
                    raise Refused("rights share entitlement is invalid")
                if position is None and quantity:
                    position = {"quantity": 0, "avg_cost": 0, "opened_on": day, "last_seen": day,
                        "last_close": _number(event["reference_price"], "official rights reference price", positive=True),
                        "stale_reason": None, "stale_days": 0, "stale_calendar_days": 0}
                    state["positions"][code] = position
                if position:
                    position.update(quantity=position["quantity"] + quantity,
                                    avg_cost=_number(event["avg_cost_after"], "rights cost basis"))
            elif kind == "recovery" and position:
                del state["positions"][code]
        else:
            raise Refused("unsupported corporate action: " + kind)
        state["applied_actions"].append(event["id"])
    if state["cash"] < 0:
        raise Refused("corporate action would borrow cash")
    state["positions"] = {c: p for c, p in state["positions"].items() if p["quantity"]}


def rebalance(previous, weights, quotes, adv, eligible, day, *, multiplier=1.0, constrained=True,
              liquidate=False, hold=False, missing_day=False, actions=(), cost_fn=one_way_cost):
    state = deepcopy(previous)
    if state["date"] is not None and day <= state["date"]:
        raise Refused("book dates must strictly increase")
    if any(type(q.get(flag)) is not bool for q in quotes.values() for flag in ("halted", "limit_up", "limit_down")):
        raise Refused("execution limit/halt flags must be explicitly verified booleans")
    for code, weight in weights.items():
        _number(weight, "target weight")
        if weight > .10 + 1e-12:
            raise Refused("target exceeds 10% cap")
    if sum(weights.values()) > 1 + 1e-12:
        raise Refused("targets would use leverage")
    apply_actions(state, actions, day)
    marks, reasons = {}, {}
    codes = set(state["positions"]) | set(weights)
    for code in sorted(codes):
        quote, position = quotes.get(code), state["positions"].get(code)
        reason = ("missing_row" if quote is None else
                  "halt" if quote["halted"] else "delisting_unconfirmed" if quote.get("delisted") else None)
        price = quote.get("close") if quote else None
        if price is None or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            reason = reason or "missing_price"
        if reason:
            reasons[code] = reason
            if position:
                marks[code] = _number(position["last_close"], "last confirmed close", positive=True)
                position.update(stale_reason=reason, stale_days=position["stale_days"] + 1,
                                stale_calendar_days=(date.fromisoformat(day) - date.fromisoformat(position["last_seen"])).days)
        else:
            marks[code] = float(price)
            if position:
                position.update(last_close=float(price), last_seen=day, stale_reason=None, stale_days=0,
                                stale_calendar_days=0)
    nav = state["cash"] + sum(p["quantity"] * marks[c] for c, p in state["positions"].items())
    _number(nav, "pre-trade NAV")
    deltas, ignored = {}, {}
    for code in sorted(codes):
        quantity = state["positions"].get(code, {}).get("quantity", 0)
        current = quantity * marks.get(code, 0)
        wanted = 0 if liquidate else weights.get(code, 0) * nav
        if wanted > current and code not in eligible:
            ignored[code] = wanted - current
            wanted = current
        delta = wanted - current
        # Step 3 freezes halted/missing rows; step 4 precedes proportional limits.
        if hold or missing_day or code in reasons:
            delta = 0.0
        if constrained and not liquidate and abs(delta) < .005 * nav:
            delta = 0.0
        deltas[code] = delta
    gross = sum(abs(v) for v in deltas.values())
    scale = min(1.0, .15 * nav / gross) if constrained and not liquidate and gross else 1.0
    capped = {c: v * scale for c, v in deltas.items()}
    for code, delta in capped.items():
        if delta:
            if code not in adv or adv[code] is None:
                reasons[code] = "adv_unavailable"
                capped[code] = 0.0
            else:
                cap = .01 * _number(adv[code], "ADV20")
                capped[code] = math.copysign(min(abs(delta), cap), delta)
    trades, buy_values, rates = [], {}, {}
    for side in ("sell", "buy"):
        for code in sorted(capped):
            value = capped[code]
            if value == 0 or (value > 0) != (side == "buy"):
                continue
            quote = quotes[code]
            if quote["limit_up"] and side == "buy" or quote["limit_down"] and side == "sell":
                reasons[code] = "limit_up" if side == "buy" else "limit_down"
                continue
            rate = cost_fn(marks[code], quote["market"], day.replace("-", ""), adv[code], side,
                           multiplier=multiplier, model_version="v2")
            _number(rate, "cost rate")
            if side == "sell":
                quantity = min(state["positions"][code]["quantity"], math.floor(-value / marks[code]))
                if quantity:
                    amount = quantity * marks[code]
                    state["cash"] += amount * (1 - rate)
                    state["positions"][code]["quantity"] -= quantity
                    trades.append({"code": code, "side": side, "quantity": quantity, "price": marks[code],
                                   "amount": amount, "cost": amount * rate, "rate": rate})
            else:
                buy_values[code], rates[code] = value, rate
    required = sum(v * (1 + rates[c]) for c, v in buy_values.items())
    cash_scale = min(1.0, state["cash"] / required) if required else 1.0
    for code in sorted(buy_values):
        quantity = math.floor(buy_values[code] * cash_scale / marks[code])
        if not quantity:
            continue
        amount, rate = quantity * marks[code], rates[code]
        if code not in state["positions"]:
            state["positions"][code] = {"quantity": 0, "avg_cost": 0.0, "opened_on": day,
                "last_close": marks[code], "last_seen": day, "stale_reason": None, "stale_days": 0,
                "stale_calendar_days": 0}
        position = state["positions"][code]
        position["avg_cost"] = (position["avg_cost"] * position["quantity"] + amount * (1 + rate)) / (position["quantity"] + quantity)
        position["quantity"] += quantity
        state["cash"] -= amount * (1 + rate)
        trades.append({"code": code, "side": "buy", "quantity": quantity, "price": marks[code],
                       "amount": amount, "cost": amount * rate, "rate": rate})
    if state["cash"] < -1e-6:
        raise Refused("negative cash after execution")
    state["positions"] = {c: p for c, p in state["positions"].items() if p["quantity"]}
    state["date"] = day
    state["nav"] = state["cash"] + sum(p["quantity"] * marks[c] for c, p in state["positions"].items())
    state["peak_nav"] = max(previous["peak_nav"], state["nav"])
    return {"state": state, "state_before_sha256": digest(previous), "pre_trade_nav": nav,
            "return": state["nav"] / previous["nav"] - 1 if previous["nav"] else 0.0, "targets": weights,
            "deltas_before_limits": deltas, "deltas_after_limits": capped,
            "turnover_scale": scale, "cash_scale": cash_scale, "ignored_increases": ignored,
            "blocks": reasons, "trades": trades, "cost": sum(t["cost"] for t in trades),
            "turnover": sum(t["amount"] for t in trades) / nav if nav else 0.0,
            "unresolved_value": sum(p["quantity"] * marks[c] for c, p in state["positions"].items() if p["stale_reason"])}
