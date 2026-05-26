from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import yaml

from src.config.settings import get_data_dir
from src.runner.llm_theme_decision_engine import LLMThemeDecisionEngine
from src.runner.paper_order_guard import PaperOrderGuard
from src.runner.paper_portfolio_manager import PaperPortfolioManager
from src.runner.paper_position_store import PaperPositionStore
from src.runner.theme_candidate_filter import ThemeCandidateFilter
from src.runner.theme_evidence_builder import ThemeEvidenceBuilder
from src.runner.theme_universe_loader import ThemeUniverseLoader
from src.runner.trade_executor import TradeExecutor

KST = timezone(timedelta(hours=9))


@dataclass
class _PaperDecision:
    total_score: int
    confidence: int
    action: Any
    risk_level: Any


class ThemePaperRunner:
    """Main orchestrator for LLM-centered multi-theme paper trading."""

    def __init__(
        self,
        *,
        config_path: str = "config/theme_trading.yaml",
        data_dir: Optional[str] = None,
        llm_client: Optional[Any] = None,
        executor: Optional[TradeExecutor] = None,
        position_store: Optional[PaperPositionStore] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ):
        self._config_path = Path(config_path)
        self._config = self._load_config()
        self._theme_config = dict(self._config.get("theme_trading") or {})
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()
        self._now_provider = now_provider or (lambda: datetime.now(KST))

        self._loader = ThemeUniverseLoader(data_dir=str(self._data_dir))
        self._filter = ThemeCandidateFilter(dict(self._theme_config.get("universe_filters") or {}))
        self._evidence_builder = ThemeEvidenceBuilder()
        self._decision_engine = LLMThemeDecisionEngine(self._theme_config, llm_client=llm_client)
        self._portfolio_manager = PaperPortfolioManager(self._theme_config)
        self._position_store = position_store or PaperPositionStore(data_dir=str(self._data_dir))
        self._guard = PaperOrderGuard({"theme_trading": self._theme_config})
        self._executor = executor or TradeExecutor(self._trade_executor_config())

    def run_once(self) -> Dict[str, Any]:
        now = self._now_provider().astimezone(KST)
        run_id = f"theme-paper-{now.strftime('%Y%m%d-%H%M%S')}"

        if not self._theme_config.get("enabled", False):
            return {
                "status": "disabled",
                "run_id": run_id,
                "reason": "theme_trading_disabled",
                "orders": [],
            }

        themes = self._enabled_themes()
        loaded_themes: List[Dict[str, Any]] = []
        filtered_themes: List[Dict[str, Any]] = []
        evidence_themes: List[Dict[str, Any]] = []
        journal_events: List[Dict[str, Any]] = []

        for theme_cfg in themes:
            universe = self._loader.load_theme(theme_cfg)
            loaded_themes.append(universe)
            if universe.get("status") == "skipped":
                event = self._journal_base(now, run_id, universe)
                event.update({"event": "theme_skipped", "reason": universe.get("reason")})
                self._position_store.append_decision_journal(event)
                journal_events.append(event)
                continue

            filtered = self._filter.filter_theme(universe)
            filtered_themes.append(filtered)
            filter_event = self._journal_base(now, run_id, universe)
            filter_event.update(
                {
                    "event": "filter_result",
                    "passed_count": filtered.get("passed_count"),
                    "rejected_count": filtered.get("rejected_count"),
                    "rejected": filtered.get("rejected"),
                }
            )
            self._position_store.append_decision_journal(filter_event)
            journal_events.append(filter_event)

            max_cards = int((self._theme_config.get("llm_decision") or {}).get("max_evidence_cards_per_theme", 10) or 10)
            evidence = self._evidence_builder.build_theme_evidence(
                theme=str(theme_cfg.get("theme") or ""),
                theme_key=str(theme_cfg.get("theme_key") or ""),
                filtered_result=filtered,
                max_cards=max_cards,
            )
            evidence_themes.append(evidence)
            evidence_event = self._journal_base(now, run_id, universe)
            evidence_event.update(
                {
                    "event": "evidence_cards",
                    "evidence_card_count": len(evidence.get("evidence_cards") or []),
                    "evidence_cards": evidence.get("evidence_cards") or [],
                }
            )
            self._position_store.append_decision_journal(evidence_event)
            journal_events.append(evidence_event)

        portfolio_state = self._position_store.get_portfolio_summary()
        decision_input = {
            "as_of": now.isoformat(),
            "themes": evidence_themes,
            "portfolio_state": portfolio_state,
            "config": self._safe_decision_config(),
        }
        llm_result = self._decision_engine.decide(decision_input)

        if llm_result.get("status") != "success":
            event = {
                "timestamp": now.isoformat(),
                "run_id": run_id,
                "event": "llm_error",
                "llm_error": llm_result.get("llm_error") or "unknown_llm_error",
            }
            self._position_store.append_decision_journal(event)
            self._append_snapshot(now, run_id, llm_result, [], loaded_themes, filtered_themes)
            return {
                "status": "llm_error",
                "run_id": run_id,
                "llm_error": llm_result.get("llm_error"),
                "orders": [],
                "themes": evidence_themes,
            }

        decision = dict(llm_result.get("decision") or {})
        self._append_llm_decision_rows(now, run_id, decision)
        current_prices = self._current_prices_from_evidence(evidence_themes)
        portfolio_result = self._portfolio_manager.build_order_intents(
            decision,
            portfolio_state,
            current_prices=current_prices,
        )

        order_results: List[Dict[str, Any]] = []
        for intent in portfolio_result.get("orders") or []:
            refreshed_state = self._position_store.get_portfolio_summary()
            guard_result = self._guard.validate(intent, refreshed_state, now=now)
            order_result = None
            if guard_result.get("allowed"):
                order_result = self._execute_intent(intent, decision, guard_result)
                self._guard.record_order(str(intent.get("stock_code") or ""), now=now)
                if self._is_effective_order(order_result):
                    if str(intent.get("side") or "").upper() == "BUY":
                        self._position_store.upsert_position(order_result, intent)
                    elif str(intent.get("side") or "").upper() == "SELL":
                        self._position_store.close_position(str(intent.get("stock_code") or ""), order_result)
            self._append_order_journal(now, run_id, intent, guard_result, order_result)
            order_results.append(
                {
                    "intent": intent,
                    "guard_result": guard_result,
                    "order_result": order_result,
                }
            )

        self._append_snapshot(now, run_id, llm_result, order_results, loaded_themes, filtered_themes)
        return {
            "status": "success",
            "run_id": run_id,
            "as_of": now.isoformat(),
            "theme_count": len(themes),
            "loaded_theme_count": len([row for row in loaded_themes if row.get("status") == "loaded"]),
            "evidence_theme_count": len(evidence_themes),
            "llm_decision": decision,
            "portfolio_result": portfolio_result,
            "orders": order_results,
            "journal_events": len(journal_events),
        }

    def run_loop(self) -> None:
        interval = int((self._theme_config.get("schedule") or {}).get("scan_interval_minutes", 30) or 30)
        sleep_seconds = max(60, interval * 60)
        while True:
            self.run_once()
            time.sleep(sleep_seconds)

    def _execute_intent(
        self,
        intent: Dict[str, Any],
        llm_decision: Dict[str, Any],
        guard_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        side = str(intent.get("side") or "").upper()
        stock_name = str(intent.get("stock_name") or "")
        stock_code = str(intent.get("stock_code") or "")
        decision = self._executor_decision(intent)
        metadata = {
            "theme_key": intent.get("theme_key"),
            "theme": intent.get("theme"),
            "llm_decision": llm_decision,
            "guard_result": guard_result,
            "position_intent": intent,
            "paper_trading_mode": "multi_theme",
        }
        if side == "BUY":
            return self._executor.execute_buy(
                stock_name=stock_name,
                stock_code=stock_code,
                decision=decision,
                current_price=int(intent.get("current_price") or 0) or None,
                amount_override=int(intent.get("order_amount") or 0),
                quantity_override=int(intent.get("quantity") or 0),
                metadata=metadata,
            )
        if side == "SELL":
            return self._executor.execute_sell(
                stock_name=stock_name,
                stock_code=stock_code,
                decision=decision,
                quantity=int(intent.get("quantity") or 0),
                current_price=int(intent.get("current_price") or 0) or None,
                metadata=metadata,
            )
        return {"status": "no_action", "reason": f"unsupported_side:{side}", "dry_run": self._executor.is_dry_run}

    @staticmethod
    def _executor_decision(intent: Dict[str, Any]) -> _PaperDecision:
        side = str(intent.get("side") or "HOLD").upper()
        action_value = {"BUY": "매수", "SELL": "매도", "HOLD": "관망"}.get(side, side)
        return _PaperDecision(
            total_score=int(intent.get("confidence") or 0),
            confidence=int(intent.get("confidence") or 0),
            action=SimpleNamespace(name=side, value=action_value),
            risk_level=SimpleNamespace(name="MEDIUM", value="보통"),
        )

    def _load_config(self) -> Dict[str, Any]:
        if not self._config_path.exists():
            return {"theme_trading": {"enabled": False, "dry_run": True, "account_type": "paper"}}
        with self._config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _trade_executor_config(self) -> Dict[str, Any]:
        portfolio = dict(self._theme_config.get("portfolio") or {})
        guard = dict(self._theme_config.get("order_guard") or {})
        return {
            "enabled": bool(self._theme_config.get("enabled", False)),
            "dry_run": bool(self._theme_config.get("dry_run", True)),
            "account_type": self._theme_config.get("account_type", "paper"),
            "order_type": self._theme_config.get("order_type", "limit"),
            "allow_real_trading": bool(self._theme_config.get("allow_real_trading", False)),
            "max_daily_buy_amount": int(portfolio.get("total_budget", 1_000_000) or 1_000_000),
            "max_position_ratio": float(portfolio.get("max_position_ratio", 0.2) or 0.2),
            "cooldown_minutes": int(guard.get("cooldown_minutes", 30) or 30),
            "auto_buy_conditions": {},
            "auto_sell_conditions": {},
        }

    def _enabled_themes(self) -> List[Dict[str, Any]]:
        return [
            dict(theme)
            for theme in list(self._theme_config.get("themes") or [])
            if isinstance(theme, dict) and theme.get("enabled", True)
        ]

    def _safe_decision_config(self) -> Dict[str, Any]:
        return {
            "llm_decision": self._theme_config.get("llm_decision") or {},
            "portfolio": self._theme_config.get("portfolio") or {},
            "order_guard": self._theme_config.get("order_guard") or {},
        }

    @staticmethod
    def _current_prices_from_evidence(evidence_themes: List[Dict[str, Any]]) -> Dict[str, int]:
        prices: Dict[str, int] = {}
        for theme in evidence_themes:
            for card in list(theme.get("evidence_cards") or []):
                stock_code = str(card.get("stock_code") or "").strip()
                features = card.get("price_features") or {}
                price = features.get("current_price")
                if stock_code and price:
                    prices[stock_code] = int(price)
        return prices

    def _append_llm_decision_rows(self, now: datetime, run_id: str, decision: Dict[str, Any]) -> None:
        for bucket, action_default in (("positions", None), ("watch", "WATCH"), ("reject", "REJECT")):
            for row in list(decision.get(bucket) or []):
                event = {
                    "timestamp": now.isoformat(),
                    "run_id": run_id,
                    "event": "llm_decision",
                    "theme_key": row.get("theme_key"),
                    "theme": row.get("theme"),
                    "stock_code": row.get("stock_code"),
                    "stock_name": row.get("stock_name"),
                    "llm_action": row.get("action") or action_default,
                    "confidence": row.get("confidence"),
                    "reason": row.get("reason"),
                    "invalidation": row.get("invalidation"),
                    "validation_errors": row.get("validation_errors"),
                }
                self._position_store.append_decision_journal(event)

    def _append_order_journal(
        self,
        now: datetime,
        run_id: str,
        intent: Dict[str, Any],
        guard_result: Dict[str, Any],
        order_result: Optional[Dict[str, Any]],
    ) -> None:
        event = {
            "timestamp": now.isoformat(),
            "run_id": run_id,
            "event": "order_result",
            "theme_key": intent.get("theme_key"),
            "theme": intent.get("theme"),
            "stock_code": intent.get("stock_code"),
            "stock_name": intent.get("stock_name"),
            "llm_action": intent.get("side"),
            "executor_action": intent.get("side") if order_result else "NONE",
            "guard_allowed": bool(guard_result.get("allowed")),
            "guard_reason": guard_result.get("reason"),
            "confidence": intent.get("confidence"),
            "reason": intent.get("reason"),
            "invalidation": intent.get("invalidation"),
            "order_status": (order_result or {}).get("status") if order_result else "blocked",
            "order_amount": intent.get("order_amount"),
            "quantity": intent.get("quantity"),
        }
        self._position_store.append_decision_journal(event)

    def _append_snapshot(
        self,
        now: datetime,
        run_id: str,
        llm_result: Dict[str, Any],
        order_results: List[Dict[str, Any]],
        loaded_themes: List[Dict[str, Any]],
        filtered_themes: List[Dict[str, Any]],
    ) -> None:
        self._position_store.append_position_snapshot(
            {
                "timestamp": now.isoformat(),
                "run_id": run_id,
                "portfolio": self._position_store.get_portfolio_summary(),
                "llm_status": llm_result.get("status"),
                "llm_error": llm_result.get("llm_error"),
                "order_count": len(order_results),
                "loaded_theme_count": len(loaded_themes),
                "filtered_theme_count": len(filtered_themes),
            }
        )

    @staticmethod
    def _journal_base(now: datetime, run_id: str, theme_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "timestamp": now.isoformat(),
            "run_id": run_id,
            "theme_key": theme_payload.get("theme_key"),
            "theme": theme_payload.get("theme"),
        }

    @staticmethod
    def _is_effective_order(order_result: Optional[Dict[str, Any]]) -> bool:
        if not order_result:
            return False
        status = str(order_result.get("status") or "").lower()
        return status in {"simulated", "submitted", "filled"}

    def to_json(self, result: Dict[str, Any]) -> str:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
