"""CH004 fixed chart exclusions on EV001 ev_new20; offline dry run by default."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, ch001, common, ev001, hc003, ordering
from src.ingestion import dart_listing


EXPERIMENT_ID = "CH004_event_chart_filters"
VARIANTS = ("e_anchor", "e_runup")
RULES = ("S", "O")
N_CONTROLS = 200
PRICE_END = pd.Timestamp("2025-12-30")
REFERENCE_HASHES = {
    EXPERIMENT_ID: "649848533003559651e541e4be69bf002402e253eadb4ac92b2df23378583f33",
    ev001.EXPERIMENT_ID: "383f306d52b74df71946ce0da08e335e95f1f5f5db947a9ec64dd8f81998874f",
    ch001.EXPERIMENT_ID: "80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f",
}
EV001_CODE_SHA256_ALLOWLIST = {
    "1063af57c51f07af9f28f96b523aede7ae10d44ed9317d3f03705f708be605e6",
    "e101b5ac04c8c56d588f7d3fbea8ad8a4a879b416520f81337e7c83ae50884db",
}
INTERPRETATIONS = [
    "Exploratory event-selection information, not an executable replacement portfolio or demonstrated profitability. Removed slots earn the unchanged universe benchmark without additional costs. Forward reproduction requires a new ID; no holdout is used.",
    "EV001 prepare/select_events/outcome_tables/observations supply both rules' final ev_new20 sets, pre-entry-feasibility V_tau and all four cost scenarios, including their -100% delisting counterparts. These are copied and recomputed/asserted before any chart filter; overlap selection is never rerun after filtering.",
    "EV001 source is restricted to the two approved SHA-256 versions at 08079e2 and f8c6845; the used hash is recorded in provenance. CH004, EV001 (7acd752) and CH001 (b53485e) preregistrations are immutable and content-pinned. No EV001 saved results or EV001 registry verdicts are consulted.",
    "s is the last XKRX session <= public_date d. CH001 adjusted_values applies to exactly 252/21 session-aligned stored returns, including a valid first return, without price substitutes or filling. EV001's compact book does not distinguish an absent stock session from an unverified one; that combined invalidity is reported as unverified_or_missing_session.",
    "Ranks use the full frozen V_tau, before executable-entry restriction, and each stock's market at s. Only valid metrics with KOSPI/KOSDAQ at s enter market-specific max-tie ranks. Missing/out-of-scope markets are market_unavailable; uncomputable events stay and cannot be randomly removed.",
    "Anchor removes percentile<=.30 of A_s/max(A_252); runup removes percentile>.80 of A_s/A_(s-20)-1. Runup includes d's return when d is a session. Anchor shares CH001 C_anchor's percentile rule but uses event-date s and EV001 V_tau rather than CH001's decision-date U/B.",
    "d_m is minus the canonically ordered sum of removed x_i divided by every E_r event in that entry month. Months without removals remain zero observations; months without events are absent. Remaining/removed level summaries omit empty baskets and report their monthly means separately.",
    "Each rule/variant starts one default_rng(0) outside the 200-path loop; draws traverse path then ascending entry month over evaluable events sorted by entry date, stock code and event quarter. choice is called even for zero/all removals. Sorting drawn indices before summation makes exact ties count as >=. Loss reruns reproduce the draw digest.",
    "Calendar-month Bartlett NW(3), no finite-sample correction, and the normal upper tail reuse EV001's monthly inference. Holm always has two variants per rule; insufficient/unavailable variants supply p=1. At least 300 events, 36 event months and 36 removal months plus defined required statistics are necessary. Strict t>2 becomes t>3 when existing CH004 rows plus these two rows exceed 20.",
    "Costs are EV001's separately priced buy/sell v2 x1/1.5/2 and v1 x1, never a multiplier on x_i or sell tax. Positive x1.5 d is required but is mechanically implied by the fixed-set replacement interpretation, not evidence of strategy cost resilience.",
    "Both S and O must pass. EV001's unavailable O sensitivity and listing-supplement failure remain sensitivity_unavailable/data_incomplete. Last-close and -100% scenarios independently rerun controls and Holm; a changed per-rule verdict is delisting_sensitive. Combined precedence reuses AV001.",
    "All YAML secondary metrics are reporting only: remaining/removed monthly excess levels, computability/reasons, overlap, 1/5/10/20-session gross excess paths, v1/cost/delisting sensitivity, year/short-sale-regime/entry-market/liquidity splits and 2025 sign. A missing gross-path benchmark member disables the entire date/horizon diagnostic without shrinking V_tau's executable benchmark.",
    "All evaluation price reads and hashes stop at 2025-12-30. Listing metadata and L may include later filings. Bounded supplement verification inventories later price files but does not open them; frozen 2026 hashes are labelled separately and are not independently reverified or evaluation inputs.",
    "Execution holds ordering.execution_lock over preflight, hashes, preparation, counting, judgement and publication. Hashes are frozen before evaluation and reinventoried before publication; exceptions after freezing record two design_aborted rows. Successful compact rows and the full finite JSON are all validated before writes. The shared registry API cannot roll back an OS failure between appends.",
    "Dry run prepares EV001 selections and chart computability only, never event outcomes, inference or publication. Fixture timing and scaled runtime/RSS are estimates, not full-data execution measurements. The unchanged cost dependency is protected by refusing a code checkout containing .env/.env-ai, without reading either file.",
]


def _registration(repo_root):
    if any((PROJECT_ROOT / name).is_file() for name in (".env", ".env-ai")):
        raise ValueError("CH004 cannot start with .env/.env-ai present; no environment file was read")
    for name, checksum in REFERENCE_HASHES.items():
        path = Path(repo_root) / "research/experiments" / name / "preregistration.md"
        if hc003._digest(path) != checksum:
            raise ValueError(f"CH004 {name} reference SHA-256 mismatch; stopped before data access")
    if hc003._digest(Path(ev001.__file__)) not in EV001_CODE_SHA256_ALLOWLIST:
        raise ValueError("CH004 EV001 source differs from its approved 08079e2/f8c6845 versions")
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (not registration.commit_hash.startswith("08079e2") or registration.fields["variants_planned"] != 2
            or registration.fields["uses_holdout"] or registration.fields["uses_llm"]):
        raise ValueError("CH004 requires immutable 08079e2, two fixed variants, no holdout and no LLM")
    ev001._registration(repo_root)
    reference = experiment_registry.verify_preregistration(ch001.EXPERIMENT_ID, repo_root)
    if not reference.commit_hash.startswith("b53485e"):
        raise ValueError("CH004 requires CH001 b53485e")
    return registration


def calendar(sessions=None):
    days = signal_eval._dates(hc003.session_calendar() if sessions is None else sessions).sort_values().unique()
    days = days[(days >= "2015-01-01") & (days <= PRICE_END)]
    if not len(days):
        raise ValueError("CH004 requires an evaluation calendar ending by 2025-12-30")
    return days


def prepare_inputs(*, data_dir, repo_root, sessions, prices=None):
    status = dart_listing.listing_status(data_dir, price_end=PRICE_END)
    if not status["supplement_complete"]:
        return {"listing_status": status}
    return ev001.prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions,
                         stage="design_validation", prices=prices)


def _universes(prepared, rule):
    return {day: sorted(u.index) for day, u in prepared["universes"][rule].items()}


def event_key(row):
    return row["entry_date"], row["stock_code"], row["fiscal_quarter"]


def ev001_baseline(prepared, rule):
    selected, counts = ev001.select_events(prepared, rule, "ev_new20")
    tables = ev001.outcome_tables(prepared, rule, selected, 20)
    rows = ev001.observations(prepared["book"], selected, tables)
    return rows, counts


def freeze_baselines(prepared):
    available = prepared["versions"]["S"].sensitivity_available
    frozen, assertions = {}, {}
    for rule in RULES:
        if rule == "O" and not available:
            continue
        universes = _universes(prepared, rule)
        rows, counts = ev001_baseline(prepared, rule)
        frozen[rule] = {"events": copy.deepcopy(rows), "universes": universes, "counts": counts}
    # Complete every rule/scenario freeze and replay before attaching any chart.
    for rule, baseline in frozen.items():
        replay, _ = ev001_baseline(prepared, rule)
        assert_baseline(baseline, replay, _universes(prepared, rule))
        assertions[rule] = {"ev_new20_exact": True, "events": len(replay),
            "frozen_sha256": hc003._content_hash(baseline)}
    return frozen, assertions


def assert_baseline(frozen, replay, universes):
    if frozen["universes"] != universes:
        raise AssertionError("CH004 V_tau differs from EV001 before entry feasibility")
    if len({event_key(row) for row in replay}) != len(replay):
        raise AssertionError("CH004 EV001 event keys are not unique")
    keys = ("public_date", "entry_date", "scheduled_exit_date", "decision_date",
            "benchmark", "benchmark_loss", "excess", "excess_loss",
            *("excess_" + scenario + suffix for scenario in hc003.SCENARIOS for suffix in ("", "_loss")))
    left, right = sorted(frozen["events"], key=event_key), sorted(replay, key=event_key)
    if [event_key(row) for row in left] != [event_key(row) for row in right]:
        raise AssertionError("CH004 event keys differ from EV001 ev_new20")
    for saved, observed in zip(left, right):
        if saved["stock_code"] not in universes[saved["decision_date"]]:
            raise AssertionError("CH004 event absent from frozen V_tau")
        for key in keys:
            if saved[key] != observed[key]:
                raise AssertionError(f"CH004 EV001 ev_new20 mismatch: {event_key(saved)} {key}")
        if not all(np.isfinite(saved[key]) for key in keys if key.startswith("excess")):
            raise AssertionError("CH004 EV001 x_i must be finite")


def chart_filters(book, codes, public_date):
    codes = pd.Index(sorted(codes), name="stock_code")
    pos = book.days.searchsorted(pd.Timestamp(public_date), side="right") - 1
    if pos < 0:
        raise ValueError("CH004 public date precedes the evaluation calendar")
    markets = [{1: "KOSPI", 2: "KOSDAQ"}.get(int(book.market[code][pos])) if code in book.raw else None
               for code in codes]
    results = {}
    for variant, length, metric in (("e_anchor", 252, "R"), ("e_runup", 21, "U")):
        start = max(0, pos - length + 1)
        returns = np.full((pos - start + 1, len(codes)), np.nan)
        verified = np.zeros(returns.shape, dtype=bool)
        for col, code in enumerate(codes):
            if code in book.raw:
                returns[:, col] = book.raw[code][start:pos + 1, 4]
                verified[:, col] = book.verified[code][start:pos + 1]
        values, reasons = ch001.adjusted_values(returns, np.where(verified, "verified", ""),
            np.ones(returns.shape, dtype=bool), length=length)
        reasons[reasons == "unverified_session"] = "unverified_or_missing_session"
        valid = reasons == ""
        metrics = np.full(len(codes), np.nan)
        if variant == "e_anchor":
            metrics[valid] = values[-1, valid] / values[:, valid].max(axis=0)
        else:
            metrics[valid] = values[-1, valid] / values[0, valid] - 1
        valid_market = pd.Series(markets).isin(("KOSPI", "KOSDAQ")).to_numpy()
        reasons[~valid_market] = "market_unavailable"
        metrics[reasons != ""] = np.nan
        result = pd.DataFrame({"computable": reasons == "", "reason": reasons, metric: metrics,
                               "market": markets}, index=codes)
        result["percentile"] = result.groupby("market", observed=True)[metric].rank(method="max", pct=True)
        result["rank_denominator"] = result.groupby("market", observed=True)[metric].transform("count").fillna(0).astype(int)
        result["removed"] = result.percentile.le(.30) if variant == "e_anchor" else result.percentile.gt(.80)
        result["session_date"] = book.days[pos].date().isoformat()
        results[variant] = result
    return results


def attach_filters(prepared, rule, rows):
    last_key, charts = None, None
    for row in sorted(rows, key=event_key):
        key = row["decision_date"], prepared["book"].days.searchsorted(pd.Timestamp(row["public_date"]), side="right") - 1
        if key != last_key:
            charts = chart_filters(prepared["book"], prepared["universes"][rule][key[0]].index, row["public_date"])
            last_key = key
        row["charts"] = {}
        for variant, frame in charts.items():
            item = frame.loc[row["stock_code"]].astype(object)
            row["charts"][variant] = item.where(pd.notna(item), None).to_dict()
            if row["charts"][variant]["removed"] and not row["charts"][variant]["computable"]:
                raise AssertionError("CH004 removed an uncomputable event")


def monthly(rows, variant, key="excess"):
    grouped = defaultdict(list)
    for row in sorted(rows, key=event_key):
        grouped[row["entry_month"]].append(row)
    result = []
    for month, group in sorted(grouped.items()):
        evaluable = [r for r in group if r["charts"][variant]["computable"]]
        removed = [r for r in group if r["charts"][variant]["removed"]]
        if not {event_key(r) for r in removed} <= {event_key(r) for r in evaluable}:
            raise AssertionError("CH004 removals must be evaluable events")
        kept = [r for r in group if not r["charts"][variant]["removed"]]
        result.append({"month": month, "events": len(group), "evaluable": len(evaluable),
            "removed": len(removed), "uncomputable": len(group) - len(evaluable),
            "difference": -float(np.sum([r[key] for r in removed])) / len(group) if removed else 0.,
            "baseline_mean": float(np.mean([r[key] for r in group])),
            "remaining_mean": float(np.mean([r[key] for r in kept])) if kept else None,
            "removed_mean": float(np.mean([r[key] for r in removed])) if removed else None})
    return result


def summarize(months):
    return {"event_count": sum(r["events"] for r in months), "removal_months": sum(r["removed"] > 0 for r in months),
        "removed_events": sum(r["removed"] for r in months),
        "primary": av001.newey_west([r["month"] for r in months], [r["difference"] for r in months]),
        "per_month": months}


def random_controls(rows, variant, actual_mean, *, key="excess"):
    groups = defaultdict(list)
    for row in sorted(rows, key=event_key):
        groups[row["entry_month"]].append(row)
    plans = []
    for month, group in sorted(groups.items()):
        candidates = [r for r in group if r["charts"][variant]["computable"]]
        k = sum(r["charts"][variant]["removed"] for r in group)
        plans.append((month, len(group), k, np.array([r[key] for r in candidates], dtype=float)))
    rng, digest, paths = np.random.default_rng(0), hashlib.sha256(), []
    for path in range(N_CONTROLS):
        differences = []
        for month, n, k, values in plans:
            chosen = rng.choice(len(values), size=k, replace=False)
            digest.update(json.dumps([path, month, chosen.tolist()], separators=(",", ":")).encode())
            differences.append(-float(values[np.sort(chosen)].sum()) / n if k else 0.)
        paths.append({"path": path, "mean": float(np.mean(differences)) if differences else None})
    share = (sum(p["mean"] >= actual_mean for p in paths) / N_CONTROLS
             if actual_mean is not None and plans else None)
    return {"share_of_controls": share, "paths": paths, "draws_sha256": digest.hexdigest(),
            "seed": 0, "path_count": N_CONTROLS}


def variant_metrics(rows, variant, *, loss=False, diagnostics=True):
    suffix = "_loss" if loss else ""
    result = summarize(monthly(rows, variant, "excess" + suffix))
    result["random_control"] = random_controls(rows, variant, result["primary"]["mean"], key="excess" + suffix)
    result["cost_sensitivity"] = {str(m): summarize(monthly(rows, variant, f"excess_v2_{m}" + suffix))
                                  for m in (1.0, 1.5, 2.0)}
    result["cost_model_v1"] = {"used_for_judgement": False,
        **summarize(monthly(rows, variant, "excess_v1_1.0" + suffix))}
    for label, start, end in (("design", "2017-05", "2024-12"), ("validation", "2025-01", "2025-12")):
        result[label] = summarize(monthly([r for r in rows if start <= r["entry_month"] <= end], variant, "excess" + suffix))
    design, validation = (result[label]["primary"]["mean"] for label in ("design", "validation"))
    result["validation_year_same_sign"] = bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None
    result["validation_sign_used_for_judgement"] = False
    if diagnostics:
        result["filter_counts"] = {"events": len(rows), "evaluable": sum(r["charts"][variant]["computable"] for r in rows),
            "uncomputable": sum(not r["charts"][variant]["computable"] for r in rows),
            "removed": result["removed_events"], "removal_months": result["removal_months"],
            "uncomputable_by_reason": dict(Counter(r["charts"][variant]["reason"] for r in rows if not r["charts"][variant]["computable"]))}
        result["excess_levels"] = {
            label: {"mean_of_monthly_means": float(np.mean(values)) if values else None, "months": len(values)}
            for label in ("baseline", "remaining", "removed")
            for values in [[r[label + "_mean"] for r in result["per_month"] if r[label + "_mean"] is not None]]}
        for label, group_key in (("by_year", lambda r: r["entry_month"][:4]), ("markets", lambda r: r["market"]),
                ("liquidity_buckets", lambda r: str(np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, r["entry_adv"], side="right")))):
            groups = defaultdict(list)
            for row in rows:
                groups[group_key(row)].append(row)
            result[label] = {name: summarize(monthly(group, variant)) for name, group in sorted(groups.items())}
        dated = pd.Series(rows, index=[r["entry_date"] for r in rows], dtype=object)
        result["short_sale_regimes"] = {name: summarize(monthly(group.tolist(), variant))
            for name, group in hc003.market_regimes.split_by_regime(dated).items()}
    return result


def unavailable_metrics(status, reason):
    return {**summarize([]), "unavailable": status, "verdict": status, "reasons": [reason],
        "cost_sensitivity": {str(m): summarize([]) for m in (1.0, 1.5, 2.0)},
        "random_control": {"share_of_controls": None}}


def judge_variants(variants, fields, trials):
    if set(variants) != set(VARIANTS):
        raise ValueError("CH004 Holm family must contain exactly two variants")
    criteria = fields["pass_criteria"]["design_validation"]
    minimum = criteria["minimum_observations"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trials > 20 else criteria["core_t_stat_gt"]
    sufficient = {}
    for v, r in variants.items():
        p = r["primary"]
        stats = [p[k] for k in ("mean", "standard_error", "t_stat", "p_one_sided")]
        stats += [r["cost_sensitivity"]["1.5"]["primary"]["mean"], r["random_control"]["share_of_controls"]]
        sufficient[v] = (not r.get("unavailable") and r["event_count"] >= minimum["events"]
            and p["count"] >= minimum["rebalances"] and r["removal_months"] >= minimum["rebalances"]
            and all(x is not None and np.isfinite(x) for x in stats) and p["standard_error"] > 0)
    p_values = {v: variants[v]["primary"]["p_one_sided"] if sufficient[v] else 1. for v in VARIANTS}
    corrected = ch001.holm(p_values, variants=VARIANTS)
    for v, r in variants.items():
        p, share = r["primary"], r["random_control"]["share_of_controls"]
        mean15 = r["cost_sensitivity"]["1.5"]["primary"]["mean"]
        r.update(holm_p=corrected[v], judgement_inputs={"p_for_holm": p_values[v], "t_threshold": threshold,
            "trial_count_after_run": trials, "events": r["event_count"], "event_months": p["count"],
            "removal_months": r["removal_months"], "mean_at_cost_1_5": mean15, "random_share": share})
        if r.get("unavailable"):
            continue
        if not sufficient[v]:
            r.update(verdict="insufficient", reasons=["300 events, 36 event months, 36 removal months and defined required statistics/positive SE required"])
            continue
        failures = []
        for failed, reason in ((p["mean"] <= criteria["net_performance_gt"], "mean d must exceed zero"),
                (p["t_stat"] <= threshold, f"NW t must exceed {threshold}"),
                (corrected[v] > .05, "Holm(2) one-sided p must be <=0.05"),
                (share > criteria["random_control_top_percent"] / 100, "control share must be <=0.05"),
                (mean15 <= criteria["net_performance_at_cost_multiplier_1_5_gt"], "cost x1.5 mean d must exceed zero")):
            if failed:
                failures.append(reason)
        r.update(verdict="fail" if failures else "pass", reasons=failures or ["all per-rule CH004 criteria satisfied"])
    return variants


def gross_paths(prepared, rule, rows):
    book, grouped = prepared["book"], defaultdict(list)
    for row in rows:
        row["gross_excess_path"] = {}
        grouped[row["entry_date"]].append(row)
    unavailable, boundary = [], Counter()
    for day, group in sorted(grouped.items()):
        entry = book.days.get_loc(pd.Timestamp(day))
        u = prepared["universes"][rule][group[0]["decision_date"]]
        codes = [code for code in u.index if book.entry_reason(code, entry) is None]
        for h in (1, 5, 10, 20):
            if entry + h - 1 >= len(book.days):
                boundary[str(h)] += len(group)
                continue
            table = book.outcomes(codes, entry, h, costs=False, reporting=True)
            missing = ev001.unavailable_path(table, day, h, len(group), len(codes))
            if missing is not None:
                unavailable.append(missing)
                continue
            benchmark = float(table.gross.mean())
            for row in group:
                row["gross_excess_path"][str(h)] = float(table.loc[row["stock_code"], "gross"] - benchmark)
    summaries = {}
    for label, group in [("ev_new20", rows), *[(v, [r for r in rows if not r["charts"][v]["removed"]]) for v in VARIANTS]]:
        summaries[label] = {}
        for h in (1, 5, 10, 20):
            values = [{**r, "gross_path": r["gross_excess_path"][str(h)]} for r in group if str(h) in r["gross_excess_path"]]
            summaries[label][str(h)] = {**ev001.monthly_summary(values, "gross_path"),
                "event_weighted_mean": float(np.mean([r["gross_path"] for r in values])) if values else None}
    return {"used_for_judgement": False, "paths": summaries, "unavailable": unavailable,
            "boundary_exclusions": dict(boundary)}


def evaluate(prepared, fields, trials):
    complete = prepared["listing_status"]["supplement_complete"]
    available = prepared["versions"]["S"].sensitivity_available if complete else None
    frozen, assertions = freeze_baselines(prepared) if complete else ({}, {})
    rules, losses, paths, overlap, chart_events = {}, {}, {}, {}, {}
    for rule in RULES:
        status = "data_incomplete" if not complete else "sensitivity_unavailable" if rule == "O" and not available else None
        if status:
            reason = (str(prepared["listing_status"]["reason"]) if not complete else
                      "corrections exist in the stage sample with zero original-filing links")
            rules[rule] = judge_variants({v: unavailable_metrics(status, reason) for v in VARIANTS}, fields, trials)
            losses[rule] = copy.deepcopy(rules[rule])
            continue
        rows = copy.deepcopy(frozen[rule]["events"])
        attach_filters(prepared, rule, rows)
        rules[rule] = judge_variants({v: variant_metrics(rows, v) for v in VARIANTS}, fields, trials)
        losses[rule] = judge_variants({v: variant_metrics(rows, v, loss=True, diagnostics=False) for v in VARIANTS}, fields, trials)
        for v in VARIANTS:
            if rules[rule][v]["random_control"]["draws_sha256"] != losses[rule][v]["random_control"]["draws_sha256"]:
                raise AssertionError("CH004 delisting sensitivity changed random draws")
        hc003.apply_delisting_verdict(rules[rule], losses[rule])
        both = [r for r in rows if all(r["charts"][v]["removed"] for v in VARIANTS)]
        overlap[rule] = {"events_removed_by_both": len(both),
            "by_entry_month": dict(sorted(Counter(r["entry_month"] for r in both).items())),
            "CH001_C_anchor_relationship": INTERPRETATIONS[5]}
        paths[rule] = gross_paths(prepared, rule, rows)
        chart_events[rule] = [{"event_key": list(event_key(r)), "charts": r["charts"],
                              "gross_excess_path": r["gross_excess_path"]} for r in rows]
        ev001._check_memory()
    variants = {v: {"verdict": av001.combine_verdict(rules["S"][v]["verdict"], rules["O"][v]["verdict"],
        sensitivity_available=available), "rule_verdicts": {r: rules[r][v]["verdict"] for r in RULES}} for v in VARIANTS}
    return {"rules": rules, "variants": variants, "delisting_sensitivity": losses,
        "frozen_baselines": frozen, "chart_events": chart_events, "assertions": assertions,
        "filter_overlap": overlap, "gross_excess_paths": paths,
        "sensitivity_unavailable_reasons": av001.sensitivity_reasons(prepared["versions"]) if complete else
            {"status": "not_evaluated", "reason": "data_incomplete"}}


def source_hashes(data_dir, *, repo_root, prices=None):
    hashes = ev001.source_hashes(data_dir, stage="design_validation", repo_root=repo_root, prices=prices)
    hashes["code"].update({str(Path(p).relative_to(PROJECT_ROOT)): hc003._digest(p)
                            for p in (__file__, ch001.__file__, ch001.d001.__file__)})
    hashes["preregistrations"] = {name: hc003._digest(Path(repo_root) / "research/experiments" / name / "preregistration.md")
        for name in (*REFERENCE_HASHES, ev001.hc002.EXPERIMENT_ID, hc003.EXPERIMENT_ID, ev001.hf001.EXPERIMENT_ID)}
    hashes["documentation"] = {name: hc003._digest(Path(repo_root) / "research/experiments" / name)
                               for name in ("README.md", "DATA_ISSUES.md")}
    return hashes


def integrity_price_access(data_dir, status):
    result = {"evaluation_use": False, "nonprotected_2026_hash_access": {},
        "protected_2026_frozen_hashes_only": {}, "nonprotected_2026_frozen_hashes_only": {}}
    if status["supplement_complete"]:
        state = dart_listing._state(dart_listing.directory(data_dir) / "list_supplement/_state.json")
        for path, checksum in state["target_scan"]["price_files"].items():
            day = pd.Timestamp(Path(path).stem)
            if day.year == 2026:
                label = "protected_2026_frozen_hashes_only" if day <= pd.Timestamp("2026-06-30") else "nonprotected_2026_frozen_hashes_only"
                result[label][path] = checksum
    return result


def compute_design(*, data_dir, repo_root, prices=None, sessions=None, _attempt=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    sessions = calendar(sessions)
    hashes = source_hashes(data_dir, repo_root=repo_root, prices=prices)
    if _attempt is not None:
        _attempt["source_hashes"] = hashes
    prepared = prepare_inputs(data_dir=data_dir, repo_root=repo_root, sessions=sessions, prices=prices)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS)
    payload = evaluate(prepared, registration.fields, trials)
    provenance = (ev001.provenance(prepared) if prepared["listing_status"]["supplement_complete"] else
                  {"listing_status": prepared["listing_status"], "preparation_status": "data_incomplete"})
    payload.update(experiment_id=EXPERIMENT_ID, stage="design_validation", prereg_commit=registration.commit_hash,
        source_hashes=hashes, ev001_code_sha256=hashes["code"]["backtesting/experiments/ev001.py"], **provenance,
        integrity_price_access=integrity_price_access(data_dir, prepared["listing_status"]),
        trial_count_after_run=trials, code_commit=experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
        library_versions={"python": ch001.platform.python_version(), **{name: ch001.importlib.metadata.version(name)
            for name in ("numpy", "pandas", "scipy", "exchange-calendars", "PyYAML")}}, interpretations=INTERPRETATIONS)
    if hashes != source_hashes(data_dir, repo_root=repo_root, prices=prices):
        raise ValueError("CH004 inputs or code changed during execution; publication aborted")
    payload["replay_sha256"] = ev001.replay_hash(payload)
    ev001._check_memory()
    payload["performance"] = {"runtime_seconds": time.perf_counter() - started,
        "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return payload


def publish(payload, *, repo_root=PROJECT_ROOT):
    _registration(repo_root)
    if (set(payload["variants"]) != set(VARIANTS) or set(payload["rules"]) != set(RULES)
            or any(set(payload["rules"][r]) != set(VARIANTS) for r in RULES)):
        raise ValueError("CH004 publication requires both variants under both rules")
    metrics = {}
    for v in VARIANTS:
        metrics[v] = {"stage": "design_validation", "combined_verdict": payload["variants"][v]["verdict"],
            "replay_sha256": payload["replay_sha256"], "trial_count_after_run": payload["trial_count_after_run"],
            "rules": {r: {k: payload["rules"][r][v][k] for k in
                ("primary", "event_count", "removal_months", "removed_events", "holm_p", "verdict", "judgement_inputs")} for r in RULES},
            "delisting_verdicts": {r: payload["delisting_sensitivity"][r][v]["verdict"] for r in RULES}}
        experiment_registry.encode_metrics({**metrics[v], "result_file": "research/experiments/" + EXPERIMENT_ID + "/results/" + "0" * 32 + ".json.gz"})
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    lines = [f"# {EXPERIMENT_ID}", "", *[f"{v}: {payload['variants'][v]['verdict']}" for v in VARIANTS], "", *INTERPRETATIONS]
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root, summary_lines=lines)
    for v in VARIANTS:
        metrics[v]["result_file"] = str(paths[0].relative_to(repo_root))
        experiment_registry.record_trial(EXPERIMENT_ID, v, metrics[v], metrics[v]["combined_verdict"], repo_root=repo_root,
            note="CH004 frozen ev_new20; S/O calendar NW(3), Holm(2), 200 event-removal controls; no holdout")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, VARIANTS, repo_root) as attempt:
            payload = compute_design(data_dir=data_dir, repo_root=repo_root, prices=prices, sessions=sessions, _attempt=attempt)
            return publish(payload, repo_root=repo_root)


def fixture_timing():
    """Synthetic prices only; no experiment preparation or real returns."""
    days = calendar()
    days = days[days >= "2023-01-01"][:300]
    codes = [f"{pos:06d}" for pos in range(64)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000., "close": 10000., "base_price": 10000., "volume": 100.,
        "trading_value": 2e9, "ret_1d": .001, "market": "KOSPI", "calendar_status": "verified"}, index=index)
    book = ev001.EventPrices(days, days[-1], PROJECT_ROOT)
    book.add(prices)
    book.finish()
    started = time.perf_counter()
    chart_filters(book, codes, str(days[251].date()))
    chart_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for offset in range(12):
        book.outcomes(codes, 252 + offset, 20)
    outcome_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for offset in range(12):
        for horizon in (1, 5, 10, 20):
            book.outcomes(codes, 252 + offset, horizon, costs=False, reporting=True)
    gross_paths_seconds = time.perf_counter() - started
    rows = [{"entry_date": f"2024-{m:02d}-02", "entry_month": f"2024-{m:02d}", "stock_code": code,
        "fiscal_quarter": "2024Q1", "excess": (i - 32) / 1000,
        "charts": {"e_anchor": {"computable": i < 60, "removed": i < 12}}}
        for m in range(1, 13) for i, code in enumerate(codes)]
    actual = summarize(monthly(rows, "e_anchor"))["primary"]["mean"]
    started = time.perf_counter()
    random_controls(rows, "e_anchor", actual)
    return {"stocks": len(codes), "sessions": len(days), "entry_dates": 12, "event_months": 12,
        "paths": N_CONTROLS, "chart_seconds": chart_seconds, "outcome_seconds": outcome_seconds,
        "gross_paths_seconds": gross_paths_seconds, "control_seconds": time.perf_counter() - started,
        "book_array_mib": sum(a.nbytes for group in (book.raw, book.market, book.verified, book.adjusted,
            book.adv, book.last_close, book.last_trade) for a in group.values()) / 1024**2}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    sessions = calendar(sessions)
    prepared = prepare_inputs(data_dir=data_dir, repo_root=repo_root, sessions=sessions, prices=prices)
    if not prepared["listing_status"]["supplement_complete"]:
        return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash,
            "variants": VARIANTS, "rules": RULES, "read_end": "2025-12-30", "status": "data_incomplete",
            "listing_status": prepared["listing_status"], "x_i_evaluated": False,
            "projection": {"status": "unavailable", "reason": "listing supplement incomplete; preparation stopped"},
            "interpretations": INTERPRETATIONS}
    counts, charts, units, groups = {}, {}, 0, 0
    for rule in RULES:
        selected, counts[rule] = ev001.select_events(prepared, rule, "ev_new20")
        attach_filters(prepared, rule, selected)
        charts[rule] = {v: {"events": len(selected), "evaluable": sum(r["charts"][v]["computable"] for r in selected),
            "removed": sum(r["charts"][v]["removed"] for r in selected),
            "uncomputable_by_reason": dict(Counter(r["charts"][v]["reason"] for r in selected if not r["charts"][v]["computable"]))} for v in VARIANTS}
        unique = {r["entry_date"]: r for r in selected}
        units += sum(len(prepared["universes"][rule][r["decision_date"]]) for r in unique.values())
        groups += len({r["entry_month"] for r in selected})
    preparation = time.perf_counter() - started
    timing = fixture_timing()
    # Two exact primary computations and all four cost-free gross horizons.
    # Controls: two variants, each baseline/loss; groups count both rules.
    projected = preparation + (2 * timing["outcome_seconds"] + timing["gross_paths_seconds"]) * units / (timing["stocks"] * timing["entry_dates"])
    projected += 4 * timing["control_seconds"] * groups / timing["event_months"]
    peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash, "variants": VARIANTS, "rules": RULES,
        "read_end": "2025-12-30", "event_counts": counts, "chart_counts": charts,
        "listing_status": prepared["listing_status"], "L": prepared["dates"].L,
        "financial_dates": {r: prepared["versions"][r].report() for r in RULES},
        "integrity_price_access": integrity_price_access(data_dir, prepared["listing_status"]),
        "fixture_timing": timing, "projection": {"measured_preparation_seconds": preparation,
            "measured_peak_rss_mib": peak_mib, "full_run_seconds_range": [projected, projected * 3],
            "peak_rss_mib_estimate": peak_mib + units * 240 / 1024**2 + 512,
            "target_rss_bytes": ev001.MAX_RSS_BYTES,
            "basis": "Measured preparation/chart audit plus synthetic outcomes/controls scaled to universe/date units. Two primary computations and four gross horizons; 512 MiB serialization reserve. Estimates, not measured execution."},
        "x_i_evaluated": False, "interpretations": INTERPRETATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: bounded EV001 selection/chart audit, no event returns")
    mode.add_argument("--execute", action="store_true", help="evaluate design/validation and append two trials")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(json.dumps(result["variants"], ensure_ascii=False))
    else:
        print(json.dumps(dry_run(data_dir=args.data_dir), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
