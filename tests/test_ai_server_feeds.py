from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

import ai_server.app as app_module


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def news(code, index, day):
    return {"source_type": "news", "stock_code": code, "stock_name": "삼성전자", "title": f"기사 {index}",
            "url": f"https://news.example/{index}", "published_at": f"2026-09-{day:02d}T09:00:00+09:00",
            "content": "본문 " * 200, "metadata": {"press": "연합", "summary": f"요약 {index}",
                                                 "collected_at": "2026-09-27T00:00:00+00:00", "unused": "x" * 500}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_hqa_get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(app_module, "_feed_index_cache", app_module.OrderedDict())
    return TestClient(app_module.app), tmp_path


def test_news_feed_dedupes_theme_and_shared_copies_and_fills_the_limit(client):
    http, root = client
    rows = [news("005930", index, 10 + index) for index in range(3)]
    write(root / "raw/news/반도체.jsonl", rows + rows + [news("000660", 9, 20)])
    write(root / "raw/news/_shared_005930.jsonl", rows + rows)
    write(root / "raw/news/_shared_000660.jsonl", [news("000660", 8, 21)])
    body = http.get("/stocks/005930/news?limit=3").json()
    assert [item["title"] for item in body["items"]] == ["기사 2", "기사 1", "기사 0"]
    assert body["items"][0]["source"] == "연합" and body["items"][0]["summary"] == "요약 2"


def test_feed_index_keeps_only_display_fields_and_refreshes_on_change(client):
    http, root = client
    path = root / "raw/news/반도체.jsonl"
    write(path, [news("005930", 1, 11)])
    assert len(http.get("/stocks/005930/news").json()["items"]) == 1
    cached = next(iter(app_module._feed_index_cache.values()))[2]["005930"][0]
    assert "content" not in cached and "unused" not in cached["metadata"]
    write(path, [news("005930", 1, 11), news("005930", 2, 12)])
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    assert len(http.get("/stocks/005930/news").json()["items"]) == 2


def test_feed_cache_is_bounded(client, monkeypatch):
    http, root = client
    monkeypatch.setattr(app_module, "_FEED_FILE_CACHE_LIMIT", 2)
    for theme in ("a", "b", "c"):
        write(root / f"raw/news/{theme}.jsonl", [news("005930", 1, 11)])
    http.get("/stocks/005930/news")
    assert len(app_module._feed_index_cache) == 2


@pytest.mark.parametrize("path", ["/stocks/12/news", "/stocks/abcdef/disclosures", "/stocks/0126Z0/news"])
def test_feed_endpoints_reject_malformed_stock_codes(client, path):
    http, _ = client
    assert http.get(path).status_code == 400


def test_internal_status_requires_token_and_reports_operations(client, monkeypatch):
    http, root = client
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "status-token")
    monkeypatch.setenv("HQA_LLM_BUDGET_PATH", str(root / "llm_budget.sqlite3"))
    pointer = root / "canonical_index/반도체/current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"schema_version": 1, "generation": "a" * 32, "published_at": "2026-09-28T00:00:00+00:00"}))
    assert http.get("/internal/status").status_code == 401
    body = http.get("/internal/status", headers={"X-HQA-Internal-Token": "status-token"}).json()
    assert body["llm_budget"] == {"status": "not_initialized"}
    assert not (root / "llm_budget.sqlite3").exists()  # status never creates a ledger
    assert body["themes"]["반도체"]["generation"] == "a" * 32
    assert isinstance(body["calendar_warnings"], list) and isinstance(body["runtime_tasks"], dict)


def test_feeds_and_status_answer_while_runtime_tasks_hold_every_default_thread(tmp_path, monkeypatch):
    import threading
    import time
    import src.runner.shared_analysis as shared

    monkeypatch.setattr(app_module, "_hqa_get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(app_module, "_feed_index_cache", app_module.OrderedDict())
    monkeypatch.setattr(app_module, "_runtime_tasks", app_module.OrderedDict())
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "fixture-token")
    write(tmp_path / "raw/news/반도체.jsonl", [news("005930", 1, 11)])
    release = threading.Event()

    class BlockedPreview:  # stands in for previews waiting on LLM calls
        def preview_stock(self, code):
            release.wait(10)
            return {"stock_code": code, "status": "completed"}

    monkeypatch.setattr(shared, "get_runtime_analysis_service", lambda *args, **kwargs: BlockedPreview())
    headers = {"X-HQA-Internal-Token": "fixture-token"}
    with TestClient(app_module.app) as http:
        try:
            for index in range(40):  # more than the default executor ever has (at most 32 threads)
                response = http.post("/runtime/stock-preview", json={"stock_code": f"{index:06d}"}, headers=headers)
                assert response.status_code == 202
            started = time.monotonic()
            assert http.get("/stocks/005930/news").json()["items"]
            assert http.get("/internal/status", headers=headers).json()["runtime_tasks"]["running"] > 0
            assert time.monotonic() - started < 5
        finally:
            release.set()
