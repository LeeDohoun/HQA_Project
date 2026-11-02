"""Market regime coefficient: direction, neutrality, and removability.

The removability tests are the point of this file: backtests must be able to turn
VKOSPI off and get numerically identical results to a build without it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from src.runner.market_regime import (MARKET_REGIME_VERSION, load_market_regime,
                                      market_regime, regime_coefficient, regime_enabled)

UTC = timezone.utc
AS_OF = datetime(2026, 9, 20, tzinfo=UTC)


def history(values: list[float]) -> list[dict]:
    return [{"close": float(v), "trade_date": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}",
             "source_id": f"krx-vkospi:{i:04d}"} for i, v in enumerate(values)]


def calm() -> dict:
    return market_regime(history(list(range(40, 15, -1)) * 8 + [14.0]), AS_OF)


def panic() -> dict:
    return market_regime(history(list(range(15, 40)) * 8 + [45.0]), AS_OF)


class TestAvailability:
    def test_short_history_is_explicit(self):
        result = market_regime(history([20.0] * 50), AS_OF)
        assert result["status"] == "insufficient_history"
        assert "vkospi_percentile" not in result

    def test_ready_regime_reports_provenance(self):
        result = calm()
        assert result["status"] == "ready"
        assert result["version"] == MARKET_REGIME_VERSION
        assert result["source_id"].startswith("krx-vkospi:")
        assert 0.0 <= result["vkospi_percentile"] <= 100.0

    def test_naive_as_of_is_rejected(self):
        with pytest.raises(ValueError):
            market_regime(history([20.0] * 300), datetime(2026, 9, 20))

    def test_missing_file_degrades_without_raising(self):
        assert load_market_regime("/nonexistent", AS_OF)["status"] in {"insufficient_history", "failed"}


class TestDirection:
    def test_calm_scores_lower_than_panic(self):
        assert calm()["vkospi_percentile"] < panic()["vkospi_percentile"]

    def test_calm_sizes_up_and_panic_sizes_down(self):
        assert regime_coefficient(calm(), 0.2) > 1.0
        assert regime_coefficient(panic(), 0.2) < 1.0

    def test_median_regime_is_neutral(self):
        assert regime_coefficient({"status": "ready", "vkospi_percentile": 50.0}, 0.2) == 1.0

    def test_coefficient_is_bounded_by_sensitivity(self):
        for percentile in range(0, 101, 5):
            value = regime_coefficient({"status": "ready", "vkospi_percentile": float(percentile)}, 0.2)
            assert 0.8 <= value <= 1.2


class TestRemovability:
    """VKOSPI must be removable without changing any other number."""

    def test_zero_sensitivity_is_exactly_neutral(self):
        for regime in (calm(), panic()):
            assert regime_coefficient(regime, 0.0) == 1.0

    def test_every_unavailable_status_is_exactly_neutral(self):
        for regime in ({"status": "insufficient_history"}, {"status": "failed"},
                       {"status": "disabled"}, {"status": "undefined_percentile"},
                       {"status": "unavailable"}, {}):
            assert regime_coefficient(regime, 0.5) == 1.0, regime

    def test_env_switch_disables_without_reading_data(self):
        os.environ["HQA_MARKET_REGIME"] = "off"
        try:
            assert regime_enabled() is False
            result = market_regime(history([20.0] * 300), AS_OF)
            assert result["status"] == "disabled"
            assert regime_coefficient(result, 0.5) == 1.0
            assert load_market_regime("/any/path", AS_OF)["status"] == "disabled"
        finally:
            os.environ.pop("HQA_MARKET_REGIME", None)

    def test_env_switch_accepts_common_spellings(self):
        for value in ("off", "OFF", "0", "false", "no", "False"):
            os.environ["HQA_MARKET_REGIME"] = value
            try:
                assert regime_enabled() is False, value
            finally:
                os.environ.pop("HQA_MARKET_REGIME", None)

    def test_enabled_by_default(self):
        os.environ.pop("HQA_MARKET_REGIME", None)
        assert regime_enabled() is True

    def test_invalid_sensitivity_is_rejected(self):
        for bad in (-0.1, 1.1, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                regime_coefficient(calm(), bad)
