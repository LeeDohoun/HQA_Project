from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from backtesting import cost_model, event_study, experiment_registry, holdout, signal_eval
from backtesting.experiments import b001, common
from src.ingestion import krx_benchmarks


REGISTRATION = Path(__file__).resolve().parents[1] / "research/experiments" / b001.EXPERIMENT_ID / "preregistration.md"


@pytest.fixture
def fields(monkeypatch):
    text = REGISTRATION.read_text(encoding="utf-8").split("---", 2)[1]
    fields = yaml.safe_load(text)
    monkeypatch.setattr(common, "load_preregistration", lambda *args, **kwargs: deepcopy(fields))
    monkeypatch.setattr(experiment_registry, "required_t_stat", lambda *args, **kwargs: 2.0)
    return fields


def listing(number=1, day="20240201", **changes):
    return {"rcept_no": f"2024010100{number:04d}", "rcept_dt": day, "stock_code": "000010",
            "corp_name": "합성회사", "corp_cls": "Y", "report_nm": "단일판매ㆍ공급계약체결", "rm": "", **changes}


def sample(days=75, start="2024-01-02", stocks=4):
    dates = pd.bdate_range(start, periods=days)
    codes = [f"{10 * (number + 1):06d}" for number in range(stocks)]
    rng = np.random.default_rng(23)
    returns = rng.normal(0.002, 0.01, (days, stocks))
    closes = 10000 * np.cumprod(1 + returns, axis=0)
    bases = np.vstack([closes[:1], closes[:-1]])
    opening = bases * (1 + rng.normal(0, 0.001, (days, stocks)))
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    markets = ["KOSPI" if number % 2 == 0 else "KOSDAQ" for number in range(stocks)]
    prices = pd.DataFrame({"open": opening.ravel(), "high": closes.ravel() * 1.1,
                           "low": closes.ravel() * 0.9, "close": closes.ravel(), "volume": 100000.0,
                           "trading_value": 1e9, "base_price": bases.ravel(), "ret_1d": returns.ravel(),
                           "stock_name": "합성 보통주", "market": markets * days,
                           "calendar_status": "verified"}, index=index)
    benchmarks = pd.DataFrame({"trade_date": np.repeat(dates, 2), "market": ["KOSPI", "KOSDAQ"] * days,
                               "close": np.column_stack([1000 * 1.0002 ** np.arange(days),
                                                         1500 * 1.0004 ** np.arange(days)]).ravel()})
    return prices, benchmarks, dates


def filtered(listings, prices, dates):
    events, _ = b001.build_events(pd.DataFrame(listings))
    return b001.filter_events(events, prices, dates)[0]


def judgement(**changes):
    return {"design_events": 300, "mean_abnormal": 0.02, "mean_net": 0.01,
            "mean_net_at_cost_1_5": 0.005, "t_stat": 2.1, "random_control_share": 0.05,
            "validation_year_same_sign": True, "trial_count_after_run": 8, **changes}


def test_listing_filters_dedup_and_unmodified_category_rules():
    rows = [listing(9), listing(1, rm="정"), listing(2, report_nm="자기주식취득결정"),
            listing(3, day="20221231"), listing(4, corp_cls="N"),
            listing(5, report_nm="[기재정정]단일판매ㆍ공급계약체결"),
            listing(6, report_nm="[철회]단일판매ㆍ공급계약체결"), listing(7, rm="철"),
            listing(8, stock_code=""), listing(10, stock_code=None), listing(11, stock_code="000015"),
            listing(12, corp_name="합성스팩"), listing(13, corp_name="Synthetic SPAC"),
            listing(14, report_nm="증권발행실적보고서"), listing(15, day="20240202", corp_cls="K")]
    events, counts = b001.build_events(pd.DataFrame(rows))
    assert events.rcept_no.tolist() == [listing(1)["rcept_no"], listing(2)["rcept_no"], listing(15)["rcept_no"]]
    assert events.category.tolist() == ["contract", "buyback", "contract"]
    assert events.event_id.is_unique
    assert counts["raw"]["contract"] == 13
    assert counts["period"]["contract"] == 12
    assert counts["listed_market"]["contract"] == 11
    assert counts["no_corrections"]["contract"] == 10
    assert counts["no_withdrawals"]["contract"] == 8
    assert counts["stock_code"]["contract"] == 6
    assert counts["common_share"]["contract"] == 5
    assert counts["no_spac"]["contract"] == 3
    assert counts["registered_category"]["other"] == 0
    assert counts["deduplicated"]["contract"] == 2


def test_price_and_adv_filters_use_previous_session_and_match_common_universe():
    prices, _, dates = sample(stocks=8)
    decision = dates[25]
    prices.loc[(decision, "000020"), "close"] = 999
    prices.loc[(slice(None), "000030"), "trading_value"] = 0.999e8
    prices.loc[(dates[20], "000040"), "trading_value"] = np.nan
    prices.loc[(dates[20], "000050"), "calendar_status"] = "unverified_special_session"
    prices.loc[(decision, "000060"), "stock_name"] = "합성스팩"
    prices = prices.drop((decision, "000070"))
    prices.loc[(decision, "000080"), ["close", "trading_value"]] = [1000, 1e8]
    prices.loc[(slice(None), "000080"), "trading_value"] = 1e8
    rows = [listing(number, day=decision.strftime("%Y%m%d"), stock_code=code)
            for number, code in enumerate(prices.index.get_level_values("stock_code").unique())]
    events, _ = b001.build_events(pd.DataFrame(rows))
    actual, counts = b001.filter_events(events, prices, dates)
    assert actual.stock_code.tolist() == common.universe_filter(prices, decision).index.tolist() == ["000010", "000080"]
    assert counts["previous_price"]["contract"] == 7
    assert counts["price_no_spac"]["contract"] == 6
    assert counts["previous_close"]["contract"] == 5
    assert counts["adv20"]["contract"] == 2
    changed = prices.copy()
    changed.loc[(dates[26:], slice(None)), ["close", "trading_value"]] = [1, 1e15]
    pd.testing.assert_frame_equal(actual, b001.filter_events(events, changed, dates)[0])


@pytest.mark.parametrize("failure", ["short_history", "missing_value", "unverified_history"])
def test_adv_requires_twenty_verified_observations_without_price_volume_substitution(failure):
    prices, _, dates = sample()
    decision = dates[18] if failure == "short_history" else dates[25]
    if failure == "missing_value":
        prices.loc[(dates[20], "000010"), "trading_value"] = np.nan
    elif failure == "unverified_history":
        prices.loc[(dates[20], "000010"), "calendar_status"] = "unverified_special_session"
    events = filtered([listing(day=decision.strftime("%Y%m%d"))], prices, dates)
    assert events.empty


@pytest.mark.parametrize("receipt_offset,entry_offset", [(23, 24), (24, 25)])
def test_date_only_receipts_enter_next_session_and_entry_counts_as_one(receipt_offset, entry_offset):
    prices, benchmarks, dates = sample()
    day = dates[receipt_offset]
    events = filtered([listing(day=day.strftime("%Y%m%d"))], prices, dates)
    result = b001.evaluate_events(events, prices, benchmarks, dates, n_controls=3)["contract"]["horizons"]
    for h in b001.HORIZONS:
        row = result[str(h)]["events"][0]
        assert row["entry_date"] == dates[entry_offset].date().isoformat()
        assert row["scheduled_exit_date"] == dates[entry_offset + h - 1].date().isoformat()
    # A Saturday receipt also enters Monday, without using a same-day bar.
    friday = dates[(dates.dayofweek == 4) & (dates > dates[20])][0]
    saturday = friday + pd.Timedelta(days=1)
    assert saturday.dayofweek == 5
    weekend = filtered([listing(day=saturday.strftime("%Y%m%d"))], prices, dates)
    assert weekend.entry_date.iloc[0] == dates[dates.searchsorted(saturday)]


def test_holdout_horizons_excluded_before_prices_and_counted_independently():
    prices, benchmarks, dates = sample(days=100, start="2025-10-16")
    prices = prices.loc[prices.index.get_level_values("trade_date") < b001.CUTOFF]
    benchmarks = benchmarks.loc[benchmarks.trade_date < b001.CUTOFF]
    events = filtered([listing(day="20251223"), listing(2, day="20251231")], prices, dates)
    result = b001.evaluate_events(events, prices, benchmarks, dates, n_controls=3)["contract"]["horizons"]
    assert result["1"]["overall"]["count"] == result["3"]["overall"]["count"] == result["5"]["overall"]["count"] == 1
    assert result["1"]["excluded"]["holdout_horizon"] == 1
    assert result["20"]["excluded"] == {"holdout_horizon": 2}
    assert result["20"]["overall"]["count"] == 0


@pytest.mark.parametrize("source", ["prices", "benchmarks"])
def test_holdout_supplied_data_are_rejected_not_silently_trimmed(source, tmp_path):
    prices, benchmarks, dates = sample(days=50, start="2025-11-20")
    if source == "prices":
        events, _ = b001.build_events(pd.DataFrame([listing(day="20251201")]))
        call = lambda: b001.filter_events(events, prices, dates, repo_root=tmp_path)
    else:
        prices = prices.loc[prices.index.get_level_values("trade_date") < b001.CUTOFF]
        events = filtered([listing(day="20251201")], prices, dates)
        call = lambda: b001.evaluate_events(events, prices, benchmarks, dates, repo_root=tmp_path)
    with pytest.raises(ValueError, match="holdout"):
        call()
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("category", sorted(b001.POSITIVE))
def test_positive_judgement_requires_net_and_accepts_exact_control_boundary(fields, category):
    assert b001.judge_category(fields, category, judgement())[0] == "pass"
    assert b001.judge_category(fields, category, judgement(mean_net=-0.001))[0] == "fail"
    assert b001.judge_category(fields, category, judgement(mean_net_at_cost_1_5=0))[0] == "fail"


@pytest.mark.parametrize("category", sorted(b001.NEGATIVE))
def test_negative_judgement_uses_gross_ignores_cost_net_and_has_reversed_control_tail(fields, category):
    values = judgement(mean_abnormal=-0.02, mean_net=None, mean_net_at_cost_1_5=None,
                       t_stat=-2.1, random_control_share=0.95)
    assert b001.judge_category(fields, category, values)[0] == "pass"
    assert b001.judge_category(fields, category, {**values, "random_control_share": 0.05})[0] == "fail"
    assert b001.judge_category(fields, category, {**values, "mean_abnormal": 0.001})[0] == "fail"


@pytest.mark.parametrize("category,t", [("contract", 2), ("capital_raise", -2), ("contract", 3), ("capital_raise", -3)])
def test_t_threshold_is_strict_and_includes_impending_eight_trials(fields, category, t):
    values = judgement(t_stat=t, trial_count_after_run=24 if abs(t) == 3 else 8)
    if category in b001.NEGATIVE:
        values.update(mean_abnormal=-0.02, random_control_share=0.95)
    assert b001.judge_category(fields, category, values)[0] == "fail"
    values["t_stat"] += 0.01 if t > 0 else -0.01
    assert b001.judge_category(fields, category, values)[0] == "pass"


def test_registry_required_threshold_cannot_be_lowered_by_batch_input(fields, monkeypatch):
    monkeypatch.setattr(experiment_registry, "required_t_stat", lambda *args, **kwargs: 3)
    assert b001.judge_category(fields, "contract", judgement(t_stat=2.9))[0] == "fail"


@pytest.mark.parametrize("category", b001.CATEGORIES)
def test_minimum_is_design_event_count_for_every_category(fields, category):
    assert b001.judge_category(fields, category, judgement(design_events=299))[0] == "insufficient"


@pytest.mark.parametrize("category", ["contract", "capital_raise"])
def test_validation_year_sign_is_required(fields, category):
    values = judgement(validation_year_same_sign=False)
    if category in b001.NEGATIVE:
        values.update(mean_abnormal=-0.02, t_stat=-2.1, random_control_share=0.95)
    assert b001.judge_category(fields, category, values)[0] == "fail"
    assert b001.judge_category(fields, category, {**values, "validation_year_same_sign": None})[0] == "insufficient"


def diagnostic_result(mean=0.01, t=2.5, share=0.02):
    return {"overall": {"mean_abnormal": mean, "mean_net": mean - 0.005, "t_stat": t, "net_t_stat": 1.5},
            "design": {"count": 300, "mean_abnormal": mean, "mean_net": mean - 0.005},
            "by_year": {"2025": {"mean_abnormal": mean, "mean_net": mean - 0.005}},
            "cost_sensitivity": {"1.5": {"mean_net": mean - 0.007}},
            "random_control": {"share_of_controls": share}}


@pytest.mark.parametrize("category", ["earnings", "merger"])
@pytest.mark.parametrize("t,share,direction", [(2.5, 0.02, "positive"), (-2.5, 0.98, "negative"),
                                              (1.9, 0.02, None), (2.5, 0.2, None), (None, None, None)])
def test_undecided_records_only_supported_direction_never_passes(fields, category, t, share, direction):
    result = diagnostic_result(mean=-0.01 if t is not None and t < 0 else 0.01, t=t, share=share)
    inputs = b001.judgement_inputs(fields, category, result, 8)
    assert inputs["direction"] == direction
    assert inputs["abs_t_stat"] == (abs(t) if t is not None else None)
    assert b001.judge_category(fields, category, inputs)[0] == "direction_only"


def test_judgement_selects_direction_specific_t_and_yearly_mean(fields):
    result = diagnostic_result()
    positive = b001.judgement_inputs(fields, "contract", result, 8)
    negative = b001.judgement_inputs(fields, "capital_raise", result, 8)
    assert positive["t_stat"] == 1.5
    assert negative["t_stat"] == 2.5
    result["by_year"]["2025"]["mean_net"] = -0.01
    assert b001.judgement_inputs(fields, "contract", result, 8)["validation_year_same_sign"] is False
    assert b001.judgement_inputs(fields, "capital_raise", result, 8)["validation_year_same_sign"] is True


@pytest.mark.parametrize("mean,t,share", [(-0.01, 2.5, 0.02), (0.01, -2.5, 0.98)])
def test_undecided_direction_requires_event_mean_and_date_clustered_t_to_agree(fields, mean, t, share):
    # Unequal event counts per date can give event- and date-weighted means different signs.
    result = diagnostic_result(mean=mean, t=t, share=share)
    inputs = b001.judgement_inputs(fields, "earnings", result, 8)
    assert inputs["direction"] is None
    assert b001.judge_category(fields, "earnings", inputs)[0] == "direction_only"


def test_negative_control_share_means_actual_below_controls_including_ties():
    control_means = [-0.03] * 10 + [-0.02] * 10 + [0.01] * 180
    summary = signal_eval._control_summary(control_means, -0.02)
    assert summary["share_of_controls"] == 0.95
    assert sum(mean >= -0.02 for mean in control_means) == 190


@pytest.mark.parametrize("policy_case", ["adjusted", "raw_fallback", "stale_missing", "stale_zero_volume", "halted", "no_later_price", "limit_up", "limit_down", "missing_benchmark"])
def test_vectorized_equality_with_run_event_study_for_all_horizons(policy_case):
    prices, benchmarks, dates = sample()
    # Several events on each date verify clustered rather than event-level t.
    listings = [listing(number * 4 + stock, day=dates[number].strftime("%Y%m%d"), stock_code=code)
                for number in (24, 26, 29) for stock, code in enumerate(("000010", "000020"))]
    entry, stock = dates[25], "000010"
    if policy_case == "adjusted":
        # A split changes raw prices without a -50% adjusted return.
        prices.loc[(dates[26:], stock), ["open", "high", "low", "close", "base_price"]] *= 0.5
    elif policy_case == "raw_fallback":
        prices.loc[(dates[27], stock), "ret_1d"] = np.nan
    elif policy_case == "stale_missing":
        prices = prices.drop((dates[29], stock))
    elif policy_case == "stale_zero_volume":
        prices.loc[(dates[29], stock), "volume"] = 0
    elif policy_case == "halted":
        prices.loc[(dates[26:30], stock), ["volume", "close", "ret_1d"]] = [0, 0, np.nan]
    elif policy_case == "no_later_price":
        prices = prices.drop([(day, stock) for day in dates[29:]])
    elif policy_case == "limit_up":
        prices.loc[(entry, stock), "open"] = prices.loc[(entry, stock), "base_price"] * 1.3
    elif policy_case == "limit_down":
        prices.loc[(dates[29], stock), "close"] = prices.loc[(dates[29], stock), "base_price"] * 0.7
    elif policy_case == "missing_benchmark":
        benchmarks = benchmarks.loc[~((benchmarks.trade_date == dates[29]) & (benchmarks.market == "KOSPI"))]
    events = filtered(listings, prices, dates)
    actual = b001.evaluate_events(events, prices, benchmarks, dates, n_controls=35, seed=9)["contract"]["horizons"]
    reference_events = events.copy()
    reference_events["available_at"] = reference_events.available_at.dt.date
    expected = event_study.run_event_study(reference_events, prices, benchmarks, n_controls=35, seed=9)
    for h in b001.HORIZONS:
        ours, theirs = actual[str(h)], expected["horizons"][str(h)]
        assert ours["overall"] == pytest.approx(theirs["overall"])
        assert ours["excluded"] == theirs["excluded"]
        assert ours["excluded_count"] == theirs["excluded_count"]
        for row, target in zip(ours["events"], theirs["events"], strict=True):
            for key in ("gross", "abnormal", "cost", "net"):
                assert row[key] == pytest.approx(target[key])
            for key in ("event_id", "stock_code", "entry_date", "exit_date", "scheduled_exit_date", "price_basis", *signal_eval._RETURN_FLAGS):
                assert row[key] == target[key]
        gross = ours["random_control"]["gross"]
        for key in ("count", "p05", "p50", "p95", "share_of_controls", *signal_eval._RETURN_FLAGS):
            assert gross[key] == pytest.approx(theirs["random_control"][key])
        assert gross["valid_events_per_draw"] == theirs["random_control"]["valid_events_per_draw"]
        assert gross["excluded"] == theirs["random_control"]["excluded"]
    json.dumps(actual, allow_nan=False)


def test_cost_multipliers_and_positive_controls_compare_same_net_returns():
    prices, benchmarks, dates = sample()
    # Homogeneous entry pools isolate cost semantics from random stock selection.
    prices[["open", "close", "base_price"]] = 10000.0
    prices["ret_1d"] = 0.0
    prices["market"] = "KOSPI"
    benchmarks["close"] = 1000.0
    events = filtered([listing(day=dates[24].strftime("%Y%m%d"))], prices, dates)
    result = b001.evaluate_events(events, prices, benchmarks, dates)["contract"]["horizons"]["5"]
    for multiplier in (1.0, 1.5, 2.0):
        cost = cost_model.round_trip_cost(10000, "KOSPI", dates[25].date(), 1e9, multiplier=multiplier)
        assert result["cost_sensitivity"][str(multiplier)]["mean_net"] == pytest.approx(-cost)
    assert result["random_control"]["metric"] == "net"
    assert result["random_control"]["net"]["count"] == 200
    assert result["random_control"]["net"]["p50"] == pytest.approx(result["overall"]["mean_net"])
    assert result["random_control"]["share_of_controls"] == 1.0
    assert result["liquidity_buckets"]["0"]["count"] == 0


def test_receipt_year_defines_design_even_if_entry_is_in_2025():
    prices, benchmarks, dates = sample(days=80, start="2024-11-01")
    dates = dates[dates != pd.Timestamp("2025-01-01")]
    prices = prices.drop(pd.Timestamp("2025-01-01"), level="trade_date")
    benchmarks = benchmarks.loc[benchmarks.trade_date != "2025-01-01"]
    events = filtered([listing(day="20241231"), listing(2, day="20250102")], prices, dates)
    result = b001.evaluate_events(events, prices, benchmarks, dates, n_controls=3)["contract"]["horizons"]["5"]
    assert result["design"]["count"] == result["by_year"]["2024"]["count"] == 1
    assert result["by_year"]["2025"]["count"] == 1
    assert result["events"][0]["entry_date"] == "2025-01-02"


def test_missing_whole_price_session_cannot_shift_exit():
    prices, benchmarks, dates = sample()
    prices = prices.drop(dates[30], level="trade_date")
    events = filtered([listing(day=dates[24].strftime("%Y%m%d"))], prices, dates)
    with pytest.raises(ValueError, match="shift holding windows"):
        b001.evaluate_events(events, prices, benchmarks, dates)


def test_benchmark_loader_latest_broad_indices_skips_future_values(tmp_path):
    observed = datetime(2026, 10, 5, tzinfo=timezone.utc)
    rows = [krx_benchmarks._record("KOSPI", "코스피", krx_benchmarks._date("20240201"), 1000.0, observed),
            krx_benchmarks._record("KOSPI", "코스피", krx_benchmarks._date("20240201"), 1010.0, observed.replace(hour=1)),
            krx_benchmarks._record("KOSDAQ", "코스닥", krx_benchmarks._date("20240201"), 1500.0, observed),
            krx_benchmarks._record("KOSPI", "코스피 200", krx_benchmarks._date("20240201"), 400.0, observed),
            {"trade_date": "2026-01-02", "series": "KOSPI", "index_name": "코스피", "close": "must not inspect"}]
    directory = tmp_path / "market_context"
    directory.mkdir()
    path = directory / "benchmarks.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    loaded = b001.load_benchmarks(tmp_path)
    assert loaded.market.tolist() == ["KOSDAQ", "KOSPI"]
    assert loaded.close.tolist() == [1500, 1010]
    rows[1] = krx_benchmarks._record("KOSPI", "코스피", krx_benchmarks._date("20240201"), 1010.0, observed)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        b001.load_benchmarks(tmp_path)


def test_dry_run_loads_synthetic_archives_only_and_writes_nothing(fields, tmp_path, monkeypatch):
    prices, benchmarks, dates = sample()
    rows = [listing(day=dates[24].strftime("%Y%m%d")), listing(2, day=dates[25].strftime("%Y%m%d"))]
    path = tmp_path / "disclosures/dart_full/list/2024" / (dates[24].strftime("%Y%m%d") + ".jsonl")
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    # No 2026 listing should be opened, even if it is invalid JSON.
    forbidden = tmp_path / "disclosures/dart_full/list/2026/20260102.jsonl"
    forbidden.parent.mkdir()
    forbidden.write_text("not JSON\n")
    price_directory = tmp_path / "market/krx_daily/2024"
    price_directory.mkdir(parents=True)
    for day in dates:
        daily = prices.xs(day).reset_index().drop(columns="ret_1d")
        daily["trade_date"] = day.date().isoformat()
        daily["change_rate_pct"] = prices.xs(day).ret_1d.to_numpy() * 100
        (price_directory / f"{day:%Y%m%d}.jsonl").write_text(daily.to_json(orient="records", lines=True))
    monkeypatch.setattr(b001, "_session_calendar", lambda: dates)
    monkeypatch.setattr(b001, "evaluate_events", lambda *args, **kwargs: pytest.fail("dry-run evaluated returns"))
    monkeypatch.setattr(b001, "write_results", lambda *args, **kwargs: pytest.fail("dry-run wrote results"))
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *args, **kwargs: pytest.fail("dry-run recorded trial"))
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    plan = b001.dry_run(data_dir=tmp_path, repo_root=tmp_path)
    after = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert plan["listing_files"] == 1
    assert plan["filter_counts"]["adv20"]["contract"] == 2
    assert plan["horizons"]["5"]["planned"]["contract"] == 2
    assert plan["horizons"]["5"]["benchmark_covered"]["contract"] == 0
    assert plan["benchmark_file_exists"] is False
    assert plan["benchmark_coverage"]["KOSPI"]["sessions"] == 0


def test_cli_defaults_to_dry_run(monkeypatch, capsys):
    plan = {"data_dir": "synthetic", "read_start": "2022-12-01", "read_end": "2025-12-31",
            "listing_files": 1, "price_sessions": 30, "price_rows": 120, "filter_counts": {},
            "missing_price_sessions": [], "benchmark_file_exists": False, "benchmark_coverage": {}, "horizons": {}}
    monkeypatch.setattr(b001, "dry_run", lambda **kwargs: plan)
    monkeypatch.setattr(b001, "run_experiment", lambda **kwargs: pytest.fail("default CLI executed"))
    assert b001.main([]) == b001.main(["--dry-run"]) == 0
    assert "No returns, random draws, registry rows or result files written" in capsys.readouterr().out


def test_synthetic_run_records_eight_categories_with_batch_threshold_and_finite_results(fields, tmp_path, monkeypatch):
    prices, benchmarks, dates = sample()
    monkeypatch.setattr(experiment_registry, "trial_count", lambda *args, **kwargs: 16)
    captured = []
    monkeypatch.setattr(experiment_registry, "record_trial", lambda experiment, variant, metrics, verdict, **kwargs:
                        captured.append((experiment, variant, metrics, verdict)))
    payload = b001.run_experiment(listings=pd.DataFrame([listing(day=dates[24].strftime("%Y%m%d"))]),
                                  prices=prices, benchmarks=benchmarks, sessions=dates, repo_root=tmp_path)
    assert len(captured) == 8
    assert [row[1] for row in captured] == list(b001.CATEGORIES)
    assert all(row[2]["trial_count_after_run"] == 24 and row[2]["required_t_stat"] == 3 for row in captured)
    assert all(row[3] == "insufficient" for row in captured)
    directory = tmp_path / "research/experiments" / b001.EXPERIMENT_ID / "results"
    json_path, = directory.glob("*.json")
    summary_path, = directory.glob("*.md")
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload
    summary = summary_path.read_text(encoding="utf-8")
    assert "interpretations" in summary and b001.INTERPRETATIONS["note"] in summary
    json.dumps(payload, allow_nan=False)


def test_missing_benchmark_execution_fails_before_trials_or_results(fields, tmp_path, monkeypatch):
    prices, benchmarks, dates = sample()
    monkeypatch.setattr(experiment_registry, "record_trial", lambda *args, **kwargs: pytest.fail("recorded incomplete experiment"))
    with pytest.raises(ValueError, match="benchmark coverage"):
        b001.run_experiment(listings=pd.DataFrame([listing(day=dates[24].strftime("%Y%m%d"))]),
                             prices=prices, benchmarks=benchmarks.iloc[:-1], sessions=dates, repo_root=tmp_path)
    assert not (tmp_path / "research").exists()
