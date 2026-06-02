from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _default_price_snapshot_url() -> str:
    explicit = os.getenv("BACKEND_PRICE_SNAPSHOT_URL", "").strip()
    if explicit:
        return explicit
    base = os.getenv("BACKEND_INTERNAL_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/api/v1/internal/market/price-snapshots"
    signal_url = os.getenv("BACKEND_SIGNAL_URL", "").strip()
    if signal_url.endswith("/api/v1/internal/trading/signals"):
        return signal_url[: -len("/api/v1/internal/trading/signals")] + "/api/v1/internal/market/price-snapshots"
    return "http://localhost:8000/api/v1/internal/market/price-snapshots"


def fetch_price_snapshot(
    *,
    user_id: str,
    stock_code: str,
    backend_price_snapshot_url: Optional[str] = None,
    internal_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not user_id or not stock_code:
        return None

    url = backend_price_snapshot_url or _default_price_snapshot_url()
    payload = {"userId": user_id, "stockCodes": [stock_code]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = internal_token if internal_token is not None else os.getenv("HQA_INTERNAL_TOKEN", "").strip()
    if token:
        headers["X-HQA-Internal-Token"] = token

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if not (200 <= int(response.status) < 300):
                return None
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("price snapshot fetch failed: %s", exc)
        return None

    rows = data.get("snapshots") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict) or not row.get("success"):
        return None
    return {
        "stock_code": row.get("stockCode") or row.get("stock_code") or stock_code,
        "current_price": row.get("currentPrice") or row.get("current_price"),
        "snapshot_at": row.get("snapshotAt") or row.get("snapshot_at"),
        "source": row.get("source") or "kis",
        "success": True,
    }
