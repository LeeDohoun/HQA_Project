"""Read dated benchmark observations and explicit, source-backed index mappings."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from src.runner.analysis_data import content_hash, read_jsonl, source_time

MARKET_INDEX_NAMES = {"KOSPI": "\ucf54\uc2a4\ud53c", "KOSDAQ": "\ucf54\uc2a4\ub2e5"}
KST = timezone(timedelta(hours=9))


@lru_cache(maxsize=8)
def _cached_rows(path: str, modified: int, size: int) -> list[dict]:
    return read_jsonl(Path(path))


def _rows(path: Path) -> list[dict]:
    stat = path.stat()
    return _cached_rows(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _aware(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("benchmark mapping availability requires an ISO timestamp")
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("benchmark mapping availability requires a timezone")
    return at.astimezone(timezone.utc)


def _price_day(row: dict) -> str:
    if row.get("trade_date") is not None:
        return date.fromisoformat(row["trade_date"]).isoformat()
    return source_time(row.get("bar_at") or row["available_at"]).astimezone(KST).date().isoformat()


def _mapping(row: dict) -> dict:
    for field in ("stock_code", "kind", "series", "index_name", "source_id", "version", "source_url"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"benchmark mapping requires {field}")
    if (type(row.get("schema_version")) is not int or row["schema_version"] != 1
            or row["kind"] not in {"market", "sector"}
            or len(row["stock_code"]) != 6 or not row["stock_code"].isdigit()
            or row["series"] not in MARKET_INDEX_NAMES):
        raise ValueError("invalid benchmark mapping identity")
    url = urlsplit(row["source_url"])
    secret_keys = {"auth_key", "authorization", "api_key", "apikey", "crtfc_key", "access_token", "token", "key"}
    if (url.scheme != "https" or not url.netloc or url.username or url.password
            or any(key.lower() in secret_keys for part in (url.query, url.fragment) for key, _ in parse_qsl(part))):
        raise ValueError("benchmark mapping requires a credential-free HTTPS source URL")
    if not isinstance(row.get("effective_from"), str):
        raise ValueError("benchmark mapping requires effective_from")
    start = date.fromisoformat(row["effective_from"])
    if "effective_to" not in row:
        raise ValueError("benchmark mapping requires explicit nullable effective_to")
    if row["effective_to"] is not None and not isinstance(row["effective_to"], str):
        raise ValueError("benchmark mapping effective_to must be a date string or null")
    end = date.fromisoformat(row["effective_to"]) if row["effective_to"] is not None else None
    if end is not None and end < start:
        raise ValueError("inverted benchmark mapping interval")
    if row["kind"] == "market" and row["index_name"] != MARKET_INDEX_NAMES[row["series"]]:
        raise ValueError("market benchmark must be the broad-market index, not a sector proxy")
    return {"status": "ready", "series": row["series"], "index_name": row["index_name"],
            "mapping_source_id": row["source_id"], "mapping_version": row["version"],
            "mapping_source_url": row["source_url"], "mapping_available_at": _aware(row.get("available_at")).isoformat(),
            "effective_from": start.isoformat(), "effective_to": end.isoformat() if end else None}


def load_benchmark_context(data_dir: Path, candidate: dict, as_of: datetime) -> dict:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("benchmark context as_of requires a timezone")
    result = {kind: {"status": "unavailable", "data_gaps": [f"{kind}_mapping_unavailable"]}
              for kind in ("market", "sector")}
    history = [row for row in candidate["price_history"]
               if _aware(row.get("observed_at") or row.get("available_at")) <= as_of
               and _aware(row.get("bar_at") or row.get("available_at")) <= as_of]
    from src.ingestion.krx_chart import KrxChartCollector
    endpoints = {"KOSPI": KrxChartCollector.KOSPI_DAILY_URL, "KOSDAQ": KrxChartCollector.KOSDAQ_DAILY_URL}
    for row in history:
        if row.get("market") is not None and (row["market"] not in endpoints or row.get("source") != "krx"
                or row.get("source_url") != endpoints[row["market"]] or row.get("price_basis") != "unadjusted"):
            raise ValueError("stock market mapping requires verified KRX price provenance")
    markets = {row.get("market") for row in history}
    if len(markets) == 1 and next(iter(markets)) in MARKET_INDEX_NAMES:
        market = next(iter(markets))
        result["market"] = {"status": "ready", "series": market, "index_name": MARKET_INDEX_NAMES[market],
            "mapping_source_id": "price:" + candidate["stock_code"] + ":" + content_hash(history),
            "effective_from": min(_price_day(row) for row in history),
            "effective_to": max(_price_day(row) for row in history)}
    mapping_path = data_dir / "market_context" / "benchmark_mappings.jsonl"
    if mapping_path.exists():
        selected = {}
        today = as_of.astimezone(KST).date()
        for row in _rows(mapping_path):
            if row.get("stock_code") != candidate["stock_code"]:
                continue
            if _aware(row.get("available_at")) > as_of:
                continue
            mapped = _mapping(row)
            if date.fromisoformat(mapped["effective_from"]) > today:
                continue
            key = (row["kind"], mapped["effective_from"])
            previous = selected.get(key)
            if previous and mapped["mapping_available_at"] == previous["mapping_available_at"] and mapped != previous:
                raise ValueError("conflicting benchmark mappings at the same availability")
            if previous is None or mapped["mapping_available_at"] > previous["mapping_available_at"]:
                selected[key] = mapped
        grouped = {kind: [] for kind in result}
        for (kind, _), mapped in selected.items():
            for row in history:
                day = _price_day(row)
                if (row.get("market") and mapped["effective_from"] <= day
                        and (mapped["effective_to"] is None or day <= mapped["effective_to"])
                        and row["market"] != mapped["series"]):
                    raise ValueError("benchmark mapping conflicts with verified stock market in its effective interval")
            grouped[kind].append(mapped)
        for market in grouped["market"]:
            for sector in grouped["sector"]:
                if (market["series"] != sector["series"]
                        and market["effective_from"] <= (sector["effective_to"] or "9999-12-31")
                        and sector["effective_from"] <= (market["effective_to"] or "9999-12-31")):
                    raise ValueError("sector benchmark conflicts with verified stock market")
        for kind, mappings in grouped.items():
            if mappings:
                mappings.sort(key=lambda item: (item["mapping_available_at"], item["effective_from"]))
                result[kind] = {**mappings[-1], "mapping_history": mappings}
    path = data_dir / "market_context" / "benchmarks.jsonl"
    from src.ingestion.krx_benchmarks import validate_benchmark_record
    for kind, mapped in result.items():
        if mapped["status"] != "ready":
            continue
        for item in mapped.get("mapping_history", [mapped]):
            if not path.exists():
                item.update(status="unavailable", data_gaps=["benchmark_history_unavailable"])
                continue
            item["bars"] = [validate_benchmark_record(row) for row in _rows(path)
                            if row.get("series") == item["series"] and row.get("index_name") == item["index_name"]
                            and _aware(row.get("available_at")) <= as_of]
            if not item["bars"]:
                item.update(status="unavailable", data_gaps=["benchmark_index_not_found"])
        if "mapping_history" in mapped:
            mapped.update(mapped["mapping_history"][-1])
    return result
