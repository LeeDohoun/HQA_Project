"""BB001 rules on synthetic point-in-time data and independent references."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest
import requests

from backtesting import cost_model, experiment_registry, holdout
from backtesting.experiments import bb001 as b, common
from src.ingestion import dart_buyback as dart
from src.ingestion.dart_backfill import _write_json
from src.ingestion.storage import write_rows


@pytest.fixture(autouse=True)
def forbid_provider(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("BB001 experiment tests never call DART or another provider")
    monkeypatch.setattr(requests.Session, "get", fail)


@pytest.fixture
def fields():
    return common.load_preregistration(b.EXPERIMENT_ID)


@pytest.fixture
def registration(monkeypatch):
    registration = experiment_registry.verify_preregistration(b.EXPERIMENT_ID)
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *args, **kwargs: registration)
    return registration


def market(size=31, start="2023-10-02", end="2025-12-30"):
    days = b._days()
    days = days[(days >= start) & (days <= end)]
    codes = [f"{(i + 1) * 10:06d}" for i in range(size)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000., "close": 10000., "base_price": 10000., "volume": 10000.,
        "trading_value": 2e9, "market_cap": 1e11, "ret_1d": 0., "market": "KOSPI", "stock_name": "합성보통주",
        "calendar_status": "verified"}, index=index)
    return prices, days, codes


def item(day="20240131", *, corp="00000001", code="000010", number=1, **changes):
    return {"rcept_no": day + f"{number:06d}", "rcept_dt": day, "corp_code": corp, "stock_code": code,
            "corp_cls": "Y", "corp_name": "합성보통주", "report_nm": "주요사항보고서(자기주식취득결정)", **changes}


def structured(row, **changes):
    return {"rcept_no": row["rcept_no"], "corp_code": row["corp_code"], "aqpln_stk_ostk": "1,234",
        "aqpln_prc_ostk": "12,345,678", "aq_mth": "장내 직접 취득", "aq_pp": "취득 후 소각",
        "aqexpd_bgd": "2024.02.01", "aqexpd_edd": "2024.03.29", "aq_dd": "2000.01.01", **changes}


def cover(root, rows, *, start="2023-10-02", end=None):
    last = end or max((row["rcept_dt"] for row in rows), default="20240131")
    files = {}
    for day in pd.date_range(start, pd.Timestamp(last)):
        key = day.strftime("%Y%m%d")
        path = root / "disclosures/dart_full/list" / str(day.year) / f"{key}.jsonl"
        write_rows(path, [row for row in rows if row["rcept_dt"] == key])
        files[key] = path
    return files


def save_values(root, row, values=None, *, document=True, status="000", text=None, stale=False, complete=True):
    values = values or structured(row)
    path = dart._path(root, "structured", row["corp_code"])
    archive = json.loads(path.read_text()) if path.exists() else {"version": dart.VERSION, "corp_code": row["corp_code"],
        "from_date": dart.START, "to_date": dart.END, "complete": complete, "requests": [], "rows": {}}
    archive["rows"][values["rcept_no"]] = {"row": values, "row_sha256": dart.row_digest(values), "response_sha256": ["a" * 64]}
    _write_json(path, archive)
    if document:
        text = text if text is not None else f"원문 보통주 예정수량 {values['aqpln_stk_ostk']} 주 예정금액 {values['aqpln_prc_ostk']} 원"
        verification = dart.verify_values(values, text)
        if stale:
            verification["structured_row_sha256"] = "b" * 64
        _write_json(dart._path(root, "documents", row["rcept_no"]), {"version": dart.VERSION, "rcept_no": row["rcept_no"],
            "text": text, "status": status, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "raw_sha256": "c" * 64,
            "fetched_at": "2026-10-05T12:00:00+09:00", "verification": verification})


def event(row=None):
    row = row or item()
    return {**row, "public_date": pd.Timestamp(row["rcept_dt"]).date().isoformat(), "period": "design" if row["rcept_dt"] < "20250101" else "validation",
            "cancel": True, "structured": structured(row), "planned_amount": 12345678, "planned_quantity": 1234}


def selected(prices, days, root, row=None, horizon=21, listings=None):
    reader = b.PriceReader(root, days, prices=prices)
    source = row or item()
    planned, reason = b.schedule(event(source), horizon, days)
    assert reason is None
    match, reason, _ = b.match_controls(planned, reader, listings if listings is not None else [source])
    assert reason is None
    return {**planned, **match}, reader


def position(code, gross=0., *, vanished=False, cost=.003, horizon=21):
    return {"stock_code": code, "gross": gross, "vanished": vanished, "gross_path": [gross] * horizon,
            "costs": {"1.0": cost, "1.5": cost * 1.5, "2.0": cost * 2, "v1": cost},
            "side_costs": {"1.0": {"buy": cost / 3, "sell": cost * 2 / 3}}, "gap": .01}


def metric_rows(count=320):
    grid = pd.bdate_range("2020-01-01", "2025-12-31")
    result = []
    for index in range(count):
        day = grid[10 + index * 3]
        controls = [position(f"{(i + 1) * 10:06d}", gross=.01 * ((i % 5) - 2)) for i in range(10)]
        result.append({"rcept_no": day.strftime("%Y%m%d") + f"{index:06d}", "corp_code": f"{index:08d}", "stock_code": "900000",
            "public_date": (day - pd.Timedelta(days=1)).date().isoformat(), "entry_date": day.date().isoformat(),
            "exit_date": grid[30 + index * 3].date().isoformat(), "horizon": 21, "period": "design", "intensity": index / 100,
            "stock": position("900000", .025 + .006 * (index % 3)), "controls": controls,
            "event_features": {"prior5": -.01, "prior20": -.05 + index / 10000, "adv": 2e9},
            "control_features": {p["stock_code"]: {"prior5": -.02, "prior20": -.04, "adv": 2e9} for p in controls}})
    return result, grid


def judged(*, t=2.5, mean=.01, stressed=.008, design=300, share=.05, p=None):
    return {"primary": {"count": design + 40, "mean": mean, "t_stat": t,
            "p_value": p if p is not None else .5 * math.erfc(t / math.sqrt(2)) if t is not None else 1.},
            "design_events": design, "cost_sensitivity": {"1.5": {"mean": stressed}},
            "random_control": {"share_of_controls": share}}


def test_registration_is_committed_immutable_and_three_non_llm_non_holdout_variants():
    registration = b._registration(experiment_registry.PROJECT_ROOT)
    assert registration.fields["variants_planned"] == 3
    assert not registration.fields["uses_holdout"] and not registration.fields["uses_llm"]


def test_repeat_clock_includes_excluded_decisions_exact_63_boundary_and_same_day_lowest(tmp_path):
    _, days, _ = market(end="2024-12-30")
    first = item(days[80].strftime("%Y%m%d"))
    same = item(first["rcept_dt"], number=2)
    at63 = item(days[143].strftime("%Y%m%d"), number=3)
    after64 = item(days[207].strftime("%Y%m%d"), number=4)
    rows = [after64, same, at63, first]
    files = cover(tmp_path, rows)
    save_values(tmp_path, first, structured(first, aq_mth="장외"))
    for row in (same, at63, after64):
        save_values(tmp_path, row)
    events, counts = b.construct_events(rows, files, days, tmp_path)
    assert [row["rcept_no"] for row in events] == [after64["rcept_no"]]
    assert counts["filter_counts"] == {"not_on_market_acquisition": 1, "same_day_repeat": 1, "repeat_0_63": 1, "included": 1}
    # A future decision never retroactively marks the earlier original as a repeat.
    prior, prior_counts = b.construct_events([first], files, days, tmp_path)
    assert prior == [] and not prior_counts["audit"][0]["repeated"]


def test_lowest_same_day_receipt_cannot_be_replaced_by_a_valid_later_receipt(tmp_path):
    _, days, _ = market()
    first, second = item(number=1), item(number=2)
    files = cover(tmp_path, [first, second])
    save_values(tmp_path, second)
    events, report = b.construct_events([second, first], files, days, tmp_path)
    assert events == [] and report["filter_counts"] == {"structured_original_row_missing": 1, "same_day_repeat": 1}


def test_weekend_clock_next_exchange_open_and_archive_completeness(tmp_path):
    _, days, _ = market()
    row = item("20240203")  # Saturday; next session is Monday, not decision date.
    files = cover(tmp_path, [row])
    save_values(tmp_path, row)
    events, _ = b.construct_events([row], files, days, tmp_path)
    planned, reason = b.schedule(events[0], 21, days)
    assert reason is None and planned["entry_date"] == "2024-02-05"
    assert planned["exit_position"] == planned["entry_position"] + 20
    coverage = b.listing_coverage(row["rcept_dt"], days, files)
    assert coverage["covered"]
    files.pop("20240107")  # Empty Sunday still belongs to the observation window.
    events, report = b.construct_events([row], files, days, tmp_path)
    assert events == [] and report["filter_counts"]["incomplete_listing_window"] == 1


@pytest.mark.parametrize("field", dart.REQUIRED_FIELDS)
def test_every_required_original_field_must_be_nonempty(tmp_path, field):
    _, days, _ = market()
    row = item()
    files = cover(tmp_path, [row])
    values = structured(row, **{field: " "})
    save_values(tmp_path, row, values)
    events, report = b.construct_events([row], files, days, tmp_path)
    assert events == [] and report["filter_counts"]["required_field_missing"] == 1


@pytest.mark.parametrize("change,reason", [
    ({"stock_code": ""}, "stock_code_link_failed"), ({"stock_code": "a00010"}, "stock_code_link_failed"),
    ({"stock_code": "000015"}, "not_common_share_market_or_spac"), ({"corp_cls": "N"}, "not_common_share_market_or_spac"),
    ({"corp_name": "합성스팩"}, "not_common_share_market_or_spac")])
def test_original_listing_identifiers_and_share_type_filters(tmp_path, change, reason):
    _, days, _ = market()
    row = item(**change)
    files = cover(tmp_path, [row])
    save_values(tmp_path, row)
    events, report = b.construct_events([row], files, days, tmp_path)
    assert events == [] and report["filter_counts"][reason] == 1


def test_alphanumeric_common_code_and_historical_delisted_identity_are_kept(tmp_path):
    _, days, _ = market()
    row = item(code="A12340")
    files = cover(tmp_path, [row])
    save_values(tmp_path, row)
    events, _ = b.construct_events([row], files, days, tmp_path)
    assert events[0]["stock_code"] == "A12340"


@pytest.mark.parametrize("case,reason", [("missing", "original_document_unavailable"), ("014", "original_document_unavailable"),
    ("digits", "original_value_verification_failed"), ("stale", "original_value_verification_failed"),
    ("different_receipt", "structured_original_row_missing"), ("partial_corp", "structured_original_row_missing")])
def test_original_verification_gate_never_uses_later_corrections(tmp_path, case, reason):
    _, days, _ = market()
    row = item()
    files = cover(tmp_path, [row])
    values = structured(row, rcept_no=item(number=2)["rcept_no"]) if case == "different_receipt" else structured(row)
    save_values(tmp_path, row, values, document=case != "missing", status="014" if case == "014" else "000",
                text="보통주 1,234 주 금액 100 원" if case == "digits" else None, stale=case == "stale", complete=case != "partial_corp")
    events, report = b.construct_events([row], files, days, tmp_path)
    assert not events and report["filter_counts"][reason] == 1


@pytest.mark.parametrize("field,value,reason", [("aqpln_stk_ostk", "0", "nonpositive_plan"),
    ("aqpln_prc_ostk", "0", "nonpositive_plan"), ("aq_mth", "장내 및 장외", "not_on_market_acquisition"),
    ("aq_mth", "장내 공개매수", "not_on_market_acquisition")])
def test_positive_common_plan_and_method_rules(tmp_path, field, value, reason):
    _, days, _ = market()
    row = item()
    files = cover(tmp_path, [row])
    save_values(tmp_path, row, structured(row, **{field: value}))
    events, report = b.construct_events([row], files, days, tmp_path)
    assert not events and report["filter_counts"][reason] == 1


def test_corrections_withdrawals_and_actual_acquisition_data_do_not_change_event_or_exit(tmp_path):
    prices, days, _ = market()
    row = item()
    noise = [item("20240201", number=2, report_nm="[기재정정]자기주식취득결정"),
             item("20240202", number=3, report_nm="자기주식취득결정 철회")]
    files = cover(tmp_path, [row, *noise])
    values = structured(row, aq_dd="2000.01.01", actual_rate="0", completed_cancel="false")
    save_values(tmp_path, row, values)
    events, report = b.construct_events([row, *noise], files, days, tmp_path)
    assert len(events) == 1 and report["title_candidates"] == 1
    planned, _ = b.schedule(events[0], 21, days)
    assert planned["entry_date"] == "2024-02-01" and planned["exit_date"] != "2024-02-02"
    selection, reader = selected(prices, days, tmp_path, row, listings=[row, *noise])
    observed = b.observations([selection], reader)[0]
    assert observed["stock"]["scheduled_exit_date"] == planned["exit_date"]


@pytest.mark.parametrize("horizon", [1, 5, 21, 63])
def test_inclusive_holding_schedule_and_receipt_based_period(horizon):
    days = b._days()
    planned, reason = b.schedule(event(), horizon, days)
    assert reason is None
    assert days[planned["exit_position"]] == days[planned["entry_position"] + horizon - 1]
    assert event(item("20241231"))["period"] == "design"
    assert event(item("20250102"))["period"] == "validation"


@pytest.mark.parametrize("horizon", [21, 63])
def test_holdout_exit_is_excluded_before_any_price_read(tmp_path, monkeypatch, horizon):
    days = b._days()
    reader = b.PriceReader(tmp_path, days)
    monkeypatch.setattr(reader, "read", lambda *args: pytest.fail("forbidden event caused a price read"))
    rows, report = b.select_events([event(item("20251215"))], reader, [], horizon)
    assert not rows and report["filter_counts"]["holdout_horizon"] == 1
    assert reader.source_hashes == {}


def test_guard_runs_on_every_read_including_cache_hits_and_rejects_supplied_holdout(tmp_path, monkeypatch):
    prices, days, _ = market()
    reader = b.PriceReader(tmp_path, days, prices=prices)
    calls, original = [], holdout.guard_period
    def guard(start, end, **kwargs):
        calls.append((start, end))
        return original(start, end, **kwargs)
    monkeypatch.setattr(holdout, "guard_period", guard)
    reader.read(days[0])
    reader.read(days[0])
    assert len(calls) == 2
    with pytest.raises(ValueError, match="holdout"):
        reader.read("2026-01-02")
    forbidden = prices.iloc[:1].copy()
    forbidden.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-02"), "000010")], names=prices.index.names)
    with pytest.raises(ValueError, match="holdout"):
        b.PriceReader(tmp_path, days, prices=forbidden)


def test_price_loader_uses_latest_version_market_cap_and_hashes_only_guarded_sources(tmp_path):
    day = pd.Timestamp("2024-01-31")
    path = tmp_path / "market/krx_daily/2024/20240131.jsonl"
    row = {"trade_date": "2024-01-31", "stock_code": "000010", "market_cap": "100", "close": "10000", "change_rate_pct": "2"}
    write_rows(path, [row, {**row, "market_cap": "200"}])
    poison = tmp_path / "market/krx_daily/2026/20260102.jsonl"
    poison.parent.mkdir(parents=True)
    poison.write_text("holdout file must never be opened")
    reader = b.PriceReader(tmp_path, b._days())
    quote = reader.read(day)["000010"]
    assert quote["market_cap"] == 200 and quote["ret_1d"] == .02
    assert reader.source_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize("case,field,value", [("price", "close", 999.), ("adv", "trading_value", 9e7),
    ("return", "ret_1d", np.nan), ("return_invalid", "ret_1d", -1.01), ("calendar", "calendar_status", "unverified"),
    ("cap", "market_cap", np.nan), ("spac", "stock_name", "합성스팩"), ("market", "market", "KONEX")])
def test_each_liquidity_and_universe_condition_at_pre_entry(tmp_path, case, field, value):
    prices, days, codes = market()
    entry = days.searchsorted(pd.Timestamp("2024-01-31"), side="right")
    affected = days[entry - 20:entry] if field == "trading_value" else [days[entry - 1]]
    for day in affected:
        prices.loc[(day, codes[0]), field] = value
    reader = b.PriceReader(tmp_path, days, prices=prices)
    planned, _ = b.schedule(event(), 21, days)
    match, reason, _ = b.match_controls(planned, reader, [item()])
    assert match is None and reason == "event_liquidity_or_identity"


@pytest.mark.parametrize("case,field,value,reason", [("limit", "open", 13000., "limit_up_entry"),
    ("volume", "volume", 0., "untradable_entry"), ("missing_open", "open", np.nan, "untradable_entry")])
def test_event_unfilled_entries_are_reported_and_excluded(tmp_path, case, field, value, reason):
    prices, days, codes = market()
    prices.loc[(pd.Timestamp("2024-02-01"), codes[0]), field] = value
    reader = b.PriceReader(tmp_path, days, prices=prices)
    selections, report = b.select_events([event()], reader, [item()], 21)
    assert not selections and report["filter_counts"][reason] == 1


def test_exact_legacy_limit_and_missing_base_convention():
    row = {"open": 11500., "volume": 1., "base_price": 10000., "calendar_status": "verified"}
    assert b.entry_reason(row, {}, "2015-06-12") == "limit_up_entry"
    assert b.entry_reason(row, {}, "2015-06-15") is None
    row["base_price"] = np.nan
    assert b.entry_reason(row, {"close": 10000.}, "2024-02-01") is None
    assert b.entry_reason(row, {}, "2024-02-01") == "missing_entry_base"


def test_empirical_breakpoints_use_non_events_each_market_and_ties_go_lower(tmp_path):
    prices, days, codes = market(size=61)
    # KOSPI non-event candidates all size 1e11/return 0; enormous KOSDAQ values
    # and event values cannot move KOSPI boundaries.
    for code in codes[31:]:
        prices.loc[(slice(None), code), "market"] = "KOSDAQ"
        prices.loc[(slice(None), code), "market_cap"] = 1e15
        prices.loc[(slice(None), code), "ret_1d"] = .03
    prices.loc[(slice(None), codes[0]), "market_cap"] = 9e11
    selection, _ = selected(prices, days, tmp_path)
    assert selection["size_boundaries"] == [1e11, 1e11]
    assert selection["return_boundaries"] == [0., 0., 0., 0.]
    assert selection["size_relaxed"] and len(selection["control_features"]) == 30
    assert all(feature["market"] == "KOSPI" for feature in selection["control_features"].values())
    assert b.lower_bin(1e11, selection["size_boundaries"]) == 0
    assert b.lower_bin(.1, [.1, .2, .3, .4]) == 0
    assert b.empirical_boundaries([1, 2, 3, 4, 5, 6], 3) == [2., 4.]


def test_recent_excluded_decisions_remove_controls_but_future_and_corrections_do_not(tmp_path):
    prices, days, codes = market()
    title_rows = [item(), item("20240130", corp="00000002", code=codes[1]),
                  item("20240202", corp="00000003", code=codes[2]),
                  item("20240130", corp="00000004", code=codes[3], report_nm="[정정]자기주식취득결정")]
    selection, _ = selected(prices, days, tmp_path, listings=title_rows)
    assert codes[1] not in selection["control_features"]
    assert codes[2] in selection["control_features"] and codes[3] in selection["control_features"]


def test_controls_are_buyable_and_return_bin_never_relaxes(tmp_path):
    prices, days, codes = market(size=61)
    # Six return groups force different empirical bins; only nine in the event
    # bin survive buyability, while other return bins have many candidates.
    for index, code in enumerate(codes[1:]):
        prices.loc[(slice(None), code), "ret_1d"] = .001 * (index // 12)
    for code in codes[1:13]:
        prices.loc[(slice(None), code), "ret_1d"] = -.01
    prices.loc[(slice(None), codes[0]), "ret_1d"] = -.02
    for code in codes[1:4]:
        prices.loc[(pd.Timestamp("2024-02-01"), code), "open"] = 13000.
    planned, _ = b.schedule(event(), 21, days)
    match, reason, detail = b.match_controls(planned, b.PriceReader(tmp_path, days, prices=prices), [item()])
    assert match is not None and all(code not in match["control_features"] for code in codes[1:4])
    # Empirical lower quintile can extend past the original twelve; instead
    # put the event above every return to ensure its unchanged bin is empty.
    prices.loc[(slice(None), codes[0]), "ret_1d"] = .1
    match, reason, detail = b.match_controls(planned, b.PriceReader(tmp_path, days, prices=prices), [item()])
    assert match is None and reason == "matched_controls_below_10"
    assert detail["relaxed_matched"] < 10


@pytest.mark.parametrize("count,eligible", [(9, False), (10, True)])
def test_minimum_ten_controls_is_decided_at_entry_before_future_missing_returns(tmp_path, count, eligible):
    prices, days, codes = market(size=count + 1)
    planned, _ = b.schedule(event(), 21, days)
    prices = prices.drop(index=[(day, code) for day in days[planned["exit_position"]:] for code in codes[1:]])
    match, reason, _ = b.match_controls(planned, b.PriceReader(tmp_path, days, prices=prices), [item()])
    assert (match is not None) is eligible
    assert reason is None if eligible else reason == "matched_controls_below_10"


def test_matching_features_are_unchanged_by_future_prices_and_use_adjusted_20_session_returns(tmp_path):
    prices, days, codes = market()
    prices["ret_1d"] = .001
    first, _ = selected(prices, days, tmp_path)
    mutated = prices.copy()
    mutated.loc[mutated.index.get_level_values("trade_date") > "2024-02-01", ["close", "market_cap", "ret_1d"]] = 999.
    second, _ = selected(mutated, days, tmp_path)
    assert first == second
    assert first["event_features"]["prior20"] == pytest.approx(1.001**20 - 1)
    assert first["event_features"]["prior5"] == pytest.approx(1.001**5 - 1)


def test_holding_return_chains_adjusted_returns_and_side_costs_use_distinct_prices_dates(tmp_path, monkeypatch):
    prices, days, codes = market()
    selection, _ = selected(prices, days, tmp_path, horizon=3)
    first = selection["entry_position"]
    prices.loc[(days[first], codes[0]), "close"] = 11000.
    prices.loc[(days[first + 1], codes[0]), "ret_1d"] = .1
    prices.loc[(days[first + 2], codes[0]), ["ret_1d", "close"]] = [.2, 5000.]
    calls = []
    def side(price, market, day, adv, direction, **kwargs):
        calls.append((price, day, direction, kwargs["model_version"], kwargs["multiplier"]))
        return .001 if direction == "buy" else .002
    monkeypatch.setattr(cost_model, "one_way_cost", side)
    value = b.value_position(b.PriceReader(tmp_path, days, prices=prices), selection, codes[0], selection["event_features"])
    assert value["gross"] == pytest.approx(1.1 * 1.1 * 1.2 - 1)
    assert calls[0] == (10000., days[first].date(), "buy", "v2", 1.)
    assert calls[1] == (5000., days[first + 2].date(), "sell", "v2", 1.)
    assert value["costs"]["1.0"] == .003 and calls[-1][3] == "v1"


@pytest.mark.parametrize("vanished", [False, True])
def test_missing_exit_marks_last_trade_controls_keep_fixed_weights_and_vanish_is_sensitivity_only(tmp_path, vanished):
    prices, days, codes = market(size=11)
    selection, _ = selected(prices, days, tmp_path, horizon=5)
    target = selection["exit_position"]
    stop = len(days) if vanished else target + 1
    prices = prices.drop(index=[(day, code) for day in days[target - 1:stop] for code in codes[:2]])
    reader = b.PriceReader(tmp_path, days, prices=prices)
    row = b.observations([selection], reader)[0]
    assert len(row["controls"]) == 10 and row["control_weight"] == .1
    assert row["stock"]["valuation_date"] == days[target - 2].date().isoformat()
    assert row["stock"]["vanished"] is vanished
    assert row["controls"][0]["vanished"] is vanished
    if vanished:
        assert b._net(row["stock"], worthless=True) == -1 - row["stock"]["costs"]["1.0"]
    else:
        assert b.event_excess(row, worthless=True) == b.event_excess(row)


def test_missing_observed_adjusted_return_and_whole_market_archive_fail_not_drop(tmp_path):
    prices, days, codes = market()
    selection, _ = selected(prices, days, tmp_path, horizon=3)
    day = days[selection["entry_position"] + 1]
    prices.loc[(day, codes[0]), "ret_1d"] = np.nan
    with pytest.raises(ValueError, match="adjusted return"):
        b.observations([selection], b.PriceReader(tmp_path, days, prices=prices))
    prices = prices.drop(index=day, level="trade_date")
    with pytest.raises(ValueError, match="whole-market"):
        b.observations([selection], b.PriceReader(tmp_path, days, prices=prices))


def test_lower_limit_scheduled_close_is_a_valuation_no_extra_exit_filter(tmp_path):
    prices, days, codes = market()
    selection, _ = selected(prices, days, tmp_path, horizon=1)
    day = days[selection["entry_position"]]
    prices.loc[(day, codes[0]), "close"] = 7000.
    result = b.value_position(b.PriceReader(tmp_path, days, prices=prices), selection, codes[0], selection["event_features"])
    assert not result["stale_exit"] and result["gross"] == pytest.approx(-.3)


@pytest.mark.parametrize("lag", [0, 4, 20, 62])
def test_hac_matches_independent_bartlett_matrix_reference_with_zero_event_days(lag):
    grid = pd.bdate_range("2024-01-02", periods=90)
    positions = [1, 1, 3, 15, 20, 45, 88]
    values = np.array([.02, -.01, .05, -.03, .02, .015, .08])
    mean = values.mean()
    sums = np.array([sum(values[i] - mean for i, pos in enumerate(positions) if pos == t) for t in range(len(grid))])
    distance = np.abs(np.arange(len(grid))[:, None] - np.arange(len(grid))[None, :])
    kernel = np.maximum(0, 1 - distance / (lag + 1))
    variance = float(sums @ kernel @ sums / len(values)**2)
    result = b.hac(values, grid[positions], grid, lag)
    assert result["standard_error"] == pytest.approx(math.sqrt(variance), rel=1e-12)
    assert result["t_stat"] == pytest.approx(mean / math.sqrt(variance), rel=1e-12)
    assert result["p_value"] == pytest.approx(.5 * math.erfc(result["t_stat"] / math.sqrt(2)))
    assert result["mean"] == pytest.approx(values.mean()) and not result["small_sample_correction"]


def test_hac_does_not_compress_grid_and_undefined_statistics_are_explicit():
    grid = pd.bdate_range("2024-01-02", periods=100)
    dates, values = grid[[1, 30, 31, 90]], [.01, -.02, .03, .1]
    full = b.hac(values, dates, grid, 20)
    compressed = b.hac(values, dates, dates, 20)
    assert full["standard_error"] != pytest.approx(compressed["standard_error"])
    assert b.hac([], [], grid, 20)["p_value"] == 1.
    assert b.hac([1, 1], grid[:2], grid, 20)["t_stat"] is None
    with pytest.raises(ValueError):
        b.hac([np.nan], grid[:1], grid, 20)


def test_secondary_cluster_t_matches_existing_event_study_date_mean_method():
    values = [.01, .02, .06, -.02]
    dates = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-05", "2024-01-09"])
    groups = np.array([.015, .06, -.02])
    assert b.cluster_t(values, dates) == pytest.approx(groups.mean() / (groups.std(ddof=1) / math.sqrt(3)))


def test_random_formula_order_and_reproducibility_against_independent_draws():
    rows, _ = metric_rows(8)
    rows = rows[::-1]
    for row in rows:
        row["controls"] = row["controls"][::-1]
    result = b.random_controls(rows)
    rng, expected = np.random.default_rng(0), []
    ordered = sorted(rows, key=lambda row: (row["entry_date"], row["rcept_no"]))
    for _ in range(200):
        path = []
        for row in ordered:
            net = np.array([value["gross"] - value["costs"]["1.0"] for value in sorted(row["controls"], key=lambda p: p["stock_code"])])
            path.append(float(rng.choice(net)) - net.mean())
        expected.append(path)
    np.testing.assert_array_equal(result["paths"], expected)
    assert result == b.random_controls(rows)
    actual = np.mean([row["stock"]["gross"] - row["stock"]["costs"]["1.0"] - np.mean([p["gross"] - p["costs"]["1.0"] for p in row["controls"]]) for row in ordered])
    assert result["actual"] == pytest.approx(actual)
    assert result["share_of_controls"] == np.mean(np.mean(expected, axis=1) >= actual)


def test_random_paths_center_near_zero_when_event_effect_is_pure_noise():
    rows, _ = metric_rows(500)
    rng = np.random.default_rng(123)
    for row in rows:
        for control in row["controls"]:
            control["gross"] = float(rng.normal(0, .02))
        row["stock"]["gross"] = float(rng.normal(0, .02))
    result = b.random_controls(rows)
    assert abs(result["mean"]) < .0003
    assert abs(result["actual"]) < .003
    assert .05 < result["share_of_controls"] < .95
    # A deterministic event effect must move the actual, not the null paths.
    for row in rows:
        row["stock"]["gross"] += .2
    shifted = b.random_controls(rows)
    assert shifted["paths"] == result["paths"] and shifted["share_of_controls"] == 0.


def test_random_100percent_rerun_uses_same_draws_on_both_sides():
    rows, _ = metric_rows(6)
    for row in rows:
        for control in row["controls"]:
            control["vanished"] = True
        row["stock"]["vanished"] = True
    rerun = b.random_controls(rows, worthless=True)
    np.testing.assert_allclose(rerun["paths"], 0, atol=1e-15)
    assert rerun["actual"] == pytest.approx(0.)


def test_holm_fixed_three_family_insufficient_design_p_one_even_with_validation_events(fields):
    results = {"d21": judged(t=3.), "d63": judged(t=4., design=299), "d21_cancel": judged(t=None, design=400)}
    b.judge_variants(results, fields, 3)
    assert results["d21"]["verdict"] == "pass"
    for name in ("d63", "d21_cancel"):
        assert results[name]["verdict"] == "insufficient" and results[name]["judgement_inputs"]["holm_input_p"] == 1.
    assert results["d21"]["judgement_inputs"]["holm_p"] == pytest.approx(3 * .5 * math.erfc(3 / math.sqrt(2)))
    assert b.holm(dict(zip(b.VARIANTS, [.01, .04, .03]))) == {"d21": .03, "d21_cancel": .06, "d63": .06}


@pytest.mark.parametrize("trials,t,verdict", [(20, 2.5, "pass"), (21, 2.5, "fail"), (21, 3., "fail"), (21, 3.1, "pass"), (20, 2., "fail")])
def test_strict_t_gate_includes_this_runs_three_registry_rows(fields, trials, t, verdict):
    results = {name: judged(t=t) for name in b.VARIANTS}
    b.judge_variants(results, fields, trials)
    assert results["d21"]["verdict"] == verdict
    assert results["d21"]["judgement_inputs"]["required_t_stat"] == (3. if trials > 20 else 2.)


@pytest.mark.parametrize("field,value", [("mean", 0.), ("stressed", 0.), ("share", .051), ("p", .02)])
def test_every_other_registered_pass_gate(fields, field, value):
    results = {name: judged(t=4., **{field: value}) for name in b.VARIANTS}
    b.judge_variants(results, fields, 3)
    assert all(result["verdict"] == "fail" for result in results.values())


def test_repository_t_threshold_is_respected_and_validation_sign_is_reporting_only(fields):
    results = {name: {**judged(t=3.1), "validation_year_same_sign": False} for name in b.VARIANTS}
    b.judge_variants(results, fields, 3, repository_threshold=3.2)
    assert results["d21"]["verdict"] == "fail"
    b.judge_variants(results, fields, 3)
    assert results["d21"]["verdict"] == "pass"


def test_delisting_sensitive_verdict_never_passes_and_recomputes_holm(fields):
    results = {name: judged(t=4.) for name in b.VARIANTS}
    loss = {name: judged(t=-4., mean=-.1, stressed=-.11) for name in b.VARIANTS}
    b.judge_variants(results, fields, 3)
    b.judge_variants(loss, fields, 3)
    b.apply_delisting_sensitivity(results, loss)
    assert all(result["verdict"] == "delisting_sensitive" for result in results.values())
    assert all(result["delisting_sensitivity"]["base_verdict"] == "pass" for result in results.values())
    assert all(result["delisting_sensitivity"]["judgement_inputs"]["holm_p"] > .95 for result in results.values())


def test_reversal_balance_table_and_continuous_ols_intercept_against_reference():
    rows, grid = metric_rows(40)
    x = np.linspace(-.15, .03, len(rows))
    noise = np.sin(np.arange(len(rows))) * .002
    for row, value, residual in zip(rows, x, noise):
        row["event_features"]["prior20"] = -.04 + value
        row["stock"]["gross"] = .012 + .4 * value + residual
    balance, regression = b.reversal_adjustment(rows, grid, 21)
    y = np.array([b.event_excess(row) for row in rows])
    expected = np.linalg.lstsq(np.column_stack([np.ones(len(x)), x]), y, rcond=None)[0]
    assert len(balance["table"]) == 40
    assert balance["means"]["difference_5"] == pytest.approx(.01)
    assert balance["means"]["difference_20"] == pytest.approx(x.mean())
    assert regression["intercept"] == pytest.approx(expected[0]) and regression["slope"] == pytest.approx(expected[1])
    assert regression["intercept_standard_error"] > 0 and not regression["used_for_judgement"]
    design = np.column_stack([np.ones(len(x)), x])
    residuals = y - design @ expected
    scores = np.zeros((len(grid), 2))
    for i, row in enumerate(rows):
        scores[grid.get_loc(pd.Timestamp(row["entry_date"]))] += design[i] * residuals[i]
    distance = np.abs(np.arange(len(grid))[:, None] - np.arange(len(grid))[None, :])
    kernel = np.maximum(0, 1 - distance / 21)
    bread = np.linalg.inv(design.T @ design)
    covariance = bread @ scores.T @ kernel @ scores @ bread
    assert regression["intercept_standard_error"] == pytest.approx(math.sqrt(covariance[0, 0]))
    singular = copy.deepcopy(rows)
    for row in singular:
        row["event_features"]["prior20"] = -.04
    assert b.reversal_adjustment(singular, grid, 21)[1]["intercept"] is None


def test_intensity_uses_planned_period_exchange_sessions_not_elapsed_calendar_days(tmp_path):
    prices, days, _ = market()
    selection, reader = selected(prices, days, tmp_path)
    row = b.observations([selection], reader)[0]
    expected = ((days >= "2024-02-01") & (days <= "2024-03-29")).sum()
    assert row["planned_period_sessions"] == expected
    assert row["intensity"] == pytest.approx(12345678 / (expected * 2e9))
    selection["structured"]["aqexpd_edd"] = "잘못된 날짜"
    assert b.observations([selection], reader)[0]["intensity"] is None
    selection["structured"]["aqexpd_edd"] = "2027-01-01"
    observed = b.observations([selection], reader)[0]
    assert observed["intensity"] is None and observed["intensity_reason"]


def test_literature_cars_include_day0_only_in_first_and_do_not_charge_costs(tmp_path):
    prices, days, codes = market()
    public = pd.Timestamp("2024-01-31")
    first = days.get_loc(public)
    prices.loc[(public, codes[0]), "ret_1d"] = .05
    for code in codes[1:]:
        prices.loc[(days[first - 10], code), "ret_1d"] = .05
    for day in days[first + 1:first + 22]:
        prices.loc[(day, codes[0]), "ret_1d"] = .01
    selection, reader = selected(prices, days, tmp_path)
    result = b.literature_car(selection, reader)
    assert result["car_0_21"] == pytest.approx(1.05 * 1.01**21 - 1)
    assert result["car_1_21"] == pytest.approx(1.01**21 - 1)
    selection["public_date"] = "2024-02-03"
    assert b.literature_car(selection, reader)["reason"] == "non_session_receipt_no_disclosure_close"


def test_literature_holdout_exclusion_precedes_prices(tmp_path, monkeypatch):
    reader = b.PriceReader(tmp_path, b._days())
    monkeypatch.setattr(reader, "require", lambda day: pytest.fail("forbidden CAR price read"))
    result = b.literature_car({"rcept_no": "20251215000001", "public_date": "2025-12-15"}, reader)
    assert result["reason"] == "holdout_or_incomplete_literature_horizon"


def test_calendar_portfolio_caps_twenty_fixed_slots_idle_cash_and_order():
    grid = pd.bdate_range("2024-01-02", periods=4)
    rows = []
    for i in reversed(range(22)):
        rows.append({"rcept_no": f"20240101{i:06d}", "entry_date": grid[0].date().isoformat(), "exit_date": grid[1].date().isoformat(),
            "horizon": 2, "stock": position("900000", .1, cost=0., horizon=2), "controls": [position("000010", 0., cost=0., horizon=2)]})
    result = b.calendar_portfolio(rows, grid)
    assert len(result["accepted"]) == 20 and len(result["overflow_rcept_nos"]) == 2
    assert result["accepted"][0]["rcept_no"] == "20240101000000" and result["accepted"][0]["slot"] == 0
    assert max(row["active_slots"] for row in result["daily"]) == 20
    assert result["daily"][2]["active_slots"] == 0 and result["monthly"][0]["net_excess"] == pytest.approx(.1)
    partial = b.calendar_portfolio(rows[:1], grid)
    assert partial["monthly"][0]["event_net"] == pytest.approx(.1 / 20)
    assert b.calendar_portfolio([], grid)["monthly"][0]["net_excess"] == 0.


def test_secondary_year_regime_liquidity_intensity_and_cost_version_groups(tmp_path):
    rows, grid = metric_rows(30)
    # CAR values are evaluated independently above; empty local quote archives
    # deliberately use a non-session public date for this reporting-only test.
    for row in rows:
        row["public_date"] = "2024-01-06"
    secondary = b.secondary_metrics(rows, b.PriceReader(tmp_path, b._days()), grid, 21)
    assert set(secondary) == {"gap_mean", "reversal_balance", "continuous_adjustment", "literature_car", "intensity_terciles", "by_year", "short_sale_regimes", "liquidity_buckets", "calendar_time_portfolio"}
    assert secondary["short_sale_regimes"]["full_ban_2023"]["count"] == 30
    assert sum(group["count"] for group in secondary["intensity_terciles"]["groups"].values()) == 30
    metrics = b.variant_metrics(rows, grid, 21)
    assert set(metrics["cost_sensitivity"]) == {"1.0", "1.5", "2.0", "v1"}
    assert metrics["cost_sensitivity"]["v1"]["count"] == 30 and metrics["corporations"] == 30


def temp_preregistration(root):
    path = root / "research/experiments" / b.EXPERIMENT_ID / "preregistration.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((experiment_registry.PROJECT_ROOT / "research/experiments" / b.EXPERIMENT_ID / "preregistration.md").read_bytes())


def test_execute_writes_exact_three_trials_all_metrics_hashes_and_exclusive_results(tmp_path, registration):
    temp_preregistration(tmp_path)
    prices, days, _ = market()
    row = item()
    cover(tmp_path, [row])
    save_values(tmp_path, row)
    payload = b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, prices=prices, sessions=days)
    with (tmp_path / "research/experiments/registry.csv").open() as handle:
        trials = list(csv.DictReader(handle))
    assert [trial["variant"] for trial in trials] == list(b.VARIANTS)
    assert all(trial["verdict"] == "insufficient" for trial in trials)
    assert payload["selected_variant"] == "d21" and payload["verdict"] == "insufficient"
    assert payload["prereg_commit"] == registration.commit_hash and payload["source_hashes"]
    assert payload["supplied_price_frame_sha256"]
    for name in b.VARIANTS:
        assert set(payload["variants"][name]["secondary_horizons"]) == {"1", "5", "63"}
        assert payload["variants"][name]["primary"]["count"] == 1
        assert "continuous_adjustment" in payload["variants"][name]["secondary"]
    paths = list((tmp_path / "research/experiments" / b.EXPERIMENT_ID / "results").glob("*"))
    assert len(paths) == 2 and any(path.suffix == ".md" and "Interpretations" in path.read_text() for path in paths)
    json.dumps(payload, allow_nan=False)
    before = {path: path.read_bytes() for path in paths}
    b.write_results(payload, repo_root=tmp_path)
    assert all(path.read_bytes() == data for path, data in before.items())


def test_source_mutation_fails_before_registry_or_result_publication(tmp_path, registration, monkeypatch):
    temp_preregistration(tmp_path)
    row = item()
    files = cover(tmp_path, [row])
    save_values(tmp_path, row)
    prices, days, _ = market()
    original = b.secondary_metrics
    def mutate(rows, reader, grid, horizon):
        path = files["20240131"]
        path.write_text(path.read_text() + "\n")
        return original(rows, reader, grid, horizon)
    monkeypatch.setattr(b, "secondary_metrics", mutate)
    with pytest.raises(ValueError, match="source changed"):
        b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, prices=prices, sessions=days)
    assert not (tmp_path / "research/experiments/registry.csv").exists()
    assert not (tmp_path / "research/experiments" / b.EXPERIMENT_ID / "results").exists()


def test_dry_run_reports_all_missing_coverage_and_request_counts_without_writes(tmp_path, registration):
    row = item()
    cover(tmp_path, [row], start="2024-01-30")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = b.dry_run(data_dir=tmp_path, repo_root=tmp_path)
    assert result["construction"]["title_candidates"] == 1
    assert result["construction"]["filter_counts"]["incomplete_listing_window"] == 1
    assert result["construction"]["sequential_filters"][-1]["after"] == 0
    assert all(step["before"] == 0 for step in result["variants"]["d21"]["sequential_filters"])
    assert result["dart_plan"]["estimated_document_requests"] == 1
    assert result["dart_plan"]["listing_coverage"]["missing_days"] > 0
    assert all(value["selected_before_returns"] == 0 for value in result["variants"].values())
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after


def test_cost_model_v2_uses_t_plus_two_tax_and_historical_market_ticks():
    adv = 2e9
    # Settlement on 2025-01-03: 2025's 0.15%, even though traded in 2024.
    buy = cost_model.one_way_cost(10000., "KOSPI", "20241230", adv, "buy", model_version="v2")
    sell = cost_model.one_way_cost(10000., "KOSPI", "20241230", adv, "sell", model_version="v2")
    assert sell - buy == pytest.approx(.0015)
    for market, tick in (("KOSPI", 500), ("KOSDAQ", 100)):
        charged = cost_model.one_way_cost(300000., market, "20230120", adv, "buy", model_version="v2")
        assert charged == pytest.approx(.00015 + tick / 300000 + .0005)


def test_cancel_variant_is_filtered_by_fixed_literal_rule_before_prices(tmp_path, monkeypatch):
    reader = b.PriceReader(tmp_path, b._days())
    monkeypatch.setattr(reader, "read", lambda *args: pytest.fail("non-cancel variant event reached prices"))
    source = event()
    source["cancel"] = False
    rows, report = b.select_events([source], reader, [item()], 21, cancel=True)
    assert not rows and report["filter_counts"]["not_cancellation_purpose"] == 1


def test_receipt_prefix_never_sets_event_clock_entry_or_period(tmp_path):
    _, days, _ = market()
    row = item("20250102", rcept_no="20241230000001")
    files = cover(tmp_path, [row], start="2024-09-01")
    save_values(tmp_path, row)
    events, report = b.construct_events([row], files, days, tmp_path)
    assert events[0]["period"] == "validation" and events[0]["rcept_no"] == "20241230000001"
    planned, reason = b.schedule(events[0], 21, days)
    assert reason is None and planned["entry_date"] == "2025-01-03"
