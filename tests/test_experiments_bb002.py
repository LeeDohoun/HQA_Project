"""BB002 synthetic checks; no real protected contents, providers or ledger claims."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import bb001, bb002 as b, common
from src.ingestion import dart_buyback as dart
from src.ingestion.dart_backfill import _write_json
from src.ingestion.storage import write_rows
from dart_listing_fixtures import checkpoint


@pytest.fixture
def sessions():
    return b._calendar(b.CONFIRMATION)[0]


@pytest.fixture
def registration(tmp_path, monkeypatch):
    real = b._registration(experiment_registry.PROJECT_ROOT)
    for name in (b.EXPERIMENT_ID, b.FORWARD_ID, bb001.EXPERIMENT_ID):
        path = tmp_path / "research/experiments" / name / "preregistration.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((experiment_registry.PROJECT_ROOT / path.relative_to(tmp_path)).read_bytes())
    monkeypatch.setattr(b, "_registration", lambda root: real)
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *args, **kwargs: real)
    monkeypatch.setattr(experiment_registry, "verify_captured_preregistration", lambda *args, **kwargs: None)
    monkeypatch.setattr(b, "comparison_reference", lambda root: {
        "file": b.COMPARISON_FILE, "sha256": b.COMPARISON_SHA256, "part_hashes": {},
        "design_mean": b.DESIGN_EFFECT, "minimum_mean": b.MINIMUM_EFFECT})
    return real


def item(day="20260102", number=1, *, corp="00000001", code="000010", **changes):
    return {"rcept_dt": day, "rcept_no": day + f"{number:06d}", "corp_code": corp,
            "stock_code": code, "corp_cls": "Y", "corp_name": "합성보통주",
            "report_nm": "주요사항보고서(자기주식취득결정)", **changes}


def save_values(root, row, window=b.CONFIRMATION, **changes):
    values = {"rcept_no": row["rcept_no"], "corp_code": row["corp_code"], "aqpln_stk_ostk": "1234",
              "aqpln_prc_ostk": "12345678", "aq_mth": "장내 직접 취득", "aq_pp": "소각",
              "aqexpd_bgd": window.start, "aqexpd_edd": window.end, **changes}
    path = dart._path(root, "structured", row["corp_code"],
                      from_date=window.structured_range[0], to_date=window.structured_range[1])
    archive = json.loads(path.read_text()) if path.exists() else {
        "version": dart.VERSION, "corp_code": row["corp_code"], "complete": True,
        "from_date": window.structured_range[0], "to_date": window.structured_range[1], "requests": [], "rows": {}}
    archive["rows"][row["rcept_no"]] = {"row": values, "row_sha256": dart.row_digest(values), "response_sha256": ["a" * 64]}
    _write_json(path, archive)
    text = f"수량 {values['aqpln_stk_ostk']} 주 금액 {values['aqpln_prc_ostk']} 원"
    _write_json(dart._path(root, "documents", row["rcept_no"]), {
        "version": dart.VERSION, "rcept_no": row["rcept_no"], "status": "000", "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "raw_sha256": "c" * 64,
        "verification": dart.verify_values(values, text)})


def archives(root, sessions, rows, window=b.CONFIRMATION, *, codes=12):
    _, _, listing_start, price_start = b._calendar(window, sessions)
    for day in pd.date_range(listing_start, window.end):
        key = day.strftime("%Y%m%d")
        write_rows(root / f"disclosures/dart_full/list/{day:%Y}/{key}.jsonl",
                   [row for row in rows if row["rcept_dt"] == key])
    for day in sessions[(sessions >= price_start) & (sessions <= window.vanish_end)]:
        quotes = [{"trade_date": day.date().isoformat(), "stock_code": f"{10 * (index + 1):06d}",
                   "open": 10000., "close": 10000., "base_price": 10000., "volume": 10000.,
                   "trading_value": 2e9, "market_cap": 1e11, "change_rate_pct": 0.,
                   "market": "KOSPI", "stock_name": "합성보통주", "calendar_status": "verified"}
                  for index in range(codes)]
        write_rows(root / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl", quotes)
    checkpoint(root)
    for row in rows:
        if window.start.replace("-", "") <= row["rcept_dt"] <= window.end.replace("-", ""):
            save_values(root, row, window)


@contextmanager
def synthetic_session(root):
    """Scope synthetic reads without calling HoldoutSession/_claim at all."""
    session = holdout.HoldoutSession(b.EXPERIMENT_ID, repo_root=root)
    session._open, session._pid = True, os.getpid()
    token = holdout._SESSIONS.set((*holdout._SESSIONS.get(), session))
    try:
        yield session
    finally:
        session._open = False
        holdout._SESSIONS.reset(token)


def metric(mean=b.MINIMUM_EFFECT, stressed=.001, *, count=30, corporations=20, dates=20):
    return {"primary": {"count": count, "mean": mean, "t_stat": None, "p_value": 1.},
            "corporations": corporations, "dates": dates, "cost_sensitivity": {"1.5": {"mean": stressed}},
            "random_control": {"share_of_controls": 1.}}


def test_registration_and_fixed_bb001_reference_hash():
    registration = b._registration(experiment_registry.PROJECT_ROOT)
    assert registration.fields["uses_holdout"] and registration.fields["variants_planned"] == 1
    reference = b.comparison_reference()
    assert reference["sha256"] == b.COMPARISON_SHA256 and len(reference["part_hashes"]) == 2
    assert reference["minimum_mean"] == reference["design_mean"] / 2


def test_comparison_refuses_changed_concatenated_parts(tmp_path):
    for suffix in (".part00", ".part01"):
        path = tmp_path / (b.COMPARISON_FILE + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"changed reference")
    with pytest.raises(ValueError, match="comparison.*mismatch"):
        b.comparison_reference(tmp_path)


def test_sample_receipts_use_public_date_and_include_june_july_exit(tmp_path, sessions):
    rows = [item("20251231", corp="00000009"), item(), item("20260630", number=2, corp="00000002"),
            item("20260701", number=3, corp="00000003")]
    archives(tmp_path, sessions, rows)
    # Receipt prefix is not the publication date, including at the sample boundary.
    rows[1]["rcept_no"] = "20251231000099"
    save_values(tmp_path, rows[1])
    listing, files, _ = dart.load_listings(tmp_path, from_date="20250901", to_date="20260701")
    listing = [rows[1] if row["rcept_dt"] == "20260102" else row for row in listing] + [rows[-1]]
    events, _ = b.construct_events(listing, files, sessions, tmp_path)
    assert {row["rcept_dt"] for row in events} == {"20260102", "20260630"}
    assert all(row["period"] == "confirmation" for row in events)
    planned, reason = b.schedule(next(row for row in events if row["rcept_dt"] == "20260630"), sessions)
    assert reason is None and planned["entry_date"] == "2026-07-01"
    assert planned["exit_date"] == "2026-07-30" and planned["horizon"] == 21


def test_repeat_63_and_64_clock_uses_excluded_prior_decisions(tmp_path, sessions):
    first = int(sessions.searchsorted("2026-01-02", side="right")) - 1
    prior = item(sessions[first - 63].strftime("%Y%m%d"))
    at63 = item("20260102", number=2)
    at64 = item(sessions[first + 64].strftime("%Y%m%d"), number=3)
    same = item(at64["rcept_dt"], number=4)
    rows = [prior, at63, at64, same]
    archives(tmp_path, sessions, rows)
    listings, files, _ = dart.load_listings(tmp_path, from_date=prior["rcept_dt"], to_date=b.CONFIRMATION.end)
    events, report = b.construct_events(listings, files, sessions, tmp_path)
    assert [row["rcept_no"] for row in events] == [at64["rcept_no"]]
    assert report["filter_counts"]["repeat_0_63"] == 1 and report["filter_counts"]["same_day_repeat"] == 1


def test_missing_empty_listing_day_breaks_63_session_coverage(tmp_path, sessions):
    archives(tmp_path, sessions, [item()])
    (tmp_path / "disclosures/dart_full/list/2025/20251207.jsonl").unlink()
    listing, files, _ = dart.load_listings(tmp_path, from_date="20250901", to_date="20260630")
    events, report = b.construct_events(listing, files, sessions, tmp_path)
    assert not events and report["filter_counts"]["incomplete_listing_window"] == 1


def test_reader_never_implicitly_claims_and_guards_cache_hits_with_bb002_id(tmp_path, sessions, monkeypatch):
    archives(tmp_path, sessions, [item()])
    reader = b.PriceReader(tmp_path, sessions, repo_root=tmp_path)
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("no real ledger claim"))
    with pytest.raises(ValueError, match="open BB002"):
        reader.read("2026-01-02")
    calls, guard = [], holdout.guard_period
    def spy(start, end, **kwargs):
        calls.append(kwargs)
        return guard(start, end, **kwargs)
    monkeypatch.setattr(holdout, "guard_period", spy)
    with synthetic_session(tmp_path):
        for _ in range(2):
            assert reader.read("2026-01-02")["000010"]["open"] == 10000
    assert len(calls) == 2 and all(call["experiment_id"] == b.EXPERIMENT_ID and call["repo_root"] == tmp_path for call in calls)
    reader._load.cache_clear()


def test_supplied_protected_frame_also_requires_bb002_session(tmp_path, sessions, monkeypatch):
    index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-02"), "000010")], names=["trade_date", "stock_code"])
    frame = pd.DataFrame({"close": [10000.]}, index=index)
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("implicit protected claim"))
    with pytest.raises(ValueError, match="open BB002"):
        b.PriceReader(tmp_path, sessions, prices=frame)


def test_vanish_horizon_completeness_refuses_before_rehearsal_or_session(tmp_path, sessions, registration, monkeypatch):
    archives(tmp_path, sessions, [item()])
    (tmp_path / "market/krx_daily/2026/20260930.jsonl").unlink()
    monkeypatch.setattr(b, "rehearse", lambda **k: pytest.fail("missing horizon must fail before rehearsal"))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("must refuse before session"))
    with pytest.raises(ValueError, match="2026-09-30"):
        b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    assert not (tmp_path / holdout.LEDGER_PATH).exists()
    assert not (tmp_path / experiment_registry.REGISTRY_PATH).exists()


def test_unfetched_sources_refuse_before_session_but_official_missing_document_is_an_exclusion(tmp_path, sessions, registration, monkeypatch):
    archives(tmp_path, sessions, [item()])
    dart._path(tmp_path, "documents", item()["rcept_no"]).unlink()
    result = b.precheck(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    assert result["status"] == "insufficient" and result["collection"]["documents_missing"] == 1
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("unfetched inputs consumed a session"))
    with pytest.raises(ValueError, match="buyback archives"):
        b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    save_values(tmp_path, item())
    path = dart._path(tmp_path, "documents", item()["rcept_no"])
    document = json.loads(path.read_text())
    document.update(status="014", text="", text_sha256=hashlib.sha256(b"").hexdigest())
    _write_json(path, document)
    result = b.precheck(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    assert result["ready"] and result["counts"]["construction"]["filter_counts"]["original_document_unavailable"] == 1


@pytest.mark.parametrize("changed", ["price_inventory", "listing_content"])
def test_rehearsal_input_mutation_refuses_before_claim(tmp_path, sessions, registration, monkeypatch, changed):
    archives(tmp_path, sessions, [item()])
    def rehearse(**kwargs):
        if changed == "price_inventory":
            (tmp_path / "market/krx_daily/2026/20260930.jsonl").unlink()
        else:
            path = tmp_path / "disclosures/dart_full/list/2026/20260102.jsonl"
            path.write_text(path.read_text() + "\n")
        return {"passed": True, "code_hashes": b._code_hashes()}
    monkeypatch.setattr(b, "rehearse", rehearse)
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("changed input consumed session"))
    with pytest.raises(ValueError, match="changed"):
        b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("closed", ["2026-06-03", "2026-07-17"])
def test_shared_corrected_calendar_and_missing_official_session_refusal(sessions, closed):
    assert pd.Timestamp(closed) not in sessions
    with pytest.raises(ValueError, match="complete shared"):
        b._calendar(b.CONFIRMATION, sessions.drop(pd.Timestamp("2026-09-30")))


def test_hac_grid_keeps_no_event_days_and_fixed_end(sessions):
    _, grid, _, _ = b._calendar(b.CONFIRMATION, sessions)
    assert grid[0] == pd.Timestamp("2026-01-02") and grid[-1] == pd.Timestamp("2026-07-30")
    values, dates = np.array([.01, -.02, .03, .1]), grid[[1, 30, 31, -1]]
    actual = bb001.hac(values, dates, grid, 20)
    sums = np.zeros(len(grid))
    sums[grid.get_indexer(dates)] = values - values.mean()
    distance = np.abs(np.arange(len(grid))[:, None] - np.arange(len(grid))[None, :])
    variance = sums @ np.maximum(0, 1 - distance / 21) @ sums / len(values)**2
    assert actual["standard_error"] == pytest.approx(math.sqrt(variance))
    assert actual["grid_sessions"] == len(grid) > len(dates)
    assert actual["standard_error"] != pytest.approx(bb001.hac(values, dates, dates, 20)["standard_error"])


@pytest.mark.parametrize("changes,expected", [({}, "pass"), ({"mean": b.MINIMUM_EFFECT - 1e-12}, "fail"),
    ({"stressed": 0.}, "fail"), ({"stressed": -1e-12}, "fail"), ({"count": 29}, "insufficient"),
    ({"corporations": 19}, "insufficient"), ({"dates": 19}, "insufficient"), ({"mean": None}, "insufficient")])
def test_judgement_exact_boundaries_ignores_hac_and_random_share(changes, expected):
    base, loss = metric(**changes), metric(**changes)
    assert b.judge(base, loss)["verdict"] == expected


@pytest.mark.parametrize("base,loss,complete,expected", [
    (metric(), metric(mean=-.1), True, "delisting_sensitive"),
    (metric(mean=-.1), metric(), True, "delisting_sensitive"),
    (metric(mean=-.1), metric(mean=-.2), True, "fail"),
    (metric(count=29), metric(mean=-.1), True, "insufficient"),
    (metric(), metric(stressed=None), True, "insufficient"),
    (metric(), metric(mean=-.1), False, "insufficient")])
def test_judgement_precedence(base, loss, complete, expected):
    assert b.judge(copy.deepcopy(base), copy.deepcopy(loss), required_data_complete=complete)["verdict"] == expected


def test_precheck_reads_no_protected_contents_or_returns_and_never_claims(tmp_path, sessions, registration, monkeypatch):
    archives(tmp_path, sessions, [item()])
    original = Path.open
    def guarded(path, *args, **kwargs):
        if "krx_daily/2026/20260" in str(path) and path.stem <= "20260630":
            pytest.fail("precheck opened a protected price")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setattr(b.PriceReader, "read", lambda *a: pytest.fail("precheck calculated prices"))
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("precheck claimed ledger"))
    result = b.precheck(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    assert result["ready"] and not result["prices"]["content_checked"]
    assert result["dart_plan"]["counts"]["title_candidates"] == 1
    assert result["counts"]["construction"]["availability"]["original_values_verified"] == 1
    assert all("krx_daily" not in path for path in result["source_hashes"])
    assert not (tmp_path / holdout.LEDGER_PATH).exists() and not (tmp_path / experiment_registry.REGISTRY_PATH).exists()


def test_full_rehearsal_writes_results_never_touches_registry_ledger_or_protected_contents(tmp_path, sessions, registration, monkeypatch):
    archives(tmp_path, sessions, [item("20250102")], b.REHEARSAL)
    registry = tmp_path / experiment_registry.REGISTRY_PATH
    ledger = tmp_path / holdout.LEDGER_PATH
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("registry must never be opened")
    ledger.write_text("ledger must never be opened")
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path not in (registry, ledger), "rehearsal touched registry/ledger"
        assert not ("krx_daily/2026" in str(path) and path.stem <= "20260630"), "protected read in rehearsal"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("rehearsal claimed session"))
    monkeypatch.setattr(experiment_registry, "record_trials", lambda *a, **k: pytest.fail("rehearsal registry write"))
    report = b.rehearse(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    payload = common.read_result_json(report["result_file"])
    assert report["passed"] and "/rehearsal/" in report["result_file"]
    assert payload["stage"] == "rehearsal" and set(payload["variants"]) == {"d21"}
    assert payload["variants"]["d21"]["primary"]["count"] == 1
    assert payload["verdict"] == "insufficient" and payload["source_hashes"] and payload["code_hashes"]
    assert not list(registry.parent.glob("*.lock"))


def test_protected_calculation_rechecks_every_price_with_bb002_id_and_reports_only_d21(tmp_path, sessions, registration, monkeypatch):
    archives(tmp_path, sessions, [item()])
    prepared = b._prepare(data_dir=tmp_path, repo_root=tmp_path, window=b.CONFIRMATION, sessions=sessions)
    guards, guard = [], holdout.guard_period
    def spy(start, end, **kwargs):
        guards.append((start, kwargs))
        assert kwargs["experiment_id"] == b.EXPERIMENT_ID
        return guard(start, end, **kwargs)
    monkeypatch.setattr(holdout, "guard_period", spy)
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("synthetic test cannot claim real session"))
    with synthetic_session(tmp_path):
        result = b._calculate(prepared, data_dir=tmp_path, repo_root=tmp_path, window=b.CONFIRMATION)
    assert set(result["variants"]) == {"d21"} and result["variants"]["d21"]["primary"]["count"] == 1
    assert result["variants"]["d21"]["secondary"]["used_for_judgement"] is False
    assert not (tmp_path / holdout.LEDGER_PATH).exists()
    for path in result["source_hashes"]:
        if "krx_daily" in path:
            assert sum(str(start).replace("-", "") == Path(path).stem for start, _ in guards) >= 2


def test_vanished_minus100_recomputes_event_and_controls_without_extending_exit(tmp_path, sessions):
    archives(tmp_path, sessions, [item()])
    event = {**item(), "public_date": "2026-01-02"}
    planned, _ = b.schedule(event, sessions)
    entry = sessions[planned["entry_position"]]
    # Event disappears; a control retrades after the scheduled exit, before Sep 30.
    for day in sessions[(sessions > entry) & (sessions <= b.CONFIRMATION.vanish_end)]:
        path = tmp_path / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl"
        quotes = [json.loads(line) for line in path.read_text().splitlines()]
        quotes = [q for q in quotes if q["stock_code"] != "000010" and
                  (q["stock_code"] != "000020" or day == pd.Timestamp("2026-09-30"))]
        write_rows(path, quotes)
    reader = b.PriceReader(tmp_path, sessions, repo_root=tmp_path)
    feature = {"market": "KOSPI", "adv": 2e9, "pre_close": 10000.}
    with synthetic_session(tmp_path):
        stock = bb001.value_position(reader, planned, "000010", feature, cutoff=None)
        control = bb001.value_position(reader, planned, "000020", feature, cutoff=None)
    assert stock["vanished"] and not control["vanished"] and stock["scheduled_exit_date"] == planned["exit_date"]
    row = {"stock": stock, "controls": [control]}
    assert bb001.event_excess(row, worthless=True) == pytest.approx(bb001.event_excess(row) - 1)
    assert bb001._net(stock, "1.5", True) == -1 - stock["costs"]["1.5"]
    reader._load.cache_clear()


@pytest.mark.parametrize("abort", [False, "exception", "price_hash"])
def test_single_session_execution_and_abort_path_are_terminal(tmp_path, sessions, registration, monkeypatch, abort):
    archives(tmp_path, sessions, [item()])
    counts = {"claims": 0, "rehearsals": 0}
    def rehearse(**kwargs):
        counts["rehearsals"] += 1
        assert not (tmp_path / holdout.LEDGER_PATH).exists()
        return {"passed": True, "code_hashes": b._code_hashes(), "result_file": "synthetic-rehearsal"}
    monkeypatch.setattr(b, "rehearse", rehearse)
    original_factory = holdout.HoldoutSession
    @contextmanager
    def session_factory(experiment_id, **kwargs):
        assert experiment_id == b.EXPERIMENT_ID and counts["rehearsals"] == 1
        counts["claims"] += 1
        _write_json(tmp_path / holdout.LEDGER_PATH, {"experiment_id": experiment_id})
        session = original_factory(experiment_id, **kwargs)
        session._open, session._pid = True, os.getpid()
        token = holdout._SESSIONS.set((session,))
        try:
            yield session
        finally:
            holdout._SESSIONS.reset(token)
    monkeypatch.setattr(holdout, "HoldoutSession", session_factory)
    monkeypatch.setattr(b.hc003, "ledger_experiments", lambda root:
        {b.EXPERIMENT_ID} if (Path(root) / holdout.LEDGER_PATH).exists() else set())
    if abort == "exception":
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic post-open failure")
        monkeypatch.setattr(b, "_calculate", fail)
    elif abort == "price_hash":
        original = b._secondary
        def mutate(rows, grid):
            path = tmp_path / "market/krx_daily/2026/20260102.jsonl"
            blob = path.read_bytes()
            altered = blob.replace(b"10000.0", b"10001.0", 1)
            assert altered != blob and len(altered) == len(blob)
            path.write_bytes(altered)
            return original(rows, grid)
        monkeypatch.setattr(b, "_secondary", mutate)
    if abort:
        with pytest.raises((RuntimeError, ValueError), match="post-open|source changed"):
            b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    else:
        result = b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
        assert result["verdict"] == "insufficient"
    with (tmp_path / experiment_registry.REGISTRY_PATH).open() as handle:
        trials = list(csv.DictReader(handle))
    assert len(trials) == 1 and trials[0]["variant"] == "holdout_d21"
    assert trials[0]["verdict"] == ("holdout_aborted" if abort else "insufficient")
    if abort == "price_hash":
        metrics = json.loads(trials[0]["metrics_json"])
        payload = common.read_result_json(tmp_path / metrics["result_file"])
        assert str(tmp_path / "market/krx_daily/2026/20260102.jsonl") in payload["source_hashes"]
    with pytest.raises(ValueError, match="cannot reopen"):
        b.run_experiment(data_dir=tmp_path, repo_root=tmp_path, sessions=sessions)
    assert counts == {"claims": 1, "rehearsals": 1}
