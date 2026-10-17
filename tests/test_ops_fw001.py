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
from src.ingestion.storage import write_rows
from test_forward_fw001 import data, DAY, entry_data

ROOT = Path(__file__).resolve().parents[1]


def test_KRX_success_triggers_shadow_after_completion_with_existing_limits():
    directory = ROOT / "deploy/collector/systemd"
    krx = (directory / "hqa-krx-daily.service").read_text()
    shadow = (directory / "hqa-shadow-daily.service").read_text()
    timer = (directory / "hqa-krx-daily.timer").read_text()
    assert "OnSuccess=hqa-shadow-daily.service" in krx
    assert "After=hqa-krx-daily.service" in shadow
    assert "MemoryMax=800M" in shadow and "PrivateNetwork=true" in shadow
    assert "TimeoutStartSec=10min" in shadow  # Real decision exceeds systemd's usual 90s default.
    assert shadow.index("scripts/ops/fw001_daily.py") < shadow.index("scripts/ops/shadow_daily.py")
    for calendar in ("Mon..Fri 08:40", "Sat 08:40", "*-*-* 18:30"):
        assert f"OnCalendar={calendar} Asia/Seoul" in timer
    assert "Persistent=true" in timer


@pytest.mark.parametrize("today,start,end", [(date(2026, 10, 31), "20261029", "20261030"),
    (date(2026, 11, 1), "20261029", "20261031"), (date(2027, 1, 1), "20261230", "20261231")])
def test_catchup_runs_on_weekend_holiday_but_never_requests_today(tmp_path, today, start, end):
    write_rows(tmp_path / f"market/krx_daily/{start[:4]}/{start}.jsonl", [{"stock_code": "000010"}])
    assert krx_catchup.catchup_range(tmp_path, today) == (start, end)


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


def test_deployed_FW001_imports_and_records_offline_without_git(data, tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    for source in collector_manifest.files():
        target = server / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # The manifest itself deliberately excludes its own recursive hash.
    manifest = collector_manifest.manifest()
    (server / collector_manifest.DESTINATION).write_text(json.dumps(manifest))
    code = """
import sys,socket,json
from datetime import datetime
sys.path.insert(0,sys.argv[1])
def guard(event,args):
 if event=='open' and isinstance(args[0],str) and args[0].split('/')[-1] in ('.env','.env-ai'):
  raise AssertionError('env read')
sys.addaudithook(guard)
def deny(*a,**k): raise AssertionError('network')
socket.socket.connect=deny
socket.getaddrinfo=deny
from src.forward import fw001 as fw
fw.now=lambda:datetime(2026,10,31,8,40,tzinfo=fw.KST)
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
    assert json.loads(result.stdout)["label"] == "forward"
    assert not (server / ".git").exists()


def test_sealing_manifest_covers_actual_transfer_modules():
    manifest = collector_manifest.manifest()
    files = manifest["files"]
    for name in ("hf001", "hc003", "av001", "ordering"):
        assert f"backtesting/experiments/{name}.py" in files
    for name in ("dart_listing", "dart_financial_date_rules", "dart_quarterly"):
        assert f"src/ingestion/{name}.py" in files
    assert "backtesting/cost_model.py" in files
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
