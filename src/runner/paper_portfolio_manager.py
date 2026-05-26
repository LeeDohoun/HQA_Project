from __future__ import annotations

from typing import Any, Dict, List, Optional


class PaperPortfolioManager:
    """Convert validated LLM position proposals into concrete order intents."""

    def __init__(self, config: Dict[str, Any]):
        self._config = dict(config or {})
        self._portfolio_config = dict(self._config.get("portfolio") or {})

    def build_order_intents(
        self,
        llm_decision: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        *,
        current_prices: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        current_prices = current_prices or {}
        orders: List[Dict[str, Any]] = []
        total_budget = self._to_int(self._portfolio_config.get("total_budget"), default=0)
        cash_buffer = self._to_float(self._portfolio_config.get("cash_buffer_ratio"), default=0.0)
        max_position_ratio = self._to_float(self._portfolio_config.get("max_position_ratio"), default=1.0)
        investable_budget = max(0, int(total_budget * (1.0 - cash_buffer)))
        used_value = self._to_float(portfolio_state.get("total_position_value"), default=0.0)
        remaining_budget = max(0, int(investable_budget - used_value))

        for position in list(llm_decision.get("positions") or []):
            action = str(position.get("action") or "").upper()
            if action == "STRONG_BUY":
                action = "BUY"
            elif action == "STRONG_SELL":
                action = "SELL"
            if action not in {"BUY", "SELL", "HOLD"}:
                continue
            stock_code = str(position.get("stock_code") or "").strip()
            current_price = current_prices.get(stock_code)
            target_weight = max(0.0, self._to_float(position.get("target_weight"), default=0.0))
            capped_weight = min(target_weight, max_position_ratio)
            order_amount = min(int(total_budget * capped_weight), remaining_budget)
            quantity = (order_amount // current_price) if current_price and current_price > 0 else 0
            if action == "SELL":
                existing = self._find_position(portfolio_state, stock_code)
                quantity = self._to_int((existing or {}).get("quantity"), default=0)
                order_amount = quantity * (current_price or 0)

            intent = {
                "theme_key": position.get("theme_key"),
                "theme": position.get("theme"),
                "stock_code": stock_code,
                "stock_name": position.get("stock_name"),
                "side": action,
                "target_weight": capped_weight,
                "requested_target_weight": target_weight,
                "order_amount": order_amount,
                "quantity": quantity,
                "current_price": current_price,
                "confidence": self._to_int(position.get("confidence"), default=0),
                "reason": position.get("reason") or "LLM selected position",
                "invalidation": position.get("invalidation"),
                "llm_position": position,
            }
            orders.append(intent)
            if action == "BUY":
                remaining_budget = max(0, remaining_budget - order_amount)

        return {
            "orders": orders,
            "cash_weight": self._to_float(llm_decision.get("cash_weight"), default=cash_buffer),
        }

    @staticmethod
    def _find_position(portfolio_state: Dict[str, Any], stock_code: str) -> Optional[Dict[str, Any]]:
        for position in list(portfolio_state.get("positions") or []):
            if str(position.get("stock_code") or "").strip() == stock_code:
                return position
        return None

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
