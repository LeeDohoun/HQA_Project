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


def test_current_stock_preview_requires_internal_auth_and_returns_runtime_result(monkeypatch):
    import ai_server.app as module
    import src.runner.shared_analysis as shared
    from types import SimpleNamespace
    import asyncio
    import httpx

    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "preview-test-token")
    monkeypatch.setattr(shared, "get_runtime_analysis_service", lambda: SimpleNamespace(
        preview_stock=lambda code: {"stock_code": code, "status": "completed", "plans": []}))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post("/runtime/stock-preview", json={"stock_code": "005930"})
            assert denied.status_code == 401
            headers = {"X-HQA-Internal-Token": "preview-test-token"}
            response = await client.post("/runtime/stock-preview", json={"stock_code": "005930"}, headers=headers)
            assert response.status_code == 202
            task_id = response.json()["task_id"]
            for _ in range(100):
                await asyncio.sleep(0.01)
                task = (await client.get(f"/runtime/tasks/{task_id}", headers=headers)).json()
                if task["status"] == "completed":
                    break
            assert task["result"]["stock_code"] == "005930"
            assert task["result"]["plans"] == []
            assert task["created_at"].endswith("+00:00")
            module._runtime_tasks.pop(task_id)
    asyncio.run(run())


def test_runtime_capacity_does_not_evict_running_tasks(monkeypatch):
    import ai_server.app as module
    from collections import OrderedDict
    from fastapi import HTTPException

    monkeypatch.setattr(module, "_MAX_CACHE", 1)
    monkeypatch.setattr(module, "_runtime_tasks", OrderedDict([("active", {"status": "running"})]))
    with pytest.raises(HTTPException) as error:
        module._new_runtime_task("stock_preview")
    assert error.value.status_code == 503
    assert "active" in module._runtime_tasks
