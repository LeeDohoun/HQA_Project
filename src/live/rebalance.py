"""Order lists from target weights and a book. Pure functions: no files, no network.

Quantities use the reference price (the decision-day close), are floored to whole
shares and leave the estimated one-way cost in cash. Orders that exceed 1% of
ADV20 are flagged, never shrunk: the preregistered rule is not changed here.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field

from backtesting import cost_model

ADV_WARNING_SHARE = 0.01
PHASES = ("OPEN", "CLOSE")


class PlanError(ValueError):
    """A plan cannot be priced or is inconsistent; nothing may be ordered from it."""


@dataclass(frozen=True)
class Order:
    seq: int
    stock_code: str
    side: str
    quantity: int
    ref_price: float
    notional: float
    adv20_krw: float | None
    adv_warning: bool
    reason: str
    market: str | None = None
    cost_rate: float | None = None


@dataclass(frozen=True)
class RebalancePlan:
    strategy_id: str
    execution: str
    decision_hash: str
    decision_date: str
    session_date: str
    phase: str
    orders: tuple[Order, ...]
    skipped: tuple[dict, ...]
    targets: tuple[str, ...]
    cash_before: float
    cash_after_estimate: float
    value_estimate: float
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["orders"] = [asdict(order) for order in self.orders]
        data["skipped"] = [dict(item) for item in self.skipped]
        data["targets"] = list(self.targets)
        data["summary"] = summary(self)
        data["funding"] = funding(self)
        return data


def summary(plan: RebalancePlan) -> dict:
    buys = [o for o in plan.orders if o.side == "BUY"]
    sells = [o for o in plan.orders if o.side == "SELL"]
    return {"sessionDate": plan.session_date, "phase": plan.phase, "buyCount": len(buys),
            "sellCount": len(sells), "buyNotionalKrw": round(sum(o.notional for o in buys)),
            "sellNotionalKrw": round(sum(o.notional for o in sells)),
            "advWarnings": sum(o.adv_warning for o in plan.orders)}


def funding(plan: RebalancePlan) -> dict:
    """How the buys are paid for. Research sizing (HC003) funds buys with cash after sells,
    but at the pre-open auction sell proceeds are not yet settled cash; this makes the
    dependency explicit without changing the preregistered rule."""
    sells = sum(o.notional * (1 - (o.cost_rate or 0.0)) for o in plan.orders if o.side == "SELL")
    buys = sum(o.notional * (1 + (o.cost_rate or 0.0)) for o in plan.orders if o.side == "BUY")
    return {"deposit_cash_krw": round(plan.cash_before), "sell_proceeds_assumed_krw": round(sells),
            "buy_required_krw": round(buys),
            "requires_same_auction_sell_proceeds": bool(buys > plan.cash_before + 1e-6)}


def _valid_price(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def estimate_rate(price, market, session_date, adv20, side, model_version) -> float:
    try:
        return cost_model.one_way_cost(float(price), market, session_date.replace("-", ""), float(adv20),
                                       side, model_version=model_version)
    except ValueError as error:
        raise PlanError(f"cost_model_error:{error}") from None


def _order(code, side, qty, price, adv, reason, market, rate) -> dict:
    notional = float(qty * price)
    return {"stock_code": code, "side": side, "quantity": int(qty), "ref_price": float(price),
            "notional": notional, "adv20_krw": None if adv is None else float(adv),
            "adv_warning": bool(adv is not None and notional > ADV_WARNING_SHARE * adv),
            "reason": reason, "market": market, "cost_rate": rate}


def _sequence(rows: list[dict]) -> tuple[Order, ...]:
    """Sells first (by code), then buys (by code); seq starts at 1."""
    ordered = sorted((r for r in rows if r["side"] == "SELL"), key=lambda r: r["stock_code"]) + \
        sorted((r for r in rows if r["side"] == "BUY"), key=lambda r: r["stock_code"])
    keys = [(r["stock_code"], r["side"]) for r in ordered]
    if len(set(keys)) != len(keys):
        raise PlanError("duplicate order for one stock and side")
    return tuple(Order(seq=i, **row) for i, row in enumerate(ordered, 1))


def _require(session_date, phase, cash):
    if phase not in PHASES:
        raise PlanError(f"unknown phase: {phase}")
    if not isinstance(cash, (int, float)) or not math.isfinite(cash) or cash < 0:
        raise PlanError("cash must be finite and nonnegative")


def plan_basket_entry(strategy_id, decision, *, cash, ref_prices, markets, adv20, model_version,
                      safety=0.0) -> RebalancePlan:
    """basket_20 entry at the trade-date OPEN: decision weights over the available cash.

    qty = floor(cash * w * (1 - buy_cost - safety) / ref_price); qty 0 is skipped.
    A target that cannot be sized stays as cash (HF001: unfilled weight is cash).
    """
    session_date = decision.trade_date
    _require(session_date, "OPEN", cash)
    if not 0 <= safety < 1:
        raise PlanError("safety must be in [0, 1)")
    rows, skipped, spent = [], [], 0.0
    for code in decision.targets:
        price, market, adv = ref_prices.get(code), markets.get(code), adv20.get(code)
        if not _valid_price(price):
            skipped.append({"stock_code": code, "side": "BUY", "reason": "missing_ref_price"})
            continue
        if market is None:
            skipped.append({"stock_code": code, "side": "BUY", "reason": "missing_market"})
            continue
        if adv is None:
            skipped.append({"stock_code": code, "side": "BUY", "reason": "missing_adv20"})
            continue
        rate = estimate_rate(price, market, session_date, adv, "buy", model_version)
        qty = math.floor(cash * decision.weights[code] * (1 - rate - safety) / price)
        if qty <= 0:
            skipped.append({"stock_code": code, "side": "BUY", "reason": "zero_quantity"})
            continue
        rows.append(_order(code, "BUY", qty, price, adv, "basket_entry", market, rate))
        spent += qty * price * (1 + rate)
    if spent > cash + 1e-6:
        raise PlanError("basket entry exceeds available cash")
    return RebalancePlan(strategy_id, "basket_20", decision.record_hash, decision.decision_date, session_date,
                         "OPEN", _sequence(rows), tuple(skipped), decision.targets, float(cash),
                         float(cash - spent), float(cash))


def plan_basket_exit(strategy_id, *, decision_hash, decision_date, session_date, holdings, cash, ref_prices,
                     markets, adv20, model_version, targets=()) -> RebalancePlan:
    """basket_20 exit at the exit-date CLOSE: sell every basket holding."""
    _require(session_date, "CLOSE", cash)
    rows, skipped, proceeds, value = [], [], 0.0, float(cash)
    for code in sorted(holdings):
        qty = holdings[code]
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
            raise PlanError(f"holding quantity must be a nonnegative integer: {code}")
        if qty == 0:
            continue
        price, market, adv = ref_prices.get(code), markets.get(code), adv20.get(code)
        if not _valid_price(price):
            raise PlanError(f"unvalued_holding:{code}")
        value += qty * price
        if market is None or adv is None:
            raise PlanError(f"missing_cost_inputs:{code}")
        rate = estimate_rate(price, market, session_date, adv, "sell", model_version)
        rows.append(_order(code, "SELL", qty, price, adv, "basket_exit", market, rate))
        proceeds += qty * price * (1 - rate)
    return RebalancePlan(strategy_id, "basket_20", decision_hash, decision_date, session_date, "CLOSE",
                         _sequence(rows), tuple(skipped), tuple(targets), float(cash), float(cash + proceeds),
                         float(value))


def plan_continuous(strategy_id, decision, *, holdings, cash, ref_prices, markets, adv20, model_version,
                    band, frozen=()) -> RebalancePlan:
    """continuous rebalance at the trade-date OPEN, following HC003 rebalance conventions.

    Held stocks that cannot trade (no market/ADV, or listed in frozen) keep their value;
    targets are equal to the decision weights renormalised over executable targets of
    the allocatable value. Differences below band * value are skipped except full exits.
    Sells come first; buys are scaled down proportionally to the cash after sells.
    """
    session_date = decision.trade_date
    _require(session_date, "OPEN", cash)
    if not isinstance(band, (int, float)) or not 0 <= band < 1:
        raise PlanError("band must be in [0, 1)")
    held = {code: qty for code, qty in holdings.items() if qty}
    for code, qty in held.items():
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
            raise PlanError(f"holding quantity must be a nonnegative integer: {code}")
        if not _valid_price(ref_prices.get(code)):
            raise PlanError(f"unvalued_holding:{code}")
    value = float(cash) + sum(qty * ref_prices[code] for code, qty in held.items())
    targets = set(decision.targets)
    skipped = []
    frozen = {code for code in held if code in set(frozen) or markets.get(code) is None or adv20.get(code) is None}
    for code in sorted(frozen):
        skipped.append({"stock_code": code, "side": None, "reason": "frozen_holding"})
    executable = set()
    for code in sorted(targets - frozen):
        if not _valid_price(ref_prices.get(code)):
            skipped.append({"stock_code": code, "side": "BUY", "reason": "missing_ref_price"})
        elif markets.get(code) is None:
            skipped.append({"stock_code": code, "side": "BUY", "reason": "missing_market"})
        elif adv20.get(code) is None:
            skipped.append({"stock_code": code, "side": "BUY", "reason": "missing_adv20"})
        else:
            executable.add(code)
    allocatable = value - sum(held[code] * ref_prices[code] for code in frozen)
    total_weight = sum(decision.weights[code] for code in executable)
    sells, buys = [], {}
    for code in sorted((set(held) | executable) - frozen):
        price = ref_prices[code]
        current = held.get(code, 0) * price
        full_exit = code in held and code not in targets
        target = 0.0 if full_exit or code not in executable else allocatable * decision.weights[code] / total_weight
        delta = target - current
        if not full_exit and abs(delta) < band * value:
            if delta != 0 or code in targets:
                skipped.append({"stock_code": code, "side": "SELL" if delta < 0 else "BUY", "reason": "within_band"})
            continue
        if delta < 0:
            qty = held[code] if full_exit or target == 0 else min(held[code], math.floor(-delta / price))
            if qty <= 0:
                skipped.append({"stock_code": code, "side": "SELL", "reason": "zero_quantity"})
                continue
            reason = "full_exit" if full_exit else "rebalance_decrease"
            sells.append((code, qty, reason))
        elif delta > 0:
            buys[code] = delta
    rows, cash_now = [], float(cash)
    for code, qty, reason in sells:
        price, market, adv = ref_prices[code], markets[code], adv20[code]
        rate = estimate_rate(price, market, session_date, adv, "sell", model_version)
        rows.append(_order(code, "SELL", qty, price, adv, reason, market, rate))
        cash_now += qty * price * (1 - rate)
    rates = {code: estimate_rate(ref_prices[code], markets[code], session_date, adv20[code], "buy", model_version)
             for code in buys}
    needed = sum(amount * (1 + rates[code]) for code, amount in buys.items())
    scale = min(1.0, cash_now / needed) if needed else 1.0
    for code, desired in sorted(buys.items()):
        price = ref_prices[code]
        qty = math.floor(desired * scale / price)
        if qty <= 0:
            skipped.append({"stock_code": code, "side": "BUY", "reason": "zero_quantity"})
            continue
        reason = "rebalance_increase" if code in held else "new_position"
        rows.append(_order(code, "BUY", qty, price, adv20[code], reason, markets[code], rates[code]))
        cash_now -= qty * price * (1 + rates[code])
    if cash_now < -1e-6:
        raise PlanError("continuous rebalance would borrow cash")
    return RebalancePlan(strategy_id, "continuous", decision.record_hash, decision.decision_date, session_date,
                         "OPEN", _sequence(rows), tuple(skipped), decision.targets, float(cash), float(cash_now),
                         float(value), meta={"buy_scale": scale, "band_krw": band * value,
                                             "allocatable": allocatable, "executable": len(executable)})


def idempotency_key(strategy_id, decision_hash, session_date, phase, user_id) -> str:
    text = f"{strategy_id}|{decision_hash}|{session_date}|{phase}|{user_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _number(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def batch_payload(plan: RebalancePlan, *, user_id: str, expected_nav_krw: float, dry_run: bool) -> dict:
    """POST /api/v1/internal/strategies/batches body (docs/strategy-runtime.md)."""
    if not isinstance(user_id, str) or not user_id:
        raise PlanError("batch payload requires a user id")
    orders = list(plan.orders)
    if not orders:
        raise PlanError("batch payload requires at least one order")
    keys = [(o.stock_code, o.side) for o in orders]
    if len(set(keys)) != len(keys):
        raise PlanError("duplicate order for one stock and side")
    sells = [o.seq for o in orders if o.side == "SELL"]
    buys = [o.seq for o in orders if o.side == "BUY"]
    if sells and buys and max(sells) >= min(buys):
        raise PlanError("sell orders must precede buy orders")
    if any(o.quantity <= 0 for o in orders):
        raise PlanError("order quantities must be positive")
    return {
        "strategyId": plan.strategy_id, "userId": user_id, "decisionHash": plan.decision_hash,
        "decisionDate": plan.decision_date, "sessionDate": plan.session_date, "phase": plan.phase,
        "idempotencyKey": idempotency_key(plan.strategy_id, plan.decision_hash, plan.session_date, plan.phase,
                                          user_id),
        "dryRun": bool(dry_run), "expectedNavKrw": _number(round(float(expected_nav_krw))),
        "orders": [{"seq": o.seq, "stockCode": o.stock_code, "side": o.side, "quantity": o.quantity,
                    "refPrice": _number(o.ref_price), "adv20Krw": o.adv20_krw} for o in orders],
    }
