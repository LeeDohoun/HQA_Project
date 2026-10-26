"""One daily-bar execution model for every strategy in the fitness comparison.

Every strategy decides on a decision day D before the open, from closes through D-1 (the LLM
pipeline also reads documents published through D-1), and states its trades as plans. All plans run
through the same rules:

- a BUY fills at D's open when the open is inside its entry band (the RiskManager saw that open
  as its quote) and above its stop; otherwise it expires unfilled, as PAPER's 15-minute entries do;
- quantity is floor(min(equity x size%, cash) / price), size capped at 20% unless a strategy states
  its own weights (as the backend sizes PAPER entries); no pyramiding into a held stock;
- on each bar after entry (the entry bar included), a stop, invalidation or take-profit triggers on
  the bar's range; a gap through a level fills at the open; the stop is checked before a target on
  the same bar unless the open is already past the target; a partial take-profit sells
  floor(quantity x fraction);
- a planned exit sells at the close of its date; a halted bar (no volume) never fills;
- costs are charged on every buy and sell (15 bp a side by default).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from backtesting.fitness.pit_data import final_bars
from src.runner.analysis_data import read_jsonl

PRICE_BASIS_BREAK_RETURN = 0.305  # beyond the KRX +/-30% daily limit: a split or consolidation
MAX_POSITION_PCT = 20.0


class PricePanel:
    """The last observed daily bar of every universe stock (what executions settle against)."""

    def __init__(self, bars: dict[tuple[str, str], tuple[float, ...]], names: dict[str, str],
                 themes: dict[str, str]):
        self.names, self.themes = names, themes
        frames = {}
        for code in sorted(names):
            rows = sorted((day, values) for (stock, day), values in bars.items() if stock == code)
            frame = pd.DataFrame([values for _, values in rows], index=[day for day, _ in rows],
                                 columns=["open", "high", "low", "close", "volume"], dtype=float)
            halted = (frame["volume"] == 0) & (frame["open"] == 0)
            for column in ("open", "high", "low"):
                frame.loc[halted, column] = frame.loc[halted, "close"]
            frames[code] = frame
        self.frames = frames
        self.calendar = sorted({day for frame in frames.values() for day in frame.index})
        self._pos = {day: index for index, day in enumerate(self.calendar)}

    @classmethod
    def from_pit(cls, pit_dir: Path) -> "PricePanel":
        names, themes = {}, {}
        for path in sorted((pit_dir / "raw" / "theme_targets").glob("*.jsonl")):
            for row in read_jsonl(path):
                names[str(row["stock_code"])] = str(row["stock_name"])
                themes[str(row["stock_code"])] = path.stem
        rows = [row for path in sorted((pit_dir / "market_data").glob("*/chart.jsonl")) for row in read_jsonl(path)]
        bars = {key: value for key, value in final_bars(rows).items() if key[0] in names}
        return cls(bars, names, themes)

    def index(self, day: str) -> int:
        return self._pos[day]

    def shift(self, day: str, sessions: int) -> str | None:
        position = self._pos[day] + sessions
        return self.calendar[position] if 0 <= position < len(self.calendar) else None

    def bar(self, code: str, day: str) -> pd.Series | None:
        frame = self.frames[code]
        return frame.loc[day] if day in frame.index else None

    def history(self, code: str, through: str) -> pd.DataFrame:
        frame = self.frames[code]
        return frame.loc[frame.index <= through]

    def basis_break(self, code: str, start: str, end: str) -> bool:
        closes = self.frames[code]["close"]
        window = closes.loc[(closes.index >= start) & (closes.index <= end)].to_numpy()
        return len(window) > 1 and bool(np.any(np.abs(window[1:] / window[:-1] - 1) > PRICE_BASIS_BREAK_RETURN))


# ---------------------------------------------------------------------------------------------
# Plans and their conditions
# ---------------------------------------------------------------------------------------------
OPEN_TIME, DECISION_CLOCK, CLOSE_TIME = "09:00:00", "09:15:00", "15:20:00"
OPEN_SLOT, CLOSE_SLOT = 0, 1  # a planned exit sells at the open or at the close of its day


@dataclass
class Group:
    """One condition group: every predicate must hold (PAPER's `all`)."""
    id: str
    predicates: list[tuple[str, str, Any]]
    fraction: float = 1.0  # reduce groups sell this fraction; exits and invalidations sell all

    @property
    def timed(self) -> bool:
        return any(name == "market_time" for name, _, _ in self.predicates)


@dataclass
class Plan:
    """A strategy's instruction for one stock, in PAPER's condition language."""
    code: str
    action: str  # BUY, SELL or HOLD
    size_pct: float | None = None  # % of equity, capped at MAX_POSITION_PCT (PAPER sizing)
    weight: float | None = None  # fraction of equity stated by a rule strategy (no cap)
    entry: list[Group] = field(default_factory=list)  # empty: buy at the open
    exits: list[Group] = field(default_factory=list)  # exit and invalidation groups: sell all
    reduces: list[Group] = field(default_factory=list)
    exit_at: tuple[str, int] | None = None  # (day, OPEN_SLOT or CLOSE_SLOT)
    hard_stop: float | None = None
    stop_pct: float | None = None  # rule strategies: stop this far below the fill
    take_profit_pct: float | None = None
    hold_sessions: int | None = None  # rule strategies: planned exit at the close of the Nth session
    meta: dict = field(default_factory=dict)


def _compare(left: Any, operator: str, right: Any) -> bool:
    return {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right,
            "==": left == right, "!=": left != right}[operator]


def _price_interval(group: Group, average: float | None) -> tuple[float, float] | None:
    """The prices at which the group's price predicates hold, or None if it has none."""
    low, high, any_price = 0.0, math.inf, False
    for name, operator, value in group.predicates:
        if name == "current_price":
            level = float(value)
        elif name == "pnl_rate" and average:
            level = average * (1 + float(value) / 100)
        else:
            continue
        any_price = True
        if operator in {">", ">="}:
            low = max(low, level)
        elif operator in {"<", "<="}:
            high = min(high, level)
        elif operator == "==":
            low, high = max(low, level), min(high, level)
        else:  # "!=" holds almost everywhere on a continuous path
            continue
    return (low, high) if any_price else None


def _holds_at(group: Group, price: float, clock: str, quantity: int, average: float | None) -> bool:
    for name, operator, value in group.predicates:
        if name == "current_price":
            left = price
        elif name == "pnl_rate":
            if not average:
                return False
            left = (price / average - 1) * 100
        elif name == "holding_quantity":
            left = quantity
        elif name == "market_time":
            left, value = clock, str(value)
        else:
            return False
        if not _compare(left, operator, value if name == "market_time" else float(value)):
            return False
    return True


def _nonprice_ok(group: Group, quantity: int) -> bool:
    return all(_compare(quantity, operator, float(value)) for name, operator, value in group.predicates
               if name == "holding_quantity")


def plan_from_trading_plan(plan: dict, *, holding_average: float | None = None) -> Plan:
    """A RiskManager TradingPlan (as published to the backend) in simulator form."""
    conditions = plan["condition_payload"]

    def groups(rows: list[dict], fraction: bool = False) -> list[Group]:
        return [Group(row["id"], [(p["field"], p["operator"], p["value"]) for p in row["all"]],
                      float(row.get("reduce_fraction", 1.0)) if fraction else 1.0) for row in rows]

    exit_at = None
    if plan.get("planned_exit_at"):
        from datetime import datetime, timedelta, timezone
        local = datetime.fromisoformat(plan["planned_exit_at"].replace("Z", "+00:00")).astimezone(
            timezone(timedelta(hours=9)))
        # Daily bars: a morning exit takes the open, an afternoon one the close, and one after the
        # session the next open.
        clock = local.strftime("%H:%M:%S")
        exit_at = (local.date().isoformat(), OPEN_SLOT if clock < "12:00:00" else CLOSE_SLOT)
        if clock > "15:30:00":
            exit_at = ((local.date() + timedelta(days=1)).isoformat(), OPEN_SLOT)
    hard = None
    for group in groups(conditions["exit_conditions"]) + groups(conditions["invalidation_conditions"]):
        if len(group.predicates) == 1 and group.predicates[0][:2] == ("current_price", "<="):
            hard = max(hard or 0.0, float(group.predicates[0][2]))
    return Plan(code=plan["stock_code"], action=plan["action"], size_pct=float(plan.get("position_size_pct") or 0),
                entry=groups(conditions["entry_conditions"]),
                exits=groups(conditions["exit_conditions"]) + groups(conditions["invalidation_conditions"]),
                reduces=groups(conditions["reduce_conditions"], fraction=True), exit_at=exit_at, hard_stop=hard,
                meta={"confidence": plan.get("confidence"), "risk_level": plan.get("risk_level"),
                      "entry_price": plan.get("entry_price"), "stop_loss_price": plan.get("stop_loss_price"),
                      "take_profit_price": plan.get("take_profit_price")})


# ---------------------------------------------------------------------------------------------
# Portfolio simulation
# ---------------------------------------------------------------------------------------------
@dataclass
class Position:
    code: str
    quantity: int
    average: float
    entry_day: str
    plan: Plan
    done_reduces: set = field(default_factory=set)
    cost: float = 0.0  # buy notional plus costs, for the round-trip return
    proceeds: float = 0.0  # sell notional less costs so far


@dataclass
class DecisionView:
    """What a strategy may see on a decision day before the open: holdings and cash, the
    previous close (for features) and, for the account snapshot, the open as the 09:15 quote."""
    day: str
    previous_day: str | None
    cash: float
    equity_at_open: float
    previous_close_equity: float
    positions: dict[str, Position]
    opens: dict[str, float]


Decide = Callable[[DecisionView], list[Plan]]


class Simulator:
    def __init__(self, panel: PricePanel, *, cost_bps: float = 15.0, initial_cash: float = 10_000_000.0):
        self.panel, self.cost = panel, cost_bps / 10_000
        self.initial_cash = initial_cash

    def run(self, decide: Decide, *, start: str, end: str, decision_days: Iterable[str],
            on_day: Callable[[str, dict], None] | None = None) -> dict:
        panel = self.panel
        days = [day for day in panel.calendar if start <= day <= end]
        decisions = set(decision_days)
        cash = self.initial_cash
        positions: dict[str, Position] = {}
        trades: list[dict] = []
        closed: list[dict] = []
        curve: list[dict] = []
        events: list[dict] = []
        previous_equity = cash
        traded_value = 0.0

        def sell(position: Position, quantity: int, price: float, day: str, reason: str) -> None:
            nonlocal cash, traded_value
            quantity = min(quantity, position.quantity)
            if quantity <= 0:
                return
            value = quantity * price
            cash += value * (1 - self.cost)
            traded_value += value
            position.quantity -= quantity
            position.proceeds += value * (1 - self.cost)
            trades.append({"day": day, "code": position.code, "side": "SELL", "quantity": quantity,
                           "price": price, "reason": reason, "strategy_meta": position.plan.meta})
            if position.quantity == 0:
                positions.pop(position.code, None)
                closed.append({"code": position.code, "entry_day": position.entry_day, "exit_day": day,
                               "reason": reason, "return": position.proceeds / position.cost - 1,
                               "pnl": position.proceeds - position.cost, "meta": position.plan.meta})

        def tradable(code: str, day: str) -> pd.Series | None:
            bar = panel.bar(code, day)
            return bar if bar is not None and bar["volume"] > 0 else None

        for index, day in enumerate(days):
            previous_day = panel.shift(day, -1)
            opens = {code: float(bar["open"]) for code in panel.names
                     if (bar := tradable(code, day)) is not None}
            # 1. Positions that open the day past a level, or whose planned exit has come.
            for position in list(positions.values()):
                bar = tradable(position.code, day)
                if bar is None:
                    continue
                self._open_phase(position, float(bar["open"]), day, sell)
            # 2. The decision before the open (PAPER's 09:15 cycle, with the open as its quote).
            if day in decisions:
                equity_open = cash + sum(p.quantity * opens.get(p.code, self._last_close(p.code, day))
                                         for p in positions.values())
                view = DecisionView(day=day, previous_day=previous_day, cash=cash, equity_at_open=equity_open,
                                    previous_close_equity=previous_equity, positions=dict(positions), opens=opens)
                for plan in decide(view):
                    position = positions.get(plan.code)
                    if plan.action == "SELL":
                        if position is not None and plan.code in opens:
                            sell(position, position.quantity, opens[plan.code], day, "planned_sell")
                        continue
                    if plan.action == "HOLD":
                        if position is not None:
                            self._update_plan(position, plan, events, day)
                        continue
                    if position is not None:
                        events.append({"day": day, "code": plan.code, "event": "buy_refused_holding_exists"})
                        continue
                    price = opens.get(plan.code)
                    if price is None:
                        events.append({"day": day, "code": plan.code, "event": "buy_no_open_bar"})
                        continue
                    if plan.entry and not any(_holds_at(g, price, DECISION_CLOCK, 0, None) for g in plan.entry):
                        events.append({"day": day, "code": plan.code, "event": "buy_open_outside_entry",
                                       "open": price})
                        continue
                    fill_plan = self._absolute(plan, price, day)
                    if fill_plan.hard_stop is not None and price <= fill_plan.hard_stop:
                        events.append({"day": day, "code": plan.code, "event": "buy_at_or_below_stop"})
                        continue
                    if plan.weight is not None:
                        allocation = equity_open * plan.weight
                    else:
                        allocation = math.floor(equity_open * min(plan.size_pct or 0.0, MAX_POSITION_PCT) / 100)
                    spend = max(0.0, min(allocation, cash / (1 + self.cost)))
                    quantity = int(spend // price)
                    if quantity <= 0:
                        events.append({"day": day, "code": plan.code, "event": "buy_no_orderable_quantity",
                                       "allocation": allocation, "price": price})
                        continue
                    value = quantity * price
                    cash -= value * (1 + self.cost)
                    traded_value += value
                    positions[plan.code] = Position(plan.code, quantity, price, day, fill_plan,
                                                    cost=value * (1 + self.cost))
                    trades.append({"day": day, "code": plan.code, "side": "BUY", "quantity": quantity,
                                   "price": price, "reason": "entry", "strategy_meta": plan.meta})
            # 3. The rest of the session, then the close.
            for position in list(positions.values()):
                bar = tradable(position.code, day)
                if bar is None:
                    continue
                self._range_phase(position, bar, day, sell)
                if position.code in positions:
                    self._close_phase(position, float(bar["close"]), day, sell)
            invested = sum(p.quantity * self._last_close(p.code, day) for p in positions.values())
            equity = cash + invested
            curve.append({"day": day, "equity": equity, "cash": cash, "invested": invested,
                          "positions": len(positions)})
            if on_day is not None:
                on_day(day, curve[-1])
            previous_equity = equity
        # Positions still open at the end are valued at the last close (not sold, no exit cost).
        return {"curve": curve, "trades": trades, "closed": closed, "events": events,
                "open_positions": [{"code": p.code, "entry_day": p.entry_day, "quantity": p.quantity,
                                    "average": p.average} for p in positions.values()],
                "traded_value": traded_value, "initial_cash": self.initial_cash}

    def _last_close(self, code: str, day: str) -> float:
        history = self.panel.history(code, day)
        return float(history["close"].iloc[-1])

    def _absolute(self, plan: Plan, fill: float, day: str) -> Plan:
        """Rule strategies state stops and holding periods relative to their fill."""
        if plan.stop_pct is None and plan.take_profit_pct is None and plan.hold_sessions is None:
            return plan
        exits = list(plan.exits)
        hard = plan.hard_stop
        if plan.stop_pct is not None:
            hard = fill * (1 - plan.stop_pct)
            exits.append(Group("stop", [("current_price", "<=", hard)]))
        if plan.take_profit_pct is not None:
            exits.append(Group("take-profit", [("current_price", ">=", fill * (1 + plan.take_profit_pct))]))
        exit_at = plan.exit_at
        if plan.hold_sessions is not None:
            last = self.panel.shift(day, plan.hold_sessions - 1) or self.panel.calendar[-1]
            exit_at = (last, CLOSE_SLOT)
        return Plan(code=plan.code, action=plan.action, size_pct=plan.size_pct, weight=plan.weight, entry=plan.entry,
                    exits=exits, reduces=list(plan.reduces), exit_at=exit_at, hard_stop=hard, meta=plan.meta)

    @staticmethod
    def _update_plan(position: Position, plan: Plan, events: list, day: str) -> None:
        """A held position's new plan, as the backend applies it: a stop is never lowered (the old
        plan keeps protecting) and the planned exit never moves later."""
        old = position.plan
        if old.hard_stop is not None and (plan.hard_stop is None or plan.hard_stop < old.hard_stop):
            events.append({"day": day, "code": position.code, "event": "plan_update_kept_old_stop"})
            return
        exit_at = plan.exit_at
        if old.exit_at and (exit_at is None or exit_at > old.exit_at):
            exit_at = old.exit_at
        position.plan = Plan(code=plan.code, action="HOLD", size_pct=old.size_pct, weight=old.weight,
                             exits=plan.exits, reduces=plan.reduces, exit_at=exit_at, hard_stop=plan.hard_stop,
                             meta={**old.meta, "updated": day})
        position.done_reduces = {gid for gid in position.done_reduces if any(g.id == gid for g in plan.reduces)}

    def _open_phase(self, position: Position, price: float, day: str, sell) -> None:
        plan = position.plan
        if plan.exit_at and plan.exit_at <= (day, OPEN_SLOT):
            sell(position, position.quantity, price, day, "planned_exit")
            return
        for group in plan.exits:
            if _holds_at(group, price, OPEN_TIME, position.quantity, position.average):
                sell(position, position.quantity, price, day, f"exit:{group.id}:open")
                return
        for group in plan.reduces:
            if group.id not in position.done_reduces and _holds_at(group, price, OPEN_TIME, position.quantity,
                                                                    position.average):
                position.done_reduces.add(group.id)
                sell(position, math.floor(position.quantity * group.fraction), price, day, f"reduce:{group.id}:open")

    def _range_phase(self, position: Position, bar: pd.Series, day: str, sell) -> None:
        """Untimed price levels the session crossed. With only the day's range known, levels below
        the open are taken to come first (stops before targets), the nearest first."""
        open_, low, high = float(bar["open"]), float(bar["low"]), float(bar["high"])
        hits = []
        for kind, groups in (("exit", position.plan.exits), ("reduce", position.plan.reduces)):
            for group in groups:
                if group.timed or (kind == "reduce" and group.id in position.done_reduces):
                    continue
                if not _nonprice_ok(group, position.quantity):
                    continue
                interval = _price_interval(group, position.average)
                if interval is None or interval[0] > high or interval[1] < low:
                    continue
                fill = interval[1] if interval[1] < open_ else interval[0]
                fill = min(max(fill, low), high)
                hits.append((0 if fill <= open_ else 1, -fill if fill <= open_ else fill, kind, group, fill))
        for _, _, kind, group, fill in sorted(hits, key=lambda hit: (hit[0], hit[1])):
            if position.code is None or position.quantity == 0:
                return
            if kind == "exit":
                sell(position, position.quantity, fill, day, f"exit:{group.id}")
                return
            position.done_reduces.add(group.id)
            sell(position, math.floor(position.quantity * group.fraction), fill, day, f"reduce:{group.id}")

    def _close_phase(self, position: Position, price: float, day: str, sell) -> None:
        plan = position.plan
        for group in plan.exits:
            if group.timed and _holds_at(group, price, CLOSE_TIME, position.quantity, position.average):
                sell(position, position.quantity, price, day, f"exit:{group.id}:close")
                return
        for group in plan.reduces:
            if (group.timed and group.id not in position.done_reduces
                    and _holds_at(group, price, CLOSE_TIME, position.quantity, position.average)):
                position.done_reduces.add(group.id)
                sell(position, math.floor(position.quantity * group.fraction), price, day, f"reduce:{group.id}:close")
        if position.quantity and plan.exit_at and plan.exit_at <= (day, CLOSE_SLOT):
            sell(position, position.quantity, price, day, "planned_exit")
