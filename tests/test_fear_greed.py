"""Fear/greed index: percentile behaviour, missing inputs, and sizing tilt."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runner.fear_greed import (FEAR_GREED_VERSION, fear_greed, position_multiplier)

UTC = timezone.utc
AS_OF = datetime(2026, 9, 20, tzinfo=UTC)


def series(count: int = 300, *, rising: bool = True) -> list[dict]:
    return [{"close": (100 + i) if rising else (100 + count - i), "volume": 1000 + (i % 50)}
            for i in range(count)]


def posts(count: int, *, within_days: int = 25) -> list[dict]:
    return [{"published_at": (AS_OF - timedelta(days=i % within_days)).isoformat()} for i in range(count)]


def shorts(count: int = 200) -> list[dict]:
    return [{"short_ratio": 0.01 + (i % 40) / 1000} for i in range(count)]


class TestAvailability:
    def test_short_history_is_explicit_not_neutral(self):
        result = fear_greed(series(100), AS_OF)
        assert result["status"] == "insufficient_history"
        assert "score" not in result

    def test_naive_as_of_is_rejected(self):
        with pytest.raises(ValueError):
            fear_greed(series(), datetime(2026, 9, 20))

    def test_price_only_uses_three_components(self):
        result = fear_greed(series(), AS_OF)
        assert result["status"] == "ready"
        assert set(result["weights"]) == {"trend", "volatility", "activity"}
        assert result["version"] == FEAR_GREED_VERSION

    def test_weights_always_sum_to_one(self):
        for kwargs in ({}, {"forum_posts": posts(200)},
                       {"forum_posts": posts(200), "short_sale": shorts()}):
            result = fear_greed(series(), AS_OF, **kwargs)
            assert abs(sum(result["weights"].values()) - 1.0) < 1e-9

    def test_thin_social_is_unavailable_not_zero(self):
        result = fear_greed(series(), AS_OF, forum_posts=posts(5))
        assert result["social"]["status"] == "insufficient_posts"
        assert "social" not in result["components"]

    def test_thin_short_history_is_unavailable(self):
        result = fear_greed(series(), AS_OF, short_sale=shorts(10))
        assert result["short_interest"]["status"] == "insufficient_history"
        assert "short_interest" not in result["components"]

    def test_future_posts_are_excluded(self):
        future = [{"published_at": (AS_OF + timedelta(days=d)).isoformat()} for d in range(1, 60)]
        assert fear_greed(series(), AS_OF, forum_posts=future)["social"]["status"] != "ready"


class TestDirection:
    def test_rising_scores_greedier_than_falling(self):
        hot = fear_greed(series(rising=True), AS_OF)["score"]
        cold = fear_greed(series(rising=False), AS_OF)["score"]
        assert hot > cold

    def test_score_stays_in_range(self):
        for rising in (True, False):
            result = fear_greed(series(rising=rising), AS_OF,
                                forum_posts=posts(300), short_sale=shorts())
            assert 0.0 <= result["score"] <= 100.0

    def test_labels_follow_thresholds(self):
        assert fear_greed(series(), AS_OF)["label"] in {"extreme_fear", "neutral", "extreme_greed"}


class TestPositionMultiplier:
    def test_fear_increases_and_greed_decreases(self):
        assert position_multiplier({"status": "ready", "score": 0.0}, 0.3) == 1.3
        assert position_multiplier({"status": "ready", "score": 100.0}, 0.3) == 0.7
        assert position_multiplier({"status": "ready", "score": 50.0}, 0.3) == 1.0

    def test_zero_sensitivity_disables_the_tilt(self):
        for score in (0.0, 25.0, 50.0, 75.0, 100.0):
            assert position_multiplier({"status": "ready", "score": score}, 0.0) == 1.0

    def test_unavailable_index_never_tilts(self):
        for index in ({"status": "insufficient_history"}, {"status": "failed"}, {}, {"status": "no_components"}):
            assert position_multiplier(index, 0.5) == 1.0

    def test_invalid_sensitivity_is_rejected(self):
        for bad in (-0.1, 1.1, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                position_multiplier({"status": "ready", "score": 50.0}, bad)

    def test_multiplier_is_bounded_by_sensitivity(self):
        for score in range(0, 101, 5):
            value = position_multiplier({"status": "ready", "score": float(score)}, 0.3)
            assert 0.7 <= value <= 1.3
