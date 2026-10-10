"""Offline synthetic characteristic matching, HC002 path and publication tests."""
from __future__ import annotations

import json
import re
from itertools import product

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry
from backtesting.diagnostics.matched_controls import assign_cells, matched_controls
from backtesting.experiments import common, hc002
from scripts.research import hc002_matched_controls as runner


def flat(n=15, months=("2024-05-31",)):
    frame = pd.DataFrame([{"decision_date": pd.Timestamp(day), "stock_code": f"{(i + 1) * 10:06d}",
                           "market": "KOSPI", "market_cap": 1e10, "avg_trading_value_20d": 2e9,
                           "volatility_60d": 0.02, "industry": "반도체"}
                          for day in months for i in range(n)])
    return frame


def outcomes(features, values=0.01):
    result = features[["decision_date", "stock_code"]].copy()
    result["gross"] = values
    return result


def choose(features, codes):
    return features.loc[features.stock_code.isin(codes), ["decision_date", "stock_code"]]


def cube():
    rows, selected = [], []
    for number, (market, size, adv, vol, industry, peer) in enumerate(product(
            ("KOSPI", "KOSDAQ"), range(3), range(3), range(3), ("반도체", "자동차·부품"), range(7))):
        code = f"{(number + 1) * 10:06d}"
        rows.append({"decision_date": pd.Timestamp("2024-05-31"), "stock_code": code, "market": market,
                     "market_cap": (size + 1) * 1e10, "avg_trading_value_20d": (adv + 1) * 2e9,
                     "volatility_60d": (vol + 1) * 0.01, "industry": industry})
        if peer == 0:
            selected.append(code)
    features = pd.DataFrame(rows)
    returns = outcomes(features, 0.01 * features.market_cap / 1e10 + 0.005 * features.avg_trading_value_20d / 2e9
                       + 0.002 * features.volatility_60d / 0.01)
    return features, choose(features, selected), returns


def test_cells_are_point_in_time_market_local_and_ties_stay_together():
    features = flat(9, ("2024-05-31", "2024-06-28"))
    features["market_cap"] = np.tile(np.repeat([1e9, 2e9, 3e9], 3), 2)
    baseline = assign_cells(features)
    future = features.decision_date.eq(pd.Timestamp("2024-06-28"))
    features.loc[future, "market_cap"] = np.arange(9, 0, -1) * 1e12
    other_market = features.loc[~future].assign(market="KOSDAQ", stock_code=lambda x: "K" + x.stock_code, market_cap=1.0)
    changed = assign_cells(pd.concat([features, other_market], ignore_index=True))
    pd.testing.assert_frame_equal(baseline.loc[pd.Timestamp("2024-05-31")],
                                  changed.xs(pd.Timestamp("2024-05-31")).loc[baseline.loc[pd.Timestamp("2024-05-31")].index])
    may = baseline.loc[pd.Timestamp("2024-05-31")]
    assert may.size3.tolist() == [1] * 3 + [2] * 3 + [3] * 3
    assert may.size_percentile.tolist() == pytest.approx([1 / 6] * 3 + [0.5] * 3 + [5 / 6] * 3)


def test_future_feature_date_is_rejected():
    features = flat()
    features["feature_date"] = pd.Timestamp("2024-06-03")
    with pytest.raises(ValueError, match="decision close"):
        matched_controls(choose(features, ["000010"]), features, outcomes(features))


@pytest.mark.parametrize("column", ["market_cap", "avg_trading_value_20d", "volatility_60d"])
def test_missing_characteristics_fail_without_substitution(column):
    features = flat()
    features.loc[0, column] = np.nan
    with pytest.raises(ValueError, match=column):
        matched_controls(choose(features, ["000010"]), features, outcomes(features))


def test_matching_respects_all_cells_and_industry_without_selected_names():
    features, selection, returns = cube()
    result = matched_controls(selection, features, returns, draws=17, seed=3)
    assert result["relaxations"] == {"industry": 0, "vol": 0}
    assert result["replacement_slots"] == 0
    month = result["monthly"][0]
    controls = month["control_counts"]
    assert set(controls).isdisjoint(selection.stock_code)
    assert sum(controls.values()) == 17 * len(selection)
    assert max(controls.values()) <= 17
    ranked = assign_cells(features).reset_index().set_index("stock_code")
    cells = ["market", "size3", "adv3", "vol3", "industry"]
    selected_counts = ranked.loc[selection.stock_code].groupby(cells, observed=True).size()
    control_counts = ranked.loc[list(controls)].assign(weight=pd.Series(controls)).groupby(cells, observed=True).weight.sum()
    pd.testing.assert_series_equal(control_counts, (17 * selected_counts).rename("weight"))
    assert month["difference"] == pytest.approx(0.0, abs=1e-14)
    for characteristic in ("size", "adv", "vol"):
        assert result["balance"]["selection"][f"mean_{characteristic}_percentile"] == pytest.approx(
            result["balance"]["matched_controls"][f"mean_{characteristic}_percentile"])
    assert sum(result["balance"]["matched_controls"]["industry_shares"].values()) == pytest.approx(1.0)
    assert matched_controls(selection.sample(frac=1, random_state=1), features.sample(frac=1, random_state=2),
                            returns.sample(frac=1, random_state=3), draws=17, seed=3) == result
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("same_industry,expected", [(4, 1), (5, 0)])
def test_industry_relaxation_threshold_counts_stock_months_not_draws(same_industry, expected):
    features = flat(8)
    features.loc[same_industry + 1:, "industry"] = "자동차·부품"
    result = matched_controls(choose(features, ["000010"]), features, outcomes(features), draws=11)
    assert result["relaxations"] == {"industry": expected, "vol": 0}
    assert result["monthly"][0]["matching"][0]["relaxed"] == (["industry"] if expected else [])


def test_vol_relaxation_follows_industry_and_preserves_market_size_adv():
    features = flat(15)
    features.loc[1:, "industry"] = "자동차·부품"
    features.loc[2:, "volatility_60d"] = 0.04
    result = matched_controls(choose(features, ["000010"]), features, outcomes(features), draws=9)
    match = result["monthly"][0]["matching"][0]
    assert result["relaxations"] == {"industry": 1, "vol": 1}
    assert match["relaxed"] == ["industry", "vol"]
    assert set(match["conditions"]) == {"market", "size3", "adv3"}
    assert match["candidate_count"] == 14


def test_nested_relaxed_pools_use_tightest_pool_first():
    features = flat(15)
    features.loc[[0, 11, 12, 13, 14], "industry"] = "자동차·부품"
    selection = choose(features, features.stock_code.iloc[:6])
    result = matched_controls(selection, features, outcomes(features), draws=23)
    assert result["relaxations"] == {"industry": 1, "vol": 0}
    assert result["replacement_slots"] == 0
    counts = result["monthly"][0]["control_counts"]
    assert max(counts.values()) == 23
    assert all(counts[code] == 23 for code in features.stock_code.iloc[6:11])


def test_unavoidable_replacement_is_counted_and_months_can_reuse_names():
    features = flat(8, ("2024-05-31", "2024-06-28"))
    result = matched_controls(choose(features, features.stock_code.iloc[:6]), features, outcomes(features), draws=13)
    assert result["relaxations"] == {"industry": 12, "vol": 12}
    assert result["replacement_slots"] == 2 * 13 * 4
    for row in result["monthly"]:
        assert set(row["control_counts"]) == {"000070", "000080"}
        assert sum(row["control_counts"].values()) == 13 * 6
        assert min(row["control_counts"].values()) >= 13


def test_empty_pool_fails_after_only_requested_relaxations():
    features = flat(2)
    features.loc[1, "market"] = "KOSDAQ"
    with pytest.raises(ValueError, match="no non-selected matched controls.*2024-05-31 000010"):
        matched_controls(choose(features, ["000010"]), features, outcomes(features))


def test_size_loading_beats_random_controls_but_has_zero_matched_difference():
    features = flat(90, ("2024-05-31", "2024-06-28", "2024-07-31"))
    features["market_cap"] = np.tile(np.repeat([1e9, 2e9, 3e9], 30), 3)
    returns = outcomes(features, np.tile(np.repeat([0.01, 0.02, 0.06], 30), 3))
    selection = choose(features, features.stock_code.iloc[60:70])
    result = matched_controls(selection, features, returns)
    actual = result["monthly"][0]["selection_gross"]
    rng = np.random.default_rng(0)
    random = [returns.gross.iloc[:90].to_numpy()[rng.choice(90, 10, replace=False)].mean() for _ in range(200)]
    assert actual - np.mean(random) > 0.025
    assert result["random_control"]["actual_mean_difference"] > 0.025
    assert result["difference"]["mean"] == pytest.approx(0.0, abs=1e-14)
    assert all(row["matched_control_gross"] == pytest.approx(0.06) for row in result["monthly"])


def test_true_alpha_survives_matching_and_draw_share_compares_common_benchmark():
    features, selection, returns = cube()
    returns.loc[returns.stock_code.isin(selection.stock_code), "gross"] += 0.02
    result = matched_controls(selection, features, returns, draws=31)
    assert result["difference"]["mean"] == pytest.approx(0.02)
    assert result["regression"]["phase2_coefficient"]["mean"] == pytest.approx(0.02)
    draw = result["random_control"]
    assert draw["share_of_controls"] == 0.0
    assert draw["selection_minus_control_by_draw"] == pytest.approx([0.02] * 31)
    assert draw["share_of_controls"] == np.mean(np.asarray(draw["mean_differences"]) >= draw["actual_mean_difference"])


def test_cross_sectional_coefficient_matches_hand_normal_equations_and_nw():
    months = ("2024-05-31", "2024-07-31", "2024-08-30")
    features = flat(30, months)
    rng = np.random.default_rng(91)
    for column in ("market_cap", "avg_trading_value_20d", "volatility_60d"):
        features[column] *= np.tile(rng.uniform(0.5, 1.5, 30), 3)
    features["industry"] = np.tile(np.where(np.arange(30) % 2, "자동차·부품", "반도체"), 3)
    selection = choose(features, features.stock_code.iloc[[0, 3, 8, 12, 17, 22, 29]])
    ranked = assign_cells(features).reset_index()
    d = ranked.stock_code.isin(selection.stock_code).to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(ranked)), d, ranked[["size_percentile", "adv_percentile", "vol_percentile"]],
                         ranked.industry.eq("자동차·부품").astype(float)])
    values, expected = [], []
    for i, alpha in enumerate((0.012, 0.02, -0.004)):
        xm = x[i * 30:(i + 1) * 30]
        y = xm @ [0.01, alpha, 0.015, -0.003, 0.008, 0.002] + np.arange(30) % 4 * 0.0001
        expected.append(np.linalg.solve(xm.T @ xm, xm.T @ y)[1])
        values.extend(y)
    result = matched_controls(selection, features, outcomes(ranked, values), cells=("market",), industry=False, draws=7)
    regression = result["regression"]
    assert [row["phase2_coefficient"] for row in regression["monthly"]] == pytest.approx(expected)
    residual = np.asarray(expected) - np.mean(expected)
    # May->July is a gap, so only July->August contributes at lag 1.
    se = np.sqrt((residual @ residual + residual[1] * residual[2]) / 3**2)
    assert regression["phase2_coefficient"]["standard_error"] == pytest.approx(se)
    assert regression["phase2_coefficient"]["t_stat"] == pytest.approx(np.mean(expected) / se)


def test_matched_difference_nw_matches_hand_computation():
    features = flat(10, ("2024-05-31", "2024-06-28", "2024-07-31"))
    selection = choose(features, ["000010"])
    returns = outcomes(features, 0.0)
    expected = np.array([0.01, 0.03, -0.01])
    returns.loc[returns.stock_code.eq("000010"), "gross"] = expected
    result = matched_controls(selection, features, returns, draws=5)
    residual = expected - expected.mean()
    se = np.sqrt((residual @ residual + residual[:-1] @ residual[1:]) / 9)
    assert result["difference"]["mean"] == pytest.approx(expected.mean())
    assert result["difference"]["standard_error"] == pytest.approx(se)
    assert result["difference"]["t_stat"] == pytest.approx(expected.mean() / se)


def test_regression_reports_unidentified_dummy_instead_of_pseudoinverse_coefficient():
    features = flat(12)
    features.loc[:5, "industry"] = "자동차·부품"
    selection = choose(features, features.stock_code.iloc[:6])
    result = matched_controls(selection, features, outcomes(features), cells=("market",), industry=False)
    assert result["regression"]["monthly"][0]["reason"] == "phase2_dummy_not_identified"
    assert result["regression"]["phase2_coefficient"]["mean"] is None


def test_execution_exclusions_do_not_move_close_known_cells():
    features = flat(12)
    features["market_cap"] = np.arange(1, 13) * 1e9
    selection = choose(features, ["000030", "000120"])
    returns = outcomes(features)
    returns.loc[returns.stock_code.eq("000120"), "gross"] = np.nan
    result = matched_controls(selection, features, returns, draws=5)
    assert result["exclusions"]["universe_returns"] == result["exclusions"]["selection_returns"] == 1
    assert result["monthly"][0]["selection_count"] == 1
    assert result["monthly"][0]["matching"][0]["conditions"]["size3"] == 1
    assert result["balance"]["selection"]["mean_size_percentile"] == pytest.approx(2.5 / 12)


def hc002_archives(data_dir, policy):
    sessions = hc002.hc001._session_calendar()
    days = sessions[(sessions >= "2024-01-02") & (sessions <= "2024-07-31")]
    codes = [f"{10 * (i + 1):06d}" for i in range(8)]
    entry = days.get_loc(pd.Timestamp("2024-05-31")) + 1
    target = days[entry + 19]
    for day in days:
        rows = []
        for i, code in enumerate(codes):
            if policy == "stale" and day == target and i == 0:
                continue
            close = 5000.0 if policy == "split" and day == target and i == 0 else 10000.0
            rows.append({"trade_date": day.date().isoformat(), "stock_code": code, "stock_name": "합성보통주",
                         "market": "KOSPI", "open": 10000.0, "close": close, "base_price": close, "volume": 10000,
                         "trading_value": 2e9, "market_cap": 1e10, "calendar_status": "verified",
                         "change_rate_pct": 0.1 if i == 0 else 0.01})
        path = data_dir / "market/krx_daily/2024" / f"{day:%Y%m%d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    quarterly = data_dir / "fundamentals/dart_quarterly"
    quarterly.mkdir(parents=True)
    for year in (2023, 2024):
        records = []
        for i, code in enumerate(codes):
            values = {"revenue": 100 if year == 2023 else 150,
                      "operating_income": 10 if year == 2023 else (30 if i == 0 else 12), "net_income": 1}
            records.append({"stock_codes": [code], "corp_code": f"{i + 1:08d}", "bsns_year": str(year),
                            "reprt_code": "11013", "fiscal_quarter": f"{year}Q1", "fs_div": "CFS",
                            "available_date": f"{year}-05-15", "rcept_no": f"{year}0515{i + 1:06d}", "currency": "KRW",
                            "raw_accounts": {metric: {"thstrm_amount": str(value), "thstrm_add_amount": str(value),
                                                       "thstrm_dt": None, "currency": "KRW"} for metric, value in values.items()}})
        (quarterly / f"{year}_11013.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    reference = data_dir / "reference/dart_company/companies.jsonl"
    reference.parent.mkdir(parents=True)
    reference.write_text("".join(json.dumps({"stock_code": code, "induty_code": "261"}) + "\n" for code in codes))
    # Invalid future JSON must not be opened by any price or filing path.
    for relative in ("market/krx_daily/2026/20260102.jsonl", "fundamentals/dart_quarterly/2026_11013.jsonl"):
        path = data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("2026 must not be opened")
    return days, target


@pytest.mark.parametrize("policy", ["normal", "split", "stale"])
def test_runner_uses_exact_hc002_paths_and_only_publishes_diagnostics(tmp_path, monkeypatch, policy):
    days, target = hc002_archives(tmp_path / "data", policy)
    monkeypatch.setattr(hc002, "MONTHS", pd.period_range("2024-05", "2024-05", freq="M"))
    calls = {"universe": 0, "returns": 0}
    universe_method, returns_method = hc002.universe_on_decision, hc002.forward_observations

    def universe(*args, **kwargs):
        calls["universe"] += 1
        return universe_method(*args, **kwargs)

    def forward(*args, **kwargs):
        calls["returns"] += 1
        assert kwargs["last_dates"].loc["000010"] > target
        return returns_method(*args, **kwargs)

    def denied(*args, **kwargs):
        raise AssertionError("diagnostics must never judge, register or run a registered experiment")

    monkeypatch.setattr(hc002, "universe_on_decision", universe)
    monkeypatch.setattr(hc002, "forward_observations", forward)
    monkeypatch.setattr(hc002, "run_experiment", denied)
    monkeypatch.setattr(common, "judge", denied)
    monkeypatch.setattr(common, "write_results", denied)
    monkeypatch.setattr(experiment_registry, "record_trial", denied)
    registry = tmp_path / "research/experiments/registry.csv"
    registry.parent.mkdir(parents=True)
    registry.write_text("existing registry bytes\n")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    payload = runner.run_diagnostic(data_dir=tmp_path / "data")
    assert calls == {"universe": 1, "returns": 1}
    assert payload["monthly"][0]["selection_count"] == 1
    assert payload["monthly"][0]["selection_gross"] == pytest.approx((1.001) ** (18 if policy == "stale" else 19) - 1)
    assert payload["return_counts"]["unadjusted_fallback"] == 0
    assert payload["return_counts"]["no_later_price"] == 0
    assert payload["return_counts"]["stale_exit"] == (1 if policy == "stale" else 0)
    assert payload["performance"]["max_price_window_sessions"] == 80
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    paths = runner.write_diagnostic(payload, repo_root=tmp_path)
    assert len(paths) == 2 and all(p.parent.name == "diagnostics" for p in paths)
    assert re.fullmatch(r"matched_controls_\d{8}T\d{12}Z\.json", paths[0].name)
    assert paths[1].stem == paths[0].stem
    saved = json.loads(paths[0].read_text())
    assert saved == payload and saved["used_for_judgement"] is False
    assert '"verdict"' not in paths[0].read_text() and '"judgement_inputs"' not in paths[0].read_text()
    assert "사후 진단, 판정에 쓰지 않음" in paths[1].read_text()
    assert "특성 균형" in paths[1].read_text() and "회귀 국면 계수" in paths[1].read_text()
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert set(after) - set(before) == {p.relative_to(tmp_path) for p in paths}
    assert all(after[path] == value for path, value in before.items())


def test_default_cli_and_dry_run_do_not_read_prices_evaluate_or_write(tmp_path, monkeypatch, capsys):
    for relative in ("market/krx_daily/2025/20251230.jsonl", "market/krx_daily/2026/20260102.jsonl",
                     "fundamentals/dart_quarterly/2026_11013.jsonl"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not be read")

    def denied(*args, **kwargs):
        raise AssertionError("dry-run must only inspect coverage")

    monkeypatch.setattr(runner, "run_diagnostic", denied)
    monkeypatch.setattr(runner, "write_diagnostic", denied)
    monkeypatch.setattr(runner, "_last_dates", denied)
    monkeypatch.setattr(hc002.hc001.d001, "_load_prices", denied)
    monkeypatch.setattr(runner.krx_market, "load_prices", denied)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plan = runner.dry_run(data_dir=tmp_path)
    assert plan["planned_months"] == plan["guarded_months"] == 103
    assert plan["price_files"] == 1 and plan["last_price_file"] == "2025-12-30"
    assert plan["feature_window_covered_months"] == []
    assert plan["draws"] == 200 and plan["seed"] == 0 and plan["used_for_judgement"] is False
    assert runner.main(["--data-dir", str(tmp_path)]) == 0
    assert runner.main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
    assert "사후 진단, 판정에 쓰지 않음" in capsys.readouterr().out
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_identity_stream_guards_2026_before_opening_it(tmp_path):
    path = tmp_path / "20260102.jsonl"
    path.write_text("must not be opened")
    with pytest.raises(ValueError, match="holdout"):
        runner._last_dates([(pd.Timestamp("2026-01-02"), path)], tmp_path)
