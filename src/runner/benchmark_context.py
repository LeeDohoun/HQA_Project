"""Point-in-time price-index comparisons on the stock's exact observation dates."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

BENCHMARK_CONTEXT_VERSION = "benchmark-context-v1"
_KST = timezone(timedelta(hours=9))


def _at(value, field: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} requires an aware timestamp")
    return value.astimezone(timezone.utc)


def _number(value, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} requires a finite JSON number")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"invalid {field}")
    return number


def _source_ids(values) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("source_ids must contain nonblank strings")
    return sorted(set(values))


def _date(value, field: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{field} requires YYYY-MM-DD")
    return value


def _known_bars(rows: list[dict], cutoff: datetime, series: str) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError("benchmark bars must be a list")
    versions, seen_times, known = {}, {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("benchmark bar must be an object")
        available = _at(row.get("available_at"), "benchmark.available_at")
        if available > cutoff:
            continue
        bar_at = _at(row.get("bar_at"), "benchmark.bar_at")
        if bar_at > cutoff:
            continue
        day = _date(row.get("trade_date"), "benchmark.trade_date")
        if bar_at.astimezone(_KST).date().isoformat() != day:
            raise ValueError("benchmark trade_date must match the Korean bar date")
        if available < bar_at:
            raise ValueError("benchmark close cannot be observed before bar_at")
        for key in ("source_id", "version"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"benchmark requires {key}")
        if row.get("price_basis") != "price_index":
            raise ValueError("benchmark requires price_index basis")
        if "series" in row and row["series"] != series:
            raise ValueError("benchmark bar series differs from its mapping")
        value = {"trade_date": day, "bar_at": bar_at, "available_at": available,
                 "close": _number(row.get("close"), "benchmark.close", positive=True),
                 "source_id": row["source_id"], "version": row["version"]}
        key = (day, row["version"])
        prior = versions.get(key)
        if prior is not None:
            if any(prior[field] != value[field] for field in ("bar_at", "close", "source_id")):
                raise ValueError("conflicting benchmark values for one version")
        versions[key] = value
        key = (day, available)
        if key in seen_times and seen_times[key] != value["version"]:
            raise ValueError("conflicting benchmark revisions at the same availability time")
        seen_times[key] = value["version"]
        # A later A-B-A observation is a real reversion, not an old duplicate.
        if day not in known or value["available_at"] > known[day]["available_at"]:
            known[day] = value
    return known


def _stock_date(snapshot, cutoff: datetime) -> str | None:
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict):
        raise ValueError("stock reaction bar must be an object")
    at = _at(snapshot.get("available_at"), "stock.bar.available_at")
    if at > cutoff:
        raise ValueError("stock reaction includes a future bar")
    _number(snapshot.get("close"), "stock.close", positive=True)
    return at.astimezone(_KST).date().isoformat()


def _window(baseline: str | None, endpoint: str | None, stock_return, stock_status: str,
            known: dict, sources: list[str], ready: bool, effective_from: str | None, effective_to: str | None) -> dict:
    if stock_return is not None:
        stock_return = _number(stock_return, "stock.return_pct")
    result = {"status": "benchmark_unavailable", "baseline_trade_date": baseline,
              "endpoint_trade_date": endpoint, "index_return_pct": None, "excess_return_pp": None,
              "source_ids": sources, "benchmark_observations": None}
    if baseline is None:
        result["status"] = "stock_baseline_unavailable"
    elif endpoint is None or stock_return is None:
        result["status"] = stock_status if stock_status != "observed" else "stock_return_unavailable"
    elif endpoint <= baseline:
        raise ValueError("stock endpoint must follow its baseline trading date")
    elif ready:
        if baseline < effective_from or (effective_to is not None and endpoint > effective_to):
            result["status"] = "mapping_not_effective_for_window"
        elif baseline not in known:
            result["status"] = "benchmark_baseline_date_unavailable"
        elif endpoint not in known:
            result["status"] = "benchmark_endpoint_date_unavailable"
        else:
            first, last = known[baseline], known[endpoint]
            index_return = _number((last["close"] / first["close"] - 1) * 100, "index_return_pct")
            excess = _number(stock_return - index_return, "excess_return_pp")
            result.update(status="observed", index_return_pct=index_return, excess_return_pp=excess,
                          source_ids=_source_ids(sources + [first["source_id"], last["source_id"]]),
                          benchmark_observations={name: {"source_id": bar["source_id"], "version": bar["version"],
                              "available_at": bar["available_at"].isoformat()} for name, bar in (("baseline", first), ("endpoint", last))})
    return result


def compare_event_to_benchmarks(reaction: dict, benchmarks: dict, as_of: datetime) -> dict:
    """Use latest known revisions without forward fills, nearest dates or model inputs."""
    if not isinstance(as_of, datetime):
        raise ValueError("as_of requires an aware datetime")
    cutoff = _at(as_of, "as_of")
    if not isinstance(reaction, dict) or not isinstance(benchmarks, dict):
        raise ValueError("reaction and benchmarks must be objects")
    if not isinstance(reaction.get("event_id"), str) or not reaction["event_id"].strip():
        raise ValueError("reaction requires event_id")
    if _at(reaction.get("available_at"), "event.available_at") > cutoff:
        raise ValueError("event is not available at as_of")
    if reaction.get("price_basis") != "raw_only" or reaction.get("corporate_action_adjustment") != "unverified":
        raise ValueError("benchmark comparison currently requires raw, unverified-adjustment stock prices")
    sources = _source_ids(reaction.get("source_ids"))
    if not sources:
        raise ValueError("reaction requires source citations")
    baseline = _stock_date(reaction.get("baseline_bar"), cutoff)
    windows = {}
    for horizon in ("1", "3", "5"):
        row = reaction["horizons"][horizon]
        windows[horizon] = (_stock_date(row["bar"], cutoff), row["return_pct"], row["status"])
    latest = (_stock_date(reaction.get("latest_post_event_bar"), cutoff), reaction.get("latest_return_pct"),
              "stock_latest_return_unavailable")
    output = {"version": BENCHMARK_CONTEXT_VERSION, "event_id": reaction["event_id"], "comparison_basis": "same_date_price_index_relative_return",
              "interpretation": "Relative association, not causal alpha, beta-adjusted performance or total return",
              "stock_price_basis": "raw_only", "benchmark_price_basis": "price_index",
              "corporate_action_adjustment": "unverified", "source_ids": sources,
              "data_gaps": ["corporate_action_adjustment_unverified", "dividends_and_total_return_not_adjusted"]}
    for scope in ("market", "sector"):
        benchmark = benchmarks.get(scope)
        if benchmark is not None and not isinstance(benchmark, dict):
            raise ValueError(f"{scope} benchmark must be an object")
        benchmark = benchmark or {"status": "unavailable", "data_gaps": ["benchmark_mapping_unavailable"]}
        if benchmark.get("mapping_available_at") is not None:
            if _at(benchmark["mapping_available_at"], "mapping_available_at") > cutoff:
                benchmark = {"status": "unavailable", "data_gaps": ["mapping_not_available_as_of"]}
        ready = benchmark.get("status") == "ready"
        mapping_id = benchmark.get("mapping_source_id")
        effective_from = effective_to = None
        if ready:
            for key in ("series", "index_name", "mapping_source_id"):
                if not isinstance(benchmark.get(key), str) or not benchmark[key].strip():
                    raise ValueError(f"ready benchmark requires {key}")
            effective_from = _date(benchmark.get("effective_from"), "mapping.effective_from")
            if "effective_to" not in benchmark:
                raise ValueError("ready benchmark requires nullable effective_to")
            effective_to = _date(benchmark["effective_to"], "mapping.effective_to") if benchmark["effective_to"] is not None else None
            if effective_to is not None and effective_to < effective_from:
                raise ValueError("mapping effective_to precedes effective_from")
        elif mapping_id is not None:
            _source_ids([mapping_id])
        known = _known_bars(benchmark["bars"], cutoff, benchmark["series"]) if ready else {}
        used_sources = _source_ids(sources + ([mapping_id] if mapping_id else []))
        comparison = {key: benchmark[key] for key in ("series", "index_name", "mapping_source_id", "mapping_version", "mapping_available_at",
                       "mapping_source_url", "effective_from", "effective_to") if key in benchmark}
        comparison["horizons"] = {horizon: _window(baseline, *values, known, used_sources, ready, effective_from, effective_to)
                                  for horizon, values in windows.items()}
        comparison["latest"] = _window(baseline, *latest, known, used_sources, ready, effective_from, effective_to)
        results = [*comparison["horizons"].values(), comparison["latest"]]
        observed = sum(row["status"] == "observed" for row in results)
        comparison["status"] = "ready" if observed == len(results) else "partial" if observed else "unavailable"
        gaps = benchmark.get("data_gaps", [])
        if not isinstance(gaps, list) or any(not isinstance(gap, str) for gap in gaps):
            raise ValueError("benchmark data_gaps must be strings")
        comparison["data_gaps"] = sorted(set(gaps + [row["status"] for row in results if row["status"] != "observed"]))
        comparison["source_ids"] = _source_ids([source for row in results for source in row["source_ids"]])
        output[scope] = comparison
        output["source_ids"] = _source_ids(output["source_ids"] + comparison["source_ids"])
        output["data_gaps"].extend(f"{scope}:{gap}" for gap in comparison["data_gaps"])
    return output
