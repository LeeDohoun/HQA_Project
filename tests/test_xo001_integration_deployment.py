import ast
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path

import numpy as np
import pytest

from backtesting.experiments.xo001 import Refused, aware
from backtesting.experiments.xo001 import activation
from backtesting.experiments.xo001.calendar import Calendar
from backtesting.experiments.xo001.evaluate import evaluate
from backtesting.experiments.xo001.recorder import Recorder, run_morning
from backtesting.experiments.xo001.roster import BB003, eligibility
from backtesting.experiments.xo001.storage import Store, encoded, load, sha
from scripts.xo001.__main__ import main, require_installed
from test_xo001_support import Clock, Quotes, SPEC, bb003_record, fake_kernel, good_clock, plan, profile_data, simple_roster, fake_runtime
from test_xo001_rate_calendar_roster import history_rows

ROOT = Path(__file__).resolve().parents[1]


def test_pre_entry_matches_frozen_bb001_functions_without_importing_its_settings():
    source = ast.parse((ROOT / "backtesting/experiments/bb001.py").read_bytes())
    nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in {"_positive", "_pre_entry", "empirical_boundaries", "lower_bin"}]
    scope = {"np": np, "Counter": Counter}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "frozen-bb001-pure-functions", "exec"), scope)
    history = history_rows()
    history[-1]["000010"]["stock_name"] = "a스팩"
    history[0]["000020"]["ret_1d"] = -.5
    class Reader:
        sessions = list(range(21))
        def read(self, day):
            return history[day]
    assert eligibility(history) == scope["_pre_entry"](Reader(), 20)


def test_read_only_input_roots_and_no_heavy_lock_in_any_deployment_unit():
    directory = ROOT / "deploy/collector-xo001"
    for unit in (directory / "systemd").glob("*.service"):
        text = unit.read_text()
        assert "WorkingDirectory=/opt/hqa-xo001" in text
        assert "ReadWritePaths=/var/lib/hqa-xo001\n" in text
        assert "ReadOnlyPaths=/var/lib/hqa/data /var/lib/hqa-xo001/activation-control" in text
        assert "InaccessiblePaths=-/opt/hqa\n" in text
        assert "MemoryMax=384M" in text
        assert "Conflicts=" not in text
        if "reconcile" not in unit.name:
            assert "EnvironmentFile=/etc/hqa/xo001.env" in text
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix != ".pyc":
            text = path.read_text()
            assert "/run/hqa-heavy/run.lock" not in text
            assert "systemctl stop hqa-fw" not in text
    installer = (directory / "install.py").read_text()
    assert '"--no-index"' in installer
    assert '"--only-binary=:all:"' in installer


def test_cli_dry_run_and_plan_never_read_env_or_create_output(tmp_path, monkeypatch, capsys):
    def denied():
        pytest.fail("dry run attempted to read credentials")
    monkeypatch.setattr("scripts.xo001.__main__.Credentials.environment", denied)
    root = tmp_path / "own"
    assert main(["morning", "--data-root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["network"] is False
    assert main(["plan", "--data-root", str(root)]) == 0
    assert not root.exists()
    with pytest.raises(Refused, match="private venv"):
        require_installed(Store(root))


def test_main_run_missing_calendar_records_missing_without_any_request(tmp_path, fake_kernel):
    clock, client = Clock(), Quotes(Clock())
    store = Store(tmp_path / "own", clock=clock)
    class Unverified:
        def day(self, day):
            raise Refused("calendar_unverified:" + day)
    result = run_morning(store, client, BB003(tmp_path / "bb003", store), tmp_path / "source", Unverified(), {})
    assert result["status"] == "calendar_unverified"
    assert store.get("missing_day", "2026-11-03")["orders"] is False
    assert client.sequence == 0


def test_morning_recovers_fixed_slots_late_event_and_preserves_0825_roster(tmp_path, fake_kernel, monkeypatch):
    clock = Clock()
    store = Store(tmp_path / "own", clock=clock)
    original = simple_roster(store)
    activation.seal_profile(store, profile_data(), SPEC)
    store.seal("shakedown_plan", "november", {"tests": [], "label": "fake integration plan"})
    source = tmp_path / "bb003"
    clock.set("2026-11-03T08:58:58+09:00")
    event = {"rcept_no": "20261102000002", "stock_code": "000660", "corp_code": "other",
             "entry_date": "2026-11-03", "rcept_dt": "20261102"}
    bb003_record(source, "decisions", event["rcept_no"], {"event": event, "status": "included", "event_features": {}}, clock,
                 deadline="2026-11-03T08:59:00+09:00")
    clock.set("2026-11-03T08:58:55+09:00")
    class FakeCalendar:
        def day(self, day):
            return plan(day)
    monkeypatch.setattr("backtesting.experiments.xo001.recorder.verify", lambda *_: None)
    client = Quotes(clock)
    result = run_morning(store, client, BB003(source, store), tmp_path / "source", FakeCalendar(), {},
                         shakedown=True, sleeper=clock.sleep, clock_status=good_clock)
    assert result["preseal_valid"]
    assert store.get("roster", "2026-11-03") == original
    assert store.get("denominator", "2026-11-03")["events"][0]["event"]["stock_code"] == "000660"
    assert store.get("late_event", "2026-11-03." + event["rcept_no"])["roster_replaced"] is False
    assert store.get("snapshot", "2026-11-03.s0859")["states"]["000660"]["state"] == "unknown"
    assert store.get("postseal", "2026-11-03")["states"]["000660"]["state"] == "observed"
    # Completed run restarted after the window must never replace either segment.
    proofs = {kind: store.proof(kind, "2026-11-03")["record_sha256"] for kind in ("preseal", "postseal")}
    clock.sleep(10)
    client2 = Quotes(clock)
    client2.connection_id = "restart"
    result = run_morning(store, client2, BB003(source, store), tmp_path / "source", FakeCalendar(), {},
                         shakedown=True, sleeper=clock.sleep, clock_status=good_clock)
    assert result["restart_recovered"]
    assert proofs == {kind: store.proof(kind, "2026-11-03")["record_sha256"] for kind in proofs}


def test_global_primary_denominator_includes_bb003_event_on_total_recorder_outage_day(tmp_path, fake_kernel, fake_runtime, monkeypatch):
    monkeypatch.setattr("backtesting.experiments.xo001.evaluate.register_final", lambda *_: None)
    clock = Clock("2026-11-30T10:00:00+09:00")
    store = Store(tmp_path / "own", clock=clock)
    store.seal("activation", "activation", {"binding": {}, "diagnostic_approved": False}, deadline=activation.ACTIVATION_BY)
    source = tmp_path / "bb003"
    clock.set("2026-12-02T08:58:00+09:00")
    event = {"rcept_no": "20261201000001", "stock_code": "005930", "corp_code": "company",
             "entry_date": "2026-12-02", "rcept_dt": "20261201"}
    bb003_record(source, "decisions", event["rcept_no"], {"status": "included", "event": event,
                  "event_features": {"pre_close": 10000}}, clock, deadline="2026-12-02T08:59:00+09:00")
    bb003_record(source, "post_entry", event["rcept_no"], {"status": "excluded", "reason": "limit_up_entry", "selection": None}, clock)
    class FinalCalendar:
        def day(self, day):
            return plan(day) if day in {"2026-12-02", "2028-10-02"} else {"session": False}
        def last_entry(self):
            return "2028-10-02"
        def freeze(self):
            return aware("2028-10-09T23:59:59+09:00")
    clock.set("2028-10-10T12:00:00+09:00")
    result = evaluate(store, FinalCalendar(), BB003(source, store))
    assert result["status"] == "measurement_insufficient"
    assert result["overall_denominator"] == 1
    assert result["coverage_first"]["coverage"] == 0
    assert result["price_denominator"] == 0
    assert result["primary_rows"][0]["rcept_no"] == event["rcept_no"]
    assert result["strategy_test"] is False
    assert result["diagnostic_in_primary"] is False
    assert evaluate(store, FinalCalendar(), BB003(source, store)) == result


def test_unknown_clock_or_late_first_print_never_counts_as_halt(tmp_path, fake_kernel):
    from backtesting.experiments.xo001.fields import Profile
    clock = Clock()
    store = Store(tmp_path / "own", clock=clock)
    simple_roster(store)
    recorder = Recorder(store, Quotes(clock, lambda value: {**value, "trade": {**value["trade"], "auction_flag": ""}}),
                        Profile(profile_data()), plan(), clock_status=good_clock)
    clock.set("2026-11-03T09:01:01+09:00")
    recorder.block("005930", "event", book=False, phase="post", deadline=clock() + timedelta(seconds=2))
    clock.set("2026-11-03T09:05:00+09:00")
    value = recorder.seal_post()["record"]["states"]["005930"]
    assert value["state"] == "unknown"
    assert value["opening_category"] != "official_halt"
