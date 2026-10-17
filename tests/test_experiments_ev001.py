"""EV001 rules exercised on small local fixtures; no real experiment execution."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry, holdout
from backtesting.experiments import common, ev001, hc001, hc002, hc003, hf001, ordering
from src.ingestion import dart_listing, dart_quarterly
from src.ingestion.dart_financial_date_rules import FilingDates, FinancialVersions
from src.ingestion.storage import write_rows
from dart_listing_fixtures import checkpoint
from test_dart_financial_date_rules import filing, save, version
from test_experiments_hc003 import prices_for


@pytest.fixture
def repo(tmp_path, monkeypatch):
    ids = (ev001.EXPERIMENT_ID, hc002.EXPERIMENT_ID, hc003.EXPERIMENT_ID, hf001.EXPERIMENT_ID)
    registrations = {name: experiment_registry.verify_preregistration(name) for name in ids}
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda name, *a, **k: registrations[name])
    monkeypatch.setattr(holdout, "verify_preregistration", lambda name, *a, **k: registrations[name])
    monkeypatch.setattr(experiment_registry, "_git", lambda *a: "fixture-commit")
    monkeypatch.setattr(ev001, "N_CONTROLS", 4)
    return tmp_path


def calendar(start="2023-01-01", end="2026-07-31"):
    days = hc003.session_calendar()
    return pd.DatetimeIndex(days[(days >= start) & (days <= end)])


def financial_fixture(root, *, prior_income=10, prior_revenue=110, high=False):
    """Complete standalone/YTD values, ending with a May-2025 new phase-2 filing."""
    records, listings = [], []
    for q in pd.period_range("2023Q1", "2025Q1", freq="Q"):
        revenue = prior_revenue if str(q) == "2024Q4" else 120 if str(q) == "2025Q1" else 200 if high and str(q) == "2024Q3" else 100
        income = prior_income if str(q) == "2024Q4" else 20 if str(q) == "2025Q1" else 10
        release = pd.Timestamp(q.year + (q.quarter == 4), {1: 5, 2: 8, 3: 11, 4: 3}[q.quarter], 15)
        report = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}[q.quarter]
        title = {1: "분기보고서", 2: "반기보고서", 3: "분기보고서", 4: "사업보고서"}[q.quarter]
        listing = filing(release.strftime("%Y%m%d"), title=f"{title} ({q.year}.{q.quarter * 3:02d})")
        row = version(listing["rcept_no"], year=q.year, report=report, revenue=revenue, income=income)
        previous = [r for r in records if r["bsns_year"] == q.year]
        for key, value in zip(dart_quarterly.ACCOUNTS, (revenue, income, 5)):
            account = row["raw_accounts"][key]
            cumulative = sum(r["standalone"][key] for r in previous) + value
            account["thstrm_dt"] = f"{q.year}.{(q.quarter - 1) * 3 + 1:02d}.01~{q.year}.{q.quarter * 3:02d}.{30 if q.quarter in (2, 3) else 31}"
            if q.quarter in (2, 3):
                account["thstrm_add_amount"] = cumulative
            elif q.quarter == 4:
                account["thstrm_amount"] = cumulative
        row["standalone"] = dict(zip(dart_quarterly.ACCOUNTS, (revenue, income, 5)))
        records.append(row)
        listings.append(listing)
    save(root, *records)
    return records, listings


def events_for(root, listings, rule="S", stage="design_validation"):
    dates = FilingDates(listings)
    versions = FinancialVersions(root, dates, rule, stage, sample_end=ev001.BOUNDS[stage][1])
    events, counts = ev001.filing_events(versions, dates, calendar())
    return events, counts, versions


@pytest.mark.parametrize("prior_income,high,expected", [(10, False, 3), (15, False, 2), (11, True, "none"), (5, False, 3)])
def test_new_entry_maintenance_and_computable_unphased_prior_use_HC001(repo, prior_income, high, expected):
    _, listings = financial_fixture(repo, prior_income=prior_income, high=high)
    events, counts, _ = events_for(repo, listings)
    current = [e for e in events if e["fiscal_quarter"] == "2025Q1"]
    assert len(current) == 1
    assert current[0]["prior_phase"] == expected
    assert current[0]["kind"] == ("maintenance" if expected == 2 else "new_entrant")
    assert current[0]["public_date"] == "2025-05-15"
    assert current[0]["entry_date"] == "2025-05-16"
    assert counts["by_year"]["2025"][current[0]["kind"]] >= 1


def test_uncomputable_prior_excluded_and_no_older_signal_substitution(repo):
    records, listings = financial_fixture(repo)
    records = [r for r in records if r["fiscal_quarter"] != "2023Q4"]
    save(repo, *records)
    # save appends by archive name; explicitly remove this excluded archive.
    (repo / "fundamentals/dart_quarterly/2023_11011.jsonl").unlink()
    events, counts, _ = events_for(repo, listings)
    assert not any(e["fiscal_quarter"] == "2025Q1" for e in events)
    assert counts["by_year"]["2025"]["prior_phase_uncomputable"] == 1


def test_CFS_priority_OFS_fallback_single_stock_quarter_date_and_late_old_quarter(repo):
    records, listings = financial_fixture(repo)
    current = records[-1]
    save(repo, *records, {**copy.deepcopy(current), "fs_div": "OFS"})
    events, _, _ = events_for(repo, listings)
    current_events = [e for e in events if e["fiscal_quarter"] == "2025Q1"]
    assert len(current_events) == 1 and current_events[0]["fs_div"] == "CFS"
    records[-1]["fs_div"] = "OFS"
    save(repo, *records)
    events, _, _ = events_for(repo, listings)
    assert [e for e in events if e["fiscal_quarter"] == "2025Q1"][0]["fs_div"] == "OFS"
    newer = version("20250501000002", report="11012", revenue=150, income=30)
    save(repo, *records, newer)
    events, counts, _ = events_for(repo, [*listings, filing("20250501", number=2, title="반기보고서 (2025.06)")])
    assert not any(e["fiscal_quarter"] == "2025Q1" for e in events)
    assert counts["by_year"]["2025"]["not_latest_quarter"] >= 1


def test_O_changes_events_prior_and_universe_but_actual_cutoff_still_gates(repo):
    records, listings = financial_fixture(repo)
    original = listings[-1]
    corrected = filing("20250901", "20250902", "[정정]분기보고서 (2025.03)")
    records[-1].update(rcept_no=corrected["rcept_no"], available_date="2025-09-01")
    save(repo, *records)
    all_listings = [*listings, corrected]
    s, _, _ = events_for(repo, all_listings, "S")
    o, _, versions = events_for(repo, all_listings, "O")
    assert [e for e in s if e["fiscal_quarter"] == "2025Q1"][0]["public_date"] == "2025-09-02"
    event = [e for e in o if e["fiscal_quarter"] == "2025Q1"][0]
    assert event["public_date"] == "2025-05-15" and event["actual_public_date"] == "2025-09-02"
    prices = prices_for(calendar("2025-07-01", "2025-08-29"), ("000010",))
    universes = []
    for rule in ev001.RULES:
        loader = FinancialVersions(repo, FilingDates(all_listings), rule, "design_validation")
        with common.guarded_reads(ev001.EXPERIMENT_ID):
            u, _ = hc002.universe_on_decision(prices, "2025-08-29", data_dir=repo, repo_root=repo, financial_loader=loader.load)
        universes.append(u)
    assert universes[0].fiscal_quarter.tolist() == ["2024Q4"]
    assert universes[1].fiscal_quarter.tolist() == ["2025Q1"]
    protected = filing("20251231", "20260101", "[정정]분기보고서 (2025.03)", number=3)
    records[-1].update(rcept_no=protected["rcept_no"], available_date="2025-12-31")
    save(repo, *records)
    for rule in ev001.RULES:
        events, _, loaded = events_for(repo, [*listings, protected], rule)
        assert not any(e["fiscal_quarter"] == "2025Q1" for e in events)
        assert loaded.counts["stage_cutoff"] == 1
    assert versions.sensitivity_available and event["original_rcept_no"] == original["rcept_no"]


def test_all_four_date_rules_are_applied_before_event_and_comparison_derivation(repo):
    records, listings = financial_fixture(repo)
    records[0].update(rcept_no="20150501000001", available_date="2015-05-01")
    records[-1].update(rcept_no="20250515000088", available_date="2025-05-15")
    conflict = listings[1]
    listing_lag = filing("20241001", "20241018", "기타", number=99)
    dates = FilingDates([*listings, {**conflict, "report_nm": "different"}, listing_lag])
    save(repo, *records)
    versions = FinancialVersions(repo, dates, "S", "design_validation")
    events, _ = ev001.filing_events(versions, dates, calendar())
    assert dates.L == 17
    assert dates.annotate(records[0])["actual_public_date"] == "2015-05-18"
    assert versions.counts["conflicting_link"] == 1
    assert versions.counts["unlinked_since_listing_start"] == 1
    assert versions.counts["unlinked_pre_listing_plus_L"] == 1
    assert not any(e["fiscal_quarter"] == "2025Q1" for e in events)


def test_same_day_prior_and_cumulative_dependencies_are_not_known_strictly_before_d(repo):
    records, listings = financial_fixture(repo)
    prior = next(r for r in records if r["fiscal_quarter"] == "2024Q4")
    prior.update(rcept_no="20250515000002", available_date="2025-05-15")
    save(repo, *records)
    events, counts, _ = events_for(repo, [*listings, filing("20250515", number=2, title="사업보고서 (2024.12)")])
    assert not any(e["fiscal_quarter"] == "2025Q1" for e in events)
    assert counts["by_year"]["2025"]["prior_phase_uncomputable"] >= 1
    q2 = version("20250515000003", report="11012", revenue=250, income=40)
    for account in q2["raw_accounts"].values():
        account["thstrm_dt"] = "2025.01.01~2025.06.30"
    save(repo, *records, q2)
    events, counts, _ = events_for(repo, [*listings, filing("20250515", number=2, title="사업보고서 (2024.12)"),
                                       filing("20250515", number=3, title="반기보고서 (2025.06)")])
    assert not any(e["fiscal_quarter"] == "2025Q2" for e in events)
    assert counts["by_year"]["2025"]["current_phase_uncomputable"] >= 1


def make_book(root, days=None, prices=None):
    if days is None:
        days = calendar("2025-01-02", "2025-12-30")
    if prices is None:
        prices = prices_for(days, ("000010", "000020", "000030", "000040", "000050"))
    book = ev001.EventPrices(days, days[-1], root)
    book.add(prices)
    book.finish()
    return book, prices


def event(day="2025-05-16", code="000010", **kwargs):
    values = dict(stock_code=code, corp_code="00000001", fiscal_quarter="2025Q1", public_date="2025-05-15",
        entry_date=day, entry_month=day[:7], prior_phase=3, kind="new_entrant", fs_div="CFS", rcept_no="20250515000001",
        actual_public_date="2025-05-15", assumed_public_date="2025-05-15", is_correction=False,
        decision_date="2025-04-30", f_vol=False, f_raise=False, raise_covered=True, volatility_uncomputable=False)
    return {**values, **kwargs}


def prepared_for(book, events):
    u = pd.DataFrame(index=pd.Index(sorted(book.raw), name="stock_code"))
    return {"book": book, "events": {r: copy.deepcopy(events) for r in ev001.RULES},
            "universes": {r: {e["decision_date"]: u.copy() for e in events} for r in ev001.RULES}}


def test_frozen_previous_month_end_and_variant_overlap_inclusive_exit(repo):
    book, _ = make_book(repo)
    assert ev001.decision_date("2017-05-02", calendar("2017-01-01", "2017-06-30")) == pd.Timestamp("2017-04-28")
    assert ev001.decision_date("2025-05-30", book.days) == pd.Timestamp("2025-04-30")
    first = book.days.get_loc(pd.Timestamp("2025-05-16"))
    same_exit, after_exit = (book.days[first + offset].date().isoformat() for offset in (19, 20))
    events = [event(), event(same_exit, fiscal_quarter="2025Q2"), event(after_exit, fiscal_quarter="2025Q3")]
    prepared = prepared_for(book, events)
    twenty, counts = ev001.select_events(prepared, "S", "ev_new20")
    sixty, _ = ev001.select_events(prepared, "S", "ev_new60")
    assert [e["entry_date"] for e in twenty] == ["2025-05-16", after_exit]
    assert len(sixty) == 1 and counts["total"]["holding_overlap"] == 1
    prepared["universes"]["S"]["2025-04-30"] = pd.DataFrame(index=["000020"])
    assert ev001.select_events(prepared, "S", "ev_new20")[1]["total"]["outside_frozen_universe"] == 3


@pytest.mark.parametrize("column,value,reason", [("open", np.nan, "missing_entry_price"), ("volume", 0, "untradable_entry"),
    ("open", 12950, "limit_up_entry"), ("base_price", 0, "missing_entry_close_or_base")])
def test_entry_exclusions_match_HC003_3_1(repo, column, value, reason):
    days = calendar("2025-01-02", "2025-12-30")
    prices = prices_for(days, ("000010",))
    prices.loc[(pd.Timestamp("2025-05-16"), "000010"), column] = value
    book, _ = make_book(repo, days, prices)
    assert book.entry_reason("000010", days.get_loc(pd.Timestamp("2025-05-16"))) == reason
    selected, counts = ev001.select_events(prepared_for(book, [event()]), "S", "ev_new20")
    assert not selected and counts["total"][reason] == 1


def test_base_fallback_uses_previous_exchange_session_and_lower_limit_open_allowed(repo):
    days = calendar("2025-01-02", "2025-12-30")
    prices = prices_for(days, ("000010",))
    prices.loc[(pd.Timestamp("2025-05-16"), "000010"), ["open", "base_price"]] = [7050, np.nan]
    book, _ = make_book(repo, days, prices)
    pos = days.get_loc(pd.Timestamp("2025-05-16"))
    assert book.entry_reason("000010", pos) is None
    prices = prices.drop((days[pos - 1], "000010"))
    book, _ = make_book(repo, days, prices)
    assert book.entry_reason("000010", pos) == "missing_entry_close_or_base"


def test_adjusted_chain_and_each_side_actual_raw_date_market_prior_ADV(repo, monkeypatch):
    days = calendar("2025-01-02", "2025-12-30")
    prices = prices_for(days, ("000010",))
    entry = days.get_loc(pd.Timestamp("2025-05-16"))
    target = entry + 19
    prices.loc[(days[entry], "000010"), ["open", "close"]] = [9000, 10000]
    prices.loc[(days[entry + 1], "000010"), ["close", "base_price", "ret_1d"]] = [5000, 5000, .1]
    prices.loc[(days[target], "000010"), ["close", "base_price", "ret_1d", "market"]] = [6000, 5000, np.nan, "KOSDAQ"]
    prices.loc[(days[entry - 1], "000010"), "trading_value"] = 4e9
    prices.loc[(days[target - 1], "000010"), "trading_value"] = 8e9
    calls = []
    def rate(price, market, day, adv, side, *, multiplier, model_version):
        calls.append((price, market, day, adv, side, multiplier, model_version))
        return (.01 if side == "buy" else .02) * multiplier
    monkeypatch.setattr(cost_model, "one_way_cost", rate)
    book, _ = make_book(repo, days, prices)
    row = book.outcomes(["000010"], entry, 20).iloc[0]
    gross = 10000 / 9000 * 1.1 * 1.2 - 1
    assert row.gross == pytest.approx(gross)
    assert row["v2_1.0"] == pytest.approx((1 + gross) * .98 / 1.01 - 1)
    assert row["v2_1.5"] == pytest.approx((1 + gross) * .97 / 1.015 - 1)
    assert row["v2_2.0"] == pytest.approx((1 + gross) * .96 / 1.02 - 1)
    assert calls[0] == (9000., "KOSPI", days[entry].date(), 2.1e9, "buy", 1., "v2")
    assert calls[1] == (6000., "KOSDAQ", days[target].date(), 2.4e9, "sell", 1., "v2")
    assert {r[-1] for r in calls} == {"v1", "v2"}
    assert book.counts["unadjusted_fallback"] == 1


def test_missing_cost_inputs_fail_instead_of_excluding_or_substituting(repo):
    book, prices = make_book(repo)
    pos = book.days.get_loc(pd.Timestamp("2025-05-16"))
    prices.loc[(book.days[pos - 1], "000010"), "trading_value"] = np.nan
    book, _ = make_book(repo, book.days, prices)
    with pytest.raises(ValueError, match="prior-20-session ADV"):
        book.outcomes(["000010"], pos, 20)


@pytest.mark.parametrize("resumes", [True, False])
def test_missing_exit_resumption_and_delisting_only_through_stage_end(repo, resumes):
    days = calendar("2025-01-02", "2025-12-30")
    prices = prices_for(days, ("000010",))
    pos = days.get_loc(pd.Timestamp("2025-05-16"))
    target = pos + 19
    missing = (prices.index.get_level_values("trade_date") >= days[target])
    if resumes:
        missing &= prices.index.get_level_values("trade_date") < days[target + 3]
    prices = prices.loc[~missing]
    book, _ = make_book(repo, days, prices)
    row = book.outcomes(["000010"], pos, 20).iloc[0]
    assert row.exit_position == target - 1
    assert row.delisted == (not resumes) and row.halted_exit == resumes
    assert row.gross_loss == (-1 if not resumes else row.gross)
    assert row["v2_1.0_loss"] == (-1 if not resumes else row["v2_1.0"])
    # Even a known later resumption cannot be supplied to this stage's book.
    with pytest.raises(ValueError, match="outside stage"):
        book.add(prices_for(pd.DatetimeIndex([pd.Timestamp("2026-01-02")]), ("000010",)))


def test_lower_limit_close_valued_at_prior_permissible_close(repo):
    book, prices = make_book(repo)
    entry = book.days.get_loc(pd.Timestamp("2025-05-16"))
    target = entry + 19
    prices.loc[(book.days[target], "000010"), "close"] = 7000
    book, _ = make_book(repo, book.days, prices)
    row = book.outcomes(["000010"], entry, 20).iloc[0]
    assert row.exit_position == target - 1 and row.limit_down_exit and row.stale_exit


def test_benchmark_drops_nontradable_members_and_renormalizes(repo):
    book, prices = make_book(repo)
    entry = book.days.get_loc(pd.Timestamp("2025-05-16"))
    prices.loc[(book.days[entry], "000020"), "volume"] = 0
    prices.loc[(book.days[entry + 1], "000010"), "ret_1d"] = .1
    book, _ = make_book(repo, book.days, prices)
    prepared = prepared_for(book, [event()])
    selected, _ = ev001.select_events(prepared, "S", "ev_new20")
    tables = ev001.outcome_tables(prepared, "S", selected, 20)
    assert tables["2025-05-16"]["dropped_members"] == 1
    assert tables["2025-05-16"]["benchmark"] == pytest.approx(.1 / 4)


def test_volatility_uses_last_session_at_d_market_ranks_60_verified_max_ties(repo):
    book, prices = make_book(repo)
    rng = np.random.default_rng(1)
    for number, code in enumerate(sorted(book.raw)):
        prices.loc[(slice(None), code), "ret_1d"] = rng.normal(0, (number + 1) * .001, len(book.days))
    prices.loc[(slice(None), "000050"), "market"] = "KOSDAQ"
    book, _ = make_book(repo, book.days, prices)
    pos = book.days.searchsorted(pd.Timestamp("2025-05-18"), side="right") - 1
    u = pd.DataFrame(index=pd.Index(sorted(book.raw), name="stock_code"))
    expected = hf001.volatility_filter(prices, u, book.days[pos], book.days)
    actual = book.volatility(u.index, pos)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.loc["000050", "percentile"] == 1 and actual.loc["000050", "f_vol"]
    prices.loc[(book.days[pos - 59], "000010"), "calendar_status"] = "unverified"
    book, _ = make_book(repo, book.days, prices)
    assert pd.isna(book.volatility(u.index, pos).loc["000010", "volatility_60d"])
    assert not book.volatility(u.index, pos).loc["000010", "f_vol"]


@pytest.mark.parametrize("flag,reason", [("f_vol", "filtered_vol"), ("f_raise", "filtered_raise"),
                                       ("raise_covered", "listing_coverage_unusable")])
def test_filters_coverage_only_affect_filtered_variant_and_are_counted(repo, flag, reason):
    book, _ = make_book(repo)
    prepared = prepared_for(book, [event(**{flag: flag != "raise_covered"})])
    assert len(ev001.select_events(prepared, "S", "ev_new20")[0]) == 1
    assert len(ev001.select_events(prepared, "S", "ev_new60")[0]) == 1
    selected, counts = ev001.select_events(prepared, "S", "ev_new20_f")
    assert not selected and counts["total"][reason] == 1


def test_raise_effective_next_session_same_day_age_0_125_not_126():
    days = calendar("2024-01-01", "2025-12-30")
    pos = days.get_loc(pd.Timestamp("2025-05-16"))
    originals = [filing(days[pos - age - 1].strftime("%Y%m%d"), number=n, title="유상증자결정")
                 for n, age in enumerate((0, 125, 126), 1)]
    extra = [filing("20250515", number=4, title="[정정]유상증자결정"),
             filing("20250515", number=5, title="유상무상증자결정"), filing("20250515", number=6, title="철회 유상증자결정")]
    frame = pd.DataFrame([*originals, *extra])
    events, counts = hf001.rights_events(frame, days)
    assert (pos - events.known_position).tolist() == [126, 125, 0]
    assert len(events.loc[events.known_position.between(pos - 125, pos)]) == 2
    assert counts["correction_titles"] == 1 and counts["withdrawal_titles"] == 1


def test_NW_calendar_lag_gaps_monthly_equal_weight_and_design_only_reference():
    rows = [event(day, code=str(n), excess=value) for n, (day, value) in enumerate(
        [("2020-01-02", .1), ("2020-01-03", .3), ("2020-03-02", -.1), ("2020-04-02", .4), ("2020-08-03", .2)])]
    summary = ev001.monthly_summary(rows)
    months, values = ["2020-01", "2020-03", "2020-04", "2020-08"], np.array([.2, -.1, .4, .2])
    assert summary["primary"]["mean"] == pytest.approx(values.mean())
    centered = values - values.mean()
    expected = np.dot(centered, centered) / 4 + 2 * (.75 * centered[2] * centered[1] + .5 * centered[1] * centered[0] + .25 * centered[2] * centered[0]) / 4
    assert summary["primary"]["long_run_variance"] == pytest.approx(expected)
    assert summary["primary"]["lag_pair_counts"] == {"1": 1, "2": 1, "3": 1}
    assert summary["primary"]["p_one_sided"] == pytest.approx(.5 * math.erfc(summary["primary"]["t_stat"] / math.sqrt(2)))


def passing_metrics():
    primary = {**ev001.monthly_summary([])["primary"], "count": 36, "mean": .01, "standard_error": .001,
               "t_stat": 2.5, "p_one_sided": .001}
    return {"event_count": 300, "entry_dates": 100, "primary": primary,
            "cost_sensitivity": {"1.5": {"primary": {"mean": .005}}},
            "random_control": {"share_of_controls": .05}, "design": {"primary": {"mean": .01}}}


@pytest.mark.parametrize("events,months,se", [(299, 36, .001), (300, 35, .001), (300, 36, None), (300, 36, 0)])
def test_insufficient_events_months_or_undefined_SE_keep_Holm_three(repo, events, months, se):
    variants = {name: passing_metrics() for name in ev001.VARIANTS}
    result = variants["ev_new20"]
    result["event_count"], result["primary"]["count"], result["primary"]["standard_error"] = events, months, se
    ev001.judge_variants(variants, ev001._registration(repo).fields, 3)
    assert result["verdict"] == "insufficient" and result["judgement_inputs"]["p_for_holm"] == 1
    assert variants["ev_new60"]["holm_p"] == .003


@pytest.mark.parametrize("trials,expected", [(20, "pass"), (21, "fail")])
def test_trial_threshold_includes_three_new_rows_and_strict_t(repo, trials, expected):
    variants = {name: passing_metrics() for name in ev001.VARIANTS}
    ev001.judge_variants(variants, ev001._registration(repo).fields, trials)
    assert all(r["verdict"] == expected for r in variants.values())
    assert all(r["judgement_inputs"]["t_threshold"] == (2 if trials == 20 else 3) for r in variants.values())


@pytest.mark.parametrize("change", ["mean", "cost", "share", "shortage", "t", "holm"])
def test_each_pass_gate_is_required(repo, change):
    variants = {name: passing_metrics() for name in ev001.VARIANTS}
    for r in variants.values():
        if change == "mean": r["primary"]["mean"] = 0
        if change == "cost": r["cost_sensitivity"]["1.5"]["primary"]["mean"] = 0
        if change == "share": r["random_control"]["share_of_controls"] = .051
        if change == "shortage": r["random_control"]["share_of_controls"] = None
        if change == "t": r["primary"]["t_stat"] = 2
        if change == "holm": r["primary"]["p_one_sided"] = .051
    ev001.judge_variants(variants, ev001._registration(repo).fields, 3)
    assert all(r["verdict"] == "fail" for r in variants.values())


def test_controls_path_date_order_sorted_candidates_nonreplacement_overlap_draws_and_reset(repo):
    book, _ = make_book(repo)
    pos = book.days.get_loc(pd.Timestamp("2025-05-16"))
    next_day = book.days[pos + 1].date().isoformat()
    after_exit = book.days[pos + 20].date().isoformat()
    events = [event(), event(next_day, code="000020"), event(after_exit, code="000010")]
    prepared = prepared_for(book, events)
    tables = ev001.outcome_tables(prepared, "S", events, 20)
    main, loss = ev001.random_controls(book, events, tables, 20, -.01, -.01)
    rng, expected = np.random.default_rng(0), []
    for path in range(ev001.N_CONTROLS):
        held, choices = {}, []
        for event_row in events:
            day = event_row["entry_date"]
            entry = book.days.get_loc(pd.Timestamp(day))
            held = {code: end for code, end in held.items() if end >= entry}
            candidates = sorted(set(book.raw) - set(held) - {event_row["stock_code"]})
            chosen = rng.choice(candidates, size=1, replace=False).tolist()
            choices.append(chosen)
            held.update({code: entry + 19 for code in chosen})
        expected.append(choices)
    draws = main["draws"]
    decoded = [[[draws["code_table"][i] for i in chosen] for chosen in path] for path in draws["paths"]]
    assert decoded == expected
    assert main == ev001.random_controls(book, events, tables, 20, -.01, -.01)[0]
    assert loss["path_means"] == main["path_means"] and main["computable"]


def test_controls_match_final_count_each_date_exclude_all_day_event_stocks_and_shortage(repo):
    book, _ = make_book(repo)
    events = [event(code=code) for code in ("000010", "000020")]
    prepared = prepared_for(book, events)
    tables = ev001.outcome_tables(prepared, "S", events, 20)
    main, _ = ev001.random_controls(book, events, tables, 20, 0., 0.)
    draws = main["draws"]
    for path in draws["paths"]:
        chosen = [draws["code_table"][i] for i in path[0]]
        assert len(chosen) == len(set(chosen)) == 2
        assert not set(chosen) & {"000010", "000020"}
    all_events = [event(code=code) for code in sorted(book.raw)]
    shortage, _ = ev001.random_controls(book, all_events, tables, 20, 0., 0.)
    assert not shortage["computable"] and shortage["share_of_controls"] is None
    assert len(shortage["shortages"]) == ev001.N_CONTROLS
    assert shortage["reason"] == "candidate_shortage"


def test_secondary_paths_boundary_months_prior_groups_corrections_and_maintenance(repo):
    book, _ = make_book(repo)
    selected = [event("2025-11-20", public_date="2025-11-19", entry_month="2025-11", decision_date="2025-10-31",
                      is_correction=True), event("2025-11-21", code="000020", public_date="2025-11-20",
                      entry_month="2025-11", decision_date="2025-10-31", kind="maintenance", prior_phase=2)]
    prepared = prepared_for(book, selected)
    chosen, _ = ev001.select_events(prepared, "S", "ev_new20")
    main, _ = ev001.variant_metrics(prepared, "S", "ev_new20", chosen)
    ev001.secondary_metrics(prepared, "S", "ev_new20", main)
    assert main["path_boundary_exclusions"] == {"40": 1, "60": 1}
    assert set(main["events"][0]["gross_excess_path"]) == {"1", "5", "10", "20"}
    assert main["prior_phases"]["3"]["event_count"] == main["corrections"]["True"]["event_count"] == 1
    assert main["maintenance"]["event_count"] == 0 and not main["maintenance"]["used_for_judgement"]
    assert main["maintenance_all_period"]["event_count"] == 1


@pytest.mark.parametrize("down_code", ["000010", "000020"])
def test_reporting_one_day_limit_down_on_entry_recovers_later_preserves_benchmark_and_primary(repo, down_code):
    book, prices = make_book(repo)
    day = "2025-05-16"
    key = pd.Timestamp(day), down_code
    prices.loc[key, ["open", "close", "base_price", "ret_1d"]] = [10000, 7000, 10000, -.3]
    entry = book.days.get_loc(pd.Timestamp(day))
    for pos, closing, base in ((entry + 1, 8000, 7000), (entry + 2, 9000, 8000), (entry + 3, 10000, 9000)):
        prices.loc[(book.days[pos], down_code), ["open", "close", "base_price", "ret_1d"]] = [
            closing, closing, base, closing / base - 1]
    book, _ = make_book(repo, prices=prices)
    entry = book.days.get_loc(pd.Timestamp(day))
    assert book.entry_reason(down_code, entry) is None
    assert book.last_close[down_code][entry] < entry
    assert book.last_close[down_code][entry + 1] == entry + 1
    prepared = prepared_for(book, [event(day)])
    selected, _ = ev001.select_events(prepared, "S", "ev_new20")
    main, _ = ev001.variant_metrics(prepared, "S", "ev_new20", selected)
    primary = copy.deepcopy(main)
    with pytest.raises(ValueError, match="permissible exit"):
        book.outcomes([down_code], entry, 1)
    ev001.secondary_metrics(prepared, "S", "ev_new20", main)
    assert all(main[key] == primary[key] for key in primary if key != "events")
    assert main["events"][0]["benchmark"] == primary["events"][0]["benchmark"]
    assert main["events"][0]["benchmark_dropped_members"] == 0
    assert "1" not in main["events"][0]["gross_excess_path"]
    assert "5" in main["events"][0]["gross_excess_path"]
    missing, = main["path_unavailable"]
    assert (missing["status"], missing["entry_date"], missing["horizon"]) == ("unavailable", day, 1)
    assert missing["members"] == [{"stock_code": down_code, "reason": "no_permissible_exit_on_or_after_entry"}]
    assert missing["counts"] == {"events": 1, "benchmark_members": 5, "unavailable_members": 1,
                                 "by_reason": {"no_permissible_exit_on_or_after_entry": 1}}
    assert main["gross_excess_paths"]["1"]["event_count"] == 0


def test_maintenance_dates_restricted_before_independent_overlap_selection(repo):
    book, _ = make_book(repo)
    prepared = prepared_for(book, [event(), event("2025-05-19"),
        event("2025-05-15", code="000020", kind="maintenance"),
        event(code="000020", kind="maintenance"),
        event("2025-05-19", code="000030", kind="maintenance")])
    selected, _ = ev001.select_events(prepared, "S", "ev_new20")
    assert [e["entry_date"] for e in selected] == ["2025-05-16"]
    main, _ = ev001.variant_metrics(prepared, "S", "ev_new20", selected)
    ev001.secondary_metrics(prepared, "S", "ev_new20", main)
    same, all_period = main["maintenance"], main["maintenance_all_period"]
    assert same["date_scope"] == "final_new_entrant_entry_dates"
    assert [e["entry_date"] for e in same["events"]] == ["2025-05-16"]
    assert same["counts"]["total"] == {"candidates": 1, "selected": 1}
    assert all_period["date_scope"] == "all_period"
    assert [e["entry_date"] for e in all_period["events"]] == ["2025-05-15", "2025-05-19"]
    assert all_period["counts"]["total"]["holding_overlap"] == 1


def test_maintenance_without_permissible_exit_is_reported_separately(repo):
    book, prices = make_book(repo)
    entry = book.days.get_loc(pd.Timestamp("2025-05-20"))
    for day in book.days[entry:entry + 20]:
        prices.loc[(day, "000020"), ["open", "close", "base_price", "ret_1d"]] = [1000, 700, 1000, -.3]
    book, _ = make_book(repo, prices=prices)
    prepared = prepared_for(book, [event(), event("2025-05-20", code="000020", kind="maintenance")])
    selected, _ = ev001.select_events(prepared, "S", "ev_new20")
    main, _ = ev001.variant_metrics(prepared, "S", "ev_new20", selected)
    ev001.secondary_metrics(prepared, "S", "ev_new20", main)
    assert main["event_count"] == 1
    diagnostic = main["maintenance_all_period"]
    assert diagnostic["event_count"] == 0 and diagnostic["counts"]["total"]["selected"] == 1
    missing, = diagnostic["unavailable_paths"]
    assert (missing["entry_date"], missing["horizon"], missing["counts"]["events"]) == ("2025-05-20", 20, 1)


@pytest.mark.parametrize("missing", ["market", "adv", "cost"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_reporting_missing_cost_inputs_preserves_primary_and_records_unavailable_date(repo, monkeypatch, missing, side):
    book, _ = make_book(repo)
    maintained = event("2025-07-01", code="000020", kind="maintenance", decision_date="2025-06-30")
    prepared = prepared_for(book, [event(), maintained])
    selected, _ = ev001.select_events(prepared, "S", "ev_new20")
    primary, _ = ev001.variant_metrics(prepared, "S", "ev_new20", selected)
    before = copy.deepcopy(primary)
    entry = book.days.get_loc(pd.Timestamp(maintained["entry_date"]))
    pos = entry if side == "buy" else entry + 19
    if missing == "market":
        book.market["000020"][pos] = 0
    elif missing == "adv":
        book.adv["000020"] = book.adv["000020"].copy()
        book.adv["000020"][pos] = np.nan
    else:
        cost = cost_model.one_way_cost
        def unavailable_cost(price, market, day, adv, trade_side, **kwargs):
            if day == book.days[pos].date() and trade_side == side:
                raise ValueError("missing fixture cost input")
            return cost(price, market, day, adv, trade_side, **kwargs)
        monkeypatch.setattr(cost_model, "one_way_cost", unavailable_cost)
    # The same inputs are still fatal to primary computation.
    with pytest.raises(ValueError, match="missing"):
        ev001.outcome_tables(prepared, "S", [maintained], 20)
    ev001.secondary_metrics(prepared, "S", "ev_new20", primary)
    assert all(primary[key] == before[key] for key in before if key != "events")
    assert [{k: v for k, v in row.items() if k != "gross_excess_path"} for row in primary["events"]] == before["events"]
    assert all(summary["event_count"] == 1 for summary in primary["gross_excess_paths"].values())
    diagnostic = primary["maintenance_all_period"]
    assert diagnostic["event_count"] == 0 and diagnostic["counts"]["total"]["selected"] == 1
    unavailable, = diagnostic["unavailable_paths"]
    assert unavailable["status"] == "unavailable" and not unavailable["used_for_judgement"]
    assert unavailable["entry_date"] == maintained["entry_date"] and unavailable["horizon"] == 20
    count = 5 if missing == "cost" else 1
    assert unavailable["counts"] == {"events": 1, "benchmark_members": 5, "unavailable_members": count,
                                      "by_reason": {"cost_inputs_unavailable": count}}
    assert all(row["reason"] == "cost_inputs_unavailable" and "missing" in row["detail"]
               for row in unavailable["members"])
    if missing != "cost":
        assert unavailable["members"][0]["stock_code"] == "000020"
    # Gross-only secondary horizons do not require market/ADV/cost inputs.
    gross = book.outcomes(["000020"], entry, 20, costs=False, reporting=True)
    assert not gross.attrs.get("unavailable") and len(gross) == 1


@pytest.mark.parametrize("events,counts,expected", [(29, [10, 19], "holdout_insufficient"),
    (30, [9, 21], "holdout_insufficient"), (30, [10, 20], "pass")])
def test_holdout_sample_gate_uses_events_and_two_months_with_ten(events, counts, expected):
    result = {"event_count": events, "per_month": [{"events": n} for n in counts],
              "primary": {"mean": .005, "t_stat": None}, "random_control": {"share_of_controls": None}}
    assert ev001.holdout_verdict(result, .01)[0] == expected


@pytest.mark.parametrize("effect,mean,expected", [(None, .1, "fail"), (0., .1, "fail"), (-.1, .1, "fail"),
    (.01, .00499, "fail"), (.01, .005, "pass"), (.01, -.01, "fail")])
def test_holdout_own_design_only_effect_half_direction_and_no_t_or_control_gate(effect, mean, expected):
    result = {"event_count": 30, "per_month": [{"events": 10}, {"events": 20}], "primary": {"mean": mean}}
    assert ev001.holdout_verdict(result, effect)[0] == expected


@pytest.mark.parametrize("s,o,available,expected", [("pass", "pass", True, "pass"), ("pass", "fail", True, "version_sensitive"),
    ("fail", "pass", True, "version_sensitive"), ("pass", "pass", False, "sensitivity_unavailable"),
    ("delisting_sensitive", "pass", True, "delisting_sensitive"), ("holdout_insufficient", "fail", True, "holdout_insufficient")])
def test_both_rule_sensitivity_statuses(s, o, available, expected):
    assert ev001.av001.combine_verdict(s, o, sensitivity_available=available) == expected


def test_delisting_verdict_change_cannot_pass(repo):
    main, loss = {name: passing_metrics() for name in ev001.VARIANTS}, {name: passing_metrics() for name in ev001.VARIANTS}
    for r in loss.values():
        r["primary"]["mean"] = -1
    fields = ev001._registration(repo).fields
    ev001.judge_variants(main, fields, 3)
    ev001.judge_variants(loss, fields, 3)
    hc003.apply_delisting_verdict(main, loss)
    assert all(r["verdict"] == "delisting_sensitive" for r in main.values())


def design_payload(repo, *, passed=ev001.VARIANTS):
    rules = {}
    for rule in ev001.RULES:
        rules[rule] = {}
        for name in ev001.VARIANTS:
            metric = passing_metrics()
            metric.update(verdict="pass" if name in passed else "fail", events=[event("2020-01-02", entry_month="2020-01", excess=.01)])
            rules[rule][name] = metric
    payload = {"experiment_id": ev001.EXPERIMENT_ID, "stage": "design_validation", "selected_variant": "ev_new20",
        "prereg_commit": ev001._registration(repo).commit_hash, "verdict": rules["S"]["ev_new20"]["verdict"],
        "rules": rules, "variants": {name: {"verdict": rules["S"][name]["verdict"],
            "rule_verdicts": {r: rules[r][name]["verdict"] for r in ev001.RULES}} for name in ev001.VARIANTS},
        "design_effects": {r: {name: .01 for name in ev001.VARIANTS} for r in ev001.RULES}, "L": 17,
        "source_hashes": {"code": {"fixture": "a"}, "prices": {}}, "trial_count_after_run": 3,
        "code_commit": "old-head", "library_versions": {}, "interpretations": ev001.INTERPRETATIONS}
    payload["replay_sha256"] = ev001.replay_hash(payload)
    return payload


def test_publication_compact_three_rows_same_file_ordering_release_and_saved_integrity(repo):
    payload = design_payload(repo, passed=())
    ev001.publish(payload, repo_root=repo)
    rows = hc003.registry_rows(repo)
    assert len(rows) == 3
    assert max(len(row["metrics_json"].encode()) for row in rows) < 4096
    state = ordering.release_state(rows, ev001.EXPERIMENT_ID, ev001.VARIANTS)
    assert state["passing_variants"] == []
    saved, run = ev001.saved_design(rows, repo_root=repo, registration=ev001._registration(repo))
    assert saved == payload and run["result_file"] == state["result_file"]
    with pytest.raises(ValueError, match="all three"):
        ordering.release_state(rows[:2], ev001.EXPERIMENT_ID, ev001.VARIANTS)
    path = repo / state["result_file"]
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="hash differs"):
        ev001.saved_design(rows, repo_root=repo, registration=ev001._registration(repo))


def test_publication_validates_all_rows_before_any_write(repo):
    payload = design_payload(repo)
    payload["rules"]["O"]["ev_new20_f"]["primary"]["mean"] = float("nan")
    with pytest.raises(ValueError):
        ev001.publish(payload, repo_root=repo)
    assert not list(repo.rglob("results/*")) and not (repo / experiment_registry.REGISTRY_PATH).exists()


@pytest.mark.parametrize("failure", ["prepare", "evaluate", "interrupt"])
def test_design_abort_records_each_variant_hashes_run_location_and_blocks_release(repo, monkeypatch, failure):
    ev001.publish(design_payload(repo, passed=()), repo_root=repo)
    hashes = {"prices": {str(i): "a" * 64 for i in range(2000)}, "financials": {"q": "b" * 64}}
    monkeypatch.setattr(ev001, "source_hashes", lambda *a, **k: hashes)
    def fail(*args, **kwargs):
        raise KeyboardInterrupt("fixture interrupt") if failure == "interrupt" else ValueError("fixture evaluation failed")
    monkeypatch.setattr(ev001, "prepare", fail if failure == "prepare" else lambda **k: {})
    monkeypatch.setattr(ev001, "evaluate", fail)
    for attempt in range(2):
        with pytest.raises(KeyboardInterrupt if failure == "interrupt" else ValueError):
            ev001.run_experiment(data_dir=repo, repo_root=repo, sessions=calendar())
        rows = hc003.registry_rows(repo)
        assert experiment_registry.trial_count(ev001.EXPERIMENT_ID, repo_root=repo) == 6 + 3 * attempt
        aborted = rows[-3:]
        assert [r["variant"] for r in aborted] == list(ev001.VARIANTS)
        assert all(r["verdict"] == "design_aborted" for r in aborted)
        details = [json.loads(r["metrics_json"]) for r in aborted]
        assert len({r["run_id"] for r in details}) == 1
        assert details[0]["failure_location"]["function"] == "fail"
        assert details[0]["failure_location"]["line"] > 0
        assert set(details[0]["input_hashes"]) == set(hashes)
        assert details[0]["input_hashes"] == {key: hashlib.sha256(
            json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest() for key, value in hashes.items()}
        assert max(len(r["metrics_json"].encode()) for r in aborted) < 4096
        with pytest.raises(ValueError, match="all three"):
            ordering.release_state(rows, ev001.EXPERIMENT_ID, ev001.VARIANTS)
    assert len({json.loads(r["metrics_json"])["run_id"] for r in rows[3:]}) == 2
    assert not (repo / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("preflight", ["_registration", "source_hashes"])
def test_design_preflight_failure_does_not_record_evaluation(repo, monkeypatch, preflight):
    def fail(*a, **k):
        raise ValueError("fixture preflight failed")
    monkeypatch.setattr(ev001, preflight, fail)
    with pytest.raises(ValueError, match="preflight"):
        ev001.run_experiment(data_dir=repo, repo_root=repo, sessions=calendar())
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()


def test_holdout_replay_ignores_HEAD_but_checks_input_code_and_deterministic_content(repo, monkeypatch):
    payload = design_payload(repo)
    replay = copy.deepcopy(payload)
    replay["code_commit"] = "new-head"
    replay["performance"] = {"runtime_seconds": 12345}
    assert ev001.replay_hash(replay) == payload["replay_sha256"]
    monkeypatch.setattr(ev001, "compute_design", lambda **kwargs: replay)
    ev001.verify_non_holdout(payload, data_dir=repo, repo_root=repo, sessions=[])
    for key in ("code", "prices"):
        replay["source_hashes"][key]["changed"] = "b"
        replay["replay_sha256"] = ev001.replay_hash(replay)
        with pytest.raises(ValueError, match="reproducibility mismatch"):
            ev001.verify_non_holdout(payload, data_dir=repo, repo_root=repo, sessions=[])
        replay = copy.deepcopy(payload)
        monkeypatch.setattr(ev001, "compute_design", lambda **kwargs: replay)
    replay["variants"]["ev_new20"]["verdict"] = "fail"
    replay["replay_sha256"] = ev001.replay_hash(replay)
    with pytest.raises(ValueError, match="reproducibility mismatch"):
        ev001.verify_non_holdout(payload, data_dir=repo, repo_root=repo, sessions=[])


def test_new_design_trial_with_current_code_becomes_latest_reproduction_baseline(repo, monkeypatch):
    _, listings = financial_fixture(repo)
    for row in listings:
        write_rows(repo / "disclosures/dart_full/list" / row["rcept_dt"][:4] / (row["rcept_dt"] + ".jsonl"), [row])
    days = calendar("2023-01-01", "2025-12-30")
    for day, frame in prices_for(days).reset_index().groupby("trade_date"):
        frame = frame.assign(trade_date=day.strftime("%Y%m%d"), change_rate_pct=frame.ret_1d * 100).drop(columns="ret_1d")
        write_rows(repo / "market/krx_daily" / str(day.year) / (day.strftime("%Y%m%d") + ".jsonl"), frame.to_dict("records"))
    checkpoint(repo)
    hashes = ev001.source_hashes
    code_path = "backtesting/experiments/ev001.py"
    def old_code(*args, **kwargs):
        result = hashes(*args, **kwargs)
        result["code"][code_path] = "previous-code"
        return result
    with monkeypatch.context() as patch:
        patch.setattr(ev001, "source_hashes", old_code)
        ev001.run_experiment(data_dir=repo, repo_root=repo, sessions=days)
    old, old_run = ev001.saved_design(hc003.registry_rows(repo), repo_root=repo, registration=ev001._registration(repo))
    with pytest.raises(ValueError, match="code hashes differ; run a new design/validation trial with this code first"):
        ev001.verify_non_holdout(old, data_dir=repo, repo_root=repo, sessions=days)
    assert len(hc003.registry_rows(repo)) == 3 and not (repo / holdout.LEDGER_PATH).exists()
    current = ev001.run_experiment(data_dir=repo, repo_root=repo, sessions=days)
    saved, run = ev001.saved_design(hc003.registry_rows(repo), repo_root=repo, registration=ev001._registration(repo))
    assert saved == current and run["result_file"] != old_run["result_file"]
    assert saved["source_hashes"]["code"][code_path] == hc003._digest(Path(ev001.__file__))
    assert saved["trial_count_after_run"] == experiment_registry.trial_count(ev001.EXPERIMENT_ID, repo_root=repo) == 6
    ev001.verify_non_holdout(saved, data_dir=repo, repo_root=repo, sessions=days)
    assert len(hc003.registry_rows(repo)) == 6 and not (repo / holdout.LEDGER_PATH).exists()


def holdout_mocks(repo, monkeypatch, *, passed=("ev_new20",)):
    payload = design_payload(repo, passed=passed)
    ev001.publish(payload, repo_root=repo)
    monkeypatch.setattr(ev001, "verify_non_holdout", lambda *a, **k: None)
    monkeypatch.setattr(ev001.financial_dates.FilingDates, "from_data", lambda *a: object())
    monkeypatch.setattr(ev001.financial_dates, "FinancialVersions", lambda *a, **k: SimpleNamespace(sensitivity_available=True))
    return payload


def test_no_pass_does_not_open_holdout_or_inspect_protected_inputs(repo, monkeypatch):
    holdout_mocks(repo, monkeypatch, passed=())
    monkeypatch.setattr(ev001, "holdout_input_hashes", lambda *a, **k: pytest.fail("protected inputs inspected"))
    result = ev001.run_holdout(data_dir=repo, repo_root=repo)
    assert result["verdict"] == "not_applicable" and not (repo / holdout.LEDGER_PATH).exists()


def test_LH002_ledger_contamination_records_terminal_without_opening_EV001(repo, monkeypatch):
    holdout_mocks(repo, monkeypatch)
    ledger = repo / holdout.LEDGER_PATH
    ledger.write_text(json.dumps(dict(experiment_id="LH002_llm_hegemony_judge", recorded_at="fixture", from_date="2026-01-01", to_date="2026-06-30")) + "\n")
    monkeypatch.setattr(ev001, "holdout_input_hashes", lambda *a, **k: pytest.fail("protected inputs inspected"))
    result = ev001.run_holdout(data_dir=repo, repo_root=repo)
    assert result["verdict"] == "holdout_contaminated"
    assert hc003.ledger_experiments(repo) == {"LH002_llm_hegemony_judge"}
    assert ordering.release_state(hc003.registry_rows(repo), ev001.EXPERIMENT_ID, ev001.VARIANTS)["terminal_holdout_variants"] == ["ev_new20"]


@pytest.mark.parametrize("when", ["entry", "after", "exception"])
def test_frozen_input_change_or_exception_consumes_single_session_aborts_and_releases(repo, monkeypatch, when):
    holdout_mocks(repo, monkeypatch)
    calls = []
    def hashes(*a, before_open=False, **k):
        if before_open:
            assert not holdout._SESSIONS.get()
            return {"frozen": "a"}
        assert len(holdout._SESSIONS.get()) == 1
        assert holdout._SESSIONS.get()[0].experiment_id == ev001.EXPERIMENT_ID
        calls.append(1)
        return {"frozen": "b" if when == "entry" or len(calls) > 1 else "a"}
    monkeypatch.setattr(ev001, "holdout_input_hashes", hashes)
    def prepare(**kwargs):
        assert kwargs["stage"] == "holdout"
        if when == "exception":
            raise RuntimeError("fixture failure")
        return {}
    monkeypatch.setattr(ev001, "prepare", prepare)
    monkeypatch.setattr(ev001, "evaluate", lambda *a, **k: {"variants": {"ev_new20": {"verdict": "pass"}}})
    with pytest.raises((ValueError, RuntimeError), match="changed|fixture failure"):
        ev001.run_holdout(data_dir=repo, repo_root=repo)
    assert hc003.ledger_experiments(repo) == {ev001.EXPERIMENT_ID}
    rows = hc003.registry_rows(repo)
    assert rows[-1]["variant"] == "holdout_ev_new20" and rows[-1]["verdict"] == "holdout_aborted"
    metrics = json.loads(rows[-1]["metrics_json"])
    assert metrics["run_id"] and metrics["input_hashes"]["frozen"]
    assert metrics["failure_location"]["function"] == ("prepare" if when == "exception" else "run_holdout")
    assert ordering.release_state(rows, ev001.EXPERIMENT_ID, ev001.VARIANTS)["terminal_holdout_variants"] == ["ev_new20"]
    with pytest.raises(ValueError, match="cannot reopen"):
        ev001.run_holdout(data_dir=repo, repo_root=repo)


@pytest.mark.parametrize("failure", ["reproduction", "freeze", "enter_before_claim", "enter_after_claim"])
def test_holdout_failure_uses_durable_claim_for_terminal_rows_and_retry(repo, monkeypatch, failure):
    holdout_mocks(repo, monkeypatch)
    design_rows = hc003.registry_rows(repo)
    monkeypatch.setattr(ev001, "holdout_input_hashes", lambda *a, **k: {"frozen": "a"})
    def computation_failed(**kwargs):
        raise RuntimeError("post-claim computation failed")
    monkeypatch.setattr(ev001, "prepare", computation_failed)
    ledger = repo / holdout.LEDGER_PATH
    ledger.write_text(json.dumps(dict(experiment_id="unrelated", recorded_at="fixture",
                                     from_date="2026-01-01", to_date="2026-06-30")) + "\n")
    claim = holdout._claim
    def fail(*args, **kwargs):
        if failure == "enter_after_claim":
            claim(*args, **kwargs)
        raise RuntimeError("fixture failure: " + failure)
    with monkeypatch.context() as patch:
        if failure.startswith("enter_"):
            patch.setattr(holdout, "_claim", fail)
        else:
            patch.setattr(ev001, "verify_non_holdout" if failure == "reproduction" else "holdout_input_hashes", fail)
        with pytest.raises(RuntimeError, match="fixture failure"):
            ev001.run_holdout(data_dir=repo, repo_root=repo)
    if failure != "enter_after_claim":
        assert hc003.registry_rows(repo) == design_rows
        assert hc003.ledger_experiments(repo) == {"unrelated"}
        with pytest.raises(RuntimeError, match="post-claim computation failed"):
            ev001.run_holdout(data_dir=repo, repo_root=repo)
    rows = hc003.registry_rows(repo)
    assert rows[:-1] == design_rows
    assert rows[-1]["variant"] == "holdout_ev_new20" and rows[-1]["verdict"] == "holdout_aborted"
    assert hc003.ledger_experiments(repo) == {"unrelated", ev001.EXPERIMENT_ID}
    assert ordering.release_state(rows, ev001.EXPERIMENT_ID, ev001.VARIANTS)["terminal_holdout_variants"] == ["ev_new20"]
    with pytest.raises(ValueError, match="cannot reopen"):
        ev001.run_holdout(data_dir=repo, repo_root=repo)


def test_holdout_reproduction_failure_and_unavailable_original_sensitivity_stop_preopen(repo, monkeypatch):
    holdout_mocks(repo, monkeypatch)
    def failed(*a, **k):
        raise ValueError("reproducibility mismatch")
    monkeypatch.setattr(ev001, "verify_non_holdout", failed)
    with pytest.raises(ValueError, match="reproducibility"):
        ev001.run_holdout(data_dir=repo, repo_root=repo)
    assert not (repo / holdout.LEDGER_PATH).exists()
    monkeypatch.setattr(ev001, "verify_non_holdout", lambda *a, **k: None)
    monkeypatch.setattr(ev001.financial_dates, "FinancialVersions", lambda *a, **k: SimpleNamespace(sensitivity_available=False))
    with pytest.raises(ValueError, match="sensitivity_unavailable.*no terminal release"):
        ev001.run_holdout(data_dir=repo, repo_root=repo)
    assert not (repo / holdout.LEDGER_PATH).exists()


def test_protected_hashes_frozen_preopen_and_rehashed_only_inside_EV001_session(repo):
    root = repo / "data"
    write_rows(root / "market/krx_daily/2025/20251230.jsonl", [{"trade_date": "2025-12-30", "stock_code": "000010"}])
    write_rows(root / "market/krx_daily/2026/20260102.jsonl", [{"trade_date": "2026-01-02", "stock_code": "000010"}])
    checkpoint(root)
    with pytest.raises(ValueError, match="requires an open EV001"):
        ev001.holdout_input_hashes(root, repo)
    frozen = ev001.holdout_input_hashes(root, repo, before_open=True)
    with holdout.HoldoutSession(ev001.EXPERIMENT_ID, repo_root=repo):
        assert ev001.holdout_input_hashes(root, repo) == frozen
        write_rows(root / "market/krx_daily/2026/20260102.jsonl", [{"trade_date": "2026-01-02", "stock_code": "000020"}])
        assert ev001.holdout_input_hashes(root, repo) != frozen


def test_frozen_target_scan_bounded_status_never_opens_protected_or_later_prices(repo, monkeypatch):
    root = repo / "data"
    for stamp in ("20251230", "20260102", "20260701"):
        write_rows(root / "market/krx_daily" / stamp[:4] / (stamp + ".jsonl"), [{"trade_date": pd.Timestamp(stamp).date().isoformat(), "stock_code": "000010"}])
    checkpoint(root)
    original = dart_listing.file_hash
    def checked(path):
        if "krx_daily" in str(path) and Path(path).stem > "20251230":
            pytest.fail("out-of-stage price contents hashed")
        return original(path)
    monkeypatch.setattr(dart_listing, "file_hash", checked)
    assert dart_listing.listing_status(root, price_end="20251230")["supplement_complete"]
    # Outside-stage changes cannot change the stage's price reads.
    (root / "market/krx_daily/2026/20260701.jsonl").write_text("changed\n")
    assert dart_listing.listing_status(root, price_end="20251230")["supplement_complete"]
    with pytest.raises(ValueError, match="non-protected KRX file changed"):
        monkeypatch.setattr(dart_listing, "file_hash", original)
        dart_listing.verified_target_scan(root, dart_listing._state(root / "disclosures/dart_full/list_supplement/_state.json"))


def test_real_calendar_and_cost_import_are_offline_without_runtime_or_env_initialization():
    code = '''
import sys
def audit(event, args):
    if event == 'open' and str(args[0]).split('/')[-1] in ('.env', '.env-ai'):
        raise AssertionError('private environment opened')
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise AssertionError('network attempted')
sys.addaudithook(audit)
from datetime import date
from backtesting import cost_model
from backtesting.experiments import ev001
assert ev001.EXPERIMENT_ID == 'EV001_earnings_filing_entry'
cost_model.one_way_cost(10000., 'KOSPI', date(2025, 5, 16), 2e9, 'sell', model_version='v2')
assert 'src.runner.analysis_scheduler' not in sys.modules
assert 'src.runner.theme_leader_trading_runner' not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr


def test_small_complete_design_replays_without_real_data_results_and_default_cli_is_dry(repo, monkeypatch):
    _, listings = financial_fixture(repo)
    for row in listings:
        write_rows(repo / "disclosures/dart_full/list" / row["rcept_dt"][:4] / (row["rcept_dt"] + ".jsonl"), [row])
    checkpoint(repo)
    days = calendar("2023-01-01", "2025-12-30")
    prices = prices_for(days)
    payload = ev001.compute_design(data_dir=repo, repo_root=repo, sessions=days, prices=prices)
    assert payload["prereg_commit"].startswith("7acd752")
    assert set(payload["rules"]) == {"S", "O"} and set(payload["variants"]) == set(ev001.VARIANTS)
    assert payload["rules"]["S"]["ev_new20"]["event_count"] == 1
    assert payload["rules"]["S"]["ev_new20_f"]["event_count"] == 0
    assert payload["design_effects"]["S"]["ev_new20"] is None
    assert all(r["verdict"] == "insufficient" for r in payload["variants"].values())
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()
    with ordering.design_attempt(ev001.EXPERIMENT_ID, ev001.VARIANTS, repo) as attempt:
        second = ev001.compute_design(data_dir=repo, repo_root=repo, sessions=days, prices=prices, _attempt=attempt)
    assert second["replay_sha256"] == payload["replay_sha256"]
    assert json.dumps({k: v for k, v in second.items() if k != "performance"}, sort_keys=True).encode() == json.dumps(
        {k: v for k, v in payload.items() if k != "performance"}, sort_keys=True).encode()
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()
    # Default CLI dispatch never chooses execution or a holdout.
    def dry(**kwargs):
        raise RuntimeError("dry dispatch")
    monkeypatch.setattr(ev001, "dry_run", dry)
    with pytest.raises(RuntimeError, match="dry dispatch"):
        ev001.main([])
    with pytest.raises(SystemExit):
        ev001.main(["--execute", "--holdout"])


def test_both_rules_and_all_eligible_variants_finish_in_one_guarded_holdout_session(repo, monkeypatch):
    passed = ("ev_new20", "ev_new20_f")
    holdout_mocks(repo, monkeypatch, passed=passed)
    hashes, queries = [], []
    def frozen(*a, before_open=False, **k):
        hashes.append(before_open)
        if not before_open:
            assert len(holdout._SESSIONS.get()) == 1
        return {"frozen": "a"}
    monkeypatch.setattr(ev001, "holdout_input_hashes", frozen)
    guard = holdout.guard_period
    def checked(start, end, **kwargs):
        if pd.Timestamp(end) >= pd.Timestamp("2026-01-01"):
            assert kwargs["experiment_id"] == ev001.EXPERIMENT_ID
            assert pd.Timestamp(end) <= pd.Timestamp("2026-06-30")
            queries.append((start, end))
        return guard(start, end, **kwargs)
    monkeypatch.setattr(holdout, "guard_period", checked)
    status = {"listing_version": {"fixture": True}}
    def prepare(**kwargs):
        assert len(holdout._SESSIONS.get()) == 1
        days = calendar("2025-10-01", "2026-06-30")
        codes = [f"{n:05d}0" for n in range(65)]
        prices = prices_for(days, codes)
        for code in codes[:15]:
            prices.loc[(slice(None), code), "ret_1d"] = .002
        book, _ = make_book(repo, days, prices)
        entry_dates = [days[days.searchsorted(pd.Timestamp(stamp))].date().isoformat()
                       for stamp in ("2026-01-05", "2026-02-16")]
        events = [event(day, code, public_date=(pd.Timestamp(day) - pd.Timedelta(days=1)).date().isoformat(),
                        decision_date=ev001.decision_date(day, days).date().isoformat())
                  for day in entry_dates for code in codes[:15]]
        prepared = prepared_for(book, events)
        prepared.update(versions={r: SimpleNamespace(sensitivity_available=True, annotations=[], report=lambda: {}) for r in ev001.RULES},
            dates=SimpleNamespace(L=17), coverage={}, listing_status=status, event_counts={},
            universe_counts={r: [] for r in ev001.RULES}, raise_counts={})
        return prepared
    monkeypatch.setattr(ev001, "prepare", prepare)
    monkeypatch.setattr(dart_listing, "listing_status", lambda *a, **k: status)
    result = ev001.run_holdout(data_dir=repo, repo_root=repo)
    assert set(result["variants"]) == set(passed) and result["verdict"] == "pass"
    assert all(result["rules"][r][v]["verdict"] == "pass" for r in ev001.RULES for v in passed)
    assert all(result["rules"][r][v]["event_count"] == 30 for r in ev001.RULES for v in passed)
    assert queries and hashes == [True, False, False]
    assert len((repo / holdout.LEDGER_PATH).read_text().splitlines()) == 1
    released = ordering.release_state(hc003.registry_rows(repo), ev001.EXPERIMENT_ID, ev001.VARIANTS)
    assert released["terminal_holdout_variants"] == sorted(passed)


def test_design_only_reference_uses_entry_month_even_with_validation_exit(repo):
    book, _ = make_book(repo, calendar("2024-10-01", "2025-04-30"))
    events = [event("2024-12-20", entry_month="2024-12", public_date="2024-12-19", decision_date="2024-11-29"),
              event("2025-02-03", code="000020", entry_month="2025-02", public_date="2025-02-02", decision_date="2025-01-31")]
    prepared = prepared_for(book, events)
    for code in book.raw:
        book.raw[code][:, 4] = .001 if code == "000010" else 0
    # Rebuild adjusted marks with the synthetic return change.
    book.finish()
    selected, _ = ev001.select_events(prepared, "S", "ev_new20")
    main, _ = ev001.variant_metrics(prepared, "S", "ev_new20", selected)
    assert main["events"][0]["scheduled_exit_date"].startswith("2025-")
    assert main["design"]["primary"]["mean"] == main["events"][0]["excess"]
    assert main["validation"]["primary"]["mean"] == main["events"][1]["excess"]
    assert main["primary"]["mean"] != main["design"]["primary"]["mean"]


def test_prepare_fails_missing_whole_market_session_and_incomplete_supplement(repo, monkeypatch):
    _, listings = financial_fixture(repo)
    for row in listings:
        write_rows(repo / "disclosures/dart_full/list" / row["rcept_dt"][:4] / (row["rcept_dt"] + ".jsonl"), [row])
    days = calendar("2025-01-01", "2025-12-30")
    prices = prices_for(days)
    with pytest.raises(ValueError, match="supplement incomplete"):
        ev001.prepare(data_dir=repo, repo_root=repo, sessions=days, stage="design_validation", prices=prices)
    checkpoint(repo)
    missing = prices.loc[prices.index.get_level_values("trade_date") != days[30]]
    with pytest.raises(ValueError, match="whole-market price sessions"):
        ev001.prepare(data_dir=repo, repo_root=repo, sessions=days, stage="design_validation", prices=missing)


def test_holdout_actual_cutoff_and_entry_exit_bounds_apply_under_both_rules(repo):
    records, listings = financial_fixture(repo)
    current = records[-1]
    records[-1] = {**current, "bsns_year": 2026, "fiscal_quarter": "2026Q1", "rcept_no": "20260630000002", "available_date": "2026-06-30"}
    save(repo, *records)
    listed = [*listings, filing("20260515", title="분기보고서 (2026.03)"),
              filing("20260630", "20260701", "[정정]분기보고서 (2026.03)", number=2)]
    for rule in ev001.RULES:
        events, _, loaded = events_for(repo, listed, rule, "holdout")
        assert not any(e["fiscal_quarter"] == "2026Q1" for e in events)
        assert loaded.counts["stage_cutoff"] == 1
    with holdout.HoldoutSession(ev001.EXPERIMENT_ID, repo_root=repo):
        book, _ = make_book(repo, calendar("2025-12-01", "2026-06-30"))
        rows = [event("2026-06-29", public_date="2026-06-28", decision_date="2026-05-29")]
        assert ev001.select_events(prepared_for(book, rows), "S", "ev_new20")[1]["total"]["scheduled_exit_outside_stage"] == 1


def test_zero_original_links_makes_O_unavailable_and_no_variant_passes_design(repo):
    book, _ = make_book(repo)
    prepared = prepared_for(book, [event()])
    prepared["versions"] = {r: SimpleNamespace(sensitivity_available=False) for r in ev001.RULES}
    result = ev001.evaluate(prepared, ev001._registration(repo).fields, trials=3)
    assert all(v["verdict"] == "sensitivity_unavailable" for v in result["variants"].values())
    assert set(result["rules"]["O"]) == set(ev001.VARIANTS)
    assert all(v["judgement_inputs"]["p_for_holm"] == 1 for v in result["rules"]["O"].values())
    assert result["sensitivity_unavailable_reasons"]["O"]


def test_dry_run_prints_counts_listing_L_and_estimates_without_local_outcomes_or_writes(repo, monkeypatch, capsys):
    book, _ = make_book(repo)
    prepared = prepared_for(book, [event()])
    prepared.update(versions={r: SimpleNamespace(report=lambda: {}) for r in ev001.RULES},
        dates=SimpleNamespace(L=17), event_counts={r: {} for r in ev001.RULES}, coverage={}, price_paths=[],
        listing_status={"supplement_complete": True, "reason": None, "completed_corps": 1, "target_count": 1})
    def prepare(**kwargs):
        assert kwargs["dry"] and kwargs["stage"] == "design_validation"
        return prepared
    monkeypatch.setattr(ev001, "prepare", prepare)
    monkeypatch.setattr(book, "outcomes", lambda *a, **k: pytest.fail("local event return evaluated during dry run"))
    plan = ev001.dry_run(data_dir=repo, repo_root=repo)
    assert plan["event_counts"]["S"]["ev_new20"]["total"]["selected"] == 1
    assert plan["fixture_timing"]["paths"] == ev001.N_CONTROLS
    assert plan["projection"]["peak_rss_mib"] < ev001.MAX_RSS_BYTES / 1024**2
    monkeypatch.setattr(ev001, "dry_run", lambda **kwargs: plan)
    assert ev001.main([]) == 0
    output = capsys.readouterr().out
    assert "L=17" in output and "per rule/variant/year/reason" in output and "Projected full runtime/RSS" in output
    assert not (repo / experiment_registry.REGISTRY_PATH).exists()
    assert not (repo / holdout.LEDGER_PATH).exists()
    assert not list(repo.rglob("results/*"))
