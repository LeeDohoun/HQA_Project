from __future__ import annotations

import json

from src.runner.price_snapshot_client import fetch_price_snapshot


def test_fetch_price_snapshot_uses_hqa_internal_token_env(monkeypatch):
    captured = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "snapshots": [
                        {
                            "success": True,
                            "stockCode": "111111",
                            "currentPrice": 12345,
                            "snapshotAt": "2026-06-02T10:00:00+09:00",
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["token"] = request.headers.get("X-hqa-internal-token")
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "shared-token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    snapshot = fetch_price_snapshot(
        user_id="user-1",
        stock_code="111111",
        backend_price_snapshot_url="http://backend/api/v1/internal/market/price-snapshots",
    )

    assert snapshot["current_price"] == 12345
    assert captured == {"token": "shared-token", "timeout": 5}
