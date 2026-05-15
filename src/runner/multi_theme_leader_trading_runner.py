from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.risk_manager import InvestmentAction
from src.config.settings import get_data_dir
from src.runner.theme_leader_trading_runner import ThemeLeaderTradingRunner

KST = timezone(timedelta(hours=9))
RISK_ORDER = {
    "VERY_LOW": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "VERY_HIGH": 4,
}


class MultiThemeLeaderTradingRunner:
    """Run all themes, rank leaders globally, and route selected leaders to preview/execute."""

    def __init__(
        self,
        *,
        config_path: str = "config/watchlist.yaml",
        data_dir: Optional[str] = None,
        dry_run_override: Optional[bool] = None,
        trading_enabled_override: Optional[bool] = None,
        account_type_override: Optional[str] = None,
        theme_runner: Optional[ThemeLeaderTradingRunner] = None,
    ):
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()
        self._theme_runner = theme_runner or ThemeLeaderTradingRunner(
            config_path=config_path,
            data_dir=str(self._data_dir),
            dry_run_override=dry_run_override,
            trading_enabled_override=trading_enabled_override,
            account_type_override=account_type_override,
        )

    def run_all(
        self,
        *,
        candidate_limit: int = 5,
        per_theme_top_n: int = 3,
        top_n: int = 5,
        execute: bool = False,
        min_leader_score: Optional[int] = None,
        min_confidence: Optional[int] = None,
        max_risk_level: Optional[str] = None,
        buy_only: bool = True,
        strategy_profile: str = "default",
        include_theme_keys: Optional[Sequence[str]] = None,
        exclude_theme_keys: Optional[Sequence[str]] = None,
        save_report: bool = True,
    ) -> Dict[str, Any]:
        resolved_profile = self._normalize_strategy_profile(strategy_profile)
        themes = self._resolve_themes(include_theme_keys=include_theme_keys, exclude_theme_keys=exclude_theme_keys)

        per_theme_results: List[Dict[str, Any]] = []
        all_rows: List[Dict[str, Any]] = []

        for theme in themes:
            result = self._theme_runner.run_once(
                theme=theme["theme_name"],
                theme_key=theme["theme_key"],
                candidate_limit=candidate_limit,
                top_n=per_theme_top_n,
                execute_top_n=0,
                execute=False,
                min_leader_score=None,
                strategy_profile=resolved_profile,
                save_report=False,
            )
            per_theme_results.append(
                {
                    "theme": result.get("theme", theme["theme_name"]),
                    "theme_key": result.get("theme_key", theme["theme_key"]),
                    "status": result.get("status"),
                    "candidate_count": int(result.get("candidate_count", 0) or 0),
                    "evaluated_count": int(result.get("evaluated_count", 0) or 0),
                    "leaders": list(result.get("leaders") or []),
                }
            )

            for rank, leader in enumerate(result.get("leaders") or [], start=1):
                all_rows.append(
                    {
                        "theme": result.get("theme", theme["theme_name"]),
                        "theme_key": result.get("theme_key", theme["theme_key"]),
                        "theme_rank": rank,
                        "leader": leader,
                    }
                )

        scored = self._rank_globally(
            all_rows,
            min_leader_score=min_leader_score,
            min_confidence=min_confidence,
            max_risk_level=max_risk_level,
            buy_only=buy_only,
        )

        selected = scored[: max(0, top_n)]
        trade_results = self._run_selected(selected=selected, execute=execute)

        best_theme = selected[0]["theme"] if selected else None
        best_leader_stocks = [
            {
                "theme": row["theme"],
                "theme_key": row["theme_key"],
                "stock_name": row["stock_name"],
                "stock_code": row["stock_code"],
                "leader_score": row["leader_score"],
                "confidence": row["confidence"],
                "risk_level": row["risk_level"],
                "normalized_rank_score": row["normalized_rank_score"],
            }
            for row in selected
        ]

        result = {
            "status": "success",
            "mode": "execute" if execute else "preview",
            "executed_at": datetime.now(KST).isoformat(),
            "runtime": self._theme_runner._executor.get_runtime_config(),
            "strategy_profile": resolved_profile,
            "theme_count": len(themes),
            "leader_count": len(all_rows),
            "selected_count": len(selected),
            "thresholds": {
                "min_leader_score": int(min_leader_score) if min_leader_score is not None else None,
                "min_confidence": int(min_confidence) if min_confidence is not None else None,
                "max_risk_level": str(max_risk_level).upper() if max_risk_level is not None else None,
                "buy_only": bool(buy_only),
            },
            "weights": {
                "leader_score": 0.5,
                "confidence": 0.3,
                "risk": 0.2,
            },
            "best_theme": best_theme,
            "best_leader_stocks": best_leader_stocks,
            "themes": per_theme_results,
            "global_ranked_leaders": scored,
            "trade_results": trade_results,
            "summary": self._summarize_rows(trade_results),
        }

        if save_report:
            result["report_path"] = str(self._save_report(result))
        return result

    def get_holdings(self) -> List[Any]:
        return self._theme_runner.get_holdings()

    def get_current_price(self, stock_code: str) -> Optional[int]:
        return self._theme_runner.get_current_price(stock_code)

    @staticmethod
    def _normalize_strategy_profile(strategy_profile: str) -> str:
        profile = str(strategy_profile or "default").strip().lower()
        if profile in {"short", "long"}:
            return profile
        return "default"

    def _resolve_themes(
        self,
        *,
        include_theme_keys: Optional[Sequence[str]],
        exclude_theme_keys: Optional[Sequence[str]],
    ) -> List[Dict[str, str]]:
        include = {str(item).strip() for item in (include_theme_keys or []) if str(item).strip()}
        exclude = {str(item).strip() for item in (exclude_theme_keys or []) if str(item).strip()}

        theme_dir = self._data_dir / "raw" / "theme_targets"
        rows: List[Dict[str, str]] = []

        for path in sorted(theme_dir.glob("*.meta.json")):
            try:
                with path.open("r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue

            theme_key = str(meta.get("theme_key") or path.name.replace(".meta.json", "")).strip()
            theme_name = str(meta.get("theme_name") or theme_key).strip()
            if not theme_key:
                continue
            if include and theme_key not in include:
                continue
            if theme_key in exclude:
                continue
            rows.append({"theme_key": theme_key, "theme_name": theme_name})

        if rows:
            return sorted(rows, key=lambda row: (row["theme_key"], row["theme_name"]))

        for path in sorted(theme_dir.glob("*.jsonl")):
            if path.name.endswith(".meta.json"):
                continue
            theme_key = path.stem.strip()
            if not theme_key:
                continue
            if include and theme_key not in include:
                continue
            if theme_key in exclude:
                continue
            rows.append({"theme_key": theme_key, "theme_name": theme_key})

        return sorted(rows, key=lambda row: (row["theme_key"], row["theme_name"]))

    def _rank_globally(
        self,
        rows: List[Dict[str, Any]],
        *,
        min_leader_score: Optional[int],
        min_confidence: Optional[int],
        max_risk_level: Optional[str],
        buy_only: bool,
    ) -> List[Dict[str, Any]]:
        parsed: List[Dict[str, Any]] = []
        max_risk_rank = self._risk_rank(max_risk_level) if max_risk_level is not None else None

        for row in rows:
            leader = row.get("leader") or {}
            candidate = leader.get("candidate") or {}
            final_decision = leader.get("final_decision") or {}

            leader_score = self._to_int(leader.get("leader_score"), default=0)
            confidence = self._to_int(final_decision.get("confidence"), default=0)
            action_code, action_label = self._action_pair(final_decision)
            risk_code, risk_label = self._risk_pair(final_decision)
            risk_rank = self._risk_rank(risk_code)

            stock_name = str(candidate.get("stock_name") or "").strip()
            stock_code = str(candidate.get("stock_code") or "").strip()

            blocked_reasons: List[str] = []
            if min_leader_score is not None and leader_score < min_leader_score:
                blocked_reasons.append(f"leader_score_below_minimum:{leader_score}<{min_leader_score}")
            if min_confidence is not None and confidence < min_confidence:
                blocked_reasons.append(f"confidence_below_minimum:{confidence}<{min_confidence}")
            if max_risk_rank is not None and risk_rank > max_risk_rank:
                blocked_reasons.append(f"risk_above_maximum:{risk_code}>{max_risk_level}")
            if buy_only and action_code not in {InvestmentAction.BUY.name, InvestmentAction.STRONG_BUY.name}:
                blocked_reasons.append(f"action_not_buy:{action_code}")
            if not stock_name or not stock_code:
                blocked_reasons.append("missing_stock_identity")

            parsed.append(
                {
                    "theme": row.get("theme"),
                    "theme_key": row.get("theme_key"),
                    "theme_rank": int(row.get("theme_rank") or 0),
                    "stock_name": stock_name,
                    "stock_code": stock_code,
                    "leader_score": leader_score,
                    "confidence": confidence,
                    "action": action_label,
                    "action_code": action_code,
                    "risk_level": risk_label,
                    "risk_level_code": risk_code,
                    "risk_rank": risk_rank,
                    "leader": leader,
                    "eligible": len(blocked_reasons) == 0,
                    "blocked_reasons": blocked_reasons,
                }
            )

        eligible = [row for row in parsed if row["eligible"]]
        score_norm = self._minmax_map([row["leader_score"] for row in eligible])
        conf_norm = self._minmax_map([row["confidence"] for row in eligible])
        risk_inv_norm = self._minmax_inverse_map([row["risk_rank"] for row in eligible])

        for row in eligible:
            score_value = score_norm.get(row["leader_score"], 0.0)
            conf_value = conf_norm.get(row["confidence"], 0.0)
            risk_value = risk_inv_norm.get(row["risk_rank"], 0.0)
            rank_score = round(score_value * 0.5 + conf_value * 0.3 + risk_value * 0.2, 6)
            row["normalized_components"] = {
                "leader_score": score_value,
                "confidence": conf_value,
                "risk": risk_value,
            }
            row["normalized_rank_score"] = rank_score

        ranked = sorted(
            eligible,
            key=lambda row: (
                row["normalized_rank_score"],
                row["leader_score"],
                row["confidence"],
                -row["risk_rank"],
                str(row["theme_key"] or ""),
                str(row["stock_code"] or ""),
            ),
            reverse=True,
        )

        for idx, row in enumerate(ranked, start=1):
            row["global_rank"] = idx

        return [
            {
                "global_rank": int(row["global_rank"]),
                "theme": row["theme"],
                "theme_key": row["theme_key"],
                "theme_rank": int(row["theme_rank"]),
                "stock_name": row["stock_name"],
                "stock_code": row["stock_code"],
                "leader_score": int(row["leader_score"]),
                "confidence": int(row["confidence"]),
                "action": row["action"],
                "action_code": row["action_code"],
                "risk_level": row["risk_level"],
                "risk_level_code": row["risk_level_code"],
                "normalized_components": row.get("normalized_components") or {
                    "leader_score": 0.0,
                    "confidence": 0.0,
                    "risk": 0.0,
                },
                "normalized_rank_score": float(row.get("normalized_rank_score") or 0.0),
                "blocked_reasons": list(row["blocked_reasons"]),
                "eligible": bool(row["eligible"]),
                "leader": row["leader"],
            }
            for row in ranked
        ]

    def _run_selected(self, *, selected: List[Dict[str, Any]], execute: bool) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in selected:
            preview_or_trade = self._theme_runner._preview_or_execute_leader(
                leader=row.get("leader") or {},
                rank=int(row.get("global_rank") or 0),
                execute=execute,
                min_leader_score=None,
            )
            rows.append(
                {
                    "global_rank": int(row.get("global_rank") or 0),
                    "theme": row.get("theme"),
                    "theme_key": row.get("theme_key"),
                    **preview_or_trade,
                }
            )

        return sorted(
            rows,
            key=lambda item: (
                int(item.get("global_rank") or 0),
                str(item.get("theme_key") or ""),
                str(item.get("stock_code") or ""),
            ),
        )

    @staticmethod
    def _to_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _action_pair(payload: Dict[str, Any]) -> Tuple[str, str]:
        code = str(payload.get("action_code") or "").strip().upper()
        label = str(payload.get("action") or "").strip()
        if code:
            return code, label or code

        for action in InvestmentAction:
            if action.value == label:
                return action.name, label
        return "HOLD", label or "HOLD"

    @staticmethod
    def _risk_pair(payload: Dict[str, Any]) -> Tuple[str, str]:
        code = str(payload.get("risk_level_code") or "").strip().upper()
        label = str(payload.get("risk_level") or "").strip()
        if code:
            return code, label or code

        for key in RISK_ORDER:
            if label == key:
                return key, label
        return "MEDIUM", label or "MEDIUM"

    @staticmethod
    def _risk_rank(code: str) -> int:
        return RISK_ORDER.get(str(code or "").upper(), RISK_ORDER["MEDIUM"])

    @staticmethod
    def _minmax_map(values: List[int]) -> Dict[int, float]:
        if not values:
            return {}
        low = min(values)
        high = max(values)
        if low == high:
            return {low: 1.0}
        return {value: round((value - low) / (high - low), 6) for value in set(values)}

    @staticmethod
    def _minmax_inverse_map(values: List[int]) -> Dict[int, float]:
        if not values:
            return {}
        low = min(values)
        high = max(values)
        if low == high:
            return {low: 1.0}
        return {value: round((high - value) / (high - low), 6) for value in set(values)}

    @staticmethod
    def _summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            summary[status] = summary.get(status, 0) + 1
        return dict(sorted(summary.items(), key=lambda item: item[0]))

    def _save_report(self, result: Dict[str, Any]) -> Path:
        reports_dir = self._data_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        mode = str(result.get("mode") or "preview")
        path = reports_dir / f"multi_theme_leader_trading_{mode}_{stamp}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        return path
