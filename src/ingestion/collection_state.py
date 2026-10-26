"""Public per-stock collection reuse; only completed requests advance coverage."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import atomic_write, file_lock

KST = timezone(timedelta(hours=9))


def _holds_last_session(payload: dict, to_date: str) -> bool:
    """Whether a daily-bar result already contains the last exchange session through to_date."""
    try:
        from src.runner.trading_calendar import is_trading_day
        day = datetime.strptime(to_date, "%Y%m%d").date()
        for _ in range(31):
            if is_trading_day(day.isoformat()):
                break
            day -= timedelta(days=1)
        else:
            return False
    except Exception:
        return False
    return any((row.get("metadata") or {}).get("trade_date") == day.isoformat()
               for row in payload.get("market_records") or [])


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
        if state and state["requested"] == requested:
            age = now - datetime.fromisoformat(state["completed_at"])
            if timedelta(0) <= age < timedelta(minutes=15):
                return state["result"], True
            # Bars through to_date do not change once their session closed, and the KIS key is
            # shared with live trading: once a KST day's result holds the last session, later
            # runs that day reuse it instead of calling KIS again during market hours.
            if (source == "kis_chart" and timedelta(0) <= age
                    and datetime.fromisoformat(state["completed_at"]).astimezone(KST).date() == now.astimezone(KST).date()
                    and _holds_last_session(state["result"], request.to_date)):
                return state["result"], True
        start = request.from_date
        if state and source in {"dart", "chart", "kis_chart"} and state["requested"][0] <= start:
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
