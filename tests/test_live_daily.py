from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from src.forward import fw001
from src.live import runtime, shadow
from test_live_fixtures import REPO, decision_record, make_repo, write_decision, write_flat_days

KST = shadow.KST
A, B, C = "000010", "000020", "000030"
HF_PASS = [("2026-10-17T12:11:15+00:00", "HF001_hegemony_filters", "f_both", "pass"),
           ("2026-10-18T00:00:00+00:00", "HF001_hegemony_filters", "holdout_f_both", "pass"),
           ("2026-10-17T11:54:26+00:00", "AV001_avoidance_portfolio", "av_all", "pass")]
DEPLOY = REPO / "deploy/strategy-runtime"


def _ctx(tmp_path, **seal):
    root = make_repo(tmp_path, rows=HF_PASS)
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=[A, B, C], av_all=[A, B]), **seal)
    write_flat_days(data, "2026-10-30", "2026-11-30", [A, B, C])
    return runtime.load_context(root, data)


def _steps(summary):
    return {s["step"]: s for s in summary["steps"]}


def at(day, hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=KST)


# ---------------------------------------------------------------- daily


def test_non_session_day_only_checks_and_exits_zero(tmp_path):
    ctx = _ctx(tmp_path)
    summary, code = runtime.daily_command(ctx, session="2026-10-31", execute=True, now=at("2026-10-31", "18:30"),
                                          env={})
    steps = _steps(summary)
    assert code == 0 and summary["isSession"] is False and summary["when"] == "evening"
    assert steps["shadow_fill"]["status"] == "skipped" and "plans" not in steps
    assert not (ctx.data_dir / "live/hf001_f_both").exists()
    assert (ctx.data_dir / "live/runs/20261031_evening.json").is_file()


def test_morning_seal_check_final(tmp_path):
    ctx = _ctx(tmp_path)
    summary, code = runtime.daily_command(ctx, session="2026-11-02", when="morning", now=at("2026-11-02", "09:05"),
                                          env={})
    rows = _steps(summary)["decision_seals"]["result"]["strategies"]
    assert code == 0 and {r["state"] for r in rows} == {"final"} and len(rows) == 2
    assert _steps(summary)["backend_checks"]["result"] == {"configured": False, "note": "backend_not_configured"}
    assert summary["dryRun"] and not (ctx.data_dir / "live").exists()


@pytest.mark.parametrize("case, state", [("missing", "decision_not_sealed"), ("late", "decision_seal_late"),
                                         ("invalid", "decision_seal_invalid"), ("absent", "decision_missing")])
def test_morning_seal_check_not_final_fails_and_alerts(tmp_path, case, state):
    if case == "missing":
        ctx = _ctx(tmp_path, seal=False)
    elif case == "late":
        ctx = _ctx(tmp_path, sealed_at="2026-11-02T08:58:59.500000+09:00")
    else:
        ctx = _ctx(tmp_path)
        marker = fw001.marker_path(ctx.data_dir, "decisions", "2026-10")
        if case == "invalid":
            (marker.parent / "extra").write_text("x", encoding="utf-8")
        else:
            shutil.rmtree(marker.parent)
            (ctx.data_dir / "forward/fw001/decisions/202610.json").unlink()
    summary, code = runtime.daily_command(ctx, session="2026-11-02", when="morning", execute=True,
                                          now=at("2026-11-02", "09:05"), env={})
    step = _steps(summary)["decision_seals"]
    assert code == 1 and step["status"] == "failed" and step["error"] == f"decision_not_final:2026-10:{state}"
    assert {r["state"] for r in step["result"]["strategies"]} == {state}
    if case == "invalid":
        assert "FW001 invalid completion" in step["result"]["strategies"][0]["error"]
    sent = _steps(summary)["alerts"]["result"]["alerts"]
    assert [a["code"] for a in sent].count("decision_not_final") == 2
    assert all(a["code"] != "daily_step_failed" for a in sent)


def test_evening_run_writes_ledger_plans_status_alerts_and_run_summary(tmp_path):
    ctx = _ctx(tmp_path)
    now = at("2026-10-30", "18:30")
    summary, code = runtime.daily_command(ctx, session="2026-10-30", execute=True, now=now, env={})
    steps = _steps(summary)
    assert code == 0, summary
    assert steps["shadow_fill"]["status"] == "ok"
    assert shadow.ledger_path(ctx.data_dir, "hf001_f_both", "2026-10-30").is_file()
    plans = steps["plans"]["result"]
    assert plans["sessionDate"] == "2026-11-02"
    assert {(p["strategyId"], p["phase"]) for p in plans["plans"]} == {("hf001_f_both", "OPEN"),
                                                                      ("av001_av_all", "OPEN")}
    assert runtime.plan_path(ctx.data_dir, "av001_av_all", "2026-11-02", "OPEN").is_file()
    assert steps["status"]["status"] == "ok"
    assert steps["publish_status"]["status"] == "skipped" and steps["publish_status"]["result"]["note"] == "backend_not_configured"
    sent = steps["alerts"]["result"]["alerts"]
    assert ("info", "evening_summary") in {(a["severity"], a["code"]) for a in sent}
    assert ("warning", "stage_blocked") in {(a["severity"], a["code"]) for a in sent}  # AV001 has no holdout pass
    assert (ctx.data_dir / "live/alerts/20261030.jsonl").is_file()
    first = ctx.data_dir / "live/runs/20261030_evening.json"
    original = first.read_text(encoding="utf-8")
    assert json.loads(original)["runId"] == summary["runId"]
    again, code = runtime.daily_command(ctx, session="2026-10-30", execute=True, now=at("2026-10-30", "18:45"),
                                        env={})
    assert code == 0 and first.read_text(encoding="utf-8") == original  # immutable per run
    runs = sorted(p.name for p in (ctx.data_dir / "live/runs").iterdir())
    assert len(runs) == 2 and runs[1].startswith("20261030_evening_184500-")
    stage_alerts = [a for a in _steps(again)["alerts"]["result"]["alerts"] if a["code"] == "stage_blocked"]
    assert stage_alerts == []  # unchanged stage is not re-alerted


def test_default_phase_by_time(tmp_path):
    ctx = _ctx(tmp_path)
    assert runtime.daily_command(ctx, session="2026-11-03", now=at("2026-11-03", "16:29"), env={})[0]["when"] == \
        "morning"
    assert runtime.daily_command(ctx, session="2026-11-03", now=at("2026-11-03", "16:30"), env={})[0]["when"] == \
        "evening"


def test_step_failure_does_not_stop_independent_steps(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("status exploded")

    monkeypatch.setattr(runtime, "status_command", broken)
    summary, code = runtime.daily_command(ctx, session="2026-10-30", execute=True, now=at("2026-10-30", "18:30"),
                                          env={})
    steps = _steps(summary)
    assert code == 1 and summary["status"] == "failed"
    assert steps["status"] == {"step": "status", "status": "failed", "error": "RuntimeError: status exploded"}
    assert steps["shadow_fill"]["status"] == "ok" and steps["plans"]["status"] == "ok"
    assert steps["publish_status"]["status"] == "skipped" and steps["alerts"]["status"] == "ok"
    failed = [a for a in steps["alerts"]["result"]["alerts"] if a["code"] == "daily_step_failed"]
    assert failed and "status exploded" in failed[0]["message"]


def test_evening_refusal_is_a_failed_step(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.data_dir / "market/krx_daily/2026/20261102.jsonl").unlink()
    summary, code = runtime.daily_command(ctx, session="2026-11-02", execute=True, now=at("2026-11-02", "18:30"),
                                          env={})
    step = _steps(summary)["shadow_fill"]
    assert code == 1 and step["status"] == "failed" and "price_file_missing" in step["error"]
    codes = [a["code"] for a in _steps(summary)["alerts"]["result"]["alerts"]]
    assert "incident_file" in codes and "daily_step_failed" in codes


class FakeBackend:
    def __init__(self):
        self.published = []

    def unknown_orders(self):
        return [{"orderId": "o-1", "strategyId": "hf001_f_both"}]

    def risk(self, user_id):
        return {"buyAllowed": False, "fullReviewRequired": False, "halted": False, "reasons": ["DAILY_LOSS"]}

    def get_batch(self, batch_id):
        return {"batchId": batch_id, "status": "REJECTED"}

    def publish_status(self, strategy_id, generated_at, payload):
        self.published.append(strategy_id)
        return {"stored": True}


def test_evening_with_backend_publishes_and_alerts(tmp_path):
    ctx = _ctx(tmp_path)
    import dataclasses
    ctx = dataclasses.replace(ctx, entries=tuple(
        dataclasses.replace(e, account_user_id="u1") if e.strategy_id == "hf001_f_both" else e for e in ctx.entries))
    runtime.record_batch(ctx.data_dir, "hf001_f_both",
                         {"userId": "u1", "sessionDate": "2026-10-30", "phase": "CLOSE", "decisionHash": "h",
                          "idempotencyKey": "k"}, {"batchId": "b-1"})
    fake = FakeBackend()
    summary, code = runtime.daily_command(ctx, session="2026-10-30", execute=True, now=at("2026-10-30", "18:30"),
                                          client_factory=lambda: fake, env={})
    steps = _steps(summary)
    assert code == 0 and fake.published == ["hf001_f_both", "av001_av_all"]
    backend = steps["backend_checks"]["result"]
    assert backend["configured"] and backend["batches"][0]["status"] == "REJECTED"
    codes = {a["code"] for a in steps["alerts"]["result"]["alerts"]}
    assert {"unknown_orders", "batch_problem", "risk_buy_blocked"} <= codes


def test_unknown_alert_channel_fails_before_any_work(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="unknown_alert_channel:pigeon"):
        runtime.daily_command(ctx, session="2026-10-30", execute=True, now=at("2026-10-30", "18:30"),
                              env={"HQA_ALERT_CHANNELS": "pigeon"})
    assert not (ctx.data_dir / "live").exists()


def test_cli_daily_exit_code(tmp_path, capsys, monkeypatch):
    from scripts.ops import strategy_runtime as cli

    ctx = _ctx(tmp_path, seal=False)
    for name in ("BACKEND_INTERNAL_BASE_URL", "BACKEND_BASE_URL", "HQA_INTERNAL_TOKEN", "HQA_ALERT_CHANNELS"):
        monkeypatch.delenv(name, raising=False)
    code = cli.main(["--repo-root", str(ctx.repo_root), "--data-dir", str(ctx.data_dir), "daily",
                     "--session", "2026-11-02", "--when", "morning"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["status"] == "failed" and out["dryRun"]


# ---------------------------------------------------------------- deploy files


def _unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(DEPLOY / "systemd" / name, encoding="utf-8")
    return parser


@pytest.mark.parametrize("name, command", [
    ("hqa-strategy-runtime-morning.service", "deploy/strategy-runtime/morning.sh"),
    ("hqa-strategy-runtime-evening.service", "deploy/strategy-runtime/evening.sh"),
    ("hqa-strategy-runtime-failure@.service", "strategy_runtime alerts --execute --failure-unit %i"),
])
def test_service_units(name, command):
    unit = _unit(name)
    service = unit["Service"]
    assert command in service["ExecStart"]
    for key, value in {"Type": "oneshot", "User": "hqa", "Group": "hqa", "WorkingDirectory": "/opt/hqa",
                       "EnvironmentFile": "/etc/hqa/strategy-runtime.env", "NoNewPrivileges": "true",
                       "ProtectSystem": "strict", "ReadWritePaths": "/opt/hqa/data", "PrivateTmp": "true"}.items():
        assert service[key] == value, key
    assert service["MemoryMax"] and service["TimeoutStartSec"]
    assert "PrivateNetwork" not in service  # the runtime talks to the backend
    if "failure@" not in name:
        assert unit["Unit"]["OnFailure"] == "hqa-strategy-runtime-failure@%n.service"


@pytest.mark.parametrize("name, calendar, service", [
    ("hqa-strategy-runtime-morning.timer", "Mon..Fri *-*-* 09:05 Asia/Seoul", "hqa-strategy-runtime-morning.service"),
    ("hqa-strategy-runtime-evening.timer", "Mon..Fri *-*-* 19:30 Asia/Seoul", "hqa-strategy-runtime-evening.service"),
])
def test_timer_units(name, calendar, service):
    unit = _unit(name)
    assert unit["Timer"]["OnCalendar"] == calendar and unit["Timer"]["Persistent"] == "true"
    assert unit["Timer"]["Unit"] == service and unit["Install"]["WantedBy"] == "timers.target"


def test_env_example_lists_names_only():
    lines = [line for line in (DEPLOY / "strategy-runtime.env.example").read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#")]
    assert lines == ["HQA_DATA_DIR=", "BACKEND_INTERNAL_BASE_URL=", "HQA_INTERNAL_TOKEN=", "HQA_ALERT_CHANNELS=",
                     "HQA_TELEGRAM_BOT_TOKEN=", "HQA_TELEGRAM_CHAT_ID=", "HQA_COLLECTOR_HOST=",
                     "HQA_COLLECTOR_SSH_KEY="]


def _bash(args, env, cwd):
    clean = {"PATH": os.environ["PATH"], "HOME": str(cwd), **env}
    return subprocess.run(["bash", *args], cwd=cwd, env=clean, capture_output=True, text=True, timeout=60)


def test_install_dry_run_prints_and_copies_nothing(tmp_path):
    target = tmp_path / "systemd"
    result = _bash([str(DEPLOY / "install.sh")], {"HQA_SYSTEMD_DIR": str(target)}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Dry run: nothing was copied" in result.stdout and "hqa-strategy-runtime-evening.timer" in result.stdout
    assert not target.exists()
    result = _bash([str(DEPLOY / "install.sh"), "--execute"], {"HQA_SYSTEMD_DIR": str(target)}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in target.iterdir()) == sorted(p.name for p in (DEPLOY / "systemd").iterdir())
    assert "systemctl enable --now" in result.stdout  # printed for the operator, never run


def test_evening_wrapper_without_pull_and_with_failed_pull(tmp_path):
    fake = tmp_path / "fake-python"
    fake.write_text("#!/usr/bin/env bash\nprintf 'RAN %s\\n' \"$*\"\n", encoding="utf-8")
    fake.chmod(0o755)
    result = _bash([str(DEPLOY / "evening.sh")], {"HQA_PYTHON": str(fake)}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "RAN -m scripts.ops.strategy_runtime daily --when evening --execute" in result.stdout
    # An invalid host is refused by pull_collector_data.sh before any connection.
    result = _bash([str(DEPLOY / "evening.sh")], {"HQA_PYTHON": str(fake), "HQA_COLLECTOR_HOST": "not-a-host"},
                   tmp_path)
    assert result.returncode == 1 and "pull failed" in result.stderr and "RAN" not in result.stdout
    result = _bash([str(DEPLOY / "evening.sh")], {"HQA_PYTHON": str(fake), "HQA_COLLECTOR_HOST": "u@h",
                                                  "HQA_DATA_DIR": str(tmp_path / "elsewhere")}, tmp_path)
    assert result.returncode == 1 and "must be" in result.stderr and "RAN" not in result.stdout


def test_morning_wrapper_pulls_before_the_seal_check_and_run_sh_needs_a_phase(tmp_path):
    fake = tmp_path / "fake-python"
    fake.write_text("#!/usr/bin/env bash\nprintf 'RAN %s\\n' \"$*\"\n", encoding="utf-8")
    fake.chmod(0o755)
    result = _bash([str(DEPLOY / "morning.sh")], {"HQA_PYTHON": str(fake)}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "RAN -m scripts.ops.strategy_runtime daily --when morning --execute" in result.stdout
    result = _bash([str(DEPLOY / "morning.sh")], {"HQA_PYTHON": str(fake), "HQA_COLLECTOR_HOST": "not-a-host"},
                   tmp_path)
    assert result.returncode == 1 and "morning run did not start" in result.stderr and "RAN" not in result.stdout
    result = _bash([str(DEPLOY / "run.sh")], {"HQA_PYTHON": str(fake)}, tmp_path)
    assert result.returncode == 2 and "Usage" in result.stderr


def test_timestamps_are_kst():
    assert at("2026-10-30", "18:30").astimezone(timezone.utc).hour == 9
