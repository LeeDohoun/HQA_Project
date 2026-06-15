from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMThemeDecisionEngine:
    """Ask an LLM to choose themes, leaders, weights, and invalidation rules."""

    def __init__(self, config: Dict[str, Any], *, llm_client: Optional[Any] = None):
        self._config = dict(config or {})
        self._llm_client = llm_client

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        llm_config = dict(self._config.get("llm_decision") or {})
        if not llm_config.get("enabled", True):
            return {
                "status": "disabled",
                "llm_error": "llm_decision_disabled",
                "decision": self._empty_decision(),
            }

        prompt = self._build_prompt(payload)
        try:
            raw_payload = self._invoke(prompt)
            decision = self._parse_response(raw_payload)
            normalized = self._normalize_decision(decision)
            return {
                "status": "success",
                "llm_error": None,
                "decision": normalized,
                "raw_response": raw_payload if isinstance(raw_payload, dict) else str(raw_payload),
            }
        except Exception as exc:
            logger.warning("LLM theme decision failed: %s", exc)
            return {
                "status": "error",
                "llm_error": str(exc),
                "decision": self._empty_decision(),
            }

    def _invoke(self, prompt: str) -> Any:
        client = self._llm_client
        if client is None:
            from src.agents.llm_config import get_risk_manager_llm

            client = get_risk_manager_llm()
            self._llm_client = client

        if callable(client) and not hasattr(client, "invoke"):
            return client(prompt)

        response = client.invoke(prompt)
        return getattr(response, "content", response)

    def _parse_response(self, raw_payload: Any) -> Dict[str, Any]:
        if isinstance(raw_payload, dict):
            return raw_payload
        text = str(raw_payload or "").strip()
        if not text:
            raise ValueError("empty_llm_response")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"llm_json_parse_error:{exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("llm_response_not_object")
        return payload

    def _normalize_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        llm_config = dict(self._config.get("llm_decision") or {})
        portfolio_config = dict(self._config.get("portfolio") or {})
        top_themes = self._to_int(llm_config.get("top_themes"), default=3)
        max_positions = self._to_int(portfolio_config.get("max_positions"), default=5)
        min_confidence_buy = self._to_int(llm_config.get("min_confidence_buy"), default=65)
        require_reason = bool(llm_config.get("require_reason", True))
        require_invalidation = bool(llm_config.get("require_invalidation", True))

        selected_themes = [
            self._normalize_theme(row)
            for row in list(decision.get("selected_themes") or [])[: max(0, top_themes)]
            if isinstance(row, dict)
        ]
        excluded_themes = [
            self._normalize_excluded_theme(row)
            for row in list(decision.get("excluded_themes") or [])
            if isinstance(row, dict)
        ]

        positions: List[Dict[str, Any]] = []
        watch: List[Dict[str, Any]] = []
        reject: List[Dict[str, Any]] = []
        for row in list(decision.get("positions") or []):
            if not isinstance(row, dict):
                continue
            position = self._normalize_position(row)
            errors = []
            if position["action"] == "BUY":
                if position["confidence"] < min_confidence_buy:
                    errors.append(f"confidence_below_min_buy:{position['confidence']}<{min_confidence_buy}")
                if require_reason and not position.get("reason"):
                    errors.append("missing_buy_reason")
                if require_invalidation and not position.get("invalidation"):
                    errors.append("missing_buy_invalidation")
            if errors:
                position["validation_errors"] = errors
                position["action"] = "WATCH"
                watch.append(position)
                continue
            positions.append(position)
            if len(positions) >= max(0, max_positions):
                break

        for row in list(decision.get("watch") or []):
            if isinstance(row, dict):
                watch.append(self._normalize_position({**row, "action": "WATCH"}))
        for row in list(decision.get("reject") or []):
            if isinstance(row, dict):
                reject.append(self._normalize_position({**row, "action": "REJECT"}))

        return {
            "market_regime": str(decision.get("market_regime") or "unknown"),
            "selected_themes": selected_themes,
            "excluded_themes": excluded_themes,
            "positions": positions,
            "watch": watch,
            "reject": reject,
            "cash_weight": self._to_float(
                decision.get("cash_weight"),
                default=float(portfolio_config.get("cash_buffer_ratio", 0.0) or 0.0),
            ),
        }

    @staticmethod
    def _normalize_theme(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "theme_key": str(row.get("theme_key") or "").strip(),
            "theme": str(row.get("theme") or "").strip(),
            "weight": LLMThemeDecisionEngine._to_float(row.get("weight"), default=0.0),
            "reason": str(row.get("reason") or "").strip(),
        }

    @staticmethod
    def _normalize_excluded_theme(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "theme_key": str(row.get("theme_key") or "").strip(),
            "theme": str(row.get("theme") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
        }

    @staticmethod
    def _normalize_position(row: Dict[str, Any]) -> Dict[str, Any]:
        action = str(row.get("action") or "WATCH").strip().upper()
        if action == "STRONG_BUY":
            action = "BUY"
        elif action == "STRONG_SELL":
            action = "SELL"
        return {
            "theme_key": str(row.get("theme_key") or "").strip(),
            "theme": str(row.get("theme") or "").strip(),
            "stock_code": str(row.get("stock_code") or "").strip(),
            "stock_name": str(row.get("stock_name") or "").strip(),
            "action": action,
            "target_weight": LLMThemeDecisionEngine._to_float(row.get("target_weight"), default=0.0),
            "confidence": LLMThemeDecisionEngine._to_int(row.get("confidence"), default=0),
            "reason": str(row.get("reason") or "").strip(),
            "invalidation": str(row.get("invalidation") or "").strip(),
        }

    def _build_prompt(self, payload: Dict[str, Any]) -> str:
        compact_payload = json.dumps(payload, ensure_ascii=False, default=str)
        return (
            "너는 한국 주식 multi-theme 모의투자 계좌의 투자위원회다.\n"
            "정량 feature, 문서 근거, 포트폴리오 상태를 참고해 이번 실행 주기에서 투자할 테마와 종목을 선택한다.\n"
            "판단 기준: 테마 직접성, 테마 내 주도주 여부, 가격/거래대금 주도성, 뉴스/DART 촉매의 질, "
            "단기 과열 위험, 유동성, 보유 포지션과의 중복, 포트폴리오 비중, 매수 논리의 명확성, "
            "무효화 조건의 명확성.\n"
            "반드시 JSON만 출력한다. BUY에는 reason, invalidation, confidence, target_weight가 필수다. "
            "모르면 BUY하지 말고 WATCH로 둔다.\n"
            "필수 JSON 키: market_regime, selected_themes, excluded_themes, positions, watch, reject, cash_weight.\n"
            f"입력 데이터:\n{compact_payload}"
        )

    @staticmethod
    def _empty_decision() -> Dict[str, Any]:
        return {
            "market_regime": "unknown",
            "selected_themes": [],
            "excluded_themes": [],
            "positions": [],
            "watch": [],
            "reject": [],
            "cash_weight": 1.0,
        }

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
