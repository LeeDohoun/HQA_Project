from __future__ import annotations

# File role:
# - Source-based retrieval weighting and freshness decay.
# - Applied between retrieval and reranking to adjust document scores
#   based on source credibility and temporal freshness.

"""Source weighting and freshness decay for canonical RAG retrieval."""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ── Source credibility weights ──
# Higher = more trusted for investment decisions
SOURCE_WEIGHTS: Dict[str, float] = {
    "report": 1.0,
    "dart": 0.95,
    "news": 0.80,
    "general_news": 0.75,
    "forum": 0.35,
}

# ── Freshness decay configuration ──
# Days → bonus/penalty multiplier
FRESHNESS_CONFIG = {
    "news": {
        "boost_days": 7,        # News < 7 days → bonus
        "boost_factor": 1.3,    # 30% boost for very recent news
        "decay_days": 60,       # News > 60 days → penalty
        "decay_factor": 0.6,    # 40% penalty for old news
    },
    "general_news": {
        "boost_days": 7,
        "boost_factor": 1.3,
        "decay_days": 60,
        "decay_factor": 0.6,
    },
    "dart": {
        "boost_days": 14,       # Recent disclosures get a bonus
        "boost_factor": 1.2,
        "decay_days": 180,
        "decay_factor": 0.8,    # DART retains value longer
    },
    "report": {
        "boost_days": 30,       # Reports stay relevant longer
        "boost_factor": 1.15,
        "decay_days": 365,
        "decay_factor": 0.7,
    },
    "forum": {
        "boost_days": 3,        # Forum posts lose relevance fast
        "boost_factor": 1.1,
        "decay_days": 30,
        "decay_factor": 0.4,    # Old forum posts heavily penalised
    },
}

# ── Query-intent → preferred source filter ──
INTENT_SOURCE_MAP: Dict[str, List[str]] = {
    "earnings": ["report", "dart", "news"],
    "investment": ["report", "dart", "news"],
    "policy": ["dart", "news", "report"],
    "regulation": ["dart", "news", "report"],
    "sentiment": ["news", "forum"],
    "industry": ["report", "news"],
    "default": ["report", "dart", "news", "general_news", "forum"],
}


def get_source_weight(source_type: str) -> float:
    """Return the base credibility weight for a source type."""
    return SOURCE_WEIGHTS.get(source_type.strip().lower(), 0.5)


def compute_freshness_multiplier(
    source_type: str,
    published_at: str,
    reference_date: Optional[datetime] = None,
) -> float:
    """Compute a freshness multiplier (> 1.0 = boost, < 1.0 = penalty)."""
    source = source_type.strip().lower()
    config = FRESHNESS_CONFIG.get(source)
    if not config:
        return 1.0  # no decay for unknown sources

    if not published_at:
        return 0.8  # unknown date → slight penalty

    ref = reference_date or datetime.now(tz=None)

    try:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y%m%d", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(published_at.strip()[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return 0.8

        delta_days = (ref - dt).days

        if delta_days < 0:
            return config["boost_factor"]
        if delta_days <= config["boost_days"]:
            return config["boost_factor"]
        if delta_days <= config["decay_days"]:
            return 1.0  # neutral range
        return config["decay_factor"]

    except Exception:
        return 0.8


def apply_source_weighting(
    results: List[Dict],
    reference_date: Optional[datetime] = None,
) -> List[Dict]:
    """Apply source credibility and freshness weighting to retrieval results.

    Each result dict must have:
        - "score": float — base retrieval score
        - "source_type": str
        - "metadata": dict (with optional "published_at")

    Modifies results in-place and returns them sorted by weighted_score descending.
    """
    for result in results:
        source_type = str(result.get("source_type", "") or "").strip().lower()
        metadata = result.get("metadata") or {}

        base_score = float(result.get("score", 0.0))
        credibility = get_source_weight(source_type)

        published_at = str(metadata.get("published_at", "") or "").strip()
        freshness_mult = compute_freshness_multiplier(source_type, published_at, reference_date)

        # Combined weighted score
        weighted_score = base_score * credibility * freshness_mult

        result["weighted_score"] = weighted_score
        result["credibility_weight"] = credibility
        result["freshness_multiplier"] = freshness_mult

    results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
    return results


def get_intent_sources(intent: str) -> List[str]:
    """Return the preferred source types for a given query intent."""
    return INTENT_SOURCE_MAP.get(intent.strip().lower(), INTENT_SOURCE_MAP["default"])
