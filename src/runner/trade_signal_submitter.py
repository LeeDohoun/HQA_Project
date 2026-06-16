from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def build_trade_signal_payloads(
    *,
    user_id: str,
    result: Dict[str, Any],
    source: str = "multi_theme_leader",
    now: Optional[datetime] = None,
    ttl_minutes: int = 15,
) -> List[Dict[str, Any]]:
    if not user_id:
        return []

    base_time = now or datetime.now(KST)
    expires_at = base_time + timedelta(minutes=max(1, int(ttl_minutes)))
    strategy_profile = str(result.get("strategy_profile") or "default")
    payloads: List[Dict[str, Any]] = []

    for row in result.get("global_ranked_leaders") or []:
        if row.get("eligible") is False:
            continue
        leader = dict(row.get("leader") or {})
        decision = dict(leader.get("final_decision") or {})
        action = str(decision.get("action_code") or row.get("action_code") or "").strip().upper()
        if action not in {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL", "REDUCE"}:
            continue
        stock_code = row.get("stock_code")
        idempotency_key = ":".join(
            [
                user_id,
                source,
                strategy_profile,
                str(stock_code or ""),
                action,
                expires_at.isoformat(),
            ]
        )

        payload = {
            "userId": user_id,
            "source": source,
            "strategyProfile": strategy_profile,
            "themeKey": row.get("theme_key"),
            "themeName": row.get("theme"),
            "stockCode": stock_code,
            "stockName": row.get("stock_name"),
            "action": action,
            "leaderScore": int(row.get("leader_score") or 0),
            "confidence": int(decision.get("confidence") or row.get("confidence") or 0),
            "riskLevel": str(decision.get("risk_level_code") or row.get("risk_level_code") or "MEDIUM"),
            "positionSize": str(decision.get("position_size") or "0%"),
            "signalPrice": _signal_price_from_leader(leader, row),
            "stopLoss": str(decision.get("stop_loss") or ""),
            "reason": str(decision.get("summary") or ""),
            "expiresAt": expires_at.isoformat(),
            "tradePlanJson": dict(decision.get("trade_plan") or {}),
            "conditionPayload": {
                "entry_conditions": _condition_list(decision.get("entry_conditions")),
                "exit_conditions": _condition_list(decision.get("exit_conditions")),
                "reduce_conditions": _condition_list(decision.get("reduce_conditions")),
                "invalidation_conditions": _condition_list(decision.get("invalidation_conditions")),
            },
            "idempotencyKey": idempotency_key,
            "rawPayload": {"leader": leader, "rank": row},
        }
        payloads.append(payload)

    return payloads


def _condition_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _signal_price_from_leader(leader: Dict[str, Any], row: Dict[str, Any]) -> Any:
    chartist = leader.get("chartist") if isinstance(leader.get("chartist"), dict) else {}
    snapshot = chartist.get("price_snapshot") if isinstance(chartist.get("price_snapshot"), dict) else {}
    return snapshot.get("current_price") or snapshot.get("currentPrice") or row.get("price")


def submit_trade_signals(
    *,
    user_id: str,
    result: Dict[str, Any],
    backend_signal_url: Optional[str] = None,
    internal_token: Optional[str] = None,
    ttl_minutes: int = 15,
) -> Dict[str, Any]:
    url = backend_signal_url or os.getenv("BACKEND_SIGNAL_URL", "").strip()
    payloads = build_trade_signal_payloads(user_id=user_id, result=result, ttl_minutes=ttl_minutes)
    if not url or not payloads:
        return {"submitted": 0, "skipped": len(payloads), "enabled": bool(url)}

    submitted = 0
    failures: List[str] = []
    for payload in payloads:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "").strip()
        if token:
            headers["X-HQA-Internal-Token"] = token
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if 200 <= int(response.status) < 300:
                    submitted += 1
                else:
                    failures.append(f"{payload.get('stockCode')}:HTTP_{response.status}")
        except Exception as exc:
            logger.warning("trade signal submit failed: %s", exc)
            failures.append(f"{payload.get('stockCode')}:{type(exc).__name__}")

    return {"submitted": submitted, "failed": len(failures), "failures": failures, "enabled": True}
