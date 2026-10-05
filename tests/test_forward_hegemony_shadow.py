"""Forward HC002 records with synthetic, offline exchange-session archives."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments import hc001, hc002
from src.forward import hegemony_shadow as shadow
from src.ingestion import krx_market
from src.ingestion.storage import read_rows, write_rows


CODES = ["000010", "000020", "000030", "000040", "000050", "000060", "000011", "000070"]
DECISION = pd.Timestamp("2026-10-30")


def filing(code, year, revenue, income, *, day=None):
    day = day or f"{year}-08-14"
    return {"stock_codes": [code], "corp_code": f"{int(code):08d}", "bsns_year": year,
            "reprt_code": "11012", "fiscal_quarter": f"{year}Q2", "fs_div": "CFS",
            "available_date": day, "rcept_no": day.replace("-", "") + f"{int(code):06d}",
            "currency": "KRW", "raw_accounts": {
                metric: {"thstrm_amount": str(value), "thstrm_add_amount": str(value * 2),
                         "thstrm_dt": None, "currency": "KRW"}
                for metric, value in (("revenue", revenue), ("operating_income", income), ("net_income", 1))}}


def market_rows(day, position):
    return [{"trade_date": day.date().isoformat(), "stock_code": code, "stock_name": "기업" + code,
             "market": "KOSPI", "open": 10000, "high": 10100, "low": 9900,
             "close": 999 if code == "000070" else 10000, "base_price": 10000,
             "volume": 200000, "trading_value": 2e9, "calendar_status": "verified",
             "change_rate_pct": (-1 if position % 2 else 1) * (number + 1) * 0.1}
            for number, code in enumerate(CODES)]


def save_market(data, days, *, returns=None):
    for position, day in enumerate(days):
        rows = market_rows(day, position)
        if returns is not None:
            for row in rows:
                row["change_rate_pct"] = 100 * returns.get(row["stock_code"], 0)
        write_rows(data / "market/krx_daily" / f"{day:%Y}" / f"{day:%Y%m%d}.jsonl", rows)


def change_market(data, day, code, **changes):
    path = data / "market/krx_daily" / f"{day:%Y}" / f"{day:%Y%m%d}.jsonl"
    rows = read_rows(path)
    if changes.pop("remove", False):
        rows = [row for row in rows if row["stock_code"] != code]
    else:
        for row in rows:
            if row["stock_code"] == code:
                row.update(changes)
    write_rows(path, rows)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 10, 30, 17, tzinfo=shadow.KST))
    sessions = shadow._calendar(DECISION)
    position = sessions.get_loc(DECISION)
    save_market(tmp_path, sessions[position - 59:position + 1])
    revenue = [120, 120, 90, 120, 120, 120, 120, 120]
    income = [15, 11, 5, 15, 16, 11, 15, 15]
    for year in (2024, 2025):
        rows = [filing(code, year, 100 if year == 2024 else revenue[number],
                       10 if year == 2024 else income[number]) for number, code in enumerate(CODES)]
        write_rows(tmp_path / "fundamentals/dart_quarterly" / f"{year}_11012.jsonl", rows)
    profiles = [{"stock_code": code, "induty_code": "64000" if code == "000040" else "26100"} for code in CODES]
    write_rows(tmp_path / "reference/dart_company/companies.jsonl", profiles)
    return tmp_path


def holding_days(day=DECISION):
    sessions = shadow._calendar(day)
    position = sessions.get_loc(day)
    return sessions[position + 1:position + 1 + 20]


def decision_path(data):
    return data / shadow.RECORD_DIR / "decisions/202610.json"


def test_decision_matches_hc002_monthly_portfolio(data, monkeypatch):
    future = holding_days()
    save_market(data, future)
    monkeypatch.setattr(hc002, "MONTHS", pd.period_range("2026-10", "2026-10", freq="M"))
    monkeypatch.setattr(hc001, "CUTOFF", pd.Timestamp("2027-01-01"))
    days = shadow._calendar(DECISION)
    prices = krx_market.load_prices(None, "20260701", future[-1].strftime("%Y%m%d"), data)
    expected, _, excluded, _ = hc002.build_universe(prices, data_dir=data, sessions=days)
    assert not excluded
    record = shadow.decide(DECISION, data, backfill_label=True)
    selected = expected.loc[expected.phase.eq(2)].set_index("stock_code")
    assert [row["stock_code"] for row in record["holdings"]] == sorted(selected.index)
    assert record["universe_size"] == len(expected) == 5
    for row in record["holdings"]:
        signal = selected.loc[row["stock_code"]]
        for field in ("phase", "score", "revenue_yoy", "operating_income_yoy", "fiscal_quarter", "rcept_no", "avg_trading_value_20d"):
            assert row[field] == signal[field]
        assert row["weight"] == 1 / len(selected)
    assert record["planned_entry_date"] == "2026-11-02"
    assert record["planned_exit_date"] == future[-1].date().isoformat()
    assert len(record["planned_sessions"]) == 20
    assert len(record["preregistration_commit"]) == len(record["code_commit"]) == 40
    assert len(record["code_snapshot_sha256"]) == 64
    assert not (data / shadow.RECORD_DIR).exists()


@pytest.mark.parametrize("receipt,quarter", [("2026-10-29", "2026Q2"), ("2026-10-30", "2025Q2")])
def test_forward_quarters_and_strict_same_day_cutoff(data, receipt, quarter):
    write_rows(data / "fundamentals/dart_quarterly/2026_11012.jsonl",
               [filing("000010", 2026, 240, 60, day=receipt)])
    record = shadow.decide(DECISION, data)
    row = next(row for row in record["holdings"] if row["stock_code"] == "000010")
    assert row["fiscal_quarter"] == quarter
    assert row["yoy_reference"]["fiscal_quarter"] == ("2025Q2" if quarter == "2026Q2" else "2024Q2")
    assert row["revenue_yoy"] == (100 if quarter == "2026Q2" else 20)


def test_identical_rerun_preserves_immutable_bytes_and_original_time(data, monkeypatch):
    first = shadow.decide(DECISION, data, execute=True)
    saved = decision_path(data).read_bytes()
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 10, 31, 17, tzinfo=shadow.KST))
    assert shadow.decide(DECISION, data, execute=True) == first
    assert decision_path(data).read_bytes() == saved


def test_differing_rerun_refuses_and_explicit_append_keeps_original(data):
    shadow.decide(DECISION, data, execute=True)
    original = decision_path(data).read_bytes()
    change_market(data, DECISION, "000010", trading_value=4e9)
    for execute in (False, True):
        with pytest.raises(shadow.DecisionConflict) as error:
            shadow.decide(DECISION, data, execute=execute)
        assert any(item["field"] == "holdings" for item in error.value.differences)
        assert decision_path(data).read_bytes() == original
    rerun = shadow.decide(DECISION, data, execute=True, append_rerun=True)
    assert rerun["record_type"] == "rerun" and rerun["differences"]
    assert decision_path(data).read_bytes() == original
    assert len(read_rows(data / shadow.RECORD_DIR / "reruns/202610.jsonl")) == 1
    assert shadow.status(data)["forward_decisions"] == 1


def test_immutable_decision_detects_removed_nullable_metadata(data):
    original = shadow.decide(DECISION, data, execute=True)
    original["operator_note"] = None
    decision_path(data).write_text(json.dumps(original), encoding="utf-8")
    saved = decision_path(data).read_bytes()
    with pytest.raises(shadow.DecisionConflict) as error:
        shadow.decide(DECISION, data, execute=True)
    assert {"field": "operator_note", "before": None, "after": None, "change": "removed"} in error.value.differences
    assert decision_path(data).read_bytes() == saved


@pytest.mark.parametrize("lag,backfill,label", [(3, False, "forward"), (4, False, None),
                                                (4, True, "reconstructed"), (0, True, "reconstructed")])
def test_backdating_guard_counts_exchange_sessions(data, lag, backfill, label):
    save_market(data, holding_days()[:lag])
    if label is None:
        with pytest.raises(ValueError, match="backdated decision: 4 sessions"):
            shadow.decide(DECISION, data, execute=True)
        assert not (data / shadow.RECORD_DIR).exists()
    else:
        record = shadow.decide(DECISION, data, backfill_label=backfill)
        assert record["label"] == label and record["recording_delay_sessions"] == lag


def test_stale_price_cache_cannot_admit_backdated_forward_record(data, monkeypatch):
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 11, 6, 17, tzinfo=shadow.KST))
    with pytest.raises(ValueError, match="5 sessions behind current completed XKRX"):
        shadow.decide(DECISION, data, execute=True)
    assert not (data / shadow.RECORD_DIR).exists()
    record = shadow.decide(DECISION, data, backfill_label=True)
    assert record["label"] == "reconstructed" and record["recording_delay_sessions"] == 0


@pytest.mark.parametrize("day,now", [("2026-10-29", "2026-10-30T17:00:00+09:00"),
                                    ("2026-10-30", "2026-10-30T15:59:00+09:00"),
                                    ("2026-11-30", "2026-10-30T17:00:00+09:00")])
def test_only_closed_month_end_decisions(data, monkeypatch, day, now):
    monkeypatch.setattr(shadow, "_now", lambda: datetime.fromisoformat(now))
    with pytest.raises(ValueError, match="last session, after 16:00"):
        shadow.decide(day, data)


def test_missing_required_prices_and_pre_forward_date_fail(data):
    with pytest.raises(ValueError, match="precedes the preregistered forward period"):
        shadow.decide("2026-09-30", data, backfill_label=True)
    decision_path_market = data / "market/krx_daily/2026/20261030.jsonl"
    saved = decision_path_market.read_text()
    decision_path_market.unlink()
    with pytest.raises(ValueError, match="decision-day KRX prices"):
        shadow.decide(DECISION, data)
    decision_path_market.write_text(saved)
    (data / "market/krx_daily/2026/20261029.jsonl").unlink()
    with pytest.raises(ValueError, match="missing required ADV sessions"):
        shadow.decide(DECISION, data)


def test_profile_threshold_is_shared_and_reported(data):
    initial = shadow.decide(DECISION, data)
    assert initial["data_snapshot"]["profile_coverage"] == 1
    assert initial["data_snapshot"]["financial_exclusion_applied"] is True
    (data / "reference/dart_company/companies.jsonl").unlink()
    without = shadow.decide(DECISION, data)
    assert without["data_snapshot"]["profile_coverage"] == 0
    assert without["data_snapshot"]["financial_exclusion_applied"] is False
    assert "000040" in [row["stock_code"] for row in without["holdings"]]


def test_tags_never_change_selection_and_partial_archive_is_unknown(data):
    before = shadow.decide(DECISION, data)
    assert all(row["tags"]["capital_raise_prior_60_sessions"] == "unknown" for row in before["holdings"])
    window_start = pd.Timestamp(before["data_snapshot"]["dart_listing"]["window_start"])
    directory = data / "disclosures/dart_full/list/2026"
    rows = [{"stock_code": "000010", "rcept_dt": "20261029", "report_nm": "[정정] 유 상 증 자 결정"},
            {"stock_code": "000050", "rcept_dt": "20261029", "report_nm": "전환사채 발행 및 유상증자"}]
    write_rows(directory / "20261029.jsonl", rows)
    partial = shadow.decide(DECISION, data)
    tags = {row["stock_code"]: row["tags"] for row in partial["holdings"]}
    assert tags["000010"]["capital_raise_prior_60_sessions"] is True
    assert tags["000050"]["capital_raise_prior_60_sessions"] == "unknown"  # Bond category has priority.
    for day in pd.date_range(window_start, DECISION):
        path = directory / f"{day:%Y%m%d}.jsonl"
        if not path.exists():
            write_rows(path, [])
    # Future receipts never leak into the informational tag.
    write_rows(directory / "20261102.jsonl",
               [{"stock_code": "000050", "rcept_dt": "20261102", "report_nm": "유상증자"}])
    complete = shadow.decide(DECISION, data)
    assert complete["holdings"][1]["tags"]["capital_raise_prior_60_sessions"] is False
    for record in (partial, complete):
        assert [{k: v for k, v in row.items() if k != "tags"} for row in record["holdings"]] == [
            {k: v for k, v in row.items() if k != "tags"} for row in before["holdings"]]
    assert [row["tags"]["volatility_quintile_60d"] for row in before["holdings"]] == [1, 4]


def test_missing_volatility_history_keeps_selected_name(data):
    first = shadow._calendar(DECISION)
    old = first[first.get_loc(DECISION) - 45]
    change_market(data, old, "000010", change_rate_pct=None)
    record = shadow.decide(DECISION, data)
    assert record["holdings"][0]["stock_code"] == "000010"
    assert record["holdings"][0]["tags"]["volatility_quintile_60d"] == "unknown"
    assert record["holdings"][0]["weight"] == 0.5


def test_evaluation_matches_hand_computed_equal_weights_chain_and_cost(data):
    shadow.decide(DECISION, data, execute=True)
    original = decision_path(data).read_bytes()
    future = holding_days()
    save_market(data, future, returns={"000010": 0.01, "000050": -0.01})
    change_market(data, future[0], "000010", open=10500)
    change_market(data, future[-1], "000050", close=5000, base_price=5000)
    gross_a, gross_b = 10000 / 10500 * 1.01 ** 19 - 1, 0.99 ** 19 - 1
    gross = (gross_a + gross_b) / 2
    cost = (0.002 + 2 * (0.00015 + 10 / 10500 + 0.0005)
            + 0.002 + 2 * (0.00015 + 10 / 10000 + 0.0005)) / 2
    benchmark = (gross_a + gross_b) / 5
    result = shadow.evaluate(data)
    row = result["decisions"][0]
    assert row["gross"] == pytest.approx(gross)
    assert row["cost"] == pytest.approx(cost)
    assert row["net"] == pytest.approx(gross - cost)
    assert row["universe_gross"] == pytest.approx(benchmark)
    assert row["excess"] == pytest.approx(gross - cost - benchmark)
    assert row["turnover_based"]["cost"] == pytest.approx(cost)
    assert result["summary"]["count"] == 1
    assert result["summary"]["t_stat"] is None
    assert result["summary"]["cumulative"] == pytest.approx(row["excess"])
    assert not any(item["unadjusted_fallback"] for item in row["outcomes"])
    assert not (data / shadow.RECORD_DIR / "evaluation.json").exists()
    written = shadow.evaluate(data, execute=True)
    assert json.loads((data / shadow.RECORD_DIR / "evaluation.json").read_text()) == written
    assert decision_path(data).read_bytes() == original


@pytest.mark.parametrize("policy,reason,count,stale,fallback", [
    ("limit_up", "limit_up_entry", 1, False, False), ("delisted", "no_later_price", 1, False, False),
    ("missing_exit", None, 2, True, False), ("zero_volume", None, 2, True, False),
    ("limit_down", None, 2, True, False), ("raw_fallback", None, 2, False, True),
])
def test_hc002_execution_exclusions_and_exit_policies(data, policy, reason, count, stale, fallback):
    shadow.decide(DECISION, data, execute=True)
    days = holding_days()
    save_market(data, days, returns={})
    if policy == "limit_up":
        change_market(data, days[0], "000010", open=13000)
    elif policy == "delisted":
        for day in days[1:]:
            change_market(data, day, "000010", remove=True)
    elif policy == "missing_exit":
        change_market(data, days[-1], "000010", remove=True)
        sessions = shadow._calendar(DECISION)
        save_market(data, sessions[sessions.get_loc(days[-1]) + 1:sessions.get_loc(days[-1]) + 2], returns={})
    elif policy == "zero_volume":
        change_market(data, days[-1], "000010", volume=0)
    elif policy == "limit_down":
        change_market(data, days[-1], "000010", close=7000)
    else:
        change_market(data, days[3], "000010", change_rate_pct=None)
        change_market(data, days[-1], "000010", close=12000)
    result = shadow.evaluate(data)["decisions"][0]
    outcome = next(row for row in result["outcomes"] if row["stock_code"] == "000010")
    assert outcome["reason"] == reason
    assert result["evaluated_holdings_count"] == count
    assert outcome["stale_exit"] is stale and outcome["unadjusted_fallback"] is fallback
    if policy in ("missing_exit", "limit_down"):
        assert outcome["actual_exit_date"] == days[-2].date().isoformat()
    if policy == "raw_fallback":
        assert outcome["gross"] == pytest.approx(0.2)


def test_waits_for_exact_exit_and_never_extends_a_missing_global_session(data):
    shadow.decide(DECISION, data, execute=True)
    days = holding_days()
    save_market(data, days[:-1])
    pending = shadow.evaluate(data)
    assert pending["pending"][0]["reason"] == "exit_close_not_stored" and pending["summary"]["count"] == 0
    save_market(data, days[-1:])
    (data / "market/krx_daily/2026" / f"{days[3]:%Y%m%d}.jsonl").unlink()
    pending = shadow.evaluate(data)
    assert pending["pending"][0]["reason"] == "missing_price_sessions" and not pending["decisions"]


def test_evaluation_uses_frozen_financials_and_benchmark_universe(data):
    shadow.decide(DECISION, data, execute=True)
    save_market(data, holding_days(), returns={"000010": 0.01})
    before = shadow.evaluate(data)["decisions"]
    write_rows(data / "fundamentals/dart_quarterly/2025_11012.jsonl", [filing("000010", 2025, 50, 1)])
    (data / "reference/dart_company/companies.jsonl").unlink()
    assert shadow.evaluate(data)["decisions"] == before


@pytest.mark.parametrize("reconstructed", [False, True])
def test_running_summary_turnover_and_reconstruction_isolation(data, monkeypatch, reconstructed):
    shadow.decide(DECISION, data, execute=True, backfill_label=reconstructed)
    second = pd.Timestamp("2026-11-30")
    sessions = shadow._calendar(DECISION)
    save_market(data, sessions[(sessions > DECISION) & (sessions <= second)], returns={"000010": 0.001, "000050": 0.001})
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 11, 30, 17, tzinfo=shadow.KST))
    shadow.decide(second, data, execute=True)
    save_market(data, holding_days(second), returns={"000010": 0.002, "000050": 0.002})
    result = shadow.evaluate(data)
    rows = result["decisions"]
    assert rows[1]["turnover_based"]["retained"] == (0 if reconstructed else 2)
    assert rows[1]["turnover_based"]["cost"] == pytest.approx(rows[1]["cost"] if reconstructed else 0)
    expected = [row["excess"] for row in rows if row["label"] == "forward"]
    summary = result["summary"]
    assert summary["count"] == len(expected) == (1 if reconstructed else 2)
    assert summary["mean"] == pytest.approx(np.mean(expected))
    assert summary["cumulative"] == pytest.approx(np.prod(1 + np.asarray(expected)) - 1)
    if reconstructed:
        assert summary["t_stat"] is None and rows[0]["running_summary"]["count"] == 0
    else:
        assert summary["t_stat"] == pytest.approx(np.mean(expected) / (np.std(expected, ddof=1) / np.sqrt(2)))


def test_unknown_git_metadata_and_holdout_price_archives_are_not_read(data, monkeypatch):
    write_rows(data / "market/krx_daily/2026/20260102.jsonl", [{"invalid": "must not be read"}])
    monkeypatch.setattr(shadow, "_git", lambda *args: "unknown")
    record = shadow.decide(DECISION, data)
    assert record["preregistration_commit"] == record["code_commit"] == "unknown"
    assert not (data / "research/experiments/holdout_ledger.jsonl").exists()


def cli_module():
    spec = importlib.util.spec_from_file_location("hqa_forward_cli", shadow.PROJECT_ROOT / "scripts/forward/hegemony_shadow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("now", ["2026-10-05T17:00:00+09:00", "2026-10-30T15:59:00+09:00"])
def test_default_cli_non_decision_day_exits_zero_without_writes(tmp_path, monkeypatch, capsys, now):
    monkeypatch.setattr(shadow, "_now", lambda: datetime.fromisoformat(now))
    assert cli_module().main(["decide", "--data-dir", str(tmp_path), "--execute"]) == 0
    assert capsys.readouterr().out.strip() == "not a decision day"
    assert not list(tmp_path.iterdir())


def test_cli_dry_run_status_and_conflict_failure(data, capsys):
    cli = cli_module()
    assert cli.main(["decide", "--date", "2026-10-30", "--data-dir", str(data)]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not (data / shadow.RECORD_DIR).exists()
    assert cli.main(["decide", "--date", "2026-10-30", "--data-dir", str(data), "--execute"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--data-dir", str(data)]) == 0
    assert json.loads(capsys.readouterr().out)["forward_decisions"] == 1
    change_market(data, DECISION, "000010", trading_value=4e9)
    assert cli.main(["decide", "--date", "2026-10-30", "--data-dir", str(data), "--execute"]) == 1
    assert "immutable decision differs" in capsys.readouterr().err


def test_cli_evaluate_is_dry_by_default(data, capsys):
    cli = cli_module()
    assert cli.main(["evaluate", "--data-dir", str(data)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["evaluation"]["summary"]["count"] == 0
    assert not (data / shadow.RECORD_DIR).exists()
    assert cli.main(["evaluate", "--data-dir", str(data), "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is False
    assert (data / shadow.RECORD_DIR / "evaluation.json").is_file()


def test_fresh_process_module_inspection_has_no_trading_or_backend_imports(data):
    save_market(data, holding_days())
    code = """
import socket
def denied(*args, **kwargs):
    raise AssertionError('network is forbidden')
socket.socket.connect = denied
socket.getaddrinfo = denied
import sys
from datetime import datetime
from pathlib import Path
from src.forward import hegemony_shadow
hegemony_shadow._now = lambda: datetime(2026, 12, 31, 17, tzinfo=hegemony_shadow.KST)
hegemony_shadow.decide('2026-10-30', sys.argv[1], execute=True, backfill_label=True)
hegemony_shadow.evaluate(sys.argv[1], execute=True)
bad = []
for name, module in sys.modules.items():
    path = str(getattr(module, '__file__', '') or '')
    if (name.startswith('src.runner') or 'trade_signal_submitter' in name + path
            or 'backend' in name or name.startswith('src.tools.kis')
            or ('/src/runner/' in path and 'trading' in Path(path).name)):
        bad.append(name)
assert not bad, bad
print('isolated')
"""
    result = subprocess.run([sys.executable, "-c", code, str(data)], cwd=shadow.PROJECT_ROOT,
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
