"""HC001 definitions and runner plumbing, using local synthetic archives only."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiments import common, hc001
from src.ingestion import dart_quarterly
from src.research import industry_map


@pytest.fixture
def fields():
    return common.load_preregistration(hc001.EXPERIMENT_ID)


def filing(year, quarter, revenue, income, day, *, code="000010", number=1, division="CFS", conflict=False):
    report = next(report for report, q in dart_quarterly.REPORTS.items() if q == quarter)
    receipt = day.replace("-", "") + f"{number:06d}"
    accounts = {metric: {"thstrm_amount": str(value), "thstrm_add_amount": str(value * quarter),
                         "thstrm_dt": None, "currency": "KRW"}
                for metric, value in (("revenue", revenue), ("operating_income", income), ("net_income", 1)) if value is not None}
    row = {"stock_codes": [code], "corp_code": f"{int(code):08d}", "bsns_year": year,
           "reprt_code": report, "fiscal_quarter": f"{year}Q{quarter}", "fs_div": division,
           "available_date": day, "rcept_no": receipt, "currency": "KRW", "raw_accounts": accounts}
    if conflict:
        row["raw_accounts"].pop("operating_income", None)
        row["conflicting_accounts"] = {"operating_income": "영업이익"}
    return row


def archive(data_dir, rows):
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True, exist_ok=True)
    groups = {}
    for row in rows:
        groups.setdefault(f"{row['bsns_year']}_{row['reprt_code']}.jsonl", []).append(row)
    for name, records in groups.items():
        (directory / name).write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def market():
    dates = pd.bdate_range("2024-01-02", "2025-12-31")
    codes = ["000010", "000020", "000030", "000040", "000050"]
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    frame = pd.DataFrame({"open": 10000.0, "close": 10000.0, "volume": 10000.0,
                          "base_price": 10000.0, "ret_1d": np.tile([0.001, 0.0002, -0.0001, 0.0, 0.0005], len(dates)),
                          "market": "KOSPI", "stock_name": "합성보통주", "calendar_status": "verified",
                          "trading_value": np.tile([2e8, 2e9, 2e10, 3e8, 3e9], len(dates))}, index=index)
    return frame, dates


def universe(prices, day):
    dates = common._sessions(prices)
    codes = prices.index.get_level_values("stock_code").unique()
    return pd.DataFrame({"trade_date": pd.Timestamp(day), "decision_date": dates[dates.get_loc(pd.Timestamp(day)) - 1],
                         "stock_code": codes, "avg_trading_value_20d": 2e9,
                         "phase": [2, 3, 4, 1, 2], "revenue_yoy": [10, 10, -5, 20, 5], "score": [10, -2, -5, 0, 8]})


def financial_history(revenue=220, income=30):
    periods = pd.period_range("2022Q4", "2024Q1", freq="Q")
    return pd.DataFrame({"corp_code": "00000010", "stock_code": "000010", "fiscal_quarter": periods.astype(str),
                         "revenue": [100.0, 110.0, 120.0, 130.0, 140.0, float(revenue)],
                         "operating_income": [10.0] * 5 + [float(income)]})


def test_rebalance_calendar_uses_exchange_sessions_and_entry_is_day_one():
    sessions = hc001._session_calendar()
    planned, excluded = hc001.rebalance_calendar(sessions)
    assert len(planned) == 39 and len(excluded) == 1
    lookup = {row["anchor"]: row for row in planned}
    assert lookup["2024-08-15"]["trade_date"] == "2024-08-16"
    assert lookup["2020-11-15"]["trade_date"] == "2020-11-16"
    assert lookup["2016-04-01"]["trade_date"] == "2016-04-01"
    for row in planned:
        entry = sessions.get_loc(pd.Timestamp(row["trade_date"]))
        assert sessions[entry + 59] == pd.Timestamp(row["exit_date"])
        assert pd.Timestamp(row["exit_date"]) < hc001.CUTOFF
    assert excluded[0]["anchor"] == "2025-11-15"
    assert excluded[0]["trade_date"] == "2025-11-17"
    assert excluded[0]["reason"] == "holdout_horizon"
    assert pd.Timestamp(excluded[0]["exit_date"]) >= hc001.CUTOFF


def test_calendar_does_not_move_a_missing_anchor_to_a_later_quarter():
    sessions = pd.bdate_range("2024-05-01", "2026-03-31")
    planned, excluded = hc001.rebalance_calendar(sessions)
    assert {"anchor": "2024-04-01", "reason": "missing_rebalance_session"} in excluded
    assert not any(row["anchor"] == "2024-04-01" for row in planned)


def test_receipt_on_rebalance_day_and_later_correction_never_leak(tmp_path):
    archive(tmp_path, [filing(2022, 1, 80, 8, "2022-05-15"), filing(2023, 1, 100, 10, "2023-05-15"),
                      filing(2024, 1, 150, 30, "2024-05-16"),
                      filing(2024, 1, 200, 15, "2024-06-20", number=2)])
    _, receipts = hc001._financial_inventory(tmp_path)
    same_day = hc001.signals_as_of("2024-05-16", data_dir=tmp_path, first_receipts=receipts).loc["000010"]
    assert same_day.fiscal_quarter == "2023Q1" and same_day.revenue_yoy == 25
    for day in ("2024-05-17", "2024-06-20"):
        row = hc001.signals_as_of(day, data_dir=tmp_path, first_receipts=receipts).loc["000010"]
        assert row.fiscal_quarter == "2024Q1" and row.revenue_yoy == 50
        assert row.operating_income_yoy == 200 and row.phase == 2 and not row.correction_used
    revised = hc001.signals_as_of("2024-06-21", data_dir=tmp_path, first_receipts=receipts).loc["000010"]
    assert revised.revenue_yoy == 100 and revised.operating_income_yoy == 50
    assert revised.phase == 3 and revised.correction_used


def test_prior_year_correction_is_selected_only_when_known(tmp_path):
    archive(tmp_path, [filing(2023, 1, 100, 10, "2023-05-15"),
                      filing(2023, 1, 125, 20, "2024-06-20", number=2),
                      filing(2024, 1, 150, 30, "2024-05-15")])
    _, receipts = hc001._financial_inventory(tmp_path)
    before = hc001.signals_as_of("2024-05-16", data_dir=tmp_path, first_receipts=receipts).loc["000010"]
    after = hc001.signals_as_of("2024-06-21", data_dir=tmp_path, first_receipts=receipts).loc["000010"]
    assert before.revenue_yoy == 50 and before.operating_income_yoy == 200
    assert after.revenue_yoy == 20 and after.operating_income_yoy == 50 and after.prior_correction_used


def test_latest_missing_quarter_is_not_replaced_with_an_older_signal(tmp_path):
    archive(tmp_path, [filing(2022, 1, 80, 8, "2022-05-15"), filing(2023, 1, 100, 10, "2023-05-15"),
                      filing(2024, 1, 150, None, "2024-05-15", conflict=True)])
    row = hc001.signals_as_of("2024-05-16", data_dir=tmp_path).loc["000010"]
    assert row.fiscal_quarter == "2024Q1" and not row.computable
    assert pd.isna(row.phase) and row.conflicting_metrics == 1


@pytest.mark.parametrize("revenue,income,phase,overlap", [
    (220, 30, 2, False), (220, 15, 3, True), (220, 20, 1, False), (90, 30, 4, False),
])
def test_phase_order_is_preregistration_order(revenue, income, phase, overlap):
    row = hc001.phase_signals(financial_history(revenue, income)).iloc[-1]
    assert row.phase == phase and bool(row.phase_overlap) == overlap
    if phase == 3:
        assert row.matches_phase1 and row.matches_phase3
        assert dart_quarterly.yoy_signals(financial_history(revenue, income)).iloc[-1].phase == 1


def test_phase4_includes_zero_growth_and_phase1_wins_its_overlap():
    frame = financial_history(110, 10)
    frame["revenue"] = 110.0
    row = hc001.phase_signals(frame).iloc[-1]
    assert row.matches_phase1 and row.matches_phase4 and row.phase == 1 and row.phase_overlap
    row = hc001.phase_signals(frame.iloc[-5:]).iloc[-1]
    assert row.phase == 4 and not row.matches_phase1


def test_phase1_requires_six_consecutive_quarters():
    frame = financial_history(220, 20).drop(index=2)
    row = hc001.phase_signals(frame).iloc[-1]
    assert row.computable and not row.matches_phase1 and pd.isna(row.phase)


@pytest.mark.parametrize("previous,current,expected", [(10, -1, -200), (-10, -20, -200), (-10, 5, 150), (10, 100, 500), (0, 1, None)])
def test_yoy_loss_cap_and_zero_denominator_conventions(previous, current, expected):
    frame = financial_history(220, current)
    frame.loc[1, "operating_income"] = previous
    row = hc001.phase_signals(frame).iloc[-1]
    if expected is None:
        assert not row.computable and pd.isna(row.operating_income_yoy)
    else:
        assert row.operating_income_yoy == expected


@pytest.mark.parametrize("profile_count,apply", [(0, False), (3, False), (4, True), (5, True)])
def test_financial_exclusion_falls_back_below_eighty_percent(profile_count, apply):
    codes = [f"{i * 10:06d}" for i in range(1, 6)]
    frame = pd.DataFrame({"phase": 2}, index=pd.Index(codes, name="stock_code"))
    groups = ["금융(은행·증권·보험)", "지주회사", "반도체", industry_map.UNCLASSIFIED, "기타"]
    profiles = dict(zip(codes[:profile_count], groups[:profile_count]))
    filtered, report = hc001.exclude_financials(frame, profiles)
    assert report["profile_coverage"] == profile_count / 5
    assert report["exclusion_applied"] == apply
    assert filtered.index.tolist() == (codes[2:] if apply else codes)
    assert report["financials_excluded"] == (2 if apply else 0)
    if not apply:
        assert "financials included" in report["note"]


def test_current_profile_loader_preserves_industry_of_classification(tmp_path):
    path = tmp_path / "reference/dart_company/companies.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"stock_code": "000010", "induty_code": "64992"}) + "\n")
    assert hc001._industries(tmp_path)["000010"] == industry_map.industry_of("000010", companies_path=path) == "지주회사"


@pytest.mark.parametrize("policy", ["adjusted", "raw_fallback", "limit_up", "stale", "zero_volume", "delisted"])
def test_holding_policies_use_rebalance_open_and_sixtieth_close(policy):
    prices, dates = market()
    day = pd.Timestamp("2024-05-16")
    entry = dates.get_loc(day)
    exit_day = dates[entry + 59]
    code = "000010"
    if policy == "adjusted":
        prices.loc[(day, code), "open"] = 10500.0
        prices.loc[(exit_day, code), "close"] = 5000.0  # raw split; adjusted chain remains intact
        prices.loc[(exit_day, code), "base_price"] = 5000.0
    elif policy == "raw_fallback":
        prices.loc[(dates[entry + 3], code), "ret_1d"] = np.nan
        prices.loc[(exit_day, code), "close"] = 12000.0
    elif policy == "limit_up":
        prices.loc[(day, code), "open"] = 13000.0
    elif policy == "stale":
        prices = prices.drop((exit_day, code))
    elif policy == "zero_volume":
        prices.loc[(exit_day, code), "volume"] = 0.0
    elif policy == "delisted":
        prices = prices.drop([(date, code) for date in dates[entry + 1:]])
    selected = universe(prices, day)
    row = hc001._observations(prices, selected).set_index("stock_code").loc[code]
    if policy in ("limit_up", "delisted"):
        assert pd.isna(row.gross)
        assert row.reason == ("limit_up_entry" if policy == "limit_up" else "no_later_price")
        assert pd.isna(row["cost_1.0"])
    else:
        actual_exit = dates[entry + 58] if policy == "stale" else exit_day
        if policy == "raw_fallback":
            expected = 0.2
            assert row.unadjusted_fallback
        else:
            chain = prices.loc[(slice(dates[entry + 1], actual_exit), code), "ret_1d"]
            expected = prices.loc[(day, code), "close"] / prices.loc[(day, code), "open"] * (1 + chain).prod() - 1
            assert not row.unadjusted_fallback
        assert row.gross == pytest.approx(expected)
        assert row.exit_date == actual_exit
        assert bool(row.stale_exit) == (policy in ("stale", "zero_volume"))
        for multiplier in (1.0, 1.5, 2.0):
            assert row[f"cost_{multiplier}"] == cost_model.round_trip_cost(
                float(prices.loc[(day, code), "open"]), "KOSPI", day.date(), 2e9, multiplier=multiplier)


def metric_frame():
    dates = pd.to_datetime(["2024-05-16", "2024-08-16", "2024-11-15", "2025-04-01"])
    rows = []
    for day, actual, other in zip(dates, [0.03, 0.05, 0.02, 0.06], [0.01, 0.04, 0.08, 0.03]):
        for code, phase, gross, score in (("000010", 2, actual, 10), ("000020", 3, other, 0)):
            rows.append({"trade_date": day, "stock_code": code, "gross": gross, "phase": phase, "score": score,
                         "revenue_yoy": 10, "avg_trading_value_20d": 2e9,
                         **{f"cost_{m}": 0.002 * m for m in (1.0, 1.5, 2.0)}})
    return pd.DataFrame(rows)


def test_excess_and_t_use_all_rebalances_with_gross_universe_benchmark(fields):
    frame = metric_frame()
    result = hc001.portfolio_metrics(frame, rng=np.random.default_rng(0))
    benchmark = frame.groupby("trade_date").gross.mean()
    actual = frame.loc[frame.phase == 2].set_index("trade_date").gross
    expected = actual - 0.002 - benchmark
    assert result["excess"]["count"] == 4
    assert result["excess"]["mean"] == pytest.approx(expected.mean())
    assert result["excess"]["t_stat"] == pytest.approx(expected.mean() / (expected.std(ddof=1) / np.sqrt(4)))
    assert result["cost_sensitivity"]["1.0"]["top_net"] > 0 > result["excess"]["mean"]
    assert result["cost_sensitivity"]["1.5"]["top_net_excess"] == pytest.approx((actual - 0.003 - benchmark).mean())
    assert result["cost_sensitivity"]["2.0"]["top_net_excess"] == pytest.approx((actual - 0.004 - benchmark).mean())
    inputs = hc001._judgement_inputs(result, fields, 2)
    assert inputs["observations"] == 4 and inputs["t_stat"] == result["excess"]["t_stat"]
    assert inputs[hc001.NET_METRIC] == result["excess"]["mean"]
    assert inputs["validation_year_same_sign"] is False
    assert inputs["design_mean_excess"] == pytest.approx(expected.iloc[:3].mean())
    assert inputs["validation_mean_excess"] == pytest.approx(expected.iloc[3])
    assert set(result["excess"]["by_year"]) == {"2024", "2025"}
    assert result["capacity"]["strategy_capacity"] == 2e7
    assert all(value == 2e7 for value in result["capacity_by_rebalance"].values())


def test_judge_receives_excess_metrics_even_when_absolute_net_is_positive(fields):
    result = hc001.portfolio_metrics(metric_frame(), rng=np.random.default_rng(0))
    inputs = hc001._judgement_inputs(result, fields, 2)
    inputs.update(observations=36, t_stat=2.1, random_control_share=0.05, validation_year_same_sign=True)
    verdict, reasons = common.judge(fields, inputs, net_metric=hc001.NET_METRIC,
                                    net_at_cost_1_5_metric=hc001.NET_AT_COST_1_5_METRIC)
    assert result["cost_sensitivity"]["1.0"]["top_net"] > 0
    assert verdict == "fail" and len(reasons) == 2
    assert any(hc001.NET_METRIC in reason for reason in reasons)
    assert any(hc001.NET_AT_COST_1_5_METRIC in reason for reason in reasons)


def test_control_sampling_is_reproducible_same_size_without_replacement():
    group = pd.DataFrame({"stock_code": ["000030", "000010", "000020"], "gross": [0.4, 0.1, 0.2], "cost_1.0": [0.03, 0.01, 0.02]})
    actual = hc001.random_controls(group, 2, np.random.default_rng(19))
    sorted_group = group.sort_values("stock_code")
    net = (sorted_group.gross - sorted_group["cost_1.0"]).to_numpy()
    rng = np.random.default_rng(19)
    expected = [net[rng.choice(3, size=2, replace=False)].mean() - group.gross.mean() for _ in range(200)]
    np.testing.assert_allclose(actual, expected)
    np.testing.assert_array_equal(actual, hc001.random_controls(group.iloc[::-1], 2, np.random.default_rng(19)))
    np.testing.assert_allclose(hc001.random_controls(group, 3, np.random.default_rng(19)), -group["cost_1.0"].mean())


def test_controls_compare_cross_rebalance_means_and_match_actual_dates():
    frame = metric_frame()
    result = hc001.portfolio_metrics(frame, rng=np.random.default_rng(7))
    rng = np.random.default_rng(7)
    expected = np.mean([hc001.random_controls(group, 1, rng) for _, group in frame.groupby("trade_date")], axis=0)
    np.testing.assert_allclose(result["random_control"]["mean_excess"], expected)
    assert result["random_control"]["count"] == 200 and result["random_control"]["rebalances"] == 4
    assert result["random_control"]["share_of_controls"] == np.mean(expected >= result["excess"]["mean"])


def test_score_ic_uses_only_positive_revenue_and_t_over_rebalances():
    frame = metric_frame()
    excluded = frame.iloc[:1].copy().assign(stock_code="000030", revenue_yoy=0, score=-999, gross=999)
    result = hc001.score_ic(pd.concat([frame, excluded]))
    expected = [group.score.rank().corr(group.gross.rank()) for _, group in frame.groupby("trade_date")]
    assert result["count"] == 4 and all(row["stocks"] == 2 for row in result["daily"])
    assert result["mean"] == pytest.approx(np.mean(expected))
    assert result["t_stat"] == pytest.approx(np.mean(expected) / (np.std(expected, ddof=1) / 2))


def test_undefined_ic_does_not_remove_a_valid_phase2_rebalance():
    frame = metric_frame()
    frame["score"] = 1.0
    assert hc001.score_ic(frame)["count"] == 0
    assert hc001.portfolio_metrics(frame)["excess"]["count"] == 4


def test_universe_uses_previous_session_and_reports_missing_conflicts(tmp_path):
    prices, dates = market()
    day = pd.Timestamp("2024-05-16")
    rows = [filing(year, 1, revenue, income, f"{year}-05-15", code=code)
            for code in ("000010", "000020", "000030", "000040")
            for year, revenue, income in ((2023, 100, 10), (2024, 150, 30))]
    rows.append(filing(2024, 1, 150, None, "2024-05-15", code="000050", conflict=True))
    archive(tmp_path, rows)
    # Entry-day values cannot change the eligibility measured at yesterday's close.
    prices.loc[(day, "000010"), ["close", "trading_value"]] = [900, 1.0]
    prices.loc[(dates[dates.get_loc(day) - 1], "000020"), "close"] = 999
    prices.loc[(slice(None), "000030"), "stock_name"] = "합성스팩"
    prices.loc[(slice(None), "000040"), "trading_value"] = 1e7
    result, counts, _, _ = hc001.build_universe(prices, data_dir=tmp_path, sessions=dates)
    assert result.loc[result.trade_date == day].stock_code.tolist() == ["000010"]
    report = next(row for row in counts if row["trade_date"] == "2024-05-16")
    assert report["price_eligible"] == 2 and report["no_data"] == 1 and report["conflicting_metrics"] == 1
    assert report["industry_profiles"]["exclusion_applied"] is False


def test_missing_global_session_is_reported_instead_of_lengthening_horizon(tmp_path):
    prices, dates = market()
    archive(tmp_path, [])
    prices = prices.drop(pd.Timestamp("2024-06-03"), level="trade_date")
    _, _, excluded, _ = hc001.build_universe(prices, data_dir=tmp_path, sessions=dates)
    row = next(row for row in excluded if row.get("trade_date") == "2024-05-16")
    assert row["reason"] == "missing_price_sessions" and row["missing_sessions"] == ["2024-06-03"]


def test_holdout_guard_aborts_before_loading_financials_or_recording(tmp_path, monkeypatch):
    prices, _ = market()
    future = prices.iloc[:1].copy()
    future.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-01"), "000010")], names=prices.index.names)
    def denied(*args, **kwargs):
        raise AssertionError("work started after holdout violation")
    monkeypatch.setattr(hc001, "build_universe", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    with pytest.raises(ValueError, match="holdout"):
        hc001.run_experiment(prices=pd.concat([prices, future]))
    with pytest.raises(ValueError, match="holdout"):
        hc001.d001._load_prices(tmp_path / "absent", "20150101", "20260101")
    assert not (tmp_path / "research").exists()


@pytest.mark.parametrize("has_financials", [False, True])
def test_synthetic_runner_records_exactly_two_variants_and_correct_summary(tmp_path, monkeypatch, has_financials):
    registration = experiment_registry.verify_preregistration(hc001.EXPERIMENT_ID)
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *args, **kwargs: registration)
    prices, dates = market()
    rows = [filing(year, 1, revenue, income, f"{year}-05-15", code=code)
            for code in prices.index.get_level_values("stock_code").unique()
            for year, revenue, income in ((2023, 100, 10), (2024, 150, 30))] if has_financials else []
    archive(tmp_path / "data", rows)
    calendar = pd.bdate_range(dates[0], "2026-03-31")
    result = hc001.run_experiment(prices=prices, sessions=calendar, data_dir=tmp_path / "data", repo_root=tmp_path)
    assert result["selected_variant"] == "phase2_ew" and result["verdict"] == "insufficient"
    assert result["counts"]["holdout_horizon_exclusions"] == 1
    with (tmp_path / experiment_registry.REGISTRY_PATH).open() as handle:
        trials = list(csv.DictReader(handle))
    assert [row["variant"] for row in trials] == list(hc001.VARIANTS)
    assert trials[1]["verdict"] == "diagnostic"
    inputs = json.loads(trials[0]["metrics_json"])
    assert inputs["t_stat"] == result["variants"]["phase2_ew"]["excess"]["t_stat"]
    assert inputs["trial_count_after_run"] == 2
    files = sorted((tmp_path / "research/experiments" / hc001.EXPERIMENT_ID / "results").glob("*"))
    assert len(files) == 2
    assert json.loads(next(path for path in files if path.suffix == ".json").read_text()) == result
    summary = next(path for path in files if path.suffix == ".md").read_text()
    assert "초과수익 t" in summary and "## interpretations" in summary
    assert "월별 IC 한 개를 관측 한 회" not in summary
    assert hc001.INTERPRETATIONS["note"] in summary
    assert not (tmp_path / "research/experiments/holdout_ledger.jsonl").exists()


def test_dry_run_reports_archive_coverage_without_loading_prices_or_evaluating(tmp_path, monkeypatch, capsys):
    archive(tmp_path, [filing(2024, 1, 100, 10, "2024-05-15")])
    (tmp_path / "fundamentals/dart_quarterly/2024_11012.jsonl").touch()
    later = tmp_path / "fundamentals/dart_quarterly/2026_11013.jsonl"
    later.write_text("not read: holdout financials")
    price_dir = tmp_path / "market/krx_daily/2025"
    price_dir.mkdir(parents=True)
    (price_dir / "20251230.jsonl").write_text("not read: coverage only")
    holdout_dir = tmp_path / "market/krx_daily/2026"
    holdout_dir.mkdir()
    (holdout_dir / "20260102.jsonl").write_text("not read: holdout prices")
    def denied(*args, **kwargs):
        raise AssertionError("dry-run started evaluation or writing")
    monkeypatch.setattr(dart_quarterly, "load_quarterly", denied)
    monkeypatch.setattr(hc001.d001, "_load_prices", denied)
    monkeypatch.setattr(hc001, "run_experiment", denied)
    monkeypatch.setattr(common, "write_results", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = hc001.dry_run(data_dir=tmp_path)
    assert plan["price_files"] == 1 and plan["last_price_file"] == "2025-12-30"
    assert plan["guarded_rebalances"] == 39
    assert plan["financials"]["empty_quarters"] == ["2024Q2"]
    assert plan["financials"]["quarters"]["2026Q1"]["rows"] is None
    assert plan["financials"]["stored_rows_through_2025"] == 1
    assert hc001.main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
    assert hc001.main(["--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "39 before 2026" in output and "No returns evaluated" in output
    assert "financials included" not in output  # Actual universe coverage is measured on execution.
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
