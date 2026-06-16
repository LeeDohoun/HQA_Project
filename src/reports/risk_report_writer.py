from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List


def render_risk_report_markdown(decision: Dict[str, Any]) -> str:
    data = deepcopy(decision)
    stock_name = str(data.get("stock_name") or data.get("stockName") or "-")
    stock_code = str(data.get("stock_code") or data.get("stockCode") or "-")
    lines = [
        f"# {stock_name} ({stock_code}) RiskManager 리포트",
        "",
        f"- 판단: {data.get('action_code') or data.get('action') or '-'}",
        f"- 확신도: {data.get('confidence', '-')}",
        f"- 리스크: {data.get('risk_level_code') or data.get('risk_level') or '-'}",
        f"- 제안 비중: {data.get('position_size') or data.get('positionSize') or '-'}",
        "",
        "## 요약",
        str(data.get("summary") or "-"),
        "",
        "## Trade Plan",
        _json_block(data.get("trade_plan") or data.get("tradePlanJson") or {}),
        "",
        "## Entry Conditions",
        *_condition_lines(data.get("entry_conditions")),
        "",
        "## Exit Conditions",
        *_condition_lines(data.get("exit_conditions")),
        "",
        "## Reduce Conditions",
        *_condition_lines(data.get("reduce_conditions")),
        "",
        "## Invalidation Conditions",
        *_condition_lines(data.get("invalidation_conditions")),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _condition_lines(conditions: Any) -> List[str]:
    if not isinstance(conditions, Iterable) or isinstance(conditions, (str, bytes, dict)):
        return ["- 없음"]
    lines = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        description = condition.get("description")
        if description:
            lines.append(f"- {description}")
        else:
            lines.append(f"- `{json.dumps(condition, ensure_ascii=False, sort_keys=True)}`")
    return lines or ["- 없음"]


def _json_block(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "- 없음"
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"
