from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.agents.risk_manager import InvestmentAction
from src.config.settings import get_data_dir
from src.runner.decision_adapter import build_final_decision_from_payload
from src.runner.trade_executor import TradeExecutor

KST = timezone(timedelta(hours=9))


class ThemeLeaderTradingRunner:
    """Run theme leader discovery and route selected leaders into TradeExecutor."""

    def __init__(
        self,
        *,
        config_path: str = "config/watchlist.yaml",
        data_dir: Optional[str] = None,
        dry_run_override: Optional[bool] = None,
        trading_enabled_override: Optional[bool] = None,
        account_type_override: Optional[str] = None,
        orchestrator: Any = None,
        executor: Optional[TradeExecutor] = None,
    ):
        self._config_path = Path(config_path)
        self._config = self._load_config()
        trading_config = dict(self._config.get("trading") or {})
        if dry_run_override is not None:
            trading_config["dry_run"] = dry_run_override
        if trading_enabled_override is not None:
            trading_config["enabled"] = trading_enabled_override
        if account_type_override is not None:
            trading_config["account_type"] = account_type_override
        self._config["trading"] = trading_config
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()
        self._orchestrator = orchestrator
        self._executor = executor or TradeExecutor(trading_config)

    def _load_config(self) -> Dict[str, Any]:
        if not self._config_path.exists():
            return {
                "schedule": {"enabled": False},
                "watchlist": [],
                "trading": {"enabled": False, "dry_run": True, "account_type": "paper"},
            }
        with self._config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def run_once(
        self,
        *,
        theme: str,
        theme_key: str = "",
        candidate_limit: int = 5,
        top_n: int = 3,
        execute_top_n: int = 1,
        execute: bool = False,
        min_leader_score: Optional[int] = None,
        strategy_profile: str = "default",
        save_report: bool = True,
    ) -> Dict[str, Any]:
        orchestrator = self._get_orchestrator()
        theme_result = orchestrator.run(
            theme=theme,
            theme_key=theme_key,
            candidate_limit=candidate_limit,
            top_n=top_n,
            strategy_profile=strategy_profile,
        )

        leaders = list(theme_result.get("leaders") or [])
        selected = leaders[: max(0, execute_top_n)]
        trade_results = []

        for rank, leader in enumerate(selected, start=1):
            trade_results.append(
                self._preview_or_execute_leader(
                    leader=leader,
                    rank=rank,
                    execute=execute,
                    min_leader_score=min_leader_score,
                )
            )

        result = {
            "status": "success" if theme_result.get("status") == "success" else "error",
            "mode": "execute" if execute else "preview",
            "theme": theme_result.get("theme", theme),
            "theme_key": theme_result.get("theme_key", theme_key),
            "strategy_profile": theme_result.get("strategy_profile", strategy_profile),
            "candidate_count": theme_result.get("candidate_count", 0),
            "evaluated_count": theme_result.get("evaluated_count", 0),
            "selected_count": len(selected),
            "executed_at": datetime.now(KST).isoformat(),
            "runtime": self._executor.get_runtime_config(),
            "leaders": leaders,
            "trade_results": trade_results,
            "summary": self._summarize_trade_results(trade_results),
        }

        if save_report:
            result["report_path"] = str(self._save_report(result))
        return result

    def run_from_report(
        self,
        *,
        report_path: str,
        execute_top_n: Optional[int] = None,
        execute: bool = False,
        save_report: bool = True,
    ) -> Dict[str, Any]:
        """Route a saved preview report into the same preview/execute path without re-running LLMs."""
        with Path(report_path).open("r", encoding="utf-8") as f:
            source = json.load(f)

        ready_rows = [
            row
            for row in source.get("trade_results", [])
            if str(row.get("status") or "") == "ready"
        ]
        if execute_top_n is not None:
            ready_rows = ready_rows[: max(0, execute_top_n)]

        leaders_by_rank = {
            idx: leader for idx, leader in enumerate(source.get("leaders") or [], start=1)
        }
        trade_results = []
        for row in ready_rows:
            rank = int(row.get("rank") or 0)
            leader = leaders_by_rank.get(rank)
            if not leader:
                trade_results.append(
                    {
                        "rank": rank,
                        "stock_name": row.get("stock_name"),
                        "stock_code": row.get("stock_code"),
                        "status": "blocked",
                        "reason": "missing_leader_in_report",
                        "mode": "execute" if execute else "preview",
                    }
                )
                continue
            trade_results.append(
                self._preview_or_execute_leader(
                    leader=leader,
                    rank=rank,
                    execute=execute,
                    min_leader_score=None,
                )
            )

        result = {
            "status": "success",
            "mode": "execute_from_report" if execute else "preview_from_report",
            "source_report_path": str(report_path),
            "theme": source.get("theme"),
            "theme_key": source.get("theme_key"),
            "selected_count": len(ready_rows),
            "executed_at": datetime.now(KST).isoformat(),
            "runtime": self._executor.get_runtime_config(),
            "trade_results": trade_results,
            "summary": self._summarize_trade_results(trade_results),
        }
        if save_report:
            result["report_path"] = str(self._save_report(result))
        return result

    def _get_orchestrator(self):
        if self._orchestrator is not None:
            return self._orchestrator
        from src.agents import ThemeLeaderOrchestrator

        self._orchestrator = ThemeLeaderOrchestrator(data_dir=str(self._data_dir))
        return self._orchestrator

    def _preview_or_execute_leader(
        self,
        *,
        leader: Dict[str, Any],
        rank: int,
        execute: bool,
        min_leader_score: Optional[int],
    ) -> Dict[str, Any]:
        candidate = leader.get("candidate") or {}
        stock_name = str(candidate.get("stock_name") or "").strip()
        stock_code = str(candidate.get("stock_code") or "").strip()
        leader_score = self._coerce_leader_score(leader.get("leader_score"))
        decision_payload = leader.get("final_decision") or {}

        base = {
            "rank": rank,
            "stock_name": stock_name,
            "stock_code": stock_code,
            "leader_score": leader_score if leader_score is not None else leader.get("leader_score"),
            "mode": "execute" if execute else "preview",
        }

        if not stock_name or not stock_code:
            return {**base, "status": "blocked", "reason": "missing_stock_identity"}

        if leader_score is None:
            return {**base, "status": "blocked", "reason": "invalid_leader_score"}

        if min_leader_score is not None and leader_score < min_leader_score:
            return {
                **base,
                "status": "blocked",
                "reason": f"leader_score_below_minimum:{leader_score}<{min_leader_score}",
            }

        decision = build_final_decision_from_payload(
            stock_name=stock_name,
            stock_code=stock_code,
            payload=decision_payload,
        )
        current_price = self._get_current_price(stock_code)

        if self._needs_buy_price(decision) and (current_price is None or current_price <= 0):
            return {
                **base,
                "status": "blocked",
                "reason": "missing_current_price_for_buy",
                "decision": decision_payload,
            }

        quantity = 0
        if decision.action in {InvestmentAction.SELL, InvestmentAction.STRONG_SELL}:
            quantity = self._get_sell_quantity(stock_code)

        if execute:
            trade = self._executor.execute_decision(
                stock_name=stock_name,
                stock_code=stock_code,
                decision=decision,
                quantity=quantity,
                current_price=current_price,
            )
            return {
                **base,
                "status": trade.get("status", "unknown"),
                "price": current_price,
                "decision": decision_payload,
                "trade": trade,
            }

        preview = self._executor.preview_decision(
            stock_name=stock_name,
            stock_code=stock_code,
            decision=decision,
            quantity=quantity,
            current_price=current_price,
        )
        return {
            **base,
            "status": preview.get("status", "unknown"),
            "price": current_price,
            "decision": decision_payload,
            "preview": preview,
        }

    @staticmethod
    def _needs_buy_price(decision) -> bool:
        return decision.action in {InvestmentAction.BUY, InvestmentAction.STRONG_BUY}

    @staticmethod
    def _coerce_leader_score(value: Any) -> Optional[int]:
        if value is None or value == "":
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _get_current_price(self, stock_code: str) -> Optional[int]:
        try:
            tool = self._get_realtime_tool()
            if not tool.is_available:
                return None
            quote = tool.get_current_price(stock_code)
            if quote and hasattr(quote, "current_price"):
                return int(quote.current_price)
            if isinstance(quote, dict):
                return int(quote.get("stck_prpr", 0) or 0)
        except Exception:
            return None
        return None

    def get_current_price(self, stock_code: str) -> Optional[int]:
        return self._get_current_price(stock_code)

    def get_holdings(self) -> List[Any]:
        try:
            tool = self._get_realtime_tool()
            if not tool.is_available:
                return []
            return list(tool.get_holdings() or [])
        except Exception:
            return []

    def _get_sell_quantity(self, stock_code: str) -> int:
        try:
            tool = self._get_realtime_tool()
            if not tool.is_available:
                return 0
            return int(tool.get_holding_quantity(stock_code, orderable=True))
        except Exception:
            return 0

    def _get_realtime_tool(self):
        from src.tools.realtime_tool import KISRealtimeTool

        return KISRealtimeTool(paper=self._is_paper_account())

    def _is_paper_account(self) -> bool:
        trading = self._config.get("trading") or {}
        return trading.get("account_type", "paper") == "paper"

    @staticmethod
    def _summarize_trade_results(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            summary[status] = summary.get(status, 0) + 1
        return summary

    def _save_report(self, result: Dict[str, Any]) -> Path:
        reports_dir = self._data_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        theme_key = str(result.get("theme_key") or result.get("theme") or "theme").replace("/", "_")
        mode = str(result.get("mode") or "preview")
        path = reports_dir / f"{theme_key}_theme_trading_{mode}_{stamp}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        return path
