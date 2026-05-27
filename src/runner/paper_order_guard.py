from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

KST = timezone(timedelta(hours=9))


class PaperOrderGuard:
    """Final code-level safety gate before an LLM decision can reach TradeExecutor."""

    def __init__(self, config: Dict[str, Any]):
        self._config = dict(config or {})
        self._trading_config = dict(self._config.get("theme_trading") or self._config)
        self._portfolio_config = dict(self._trading_config.get("portfolio") or {})
        self._llm_config = dict(self._trading_config.get("llm_decision") or {})
        self._guard_config = dict(self._trading_config.get("order_guard") or {})
        self._schedule_config = dict(self._trading_config.get("schedule") or {})
        self._last_order_time: Dict[str, datetime] = {}

    def validate(
        self,
        intent: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        *,
        llm_error: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        side = str(intent.get("side") or "").upper()
        stock_code = str(intent.get("stock_code") or "").strip()
        total_budget = self._to_float(self._portfolio_config.get("total_budget"), default=0.0)
        existing = self._find_position(portfolio_state, stock_code)

        if not self._trading_config.get("enabled", False):
            return self._blocked("trading_disabled", "theme_trading.enabled is false")

        if llm_error and self._guard_bool("block_if_llm_error", True):
            return self._blocked("llm_error", llm_error)

        if side not in {"BUY", "SELL", "HOLD"}:
            return self._blocked("invalid_side", f"unsupported side={side}")

        if side == "HOLD":
            return self._blocked("hold_intent", "HOLD intents are not executable orders")

        min_confidence = self._to_int(self._llm_config.get("min_confidence_buy"), default=65)
        confidence = self._to_int(intent.get("confidence"), default=0)
        if side == "BUY" and confidence < min_confidence:
            return self._blocked("low_confidence", f"confidence {confidence} < {min_confidence}")

        current_price = self._to_float(intent.get("current_price"), default=0.0)
        if current_price <= 0 and self._guard_bool("block_if_price_missing", True):
            return self._blocked("missing_current_price", "current_price is missing or non-positive")

        quantity = self._to_int(intent.get("quantity"), default=0)
        if quantity <= 0 and self._guard_bool("block_if_quantity_zero", True):
            return self._blocked("quantity_zero", "order quantity is zero")

        allow_scale_in = bool(self._portfolio_config.get("allow_scale_in", False))
        if side == "BUY" and existing and not allow_scale_in and self._guard_bool("block_duplicate_position", True):
            return self._blocked("duplicate_position", "allow_scale_in=false and stock already held")

        max_positions = self._to_int(self._portfolio_config.get("max_positions"), default=0)
        positions_count = self._to_int(portfolio_state.get("positions_count"), default=len(portfolio_state.get("positions") or []))
        if side == "BUY" and not existing and max_positions > 0 and positions_count >= max_positions:
            return self._blocked("max_positions", f"positions_count {positions_count} >= {max_positions}")

        order_amount = self._to_float(intent.get("order_amount"), default=0.0)
        max_position_ratio = self._to_float(self._portfolio_config.get("max_position_ratio"), default=1.0)
        if side == "BUY" and total_budget > 0 and max_position_ratio > 0:
            if order_amount / total_budget > max_position_ratio + 1e-9:
                return self._blocked("max_position_ratio", "order amount exceeds max_position_ratio")

        max_theme_ratio = self._to_float(self._portfolio_config.get("max_theme_ratio"), default=1.0)
        if side == "BUY" and total_budget > 0 and max_theme_ratio > 0:
            theme_key = str(intent.get("theme_key") or "").strip()
            theme_values = dict(portfolio_state.get("theme_values") or {})
            projected = self._to_float(theme_values.get(theme_key), default=0.0) + order_amount
            if projected / total_budget > max_theme_ratio + 1e-9:
                return self._blocked("max_theme_ratio", "projected theme exposure exceeds max_theme_ratio")

        now = now or datetime.now(KST)
        cooldown_minutes = self._to_int(self._guard_config.get("cooldown_minutes"), default=0)
        previous = self._last_order_time.get(stock_code)
        if cooldown_minutes > 0 and previous is not None:
            elapsed = (now - previous).total_seconds() / 60.0
            if elapsed < cooldown_minutes:
                return self._blocked("cooldown", f"last order {elapsed:.1f} minutes ago")

        if self._schedule_bool("market_hours_only", False) and not self._is_market_hours(now):
            return self._blocked("outside_market_hours", "market_hours_only=true and now is outside KRX regular hours")

        return {"allowed": True, "reason": "ok", "detail": "order passed guard"}

    def record_order(self, stock_code: str, *, now: Optional[datetime] = None) -> None:
        stock_code = str(stock_code or "").strip()
        if stock_code:
            self._last_order_time[stock_code] = now or datetime.now(KST)

    @staticmethod
    def _find_position(portfolio_state: Dict[str, Any], stock_code: str) -> Optional[Dict[str, Any]]:
        for position in list(portfolio_state.get("positions") or []):
            if str(position.get("stock_code") or "").strip() == stock_code:
                return position
        return None

    @staticmethod
    def _is_market_hours(now: datetime) -> bool:
        local = now.astimezone(KST)
        if local.weekday() >= 5:
            return False
        return time(9, 0) <= local.time() <= time(15, 30)

    @staticmethod
    def _blocked(reason: str, detail: str) -> Dict[str, Any]:
        return {"allowed": False, "reason": reason, "detail": detail}

    def _guard_bool(self, key: str, default: bool) -> bool:
        return self._to_bool(self._guard_config.get(key, default))

    def _schedule_bool(self, key: str, default: bool) -> bool:
        return self._to_bool(self._schedule_config.get(key, default))

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _to_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
