"""Raw observed daily price response after evidence became usable, not causality."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
KST = timezone(timedelta(hours=9))


def _aware(value, field: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    return value.astimezone(UTC)


def _number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (field != "volume" and number == 0):
        raise ValueError(f"Invalid OHLCV {field}")
    return number


def _snapshot(bar: dict | None) -> dict | None:
    return {"available_at": bar["at"].isoformat(), "close": bar["close"]} if bar else None


def _ratio(numerator: float, denominator: float) -> float:
    value = numerator / denominator
    if not math.isfinite(value):
        raise ValueError("Observed reaction ratio exceeds finite numeric range")
    return value


def calculate_event_reaction(event: dict, price_history: list[dict], as_of: datetime,
                             price_source_id: str) -> dict:
    """Horizon sessions count supplied completed bars, without inferring missing dates."""
    if not isinstance(event, dict) or not isinstance(event.get("event_id"), str) or not event["event_id"].strip():
        raise ValueError("event_id must be a nonblank string")
    if not isinstance(price_source_id, str) or not price_source_id.strip():
        raise ValueError("price_source_id must be a nonblank string")
    if not isinstance(as_of, datetime):
        raise ValueError("as_of must be a timezone-aware datetime")
    cutoff = _aware(as_of, "as_of")
    available = _aware(event.get("available_at"), "event.available_at")
    if available > cutoff:
        raise ValueError("Event was not available at as_of")
    if not isinstance(price_history, list):
        raise ValueError("price_history must be a list of completed daily OHLCV bars")
    by_day = {}
    for row in price_history:
        if not isinstance(row, dict):
            raise ValueError("Each OHLCV bar must be an object")
        at = _aware(row.get("available_at"), "price.available_at")
        if at > cutoff:
            continue
        bar = {"at": at, **{field: _number(row.get(field), field) for field in ("open", "high", "low", "close", "volume")}}
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            raise ValueError("Inconsistent OHLC prices")
        day = at.astimezone(KST).date()
        if day in by_day and by_day[day] != bar:
            raise ValueError("Conflicting completed daily bars for the same Korean trading date")
        by_day[day] = bar
    known = sorted(by_day.values(), key=lambda bar: bar["at"])
    prior = [bar for bar in known if bar["at"] < available]
    post = [bar for bar in known if bar["at"] > available]
    baseline = prior[-1] if prior else None
    gaps = ["benchmark_unavailable", "corporate_action_adjustment_unverified"]
    if baseline is None:
        gaps.append("prior_close_unavailable")
    if not post:
        gaps.append("post_event_bar_unavailable")

    def reaction(bar):
        if bar is None or baseline is None:
            return None
        value = (_ratio(bar["close"], baseline["close"]) - 1) * 100
        if not math.isfinite(value):
            raise ValueError("Observed return exceeds finite numeric range")
        return value

    horizons = {}
    for sessions in (1, 3, 5):
        bar = post[sessions - 1] if len(post) >= sessions else None
        status = "observed" if bar and baseline else "prior_close_unavailable" if bar else "insufficient_post_event_bars"
        horizons[str(sessions)] = {"status": status, "return_pct": reaction(bar), "bar": _snapshot(bar)}
        if bar is None:
            gaps.append(f"session_{sessions}_unavailable")
    volume_prior = prior[-20:]
    mean_volume = math.fsum(bar["volume"] / 20 for bar in volume_prior) if len(volume_prior) == 20 else None
    volume_ratio = None
    if len(volume_prior) < 20:
        volume_status = "insufficient_pre_event_volume_history"
    elif mean_volume == 0:
        volume_status = "zero_pre_event_mean_volume"
    elif not post:
        volume_status = "post_event_bar_unavailable"
    else:
        volume_status = "observed"
        volume_ratio = _ratio(post[0]["volume"], mean_volume)
    if volume_status != "observed" and volume_status not in gaps:
        gaps.append(volume_status)
    status = "unavailable" if baseline is None or not post else "ready" if len(post) >= 5 and volume_ratio is not None else "partial"
    return {"event_id": event["event_id"], "available_at": available.isoformat(),
            "source_ids": [event["event_id"], price_source_id], "status": status,
            "interpretation": "Observed response since first usable evidence, not a causal or abnormal return",
            "horizon_basis": "supplied completed daily bars, not calendar days",
            "price_basis": "raw_only", "corporate_action_adjustment": "unverified",
            "market_adjusted_return_pct": None, "baseline_bar": _snapshot(baseline),
            "as_of_bar": _snapshot(known[-1] if known else None), "post_event_bar_count": len(post),
            "horizons": horizons, "latest_return_pct": reaction(post[-1] if post else None),
            "latest_post_event_bar": _snapshot(post[-1] if post else None),
            "volume_reaction": {"status": volume_status, "ratio": volume_ratio,
                                "first_post_event_volume": post[0]["volume"] if post else None,
                                "baseline_mean_volume": mean_volume, "baseline_bar_count": len(volume_prior),
                                "baseline_start_at": volume_prior[0]["at"].isoformat() if volume_prior else None,
                                "baseline_end_at": volume_prior[-1]["at"].isoformat() if volume_prior else None},
            "data_gaps": gaps}
