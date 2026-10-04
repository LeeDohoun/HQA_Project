"""Pure account risk decisions; only the operator's kill switch reads a file."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class AccountState:
    equity_now: float
    equity_start_of_day: float
    equity_peak: float
    orders_today_by_stock: dict[str, int]
    cancels_today_by_stock: dict[str, int]
    orders_today_total: int


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    full_review_required: bool = False


def _number(value, name, *, positive=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{name} must be a finite number")
    if not np.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")


def _count(value, name, *, minimum=0):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _fraction(value, name):
    _number(value, name)
    if value > 1:
        raise ValueError(f"{name} must be in [0, 1]")


def check_new_entry(state: AccountState, *, daily_loss_limit=0.02, mdd_stop=0.10) -> RiskDecision:
    """Block strictly above the day-loss limit and at/above the peak drawdown limit.

    Loss is relative to start-of-day equity; drawdown is relative to equity_peak.
    The fixed 15% review flag requests a liquidation decision, not an automatic
    liquidation. Thresholds follow roadmap 6.4; backend checks remain independent.
    """
    _number(state.equity_now, "equity_now")
    _number(state.equity_start_of_day, "equity_start_of_day", positive=True)
    _number(state.equity_peak, "equity_peak", positive=True)
    _fraction(daily_loss_limit, "daily_loss_limit")
    _fraction(mdd_stop, "mdd_stop")
    reasons = []
    # Compare equity thresholds directly to avoid cancellation in 1 - now / base.
    if state.equity_now < state.equity_start_of_day * (1 - daily_loss_limit):
        reasons.append("daily_loss_limit")
    if state.equity_now <= state.equity_peak * (1 - mdd_stop):
        reasons.append("mdd_stop")
    allowed = not reasons
    full_review = state.equity_now <= state.equity_peak * 0.85
    if full_review:
        reasons.append("full_review_required")
    return RiskDecision(allowed, tuple(reasons), full_review)


def check_order(
    state: AccountState, stock_code, *, max_orders_per_stock_per_day=5,
    max_cancel_ratio=0.5, max_orders_per_day=100, order_qty,
    avg_minute_volume=None, max_qty_vs_minute_volume=0.2,
) -> RiskDecision:
    """Check a proposed order against history before counting that new order.

    Cancel ratio uses this stock's cancels / orders, before the proposal; a new
    order cannot dilute an already excessive cancel ratio. Zero historical orders
    imply zero cancels. Missing stock keys mean no activity, not missing market data.
    avg_minute_volume, when supplied, is shares/minute; None skips that one check
    explicitly, so the caller must supply it for volume-sensitive order screening.
    """
    # Conservative self-imposed limits, not legal market-manipulation thresholds.
    # They complement, rather than replace, independent backend order validation.
    if not isinstance(stock_code, str) or not stock_code.strip():
        raise ValueError("stock_code must be a nonblank string")
    _count(max_orders_per_stock_per_day, "max_orders_per_stock_per_day", minimum=1)
    _count(max_orders_per_day, "max_orders_per_day", minimum=1)
    _count(order_qty, "order_qty", minimum=1)
    _fraction(max_cancel_ratio, "max_cancel_ratio")
    _fraction(max_qty_vs_minute_volume, "max_qty_vs_minute_volume")
    _count(state.orders_today_total, "orders_today_total")
    for code, orders in state.orders_today_by_stock.items():
        _count(orders, f"orders for {code}")
    for code, cancels in state.cancels_today_by_stock.items():
        _count(cancels, f"cancels for {code}")
        if cancels > state.orders_today_by_stock.get(code, 0):
            raise ValueError("cancels cannot exceed historical orders")
    if sum(state.orders_today_by_stock.values()) > state.orders_today_total:
        raise ValueError("per-stock orders cannot exceed orders_today_total")
    orders = state.orders_today_by_stock.get(stock_code, 0)
    cancels = state.cancels_today_by_stock.get(stock_code, 0)
    reasons = []
    if orders >= max_orders_per_stock_per_day:
        reasons.append("max_orders_per_stock_per_day")
    if cancels > max_cancel_ratio * orders:
        reasons.append("max_cancel_ratio")
    if state.orders_today_total >= max_orders_per_day:
        reasons.append("max_orders_per_day")
    if avg_minute_volume is not None:
        _number(avg_minute_volume, "avg_minute_volume")
        if order_qty > max_qty_vs_minute_volume * avg_minute_volume:
            reasons.append("max_qty_vs_minute_volume")
    return RiskDecision(not reasons, tuple(reasons))


def kill_switch(path: str | Path) -> bool:
    """The operator's named file stops trading when it exists; never creates it."""
    return Path(path).exists()
