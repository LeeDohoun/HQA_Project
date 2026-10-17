"""LH003 decisions use injected Python fakes; real CLI and holdout are forbidden."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from backtesting import holdout
from backtesting.experiments.lh001.arms import aggregate
from backtesting.experiments.lh003 import CALL_CAP, CODEX_VERSION, PROMPT_DIR, Refused
from backtesting.experiments.lh003 import calendar, inputs, workflow
from backtesting.experiments.lh003.runner import LlmRunner, check_committed
from backtesting.experiments.lh003.simulation import FakeCodex
from backtesting.experiments.lh003.storage import attempts, aware, digest, file_hash, read, seal, verified
from test_experiments_lh001 import synthetic, bundle_for


@pytest.fixture(autouse=True)
def forbid_real_calls_and_holdout(monkeypatch):
    original = subprocess.run
    def process(command, *args, **kwargs):
        if command[0] == "codex":
            pytest.fail("real Codex subprocess forbidden")
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", process)
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("real HoldoutSession forbidden"))


@pytest.fixture
def bundle(synthetic):
    return {**bundle_for(synthetic), "decision_date": "2026-11-13"}


class Clock:
    def __init__(self, at="2026-11-13T18:00:00+09:00"):
        self.at = aware(at)
    def __call__(self):
        return self.at


class Process(FakeCodex):
    def __init__(self, *, outputs=None, forbidden=None, fail=None, drift_after=None):
        super().__init__()
        self.outputs, self.forbidden, self.fail, self.drift_after = outputs, forbidden, fail, drift_after
        self.prompts, self.commands, self.cwds = [], [], []
    def __call__(self, command, **kwargs):
        if command == ["codex", "--version"]:
            return SimpleNamespace(stdout="codex-cli changed" if self.drift_after is not None and self.execs >= self.drift_after
                                   else CODEX_VERSION, stderr="", returncode=0)
        self.prompts.append(kwargs["input"])
        self.commands.append(command)
        self.cwds.append((Path(kwargs["cwd"]), list(Path(kwargs["cwd"]).iterdir())))
        if self.fail:
            self.execs += 1
            raise self.fail
        response = super().__call__(command, **kwargs)
        output_path = Path(command[command.index("-o") + 1])
        output = self.outputs[self.execs - 1] if self.outputs else json.loads(output_path.read_text())
        output_path.write_text(json.dumps(output))
        events = [{"type": "turn.started"}]
        if self.forbidden:
            events.append({"type": "item.completed", "item": {"type": self.forbidden}})
        events += [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(output)}},
                   {"type": "turn.completed"}]
        return SimpleNamespace(stdout="\n".join(json.dumps(e) for e in events), stderr="raw fake stderr", returncode=0)


def instance(root, process, clock=None, prompt_dir=PROMPT_DIR):
    return LlmRunner(root, subprocess_call=process, clock=clock or Clock(), prompt_dir=prompt_dir)


def test_v3_dynamic_schema_array_lengths_enum_uniqueness_and_cross_overlap(bundle):
    payload = inputs.render(bundle)
    ids = sorted(payload["id_mapping"])
    good = {"ranked": ids[:30], "avoid": ids[30:40]}
    inputs.validate(good, payload["schema"])
    for broken in ({**good, "ranked": ids[:29]}, {**good, "avoid": ids[30:39]},
                   {**good, "ranked": [ids[0]] * 30}, {**good, "avoid": ["unknown"] + ids[31:40]},
                   {**good, "ranked": [{"id": i} for i in ids[:30]]}, {**good, "extra": "forbidden"}):
        with pytest.raises(ValidationError):
            inputs.validate(broken, payload["schema"])
    with pytest.raises(Refused, match="overlap"):
        inputs.validate({**good, "avoid": ids[20:30]}, payload["schema"])
    assert payload["schema_sha256"] == digest(payload["schema"])
    assert payload["prompt_sha256"] == digest(payload["prompt"])
    assert "UNTRUSTED_DATA" in payload["prompt"] and "파일 읽기" in payload["prompt"]
    assert all("$schema" not in s for s in (payload["schema"],))


@pytest.mark.parametrize("filename", ["v3_common.txt", "v3_c_num.txt", "v3_reask.txt", "v3_c_num.schema.json"])
def test_leak_checker_covers_final_prompt_dynamic_schema_and_fixed_reask(tmp_path, bundle, filename):
    directory = tmp_path / "prompts"
    shutil.copytree(PROMPT_DIR, directory)
    path = directory / filename
    if filename.endswith("json"):
        schema = read(path)
        schema["title"] = bundle["stocks"][0]["stock_code"]
        path.write_text(json.dumps(schema))
    else:
        path.write_text(path.read_text() + "\n" + bundle["stocks"][0]["name"])
    with pytest.raises(AssertionError, match="leak"):
        inputs.render(bundle, directory)


def test_A_is_not_capped_and_shown_pool_is_adv_top_300(bundle):
    original = bundle["stocks"][0]
    bundle["stocks"] = [{**original, "stock_code": f"{(i + 1) * 10:06d}", "name": f"기업{i}", "adv_20": i + 1}
                        for i in range(305)]
    bundle["a_codes"] = [s["stock_code"] for s in bundle["stocks"]]
    payload = inputs.render(bundle)
    assert len(payload["candidate_codes"]) == 300 and len(bundle["a_codes"]) == 305
    assert payload["candidate_codes"][0] == bundle["stocks"][-1]["stock_code"]
    assert bundle["stocks"][0]["stock_code"] not in payload["candidate_codes"]


def test_three_independent_repeats_use_empty_external_workspaces_and_frozen_prompt(tmp_path, bundle):
    process = Process()
    payload = inputs.render(bundle)
    result = instance(tmp_path, process).decide(payload, week=0, deadline=calendar.plan()[0]["deadline"])
    assert result["valid"] and len(result["selected"]) == len(result["avoid"]) == 10
    assert process.execs == 3 and len({str(p) for p, _ in process.cwds}) == 3
    assert not any(content for _, content in process.cwds)
    assert process.prompts == [payload["prompt"]] * 3
    for command in process.commands:
        assert command[command.index("-m") + 1] == "gpt-6-luna"
        assert command[-1] == "-" and command[command.index("--sandbox") + 1] == "read-only"
        assert all(flag in command for flag in ("--ephemeral", "--skip-git-repo-check", "--json", "--output-schema"))
    assert len(attempts(tmp_path)) == 3
    assert result["jaccard"] == 1


def test_fixed_reask_does_not_echo_invalid_output_and_preserves_raw_attempts(tmp_path, bundle):
    payload = inputs.render(bundle)
    ids = sorted(payload["id_mapping"])
    good = {"ranked": ids[:30], "avoid": ids[30:40]}
    bad = {"ranked": ["INVALID_RAW_DO_NOT_ECHO"] * 30, "avoid": ids[30:40]}
    process = Process(outputs=[bad, good, good, good])
    result = instance(tmp_path, process).decide(payload, week=0, deadline=calendar.plan()[0]["deadline"])
    assert result["valid"] and process.execs == 4
    assert process.prompts[1] == payload["prompt"] + "\n" + payload["reask"]
    assert "INVALID_RAW_DO_NOT_ECHO" not in process.prompts[1]
    first = verified(tmp_path / "attempts/000001.result.json")
    assert "INVALID_RAW_DO_NOT_ECHO" in first["stdout"] and first["stderr"] == "raw fake stderr"
    assert "error" in first and first["state"] == "invalid"
    assert first["finished_at"] and verified(tmp_path / "attempts/000001.started.json")["started_at"]


@pytest.mark.parametrize("kind", ["command_execution", "file_read", "web_search", "mcp_tool_call", "unknown_tool"])
def test_tool_events_invalidate_every_repeat_with_at_most_one_reask(tmp_path, bundle, kind):
    process = Process(forbidden=kind)
    result = instance(tmp_path, process).decide(inputs.render(bundle), week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == 6
    assert [len(r["attempts"]) for r in result["repeats"]] == [2, 2, 2]


def test_one_failed_repeat_invalidates_decision_without_extra_repeats(tmp_path, bundle):
    payload = inputs.render(bundle)
    ids = sorted(payload["id_mapping"])
    good = {"ranked": ids[:30], "avoid": ids[30:40]}
    process = Process(outputs=[good, {}, {}, good])
    result = instance(tmp_path, process).decide(payload, week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == 4 and len(result["repeats"]) == 3


def test_39_candidates_make_no_identity_or_model_call(tmp_path, bundle):
    bundle["a_codes"] = bundle["a_codes"][:39]
    result = instance(tmp_path, lambda *a, **k: pytest.fail("no call expected")).decide(
        inputs.render(bundle), week=0, deadline=calendar.plan()[0]["deadline"])
    assert result["reasons"] == ["fewer_than_40_candidates"] and not attempts(tmp_path)


def test_timeout_and_interruption_are_durable_and_resume_has_only_one_reask(tmp_path, bundle):
    payload, clock = inputs.render(bundle), Clock()
    process = Process(fail=KeyboardInterrupt("fake kill"))
    with pytest.raises(KeyboardInterrupt):
        instance(tmp_path, process, clock).call(payload, 0, week=0, deadline=calendar.plan()[0]["deadline"])
    assert len(attempts(tmp_path)) == 1
    process = Process(fail=subprocess.TimeoutExpired("fake", 300, output="partial stdout", stderr="partial stderr"))
    result = instance(tmp_path, process, clock).call(payload, 0, week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and len(result["attempts"]) == 2 and process.execs == 1
    assert verified(tmp_path / "attempts/000002.result.json")["stdout"] == "partial stdout"
    assert len(attempts(tmp_path)) == 2
    assert not instance(tmp_path, Process(), clock).call(payload, 0, week=0, deadline=calendar.plan()[0]["deadline"])["valid"]
    assert len(attempts(tmp_path)) == 2


def test_pending_reservation_counts_on_restart(tmp_path, bundle):
    payload = inputs.render(bundle)
    instance(tmp_path, Process()).identity()
    key = digest([0, payload, 0])
    seal(tmp_path / "attempts/000001.started.json", {"number": 1, "key": key})
    process = Process()
    result = instance(tmp_path, process).call(payload, 0, week=0, deadline=calendar.plan()[0]["deadline"])
    assert result["valid"] and process.execs == 1 and len(attempts(tmp_path)) == 2
    assert verified(tmp_path / "attempts/000001.result.json")["state"] == "interrupted"


def test_hard_320_cap_including_prior_interruptions(tmp_path, bundle):
    instance(tmp_path, Process()).identity()
    for n in range(1, 320):
        seal(tmp_path / "attempts" / f"{n:06d}.started.json", {"number": n, "key": "earlier_interrupt"})
    process = Process()
    result = instance(tmp_path, process).decide(inputs.render(bundle), week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == 1 and len(attempts(tmp_path)) == CALL_CAP
    later = Process()
    assert not instance(tmp_path, later).decide(inputs.render(bundle), week=1, deadline=calendar.plan()[1]["deadline"])["valid"]
    assert later.execs == 0


@pytest.mark.parametrize("when", [0, 1])
def test_version_drift_persistently_stops_current_and_subsequent_decisions(tmp_path, bundle, when):
    process = Process(drift_after=when)
    result = instance(tmp_path, process).decide(inputs.render(bundle), week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == when
    assert (tmp_path / "model_stopped.json").exists()
    later = Process()
    result = instance(tmp_path, later).decide(inputs.render(bundle), week=1, deadline=calendar.plan()[1]["deadline"])
    assert not result["valid"] and later.execs == 0


def test_model_name_and_prompt_drift_stop_without_extra_calls(tmp_path, bundle):
    directory = tmp_path / "prompts"
    shutil.copytree(PROMPT_DIR, directory)
    payload = inputs.render(bundle, directory)
    process = Process()
    runner = instance(tmp_path / "model_drift", process, prompt_dir=directory)
    runner.model = "another-model"
    assert not runner.decide(payload, week=0, deadline=calendar.plan()[0]["deadline"])["valid"]
    assert process.execs == 0
    class Changed(Process):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if command[:2] == ["codex", "exec"]:
                path = directory / "v3_c_num.txt"
                path.write_text(path.read_text() + "\nchanged")
            return result
    process = Changed()
    result = instance(tmp_path / "prompt_drift", process, prompt_dir=directory).decide(
        payload, week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == 1


def test_implementation_drift_cannot_be_hidden_by_a_new_clean_commit(tmp_path, bundle, monkeypatch):
    from backtesting.experiments.lh003 import runner as module
    payload, process = inputs.render(bundle), Process()
    runner = instance(tmp_path, process)
    runner.identity()
    monkeypatch.setattr(module, "implementation_hashes", lambda root: {"runner.py": "different committed bytes"})
    result = runner.decide(payload, week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == 0


def test_malformed_events_and_inconsistent_final_file_are_invalid_and_archived(tmp_path, bundle):
    class Malformed(Process):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if command[:2] == ["codex", "exec"]:
                Path(command[command.index("-o") + 1]).write_text("invalid output file bytes")
                result.stdout += "\nmalformed event"
            return result
    process = Malformed()
    result = instance(tmp_path, process).call(inputs.render(bundle), 0, week=0, deadline=calendar.plan()[0]["deadline"])
    assert not result["valid"] and process.execs == 2
    assert verified(tmp_path / "attempts/000001.result.json")["output_raw"] == "invalid output file bytes"


def test_frequency_mean_rank_and_ID_tie_break_are_the_LH002_rule():
    assert aggregate([["B", "A"], ["A", "B"]], 2) == ["A", "B"]
    assert aggregate([["Z", "A"], ["Z", "B"], ["C", "B"]], 2) == ["Z", "B"]


@pytest.fixture
def weekly(tmp_path, bundle, monkeypatch):
    root, source, clock = tmp_path / "state", tmp_path / "source", Clock()
    calendar.check(root, at=clock())
    seal(root / "start.json", {"synthetic": True, "snapshot_path": str(tmp_path / "snapshot"),
                               "snapshot_sha256": "fake", "machine_id": workflow.machine_id()})
    monkeypatch.setattr(workflow, "Snapshot", lambda *a, **k: SimpleNamespace(manifest={"synthetic": True}))
    path = inputs.price_path(source, "2026-11-13")
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps({"trade_date": "2026-11-13", "calendar_status": "verified", "market": market})
                              for market in ("KOSPI", "KOSDAQ")) + "\n")
    manifest = source / "sync.json"
    manifest.write_text(json.dumps({"verified_at": clock().isoformat(), "files": {
        path.relative_to(source).as_posix(): file_hash(path)}}))
    def loader(*args):
        return {"bundle": bundle, "source_hashes": {"fake": "fixtures"}, "snapshot_sha256": "fake"}
    return {"data_dir": source, "root": root, "decision_day": "2026-11-13", "sync_manifest": manifest,
            "repo_root": tmp_path, "clock": clock, "simulation": True, "prepared_loader": loader}


def test_A_and_common_input_are_sealed_before_first_call_and_resume_uses_them(weekly):
    process = Process()
    original = process.__call__
    class Checked:
        def __call__(self, command, **kwargs):
            if command[:2] == ["codex", "exec"]:
                assert (weekly["root"] / "weeks/00/input.json").exists()
                assert (weekly["root"] / "weeks/00/fallback_receipt.json").exists()
            return original(command, **kwargs)
    runner = instance(weekly["root"], Checked(), weekly["clock"])
    result = workflow.run_week(**weekly, runner=runner)
    assert result["valid"] and result["observation"] and len(result["policy_codes"]) == 10
    frozen = digest(verified(weekly["root"] / "weeks/00/input.json"))
    weekly["prepared_loader"] = lambda *a: pytest.fail("source reread on resume")
    again = workflow.run_week(**weekly, runner=instance(weekly["root"], Process(), weekly["clock"]))
    assert again == result and again["input_sha256"] == frozen


def test_crash_resume_reuses_A_inputs_counts_attempt_and_never_reopens_holdout(weekly):
    with pytest.raises(KeyboardInterrupt):
        workflow.run_week(**weekly, runner=instance(weekly["root"], Process(fail=KeyboardInterrupt()), weekly["clock"]))
    assert workflow.load_week(weekly["root"], 0)["policy_codes"] == verified(weekly["root"] / "weeks/00/input.json")["bundle"]["a_codes"]
    weekly["prepared_loader"] = lambda *a: pytest.fail("source reread")
    result = workflow.run_week(**weekly, runner=instance(weekly["root"], Process(), weekly["clock"]))
    assert result["valid"] and len(attempts(weekly["root"])) == 4


def test_late_model_result_uses_timely_A_and_fresh_late_input_is_reconstructed(weekly):
    clock, root = weekly["clock"], weekly["root"]
    class Late(Process):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if command[:2] == ["codex", "exec"]:
                clock.at = aware(calendar.plan()[0]["deadline"])
            return result
    result = workflow.run_week(**weekly, runner=instance(root, Late(), clock))
    assert not result["valid"] and result["observation"] and result["policy_codes"] == result["a_codes"]
    weekly["root"] = root.parent / "late_state"
    shutil.copytree(root / "calendar", weekly["root"] / "calendar")
    seal(weekly["root"] / "start.json", verified(root / "start.json"))
    process = Process()
    result = workflow.run_week(**weekly, runner=instance(weekly["root"], process, clock))
    assert not result["observation"] and result["reconstructed"] and process.execs == 0


def test_missing_decision_day_refuses_cleanly_before_any_input_or_attempt(weekly):
    inputs.price_path(weekly["data_dir"], weekly["decision_day"]).unlink()
    with pytest.raises(Refused, match="decision-day KRX data missing"):
        workflow.run_week(**weekly, runner=instance(weekly["root"], Process(), weekly["clock"]))
    assert not attempts(weekly["root"]) and not (weekly["root"] / "weeks/00/input.json").exists()


def test_unreproducible_A_is_recorded_as_missing_not_zero(weekly):
    weekly["prepared_loader"] = lambda *a: (_ for _ in ()).throw(Refused("missing mandatory financials"))
    with pytest.raises(Refused, match="financials"):
        workflow.run_week(**weekly, runner=instance(weekly["root"], Process(), weekly["clock"]))
    assert workflow.load_week(weekly["root"], 0) is None
    assert list((weekly["root"] / "weeks/00/input_failures").glob("*.json"))


def test_real_call_commit_gate_rejects_uncommitted_implementation(tmp_path, monkeypatch):
    root = tmp_path
    package = root / "backtesting/experiments/lh003"
    package.mkdir(parents=True)
    for index in range(7):
        (package / f"file{index}.py").write_text("# fixture")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(Refused, match="committed"):
        check_committed(root)
