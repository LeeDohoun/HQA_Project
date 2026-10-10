"""Validated wire contracts for the fixed analysis DAG and executable plans."""
from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, StrictFloat, StrictInt,
                      ValidationError, model_validator)


# The backend refuses an entry whose live price is more than 3% from the plan's entry price.
ENTRY_DRIFT_LIMIT = 0.03


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Citation(Contract):
    source_id: str = Field(min_length=1)
    claim: str = Field(min_length=1, max_length=400)


class SpecialistResult(Contract):
    stock_code: str = Field(pattern=r"^[0-9]{6}$")
    role: Literal["analyst", "quant", "chartist"]
    score: StrictFloat = Field(ge=0, le=100)
    confidence: StrictInt = Field(ge=0, le=100)
    thesis: str = Field(min_length=1, max_length=1500)
    risks: list[str] = Field(max_length=6)
    citations: list[Citation] = Field(min_length=1, max_length=8)
    data_gaps: list[str] = Field(max_length=8)


class Predicate(Contract):
    field: Literal["current_price", "pnl_rate", "holding_quantity", "market_time"]
    operator: Literal[">", ">=", "<", "<=", "==", "!="]
    value: StrictFloat | str

    @model_validator(mode="after")
    def validate_value(self):
        if self.field == "market_time":
            if not isinstance(self.value, str):
                raise ValueError("market_time requires HH:mm:ss")
            parsed = time.fromisoformat(self.value)
            if parsed.tzinfo is not None or len(self.value) != 8:
                raise ValueError("market_time requires local HH:mm:ss")
        else:
            if isinstance(self.value, (str, bool)):
                raise ValueError("numeric condition requires a JSON number")
            if self.field == "current_price" and self.value <= 0:
                raise ValueError("current_price must be positive")
            if self.field == "holding_quantity" and (self.value < 0 or not self.value.is_integer()):
                raise ValueError("holding_quantity must be a nonnegative integer")
        return self


class ConditionGroup(Contract):
    # The backend stores trigger keys from these IDs and accepts only [A-Za-z0-9_-];
    # "planned-exit" is reserved for the monitor's time-based exit.
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    all: list[Predicate] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def reserved_id(self):
        if self.id == "planned-exit":
            raise ValueError("condition id planned-exit is reserved")
        return self


class ReduceConditionGroup(ConditionGroup):
    reduce_fraction: StrictFloat = Field(gt=0, le=1)


class ConditionPayload(Contract):
    schema_version: Literal[2]
    entry_conditions: list[ConditionGroup] = Field(max_length=5)
    exit_conditions: list[ConditionGroup] = Field(max_length=5)
    reduce_conditions: list[ReduceConditionGroup] = Field(max_length=5)
    invalidation_conditions: list[ConditionGroup] = Field(max_length=5)

    @model_validator(mode="after")
    def unique_ids(self):
        groups = self.entry_conditions + self.exit_conditions + self.reduce_conditions + self.invalidation_conditions
        if len({g.id for g in groups}) != len(groups):
            raise ValueError("condition ids must be unique across the plan")
        return self


class TradingPlan(Contract):
    stock_code: str = Field(pattern=r"^[0-9]{6}$")
    stock_name: str = Field(min_length=1)
    action: Literal["BUY", "SELL", "HOLD"]
    holding_quantity: StrictInt = Field(ge=0)
    confidence: StrictInt = Field(ge=0, le=100)
    risk_level: Literal["VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"]
    position_size_pct: StrictFloat = Field(ge=0, le=20)
    entry_price: StrictFloat | None = Field(gt=0)
    stop_loss_price: StrictFloat | None = Field(gt=0)
    take_profit_price: StrictFloat | None = Field(gt=0)
    entry_valid_until: AwareDatetime
    planned_exit_at: AwareDatetime
    condition_payload: ConditionPayload
    citations: list[Citation] = Field(min_length=1, max_length=8)
    reasoning: str = Field(min_length=1, max_length=1800)

    @model_validator(mode="after")
    def validate_plan(self):
        if self.planned_exit_at <= self.entry_valid_until:
            raise ValueError("planned exit must follow entry expiry")
        if self.action == "BUY":
            if None in (self.entry_price, self.stop_loss_price, self.take_profit_price):
                raise ValueError("BUY requires numerical entry, stop and target")
            if not self.stop_loss_price < self.entry_price < self.take_profit_price:
                raise ValueError("BUY requires stop < entry < target")
            if not self.position_size_pct or not self.condition_payload.entry_conditions:
                raise ValueError("BUY requires target equity allocation and entry conditions")
            if not self.condition_payload.exit_conditions or not self.condition_payload.invalidation_conditions:
                raise ValueError("BUY requires exit and invalidation conditions")
            stop_groups = self.condition_payload.exit_conditions + self.condition_payload.invalidation_conditions
            if not any(len(group.all) == 1 and group.all[0].field == "current_price"
                       and group.all[0].operator == "<=" and group.all[0].value == self.stop_loss_price
                       for group in stop_groups):
                raise ValueError("BUY requires an unconditional current_price <= stop_loss_price exit or invalidation group")
        if self.action == "SELL" and self.holding_quantity == 0:
            raise ValueError("SELL requires an existing holding")
        payload = self.condition_payload
        # The backend exits a held position when an invalidation group matches, so only
        # adverse moves (price or pnl falling) may invalidate; "price ran away" is already
        # refused at entry by the backend's 3% drift rule.
        for group in payload.invalidation_conditions:
            if any(p.field in {"current_price", "pnl_rate"} and p.operator not in {"<", "<="} for p in group.all):
                raise ValueError("invalidation conditions must describe adverse moves (current_price/pnl_rate < or <=)")
        if self.holding_quantity == 0 and any(p.field == "pnl_rate" for group in
                                              payload.entry_conditions + payload.invalidation_conditions for p in group.all):
            raise ValueError("pnl_rate needs a held position; entry and invalidation conditions use current_price")
        if self.action == "BUY":
            for group in payload.entry_conditions:
                for p in group.all:
                    if p.field == "current_price" and abs(p.value / self.entry_price - 1) > ENTRY_DRIFT_LIMIT + 1e-9:
                        raise ValueError("entry price conditions must stay within 3% of entry_price (backend drift limit)")
        if self.holding_quantity and not (self.condition_payload.exit_conditions or self.condition_payload.reduce_conditions):
            raise ValueError("held positions require explicit protection conditions")
        return self


class AccountDecision(Contract):
    plans: list[TradingPlan]
    reasoning: str = Field(min_length=1, max_length=1600)
    # A JSON schema cannot express the plan rules (stop < entry < target, adverse
    # invalidation, ...), so a model can break them in one plan. That plan is set aside
    # with its reason instead of voiding the account's other plans, above all its
    # holdings' protection updates. The schema the model sees is unchanged.
    _invalid_plans: list[dict] = PrivateAttr(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def set_aside_invalid_plans(cls, data, handler):
        if not isinstance(data, dict) or not isinstance(data.get("plans"), list):
            return handler(data)
        valid, invalid = [], []
        for item in data["plans"]:
            try:
                valid.append(item if isinstance(item, TradingPlan) else TradingPlan.model_validate(item))
            except ValidationError as exc:
                code = item.get("stock_code") if isinstance(item, dict) else None
                invalid.append({"stock_code": code if isinstance(code, str) else None,
                                "action": item.get("action") if isinstance(item, dict) else None,
                                "reason": "plan contract: " + "; ".join(
                                    (".".join(map(str, error["loc"])) + ": " if error["loc"] else "") + error["msg"]
                                    for error in exc.errors()[:3]),
                                "plan": item})
        codes = [plan.stock_code for plan in valid] + [plan["stock_code"] for plan in invalid]
        if None in codes or len(set(codes)) != len(codes):
            raise ValueError("one plan per stock is required")
        decision = handler({**data, "plans": valid})
        decision._invalid_plans = invalid
        return decision

    @property
    def invalid_plans(self) -> list[dict]:
        return self._invalid_plans


class Holding(Contract):
    stockCode: str = Field(pattern=r"^[0-9]{6}$")
    stockName: str = Field(min_length=1)
    quantity: StrictInt = Field(ge=0)
    sellableQuantity: StrictInt = Field(ge=0)
    avgPrice: StrictFloat = Field(gt=0)
    currentPrice: StrictFloat = Field(gt=0)
    evalAmount: StrictFloat = Field(ge=0)
    pnlRate: StrictFloat


class AccountSnapshot(Contract):
    userId: str = Field(min_length=1)
    accountMode: Literal["PAPER"]
    success: Literal[True]
    capturedAt: AwareDatetime
    source: Literal["kis"]
    maxPositionPct: StrictFloat = Field(gt=0, le=20)
    dailyBuyLimit: StrictFloat = Field(gt=0)
    orderableCash: StrictFloat = Field(ge=0)
    orderableCashSource: str = Field(min_length=1)
    reservedCash: StrictFloat = Field(ge=0)
    equity: StrictFloat = Field(gt=0)
    dailyPnlPct: StrictFloat | None
    dailyPnlBaselineSource: str | None
    entryEligible: StrictBool
    entryBlockReason: str | None
    holdings: list[Holding]
    monitorCapacity: StrictInt = Field(ge=0)
    monitorSymbolCount: StrictInt = Field(ge=0)
    monitorCapacityExceeded: StrictBool
    # Set by the client, not the backend: holdings the pipeline cannot analyse.
    unsupportedHoldings: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_account(self):
        if self.entryEligible and self.dailyPnlPct is None:
            raise ValueError("entry requires the actual daily equity baseline")
        if len({h.stockCode for h in self.holdings}) != len(self.holdings):
            raise ValueError("duplicate account holdings")
        return self
