"""AV001 avoidance portfolios. Offline plan by default; holdout is explicit."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import platform
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, holdout, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001, hc002, hc003, hf001, ordering
from src.ingestion import dart_financial_date_rules as financial_dates
from src.ingestion import dart_listing
from src.runner import trading_calendar


EXPERIMENT_ID = "AV001_avoidance_portfolio"
VARIANTS = ("av_all", "av_phase", "av_conc")
RULES = ("S", "O")
N_CONTROLS = 200
MAX_RSS_BYTES = 7_000_000_000
AUM_SIZES = (1e6, 1e7, 1e8, 1e9, 1e10)
INTERPRETATIONS = [
    "Actual dates gate financial values before derivation; S/O use the unchanged CFS/OFS ranking, HC002 eligibility and HC001 phase priority. The full merged listing is scanned for date provenance and L, including later dates; this metadata scan never evaluates protected returns.",
    "The sample for correction-link availability is stage-eligible stored versions known under at least one rule by the final decision. Counts are by receipt year; fiscal quarter and both public dates are retained in version provenance.",
    "F_raise uses the HF001 title policy and max(list date, receipt-prefix date), identical under S/O. Every calendar day in every 126-session window must be complete; incomplete coverage stops av_all/av_conc for the entire run, leaving Holm(3).",
    "Each variant/rule/control type starts default_rng(0). Draw order is month then path, with sorted codes. The preregistration fixes seed/candidates but does not fix path-major draw order. Loss reruns reset the same generators and repeat the policy with their own resulting holdings. Vectorised paths preserve scalar float sums and cash-update order; path means use the same reduction as actual means so exact ties count as >=.",
    "The primary control uses pre-trade H intersect prior positive holdings for r_t, caps retention, fills fresh names first, then unused K. Its allocation denominator is its own executable target count. The HC003 secondary uses actual post-trade retained_count and actual n, with no refill from unused K; it is never judged.",
    "Empty H is a cash observation if U and portfolio value are valid. The YAML minimum's explicit monthly rule and section 6 require 36 valid months; the generic 300-event alternative does not add a stock-count gate.",
    "Calendar NW uses valid month pairs at exact calendar lags 1/2/3, divisor N and no finite-sample correction. Undefined SE or fewer than 36 months gives Holm input 1. The 2025 sign and subgroup results are report-only.",
    "Delisting changes are tested after per-rule NW/Holm/control judgement. Coverage-stopped variants remain data_incomplete. Other combined non-pass priority is sensitivity_unavailable, delisting_sensitive, one-rule pass/version_sensitive, insufficient, risk_only, fail; both rules must pass. Correction-link availability is checked separately for design and holdout; an unavailable original-link sensitivity also marks the overall run sensitivity_unavailable.",
    "Market/liquidity diagnostics rerun inside each subset, with volatility ranks frozen from full U. HC003 t_hold comparison reuses the same rule-specific U and accounting, rather than mixing different financial date rules.",
    "Capacity reports actual-trade ADV 1% limits and nearest-share order rounding at initial KRW AUM 1/10/100/1000/10000 million (half shares round up). Rounded order cash shortfalls are reported, never hidden by extra scaling; these are diagnostics, not judged portfolios.",
    "Holdout uses the saved November states/RNG and December close values. Its first benchmark is December U weighted at the December close; strategy and controls retain the opening gap. Six valid months and positive design-only effects are required, without a t/Holm design gate.",
    "The latest design attempt must have three rows from one file. Holdout terminal rows name that design file. LH002 checks AV001 and EV001 under the same nonblocking execution lock as holdout claims; partial attempts and stale terminal rows do not release it.",
]


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (registration.fields["variants_planned"] != 3 or not registration.fields["uses_holdout"]
            or registration.fields["uses_llm"]):
        raise ValueError("AV001 requires its three fixed variants, holdout and no LLM")
    for experiment in (hc003.EXPERIMENT_ID, hf001.EXPERIMENT_ID, hc002.EXPERIMENT_ID):
        experiment_registry.verify_preregistration(experiment, repo_root)
    return registration


def _check_memory():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    if peak > MAX_RSS_BYTES:
        raise MemoryError(f"AV001 exceeded its 7 GB peak RSS budget: {peak} bytes")


def newey_west(months, values):
    months = pd.PeriodIndex(months, freq="M")
    values = np.asarray(values, dtype=float)
    if len(months) != len(values) or months.has_duplicates or months.hasnans or not np.isfinite(values).all():
        raise ValueError("calendar NW requires unique months and finite paired observations")
    result = hc003.newey_west([])
    n = len(values)
    mean = float(values.mean()) if n else None
    result.update(count=n, mean=mean, std=float(values.std(ddof=1)) if n > 1 else None,
                  calendar_lags=True, lag_pair_counts={str(k): 0 for k in (1, 2, 3)})
    if n > 1:
        centered = values - mean
        positions = {month.ordinal: pos for pos, month in enumerate(months)}
        variance = float(np.dot(centered, centered) / n)
        for lag in (1, 2, 3):
            pairs = [(pos, positions[month.ordinal - lag]) for pos, month in enumerate(months)
                     if month.ordinal - lag in positions]
            result["lag_pair_counts"][str(lag)] = len(pairs)
            variance += 2 * (1 - lag / 4) * sum(centered[a] * centered[b] for a, b in pairs) / n
        result["long_run_variance"] = float(variance)
        if variance > 0 and np.ptp(values) > 0:
            se = math.sqrt(variance / n)
            statistic = mean / se
            result.update(standard_error=se, t_stat=statistic,
                          p_one_sided=0.5 * math.erfc(statistic / math.sqrt(2)))
    return result


def holm(p_values):
    if set(p_values) != set(VARIANTS):
        raise ValueError("AV001 Holm family must contain exactly three variants")
    corrected = hc003.holm(dict(zip(hc003.VARIANTS, (p_values[name] for name in VARIANTS))))
    return dict(zip(VARIANTS, (corrected[name] for name in hc003.VARIANTS)))


def target_set(variant, selection, held):
    u = selection["universe"]
    phases = set(u.index[u.phase.isin((3, 4))])
    filters = selection["F_vol"] | selection["F_raise"]
    if variant == "av_all":
        return set(u.index) - phases - filters
    if variant == "av_phase":
        return set(u.index) - phases
    if variant == "av_conc":
        return set(u.index[u.phase.eq(2)]) - filters
    raise ValueError("unknown AV001 variant")


def observation_validity(selection, ret, benchmark):
    return bool(ret is not None and benchmark is not None and np.isfinite(ret) and np.isfinite(benchmark))


def random_target(universe, previous, size, retained, rng):
    u, previous = set(universe), set(previous)
    k = u & previous
    keep = set(rng.choice(sorted(k), size=min(retained, len(k), size), replace=False).tolist())
    fresh = sorted(u - previous)
    keep.update(rng.choice(fresh, size=min(size - len(keep), len(fresh)), replace=False).tolist())
    available = sorted(k - keep)
    adjustment = min(size - len(keep), len(available))
    keep.update(rng.choice(available, size=adjustment, replace=False).tolist())
    if len(keep) != min(size, len(u)):
        raise ValueError("fill-first control failed its registered target-size check")
    return keep, adjustment, size - len(keep)


def coverage_plan(data_dir, planned, sessions):
    status = dart_listing.listing_status(data_dir, experiment_id=EXPERIMENT_ID)
    files = dart_listing.listing_paths(data_dir)
    months = [{**{key: row[key] for key in ("month", "decision_date", "trade_date", "end_date", "end_kind")},
               "listing": hf001.listing_coverage(row["decision_date"], sessions, files,
                data_dir=data_dir, status=status)} for row in planned]
    return status, months


def _selections(prices, planned, versions, events, coverage, *, data_dir, repo_root, sessions):
    result, counts = [], []
    available = common._sessions(prices)
    with common.guarded_reads(EXPERIMENT_ID):
        for row in planned:
            day = pd.Timestamp(row["decision_date"])
            holdout.guard_period(day.date(), day.date(), experiment_id=EXPERIMENT_ID, repo_root=repo_root)
            pos = sessions.get_loc(day)
            required = sessions[max(0, pos - 19):pos + 1]
            if pos < 19 or len(required.difference(available)):
                raise ValueError(f"incomplete HC002 price history on {day.date()}")
            history = prices.loc[prices.index.get_level_values("trade_date").isin(required)]
            u, report = hc002.universe_on_decision(history, day, data_dir=data_dir, repo_root=repo_root,
                                                  financial_loader=versions.load)
            u = u.sort_index()
            if len(u):
                volatility = hf001.volatility_filter(prices, u, day, sessions)
                f_vol = set(volatility.index[volatility.f_vol])
                uncomputable = int(volatility.volatility_60d.isna().sum())
            else:
                f_vol, uncomputable = set(), 0
            known = events.loc[events.known_position.between(pos - 125, pos)]
            f_raise = set(known.stock_code) & set(u.index)
            result.append({**row, "universe": u, "F_vol": f_vol, "F_raise": f_raise,
                           "markets": history.xs(day, level="trade_date").market.reindex(u.index),
                           "blocked_buys": set(), "filter_covered": True,
                           "listing": coverage[row["month"]], "vol_uncomputable_U": uncomputable})
            counts.append({**report, "decision_date": row["decision_date"]})
    return result, counts


def prepare(*, data_dir, repo_root, sessions, planned, stage, prices=None, end=None, checkpoint_close=None):
    custom_end = end is not None
    end = (hc003.HOLDOUT_END if stage == "holdout" else hc003.DESIGN_END) if end is None else pd.Timestamp(end)
    checkpoint_close = (hc003.DESIGN_END if stage == "holdout" else None) if checkpoint_close is None else pd.Timestamp(checkpoint_close)
    status, coverage = coverage_plan(data_dir, planned, sessions)
    by_month = {row["month"]: row["listing"] for row in coverage}
    dates = financial_dates.FilingDates.from_data(data_dir)
    versions = {rule: financial_dates.FinancialVersions(data_dir, dates, rule, stage,
                sample_end=planned[-1]["decision_date"], cutoff=end + pd.Timedelta(days=1) if custom_end else None) for rule in RULES}
    event_listings = dates.event_listings()
    event_listings = event_listings.loc[event_listings.rcept_dt < end.strftime("%Y%m%d")]
    events, event_counts = hf001.rights_events(event_listings, sessions)
    selections, counts, paths = {r: [] for r in RULES}, {r: [] for r in RULES}, []
    days = sessions[sessions <= end]
    def windows():
        tail = None
        for quarter in sorted(set(days.to_period("Q"))):
            yearly = days[days.to_period("Q") == quarter]
            if prices is None:
                frame, files = hc003.load_prices(data_dir, yearly[0], yearly[-1],
                    repo_root=repo_root, experiment_id=EXPERIMENT_ID)
                paths.extend(files)
            else:
                frame = prices.loc[prices.index.get_level_values("trade_date").isin(yearly)]
                if frame.empty:
                    continue
            common.guard_prices(frame, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
            combined = frame if tail is None else pd.concat([tail, frame]).sort_index()
            local_plan = [row for row in planned if pd.Timestamp(row["decision_date"]).to_period("Q") == quarter]
            for rule in RULES:
                chosen, reports = _selections(combined, local_plan, versions[rule], events, by_month,
                    data_dir=data_dir, repo_root=repo_root, sessions=sessions)
                selections[rule].extend(chosen)
                counts[rule].extend(reports)
            recent = common._sessions(combined)[-60:]
            tail = combined.loc[combined.index.get_level_values("trade_date").isin(recent)].copy()
            yield frame
            _check_memory()
    rebalances = [row["trade_date"] for row in planned] + [row["end_date"] for row in planned if row["end_kind"] == "open"]
    book = hc003.PriceBook.from_windows(windows(), days, rebalances, end,
        experiment_id=EXPERIMENT_ID, repo_root=repo_root,
        close_dates=[checkpoint_close] if checkpoint_close is not None else ())
    if any(len(selections[r]) != len(planned) for r in RULES):
        raise ValueError("missing decision months; continuous portfolio evaluation cannot drop them")
    if checkpoint_close is not None:
        for rule in RULES:
            selections[rule][0].update(benchmark_start_date=checkpoint_close.date().isoformat(), benchmark_start_kind="close")
    return {"book": book, "selections": selections, "counts": counts, "dates": dates,
            "versions": versions, "price_paths": paths, "listing_status": status,
            "coverage": coverage, "event_counts": event_counts,
            "raise_complete": all(row["listing"]["covered"] for row in coverage)}


def _simulate(book, selections, variant, **kwargs):
    return hc003.simulate(book, selections, variant, experiment_id=EXPERIMENT_ID, variant_names=VARIANTS,
                          target_set_fn=target_set, observation_validity=observation_validity, **kwargs)


def summary(rows):
    valid = [row for row in rows if row["valid"]]
    result = hc003._monthly_summary([])
    result.update(primary=newey_west([row["month"] for row in valid], [row["excess"] for row in valid]),
                  mean_return=hc003._mean([row["return"] for row in valid]),
                  mean_benchmark=hc003._mean([row["benchmark"] for row in valid]),
                  accounting={key: hc003._mean([row[key] for row in rows]) for key in result["accounting"]},
                  buy_cost=sum(row["buy_cost"] for row in rows), sell_cost=sum(row["sell_cost"] for row in rows),
                  risk={name: hf001.risk_metrics([row[key] for row in valid])
                        for name, key in (("strategy", "return"), ("benchmark", "benchmark"))})
    return result


def controls(book, selections, variant, actual, *, loss=False, saved=None, secondary=False):
    codes = sorted(book.closings)
    positions = {code: pos for pos, code in enumerate(codes)}
    cash, quantities = np.ones(N_CONTROLS), np.zeros((N_CONTROLS, len(codes)))
    rng = np.random.default_rng(0)
    if saved is not None:
        if len(saved["cash"]) != N_CONTROLS:
            raise ValueError("saved control path count differs")
        cash = np.array(saved["cash"], dtype=float)
        for pos, code in enumerate(saved["codes"]):
            if code not in positions:
                raise ValueError("saved control holding lacks a carried price")
            quantities[:, positions[code]] = np.asarray(saved["quantities"])[:, pos]
        rng.bit_generator.state = saved["rng_state"]
    observations, accounting_rows, adjustments, shortages = [], [], [], []
    for number, selection in enumerate(selections):
        row = actual["per_month"][number]
        targets = np.zeros_like(quantities, dtype=bool)
        adjustment, shortage = np.zeros(N_CONTROLS, dtype=int), np.zeros(N_CONTROLS, dtype=int)
        for path in range(N_CONTROLS):
            previous = [codes[pos] for pos in np.flatnonzero(quantities[path] > 0)]
            if secondary:
                chosen = hc003.random_target(selection["universe"].index, previous, len(row["H"]), row["retained_count"], rng)
                shortage[path] = max(0, len(row["H"]) - len(chosen))
            else:
                chosen, adjustment[path], shortage[path] = random_target(selection["universe"].index, previous,
                    len(row["H"]), row["target_retained_count"], rng)
            targets[path, [positions[code] for code in chosen]] = True
        quotes = book.quotes(selection["trade_date"], loss=loss)
        holdout.guard_period(pd.Timestamp(selection["trade_date"]).date(), pd.Timestamp(selection["end_date"]).date(),
                             experiment_id=EXPERIMENT_ID, repo_root=book.repo_root)
        cash, quantities, report = hc003.rebalance_paths(cash, quantities, codes, quotes, targets,
            selection["trade_date"], allocation_n=np.full(N_CONTROLS, row["n"]) if secondary else None)
        beginning = np.asarray(saved["terminal_values"]) if number == 0 and saved is not None else report["pre_value"]
        end_marks = book.marks(selection["end_date"], selection["end_kind"], loss=loss)
        if any(code not in end_marks and (quantities[:, pos] > 0).any() for pos, code in enumerate(codes)):
            raise ValueError("control holding lacks a terminal valuation")
        ending = cash + hc003.path_sums(quantities * np.array([end_marks.get(code, 0.0) for code in codes]))
        returns = np.divide(ending, beginning, out=np.full_like(ending, np.nan), where=beginning > 0) - 1
        benchmark = hc003.benchmark_return(book, selection, loss=loss)
        excess = returns - benchmark if benchmark is not None else np.full_like(returns, np.nan)
        observations.append(excess)
        held = np.array([code in row["holdings"] for code in codes])
        overlap = (quantities[:, held] > 0).sum(axis=1) / held.sum() if held.any() else np.full(N_CONTROLS, np.nan)
        report["overlap"] = overlap
        accounting_rows.append(report)
        adjustments.append(adjustment)
        shortages.append(shortage)
    excess = np.array(observations)
    valid_counts = np.isfinite(excess).sum(axis=0)
    means = np.array([float(path[np.isfinite(path)].mean()) if count else np.nan
                      for path, count in zip(excess.T, valid_counts)])
    finite_means = [float(mean) for mean in means if np.isfinite(mean)]
    comparison = signal_eval._control_summary(finite_means, summary(actual["per_month"])["primary"]["mean"])
    if len(finite_means) != N_CONTROLS:
        comparison["share_of_controls"] = None
    keys = ("buy_turnover", "sell_turnover", "buy_cost", "sell_cost", "cost_fraction", "cash_weight", "frozen_weight", "holdings_count", "overlap",
            "band_skipped_buy_value", "band_skipped_sell_value")
    def finite_mean(array):
        valid = np.asarray(array)[np.isfinite(array)]
        return float(valid.mean()) if len(valid) else None
    per_month = [{"month": selection["month"], "strategy": {key: actual["per_month"][pos].get(key) for key in keys},
                  "control_mean": {key: finite_mean(accounting_rows[pos][key]) for key in keys},
                  "band_skipped_by_path": {side: accounting_rows[pos]["band_skipped_" + side + "_value"].tolist()
                                           for side in ("buy", "sell")},
                  "executable_denominators": accounting_rows[pos]["n"].astype(int).tolist(),
                  "target_count_adjustments": adjustments[pos].tolist(), "target_shortages": shortages[pos].tolist()}
                 for pos, selection in enumerate(selections)]
    terminal = book.marks(book.end, "close", loss=loss)
    terminal_values = cash + hc003.path_sums(quantities * np.array([terminal.get(code, 0.0) for code in codes]))
    return {**comparison, "path_means": [float(x) if np.isfinite(x) else None for x in means],
            "path_valid_months": valid_counts.tolist(), "per_month": per_month,
            "accounting_mean": {key: finite_mean([row[key] for row in accounting_rows]) for key in keys},
            "target_count_adjustments": int(np.sum(adjustments)), "target_shortages": int(np.sum(shortages)),
            "seed": 0, "paths": N_CONTROLS, "draw_order": "month/path/stock_code",
            "used_for_judgement": not secondary,
            "checkpoint": {"codes": codes, "cash": cash.tolist(), "quantities": quantities.tolist(),
                           "terminal_values": terminal_values.tolist(), "rng_state": rng.bit_generator.state}}


def variant_metrics(book, selections, variant, *, loss=False, saved=None, diagnostics=True):
    runs = {}
    for key, (version, multiplier) in hc003.SCENARIOS.items():
        initial = {} if saved is None else {"initial_state": saved["scenarios"][key]["state"],
                                            "initial_value": saved["scenarios"][key]["terminal_value"]}
        runs[key] = _simulate(book, selections, variant, loss=loss, model_version=version,
                               multiplier=multiplier, retain_details=key == "v2_1.0", **initial)
    main = runs["v2_1.0"]
    rows = main["per_month"]
    result = {**summary(rows), "per_month": rows,
        "cost_sensitivity": {str(m): summary(runs[f"v2_{m}"]["per_month"]) for m in (1.0, 1.5, 2.0)},
        "cost_months": {key: run["per_month"] for key, run in runs.items() if key != "v2_1.0"},
        "cost_model_v1": {"used_for_judgement": False, **summary(runs["v1_1.0"]["per_month"])},
        "random_control": controls(book, selections, variant, main, loss=loss,
                                    saved=saved["controls"] if saved is not None else None),
        "secondary_control": controls(book, selections, variant, main, loss=loss, secondary=True,
                                       saved=saved["secondary_controls"] if saved is not None else None),
        "checkpoint": {key: {"state": run["state"], "terminal_value": run["terminal_value"]} for key, run in runs.items()}}
    for period, start, end in (("design", "2017-05", "2024-12"), ("validation", "2025-01", "2025-11")):
        result[period] = summary([row for row in rows if start <= row["month"] <= end])
    design, validation = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
    result["validation_year_same_sign"] = bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None
    result["validation_sign_used_for_judgement"] = False
    if diagnostics:
        result["by_year"] = {year: summary([row for row in rows if row["month"].startswith(year)])
                             for year in sorted({row["month"][:4] for row in rows})}
        dated = pd.Series(rows, index=[row["decision_date"] for row in rows], dtype=object)
        result["short_sale_regimes"] = {name: summary(group.tolist())
            for name, group in hc003.market_regimes.split_by_regime(dated).items()}
    return result


def unavailable_metrics(reason, *, reasons=None):
    result = summary([])
    result.update(per_month=[], unavailable=reason, verdict=reason, reasons=reasons or [reason],
        cost_sensitivity={str(m): summary([]) for m in (1.0, 1.5, 2.0)},
        random_control={"share_of_controls": None}, design=summary([]), validation=summary([]))
    return result


def judge_variants(variants, fields, trials):
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trials > 20 else criteria["core_t_stat_gt"]
    sufficient = {name: result["primary"]["count"] >= 36 and result["primary"]["standard_error"] is not None
                  and result["primary"]["standard_error"] > 0 and result["primary"]["t_stat"] is not None
                  and not result.get("unavailable") for name, result in variants.items()}
    corrected = holm({name: variants[name]["primary"]["p_one_sided"] if sufficient[name] else 1.0 for name in VARIANTS})
    for name in VARIANTS:
        result = variants[name]
        primary = result["primary"]
        mean15 = result["cost_sensitivity"]["1.5"]["primary"]["mean"]
        share = result["random_control"]["share_of_controls"]
        result["holm_p"] = corrected[name]
        result["judgement_inputs"] = {**primary, "p_for_holm": primary["p_one_sided"] if sufficient[name] else 1.0,
            "holm_p": corrected[name], "t_threshold": float(threshold), "mean_at_cost_1_5": mean15,
            "random_control_share": share, "trial_count_after_run": trials,
            "design_mean_excess": result["design"]["primary"]["mean"]}
        if result.get("unavailable"):
            continue
        if not sufficient[name] or mean15 is None or share is None:
            result.update(verdict="insufficient", reasons=["36 valid months and defined SE/cost/control results required"])
            continue
        strategy, benchmark = result["risk"]["strategy"], result["risk"]["benchmark"]
        risk_only = (primary["mean"] <= 0 and strategy["volatility"] < benchmark["volatility"]
                     and strategy["lower_5pct_mean"] > benchmark["lower_5pct_mean"])
        failures = []
        if primary["mean"] <= criteria["net_performance_gt"]:
            failures.append("mean e must exceed 0")
        if primary["t_stat"] <= threshold:
            failures.append(f"NW t must exceed {threshold:g}")
        if corrected[name] > 0.05:
            failures.append("Holm(3) one-sided p must be <=0.05")
        if share > criteria["random_control_top_percent"] / 100:
            failures.append("random share must be <=0.05")
        if mean15 <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
            failures.append("cost x1.5 mean e must exceed 0")
        verdict = "risk_only" if risk_only else "fail" if failures else "pass"
        result.update(verdict=verdict, reasons=failures if failures else ["all per-rule AV001 criteria satisfied"])
    return variants


def combine_verdict(s, o, *, sensitivity_available=True):
    if s == o == "data_incomplete":
        return "data_incomplete"
    if not sensitivity_available:
        return "sensitivity_unavailable"
    if "delisting_sensitive" in (s, o):
        return "delisting_sensitive"
    if s == o == "pass":
        return "pass"
    if (s == "pass") != (o == "pass"):
        return "version_sensitive"
    for status in ("data_incomplete", "holdout_insufficient", "insufficient", "risk_only"):
        if status in (s, o):
            return status
    return "fail"


def _pop_checkpoint(result):
    return {"scenarios": result.pop("checkpoint"),
            "controls": result["random_control"].pop("checkpoint"),
            "secondary_controls": result["secondary_control"].pop("checkpoint")}


def _compact_months(result):
    for row in result["per_month"]:
        for key in ("H", "T", "Z", "holdings", "trades"):
            row.pop(key, None)


def secondary_metrics(book, selections, variant, result, baseline, *, saved=None):
    baskets, exclusions, capacity_rows = [], [], []
    for selection, row in zip(selections, result["per_month"]):
        h = set(row["H"])
        u = selection["universe"]
        rejected = set(u.index) - h
        basket = {**selection, "universe": u.loc[sorted(rejected)]}
        basket_return = hc003.benchmark_return(book, basket)
        baskets.append({"month": row["month"], "excluded_count": len(rejected), "gross": basket_return,
            "universe_gross": row["benchmark"],
            "excess": basket_return - row["benchmark"] if basket_return is not None and row["benchmark"] is not None else None})
        sets = [set(u.index[u.phase.eq(phase)]) for phase in (3, 4)] + [selection["F_vol"], selection["F_raise"]]
        exclusions.append({"month": row["month"], "U": len(u), "H": len(h), "excluded_total": len(rejected),
            **dict(zip(("phase3", "phase4", "F_vol", "F_raise"), map(len, sets))),
            "overlap_names": sum(sum(code in group for group in sets) > 1 for code in u.index)})
        quotes = book.quotes(selection["trade_date"])
        limit = min((trade["adv"] * 0.01 / (trade["amount"] / row["pre_value"])
                     for trade in row["trades"] if trade["amount"] > 0 and row["pre_value"] > 0), default=None)
        rounded = {}
        for aum in AUM_SIZES:
            error, zeros, rounded_buy, rounded_sell, rounded_cost, adv_violations = 0.0, 0, 0.0, 0.0, 0.0, 0
            for trade in row["trades"]:
                amount = trade["amount"] * aum
                price = quotes[trade["stock_code"]].raw_open
                shares = math.floor(amount / price + 0.5)
                value = shares * price
                error += abs(value - amount)
                zeros += shares == 0
                adv_violations += value > trade["adv"] * 0.01
                cost = value * trade["cost"] / trade["amount"]
                rounded_cost += cost
                if trade["side"] == "buy":
                    rounded_buy += value + cost
                else:
                    rounded_sell += value - cost
            initial_cash = (row["cash_weight"] * row["pre_value"] + row["buy_amount"] + row["buy_cost"]
                            - row["sell_amount"] + row["sell_cost"]) * aum
            rounded[str(int(aum))] = {"zero_share_orders": zeros, "absolute_notional_error": error,
                "error_fraction_of_value": error / (aum * row["pre_value"]) if row["pre_value"] > 0 else None,
                "rounded_cost": rounded_cost, "cash_shortfall": max(0.0, rounded_buy - rounded_sell - initial_cash),
                "adv_1pct_violations": adv_violations}
        capacity_rows.append({"month": row["month"], "aum_at_1pct_adv": limit, "nearest_share": rounded})
    result["excluded_basket"] = {"per_month": baskets, "mean_excess": hc003._mean([r["excess"] for r in baskets])}
    result["exclusion_counts"] = exclusions
    result["capacity"] = {"initial_aum_krw": list(AUM_SIZES), "per_month": capacity_rows,
        "minimum_aum_at_1pct_adv": min((r["aum_at_1pct_adv"] for r in capacity_rows if r["aum_at_1pct_adv"] is not None), default=None)}
    prior = {row["month"]: row for row in baseline["per_month"]}
    comparisons = [{"month": row["month"], "av_excess": row["excess"], "hc003_t_hold_excess": prior[row["month"]]["excess"],
                    "net_difference": row["return"] - prior[row["month"]]["return"]
                    if row["valid"] and prior[row["month"]]["valid"] else None} for row in result["per_month"]]
    result["hc003_t_hold"] = {"per_month": comparisons, "mean_net_difference": hc003._mean([r["net_difference"] for r in comparisons])}
    # Selection-date market metadata comes from the decision price row, not a
    # future market classification. It is retained on each selection below.
    market_runs, checkpoints = {}, {}
    def subset_run(name, selected):
        initial = {} if saved is None else {"initial_state": saved[name]["state"],
                                            "initial_value": saved[name]["terminal_value"]}
        run = _simulate(book, selected, variant, retain_details=False, **initial)
        checkpoints[name] = {"state": run["state"], "terminal_value": run["terminal_value"]}
        return summary(run["per_month"])
    for market in ("KOSPI", "KOSDAQ"):
        selected = [{**row, "universe": row["universe"].loc[row["markets"].eq(market).reindex(row["universe"].index).fillna(False)]}
                    for row in selections]
        market_runs[market] = subset_run("market_" + market, selected)
    buckets, lower = {}, 0.0
    for number, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
        selected = [{**row, "universe": row["universe"].loc[
            row["universe"].avg_trading_value_20d.ge(lower) & row["universe"].avg_trading_value_20d.lt(upper)]} for row in selections]
        buckets[str(number)] = {**subset_run("liquidity_" + str(number), selected),
                               "lower_inclusive": lower, "upper_exclusive": upper if np.isfinite(upper) else None}
        lower = upper
    result.update(markets=market_runs, liquidity_buckets=buckets)
    return checkpoints


def input_price_paths(data_dir, stage):
    """Inventory only: protected contents are hashed only in a session."""
    end = "20251230" if stage == "design_validation" else "20260630"
    return sorted((pd.Timestamp(p.stem), p) for p in (Path(data_dir) / "market/krx_daily").glob("*/*.jsonl")
                  if len(p.stem) == 8 and p.stem.isdigit() and "20150101" <= p.stem <= end)


def source_hashes(data_dir, price_paths, *, stage, prices=None, frozen_prices=None, repo_root=PROJECT_ROOT):
    root = Path(data_dir)
    groups = {"prices": [path for _, path in price_paths],
        "financials": sorted(path for path in (root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")
                             if int(path.stem[:4]) <= (2025 if stage == "design_validation" else 2026)),
        "companies": [root / "reference/dart_company/companies.jsonl"],
        "main_listing": sorted((root / "disclosures/dart_full/list").glob("*/*.jsonl")),
        "supplement": sorted((root / "disclosures/dart_full/list_supplement").rglob("*.jsonl")),
        "coverage_metadata": [root / "reference/corp_codes.csv", root / "disclosures/dart_full/_state.json",
                              root / "disclosures/dart_full/list_supplement/_state.json"]}
    # Missing profiles follow HC002's registered financial-industry policy.
    result = {name: {str(path.relative_to(root)): hc003._digest(path) for path in paths if path.is_file()}
              for name, paths in groups.items() if name != "prices"}
    result["prices"] = {}
    for day, path in price_paths:
        key = path.relative_to(root).as_posix()
        if holdout.HOLDOUT_START <= day.date() <= holdout.HOLDOUT_END:
            if frozen_prices is not None:
                if key not in frozen_prices:
                    raise ValueError("protected price is absent from the frozen target scan: " + key)
                result["prices"][key] = frozen_prices[key]
                continue
            session = next((s for s in holdout._SESSIONS.get() if s._open and s.experiment_id == EXPERIMENT_ID
                            and s.repo_root == Path(repo_root).resolve()), None)
            if session is None:
                raise ValueError("protected price hashing requires an open AV001 HoldoutSession")
            session.guard_period(day.date(), day.date())
        else:
            holdout.guard_period(day.date(), day.date(), experiment_id=EXPERIMENT_ID, repo_root=repo_root)
        result["prices"][key] = hc003._digest(path)
    sources = (Path(__file__), Path(hc003.__file__), Path(hc002.__file__), Path(hc001.__file__), Path(hf001.__file__),
        Path(common.__file__), Path(ordering.__file__), Path(financial_dates.__file__), Path(dart_listing.__file__),
        Path(hc003.dart_quarterly.__file__), PROJECT_ROOT / "src/ingestion/dart_filing_dates.py",
        Path(cost_model.__file__), Path(holdout.__file__), Path(experiment_registry.__file__),
        Path(hc003.capacity.__file__), PROJECT_ROOT / "src/utils/stock_codes.py",
        Path(hc003.krx_market.__file__), Path(signal_eval.__file__), Path(hc003.market_regimes.__file__),
        PROJECT_ROOT / "src/runner/trading_calendar.py", PROJECT_ROOT / "src/ingestion/storage.py", Path(hc001.industry_map.__file__))
    result["code"] = {str(path.relative_to(PROJECT_ROOT)): hc003._digest(path) for path in sources}
    if prices is not None:
        result["in_memory_prices"] = hc003.source_hashes(data_dir, [], {}, prices=prices)["in_memory_prices"]
    return result


def holdout_input_hashes(data_dir, repo_root, *, before_open=False):
    frozen = None
    if before_open:
        state = dart_listing._state(dart_listing.directory(data_dir) / "list_supplement/_state.json")
        if state is not None:
            frozen = dart_listing.verified_target_scan(Path(data_dir), state)["price_files"]
        else:
            frozen = {}  # A protected file without frozen provenance is rejected.
    return source_hashes(data_dir, input_price_paths(data_dir, "holdout"), stage="holdout",
                         frozen_prices=frozen, repo_root=repo_root)


def replay_hash(payload):
    """HEAD is provenance; content hashes and deterministic results define replay."""
    return hc003._content_hash({k: v for k, v in payload.items()
                               if k not in ("code_commit", "performance", "replay_sha256")})


def sensitivity_reasons(versions):
    return {r: (["Corrections in the stage sample have zero successful original-filing links; "
                 "rule O sensitivity cannot be computed."] if r == "O" and not versions[r].sensitivity_available else [])
            for r in RULES}


def _date_differences(prepared):
    months = []
    annotations = prepared["versions"]["S"].annotations
    for s, o in zip(prepared["selections"]["S"], prepared["selections"]["O"]):
        day = s["decision_date"]
        shifted = {row["corp_code"] for row in annotations if row["actual_public_date"] is not None
                   and row["actual_public_date"] < financial_dates.STAGE_CUTOFFS[prepared["versions"]["S"].stage]
                   and row["assumed_public_date"] < day <= row["actual_public_date"]}
        su, ou = s["universe"], o["universe"]
        changed = set(su.index) ^ set(ou.index)
        for code in set(su.index) & set(ou.index):
            for key in ("phase", "revenue_yoy", "operating_income_yoy", "rcept_no"):
                left, right = su.loc[code, key], ou.loc[code, key]
                if not (pd.isna(left) and pd.isna(right)) and (pd.isna(left) != pd.isna(right) or left != right):
                    changed.add(code)
                    break
        months.append({"month": s["month"], "date_shifted_companies": len(shifted),
                       "date_shifted_corp_codes": sorted(shifted), "changed_universe_or_signal_codes": sorted(changed)})
    return {"per_month": months, "company_months": sum(row["date_shifted_companies"] for row in months),
            "affected_companies": len({corp for row in months for corp in row["date_shifted_corp_codes"]}),
            "affected_months": sum(row["date_shifted_companies"] > 0 for row in months)}


def compute_design(*, data_dir, repo_root, sessions=None, prices=None, trials=None, _attempt=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    sessions = hc003.session_calendar() if sessions is None else signal_eval._dates(sessions).sort_values().unique()
    planned, excluded = hc003.rebalance_calendar(sessions)
    if not planned or planned[-1]["month"] != "2025-11":
        raise ValueError("design must end with the registered November state and December close")
    if excluded:
        raise ValueError("missing calendar months; AV001 cannot drop months")
    if prices is not None:
        if prices.empty or common._sessions(prices).max() > hc003.DESIGN_END:
            raise ValueError("design prices must end by 2025-12-30")
        common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    trading_calendar.preflight_price_sessions(data_dir,
        sessions[0] if prices is None else common._sessions(prices).min(),
        hc003.DESIGN_END if prices is None else common._sessions(prices).max(),
        price_days=None if prices is None else common._sessions(prices))
    paths = [] if prices is not None else hc003.price_files(data_dir, "20150101", "20251230",
                                                          repo_root=repo_root, experiment_id=EXPERIMENT_ID)
    hashes = source_hashes(data_dir, paths, stage="design_validation", prices=prices, repo_root=repo_root)
    if _attempt is not None:
        _attempt["source_hashes"] = hashes
    prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, planned=planned,
                       stage="design_validation", prices=prices)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3 if trials is None else trials
    rules, sensitivity, states, comparison_states = {}, {}, {}, {}
    available = prepared["versions"]["S"].sensitivity_available
    for rule in RULES:
        book, selections = prepared["book"], prepared["selections"][rule]
        variants, losses = {}, {}
        for variant in VARIANTS:
            if variant != "av_phase" and not prepared["raise_complete"]:
                variants[variant], losses[variant] = unavailable_metrics("data_incomplete"), unavailable_metrics("data_incomplete")
                continue
            if rule == "O" and not available:
                reasons = sensitivity_reasons(prepared["versions"])[rule]
                variants[variant] = unavailable_metrics("sensitivity_unavailable", reasons=reasons)
                losses[variant] = unavailable_metrics("sensitivity_unavailable", reasons=reasons)
                continue
            variants[variant] = variant_metrics(book, selections, variant)
            losses[variant] = variant_metrics(book, selections, variant, loss=True, diagnostics=False)
        judge_variants(variants, registration.fields, trials)
        judge_variants(losses, registration.fields, trials)
        hc003.apply_delisting_verdict(variants, losses)
        states[rule] = {}
        comparison_states[rule] = {}
        if any(not result.get("unavailable") for result in variants.values()):
            baseline = hc003.simulate(book, selections, "t_hold", experiment_id=EXPERIMENT_ID,
                                      variant_names=hc003.VARIANTS, retain_details=False)
            comparison_states[rule] = {"state": baseline["state"], "terminal_value": baseline["terminal_value"]}
        for variant in VARIANTS:
            if variants[variant].get("unavailable"):
                continue
            diagnostics = secondary_metrics(book, selections, variant, variants[variant], baseline)
            states[rule][variant] = _pop_checkpoint(variants[variant])
            states[rule][variant]["diagnostics"] = diagnostics
            _pop_checkpoint(losses[variant])
            _compact_months(losses[variant])
        rules[rule], sensitivity[rule] = variants, losses
    variants = {name: {"verdict": combine_verdict(rules["S"][name]["verdict"], rules["O"][name]["verdict"],
                                                sensitivity_available=available),
                       "rule_verdicts": {r: rules[r][name]["verdict"] for r in RULES}} for name in VARIANTS}
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "design_validation", "selected_variant": "av_all",
        "verdict": variants["av_all"]["verdict"] if available else "sensitivity_unavailable",
        "reasons": ["A variant must pass both S and O; all three are reported."],
        "prereg_commit": registration.commit_hash, "rules": rules, "variants": variants,
        "filing_rules": {r: prepared["versions"][r].report() for r in RULES}, "L": prepared["dates"].L,
        "sensitivity_unavailable_reasons": sensitivity_reasons(prepared["versions"]),
        "version_provenance": prepared["versions"]["S"].annotations, "date_rule_differences": _date_differences(prepared),
        "delisting_sensitivity": sensitivity, "design_states": states,
        "comparison_states": comparison_states,
        "design_effects": {r: {v: rules[r][v]["design"]["primary"]["mean"] for v in VARIANTS} for r in RULES},
        "state_sha256": hc003._content_hash(states), "state_decision_month": "2025-11", "state_valuation_date": "2025-12-30",
        "source_hashes": hashes, "listing_version": prepared["listing_status"]["listing_version"],
        "code_commit": experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
        "library_versions": {"python": platform.python_version(),
            **{name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy", "exchange-calendars", "PyYAML")}},
        "coverage": prepared["coverage"], "listing_status": prepared["listing_status"],
        "counts": {"by_rule": prepared["counts"], "rights_events": prepared["event_counts"], **prepared["book"].counts,
                   "financial_signal_missing_by_year": {r: hc003._financial_ratios(prepared["counts"][r]) for r in RULES}},
        "selections": {r: [{**{k: row[k] for k in ("month", "decision_date", "trade_date", "end_date", "end_kind")},
            "U": row["universe"].index.tolist(), "H": {v: sorted(target_set(v, row, ())) for v in VARIANTS},
            "F_vol": sorted(row["F_vol"]), "F_raise": sorted(row["F_raise"]), "vol_uncomputable_U": row["vol_uncomputable_U"]}
                           for row in prepared["selections"][r]] for r in RULES},
        "trial_count_after_run": trials, "interpretations": INTERPRETATIONS}
    final_paths = input_price_paths(data_dir, "design_validation") if prices is None else []
    if hashes != source_hashes(data_dir, final_paths, stage="design_validation", prices=prices, repo_root=repo_root):
        raise ValueError("input files changed during computation")
    payload["replay_sha256"] = replay_hash(payload)
    _check_memory()
    payload["performance"] = {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return payload


def registry_metrics(payload, *, design_result_file=None):
    stage = payload["stage"]
    metrics = {}
    for name, combined in payload["variants"].items():
        rules = {}
        for rule in RULES:
            result = payload["rules"][rule][name]
            rules[rule] = {"verdict": result["verdict"], "primary": result["primary"],
                "holm_p": result.get("holm_p"), "mean_at_cost_1_5": result.get("cost_sensitivity", {}).get("1.5", {}).get("primary", {}).get("mean"),
                "random_share": result.get("random_control", {}).get("share_of_controls"),
                "base_verdict": result.get("base_verdict"), "delisting_loss_verdict": result.get("delisting_loss_verdict")}
        metrics[name if stage == "design_validation" else f"holdout_{name}"] = {
            "stage": stage, "rules": rules, "combined_verdict": combined["verdict"],
            "state_sha256": payload.get("state_sha256"), "replay_sha256": payload.get("replay_sha256"),
            "design_result_file": design_result_file, "L": payload.get("L")}
    if "abort" in payload:
        for row in metrics.values():
            row.update(payload["abort"])
    return metrics


def publish(payload, *, repo_root, design_result_file=None, aborted_registration=None):
    names = set(payload["variants"])
    if (payload["stage"] not in ("design_validation", "holdout") or not names or not names.issubset(VARIANTS)
            or payload["stage"] == "design_validation" and names != set(VARIANTS)
            or set(payload["rules"]) != set(RULES)
            or any(set(payload["rules"][rule]) != names for rule in RULES)):
        raise ValueError("AV001 publication requires all three design variants and matching S/O summaries")
    metrics = registry_metrics(payload, design_result_file=design_result_file)
    for row in metrics.values():
        experiment_registry.encode_metrics(row)
    lines = [f"# {EXPERIMENT_ID}: {payload['stage']}", "", f"Verdict: {payload['verdict']}", "",
             "| Variant | S | O | Combined |", "| --- | --- | --- | --- |"]
    for name, result in payload["variants"].items():
        lines.append(f"| {name} | {result['rule_verdicts']['S']} | {result['rule_verdicts']['O']} | {result['verdict']} |")
    lines += ["", *INTERPRETATIONS]
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root, summary_lines=lines,
                                 aborted_registration=aborted_registration)
    result_file, checksum = str(paths[0].relative_to(repo_root)), hc003._digest(paths[0])
    for row in metrics.values():
        row.update(result_file=result_file, result_sha256=checksum)
    for row in metrics.values():
        experiment_registry.encode_metrics(row)
    for name, row in metrics.items():
        if aborted_registration is not None:
            experiment_registry.record_aborted_trial(EXPERIMENT_ID, name, row, row["combined_verdict"],
                preregistration=aborted_registration, repo_root=repo_root,
                note=f"AV001 {payload['stage']}; aborted attempt; compact summary")
        else:
            experiment_registry.record_trial(EXPERIMENT_ID, name, row, row["combined_verdict"], repo_root=repo_root,
                                             note=f"AV001 {payload['stage']}; S/O, calendar NW(3), Holm(3); compact summary")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, VARIANTS, repo_root) as attempt:
            result = compute_design(data_dir=data_dir, repo_root=repo_root, prices=prices, sessions=sessions, _attempt=attempt)
            return publish(result, repo_root=repo_root)


def saved_design(rows, *, repo_root, registration):
    run = ordering.design_run(rows, EXPERIMENT_ID, VARIANTS)
    path = (Path(repo_root) / run["result_file"]).resolve()
    directory = (Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results").resolve()
    if not path.is_relative_to(directory):
        raise ValueError("design result path is outside AV001 results")
    checksum = hc003._digest(path)
    relevant = [json.loads(row["metrics_json"]) for row in rows if row["experiment_id"] == EXPERIMENT_ID
                and row["variant"] in VARIANTS and json.loads(row["metrics_json"]).get("result_file") == run["result_file"]]
    if len(relevant) != 3 or any(row.get("result_sha256") != checksum for row in relevant):
        raise ValueError("design result hash differs from its three registry rows")
    design = common.read_result_json(path)
    if (design["stage"] != "design_validation" or design["prereg_commit"] != registration.commit_hash
            or design["state_decision_month"] != "2025-11" or design["state_valuation_date"] != "2025-12-30"
            or hc003._content_hash(design["design_states"]) != design["state_sha256"]
            or any(row.get("state_sha256") != design["state_sha256"] for row in relevant)):
        raise ValueError("saved financial-rule states/preregistration differ")
    passed = run["passing_variants"]
    if passed != [v for v in VARIANTS if design["variants"][v]["verdict"] == "pass"]:
        raise ValueError("registry passing set differs from design result")
    if any(design["rules"][r][v]["verdict"] != "pass" for r in RULES for v in passed):
        raise ValueError("holdout eligibility requires both rules to pass")
    return design, run


def verify_non_holdout(design, *, data_dir, repo_root, sessions):
    replay = compute_design(data_dir=data_dir, repo_root=repo_root, sessions=sessions,
                            trials=design["trial_count_after_run"])
    if replay["source_hashes"].get("code") != design["source_hashes"].get("code"):
        raise ValueError("non-holdout reproducibility mismatch: code hashes differ; "
                         "run a new design/validation trial with this code first; protected evaluation remains unopened")
    if replay["source_hashes"] != design["source_hashes"] or replay["replay_sha256"] != design["replay_sha256"]:
        raise ValueError("non-holdout reproducibility mismatch; protected evaluation was not opened")


def holdout_verdict(result, design_effect):
    primary = result["primary"]
    if primary["count"] != 6:
        return "holdout_insufficient", ["all six valid holdout months are required"]
    mean = primary["mean"]
    if design_effect is None or not np.isfinite(design_effect) or design_effect <= 0:
        return "fail", ["saved design-only mean is undefined or nonpositive"]
    if mean is None or mean <= 0 or np.sign(mean) != np.sign(design_effect):
        return "fail", ["holdout mean e must be positive and have the design direction"]
    if mean < design_effect * 0.5:
        return "fail", ["holdout effect must reach half its own rule's design-only effect"]
    return "pass", ["all per-rule AV001 section 8 conditions satisfied"]


def terminal_payload(status, reason, passed, *, design, registration):
    return {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "passed_design_variants",
        "prereg_commit": registration.commit_hash, "verdict": status, "reasons": [reason], "L": design["L"],
        "rules": {r: {v: {**unavailable_metrics(status), "reasons": [reason]} for v in passed} for r in RULES},
        "variants": {v: {"verdict": status, "rule_verdicts": {r: status for r in RULES}} for v in passed},
        "state_sha256": design["state_sha256"], "design_effects": design["design_effects"],
        "interpretations": INTERPRETATIONS}


def evaluate_holdout(prepared, design, passed):
    rules, losses = {}, {}
    available = prepared["versions"]["S"].sensitivity_available
    for rule in RULES:
        selections, book = prepared["selections"][rule], prepared["book"]
        rules[rule], losses[rule] = {}, {}
        if rule == "O" and not available:
            reasons = sensitivity_reasons(prepared["versions"])[rule]
            rules[rule] = {v: unavailable_metrics("sensitivity_unavailable", reasons=reasons) for v in passed}
            losses[rule] = {v: unavailable_metrics("sensitivity_unavailable", reasons=reasons) for v in passed}
            continue
        for v in passed:
            for destination, loss in ((rules[rule], False), (losses[rule], True)):
                result = variant_metrics(book, selections, v, loss=loss,
                                         saved=design["design_states"][rule][v])
                effect = design["design_effects"][rule][v]
                verdict, reasons = holdout_verdict(result, effect)
                result.update(verdict=verdict, reasons=reasons, design_mean_excess=effect)
                _pop_checkpoint(result)
                destination[v] = result
        hc003.apply_delisting_verdict(rules[rule], losses[rule])
        comparison = design["comparison_states"][rule]
        baseline = hc003.simulate(book, selections, "t_hold", experiment_id=EXPERIMENT_ID,
            variant_names=hc003.VARIANTS, initial_state=comparison["state"],
            initial_value=comparison["terminal_value"], retain_details=False)
        for v in passed:
            secondary_metrics(book, selections, v, rules[rule][v], baseline,
                              saved=design["design_states"][rule][v]["diagnostics"])
            _compact_months(losses[rule][v])
    variants = {v: {"verdict": combine_verdict(rules["S"][v]["verdict"], rules["O"][v]["verdict"],
                                             sensitivity_available=available),
                    "rule_verdicts": {r: rules[r][v]["verdict"] for r in RULES}} for v in passed}
    return rules, losses, variants


def run_holdout(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    with ordering.execution_lock(repo_root):
        registration = _registration(repo_root)
        used = hc003.ledger_experiments(repo_root)
        rows = hc003.registry_rows(repo_root)
        design, run = saved_design(rows, repo_root=repo_root, registration=registration)
        passed = run["passing_variants"]
        if not passed:
            return {"stage": "holdout", "verdict": "not_applicable", "variants": {},
                    "reasons": ["No variant passed both rules; holdout remains unopened."]}
        terminal = [row for row in rows if row["experiment_id"] == EXPERIMENT_ID
                    and json.loads(row["metrics_json"]).get("stage") == "holdout"]
        if EXPERIMENT_ID in used or terminal:
            raise ValueError("AV001 holdout used or terminally blocked; cannot reopen")
        if "LH002_llm_hegemony_judge" in used:
            payload = terminal_payload("holdout_contaminated", "LH002 is in the ledger; no protected returns read",
                                       passed, design=design, registration=registration)
            return publish(payload, repo_root=repo_root, design_result_file=run["result_file"])
        sessions = hc003.session_calendar()
        verify_non_holdout(design, data_dir=data_dir, repo_root=repo_root, sessions=sessions)
        planned, excluded = hc003.rebalance_calendar(sessions, holdout_mode=True)
        if len(planned) != 6 or excluded:
            raise ValueError("holdout calendar must contain the six registered observations")
        _, coverage = coverage_plan(data_dir, planned, sessions)
        if any(not row["listing"]["covered"] for row in coverage) and any(v != "av_phase" for v in passed):
            raise ValueError("data_incomplete: holdout F_raise listing windows are incomplete; no session opened")
        trading_calendar.preflight_price_sessions(data_dir, sessions[0], hc003.HOLDOUT_END)
        hashes = holdout_input_hashes(data_dir, repo_root, before_open=True)
        started = time.perf_counter()
        try:
            with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=repo_root):
                if holdout_input_hashes(data_dir, repo_root) != hashes:
                    raise ValueError("holdout inputs changed between pre-open freeze and session entry")
                _, verified_coverage = coverage_plan(data_dir, planned, sessions)
                if verified_coverage != coverage:
                    raise ValueError("holdout coverage changed after input freeze")
                prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, planned=planned, stage="holdout")
                rules, losses, variants = evaluate_holdout(prepared, design, passed)
                book = prepared["book"]
                if holdout_input_hashes(data_dir, repo_root) != hashes:
                    raise ValueError("holdout inputs changed during computation")
                if coverage_plan(data_dir, planned, sessions)[1] != coverage:
                    raise ValueError("holdout coverage changed during computation")
            headline = "av_all" if "av_all" in passed else passed[0]
            payload = {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "passed_design_variants",
                "verdict": variants[headline]["verdict"], "reasons": ["Final pass requires both rule-specific holdout passes and available original-link sensitivity."],
                "variants": variants, "rules": rules, "delisting_sensitivity": losses,
                "design_effects": design["design_effects"], "state_sha256": design["state_sha256"],
                "prereg_commit": registration.commit_hash, "L": prepared["dates"].L,
                "filing_rules": {r: prepared["versions"][r].report() for r in RULES},
                "sensitivity_unavailable_reasons": sensitivity_reasons(prepared["versions"]),
                "version_provenance": prepared["versions"]["S"].annotations,
                "date_rule_differences": _date_differences(prepared),
                "source_hashes": hashes, "input_hash_verification": "pre_open == session_before == session_after",
                "listing_version": prepared["listing_status"]["listing_version"],
                "code_commit": experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
                "design_code_commit": design["code_commit"], "library_versions": design["library_versions"],
                "counts": {"by_rule": prepared["counts"], "rights_events": prepared["event_counts"], **book.counts},
                "coverage": coverage, "interpretations": INTERPRETATIONS,
                "performance": {"runtime_seconds": time.perf_counter() - started,
                    "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
            return publish(payload, repo_root=repo_root, design_result_file=run["result_file"])
        except BaseException as exc:
            if EXPERIMENT_ID not in hc003.ledger_experiments(repo_root):
                raise
            payload = terminal_payload("holdout_aborted", f"{type(exc).__name__}: {exc}", passed,
                                       design=design, registration=registration)
            payload.update(source_hashes=hashes, input_hash_verification="aborted; inputs not verified")
            payload["abort"] = ordering.abort_details(hashes, exc)
            publish(payload, repo_root=repo_root, design_result_file=run["result_file"], aborted_registration=registration)
            raise


def rehearsal_window(start="2025-01-01", end="2025-06-30"):
    """Only complete historical six-month windows, never protected returns."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if (pd.isna(start) or pd.isna(end) or start.tzinfo is not None or end.tzinfo is not None
            or start != start.normalize() or end != end.normalize()
            or start.day != 1 or end != end + pd.offsets.MonthEnd(0)
            or end.to_period("M").ordinal - start.to_period("M").ordinal != 5
            or start < pd.Timestamp("2017-07-01") or end >= pd.Timestamp("2026-01-01")):
        raise ValueError("rehearsal requires six complete unprotected months between 2017-07-01 and 2025-12-31")
    sessions = trading_calendar.session_calendar("2015-01-01", end)
    return start, end, sessions


def rehearse_holdout(start="2025-01-01", end="2025-06-30", *,
                     data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    """Rebuild prior checkpoints, then exercise the shared holdout evaluator.

    No ledger session, result publication or registry writes are permitted here.
    All variants are exercised regardless of historical pass/fail eligibility.
    """
    start, end, sessions = rehearsal_window(start, end)
    _registration(repo_root)
    preflight = trading_calendar.preflight_price_sessions(data_dir, sessions[0], end)
    prior_close = sessions[sessions < start][-1]
    end_close = sessions[-1]
    training_months = pd.period_range("2017-05", start.to_period("M") - 2, freq="M")
    planned, excluded = hc003.rebalance_calendar(sessions, months=training_months, end=prior_close)
    if excluded:
        raise ValueError("rehearsal checkpoint calendar is incomplete")
    training = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, planned=planned,
                       stage="design_validation", end=prior_close)
    if not training["raise_complete"] or not training["versions"]["S"].sensitivity_available:
        raise ValueError("rehearsal checkpoint listing/original-link coverage is incomplete")
    design = {"design_states": {}, "comparison_states": {}, "design_effects": {}}
    for rule in RULES:
        book, selections = training["book"], training["selections"][rule]
        baseline = hc003.simulate(book, selections, "t_hold", experiment_id=EXPERIMENT_ID,
                                  variant_names=hc003.VARIANTS, retain_details=False)
        design["comparison_states"][rule] = {"state": baseline["state"], "terminal_value": baseline["terminal_value"]}
        design["design_states"][rule], design["design_effects"][rule] = {}, {}
        for variant in VARIANTS:
            result = variant_metrics(book, selections, variant)
            diagnostics = secondary_metrics(book, selections, variant, result, baseline)
            design["design_states"][rule][variant] = _pop_checkpoint(result)
            design["design_states"][rule][variant]["diagnostics"] = diagnostics
            design["design_effects"][rule][variant] = result["primary"]["mean"]
    del training, book, baseline, result
    months = pd.period_range(start.to_period("M") - 1, end.to_period("M") - 1, freq="M")
    planned, excluded = hc003.rebalance_calendar(sessions, months=months, end=end_close)
    if len(planned) != 6 or excluded:
        raise ValueError("rehearsal must contain six monthly observations")
    prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, planned=planned,
                       stage="design_validation", end=end_close, checkpoint_close=prior_close)
    if not prepared["raise_complete"]:
        raise ValueError("rehearsal holdout listing coverage is incomplete")
    rules, losses, variants = evaluate_holdout(prepared, design, VARIANTS)
    return {"experiment_id": EXPERIMENT_ID, "stage": "holdout_rehearsal", "start": start.date().isoformat(),
            "end": end.date().isoformat(), "preflight": preflight, "variants": variants,
            "rules": rules, "delisting_sensitivity": losses, "design_effects": design["design_effects"],
            "checkpoint_close": prior_close.date().isoformat()}


def fixture_timing(*, stocks=512, paths=200, months=12):
    """Synthetic accounting/draw timing; no local return or financial evaluation."""
    codes = [f"{pos:06d}" for pos in range(stocks)]
    quotes = {code: hc003.Quote(1.0, 10000.0, "KOSPI", 2e9, True) for code in codes}
    cash, quantities, rng = np.ones(paths), np.zeros((paths, stocks)), np.random.default_rng(0)
    started = time.perf_counter()
    for month in range(months):
        mask = np.zeros((paths, stocks), dtype=bool)
        for path in range(paths):
            previous = [codes[pos] for pos in np.flatnonzero(quantities[path] > 0)]
            chosen, _, _ = random_target(codes, previous, stocks * 3 // 4, stocks * 2 // 3 if month else 0, rng)
            mask[path, [int(code) for code in chosen]] = True
        cash, quantities, _ = hc003.rebalance_paths(cash, quantities, codes, quotes, mask, "2025-05-02")
    return {"seconds": time.perf_counter() - started, "stocks": stocks, "paths": paths, "months": months,
            "quantity_matrix_mib": quantities.nbytes / 1024**2}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    registration = _registration(repo_root)
    sessions = hc003.session_calendar()
    planned, excluded = hc003.rebalance_calendar(sessions)
    status, months = coverage_plan(data_dir, planned, sessions)
    paths = hc003.price_files(data_dir, "20150101", "20251230", repo_root=repo_root, experiment_id=EXPERIMENT_ID)
    price_days = pd.DatetimeIndex([day for day, _ in paths])
    dates = financial_dates.FilingDates.from_data(data_dir)
    audit = financial_dates.FinancialVersions(data_dir, dates, "S", "design_validation", derive=False,
                                             sample_end=planned[-1]["decision_date"])
    timing = fixture_timing()
    for row in months:
        pos = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        end = sessions.get_loc(pd.Timestamp(row["end_date"]))
        required = sessions[max(0, pos - 59):end + 1]
        row["price_covered"] = required.difference(price_days).empty
    projected_stocks = 4000
    # Two rules x three variants x two valuations x two control types, plus
    # source parsing/financial signals and actual cost/subset diagnostics.
    control_seconds = timing["seconds"] * len(planned) / timing["months"] * projected_stocks / timing["stocks"] * 24
    listing_bytes = sum(path.stat().st_size for folder in ("list", "list_supplement")
        for path in (Path(data_dir) / "disclosures/dart_full" / folder).rglob("*.jsonl"))
    financial_bytes = sum(path.stat().st_size for path in audit.paths)
    quarter_bytes = max((sum(path.stat().st_size for day, path in paths if day.to_period("Q") == quarter)
                         for quarter in set(price_days.to_period("Q"))), default=0)
    projected_mib = (1024**3 + listing_bytes * 4 + financial_bytes * 3 + quarter_bytes * 3) / 1024**2
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash, "variants": VARIANTS,
        "planned_months": len(planned), "excluded_months": excluded, "months": months,
        "covered_price_months": sum(row["price_covered"] for row in months),
        "listing_incomplete_months": sum(not row["listing"]["covered"] for row in months),
        "listing_status": status, "L": dates.L, "financial_dates": audit.report(),
        "fixture_timing": timing, "projection": {"full_run_seconds_range": [control_seconds, control_seconds * 3 + 900],
            "peak_rss_mib": projected_mib, "rss_target_mib": MAX_RSS_BYTES / 1024**2,
            "basis": "Measured synthetic draws/accounting scaled to 4000 names, 200 paths, 103 months and 24 control evaluations. RSS is an input-size-based estimate including a 1 GiB result/serialization reserve; no full-run measurement."},
        "interpretations": INTERPRETATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: local coverage/date audit and synthetic timing")
    mode.add_argument("--execute", action="store_true", help="append three design rows with both rules")
    mode.add_argument("--holdout", action="store_true", help="one protected session for both rules' passing variants")
    mode.add_argument("--rehearse-holdout", nargs="*", metavar="DATE",
                      help="read-only holdout rehearsal; default 2025-01-01 2025-06-30")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.rehearse_holdout is not None:
        if len(args.rehearse_holdout) not in (0, 2):
            parser.error("--rehearse-holdout takes START END or no dates")
        result = rehearse_holdout(*args.rehearse_holdout, data_dir=args.data_dir)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    elif args.execute or args.holdout:
        result = (run_holdout if args.holdout else run_experiment)(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} {result['stage']}: {result['verdict']}")
        if result["stage"] == "holdout" and any(v["verdict"] == "sensitivity_unavailable"
                                               for v in result["variants"].values()):
            print("USER DECISION REQUIRED: holdout sensitivity_unavailable is not a registered terminal status. "
                  "LH002 remains blocked; this holdout cannot be reopened. "
                  + json.dumps(result["sensitivity_unavailable_reasons"], ensure_ascii=False))
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run; variants={','.join(VARIANTS)}; rules=S,O")
        print(f"Preregistration: {plan['prereg_commit']}; months={plan['planned_months']}; price-covered={plan['covered_price_months']}")
        status = plan["listing_status"]
        print(f"Listing supplement complete={status['supplement_complete']}; reason={status['reason']}; completed={status['completed_corps']}/{status['target_count']}")
        print(f"126-session windows incomplete={plan['listing_incomplete_months']}; av_all/av_conc stop as data_incomplete; av_phase may run.")
        print(f"Actual-public-date L={plan['L']} days; financial date counts: " + json.dumps(plan["financial_dates"], ensure_ascii=False))
        print("Fixture timing: " + json.dumps(plan["fixture_timing"]))
        print("Projected runtime/RSS: " + json.dumps(plan["projection"]))
        print("Accounting: HC003 v2, sells first, freeze before allocation, one 0.1% band; calendar NW(3), Holm(3) per rule; fill-first 200 paths.")
        print("No local returns evaluated; no registry/results writes or holdout session. S/O actual-date cutoffs: 2026-01-01 / 2026-07-01.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
