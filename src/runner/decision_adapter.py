from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.agents.risk_manager import FinalDecision, InvestmentAction, RiskLevel


def _coerce_enum(value: Any, enum_cls, default):
    raw = str(value or "").strip()
    if not raw:
        return default

    direct = enum_cls.__members__.get(raw)
    if direct is not None:
        return direct

    upper = enum_cls.__members__.get(raw.upper())
    if upper is not None:
        return upper

    for member in enum_cls:
        if member.value == raw:
            return member

    return default


def _bounded_int(value: Any, *, default: int, low: int = 0, high: int = 100) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _list_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def build_final_decision_from_payload(
    stock_name: str,
    stock_code: str,
    payload: Dict[str, Any] | Any,
) -> FinalDecision:
    """Convert a theme/API decision payload into the executable decision object."""
    if hasattr(payload, "model_dump"):
        data = payload.model_dump()
    elif isinstance(payload, dict):
        data = dict(payload)
    else:
        data = {}

    action = _coerce_enum(
        data.get("action_code") or data.get("action"),
        InvestmentAction,
        InvestmentAction.HOLD,
    )
    risk_level = _coerce_enum(
        data.get("risk_level_code") or data.get("risk_level"),
        RiskLevel,
        RiskLevel.MEDIUM,
    )

    return FinalDecision(
        stock_name=stock_name,
        stock_code=stock_code,
        total_score=_bounded_int(data.get("total_score"), default=50),
        action=action,
        confidence=_bounded_int(data.get("confidence"), default=50),
        risk_level=risk_level,
        risk_factors=_list_value(data.get("risk_factors"))[:5],
        position_size=str(data.get("position_size") or "0%"),
        entry_strategy=str(data.get("entry_strategy") or ""),
        exit_strategy=str(data.get("exit_strategy") or ""),
        stop_loss=str(data.get("stop_loss") or ""),
        signal_alignment=str(data.get("signal_alignment") or ""),
        key_catalysts=_list_value(data.get("key_catalysts"))[:5],
        contrarian_view=str(data.get("contrarian_view") or ""),
        summary=str(data.get("summary") or ""),
        detailed_reasoning=str(data.get("detailed_reasoning") or ""),
        validation_status=str(data.get("validation_status") or "disabled"),
        validation_summary=str(data.get("validation_summary") or ""),
        validator_model=str(data.get("validator_model") or ""),
        primary_model=str(data.get("primary_model") or ""),
        validator_action=str(data.get("validator_action") or ""),
        validator_confidence=_bounded_int(
            data.get("validator_confidence"),
            default=0,
        ),
    )


def final_decision_to_payload(decision: FinalDecision) -> Dict[str, Any]:
    """Return the complete stable payload needed by API and paper trading paths."""
    return {
        "total_score": decision.total_score,
        "action": decision.action.value,
        "action_code": decision.action.name,
        "confidence": decision.confidence,
        "risk_level": decision.risk_level.value,
        "risk_level_code": decision.risk_level.name,
        "risk_factors": list(decision.risk_factors),
        "position_size": decision.position_size,
        "entry_strategy": decision.entry_strategy,
        "exit_strategy": decision.exit_strategy,
        "stop_loss": decision.stop_loss,
        "signal_alignment": decision.signal_alignment,
        "key_catalysts": list(decision.key_catalysts),
        "contrarian_view": decision.contrarian_view,
        "summary": decision.summary,
        "detailed_reasoning": decision.detailed_reasoning,
        "validation_status": decision.validation_status,
        "validation_summary": decision.validation_summary,
        "validator_model": decision.validator_model,
        "primary_model": decision.primary_model,
        "validator_action": decision.validator_action,
        "validator_confidence": decision.validator_confidence,
        "timestamp": decision.timestamp,
    }
