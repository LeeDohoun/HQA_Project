"""HF001 preregistered HC002 avoidance filters; offline dry-run is the default."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001, hc002
from src.ingestion.storage import read_rows
from src.research import market_regimes


EXPERIMENT_ID = "HF001_hegemony_filters"
VARIANTS = ("f_vol", "f_raise", "f_both")
MULTIPLIERS = (1.0, 1.5, 2.0)
N_CONTROLS = 200
LISTING_START = pd.Timestamp("2016-06-01")
LISTING_END = pd.Timestamp("2025-11-30")
PRICE_LIMITS = ((pd.Timestamp("2015-01-01"), 0.15), (pd.Timestamp("2015-06-15"), 0.30))
LISTING_COLUMNS = ("rcept_no", "rcept_dt", "stock_code", "report_nm")
ASSUMPTIONS = [
    "U is the exact HC002 build_universe path; decision P is its phase-2 set, independently asserted against HC002 universe_on_decision before execution exclusions.",
    "Unfilled entries (missing/untradable/limit-up) retain their decision weight as cash, with zero return and cost. Entry exclusions never redistribute weights using future prices; exit problems never delete selected names.",
    "Price limits are 15% before 2015-06-15 and 30% thereafter, with HC002's existing 0.5 percentage-point tick-rounding allowance. Missing base_price uses the previous session close, as in signal_eval.",
    "A position without a tradable scheduled close and without a later positive-volume trade through 2025-12-31 is treated as delisted, not independently confirmed delisting. Later prices classify resumption only; they never determine an exit value or signal.",
    "Limit-down closes are excluded as fills. Their last permissible pre-exit close is a valuation; no holding window is extended. Existing HC002 adjusted-return chaining and explicitly counted raw-price fallback are retained.",
    "Listing coverage requires every calendar-day archive from the session preceding the oldest of 126 known-event sessions through the day before decision. Weekend/holiday filings matter. Empty daily archives prove a completed empty listing; min/max dates alone do not prove coverage.",
    "Titles are matched literally. Any title containing 정정 is conservatively excluded as a new event; any containing 철회 is counted and never used. Receipt duplicates must agree; missing stock identifiers are reported, never inferred from future aliases.",
    "Volatility requires all 60 verified session returns, including decision. Invalid/missing log returns are uncomputable and never trigger exclusion. Market membership is the decision-date KRX market.",
    "Each variant independently starts default_rng(0). Draw order is path, then ascending decision month, then stock-code-sorted P; choice(size=removed, replace=False) is called even for zero/all removed. The sensitivity rerun uses identical draws.",
    "NW lag 1 uses the sequence of valid monthly cohorts without fabricated observations for gaps. Bartlett weight is 1/2; autocovariances divide by n; the variance of the mean divides by n again. No finite-sample correction; undefined tests have p=1 in Holm(3).",
    "2025 same-sign is reported as a stability diagnostic, as HF001 section 5 specifies, and is not an additional pass criterion or independent validation. Every judged variant needs 36 valid months; event counts cannot substitute.",
    "Lower 5% mean uses the worst ceil(0.05*n) monthly cohorts. Drawdown starts at wealth 1 and stops at zero after a total loss; it is a stitched-cohort diagnostic, not executable portfolio performance.",
    "Year and short-selling-ban diagnostics use decision dates (ban inclusive 2023-11-06..2025-03-30). Liquidity diagnostics compare P and U inside each existing cost-model ADV bucket; empty P buckets are uncomputable, not cash months.",
    "Both portfolios, U benchmark and controls pay full entry-date round-trip costs for filled positions every month. Multipliers scale commission/spread/slippage but never sell tax; filtered-empty months are zero-return, zero-cost cash without replacement names.",
    "Current company classifications and latest saved KRX revisions do not reconstruct historical vintages. Source SHA-256 hashes identify the actual local archives, including all quarterly archives read by the unchanged HC002 loader; no 2026 prices or holdout evaluation are used.",
]
INTERPRETATIONS = {
    "note": "HF001 is follow-up exploration. Its primary is monthly filtered net minus pre-filter net, not IC or standalone profitability; f_both is fixed primary, all three variants are judged.",
    "metrics": {
        "cost_model_v2": common.COST_MODEL_V2_INTERPRETATION,
        "short_sale_regimes": "Reporting-only splits of the primary filtered-minus-baseline net metric by documented FSC short-sale intervals, using decision dates; no subgroup verdicts.",
        "sets": "U and decision P are HC002 eligible and phase-2 codes; removed/remaining partition P. No replacement names. hc002_phase2_equal is an execution assertion.",
        "primary": "count/mean/std summarize finite monthly differences; std is sample ddof=1. standard_error/t_stat/p_one_sided use NW Bartlett lag 1 and a one-sided standard normal upper tail. Long-run variance has divisor n, no small-sample correction.",
        "holm_p": "Holm step-down adjusted p over exactly f_vol, f_raise, f_both, including p=1 for undefined tests, separately for each delisting valuation.",
        "judgement": "36 valid months, mean difference >0, cost x1.5 mean difference >0, control share <=0.05, Holm p<=0.05 AND strict t>2 (t>3 when existing HF001 trials plus this run's three rows exceed 20).",
        "cost_sensitivity": "For each of 1.0/1.5/2.0: monthly equal-weight filtered, baseline P and U net returns; difference=filtered-baseline, universe_excess=filtered-U. Full round-trip costs at entry price/date/market and decision ADV; tax unscaled.",
        "removed_minus_remaining": "Mean removed-bundle net minus remaining-bundle net at x1 cost, only months where both bundles contain stocks. No-removal and all-removed months report null.",
        "risk": "Count, sample monthly volatility, mean of worst ceil(5%*count) cohorts and initial-wealth-inclusive maximum drawdown, separately for filtered and baseline x1 net returns. No annualization; drawdown is a diagnostic.",
        "random_control": "200 entire-period paths; each month removes the actual filter count without replacement from sorted P. Path mean differences compared to actual mean; share_of_controls is count(path mean>=actual)/200, without plus-one correction. p05/p50/p95 summarize path means; path_means retain every path.",
        "period_diagnostics": "by_year, design, validation and ban inside/outside summarize decision-month differences and costs on their own valid cohorts. validation_year_same_sign compares signs, diagnostic only. Liquidity buckets use decision ADV bounds, lower inclusive/upper exclusive.",
        "filter_counts": "Valid, skipped, listing-coverage-excluded, actually-filtered and cash month counts; removed/remaining stock-cohort counts. Volatility-uncomputable counts are reported for U and P; high-volatility cutoff is market-specific max-tie ascending rank/n >0.80.",
        "raise_events": "Only original titles containing 유상증자결정 without 무상/정정/철회. known_date is first exchange session strictly after receipt, event age is decision session index minus known index, restricted to 0..125. Counts include duplicate, correction, bonus, withdrawal and missing-identifier listings.",
        "exit_counts": "Position-cohort counts for filled/unfilled entries, stale/limit-down valuations, halt resumption, presumed delistings and raw fallback. exit_date is valuation date, scheduled_exit_date is the original 20th session. No no_later_price deletion.",
        "delisting_sensitivity": "Repeat every portfolio, benchmark, control, NW and Holm judgement with presumed delisted stocks' gross=-1 and the same full costs/weights. Any changed variant verdict becomes delisting_sensitive and cannot pass.",
        "coverage": "Archive existence and missing calendar/session days, not proof of signal computability or original historical vintages. Per-month listing coverage guards the entire known-event window; price coverage guards decision ADV and original holding horizon.",
        "source_hashes": "SHA-256 per actual KRX, quarterly, company-profile and DART listing file, plus runner/shared code and preregistration; injected prices are separately marked as in-memory with a content digest.",
        "performance": "runtime_seconds before publication and peak_memory_mib process-wide maximum RSS on Linux/WSL; these are implementation diagnostics.",
        "verdict": "f_both determines the experiment verdict; other variants remain independently judged. risk_only requires nonpositive primary and both better volatility and lower-5% tail; it never passes. Missing required observations/statistics is insufficient.",
    },
}


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    fields = registration.fields
    if fields["variants_planned"] != 3 or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("HF001 requires three fixed variants, no holdout and no LLM")
    experiment_registry.verify_preregistration(hc002.EXPERIMENT_ID, repo_root)
    return registration


def price_limit(day):
    day = pd.Timestamp(day)
    if day < PRICE_LIMITS[0][0]:
        raise ValueError("price-limit date must be on or after 2015-01-01")
    return next(limit for start, limit in reversed(PRICE_LIMITS) if day >= start)


def _listing_files(data_dir):
    directory = Path(data_dir) / "disclosures/dart_full/list"
    # Missing coverage is an explicit exclusion of raise variants, never no-events data.
    return {pd.Timestamp(path.stem): path for path in sorted(directory.glob("*/*.jsonl"))
            if len(path.stem) == 8 and path.stem.isdigit()
            and LISTING_START <= pd.Timestamp(path.stem) <= LISTING_END}


def load_listings(files):
    rows = []
    for day, path in files.items():
        for row in read_rows(path):
            if row.get("rcept_dt") != day.strftime("%Y%m%d"):
                raise ValueError(f"listing receipt date does not match archive: {path}")
            rows.append({name: row.get(name) for name in LISTING_COLUMNS})
    return pd.DataFrame(rows, columns=LISTING_COLUMNS)


def rights_events(listings, sessions):
    """Receipt-level events only: corrections/withdrawals never reset the window."""
    if not set(LISTING_COLUMNS).issubset(listings.columns):
        raise ValueError("rights listings require " + ", ".join(LISTING_COLUMNS))
    frame = listings.copy()
    for name in ("rcept_no", "rcept_dt", "report_nm"):
        if not frame[name].map(lambda value: isinstance(value, str)).all():
            raise ValueError(f"listing {name} must be a string")
    if not frame.rcept_no.str.fullmatch(r"[0-9]{14}").all():
        raise ValueError("listing receipt number must contain 14 digits")
    official = pd.to_datetime(frame.rcept_dt, format="%Y%m%d", errors="raise")
    # The receipt number starts with the submission date. Late-evening filings
    # carry the next day's rcept_dt (about 0.7% of rows), KRX notices use other
    # numbering, and a few re-filed documents are listed under their original,
    # earlier rcept_dt. Use the later of the two dates so a document is never
    # treated as known before it existed, and count every disagreement.
    submitted = pd.to_datetime(frame.rcept_no.str[:8], format="%Y%m%d", errors="raise")
    receipts = official.where(official >= submitted, submitted)
    lag = (official - submitted).dt.days
    for _, group in frame.groupby("rcept_no"):
        if len(group.drop_duplicates()) != 1:
            raise ValueError("conflicting duplicate listing receipt")
    counts = {"raw_rows": len(frame), "duplicate_receipts": int(frame.rcept_no.duplicated().sum()),
              "receipt_number_after_rcept_dt": int((lag < 0).sum()),
              "receipt_number_over_7_days_before_rcept_dt": int((lag > 7).sum())}
    frame = frame.drop_duplicates("rcept_no").copy()
    title = frame.report_nm
    withdrawal = title.str.contains("철회", regex=False)
    correction = title.str.contains("정정", regex=False)
    bonus = title.str.contains("무상", regex=False)
    candidate = title.str.contains("유상증자결정", regex=False)
    counts.update(withdrawal_titles=int(withdrawal.sum()), correction_titles=int((candidate & correction).sum()),
                  bonus_titles=int((candidate & bonus).sum()))
    original = candidate & ~withdrawal & ~correction & ~bonus
    codes = frame.stock_code.fillna("")
    if not codes.map(lambda value: isinstance(value, str)).all():
        raise ValueError("listing stock_code must be a string or missing")
    frame["stock_code"] = codes.str.strip()
    counts["missing_stock_code_events"] = int((original & frame.stock_code.eq("")).sum())
    frame = frame.loc[original & frame.stock_code.ne("")].copy()
    days = signal_eval._dates(sessions).sort_values().unique()
    known = days.searchsorted(receipts.loc[frame.index], side="right")
    frame["known_position"] = known
    frame["known_date"] = [days[pos] if pos < len(days) else pd.NaT for pos in known]
    counts["original_events"] = len(frame)
    return frame.sort_values(["known_position", "stock_code", "rcept_no"]), counts


def listing_coverage(decision, sessions, files):
    days = signal_eval._dates(sessions).sort_values().unique()
    pos = days.get_loc(pd.Timestamp(decision))
    if pos < 126:
        return {"covered": False, "reason": "incomplete_126_session_calendar", "required_days": None,
                "available_days": 0, "missing_days": []}
    start, end = days[pos - 126], days[pos] - pd.Timedelta(days=1)
    required = pd.date_range(start, end)
    missing = required.difference(pd.DatetimeIndex(list(files)))
    return {"covered": len(missing) == 0, "reason": None if not len(missing) else "incomplete_listing_window",
            "known_window_start": days[pos - 125].date().isoformat(),
            "receipt_start": start.date().isoformat(), "receipt_end": end.date().isoformat(),
            "required_days": len(required), "available_days": len(required) - len(missing),
            "missing_days": [day.date().isoformat() for day in missing]}


def volatility_filter(prices, universe, decision, sessions):
    """Exactly 60 decision-inclusive log returns; ranks within U and each market."""
    days = signal_eval._dates(sessions).sort_values().unique()
    pos = days.get_loc(pd.Timestamp(decision))
    window = days[max(0, pos - 59):pos + 1]
    history = prices.loc[prices.index.get_level_values("trade_date").isin(window)]
    current = history.xs(pd.Timestamp(decision), level="trade_date")
    returns = history.ret_1d.where(history.calendar_status.eq("verified")).unstack("stock_code")
    returns = returns.reindex(index=window, columns=universe.index)
    logs = np.log1p(returns.where(np.isfinite(returns) & returns.gt(-1)))
    sigma = logs.std(ddof=1).where(logs.count().eq(60))
    result = pd.DataFrame({"volatility_60d": sigma, "market": current.market.reindex(universe.index)})
    result["percentile"] = result.groupby("market", observed=True).volatility_60d.rank(method="max", pct=True)
    result["f_vol"] = result.percentile.gt(0.80)
    return result


def build_selections(prices, *, data_dir, repo_root, sessions, listing_files, events):
    universe, counts, excluded, financials = hc002.build_universe(
        prices, data_dir=data_dir, repo_root=repo_root, sessions=sessions)
    universe = universe.copy()
    for flag in VARIANTS:
        universe[flag] = False
    universe["raise_covered"] = False
    reports = []
    for day, group in universe.groupby("decision_date", sort=True):
        pos = sessions.get_loc(day)
        history = prices.loc[prices.index.get_level_values("trade_date").isin(sessions[pos - 19:pos + 1])]
        reference, _ = hc002.universe_on_decision(history, day, data_dir=data_dir, repo_root=repo_root)
        p = set(group.loc[group.phase.eq(2), "stock_code"])
        expected = set(reference.index[reference.phase.eq(2)])
        if p != expected:
            raise AssertionError(f"HF001 P differs from HC002 phase-2 set on {day.date()}: {sorted(p ^ expected)}")
        eligible = group.set_index("stock_code")
        volatility = volatility_filter(prices, eligible, day, sessions)
        covered = listing_coverage(day, sessions, listing_files)
        known = events.loc[events.known_position.between(pos - 125, pos)]
        vol = group.stock_code.map(volatility.f_vol)
        raised = group.stock_code.isin(known.stock_code)
        universe.loc[group.index, "f_vol"] = vol.to_numpy()
        universe.loc[group.index, "f_raise"] = raised.to_numpy()
        universe.loc[group.index, "f_both"] = (vol | raised).to_numpy()
        universe.loc[group.index, "raise_covered"] = covered["covered"]
        reports.append({"month": str(day.to_period("M")), "decision_date": day.date().isoformat(),
                        "U": sorted(eligible.index), "P": sorted(p), "hc002_phase2_equal": True,
                        "vol_uncomputable_U": int(volatility.volatility_60d.isna().sum()),
                        "vol_uncomputable_P": int(volatility.reindex(sorted(p)).volatility_60d.isna().sum()),
                        "volatility": [{"stock_code": code, "market": row.market,
                                        "volatility_60d": float(row.volatility_60d) if pd.notna(row.volatility_60d) else None,
                                        "percentile": float(row.percentile) if pd.notna(row.percentile) else None}
                                       for code, row in volatility.iterrows()], "listing": covered})
    return universe, counts, reports, excluded, financials


def forward_observations(prices, universe, sessions, *, repo_root=PROJECT_ROOT):
    """Value every decision-selected slot; never delete an entered position."""
    common.guard_prices(prices, repo_root=repo_root)
    columns = [*universe.columns, "gross", "gross_delisting_loss", "filled", "reason", "exit_date",
               "scheduled_exit_date", "delisted", "halted_exit", "stale_exit", "limit_down_exit",
               "unadjusted_fallback", "bucket", *(f"cost_{m}" for m in MULTIPLIERS)]
    if universe.empty:
        return pd.DataFrame(columns=columns)
    needed = set(universe.stock_code)
    fields = ["open", "close", "volume", "market", "ret_1d", *(["base_price"] if "base_price" in prices else [])]
    by_stock = {code: group.droplevel("stock_code").reindex(sessions)
                for code, group in prices[fields].groupby(level="stock_code", observed=True) if code in needed}
    thresholds = np.array([price_limit(day) - 0.005 for day in sessions])
    positions = np.arange(len(sessions))
    cached = {}
    rows = []
    for selected in universe.to_dict("records"):
        frame = by_stock[selected["stock_code"]]
        entry = sessions.get_loc(pd.Timestamp(selected["trade_date"]))
        target = entry + hc002.HORIZON - 1
        if target >= len(sessions) or sessions[target] >= hc001.CUTOFF:
            raise ValueError("HF001 holding horizon must be complete and precede 2026")
        code = selected["stock_code"]
        if code not in cached:
            opening, closing, volume = (frame[name].to_numpy(dtype=float) for name in ("open", "close", "volume"))
            bases = frame.base_price.to_numpy(dtype=float) if "base_price" in frame else np.full(len(frame), np.nan)
            bases = np.where(np.isnan(bases), np.r_[np.nan, closing[:-1]], bases)
            has_base = np.isfinite(bases) & (bases > 0)
            down = has_base & (closing <= bases * (1 - thresholds))
            trades = np.isfinite(closing) & (closing > 0) & np.isfinite(volume) & (volume > 0)
            permissible = np.isfinite(closing) & (closing > 0) & ~down
            last_close = np.maximum.accumulate(np.where(permissible, positions, -1))
            last_trade = np.maximum.accumulate(np.where(permissible & trades, positions, -1))
            cached[code] = (opening, closing, volume, bases, has_base, down, trades, last_close, last_trade)
        opening, closing, volume, bases, has_base, down, trades, last_close, last_trade = cached[code]
        reason = None
        if not np.isfinite(opening[entry]):
            reason = "missing_entry_price"
        elif opening[entry] <= 0 or not np.isfinite(volume[entry]) or volume[entry] <= 0:
            reason = "untradable_entry"
        elif not has_base[entry]:
            raise ValueError("entry price-limit reference is missing")
        elif opening[entry] >= bases[entry] * (1 + thresholds[entry]):
            reason = "limit_up_entry"
        row = {**selected, "scheduled_exit_date": sessions[target], "exit_date": pd.NaT,
               "filled": reason is None, "reason": reason, "gross": 0.0, "gross_delisting_loss": 0.0,
               "delisted": False, "halted_exit": False, "stale_exit": False, "limit_down_exit": False,
               "unadjusted_fallback": False,
               "bucket": int(np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, selected["avg_trading_value_20d"], side="right"))}
        for m in MULTIPLIERS:
            row[f"cost_{m}"] = 0.0
        if reason is None:
            resumed = trades[target + 1:].any()
            delisted = not trades[target] and not resumed
            # Delisting uses actual last trading close (including liquidation trading).
            actual = int(last_trade[target] if delisted else last_close[target])
            if actual < entry:
                raise ValueError(f"entered stock has no permissible exit close: {selected['stock_code']}")
            chain = frame.ret_1d.iloc[entry + 1:actual + 1].to_numpy(dtype=float)
            adjusted = np.isfinite(closing[entry]) and closing[entry] > 0 and np.isfinite(chain).all() and (chain >= -1).all()
            gross = closing[entry] / opening[entry] * np.prod(1 + chain) - 1 if adjusted else closing[actual] / opening[entry] - 1
            row.update(gross=float(gross), gross_delisting_loss=-1.0 if delisted else float(gross),
                       delisted=bool(delisted), halted_exit=bool(not trades[target] and resumed),
                       stale_exit=bool(not trades[target] or down[target]), limit_down_exit=bool(down[target]),
                       unadjusted_fallback=not bool(adjusted), exit_date=sessions[actual])
            for m in MULTIPLIERS:
                row[f"cost_{m}"] = cost_model.round_trip_cost(float(opening[entry]), frame.market.iloc[entry],
                    sessions[entry].date(), float(selected["avg_trading_value_20d"]), multiplier=m)
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def newey_west(values):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("NW observations must all be finite")
    n = len(values)
    mean = float(values.mean()) if n else None
    result = {"count": n, "mean": mean, "std": float(values.std(ddof=1)) if n > 1 else None,
              "standard_error": None, "long_run_variance": None, "t_stat": None, "p_one_sided": 1.0}
    if n > 1:
        if np.ptp(values) == 0:
            result.update(mean=float(values[0]), std=0.0, standard_error=0.0, long_run_variance=0.0)
            return result
        centered = values - mean
        variance = float((np.dot(centered, centered) + np.dot(centered[1:], centered[:-1])) / n)
        result["long_run_variance"] = variance
        if variance > 0:
            se = math.sqrt(variance / n)
            t = mean / se
            result.update(standard_error=se, t_stat=t, p_one_sided=0.5 * math.erfc(t / math.sqrt(2)))
    return result


def holm(p_values):
    if set(p_values) != set(VARIANTS):
        raise ValueError("HF001 Holm family must contain exactly the three judged variants")
    ordered = sorted(VARIANTS, key=lambda name: (p_values[name], VARIANTS.index(name)))
    adjusted, previous = {}, 0.0
    for rank, name in enumerate(ordered):
        p = p_values[name]
        if not np.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Holm requires finite probabilities in [0, 1]")
        previous = max(previous, min(1.0, (3 - rank) * p))
        adjusted[name] = previous
    return adjusted


def _mean(values):
    values = [value for value in values if value is not None]
    return float(np.mean(values)) if values else None


def risk_metrics(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"count": 0, "volatility": None, "lower_5pct_mean": None, "max_drawdown": None}
    wealth = np.r_[1.0, np.cumprod(1 + values)]
    ruin = np.flatnonzero(values <= -1)
    if len(ruin):
        wealth[ruin[0] + 1:] = 0.0
    return {"count": len(values), "volatility": float(values.std(ddof=1)) if len(values) > 1 else None,
            "lower_5pct_mean": float(np.sort(values)[:math.ceil(len(values) * 0.05)].mean()),
            "max_drawdown": float(np.max(1 - wealth / np.maximum.accumulate(wealth)))}


def _monthly(frame, variant, *, loss=False):
    rows, groups = [], []
    gross = "gross_delisting_loss" if loss else "gross"
    for day, u in frame.groupby("decision_date", sort=True):
        if variant != "f_vol" and not u.raise_covered.all():
            continue
        p = u.loc[u.phase.eq(2)].sort_values("stock_code", kind="stable")
        if p.empty:
            continue
        removed, kept = p.loc[p[variant]], p.loc[~p[variant]]
        row = {"decision_date": day.date().isoformat(), "P": p.stock_code.tolist(),
               "removed": removed.stock_code.tolist(), "remaining": kept.stock_code.tolist(),
               "cash": kept.empty, "costs": {}}
        for m in MULTIPLIERS:
            def net(group):
                return float((group[gross] - group[f"cost_{m}"]).mean()) if len(group) else 0.0
            filtered, baseline, benchmark = net(kept), net(p), net(u)
            row["costs"][str(m)] = {"filtered_net": filtered, "baseline_net": baseline, "universe_net": benchmark,
                                   "difference": filtered - baseline, "universe_excess": filtered - benchmark,
                                   "filtered_cost": float(kept[f"cost_{m}"].mean()) if len(kept) else 0.0,
                                   "baseline_cost": float(p[f"cost_{m}"].mean())}
        row["removed_minus_remaining"] = (
            float((removed[gross] - removed["cost_1.0"]).mean()) - row["costs"]["1.0"]["filtered_net"]
            if len(removed) and len(kept) else None)
        rows.append(row)
        groups.append(p)
    return rows, groups


def random_controls(groups, rows, *, loss=False):
    rng = np.random.default_rng(0)
    gross = "gross_delisting_loss" if loss else "gross"
    means = []
    if rows:
        # Path-major, not monthly-major: a control is one complete-period path.
        nets = [(group[gross] - group["cost_1.0"]).to_numpy(dtype=float) for group in groups]
        for _ in range(N_CONTROLS):
            differences = []
            for net, row in zip(nets, rows):
                removed = rng.choice(len(net), size=len(row["removed"]), replace=False)
                keep = np.ones(len(net), dtype=bool)
                keep[removed] = False
                filtered = float(net[keep].mean()) if keep.any() else 0.0
                differences.append(filtered - row["costs"]["1.0"]["baseline_net"])
            means.append(float(np.mean(differences)))
    actual = _mean([row["costs"]["1.0"]["difference"] for row in rows])
    return {**signal_eval._control_summary(means, actual), "path_means": means, "seed": 0,
            "paths_planned": N_CONTROLS, "months": len(rows), "draw_order": "path/month/stock_code"}


def _cohort_summary(rows):
    return {"primary": newey_west([row["costs"]["1.0"]["difference"] for row in rows]),
            "cost_sensitivity": {str(m): {key: _mean([row["costs"][str(m)][key] for row in rows])
                                        for key in ("filtered_net", "baseline_net", "universe_net", "difference",
                                                    "universe_excess", "filtered_cost", "baseline_cost")}
                                 for m in MULTIPLIERS},
            "removed_minus_remaining": {"mean": _mean([row["removed_minus_remaining"] for row in rows]),
                                        "count": sum(row["removed_minus_remaining"] is not None for row in rows)},
            "risk": {name: risk_metrics([row["costs"]["1.0"][f"{name}_net"] for row in rows])
                     for name in ("filtered", "baseline")}}


def variant_metrics(frame, variant, *, loss=False, diagnostics=True):
    rows, groups = _monthly(frame, variant, loss=loss)
    result = {**_cohort_summary(rows), "per_month": rows, "random_control": random_controls(groups, rows, loss=loss),
              "filtered_months": sum(bool(row["removed"]) for row in rows),
              "removed_stock_cohorts": sum(len(row["removed"]) for row in rows),
              "cash_months": sum(row["cash"] for row in rows), "skipped_month_count": len(hc002.MONTHS) - len(rows)}
    result["by_year"] = {year: _cohort_summary([row for row in rows if row["decision_date"].startswith(year)])
                         for year in sorted({row["decision_date"][:4] for row in rows})}
    dated_rows = pd.Series(rows, index=[row["decision_date"] for row in rows], dtype=object)
    result["short_sale_regimes"] = {label: _cohort_summary(group.tolist())
                                    for label, group in market_regimes.split_by_regime(dated_rows).items()}
    for period, start, end in (("design", "2017-05-01", "2024-12-31"), ("validation", "2025-01-01", "2025-11-30")):
        result[period] = _cohort_summary([row for row in rows if start <= row["decision_date"] <= end])
    design, validation = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
    result["validation_year_same_sign"] = bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None
    result["validation_sign_used_for_judgement"] = False
    result["ban_period"] = {label: _cohort_summary([row for row in rows if
                            ("2023-11-06" <= row["decision_date"] <= "2025-03-30") == inside])
                            for label, inside in (("inside", True), ("outside", False))}
    if diagnostics:
        result["liquidity_buckets"] = {}
        lower = 0.0
        for bucket, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
            bucket_rows, _ = _monthly(frame.loc[frame.bucket.eq(bucket)], variant, loss=loss)
            result["liquidity_buckets"][str(bucket)] = {**_cohort_summary(bucket_rows),
                "lower_inclusive": lower, "upper_exclusive": float(upper) if np.isfinite(upper) else None}
            lower = float(upper)
    return result


def judge_variants(variants, fields, trial_count_after_run):
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trial_count_after_run > 20 else criteria["core_t_stat_gt"]
    corrected = holm({name: variants[name]["primary"]["p_one_sided"] for name in VARIANTS})
    for name, result in variants.items():
        primary = result["primary"]
        mean15 = result["cost_sensitivity"]["1.5"]["difference"]
        share = result["random_control"]["share_of_controls"]
        inputs = {"observations": primary["count"], "mean_difference": primary["mean"], "t_stat": primary["t_stat"],
                  "p_one_sided": primary["p_one_sided"], "holm_p": corrected[name], "t_threshold": float(threshold),
                  "mean_difference_at_cost_1_5": mean15, "random_control_share": share,
                  "trial_count_after_run": trial_count_after_run}
        result.update(holm_p=corrected[name], judgement_inputs=inputs)
        missing = []
        if primary["count"] < criteria["minimum_observations"]["rebalances"]:
            missing.append(f"valid monthly observations {primary['count']} < 36")
        if primary["t_stat"] is None or share is None or mean15 is None:
            missing.append("required primary statistic, control or cost result is undefined")
        failures = []
        if primary["mean"] is not None and primary["mean"] <= criteria["net_performance_gt"]:
            failures.append("mean filtered-minus-baseline net must exceed 0")
        if mean15 is not None and mean15 <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
            failures.append("mean filtered-minus-baseline net at cost x1.5 must exceed 0")
        if primary["t_stat"] is not None and primary["t_stat"] <= threshold:
            failures.append(f"NW t must exceed {threshold:g}")
        if corrected[name] > 0.05:
            failures.append("Holm(3) one-sided p must be <= 0.05")
        if share is not None and share > criteria["random_control_top_percent"] / 100:
            failures.append("random-control share must be <= 0.05")
        filtered, baseline = result["risk"]["filtered"], result["risk"]["baseline"]
        risk_only = (primary["mean"] is not None and primary["mean"] <= 0
                     and all(filtered[key] is not None and baseline[key] is not None for key in ("volatility", "lower_5pct_mean"))
                     and filtered["volatility"] < baseline["volatility"]
                     and filtered["lower_5pct_mean"] > baseline["lower_5pct_mean"])
        if missing:
            verdict, reasons = "insufficient", missing
        elif risk_only:
            verdict, reasons = "risk_only", ["volatility and lower tail improve but mean net difference is nonpositive"]
        else:
            verdict, reasons = ("fail", failures) if failures else ("pass", ["all HF001 preregistered criteria satisfied"])
        result.update(verdict=verdict, reasons=reasons)
    return variants


def evaluate(frame, fields, trial_count_after_run):
    main = judge_variants({name: variant_metrics(frame, name) for name in VARIANTS}, fields, trial_count_after_run)
    sensitivity = judge_variants({name: variant_metrics(frame, name, loss=True) for name in VARIANTS}, fields, trial_count_after_run)
    for name in VARIANTS:
        main[name]["base_verdict"] = main[name]["verdict"]
        main[name]["delisting_loss_verdict"] = sensitivity[name]["verdict"]
        sensitive = main[name]["verdict"] != sensitivity[name]["verdict"]
        main[name]["delisting_sensitive"] = sensitive
        if sensitive:
            main[name].update(verdict="delisting_sensitive", reasons=[
                f"last-close verdict {main[name]['base_verdict']} differs from -100% delisting verdict {sensitivity[name]['verdict']}"])
    return main, sensitivity


def cost_model_v2_metrics(observations, prices):
    adjusted = common.reprice_costs_v2(observations, prices, "trade_date", observations.filled.astype(bool))
    variants = {}
    for name in VARIANTS:
        rows, _ = _monthly(adjusted, name)
        variants[name] = {**_cohort_summary(rows), "per_month": rows}
    return {"used_for_judgement": False, "interpretation": common.COST_MODEL_V2_INTERPRETATION,
            "variants": variants}


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_hashes(data_dir, price_files, listing_files, prices=None):
    root = Path(data_dir)
    profile = root / "reference/dart_company/companies.jsonl"
    groups = {"krx_daily": [path for _, path in price_files],
              "quarterly": sorted((root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")),
              "company_profile": [profile] if profile.is_file() else [], "dart_listings": list(listing_files.values())}
    result = {name: {str(path.relative_to(root)): _digest(path) for path in paths} for name, paths in groups.items()}
    result["code"] = {str(path.relative_to(PROJECT_ROOT)): _digest(path)
                      for path in (Path(__file__), Path(hc001.__file__), Path(hc002.__file__), Path(cost_model.__file__))}
    if prices is not None:
        result["in_memory_prices"] = hashlib.sha256(pd.util.hash_pandas_object(prices, index=True).to_numpy().tobytes()).hexdigest()
    return result


def write_results(payload, *, repo_root=PROJECT_ROOT):
    """Finite JSON and an HF001-specific summary, without a fabricated IC schema."""
    _registration(repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    lines = [f"# 실험 결과: {EXPERIMENT_ID}", "", f"- 고정 주 변형: f_both; 판정: {payload['verdict']}",
             "- 사유: " + "; ".join(payload["reasons"]), "", "월별 투자 묶음의 필터 후 순수익 − 필터 전 순수익입니다. 2025년은 안정성 진단이며 독립 검증이 아닙니다.", "",
             "| 변형 | 유효 월 | 평균 차이 | NW t | 단측 p | Holm p | 비용 x1.5 차이 | 대조군 비율 | 판정 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for name, result in payload["variants"].items():
        primary = result["primary"]
        lines.append(f"| {name} | {primary['count']} | {primary['mean']} | {primary['t_stat']} | {primary['p_one_sided']} | {result['holm_p']} | {result['cost_sensitivity']['1.5']['difference']} | {result['random_control']['share_of_controls']} | {result['verdict']} |")
    lines += ["", "상장폐지 −100% 재계산의 모든 지표·판정, 연도·금지기간·유동성별 진단, 월별 선정·제외와 원천 파일 SHA-256은 JSON에 기록합니다.",
              "최대 낙폭은 월별 묶음을 이어 붙인 진단이며 운용 성과가 아닙니다.", "", "## interpretations", "", INTERPRETATIONS["note"], ""]
    lines += [f"- {name}: {definition}" for name, definition in INTERPRETATIONS["metrics"].items()]
    lines += ["", "## assumptions", "", *[f"- {note}" for note in payload["assumptions"]]]
    directory = Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = directory / f"{stamp}.json", directory / f"{stamp}.md"
    with paths[0].open("x", encoding="utf-8") as jf:
        with paths[1].open("x", encoding="utf-8") as mf:
            jf.write(encoded)
            mf.write("\n".join(lines) + "\n")
    return paths


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    injected = prices is not None
    if injected:
        common.guard_prices(prices, repo_root=repo_root)
    sessions = hc001._session_calendar() if sessions is None else signal_eval._dates(sessions).sort_values().unique()
    price_files = [] if injected else hc001.d001._price_files(
        Path(data_dir) / "market/krx_daily", hc001.PRICE_START, hc001.PRICE_END, repo_root=repo_root)
    files = _listing_files(data_dir)
    hashes = source_hashes(data_dir, price_files, files, prices if injected else None)
    if prices is None:
        prices = hc001.d001._load_prices(Path(data_dir) / "market/krx_daily", hc001.PRICE_START, hc001.PRICE_END, repo_root=repo_root)
    common.guard_prices(prices, repo_root=repo_root)
    if prices.empty or common._sessions(prices).max() >= hc001.CUTOFF:
        raise ValueError("HF001 prices must be nonempty and precede 2026-01-01")
    if prices.index.has_duplicates:
        raise ValueError("price keys must be unique")
    prices = prices.sort_index()
    events, event_counts = rights_events(load_listings(files), sessions)
    universe, counts, reports, excluded, financials = build_selections(
        prices, data_dir=data_dir, repo_root=repo_root, sessions=sessions, listing_files=files, events=events)
    observations = forward_observations(prices, universe, sessions, repo_root=repo_root)
    trial_count = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3
    variants, sensitivity = evaluate(observations, registration.fields, trial_count)
    primary = variants["f_both"]
    # Coverage is reported for every planned month, even if HC002 has no signal there.
    planned, _ = hc002.rebalance_calendar(sessions)
    listing_months = [{**row, **listing_coverage(row["decision_date"], sessions, files)} for row in planned]
    days = common._sessions(prices)
    exit_flags = ("filled", "delisted", "halted_exit", "stale_exit", "limit_down_exit", "unadjusted_fallback")
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": "f_both", "verdict": primary["verdict"],
               "reasons": primary["reasons"], "prereg_commit": registration.commit_hash,
               "prereg_sha256": _digest(Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md"),
               "variants": variants, "delisting_sensitivity": {"gross_return": -1.0, "variants": sensitivity},
               "cost_model_v2": cost_model_v2_metrics(observations, prices),
               "interpretations": INTERPRETATIONS, "assumptions": ASSUMPTIONS, "source_hashes": hashes,
               "selections": reports, "skipped_months": excluded,
               "counts": {"hc002_by_month": counts, "rights_events": event_counts,
                          **{flag: int(observations[flag].sum()) for flag in exit_flags},
                          "entry_exclusions": observations.reason.dropna().value_counts().to_dict(),
                          "holdout_horizon_exclusions": sum(row["reason"] == "holdout_horizon" for row in excluded),
                          "listing_coverage_excluded_months": sum(not row["covered"] for row in listing_months),
                          "vol_uncomputable_U": sum(row["vol_uncomputable_U"] for row in reports),
                          "vol_uncomputable_P": sum(row["vol_uncomputable_P"] for row in reports)},
               "exit_valuations": [{"decision_date": row.decision_date.date().isoformat(), "stock_code": row.stock_code,
                                    "scheduled_exit_date": row.scheduled_exit_date.date().isoformat(),
                                    "exit_date": row.exit_date.date().isoformat() if pd.notna(row.exit_date) else None,
                                    "gross": row.gross, "gross_delisting_loss": row.gross_delisting_loss,
                                    "reason": row.reason, **{flag: bool(getattr(row, flag)) for flag in exit_flags}}
                                   for row in observations.itertuples()],
               "coverage": {"financials": financials, "planned_months": len(hc002.MONTHS), "listings_by_month": listing_months,
                            "first_session": days.min().date().isoformat(), "last_session": days.max().date().isoformat(),
                            "sessions": len(days), "stocks": int(prices.index.get_level_values("stock_code").nunique()),
                            "calendar_version": f"exchange-calendars:{hc001.calendars.__version__}:XKRX"},
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    for name in VARIANTS:
        result = variants[name]
        experiment_registry.record_trial(EXPERIMENT_ID, name, {
            **result["judgement_inputs"], "base_verdict": result["base_verdict"],
            "delisting_loss_verdict": result["delisting_loss_verdict"], "delisting_sensitive": result["delisting_sensitive"]},
            result["verdict"], repo_root=repo_root, note="fixed HF001 variant; NW(1), Holm(3), full costs; holdout unused")
    write_results(payload, repo_root=repo_root)
    return payload


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    registration = _registration(repo_root)
    sessions = hc001._session_calendar()
    planned, excluded = hc002.rebalance_calendar(sessions)
    prices = hc001.d001._price_files(Path(data_dir) / "market/krx_daily", hc001.PRICE_START, hc001.PRICE_END, repo_root=repo_root)
    price_days = pd.DatetimeIndex([day for day, _ in prices])
    files = _listing_files(data_dir)
    listing_days = pd.DatetimeIndex(list(files))
    months = []
    for row in planned:
        pos = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        required = sessions[pos - 19:pos + 1 + hc002.HORIZON]
        months.append({**row, "price_covered": not len(required.difference(price_days)),
                       "volatility_window_covered": pos >= 59 and not len(sessions[pos - 59:pos + 1].difference(price_days)),
                       "listing": listing_coverage(row["decision_date"], sessions, files)})
    financials, _ = hc001._financial_inventory(data_dir)
    profiles = hc001._industries(data_dir)
    return {"experiment_id": EXPERIMENT_ID, "variants": list(VARIANTS), "data_dir": str(Path(data_dir)),
            "planned_months": len(hc002.MONTHS), "guarded_months": len(planned), "excluded_months": excluded,
            "months": months, "price_files": len(prices), "listing_files": len(files), "financials": financials,
            "industry_profiles": len(profiles), "read_start": "2015-01-01", "read_end": "2025-12-31",
            "first_listing_file": listing_days.min().date().isoformat() if len(files) else None,
            "last_listing_file": listing_days.max().date().isoformat() if len(files) else None,
            "listing_coverage_excluded_months": sum(not row["listing"]["covered"] for row in months),
            "covered_price_months": sum(row["price_covered"] for row in months),
            "maximum_valid_months_from_coverage": {name: sum(row["price_covered"] and
                (name == "f_vol" or row["listing"]["covered"]) for row in months) for name in VARIANTS},
            "minimum_valid_months": registration.fields["pass_criteria"]["design_validation"]["minimum_observations"]["rebalances"],
            "assumptions": ASSUMPTIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print plan and per-month local coverage (default)")
    mode.add_argument("--execute", action="store_true", help="evaluate and record exactly the three local variants")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; f_both months={result['variants']['f_both']['primary']['count']}")
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run; fixed primary=f_both; variants=" + ",".join(VARIANTS))
        print(f"Months: {plan['planned_months']} planned; {plan['guarded_months']} before 2026; prices={plan['covered_price_months']} covered")
        print(f"Entry: next session open; exit: entry-inclusive 20th session close; full round-trip costs; NW(1)/Holm(3). Read span {plan['read_start']}..{plan['read_end']}.")
        print(f"Files: KRX={plan['price_files']}; listings={plan['listing_files']} ({plan['first_listing_file']}..{plan['last_listing_file']}); profiles={plan['industry_profiles']}")
        print(f"Listing-window exclusions for f_raise/f_both: {plan['listing_coverage_excluded_months']}. Historical 2016-06..2022 listing backfill is required for early months.")
        print(f"Maximum valid months from file coverage (before signal checks): {plan['maximum_valid_months_from_coverage']}; each variant requires {plan['minimum_valid_months']}.")
        for row in plan["months"]:
            listing = row["listing"]
            print(f"{row['month']} decision={row['decision_date']} price={row['price_covered']} vol60={row['volatility_window_covered']} listing126={listing['covered']} ({listing['available_days']}/{listing['required_days']} days) reason={listing['reason']}")
        print("Excluded calendar months: " + json.dumps(plan["excluded_months"]))
        print(f"Quarterly directory exists={plan['financials']['directory_exists']}; archives={len(plan['financials']['quarters'])}; rows through 2025={plan['financials']['stored_rows_through_2025']}")
        print("Missing quarterly archives: " + ",".join(plan["financials"]["missing_quarters"]))
        print("No returns evaluated; no random draws, registry rows or result files written. Coverage reports archives, not computable stock signals; execute reports selection/filter counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
