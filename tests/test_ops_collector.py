import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ops import collector_status as status
from scripts.ops import dart_backfill_daily as dart_daily
from scripts.ops import krx_catchup as catchup
from scripts.ops import investor_flow_daily as flow_daily
from src.ingestion import dart_backfill
from src.ingestion import krx_chart

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 8, 20, tzinfo=status.KST)


@pytest.fixture(autouse=True)
def offline_clock_and_disk(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(catchup, "datetime", FixedDatetime)
    monkeypatch.setattr(status, "datetime", FixedDatetime)
    monkeypatch.setattr(dart_daily, "datetime", FixedDatetime)
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW)
    monkeypatch.setattr(status.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(8_000_000_000, 0, 8_000_000_000))
    monkeypatch.setattr("src.config.settings.load_project_env", lambda *args, **kwargs: pytest.fail("Unexpected env read"))
    monkeypatch.setattr(krx_chart.requests, "Session", lambda: pytest.fail("Unexpected provider session"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def daily(data_dir, day, *calendar_statuses):
    path = data_dir / "market" / "krx_daily" / day[:4] / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"calendar_status": value}) + "\n"
                            for value in (calendar_statuses or ("verified",))), encoding="utf-8")
    return path


def test_catchup_empty_store_starts_fourteen_calendar_days_ago(tmp_path, capsys):
    assert catchup.catchup_range(tmp_path, NOW.date()) == ("20260825", "20260907")
    catchup.main(["--data-dir", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert report["planned"] == ["20260825", "20260826", "20260827", "20260828", "20260831",
                                 "20260901", "20260902", "20260903", "20260904", "20260907"]
    assert report["dry_run"] and report["fetched"] == 0
    assert list(tmp_path.iterdir()) == []


def test_catchup_weekend_gap_skips_saved_friday_and_weekend(tmp_path, capsys):
    daily(tmp_path, "20260904")
    assert catchup.catchup_range(tmp_path, NOW.date()) == ("20260904", "20260907")
    catchup.main(["--data-dir", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert report["planned"] == ["20260907"]
    assert report["skipped_existing"] == ["20260904"]


@pytest.mark.parametrize("execute", [False, True])
def test_catchup_retries_empty_weekday_before_later_saved_day(tmp_path, monkeypatch, capsys, execute):
    daily(tmp_path, "20260907")
    state = tmp_path / "market/krx_daily/_state.json"
    write_json(state, {"empty_dates": ["20260904"]})
    requested = []

    def collect_day(self, day):
        requested.append(day)
        return [{"stock_code": "005930", "trade_date": "2026-09-04", "version": "recovered",
                 "calendar_status": "verified", "collected_at": NOW.isoformat(), "available_at": NOW.isoformat()}]

    monkeypatch.setattr(catchup.krx_market.KrxMarketCollector, "collect_day", collect_day)
    catchup.main(["--data-dir", str(tmp_path)] + (["--execute"] if execute else []))
    report = json.loads(capsys.readouterr().out)
    assert (report["from_date"], report["to_date"]) == ("20260904", "20260907")
    assert report["planned"] == ["20260904"] and report["skipped_existing"] == ["20260907"]
    assert report["stale_empty_dates_not_retried"] == []
    assert requested == (["20260904"] if execute else [])
    assert report["fetched"] == report["saved_rows"] == int(execute)
    assert (tmp_path / "market/krx_daily/2026/20260904.jsonl").exists() is execute
    assert json.loads(state.read_text()) == {"empty_dates": [] if execute else ["20260904"]}


@pytest.mark.parametrize("stored_day", ["20260907", "20260806"])
@pytest.mark.parametrize("execute", [False, True])
def test_catchup_stale_empty_date_is_reported_without_retry(tmp_path, monkeypatch, capsys, stored_day, execute):
    daily(tmp_path, stored_day)
    state = tmp_path / "market/krx_daily/_state.json"
    write_json(state, {"empty_dates": ["20260807"]})
    requested = []

    def collect_day(self, day):
        requested.append(day)
        return []

    monkeypatch.setattr(catchup.krx_market.KrxMarketCollector, "collect_day", collect_day)
    catchup.main(["--data-dir", str(tmp_path)] + (["--execute"] if execute else []))
    report = json.loads(capsys.readouterr().out)
    assert report["from_date"] == stored_day and report["to_date"] == "20260907"
    assert report["stale_empty_dates_not_retried"] == ["20260807"]
    assert "20260807" not in report["planned"] and "20260807" not in requested
    assert requested == (report["planned"] if execute else [])
    assert report["fetched"] == (len(report["planned"]) if execute else 0)
    assert "20260807" in json.loads(state.read_text())["empty_dates"]


def test_catchup_holiday_stays_recorded_after_repeated_empty_responses(tmp_path, monkeypatch, capsys):
    holiday = date(2026, 8, 17)
    for offset in range(1, (NOW.date() - holiday).days):
        day = holiday + timedelta(days=offset)
        if day.weekday() < 5:
            daily(tmp_path, day.strftime("%Y%m%d"))
    state = tmp_path / "market/krx_daily/_state.json"
    write_json(state, {"empty_dates": ["20260817"]})
    original_state = state.read_bytes()
    requested = []

    def collect_day(self, day):
        requested.append(day)
        return []

    monkeypatch.setattr(catchup.krx_market.KrxMarketCollector, "collect_day", collect_day)
    for _ in range(2):
        catchup.main(["--data-dir", str(tmp_path), "--execute"])
        report = json.loads(capsys.readouterr().out)
        assert report["planned"] == report["empty"] == ["20260817"]
        assert report["fetched"] == 1 and report["saved_rows"] == 0
        assert state.read_bytes() == original_state
        assert not (tmp_path / "market/krx_daily/2026/20260817.jsonl").exists()
    assert requested == ["20260817", "20260817"]


@pytest.mark.parametrize("empty_day", ["20260901", "20260905", "20260908", "20260909"])
def test_catchup_ignores_stored_weekend_current_and_future_empty_dates(tmp_path, monkeypatch, capsys, empty_day):
    daily(tmp_path, "20260901")
    daily(tmp_path, "20260907")
    write_json(tmp_path / "market/krx_daily/_state.json", {"empty_dates": [empty_day]})
    monkeypatch.setattr(catchup.krx_market.KrxMarketCollector, "collect_day",
                        lambda *args: pytest.fail("Unexpected day requested"))
    assert catchup.catchup_range(tmp_path, NOW.date()) == ("20260907", "20260907")
    catchup.main(["--data-dir", str(tmp_path), "--execute"])
    report = json.loads(capsys.readouterr().out)
    assert report["planned"] == [] and report["fetched"] == 0
    assert report["stale_empty_dates_not_retried"] == []


def test_catchup_retries_empty_weekday_exactly_thirty_calendar_days_old(tmp_path):
    daily(tmp_path, "20260907")
    write_json(tmp_path / "market/krx_daily/_state.json", {"empty_dates": ["20260810"]})
    assert catchup.catchup_range(tmp_path, date(2026, 9, 9)) == ("20260810", "20260908")


def test_catchup_empty_store_retries_recent_empty_before_bootstrap_range(tmp_path):
    write_json(tmp_path / "market/krx_daily/_state.json", {"empty_dates": ["20260817"]})
    assert catchup.catchup_range(tmp_path, NOW.date()) == ("20260817", "20260907")


def test_catchup_already_current_makes_no_requests(tmp_path, capsys):
    daily(tmp_path, "20260907")
    catchup.main(["--data-dir", str(tmp_path), "--execute"])
    report = json.loads(capsys.readouterr().out)
    assert not report["dry_run"] and report["planned"] == []
    assert report["fetched"] == report["saved_rows"] == 0


@pytest.mark.parametrize("today", [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 12), date(2026, 1, 1)])
def test_catchup_never_includes_today(tmp_path, today):
    start, end = catchup.catchup_range(tmp_path, today)
    assert datetime.strptime(start, "%Y%m%d").date() < today
    assert datetime.strptime(end, "%Y%m%d").date() < today


@pytest.mark.parametrize("day", ["20260908", "20260909"])
def test_catchup_rejects_current_or_future_store_dates(tmp_path, day):
    daily(tmp_path, day)
    with pytest.raises(ValueError, match="current or future"):
        catchup.catchup_range(tmp_path, NOW.date())


@pytest.mark.parametrize("execute", [False, True])
def test_catchup_execute_is_explicit_and_environment_data_dir_is_used(tmp_path, monkeypatch, capsys, execute):
    calls = []

    def fake_backfill(start, end, data_dir, *, execute):
        calls.append((start, end, data_dir, execute))
        return {"planned": ["20260907"]}

    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(catchup.krx_market, "backfill", fake_backfill)
    catchup.main(["--execute"] if execute else [])
    assert calls == [("20260825", "20260907", tmp_path, execute)]
    assert json.loads(capsys.readouterr().out)["dry_run"] is not execute


def test_catchup_provider_failure_propagates(tmp_path, monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(catchup.krx_market, "backfill", failed)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        catchup.main(["--data-dir", str(tmp_path), "--execute"])


@pytest.mark.parametrize("now,stale", [
    ("2026-09-08T09:59:59+09:00", False),
    ("2026-09-08T10:00:00+09:00", True),
    ("2026-09-08T20:00:00+09:00", True),
    ("2026-09-12T20:00:00+09:00", False),
    ("2026-09-08T01:00:00+00:00", True),
])
def test_status_stale_threshold_uses_kst_weekdays(tmp_path, now, stale):
    report = status.collect_status(tmp_path, clock=lambda: datetime.fromisoformat(now))
    assert report["poller_stale"] is stale
    assert ("poller_stale" in report["flags"]) is stale
    assert report["krx_lag_days"] is None and "krx_store_empty" in report["flags"]


def test_status_empty_successful_poll_is_fresh_without_jsonl(tmp_path):
    write_json(tmp_path / "disclosures/first_seen/_state.json", {"last_completed_date": "20260908"})
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert not report["poller_stale"]
    assert report["dart_latest_file"] is None and report["newest_first_seen_at"] is None


def test_status_reads_only_latest_dart_file_and_maximum_timestamps(tmp_path):
    directory = tmp_path / "disclosures" / "first_seen"
    directory.mkdir(parents=True)
    (directory / "20260907.jsonl").write_text("old archive is deliberately not JSON", encoding="utf-8")
    (directory / "20260908.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"first_seen_at": "2026-09-08T02:00:00+00:00", "poll_completed_at": "2026-09-08T11:00:01+09:00"},
        {"first_seen_at": "2026-09-08T10:00:00+09:00", "poll_completed_at": "2026-09-08T12:00:00+09:00"},
    ]), encoding="utf-8")
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert report["newest_first_seen_at"] == "2026-09-08T11:00:00+09:00"
    assert report["newest_poll_completed_at"] == "2026-09-08T12:00:00+09:00"
    assert report["dart_latest_file"] == "disclosures/first_seen/20260908.jsonl"
    assert not report["poller_stale"]


@pytest.mark.parametrize("day,lag,flagged", [("20260907", 0, False), ("20260904", 1, False), ("20260903", 2, True)])
def test_status_krx_weekday_lag_skips_weekend(tmp_path, day, lag, flagged):
    daily(tmp_path, day)
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert report["krx_lag_days"] == lag
    assert ("krx_lag_days" in report["flags"]) is flagged
    assert report["krx_expected_date"] == "2026-09-07"


def test_status_monday_expects_friday(tmp_path):
    daily(tmp_path, "20260904")
    report = status.collect_status(tmp_path, clock=lambda: datetime(2026, 9, 7, 20, tzinfo=status.KST))
    assert report["krx_expected_date"] == "2026-09-04" and report["krx_lag_days"] == 0


def test_status_preserves_empty_and_older_calendar_unverified_dates(tmp_path):
    daily(tmp_path, "20260903", "verified", "unverified_special_session")
    daily(tmp_path, "20260904", "exchange_calendar_mismatch")
    daily(tmp_path, "20260907")
    write_json(tmp_path / "market/krx_daily/_state.json", {"empty_dates": ["20260902", "20260901"]})
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert report["empty_dates"] == ["20260901", "20260902"]
    assert report["calendar_unverified_dates"] == ["20260903", "20260904"]
    assert {"empty_dates", "calendar_unverified_dates"} <= set(report["flags"])
    assert report["krx_newest_date"] == "2026-09-07"


@pytest.mark.parametrize("free,low", [(999_999_999, True), (1_000_000_000, False)])
def test_status_low_disk_threshold(tmp_path, monkeypatch, free, low):
    monkeypatch.setattr(status.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(2_000_000_000, 0, free))
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert report["disk_free_bytes"] == free and report["low_disk"] is low
    assert ("low_disk" in report["flags"]) is low


def test_status_bad_state_and_naive_clock_fail_clearly(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        status.collect_status(tmp_path, clock=lambda: NOW.replace(tzinfo=None))
    write_json(tmp_path / "disclosures/first_seen/_state.json", [])
    with pytest.raises(ValueError, match="state object"):
        status.collect_status(tmp_path, clock=lambda: NOW)


def test_status_cli_writes_and_prints_same_report_without_reading_secrets(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("this file must never be read", encoding="utf-8")
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert not path.name.startswith(".env")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DART_API_KEY", "not-for-status")
    monkeypatch.setenv("KRX_OPEN_API_KEY", "also-not-for-status")
    getenv = status.os.getenv
    monkeypatch.setattr(status.os, "getenv", lambda name, default=None: getenv(name, default)
                        if name == "HQA_DATA_DIR" else pytest.fail("Status read an unrelated environment variable"))
    status.main([])
    printed = capsys.readouterr().out
    assert json.loads(printed) == json.loads((tmp_path / "ops/status.json").read_text())
    assert "not-for-status" not in printed
    assert not list((tmp_path / "ops").glob(".status.*"))


@pytest.mark.parametrize("name", ["hqa-dart-poller", "hqa-krx-daily", "hqa-collector-status", "hqa-dart-backfill"])
def test_service_units_use_hqa_environment_and_hardening(name):
    text = (ROOT / "deploy/collector/systemd" / f"{name}.service").read_text()
    for directive in ("User=hqa", "Group=hqa", "EnvironmentFile=/etc/hqa/collector.env",
                      "NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true",
                      "ReadWritePaths=/var/lib/hqa/data", "ProtectHome=true", "UMask=0027"):
        assert directive in text.splitlines()


def test_investor_flow_service_and_timer():
    test_service_units_use_hqa_environment_and_hardening("hqa-investor-flow")
    directory = ROOT / "deploy/collector/systemd"
    service = (directory / "hqa-investor-flow.service").read_text()
    assert "TimeoutStartSec=2h" in service.splitlines()
    assert "scripts/ops/investor_flow_daily.py --execute" in service
    timer = (directory / "hqa-investor-flow.timer").read_text()
    for directive in ("OnCalendar=Mon..Fri 19:00 Asia/Seoul", "Persistent=true", "Unit=hqa-investor-flow.service"):
        assert directive in timer.splitlines()


def test_service_schedule_and_poller_restart():
    directory = ROOT / "deploy/collector/systemd"
    dart = (directory / "hqa-dart-poller.service").read_text()
    assert "--loop --execute" in dart and "Restart=always\nRestartSec=30" in dart
    for name, calendar in (("hqa-krx-daily", "Mon..Fri 08:40 Asia/Seoul"),
                           ("hqa-collector-status", "*-*-* 20:00 Asia/Seoul"),
                           ("hqa-dart-backfill", "*-*-* 00:20 Asia/Seoul")):
        timer = (directory / f"{name}.timer").read_text()
        assert f"OnCalendar={calendar}" in timer and "Persistent=true" in timer
        assert f"Unit={name}.service" in timer
    service = (directory / "hqa-dart-backfill.service").read_text()
    for directive in ("Type=oneshot", "TimeoutStartSec=6h", "Nice=10"):
        assert directive in service.splitlines()
    assert "scripts/ops/dart_backfill_daily.py --execute" in service
    assert "RandomizedDelaySec=300" in (directory / "hqa-dart-backfill.timer").read_text().splitlines()


def test_push_excludes_secrets_and_root_data_without_excluding_scripts_data():
    text = (ROOT / "scripts/ops/push_collector_code.sh").read_text()
    assert "--exclude='.env*'" in text and "--exclude='/data/'" in text
    assert "--exclude='data/'" not in text
    assert "$project_root/./scripts/data/" in text


@pytest.mark.parametrize("settings,exit_code", [
    ("", 10),
    ("DART_API_KEY=\nKRX_OPEN_API_KEY=fixture-krx", 10),
    ("DART_API_KEY='   '\nKRX_OPEN_API_KEY=fixture-krx", 10),
    ("DART_API_KEY=fixture-dart\nKRX_OPEN_API_KEY=fixture-krx", 0),
    ("export DART_API_KEY=fixture-dart\nKRX_OPEN_API_KEY=fixture-krx", 2),
    ("DART_API_KEY=fixture-dart\nKRX_OPEN_API_KEY=fixture-krx\nKIS_APP_KEY=forbidden-key", 2),
])
def test_install_environment_gate_offline(tmp_path, settings, exit_code):
    installer = (ROOT / "deploy/collector/install.sh").read_text()
    gate = installer.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    path = tmp_path / "collector.env"
    path.write_text("HQA_DATA_DIR=/var/lib/hqa/data\nTZ=Asia/Seoul\n" + settings + "\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-c", gate, str(path)], capture_output=True, text=True)
    assert result.returncode == exit_code
    assert not any(value in result.stdout + result.stderr
                   for value in ("fixture-dart", "fixture-krx", "forbidden-key"))


def test_install_gates_backfill_timer_and_stops_service_with_other_collectors():
    installer = (ROOT / "deploy/collector/install.sh").read_text()
    disabled, enabled = installer.split("systemctl enable --now", 1)
    assert "if [[ \"$env_check\" -ne 0 ]]; then" in disabled
    assert "hqa-dart-backfill.timer" in disabled.split("systemctl disable --now", 1)[1].splitlines()[0]
    assert "hqa-dart-backfill.service" in disabled.split("systemctl stop", 1)[1].splitlines()[0]
    assert "hqa-dart-backfill.timer" in enabled.splitlines()[0]
    assert 'for unit in /opt/hqa/deploy/collector/systemd/*; do' in installer


@pytest.mark.parametrize("settings,gate_code,enabled", [
    ("", 0, False),
    ("KIS_DATA_APP_KEY=\nKIS_DATA_APP_SECRET=", 0, False),
    ("KIS_DATA_APP_KEY='   '\nKIS_DATA_APP_SECRET='   '", 0, False),
    ("KIS_DATA_APP_KEY=fixture-data-key\nKIS_DATA_APP_SECRET=fixture-data-secret", 20, True),
    ("KIS_DATA_APP_KEY=fixture-data-key", 2, False),
    ("KIS_DATA_APP_SECRET=fixture-data-secret", 2, False),
    ("KIS_DATA_APP_KEY=fixture-data-key\nKIS_DATA_APP_SECRET='   '", 2, False),
    ("KIS_APP_KEY=fixture-forbidden-key", 2, False),
])
def test_install_optional_dedicated_pair_and_timer_gate(tmp_path, settings, gate_code, enabled):
    installer = (ROOT / "deploy/collector/install.sh").read_text()
    gate = installer.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    path = tmp_path / "collector.env"
    path.write_text("HQA_DATA_DIR=/var/lib/hqa/data\nTZ=Asia/Seoul\n"
                    "DART_API_KEY=fixture-dart\nKRX_OPEN_API_KEY=fixture-krx\n" + settings + "\n")
    checked = subprocess.run([sys.executable, "-c", gate, str(path)], capture_output=True, text=True)
    assert checked.returncode == gate_code
    # Exercise the actual shell gate with a local systemctl stub; skip all installation/network work.
    log = tmp_path / "systemctl.log"
    script = 'systemctl() { printf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_LOG"; }\n'
    script += f"env_check={checked.returncode}\n" + installer[installer.index("investor_flow_enabled=false"):]
    result = subprocess.run(["bash", "-c", script], env={**os.environ, "TEST_SYSTEMCTL_LOG": str(log)},
                            capture_output=True, text=True)
    commands = log.read_text().splitlines()
    assert result.returncode == (2 if gate_code == 2 else 0)
    assert ("enable --now hqa-investor-flow.timer" in commands) is enabled
    if not enabled:
        assert any(command.startswith("disable --now ") and "hqa-investor-flow.timer" in command for command in commands)
        assert any(command.startswith("stop ") and "hqa-investor-flow.service" in command for command in commands)
    if gate_code == 0:
        assert any(command.startswith("enable --now ") and "hqa-krx-daily.timer" in command for command in commands)
    public = checked.stdout + checked.stderr + result.stdout + result.stderr
    assert all(value not in public for value in ("fixture-data-key", "fixture-data-secret", "fixture-forbidden-key"))


def test_install_missing_base_keys_disables_investor_flow_even_with_dedicated_pair(tmp_path):
    installer = (ROOT / "deploy/collector/install.sh").read_text()
    gate = installer.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    path = tmp_path / "collector.env"
    path.write_text("HQA_DATA_DIR=/var/lib/hqa/data\nKIS_DATA_APP_KEY=fixture-data-key\n"
                    "KIS_DATA_APP_SECRET=fixture-data-secret\n")
    result = subprocess.run([sys.executable, "-c", gate, str(path)], capture_output=True, text=True)
    assert result.returncode == 10


def test_status_investor_flow_reads_local_dates_and_last_failure_list_only(tmp_path, monkeypatch):
    directory = tmp_path / "market/investor_flow/2026"
    directory.mkdir(parents=True)
    (directory / "20260904.jsonl").touch()
    (directory / "20260908.jsonl").touch()
    write_json(directory.parent / "_last_run.json", {"failures": [
        {"stock_code": "005930", "reason": "empty_output"}, {"stock_code": "000660", "reason": "rate_limited"}]})
    write_json(tmp_path / ".kis_tokens/never-read.json", {"access_token": "fixture-private-token"})
    read_text = Path.read_text

    def local_read(path, *args, **kwargs):
        assert ".kis_tokens" not in path.parts
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", local_read)
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert report["investor_flow"] == {"latest_stored_date": "2026-09-08", "last_run_failures_count": 2}
    assert "fixture-private-token" not in json.dumps(report)


def test_status_investor_flow_distinguishes_no_run_from_zero_failures(tmp_path):
    assert status.collect_status(tmp_path, clock=lambda: NOW)["investor_flow"] == {
        "latest_stored_date": None, "last_run_failures_count": None}
    write_json(tmp_path / "market/investor_flow/_last_run.json", {"failures": []})
    assert status.collect_status(tmp_path, clock=lambda: NOW)["investor_flow"] == {
        "latest_stored_date": None, "last_run_failures_count": 0}


def test_investor_flow_cli_dry_run_uses_krx_and_reads_no_secrets(tmp_path, monkeypatch, capsys):
    path = tmp_path / "market/krx_daily/2026/20260907.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps({"stock_code": code}) + "\n"
                            for code in ("005930", "000660", "012450", "0009K0")))
    getenv = flow_daily.os.getenv
    monkeypatch.setattr(flow_daily.os, "getenv", lambda name, default=None: getenv(name, default)
                        if name == "HQA_DATA_DIR" else pytest.fail("Dry run read a secret"))
    flow_daily.main(["--data-dir", str(tmp_path), "--max-stocks", "3"])
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] and report["stocks_planned"] == 3
    assert report["stocks_attempted"] == report["rows_saved"] == 0
    assert not (tmp_path / "market/investor_flow").exists() and not (tmp_path / ".kis_tokens").exists()


def test_investor_flow_cli_manual_subset_and_dedicated_environment(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("KIS_DATA_APP_KEY", "fixture-data-key")
    monkeypatch.setenv("KIS_DATA_APP_SECRET", "fixture-data-secret")
    monkeypatch.setenv("KIS_APP_KEY", "must-not-use-trading-key")

    class Collector:
        def __init__(self, app_key, app_secret, *, data_dir):
            assert (app_key, app_secret, data_dir) == ("fixture-data-key", "fixture-data-secret", tmp_path)

        def collect_day(self, codes, *, execute):
            calls.append((codes, execute))
            return {"stocks_attempted": 2, "stocks_ok": 1, "stocks_failed": 1, "rows_saved": 30,
                    "failures": [{"stock_code": "000660", "reason": "empty_output"}]}

    monkeypatch.setattr(flow_daily, "KisInvestorFlowCollector", Collector)
    monkeypatch.setattr(flow_daily, "load_stock_codes", lambda *a: pytest.fail("Manual subset loaded the universe"))
    flow_daily.main(["--execute", "--data-dir", str(tmp_path), "--codes", "005930, 005930, 000660,012450", "--max-stocks", "2"])
    assert calls == [(["005930", "000660"], True)]
    output = capsys.readouterr().out
    assert json.loads(output)["stocks_failed"] == 1
    assert "fixture-data-key" not in output and "fixture-data-secret" not in output


@pytest.mark.parametrize("args", [[], ["--codes", ""], ["--codes", "00593"],
                                  ["--codes", "005930", "--max-stocks", "0"]])
def test_investor_flow_cli_invalid_config_fails_before_requests(tmp_path, capsys, args):
    with pytest.raises(SystemExit) as exited:
        flow_daily.main(["--data-dir", str(tmp_path), *args])
    assert exited.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("now,end", [
    ("2026-10-04T14:59:59+00:00", "2026-10-03"),
    ("2026-10-04T15:00:00+00:00", "2026-10-04"),
    ("2026-01-01T00:20:00+09:00", "2025-12-31"),
])
def test_dart_daily_range_and_priority_order(tmp_path, monkeypatch, now, end):
    calls = []

    def planned(start, stop, **kwargs):
        calls.append((start, stop, kwargs))
        return {"status": "dry_run", "pending_details_by_category": {"contract": 3}}

    monkeypatch.setattr(dart_daily, "backfill", planned)
    report = dart_daily.daily_backfill(tmp_path, clock=lambda: datetime.fromisoformat(now))
    assert report["to_date"] == end
    assert [(start, stop) for start, stop, _ in calls] == [("2023-01-01", end)] * 3
    assert [kwargs["stage"] for _, _, kwargs in calls] == ["list", "details", "details"]
    assert [kwargs["categories"] for _, _, kwargs in calls] == [None, dart_daily.DEFAULT_PRIORITY_CATEGORIES, None]
    assert all(kwargs["data_dir"] == tmp_path and kwargs["max_requests"] == 17_000 for _, _, kwargs in calls)
    assert all(not kwargs["execute"] for _, _, kwargs in calls)
    assert report["steps"]["priority_details"]["pending_details_by_category"] == {"contract": 3}


def test_dart_daily_dry_run_no_requests_secrets_or_writes(tmp_path, monkeypatch, capsys):
    getenv = dart_daily.os.getenv
    monkeypatch.setattr(dart_daily.os, "getenv", lambda name, default=None: getenv(name, default)
                        if name == "HQA_DATA_DIR" else pytest.fail("Dry run read a secret"))
    directory = tmp_path / "new-data"
    dart_daily.main(["--data-dir", str(directory), "--priority-categories", "earnings, contract"])
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run" and report["requests_made"] == 0
    assert report["steps"]["priority_details"]["categories"] == ["earnings", "contract"]
    assert all(part["status"] == "dry_run" and part["requests_made"] == 0 for part in report["steps"].values())
    assert not directory.exists()


def dart_daily_fixture(data_dir, monkeypatch, payloads, *, used=0, provider_limited=False):
    """Complete old listings locally, then inject only offline provider responses."""
    directory = data_dir / "disclosures/dart_full"
    days = []
    day = date(2023, 1, 1)
    while day < NOW.date() - timedelta(days=1):
        saved_day = day.strftime("%Y%m%d")
        path = directory / "list" / saved_day[:4] / f"{saved_day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        days.append(saved_day)
        day += timedelta(days=1)
    write_json(directory / "_state.json", {"completed_listing_days": days,
               "completed_detail_rcept_nos": [], "skipped_missing_stock_code_rcept_nos": []})
    write_json(directory / "_quota.json", {"days": {NOW.date().isoformat():
               {"requests": used, "provider_limited": provider_limited}}})
    calls = []
    remaining = iter(payloads)

    def get(url, *, params, timeout):
        calls.append((url, params))
        payload = next(remaining)
        return SimpleNamespace(json=lambda: payload, content=payload if isinstance(payload, bytes) else b"",
                               raise_for_status=lambda: None)

    def offline_backfill(*args, **kwargs):
        return dart_backfill.backfill(*args, **kwargs, session=SimpleNamespace(get=get), sleeper=lambda _: None)

    monkeypatch.setattr(dart_daily, "backfill", offline_backfill)
    monkeypatch.setenv("DART_API_KEY", "fixture-private-key")
    return calls, directory


def dart_daily_payloads():
    day = (NOW.date() - timedelta(days=1)).strftime("%Y%m%d")
    rows = [{"rcept_no": f"{day}{number:06d}", "rcept_dt": day, "report_nm": title,
             "corp_code": "00126380", "corp_name": "시험기업", "stock_code": "005930",
             "corp_cls": "Y", "flr_nm": "시험기업", "rm": ""}
            for number, title in enumerate(["단일판매ㆍ공급계약체결", "공급계약체결", "실적발표"], 1)]
    listing = {"status": "000", "page_no": 1, "page_count": 100, "total_count": 3,
               "total_page": 1, "list": rows}
    document = b"<result><status>013</status></result>"
    return [listing, {"status": "013"}, document, document, document]


@pytest.mark.parametrize("remaining,stopped_step", [(0, "listing"), (3, "priority_details"),
                                                   (4, "all_details"), (5, None)])
def test_dart_daily_persisted_budget_shared_across_steps_and_reruns(tmp_path, monkeypatch, capsys,
                                                                  remaining, stopped_step):
    calls, directory = dart_daily_fixture(tmp_path, monkeypatch, dart_daily_payloads(), used=17_000 - remaining)
    dart_daily.main(["--data-dir", str(tmp_path), "--execute"])
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == ("quota_reached" if stopped_step else "ok")
    assert len(calls) == report["requests_made"] == remaining
    assert sum(part["requests_made"] for part in report["steps"].values()) == remaining
    steps = list(report["steps"])
    if stopped_step:
        assert report["steps"][stopped_step]["status"] == "quota_reached"
        assert all(report["steps"][name]["status"] == "not_run" for name in steps[steps.index(stopped_step) + 1:])
    else:
        assert [report["steps"][name]["requests_made"] for name in steps] == [2, 2, 1]
    counter = json.loads((directory / "_quota.json").read_text())["days"][NOW.date().isoformat()]
    assert counter == {"requests": 17_000, "provider_limited": False}
    dart_daily.main(["--data-dir", str(tmp_path), "--execute"])
    assert json.loads(capsys.readouterr().out)["requests_made"] == 0
    assert len(calls) == remaining


@pytest.mark.parametrize("step,index", [("listing", 0), ("priority_details", 2), ("all_details", 4)])
@pytest.mark.parametrize("provider_status", ["020", "010"])
def test_dart_daily_provider_quota_and_errors_exit_with_one_safe_summary(tmp_path, monkeypatch, capsys,
                                                                      step, index, provider_status):
    payloads = dart_daily_payloads()[:index]
    error = {"status": provider_status, "message": "fixture-private-key"}
    payloads.append(error if index == 0 else
                    f"<result><status>{provider_status}</status><message>fixture-private-key</message></result>".encode())
    calls, directory = dart_daily_fixture(tmp_path, monkeypatch, payloads)
    args = ["--data-dir", str(tmp_path), "--execute"]
    if provider_status == "020":
        dart_daily.main(args)
    else:
        with pytest.raises(SystemExit) as exited:
            dart_daily.main(args)
        assert exited.value.code == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert "fixture-private-key" not in output.out + output.err
    assert report["status"] == report["steps"][step]["status"] == ("quota_reached" if provider_status == "020" else "error")
    assert len(calls) == report["requests_made"] == index + 1
    counter = json.loads((directory / "_quota.json").read_text())["days"][NOW.date().isoformat()]
    assert counter["provider_limited"] is (provider_status == "020")


@pytest.mark.parametrize("args", [["--max-requests", "0"], ["--priority-categories", "unknown"],
                                 ["--priority-categories", ""]])
def test_dart_daily_invalid_options_fail_before_work(tmp_path, capsys, args):
    with pytest.raises(SystemExit) as exited:
        dart_daily.main(["--data-dir", str(tmp_path), *args])
    assert exited.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limited", [False, True])
def test_status_backfill_uses_checkpoints_and_kst_quota_only(tmp_path, monkeypatch, limited):
    directory = tmp_path / "disclosures/dart_full"
    write_json(directory / "_state.json", {"completed_listing_days": ["20230101", "20230102"],
               "completed_detail_rcept_nos": ["20230101000001"],
               "skipped_missing_stock_code_rcept_nos": ["20230101000002", "20230102000003"]})
    write_json(directory / "_quota.json", {"days": {
        "2026-09-07": {"requests": 17_000, "provider_limited": True},
        "2026-09-08": {"requests": 1234, "provider_limited": limited}}})
    glob = Path.glob

    def guarded_glob(path, pattern):
        assert "dart_full" not in path.parts
        return glob(path, pattern)

    monkeypatch.setattr(Path, "glob", guarded_glob)
    report = status.collect_status(tmp_path, clock=lambda: datetime.fromisoformat("2026-09-07T15:20:00+00:00"))
    assert report["dart_backfill"] == {"completed_listing_days": 2, "completed_detail_receipts": 1,
        "skipped_receipts": 2, "requests_today": 1234, "provider_limited": limited}


@pytest.mark.parametrize("previous_day", [False, True])
def test_status_backfill_without_today_quota_or_progress(tmp_path, previous_day):
    if previous_day:
        write_json(tmp_path / "disclosures/dart_full/_quota.json", {"days": {
            "2026-09-07": {"requests": 17_000, "provider_limited": True}}})
    report = status.collect_status(tmp_path, clock=lambda: NOW)
    assert report["dart_backfill"] == {"completed_listing_days": 0, "completed_detail_receipts": 0,
        "skipped_receipts": 0, "requests_today": 0, "provider_limited": False}


@pytest.mark.parametrize("script", ["push_collector_code.sh", "pull_collector_data.sh"])
@pytest.mark.parametrize("execute", [False, True])
def test_transfer_scripts_default_to_dry_run_and_quote_paths(tmp_path, script, execute):
    project = tmp_path / "project with spaces"
    script_path = project / "scripts" / "ops" / script
    script_path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/ops" / script, script_path)
    python = project / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$HQA_DATA_DIR"\n', encoding="utf-8")
    python.chmod(0o755)
    commands = tmp_path / "commands"
    commands.mkdir()
    rsync = commands / "rsync"
    rsync.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                     'with open(os.environ["TEST_RSYNC_LOG"], "w") as handle:\n'
                     '    json.dump(sys.argv[1:], handle)\n', encoding="utf-8")
    rsync.chmod(0o755)
    key = tmp_path / 'ssh key with "quotes"'
    key.touch()
    log = tmp_path / "rsync.json"
    data_dir = tmp_path / "local data"
    env = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}", "HQA_DATA_DIR": str(data_dir),
           "TEST_RSYNC_LOG": str(log), "PYTHONDONTWRITEBYTECODE": "1"}
    command = ["bash", str(script_path), "operator@192.0.2.1", str(key)]
    if execute:
        command.append("--execute")
    subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    arguments = json.loads(log.read_text())
    assert ("-n" in arguments) is not execute
    assert "--delete" not in arguments
    assert '--rsync-path=sudo -n rsync' in arguments
    ssh = arguments[arguments.index("-e") + 1]
    assert f'-i "{str(key).replace(chr(34), chr(34) * 2)}"' in ssh
    if script.startswith("push"):
        assert f"{project}/./src/" in arguments
        assert arguments[-1] == "operator@192.0.2.1:/opt/hqa/"
    else:
        assert arguments[-1] == str(data_dir) + "/"
        assert arguments[-4:-1] == [f"operator@192.0.2.1:/var/lib/hqa/data/{name}"
                                    for name in ("disclosures", "market", "ops")]
        assert data_dir.exists() is execute
