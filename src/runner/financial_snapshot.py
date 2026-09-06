"""Dated DART financial facts; fiscal statement dates are never availability dates."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

AMOUNTS = ("revenue", "operating_profit", "net_income", "assets", "liabilities", "equity",
           "current_assets", "current_liabilities")
UNITS = {"KRW": Decimal(1), "\uc6d0": Decimal(1), "\ucc9c\uc6d0": Decimal(1000),
         "\ub9cc\uc6d0": Decimal(10000), "\ubc31\ub9cc\uc6d0": Decimal(1000000),
         "\uc5b5\uc6d0": Decimal(100000000)}
PERIODS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def _at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("financial availability timestamps must include timezone")
    return parsed


def _amount(value: Any, multiplier: Decimal) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a financial amount")
    try:
        amount = Decimal(str(value).replace(",", "")) * multiplier
    except InvalidOperation as exc:
        raise ValueError("invalid financial amount") from exc
    if not amount.is_finite():
        raise ValueError("nonfinite financial amount")
    return float(amount)


def _ratio(values: dict, numerator: str, denominator: str) -> float | None:
    if values[numerator] is None or values[denominator] in (None, 0):
        return None
    return round(values[numerator] / values[denominator] * 100, 6)


def load_financial_snapshot(data_dir: Path, stock_code: str, as_of: datetime) -> dict:
    if as_of.tzinfo is None:
        raise ValueError("analysis timestamp must include timezone")
    paths = sorted((data_dir / "raw" / "financials").glob("*.jsonl"))
    paths += sorted((data_dir / "market_data").glob("*/financials.jsonl"))
    observations: dict[tuple, list[dict]] = {}
    gaps = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if str(row.get("stock_code")) != stock_code:
                    continue
                meta = row.get("metadata") or {}
                try:
                    observed = _at(meta.get("collected_at") or meta.get("observed_at"))
                    published = _at(meta["published_at"]) if meta.get("published_at") else None
                    available = max(observed, published) if published else observed
                    if available > as_of:
                        continue
                    receipt = str(meta.get("rcept_no") or "")
                    if len(receipt) != 14 or not receipt.isdigit():
                        raise ValueError("missing DART receipt identifier")
                    if meta.get("source") != "dart" or meta.get("fs_div") not in {"CFS", "OFS"}:
                        raise ValueError("missing DART source or financial statement division")
                    if row.get("currency") != "KRW" or meta.get("currency_verified") is not True:
                        raise ValueError("unverified financial currency")
                    unit = meta["amount_unit"]
                    if unit not in UNITS:
                        raise ValueError("unsupported financial amount unit")
                    values = {key: _amount(row.get(key), UNITS[unit]) for key in AMOUNTS}
                    fiscal_year, period = int(row["fiscal_year"]), PERIODS[row["report_code"]]
                    if fiscal_year < as_of.year - 2 or fiscal_year > as_of.year:
                        raise ValueError("financial reporting year outside allowed window")
                    ratios = {"roe": _ratio(values, "net_income", "equity"),
                              "roa": _ratio(values, "net_income", "assets"),
                              "debt_ratio": _ratio(values, "liabilities", "equity"),
                              "current_ratio": _ratio(values, "current_assets", "current_liabilities"),
                              "operating_margin": _ratio(values, "operating_profit", "revenue"),
                              "net_margin": _ratio(values, "net_income", "revenue")}
                    identity = {"values": values, "receipt": receipt, "fs_div": meta["fs_div"],
                                "fiscal_year": fiscal_year, "report_code": row["report_code"]}
                    version = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
                    observation_id = hashlib.sha256(f"{version}|{available.isoformat()}".encode()).hexdigest()
                    missing = [key for key in AMOUNTS[:6] if values[key] is None]
                    data = {"status": "ready" if not missing else "blocked",
                            "source_id": f"dart-financial:{receipt}:{observation_id}", "version": version,
                            "available_at": available.isoformat(), "observed_at": observed.isoformat(),
                            "published_at": published.isoformat() if published else None,
                            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                            "fs_div": meta["fs_div"], "currency": "KRW", "amount_unit": "KRW",
                            "fiscal_year": fiscal_year, "report_code": row["report_code"], "period": period,
                            "values": values, "ratios": ratios,
                            "gaps": [f"missing_financial_field:{key}" for key in missing]}
                    key = (fiscal_year, row["report_code"], meta["fs_div"])
                    observations.setdefault(key, []).append(data)
                except (ValueError, KeyError, TypeError) as exc:
                    gaps.append(f"{path.name}:{line_number}:{exc}")
    if not observations:
        return {"status": "blocked", "source_id": None, "ratios": None, "values": None,
                "gaps": gaps or ["no_dated_financial_snapshot; recollect DART financials"]}
    versions = []
    for rows in observations.values():
        current = None
        previous = None
        # Cross-theme copies coalesce, but A -> B -> A is three observations,
        # not two unique content hashes. Repeated A keeps its first availability.
        for row in sorted(rows, key=lambda value: _at(value["available_at"])):
            if (previous is not None and _at(row["available_at"]) == _at(previous["available_at"])
                    and row["version"] != previous["version"]):
                return {"status": "blocked", "source_id": None, "ratios": None, "values": None,
                        "gaps": gaps + ["conflicting_financial_observations_at_same_time"]}
            if current is None or row["version"] != current["version"]:
                current = row
            previous = row
        versions.append(current)
    latest = max(versions, key=lambda row: (row["fiscal_year"], row["period"],
                 row["fs_div"] == "CFS", _at(row["available_at"])))
    latest["gaps"].extend(gaps)
    return latest
