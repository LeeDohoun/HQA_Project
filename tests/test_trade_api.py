from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import reset_settings_cache

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ai_server.app import app


def test_trading_preview_and_execute_endpoints(tmp_path, monkeypatch):
    orders_dir = tmp_path / "orders"
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    client = TestClient(app)
    payload = {
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "current_price": 100000,
        "dry_run_override": True,
        "trading_enabled_override": True,
        "final_decision": {
            "total_score": 88,
            "action": "매수",
            "confidence": 72,
            "risk_level": "낮음",
            "summary": "자동매매 테스트용 매수 판단",
        },
    }

    preview_response = client.post("/trading/decision/preview", json=payload)
    assert preview_response.status_code == 200
    preview_body = preview_response.json()
    assert preview_body["decision"]["action_code"] == "BUY"
    assert preview_body["preview"]["status"] == "ready"

    execute_response = client.post("/trading/decision/execute", json=payload)
    assert execute_response.status_code == 200
    execute_body = execute_response.json()
    assert execute_body["trade"]["status"] == "simulated"

    orders_response = client.get("/trading/orders")
    assert orders_response.status_code == 200
    orders_body = orders_response.json()
    assert orders_body["count"] >= 1
    assert any(row.get("stock_code") == "005930" for row in orders_body["orders"])

    reset_settings_cache()
