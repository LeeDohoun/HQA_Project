from __future__ import annotations

import json
import hashlib
import logging
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
# A plan POST makes a fresh paced KIS balance call on the backend.
SUBMIT_TIMEOUT_SECONDS = 30


def build_trade_signal_payloads(
    *,
    user_id: str,
    result: Dict[str, Any],
    source: str = "multi_theme_leader",
    now: Optional[datetime] = None,
    ttl_minutes: int = 15,
    active_plans: Optional[List[Dict[str, Any]]] = None,
    skipped: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not user_id:
        return []

    if result.get("schema_version") == 2:
        return _build_v2_payloads(user_id, result, source, now or datetime.now(KST), active_plans or [], skipped)

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


def _hard_stop(conditions: Dict[str, Any]) -> Optional[float]:
    """The backend's hard stop: the highest single-predicate current_price <= group."""
    stops = [atom["value"] for name in ("exit_conditions", "invalidation_conditions")
             for group in conditions.get(name) or [] if isinstance(group, dict) and len(group.get("all") or []) == 1
             for atom in group["all"] if atom.get("field") == "current_price" and atom.get("operator") == "<="
             and isinstance(atom.get("value"), (int, float)) and not isinstance(atom.get("value"), bool)]
    return max(stops) if stops else None


def _free_id(conditions: Dict[str, Any], base: str) -> str:
    used = {group.get("id") for name in ("entry_conditions", "exit_conditions", "reduce_conditions",
                                         "invalidation_conditions") for group in conditions.get(name) or []}
    candidate, index = base, 1
    while candidate in used:
        index += 1
        candidate = f"{base}-{index}"
    return candidate


def _build_v2_payloads(
    user_id: str,
    result: Dict[str, Any],
    source: str,
    now: datetime,
    active_plans: List[Dict[str, Any]],
    skipped: Optional[List[str]] = None,
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
    skipped = [] if skipped is None else skipped
    for plan in plans:
        if plan.action == "HOLD" and plan.holding_quantity == 0:
            continue
        # One unusable plan is skipped and reported; it must not stop the account's other
        # plans, above all the protection updates for its holdings.
        if plan.entry_valid_until > as_of + timedelta(minutes=15):
            skipped.append(f"{plan.stock_code}:entry_validity_beyond_15_minutes")
            continue
        if plan.action == "BUY" and plan.holding_quantity == 0 and plan.entry_valid_until <= now:
            skipped.append(f"{plan.stock_code}:entry_plan_expired")
            continue
        previous = active.get(plan.stock_code)
        version = int(previous["planVersion"]) + 1 if previous else 1
        key = hashlib.sha256(json.dumps([user_id, analysis_id, plan.stock_code, strategy],
                                       separators=(",", ":")).encode()).hexdigest()
        row = ranked.get(plan.stock_code, {})
        action = "HOLD" if plan.action == "BUY" and plan.holding_quantity else plan.action
        data = plan.model_dump(mode="json")
        conditions = plan.condition_payload.model_dump(mode="json")
        if plan.holding_quantity and previous:
            # The backend refuses to lower or drop an open position's hard stop; keep the
            # current floor instead of losing every other update in this plan.
            prior = _hard_stop(previous.get("conditionPayload") or {})
            current = _hard_stop(conditions)
            if prior is not None and (current is None or current < prior):
                conditions["exit_conditions"].append({"id": _free_id(conditions, "carried-stop"), "all": [
                    {"field": "current_price", "operator": "<=", "value": prior}]})
        if action == "SELL":
            # A SELL decision is an exit at the monitor's next poll, not a plan waiting for a trigger.
            conditions["exit_conditions"].append({"id": _free_id(conditions, "sell-now"), "all": [
                {"field": "holding_quantity", "operator": ">", "value": 0.0}]})
        signal_price = int(round(plan.entry_price)) if plan.entry_price is not None else None
        if action == "BUY" and (signal_price is None or plan.stop_loss_price >= signal_price):
            skipped.append(f"{plan.stock_code}:stop_not_below_whole_won_entry")
            continue
        score = row.get("leader_score")
        leader_score = int(round(score)) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
        payloads.append({
            "userId": user_id, "source": source, "strategyProfile": strategy,
            "analysisId": analysis_id, "analysisAsOf": as_of.isoformat(), "accountMode": "PAPER", "planVersion": version,
            "stockCode": plan.stock_code, "stockName": plan.stock_name,
            "action": action, "leaderScore": leader_score,
            "confidence": plan.confidence, "riskLevel": plan.risk_level,
            "targetPositionPct": plan.position_size_pct, "positionSize": f"{plan.position_size_pct:g}%",
            "signalPrice": signal_price, "stopLoss": str(plan.stop_loss_price) if plan.stop_loss_price is not None else None,
            "reason": plan.reasoning, "entryValidUntil": plan.entry_valid_until.isoformat(),
            "expiresAt": plan.entry_valid_until.isoformat(), "plannedExitAt": plan.planned_exit_at.isoformat(),
            "tradePlanJson": data, "conditionPayload": conditions,
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
    skipped: List[str] = []
    payloads = build_trade_signal_payloads(user_id=user_id, result=result, ttl_minutes=ttl_minutes,
                                           active_plans=active_plans, skipped=skipped)
    if not url or not payloads:
        return {"submitted": 0, "skipped": len(payloads), "enabled": bool(url), "skipped_plans": skipped}

    submitted = 0
    failures: List[str] = []
    for payload in payloads:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["X-HQA-Internal-Token"] = token
        code = payload.get("stockCode")
        # The backend paces every KIS call at 1/s, so a slow answer is not a refusal. The
        # idempotency key makes a single retry safe: a committed first attempt is replayed.
        for attempt in (1, 2):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=SUBMIT_TIMEOUT_SECONDS) as response:
                    if 200 <= int(response.status) < 300:
                        submitted += 1
                    else:
                        failures.append(f"{code}:HTTP_{response.status}")
                break
            except urllib.error.HTTPError as exc:
                reason = exc.read(300).decode("utf-8", "replace") if exc.fp is not None else ""
                failures.append(f"{code}:HTTP_{exc.code}:{reason}".rstrip(":"))
                break
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                if attempt == 2:
                    logger.warning("trade signal submit failed twice: %s", exc)
                    failures.append(f"{code}:{type(exc).__name__}:outcome_unknown_after_retry")
            except Exception as exc:
                logger.warning("trade signal submit failed: %s", exc)
                failures.append(f"{code}:{type(exc).__name__}")
                break

    return {"submitted": submitted, "failed": len(failures), "failures": failures, "enabled": True,
            "skipped_plans": skipped}
