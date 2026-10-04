"""D001 monthly momentum/low-volatility runner; dry-run is the CLI default.

Prices are streamed once using KRX's latest-per-stock daily episode rule. Only
needed columns are retained. Shared signal_eval holding policies are evaluated
once, and monthly statistics use stride 1 rather than its daily horizon stride.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import capacity, cost_model, experiment_registry, holdout, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common
from src.strategies.data import KST, PointInTimeData
from src.strategies.factors import combined_score, low_volatility, momentum_12_1


EXPERIMENT_ID = "D001_momentum_lowvol"
VARIANTS = ("combined", "momentum", "lowvol")
PRICE_START = "20150102"
MIN_STOCKS = 10
HORIZON = 20
N_CONTROLS = 200
NUMERIC_COLUMNS = ("open", "close", "volume", "trading_value", "base_price", "ret_1d")
TEXT_COLUMNS = ("stock_name", "market", "calendar_status", "bar_at")
NET_METRIC = "top_net_excess"
NET_AT_COST_1_5_METRIC = "top_net_excess_at_cost_1_5"
INTERPRETATIONS = {
    "note": "정의는 실제 결과를 보기 전(2026-10-05)에 확정",
    "criteria": {
        "net_performance_gt": {
            "metric": f"judgement_inputs.{NET_METRIC} = cost_sensitivity['1.0'].top_net_excess",
            "definition": "월별 (최상위 5분위 순수익 - 같은 월 유니버스 동일가중 총수익)의 평균. 절대 순수익은 보조 수치입니다.",
        },
        "net_performance_at_cost_multiplier_1_5_gt": {
            "metric": f"judgement_inputs.{NET_AT_COST_1_5_METRIC} = cost_sensitivity['1.5'].top_net_excess",
            "definition": "비용 1.5배의 월별 최상위 5분위 순수익에서 같은 월 유니버스 동일가중 총수익을 뺀 평균. 기존 비용 모델대로 세금에는 배수를 적용하지 않습니다.",
        },
        "core_t_stat_gt": {
            "metric": "judgement_inputs.t_stat = ic.t_stat",
            "definition": "설계+검증 구간의 모든 유효 월별 Spearman IC 평균 / (표본 표준편차 / 월 수의 제곱근).",
        },
        "core_t_stat_gt_when_trials_gt_20": {
            "metric": "judgement_inputs.t_stat = ic.t_stat",
            "definition": "이번 실행의 예정 대장 행을 포함해 시도 수가 20을 초과하면 같은 월별 IC t에 강화된 기준을 적용합니다.",
        },
        "random_control_top_percent": {
            "metric": "judgement_inputs.random_control_share = random_control.share_of_controls",
            "definition": "200개 무작위 점수 대조군 중 IC 평균이 실제 IC 평균 이상인 비율. 상한은 사전등록 백분율과 5% 중 작은 값입니다.",
        },
        "minimum_observations": {
            "metric": "judgement_inputs.observations = ic.count",
            "definition": "유효 월별 IC 수를 minimum_observations.rebalances와 비교합니다. 월별 관측 규칙을 사용하며 종목·이벤트 행 수가 아닙니다.",
        },
        "validation_year_same_sign": {
            "metric": "judgement_inputs.validation_year_same_sign = sign(ic.by_year['2025'].mean) == sign(design_ic.mean)",
            "definition": "검증 연도(2025)의 월별 IC 평균과 설계 구간 월별 IC 평균의 부호가 같아야 합니다.",
        },
    },
}


def _price_files(data_dir, start, end, *, repo_root=PROJECT_ROOT):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    holdout.guard_period(start.date(), end.date(), repo_root=repo_root)
    directory = Path(data_dir)
    if not directory.is_dir():
        raise ValueError(f"price directory does not exist: {directory}")
    files = []
    for path in directory.glob("*/*.jsonl"):
        if len(path.stem) == 8 and path.stem.isdigit():
            day = pd.Timestamp(path.stem)
            if start <= day <= end and path.stat().st_size:
                files.append((day, path))
    return sorted(files)


def _load_prices(data_dir, start, end, *, repo_root=PROJECT_ROOT):
    """Match load_prices' latest-episode and numeric rules without its full row list."""
    frames = []
    for day, path in _price_files(data_dir, start, end, repo_root=repo_root):
        latest = {}
        expected_day = day.date().isoformat()
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["trade_date"] != expected_day:
                    raise ValueError(f"price row does not match file date: {path.name}:{number}")
                code = row["stock_code"]
                if not isinstance(code, str) or not code:
                    raise ValueError(f"missing stock_code: {path.name}:{number}")
                code = sys.intern(code)
                latest[code] = {name: row.get(name) for name in (*NUMERIC_COLUMNS, *TEXT_COLUMNS)}
                # Millions of bars repeat these few thousand labels and dates.
                for name in TEXT_COLUMNS:
                    value = latest[code][name]
                    if isinstance(value, str):
                        latest[code][name] = sys.intern(value)
                latest[code]["ret_1d"] = None if row.get("change_rate_pct") is None else float(row["change_rate_pct"]) / 100
        if not latest:
            continue
        frame = pd.DataFrame.from_dict(latest, orient="index").rename_axis("stock_code")
        for name in NUMERIC_COLUMNS:
            frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
        frame["trade_date"] = day
        frames.append(frame.reset_index().set_index(["trade_date", "stock_code"]))
    if not frames:
        raise ValueError("no price observations in the guarded D001 span")
    prices = pd.concat(frames).sort_index()
    for name in TEXT_COLUMNS:
        prices[name] = prices[name].astype("category")
    return prices


def _period(fields):
    periods = fields["data_periods"]
    return pd.Timestamp(periods["design"]["from"]), pd.Timestamp(periods["validation"]["to"])


def dry_run(*, data_dir=PROJECT_ROOT / "data/market/krx_daily", repo_root=PROJECT_ROOT) -> dict:
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    start, end = _period(fields)
    files = _price_files(data_dir, PRICE_START, end, repo_root=repo_root)
    days = pd.DatetimeIndex([day for day, _ in files])
    months = pd.period_range(start, end, freq="M")
    present = set(days.to_period("M"))
    last = pd.Series(days, index=days.to_period("M")).groupby(level=0).max()
    incomplete = [str(month) for month in months if month in last.index
                  and days.get_loc(last.loc[month]) + HORIZON >= len(days)]
    return {"experiment_id": EXPERIMENT_ID, "decision_start": str(months[0]), "decision_end": str(months[-1]),
            "months": len(months), "design_months": len(pd.period_range(start, pd.Timestamp(fields["data_periods"]["design"]["to"]), freq="M")),
            "variants": list(VARIANTS), "data_dir": str(Path(data_dir)),
            "read_start": pd.Timestamp(PRICE_START).date().isoformat(), "read_end": end.date().isoformat(),
            "files": len(files), "first_file": days[0].date().isoformat() if len(days) else None,
            "last_file": days[-1].date().isoformat() if len(days) else None,
            "files_by_year": {str(year): int((days.year == year).sum()) for year in range(2015, end.year + 1)},
            "covered_months": sum(month in present for month in months),
            "missing_months": [str(month) for month in months if month not in present],
            "incomplete_horizon_months": incomplete}


def build_scores(prices, fields, *, repo_root=PROJECT_ROOT):
    """Vectorized stock features on 253-session PointInTimeData windows."""
    common.guard_prices(prices, repo_root=repo_root)
    start, end = _period(fields)
    months = pd.period_range(start, end, freq="M")
    sessions = common._sessions(prices)
    dates = common.month_end_sessions(prices, start, end)
    month_dates = dict(zip(dates.to_period("M"), dates))
    raw_days = prices.index.get_level_values("trade_date")
    score_rows = {name: [] for name in VARIANTS}
    universe_rows, skipped = [], []
    for month in months:
        day = month_dates.get(month)
        if day is None:
            skipped.append({"month": str(month), "reason": "no_price_sessions"})
            continue
        position = sessions.get_loc(day)
        if position + HORIZON >= len(sessions):
            skipped.append({"month": str(month), "reason": "incomplete_20_session_horizon"})
            continue
        left = raw_days.searchsorted(sessions[max(0, position - 252)], side="left")
        right = raw_days.searchsorted(day, side="right")
        columns = ["close", "ret_1d", "trading_value", "stock_name", "market", "calendar_status"]
        if "bar_at" in prices:
            columns.append("bar_at")
        data = PointInTimeData(prices.iloc[left:right][columns], pd.DataFrame())
        history = data.prices_as_of(day.tz_localize(KST) + pd.Timedelta(hours=17), 253)
        if history.empty or day not in history.index.get_level_values("trade_date"):
            skipped.append({"month": str(month), "reason": "no_available_decision_prices"})
            continue
        current = history.xs(day, level="trade_date")
        if not current.calendar_status.eq("verified").any():
            skipped.append({"month": str(month), "reason": "unverified_calendar"})
            continue
        universe = common.universe_filter(history, day, repo_root=repo_root)
        universe_rows.append(universe[["avg_trading_value_20d"]].assign(trade_date=day).reset_index())
        returns = history.ret_1d.where(history.calendar_status.eq("verified")).unstack("stock_code")
        features = pd.DataFrame({"momentum": momentum_12_1(returns), "lowvol": low_volatility(returns)}).reindex(universe.index)
        complete = features.loc[np.isfinite(features).all(axis=1)]
        values = {"combined": combined_score(complete), "momentum": features.momentum, "lowvol": features.lowvol}
        for name, scores in values.items():
            valid = scores.loc[np.isfinite(scores)]
            score_rows[name].append(valid.rename("score").rename_axis("stock_code").reset_index().assign(trade_date=day))
    universe = pd.concat(universe_rows, ignore_index=True) if universe_rows else pd.DataFrame(
        columns=["stock_code", "avg_trading_value_20d", "trade_date"])
    scores = {name: pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["stock_code", "score", "trade_date"]) for name, rows in score_rows.items()}
    return scores, universe, dates, skipped


def monthly_ic_statistics(monthly_ic: pd.Series) -> dict:
    """All finite monthly ICs enter the t statistic; no 20-month subsampling."""
    return signal_eval._summary(monthly_ic.to_numpy(dtype=float))


def _monthly_ic(frame):
    rows = {}
    for day, group in frame.groupby("trade_date", sort=True):
        if len(group) < MIN_STOCKS:
            continue
        score, returns = signal_eval._rank_vector(group.score), signal_eval._rank_vector(group.gross)
        rows[day] = float(np.dot(score, returns)) if score is not None and returns is not None else np.nan
    return pd.Series(list(rows.values()), index=pd.DatetimeIndex(list(rows)), dtype=float)


def select_variant(monthly_ics, fields):
    period = fields["data_periods"]["design"]
    start, end = pd.Timestamp(period["from"]), pd.Timestamp(period["to"])
    statistics = {name: monthly_ic_statistics(values.loc[(values.index >= start) & (values.index <= end)])
                  for name, values in monthly_ics.items()}
    valid = [name for name in VARIANTS if statistics[name]["t_stat"] is not None]
    # Preregistered table order resolves an exact design-t tie without validation.
    selected = max(valid, key=lambda name: statistics[name]["t_stat"]) if valid else None
    return selected, statistics


def _observations(prices, universe):
    index = pd.MultiIndex.from_frame(universe[["trade_date", "stock_code"]])
    if universe.empty:
        return pd.DataFrame(columns=["trade_date", "stock_code", "gross", "reason", "avg_trading_value_20d",
                                     "bucket", "cost_1.0", "cost_1.5", "cost_2.0", *signal_eval._RETURN_FLAGS])
    holding = signal_eval._holding_data(prices, "exclude")
    window = signal_eval._holding_window(holding, HORIZON, "last_close")
    observations = signal_eval._forward_observations(holding, window, index)
    entries = holding["sessions"].get_indexer(index.get_level_values("trade_date")) + 1
    stocks = holding["codes"].get_indexer(index.get_level_values("stock_code"))
    observations["entry_open"] = holding["open"][entries, stocks]
    observations["entry_market"] = holding["market"][entries, stocks]
    observations["entry_date"] = holding["sessions"].to_numpy()[entries]
    del holding, window
    observations["avg_trading_value_20d"] = universe.avg_trading_value_20d.to_numpy()
    observations["bucket"] = np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, observations.avg_trading_value_20d, side="right")
    valid = observations.loc[np.isfinite(observations.gross)]
    for multiplier in (1.0, 1.5, 2.0):
        observations[f"cost_{multiplier}"] = np.nan
        observations.loc[valid.index, f"cost_{multiplier}"] = cost_model.round_trip_cost_vectorized(
            valid.entry_open.to_numpy(), valid.entry_market.to_numpy(), valid.entry_date.to_numpy(),
            valid.avg_trading_value_20d.to_numpy(), multiplier=multiplier)
    return observations.reset_index()


def _metrics(frame, dates, benchmark, rng):
    """Reuse signal metrics with MONTHLY sampling stride 1, cached 20-session returns."""
    result = signal_eval._metrics(frame, dates, 1, 5, MIN_STOCKS, N_CONTROLS, rng)
    portfolios = result["quantiles"]["daily"]
    for row in portfolios:
        row["universe_gross"] = float(benchmark.loc[pd.Timestamp(row["trade_date"])])
        row["top_net_excess"] = row["quantiles"]["5"]["net"] - row["universe_gross"]
    result["quantiles"]["top_net_excess"] = signal_eval._summary([row["top_net_excess"] for row in portfolios])["mean"]
    measured_dates = {pd.Timestamp(row["trade_date"]) for row in portfolios}
    ordered = frame.loc[frame.trade_date.isin(measured_dates)].sort_values(["trade_date", "score", "stock_code"], kind="stable").copy()
    groups = ordered.groupby("trade_date")
    ordered["quantile"] = groups.cumcount() * 5 // groups.stock_code.transform("size") + 1
    top = ordered.loc[ordered["quantile"] == 5]
    result["cost_sensitivity"] = {}
    for multiplier in (1.0, 1.5, 2.0):
        net = (top.gross - top[f"cost_{multiplier}"]).groupby(top.trade_date).mean()
        excess = net - benchmark.reindex(net.index)
        result["cost_sensitivity"][str(multiplier)] = {"top_net": signal_eval._summary(net)["mean"],
                                                       "top_net_excess": signal_eval._summary(excess)["mean"]}
    result["capacity"] = capacity.strategy_capacity(top[
        ["trade_date", "stock_code", "avg_trading_value_20d"]]) if not top.empty else None
    # Also give the strict equal-weight 1% limit for each month's actual selection.
    result["capacity_by_month"] = {day.date().isoformat(): len(group) * float(group.avg_trading_value_20d.min()) * 0.01
                                   for day, group in top.groupby("trade_date")}
    result["skipped_months"] = [day.date().isoformat() for day in dates if day not in measured_dates]
    return result


def _evaluate(frame, dates, observations, rng):
    benchmark = observations.loc[np.isfinite(observations.gross)].groupby("trade_date").gross.mean()
    valid = frame.loc[np.isfinite(frame.gross)].copy()
    valid["net"] = valid.gross - valid["cost_1.0"]
    result = _metrics(valid, dates, benchmark, rng)
    result.update(signal_eval._return_counts(frame))
    result["delisting_exclusions"] = result["no_later_price"]
    result["stale_exits"] = signal_eval._stale_records(frame)
    result["liquidity_buckets"] = {}
    lower = 0.0
    for number, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
        bucket_observations = observations.loc[(observations.bucket == number) & np.isfinite(observations.gross)]
        bucket_benchmark = bucket_observations.groupby("trade_date").gross.mean()
        bucket = _metrics(valid.loc[valid.bucket == number], dates, bucket_benchmark, rng)
        bucket.update(lower_inclusive=lower, upper_exclusive=float(upper) if np.isfinite(upper) else None)
        result["liquidity_buckets"][str(number)] = bucket
        lower = float(upper)
    return result


def _judgement_inputs(result, design, fields, trial_count):
    year = pd.Timestamp(fields["data_periods"]["validation"]["from"]).year
    validation = result["ic"]["by_year"].get(str(year), {}).get("mean")
    same_sign = bool(np.sign(validation) == np.sign(design["mean"])) if validation is not None and design["mean"] is not None else None
    return {"observations": result["ic"]["count"], "t_stat": result["ic"]["t_stat"],
            "random_control_share": result["random_control"]["share_of_controls"],
            "net": result["cost_sensitivity"]["1.0"]["top_net"],
            "net_at_cost_1_5": result["cost_sensitivity"]["1.5"]["top_net"],
            NET_METRIC: result["cost_sensitivity"]["1.0"]["top_net_excess"],
            NET_AT_COST_1_5_METRIC: result["cost_sensitivity"]["1.5"]["top_net_excess"],
            "validation_year_same_sign": same_sign, "trial_count_after_run": trial_count}


def run_experiment(*, data_dir=PROJECT_ROOT / "data/market/krx_daily", repo_root=PROJECT_ROOT, prices=None) -> dict:
    """Execute and append all three diagnostics plus the selected final verdict.

    prices is an offline test input; production loads the guarded local span once.
    Validation diagnostics for unselected variants are reported after selection
    and cannot change it. They are not additional selected hypotheses.
    """
    started = time.perf_counter()
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    if fields["variants_planned"] != len(VARIANTS) or fields["uses_holdout"]:
        raise ValueError("D001 requires its three preregistered variants and no holdout")
    _, end = _period(fields)
    if prices is None:
        prices = _load_prices(data_dir, PRICE_START, end, repo_root=repo_root)
    common.guard_prices(prices, repo_root=repo_root)
    if not prices.index.is_monotonic_increasing:
        prices = prices.sort_index()
    if prices.index.has_duplicates:
        raise ValueError("price keys must be unique")
    if prices.empty:
        raise ValueError("no price observations in the guarded D001 span")
    scores, universe, dates, skipped = build_scores(prices, fields, repo_root=repo_root)
    observations = _observations(prices, universe)
    frames = {name: values.merge(observations, on=["trade_date", "stock_code"], how="left") for name, values in scores.items()}
    period = fields["data_periods"]["design"]
    monthly_ics = {name: _monthly_ic(frame.loc[np.isfinite(frame.gross) & frame.trade_date.between(
        pd.Timestamp(period["from"]), pd.Timestamp(period["to"]))]) for name, frame in frames.items()}
    selected, design = select_variant(monthly_ics, fields)
    total_trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS) + 1
    months = pd.period_range(*_period(fields), freq="M")
    common_reasons = {row["month"]: row["reason"] for row in skipped}
    variants = {}
    for number, name in enumerate(VARIANTS):
        result = _evaluate(frames[name], dates, observations, np.random.default_rng(number))
        ic_by_month = {row["trade_date"][:7]: row["ic"] for row in result["ic"]["daily"]}
        result["skipped_months"] = []
        for month in months:
            key = str(month)
            if ic_by_month.get(key) is None:
                reason = "undefined_monthly_ic" if key in ic_by_month else "insufficient_score_or_return_observations"
                result["skipped_months"].append({"month": key, "reason": common_reasons.get(key, reason)})
        result["skipped_month_count"] = len(result["skipped_months"])
        inputs = _judgement_inputs(result, design[name], fields, total_trials)
        verdict, reasons = common.judge(fields, inputs, net_metric=NET_METRIC,
                                        net_at_cost_1_5_metric=NET_AT_COST_1_5_METRIC, repo_root=repo_root)
        result.update(design_ic=design[name], verdict=verdict, reasons=reasons, judgement_inputs=inputs)
        variants[name] = result
        experiment_registry.record_trial(EXPERIMENT_ID, name, inputs, verdict, repo_root=repo_root,
                                         note="selected variant" if name == selected else "unselected diagnostic; design-only selection")
    verdict, reasons = (variants[selected]["verdict"], variants[selected]["reasons"]) if selected else (
        "insufficient", ["no variant has a defined design-period monthly IC t statistic"])
    final_metrics = {"selected_variant": selected, **(variants[selected]["judgement_inputs"] if selected else {})}
    experiment_registry.record_trial(EXPERIMENT_ID, "final", final_metrics, verdict, repo_root=repo_root, note="D001 final verdict")
    days = prices.index.get_level_values("trade_date")
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": selected, "verdict": verdict, "reasons": reasons,
               "variants": variants, "common_skipped_months": skipped, "interpretations": INTERPRETATIONS,
               "coverage": {"first_session": days.min().date().isoformat(), "last_session": days.max().date().isoformat(),
                            "sessions": int(days.nunique()), "stocks": int(prices.index.get_level_values("stock_code").nunique()),
                            "price_rows": len(prices), "planned_months": len(months)},
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
               "assumptions": ["2025-12 등 20거래일 청산 구간이 없는 월은 제외하며 2026년 가격을 읽지 않습니다.",
                               "월말 장 마감 후 17:00 KST의 PointInTimeData를 사용합니다. 미검증 행과 미관측 값은 채우지 않습니다.",
                               "IC와 5분위는 유효 점수·체결 결과가 있는 종목 10개 이상인 월에서 계산합니다. IC t는 모든 유효 월을 사용합니다.",
                               "동일 월 유니버스 벤치마크는 팩터 유효 여부와 무관하게 공통 유니버스의 체결 가능한 종목을 동일가중합니다. 유동성 지표는 해당 구간 안에서 비교합니다.",
                               "net 판정은 월별 최상위 분위 순수익에서 같은 월 유니버스 동일가중 총수익을 뺀 평균(top_net_excess)입니다. 절대 순수익은 보조 수치로 보고합니다.",
                               "설계 t가 같은 경우 사전등록 표 순서로 선택합니다. 미선택 변형의 검증 결과는 선택 이후 진단으로만 기록합니다.",
                               "가격 파일의 최신 저장 관측을 사용합니다. 원주가 대체와 no_later_price 제외는 signal_eval 정책을 따릅니다.",
                               "no_later_price는 이후 가격 부재에 따른 제외이며, 실제 상장폐지를 독립적으로 확인한 건수는 아닙니다.",
                               "용량은 기존 p25 추정치와 월별 최저 거래대금에 따른 동일가중 1% 한도를 함께 보고합니다.",
                               "배수 비용은 진입 시가·진입일·결정일 ADV를 사용하며 세금에는 배수를 적용하지 않습니다.",
                               "메모리는 Linux/WSL 프로세스 전체의 최고 RSS이며 시간은 결과 파일 작성 직전까지입니다."]}
    common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print plan and file coverage only (default)")
    mode.add_argument("--execute", action="store_true", help="run local D001 evaluation and append results")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data/market/krx_daily")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; selected={result['selected_variant']}")
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run")
        print(f"Decision months: {plan['decision_start']}..{plan['decision_end']} ({plan['months']}; design {plan['design_months']}; validation {plan['months'] - plan['design_months']})")
        print("Variants: " + ", ".join(plan["variants"]))
        print(f"Data directory: {plan['data_dir']}")
        print(f"Guarded read span: {plan['read_start']}..{plan['read_end']}")
        print(f"Price files: {plan['files']}; first={plan['first_file']}; last={plan['last_file']}")
        print("Files by year: " + ", ".join(f"{year}={count}" for year, count in plan["files_by_year"].items()))
        print(f"Covered decision months: {plan['covered_months']}/{plan['months']}")
        print("Missing months: " + ", ".join(plan["missing_months"]))
        print("Incomplete 20-session horizon months: " + ", ".join(plan["incomplete_horizon_months"]))
        print("Coverage counts stored files; calendar verification, stock counts and features are checked on execute.")
        print("No prices evaluated; no registry rows or result files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
