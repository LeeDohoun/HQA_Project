"""Round 23: synthetic distributions, actual-module pins and per-run dates."""
import json
from pathlib import Path

import pytest

from backtesting import experiment_registry
from backtesting.experiments.lh003 import EVALUATION_AT, Refused
from backtesting.experiments.lh003 import calendar, environment, evaluate, executions, inputs, pins, runner, workflow
from backtesting.experiments.lh003.distribution import latest, protected
from backtesting.experiments.lh003.simulation import synthetic_distribution
from backtesting.experiments.lh003.storage import aware, digest, read, verified
from scripts.lh003.distribute import publish
from src.ingestion.dart_filing_dates import known_date
from src.ingestion.storage import read_rows, write_rows
from src.runner import trading_calendar as shared
from test_experiments_lh003_evaluate import pinned_start
from test_experiments_lh003_runner import Clock, Process, forbid_real_calls_and_holdout, instance, verified_calendar, weekly, bundle
from test_experiments_lh003_snapshot import snapshot_for, source, source_fixture
from test_experiments_lh001 import synthetic

AT = "2026-11-13T18:00:00+09:00"


def republish(snapshot, source, tmp_path):
    snapshot.distribution, sync = synthetic_distribution(source, tmp_path / "updated", at=aware(AT))
    return sync


def test_distribution_has_entire_fixed_scope_without_h1_rows_or_checkpoint_summaries(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    publication = snapshot.distribution
    assert "market_context/benchmarks.jsonl" in publication.manifest["files"]
    assert "reference/corp_codes.csv" in publication.manifest["files"]
    assert not any("_state" in name for name in publication.manifest["files"])
    for name in publication.manifest["files"]:
        if not name.endswith(".jsonl") or name.startswith("reference/"):
            continue
        for row in read_rows(publication.path(name)):
            day = (known_date(row["rcept_no"], row.get("rcept_dt")) if name.startswith(("fundamentals/", "disclosures/"))
                   else row["trade_date"])
            assert not protected(day)
    assert all(not protected(d) for d in read(publication.path("lh003_coverage.json"))["covered_listing_days"])
    assert len(list((publication.root / "fundamentals/dart_quarterly").glob("*.jsonl"))) == len(list((source / "fundamentals/dart_quarterly").glob("*.jsonl")))


def test_actual_public_date_controls_distribution_not_receipt_or_fiscal_period(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    path = source / "fundamentals/dart_quarterly/2026_11012.jsonl"
    row = {**read_rows(path)[0], "rcept_no": "20260630000999", "available_date": "2026-06-30"}
    write_rows(path, [*read_rows(path), row])
    write_rows(source / "disclosures/dart_full/list/2026/20260701.jsonl", [{"corp_code": row["corp_code"],
        "stock_code": row["stock_codes"][0], "rcept_no": row["rcept_no"], "rcept_dt": "20260701",
        "report_nm": "정정 반기보고서 (2026.06)"}])
    republish(snapshot, source, tmp_path)
    assert row in read_rows(snapshot.distribution.path(path.relative_to(source).as_posix()))


def test_new_nonh1_financial_and_benchmark_versions_are_captured_but_new_h1_is_not(source, tmp_path):
    snapshot = snapshot_for(source, tmp_path)
    before = digest(snapshot.distribution.manifest)
    path = source / "fundamentals/dart_quarterly/2026_11012.jsonl"
    original = read_rows(path)[0]
    nonh1 = {**original, "rcept_no": "20261112000999", "available_date": "2026-11-12"}
    h1 = {**original, "rcept_no": "20260601000999", "available_date": "2026-06-01", "currency": "H1-SECRET"}
    write_rows(path, [*read_rows(path), nonh1, h1])
    for row in (nonh1, h1):
        stamp = row["available_date"].replace("-", "")
        listing = source / "disclosures/dart_full/list/2026" / (stamp + ".jsonl")
        write_rows(listing, [*read_rows(listing), {"corp_code": row["corp_code"], "stock_code": row["stock_codes"][0],
            "rcept_no": row["rcept_no"], "rcept_dt": stamp, "report_nm": "정정 반기보고서 (2026.06)"}])
    bench = source / "market_context/benchmarks.jsonl"
    added_index = {"trade_date": "2026-11-16", "series": "KOSPI", "index_name": "코스피", "close": 1234}
    write_rows(bench, [*read_rows(bench), added_index])
    # Existence in raw storage does not backfill a publication already sealed.
    assert nonh1["rcept_no"].encode() not in snapshot.stream(path.relative_to(source).as_posix())
    republish(snapshot, source, tmp_path)
    assert digest(snapshot.distribution.manifest) != before
    merged = snapshot.stream(path.relative_to(source).as_posix())
    assert nonh1["rcept_no"].encode() in merged and b"H1-SECRET" not in merged
    assert added_index in [json.loads(line) for line in snapshot.stream("market_context/benchmarks.jsonl").splitlines()]


def test_research_never_inventories_or_reads_raw_files(source, tmp_path, monkeypatch):
    snapshot = snapshot_for(source, tmp_path)
    opened, globbed = Path.open, Path.glob
    def guard_open(path, *a, **k):
        if path.resolve().is_relative_to(source.resolve()):
            pytest.fail("research read a raw source")
        return opened(path, *a, **k)
    def guard_glob(path, *a, **k):
        if path.resolve().is_relative_to(source.resolve()):
            pytest.fail("research inventoried a raw source")
        return globbed(path, *a, **k)
    monkeypatch.setattr(Path, "open", guard_open)
    monkeypatch.setattr(Path, "glob", guard_glob)
    assert len(inputs.prepare(source, snapshot, "2026-11-13")["bundle"]["a_codes"]) == 40


def test_missing_failed_or_future_publication_never_uses_previous_update(source, tmp_path):
    directory = tmp_path / "distributions"
    with pytest.raises(Refused, match="no completed"):
        latest(directory, sync_manifest=tmp_path / "absent", at=aware(AT))
    publication, sync = synthetic_distribution(source, directory, at=aware(AT))
    with pytest.raises(Refused, match="publication time"):
        latest(directory, sync_manifest=sync, at=aware("2026-11-13T17:59:59+09:00"), allow_synthetic=True)
    bad = tmp_path / "bad_sync.json"
    value = read(sync)
    del value["files"]["market_context/benchmarks.jsonl"]
    bad.write_text(json.dumps(value))
    with pytest.raises(Refused, match="entire fixed archive scope"):
        publish(data_dir=source, directory=directory, sync_manifest=bad, clock=Clock(), synthetic=True)
    with pytest.raises(Refused, match="failed or is incomplete"):
        latest(directory, sync_manifest=sync, at=aware(AT), allow_synthetic=True)
    assert publication.path("market_context/benchmarks.jsonl").is_file()


def test_distribution_detects_mutation_during_capture(source, tmp_path, monkeypatch):
    from scripts.lh003 import distribute
    _, sync = synthetic_distribution(source, tmp_path / "first", at=aware(AT))
    original = distribute.financial_dates
    def changed(*a):
        dates = original(*a)
        (source / "market_context/benchmarks.jsonl").write_text("changed during capture\n")
        return dates
    monkeypatch.setattr(distribute, "financial_dates", changed)
    with pytest.raises((Refused, json.JSONDecodeError)):
        publish(data_dir=source, directory=tmp_path / "failed", sync_manifest=sync, clock=Clock(), synthetic=True)
    assert not list((tmp_path / "failed/publications").glob("*.complete.json"))


@pytest.mark.parametrize("day", [None, "2026-02-30"])
def test_unclassifiable_trade_date_fails_publication_instead_of_exporting_row(source, tmp_path, day):
    snapshot = snapshot_for(source, tmp_path)
    path = source / "market_context/benchmarks.jsonl"
    write_rows(path, [*read_rows(path), {"trade_date": day, "series": "KOSPI", "index_name": "코스피", "close": 1234}])
    with pytest.raises(Refused, match="invalid archive date"):
        republish(snapshot, source, tmp_path)
    assert not list((tmp_path / "updated/publications").glob("*.complete.json"))


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    root = tmp_path / "state"
    pinned_start(root, tmp_path)
    # The copied test modules stand in for the loaded modules. No source file
    # in the real collector/worktree is modified by these amendment tests.
    monkeypatch.setattr(pins, "loaded_hashes", lambda: runner.mechanical_data_hashes(tmp_path))
    return root, tmp_path


def tax_update(repo):
    path = repo / "backtesting/cost_model.py"
    old = '    (date(2026, 1, 1), {"KOSPI": 0.0020, "KOSDAQ": 0.0020}),'
    path.write_text(path.read_text().replace(old, old + '\n    (date(2027, 1, 1), {"KOSPI": 0.0019, "KOSDAQ": 0.0019}),'))
    return "backtesting/cost_model.py"


def calendar_update(repo):
    path = repo / "src/runner/trading_calendar.py"
    path.write_text(path.read_text().replace('CALENDAR_VERIFIED_THROUGH = "2026-12-31"',
        'CALENDAR_VERIFIED_THROUGH = "2027-12-31"').replace("min(2026, year + 1)", "min(2027, year + 1)"))
    return "src/runner/trading_calendar.py"


def amend(root, repo, path, **kwargs):
    return pins.record_amendment(root, repo, path=path, legal_source="https://official.example.test/notice",
        effective_date="2027-01-01", changed_at="2026-11-13T17:00:00+09:00", clock=Clock(), **kwargs)


@pytest.mark.parametrize("update", [tax_update, calendar_update])
def test_actual_module_update_requires_amendment_then_preserves_model_identity(pinned, update):
    root, repo = pinned
    model = runner.LlmRunner(root, repo_root=repo, subprocess_call=Process(), clock=Clock())
    baseline = model.identity()
    path = update(repo)
    with pytest.raises(Refused, match="pinned implementation"):
        runner.verify_pinned(root, repo, at=Clock()())
    record = amend(root, repo, path)
    applied = runner.verify_pinned(root, repo, at=Clock()())
    assert applied["implementation_hashes"][path] == record["after_sha256"]
    assert applied["amendment_hashes"] == [digest(record)]
    assert model.identity()["implementation_hashes"] == baseline["implementation_hashes"]
    assert not (root / "model_stopped.json").exists()
    historical = pins.verify(root, repo, at="2026-11-13T16:00:00+09:00", current={}, historical=True)
    assert historical["amendment_hashes"] == []


@pytest.mark.parametrize("path,old,new", [
    ("backtesting/cost_model.py", "0.00015", "0.00016"),
    ("backtesting/cost_model.py", '"KOSDAQ": 0.0020', '"KOSDAQ": float(0.0020)'),
    ("src/runner/trading_calendar.py", "min(2026, year + 1)", "min(2026, year + 2)"),
    ("src/runner/trading_calendar.py", 'CALENDAR_VERIFIED_THROUGH = "2026-12-31"', 'CALENDAR_VERIFIED_THROUGH = "2027-12-31"'),
    ("src/runner/trading_calendar.py", 'if day in PENDING_SPECIAL_SESSIONS', 'if False and day in PENDING_SPECIAL_SESSIONS'),
])
def test_ast_rejects_algorithm_changes_executable_data_and_incomplete_boundary(pinned, path, old, new):
    root, repo = pinned
    target = repo / path
    assert old in target.read_text()
    target.write_text(target.read_text().replace(old, new))
    with pytest.raises(Refused, match="AST|literal|boundary"):
        amend(root, repo, path)
    assert not list((root / "amendments").glob("*.json"))


def test_amendment_chain_and_actually_loaded_hash_are_verified(pinned, monkeypatch):
    root, repo = pinned
    path = tax_update(repo)
    record = amend(root, repo, path)
    monkeypatch.setattr(pins, "loaded_hashes", lambda: {**runner.mechanical_data_hashes(repo), path: record["before_sha256"]})
    with pytest.raises(Refused, match="loaded code"):
        runner.verify_pinned(root, repo, at=Clock()())
    journal = root / "amendments/000000.json"
    journal.chmod(0o644)
    journal.write_text(journal.read_text().replace(record["before_sha256"], "0" * 64))
    with pytest.raises(Refused, match="hash mismatch"):
        runner.verify_pinned(root, repo, at=Clock()())


@pytest.mark.parametrize("bad", ["source", "effective", "time"])
def test_amendment_requires_source_effective_date_and_change_time(pinned, bad):
    root, repo = pinned
    path = tax_update(repo)
    values = dict(legal_source="https://official.example.test/notice", effective_date="2027-01-01", changed_at=AT)
    values[{"source": "legal_source", "effective": "effective_date", "time": "changed_at"}[bad]] = ""
    with pytest.raises(ValueError):
        pins.record_amendment(root, repo, path=path, clock=Clock(), **values)


def test_code_pin_is_checked_before_weekly_A_and_no_bad_fallback_remains(weekly):
    path = weekly["repo_root"] / "backtesting/experiments/lh001/inputs.py"
    path.write_text(path.read_text() + "\n# algorithm drift\n")
    weekly["prepared_loader"] = lambda *a: pytest.fail("A generated before pin verification")
    process = Process()
    with pytest.raises(Refused, match="pinned implementation"):
        workflow.run_week(**weekly, runner=instance(weekly["root"], process))
    assert process.execs == 0
    assert not (weekly["root"] / "weeks/00/input.json").exists()
    assert not (weekly["root"] / "weeks/00/fallback.json").exists()


def test_decision_records_applied_code_amendment_and_distribution_hashes(weekly):
    decision = workflow.run_week(**weekly, runner=instance(weekly["root"], Process()))
    prepared = verified(weekly["root"] / "weeks/00/input.json")
    assert decision["code_pins"] == prepared["code_pins"]
    assert decision["distribution"] == prepared["distribution"]
    assert decision["code_pins"]["code_baseline_sha256"]
    assert set(pins.MECHANICAL).issubset(decision["code_pins"]["implementation_hashes"])


def test_environment_checks_pins_before_A_generation(pinned, monkeypatch):
    root, repo = pinned
    tax_update(repo)  # no amendment
    monkeypatch.setattr(environment, "PROJECT_ROOT", repo)
    monkeypatch.setattr(environment, "synthetic_archive", lambda *a, **k: pytest.fail("A before pin check"))
    with pytest.raises(Refused, match="pinned implementation"):
        environment.verify_environment(data_dir=repo / "missing", state_root=root, synthetic=True, clock=Clock())


def test_execution_checks_B_before_reading_any_prices(pinned):
    root, repo = pinned
    calendar_update(repo)  # no amendment
    with pytest.raises(Refused, match="pinned implementation"):
        executions.record_executions(data_dir=repo / "missing", root=root, decisions=[],
            sync_manifest=repo / "absent.json", clock=Clock(), repo_root=repo)


def test_approved_B_is_used_by_environment_execution_and_final_evaluation(pinned, monkeypatch):
    from types import SimpleNamespace

    root, repo = pinned
    record = amend(root, repo, tax_update(repo))
    monkeypatch.setattr(environment, "PROJECT_ROOT", repo)
    report = environment.verify_environment(data_dir=repo / "missing", state_root=root, clock=Clock())
    assert report["code_pins"]["amendment_hashes"] == [digest(record)]
    manifest = repo / "execution_sync.json"
    manifest.write_text(json.dumps({"as_of": EVALUATION_AT, "files": {}}))
    monkeypatch.setattr(executions, "latest_distribution", lambda *a, **k: (SimpleNamespace(root=repo), {}))
    with pytest.raises(Refused, match="timely real decision"):
        executions.record_executions(data_dir=repo, root=root, decisions=[], sync_manifest=manifest,
            repo_root=repo, clock=Clock(EVALUATION_AT))
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a, **k: SimpleNamespace(commit_hash="fixture", fields={}))
    result = evaluate.evaluate_saved(root=root, repo_root=repo, clock=Clock(EVALUATION_AT))
    assert result["code_pins"]["amendment_hashes"] == [digest(record)]


def test_loaded_hash_is_not_relabelled_by_a_file_change(monkeypatch):
    original = pins.loaded_hashes()
    monkeypatch.setattr(pins, "file_hash", lambda *a: "changed-source-on-disk")
    assert pins.loaded_hashes() == original


def test_weekly_requires_completed_distribution_before_A(weekly, monkeypatch):
    monkeypatch.setattr(workflow, "latest_distribution", latest)
    weekly["prepared_loader"] = lambda *a: pytest.fail("A generated without completed distribution")
    process = Process()
    with pytest.raises(Refused, match="no completed"):
        workflow.run_week(**weekly, runner=instance(weekly["root"], process))
    assert not (weekly["root"] / "weeks/00/input.json").exists() and process.execs == 0


def test_first_week_keeps_52_slots_without_requiring_future_calendar(monkeypatch):
    monkeypatch.setattr(shared, "CALENDAR_VERIFIED_THROUGH", "2026-12-31")
    monkeypatch.setattr(shared, "SPECIAL_CLOSES", {})
    description = calendar.description(decision_day="2026-11-13")
    assert len(description["slots"]) == 52 and len(description["decisions"]) == 1
    assert description["decisions"][0]["index"] == 0
    assert description["decisions"][0]["exit_date"].startswith("2026-")
    with pytest.raises(ValueError, match="calendar_unverified"):
        calendar.description(decision_day="2027-01-08")
    assert calendar.WEEKS[-1].end_time.date().isoformat() == "2027-11-05"


def test_unverified_needed_exit_stops_instead_of_dropping_slot(monkeypatch):
    monkeypatch.setattr(shared, "CALENDAR_VERIFIED_THROUGH", "2026-12-31")
    with pytest.raises(ValueError, match="calendar_unverified"):
        calendar.description(decision_day="2026-12-18")


def test_next_week_calendar_scope_does_not_require_unrelated_change_note(tmp_path):
    first = calendar.check(tmp_path, at=Clock()(), decision_day="2026-11-13")
    second = calendar.check(tmp_path, at=Clock()(), decision_day="2026-11-20")
    assert first["decision_scope"] == 0 and second["decision_scope"] == 1
    assert len(second["slots"]) == 52
