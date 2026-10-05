"""HC001 quarterly operating leverage experiment; dry-run is the CLI default.

Definitions below are fixed before evaluating results. Filing versions come from
load_quarterly; only its YoY arithmetic, never its phase priority, is reused.
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import exchange_calendars as calendars
import numpy as np
import pandas as pd

from backtesting import capacity, cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, d001
from src.ingestion import dart_quarterly
from src.ingestion.storage import read_rows
from src.research import industry_map


EXPERIMENT_ID = "HC001_operating_leverage"
VARIANTS = ("phase2_ew", "score_ic")
HORIZON = 60
N_CONTROLS = 200
PRICE_START, PRICE_END = "20150101", "20251231"
CUTOFF = pd.Timestamp("2026-01-01")
FINANCIAL_GROUPS = {"금융(은행·증권·보험)", "지주회사"}
NET_METRIC = "mean_rebalance_excess"
NET_AT_COST_1_5_METRIC = "mean_rebalance_excess_at_cost_1_5"
INTERPRETATIONS = {
    "note": "정의는 실제 결과를 보기 전(2026-10-05)에 확정. 국면 우선순위는 사전등록의 2 → 3 → 1 → 4입니다.",
    "criteria": {
        "net_performance_gt": {
            "metric": f"judgement_inputs.{NET_METRIC} = excess.mean",
            "definition": "리밸런싱별 (2국면 동일가중 60세션 순수익 − 같은 유니버스 동일가중 총수익)의 평균입니다.",
        },
        "net_performance_at_cost_multiplier_1_5_gt": {
            "metric": f"judgement_inputs.{NET_AT_COST_1_5_METRIC} = cost_sensitivity['1.5'].top_net_excess",
            "definition": "같은 리밸런싱에서 비용 1.5배의 순초과수익 평균. 비용 모델대로 세금에는 배수를 적용하지 않습니다.",
        },
        "core_t_stat_gt": {
            "metric": "judgement_inputs.t_stat = excess.t_stat",
            "definition": "모든 유효 리밸런싱의 초과수익 평균 / (표본 표준편차 / 리밸런싱 수의 제곱근). IC t가 아닙니다.",
        },
        "core_t_stat_gt_when_trials_gt_20": {
            "metric": "judgement_inputs.t_stat = excess.t_stat",
            "definition": "이번 실행의 두 대장 행을 포함해 시도 수가 20을 초과하면 같은 초과수익 t에 강화된 기준을 적용합니다.",
        },
        "random_control_top_percent": {
            "metric": "judgement_inputs.random_control_share = random_control.share_of_controls",
            "definition": "매 리밸런싱 같은 종목 수를 비복원 추출한 200개 대조군 중 평균 순초과수익이 실제 이상인 비율. 관측 날짜와 왕복 비용은 실제와 같습니다.",
        },
        "minimum_observations": {
            "metric": "judgement_inputs.observations = excess.count",
            "definition": "2국면 포트폴리오 초과수익을 계산할 수 있는 리밸런싱 수를 36회 기준과 비교합니다. 종목 행 수가 아닙니다.",
        },
        "validation_year_same_sign": {
            "metric": "judgement_inputs.validation_year_same_sign",
            "definition": "2025년 리밸런싱 평균 순초과수익과 2016~2024년 평균 순초과수익의 부호를 비교합니다.",
        },
    },
}


def _registration(repo_root):
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    if fields["variants_planned"] != len(VARIANTS) or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("HC001 requires its two fixed variants, no holdout and no LLM")
    return fields


def _session_calendar():
    # Calendar metadata after 2025 is used only to identify forbidden horizons;
    # no price file dated 2026 or later is opened.
    return calendars.get_calendar("XKRX", start="2015-01-01", end="2026-03-31").sessions


def rebalance_calendar(sessions):
    """Resolve the 40 anchors on an exchange calendar, counting entry as day 1."""
    days = signal_eval._dates(sessions).sort_values().unique()
    planned, excluded = [], []
    for year in range(2016, 2026):
        for month, date in ((4, 1), (5, 16), (8, 15), (11, 15)):
            anchor = pd.Timestamp(year, month, date)
            position = days.searchsorted(anchor)
            row = {"anchor": anchor.date().isoformat()}
            if position == len(days) or days[position].year != year or days[position].month != month:
                excluded.append({**row, "reason": "missing_rebalance_session"})
                continue
            row["trade_date"] = days[position].date().isoformat()
            target = position + HORIZON - 1
            if target >= len(days):
                excluded.append({**row, "reason": "incomplete_calendar_horizon"})
                continue
            row["exit_date"] = days[target].date().isoformat()
            if days[target] >= CUTOFF:
                excluded.append({**row, "reason": "holdout_horizon"})
            else:
                planned.append(row)
    return planned, excluded


def _financial_inventory(data_dir):
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    files, receipts, stocks = {}, {}, set()
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_110*.jsonl")):
        year, report = path.stem.split("_")
        if report not in dart_quarterly.REPORTS:
            raise ValueError(f"unknown quarterly report archive: {path.name}")
        quarter = f"{year}Q{dart_quarterly.REPORTS[report]}"
        # Report later archive presence without inspecting holdout financials.
        row = {"file": path.name, "bytes": path.stat().st_size, "rows": None}
        if int(year) <= 2025:
            records = read_rows(path)
            row["rows"] = len(records)
            for record in records:
                key = record["corp_code"], record["fiscal_quarter"], record["fs_div"]
                receipts.setdefault(key, set()).add(record["rcept_no"])
                stocks.update(record["stock_codes"])
        files[quarter] = row
    expected = [f"{year}Q{quarter}" for year in range(2015, 2026) for quarter in range(1, 5)]
    coverage = {"directory": str(directory), "directory_exists": directory.is_dir(), "quarters": files,
                "missing_quarters": [key for key in expected if key not in files],
                "empty_quarters": [key for key in expected if key in files and not files[key]["bytes"]],
                "stored_rows_through_2025": sum(row["rows"] for row in files.values() if row["rows"] is not None),
                "stock_aliases_through_2025": len(stocks)}
    first_receipts = {key: min(values) for key, values in receipts.items()}
    return coverage, first_receipts


def _industries(data_dir):
    path = Path(data_dir) / "reference/dart_company/companies.jsonl"
    # Missing profiles are the explicitly preregistered inclusion policy.
    # Cache the same mapping used by industry_of, rather than rereading it per stock.
    return industry_map.load_industry_map(path) if path.is_file() else {}


def exclude_financials(universe, profiles):
    profiled = universe.index.isin(profiles)
    coverage = float(profiled.mean()) if len(universe) else None
    groups = pd.Series([profiles.get(code, industry_map.UNCLASSIFIED) for code in universe.index], index=universe.index)
    apply = coverage is not None and coverage >= 0.8
    excluded = groups.isin(FINANCIAL_GROUPS) if apply else pd.Series(False, index=universe.index)
    return universe.loc[~excluded], {
        "universe_size": len(universe), "profiled": int(profiled.sum()), "profile_coverage": coverage,
        "classified": int(groups.ne(industry_map.UNCLASSIFIED).sum()),
        "financials_excluded": int(excluded.sum()), "exclusion_applied": apply,
        "note": "financial industries excluded" if apply else "profile coverage below 80% or no universe; financials included",
    }


def phase_signals(frame):
    """Reuse -200%/500% YoY conventions; implement the preregistered phase order."""
    result = dart_quarterly.yoy_signals(frame)
    periods = pd.PeriodIndex(result.fiscal_quarter, freq="Q")
    values = {(corp, period): revenue for corp, period, revenue in zip(result.corp_code, periods, result.revenue)}
    highs = []
    for corp, period, current in zip(result.corp_code, periods, result.revenue):
        history = [values.get((corp, period - offset), np.nan) for offset in range(6)]
        highs.append(all(np.isfinite(value) for value in history) and current >= max(history))
    computable = np.isfinite(result.revenue_yoy) & np.isfinite(result.operating_income_yoy)
    masks = {
        2: computable & (result.revenue_yoy > 0) & (result.operating_income_yoy > result.revenue_yoy),
        3: computable & (result.revenue_yoy > 0) & (result.operating_income_yoy < result.revenue_yoy),
        1: computable & pd.Series(highs, index=result.index) & (result.operating_income_yoy <= result.revenue_yoy),
        4: computable & (result.revenue_yoy <= 0),
    }
    phases = pd.Series(pd.NA, index=result.index, dtype="Int64")
    for phase, mask in masks.items():
        result[f"matches_phase{phase}"] = mask
        phases.loc[mask & phases.isna()] = phase
    result["phase"] = phases
    result["phase_overlap"] = sum(masks.values()) > 1
    result["score"] = result.operating_income_yoy - result.revenue_yoy
    result["computable"] = computable
    return result


def signals_as_of(day, *, data_dir=PROJECT_ROOT / "data", first_receipts=None):
    known = dart_quarterly.load_quarterly(day, data_dir=data_dir)
    signals = phase_signals(known)
    signals["correction_used"] = False
    if first_receipts is not None:
        signals["correction_used"] = [row.rcept_no != first_receipts[(row.corp_code, row.fiscal_quarter, row.fs_div)]
                                       for row in signals.itertuples()]
    # Pick the latest quarter BEFORE checking computability; never backfill a
    # missing current signal with a more convenient older quarter.
    latest = signals.sort_values("fiscal_quarter", kind="stable").groupby("stock_code", sort=False).tail(1).copy()
    by_quarter = {(row.stock_code, row.fiscal_quarter): row for row in signals.itertuples()}
    prior_corrected, conflicts = [], []
    for row in latest.itertuples():
        prior = by_quarter.get((row.stock_code, str(pd.Period(row.fiscal_quarter, freq="Q") - 4)))
        prior_corrected.append(prior is not None and prior.correction_used)
        reasons = [row.missing_reasons, prior.missing_reasons if prior is not None else {}]
        conflicts.append(sum(isinstance(reason.get(metric), dict) and reason[metric].get("reason") == "conflicting_accounts"
                             for reason in reasons for metric in ("revenue", "operating_income")))
    latest["prior_correction_used"] = prior_corrected
    latest["conflicting_metrics"] = conflicts
    return latest.set_index("stock_code")


def _universe_on_decision(history, decision, day, profiles, *, data_dir, first_receipts, repo_root):
    universe = common.universe_filter(history, decision, repo_root=repo_root)
    signals = signals_as_of(day, data_dir=data_dir, first_receipts=first_receipts).reindex(universe.index)
    valid = signals.computable.eq(True)
    report = {"trade_date": day.date().isoformat(), "price_eligible": len(universe),
              "no_data": int((~valid).sum()),
              "no_financials": int(signals.fiscal_quarter.isna().sum()),
              "corrections_used": int((signals.correction_used.eq(True) | signals.prior_correction_used.eq(True)).sum()),
              "conflicting_metrics": int(signals.conflicting_metrics.sum()),
              "phase_overlap": int(signals.phase_overlap.eq(True).sum()),
              "phase1_phase3_overlap": int((signals.matches_phase1.eq(True) & signals.matches_phase3.eq(True)).sum()),
              "phase1_phase4_overlap": int((signals.matches_phase1.eq(True) & signals.matches_phase4.eq(True)).sum())}
    eligible = universe[["avg_trading_value_20d"]].join(signals.loc[valid])
    eligible = eligible.loc[valid]
    eligible, industry = exclude_financials(eligible, profiles)
    report["industry_profiles"] = industry
    report["unclassified_phase"] = int(eligible.phase.isna().sum())
    return eligible, report


def build_universe(prices, *, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, sessions=None):
    common.guard_prices(prices, repo_root=repo_root)
    available = common._sessions(prices)
    sessions = _session_calendar() if sessions is None else signal_eval._dates(sessions)
    if not available.difference(sessions).empty:
        raise ValueError("price dates must be exchange sessions")
    planned, excluded = rebalance_calendar(sessions)
    coverage, first_receipts = _financial_inventory(data_dir)
    if not coverage["directory_exists"]:
        raise ValueError(f"quarterly directory does not exist: {coverage['directory']}")
    profiles = _industries(data_dir)
    rows, counts = [], []
    for rebalance in planned:
        day = pd.Timestamp(rebalance["trade_date"])
        position = sessions.get_loc(day)
        required = sessions[max(0, position - 20):position + HORIZON]
        missing = required.difference(available)
        if position < 20 or len(missing):
            excluded.append({**rebalance, "reason": "missing_price_sessions", "missing_sessions": [date.date().isoformat() for date in missing]})
            continue
        decision = sessions[position - 1]
        price_days = prices.index.get_level_values("trade_date")
        history = prices.loc[price_days.isin(sessions[position - 20:position])]
        eligible, report = _universe_on_decision(
            history, decision, day, profiles, data_dir=data_dir, first_receipts=first_receipts, repo_root=repo_root)
        counts.append(report)
        if eligible.empty:
            excluded.append({**rebalance, "reason": "no_eligible_signals"})
            continue
        rows.append(eligible.assign(trade_date=day, decision_date=decision).reset_index())
    columns = ["stock_code", "avg_trading_value_20d", "phase", "revenue_yoy", "score", "trade_date", "decision_date"]
    universe = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=columns)
    return universe, counts, excluded, coverage


def _observations(prices, universe, *, repo_root=PROJECT_ROOT):
    return _observations_for_horizon(prices, universe, HORIZON, repo_root=repo_root)


def _observations_for_horizon(prices, universe, horizon, *, repo_root=PROJECT_ROOT, last_dates=None):
    common.guard_prices(prices, repo_root=repo_root)
    columns = ["trade_date", "stock_code", "avg_trading_value_20d", "phase", "revenue_yoy", "score", "gross", "reason", "exit_date",
               "bucket", "cost_1.0", "cost_1.5", "cost_2.0", *signal_eval._RETURN_FLAGS]
    if universe.empty:
        return pd.DataFrame(columns=columns)
    index = pd.MultiIndex.from_frame(universe[["decision_date", "stock_code"]]).set_names(["trade_date", "stock_code"])
    holding = signal_eval._holding_data(prices, "exclude", last_dates=last_dates)
    window = signal_eval._holding_window(holding, horizon, "last_close")
    observations = signal_eval._forward_observations(holding, window, index).reset_index(drop=True)
    entries = holding["sessions"].get_indexer(universe.trade_date)
    stocks = holding["codes"].get_indexer(universe.stock_code)
    if (entries < 0).any() or (stocks < 0).any():
        raise ValueError("universe entry sessions and stock codes must exist in prices")
    opening, market = holding["open"][entries, stocks], holding["market"][entries, stocks]
    observations = pd.concat([universe.reset_index(drop=True), observations], axis=1)
    observations["bucket"] = np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, observations.avg_trading_value_20d, side="right")
    valid = np.isfinite(observations.gross)
    for multiplier in (1.0, 1.5, 2.0):
        observations[f"cost_{multiplier}"] = np.nan
        observations.loc[valid, f"cost_{multiplier}"] = cost_model.round_trip_cost_vectorized(
            opening[valid], market[valid], observations.trade_date.to_numpy()[valid],
            observations.avg_trading_value_20d.to_numpy()[valid], multiplier=multiplier)
    return observations


def _statistics(rows, value):
    result = signal_eval._summary([row[value] for row in rows])
    result["by_year"] = {year: signal_eval._summary([row[value] for row in rows if row["trade_date"].startswith(year)])
                         for year in sorted({row["trade_date"][:4] for row in rows})}
    return result


def random_controls(group, size, rng):
    """200 same-size net portfolios from the same executable universe, no replacement."""
    if not 0 < size <= len(group):
        raise ValueError("control portfolio size must be within the universe")
    group = group.sort_values("stock_code", kind="stable")
    net = (group.gross - group["cost_1.0"]).to_numpy(dtype=float)
    benchmark = float(group.gross.mean())
    return np.array([float(net[rng.choice(len(net), size=size, replace=False)].mean()) - benchmark
                     for _ in range(N_CONTROLS)])


def portfolio_metrics(frame, phase=2, *, rng=None):
    valid = frame.loc[np.isfinite(frame.gross.astype(float))].copy()
    rows, controls, selections = [], [], []
    for day, group in valid.groupby("trade_date", sort=True):
        chosen = group.loc[group.phase.eq(phase)]
        if chosen.empty:
            continue
        benchmark = float(group.gross.mean())
        row = {"trade_date": day.date().isoformat(), "count": len(chosen), "universe_count": len(group),
               "gross": float(chosen.gross.mean()), "universe_gross": benchmark}
        for multiplier in (1.0, 1.5, 2.0):
            row[f"net_{multiplier}"] = float((chosen.gross - chosen[f"cost_{multiplier}"]).mean())
            row[f"excess_{multiplier}"] = row[f"net_{multiplier}"] - benchmark
        rows.append(row)
        selections.append(chosen[["trade_date", "stock_code", "avg_trading_value_20d"]])
        if rng is not None:
            controls.append(random_controls(group, len(chosen), rng))
    excess = _statistics(rows, "excess_1.0")
    control_means = np.mean(controls, axis=0) if controls else np.array([])
    selected = pd.concat(selections, ignore_index=True) if selections else valid.iloc[:0]
    return {"excess": excess, "per_rebalance": rows,
            "cost_sensitivity": {str(multiplier): {"top_net": _statistics(rows, f"net_{multiplier}")["mean"],
                                                   "top_net_excess": _statistics(rows, f"excess_{multiplier}")["mean"],
                                                   "excess": _statistics(rows, f"excess_{multiplier}")}
                                 for multiplier in (1.0, 1.5, 2.0)},
            "random_control": {**signal_eval._control_summary(control_means, excess["mean"]),
                               "mean_excess": control_means.tolist(), "rebalances": len(controls), "portfolios_per_rebalance": N_CONTROLS},
            "capacity": capacity.strategy_capacity(selected[["trade_date", "stock_code", "avg_trading_value_20d"]]) if len(selected) else None,
            "capacity_by_rebalance": {day.date().isoformat(): len(group) * float(group.avg_trading_value_20d.min()) * 0.01
                                      for day, group in selected.groupby("trade_date")}}


def score_ic(frame):
    valid = frame.loc[np.isfinite(frame.gross.astype(float)) & (frame.revenue_yoy > 0)]
    rows = []
    for day, group in valid.groupby("trade_date", sort=True):
        score_rank, return_rank = signal_eval._rank_vector(group.score), signal_eval._rank_vector(group.gross)
        ic = float(np.dot(score_rank, return_rank)) if score_rank is not None and return_rank is not None else None
        rows.append({"trade_date": day.date().isoformat(), "ic": ic, "stocks": len(group)})
    result = _statistics(rows, "ic")
    result["daily"] = rows
    return result


def _judgement_inputs(result, fields, trial_count):
    period = fields["data_periods"]["design"]
    design = signal_eval._summary([row["excess_1.0"] for row in result["per_rebalance"]
                                  if pd.Timestamp(period["from"]) <= pd.Timestamp(row["trade_date"]) <= pd.Timestamp(period["to"])])
    year = str(pd.Timestamp(fields["data_periods"]["validation"]["from"]).year)
    validation = result["excess"]["by_year"].get(year, {}).get("mean")
    same_sign = bool(np.sign(validation) == np.sign(design["mean"])) if validation is not None and design["mean"] is not None else None
    return {"observations": result["excess"]["count"], "t_stat": result["excess"]["t_stat"],
            "random_control_share": result["random_control"]["share_of_controls"],
            NET_METRIC: result["excess"]["mean"],
            NET_AT_COST_1_5_METRIC: result["cost_sensitivity"]["1.5"]["top_net_excess"],
            "validation_year_same_sign": same_sign, "design_mean_excess": design["mean"],
            "validation_mean_excess": validation, "trial_count_after_run": trial_count}


def _write_results(payload, *, repo_root):
    # Reuse committed-registration checks, finite JSON and exclusive filenames.
    # The shared Markdown template is D001-specific; present HC001's actual unit
    # and primary statistic while retaining its interpretations/assumptions tail.
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root)
    summary = paths[1].read_text(encoding="utf-8")
    tail = summary[summary.index("## interpretations"):].replace(
        "제외 월, 원주가 대체, 상장폐지 관련 제외, 비용 배수, 연도별 IC, 유동성 및 용량은 같은 이름의 JSON에 기록했습니다.",
        "리밸런싱별 초과수익, 보조 IC, 제외·중복·프로필 건수, 국면별·유동성별·연도별 결과와 용량은 같은 이름의 JSON에 기록했습니다.")
    phase2, ic = payload["variants"]["phase2_ew"], payload["variants"]["score_ic"]["ic"]
    lines = [f"# 실험 결과: {EXPERIMENT_ID}", "", f"- 주 판정 변형: phase2_ew; 판정: {payload['verdict']}",
             "- 판정 사유: " + "; ".join(payload["reasons"]), "", "관측 단위는 분기 리밸런싱이며 변형 선택은 없습니다.", "",
             "| 리밸런싱 수 | 평균 순초과수익 | 초과수익 t | 비용 x1.5 순초과수익 | 비용 x2 순초과수익 | 대조군 이상 비율 |",
             "| ---: | ---: | ---: | ---: | ---: | ---: |",
             f"| {phase2['excess']['count']} | {phase2['excess']['mean']} | {phase2['excess']['t_stat']} | "
             f"{phase2['cost_sensitivity']['1.5']['top_net_excess']} | {phase2['cost_sensitivity']['2.0']['top_net_excess']} | "
             f"{phase2['random_control']['share_of_controls']} |", "",
             f"score_ic (보조): 유효 리밸런싱 {ic['count']}회, 평균 {ic['mean']}, t {ic['t_stat']}.", "", tail]
    paths[1].write_text("\n".join(lines), encoding="utf-8")
    return paths


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    """Evaluate local design/validation data and record exactly two fixed variants.

    Supplied prices/sessions are offline fixture inputs, not CLI strategy options.
    """
    started = time.perf_counter()
    fields = _registration(repo_root)
    if prices is None:
        prices = d001._load_prices(Path(data_dir) / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
    common.guard_prices(prices, repo_root=repo_root)
    if prices.empty:
        raise ValueError("no price observations in the guarded HC001 span")
    if common._sessions(prices).max() >= CUTOFF:
        raise ValueError("HC001 prices must precede 2026-01-01")
    if prices.index.has_duplicates:
        raise ValueError("price keys must be unique")
    prices = prices.sort_index()
    universe, counts, excluded, financial_coverage = build_universe(prices, data_dir=data_dir, repo_root=repo_root, sessions=sessions)
    observations = _observations(prices, universe, repo_root=repo_root)
    primary = portfolio_metrics(observations, rng=np.random.default_rng(0))
    ic = score_ic(observations)
    inputs = _judgement_inputs(primary, fields, experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS))
    verdict, reasons = common.judge(fields, inputs, net_metric=NET_METRIC,
                                    net_at_cost_1_5_metric=NET_AT_COST_1_5_METRIC, repo_root=repo_root)
    reasons = [reason.replace("monthly observations", "rebalance observations").replace("validation-year IC sign", "validation-year excess sign") for reason in reasons]
    return_counts = signal_eval._return_counts(observations)
    primary.update(ic=ic, verdict=verdict, reasons=reasons, judgement_inputs=inputs,
                   skipped_rebalance_count=40 - primary["excess"]["count"],
                   skipped_month_count=40 - primary["excess"]["count"], **return_counts,
                   delisting_exclusions=return_counts["no_later_price"])
    # These empty portfolio fields are only the shared writer's display contract;
    # score_ic is an IC diagnostic and has no portfolio or independent verdict.
    diagnostic = {"ic": ic, "verdict": "diagnostic", "cost_sensitivity": {
        str(multiplier): {"top_net": None, "top_net_excess": None} for multiplier in (1.0, 1.5, 2.0)},
        "random_control": {"share_of_controls": None}, "skipped_month_count": 40 - ic["count"],
        "unadjusted_fallback": return_counts["unadjusted_fallback"], "delisting_exclusions": return_counts["no_later_price"]}
    selected_dates = {row["trade_date"] for row in primary["per_rebalance"]}
    skipped = [*excluded, *({"trade_date": day.date().isoformat(), "reason": "no_executable_phase2_names"}
                            for day in sorted(universe.trade_date.unique()) if day.date().isoformat() not in selected_dates)]
    buckets = {}
    lower = 0.0
    for number, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
        bucket = observations.loc[observations.bucket == number]
        buckets[str(number)] = {**portfolio_metrics(bucket), "ic": score_ic(bucket),
                               "lower_inclusive": lower, "upper_exclusive": float(upper) if np.isfinite(upper) else None}
        lower = float(upper)
    days = common._sessions(prices)
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": "phase2_ew", "verdict": verdict, "reasons": reasons,
               "variants": {"phase2_ew": primary, "score_ic": diagnostic}, "interpretations": INTERPRETATIONS,
               "secondary_phases": {str(phase): portfolio_metrics(observations, phase) for phase in (1, 3, 4)},
               "liquidity_buckets": buckets, "skipped_rebalances": skipped,
               "counts": {"by_rebalance": counts, "holdout_horizon_exclusions": sum(row["reason"] == "holdout_horizon" for row in excluded),
                          **{name: sum(row[name] for row in counts) for name in ("no_data", "no_financials", "corrections_used", "conflicting_metrics", "phase_overlap")},
                          **return_counts, "delisting_exclusions": return_counts["no_later_price"]},
               "stale_exits": signal_eval._stale_records(observations),
               "coverage": {"financials": financial_coverage, "first_session": days.min().date().isoformat(),
                            "last_session": days.max().date().isoformat(), "sessions": len(days),
                            "stocks": int(prices.index.get_level_values("stock_code").nunique()), "planned_rebalances": 40,
                            "calendar_version": f"exchange-calendars:{calendars.__version__}:XKRX"},
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
               "assumptions": [
                   "국면은 사전등록을 우선합니다. 최근 6분기 최대는 당기를 포함한 연속 6분기가 필요하며 같은 값도 최대로 봅니다.",
                   "가격은 KRX 최신 저장 버전입니다. 결정에는 전 세션까지 완전히 검증된 20세션 ADV를 사용하며 2026년 이후 가격은 읽지 않습니다.",
                   "재무 접수일 < 리밸런싱일입니다. 최신 분기가 결측이면 과거 분기로 돌아가지 않습니다. YoY 0분모는 결측이며 적자 전환·확대는 -200%, 상한은 500%입니다.",
                   "업종은 현재 프로필입니다. 신호 계산 가능한 유니버스의 프로필 비율 80% 미만은 금융을 포함하며 미분류 프로필도 따로 집계합니다.",
                   "벤치마크와 무작위 추출은 같은 신호 계산 가능·업종 필터 적용·체결 가능 유니버스입니다. 대조군도 종목별 왕복 비용 전액을 차감합니다.",
                   "상한가 시가 진입은 제외하며 결측 청산은 마지막 종가입니다. ret_1d 결측이면 기존 signal_eval의 원주가 대체를 명시 집계합니다.",
                   "no_later_price는 2025년까지 후속 가격이 없는 제외이며 실제 상장폐지를 독립 확인한 건수가 아닙니다.",
                   "corrections_used는 당기나 전년 동기에 첫 저장 접수번호 외 버전을 쓴 기업·리밸런싱 수입니다. 저장되지 않은 정정 이력은 검출하지 못합니다. conflicting_metrics는 두 분기의 매출·영업이익 충돌 수입니다.",
                   "유동성 구간은 각 구간 내부 유니버스 평균과 비교합니다. 연도별은 리밸런싱 연도이며 60세션 창을 단축하지 않습니다.",
                   "용량은 기존 p25 추정과 동일가중 모든 종목이 ADV 1% 이하인 종목 수 × 최소 ADV × 1% 한도를 함께 보고합니다.",
                   "비용은 진입 시가·진입일·결정일 ADV로 왕복 전액을 계산하며 세금에는 배수를 적용하지 않습니다. seed=0으로 200개 대조군을 재현합니다.",
                   "score_ic는 매출 YoY > 0인 연속 점수와 총수익의 평균 순위 Spearman입니다. 모든 유효 리밸런싱의 t를 보고하며 phase2_ew만 주 판정합니다.",
               ]}
    # Both diagnostics are predetermined trials; no extra selection/final trial.
    experiment_registry.record_trial(EXPERIMENT_ID, "phase2_ew", inputs, verdict, repo_root=repo_root, note="fixed primary; t and net use rebalance excess")
    experiment_registry.record_trial(EXPERIMENT_ID, "score_ic", ic, "diagnostic", repo_root=repo_root, note="preregistered secondary; no independent pass judgement")
    _write_results(payload, repo_root=repo_root)
    return payload


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    planned, excluded = rebalance_calendar(_session_calendar())
    price_files = d001._price_files(Path(data_dir) / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
    days = pd.DatetimeIndex([day for day, _ in price_files])
    sessions = _session_calendar()
    covered, incomplete = [], []
    for row in planned:
        position = sessions.get_loc(pd.Timestamp(row["trade_date"]))
        required = sessions[position - 20:position + HORIZON]
        (covered if required.difference(days).empty else incomplete).append(row["trade_date"])
    financials, _ = _financial_inventory(data_dir)
    profiles = _industries(data_dir)
    return {"experiment_id": EXPERIMENT_ID, "variants": list(VARIANTS), "data_dir": str(Path(data_dir)),
            "read_start": "2015-01-01", "read_end": "2025-12-31", "planned_rebalances": 40,
            "guarded_rebalances": len(planned), "excluded_rebalances": excluded, "covered_rebalances": covered,
            "incomplete_price_rebalances": incomplete, "price_files": len(price_files),
            "first_price_file": days.min().date().isoformat() if len(days) else None,
            "last_price_file": days.max().date().isoformat() if len(days) else None,
            "price_files_by_year": {str(year): int((days.year == year).sum()) for year in range(2015, 2026)},
            "financials": financials, "industry_profiles": len(profiles),
            "classified_profiles": sum(group != industry_map.UNCLASSIFIED for group in profiles.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print plan and local coverage only (default)")
    mode.add_argument("--execute", action="store_true", help="run and record the two preregistered local variants")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data", help="data root containing market/, fundamentals/ and reference/")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; rebalances={result['variants']['phase2_ew']['excess']['count']}")
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run")
        print(f"Data directory: {plan['data_dir']}")
        print("Variants: " + ", ".join(plan["variants"]) + "; judged=phase2_ew")
        print(f"Rebalances: {plan['planned_rebalances']} planned (2016-04..2025-11); {plan['guarded_rebalances']} before 2026")
        print("Excluded rebalances: " + json.dumps(plan["excluded_rebalances"]))
        print(f"Guarded price span: {plan['read_start']}..{plan['read_end']}; files={plan['price_files']}; first={plan['first_price_file']}; last={plan['last_price_file']}")
        print("Price files by year: " + ", ".join(f"{year}={count}" for year, count in plan["price_files_by_year"].items()))
        print(f"Rebalances with price-file coverage (20 prior + 60 holding sessions): {len(plan['covered_rebalances'])}/{plan['guarded_rebalances']}")
        print("Incomplete price rebalances: " + ", ".join(plan["incomplete_price_rebalances"]))
        financials = plan["financials"]
        print(f"Quarterly directory: {financials['directory']}; exists={financials['directory_exists']}")
        print(f"Quarterly archives: {len(financials['quarters'])}; rows through 2025={financials['stored_rows_through_2025']}; stock aliases={financials['stock_aliases_through_2025']}")
        for year in sorted({quarter[:4] for quarter in financials["quarters"]}):
            print(f"Quarterly {year}: " + ", ".join(f"{quarter[-2:]}={row['rows'] if row['rows'] is not None else 'presence only'} rows ({row['bytes']} bytes)"
                                                   for quarter, row in sorted(financials["quarters"].items()) if quarter.startswith(year)))
        print("Missing quarterly archives: " + ", ".join(financials["missing_quarters"]))
        print("Empty quarterly archives: " + ", ".join(financials["empty_quarters"]))
        print(f"Industry profiles: {plan['industry_profiles']}; classified={plan['classified_profiles']}; per-universe coverage/exclusion checked on execute (80% threshold).")
        print("Coverage is an archive snapshot, not proof of complete collection or computable signals. No returns evaluated; no registry rows or result files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
