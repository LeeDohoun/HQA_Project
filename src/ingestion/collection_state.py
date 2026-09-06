"""Public per-stock collection reuse; only completed requests advance coverage."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import atomic_write, file_lock


def collect_shared(request, source: str, collect):
    identity = {"source": source, "stock_code": request.target.stock_code, "schema_version": 2}
    if source in {"dart", "financials"}:
        identity["corp_code"] = request.target.corp_code
    elif source == "news":
        identity.update(stock_name=request.target.stock_name, max_news=request.max_news)
    elif source == "forum":
        identity["forum_pages"] = request.forum_pages
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = Path(request.raw_output_dir).parent / "collection_state" / f"{key}.json"
    requested = [request.from_date, request.to_date]
    with file_lock(path.with_suffix(".lock")):
        state = json.loads(path.read_text()) if path.exists() else None
        now = datetime.now(timezone.utc)
        if state and state["identity"] != identity:
            raise ValueError("collection state identity mismatch")
        if (state and state["requested"] == requested
                and timedelta(0) <= now - datetime.fromisoformat(state["completed_at"]) < timedelta(minutes=15)):
            return state["result"], True
        start = request.from_date
        if state and source in {"dart", "chart"} and state["requested"][0] <= start:
            # Revisit recent dates so provider corrections are observed, not overwritten.
            overlap = datetime.strptime(state["requested"][1], "%Y%m%d") - timedelta(days=7)
            start = max(start, min(overlap.strftime("%Y%m%d"), request.to_date))
        result = collect(replace(request, from_date=start, enabled_sources=[source], incremental=False,
                                 theme_key=f"_shared_{request.target.stock_code}"))
        payload = asdict(result)
        if (result.report.source_success.get(source)
                and result.report.source_counts.get(source, 0) > 0):
            atomic_write(path, json.dumps({"identity": identity, "requested": requested,
                "completed_at": datetime.now(timezone.utc).isoformat(), "result": payload},
                ensure_ascii=False, allow_nan=False))
        return payload, False
