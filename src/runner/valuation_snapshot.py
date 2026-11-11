"""Latest KIS valuation snapshot per stock (EPS, PBR, listed shares) for the quant's market valuation.

The kis_chart collector writes one file per stock from the daily chart's summary block. The quant
otherwise sees no market price at all: on 2026-11-11 it took a disclosure's disposal price for the
share price. A snapshot is used only when it was observed before the analysis time and is recent.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

MAX_SNAPSHOT_AGE = timedelta(days=10)
UNAVAILABLE = {"status": "unavailable"}


def valuation_snapshot_path(data_dir: Path, stock_code: str) -> Path:
    return Path(data_dir) / "market_context" / "valuation" / f"{stock_code}.json"


def load_valuation_snapshot(data_dir: Path, stock_code: str, as_of: datetime) -> dict:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("valuation snapshot as_of requires a timezone")
    try:
        row = json.loads(valuation_snapshot_path(data_dir, stock_code).read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(row["observed_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return dict(UNAVAILABLE)
    if (not isinstance(row, dict) or row.get("stock_code") != stock_code or row.get("schema_version") != 1
            or observed.tzinfo is None or observed > as_of or as_of - observed > MAX_SNAPSHOT_AGE):
        return dict(UNAVAILABLE)
    for key in ("quote", "eps", "per", "pbr"):
        value = row.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            return dict(UNAVAILABLE)
    bps = row.get("bps")
    if bps is not None and (type(bps) not in (int, float) or not math.isfinite(bps) or bps <= 0):
        return dict(UNAVAILABLE)
    if type(row.get("listed_shares")) is not int or row["listed_shares"] <= 0:
        return dict(UNAVAILABLE)
    return {**row, "status": "ready"}
