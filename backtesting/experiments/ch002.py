"""CH002 preregistered close-signal/next-open exits; offline dry run by default."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, ch001, common, hf001, ordering

EXPERIMENT_ID = "CH002_chart_exit_rules"
VARIANTS = ("x_trail10", "x_low10")
REFERENCE_SHA256 = "80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f"
MULTIPLIERS = ch001.MULTIPLIERS
N_CONTROLS = 200
INTERPRETATIONS = [
    "Historical follow-up improvement over B, not standalone profitability; 2025 sign is diagnostic and no holdout is used.",
    "W1 uses verified entry-session close/open, not the unused entry-session return. Later W requires verified finite ret>-1; invalid W is permanent. Low10 independently rebuilds the CH001 11-session A window and resumes after gaps.",
    "Signals use closes on days 1..19 including W0 in the peak. Requests are irrevocable; day k+1 is the first attempt, including day 20. A day-20 open sale is an early fill relative to the scheduled close.",
    "Only NaN/missing bases use the preceding session close, exactly as HF001. An otherwise tradable requested open without a finite positive reference is an error. Raw open/entry fallback is allowed only when prior W is uncomputable.",
    "Primary costs are v1 buy at entry and sell at actual realization date/price/market; maturity uses HF001 exit_date and that date's raw close. ADV_sell uses exactly the preceding 20 sessions, excludes the sale date, includes zeros; any negative value stops even if another value is missing. Missing/nonfinite ADV becomes 0 and is counted per realized position.",
    "Primary delisting loss is exactly -1 without added costs for maturity valuations only; successful early sales retain realized returns. HF001 round-trip d is diagnostic only and retains HF001's full-cost loss convention.",
    "The selected B denominator is fixed before entry; unfilled slots stay cash. Cash days count sale-open day through day 20 (21-j); unfilled entries contribute 20. Avoided post-sale gross return is G_maturity/G_sale-1, with both registered fallback policies retained.",
    "Controls condition on actual first-attempt days, not fill days. One RNG per rule/variant, path then month, choice over sorted filled B followed by permutation(m), including m=0. Loss repeats the same draw stream; controls test name/date assignment, not market-wide timing.",
    "36 valid U months AND 36 months with an actual early fill; generic 300 events is not an extra gate. Empty B in nonempty U is a valid cash zero; empty U is invalid. Holm family remains exactly two with undefined/insufficient p=1.",
    "Risk-only requires nonpositive d, lower sample volatility and improved worst-ceil(5%*N) mean. Drawdown starts at wealth 1. Market/liquidity diagnostics re-equal-weight inside the subset; year/ban splits use decision date.",
    "S/O, coverage stops and combined verdict precedence reuse CH001/AV001. All finite registry rows are validated before publication; record_trial does not provide rollback after an OS append failure.",
    "Dry run inventories real bounded price files, listing coverage and financial-date metadata, without loading evaluation prices or calculating real holdings, signals or outcomes. Synthetic timings are projections only. Nonprotected 2026 integrity hashes are distinct from evaluation input; protected price contents are never opened.",
    "Execution uses ordering.execution_lock(repo_root) across preflight, trial-count lookup, judgement, publication and registry appends. After preflight hashes are fixed, exceptions record one design_aborted row per registered variant through ordering.design_attempt; failures before hashes record no attempt. Real execution must run in the main checkout after merge so the directory lock and registry are shared with other research runs.",
    "Input and code SHA-256 are fixed before preparation reads/evaluation and re-inventoried immediately before publication. A changed input, inventory or code aborts with the original hash category digests and no verdict publication. Price-content hashing is bounded through 2025; protected January-June 2026 price files are never opened.",
]


def _registration(repo_root):
    reference = Path(repo_root) / "research/experiments" / ch001.EXPERIMENT_ID / "preregistration.md"
    if hf001._digest(reference) != REFERENCE_SHA256:
        raise ValueError("CH002 CH001 reference SHA-256 mismatch; stopped before data access")
    ch001._registration(repo_root)
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (registration.fields["variants_planned"] != len(VARIANTS) or registration.fields["uses_holdout"]
            or registration.fields["uses_llm"]):
        raise ValueError("CH002 requires two fixed variants, no holdout and no LLM")
    return registration


def holding_values(frame, entry):
    """Causal W, with a permanent invalid suffix; frame is session-aligned."""
    values, reasons = np.full(21, np.nan), [""] * 21
    values[0] = 1.0
    cause = ""
    for k in range(1, 21):
        row = frame.iloc[entry + k - 1]
        if cause:
            reasons[k] = "after_" + cause
            continue
        if not row._present:
            cause = "missing_session"
        elif row.calendar_status != "verified":
            cause = "unverified_session"
        elif k == 1:
            if not np.isfinite(row.close) or row.close <= 0:
                cause = "invalid_entry_close"
            else:
                values[k] = row.close / row.open
        elif not np.isfinite(row.ret_1d) or row.ret_1d <= -1:
            cause = "invalid_return"
        else:
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                values[k] = values[k - 1] * (1 + row.ret_1d)
        if not cause and (not np.isfinite(values[k]) or values[k] <= 0):
            cause = "invalid_holding_value"
            values[k] = np.nan
        reasons[k] = cause
    return values, reasons


def first_request(frame, entry, values, reasons, variant):
    invalid = Counter()
    for k in range(1, 20):
        if variant == "x_trail10":
            if reasons[k]:
                invalid[reasons[k]] += 1
                continue
            hit = values[k] <= .90 * values[:k + 1].max()
        elif variant == "x_low10":
            pos = entry + k - 1
            window = frame.iloc[max(0, pos - 10):pos + 1]
            adjusted, why = ch001.adjusted_values(window.ret_1d.to_numpy(dtype=float)[:, None],
                window.calendar_status.to_numpy()[:, None], window._present.to_numpy()[:, None], length=11)
            if why[0]:
                invalid[str(why[0])] += 1
                continue
            hit = adjusted[-1, 0] < adjusted[:-1, 0].min()
        else:
            raise ValueError("unknown CH002 variant")
        if hit:
            return k + 1, dict(invalid)
    return None, dict(invalid)


def sell_adv(frame, position):
    window = frame.trading_value.iloc[max(0, position - 20):position].to_numpy(dtype=float)
    if (window < 0).any():
        raise ValueError("CH002 negative trading_value in ADV_sell window")
    incomplete = len(window) != 20 or not np.isfinite(window).all()
    return (0.0, True) if incomplete else (float(window.mean()), False)


def realize(frame, sessions, observed, values, *, first_attempt=None):
    """The requested first sellable open, otherwise unchanged HF001 maturity."""
    entry = sessions.get_loc(pd.Timestamp(observed["trade_date"]))
    target = entry + 19
    sold, fallback, fill_day = False, False, 0
    if first_attempt is not None:
        if not 2 <= first_attempt <= 20:
            raise ValueError("CH002 first attempt must be holding day 2..20")
        for j in range(first_attempt, 21):
            pos = entry + j - 1
            quote = frame.iloc[pos]
            if not np.isfinite(quote.open) or quote.open <= 0 or not np.isfinite(quote.volume) or quote.volume <= 0:
                continue
            base = quote.base_price
            if np.isnan(base):
                base = frame.close.iloc[pos - 1]
            if not np.isfinite(base) or base <= 0:
                raise ValueError("CH002 sell price-limit reference is missing or invalid")
            if quote.open <= base * (1 - (hf001.price_limit(sessions[pos]) - .005)):
                continue
            fallback = not np.isfinite(values[j - 1])
            multiple = quote.open / frame.open.iloc[entry] if fallback else values[j - 1] * quote.open / base
            sold, fill_day = True, j
            price, market, day = float(quote.open), quote.market, sessions[pos]
            break
    if not sold:
        day = pd.Timestamp(observed["exit_date"])
        pos = sessions.get_loc(day)
        quote = frame.iloc[pos]
        price, market = float(quote.close), quote.market
        multiple = float(observed["gross"]) + 1
        fallback = bool(observed["unadjusted_fallback"])
    if not np.isfinite(multiple) or multiple < 0:
        raise ValueError("CH002 realized value must be finite and nonnegative")
    adv, incomplete = sell_adv(frame, pos)
    net, loss, costs = [], [], []
    delisted = bool(observed["delisted"]) and not sold
    for m in MULTIPLIERS:
        buy = cost_model.one_way_cost(float(frame.open.iloc[entry]), frame.market.iloc[entry],
            sessions[entry].date(), float(observed["avg_trading_value_20d"]), "buy", multiplier=m, model_version="v1")
        sell = cost_model.one_way_cost(price, market, day.date(), adv, "sell", multiplier=m, model_version="v1")
        value = float(multiple * (1 - sell) / (1 + buy) - 1)
        net.append(value)
        loss.append(-1.0 if delisted else value)
        costs.append({"buy": float(buy), "sell": float(sell), "effective_drag": float(multiple - 1 - value)})
    signal_pos = entry + first_attempt - 2 if first_attempt is not None else None
    date = lambda value: pd.Timestamp(value).date().isoformat()
    return {"G": float(multiple), "net": net, "loss_net": loss, "costs": costs,
        "signal_date": date(sessions[signal_pos]) if signal_pos is not None else None,
        "request_date": date(sessions[signal_pos]) if signal_pos is not None else None,
        "first_attempt_day": first_attempt,
        "first_attempt_date": date(sessions[signal_pos + 1]) if signal_pos is not None else None,
        "fill_date": date(day) if sold else None, "fill_day": fill_day,
        "valuation_date": date(observed["exit_date"]), "realization_date": date(day),
        "scheduled_exit_date": date(sessions[target]), "sell_price": price, "sell_market": market,
        "sell_adv": adv, "sell_adv_incomplete": incomplete, "early_fill": sold,
        "unfilled_request": first_attempt is not None and not sold,
        "delay_sessions": fill_day - first_attempt if sold else None,
        "raw_price_fallback": fallback, "delisted_valuation": delisted,
        "cash_days": 21 - fill_day if sold else 0,
        "post_fill_to_maturity_gross": float((observed["gross"] + 1) / multiple - 1) if sold and multiple > 0 else None}


def build_cohorts(prices, observations, selections, sessions):
    needed = set().union(*(s["B"] for s in selections))
    fields = ["open", "close", "volume", "market", "ret_1d", "base_price", "trading_value", "calendar_status"]
    books = {code: group.droplevel("stock_code").assign(_present=True).reindex(sessions).assign(
                _present=lambda f: f._present.eq(True))
             for code, group in prices[fields].groupby(level="stock_code", observed=True) if code in needed}
    groups = {day: group.set_index("stock_code") for day, group in observations.groupby("decision_date", sort=True)}
    cohorts = []
    for selection in selections:
        codes = sorted(selection["B"])
        group = groups.get(pd.Timestamp(selection["decision_date"]), observations.iloc[:0].set_index("stock_code"))
        n = len(codes)
        nets, loss_nets = np.zeros((n, 21, 3)), np.zeros((n, 21, 3))
        fills = np.zeros((n, 21), dtype=int)
        incomplete_adv, raw_fallback = np.zeros((n, 21), dtype=bool), np.zeros((n, 21), dtype=bool)
        filled, records, contexts = [], {v: [] for v in VARIANTS}, {}
        cohort = {"selection": selection, "group": group, "codes": codes, "filled": filled,
            "nets": nets, "loss_nets": loss_nets, "fills": fills, "records": records,
            "incomplete_adv": incomplete_adv, "raw_fallback": raw_fallback,
            "contexts": contexts, "sessions": sessions, "computed": np.zeros((n, 21), dtype=bool),
            "holding_dates": [str(day.date()) for day in sessions[sessions.get_loc(pd.Timestamp(selection["trade_date"])):][:20]]}
        for number, code in enumerate(codes):
            observed = group.loc[code].to_dict()
            if not observed["filled"]:
                continue
            filled.append(number)
            frame = books[code]
            entry = sessions.get_loc(pd.Timestamp(observed["trade_date"]))
            values, reasons = holding_values(frame, entry)
            baseline = realize(frame, sessions, observed, values)
            if baseline["G"] != observed["gross"] + 1:
                raise AssertionError("CH002 disabled exits differ from HF001 gross")
            requests = {v: first_request(frame, entry, values, reasons, v) for v in VARIANTS}
            contexts[number] = (frame, observed, values, baseline, requests)
        for number in filled:
            code = codes[number]
            frame, observed, values, baseline, requests = contexts[number]
            attempts = {0, *(request for request, _ in requests.values() if request is not None)}
            cache = {attempt: cache_exit(cohort, number, attempt) for attempt in sorted(attempts)}
            for variant in VARIANTS:
                request, invalid = requests[variant]
                outcome = cache[request or 0]
                records[variant].append({"stock_code": code, "entry_date": str(pd.Timestamp(observed["trade_date"]).date()),
                    "baseline": baseline, **outcome, "signal_uncomputable_by_reason": invalid,
                    "hf001_round_trip_net": [outcome["G"] - 1 - observed[f"cost_{m}"] for m in MULTIPLIERS],
                    "hf001_round_trip_baseline": [observed["gross"] - observed[f"cost_{m}"] for m in MULTIPLIERS],
                    "hf001_round_trip_loss_net": [(-1. if outcome["delisted_valuation"] else outcome["G"] - 1) - observed[f"cost_{m}"] for m in MULTIPLIERS],
                    "hf001_round_trip_loss_baseline": [observed["gross_delisting_loss"] - observed[f"cost_{m}"] for m in MULTIPLIERS]})
        cohorts.append(cohort)
    return cohorts


def cache_exit(cohort, number, attempt):
    """Evaluate only an actual exit or a counterfactual used by a drawn path."""
    if cohort["computed"][number, attempt]:
        return None
    frame, observed, values, baseline, _ = cohort["contexts"][number]
    outcome = realize(frame, cohort["sessions"], observed, values, first_attempt=attempt) if attempt else baseline
    cohort["nets"][number, attempt], cohort["loss_nets"][number, attempt] = outcome["net"], outcome["loss_net"]
    cohort["fills"][number, attempt] = outcome["fill_day"]
    cohort["incomplete_adv"][number, attempt] = outcome["sell_adv_incomplete"]
    cohort["raw_fallback"][number, attempt] = outcome["raw_price_fallback"]
    cohort["computed"][number, attempt] = True
    return outcome


def monthly(cohorts, variant, *, loss=False, market=None, bucket=None):
    rows = []
    for cohort in cohorts:
        selection, group, codes = cohort["selection"], cohort["group"], cohort["codes"]
        keep = [pos for pos, code in enumerate(codes) if
            (market is None or group.loc[code, "decision_market"] == market)
            and (bucket is None or group.loc[code, "bucket"] == bucket)]
        records = {row["stock_code"]: row for row in cohort["records"][variant]}
        attempts = [records[code]["first_attempt_day"] or 0 if code in records else 0 for code in codes]
        table = cohort["loss_nets" if loss else "nets"]
        chosen = table[np.arange(len(codes)), attempts]
        costs = {}
        for pos, m in enumerate(MULTIPLIERS):
            filtered = float(chosen[keep, pos].mean()) if keep else 0.0
            baseline = float(table[keep, 0, pos].mean()) if keep else 0.0
            realized = [records[codes[i]] for i in keep if codes[i] in records]
            def drag(row, which):
                item = row if which == "filtered" else row["baseline"]
                return 0.0 if loss and item["delisted_valuation"] else item["costs"][pos]["effective_drag"]
            costs[str(m)] = {"filtered_net": filtered, "baseline_net": baseline, "difference": filtered - baseline,
                "universe_net": None, "universe_excess": None,
                "filtered_cost": sum(drag(r, "filtered") for r in realized) / len(keep) if keep else 0.,
                "baseline_cost": sum(drag(r, "baseline") for r in realized) / len(keep) if keep else 0.}
        actual = [records[codes[i]] for i in keep if codes[i] in records]
        reference = group
        if market is not None:
            reference = reference.loc[reference.decision_market.eq(market)]
        if bucket is not None:
            reference = reference.loc[reference.bucket.eq(bucket)]
        valid = bool(len(reference))
        rows.append({"month": selection["month"], "decision_date": selection["decision_date"], "valid": valid,
            "B": [codes[i] for i in keep], "costs": costs, "removed_minus_remaining": None,
            "early_fills": sum(r["early_fill"] for r in actual), "requests": sum(r["first_attempt_day"] is not None for r in actual),
            "cash_weight_day_average": (20 * (len(keep) - len(actual)) + sum(r["cash_days"] for r in actual)) / (20 * len(keep)) if keep else 1.,
            "hf001_round_trip_d": {str(m): sum(r["hf001_round_trip_loss_net" if loss else "hf001_round_trip_net"][i]
                - r["hf001_round_trip_loss_baseline" if loss else "hf001_round_trip_baseline"][i]
                for r in actual) / len(keep) if keep else 0. for i, m in enumerate(MULTIPLIERS)}})
    return rows


def random_controls(cohorts, rows, variant, *, loss=False):
    rng, digest, means, diagnostics = np.random.default_rng(0), hashlib.sha256(), [], []
    for path in range(N_CONTROLS):
        differences, fills_total, cash_total, fill_dates = [], 0, 0, Counter()
        requests_total = 0
        incomplete_total, fallback_total = 0, 0
        for cohort, row in zip(cohorts, rows):
            actual = [r["first_attempt_day"] for r in cohort["records"][variant] if r["first_attempt_day"] is not None]
            requests_total += len(actual)
            chosen = rng.choice(cohort["filled"], size=len(actual), replace=False).astype(int)
            order = rng.permutation(len(actual))
            assigned = [actual[i] for i in order]
            for number, attempt in zip(chosen, assigned):
                cache_exit(cohort, int(number), attempt)
            digest.update((json.dumps([path, row["month"], [cohort["codes"][i] for i in chosen], assigned], separators=(",", ":")) + "\n").encode())
            attempts = np.zeros(len(cohort["codes"]), dtype=int)
            attempts[chosen] = assigned
            table = cohort["loss_nets" if loss else "nets"]
            net = table[np.arange(len(attempts)), attempts, 0]
            fills = cohort["fills"][np.arange(len(attempts)), attempts]
            incomplete_total += int(cohort["incomplete_adv"][np.arange(len(attempts)), attempts].sum())
            fallback_total += int(cohort["raw_fallback"][np.arange(len(attempts)), attempts].sum())
            fills_total += int((fills > 0).sum())
            cash_total += int(np.where(fills > 0, 21 - fills, 0).sum())
            for fill_day in fills[fills > 0]:
                fill_dates[cohort["holding_dates"][fill_day - 1]] += 1
            if row["valid"]:
                differences.append((float(net.mean()) if len(net) else 0.) - row["costs"]["1.0"]["baseline_net"])
        if differences:
            means.append(float(np.mean(differences)))
        diagnostics.append({"early_fills": fills_total, "early_sale_cash_days": cash_total,
            "unfilled_requests": requests_total - fills_total, "fill_date_counts": dict(fill_dates),
            "sell_adv_incomplete": incomplete_total, "raw_price_fallback": fallback_total})
    actual = hf001._mean([r["costs"]["1.0"]["difference"] for r in rows if r["valid"]])
    actual_execution = {"early_fills": sum(r["early_fill"] for c in cohorts for r in c["records"][variant]),
        "early_sale_cash_days": sum(r["cash_days"] for c in cohorts for r in c["records"][variant]),
        "unfilled_requests": sum(r["unfilled_request"] for c in cohorts for r in c["records"][variant]),
        "fill_date_counts": dict(Counter(r["fill_date"] for c in cohorts for r in c["records"][variant] if r["early_fill"]))}
    control_dates = set(actual_execution["fill_date_counts"]).union(*(d["fill_date_counts"] for d in diagnostics))
    return {**signal_eval._control_summary(means, actual), "path_means": means, "paths_planned": N_CONTROLS,
        "seed": 0, "draw_order": "path/month/choice(sorted filled B)/permutation(m)", "draws_sha256": digest.hexdigest(),
        "path_execution": diagnostics,
        "actual_execution": actual_execution,
        "mean_control_minus_actual_execution": {
            **{key: float(np.mean([d[key] for d in diagnostics])) - actual_execution[key]
               for key in ("early_fills", "early_sale_cash_days", "unfilled_requests")},
            "fill_date_counts": {day: float(np.mean([d["fill_date_counts"].get(day, 0) for d in diagnostics]))
                - actual_execution["fill_date_counts"].get(day, 0) for day in sorted(control_dates)}},
        "interpretation": "Conditional name selection/date assignment; not a market timing test."}


def variant_metrics(cohorts, variant, *, loss=False, diagnostics=True):
    rows = monthly(cohorts, variant, loss=loss)
    valid = [r for r in rows if r["valid"]]
    records = [r for c in cohorts for r in c["records"][variant]]
    reasons = Counter()
    for row in records:
        reasons.update(row["signal_uncomputable_by_reason"])
    result = {**ch001.summarize(rows), "per_month": rows,
        "random_control": random_controls(cohorts, rows, variant, loss=loss),
        "filtered_months": sum(r["early_fills"] > 0 for r in valid),
        "execution": {"requests": sum(r["first_attempt_day"] is not None for r in records),
            "early_fills": sum(r["early_fill"] for r in records), "unfilled_requests": sum(r["unfilled_request"] for r in records),
            "raw_price_fallback": sum(r["raw_price_fallback"] for r in records),
            "sell_adv_incomplete": sum(r["sell_adv_incomplete"] for r in records),
            "baseline_sell_adv_incomplete": sum(r["baseline"]["sell_adv_incomplete"] for r in records),
            "signal_uncomputable_by_reason": dict(reasons),
            "delay_sessions": dict(Counter(str(r["delay_sessions"]) for r in records if r["early_fill"])),
            "fill_day_distribution": dict(Counter(str(r["fill_day"]) for r in records if r["early_fill"])),
            "early_sale_cash_days": sum(r["cash_days"] for r in records),
            "post_fill_to_maturity_gross_mean": hf001._mean([r["post_fill_to_maturity_gross"] for r in records])},
        "mean_cash_weight_day_average": hf001._mean([r["cash_weight_day_average"] for r in valid]),
        "hf001_round_trip_d": {"used_for_judgement": False,
            **{str(m): hf001._mean([r["hf001_round_trip_d"][str(m)] for r in valid]) for m in MULTIPLIERS}}}
    for label, start, end in (("design", "2017-05", "2024-12"), ("validation", "2025-01", "2025-11")):
        result[label] = ch001.summarize([r for r in rows if start <= r["month"] <= end])
    a, b = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
    result.update(validation_year_same_sign=bool(np.sign(a) == np.sign(b)) if a is not None and b is not None else None,
                  validation_sign_used_for_judgement=False)
    if diagnostics:
        result["positions"] = records
        result["unfilled_entries"] = [{"month": c["selection"]["month"], "stock_code": code,
            "entry_date": str(pd.Timestamp(c["selection"]["trade_date"]).date()),
            "reason": c["group"].loc[code, "reason"], "signal_date": None, "request_date": None,
            "fill_date": None, "valuation_date": None}
            for c in cohorts for i, code in enumerate(c["codes"]) if i not in c["filled"]]
        result["by_year"] = {year: ch001.summarize([r for r in rows if r["month"].startswith(year)]) for year in sorted({r["month"][:4] for r in rows})}
        result["ban_period"] = {label: ch001.summarize([r for r in rows if ("2023-11-06" <= r["decision_date"] <= "2025-03-30") == inside]) for label, inside in (("inside", True), ("outside", False))}
        result["markets"] = {market: ch001.summarize(monthly(cohorts, variant, loss=loss, market=market)) for market in ("KOSPI", "KOSDAQ")}
        result["liquidity_buckets"] = {str(bucket): ch001.summarize(monthly(cohorts, variant, loss=loss, bucket=bucket)) for bucket in range(4)}
    return result


def judge_variants(variants, fields, trials):
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trials > 20 else criteria["core_t_stat_gt"]
    sufficient = {v: r["primary"]["count"] >= 36 and r["filtered_months"] >= 36
        and r["primary"]["t_stat"] is not None and r["primary"]["standard_error"] is not None
        and r["primary"]["standard_error"] > 0 and not r.get("unavailable") for v, r in variants.items()}
    corrected = ch001.holm({v: variants[v]["primary"]["p_one_sided"] if sufficient[v] else 1. for v in VARIANTS}, variants=VARIANTS)
    for v, result in variants.items():
        p, share = result["primary"], result["random_control"]["share_of_controls"]
        mean15 = result["cost_sensitivity"]["1.5"]["difference"]
        result.update(holm_p=corrected[v], judgement_inputs={**p, "filtered_months": result["filtered_months"],
            "p_for_holm": p["p_one_sided"] if sufficient[v] else 1., "holm_p": corrected[v],
            "t_threshold": float(threshold), "trial_count_after_run": trials, "mean_at_cost_1_5": mean15, "random_control_share": share})
        if result.get("unavailable"):
            continue
        if not sufficient[v] or mean15 is None or share is None:
            result.update(verdict="insufficient", reasons=["36 valid months and 36 actual early-fill months plus defined statistics required"])
            continue
        f, b = result["risk"]["filtered"], result["risk"]["baseline"]
        risk_only = p["mean"] <= 0 and f["volatility"] < b["volatility"] and f["lower_5pct_mean"] > b["lower_5pct_mean"]
        failures = []
        if p["mean"] <= 0: failures.append("mean d must exceed 0")
        if p["t_stat"] <= threshold: failures.append(f"NW t must exceed {threshold:g}")
        if corrected[v] > .05: failures.append("Holm(2) p must be <=0.05")
        if share > .05: failures.append("control share must be <=0.05")
        if mean15 <= 0: failures.append("cost x1.5 mean d must exceed 0")
        result.update(verdict="risk_only" if risk_only else "fail" if failures else "pass", reasons=failures)
    return variants


def evaluate_rule(cohorts, fields, trials):
    main = judge_variants({v: variant_metrics(cohorts, v) for v in VARIANTS}, fields, trials)
    losses = judge_variants({v: variant_metrics(cohorts, v, loss=True, diagnostics=False) for v in VARIANTS}, fields, trials)
    for v in VARIANTS:
        if main[v]["random_control"]["draws_sha256"] != losses[v]["random_control"]["draws_sha256"]:
            raise AssertionError("CH002 loss draw stream changed")
        main[v].update(base_verdict=main[v]["verdict"], delisting_loss_verdict=losses[v]["verdict"],
                       delisting_sensitive=main[v]["verdict"] != losses[v]["verdict"])
        if main[v]["delisting_sensitive"]:
            main[v].update(verdict="delisting_sensitive", reasons=["delisting loss changes verdict"])
    return main, losses


def publish(payload, *, repo_root=PROJECT_ROOT):
    _registration(repo_root)
    rows = []
    for v in VARIANTS:
        metrics = {"stage": "design_validation", "combined_verdict": payload["variants"][v]["verdict"],
            "rules": {r: {key: payload["rules"][r][v][key] for key in ("primary", "filtered_months", "verdict", "judgement_inputs")} for r in ch001.RULES},
            "delisting_verdicts": {r: payload["delisting_sensitivity"][r][v]["verdict"] for r in ch001.RULES}}
        experiment_registry.encode_metrics(metrics)
        rows.append((v, metrics))
    json.dumps(payload, allow_nan=False)
    lines = [f"# {EXPERIMENT_ID}", "", *[f"{v}: {payload['variants'][v]['verdict']}" for v in VARIANTS], "", *INTERPRETATIONS]
    # Pre-validate with a conservative maximum result filename before any write.
    for _, metrics in rows:
        experiment_registry.encode_metrics({**metrics, "result_file": "research/experiments/" + EXPERIMENT_ID + "/results/" + "0" * 32 + ".json.gz"})
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root, summary_lines=lines)
    for v, metrics in rows:
        metrics["result_file"] = str(paths[0].relative_to(repo_root))
        experiment_registry.record_trial(EXPERIMENT_ID, v, metrics, metrics["combined_verdict"], repo_root=repo_root,
            note="CH002 S/O, actual one-way v1 costs, NW(1), Holm(2); no holdout")
    return payload


def source_hashes(data_dir, price_paths, *, repo_root=PROJECT_ROOT, prices=None):
    hashes = ch001.source_hashes(data_dir, price_paths, repo_root=repo_root, prices=prices)
    hashes["code"]["backtesting/experiments/ch002.py"] = hf001._digest(Path(__file__))
    hashes["preregistrations"][EXPERIMENT_ID] = hf001._digest(Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md")
    return hashes


def compute_design(*, data_dir, repo_root, prices=None, sessions=None, _attempt=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    injected = prices is not None
    sessions, _ = ch001.calendar_plan(sessions)
    if prices is not None and (prices.empty or common._sessions(prices).max() > ch001.PRICE_END):
        raise ValueError("CH001 requires nonempty evaluation prices strictly before 2026")
    paths = [] if injected else ch001.d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    hashes = source_hashes(data_dir, paths, repo_root=repo_root, prices=prices)
    if _attempt is not None:
        _attempt["source_hashes"] = hashes
    prices, paths, sessions, prepared = ch001.prepare_inputs(data_dir=data_dir, repo_root=repo_root,
        prices=prices, sessions=sessions, attach_charts=False)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 2
    rules, losses, assertions = {}, {}, {}
    for rule in ch001.RULES:
        reason = "data_incomplete" if not prepared["raise_complete"] else "sensitivity_unavailable" if rule == "O" and not prepared["sensitivity_available"] else None
        if reason:
            rules[rule] = judge_variants({v: ch001.unavailable_metrics(reason) for v in VARIANTS}, registration.fields, trials)
            losses[rule] = judge_variants({v: ch001.unavailable_metrics(reason) for v in VARIANTS}, registration.fields, trials)
            continue
        selections = prepared["selections"][rule]
        observations = ch001.forward_observations(prices, selections, sessions, repo_root=repo_root)
        assertions[rule] = ch001.assert_filters_off(observations, selections)
        cohorts = build_cohorts(prices, observations, selections, sessions)
        rules[rule], losses[rule] = evaluate_rule(cohorts, registration.fields, trials)
        assertions[rule]["disabled_exit_HF001_gross_equal"] = True
        del cohorts, observations
        ch001._check_memory()
    variants = {v: {"verdict": av001.combine_verdict(rules["S"][v]["verdict"], rules["O"][v]["verdict"], sensitivity_available=prepared["sensitivity_available"])} for v in VARIANTS}
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "design_validation", "variants": variants,
        "rules": rules, "delisting_sensitivity": losses, "assertions": assertions,
        "prereg_commit": registration.commit_hash, "preregistration_sha256": hashes["preregistrations"][EXPERIMENT_ID],
        "reference_sha256": REFERENCE_SHA256, "source_hashes": hashes,
        "code_commit": experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
        "library_versions": {"python": ch001.platform.python_version(), **{n: ch001.importlib.metadata.version(n) for n in ("numpy", "pandas", "exchange-calendars", "PyYAML")}},
        **{key: prepared[key] for key in ("counts", "filing_rules", "L", "version_provenance", "date_rule_differences", "listing_status", "coverage", "integrity_price_access", "event_counts")},
        "selections": {r: [{"month": s["month"], "U": sorted(s["universe"].index), "P": sorted(s["P"]), "B": sorted(s["B"]), "F_vol": sorted(s["F_vol"]), "F_raise": sorted(s["F_raise"])} for s in prepared["selections"][r]] for r in ch001.RULES},
        "interpretations": INTERPRETATIONS, "performance": {"runtime_seconds": time.perf_counter() - started,
            "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    final_paths = [] if injected else ch001.d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    if hashes != source_hashes(data_dir, final_paths, repo_root=repo_root, prices=prices if injected else None):
        raise ValueError("CH002 inputs or code changed during execution; publication aborted")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, VARIANTS, repo_root) as attempt:
            payload = compute_design(data_dir=data_dir, repo_root=repo_root, prices=prices, sessions=sessions, _attempt=attempt)
            return publish(payload, repo_root=repo_root)


def fixture_timing():
    days = pd.bdate_range("2024-01-02", periods=70)
    codes = [f"{i * 10:06d}" for i in range(1, 33)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000., "close": 10000., "base_price": 10000., "volume": 100.,
        "trading_value": 2e9, "ret_1d": 0., "calendar_status": "verified", "market": "KOSPI"}, index=index)
    # Cover all 19 first-attempt days so timing includes the largest exit table.
    for pos, code in enumerate(codes):
        signal_day = pos % 19 + 1
        prices.loc[(days[30 + signal_day], code), "ret_1d"] = -.11
        if signal_day == 1:
            prices.loc[(days[31], code), "close"] = 8900.
    u = pd.DataFrame({"stock_code": codes, "decision_date": days[30], "trade_date": days[31],
        "avg_trading_value_20d": 2e9, "phase": 2, "decision_market": "KOSPI"})
    s = {"month": "2024-02", "decision_date": str(days[30].date()), "trade_date": str(days[31].date()),
        "universe": u.set_index("stock_code"), "B": set(codes)}
    started = time.perf_counter()
    observed = hf001.forward_observations(prices, u, days)
    forward_seconds = time.perf_counter() - started
    started = time.perf_counter()
    cohorts = build_cohorts(prices, observed, [s], days)
    build_seconds = time.perf_counter() - started
    started = time.perf_counter()
    random_controls(cohorts, monthly(cohorts, VARIANTS[0]), VARIANTS[0])
    warm_seconds = time.perf_counter() - started
    started = time.perf_counter()
    random_controls(cohorts, monthly(cohorts, VARIANTS[0]), VARIANTS[0])
    cached_seconds = time.perf_counter() - started
    return {"stocks": len(codes), "forward_seconds": forward_seconds,
        "exit_seconds": build_seconds + max(0., warm_seconds - cached_seconds),
        "build_actual_exit_seconds": build_seconds, "cache_warm_control_seconds": warm_seconds,
        "request_day_pairs_computed": int(cohorts[0]["computed"][:, 2:].sum()),
        "control_seconds": cached_seconds, "months": 1, "paths": N_CONTROLS}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    registration = _registration(repo_root)
    started = time.perf_counter()
    sessions, planned = ch001.calendar_plan()
    paths = ch001.d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    status, coverage = av001.coverage_plan(data_dir, planned, sessions)
    dates = av001.financial_dates.FilingDates.from_data(data_dir)
    audit = av001.financial_dates.FinancialVersions(data_dir, dates, "S", "design_validation", derive=False, sample_end=planned[-1]["decision_date"])
    available = pd.DatetimeIndex([day for day, _ in paths])
    timing = fixture_timing()
    measured = time.perf_counter() - started
    preparation_budget = 1800.0
    estimated = measured + preparation_budget + (timing["exit_seconds"] + timing["forward_seconds"]) * 2 * 103 * 4000 / timing["stocks"] + timing["control_seconds"] * 4000 / timing["stocks"] * 103 * 8
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash,
        "reference_sha256": REFERENCE_SHA256, "preregistration_sha256": hf001._digest(Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md"),
        "planned_months": len(planned), "price_files": len(paths), "read_start": str(sessions[0].date()), "read_end": str(sessions[-1].date()),
        "price_covered_months": sum(not len(sessions[sessions.get_loc(pd.Timestamp(p["decision_date"])) - 59:sessions.get_loc(pd.Timestamp(p["exit_date"])) + 1].difference(available)) for p in planned),
        "listing_incomplete_months": sum(not r["listing"]["covered"] for r in coverage), "listing_status": status,
        "financial_dates": audit.report(), "L": dates.L,
        "integrity_price_access": ch001.integrity_price_access(data_dir, status), "fixture_timing": timing,
        "projection": {"measured_dry_run_seconds": measured, "measured_peak_rss_mib": peak,
            "assumed_price_financial_preparation_seconds": preparation_budget,
            "full_run_seconds_range": [estimated, estimated * 3 + 900], "peak_rss_mib_estimate": peak + 4096,
            "basis": "Synthetic table covering all 19 request days scaled to 4000 U/B names, 103 months and 2 rules; 8 loss/variant control evaluations. Price loading/financial derivation has an assumed 1800s budget, not measured here. Add 4 GiB prices/tables/serialization reserve; heuristic, not a measured bound."},
        "interpretations": INTERPRETATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    result = run_experiment() if args.execute else dry_run()
    output = result["variants"] if args.execute else {k: v for k, v in result.items() if k not in
        ("listing_status", "financial_dates", "integrity_price_access", "interpretations")}
    if not args.execute and "listing_status" in result:
        output["listing_supplement_complete"] = result["listing_status"]["supplement_complete"]
        output["O_sensitivity_available"] = result["financial_dates"]["sensitivity_available"]
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
