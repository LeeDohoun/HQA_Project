"""HC003 executions, with QP001's sole target-amount exception."""
from __future__ import annotations

from contextlib import nullcontext
import json

import numpy as np
import pandas as pd

from backtesting import cost_model, holdout
from backtesting.experiments import av001, hc003, hf001
from . import DESIGN_END, DESIGN_START, EXPERIMENT_ID, N_CONTROLS
from . import artifacts
from .selection import group_targets


def rebalance(state, quotes, targets, day, *, groups=None, weights=None,
              model_version="v2", multiplier=1.0, no_trade=False, cost_rates=None):
    if groups is None:
        return hc003.rebalance(state, quotes, targets, day, model_version=model_version,
                               multiplier=multiplier, no_trade=no_trade)
    # HC003 3.3 verbatim execution order: only the scalar target becomes
    # W * alpha_g / |T_g|. Band and cash-funding remain portfolio-wide.
    state = hc003.PortfolioState.from_dict(state.to_dict())
    before, targets = set(state.quantities), set(targets)
    marks = {code: quote.mark for code, quote in quotes.items()}
    value = hc003.portfolio_value(state, marks)
    frozen = {code for code in before if no_trade or not quotes[code].tradable or quotes[code].limit_down}
    frozen_value = sum(state.quantities[code] * marks[code] for code in sorted(frozen))
    allocatable = value - frozen_value
    executable = {code for code in targets - frozen if not no_trade and code in quotes and quotes[code].tradable
                  and not quotes[code].limit_down and not (quotes[code].limit_up and code not in before)}
    if set(weights) != set(groups) or (groups and not np.isclose(sum(weights.values()), 1)):
        raise ValueError("group weights must match C_t and sum to one")
    if any(weights[key] < 0 for key in groups) or not targets <= set().union(set(), *groups.values()):
        raise ValueError("targets must belong to C_t groups with nonnegative weights")
    amounts, executable_counts = {}, {}
    for key, codes in groups.items():
        members = executable & set(codes)
        executable_counts[key] = len(members)
        for code in members:
            if code in amounts:
                raise ValueError("C_t groups must be disjoint")
            amounts[code] = allocatable * weights[key] / len(members)
    buy_orders, sells, trades = {}, {}, []
    band_skipped = {"buy": 0.0, "sell": 0.0}
    for code in sorted((before | executable) - frozen):
        current = state.quantities.get(code, 0.0) * marks[code]
        full_exit = code in before and code not in targets
        delta = (0.0 if full_exit or code not in executable else amounts[code]) - current
        if not full_exit and abs(delta) < value * 0.001:
            if delta < 0:
                band_skipped["sell"] -= delta
            elif delta > 0 and not quotes[code].limit_up:
                band_skipped["buy"] += delta
            continue
        if delta < 0:
            sells[code] = (-delta, full_exit)
        elif delta > 0 and not quotes[code].limit_up:
            buy_orders[code] = delta
    def rate(code, side):
        key = code, side
        if cost_rates is not None and key in cost_rates:
            return cost_rates[key]
        quote = quotes[code]
        value = cost_model.one_way_cost(quote.raw_open, quote.market, pd.Timestamp(day).date(), quote.adv, side,
                                        multiplier=multiplier, model_version=model_version)
        if cost_rates is not None:
            cost_rates[key] = value
        return value
    for code, (amount, full_exit) in sells.items():
        cost = amount * rate(code, "sell")
        state.cash += amount - cost
        if full_exit or amount >= state.quantities[code] * marks[code]:
            del state.quantities[code]
        else:
            state.quantities[code] -= amount / marks[code]
        trades.append({"stock_code": code, "side": "sell", "amount": amount, "cost": cost,
                       "adv": quotes[code].adv, "full_exit": full_exit})
    buy_rates = {code: rate(code, "buy") for code in buy_orders}
    needed = sum(amount * (1 + buy_rates[code]) for code, amount in buy_orders.items())
    scale = min(1.0, state.cash / needed) if needed else 1.0
    for code, desired in buy_orders.items():
        amount = desired * scale
        if amount == 0:
            continue
        cost = amount * buy_rates[code]
        state.cash -= amount + cost
        state.quantities[code] = state.quantities.get(code, 0.0) + amount / marks[code]
        trades.append({"stock_code": code, "side": "buy", "amount": amount, "cost": cost,
                       "adv": quotes[code].adv, "full_exit": False})
    if state.cash < -1e-12 * max(value, 1.0):
        raise ValueError("portfolio borrowing detected")
    state.cash = max(0.0, state.cash)
    totals = {side: sum(row["amount"] for row in trades if row["side"] == side) for side in ("buy", "sell")}
    costs = {side: sum(row["cost"] for row in trades if row["side"] == side) for side in ("buy", "sell")}
    post_value = hc003.portfolio_value(state, marks)
    return state, {"pre_value": value, "post_value": post_value,
        "H": sorted(targets), "Z": sorted(frozen), "T": sorted(executable), "n": len(executable),
        "W": allocatable, "target_amounts": amounts, "buy_scale": scale, "trades": trades,
        "group_weights": {str(key): weights[key] for key in groups},
        "group_executable_counts": {str(key): executable_counts[key] for key in groups},
        "empty_group_cash_target": sum(allocatable * weights[key] for key in groups if not executable_counts[key]),
        "group_actual_weights": {str(key): sum(state.quantities.get(code, 0) * marks.get(code, 0)
            for code in sorted(codes)) / post_value if post_value > 0 else None for key, codes in groups.items()},
        "buy_amount": totals["buy"], "sell_amount": totals["sell"],
        "band_skipped_buy_value": band_skipped["buy"], "band_skipped_sell_value": band_skipped["sell"],
        "buy_cost": costs["buy"], "sell_cost": costs["sell"], "cost": sum(costs.values()),
        "buy_turnover": totals["buy"] / value if value > 0 else None,
        "sell_turnover": totals["sell"] / value if value > 0 else None,
        "cost_fraction": sum(costs.values()) / value if value > 0 else None,
        "holdings_count": len(state.quantities), "retained_count": len(before & set(state.quantities)),
        "retention_ratio": len(before & set(state.quantities)) / len(before) if before else None,
        "frozen_weight": frozen_value / value if value > 0 else None,
        "cash_weight": state.cash / value if value > 0 else None}


def simulate(book, selections, *, experiment_id, variant, targets_fn, baseline=None,
             model_version="v2", multiplier=1.0, loss=False, initial_state=None, initial_value=None, retain_details=True):
    """Shared AV replay and QP execution path, with explicit target/baseline."""
    if book.experiment_id != experiment_id:
        raise ValueError("accounting experiment ID differs from its explicit contract")
    state = hc003.PortfolioState() if initial_state is None else hc003.PortfolioState.from_dict(initial_state)
    rows = []
    for number, selection in enumerate(selections):
        holdout.guard_period(pd.Timestamp(selection["trade_date"]).date(), pd.Timestamp(selection["end_date"]).date(),
                             experiment_id=experiment_id, repo_root=book.repo_root)
        targets = targets_fn(variant, selection, state.quantities)
        previous = set(state.quantities)
        groups = selection.get("groups")
        retained = {str(key): len(set(codes) & set(targets) & set(state.quantities))
                    for key, codes in (groups or {}).items()}
        state, report = rebalance(state, book.quotes(selection["trade_date"], loss=loss), targets,
            selection["trade_date"], groups=groups, weights=selection.get("weights"),
            model_version=model_version, multiplier=multiplier, no_trade=selection.get("no_trade", False))
        beginning = initial_value if number == 0 and initial_value is not None else report["pre_value"]
        ending = hc003.portfolio_value(state, book.marks(selection["end_date"], selection["end_kind"], loss=loss))
        ret = ending / beginning - 1 if beginning > 0 else None
        benchmark = (ret if experiment_id == EXPERIMENT_ID and variant == "baseline" else
                     hc003.benchmark_return(book, selection, loss=loss)) if baseline is None else baseline[number]["return"]
        valid = av001.observation_validity(selection, ret, benchmark)
        if not retain_details:
            report = {key: value for key, value in report.items() if key not in ("H", "Z", "T", "trades", "target_amounts")}
        rows.append({**{key: selection[key] for key in ("month", "decision_date", "trade_date", "end_date", "end_kind")},
            **report, **({"holdings": sorted(state.quantities)} if retain_details else {}), "target_retained_by_group": retained,
            "target_retained_count": len(set(targets) & previous),
            "return": ret, "benchmark": benchmark, "excess": ret - benchmark if valid else None,
            "valid": valid, "return_denominator": beginning, "end_value": ending})
    return {"per_month": rows, "state": state.to_dict(),
            "terminal_value": hc003.portfolio_value(state, book.marks(book.end, "close", loss=loss))}


def qp_targets(variant, selection, held):
    return set(selection["common"].index) if variant == "baseline" else set().union(set(), *selection["selected"].values())


def paired_scenarios(book, selections, *, loss=False):
    runs = {}
    for name, (model, multiplier) in hc003.SCENARIOS.items():
        baseline = simulate(book, selections, experiment_id=EXPERIMENT_ID, variant="baseline", targets_fn=qp_targets,
                            model_version=model, multiplier=multiplier, loss=loss, retain_details=name == "v2_1.0")
        strategy = simulate(book, selections, experiment_id=EXPERIMENT_ID, variant="q_roa50", targets_fn=qp_targets,
                            baseline=baseline["per_month"], model_version=model, multiplier=multiplier, loss=loss, retain_details=name == "v2_1.0")
        runs[name] = {"baseline": baseline, "strategy": strategy}
    return runs


def random_targets(selection, previous, reference, rng, *, independent=False):
    targets, adjustments = set(), {}
    for key, codes in sorted(selection["groups"].items()):
        size = len(selection["selected"][key])
        if independent:
            chosen = set(rng.choice(sorted(codes), size=size, replace=False).tolist())
            adjustment, shortage = 0, 0
        else:
            chosen, adjustment, shortage = av001.random_target(codes, previous, size,
                reference["target_retained_by_group"][str(key)], rng)
        targets.update(chosen)
        adjustments[str(key)] = {"adjustment": adjustment, "shortage": shortage}
    return targets, adjustments


def controls(book, selections, actual, baseline, *, loss=False, independent=False, paths=N_CONTROLS, details_path=None):
    rng = np.random.default_rng(0)
    states = [hc003.PortfolioState() for _ in range(paths)]
    observations = [[] for _ in range(paths)]
    diagnostics = []
    with artifacts.atomic_output(details_path) if details_path is not None else nullcontext() as handle:
        for number, selection in enumerate(selections):
            holdout.guard_period(pd.Timestamp(selection["trade_date"]).date(), pd.Timestamp(selection["end_date"]).date(),
                                 experiment_id=EXPERIMENT_ID, repo_root=book.repo_root)
            quotes = book.quotes(selection["trade_date"], loss=loss)
            marks = book.marks(selection["end_date"], selection["end_kind"], loss=loss)
            month = []
            cost_rates = {}  # Identical quotes/day/scenario across this month's 200 paths only.
            for path, state in enumerate(states):
                targets, adjustments = random_targets(selection, state.quantities, actual[number], rng, independent=independent)
                state, report = rebalance(state, quotes, targets, selection["trade_date"], groups=selection["groups"],
                    weights=selection["weights"], no_trade=selection["no_trade"], cost_rates=cost_rates)
                states[path] = state
                value = hc003.portfolio_value(state, marks)
                ret = value / report["pre_value"] - 1 if report["pre_value"] > 0 else None
                benchmark = baseline[number]["return"]
                excess = ret - benchmark if ret is not None and benchmark is not None else None
                observations[path].append({"month": selection["month"], "excess": excess})
                month.append({"adjustments": adjustments, "holdings_count": report["holdings_count"],
                    "buy_turnover": report["buy_turnover"], "sell_turnover": report["sell_turnover"],
                    "cost": report["cost"], "cash_weight": report["cash_weight"], "frozen_weight": report["frozen_weight"],
                    "overlap": len(set(state.quantities) & set(actual[number]["holdings"])) / len(actual[number]["holdings"])
                        if actual[number]["holdings"] else None})
            detail = {"month": selection["month"], "paths": month}
            if handle is None:
                diagnostics.append(detail)
            else:
                json.dump(detail, handle, sort_keys=True, allow_nan=False)
                handle.write("\n")
        if handle is not None:
            for path, state in enumerate(states):
                json.dump({"path": path, "state": state.to_dict()}, handle, sort_keys=True, allow_nan=False)
                handle.write("\n")
    means = [hc003._mean([row["excess"] for row in rows if DESIGN_START <= row["month"] <= DESIGN_END]) for rows in observations]
    real_mean = hc003._mean([row["excess"] for row in actual if DESIGN_START <= row["month"] <= DESIGN_END])
    share = sum(mean >= real_mean for mean in means) / paths if paths and real_mean is not None and all(m is not None for m in means) else None
    result = {"seed": 0, "paths": paths, "draw_order": "month/path/(market,phase2,size)/stock_code",
            "used_for_judgement": not independent, "share_of_controls": share,
            "path_means": means, "rng_state": rng.bit_generator.state}
    if details_path is None:
        result.update(per_month=diagnostics, states=[state.to_dict() for state in states])
    else:
        result["details"] = {"file": str(details_path), "sha256": artifacts.digest(details_path),
                             "month_rows": len(selections), "state_rows": paths}
    return result


def summary(rows):
    valid = [row for row in rows if row["valid"]]
    return {"primary": av001.newey_west([r["month"] for r in valid], [r["excess"] for r in valid]),
            "mean_return": hc003._mean([r["return"] for r in valid]),
            "mean_baseline": hc003._mean([r["benchmark"] for r in valid]),
            "nav_ratio": rows[-1]["end_value"] / rows[0]["return_denominator"] if rows and rows[0]["return_denominator"] > 0 else None,
            "accounting": {key: hc003._mean([r[key] for r in rows if key in r]) for key in
                ("buy_turnover", "sell_turnover", "buy_cost", "sell_cost", "cost_fraction", "holdings_count",
                 "retention_ratio", "cash_weight", "frozen_weight")},
            "risk": {name: hf001.risk_metrics([r[key] for r in valid])
                     for name, key in (("strategy", "return"), ("baseline", "benchmark"))}}


def subgroup_diagnostics(book, selections, strategy, baseline, *, loss=False):
    """Actual costed group contributions and untradeable top/bottom spreads."""
    previous = {"strategy": set(), "baseline": set()}
    quantities = {"strategy": {}, "baseline": {}}
    per_month = []
    for selection, actual, base in zip(selections, strategy, baseline):
        opening = book.marks(selection["trade_date"], "open", loss=loss)
        ending = book.marks(selection["end_date"], selection["end_kind"], loss=loss)
        groups = selection["groups"]
        rows = {}
        # Trades and end holdings reconstruct each portfolio's dollar P&L by
        # the current decision group. Outside-C frozen names remain explicit.
        buckets = {str(key): set(codes) for key, codes in groups.items()}
        all_current = set().union(set(), *groups.values())
        buckets["outside_C"] = (previous["strategy"] | previous["baseline"]) - all_current
        contributions = {}
        for name, row in (("strategy", actual), ("baseline", base)):
            before = dict(quantities[name])
            after = dict(before)
            for trade in row["trades"]:
                code = trade["stock_code"]
                change = trade["amount"] / opening[code]
                if trade["side"] == "sell":
                    if trade["full_exit"]:
                        after.pop(code)
                    else:
                        after[code] -= change
                else:
                    after[code] = after.get(code, 0) + change
            contributions[name] = {}
            for label, codes in buckets.items():
                profit = sum(after.get(code, 0) * ending[code] - before.get(code, 0) * opening[code] for code in sorted(codes))
                profit += sum((trade["amount"] if trade["side"] == "sell" else -trade["amount"]) - trade["cost"]
                              for trade in row["trades"] if trade["stock_code"] in codes)
                contributions[name][label] = profit / row["return_denominator"] if row["return_denominator"] > 0 else None
            quantities[name], previous[name] = after, set(after)
        for key, codes in groups.items():
            selected = selection["selected"][key]
            def gross(members):
                return hc003._mean([ending[code] / opening[code] - 1 if opening[code] > 0 else 0.0 for code in sorted(members)])
            top, bottom = gross(selected), gross(set(codes) - selected)
            q, b = contributions["strategy"][str(key)], contributions["baseline"][str(key)]
            rows[str(key)] = {"e_contribution": q - b if q is not None and b is not None else None,
                "top_half_gross": top, "bottom_half_gross": bottom,
                "long_short_diagnostic": top - bottom if top is not None and bottom is not None else None,
                "target_alpha": selection["weights"][key],
                "strategy_actual_weight": actual["group_actual_weights"][str(key)],
                "baseline_actual_weight": base["group_actual_weights"][str(key)]}
        q, b = contributions["strategy"]["outside_C"], contributions["baseline"]["outside_C"]
        # A market's portfolio contribution is the sum of its group contributions.
        markets = {market: sum(rows[str(key)]["e_contribution"] for key in groups if key[0] == market)
                   if all(rows[str(key)]["e_contribution"] is not None for key in groups if key[0] == market) else None
                   for market in sorted({key[0] for key in groups})}
        per_month.append({"month": selection["month"], "groups": rows, "markets_e_contribution": markets,
                          "outside_C_e_contribution": q - b if q is not None and b is not None else None})
    return {"per_month": per_month, "long_short_used_for_judgement": False,
            "group_e_definition": "Costed dollar P&L contribution divided by each portfolio's pre-trade NAV; current decision groups, frozen outside-C reported separately.",
            "by_group": {key: {"mean_e_contribution": hc003._mean([row["groups"][key]["e_contribution"] for row in per_month if key in row["groups"]]),
                "mean_long_short_diagnostic": hc003._mean([row["groups"][key]["long_short_diagnostic"] for row in per_month if key in row["groups"]])}
                for key in sorted({key for row in per_month for key in row["groups"]})},
            "by_market_mean_e_contribution": {market: hc003._mean([r["markets_e_contribution"].get(market) for r in per_month])
                for market in sorted({m for r in per_month for m in r["markets_e_contribution"]})}}
