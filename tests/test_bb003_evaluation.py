"""Calendar, costs, final judgement, acquisition cutoff and count-only reports."""
import copy
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model
from backtesting.experiments import bb001
from backtesting.experiments.bb003 import KST, MINIMUM_EFFECT, NotReady
from backtesting.experiments.bb003 import evaluate as e, inputs, policy, recorder, storage
from test_bb003_recorder import env, fake_publication, operation_verification, run


def metric_rows():
    grid = pd.bdate_range("2026-12-01", "2028-10-30")
    def position(code, gross):
        return {"stock_code": code, "gross": gross, "vanished": False, "gross_path": [gross] * 21,
            "costs": {"1.0": .003, "1.5": .004, "2.0": .005, "v1": .003},
            "side_costs": {"1.0": {"buy": .001, "sell": .002}}}
    rows = []
    for i in range(100):
        rows.append({"rcept_no": f"202612{i:08d}", "corp_code": f"{i % 40:08d}", "stock_code": "900000",
            "entry_date": grid[i * 3].date().isoformat(), "exit_date": grid[i * 3 + 20].date().isoformat(),
            "horizon": 21, "cancel": bool(i % 2), "stock": position("900000", .025 + .002 * (i % 3)),
            "controls": [position(f"{j * 10:06d}", .001 * j) for j in range(1, 11)]})
    return rows, grid


def test_E_V_F_independent_of_observed_receipts(env):
    _, _, rules, _, _, _ = env
    frozen = rules.freeze()
    assert frozen["last_entry"] == "2028-10-02"
    assert frozen["E"] == (pd.Timestamp("2028-10-02") + pd.offsets.BDay(20)).date().isoformat()
    assert frozen["V"] == (pd.Timestamp("2028-10-02") + pd.offsets.BDay(83)).date().isoformat()
    assert storage.aware(frozen["F"]).date() == pd.Timestamp(frozen["V"]).date() + timedelta(days=7)
    assert storage.aware(frozen["F"]).strftime("%H:%M:%S") == "23:59:59"
    assert frozen["grid"][0] == pd.Timestamp("2026-12-01")
    assert frozen["grid"][-1] == pd.Timestamp(frozen["E"])


def test_unknown_needed_calendar_stops_before_input_reads(env, monkeypatch):
    source, store, rules, client, _, _ = env
    data = {**rules.data, "calendar_through": "2026-12-01",
            "sessions": rules.sessions[rules.sessions <= "2026-12-01"].strftime("%Y-%m-%d").tolist()}
    monkeypatch.setattr(inputs, "read_bytes", lambda *a: pytest.fail("no reads before complete calendar"))
    result = recorder.prepare_day(store, source, policy.Policy(data), client)
    assert result["status"] == "data_not_ready" and "calendar_unverified" in result["reason"]
    assert store.get("readiness", store.keys("readiness")[0])["events_dropped"] is False
    assert store.keys("decisions") == []


def test_calendar_cannot_guess_fixed_freeze(env):
    _, _, rules, _, _, _ = env
    data = {**rules.data, "calendar_through": "2028-12-31",
            "sessions": rules.sessions[rules.sessions <= "2028-12-31"].strftime("%Y-%m-%d").tolist()}
    with pytest.raises(NotReady, match="E_plus_63"):
        policy.Policy(data).freeze()


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("multiplier", [1., 1.5, 2.])
def test_cost_formula_reuses_BB001_known_dates(version, multiplier):
    rules = policy.shared_policy("2025-01-01", "2026-12-31")
    for side in ("buy", "sell"):
        actual = rules.cost(10000., "KOSPI", "2025-07-10", 2e9, side, multiplier=multiplier, version=version)
        expected = cost_model.one_way_cost(10000., "KOSPI", pd.Timestamp("2025-07-10").date(), 2e9,
                                           side, multiplier=multiplier, model_version=version)
        assert actual == pytest.approx(expected)


def test_unknown_tax_does_not_roll_forward_last_rate(env):
    _, _, rules, _, _, _ = env
    bounded = policy.Policy({**rules.data, "tax_through": "2026-12-31"})
    with pytest.raises(NotReady, match="tax_unverified"):
        bounded.cost(10000., "KOSPI", "2027-01-04", 2e9, "sell")


def test_v2_tax_follows_T_plus_2_and_is_not_multiplied(env):
    _, _, rules, _, _, _ = env
    amended = policy.Policy({**rules.data, "tax": rules.data["tax"] + [{"from": "2027-01-01", "KOSPI": .004, "KOSDAQ": .004}]})
    v1 = amended.cost(10000., "KOSPI", "2026-12-30", 2e9, "sell", version="v1")
    v2 = amended.cost(10000., "KOSPI", "2026-12-30", 2e9, "sell", version="v2")
    double = amended.cost(10000., "KOSPI", "2026-12-30", 2e9, "sell", version="v2", multiplier=2)
    assert v2 - v1 == pytest.approx(.002)
    assert double == pytest.approx(.004 + 2 * (v2 - .004))


@pytest.mark.parametrize("second,valid", [(57, True), (58, True), (59, False)])
def test_activation_inclusive_cutoff_with_one_second_bound(tmp_path, fake_publication, second, valid):
    clock, _ = fake_publication
    clock[0] = datetime(2026, 11, 30, 23, 59, second, tzinfo=KST)
    store = storage.Store(tmp_path, clock=lambda: clock[0])
    if valid:
        recorder.activate(store, commit="a" * 40, manifest_hash="b" * 64)
        assert store.path("activation.json").is_file()
        assert recorder.activation_status(store) == "active"
    else:
        with pytest.raises(ValueError, match="not_started"):
            recorder.activate(store, commit="a" * 40, manifest_hash="b" * 64)
        clock[0] += timedelta(seconds=2)
        assert recorder.activation_status(store) == "not_started"


def test_first_success_cannot_define_start(tmp_path, fake_publication):
    clock, _ = fake_publication
    clock[0] = datetime(2026, 12, 1, 0, 0, tzinfo=KST)
    store = storage.Store(tmp_path, clock=lambda: clock[0])
    assert recorder.activation_status(store) == "not_started"
    with pytest.raises(ValueError, match="not_started"):
        recorder.activate(store, commit="a" * 40, manifest_hash="b" * 64)


def test_evaluator_refuses_through_F_without_claim(env):
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"])
    with pytest.raises(ValueError, match="before/at F"):
        e.evaluate(store, rules)
    assert store.get("evaluation_started", "final") is None


def test_not_started_final_and_only_one_attempt(env, tmp_path):
    _, _, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    store = storage.Store(tmp_path / "never-activated", clock=lambda: clock[0])
    assert e.evaluate(store, rules)["verdict"] == "not_started"
    assert store.get("trials", "d21")["verdict"] == "not_started"
    with pytest.raises(ValueError, match="already attempted"):
        e.evaluate(store, rules)


def test_required_evaluation_data_missing_keeps_entered_event(env):
    source, store, rules, _, clock, item = env
    run(env)
    clock[0] = datetime(2026, 12, 2, 18, 30, tzinfo=KST)
    recorder.post_entry(store, source, rules)
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    result = e.evaluate(store, rules)
    assert result["verdict"] == "insufficient"
    assert result["executed_events"] == 1 and result["per_event"] is None
    assert result["missing_entered_events_are_never_deleted"]
    assert store.get("post_entry", item["rcept_no"])["status"] == "executed"


def test_daily_vintage_available_after_F_is_refused(env):
    source, store, rules, _, clock, _ = env
    frozen = rules.freeze()
    clock[0] = storage.aware(frozen["F"]) + timedelta(days=1)
    writer = inputs.Prices(source, rules, store, as_of="2026-12-02")
    writer.snapshot("2026-12-02")
    reader = inputs.Prices(source, rules, store, as_of=frozen["V"], frozen=True, freeze_at=frozen["F"])
    with pytest.raises(NotReady, match="not_available_by_F"):
        reader.read("2026-12-02")


def test_statistics_and_random_paths_reuse_frozen_BB001():
    rows, grid = metric_rows()
    result = e.metrics(rows, grid)
    assert result["primary"] == bb001.hac([bb001.event_excess(r) for r in rows], [r["entry_date"] for r in rows], grid, 20)
    assert result["primary"]["grid_sessions"] == len(grid)
    assert result["random_control"]["count"] == 200
    assert result["random_control"] == bb001.random_controls(list(reversed(rows)))
    assert e.judge(result, copy.deepcopy(result))["verdict"] == "pass"


def test_random_draws_are_path_major_default_rng_zero():
    rows, _ = metric_rows()
    result = bb001.random_controls(rows[:3])
    rng = np.random.default_rng(0)
    net = np.arange(1, 11) * .001 - .003
    expected = [[net[rng.integers(10)] - net.mean() for _ in range(3)] for _ in range(200)]
    assert np.array(result["paths"]) == pytest.approx(np.asarray(expected))


@pytest.mark.parametrize("field", ["events", "firms", "dates", "mean", "t", "cost", "random", "nan_t"])
def test_insufficient_precedes_delisting_sensitive(field):
    rows, grid = metric_rows()
    base = e.metrics(rows, grid)
    loss = copy.deepcopy(base)
    if field == "events":
        loss["primary"]["count"] = 99
    elif field == "firms":
        loss["corporations"] = 39
    elif field == "dates":
        loss["dates"] = 49
    elif field in {"mean", "t", "nan_t"}:
        loss["primary"]["mean" if field == "mean" else "t_stat"] = float("nan") if field == "nan_t" else None
    elif field == "cost":
        loss["cost_sensitivity"]["1.5"]["mean"] = None
    else:
        loss["random_control"]["share_of_controls"] = None
    assert e.judge(base, loss)["verdict"] == "insufficient"


def test_verdict_precedence_and_exact_thresholds():
    rows, grid = metric_rows()
    base, loss = e.metrics(rows, grid), e.metrics(rows, grid)
    base["primary"]["mean"] = MINIMUM_EFFECT
    assert e.judge(base, loss)["verdict"] == "pass"
    loss["primary"]["t_stat"] = 2.
    assert e.judge(base, loss)["verdict"] == "delisting_sensitive"
    base["cost_sensitivity"]["1.5"]["mean"] = 0.
    assert e.judge(base, loss)["verdict"] == "fail"
    assert e.judge(base, loss, required_data_complete=False)["verdict"] == "insufficient"


def test_twenty_slot_portfolio_overflow_and_empty_cash():
    rows, grid = metric_rows()
    simultaneous = [{**rows[0], "rcept_no": str(i)} for i in range(21)]
    result = bb001.calendar_portfolio(simultaneous, grid)
    assert len(result["accepted"]) == 20 and len(result["overflow_rcept_nos"]) == 1
    assert result["daily"][0]["active_slots"] == 20
    empty = bb001.calendar_portfolio([], grid)
    assert all(row["active_slots"] == 0 and row["event_nav"] == pytest.approx(1.) for row in empty["daily"])


def test_interim_never_reads_prices_or_returns(env, monkeypatch):
    _, store, _, _, _, _ = env
    run(env)
    monkeypatch.setattr(inputs.Prices, "read", lambda *a: pytest.fail("interim must not read returns"))
    result = e.interim(store)
    assert result["returns_included"] is False
    assert result["decisions"] == {"included": 1}
    assert set(result) & {"metrics", "per_event", "mean_excess", "price_hashes"} == set()


@pytest.mark.parametrize("retraded", [False, True])
def test_suspensions_keep_positions_and_only_no_retrading_through_V_vanishes(env, retraded):
    _, _, rules, _, _, _ = env
    selected = rules.schedule("2026-12-01")
    first = selected["entry_position"]
    class Reader:
        sessions, policy = rules.sessions, rules
        def require(self, day):
            age = self.sessions.get_loc(day) - first
            return {"000010": {"calendar_status": "verified", "open": 10000., "close": 10000.,
                "volume": 10000. if age < 10 else 0., "ret_1d": .01}}
        def last_trades(self):
            return {"000010": self.sessions[first + (50 if retraded else 9)]}
    result = e.value_position(Reader(), selected, "000010", {"market": "KOSPI", "adv": 2e9, "pre_close": 10000.})
    assert result["gross"] == pytest.approx(1.01**9 - 1)
    assert result["gross_path"][-1] == result["gross_path"][9]
    assert result["vanished"] is (not retraded)
    if not retraded:
        assert bb001._net(result, worthless=True) == -1. - result["costs"]["1.0"]


def test_missing_adjusted_return_on_entered_position_is_not_a_raw_close_fallback(env):
    _, _, rules, _, _, _ = env
    selected = rules.schedule("2026-12-01")
    class Reader:
        sessions, policy = rules.sessions, rules
        def require(self, day):
            return {"000010": {"calendar_status": "verified", "open": 10000., "close": 10000.,
                               "volume": 10000., "ret_1d": float("nan")}}
    with pytest.raises(NotReady, match="missing_adjusted_return"):
        e.value_position(Reader(), selected, "000010", {"market": "KOSPI", "adv": 2e9, "pre_close": 10000.})


def test_full_frozen_evaluation_keeps_one_event_and_cash_grid(env, tmp_path):
    import json
    from pathlib import Path
    source, _, rules, client, clock, item = env
    clock[0] = storage.aware("2026-11-30T12:00:00+09:00")
    store = storage.Store(tmp_path / "complete-final", clock=lambda: clock[0])
    relative = "backtesting/experiments/bb003/evaluate.py"
    root = Path(__file__).resolve().parents[1]
    manifest = storage.encoded({"files": {relative: storage.sha((root / relative).read_bytes())}})
    store.test_manifest_sha256 = storage.sha(manifest)
    recorder.activate(store, commit="a" * 40, manifest_hash=storage.sha(manifest), manifest_raw=manifest)
    clock[0] = storage.aware("2026-12-02T08:20:00+09:00")
    store.morning_guard = lambda: recorder.entry_plan(rules, rules.sessions.get_loc(clock[0].date().isoformat()))
    run((source, store, rules, client, clock, item))
    clock[0] = storage.aware("2026-12-02T18:30:00+09:00")
    recorder.post_entry(store, source, rules)
    template = [json.loads(line) for line in (source / "market/krx_daily/2026/20261202.jsonl").read_bytes().splitlines()]
    frozen = rules.freeze()
    for day in rules.sessions[(rules.sessions >= "2026-12-01") & (rules.sessions <= frozen["V"])]:
        path = source / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl"
        if not path.exists():
            storage.put(path, b"".join(storage.encoded({**r, "trade_date": day.date().isoformat()}) for r in template))
    clock[0] = storage.aware(frozen["F"]) - timedelta(days=2)
    recorder.capture_prices(store, source, rules)
    clock[0] = storage.aware(frozen["F"]) + timedelta(seconds=1)
    result = e.evaluate(store, rules)
    assert result["verdict"] == "insufficient"  # One event, never promoted to pass.
    assert result["executed_events"] == len(result["per_event"]) == 1
    assert result["metrics"]["random_control"]["count"] == 200
    assert set(result["metrics"]["cost_sensitivity"]) == {"1.0", "1.5", "2.0", "v1"}
    assert result["per_event"][0]["stock"]["gross"] == 0.
    assert result["calendar_portfolio"]["max_slots"] == 20
    assert len(result["calendar_portfolio"]["daily"]) == len(frozen["grid"])
    assert max(result["price_hashes"]) == pd.Timestamp(frozen["V"]).strftime("%Y%m%d")
    with pytest.raises(ValueError, match="already attempted"):
        e.evaluate(store, rules)


def test_registry_export_retries_only_publication(env, tmp_path, monkeypatch):
    import csv
    import json
    from backtesting.experiments.bb003.publish import publish_trial
    from backtesting import experiment_registry
    _, _, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    store = storage.Store(tmp_path / "closed-id", clock=lambda: clock[0])
    e.evaluate(store, rules)  # not_started is still a terminal trial.
    root = tmp_path / "pc"
    calls = []
    def append(experiment, trials, **kwargs):
        calls.append(trials)
        path = root / experiment_registry.REGISTRY_PATH
        path.parent.mkdir(parents=True)
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("experiment_id", "variant", "metrics_json"))
            writer.writeheader()
            writer.writerow({"experiment_id": experiment, "variant": trials[0][0], "metrics_json": json.dumps(trials[0][1])})
    monkeypatch.setattr(experiment_registry, "record_trials", append)
    assert publish_trial(store, root)["status"] == "exported"
    assert publish_trial(store, root)["status"] == "already_exported"
    assert len(calls) == 1
    with pytest.raises(ValueError, match="shared main"):
        publish_trial(store, storage.MAIN)


@pytest.mark.parametrize("damage", ["missing_blob", "changed_blob", "changed_record", "unapproved_manifest"])
def test_no_returns_integrity_prevalidation_does_not_consume_evaluation(env, monkeypatch, damage):
    _, store, rules, _, clock, item = env
    run(env)
    record = store.get("decisions", item["rcept_no"])
    checksum = next(iter(record["input_hashes"].values()))
    if damage == "missing_blob":
        store.path("inputs/" + checksum).unlink()
    elif damage == "changed_blob":
        store.path("inputs/" + checksum).write_bytes(b"corrupted input")
    elif damage == "changed_record":
        store.path(store.proof("decisions", item["rcept_no"])["record_path"]).write_bytes(b"{}")
    else:
        store.test_manifest_sha256 = "c" * 64
        store.seal("sample", "unapproved", {"value": 1})
        store.test_manifest_sha256 = "b" * 64
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    monkeypatch.setattr(e, "Prices", lambda *a, **k: pytest.fail("prevalidation must not consume returns"))
    with pytest.raises((ValueError, FileNotFoundError)):
        e.evaluate(store, rules)
    assert store.proof("evaluation_started", "final") is None
    assert not store.path("evaluation_abort.json").exists()


@pytest.mark.parametrize("error", [OSError("storage failure"), ValueError("invalid computed input"), KeyboardInterrupt()])
def test_any_error_after_consumption_has_explicit_terminal_abort(env, monkeypatch, error):
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    calls = []
    def fail(*args):
        calls.append(1)
        assert store.proof("evaluation_started", "final")
        raise error
    monkeypatch.setattr(e, "compute", fail)
    with pytest.raises(type(error)):
        e.evaluate(store, rules)
    abort = storage.load(store.path("evaluation_abort.json"))
    assert abort["status"] == "forward_aborted" and abort["terminal"] and abort["uses_holdout"] is False
    assert abort["error_type"] == type(error).__name__
    assert abort["manifest_sha256"] == store.get("evaluation_started", "final")["manifest_sha256"]
    with pytest.raises(ValueError, match="already attempted"):
        e.evaluate(store, rules)
    assert calls == [1]


@pytest.mark.parametrize("failure", ["before_save", "after_candidate", "trial_save"])
def test_recovery_only_resaves_sealed_computation_without_reading_returns(env, monkeypatch, failure):
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    original_seal, original_link = store.seal, storage.os.link
    def fail_seal(kind, key, record, **kwargs):
        if (kind == "evaluation" and failure == "before_save") or (kind == "trials" and failure == "trial_save"):
            raise OSError("simulated save interruption")
        return original_seal(kind, key, record, **kwargs)
    def fail_link(source, target):
        if failure == "after_candidate" and Path(target) == store.marker("evaluation", "final"):
            raise OSError("simulated completion interruption")
        return original_link(source, target)
    from pathlib import Path
    monkeypatch.setattr(store, "seal", fail_seal)
    monkeypatch.setattr(storage.os, "link", fail_link)
    with pytest.raises(OSError):
        e.evaluate(store, rules)
    computed = store.get("evaluation_computed", "final")
    assert computed and store.path("evaluation_abort.json").is_file()
    candidate = store.path("evaluation.json").read_bytes() if store.path("evaluation.json").exists() else None
    monkeypatch.setattr(store, "seal", original_seal)
    monkeypatch.setattr(storage.os, "link", original_link)
    monkeypatch.setattr(e, "compute", lambda *a: pytest.fail("recovery cannot recompute"))
    monkeypatch.setattr(e, "Prices", lambda *a, **k: pytest.fail("recovery cannot read prices"))
    clock[0] += timedelta(days=2)
    recovered = e.recover_evaluation(store)
    assert recovered == computed["result"]
    assert e.recover_evaluation(store) == recovered
    assert store.get("trials", "d21")["evaluation_sha256"] == store.proof("evaluation", "final")["record_sha256"]
    if candidate:
        assert store.path("evaluation.json").read_bytes() == candidate
    with pytest.raises(ValueError, match="already attempted"):
        e.evaluate(store, rules)


def test_interrupted_consumed_evaluation_without_checkpoint_is_terminal(env):
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    store.seal("evaluation_started", "final", {"freeze": {"F": rules.freeze()["F"]}})
    with pytest.raises(ValueError, match="no computed and sealed"):
        e.recover_evaluation(store)
    assert storage.load(store.path("evaluation_abort.json"))["terminal"]
    assert store.proof("evaluation_computed", "final") is None


@pytest.mark.parametrize("origin", ["exception", "interrupted"])
@pytest.mark.parametrize("append_interrupted", [False, True])
def test_terminal_abort_registry_exactly_once_without_checkpoint_or_computation(env, tmp_path, monkeypatch, origin, append_interrupted):
    import csv
    from types import SimpleNamespace
    from backtesting import experiment_registry
    from backtesting.experiments.bb003.publish import publish_trial
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    def fail(*args):
        raise OSError("evaluation interrupted before checkpoint")
    monkeypatch.setattr(e, "compute", fail)
    if origin == "exception":
        with pytest.raises(OSError):
            e.evaluate(store, rules)
    else:
        store.seal("evaluation_started", "final", {"freeze": {"F": rules.freeze()["F"]}})
    monkeypatch.setattr(e, "compute", lambda *a: pytest.fail("publication cannot recompute"))
    monkeypatch.setattr(e, "Prices", lambda *a, **k: pytest.fail("publication cannot read returns"))
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a: SimpleNamespace(commit_hash="a" * 40))
    monkeypatch.setattr(experiment_registry, "_commit_aliases", lambda *a: set())
    root = tmp_path / "pc"
    append = experiment_registry.record_trials
    def interrupted(*args, **kwargs):
        append(*args, **kwargs)
        raise OSError("interrupted after durable registry publication")
    if append_interrupted:
        monkeypatch.setattr(experiment_registry, "record_trials", interrupted)
        with pytest.raises(OSError, match="durable registry"):
            publish_trial(store, root)
        monkeypatch.setattr(experiment_registry, "record_trials", append)
    else:
        assert publish_trial(store, root)["status"] == "exported"
    abort = store.path("evaluation_abort.json").read_bytes()
    clock[0] += timedelta(days=2)
    assert publish_trial(store, root)["status"] == "already_exported"
    with (root / experiment_registry.REGISTRY_PATH).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["verdict"] == "forward_aborted"
    import json
    metrics = json.loads(rows[0]["metrics_json"])
    assert metrics["evaluation_abort_sha256"] == storage.sha(abort)
    assert metrics["evaluation_started_sha256"] == store.proof("evaluation_started", "final")["record_sha256"]
    assert not metrics["recomputation_allowed"] and metrics["evaluation_status"] == "forward_aborted"
    assert metrics["uses_holdout"] is False
    assert store.path("evaluation_abort.json").read_bytes() == abort
    assert store.proof("evaluation_computed", "final") is None and store.proof("evaluation", "final") is None
    with pytest.raises(ValueError, match="already attempted"):
        e.evaluate(store, rules)


def test_abort_publication_refuses_a_recoverable_computation_checkpoint(env, tmp_path, monkeypatch):
    from backtesting.experiments.bb003.publish import publish_trial
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    def fail(*args):
        raise OSError("final save failed")
    monkeypatch.setattr(e, "save_computed", fail)
    with pytest.raises(OSError):
        e.evaluate(store, rules)
    with pytest.raises(ValueError, match="recover a checkpoint first"):
        publish_trial(store, tmp_path / "pc")
    assert not (tmp_path / "pc").exists()


def test_prevalidation_code_failure_leaves_evaluation_unconsumed(env, monkeypatch):
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    def changed(*a, **k):
        raise ValueError("changed sealed code")
    monkeypatch.setattr(e, "implementation_check", changed)
    with pytest.raises(ValueError, match="changed sealed code"):
        e.evaluate(store, rules)
    assert store.proof("evaluation_started", "final") is None


def test_registry_reader_recovers_with_large_existing_metrics_field(env, tmp_path, monkeypatch):
    import csv
    import json
    from backtesting import experiment_registry
    from backtesting.experiments.bb003.publish import publish_trial
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    e.evaluate(store, rules)
    path = tmp_path / "pc" / experiment_registry.REGISTRY_PATH
    path.parent.mkdir(parents=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("experiment_id", "variant", "metrics_json"))
        writer.writeheader()
        writer.writerow({"experiment_id": "older", "variant": "large", "metrics_json": "x" * 200000})
        writer.writerow({"experiment_id": "BB003_buyback_forward", "variant": "forward_d21",
            "metrics_json": json.dumps({"evaluation_sha256": store.proof("evaluation", "final")["record_sha256"]})})
    previous = csv.field_size_limit(131072)
    monkeypatch.setattr(experiment_registry, "record_trials", lambda *a, **k: pytest.fail("already registered"))
    try:
        assert publish_trial(store, tmp_path / "pc")["status"] == "already_exported"
    finally:
        csv.field_size_limit(previous)


def test_daily_recording_after_receipt_end_does_not_require_future_V_F(env, monkeypatch):
    source, store, rules, _, clock, _ = env
    bounded = policy.Policy({**rules.data, "calendar_through": "2028-10-10",
        "sessions": rules.sessions[rules.sessions <= "2028-10-10"].strftime("%Y-%m-%d").tolist()})
    clock[0] = storage.aware("2028-10-03T18:30:00+09:00")
    with pytest.raises(NotReady, match="E_plus_63"):
        bounded.freeze()
    captured = []
    monkeypatch.setattr(inputs.Prices, "snapshot", lambda self, day: captured.append(day))
    result = recorder.capture_prices(store, source, bounded, start="2028-10-02")
    assert result == {"captured": 2}
    assert captured == list(pd.to_datetime(["2028-10-02", "2028-10-03"]))
    assert recorder.post_entry(store, source, bounded) == {}


def test_capture_at_F_only_requires_calendar_through_V(env, monkeypatch):
    source, store, rules, _, clock, _ = env
    frozen = rules.freeze()
    bounded = policy.Policy({**rules.data, "calendar_through": frozen["V"],
        "sessions": rules.sessions[rules.sessions <= frozen["V"]].strftime("%Y-%m-%d").tolist()})
    clock[0] = storage.aware(frozen["F"])
    captured = []
    monkeypatch.setattr(inputs.Prices, "snapshot", lambda self, day: captured.append(day))
    result = recorder.capture_prices(store, source, bounded, start=frozen["V"])
    assert result == {"captured": 1} and captured == [pd.Timestamp(frozen["V"])]


def test_refused_second_evaluation_does_not_abort_an_already_successful_result(env, tmp_path):
    _, _, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    store = storage.Store(tmp_path / "not-started", clock=lambda: clock[0])
    e.evaluate(store, rules)
    with pytest.raises(ValueError, match="already attempted"):
        e.evaluate(store, rules)
    assert not store.path("evaluation_abort.json").exists()


@pytest.mark.parametrize("failure", ["verification", "preregistration"])
def test_manual_evaluation_preflight_error_audits_an_interrupted_consumed_attempt(env, monkeypatch, failure):
    from scripts.bb003.__main__ import main
    from backtesting import experiment_registry
    from backtesting.experiments.bb003 import deployment
    _, store, rules, _, clock, _ = env
    clock[0] = storage.aware(rules.freeze()["F"]) + timedelta(seconds=1)
    store.seal("evaluation_started", "final", {"freeze": {"F": rules.freeze()["F"]}})
    def reject(*a, **k):
        raise ValueError("interrupted attempt preflight error")
    if failure == "verification":
        monkeypatch.setattr(deployment, "verify_operation", reject)
    else:
        monkeypatch.setattr(experiment_registry, "verify_preregistration", reject)
    monkeypatch.setattr(storage, "Store", lambda *a, **k: store)
    monkeypatch.setattr(e, "Prices", lambda *a, **k: pytest.fail("preflight cannot open returns"))
    with pytest.raises(ValueError, match="preflight error"):
        main(["evaluate", "--execute", "--output-root", str(store.root.parent.parent)])
    assert storage.load(store.path("evaluation_abort.json"))["terminal"]
    assert not store.proof("evaluation_computed", "final")
