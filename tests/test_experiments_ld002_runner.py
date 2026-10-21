from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from backtesting.experiments.ld002 import CODEX_VERSION, Refused
from backtesting.experiments.ld002.inputs import payload, validate
from backtesting.experiments.ld002.runner import LlmRunner, probe_identity
from backtesting.experiments.ld002.storage import attempts, cutoff, digest, seal, verified
from ld002_fixtures import CODES, Clock, FakeCodex, identity, output, snapshot


def runner(root, process, clock=None, probe=None):
    clock = clock or Clock()
    return LlmRunner(root, identity(), process=process, identity_probe=probe or identity, clock=clock, synthetic=True)


@pytest.mark.parametrize("change", [
    lambda o: o.update(invest_level=1), lambda o: o.update(invest_level=True),
    lambda o: o.update(invest_level=100.0), lambda o: o.update(holdings=[]),
    lambda o: o.update(invest_level=0), lambda o: o.update(rationale="가" * 601),
    lambda o: o.update(memo="나" * 801), lambda o: o.update(unexpected=True),
    lambda o: o["holdings"].append(o["holdings"][0]),
    lambda o: o["holdings"][0].update(code="999999"),
    lambda o: o["holdings"][0].update(conviction=0), lambda o: o["holdings"][0].update(conviction=True),
    lambda o: o["holdings"][0].update(conviction=3.0),
    lambda o: o.update(holdings=[{"code": c, "conviction": 1} for c in CODES]),
])
def test_invalid_outputs_are_rejected(change):
    answer = output()
    change(answer)
    with pytest.raises((Refused, ValidationError)):
        validate(answer, payload(snapshot())["schema"])


@pytest.mark.parametrize("level", [0, 25, 50, 75, 100])
def test_level_and_text_length_boundaries(level):
    answer = output([] if not level else CODES[:20], level)
    answer.update(rationale="가" * 600, memo="나" * 800)
    validate(answer, payload(snapshot())["schema"])


def test_empty_universe_still_accepts_cash_output_without_invalid_empty_enum():
    validate(output([], 0), payload(snapshot(codes=[]))["schema"])


def test_single_fixed_reask_same_input_then_no_more_calls_on_restart(tmp_path):
    bad = output()
    bad["holdings"].append(bad["holdings"][0])
    process = FakeCodex([bad, output()])
    call = runner(tmp_path, process)
    data = payload(snapshot())
    result = call.decide(data, day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert result["valid"] and len(result["attempts"]) == 2
    assert process.calls[1][1]["input"] == process.calls[0][1]["input"] + "\n" + data["reask"]
    assert "--ephemeral" in process.calls[0][0] and process.calls[0][1]["timeout"] == 480
    assert call.decide(data, day="2026-11-16", deadline=cutoff("2026-11-16", 12))["valid"]
    assert len(process.calls) == 2


def test_two_invalid_attempts_exhaust_the_day(tmp_path):
    bad = {**output(), "memo": "가" * 801}
    process = FakeCodex([bad])
    call = runner(tmp_path, process)
    data = payload(snapshot())
    for _ in range(2):
        assert not call.decide(data, day="2026-11-16", deadline=cutoff("2026-11-16", 12))["valid"]
    assert len(process.calls) == 2 and len(attempts(tmp_path)) == 2


@pytest.mark.parametrize("item_type", ["command_execution", "file_read", "web_search", "mcp_tool_call", "unknown_tool"])
def test_tool_use_is_invalid_and_preserved_in_both_attempts(tmp_path, item_type):
    process = FakeCodex(extra_events=[{"type": "item.completed", "item": {"type": item_type}}])
    result = runner(tmp_path, process).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert not result["valid"] and len(process.calls) == 2
    assert item_type in result["attempts"][0]["stdout"]


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("fake", 480), OSError("interrupted"),
    ValueError("malformed output")])
def test_timeout_process_failure_and_invalid_json_consume_attempts(tmp_path, failure):
    process = FakeCodex([failure])
    result = runner(tmp_path, process).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert not result["valid"] and len(attempts(tmp_path)) == 2


def test_quota_is_counted_and_logged(tmp_path):
    def quota(*a, **k):
        return SimpleNamespace(returncode=1, stdout=json.dumps({"type": "error", "message": "usage limit"}), stderr="quota")
    result = runner(tmp_path, FakeCodex([quota])).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert len(result["attempts"]) == 2 and all("usage_limit" in a["error"] for a in result["attempts"])


def test_unknown_event_and_inconsistent_final_message_are_invalid(tmp_path):
    process = FakeCodex(extra_events=[{"type": "surprise"}])
    assert not runner(tmp_path, process).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))["valid"]


@pytest.mark.parametrize("field", ["model", "codex_version", "executable_sha256", "artifacts"])
def test_identity_drift_before_call_is_permanent_model_changed(tmp_path, field):
    changed = {**identity(), field: "changed"}
    process = FakeCodex()
    result = runner(tmp_path, process, probe=lambda: changed).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert result["reason"] == "model_changed" and not process.calls
    assert verified(tmp_path / "model_changed.json")["status"] == "model_changed"
    again = runner(tmp_path, process).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert again["reason"] == "model_changed" and not process.calls


def test_post_call_drift_discards_an_otherwise_valid_answer(tmp_path):
    current = [identity()]
    process = FakeCodex(after=lambda: current.__setitem__(0, {**identity(), "executable_sha256": "changed"}))
    result = runner(tmp_path, process, probe=lambda: current[0]).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert result["reason"] == "model_changed" and len(process.calls) == 1 and result["output"] is None


def test_unverifiable_identity_stops_before_a_model_call(tmp_path):
    def unavailable():
        raise OSError("identity unavailable")
    process = FakeCodex()
    result = runner(tmp_path, process, probe=unavailable).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert result["reason"] == "model_changed" and not process.calls


def test_drift_in_event_stream_is_a_terminal_identity_change(tmp_path):
    process = FakeCodex(extra_events=[{"type": "turn.started", "model": "other"}])
    result = runner(tmp_path, process).decide(payload(snapshot()), day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert result["reason"] == "model_changed" and len(process.calls) == 1


def test_pending_reservation_counts_as_interrupted_then_one_reask(tmp_path):
    data = payload(snapshot())
    key = digest(["2026-11-16", data, False])
    seal(tmp_path / "attempts/000001.started.json", {"number": 1, "key": key, "day": "2026-11-16", "format_trial": False, "try": 0})
    process = FakeCodex()
    result = runner(tmp_path, process).decide(data, day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    assert len(result["attempts"]) == 2 and result["attempts"][0]["state"] == "interrupted" and len(process.calls) == 1


def test_torn_budget_and_changed_same_day_input_refuse_reset(tmp_path):
    process = FakeCodex()
    call = runner(tmp_path, process)
    data = payload(snapshot())
    call.decide(data, day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    with pytest.raises(Refused, match="cannot replace"):
        call.decide({**data, "prompt": "changed"}, day="2026-11-16", deadline=cutoff("2026-11-16", 12))
    (tmp_path / "attempts/000001.started.json").unlink()
    with pytest.raises(Refused, match="reservation"):
        attempts(tmp_path)


def test_synthetic_format_cap_is_ten_and_uses_same_attempt_journal(tmp_path):
    call, process = None, FakeCodex()
    call = runner(tmp_path, process)
    for i in range(11):
        result = call.decide(payload(snapshot()), day=f"format:{i}", deadline=cutoff("2026-11-16", 12), format_trial=True)
        assert result["valid"] == (i < 10)
    assert len(process.calls) == len(attempts(tmp_path)) == 10


def test_decision_at_deadline_never_starts_a_call(tmp_path):
    clock, process = Clock("2026-11-16T12:00:00+09:00"), FakeCodex()
    assert runner(tmp_path, process, clock).decide(payload(snapshot()), day="2026-11-16", deadline=clock())["reason"] == "decision_late"
    assert not process.calls


def test_binary_hash_and_cli_version_probe_uses_fake_only(tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("fixture binary")
    calls = []
    def fake(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=CODEX_VERSION, stderr="")
    result = probe_identity(process=fake, executable=binary)
    assert calls == [[str(binary), "--version"]] and result["executable_sha256"]


def test_node_launcher_pins_native_binary_and_detects_post_probe_change(tmp_path):
    launcher = tmp_path / "codex/bin/codex.js"
    native = tmp_path / "codex/vendor/fixture-linux/codex/codex"
    launcher.parent.mkdir(parents=True)
    native.parent.mkdir(parents=True)
    launcher.write_text("fixture launcher")
    native.write_text("fixture native")
    def version(*a, **k):
        return SimpleNamespace(returncode=0, stdout=CODEX_VERSION, stderr="")
    result = probe_identity(process=version, executable=launcher)
    assert set(result["executable_artifacts"]) == {str(launcher), str(native)}
    def changed(*a, **k):
        native.write_text("changed native")
        return version()
    with pytest.raises(Refused, match="changed"):
        probe_identity(process=changed, executable=launcher)


def test_launcher_with_unverifiable_native_identity_refuses(tmp_path):
    launcher = tmp_path / "codex/bin/codex.js"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("fixture launcher")
    with pytest.raises(Refused, match="native executable identity unavailable"):
        probe_identity(process=lambda *a, **k: pytest.fail("cannot probe before artifact identity"), executable=launcher)
