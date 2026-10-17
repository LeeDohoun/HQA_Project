"""FW001 collector-completion triggering and offline deployment preparation."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.ops import collector_manifest, fw001_daily, krx_catchup
from src.forward import fw001 as fw
from src.ingestion import krx_chart, krx_market
from src.ingestion.storage import write_rows
from test_forward_fw001 import data, DAY, entry_data, publication_clock
from test_krx_market import Session, raw, response

ROOT = Path(__file__).resolve().parents[1]


def test_KRX_success_triggers_independent_FW001_before_shadow_with_evening_recovery():
    directory = ROOT / "deploy/collector/systemd"
    krx = (directory / "hqa-krx-daily.service").read_text()
    shadow = (directory / "hqa-shadow-daily.service").read_text()
    recorder = (directory / "hqa-fw001-daily.service").read_text()
    recovery = (directory / "hqa-fw001-daily.timer").read_text()
    timer = (directory / "hqa-krx-daily.timer").read_text()
    assert "OnSuccess=hqa-fw001-daily.service hqa-shadow-daily.service" in krx
    assert "After=hqa-krx-daily.service hqa-fw001-daily.service" in shadow
    assert "MemoryMax=800M" in shadow and "PrivateNetwork=true" in shadow
    assert "fw001_daily.py" not in shadow
    assert "TimeoutStartSec=15min" in recorder and "MemoryMax=800M" in recorder
    assert "PrivateNetwork=true" in recorder and "ConditionPathExists=/etc/hqa/fw001-activated" in recorder
    assert "Before=hqa-shadow-daily.service" in recorder
    assert "krx_catchup.py --execute" in krx
    assert "OnCalendar=*-*-* 08:15 Asia/Seoul" in recovery
    assert "OnCalendar=2027-11-15 00:00 Asia/Seoul" in recovery
    for calendar in ("Mon..Fri 08:40", "Sat 08:40", "*-*-* 18:30"):
        assert f"OnCalendar={calendar} Asia/Seoul" in timer
    assert "16:10" not in timer
    assert "Persistent=true" in timer


@pytest.mark.parametrize("today,start,end", [(date(2026, 10, 31), "20261029", "20261030"),
    (date(2026, 11, 1), "20261029", "20261031"), (date(2027, 1, 1), "20261230", "20261231")])
def test_catchup_runs_on_weekend_holiday_but_never_requests_today(tmp_path, today, start, end):
    write_rows(tmp_path / f"market/krx_daily/{start[:4]}/{start}.jsonl", [{"stock_code": "000010"}])
    assert krx_catchup.catchup_range(tmp_path, today) == (start, end)


@pytest.mark.parametrize("stamp,stored,end,requested,status", [
    ("2026-10-30T08:40:00+09:00", "20261028", "20261029", "20261029", "verified"),
    ("2026-10-30T16:10:00+09:00", "20261028", "20261029", "20261029", "verified"),
    ("2026-10-30T18:30:00+09:00", "20261028", "20261029", "20261029", "verified"),
    ("2026-10-31T08:40:00+09:00", "20261029", "20261030", "20261030", "verified"),
    ("2026-11-01T18:30:00+09:00", "20261029", "20261031", "20261030", "verified"),
    ("2026-11-18T08:40:00+09:00", "20261116", "20261117", "20261117", "verified"),
    ("2026-11-19T08:40:00+09:00", "20261117", "20261118", "20261118", "verified"),
    ("2026-11-19T18:30:00+09:00", "20261117", "20261118", "20261118", "verified"),
    ("2026-11-20T08:40:00+09:00", "20261118", "20261119", "20261119", "unverified_special_session"),
])
def test_catchup_actual_collection_path_only_requests_previous_days(tmp_path, monkeypatch, capsys,
                                                                  stamp, stored, end, requested, status):
    write_rows(tmp_path / f"market/krx_daily/2026/{stored}.jsonl", [{"stock_code": "000010"}])
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime.fromisoformat(stamp)
    monkeypatch.setattr(krx_catchup, "datetime", Clock)
    monkeypatch.setattr(krx_chart, "_now", lambda: datetime.fromisoformat(stamp))
    monkeypatch.setenv("KRX_OPEN_API_KEY", "fixture-key")
    session = Session(response(raw(day=requested)), response(raw("035720", day=requested)))
    session.close = lambda: None
    monkeypatch.setattr(krx_market.requests, "Session", lambda: session)
    krx_catchup.main(["--data-dir", str(tmp_path), "--execute"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["to_date"] == end and summary["saved_rows"] == 2
    assert [call[1]["params"]["basDd"] for call in session.calls] == [requested, requested]
    rows = krx_market.load_universe(requested, tmp_path)
    assert {row["market"] for row in rows} == {"KOSPI", "KOSDAQ"}
    assert {row["calendar_status"] for row in rows} == {status}
    if status != "verified":
        with pytest.raises(ValueError, match="calendar_special_session_coverage_unverified"):
            fw.verified_day(tmp_path, requested)


def test_restart_finishes_only_missing_execution_scenarios(data, monkeypatch):
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 10, 31, 8, 40, tzinfo=fw.KST))
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, 40, tzinfo=fw.KST))
    fw.execution(decision, data, multiplier=1.0, execute=True)
    path = fw.path_for(data, "executions", "2026-10", 1.0)
    original = path.read_bytes()
    report = fw.daily(data, execute=True)
    assert not report["missing"] and len(report["executions"]) == 3
    assert path.read_bytes() == original
    reports = [fw.load_json(fw.path_for(data, "executions", "2026-10", m)) for m in fw.hf001.MULTIPLIERS]
    assert len({fw.digest(row["input_hashes"]) for row in reports}) == 1
    assert len({row["decision_hash"] for row in reports}) == 1


def test_sync_supplement_default_dry_run_keeps_state_and_has_no_delete(tmp_path):
    supplement = tmp_path / "PC data/disclosures/dart_full/list_supplement"
    supplement.mkdir(parents=True)
    (supplement / "_state.json").write_text("{}")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "rsync"
    fake.write_text("#!/usr/bin/env python3\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n")
    fake.chmod(0o755)
    result = subprocess.run(["bash", str(ROOT / "scripts/ops/push_fw001_supplement.sh"),
        str(tmp_path / "PC data"), "operator@192.0.2.1"], capture_output=True, text=True, check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"})
    args = json.loads(result.stdout)
    assert "-n" in args and "--delete" not in args and "--chown=hqa:hqa" in args
    assert args[-2] == str(supplement) + "/"
    assert args[-1].endswith("/data/disclosures/dart_full/list_supplement/")
    assert not any("exclude=_state" in arg for arg in args)


def test_deployed_module_set_and_recorder_dry_run_from_sealed_manifest_without_git(data, tmp_path):
    server = tmp_path / "server"
    server.mkdir()
    manifest = json.loads((ROOT / collector_manifest.DESTINATION).read_text())
    for name in manifest["files"]:
        source, target = ROOT / name, server / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # The manifest itself deliberately excludes its own recursive hash.
    (server / collector_manifest.DESTINATION).write_text(json.dumps(manifest))
    code = """
import sys,socket,json,importlib,os
from pathlib import Path
import pandas as pd
from datetime import datetime
sys.path.insert(0,sys.argv[1])
def guard(event,args):
 if event=='open' and isinstance(args[0],str) and args[0].split('/')[-1] in ('.env','.env-ai'):
  raise AssertionError('env read')
sys.addaudithook(guard)
def deny(*a,**k): raise AssertionError('network')
socket.socket.connect=deny
socket.getaddrinfo=deny
for module in ('backtesting.append_only','backtesting.cost_model','backtesting.experiment_registry',
               'backtesting.experiments.av001','backtesting.experiments.hf001','backtesting.experiments.hc003',
               'src.runner.trading_calendar','src.forward.fw001','src.forward.hegemony_shadow',
               'scripts.ops.fw001_daily','scripts.ops.shadow_daily'):
 importlib.import_module(module)
from src.forward import fw001 as fw
from scripts.ops import fw001_daily
fw.now=lambda:datetime(2026,10,31,8,40,tzinfo=fw.KST)
link=os.link
def publish(source,target):
 link(source,target)
 target=Path(target)
 if target.name=='complete.json':
  prepared=pd.Timestamp(fw.load_json(target)['prepared_at']).value
  os.utime(target,ns=(prepared,prepared))
  stamp=pd.Timestamp(fw.now()).value
  os.utime(target.parent,ns=(stamp,stamp))
os.link=publish
assert fw001_daily.main(['--data-dir',sys.argv[2]])==0
assert not (fw.Path(sys.argv[2])/fw.RECORD_DIR).exists()
result=fw.decide('2026-10-30',sys.argv[2],execute=True)
assert result['label']=='forward'
assert result['preregistration_commit']=='23e2051'
assert result['rules']['av_all']['financial_rule']=='S'
print(json.dumps({'label':result['label']}))
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(server), str(data)], cwd=server,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1"},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert json.loads(lines[0])["status"] == "dry_run"
    assert json.loads(lines[-1])["label"] == "forward"
    assert not (server / ".git").exists()


def test_sealing_manifest_covers_actual_transfer_modules():
    manifest = collector_manifest.manifest()
    files = manifest["files"]
    for name in ("hf001", "hc003", "av001", "ordering"):
        assert f"backtesting/experiments/{name}.py" in files
    for name in ("dart_listing", "dart_financial_date_rules", "dart_quarterly"):
        assert f"src/ingestion/{name}.py" in files
    assert "backtesting/cost_model.py" in files
    assert "backtesting/append_only.py" in files
    assert "src/runner/trading_calendar.py" in files
    assert "research/experiments/FW001_forward_replication/preregistration.md" in files
    assert all(len(checksum) == 64 for checksum in files.values())
    assert not any(name.startswith(("tests/", "data/", "venv/", ".env")) for name in files)


def test_daily_and_rehearsal_CLI_default_is_read_only(data, monkeypatch, capsys):
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 10, 31, 8, 40, tzinfo=fw.KST))
    assert fw001_daily.main(["--data-dir", str(data)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run" and not (data / fw.RECORD_DIR).exists()
    with pytest.raises(ValueError, match="strictly read-only"):
        fw.decide(DAY, data, rehearsal=True, execute=True)


@pytest.mark.parametrize("entry", ["decision", "execution", "daily", "evaluation"])
def test_manifest_hash_verification_precedes_every_run(tmp_path, monkeypatch, entry):
    def changed(**kwargs):
        raise ValueError("collector manifest differs: src/forward/fw001.py")
    monkeypatch.setattr(fw, "verify_manifest", changed)
    with pytest.raises(ValueError, match="manifest differs"):
        if entry == "decision":
            fw.decide(DAY, tmp_path)
        elif entry == "execution":
            fw.execution({}, tmp_path, multiplier=1.0)
        elif entry == "daily":
            fw.daily(tmp_path)
        else:
            from backtesting.experiments import fw001 as evaluator
            evaluator.evaluate(tmp_path)
    assert not list(tmp_path.iterdir())


def test_manifest_amendment_links_exact_change_and_unlisted_edits_fail(tmp_path):
    server = tmp_path / "seal-test"
    for path in collector_manifest.files():
        target = server / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    destination = server / collector_manifest.DESTINATION
    destination.write_text(json.dumps(collector_manifest.manifest()))
    old_hash = collector_manifest.checksum(destination)
    source = server / "src/forward/fw001.py"
    source.write_text(source.read_text() + "\n# documented fixture bug fix\n")
    with pytest.raises(ValueError, match="manifest differs"):
        collector_manifest.verify(server)
    # PC-only implementation files are also part of the new seal.
    for name in collector_manifest.manifest()["implementation_files"]:
        target = server / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    saved = collector_manifest.reseal(server, reason="fixture bug fix")
    assert saved["previous_manifest"]["sha256"] == old_hash
    collector_manifest.verify(server, implementation=True)
    amendment_path = server / saved["change_manifest"]["path"]
    amendment_path.write_text("{}")
    with pytest.raises(ValueError, match="manifest differs"):
        collector_manifest.verify(server)
