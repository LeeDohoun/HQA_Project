"""Deterministic long-only sizing and exits from caller-supplied daily prices."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _number(value, name, *, positive=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{name} must be a finite number")
    if not np.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")


def _integer(value, name, *, minimum=1):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _dates(values):
    dates = pd.DatetimeIndex(pd.to_datetime(values))
    if dates.hasnans or dates.tz is not None or not dates.equals(dates.normalize()):
        raise ValueError("sessions must be date-only, timezone-naive dates")
    return dates


def _price_frame(prices, fields):
    if not isinstance(prices, pd.DataFrame):
        raise ValueError("prices must be a DataFrame")
    frame = prices.reset_index() if prices.index.names == ["trade_date", "stock_code"] else prices.copy()
    required = {"trade_date", "stock_code", *fields}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing price columns: {sorted(required - set(frame.columns))}")
    frame["trade_date"] = _dates(frame.trade_date)
    if not frame.stock_code.map(lambda code: isinstance(code, str) and bool(code.strip())).all():
        raise ValueError("stock_code must be a nonblank string")
    if frame.duplicated(["trade_date", "stock_code"]).any():
        raise ValueError("price keys must be unique")
    return frame.sort_values(["trade_date", "stock_code"])


def _capped_weights(raw, themes, max_weight, max_theme_weight):
    """Water-fill in raw-weight proportions until fully invested or all capped.

    Weights only increase. Every partial step reaches a stock or theme cap and
    removes at least one active name; the last step exhausts the budget. Thus
    convergence takes at most n + 1 steps, with a 1e-12 weight tolerance. Cash is
    retained when the caps make unit weight infeasible; caps are never relaxed.
    max_weight may also be a per-name array (used by the risk allocator).
    None denotes an unclassified name, not a shared theme.
    """
    weights = np.zeros(len(raw))
    caps = np.broadcast_to(max_weight, raw.shape)
    active = caps > 0
    groups = [np.array([theme == label for theme in themes])
              for label in sorted({theme for theme in themes if theme is not None})]
    for _ in range(len(raw) + 1):
        remaining = 1.0 - weights.sum()
        if remaining <= 1e-12 or not active.any():
            return weights
        step = remaining / raw[active].sum()
        step = min(step, np.min((caps[active] - weights[active]) / raw[active]))
        for group in groups:
            members = group & active
            if members.any():
                step = min(step, (max_theme_weight - weights[group].sum()) / raw[members].sum())
        weights[active] += max(0.0, step) * raw[active]
        active &= weights < caps - 1e-12
        for group in groups:
            if weights[group].sum() >= max_theme_weight - 1e-12:
                active[group] = False
    raise RuntimeError("capped weight redistribution did not converge")


def size_positions(
    targets, prices, capital_krw, *, max_positions=20, max_weight=0.10,
    max_theme_weight=0.30, adv_participation=0.01, vol_lookback=20, min_order_krw=100_000,
) -> tuple[pd.DataFrame, float]:
    """Return (orders, uninvested_cash); weight is final notional / capital.

    targets are (stock_code, score) or (stock_code, score, theme). Higher scores
    select names, with stock_code breaking ties; scores do not set weights.
    prices may have (trade_date, stock_code) columns or that MultiIndex, with
    close, ret_1d (fractional returns), and trading_value (KRW). The caller must
    cut prices to its information time. The last supplied session is the common
    reference date; every selected name needs complete trailing session windows.
    Volatility is sample std (ddof=1); missing/zero volatility raises an error.

    Stock/theme caps redistribute in inverse-volatility proportions. Subsequent
    20-session ADV caps, share flooring, and minimum-order drops retain cash and
    do not redistribute. binding_constraint lists the limiting steps in order.
    Reference closes are used as observed, without tick rounding or cost reserves;
    this table is sizing advice, not an executable order or a fill assumption.
    """
    _number(capital_krw, "capital_krw")
    _integer(max_positions, "max_positions")
    _integer(vol_lookback, "vol_lookback", minimum=2)
    _number(min_order_krw, "min_order_krw")
    for value, name in ((max_weight, "max_weight"), (max_theme_weight, "max_theme_weight"),
                        (adv_participation, "adv_participation")):
        _number(value, name, positive=True)
        if value > 1:
            raise ValueError(f"{name} must be in (0, 1]")
    selected = []
    for target in targets:
        if len(target) not in (2, 3):
            raise ValueError("targets must contain (stock_code, score, optional theme)")
        code, score = target[:2]
        theme = target[2] if len(target) == 3 else None
        if not isinstance(code, str) or not code.strip():
            raise ValueError("target stock_code must be a nonblank string")
        if isinstance(score, (bool, np.bool_)) or not isinstance(score, (int, float, np.number)) or not np.isfinite(score):
            raise ValueError("score must be finite")
        if theme is not None and (not isinstance(theme, str) or not theme.strip()):
            raise ValueError("theme must be None or a nonblank string")
        selected.append((code, float(score), theme))
    if len({code for code, _, _ in selected}) != len(selected):
        raise ValueError("target stock codes must be unique")
    selected = sorted(selected, key=lambda row: (-row[1], row[0]))[:max_positions]
    columns = ["stock_code", "score", "theme", "weight", "shares", "notional", "binding_constraint"]
    if not selected or capital_krw == 0:
        return pd.DataFrame(columns=columns), float(capital_krw)
    frame = _price_frame(prices, {"close", "ret_1d", "trading_value"})
    sessions = frame.trade_date.drop_duplicates().to_numpy()
    if len(sessions) < max(20, vol_lookback):
        raise ValueError("insufficient session history for volatility and 20-session ADV")
    closes, volatilities, advs = [], [], []
    for code, _, _ in selected:
        history = frame.loc[frame.stock_code == code].set_index("trade_date")
        returns = history.ret_1d.reindex(sessions[-vol_lookback:]).to_numpy(dtype=float)
        values = history.trading_value.reindex(sessions[-20:]).to_numpy(dtype=float)
        close = history.close.reindex(sessions[-1:]).iloc[0]
        _number(close, f"reference close for {code}", positive=True)
        if not np.isfinite(returns).all():
            raise ValueError(f"{code} requires complete finite ret_1d history")
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"{code} requires complete nonnegative trading_value history")
        volatility = float(np.std(returns, ddof=1))
        _number(volatility, f"volatility for {code}", positive=True)
        closes.append(float(close))
        volatilities.append(volatility)
        advs.append(float(values.mean()))
    raw = np.min(volatilities) / np.array(volatilities)
    if (raw <= 0).any():
        raise ValueError("volatility range cannot be normalized")
    themes = [theme for _, _, theme in selected]
    weights = _capped_weights(raw / raw.sum(), themes, max_weight, max_theme_weight)
    rows = []
    for (code, score, theme), weight, close, adv in zip(selected, weights, closes, advs):
        reasons = []
        if weight >= max_weight - 1e-12:
            reasons.append("max_weight")
        if theme is not None and sum(w for w, t in zip(weights, themes) if t == theme) >= max_theme_weight - 1e-12:
            reasons.append("max_theme_weight")
        budget = float(weight * capital_krw)
        if adv_participation * adv < budget:
            budget = adv_participation * adv
            reasons.append("adv_cap")
        shares = math.floor(budget / close)
        notional = shares * close
        if notional > budget:  # Floating-point division must never round an order above its cap.
            shares -= 1
            notional = shares * close
        if notional < budget:
            reasons.append("share_rounding")
        if shares == 0 or notional < min_order_krw:
            continue
        rows.append((code, score, theme, notional / capital_krw, shares, notional, ",".join(reasons) or "none"))
    orders = pd.DataFrame(rows, columns=columns)
    return orders, float(capital_krw - sum(row[5] for row in rows))


def atr_stop(prices, stock_code, as_of, multiple=2.0, lookback=14) -> float:
    """Long stop = last close - multiple * arithmetic-mean true range.

    Uses only sessions <= as_of and requires lookback + 1 complete sessions for
    previous-close gaps. This is a theoretical price, before execution tick rounding.
    """
    _number(multiple, "multiple", positive=True)
    _integer(lookback, "lookback")
    day = _dates([as_of])[0]
    frame = _price_frame(prices, {"high", "low", "close"})
    frame = frame.loc[frame.trade_date <= day]
    sessions = frame.trade_date.drop_duplicates().to_numpy()[-(lookback + 1):]
    if len(sessions) < lookback + 1:
        raise ValueError("insufficient session history for ATR")
    history = frame.loc[frame.stock_code == stock_code].set_index("trade_date").reindex(sessions)
    values = history[["high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("ATR requires complete positive high/low/close history")
    if (history.high < history.low).any() or (history.close > history.high).any() or (history.close < history.low).any():
        raise ValueError("high/low/close are inconsistent")
    previous = history.close.shift(1)
    ranges = np.maximum.reduce([(history.high - history.low).to_numpy(),
                                (history.high - previous).abs().to_numpy(),
                                (history.low - previous).abs().to_numpy()])[1:]
    stop = float(history.close.iloc[-1] - multiple * ranges.mean())
    _number(stop, "ATR stop", positive=True)
    return stop


def time_stop(entry_date, sessions, max_hold_sessions) -> bool:
    """True at the holding-session limit, counting the entry session as one.

    sessions is the observed exchange calendar through the caller's decision
    date, not a future calendar. Entry must be present; holidays are not inferred.
    """
    _integer(max_hold_sessions, "max_hold_sessions")
    dates = _dates(sessions)
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("sessions must be unique and increasing")
    entry = _dates([entry_date])[0]
    if entry not in dates:
        raise ValueError("entry_date must be an observed trading session")
    return bool(len(dates) - dates.get_loc(entry) >= max_hold_sessions)
