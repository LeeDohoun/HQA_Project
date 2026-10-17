"""FW001 preregistered recording contracts, using only synthetic local archives."""
from datetime import datetime, timedelta
from functools import lru_cache
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model
from backtesting.experiments import hc003, hf001
from src.forward import fw001 as fw
from src.ingestion import dart_financial_date_rules, dart_quarterly, krx_market
from src.ingestion.storage import read_rows, write_rows
from src.runner import trading_calendar
from dart_listing_fixtures import checkpoint
from test_forward_hegemony_shadow import filing, save_market, change_market, CODES

DAY = pd.Timestamp("2026-10-30")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("src.config.settings.load_project_env", lambda *a, **k: pytest.fail("env read"))
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 10, 31, 8, 40, tzinfo=fw.KST))


@pytest.fixture(autouse=True)
def publication_clock(monkeypatch):
    """Emulate kernel timestamps on fixture dates, without changing the host clock."""
    link = os.link
    def publish(source, target):
        link(source, target)
        target = Path(target)
        if target.name == "complete.json":
            prepared = pd.Timestamp(fw.load_json(target)["prepared_at"]).value
            os.utime(target, ns=(prepared, prepared))
            published = pd.Timestamp(fw.now()).value
            os.utime(target.parent, ns=(published, published))
    monkeypatch.setattr(os, "link", publish)
    return link


@pytest.fixture
def synthetic_future_calendar(monkeypatch):
    """Test accounting with hypothetical notices; no production 2027 approval."""
    trading_calendar.session_calendar.cache_clear()
    trading_calendar.daily_session_close.cache_clear()
    monkeypatch.setattr(trading_calendar, "CALENDAR_VERIFIED_THROUGH", "2027-12-31")
    @lru_cache(maxsize=4)
    def factory(year):
        return trading_calendar._ProjectXKRX(start=f"{year - 2}-01-01", end=f"{year}-12-31")
    monkeypatch.setattr(trading_calendar, "_calendar", factory)
    monkeypatch.setattr(trading_calendar, "PENDING_SPECIAL_SESSIONS", {})
    monkeypatch.setattr(fw, "calendar", lru_cache(maxsize=32)(fw.calendar))
    yield
    trading_calendar.session_calendar.cache_clear()
    trading_calendar.daily_session_close.cache_clear()


@pytest.fixture
def data(tmp_path):
    sessions = fw.calendar(DAY)
    pos = sessions.get_loc(DAY)
    save_market(tmp_path, sessions[pos - 59:pos + 1])
    for day in sessions[pos - 59:pos + 1]:
        path = krx_market._path(day.date(), tmp_path)
        rows = read_rows(path)
        for i, row in enumerate(rows):
            row["market"] = "KOSPI" if i < 4 else "KOSDAQ"
        write_rows(path, rows)
    profiles = [{"corp_code": f"{int(code):08d}", "stock_code": code, "corp_cls": "Y" if i < 4 else "K",
                 "induty_code": "64000" if code == "000040" else "26100"} for i, code in enumerate(CODES)]
    write_rows(tmp_path / "reference/dart_company/companies.jsonl", profiles)
    (tmp_path / "reference/corp_codes.csv").write_text("corp_code,stock_code\n" + "".join(
        f"{int(code):08d},{code}\n" for code in CODES))
    revenue, income = [120, 120, 90, 120, 120, 120, 120, 120], [15, 11, 5, 15, 16, 11, 15, 15]
    for year in (2024, 2025):
        rows = [filing(code, year, 100 if year == 2024 else revenue[i], 10 if year == 2024 else income[i])
                for i, code in enumerate(CODES)]
        write_rows(tmp_path / f"fundamentals/dart_quarterly/{year}_11012.jsonl", rows)
        write_rows(tmp_path / f"disclosures/dart_full/list/{year}/{year}0814.jsonl", [
            {"corp_code": row["corp_code"], "stock_code": row["stock_codes"][0], "rcept_no": row["rcept_no"],
             "rcept_dt": f"{year}0814", "report_nm": f"반기보고서 ({year}.06)"} for row in rows])
    for day in pd.date_range(sessions[pos - 126], DAY - pd.Timedelta(days=1)):
        write_rows(tmp_path / f"disclosures/dart_full/list/{day:%Y}/{day:%Y%m%d}.jsonl", [])
    checkpoint(tmp_path)
    return tmp_path


def entry_data(data, decision, *, end=None):
    sessions = fw.calendar(decision) if end is None else trading_calendar.session_calendar(decision, end)
    start = sessions.get_loc(pd.Timestamp(decision)) + 1
    stop = start + 1 if end is None else sessions.get_loc(pd.Timestamp(end)) + 1
    save_market(data, sessions[start:stop])
    for day in sessions[start:stop]:
        path = krx_market._path(day.date(), data)
        rows = read_rows(path)
        for i, row in enumerate(rows):
            row["market"] = "KOSPI" if i < 4 else "KOSDAQ"
        write_rows(path, rows)


def test_definitions_separate_HF_baskets_and_S_universe(data):
    record = fw.decide(DAY, data)
    frames, report = fw.financial_frames(data, DAY)
    pd.testing.assert_frame_equal(frames["stored"], dart_quarterly.load_quarterly(DAY, data_dir=data), check_flags=False)
    rules = record["rules"]
    assert rules["f_both"]["unfiltered_targets"] == ["000010", "000050"]
    assert rules["av_all"]["targets"] == ["000010", "000050"]
    assert rules["av_all"]["financial_rule"] == "S"
    assert record["listing"]["covered"] and record["financial_dates"]["L"] == 0
    assert record["label"] == "forward"
    assert record["states"]["1.0"]["cash"] == record["states"]["1.0"]["value"] == 1
    assert record["input_hashes"] and record["code_hashes"]
    assert not (data / fw.RECORD_DIR).exists()


@pytest.mark.parametrize("stamp,label", [("2026-11-02T08:58:58+09:00", "forward"),
                                          ("2026-11-02T08:59:00+09:00", "reconstructed"),
                                          ("2026-11-05T08:40:00+09:00", "reconstructed")])
def test_strict_deadline_no_three_session_grace(data, monkeypatch, stamp, label):
    monkeypatch.setattr(fw, "now", lambda: datetime.fromisoformat(stamp))
    record = fw.decide(DAY, data, execute=True)
    assert record["label"] == label
    assert fw.load_json(fw.path_for(data, "decisions", "2026-10")) == record
    assert fw.marker_path(data, "decisions", "2026-10").exists() == (label == "forward")


def test_candidate_is_not_finalised_by_its_own_publication(tmp_path, monkeypatch):
    stamps = iter(["2026-11-02T08:58:59+09:00"])
    monkeypatch.setattr(fw, "now", lambda: datetime.fromisoformat(next(stamps)))
    record = fw.put(tmp_path / "decision.json", {}, execute=True,
                    deadline=datetime(2026, 11, 2, 8, 59, tzinfo=fw.KST))
    assert record["label"] == "forward"
    assert fw.load_json(tmp_path / "decision.json") == record
    assert fw.completion_proof(tmp_path, "decisions", "2026-10") is None


def test_immutable_decision_execution_and_separate_rerun(data, monkeypatch):
    decision = fw.decide(DAY, data, execute=True)
    original = fw.path_for(data, "decisions", "2026-10").read_bytes()
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    for m in hf001.MULTIPLIERS:
        first = fw.execution(decision, data, multiplier=m, execute=True)
        assert first["av_all"]["status"] == "executed"
        path = fw.path_for(data, "executions", "2026-10", m)
        saved = path.read_bytes()
        change_market(data, pd.Timestamp(decision["trade_date"]), "000010", open=9000)
        assert fw.execution(decision, data, multiplier=m, execute=True) == first
        assert path.read_bytes() == saved
    change_market(data, DAY, "000010", trading_value=3e9)
    assert fw.decide(DAY, data, execute=True) == decision
    rerun = fw.decide(DAY, data, execute=True, append_rerun=True)
    assert rerun["label"] == "reconstructed" and rerun["record_type"] == "rerun"
    assert fw.path_for(data, "decisions", "2026-10").read_bytes() == original


def test_incomplete_listing_or_market_never_becomes_observation(data):
    state = data / "disclosures/dart_full/_state.json"
    original = fw.load_json(state)
    original["completed_listing_days"].remove("20261001")
    state.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="126-session"):
        fw.decide(DAY, data, execute=True)
    assert not fw.path_for(data, "decisions", "2026-10").exists()


def test_merged_supplement_and_frozen_scan_protect_holdout(data, monkeypatch):
    original_open = Path.open
    def guarded(path, *args, **kwargs):
        if path.parent.parent.name == "krx_daily" and "20260101" <= path.stem <= "20260630":
            pytest.fail("protected price contents opened")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    fw.decide(DAY, data)
    state = data / "disclosures/dart_full/list_supplement/_state.json"
    broken = fw.load_json(state)
    del broken["target_scan"]
    state.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="126-session"):
        fw.decide(DAY, data)


def test_chain_carry_matches_single_stream_and_no_trade_band(data, synthetic_future_calendar):
    # Hypothetical special-hours coverage is confined to this accounting fixture.
    decision = fw.decide(DAY, data)
    entry_data(data, DAY, end="2026-11-30")
    sessions = fw.calendar(DAY)
    days = sessions[(sessions >= pd.Timestamp("2026-11-02")) & (sessions <= pd.Timestamp("2026-11-30"))]
    combined = fw.book_from_days(data, days, entry=days[0], chain=decision["chain"])
    first = fw.book_from_days(data, days[:10], entry=days[0], chain=decision["chain"])
    second = fw.book_from_days(data, days[10:], chain=first.chain_state)
    for code, row in second.chain_state.items():
        assert row["adjusted_close"] == pytest.approx(combined.chain_state[code]["adjusted_close"], abs=1e-14)
        assert {k: v for k, v in row.items() if k != "adjusted_close"} == {
            k: v for k, v in combined.chain_state[code].items() if k != "adjusted_close"}
    state, _ = hc003.rebalance(hc003.PortfolioState(), first.quotes(days[0]), {"000010", "000050"}, days[0])
    hold, report = hc003.rebalance(state, first.quotes(days[0]), {"000020"}, days[0], no_trade=True)
    assert hold.to_dict() == state.to_dict() and report["trades"] == [] and report["cost"] == 0


def test_missing_month_is_no_bundle_no_trade_and_counted(data, monkeypatch):
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    report = fw.daily(data, execute=True)
    assert report["missing"] == ["2026-10"] and report["missing_months"] == 1
    row = fw.load_json(fw.path_for(data, "executions", "2026-10", 1.0))
    assert row["f_both"]["baskets"] == {}
    assert row["av_all"]["no_trade"] and row["av_all"]["state_after"] == {"cash": 1.0, "quantities": {}}


def test_late_reconstruction_never_changes_a_sealed_missing_transition(data, monkeypatch):
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    fw.daily(data, execute=True)
    path = fw.path_for(data, "executions", "2026-10", 1.0)
    original = path.read_bytes()
    late = fw.decide(DAY, data, execute=True)
    assert late["label"] == "reconstructed"
    report = fw.daily(data, execute=True)
    assert report["missing"] == ["2026-10"] and path.read_bytes() == original


def test_omitted_first_month_keeps_known_cash_without_inventing_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 12, 3, 8, 40, tzinfo=fw.KST))
    report = fw.daily(tmp_path, execute=True)
    assert report["missing"] == ["2026-10", "2026-11"]
    for month in report["missing"]:
        for m in fw.hf001.MULTIPLIERS:
            record = fw.load_json(fw.path_for(tmp_path, "executions", month, m))
            assert record["av_all"]["state_after"] == {"cash": 1.0, "quantities": {}}
            assert record["av_all"]["accounting"]["trades"] == []
            assert record["quotes"] == {} and record["input_hashes"] == {}
    assert not (tmp_path / "market").exists()


def test_2027_buy_needs_no_tax_but_sell_evaluation_blocks(synthetic_future_calendar):
    assert cost_model.one_way_cost(10_000, "KOSPI", "20270104", 2e9, "buy", model_version="v2") > 0
    with pytest.raises(ValueError, match="unknown tax year: 2027"):
        cost_model.one_way_cost(10_000, "KOSPI", "20270104", 2e9, "sell", model_version="v2")
    with pytest.raises(ValueError, match="legally sourced"):
        fw.require_2027_costs()


def test_freeze_chooses_server_copy_when_PC_hash_differs(data, tmp_path):
    descriptor = fw.freeze_price(data, DAY, execute=True)
    pc = tmp_path / "pc"
    pc_path = pc / descriptor["path"]
    pc_path.parent.mkdir(parents=True)
    pc_path.write_text('{"wrong":true}\n')
    frame = fw.recorded_price_window(data, pd.DatetimeIndex([DAY]), pc_dir=pc)
    assert len(frame) == len(CODES)
    blob = data / fw.RECORD_DIR / "snapshots" / (descriptor["sha256"] + ".jsonl")
    blob.write_text("corrupt\n")
    with pytest.raises(ValueError, match="corrupt"):
        fw.recorded_price_window(data, pd.DatetimeIndex([DAY]), pc_dir=pc)


@pytest.mark.parametrize("loader", [fw.price_window, fw.recorded_price_window])
@pytest.mark.parametrize("days,status,error", [
    (["2026-11-18"], "verified", None),
    (["2026-11-20"], "verified", None),
    (["2026-11-18", "2026-11-19", "2026-11-20"], "verified", "calendar_special_session_coverage_unverified"),
    (["2026-11-18"], "unverified_special_session", "unverified calendar sessions"),
])
def test_all_price_window_sessions_require_verified_calendar(tmp_path, loader, days, status, error):
    days = pd.DatetimeIndex(days)
    save_market(tmp_path, days)
    for day in days:
        change_market(tmp_path, day, CODES[0], calendar_status=status)
    if error:
        with pytest.raises(ValueError, match=error):
            loader(tmp_path, days)
    else:
        assert len(loader(tmp_path, days)) == len(days) * len(CODES)


def test_date_filter_preserves_global_L_and_conflict_rule_S():
    rows = [{"corp_code": "00000001", "stock_code": "000001", "rcept_no": "20150601000001",
             "rcept_dt": "20150618", "report_nm": "반기보고서 (2015.06)"},
            {"corp_code": "00000002", "stock_code": "000002", "rcept_no": "20261001000002",
             "rcept_dt": "20261002", "report_nm": "반기보고서 (2026.06)"}]
    dates = dart_financial_date_rules.FilingDates(rows, receipt_filter={"20261001000002"})
    assert dates.L == 17 and list(dates.receipts) == ["20261001000002"]
    assert dates.annotate({"corp_code": "00000002", "rcept_no": "20261001000002"})["actual_public_date"] == "2026-10-02"


def test_calendar_and_legacy_special_session_guard_is_not_bypassed():
    assert fw.plan("2026-10-30")["trade_date"] == "2026-11-02"
    assert pd.Timestamp("2026-07-17") not in fw.calendar(DAY)
    with pytest.raises(ValueError, match="calendar_unverified"):
        fw.plan("2027-09-30")
    assert trading_calendar.daily_session_close("2026-11-02")
    with pytest.raises(ValueError, match="official_KRX_notice_required"):
        trading_calendar.daily_session_close("2026-11-19")


def test_missing_volatility_session_is_uncomputable_not_an_exclusion(data):
    sessions = fw.calendar(DAY)
    old = sessions[sessions.get_loc(DAY) - 40]
    krx_market._path(old.date(), data).unlink()
    checkpoint(data)  # Fixture's frozen inventory was collected with this actual gap.
    record = fw.decide(DAY, data)
    assert record["missing_price_sessions"] == [str(old.date())]
    for rule in record["rules"].values():
        assert rule["vol_uncomputable"] == len(rule["universe"])
        assert rule["F_vol"] == []


def test_S_actual_date_is_strict_while_HF_keeps_stored_date(data):
    raw = filing("000010", 2026, 240, 60, day="2026-10-29")
    write_rows(data / "fundamentals/dart_quarterly/2026_11012.jsonl", [raw])
    write_rows(data / "disclosures/dart_full/list/2026/20261030.jsonl", [{
        "rcept_no": raw["rcept_no"], "rcept_dt": "20261030", "corp_code": raw["corp_code"],
        "stock_code": "000010", "report_nm": "반기보고서 (2026.06)"}])
    record = fw.decide(DAY, data)
    assert next(r for r in record["rules"]["f_both"]["universe"] if r["stock_code"] == "000010")["fiscal_quarter"] == "2026Q2"
    assert next(r for r in record["rules"]["av_all"]["universe"] if r["stock_code"] == "000010")["fiscal_quarter"] == "2025Q2"


def test_supplement_raise_event_and_HF_limit_up_cash_slot(data, monkeypatch):
    write_rows(data / "disclosures/dart_full/list_supplement/2026/20261001.jsonl", [{
        "corp_code": "00000050", "stock_code": "000050", "rcept_no": "20261001000500",
        "rcept_dt": "20261001", "report_nm": "주요사항보고서(유상증자결정)"}])
    checkpoint(data, corps=["00000050"])
    decision = fw.decide(DAY, data, execute=True)
    assert decision["rules"]["f_both"]["targets"] == ["000010"]
    assert decision["rules"]["f_both"]["unfiltered_targets"] == ["000010", "000050"]
    entry_data(data, DAY)
    change_market(data, pd.Timestamp(decision["trade_date"]), "000010", open=13000)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    row = fw.execution(decision, data, multiplier=1.0, execute=True)
    assert row["f_both"]["slots"]["000010"]["reason"] == "limit_up_entry"
    assert row["f_both"]["baskets"]["filtered"]["cash"] == 1.0
    assert row["f_both"]["baskets"]["unfiltered"]["cash"] == 0.5
    assert row["f_both"]["baskets"]["unfiltered"]["raw_quantities"]["000050"] == 0.5 / 10000


def test_non_finalised_dry_run_cannot_create_an_observation_execution(data, monkeypatch):
    decision = fw.decide(DAY, data)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    with pytest.raises(ValueError, match="atomically finalised"):
        fw.execution(decision, data, multiplier=1.0, execute=True)
    assert not fw.path_for(data, "executions", "2026-10", 1.0).exists()


def test_2027_pending_HF_cost_resolution_preserves_already_sealed_AV_fills(data, monkeypatch, synthetic_future_calendar):
    decision = fw.decide(DAY, data)
    decision.update(fw.plan("2026-12-30"), chain_date="2026-12-30")
    entry_data(data, pd.Timestamp("2026-12-30"))
    monkeypatch.setattr(fw, "now", lambda: datetime(2027, 1, 5, 8, 40, tzinfo=fw.KST))
    row = fw.execution(decision, data, multiplier=1.0)
    assert row["av_all"]["status"] == "executed"  # Buys need no future sell tax.
    assert any(slot["round_trip_cost"] is None for slot in row["f_both"]["slots"].values())
    path = fw.path_for(data, "executions", "2026-12", 1.0)
    fw.put(path, row, execute=True)  # Fixture injects an immutable pending execution.
    # This cost-resolution unit fixture is independent of finalisation coverage.
    monkeypatch.setattr(fw, "completion_proof", lambda *a, **k: {"fixture": True})
    original = path.read_bytes()
    monkeypatch.setattr(cost_model, "one_way_cost", lambda *a, **k: 0.001 * k.get("multiplier", 1.0))
    resolution = fw.resolve_costs(decision, data, multiplier=1.0, execute=True)
    assert resolution["record_type"] == "cost_resolution"
    assert resolution["av_all"] == row["av_all"] and path.read_bytes() == original
    assert all(slot["round_trip_cost"] is not None for slot in resolution["f_both"]["slots"].values())
    assert fw.effective_execution(data, "2026-12", 1.0) == resolution


def test_unknown_2027_sale_seals_inputs_and_does_not_guess_cash_or_buys(data, monkeypatch, synthetic_future_calendar):
    decision = fw.decide(DAY, data)
    decision.update(fw.plan("2026-12-30"), chain_date="2026-12-30")
    decision["states"]["1.0"] = {"cash": 0.0, "quantities": {"000020": 1.0}}
    entry_data(data, pd.Timestamp("2026-12-30"))
    monkeypatch.setattr(fw, "now", lambda: datetime(2027, 1, 5, 8, 40, tzinfo=fw.KST))
    record = fw.execution(decision, data, multiplier=1.0)
    assert record["av_all"]["status"] == "cost_evaluation_blocked"
    assert record["av_all"]["state_after"] is None
    assert record["av_all"]["state_before"] == {"cash": 0.0, "quantities": {"000020": 1.0}}
    assert record["input_hashes"] and record["quotes"]


@pytest.mark.parametrize("restart,expected", [("2026-11-02T08:58:00+09:00", "forward"),
                                            ("2026-11-02T08:59:00+09:00", "reconstructed")])
def test_crash_after_candidate_before_completion_cannot_admit_a_late_decision(data, monkeypatch, restart, expected):
    def crash(*args, **kwargs):
        raise KeyboardInterrupt("crash before proof publication")
    with monkeypatch.context() as patch:
        patch.setattr(fw, "publish_completion", crash)
        with pytest.raises(KeyboardInterrupt):
            fw.decide(DAY, data, execute=True)
    assert fw.path_for(data, "decisions", "2026-10").is_file()
    assert fw.completion_proof(data, "decisions", "2026-10") is None
    monkeypatch.setattr(fw, "now", lambda: datetime.fromisoformat(restart))
    recovered = fw.decide(DAY, data, execute=True)
    assert recovered["label"] == expected
    proof = fw.completion_proof(data, "decisions", "2026-10")
    assert (proof is not None) == (expected == "forward")
    if proof:
        assert proof["sealed_at"] == restart
        assert proof["record_digest"] == fw.dart_listing.file_hash(fw.path_for(data, "decisions", "2026-10"))


@pytest.mark.parametrize("published,valid", [("2026-11-02T08:58:58.999999999+09:00", True),
                                            ("2026-11-02T08:58:59.999999999+09:00", False),
                                            ("2026-11-02T08:59:00+09:00", False),
                                            ("2026-11-02T08:59:01+09:00", False)])
def test_crash_immediately_after_marker_publication_proves_actual_deadline(tmp_path, monkeypatch, published, valid):
    prepared = "2026-11-02T08:58:58+09:00"
    monkeypatch.setattr(fw, "now", lambda: datetime.fromisoformat(prepared))
    record = {"month": "2026-10", "label": "forward", "finalised_at": prepared,
              "deadline": "2026-11-02T08:59:00+09:00", "input_hashes": {}, "input_snapshots": {}}
    fw.put(fw.path_for(tmp_path, "decisions", "2026-10"), record, execute=True)
    link = os.link
    def crash(source, target):
        link(source, target)
        target = Path(target)
        if target.name == "complete.json":
            stamp = pd.Timestamp(published).value
            os.utime(target.parent, ns=(stamp, stamp))
            raise KeyboardInterrupt("crash immediately after atomic publication")
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", crash)
        with pytest.raises(KeyboardInterrupt):
            fw.complete(tmp_path, "decisions", record, execute=True)
    marker = fw.marker_path(tmp_path, "decisions", "2026-10")
    original = marker.read_bytes()
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    if valid:
        proof = fw.completion_proof(tmp_path, "decisions", "2026-10")
        assert pd.Timestamp(proof["sealed_at"]) == pd.Timestamp(published)
        assert fw.complete(tmp_path, "decisions", record, execute=True) == record
    else:
        assert fw.completion_proof(tmp_path, "decisions", "2026-10") is None
        candidate = fw.path_for(tmp_path, "decisions", "2026-10")
        original_record = candidate.read_bytes()
        assert fw.complete(tmp_path, "decisions", record, execute=True) == record
        assert candidate.read_bytes() == original_record
    assert marker.read_bytes() == original  # No post-publication revoke or rewrite.


def test_deadline_check_is_after_marker_fsync_before_publication(tmp_path, monkeypatch):
    prepared = "2026-11-02T08:58:58+09:00"
    late = datetime(2026, 11, 2, 8, 59, tzinfo=fw.KST)
    monkeypatch.setattr(fw, "now", lambda: datetime.fromisoformat(prepared))
    record = {"month": "2026-10", "label": "forward", "finalised_at": prepared,
              "deadline": late.isoformat(), "input_hashes": {}, "input_snapshots": {}}
    fw.put(fw.path_for(tmp_path, "decisions", "2026-10"), record, execute=True)
    fsync = os.fsync
    def delay(fd):
        fsync(fd)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            monkeypatch.setattr(fw, "now", lambda: late)
    monkeypatch.setattr(os, "fsync", delay)
    result = fw.complete(tmp_path, "decisions", record, execute=True)
    assert result["label"] == "reconstructed"
    assert not fw.marker_path(tmp_path, "decisions", "2026-10").exists()


def test_publication_uses_kernel_clock_and_survives_copy_with_preserved_mtimes(tmp_path, monkeypatch, publication_clock):
    # Undo only the emulated link: exercise actual kernel publication metadata.
    monkeypatch.setattr(os, "link", publication_clock)
    monkeypatch.setattr(fw, "now", lambda: datetime.now(fw.KST))
    stamp = datetime.now(fw.KST)
    record = {"month": "2026-10", "label": "forward", "finalised_at": stamp.isoformat(),
              "deadline": (stamp + timedelta(minutes=5)).isoformat(), "input_hashes": {}, "input_snapshots": {}}
    fw.put(fw.path_for(tmp_path, "decisions", "2026-10"), record, execute=True)
    fw.complete(tmp_path, "decisions", record, execute=True)
    proof = fw.completion_proof(tmp_path, "decisions", "2026-10")
    assert pd.Timestamp(proof["sealed_at"]) <= pd.Timestamp(datetime.now(fw.KST))
    assert pd.Timestamp(proof["publication_not_after"]) >= pd.Timestamp(stamp)
    copy = tmp_path / "copied"
    shutil.copytree(tmp_path / fw.RECORD_DIR, copy / fw.RECORD_DIR)
    assert fw.completion_proof(copy, "decisions", "2026-10") == proof
    marker = fw.marker_path(copy, "decisions", "2026-10")
    late = pd.Timestamp(record["deadline"]).value
    os.utime(marker.parent, ns=(late, late))
    assert fw.completion_proof(copy, "decisions", "2026-10") is None


@pytest.mark.parametrize("late", [False, True])
def test_process_exit_immediately_after_native_kernel_publication(tmp_path, late):
    code = """
import os, sys, time
from datetime import timedelta
from pathlib import Path
def guard(event,args):
 if event=='open' and isinstance(args[0],str) and Path(args[0]).name.startswith('.env'):
  raise AssertionError('env read')
 if event.startswith(('socket.connect','socket.getaddrinfo')):
  raise AssertionError('network')
sys.addaudithook(guard)
from src.forward import fw001 as fw
root=Path(sys.argv[1])
stamp=fw.now()
deadline=stamp+timedelta(seconds=2)
record={'month':'2026-10','label':'forward','finalised_at':stamp.isoformat(),
        'deadline':deadline.isoformat(),'input_hashes':{},'input_snapshots':{}}
fw.put(fw.path_for(root,'decisions','2026-10'),record,execute=True)
link=os.link
def crash(source,target):
 if Path(target).name=='complete.json':
  if sys.argv[2]=='late': time.sleep(max(0,(deadline-fw.now()).total_seconds())+0.05)
  link(source,target)
  os._exit(73)
 link(source,target)
os.link=crash
fw.complete(root,'decisions',record,execute=True)
"""
    child = subprocess.run([sys.executable, "-c", code, str(tmp_path), "late" if late else "early"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1"})
    assert child.returncode == 73, child.stderr
    marker = fw.marker_path(tmp_path, "decisions", "2026-10")
    original = marker.read_bytes()
    if late:
        assert fw.completion_proof(tmp_path, "decisions", "2026-10") is None
    else:
        assert fw.completion_proof(tmp_path, "decisions", "2026-10") is not None
    assert marker.read_bytes() == original
    # os._exit bypassed all finally blocks; the unremoved temporary is outside
    # the private publication directory and cannot change its clock.
    assert list(marker.parent.parent.glob(".fw001-proof-*"))


@pytest.mark.parametrize("offset,included", [(-1, True), (0, True), (1, False)])
def test_completion_freeze_preserves_nanosecond_publication_bound(tmp_path, monkeypatch, offset, included):
    prepared = fw.FREEZE_AT - timedelta(seconds=2)
    monkeypatch.setattr(fw, "now", lambda: prepared)
    record = {"month": "2026-10", "label": "forward", "finalised_at": prepared.isoformat(),
              "deadline": (fw.FREEZE_AT + timedelta(days=1)).isoformat(),
              "input_hashes": {}, "input_snapshots": {}}
    fw.put(fw.path_for(tmp_path, "decisions", "2026-10"), record, execute=True)
    fw.complete(tmp_path, "decisions", record, execute=True)
    marker = fw.marker_path(tmp_path, "decisions", "2026-10")
    publication_ns = pd.Timestamp(fw.FREEZE_AT).value - fw.PUBLICATION_PRECISION.value + offset
    os.utime(marker.parent, ns=(publication_ns, publication_ns))
    monkeypatch.setattr(fw, "now", lambda: fw.FREEZE_AT)
    freeze = fw.seal_evaluation_inputs(tmp_path)
    assert (marker.relative_to(tmp_path).as_posix() in freeze["input_hashes"]) == included


def test_preserved_inputs_recompute_after_mutable_archives_are_destroyed(data, tmp_path):
    write_rows(data / "disclosures/dart_full/list_supplement/2026/20261001.jsonl", [{
        "corp_code": "00000050", "stock_code": "000050", "rcept_no": "20261001000500",
        "rcept_dt": "20261001", "report_nm": "주요사항보고서(유상증자결정)"}])
    checkpoint(data, corps=["00000050"])
    decision = fw.decide(DAY, data, execute=True)
    restored = tmp_path / "restored-inputs"
    for name, item in decision["input_snapshots"].items():
        source, target = data / item["blob"], restored / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        (data / name).write_text("destroyed mutable source\n")
    fw.verify_input_snapshots(data, decision)
    recomputed = fw.decide(DAY, restored)
    for field in ("rules", "chain", "states", "financial_dates", "raise_events", "input_hashes"):
        assert recomputed[field] == decision[field]
    item = next(iter(decision["input_snapshots"].values()))
    (data / item["blob"]).write_text("corrupt\n")
    with pytest.raises(ValueError, match="snapshot unavailable or corrupt"):
        fw.verify_input_snapshots(data, decision)


@pytest.mark.parametrize("source_change", ["deleted", "corrupt"])
def test_partial_execution_restart_uses_only_verified_sealed_entry_bytes(data, monkeypatch, source_change):
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    original = fw.execution(decision, data, multiplier=1.0, execute=True)
    # Also recover a crash between execution publication and its own proof.
    fw.marker_path(data, "executions", "2026-10", 1.0).unlink()
    source = krx_market._path(pd.Timestamp(decision["trade_date"]).date(), data)
    if source_change == "deleted":
        source.unlink()
    else:
        source.write_text("invalid JSON\n")
    original_open = Path.open
    def no_mutable_entry(path, *args, **kwargs):
        if path == source:
            pytest.fail("restart read mutable entry source")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_mutable_entry)
    report = fw.daily(data, execute=True)
    assert len(report["executions"]) == 3 and not report["missing"]
    for m in hf001.MULTIPLIERS:
        record = fw.effective_execution(data, "2026-10", m)
        assert record["input_hashes"] == original["input_hashes"]
        proof = fw.completion_proof(data, "executions", "2026-10", m)
        assert proof["sealed_at"] == "2026-11-03T08:40:00+09:00"
        assert proof["deadline"] == decision["deadline"]


def test_corrupt_entry_blob_fails_recovery_and_blob_publication_fsyncs_directory(data, monkeypatch):
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    directories = []
    real_sync = fw.fsync_directory
    def sync(path):
        directories.append(path)
        real_sync(path)
    monkeypatch.setattr(fw, "fsync_directory", sync)
    fw.execution(decision, data, multiplier=1.0, execute=True)
    assert data / fw.RECORD_DIR / "snapshots" in directories
    descriptor = fw.load_json(fw.path_for(data, "execution_prices", "20261102"))
    (data / fw.RECORD_DIR / "snapshots" / (descriptor["sha256"] + ".jsonl")).write_text("corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        fw.execution(decision, data, multiplier=1.5, execute=True)
    assert not fw.path_for(data, "executions", "2026-10", 1.5).exists()


def test_unverified_2027_months_are_missing_and_preserve_last_known_holdings(data, monkeypatch):
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    fw.daily(data, execute=True)
    original = fw.effective_execution(data, "2026-10", 1.0)
    assert original["av_all"]["state_after"]["quantities"]
    monkeypatch.setattr(fw, "now", lambda: datetime(2027, 2, 1, 8, tzinfo=fw.KST))
    report = fw.daily(data, execute=True)
    assert "2027-01" in report["missing"] and report["status"] == "waiting"
    omitted = fw.load_json(fw.path_for(data, "missing", "2027-01"))
    assert omitted["calendar_status"] == "unverified" and omitted["trade_date"] is None
    november = fw.load_json(fw.path_for(data, "missing", "2026-11"))
    assert omitted["states"] == november["states"]
    assert omitted["states"]["1.0"]["quantities"] == original["av_all"]["state_after"]["quantities"]
    assert not fw.path_for(data, "executions", "2027-01", 1.0).exists()
    with pytest.raises(ValueError, match="calendar_unverified"):
        fw.execution(omitted, data, multiplier=1.0, execute=True)


def test_execution_candidate_cannot_get_a_proof_with_corrupt_sealed_inputs(data, monkeypatch):
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, tzinfo=fw.KST))
    fw.execution(decision, data, multiplier=1.0, execute=True)
    marker = fw.marker_path(data, "executions", "2026-10", 1.0)
    marker.unlink()  # Simulate an interruption before proof publication.
    descriptor = fw.load_json(fw.path_for(data, "execution_prices", "20261102"))
    (data / fw.RECORD_DIR / "snapshots" / (descriptor["sha256"] + ".jsonl")).write_text("corrupt")
    with pytest.raises(ValueError, match="sealed input unavailable or corrupt"):
        fw.execution(decision, data, multiplier=1.0, execute=True)
    assert not marker.exists()


def test_missing_state_recovery_does_not_rebuild_a_chain_from_mutable_history(data, monkeypatch):
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, tzinfo=fw.KST))
    fw.execution(decision, data, multiplier=1.0, execute=True)
    unavailable = {**decision, "states": None, "chain": None, "state_error": "lost state"}
    raw_open = Path.open
    def immutable_only(path, *args, **kwargs):
        if path.is_relative_to(data / "market/krx_daily"):
            pytest.fail("recovery read mutable history")
        return raw_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", immutable_only)
    record = fw.execution(unavailable, data, multiplier=1.5, rehearsal=True)
    assert record["av_all"]["status"] == "insufficient" and record["av_all"]["state_after"] is None


def test_missing_month_keeps_holdings_but_cannot_execute_with_an_unvalued_old_chain(data, monkeypatch):
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, tzinfo=fw.KST))
    fw.daily(data, execute=True)
    original = fw.effective_execution(data, "2026-10", 1.0)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 12, 3, 8, tzinfo=fw.KST))
    fw.daily(data, execute=True)  # November prices are unavailable; keep the known state.
    omitted = fw.load_json(fw.path_for(data, "missing", "2026-11"))
    assert omitted["states"]["1.0"]["quantities"] == original["av_all"]["state_after"]["quantities"]
    assert omitted["chain_date"] == original["chain_date"]
    entry_data(data, pd.Timestamp("2026-11-30"))  # Only the new entry day arrives.
    with pytest.raises(ValueError, match="sealed decision-close chain"):
        fw.daily(data, execute=True)
    assert not fw.path_for(data, "executions", "2026-11", 1.0).exists()
    assert fw.effective_execution(data, "2026-10", 1.0) == original
