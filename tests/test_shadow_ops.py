"""Collector HC002 scheduling, bounded memory and DART refreshes, all offline."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.ops import fundamentals_refresh as refresh
from scripts.ops import shadow_daily as daily
from src.forward import hegemony_shadow as shadow
from src.ingestion import dart_company, dart_quarterly, krx_market
from src.ingestion.storage import read_rows, write_rows
from test_forward_hegemony_shadow import (
    DECISION, change_market, data as shadow_data, decision_path, filing, holding_days, save_market,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 11, 3, tzinfo=shadow.KST)


@pytest.fixture(autouse=True)
def no_real_providers_or_env(monkeypatch):
    monkeypatch.setattr("requests.Session", lambda: pytest.fail("Unexpected DART session"))
    monkeypatch.setattr("src.config.settings.load_project_env", lambda *a: pytest.fail("Unexpected env read"))


@pytest.mark.parametrize("day,now,expected", [
    ("2026-10-30", "2026-10-31T08:55:00+09:00", True),  # Friday received Saturday.
    ("2027-04-30", "2027-05-01T08:55:00+09:00", True),  # Calendar month ends Friday; arrival next month.
    ("2026-10-29", "2026-10-31T08:55:00+09:00", False),
    ("2026-12-30", "2026-12-31T08:55:00+09:00", True),  # Year-end holiday on Thursday.
    ("2026-12-31", "2027-01-01T08:55:00+09:00", False),
    ("2026-10-30", "2026-10-30T15:59:00+09:00", False),
])
def test_real_xkrx_month_end_including_holidays(day, now, expected):
    assert bool(shadow.is_decision_day(day, now=datetime.fromisoformat(now))) is expected


def test_daily_records_saturday_arrival_once_and_keeps_actual_time(shadow_data, monkeypatch):
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 10, 31, 8, 55, tzinfo=shadow.KST))
    first = daily.daily_shadow(shadow_data, execute=True)
    record = json.loads(decision_path(shadow_data).read_text())
    assert first["decision"]["status"] == "recorded"
    assert record["decision_date"] == "2026-10-30" and record["label"] == "forward"
    assert record["recorded_at"] == "2026-10-31T08:55:00+09:00"
    saved = decision_path(shadow_data).read_bytes()
    # New data must never cause a different daily decision or append a rerun.
    change_market(shadow_data, DECISION, "000010", trading_value=4e9)
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 10, 31, 12, 30, tzinfo=shadow.KST))
    second = daily.daily_shadow(shadow_data, execute=True)
    assert second["decision"] == {"status": "skipped", "reason": "already_recorded"}
    assert second["evaluation"]["status"] == "ok"
    assert decision_path(shadow_data).read_bytes() == saved
    assert not (shadow_data / shadow.RECORD_DIR / "reruns").exists()
    assert json.loads((shadow_data / shadow.RECORD_DIR / "_last_run.json").read_text()) == second


def test_daily_friday_month_end_arriving_saturday_in_next_month(shadow_data, monkeypatch):
    day = pd.Timestamp("2027-04-30")
    sessions = shadow._calendar(day)
    position = sessions.get_loc(day)
    save_market(shadow_data, sessions[position - 59:position + 1])
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2027, 5, 1, 8, 55, tzinfo=shadow.KST))
    report = daily.daily_shadow(shadow_data, execute=True)
    assert report["decision"]["date"] == "2027-04-30"
    path = shadow_data / shadow.RECORD_DIR / "decisions/202704.json"
    assert json.loads(path.read_text())["recorded_at"] == "2027-05-01T08:55:00+09:00"
    assert not (path.parent / "202705.json").exists()
    assert daily.daily_shadow(shadow_data, execute=True)["decision"]["reason"] == "already_recorded"


@pytest.mark.parametrize("status", [None, "unverified_special_session", "exchange_calendar_mismatch"])
def test_daily_requires_every_latest_price_calendar_verified(shadow_data, status):
    change_market(shadow_data, DECISION, "000010", calendar_status=status)
    report = daily.daily_shadow(shadow_data, execute=True)
    assert report["decision"]["reason"] == "incomplete_krx_day"
    assert report["evaluation"]["status"] == "ok" and not decision_path(shadow_data).exists()


def test_daily_stale_month_end_refuses_without_reconstruction_and_evaluates(shadow_data, monkeypatch):
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 11, 6, 12, 30, tzinfo=shadow.KST))
    assert daily.main(["--execute", "--data-dir", str(shadow_data)]) == 0
    report = json.loads((shadow_data / shadow.RECORD_DIR / "_last_run.json").read_text())
    assert report["status"] == "error" and report["decision"]["status"] == "refused"
    assert "backdated decision" in report["decision"]["reason"]
    assert report["evaluation"]["status"] == "ok" and not decision_path(shadow_data).exists()


def test_daily_uses_latest_day_and_never_fills_missing_previous_month(shadow_data, monkeypatch):
    save_market(shadow_data, holding_days()[:1])
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 11, 3, 8, 55, tzinfo=shadow.KST))
    report = daily.daily_shadow(shadow_data, execute=True)
    assert report["latest_krx_day"] == "2026-11-02"
    assert report["decision"]["reason"] == "not_month_end"
    assert not decision_path(shadow_data).exists()


def test_daily_default_and_empty_store_exit_zero_without_writes(shadow_data, tmp_path, capsys):
    assert daily.main(["--data-dir", str(shadow_data)]) == 0
    assert json.loads(capsys.readouterr().out)["decision"]["status"] == "planned"
    assert not (shadow_data / shadow.RECORD_DIR).exists()
    empty = tmp_path / "empty"
    assert daily.main(["--data-dir", str(empty)]) == 0
    assert json.loads(capsys.readouterr().out)["decision"]["reason"] == "no_krx_data"
    assert not empty.exists()


def test_server_without_research_records_explicit_unknown_preregistration(shadow_data, monkeypatch):
    # Only the preregistration presence check sees a server without research/;
    # the code digest still reads the actual deployed source files.
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: False if path == ROOT / shadow.PREREGISTRATION else original(path))
    report = daily.daily_shadow(shadow_data, execute=True)
    assert report["status"] == "ok"
    record = json.loads(decision_path(shadow_data).read_text())
    assert record["preregistration_commit"] == "unknown (server)"


def test_deployed_transfer_manifest_can_record_without_repo_or_research(shadow_data, tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    rsync = commands / "rsync"
    rsync.write_text("#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    rsync.chmod(0o755)
    result = subprocess.run(["bash", str(ROOT / "scripts/ops/push_collector_code.sh"), "operator@192.0.2.1"],
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"}, capture_output=True, text=True, check=True)
    arguments = json.loads(result.stdout.splitlines()[-1])
    sources = [argument for argument in arguments if "/./" in argument]
    server = tmp_path / "server"
    server.mkdir()
    # Apply the actual rsync exclusions locally, without SSH or sudo. In
    # particular /research/ must not remove required src/research/ modules.
    rsync_program = shutil.which("rsync")
    assert rsync_program is not None, "local rsync is required for deployment verification"
    excludes = [argument for argument in arguments if argument.startswith("--exclude=")]
    subprocess.run([rsync_program, "-aR", *excludes, *sources, str(server) + "/"],
                   capture_output=True, text=True, check=True)
    code = """
import sys, socket, json
from datetime import datetime
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def denied(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect = denied
socket.getaddrinfo = denied
from scripts.ops import shadow_daily, fundamentals_refresh
from src.forward import hegemony_shadow as shadow
shadow._now = lambda: datetime(2026, 10, 31, 8, 55, tzinfo=shadow.KST)
result = shadow_daily.daily_shadow(sys.argv[2], execute=True)
assert result['status'] == 'ok', result
record = shadow._decisions(sys.argv[2])[0]
assert record['preregistration_commit'] == 'unknown (server)'
assert not Path('research').exists() and not Path('.git').exists()
assert not any(name.startswith(('src.runner', 'src.tools.kis')) for name in sys.modules)
print(json.dumps(result))
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(server), str(shadow_data)], cwd=server,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"]["status"] == "recorded"


@pytest.mark.parametrize("policy", ["normal", "raw", "halted", "limit_up", "limit_down", "delisted",
                                    "missing_exit", "returns_much_later", "later_untradable"])
def test_bounded_evaluation_equals_unbounded_for_all_exit_policies(shadow_data, policy, monkeypatch):
    shadow.decide(DECISION, shadow_data, execute=True)
    days = holding_days()
    save_market(shadow_data, days, returns={"000010": 0.001})
    sessions = shadow._calendar(DECISION, pd.Timestamp("2027-04-30"))
    later = sessions[(sessions > days[-1]) & (sessions < pd.Timestamp("2027-04-30"))]
    save_market(shadow_data, later, returns={"000010": 0.1})
    if policy == "raw":
        change_market(shadow_data, days[3], "000010", change_rate_pct=None)
    elif policy == "halted":
        for day in days[1:]:
            change_market(shadow_data, day, "000010", volume=0)
    elif policy == "limit_up":
        change_market(shadow_data, days[0], "000010", open=13000)
    elif policy == "limit_down":
        change_market(shadow_data, days[-1], "000010", close=7000)
    elif policy in {"delisted", "returns_much_later", "later_untradable"}:
        for day in [*days[1:], *later]:
            change_market(shadow_data, day, "000010", remove=True)
        if policy != "delisted":
            rows = read_rows(krx_market._path(later[-1].date(), shadow_data))
            rows.append({**rows[0], "stock_code": "000010", "volume": 0 if policy == "later_untradable" else 200000})
            write_rows(krx_market._path(later[-1].date(), shadow_data), rows)
    elif policy == "missing_exit":
        change_market(shadow_data, days[-1], "000010", remove=True)
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2027, 5, 1, 8, 55, tzinfo=shadow.KST))
    expected = shadow.evaluate(shadow_data)
    load = krx_market.load_prices
    calls = []
    def bounded(codes, start, end, data_dir, **kwargs):
        calls.append((start, end))
        return load(codes, start, end, data_dir, **kwargs)
    monkeypatch.setattr(krx_market, "load_prices", bounded)
    actual = shadow.evaluate(shadow_data, bounded_window=True)
    assert actual == expected
    assert calls == [("20261030", f"{days[-1]:%Y%m%d}")]


def test_bounded_decision_selection_matches_all_available_price_history(shadow_data):
    original = shadow.decide(DECISION, shadow_data)
    record = shadow.decide(DECISION, shadow_data, bounded_window=True)
    assert record["universe"] == original["universe"]
    assert record["holdings"] == original["holdings"]
    prices = krx_market.load_prices(None, "20260701", "20261030", shadow_data)
    full, _ = shadow.hc002.universe_on_decision(prices, DECISION, data_dir=shadow_data)
    assert [row["stock_code"] for row in record["holdings"]] == sorted(full.index[full.phase.eq(2)])
    for row in record["universe"]:
        for field in ("phase", "score", "avg_trading_value_20d", "revenue_yoy", "operating_income_yoy"):
            assert row[field] == full.loc[row["stock_code"], field]


@pytest.mark.parametrize("reconstructed", [False, True])
def test_bounded_monthly_history_matches_turnover_and_running_summaries(shadow_data, monkeypatch, reconstructed):
    shadow.decide(DECISION, shadow_data, execute=True, backfill_label=reconstructed, bounded_window=True)
    second = pd.Timestamp("2026-11-30")
    sessions = shadow._calendar(DECISION)
    save_market(shadow_data, sessions[(sessions > DECISION) & (sessions <= second)],
                returns={"000010": 0.001, "000050": -0.001})
    monkeypatch.setattr(shadow, "_now", lambda: datetime(2026, 11, 30, 17, tzinfo=shadow.KST))
    shadow.decide(second, shadow_data, execute=True, bounded_window=True)
    save_market(shadow_data, holding_days(second), returns={"000010": 0.002, "000050": -0.002})
    assert shadow.evaluate(shadow_data, bounded_window=True) == shadow.evaluate(shadow_data)


def test_year_at_a_time_financials_equal_original_full_history(shadow_data):
    directory = shadow_data / "fundamentals/dart_quarterly"
    for year in range(2023, 2027):
        for report, quarter in dart_quarterly.REPORTS.items():
            original = filing("000010", year, 100, 10, day=f"{year}-05-14")
            original.update(reprt_code=report, fiscal_quarter=f"{year}Q{quarter}")
            corrected = {**original, "available_date": f"{year}-06-01", "rcept_no": f"{year}0601000010"}
            separate = {**corrected, "fs_div": "OFS", "stock_codes": ["000010", "000011"]}
            existing = read_rows(directory / f"{year}_{report}.jsonl")
            write_rows(directory / f"{year}_{report}.jsonl", [*existing, original, corrected, separate])
    # Reproduce the original all-years algorithm, including statement preference,
    # correction ordering, alias ordering and strict receipt-day cutoff.
    records = [row for path in sorted(directory.glob("*_110*.jsonl")) for row in read_rows(path)
               if row["available_date"] < "2026-10-30"]
    latest = {}
    for row in dart_quarterly._derive(records):
        key = row["corp_code"], row["fiscal_quarter"]
        rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
        if key not in latest or rank > latest[key][0]:
            latest[key] = rank, row
    rows = [{**{field: row[field] for field in dart_quarterly.COLUMNS if field != "stock_code"}, "stock_code": code}
            for _, row in (latest[key] for key in sorted(latest)) for code in row["stock_codes"]]
    expected = pd.DataFrame(rows, columns=dart_quarterly.COLUMNS)
    for metric in dart_quarterly.ACCOUNTS:
        expected[metric] = pd.to_numeric(expected[metric]).astype(float)
    pd.testing.assert_frame_equal(dart_quarterly.load_quarterly(DECISION, data_dir=shadow_data), expected)


def refresh_archive(data_dir, count=3):
    codes = [f"{number * 10:06d}" for number in range(1, count + 1)]
    path = data_dir / "reference/corp_codes.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["corp_code", "stock_code"])
        writer.writeheader()
        writer.writerows({"corp_code": f"{int(code):08d}", "stock_code": code} for code in codes)
        writer.writerow({"corp_code": "99999999", "stock_code": ""})  # Nonlisted corporate reference.
    write_rows(data_dir / "market/krx_daily/2026/20261008.jsonl", [{"stock_code": code} for code in codes])
    (data_dir / "disclosures/dart_full/list").mkdir(parents=True)
    return codes


class OfflineDart:
    def __init__(self, *, quarterly_status="013", company_status="000"):
        self.calls = []
        self.quarterly_status, self.company_status = quarterly_status, company_status

    def get(self, url, *, params, **kwargs):
        self.calls.append((url, params))
        if url == dart_quarterly.URL:
            payload = {"status": self.quarterly_status}
        else:
            corp = params["corp_code"]
            payload = {"status": self.company_status, "corp_code": corp, "stock_code": f"{int(corp):06d}",
                       "corp_name": "합성기업", "corp_cls": "Y", "induty_code": "26100", "est_dt": "", "acc_mt": "12"}
        return SimpleNamespace(json=lambda: payload, status_code=200, raise_for_status=lambda: None)


def run_refresh(data, session, **kwargs):
    return refresh.refresh_fundamentals(data, execute=True, api_key="offline-fixture", session=session,
        clock=kwargs.pop("clock", lambda: NOW), sleeper=lambda _: None, **kwargs)


def test_refresh_dry_run_no_requests_secrets_writes_and_years(tmp_path, monkeypatch, capsys):
    refresh_archive(tmp_path, 201)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    monkeypatch.setattr(refresh, "datetime", SimpleNamespace(now=lambda tz: NOW,
        strptime=datetime.strptime, fromisoformat=datetime.fromisoformat))
    getenv = os.getenv
    monkeypatch.setattr(refresh.os, "getenv", lambda name, default=None: getenv(name, default)
                        if name == "HQA_DATA_DIR" else pytest.fail("Dry run read a secret"))
    assert refresh.main(["--data-dir", str(tmp_path), "--max-requests", "7"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run" and report["requests_used"] == 0
    assert (report["from_year"], report["to_year"]) == (2025, 2026)
    assert report["quarterly"]["planned_requests"] == 7
    assert report["company"]["planned_requests"] == 200
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_refresh_caps_quarterly_and_profiles_across_weekly_reruns(tmp_path):
    refresh_archive(tmp_path, 201)
    session = OfflineDart()
    first = run_refresh(tmp_path, session, max_requests=7)
    assert first["status"] == "quota_reached"
    assert (first["quarterly_requests"], first["company_requests"], first["requests_used"]) == (7, 200, 207)
    assert len(read_rows(tmp_path / "reference/dart_company/companies.jsonl")) == 200
    second = run_refresh(tmp_path, session, max_requests=7, clock=lambda: NOW + timedelta(hours=5))
    assert second["requests_used"] == 0 and len(session.calls) == 207
    third = run_refresh(tmp_path, session, max_requests=7, clock=lambda: NOW + timedelta(days=1))
    assert third["quarterly_requests"] == 7 and third["company_requests"] == 1
    assert json.loads((tmp_path / refresh.LAST_RUN).read_text()) == third


def test_refresh_rotates_capped_batches_across_both_business_years(tmp_path):
    refresh_archive(tmp_path, 201)
    session = OfflineDart()
    for week in range(4):
        report = run_refresh(tmp_path, session, max_requests=7, clock=lambda: NOW + timedelta(days=7 * week))
        assert report["quarterly_requests"] == 7
    batches = {(params["bsns_year"], params["reprt_code"], params["corp_code"])
               for url, params in session.calls if url == dart_quarterly.URL}
    assert len(batches) == 24  # Two years × four reports × three company batches.
    assert {year for year, _, _ in batches} == {"2025", "2026"}


def test_refresh_profile_subset_only_latest_krx_missing_profiles(tmp_path):
    codes = refresh_archive(tmp_path)
    session = OfflineDart()
    dart_company.DartCompanyCollector("offline-fixture", data_dir=tmp_path, session=session,
        clock=lambda: NOW, sleeper=lambda _: None).collect(tmp_path / "reference/corp_codes.csv",
            stock_codes=codes[:1], execute=True)
    write_rows(tmp_path / "market/krx_daily/2026/20261008.jsonl", [{"stock_code": code} for code in codes[:2]])
    session.calls.clear()
    report = run_refresh(tmp_path, session)
    requested = [params["corp_code"] for url, params in session.calls if url == dart_company.COMPANY_URL]
    assert requested == ["00000020"] and report["company_requests"] == 1
    assert report["quarterly_requests"] == 8


def test_refresh_reissues_completed_batches_and_preserves_original_receipts(tmp_path):
    codes = refresh_archive(tmp_path, 1)
    class CorrectingDart(OfflineDart):
        def get(self, url, *, params, **kwargs):
            if url != dart_quarterly.URL or params["reprt_code"] != "11013":
                return super().get(url, params=params, **kwargs)
            self.calls.append((url, params))
            year = params["bsns_year"]
            rows = [{"corp_code": "00000010", "bsns_year": year, "reprt_code": "11013", "fs_div": "CFS",
                     "rcept_no": f"{NOW:%Y%m%d}000001", "currency": "KRW", "account_nm": name,
                     "thstrm_amount": str(value)} for name, value in (("매출액", 200), ("영업이익", 50), ("당기순이익", 20))]
            return SimpleNamespace(json=lambda: {"status": "000", "list": rows}, raise_for_status=lambda: None)
    dart_quarterly.backfill(2025, 2026, data_dir=tmp_path, execute=True, api_key="offline-fixture",
        session=OfflineDart(), corp_codes_path=tmp_path / "reference/corp_codes.csv", clock=lambda: NOW - timedelta(days=7))
    original = {**filing(codes[0], 2025, 100, 10, day="2025-05-15"),
                "reprt_code": "11013", "fiscal_quarter": "2025Q1"}
    path = tmp_path / "fundamentals/dart_quarterly/2025_11013.jsonl"
    dart_quarterly._publish(path.parent, 2025, "11013", [original])
    before = read_rows(path)[0]
    report = run_refresh(tmp_path, CorrectingDart())
    assert report["quarterly_requests"] == 8
    assert read_rows(path)[0] == before
    assert {row["rcept_no"] for row in read_rows(path)} == {before["rcept_no"], "20261011000001"}


@pytest.mark.parametrize("blocked,used,expected", [(False, 17997, 3), (False, 18000, 0), (True, 100, 0)])
def test_refresh_respects_shared_backfill_budget_and_provider_limit(tmp_path, blocked, used, expected):
    refresh_archive(tmp_path, 1)
    path = tmp_path / "disclosures/dart_full/_quota.json"
    path.write_text(json.dumps({"days": {NOW.date().isoformat(): {"requests": used, "provider_limited": blocked}}}))
    session = OfflineDart()
    report = run_refresh(tmp_path, session)
    assert report["requests_used"] == len(session.calls) == expected
    assert report["status"] == "quota_reached"
    assert json.loads(path.read_text())["days"][NOW.date().isoformat()]["requests"] == used


@pytest.mark.parametrize("kind,provider_status,status", [("quarterly", "020", "quota_reached"),
    ("quarterly", "010", "error"), ("company", "020", "quota_reached"), ("company", "010", "error")])
def test_refresh_errors_are_counted_and_do_not_continue_or_leak_keys(tmp_path, kind, provider_status, status):
    refresh_archive(tmp_path, 1)
    session = OfflineDart(**{f"{kind}_status": provider_status})
    report = run_refresh(tmp_path, session)
    assert report["status"] == status
    assert report["requests_used"] == len(session.calls) == (1 if kind == "quarterly" else 9)
    assert "offline-fixture" not in json.dumps(report)
    if kind == "quarterly":
        assert not any(url == dart_company.COMPANY_URL for url, _ in session.calls)


def test_refresh_stops_before_requests_at_kst_midnight(tmp_path):
    refresh_archive(tmp_path, 1)
    times = iter([NOW, NOW, NOW + timedelta(days=1)])
    session = OfflineDart()
    report = run_refresh(tmp_path, session, clock=lambda: next(times, NOW + timedelta(days=1)))
    assert report["status"] == "error" and report["requests_used"] == 1
    assert len(session.calls) == 1


@pytest.mark.parametrize("max_requests", [0, -1])
def test_refresh_invalid_cap_fails_before_requests_or_writes(tmp_path, capsys, max_requests):
    assert refresh.main(["--data-dir", str(tmp_path), "--max-requests", str(max_requests)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert not list(tmp_path.iterdir())


def _measure_shadow_memory(data_dir):
    """Fresh-process benchmark: 3,000 stocks, 60 prices, 12 years of financials."""
    import socket
    import resource
    socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError("network forbidden"))
    socket.getaddrinfo = socket.socket.connect
    shadow._now = lambda: datetime(2026, 10, 31, 8, 55, tzinfo=shadow.KST)
    codes = [f"{number * 10:06d}" for number in range(1, 3001)]
    sessions = shadow._calendar(DECISION)
    position = sessions.get_loc(DECISION)
    for day in sessions[position - 59:position + 1 + 20]:
        path = krx_market._path(day.date(), data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as handle:
            for number, code in enumerate(codes):
                row = {"trade_date": day.date().isoformat(), "stock_code": code, "stock_name": "합성기업" + code,
                    "market": "KOSPI", "open": "10000", "high": "10100", "low": "9900", "close": "10000",
                    "base_price": "10000", "volume": "200000", "trading_value": "2000000000",
                    "market_cap": "1000000000000", "listed_shares": "100000000",
                    "change_rate_pct": str((number % 10 + 1) / 10), "calendar_status": "verified",
                    "price_basis": "unadjusted", "collected_at": "2026-10-31T08:40:00+09:00",
                    "available_at": "2026-10-31T08:40:00+09:00", "bar_at": day.date().isoformat() + "T15:30:00+09:00",
                    "version": "a" * 64, "source_url": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=" + f"{day:%Y%m%d}"}
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    directory = data_dir / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True)
    for year in range(2015, 2027):
        for report, quarter in dart_quarterly.REPORTS.items():
            available = f"{year}-" + {1: "05-15", 2: "08-14", 3: "11-14", 4: "12-29"}[quarter]
            with (directory / f"{year}_{report}.jsonl").open("w") as handle:
                for code in codes:
                    row = filing(code, year, 100 + 10 * (year - 2015), 10 + 5 * (year - 2015), day=available)
                    row.update(reprt_code=report, fiscal_quarter=f"{year}Q{quarter}")
                    for value in row["raw_accounts"].values():
                        value["thstrm_add_amount"] = str(float(value["thstrm_amount"]) * quarter)
                    handle.write(json.dumps(row) + "\n")
    # Future price files are present, but are hidden for the first forward decision.
    real_days = shadow._price_days
    shadow._price_days = lambda root: [day for day in real_days(root) if day <= DECISION]
    decided = daily.daily_shadow(data_dir, execute=True)
    assert decided["decision"]["status"] == "recorded", decided
    shadow._price_days = real_days
    shadow._now = lambda: datetime(2026, 12, 1, 8, 55, tzinfo=shadow.KST)
    evaluated = daily.daily_shadow(data_dir, execute=True)
    assert evaluated["evaluation"]["evaluated_count"] == 1, evaluated
    print(json.dumps({"stocks": len(codes), "price_sessions": 80, "financial_years": 12,
                      "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}))


def test_shadow_full_market_peak_rss_under_600_mib(tmp_path, record_property):
    code = "import sys; from pathlib import Path; sys.path.insert(0, 'tests'); " \
           "from test_shadow_ops import _measure_shadow_memory; _measure_shadow_memory(Path(sys.argv[1]))"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    print("HC002 memory: " + json.dumps(report))
    record_property("shadow_peak_rss_mib", report["peak_rss_mib"])
    assert report["peak_rss_mib"] < 600, report
