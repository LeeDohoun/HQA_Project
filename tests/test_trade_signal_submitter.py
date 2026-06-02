from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
