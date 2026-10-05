"""Offline LH002 regression checks; model subprocesses are always injected."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import subprocess
from collections import Counter
from dataclasses import FrozenInstanceError
from fractions import Fraction
from math import comb
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from backtesting import experiment_registry, holdout
from backtesting.experiments import common, hc001
from backtesting.experiments.lh001 import guard_data, workspace
from backtesting.experiments.lh001 import __main__ as cli
from backtesting.experiments.lh001 import arms, evaluate, inputs, probe, probe_v2
from backtesting.experiments.lh001.config import ExperimentConfig, LH001, LH002
from backtesting.experiments.lh001.runner import LlmRunner, StageStopped, check_committed, prompt_hashes, read_json, save_json
from backtesting.experiments.lh002 import __main__ as alias
from test_experiments_lh001 import FakeArmRunner, FakeProcess, bundle_for, extracted, good_result, metric_example, ranked, synthetic


@pytest.fixture(autouse=True)
def no_real_codex(monkeypatch):
    original = subprocess.run

    def process(command, *args, **kwargs):
        if command and command[0] == "codex":
            pytest.fail("real Codex process attempted")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", process)


@pytest.fixture
def offline_repo(tmp_path, monkeypatch):
    fields = {config.experiment_id: yaml.safe_load((config.prompt_dir.parent / "preregistration.md").read_text().split("---", 2)[1])
              for config in (LH001, LH002)}
    monkeypatch.setattr(common, "load_preregistration", lambda experiment, **k: fields[experiment])
    monkeypatch.setattr(experiment_registry, "verify_preregistration",
                        lambda experiment, *a: SimpleNamespace(fields=fields[experiment], commit_hash="synthetic-" + experiment))
    monkeypatch.setattr(holdout, "verify_preregistration", lambda experiment, *a: SimpleNamespace(fields=fields[experiment]))
    return tmp_path


def facts():
    return {"kospi_level": 2500,
            "sectors": [{"name": f"업종{i}", "return": (i - 4) * 0.04} for i in range(9)],
            "stocks": [{"stock_code": f"{10 * (i + 1):06d}", "name": f"합성{i}", "return": i * 0.01} for i in range(30)]}


def scores(control_correct=24, validation_correct=2):
    counts = [min(8, max(0, control_correct - index * 8)) for index in range(6)]
    return [{"month": str(month), "correct": counts[index] if index < 6 else validation_correct, "questions": 8}
            for index, month in enumerate(probe_v2.MONTHS)]


def answers(quiz, correct=8):
    return {"answers": [{"id": row["id"], "option": row["correct"] if index < correct else
                          next(letter for letter in "ABC" if letter != row["correct"]), "confidence": 3, "reason": "기억"}
                         for index, row in enumerate(quiz["questions"])]}


def runner(data_dir, process, config=LH002):
    return LlmRunner(data_dir=data_dir, subprocess_call=process, enforce_committed=False, config=config)


def call_stage1(instance):
    return instance.call("v1_b_stage1.txt", "synthetic table", "v1_b_stage1.schema.json", 0, stage="screening")


def test_config_paths_and_fixed_caps_preserve_default_lh001(tmp_path):
    assert isinstance(LH002, ExperimentConfig)
    assert LH001.budget_cap == LH002.budget_cap == 1100
    assert workspace(tmp_path) == workspace(tmp_path, LH001) == tmp_path / "research/lh001"
    assert workspace(tmp_path, LH002) == tmp_path / "research/lh002"
    assert LH001.prompt_dir != LH002.prompt_dir
    assert (LH001.probe_version, LH002.probe_version) == ("v1", "v2")
    with pytest.raises(FrozenInstanceError):
        LH002.experiment_id = LH001.experiment_id


def test_prompt_copies_have_identical_hashes_and_exclude_v1_probe():
    names = {path.name for path in LH001.prompt_dir.glob("v1*") if not path.name.startswith("v1_probe.")}
    assert len(names) == 10
    assert {path.name for path in LH002.prompt_dir.glob("v1*")} == names
    for name in names:
        assert hashlib.sha256((LH001.prompt_dir / name).read_bytes()).digest() == hashlib.sha256((LH002.prompt_dir / name).read_bytes()).digest()
    assert "65dd9e5" in (LH002.prompt_dir / "README.md").read_text()
    assert {"v2_probe.txt", "v2_probe.schema.json"}.issubset(prompt_hashes(LH002.prompt_dir))


def test_lh001_cache_key_manifest_and_prompt_bytes_keep_legacy_format(tmp_path):
    process = FakeProcess()
    instance = runner(tmp_path, process, LH001)
    result = call_stage1(instance)
    instructions = (LH001.prompt_dir / "v1_common.txt").read_text() + "\n" + (LH001.prompt_dir / "v1_b_stage1.txt").read_text()
    schema = read_json(LH001.prompt_dir / "v1_b_stage1.schema.json")
    version = "v1/v1_b_stage1.txt/" + inputs.digest(instructions)
    assert result["key"] == inputs.digest([version, schema, "synthetic table", 0])
    assert read_json(workspace(tmp_path) / "manifest.json") == {
        "model": "gpt-6-luna", "codex_version": "codex-cli 0.160.0", "prompt_version": "v1", "hashes": prompt_hashes(LH001.prompt_dir)}


def test_cache_budget_and_manifest_are_independent_even_for_identical_strategy_input(tmp_path):
    legacy = runner(tmp_path, FakeProcess(), LH001)
    old_call = call_stage1(legacy)
    save_json(workspace(tmp_path) / "budget.json", {"used": 1100, "cap": 1100})
    before = {str(path): path.read_bytes() for path in workspace(tmp_path).rglob("*") if path.is_file()}
    process = FakeProcess()
    instance = runner(tmp_path, process)
    result = call_stage1(instance)
    assert result["key"] != old_call["key"] and not result["cache_hit"] and process.execs == 1
    assert instance.budget() == {"used": 1, "cap": 1100}
    resumed = FakeProcess()
    assert call_stage1(runner(tmp_path, resumed))["cache_hit"] and resumed.execs == 0
    assert before == {str(path): path.read_bytes() for path in workspace(tmp_path).rglob("*") if path.is_file()}
    manifest = read_json(workspace(tmp_path, LH002) / "manifest.json")
    assert manifest["experiment_id"] == LH002.experiment_id and manifest["probe_version"] == "v2"


@pytest.mark.parametrize("used,execs", [(1100, 0), (1099, 1)])
def test_lh002_budget_cap_is_persistent_and_counts_invalid_attempts(tmp_path, used, execs):
    save_json(workspace(tmp_path, LH002) / "budget.json", {"used": used, "cap": 1100})
    process = FakeProcess(forbidden="file_read")
    with pytest.raises(StageStopped, match="budget"):
        call_stage1(runner(tmp_path, process))
    assert process.execs == execs and runner(tmp_path, FakeProcess()).budget()["used"] == 1100
    assert not workspace(tmp_path).exists()


@pytest.mark.parametrize("template,schema_name,is_probe", [
    ("v2_probe.txt", "v2_probe.schema.json", True),
    ("v1_b_stage1.txt", "v1_b_stage1.schema.json", False),
])
def test_only_v2_probe_omits_common_memory_ban(tmp_path, template, schema_name, is_probe):
    quiz = probe_v2.make_questions("2024-01", facts())
    process = FakeProcess(answers(quiz) if is_probe else {"ranked": ranked(["a", "b", "c"])})
    sent = []

    def capture(command, **kwargs):
        if command[1] == "exec":
            sent.append(kwargs["input"])
        return process(command, **kwargs)

    instance = runner(tmp_path, capture)
    result = instance.call(template, probe_v2.render_questions(quiz) if is_probe else "synthetic table", schema_name, 0,
                           validate=probe_v2.validate_answers if is_probe else None, stage="probe" if is_probe else "screening")
    assert result["valid"]
    common_text = (LH001.prompt_dir / "v1_common.txt").read_text()
    assert (common_text in sent[0]) is (not is_probe)
    assert ("기억을 사용하지 마세요" in sent[0]) is (not is_probe)
    if is_probe:
        assert sent[0].startswith((LH002.prompt_dir / "v2_probe.txt").read_text() + "\n<UNTRUSTED_DATA>")
        assert "기억에 근거해" in sent[0] and "확신도를 낮게" in sent[0]
        assert result["prompt_version"].startswith("v2/")


def test_prompt_preflight_checks_new_v2_files(monkeypatch):
    checked = []

    def git(command, **kwargs):
        checked.append(command[-1])
        return SimpleNamespace(returncode=1 if command[-1].endswith("v2_probe.txt") else 0)

    monkeypatch.setattr(subprocess, "run", git)
    with pytest.raises(StageStopped, match="v2_probe.txt"):
        check_committed(LH002.prompt_dir, LH002.prompt_dir.parents[3])
    assert any(path.endswith("v1_common.txt") for path in checked)
    assert any(path.endswith("v2_probe.schema.json") for path in checked)


@pytest.mark.parametrize("violation", ["count", "duplicate", "option", "confidence_low", "confidence_high", "confidence_type", "reason", "extra"])
def test_v2_schema_and_validator_fail_closed(violation):
    output = answers(probe_v2.make_questions("2024-01", facts()))
    if violation == "count":
        output["answers"].pop()
    elif violation == "duplicate":
        output["answers"][0]["id"] = "q2"
    elif violation == "option":
        output["answers"][0]["option"] = "D"
    elif violation.startswith("confidence"):
        output["answers"][0]["confidence"] = {"confidence_low": 0, "confidence_high": 6, "confidence_type": True}[violation]
    elif violation == "reason":
        output["answers"][0]["reason"] = "가" * 101
    else:
        output["extra"] = 1
    with pytest.raises((ValueError, ValidationError)):
        Draft202012Validator(read_json(LH002.prompt_dir / "v2_probe.schema.json")).validate(output)
        probe_v2.validate_answers(output)


def test_probe_schedule_determinism_and_no_answer_or_return_leak():
    assert len(probe_v2.MONTHS) == 27
    assert [str(month) for month in probe_v2.MONTHS[:6]] == [f"2024-{month:02d}" for month in range(1, 7)]
    assert probe_v2.MONTHS[6] == pd.Period("2025-01") and probe_v2.MONTHS[-1] == pd.Period("2026-09")
    quiz = probe_v2.make_questions("2024-01", facts())
    reversed_facts = facts()
    for field in ("sectors", "stocks"):
        reversed_facts[field].reverse()
    assert quiz == probe_v2.make_questions("2024-01", reversed_facts)
    assert quiz != probe_v2.make_questions("2024-02", facts())
    assert [row["id"] for row in quiz["questions"]] == [f"q{number}" for number in range(1, 9)]
    assert all({option["id"] for option in row["options"]} == set("ABC") for row in quiz["questions"])
    rendered = probe_v2.render_questions(quiz)
    assert "correct" not in rendered and "return" not in rendered and "read_note" not in rendered


def test_kospi_positions_and_shuffled_answer_letters_are_uniform_and_decoys_match_ratios():
    positions, letters = Counter(), Counter()
    raw = facts()
    for month in pd.period_range("1800-01", periods=2400, freq="M"):
        question = probe_v2.make_questions(month, raw)["questions"][0]
        levels = sorted(float(option["label"]) for option in question["options"])
        actual = next(float(option["label"]) for option in question["options"] if option["id"] == question["correct"])
        assert actual == 2500
        position = levels.index(actual)
        positions[position] += 1
        letters[question["correct"]] += 1
        assert [value / actual for value in levels] == pytest.approx(((1, 1.15, 1.32), (0.87, 1, 1.15), (0.76, 0.87, 1))[position])
    assert set(positions) == {0, 1, 2} and set(letters) == set("ABC")
    assert all(0.30 < count / 2400 < 0.37 for count in [*positions.values(), *letters.values()])


def test_sector_and_stock_triples_are_disjoint_and_sector_returns_are_separated():
    raw = facts()
    sector_returns = {row["name"]: row["return"] for row in raw["sectors"]}
    stock_returns = {row["name"] + "(" + row["stock_code"] + ")": row["return"] for row in raw["stocks"]}
    for month in probe_v2.MONTHS:
        quiz = probe_v2.make_questions(month, raw)
        sector_groups = [{option["label"] for option in q["options"]} for q in quiz["questions"][1:3]]
        stock_groups = [{option["label"] for option in q["options"]} for q in quiz["questions"][3:]]
        assert len(set.union(*sector_groups)) == 6 and len(set.union(*stock_groups)) == 15
        for group in sector_groups:
            assert all(abs(sector_returns[a] - sector_returns[b]) >= 0.03 - 1e-12 for a, b in itertools.combinations(group, 2))
        for q in quiz["questions"][1:]:
            values = sector_returns if q["id"] in ("q2", "q3") else stock_returns
            correct = next(option["label"] for option in q["options"] if option["id"] == q["correct"])
            assert values[correct] == max(values[option["label"]] for option in q["options"])


def test_sector_pair_search_finds_a_jointly_feasible_pair_at_exact_three_point_boundary():
    raw = facts()
    raw["sectors"] = [{"name": f"sector{i}", "return": value} for i, value in enumerate((-.03, -.02, 0, .01, .03, .04))]
    for month in probe_v2.MONTHS:
        assert len(probe_v2.make_questions(month, raw)["questions"]) == 8


@pytest.mark.parametrize("missing", ["level", "sector_count", "sector_separation", "sector_disjoint", "sector_duplicate",
                                    "sector_return", "stock_count", "stock_duplicate", "stock_return", "stock_tie", "rounded_level"])
def test_question_generation_fails_loudly_instead_of_shortening(missing):
    raw = facts()
    if missing == "level":
        raw.pop("kospi_level")
    elif missing == "sector_count":
        raw["sectors"] = raw["sectors"][:5]
    elif missing == "sector_separation":
        raw["sectors"] = [{"name": str(i), "return": i * .001} for i in range(9)]
    elif missing == "sector_disjoint":
        raw["sectors"] = [{"name": str(i), "return": value} for i, value in enumerate((0, .04, .08, .08, .08, .08))]
    elif missing == "sector_duplicate":
        raw["sectors"][0] = raw["sectors"][1]
    elif missing == "sector_return":
        raw["sectors"][0]["return"] = np.nan
    elif missing == "stock_count":
        raw["stocks"].pop()
    elif missing == "stock_duplicate":
        raw["stocks"][0] = raw["stocks"][1]
    elif missing == "stock_return":
        raw["stocks"][0].pop("return")
    elif missing == "stock_tie":
        for stock in raw["stocks"]:
            stock["return"] = 0
    else:
        raw["kospi_level"] = .001
    with pytest.raises(probe_v2.ProbeUnavailable):
        probe_v2.make_questions("2024-01", raw)


def write_probe_archive(root, months, sessions):
    raw = facts()
    benchmarks = {}
    for month in months:
        for day, current in zip(probe_v2.month_ends(month, sessions), (False, True)):
            path = root / "market/krx_daily" / str(day.year) / f"{day:%Y%m%d}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = [{"stock_code": row["stock_code"], "stock_name": row["name"], "market": "KOSPI", "market_cap": 1e10 + i,
                     "close": 1000 * (1 + row["return"] if current else 1), "open": "forbidden-unused", "signals": "forbidden-unused"}
                    for i, row in enumerate(raw["stocks"])]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            benchmarks[day, "코스피"] = 2500
            for row in raw["sectors"]:
                benchmarks[day, row["name"]] = 100 * (1 + row["return"] if current else 1)
            benchmarks[day, "코스피 200"] = 100
    path = root / "market_context/benchmarks.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{"trade_date": day.date().isoformat(), "series": "KOSPI", "index_name": name, "close": value}
            for (day, name), value in benchmarks.items()]
    rows.append({"trade_date": "2024-01-15", "series": "KOSPI", "index_name": "코스피", "close": "forbidden-non-month-end"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.mark.parametrize("month", ["2024-01", "2024-06", "2026-01", "2026-09"])
def test_dedicated_reader_extends_to_controls_and_never_guards_or_evaluates(tmp_path, monkeypatch, month):
    sessions = pd.bdate_range("2023-12-01", "2026-10-01")
    write_probe_archive(tmp_path, [month], sessions)
    monkeypatch.setattr(probe, "guard_data", lambda *a, **k: pytest.fail("contamination reader must not guard"))
    monkeypatch.setattr(holdout, "guard_period", lambda *a, **k: pytest.fail("holdout claim attempted"))
    monkeypatch.setattr(inputs, "load_history", lambda *a, **k: pytest.fail("daily strategy prices read"))
    monkeypatch.setattr(evaluate, "observations", lambda *a, **k: pytest.fail("strategy returns evaluated"))
    measured = probe_v2.read_contamination_facts([month], data_dir=tmp_path, sessions=sessions)[month]
    assert set(measured) == {"kospi_level", "sectors", "stocks", "read_note", "dates"}
    assert measured["kospi_level"] == 2500 and len(measured["stocks"]) == 30
    assert all(set(row) == {"stock_code", "name", "return"} for row in measured["stocks"])
    assert "코스피 200" not in {row["name"] for row in measured["sectors"]}
    assert "not a strategy evaluation" in measured["read_note"]
    assert len(probe_v2.make_questions(month, measured)["questions"]) == 8
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


def test_reader_missing_top30_close_fails_before_any_calls(tmp_path):
    sessions = pd.bdate_range("2023-12-01", "2024-02-01")
    write_probe_archive(tmp_path, ["2024-01"], sessions)
    current = probe_v2.month_ends("2024-01", sessions)[1]
    path = tmp_path / "market/krx_daily" / str(current.year) / f"{current:%Y%m%d}.jsonl"
    path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    with pytest.raises(probe_v2.ProbeUnavailable, match="missing top-30"):
        probe_v2.read_contamination_facts(["2024-01"], data_dir=tmp_path, sessions=sessions)


@pytest.mark.parametrize("correct,contaminated", [(0, False), (6, False), (7, True), (8, True)])
def test_score_uses_seven_of_eight_rule(correct, contaminated):
    quiz = probe_v2.make_questions("2026-01", facts())
    assert probe_v2.score(quiz, answers(quiz, correct)) == {"month": "2026-01", "correct": correct,
                                                         "questions": 8, "contaminated": contaminated}


@pytest.mark.parametrize("correct,total", [(0, 48), (21, 48), (22, 48), (48, 48), (31, 72), (32, 72), (18, 40), (19, 40)])
def test_exact_binomial_tail_matches_fraction_reference(correct, total):
    reference = sum((Fraction(comb(total, k)) * Fraction(1, 3) ** k * Fraction(2, 3) ** (total - k)
                     for k in range(correct, total + 1)), Fraction(0))
    assert probe_v2.binomial_p(correct, total) == float(reference)


@pytest.mark.parametrize("correct,verdict", [(0, "probe_insensitive"), (21, "probe_insensitive"), (22, "clean"), (48, "clean")])
def test_positive_control_48_answer_significance_gate(correct, verdict):
    result = probe_v2.clean_window(scores(control_correct=correct))
    assert result["verdict"] == verdict
    assert result["positive_control"]["correct"] == correct and result["positive_control"]["questions"] == 48
    assert result["positive_control"]["sensitive"] is (correct >= 22)
    if verdict == "probe_insensitive":
        assert result["clean_start"] is None and not result["candidate_windows"]


def test_2025_months_are_boundary_information_only():
    baseline = probe_v2.clean_window(scores())
    changed = scores()
    for row in changed:
        if row["month"].startswith("2025-"):
            row["correct"] = 8
    result = probe_v2.clean_window(changed)
    assert result["verdict"] == "clean" and result["clean_start"] == "2026-01-01"
    assert result["pooled_p_value"] == baseline["pooled_p_value"]
    assert len(result["boundary_2025"]) == 12 and all(row["correct"] == 8 for row in result["boundary_2025"])


def test_earliest_clean_start_after_contamination():
    data = scores()
    next(row for row in data if row["month"] == "2026-04")["correct"] = 7
    result = probe_v2.clean_window(data)
    assert result["verdict"] == "clean" and result["clean_start"] == "2026-05-01"
    assert [row["month"] for row in result["candidate_windows"]] == [f"2026-{month:02d}" for month in range(1, 6)]


@pytest.mark.parametrize("pooled_correct,expected_start", [(31, "2026-01-01"), (32, "2026-02-01")])
def test_pooled_p_threshold_straddles_five_percent(pooled_correct, expected_start):
    data = scores(validation_correct=3)
    validation = [row for row in data if row["month"].startswith("2026-")]
    for row in validation[:pooled_correct - 27]:
        row["correct"] += 1
    result = probe_v2.clean_window(data)
    assert result["clean_start"] == expected_start and result["pooled_p_value"] >= .05
    assert (result["candidate_windows"][0]["p_value"] >= .05) is (pooled_correct == 31)


def test_pooled_significance_pushes_without_any_seven_answer_month():
    data = scores(validation_correct=3)
    for row in data:
        if "2026-01" <= row["month"] <= "2026-04":
            row["correct"] = 6
    result = probe_v2.clean_window(data)
    assert result["clean_start"] == "2026-04-01" and result["verdict"] == "clean"
    assert all(not row["contaminated_months"] for row in result["candidate_windows"])
    assert all(row["p_value"] < .05 for row in result["candidate_windows"][:-1])


@pytest.mark.parametrize("cause", ["contaminated_may", "contaminated_september", "pooled_significance"])
def test_insufficient_clean_window(cause):
    data = scores(validation_correct=4 if cause == "pooled_significance" else 2)
    if cause != "pooled_significance":
        month = "2026-05" if cause == "contaminated_may" else "2026-09"
        next(row for row in data if row["month"] == month)["correct"] = 7
    result = probe_v2.clean_window(data)
    assert result["verdict"] == "insufficient_clean_window" and result["clean_start"] is None


@pytest.mark.parametrize("invalid", ["missing_control", "missing_month", "duplicate_month", "wrong_questions", "invalid_correct"])
def test_incomplete_or_invalid_scores_never_approve_window(invalid):
    data = scores()
    if invalid == "missing_control":
        data.pop(0)
    elif invalid == "missing_month":
        data.pop()
    elif invalid == "duplicate_month":
        data[-1] = data[-2]
    elif invalid == "wrong_questions":
        data[-1]["questions"] = 7
    else:
        data[-1]["correct"] = True
    assert probe_v2.clean_window(data)["verdict"] == "insufficient"


def test_status_and_checkpoint_never_borrow_lh001_state(offline_repo):
    old = workspace(offline_repo)
    save_json(old / "budget.json", {"used": 1100, "cap": 1100})
    save_json(old / "calls/bad-for-lh002.json", {"wrong_experiment": True})
    save_json(old / "probe.json", {"stage": "probe", "verdict": "clean", "smoke": False})
    with holdout.HoldoutSession(LH001.experiment_id, repo_root=offline_repo):
        pass
    before = {str(path): path.read_bytes() for path in offline_repo.rglob("*") if path.is_file()}
    result = cli.status(data_dir=offline_repo, repo_root=offline_repo, config=LH002)
    assert result["experiment_id"] == LH002.experiment_id
    assert result["budget"] == {"used": 0, "cap": 1100, "remaining": 1100}
    assert result["cached_calls"] == result["cached_valid_calls"] == 0 and not result["holdout_claimed"]
    assert all(state == "missing" for state in result["stages"].values())
    assert cli._completed("probe", offline_repo, None, LH002) is None
    with pytest.raises(ValueError, match="stored completed probe"):
        cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, config=LH002)
    assert before == {str(path): path.read_bytes() for path in offline_repo.rglob("*") if path.is_file()}


@pytest.mark.parametrize("stage", ["probe", "screening", "final"])
def test_lh002_rejects_a_lh001_runner_before_state_or_source_access(offline_repo, monkeypatch, stage):
    instance = runner(offline_repo, FakeProcess(), LH001)
    monkeypatch.setattr(cli, "_completed", lambda *a: pytest.fail("checkpoint read"))
    with pytest.raises(ValueError, match="runner experiment configuration"):
        getattr(cli, "execute_" + stage)(data_dir=offline_repo, repo_root=offline_repo, config=LH002, runner=instance)
    assert not workspace(offline_repo).exists() and not workspace(offline_repo, LH002).exists()


@pytest.mark.parametrize("field", ["pooled_p_value", "positive_control"])
def test_final_recomputes_stored_v2_binomial_fields_before_holdout(offline_repo, monkeypatch, field):
    data = scores()
    saved = {**probe_v2.clean_window(data), "scores": data}
    saved[field] = 1.0 if field == "pooled_p_value" else {}
    save_json(workspace(offline_repo, LH002) / "probe.json", saved)
    monkeypatch.setattr(cli, "price_days", lambda *a: pytest.fail("source inventory read"))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("holdout opened"))
    with pytest.raises(ValueError, match="does not match its scores"):
        cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, config=LH002)


def test_publication_uses_own_results_and_appends_only_lh002_rows(offline_repo):
    experiment_registry.record_trial(LH001.experiment_id, "b_num", {}, "insufficient", repo_root=offline_repo)
    registry = offline_repo / experiment_registry.REGISTRY_PATH
    before = registry.read_bytes()
    payload = cli._payload("screening", {arm: cli._unmeasured("insufficient", "synthetic") for arm in cli.VARIANTS}, 0, config=LH002)
    published = cli._publish(payload, offline_repo, offline_repo, False, LH002)
    assert published["experiment_id"] == LH002.experiment_id
    assert all(LH002.experiment_id in path for path in published["result_files"])
    assert not (offline_repo / "research/experiments" / LH001.experiment_id / "results").exists()
    assert registry.read_bytes().startswith(before)
    with registry.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["experiment_id"] for row in rows] == [LH001.experiment_id] + [LH002.experiment_id] * 5
    assert [row["variant"] for row in rows[1:]] == list(cli.VARIANTS)
    assert all(row["note"].startswith("LH002 screening;") for row in rows[1:])
    assert (workspace(offline_repo, LH002) / "screening.json").exists()
    assert not workspace(offline_repo).exists()
    with pytest.raises(ValueError, match="experiment ID"):
        evaluate.publish(payload, repo_root=offline_repo, config=LH001)


@pytest.mark.parametrize("config", [LH001, LH002])
def test_holdout_guards_require_the_matching_experiment_session(offline_repo, config):
    other = LH002 if config == LH001 else LH001
    with holdout.HoldoutSession(config.experiment_id, repo_root=offline_repo):
        guard_data("2026-01-01", "2026-01-31", repo_root=offline_repo, config=config)
        guard_data("2026-05-01", "2026-10-30", repo_root=offline_repo, config=config)
        with pytest.raises(ValueError, match="open HoldoutSession"):
            guard_data("2026-01-01", "2026-01-31", repo_root=offline_repo, config=other)
    rows = [json.loads(line) for line in (offline_repo / holdout.LEDGER_PATH).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["experiment_id"] == config.experiment_id


@pytest.mark.parametrize("reader", ["financials", "benchmarks", "disclosures", "history", "last_dates", "business_texts"])
def test_protected_readers_use_lh002_guard_before_reading(offline_repo, reader):
    with holdout.HoldoutSession(LH001.experiment_id, repo_root=offline_repo):
        with pytest.raises(ValueError, match="LH002 protected source reads"):
            kwargs = {"repo_root": offline_repo, "config": LH002}
            if reader == "financials":
                inputs.load_financials("2026-07-01", data_dir=offline_repo, **kwargs)
            elif reader == "benchmarks":
                inputs.load_benchmarks(offline_repo, "2026-01-01", "2026-07-01", **kwargs)
            elif reader == "disclosures":
                inputs.disclosure_counts(offline_repo, pd.bdate_range("2026-03-01", periods=60), {"000010"}, **kwargs)
            elif reader == "business_texts":
                inputs.business_texts(["000010"], "2026-07-01", offline_repo, **kwargs)
            else:
                getattr(inputs, "load_" + reader)("2026-01-01", "2026-07-01", data_dir=offline_repo, **kwargs)
    rows = [json.loads(line) for line in (offline_repo / holdout.LEDGER_PATH).read_text().splitlines()]
    assert [row["experiment_id"] for row in rows] == [LH001.experiment_id]


class FakeProbeRunner:
    def __init__(self, data_dir, control_answers=4):
        self.directory = workspace(data_dir, LH002)
        self.hashes = prompt_hashes(LH002.prompt_dir)
        self.calls = []
        self.control_answers = control_answers

    def call(self, template, rendered, schema_name, repeat_index, *, validate, stage):
        assert template == "v2_probe.txt" and schema_name == "v2_probe.schema.json" and stage == "probe"
        month = json.loads(rendered)["month"]
        quiz = probe_v2.make_questions(month, facts())
        output = answers(quiz, self.control_answers if month.startswith("2024-") else 2)
        validate(output)
        self.calls.append(month)
        return {"key": inputs.digest([template, rendered, repeat_index]), "valid": True, "output": output}


@pytest.mark.parametrize("control_answers,verdict", [(4, "clean"), (2, "probe_insensitive")])
def test_probe_execution_27_injected_calls_and_reuse_publish_only_lh002(offline_repo, control_answers, verdict):
    save_json(workspace(offline_repo) / "probe.json", {"stage": "probe", "verdict": "insufficient_clean_window"})
    old = (workspace(offline_repo) / "probe.json").read_bytes()
    instance = FakeProbeRunner(offline_repo, control_answers)
    measured_months = []

    def reader(months, **kwargs):
        measured_months.extend(str(month) for month in months)
        return {str(month): facts() for month in months}

    result = cli.execute_probe(data_dir=offline_repo, repo_root=offline_repo, runner=instance, config=LH002, facts_reader=reader,
                               sessions=pd.bdate_range("2023-12-01", "2026-10-01"))
    assert result["experiment_id"] == LH002.experiment_id and result["probe"]["verdict"] == verdict
    assert instance.calls == measured_months == [str(month) for month in probe_v2.MONTHS]
    saved = read_json(workspace(offline_repo, LH002) / "probe.json")
    assert saved["verdict"] == verdict and len(saved["scores"]) == 27
    assert all(row["questions"] == 8 for row in saved["scores"])
    reused = cli.execute_probe(data_dir=offline_repo, repo_root=offline_repo, runner=instance, config=LH002,
                               facts_reader=lambda *a, **k: pytest.fail("completed probe reread"))
    assert reused["completed_stage_reused"] and len(instance.calls) == 27
    assert experiment_registry.trial_count(LH002.experiment_id, repo_root=offline_repo) == 5
    assert experiment_registry.trial_count(LH001.experiment_id, repo_root=offline_repo) == 0
    assert (workspace(offline_repo) / "probe.json").read_bytes() == old
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


def test_missing_month_questions_stop_the_stage_before_first_model_call(offline_repo):
    instance = FakeProbeRunner(offline_repo)

    def reader(months, **kwargs):
        raw = {str(month): facts() for month in months}
        raw["2026-09"]["sectors"] = []
        return raw

    with pytest.raises(probe_v2.ProbeUnavailable):
        cli.execute_probe(data_dir=offline_repo, repo_root=offline_repo, runner=instance, config=LH002, facts_reader=reader,
                          sessions=pd.bdate_range("2023-12-01", "2026-10-01"))
    assert instance.calls == [] and not workspace(offline_repo, LH002).exists()
    assert not (offline_repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("verdict", ["probe_insensitive", "insufficient_clean_window", "insufficient"])
def test_failed_v2_probe_blocks_final_before_data_or_holdout(offline_repo, monkeypatch, verdict):
    save_json(workspace(offline_repo, LH002) / "probe.json", {"verdict": verdict, "smoke": False})
    monkeypatch.setattr(cli, "price_days", lambda *a: pytest.fail("source inventory read"))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("holdout opened"))
    with pytest.raises(ValueError, match="refuses"):
        cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, config=LH002)


def test_final_uses_lh002_ledger_registry_and_frozen_snapshot(offline_repo, monkeypatch):
    with holdout.HoldoutSession(LH001.experiment_id, repo_root=offline_repo):
        pass
    ledger = offline_repo / holdout.LEDGER_PATH
    old_ledger = ledger.read_bytes()
    save_json(workspace(offline_repo) / "final.json", {"stage": "final", "verdict": "pass", "smoke": False})
    old_final = (workspace(offline_repo) / "final.json").read_bytes()
    data = scores()
    root = workspace(offline_repo, LH002)
    save_json(root / "probe.json", {**probe_v2.clean_window(data), "scores": data})
    save_json(root / "screening.json", {"smoke": False, "variants": {arm: good_result() for arm in cli.VARIANTS[:4]}})
    monkeypatch.setattr(cli, "price_days", lambda *a: pd.DatetimeIndex(["2026-10-30"]))
    frame, decisions = metric_example()
    captured = []

    def source(planned, **kwargs):
        assert kwargs["config"] == LH002
        guard_data("2026-01-01", "2026-10-30", repo_root=offline_repo, config=LH002)
        captured.append("source")
        return {"prepared": [], "observations": inputs.json_value(frame.to_dict("records")), "exclusions": []}

    def selected(*args, **kwargs):
        assert kwargs["config"] == LH002
        return decisions, []

    monkeypatch.setattr(cli, "_prepare_decisions", source)
    monkeypatch.setattr(cli, "_run_snapshot", selected)
    trial_count = experiment_registry.trial_count

    def own_trials(experiment, **kwargs):
        assert experiment == LH002.experiment_id
        return trial_count(experiment, **kwargs)

    monkeypatch.setattr(experiment_registry, "trial_count", own_trials)
    sessions = pd.bdate_range("2015-01-01", "2026-11-30")
    result = cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, runner=FakeProbeRunner(offline_repo),
                               sessions=sessions, config=LH002)
    assert result["experiment_id"] == LH002.experiment_id and result["source_reads"].startswith("single LH002 HoldoutSession")
    assert ledger.read_bytes().startswith(old_ledger)
    assert [json.loads(line)["experiment_id"] for line in ledger.read_text().splitlines()] == [LH001.experiment_id, LH002.experiment_id]
    assert trial_count(LH002.experiment_id, repo_root=offline_repo) == 5
    assert trial_count(LH001.experiment_id, repo_root=offline_repo) == 0
    assert captured == ["source"] and (root / "final_snapshot.json").exists()
    # Simulate interruption after frozen acquisition but before stage checkpoint.
    (root / "final.json").unlink()
    monkeypatch.setattr(cli, "_prepare_decisions", lambda *a, **k: pytest.fail("protected source reread"))
    resumed = cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, runner=FakeProbeRunner(offline_repo),
                                sessions=sessions, config=LH002)
    assert resumed["snapshot_hash"] == result["snapshot_hash"] and captured == ["source"]
    assert len(ledger.read_text().splitlines()) == 2
    assert (workspace(offline_repo) / "final.json").read_bytes() == old_final


def test_b_random_control_top300_includes_unselected_industries_and_uses_adv(synthetic):
    bundle = bundle_for(synthetic)
    for index in range(290):
        bundle["stocks"].append({**bundle["stocks"][0], "stock_code": f"{10000 + index * 10:06d}", "name": f"확장{index}",
                                  "industry": inputs.GROUPS[3], "adv_20": 1e15 - index * 1e6, "trading_value": 1.0})
    instance = FakeArmRunner(synthetic[-1])
    instance.directory = workspace(synthetic[-1], LH002)
    result = arms.run_decision(instance, bundle, final=False, config=LH002)
    selected_industries = {row["industry"] for row in bundle["industries"] if row["id"] in result["b_stage1"]["selected"]}
    assert inputs.GROUPS[3] not in selected_industries
    controls = result["b_num"]["random_pool"]
    assert len(controls) == 300 and controls == arms.cap_codes(bundle)
    assert controls[0] == "010000"
    assert len(set(controls) & {row["stock_code"] for row in bundle["stocks"] if row["industry"] not in selected_industries}) == 290
    assert result["c_num"]["random_pool"] == arms.cap_codes(bundle, bundle["a_codes"])


def test_lh002_source_to_text_pipeline_propagates_config_without_changing_strategy(synthetic, monkeypatch):
    prices, days, known, profiles, benchmarks, root = synthetic
    day, end = days[270], days[290]
    planned = [{"decision_date": day.date().isoformat(), "trade_date": days[271].date().isoformat(), "exit_date": end.date().isoformat()}]
    monkeypatch.setattr(cli, "price_days", lambda *a: days)
    guarded = []

    def guard(start, finish, **kwargs):
        assert kwargs["config"] == LH002
        guarded.append((start, finish))
        guard_data(start, finish, **kwargs)

    def load(start, finish, **kwargs):
        assert kwargs["config"] == LH002 and pd.Timestamp(finish) == end
        dates = prices.index.get_level_values("trade_date")
        return prices.loc[(dates >= start) & (dates <= finish)]

    monkeypatch.setattr(inputs, "guard_data", guard)
    monkeypatch.setattr(evaluate, "guard_data", guard)
    monkeypatch.setattr(inputs, "load_history", load)
    monkeypatch.setattr(inputs, "load_last_dates", lambda *a, **k: pd.Series({code: days[-1] for code in profiles}))
    monkeypatch.setattr(inputs, "load_financials", lambda *a, **k: known)
    monkeypatch.setattr(inputs, "load_benchmarks", lambda *a, **k: benchmarks)
    monkeypatch.setattr(hc001, "_industries", lambda *a: profiles)
    snapshot = cli._prepare_decisions(planned, data_dir=root, repo_root=root, sessions=days, with_text=True,
                                      text_loader=lambda *a: extracted(), config=LH002)
    assert guarded and len(snapshot["observations"]) == 60 and not snapshot["exclusions"]
    instance = FakeArmRunner(root)
    instance.directory = workspace(root, LH002)
    selected, diagnostic = cli._run_snapshot(snapshot, instance, final=True, data_dir=root, config=LH002)
    assert not diagnostic and len(instance.calls) == 15
    for numeric, text in (("b_num", "b_text"), ("c_num", "c_text")):
        assert selected[0]["arms"][text]["random_pool"] == selected[0]["arms"][numeric]["candidates"]
    assert evaluate.metrics(cli._frame(snapshot), selected)["variants"]["c_text"]["primary"]["count"] == 1


@pytest.mark.parametrize("stage", ["probe", "screening", "final", "status"])
def test_cli_selects_lh002_for_every_stage(offline_repo, monkeypatch, capsys, stage):
    seen = []

    def dispatch(*args, **kwargs):
        seen.append((args, kwargs))
        return {"experiment_id": kwargs["config"].experiment_id}

    monkeypatch.setattr(cli, "status" if stage == "status" else "dry_run", dispatch)
    assert cli.main(["--experiment", "LH002", stage, "--data-dir", str(offline_repo)]) == 0
    assert json.loads(capsys.readouterr().out)["experiment_id"] == LH002.experiment_id
    assert seen[0][1]["config"] == LH002
    assert seen[0][0] == (() if stage == "status" else (stage,))


@pytest.mark.parametrize("stage", ["probe", "screening", "final"])
def test_cli_execute_dispatch_checks_lh002_prompts_without_launching_models(offline_repo, monkeypatch, capsys, stage):
    checked, dispatched = [], []
    monkeypatch.setattr(cli, "check_committed", lambda prompt_dir, root: checked.append(prompt_dir))

    def execute(**kwargs):
        dispatched.append(kwargs)
        return {"experiment_id": kwargs["config"].experiment_id}

    monkeypatch.setattr(cli, "execute_" + stage, execute)
    assert cli.main(["--experiment", "LH002", stage, "--execute", "--data-dir", str(offline_repo)]) == 0
    assert checked == [LH002.prompt_dir] and dispatched[0]["config"] == LH002
    assert json.loads(capsys.readouterr().out)["experiment_id"] == LH002.experiment_id


@pytest.mark.parametrize("entrypoint,experiment", [(cli.main, LH001), (alias.main, LH002)])
def test_default_and_thin_alias_select_correct_experiment(offline_repo, monkeypatch, capsys, entrypoint, experiment):
    monkeypatch.setattr(cli, "status", lambda **kwargs: {"experiment_id": kwargs["config"].experiment_id})
    assert entrypoint(["status", "--data-dir", str(offline_repo)]) == 0
    assert json.loads(capsys.readouterr().out)["experiment_id"] == experiment.experiment_id


def test_cli_interruption_records_lh002_identity_only(offline_repo, monkeypatch, capsys):
    monkeypatch.setattr(cli, "check_committed", lambda *a: None)
    monkeypatch.setattr(cli, "execute_probe", lambda **k: (_ for _ in ()).throw(StageStopped("synthetic stop")))
    publications = []
    monkeypatch.setattr(evaluate, "publish", lambda payload, **k: publications.append((payload, k)))
    assert cli.main(["--experiment", "LH002", "probe", "--execute", "--data-dir", str(offline_repo)]) == 2
    assert publications[0][0]["experiment_id"] == LH002.experiment_id and publications[0][1]["config"] == LH002
    assert json.loads(capsys.readouterr().out)["error"] == "synthetic stop"


def test_lh002_dry_run_is_read_only_and_estimates_27_calls(offline_repo, monkeypatch, capsys):
    monkeypatch.setattr(probe_v2, "read_contamination_facts", lambda *a, **k: pytest.fail("probe facts read"))
    monkeypatch.setattr(inputs, "load_history", lambda *a, **k: pytest.fail("strategy source read"))
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: pytest.fail("registry write"))
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("holdout claimed"))
    before = list(offline_repo.rglob("*"))
    assert cli.main(["--experiment", "LH002", "probe", "--data-dir", str(offline_repo)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] and result["estimated_calls"] == result["decisions"] == 27
    assert result["first_decision"] == "2024-01" and result["last_decision"] == "2026-09"
    assert result["cache_hits"] == 0 and result["budget"]["remaining"] == 1100
    assert alias.main(["status", "--data-dir", str(offline_repo)]) == 0
    assert json.loads(capsys.readouterr().out)["holdout_claimed"] is False
    assert before == list(offline_repo.rglob("*"))
