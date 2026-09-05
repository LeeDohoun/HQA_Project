"""Compare explicitly supplied net-equity and fill observations without any API."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Annotated, Literal

import numpy as np
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StrictFloat, StringConstraints, model_validator

from backtesting.leader_backtest import _max_drawdown


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Observation timestamps must be timezone-aware ISO strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Observation timestamps require a timezone")
    return parsed.astimezone(timezone.utc)


Timestamp = Annotated[AwareDatetime, BeforeValidator(_timestamp)]
Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
StockCode = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{6}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Period(Contract):
    start: Timestamp
    end: Timestamp


class Costs(Contract):
    fee_bps: StrictFloat = Field(ge=0)
    slippage_bps: StrictFloat = Field(ge=0)


class Position(Contract):
    stock_code: StockCode
    sector: Name
    market_value: StrictFloat = Field(ge=0)


class EquityObservation(Contract):
    timestamp: Timestamp
    net_equity: StrictFloat = Field(gt=0)
    positions: list[Position]


class Fill(Contract):
    fill_id: Name
    timestamp: Timestamp
    stock_code: StockCode
    side: Literal["BUY", "SELL"]
    notional: StrictFloat = Field(gt=0)
    fees: StrictFloat = Field(ge=0)


class ObservedRun(Contract):
    universe: list[StockCode] = Field(min_length=1)
    currency: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]{3}$")]
    period: Period
    cost_assumptions: Costs
    equity_basis: Literal["net_of_fees_and_slippage"]
    cash_flows: None
    equity: list[EquityObservation] = Field(min_length=2)
    fills: list[Fill]

    @model_validator(mode="after")
    def valid_observations(self):
        universe = set(self.universe)
        if len(universe) != len(self.universe):
            raise ValueError("Universe must contain unique stocks")
        times = [row.timestamp for row in self.equity]
        if any(left >= right for left, right in zip(times, times[1:])):
            raise ValueError("Equity observations must be strictly chronological")
        if (times[0], times[-1]) != (self.period.start, self.period.end):
            raise ValueError("Equity endpoints must match the declared period")
        for row in self.equity:
            codes = [position.stock_code for position in row.positions]
            if len(codes) != len(set(codes)) or not set(codes) <= universe:
                raise ValueError("Positions must be unique stocks in the universe")
        if len({fill.fill_id for fill in self.fills}) != len(self.fills):
            raise ValueError("Duplicate observed fill ID")
        if any(a.timestamp > b.timestamp for a, b in zip(self.fills, self.fills[1:])):
            raise ValueError("Fills must be chronological")
        if any(fill.stock_code not in universe or not self.period.start <= fill.timestamp <= self.period.end
               for fill in self.fills):
            raise ValueError("Fills must belong to the declared universe and period")
        return self


class Comparison(Contract):
    strategy: ObservedRun
    numerical_baseline: ObservedRun
    buy_and_hold: ObservedRun

    @model_validator(mode="after")
    def comparable(self):
        reference = self.strategy
        sectors = {}
        for run in (self.strategy, self.numerical_baseline, self.buy_and_hold):
            if (set(run.universe) != set(reference.universe) or run.currency != reference.currency
                    or run.period != reference.period or run.cost_assumptions != reference.cost_assumptions
                    or [row.timestamp for row in run.equity] != [row.timestamp for row in reference.equity]):
                raise ValueError("Comparison requires identical universe, currency, period, costs and observation timestamps")
            for row in run.equity:
                for position in row.positions:
                    if sectors.setdefault(position.stock_code, position.sector) != position.sector:
                        raise ValueError("Use the same sector classification throughout the comparison")
        return self


def _metrics(run: ObservedRun) -> dict:
    equity = np.array([row.net_equity for row in run.equity])
    count = len(equity)
    average_equity = math.fsum(value / count for value in equity)
    traded = math.fsum(fill.notional for fill in run.fills)
    sectors = {position.sector for row in run.equity for position in row.positions}
    sector_exposure = {sector: math.fsum(
        position.market_value / row.net_equity * 100 / count
        for row in run.equity for position in row.positions if position.sector == sector)
        for sector in sorted(sectors)}
    return {"observations": count, "fill_count": len(run.fills),
            "initial_net_equity": float(equity[0]), "final_net_equity": float(equity[-1]),
            "net_return_pct": float((equity[-1] / equity[0] - 1) * 100),
            "max_drawdown_pct": _max_drawdown(equity) * 100,
            "traded_notional": traded, "one_way_turnover": traded / average_equity,
            "mean_market_exposure_pct": math.fsum(sector_exposure.values()),
            "mean_sector_exposure_pct": sector_exposure,
            "fees_paid": math.fsum(fill.fees for fill in run.fills)}


def compare_performance(payload: dict) -> dict:
    comparison = Comparison.model_validate(payload)
    runs = {name: _metrics(getattr(comparison, name)) for name in Comparison.model_fields}
    return {"currency": comparison.strategy.currency,
            "universe": sorted(comparison.strategy.universe),
            "period": comparison.strategy.period.model_dump(mode="json"),
            "cost_assumptions": comparison.strategy.cost_assumptions.model_dump(),
            "equity_basis": comparison.strategy.equity_basis,
            "turnover_definition": "sum of each BUY and SELL fill notional once / arithmetic mean observed net equity; not annualized",
            "exposure_weighting": "equal weight per aligned observation, including cash-only observations",
            "runs": runs,
            "excess_net_return_percentage_points": {
                name: runs["strategy"]["net_return_pct"] - runs[name]["net_return_pct"]
                for name in ("numerical_baseline", "buy_and_hold")},
            "not_measured": ["unfilled or rejected orders", "realized slippage attribution",
                             "intraperiod drawdown between observations", "point-in-time source integrity",
                             "statistical significance or future profitability"]}
