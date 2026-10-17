"""CH003 historical index MA200 cash filter; offline dry run by default."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np
import pandas as pd

from backtesting import experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, ch001, common, hf001, ordering

EXPERIMENT_ID = "CH003_market_regime_ma"
VARIANT = "r_ma200"
VARIANTS = (VARIANT,)
MARKETS = ("KOSPI", "KOSDAQ")
INDEX_NAMES = dict(zip(MARKETS, ("코스피", "코스닥")))
MONTHS = tuple(str(m) for m in pd.period_range("2017-05", "2025-11", freq="M"))
REFERENCE_SHA256 = "80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f"
LAGS = (1, 6, 12)
N_CONTROLS = 200
INTERPRETATIONS = [
    "Historical reanalysis using latest available_at per exact broad-market series/name/day. Availability is a saved-version timestamp, never a contemporaneous disclosure gate. All versions at the same timestamp must agree in close, including superseded versions. 2026 index rows are counted but excluded from every MA input.",
    "MA200 uses the decision-inclusive 200 contiguous XKRX sessions with finite positive closes. A missing/invalid close disables that market's filter for reporting; any uncomputable market-month in fixed T makes primary insufficient. Equality to MA is not bearish.",
    "B equal weights are fixed before entry. Bearish-market slots become zero-return/zero-cost cash; other slots retain 1/|B|. An active month needs at least one would-be-filled B name changed to cash; already-unfilled slots do not count.",
    "T is exactly 2017-05..2025-11, 103 months. No gaps are dropped or compressed in primary statistics or shifted paths. Empty B with nonempty U is a valid zero; an empty U also makes the primary unavailable. Filter-off diagnostics may still be reported on valid months.",
    "Every nonzero positive np.roll shift moves the two-market vector jointly, preserving contemporaneous joint bearish months. The share count/(N-1) is not a rank-test p-value; (count+1)/N includes the actual path and is reported separately. Circular wrapping creates an artificial end/start spell.",
    "Auxiliary independent-month controls use one default_rng(0) per rule, path then KOSPI then KOSDAQ, drawing k computable month positions without replacement even at k=0/all. Diagnostic only; loss reruns reproduce the same draws.",
    "NW uses Bartlett lags 1,6,12, divisors N, no finite-sample correction and normal upper tails. All three must meet strict t>2 (3 after 20 trials) and p<=.05; no lag selection. 36 valid AND 36 active months are required; generic 300 events adds no gate.",
    "Risk-only requires nonpositive mean d, lower sample volatility, higher worst-ceil(5%*N) mean and lower stitched maximum drawdown. Year/ban splits use decision dates; market and P diagnostics rerun fixed-slot cash filtering within their own equal-weight baskets.",
    "HF001 entry-date full round-trip v1 accounting and its delisting-loss costs are retained; no CH002 one-way accounting is used. x1.5 must have positive d. S and O must both pass; version/sensitivity/delisting/coverage precedence reuses CH001/AV001.",
    "Spells are linear contiguous bearish runs on T, not circular, with missing regimes breaking runs. Active-month baseline mean is the full B return in those months, not a conditional sub-basket return.",
    "A pass is approximate inference meeting three lag criteria, not proof of enough independent bearish events or alpha. 2025 is a stability diagnostic and forward reproduction needs a new ID.",
    "Dry run audits real index computability, bounded price-file coverage and listing/date metadata without real portfolio evaluation, publication or holdout. Synthetic performance estimates are not measured execution bounds; 2026 supplement integrity hashes are not evaluation inputs.",
    "Execution uses ordering.execution_lock(repo_root) across preflight, trial-count lookup, judgement, publication and registry appends. After preflight hashes are fixed, exceptions record one design_aborted row per registered variant through ordering.design_attempt; failures before hashes record no attempt. Real execution must run in the main checkout after merge so the directory lock and registry are shared with other research runs.",
    "Input and code SHA-256 are fixed before preparation reads/evaluation and re-inventoried immediately before publication. A changed input, inventory or code aborts with the original hash category digests and no verdict publication. Price-content hashing is bounded through 2025; protected January-June 2026 price files are never opened.",
]


def _registration(repo_root):
    reference = Path(repo_root) / "research/experiments" / ch001.EXPERIMENT_ID / "preregistration.md"
    if hf001._digest(reference) != REFERENCE_SHA256:
        raise ValueError("CH003 CH001 reference SHA-256 mismatch; stopped before data access")
    ch001._registration(repo_root)
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if registration.fields["variants_planned"] != 1 or registration.fields["uses_holdout"] or registration.fields["uses_llm"]:
        raise ValueError("CH003 requires one fixed variant, no holdout and no LLM")
    return registration


def load_indices(path):
    """No as_of filter; select latest saved version, detecting timestamp conflicts."""
    versions, latest, ignored = {}, {}, Counter()
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            market = row.get("series")
            if market not in MARKETS or row.get("index_name") != INDEX_NAMES[market]:
                ignored["other_series_or_name"] += 1
                continue
            day = pd.Timestamp(row["trade_date"])
            if pd.isna(day) or day.tz is not None or day != day.normalize():
                raise ValueError(f"CH003 index trade_date must be a date: line {number}")
            if day > pd.Timestamp("2025-11-30"):
                ignored["after_final_decision"] += 1
                if day.year == 2026:
                    ignored["2026_rows_not_used"] += 1
                continue
            at = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError("CH003 index available_at requires an aware timestamp")
            close = float(row["close"]) if row.get("close") is not None else np.nan
            key = (market, INDEX_NAMES[market], day)
            version = (*key, at)
            if version in versions and not (versions[version] == close or (np.isnan(versions[version]) and np.isnan(close))):
                raise ValueError(f"CH003 index version conflict: {market} {day.date()} {at.isoformat()}")
            versions[version] = close
            if key not in latest or at > latest[key][0]:
                latest[key] = (at, close)
    records = [{"trade_date": key[2], "market": key[0], "close": value[1], "available_at": value[0].isoformat()} for key, value in latest.items()]
    frame = pd.DataFrame(records, columns=["trade_date", "market", "close", "available_at"]).set_index(["trade_date", "market"]).sort_index()
    return frame, {"selected_rows": len(frame), "versions_checked": len(versions), "ignored": dict(ignored), "sha256": hf001._digest(path)}


def market_regimes(indices, planned, sessions):
    result = []
    for plan in planned:
        day = pd.Timestamp(plan["decision_date"])
        pos = sessions.get_loc(day)
        window = sessions[max(0, pos - 199):pos + 1]
        markets = {}
        for market in MARKETS:
            closes = indices.close.reindex(pd.MultiIndex.from_product([window, [market]], names=["trade_date", "market"])).to_numpy(dtype=float)
            invalid = ~np.isfinite(closes) | (closes <= 0)
            computable = len(window) == 200 and not invalid.any()
            ma = float(closes.mean()) if computable else None
            if ma is not None and (not np.isfinite(ma) or ma <= 0):
                raise ValueError("CH003 nonfinite MA200 arithmetic")
            markets[market] = {"computable": bool(computable), "bearish": bool(closes[-1] < ma) if computable else False,
                "close": float(closes[-1]) if len(closes) and np.isfinite(closes[-1]) else None,
                "ma200": ma, "invalid_sessions": int(invalid.sum()),
                "reason": None if computable else "short_calendar" if len(window) != 200 else "missing_or_invalid_close"}
        result.append({"month": plan["month"], "decision_date": plan["decision_date"], "markets": markets})
    return result


def regime_summary(regimes):
    result = {}
    for market in MARKETS:
        lengths, current = [], 0
        for row in regimes:
            if row["markets"][market]["bearish"]:
                current += 1
            elif current:
                lengths.append(current)
                current = 0
        if current:
            lengths.append(current)
        result[market] = {"bearish_months": sum(r["markets"][market]["bearish"] for r in regimes),
            "uncomputable_months": sum(not r["markets"][market]["computable"] for r in regimes),
            "spell_count": len(lengths), "spell_lengths": lengths, "max_spell_length": max(lengths) if lengths else 0}
    result["joint_bearish_months"] = sum(all(r["markets"][m]["bearish"] for m in MARKETS) for r in regimes)
    return result


def _difference(net, markets, bearish):
    baseline = float(net.mean()) if len(net) else 0.
    cash = np.array([bearish[MARKETS.index(market)] for market in markets], dtype=bool)
    filtered = float(np.where(cash, 0., net).mean()) if len(net) else 0.
    return filtered, baseline, filtered - baseline


def monthly(observations, selections, regimes, *, loss=False, scope="B", market=None):
    if [s["month"] for s in selections] != [r["month"] for r in regimes]:
        raise ValueError("CH003 selections and regimes must have identical ordered months")
    groups = {day: group.set_index("stock_code", drop=False) for day, group in observations.groupby("decision_date", sort=True)}
    rows = []
    gross = "gross_delisting_loss" if loss else "gross"
    for selection, regime in zip(selections, regimes):
        group = groups.get(pd.Timestamp(selection["decision_date"]), observations.iloc[:0].set_index("stock_code", drop=False))
        if market is not None:
            group = group.loc[group.decision_market.eq(market)]
        selected = group.loc[sorted(set(group.index) & selection[scope])]
        if not selected.decision_market.isin(MARKETS).all():
            raise ValueError("CH003 requires decision-date KOSPI/KOSDAQ membership")
        markets = selected.decision_market.to_numpy()
        flags = [regime["markets"][m]["bearish"] for m in MARKETS]
        cash = np.array([flags[MARKETS.index(m)] for m in markets], dtype=bool)
        costs = {}
        for m in ch001.MULTIPLIERS:
            net = (selected[gross] - selected[f"cost_{m}"]).to_numpy(dtype=float)
            filtered, baseline, difference = _difference(net, markets, flags)
            universe = float((group[gross] - group[f"cost_{m}"]).mean()) if len(group) else 0.
            costs[str(m)] = {"filtered_net": filtered, "baseline_net": baseline, "difference": difference,
                "universe_net": universe, "universe_excess": filtered - universe,
                "filtered_cost": float(np.where(cash, 0., selected[f"cost_{m}"]).mean()) if len(selected) else 0.,
                "baseline_cost": float(selected[f"cost_{m}"].mean()) if len(selected) else 0.}
        rows.append({"month": selection["month"], "decision_date": selection["decision_date"], "valid": bool(len(group)),
            "B": list(selected.index), "cash_names": list(selected.index[cash]),
            "weights": {code: 1 / len(selected) for code in selected.index},
            "cash_weight": float((cash | ~selected.filled.to_numpy(dtype=bool)).mean()) if len(selected) else 1.,
            "active": bool((cash & selected.filled.to_numpy(dtype=bool)).any()), "costs": costs,
            "removed_minus_remaining": None,
            "slot_net": (selected[gross] - selected["cost_1.0"]).tolist(), "slot_markets": markets.tolist(),
            "slot_filled": selected.filled.astype(bool).tolist()})
    return rows


def _path(rows, flags):
    differences, active = [], 0
    for row, bearish in zip(rows, flags):
        differences.append(_difference(np.asarray(row["slot_net"]), row["slot_markets"], bearish)[2])
        active += any(filled and bearish[MARKETS.index(market)] for filled, market in zip(row["slot_filled"], row["slot_markets"]))
    return differences, active


def circular_controls(rows, regimes):
    flags = np.array([[r["markets"][m]["bearish"] for m in MARKETS] for r in regimes], dtype=bool)
    actual = float(np.mean(_path(rows, flags)[0])) if rows else None
    paths = []
    for shift in range(1, len(rows)):
        values, active = _path(rows, np.roll(flags, shift, axis=0))
        paths.append({"shift": shift, "mean": float(np.mean(values)), "active_months": active,
            "months": len(values), "nw": {str(lag): ch001.newey_west(values, lag=lag) for lag in LAGS}})
    count = sum(p["mean"] >= actual for p in paths) if actual is not None else 0
    return {"actual_mean": actual, "count_ge_actual": count, "paths": paths,
        "share_of_controls": count / len(paths) if paths else None,
        "self_inclusive_rank_share": (count + 1) / len(rows) if rows else None,
        "interpretation": "count/(N-1) is the share among nonzero joint shifts, not a rank-test p-value; (count+1)/N includes the actual path.",
        "circular_boundary": "Last and first decision months are artificially adjacent."}


def auxiliary_controls(rows, regimes):
    rng, digest, means = np.random.default_rng(0), hashlib.sha256(), []
    for path in range(N_CONTROLS):
        flags = np.zeros((len(rows), 2), dtype=bool)
        for col, market in enumerate(MARKETS):
            candidates = [i for i, row in enumerate(regimes) if row["markets"][market]["computable"]]
            k = sum(row["markets"][market]["bearish"] for row in regimes)
            drawn = rng.choice(candidates, size=k, replace=False).astype(int)
            flags[drawn, col] = True
            digest.update((json.dumps([path, market, drawn.tolist()], separators=(",", ":")) + "\n").encode())
        values, _ = _path(rows, flags)
        if values:
            means.append(float(np.mean(values)))
    actual = hf001._mean([r["costs"]["1.0"]["difference"] for r in rows])
    return {**signal_eval._control_summary(means, actual), "path_means": means, "seed": 0,
        "paths_planned": N_CONTROLS, "draw_order": "path/KOSPI/KOSDAQ", "draws_sha256": digest.hexdigest(), "used_for_judgement": False}


def variant_metrics(observations, selections, regimes, *, loss=False, diagnostics=True):
    rows = monthly(observations, selections, regimes, loss=loss)
    full_T = [row["month"] for row in rows] == list(MONTHS)
    complete = full_T and all(row["valid"] for row in rows) and all(r["markets"][m]["computable"] for r in regimes for m in MARKETS)
    report = ch001.summarize(rows)
    nw = {str(lag): ch001.newey_west([r["costs"]["1.0"]["difference"] for r in rows] if complete else [], lag=lag) for lag in LAGS}
    circular, auxiliary = circular_controls(rows, regimes), auxiliary_controls(rows, regimes)
    circular["used_for_judgement"] = complete
    result = {**report, "primary": nw["1"], "nw": nw, "complete_T": complete, "per_month": rows,
        "filtered_months": sum(r["active"] for r in rows if r["valid"]),
        "random_control": circular, "auxiliary_control": auxiliary,
        "active_month_baseline_mean": hf001._mean([r["costs"]["1.0"]["baseline_net"] for r in rows if r["valid"] and r["active"]])}
    if not complete:
        result["filter_off_diagnostic"] = {**report, "used_for_judgement": False,
            "reason": "T must be the full registered calendar with both markets computable and valid U each month"}
    for label, start, end in (("design", "2017-05", "2024-12"), ("validation", "2025-01", "2025-11")):
        result[label] = ch001.summarize([r for r in rows if start <= r["month"] <= end])
    a, b = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
    result.update(validation_year_same_sign=bool(np.sign(a) == np.sign(b)) if a is not None and b is not None else None,
                  validation_sign_used_for_judgement=False)
    if diagnostics:
        result["by_year"] = {year: ch001.summarize([r for r in rows if r["month"].startswith(year)]) for year in sorted({r["month"][:4] for r in rows})}
        result["ban_period"] = {label: ch001.summarize([r for r in rows if ("2023-11-06" <= r["decision_date"] <= "2025-03-30") == inside]) for label, inside in (("inside", True), ("outside", False))}
        result["markets"] = {market: ch001.summarize(monthly(observations, selections, regimes, loss=loss, market=market)) for market in MARKETS}
        reference = monthly(observations, selections, regimes, loss=loss, scope="P")
        result["reference_P"] = {**ch001.summarize(reference), "per_month": reference, "used_for_judgement": False}
    return result


def judge(result, fields, trials):
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trials > 20 else criteria["core_t_stat_gt"]
    nw = result["nw"]
    mean15, share = result["cost_sensitivity"]["1.5"]["difference"], result["random_control"]["share_of_controls"]
    result["judgement_inputs"] = {"nw": nw, "t_threshold": float(threshold), "trial_count_after_run": trials,
        "filtered_months": result["filtered_months"], "complete_T": result["complete_T"],
        "mean_at_cost_1_5": mean15, "random_control_share": share}
    if result.get("unavailable"):
        return result
    if (not result["complete_T"] or result["primary"]["count"] < 36 or result["filtered_months"] < 36
            or mean15 is None or share is None or any(r["t_stat"] is None or r["standard_error"] is None or r["standard_error"] <= 0 for r in nw.values())):
        result.update(verdict="insufficient", reasons=["Full computable T, 36 valid and 36 active months and defined NW at every lag required"])
        return result
    f, b, p = result["risk"]["filtered"], result["risk"]["baseline"], result["primary"]
    risk_only = p["mean"] <= 0 and f["volatility"] < b["volatility"] and f["lower_5pct_mean"] > b["lower_5pct_mean"] and f["max_drawdown"] < b["max_drawdown"]
    failures = []
    if p["mean"] <= 0: failures.append("mean d must exceed 0")
    if mean15 <= 0: failures.append("cost x1.5 mean d must exceed 0")
    if share > .05: failures.append("joint circular-shift share must be <=0.05")
    for lag, values in nw.items():
        if values["t_stat"] <= threshold or values["p_one_sided"] > .05:
            failures.append(f"NW({lag}) requires strict t>{threshold:g} and one-sided p<=.05")
    result.update(verdict="risk_only" if risk_only else "fail" if failures else "pass", reasons=failures)
    return result


def unavailable_metrics(reason):
    return {**ch001.unavailable_metrics(reason), "nw": {str(lag): ch001.newey_west([], lag=lag) for lag in LAGS}, "complete_T": False}


def evaluate_rule(observations, selections, regimes, fields, trials):
    main = judge(variant_metrics(observations, selections, regimes), fields, trials)
    loss = judge(variant_metrics(observations, selections, regimes, loss=True, diagnostics=False), fields, trials)
    if main["auxiliary_control"]["draws_sha256"] != loss["auxiliary_control"]["draws_sha256"]:
        raise AssertionError("CH003 loss auxiliary draw stream changed")
    main.update(base_verdict=main["verdict"], delisting_loss_verdict=loss["verdict"], delisting_sensitive=main["verdict"] != loss["verdict"])
    if main["delisting_sensitive"]:
        main.update(verdict="delisting_sensitive", reasons=["delisting loss changes verdict"])
    return main, loss


def publish(payload, *, repo_root=PROJECT_ROOT):
    _registration(repo_root)
    metrics = {"stage": "design_validation", "combined_verdict": payload["variants"][VARIANT]["verdict"],
        "rules": {r: {k: payload["rules"][r][VARIANT][k] for k in ("primary", "nw", "filtered_months", "verdict", "judgement_inputs")} for r in ch001.RULES},
        "delisting_verdicts": {r: payload["delisting_sensitivity"][r][VARIANT]["verdict"] for r in ch001.RULES}}
    experiment_registry.encode_metrics({**metrics, "result_file": "research/experiments/" + EXPERIMENT_ID + "/results/" + "0" * 32 + ".json.gz"})
    json.dumps(payload, allow_nan=False)
    lines = [f"# {EXPERIMENT_ID}: {metrics['combined_verdict']}", "", *INTERPRETATIONS]
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root, summary_lines=lines)
    metrics["result_file"] = str(paths[0].relative_to(repo_root))
    experiment_registry.record_trial(EXPERIMENT_ID, VARIANT, metrics, metrics["combined_verdict"], repo_root=repo_root,
        note="CH003 S/O, NW(1/6/12), joint circular shifts; no holdout")
    return payload


def source_hashes(data_dir, price_paths, *, repo_root=PROJECT_ROOT, prices=None):
    hashes = ch001.source_hashes(data_dir, price_paths, repo_root=repo_root, prices=prices)
    hashes["code"]["backtesting/experiments/ch003.py"] = hf001._digest(Path(__file__))
    hashes["preregistrations"][EXPERIMENT_ID] = hf001._digest(Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md")
    hashes["index"] = {"market_context/benchmarks.jsonl": hf001._digest(Path(data_dir) / "market_context/benchmarks.jsonl")}
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
    selections = prepared["selections"]
    if any([s["month"] for s in selections[r]] != list(MONTHS) for r in ch001.RULES):
        raise ValueError("CH003 cannot change, drop or compress its registered decision-month T")
    index_path = Path(data_dir) / "market_context/benchmarks.jsonl"
    indices, inventory = load_indices(index_path)
    regimes = market_regimes(indices, selections["S"], sessions)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 1
    rules, losses, assertions = {}, {}, {}
    for rule in ch001.RULES:
        reason = "data_incomplete" if not prepared["raise_complete"] else "sensitivity_unavailable" if rule == "O" and not prepared["sensitivity_available"] else None
        if reason:
            rules[rule] = {VARIANT: judge(unavailable_metrics(reason), registration.fields, trials)}
            losses[rule] = {VARIANT: judge(unavailable_metrics(reason), registration.fields, trials)}
            continue
        observations = ch001.forward_observations(prices, selections[rule], sessions, repo_root=repo_root)
        assertions[rule] = ch001.assert_filters_off(observations, selections[rule])
        main, loss = evaluate_rule(observations, selections[rule], regimes, registration.fields, trials)
        rules[rule], losses[rule] = {VARIANT: main}, {VARIANT: loss}
        del observations
        ch001._check_memory()
    verdict = av001.combine_verdict(rules["S"][VARIANT]["verdict"], rules["O"][VARIANT]["verdict"], sensitivity_available=prepared["sensitivity_available"])
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "design_validation", "variants": {VARIANT: {"verdict": verdict}},
        "rules": rules, "delisting_sensitivity": losses, "assertions": assertions, "regimes": regimes,
        "regime_summary": regime_summary(regimes), "index_inventory": inventory,
        "prereg_commit": registration.commit_hash, "preregistration_sha256": hashes["preregistrations"][EXPERIMENT_ID],
        "reference_sha256": REFERENCE_SHA256, "source_hashes": hashes,
        "code_commit": experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
        "library_versions": {"python": ch001.platform.python_version(), **{n: ch001.importlib.metadata.version(n) for n in ("numpy", "pandas", "exchange-calendars", "PyYAML")}},
        **{k: prepared[k] for k in ("counts", "filing_rules", "L", "version_provenance", "date_rule_differences", "listing_status", "coverage", "integrity_price_access", "event_counts")},
        "selections": {r: [{"month": s["month"], "U": sorted(s["universe"].index), "P": sorted(s["P"]), "B": sorted(s["B"]), "F_vol": sorted(s["F_vol"]), "F_raise": sorted(s["F_raise"])} for s in selections[r]] for r in ch001.RULES},
        "interpretations": INTERPRETATIONS, "performance": {"runtime_seconds": time.perf_counter() - started,
            "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    final_paths = [] if injected else ch001.d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    if hashes != source_hashes(data_dir, final_paths, repo_root=repo_root, prices=prices if injected else None):
        raise ValueError("CH003 inputs or code changed during execution; publication aborted")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, VARIANTS, repo_root) as attempt:
            payload = compute_design(data_dir=data_dir, repo_root=repo_root, prices=prices, sessions=sessions, _attempt=attempt)
            return publish(payload, repo_root=repo_root)


def fixture_timing():
    rng = np.random.default_rng(1)
    rows, regimes = [], []
    for month in MONTHS:
        net = rng.normal(0, .1, 128)
        markets = [MARKETS[i % 2] for i in range(128)]
        flags = [bool(rng.integers(2)), bool(rng.integers(2))]
        rows.append({"slot_net": net.tolist(), "slot_markets": markets, "slot_filled": [True] * 128,
                     "costs": {"1.0": {"difference": _difference(net, markets, flags)[2]}}})
        regimes.append({"markets": {m: {"computable": True, "bearish": flags[i]} for i, m in enumerate(MARKETS)}})
    started = time.perf_counter()
    circular_controls(rows, regimes)
    circular_seconds = time.perf_counter() - started
    started = time.perf_counter()
    auxiliary_controls(rows, regimes)
    return {"stocks": 128, "months": len(rows), "circular_paths": len(rows) - 1, "auxiliary_paths": N_CONTROLS,
        "circular_seconds": circular_seconds, "auxiliary_seconds": time.perf_counter() - started}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    registration = _registration(repo_root)
    started = time.perf_counter()
    sessions, planned = ch001.calendar_plan()
    if [r["month"] for r in planned] != list(MONTHS):
        raise ValueError("CH003 dry run requires its full registered T")
    paths = ch001.d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    price_days = pd.DatetimeIndex([day for day, _ in paths])
    status, coverage = av001.coverage_plan(data_dir, planned, sessions)
    dates = av001.financial_dates.FilingDates.from_data(data_dir)
    audit = av001.financial_dates.FinancialVersions(data_dir, dates, "S", "design_validation", derive=False, sample_end=planned[-1]["decision_date"])
    indices, inventory = load_indices(Path(data_dir) / "market_context/benchmarks.jsonl")
    regimes = market_regimes(indices, planned, sessions)
    timing, forward = fixture_timing(), ch001.fixture_timing()
    measured = time.perf_counter() - started
    preparation_budget = 1800.0
    estimated = measured + preparation_budget + (timing["circular_seconds"] + timing["auxiliary_seconds"]) * 4000 / 128 * 4 + forward["forward_seconds"] * 4000 * 103 * 2 / forward["stocks"]
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash, "reference_sha256": REFERENCE_SHA256,
        "preregistration_sha256": hf001._digest(Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md"),
        "planned_months": len(planned), "price_files": len(paths), "read_start": str(sessions[0].date()), "read_end": str(sessions[-1].date()),
        "price_covered_months": sum(not len(sessions[sessions.get_loc(pd.Timestamp(p["decision_date"])) - 59:sessions.get_loc(pd.Timestamp(p["exit_date"])) + 1].difference(price_days)) for p in planned),
        "listing_status": status, "listing_incomplete_months": sum(not r["listing"]["covered"] for r in coverage),
        "financial_dates": audit.report(), "L": dates.L, "index_inventory": inventory,
        "regimes": regimes, "regime_summary": regime_summary(regimes),
        "primary_data_complete": all(r["markets"][m]["computable"] for r in regimes for m in MARKETS),
        "integrity_price_access": ch001.integrity_price_access(data_dir, status), "fixture_timing": timing,
        "projection": {"measured_dry_run_seconds": measured, "measured_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "assumed_price_financial_preparation_seconds": preparation_budget,
            "full_run_seconds_range": [estimated, estimated * 3 + 900],
            "peak_rss_mib_estimate": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 + 4096,
            "basis": "Synthetic 103-month circular/auxiliary controls scaled from 128 to 4000 names, 2 rules and 2 valuations; synthetic HF001 entries scaled to U cohorts. Price loading/financial derivation has an assumed 1800s budget, not measured here. Add 4 GiB price/results reserve; heuristic only."},
        "interpretations": INTERPRETATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    result = run_experiment() if args.execute else dry_run()
    output = result["variants"] if args.execute else {k: v for k, v in result.items() if k not in
        ("regimes", "listing_status", "financial_dates", "integrity_price_access", "interpretations")}
    if not args.execute and "listing_status" in result:
        output["listing_supplement_complete"] = result["listing_status"]["supplement_complete"]
        output["O_sensitivity_available"] = result["financial_dates"]["sensitivity_available"]
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
