"""Return-free precheck, AV replay boundaries and command isolation."""
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from backtesting.experiments.qp001 import runner
from src.ingestion.dart_financial_date_rules import FilingDates
from src.ingestion.storage import write_rows
from test_experiments_qp001_financials import install, version
from test_experiments_qp001_portfolio import fixture_book


def monthly_inputs(monkeypatch, *, count=10, computable=9):
    sessions = pd.bdate_range("2023-01-02", "2023-12-29")
    day = sessions[-2]
    codes = [f"{i:05d}0" for i in range(count)]
    universe = pd.DataFrame({"corp_code": [f"{i:08d}" for i in range(count)], "phase": 1,
        "fiscal_quarter": "2023Q3"}, index=codes)
    index = pd.MultiIndex.from_product([sessions[-61:-1], codes], names=["trade_date", "stock_code"])
    history = pd.DataFrame({"market": "KOSPI", "market_cap": 100., "ret_1d": float("nan"), "calendar_status": "verified"}, index=index)
    monkeypatch.setattr(runner.hc002, "universe_on_decision", lambda *a, **k: (universe, {"price_eligible": count}))
    calls = []
    def roa(corp, quarter, decision):
        calls.append((corp, quarter))
        valid = int(corp) < computable
        return {"roa": int(corp) / 10 if valid else None, "fs_div": "CFS", "fallback": False,
                "failures": {} if valid else {"CFS": "missing_quarter", "OFS": "missing_quarter"}}
    versions = SimpleNamespace(cutoff="2024-01-01", load=lambda *a, **k: None, roa=roa)
    row = {"month": "2023-12", "decision_date": str(day.date()), "trade_date": "2024-01-02",
           "end_date": "2024-02-01", "end_kind": "open"}
    events = pd.DataFrame({"known_position": pd.Series(dtype=int), "stock_code": pd.Series(dtype=str)})
    return sessions, history, versions, row, events, calls


@pytest.mark.parametrize("computable,expected", [(9, True), (8, False)])
def test_completeness_90_percent_is_inclusive_and_month_is_kept(monkeypatch, tmp_path, computable, expected):
    sessions, history, versions, row, events, calls = monthly_inputs(monkeypatch, computable=computable)
    selected = runner.select_month(history, row, versions, events, [], {"covered": True}, sessions,
        data_dir=tmp_path, repo_root=tmp_path)
    assert selected["precheck"]["C_over_A"] == computable / 10
    assert selected["data_complete"] is expected
    assert selected["month"] == "2023-12"
    assert {quarter for _, quarter in calls} == {"2023Q3"}
    assert len(selected["common"]) == computable


def test_rule_specific_common_sets_do_not_intersect(monkeypatch, tmp_path):
    args = monthly_inputs(monkeypatch, computable=9)
    sessions, history, versions, row, events, _ = args
    s = runner.select_month(history, row, versions, events, [], {"covered": True}, sessions,
        data_dir=tmp_path, repo_root=tmp_path)
    previous = versions.roa
    def shifted(corp, quarter, day):
        return previous(f"{(int(corp) + 9) % 10:08d}", quarter, day)
    versions.roa = shifted
    o = runner.select_month(history, row, versions, events, [], {"covered": True}, sessions,
        data_dir=tmp_path, repo_root=tmp_path)
    assert len(s["common"]) == len(o["common"]) == 9
    assert set(s["common"].index) != set(o["common"].index)


@pytest.mark.parametrize("age,excluded", [(-1, False), (0, True), (62, True), (63, False)])
def test_buyback_title_only_exclusion_next_session_clock(monkeypatch, tmp_path, age, excluded):
    sessions, history, versions, row, events, _ = monthly_inputs(monkeypatch, computable=10)
    pos = sessions.get_loc(pd.Timestamp(row["decision_date"]))
    bb = [{"corp_code": "00000000", "actual_public_date": "2023-01-01", "known_position": pos - age}]
    selected = runner.select_month(history, row, versions, events, bb, {"covered": True}, sessions,
        data_dir=tmp_path, repo_root=tmp_path)
    assert ("000000" not in selected["A"]) is excluded


def test_buyback_titles_ignore_structured_response_and_future_actual_dates():
    sessions = pd.bdate_range("2023-01-02", "2023-02-28")
    titles = ["자기 주식 취득 결정", "자기주식취득결정(신탁)", "[정정]자기주식취득결정", "자기주식취득결정철회", "자기주식취득결정취소"]
    listings = [{"corp_code": f"{i:08d}", "stock_code": f"{i:05d}0", "rcept_no": f"20230106{i:06d}",
                 "rcept_dt": "20230106", "report_nm": title} for i, title in enumerate(titles)]
    dates = FilingDates(listings)
    events = runner.buyback_events(dates, sessions)
    assert len(events) == 1
    assert sessions[events[0]["known_position"]] == pd.Timestamp("2023-01-09")


def test_precheck_load_history_only_trailing_signal_and_never_uses_portfolio_loader(tmp_path, monkeypatch):
    sessions = pd.bdate_range("2023-01-02", periods=61)
    for day in sessions:
        write_rows(tmp_path / "market/krx_daily/2023" / day.strftime("%Y%m%d.jsonl"), [{
            "trade_date": str(day.date()), "stock_code": "000010", "stock_name": "보통주",
            "market": "KOSPI", "market_cap": 100., "close": 10000, "trading_value": 2e9,
            "calendar_status": "verified", "ret_1d": "MUST NOT USE", "change_rate_pct": 1.,
            "open": "MUST NOT USE", "volume": "MUST NOT USE"}])
    def denied(*a, **k):
        raise AssertionError("precheck reached a return calculation")
    monkeypatch.setattr(runner.hc003.krx_market, "load_prices", denied)
    monkeypatch.setattr(runner.hc003, "load_prices", denied)
    history = runner.load_history(tmp_path, sessions, sessions[-2], repo_root=tmp_path)
    assert not {"open", "volume"} & set(history)
    assert history.ret_1d.tolist() == [.01] * 60
    assert history.index.get_level_values("trade_date").max() == sessions[-2]
    assert history.close.tolist() == [10000] * 60


def test_precheck_computes_trailing_volatility_without_portfolio_or_forward_paths(tmp_path, monkeypatch):
    install(tmp_path, [version(2023, 1, 10)])
    registry = tmp_path / "research/experiments/registry.csv"
    registry.parent.mkdir(parents=True)
    registry.write_text("synthetic registry\n")
    def denied(*a, **k):
        raise AssertionError("precheck reached returns")
    monkeypatch.setattr(runner, "execute", denied)
    monkeypatch.setattr(runner, "price_book", denied)
    monkeypatch.setattr(runner.hc003, "load_prices", denied)
    monkeypatch.setattr(runner.portfolio, "simulate", denied)
    monkeypatch.setattr(runner.portfolio, "rebalance", denied)
    monkeypatch.setattr(runner.hc003, "PriceBook", denied)
    monkeypatch.setattr(runner.hc002, "forward_observations", denied)
    sessions, history, versions, row, events, _ = monthly_inputs(monkeypatch, computable=10)
    history["ret_1d"] = [((i // 10) % 2) * (i % 10 + 1) / 1000 for i in range(len(history))]
    monkeypatch.setattr(runner, "calendar", lambda unused: (sessions, [row]))
    monkeypatch.setattr(runner, "input_hashes", lambda *a: {"data": {}, "code": {}})
    def selections(*a, precheck_only, source_hashes, **k):
        assert precheck_only and source_hashes == {"data": {}, "code": {}}
        selected = runner.select_month(history, row, versions, events, [], {"covered": True}, sessions,
            data_dir=tmp_path, repo_root=tmp_path)
        return {"selections": {r: [selected] for r in ("S", "O")}, "filing_rules": {}, "L": 17,
                "sensitivity_available": True, "sensitivity_by_stage": {"design": True}}
    monkeypatch.setattr(runner, "prepare_selections", selections)
    result = runner.precheck(data_dir=tmp_path, repo_root=tmp_path)
    assert result["returns_computed"] is False and result["forward_prices_used"] is False
    assert result["trailing_volatility_computed"] is True
    assert result["link_coverage"]["op_conflicts"] == 0
    assert result["status"] == "ok"
    assert result["monthly_summary"]["S"]["minimum_C_over_A"] == 1.
    assert result["monthly"]["S"][0]["F_vol_count"] == 2
    assert result["registry_seal"]["sha256"] == runner.digest(registry)


def test_calendar_keeps_december_design_period_and_all_validation_decisions():
    sessions, plan = runner.calendar()
    december = next(row for row in plan if row["month"] == "2023-12")
    assert december["trade_date"] == "2024-01-02" and december["end_date"] == "2024-02-01"
    assert len([row for row in plan if row["month"] >= "2024-01"]) == 23
    assert plan[-1]["end_date"] == "2025-12-30" and plan[-1]["end_kind"] == "close"


def test_cli_has_no_implicit_execute_and_reproduce_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "execute", lambda **k: pytest.fail("unrequested real run"))
    with pytest.raises(SystemExit) as error:
        runner.main([])
    assert error.value.code == 2
    with pytest.raises(SystemExit):
        runner.main(["--reproduce-av001", "--output", str(tmp_path / "result.json")])
    with pytest.raises(SystemExit):
        runner.main(["--execute"])


def test_cli_rejects_main_checkout_data_and_symlink_output_before_computation(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    link = tmp_path / "escape"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    for path in (data / "result.json", link / "result.json"):
        with pytest.raises(SystemExit):
            runner.main(["--execute", "--output", str(path)])


@pytest.fixture
def synthetic_execution(tmp_path, monkeypatch):
    book, selections = fixture_book(tmp_path)
    registration = SimpleNamespace(commit_hash="synthetic", fields={"variants_planned": 1, "uses_holdout": False, "uses_llm": False})
    monkeypatch.setattr(runner.experiment_registry, "verify_preregistration", lambda *a: registration)
    monkeypatch.setattr(runner.experiment_registry, "_git", lambda *a: "synthetic")
    monkeypatch.setattr(runner, "seal_registry", lambda root: {"sha256": "synthetic", "M": 167})
    monkeypatch.setattr(runner, "calendar", lambda: ([], selections))
    monkeypatch.setattr(runner, "AssetArchive", lambda data: SimpleNamespace(pending=0))
    monkeypatch.setattr(runner, "link_coverage", lambda *a: {"op_conflicts": 0})
    monkeypatch.setattr(runner, "input_hashes", lambda *a: {"data": {}, "code": {}})
    for row in selections:
        row["precheck"] = {"data_complete": True}
        row.update(A=list("abcd"), boundaries={}, roa_provenance={})
    monkeypatch.setattr(runner, "prepare_selections", lambda *a, **k: {
        "selections": {rule: selections for rule in ("S", "O")}, "filing_rules": {}, "L": 17,
        "sensitivity_available": True, "sensitivity_by_stage": {"design": True, "validation": True}})
    monkeypatch.setattr(runner, "price_book", lambda *a: book)
    original_controls = runner.portfolio.controls
    calls = []
    def controls(*a, **k):
        calls.append((k["loss"], k.get("independent", False)))
        return original_controls(*a, **k, paths=3)
    monkeypatch.setattr(runner.portfolio, "controls", controls)
    monkeypatch.setattr(runner.experiment_registry, "verify_captured_preregistration", lambda *a: None)
    return calls


def test_execute_synthetic_rules_loss_and_atomic_results(tmp_path, synthetic_execution):
    output = tmp_path / "result.json"
    payload = runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output)
    assert payload["judgement"]["verdict"] == "insufficient"  # Only two synthetic months.
    assert len(synthetic_execution) == 8 and set(synthetic_execution) == {(False, False), (False, True), (True, False), (True, True)}
    assert set(payload["rules"]) == set(payload["vanished"]) == {"S", "O"}
    summary = runner.registry_metrics(payload)
    assert summary["M"] == summary["registry_seal"]["M"] == 167
    assert set(summary["rules"]) == {"S", "O"}
    assert json.loads(output.read_text()) == payload
    assert len(list(output.with_suffix(".controls").glob("*.jsonl"))) == 8
    rows = runner.hc003.registry_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "insufficient"


def test_relative_execute_output_keeps_control_links_relative_to_repo(tmp_path, monkeypatch, synthetic_execution):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    payload = runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=Path("result.json"))
    assert payload["rules"]["S"]["random_control"]["details"]["file"] == "result.controls/S_main_matched.jsonl"
    assert runner.recover_registry(Path("result.json"), repo_root=tmp_path)["registry_appended"] is False


def test_incomplete_precheck_exits_nonzero_without_changing_resource_limits(monkeypatch, capsys):
    monkeypatch.setattr(runner.resource, "setrlimit", lambda *a: None)
    monkeypatch.setattr(runner, "precheck", lambda **k: {"status": "incomplete", "reason": "source_snapshot_changed"})
    assert runner.main(["--precheck"]) == 2
    assert '"status": "incomplete"' in capsys.readouterr().out


def test_disappearance_after_last_trade_is_zeroed_at_terminal_close(tmp_path, monkeypatch):
    sessions = pd.bdate_range("2023-05-01", periods=30)
    index = pd.MultiIndex.from_product([sessions, ["a", "b"]], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000., "close": 10000., "base_price": 10000., "ret_1d": 0.,
        "volume": 1000., "market": "KOSPI", "trading_value": 2e9, "calendar_status": "verified"}, index=index)
    prices = prices.loc[~((prices.index.get_level_values("stock_code") == "a") &
                           (prices.index.get_level_values("trade_date") > sessions[22]))]
    def load(data_dir, start, end, **kwargs):
        return prices.loc[prices.index.get_level_values("trade_date").to_series(index=prices.index).between(start, end)], []
    monkeypatch.setattr(runner.hc003, "load_prices", load)
    planned = [{"trade_date": str(sessions[20].date()), "end_date": str(sessions[-1].date()), "end_kind": "close"}]
    book = runner.price_book(tmp_path, tmp_path, sessions, planned)
    assert book.marks(sessions[-1], "close")["a"] > 0
    assert book.marks(sessions[-1], "close", loss=True)["a"] == 0


def test_design_failed_O_link_is_not_masked_by_validation_success(tmp_path, monkeypatch):
    dates = SimpleNamespace(L=17, event_listings=lambda: pd.DataFrame({"rcept_dt": pd.Series(dtype=str)}))
    monkeypatch.setattr(runner, "filing_dates", lambda data: dates)
    events = pd.DataFrame({"rcept_dt": pd.Series(dtype=str), "rcept_no": pd.Series(dtype=str)})
    monkeypatch.setattr(runner.hf001, "rights_events", lambda *a: (events, {}))
    monkeypatch.setattr(runner, "buyback_events", lambda *a: [])
    monkeypatch.setattr(runner.dart_listing, "listing_status", lambda *a, **k: {})
    monkeypatch.setattr(runner.dart_listing, "listing_paths", lambda *a: [])
    monkeypatch.setattr(runner.hf001, "listing_coverage", lambda *a, **k: {"covered": True})
    def versions(*a, cutoff, **k):
        design = cutoff == "2024-01-01"
        return SimpleNamespace(report=lambda: {"sensitivity_available": not design,
            "sample_correction_links": {"failure": int(design), "success": int(not design)}})
    monkeypatch.setattr(runner, "ProfitabilityVersions", versions)
    monkeypatch.setattr(runner, "load_history", lambda *a, **k: None)
    monkeypatch.setattr(runner, "select_month", lambda history, row, *a, **k: {"precheck": dict(row)})
    sessions = pd.bdate_range("2023-01-02", "2024-02-01")
    plan = [{"month": month, "decision_date": date} for month, date in
            (("2023-12", "2023-12-29"), ("2024-01", "2024-01-31"))]
    result = runner.prepare_selections(tmp_path, tmp_path, sessions, plan, assets=None,
        precheck_only=True, source_hashes={"data": {}, "code": {}})
    assert result["sensitivity_by_stage"] == {"design": False, "validation": True}
    assert result["sensitivity_available"] is False
    assert all(len(result["selections"][rule]) == 2 for rule in ("S", "O"))
    for rows in result["selections"].values():
        for row in rows:
            prices = row["precheck"]["input_hashes"]["decision_prices"]
            assert len(prices) == 60
            assert max(prices).split("/")[-1][:8] <= row["precheck"]["decision_date"].replace("-", "")
    from test_experiments_qp001_judge import passing
    metrics = {rule: passing() for rule in ("S", "O")}
    assert runner.judge.judge(metrics, metrics,
        sensitivity_available=result["sensitivity_available"])["verdict"] == "sensitivity_unavailable"


@pytest.mark.parametrize("failure", ["hash", "memory", "serialization"])
def test_post_performance_failures_record_design_aborted(tmp_path, monkeypatch, synthetic_execution, failure):
    if failure == "hash":
        calls = []
        def hashes(*a):
            calls.append(True)
            return {"data": {"snapshot": str(len(calls))}, "code": {}}
        monkeypatch.setattr(runner, "input_hashes", hashes)
    elif failure == "memory":
        def failed(*a):
            raise MemoryError("synthetic allocation failure")
        monkeypatch.setattr(runner, "price_book", failed)
    else:
        original = runner.compute
        def bad_json(**kwargs):
            payload = original(**kwargs)
            payload["invalid"] = object()
            return payload
        monkeypatch.setattr(runner, "compute", bad_json)
    output = tmp_path / "result.json"
    with pytest.raises((ValueError, MemoryError, TypeError)):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output)
    assert not output.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    rows = runner.hc003.registry_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "design_aborted"
    assert json.loads(rows[0]["metrics_json"])["failure_type"] == {
        "hash": "ValueError", "memory": "MemoryError", "serialization": "TypeError"}[failure]


def test_save_then_registry_failure_recovers_without_any_computation(tmp_path, monkeypatch, synthetic_execution):
    record = runner.experiment_registry.record_trial
    def failed(*a, **k):
        raise OSError("synthetic registry append failure")
    monkeypatch.setattr(runner.experiment_registry, "record_trial", failed)
    output = tmp_path / "result.json"
    with pytest.raises(OSError, match="registry append"):
        runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output)
    assert output.is_file()
    before = output.read_bytes()
    assert runner.hc003.registry_rows(tmp_path)[0]["verdict"] == "design_aborted"
    def denied(*a, **k):
        pytest.fail("save-only recovery recomputed an experiment")
    for name in ("compute", "execute", "price_book", "input_hashes", "prepare_selections", "load_history"):
        monkeypatch.setattr(runner, name, denied)
    monkeypatch.setattr(runner.experiment_registry, "record_trial", record)
    recovered = runner.recover_registry(output, repo_root=tmp_path)
    assert recovered["registry_appended"] and not recovered["recomputed"]
    assert not runner.recover_registry(output, repo_root=tmp_path)["registry_appended"]
    assert output.read_bytes() == before
    rows = runner.hc003.registry_rows(tmp_path)
    assert [r["verdict"] for r in rows] == ["design_aborted", "insufficient"]


def test_result_file_is_fsynced_and_atomically_published_before_registry(tmp_path, monkeypatch, synthetic_execution):
    import stat
    from backtesting.experiments.qp001 import artifacts
    events = []
    sync, link = artifacts.os.fsync, artifacts.os.link
    def fsync(fd):
        events.append("dir_fsync" if stat.S_ISDIR(artifacts.os.fstat(fd).st_mode) else "file_fsync")
        sync(fd)
    def publish(src, dst):
        events.append("publish:" + dst.name)
        link(src, dst)
    monkeypatch.setattr(artifacts.os, "fsync", fsync)
    monkeypatch.setattr(artifacts.os, "link", publish)
    record = runner.experiment_registry.record_trial
    output = tmp_path / "result.json"
    def append(*a, **k):
        assert events[-3:] == ["file_fsync", "publish:result.json", "dir_fsync"]
        json.loads(output.read_text())
        return record(*a, **k)
    monkeypatch.setattr(runner.experiment_registry, "record_trial", append)
    runner.execute(data_dir=tmp_path, repo_root=tmp_path, output=output)
