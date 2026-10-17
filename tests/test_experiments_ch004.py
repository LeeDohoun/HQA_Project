"""CH004 synthetic event/rank/inference/publication fixtures; no real execution."""
import builtins
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import ch001, ch004, ev001, hc003, ordering
from src.ingestion.storage import write_rows
from ch_test_fixtures import hermetic_ch_roots
from dart_listing_fixtures import checkpoint
from preregistration_fixtures import committed_preregistrations
from test_experiments_ev001 import calendar, event, financial_fixture, prepared_for
from test_experiments_hc003 import prices_for


@pytest.fixture
def repo(tmp_path):
    names = (*ch004.REFERENCE_HASHES, ev001.hc002.EXPERIMENT_ID, hc003.EXPERIMENT_ID, ev001.hf001.EXPERIMENT_ID)
    committed_preregistrations(tmp_path, names)
    for name in ("README.md", "DATA_ISSUES.md"):
        (tmp_path / "research/experiments" / name).write_bytes((ch004.PROJECT_ROOT / "research/experiments" / name).read_bytes())
    return tmp_path


@pytest.fixture
def synthetic_ev001_source(monkeypatch):
    # Exercise synthetic CH004 logic with the live source and honest provenance.
    # Tests of the reviewed-source policy deliberately do not use this fixture.
    monkeypatch.setattr(ch004, "EV001_CODE_SHA256_ALLOWLIST",
                        ch004.EV001_CODE_SHA256_ALLOWLIST | {hc003._digest(ev001.__file__)})


@pytest.fixture
def complete_inputs(repo, synthetic_ev001_source):
    data = repo / "data"
    _, listings = financial_fixture(data)
    for row in listings:
        write_rows(data / "disclosures/dart_full/list" / row["rcept_dt"][:4] / (row["rcept_dt"] + ".jsonl"), [row])
    # These protected files are created/frozen by the fixture builder before
    # traps. CH004 must subsequently inventory them without opening contents.
    for stamp in ("20251230", "20251231", "20260102", "20260303", "20260630", "20260701"):
        write_rows(data / "market/krx_daily" / stamp[:4] / (stamp + ".jsonl"),
                   [{"trade_date": str(pd.Timestamp(stamp).date()), "stock_code": "000010"}])
    checkpoint(data)
    days = calendar("2023-01-01", "2025-12-30")
    prices = prices_for(days)
    prices.loc[(slice(None), "000010"), "ret_1d"] = .001
    return dict(data_dir=data, repo_root=repo, prices=prices, sessions=days)


def book_for(root, *, codes=None, days=None, prices=None):
    days = pd.bdate_range("2023-01-02", periods=300) if days is None else days
    codes = [f"{n:06d}" for n in range(1, 11)] if codes is None else codes
    prices = prices_for(days, codes) if prices is None else prices
    book = ev001.EventPrices(days, days[-1], root)
    book.add(prices)
    book.finish()
    return book, prices


def prepared_metadata(prepared, *, available=True, complete=True):
    prepared.update(versions={r: SimpleNamespace(sensitivity_available=available, annotations=[], report=lambda: {}) for r in ch004.RULES},
        listing_status={"supplement_complete": complete, "reason": None if complete else "fixture supplement incomplete"})
    return prepared


def chart_row(month, code, value, *, removed=True, computable=True, day="02"):
    return {"entry_month": month, "entry_date": month + "-" + day, "stock_code": code, "fiscal_quarter": "2024Q1",
        "excess": value, "excess_loss": value, "market": "KOSPI", "entry_adv": 2e9,
        **{"excess_" + scenario + suffix: value for scenario in hc003.SCENARIOS for suffix in ("", "_loss")},
        "charts": {v: {"computable": computable, "removed": removed, "reason": "" if computable else "market_unavailable"}
                   for v in ch004.VARIANTS}}


def trap_opens(monkeypatch, data):
    opened = []
    original_builtin, original_io = builtins.open, io.open
    def check(path, mode):
        if isinstance(path, (str, bytes, Path)):
            p = Path(path)
            if p.name in (".env", ".env-ai"):
                pytest.fail("private environment file opened")
            if "krx_daily" in p.parts:
                if p.stem.isdigit() and "20260101" <= p.stem <= "20260630":
                    pytest.fail("protected 2026 price file opened")
                if p.stem.isdigit() and p.stem > "20251230":
                    pytest.fail("out-of-evaluation price file opened")
            if p.is_relative_to(data) and any(c in mode for c in "wax+"):
                pytest.fail("data write attempted")
            opened.append(str(p))
    def builtin(path, mode="r", *args, **kwargs):
        check(path, mode)
        return original_builtin(path, mode, *args, **kwargs)
    def file_io(path, mode="r", *args, **kwargs):
        check(path, mode)
        return original_io(path, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", builtin)
    monkeypatch.setattr(io, "open", file_io)
    return opened


def test_registration_pins_all_three_documents(repo, synthetic_ev001_source):
    registered = ch004._registration(repo)
    assert registered.commit_hash.startswith("08079e2")
    assert registered.fields["variants_planned"] == 2
    assert not registered.fields["uses_holdout"] and not registered.fields["uses_llm"]


def test_real_allowlist_keeps_two_reviewed_hashes_and_refuses_current_EV001(repo, monkeypatch):
    assert ch004.EV001_CODE_SHA256_ALLOWLIST == {
        "1063af57c51f07af9f28f96b523aede7ae10d44ed9317d3f03705f708be605e6",
        "e101b5ac04c8c56d588f7d3fbea8ad8a4a879b416520f81337e7c83ae50884db",
    }
    assert hc003._digest(ev001.__file__) not in ch004.EV001_CODE_SHA256_ALLOWLIST
    monkeypatch.setattr(ch004, "prepare_inputs", lambda **k: pytest.fail("data accessed"))
    with pytest.raises(ValueError, match="EV001 source"):
        ch004._registration(repo)
    for runner in (ch004.run_experiment, ch004.dry_run):
        with pytest.raises(ValueError, match="EV001 source"):
            runner(repo_root=repo)
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("digest", [
    "1063af57c51f07af9f28f96b523aede7ae10d44ed9317d3f03705f708be605e6",
    "e101b5ac04c8c56d588f7d3fbea8ad8a4a879b416520f81337e7c83ae50884db",
    "0" * 64,
])
def test_EV001_source_allowlist_rejects_every_other_hash(repo, monkeypatch, digest):
    original = hc003._digest
    monkeypatch.setattr(hc003, "_digest", lambda path: digest if Path(path) == Path(ev001.__file__) else original(path))
    if digest in ch004.EV001_CODE_SHA256_ALLOWLIST:
        ch004._registration(repo)
    else:
        monkeypatch.setattr(ch004, "prepare_inputs", lambda **k: pytest.fail("data accessed"))
        for runner in (ch004.run_experiment, ch004.dry_run):
            with pytest.raises(ValueError, match="EV001 source"):
                runner(repo_root=repo)
        assert not (repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("name", [".env", ".env-ai"])
def test_env_preflight_checks_existence_without_opening(tmp_path, monkeypatch, name, hermetic_ch_roots):
    (hermetic_ch_roots / name).touch()
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("preflight opened a file"))
    with pytest.raises(ValueError, match="no environment file was read"):
        ch004._registration(tmp_path)


@pytest.mark.parametrize("name", list(ch004.REFERENCE_HASHES))
def test_changed_preregistration_stops_before_data(repo, monkeypatch, name, synthetic_ev001_source):
    path = repo / "research/experiments" / name / "preregistration.md"
    path.write_text(path.read_text() + "\nchanged")
    monkeypatch.setattr(ch004, "prepare_inputs", lambda **k: pytest.fail("data accessed"))
    for runner in (ch004.run_experiment, ch004.dry_run):
        reason = ("preregistration git check failed" if name == ch004.EXPERIMENT_ID and runner == ch004.run_experiment
                  else "reference SHA-256 mismatch")
        with pytest.raises(ValueError, match=reason):
            runner(repo_root=repo)
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()


def test_anchor_runup_inclusive_windows_max_ties_and_exact_boundaries(tmp_path):
    book, _ = book_for(tmp_path)
    codes = sorted(book.raw)
    # The first stored return is validated but never multiplied into A.
    for i, code in enumerate(codes):
        book.raw[code][:, 4] = -.001 * (10 - i)
        book.raw[code][0, 4] = .9
    charts = ch004.chart_filters(book, codes, str(book.days[251].date()))
    anchor, runup = charts["e_anchor"], charts["e_runup"]
    assert anchor.R.tolist() == pytest.approx([(1 - .001 * (10 - i)) ** 251 for i in range(10)])
    assert runup.U.tolist() == pytest.approx([(1 - .001 * (10 - i)) ** 20 - 1 for i in range(10)])
    assert anchor.percentile.tolist() == pytest.approx(np.arange(1, 11) / 10)
    assert anchor.index[anchor.removed].tolist() == codes[:3]  # <=.30
    assert runup.index[runup.removed].tolist() == codes[8:]  # >.80, not >=
    # A tie's maximum rank is used, not its average/minimum rank.
    book.raw[codes[3]][:, 4] = book.raw[codes[2]][:, 4]
    tied = ch004.chart_filters(book, codes, str(book.days[251].date()))["e_anchor"]
    assert tied.loc[codes[2:4], "percentile"].tolist() == [.4, .4]
    assert not tied.loc[codes[2:4], "removed"].any()


def test_s_last_XKRX_session_at_d_and_d_return_included(tmp_path):
    days = calendar("2023-01-01", "2025-05-20")
    book, _ = book_for(tmp_path, days=days)
    pos = days.get_loc(pd.Timestamp("2025-05-16"))  # Friday
    code = sorted(book.raw)[0]
    book.raw[code][pos, 4] = .25
    book.raw[code][pos + 1, 4] = -.9  # Monday must never leak into Sunday.
    for d in ("2025-05-16", "2025-05-17", "2025-05-18"):
        result = ch004.chart_filters(book, sorted(book.raw), d)["e_runup"]
        assert result.loc[code, "session_date"] == "2025-05-16"
        assert result.loc[code, "U"] == pytest.approx(.25)
        assert result.loc[code, "removed"]


def test_preentry_full_V_tau_market_at_s_and_invalid_market_denominator(tmp_path):
    book, _ = book_for(tmp_path)
    codes, pos = sorted(book.raw), 270
    for i, code in enumerate(codes):
        book.raw[code][:, 4] = -.001 * (10 - i)
    # A non-executable name is still in the rank comparison universe.
    book.raw[codes[9]][pos + 1, 2] = 0.
    # A changed market uses s itself, with no previous/current substitution.
    book.market[codes[8]][pos] = 2
    book.market[codes[9]][pos] = 0
    book.market[codes[9]][pos - 1] = 1
    book.market[codes[9]][pos + 1] = 1
    charts = ch004.chart_filters(book, codes, str(book.days[pos].date()))
    for frame in charts.values():
        assert frame.loc[codes[0], "rank_denominator"] == 8
        assert frame.loc[codes[8], "market"] == "KOSDAQ" and frame.loc[codes[8], "percentile"] == 1.
        assert frame.loc[codes[9], "reason"] == "market_unavailable"
        assert not frame.loc[codes[9], "computable"] and not frame.loc[codes[9], "removed"]
    book.market[codes[9]][pos] = 1
    ranked = ch004.chart_filters(book, codes, str(book.days[pos].date()))["e_anchor"]
    assert ranked.loc[codes[0], "rank_denominator"] == 9
    assert ranked.loc[codes[0], "percentile"] == 1 / 9


@pytest.mark.parametrize("bad,reason", [(np.nan, "invalid_return"), (-1., "invalid_return"), (np.inf, "invalid_return")])
@pytest.mark.parametrize("length,variant", [(252, "e_anchor"), (21, "e_runup")])
def test_invalid_first_return_disables_without_filling(tmp_path, bad, reason, length, variant):
    book, _ = book_for(tmp_path)
    code, pos = sorted(book.raw)[0], 270
    book.raw[code][pos - length + 1, 4] = bad
    frame = ch004.chart_filters(book, sorted(book.raw), str(book.days[pos].date()))[variant]
    assert frame.loc[code, "reason"] == reason
    assert not frame.loc[code, "computable"] and not frame.loc[code, "removed"]


@pytest.mark.parametrize("bad,invalid_close,failure", [
    (np.inf, False, "invalid adjusted return"),
    (-1., False, "undefined adjusted open-to-close return"),
    (np.nan, False, None),
    (-1., True, None),
], ids=["inf-stops-preparation", "minus-one-stops-baseline", "nan-keeps-event", "minus-one-keeps-event"])
def test_source_invalid_returns_respect_EV001_base_and_chart_boundary(complete_inputs, monkeypatch,
                                                                     bad, invalid_close, failure):
    root = complete_inputs["repo_root"]
    registration = experiment_registry.verify_preregistration(ch004.EXPERIMENT_ID, root)
    # Inject into the source frame before real EV001.prepare/EventPrices.finish.
    # The public session is in both chart windows and precedes executable entry.
    key = (pd.Timestamp("2025-05-15"), "000010")
    complete_inputs["prices"].loc[key, "ret_1d"] = bad
    if invalid_close:
        complete_inputs["prices"].loc[key, "close"] = np.nan
    trap_opens(monkeypatch, complete_inputs["data_dir"])
    if failure:
        monkeypatch.setattr(ch004, "chart_filters", lambda *a, **k: pytest.fail("charts reached after base failure"))
        monkeypatch.setattr(ch004, "publish", lambda *a, **k: pytest.fail("published invalid baseline"))
        with np.errstate(invalid="ignore"), pytest.raises(ValueError, match=failure):
            ch004.run_experiment(**complete_inputs)
        rows = hc003.registry_rows(root)
        assert [r["variant"] for r in rows] == list(ch004.VARIANTS)
        assert all(r["verdict"] == "design_aborted" and r["prereg_commit"] == registration.commit_hash for r in rows)
        function = "finish" if np.isinf(bad) else "outcomes"
        assert all(json.loads(r["metrics_json"])["failure_location"]["function"] == function for r in rows)
        assert not list((root / "research/experiments" / ch004.EXPERIMENT_ID / "results").glob("*"))
    else:
        result = ch004.run_experiment(**complete_inputs)
        for rule in ch004.RULES:
            assert result["assertions"][rule]["ev_new20_exact"]
            baseline = result["frozen_baselines"][rule]["events"]
            events = result["chart_events"][rule]
            assert len(baseline) == len(events) == 1
            assert ch004.event_key(baseline[0]) == tuple(events[0]["event_key"])
            for variant in ch004.VARIANTS:
                chart = events[0]["charts"][variant]
                assert chart["reason"] == "invalid_return"
                assert not chart["computable"] and not chart["removed"]
                metrics = result["rules"][rule][variant]
                assert metrics["event_count"] == 1 and metrics["removed_events"] == 0
        rows = hc003.registry_rows(root)
        assert [r["variant"] for r in rows] == list(ch004.VARIANTS)
        assert all(r["verdict"] == "insufficient" for r in rows)
        json.dumps(result, allow_nan=False)


def test_CH001_adjusted_function_parity_zero_volume_short_window_and_overflow(tmp_path):
    book, prices = book_for(tmp_path)
    codes, pos = sorted(book.raw), 270
    code = codes[0]
    book.raw[code][:, 2] = 0.
    prices.loc[(slice(None), code), "volume"] = 0.
    for variant, length, metric in (("e_anchor", 252, "R"), ("e_runup", 21, "U")):
        a, reasons = ch001.adjusted_index(prices, codes, book.days[pos], book.days, length)
        actual = ch004.chart_filters(book, codes, str(book.days[pos].date()))[variant]
        assert not reasons.any() and actual.loc[code, "computable"]
        expected = a[-1] / a.max(axis=0) if length == 252 else a[-1] / a[0] - 1
        assert actual[metric].tolist() == pytest.approx(expected)
    short = ch004.chart_filters(book, codes, str(book.days[10].date()))
    assert all(frame.reason.eq("short_calendar").all() for frame in short.values())
    book.raw[code][:, 4] = 1e300
    assert ch004.chart_filters(book, codes, str(book.days[pos].date()))["e_anchor"].loc[code, "reason"] == "invalid_adjusted_index"
    book.raw[code][:, 4] = 0.
    book.verified[code][pos - 10] = False
    assert all(frame.loc[code, "reason"] == "unverified_or_missing_session" for frame in ch004.chart_filters(book, codes, str(book.days[pos].date())).values())


def test_EV001_final_overlap_is_fixed_before_filter_and_x_scenarios_exact(tmp_path):
    days = calendar("2024-01-01", "2025-12-30")
    book, prices = book_for(tmp_path, days=days, codes=["000010", "000020", "000030"])
    first = days.get_loc(pd.Timestamp("2025-05-16"))
    prices.loc[(days[first + 2], "000010"), "ret_1d"] = .1
    prices.loc[(days[first], "000030"), "volume"] = 0
    book, _ = book_for(tmp_path, days=days, prices=prices)
    rows = [event(), event(str(days[first + 5].date()), fiscal_quarter="2025Q2")]
    prepared = prepared_metadata(prepared_for(book, rows))
    frozen, assertions = ch004.freeze_baselines(prepared)
    actual, _ = ch004.ev001_baseline(prepared, "S")
    saved = frozen["S"]["events"]
    assert len(saved) == 1 and frozen["S"]["counts"]["total"]["holding_overlap"] == 1
    assert frozen["S"]["universes"]["2025-04-30"] == sorted(book.raw)
    assert actual[0]["benchmark_dropped_members"] == 1
    for scenario in hc003.SCENARIOS:
        assert saved[0]["excess_" + scenario] == actual[0][scenario] - actual[0]["benchmark"]
        assert saved[0]["excess_" + scenario + "_loss"] == actual[0][scenario + "_loss"] - actual[0]["benchmark_loss"]
    assert assertions["S"]["ev_new20_exact"]
    ch004.attach_filters(prepared, "S", saved)
    assert len(saved) == 1  # Filtering does not admit the overlapped filing.
    assert all(actual[0][key] == saved[0][key] for key in actual[0])


@pytest.mark.parametrize("changed", ["key", "public_date", "entry_date", "scheduled_exit_date", "universe", "excess_v2_1.0", "excess_v2_1.5", "excess_v2_2.0_loss"])
def test_baseline_assertion_stops_every_required_mismatch(tmp_path, changed):
    days = calendar("2025-01-01", "2025-12-30")
    book, _ = book_for(tmp_path, days=days, codes=["000010", "000020"])
    prepared = prepared_metadata(prepared_for(book, [event()]))
    frozen, _ = ch004.freeze_baselines(prepared)
    rows = copy.deepcopy(frozen["S"]["events"])
    universe = copy.deepcopy(frozen["S"]["universes"])
    if changed == "key": rows[0]["fiscal_quarter"] = "2025Q2"
    elif changed == "universe": universe["2025-04-30"].pop()
    elif changed.startswith("excess"): rows[0][changed] += 1e-12
    else: rows[0][changed] = "2025-05-19"
    with pytest.raises(AssertionError, match="EV001|V_tau"):
        ch004.assert_baseline(frozen["S"], rows, universe)


def test_monthly_fixed_denominator_zero_month_and_level_means():
    rows = [chart_row("2024-01", "b", -.3), chart_row("2024-01", "a", .1, removed=False),
            chart_row("2024-01", "c", -.6, removed=False, computable=False),
            chart_row("2024-03", "a", .2, removed=False)]
    actual = ch004.monthly(rows, "e_anchor")
    assert [r["month"] for r in actual] == ["2024-01", "2024-03"]
    assert actual[0]["difference"] == pytest.approx(.3 / 3)
    assert actual[1]["difference"] == 0.
    assert actual[0]["remaining_mean"] == -.25
    result = ch004.variant_metrics(rows, "e_anchor")
    assert result["primary"]["count"] == 2 and result["removal_months"] == 1
    assert result["primary"]["mean"] == pytest.approx(.05)
    assert result["excess_levels"]["remaining"]["mean_of_monthly_means"] == pytest.approx(-.025)
    assert result["filter_counts"]["uncomputable_by_reason"] == {"market_unavailable": 1}
    rows[0]["charts"]["e_anchor"]["computable"] = False
    with pytest.raises(AssertionError, match="evaluable"):
        ch004.monthly(rows, "e_anchor")


def test_NW_matches_EV001_calendar_lag3_manual_covariance():
    months = ["2020-01", "2020-03", "2020-04", "2020-08"]
    values = np.array([.2, -.1, .4, .2])
    rows = [chart_row(m, "a", -v) for m, v in zip(months, values)]
    stats = ch004.summarize(ch004.monthly(rows, "e_anchor"))["primary"]
    centered, n = values - values.mean(), len(values)
    variance = np.dot(centered, centered) / n
    ordinals = [pd.Period(m).ordinal for m in months]
    for lag in (1, 2, 3):
        covariance = sum(centered[a] * centered[b] for a in range(n) for b in range(a) if ordinals[a] - ordinals[b] == lag) / n
        variance += 2 * (1 - lag / 4) * covariance
    assert stats["standard_error"] == pytest.approx(math.sqrt(variance / n))
    assert stats["lag_pair_counts"] == {"1": 1, "2": 1, "3": 1}
    ev_rows = [{"entry_month": m, "entry_date": m + "-02", "excess": v} for m, v in zip(months, values)]
    assert stats == ev001.monthly_summary(ev_rows)["primary"]


def test_control_golden_stream_one_rng_sorted_keys_zero_all_and_uncomputable(monkeypatch):
    rows = [chart_row("2024-03", "b", -.1), chart_row("2024-03", "a", .2),
            chart_row("2024-01", "z", -100., removed=False, computable=False),
            chart_row("2024-01", "b", -.3), chart_row("2024-01", "a", .1, removed=False, day="03"),
            chart_row("2024-02", "a", -.4, removed=False)]
    actual_mean = ch004.summarize(ch004.monthly(rows, "e_anchor"))["primary"]["mean"]
    original, seeds, calls = np.random.default_rng, [], []
    def tracked(seed):
        seeds.append(seed)
        rng = original(seed)
        def choice(a, *, size, replace):
            calls.append((a, size, replace))
            return rng.choice(a, size=size, replace=replace)
        return SimpleNamespace(choice=choice)
    monkeypatch.setattr(np.random, "default_rng", tracked)
    result = ch004.random_controls(rows, "e_anchor", actual_mean)
    assert seeds == [0]
    assert calls == [(2, 1, False), (1, 0, False), (2, 2, False)] * 200
    rng, expected, digest = original(0), [], hashlib.sha256()
    for path in range(200):
        differences = []
        for month, values, n, k in [("2024-01", [-.3, .1], 3, 1), ("2024-02", [-.4], 1, 0), ("2024-03", [.2, -.1], 2, 2)]:
            chosen = rng.choice(len(values), size=k, replace=False)
            digest.update(json.dumps([path, month, chosen.tolist()], separators=(",", ":")).encode())
            differences.append(-sum(np.array(values)[sorted(chosen)]) / n if k else 0.)
        expected.append(float(np.mean(differences)))
    assert [p["mean"] for p in result["paths"]] == expected
    assert result["draws_sha256"] == digest.hexdigest()
    assert result["share_of_controls"] == sum(v >= actual_mean for v in expected) / 200
    assert len(set(expected)) > 1


def test_control_exact_ties_and_no_evaluable_candidates_still_call_choice(monkeypatch):
    rows = [chart_row("2024-01", "a", -.1), chart_row("2024-01", "b", -.2),
            chart_row("2024-02", "a", -.5, removed=False, computable=False)]
    actual = ch004.summarize(ch004.monthly(rows, "e_anchor"))["primary"]["mean"]
    result = ch004.random_controls(rows, "e_anchor", actual)
    assert result["share_of_controls"] == 1.
    assert all(p["mean"] == actual for p in result["paths"])


def judgement_results():
    return {v: {"event_count": 300, "removal_months": 36, "removed_events": 100,
        "primary": {"count": 36, "mean": .01, "standard_error": .001, "t_stat": 2.5, "p_one_sided": .01},
        "cost_sensitivity": {"1.5": {"primary": {"mean": .011}}}, "random_control": {"share_of_controls": .05}}
        for v in ch004.VARIANTS}


@pytest.mark.parametrize("trials,verdict", [(20, "pass"), (21, "fail")])
def test_holm_two_and_strict_cumulative_trial_threshold(repo, trials, verdict, synthetic_ev001_source):
    results = judgement_results()
    results["e_runup"]["primary"]["p_one_sided"] = .04
    actual = ch004.judge_variants(results, ch004._registration(repo).fields, trials)
    assert actual["e_anchor"]["holm_p"] == .02 and actual["e_runup"]["holm_p"] == .04
    assert all(r["verdict"] == verdict for r in actual.values())
    for r in actual.values(): r["primary"]["t_stat"] = 2. if trials == 20 else 3.
    assert all(r["verdict"] == "fail" for r in ch004.judge_variants(actual, ch004._registration(repo).fields, trials).values())


@pytest.mark.parametrize("missing", ["events", "months", "removal_months", "se", "t", "p", "mean", "cost", "control"])
def test_insufficient_supplies_p_one_but_keeps_Holm_two(repo, missing, synthetic_ev001_source):
    results = judgement_results()
    r = results["e_anchor"]
    if missing == "events": r["event_count"] = 299
    elif missing == "months": r["primary"]["count"] = 35
    elif missing == "removal_months": r["removal_months"] = 35
    elif missing == "cost": r["cost_sensitivity"]["1.5"]["primary"]["mean"] = None
    elif missing == "control": r["random_control"]["share_of_controls"] = None
    else: r["primary"][{"se": "standard_error", "t": "t_stat", "p": "p_one_sided", "mean": "mean"}[missing]] = None
    result = ch004.judge_variants(results, ch004._registration(repo).fields, 2)
    assert result["e_anchor"]["verdict"] == "insufficient"
    assert result["e_anchor"]["judgement_inputs"]["p_for_holm"] == 1.
    assert result["e_runup"]["holm_p"] == .02


@pytest.mark.parametrize("s,o,available,expected", [("pass", "pass", True, "pass"), ("pass", "fail", True, "version_sensitive"),
    ("fail", "pass", True, "version_sensitive"), ("insufficient", "fail", True, "insufficient"),
    ("delisting_sensitive", "pass", True, "delisting_sensitive"), ("pass", "sensitivity_unavailable", False, "sensitivity_unavailable"),
    ("data_incomplete", "data_incomplete", False, "data_incomplete")])
def test_combination_matches_EV001_precedence(s, o, available, expected):
    assert ch004.av001.combine_verdict(s, o, sensitivity_available=available) == expected


def test_delisting_loss_reprices_x_and_reruns_controls_Holm_to_change_verdict(repo, monkeypatch, synthetic_ev001_source):
    # Removing a consistent loser passes; loss of a retained benchmark-relative
    # event changes the random controls while the actual d remains fixed.
    rows = []
    for i, month in enumerate(pd.period_range("2020-01", periods=40, freq="M")):
        for j in range(10):
            r = chart_row(str(month), str(j), -.02 * (1 + i % 3 / 10) if j == 0 else .005, removed=j == 0)
            if j == 1:
                for key in list(r):
                    if key.startswith("excess") and key.endswith("loss"): r[key] = -1.
            rows.append(r)
    rules = ch004.judge_variants({v: ch004.variant_metrics(rows, v, diagnostics=False) for v in ch004.VARIANTS}, ch004._registration(repo).fields, 2)
    losses = ch004.judge_variants({v: ch004.variant_metrics(rows, v, loss=True, diagnostics=False) for v in ch004.VARIANTS}, ch004._registration(repo).fields, 2)
    hc003.apply_delisting_verdict(rules, losses)
    for v in ch004.VARIANTS:
        assert rules[v]["base_verdict"] == "pass" and losses[v]["verdict"] == "fail"
        assert rules[v]["verdict"] == "delisting_sensitive"
        assert rules[v]["random_control"]["draws_sha256"] == losses[v]["random_control"]["draws_sha256"]
        assert rules[v]["primary"] == losses[v]["primary"]
        assert rules[v]["random_control"]["share_of_controls"] < losses[v]["random_control"]["share_of_controls"]


@pytest.mark.parametrize("complete,available,status", [(False, True, "data_incomplete"), (True, False, "sensitivity_unavailable")])
def test_incomplete_and_original_sensitivity_statuses(repo, complete, available, status, synthetic_ev001_source):
    book, _ = book_for(repo, days=calendar("2025-01-01", "2025-12-30"), codes=["000010", "000020"])
    prepared = prepared_metadata(prepared_for(book, [event()]), complete=complete, available=available)
    result = ch004.evaluate(prepared, ch004._registration(repo).fields, 2)
    assert all(r["verdict"] == status for r in result["variants"].values())
    assert all(r["judgement_inputs"]["p_for_holm"] == 1. for r in result["rules"]["O"].values())
    assert "O" not in result["assertions"]
    if not complete: assert not result["frozen_baselines"]


def test_incomplete_listing_stops_before_EV001_preparation_and_records_two_final_rows(complete_inputs, monkeypatch):
    data, root = complete_inputs["data_dir"], complete_inputs["repo_root"]
    # Remove only the fixture checkpoint; the original EV001 reason must be
    # propagated even if no financial/price preparation can then be performed.
    (data / "disclosures/dart_full/list_supplement/_state.json").unlink()
    monkeypatch.setattr(ev001, "prepare", lambda **k: pytest.fail("prepared incomplete data"))
    monkeypatch.setattr(ch004, "freeze_baselines", lambda *a: pytest.fail("evaluated incomplete data"))
    trap_opens(monkeypatch, data)
    result = ch004.run_experiment(**complete_inputs)
    assert result["preparation_status"] == "data_incomplete"
    assert not result["listing_status"]["supplement_complete"]
    assert all(r["verdict"] == "data_incomplete" for r in result["variants"].values())
    rows = hc003.registry_rows(root)
    assert len(rows) == 2 and all(r["verdict"] == "data_incomplete" for r in rows)
    plan = ch004.dry_run(**complete_inputs)
    assert plan["status"] == "data_incomplete" and not plan["x_i_evaluated"]
    assert plan["projection"]["status"] == "unavailable"


def test_complete_fixture_replay_all_secondary_metrics_and_no_protected_price_opens(complete_inputs, monkeypatch):
    kwargs = complete_inputs
    opened = trap_opens(monkeypatch, kwargs["data_dir"])
    attempt = {}
    result = ch004.compute_design(**kwargs, _attempt=attempt)
    second = ch004.compute_design(**kwargs)
    assert result["replay_sha256"] == second["replay_sha256"]
    assert result["source_hashes"] == attempt["source_hashes"]
    assert result["ev001_code_sha256"] == hc003._digest(ev001.__file__)
    assert result["ev001_code_sha256"] == result["source_hashes"]["code"]["backtesting/experiments/ev001.py"]
    assert set(result["source_hashes"]["preregistrations"]) >= set(ch004.REFERENCE_HASHES)
    assert not result["source_hashes"]["prices"]  # Supplied frame is hashed instead.
    assert result["source_hashes"]["in_memory_prices"]
    assert result["integrity_price_access"]["protected_2026_frozen_hashes_only"]
    for rule in ch004.RULES:
        assert result["assertions"][rule]["ev_new20_exact"]
        assert result["assertions"][rule]["frozen_sha256"] == hc003._content_hash(result["frozen_baselines"][rule])
        assert "charts" not in result["frozen_baselines"][rule]["events"][0]
        assert result["chart_events"][rule][0]["charts"]
        assert result["frozen_universes"][rule] == result["frozen_baselines"][rule]["universes"]
        assert result["filter_overlap"][rule]["CH001_C_anchor_relationship"]
        assert set(result["gross_excess_paths"][rule]["paths"]) == {"ev_new20", *ch004.VARIANTS}
        for v in ch004.VARIANTS:
            r = result["rules"][rule][v]
            assert r["event_count"] == 1 and r["verdict"] == "insufficient"
            assert {"excess_levels", "filter_counts", "by_year", "markets", "short_sale_regimes", "liquidity_buckets", "cost_model_v1"} <= set(r)
            assert set(r["cost_sensitivity"]) == {"1.0", "1.5", "2.0"}
            assert not r["validation_sign_used_for_judgement"]
    assert opened
    json.dumps(result, allow_nan=False)
    assert not (kwargs["repo_root"] / experiment_registry.REGISTRY_PATH).exists()
    assert not (kwargs["repo_root"] / holdout.LEDGER_PATH).exists()


def test_dry_path_on_complete_fixture_never_evaluates_outcomes_or_publishes(complete_inputs, monkeypatch, capsys):
    trap_opens(monkeypatch, complete_inputs["data_dir"])
    monkeypatch.setattr(ev001.EventPrices, "outcomes", lambda *a, **k: pytest.fail("real/supplied event outcome in dry run"))
    # The timing routine is separately tested on synthetic arrays below.
    monkeypatch.setattr(ch004, "fixture_timing", lambda: {"stocks": 64, "entry_dates": 12, "event_months": 12,
        "paths": 200, "outcome_seconds": .5, "gross_paths_seconds": .1, "control_seconds": .02})
    plan = ch004.dry_run(**complete_inputs)
    assert not plan["x_i_evaluated"] and plan["read_end"] == "2025-12-30"
    assert plan["event_counts"]["S"]["total"]["selected"] == 1
    assert plan["projection"]["full_run_seconds_range"][1] > 0
    monkeypatch.setattr(ch004, "dry_run", lambda **k: plan)
    monkeypatch.setattr(ch004, "run_experiment", lambda **k: pytest.fail("default executed"))
    assert ch004.main([]) == 0 and '"x_i_evaluated": false' in capsys.readouterr().out
    with pytest.raises(SystemExit): ch004.main(["--holdout"])
    with pytest.raises(SystemExit): ch004.main(["--dry-run", "--execute"])
    assert not (complete_inputs["repo_root"] / experiment_registry.REGISTRY_PATH).exists()
    assert not list(complete_inputs["repo_root"].rglob("results/*"))


def test_timing_is_synthetic_and_exercises_full_200_control_paths():
    timing = ch004.fixture_timing()
    assert timing["paths"] == 200 and timing["stocks"] == 64
    assert all(timing[k] > 0 for k in ("chart_seconds", "outcome_seconds", "gross_paths_seconds", "control_seconds", "book_array_mib"))


@pytest.mark.parametrize("phase", ["pre_hash", "prepare", "evaluate", "publish"])
def test_failures_before_after_freeze_record_zero_or_two_attempts(complete_inputs, monkeypatch, phase):
    root = complete_inputs["repo_root"]
    registration = experiment_registry.verify_preregistration(ch004.EXPERIMENT_ID, root)
    trap_opens(monkeypatch, complete_inputs["data_dir"])
    target = {"pre_hash": "source_hashes", "prepare": "prepare_inputs", "evaluate": "evaluate", "publish": "publish"}[phase]
    def failed(*a, **k): raise RuntimeError("injected " + phase)
    monkeypatch.setattr(ch004, target, failed)
    with pytest.raises(RuntimeError, match="injected"):
        ch004.run_experiment(**complete_inputs)
    if phase == "pre_hash":
        assert not (root / experiment_registry.REGISTRY_PATH).exists()
    else:
        rows = hc003.registry_rows(root)
        assert [r["variant"] for r in rows] == list(ch004.VARIANTS)
        assert all(r["verdict"] == "design_aborted" for r in rows)
        assert all(r["prereg_commit"] == registration.commit_hash for r in rows)
        metrics = [json.loads(r["metrics_json"]) for r in rows]
        assert len({m["run_id"] for m in metrics}) == 1
        assert all(m["input_hashes"]["code"] and m["failure_location"]["function"] == "failed" for m in metrics)
        assert experiment_registry.trial_count(ch004.EXPERIMENT_ID, repo_root=root) == 2
    assert not list(root.rglob("results/*"))


@pytest.mark.parametrize("kind", ["financial", "documentation", "in_memory_prices", "preregistration"])
def test_input_mutation_after_hash_freeze_aborts_before_publication(complete_inputs, monkeypatch, kind):
    registration = experiment_registry.verify_preregistration(ch004.EXPERIMENT_ID, complete_inputs["repo_root"])
    original = ch004.evaluate
    if kind == "financial": path = next((complete_inputs["data_dir"] / "fundamentals/dart_quarterly").glob("*.jsonl"))
    elif kind == "preregistration": path = complete_inputs["repo_root"] / registration.path
    else: path = complete_inputs["repo_root"] / "research/experiments/DATA_ISSUES.md"
    initial = ch004.source_hashes(complete_inputs["data_dir"], repo_root=complete_inputs["repo_root"], prices=complete_inputs["prices"])
    def evaluate(*a, **k):
        result = original(*a, **k)
        if kind == "in_memory_prices": complete_inputs["prices"].iloc[0, 0] += 1.
        else: path.write_text(path.read_text() + "\n")
        return result
    monkeypatch.setattr(ch004, "evaluate", evaluate)
    monkeypatch.setattr(ch004, "publish", lambda *a, **k: pytest.fail("published changed inputs"))
    with pytest.raises(ValueError, match="inputs or code changed"):
        ch004.run_experiment(**complete_inputs)
    rows = hc003.registry_rows(complete_inputs["repo_root"])
    assert len(rows) == 2 and all(r["verdict"] == "design_aborted" for r in rows)
    assert all(r["prereg_commit"] == registration.commit_hash for r in rows)
    if kind == "preregistration":
        with pytest.raises(ValueError, match="preregistration git check failed"):
            experiment_registry.verify_preregistration(ch004.EXPERIMENT_ID, complete_inputs["repo_root"])
    assert json.loads(rows[0]["metrics_json"])["input_hashes"]["financials"] == hashlib.sha256(json.dumps(initial["financials"], sort_keys=True).encode()).hexdigest()


def test_file_hash_inventory_is_bounded_and_reinventoried(complete_inputs, monkeypatch):
    data, root = complete_inputs["data_dir"], complete_inputs["repo_root"]
    with monkeypatch.context() as trap:
        trap_opens(trap, data)
        first = ch004.source_hashes(data, repo_root=root)
    assert set(first["prices"]) == {"market/krx_daily/2025/20251230.jsonl"}
    # Metadata only can inventory protected paths; no protected content hashes.
    assert "market/krx_daily/2026/20260102.jsonl" not in first["prices"]
    write_rows(data / "market/krx_daily/2025/20251229.jsonl", [])
    trap_opens(monkeypatch, data)
    assert first != ch004.source_hashes(data, repo_root=root)


def test_file_backed_complete_pipeline_and_dry_run_under_protected_open_trap(complete_inputs, monkeypatch):
    kwargs = complete_inputs.copy()
    prices = kwargs.pop("prices")
    data = kwargs["data_dir"]
    for day, frame in prices.groupby(level="trade_date", sort=True):
        rows = frame.reset_index().drop(columns="ret_1d").assign(change_rate_pct=frame.ret_1d.to_numpy() * 100)
        path = data / "market/krx_daily" / str(day.year) / (day.strftime("%Y%m%d") + ".jsonl")
        write_rows(path, rows.assign(trade_date=str(day.date())).to_dict("records"))
    checkpoint(data)  # Freeze the complete synthetic inventory before trapping.
    opened = trap_opens(monkeypatch, data)
    result = ch004.compute_design(**kwargs)
    assert all(result["assertions"][r]["ev_new20_exact"] for r in ch004.RULES)
    assert len(result["source_hashes"]["prices"]) == len(kwargs["sessions"])
    assert any("20251230.jsonl" in path for path in opened)
    assert not any("2026/" in path for path in opened)
    # This also exercises the real dry-run timing routine on synthetic prices.
    plan = ch004.dry_run(**kwargs)
    assert not plan["x_i_evaluated"] and plan["fixture_timing"]["paths"] == 200
    assert not list(kwargs["repo_root"].rglob("results/*"))


def test_all_EV001_replays_finish_before_first_chart_and_mismatch_stops(repo, monkeypatch, synthetic_ev001_source):
    book, _ = book_for(repo, days=calendar("2025-01-01", "2025-12-30"), codes=["000010", "000020"])
    prepared = prepared_metadata(prepared_for(book, [event()]))
    baseline, chart = ch004.ev001_baseline, ch004.attach_filters
    calls = []
    def replay(prepared, rule):
        calls.append(rule)
        return baseline(prepared, rule)
    def charts(*args):
        assert calls == ["S", "O", "S", "O"]
        return chart(*args)
    monkeypatch.setattr(ch004, "ev001_baseline", replay)
    monkeypatch.setattr(ch004, "attach_filters", charts)
    ch004.evaluate(prepared, ch004._registration(repo).fields, 2)
    calls.clear()
    def mismatch(prepared, rule):
        rows, counts = replay(prepared, rule)
        if calls == ["S", "O", "S"]: rows[0]["excess_v2_1.0"] += 1e-12
        return rows, counts
    monkeypatch.setattr(ch004, "ev001_baseline", mismatch)
    monkeypatch.setattr(ch004, "attach_filters", lambda *a: pytest.fail("chart evaluated before equality assertion"))
    with pytest.raises(AssertionError, match="mismatch"):
        ch004.evaluate(prepared, ch004._registration(repo).fields, 2)


def test_secondary_gross_paths_preserve_whole_benchmark_and_only_four_horizons(repo):
    days = calendar("2024-01-01", "2025-12-30")
    prices = prices_for(days)
    # Only a benchmark member is unsellable on day 1, with a valid later exit.
    prices.loc[(pd.Timestamp("2025-05-16"), "000020"), "close"] = 7000.
    book, _ = book_for(repo, days=days, prices=prices)
    prepared = prepared_metadata(prepared_for(book, [event()]))
    frozen, _ = ch004.freeze_baselines(prepared)
    rows = frozen["S"]["events"]
    ch004.attach_filters(prepared, "S", rows)
    result = ch004.gross_paths(prepared, "S", rows)
    assert set(result["paths"]["ev_new20"]) == {"1", "5", "10", "20"}
    assert result["paths"]["ev_new20"]["1"]["event_count"] == 0
    assert "1" not in rows[0]["gross_excess_path"]
    assert result["unavailable"][0]["horizon"] == 1
    assert result["unavailable"][0]["members"][0]["stock_code"] == "000020"
    assert result["paths"]["ev_new20"]["20"]["event_count"] == 1
    assert result["paths"]["ev_new20"]["20"]["primary"]["mean"] == rows[0]["gross"] - rows[0]["benchmark"]


def test_count_judgement_publication_under_one_lock_two_finite_rows(complete_inputs, monkeypatch):
    root = complete_inputs["repo_root"]
    for _ in range(19):
        experiment_registry.record_trial(ch004.EXPERIMENT_ID, "prior", {}, "design_aborted", repo_root=root)
    original_lock, original_count, original_record = ordering.execution_lock, experiment_registry.trial_count, experiment_registry.record_trial
    held, counts, appends = [], [], []
    @contextmanager
    def lock(repo_root):
        with original_lock(repo_root):
            held.append(repo_root)
            try: yield
            finally: held.pop()
    def count(*a, **k):
        assert held == [root]
        counts.append(a[0])
        return original_count(*a, **k)
    def record(*a, **k):
        assert held == [root]
        appends.append(a[1])
        return original_record(*a, **k)
    monkeypatch.setattr(ordering, "execution_lock", lock)
    monkeypatch.setattr(experiment_registry, "trial_count", count)
    monkeypatch.setattr(experiment_registry, "record_trial", record)
    result = ch004.run_experiment(**complete_inputs)
    assert counts == [ch004.EXPERIMENT_ID] and appends == list(ch004.VARIANTS)
    assert result["trial_count_after_run"] == 21
    assert all(r["judgement_inputs"]["t_threshold"] == 3 for rules in result["rules"].values() for r in rules.values())
    rows = hc003.registry_rows(root)
    assert len(rows) == 21
    for row in rows[-2:]:
        metrics = json.loads(row["metrics_json"])
        assert (root / metrics["result_file"]).is_file()
        assert len(row["metrics_json"].encode()) < experiment_registry.MAX_METRICS_BYTES
    with original_lock(root):
        with pytest.raises(ValueError, match="another research"):
            ch004.run_experiment(**complete_inputs)
    assert len(hc003.registry_rows(root)) == 21


def test_validate_both_rows_before_any_publication(complete_inputs, monkeypatch):
    payload = ch004.compute_design(**complete_inputs)
    payload["rules"]["O"]["e_runup"]["primary"]["mean"] = float("nan")
    monkeypatch.setattr(ch004.common, "write_results", lambda *a, **k: pytest.fail("wrote before validating all rows"))
    with pytest.raises(ValueError, match="finite JSON"):
        ch004.publish(payload, repo_root=complete_inputs["repo_root"])
    assert not (complete_inputs["repo_root"] / experiment_registry.REGISTRY_PATH).exists()
