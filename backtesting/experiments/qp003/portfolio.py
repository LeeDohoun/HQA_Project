"""Hold-aware paths using QP001's unchanged group execution and selection."""
from contextlib import nullcontext
import json
import math

import numpy as np
import pandas as pd

from backtesting import holdout
from backtesting.experiments import av001, hc003
from backtesting.experiments.qp001 import artifacts
from backtesting.experiments.qp001.portfolio import (
    qp_targets, random_targets, rebalance, subgroup_diagnostics, summary,
)
from . import DESIGN_END, DESIGN_START, EXPERIMENT_ID, N_CONTROLS, VARIANT


def simulate(book, selections, *, experiment_id, variant, targets_fn, baseline=None,
             model_version="v2", multiplier=1.0, loss=False, initial_state=None,
             initial_value=None, retain_details=True):
    if book.experiment_id != experiment_id:
        raise ValueError("accounting experiment ID differs from its explicit contract")
    state = hc003.PortfolioState() if initial_state is None else hc003.PortfolioState.from_dict(initial_state)
    rows = []
    for number, selection in enumerate(selections):
        holdout.guard_period(pd.Timestamp(selection["trade_date"]).date(), pd.Timestamp(selection["end_date"]).date(),
                             experiment_id=experiment_id, repo_root=book.repo_root)
        permitted = selection["rebalance_permitted"]
        # Do not select targets (or draw random numbers) for a held decision.
        targets = targets_fn(variant, selection, state.quantities) if permitted else set()
        previous = set(state.quantities)
        groups = selection.get("groups")
        retained = {str(key): len(set(codes) & targets & previous) for key, codes in (groups or {}).items()}
        state, report = rebalance(state, book.quotes(selection["trade_date"], loss=loss), targets,
            selection["trade_date"], groups=groups, weights=selection.get("weights"),
            model_version=model_version, multiplier=multiplier, no_trade=not permitted)
        beginning = initial_value if number == 0 and initial_value is not None else report["pre_value"]
        ending = hc003.portfolio_value(state, book.marks(selection["end_date"], selection["end_kind"], loss=loss))
        ret = ending / beginning - 1 if beginning > 0 else None
        benchmark = (ret if experiment_id == EXPERIMENT_ID and variant == "baseline" else
                     hc003.benchmark_return(book, selection, loss=loss)) if baseline is None else baseline[number]["return"]
        valid = av001.observation_validity(selection, ret, benchmark)
        if not retain_details:
            report = {key: value for key, value in report.items() if key not in ("H", "Z", "T", "trades", "target_amounts")}
        rows.append({**{key: selection[key] for key in ("month", "decision_date", "trade_date", "end_date", "end_kind")},
            **report, **({"holdings": sorted(state.quantities)} if retain_details else {}),
            "rebalance_permitted": permitted, "decision_status": selection["decision_status"],
            "target_retained_by_group": retained, "target_retained_count": len(targets & previous),
            "return": ret, "benchmark": benchmark, "excess": ret - benchmark if valid else None,
            "valid": valid, "return_denominator": beginning, "end_value": ending})
    return {"per_month": rows, "state": state.to_dict(),
            "terminal_value": hc003.portfolio_value(state, book.marks(book.end, "close", loss=loss))}


def paired_scenarios(book, selections, *, loss=False):
    runs = {}
    for name, (model, multiplier) in hc003.SCENARIOS.items():
        baseline = simulate(book, selections, experiment_id=EXPERIMENT_ID, variant="baseline", targets_fn=qp_targets,
            model_version=model, multiplier=multiplier, loss=loss, retain_details=name == "v2_1.0")
        strategy = simulate(book, selections, experiment_id=EXPERIMENT_ID, variant=VARIANT, targets_fn=qp_targets,
            baseline=baseline["per_month"], model_version=model, multiplier=multiplier,
            loss=loss, retain_details=name == "v2_1.0")
        runs[name] = {"baseline": baseline, "strategy": strategy}
    return runs


def full_mean(values):
    # A missing observation invalidates the entire mean; its month stays in the denominator.
    return sum(values) / len(values) if values and all(v is not None and math.isfinite(v) for v in values) else None


def controls(book, selections, actual, baseline, *, loss=False, independent=False, paths=N_CONTROLS, details_path=None):
    if book.experiment_id != EXPERIMENT_ID:
        raise ValueError("control experiment ID differs from QP003")
    months = [s["month"] for s in selections]
    if [r["month"] for r in actual] != months or [r["month"] for r in baseline] != months:
        raise ValueError("controls require every actual and baseline month in order")
    rng = np.random.default_rng(0)
    states = [hc003.PortfolioState() for _ in range(paths)]
    observations, diagnostics = [[] for _ in range(paths)], []
    design_indices = [i for i, month in enumerate(months) if DESIGN_START <= month <= DESIGN_END]
    with artifacts.atomic_output(details_path) if details_path is not None else nullcontext() as handle:
        for number, selection in enumerate(selections):
            holdout.guard_period(pd.Timestamp(selection["trade_date"]).date(), pd.Timestamp(selection["end_date"]).date(),
                                 experiment_id=EXPERIMENT_ID, repo_root=book.repo_root)
            quotes = book.quotes(selection["trade_date"], loss=loss)
            marks = book.marks(selection["end_date"], selection["end_kind"], loss=loss)
            month, cost_rates = [], {}
            permitted = selection["rebalance_permitted"]
            for path, state in enumerate(states):
                targets, adjustments = (random_targets(selection, state.quantities, actual[number], rng,
                    independent=independent) if permitted else (set(), {}))
                state, report = rebalance(state, quotes, targets, selection["trade_date"], groups=selection["groups"],
                    weights=selection["weights"], no_trade=not permitted, cost_rates=cost_rates)
                states[path] = state
                value = hc003.portfolio_value(state, marks)
                ret = value / report["pre_value"] - 1 if report["pre_value"] > 0 else None
                benchmark = baseline[number]["return"]
                excess = ret - benchmark if av001.observation_validity(selection, ret, benchmark) else None
                observations[path].append(excess)
                month.append({"adjustments": adjustments, "holdings_count": report["holdings_count"],
                    "buy_turnover": report["buy_turnover"], "sell_turnover": report["sell_turnover"],
                    "cost": report["cost"], "cash_weight": report["cash_weight"], "frozen_weight": report["frozen_weight"],
                    "return": ret, "excess": excess,
                    "overlap": len(set(state.quantities) & set(actual[number]["holdings"])) / len(actual[number]["holdings"])
                        if actual[number]["holdings"] else None})
            detail = {"month": selection["month"], "rebalance_permitted": permitted,
                      "decision_status": selection["decision_status"], "paths": month}
            if handle is None:
                diagnostics.append(detail)
            else:
                json.dump(detail, handle, sort_keys=True, allow_nan=False)
                handle.write("\n")
        if handle is not None:
            for path, state in enumerate(states):
                json.dump({"path": path, "state": state.to_dict()}, handle, sort_keys=True, allow_nan=False)
                handle.write("\n")
    means = [full_mean([rows[i] for i in design_indices]) for rows in observations]
    real_mean = full_mean([actual[i]["excess"] for i in design_indices])
    share = (sum(mean >= real_mean for mean in means) / paths
             if paths and real_mean is not None and all(m is not None for m in means) else None)
    result = {"seed": 0, "paths": paths, "draw_order": "permitted month/path/(market,phase2,size)/stock_code",
              "used_for_judgement": not independent, "share_of_controls": share,
              "path_means": means, "design_denominator": len(design_indices), "rng_state": rng.bit_generator.state}
    if details_path is None:
        result.update(per_month=diagnostics, states=[state.to_dict() for state in states])
    else:
        result["details"] = {"file": str(details_path), "sha256": artifacts.digest(details_path),
                             "month_rows": len(selections), "state_rows": paths}
    return result
