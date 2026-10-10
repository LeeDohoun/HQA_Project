"""Market-wide regime coefficient from VKOSPI, applied separately from the per-stock index.

This module is deliberately self-contained so it can be disabled or removed without
touching the per-stock fear/greed index:

  - `HQA_MARKET_REGIME=off` (or a missing VKOSPI file) makes `market_regime` return
    an unavailable status, and `regime_coefficient` then returns exactly 1.0.
  - Deleting this file only breaks its own import site in shared_analysis, which
    already degrades to a neutral coefficient when the module raises.

The coefficient multiplies the per-stock tilt; it is never averaged into the
stock's own-history percentile, because a market-wide value is identical for every
stock and would only dilute cross-sectional signal.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

import pandas as pd

MARKET_REGIME_VERSION = "regime-v1-vkospi-percentile"
LOOKBACK_SESSIONS = 252
MIN_SESSIONS = 120
# A calm market permits slightly larger entries; a panicked one shrinks them.
CALM_BELOW = 25.0
PANIC_ABOVE = 75.0


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market regime timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def regime_enabled() -> bool:
    """False disables the whole feature, including its file read."""
    return os.getenv("HQA_MARKET_REGIME", "on").strip().lower() not in {"off", "0", "false", "no"}


def market_regime(volatility_history: list[dict], as_of: datetime) -> dict:
    """VKOSPI percentile over its own trailing year. High VKOSPI is market fear."""
    as_of = _aware(as_of)
    if not regime_enabled():
        return {"status": "disabled", "version": MARKET_REGIME_VERSION}
    if not isinstance(volatility_history, list) or len(volatility_history) < MIN_SESSIONS:
        return {"status": "insufficient_history", "version": MARKET_REGIME_VERSION,
                "sessions": len(volatility_history) if isinstance(volatility_history, list) else 0,
                "required": MIN_SESSIONS}

    closes = pd.Series([float(row["close"]) for row in volatility_history], dtype="float64")
    window = closes.dropna().tail(LOOKBACK_SESSIONS)
    if len(window) < MIN_SESSIONS:
        return {"status": "insufficient_history", "version": MARKET_REGIME_VERSION,
                "sessions": int(len(window)), "required": MIN_SESSIONS}
    percentile = float(window.rank(pct=True).iloc[-1] * 100)
    if not math.isfinite(percentile):
        return {"status": "undefined_percentile", "version": MARKET_REGIME_VERSION}

    return {
        "status": "ready",
        "version": MARKET_REGIME_VERSION,
        "as_of": as_of.isoformat(),
        "vkospi": round(float(window.iloc[-1]), 4),
        "vkospi_percentile": round(percentile, 1),
        "trade_date": volatility_history[-1]["trade_date"],
        "source_id": volatility_history[-1]["source_id"],
        "label": ("calm" if percentile < CALM_BELOW
                  else "panic" if percentile > PANIC_ABOVE else "normal"),
        "sessions": int(len(window)),
        "interpretation": ("KOSPI200 option-implied volatility as a percentile of its own "
                           "trailing year. Market-wide, identical for every stock; observed "
                           "positioning context, not a forecast."),
    }


def regime_coefficient(regime: dict, sensitivity: float) -> float:
    """Market-wide size coefficient. Returns exactly 1.0 when unavailable or disabled."""
    if not isinstance(sensitivity, (int, float)) or not math.isfinite(float(sensitivity)):
        raise ValueError("market regime sensitivity must be a finite number")
    if not 0.0 <= float(sensitivity) <= 1.0:
        raise ValueError("market regime sensitivity must be between 0 and 1")
    if not isinstance(regime, dict) or regime.get("status") != "ready" or not sensitivity:
        return 1.0
    # High VKOSPI percentile (market fear) scales exposure down, calm scales it up.
    tilt = (50.0 - float(regime["vkospi_percentile"])) / 50.0
    return round(1.0 + tilt * float(sensitivity), 6)


def load_market_regime(data_dir, as_of: datetime) -> dict:
    """Reads the stored VKOSPI history and returns the regime, never raising."""
    if not regime_enabled():
        return {"status": "disabled", "version": MARKET_REGIME_VERSION}
    try:
        from pathlib import Path

        from src.ingestion.krx_volatility import load_volatility_index
        history = load_volatility_index(Path(data_dir) / "market_context" / "volatility_index.jsonl", as_of)
        return market_regime(history, as_of)
    except Exception as exc:
        return {"status": "failed", "version": MARKET_REGIME_VERSION,
                "error": f"{type(exc).__name__}: {exc}"}


def demo() -> None:
    """Self-check: percentile direction, disable switch, neutral fallbacks."""
    base = datetime(2026, 9, 20, tzinfo=timezone.utc)

    def history(values):
        return [{"close": v, "trade_date": f"2026-01-{1 + i % 28:02d}", "source_id": f"krx-vkospi:{i}"}
                for i, v in enumerate(values)]

    calm = market_regime(history(list(range(40, 15, -1)) * 8 + [14.0]), base)
    panic = market_regime(history(list(range(15, 40)) * 8 + [45.0]), base)
    assert calm["status"] == "ready" and panic["status"] == "ready"
    assert calm["vkospi_percentile"] < panic["vkospi_percentile"]

    # Calm market sizes up, panicked market sizes down.
    assert regime_coefficient(calm, 0.2) > 1.0
    assert regime_coefficient(panic, 0.2) < 1.0
    assert regime_coefficient({"status": "ready", "vkospi_percentile": 50.0}, 0.2) == 1.0

    # Every unavailable path is exactly neutral.
    for row in ({"status": "insufficient_history"}, {"status": "failed"},
                {"status": "disabled"}, {}, {"status": "undefined_percentile"}):
        assert regime_coefficient(row, 0.5) == 1.0, row
    assert regime_coefficient(calm, 0.0) == 1.0

    # The kill switch short-circuits before any file read.
    os.environ["HQA_MARKET_REGIME"] = "off"
    try:
        assert market_regime(history([20.0] * 300), base)["status"] == "disabled"
        assert regime_coefficient(market_regime(history([20.0] * 300), base), 0.5) == 1.0
        assert load_market_regime("/nonexistent", base)["status"] == "disabled"
    finally:
        os.environ.pop("HQA_MARKET_REGIME", None)

    # A missing file degrades to unavailable, never to an exception.
    assert load_market_regime("/nonexistent", base)["status"] in {"insufficient_history", "failed"}

    for bad in (-0.1, 1.5, float("nan")):
        try:
            regime_coefficient(calm, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"sensitivity {bad} must be rejected")

    print("market_regime demo OK",
          {"calm_pct": calm["vkospi_percentile"], "panic_pct": panic["vkospi_percentile"],
           "calm_coef": regime_coefficient(calm, 0.2), "panic_coef": regime_coefficient(panic, 0.2)})


if __name__ == "__main__":
    demo()
