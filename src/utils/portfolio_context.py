from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

KST = timezone(timedelta(hours=9))


def unavailable_portfolio_context(reason: str) -> Dict[str, Any]:
    return {
        "available": False,
        "source": "kis_balance",
        "reason": str(reason or "unavailable"),
        "captured_at": datetime.now(KST).isoformat(),
        "summary": {
            "holding_count": 0,
            "total_evaluation_amount": None,
            "total_profit_loss": None,
            "available_cash": None,
        },
        "positions_by_code": {},
    }


def build_portfolio_context_from_balance_response(response: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return unavailable_portfolio_context("invalid_balance_response")
    if response.get("rt_cd") != "0":
        return unavailable_portfolio_context(response.get("msg1") or response.get("msg_cd") or "balance_query_failed")

    positions = [_normalize_holding(row) for row in list(response.get("output1") or [])]
    positions = [row for row in positions if row.get("stock_code") and row.get("holding_quantity", 0) > 0]
    positions_by_code = {row["stock_code"]: row for row in positions}
    balance_rows = response.get("output2") or []
    balance = balance_rows[0] if isinstance(balance_rows, list) and balance_rows else balance_rows
    if not isinstance(balance, dict):
        balance = {}

    total_eval = _first_number(balance, ["tot_evlu_amt", "scts_evlu_amt", "evlu_amt_smtl"])
    total_pnl = _first_number(balance, ["evlu_pfls_smtl_amt", "tot_evlu_pfls_amt", "evlu_pfls_amt"])
    available_cash = _first_number(balance, ["dnca_tot_amt", "nxdy_excc_amt", "ord_psbl_cash", "nass_amt"])

    if total_eval is None:
        total_eval = sum(_to_int(row.get("evaluation_amount")) for row in positions)
    if total_pnl is None:
        total_pnl = sum(_to_int(row.get("profit_loss")) for row in positions)

    return {
        "available": True,
        "source": "kis_balance",
        "captured_at": datetime.now(KST).isoformat(),
        "summary": {
            "holding_count": len(positions),
            "total_evaluation_amount": total_eval,
            "total_profit_loss": total_pnl,
            "available_cash": available_cash,
        },
        "positions_by_code": positions_by_code,
    }


def build_portfolio_context_from_holdings(holdings: Iterable[Any]) -> Dict[str, Any]:
    positions = [_normalize_holding(row) for row in list(holdings or [])]
    positions = [row for row in positions if row.get("stock_code") and row.get("holding_quantity", 0) > 0]
    positions_by_code = {row["stock_code"]: row for row in positions}
    return {
        "available": True,
        "source": "holdings",
        "captured_at": datetime.now(KST).isoformat(),
        "summary": {
            "holding_count": len(positions),
            "total_evaluation_amount": sum(_to_int(row.get("evaluation_amount")) for row in positions),
            "total_profit_loss": sum(_to_int(row.get("profit_loss")) for row in positions),
            "available_cash": None,
        },
        "positions_by_code": positions_by_code,
    }


def candidate_portfolio_context(portfolio_context: Optional[Dict[str, Any]], stock_code: str) -> Dict[str, Any]:
    context = dict(portfolio_context or unavailable_portfolio_context("not_provided"))
    positions = dict(context.get("positions_by_code") or {})
    stock_code = str(stock_code or "").strip()
    position = positions.get(stock_code)
    return {
        "available": bool(context.get("available")),
        "source": context.get("source"),
        "reason": context.get("reason"),
        "captured_at": context.get("captured_at"),
        "summary": dict(context.get("summary") or {}),
        "position": position,
        "is_held": bool(position),
    }


def prompt_block_for_portfolio_context(context: Optional[Dict[str, Any]]) -> str:
    if not context:
        return "- portfolio: not provided"
    if not context.get("available"):
        reason = context.get("reason") or "unavailable"
        return f"- portfolio context unavailable: {reason}"

    summary = dict(context.get("summary") or {})
    position = context.get("position")
    lines = [
        "- portfolio context source: " + str(context.get("source") or "unknown"),
        f"- holdings count: {summary.get('holding_count', 0)}",
        f"- total evaluation amount: {_format_optional(summary.get('total_evaluation_amount'))}",
        f"- total profit/loss: {_format_optional(summary.get('total_profit_loss'))}",
        f"- available cash: {_format_optional(summary.get('available_cash'))}",
    ]
    if position:
        lines.extend(
            [
                "- current stock is held: yes",
                f"- held quantity: {position.get('holding_quantity')}",
                f"- orderable quantity: {position.get('orderable_quantity')}",
                f"- average/entry price: {_format_optional(position.get('average_price'))}",
                f"- current price: {_format_optional(position.get('current_price'))}",
                f"- evaluation amount: {_format_optional(position.get('evaluation_amount'))}",
                f"- unrealized profit/loss: {_format_optional(position.get('profit_loss'))}",
                f"- unrealized profit/loss rate: {_format_optional(position.get('profit_loss_rate'))}%",
            ]
        )
    else:
        lines.append("- current stock is held: no")
    return "\n".join(lines)


def _normalize_holding(row: Any) -> Dict[str, Any]:
    return {
        "stock_code": str(_get(row, ["stock_code", "pdno", "PDNO"]) or "").strip(),
        "stock_name": str(_get(row, ["stock_name", "prdt_name", "prdt_abrv_name"]) or "").strip(),
        "holding_quantity": _to_int(_get(row, ["holding_quantity", "hldg_qty"])),
        "orderable_quantity": _to_int(_get(row, ["orderable_quantity", "ord_psbl_qty", "holding_quantity", "hldg_qty"])),
        "average_price": _first_number(row, ["average_price", "pchs_avg_pric", "avg_price"]),
        "current_price": _to_int(_get(row, ["current_price", "prpr"])),
        "evaluation_amount": _to_int(_get(row, ["evaluation_amount", "evlu_amt"])),
        "profit_loss": _to_int(_get(row, ["profit_loss", "evlu_pfls_amt"])),
        "profit_loss_rate": _to_float(_get(row, ["profit_loss_rate", "evlu_pfls_rt"])),
    }


def _get(row: Any, keys: List[str]) -> Any:
    if isinstance(row, dict):
        for key in keys:
            if key in row:
                return row.get(key)
        return None
    for key in keys:
        if hasattr(row, key):
            return getattr(row, key)
    return None


def _first_number(row: Any, keys: List[str]) -> Optional[float]:
    value = _get(row, keys)
    if value in (None, ""):
        return None
    return _to_float(value)


def _to_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _format_optional(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)
