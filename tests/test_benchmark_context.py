from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.runner.benchmark_context import compare_event_to_benchmarks
from src.runner.event_reaction import calculate_event_reaction

KST = timezone(timedelta(hours=9))
DATES = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"]


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=KST)


def stock_reaction():
    rows = [{"available_at": at(day + "T15:30:00").isoformat(), "open": close,
             "high": close + 1, "low": close - 1, "close": close, "volume": 100.0}
            for day, close in zip(DATES, [100.0, 110.0, 115.0, 120.0, 125.0, 130.0])]
    return calculate_event_reaction({"event_id": "event:1", "available_at": at("2026-09-01T10:00:00")},
                                    rows, at("2026-09-07T17:00:00"), "stock:raw-v1")


def index_bar(day, close, *, series="KOSPI", version="v1", available_at=None):
    return {"trade_date": day, "bar_at": at(day + "T15:30:00").isoformat(),
            "available_at": available_at or at(day + "T16:00:00").isoformat(), "close": close,
            "source_id": f"index:{series}:{day}:{version}", "version": version, "price_basis": "price_index"}


def benchmarks():
    return {scope: {"status": "ready", "series": series, "index_name": name,
                    "mapping_source_id": f"mapping:{scope}:v1",
                    "mapping_available_at": "2026-08-01T10:00:00+09:00",
                    "effective_from": "2026-08-01", "effective_to": None,
                    "bars": [index_bar(day, close, series=series) for day, close in zip(DATES, closes)]}
            for scope, series, name, closes in [("market", "KOSPI", "코스피", [1000.0, 1020.0, 1030.0, 1050.0, 1060.0, 1100.0]),
                ("sector", "KOSPI_ELECTRIC", "전기전자", [2000.0, 2100.0, 2080.0, 2200.0, 2160.0, 2300.0])]}


def compare(data=None, reaction=None, cutoff=None):
    return compare_event_to_benchmarks(reaction or stock_reaction(), benchmarks() if data is None else data,
                                       cutoff or at("2026-09-07T17:00:00"))


def test_exact_matching_market_and_sector_returns_and_citations():
    result = compare()
    assert result["market"]["status"] == result["sector"]["status"] == "ready"
    assert result["market"]["horizons"]["1"]["index_return_pct"] == pytest.approx(2.0)
    assert result["market"]["horizons"]["1"]["excess_return_pp"] == pytest.approx(8.0)
    assert result["sector"]["horizons"]["3"]["excess_return_pp"] == pytest.approx(10.0)
    assert result["market"]["latest"]["excess_return_pp"] == pytest.approx(20.0)
    assert result["sector"]["latest"]["excess_return_pp"] == pytest.approx(15.0)
    horizon = result["market"]["horizons"]["3"]
    assert horizon["baseline_trade_date"] == "2026-08-31"
    assert horizon["endpoint_trade_date"] == "2026-09-03"
    assert set(horizon["source_ids"]) == {"event:1", "stock:raw-v1", "mapping:market:v1",
        "index:KOSPI:2026-08-31:v1", "index:KOSPI:2026-09-03:v1"}
    assert set(horizon["source_ids"]) <= set(result["source_ids"])
    assert horizon["benchmark_observations"]["endpoint"] == {"source_id": "index:KOSPI:2026-09-03:v1",
        "version": "v1", "available_at": "2026-09-03T07:00:00+00:00"}
    assert result["benchmark_price_basis"] == "price_index"
    assert result["stock_price_basis"] == "raw_only"
    assert result["corporate_action_adjustment"] == "unverified"
    assert "not causal alpha" in result["interpretation"] and "total return" in result["interpretation"]
    assert "corporate_action_adjustment_unverified" in result["data_gaps"]
    assert "dividends_and_total_return_not_adjusted" in result["data_gaps"]


def test_missing_exact_endpoint_never_uses_nearby_index_date():
    data = benchmarks()
    data["market"]["bars"] = [row for row in data["market"]["bars"] if row["trade_date"] != "2026-09-03"]
    result = compare(data)
    horizon = result["market"]["horizons"]["3"]
    assert result["market"]["status"] == "partial"
    assert horizon["status"] == "benchmark_endpoint_date_unavailable"
    assert horizon["excess_return_pp"] is None and horizon["index_return_pct"] is None
    assert result["market"]["horizons"]["1"]["status"] == "observed"
    assert "market:benchmark_endpoint_date_unavailable" in result["data_gaps"]


def test_missing_exact_baseline_never_forward_fills():
    data = benchmarks()
    data["market"]["bars"] = data["market"]["bars"][1:] + [index_bar("2026-08-28", 999.0)]
    result = compare(data)
    assert result["market"]["status"] == "unavailable"
    assert result["market"]["latest"]["status"] == "benchmark_baseline_date_unavailable"
    assert result["sector"]["status"] == "ready"


def test_latest_known_revision_selected_and_future_revision_cannot_change_past():
    data = benchmarks()
    original = compare(data)
    future = index_bar("2026-09-03", float("nan"), version="v3", available_at="2026-09-08T16:00:00+09:00")
    data["market"]["bars"].append(future)
    assert compare(data) == original
    revision = index_bar("2026-09-03", 1100.0, version="v2", available_at="2026-09-07T16:30:00+09:00")
    data["market"]["bars"].append(revision)
    result = compare(data)
    horizon = result["market"]["horizons"]["3"]
    assert horizon["excess_return_pp"] == pytest.approx(10.0)
    assert revision["source_id"] in horizon["source_ids"]
    assert "index:KOSPI:2026-09-03:v1" not in horizon["source_ids"]
    assert compare(data, cutoff=at("2026-09-07T16:15:00")) == original


def test_unchanged_bars_and_mappings_do_not_change_output_after_clock_tick():
    assert compare() == compare(cutoff=at("2026-09-07T17:15:00"))


def test_reobserved_version_is_a_new_a_b_a_episode_not_collapsed_into_first_observation():
    data = benchmarks()
    original = compare(data)
    data["market"]["bars"].append(index_bar("2026-09-03", 1100.0, version="v2", available_at="2026-09-04T17:00:00+09:00"))
    corrected = compare(data)
    data["market"]["bars"].append(index_bar("2026-09-03", 1050.0, available_at="2026-09-07T16:30:00+09:00"))
    expected = compare(data)
    assert expected != corrected
    assert expected["market"]["horizons"]["3"]["index_return_pct"] == pytest.approx(5.0)
    assert "index:KOSPI:2026-09-03:v1" in expected["market"]["horizons"]["3"]["source_ids"]
    assert expected != original
    observation = expected["market"]["horizons"]["3"]["benchmark_observations"]["endpoint"]
    assert observation["available_at"] == "2026-09-07T07:30:00+00:00"
    data["market"]["bars"].reverse()
    assert compare(data) == expected


@pytest.mark.parametrize("kind", ["same_version", "same_time"])
def test_conflicting_known_versions_fail(kind):
    data = benchmarks()
    data["market"]["bars"].append(index_bar("2026-09-03", 999.0, version="v1" if kind == "same_version" else "v2"))
    with pytest.raises(ValueError, match="conflicting benchmark"):
        compare(data)


@pytest.mark.parametrize("change", [
    {"close": True}, {"close": "1000"}, {"close": 0}, {"close": float("inf")},
    {"available_at": "2026-09-03T16:00:00"}, {"bar_at": "2026-09-03T15:30:00"},
    {"trade_date": "2026-09-04"}, {"source_id": ""}, {"version": None}, {"price_basis": "total_return"},
    {"series": "wrong-series"},
    {"available_at": "2026-09-03T15:00:00+09:00"},
])
def test_malformed_known_bars_fail(change):
    data = benchmarks()
    data["market"]["bars"][3].update(change)
    with pytest.raises(ValueError):
        compare(data)


def test_unavailable_mapping_retains_explicit_reason_without_synthetic_comparison():
    result = compare({"sector": {"status": "blocked", "data_gaps": ["sector_mapping_unverified"]}})
    assert result["market"]["status"] == result["sector"]["status"] == "unavailable"
    assert "sector:sector_mapping_unverified" in result["data_gaps"]
    assert "market:benchmark_mapping_unavailable" in result["data_gaps"]
    assert result["market"]["latest"]["excess_return_pp"] is None
    assert result["source_ids"] == ["event:1", "stock:raw-v1"]


def test_stock_missing_horizon_preserves_missing_semantics():
    reaction = stock_reaction()
    reaction["horizons"]["5"] = {"status": "insufficient_post_event_bars", "bar": None, "return_pct": None}
    result = compare(reaction=reaction)
    assert result["market"]["status"] == "partial"
    assert result["market"]["horizons"]["5"]["status"] == "insufficient_post_event_bars"
    assert result["market"]["horizons"]["5"]["excess_return_pp"] is None


def test_future_known_bar_and_unknown_future_revision_do_not_affect_past():
    data = benchmarks()
    expected = compare(data)
    data["market"]["bars"].extend([
        {"available_at": "2026-09-08T16:00:00+09:00"},
        {"available_at": "2026-09-07T16:00:00+09:00", "bar_at": "2026-09-08T15:30:00+09:00"}])
    assert compare(data) == expected


def test_naive_cutoff_and_future_stock_observations_fail():
    with pytest.raises(ValueError, match="aware"):
        compare(cutoff=datetime(2026, 9, 7, 17))
    reaction = deepcopy(stock_reaction())
    reaction["horizons"]["5"]["bar"]["available_at"] = "2026-09-08T15:30:00+09:00"
    with pytest.raises(ValueError, match="future bar"):
        compare(reaction=reaction)


@pytest.mark.parametrize("interval", [
    {"effective_from": "2026-09-01", "effective_to": None},
    {"effective_from": "2026-08-01", "effective_to": "2026-09-02"},
])
def test_mapping_must_cover_exact_stock_baseline_and_endpoint_dates(interval):
    data = benchmarks()
    data["sector"].update(interval)
    result = compare(data)
    assert result["sector"]["horizons"]["3"]["status"] == "mapping_not_effective_for_window"
    assert result["sector"]["horizons"]["3"]["excess_return_pp"] is None
    assert result["market"]["status"] == "ready"
    assert "sector:mapping_not_effective_for_window" in result["data_gaps"]


def test_mapping_interval_boundaries_are_inclusive_and_future_mapping_is_not_used():
    data = benchmarks()
    data["sector"].update(effective_from=DATES[0], effective_to=DATES[-1])
    assert compare(data)["sector"]["status"] == "ready"
    data["sector"]["mapping_available_at"] = "2026-09-08T10:00:00+09:00"
    data["sector"]["bars"] = [{"close": "malformed future mapping must not matter"}]
    result = compare(data)
    assert result["sector"]["status"] == "unavailable"
    assert "sector:mapping_not_available_as_of" in result["data_gaps"]
    assert "mapping:sector:v1" not in result["source_ids"]


@pytest.mark.parametrize("change", [
    {"effective_from": None}, {"effective_from": "20260901"},
    {"effective_to": "2026-07-01"}, {"mapping_available_at": "2026-09-01T10:00:00"},
])
def test_malformed_known_mapping_interval_and_observation_fail(change):
    data = benchmarks()
    data["sector"].update(change)
    with pytest.raises(ValueError):
        compare(data)


@pytest.mark.parametrize("reverse", [False, True])
def test_historical_mapping_is_selected_for_the_event_window_not_latest_classification(reverse):
    data = benchmarks()
    old = deepcopy(data["sector"])
    old["effective_to"] = "2026-09-07"
    new = {**deepcopy(old), "effective_from": "2026-09-08", "effective_to": None,
           "mapping_source_id": "mapping:new-sector", "index_name": "New sector",
           "mapping_available_at": "2026-09-07T16:00:00+09:00"}
    history = [old, new]
    data["sector"] = {**new, "mapping_history": history[::-1] if reverse else history}
    result = compare(data)
    assert result["sector"]["status"] == "ready"
    assert result["sector"]["mapping_source_id"] == old["mapping_source_id"]
    assert new["mapping_source_id"] not in result["source_ids"]


def test_new_classification_closes_older_open_interval_without_splicing_index_series():
    data = benchmarks()
    old = deepcopy(data["sector"])
    new = {**deepcopy(old), "effective_from": "2026-09-03", "mapping_source_id": "mapping:new-sector",
           "mapping_available_at": "2026-09-03T09:00:00+09:00"}
    data["sector"] = {**new, "mapping_history": [old, new]}
    result = compare(data)
    assert result["sector"]["horizons"]["1"]["status"] == "observed"
    assert result["sector"]["horizons"]["3"]["status"] == "mapping_not_effective_for_window"
    assert result["sector"]["latest"]["excess_return_pp"] is None


def test_revised_closed_interval_does_not_revive_superseded_open_mapping():
    data = benchmarks()
    original = deepcopy(data["sector"])
    revised = {**deepcopy(original), "effective_to": "2026-08-30",
               "mapping_available_at": "2026-09-07T16:00:00+09:00", "mapping_source_id": "mapping:closed"}
    data["sector"] = {**revised, "mapping_history": [original, revised]}
    result = compare(data)
    assert result["sector"]["status"] == "unavailable"
    assert result["sector"]["latest"]["status"] == "mapping_not_effective_for_window"


def test_future_mapping_in_history_does_not_change_known_classification():
    data = benchmarks()
    original = deepcopy(data["sector"])
    future = {"mapping_available_at": "2026-09-08T16:00:00+09:00"}
    data["sector"] = {**original, "mapping_history": [original, future]}
    assert compare(data)["sector"] == compare()["sector"]


def test_stock_collection_time_is_not_used_as_benchmark_trade_date():
    reaction = stock_reaction()
    reaction["baseline_bar"].update(bar_at="2026-08-31T15:30:00+09:00", trade_date="2026-08-31",
                                    observed_at="2026-09-07T16:00:00+09:00")
    assert compare(reaction=reaction)["market"]["latest"]["baseline_trade_date"] == "2026-08-31"
    reaction["baseline_bar"]["observed_at"] = "2026-09-08T16:00:00+09:00"
    with pytest.raises(ValueError, match="future bar"):
        compare(reaction=reaction)
