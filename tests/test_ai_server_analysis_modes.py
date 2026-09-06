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


def test_legacy_single_stock_analysis_endpoints_are_removed():
    client = TestClient(app)

    post_response = client.post(
        "/analyze",
        json={
            "task_id": "legacy-analysis",
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "mode": "full",
        },
    )
    get_response = client.get("/analyze/legacy-analysis")

    assert post_response.status_code == 404
    assert get_response.status_code == 404


def test_legacy_theme_analysis_endpoints_are_removed():
    client = TestClient(app)

    post_response = client.post(
        "/theme/analyze",
        json={
            "task_id": "legacy-theme",
            "theme": "반도체",
            "theme_key": "semiconductor",
        },
    )
    get_response = client.get("/theme/analyze/legacy-theme")

    assert post_response.status_code == 404
    assert get_response.status_code == 404


def test_legacy_analysis_executor_functions_are_removed():
    import ai_server.app as app_module

    assert not hasattr(app_module, "_execute_quick")
    assert not hasattr(app_module, "_execute_full")
    assert not hasattr(app_module, "_execute_theme")
