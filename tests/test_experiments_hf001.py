"""Synthetic, offline checks of the immutable HF001 rules and publication."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry
from backtesting.experiments import common, hc001, hc002, hf001
from src.ingestion import dart_quarterly


def market(start="2023-10-02", end="2025-12-31", codes=None):
    days = pd.bdate_range(start, end)
    codes = codes or [f"{number * 10:06d}" for number in range(1, 11)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    logs = np.where(np.arange(len(days))[:, None] % 2, 1, -1) * np.arange(1, len(codes) + 1)[None, :] / 1000
    prices = pd.DataFrame({"open": 10000.0, "close": 10000.0, "base_price": 10000.0,
                           "volume": 10000.0, "ret_1d": np.expm1(logs).ravel(), "trading_value": 2e9,
                           "market": np.tile(["KOSPI" if i < 5 else "KOSDAQ" for i in range(len(codes))], len(days)),
                           "stock_name": "합성보통주", "calendar_status": "verified"}, index=index)
    return prices, days


def filing(code, year, revenue, income, day, number=1):
    return {"stock_codes": [code], "corp_code": f"{int(code):08d}", "bsns_year": year,
            "reprt_code": "11013", "fiscal_quarter": f"{year}Q1", "fs_div": "CFS",
            "available_date": day, "rcept_no": day.replace("-", "") + f"{number:06d}", "currency": "KRW",
            "raw_accounts": {metric: {"thstrm_amount": str(value), "thstrm_add_amount": str(value),
                                     "thstrm_dt": None, "currency": "KRW"}
                             for metric, value in (("revenue", revenue), ("operating_income", income), ("net_income", 1))}}


def archive(data_dir, records):
    directory = data_dir / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True, exist_ok=True)
    groups = {}
    for row in records:
        groups.setdefault(f"{row['bsns_year']}_{row['reprt_code']}.jsonl", []).append(row)
    for name, rows in groups.items():
        (directory / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def financials(data_dir, codes):
    archive(data_dir, [filing(code, year, revenue, income, f"{year}-05-15", number=int(code))
                      for code in codes for year, revenue, income in ((2022, 80, 8), (2023, 100, 12), (2024, 150, 30))])


def listing_row(day, title="유상증자결정", code="000010", number=1):
    day = pd.Timestamp(day).strftime("%Y%m%d")
    return {"rcept_no": day + f"{number:06d}", "rcept_dt": day, "stock_code": code, "report_nm": title}


def listing_archive(data_dir, start, end, rows=()):
    files = {}
    for day in pd.date_range(start, end):
        path = data_dir / "disclosures/dart_full/list" / day.strftime("%Y") / (day.strftime("%Y%m%d") + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows
                                if row["rcept_dt"] == day.strftime("%Y%m%d")), encoding="utf-8")
        files[day] = path
    return files


def selections(prices, days, day="2024-05-31", phases=None):
    codes = prices.index.get_level_values("stock_code").unique().tolist()
    decision = pd.Timestamp(day)
    return pd.DataFrame({"stock_code": codes, "decision_date": decision,
                         "trade_date": days[days.get_loc(decision) + 1], "phase": phases or [2] * len(codes),
                         "avg_trading_value_20d": 2e9, "f_vol": False, "f_raise": False,
                         "f_both": False, "raise_covered": True})


@pytest.fixture
def fields():
    return experiment_registry.verify_preregistration(hf001.EXPERIMENT_ID).fields


@pytest.fixture
def registered_repo(tmp_path):
    for experiment_id in (hf001.EXPERIMENT_ID, hc002.EXPERIMENT_ID):
        relative = Path("research/experiments") / experiment_id / "preregistration.md"
        target = tmp_path / relative
        target.parent.mkdir(parents=True)
        target.write_bytes((experiment_registry.PROJECT_ROOT / relative).read_bytes())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "research"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=HF001 test", "-c", "user.email=hf001@example.invalid",
                    "commit", "-qm", "immutable synthetic preregistrations"], check=True)
    return tmp_path


def test_committed_registration_is_required_and_never_edited(registered_repo):
    registration = hf001._registration(registered_repo)
    assert registration.fields["experiment_id"] == hf001.EXPERIMENT_ID
    path = registered_repo / "research/experiments" / hf001.EXPERIMENT_ID / "preregistration.md"
    path.write_text(path.read_text() + "changed plan\n")
    with pytest.raises(ValueError, match="git check"):
        hf001._registration(registered_repo)


def test_calendar_is_exactly_hc002_and_entry_counts_as_day_one():
    sessions = pd.bdate_range("2015-01-01", "2026-03-31").drop(pd.Timestamp("2024-05-31"))
    planned, excluded = hc002.rebalance_calendar(sessions)
    assert len(planned) == 103 and not excluded
    may = next(row for row in planned if row["month"] == "2024-05")
    assert may["decision_date"] == "2024-05-30" and may["trade_date"] == "2024-06-03"
    assert sessions[sessions.get_loc(pd.Timestamp(may["trade_date"])) + 19] == pd.Timestamp(may["exit_date"])


def test_universe_order_financial_denominator_and_p_exact_hc002(tmp_path):
    prices, days = market(codes=[f"{i * 10:06d}" for i in range(1, 7)])
    financials(tmp_path, [f"{i * 10:06d}" for i in range(1, 6)])
    profile = tmp_path / "reference/dart_company/companies.jsonl"
    profile.parent.mkdir(parents=True)
    profile.write_text("".join(json.dumps({"stock_code": f"{i * 10:06d}", "induty_code": "64" if i == 1 else "261"}) + "\n"
                               for i in range(1, 5)))
    events, _ = hf001.rights_events(pd.DataFrame(columns=hf001.LISTING_COLUMNS), days)
    universe, counts, reports, _, _ = hf001.build_selections(
        prices, data_dir=tmp_path, repo_root=experiment_registry.PROJECT_ROOT, sessions=days, listing_files={}, events=events)
    day = pd.Timestamp("2024-05-31")
    row = next(row for row in counts if row["decision_date"] == str(day.date()))
    assert row["price_eligible"] == 6 and row["no_data"] == 1
    assert row["industry_profiles"]["universe_size"] == 5
    assert row["industry_profiles"]["profile_coverage"] == 0.8
    assert row["industry_profiles"]["financials_excluded"] == 1
    actual = universe.loc[universe.decision_date.eq(day)]
    reference, _ = hc002.universe_on_decision(prices, day, data_dir=tmp_path)
    assert set(actual.stock_code) == set(reference.index) == {"000020", "000030", "000040", "000050"}
    report = next(row for row in reports if row["decision_date"] == str(day.date()))
    assert report["hc002_phase2_equal"] and report["P"] == sorted(reference.index[reference.phase.eq(2)])


def test_phase2_disagreement_stops_before_evaluation(tmp_path, monkeypatch):
    prices, days = market(codes=["000010"])
    financials(tmp_path, ["000010"])
    original = hc002.build_universe
    def changed(*args, **kwargs):
        universe, *rest = original(*args, **kwargs)
        universe.loc[universe.decision_date.eq(pd.Timestamp("2024-05-31")), "phase"] = 3
        return universe, *rest
    monkeypatch.setattr(hc002, "build_universe", changed)
    events, _ = hf001.rights_events(pd.DataFrame(columns=hf001.LISTING_COLUMNS), days)
    with pytest.raises(AssertionError, match="P differs from HC002"):
        hf001.build_selections(prices, data_dir=tmp_path, repo_root=experiment_registry.PROJECT_ROOT,
                              sessions=days, listing_files={}, events=events)


def test_actual_filter_sets_are_union_and_only_known_original_events(tmp_path):
    prices, days = market()
    codes = prices.index.get_level_values("stock_code").unique()
    financials(tmp_path, codes)
    listings = pd.DataFrame([listing_row("2024-05-30", code="000010"),
                            listing_row("2024-05-31", code="000020"),
                            listing_row("2024-05-30", "[첨부정정]유상증자결정", "000030", 2),
                            listing_row("2024-05-30", "유상증자결정 철회", "000040", 3)])
    files = listing_archive(tmp_path, "2023-01-01", "2024-05-31", listings.to_dict("records"))
    events, counts = hf001.rights_events(listings, days)
    result, _, _, _, _ = hf001.build_selections(prices, data_dir=tmp_path,
        repo_root=experiment_registry.PROJECT_ROOT, sessions=days, listing_files=files, events=events)
    may = result.loc[result.decision_date.eq(pd.Timestamp("2024-05-31"))]
    assert set(may.loc[may.f_vol, "stock_code"]) == {"000050", "000100"}
    assert set(may.loc[may.f_raise, "stock_code"]) == {"000010"}
    assert set(may.loc[may.f_both, "stock_code"]) == {"000010", "000050", "000100"}
    assert may.raise_covered.all() and counts["withdrawal_titles"] == 1


def test_volatility_ranks_use_all_u_including_non_phase2_names(tmp_path):
    prices, days = market(codes=[f"{i * 10:06d}" for i in range(1, 6)])
    codes = prices.index.get_level_values("stock_code").unique()
    financials(tmp_path, codes)
    known = filing("000050", 2024, 150, 15, "2024-05-16", 2)
    path = tmp_path / "fundamentals/dart_quarterly/2024_11013.jsonl"
    with path.open("a") as handle:
        handle.write(json.dumps(known) + "\n")
    events, _ = hf001.rights_events(pd.DataFrame(columns=hf001.LISTING_COLUMNS), days)
    result, _, _, _, _ = hf001.build_selections(prices, data_dir=tmp_path,
        repo_root=experiment_registry.PROJECT_ROOT, sessions=days, listing_files={}, events=events)
    may = result.loc[result.decision_date.eq(pd.Timestamp("2024-05-31"))].set_index("stock_code")
    assert may.loc["000050", "phase"] == 3 and may.loc["000050", "f_vol"]
    assert not may.loc[may.phase.eq(2), "f_vol"].any()  # The fourth U rank is 0.8, not 1.0 within P.


def test_no_future_financials_disclosures_or_price_signals(tmp_path):
    prices, days = market(codes=["000010", "000020"])
    financials(tmp_path, ["000010", "000020"])
    day = pd.Timestamp("2024-05-31")
    before, _ = hc002.universe_on_decision(prices, day, data_dir=tmp_path)
    archive(tmp_path, [filing("000010", 2023, 100, 12, "2023-05-15"),
                      filing("000010", 2024, 150, 30, "2024-05-15"),
                      filing("000010", 2024, 200, 1, "2024-05-31", 2),
                      filing("000010", 2024, 300, 1, "2024-06-03", 3)])
    after, _ = hc002.universe_on_decision(prices, day, data_dir=tmp_path)
    assert after.loc["000010", "phase"] == before.loc["000010", "phase"] == 2
    assert after.loc["000010", "rcept_no"] == "20240515000001"
    vol = hf001.volatility_filter(prices, after, day, days)
    future = prices.index.get_level_values("trade_date") > day
    prices.loc[future, ["ret_1d", "close", "trading_value"]] = [0.9, 1, 0]
    again, _ = hc002.universe_on_decision(prices, day, data_dir=tmp_path)
    pd.testing.assert_frame_equal(after, again)
    pd.testing.assert_frame_equal(vol, hf001.volatility_filter(prices, again, day, days))
    listings = pd.DataFrame([listing_row("2024-05-31"), listing_row("2024-06-03", number=2)])
    events, _ = hf001.rights_events(listings, days)
    assert events.known_position.gt(days.get_loc(day)).all()


def test_volatility_sample_std_market_ranks_max_ties_and_strict_80_percentile():
    prices, days = market()
    day = pd.Timestamp("2024-05-31")
    codes = prices.index.get_level_values("stock_code").unique()
    universe = pd.DataFrame(index=codes)
    result = hf001.volatility_filter(prices, universe, day, days)
    log = np.log1p(prices.loc[(slice(None, day), "000050"), "ret_1d"].tail(60))
    assert result.loc["000050", "volatility_60d"] == pytest.approx(log.std(ddof=1))
    assert result.loc["000040", "percentile"] == 0.8 and not result.loc["000040", "f_vol"]
    assert result.loc["000050", "f_vol"] and result.loc["000100", "f_vol"]
    assert result.f_vol.sum() == 2  # Top one of each five-stock market.
    history = prices.index.get_level_values("trade_date") <= day
    target = history & (prices.index.get_level_values("stock_code") == "000040")
    source = history & (prices.index.get_level_values("stock_code") == "000050")
    prices.loc[target, "ret_1d"] = prices.loc[source, "ret_1d"].to_numpy()
    tied = hf001.volatility_filter(prices, universe, day, days)
    assert tied.loc[["000040", "000050"], "percentile"].eq(1.0).all()
    assert tied.loc[["000040", "000050"], "f_vol"].all()


@pytest.mark.parametrize("problem", ["missing_return", "missing_row", "unverified", "invalid_log"])
def test_missing_any_of_sixty_returns_is_uncomputable_and_not_excluded(problem):
    prices, days = market(codes=["000010"])
    day = pd.Timestamp("2024-05-31")
    oldest = days[days.get_loc(day) - 59]
    if problem == "missing_row":
        prices = prices.drop((oldest, "000010"))
    elif problem == "unverified":
        prices.loc[(oldest, "000010"), "calendar_status"] = "unverified"
    else:
        prices.loc[(oldest, "000010"), "ret_1d"] = np.nan if problem == "missing_return" else -1
    result = hf001.volatility_filter(prices, pd.DataFrame(index=["000010"]), day, days)
    assert pd.isna(result.loc["000010", "volatility_60d"])
    assert not result.loc["000010", "f_vol"]


def test_decision_return_is_included_but_sixty_first_old_return_is_not():
    prices, days = market(codes=["000010"])
    day = pd.Timestamp("2024-05-31")
    universe = pd.DataFrame(index=["000010"])
    before = hf001.volatility_filter(prices, universe, day, days)
    prices.loc[(days[days.get_loc(day) - 60], "000010"), "ret_1d"] = 0.9
    pd.testing.assert_frame_equal(before, hf001.volatility_filter(prices, universe, day, days))
    prices.loc[(day, "000010"), "ret_1d"] = 0.9
    assert hf001.volatility_filter(prices, universe, day, days).volatility_60d.iloc[0] > before.volatility_60d.iloc[0]


def test_original_rights_only_next_session_corrections_and_withdrawal_do_not_restart():
    days = pd.bdate_range("2023-01-02", "2025-12-31").drop(pd.Timestamp("2024-05-06"))
    original = listing_row("2024-05-03")
    listings = pd.DataFrame([original, original, listing_row("2024-05-04", "[기재정정]유상증자결정", number=2),
                            listing_row("2024-05-05", "[첨부정정]유상증자결정", number=3),
                            listing_row("2024-05-07", "유상증자결정(무상포함)", number=4),
                            listing_row("2024-05-08", "무상증자결정", number=5),
                            listing_row("2024-05-09", "유상증자결정철회", number=6),
                            listing_row("2024-05-10", "기타 철회신고", number=7),
                            listing_row("2024-05-13", code=None, number=8)])
    events, counts = hf001.rights_events(listings, days)
    assert len(events) == 1 and events.iloc[0].known_date == pd.Timestamp("2024-05-07")
    assert counts["duplicate_receipts"] == 1 and counts["withdrawal_titles"] == 2
    assert counts["correction_titles"] == 2 and counts["bonus_titles"] == 1
    assert counts["missing_stock_code_events"] == 1
    known = events.known_position.iloc[0]
    assert len(events.loc[events.known_position.between(known - 125, known)]) == 1
    assert len(events.loc[events.known_position.between(known + 125 - 125, known + 125)]) == 1
    assert not len(events.loc[events.known_position.between(known + 126 - 125, known + 126)])


def test_conflicting_receipt_is_an_error_not_a_new_event():
    first = listing_row("2024-05-03")
    with pytest.raises(ValueError, match="conflicting duplicate"):
        hf001.rights_events(pd.DataFrame([first, {**first, "report_nm": "다른 유상증자결정"}]), pd.bdate_range("2024-01-01", "2024-12-31"))


def test_listing_entire_window_includes_weekends_zero_files_and_internal_gaps(tmp_path):
    days = pd.bdate_range("2015-01-01", "2025-12-31")
    day = pd.Timestamp("2024-05-31")
    position = days.get_loc(day)
    start = days[position - 126]
    files = listing_archive(tmp_path, start, day - pd.Timedelta(days=1))
    covered = hf001.listing_coverage(day, days, files)
    assert covered["covered"] and covered["required_days"] > 126
    assert covered["receipt_start"] == str(start.date())
    assert covered["known_window_start"] == str(days[position - 125].date())
    weekend = next(date for date in files if date.weekday() == 6)
    files[weekend].unlink()
    files = hf001._listing_files(tmp_path)
    missing = hf001.listing_coverage(day, days, files)
    assert not missing["covered"] and missing["missing_days"] == [str(weekend.date())]
    assert not hf001.listing_coverage(days[125], days, files)["covered"]


@pytest.mark.parametrize("entry_date,nominal", [("2015-06-12", 0.15), ("2015-06-15", 0.30), ("2024-06-03", 0.30)])
def test_date_specific_limit_up_excludes_fill_and_keeps_frozen_cash_slot(entry_date, nominal):
    prices, days = market("2015-01-01", "2025-12-31", ["000010", "000020"])
    entry = pd.Timestamp(entry_date)
    decision = days[days.get_loc(entry) - 1]
    universe = selections(prices, days, decision)
    assert hf001.price_limit(entry) == nominal
    prices.loc[(entry, "000010"), "open"] = 10000 * (1 + nominal - 0.005)
    rows = hf001.forward_observations(prices, universe, days).set_index("stock_code")
    assert rows.loc["000010", "reason"] == "limit_up_entry"
    assert rows.loc["000010", "gross"] == rows.loc["000010", "cost_1.0"] == 0
    assert not rows.loc["000010", "filled"] and rows.loc["000020", "filled"]
    assert len(rows) == len(universe)  # No weight redistributed to the other stock.


@pytest.mark.parametrize("exit_date,nominal", [("2015-06-12", 0.15), ("2015-06-15", 0.30), ("2024-06-28", 0.30)])
def test_date_specific_limit_down_is_not_used_as_exit_fill(exit_date, nominal):
    prices, days = market("2015-01-01", "2025-12-31", ["000010"])
    target = days.get_loc(pd.Timestamp(exit_date))
    decision = days[target - 20]
    universe = selections(prices, days, decision)
    prices.loc[(days[target], "000010"), "close"] = 10000 * (1 - nominal + 0.005)
    rows = hf001.forward_observations(prices, universe, days)
    assert rows.limit_down_exit.iloc[0] and rows.stale_exit.iloc[0]
    assert rows.exit_date.iloc[0] == days[target - 1]
    assert np.isfinite(rows.gross.iloc[0]) and rows.filled.iloc[0]


@pytest.mark.parametrize("entry_problem", ["missing", "zero_volume", "zero_open"])
def test_entry_exclusions_never_fill_or_pay_costs(entry_problem):
    prices, days = market(codes=["000010", "000020"])
    universe = selections(prices, days)
    entry = universe.trade_date.iloc[0]
    if entry_problem == "missing":
        prices = prices.drop((entry, "000010"))
    else:
        prices.loc[(entry, "000010"), "volume" if entry_problem == "zero_volume" else "open"] = 0
    row = hf001.forward_observations(prices, universe, days).iloc[0]
    assert not row.filled and row.gross == row.gross_delisting_loss == 0
    assert all(row[f"cost_{m}"] == 0 for m in hf001.MULTIPLIERS)


def test_halt_and_delisting_keep_original_names_weights_and_last_close_not_resume_price():
    prices, days = market(codes=["000010", "000020", "000030"])
    universe = selections(prices, days)
    entry = days.get_loc(universe.trade_date.iloc[0])
    last = days[entry + 10]
    target = days[entry + 19]
    for code in ("000010", "000020"):
        prices.loc[(last, code), "close"] = 12000
        prices.loc[(days[entry + 3], code), "ret_1d"] = np.nan
    prices = prices.drop([(day, "000010") for day in days[entry + 11:entry + 23]])
    prices = prices.drop([(day, "000020") for day in days[entry + 11:]])
    prices.loc[(days[entry + 23], "000010"), "close"] = 90000
    rows = hf001.forward_observations(prices, universe, days).set_index("stock_code")
    assert len(rows) == 3 and rows.filled.all() and rows.reason.isna().all()
    assert rows.loc["000010", "halted_exit"] and not rows.loc["000010", "delisted"]
    assert rows.loc["000020", "delisted"] and not rows.loc["000020", "halted_exit"]
    assert rows.loc["000010", "exit_date"] == rows.loc["000020", "exit_date"] == last
    assert rows.loc["000010", "scheduled_exit_date"] == target
    assert rows.loc["000010", "gross"] == rows.loc["000020", "gross"] == pytest.approx(0.2)
    assert rows.loc["000020", "gross_delisting_loss"] == -1
    assert rows.loc["000010", "gross_delisting_loss"] == pytest.approx(0.2)
    assert rows.loc[["000010", "000020"], "unadjusted_fallback"].all()
    prices.loc[(days[entry + 23], "000010"), "close"] = 1000000
    again = hf001.forward_observations(prices, universe, days).set_index("stock_code")
    assert again.loc["000010", "gross"] == rows.loc["000010", "gross"]


def test_zero_volume_future_rows_are_not_evidence_of_trading_resumption():
    prices, days = market(codes=["000010"])
    universe = selections(prices, days)
    entry = days.get_loc(universe.trade_date.iloc[0])
    prices.loc[(slice(days[entry + 5], None), "000010"), "volume"] = 0
    row = hf001.forward_observations(prices, universe, days).iloc[0]
    assert row.delisted and row.exit_date == days[entry + 4] and row.gross_delisting_loss == -1


def test_adjusted_return_chain_exit_horizon_and_costs_use_true_entry_date():
    prices, days = market("2024-01-01", "2025-12-31", ["000010"])
    universe = selections(prices, days, "2024-12-31")
    entry = days.get_loc(universe.trade_date.iloc[0])
    target = entry + 19
    prices.loc[(days[entry], "000010"), ["open", "close"]] = [10500, 11000]
    prices.loc[(days[target], "000010"), "close"] = 5000  # Raw split price must not distort the adjusted return.
    prices.loc[(days[target], "000010"), "base_price"] = 5000
    row = hf001.forward_observations(prices, universe, days).iloc[0]
    expected = 11000 / 10500 * np.prod(1 + prices.loc[(slice(days[entry + 1], days[target]), "000010"), "ret_1d"]) - 1
    assert row.gross == pytest.approx(expected) and not row.unadjusted_fallback
    assert row.exit_date == days[target]
    for m in hf001.MULTIPLIERS:
        assert row[f"cost_{m}"] == cost_model.round_trip_cost(10500.0, "KOSPI", days[entry].date(), 2e9, multiplier=m)
    # Only the non-tax costs scale: the 2025 tax of 0.0015 remains fixed.
    assert row["cost_2.0"] == pytest.approx(2 * row["cost_1.0"] - 0.0015)


def metric_frame(months=40, stocks=50, delist=False):
    rows = []
    dates = pd.date_range("2021-12-31", periods=months, freq="ME")
    for number, day in enumerate(dates):
        for stock in range(stocks):
            gross = -0.3 - (number % 3) * 0.1 if stock == 0 else 0.03 + (number % 5) * 0.001
            rows.append({"decision_date": day, "stock_code": f"{stock * 10:06d}", "phase": 2,
                         "gross": gross, "gross_delisting_loss": -1.0 if delist and stock else gross,
                         "delisted": delist and stock > 0, "raise_covered": True,
                         "f_vol": stock == 0, "f_raise": stock == 0, "f_both": stock == 0,
                         "bucket": stock % 4, **{f"cost_{m}": 0.001 * m + 0.0015 for m in hf001.MULTIPLIERS}})
    return pd.DataFrame(rows)


def test_filter_only_removes_reweights_cash_and_charges_benchmark_full_costs():
    frame = metric_frame(months=3, stocks=3)
    dates = sorted(frame.decision_date.unique())
    frame.loc[frame.decision_date.eq(dates[0]), "f_vol"] = False
    frame.loc[frame.decision_date.eq(dates[2]), "f_vol"] = True
    result = hf001.variant_metrics(frame, "f_vol")
    first, middle, cash = result["per_month"]
    assert first["costs"]["1.0"]["difference"] == 0 and len(first["remaining"]) == 3
    assert middle["remaining"] == ["000010", "000020"] and middle["removed"] == ["000000"]
    expected = frame.loc[frame.decision_date.eq(dates[1]) & ~frame.f_vol]
    assert middle["costs"]["1.0"]["filtered_net"] == pytest.approx((expected.gross - expected["cost_1.0"]).mean())
    assert middle["costs"]["1.0"]["universe_net"] == middle["costs"]["1.0"]["baseline_net"]
    assert cash["cash"] and cash["remaining"] == []
    assert all(cash["costs"][str(m)]["filtered_net"] == cash["costs"][str(m)]["filtered_cost"] == 0 for m in hf001.MULTIPLIERS)
    assert cash["costs"]["1.0"]["difference"] == -cash["costs"]["1.0"]["baseline_net"]
    assert result["cash_months"] == 1 and result["filtered_months"] == 2 and result["removed_stock_cohorts"] == 4
    assert first["removed_minus_remaining"] is cash["removed_minus_remaining"] is None


def test_uncovered_listing_months_exclude_raise_and_both_but_preserve_vol():
    frame = metric_frame(months=3, stocks=3)
    frame.loc[frame.decision_date.eq(frame.decision_date.min()), "raise_covered"] = False
    assert hf001.variant_metrics(frame, "f_vol")["primary"]["count"] == 3
    for variant in ("f_raise", "f_both"):
        result = hf001.variant_metrics(frame, variant)
        assert result["primary"]["count"] == 2
        assert result["random_control"]["months"] == 2


def test_random_controls_golden_path_major_default_rng_zero_sorted_no_replacement():
    frame = metric_frame(months=4, stocks=4).sample(frac=1, random_state=11)
    dates = sorted(frame.decision_date.unique())
    frame.loc[frame.decision_date.eq(dates[0]), "f_vol"] = False
    frame.loc[frame.decision_date.eq(dates[1]), "f_vol"] = True
    frame.loc[frame.decision_date.eq(dates[2]), "f_vol"] = frame.stock_code.isin(["000000", "000010"])
    result = hf001.variant_metrics(frame, "f_vol")
    rng = np.random.default_rng(0)
    expected = []
    for _ in range(200):
        path = []
        for day in dates:
            group = frame.loc[frame.decision_date.eq(day)].sort_values("stock_code")
            net = (group.gross - group["cost_1.0"]).to_numpy()
            removed = rng.choice(len(net), size=int(group.f_vol.sum()), replace=False)
            remaining = np.delete(net, removed)
            path.append((remaining.mean() if len(remaining) else 0) - net.mean())
        expected.append(float(np.mean(path)))
    assert result["random_control"]["path_means"] == expected
    actual = result["primary"]["mean"]
    assert result["random_control"]["share_of_controls"] == sum(value >= actual for value in expected) / 200
    assert result["random_control"] == hf001.variant_metrics(frame.iloc[::-1], "f_vol")["random_control"]


def test_newey_west_bartlett_lag_one_no_sample_correction_normal_one_sided():
    values = np.array([0.01, 0.04, 0.03, -0.01, 0.02, 0.06])
    result = hf001.newey_west(values)
    centered = values - values.mean()
    gamma0 = np.dot(centered, centered) / len(values)
    gamma1 = np.dot(centered[1:], centered[:-1]) / len(values)
    variance = (gamma0 + 2 * 0.5 * gamma1) / len(values)
    t = values.mean() / math.sqrt(variance)
    assert result["standard_error"] == pytest.approx(math.sqrt(variance))
    assert result["t_stat"] == pytest.approx(t)
    assert result["p_one_sided"] == pytest.approx(0.5 * math.erfc(t / math.sqrt(2)))
    assert result["t_stat"] != pytest.approx(values.mean() / (values.std(ddof=1) / math.sqrt(len(values))))
    assert hf001.newey_west(-values)["p_one_sided"] == pytest.approx(1 - result["p_one_sided"])


@pytest.mark.parametrize("values", [[], [0.1], [0.0] * 40, [0.08] * 40, [0.1] * 36, [0.003] * 37])
def test_uncomputable_tests_have_p_one_and_no_t(values):
    result = hf001.newey_west(values)
    assert result["p_one_sided"] == 1 and result["t_stat"] is None


def test_holm_family_three_includes_uncomputable_p_one_and_stepdown():
    assert hf001.holm({"f_vol": 0.01, "f_raise": 0.03, "f_both": 1.0}) == {"f_vol": 0.03, "f_raise": 0.06, "f_both": 1.0}
    assert hf001.holm({"f_vol": 0.01, "f_raise": 0.011, "f_both": 0.012}) == {name: 0.03 for name in hf001.VARIANTS}
    with pytest.raises(ValueError, match="exactly"):
        hf001.holm({"f_vol": 0.01})


@pytest.mark.parametrize("trials,t,expected", [(20, 2.0, "fail"), (20, 2.01, "pass"),
                                               (21, 3.0, "fail"), (21, 3.01, "pass")])
def test_judgement_requires_holm_and_strict_repository_t_threshold(fields, trials, t, expected):
    variants = {name: hf001.variant_metrics(metric_frame(), name) for name in hf001.VARIANTS}
    for result in variants.values():
        result["primary"].update(t_stat=t, p_one_sided=0.01)
        result["random_control"]["share_of_controls"] = 0.05
    result = hf001.judge_variants(variants, fields, trials)
    assert all(variant["verdict"] == expected for variant in result.values())
    for variant in result.values():
        assert variant["judgement_inputs"]["t_threshold"] == (2 if trials <= 20 else 3)
    variants["f_both"]["primary"]["p_one_sided"] = 0.06
    assert hf001.judge_variants(variants, fields, trials)["f_both"]["verdict"] == "fail"


@pytest.mark.parametrize("failure", ["negative", "cost_1_5", "control", "months", "undefined"])
def test_each_remaining_pass_requirement_is_enforced(fields, failure):
    variants = {name: hf001.variant_metrics(metric_frame(), name) for name in hf001.VARIANTS}
    target = variants["f_both"]
    if failure == "negative":
        target["primary"]["mean"] = 0
        target["risk"]["filtered"]["volatility"] = target["risk"]["baseline"]["volatility"]
    elif failure == "cost_1_5":
        target["cost_sensitivity"]["1.5"]["difference"] = 0
    elif failure == "control":
        target["random_control"]["share_of_controls"] = 0.055
    elif failure == "months":
        target["primary"]["count"] = 35  # Thousands of stock rows cannot substitute for month 36.
    else:
        target["primary"].update(t_stat=None, p_one_sided=1)
    judged = hf001.judge_variants(variants, fields, 3)
    assert judged["f_both"]["verdict"] == ("insufficient" if failure in ("months", "undefined") else "fail")


def test_delisting_loss_repeats_all_metrics_controls_holm_and_changed_verdict_never_passes(fields):
    frame = metric_frame(delist=True)
    main, sensitivity = hf001.evaluate(frame, fields, 3)
    for name in hf001.VARIANTS:
        assert main[name]["base_verdict"] == "pass"
        assert sensitivity[name]["verdict"] == "fail"
        assert main[name]["verdict"] == "delisting_sensitive" and main[name]["delisting_sensitive"]
        assert main[name]["primary"]["mean"] > 0 > sensitivity[name]["primary"]["mean"]
        assert sensitivity[name]["holm_p"] > 0.05
        assert sensitivity[name]["random_control"]["path_means"] != main[name]["random_control"]["path_means"]
        row = sensitivity[name]["per_month"][0]["costs"]["1.0"]
        assert row["filtered_net"] == pytest.approx(-1 - 0.0025)
        assert row["universe_net"] == row["baseline_net"]
        assert len(main[name]["per_month"][0]["P"]) == len(sensitivity[name]["per_month"][0]["P"]) == 50


def test_risk_only_nonpositive_primary_with_better_volatility_and_tail(fields):
    frame = metric_frame(months=40, stocks=2)
    for number, (_, group) in enumerate(frame.groupby("decision_date", sort=True)):
        baseline = -0.1 if number % 2 == 0 else 0.15
        frame.loc[group.index[0], "gross"] = 2 * baseline - 0.02
        frame.loc[group.index[1], "gross"] = 0.02
    frame["gross_delisting_loss"] = frame.gross
    variants = {name: hf001.variant_metrics(frame, name) for name in hf001.VARIANTS}
    result = hf001.judge_variants(variants, fields, 3)
    assert result["f_both"]["primary"]["mean"] < 0
    assert result["f_both"]["verdict"] == "risk_only"


def test_risk_diagnostics_lower_five_percent_and_initial_wealth_drawdown():
    result = hf001.risk_metrics([-0.2, 0.1, -0.1, 0.3])
    assert result["lower_5pct_mean"] == -0.2
    wealth = np.r_[1, np.cumprod(1 + np.array([-0.2, 0.1, -0.1, 0.3]))]
    assert result["max_drawdown"] == pytest.approx((1 - wealth / np.maximum.accumulate(wealth)).max())
    assert hf001.risk_metrics([-1.0025, 0.5])["max_drawdown"] == 1


def test_year_ban_liquidity_cost_secondary_and_validation_sign_is_only_diagnostic(fields):
    frame = metric_frame(months=40, stocks=5)
    result = hf001.variant_metrics(frame, "f_both")
    assert result["validation"]["primary"]["count"] == 3
    assert result["by_year"]["2025"]["primary"] == result["validation"]["primary"]
    dates = sorted(frame.decision_date.unique())
    ban_count = sum(pd.Timestamp("2023-11-06") <= date <= pd.Timestamp("2025-03-30") for date in dates)
    assert result["ban_period"]["inside"]["primary"]["count"] == ban_count
    assert set(result["liquidity_buckets"]) == {"0", "1", "2", "3"}
    assert result["liquidity_buckets"]["1"]["primary"]["mean"] == 0
    assert result["cost_sensitivity"]["2.0"]["filtered_net"] < result["cost_sensitivity"]["1.0"]["filtered_net"]
    variants = {name: hf001.variant_metrics(metric_frame(), name) for name in hf001.VARIANTS}
    for value in variants.values():
        value["validation_year_same_sign"] = False
    assert hf001.judge_variants(variants, fields, 3)["f_both"]["verdict"] == "pass"


def test_source_hashes_cover_actual_files_and_latest_daily_versions(tmp_path):
    day = pd.Timestamp("2024-05-31")
    price_file = tmp_path / "market/krx_daily/2024/20240531.jsonl"
    price_file.parent.mkdir(parents=True)
    records = [{"trade_date": str(day.date()), "stock_code": "000010", "open": 10000, "close": close,
                "volume": 10000, "trading_value": 2e9, "base_price": 10000, "change_rate_pct": change,
                "stock_name": "합성보통주", "market": "KOSPI", "calendar_status": "verified", "bar_at": "2024-05-31T15:30:00+09:00"}
               for close, change in ((11000, 10), (12000, 20), (11000, 10))]
    price_file.write_text("".join(json.dumps(row) + "\n" for row in records))
    financials(tmp_path, ["000010"])
    company = tmp_path / "reference/dart_company/companies.jsonl"
    company.parent.mkdir(parents=True)
    company.write_text(json.dumps({"stock_code": "000010", "induty_code": "261"}) + "\n")
    listings = listing_archive(tmp_path, "2024-05-30", "2024-05-31")
    prices = hc001.d001._load_prices(price_file.parent.parent, "20240531", "20240531")
    assert len(prices) == 1 and prices.close.iloc[0] == 11000 and prices.ret_1d.iloc[0] == 0.1
    hashes = hf001.source_hashes(tmp_path, [(day, price_file)], listings)
    for group in ("krx_daily", "quarterly", "company_profile", "dart_listings"):
        assert hashes[group]
        for relative, value in hashes[group].items():
            assert value == hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest()


@pytest.mark.parametrize("existing_trials,threshold", [(17, 2.0), (18, 3.0)])
def test_runner_records_exactly_three_rows_finite_results_interpretations_and_hashes(registered_repo, monkeypatch, existing_trials, threshold):
    root = registered_repo
    data = root / "data"
    prices, days = market("2024-01-01", codes=["000010", "000020"])
    financials(data, ["000010", "000020"])
    listing_archive(data, "2023-01-01", "2025-11-30", [listing_row("2024-05-30")])
    registry = root / experiment_registry.REGISTRY_PATH
    with registry.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(experiment_registry.REGISTRY_COLUMNS)
        commit = hf001._registration(root).commit_hash
        for _ in range(existing_trials):
            writer.writerow(("2026-01-01", hf001.EXPERIMENT_ID, commit, "previous", "fail", "{}", ""))
    def denied(*args, **kwargs):
        raise AssertionError("common.judge cannot judge HF001")
    monkeypatch.setattr(common, "judge", denied)
    result = hf001.run_experiment(prices=prices, sessions=days, data_dir=data, repo_root=root)
    assert result["selected_variant"] == "f_both" and result["verdict"] == "insufficient"
    assert result["coverage"]["planned_months"] == 103
    assert result["source_hashes"]["quarterly"] and result["source_hashes"]["dart_listings"]
    assert result["source_hashes"]["in_memory_prices"]
    with registry.open() as handle:
        trials = list(csv.DictReader(handle))
    assert len(trials) == existing_trials + 3
    assert [row["variant"] for row in trials[-3:]] == list(hf001.VARIANTS)
    for row in trials[-3:]:
        inputs = json.loads(row["metrics_json"])
        assert inputs["trial_count_after_run"] == existing_trials + 3 and inputs["t_threshold"] == threshold
    output = root / "research/experiments" / hf001.EXPERIMENT_ID / "results"
    files = list(output.glob("*"))
    assert len(files) == 2
    assert json.loads(next(path for path in files if path.suffix == ".json").read_text()) == result
    summary = next(path for path in files if path.suffix == ".md").read_text()
    assert "NW t" in summary and "Holm p" in summary and "interpretations" in summary and "assumptions" in summary
    assert "월별 IC 한 개" not in summary
    assert not (root / "research/experiments/holdout_ledger.jsonl").exists()


def test_holdout_guard_precedes_reading_sources_and_financials(monkeypatch):
    prices, _ = market("2026-01-01", "2026-01-05", ["000010"])
    def denied(*args, **kwargs):
        raise AssertionError("sources read before holdout guard")
    monkeypatch.setattr(hf001, "source_hashes", denied)
    monkeypatch.setattr(hc002, "build_universe", denied)
    with pytest.raises(ValueError, match="holdout"):
        hf001.run_experiment(prices=prices)


def test_dry_run_per_month_reports_pending_backfill_and_has_no_side_effects(tmp_path, monkeypatch, capsys):
    financials(tmp_path, ["000010"])
    (tmp_path / "fundamentals/dart_quarterly/2026_11013.jsonl").write_text("future financials must not be opened")
    prices = tmp_path / "market/krx_daily/2025"
    prices.mkdir(parents=True)
    (prices / "20251128.jsonl").write_text("price data must not be loaded")
    future = tmp_path / "market/krx_daily/2026"
    future.mkdir()
    (future / "20260102.jsonl").write_text("holdout prices must not be opened")
    listing_archive(tmp_path, "2023-01-01", "2025-11-30")
    def denied(*args, **kwargs):
        raise AssertionError("dry-run evaluated, drew, or wrote")
    monkeypatch.setattr(hc001.d001, "_load_prices", denied)
    monkeypatch.setattr(dart_quarterly, "load_quarterly", denied)
    monkeypatch.setattr(hf001, "run_experiment", denied)
    monkeypatch.setattr(hf001, "write_results", denied)
    monkeypatch.setattr(hf001, "load_listings", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    monkeypatch.setattr(np.random, "default_rng", denied)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = hf001.dry_run(data_dir=tmp_path)
    assert plan["planned_months"] == plan["guarded_months"] == 103
    assert plan["price_files"] == 1 and plan["first_listing_file"] == "2023-01-01"
    assert plan["maximum_valid_months_from_coverage"] == {name: 0 for name in hf001.VARIANTS}
    assert plan["minimum_valid_months"] == 36
    coverage = {row["month"]: row["listing"]["covered"] for row in plan["months"]}
    assert not coverage["2017-05"] and not coverage["2023-05"] and coverage["2023-07"] and coverage["2025-11"]
    assert plan["financials"]["quarters"]["2026Q1"]["rows"] is None
    assert hf001.main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
    assert hf001.main(["--data-dir", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "2017-05 decision=" in text and "2025-11 decision=" in text and "listing126=False" in text
    assert "2016-06..2022" in text and "No returns evaluated" in text
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_execute_cli_dispatches_only_explicit_execute(monkeypatch, capsys):
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return {"verdict": "insufficient", "variants": {"f_both": {"primary": {"count": 0}}}}
    monkeypatch.setattr(hf001, "run_experiment", run)
    assert hf001.main(["--execute", "--data-dir", "/tmp/hf001-local"]) == 0
    assert calls == [{"data_dir": Path("/tmp/hf001-local")}]
    assert "insufficient" in capsys.readouterr().out
