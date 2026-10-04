from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, signal_eval
from backtesting.experiments import common, hi001
from src.research import industry_index


@pytest.fixture
def fields():
    # Production verifies the committed plan; synthetic tests never change it.
    return common.load_preregistration(hi001.EXPERIMENT_ID)


def prices(days=160, groups=5, members=12, start="2024-01-02"):
    dates = pd.bdate_range(start, periods=days)
    codes = [f"{(number + 1) * 10:06d}" for number in range(groups * members)]
    labels = np.repeat([chr(65 + number) for number in range(groups)], members)
    rates = np.repeat(np.arange(groups) * 0.0007, members) + np.tile(
        (np.arange(members) % 5 - 2) * 0.0002, groups)
    caps = np.tile(np.arange(1, members + 1), groups) * np.repeat(np.arange(1, groups + 1), members) * 1e9
    closing = 2000 * (1 + rates[None, :]) ** np.arange(days)[:, None]
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    frame = pd.DataFrame({"stock_name": "보통주", "market": "KOSPI", "calendar_status": "verified",
                          "industry": np.tile(labels, days), "market_cap": np.tile(caps, days),
                          "trading_value": 2e8, "ret_1d": np.tile(rates, days),
                          "close": closing.ravel(), "open": (closing / (1 + rates)).ravel()}, index=index)
    return frame, dates


def snapshots(count=4, industries=("A", "B", "C", "D", "E"), costs=0.006):
    dates = pd.to_datetime(["2024-10-31", "2024-11-29", "2025-01-31", "2025-02-28"])[:count]
    rows = []
    for number, day in enumerate(dates):
        table = pd.DataFrame(index=list(industries))
        table["rs"] = np.arange(len(table), 0, -1, dtype=float)
        table["rs_breadth"] = np.arange(1, len(table) + 1, dtype=float)
        table["rs_breadth_topcap"] = table.rs
        table["cap_gross"] = np.arange(len(table)) * 0.004 + 0.03 + number * 0.003
        table["equal_gross"] = table.cap_gross - 0.001
        for weighting in ("cap", "equal"):
            for multiplier in hi001.MULTIPLIERS:
                table[f"{weighting}_cost_{multiplier}"] = costs * multiplier
        rows.append({"trade_date": day, "entry_date": day + pd.Timedelta(days=1),
                     "exit_date": day + pd.Timedelta(days=28), "table": table,
                     "benchmark": 0.01, "regime": ("above", "below", "above", "unmeasured")[number]})
    return rows


def test_feature_formulas_use_difference_breadth_and_top_five_median():
    frame, dates = prices()
    day = dates[130]
    features, members = hi001.features_at_close(frame, day)
    current = frame.xs(day, level="trade_date")
    market_daily = np.average(current.ret_1d, weights=current.market_cap)
    market_return = (1 + market_daily) ** 60 - 1
    for label, group in members.groupby("industry"):
        industry_daily = np.average(group.ret_1d, weights=group.market_cap)
        assert features.loc[label, "rs60"] == pytest.approx((1 + industry_daily) ** 60 - 1 - market_return)
        assert features.loc[label, "breadth"] == pytest.approx(float((group.ret_1d > 0).mean()))
        top = group.market_cap.sort_values(ascending=False, kind="stable").head(5).index
        expected = float(((1 + group.loc[top, "ret_1d"]) ** 60 - 1).median()) - market_return
        assert features.loc[label, "topcap"] == pytest.approx(expected)
    for name, columns in hi001.FEATURES.items():
        pd.testing.assert_series_equal(features[name], features[list(columns)].rank(pct=True).mean(axis=1).rename(name))


def test_features_include_the_decision_close_and_ignore_future_rows():
    frame, dates = prices()
    day, code = dates[130], "000010"
    baseline, _ = hi001.features_at_close(frame, day)
    frame.loc[(day, code), "ret_1d"] = 0.2
    at_close, members = hi001.features_at_close(frame, day)
    assert at_close.loc["A", "rs60"] != baseline.loc["A", "rs60"]
    future = frame.index.get_level_values("trade_date") > day
    frame.loc[future, ["ret_1d", "close", "market_cap", "trading_value"]] = [0.9, 1e10, 1e15, 0]
    frame.loc[future, "stock_name"] = "미래 SPAC"
    unchanged, same_members = hi001.features_at_close(frame, day)
    pd.testing.assert_frame_equal(at_close, unchanged)
    pd.testing.assert_frame_equal(members, same_members)


def test_membership_filters_and_industry_minimum_are_point_in_time():
    frame, dates = prices()
    day = dates[130]
    frame.loc[(slice(None), "000010"), "stock_name"] = "Example SPAC"
    frame.loc[(slice(None), "000020"), "stock_name"] = "합성스팩"
    frame.loc[(slice(None), "000030"), "stock_name"] = "기업 인수 목적 회사"
    frame.loc[(slice(None), "000130"), "trading_value"] = hi001.MIN_ADV - 1
    frame.loc[(dates[125], "000140"), "trading_value"] = np.nan
    frame.loc[frame.industry.eq("C"), "industry"] = "미분류"
    frame.loc[frame.industry.eq("D"), "industry"] = "지주회사"
    features, members = hi001.features_at_close(frame, day)
    assert set(features.index) == {"B", "E"}
    assert features.loc["B", "member_count"] == 10
    assert "000010" not in members.index and "000130" not in members.index
    assert "000140" not in members.index
    frame.loc[(dates[131], "000130"), "trading_value"] = 1e15
    pd.testing.assert_frame_equal(features, hi001.features_at_close(frame, day)[0])


def test_hi001_does_not_inherit_d001_price_floor_or_include_preferred_shares():
    frame, dates = prices(days=90, groups=1, members=11)
    frame.loc[(slice(None), "000010"), "close"] = 500
    codes = frame.index.get_level_values("stock_code").to_series(index=frame.index).replace({"000110": "000115"})
    frame.index = pd.MultiIndex.from_arrays([frame.index.get_level_values("trade_date"), codes], names=frame.index.names)
    features, members = hi001.features_at_close(frame, dates[-1])
    assert "000010" in members.index
    assert "000115" not in members.index
    assert features.loc["A", "member_count"] == 10


@pytest.mark.parametrize("missing", ["liquidity", "unverified", "nineteen_sessions"])
def test_complete_verified_adv_is_required(missing):
    frame, dates = prices(days=25, groups=1, members=10)
    if missing == "liquidity":
        frame.loc[(dates[-5], "000010"), "trading_value"] = np.nan
    elif missing == "unverified":
        frame.loc[(dates[-5], "000010"), "calendar_status"] = "unverified_special_session"
    else:
        frame = frame.loc[frame.index.get_level_values("trade_date").isin(dates[-19:])]
    features, members = hi001.features_at_close(frame, dates[-1])
    assert features.empty
    assert "000010" not in members.index


def test_streamed_indices_match_full_indices_and_keep_retirements_permanent():
    frame, dates = prices(days=145, groups=2)
    code = "000010"
    frame = frame.drop([(day, code) for day in dates[43:46]])
    frame.loc[(dates[95], "000130"), "ret_1d"] = np.nan
    # The common decision-universe calendar gate does not change index policies.
    frame.loc[(dates[100], slice(None)), "calendar_status"] = "unverified_special_session"
    expected = industry_index.build_indices(frame, min_trading_value_20d=hi001.MIN_ADV)
    factory = lambda: (frame.loc[[day]] for day in dates)
    actual, dropped = hi001.stream_indices(factory)
    pd.testing.assert_frame_equal(actual, expected)
    assert dropped == {code: dates[43]}
    assert actual.attrs["members_dropped_no_row"] == 1
    assert actual.attrs["raw_fallback"] == 1
    assert actual.attrs["members_dropped_no_row_by_industry"] == {"A": 1}
    assert actual.attrs["raw_fallback_by_industry"] == {"B": 1}
    features, members = hi001.features_at_close(frame.loc[dates[-90]:], dates[-1], dropped=dropped)
    assert code not in members.index
    assert features.loc["A", "member_count"] == 11


def test_zero_member_coverage_is_explicitly_unmeasured():
    frame, dates = prices(days=25, groups=1)
    frame.loc[(dates[-5], slice(None)), "calendar_status"] = "unverified_special_session"
    coverage = hi001._coverage(frame, dates[-1], hi001.PROJECT_ROOT)
    assert coverage["members"] == 0
    assert coverage["industries_ge_10"] == {}
    assert coverage["mapped_cap_share"] is None and coverage["unmapped_cap_share"] is None


def test_first_day_uses_members_open_then_nineteen_prior_cap_index_returns():
    frame, dates = prices(days=165)
    day, entry, exit_day = dates[130], dates[131], dates[150]
    _, members = hi001.features_at_close(frame, day)
    frame.loc[(entry, "000010"), "open"] = frame.loc[(entry, "000010"), "close"] / 1.1
    # Same-day cap changes affect only the following session's index weights.
    frame.loc[(entry, "000020"), "market_cap"] *= 100
    indices = industry_index.build_indices(frame, min_trading_value_20d=hi001.MIN_ADV)
    result, actual_entry = hi001.holding_returns(frame, indices, members, day, exit_day)
    assert actual_entry == entry
    for label in ("A", industry_index.ALL_MARKET):
        group = members if label == industry_index.ALL_MARKET else members.loc[members.industry.eq(label)]
        entry_rows = frame.xs(entry, level="trade_date").reindex(group.index)
        leg = np.average(entry_rows.close / entry_rows.open - 1, weights=group.market_cap)
        chain = indices.xs(label, level="industry").cap_return.loc[dates[132]:exit_day]
        expected = (1 + leg) * (1 + chain).prod() - 1
        assert result.loc[label, "cap_gross"] == pytest.approx(expected)
    group = members.loc[members.industry.eq("A")]
    for multiplier in hi001.MULTIPLIERS:
        expected = np.average(cost_model.round_trip_cost_vectorized(
            group.close.to_numpy(), group.market.to_numpy(), entry.to_numpy(),
            group.avg_trading_value_20d.to_numpy(), multiplier=multiplier), weights=group.market_cap)
        assert result.loc["A", f"cap_cost_{multiplier}"] == pytest.approx(expected)
        equal_expected = cost_model.round_trip_cost_vectorized(group.close.to_numpy(), group.market.to_numpy(),
            entry.to_numpy(), group.avg_trading_value_20d.to_numpy(), multiplier=multiplier).mean()
        assert result.loc["A", f"equal_cost_{multiplier}"] == pytest.approx(equal_expected)


def test_member_missing_on_first_day_contributes_zero_without_renormalizing():
    frame, dates = prices(days=165, groups=1)
    day, entry, exit_day, code = dates[130], dates[131], dates[150], "000120"
    _, members = hi001.features_at_close(frame, day)
    frame = frame.drop([(date, code) for date in dates[131:]])
    indices = industry_index.build_indices(frame, min_trading_value_20d=hi001.MIN_ADV)
    result, _ = hi001.holding_returns(frame, indices, members, day, exit_day)
    remaining = members.drop(code)
    entry_rows = frame.xs(entry, level="trade_date").reindex(remaining.index)
    leg = float(((remaining.market_cap / members.market_cap.sum()) * (entry_rows.close / entry_rows.open - 1)).sum())
    chain = indices.xs("A", level="industry").cap_return.loc[dates[132]:exit_day]
    assert result.loc["A", "cap_gross"] == pytest.approx((1 + leg) * (1 + chain).prod() - 1)
    assert indices.attrs["members_dropped_no_row"] == 1


def test_present_member_with_missing_open_cannot_be_replaced_by_close():
    frame, dates = prices(days=165)
    day = dates[130]
    _, members = hi001.features_at_close(frame, day)
    frame.loc[(dates[131], "000010"), "open"] = np.nan
    indices = industry_index.build_indices(frame, min_trading_value_20d=hi001.MIN_ADV)
    result, _ = hi001.holding_returns(frame, indices, members, day, dates[150])
    assert np.isnan(result.loc["A", "cap_gross"])
    assert np.isnan(result.loc[industry_index.ALL_MARKET, "cap_gross"])


def test_turnover_charges_entries_and_exits_and_nothing_for_unchanged_holdings():
    assert hi001.turnover_cost(("A", "B", "C"), (), {"A": 0.01, "B": 0.02, "C": 0.03}, {}) == pytest.approx(0.02)
    assert hi001.turnover_cost(("C", "A", "B"), ("A", "B", "C"), {"A": 99, "B": 99, "C": 99}, {}) == 0
    cost = hi001.turnover_cost(("B", "C", "D"), ("A", "B", "C"),
                              {"B": 0.02, "C": 0.03, "D": 0.04}, {"A": 0.01, "B": 0.02, "C": 0.03})
    assert cost == pytest.approx((0.01 + 0.04) / 3)


def test_selection_uses_design_net_excess_t_only(fields):
    dates = pd.to_datetime(["2024-09-30", "2024-10-31", "2024-11-29", "2025-01-31", "2025-02-28"])
    values = {"rs": pd.Series([0.03, 0.031, 0.032, -1, -2], index=dates),
              "rs_breadth": pd.Series([0.01, 0.02, 0.03, 2, 2.001], index=dates),
              "rs_breadth_topcap": pd.Series([-0.02, 0.01, 0.02, 3, 3.001], index=dates)}
    selected, design = hi001.select_variant(values, fields)
    assert selected == "rs"
    assert design["rs"]["count"] == 3
    values["rs"].iloc[3:] = [99, 100]
    values["rs_breadth"].iloc[3:] = [-99, -100]
    assert hi001.select_variant(values, fields) == (selected, design)
    tied = {name: values["rs"].copy() for name in hi001.VARIANTS}
    assert hi001.select_variant(tied, fields)[0] == "rs"


def test_control_sampling_is_reproducible_without_replacement():
    first = hi001.sample_control_portfolios("ABCDE", np.random.default_rng(17))
    second = hi001.sample_control_portfolios("EDCBA", np.random.default_rng(17))
    assert first == second
    assert len(first) == 200
    assert all(len(set(portfolio)) == 3 and set(portfolio) <= set("ABCDE") for portfolio in first)
    assert len(set(first)) > 1
    with pytest.raises(ValueError, match="three"):
        hi001.sample_control_portfolios("AB", np.random.default_rng(17))


def test_controls_keep_prior_holdings_and_use_exactly_the_actual_months():
    months = snapshots(industries=("A", "B", "C"), costs=0.03)
    result = hi001.evaluate_variant(months, "rs")
    rows = result["monthly"]
    assert rows[0]["cost_1.0"] == pytest.approx(0.03)
    assert all(row["cost_1.0"] == 0 for row in rows[1:])
    expected = np.mean([row["excess_1.0"] for row in rows])
    assert result["random_control"]["mean_net_excess_by_control"] == pytest.approx([expected] * 200)
    assert result["random_control"]["monthly_observations"] == len(rows)
    assert result["random_control"]["share_of_controls"] == 1


def test_controls_pay_for_both_sides_of_a_complete_industry_change():
    months = snapshots(count=2, industries=("A", "B", "C"), costs=0.03)
    months[1]["table"].index = ["D", "E", "F"]
    result = hi001.evaluate_variant(months, "rs")
    assert result["monthly"][1]["cost_1.0"] == pytest.approx(0.06)
    assert result["random_control"]["mean_net_excess_by_control"] == pytest.approx([result["monthly_excess"]["mean"]] * 200)


def test_future_missing_return_excludes_month_without_changing_candidate_pool():
    months = snapshots(count=1)
    months[0]["table"].loc["E", "cap_gross"] = np.nan
    result = hi001.evaluate_variant(months, "rs")
    assert result["monthly"] == []
    assert result["monthly_excess"]["count"] == 0
    assert result["random_control"]["count"] == 0
    assert result["skipped_months"][0]["reason"] == "insufficient_features_or_index_horizon"


def test_judgement_uses_monthly_net_not_ic_and_validation_net_sign(fields):
    months = snapshots()
    for month in months:
        month["table"]["rs"] = 1.0  # Constant scores make IC undefined.
    result = hi001.evaluate_variant(months, "rs")
    assert result["ic"]["count"] == 0
    assert result["monthly_excess"]["count"] == 4
    design = signal_eval._summary([row["excess_1.0"] for row in result["monthly"][:2]])
    inputs = hi001._judgement_inputs(result, design, fields, 4)
    assert inputs["observations"] == 4
    assert inputs["t_stat"] == result["monthly_excess"]["t_stat"]
    assert inputs[hi001.NET_METRIC] == result["monthly_excess"]["mean"]
    assert inputs["validation_year_same_sign"] is True
    for month in months[2:]:
        month["table"]["cap_gross"] = -0.1
    negative = hi001.evaluate_variant(months, "rs")
    assert hi001._judgement_inputs(negative, design, fields, 4)["validation_year_same_sign"] is False


@pytest.mark.parametrize("direction,expected", [(1, "above"), (-1, "below"), (0, "equal")])
def test_market_regime_uses_decision_index_vs_120_session_average(direction, expected):
    dates = pd.bdate_range("2024-01-02", periods=150)
    index = pd.MultiIndex.from_product([dates, [industry_index.ALL_MARKET]], names=["trade_date", "industry"])
    indices = pd.DataFrame({"cap_index": 1000 + direction * np.arange(len(dates))}, index=index)
    assert hi001.market_regime(indices, dates[130]) == expected
    indices.loc[(dates[131:], industry_index.ALL_MARKET), "cap_index"] = 1e12
    assert hi001.market_regime(indices, dates[130]) == expected
    assert hi001.market_regime(indices, dates[118]) == "unmeasured"


def test_yearly_and_regime_splits_and_secondary_portfolios():
    result = hi001.evaluate_variant(snapshots(), "rs")
    assert result["monthly_excess"]["by_year"]["2024"]["count"] == 2
    assert result["monthly_excess"]["by_year"]["2025"]["count"] == 2
    assert result["regime_split"]["above"]["count"] == 2
    assert result["regime_split"]["below"]["count"] == 1
    assert result["regime_split"]["unmeasured"]["count"] == 1
    assert set(result["secondary"]) == {"top_1", "top_5", "equal_weight"}
    assert len(result["secondary"]["top_1"]["monthly"][0]["industries"]) == 1
    assert len(result["secondary"]["top_5"]["monthly"][0]["industries"]) == 5
    assert result["secondary"]["equal_weight"]["cost_sensitivity"]["1.0"]["mean"] != result["monthly_excess"]["mean"]


def test_decision_schedule_counts_2026_exclusion_and_missing_horizons(fields):
    days = pd.bdate_range("2015-01-02", "2025-12-31")
    decisions, skipped = hi001.decision_schedule(days, fields)
    assert len(decisions) == 119
    assert str(decisions[-1].to_period("M")) == "2025-11"
    assert skipped == [{"month": "2025-12", "reason": "20_session_horizon_would_read_2026"}]
    shortened = days[days <= "2025-11-07"]
    _, incomplete = hi001.decision_schedule(shortened, fields)
    assert {"month": "2025-11", "reason": "incomplete_20_session_horizon"} in incomplete


def test_supplied_holdout_prices_are_guarded_before_any_index_read(fields, tmp_path):
    frame, _ = prices(days=5, start="2026-01-02")
    with pytest.raises(ValueError, match="holdout"):
        hi001._inputs(tmp_path, tmp_path / "absent.jsonl", fields, tmp_path, frame)


def test_dry_run_real_file_layout_coverage_and_no_2026_read(tmp_path, monkeypatch, capsys, fields):
    directory = tmp_path / "market/krx_daily"
    companies = tmp_path / "reference/dart_company/companies.jsonl"
    companies.parent.mkdir(parents=True)
    frame, dates = prices(days=90, groups=2, members=10)
    codes = frame.index.get_level_values("stock_code").unique()
    companies.write_text("".join(json.dumps({"stock_code": code, "induty_code": "26110"}) + "\n"
                                   for code in codes[:10]), encoding="utf-8")
    for day in dates:
        path = directory / f"{day:%Y}/{day:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{**row.to_dict(), "stock_code": code, "trade_date": day.date().isoformat(),
                 "change_rate_pct": row.ret_1d * 100} for code, row in frame.xs(day).iterrows()]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    forbidden = directory / "2026/20260102.jsonl"
    forbidden.parent.mkdir()
    forbidden.write_text("MUST NOT READ HOLDOUT\n", encoding="utf-8")
    monkeypatch.setattr(common, "load_preregistration", lambda *args, **kwargs: fields)
    monkeypatch.setattr(hi001, "run_experiment", lambda **kwargs: pytest.fail("dry-run executed"))
    plan = hi001.dry_run(data_dir=directory, companies_path=companies, repo_root=tmp_path)
    assert plan["profiles_found"] == 10
    assert plan["files"] == 90
    assert plan["coverage_by_month"]["2024-02"]["industries_ge_10"] == {"반도체": 10}
    assert plan["mapped_cap_share"] == pytest.approx(1 / 3)
    assert plan["unmapped_cap_share"] == pytest.approx(2 / 3)
    assert plan["excluded_2026_count"] == 1
    assert hi001.main(["--dry-run", "--data-dir", str(directory), "--companies-path", str(companies)]) == 0
    output = capsys.readouterr().out
    assert "Profiles found (stock identities): 10" in output
    assert "industries >=10 (1): 반도체=10" in output
    assert "No portfolios evaluated" in output
    assert not (tmp_path / "research").exists()


def test_summary_keeps_primary_net_metric_and_classification_limitation(tmp_path, monkeypatch, fields):
    monkeypatch.setattr(common, "load_preregistration", lambda *args, **kwargs: fields)
    result = hi001.evaluate_variant(snapshots(), "rs")
    result.update(skipped_month_count=0, unadjusted_fallback=0, delisting_exclusions=0)
    payload = {"selected_variant": "rs", "verdict": "insufficient", "reasons": ["synthetic"], "variants": {"rs": result},
               "interpretations": hi001.INTERPRETATIONS, "assumptions": hi001.ASSUMPTIONS,
               "performance": {"runtime_seconds": 0.1, "peak_memory_mib": 20.0}}
    json_path, summary_path = hi001._write_results(payload, tmp_path)
    summary = summary_path.read_text(encoding="utf-8")
    assert "월별 상위 3개 업종 순초과수익" in summary
    assert "월별 IC 한 개를 관측 한 회" not in summary
    assert "현재 DART 스냅샷" in summary
    assert hi001.INTERPRETATIONS["note"] in summary
    assert hi001.NET_METRIC in summary
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload


def test_synthetic_runner_records_three_variants_and_final_with_monthly_judge(monkeypatch, tmp_path, fields):
    frame, _ = prices(days=190)
    recorded, judged, written = [], [], []
    monkeypatch.setattr(common, "load_preregistration", lambda *args, **kwargs: fields)
    monkeypatch.setattr(hi001.experiment_registry, "trial_count", lambda *args, **kwargs: 20)
    monkeypatch.setattr(hi001.experiment_registry, "record_trial", lambda *args, **kwargs: recorded.append((args, kwargs)))
    original_judge = common.judge
    def judge(*args, **kwargs):
        judged.append((args, kwargs))
        return original_judge(*args, **kwargs)
    monkeypatch.setattr(common, "judge", judge)
    monkeypatch.setattr(hi001, "_write_results", lambda *args: written.append(args))
    payload = hi001.run_experiment(prices=frame, repo_root=tmp_path)
    assert [args[1] for args, _ in recorded] == [*hi001.VARIANTS, "final"]
    assert written[0][0] is payload
    for (args, kwargs), result in zip(judged, payload["variants"].values()):
        assert kwargs["net_metric"] == hi001.NET_METRIC
        assert kwargs["net_at_cost_1_5_metric"] == hi001.NET_AT_COST_1_5_METRIC
        assert args[1]["observations"] == result["monthly_excess"]["count"]
        assert args[1]["trial_count_after_run"] == 24
    assert not (tmp_path / "research").exists()
