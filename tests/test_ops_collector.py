import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from scripts.ops import collector_status as status
from scripts.ops import krx_catchup as catchup
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


@pytest.mark.parametrize("name", ["hqa-dart-poller", "hqa-krx-daily", "hqa-collector-status"])
def test_service_units_use_hqa_environment_and_hardening(name):
    text = (ROOT / "deploy/collector/systemd" / f"{name}.service").read_text()
    for directive in ("User=hqa", "Group=hqa", "EnvironmentFile=/etc/hqa/collector.env",
                      "NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true",
                      "ReadWritePaths=/var/lib/hqa/data"):
        assert directive in text.splitlines()


def test_service_schedule_and_poller_restart():
    directory = ROOT / "deploy/collector/systemd"
    dart = (directory / "hqa-dart-poller.service").read_text()
    assert "--loop --execute" in dart and "Restart=always\nRestartSec=30" in dart
    for name, calendar in (("hqa-krx-daily", "Mon..Fri 08:40 Asia/Seoul"),
                           ("hqa-collector-status", "*-*-* 20:00 Asia/Seoul")):
        timer = (directory / f"{name}.timer").read_text()
        assert f"OnCalendar={calendar}" in timer and "Persistent=true" in timer
        assert f"Unit={name}.service" in timer


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
