from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from src.data_pipeline.price_loader import PriceLoader


@dataclass(frozen=True)
class SignalQualityConfig:
    enabled: bool
    mode: str
    apply_scopes: List[str]
    penalty_weights: Dict[str, float]
    thresholds: Dict[str, float]
    report_only: bool


DEFAULT_PENALTY_WEIGHTS: Dict[str, float] = {
    "low_trading_value": 8.0,
    "high_volatility": 6.0,
    "weak_return_5d": 4.0,
    "weak_return_20d": 4.0,
    "below_trend_150d": 7.0,
    "weak_breadth": 5.0,
    "missing_data": 6.0,
}

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "min_avg_trading_value_20d": 1_000_000_000.0,
    "max_volatility_20d": 0.045,
    "min_return_5d": -0.02,
    "min_return_20d": -0.05,
    "min_trend_150d": 1.0,
    "min_breadth_ratio": 0.45,
}


def resolve_signal_quality_config(raw: Mapping[str, Any]) -> SignalQualityConfig:
    mode = str(raw.get("mode") or "shadow_penalty").strip().lower()
    scopes = [str(x).strip().lower() for x in (raw.get("apply_scopes") or ["short", "long"]) if str(x).strip()]
    weights = dict(DEFAULT_PENALTY_WEIGHTS)
    weights.update({str(k): float(v) for k, v in (raw.get("penalty_weights") or {}).items()})
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update({str(k): float(v) for k, v in (raw.get("thresholds") or {}).items()})
    return SignalQualityConfig(
        enabled=bool(raw.get("enabled", False)),
        mode=mode,
        apply_scopes=scopes or ["short", "long"],
        penalty_weights=weights,
        thresholds=thresholds,
        report_only=bool(raw.get("report_only", False)),
    )


class SignalQualityFilter:
    def __init__(self, *, data_dir: str, theme_key: Optional[str] = None):
        self._loader = PriceLoader(data_dir=data_dir, theme_key=theme_key)

    def evaluate(
        self,
        *,
        stock_code: str,
        breadth_ratio: Optional[float],
        config: SignalQualityConfig,
    ) -> Dict[str, Any]:
        metrics = self._compute_metrics(stock_code)
        violations: List[str] = []
        penalty = 0.0
        t = config.thresholds
        w = config.penalty_weights

        def _penalize(key: str, violation: str) -> None:
            nonlocal penalty
            violations.append(violation)
            penalty += float(w.get(key, 0.0))

        if metrics["avg_trading_value_20d"] is None:
            _penalize("missing_data", "missing_avg_trading_value_20d")
        elif metrics["avg_trading_value_20d"] < float(t["min_avg_trading_value_20d"]):
            _penalize("low_trading_value", "avg_trading_value_20d_below_min")

        if metrics["volatility_20d"] is None:
            _penalize("missing_data", "missing_volatility_20d")
        elif metrics["volatility_20d"] > float(t["max_volatility_20d"]):
            _penalize("high_volatility", "volatility_20d_above_max")

        if metrics["return_5d"] is None:
            _penalize("missing_data", "missing_return_5d")
        elif metrics["return_5d"] < float(t["min_return_5d"]):
            _penalize("weak_return_5d", "return_5d_below_min")

        if metrics["return_20d"] is None:
            _penalize("missing_data", "missing_return_20d")
        elif metrics["return_20d"] < float(t["min_return_20d"]):
            _penalize("weak_return_20d", "return_20d_below_min")

        if metrics["trend_150d"] is None:
            _penalize("missing_data", "missing_trend_150d")
        elif metrics["trend_150d"] < float(t["min_trend_150d"]):
            _penalize("below_trend_150d", "trend_150d_below_min")

        breadth_state = self._resolve_breadth_state(breadth_ratio, float(t["min_breadth_ratio"]))
        if breadth_state["state"] in {"narrow", "unknown"}:
            _penalize("weak_breadth", f"breadth_{breadth_state['state']}")

        return {
            "violations": violations,
            "penalty": round(penalty, 4),
            "metrics_snapshot": metrics,
            "breadth_state": breadth_state,
        }

    def _compute_metrics(self, stock_code: str) -> Dict[str, Optional[float]]:
        try:
            df = self._loader.get_stock_data(stock_code, days=220)
        except Exception:
            return {
                "avg_trading_value_20d": None,
                "volatility_20d": None,
                "return_5d": None,
                "return_20d": None,
                "trend_150d": None,
            }

        close = df["Close"]
        volume = df["Volume"]
        trading_value = close * volume
        ret_1d = close.pct_change()

        def _safe_value(series, idx: int = -1) -> Optional[float]:
            if series is None or len(series) == 0:
                return None
            value = series.iloc[idx]
            if value is None:
                return None
            try:
                if value != value:
                    return None
                return float(value)
            except Exception:
                return None

        avg_trading_value_20d = _safe_value(trading_value.rolling(20).mean())
        volatility_20d = _safe_value(ret_1d.rolling(20).std())
        return_5d = _safe_value(close.pct_change(5))
        return_20d = _safe_value(close.pct_change(20))
        ma150 = _safe_value(close.rolling(150).mean())
        last_close = _safe_value(close)
        trend_150d = (last_close / ma150) if (last_close and ma150 and ma150 > 0) else None

        return {
            "avg_trading_value_20d": avg_trading_value_20d,
            "volatility_20d": volatility_20d,
            "return_5d": return_5d,
            "return_20d": return_20d,
            "trend_150d": trend_150d,
        }

    @staticmethod
    def _resolve_breadth_state(breadth_ratio: Optional[float], min_breadth_ratio: float) -> Dict[str, Any]:
        if breadth_ratio is None:
            return {"state": "unknown", "ratio": None, "threshold": min_breadth_ratio}
        state = "broad" if float(breadth_ratio) >= float(min_breadth_ratio) else "narrow"
        return {"state": state, "ratio": round(float(breadth_ratio), 6), "threshold": min_breadth_ratio}
