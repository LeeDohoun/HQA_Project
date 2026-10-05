"""Shared preregistration, monthly universe, verdict and result publication rules."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, holdout
from backtesting.experiment_registry import PROJECT_ROOT, REGISTRY_PATH


COST_MODEL_V2_INTERPRETATION = (
    "Post-registration diagnostic, not used for verdicts. Same portfolio names, weights, "
    "cash slots and gross returns; recompute full entry-date round-trip costs with v2 "
    "(dated market ticks and T+2 settlement tax) at multipliers 1/1.5/2. "
    "The registered primary costs and all judgement inputs remain v1."
)


def reprice_costs_v2(observations, prices, entry_column, charged):
    """Copy fixed observations and reprice only their already charged entries."""
    adjusted = observations.copy()
    if not charged.any():
        return adjusted
    rows = observations.loc[charged]
    index = pd.MultiIndex.from_arrays([rows[entry_column], rows.stock_code],
                                      names=["trade_date", "stock_code"])
    entries = prices.reindex(index)
    for multiplier in (1.0, 1.5, 2.0):
        adjusted.loc[charged, f"cost_{multiplier}"] = cost_model.round_trip_cost_vectorized(
            entries.open.to_numpy(), entries.market.to_numpy(), rows[entry_column].to_numpy(),
            rows.avg_trading_value_20d.to_numpy(), multiplier=multiplier, model_version="v2")
    return adjusted


def load_preregistration(experiment_id, *, repo_root=PROJECT_ROOT) -> dict:
    return experiment_registry.verify_preregistration(experiment_id, repo_root).fields


def guard_prices(prices, *, repo_root=PROJECT_ROOT) -> None:
    """Guard the entire supplied span, rather than just selected scoring dates.

    These design/validation runners never claim a holdout evaluation, even if a
    different registration happens to permit one.
    """
    if not prices.empty:
        days = prices.index.get_level_values("trade_date")
        holdout.guard_period(days.min().date(), days.max().date(), repo_root=repo_root)


def _sessions(prices) -> pd.DatetimeIndex:
    if not isinstance(prices.index, pd.MultiIndex) or prices.index.names != ["trade_date", "stock_code"]:
        raise ValueError("prices must be indexed by (trade_date, stock_code)")
    days = pd.DatetimeIndex(prices.index.get_level_values("trade_date").unique()).sort_values()
    if days.hasnans or days.tz is not None or not days.equals(days.normalize()):
        raise ValueError("trade_date must contain date-only, timezone-naive session dates")
    return days


def month_end_sessions(prices, start, end) -> pd.DatetimeIndex:
    """Last supplied session of each month within the inclusive decision period."""
    days = _sessions(prices)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start > end:
        raise ValueError("start must not be after end")
    days = days[(days >= start) & (days <= end)]
    return pd.DatetimeIndex(pd.Series(days, index=days.to_period("M")).groupby(level=0).max())


def next_session(prices, day):
    days = _sessions(prices)
    position = days.searchsorted(pd.Timestamp(day), side="right")
    return days[position] if position < len(days) else None


def universe_filter(prices, decision_date, *, repo_root=PROJECT_ROOT) -> pd.DataFrame:
    """D001/HC001/HI001 common shares with complete, verified 20-session ADV.

    The caller supplies a point-in-time view. This additionally discards dates
    after the decision date, requires its exact current row, and never uses a
    close*volume substitute for missing trading_value.
    """
    days = _sessions(prices)
    guard_prices(prices, repo_root=repo_root)
    required = {"stock_name", "market", "close", "trading_value", "calendar_status"}
    if not required.issubset(prices.columns):
        raise ValueError(f"missing universe columns: {sorted(required - set(prices.columns))}")
    day = pd.Timestamp(decision_date)
    history = days[days <= day][-20:]
    if day not in days or len(history) < 20:
        return prices.iloc[:0].assign(avg_trading_value_20d=pd.Series(dtype=float)).droplevel("trade_date")
    window = prices.loc[prices.index.get_level_values("trade_date").isin(history)]
    value = window.trading_value.where(window.calendar_status.eq("verified"))
    wide = value.unstack("stock_code").reindex(history)
    wide = wide.where(np.isfinite(wide))
    adv = wide.mean().where(wide.count() == 20)
    current = prices.xs(day, level="trade_date").copy()
    if current.stock_name.isna().any() or not current.stock_name.map(lambda name: isinstance(name, str) and bool(name.strip())).all():
        raise ValueError("stock_name is required to exclude SPACs")
    codes = current.index.astype(str)
    current["avg_trading_value_20d"] = adv.reindex(current.index)
    eligible = (codes.str.endswith("0") & ~current.stock_name.str.contains("스팩", regex=False)
                & current.market.isin(("KOSPI", "KOSDAQ")) & current.calendar_status.eq("verified")
                & np.isfinite(current.close) & (current.close >= 1000)
                & (current.avg_trading_value_20d >= 1e8))
    return current.loc[eligible]


def judge(fields, results, *, net_metric, net_at_cost_1_5_metric,
          repo_root=PROJECT_ROOT, registry_path=REGISTRY_PATH) -> tuple[str, list[str]]:
    """Judge monthly observations and runner-defined net performance, with reasons.

    results contains observations, t_stat, random_control_share, the two named
    net performance metrics and validation_year_same_sign (None if not measured).
    trial_count_after_run, when supplied by a batch runner, also includes its
    impending registry rows so a batch crossing 20 cannot use the lower t bar.
    """
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = max(float(criteria["core_t_stat_gt"]), experiment_registry.required_t_stat(
        fields["experiment_id"], repo_root=repo_root, registry_path=registry_path))
    if results.get("trial_count_after_run", 0) > 20:
        threshold = max(threshold, float(criteria["core_t_stat_gt_when_trials_gt_20"]))
    missing = []
    minimum = criteria["minimum_observations"]["rebalances"]
    if results["observations"] < minimum:
        missing.append(f"monthly observations {results['observations']} < {minimum}")
    for name in ("t_stat", "random_control_share", net_metric, net_at_cost_1_5_metric):
        value = results[name]
        if value is None or not np.isfinite(value):
            missing.append(f"{name} is undefined")
    if results["validation_year_same_sign"] is None:
        missing.append("validation-year sign is unmeasured")
    if missing:
        return "insufficient", missing
    failures = []
    if results["t_stat"] <= threshold:
        failures.append(f"t_stat must exceed {threshold:g}")
    random_max = min(0.05, criteria["random_control_top_percent"] / 100)
    if results["random_control_share"] > random_max:
        failures.append(f"random_control_share must be <= {random_max:g}")
    if results[net_metric] <= criteria["net_performance_gt"]:
        failures.append(f"{net_metric} must exceed {criteria['net_performance_gt']:g}")
    if results[net_at_cost_1_5_metric] <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
        failures.append(f"{net_at_cost_1_5_metric} at cost x1.5 must exceed "
                        f"{criteria['net_performance_at_cost_multiplier_1_5_gt']:g}")
    if not results["validation_year_same_sign"]:
        failures.append("validation-year IC sign differs from design")
    return ("fail", failures) if failures else ("pass", ["all preregistered criteria satisfied"])


def write_results(experiment_id, payload, *, repo_root=PROJECT_ROOT) -> tuple[Path, Path]:
    """Publish finite JSON and a Korean summary with exclusive UTC filenames."""
    load_preregistration(experiment_id, repo_root=repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    verdict = {"pass": "통과", "fail": "실패", "insufficient": "관측 부족"}[payload["verdict"]]
    lines = [f"# 실험 결과: {experiment_id}", "",
             f"- 선택 변형: {payload['selected_variant']}", f"- 최종 판정: {verdict}",
             "- 판정 사유: " + "; ".join(payload["reasons"]), "",
             "월별 IC 한 개를 관측 한 회로 사용합니다. 변형 선택에는 설계 구간만 사용합니다.", "",
             "| 변형 | 월 수 | 월별 IC 평균 | 월별 IC t | 최상위 분위 절대 순수익 (보조) | 유니버스 대비 순초과수익 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for variant, result in payload["variants"].items():
        ic, costs = result["ic"], result["cost_sensitivity"]["1.0"]
        lines.append(f"| {variant} | {ic['count']} | {ic['mean']} | {ic['t_stat']} | {costs['top_net']} | {costs['top_net_excess']} |")
    lines += ["", "| 변형 | 비용 1.5배 절대 순수익 (보조) | 비용 1.5배 순초과수익 | 비용 2배 절대 순수익 (보조) | 실제 IC 이상 대조군 비율 | 제외 월 | 원주가 대체 | 이후 가격 부재 제외 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for variant, result in payload["variants"].items():
        lines.append(f"| {variant} | {result['cost_sensitivity']['1.5']['top_net']} | "
                     f"{result['cost_sensitivity']['1.5']['top_net_excess']} | "
                     f"{result['cost_sensitivity']['2.0']['top_net']} | {result['random_control']['share_of_controls']} | "
                     f"{result['skipped_month_count']} | {result['unadjusted_fallback']} | {result['delisting_exclusions']} |")
    interpretations = payload["interpretations"]
    lines += ["", "## interpretations (판정 기준 해석)", "", interpretations["note"], "",
              "| 기준 | 사용 지표 | 정의 |", "| --- | --- | --- |"]
    for criterion, interpretation in interpretations["criteria"].items():
        lines.append(f"| {criterion} | {interpretation['metric']} | {interpretation['definition']} |")
    performance = payload["performance"]
    lines += ["", f"실행 시간: {performance['runtime_seconds']:.3f}초. "
              f"프로세스 최대 메모리(RSS): {performance['peak_memory_mib']:.1f} MiB.", "",
              "제외 월, 원주가 대체, 상장폐지 관련 제외, 비용 배수, 연도별 IC, 유동성 및 용량은 같은 이름의 JSON에 기록했습니다.", "",
              "가정과 제한:", ""] + [f"- {note}" for note in payload["assumptions"]]
    directory = Path(repo_root) / "research/experiments" / experiment_id / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, summary_path = directory / f"{stamp}.json", directory / f"{stamp}.md"
    # Reserve both before writing. A collision is an explicit error, never an overwrite.
    with json_path.open("x", encoding="utf-8") as json_file:
        with summary_path.open("x", encoding="utf-8") as summary_file:
            json_file.write(encoded)
            summary_file.write("\n".join(lines) + "\n")
    return json_path, summary_path
