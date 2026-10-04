"""Deterministic, capacity-constrained equal-risk strategy allocation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .position_sizing import _integer, _number


@dataclass(frozen=True)
class StrategyStats:
    strategy_id: str
    stage: str
    realized_vol: float
    capacity_krw: float
    correlation_group: str
    rolling_ic_6m: float
    realized_vs_expected_percentile: float
    months_in_decay: int


def allocate(stats, total_capital_krw, *, target_portfolio_vol=0.15,
             max_risk_share=0.40, group_risk_cap=0.60) -> dict:
    """Return allocations, unallocated_capital_krw, and cash reasons.

    Only small_live/scaled are PAPER-passed. Risk is allocation_krw * realized_vol;
    risk_share divides by B = total_capital_krw * target_portfolio_vol. This is a
    standalone-volatility budget, not covariance-based portfolio risk. Each group
    is a disjoint label. Start with equal risk B / n, redistributing capped risk
    equally among uncapped strategies. Progressive filling stops at the risk
    budget, the capital limit, or when every strategy is capped; unused capital
    stays in cash. Portfolio reasons report binding constraints, including
    capital_limit and budget_fully_used. decay_action is a separate policy
    decision and does not silently alter allocations here.
    """
    _number(total_capital_krw, "total_capital_krw")
    for value, name in ((target_portfolio_vol, "target_portfolio_vol"),
                        (max_risk_share, "max_risk_share"), (group_risk_cap, "group_risk_cap")):
        _number(value, name, positive=True)
        if value > 1:
            raise ValueError(f"{name} must be in (0, 1]")
    items = list(stats)
    for item in items:
        if not isinstance(item, StrategyStats):
            raise ValueError("stats must contain StrategyStats")
        for value, name in ((item.strategy_id, "strategy_id"), (item.stage, "stage"),
                            (item.correlation_group, "correlation_group")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonblank string")
        _number(item.capacity_krw, "capacity_krw")
        if item.stage in ("small_live", "scaled"):
            _number(item.realized_vol, "realized_vol", positive=True)
    if len({item.strategy_id for item in items}) != len(items):
        raise ValueError("strategy IDs must be unique")
    items.sort(key=lambda item: item.strategy_id)
    eligible = [item for item in items if item.stage in ("small_live", "scaled") and item.capacity_krw > 0]
    risk_budget = total_capital_krw * target_portfolio_vol
    amounts = np.zeros(len(eligible))
    cash_reasons = []
    if total_capital_krw == 0:
        cash_reasons.append("zero_capital")
    elif not eligible:
        cash_reasons.append("no_eligible_capacity")
    else:
        vols = np.array([item.realized_vol for item in eligible])
        capacities = np.array([item.capacity_krw for item in eligible])
        risk_capacity = capacities * vols
        _number(risk_budget, "portfolio risk budget", positive=True)
        if not np.isfinite(risk_capacity).all():
            raise ValueError("capacity risk must be finite")
        caps = np.minimum(max_risk_share, risk_capacity / risk_budget)
        groups = [np.array([item.correlation_group == group for item in eligible])
                  for group in sorted({item.correlation_group for item in eligible})]
        shares = np.zeros(len(eligible))
        capital_per_share = target_portfolio_vol / vols
        active = caps > 0
        for _ in range(len(eligible) + 1):
            if not active.any():
                break
            budget_step = (1.0 - shares.sum()) / active.sum()
            capital_step = (1.0 - np.dot(shares, capital_per_share)) / capital_per_share[active].sum()
            step = min(budget_step, capital_step, np.min(caps[active] - shares[active]))
            for group in groups:
                members = group & active
                if members.any():
                    step = min(step, (group_risk_cap - shares[group].sum()) / members.sum())
            shares[active] += max(0.0, step)
            if step >= min(budget_step, capital_step):
                break
            active &= shares < caps - 1e-12
            for group in groups:
                if shares[group].sum() >= group_risk_cap - 1e-12:
                    active[group] = False
        else:
            raise RuntimeError("capped risk redistribution did not converge")
        amounts = np.minimum(capacities, risk_budget * shares / vols)
        allocated = sum(float(amount) for amount in amounts)
        if allocated > total_capital_krw:
            amounts *= total_capital_krw / allocated  # Roundoff must not exceed the capital limit.
    by_id = {item.strategy_id: float(amount) for item, amount in zip(eligible, amounts)}
    risks = {item.strategy_id: by_id[item.strategy_id] * item.realized_vol for item in eligible}
    total_risk = sum(risks.values())
    group_risks = {group: sum(risks[item.strategy_id] for item in eligible if item.correlation_group == group)
                   for group in {item.correlation_group for item in eligible}}
    rows = []
    for item in items:
        amount = by_id.get(item.strategy_id, 0.0)
        share = risks.get(item.strategy_id, 0.0) / risk_budget if risk_budget else 0.0
        reasons = []
        if item.stage not in ("small_live", "scaled"):
            reasons.append("stage_not_eligible")
        elif total_capital_krw == 0:
            reasons.append("zero_capital")
        elif item.capacity_krw == 0:
            reasons.append("capacity_krw")
        else:
            if np.isclose(amount, item.capacity_krw, rtol=1e-10, atol=1e-6):
                reasons.append("capacity_krw")
            if np.isclose(share, max_risk_share, rtol=0, atol=1e-10):
                reasons.append("max_risk_share")
            if np.isclose(group_risks[item.correlation_group] / risk_budget, group_risk_cap,
                                         rtol=0, atol=1e-10):
                reasons.append("group_risk_cap")
        rows.append((item.strategy_id, item.correlation_group, amount,
                     amount / total_capital_krw if total_capital_krw else 0.0, share, tuple(reasons)))
    table = pd.DataFrame(rows, columns=["strategy_id", "correlation_group", "allocation_krw",
                                        "weight", "risk_share", "reasons"])
    cash = float(total_capital_krw - sum(by_id.values()))
    total_risk_used_share = total_risk / risk_budget if risk_budget else 0.0
    if not cash_reasons:
        for reason in ("max_risk_share", "group_risk_cap", "capacity_krw"):
            if any(row[2] > 0 and reason in row[-1] for row in rows):
                cash_reasons.append(reason)
        if np.isclose(sum(by_id.values()) / total_capital_krw, 1.0, rtol=0, atol=1e-10):
            cash_reasons.append("capital_limit")
        if np.isclose(total_risk_used_share, 1.0, rtol=0, atol=1e-10):
            cash_reasons.append("budget_fully_used")
    return {"allocations": table, "unallocated_capital_krw": cash,
            "total_risk_used_share": total_risk_used_share, "reasons": tuple(cash_reasons)}


def decay_action(stats: StrategyStats) -> str:
    """Roadmap 6.1 thresholds; changing them requires a new preregistration.

    months_in_decay is the caller's consecutive months with either adverse
    condition. A recovered strategy is kept even if that counter was not reset.
    """
    ic = stats.rolling_ic_6m
    if isinstance(ic, (bool, np.bool_)) or not isinstance(ic, (int, float, np.number)) or not np.isfinite(ic):
        raise ValueError("rolling_ic_6m must be finite")
    _number(stats.realized_vs_expected_percentile, "realized_vs_expected_percentile")
    if stats.realized_vs_expected_percentile > 100:
        raise ValueError("realized_vs_expected_percentile must be in [0, 100]")
    _integer(stats.months_in_decay, "months_in_decay", minimum=0)
    decaying = ic < 0 or stats.realized_vs_expected_percentile < 5
    if not decaying:
        return "keep"
    return "retire" if stats.months_in_decay >= 12 else "halve"
