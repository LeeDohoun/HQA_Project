from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ai_server.app import app


def test_health_reports_runtime_port(monkeypatch):
    monkeypatch.setenv("PORT", "8123")

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["port"] == 8123


def test_backtest_result_submit_and_fetch():
    client = TestClient(app)
    payload = {
        "task_id": "bt-ai-2025-smoke",
        "theme": "AI",
        "theme_key": "ai",
        "period": {"from_date": "2025-01-01", "to_date": "2025-12-31"},
        "strategy": {"rebalance": "monthly", "top_n": 3},
        "metrics": {
            "total_return_pct": 12.5,
            "mdd_pct": -8.2,
            "sharpe": 1.1,
        },
        "leaders": [
            {"stock_name": "삼성전자", "stock_code": "005930", "leader_score": 82}
        ],
        "predictions": [
            {"as_of_date": "2025-06-30", "stock_code": "005930", "horizon_days": 20}
        ],
    }

    submit_response = client.post("/backtest/results", json=payload)
    assert submit_response.status_code == 201
    assert submit_response.json()["status"] == "stored"

    fetch_response = client.get("/backtest/results/bt-ai-2025-smoke")
    assert fetch_response.status_code == 200
    body = fetch_response.json()
    assert body["mode"] == "backtest"
    assert body["result_type"] == "backtest"
    assert body["theme_key"] == "ai"
    assert body["metrics"]["sharpe"] == 1.1
    assert body["leaders"][0]["stock_code"] == "005930"
    assert body["received_at"]

    shared_fetch_response = client.get("/analyze/bt-ai-2025-smoke")
    assert shared_fetch_response.status_code == 404


def test_ai_server_direct_order_endpoints_are_removed():
    client = TestClient(app)
    payload = {
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "current_price": 100000,
        "final_decision": {
            "total_score": 88,
            "action": "매수",
            "confidence": 72,
            "risk_level": "낮음",
            "summary": "자동매매 테스트용 매수 판단",
        },
    }

    preview_response = client.post("/trading/decision/preview", json=payload)
    assert preview_response.status_code == 404

    execute_response = client.post("/trading/decision/execute", json=payload)
    assert execute_response.status_code == 404
