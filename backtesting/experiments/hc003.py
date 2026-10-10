"""HC003 persistent operating-leverage portfolios; local dry-run by default."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import resource
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import capacity, cost_model, experiment_registry, holdout, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001, hc002, hf001
from src.ingestion import dart_quarterly, krx_market
from src.research import market_regimes


EXPERIMENT_ID = "HC003_operating_leverage_turnover"
VARIANTS = ("t_hold", "t_hyst", "t_filter")
N_CONTROLS = 200
DESIGN_END = pd.Timestamp("2025-12-30")
HOLDOUT_END = pd.Timestamp("2026-06-30")
SCENARIOS = {"v2_1.0": ("v2", 1.0), "v2_1.5": ("v2", 1.5),
             "v2_2.0": ("v2", 2.0), "v1_1.0": ("v1", 1.0)}
ASSUMPTIONS = [
    "Fractional adjusted-index units are used with initial cash 1; no integer-lot rounding, interest, dividends outside ret_1d, or borrowing is added.",
    "U and P call HC002 universe_on_decision unchanged through a read-only symlink view that excludes archive years after the last decision year. Nested common guards inherit HC003's explicitly scoped ID; financial receipts within each archive remain strictly before the decision session.",
    "The first observed positive close seeds A=1. Missing raw close or limit reference leaves A unchanged and prevents trading; missing ret_1d uses close/base-1 and is counted per stock/session. The base fallback is the immediately preceding exchange-session raw close, never a forward-filled older close.",
    "At an open, a computable current adjusted open is the latest observed quote, including frozen zero-volume/limit-down names. Otherwise use the preceding adjusted close; current-day closing quotes are never substituted for a missing open. At the terminal close use the last observed adjusted close.",
    "Unknown market or incomplete verified prior-20-session trading_value prevents trading (and freezes existing holdings), with explicit counts. No close*volume or stale ADV substitute is used. New lower-limit names cannot be bought. Missing whole-market session coverage fails before inference; it is never mistaken for a vanished stock.",
    "If the HF001 126-session listing window is incomplete, t_filter blocks every buy, continues target-directed sells/valuations, and excludes that month from inference and control means. Missing 60-session volatility never blocks a buy, as HF001 specifies.",
    "Retention for control matching counts all prior positive-quantity holdings still held after trading, including frozen names outside H. If this exceeds the current H size, keep the requested count where candidates permit and add no fresh names. Retention ratio divides by prior positive-quantity holdings (first month: null).",
    "Each variant starts default_rng(0); draw order is path then month, candidates code-sorted, including choice of size zero. Controls use the actual n even when candidates or executable slots are missing; every unfilled slot stays cash. Filter buy blocks apply to sampled names too.",
    "NW uses the ordered valid monthly observations, Bartlett lag 3 with autocovariance divisor n and no finite-sample correction. Missing months are not invented. Monthly counts, not the YAML event alternative, determine sufficiency.",
    "2025 sign is a reported stability diagnostic, not an extra pass condition. All three variants receive Holm and the registry t threshold; t_hold is the fixed headline and no variant is selected after seeing results.",
    "Zero-loss sensitivity uses last positive-close, positive-volume trade through the fixed evaluation end, including liquidation trading; zero starts at the first subsequent scheduled rebalance. Units are never retroactively sold. At the terminal close there is no new rebalance or liquidation.",
    "Liquidity diagnostics rerun the same accounting within each decision-ADV bucket, using filters ranked in full U. Capacity reports p25 ADV estimates and the minimum AUM allowing each actual one-way trade to stay within ADV 1%; these are diagnostics.",
    "HC002 comparison recomputes its unchanged phase2_ew full-round-trip v1 20-session cohorts on the same decision U; its different holding interval is explicit and is not a replacement benchmark.",
    "Holdout only judges variants with a stored design/validation pass, on its six months and the section-6 effect criteria. It does not reuse the 36-month/NW/Holm design pass gate. Cost scenarios continue their own stored November post-trade state; sensitivity begins from those same states and the stored December close value, without changing past design results.",
    "Holdout control diagnostics continue stored November control states and the saved RNG state. There is no resampling of the design history. No protected data is read until non-holdout replay verifies the saved computation and one durable HC003 session is opened.",
    "Latest saved KRX revisions and current company classifications do not reconstruct historical vintages; all read archives and computation sources are SHA-256 identified. No long-term profitability confirmation is implied.",
]
INTERPRETATIONS = {
    "note": "Exploratory reanalysis after HC002 turnover diagnostics; 2025 is not independent validation. Six holdout months only check reproduction of fixed operating rules.",
    "primary": "e=next pre-trade value/current pre-trade value-1 minus U equal-weight gross adjusted-open returns; the terminal month ends at the registered close without liquidation.",
    "accounting": "Frozen Z is fixed before T and W/n. Sells precede buys, one-way v2 costs apply to actual notionals, all buys scale together including costs. The 0.1% pre-trade-value band is tested once before scaling, full exits exempt.",
    "statistics": "Bartlett NW lag 3, no small-sample correction; upper normal tail; Holm family always three, with p=1 for fewer than 36 valid months or undefined SE. Strict t>2, or t>3 if existing HC003 rows plus these three exceed 20.",
    "controls": "200 retention-count-matched paths per variant, actual allocation denominator, unfilled cash slots. Mean e ranking uses >= without a plus-one correction. Dollar turnover is not matched; strategy/control buy/sell turnover and costs are reported side by side.",
    "sensitivity": "Rerun the entire accounting for costs v2 x1.5/x2 and v1 x1. A separate vanished-stock -100% rerun repeats controls, costs, NW, Holm and verdicts; any changed verdict becomes delisting_sensitive, never pass.",
    "secondary": "Monthly counts/retention/frozen/cash, year/regime/bucket splits, capacity, HC002 monthly differences, and price-eligible but uncomputable-signal ratios are report-only. Tax is never multiplied.",
    "holdout": "Ledger-only LH002 contamination check first; saved November units/cash and December close value are verified on non-holdout data. January includes the opening gap and trading cost. A failure consumes the session and records holdout_aborted; no reopen.",
}


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    fields = registration.fields
    if fields["variants_planned"] != 3 or not fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("HC003 requires three fixed variants, holdout and no LLM")
    for experiment in (hc002.EXPERIMENT_ID, hf001.EXPERIMENT_ID):
        experiment_registry.verify_preregistration(experiment, repo_root)
    return registration


def newey_west(values):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("NW observations must be finite")
    n = len(values)
    mean = float(values.mean()) if n else None
    result = {"count": n, "mean": mean, "std": float(values.std(ddof=1)) if n > 1 else None,
              "standard_error": None, "long_run_variance": None, "t_stat": None,
              "p_one_sided": 1.0, "lag": 3, "small_sample_correction": False}
    if n > 1:
        centered = values - mean
        variance = float(np.dot(centered, centered) / n)
        for lag in range(1, min(3, n - 1) + 1):
            variance += 2 * (1 - lag / 4) * float(np.dot(centered[lag:], centered[:-lag]) / n)
        result["long_run_variance"] = variance
        if variance > 0 and np.ptp(values) > 0:
            se = math.sqrt(variance / n)
            statistic = mean / se
            result.update(standard_error=se, t_stat=statistic,
                          p_one_sided=0.5 * math.erfc(statistic / math.sqrt(2)))
    return result


def holm(p_values):
    if set(p_values) != set(VARIANTS):
        raise ValueError("HC003 Holm family must contain exactly three variants")
    if any(not np.isfinite(p) or not 0 <= p <= 1 for p in p_values.values()):
        raise ValueError("Holm requires probabilities in [0,1]")
    adjusted, previous = {}, 0.0
    for rank, name in enumerate(sorted(VARIANTS, key=lambda key: (p_values[key], VARIANTS.index(key)))):
        previous = max(previous, min(1.0, (3 - rank) * p_values[name]))
        adjusted[name] = previous
    return adjusted


@dataclass
class PortfolioState:
    cash: float = 1.0
    quantities: dict[str, float] = field(default_factory=dict)

    def to_dict(self):
        return {"cash": float(self.cash), "quantities": dict(sorted(self.quantities.items()))}

    @classmethod
    def from_dict(cls, value):
        cash = value["cash"]
        quantities = value["quantities"]
        if not np.isfinite(cash) or cash < 0 or any(not np.isfinite(q) or q <= 0 for q in quantities.values()):
            raise ValueError("invalid saved portfolio state")
        return cls(float(cash), {code: float(q) for code, q in quantities.items()})


@dataclass(frozen=True)
class Quote:
    mark: float
    raw_open: float
    market: str
    adv: float
    tradable: bool
    limit_up: bool = False
    limit_down: bool = False


class PriceBook:
    """Adjusted-index marks; all execution/sensitivity snapshots are cached once."""

    def __init__(self, prices, sessions, rebalances, end, *, repo_root=PROJECT_ROOT):
        common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
        if prices.index.has_duplicates:
            raise ValueError("price keys must be unique")
        required = {"open", "close", "volume", "market", "trading_value", "calendar_status", "ret_1d"}
        if not required.issubset(prices.columns):
            raise ValueError(f"missing accounting columns: {sorted(required - set(prices.columns))}")
        self.days = signal_eval._dates(sessions).sort_values().unique()
        self.days = self.days[self.days <= pd.Timestamp(end)]
        if not common._sessions(prices).difference(self.days).empty:
            raise ValueError("accounting prices must be exchange sessions within the evaluation span")
        self.end = pd.Timestamp(end)
        self.rebalances = signal_eval._dates(rebalances).sort_values().unique()
        self.openings = {day: {} for day in self.rebalances}
        self.closings = {}
        self.zero_from = {}
        self.counts = {"unadjusted_fallback": 0, "unchanged_missing_price_or_base": 0,
                       "missing_base": 0, "cost_input_untradable": 0}
        thresholds = np.array([hf001.price_limit(day) - 0.005 for day in self.days])
        for code, group in prices.groupby(level="stock_code", observed=True, sort=True):
            frame = group.droplevel("stock_code").reindex(self.days)
            closing = frame.close.to_numpy(dtype=float)
            opening = frame.open.to_numpy(dtype=float)
            volume = frame.volume.to_numpy(dtype=float)
            previous = np.r_[np.nan, closing[:-1]]
            bases = frame.base_price.to_numpy(dtype=float) if "base_price" in frame else np.full(len(frame), np.nan)
            bases = np.where(np.isnan(bases), previous, bases)
            valid_close = np.isfinite(closing) & (closing > 0)
            valid_base = np.isfinite(bases) & (bases > 0)
            returns = frame.ret_1d.to_numpy(dtype=float)
            if np.isinf(returns).any() or (returns[np.isfinite(returns)] < -1).any():
                raise ValueError(f"invalid adjusted daily return: {code}")
            observed = frame.index.isin(group.index.get_level_values("trade_date"))
            valid = valid_close & valid_base
            fallback = valid & np.isnan(returns)
            factors = np.ones(len(frame))
            factors[valid] = 1 + returns[valid]
            factors[fallback] = closing[fallback] / bases[fallback]
            first = np.flatnonzero(valid_close)
            if not len(first):
                continue
            first = first[0]
            factors[:first + 1] = 1.0
            adjusted_close = np.cumprod(factors)
            adjusted_close[:first] = np.nan
            # No current close or base means no change and no executable open.
            adjusted_open = np.r_[np.nan, adjusted_close[:-1]]
            current_open = valid & np.isfinite(opening) & (opening > 0)
            adjusted_open[current_open] = adjusted_close[current_open] * opening[current_open] / closing[current_open]
            value = frame.trading_value.where(frame.calendar_status.eq("verified"))
            value = value.where(np.isfinite(value) & value.ge(0))
            adv = value.shift(1).rolling(20, min_periods=20).mean().to_numpy()
            markets = frame.market.to_numpy()
            cost_known = np.isfinite(adv) & np.isin(markets, ("KOSPI", "KOSDAQ"))
            tradable = current_open & np.isfinite(volume) & (volume > 0) & cost_known & (adjusted_open > 0)
            up = valid_base & (opening >= bases * (1 + thresholds))
            down = valid_base & (opening <= bases * (1 - thresholds))
            self.counts["unadjusted_fallback"] += int(fallback[first + 1:].sum())
            self.counts["missing_base"] += int((observed & ~valid_base).sum())
            self.counts["unchanged_missing_price_or_base"] += int((observed & ~valid).sum())
            self.counts["cost_input_untradable"] += int((current_open & (volume > 0) & ~cost_known).sum())
            trades = valid_close & np.isfinite(volume) & (volume > 0)
            last = np.flatnonzero(trades)
            if len(last):
                after = self.rebalances[self.rebalances > self.days[last[-1]]]
                if len(after):
                    self.zero_from[code] = after[0]
            for day in self.rebalances:
                pos = self.days.get_loc(day)
                mark = adjusted_open[pos]
                if np.isfinite(mark):
                    self.openings[day][code] = Quote(float(mark), float(opening[pos]), markets[pos],
                        float(adv[pos]), bool(tradable[pos]), bool(up[pos]), bool(down[pos]))
            if np.isfinite(adjusted_close[-1]):
                self.closings[code] = float(adjusted_close[-1])

    def quotes(self, day, *, loss=False):
        day = pd.Timestamp(day)
        quotes = self.openings[day]
        if not loss:
            return quotes
        return {code: Quote(0.0, q.raw_open, q.market, q.adv, False, q.limit_up, q.limit_down)
                if code in self.zero_from and self.zero_from[code] <= day else q for code, q in quotes.items()}

    def marks(self, day, kind="open", *, loss=False):
        day = pd.Timestamp(day)
        values = self.closings if kind == "close" else {code: q.mark for code, q in self.openings[day].items()}
        if kind == "close" and day != self.end:
            raise ValueError("close valuation must use the fixed evaluation end")
        return {code: 0.0 if loss and code in self.zero_from and self.zero_from[code] <= day else value
                for code, value in values.items()}


def portfolio_value(state, marks):
    if set(state.quantities) - set(marks):
        raise ValueError("held stock has no prior adjusted valuation")
    return float(state.cash + sum(state.quantities[code] * marks[code] for code in sorted(state.quantities)))


def rebalance(state, quotes, targets, day, *, blocked_buys=(), allocation_n=None,
              model_version="v2", multiplier=1.0):
    """One open: freeze, fix targets, sell, and proportionally cash-fund buys."""
    state = PortfolioState.from_dict(state.to_dict())
    before = set(state.quantities)
    marks = {code: q.mark for code, q in quotes.items()}
    value = portfolio_value(state, marks)
    frozen = {code for code in before if not quotes[code].tradable or quotes[code].limit_down}
    frozen_value = sum(state.quantities[code] * marks[code] for code in sorted(frozen))
    allocatable = value - frozen_value
    targets = set(targets)
    executable = {code for code in targets - frozen if code in quotes and quotes[code].tradable
                  and not quotes[code].limit_down and not (quotes[code].limit_up and code not in before)}
    n = len(executable) if allocation_n is None else allocation_n
    if type(n) is not int or n < 0:
        raise ValueError("allocation denominator must be a nonnegative integer")
    target = allocatable / n if n else 0.0
    buy_orders, sells, trades = {}, {}, []
    for code in sorted((before | executable) - frozen):
        current = state.quantities.get(code, 0.0) * marks[code]
        full_exit = code in before and code not in targets
        delta = (0.0 if full_exit or code not in executable else target) - current
        if not full_exit and abs(delta) < value * 0.001:
            continue
        if delta < 0:
            sells[code] = (-delta, full_exit)
        elif delta > 0 and code not in blocked_buys and not quotes[code].limit_up:
            buy_orders[code] = delta
    def rate(code, side):
        q = quotes[code]
        return cost_model.one_way_cost(q.raw_open, q.market, pd.Timestamp(day).date(), q.adv, side,
                                       multiplier=multiplier, model_version=model_version)
    for code, (amount, full_exit) in sells.items():
        cost = amount * rate(code, "sell")
        state.cash += amount - cost
        if full_exit or amount >= state.quantities[code] * marks[code]:
            del state.quantities[code]
        else:
            state.quantities[code] -= amount / marks[code]
        trades.append({"stock_code": code, "side": "sell", "amount": amount, "cost": cost,
                       "adv": quotes[code].adv, "full_exit": full_exit})
    buy_rates = {code: rate(code, "buy") for code in buy_orders}
    needed = sum(amount * (1 + buy_rates[code]) for code, amount in buy_orders.items())
    scale = min(1.0, state.cash / needed) if needed else 1.0
    for code, desired in buy_orders.items():
        amount = desired * scale
        if amount == 0:
            continue
        cost = amount * buy_rates[code]
        state.cash -= amount + cost
        state.quantities[code] = state.quantities.get(code, 0.0) + amount / marks[code]
        trades.append({"stock_code": code, "side": "buy", "amount": amount, "cost": cost,
                       "adv": quotes[code].adv, "full_exit": False})
    if state.cash < -1e-12 * max(value, 1.0):
        raise ValueError("portfolio borrowing detected")
    state.cash = max(0.0, state.cash)  # Floating-point dust only; checked above.
    totals = {side: sum(row["amount"] for row in trades if row["side"] == side) for side in ("buy", "sell")}
    costs = {side: sum(row["cost"] for row in trades if row["side"] == side) for side in ("buy", "sell")}
    retained = before & set(state.quantities)
    return state, {"pre_value": value, "post_value": portfolio_value(state, marks),
        "H": sorted(targets), "Z": sorted(frozen), "T": sorted(executable), "n": n,
        "W": allocatable, "target_amount": target, "buy_scale": scale, "trades": trades,
        "buy_amount": totals["buy"], "sell_amount": totals["sell"],
        "buy_cost": costs["buy"], "sell_cost": costs["sell"], "cost": sum(costs.values()),
        "buy_turnover": totals["buy"] / value if value > 0 else None,
        "sell_turnover": totals["sell"] / value if value > 0 else None,
        "cost_fraction": sum(costs.values()) / value if value > 0 else None,
        "holdings_count": len(state.quantities), "retained_count": len(retained),
        "retention_ratio": len(retained) / len(before) if before else None,
        "frozen_weight": frozen_value / value if value > 0 else None,
        "cash_weight": state.cash / value if value > 0 else None}


def target_set(variant, universe, held):
    phase2 = set(universe.index[universe.phase.eq(2)])
    if variant == "t_hyst":
        phase2 |= set(universe.index[universe.phase.isin((1, 2, 3))]) & set(held)
    elif variant not in VARIANTS:
        raise ValueError("unknown HC003 variant")
    return phase2


def random_target(universe, previous, size, retained, rng):
    previous = set(previous)
    candidates = sorted(set(universe) & previous)
    keep_count = min(retained, len(candidates))
    keep = set(rng.choice(candidates, size=keep_count, replace=False).tolist())
    candidates = sorted(set(universe) - previous)
    fresh = rng.choice(candidates, size=min(max(0, size - len(keep)), len(candidates)), replace=False)
    return keep | set(fresh.tolist())


def session_calendar():
    return hc001.calendars.get_calendar("XKRX", start="2015-01-01", end="2026-07-31").sessions


def rebalance_calendar(sessions, *, holdout_mode=False):
    days = signal_eval._dates(sessions).sort_values().unique()
    months = pd.period_range("2025-12", "2026-05", freq="M") if holdout_mode else hc002.MONTHS
    end = HOLDOUT_END if holdout_mode else DESIGN_END
    planned, excluded = [], []
    for month in months:
        monthly = days[days.to_period("M") == month]
        row = {"month": str(month)}
        if not len(monthly):
            excluded.append({**row, "reason": "missing_decision_session"})
            continue
        decision = monthly[-1]
        pos = days.get_loc(decision) + 1
        if pos >= len(days) or days[pos] > end:
            excluded.append({**row, "reason": "missing_entry_session"})
            continue
        next_month = days[days.to_period("M") == month + 1]
        row.update(decision_date=decision.date().isoformat(), trade_date=days[pos].date().isoformat())
        if month == months[-1]:
            if end not in days:
                excluded.append({**row, "reason": "missing_terminal_close_session"})
                continue
            row.update(end_date=end.date().isoformat(), end_kind="close")
        elif len(next_month) and days.get_loc(next_month[-1]) + 1 < len(days):
            row.update(end_date=days[days.get_loc(next_month[-1]) + 1].date().isoformat(), end_kind="open")
        else:
            excluded.append({**row, "reason": "missing_next_rebalance_session"})
            continue
        planned.append(row)
    return planned, excluded


def price_files(data_dir, start, end, *, repo_root=PROJECT_ROOT, experiment_id=None):
    """Guard before inspecting the requested files; never inspect later contents."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    holdout.guard_period(start.date(), end.date(), experiment_id=experiment_id, repo_root=repo_root)
    directory = Path(data_dir) / "market/krx_daily"
    if not directory.is_dir():
        raise ValueError(f"price directory does not exist: {directory}")
    return sorted((pd.Timestamp(path.stem), path) for path in directory.glob("*/*.jsonl")
                  if len(path.stem) == 8 and path.stem.isdigit()
                  and start <= pd.Timestamp(path.stem) <= end and path.stat().st_size)


def load_prices(data_dir, start, end, *, repo_root=PROJECT_ROOT, experiment_id=None):
    """Bound memory per day, retaining KRX's latest-episode/numeric semantics."""
    files = price_files(data_dir, start, end, repo_root=repo_root, experiment_id=experiment_id)
    frames = []
    columns = {"trade_date", "stock_code", "stock_name", "market", "calendar_status", "base_price",
               "open", "high", "low", "close", "volume", "trading_value", "change_rate_pct"}
    for day, _ in files:
        holdout.guard_period(day.date(), day.date(), experiment_id=experiment_id, repo_root=repo_root)
        frame = krx_market.load_prices(None, day.strftime("%Y%m%d"), day.strftime("%Y%m%d"), data_dir, columns=columns)
        if not (frame.index.get_level_values("trade_date") == day).all():
            raise ValueError("price row date disagrees with archive date")
        frames.append(frame)
    if not frames:
        raise ValueError("no price observations in the guarded HC003 span")
    prices = pd.concat(frames).sort_index()
    common.guard_prices(prices, experiment_id=experiment_id, repo_root=repo_root)
    return prices, files


def listing_files(data_dir, end, *, repo_root=PROJECT_ROOT, experiment_id=None):
    end = pd.Timestamp(end)
    holdout.guard_period(hf001.LISTING_START.date(), end.date(), experiment_id=experiment_id, repo_root=repo_root)
    directory = Path(data_dir) / "disclosures/dart_full/list"
    return {pd.Timestamp(path.stem): path for path in sorted(directory.glob("*/*.jsonl"))
            if len(path.stem) == 8 and path.stem.isdigit() and hf001.LISTING_START <= pd.Timestamp(path.stem) <= end}


def build_selections(prices, planned, *, data_dir, repo_root, sessions, files, events):
    """Use HC002's exact decision path and import HF001's two filter definitions."""
    common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    selections, counts = [], []
    available = common._sessions(prices)
    through = max(row["decision_date"] for row in planned)
    with common.guarded_reads(EXPERIMENT_ID), common.quarterly_view(
            data_dir, through, experiment_id=EXPERIMENT_ID, repo_root=repo_root) as view:
        for row in planned:
            day = pd.Timestamp(row["decision_date"])
            holdout.guard_period(day.date(), day.date(), experiment_id=EXPERIMENT_ID, repo_root=repo_root)
            pos = sessions.get_loc(day)
            required = sessions[max(0, pos - 19):pos + 1]
            if pos < 19 or len(required.difference(available)):
                raise ValueError(f"incomplete HC002 price history on {day.date()}")
            history = prices.loc[prices.index.get_level_values("trade_date").isin(required)]
            universe, report = hc002.universe_on_decision(history, day, data_dir=view, repo_root=repo_root)
            universe = universe.sort_index()
            if not universe.empty:
                vol = hf001.volatility_filter(prices, universe, day, sessions)
                known = events.loc[events.known_position.between(pos - 125, pos)]
                blocked = set(vol.index[vol.f_vol]) | (set(known.stock_code) & set(universe.index))
                uncomputable = int(vol.volatility_60d.isna().sum())
            else:
                blocked, uncomputable = set(), 0
            coverage = hf001.listing_coverage(day, sessions, files)
            selections.append({**row, "universe": universe, "blocked_buys": blocked,
                               "filter_covered": coverage["covered"], "listing": coverage,
                               "vol_uncomputable_U": uncomputable})
            counts.append({**report, "decision_date": row["decision_date"]})
    return selections, counts


def benchmark_return(book, selection, *, loss=False):
    codes = list(selection["universe"].index)
    if not codes:
        return None
    opening = book.marks(selection["trade_date"], loss=loss)
    ending = book.marks(selection["end_date"], selection["end_kind"], loss=loss)
    values = []
    for code in codes:
        if code not in opening:
            # A selected price-eligible name must already have a close observation.
            raise ValueError(f"benchmark stock lacks an initial valuation: {code}")
        if code not in ending:
            raise ValueError(f"benchmark stock lacks a terminal valuation: {code}")
        values.append(ending[code] / opening[code] - 1 if opening[code] > 0 else 0.0)
    return float(np.mean(values))


def simulate(book, selections, variant, *, model_version="v2", multiplier=1.0, loss=False,
             initial_state=None, initial_value=None, control_reference=None, rng=None):
    state = PortfolioState() if initial_state is None else PortfolioState.from_dict(initial_state)
    rows = []
    for number, selection in enumerate(selections):
        u = selection["universe"]
        if control_reference is None:
            targets = target_set(variant, u, state.quantities)
            denominator = None
        else:
            reference = control_reference[number]
            targets = random_target(u.index, state.quantities, len(reference["H"]),
                                    reference["retained_count"], rng)
            denominator = reference["n"]
        blocked = selection["blocked_buys"] if variant == "t_filter" else set()
        covered = variant != "t_filter" or selection["filter_covered"]
        if not covered:
            blocked = set(u.index)
        state, accounting = rebalance(state, book.quotes(selection["trade_date"], loss=loss), targets,
            selection["trade_date"], blocked_buys=blocked, allocation_n=denominator,
            model_version=model_version, multiplier=multiplier)
        beginning = initial_value if number == 0 and initial_value is not None else accounting["pre_value"]
        ending = portfolio_value(state, book.marks(selection["end_date"], selection["end_kind"], loss=loss))
        ret = ending / beginning - 1 if beginning > 0 else None
        benchmark = benchmark_return(book, selection, loss=loss)
        valid = covered and ret is not None and benchmark is not None
        rows.append({**{key: selection[key] for key in ("month", "decision_date", "trade_date", "end_date", "end_kind")},
                     **accounting, "return": ret, "benchmark": benchmark,
                     "excess": ret - benchmark if valid else None, "valid": valid,
                     "exclusion": None if valid else "incomplete_listing_window" if not covered else "invalid_value_or_U",
                     "return_denominator": beginning, "end_value": ending})
    return {"per_month": rows, "state": state.to_dict(),
            "terminal_value": portfolio_value(state, book.marks(book.end, "close", loss=loss))}


def _mean(values):
    values = [value for value in values if value is not None]
    return float(np.mean(values)) if values else None


def _monthly_summary(rows):
    valid = [row for row in rows if row["valid"]]
    return {"primary": newey_west([row["excess"] for row in valid]),
            "mean_return": _mean([row["return"] for row in valid]),
            "mean_benchmark": _mean([row["benchmark"] for row in valid]),
            "accounting": {key: _mean([row[key] for row in rows]) for key in (
                "buy_turnover", "sell_turnover", "cost_fraction", "holdings_count", "retention_ratio",
                "frozen_weight", "cash_weight")},
            "buy_cost": sum(row["buy_cost"] for row in rows), "sell_cost": sum(row["sell_cost"] for row in rows)}


def control_metrics(book, selections, variant, actual, *, loss=False, saved=None):
    rng = np.random.default_rng(0)
    if saved is not None:
        rng.bit_generator.state = saved["rng_state"]
    summaries, checkpoints, path_rows = [], [], []
    for path in range(N_CONTROLS):
        initial = {} if saved is None else {"initial_state": saved["states"][path],
                                           "initial_value": saved["terminal_values"][path]}
        run = simulate(book, selections, variant, loss=loss, control_reference=actual["per_month"], rng=rng, **initial)
        summaries.append(_monthly_summary(run["per_month"]))
        checkpoints.append({"state": run["state"], "terminal_value": run["terminal_value"]})
        # Keep dollar turnover and costs beside every actual decision, without all fills.
        path_rows.append([{key: row[key] for key in ("decision_date", "excess", "valid", "buy_turnover",
                          "sell_turnover", "cost_fraction", "buy_cost", "sell_cost")} for row in run["per_month"]])
    means = [row["primary"]["mean"] for row in summaries]
    finite = [value for value in means if value is not None]
    actual_mean = _monthly_summary(actual["per_month"])["primary"]["mean"]
    comparison = signal_eval._control_summary(finite, actual_mean)
    monthly = []
    for pos, row in enumerate(actual["per_month"]):
        keys = ("buy_turnover", "sell_turnover", "cost_fraction", "buy_cost", "sell_cost")
        monthly.append({"decision_date": row["decision_date"],
                        "strategy": {key: row[key] for key in keys},
                        "control_mean": {key: _mean([path[pos][key] for path in path_rows]) for key in keys}})
    return {**comparison, "path_means": means, "per_month": monthly,
            "path_accounting": [row["accounting"] for row in summaries], "seed": 0,
            "paths_planned": N_CONTROLS, "draw_order": "path/month/stock_code",
            "checkpoint": {"states": [row["state"] for row in checkpoints],
                           "terminal_values": [row["terminal_value"] for row in checkpoints],
                           "rng_state": rng.bit_generator.state}}


def strategy_metrics(book, selections, variant, *, loss=False, states=None, controls=None):
    runs = {}
    for key, (version, multiplier) in SCENARIOS.items():
        initial = {} if states is None else {"initial_state": states[key]["state"],
                                            "initial_value": states[key]["terminal_value"]}
        runs[key] = simulate(book, selections, variant, model_version=version, multiplier=multiplier, loss=loss, **initial)
    main = runs["v2_1.0"]
    rows = main["per_month"]
    result = {**_monthly_summary(rows), "per_month": rows,
              "cost_sensitivity": {str(multiplier): {**_monthly_summary(runs[f"v2_{multiplier}"]["per_month"]),
                                     "per_month": runs[f"v2_{multiplier}"]["per_month"]} for multiplier in (1.0, 1.5, 2.0)},
              "cost_model_v1": {"used_for_judgement": False, **_monthly_summary(runs["v1_1.0"]["per_month"]),
                                "per_month": runs["v1_1.0"]["per_month"]},
              "random_control": control_metrics(book, selections, variant, main, loss=loss, saved=controls),
              "checkpoint": {key: {"state": run["state"], "terminal_value": run["terminal_value"]} for key, run in runs.items()}}
    result["by_year"] = {year: _monthly_summary([row for row in rows if row["decision_date"].startswith(year)])
                         for year in sorted({row["decision_date"][:4] for row in rows})}
    dated = pd.Series(rows, index=[row["decision_date"] for row in rows], dtype=object)
    result["short_sale_regimes"] = {name: _monthly_summary(group.tolist())
                                    for name, group in market_regimes.split_by_regime(dated).items()}
    for period, start, end in (("design", "2017-05-01", "2024-12-31"), ("validation", "2025-01-01", "2025-11-30")):
        result[period] = _monthly_summary([row for row in rows if start <= row["decision_date"] <= end])
    design, validation = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
    result["validation_year_same_sign"] = bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None
    result["validation_sign_used_for_judgement"] = False
    return result


def judge_variants(variants, fields, trial_count_after_run):
    criteria = fields["pass_criteria"]["design_validation"]
    minimum = criteria["minimum_observations"]["rebalances"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trial_count_after_run > 20 else criteria["core_t_stat_gt"]
    sufficient = {name: result["primary"]["count"] >= minimum and result["primary"]["t_stat"] is not None
                  and result["primary"]["standard_error"] is not None and result["primary"]["standard_error"] > 0
                  for name, result in variants.items()}
    corrected = holm({name: result["primary"]["p_one_sided"] if sufficient[name] else 1.0
                      for name, result in variants.items()})
    for name, result in variants.items():
        primary = result["primary"]
        mean15 = result["cost_sensitivity"]["1.5"]["primary"]["mean"]
        share = result["random_control"]["share_of_controls"]
        inputs = {**primary, "p_for_holm": primary["p_one_sided"] if sufficient[name] else 1.0,
                  "holm_p": corrected[name], "t_threshold": float(threshold), "mean_at_cost_1_5": mean15,
                  "random_control_share": share, "trial_count_after_run": trial_count_after_run,
                  "design_mean_excess": result["design"]["primary"]["mean"]}
        failures = []
        if not sufficient[name] or share is None or mean15 is None:
            verdict, reasons = "insufficient", ["fewer than required valid months or undefined SE/control/cost result"]
        else:
            if primary["mean"] <= criteria["net_performance_gt"]:
                failures.append("mean net excess must exceed 0")
            if mean15 <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
                failures.append("mean net excess at cost x1.5 must exceed 0")
            if primary["t_stat"] <= threshold:
                failures.append(f"NW t must exceed {threshold:g}")
            if corrected[name] > 0.05:
                failures.append("Holm(3) one-sided p must be <=0.05")
            if share > criteria["random_control_top_percent"] / 100:
                failures.append("random control share must be <=0.05")
            verdict, reasons = ("fail", failures) if failures else ("pass", ["all HC003 preregistered criteria satisfied"])
        result.update(verdict=verdict, reasons=reasons, holm_p=corrected[name], judgement_inputs=inputs)
    return variants


def apply_delisting_verdict(main, sensitivity):
    for name, result in main.items():
        base, alternative = result["verdict"], sensitivity[name]["verdict"]
        result.update(base_verdict=base, delisting_loss_verdict=alternative, delisting_sensitive=base != alternative)
        if base != alternative:
            result.update(verdict="delisting_sensitive", reasons=[f"last-quote verdict {base} differs from zero-loss verdict {alternative}"])
    return main


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def source_hashes(data_dir, price_paths, files, *, prices=None, end=DESIGN_END):
    root = Path(data_dir)
    profile = root / "reference/dart_company/companies.jsonl"
    groups = {"krx_daily": [path for _, path in price_paths], "dart_listings": list(files.values()),
              "quarterly": sorted(path for path in (root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")
                                  if int(path.stem[:4]) <= pd.Timestamp(end).year),
              "company_profile": [profile] if profile.is_file() else []}
    hashes = {name: {str(path.relative_to(root)): _digest(path) for path in paths} for name, paths in groups.items()}
    sources = (Path(__file__), Path(common.__file__), Path(hc001.__file__), Path(hc002.__file__),
               Path(hf001.__file__), Path(cost_model.__file__), Path(signal_eval.__file__),
               Path(holdout.__file__), Path(experiment_registry.__file__), Path(market_regimes.__file__),
               Path(krx_market.__file__), Path(dart_quarterly.__file__), Path(capacity.__file__),
               Path(hc001.industry_map.__file__), PROJECT_ROOT / "src/ingestion/storage.py",
               PROJECT_ROOT / "src/runner/trading_calendar.py")
    hashes["code"] = {str(path.relative_to(PROJECT_ROOT)): _digest(path) for path in sources}
    if prices is not None:
        hashes["in_memory_prices"] = hashlib.sha256(pd.util.hash_pandas_object(prices, index=True).to_numpy().tobytes()).hexdigest()
    return hashes


def _book(prices, selections, sessions, end, repo_root):
    rebalances = [row["trade_date"] for row in selections]
    rebalances += [row["end_date"] for row in selections if row["end_kind"] == "open"]
    return PriceBook(prices, sessions, rebalances, end, repo_root=repo_root)


def _prepare(prices, planned, *, data_dir, repo_root, sessions, files):
    common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    days = common._sessions(prices)
    required = sessions[(sessions >= pd.Timestamp(planned[0]["decision_date"])) &
                        (sessions <= pd.Timestamp(planned[-1]["end_date"]))] if planned else pd.DatetimeIndex([])
    if len(required.difference(days)):
        raise ValueError("incomplete exchange-session price coverage; cannot infer stock disappearance")
    if not planned:
        raise ValueError("no planned rebalance months")
    # The full listing span is guarded before HF001's read-only parsing function.
    if files:
        listing_days = pd.DatetimeIndex(list(files))
        holdout.guard_period(listing_days.min().date(), listing_days.max().date(),
                             experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    events, event_counts = hf001.rights_events(hf001.load_listings(files), sessions)
    selections, counts = build_selections(prices, planned, data_dir=data_dir, repo_root=repo_root,
                                          sessions=sessions, files=files, events=events)
    return selections, counts, event_counts


def secondary_metrics(book, selections, variant, result, hc002_monthly):
    buckets = {}
    lower = 0.0
    for number, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
        selected = []
        for row in selections:
            u = row["universe"]
            subset = u.loc[(u.avg_trading_value_20d >= lower) & (u.avg_trading_value_20d < upper)]
            selected.append({**row, "universe": subset})
        run = simulate(book, selected, variant)
        buckets[str(number)] = {**_monthly_summary(run["per_month"]), "per_month": run["per_month"],
                               "lower_inclusive": lower, "upper_exclusive": float(upper) if np.isfinite(upper) else None}
        lower = upper
    capacity_rows, limits, comparisons = [], [], []
    for row in result["per_month"]:
        for trade in row["trades"]:
            capacity_rows.append({"trade_date": row["decision_date"], "stock_code": trade["stock_code"],
                                  "avg_trading_value_20d": trade["adv"]})
        limit = min((trade["adv"] * 0.01 / (trade["amount"] / row["pre_value"])
                     for trade in row["trades"] if trade["amount"] > 0 and row["pre_value"] > 0), default=None)
        limits.append({"decision_date": row["decision_date"], "aum_at_1pct_adv": limit})
        baseline = hc002_monthly.get(row["decision_date"])
        comparisons.append({"decision_date": row["decision_date"], "strategy_net": row["return"],
                            "hc002_phase2_ew_net": baseline,
                            "net_difference": row["return"] - baseline if baseline is not None and row["return"] is not None else None})
    sample = pd.DataFrame(capacity_rows).drop_duplicates(["trade_date", "stock_code"]) if capacity_rows else None
    result.update(liquidity_buckets=buckets,
                  capacity={"p25_estimate": capacity.strategy_capacity(sample) if sample is not None else None,
                            "actual_trade_limits": limits,
                            "minimum_aum_at_1pct_adv": min((row["aum_at_1pct_adv"] for row in limits
                                                           if row["aum_at_1pct_adv"] is not None), default=None)},
                  hc002_full_turnover={"interpretation": INTERPRETATIONS["secondary"] + " " + ASSUMPTIONS[12],
                                       "per_month": comparisons,
                                       "mean_net_difference": _mean([row["net_difference"] for row in comparisons])})


def hc002_comparison(prices, selections, *, repo_root):
    rows = [row["universe"].assign(decision_date=pd.Timestamp(row["decision_date"]),
                                   trade_date=pd.Timestamp(row["trade_date"])).reset_index()
            for row in selections if not row["universe"].empty]
    if not rows:
        return {}
    universe = pd.concat(rows, ignore_index=True)
    with common.guarded_reads(EXPERIMENT_ID):
        observations = hc002.forward_observations(prices, universe, repo_root=repo_root)
    baseline = hc001.portfolio_metrics(observations)
    return {row["trade_date"]: row["net_1.0"] for row in baseline["per_rebalance"]}


def _financial_ratios(counts):
    result = {}
    for year in sorted({row["decision_date"][:4] for row in counts}):
        rows = [row for row in counts if row["decision_date"].startswith(year)]
        eligible = sum(row["price_eligible"] for row in rows)
        missing = sum(row["no_data"] for row in rows)
        result[year] = {"price_eligible_stock_months": eligible, "uncomputable_stock_months": missing,
                        "uncomputable_ratio": missing / eligible if eligible else None}
    return result


def write_results(payload, *, repo_root=PROJECT_ROOT):
    """HC003's own finite JSON and interpretations, with exclusive filenames."""
    _registration(repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    lines = [f"# {EXPERIMENT_ID}: {payload['stage']}", "", f"- Verdict: {payload['verdict']}",
             "- Reasons: " + "; ".join(payload["reasons"]), "", INTERPRETATIONS["note"], "",
             "| Variant | Months | Mean e | NW(3) t | Holm p | Strategy buy/sell turnover | Strategy cost | Control buy/sell turnover | Control cost | Verdict |",
             "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: | --- |"]
    for name, result in payload["variants"].items():
        stat, accounting = result["primary"], result["accounting"]
        controls = result["random_control"]["path_accounting"]
        control_buy = _mean([row["buy_turnover"] for row in controls])
        control_sell = _mean([row["sell_turnover"] for row in controls])
        control_cost = _mean([row["cost_fraction"] for row in controls])
        lines.append(f"| {name} | {stat['count']} | {stat['mean']} | {stat['t_stat']} | {result.get('holm_p')} | "
                     f"{accounting['buy_turnover']}/{accounting['sell_turnover']} | {accounting['cost_fraction']} | "
                     f"{control_buy}/{control_sell} | {control_cost} | {result['verdict']} |")
    lines += ["", "## interpretations", ""] + [f"- {key}: {value}" for key, value in INTERPRETATIONS.items()]
    lines += ["", "## assumptions", ""] + [f"- {value}" for value in ASSUMPTIONS]
    directory = Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = directory / f"{stamp}.json", directory / f"{stamp}.md"
    with paths[0].open("x", encoding="utf-8") as jf:
        with paths[1].open("x", encoding="utf-8") as mf:
            jf.write(encoded)
            mf.write("\n".join(lines) + "\n")
    return paths


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    injected = prices is not None
    if injected:
        # Never consume a session through an injected design price frame.
        common.guard_prices(prices, repo_root=repo_root)
    sessions = session_calendar() if sessions is None else signal_eval._dates(sessions).sort_values().unique()
    planned, excluded = rebalance_calendar(sessions)
    paths = []
    if prices is None:
        prices, paths = load_prices(data_dir, "20150101", "20251230", repo_root=repo_root)
    if prices.empty or common._sessions(prices).max() > DESIGN_END:
        raise ValueError("design/validation prices must end by 2025-12-30")
    files = listing_files(data_dir, "20251130", repo_root=repo_root)
    hashes = source_hashes(data_dir, paths, files, prices=prices if injected else None)
    selections, counts, events = _prepare(prices, planned, data_dir=data_dir, repo_root=repo_root,
                                          sessions=sessions, files=files)
    if selections[-1]["month"] != "2025-11":
        raise ValueError("design run must persist the registered 2025-11 post-trade state")
    book = _book(prices, selections, sessions, DESIGN_END, repo_root)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3
    variants = judge_variants({name: strategy_metrics(book, selections, name) for name in VARIANTS}, registration.fields, trials)
    sensitivity = judge_variants({name: strategy_metrics(book, selections, name, loss=True) for name in VARIANTS}, registration.fields, trials)
    apply_delisting_verdict(variants, sensitivity)
    baseline = hc002_comparison(prices, selections, repo_root=repo_root)
    for name in VARIANTS:
        secondary_metrics(book, selections, name, variants[name], baseline)
    states = {name: {"scenarios": variants[name].pop("checkpoint"),
                     "controls": variants[name]["random_control"].pop("checkpoint")} for name in VARIANTS}
    for result in sensitivity.values():
        result.pop("checkpoint")
        result["random_control"].pop("checkpoint")
    primary = variants["t_hold"]
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "design_validation", "selected_variant": "t_hold",
               "verdict": primary["verdict"], "reasons": primary["reasons"], "prereg_commit": registration.commit_hash,
               "prereg_sha256": _digest(Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md"),
               "variants": variants, "delisting_sensitivity": {"variants": sensitivity},
               "design_states": states, "state_sha256": _content_hash(states),
               "state_decision_month": "2025-11", "state_valuation_date": "2025-12-30",
               "source_hashes": hashes, "interpretations": INTERPRETATIONS, "assumptions": ASSUMPTIONS,
               "selections": [{**{key: value for key, value in row.items() if key not in ("universe", "blocked_buys")},
                               "U": list(row["universe"].index),
                               "P": sorted(target_set("t_hold", row["universe"], ())),
                               "blocked_buys": sorted(row["blocked_buys"])} for row in selections],
               "counts": {"hc002_by_month": counts, "rights_events": events, **book.counts,
                          "financial_signal_missing_by_year": _financial_ratios(counts)},
               "skipped_months": excluded,
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    result_paths = write_results(payload, repo_root=repo_root)
    relative = str(result_paths[0].relative_to(repo_root))
    for name in VARIANTS:
        result = variants[name]
        experiment_registry.record_trial(EXPERIMENT_ID, name, {
            **result["judgement_inputs"], "stage": "design_validation", "result_file": relative,
            "result_sha256": _digest(result_paths[0]), "state_sha256": payload["state_sha256"],
            "base_verdict": result["base_verdict"], "delisting_loss_verdict": result["delisting_loss_verdict"]},
            result["verdict"], repo_root=repo_root, note="fixed HC003 variant; v2 turnover accounting, NW(3), Holm(3); no holdout")
    return payload


def registry_rows(repo_root):
    path = Path(repo_root) / experiment_registry.REGISTRY_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            return experiment_registry._registry_rows(handle)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def ledger_experiments(repo_root):
    path = Path(repo_root) / holdout.LEDGER_PATH
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        rows = [json.loads(line) for line in handle]
    if any(not isinstance(row, dict) or not all(key in row for key in
               ("experiment_id", "recorded_at", "from_date", "to_date")) for row in rows):
        raise ValueError("invalid holdout ledger")
    return {row["experiment_id"] for row in rows}


def _saved_design(rows, repo_root, registration):
    passing = [row for row in rows if row["experiment_id"] == EXPERIMENT_ID
               and row["variant"] in VARIANTS and row["verdict"] == "pass"]
    if not passing:
        raise ValueError("HC003 holdout requires a design/validation pass")
    metrics = json.loads(passing[-1]["metrics_json"])
    path = (Path(repo_root) / metrics["result_file"]).resolve()
    directory = (Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results").resolve()
    if not path.is_relative_to(directory) or _digest(path) != metrics["result_sha256"]:
        raise ValueError("saved design result hash/path differs")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (payload["stage"] != "design_validation" or payload["prereg_commit"] != registration.commit_hash
            or _content_hash(payload["design_states"]) != payload["state_sha256"]
            or payload["state_sha256"] != metrics["state_sha256"]
            or payload["state_decision_month"] != "2025-11" or payload["state_valuation_date"] != "2025-12-30"):
        raise ValueError("saved November state/preregistration differs")
    passed = [name for name in VARIANTS if payload["variants"][name]["verdict"] == "pass"
              and any(row["variant"] == name and json.loads(row["metrics_json"]).get("result_file") == metrics["result_file"]
                      for row in passing)]
    if not passed:
        raise ValueError("no registered pass matches the saved design result")
    return payload, passed


def verify_non_holdout(design, passed, *, data_dir, repo_root, sessions):
    """Replay every relevant scenario and RNG path before a protected read."""
    prices, paths = load_prices(data_dir, "20150101", "20251230", repo_root=repo_root)
    files = listing_files(data_dir, "20251130", repo_root=repo_root)
    if source_hashes(data_dir, paths, files) != design["source_hashes"]:
        raise ValueError("computation sources differ from saved design run")
    planned, _ = rebalance_calendar(sessions)
    selections, _, _ = _prepare(prices, planned, data_dir=data_dir, repo_root=repo_root, sessions=sessions, files=files)
    book = _book(prices, selections, sessions, DESIGN_END, repo_root)
    for name in passed:
        replay = strategy_metrics(book, selections, name)
        expected = design["design_states"][name]
        actual = {"scenarios": replay["checkpoint"], "controls": replay["random_control"]["checkpoint"]}
        if _content_hash(actual) != _content_hash(expected):
            raise ValueError("non-holdout replay does not reproduce November state and December value")
    return prices


def holdout_verdict(result, design_effect, fields):
    criteria = fields["pass_criteria"]["holdout"]
    mean = result["primary"]["mean"]
    if result["primary"]["count"] != 6 or mean is None or design_effect is None or design_effect <= 0:
        return "insufficient", ["six valid holdout months and positive design effect required"]
    if mean <= criteria["net_performance_gt"] or np.sign(mean) != np.sign(design_effect):
        return "fail", ["holdout net excess must be positive and have the design direction"]
    if mean < design_effect * criteria["minimum_fraction_of_design_effect"]:
        return "fail", ["holdout effect must be at least half the design-period effect"]
    return "pass", ["all HC003 section-6 criteria satisfied"]


def _holdout_status(status, reason, *, repo_root):
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "passed_design_variants",
               "verdict": status, "reasons": [reason], "variants": {},
               "interpretations": INTERPRETATIONS, "assumptions": ASSUMPTIONS}
    experiment_registry.record_trial(EXPERIMENT_ID, status, {"stage": "holdout", "reason": reason},
                                      status, repo_root=repo_root, note="terminal; no reopening of protected evaluation")
    write_results(payload, repo_root=repo_root)
    return payload


def run_holdout(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    started = time.perf_counter()
    registration = _registration(repo_root)
    # Read only the ledger before any returns or source computation.
    used = ledger_experiments(repo_root)
    rows = registry_rows(repo_root)
    if not any(row["experiment_id"] == EXPERIMENT_ID and row["variant"] in VARIANTS
               and row["verdict"] == "pass" for row in rows):
        raise ValueError("HC003 holdout requires a design/validation pass")
    if EXPERIMENT_ID in used or any(row["experiment_id"] == EXPERIMENT_ID and
            (row["variant"].startswith("holdout_") or row["verdict"] in ("holdout_aborted", "holdout_contaminated")) for row in rows):
        raise ValueError("HC003 holdout already used or terminally blocked; cannot reopen")
    if "LH002_llm_hegemony_judge" in used:
        return _holdout_status("holdout_contaminated", "LH002 entry exists in holdout ledger; no returns read", repo_root=repo_root)
    design, passed = _saved_design(rows, repo_root, registration)
    sessions = session_calendar()
    history = verify_non_holdout(design, passed, data_dir=data_dir, repo_root=repo_root, sessions=sessions)
    planned, excluded = rebalance_calendar(sessions, holdout_mode=True)
    if len(planned) != 6 or excluded:
        raise ValueError("holdout calendar must contain the six preregistered months")
    try:
        with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=repo_root):
            protected, paths = load_prices(data_dir, "20260101", "20260630", repo_root=repo_root, experiment_id=EXPERIMENT_ID)
            prices = pd.concat([history, protected]).sort_index()
            files = listing_files(data_dir, "20260531", repo_root=repo_root, experiment_id=EXPERIMENT_ID)
            hashes = source_hashes(data_dir, paths, files, end=HOLDOUT_END)
            hashes["krx_daily"] = {**design["source_hashes"].get("krx_daily", {}), **hashes["krx_daily"]}
            selections, counts, events = _prepare(prices, planned, data_dir=data_dir, repo_root=repo_root,
                                                  sessions=sessions, files=files)
            book = _book(prices, selections, sessions, HOLDOUT_END, repo_root)
            variants, sensitivity = {}, {}
            baseline = hc002_comparison(prices, selections, repo_root=repo_root)
            for name in passed:
                stored = design["design_states"][name]
                effect = design["variants"][name]["design"]["primary"]["mean"]
                for destination, loss in ((variants, False), (sensitivity, True)):
                    result = strategy_metrics(book, selections, name, loss=loss,
                                              states=stored["scenarios"], controls=stored["controls"])
                    verdict, reasons = holdout_verdict(result, effect, registration.fields)
                    result.update(verdict=verdict, reasons=reasons, design_mean_excess=effect)
                    result.pop("checkpoint")
                    result["random_control"].pop("checkpoint")
                    destination[name] = result
                secondary_metrics(book, selections, name, variants[name], baseline)
            apply_delisting_verdict(variants, sensitivity)
        headline = variants.get("t_hold", variants[passed[0]])
        payload = {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "passed_design_variants",
                   "verdict": headline["verdict"], "reasons": headline["reasons"], "variants": variants,
                   "delisting_sensitivity": {"variants": sensitivity}, "source_hashes": hashes,
                   "design_state_sha256": design["state_sha256"], "prereg_commit": registration.commit_hash,
                   "interpretations": INTERPRETATIONS, "assumptions": ASSUMPTIONS,
                   "counts": {**book.counts, "hc002_by_month": counts, "rights_events": events,
                              "financial_signal_missing_by_year": _financial_ratios(counts)},
                   "performance": {"runtime_seconds": time.perf_counter() - started,
                                   "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
        result_paths = write_results(payload, repo_root=repo_root)
        for name, result in variants.items():
            experiment_registry.record_trial(EXPERIMENT_ID, f"holdout_{name}", {
                "stage": "holdout", "mean_excess": result["primary"]["mean"], "months": result["primary"]["count"],
                "design_mean_excess": result["design_mean_excess"], "state_sha256": design["state_sha256"],
                "result_file": str(result_paths[0].relative_to(repo_root))}, result["verdict"], repo_root=repo_root)
        return payload
    except BaseException as exc:
        _holdout_status("holdout_aborted", f"{type(exc).__name__}: {exc}", repo_root=repo_root)
        raise


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    registration = _registration(repo_root)
    sessions = session_calendar()
    planned, excluded = rebalance_calendar(sessions)
    prices = price_files(data_dir, "20150101", "20251230", repo_root=repo_root)
    files = listing_files(data_dir, "20251130", repo_root=repo_root)
    price_days = pd.DatetimeIndex([day for day, _ in prices])
    months = []
    for row in planned:
        pos = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        end = sessions.get_loc(pd.Timestamp(row["end_date"]))
        required = sessions[max(0, pos - 19):end + 1]
        months.append({**row, "price_covered": not len(required.difference(price_days)),
                       "vol60_covered": pos >= 59 and not len(sessions[pos - 59:pos + 1].difference(price_days)),
                       "listing": hf001.listing_coverage(row["decision_date"], sessions, files)})
    financials, _ = hc001._financial_inventory(data_dir)
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash,
            "planned_months": len(hc002.MONTHS), "calendar_months": len(planned), "excluded_months": excluded,
            "covered_price_months": sum(row["price_covered"] for row in months),
            "listing_excluded_months": sum(not row["listing"]["covered"] for row in months),
            "months": months, "price_files": len(prices), "listing_files": len(files),
            "financials": financials, "industry_profiles": len(hc001._industries(data_dir)),
            "minimum_months": registration.fields["pass_criteria"]["design_validation"]["minimum_observations"]["rebalances"],
            "read_span": "2015-01-01..2025-12-30", "assumptions": ASSUMPTIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="local plan and coverage only (default)")
    mode.add_argument("--execute", action="store_true", help="record all three design/validation variants")
    mode.add_argument("--holdout", action="store_true", help="single protected evaluation of passing variants")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute or args.holdout:
        result = (run_holdout if args.holdout else run_experiment)(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} {result['stage']}: {result['verdict']}")
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run; variants=" + ",".join(VARIANTS) + "; fixed headline=t_hold")
        print(f"Committed immutable preregistration: {plan['prereg_commit']}")
        print(f"Months: {plan['planned_months']} planned; calendar={plan['calendar_months']}; price-covered={plan['covered_price_months']}; minimum={plan['minimum_months']}")
        print(f"Guarded price span: {plan['read_span']}; KRX files={plan['price_files']}; listing files={plan['listing_files']}; profiles={plan['industry_profiles']}")
        print(f"t_filter incomplete listing-window months: {plan['listing_excluded_months']} (buys blocked; excluded from inference)")
        print("Accounting: persistent units/cash; sells first; v2 one-way costs; 0.1% band once; NW(3)/Holm(3); 200 controls per variant.")
        print("First month: cash 1. Last month: 2025-12-30 close without liquidation. Holdout: stored November state; one HC003 session after non-holdout verification and LH002 ledger check.")
        print(f"Quarterly archives={len(plan['financials']['quarters'])}; rows through 2025={plan['financials']['stored_rows_through_2025']}")
        print("Missing quarterly archives: " + ",".join(plan["financials"]["missing_quarters"]))
        print("Incomplete price months: " + ",".join(row["month"] for row in plan["months"] if not row["price_covered"]))
        print("Excluded calendar months: " + json.dumps(plan["excluded_months"]))
        print("No returns evaluated; no random draws, registry rows, result files or holdout claims. Coverage does not prove computable signals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
