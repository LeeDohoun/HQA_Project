"""CH001's fixed chart avoidance filters. Offline dry run is the default."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import resource
import sys
import time

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, holdout, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, common, d001, hc001, hc002, hf001, ordering
from src.ingestion import dart_listing
from src.research import market_regimes


EXPERIMENT_ID = "CH001_chart_avoidance_filters"
VARIANTS = ("ch_heat", "ch_wick", "ch_anchor")
RULES = ("S", "O")
MULTIPLIERS = (1.0, 1.5, 2.0)
N_CONTROLS = 200
PRICE_END = pd.Timestamp("2025-12-31")
MAX_RSS_BYTES = 7_000_000_000
NUMERIC_COLUMNS = (*d001.NUMERIC_COLUMNS, "high", "low")
INTERPRETATIONS = [
    "AV001.prepare is called once at design_validation for both S/O rules. Its unused HC003 price book is discarded; no HC003 accounting or price-index fallback is used for CH001 signals or returns.",
    "The HC002 month-end/20-session calendar supplies AV001's decision and entry dates. Preparation end_date/end_kind metadata describe the 20-session close; they do not change AV001's universe or filters. Evaluation prices start at the first decision's 252-session window and end at the last XKRX session of 2025. Later prices within that span classify resumption exactly as HF001 does.",
    "A's first return must be valid although A starts at 1 and only subsequent returns multiply it. Missing stock sessions never extend windows. Heat/anchor use verified stored returns, including zero-volume days, without any raw-price substitute.",
    "Wick's explicit candidate rules override the common return-day rule: no ret_1d or calendar_status test is added. Each candidate needs its own 22-session span (21 bases plus the preceding close); five candidates need 26 sessions. Invalid candidates retain all failed reason labels; stock reason counts can overlap. Partial-uncomputable names remain evaluable and eligible for random removal.",
    "Anchor percentiles are recomputed within each rule's full U and decision-date market, before intersecting B or diagnostic subsets. RSI and percentile boundary counts are inclusive and reported separately for U, P and B.",
    "A month with nonempty U and complete market/session/listing coverage is valid even when B is empty (d=0). Empty U is retained as an invalid observation, never invented as a zero-effect valid month. Selected but unfilled slots remain cash; surviving selected slots are reweighted equally before execution.",
    "Universe excess uses U equal-weight net, including its unfilled cash slots and full costs, as in HF001. Removed-minus-remaining is defined only when both selected baskets are nonempty. Diagnostic market/ADV subsets rerun equal-weight accounting within the subset, with chart ranks frozen from full U; an empty subset U is invalid.",
    "NW reuses HF001's ordered valid-cohort Bartlett lag 1 (no finite-sample correction), upper normal tail and undefined p=1. Holm maps the fixed three CH variants to HF001's three slots. Both 36 valid months and 36 actual-removal months are required; the YAML's generic 300 events is not an additional or substitute gate.",
    "Each rule/variant starts one default_rng(0), traversing path then every decision month then sorted E, calling choice even for zero/all removals. Loss reruns assert identical draw-stream SHA-256. Control means use the same sorted-name mean reduction as actual baskets, so exact ties count as >=.",
    "2025 sign, v2 costs, risk, year/ban/market/liquidity splits, P-reference effects and overlap are reporting only. Risk-only requires nonpositive mean d, lower sample volatility and higher worst-ceil(5%*N) mean on the same valid months. Drawdown starts at wealth 1 and is a stitched-cohort diagnostic.",
    "Combined non-pass precedence follows AV001: data_incomplete for both rules, sensitivity_unavailable, delisting_sensitive, one-rule pass/version_sensitive, insufficient, risk_only, fail. Each rule's loss judgement reruns controls and Holm before comparison; both rules must pass without a changed loss verdict.",
    "All three finite compact registry rows are validated before publication or any append. Result details use the shared exclusive-file writer and gzip above 50 MiB. Registry appends use record_trial once per variant; the existing API does not promise atomic rollback after an operating-system write failure.",
    "Supplement verification may hash non-protected 2026 price files. Their verified frozen hashes are recorded separately from evaluation prices; protected January-June files are inventoried only, never opened or rehashed by CH001. Full merged listing metadata, including later filings, determine date provenance and L as AV001 requires.",
    "The unchanged v2 cost module's settlement-calendar import can invoke the project environment loader. CH001 refuses to start if .env or .env-ai exists in the code checkout, rather than allowing that dependency to read credentials. This is a preflight stop, not an alternative cost/calendar implementation.",
    "Dry run prepares real selections and chart computability, but never calls forward_observations, computes portfolio outcomes, judges variants, reads HF001 performance results or publishes. AV001 preparation internally builds unused price indices. Runtime/RSS projections combine measured preparation and synthetic fixtures; they are estimates, not a full execution measurement.",
    "Execution uses ordering.execution_lock(repo_root) across preflight, trial-count lookup, judgement, publication and registry appends. After preflight hashes are fixed, exceptions record one design_aborted row per registered variant through ordering.design_attempt; failures before hashes record no attempt. Real execution must run in the main checkout after merge so the directory lock and registry are shared with other research runs.",
    "Input and code SHA-256 are fixed before preparation reads/evaluation and re-inventoried immediately before publication. A changed input, inventory or code aborts with the original hash category digests and no verdict publication. Price-content hashing is bounded through 2025; protected January-June 2026 price files are never opened.",
    "The wick corporate-action guard compares abs(base-previous_close) > 0.005*previous_close for positive prices. Exactly +0.5% and -0.5% pass without a tolerance; either boundary just outside fails.",
]


def _registration(repo_root):
    if any((PROJECT_ROOT / name).is_file() for name in (".env", ".env-ai")):
        raise ValueError("CH001 cannot start with .env/.env-ai present: the unchanged v2 settlement import "
                         "can load project credentials; no environment file was read")
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (registration.fields["variants_planned"] != 3 or registration.fields["uses_holdout"]
            or registration.fields["uses_llm"]):
        raise ValueError("CH001 requires three fixed variants, no holdout and no LLM")
    av001._registration(repo_root)
    return registration


def _check_memory():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    if peak > MAX_RSS_BYTES:
        raise MemoryError(f"CH001 exceeded its 7 GB peak RSS target: {peak} bytes")


def calendar_plan(sessions=None):
    days = hc001._session_calendar() if sessions is None else signal_eval._dates(sessions).sort_values().unique()
    planned, excluded = hc002.rebalance_calendar(days)
    if excluded or len(planned) != len(hc002.MONTHS):
        raise ValueError("CH001 cannot drop registered decision months: " + str(excluded))
    first = days.get_loc(pd.Timestamp(planned[0]["decision_date"]))
    if first < 251:
        raise ValueError("calendar does not contain the first 252-session anchor window")
    days = days[(days >= days[first - 251]) & (days <= PRICE_END)]
    return days, [{**row, "end_date": row["exit_date"], "end_kind": "close"} for row in planned]


def load_prices(data_dir, start, end, *, repo_root=PROJECT_ROOT):
    """D001's last saved row per stock/day, also retaining high and low."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start > end or end > PRICE_END:
        raise ValueError("CH001 price reads must end by 2025-12-31")
    holdout.guard_period(start.date(), end.date(), experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    files = d001._price_files(Path(data_dir) / "market/krx_daily", start, end, repo_root=repo_root)
    frames = []
    for day, path in files:
        latest = {}
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["trade_date"] != day.date().isoformat():
                    raise ValueError(f"price row does not match file date: {path.name}:{number}")
                code = row["stock_code"]
                if not isinstance(code, str) or not code:
                    raise ValueError(f"missing stock_code: {path.name}:{number}")
                code = sys.intern(code)
                values = {name: row.get(name) for name in (*NUMERIC_COLUMNS, *d001.TEXT_COLUMNS)}
                for name in d001.TEXT_COLUMNS:
                    if isinstance(values[name], str):
                        values[name] = sys.intern(values[name])
                values["ret_1d"] = None if row.get("change_rate_pct") is None else float(row["change_rate_pct"]) / 100
                latest[code] = values
        if latest:
            frame = pd.DataFrame.from_dict(latest, orient="index").rename_axis("stock_code")
            for name in NUMERIC_COLUMNS:
                frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
            frames.append(frame.assign(trade_date=day).reset_index().set_index(["trade_date", "stock_code"]))
    if not frames:
        raise ValueError("no price observations in the guarded CH001 span")
    prices = pd.concat(frames).sort_index()
    if prices.index.has_duplicates:
        raise ValueError("CH001 price keys must be unique")
    for name in d001.TEXT_COLUMNS:
        prices[name] = prices[name].astype("category")
    return prices, files


def _window(prices, codes, decision, sessions, length, columns):
    pos = sessions.get_loc(pd.Timestamp(decision))
    days = sessions[max(0, pos - length + 1):pos + 1]
    index_days = prices.index.get_level_values("trade_date")
    frame = prices.iloc[index_days.searchsorted(days[0]):index_days.searchsorted(days[-1], side="right")]
    values = {key: frame[key].unstack("stock_code").reindex(index=days, columns=codes).to_numpy()
              for key in columns}
    present = pd.Series(True, index=frame.index).unstack("stock_code").reindex(index=days, columns=codes)
    return days, values, present.eq(True).to_numpy()


def adjusted_index(prices, codes, decision, sessions, length):
    _, values, present = _window(prices, codes, decision, sessions, length, ("ret_1d", "calendar_status"))
    return adjusted_values(values["ret_1d"].astype(float), values["calendar_status"], present, length=length)


def adjusted_values(returns, statuses, present, *, length):
    """The same CH001 A/valid-return-day rule on session-aligned arrays."""
    reasons = np.full(returns.shape[1], "", dtype=object)
    reasons[~((np.isfinite(returns) & (returns > -1)).all(axis=0))] = "invalid_return"
    reasons[~(statuses == "verified").all(axis=0)] = "unverified_session"
    reasons[~present.all(axis=0)] = "missing_session"
    if len(returns) != length:
        reasons[:] = "short_calendar"
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        adjusted = np.vstack([np.ones(returns.shape[1]), np.cumprod(1 + returns[1:], axis=0)])
    finite = (np.isfinite(adjusted) & (adjusted > 0)).all(axis=0)
    reasons[(reasons == "") & ~finite] = "invalid_adjusted_index"
    return adjusted, reasons


def heat_filter(prices, codes, decision, sessions):
    codes = pd.Index(sorted(codes), name="stock_code")
    adjusted, reasons = adjusted_index(prices, codes, decision, sessions, 60)
    rsi = np.full(len(codes), np.nan)
    if len(adjusted) == 60:
        delta = np.diff(adjusted, axis=0)
        gains, losses = np.maximum(delta, 0), np.maximum(-delta, 0)
        g, l = gains[:14].mean(axis=0), losses[:14].mean(axis=0)
        for gain, loss in zip(gains[14:], losses[14:]):
            g, l = (13 * g + gain) / 14, (13 * l + loss) / 14
        flat = (g == 0) & (l == 0)
        reasons[(reasons == "") & flat] = "flat_index"
        valid = reasons == ""
        rsi[valid & (l == 0) & (g > 0)] = 100.0
        regular = valid & (l > 0)
        rsi[regular] = 100 - 100 / (1 + g[regular] / l[regular])
    return pd.DataFrame({"computable": reasons == "", "removed": rsi >= 70,
                         "reason": reasons, "rsi": rsi}, index=codes)


def wick_filter(prices, codes, decision, sessions):
    codes = pd.Index(sorted(codes), name="stock_code")
    days, values, present = _window(prices, codes, decision, sessions, 26,
                                    ("open", "high", "low", "close", "volume", "base_price"))
    values = {key: value.astype(float) for key, value in values.items()}
    valid_count, spikes = np.zeros(len(codes), dtype=int), np.zeros(len(codes), dtype=int)
    invalid_reasons = [Counter() for _ in codes]
    for s in range(max(0, len(days) - 5), len(days)):
        reasons = {}
        if s < 21:
            reasons["short_history"] = np.ones(len(codes), dtype=bool)
        else:
            opening, high, low, close, volume = (values[key][s] for key in ("open", "high", "low", "close", "volume"))
            ohlc = np.array([opening, high, low, close])
            reasons["invalid_ohlc"] = (~(np.isfinite(ohlc) & (ohlc > 0)).all(axis=0)
                | (low > np.minimum(opening, close)) | (np.maximum(opening, close) > high) | (high <= low))
            reasons["invalid_candidate_volume"] = ~np.isfinite(volume) | (volume <= 0)
            prior_volume = values["volume"][s - 20:s]
            average = prior_volume.mean(axis=0)
            reasons["invalid_prior_volume"] = ~(np.isfinite(prior_volume) & (prior_volume >= 0)).all(axis=0)
            reasons["nonpositive_mean_volume"] = np.isfinite(average) & (average <= 0)
            bases, previous = values["base_price"][s - 20:s + 1], values["close"][s - 21:s]
            reasons["invalid_base"] = ~(np.isfinite(bases) & (bases > 0)).all(axis=0)
            reasons["invalid_previous_close"] = ~(np.isfinite(previous) & (previous > 0)).all(axis=0)
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                changes = np.abs(bases - previous) > 0.005 * previous
                wick = (high - np.maximum(opening, close)) / (high - low)
            reasons["corporate_action"] = changes.any(axis=0)
            reasons["missing_session"] = ~present[s - 21:s + 1].all(axis=0)
        valid = ~np.logical_or.reduce(list(reasons.values()))
        valid_count += valid
        if s >= 21:
            spikes += valid & (wick >= 0.5) & (volume >= 2 * average)
        for label, mask in reasons.items():
            for pos in np.flatnonzero(mask):
                invalid_reasons[pos][label] += 1
    # Fewer than five calendar sessions also cannot provide valid candidates.
    for reasons in invalid_reasons:
        if len(days) < 5:
            reasons["short_history"] += 5 - len(days)
    return pd.DataFrame({"computable": valid_count > 0, "removed": spikes > 0,
        "reason": ["" if count else "no_valid_candidate" for count in valid_count],
        "valid_candidate_days": valid_count, "spike_days": spikes,
        "partial_uncomputable": (valid_count > 0) & (valid_count < 5),
        "invalid_candidate_reasons": [dict(reasons) for reasons in invalid_reasons]}, index=codes)


def anchor_filter(prices, codes, markets, decision, sessions):
    codes = pd.Index(sorted(codes), name="stock_code")
    adjusted, reasons = adjusted_index(prices, codes, decision, sessions, 252)
    ratios = np.full(len(codes), np.nan)
    valid = reasons == ""
    ratios[valid] = adjusted[-1, valid] / adjusted[:, valid].max(axis=0)
    result = pd.DataFrame({"computable": valid, "reason": reasons, "R": ratios,
                           "market": markets.reindex(codes)}, index=codes)
    if not result.market.isin(("KOSPI", "KOSDAQ")).all():
        raise ValueError("anchor requires each U name's decision-date KOSPI/KOSDAQ market")
    result["percentile"] = result.groupby("market", observed=True).R.rank(method="max", pct=True)
    result["removed"] = result.percentile.le(0.30)
    return result


def baseline_set(selection):
    u = selection["universe"]
    p = set(u.index[u.phase.eq(2)])
    b = p - selection["F_vol"] - selection["F_raise"]
    expected = av001.target_set("av_conc", selection, set())
    if b != expected:
        raise AssertionError(f"CH001 B != AV001 av_conc on {selection['month']}: {sorted(b ^ expected)}")
    return p, b


def attach_filters(prices, selections, sessions):
    for selection in selections:
        u, day = selection["universe"], selection["decision_date"]
        selection["P"], selection["B"] = baseline_set(selection)
        selection["charts"] = dict(zip(VARIANTS, (
            heat_filter(prices, u.index, day, sessions),
            wick_filter(prices, u.index, day, sessions),
            anchor_filter(prices, u.index, selection["markets"], day, sessions))))


def filter_counts(chart, codes):
    part = chart.loc[sorted(codes)]
    reasons = Counter(value for value in part.reason if value)
    candidate_reasons = Counter()
    if "invalid_candidate_reasons" in part:
        for computable, row in zip(part.computable, part.invalid_candidate_reasons):
            candidate_reasons.update(row)
            if not computable:
                reasons.update(row.keys())
    return {"names": len(part), "evaluable": int(part.computable.sum()),
            "uncomputable": int((~part.computable).sum()), "removed": int(part.removed.sum()),
            "uncomputable_by_reason": dict(reasons), "invalid_candidates_by_reason": dict(candidate_reasons),
            "partial_uncomputable": int(part.partial_uncomputable.sum()) if "partial_uncomputable" in part else 0,
            "boundary_rsi_69_71": int(part.rsi.between(69, 71).sum()) if "rsi" in part else 0,
            "boundary_percentile_029_031": int(part.percentile.between(.29, .31).sum()) if "percentile" in part else 0}


def integrity_price_access(data_dir, status):
    """Report only the hashes verified by the successful supplement check."""
    result = {"evaluation_use": False, "source": "dart_listing.verified_target_scan",
              "nonprotected_2026_hash_access": {}, "protected_2026_frozen_hashes_only": {}}
    if status["supplement_complete"]:
        state = dart_listing._state(dart_listing.directory(data_dir) / "list_supplement/_state.json")
        for path, checksum in state["target_scan"]["price_files"].items():
            day = pd.Timestamp(Path(path).stem)
            if day.year == 2026:
                key = "protected_2026_frozen_hashes_only" if day <= pd.Timestamp("2026-06-30") else "nonprotected_2026_hash_access"
                result[key][path] = checksum
    return result


def prepare_inputs(*, data_dir, repo_root, prices=None, sessions=None, attach_charts=True):
    started = time.perf_counter()
    sessions, planned = calendar_plan(sessions)
    paths = []
    if prices is None:
        prices, paths = load_prices(data_dir, sessions[0], sessions[-1], repo_root=repo_root)
    if prices.empty or common._sessions(prices).max() > PRICE_END:
        raise ValueError("CH001 requires nonempty evaluation prices strictly before 2026")
    common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    if not common._sessions(prices).equals(sessions):
        raise ValueError("CH001 requires every XKRX session in its bounded price span")
    prepared = av001.prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions,
        planned=planned, stage="design_validation", prices=prices)
    metadata = {"selections": prepared["selections"], "counts": prepared["counts"],
        "filing_rules": {r: prepared["versions"][r].report() for r in RULES},
        "sensitivity_available": prepared["versions"]["O"].sensitivity_available,
        "L": prepared["dates"].L, "version_provenance": prepared["versions"]["S"].annotations,
        "date_rule_differences": av001._date_differences(prepared),
        "listing_status": prepared["listing_status"], "coverage": prepared["coverage"],
        "raise_complete": prepared["raise_complete"], "event_counts": prepared["event_counts"]}
    del prepared  # Release the unused PriceBook, receipts and derived financial values.
    for rule in RULES:
        if attach_charts:
            attach_filters(prices, metadata["selections"][rule], sessions)
        else:
            for selection in metadata["selections"][rule]:
                selection["P"], selection["B"] = baseline_set(selection)
    metadata.update(integrity_price_access=integrity_price_access(data_dir, metadata["listing_status"]),
        preparation_seconds=time.perf_counter() - started)
    _check_memory()
    return prices, paths, sessions, metadata


def forward_observations(prices, selections, sessions, *, repo_root=PROJECT_ROOT):
    frames = [row["universe"][["avg_trading_value_20d", "phase"]].assign(
        decision_date=pd.Timestamp(row["decision_date"]), trade_date=pd.Timestamp(row["trade_date"]),
        decision_market=row["markets"]).reset_index() for row in selections]
    universe = pd.concat(frames, ignore_index=True)
    with common.guarded_reads(EXPERIMENT_ID):
        return hf001.forward_observations(prices, universe, sessions, repo_root=repo_root)


def portfolio_snapshot(group, *, loss=False):
    group = group.sort_values("stock_code", kind="stable")
    names = group.stock_code.tolist()
    gross = "gross_delisting_loss" if loss else "gross"
    return {"names": names, "weights": {code: 1 / len(names) for code in names},
        "cash_weight": float((~group.filled.astype(bool)).mean()) if names else 1.0,
        "costs": {str(m): {"net": float((group[gross] - group[f"cost_{m}"]).mean()) if names else 0.0,
                           "cost": float(group[f"cost_{m}"].mean()) if names else 0.0} for m in MULTIPLIERS}}


def monthly(observations, selections, variant, *, loss=False, scope="B", market=None, bucket=None):
    groups = {day: group.set_index("stock_code", drop=False) for day, group in observations.groupby("decision_date", sort=True)}
    rows = []
    for selection in selections:
        day = pd.Timestamp(selection["decision_date"])
        group = groups[day] if len(selection["universe"]) else observations.iloc[:0].set_index("stock_code", drop=False)
        if market is not None:
            group = group.loc[group.decision_market.eq(market)]
        if bucket is not None:
            group = group.loc[group.bucket.eq(bucket)]
        codes = set(group.index) & selection[scope]
        chart = selection["charts"][variant].loc[sorted(codes)] if variant is not None else None
        removed = set(chart.index[chart.removed]) if chart is not None else set()
        evaluable = set(chart.index[chart.computable]) if chart is not None else codes
        if not removed <= evaluable:
            raise AssertionError("actual removed set is not a subset of E")
        baseline = portfolio_snapshot(group.loc[sorted(codes)].reset_index(drop=True), loss=loss)
        filtered = portfolio_snapshot(group.loc[sorted(codes - removed)].reset_index(drop=True), loss=loss)
        universe = portfolio_snapshot(group.reset_index(drop=True), loss=loss)
        rejected = portfolio_snapshot(group.loc[sorted(removed)].reset_index(drop=True), loss=loss)
        costs = {str(m): {"filtered_net": filtered["costs"][str(m)]["net"],
            "baseline_net": baseline["costs"][str(m)]["net"], "universe_net": universe["costs"][str(m)]["net"],
            "difference": filtered["costs"][str(m)]["net"] - baseline["costs"][str(m)]["net"],
            "universe_excess": filtered["costs"][str(m)]["net"] - universe["costs"][str(m)]["net"],
            "filtered_cost": filtered["costs"][str(m)]["cost"], "baseline_cost": baseline["costs"][str(m)]["cost"]}
            for m in MULTIPLIERS}
        rows.append({"month": selection["month"], "decision_date": selection["decision_date"],
            "valid": bool(len(group)), "invalid_reason": None if len(group) else "empty_universe",
            "B": sorted(codes), "E": sorted(evaluable), "removed": sorted(removed),
            "remaining": filtered["names"], "baseline": baseline, "filtered": filtered,
            "cash": not filtered["names"], "baseline_empty": not baseline["names"], "costs": costs,
            "counts": filter_counts(chart, codes) if chart is not None else None,
            "removed_minus_remaining": rejected["costs"]["1.0"]["net"] - filtered["costs"]["1.0"]["net"]
                if removed and filtered["names"] else None})
    return rows


def assert_filters_off(observations, selections):
    off = monthly(observations, selections, None)
    for selection, row in zip(selections, off):
        codes = av001.target_set("av_conc", selection, set())
        chosen = observations.loc[observations.decision_date.eq(pd.Timestamp(selection["decision_date"]))
                                  & observations.stock_code.isin(codes)]
        expected = portfolio_snapshot(chosen)
        if row["filtered"] != expected or row["baseline"] != expected or row["costs"]["1.0"]["difference"] != 0:
            raise AssertionError(f"chart-filters-off baseline mismatch on {selection['month']}")
    return {"months": len(off), "exact_names_weights_cash_cost_net": True}


def summarize(rows):
    return hf001._cohort_summary([row for row in rows if row["valid"]])


def random_controls(observations, rows, *, loss=False):
    groups = {day: group.set_index("stock_code") for day, group in observations.groupby("decision_date", sort=True)}
    inputs = []
    gross = "gross_delisting_loss" if loss else "gross"
    for row in rows:
        if not set(row["removed"]) <= set(row["E"]) <= set(row["B"]):
            raise AssertionError("actual removed set must be a subset of E within B")
        group = groups.get(pd.Timestamp(row["decision_date"]))
        net = (group.loc[row["B"], gross] - group.loc[row["B"], "cost_1.0"]).to_numpy(dtype=float) if row["B"] else np.array([])
        positions = {code: pos for pos, code in enumerate(row["B"])}
        inputs.append((net, positions))
    rng = np.random.default_rng(0)
    digest, means = hashlib.sha256(), []
    for path in range(N_CONTROLS):
        differences = []
        for row, (net, positions) in zip(rows, inputs):
            drawn = rng.choice(sorted(row["E"]), size=len(row["removed"]), replace=False).tolist()
            digest.update((json.dumps([path, row["month"], drawn], separators=(",", ":")) + "\n").encode())
            keep = np.ones(len(net), dtype=bool)
            keep[[positions[code] for code in drawn]] = False
            filtered = float(net[keep].mean()) if keep.any() else 0.0
            if row["valid"]:
                differences.append(filtered - row["costs"]["1.0"]["baseline_net"])
        if differences:
            means.append(float(np.mean(differences)))
    actual = hf001._mean([row["costs"]["1.0"]["difference"] for row in rows if row["valid"]])
    return {**signal_eval._control_summary(means, actual), "path_means": means,
            "seed": 0, "paths_planned": N_CONTROLS, "draw_order": "path/month/stock_code",
            "draws_sha256": digest.hexdigest(), "months": sum(row["valid"] for row in rows)}


def variant_metrics(observations, selections, variant, *, loss=False, diagnostics=True):
    rows = monthly(observations, selections, variant, loss=loss)
    valid = [row for row in rows if row["valid"]]
    result = {**summarize(rows), "per_month": rows,
        "random_control": random_controls(observations, rows, loss=loss),
        "filtered_months": sum(bool(row["removed"]) for row in valid),
        "removed_stock_cohorts": sum(len(row["removed"]) for row in valid),
        "cash_months": sum(row["cash"] for row in valid),
        "empty_B_months": sum(row["baseline_empty"] for row in valid),
        "invalid_months": len(rows) - len(valid)}
    for label, start, end in (("design", "2017-05", "2024-12"), ("validation", "2025-01", "2025-11")):
        result[label] = summarize([row for row in rows if start <= row["month"] <= end])
    design, validation = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
    result["validation_year_same_sign"] = bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None
    result["validation_sign_used_for_judgement"] = False
    if diagnostics:
        result["by_year"] = {year: summarize([row for row in rows if row["month"].startswith(year)])
                             for year in sorted({row["month"][:4] for row in rows})}
        dated = pd.Series(rows, index=[row["decision_date"] for row in rows], dtype=object)
        result["short_sale_regimes"] = {name: summarize(group.tolist()) for name, group in market_regimes.split_by_regime(dated).items()}
        result["ban_period"] = {name: summarize([row for row in rows if
            ("2023-11-06" <= row["decision_date"] <= "2025-03-30") == inside])
            for name, inside in (("inside", True), ("outside", False))}
        result["markets"] = {market: summarize(monthly(observations, selections, variant, loss=loss, market=market))
                             for market in ("KOSPI", "KOSDAQ")}
        lower, buckets = 0.0, {}
        for bucket, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
            buckets[str(bucket)] = {**summarize(monthly(observations, selections, variant, loss=loss, bucket=bucket)),
                "lower_inclusive": lower, "upper_exclusive": float(upper) if np.isfinite(upper) else None}
            lower = float(upper)
        result["liquidity_buckets"] = buckets
        reference = monthly(observations, selections, variant, loss=loss, scope="P")
        overlap = []
        for selection, row in zip(selections, reference):
            removed = set(row["removed"])
            overlap.append({"month": row["month"], "removed_P": len(removed),
                **{flag: {"count": len(removed & selection[flag]),
                          "share": len(removed & selection[flag]) / len(removed) if removed else None}
                   for flag in ("F_vol", "F_raise")},
                "counts": {scope: filter_counts(selection["charts"][variant], codes)
                           for scope, codes in (("U", selection["universe"].index), ("P", selection["P"]), ("B", selection["B"]))}})
        result["reference_P"] = {"used_for_judgement": False, **summarize(reference), "per_month": reference}
        result["overlap_and_computability"] = overlap
    return result


def unavailable_metrics(reason):
    return {**summarize([]), "per_month": [], "filtered_months": 0, "removed_stock_cohorts": 0,
        "random_control": {"share_of_controls": None}, "design": summarize([]), "validation": summarize([]),
        "unavailable": reason, "verdict": reason, "reasons": [reason]}


def newey_west(values, *, lag=1):
    """HF001 Bartlett accounting, optionally with a longer registered lag."""
    if type(lag) is not int or lag < 1:
        raise ValueError("NW lag must be a positive integer")
    result = hf001.newey_west(values)
    if lag == 1 or result["count"] < 2 or result["std"] == 0:
        return result
    values = np.asarray(values, dtype=float)
    n = len(values)
    centered = values - result["mean"]
    variance = float(np.dot(centered, centered) / n)
    for offset in range(1, min(lag, n - 1) + 1):
        variance += 2 * (1 - offset / (lag + 1)) * float(np.dot(centered[offset:], centered[:-offset])) / n
    result.update(long_run_variance=variance, standard_error=None, t_stat=None, p_one_sided=1.0)
    if variance > 0:
        se = math.sqrt(variance / n)
        statistic = result["mean"] / se
        result.update(standard_error=se, t_stat=statistic,
                      p_one_sided=0.5 * math.erfc(statistic / math.sqrt(2)))
    return result


def holm(p_values, *, variants=VARIANTS):
    if set(p_values) != set(variants):
        raise ValueError("CH001 Holm family must contain exactly three variants" if variants == VARIANTS
                         else "Holm family must contain exactly " + ", ".join(variants))
    if variants == VARIANTS:
        adjusted = hf001.holm(dict(zip(hf001.VARIANTS, (p_values[name] for name in VARIANTS))))
        return dict(zip(VARIANTS, (adjusted[name] for name in hf001.VARIANTS)))
    ordered = sorted(variants, key=lambda name: (p_values[name], variants.index(name)))
    adjusted, previous = {}, 0.0
    for rank, name in enumerate(ordered):
        p = p_values[name]
        if not np.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Holm requires finite probabilities in [0, 1]")
        previous = max(previous, min(1.0, (len(variants) - rank) * p))
        adjusted[name] = previous
    return adjusted


def judge_variants(variants, fields, trials):
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trials > 20 else criteria["core_t_stat_gt"]
    sufficient = {name: result["primary"]["count"] >= 36 and result["filtered_months"] >= 36
        and result["primary"]["t_stat"] is not None and result["primary"]["standard_error"] is not None
        and result["primary"]["standard_error"] > 0 and not result.get("unavailable") for name, result in variants.items()}
    corrected = holm({name: variants[name]["primary"]["p_one_sided"] if sufficient[name] else 1.0 for name in VARIANTS})
    for name, result in variants.items():
        primary, share = result["primary"], result["random_control"]["share_of_controls"]
        mean15 = result["cost_sensitivity"]["1.5"]["difference"]
        result.update(holm_p=corrected[name], judgement_inputs={**primary, "filtered_months": result["filtered_months"],
            "p_for_holm": primary["p_one_sided"] if sufficient[name] else 1.0,
            "holm_p": corrected[name], "t_threshold": float(threshold), "trial_count_after_run": trials,
            "mean_at_cost_1_5": mean15, "random_control_share": share})
        if result.get("unavailable"):
            continue
        if not sufficient[name] or mean15 is None or share is None:
            result.update(verdict="insufficient", reasons=["36 valid months, 36 actual-removal months and defined statistics/cost/control required"])
            continue
        filtered, baseline = result["risk"]["filtered"], result["risk"]["baseline"]
        risk_only = (primary["mean"] <= 0 and filtered["volatility"] < baseline["volatility"]
                     and filtered["lower_5pct_mean"] > baseline["lower_5pct_mean"])
        failures = []
        if primary["mean"] <= criteria["net_performance_gt"]:
            failures.append("mean d must exceed 0")
        if primary["t_stat"] <= threshold:
            failures.append(f"NW t must exceed {threshold:g}")
        if corrected[name] > .05:
            failures.append("Holm(3) one-sided p must be <=0.05")
        if share > criteria["random_control_top_percent"] / 100:
            failures.append("control share must be <=0.05")
        if mean15 <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
            failures.append("cost x1.5 mean d must exceed 0")
        result.update(verdict="risk_only" if risk_only else "fail" if failures else "pass",
                      reasons=failures if failures else ["all CH001 per-rule criteria satisfied"])
    return variants


def evaluate_rule(observations, selections, fields, trials):
    main = judge_variants({v: variant_metrics(observations, selections, v) for v in VARIANTS}, fields, trials)
    losses = judge_variants({v: variant_metrics(observations, selections, v, loss=True, diagnostics=False)
                            for v in VARIANTS}, fields, trials)
    for variant in VARIANTS:
        result, loss = main[variant], losses[variant]
        if result["random_control"]["draws_sha256"] != loss["random_control"]["draws_sha256"]:
            raise AssertionError("delisting sensitivity did not reproduce the random draws")
        result.update(base_verdict=result["verdict"], delisting_loss_verdict=loss["verdict"],
                      delisting_sensitive=result["verdict"] != loss["verdict"])
        if result["delisting_sensitive"]:
            result.update(verdict="delisting_sensitive", reasons=["last-close and -100% delisting verdicts differ"])
    return main, losses


def cost_model_v2_metrics(observations, prices, selections):
    adjusted = common.reprice_costs_v2(observations, prices, "trade_date", observations.filled.astype(bool))
    variants = {}
    for variant in VARIANTS:
        rows = monthly(adjusted, selections, variant)
        variants[variant] = {**summarize(rows), "per_month": rows}
    return {"used_for_judgement": False, "interpretation": common.COST_MODEL_V2_INTERPRETATION,
            "variants": variants}


def hf001_comparison(observations, selections, *, repo_root=PROJECT_ROOT):
    directory = Path(repo_root) / "research/experiments" / hf001.EXPERIMENT_ID / "results"
    paths = sorted(set(directory.glob("*.json.gz")) | set(directory.glob("*.json")))
    if not paths:
        return {"available": False, "reason": "no local HF001 result JSON"}
    comparisons = []
    current = monthly(observations, selections, None)
    current_by_month = {row["month"]: row for row in current if row["valid"]}
    for path in paths:
        saved = common.read_result_json(path)
        old = saved["variants"]["f_both"]["per_month"]
        old_by_month = {str(pd.Timestamp(row["decision_date"]).to_period("M")): row for row in old}
        common_months = sorted(set(current_by_month) & set(old_by_month))
        differences = [{"month": month,
            "name_difference_count": len(set(current_by_month[month]["B"]) ^ set(old_by_month[month]["remaining"])),
            "net_difference": current_by_month[month]["costs"]["1.0"]["baseline_net"] - old_by_month[month]["costs"]["1.0"]["filtered_net"]}
            for month in common_months]
        comparisons.append({"result_file": str(path.relative_to(repo_root)), "sha256": hf001._digest(path),
            "CH001_months": len(current_by_month), "HF001_months": len(old_by_month), "common_months": len(common_months),
            "CH001_only_months": sorted(set(current_by_month) - set(old_by_month)),
            "HF001_only_months": sorted(set(old_by_month) - set(current_by_month)),
            "CH001_mean_net": hf001._mean([r["costs"]["1.0"]["baseline_net"] for r in current_by_month.values()]),
            "HF001_mean_net": hf001._mean([r["costs"]["1.0"]["filtered_net"] for r in old]),
            "common_month_mean_net_difference": hf001._mean([r["net_difference"] for r in differences]),
            "name_difference_count": sum(r["name_difference_count"] for r in differences), "per_month": differences})
    return {"available": True, "comparisons": comparisons}


def source_hashes(data_dir, price_paths, *, repo_root=PROJECT_ROOT, prices=None):
    if any(pd.Timestamp(day) > PRICE_END for day, _ in price_paths):
        raise ValueError("CH001 evaluation price hashes must end by 2025-12-31")
    result = av001.source_hashes(data_dir, price_paths, stage="design_validation", prices=prices, repo_root=repo_root)
    sources = (Path(__file__), Path(d001.__file__))
    result["code"].update({str(path.relative_to(PROJECT_ROOT)): hf001._digest(path) for path in sources})
    result["preregistrations"] = {name: hf001._digest(Path(repo_root) / "research/experiments" / name / "preregistration.md")
        for name in (EXPERIMENT_ID, av001.EXPERIMENT_ID, hf001.EXPERIMENT_ID)}
    return result


def _chart_records(prepared):
    return {rule: [{"month": row["month"], "U": sorted(row["universe"].index), "P": sorted(row["P"]), "B": sorted(row["B"]),
        "F_vol": sorted(row["F_vol"]), "F_raise": sorted(row["F_raise"]),
        "charts": {name: frame.reset_index().astype(object).where(pd.notna(frame.reset_index()), None).to_dict("records")
                   for name, frame in row["charts"].items()}} for row in prepared["selections"][rule]] for rule in RULES}


def registry_rows(payload, result_file):
    rows = []
    for variant in VARIANTS:
        metrics = {"stage": "design_validation", "result_file": result_file,
            "combined_verdict": payload["variants"][variant]["verdict"],
            "rules": {rule: {key: payload["rules"][rule][variant][key] for key in
                ("primary", "filtered_months", "removed_stock_cohorts", "holm_p", "verdict", "judgement_inputs")}
                for rule in RULES},
            "delisting_verdicts": {rule: payload["delisting_sensitivity"][rule][variant]["verdict"] for rule in RULES}}
        experiment_registry.encode_metrics(metrics)
        rows.append((variant, metrics, metrics["combined_verdict"]))
    return rows


def publish(payload, *, repo_root=PROJECT_ROOT):
    _registration(repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = ".json.gz" if len(encoded.encode("utf-8")) > common.RESULT_GZIP_THRESHOLD_BYTES else ".json"
    relative = Path("research/experiments") / EXPERIMENT_ID / "results" / (stamp + suffix)
    rows = registry_rows(payload, relative.as_posix())  # Validate all three before writing anything.
    lines = [f"# {EXPERIMENT_ID}", "", "Passed variants: " + ", ".join(payload["passed_variants"]), "",
        "Exploratory improvement over B; 2025 is a stability diagnostic. No holdout is used.", "",
        "| Variant | Rule | Valid months | Removal months | Mean d | NW t | Holm p | x1.5 d | Control share | Verdict |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for variant in VARIANTS:
        for rule in RULES:
            r = payload["rules"][rule][variant]
            lines.append(f"| {variant} | {rule} | {r['primary']['count']} | {r['filtered_months']} | {r['primary']['mean']} | {r['primary']['t_stat']} | {r['holm_p']} | {r['cost_sensitivity']['1.5']['difference']} | {r['random_control']['share_of_controls']} | {r['verdict']} |")
    lines += ["", *[f"- Combined {variant}: {payload['variants'][variant]['verdict']}" for variant in VARIANTS]]
    lines += ["", "Full selections, accounting, controls, sensitivity and input/code SHA-256 are in the JSON.",
              "", "Interpretations:", "", *["- " + note for note in INTERPRETATIONS]]
    common.write_result_files(Path(repo_root) / relative.parent, stamp, encoded, lines)
    for variant, metrics, verdict in rows:
        experiment_registry.record_trial(EXPERIMENT_ID, variant, metrics, verdict, repo_root=repo_root,
            note="S/O separately judged; combined CH001 verdict; no holdout")
    return payload


def compute_design(*, data_dir, repo_root, prices=None, sessions=None, _attempt=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    injected = prices is not None
    sessions, _ = calendar_plan(sessions)
    if prices is not None and (prices.empty or common._sessions(prices).max() > PRICE_END):
        raise ValueError("CH001 requires nonempty evaluation prices strictly before 2026")
    paths = [] if injected else d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    hashes = source_hashes(data_dir, paths, repo_root=repo_root, prices=prices)
    if _attempt is not None:
        _attempt["source_hashes"] = hashes
    prices, paths, sessions, prepared = prepare_inputs(data_dir=data_dir, repo_root=repo_root, prices=prices, sessions=sessions)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS)
    rules, losses, v2, comparisons, assertions = {}, {}, {}, {}, {}
    for rule in RULES:
        reason = "data_incomplete" if not prepared["raise_complete"] else (
            "sensitivity_unavailable" if rule == "O" and not prepared["sensitivity_available"] else None)
        if reason:
            rules[rule] = judge_variants({v: unavailable_metrics(reason) for v in VARIANTS}, registration.fields, trials)
            losses[rule] = judge_variants({v: unavailable_metrics(reason) for v in VARIANTS}, registration.fields, trials)
            continue
        selections = prepared["selections"][rule]
        observations = forward_observations(prices, selections, sessions, repo_root=repo_root)
        assertions[rule] = assert_filters_off(observations, selections)
        rules[rule], losses[rule] = evaluate_rule(observations, selections, registration.fields, trials)
        v2[rule] = cost_model_v2_metrics(observations, prices, selections)
        comparisons[rule] = hf001_comparison(observations, selections, repo_root=repo_root)
        _check_memory()
    variants = {v: {"verdict": av001.combine_verdict(rules["S"][v]["verdict"], rules["O"][v]["verdict"],
        sensitivity_available=prepared["sensitivity_available"])} for v in VARIANTS}
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "design_validation", "variants": variants,
        "passed_variants": [v for v in VARIANTS if variants[v]["verdict"] == "pass"],
        "rules": rules, "delisting_sensitivity": losses, "cost_model_v2": v2,
        "hf001_comparison": comparisons, "assertions": assertions,
        "selections": _chart_records(prepared), "counts": prepared["counts"], "rights_events": prepared["event_counts"],
        "filing_rules": prepared["filing_rules"], "date_rule_differences": prepared["date_rule_differences"],
        "version_provenance": prepared["version_provenance"], "L": prepared["L"],
        "listing_status": prepared["listing_status"], "listing_version": prepared["listing_status"]["listing_version"],
        "coverage": prepared["coverage"], "integrity_price_access": prepared["integrity_price_access"],
        "source_hashes": hashes,
        "prereg_commit": registration.commit_hash,
        "preregistration_sha256": hashes["preregistrations"][EXPERIMENT_ID],
        "code_commit": experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
        "library_versions": {"python": platform.python_version(), **{name: importlib.metadata.version(name)
                             for name in ("numpy", "pandas", "exchange-calendars", "PyYAML")}},
        "interpretations": INTERPRETATIONS,
        "performance": {"runtime_seconds": time.perf_counter() - started,
                        "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    _check_memory()
    final_paths = [] if injected else d001._price_files(Path(data_dir) / "market/krx_daily", sessions[0], sessions[-1], repo_root=repo_root)
    if hashes != source_hashes(data_dir, final_paths, repo_root=repo_root, prices=prices if injected else None):
        raise ValueError("CH001 inputs or code changed during execution; publication aborted")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, VARIANTS, repo_root) as attempt:
            payload = compute_design(data_dir=data_dir, repo_root=repo_root, prices=prices, sessions=sessions, _attempt=attempt)
            return publish(payload, repo_root=repo_root)


def fixture_timing():
    """Only synthetic prices/returns; exercises the actual filter/entry/control paths."""
    sessions = pd.bdate_range("2023-01-02", periods=300)
    codes = [f"{pos * 10:06d}" for pos in range(1, 129)]
    index = pd.MultiIndex.from_product([sessions, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000., "high": 10500., "low": 9900., "close": 10000.,
        "base_price": 10000., "volume": 100., "trading_value": 2e9,
        "ret_1d": .001, "market": "KOSPI", "calendar_status": "verified"}, index=index)
    started = time.perf_counter()
    decision = sessions[251]
    heat_filter(prices, codes, decision, sessions)
    wick_filter(prices, codes, decision, sessions)
    anchor_filter(prices, codes, pd.Series("KOSPI", index=codes), decision, sessions)
    filter_seconds = time.perf_counter() - started
    universe = pd.DataFrame({"stock_code": codes, "decision_date": decision,
        "trade_date": sessions[252], "avg_trading_value_20d": 2e9, "phase": 2})
    started = time.perf_counter()
    observations = hf001.forward_observations(prices, universe, sessions)
    forward_seconds = time.perf_counter() - started
    # Twelve synthetic cohorts, with actual E/removed subsets and a shared RNG.
    observations = pd.concat([observations.assign(decision_date=day) for day in sessions[251:263]], ignore_index=True)
    rows = [{"month": str(pos), "decision_date": day.date().isoformat(), "valid": True,
        "B": codes, "E": codes[:-8], "removed": codes[:12],
        "costs": {"1.0": {"difference": 0., "baseline_net": float((group.gross - group["cost_1.0"]).mean())}}}
        for pos, (day, group) in enumerate(observations.groupby("decision_date", sort=True))]
    started = time.perf_counter()
    random_controls(observations, rows)
    return {"stocks": len(codes), "sessions": len(sessions), "control_months": len(rows),
            "paths": N_CONTROLS, "filter_seconds": filter_seconds, "forward_seconds": forward_seconds,
            "control_seconds": time.perf_counter() - started}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    registration = _registration(repo_root)
    prices, paths, sessions, prepared = prepare_inputs(data_dir=data_dir, repo_root=repo_root)
    counts = {rule: {variant: [{"month": row["month"],
        **{scope: filter_counts(row["charts"][variant], codes) for scope, codes in
           (("U", row["universe"].index), ("P", row["P"]), ("B", row["B"]))}}
        for row in prepared["selections"][rule]] for variant in VARIANTS} for rule in RULES}
    timing = fixture_timing()
    cohorts = sum(len(row["universe"]) for rule in RULES for row in prepared["selections"][rule])
    forward_seconds = timing["forward_seconds"] * cohorts / timing["stocks"] * len(sessions) / timing["sessions"]
    control_seconds = timing["control_seconds"] * len(hc002.MONTHS) / timing["control_months"] * 12
    names = prices.index.get_level_values("stock_code").nunique()
    peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    extra_mib = names * len(sessions) * 160 / 1024**2 + 512
    base_seconds = prepared["preparation_seconds"] + forward_seconds + control_seconds
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash,
        "variants": VARIANTS, "rules": RULES, "planned_months": len(hc002.MONTHS),
        "read_start": sessions[0].date().isoformat(), "read_end": sessions[-1].date().isoformat(),
        "price_files": len(paths), "price_rows": len(prices), "listing_status": prepared["listing_status"],
        "listing_incomplete_months": sum(not row["listing"]["covered"] for row in prepared["coverage"]),
        "L": prepared["L"], "financial_dates": prepared["filing_rules"],
        "sensitivity_available": prepared["sensitivity_available"], "filter_counts": counts,
        "integrity_price_access": prepared["integrity_price_access"], "fixture_timing": timing,
        "projection": {"measured_preparation_seconds": prepared["preparation_seconds"],
            "measured_peak_rss_mib": peak_mib, "full_run_seconds_range": [base_seconds, base_seconds * 2 + 120],
            "peak_rss_mib_estimate": peak_mib + extra_mib, "target_rss_bytes": MAX_RSS_BYTES,
            "basis": "Actual full S/O preparation and filters, synthetic forward scaled by stock-cohorts/session span, 12 rule/variant/loss controls; extra RSS allows 160 bytes per name/session plus 512 MiB serialization. Estimates, not measured execution."},
        "interpretations": INTERPRETATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: offline selections/filters audit without forward portfolio outcomes")
    mode.add_argument("--execute", action="store_true", help="evaluate and append the fixed three trials")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment()
        print("Passed variants: " + ", ".join(result["passed_variants"]))
    else:
        print(f"{EXPERIMENT_ID} dry-run; variants={','.join(VARIANTS)}; rules=S,O", flush=True)
        print("Preparing real U/P/B and chart computability; no forward portfolio outcomes or publication.", flush=True)
        plan = dry_run()
        print(f"Preregistration: {plan['prereg_commit']}; months={plan['planned_months']}")
        print(f"Evaluation price span: {plan['read_start']}..{plan['read_end']}; files={plan['price_files']}; rows={plan['price_rows']}")
        status = plan["listing_status"]
        print(f"Listing supplement complete={status['supplement_complete']}; reason={status['reason']}; completed={status['completed_corps']}/{status['target_count']}; incomplete months={plan['listing_incomplete_months']}")
        print(f"Actual-public-date L={plan['L']} days; O sensitivity available={plan['sensitivity_available']}")
        print("Financial date counts: " + json.dumps(plan["financial_dates"], ensure_ascii=False))
        for rule in RULES:
            for variant in VARIANTS:
                rows = plan["filter_counts"][rule][variant]
                totals = {key: sum(row["B"][key] for row in rows) for key in
                          ("names", "evaluable", "uncomputable", "partial_uncomputable", "removed", "boundary_rsi_69_71", "boundary_percentile_029_031")}
                reasons = Counter()
                for row in rows:
                    reasons.update(row["B"]["uncomputable_by_reason"])
                print(f"Filter computability {rule}/{variant}: " + json.dumps({**totals, "uncomputable_by_reason": dict(reasons)}))
        access = plan["integrity_price_access"]
        print(f"Supplement integrity only: hashed {len(access['nonprotected_2026_hash_access'])} nonprotected 2026 price files; {len(access['protected_2026_frozen_hashes_only'])} protected files use frozen hashes only (never opened).")
        print("Fixture timing: " + json.dumps(plan["fixture_timing"]))
        print("Projected runtime/RSS: " + json.dumps(plan["projection"]))
        print("No real forward portfolio returns evaluated; no registry/results writes; no holdout mode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
