"""HC002 monthly operating leverage experiment; dry-run is the CLI default."""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001


EXPERIMENT_ID = "HC002_operating_leverage_monthly"
VARIANTS = hc001.VARIANTS
HORIZON = 20
MONTHS = pd.period_range("2017-05", "2025-11", freq="M")
NET_METRIC = hc001.NET_METRIC
NET_AT_COST_1_5_METRIC = hc001.NET_AT_COST_1_5_METRIC
INTERPRETATIONS = {
    "note": "HC002 사전등록의 월별 관측과 전액 왕복 비용을 적용합니다. 회전율 비용은 보조 결과이며 판정에 사용하지 않습니다.",
    "criteria": {
        name: {key: value.replace("60세션", "20세션").replace("리밸런싱", "월별 관측")
               .replace("2016~2024", "2017-05~2024-12").replace("2025년", "2025-01~2025-11")
               for key, value in interpretation.items()}
        for name, interpretation in hc001.INTERPRETATIONS["criteria"].items()
    },
}


def _registration(repo_root):
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    if fields["variants_planned"] != len(VARIANTS) or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("HC002 requires its two fixed variants, no holdout and no LLM")
    return fields


def rebalance_calendar(sessions):
    """Month-end decisions, next-session entries and entry-inclusive 20-session exits."""
    days = signal_eval._dates(sessions).sort_values().unique()
    planned, excluded = [], []
    for month in MONTHS:
        row = {"month": str(month)}
        monthly = days[days.to_period("M") == month]
        if monthly.empty:
            excluded.append({**row, "reason": "missing_decision_session"})
            continue
        decision = monthly[-1]
        row["decision_date"] = decision.date().isoformat()
        entry = days.get_loc(decision) + 1
        if entry >= len(days):
            excluded.append({**row, "reason": "missing_entry_session"})
            continue
        row["trade_date"] = days[entry].date().isoformat()
        target = entry + HORIZON - 1
        if target >= len(days):
            excluded.append({**row, "reason": "incomplete_calendar_horizon"})
            continue
        row["exit_date"] = days[target].date().isoformat()
        if days[target] >= hc001.CUTOFF:
            excluded.append({**row, "reason": "holdout_horizon"})
        else:
            planned.append(row)
    return planned, excluded


def build_universe(prices, *, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, sessions=None):
    common.guard_prices(prices, repo_root=repo_root)
    available = common._sessions(prices)
    sessions = hc001._session_calendar() if sessions is None else signal_eval._dates(sessions).sort_values().unique()
    if not available.difference(sessions).empty:
        raise ValueError("price dates must be exchange sessions")
    planned, excluded = rebalance_calendar(sessions)
    coverage, first_receipts = hc001._financial_inventory(data_dir)
    if not coverage["directory_exists"]:
        raise ValueError(f"quarterly directory does not exist: {coverage['directory']}")
    profiles = hc001._industries(data_dir)
    rows, counts = [], []
    for rebalance in planned:
        day = pd.Timestamp(rebalance["decision_date"])
        position = sessions.get_loc(day)
        required = sessions[max(0, position - 19):position + 1 + HORIZON]
        missing = required.difference(available)
        if position < 19 or len(missing):
            excluded.append({**rebalance, "reason": "missing_price_sessions",
                             "missing_sessions": [date.date().isoformat() for date in missing]})
            continue
        history = prices.loc[prices.index.get_level_values("trade_date").isin(sessions[position - 19:position + 1])]
        # Month-end close is available; filings must still precede the decision
        # session, rather than the following entry session (HC001's strict rule).
        eligible, report = hc001._universe_on_decision(
            history, day, day, profiles, data_dir=data_dir, first_receipts=first_receipts, repo_root=repo_root)
        report.update(decision_date=rebalance["decision_date"], trade_date=rebalance["trade_date"])
        counts.append(report)
        if eligible.empty:
            excluded.append({**rebalance, "reason": "no_eligible_signals"})
            continue
        rows.append(eligible.assign(trade_date=pd.Timestamp(rebalance["trade_date"]), decision_date=day).reset_index())
    columns = ["stock_code", "avg_trading_value_20d", "phase", "revenue_yoy", "score", "trade_date", "decision_date"]
    universe = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=columns)
    return universe, counts, excluded, coverage


def _observations(prices, universe, *, repo_root=PROJECT_ROOT):
    observations = hc001._observations_for_horizon(prices, universe, HORIZON, repo_root=repo_root)
    # Shared metrics group/year-label by trade_date. Label observations with the
    # decision month, preserving the true entry used for execution and costs.
    observations["entry_date"] = observations.trade_date
    observations["trade_date"] = universe.decision_date.to_numpy()
    return observations


def turnover_metrics(frame):
    """Secondary only: retained executable phase-2 names pay no round-trip cost."""
    adjusted = frame.copy()
    previous, previous_month = set(), None
    rows = []
    for day, group in adjusted.groupby("trade_date", sort=True):
        month = day.to_period("M")
        if previous_month is None or month != previous_month + 1:
            previous = set()
        chosen = group.loc[np.isfinite(group.gross.astype(float)) & group.phase.eq(2)]
        current = set(chosen.stock_code)
        retained = chosen.stock_code.isin(previous)
        adjusted.loc[chosen.index[retained], [f"cost_{m}" for m in (1.0, 1.5, 2.0)]] = 0.0
        if current:
            rows.append({"decision_date": day.date().isoformat(), "count": len(current),
                         "retained": int(retained.sum()), "charged": int((~retained).sum()),
                         "turnover": float((~retained).mean())})
        previous, previous_month = current, month
    return {**hc001.portfolio_metrics(adjusted), "turnover_by_month": rows,
            "used_for_judgement": False}


def _write_results(payload, *, repo_root):
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root)
    summary = paths[1].read_text(encoding="utf-8")
    tail = summary[summary.index("## interpretations"):]
    primary = payload["variants"]["phase2_ew"]
    ic = payload["variants"]["score_ic"]["ic"]
    lines = [f"# 실험 결과: {EXPERIMENT_ID}", "", f"- 주 판정 변형: phase2_ew; 판정: {payload['verdict']}",
             "- 판정 사유: " + "; ".join(payload["reasons"]), "",
             "관측 단위는 월입니다. 전액 왕복 비용으로 판정하며 변형 선택은 없습니다.", "",
             "| 월 수 | 평균 순초과수익 | 초과수익 t | 비용 x1.5 순초과수익 | 비용 x2 순초과수익 | 대조군 이상 비율 |",
             "| ---: | ---: | ---: | ---: | ---: | ---: |",
             f"| {primary['excess']['count']} | {primary['excess']['mean']} | {primary['excess']['t_stat']} | "
             f"{primary['cost_sensitivity']['1.5']['top_net_excess']} | {primary['cost_sensitivity']['2.0']['top_net_excess']} | "
             f"{primary['random_control']['share_of_controls']} |", "",
             f"score_ic (보조): 유효 월 {ic['count']}회, 평균 {ic['mean']}, t {ic['t_stat']}.",
             f"회전율 비용 순초과수익 (보조, 판정 미사용): {payload['turnover_based']['excess']['mean']}.", "", tail]
    paths[1].write_text("\n".join(lines), encoding="utf-8")
    return paths


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    """Evaluate local design/validation data and record the two fixed variants."""
    started = time.perf_counter()
    fields = _registration(repo_root)
    if prices is None:
        prices = hc001.d001._load_prices(Path(data_dir) / "market/krx_daily", hc001.PRICE_START, hc001.PRICE_END, repo_root=repo_root)
    common.guard_prices(prices, repo_root=repo_root)
    if prices.empty:
        raise ValueError("no price observations in the guarded HC002 span")
    if common._sessions(prices).max() >= hc001.CUTOFF:
        raise ValueError("HC002 prices must precede 2026-01-01")
    if prices.index.has_duplicates:
        raise ValueError("price keys must be unique")
    prices = prices.sort_index()
    universe, counts, excluded, financials = build_universe(prices, data_dir=data_dir, repo_root=repo_root, sessions=sessions)
    observations = _observations(prices, universe, repo_root=repo_root)
    primary = hc001.portfolio_metrics(observations, rng=np.random.default_rng(0))
    ic = hc001.score_ic(observations)
    inputs = hc001._judgement_inputs(primary, fields, experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS))
    verdict, reasons = common.judge(fields, inputs, net_metric=NET_METRIC,
                                    net_at_cost_1_5_metric=NET_AT_COST_1_5_METRIC, repo_root=repo_root)
    reasons = [reason.replace("validation-year IC sign", "validation-year excess sign") for reason in reasons]
    return_counts = signal_eval._return_counts(observations)
    primary.update(ic=ic, verdict=verdict, reasons=reasons, judgement_inputs=inputs,
                   skipped_month_count=len(MONTHS) - primary["excess"]["count"], **return_counts,
                   delisting_exclusions=return_counts["no_later_price"])
    diagnostic = {"ic": ic, "verdict": "diagnostic", "cost_sensitivity": {
        str(m): {"top_net": None, "top_net_excess": None} for m in (1.0, 1.5, 2.0)},
        "random_control": {"share_of_controls": None}, "skipped_month_count": len(MONTHS) - ic["count"],
        "unadjusted_fallback": return_counts["unadjusted_fallback"], "delisting_exclusions": return_counts["no_later_price"]}
    selected_dates = {row["trade_date"] for row in primary["per_rebalance"]}
    skipped = [*excluded, *({"decision_date": day.date().isoformat(), "reason": "no_executable_phase2_names"}
                            for day in sorted(universe.decision_date.unique()) if day.date().isoformat() not in selected_dates)]
    buckets = {}
    lower = 0.0
    for number, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
        bucket = observations.loc[observations.bucket == number]
        buckets[str(number)] = {**hc001.portfolio_metrics(bucket), "ic": hc001.score_ic(bucket),
                               "lower_inclusive": lower, "upper_exclusive": float(upper) if np.isfinite(upper) else None}
        lower = float(upper)
    days = common._sessions(prices)
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": "phase2_ew", "verdict": verdict, "reasons": reasons,
               "variants": {"phase2_ew": primary, "score_ic": diagnostic}, "interpretations": INTERPRETATIONS,
               "turnover_based": turnover_metrics(observations),
               "secondary_phases": {str(phase): hc001.portfolio_metrics(observations, phase) for phase in (1, 3, 4)},
               "liquidity_buckets": buckets, "skipped_months": skipped,
               "counts": {"by_month": counts, "holdout_horizon_exclusions": sum(row["reason"] == "holdout_horizon" for row in excluded),
                          **{name: sum(row[name] for row in counts) for name in ("no_data", "no_financials", "corrections_used", "conflicting_metrics", "phase_overlap")},
                          **return_counts, "delisting_exclusions": return_counts["no_later_price"]},
               "stale_exits": signal_eval._stale_records(observations),
               "coverage": {"financials": financials, "first_session": days.min().date().isoformat(),
                            "last_session": days.max().date().isoformat(), "sessions": len(days),
                            "stocks": int(prices.index.get_level_values("stock_code").nunique()), "planned_months": len(MONTHS),
                            "calendar_version": f"exchange-calendars:{hc001.calendars.__version__}:XKRX"},
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
               "assumptions": [
                   "HC001의 분기 단독 값·YoY·국면 우선순위·금융업 80% 프로필 기준·상한가·청산·상장폐지 처리와 비용 모델을 재사용합니다.",
                   "월말 종가와 당일까지 검증된 20세션 ADV로 유니버스를 정합니다. 재무 접수일은 월말 의사결정일보다 이전이어야 하며 최신 분기 결측을 과거 분기로 대체하지 않습니다.",
                   "다음 세션 시가 진입, 진입일 포함 20번째 세션 종가 청산입니다. 2026년 가격은 읽지 않으며 해당 연도에 닿는 보유기간은 제외·집계합니다.",
                   "per_rebalance와 IC daily의 trade_date는 월말 의사결정일입니다. 비용은 실제 진입 시가·진입일·월말 ADV로 계산하며 세금에는 배수를 적용하지 않습니다.",
                   "벤치마크는 같은 체결 가능 유니버스의 비용 전 총수익입니다. seed=0인 200개 동일 수 비복원 대조군도 전액 왕복 비용을 부담합니다.",
                   "회전율 보조 결과는 직전 달의 체결 가능 2국면 종목 유지분에 비용 0을 적용합니다. 첫 달·관측 공백 이후는 전액 비용이며 비중 변화는 별도 과금하지 않습니다.",
                   "가격·업종은 현재 저장 버전입니다. 원주가 대체 및 후속 가격 부재 제외는 집계하며 실제 상장폐지를 독립 확인한 건수는 아닙니다.",
                   "용량은 HC001과 같은 p25 및 종목 수 × 최소 ADV × 1%입니다. 유동성 구간은 구간 내부 유니버스와 비교합니다. 연도별 집계는 의사결정 연도입니다.",
               ]}
    experiment_registry.record_trial(EXPERIMENT_ID, "phase2_ew", inputs, verdict, repo_root=repo_root, note="fixed primary; monthly excess with full round-trip costs")
    experiment_registry.record_trial(EXPERIMENT_ID, "score_ic", ic, "diagnostic", repo_root=repo_root, note="preregistered secondary; no independent pass judgement")
    _write_results(payload, repo_root=repo_root)
    return payload


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    sessions = hc001._session_calendar()
    planned, excluded = rebalance_calendar(sessions)
    price_files = hc001.d001._price_files(Path(data_dir) / "market/krx_daily", hc001.PRICE_START, hc001.PRICE_END, repo_root=repo_root)
    days = pd.DatetimeIndex([day for day, _ in price_files])
    covered, incomplete = [], []
    for row in planned:
        position = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        required = sessions[position - 19:position + 1 + HORIZON]
        (covered if required.difference(days).empty else incomplete).append(row["decision_date"])
    financials, _ = hc001._financial_inventory(data_dir)
    profiles = hc001._industries(data_dir)
    return {"experiment_id": EXPERIMENT_ID, "variants": list(VARIANTS), "data_dir": str(Path(data_dir)),
            "planned_months": len(MONTHS), "guarded_months": len(planned), "excluded_months": excluded,
            "holdout_horizon_exclusions": sum(row["reason"] == "holdout_horizon" for row in excluded),
            "covered_months": covered, "incomplete_price_months": incomplete,
            "read_start": "2015-01-01", "read_end": "2025-12-31", "price_files": len(price_files),
            "first_price_file": days.min().date().isoformat() if len(days) else None,
            "last_price_file": days.max().date().isoformat() if len(days) else None,
            "financials": financials, "industry_profiles": len(profiles)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print plan and local coverage only (default)")
    mode.add_argument("--execute", action="store_true", help="run and record the two preregistered local variants")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; months={result['variants']['phase2_ew']['excess']['count']}")
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run")
        print(f"Data directory: {plan['data_dir']}")
        print("Variants: " + ", ".join(plan["variants"]) + "; judged=phase2_ew; costs=full round trip; turnover=secondary only")
        print(f"Months: {plan['planned_months']} planned (2017-05..2025-11); {plan['guarded_months']} before 2026")
        print("Entry: next session open; exit: 20th session close (entry counts as day 1).")
        print(f"Holdout horizon exclusions: {plan['holdout_horizon_exclusions']}")
        print("Excluded months: " + json.dumps(plan["excluded_months"]))
        print(f"Guarded price span: {plan['read_start']}..{plan['read_end']}; files={plan['price_files']}; first={plan['first_price_file']}; last={plan['last_price_file']}")
        print(f"Months with price-file coverage (20 through decision + 20 holding sessions): {len(plan['covered_months'])}/{plan['guarded_months']}")
        print("Incomplete price months: " + ", ".join(plan["incomplete_price_months"]))
        financials = plan["financials"]
        print(f"Quarterly directory: {financials['directory']}; exists={financials['directory_exists']}")
        print(f"Quarterly archives: {len(financials['quarters'])}; rows through 2025={financials['stored_rows_through_2025']}")
        print("Missing quarterly archives: " + ", ".join(financials["missing_quarters"]))
        print("Empty quarterly archives: " + ", ".join(financials["empty_quarters"]))
        print(f"Industry profiles: {plan['industry_profiles']}; per-universe exclusion checked on execute (80% threshold).")
        print("Coverage is an archive snapshot, not proof of complete collection or computable signals. No returns evaluated; no registry rows or result files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
