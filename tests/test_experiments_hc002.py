"""HC002 preregistered monthly behaviour, using synthetic offline inputs."""
from __future__ import annotations

import csv
import json

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry
from backtesting.experiments import common, hc001, hc002
from src.ingestion import dart_quarterly


def archive(data_dir, rows):
    directory = data_dir / "fundamentals/dart_quarterly"
    directory.mkdir(parents=True, exist_ok=True)
    groups = {}
    for year, revenue, income, day, number in rows:
        row = {"stock_codes": ["000010"], "corp_code": "00000010", "bsns_year": year,
               "reprt_code": "11013", "fiscal_quarter": f"{year}Q1", "fs_div": "CFS",
               "available_date": day, "rcept_no": day.replace("-", "") + f"{number:06d}",
               "currency": "KRW", "raw_accounts": {
                   metric: {"thstrm_amount": str(value), "thstrm_add_amount": str(value),
                            "thstrm_dt": None, "currency": "KRW"}
                   for metric, value in (("revenue", revenue), ("operating_income", income), ("net_income", 1))}}
        groups.setdefault(f"{year}_11013.jsonl", []).append(row)
    for name, records in groups.items():
        (directory / name).write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def market():
    dates = pd.bdate_range("2024-01-02", "2025-12-31")
    index = pd.MultiIndex.from_product([dates, ["000010", "000020"]], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000.0, "close": 10000.0, "base_price": 10000.0,
                           "volume": 10000.0, "ret_1d": np.tile([0.001, 0.0001], len(dates)),
                           "market": "KOSPI", "stock_name": "합성보통주", "calendar_status": "verified",
                           "trading_value": 2e9}, index=index)
    return prices, dates


def selected(dates, decision="2024-05-31"):
    day = pd.Timestamp(decision)
    return pd.DataFrame({"decision_date": day, "trade_date": dates[dates.get_loc(day) + 1],
                         "stock_code": ["000010", "000020"], "avg_trading_value_20d": 2e9,
                         "phase": [2, 3], "revenue_yoy": 10.0, "score": [10.0, -5.0]})


def metric_frame():
    rows = []
    for day, actual, other in zip(pd.to_datetime(["2024-11-29", "2024-12-31", "2025-01-31", "2025-02-28"]),
                                  [0.03, 0.04, 0.01, 0.02], [0.01, 0.02, 0.07, 0.08]):
        for code, phase, gross in (("000010", 2, actual), ("000020", 3, other)):
            rows.append({"trade_date": day, "stock_code": code, "gross": gross, "phase": phase,
                         "score": 10.0 if phase == 2 else -5.0, "revenue_yoy": 10.0,
                         "avg_trading_value_20d": 2e9, **{f"cost_{m}": 0.002 * m for m in (1.0, 1.5, 2.0)}})
    return pd.DataFrame(rows)


def test_month_end_calendar_next_entry_and_twentieth_exit():
    sessions = pd.bdate_range("2017-04-01", "2026-03-31").drop(pd.Timestamp("2024-05-31"))
    planned, excluded = hc002.rebalance_calendar(sessions[::-1])
    assert len(planned) == 103 and not excluded
    assert planned[0]["month"] == "2017-05" and planned[-1]["month"] == "2025-11"
    may = next(row for row in planned if row["month"] == "2024-05")
    assert may["decision_date"] == "2024-05-30" and may["trade_date"] == "2024-06-03"
    for row in planned:
        decision = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        assert sessions[decision + 1] == pd.Timestamp(row["trade_date"])
        assert sessions[decision + 20] == pd.Timestamp(row["exit_date"])
        month = pd.Period(row["month"], freq="M")
        assert sessions[sessions.to_period("M") == month][-1] == pd.Timestamp(row["decision_date"])
        assert pd.Timestamp(row["exit_date"]) < hc001.CUTOFF


def test_horizon_touching_cutoff_is_excluded_and_counted(tmp_path, monkeypatch):
    # No December sessions: November's holding window ends in January.
    sessions = pd.bdate_range("2017-04-01", "2026-03-31")
    sessions = sessions[sessions.to_period("M") != pd.Period("2025-12")]
    planned, excluded = hc002.rebalance_calendar(sessions)
    assert len(planned) == 102
    assert len(excluded) == 1 and excluded[0]["month"] == "2025-11"
    assert excluded[0]["reason"] == "holdout_horizon"
    assert pd.Timestamp(excluded[0]["exit_date"]) >= hc001.CUTOFF
    monkeypatch.setattr(hc001, "_session_calendar", lambda: sessions)
    (tmp_path / "market/krx_daily").mkdir(parents=True)
    assert hc002.dry_run(data_dir=tmp_path)["holdout_horizon_exclusions"] == 1


@pytest.mark.parametrize("end,reason", [("2025-11-28", "missing_entry_session"),
                                       ("2025-12-02", "incomplete_calendar_horizon")])
def test_calendar_missing_entry_or_horizon_is_not_shortened(end, reason):
    _, excluded = hc002.rebalance_calendar(pd.bdate_range("2017-04-01", end))
    assert next(row for row in excluded if row["month"] == "2025-11")["reason"] == reason


def test_calendar_does_not_substitute_a_later_month():
    planned, excluded = hc002.rebalance_calendar(pd.bdate_range("2024-06-01", "2026-03-31"))
    assert {"month": "2024-05", "reason": "missing_decision_session"} in excluded
    assert all(row["month"] != "2024-05" for row in planned)


def test_month_end_signal_excludes_same_day_filing_and_uses_known_correction(tmp_path):
    prices, dates = market()
    archive(tmp_path, [(2022, 80, 8, "2022-05-15", 1), (2023, 100, 10, "2023-05-15", 1),
                       (2024, 150, 30, "2024-05-31", 1),
                       (2024, 200, 15, "2024-06-27", 2),
                       (2024, 250, 100, "2024-06-28", 3)])
    result, counts, _, _ = hc002.build_universe(prices, data_dir=tmp_path, sessions=dates)
    may = result.loc[result.decision_date == pd.Timestamp("2024-05-31")].iloc[0]
    june = result.loc[result.decision_date == pd.Timestamp("2024-06-28")].iloc[0]
    assert may.fiscal_quarter == "2023Q1" and may.revenue_yoy == 25
    assert may.trade_date == pd.Timestamp("2024-06-03")
    assert june.fiscal_quarter == "2024Q1" and june.revenue_yoy == 100
    assert june.operating_income_yoy == 50 and june.phase == 3 and june.correction_used
    june_count = next(row for row in counts if row["decision_date"] == "2024-06-28")
    assert june_count["corrections_used"] == 1


def test_universe_uses_month_end_prices_without_entry_day_leakage(tmp_path):
    prices, dates = market()
    archive(tmp_path, [(2023, 100, 10, "2023-05-15", 1), (2024, 150, 30, "2024-05-15", 1)])
    before, _, _, _ = hc002.build_universe(prices, data_dir=tmp_path, sessions=dates)
    prices.loc[(pd.Timestamp("2024-06-03"), "000010"), ["close", "trading_value"]] = [900, 1]
    after, _, _, _ = hc002.build_universe(prices, data_dir=tmp_path, sessions=dates)
    mask = before.decision_date == pd.Timestamp("2024-05-31")
    pd.testing.assert_frame_equal(before.loc[mask], after.loc[mask])
    prices.loc[(pd.Timestamp("2024-05-31"), "000010"), "close"] = 999
    result, _, _, _ = hc002.build_universe(prices, data_dir=tmp_path, sessions=dates)
    assert not (result.decision_date == pd.Timestamp("2024-05-31")).any()


def test_missing_global_session_does_not_extend_the_horizon(tmp_path):
    prices, dates = market()
    archive(tmp_path, [])
    prices = prices.drop(pd.Timestamp("2024-06-10"), level="trade_date")
    _, _, excluded, _ = hc002.build_universe(prices, data_dir=tmp_path, sessions=dates)
    row = next(row for row in excluded if row.get("decision_date") == "2024-05-31")
    assert row["reason"] == "missing_price_sessions" and row["missing_sessions"] == ["2024-06-10"]


@pytest.mark.parametrize("policy", ["adjusted", "raw_fallback", "limit_up", "stale", "zero_volume", "delisted"])
def test_next_open_twentieth_close_and_shared_cost_and_execution_policies(policy):
    prices, dates = market()
    universe = selected(dates)
    entry_day = universe.trade_date.iloc[0]
    entry = dates.get_loc(entry_day)
    exit_day = dates[entry + 19]
    code = "000010"
    if policy == "adjusted":
        prices.loc[(entry_day, code), "open"] = 10500.0
        prices.loc[(exit_day, code), ["close", "base_price"]] = 5000.0
    elif policy == "raw_fallback":
        prices.loc[(dates[entry + 3], code), "ret_1d"] = np.nan
        prices.loc[(exit_day, code), "close"] = 12000.0
    elif policy == "limit_up":
        prices.loc[(entry_day, code), "open"] = 13000.0
    elif policy == "stale":
        prices = prices.drop((exit_day, code))
    elif policy == "zero_volume":
        prices.loc[(exit_day, code), "volume"] = 0.0
    else:
        prices = prices.drop([(day, code) for day in dates[entry + 1:]])
    row = hc002._observations(prices, universe).set_index("stock_code").loc[code]
    assert row.trade_date == pd.Timestamp("2024-05-31") and row.entry_date == entry_day
    if policy in ("limit_up", "delisted"):
        assert pd.isna(row.gross) and pd.isna(row["cost_1.0"])
        assert row.reason == ("limit_up_entry" if policy == "limit_up" else "no_later_price")
    else:
        actual_exit = dates[entry + 18] if policy == "stale" else exit_day
        assert row.exit_date == actual_exit
        if policy == "raw_fallback":
            expected = 0.2
            assert row.unadjusted_fallback
        else:
            chain = prices.loc[(slice(dates[entry + 1], actual_exit), code), "ret_1d"]
            expected = prices.loc[(entry_day, code), "close"] / prices.loc[(entry_day, code), "open"] * (1 + chain).prod() - 1
            assert not row.unadjusted_fallback
        assert row.gross == pytest.approx(expected)
        assert bool(row.stale_exit) == (policy in ("stale", "zero_volume"))
        for m in (1.0, 1.5, 2.0):
            assert row[f"cost_{m}"] == cost_model.round_trip_cost(
                float(prices.loc[(entry_day, code), "open"]), "KOSPI", entry_day.date(), 2e9, multiplier=m)


def test_turnover_secondary_waives_retained_names_and_charges_new_names():
    frame = metric_frame()
    second = frame.trade_date == pd.Timestamp("2024-12-31")
    frame.loc[second, "phase"] = 2  # One retained and one new name.
    before = frame.copy(deep=True)
    result = hc002.turnover_metrics(frame)
    judged = hc001.portfolio_metrics(frame)
    assert result["used_for_judgement"] is False
    assert result["turnover_by_month"][0]["turnover"] == 1.0
    assert result["turnover_by_month"][1] == {"decision_date": "2024-12-31", "count": 2,
                                              "retained": 1, "charged": 1, "turnover": 0.5}
    for m in (1.0, 1.5, 2.0):
        assert result["per_rebalance"][0][f"net_{m}"] == judged["per_rebalance"][0][f"net_{m}"]
        assert result["per_rebalance"][1][f"net_{m}"] == pytest.approx(0.03 - 0.001 * m)
        assert result["per_rebalance"][2][f"net_{m}"] == pytest.approx(0.01)
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("gap", ["absent_month", "empty_phase2", "unexecutable_phase2"])
def test_turnover_does_not_carry_names_across_a_month_without_holdings(gap):
    frame = metric_frame()
    second = frame.trade_date == pd.Timestamp("2024-12-31")
    if gap == "absent_month":
        frame = frame.loc[~second]
    elif gap == "empty_phase2":
        frame.loc[second, "phase"] = 3
    else:
        frame.loc[second & frame.phase.eq(2), "gross"] = np.nan
    result = hc002.turnover_metrics(frame)
    january = next(row for row in result["turnover_by_month"] if row["decision_date"] == "2025-01-31")
    assert january["retained"] == 0 and january["turnover"] == 1.0


def test_judgement_uses_monthly_full_cost_excess_and_validation_same_sign():
    fields = common.load_preregistration(hc002.EXPERIMENT_ID)
    frame = metric_frame()
    primary = hc001.portfolio_metrics(frame, rng=np.random.default_rng(0))
    inputs = hc001._judgement_inputs(primary, fields, 2)
    excess = pd.Series([row["excess_1.0"] for row in primary["per_rebalance"]])
    assert inputs["observations"] == 4
    assert inputs[hc002.NET_METRIC] == pytest.approx(excess.mean())
    assert inputs["t_stat"] == pytest.approx(excess.mean() / (excess.std(ddof=1) / 2))
    assert inputs[hc002.NET_AT_COST_1_5_METRIC] == primary["cost_sensitivity"]["1.5"]["top_net_excess"]
    assert inputs["design_mean_excess"] == pytest.approx(excess.iloc[:2].mean())
    assert inputs["validation_mean_excess"] == pytest.approx(excess.iloc[2:].mean())
    assert inputs["validation_year_same_sign"] is False
    inputs.update(observations=36, t_stat=2.1, random_control_share=0.05)
    verdict, reasons = common.judge(fields, inputs, net_metric=hc002.NET_METRIC,
                                    net_at_cost_1_5_metric=hc002.NET_AT_COST_1_5_METRIC)
    assert verdict == "fail" and any("sign" in reason for reason in reasons)
    frame.loc[frame.trade_date.dt.year == 2025, "gross"] *= -1
    passing = hc001._judgement_inputs(hc001.portfolio_metrics(frame), fields, 2)
    assert passing["validation_year_same_sign"] is True


def test_december_design_and_january_validation_are_labelled_by_decision():
    prices, dates = market()
    frame = hc002._observations(prices, selected(dates, "2024-12-31"))
    assert frame.entry_date.eq(pd.Timestamp("2025-01-01")).all()
    primary = hc001.portfolio_metrics(frame)
    assert set(primary["excess"]["by_year"]) == {"2024"}
    assert primary["per_rebalance"][0]["trade_date"] == "2024-12-31"
    expected_cost = cost_model.round_trip_cost(10000.0, "KOSPI", pd.Timestamp("2025-01-01").date(), 2e9)
    assert frame["cost_1.0"].eq(expected_cost).all()


@pytest.mark.parametrize("has_financials", [False, True])
def test_synthetic_runner_records_only_two_variants_and_wires_primary(tmp_path, monkeypatch, has_financials):
    registration = experiment_registry.verify_preregistration(hc002.EXPERIMENT_ID)
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *args, **kwargs: registration)
    prices, dates = market()
    archive(tmp_path / "data", [(2023, 100, 10, "2023-05-15", 1),
                                (2024, 150, 30, "2024-05-15", 1)] if has_financials else [])
    calls = []
    judge = common.judge
    def capture(fields, inputs, **kwargs):
        calls.append((inputs.copy(), kwargs.copy()))
        return judge(fields, inputs, **kwargs)
    monkeypatch.setattr(common, "judge", capture)
    calendar = pd.bdate_range(dates[0], "2026-03-31")
    result = hc002.run_experiment(prices=prices, sessions=calendar, data_dir=tmp_path / "data", repo_root=tmp_path)
    primary = result["variants"]["phase2_ew"]
    assert result["selected_variant"] == "phase2_ew" and result["verdict"] == "insufficient"
    assert result["coverage"]["planned_months"] == 103
    assert result["counts"]["holdout_horizon_exclusions"] == 0
    assert calls[0][0] == primary["judgement_inputs"]
    assert calls[0][0][hc002.NET_METRIC] == primary["excess"]["mean"]
    assert calls[0][1]["net_metric"] == hc002.NET_METRIC
    assert calls[0][1]["net_at_cost_1_5_metric"] == hc002.NET_AT_COST_1_5_METRIC
    assert result["turnover_based"]["used_for_judgement"] is False
    if has_financials:
        assert primary["excess"]["count"] == 19  # May-Dec 2024, Jan-Nov 2025.
        assert result["turnover_based"]["excess"]["mean"] > primary["excess"]["mean"]
    with (tmp_path / experiment_registry.REGISTRY_PATH).open() as handle:
        trials = list(csv.DictReader(handle))
    assert [row["variant"] for row in trials] == list(hc002.VARIANTS)
    assert trials[1]["verdict"] == "diagnostic"
    assert json.loads(trials[0]["metrics_json"]) == primary["judgement_inputs"]
    assert primary["judgement_inputs"]["trial_count_after_run"] == 2
    files = list((tmp_path / "research/experiments" / hc002.EXPERIMENT_ID / "results").glob("*"))
    assert len(files) == 2
    assert json.loads(next(path for path in files if path.suffix == ".json").read_text()) == result
    summary = next(path for path in files if path.suffix == ".md").read_text()
    assert "관측 단위는 월입니다" in summary and "초과수익 t" in summary and "판정 미사용" in summary
    assert "월별 IC 한 개를 관측 한 회" not in summary
    assert not (tmp_path / "research/experiments/holdout_ledger.jsonl").exists()


def test_holdout_guard_precedes_financial_loading_and_recording(monkeypatch):
    prices, _ = market()
    future = prices.iloc[:1].copy()
    future.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-01"), "000010")], names=prices.index.names)
    def denied(*args, **kwargs):
        raise AssertionError("holdout guard was bypassed")
    monkeypatch.setattr(hc002, "build_universe", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    with pytest.raises(ValueError, match="holdout"):
        hc002.run_experiment(prices=pd.concat([prices, future]))


def test_dry_run_and_default_cli_are_read_only(tmp_path, monkeypatch, capsys):
    archive(tmp_path, [(2024, 100, 10, "2024-05-15", 1)])
    later = tmp_path / "fundamentals/dart_quarterly/2026_11013.jsonl"
    later.write_text("holdout financials must not be opened")
    price_dir = tmp_path / "market/krx_daily/2025"
    price_dir.mkdir(parents=True)
    (price_dir / "20251230.jsonl").write_text("price coverage only; must not be opened")
    later_prices = tmp_path / "market/krx_daily/2026"
    later_prices.mkdir()
    (later_prices / "20260102.jsonl").write_text("holdout prices must not be opened")
    def denied(*args, **kwargs):
        raise AssertionError("dry-run evaluated or wrote results")
    monkeypatch.setattr(dart_quarterly, "load_quarterly", denied)
    monkeypatch.setattr(hc001.d001, "_load_prices", denied)
    monkeypatch.setattr(hc002, "run_experiment", denied)
    monkeypatch.setattr(common, "write_results", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = hc002.dry_run(data_dir=tmp_path)
    assert plan["planned_months"] == plan["guarded_months"] == 103
    assert plan["holdout_horizon_exclusions"] == 0
    assert plan["price_files"] == 1 and plan["last_price_file"] == "2025-12-30"
    assert plan["financials"]["quarters"]["2026Q1"]["rows"] is None
    assert hc002.main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
    assert hc002.main(["--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "103 before 2026" in output and "turnover=secondary only" in output and "No returns evaluated" in output
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
