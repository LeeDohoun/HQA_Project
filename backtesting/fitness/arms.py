"""The simpler strategies the PAPER pipeline is compared with, stated as plans for `Simulator`.

Every arm sees closes through the day before its decision day and the same PAPER eligibility
(the watchlist's theme-universe entry filters), and trades only at the decision day's open.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
import yaml

from backtesting.fitness.simulate import DecisionView, Plan, PricePanel
from src.runner.analysis_data import PRICE_WEIGHTS, rank_price_candidates

TECHNICAL_BASELINES = ("rsi_oversold", "bollinger_lower", "rsi_ranked", "bollinger_ranked", "momentum_20d",
                       "vol_adjusted_momentum")


def entry_filters(config_path: str = "config/watchlist.yaml") -> dict:
    with open(config_path, encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle)["trading"].get("theme_universe_filters") or {})


class Features:
    """PAPER's price factors (`analysis_data.price_features`) from the last 300 bars through a day."""

    def __init__(self, panel: PricePanel, filters: dict):
        self.panel, self.filters = panel, filters
        self._cache: dict[tuple[str, str], dict | None] = {}

    def get(self, code: str, through: str) -> dict | None:
        key = (code, through)
        if key not in self._cache:
            self._cache[key] = self._compute(code, through)
        return self._cache[key]

    def _compute(self, code: str, through: str) -> dict | None:
        known = self.panel.history(code, through).tail(300)
        if len(known) < 150 or known.index[-1] != through:
            return None
        close, volume = known["close"], known["volume"]
        if volume.tail(20).mean() <= 0:
            return None
        features = {"current_price": float(close.iloc[-1]), "history_days": len(known),
                    "return_5d": float(close.iloc[-1] / close.iloc[-6] - 1),
                    "return_20d": float(close.iloc[-1] / close.iloc[-21] - 1),
                    "return_60d": float(close.iloc[-1] / close.iloc[-61] - 1),
                    "trend_150d": float(close.iloc[-1] / close.tail(150).mean() - 1),
                    "volume_ratio_20d": float(volume.iloc[-1] / volume.tail(20).mean()),
                    "volatility_20d": float(close.pct_change().dropna().tail(20).std() * math.sqrt(252)),
                    "avg_trading_value_20d": float((close * volume).tail(20).mean())}
        if any(not math.isfinite(features[key]) for key in PRICE_WEIGHTS):
            return None
        return features

    def filter_errors(self, features: dict) -> list[str]:
        """`LocalAnalysisData._filter_errors` with the same watchlist thresholds."""
        errors = []
        if features["history_days"] < int(self.filters.get("min_history_days", 150)):
            errors.append("min_history_days")
        if features["volume_ratio_20d"] <= 0:
            errors.append("no_trade_latest_session")
        for setting, factor, minimum in (("min_avg_trading_value_20d", "avg_trading_value_20d", True),
                                        ("max_volatility_20d", "volatility_20d", False),
                                        ("max_return_5d", "return_5d", False),
                                        ("max_return_20d", "return_20d", False),
                                        ("min_trend_150d", "trend_150d", True)):
            if self.filters.get(setting) is not None:
                threshold = float(self.filters[setting])
                if (features[factor] < threshold) if minimum else (features[factor] > threshold):
                    errors.append(setting)
        return errors

    def eligible(self, view: DecisionView) -> list[dict]:
        """Stocks PAPER would rank on this day: factors through the previous close, no entry-filter
        error, and a tradable open."""
        rows = []
        if view.previous_day is None:
            return rows
        for code in sorted(self.panel.names):
            features = self.get(code, view.previous_day)
            if features is None or code not in view.opens or self.filter_errors(features):
                continue
            rows.append({"stock_code": code, "stock_name": self.panel.names[code], "features": features})
        return rows

    def ranked(self, view: DecisionView) -> list[dict]:
        rows = self.eligible(view)
        return rank_price_candidates(rows) if rows else []


def schedule(panel: PricePanel, start: str, end: str, kind: str) -> list[str]:
    """Decision days: every session, the first session of each week, or of each month."""
    days = [day for day in panel.calendar if start <= day <= end]
    if kind == "daily":
        return days
    out, seen = [], set()
    for day in days:
        stamp = pd.Timestamp(day)
        key = (stamp.isocalendar().year, stamp.isocalendar().week) if kind == "weekly" else (stamp.year, stamp.month)
        if key not in seen:
            seen.add(key)
            out.append(day)
    return out


def rule_leader(features: Features, *, top_n: int = 3, hold: int = 20, stop_pct: float | None = None,
                take_profit_pct: float | None = None, size_pct: float | None = None):
    """PAPER's own rule score (the prefilter's price_score): buy the top N not already held."""

    def decide(view: DecisionView) -> list[Plan]:
        picks = [row for row in features.ranked(view) if row["stock_code"] not in view.positions][:top_n]
        return [Plan(code=row["stock_code"], action="BUY", weight=None if size_pct else 1.0 / top_n,
                     size_pct=size_pct, stop_pct=stop_pct, take_profit_pct=take_profit_pct, hold_sessions=hold,
                     meta={"arm": "rule_leader", "rank": rank, "price_score": row["price_score"]})
                for rank, row in enumerate(picks, 1)]

    return decide


def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0).tail(period).mean()
    loss = (-delta.clip(upper=0)).tail(period).mean()
    if not math.isfinite(float(loss)) or float(loss) <= 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + float(gain) / float(loss)))


def technical(features: Features, baseline: str, *, top_n: int = 3, hold: int = 5, stop_pct: float | None = None):
    """The first pass's technical baselines (`backtesting/technical_baseline.py` signals and scores)."""
    if baseline not in TECHNICAL_BASELINES:
        raise ValueError(baseline)

    def score(view: DecisionView, row: dict) -> float | None:
        closes = features.panel.history(row["stock_code"], view.previous_day)["close"]
        rsi = _rsi(closes)
        middle, std = float(closes.tail(20).mean()), float(closes.tail(20).std(ddof=0))
        width = 4.0 * std
        percent_b = (float(closes.iloc[-1]) - (middle - 2.0 * std)) / width if width > 0 else 0.5
        if baseline == "rsi_oversold" and rsi > 30:
            return None
        if baseline == "bollinger_lower" and percent_b > 0:
            return None
        if baseline.startswith("rsi"):
            return max(0.0, 100.0 - rsi)
        if baseline.startswith("bollinger"):
            return max(0.0, (1.0 - percent_b) * 100.0)
        if baseline == "momentum_20d":
            return row["features"]["return_20d"] * 100.0
        return row["features"]["return_20d"] / max(0.05, row["features"]["volatility_20d"]) * 100.0

    def decide(view: DecisionView) -> list[Plan]:
        scored = []
        for row in features.eligible(view):
            if row["stock_code"] in view.positions:
                continue
            value = score(view, row)
            if value is not None:
                scored.append((value, -row["features"]["volatility_20d"], row["features"]["avg_trading_value_20d"], row))
        scored.sort(key=lambda item: item[:3], reverse=True)
        return [Plan(code=row["stock_code"], action="BUY", weight=1.0 / top_n, stop_pct=stop_pct, hold_sessions=hold,
                     meta={"arm": baseline, "rank": rank, "score": value})
                for rank, (value, _, _, row) in enumerate(scored[:top_n], 1)]

    return decide


def buy_and_hold(features: Features):
    """Equal weight in every stock tradable on the first day, held to the end."""
    state = {"done": False}

    def decide(view: DecisionView) -> list[Plan]:
        if state["done"]:
            return []
        state["done"] = True
        codes = sorted(view.opens)
        return [Plan(code=code, action="BUY", weight=1.0 / len(codes), meta={"arm": "buy_and_hold"}) for code in codes]

    return decide


def plan_template(features: Features, *, top_n: int = 1, size_pct: float = 5.0, stop_pct: float = 0.07,
                  take_profit_pct: float = 0.10, reduce_pct: float = 0.06, hold: int = 7):
    """The shape of PAPER's RiskManager plans (10-12..10-19 medians: 5% size, stop -7%, half at
    +6%, the rest at +10%, out in about 7 sessions) applied mechanically to the rule ranking."""
    from backtesting.fitness.simulate import Group

    def decide(view: DecisionView) -> list[Plan]:
        picks = [row for row in features.ranked(view) if row["stock_code"] not in view.positions][:top_n]
        plans = []
        for row in picks:
            price = view.opens[row["stock_code"]]
            plans.append(Plan(code=row["stock_code"], action="BUY", size_pct=size_pct, stop_pct=stop_pct,
                              take_profit_pct=take_profit_pct, hold_sessions=hold,
                              reduces=[Group("take-profit-1", [("current_price", ">=", price * (1 + reduce_pct))], 0.5)],
                              meta={"arm": "plan_template", "price_score": row["price_score"]}))
        return plans

    return decide
