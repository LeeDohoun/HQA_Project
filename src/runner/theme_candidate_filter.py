from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class ThemeCandidateFilter:
    """First-pass deterministic filter for a loaded theme universe."""

    def __init__(self, filters: Dict[str, Any]):
        self._filters = dict(filters or {})

    def filter_theme(self, universe: Dict[str, Any]) -> Dict[str, Any]:
        passed: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        for candidate in universe.get("candidates") or []:
            features, feature_errors = self._compute_features(candidate.get("price_history") or [])
            reasons = self._rejection_reasons(candidate, features, feature_errors)
            row = {
                "theme": universe.get("theme"),
                "theme_key": universe.get("theme_key"),
                "stock_code": candidate.get("stock_code"),
                "stock_name": candidate.get("stock_name"),
                "features": features,
                "source_counts": dict(candidate.get("source_counts") or {}),
                "data_flags": dict(candidate.get("data_flags") or {}),
                "candidate": candidate,
            }
            if reasons:
                rejected.append(
                    {
                        "theme": universe.get("theme"),
                        "theme_key": universe.get("theme_key"),
                        "stock_code": candidate.get("stock_code"),
                        "stock_name": candidate.get("stock_name"),
                        "reason": reasons[0],
                        "reasons": reasons,
                        "features": features,
                    }
                )
            else:
                passed.append(row)

        return {
            "theme": universe.get("theme"),
            "theme_key": universe.get("theme_key"),
            "status": "filtered",
            "passed": passed,
            "rejected": rejected,
            "passed_count": len(passed),
            "rejected_count": len(rejected),
        }

    def _rejection_reasons(
        self,
        candidate: Dict[str, Any],
        features: Dict[str, Any],
        feature_errors: List[str],
    ) -> List[str]:
        reasons: List[str] = list(feature_errors)
        flags = candidate.get("data_flags") or {}
        history_days = int(features.get("history_days") or 0)

        if self._bool("require_price_history", True) and not flags.get("has_price_history"):
            reasons.append("missing_price_history")

        min_history = self._to_int(self._filters.get("min_history_days"), default=0)
        if min_history > 0 and history_days < min_history:
            reasons.append(f"insufficient_price_history:{history_days}<{min_history}")

        min_value = self._to_float(self._filters.get("min_avg_trading_value_20d"))
        avg_value = self._to_float(features.get("avg_trading_value_20d"))
        if min_value is not None:
            if avg_value is None:
                reasons.append("missing_avg_trading_value_20d")
            elif avg_value < min_value:
                reasons.append("low_liquidity")

        max_vol = self._to_float(self._filters.get("max_volatility_20d"))
        volatility = self._to_float(features.get("volatility_20d"))
        if max_vol is not None:
            if volatility is None:
                reasons.append("missing_volatility_20d")
            elif volatility > max_vol:
                reasons.append("high_volatility")

        max_return_5d = self._to_float(self._filters.get("max_return_5d"))
        return_5d = self._to_float(features.get("return_5d"))
        if max_return_5d is not None:
            if return_5d is None:
                reasons.append("missing_return_5d")
            elif return_5d > max_return_5d:
                reasons.append("overheated_return_5d")

        max_return_20d = self._to_float(self._filters.get("max_return_20d"))
        return_20d = self._to_float(features.get("return_20d"))
        if max_return_20d is not None:
            if return_20d is None:
                reasons.append("missing_return_20d")
            elif return_20d > max_return_20d:
                reasons.append("overheated_return_20d")

        min_trend_150d = self._to_float(self._filters.get("min_trend_150d"))
        trend_150d = self._to_float(features.get("trend_150d"))
        if min_trend_150d is not None:
            if trend_150d is None:
                reasons.append("missing_trend_150d")
            elif trend_150d < min_trend_150d:
                reasons.append("weak_trend_150d")

        if self._bool("require_recent_documents", False) and not flags.get("has_recent_documents"):
            reasons.append("missing_recent_documents")

        return self._dedupe(reasons)

    def _compute_features(self, price_history: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
        rows = self._sort_price_rows(price_history)
        closes: List[float] = []
        volumes: List[float] = []
        trading_values: List[float] = []

        for row in rows:
            close = self._number(row.get("close") or row.get("stck_clpr") or row.get("price"))
            volume = self._number(row.get("volume") or row.get("acml_vol") or row.get("거래량"))
            trading_value = self._number(
                row.get("trading_value")
                or row.get("amount")
                or row.get("acc_trdval")
                or row.get("acml_tr_pbmn")
            )
            if close is None or close <= 0:
                continue
            closes.append(close)
            volumes.append(volume or 0.0)
            trading_values.append(trading_value if trading_value is not None else close * (volume or 0.0))

        features = {
            "history_days": len(closes),
            "current_price": int(closes[-1]) if closes else None,
            "return_5d": self._period_return(closes, 5),
            "return_20d": self._period_return(closes, 20),
            "return_60d": self._period_return(closes, 60),
            "trend_150d": self._period_return(closes, 150),
            "volume_ratio_20d": self._volume_ratio(volumes),
            "volatility_20d": self._volatility(closes, 20),
            "avg_trading_value_20d": self._average(trading_values[-20:]) if trading_values else None,
            "latest_timestamp": rows[-1].get("timestamp") if rows else None,
        }
        errors: List[str] = []
        if price_history and not closes:
            errors.append("invalid_price_history")
        return features, errors

    @staticmethod
    def _sort_price_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def key(row: Dict[str, Any]) -> Tuple[float, str]:
            raw = str(row.get("timestamp") or row.get("date") or row.get("stck_bsop_date") or "")
            normalized = raw.replace(".", "-")
            if len(normalized) == 8 and normalized.isdigit():
                normalized = f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
            try:
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is not None:
                    return parsed.timestamp(), raw
                return parsed.replace(tzinfo=None).timestamp(), raw
            except Exception:
                return datetime.min.timestamp(), raw

        return sorted(rows, key=key)

    @classmethod
    def _period_return(cls, closes: List[float], days: int) -> Optional[float]:
        if len(closes) <= days:
            return None
        base = closes[-days - 1]
        if base <= 0:
            return None
        return round((closes[-1] / base) - 1.0, 6)

    @classmethod
    def _volatility(cls, closes: List[float], days: int) -> Optional[float]:
        if len(closes) <= days:
            return None
        returns = []
        tail = closes[-days - 1 :]
        for previous, current in zip(tail, tail[1:]):
            if previous > 0:
                returns.append((current / previous) - 1.0)
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return round(math.sqrt(variance), 6)

    @classmethod
    def _volume_ratio(cls, volumes: List[float]) -> Optional[float]:
        if len(volumes) < 20:
            return None
        recent = cls._average(volumes[-5:])
        baseline = cls._average(volumes[-20:])
        if recent is None or baseline is None or baseline <= 0:
            return None
        return round(recent / baseline, 6)

    @staticmethod
    def _average(values: List[float]) -> Optional[float]:
        clean = [float(value) for value in values if value is not None]
        if not clean:
            return None
        return round(sum(clean) / len(clean), 6)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            text = str(value).replace(",", "").strip()
            if not text:
                return None
            return float(text)
        except (TypeError, ValueError):
            return None

    def _bool(self, key: str, default: bool) -> bool:
        value = self._filters.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result
