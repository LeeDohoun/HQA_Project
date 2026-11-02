"""Point-in-time fear/greed sizing for the historical backtest.

Reuses the production `src/runner/fear_greed.py` and `src/runner/market_regime.py`
so a backtest measures the shipped formula rather than a reimplementation.

Removability, which the whole comparison depends on:
  - `fg_sensitivity=0` and `regime_sensitivity=0` reproduce equal weighting exactly.
  - The market coefficient collapses to 1.0 when VKOSPI is missing or disabled,
    so a run without `volatility_index.jsonl` equals a run without the feature.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.runner.fear_greed import fear_greed, position_multiplier
from src.runner.market_regime import market_regime, regime_coefficient

KST = timezone(timedelta(hours=9))


def _as_of_datetime(as_of_ymd: str) -> datetime:
    """Trading-day close in KST: the point in time the decision is made."""
    day = datetime.strptime(str(as_of_ymd), "%Y%m%d")
    return day.replace(hour=15, minute=30, tzinfo=KST).astimezone(timezone.utc)


def price_history_at(df: pd.DataFrame, as_of_ymd: str, sessions: int = 400) -> List[Dict[str, float]]:
    """Completed bars up to as_of, in the shape fear_greed expects."""
    ymd = df["YMD"] if "YMD" in df else pd.Series(df.index.strftime("%Y%m%d"), index=df.index)
    known = df.loc[ymd <= str(as_of_ymd)].tail(sessions)
    return [{"close": float(row.Close), "volume": float(row.Volume)}
            for row in known.itertuples()]


class BacktestFearGreed:
    """Computes per-period tilts from archived point-in-time inputs only."""

    def __init__(self, data_dir: str | Path, *, fg_sensitivity: float = 0.0,
                 regime_sensitivity: float = 0.0, use_social: bool = True,
                 use_short_interest: bool = True, use_market_regime: bool = True):
        for name, value in (("fg_sensitivity", fg_sensitivity),
                            ("regime_sensitivity", regime_sensitivity)):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        self.data_dir = Path(data_dir)
        self.fg_sensitivity = float(fg_sensitivity)
        self.regime_sensitivity = float(regime_sensitivity)
        self.use_social = bool(use_social)
        self.use_short_interest = bool(use_short_interest)
        self.use_market_regime = bool(use_market_regime)
        self._forum: Optional[Dict[str, List[dict]]] = None
        self._short: Optional[Dict[str, List[dict]]] = None
        self._vkospi: Optional[List[dict]] = None
        self.warnings: List[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.fg_sensitivity or self.regime_sensitivity)

    # ---- archived inputs, loaded once ----

    def _forum_posts(self, theme_key: str) -> Dict[str, List[dict]]:
        if self._forum is not None:
            return self._forum
        self._forum = {}
        if not self.use_social:
            return self._forum
        from src.runner.analysis_data import read_jsonl
        from src.runner.theme_universe_loader import ThemeUniverseLoader
        index = self.data_dir / "canonical_index" / theme_key
        path = index / "documents.jsonl"
        if not path.exists():
            path = index / "corpus.jsonl"
        if not path.exists():
            self.warnings.append(f"fear_greed_social_unavailable:{theme_key}")
            return self._forum
        seen: set[str] = set()
        for row in read_jsonl(path):
            outer = row.get("metadata") or {}
            meta = {**(outer.get("metadata") or {}), **outer}
            if (row.get("source_type") or meta.get("source_type")) != "forum":
                continue
            code = ThemeUniverseLoader._stock_code(row)
            identity = meta.get("doc_id") or meta.get("url")
            published = row.get("published_at") or meta.get("published_at")
            if not code or not identity or not published or identity in seen:
                continue
            seen.add(identity)
            self._forum.setdefault(code, []).append({"published_at": published})
        return self._forum

    def _short_sale(self) -> Dict[str, List[dict]]:
        if self._short is not None:
            return self._short
        self._short = {}
        if not self.use_short_interest:
            return self._short
        path = self.data_dir / "market_context" / "short_sale.jsonl"
        if not path.exists():
            self.warnings.append("fear_greed_short_interest_unavailable")
            return self._short
        import json
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            self._short.setdefault(row["stock_code"], []).append(row)
        for rows in self._short.values():
            rows.sort(key=lambda row: row["trade_date"])
        return self._short

    def _volatility(self) -> List[dict]:
        if self._vkospi is not None:
            return self._vkospi
        self._vkospi = []
        if not (self.use_market_regime and self.regime_sensitivity):
            return self._vkospi
        path = self.data_dir / "market_context" / "volatility_index.jsonl"
        if not path.exists():
            self.warnings.append("market_regime_unavailable")
            return self._vkospi
        import json
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self._vkospi = sorted(rows, key=lambda row: row["trade_date"])
        return self._vkospi

    # ---- per-period computation ----

    def regime_at(self, as_of_ymd: str) -> dict:
        """Market regime from VKOSPI observed at or before as_of."""
        if not (self.use_market_regime and self.regime_sensitivity):
            return {"status": "disabled"}
        as_of = _as_of_datetime(as_of_ymd)
        history = [row for row in self._volatility()
                   if datetime.fromisoformat(row["available_at"].replace("Z", "+00:00")) <= as_of]
        if not history:
            return {"status": "insufficient_history", "sessions": 0}
        return market_regime(history, as_of)

    def index_for(self, *, stock_code: str, theme_key: str, df: pd.DataFrame,
                  as_of_ymd: str) -> dict:
        as_of = _as_of_datetime(as_of_ymd)
        history = price_history_at(df, as_of_ymd)
        posts = [post for post in self._forum_posts(theme_key).get(stock_code, [])]
        shorts = [row for row in self._short_sale().get(stock_code, [])
                  if datetime.fromisoformat(row["available_at"].replace("Z", "+00:00")) <= as_of]
        try:
            return fear_greed(history, as_of, forum_posts=posts, short_sale=shorts)
        except Exception as exc:  # A scoring failure must not abort the backtest.
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    def weights(self, selected: List[Dict[str, Any]], *, theme_key: str,
                prices: Dict[str, pd.DataFrame], as_of_ymd: str) -> Dict[str, Any]:
        """Normalized portfolio weights plus the audit detail behind them.

        With both sensitivities at 0 every multiplier is 1.0, so the result is
        exactly equal weighting and the run reproduces the no-feature baseline.
        """
        count = len(selected)
        if not count:
            return {"weights": [], "regime": {"status": "no_positions"}, "details": []}
        regime = self.regime_at(as_of_ymd)
        coefficient = regime_coefficient(regime, self.regime_sensitivity)

        multipliers, details = [], []
        for row in selected:
            code = row["stock_code"]
            index = ({"status": "disabled"} if not self.fg_sensitivity
                     else self.index_for(stock_code=code, theme_key=theme_key,
                                         df=prices[code], as_of_ymd=as_of_ymd))
            stock_multiplier = position_multiplier(index, self.fg_sensitivity)
            combined = stock_multiplier * coefficient
            multipliers.append(combined)
            details.append({"stock_code": code, "status": index.get("status"),
                            "score": index.get("score"), "label": index.get("label"),
                            "components": index.get("components"),
                            "stock_multiplier": round(stock_multiplier, 6),
                            "market_coefficient": coefficient,
                            "combined_multiplier": round(combined, 6)})

        # Relative weights come from the per-stock tilt only. Normalizing would
        # cancel a factor common to every position, so the market coefficient
        # instead scales total invested exposure, with the remainder held in cash.
        stock_multipliers = [detail["stock_multiplier"] for detail in details]
        total = sum(stock_multipliers)
        if total <= 0:
            weights = [1.0 / count] * count
        else:
            weights = [value / total for value in stock_multipliers]
        invested = min(1.0, max(0.0, coefficient))
        weights = [weight * invested for weight in weights]
        for detail, weight in zip(details, weights):
            detail["weight"] = round(weight, 6)
        return {"weights": weights, "regime": regime, "details": details,
                "market_coefficient": coefficient, "invested_fraction": round(invested, 6),
                "cash_fraction": round(1.0 - invested, 6)}

    def metadata(self) -> Dict[str, Any]:
        from src.runner.fear_greed import FEAR_GREED_VERSION
        from src.runner.market_regime import MARKET_REGIME_VERSION
        return {"enabled": self.enabled,
                "fear_greed_version": FEAR_GREED_VERSION,
                "market_regime_version": MARKET_REGIME_VERSION,
                "fg_sensitivity": self.fg_sensitivity,
                "regime_sensitivity": self.regime_sensitivity,
                "use_social": self.use_social,
                "use_short_interest": self.use_short_interest,
                "use_market_regime": self.use_market_regime,
                "warnings": sorted(set(self.warnings))}


def demo() -> None:
    """Self-check: equal weights when disabled, tilted and normalized when on."""
    import numpy as np

    dates = pd.date_range("2025-01-01", periods=400, freq="D")
    frames = {}
    for code, drift in (("000001", 1.0), ("000002", -1.0)):
        closes = 10000 + np.arange(400) * drift * 10
        frames[code] = pd.DataFrame({"Close": closes, "Volume": 1000 + np.arange(400) % 50},
                                    index=dates)
    selected = [{"stock_code": "000001"}, {"stock_code": "000002"}]

    off = BacktestFearGreed("/nonexistent", fg_sensitivity=0.0, regime_sensitivity=0.0)
    result = off.weights(selected, theme_key="t", prices=frames, as_of_ymd="20251231")
    assert result["weights"] == [0.5, 0.5], result["weights"]
    assert result["market_coefficient"] == 1.0
    assert not off.enabled

    on = BacktestFearGreed("/nonexistent", fg_sensitivity=0.3, regime_sensitivity=0.0)
    tilted = on.weights(selected, theme_key="t", prices=frames, as_of_ymd="20251231")
    assert abs(sum(tilted["weights"]) - 1.0) < 1e-9
    # The falling stock reads as fear and receives the larger weight.
    assert tilted["weights"][1] > tilted["weights"][0], tilted["weights"]

    # A missing VKOSPI file leaves the coefficient neutral rather than failing.
    regime_on = BacktestFearGreed("/nonexistent", fg_sensitivity=0.0, regime_sensitivity=0.2)
    neutral = regime_on.weights(selected, theme_key="t", prices=frames, as_of_ymd="20251231")
    assert neutral["market_coefficient"] == 1.0
    assert neutral["weights"] == [0.5, 0.5]

    for bad in (-0.1, 1.5, float("nan")):
        try:
            BacktestFearGreed("/tmp", fg_sensitivity=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"sensitivity {bad} must be rejected")

    print("backtest fear_greed demo OK",
          {"off": result["weights"], "on": [round(w, 4) for w in tilted["weights"]]})


if __name__ == "__main__":
    demo()
