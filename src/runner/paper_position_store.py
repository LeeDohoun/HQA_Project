from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.settings import get_data_dir

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class PaperPositionStore:
    """Persist paper positions and audit journals for theme paper trading."""

    def __init__(self, *, data_dir: Optional[str] = None):
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()
        self._base_dir = self._data_dir / "paper_trading"
        self._positions_path = self._base_dir / "positions.json"
        self._decision_journal_path = self._base_dir / "decision_journal.jsonl"
        self._position_snapshots_path = self._base_dir / "position_snapshots.jsonl"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._load_error: Optional[str] = None

    def load_positions(self) -> List[Dict[str, Any]]:
        if not self._positions_path.exists():
            return []
        try:
            with self._positions_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            self._load_error = str(exc)
            logger.warning("Failed to load paper positions: %s", exc)
            return []

        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            rows = payload.get("positions") or []
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return []

    def get_position(self, stock_code: str) -> Optional[Dict[str, Any]]:
        stock_code = str(stock_code or "").strip()
        for position in self.load_positions():
            if str(position.get("stock_code") or "").strip() == stock_code:
                return position
        return None

    def upsert_position(self, order_result: Dict[str, Any], llm_decision: Dict[str, Any]) -> Dict[str, Any]:
        positions = self.load_positions()
        stock_code = str(order_result.get("stock_code") or llm_decision.get("stock_code") or "").strip()
        quantity = self._to_int(order_result.get("quantity"), default=0)
        price = self._to_float(order_result.get("price"), default=0.0)
        now = datetime.now(KST).isoformat()
        existing = None
        for position in positions:
            if str(position.get("stock_code") or "").strip() == stock_code:
                existing = position
                break

        if existing:
            old_qty = self._to_int(existing.get("quantity"), default=0)
            old_price = self._to_float(existing.get("entry_price"), default=0.0)
            new_qty = old_qty + quantity
            if new_qty > 0:
                existing["entry_price"] = round(((old_qty * old_price) + (quantity * price)) / new_qty, 4)
            existing["quantity"] = new_qty
            existing["last_review_time"] = now
            position = existing
        else:
            position = {
                "theme_key": llm_decision.get("theme_key"),
                "theme": llm_decision.get("theme"),
                "stock_code": stock_code,
                "stock_name": order_result.get("stock_name") or llm_decision.get("stock_name"),
                "entry_price": price,
                "quantity": quantity,
                "entry_reason": llm_decision.get("reason"),
                "invalidation": llm_decision.get("invalidation"),
                "entry_confidence": llm_decision.get("confidence"),
                "peak_profit_rate": 0.0,
                "last_review_time": now,
            }
            positions.append(position)

        self._save_positions(positions)
        return position

    def close_position(self, stock_code: str, order_result: Dict[str, Any]) -> bool:
        stock_code = str(stock_code or "").strip()
        positions = [
            position
            for position in self.load_positions()
            if str(position.get("stock_code") or "").strip() != stock_code
        ]
        self._save_positions(positions)
        _ = order_result
        return True

    def update_mark_to_market(self, stock_code: str, current_price: Any) -> Optional[Dict[str, Any]]:
        positions = self.load_positions()
        current = self._to_float(current_price, default=0.0)
        if current <= 0:
            return None
        updated = None
        for position in positions:
            if str(position.get("stock_code") or "").strip() != str(stock_code or "").strip():
                continue
            entry = self._to_float(position.get("entry_price"), default=0.0)
            if entry <= 0:
                continue
            profit_rate = round(((current / entry) - 1.0) * 100.0, 4)
            position["current_price"] = current
            position["profit_rate"] = profit_rate
            position["peak_profit_rate"] = max(
                self._to_float(position.get("peak_profit_rate"), default=profit_rate),
                profit_rate,
            )
            position["last_review_time"] = datetime.now(KST).isoformat()
            updated = position
        if updated:
            self._save_positions(positions)
        return updated

    def get_portfolio_summary(self) -> Dict[str, Any]:
        positions = self.load_positions()
        theme_values: Dict[str, float] = {}
        total_value = 0.0
        for position in positions:
            quantity = self._to_int(position.get("quantity"), default=0)
            price = self._to_float(position.get("current_price"), default=0.0)
            if price <= 0:
                price = self._to_float(position.get("entry_price"), default=0.0)
            value = max(0.0, quantity * price)
            total_value += value
            theme_key = str(position.get("theme_key") or "").strip()
            if theme_key:
                theme_values[theme_key] = theme_values.get(theme_key, 0.0) + value
        return {
            "positions": positions,
            "positions_count": len(positions),
            "total_position_value": total_value,
            "theme_values": theme_values,
            "load_error": self._load_error,
        }

    def append_decision_journal(self, payload: Dict[str, Any]) -> None:
        self._append_jsonl(self._decision_journal_path, payload)

    def append_position_snapshot(self, payload: Dict[str, Any]) -> None:
        self._append_jsonl(self._position_snapshots_path, payload)

    def _save_positions(self, positions: List[Dict[str, Any]]) -> None:
        if self._load_error and self._positions_path.exists():
            raise RuntimeError(f"refusing_to_overwrite_unreadable_positions:{self._load_error}")
        payload = {
            "updated_at": datetime.now(KST).isoformat(),
            "positions": positions,
        }
        tmp_path = self._positions_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        tmp_path.replace(self._positions_path)

    @staticmethod
    def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

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
