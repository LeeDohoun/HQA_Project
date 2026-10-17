"""Hand-calculated CH002 execution, cost, controls and offline access fixtures."""
import builtins
import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import ch001, ch002, ch003, common, hf001, ordering
from src.ingestion.storage import write_rows
from ch_test_fixtures import hermetic_ch_roots
from dart_listing_fixtures import checkpoint
from test_experiments_ch001 import offline_repo, prepared_fixture, market, selection, judgement_fixture


@pytest.fixture
def runner_repo(offline_repo):
    for name in (ch002.EXPERIMENT_ID, ch003.EXPERIMENT_ID):
        relative = f"research/experiments/{name}/preregistration.md"
        subprocess.run(["git", "-C", str(offline_repo), "checkout", "HEAD", "--", relative],
                       check=True, capture_output=True)
        experiment_registry.verify_preregistration(name, offline_repo)
    return offline_repo


@pytest.fixture(params=[ch001, ch002, ch003], ids=["CH001", "CH002", "CH003"])
def design_case(request, runner_repo, monkeypatch):
    module = request.param
    data, prices, days = prepared_fixture(runner_repo)
    if module is ch003:
        from test_experiments_ch003 import save_indices
        save_indices(data, days)
        monkeypatch.setattr(ch003, "MONTHS", ("2024-05", "2024-06"))
    trap_price_opens(monkeypatch)
    return module, dict(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)


def test_exception_after_real_evaluation_records_each_aborted_variant(design_case, monkeypatch):
    module, kwargs = design_case
    registration = experiment_registry.verify_preregistration(module.EXPERIMENT_ID, kwargs["repo_root"])
    original = module.evaluate_rule
    evaluated = []
    def evaluate(*args, **kw):
        evaluated.append(original(*args, **kw))
        raise RuntimeError("injected after evaluation")
    monkeypatch.setattr(module, "evaluate_rule", evaluate)
    monkeypatch.setattr(module, "publish", lambda *a, **k: pytest.fail("published failed evaluation"))
    with pytest.raises(RuntimeError, match="injected after evaluation"):
        module.run_experiment(**kwargs)
    assert len(evaluated) == 1
    rows = ch001.av001.hc003.registry_rows(kwargs["repo_root"])
    assert [r["variant"] for r in rows] == list(module.VARIANTS)
    assert all(r["verdict"] == "design_aborted" for r in rows)
    assert all(r["prereg_commit"] == registration.commit_hash for r in rows)
    metrics = [json.loads(r["metrics_json"]) for r in rows]
    assert len({m["run_id"] for m in metrics}) == 1
    assert all(m["status"] == "aborted" and m["input_hashes"]["code"]
               and m["failure_type"] == "RuntimeError"
               and m["failure_location"]["function"] == "evaluate" for m in metrics)
    assert not list((kwargs["repo_root"] / "research/experiments" / module.EXPERIMENT_ID / "results").glob("*"))


def test_exception_before_preflight_hashes_records_nothing(design_case, monkeypatch):
    module, kwargs = design_case
    def hashes(*args, **kw):
        raise RuntimeError("injected before hashes")
    monkeypatch.setattr(module, "source_hashes", hashes)
    monkeypatch.setattr(ch001, "prepare_inputs", lambda **k: pytest.fail("prepared before hashes"))
    with pytest.raises(RuntimeError, match="injected before hashes"):
        module.run_experiment(**kwargs)
    assert not (kwargs["repo_root"] / experiment_registry.REGISTRY_PATH).exists()


def test_midrun_preregistration_change_records_against_captured_registration(design_case, monkeypatch):
    module, kwargs = design_case
    root = kwargs["repo_root"]
    registration = experiment_registry.verify_preregistration(module.EXPERIMENT_ID, root)
    path = root / registration.path
    original = module.evaluate_rule
    def evaluate(*args, **kw):
        result = original(*args, **kw)
        path.write_text(path.read_text() + "\nchanged during evaluation\n")
        return result
    monkeypatch.setattr(module, "evaluate_rule", evaluate)
    monkeypatch.setattr(module, "publish", lambda *a, **k: pytest.fail("published changed plan"))
    with pytest.raises(ValueError, match="inputs or code changed"):
        module.run_experiment(**kwargs)
    with pytest.raises(ValueError, match="preregistration git check failed"):
        experiment_registry.verify_preregistration(module.EXPERIMENT_ID, root)
    rows = ch001.av001.hc003.registry_rows(root)
    assert [r["variant"] for r in rows] == list(module.VARIANTS)
    assert all(r["verdict"] == "design_aborted" and r["prereg_commit"] == registration.commit_hash for r in rows)
    assert len({json.loads(r["metrics_json"])["run_id"] for r in rows}) == 1
    assert not list((root / "research/experiments" / module.EXPERIMENT_ID / "results").glob("*"))


def test_count_judgement_and_publication_share_recording_root_lock(design_case, monkeypatch):
    module, kwargs = design_case
    root = kwargs["repo_root"]
    count, record, lock, publish = (experiment_registry.trial_count, experiment_registry.record_trial,
                                  ordering.execution_lock, module.publish)
    prior = 20 - len(module.VARIANTS)
    for _ in range(prior):
        record(module.EXPERIMENT_ID, "prior", {}, "fail", repo_root=root)
    held, reads, writes = [], [], []
    @contextmanager
    def tracked_lock(repo_root):
        assert repo_root == root
        with lock(repo_root):
            held.append(True)
            # Simulate a preceding run finishing before we obtain the lock.
            record(module.EXPERIMENT_ID, "preceding", {}, "fail", repo_root=root)
            try:
                yield
            finally:
                held.pop()
    def trial_count(*args, **kw):
        assert held and kw["repo_root"] == root
        reads.append(True)
        return count(*args, **kw)
    def record_trial(*args, **kw):
        assert held and kw["repo_root"] == root
        with pytest.raises(ValueError, match="in progress"):
            with lock(root):
                pytest.fail("execution directory is not locked")
        writes.append(True)
        return record(*args, **kw)
    def publication(payload, **kw):
        assert held and kw["repo_root"] == root
        for rule in ch001.RULES:
            for result in payload["rules"][rule].values():
                assert result["judgement_inputs"]["trial_count_after_run"] == 21
                assert result["judgement_inputs"]["t_threshold"] == 3.
        return publish(payload, **kw)
    monkeypatch.setattr(ordering, "execution_lock", tracked_lock)
    monkeypatch.setattr(experiment_registry, "trial_count", trial_count)
    monkeypatch.setattr(experiment_registry, "record_trial", record_trial)
    monkeypatch.setattr(module, "publish", publication)
    module.run_experiment(**kwargs)
    assert reads == [True] and len(writes) == len(module.VARIANTS) and not held
    assert count(module.EXPERIMENT_ID, repo_root=root) == 21


@pytest.mark.parametrize("when", ["preparation", "evaluation"])
def test_replaced_input_aborts_without_publishing_verdict(design_case, monkeypatch, when):
    module, kwargs = design_case
    root, data = kwargs["repo_root"], kwargs["data_dir"]
    path = next((data / "fundamentals/dart_quarterly").glob("*.jsonl"))
    initial_hash = hf001._digest(path)
    def replace():
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes() + b"\n")
        replacement.replace(path)
    if when == "preparation":
        original = ch001.prepare_inputs
        def prepare(**kw):
            result = original(**kw)
            replace()
            return result
        monkeypatch.setattr(ch001, "prepare_inputs", prepare)
    else:
        original = module.evaluate_rule
        def evaluate(*args, **kw):
            result = original(*args, **kw)
            replace()
            return result
        monkeypatch.setattr(module, "evaluate_rule", evaluate)
    original_hashes, snapshots = module.source_hashes, []
    def hashes(*args, **kw):
        result = original_hashes(*args, **kw)
        snapshots.append(result)
        return result
    monkeypatch.setattr(module, "source_hashes", hashes)
    monkeypatch.setattr(module, "publish", lambda *a, **k: pytest.fail("published changed inputs"))
    with pytest.raises(ValueError, match="inputs or code changed"):
        module.run_experiment(**kwargs)
    key = path.relative_to(data).as_posix()
    assert snapshots[0]["financials"][key] == initial_hash
    assert snapshots[-1]["financials"][key] != initial_hash
    rows = ch001.av001.hc003.registry_rows(root)
    assert len(rows) == len(module.VARIANTS) and all(r["verdict"] == "design_aborted" for r in rows)
    expected = hashlib.sha256(json.dumps(snapshots[0]["financials"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    assert all(json.loads(r["metrics_json"])["input_hashes"]["financials"] == expected for r in rows)
    assert not list((root / "research/experiments" / module.EXPERIMENT_ID / "results").glob("*"))


def test_runner_code_hash_is_fixed_before_preparation_and_rechecked(design_case, monkeypatch):
    module, kwargs = design_case
    prepare, digest = ch001.prepare_inputs, hf001._digest
    changed, snapshots = [], []
    def code_digest(path):
        if changed and Path(path) == Path(module.__file__):
            return "0" * 64
        return digest(path)
    hashes = module.source_hashes
    def snapshot(*args, **kw):
        result = hashes(*args, **kw)
        snapshots.append(result)
        return result
    def preparation(**kw):
        assert len(snapshots) == 1
        result = prepare(**kw)
        changed.append(True)
        return result
    monkeypatch.setattr(hf001, "_digest", code_digest)
    monkeypatch.setattr(module, "source_hashes", snapshot)
    monkeypatch.setattr(ch001, "prepare_inputs", preparation)
    monkeypatch.setattr(module, "publish", lambda *a, **k: pytest.fail("published changed code"))
    with pytest.raises(ValueError, match="inputs or code changed"):
        module.run_experiment(**kwargs)
    key = Path(module.__file__).relative_to(ch001.PROJECT_ROOT).as_posix()
    assert snapshots[0]["code"][key] == digest(Path(module.__file__))
    assert snapshots[-1]["code"][key] == "0" * 64
    rows = ch001.av001.hc003.registry_rows(kwargs["repo_root"])
    assert len(rows) == len(module.VARIANTS) and all(r["verdict"] == "design_aborted" for r in rows)


def holding_fixture(codes=("000010",)):
    prices, days = market(pd.bdate_range("2024-01-02", periods=75), codes=codes)
    prices[["open", "close", "base_price"]] = 10000.
    prices.high, prices.low = 11000., 9000.
    selected = selection(days[30], list(codes))
    return prices, days, selected


def observed_case(prices, days, selected):
    observations = ch001.forward_observations(prices, [selected], days)
    code = sorted(selected["B"])[0]
    frame = prices.xs(code, level="stock_code").assign(_present=True)
    observed = observations.set_index("stock_code").loc[code].to_dict()
    entry = days.get_loc(pd.Timestamp(selected["trade_date"]))
    values, reasons = ch002.holding_values(frame, entry)
    return frame, observed, entry, values, reasons


def test_reference_hash_checked_before_data_or_other_registration(runner_repo, monkeypatch):
    path = runner_repo / "research/experiments" / ch001.EXPERIMENT_ID / "preregistration.md"
    path.write_text(path.read_text() + "\nchanged")
    monkeypatch.setattr(ch001, "_registration", lambda *a: pytest.fail("hash did not stop first"))
    for runner in (ch002.run_experiment, ch002.dry_run):
        with pytest.raises(ValueError, match="reference SHA-256 mismatch"):
            runner(repo_root=runner_repo)


def test_registration_fixed_variants(runner_repo):
    assert ch002._registration(runner_repo).fields["variants_planned"] == 2


def test_actual_worktree_prereg_presence():
    path = ch001.PROJECT_ROOT / "research/experiments" / ch002.EXPERIMENT_ID / "preregistration.md"
    if path.exists():
        assert ch002._registration(ch001.PROJECT_ROOT).commit_hash.startswith("1a70359")
    else:
        with pytest.raises(ValueError, match="regular file"):
            ch002._registration(ch001.PROJECT_ROOT)


def test_trailing_peak_includes_W0_and_primary_costs_are_multiplicative_v1():
    prices, days, selected = holding_fixture()
    prices.loc[(days[31], "000010"), "close"] = 9000.
    frame, observed, entry, values, reasons = observed_case(prices, days, selected)
    assert values[0] == 1 and values[1] == .9
    request, invalid = ch002.first_request(frame, entry, values, reasons, "x_trail10")
    assert request == 2 and invalid == {}
    result = ch002.realize(frame, days, observed, values, first_attempt=request)
    assert result["G"] == .9 and result["fill_day"] == 2
    assert result["signal_date"] == result["request_date"] == str(days[31].date())
    assert result["first_attempt_date"] == result["fill_date"] == str(days[32].date())
    assert result["valuation_date"] == str(observed["exit_date"].date())
    # Price 10,000, ADV 2e9: commission .00015 + spread .001 + slip .0005.
    for i, m in enumerate(ch002.MULTIPLIERS):
        cb = .00165 * m
        cs = .0018 + .00165 * m
        assert result["net"][i] == pytest.approx(.9 * (1 - cs) / (1 + cb) - 1)
    assert result["cash_days"] == 19


def test_delayed_limit_down_and_halt_fill_request_never_cancelled():
    prices, days, selected = holding_fixture()
    prices.loc[(days[31], "000010"), "close"] = 9000.
    prices.loc[(days[32], "000010"), ["open", "ret_1d"]] = [7050., -.1]
    prices.loc[(days[33], "000010"), "volume"] = 0.
    prices.loc[(days[34], "000010"), "open"] = 8000.
    frame, observed, entry, values, reasons = observed_case(prices, days, selected)
    request, _ = ch002.first_request(frame, entry, values, reasons, "x_trail10")
    result = ch002.realize(frame, days, observed, values, first_attempt=request)
    assert request == 2 and result["fill_day"] == 4 and result["delay_sessions"] == 2
    assert result["G"] == pytest.approx(.9 * .9 * .8)
    assert result["cash_days"] == 17 and not result["raw_price_fallback"]


def test_halt_permanently_breaks_W_but_low10_resumes_and_sale_uses_raw_open():
    prices, days, selected = holding_fixture()
    prices.loc[(days[32], "000010"), ["close", "ret_1d", "volume"]] = [np.nan, np.nan, 0.]
    prices.loc[(days[46], "000010"), "ret_1d"] = -.2  # holding day 16
    prices.loc[(days[47], "000010"), "open"] = 8000.
    frame, observed, entry, values, reasons = observed_case(prices, days, selected)
    assert np.isnan(values[2:]).all()
    trail, trail_invalid = ch002.first_request(frame, entry, values, reasons, "x_trail10")
    low, low_invalid = ch002.first_request(frame, entry, values, reasons, "x_low10")
    assert trail is None and sum(trail_invalid.values()) == 18
    assert low == 17 and sum(low_invalid.values()) == 11
    result = ch002.realize(frame, days, observed, values, first_attempt=low)
    assert result["G"] == .8 and result["raw_price_fallback"]
    assert result["fill_date"] == str(days[47].date())


@pytest.mark.parametrize("field,value,reason", [("close", 0., "invalid_entry_close"),
    ("calendar_status", "unverified", "unverified_session"), ("ret_1d", -1., "invalid_return"),
    ("ret_1d", np.inf, "invalid_return")])
def test_W_invalid_conditions_and_never_repaired_by_future_data(field, value, reason):
    prices, days, selected = holding_fixture()
    pos = 31 if field != "ret_1d" else 33
    prices.loc[(days[pos], "000010"), field] = value
    frame, _, entry, values, reasons = observed_case(prices, days, selected)
    k = pos - entry + 1
    assert reasons[k] == reason and np.isnan(values[k:]).all()


def test_low10_strict_inequality_preentry_window_first_return_valid_and_zero_volume_allowed():
    prices, days, selected = holding_fixture()
    frame, _, entry, values, reasons = observed_case(prices, days, selected)
    assert ch002.first_request(frame, entry, values, reasons, "x_low10")[0] is None
    frame.loc[days[31], ["ret_1d", "volume"]] = [-.01, 0.]
    assert ch002.first_request(frame, entry, values, reasons, "x_low10")[0] == 2
    frame.loc[days[21], "ret_1d"] = np.nan  # first return is validated although A starts at 1
    assert ch002.first_request(frame, entry, values, reasons, "x_low10")[0] is None


def test_day19_signal_day20_open_fill_but_no_day20_close_signal():
    prices, days, selected = holding_fixture()
    prices.loc[(days[49], "000010"), "ret_1d"] = -.11
    frame, observed, entry, values, reasons = observed_case(prices, days, selected)
    request, _ = ch002.first_request(frame, entry, values, reasons, "x_trail10")
    assert request == 20
    result = ch002.realize(frame, days, observed, values, first_attempt=request)
    assert result["early_fill"] and result["fill_day"] == 20 and result["cash_days"] == 1
    frame.loc[days[49], "ret_1d"] = 0.
    frame.loc[days[50], "ret_1d"] = -.2
    values, reasons = ch002.holding_values(frame, entry)
    assert ch002.first_request(frame, entry, values, reasons, "x_trail10")[0] is None


@pytest.mark.parametrize("delisted", [False, True])
def test_unsold_request_uses_HF001_valuation_and_loss_without_extra_cost(delisted):
    prices, days, selected = holding_fixture()
    prices.loc[(days[31], "000010"), "close"] = 9000.
    stop = len(days) if delisted else 51
    prices.loc[(days[32:stop], "000010"), "volume"] = 0.
    frame, observed, _, values, _ = observed_case(prices, days, selected)
    result = ch002.realize(frame, days, observed, values, first_attempt=2)
    assert result["unfilled_request"] and not result["early_fill"] and result["fill_date"] is None
    assert result["realization_date"] == result["valuation_date"] == str(observed["exit_date"].date())
    assert result["G"] == observed["gross"] + 1
    if delisted:
        assert result["loss_net"] == [-1.] * 3
    else:
        assert result["loss_net"] == result["net"]


def test_successful_sale_before_presumed_delisting_keeps_realized_loss_sensitivity():
    prices, days, selected = holding_fixture()
    prices.loc[(days[31], "000010"), "close"] = 9000.
    prices.loc[(days[33:], "000010"), "volume"] = 0.
    frame, observed, _, values, _ = observed_case(prices, days, selected)
    assert observed["delisted"]
    result = ch002.realize(frame, days, observed, values, first_attempt=2)
    assert result["early_fill"] and not result["delisted_valuation"] and result["loss_net"] == result["net"]


@pytest.mark.parametrize("value", [np.nan, np.inf])
def test_partial_missing_ADV_sell_is_zero_and_counts_not_skip_mean(value):
    prices, days, selected = holding_fixture()
    frame, observed, entry, values, _ = observed_case(prices, days, selected)
    frame.loc[days[entry - 10], "trading_value"] = value
    frame.loc[days[entry + 1], "trading_value"] = 999e12  # sale date is excluded
    result = ch002.realize(frame, days, observed, values, first_attempt=2)
    assert result["sell_adv"] == 0. and result["sell_adv_incomplete"]
    assert result["costs"][0]["sell"] == pytest.approx(.0018 + .00015 + .001 + .003)


def test_ADV_zero_is_included_and_negative_stops_even_alongside_missing():
    prices, days, selected = holding_fixture()
    frame, _, entry, _, _ = observed_case(prices, days, selected)
    frame.loc[days[entry - 5], "trading_value"] = 0.
    adv, incomplete = ch002.sell_adv(frame, entry + 1)
    assert adv == 19 * 2e9 / 20 and not incomplete
    frame.loc[days[entry - 6], "trading_value"] = np.nan
    frame.loc[days[entry - 5], "trading_value"] = -1.
    with pytest.raises(ValueError, match="negative trading_value"):
        ch002.sell_adv(frame, entry + 1)


def test_missing_base_uses_previous_close_but_invalid_reference_stops():
    prices, days, selected = holding_fixture()
    frame, observed, entry, values, _ = observed_case(prices, days, selected)
    frame.loc[days[entry + 1], ["base_price", "open"]] = [np.nan, 7051.]
    result = ch002.realize(frame, days, observed, values, first_attempt=2)
    assert result["fill_day"] == 2 and result["G"] == .7051
    frame.loc[days[entry + 1], "base_price"] = 0.
    with pytest.raises(ValueError, match="reference"):
        ch002.realize(frame, days, observed, values, first_attempt=2)


def test_monthly_fixed_B_weights_unfilled_cash_and_no_real_request_no_reprice():
    prices, days, selected = holding_fixture(("000010", "000020"))
    prices.loc[(days[31], "000020"), "volume"] = 0.
    observed = ch001.forward_observations(prices, [selected], days)
    cohorts = ch002.build_cohorts(prices, observed, [selected], days)
    rows = ch002.monthly(cohorts, "x_trail10")
    baseline = cohorts[0]["records"]["x_trail10"][0]["baseline"]["net"][0]
    assert rows[0]["costs"]["1.0"]["difference"] == 0.
    assert rows[0]["costs"]["1.0"]["baseline_net"] == baseline / 2
    assert rows[0]["cash_weight_day_average"] == .5
    assert rows[0]["requests"] == rows[0]["early_fills"] == 0
    assert np.count_nonzero(cohorts[0]["nets"][:, 2:]) == 0


def test_controls_golden_stream_choice_then_permutation_including_m_zero(monkeypatch):
    monkeypatch.setattr(ch002, "N_CONTROLS", 4)
    prices, days, selected = holding_fixture(("000030", "000010", "000020"))
    prices.loc[(days[31], "000010"), "close"] = 9000.
    prices.loc[(days[33], "000020"), "ret_1d"] = -.2
    observations = ch001.forward_observations(prices, [selected], days)
    cohorts = ch002.build_cohorts(prices, observations, [selected], days)
    # A zero-request month still calls both RNG functions.
    zero = copy.deepcopy(cohorts[0])
    zero["records"]["x_trail10"] = [dict(r, first_attempt_day=None) for r in zero["records"]["x_trail10"]]
    zero["selection"]["month"] = "2024-03"
    cohorts.append(zero)
    rows = ch002.monthly(cohorts, "x_trail10")
    original, calls, seeds = np.random.default_rng, [], []
    def tracked(seed):
        seeds.append(seed)
        rng = original(seed)
        def choice(candidates, *, size, replace):
            calls.append(("choice", size, replace))
            return rng.choice(candidates, size=size, replace=replace)
        def permutation(size):
            calls.append(("permutation", size))
            return rng.permutation(size)
        return SimpleNamespace(choice=choice, permutation=permutation)
    monkeypatch.setattr(np.random, "default_rng", tracked)
    actual = ch002.random_controls(cohorts, rows, "x_trail10")
    assert seeds == [0] and calls == [("choice", 2, False), ("permutation", 2), ("choice", 0, False), ("permutation", 0)] * 4
    rng, digest, expected = original(0), hashlib.sha256(), []
    for path in range(4):
        differences = []
        for cohort, row in zip(cohorts, rows):
            requests = [r["first_attempt_day"] for r in cohort["records"]["x_trail10"] if r["first_attempt_day"] is not None]
            chosen = rng.choice(cohort["filled"], size=len(requests), replace=False)
            assigned = [requests[i] for i in rng.permutation(len(requests))]
            digest.update((json.dumps([path, row["month"], [cohort["codes"][i] for i in chosen], assigned], separators=(",", ":")) + "\n").encode())
            attempts = np.zeros(len(cohort["codes"]), dtype=int)
            attempts[chosen] = assigned
            differences.append(float(cohort["nets"][np.arange(len(attempts)), attempts, 0].mean()) - row["costs"]["1.0"]["baseline_net"])
        expected.append(float(np.mean(differences)))
    assert actual["path_means"] == expected and actual["draws_sha256"] == digest.hexdigest()
    loss = ch002.random_controls(cohorts, ch002.monthly(cohorts, "x_trail10", loss=True), "x_trail10", loss=True)
    assert loss["draws_sha256"] == actual["draws_sha256"]


def test_controls_keep_delayed_and_unfilled_requests_first_attempt_days(monkeypatch):
    monkeypatch.setattr(ch002, "N_CONTROLS", 2)
    prices, days, selected = holding_fixture(("000010", "000020", "000030", "000040"))
    prices.loc[(days[31], "000010"), "close"] = 9000.  # request day 2, fill day 4
    prices.loc[(days[32:34], "000010"), "volume"] = 0.
    prices.loc[(days[34], "000010"), "open"] = 8000.
    prices.loc[(days[33], "000020"), "ret_1d"] = -.2  # request day 4, never filled
    prices.loc[(days[34:51], "000020"), "volume"] = 0.
    # Adjusted gains with unchanged raw quotes; neither control name signals.
    prices.loc[(days[32], "000030"), "ret_1d"] = .1
    prices.loc[(days[32], "000040"), "ret_1d"] = .2
    observed = ch001.forward_observations(prices, [selected], days)
    cohorts = ch002.build_cohorts(prices, observed, [selected], days)
    records = cohorts[0]["records"]["x_trail10"]
    assert [r["first_attempt_day"] for r in records] == [2, 4, None, None]
    assert [r["fill_day"] for r in records] == [4, 0, 0, 0]
    assert [r["unfilled_request"] for r in records] == [False, True, False, False]
    rows = ch002.monthly(cohorts, "x_trail10")
    assert rows[0]["requests"] == 2 and rows[0]["early_fills"] == 1
    # All baseline sales use 10,000 and ADV 2e9. Only the delayed sale uses 8,000.
    factor = (1 - .00345) / (1 + .00165)
    delayed_net = .72 * (1 - (.0018 + .00015 + 10/8000 + .0005)) / (1 + .00165) - 1
    assert rows[0]["costs"]["1.0"]["baseline_net"] == pytest.approx(factor - 1)
    assert rows[0]["costs"]["1.0"]["difference"] == pytest.approx((delayed_net - (.9 * factor - 1)) / 4)

    orders, calls, seeds = iter(([1, 0], [0, 1])), [], []
    def choice(candidates, *, size, replace):
        calls.append((list(candidates), size, replace))
        return np.array([2, 3])
    def permutation(size):
        assert size == 2
        return np.array(next(orders))
    def rng(seed):
        seeds.append(seed)
        return SimpleNamespace(choice=choice, permutation=permutation)
    monkeypatch.setattr(np.random, "default_rng", rng)
    original, assigned = ch002.cache_exit, []
    def cache_exit(cohort, number, attempt):
        assigned.append((cohort["codes"][number], attempt))
        return original(cohort, number, attempt)
    monkeypatch.setattr(ch002, "cache_exit", cache_exit)
    result = ch002.random_controls(cohorts, rows, "x_trail10")
    assert seeds == [0] and calls == [([0, 1, 2, 3], 2, False)] * 2
    assert assigned == [("000030", 4), ("000040", 2), ("000030", 2), ("000040", 4)]
    assert result["actual_execution"] == {"early_fills": 1, "early_sale_cash_days": 17,
        "unfilled_requests": 1, "fill_date_counts": {str(days[34].date()): 1}}
    assert result["path_execution"] == [{"early_fills": 2, "early_sale_cash_days": 36,
        "unfilled_requests": 0, "fill_date_counts": {str(days[32].date()): 1, str(days[34].date()): 1},
        "sell_adv_incomplete": 0, "raw_price_fallback": 0}] * 2
    # Baseline G: (.9 + .8 + 1.1 + 1.2)/4 = 1. Control G: .95 and .975.
    assert result["path_means"] == pytest.approx([-.05 * factor, -.025 * factor])


def metrics_fixture():
    prototype = judgement_fixture()["ch_heat"]
    return {v: copy.deepcopy(prototype) for v in ch002.VARIANTS}


@pytest.mark.parametrize("trials,t,verdict", [(20, 2., "fail"), (20, 2.01, "pass"), (21, 3., "fail"), (21, 3.01, "pass")])
def test_strict_t_after_two_current_rows_and_Holm2(runner_repo, trials, t, verdict):
    metrics = metrics_fixture()
    for r in metrics.values(): r["primary"]["t_stat"] = t
    ch002.judge_variants(metrics, ch002._registration(runner_repo).fields, trials)
    assert all(r["verdict"] == verdict for r in metrics.values())
    assert ch001.holm(dict(zip(ch002.VARIANTS, [.01, .04])), variants=ch002.VARIANTS) == dict(zip(ch002.VARIANTS, [.02, .04]))


@pytest.mark.parametrize("failure,expected", [("months", "insufficient"), ("early_months", "insufficient"),
    ("undefined", "insufficient"), ("cost", "fail"), ("control", "fail"), ("holm", "fail"), ("mean", "risk_only")])
def test_every_judgement_gate_and_missing_p_is_one(runner_repo, failure, expected):
    metrics = metrics_fixture()
    r = metrics["x_trail10"]
    if failure == "months": r["primary"]["count"] = 35
    elif failure == "early_months": r["filtered_months"] = 35
    elif failure == "undefined": r["primary"]["standard_error"], r["primary"]["t_stat"] = 0., None
    elif failure == "cost": r["cost_sensitivity"]["1.5"]["difference"] = 0.
    elif failure == "control": r["random_control"]["share_of_controls"] = .05001
    elif failure == "holm":
        for v in metrics.values(): v["primary"]["p_one_sided"] = .04
    else: r["primary"]["mean"] = 0.
    ch002.judge_variants(metrics, ch002._registration(runner_repo).fields, 2)
    assert r["verdict"] == expected
    if expected == "insufficient": assert r["judgement_inputs"]["p_for_holm"] == 1.


@pytest.mark.parametrize("prior", [18, 19])
def test_fixture_execute_exactly_two_compact_rows_and_provenance(runner_repo, prior):
    data, prices, days = prepared_fixture(runner_repo)
    for _ in range(prior): experiment_registry.record_trial(ch002.EXPERIMENT_ID, "prior", {}, "fail", repo_root=runner_repo)
    result = ch002.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert experiment_registry.trial_count(ch002.EXPERIMENT_ID, repo_root=runner_repo) == prior + 2
    for rule in ch001.RULES:
        assert result["assertions"][rule]["disabled_exit_HF001_gross_equal"]
        for v in ch002.VARIANTS:
            r = result["rules"][rule][v]
            assert r["judgement_inputs"]["trial_count_after_run"] == prior + 2
            assert r["judgement_inputs"]["t_threshold"] == (2. if prior == 18 else 3.)
            assert r["random_control"]["draws_sha256"] == result["delisting_sensitivity"][rule][v]["random_control"]["draws_sha256"]
    assert result["source_hashes"]["preregistrations"][ch001.EXPERIMENT_ID] == result["reference_sha256"]
    assert result["source_hashes"]["code"]["backtesting/experiments/ch002.py"] == hf001._digest(Path(ch002.__file__))
    assert len(list((runner_repo / "research/experiments" / ch002.EXPERIMENT_ID / "results").glob("*.json"))) == 1


def protect_fixture(root, *, frozen, refresh):
    data, prices, days = prepared_fixture(root)
    for date in ("20260102", "20260630", "20260701"):
        write_rows(data / f"market/krx_daily/2026/{date}.jsonl", [{"stock_code": "000030", "trade_date": pd.Timestamp(date).date().isoformat()}])
    state = checkpoint(data)
    if not frozen:
        del state["target_scan"]
        (data / "disclosures/dart_full/list_supplement/_state.json").write_text(json.dumps(state))
    if refresh:
        (data / "reference/corp_codes.csv").write_text("corp_code,stock_code\n00000003,000030\n")
        write_rows(data / "reference/dart_company/companies.jsonl", [{"corp_code": "00000003", "corp_cls": "E"}])
    return data, prices, days


def trap_price_opens(monkeypatch):
    path_open, builtin_open, opened = Path.open, builtins.open, []
    def check(path):
        if isinstance(path, (str, Path)):
            p = Path(path)
            if "krx_daily" in p.parts:
                assert not "20260101" <= p.stem <= "20260630", "protected price content opened"
                if p.stem.startswith("2026"):
                    opened.append(p)
            assert p.name not in (".env", ".env-ai")
    def checked_path(path, *a, **k):
        check(path)
        return path_open(path, *a, **k)
    def checked_builtin(path, *a, **k):
        check(path)
        return builtin_open(path, *a, **k)
    monkeypatch.setattr(Path, "open", checked_path)
    monkeypatch.setattr(builtins, "open", checked_builtin)
    return opened


@pytest.mark.parametrize("mode", ["design", "dry_run"])
@pytest.mark.parametrize("frozen,refresh", [(False, True), (True, True), (True, False)])
def test_design_and_dry_run_never_open_protected_price_files(runner_repo, monkeypatch, mode, frozen, refresh):
    data, prices, days = protect_fixture(runner_repo, frozen=frozen, refresh=refresh)
    trap_price_opens(monkeypatch)
    original = ch001.calendar_plan
    monkeypatch.setattr(ch001, "calendar_plan", lambda sessions=None: original(days if sessions is None else sessions))
    original_hashes = ch001.source_hashes
    def hashes(data_dir, paths, **kwargs):
        assert all(day.year < 2026 for day, _ in paths)
        if kwargs.get("prices") is not None: assert common._sessions(kwargs["prices"]).max().year < 2026
        return original_hashes(data_dir, paths, **kwargs)
    monkeypatch.setattr(ch001, "source_hashes", hashes)
    if mode == "dry_run":
        monkeypatch.setattr(ch001, "forward_observations", lambda *a, **k: pytest.fail("dry-run real outcomes"))
        monkeypatch.setattr(ch002, "judge_variants", lambda *a, **k: pytest.fail("dry-run judgement"))
        monkeypatch.setattr(ch002, "publish", lambda *a, **k: pytest.fail("dry-run publication"))
        result = ch002.dry_run(data_dir=data, repo_root=runner_repo)
    else:
        result = ch002.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert result["listing_status"]["supplement_complete"] == (frozen and not refresh)
    assert not (runner_repo / holdout.LEDGER_PATH).exists()


def test_cli_defaults_dry_and_no_holdout(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ch002, "dry_run", lambda: calls.append("dry") or {})
    monkeypatch.setattr(ch002, "run_experiment", lambda: calls.append("execute") or {"variants": {}})
    for argv in ([], ["--dry-run"], ["--execute"]): assert ch002.main(argv) == 0
    assert calls == ["dry", "dry", "execute"]
    with pytest.raises(SystemExit): ch002.main(["--holdout"])
    with pytest.raises(SystemExit): ch002.main(["--execute", "--dry-run"])


@pytest.mark.parametrize("day", ["2026-01-02", "2026-07-01"])
def test_design_rejects_any_2026_evaluation_price_before_forward_or_holdout(runner_repo, monkeypatch, day):
    data, prices, days = prepared_fixture(runner_repo)
    extra = prices.iloc[:1].copy()
    extra.index = pd.MultiIndex.from_tuples([(pd.Timestamp(day), "000010")], names=prices.index.names)
    prices = pd.concat([prices, extra]).sort_index()
    trap_price_opens(monkeypatch)
    monkeypatch.setattr(ch001, "forward_observations", lambda *a, **k: pytest.fail("2026 evaluation reached forward accounting"))
    with pytest.raises(ValueError, match="strictly before 2026"):
        ch002.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert not (runner_repo / holdout.LEDGER_PATH).exists()


def test_costs_use_actual_sell_date_tax_price_and_previous_ADV_across_year_boundary():
    prices, _days, _selected = holding_fixture()
    days = pd.bdate_range("2024-11-18", periods=75)
    prices.index = pd.MultiIndex.from_product([days, ["000010"]], names=prices.index.names)
    selected = selection(days[30], ["000010"])
    frame, observed, entry, values, _ = observed_case(prices, days, selected)
    # Entry in December, sale in January at a different tick/spread and ADV bucket.
    assert days[entry].year == 2024 and days[entry + 10].year == 2025
    frame.loc[days[entry + 10], "open"] = 50000.
    frame.loc[days[entry - 10:entry + 10], "trading_value"] = 0.
    result = ch002.realize(frame, days, observed, values, first_attempt=11)
    expected_buy = .00015 + .001 + .0005
    expected_sell = .0015 + .00015 + .002 + .003  # 2025 tax; tick 100 at 50,000
    assert result["sell_adv"] == 0. and not result["sell_adv_incomplete"]
    assert result["costs"][0]["buy"] == pytest.approx(expected_buy)
    assert result["costs"][0]["sell"] == pytest.approx(expected_sell)
    assert result["net"][0] == pytest.approx(5 * (1 - expected_sell) / (1 + expected_buy) - 1)


def test_all_filled_names_requested_control_exact_ties_and_secondary_execution():
    prices, days, selected = holding_fixture(("000010", "000020", "000030"))
    prices.loc[(days[31], slice(None)), "close"] = 9000.
    observed = ch001.forward_observations(prices, [selected], days)
    cohorts = ch002.build_cohorts(prices, observed, [selected], days)
    rows = ch002.monthly(cohorts, "x_trail10")
    result = ch002.random_controls(cohorts, rows, "x_trail10")
    assert result["share_of_controls"] == 1.
    assert result["actual_execution"]["early_fills"] == 3
    assert all(p["early_fills"] == 3 and p["early_sale_cash_days"] == 57 for p in result["path_execution"])


def test_delisting_rejudges_both_variants_Holm_and_controls(runner_repo, monkeypatch):
    calls = []
    def metrics(cohorts, variant, *, loss=False, diagnostics=True):
        calls.append((variant, loss))
        r = metrics_fixture()[variant]
        r["random_control"]["draws_sha256"] = "identical-draws"
        if loss: r["cost_sensitivity"]["1.5"]["difference"] = -.01
        return r
    monkeypatch.setattr(ch002, "variant_metrics", metrics)
    main, loss = ch002.evaluate_rule(None, ch002._registration(runner_repo).fields, 2)
    assert len(calls) == 4 and all(r["verdict"] == "delisting_sensitive" for r in main.values())
    assert all(r["verdict"] == "fail" and "holm_p" in r for r in loss.values())


def test_one_real_delisting_changes_control_exits_holm_and_final_verdict(runner_repo):
    days = pd.bdate_range("2020-01-02", periods=1050)
    prices, _ = market(days, codes=("000010", "000020"))
    prices[["open", "close", "base_price"]] = 10000.
    prices.high, prices.low = 11000., 8000.
    selections = []
    for i in range(40):
        entry = 31 + 25 * i
        selections.append(selection(days[entry - 1], ["000010", "000020"]))
        prices.loc[(days[entry:entry + 20], "000010"), "close"] = 9000.
        prices.loc[(days[entry], "000010"), "ret_1d"] = -.1
        prices.loc[(days[entry + 1:entry + 20], "000010"), ["open", "base_price"]] = 9000.
        maturity = .889 if i % 2 == 0 else .891
        prices.loc[(days[entry + 19], "000010"), ["close", "ret_1d"]] = [10000 * maturity, maturity / .9 - 1]
    # Only the final b position disappears, after a day-2 control sale could
    # have filled. Actual exits request only a, leaving b held to maturity.
    prices.loc[(days[entry + 2:], "000020"), "volume"] = 0.
    observed = ch001.forward_observations(prices, selections, days, repo_root=runner_repo)
    assert observed.delisted.sum() == 1
    assert observed.loc[observed.delisted, "stock_code"].tolist() == ["000020"]
    assert observed.loc[observed.delisted, "gross_delisting_loss"].tolist() == [-1.]
    cohorts = ch002.build_cohorts(prices, observed, selections, days)
    main, losses = ch002.evaluate_rule(cohorts, ch002._registration(runner_repo).fields, 2)
    for variant in ch002.VARIANTS:
        result, loss = main[variant], losses[variant]
        expected_d = [(c["records"][variant][0]["net"][0] - c["records"][variant][0]["baseline"]["net"][0]) / 2 for c in cohorts]
        assert [r["costs"]["1.0"]["difference"] for r in result["per_month"]] == pytest.approx(expected_d)
        assert [r["costs"]["1.0"]["difference"] for r in loss["per_month"]] == pytest.approx(expected_d)
        b_effects = []
        for c in cohorts:
            entry_date = pd.Timestamp(c["selection"]["trade_date"])
            sell_date = days[days.get_loc(entry_date) + 1]
            buy = ch001.cost_model.one_way_cost(10000., "KOSPI", entry_date.date(), 2e9, "buy", model_version="v1")
            sell = ch001.cost_model.one_way_cost(10000., "KOSPI", sell_date.date(), 2e9, "sell", model_version="v1")
            early_net = (1 - sell) / (1 + buy) - 1
            b_effects.append((early_net - c["records"][variant][1]["baseline"]["net"][0]) / 2)
        rng, controls, loss_controls = np.random.default_rng(0), [], []
        lost_baseline = cohorts[-1]["records"][variant][1]["baseline"]["net"][0]
        for _ in range(ch002.N_CONTROLS):
            chosen = []
            for _ in cohorts:
                chosen.append(rng.choice([0, 1], size=1, replace=False)[0])
                rng.permutation(1)
            mean = np.where(np.asarray(chosen) == 0, expected_d, b_effects).mean()
            controls.append(mean)
            # A path selling b on day 2 avoids its later -100% valuation.
            loss_controls.append(mean + ((lost_baseline + 1) / 2 / 40 if chosen[-1] == 1 else 0.))
        assert result["random_control"]["path_means"] == pytest.approx(controls)
        assert loss["random_control"]["path_means"] == pytest.approx(loss_controls)
        assert any(a == b for a, b in zip(controls, loss_controls))
        assert any(a < b for a, b in zip(controls, loss_controls))
        assert result["random_control"]["share_of_controls"] <= .05 < loss["random_control"]["share_of_controls"]
        assert result["random_control"]["draws_sha256"] == loss["random_control"]["draws_sha256"]
        assert result["holm_p"] == pytest.approx(min(2 * result["primary"]["p_one_sided"], 1.))
        assert loss["holm_p"] == pytest.approx(min(2 * loss["primary"]["p_one_sided"], 1.))
        assert result["base_verdict"] == "pass" and loss["verdict"] == "fail"
        assert result["verdict"] == ch001.av001.combine_verdict(result["verdict"], result["verdict"]) == "delisting_sensitive"


def test_all_registry_rows_validated_before_publication(runner_repo, monkeypatch):
    rules = {r: ch002.judge_variants(metrics_fixture(), ch002._registration(runner_repo).fields, 2) for r in ch001.RULES}
    payload = {"rules": rules, "delisting_sensitivity": copy.deepcopy(rules), "variants": {v: {"verdict": "pass"} for v in ch002.VARIANTS}}
    payload["rules"]["O"]["x_low10"]["judgement_inputs"]["oversize"] = "x" * 32768
    monkeypatch.setattr(common, "write_results", lambda *a, **k: pytest.fail("published before validating every row"))
    with pytest.raises(ValueError, match="32 KiB"):
        ch002.publish(payload, repo_root=runner_repo)
    assert not (runner_repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("module", [ch002, ch003])
def test_unavailable_O_never_reaches_forward_and_combines_as_sensitivity_unavailable(runner_repo, monkeypatch, module):
    data, prices, days = prepared_fixture(runner_repo)
    if module is ch003:
        from test_experiments_ch003 import save_indices
        save_indices(data, days)
        monkeypatch.setattr(ch003, "MONTHS", ("2024-05", "2024-06"))
    original_prepare, original_forward, calls = ch001.prepare_inputs, ch001.forward_observations, []
    def prepare(**kwargs):
        result = original_prepare(**kwargs)
        result[3]["sensitivity_available"] = False
        return result
    def forward(*args, **kwargs):
        calls.append(1)
        return original_forward(*args, **kwargs)
    monkeypatch.setattr(ch001, "prepare_inputs", prepare)
    monkeypatch.setattr(ch001, "forward_observations", forward)
    result = module.run_experiment(data_dir=data, repo_root=runner_repo, prices=prices, sessions=days)
    assert calls == [1]
    assert all(r["verdict"] == "sensitivity_unavailable" for r in result["variants"].values())
    assert all(r["verdict"] == "sensitivity_unavailable" for r in result["rules"]["O"].values())


@pytest.mark.parametrize("empty_B", [False, True])
def test_controls_still_call_zero_draws_with_empty_B_or_no_filled_entries(empty_B):
    prices, days, selected = holding_fixture(("000010", "000020"))
    if empty_B:
        selected["F_vol"] = set(selected["universe"].index)
        selected["P"], selected["B"] = ch001.baseline_set(selected)
    else:
        prices.loc[(days[31], slice(None)), "volume"] = 0.
    observed = ch001.forward_observations(prices, [selected], days)
    cohorts = ch002.build_cohorts(prices, observed, [selected], days)
    rows = ch002.monthly(cohorts, "x_trail10")
    result = ch002.random_controls(cohorts, rows, "x_trail10")
    assert rows[0]["valid"] and rows[0]["cash_weight_day_average"] == 1.
    assert result["path_means"] == [0.] * 200 and result["share_of_controls"] == 1.
    assert all(p["early_fills"] == p["unfilled_requests"] == 0 for p in result["path_execution"])


def test_never_prices_or_validates_an_undrawn_counterfactual(monkeypatch):
    prices, days, selected = holding_fixture(("000010", "000020", "000030", "000040"))
    prices.loc[(days[31], "000010"), "close"] = 9000.  # actual attempt 2
    prices.loc[(days[33], "000020"), "ret_1d"] = -.2  # actual attempt 4
    observed = ch001.forward_observations(prices, [selected], days)
    observed["tag"] = observed.stock_code
    original, seen = ch002.realize, []
    def realize(frame, sessions, row, values, *, first_attempt=None):
        seen.append((row["tag"], first_attempt))
        if row["tag"] == "000010" and first_attempt == 4:
            pytest.fail("an unused code/day counterfactual was evaluated")
        return original(frame, sessions, row, values, first_attempt=first_attempt)
    monkeypatch.setattr(ch002, "realize", realize)
    monkeypatch.setattr(ch002, "N_CONTROLS", 1)
    cohorts = ch002.build_cohorts(prices, observed, [selected], days)
    ch002.random_controls(cohorts, ch002.monthly(cohorts, "x_trail10"), "x_trail10")
    assert ("000010", 4) not in seen
    assert ("000030", 4) in seen and ("000040", 2) in seen
