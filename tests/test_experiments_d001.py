from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiments import common, d001
from src.ingestion.krx_market import load_prices
from src.strategies import PointInTimeData
from src.strategies.factors import MomentumLowVolStrategy


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.name", "Offline Test")
    git("config", "user.email", "offline@example.invalid")
    relative = Path("research/experiments") / d001.EXPERIMENT_ID / "preregistration.md"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text((experiment_registry.PROJECT_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    git("add", "--", relative.as_posix())
    git("-c", "commit.gpgsign=false", "commit", "-qm", "Register synthetic experiment")
    return tmp_path, common.load_preregistration(d001.EXPERIMENT_ID, repo_root=tmp_path)


@pytest.fixture(scope="module")
def market():
    rng = np.random.default_rng(81)
    dates = pd.bdate_range("2015-01-02", "2025-12-31")
    codes = [f"{i * 10:06d}" for i in range(20)]
    shape = len(dates), len(codes)
    returns = rng.normal(np.linspace(-0.0001, 0.0007, len(codes)), np.linspace(0.004, 0.014, len(codes)), size=shape)
    close = 10000 * np.cumprod(1 + returns, axis=0)
    previous = np.vstack([np.full(len(codes), 10000), close[:-1]])
    opening = previous * (1 + rng.normal(0, 0.001, size=shape))
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    return pd.DataFrame({"open": opening.ravel(), "close": close.ravel(), "base_price": previous.ravel(),
                         "high": np.maximum(opening, close).ravel() * 1.001,
                         "low": np.minimum(opening, close).ravel() * 0.999,
                         "ret_1d": returns.ravel(), "volume": 100000.0,
                         "stock_name": np.tile([f"기업{i}" for i in range(len(codes))], len(dates)),
                         "market": np.tile(["KOSPI", "KOSDAQ"] * 10, len(dates)),
                         "trading_value": np.tile(np.resize([2e8, 2e9, 2e10], len(codes)), len(dates)),
                         "market_cap": 1e11, "calendar_status": "verified",
                         "bar_at": np.repeat(dates.strftime("%Y-%m-%dT15:30:00+09:00"), len(codes))}, index=index)


def universe_for(prices, dates):
    codes = prices.index.get_level_values("stock_code").unique()
    return pd.DataFrame({"trade_date": np.repeat(dates, len(codes)), "stock_code": list(codes) * len(dates),
                         "avg_trading_value_20d": 2e9})


def test_monthly_t_uses_every_month_with_a_twenty_session_horizon(market):
    prices = market.loc[:"2019-02-28"]
    dates = common.month_end_sessions(prices, "20160101", "20181231")
    universe = universe_for(prices, dates)
    observations = d001._observations(prices, universe)
    scores = universe[["trade_date", "stock_code"]].copy()
    scores["score"] = np.random.default_rng(72).normal(size=len(scores))
    frame = scores.merge(observations, on=["trade_date", "stock_code"])
    result = d001._evaluate(frame, dates, observations, np.random.default_rng(0))
    monthly = np.array([row["ic"] for row in result["ic"]["daily"]])
    assert len(monthly) == result["ic"]["count"] == 36
    expected = monthly.mean() / (monthly.std(ddof=1) / np.sqrt(36))
    assert result["ic"]["t_stat"] == pytest.approx(expected)
    assert result["ic"]["non_overlapping"]["count"] == 36
    daily_evaluator = signal_eval.evaluate_signal(scores, prices, horizons=(20,), n_controls=1)["horizons"]["20"]
    assert daily_evaluator["ic"]["non_overlapping"]["count"] == 2
    assert result["ic"]["t_stat"] != pytest.approx(daily_evaluator["ic"]["t_stat"])


def test_monthly_statistics_skip_only_undefined_values():
    series = pd.Series([0.1, np.nan, 0.2, 0.3], index=pd.date_range("2024-01-31", periods=4, freq="ME"))
    result = d001.monthly_ic_statistics(series)
    assert result["count"] == 3
    assert result["t_stat"] == pytest.approx(0.2 / (0.1 / np.sqrt(3)))


def test_rising_market_positive_absolute_net_but_negative_universe_excess_fails(repo):
    root, fields = repo
    dates = pd.date_range("2023-01-31", periods=36, freq="ME")
    rows = []
    for number, day in enumerate(dates):
        gross = np.linspace(0.03, 0.05, 10)
        if number % 2:
            gross[[0, 1]] = gross[[1, 0]]
        for stock, value in enumerate(np.r_[gross, np.repeat(0.2, 10)]):
            rows.append({"trade_date": day, "stock_code": f"{stock * 10:06d}", "gross": value,
                         "score": stock, "avg_trading_value_20d": 2e9, "bucket": 1,
                         "cost_1.0": 0.002, "cost_1.5": 0.003, "cost_2.0": 0.004,
                         "reason": None, **dict.fromkeys(signal_eval._RETURN_FLAGS, False)})
    observations = pd.DataFrame(rows)
    # The investable universe also includes stocks without a valid factor score.
    frame = observations.loc[observations.score < 10].copy()
    result = d001._evaluate(frame, dates, observations, np.random.default_rng(0))
    design = d001.monthly_ic_statistics(d001._monthly_ic(frame.loc[frame.trade_date.dt.year < 2025]))
    values = d001._judgement_inputs(result, design, fields, 4)
    universe_gross = observations.groupby("trade_date").gross.mean()
    top_gross = frame.loc[frame.score >= 8].groupby("trade_date").gross.mean()
    for multiplier, metric, absolute in ((1.0, d001.NET_METRIC, "net"),
                                          (1.5, d001.NET_AT_COST_1_5_METRIC, "net_at_cost_1_5")):
        assert values[absolute] > 0
        assert values[metric] == pytest.approx((top_gross - 0.002 * multiplier - universe_gross).mean())
        assert values[metric] < 0
    verdict, reasons = common.judge(fields, values, net_metric=d001.NET_METRIC,
                                    net_at_cost_1_5_metric=d001.NET_AT_COST_1_5_METRIC, repo_root=root)
    assert verdict == "fail"
    assert len(reasons) == 2
    assert all("top_net_excess" in reason for reason in reasons)


def test_selection_uses_design_only_even_if_validation_reverses_the_ranking(repo):
    _, fields = repo
    design = pd.date_range("2024-01-31", periods=12, freq="ME")
    validation = pd.date_range("2025-01-31", periods=12, freq="ME")
    dates = design.append(validation)
    values = {"combined": pd.Series(np.r_[np.linspace(-0.1, 0.5, 12), np.repeat(0.9, 12)], index=dates),
              "momentum": pd.Series(np.r_[np.linspace(0.1, 0.2, 12), np.repeat(-0.9, 12)], index=dates),
              "lowvol": pd.Series(np.r_[np.linspace(-0.2, 0.2, 12), np.repeat(0.99, 12)], index=dates)}
    selected, statistics = d001.select_variant(values, fields)
    assert selected == "momentum"
    assert max(values, key=lambda name: d001.monthly_ic_statistics(values[name])["t_stat"]) != selected
    changed = {name: value.copy() for name, value in values.items()}
    for name, value in changed.items():
        value.loc[validation] = 0.99 if name == "momentum" else -0.99
    assert d001.select_variant(changed, fields) == (selected, statistics)
    assert all(result["count"] == 12 for result in statistics.values())


def test_undefined_design_statistics_do_not_silently_select_a_variant(repo):
    _, fields = repo
    values = {name: pd.Series(dtype=float, index=pd.DatetimeIndex([])) for name in d001.VARIANTS}
    selected, statistics = d001.select_variant(values, fields)
    assert selected is None
    assert all(value["t_stat"] is None for value in statistics.values())


def test_scores_match_factor_strategy_and_future_data_cannot_change_them(repo, market):
    root, fields = repo
    prices = market.loc["2023-01-01":].copy()
    day = pd.Timestamp("2024-06-28")
    scores, _, _, _ = d001.build_scores(prices, fields, repo_root=root)
    actual = scores["combined"].loc[scores["combined"].trade_date == day, ["stock_code", "score"]].reset_index(drop=True)
    expected = MomentumLowVolStrategy().score(day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=17),
                                             PointInTimeData(prices, pd.DataFrame()))
    pd.testing.assert_frame_equal(actual, expected)
    future = prices.index.get_level_values("trade_date") > day
    prices.loc[future, "ret_1d"] = 0.5
    prices.loc[future, "trading_value"] = 1e15
    changed, _, _, _ = d001.build_scores(prices, fields, repo_root=root)
    for name in d001.VARIANTS:
        pd.testing.assert_frame_equal(scores[name].loc[scores[name].trade_date <= day].reset_index(drop=True),
                                      changed[name].loc[changed[name].trade_date <= day].reset_index(drop=True))


def test_unverified_calendar_and_incomplete_horizon_are_reported(repo, market):
    root, fields = repo
    prices = market.loc["2024-01-01":].copy()
    prices.loc[(pd.Timestamp("2025-11-28"), slice(None)), "calendar_status"] = "unverified_special_session"
    scores, _, _, skipped = d001.build_scores(prices, fields, repo_root=root)
    assert {"month": "2025-11", "reason": "unverified_calendar"} in skipped
    assert {"month": "2025-12", "reason": "incomplete_20_session_horizon"} in skipped
    assert all(not frame.trade_date.isin(pd.to_datetime(["2025-11-28", "2025-12-31"])).any() for frame in scores.values())


def test_holdout_guard_aborts_before_scoring_or_loading(repo, market, monkeypatch):
    root, _ = repo
    future = market.iloc[:1].copy()
    future.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-01"), "000000")], names=market.index.names)
    def forbidden(*args, **kwargs):
        raise AssertionError("scoring started after guard violation")
    monkeypatch.setattr(d001, "build_scores", forbidden)
    with pytest.raises(ValueError, match="holdout"):
        d001.run_experiment(prices=pd.concat([market, future]), repo_root=root)
    with pytest.raises(ValueError, match="holdout"):
        d001._load_prices(root / "absent", "20150102", "20260101", repo_root=root)
    assert experiment_registry.trial_count(d001.EXPERIMENT_ID, repo_root=root) == 0
    assert not (root / "research/experiments/holdout_ledger.jsonl").exists()


def test_streamed_reader_matches_latest_episode_loader(repo, market):
    root, _ = repo
    base = root / "data"
    directory = base / "market/krx_daily/2015"
    directory.mkdir(parents=True)
    days = market.index.get_level_values("trade_date").unique()[:2]
    for day in days:
        frame = market.xs(day).iloc[:2].reset_index()
        rows = json.loads(frame.to_json(orient="records"))
        for row in rows:
            row["trade_date"] = day.date().isoformat()
            row["change_rate_pct"] = row.pop("ret_1d") * 100
        revised = dict(rows[0], close=12345.0, change_rate_pct=1.25)
        rows.append(revised)
        (directory / f"{day:%Y%m%d}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    actual = d001._load_prices(directory.parent, "20150102", "20150105", repo_root=root)
    expected = load_prices(None, "20150102", "20150105", base)
    pd.testing.assert_frame_equal(actual[list(d001.NUMERIC_COLUMNS)], expected[list(d001.NUMERIC_COLUMNS)], check_dtype=False)
    assert actual.loc[(days[0], "000000"), "ret_1d"] == 0.0125


@pytest.mark.parametrize("policy", ["adjusted", "raw_fallback", "limit_up", "stale", "delisted"])
def test_runner_reuses_twenty_session_execution_policies(market, policy):
    prices = market.loc["2024-01-01":"2024-06-28"].copy()
    dates = prices.index.get_level_values("trade_date").unique()
    day = pd.Timestamp("2024-01-31")
    position = dates.get_loc(day)
    entry, exit_day = dates[position + 1], dates[position + 20]
    code = "000000"
    if policy == "raw_fallback":
        prices.loc[(dates[position + 2], code), "ret_1d"] = np.nan
    elif policy == "limit_up":
        prices.loc[(entry, code), "open"] = prices.loc[(entry, code), "base_price"] * 1.30
    elif policy == "stale":
        prices = prices.drop((exit_day, code))
    elif policy == "delisted":
        prices = prices.drop([(date, code) for date in dates[position + 2:]])
    universe = universe_for(prices, pd.DatetimeIndex([day]))
    actual = d001._observations(prices, universe).set_index("stock_code")
    row = actual.loc[code]
    if policy == "limit_up":
        assert row.reason == "limit_up_entry" and pd.isna(row.gross)
    elif policy == "delisted":
        assert row.reason == "no_later_price" and pd.isna(row.gross)
    else:
        actual_exit = dates[position + 19] if policy == "stale" else exit_day
        if policy == "raw_fallback":
            expected = prices.loc[(actual_exit, code), "close"] / prices.loc[(entry, code), "open"] - 1
            assert row.unadjusted_fallback
        else:
            chain = prices.loc[(slice(dates[position + 2], actual_exit), code), "ret_1d"]
            expected = prices.loc[(entry, code), "close"] / prices.loc[(entry, code), "open"] * (1 + chain).prod() - 1
            assert not row.unadjusted_fallback
        assert row.gross == pytest.approx(expected)
        assert bool(row.stale_exit) == (policy == "stale")


def test_costs_use_full_entry_date_for_midyear_tax_change(market):
    prices = market.loc["2019-05-01":"2019-07-31"]
    day = pd.Timestamp("2019-05-31")
    observations = d001._observations(prices, universe_for(prices, pd.DatetimeIndex([day])))
    row = observations.iloc[0]
    expected = cost_model.round_trip_cost(row.entry_open, row.entry_market, pd.Timestamp("2019-06-03").date(), 2e9)
    assert row["cost_1.0"] == pytest.approx(expected)
    wrong_year_only = cost_model.round_trip_cost(row.entry_open, row.entry_market, pd.Timestamp("2019-01-01").date(), 2e9)
    assert wrong_year_only - row["cost_1.0"] == pytest.approx(0.0005)


def test_missing_all_features_records_insufficient_without_a_default_variant(repo, market):
    root, _ = repo
    prices = market.loc["2016-01-01":"2016-05-31"].copy()
    prices["ret_1d"] = np.nan
    result = d001.run_experiment(prices=prices, repo_root=root)
    assert result["selected_variant"] is None
    assert result["verdict"] == "insufficient"
    assert all(value["ic"]["count"] == 0 for value in result["variants"].values())
    assert all(len(value["skipped_months"]) == value["skipped_month_count"] == 120
               for value in result["variants"].values())
    assert experiment_registry.trial_count(d001.EXPERIMENT_ID, repo_root=root) == 4


def test_run_loads_once_appends_all_variants_and_final_and_writes_results(repo, market, monkeypatch):
    root, _ = repo
    calls = []
    def load(*args, **kwargs):
        calls.append((args, kwargs))
        return market
    monkeypatch.setattr(d001, "_load_prices", load)
    experiment_registry.record_trial(d001.EXPERIMENT_ID, "previous", {"net": -0.1}, "fail", repo_root=root)
    registry = root / experiment_registry.REGISTRY_PATH
    previous = registry.read_bytes()
    result = d001.run_experiment(repo_root=root)
    assert len(calls) == 1
    assert registry.read_bytes().startswith(previous)
    with registry.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["variant"] for row in rows] == ["previous", *d001.VARIANTS, "final"]
    assert rows[-1]["verdict"] == result["verdict"]
    assert json.loads(rows[-1]["metrics_json"])["selected_variant"] == result["selected_variant"]
    for value in result["variants"].values():
        assert value["ic"]["count"] == 119
        assert value["design_ic"]["count"] == 108
        assert value["random_control"]["count"] == 200
        assert value["skipped_month_count"] == 1
        assert value["skipped_months"] == [{"month": "2025-12", "reason": "incomplete_20_session_horizon"}]
        assert set(value["cost_sensitivity"]) == {"1.0", "1.5", "2.0"}
        assert set(value["liquidity_buckets"]) == {"0", "1", "2", "3"}
        assert value["capacity"]["positions"] > 0
        assert sum(bucket["observations"] for bucket in value["liquidity_buckets"].values()) == value["observations"]
        assert value["judgement_inputs"][d001.NET_METRIC] == value["cost_sensitivity"]["1.0"]["top_net_excess"]
        assert value["judgement_inputs"][d001.NET_AT_COST_1_5_METRIC] == value["cost_sensitivity"]["1.5"]["top_net_excess"]
    assert result["performance"]["runtime_seconds"] > 0
    assert result["performance"]["peak_memory_mib"] > 0
    directory = root / "research/experiments" / d001.EXPERIMENT_ID / "results"
    paths = list(directory.glob("*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text(encoding="utf-8")) == result
    summary = paths[0].with_suffix(".md").read_text(encoding="utf-8")
    assert "실행 시간" in summary
    interpretations = result["interpretations"]
    assert interpretations["note"] == "정의는 실제 결과를 보기 전(2026-10-05)에 확정"
    assert "## interpretations" in summary and interpretations["note"] in summary
    fields = common.load_preregistration(d001.EXPERIMENT_ID, repo_root=root)
    assert set(interpretations["criteria"]) == set(fields["pass_criteria"]["design_validation"]) | {"validation_year_same_sign"}
    for criterion, interpretation in interpretations["criteria"].items():
        assert criterion in summary
        assert interpretation["metric"] in summary
        assert interpretation["definition"] in summary
    for multiplier, metric, criterion in (
        ("1.0", d001.NET_METRIC, "net_performance_gt"),
        ("1.5", d001.NET_AT_COST_1_5_METRIC, "net_performance_at_cost_multiplier_1_5_gt"),
    ):
        assert f"judgement_inputs.{metric}" in interpretations["criteria"][criterion]["metric"]
        assert f"cost_sensitivity['{multiplier}'].top_net_excess" in interpretations["criteria"][criterion]["metric"]
    assert "절대 순수익 (보조)" in summary
    assert not (root / "research/experiments/holdout_ledger.jsonl").exists()


def test_dry_run_reads_only_file_coverage_and_never_executes(repo, tmp_path, monkeypatch, capsys):
    root, _ = repo
    directory = tmp_path / "prices/2025"
    directory.mkdir(parents=True)
    (directory / "20251231.jsonl").write_text("price contents must not be parsed\n", encoding="utf-8")
    holdout_directory = tmp_path / "prices/2026"
    holdout_directory.mkdir()
    (holdout_directory / "20260101.jsonl").write_text("holdout must not be read\n", encoding="utf-8")
    original = d001.dry_run
    monkeypatch.setattr(d001, "dry_run", lambda **kwargs: original(repo_root=root, **kwargs))
    def forbidden(**kwargs):
        raise AssertionError("dry-run executed an experiment")
    monkeypatch.setattr(d001, "run_experiment", forbidden)
    assert d001.main(["--dry-run", "--data-dir", str(directory.parent)]) == 0
    output = capsys.readouterr().out
    assert "2016-01..2025-12 (120; design 108; validation 12)" in output
    assert "Price files: 1" in output
    assert "Incomplete 20-session horizon months: 2025-12" in output
    assert experiment_registry.trial_count(d001.EXPERIMENT_ID, repo_root=root) == 0
    assert not (root / "research/experiments" / d001.EXPERIMENT_ID / "results").exists()
