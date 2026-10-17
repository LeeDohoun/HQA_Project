import json
import time

import pandas as pd
import pytest

from dart_listing_fixtures import checkpoint
from src.ingestion import dart_listing as listing, dart_buyback, dart_business_text, dart_quarterly
from src.ingestion.storage import write_rows
from src.forward import hegemony_shadow as shadow
from backtesting.experiments import b001, hf001, hc003, bb001
from backtesting.experiments.lh001 import inputs
from backtesting.experiments.lh001 import __main__ as lh_cli


def row(day="20240102", number=1, **kwargs):
    return {"rcept_no": day + f"{number:06d}", "rcept_dt": day, "corp_code": "00000001",
            "stock_code": "000010", "corp_name": "과거상장", "corp_cls": "E",
            "report_nm": "유상증자결정", "flr_nm": "회사", "rm": "", **kwargs}


def save(data, rows, supplement=False):
    folder = "list_supplement" if supplement else "list"
    for day in {r["rcept_dt"] for r in rows}:
        write_rows(listing.directory(data) / folder / day[:4] / f"{day}.jsonl",
                   [r for r in rows if r["rcept_dt"] == day])


def test_merge_same_duplicates_supplement_only_days_and_conflicts(tmp_path):
    r = row()
    save(tmp_path, [r])
    save(tmp_path, [r, row(number=2)], True)
    assert listing.listing_rows(tmp_path, "2024-01-02") == [r, row(number=2)]
    save(tmp_path, [row("20240103")], True)
    assert set(listing.listing_paths(tmp_path)) == {"20240102", "20240103"}
    save(tmp_path, [{**r, "report_nm": "다른 공시"}], True)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        listing.listing_rows(tmp_path, "20240102")


def test_merged_overlap_preserves_main_current_fields(tmp_path):
    main = row(corp_cls="Y", corp_name="당시명", stock_code="000020", flr_nm="이전제출인", rm="이전비고")
    save(tmp_path, [main])
    save(tmp_path, [row()], True)
    assert listing.listing_rows(tmp_path, "20240102") == [main]


def test_status_hashes_target_integrity_and_read_range_refusal(tmp_path):
    assert not listing.listing_status(tmp_path)["supplement_complete"]
    with pytest.raises(ValueError, match="supplement incomplete"):
        listing.require_supplement(tmp_path, ["20240102"])
    state = checkpoint(tmp_path, end="20240102", corps=["00000001"])
    before = listing.listing_version(tmp_path)
    assert listing.require_supplement(tmp_path, ["20240102"]) == before
    assert listing.require_supplement(tmp_path, ["20240103"]) == before
    with pytest.raises(ValueError, match="range"):
        listing.require_supplement(tmp_path, ["20150531"])
    state["completed_corps"] = []
    for field in ("rows_per_corp", "skipped_per_corp", "duplicates_per_corp"):
        state[field] = {}
    path = listing.directory(tmp_path) / "list_supplement/_state.json"
    path.write_text(json.dumps(state))
    assert listing.listing_version(tmp_path) != before
    with pytest.raises(ValueError, match="pending_corps"):
        listing.require_supplement(tmp_path, ["20240102"])
    state["target_set_hash"] = "bad"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="target or range"):
        listing.listing_status(tmp_path)


def test_reference_refresh_keeps_complete_supplement_and_both_hashes(tmp_path):
    checkpoint(tmp_path)
    before = listing.listing_status(tmp_path)
    profile = tmp_path / "reference/dart_company/companies.jsonl"
    write_rows(profile, [{"corp_code": "00000001", "corp_cls": "Y", "corp_name": "주간갱신"}])
    (tmp_path / "reference/corp_codes.csv").write_text("corp_code,stock_code\n00000001,000010\n00000002,000020\n")
    after = listing.listing_status(tmp_path)
    assert after["supplement_complete"]
    assert after["reference_version_at_collection"] == before["reference_version_now"]
    assert after["reference_version_now"] != after["reference_version_at_collection"]
    listing.require_supplement(tmp_path, ["20240102"])


def test_counts_only_complete_main_and_complete_supplement_days(tmp_path):
    sessions = pd.bdate_range("2024-01-02", periods=60)
    expected = [f"{d:%Y%m%d}" for d in pd.date_range(sessions[0], sessions[-1])]
    for day in expected:
        write_rows(listing.directory(tmp_path) / "list" / day[:4] / f"{day}.jsonl", [])
    save(tmp_path, [row()], True)
    state = checkpoint(tmp_path, end=expected[-2], days=expected, corps=["00000001"])
    assert inputs.disclosure_counts(tmp_path, sessions, {"000010"})["000010"]["counts"]["capital_raise"] == 1
    missing_path = listing.directory(tmp_path) / "list" / expected[-1][:4] / f"{expected[-1]}.jsonl"
    missing_path.unlink()
    partial = inputs.disclosure_counts(tmp_path, sessions, {"000010", "000020"})
    assert partial["000010"]["counts"] is None
    assert partial["000010"]["coverage"]["days"] == len(expected) - 1
    checkpoint(tmp_path, end=expected[-1], days=expected[:-1], corps=["00000001"])
    assert inputs.disclosure_counts(tmp_path, sessions, {"000010"})["000010"]["counts"] is None
    write_rows(missing_path, [])
    checkpoint(tmp_path, end=expected[-1], days=expected, corps=["00000001"])
    complete = inputs.disclosure_counts(tmp_path, sessions, {"000010", "000020"})
    assert complete["000010"]["counts"]["capital_raise"] == 1
    assert set(complete["000020"]["counts"].values()) == {0}


@pytest.mark.parametrize("runner", [hf001, b001, bb001])
def test_research_runners_refuse_before_trials_or_results(tmp_path, monkeypatch, runner):
    monkeypatch.setattr(runner, "_registration", lambda *a: None)
    monkeypatch.setattr(runner, "write_results", lambda *a, **k: pytest.fail("refusal wrote results"))
    monkeypatch.setattr(runner.experiment_registry, "record_trial", lambda *a, **k: pytest.fail("refusal wrote registry"))
    with pytest.raises(ValueError, match="supplement incomplete"):
        runner.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=pd.bdate_range("2024-01-02", periods=60), prices=pd.DataFrame())


def test_hc003_and_new_lh_snapshot_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(hc003.common, "guard_prices", lambda *a, **k: None)
    days = pd.bdate_range("2024-01-02", periods=60)
    prices = pd.DataFrame(index=pd.MultiIndex.from_product([days, ["000010"]], names=["trade_date", "stock_code"]))
    planned = [{"decision_date": days[0], "end_date": days[-1]}]
    with pytest.raises(ValueError, match="supplement incomplete"):
        hc003._prepare(prices, planned, data_dir=tmp_path, repo_root=tmp_path, sessions=days, files={})
    with pytest.raises(ValueError, match="supplement incomplete"):
        lh_cli._prepare_decisions(planned, data_dir=tmp_path, repo_root=tmp_path, sessions=days, with_text=False)


def test_cached_snapshot_keeps_legacy_hash_and_never_rebuilds(tmp_path):
    path = tmp_path / "screening_snapshot.json"
    data = {"prepared": [], "observations": [], "exclusions": []}
    stored = {"data": data, "metadata": {"stage": "screening"}, "sha256": inputs.digest(data)}
    path.write_text(json.dumps(stored))
    before = path.read_bytes()
    actual = lh_cli._snapshot(path, lambda: pytest.fail("rebuilt frozen cache"), metadata=stored["metadata"])
    assert actual == data and path.read_bytes() == before


def test_all_consumers_see_supplement_rows(tmp_path):
    event = row(report_nm="자기주식취득결정")
    periodic = row(number=2, report_nm="분기보고서 (2023.09)")
    save(tmp_path, [event, periodic], True)
    files = hf001._listing_files(tmp_path)
    assert len(hf001.load_listings(files, data_dir=tmp_path)) == 2
    assert len(b001.load_listings(tmp_path, repo_root=tmp_path)[0]) == 2
    assert len(dart_buyback.load_listings(tmp_path)[0]) == 2
    assert dart_business_text.select_report("000010", "2024-01-03", tmp_path)["rcept_no"] == periodic["rcept_no"]
    master = tmp_path / "reference/corp_codes.csv"
    master.parent.mkdir()
    master.write_text("corp_code,stock_code\n00000002,000020\n")
    main = listing.directory(tmp_path) / "list"
    main.mkdir()
    universe = dart_quarterly.load_universe(corp_codes_path=master, listing_dir=main)
    assert universe == {"00000001": ["000010"], "00000002": ["000020"]}


def test_shadow_absent_supplement_nonfailure_and_records_available_supplement(tmp_path):
    start, end = pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")
    tags, status = shadow._capital_raise_tags(tmp_path, start, end, ["000010"])
    assert tags == {"000010": "unknown"} and status["source"] == "main"
    save(tmp_path, [row()], True)
    tags, status = shadow._capital_raise_tags(tmp_path, start, end, ["000010"])
    assert tags["000010"] is True and status["source"] == "main+supplement"
    assert not status["supplement_complete"] and status["listing_version"]["supplement_state_sha256"] is None


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed", "count", "legacy"])
def test_manifest_fails_closed_and_version_tracks_actual_files(tmp_path, mutation):
    save(tmp_path, [row()], True)
    state = checkpoint(tmp_path, corps=["00000001"])
    path = listing.directory(tmp_path) / "list_supplement/2024/20240102.jsonl"
    before = listing.listing_version(tmp_path)
    if mutation == "missing":
        path.unlink()
    elif mutation == "extra":
        save(tmp_path, [row("20240103")], True)
    elif mutation == "changed":
        save(tmp_path, [row(report_nm="바뀐 공시")], True)
    else:
        if mutation == "count":
            state["file_manifest"]["2024/20240102.jsonl"]["rows"] = 2
        else:
            del state["file_manifest"]
        (path.parents[1] / "_state.json").write_text(json.dumps(state))
    status = listing.listing_status(tmp_path)
    assert not status["supplement_complete"]
    if mutation in {"missing", "extra", "changed"}:
        assert status["file_integrity"][mutation]
    assert listing.listing_version(tmp_path) != before
    with pytest.raises(ValueError, match="supplement incomplete"):
        listing.require_supplement(tmp_path, ["20240102"])


def test_current_required_targets_exclude_main_corps_and_post_end_trading(tmp_path):
    state = checkpoint(tmp_path, end="20240102", corps=["00000001"])
    master = tmp_path / "reference/corp_codes.csv"
    master.write_text("corp_code,stock_code\n00000001,000010\n00000002,000020\n00000003,000030\n00000004,000040\n00000005,000050\n")
    write_rows(tmp_path / "reference/dart_company/companies.jsonl",
               [{"corp_code": f"{n:08d}", "corp_cls": "Y" if n == 4 else "E"} for n in range(1, 5)])
    write_rows(tmp_path / "market/krx_daily/2024/20240102.jsonl",
               [{"stock_code": c, "trade_date": "20240102"} for c in ("000010", "000020", "000040", "000050")])
    write_rows(tmp_path / "market/krx_daily/2024/20240103.jsonl",
               [{"stock_code": "000030", "trade_date": "20240103"}])
    save(tmp_path, [row(corp_code="00000002", stock_code="000020")])
    save(tmp_path, [row("20240103", corp_code="00000005", stock_code="000050")])
    checkpoint(tmp_path, end="20240102", corps=["00000001"])
    status = listing.listing_status(tmp_path)
    assert status["reason"] == "supplement_targets_missing"
    assert status["missing_target_corps"] == ["00000005"]  # Unknown classification also requires repair.
    assert listing.required_supplement_targets(tmp_path, "20240102") == ["00000001", "00000005"]
    with pytest.raises(ValueError, match="supplement_targets_missing"):
        listing.require_supplement(tmp_path, ["20240102"])
    checkpoint(tmp_path, end="20240102", corps=["00000001", "00000005"])
    assert listing.listing_status(tmp_path)["supplement_complete"]


def test_main_appearance_before_start_also_excludes_required_target(tmp_path):
    checkpoint(tmp_path)
    (tmp_path / "reference/corp_codes.csv").write_text("corp_code,stock_code\n00000001,000010\n")
    write_rows(tmp_path / "reference/dart_company/companies.jsonl", [{"corp_code": "00000001", "corp_cls": "E"}])
    write_rows(tmp_path / "market/krx_daily/2016/20160601.jsonl", [{"stock_code": "000010", "trade_date": "20160601"}])
    save(tmp_path, [row("20150515")])
    checkpoint(tmp_path)
    assert listing.listing_status(tmp_path)["supplement_complete"]


@pytest.mark.parametrize("source", ["main", "krx"])
def test_target_scan_cache_rechecks_archive_edits_with_preserved_mtime(tmp_path, source):
    import os
    checkpoint(tmp_path)
    (tmp_path / "reference/corp_codes.csv").write_text("corp_code,stock_code\n00000001,000010\n")
    write_rows(tmp_path / "reference/dart_company/companies.jsonl", [{"corp_code": "00000001", "corp_cls": "E"}])
    krx = tmp_path / "market/krx_daily/2016/20160601.jsonl"
    write_rows(krx, [{"stock_code": "000010", "trade_date": "20160601"}])
    save(tmp_path, [row(corp_code="00000002", stock_code="000020")])
    assert listing.required_supplement_targets(tmp_path, "20261009") == ["00000001"]
    path = krx if source == "krx" else listing.directory(tmp_path) / "list/2024/20240102.jsonl"
    stat = path.stat()
    original = path.read_text()
    changed = original.replace("000010", "000020") if source == "krx" else original.replace("00000002", "00000001")
    assert len(original.encode()) == len(changed.encode())
    # Kernel file timestamps advance in coarse ticks (milliseconds on WSL2);
    # wait past one so the edit's ctime differs from the first scan's.
    time.sleep(0.05)
    path.write_text(changed)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert listing.required_supplement_targets(tmp_path, "20261009") == []


def test_union_of_files_is_never_research_coverage_and_post_end_needs_main(tmp_path):
    sessions = pd.bdate_range("2023-01-02", "2024-01-03")
    decision = sessions[-1]
    days = pd.date_range(sessions[-127], decision - pd.Timedelta(days=1))
    stamps = [d.strftime("%Y%m%d") for d in days]
    for stamp in stamps:
        write_rows(listing.directory(tmp_path) / "list" / stamp[:4] / f"{stamp}.jsonl", [])
    checkpoint(tmp_path, end="20240101", days=stamps)
    missing = "20240102"  # After end_de: still requires a main file and checkpoint.
    (listing.directory(tmp_path) / "list/2024/20240102.jsonl").unlink()
    save(tmp_path, [row()], True)
    checkpoint(tmp_path, end="20240101", days=stamps, corps=["00000001"])
    paths = hf001._listing_files(tmp_path)
    assert missing in listing.listing_paths(tmp_path)
    assert missing not in listing.covered_listing_days(tmp_path, stamps)
    assert not hf001.listing_coverage(decision, sessions, paths, data_dir=tmp_path)["covered"]
    assert not bb001.listing_coverage(decision, sessions, paths, data_dir=tmp_path)["covered"]
    # HC003 uses precisely HF001's coverage; its row reader keeps the union.
    assert pd.Timestamp(missing) in hc003.listing_files(tmp_path, decision)
    write_rows(listing.directory(tmp_path) / "list/2024/20240102.jsonl", [])
    assert hf001.listing_coverage(decision, sessions, paths, data_dir=tmp_path)["covered"]
    checkpoint(tmp_path, end="20240101", days=stamps[:-1], corps=["00000001"])
    assert missing not in listing.covered_listing_days(tmp_path, stamps)


def test_post_end_main_coverage_survives_invalid_supplement_but_historical_does_not(tmp_path):
    save(tmp_path, [row("20261009"), row("20261010")])
    state = checkpoint(tmp_path, days=["20261009", "20261010"])
    del state["file_manifest"]
    (listing.directory(tmp_path) / "list_supplement/_state.json").write_text(json.dumps(state))
    assert listing.covered_listing_days(tmp_path, ["20261009", "20261010"]) == ["20261010"]
    listing.require_supplement(tmp_path, ["20261010", "20261030"])
    with pytest.raises(ValueError, match="manifest_missing"):
        listing.require_supplement(tmp_path, ["20261009"])


def test_b001_missing_main_refuses_even_with_supplement_file(tmp_path, monkeypatch):
    save(tmp_path, [row()], True)
    checkpoint(tmp_path, corps=["00000001"])
    monkeypatch.setattr(b001, "_registration", lambda *a: None)
    monkeypatch.setattr(b001.experiment_registry, "record_trial", lambda *a, **k: pytest.fail("trial written"))
    with pytest.raises(ValueError, match="complete main and supplement"):
        b001.run_experiment(data_dir=tmp_path, repo_root=tmp_path)


def test_lh002_counts_through_october_30_extend_past_supplement_end(tmp_path):
    sessions = pd.bdate_range(end="2026-10-30", periods=60)
    days = [day.strftime("%Y%m%d") for day in pd.date_range(sessions[0], sessions[-1])]
    for day in days:
        write_rows(listing.directory(tmp_path) / "list" / day[:4] / f"{day}.jsonl", [])
    checkpoint(tmp_path, days=days)
    counts = inputs.disclosure_counts(tmp_path, sessions, {"000010"})["000010"]
    assert counts["coverage"]["complete"] and set(counts["counts"].values()) == {0}
    state = listing.listing_status(tmp_path)
    assert state["end_de"] == "20261009" and "Y/K" in state["post_end_de_assumption"]
    listing.require_supplement(tmp_path, days)


def test_offline_frozen_scan_backfill_only_adds_state_field_and_is_idempotent(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts.data.backfill_listing_target_scan import backfill

    protected = tmp_path / "market/krx_daily/2026/20260102.jsonl"
    write_rows(protected, [{"trade_date": "20260102", "stock_code": "000010"}])
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference/corp_codes.csv").write_text("corp_code,stock_code\n00000001,000010\n")
    save(tmp_path, [row()], True)
    state = checkpoint(tmp_path, corps=["00000001"])
    del state["target_scan"]
    state_path = listing.directory(tmp_path) / "list_supplement/_state.json"
    state_path.write_text(json.dumps(state))
    before_version = listing.listing_version(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not listing.listing_status(tmp_path)["supplement_complete"]
    original_open = Path.open
    def checked_open(path, *a, **k):
        assert path != protected, "dry-run opened protected contents"
        return original_open(path, *a, **k)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", checked_open)
        plan = backfill(tmp_path)
        assert plan["protected_files"] == 1 and plan["protected_price_contents_opened"] is False
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert backfill(tmp_path, write=True)["status"] == "frozen"
    updated = json.loads(state_path.read_text())
    scan = updated.pop("target_scan")
    assert updated == state  # Including the original manifest and counters.
    assert scan["traded_codes_per_corp"] == {"00000001": ["000010"]}
    assert scan["price_files"] == {"market/krx_daily/2026/20260102.jsonl": listing.file_hash(protected)}
    assert listing.listing_version(tmp_path) != before_version
    written = state_path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", checked_open)
        assert listing.listing_status(tmp_path)["supplement_complete"]
        assert backfill(tmp_path, write=True) == {"status": "already_frozen", "state_changed": False}
    assert written == state_path.read_bytes()
    for path, content in before.items():
        if path != state_path:
            assert path.read_bytes() == content


@pytest.mark.parametrize("change", ["file_added", "file_removed", "nonprotected_changed"])
def test_frozen_scan_rejects_price_inventory_and_nonprotected_content_changes(tmp_path, change):
    path = tmp_path / "market/krx_daily/2025/20250102.jsonl"
    write_rows(path, [{"trade_date": "20250102", "stock_code": "000010"}])
    checkpoint(tmp_path)
    assert listing.listing_status(tmp_path)["supplement_complete"]
    if change == "file_added":
        write_rows(tmp_path / "market/krx_daily/2026/20260102.jsonl", [{"trade_date": "20260102"}])
    elif change == "file_removed":
        path.unlink()
    else:
        write_rows(path, [{"trade_date": "20250102", "stock_code": "000020"}])
    status = listing.listing_status(tmp_path)
    assert not status["supplement_complete"]
    assert "frozen target scan" in status["file_integrity"]["reference_error"]
