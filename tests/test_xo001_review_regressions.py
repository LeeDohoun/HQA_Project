"""Independent-review regressions: offline inputs, clocks and HTTP connections only."""
from datetime import timedelta
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtesting import experiment_registry as registry
from backtesting.experiments.xo001 import Refused, aware
from backtesting.experiments.xo001 import activation, kis, recorder, roster
from backtesting.experiments.xo001.calendar import time_map
from backtesting.experiments.xo001.diagnostic import Diagnostic
from backtesting.experiments.xo001.fields import Profile
from backtesting.experiments.xo001.rate import RateManager
from backtesting.experiments.xo001.storage import Store, encoded, sha
from scripts.xo001.__main__ import execute
from test_xo001_capture_evaluate import full_capture
from test_xo001_support import (Clock, Quotes, SPEC, bb003_record, credentials, fake_kernel,
                                good_clock, plan, profile_data, simple_roster)

evaluation = importlib.import_module("backtesting.experiments.xo001.evaluate")


class Calendar:
    def day(self, day):
        return plan(day)

    def freeze(self):
        return aware("2028-10-09T23:59:59+09:00")

    def last_entry(self):
        return "2026-12-01"


class EmptyBB003:
    def keys(self, _):
        return []


class NoInputs:
    def __getattr__(self, _):
        pytest.fail("measurement read inputs before verification / during sealed-result recovery")


def runtime_setup(tmp_path, monkeypatch):
    clock = Clock("2026-11-30T10:00:00+09:00")
    store = Store(tmp_path / "own", clock=clock)
    state = {"fingerprint": {"code_sha256": "a" * 64,
        "files": {"backtesting/experiments/xo001/recorder.py": "a" * 64}},
        "environment": {"environment_sha256": "b" * 64, "environment": {"host": "test"}}}
    monkeypatch.setattr(activation, "fingerprint", lambda: state["fingerprint"])
    monkeypatch.setattr(activation, "environment", lambda: state["environment"])
    monkeypatch.setattr(activation, "profile_from", lambda _: Profile(profile_data()))
    binding = {**state["fingerprint"], **state["environment"], **credentials().identifiers(),
        "profile_sha256": sha(encoded(profile_data())), "config_sha256": sha(encoded(activation.CONFIG))}
    store.seal("activation", "activation", {"binding": binding, "diagnostic_approved": False},
               deadline=activation.ACTIVATION_BY)
    clock.set("2028-10-10T12:00:00+09:00")
    return store, clock, state


def daily_file(source, day, markets=("KOSPI", "KOSDAQ")):
    path = source / f"market/krx_daily/{day[:4]}/{day.replace('-', '')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(encoded({"trade_date": day, "calendar_status": "verified",
        "stock_code": str(i).zfill(6), "market": market, "open": 10000, "volume": 10})
        for i, market in enumerate(markets)))
    return path


def test_pending_reconciliation_survives_evening_morning_restart_and_partial_market(tmp_path, monkeypatch, fake_kernel):
    store, clock, _ = runtime_setup(tmp_path, monkeypatch)
    day, source = "2026-12-01", tmp_path / "krx"
    clock.set(day + "T18:15:00+09:00")
    store.seal("roster", day, {"date": day, "label": "reconstructed"})
    first = recorder.reconcile_pending(store, source, Calendar(), EmptyBB003())
    assert first["pending"] == [day]
    assert store.get("reconcile_due", day)["date"] == day
    # A fresh process does not need to be passed --date again.
    restarted = Store(store.root, clock=clock)
    clock.set("2026-12-02T09:30:00+09:00")
    daily_file(source, day, ("KOSPI",))
    assert recorder.reconcile_pending(restarted, source, Calendar(), EmptyBB003())["pending"] == [day]
    assert not store.proof("reconciliation", day)
    daily_file(source, day)
    result = recorder.reconcile_pending(restarted, source, Calendar(), EmptyBB003())
    assert result["completed"] == [day] and result["pending"] == []
    proof = store.proof("reconciliation", day)
    assert proof["record"]["verification"]["binding"]["code_sha256"] == "a" * 64
    assert recorder.reconcile_pending(restarted, source, Calendar(), EmptyBB003())["completed"] == []
    assert store.proof("reconciliation", day)["record_sha256"] == proof["record_sha256"]


def test_pending_reconciliation_catches_bb003_outage_days_and_stops_at_freeze(tmp_path, monkeypatch, fake_kernel):
    store, clock, _ = runtime_setup(tmp_path, monkeypatch)
    source = tmp_path / "krx"
    day = "2026-12-01"
    class OutageBB003(EmptyBB003):
        def keys(self, kind):
            return ["event"] if kind == "decisions" else []
        def get(self, *_):
            return {"status": "included", "event": {"entry_date": day}}
    # At freeze, still acquire complete source data; after freeze, never acquire it.
    clock.set(Calendar().freeze())
    assert recorder.reconcile_pending(store, source, Calendar(), OutageBB003())["pending"] == [day]
    daily_file(source, day)
    clock.sleep(1)
    result = recorder.reconcile_pending(store, source, Calendar(), OutageBB003())
    assert result["status"] == "frozen" and result["pending"] == [day]
    assert store.proof("reconciliation", day) is None


def test_reconciliation_timer_runs_after_morning_collector_and_in_evening():
    text = Path("deploy/collector-xo001/systemd/hqa-xo001-reconcile.timer").read_text()
    assert "OnCalendar=*-*-* 09:30:00 Asia/Seoul" in text
    assert "OnCalendar=*-*-* 18:15:00 Asia/Seoul" in text


@pytest.mark.parametrize("command", ["reconcile", "evaluate"])
@pytest.mark.parametrize("change", ["code", "environment", "amendment"])
def test_measurement_paths_refuse_modified_runtime_before_inputs(tmp_path, monkeypatch, fake_kernel, command, change):
    store, _, state = runtime_setup(tmp_path, monkeypatch)
    if change == "code":
        state["fingerprint"] = {**state["fingerprint"], "code_sha256": "d" * 64}
    elif change == "environment":
        state["environment"] = {**state["environment"], "environment_sha256": "d" * 64}
    else:
        store.seal("bugfix", "000000", {"before": {}, "after": {}, "reason": "unbound", "evidence_hashes": []})
    with pytest.raises(Refused, match="binding differs|amendment chain"):
        if command == "reconcile":
            recorder.reconcile(store, NoInputs(), NoInputs(), NoInputs(), "2026-12-01")
        else:
            evaluation.evaluate(store, NoInputs(), NoInputs())
    assert store.keys("evaluation") == [] and store.keys("reconciliation") == []


@pytest.mark.parametrize("command", ["reconcile", "evaluate"])
def test_measurement_verifies_again_before_sealing(tmp_path, monkeypatch, fake_kernel, command):
    store, clock, state = runtime_setup(tmp_path, monkeypatch)
    def drift(*_):
        state["environment"] = {**state["environment"], "environment_sha256": "e" * 64}
    with pytest.raises(Refused, match="binding differs"):
        if command == "reconcile":
            clock.set("2026-12-01T18:15:00+09:00")
            source = tmp_path / "krx"
            daily_file(source, "2026-12-01")
            monkeypatch.setattr(recorder, "record_omissions", drift)
            recorder.reconcile(store, source, Calendar(), EmptyBB003(), "2026-12-01")
        else:
            original = evaluation.measure
            def measure(rows):
                drift()
                return original(rows)
            monkeypatch.setattr(evaluation, "measure", measure)
            evaluation.evaluate(store, Calendar(), EmptyBB003())
    assert store.keys("evaluation") == [] and store.keys("reconciliation") == []


@pytest.mark.parametrize("command", ["reconcile", "evaluate"])
def test_cli_offline_paths_verify_without_credentials(tmp_path, monkeypatch, fake_kernel, command):
    store, _, state = runtime_setup(tmp_path, monkeypatch)
    state["fingerprint"] = {**state["fingerprint"], "code_sha256": "e" * 64}
    monkeypatch.setattr(kis.Credentials, "environment", lambda: pytest.fail("offline measurement loaded credentials"))
    args = SimpleNamespace(command=command, bb003_root=tmp_path / "bb003", source_data=tmp_path / "krx",
                           date=None, registry_root=tmp_path / "registry")
    with pytest.raises(Refused, match="binding differs"):
        execute(args, store)


@pytest.mark.parametrize("delay,bound,synchronized,expected", [
    (2, .01, True, "late"), (0, .5, True, "not_submitted"), (0, .01, False, "not_submitted")])
def test_order_slow_connect_and_clock_recheck_abort_before_http_and_record(tmp_path, monkeypatch, fake_kernel,
                                                                         delay, bound, synchronized, expected):
    clock = Clock("2026-11-03T08:59:29.600000+09:00")
    store = Store(tmp_path / "own", clock=clock)
    class Connection:
        sent, closed = False, False
        def __init__(self, *_args, **_kwargs):
            pass
        def connect(self):
            clock.sleep(delay)
        def request(self, *_args, **_kwargs):
            Connection.sent = True
            pytest.fail("HTTP request written after deadline / invalid clock")
        def close(self):
            Connection.closed = True
    monkeypatch.setattr(kis.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(recorder, "clock_evidence", lambda: {
        "synchronized": synchronized, "error_bound_seconds": bound})
    rate = RateManager(store.path("control"), clock=clock, monotonic=lambda: clock.mono, sleeper=clock.sleep)
    client = kis.Client(credentials(), store.root, rate, clock=clock, monotonic=lambda: clock.mono)
    monkeypatch.setattr(client, "token", lambda **_: "fake-token")
    diagnostic = Diagnostic(store, client, credentials().identifiers(), gate=lambda *_: True)
    times = time_map(plan())
    body = diagnostic._body("005930", "buy", 1)
    result = diagnostic._send("slow", {"date": "2026-11-03", "stock_code": "005930", "side": "buy",
        "quantity": 1, "order_type": "01"}, body, "VTTC0012U", times["submit"], times["publish"])
    assert result["state"] == expected
    assert result["envelope"]["error"] == "order_HTTP_not_transmitted"
    assert "after connect" in result["envelope"]["reason"]
    assert not Connection.sent and Connection.closed
    assert store.get("order_result", "slow") == result | {
        "record_kind": "order_result", "record_key": "slow", "recorded_at": clock().isoformat()}
    assert diagnostic._send("slow", {}, body, "VTTC0012U", times["submit"], times["publish"])["status"] == "already_attempted"


@pytest.mark.parametrize("late_label", ["forward", "reconstructed"])
def test_late_roster_kernel_proof_cannot_enter_primary_even_with_complete_capture(tmp_path, fake_kernel, late_label):
    store, _, clock = full_capture(tmp_path)
    # Reproduce a legacy roster: no deadline and a plausible forward label, sealed late.
    original = store.proof("roster", "2026-11-03")["record"]
    store.marker("roster", "2026-11-03").unlink()
    clock.set("2026-11-03T08:25:00+09:00")
    store.seal("roster", "2026-11-03", {**original, "label": late_label})
    row = evaluation.observation(store, "2026-11-03", "005930", "company")
    stats = evaluation.measure([row])
    assert row["auxiliary_only"] and not row["complete"] and not row["valid_pair"]
    assert stats["overall_denominator"] == 1 and stats["coverage_first"]["coverage"] == 0


def test_roster_publication_full_second_bound_and_late_recovery(tmp_path, fake_kernel):
    clock = Clock("2026-11-03T08:24:59.500000+09:00")
    store = Store(tmp_path / "own", clock=clock)
    class Candidates:
        def candidates(self, *_):
            return []
    value = roster.make_roster(store, Candidates(), None, None, plan(), population=({}, {}, {}, set()))
    assert store.proof("roster", "2026-11-03")["deadline"] == plan()["times"]["roster"]
    assert not store.proof("roster", "2026-11-03")["valid"]
    clock.sleep(5)
    assert roster.make_roster(store, Candidates(), None, None, plan()) == value
    other = Store(tmp_path / "late", clock=clock)
    assert roster.make_roster(other, Candidates(), None, None, plan(), population=({}, {}, {}, set()))["label"] == "reconstructed"


def test_reconstructed_snapshot_remains_auxiliary(tmp_path, fake_kernel):
    store, _, clock = full_capture(tmp_path)
    key = "2026-11-03.s0830"
    snapshot = store.proof("snapshot", key)["record"]
    store.marker("snapshot", key).unlink()
    clock.set("2026-11-03T08:30:00+09:00")
    replacement = store.seal("snapshot", key, {**snapshot, "label": "reconstructed"})
    pre = store.proof("preseal", "2026-11-03")["record"]
    store.marker("preseal", "2026-11-03").unlink()
    clock.set("2026-11-03T08:59:00+09:00")
    store.seal("preseal", "2026-11-03", {**pre,
        "snapshot_hashes": {**pre["snapshot_hashes"], "s0830": replacement["record_sha256"]}},
        deadline="2026-11-03T08:59:30+09:00")
    assert not evaluation.observation(store, "2026-11-03", "005930", "company")["complete"]


@pytest.mark.parametrize("kind,expected", [("sample", "unknown"), ("first_delayed", "observed"), ("first_delayed_auction", "observed"),
                                            ("halt", "confirmed_absent"), ("failure", "unknown")])
def test_post_completion_requires_provider_first_trade_or_official_absence(tmp_path, fake_kernel, kind, expected):
    clock = Clock()
    store = Store(tmp_path / "own", clock=clock)
    simple_roster(store)
    profile = profile_data()
    profile["trade"]["first_trade_flag"] = "trade.first_trade_flag"
    profile["first_trade_values"] = ["FIRST"]
    def transform(value):
        value["trade"].update(auction_flag="AUCTION" if kind == "first_delayed_auction" else "",
                              first_trade_flag="FIRST" if kind == "first_delayed" else "")
        value["trade"]["status"] = "DELAY" if kind.startswith("first_delayed") else "HALT" if kind == "halt" else "NORMAL"
        return value
    capture = recorder.Recorder(store, Quotes(clock, transform), Profile(profile), plan(), clock_status=good_clock)
    clock.set("2026-11-03T09:02:00+09:00" if kind.startswith("first_delayed") else "2026-11-03T09:00:01+09:00")
    if kind != "failure":
        capture.block("005930", "event", book=False, phase="post", deadline=clock() + timedelta(seconds=2))
    clock.set("2026-11-03T09:05:00+09:00")
    opening = capture.seal_post()["record"]["states"]["005930"]
    assert opening["state"] == expected
    if kind.startswith("first_delayed"):
        assert opening["opening_category"] == "opening_delayed"
        assert opening["first_print"]["provider_at"] == "2026-11-03T09:02:00+09:00"
        assert opening["first_60s_sampled_prints"] == []
    elif kind == "sample":
        assert opening["first_print"] is None and opening["earliest_sampled_print"]


@pytest.mark.parametrize("confirmation", ["first_trade", "auction"])
@pytest.mark.parametrize("provider_time,category,latency_seconds", [
    ("090200", "opening_delayed", 0),
    ("090000", None, 120),
])
def test_normal_opening_category_uses_confirmed_first_event_not_receive_time(
        tmp_path, fake_kernel, confirmation, provider_time, category, latency_seconds):
    clock = Clock()
    store = Store(tmp_path / "own", clock=clock)
    simple_roster(store)
    profile = profile_data()
    profile["trade"]["first_trade_flag"] = "trade.first_trade_flag"
    profile["first_trade_values"] = ["FIRST"]
    def transform(value):
        value["trade"].update(status="NORMAL", provider_time=provider_time,
                              first_trade_flag="FIRST" if confirmation == "first_trade" else "",
                              auction_flag="AUCTION" if confirmation == "auction" else "")
        return value
    capture = recorder.Recorder(store, Quotes(clock, transform), Profile(profile), plan(), clock_status=good_clock)
    clock.set("2026-11-03T09:02:00+09:00")
    raw = capture.block("005930", "event", book=False, phase="post", deadline=clock() + timedelta(seconds=2))
    assert raw["normalized"]["status"] == "NORMAL"
    assert raw["normalized"]["opening_delayed"] is False
    clock.set("2026-11-03T09:05:00+09:00")
    opening = capture.seal_post()["record"]["states"]["005930"]
    assert opening["state"] == "observed"
    assert opening["opening_category"] == (category or ("auction_confirmed" if confirmation == "auction" else "unknown"))
    first = opening["first_print"]
    assert first["provider_at"] == raw["normalized"]["provider_at"]
    assert first["received_utc"] == raw["envelope"]["received_utc"]
    assert aware(first["received_utc"]) == aware("2026-11-03T09:02:00+09:00")
    assert (aware(first["received_utc"]) - aware(first["provider_at"])).total_seconds() == latency_seconds
    assert opening["first_60s_sampled_prints"] == []


def test_final_registry_registration_recovers_without_recomputation_and_links_approved_hashes(tmp_path, monkeypatch, fake_kernel):
    store, clock, state = runtime_setup(tmp_path, monkeypatch)
    evidence = store.blob(b"offline bug-fix regression validation")
    state["fingerprint"] = {"code_sha256": "d" * 64,
        "files": {"backtesting/experiments/xo001/recorder.py": "d" * 64}}
    before = store.get("activation", "activation")["binding"]
    after = {**before, **state["fingerprint"]}
    amendment = activation.bugfix(store, after, reason="review corrections", evidence_hashes=[evidence])
    registration = SimpleNamespace(commit_hash=activation.PREREG_COMMIT)
    monkeypatch.setattr(registry, "verify_preregistration", lambda *_: registration)
    root = tmp_path / "registered_checkout"
    original = registry.record_trials
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("simulated crash after seal")
    monkeypatch.setattr(registry, "record_trials", interrupted)
    with pytest.raises(RuntimeError, match="simulated crash"):
        evaluation.evaluate(store, Calendar(), EmptyBB003(), registry_root=root)
    sealed = store.proof("evaluation", "final")
    assert sealed["record"]["verification"]["approved_amendment_sha256"] == [amendment["record_sha256"]]
    monkeypatch.setattr(registry, "record_trials", original)
    monkeypatch.setattr(evaluation, "measure", lambda *_: pytest.fail("recomputed frozen measurement"))
    recovered = evaluation.evaluate(store, NoInputs(), NoInputs(), registry_root=root)
    assert recovered == sealed["record"]
    # Includes recovery from a crash after the append but before any receipt.
    assert evaluation.evaluate(store, NoInputs(), NoInputs(), registry_root=root) == recovered
    path = root / registry.REGISTRY_PATH
    with path.open() as handle:
        rows = registry._registry_rows(handle)
    assert len(rows) == 1 and rows[0]["verdict"] == "measurement_insufficient"
    assert rows[0]["variant"] == "measurement"
    metrics = json.loads(rows[0]["metrics_json"])
    assert metrics["result_sha256"] == sealed["record_sha256"]
    assert metrics["code_sha256"] == "d" * 64 and metrics["environment_sha256"] == "b" * 64
    assert metrics["approved_amendment_sha256"] == [amendment["record_sha256"]]
    assert metrics["strategy_test"] is False


def test_healthy_order_guard_runs_after_tls_and_immediately_before_http(tmp_path, monkeypatch):
    from backtesting.experiments.xo001.diagnostic import OrderPermit
    clock = Clock("2026-11-03T08:59:05+09:00")
    events = []
    body = {"intent": "synthetic"}
    permit = OrderPermit("VTTC0012U", sha(encoded(body)), clock(), clock() + timedelta(seconds=25))
    class Response:
        status = 200
        def read(self, _):
            return b'{"rt_cd":"0"}'
        def getheaders(self):
            return []
    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass
        def connect(self):
            assert not permit.used
            events.append("TLS_connected")
            clock.sleep(.2)
        def request(self, *_args, **_kwargs):
            assert permit.used
            events.append("HTTP_written")
        def getresponse(self):
            return Response()
        def close(self):
            events.append("closed")
    def clock_status():
        events.append("clock_checked")
        return good_clock()
    monkeypatch.setattr(kis.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(recorder, "clock_evidence", clock_status)
    rate = RateManager(tmp_path / "control", clock=clock, monotonic=lambda: clock.mono, sleeper=clock.sleep)
    client = kis.Client(credentials(), tmp_path / "own", rate, clock=clock)
    result = client._wire("POST", kis.ORDER, "VTTC0012U", priority="event", deadline=permit.end,
                          token="fake-token", permit=permit, body=body)
    assert result["error"] is None
    assert events == ["TLS_connected", "clock_checked", "HTTP_written", "closed"]
    with pytest.raises(kis.NotTransmitted, match="post-connect transmission guard"):
        kis.HTTPS().request("POST", kis.BASE + kis.ORDER, headers={"tr_id": "VTTC0012U"})


def test_registry_conflicting_row_fails_without_duplicate_or_result_rewrite(tmp_path, monkeypatch, fake_kernel):
    store, _, _ = runtime_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "verify_preregistration", lambda *_: SimpleNamespace(commit_hash=activation.PREREG_COMMIT))
    root = tmp_path / "registered_checkout"
    result = evaluation.evaluate(store, Calendar(), EmptyBB003(), registry_root=root)
    path = root / registry.REGISTRY_PATH
    content = path.read_bytes().replace(b"measurement_insufficient", b"measurement_complete")
    path.write_bytes(content)
    with pytest.raises(Refused, match="registry conflicts"):
        evaluation.evaluate(store, NoInputs(), NoInputs(), registry_root=root)
    assert path.read_bytes() == content
    assert store.get("evaluation", "final") == result


def test_coordinator_registry_write_scope_only_applies_to_explicit_final_evaluate(tmp_path):
    # Execute only the shell's argument construction with synthetic local paths.
    import os
    import subprocess
    root = tmp_path / "registry"
    (root / ".git").mkdir(parents=True)
    (root / "research/experiments").mkdir(parents=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "id").write_text('#!/bin/bash\necho 0\n')
    (binary / "systemd-run").write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    for name in ("id", "systemd-run"):
        (binary / name).chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"]}
    command = ["bash", "deploy/collector-xo001/coordinator.sh"]
    output = subprocess.check_output([*command, "evaluate", "--registry-root", str(root), "--execute"], env=env, text=True)
    assert "--property=ReadWritePaths=" + str(root / "research/experiments") in output.splitlines()
    normal = subprocess.check_output([*command, "reconcile", "--execute"], env=env, text=True)
    assert str(root) not in normal
    assert subprocess.run([*command, "evaluate", "--execute"], env=env, capture_output=True).returncode != 0
