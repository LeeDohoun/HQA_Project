from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from src.live import alerts, runtime, shadow
from test_live_fixtures import decision_record, make_repo, write_decision, write_flat_days

KST = shadow.KST
NOW = datetime(2026, 11, 2, 18, 40, tzinfo=KST)


def _codes(items):
    return sorted((a.severity, a.code) for a in items)


def _status(*, errors=(), stages=(("hf001_f_both", "shadow", "observe", ["holdout_not_passed:x"]),)):
    return {"errors": list(errors), "strategies": [
        {"strategyId": sid, "stage": {"requested": req, "effective": eff, "reasons": reasons},
         "shadow": {"navKrw": 100_000_000, "returnPct": 0.0, "sessions": 0, "incidents": 0}}
        for sid, req, eff, reasons in stages]}


def test_alert_validation_and_default_dedupe_key():
    alert = alerts.Alert("warning", "x", "s", "m")
    assert alert.dedupe_key == "x:s"
    with pytest.raises(ValueError, match="severity"):
        alerts.Alert("fatal", "x", None, "m")


def test_critical_rules():
    summary = {"runId": "r", "session": "2026-11-02", "steps": [
        {"step": "shadow_fill", "status": "failed", "error": "ValueError: boom"},
        {"step": "decision_seals", "status": "failed", "error": "decision_not_final:2026-10:decision_seal_late",
         "result": {"strategies": [{"strategyId": "hf001_f_both", "month": "2026-10", "state": "decision_seal_late"},
                                   {"strategyId": "av001_av_all", "month": "2026-10", "state": "final"}]}}]}
    inputs = alerts.AlertInputs(
        today=NOW.date(), summary=summary, status=_status(errors=["hf001_f_both:decision_seal_invalid:2026-10:x"]),
        ledgers={"hf001_f_both": [{"date": "2026-11-02", "incidents": ["price_row_missing:000010"]}]},
        incident_files={"hf001_f_both": [{"_file": "20261102_price_file_missing_ab.json", "date": "2026-11-02",
                                          "kind": "price_file_missing", "detail": "2026-11-02"}]},
        backend={"configured": True, "unknown_orders": [{"orderId": "o1"}],
                 "batches": [{"batchId": "b1", "status": "PARTIAL", "strategyId": "hf001_f_both",
                              "sessionDate": "2026-11-02", "phase": "OPEN"},
                             {"batchId": "b2", "status": "COMPLETED"}],
                 "risk": {"hf001_f_both": {"buyAllowed": False, "fullReviewRequired": True, "halted": True,
                                           "reasons": ["DRAWDOWN_STOP"]}}},
        failure_unit="hqa-strategy-runtime-evening.service")
    found = alerts.evaluate(inputs, {})
    codes = [a.code for a in found if a.severity == "critical"]
    assert sorted(codes) == sorted(["status_error", "shadow_incident", "incident_file", "decision_not_final",
                                    "daily_step_failed", "systemd_unit_failed", "unknown_orders", "batch_problem",
                                    "risk_buy_blocked", "risk_full_review", "trading_halted"])
    step = next(a for a in found if a.code == "daily_step_failed")
    assert step.details["step"] == "shadow_fill" and "boom" in step.message  # decision_seals has its own rule
    assert next(a for a in found if a.code == "decision_not_final").strategy_id == "hf001_f_both"


def test_warning_rules_and_stage_change_only():
    summary = {"steps": [{"step": "plans", "status": "ok", "result": {"plans": [
        {"strategyId": "av001_av_all", "sessionDate": "2026-12-01", "phase": "OPEN", "advWarnings": 3,
         "funding": {"requires_same_auction_sell_proceeds": True}}]}}]}
    inputs = alerts.AlertInputs(today=NOW.date(), summary=summary, status=_status(),
                                calendar_warnings=["calendar_notice_required:2026-11-19:blocks_in_17_days:x"])
    found = alerts.evaluate(inputs, {})
    assert _codes(found) == [("warning", "adv_warnings"), ("warning", "calendar_review"),
                             ("warning", "funding_requires_sell_proceeds"), ("warning", "stage_blocked")]
    assert not [a for a in alerts.evaluate(inputs, {"stages": {"hf001_f_both": "observe"}}) if a.code == "stage_blocked"]
    moved = alerts.evaluate(inputs, {"stages": {"hf001_f_both": "shadow"}})
    assert [a.code for a in moved].count("stage_blocked") == 1


def test_morning_daily_summary_and_backend_not_configured():
    summary = {"runId": "r1", "steps": [{"step": "plans", "status": "ok", "result": {"plans": [
        {"strategyId": "hf001_f_both", "sessionDate": "2026-11-02", "phase": "OPEN"}]}}]}
    inputs = alerts.AlertInputs(today=NOW.date(), when="morning", session="2026-11-02", summary=summary,
                                status=_status(stages=(("hf001_f_both", "shadow", "shadow", []),)),
                                backend={"configured": False, "note": "backend_not_configured"})
    (info,) = alerts.evaluate(inputs, {})
    assert (info.severity, info.code) == ("info", "daily_summary")
    assert info.details["strategies"][0]["nextAction"] == "2026-11-02 OPEN"
    assert info.message.startswith("일일 요약 2026-11-02")
    inputs.when = "evening"
    assert alerts.evaluate(inputs, {}) == []


def test_dedupe_windows():
    critical = alerts.Alert("critical", "c", None, "m", {}, "k1")
    warning = alerts.Alert("warning", "w", None, "m", {}, "k2")
    info = alerts.Alert("info", "i", None, "m", {}, "k3")
    state = {"sent": {"k1": NOW.replace(hour=9).isoformat(), "k2": (NOW - timedelta(days=6)).isoformat(),
                      "k3": NOW.isoformat()}}
    assert [a.code for a in alerts.due([critical, warning, info], state, NOW)] == ["i"]
    tomorrow = NOW + timedelta(hours=6)  # 00:40 KST the next day
    assert [a.code for a in alerts.due([critical, warning, info], state, tomorrow)] == ["c", "i"]
    assert [a.code for a in alerts.due([warning], state, NOW + timedelta(days=1))] == ["w"]


def test_channel_registry_and_unknown_channel():
    assert alerts.configured_channel_names({}) == []
    assert alerts.configured_channel_names({"HQA_ALERT_CHANNELS": " file, stdout ,file"}) == ["file", "stdout"]
    with pytest.raises(ValueError, match="unknown_alert_channel:pigeon"):
        alerts.configured_channel_names({"HQA_ALERT_CHANNELS": "file,pigeon"})
    sent = []

    class Memory:
        name = "memory"

        def __init__(self, data_dir, now, **kwargs):
            pass

        def send(self, items):
            sent.extend(items)
            return {"channel": "memory", "sent": len(items)}

    alerts.register_channel("memory", Memory)
    try:
        assert alerts.configured_channel_names({"HQA_ALERT_CHANNELS": "memory"}) == ["memory"]
        names = [c.name for c in alerts.build_channels("/x", NOW, execute=True, env={"HQA_ALERT_CHANNELS": "memory"})]
        assert names == ["file", "memory"]
        assert [c.name for c in alerts.build_channels("/x", NOW, execute=False, env={})] == ["stdout"]
    finally:
        alerts.CHANNEL_REGISTRY.pop("memory")
    with pytest.raises(ValueError):
        alerts.register_channel("Bad,Name", Memory)


def test_dispatch_writes_file_state_and_cursor_only_with_execute(tmp_path):
    inputs = alerts.AlertInputs(today=NOW.date(), status=_status(errors=["e1"]),
                                ledgers={"hf001_f_both": [{"date": "2026-11-02", "incidents": ["x"]}]})
    dry = alerts.dispatch(inputs, tmp_path, NOW, execute=False, env={})
    assert dry["sent"] == 3 and dry["channels"][0]["channel"] == "stdout"
    assert not (tmp_path / "live").exists()
    first = alerts.dispatch(inputs, tmp_path, NOW, execute=True, env={})
    assert first["sent"] == 3 and first["status"] == "ok"
    lines = (tmp_path / "live/alerts/20261102.jsonl").read_text(encoding="utf-8").splitlines()
    assert sorted(json.loads(line)["code"] for line in lines) == ["shadow_incident", "stage_blocked", "status_error"]
    state = alerts.load_state(tmp_path)
    assert state["incidents_seen"]["hf001_f_both"]["ledger_through"] == "2026-11-02"
    assert state["stages"] == {"hf001_f_both": "observe"}
    again = alerts.dispatch(inputs, tmp_path, NOW + timedelta(minutes=5), execute=True, env={})
    assert again["sent"] == 0 and again["evaluated"] == 1 and again["suppressed"] == 1
    later = alerts.dispatch(inputs, tmp_path, NOW + timedelta(days=1), execute=True, env={})
    assert [a["code"] for a in later["alerts"]] == ["status_error"]  # incidents are alerted once, ever


def test_garbled_state_is_an_error(tmp_path):
    path = alerts.state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="garbled"):
        alerts.load_state(tmp_path)


def test_alerts_command_failure_unit_and_backend_not_configured(tmp_path):
    root = make_repo(tmp_path)
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=["000010"], av_all=["000010"]))
    write_flat_days(data, "2026-10-30", "2026-11-02", ["000010"])
    ctx = runtime.load_context(root, data)
    result = runtime.alerts_command(ctx, execute=True, failure_unit="hqa-strategy-runtime-evening.service",
                                    now=NOW, env={})
    codes = [(a["severity"], a["code"]) for a in result["alerts"]]
    assert ("critical", "systemd_unit_failed") in codes
    assert result["status"] == "ok" and not result["dryRun"]
    assert all(c[1] not in ("unknown_orders", "risk_buy_blocked") for c in codes)
    line = (data / "live/alerts/20261102.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(x)["code"] == "systemd_unit_failed" for x in line)
    assert date.fromisoformat(json.loads(line[0])["sent_at"][:10]) == NOW.date()


# ---------------------------------------------------------------- telegram

TOKEN = "123456:SECRET-token"
TG_ENV = {"HQA_ALERT_CHANNELS": "file,telegram", "HQA_TELEGRAM_BOT_TOKEN": TOKEN, "HQA_TELEGRAM_CHAT_ID": "-100"}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body if body is not None else {"ok": True, "result": {}}

    def json(self):
        return self.body


class FakeTelegram:
    def __init__(self, responses=()):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return FakeResponse()


def _channel(fake, sleeps=None, context=None):
    return alerts.TelegramChannel(env=TG_ENV, session=fake, sleep=(sleeps.append if sleeps is not None else None),
                                  context=context or {"day": "2026-11-02", "when": "evening"})


def _alert(severity, code, sid=None, message="메시지"):
    return alerts.Alert(severity, code, sid, message, {}, f"{code}:{sid}")


def test_telegram_payload_and_grouping_critical_first():
    fake = FakeTelegram()
    result = _channel(fake).send([_alert("info", "daily_summary", message="요약"),
                                  _alert("critical", "unknown_orders", message="확인 필요 주문 1건"),
                                  _alert("warning", "stage_blocked", "av001_av_all", "단계")])
    (call,) = fake.calls
    assert call["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage" and call["timeout"] == 10
    assert set(call["json"]) == {"chat_id", "text", "disable_web_page_preview"}
    assert call["json"]["chat_id"] == "-100" and call["json"]["disable_web_page_preview"] is True
    lines = call["json"]["text"].split("\n")
    assert lines[0] == "[HQA 전략] 긴급 1 · 주의 1 · 정보 1 (2026-11-02 저녁)"
    assert lines[1:] == ["긴급 | 전체 | 확인 필요 주문 1건", "주의 | av001_av_all | 단계", "정보 | 전체 | 요약"]
    assert result == {"channel": "telegram", "sent": 3, "messages": 1,
                      "delivered": ["unknown_orders:None", "stage_blocked:av001_av_all", "daily_summary:None"]}


def test_telegram_chunks_under_limit():
    many = [_alert("warning", f"w{i}", message="가" * 300) for i in range(40)]
    many.append(_alert("critical", "c", message="긴급!"))
    messages = alerts.render_messages(many, day="2026-11-02", when="morning")
    assert len(messages) > 1 and all(len(text) < 4096 for text, _ in messages)
    assert messages[0][0].startswith("[HQA 전략] 긴급 1 · 주의 40 · 정보 0 (2026-11-02 아침) (1/")
    assert messages[0][0].split("\n")[1] == "긴급 | 전체 | 긴급!"
    assert sum(len(keys) for _, keys in messages) == 41
    (single,) = alerts.render_messages([_alert("critical", "x", message="나" * 9000)], day="d", when=None)
    assert len(single[0]) < 4096 and "(d 수시)" in single[0]


def test_telegram_retries_429_with_capped_retry_after():
    sleeps = []
    fake = FakeTelegram([FakeResponse(429, {"ok": False, "parameters": {"retry_after": 99}}),
                         FakeResponse(429, {"ok": False, "parameters": {"retry_after": 2}}), FakeResponse()])
    result = _channel(fake, sleeps).send([_alert("critical", "c")])
    assert result["sent"] == 1 and "error" not in result and sleeps == [30, 2.0] and len(fake.calls) == 3
    fake = FakeTelegram([FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}})] * 4)
    result = _channel(fake, []).send([_alert("critical", "c")])
    assert len(fake.calls) == 4 and result["delivered"] == [] and "telegram_send_failed" in result["error"]


def test_telegram_errors_never_reveal_the_token():
    import requests

    fake = FakeTelegram([requests.ConnectionError(f"failed https://api.telegram.org/bot{TOKEN}/sendMessage")])
    result = _channel(fake).send([_alert("critical", "c")])
    assert TOKEN not in json.dumps(result) and "<redacted>" in result["error"]
    fake = FakeTelegram([FakeResponse(401, {"ok": False, "description": "Unauthorized"})])
    result = _channel(fake).send([_alert("critical", "c")])
    assert result["error"] == "RuntimeError: telegram_send_failed:HTTP 401:Unauthorized"
    assert TOKEN not in repr(_channel(FakeTelegram()).__dict__.get("chat_id"))


def test_telegram_missing_env_is_a_startup_error():
    with pytest.raises(ValueError, match="telegram_not_configured:HQA_TELEGRAM_BOT_TOKEN,HQA_TELEGRAM_CHAT_ID"):
        alerts.configured_channel_names({"HQA_ALERT_CHANNELS": "file,telegram"})
    with pytest.raises(ValueError, match="telegram_not_configured:HQA_TELEGRAM_CHAT_ID"):
        alerts.configured_channel_names({"HQA_ALERT_CHANNELS": "telegram", "HQA_TELEGRAM_BOT_TOKEN": "x"})
    assert alerts.configured_channel_names(TG_ENV) == ["file", "telegram"]


def test_failed_telegram_keeps_file_record_fails_and_retries_next_run(tmp_path, monkeypatch):
    inputs = alerts.AlertInputs(today=NOW.date(), status=_status(errors=["e1"]),
                                ledgers={"hf001_f_both": [{"date": "2026-11-02", "incidents": ["x"]}]})
    failing = FakeTelegram([FakeResponse(500, {"ok": False, "description": "boom"})])
    monkeypatch.setattr(alerts, "requests", failing)
    result = alerts.dispatch(inputs, tmp_path, NOW, execute=True, env=TG_ENV)
    assert result["status"] == "failed"
    file_result, telegram = result["channels"]
    assert file_result["sent"] == 3 and "error" in telegram and TOKEN not in json.dumps(result)
    assert len((tmp_path / "live/alerts/20261102.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    state = alerts.load_state(tmp_path)
    assert state.get("sent", {}) == {} and "incidents_seen" not in state  # nothing delivered: retry next run
    working = FakeTelegram()
    monkeypatch.setattr(alerts, "requests", working)
    retry = alerts.dispatch(inputs, tmp_path, NOW + timedelta(minutes=10), execute=True, env=TG_ENV)
    assert retry["status"] == "ok" and retry["sent"] == 3 and len(working.calls) == 1
    assert len(alerts.load_state(tmp_path)["sent"]) == 3
    quiet = alerts.dispatch(inputs, tmp_path, NOW + timedelta(minutes=20), execute=True, env=TG_ENV)
    assert quiet["sent"] == 0 and len(working.calls) == 1


def test_daily_exit_code_nonzero_when_telegram_fails(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=["000010"], av_all=["000010"]))
    write_flat_days(data, "2026-10-30", "2026-11-02", ["000010"])
    ctx = runtime.load_context(root, data)
    monkeypatch.setattr(alerts, "requests", FakeTelegram([FakeResponse(500, {"ok": False})] * 5))
    summary, code = runtime.daily_command(ctx, session="2026-11-02", execute=True,
                                          now=datetime(2026, 11, 2, 9, 10, tzinfo=KST), env=TG_ENV)
    step = next(s for s in summary["steps"] if s["step"] == "alerts")
    assert code == 1 and step["status"] == "failed" and step["error"] == "alert_channel_failed"
    assert TOKEN not in (data / "live/runs/20261102_morning.json").read_text(encoding="utf-8")
