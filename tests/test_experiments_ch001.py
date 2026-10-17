"""Hand-computed CH001 rules and offline accounting/publication fixtures."""
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry, holdout
from backtesting.experiments import av001, ch001, common, d001, hc002, hf001
from src.ingestion import dart_listing
from src.ingestion.storage import write_rows
from dart_listing_fixtures import checkpoint
from preregistration_fixtures import committed_preregistrations
from test_dart_financial_date_rules import filing, save, version


def market(days=None, codes=("000010",), returns=0.):
    days = pd.bdate_range("2023-01-02", periods=300) if days is None else days
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    return pd.DataFrame({"stock_name": "fixture", "market": "KOSPI", "calendar_status": "verified",
        "open": 100., "high": 110., "low": 90., "close": 100., "base_price": 100.,
        "volume": 100., "trading_value": 2e9, "ret_1d": returns}, index=index), days


def set_index_path(prices, days, code, values):
    prices.loc[(days, code), "ret_1d"] = np.r_[0., np.asarray(values)[1:] / np.asarray(values)[:-1] - 1]


def selection(day, codes, *, phases=None, f_vol=(), f_raise=(), removed=(), evaluable=None):
    day = pd.Timestamp(day)
    u = pd.DataFrame({"phase": phases or [2] * len(codes), "avg_trading_value_20d": 2e9},
                     index=pd.Index(codes, name="stock_code"))
    row = {"month": str(day.to_period("M")), "decision_date": day.date().isoformat(),
        "trade_date": (day + pd.offsets.BDay()).date().isoformat(), "universe": u,
        "markets": pd.Series("KOSPI", index=u.index), "F_vol": set(f_vol), "F_raise": set(f_raise)}
    row["P"], row["B"] = ch001.baseline_set(row)
    evaluable = set(codes) if evaluable is None else set(evaluable)
    row["charts"] = {name: pd.DataFrame({"computable": [c in evaluable for c in codes],
        "removed": [c in removed for c in codes], "reason": ["" if c in evaluable else "missing_session" for c in codes]}, index=u.index)
        for name in ch001.VARIANTS}
    return row


def observations(rows, nets=None):
    records = []
    for row in rows:
        for pos, code in enumerate(row["universe"].index):
            records.append({"stock_code": code, "decision_date": pd.Timestamp(row["decision_date"]),
                "trade_date": pd.Timestamp(row["trade_date"]), "filled": True, "gross": (nets or [.1, -.1, .2, 0.])[pos],
                "gross_delisting_loss": (nets or [.1, -.1, .2, 0.])[pos], "decision_market": "KOSPI",
                "bucket": 2, "avg_trading_value_20d": 2e9, "phase": row["universe"].loc[code, "phase"],
                **{f"cost_{m}": .01 * m for m in ch001.MULTIPLIERS}})
    return pd.DataFrame(records, columns=["stock_code", "decision_date", "trade_date", "filled", "gross",
        "gross_delisting_loss", "decision_market", "bucket", "avg_trading_value_20d", "phase",
        *[f"cost_{m}" for m in ch001.MULTIPLIERS]])


@pytest.fixture
def fields():
    return experiment_registry.verify_preregistration(ch001.EXPERIMENT_ID).fields


def test_registration_is_committed_fixed_plan_and_no_holdout():
    registered = ch001._registration(experiment_registry.PROJECT_ROOT)
    assert registered.commit_hash.startswith("b53485e")
    assert registered.fields["variants_planned"] == 3
    assert registered.fields["uses_holdout"] is False
    assert hf001._digest(experiment_registry.PROJECT_ROOT / "research/experiments" / ch001.EXPERIMENT_ID / "preregistration.md") == "80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f"


@pytest.mark.parametrize("name", [".env", ".env-ai"])
def test_environment_preflight_stops_before_any_credential_file_read(tmp_path, monkeypatch, name):
    (tmp_path / name).touch()
    monkeypatch.setattr(ch001, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("preflight opened a file"))
    with pytest.raises(ValueError, match="no environment file was read"):
        ch001._registration(tmp_path)


def test_calendar_anchor_window_reaches_2016_and_horizon_is_hf001():
    sessions, plan = ch001.calendar_plan()
    assert len(plan) == 103 and sessions[0].year == 2016 and sessions[-1].year == 2025
    for row in plan:
        decision = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        assert sessions[decision + 1] == pd.Timestamp(row["trade_date"])
        assert sessions[decision + 20] == pd.Timestamp(row["exit_date"])
    assert sessions[251] == pd.Timestamp(plan[0]["decision_date"])


def test_calendar_does_not_drop_months_or_extend_short_windows():
    with pytest.raises(ValueError, match="cannot drop"):
        ch001.calendar_plan(pd.bdate_range("2024-01-01", "2025-12-30"))


def test_loader_latest_saved_A_B_A_preserves_ohlcv_and_matches_d001(tmp_path):
    day = "2024-05-31"
    base = {"stock_code": "000010", "stock_name": "fixture", "trade_date": day,
        "market": "KOSPI", "calendar_status": "verified", "bar_at": day + "T15:30:00+09:00",
        "open": "100", "high": "120", "low": "90", "close": "110", "base_price": "100",
        "volume": "200", "trading_value": "22000", "change_rate_pct": "10"}
    path = tmp_path / "market/krx_daily/2024/20240531.jsonl"
    write_rows(path, [base, {**base, "high": "150", "change_rate_pct": "20"}, base])
    actual, files = ch001.load_prices(tmp_path, day, day)
    expected = d001._load_prices(tmp_path / "market/krx_daily", day, day)
    pd.testing.assert_frame_equal(actual[list(expected.columns)], expected)
    assert actual.iloc[0].high == 120 and actual.iloc[0].low == 90 and actual.iloc[0].ret_1d == .1
    assert files == [(pd.Timestamp(day), path)]
    del base["base_price"]
    write_rows(path, [base])
    assert np.isnan(ch001.load_prices(tmp_path, day, day)[0].iloc[0].base_price)


def test_price_guard_rejects_2026_before_any_file_open(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("forbidden file access"))
    for end in ("2026-01-02", "2026-07-01"):
        with pytest.raises(ValueError, match="2025-12-31"):
            ch001.load_prices(tmp_path, "2025-12-01", end)
        with pytest.raises(ValueError, match="2025-12-31"):
            ch001.source_hashes(tmp_path, [(pd.Timestamp(end), tmp_path / "forbidden.jsonl")])


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf, -1., -1.1])
@pytest.mark.parametrize("position", [0, 59])
def test_heat_requires_every_return_including_first_day(bad, position):
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=60), returns=.01)
    prices.loc[(days[position], "000010"), "ret_1d"] = bad
    row = ch001.heat_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert not row.computable and not row.removed


def test_heat_wilder_first_14_deltas_then_exactly_45_updates_hand_computed():
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=60))
    # 13 gains then 46 losses: seed g=13/14,l=1/14; 45 later losses.
    values = np.r_[100., 100 + np.cumsum(np.r_[np.ones(13), -np.ones(46)])]
    set_index_path(prices, days, "000010", values)
    row = ch001.heat_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert row.rsi == pytest.approx(100 * (13 / 14) ** 46, abs=1e-10)
    assert row.computable and not row.removed


@pytest.mark.parametrize("returns,rsi,computable,removed", [(.01, 100., True, True), (-.01, 0., True, False), (0., None, False, False)])
def test_heat_no_losses_no_gains_and_flat(returns, rsi, computable, removed):
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=60), returns=returns)
    prices["volume"] = 0.
    row = ch001.heat_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert bool(row.computable) == computable and bool(row.removed) == removed
    assert np.isnan(row.rsi) if rsi is None else row.rsi == pytest.approx(rsi)
    if not computable:
        assert row.reason == "flat_index"


def test_heat_inclusive_threshold_and_boundary_count(monkeypatch):
    # Constant gains/losses seed ratio 7:3, then zero deltas preserve the ratio.
    index = np.r_[100., 100 + np.cumsum(np.r_[np.repeat(35., 7), np.repeat(-15., 7), np.zeros(45)])]
    monkeypatch.setattr(ch001, "adjusted_index", lambda *a: (index[:, None], np.array([""], dtype=object)))
    result = ch001.heat_filter(None, ["000010"], None, None)
    assert result.iloc[0].rsi == 70.
    assert result.iloc[0].removed
    assert ch001.filter_counts(result, ["000010"])["boundary_rsi_69_71"] == 1


@pytest.mark.parametrize("problem", ["missing", "unverified", "short"])
def test_heat_contiguous_calendar_no_fill_or_skip(problem):
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=61), returns=.01)
    if problem == "missing":
        prices = prices.drop((days[20], "000010"))
    elif problem == "unverified":
        prices.loc[(days[20], "000010"), "calendar_status"] = "unverified"
    else:
        prices, days = prices.iloc[:59], days[:59]
    row = ch001.heat_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert not row.computable and not row.removed


def wick_fixture():
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=26))
    prices.loc[(days[-1], "000010"), "volume"] = 200.
    return prices, days


def test_wick_exact_half_wick_twice_prior_mean_and_common_return_override():
    prices, days = wick_fixture()
    prices["ret_1d"], prices["calendar_status"] = np.nan, "unverified"
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert row.computable and row.removed and row.valid_candidate_days == 5 and row.spike_days == 1
    assert not row.partial_uncomputable


@pytest.mark.parametrize("field,value", [("open", 111.), ("close", 89.), ("high", 90.),
    ("low", 101.), ("high", np.inf), ("low", 0.), ("open", np.nan), ("volume", 0.), ("volume", np.nan)])
def test_wick_invalid_candidate_ohlc_order_range_or_volume(field, value):
    prices, days = wick_fixture()
    prices.loc[(days[-1], "000010"), field] = value
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert row.computable and not row.removed and row.partial_uncomputable and row.valid_candidate_days == 4


@pytest.mark.parametrize("field,value,reason", [("base_price", np.nan, "invalid_base"),
    ("base_price", 0., "invalid_base"), ("base_price", 50., "corporate_action"),
    ("base_price", 100.51, "corporate_action"), ("close", np.nan, "invalid_previous_close")])
def test_wick_guard_covers_all_21_days_no_missing_base_substitution(field, value, reason):
    prices, days = wick_fixture()
    prices.loc[(days[20], "000010"), field] = value
    result = ch001.wick_filter(prices, ["000010"], days[-1], days)
    row = result.iloc[0]
    assert not row.computable and not row.removed and not row.partial_uncomputable
    assert row.invalid_candidate_reasons[reason] == 5
    counts = ch001.filter_counts(result, ["000010"])
    assert counts["uncomputable_by_reason"][reason] == 1
    assert counts["invalid_candidates_by_reason"][reason] == 5


def test_wick_005_guard_boundary_and_valid_zero_prior_volume():
    prices, days = wick_fixture()
    prices.loc[(days[10], "000010"), "volume"] = 0.
    prices.loc[(days[10], "000010"), "base_price"] = 100.5
    prices.loc[(days[-1], "000010"), "volume"] = 190.  # 2 * ((19*100+0)/20)
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert row.computable and row.removed and row.valid_candidate_days == 5


@pytest.mark.parametrize("base,accepted", [(10050., True), (9950., True),
    (10050.000001, False), (9949.999999, False)])
def test_wick_corporate_action_both_exact_boundaries_and_just_outside(base, accepted):
    prices, days = wick_fixture()
    prices[["open", "close", "base_price"]] = 10000.
    prices.high, prices.low = 11000., 9000.
    prices.loc[(days[10], "000010"), "base_price"] = base
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert bool(row.computable) == accepted
    assert row.valid_candidate_days == (5 if accepted else 0)
    assert row.invalid_candidate_reasons.get("corporate_action", 0) == (0 if accepted else 5)


def test_wick_guard_ratio_overflow_cannot_make_a_candidate_valid():
    prices, days = wick_fixture()
    prices.loc[(days[10], "000010"), "base_price"] = 1e308
    prices.loc[(days[9], "000010"), "close"] = 1e-308
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert not row.computable and not row.removed
    assert row.invalid_candidate_reasons["corporate_action"] == 5


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1.])
def test_wick_prior_volume_cannot_drop_bad_observations(bad):
    prices, days = wick_fixture()
    prices.loc[(days[10], "000010"), "volume"] = bad
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert not row.computable and not row.removed
    assert row.invalid_candidate_reasons["invalid_prior_volume"] == 5


def test_wick_zero_prior_mean_is_uncomputable():
    prices, days = wick_fixture()
    prices.loc[(days[:-1], "000010"), "volume"] = 0.
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert not row.computable and not row.removed
    assert row.invalid_candidate_reasons["nonpositive_mean_volume"] == 5


def test_wick_partial_uncomputable_spike_removes_and_is_in_E():
    prices, days = wick_fixture()
    prices.loc[(days[1], "000010"), "base_price"] = np.nan
    result = ch001.wick_filter(prices, ["000010"], days[-1], days)
    row = result.iloc[0]
    assert row.computable and row.removed and row.partial_uncomputable and row.valid_candidate_days == 4
    s = selection(days[-1], ["000010"])
    s["charts"]["ch_wick"] = result
    monthly = ch001.monthly(observations([s]), [s], "ch_wick")[0]
    assert monthly["E"] == monthly["removed"] == ["000010"]
    assert monthly["counts"]["partial_uncomputable"] == 1


def test_wick_earliest_candidate_needs_26_sessions_and_short_history_can_be_partial():
    prices, days = wick_fixture()
    prices["volume"] = 100.
    prices.loc[(days[21], "000010"), "volume"] = 200.
    assert ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0].removed
    prices = prices.drop((days[0], "000010"))
    row = ch001.wick_filter(prices, ["000010"], days[-1], days).iloc[0]
    assert row.valid_candidate_days == 4 and row.partial_uncomputable and not row.removed
    row = ch001.wick_filter(prices, ["000010"], days[20], days).iloc[0]
    assert not row.computable and not row.removed


def anchor_fixture():
    codes = [f"{i * 10:06d}" for i in range(1, 13)]
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=252), codes=codes)
    for code, ratio in zip(codes, [i / 10 for i in range(1, 11)] + [.1, .2]):
        prices.loc[(days[-1], code), "ret_1d"] = ratio - 1
    markets = pd.Series(["KOSPI"] * 10 + ["KOSDAQ"] * 2, index=codes)
    return prices, days, codes, markets


def test_anchor_hand_ratios_market_specific_full_U_and_inclusive_30pct():
    prices, days, codes, markets = anchor_fixture()
    result = ch001.anchor_filter(prices, codes, markets, days[-1], days)
    assert result.R.to_numpy() == pytest.approx([i / 10 for i in range(1, 11)] + [.1, .2])
    assert result.index[result.removed].tolist() == codes[:3]
    assert result.loc[codes[2], "percentile"] == .3
    assert result.loc[codes[-2], "percentile"] == .5
    assert ch001.filter_counts(result, codes)["boundary_percentile_029_031"] == 1
    s = selection(days[-1], codes, phases=[4, 4, 2] + [4] * 9)
    s["markets"] = markets
    ch001.attach_filters(prices, [s], days)
    assert s["B"] == {codes[2]} and s["charts"]["ch_anchor"].loc[codes[2], "removed"]


def test_anchor_ties_use_maximum_rank_not_average_or_first():
    prices, days, codes, markets = anchor_fixture()
    prices.loc[(days[-1], codes[3]), "ret_1d"] = -.7
    result = ch001.anchor_filter(prices, codes, markets, days[-1], days)
    assert result.loc[codes[2:4], "percentile"].tolist() == [.4, .4]
    assert result.index[result.removed].tolist() == codes[:2]


@pytest.mark.parametrize("problem", ["first_invalid", "missing", "unverified", "short"])
def test_anchor_every_252_day_valid_and_only_evaluable_U_in_denominator(problem):
    prices, days, codes, markets = anchor_fixture()
    if problem == "first_invalid":
        prices.loc[(days[0], codes[9]), "ret_1d"] = -1.
    elif problem == "missing":
        prices = prices.drop((days[100], codes[9]))
    elif problem == "unverified":
        prices.loc[(days[100], codes[9]), "calendar_status"] = "unverified"
    else:
        prices = prices.loc[prices.index.get_level_values("trade_date") != days[0]]
    result = ch001.anchor_filter(prices, codes, markets, days[-1], days)
    assert not result.loc[codes[9], "computable"] and not result.loc[codes[9], "removed"]
    assert result.index[result.removed].tolist() == ([] if problem == "short" else codes[:2])


def test_adjusted_index_overflow_and_underflow_are_uncomputable():
    prices, days = market(days=pd.bdate_range("2024-01-02", periods=252), returns=1e100)
    result = ch001.anchor_filter(prices, ["000010"], pd.Series("KOSPI", index=["000010"]), days[-1], days)
    assert result.iloc[0].reason == "invalid_adjusted_index" and not result.iloc[0].removed
    prices.ret_1d = -1 + 1e-15
    assert not ch001.heat_filter(prices, ["000010"], days[-1], days).iloc[0].computable


def test_B_definition_phase_priority_and_AV001_equivalence_tripwire(monkeypatch):
    s = selection("2024-05-31", ["a", "b", "c", "d"], phases=[2, 2, 3, None], f_vol=["b"], f_raise=["c"])
    assert s["P"] == {"a", "b"} and s["B"] == {"a"}
    monkeypatch.setattr(av001, "target_set", lambda *a: {"a", "d"})
    with pytest.raises(AssertionError, match="B != AV001"):
        ch001.baseline_set(s)


def test_monthly_baseline_is_B_reweight_before_fills_cash_costs_and_empty_B_kept():
    codes = ["a", "b", "c", "d"]
    s = selection("2024-05-31", codes, f_vol=["d"], removed=["b"])
    frame = observations([s])
    frame.loc[frame.stock_code.eq("c"), ["filled", "gross", "gross_delisting_loss", "cost_1.0", "cost_1.5", "cost_2.0"]] = [False, 0., 0., 0., 0., 0.]
    row = ch001.monthly(frame, [s], "ch_heat")[0]
    assert row["B"] == ["a", "b", "c"] and row["remaining"] == ["a", "c"]
    assert row["filtered"]["weights"] == {"a": .5, "c": .5}
    assert row["filtered"]["cash_weight"] == .5 and row["baseline"]["cash_weight"] == pytest.approx(1 / 3)
    assert row["costs"]["1.0"]["baseline_net"] == pytest.approx(-.02 / 3)
    assert row["costs"]["1.0"]["filtered_net"] == pytest.approx(.09 / 2)
    assert row["costs"]["1.5"]["filtered_cost"] == pytest.approx(.015 / 2)
    assert row["removed_minus_remaining"] == pytest.approx(-.11 - .045)
    assert ch001.assert_filters_off(frame, [s])["exact_names_weights_cash_cost_net"]
    empty = selection("2024-06-28", codes, f_raise=codes)
    all_removed = selection("2024-07-31", codes, removed=codes)
    rows = ch001.monthly(observations([empty, all_removed]), [empty, all_removed], "ch_heat")
    assert rows[0]["valid"] and rows[0]["costs"]["1.0"]["difference"] == 0 and rows[0]["baseline_empty"]
    assert rows[1]["valid"] and rows[1]["cash"] and rows[1]["filtered"]["cash_weight"] == 1
    assert rows[1]["costs"]["1.0"]["filtered_net"] == rows[1]["costs"]["1.0"]["filtered_cost"] == 0


def test_off_assertion_stops_on_accounting_mismatch(monkeypatch):
    s = selection("2024-05-31", ["a"])
    frame = observations([s])
    original = ch001.monthly
    def broken(*a, **k):
        rows = original(*a, **k)
        rows[0]["filtered"]["cash_weight"] = .1
        return rows
    monkeypatch.setattr(ch001, "monthly", broken)
    with pytest.raises(AssertionError, match="filters-off baseline"):
        ch001.assert_filters_off(frame, [s])


def test_empty_U_does_not_manufacture_a_valid_zero_month():
    s = selection("2024-05-31", [])
    rows = ch001.monthly(observations([s]), [s], "ch_heat")
    assert not rows[0]["valid"] and ch001.summarize(rows)["primary"]["count"] == 0


def test_forward_is_unchanged_hf001_with_unfilled_delisting_halt_and_limit_down():
    codes = ["000010", "000020", "000030", "000040"]
    prices, days = market(codes=codes)
    s = selection(days[251], codes)
    entry, exit_day = days[252], days[271]
    prices.loc[(entry, codes[0]), "open"] = 130.  # limit-up cash slot
    # A later positive-volume trade identifies a halt; no later trade identifies delisting.
    prices = prices.drop(index=pd.MultiIndex.from_product([days[270:], [codes[1]]]))
    prices.loc[(exit_day, codes[2]), "volume"] = 0.
    prices.loc[(exit_day, codes[3]), "close"] = 70.  # last permissible valuation precedes exit
    actual = ch001.forward_observations(prices, [s], days)
    u = s["universe"][["avg_trading_value_20d", "phase"]].assign(decision_date=pd.Timestamp(s["decision_date"]),
        trade_date=pd.Timestamp(s["trade_date"]), decision_market=s["markets"]).reset_index()
    expected = hf001.forward_observations(prices, u, days)
    pd.testing.assert_frame_equal(actual, expected)
    indexed = actual.set_index("stock_code")
    assert not indexed.loc[codes[0], "filled"] and indexed.loc[codes[0], "cost_1.0"] == 0
    assert indexed.loc[codes[1], "delisted"] and indexed.loc[codes[1], "gross_delisting_loss"] == -1
    assert indexed.loc[codes[2], "halted_exit"] and indexed.loc[codes[3], "limit_down_exit"]
    assert indexed.loc[codes[3], "scheduled_exit_date"] == exit_day


def test_controls_golden_one_rng_path_major_sorted_E_zero_all_and_uncomputables_kept(monkeypatch):
    monkeypatch.setattr(ch001, "N_CONTROLS", 4)
    rows = [selection("2024-05-31", ["d", "c", "b", "a"], removed=["b"], evaluable=["a", "b", "c"]),
            selection("2024-06-28", ["a", "b", "c", "d"], removed=[], evaluable=[]),
            selection("2024-07-31", ["a", "b", "c", "d"], removed=["a", "b"], evaluable=["a", "b"])]
    frame = observations(rows)
    frame.gross_delisting_loss = frame.gross - .1
    monthly = ch001.monthly(frame, rows, "ch_heat")
    original_rng, choices, seeds = np.random.default_rng, [], []
    def tracked_rng(seed):
        seeds.append(seed)
        generator = original_rng(seed)
        def choice(candidates, *, size, replace):
            choices.append((list(candidates), size, replace))
            return generator.choice(candidates, size=size, replace=replace)
        return SimpleNamespace(choice=choice)
    monkeypatch.setattr(np.random, "default_rng", tracked_rng)
    actual = ch001.random_controls(frame, monthly)
    assert seeds == [0] and len(choices) == 12
    assert choices[1] == ([], 0, False) and choices[2] == (["a", "b"], 2, False)
    rng, digest, expected = original_rng(0), hashlib.sha256(), []
    for path in range(4):
        differences = []
        for row in monthly:
            drawn = rng.choice(sorted(row["E"]), size=len(row["removed"]), replace=False).tolist()
            assert "d" not in drawn
            digest.update((json.dumps([path, row["month"], drawn], separators=(",", ":")) + "\n").encode())
            group = frame.loc[frame.decision_date.eq(pd.Timestamp(row["decision_date"])) & ~frame.stock_code.isin(drawn)].sort_values("stock_code")
            differences.append(float((group.gross - group["cost_1.0"]).mean()) - row["costs"]["1.0"]["baseline_net"])
        expected.append(float(np.mean(differences)))
    assert actual["path_means"] == expected and actual["draws_sha256"] == digest.hexdigest()
    loss_months = ch001.monthly(frame, rows, "ch_heat", loss=True)
    assert ch001.random_controls(frame, loss_months, loss=True)["draws_sha256"] == actual["draws_sha256"]
    bad = copy.deepcopy(monthly)
    bad[0]["removed"] = ["d"]
    with pytest.raises(AssertionError, match="subset of E"):
        ch001.random_controls(frame, bad)


def test_control_exact_ties_use_ge_without_plus_one(monkeypatch):
    monkeypatch.setattr(ch001, "N_CONTROLS", 4)
    s = selection("2024-05-31", ["a", "b"], removed=[])
    frame = observations([s])
    control = ch001.random_controls(frame, ch001.monthly(frame, [s], "ch_heat"))
    assert control["share_of_controls"] == 1. and control["path_means"] == [0.] * 4


def test_statistics_nw_bartlett_lag_one_normal_tail_and_holm_three():
    values = np.array([.01, -.02, .03, .04])
    centered = values - values.mean()
    variance = (sum(centered**2) + sum(centered[1:] * centered[:-1])) / 4
    expected_se = math.sqrt(variance / 4)
    result = hf001.newey_west(values)
    assert result["standard_error"] == pytest.approx(expected_se)
    assert result["t_stat"] == pytest.approx(values.mean() / expected_se)
    assert result["p_one_sided"] == pytest.approx(.5 * math.erfc(result["t_stat"] / math.sqrt(2)))
    assert ch001.holm(dict(zip(ch001.VARIANTS, (.01, .04, 1.)))) == dict(zip(ch001.VARIANTS, (.03, .08, 1.)))
    with pytest.raises(ValueError, match="exactly three"):
        ch001.holm({"ch_heat": .01})


def judgement_fixture():
    result = ch001.unavailable_metrics("fixture")
    result.pop("unavailable")
    result.update(primary={"count": 40, "mean": .01, "std": .02, "standard_error": .002,
        "long_run_variance": .00016, "t_stat": 5., "p_one_sided": .000001}, filtered_months=40,
        risk={"filtered": {"volatility": .02, "lower_5pct_mean": -.02},
              "baseline": {"volatility": .03, "lower_5pct_mean": -.03}},
        random_control={"share_of_controls": .05})
    result["cost_sensitivity"]["1.5"]["difference"] = .005
    return {name: copy.deepcopy(result) for name in ch001.VARIANTS}


@pytest.mark.parametrize("trials,t,verdict", [(20, 2., "fail"), (20, 2.001, "pass"), (21, 3., "fail"), (21, 3.001, "pass")])
def test_strict_t_trial_count_including_three_current_rows(fields, trials, t, verdict):
    metrics = judgement_fixture()
    for result in metrics.values():
        result["primary"]["t_stat"] = t
    ch001.judge_variants(metrics, fields, trials)
    assert all(r["verdict"] == verdict for r in metrics.values())


@pytest.mark.parametrize("failure,expected", [("months", "insufficient"), ("removal_months", "insufficient"),
    ("undefined", "insufficient"), ("mean", "risk_only"), ("cost15", "fail"), ("controls", "fail"), ("holm", "fail")])
def test_all_gates_and_insufficient_p_one_fixed_holm_family(fields, failure, expected):
    metrics = judgement_fixture()
    r = metrics["ch_heat"]
    if failure == "months":
        r["primary"]["count"] = 35
    elif failure == "removal_months":
        r["filtered_months"] = 35
    elif failure == "undefined":
        r["primary"]["t_stat"], r["primary"]["standard_error"] = None, 0.
    elif failure == "mean":
        r["primary"]["mean"] = 0.
    elif failure == "cost15":
        r["cost_sensitivity"]["1.5"]["difference"] = 0.
    elif failure == "controls":
        r["random_control"]["share_of_controls"] = .05001
    else:
        for item in metrics.values():
            item["primary"]["p_one_sided"] = .04
    ch001.judge_variants(metrics, fields, 3)
    assert r["verdict"] == expected
    if expected == "insufficient":
        assert r["judgement_inputs"]["p_for_holm"] == r["holm_p"] == 1.
    if failure == "holm":
        assert r["holm_p"] == .12


@pytest.mark.parametrize("s,o,available,expected", [("pass", "pass", True, "pass"), ("pass", "fail", True, "version_sensitive"),
    ("fail", "pass", True, "version_sensitive"), ("pass", "pass", False, "sensitivity_unavailable"),
    ("delisting_sensitive", "pass", True, "delisting_sensitive"), ("insufficient", "fail", True, "insufficient"),
    ("data_incomplete", "data_incomplete", False, "data_incomplete"), ("risk_only", "fail", True, "risk_only")])
def test_combined_rule_verdicts(s, o, available, expected):
    assert av001.combine_verdict(s, o, sensitivity_available=available) == expected


def test_loss_reruns_controls_and_holm_and_changed_verdict_is_delisting_sensitive(fields, monkeypatch):
    calls = []
    def metrics(frame, selections, variant, *, loss=False, diagnostics=True):
        calls.append((variant, loss))
        result = judgement_fixture()[variant]
        result["random_control"]["draws_sha256"] = "same-draws"
        if loss:
            result["cost_sensitivity"]["1.5"]["difference"] = -.01
        return result
    monkeypatch.setattr(ch001, "variant_metrics", metrics)
    main, loss = ch001.evaluate_rule(None, None, fields, 3)
    assert len(calls) == 6 and all(r["verdict"] == "delisting_sensitive" for r in main.values())
    assert all(r["base_verdict"] == "pass" and r["delisting_loss_verdict"] == "fail" for r in main.values())
    assert all("holm_p" in r for r in loss.values())


def test_one_delisting_flows_through_monthly_controls_holm_and_combined_verdict(fields):
    selections = [selection(day, ["a", "b"], removed=["a"])
                  for day in pd.date_range("2020-01-31", periods=40, freq="ME")]
    frame = observations(selections, nets=[.008, .01])
    # The rejected name is slightly worse in every month. Only b in the last
    # cohort loses its last-close valuation under the delisting convention.
    frame.loc[frame.stock_code.eq("a"), "gross"] += np.resize([-.0002, .0002], 40)
    frame["gross_delisting_loss"] = frame.gross
    frame.loc[frame.index[-1], "gross_delisting_loss"] = -1.
    assert (frame.gross_delisting_loss != frame.gross).sum() == 1
    main, losses = ch001.evaluate_rule(frame, selections, fields, 3)
    expected_d = np.resize([.0011, .0009], 40)
    expected_loss_d = expected_d.copy()
    expected_loss_d[-1] -= 1.01 / 2
    rng, controls, loss_controls = np.random.default_rng(0), [], []
    for _ in range(ch001.N_CONTROLS):
        drawn = [rng.choice(["a", "b"], size=1, replace=False)[0] for _ in selections]
        values = np.where(np.asarray(drawn) == "a", expected_d, -expected_d)
        controls.append(values.mean())
        # Removing b avoids the loss; removing a retains b at -100%.
        loss_controls.append(values.mean() + (1 if drawn[-1] == "b" else -1) * 1.01 / 2 / 40)
    assert any(a > b for a, b in zip(loss_controls, controls))
    assert any(a < b for a, b in zip(loss_controls, controls))
    for variant in ch001.VARIANTS:
        result, loss = main[variant], losses[variant]
        assert [r["costs"]["1.0"]["difference"] for r in result["per_month"]] == pytest.approx(expected_d)
        assert [r["costs"]["1.0"]["difference"] for r in loss["per_month"]] == pytest.approx(expected_loss_d)
        assert result["random_control"]["path_means"] == pytest.approx(controls)
        assert loss["random_control"]["path_means"] == pytest.approx(loss_controls)
        assert result["random_control"]["draws_sha256"] == loss["random_control"]["draws_sha256"]
        assert result["holm_p"] == pytest.approx(min(3 * result["primary"]["p_one_sided"], 1.))
        assert loss["holm_p"] == pytest.approx(min(3 * loss["primary"]["p_one_sided"], 1.))
        assert result["holm_p"] <= .05 < loss["holm_p"]
        assert result["base_verdict"] == "pass" and loss["verdict"] == "fail"
        assert result["verdict"] == av001.combine_verdict(result["verdict"], result["verdict"]) == "delisting_sensitive"


def test_secondary_overlap_reference_boundaries_year_ban_market_liquidity_and_risk():
    rows = [selection("2024-05-31", ["a", "b", "c", "d"], f_vol=["a"], f_raise=["b"], removed=["a", "b", "c"]),
            selection("2025-04-30", ["a", "b", "c", "d"], f_vol=["a"], f_raise=["b"], removed=["a", "b", "c"])]
    for s in rows:
        s["charts"]["ch_heat"]["rsi"] = [71., 80., 70., 69.]
    frame = observations(rows, nets=[-.2, -.1, -.05, .1])
    metrics = ch001.variant_metrics(frame, rows, "ch_heat")
    assert metrics["primary"]["mean"] == pytest.approx(.075)
    overlap = metrics["overlap_and_computability"][0]
    assert overlap["F_vol"]["share"] == overlap["F_raise"]["share"] == pytest.approx(1 / 3)
    assert overlap["counts"]["U"]["boundary_rsi_69_71"] == 3
    assert metrics["reference_P"]["primary"]["mean"] > 0 and not metrics["reference_P"]["used_for_judgement"]
    assert set(metrics["by_year"]) == {"2024", "2025"}
    assert metrics["ban_period"]["inside"]["primary"]["count"] == 1
    assert metrics["ban_period"]["outside"]["primary"]["count"] == 1
    assert metrics["markets"]["KOSPI"]["primary"]["mean"] == metrics["primary"]["mean"]
    assert metrics["markets"]["KOSDAQ"]["primary"]["count"] == 0
    assert metrics["liquidity_buckets"]["2"]["primary"]["mean"] == metrics["primary"]["mean"]
    assert metrics["validation_year_same_sign"] and not metrics["validation_sign_used_for_judgement"]
    assert hf001.risk_metrics([-.1, .2])["max_drawdown"] == pytest.approx(.1)


@pytest.fixture
def offline_repo(tmp_path, monkeypatch):
    ids = (ch001.EXPERIMENT_ID, av001.EXPERIMENT_ID, hf001.EXPERIMENT_ID,
           hc002.EXPERIMENT_ID, av001.hc003.EXPERIMENT_ID)
    committed_preregistrations(tmp_path, ids)
    monkeypatch.setattr(hc002, "MONTHS", pd.period_range("2024-05", "2024-06", freq="M"))
    return tmp_path


def prepared_fixture(root):
    data = root / "data"
    days, _ = ch001.calendar_plan(pd.bdate_range("2023-05-01", "2025-12-30"))
    prices, days = market(days, codes=("000010", "000020"), returns=.001)
    prices.loc[(days, "000020"), "ret_1d"] = np.resize([.1, -.1], len(days))
    prices[["open", "close", "base_price"]] = 10000.
    prices.high, prices.low = 11000., 9000.
    for year, revenue, income in ((2022, 80, 8), (2023, 100, 12), (2024, 150, 30)):
        event = filing(f"{year}0515", title=f"분기보고서 ({year}.03)")
        second = {**filing(f"{year}0515", title=f"분기보고서 ({year}.03)", corp="00000002", number=2), "stock_code": "000020"}
        write_rows(data / f"disclosures/dart_full/list/{year}/{year}0515.jsonl", [event, second])
        save(data, version(event["rcept_no"], year=year, revenue=revenue, income=income),
             {**version(second["rcept_no"], year=year, revenue=revenue, income=income, corp="00000002"), "stock_codes": ["000020"]})
    for day in pd.date_range("2023-11-01", "2024-06-30"):
        path = data / "disclosures/dart_full/list" / str(day.year) / (day.strftime("%Y%m%d") + ".jsonl")
        if not path.exists():
            write_rows(path, [])
    checkpoint(data)
    return data, prices, days


def test_real_AV001_prepare_once_both_rules_and_filter_computability_only(offline_repo, monkeypatch):
    data, prices, days = prepared_fixture(offline_repo)
    calls, original = [], av001.prepare
    def prepare(**kwargs):
        calls.append(kwargs["stage"])
        return original(**kwargs)
    monkeypatch.setattr(av001, "prepare", prepare)
    _, _, sessions, prepared = ch001.prepare_inputs(data_dir=data, repo_root=offline_repo, prices=prices,
                                                  sessions=days)
    assert calls == ["design_validation"] and prepared["raise_complete"]
    for rule in ch001.RULES:
        assert len(prepared["selections"][rule]) == 2
        for s in prepared["selections"][rule]:
            assert s["B"] == av001.target_set("av_conc", s, set()) == {"000010"}
            assert s["charts"]["ch_heat"].loc["000010", "computable"]
    assert sessions.max().year == 2025 and prepared["L"] == 0 and prepared["sensitivity_available"]


@pytest.mark.parametrize("compressed,prior_trials,threshold", [(False, 17, 2.), (True, 18, 3.)])
def test_fixture_execute_three_compact_rows_full_results_and_report_only_v2(offline_repo, monkeypatch, compressed, prior_trials, threshold):
    data, prices, days = prepared_fixture(offline_repo)
    if compressed:
        monkeypatch.setattr(common, "RESULT_GZIP_THRESHOLD_BYTES", 1)
    for _ in range(prior_trials):
        experiment_registry.record_trial(ch001.EXPERIMENT_ID, "prior", {}, "fail", repo_root=offline_repo)
    result = ch001.run_experiment(data_dir=data, repo_root=offline_repo, prices=prices, sessions=days)
    assert experiment_registry.trial_count(ch001.EXPERIMENT_ID, repo_root=offline_repo) == prior_trials + 3
    with (offline_repo / experiment_registry.REGISTRY_PATH).open() as handle:
        rows = experiment_registry._registry_rows(handle)[-3:]
    assert [r["variant"] for r in rows] == list(ch001.VARIANTS)
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        assert set(metrics["rules"]) == {"S", "O"}
        assert len(row["metrics_json"].encode()) < 32768
        assert metrics["rules"]["S"]["judgement_inputs"]["trial_count_after_run"] == prior_trials + 3
        assert metrics["rules"]["S"]["judgement_inputs"]["t_threshold"] == threshold
        saved = common.read_result_json(offline_repo / metrics["result_file"])
        assert saved["preregistration_sha256"] == result["preregistration_sha256"]
        assert metrics["result_file"].endswith(".json.gz") == compressed
    assert all(result["assertions"][r]["exact_names_weights_cash_cost_net"] for r in ch001.RULES)
    assert all(not result["cost_model_v2"][r]["used_for_judgement"] for r in ch001.RULES)
    for rule in ch001.RULES:
        for variant in ch001.VARIANTS:
            main = result["rules"][rule][variant]["per_month"]
            repriced = result["cost_model_v2"][rule]["variants"][variant]["per_month"]
            for original, v2 in zip(main, repriced):
                for portfolio in ("baseline", "filtered"):
                    for key in ("names", "weights", "cash_weight"):
                        assert original[portfolio][key] == v2[portfolio][key]
    assert result["source_hashes"]["financials"] and result["source_hashes"]["main_listing"]
    assert result["source_hashes"]["code"]["backtesting/experiments/ch001.py"] == hf001._digest(Path(ch001.__file__))
    assert len(list((offline_repo / "research/experiments" / ch001.EXPERIMENT_ID / "results").glob("*.md"))) == 1


def test_listing_coverage_failure_stops_before_forward_and_records_data_incomplete(offline_repo, monkeypatch):
    data, prices, days = prepared_fixture(offline_repo)
    (data / "disclosures/dart_full/list/2024/20240501.jsonl").unlink()
    monkeypatch.setattr(ch001, "forward_observations", lambda *a, **k: pytest.fail("incomplete listings reached returns"))
    result = ch001.run_experiment(data_dir=data, repo_root=offline_repo, prices=prices, sessions=days)
    assert all(v["verdict"] == "data_incomplete" for v in result["variants"].values())
    assert result["passed_variants"] == []


def test_O_availability_is_checked_on_O_and_never_passes(offline_repo, monkeypatch):
    data, prices, days = prepared_fixture(offline_repo)
    original_prepare, original_forward, calls = av001.prepare, ch001.forward_observations, []
    def prepare(**kwargs):
        prepared = original_prepare(**kwargs)
        assert prepared["versions"]["S"].sensitivity_available
        report = prepared["versions"]["O"].report()
        prepared["versions"]["O"] = SimpleNamespace(sensitivity_available=False,
            report=lambda: {**report, "sensitivity_available": False})
        return prepared
    def forward(prices, selections, sessions, **kwargs):
        calls.append(selections)
        return original_forward(prices, selections, sessions, **kwargs)
    monkeypatch.setattr(av001, "prepare", prepare)
    monkeypatch.setattr(ch001, "forward_observations", forward)
    result = ch001.run_experiment(data_dir=data, repo_root=offline_repo, prices=prices, sessions=days)
    assert len(calls) == 1
    assert all(v["verdict"] == "sensitivity_unavailable" for v in result["variants"].values())
    assert all(v["verdict"] == "sensitivity_unavailable" for v in result["rules"]["O"].values())


def test_every_registry_row_validated_before_any_publication(offline_repo, monkeypatch):
    rules = {r: ch001.judge_variants(judgement_fixture(), ch001._registration(offline_repo).fields, 3) for r in ch001.RULES}
    payload = {"rules": rules, "delisting_sensitivity": copy.deepcopy(rules), "passed_variants": [],
        "variants": {v: {"verdict": "pass"} for v in ch001.VARIANTS}}
    payload["rules"]["O"]["ch_anchor"]["judgement_inputs"]["too_large"] = "x" * 32768
    monkeypatch.setattr(common, "write_result_files", lambda *a: pytest.fail("published before row validation"))
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *a, **k: pytest.fail("appended before row validation"))
    with pytest.raises(ValueError, match="32 KiB"):
        ch001.publish(payload, repo_root=offline_repo)
    assert not (offline_repo / experiment_registry.REGISTRY_PATH).exists()


def test_hf001_saved_comparison_month_mean_and_name_differences(tmp_path):
    s = selection("2024-05-31", ["a", "b"], f_vol=["b"])
    frame = observations([s])
    directory = tmp_path / "research/experiments" / hf001.EXPERIMENT_ID / "results"
    saved = {"variants": {"f_both": {"per_month": [{"decision_date": "2024-05-31", "remaining": ["b"],
        "costs": {"1.0": {"filtered_net": -.11}}}]}}}
    common.write_result_files(directory, "fixture", json.dumps(saved), ["fixture"])
    result = ch001.hf001_comparison(frame, [s], repo_root=tmp_path)
    comparison = result["comparisons"][0]
    assert comparison["name_difference_count"] == 2 and comparison["common_months"] == 1
    assert comparison["common_month_mean_net_difference"] == pytest.approx(.2)


def test_supplement_integrity_2026_hashes_separate_and_protected_never_opened(tmp_path, monkeypatch):
    for day in ("20260102", "20260701"):
        write_rows(tmp_path / f"market/krx_daily/2026/{day}.jsonl", [
            {"stock_code": "000010", "trade_date": pd.Timestamp(day).date().isoformat()}])
    checkpoint(tmp_path)
    protected = tmp_path / "market/krx_daily/2026/20260102.jsonl"
    original = Path.open
    def guarded(path, *a, **k):
        assert path != protected and path.name != ".env"
        return original(path, *a, **k)
    monkeypatch.setattr(Path, "open", guarded)
    status = dart_listing.listing_status(tmp_path)
    access = ch001.integrity_price_access(tmp_path, status)
    assert access["evaluation_use"] is False
    assert list(access["nonprotected_2026_hash_access"]) == ["market/krx_daily/2026/20260701.jsonl"]
    assert list(access["protected_2026_frozen_hashes_only"]) == ["market/krx_daily/2026/20260102.jsonl"]
    source = ch001.source_hashes(tmp_path, [], prices=pd.DataFrame())
    assert source["prices"] == {} and "in_memory_prices" in source


def test_dry_run_real_preparation_without_real_forward_verdict_or_writes(offline_repo, monkeypatch):
    data, prices, days = prepared_fixture(offline_repo)
    prepared = ch001.prepare_inputs(data_dir=data, repo_root=offline_repo, prices=prices, sessions=days)
    monkeypatch.setattr(ch001, "prepare_inputs", lambda **k: prepared)
    monkeypatch.setattr(ch001, "forward_observations", lambda *a, **k: pytest.fail("dry-run returns"))
    monkeypatch.setattr(ch001, "judge_variants", lambda *a, **k: pytest.fail("dry-run judgement"))
    monkeypatch.setattr(ch001, "publish", lambda *a, **k: pytest.fail("dry-run publication"))
    plan = ch001.dry_run(data_dir=data, repo_root=offline_repo)
    assert plan["L"] == 0 and plan["listing_incomplete_months"] == 0
    assert len(plan["filter_counts"]["S"]["ch_heat"]) == 2
    assert plan["fixture_timing"]["forward_seconds"] > 0
    assert not (offline_repo / experiment_registry.REGISTRY_PATH).exists()


def test_cli_default_dry_only_explicit_execute_and_no_holdout(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ch001, "run_experiment", lambda: calls.append("execute") or {"passed_variants": []})
    assert ch001.main(["--execute"]) == 0 and calls == ["execute"]
    with pytest.raises(SystemExit):
        ch001.main(["--holdout"])
    with pytest.raises(SystemExit):
        ch001.main(["--execute", "--dry-run"])
    # Default and explicit dry-run dispatch identically; returning a sentinel stops printing.
    def dry():
        calls.append("dry")
        raise RuntimeError("dry sentinel")
    monkeypatch.setattr(ch001, "dry_run", dry)
    for argv in ([], ["--dry-run"]):
        with pytest.raises(RuntimeError, match="dry sentinel"):
            ch001.main(argv)
    assert calls == ["execute", "dry", "dry"]


def test_fixture_runtime_reports_measured_work():
    result = ch001.fixture_timing()
    assert result["paths"] == 200 and result["stocks"] == 128 and result["control_months"] == 12
    assert all(result[key] > 0 for key in ("filter_seconds", "forward_seconds", "control_seconds"))


@pytest.mark.parametrize("mode", ["design", "dry_run"])
@pytest.mark.parametrize("frozen,refresh", [(False, True), (True, True), (True, False)])
def test_design_and_dry_run_never_open_protected_price_files(offline_repo, monkeypatch, mode, frozen, refresh):
    # Exercise the complete entry points and merged-listing checks, including a
    # refreshed delisted-company reference that used to trigger protected reads.
    from test_experiments_ch002 import protect_fixture, trap_price_opens
    data, prices, days = protect_fixture(offline_repo, frozen=frozen, refresh=refresh)
    trap_price_opens(monkeypatch)
    original = ch001.calendar_plan
    monkeypatch.setattr(ch001, "calendar_plan", lambda sessions=None: original(days if sessions is None else sessions))
    if mode == "dry_run":
        def loader(data_dir, start, end, **kwargs):
            # Invoke the real bounded file selection before supplying synthetic
            # evaluation rows; the trap remains active through preparation.
            ch001.d001._price_files(Path(data_dir) / "market/krx_daily", start, end, repo_root=offline_repo)
            return prices, []
        monkeypatch.setattr(ch001, "load_prices", loader)
        result = ch001.dry_run(data_dir=data, repo_root=offline_repo)
    else:
        result = ch001.run_experiment(data_dir=data, repo_root=offline_repo, prices=prices, sessions=days)
    assert result["listing_status"]["supplement_complete"] == (frozen and not refresh)
    assert not (offline_repo / holdout.LEDGER_PATH).exists()
