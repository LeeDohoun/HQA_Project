"""Comparison metrics on a simulated daily equity curve, with bootstrap intervals for small samples."""
from __future__ import annotations

import math
from datetime import date

import numpy as np

PERIODS = {"2024H2": ("2024-08-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
           "2026YTD": ("2026-01-01", "2026-12-31")}
TRADING_DAYS = 252


def _returns(result: dict, start: str, end: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Daily returns inside [start, end], each against the equity at the previous close. A window
    that begins with the run measures its first day from the run's starting cash: entries fill at
    that day's open and a same-day stop can close them, so the first day's result counts."""
    curve = result["curve"]
    days = [row["day"] for row in curve]
    equity = np.array([row["equity"] for row in curve], dtype=float)
    inside = [index for index, day in enumerate(days) if start <= day <= end]
    if not inside:
        return [], np.array([]), np.array([])
    first = inside[0]
    base = equity[first - 1] if first > 0 else float(result["initial_cash"])
    values = equity[inside]
    returns = values / np.concatenate([[base], values[:-1]]) - 1
    exposure = np.array([curve[index]["invested"] / curve[index]["equity"] for index in inside])
    return [days[index] for index in inside], returns, exposure


def summarize(result: dict, start: str, end: str) -> dict:
    days, returns, exposure = _returns(result, start, end)
    if not days:
        return {"days": 0}
    growth = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.concatenate([[1.0], growth]))[1:]
    drawdown = float((growth / peaks - 1).min())
    years = max((date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1, 1) / 365.25
    total = float(growth[-1] - 1)
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    downside = returns[returns < 0]
    downside_dev = float(np.sqrt(np.mean(np.minimum(returns, 0) ** 2))) if len(returns) else 0.0
    closed = [row for row in result["closed"] if start <= row["exit_day"] <= end]
    trade_returns = np.array([row["return"] for row in closed], dtype=float)
    traded = sum(t["quantity"] * t["price"] for t in result["trades"] if start <= t["day"] <= end)
    mean_equity = float(np.mean([row["equity"] for row in result["curve"] if start <= row["day"] <= end]))
    return {
        "start": days[0], "end": days[-1], "days": len(days),
        "total_return_pct": round(total * 100, 2),
        "cagr_pct": round(((1 + total) ** (1 / years) - 1) * 100, 2) if total > -1 else -100.0,
        "mdd_pct": round(drawdown * 100, 2),
        "sharpe": round(float(returns.mean()) / std * math.sqrt(TRADING_DAYS), 2) if std > 0 else None,
        "sortino": round(float(returns.mean()) / downside_dev * math.sqrt(TRADING_DAYS), 2) if downside_dev > 0 else None,
        "annual_turnover": round(traded / 2 / mean_equity * TRADING_DAYS / len(days), 2) if mean_equity else None,
        "exposure_pct": round(float(exposure.mean()) * 100, 1),
        "trades": len(closed),
        "hit_rate_pct": round(float((trade_returns > 0).mean()) * 100, 1) if len(closed) else None,
        "avg_trade_pct": round(float(trade_returns.mean()) * 100, 2) if len(closed) else None,
        "median_trade_pct": round(float(np.median(trade_returns)) * 100, 2) if len(closed) else None,
        "loss_days": int(len(downside)),
    }


def block_bootstrap(returns: np.ndarray, statistic, *, block: int = 10, samples: int = 2000, seed: int = 7) -> tuple:
    """Moving-block bootstrap 95% interval of a statistic of daily returns."""
    n = len(returns)
    if n < 2 * block:
        return (None, None)
    rng = np.random.default_rng(seed)
    starts_max = n - block + 1
    values = []
    for _ in range(samples):
        starts = rng.integers(0, starts_max, size=math.ceil(n / block))
        sample = np.concatenate([returns[s:s + block] for s in starts])[:n]
        values.append(statistic(sample))
    values = np.array([v for v in values if v is not None and math.isfinite(v)])
    return (round(float(np.percentile(values, 2.5)), 3), round(float(np.percentile(values, 97.5)), 3))


def annualized(returns: np.ndarray) -> float:
    return float(np.prod(1 + returns) ** (TRADING_DAYS / len(returns)) - 1) * 100


def sharpe(returns: np.ndarray) -> float | None:
    std = returns.std(ddof=1)
    return float(returns.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else None


def intervals(result: dict, start: str, end: str, benchmark: dict | None = None, **kwargs) -> dict:
    """95% block-bootstrap intervals of annualized return and Sharpe, and of the annualized
    daily excess over the benchmark when one is given (paired on the same days)."""
    days, returns, _ = _returns(result, start, end)
    out = {"annualized_return_pct_ci": block_bootstrap(returns, annualized, **kwargs),
           "sharpe_ci": block_bootstrap(returns, sharpe, **kwargs)}
    if benchmark is not None:
        bench_days, bench, _ = _returns(benchmark, start, end)
        if bench_days == days:
            out["excess_annualized_pp_ci"] = block_bootstrap(returns - bench,
                                                             lambda r: float(r.mean()) * TRADING_DAYS * 100, **kwargs)
            out["excess_annualized_pp"] = round(float((returns - bench).mean()) * TRADING_DAYS * 100, 2)
    return out


def trade_bootstrap(trade_returns: list[float], *, samples: int = 5000, seed: int = 11) -> dict:
    """Mean trade return and hit rate with 95% intervals from resampling trades."""
    values = np.array(trade_returns, dtype=float)
    if len(values) < 2:
        return {"n": len(values)}
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(samples, len(values)), replace=True)
    means, hits = draws.mean(axis=1) * 100, (draws > 0).mean(axis=1) * 100
    return {"n": len(values), "mean_pct": round(float(values.mean()) * 100, 2),
            "mean_pct_ci": (round(float(np.percentile(means, 2.5)), 2), round(float(np.percentile(means, 97.5)), 2)),
            "hit_rate_pct": round(float((values > 0).mean()) * 100, 1),
            "hit_rate_pct_ci": (round(float(np.percentile(hits, 2.5)), 1), round(float(np.percentile(hits, 97.5)), 1))}
