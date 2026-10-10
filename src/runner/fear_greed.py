"""Per-stock fear/greed index from observed price, flow and social inputs.

Every component is a percentile of the stock's own trailing year, so a level is
read against that stock's history rather than an absolute threshold. 0 is extreme
fear, 100 extreme greed. Missing inputs are reported as unavailable and their
weight is redistributed; they are never scored as zero or as neutral 50.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd

FEAR_GREED_VERSION = "fg-v1-percentile"
KST = timezone(timedelta(hours=9))
LOOKBACK_SESSIONS = 252
MIN_SESSIONS = 252
SOCIAL_WINDOW_DAYS = 3
SOCIAL_BASELINE_DAYS = 30
MIN_SOCIAL_POSTS = 20

# Correlated price measures are averaged inside one component before weighting,
# so "price went up" is not counted three times.
BASE_WEIGHTS = {"trend": 0.40, "volatility": 0.30, "activity": 0.30}
FULL_WEIGHTS = {"trend": 0.35, "volatility": 0.25, "activity": 0.25, "social": 0.15}
SHORT_WEIGHTS = {"trend": 0.30, "volatility": 0.20, "activity": 0.20, "social": 0.15, "short_interest": 0.15}
EXTREME_FEAR_BELOW = 20.0
EXTREME_GREED_ABOVE = 80.0


def _percentile(series: pd.Series) -> float | None:
    """Rank of the latest value within the trailing window, as 0-100."""
    window = series.dropna().tail(LOOKBACK_SESSIONS)
    if len(window) < MIN_SESSIONS // 2:
        return None
    value = float(window.rank(pct=True).iloc[-1] * 100)
    return value if math.isfinite(value) else None


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fear/greed timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _price_components(price_history: list[dict]) -> tuple[dict, list[str]]:
    close = pd.Series([float(row["close"]) for row in price_history], dtype="float64")
    volume = pd.Series([float(row["volume"]) for row in price_history], dtype="float64")
    gaps: list[str] = []

    momentum = close / close.shift(60) - 1                      # 주가 모멘텀
    disparity = close / close.rolling(20).mean() - 1            # 이격도
    strength = close.diff().clip(lower=0).rolling(14).mean() / (
        close.diff().abs().rolling(14).mean().replace(0, pd.NA))  # 주가 강도 (RSI 형태)
    volatility = close.pct_change().rolling(20).std() * math.sqrt(252)
    volume_ratio = volume / volume.rolling(20).mean()
    obv = (close.diff().apply(lambda d: 0.0 if d == 0 or pd.isna(d) else math.copysign(1.0, d)) * volume).cumsum()
    obv_slope = obv.diff(20) / volume.rolling(20).mean().replace(0, pd.NA)

    trend_parts = {"momentum": _percentile(momentum), "disparity": _percentile(disparity),
                   "strength": _percentile(strength)}
    activity_parts = {"volume_ratio": _percentile(volume_ratio), "obv_slope": _percentile(obv_slope)}
    volatility_pct = _percentile(volatility)

    components: dict[str, float] = {}
    available_trend = [v for v in trend_parts.values() if v is not None]
    if available_trend:
        components["trend"] = sum(available_trend) / len(available_trend)
    else:
        gaps.append("trend_unavailable")
    if volatility_pct is not None:
        # High realized volatility is fear, so the percentile is inverted.
        components["volatility"] = 100.0 - volatility_pct
    else:
        gaps.append("volatility_unavailable")
    available_activity = [v for v in activity_parts.values() if v is not None]
    if available_activity:
        components["activity"] = sum(available_activity) / len(available_activity)
    else:
        gaps.append("activity_unavailable")

    gaps.extend(f"undefined_factor:{name}" for name, value in
                {**trend_parts, **activity_parts}.items() if value is None)
    return components, gaps


def _social_component(forum_posts: list[dict], as_of: datetime) -> tuple[float | None, dict]:
    """Mention volume relative to the stock's own baseline. Volume, not opinion."""
    detail = {"status": "unavailable"}
    if not forum_posts:
        return None, detail
    times = []
    for post in forum_posts:
        published = post.get("published_at")
        if not published:
            continue
        try:
            stamp = _aware(published) if "+" in str(published) or "Z" in str(published) else \
                datetime.fromisoformat(str(published)).replace(tzinfo=KST).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
        if stamp <= as_of:
            times.append(stamp)
    if len(times) < MIN_SOCIAL_POSTS:
        detail = {"status": "insufficient_posts", "post_count": len(times)}
        return None, detail

    recent = [t for t in times if as_of - t <= timedelta(days=SOCIAL_WINDOW_DAYS)]
    baseline_posts = [t for t in times if as_of - t <= timedelta(days=SOCIAL_BASELINE_DAYS)]
    baseline_daily = len(baseline_posts) / SOCIAL_BASELINE_DAYS
    if baseline_daily <= 0:
        detail = {"status": "no_baseline", "post_count": len(times)}
        return None, detail

    ratio = (len(recent) / SOCIAL_WINDOW_DAYS) / baseline_daily
    # A 3x surge in chatter maps to the greed end; quiet boards map to fear.
    score = max(0.0, min(100.0, 50.0 * ratio))
    detail = {"status": "ready", "post_count_recent": len(recent),
              "baseline_posts_per_day": round(baseline_daily, 3),
              "volume_ratio": round(ratio, 3)}
    return score, detail


def _short_interest_component(short_sale: list[dict]) -> tuple[float | None, dict]:
    """Rising short ratio is fear; falling is greed. Percentile of own history."""
    if not short_sale:
        return None, {"status": "unavailable"}
    ratios = pd.Series([row["short_ratio"] for row in short_sale
                        if row.get("short_ratio") is not None], dtype="float64")
    if len(ratios) < 60:
        return None, {"status": "insufficient_history", "observations": int(len(ratios))}
    percentile = _percentile(ratios)
    if percentile is None:
        return None, {"status": "insufficient_history", "observations": int(len(ratios))}
    return 100.0 - percentile, {"status": "ready", "observations": int(len(ratios)),
                                "latest_short_ratio": round(float(ratios.iloc[-1]), 6),
                                "short_ratio_percentile": round(percentile, 1)}


def fear_greed(price_history: list[dict], as_of: datetime, *,
               forum_posts: list[dict] | None = None,
               short_sale: list[dict] | None = None) -> dict:
    """Returns the per-stock fear/greed index, or an explicit unavailable status."""
    as_of = _aware(as_of)
    if not isinstance(price_history, list) or len(price_history) < MIN_SESSIONS:
        return {"status": "insufficient_history", "version": FEAR_GREED_VERSION,
                "sessions": len(price_history) if isinstance(price_history, list) else 0,
                "required": MIN_SESSIONS}

    components, gaps = _price_components(price_history)
    social, social_detail = _social_component(forum_posts or [], as_of)
    short_score, short_detail = _short_interest_component(short_sale or [])

    if social is not None:
        components["social"] = social
    if short_score is not None:
        components["short_interest"] = short_score

    if short_score is not None:
        weights = dict(SHORT_WEIGHTS)
    elif social is not None:
        weights = dict(FULL_WEIGHTS)
    else:
        weights = dict(BASE_WEIGHTS)

    present = {name: weight for name, weight in weights.items() if name in components}
    if not present:
        return {"status": "no_components", "version": FEAR_GREED_VERSION, "data_gaps": gaps}
    # Redistribute a missing component's weight instead of scoring it as neutral.
    total = sum(present.values())
    normalized = {name: weight / total for name, weight in present.items()}
    score = sum(components[name] * normalized[name] for name in normalized)

    return {
        "status": "ready",
        "version": FEAR_GREED_VERSION,
        "as_of": as_of.isoformat(),
        "score": round(score, 1),
        "label": ("extreme_fear" if score < EXTREME_FEAR_BELOW
                  else "extreme_greed" if score > EXTREME_GREED_ABOVE else "neutral"),
        "components": {name: round(value, 1) for name, value in components.items()},
        "weights": {name: round(weight, 4) for name, weight in normalized.items()},
        "social": social_detail,
        "short_interest": short_detail,
        "data_gaps": gaps,
        "interpretation": ("Percentile of this stock's own trailing year. "
                           "0=extreme fear, 100=extreme greed. Observed association only, "
                           "not a forecast of return."),
    }


def position_multiplier(index: dict, sensitivity: float) -> float:
    """Continuous position tilt: fear scales up, greed scales down.

    Returns 1.0 (no adjustment) when the index is unavailable or sensitivity is 0.
    """
    if not isinstance(sensitivity, (int, float)) or not math.isfinite(float(sensitivity)):
        raise ValueError("fear/greed sensitivity must be a finite number")
    if not 0.0 <= float(sensitivity) <= 1.0:
        raise ValueError("fear/greed sensitivity must be between 0 and 1")
    if not isinstance(index, dict) or index.get("status") != "ready" or not sensitivity:
        return 1.0
    tilt = (50.0 - float(index["score"])) / 50.0      # +1.0 at max fear, -1.0 at max greed
    return round(1.0 + tilt * float(sensitivity), 6)


def demo() -> None:
    """Self-check: percentile behaviour, weight redistribution, tilt direction."""
    base = datetime(2026, 9, 20, tzinfo=timezone.utc)

    rising = [{"close": 100 + i, "volume": 1000 + i} for i in range(300)]
    falling = [{"close": 400 - i, "volume": 1000 + i} for i in range(300)]
    hot = fear_greed(rising, base)
    cold = fear_greed(falling, base)
    assert hot["status"] == "ready" and cold["status"] == "ready"
    assert hot["score"] > cold["score"], (hot["score"], cold["score"])

    # Insufficient history is explicit, never a neutral 50.
    assert fear_greed(rising[:100], base)["status"] == "insufficient_history"

    # Missing social/short: weights fall back to the price-only set and sum to 1.
    assert set(hot["weights"]) == {"trend", "volatility", "activity"}
    assert abs(sum(hot["weights"].values()) - 1.0) < 1e-6

    # Social present: weight is redistributed across four components.
    posts = [{"published_at": (base - timedelta(days=d % 25)).isoformat()} for d in range(200)]
    withsocial = fear_greed(rising, base, forum_posts=posts)
    assert "social" in withsocial["components"], withsocial["social"]
    assert abs(sum(withsocial["weights"].values()) - 1.0) < 1e-6

    # Too few posts is unavailable, not zero.
    assert fear_greed(rising, base, forum_posts=posts[:5])["social"]["status"] == "insufficient_posts"

    # Short interest present: five components.
    shorts = [{"short_ratio": 0.01 + (i % 50) / 1000, "trade_date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"}
              for i in range(200)]
    withshort = fear_greed(rising, base, forum_posts=posts, short_sale=shorts)
    assert "short_interest" in withshort["components"], withshort["short_interest"]
    assert abs(sum(withshort["weights"].values()) - 1.0) < 1e-6

    # Tilt: fear scales position up, greed scales it down, and 0 disables it.
    assert position_multiplier({"status": "ready", "score": 0.0}, 0.3) == 1.3
    assert position_multiplier({"status": "ready", "score": 100.0}, 0.3) == 0.7
    assert position_multiplier({"status": "ready", "score": 50.0}, 0.3) == 1.0
    assert position_multiplier({"status": "ready", "score": 0.0}, 0.0) == 1.0
    assert position_multiplier({"status": "insufficient_history"}, 0.3) == 1.0

    for bad in (-0.1, 1.5, float("nan")):
        try:
            position_multiplier({"status": "ready", "score": 50.0}, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"sensitivity {bad} must be rejected")

    print("fear_greed demo OK",
          {"hot": hot["score"], "cold": cold["score"], "components": list(withshort["components"])})


if __name__ == "__main__":
    demo()
