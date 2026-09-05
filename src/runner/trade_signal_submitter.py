from __future__ import annotations

import json
import hashlib
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
    active_plans: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if not user_id:
        return []

    if result.get("schema_version") == 2:
        return _build_v2_payloads(user_id, result, source, now or datetime.now(KST), active_plans or [])

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


def _build_v2_payloads(
    user_id: str,
    result: Dict[str, Any],
    source: str,
    now: datetime,
    active_plans: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from src.runner.analysis_contracts import TradingPlan

    if result.get("status") != "completed":
        raise ValueError("Only completed, validated analyses may publish plans")
    if result.get("user_id", user_id) != user_id:
        raise ValueError("Analysis account does not match submission account")
    analysis_id = result["analysis_id"]
    if not isinstance(analysis_id, str) or not analysis_id:
        raise ValueError("An immutable analysis_id is required")
    as_of = datetime.fromisoformat(str(result["as_of"]).replace("Z", "+00:00"))
    if as_of.tzinfo is None or now.tzinfo is None or as_of > now + timedelta(seconds=5):
        raise ValueError("Invalid analysis timestamp")
    if now - as_of > timedelta(minutes=15):
        raise ValueError("Cannot publish an expired analysis, including held-position updates")
    strategy = result["strategy_profile"]
    plans = [TradingPlan.model_validate(value) for value in result["plans"]]
    if len({plan.stock_code for plan in plans}) != len(plans):
        raise ValueError("Duplicate stock plans")
    if sum(plan.action == "BUY" and plan.holding_quantity == 0 for plan in plans) > 5:
        raise ValueError("An account may receive at most five new entry plans per cycle")
    active = {str(row["stockCode"]): row for row in active_plans if str(row["userId"]) == user_id}
    ranked = {str(row["stock_code"]): row for row in result.get("global_ranked_leaders", [])}
    payloads = []
    for plan in plans:
        if plan.action == "HOLD" and plan.holding_quantity == 0:
            continue
        if plan.entry_valid_until > as_of + timedelta(minutes=15):
            raise ValueError("Entry expiry must be within 15 minutes of the analysis snapshot")
        if plan.action == "BUY" and plan.holding_quantity == 0 and plan.entry_valid_until <= now:
            raise ValueError("Cannot publish an expired entry plan")
        previous = active.get(plan.stock_code)
        version = int(previous["planVersion"]) + 1 if previous else 1
        key = hashlib.sha256(json.dumps([user_id, analysis_id, plan.stock_code, strategy],
                                       separators=(",", ":")).encode()).hexdigest()
        row = ranked.get(plan.stock_code, {})
        action = "HOLD" if plan.action == "BUY" and plan.holding_quantity else plan.action
        data = plan.model_dump(mode="json")
        payloads.append({
            "userId": user_id, "source": source, "strategyProfile": strategy,
            "analysisId": analysis_id, "analysisAsOf": as_of.isoformat(), "accountMode": "PAPER", "planVersion": version,
            "stockCode": plan.stock_code, "stockName": plan.stock_name,
            "action": action, "leaderScore": row.get("leader_score"),
            "confidence": plan.confidence, "riskLevel": plan.risk_level,
            "targetPositionPct": plan.position_size_pct, "positionSize": f"{plan.position_size_pct:g}%",
            "signalPrice": plan.entry_price, "stopLoss": str(plan.stop_loss_price) if plan.stop_loss_price is not None else None,
            "reason": plan.reasoning, "entryValidUntil": plan.entry_valid_until.isoformat(),
            "expiresAt": plan.entry_valid_until.isoformat(), "plannedExitAt": plan.planned_exit_at.isoformat(),
            "tradePlanJson": data, "conditionPayload": plan.condition_payload.model_dump(mode="json"),
            "idempotencyKey": key,
            "rawPayload": {"analysis_id": analysis_id, "as_of": as_of.isoformat(), "plan": data},
        })
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
    token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "").strip()
    if result.get("schema_version") == 2 and not url:
        raise ValueError("BACKEND_SIGNAL_URL is required to publish v2 trading plans")
    active_plans = []
    if url and result.get("schema_version") == 2:
        from src.runner.signal_monitor import BackendSignalClient

        suffix = "/api/v1/internal/trading/signals"
        if not url.endswith(suffix):
            raise ValueError("BACKEND_SIGNAL_URL must point to the internal trading signals endpoint")
        active_plans = BackendSignalClient(base_url=url[:-len(suffix)], internal_token=token).fetch_active_signals()
    payloads = build_trade_signal_payloads(user_id=user_id, result=result, ttl_minutes=ttl_minutes, active_plans=active_plans)
    if not url or not payloads:
        return {"submitted": 0, "skipped": len(payloads), "enabled": bool(url)}

    submitted = 0
    failures: List[str] = []
    for payload in payloads:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
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
