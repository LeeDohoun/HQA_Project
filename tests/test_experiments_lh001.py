"""Synthetic LH001 checks. Every Codex subprocess is injected; no real model."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from backtesting import cost_model, experiment_registry, holdout
from backtesting.experiments import common, hc001, hc002
from backtesting.experiments.lh001 import ASSUMPTIONS, CODEX_VERSION, EXPERIMENT_ID, PROMPT_DIR, VARIANTS, guard_data, workspace
from backtesting.experiments.lh001 import __main__ as cli
from backtesting.experiments.lh001 import arms, evaluate, inputs, probe
from backtesting.experiments.lh001.runner import LlmRunner, StageStopped, inspect_events, read_json, save_json
from src.ingestion import dart_quarterly


@pytest.fixture
def fields():
    text = (PROMPT_DIR.parent / "preregistration.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


@pytest.fixture
def offline_repo(tmp_path, monkeypatch, fields):
    monkeypatch.setattr(common, "load_preregistration", lambda *a, **k: fields)
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_codex(monkeypatch):
    # Prevent any un-injected Codex process, including --version, throughout the
    # module. Other suites retain their independent fixture behaviour.
    original = subprocess.run
    def process(command, *args, **kwargs):
        if command and command[0] == "codex":
            pytest.fail("real Codex process attempted")
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", process)


@pytest.fixture
def synthetic(tmp_path):
    days = pd.bdate_range("2024-01-02", periods=300)
    codes = [f"{10 * (index + 1):06d}" for index in range(60)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    changes = np.tile(np.linspace(0.0001, 0.001, len(codes)), len(days))
    prices = pd.DataFrame({"open": 10000.0, "high": 10100.0, "low": 9900.0, "close": 10000.0,
                           "base_price": 10000.0, "volume": 10000.0, "ret_1d": changes, "market": "KOSPI",
                           "calendar_status": "verified", "trading_value": np.tile(np.arange(60) * 1e7 + 2e9, len(days)),
                           "market_cap": np.tile(np.arange(60) * 1e8 + 2e10, len(days)),
                           "stock_name": np.tile(["합성기업" + code for code in codes], len(days))}, index=index)
    rows = []
    for code in codes:
        for quarter, revenue, income, available in (("2023Q1", 100.0, 10.0, "2023-05-15"),
                                                    ("2023Q2", 110.0, 11.0, "2023-08-15"),
                                                    ("2023Q3", 115.0, 12.0, "2023-11-15"),
                                                    ("2023Q4", 118.0, 12.0, "2024-03-15"),
                                                    ("2024Q1", 120.0, 30.0, "2024-05-15")):
            rows.append({"stock_code": code, "corp_code": "corp-" + code, "fiscal_quarter": quarter,
                         "revenue": revenue, "operating_income": income, "net_income": 1.0,
                         "available_date": available, "rcept_no": available.replace("-", "") + code,
                         "fs_div": "CFS", "currency": "KRW", "missing_reasons": {}})
    known = pd.DataFrame(rows)
    profiles = {code: inputs.GROUPS[index % 3] for index, code in enumerate(codes)}
    benchmarks = pd.DataFrame([{"trade_date": day, "series": series, "index_name": name, "close": 100 * 1.001 ** index}
                               for index, day in enumerate(days) for series, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))])
    return prices, days, known, profiles, benchmarks, tmp_path


def bundle_for(synthetic, **kwargs):
    prices, days, known, profiles, benchmarks, root = synthetic
    return inputs.build_inputs(prices, days[270], data_dir=root, known=known, profiles=profiles, benchmarks=benchmarks, **kwargs)


def test_point_in_time_future_prices_and_financials_do_not_change_input(synthetic):
    prices, days, known, profiles, benchmarks, root = synthetic
    before = bundle_for(synthetic)
    future = known.loc[known.fiscal_quarter.eq("2024Q1")].copy()
    future["available_date"] = days[271].date().isoformat()
    future["fiscal_quarter"] = "2024Q2"
    future[["revenue", "operating_income"]] = 999999.0
    changed = prices.copy()
    changed.loc[changed.index.get_level_values("trade_date") > days[270], ["close", "high", "trading_value", "ret_1d"]] = 1e12
    after = inputs.build_inputs(changed, days[270], data_dir=root, known=pd.concat([known, future]), profiles=profiles, benchmarks=benchmarks)
    assert before == after
    assert all(stock["financial_date"] < before["decision_date"] for stock in before["stocks"])


def test_input_values_and_all_24_groups_at_decision_close(synthetic):
    bundle = bundle_for(synthetic)
    assert len(bundle["industries"]) == 24
    stock = bundle["stocks"][0]
    assert stock["return_20"] == pytest.approx(1.0001 ** 20 - 1)
    assert stock["return_250"] == pytest.approx(1.0001 ** 250 - 1)
    assert stock["revenue_yoy"] == 20.0
    assert stock["operating_income_yoy"] == 200.0 and stock["phase"] == 2
    assert len(stock["quarters_4"]) == 4
    assert stock["disclosures_contract"] is None
    assert not stock["listing_coverage"]["complete"]
    assert bundle["market"][0]["return_20"] == pytest.approx(1.001 ** 20 - 1)
    prices, days, known, profiles, benchmarks, root = synthetic
    changed = prices.copy()
    changed.loc[(days[270], "000010"), "ret_1d"] = 0.2
    after = inputs.build_inputs(changed, days[270], data_dir=root, known=known, profiles=profiles, benchmarks=benchmarks)
    assert after["stocks"][0]["return_20"] != stock["return_20"]


def test_anonymisation_deterministic_per_decision_and_no_money_or_identity(synthetic):
    bundle = bundle_for(synthetic)
    rendered, mapping = inputs.render(bundle, anonymised=True)
    assert rendered == inputs.render(bundle, anonymised=True)[0]
    assert "trading_value=percentile" in rendered and "KRW" not in rendered and "financial_date" not in rendered and "titles" not in rendered
    assert len(mapping) == 60 and len(set(mapping)) == 60
    inputs.leak_check(rendered, bundle)
    tomorrow = {**bundle, "decision_date": "2025-01-20"}
    assert inputs.id_map(bundle, True) != inputs.id_map(tomorrow, True)
    assert "q-3" in rendered and "0.0000" in rendered
    with pytest.raises(ValueError, match="forbidden"):
        inputs.render(bundle, anonymised=True, texts={})


@pytest.mark.parametrize("leak", ["000010", "합성기업000010", "2024-05-15", "20240515"])
def test_anonymisation_leak_checker_rejects_identity_and_dates(synthetic, leak):
    with pytest.raises(AssertionError, match="leak"):
        inputs.leak_check(leak, bundle_for(synthetic))


def test_named_input_and_memory_diagnostic_do_not_add_titles(synthetic):
    bundle = bundle_for(synthetic)
    bundle["stocks"][0]["titles"] = [{"date": "2024-12-20", "title": "공급계약 체결"}]
    named, _ = inputs.render(bundle, anonymised=False)
    assert bundle["decision_date"] in named and "합성기업000010" in named and "공급계약 체결" in named
    diagnostic, _ = inputs.render(bundle, anonymised=False, final_window=False)
    assert "공급계약 체결" not in diagnostic


def test_hc002_universe_equivalence_before_holdout(synthetic, monkeypatch):
    prices, days, known, profiles, benchmarks, root = synthetic
    day = days[270]
    history = prices.loc[prices.index.get_level_values("trade_date") <= day]
    monkeypatch.setattr(dart_quarterly, "load_quarterly", lambda *a, **k: known)
    monkeypatch.setattr(hc001, "_industries", lambda *a: profiles)
    expected, _ = hc002.universe_on_decision(history, day, data_dir=root)
    actual, _ = inputs.close_universe(history, day, known, profiles)
    assert actual.index.tolist() == expected.index.tolist()
    for field in ("phase", "score", "revenue_yoy", "operating_income_yoy", "avg_trading_value_20d"):
        pd.testing.assert_series_equal(actual[field], expected[field])


def test_disclosure_counts_partial_none_complete_zero_and_future_ignored(tmp_path):
    sessions = pd.bdate_range("2024-06-03", periods=60)
    directory = tmp_path / "disclosures/dart_full/list/2024"
    directory.mkdir(parents=True)
    first = sessions[0].strftime("%Y%m%d")
    row = {"stock_code": "000010", "rcept_dt": first, "rcept_no": first + "000001", "report_nm": "단일판매·공급계약체결"}
    (directory / f"{first}.jsonl").write_text(json.dumps(row) + "\n")
    partial = inputs.disclosure_counts(tmp_path, sessions, {"000010", "000020"})
    assert partial["000010"]["counts"] is None and partial["000020"]["counts"] is None
    assert partial["000010"]["coverage"]["available"] and not partial["000010"]["coverage"]["complete"]
    for day in pd.date_range(sessions[0], sessions[-1]):
        path = directory / f"{day:%Y%m%d}.jsonl"
        if not path.exists():
            path.write_text("")
    future = sessions[-1] + pd.Timedelta(days=1)
    (directory / f"{future:%Y%m%d}.jsonl").write_text(json.dumps({**row, "rcept_dt": f"{future:%Y%m%d}"}) + "\n")
    complete = inputs.disclosure_counts(tmp_path, sessions, {"000010", "000020"})
    assert complete["000010"]["counts"]["contract"] == 1
    assert all(value == 0 for value in complete["000020"]["counts"].values())
    assert complete["000010"]["coverage"]["complete"]


def raw_quarter(year, day, revenue=100):
    return {"stock_codes": ["000010"], "corp_code": "corp", "bsns_year": year, "reprt_code": "11013",
            "fiscal_quarter": f"{year}Q1", "fs_div": "CFS", "available_date": day,
            "rcept_no": day.replace("-", "") + "000001", "currency": "KRW",
            "raw_accounts": {metric: {"thstrm_amount": str(value), "thstrm_add_amount": str(value), "thstrm_dt": None, "currency": "KRW"}
                             for metric, value in (("revenue", revenue), ("operating_income", 10), ("net_income", 1))}}


def test_financial_loader_strict_receipts_and_never_opens_future_year(tmp_path, monkeypatch):
    directory = tmp_path / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True)
    rows = [raw_quarter(2024, "2024-05-15"), raw_quarter(2024, "2024-05-31", 999)]
    (directory / "2024_11013.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (directory / "2026_11013.jsonl").write_text("not readable\n")
    actual = inputs.load_financials("2024-05-31", data_dir=tmp_path)
    assert len(actual) == 1 and actual.iloc[0].revenue == 100
    assert actual.iloc[0].available_date == "2024-05-15"


def test_candidate_caps_adv_not_market_cap():
    stocks = [{"stock_code": f"{10 * i:06d}", "adv_20": float(i), "market_cap_percentile": 100 - i / 10} for i in range(1, 351)]
    codes = arms.cap_codes({"stocks": stocks})
    assert len(codes) == 300 and codes[0] == "003500" and codes[-1] == "000510"


def extracted(text="사업 설명", day="2024-05-15"):
    return {"text": text, "report_nm": "분기보고서", "rcept_no": day.replace("-", "") + "000001",
            "rcept_dt": day, "period": "2024Q1", "fallback_used": False, "chars": len(text),
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "extraction_version": "synthetic-v1"}


def test_text_injected_fake_provenance_and_missing_no_module_import(tmp_path):
    results = inputs.business_texts(["000010", "000020"], "2024-06-01", tmp_path,
                                    loader=lambda code, *args: extracted() if code == "000010" else None)
    assert not results["000010"]["missing"] and results["000010"]["provenance"]["extraction_version"] == "synthetic-v1"
    assert results["000020"]["display"] == "본문 없음" and results["000020"]["missing"]


@pytest.mark.parametrize("alter", ["future", "hash", "oversize"])
def test_text_rejects_broken_extractor_contract(tmp_path, alter):
    row = extracted("x" * 4001 if alter == "oversize" else "본문", "2024-06-01" if alter == "future" else "2024-05-15")
    if alter == "hash":
        row["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        inputs.business_texts(["000010"], "2024-06-01", tmp_path, loader=lambda *a: row)


def facts():
    return {"kospi_level": 2500, "sectors": [{"name": name, "return": value} for name, value in (("건설", -0.04), ("기계", 0), ("화학", 0.07))],
            "stocks": [{"stock_code": f"{10 * (i + 1):06d}", "name": "합성" + str(i), "return": i * 0.01} for i in range(30)]}


def probe_scores(correct=1):
    return [{"month": str(month), "correct": correct, "questions": 4} for month in probe.MONTHS]


def test_probe_question_generation_shuffle_and_no_answer_leak():
    quiz = probe.make_questions("2025-01", facts())
    assert quiz == probe.make_questions("2025-01", facts())
    assert quiz != probe.make_questions("2025-02", facts())
    levels = {option["label"] for option in quiz["questions"][0]["options"]}
    assert levels == {"2500.00", "2000.00", "3125.00"}
    rendered = probe.render_questions(quiz)
    assert "correct" not in rendered and "return" not in rendered
    triples = [{option["label"] for option in q["options"]} for q in quiz["questions"][2:]]
    assert triples[0].isdisjoint(triples[1])


@pytest.mark.parametrize("correct,contaminated", [(0, False), (2, False), (3, True), (4, True)])
def test_probe_scoring_contaminated_month_threshold(correct, contaminated):
    quiz = probe.make_questions("2025-01", facts())
    answers = [{"id": question["id"], "option": question["correct"] if index < correct else
                next(letter for letter in "ABC" if letter != question["correct"])} for index, question in enumerate(quiz["questions"])]
    result = probe.score(quiz, {"answers": answers})
    assert result["correct"] == correct and result["contaminated"] is contaminated


def test_probe_clean_start_minimum_and_last_contamination():
    scores = probe_scores()
    assert probe.clean_window(scores)["clean_start"] == "2026-01-01"
    next(row for row in scores if row["month"] == "2026-04")["correct"] = 3
    result = probe.clean_window(scores)
    assert result["verdict"] == "clean" and result["clean_start"] == "2026-05-01"


def test_probe_pooled_accuracy_push_and_exact_45_percent_boundary():
    scores = probe_scores()
    for row in scores:
        if "2026-01" <= row["month"] < "2026-09":
            row["correct"] = 2
    result = probe.clean_window(scores)
    assert result["verdict"] == "clean" and result["clean_start"] == "2026-05-01"
    assert result["pooled_accuracy"] == 0.45 and len(result["pooled_pushes"]) == 4


def test_probe_insufficient_window_and_missing_month_block():
    scores = probe_scores()
    next(row for row in scores if row["month"] == "2026-05")["correct"] = 3
    assert probe.clean_window(scores)["verdict"] == "insufficient_clean_window"
    assert probe.clean_window(scores[:-1])["verdict"] == "insufficient"


def test_probe_missing_sector_separation_is_not_fabricated():
    raw = facts()
    raw["sectors"] = [{"name": name, "return": 0.01 * index} for index, name in enumerate(("a", "b", "c"))]
    with pytest.raises(probe.ProbeUnavailable, match="separated"):
        probe.make_questions("2025-01", raw)


def test_dedicated_2026_reader_only_month_end_facts_no_guard_or_strategy(tmp_path, monkeypatch):
    sessions = pd.bdate_range("2025-12-01", "2026-01-31")
    previous, current = probe.month_ends("2026-01", sessions)
    for day, factor in ((previous, 0), (current, 1)):
        path = tmp_path / "market/krx_daily" / str(day.year) / f"{day:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"stock_code": f"{10 * (i + 1):06d}", "stock_name": "합성" + str(i), "market": "KOSPI",
                 "market_cap": 1e10 + i, "close": 1000 + factor * i, "open": "forbidden-unused"} for i in range(31)]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    path = tmp_path / "market_context/benchmarks.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{"trade_date": day.date().isoformat(), "series": "KOSPI", "index_name": name, "close": value}
            for day in (previous, current) for name, value in (("코스피", 2500), ("기계", 100))]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(probe, "guard_data", lambda *a, **k: pytest.fail("probe exception must not consume holdout"))
    monkeypatch.setattr(inputs, "load_history", lambda *a, **k: pytest.fail("daily strategy source read"))
    measured = probe.read_contamination_facts(["2026-01"], data_dir=tmp_path, sessions=sessions)["2026-01"]
    assert len(measured["stocks"]) == 30 and "000010" not in {row["stock_code"] for row in measured["stocks"]}
    assert all(set(row) == {"stock_code", "name", "return"} for row in measured["stocks"])
    assert "not a strategy evaluation" in measured["read_note"]
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


def ranked(ids):
    return [{"id": identity, "reason": "근거", "confidence": 3} for identity in ids]


class FakeProcess:
    def __init__(self, output=None, forbidden=None, versions=None, invalid_first=False, quota=False, timeout=False):
        self.output = output if output is not None else {"ranked": ranked(["a", "b", "c"])}
        self.forbidden, self.versions = forbidden, list(versions or [])
        self.invalid_first, self.quota, self.timeout = invalid_first, quota, timeout
        self.execs, self.commands, self.working_dirs = 0, [], []

    def __call__(self, command, **kwargs):
        if command == ["codex", "--version"]:
            assert kwargs["stdin"] == subprocess.DEVNULL
        else:
            assert command[-1] == "-" and isinstance(kwargs["input"], str) and "stdin" not in kwargs
            assert kwargs["input"].endswith("</UNTRUSTED_DATA>")
        assert kwargs["timeout"] > 0
        assert not list(Path(kwargs["cwd"]).iterdir())
        assert not Path(kwargs["cwd"]).resolve().is_relative_to(PROMPT_DIR.parents[3])
        if command == ["codex", "--version"]:
            version = self.versions.pop(0) if self.versions else CODEX_VERSION
            return SimpleNamespace(stdout=version, stderr="", returncode=0)
        assert command[:6] == ["codex", "exec", "-m", "gpt-6-luna", "-c", "model_reasoning_effort=medium"]
        assert "--sandbox" in command and command[command.index("--sandbox") + 1] == "read-only"
        assert {"--ephemeral", "--skip-git-repo-check", "--json", "--output-schema", "-o"}.issubset(command)
        self.execs += 1
        self.commands.append(command)
        self.working_dirs.append(kwargs["cwd"])
        if self.timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(json.dumps(self.output))
        events = [{"type": "thread.started"}, {"type": "turn.started"}]
        if self.forbidden is not None:
            events.append({"type": "item.completed", "item": {"type": self.forbidden}})
        if self.invalid_first and self.execs == 1:
            events.append({"type": "item.started", "item": {"type": "web_search"}})
        events += [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(self.output)}},
                   {"type": "turn.failed", "error": "usage limit reached"} if self.quota else {"type": "turn.completed"}]
        return SimpleNamespace(stdout="\n".join(json.dumps(row) for row in events), stderr="", returncode=0)


def llm(tmp_path, process, **kwargs):
    return LlmRunner(data_dir=tmp_path, subprocess_call=process, enforce_committed=False, **kwargs)


def call_stage1(runner, *, repeat=0, validator=None):
    return runner.call("v1_b_stage1.txt", "synthetic table", "v1_b_stage1.schema.json", repeat, validate=validator, stage="probe-test")


@pytest.mark.parametrize("item_type", ["command_execution", "file_read", "file_change", "file_patch", "web_search", "mcp_tool_call",
                                       "tool_call", "function_call", "collab_tool_call", "unknown_item"])
def test_runner_invalidates_every_forbidden_or_unknown_item_and_only_two_retries(tmp_path, item_type):
    process = FakeProcess(forbidden=item_type)
    runner = llm(tmp_path, process)
    result = call_stage1(runner)
    assert not result["valid"] and process.execs == 3 and runner.budget()["used"] == 3
    assert len(result["attempts"]) == 3
    assert len(set(process.working_dirs)) == 3
    assert all(attempt["event_summary"]["violations"] for attempt in result["attempts"])
    assert call_stage1(runner)["valid"] is False and process.execs == 3


@pytest.mark.parametrize("stream", ['{"type":"new_event"}', 'not JSON', '{"type":"turn.failed"}', '{"type":"item.completed"}'])
def test_event_parser_fails_closed_for_unknown_or_malformed_events(stream):
    assert inspect_events(stream)[0]["violations"]


@pytest.mark.parametrize("violation", ["schema", "unknown", "duplicate", "too_long", "extra_field"])
def test_runner_schema_and_candidate_id_validation_invalidates_call(tmp_path, violation):
    output = {"ranked": ranked(["a", "b", "c"])}
    if violation == "schema":
        output["ranked"][0]["confidence"] = 6
    elif violation == "unknown":
        output["ranked"][0]["id"] = "unknown"
    elif violation == "duplicate":
        output["ranked"][0]["id"] = "b"
    elif violation == "too_long":
        output["ranked"][0]["reason"] = "x" * 201
    else:
        output["extra"] = 1
    process = FakeProcess(output)
    result = call_stage1(llm(tmp_path, process), validator=lambda output: arms.validate_selection(output, {"a", "b", "c"}, count=3))
    assert not result["valid"] and process.execs == 3


def test_runner_retry_then_cache_resume_and_one_stored_input(tmp_path):
    process = FakeProcess(invalid_first=True)
    runner = llm(tmp_path, process)
    result = call_stage1(runner)
    assert result["valid"] and process.execs == 2 and len(result["attempts"]) == 2
    resumed_process = FakeProcess()
    resumed = call_stage1(llm(tmp_path, resumed_process))
    assert resumed["valid"] and resumed["cache_hit"] and resumed_process.execs == 0
    assert resumed["key"] == result["key"]
    assert len(list((workspace(tmp_path) / "inputs").glob("*.txt"))) == 1
    stored = read_json(workspace(tmp_path) / "calls" / (result["key"] + ".json"))
    assert "synthetic table" not in json.dumps(stored)
    assert stored["model"] == "gpt-6-luna" and stored["codex_version"] == CODEX_VERSION
    assert call_stage1(runner, repeat=1)["key"] != result["key"]


def test_runner_budget_cap_persisted_across_instances(tmp_path):
    save_json(workspace(tmp_path) / "budget.json", {"used": 1100, "cap": 1100})
    process = FakeProcess()
    with pytest.raises(StageStopped, match="budget"):
        call_stage1(llm(tmp_path, process))
    assert process.execs == 0


def test_runner_last_budget_slot_consumed_even_on_invalid_attempt(tmp_path):
    save_json(workspace(tmp_path) / "budget.json", {"used": 1099, "cap": 1100})
    process = FakeProcess(forbidden="file_read")
    with pytest.raises(StageStopped, match="budget"):
        call_stage1(llm(tmp_path, process))
    assert process.execs == 1 and read_json(workspace(tmp_path) / "budget.json")["used"] == 1100


def test_runner_version_change_aborts_and_invalidates_saved_output(tmp_path):
    process = FakeProcess(versions=[CODEX_VERSION, "codex-cli 0.161.0"])
    with pytest.raises(StageStopped, match="version"):
        call_stage1(llm(tmp_path, process))
    stored = read_json(next((workspace(tmp_path) / "calls").glob("*.json")))
    assert not stored["valid"] and stored["output"] is None


def test_runner_model_change_aborts(tmp_path):
    process = FakeProcess()
    runner = llm(tmp_path, process)
    def changed(command, **kwargs):
        result = process(command, **kwargs)
        if command[1] == "exec":
            runner.model = "other"
        return result
    runner.subprocess_call = changed
    with pytest.raises(StageStopped, match="model"):
        call_stage1(runner)


def test_runner_quota_stop_can_resume_exact_input(tmp_path):
    stopped = FakeProcess(quota=True)
    with pytest.raises(StageStopped, match="quota"):
        call_stage1(llm(tmp_path, stopped))
    assert stopped.execs == 1
    result = call_stage1(llm(tmp_path, FakeProcess()))
    assert result["valid"] and len(result["attempts"]) == 2
    assert result["attempts"][0]["state"] == "interrupted"


def test_runner_timeout_invalid_and_budget_counted(tmp_path):
    process = FakeProcess(timeout=True)
    result = call_stage1(llm(tmp_path, process))
    assert not result["valid"] and process.execs == 3
    assert all("TimeoutExpired" in attempt["error"] for attempt in result["attempts"])


def test_runner_uncommitted_prompt_preflight_blocks_no_exec(tmp_path, monkeypatch):
    from backtesting.experiments.lh001 import runner as module
    monkeypatch.setattr(module, "check_committed", lambda *args: (_ for _ in ()).throw(StageStopped("uncommitted prompt")))
    process = FakeProcess()
    runner = LlmRunner(data_dir=tmp_path, subprocess_call=process)
    with pytest.raises(StageStopped, match="uncommitted"):
        call_stage1(runner)
    assert process.execs == 0 and runner.budget()["used"] == 0


def test_aggregation_count_mean_rank_and_jaccard():
    runs = [["a", "b", "c"], ["b", "a", "d"], ["b", "d", "a"]]
    assert arms.aggregate(runs, 3) == ["b", "a", "d"]
    assert arms.aggregate(runs, 2, eligible={"a", "c", "d"}) == ["a", "d"]
    assert arms.jaccard([["a", "b"], ["b", "c"], ["a", "b"]]) == pytest.approx(5 / 9)
    assert arms.jaccard([["a"]]) is None


def test_c_avoid_duplicate_and_unknown_ids_rejected():
    output = {"ranked": ranked(["a", "b"]), "avoid": ranked(["b"])}
    with pytest.raises(ValueError, match="duplicate"):
        arms.validate_selection(output, {"a", "b", "c"}, count=2, avoid=1)


class FakeArmRunner:
    def __init__(self, data_dir):
        self.directory = workspace(data_dir)
        self.hashes = {"fake": "synthetic"}
        self.calls = []

    def call(self, template, rendered, schema_name, repeat_index, *, validate, stage):
        self.calls.append((template, rendered, repeat_index))
        lines = rendered.splitlines()
        header = next(index for index, line in enumerate(lines) if line.startswith("id|industry") and
                      (schema_name == "v1_b_stage1.schema.json" or "market_cap_percentile" in line))
        ids = []
        for line in lines[header + 1:]:
            if not line:
                break
            ids.append(line.split("|")[0])
        count = 3 if "stage1" in template else 10 if "text" in template else 30
        output = {"ranked": ranked(ids[:count])}
        if "c_num" in template:
            output["avoid"] = ranked(ids[30:40])
        validate(output)
        return {"valid": True, "output": output, "key": inputs.digest([template, rendered, repeat_index]),
                "attempts": [{"state": "valid"}]}


def test_final_arm_repeat_order_fixed_text_candidates_and_injected_extractor(synthetic):
    bundle = bundle_for(synthetic)
    runner = FakeArmRunner(synthetic[-1])
    calls = []
    def loader(code, day, directory):
        calls.append(code)
        return None if code == "000010" else extracted()
    result = arms.run_decision(runner, bundle, final=True, text_loader=loader)
    assert len(runner.calls) == 15
    assert [row[0] for row in runner.calls] == sum(([template] * 3 for template in
           ("v1_b_stage1.txt", "v1_b_num.txt", "v1_b_text.txt", "v1_c_num.txt", "v1_c_text.txt")), [])
    for arm in ("b_num", "b_text", "c_num", "c_text"):
        assert len(result[arm]["selected"]) == 10 and result[arm]["jaccard"] == 1.0
    assert result["b_text"]["random_pool"] == result["b_num"]["candidates"]
    assert result["c_text"]["random_pool"] == result["c_num"]["candidates"]
    assert len(result["c_num"]["avoid"]) == 10
    assert len(calls) == 60
    assert all("<UNTRUSTED_DATA>" not in row[1] for row in runner.calls)


def test_failed_repeat_yields_no_selection_instead_of_two_repeat_vote(synthetic):
    bundle = bundle_for(synthetic)
    runner = FakeArmRunner(synthetic[-1])
    original = runner.call
    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "v1_c_num.txt" and args[3] == 1:
            result.update(valid=False, output=None, attempts=[{"state": "invalid"}] * 3)
        return result
    runner.call = invalid
    result = arms.run_decision(runner, bundle, final=True, text_loader=lambda *a: None)
    assert result["c_num"]["selected"] == [] and result["c_num"]["invalid_calls"] == 3
    assert result["c_text"]["reason"] == "no_numeric_candidates"


def observation_universe(days, decision, codes):
    return pd.DataFrame({"decision_date": decision, "stock_code": codes, "phase": 2, "avg_trading_value_20d": 2e9})


@pytest.mark.parametrize("policy", ["adjusted", "limit_up", "stale", "raw_fallback"])
def test_evaluation_next_open_20th_close_and_full_cost_math(synthetic, policy):
    prices, days, _, _, _, _ = synthetic
    day, code = days[50], "000010"
    entry, exit_day = days[51], days[70]
    prices = prices.copy()
    prices.loc[(entry, code), ["open", "close"]] = [10500.0, 10200.0]
    if policy == "limit_up":
        prices.loc[(entry, code), "open"] = 13000
    elif policy == "stale":
        prices = prices.drop((exit_day, code))
    elif policy == "raw_fallback":
        prices.loc[(days[55], code), "ret_1d"] = np.nan
        prices.loc[(exit_day, code), "close"] = 12600.0
    result = evaluate.observations(prices, observation_universe(days, day, [code])).iloc[0]
    assert result.entry_date == entry
    if policy == "limit_up":
        assert result.reason == "limit_up_entry" and pd.isna(result.gross)
        return
    actual_exit = days[69] if policy == "stale" else exit_day
    assert result.exit_date == actual_exit
    expected = 12600 / 10500 - 1 if policy == "raw_fallback" else 10200 / 10500 * 1.0001 ** (18 if policy == "stale" else 19) - 1
    assert result.gross == pytest.approx(expected)
    for multiplier in (1.0, 1.5, 2.0):
        assert result[f"cost_{multiplier}"] == cost_model.round_trip_cost(10500, "KOSPI", entry.date(), 2e9, multiplier=multiplier)
    assert result["cost_1.5"] == pytest.approx((result["cost_1.0"] - 0.0018) * 1.5 + 0.0018)


def metric_example():
    day = pd.Timestamp("2024-07-05")
    codes = [f"{10 * (i + 1):06d}" for i in range(40)]
    frame = pd.DataFrame({"decision_date": day, "stock_code": codes, "gross": [0.1] * 10 + [0.0] * 30,
                          "phase": [2] * 20 + [3] * 20, "bucket": 2, "reason": None,
                          "entry_date": day + pd.Timedelta(days=3), "exit_date": day + pd.Timedelta(days=28),
                          **{flag: False for flag in ("unadjusted_fallback", "stale_exit", "halted_after_entry", "limit_down_exit")},
                          **{f"cost_{m}": 0.002 + 0.003 * m for m in (1.0, 1.5, 2.0)}})
    decisions = [{"decision_date": day.date().isoformat(), "arms": {arm: {"selected": codes[:10], "candidates": codes[:30],
                  "avoid": codes[20:30] if arm == "c_num" else [], "random_pool": codes[:30] if arm.endswith("text") else
                  codes[:20] if arm.startswith("c") else codes, "jaccard": 1.0, "invalid_calls": 0, "text_missing": 0} for arm in VARIANTS[:4]}}]
    return frame, decisions


def test_hand_computed_b_c_a_text_avoid_and_cost_sensitivity():
    frame, decisions = metric_example()
    result = evaluate.metrics(frame, decisions)
    assert result["variants"]["b_num"]["primary"]["mean"] == pytest.approx(0.07)
    assert result["variants"]["c_num"]["primary"]["mean"] == pytest.approx(0.05)
    assert result["a_weekly"]["primary"]["mean"] == pytest.approx(0.02)
    assert result["variants"]["c_num"]["avoid_excess"]["mean"] == pytest.approx(-0.05)
    assert result["variants"]["b_text"]["text_effect"]["mean"] == 0.0
    assert result["variants"]["b_num"]["cost_sensitivity"]["1.5"]["top_net_excess"] == pytest.approx(0.0685)
    assert result["variants"]["b_num"]["random_control"]["decisions"] == 1
    assert len(result["variants"]["b_num"]["random_control"]["mean_excess"]) == 200


@pytest.mark.parametrize("values", [[0.03, 0.01, 0.02, -0.01, 0.05, 0.04, 0.02], [1.0, 3.0], [0.02] * 20])
def test_newey_west_against_independent_covariance_matrix_reference(values):
    x = np.array(values)
    residual = np.zeros(len(x)) if np.all(x == x[0]) else x - x.mean()
    n = len(x)
    weights = np.array([[max(0, 1 - abs(i - j) / 5) for j in range(n)] for i in range(n)])
    expected_variance = residual @ weights @ residual / n ** 2
    actual = evaluate.newey_west(values)
    assert actual["standard_error"] == pytest.approx(np.sqrt(max(0, expected_variance)), abs=1e-12)
    if expected_variance > 0:
        assert actual["t_stat"] == pytest.approx(x.mean() / np.sqrt(expected_variance))
    else:
        assert actual["t_stat"] is None


def test_random_controls_seed_pool_size_and_without_replacement():
    frame, _ = metric_example()
    codes = frame.stock_code.tolist()
    first = evaluate.random_controls(frame, codes, rng=np.random.default_rng(0))
    second = evaluate.random_controls(frame.iloc[::-1], codes, rng=np.random.default_rng(0))
    assert np.array_equal(first, second) and len(first) == 200
    assert evaluate.random_controls(frame, codes[:9], rng=np.random.default_rng(0)) is None
    restricted = evaluate.random_controls(frame, codes[:10], rng=np.random.default_rng(0))
    assert np.allclose(restricted, 0.095)
    assert not np.array_equal(first, evaluate.random_controls(frame, codes, rng=np.random.default_rng(1)))


def good_result(**changes):
    primary = {"count": 20, "mean": 0.02, "t_stat": 2.1, **changes}
    return {"primary": primary, "events": 300, "random_control": {"share_of_controls": 0.05},
            "cost_sensitivity": {"1.5": {"top_net_excess": 0.01}}, "verdict": "pass"}


@pytest.mark.parametrize("arm", VARIANTS[:4])
def test_final_verdict_pass_and_text_fraction_not_applicable(fields, arm):
    screening = good_result(mean=0.02 if arm.endswith("num") else 100)
    assert evaluate.final_verdict(arm, good_result(), fields, screening=screening)[0] == "pass"


@pytest.mark.parametrize("change,expected", [({"count": 19}, "insufficient"), ({"t_stat": None}, "insufficient"),
                                            ({"t_stat": 2.0}, "fail"), ({"mean": 0.0}, "fail")])
def test_final_verdict_observation_and_strict_stat_boundaries(fields, change, expected):
    assert evaluate.final_verdict("b_text", good_result(**change), fields)[0] == expected


def test_verdict_screening_fail_numeric_direction_fraction_trials_and_probe(fields):
    screen = {**good_result(), "verdict": "screening_fail"}
    assert evaluate.final_verdict("c_num", good_result(), fields, screening=screen)[0] == "screening_fail"
    assert evaluate.final_verdict("c_num", good_result(), fields, screening=good_result(mean=0.06))[0] == "fail"
    assert evaluate.final_verdict("c_num", good_result(), fields)[0] == "insufficient"
    assert evaluate.final_verdict("b_text", good_result(t_stat=2.9), fields, trial_count=21)[0] == "fail"
    assert evaluate.final_verdict("b_text", good_result(t_stat=3.01), fields, trial_count=21)[0] == "pass"
    assert evaluate.final_verdict("b_text", good_result(), fields, clean_verdict="insufficient_clean_window")[0] == "insufficient_clean_window"
    assert evaluate.screening_verdict(good_result(t_stat=1.0))[0] == "screening_fail"
    assert evaluate.screening_verdict(good_result(t_stat=1.01))[0] == "pass"


def test_hc002_not_applicable_and_same_direction_half_effect():
    assert evaluate.hc002_verdict({}, None)[0] == "not_applicable"
    row = {"metrics_json": json.dumps({"design_mean_excess": 0.02})}
    assert evaluate.hc002_verdict({"excess": {"mean": 0.01, "count": 5}}, row)[0] == "pass"
    assert evaluate.hc002_verdict({"excess": {"mean": 0.0099, "count": 5}}, row)[0] == "fail"


def test_holdout_guard_refuses_implicit_claim(offline_repo):
    with pytest.raises(ValueError, match="open HoldoutSession"):
        guard_data("2026-01-01", "2026-01-31", repo_root=offline_repo)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


def test_screening_horizon_exclusions_and_final_weekly_calendar():
    sessions = pd.bdate_range("2015-01-01", "2026-11-30")
    no_december = sessions[sessions.to_period("M") != pd.Period("2025-12")]
    planned, excluded = hc002.rebalance_calendar(no_december)
    assert sum(row["reason"] == "holdout_horizon" for row in excluded) == 1
    assert all(row["exit_date"] < "2026-01-01" for row in planned)
    weekly, _ = cli.weekly_calendar(sessions, "2026-05-01")
    assert all(row["decision_date"] <= "2026-09-30" and row["exit_date"] <= "2026-10-30" for row in weekly)
    for row in weekly:
        position = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        assert row["trade_date"] == sessions[position + 1].date().isoformat()
        assert row["exit_date"] == sessions[position + 20].date().isoformat()


def test_final_refuses_without_probe_before_any_source_or_session(offline_repo, monkeypatch):
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("holdout opened"))
    monkeypatch.setattr(cli, "price_days", lambda *a: pytest.fail("data inventory read"))
    with pytest.raises(ValueError, match="probe"):
        cli.execute_final(data_dir=offline_repo, repo_root=offline_repo)


@pytest.mark.parametrize("state", ["insufficient", "insufficient_clean_window"])
def test_final_refuses_insufficient_probe_no_holdout(offline_repo, monkeypatch, state):
    save_json(workspace(offline_repo) / "probe.json", {"verdict": state, "smoke": False})
    monkeypatch.setattr(holdout, "HoldoutSession", lambda *a, **k: pytest.fail("holdout opened"))
    with pytest.raises(ValueError, match="refuses"):
        cli.execute_final(data_dir=offline_repo, repo_root=offline_repo)


def test_final_refuses_latest_krx_day_before_october_30(offline_repo, monkeypatch):
    scores = probe_scores()
    save_json(workspace(offline_repo) / "probe.json", {**probe.clean_window(scores), "scores": scores})
    monkeypatch.setattr(cli, "price_days", lambda *a: pd.DatetimeIndex(["2026-10-02"]))
    with pytest.raises(ValueError, match="2026-10-30"):
        cli.execute_final(data_dir=offline_repo, repo_root=offline_repo)


def test_publish_five_variants_and_smoke_never_recorded(offline_repo, monkeypatch):
    recorded = []
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: recorded.append((a, k)))
    payload = cli._payload("screening", {arm: cli._unmeasured("insufficient", "synthetic") for arm in VARIANTS}, 0)
    result, paths = evaluate.publish(payload, repo_root=offline_repo, smoke=True)
    assert result["smoke"] and recorded == []
    assert "Smoke: True" in paths[1].read_text()
    _, paths = evaluate.publish(payload, repo_root=offline_repo)
    assert [args[1] for args, kwargs in recorded] == list(VARIANTS)
    assert "NW lag 4" in paths[1].read_text() and "IC 평균" not in paths[1].read_text()


def test_cli_dry_runs_read_only_estimates_and_budget(offline_repo, monkeypatch, capsys):
    monkeypatch.setattr(inputs, "load_history", lambda *a, **k: pytest.fail("source read"))
    monkeypatch.setattr(probe, "read_contamination_facts", lambda *a, **k: pytest.fail("protected probe source read"))
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: pytest.fail("registry write"))
    before = list(offline_repo.rglob("*"))
    assert cli.main(["probe", "--data-dir", str(offline_repo)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["estimated_calls"] == 21 and output["cache_hits"] == 0 and output["budget"]["remaining"] == 1100
    assert cli.main(["status", "--data-dir", str(offline_repo)]) == 0
    assert json.loads(capsys.readouterr().out)["holdout_claimed"] is False
    assert before == list(offline_repo.rglob("*"))
    plan = cli.dry_run("screening", data_dir=offline_repo, repo_root=offline_repo,
                       sessions=pd.bdate_range("2015-01-01", "2026-11-30"))
    assert plan["decisions"] == 103 and plan["memory_diagnostic_decisions"] == 35 and plan["estimated_calls"] == 344


def test_snapshot_resume_verifies_hash_and_does_not_reread(offline_repo):
    path = workspace(offline_repo) / "final_snapshot.json"
    first = cli._snapshot(path, lambda: {"data": "synthetic frozen"}, metadata={"stage": "final"})
    second = cli._snapshot(path, lambda: pytest.fail("protected reread"), metadata={"stage": "final"})
    assert first == second
    saved = read_json(path)
    saved["data"]["data"] = "changed"
    save_json(path, saved)
    with pytest.raises(StageStopped, match="snapshot"):
        cli._snapshot(path, lambda: None, metadata={"stage": "final"})


def test_assumptions_explicit_contamination_and_frozen_resume():
    assert any("does not call guard_period" in note for note in ASSUMPTIONS)
    assert any("frozen snapshot" in note for note in ASSUMPTIONS)


def test_runner_sends_long_prompts_through_stdin_without_truncation(tmp_path):
    process = FakeProcess()
    runner = llm(tmp_path, process)
    long_input = "한국어" * 50000
    seen = []
    original = process.__call__

    def capture(command, **kwargs):
        if command != ["codex", "--version"]:
            seen.append(kwargs["input"])
        return original(command, **kwargs)

    runner.subprocess_call = capture
    try:
        runner.call("v1_b_text.txt", long_input, "v1_text.schema.json", 0)
    except Exception:
        pass
    assert seen and long_input in seen[0]


def test_identity_invalidation_is_durable(tmp_path):
    runner = llm(tmp_path, FakeProcess(versions=[CODEX_VERSION, "codex-cli 0.161.0"]))
    with pytest.raises(StageStopped):
        call_stage1(runner)
    resumed = FakeProcess()
    with pytest.raises(StageStopped, match="invalidated"):
        call_stage1(llm(tmp_path, resumed))
    assert resumed.execs == 0


def test_minimum_events_enforced_conservatively_in_addition_to_weekly_count(fields):
    result = good_result()
    result["events"] = 299
    assert evaluate.final_verdict("b_text", result, fields)[0] == "insufficient"


def test_final_opens_before_source_reads_and_resume_uses_one_frozen_snapshot(offline_repo, monkeypatch):
    scores = probe_scores()
    root = workspace(offline_repo)
    save_json(root / "probe.json", {**probe.clean_window(scores), "scores": scores})
    save_json(root / "screening.json", {"smoke": False, "variants": {arm: good_result() for arm in VARIANTS[:4]}})
    monkeypatch.setattr(cli, "price_days", lambda *a: pd.DatetimeIndex(["2026-10-30"]))
    sessions = pd.bdate_range("2015-01-01", "2026-11-30")
    opened, inside = [], [False]
    class Session:
        def __init__(self, experiment, **kwargs):
            assert experiment == EXPERIMENT_ID
        def __enter__(self):
            opened.append(1)
            inside[0] = True
        def __exit__(self, *args):
            inside[0] = False
    monkeypatch.setattr(holdout, "HoldoutSession", Session)
    frame, decisions = metric_example()
    def source(*args, **kwargs):
        assert inside[0]
        return {"prepared": [], "observations": inputs.json_value(frame.to_dict("records")), "exclusions": []}
    def linked(**kwargs):
        assert inside[0]
        return cli._unmeasured("not_applicable", "no passing HC002 row")
    monkeypatch.setattr(cli, "_prepare_decisions", source)
    monkeypatch.setattr(cli, "_prepare_hc002", linked)
    monkeypatch.setattr(cli, "_run_snapshot", lambda *a, **k: (decisions, []))
    recorded = []
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: recorded.append(a[1]))
    monkeypatch.setattr(experiment_registry, "trial_count", lambda *a, **k: 0)
    runner = FakeArmRunner(offline_repo)
    first = cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, runner=runner, sessions=sessions)
    assert first["stage"] == "final" and len(opened) == 1 and recorded == list(VARIANTS)
    monkeypatch.setattr(cli, "_prepare_decisions", lambda *a, **k: pytest.fail("protected source reread"))
    # Model-call interruption before publication can resume the identical frozen
    # acquisition. The source session is not opened a second time.
    second = cli.execute_final(data_dir=offline_repo, repo_root=offline_repo, runner=runner, sessions=sessions)
    assert len(opened) == 1 and second["snapshot_hash"] == first["snapshot_hash"]
    assert second["completed_stage_reused"] and len(recorded) == 5


def test_execute_screening_smoke_not_recorded_or_official_state(offline_repo, monkeypatch):
    frame, decisions = metric_example()
    snapshot = {"prepared": [], "observations": inputs.json_value(frame.to_dict("records")), "exclusions": []}
    monkeypatch.setattr(cli, "_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(cli, "_run_snapshot", lambda *a, **k: (decisions, []))
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: pytest.fail("smoke registry write"))
    result = cli.execute_screening(data_dir=offline_repo, repo_root=offline_repo, limit_decisions=1,
                                   runner=FakeArmRunner(offline_repo), sessions=pd.bdate_range("2015-01-01", "2026-11-30"))
    assert result["smoke"] and not (workspace(offline_repo) / "screening.json").exists()


def test_hc002_linked_evaluation_uses_fixed_month_end_rules(offline_repo, monkeypatch):
    sessions = pd.bdate_range("2015-01-01", "2026-11-30")
    monkeypatch.setattr(evaluate, "hc002_passing_row", lambda *a: {"metrics_json": json.dumps({"design_mean_excess": 0.01})})
    rows = []
    for month in pd.period_range("2026-01", "2026-05", freq="M"):
        day = sessions[sessions.to_period("M") == month][-1]
        for code, phase, gross in (("000010", 2, 0.04), ("000020", 3, 0.00)):
            rows.append({"decision_date": day, "stock_code": code, "phase": phase, "gross": gross,
                         "entry_date": sessions[sessions.get_loc(day) + 1], "exit_date": sessions[sessions.get_loc(day) + 20],
                         "avg_trading_value_20d": 2e9, **{f"cost_{m}": 0.004 for m in (1.0, 1.5, 2.0)}})
    def prepared(planned, **kwargs):
        assert len(planned) == 5
        assert all(row["exit_date"] <= "2026-06-30" for row in planned)
        for row in planned:
            month = pd.Timestamp(row["decision_date"]).to_period("M")
            assert row["decision_date"] == sessions[sessions.to_period("M") == month][-1].date().isoformat()
        return {"observations": inputs.json_value(rows), "exclusions": []}
    monkeypatch.setattr(cli, "_prepare_decisions", prepared)
    result = cli._prepare_hc002(data_dir=offline_repo, repo_root=offline_repo, sessions=sessions)
    assert result["verdict"] == "pass" and result["primary"]["count"] == 5
    assert result["primary"]["mean"] == pytest.approx(0.016)


def test_newey_west_preserves_missing_calendar_weeks():
    values = np.array([0.01, 0.03, -0.01, 0.05])
    positions = np.array([0, 1, 5, 6])
    residual = values - values.mean()
    weights = np.maximum(0, 1 - np.abs(positions[:, None] - positions[None, :]) / 5)
    variance = residual @ weights @ residual / len(values) ** 2
    result = evaluate.newey_west(values, positions=positions)
    assert result["standard_error"] == pytest.approx(np.sqrt(variance))
    assert result["t_stat"] != evaluate.newey_west(values)["t_stat"]


def test_stage_bounded_presence_scan_never_reads_2026_in_screening(tmp_path):
    for day, contents in (("20251231", json.dumps({"trade_date": "2025-12-31", "stock_code": "000010"}) + "\n"),
                          ("20260102", "forbidden future data\n")):
        path = tmp_path / "market/krx_daily" / day[:4] / (day + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    dates = inputs.load_last_dates("2025-01-01", "2025-12-31", data_dir=tmp_path)
    assert dates["000010"] == pd.Timestamp("2025-12-31")


def test_missing_exit_uses_last_close_when_bounded_inventory_has_later_row(synthetic):
    prices, days, _, _, _, _ = synthetic
    code = "000010"
    limited = prices.loc[prices.index.get_level_values("trade_date") <= days[70]].drop((days[70], code))
    observed = evaluate.observations(limited, observation_universe(days, days[50], [code]),
                                     last_dates=pd.Series({code: days[-1]})).iloc[0]
    assert observed.stale_exit and pd.isna(observed.reason) and observed.exit_date == days[69]
    without_later = evaluate.observations(limited, observation_universe(days, days[50], [code])).iloc[0]
    assert without_later.reason == "no_later_price"


def test_synthetic_source_to_text_arms_pipeline_with_injected_loader(synthetic, monkeypatch):
    prices, days, known, profiles, benchmarks, root = synthetic
    day = days[270]
    end = days[290]
    planned = [{"decision_date": day.date().isoformat(), "trade_date": days[271].date().isoformat(), "exit_date": end.date().isoformat()}]
    monkeypatch.setattr(cli, "price_days", lambda *args: days)
    def load(start, finish, **kwargs):
        assert pd.Timestamp(finish) == end
        dates = prices.index.get_level_values("trade_date")
        return prices.loc[(dates >= start) & (dates <= finish)]
    monkeypatch.setattr(inputs, "load_history", load)
    monkeypatch.setattr(inputs, "load_last_dates", lambda *a, **k: pd.Series({code: days[-1] for code in profiles}))
    monkeypatch.setattr(inputs, "load_financials", lambda *a, **k: known)
    monkeypatch.setattr(hc001, "_industries", lambda *a: profiles)
    monkeypatch.setattr(inputs, "load_benchmarks", lambda *a, **k: benchmarks)
    snapshot = cli._prepare_decisions(planned, data_dir=root, repo_root=root, sessions=days,
                                      with_text=True, text_loader=lambda *args: extracted())
    assert len(snapshot["prepared"]) == 1 and len(snapshot["observations"]) == 60 and not snapshot["exclusions"]
    selected, diagnostics = cli._run_snapshot(snapshot, FakeArmRunner(root), final=True, data_dir=root)
    assert not diagnostics and len(selected[0]["arms"]["c_text"]["selected"]) == 10
    result = evaluate.metrics(cli._frame(snapshot), selected)
    assert result["variants"]["c_text"]["primary"]["count"] == 1


def test_real_holdout_context_claims_once_for_repeated_lh001_guards(offline_repo, monkeypatch):
    monkeypatch.setattr(holdout, "verify_preregistration", lambda *a: SimpleNamespace(fields={"uses_holdout": True}))
    with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=offline_repo):
        guard_data("2026-01-01", "2026-01-31", repo_root=offline_repo)
        guard_data("2026-05-01", "2026-10-30", repo_root=offline_repo)
    rows = (offline_repo / holdout.LEDGER_PATH).read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["experiment_id"] == EXPERIMENT_ID
    with pytest.raises(ValueError, match="already used"):
        with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=offline_repo):
            pytest.fail("reopened holdout")


@pytest.mark.parametrize("metric", ["random", "stressed"])
def test_final_verdict_random_and_stressed_cost_failures(fields, metric):
    result = good_result()
    if metric == "random":
        result["random_control"]["share_of_controls"] = 0.0501
    else:
        result["cost_sensitivity"]["1.5"]["top_net_excess"] = 0.0
    assert evaluate.final_verdict("b_text", result, fields)[0] == "fail"


def test_runner_refuses_missing_persistent_budget_instead_of_resetting(tmp_path):
    process = FakeProcess()
    runner = llm(tmp_path, process)
    assert call_stage1(runner)["valid"]
    (workspace(tmp_path) / "budget.json").unlink()
    with pytest.raises(StageStopped, match="budget is missing"):
        call_stage1(llm(tmp_path, FakeProcess()), repeat=1)


@pytest.mark.parametrize("reader", ["financials", "benchmarks", "disclosures"])
def test_protected_input_readers_refuse_without_a_session_even_after_june(offline_repo, reader):
    with pytest.raises(ValueError, match="open HoldoutSession"):
        if reader == "financials":
            inputs.load_financials("2026-07-01", data_dir=offline_repo, repo_root=offline_repo)
        elif reader == "benchmarks":
            inputs.load_benchmarks(offline_repo, "2026-01-01", "2026-07-01", repo_root=offline_repo)
        else:
            inputs.disclosure_counts(offline_repo, pd.bdate_range("2026-03-01", periods=60), {"000010"}, repo_root=offline_repo)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()


def test_decision_day_date_only_disclosures_never_enter_close_known_input(synthetic):
    _, days, _, _, _, root = synthetic
    day = days[270]
    prior = days[days < day][-60:]
    for stamp in pd.date_range(prior[0], prior[-1]):
        path = root / "disclosures/dart_full/list" / str(stamp.year) / f"{stamp:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    for stamp, title in ((prior[-1], "단일판매·공급계약체결"), (day, "전환사채발행결정")):
        path = root / "disclosures/dart_full/list" / str(stamp.year) / f"{stamp:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"stock_code": "000010", "rcept_dt": f"{stamp:%Y%m%d}", "rcept_no": f"{stamp:%Y%m%d}000001", "report_nm": title}
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
    bundle = bundle_for(synthetic)
    stock = bundle["stocks"][0]
    assert stock["listing_coverage"]["complete"] and stock["disclosures_contract"] == 1
    assert stock["disclosures_convertible_bond"] == 0
    assert all(title["date"] < bundle["decision_date"] for title in stock["titles"])
    assert "전환사채발행결정" not in inputs.render(bundle, anonymised=False)[0]


def test_invalid_attempt_count_deduplicates_cached_call_reuse_across_decisions():
    frame, decisions = metric_example()
    later = frame.copy()
    later["decision_date"] += pd.Timedelta(days=7)
    later_decision = {"decision_date": later.decision_date.iloc[0].date().isoformat(), "arms": decisions[0]["arms"]}
    decisions[0]["arms"]["b_num"].update(invalid_calls=1, invalid_by_call={"same_cached_call": 1})
    result = evaluate.metrics(pd.concat([frame, later]), decisions + [later_decision])
    assert result["variants"]["b_num"]["invalid_calls"] == 1
    assert result["invalid_call_count"] == 1


def test_valid_business_reason_mentioning_quota_is_not_a_usage_stop(tmp_path):
    output = {"ranked": ranked(["a", "b", "c"])}
    output["ranked"][0]["reason"] = "quota contract changes are stated in the supplied business data"
    process = FakeProcess(output)
    assert call_stage1(llm(tmp_path, process))["valid"]
    assert process.execs == 1


def test_stock_table_daily_turnover_separate_from_cap_and_cost_adv(synthetic):
    prices, days, known, profiles, benchmarks, root = synthetic
    prices = prices.copy()
    prices.loc[(days[270], "000010"), "trading_value"] = 1e11
    bundle = inputs.build_inputs(prices, days[270], data_dir=root, known=known, profiles=profiles, benchmarks=benchmarks)
    stock = bundle["stocks"][0]
    assert stock["trading_value"] == 1e11 and stock["adv_20"] == pytest.approx((19 * 2e9 + 1e11) / 20)
    assert stock["trading_value_percentile"] == 100
    rendered, _ = inputs.render(bundle, anonymised=True)
    assert "100000000000" not in rendered and "adv_20" not in rendered
