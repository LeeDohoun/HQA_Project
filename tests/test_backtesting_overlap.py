"""Holdings that outlast the rebalance interval share capital through equal sleeves."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from backtesting.metrics import SleevedEquity, sleeve_count


@pytest.mark.parametrize("rebalance,hold_days,expected", [
    ("W", 5, 1), ("weekly", 20, 4), ("M", 20, 1), ("M", 60, 3), ("D", 5, 5), ("W", 10, 2),
    ("monthly", 60, 3),   # the date selection treats any other label as month-end
])
def test_sleeves_cover_every_rebalance_a_holding_spans(rebalance, hold_days, expected):
    assert sleeve_count(rebalance, hold_days) == expected


def test_one_sleeve_is_plain_compounding_and_overlapping_cohorts_share_capital():
    single, four = SleevedEquity(1), SleevedEquity(4)
    for index in range(4):
        single.add(index, 0.10)
        four.add(index, 0.10)
    assert single.equity == pytest.approx(1.1 ** 4)
    # Four overlapping cohorts each earned 10% on a quarter of the capital: 10% in total.
    assert four.equity == pytest.approx(1.10)
    with pytest.raises(ValueError):
        SleevedEquity(0)


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _run(tmp_path, hold_days):
    pd = pytest.importorskip("pandas")
    from backtesting.leader_backtest import run_leader_backtest

    data = tmp_path / f"h{hold_days}"
    _write_jsonl(data / "raw" / "theme_targets" / "ai.jsonl", [
        {"stock_name": name, "stock_code": code} for name, code in (("Alpha", "000001"), ("Beta", "000002"), ("Gamma", "000003"))])
    rows = []
    for i, day in enumerate(pd.bdate_range("2025-01-01", periods=120)):
        for name, code, slope in (("Alpha", "000001", 2.0), ("Beta", "000002", 0.4), ("Gamma", "000003", -0.2)):
            close = 100 + i * slope
            rows.append({"source_type": "chart", "stock_name": name, "stock_code": code,
                         "timestamp": day.strftime("%Y-%m-%dT00:00:00"), "open": close - 1, "high": close + 2,
                         "low": close - 2, "close": close, "volume": 1000 + i})
    _write_jsonl(data / "market_data" / "ai" / "chart.jsonl", rows)
    return run_leader_backtest(data_dir=data, theme="AI", theme_key="ai", from_date="20250301", to_date="20250530",
                               rebalance="W", top_n=1, hold_days=hold_days, min_history_days=20,
                               output_dir=data / "results", task_id=f"bt-overlap-h{hold_days}")


def _compounded(periods, sleeves):
    values = [1.0] * sleeves
    for index, period in enumerate(periods):
        values[index % sleeves] *= 1 + period["portfolio_return_pct"] / 100
    return sum(values) / sleeves


def test_weekly_rebalancing_with_20_day_holds_no_longer_compounds_every_overlapping_cohort(tmp_path):
    result = _run(tmp_path, 20)
    periods = result["periods"]
    assert result["period"]["capital_sleeves"] == 4 and len(periods) >= 8
    final = result["equity_curve"][-1]["equity"]
    assert final == pytest.approx(_compounded(periods, 4), rel=1e-3)   # periods keep 2-decimal percents
    # The old engine compounded each overlapping 20-day cohort on the full capital.
    assert final < _compounded(periods, 1)
    assert result["metrics"]["total_return_pct"] == pytest.approx((final - 1) * 100, abs=0.01)


def test_non_overlapping_schedules_keep_plain_compounding(tmp_path):
    result = _run(tmp_path, 5)
    assert result["period"]["capital_sleeves"] == 1
    assert result["equity_curve"][-1]["equity"] == pytest.approx(_compounded(result["periods"], 1), rel=1e-3)
    assert math.isfinite(result["metrics"]["mdd_pct"])


def test_rebalances_fall_on_real_period_ends_never_on_the_data_cut_off():
    pd = pytest.importorskip("pandas")
    from backtesting.leader_backtest import _build_common_calendar, _evaluable_rebalance_dates

    index = pd.bdate_range("2025-01-01", "2025-06-17")   # data ends mid-June
    prices = {"000001": pd.DataFrame({"close": range(len(index))}, index=index)}
    calendar = _build_common_calendar(prices, "20250101", "20251231", 20)
    months = _evaluable_rebalance_dates(prices, "20250101", "20251231", calendar, "M")
    # May 30 has fewer than 20 sessions after it; the cut-off (around May 20) is not a month end.
    assert months == ["20250131", "20250228", "20250331", "20250430"]
    weeks = _evaluable_rebalance_dates(prices, "20250101", "20251231", calendar, "W")
    assert all(pd.Timestamp(day).dayofweek == 4 for day in weeks)   # every week here ends on a Friday


def test_technical_baseline_sweeps_use_explicit_dates_instead_of_default_periods(monkeypatch, tmp_path):
    from backtesting import technical_baseline

    seen = {}
    monkeypatch.setattr(technical_baseline, "run_baseline_sweep",
                        lambda **kwargs: seen.update(kwargs) or {"artifacts": {}})
    technical_baseline.main(["--data-dir", str(tmp_path), "--from-date", "20260102", "--to-date", "20261008"])
    assert seen["periods"] == [{"name": "custom", "from_date": "20260102", "to_date": "20261008"}]
    technical_baseline.main(["--data-dir", str(tmp_path)])
    assert [period["name"] for period in seen["periods"]] == ["full", "2023", "2024", "2025", "2026q1"]


def test_a_run_ending_mid_week_or_mid_month_does_not_rebalance_on_its_end_date():
    pd = pytest.importorskip("pandas")
    from backtesting.leader_backtest import _build_common_calendar, _evaluable_rebalance_dates

    closed = {"20251225", "20251231", "20260101"}
    index = pd.DatetimeIndex([day for day in pd.bdate_range("2025-10-01", "2026-03-31") if day.strftime("%Y%m%d") not in closed])
    prices = {"000001": pd.DataFrame({"close": range(len(index))}, index=index)}
    calendar = _build_common_calendar(prices, "20251001", "20251231", 5)
    weeks = _evaluable_rebalance_dates(prices, "20251001", "20251231", calendar, "W")
    # The week of Dec 29 ends on Jan 2, after the run: Dec 30 is only the run's last session.
    assert weeks[-1] == "20251226"
    months = _evaluable_rebalance_dates(prices, "20251001", "20251215", _build_common_calendar(prices, "20251001", "20251215", 5), "M")
    assert months[-1] == "20251128"   # December ends after the run
