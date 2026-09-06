from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from src.runner.event_reaction import calculate_event_reaction

KST = timezone(timedelta(hours=9))


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=KST)


def bar(day, close=100.0, volume=200.0):
    return {"available_at": at(day + "T15:30:00").isoformat(), "open": close,
            "high": close + 1, "low": close - 1, "close": close, "volume": volume}


def event(timestamp):
    return {"event_id": "event:disclosure-1", "available_at": at(timestamp).isoformat()}


def full_history():
    days = [datetime(2026, 8, 3) + timedelta(days=i) for i in range(40)]
    days = [day.date().isoformat() for day in days if day.weekday() < 5][:26]
    rows = [bar(day) for day in days]
    for offset, close in enumerate((110.0, 115.0, 120.0, 95.0, 90.0, 105.0), start=20):
        rows[offset] = bar(days[offset], close, 600.0 if offset == 20 else 10000.0)
    return rows


@pytest.mark.parametrize("timestamp,baseline,first,expected", [
    ("2026-09-04T12:00:00", "2026-09-03", "2026-09-04", 5.0),
    ("2026-09-04T15:30:00", "2026-09-03", "2026-09-07", 10.0),
    ("2026-09-04T16:00:00", "2026-09-04", "2026-09-07", (110 / 105 - 1) * 100),
    ("2026-09-05T12:00:00", "2026-09-04", "2026-09-07", (110 / 105 - 1) * 100),
])
def test_midday_equal_close_after_close_and_weekend_use_strict_availability(timestamp, baseline, first, expected):
    rows = [bar("2026-09-02", 90), bar("2026-09-03", 100), bar("2026-09-04", 105),
            bar("2026-09-07", 110), bar("2026-09-08", 115)]
    result = calculate_event_reaction(event(timestamp), rows, at("2026-09-08T16:00:00"), "price:raw-1")
    assert result["baseline_bar"]["available_at"].startswith(baseline)
    assert result["horizons"]["1"]["bar"]["available_at"].startswith(first)
    assert result["horizons"]["1"]["return_pct"] == pytest.approx(expected)
    assert result["volume_reaction"]["baseline_end_at"].startswith(baseline)


def test_completed_horizons_latest_return_and_only_prior_20_volumes():
    rows = full_history()
    available = datetime.fromisoformat(rows[20]["available_at"]) - timedelta(hours=1)
    result = calculate_event_reaction({"event_id": "event-1", "available_at": available}, rows,
                                      datetime.fromisoformat(rows[-1]["available_at"]), "prices-1")
    assert result["status"] == "ready"
    assert result["post_event_bar_count"] == 6
    assert [result["horizons"][str(n)]["return_pct"] for n in (1, 3, 5)] == pytest.approx([10, 20, -10])
    assert result["latest_return_pct"] == pytest.approx(5)
    assert result["volume_reaction"]["ratio"] == 3
    assert result["volume_reaction"]["baseline_mean_volume"] == 200
    assert result["volume_reaction"]["baseline_bar_count"] == 20
    assert result["source_ids"] == ["event-1", "prices-1"]
    assert result["market_adjusted_return_pct"] is None
    assert result["corporate_action_adjustment"] == "unverified"
    assert result["price_basis"] == "raw_only"
    assert "benchmark_unavailable" in result["data_gaps"]


def test_future_bars_do_not_change_partial_horizons_or_unchanged_bar_cache():
    rows = full_history()
    available = datetime.fromisoformat(rows[20]["available_at"]) - timedelta(hours=1)
    cutoff = datetime.fromisoformat(rows[22]["available_at"])
    disclosure = {"event_id": "event-1", "available_at": available}
    result = calculate_event_reaction(disclosure, rows, cutoff, "prices-1")
    assert result == calculate_event_reaction(disclosure, rows[:23], cutoff, "prices-1")
    assert result == calculate_event_reaction(disclosure, rows, cutoff + timedelta(minutes=15), "prices-1")
    assert result["status"] == "partial" and result["post_event_bar_count"] == 3
    assert result["horizons"]["5"] == {"status": "insufficient_post_event_bars", "return_pct": None, "bar": None}
    assert result["latest_return_pct"] == pytest.approx(20)
    assert result["as_of_bar"]["available_at"] == cutoff.astimezone(timezone.utc).isoformat()


def test_future_event_and_naive_cutoff_are_rejected():
    with pytest.raises(ValueError, match="not available"):
        calculate_event_reaction(event("2026-09-04T16:00:00"), [], at("2026-09-04T15:00:00"), "prices")
    with pytest.raises(ValueError, match="timezone"):
        calculate_event_reaction(event("2026-09-04T12:00:00"), [], datetime(2026, 9, 4, 16), "prices")


@pytest.mark.parametrize("future", [
    {"available_at": at("2026-09-07T15:30:00").isoformat(), "close": float("nan"), "volume": True},
    {"available_at": at("2026-09-07T15:30:00").isoformat()},
    {**bar("2026-09-04", 130), "available_at": at("2026-09-04T16:00:00").isoformat()},
])
def test_future_invalid_values_and_same_day_conflicts_cannot_change_known_reaction(future):
    known = [bar("2026-09-03"), bar("2026-09-04", 110)]
    disclosure = event("2026-09-04T12:00:00")
    cutoff = at("2026-09-04T15:30:00")
    expected = calculate_event_reaction(disclosure, known, cutoff, "prices")
    assert calculate_event_reaction(disclosure, known + [future], cutoff, "prices") == expected
    assert calculate_event_reaction(disclosure, [future] + known, cutoff, "prices") == expected


def test_invalid_bar_exactly_at_cutoff_still_fails():
    row = {**bar("2026-09-04"), "volume": True}
    with pytest.raises(ValueError):
        calculate_event_reaction(event("2026-09-04T12:00:00"), [row], at("2026-09-04T15:30:00"), "prices")


def test_no_prior_close_or_no_post_bar_remains_missing_not_neutral():
    result = calculate_event_reaction(event("2026-09-04T12:00:00"), [bar("2026-09-04", 110)],
                                      at("2026-09-04T16:00:00"), "prices")
    assert result["status"] == "unavailable"
    assert result["baseline_bar"] is None and result["latest_return_pct"] is None
    assert result["horizons"]["1"]["bar"] is not None
    assert result["horizons"]["1"]["return_pct"] is None
    assert "prior_close_unavailable" in result["data_gaps"]
    result = calculate_event_reaction(event("2026-09-04T16:00:00"), [bar("2026-09-04")],
                                      at("2026-09-04T17:00:00"), "prices")
    assert result["status"] == "unavailable" and result["latest_return_pct"] is None
    assert result["post_event_bar_count"] == 0
    assert result["volume_reaction"]["ratio"] is None


def test_sparse_history_counts_observed_sessions_without_inventing_missing_dates():
    rows = [bar("2026-09-01"), bar("2026-09-04", 110), bar("2026-09-14", 120), bar("2026-10-01", 130)]
    result = calculate_event_reaction(event("2026-09-02T10:00:00"), rows, at("2026-10-02T12:00:00"), "prices")
    assert result["post_event_bar_count"] == 3
    assert result["horizons"]["3"]["return_pct"] == pytest.approx(30)
    assert result["horizons"]["5"]["return_pct"] is None
    assert result["volume_reaction"]["baseline_mean_volume"] is None
    assert result["volume_reaction"]["status"] == "insufficient_pre_event_volume_history"


@pytest.mark.parametrize("zero_baseline", [True, False])
def test_zero_volume_baseline_is_missing_but_observed_zero_post_volume_is_valid(zero_baseline):
    rows = full_history()
    if zero_baseline:
        for row in rows[:20]:
            row["volume"] = 0
    else:
        rows[20]["volume"] = 0
    available = datetime.fromisoformat(rows[20]["available_at"]) - timedelta(hours=1)
    result = calculate_event_reaction({"event_id": "event-1", "available_at": available}, rows,
                                      datetime.fromisoformat(rows[-1]["available_at"]), "prices")
    assert result["volume_reaction"]["ratio"] == (None if zero_baseline else 0)
    assert result["volume_reaction"]["status"] == ("zero_pre_event_mean_volume" if zero_baseline else "observed")


def test_identical_duplicate_bars_deduplicate_but_conflicting_daily_bars_fail():
    original = bar("2026-09-03")
    disclosure = event("2026-09-04T12:00:00")
    cutoff = at("2026-09-04T16:00:00")
    expected = calculate_event_reaction(disclosure, [original], cutoff, "prices")
    duplicate = copy.deepcopy(original)
    duplicate["available_at"] = datetime.fromisoformat(duplicate["available_at"]).astimezone(timezone.utc).isoformat()
    assert calculate_event_reaction(disclosure, [duplicate, original], cutoff, "prices") == expected
    for conflicting in (bar("2026-09-03", 110), {**original, "available_at": at("2026-09-03T16:00:00").isoformat()}):
        with pytest.raises(ValueError, match="Conflicting"):
            calculate_event_reaction(disclosure, [original, conflicting], cutoff, "prices")


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -1.0, "100"])
def test_invalid_ohlcv_values_raise(field, value):
    row = bar("2026-09-03")
    row[field] = value
    with pytest.raises(ValueError):
        calculate_event_reaction(event("2026-09-04T12:00:00"), [row], at("2026-09-04T16:00:00"), "prices")


@pytest.mark.parametrize("change", [
    lambda row: row.update(available_at="2026-09-03T15:30:00"),
    lambda row: row.update(available_at=True),
    lambda row: row.update(close=0),
    lambda row: row.update(low=102),
])
def test_invalid_bar_time_and_inconsistent_prices_raise(change):
    row = bar("2026-09-03")
    change(row)
    with pytest.raises(ValueError):
        calculate_event_reaction(event("2026-09-04T12:00:00"), [row], at("2026-09-04T16:00:00"), "prices")
