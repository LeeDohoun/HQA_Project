"""Deterministic one-way and round-trip trading costs, expressed as fractions."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np


# Combined securities transaction tax; KOSPI includes the rural special tax.
# Extend this table each year before using that year's data.
SELL_TAX = {
    2023: {"KOSPI": 0.0020, "KOSDAQ": 0.0020},
    2024: {"KOSPI": 0.0018, "KOSDAQ": 0.0018},
    2025: {"KOSPI": 0.0015, "KOSDAQ": 0.0015},
    2026: {"KOSPI": 0.0020, "KOSDAQ": 0.0020},
}

# KRX unified stock tick rule effective 2023-01-25 (KOSPI and KOSDAQ).
# Verify against the exchange's quotation price unit table when updating.
TICK_SIZES = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
              (200_000, 100), (500_000, 500), (math.inf, 1_000))
TRADING_VALUE_BUCKETS = (1e8, 1e9, 1e10, math.inf)


def _nonnegative(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.00015
    slippage_rates: tuple[float, ...] = (0.0030, 0.0015, 0.0005, 0.0002)

    def __post_init__(self) -> None:
        _nonnegative(self.commission_rate, "commission_rate")
        if not isinstance(self.slippage_rates, tuple) or len(self.slippage_rates) != 4:
            raise ValueError("slippage_rates must be a tuple of four bucket rates")
        for rate in self.slippage_rates:
            _nonnegative(rate, "slippage rate")


DEFAULT_CONFIG = CostConfig()


def tick_size(price: float) -> int:
    _nonnegative(price, "price")
    if price == 0:
        raise ValueError("price must be positive")
    return next(tick for upper, tick in TICK_SIZES if price < upper)


def one_way_cost(
    price: float,
    market: str,
    trade_date: str | date,
    avg_trading_value_20d: float,
    side: str,
    config: CostConfig = DEFAULT_CONFIG,
    multiplier: float = 1.0,
) -> float:
    """Scale commission, spread and slippage by multiplier; add sell tax unscaled."""
    spread = tick_size(price) / price
    _nonnegative(avg_trading_value_20d, "avg_trading_value_20d")
    _nonnegative(multiplier, "multiplier")
    if market not in ("KOSPI", "KOSDAQ"):
        raise ValueError(f"unknown market: {market}")
    if side not in ("buy", "sell"):
        raise ValueError(f"unknown side: {side}")
    if isinstance(trade_date, str) and len(trade_date) == 8 and trade_date.isdigit():
        trade_date = datetime.strptime(trade_date, "%Y%m%d").date()
    if type(trade_date) is not date:
        raise ValueError("trade_date must be a date or YYYYMMDD string")
    if trade_date.year not in SELL_TAX:
        raise ValueError(f"unknown tax year: {trade_date.year}")
    if not isinstance(config, CostConfig):
        raise ValueError("config must be a CostConfig")
    slippage = next(rate for upper, rate in zip(TRADING_VALUE_BUCKETS, config.slippage_rates)
                    if avg_trading_value_20d < upper)
    tax = SELL_TAX[trade_date.year][market] if side == "sell" else 0.0
    return tax + (config.commission_rate + spread + slippage) * multiplier


def round_trip_cost(
    price: float,
    market: str,
    trade_date: str | date,
    avg_trading_value_20d: float,
    config: CostConfig = DEFAULT_CONFIG,
    multiplier: float = 1.0,
) -> float:
    """Sum buy and sell costs at the same price, date and liquidity, leaving tax unscaled."""
    return sum(one_way_cost(price, market, trade_date, avg_trading_value_20d, side, config, multiplier)
               for side in ("buy", "sell"))


def round_trip_cost_vectorized(
    prices: np.ndarray, markets, years, adv: np.ndarray,
    config: CostConfig = DEFAULT_CONFIG, multiplier: float = 1.0,
) -> np.ndarray:
    """Array equivalent of round_trip_cost; years are integer calendar years."""
    _nonnegative(multiplier, "multiplier")
    if not isinstance(config, CostConfig):
        raise ValueError("config must be a CostConfig")
    prices, markets, years, adv = np.broadcast_arrays(
        np.asarray(prices), np.asarray(markets), np.asarray(years), np.asarray(adv))
    for values, name in ((prices, "price"), (adv, "avg_trading_value_20d")):
        if values.dtype.kind not in "iuf" or not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"{name} must be finite and nonnegative")
    if (prices == 0).any():
        raise ValueError("price must be positive")
    if not np.isin(markets, ("KOSPI", "KOSDAQ")).all():
        raise ValueError("unknown market")
    if years.dtype.kind not in "iu" or not np.isin(years, list(SELL_TAX)).all():
        raise ValueError("unknown tax year")
    bounds, ticks = np.asarray(TICK_SIZES).T
    spread = ticks[np.searchsorted(bounds, prices, side="right")] / prices
    slippage = np.asarray(config.slippage_rates)[np.searchsorted(TRADING_VALUE_BUCKETS, adv, side="right")]
    tax = np.empty(prices.shape, dtype=float)
    for year, rates in SELL_TAX.items():
        for market, rate in rates.items():
            tax[(years == year) & (markets == market)] = rate
    uncertain = (config.commission_rate + spread + slippage) * multiplier
    # Preserve the scalar function's addition order, including its rounding.
    return uncertain + (tax + uncertain)
