from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runner.trade_signal_submitter import build_trade_signal_payloads, submit_trade_signals


KST = timezone(timedelta(hours=9))


def test_build_trade_signal_payloads_includes_user_profile_decision_and_expiry():
    result = {
        "strategy_profile": "short",
        "global_ranked_leaders": [
            {
                "eligible": True,
                "theme": "AI",
                "theme_key": "ai",
                "stock_name": "AI-Alpha",
                "stock_code": "111111",
                "leader_score": 88,
                "confidence": 80,
                "risk_level_code": "LOW",
                "action_code": "BUY",
                "leader": {
                    "final_decision": {
                        "action_code": "BUY",
                        "confidence": 80,
                        "risk_level_code": "LOW",
                        "position_size": "25%",
                        "stop_loss": "-5%",
                        "summary": "조건부 매수",
                        "trade_plan": {
                            "strategy": "breakout",
                            "max_position_pct": 10,
                        },
                        "entry_conditions": [
                            {"field": "current_price", "operator": ">=", "value": 72000}
                        ],
                        "exit_conditions": [
                            {"field": "current_price", "operator": ">=", "value": 78000}
                        ],
                        "reduce_conditions": [],
                        "invalidation_conditions": [
                            {"field": "current_price", "operator": "<=", "value": 68000}
                        ],
                    }
                },
            },
            {
                "eligible": False,
                "theme": "AI",
                "theme_key": "ai",
                "stock_name": "AI-Beta",
                "stock_code": "111112",
                "leader_score": 60,
                "leader": {"final_decision": {"action_code": "HOLD"}},
            },
        ],
    }

    now = datetime(2026, 6, 1, 10, 0, tzinfo=KST)
    payloads = build_trade_signal_payloads(
        user_id="user-1",
        result=result,
        source="multi_theme_leader",
        now=now,
        ttl_minutes=15,
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["userId"] == "user-1"
    assert payload["source"] == "multi_theme_leader"
    assert payload["strategyProfile"] == "short"
    assert payload["themeKey"] == "ai"
    assert payload["stockCode"] == "111111"
    assert payload["action"] == "BUY"
    assert payload["leaderScore"] == 88
    assert payload["confidence"] == 80
    assert payload["riskLevel"] == "LOW"
    assert payload["positionSize"] == "25%"
    assert payload["stopLoss"] == "-5%"
    assert payload["expiresAt"] == "2026-06-01T10:15:00+09:00"
    assert payload["tradePlanJson"]["strategy"] == "breakout"
    assert "positionPolicy" not in payload
    assert payload["conditionPayload"]["entry_conditions"][0]["operator"] == ">="
    assert payload["conditionPayload"]["exit_conditions"][0]["value"] == 78000
    assert payload["idempotencyKey"] == "user-1:multi_theme_leader:short:111111:BUY:2026-06-01T10:15:00+09:00"
    assert payload["rawPayload"]["leader"]["final_decision"]["summary"] == "조건부 매수"


def test_submit_trade_signals_uses_hqa_internal_token_env(monkeypatch):
    captured = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured["token"] = request.headers.get("X-hqa-internal-token")
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "shared-token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = {
        "global_ranked_leaders": [
            {
                "eligible": True,
                "theme": "AI",
                "theme_key": "ai",
                "stock_name": "AI-Alpha",
                "stock_code": "111111",
                "leader_score": 88,
                "action_code": "BUY",
                "leader": {"final_decision": {"action_code": "BUY"}},
            }
        ]
    }

    response = submit_trade_signals(
        user_id="user-1",
        result=result,
        backend_signal_url="http://backend/api/v1/internal/trading/signals",
    )

    assert response["submitted"] == 1
    assert captured == {"token": "shared-token", "timeout": 10}


def test_build_trade_signal_payloads_includes_reduce_action_for_existing_positions():
    result = {
        "strategy_profile": "short",
        "global_ranked_leaders": [
            {
                "eligible": True,
                "theme": "반도체",
                "theme_key": "semiconductor",
                "stock_name": "삼성전자",
                "stock_code": "005930",
                "leader_score": 82,
                "leader": {
                    "final_decision": {
                        "action_code": "REDUCE",
                        "confidence": 71,
                        "risk_level_code": "MEDIUM",
                        "position_size": "50%",
                        "reduce_conditions": [
                            {"field": "current_price", "operator": "<=", "value": 68000}
                        ],
                    }
                },
            }
        ],
    }

    payloads = build_trade_signal_payloads(user_id="user-1", result=result)

    assert len(payloads) == 1
    assert payloads[0]["action"] == "REDUCE"
    assert payloads[0]["conditionPayload"]["reduce_conditions"][0]["value"] == 68000


def _v2_result():
    as_of = datetime(2026, 9, 7, 10, 0, tzinfo=KST)
    return {
        "schema_version": 2, "status": "completed", "analysis_id": "analysis-1", "user_id": "user-1",
        "as_of": as_of.isoformat(), "strategy_profile": "short",
        "plans": [{"stock_code": "005930", "stock_name": "Samsung", "action": "BUY", "holding_quantity": 0,
                   "confidence": 80, "risk_level": "MEDIUM", "position_size_pct": 10,
                   "entry_price": 100, "stop_loss_price": 90, "take_profit_price": 120,
                   "entry_valid_until": (as_of + timedelta(minutes=15)).isoformat(),
                   "planned_exit_at": (as_of + timedelta(days=3)).isoformat(),
                   "condition_payload": {"schema_version": 2,
                       "entry_conditions": [{"id": "entry", "all": [{"field": "current_price", "operator": "<=", "value": 100}]}],
                       "exit_conditions": [{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": 90}]}],
                       "reduce_conditions": [],
                       "invalidation_conditions": [{"id": "invalid", "all": [{"field": "current_price", "operator": ">", "value": 120}]}]},
                   "citations": [{"source_id": "dart-1", "claim": "Published operating results"}],
                   "reasoning": "A sourced plan"}],
    }


def test_v2_idempotency_and_expiry_do_not_change_on_resubmission():
    result = _v2_result()
    now = datetime.fromisoformat(result["as_of"])
    first = build_trade_signal_payloads(user_id="user-1", result=result, now=now)[0]
    second = build_trade_signal_payloads(user_id="user-1", result=result, now=now + timedelta(minutes=1))[0]
    assert first["idempotencyKey"] == second["idempotencyKey"]
    assert first["entryValidUntil"] == second["entryValidUntil"]
    assert first["targetPositionPct"] == 10
    assert first["conditionPayload"]["schema_version"] == 2


def test_v2_active_plan_version_increments_only_for_matching_account():
    result = _v2_result()
    payload = build_trade_signal_payloads(user_id="user-1", result=result,
        now=datetime.fromisoformat(result["as_of"]),
        active_plans=[{"userId": "user-1", "stockCode": "005930", "planVersion": 4},
                      {"userId": "user-2", "stockCode": "005930", "planVersion": 20}])[0]
    assert payload["planVersion"] == 5


def test_v2_holdings_get_protection_but_new_holds_do_not_create_orders():
    result = _v2_result()
    plan = result["plans"][0]
    plan["action"] = "HOLD"
    now = datetime.fromisoformat(result["as_of"])
    assert build_trade_signal_payloads(user_id="user-1", result=result, now=now) == []
    plan["holding_quantity"] = 3
    assert build_trade_signal_payloads(user_id="user-1", result=result, now=now)[0]["action"] == "HOLD"
    plan["action"] = "BUY"
    assert build_trade_signal_payloads(user_id="user-1", result=result, now=now)[0]["action"] == "HOLD"


def test_v2_expired_buy_and_account_mismatch_fail_clearly():
    result = _v2_result()
    with pytest.raises(ValueError, match="expired"):
        build_trade_signal_payloads(user_id="user-1", result=result,
                                    now=datetime.fromisoformat(result["as_of"]) + timedelta(minutes=16))
    with pytest.raises(ValueError, match="account"):
        build_trade_signal_payloads(user_id="user-2", result=result)


def test_v2_missing_evidence_and_failed_analysis_cannot_publish():
    result = _v2_result()
    now = datetime.fromisoformat(result["as_of"])
    result["plans"][0]["citations"] = []
    with pytest.raises(ValueError):
        build_trade_signal_payloads(user_id="user-1", result=result, now=now)
    result["status"] = "failed"
    with pytest.raises(ValueError, match="completed"):
        build_trade_signal_payloads(user_id="user-1", result=result, now=now)


def test_stale_held_plan_cannot_replace_newer_protection():
    result = _v2_result()
    result["plans"][0].update(action="HOLD", holding_quantity=3)
    with pytest.raises(ValueError, match="expired analysis"):
        build_trade_signal_payloads(user_id="user-1", result=result,
            now=datetime.fromisoformat(result["as_of"]) + timedelta(days=2),
            active_plans=[{"userId": "user-1", "stockCode": "005930", "planVersion": 10}])


def test_v2_missing_signal_endpoint_fails_before_discarding_plans(monkeypatch):
    monkeypatch.delenv("BACKEND_SIGNAL_URL", raising=False)
    with pytest.raises(ValueError, match="BACKEND_SIGNAL_URL is required"):
        submit_trade_signals(user_id="user-1", result=_v2_result())


def test_legacy_missing_signal_endpoint_retains_explicit_disabled_result(monkeypatch):
    monkeypatch.delenv("BACKEND_SIGNAL_URL", raising=False)
    result = submit_trade_signals(user_id="user-1", result={"global_ranked_leaders": []})
    assert result == {"submitted": 0, "skipped": 0, "enabled": False}
