from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments import ordering
from backtesting.experiments.ld002 import EXPERIMENT_ID, Refused
from backtesting.experiments.ld002 import data, inputs
from backtesting.experiments.ld002.execution import apply_status, close_input
from backtesting.experiments.ld002.ledger import initial_state
from backtesting.experiments.lh003.snapshot import input_view
from backtesting.experiments.lh003.simulation import synthetic_archive
from backtesting.experiments.lh003.inputs import financials
from scripts.lh003.distribute import inventory
from backtesting.experiments.ld002.storage import aware, digest, file_hash, seal, verified
from ld002_fixtures import CODES, FakeCalendar, output, snapshot


def history(stocks=405, sessions=251):
    days = pd.bdate_range(end="2026-11-13", periods=sessions)
    codes = [f"{i:05d}0" for i in range(stocks)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    frame = pd.DataFrame({"stock_name": ["합성기업" + c for _, c in index], "market": "KOSPI", "close": 10000.,
        "high": 10500., "volume": 1000, "trading_value": 500e6, "market_cap": 1e11,
        "ret_1d": .001, "calendar_status": "verified"}, index=index)
    return frame, days, codes


def empty_financials():
    return pd.DataFrame(columns=["available_date", "stock_code"])


def test_universe_threshold_cap_ties_exclusions_and_held_outside_table():
    frame, days, codes = history()
    day = days[-1]
    frame.loc[(day, codes[0]), "volume"] = 0
    frame.loc[(slice(None), codes[1]), "stock_name"] = "합성스팩"
    frame.loc[(slice(None), codes[2]), "stock_name"] = "합성리츠"
    frame.loc[(slice(None), codes[3]), "stock_name"] = "ETF 합성"
    frame.loc[(slice(None), codes[4]), "trading_value"] = 500e6 - 1
    universe, table, breadth = inputs.candidate_table(frame, day, [codes[0]], empty_financials(), {}, {codes[5]})
    assert universe == codes[5:405] and len(universe) == 400
    assert table[0]["code"] == codes[0] and table[0]["hold_or_sell_only"]
    assert table[1]["av_included"] and table[1]["revenue_yoy"] is None
    assert breadth == {"KOSPI": 1, "KOSDAQ": None}


def test_price_feature_windows_are_complete_and_missing_is_not_zero():
    frame, days, codes = history(2)
    frame.loc[(days[-2], codes[0]), "ret_1d"] = np.nan
    universe, table, _ = inputs.candidate_table(frame, days[-1], [], empty_financials(), {}, [])
    assert table[0]["return_1"] == pytest.approx(.001)
    assert table[0]["return_5"] is None and table[0]["volatility_60"] is None
    assert table[1]["return_250"] == pytest.approx(1.001 ** 250 - 1)
    assert table[1]["distance_52w_high"] < 0
    # A future row cannot alter the close-known table.
    future = frame.loc[[days[-1]]].copy()
    future.index = pd.MultiIndex.from_product([[days[-1] + pd.Timedelta(days=3)], codes], names=frame.index.names)
    future["close"] = 1
    assert inputs.candidate_table(pd.concat([frame, future]), days[-1], [], empty_financials(), {}, [])[1] == table


def test_adv_requires_all_twenty_sessions_and_does_not_impute_missing():
    frame, days, codes = history(2, 20)
    frame.loc[(days[-2], codes[0]), "trading_value"] = np.nan
    universe, _, _ = inputs.candidate_table(frame, days[-1], [], empty_financials(), {}, [])
    assert universe == [codes[1]]


def test_disclosure_order_per_company_three_and_total_three_hundred():
    rows = [{"stock_code": f"{i:05d}0", "rcept_dt": "20261113", "report_nm": f"합성{j}",
        "rcept_no": f"20261113{i:05d}{j}", "received_at": f"2026-11-13T09:{j:02d}:00+09:00"}
        for i in range(110) for j in range(4)]
    result = inputs.disclosures(rows[::-1], {r["stock_code"] for r in rows}, "2026-11-13")
    assert len(result) == 300 and result == sorted(result, key=lambda r: (r["received_at"], r["receipt"], r["code"]))
    assert max(sum(r["code"] == c for r in result) for c in {r["code"] for r in result}) <= 3
    rows[0].pop("received_at")
    with pytest.raises(Refused, match="receipt timestamps"):
        inputs.disclosures(rows, {r["stock_code"] for r in rows}, "2026-11-13")


def test_explicit_halt_metadata_controls_universe_even_when_daily_volume_is_zero():
    frame, days, codes = history(2, 20)
    frame["halted"] = False
    frame.loc[(days[-1], codes[0]), "volume"] = 0
    universe, table, _ = inputs.candidate_table(frame, days[-1], [], empty_financials(), {}, [])
    assert codes[0] in universe and table[0]["halted"] is False
    frame.loc[(days[-1], codes[0]), "halted"] = True
    assert codes[0] not in inputs.candidate_table(frame, days[-1], [], empty_financials(), {}, [])[0]


def test_v1_renderer_preserves_zero_and_uses_missing_text_and_fixed_sections():
    value = snapshot()
    value["market"] = [{"unknown": None, "zero": 0}]
    result = inputs.payload(value)
    assert '"unknown":"자료 없음"' in result["prompt"] and '"zero":0' in result["prompt"]
    assert result["prompt"].index("[decision_day]") < result["prompt"].index("[candidates]") < result["prompt"].index("[ledger]")
    assert set(result["artifact_hashes"]) == {"v1_prompt.txt", "v1_reask.txt", "v1_schema.json", "v1_renderer.py"}
    assert "오늘 종가" in result["prompt"] and "0.5%" in result["prompt"] and "15%" in result["prompt"]


@pytest.fixture
def sources(tmp_path):
    raw = synthetic_archive(tmp_path / "raw-fixture", day="2026-11-13", stocks=4)
    path = raw / "market/krx_daily/2026/20261113.jsonl"
    path.write_text("".join(json.dumps({**r, "price_basis": "unadjusted"}) + "\n" for r in data.rows(path)))
    baseline = data.capture_h1(raw, tmp_path / "snapshot", at=aware("2026-11-13T09:00:00+09:00"), synthetic=True)
    sync = tmp_path / "sync.json"
    sync.write_text(json.dumps({"verified_at": "2026-11-13T16:00:00+09:00", "transfer_route": "synthetic fixture copy",
        "files": {p.relative_to(raw).as_posix(): file_hash(p) for p in inventory(raw)}}))
    publication = data.distribute(raw, tmp_path / "distributions", sync, code_hashes={"fixture": "hash"},
                                 clock=lambda: aware("2026-11-13T16:01:00+09:00"), synthetic=True)
    distribution = data.Distribution(tmp_path / "distributions", at=aware("2026-11-16T09:00:00+09:00"),
                                     code_hashes={"fixture": "hash"}, allow_synthetic=True)
    sealed = data.Snapshot(tmp_path / "snapshot", expected_hash=digest(baseline), allow_synthetic=True, distribution=distribution)
    return raw, sealed, distribution


def test_h1_plus_nonh1_input_view_never_reopens_raw_and_uses_s_dates(sources, monkeypatch):
    raw, snapshot, distribution = sources
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to(raw.resolve()):
            pytest.fail("raw archive reopened after the one-time seal")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    with input_view(raw, snapshot, end="20261113") as view:
        known, report = financials(view, "2026-11-16")
        assert report["rule"] == "S" and not known.empty
        assert (known.available_date < "2026-11-16").all()
        assert all(not data.protected(row["trade_date"]) for row in data.rows(distribution.path("market_context/benchmarks.jsonl")))


def test_synthetic_seal_mismatch_writable_file_and_failed_latest_distribution_block(sources):
    _, snapshot, distribution = sources
    with pytest.raises(Refused, match="synthetic"):
        data.Snapshot(snapshot.root, expected_hash=digest(snapshot.manifest))
    with pytest.raises(Refused, match="hash differs"):
        data.Snapshot(snapshot.root, expected_hash="wrong", allow_synthetic=True)
    path = distribution.path("market_context/benchmarks.jsonl")
    path.chmod(0o644)
    with pytest.raises(Refused, match="writable"):
        distribution.path("market_context/benchmarks.jsonl")
    directory = distribution.root.parents[1]
    seal(directory / "journal/000001.started.json", {"at": "2026-11-16T08:00:00+09:00"})
    with pytest.raises(Refused, match="latest distribution incomplete"):
        data.Distribution(directory, at=aware("2026-11-16T09:00:00+09:00"), code_hashes={"fixture": "hash"}, allow_synthetic=True)


def test_holdout_path_claims_once_under_same_ordering_lock_on_fixtures_only(tmp_path, monkeypatch):
    raw = synthetic_archive(tmp_path / "raw-fixture", day="2026-11-13", stocks=2)
    calls = []
    monkeypatch.setattr(data.hc003, "registry_rows", lambda root: [])
    monkeypatch.setattr(ordering, "ld002_releases", lambda rows, root: {"fixture": "released"})
    @contextmanager
    def fake_session(experiment, **kwargs):
        assert ordering._lock_target(tmp_path)[1] in ordering._LOCKS.get()
        calls.append((experiment, kwargs))
        yield
    root = tmp_path / "state"
    args = dict(repo_root=tmp_path, clock=lambda: aware("2026-11-13T09:00:00+09:00"), session_factory=fake_session, synthetic=True)
    result = data.seal_holdout(raw, root, **args)
    assert result["synthetic"] and calls[0][0] == EXPERIMENT_ID and len(calls) == 1
    with pytest.raises(Refused, match="already attempted"):
        data.seal_holdout(raw, root, **args)
    assert len(calls) == 1 and not (tmp_path / "research/experiments/holdout_ledger.jsonl").exists()


def test_failed_claim_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(data.hc003, "registry_rows", lambda root: [])
    monkeypatch.setattr(ordering, "ld002_releases", lambda *a: {})
    @contextmanager
    def fake_session(*args, **kwargs):
        yield
    def broken(*a, **k):
        raise RuntimeError("fixture acquisition failed")
    args = dict(repo_root=tmp_path, clock=lambda: aware("2026-11-13T09:00:00+09:00"),
                session_factory=fake_session, acquire=broken, synthetic=True)
    with pytest.raises(RuntimeError):
        data.seal_holdout(tmp_path / "not-a-source", tmp_path / "state", **args)
    assert verified(tmp_path / "state/holdout_failed.json")["status"] == "not_started"
    with pytest.raises(Refused, match="already attempted"):
        data.seal_holdout(tmp_path / "not-a-source", tmp_path / "state", **args)


def test_av_json_without_a_completion_proof_cannot_be_consumed(tmp_path, monkeypatch):
    from src.forward import fw001
    folder = tmp_path / fw001.RECORD_DIR / "decisions"
    folder.mkdir(parents=True)
    (folder / "202610.json").write_text(json.dumps({"month": "2026-10", "decision_date": "2026-10-30"}))
    monkeypatch.setattr(fw001, "completion_proof", lambda *a, **k: None)
    with pytest.raises(Refused, match="completion proof"):
        inputs.latest_av(tmp_path, as_of="2026-11-13", at=aware("2026-11-16T09:00:00+09:00"))


def test_official_limit_action_sidecar_is_required_and_not_imputed():
    quotes = {CODES[0]: {"close": 10000}}
    document = {"day": "2026-11-16", "coverage_verified": True, "official_source": "fixture", "stocks": {}, "corporate_actions": []}
    with pytest.raises(Refused, match="incomplete"):
        apply_status(quotes, document, "2026-11-16")
    document["stocks"][CODES[0]] = {"limit_up": True, "limit_down": False, "halted": False}
    result, actions = apply_status(quotes, document, "2026-11-16")
    assert result[CODES[0]]["limit_up"] and actions == []


def test_real_input_builder_on_labelled_fixture_only(sources):
    from backtesting.experiments.ld002.calendar import Calendar
    _, snapshot, distribution = sources
    value = inputs.prepare(snapshot, "2026-11-16", initial_state(), Calendar(), at=aware("2026-11-16T09:00:00+09:00"),
        disclosure_enabled=False, av={"codes": CODES[:2], "decision_sha256": "fixture", "weights": dict.fromkeys(CODES[:2], .5)},
        nav_history=[], memos=[], last_trades=[])
    assert value["decision_day"] == "2026-11-16" and len(value["candidates"]) == 4
    assert value["financial_dates"]["rule"] == "S"
    assert value["ledger"]["cash"] == 100e6 and value["disclosures"]["omitted_for_entire_window"]
    assert len(value["adv20"]) == 4 and "합성기업" in inputs.payload(value)["prompt"]


def test_prior_adv_for_liquidation_reads_exactly_twenty_prior_sessions(sources):
    from backtesting.experiments.ld002.execution import prior_adv
    _, _, distribution = sources
    adv, hashes = prior_adv(distribution, "2026-11-16", FakeCalendar())
    assert adv[CODES[0]] == 2e9 and len(hashes) == 20
    assert all("20261116" not in name for name in hashes)
    quotes, required, _ = close_input(distribution, "2026-11-13", at=aware("2026-11-16T09:00:00+09:00"), calendar=FakeCalendar())
    assert all(required.values()) and quotes[CODES[0]]["limit_up"] is None


def test_stale_transfer_cannot_support_t_minus_one_close(sources):
    _, snapshot, distribution = sources
    distribution.manifest["sync_verified_at"] = "2026-11-13T09:00:00+09:00"
    with pytest.raises(Refused, match="verified after"):
        inputs.prepare(snapshot, "2026-11-16", initial_state(), FakeCalendar(), at=aware("2026-11-16T09:00:00+09:00"),
            disclosure_enabled=False, av={}, nav_history=[], memos=[], last_trades=[])


def test_av_uses_latest_timely_completed_publication_not_newer_incomplete_json(tmp_path, monkeypatch):
    from src.forward import fw001
    folder = tmp_path / fw001.RECORD_DIR / "decisions"
    folder.mkdir(parents=True)
    for month, date in (("2026-09", "2026-09-30"), ("2026-10", "2026-10-30")):
        (folder / (month + ".json")).write_text(json.dumps({"month": month, "decision_date": date,
            "rules": {"av_all": {"targets": CODES[:1], "weights": {CODES[0]: 1}}}}))
    monkeypatch.setattr(fw001, "completion_proof", lambda root, kind, month: None if month == "2026-10" else
        {"publication_not_after": "2026-10-01T08:00:00+09:00"})
    assert inputs.latest_av(tmp_path, as_of="2026-11-13", at=aware("2026-11-16T09:00:00+09:00"))["month"] == "2026-09"


def test_common_share_preferred_spac_reit_etf_etn_boundaries():
    assert inputs.common_stock("000010", {"stock_name": "합성", "market": "KOSPI"})
    for code, name, security in (("000015", "합성우", None), ("000010", "합성SPAC", None),
        ("000010", "합성REIT", None), ("000010", "합성ETN", None), ("000010", "합성", "etf")):
        assert not inputs.common_stock(code, {"stock_name": name, "market": "KOSPI", "security_type": security})


@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_close_reader_refuses_single_market_fixture(sources, tmp_path, market):
    from types import SimpleNamespace
    _, _, distribution = sources
    day = "2026-11-13"
    relative = "market/krx_daily/2026/20261113.jsonl"
    path = tmp_path / "one_market.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in data.rows(distribution.path(relative)) if r["market"] == market))
    partial = SimpleNamespace(manifest=distribution.manifest, path=lambda r: path)
    with pytest.raises(Refused, match="both verified market"):
        close_input(partial, day, at=aware("2026-11-16T09:00:00+09:00"), calendar=FakeCalendar())


def test_distribution_reference_includes_fw001_and_rejects_current_code_as_baseline(tmp_path, monkeypatch):
    from backtesting.experiment_registry import PROJECT_ROOT
    from backtesting.experiments.ld002.runner import implementation_hashes
    current = implementation_hashes()
    assert "src/forward/fw001.py" in current
    reference = verified(PROJECT_ROOT / data.CODE_REFERENCE)
    assert reference["start"]["implementation_hashes"] == current
    altered = {**current, "src/forward/fw001.py": "modified"}
    monkeypatch.setattr(data, "implementation_hashes", lambda root: altered)
    with pytest.raises(Refused, match="pinned implementation"):
        data.code_pins(tmp_path, PROJECT_ROOT, at=aware("2026-11-12T09:00:00+09:00"))


def test_distribution_rejects_imported_code_even_after_source_restoration(tmp_path, monkeypatch):
    import importlib.util
    from src import forward
    from backtesting.experiments.ld002 import runner
    # Load an altered fixture module and snapshot its hash at distributor import.
    path = tmp_path / "fw001.py"
    original = "VALUE = 'approved'\n"
    path.write_text("VALUE = 'altered'\n")
    spec = importlib.util.spec_from_file_location("fixture_fw001", path)
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    monkeypatch.setattr(forward, "fw001", imported)
    monkeypatch.setattr(runner, "implementation_hashes", lambda *a: {"src/forward/fw001.py": file_hash(path)})
    distributor_spec = importlib.util.spec_from_file_location("backtesting.experiments.ld002.fixture_loaded_data", data.__file__)
    distributor = importlib.util.module_from_spec(distributor_spec)
    distributor_spec.loader.exec_module(distributor)
    loaded = distributor._LOADED_HASHES
    path.write_text(original)
    approved = {"src/forward/fw001.py": file_hash(path)}
    assert imported.VALUE == "altered" and approved != loaded
    monkeypatch.setattr(distributor, "code_pins", lambda *a, **k: {"implementation_hashes": approved})
    monkeypatch.setattr(distributor, "inventory", lambda p: pytest.fail("raw inventory must not be read"))
    with pytest.raises(Refused, match="process-loaded code"):
        distributor.distribute(tmp_path / "raw", tmp_path / "out", tmp_path / "sync.json", code_hashes=approved)
    assert not (tmp_path / "out").exists()


def test_distribution_rechecks_loaded_code_before_manifest_seal(sources, tmp_path, monkeypatch):
    raw, _, _ = sources
    approved = {"fixture": "hash"}
    checks = []
    def verify(*a, **k):
        checks.append(True)
        if len(checks) == 2:
            raise Refused("process-loaded code changed")
    monkeypatch.setattr(data, "verify_distribution_code", verify)
    out = tmp_path / "verified-output"
    with pytest.raises(Refused, match="process-loaded code"):
        data.distribute(raw, out, raw.parent / "sync.json", code_hashes=approved,
            clock=lambda: aware("2026-11-16T08:00:00+09:00"))
    assert len(checks) == 2
    assert not list((out / "snapshots").glob("*/manifest.json"))
    assert not list((out / "journal").glob("*.complete.json"))
