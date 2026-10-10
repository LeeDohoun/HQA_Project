"""Backtest sizing: equal-weight reproduction, tilt direction, exposure scaling."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting.fear_greed_sizing import BacktestFearGreed, price_history_at

AS_OF = "20251231"


def frames() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2025-01-01", periods=400, freq="D")
    out = {}
    for code, drift in (("000001", 1.0), ("000002", -1.0), ("000003", 0.0)):
        out[code] = pd.DataFrame(
            {"Close": 10000 + np.arange(400) * drift * 10,
             "Volume": 1000 + np.arange(400) % 50}, index=dates)
    return out


def selected(codes=("000001", "000002", "000003")) -> list[dict]:
    return [{"stock_code": code} for code in codes]


class TestDisabled:
    def test_zero_sensitivity_is_equal_weight(self):
        sizer = BacktestFearGreed("/nonexistent", fg_sensitivity=0.0, regime_sensitivity=0.0)
        result = sizer.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)
        assert result["weights"] == [1 / 3, 1 / 3, 1 / 3]
        assert result["invested_fraction"] == 1.0
        assert result["cash_fraction"] == 0.0
        assert sizer.enabled is False

    def test_no_positions_is_handled(self):
        sizer = BacktestFearGreed("/nonexistent", fg_sensitivity=0.3)
        assert sizer.weights([], theme_key="t", prices={}, as_of_ymd=AS_OF)["weights"] == []

    def test_invalid_sensitivity_is_rejected(self):
        for bad in (-0.1, 1.5, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                BacktestFearGreed("/tmp", fg_sensitivity=bad)
            with pytest.raises(ValueError):
                BacktestFearGreed("/tmp", regime_sensitivity=bad)


class TestStockTilt:
    def test_weights_sum_to_invested_fraction(self):
        sizer = BacktestFearGreed("/nonexistent", fg_sensitivity=0.3)
        result = sizer.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)
        assert abs(sum(result["weights"]) - result["invested_fraction"]) < 1e-9

    def test_falling_stock_reads_as_fear_and_gets_more_weight(self):
        sizer = BacktestFearGreed("/nonexistent", fg_sensitivity=0.3)
        result = sizer.weights(selected(("000001", "000002")), theme_key="t",
                               prices=frames(), as_of_ymd=AS_OF)
        assert result["weights"][1] > result["weights"][0]

    def test_details_carry_the_audit_trail(self):
        sizer = BacktestFearGreed("/nonexistent", fg_sensitivity=0.3)
        result = sizer.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)
        for detail in result["details"]:
            assert {"stock_code", "status", "stock_multiplier",
                    "market_coefficient", "combined_multiplier", "weight"} <= set(detail)


class TestMarketRegime:
    """The market coefficient must change exposure, not relative weights."""

    def test_missing_vkospi_is_neutral(self):
        sizer = BacktestFearGreed("/nonexistent", fg_sensitivity=0.0, regime_sensitivity=0.2)
        result = sizer.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)
        assert result["market_coefficient"] == 1.0
        assert result["weights"] == [1 / 3, 1 / 3, 1 / 3]

    def test_coefficient_scales_exposure_not_ratios(self, tmp_path: Path):
        (tmp_path / "market_context").mkdir(parents=True)
        rows = _vkospi_rows(high_today=True)
        (tmp_path / "market_context" / "volatility_index.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

        off = BacktestFearGreed(tmp_path, fg_sensitivity=0.3, regime_sensitivity=0.0)
        on = BacktestFearGreed(tmp_path, fg_sensitivity=0.3, regime_sensitivity=0.2)
        a = off.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)
        b = on.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)

        assert b["market_coefficient"] < 1.0          # panic sizes down
        assert sum(b["weights"]) < sum(a["weights"])  # less invested overall
        # Relative proportions are unchanged: only total exposure moved.
        ratio_a = [w / sum(a["weights"]) for w in a["weights"]]
        ratio_b = [w / sum(b["weights"]) for w in b["weights"]]
        for x, y in zip(ratio_a, ratio_b):
            assert abs(x - y) < 1e-9

    def test_cash_fraction_complements_invested(self, tmp_path: Path):
        (tmp_path / "market_context").mkdir(parents=True)
        (tmp_path / "market_context" / "volatility_index.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in _vkospi_rows(high_today=True)), encoding="utf-8")
        sizer = BacktestFearGreed(tmp_path, fg_sensitivity=0.0, regime_sensitivity=0.2)
        result = sizer.weights(selected(), theme_key="t", prices=frames(), as_of_ymd=AS_OF)
        assert abs(result["invested_fraction"] + result["cash_fraction"] - 1.0) < 1e-9


class TestPointInTime:
    def test_history_excludes_future_bars(self):
        df = frames()["000001"]
        history = price_history_at(df, "20250301")
        assert len(history) == len(df.loc[df.index <= "2025-03-01"])

    def test_metadata_reports_versions_and_switches(self):
        meta = BacktestFearGreed("/nonexistent", fg_sensitivity=0.3,
                                 regime_sensitivity=0.2).metadata()
        assert meta["enabled"] is True
        assert meta["fear_greed_version"] and meta["market_regime_version"]
        assert meta["fg_sensitivity"] == 0.3 and meta["regime_sensitivity"] == 0.2


def _vkospi_rows(*, high_today: bool) -> list[dict]:
    from datetime import date, datetime, time, timedelta, timezone

    from src.ingestion.krx_volatility import _record
    kst = timezone(timedelta(hours=9))
    rows, day, level = [], date(2024, 1, 1), 15.0
    while len(rows) < 300:
        if day.weekday() < 5:
            rows.append(_record("코스피 200 변동성지수", day, level,
                                datetime.combine(day, time(16, 30), kst)))
            level = min(40.0, level + 0.05)
        day += timedelta(days=1)
    if high_today:
        last = date(2025, 12, 30)
        rows.append(_record("코스피 200 변동성지수", last, 60.0,
                            datetime.combine(last, time(16, 30), kst)))
    return rows
