"""Fixed 20-session bundles, paired NW statistics and preregistered verdicts."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001, hc002

from . import EXPERIMENT_ID, VARIANTS, guard_data
from .inputs import json_value

MULTIPLIERS = (1.0, 1.5, 2.0)
INTERPRETATIONS = {
    "note": "Four LLM groups are judged separately; multiple opportunities for a chance pass remain. Random ranks are reported for every arm; text effects and memory dependence are diagnostics. No winner is selected.",
    "criteria": {
        "primary": {"metric": "primary.mean", "definition": "Mean decision-level B net minus universe EW gross, or C net minus A EW net, using only paired executable observations."},
        "core_t_stat_gt": {"metric": "primary.t_stat", "definition": "Mean / NW standard error; Bartlett lag 4 calendar decision periods, preserving missing weeks/months, no finite-sample correction; >2 or >3 after more than 20 trial rows."},
        "random_control_top_percent": {"metric": "random_control.share_of_controls", "definition": "Fraction of 200 seed-0 control paths with paired mean excess >= actual; <=5% final, <=20% screening. Each path draws ten names without replacement each decision from its preregistered pool."},
        "minimum_observations": {"metric": "primary.count, events", "definition": "Conservatively require both 20 valid paired weekly decisions and 300 executable selected stock/decision observations; t uses weekly observations only."},
        "cost_sensitivity": {"metric": "cost_sensitivity[m].top_net_excess", "definition": "Full round-trip cost per bundle at 1/1.5/2; sell tax unscaled. Positive paired excess at 1.5 is required."},
        "holdout_direction_fraction": {"metric": "final.mean / screening.mean", "definition": "Numeric only: same sign as screening, positive, at least half its effect. Text has no screening effect; this criterion is not applicable."},
        "screening": {"metric": "primary, random_control", "definition": "Anonymous month-end numeric groups proceed if mean>0, lag-4 t>1 and random share<=20%; failed numeric groups remain screening_fail in final, but still supply text candidates."},
        "a_weekly": {"metric": "a_weekly", "definition": "HC002 phase-2 EW net minus the same universe EW gross, diagnostic; full costs at each weekly decision."},
        "b_vs_a": {"metric": "per_decision.vs_a", "definition": "B EW net minus A EW net on the same decision, diagnostic."},
        "text_effect": {"metric": "text_effect", "definition": "Paired text net minus its numeric net, both selected from the fixed numeric 30; no independent pass criterion."},
        "avoid": {"metric": "avoid_excess", "definition": "C_num avoid-ten EW net minus A EW net; diagnostic, negative is successful avoidance."},
        "memory_dependence": {"metric": "memory_dependence", "definition": "Paired named minus anonymous C_num in 2023-01..2025-11, one call each; diagnostic only."},
        "liquidity_month": {"metric": "liquidity_buckets, monthly", "definition": "Decision-close ADV cost-model buckets (within-bucket benchmarks), and decision-month means; no extra variants."},
        "jaccard": {"metric": "jaccard", "definition": "Mean of the three pairwise repeat buy-ten set intersections/unions. Stage-1 industry consistency is reported separately."},
        "invalid_calls": {"metric": "invalid_calls, invalid_call_count", "definition": "Invalid real attempts including retries, deduplicated by cache key; cache reuse is not another invalid call. Stage-1 counts are separate. A failed repeat invalidates the arm's decision."},
        "text_missing": {"metric": "text_missing", "definition": "Missing extraction per stock/decision/arm, counted once for the common text input, not once per repeat."},
        "execution": {"metric": "return_counts, stale_exits", "definition": "Shared HC002 next-open/entry-inclusive 20th-close, limit-up exclusion, adjusted ret_1d chain; shared explicitly counted raw fallback/stale/no-later-price policies."},
        "hc002_holdout": {"metric": "hc002_holdout", "definition": "Only if an HC002 phase2_ew passing registry row exists: HC002 monthly January-May rules, exits by June 30; positive same-direction effect >=0.5 of registered design effect. Otherwise not_applicable."},
        "provenance": {"metric": "manifest, inputs, snapshot_hash, assumptions", "definition": "Frozen prompt/schema hashes, fixed model/CLI version, content-addressed rendered inputs and extraction metadata. These certify artifacts, not profitability or unseen training data."},
        "compatibility_aliases": {"metric": "ic, skipped_month_count", "definition": "Shared writer compatibility only: ic aliases paired excess statistics (LLM: NW), never a rank IC; skipped_month_count counts skipped decisions, including weekly ones."},
    },
}


def newey_west(values, lag=4, *, positions=None):
    values = np.asarray(values, dtype=float)
    positions = np.arange(len(values)) if positions is None else np.asarray(positions)
    if len(positions) != len(values) or len(set(positions)) != len(positions):
        raise ValueError("NW positions must be unique and align with observations")
    valid = np.isfinite(values)
    values, positions = values[valid], positions[valid]
    n = len(values)
    mean = float(values.mean()) if n else None
    if n < 2:
        return {"count": n, "mean": mean, "std": None, "standard_error": None, "t_stat": None, "lag": lag}
    centred = np.zeros(n) if np.all(values == values[0]) else values - mean
    long_variance = float(np.dot(centred, centred) / n)
    residuals = dict(zip(positions, centred))
    for offset in range(1, lag + 1):
        covariance = float(sum(value * residuals.get(position - offset, 0) for position, value in residuals.items()) / n)
        long_variance += 2 * (1 - offset / (lag + 1)) * covariance
    se = float(np.sqrt(max(0, long_variance) / n))
    return {"count": n, "mean": mean, "std": float(values.std(ddof=1)), "standard_error": se,
            "t_stat": mean / se if se > 0 else None, "lag": lag}


def paired_stats(rows, field, cadence="weekly"):
    freq = "W-FRI" if cadence == "weekly" else "M"
    return newey_west([row[field] for row in rows], positions=[pd.Timestamp(row["decision_date"]).to_period(freq).ordinal for row in rows])


def observations(prices, universe, *, repo_root=PROJECT_ROOT, last_dates=None):
    """Unchanged HC002 holding/cost kernels with an explicit LH001 guard."""
    if prices.empty:
        raise ValueError("prices are required")
    days = pd.DatetimeIndex(prices.index.get_level_values("trade_date").unique()).sort_values()
    guard_data(days[0], days[-1], repo_root=repo_root)
    if universe.empty:
        return pd.DataFrame(columns=["decision_date", "stock_code", "gross", "phase", "bucket", "reason", "exit_date",
                                     "avg_trading_value_20d", *[f"cost_{m}" for m in MULTIPLIERS], *signal_eval._RETURN_FLAGS])
    holding = signal_eval._holding_data(prices, "exclude", last_dates=last_dates)
    window = signal_eval._holding_window(holding, 20, "last_close")
    index = pd.MultiIndex.from_frame(universe[["decision_date", "stock_code"]]).set_names(["trade_date", "stock_code"])
    computed = signal_eval._forward_observations(holding, window, index).reset_index(drop=True)
    rows = pd.concat([universe.reset_index(drop=True), computed], axis=1)
    positions = days.get_indexer(universe.decision_date) + 1
    codes = holding["codes"].get_indexer(universe.stock_code)
    if (positions >= len(days)).any() or (codes < 0).any():
        raise ValueError("entry date or stock absent from stored prices")
    opening = holding["open"][positions, codes]
    markets = holding["market"][positions, codes]
    rows["entry_date"] = days[positions]
    rows["bucket"] = np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, rows.avg_trading_value_20d, side="right")
    valid = np.isfinite(rows.gross.astype(float))
    for multiplier in MULTIPLIERS:
        rows[f"cost_{multiplier}"] = np.nan
        rows.loc[valid, f"cost_{multiplier}"] = cost_model.round_trip_cost_vectorized(
            opening[valid], markets[valid], rows.entry_date.to_numpy()[valid],
            rows.avg_trading_value_20d.to_numpy()[valid], multiplier=multiplier)
    return rows


def random_controls(frame, pool, *, rng, size=10):
    group = frame.loc[frame.stock_code.isin(pool) & np.isfinite(frame.gross.astype(float))].sort_values("stock_code", kind="stable")
    if len(group) < size:
        return None
    net = (group.gross - group["cost_1.0"]).to_numpy(float)
    return np.array([net[rng.choice(len(net), size=size, replace=False)].mean() for _ in range(200)])


def _net(frame, multiplier):
    return float((frame.gross - frame[f"cost_{multiplier}"]).mean()) if len(frame) else None


def _variant(frame, decisions, arm, *, bucket=None, cadence="weekly"):
    rows, controls = [], []
    rng = np.random.default_rng(0)
    missing = []
    for decision in sorted(decisions, key=lambda row: row["decision_date"]):
        day = decision["decision_date"]
        selection = decision["arms"].get(arm)
        if selection is None or len(selection["selected"]) != 10:
            missing.append({"decision_date": day, "reason": "no_selection"})
            continue
        group = frame.loc[frame.decision_date.eq(pd.Timestamp(day)) & np.isfinite(frame.gross.astype(float))]
        if bucket is not None:
            group = group.loc[group.bucket.eq(bucket)]
        chosen = group.loc[group.stock_code.isin(selection["selected"])]
        a = group.loc[group.phase.eq(2)]
        if chosen.empty or group.empty or (arm.startswith("c") and a.empty):
            missing.append({"decision_date": day, "reason": "no_paired_executable_returns"})
            continue
        draws = random_controls(group, selection["random_pool"], rng=rng,
                                size=len(chosen) if bucket is not None else 10)
        if draws is None:
            missing.append({"decision_date": day, "reason": "insufficient_random_pool"})
            continue
        row = {"decision_date": day, "count": len(chosen), "universe_gross": float(group.gross.mean()), "universe_count": len(group)}
        for multiplier in MULTIPLIERS:
            benchmark = float(group.gross.mean()) if arm.startswith("b") else _net(a, multiplier)
            row[f"net_{multiplier}"] = _net(chosen, multiplier)
            row[f"excess_{multiplier}"] = row[f"net_{multiplier}"] - benchmark
            row[f"vs_a_{multiplier}"] = row[f"net_{multiplier}"] - _net(a, multiplier) if len(a) else None
        avoid = group.loc[group.stock_code.isin(selection.get("avoid", []))]
        row["avoid_excess"] = _net(avoid, 1.0) - _net(a, 1.0) if len(avoid) and len(a) else None
        controls.append(draws - (float(group.gross.mean()) if arm.startswith("b") else _net(a, 1.0)))
        rows.append(row)
    primary = paired_stats(rows, "excess_1.0", cadence)
    control_means = np.mean(controls, axis=0) if controls else np.array([])
    share = float(np.mean(control_means >= primary["mean"])) if len(control_means) else None
    cost_results = {str(m): {"top_net": paired_stats(rows, f"net_{m}", cadence)["mean"],
                           "top_net_excess": paired_stats(rows, f"excess_{m}", cadence)["mean"],
                           "excess": paired_stats(rows, f"excess_{m}", cadence)} for m in MULTIPLIERS}
    calls = [decision["arms"].get(arm, {}) for decision in decisions]
    invalid_by_call = {}
    for call in calls:
        invalid_by_call.update(call.get("invalid_by_call", {}))
    consistency = [call["jaccard"] for call in calls if call.get("jaccard") is not None]
    return {"primary": primary, "ic": primary, "per_decision": rows, "events": sum(row["count"] for row in rows),
            "cost_sensitivity": cost_results, "random_control": {"share_of_controls": share, "mean_excess": control_means.tolist(),
                                                                   "portfolios_per_decision": 200, "decisions": len(controls), "seed": 0},
            "monthly": {month: paired_stats([row for row in rows if row["decision_date"].startswith(month)], "excess_1.0", cadence)
                        for month in sorted({row["decision_date"][:7] for row in rows})},
            "b_vs_a": paired_stats(rows, "vs_a_1.0", cadence),
            "avoid_excess": paired_stats(rows, "avoid_excess", cadence),
            "jaccard": float(np.mean(consistency)) if consistency else None,
            "invalid_calls": sum(invalid_by_call.values()), "invalid_by_call": invalid_by_call,
            "text_missing": sum(call.get("text_missing", 0) for call in calls),
            "no_selection_decisions": sum(not call.get("selected") for call in calls), "missing_decisions": missing,
            "skipped_month_count": len(missing), "unadjusted_fallback": int(frame.unadjusted_fallback.sum()) if "unadjusted_fallback" in frame else 0,
            "delisting_exclusions": int(frame.reason.eq("no_later_price").sum()) if "reason" in frame else 0}


def metrics(frame, decisions, *, cadence="weekly"):
    results = {arm: _variant(frame, decisions, arm, cadence=cadence) for arm in VARIANTS[:4]}
    for arm, result in results.items():
        result["liquidity_buckets"] = {str(bucket): _variant(frame, decisions, arm, bucket=bucket, cadence=cadence)
                                      for bucket in range(len(cost_model.TRADING_VALUE_BUCKETS))}
        for bucket, value in result["liquidity_buckets"].items():
            number = int(bucket)
            upper = cost_model.TRADING_VALUE_BUCKETS[number]
            value.update(lower_inclusive=0 if number == 0 else cost_model.TRADING_VALUE_BUCKETS[number - 1],
                         upper_exclusive=float(upper) if np.isfinite(upper) else None)
        if arm.endswith("text"):
            numeric = {row["decision_date"]: row for row in results[arm.replace("text", "num")]["per_decision"]}
            paired = [{"decision_date": row["decision_date"], "effect": row["net_1.0"] - numeric[row["decision_date"]]["net_1.0"]}
                      for row in result["per_decision"] if row["decision_date"] in numeric]
            result["text_effect"] = {**paired_stats(paired, "effect", cadence), "per_decision": paired}
    a_rows = []
    valid = frame.loc[np.isfinite(frame.gross.astype(float))]
    for day, group in valid.groupby("decision_date", sort=True):
        a = group.loc[group.phase.eq(2)]
        if len(a):
            a_rows.append({"decision_date": pd.Timestamp(day).date().isoformat(), "count": len(a),
                           **{f"excess_{m}": _net(a, m) - float(group.gross.mean()) for m in MULTIPLIERS}})
    stage1_invalid = {}
    for decision in decisions:
        stage1_invalid.update(decision["arms"].get("b_stage1", {}).get("invalid_by_call", {}))
    return {"variants": results, "a_weekly": {"primary": paired_stats(a_rows, "excess_1.0", cadence), "per_decision": a_rows},
            "invalid_call_count": sum(result["invalid_calls"] for result in results.values()) + sum(stage1_invalid.values()),
            "stage1_invalid_calls": sum(stage1_invalid.values()),
            "return_counts": signal_eval._return_counts(frame),
            "stale_exits": [{"decision_date": row.decision_date.date().isoformat(), "stock_code": row.stock_code,
                              "exit_date": row.exit_date.date().isoformat()} for row in frame.loc[frame.stale_exit].itertuples()],
            "stage1": [{"decision_date": row["decision_date"], **row["arms"]["b_stage1"]} for row in decisions if "b_stage1" in row["arms"]]}


def screening_verdict(result):
    primary, share = result["primary"], result["random_control"]["share_of_controls"]
    if any(value is None for value in (primary["mean"], primary["t_stat"], share)):
        return "screening_fail", ["screening metrics undefined"]
    if primary["mean"] > 0 and primary["t_stat"] > 1 and share <= 0.2:
        return "pass", ["numeric screening conditions satisfied"]
    return "screening_fail", ["screening requires mean>0, NW t>1 and random share<=20%"]


def final_verdict(arm, result, fields, *, screening=None, trial_count=0, clean_verdict="clean"):
    if clean_verdict == "insufficient_clean_window":
        return "insufficient_clean_window", ["probe clean start later than May 2026"]
    if clean_verdict != "clean":
        return "insufficient", ["probe is missing or incomplete"]
    if arm.endswith("num"):
        if screening is None:
            return "insufficient", ["numeric screening result missing"]
        if screening["verdict"] != "pass":
            return "screening_fail", ["numeric group failed screening; still supplies text candidates"]
    criteria = fields["pass_criteria"]["design_validation"]
    primary = result["primary"]
    share = result["random_control"]["share_of_controls"]
    stressed = result["cost_sensitivity"]["1.5"]["top_net_excess"]
    if (primary["count"] < criteria["minimum_observations"]["rebalances"]
            or result["events"] < criteria["minimum_observations"]["events"]) or any(
            value is None or not np.isfinite(value) for value in (primary["mean"], primary["t_stat"], share, stressed)):
        return "insufficient", ["too few paired weekly decisions/stock observations or undefined metrics"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trial_count > 20 else criteria["core_t_stat_gt"]
    failures = []
    if primary["mean"] <= criteria["net_performance_gt"]:
        failures.append("primary net excess must be positive")
    if primary["t_stat"] <= threshold:
        failures.append(f"NW t must exceed {threshold}")
    if share > criteria["random_control_top_percent"] / 100:
        failures.append("random-control rank exceeds 5%")
    if stressed <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
        failures.append("cost x1.5 net excess must be positive")
    if arm.endswith("num"):
        design = screening["primary"]["mean"]
        if design is None:
            return "insufficient", ["screening effect is undefined"]
        if np.sign(primary["mean"]) != np.sign(design) or primary["mean"] < fields["pass_criteria"]["holdout"]["minimum_fraction_of_design_effect"] * design:
            failures.append("final effect must keep screening sign and at least half its magnitude")
    return ("fail", failures) if failures else ("pass", ["all preregistered criteria satisfied"])


def hc002_passing_row(repo_root):
    path = Path(repo_root) / experiment_registry.REGISTRY_PATH
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        rows = experiment_registry._registry_rows(handle)
    passing = [row for row in rows if row["experiment_id"] == hc002.EXPERIMENT_ID and row["variant"] == "phase2_ew" and row["verdict"] == "pass"]
    return passing[-1] if passing else None


def hc002_verdict(primary, passing):
    if passing is None:
        return "not_applicable", ["HC002 has no passing phase2_ew registry row"]
    registered = json.loads(passing["metrics_json"])
    design = registered["design_mean_excess"]
    mean = primary["excess"]["mean"]
    if mean is None or design is None or primary["excess"]["count"] == 0:
        return "insufficient", ["HC002 holdout/design effect undefined"]
    if mean <= 0 or np.sign(mean) != np.sign(design) or mean < 0.5 * design:
        return "fail", ["HC002 holdout needs positive, same-sign effect at least half the design effect"]
    return "pass", ["HC002 linked single-use holdout conditions satisfied"]


def publish(payload, *, repo_root=PROJECT_ROOT, smoke=False, record=True):
    """Use shared publication/registry, replacing its legacy IC prose afterwards."""
    payload = json_value(payload)
    payload["smoke"] = smoke
    for result in payload["variants"].values():
        # Adapter fields required by common.write_results; 'ic' is explicitly an
        # alias of paired excess stats, not a measured rank IC.
        result.setdefault("ic", result.get("primary", newey_west([])))
        result.setdefault("cost_sensitivity", {str(m): {"top_net": None, "top_net_excess": None} for m in MULTIPLIERS})
        result.setdefault("random_control", {"share_of_controls": None})
        result.setdefault("skipped_month_count", 0)
        result.setdefault("unadjusted_fallback", 0)
        result.setdefault("delisting_exclusions", 0)
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root)
    lines = [f"# {EXPERIMENT_ID}: {payload['stage']}", "", f"Smoke: {smoke}; aggregate status: {payload['verdict']}", "",
             "No winning variant is selected. Returns are decimal. LLM t: NW lag 4; HC002: its monthly statistics.", "",
             "| variant | verdict | decisions | mean paired net excess | t | random share | cost x1.5 excess |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for arm, result in payload["variants"].items():
        stats = result.get("primary", result["ic"])
        lines.append(f"| {arm} | {result['verdict']} | {stats['count']} | {stats['mean']} | {stats['t_stat']} | "
                     f"{result['random_control']['share_of_controls']} | {result['cost_sensitivity']['1.5']['top_net_excess']} |")
        lines += ["", f"{arm}: " + "; ".join(result["reasons"]), ""]
    lines += ["", "## interpretations", "", INTERPRETATIONS["note"]]
    lines += [f"- {key}: {value['metric']}: {value['definition']}" for key, value in INTERPRETATIONS["criteria"].items()]
    lines += ["", "Assumptions:", ""] + [f"- {note}" for note in payload["assumptions"]]
    paths[1].write_text("\n".join(lines) + "\n", encoding="utf-8")
    if record and not smoke:
        for arm in VARIANTS:
            result = payload["variants"][arm]
            experiment_registry.record_trial(EXPERIMENT_ID, arm, result, result["verdict"], repo_root=repo_root,
                                             note=f"LH001 {payload['stage']}; fixed variant; no selection of winner")
    return payload, paths
