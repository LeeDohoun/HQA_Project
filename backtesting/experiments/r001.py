"""Preregistered industry-adjusted reversal; local, dry-run by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from backtesting import capacity, cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, d001, hc001
from src.ingestion import dart_quarterly
from src.research import industry_map, market_regimes


EXPERIMENT_ID = "R001_industry_adjusted_reversal"
VARIANTS = ("r21", "r42", "r21_profitable")
MONTHS = pd.period_range("2016-02", "2025-11", freq="M")
HORIZON, N_CONTROLS = 20, 200
PRICE_START, PRICE_END = "20150101", "20251231"
CUTOFF = pd.Timestamp("2026-01-01")
PRICE_LIMITS = ((pd.Timestamp("1900-01-01"), 0.15), (pd.Timestamp("2015-06-15"), 0.30))
COST_MULTIPLIERS = (1.0, 1.5, 2.0)
SIGNAL_COLUMNS = {"r21": "x21", "r42": "x42", "r21_profitable": "x21",
                  "raw21": "raw21", "raw42": "raw42", "raw21_profitable": "raw21"}
ASSUMPTIONS = [
    "U is common.universe_filter, including its verified, complete 20-session ADV. Daily b uses that day's U, including the stock; missing member returns make the relevant benchmark undefined. No industry_index lagged membership or raw-return substitution is used.",
    "Industry classifications are current, not historical. Unknown stocks use the U-wide return; a missing company reference file is an error. This is exploratory research using backfilled, latest stored prices, not independent validation.",
    "Decision selections and equal capital weights are fixed before examining entry or exit data. Blocked/missing entries stay in cash (zero return and zero trading cost), without replacement or redistribution, for portfolios, U and controls alike.",
    "Price limits are exactly 15% before 2015-06-15 and 30% thereafter, compared with base_price (or the immediately preceding session's close, as in the existing evaluator). An unavailable base blocks eligibility/entry; no percentage-point tolerance is added.",
    "Adjusted holding returns chain ret_1d after entry's close/open return. Sessions without a usable quote carry the previous mark during suspension; a missing ret_1d on an observed positive close makes the month uncomputable. No raw-price fallback or deletion of selected names is allowed.",
    "A lower-limit exit is valued at the last earlier non-limit close, not assumed executable. Missing exits with later trading use the last earlier close. Without later trading through 2025-12-31, the last trading close is a presumed delisting valuation, including liquidation trading; censoring cannot independently prove delisting. The -100% re-run replaces these returns in all three sets and reuses the same random draws.",
    "Missing quarterly archives make r21_profitable uncomputable, while the other variants remain eligible for judgement. load_quarterly is used unchanged on a temporary view of pre-2026 fiscal archives; receipts strictly precede the decision, and the latest four quarters must be consecutive, same corporation, same CFS/OFS basis and same known currency. No older-window substitution is allowed.",
    "Random controls restart default_rng(0) for each variant, with path outermost and valid decision months in chronological order inside each of 200 paths. Each E is code-sorted before choice(size=k, replace=False); the delisting re-run uses identical paths.",
    "Newey-West lag 1 denotes adjacent calendar months; missing months are not imputed or joined across gaps. Bartlett weight is 1/2, autocovariances have denominator n, and the mean variance is divided by n without finite-sample correction. Undefined t has p=1 in the fixed Holm(3) family.",
    "The 36-month rule applies separately to each variant; the YAML's 300-event alternative is not an additional monthly requirement. The 2025/design sign comparison is reported as a stability diagnostic, not an extra pass gate. r21 remains the fixed primary variant; no best-variant selection is made.",
    "Every entered name pays full round-trip costs each month, including retained names, at entry open/date and decision ADV. Cost multipliers scale commission, spread and slippage, not sell tax. Retention discounts are secondary only and reset after missing months.",
    "IC uses E and the same open-to-exit gross/cash valuations with average ranks. The unadjusted comparison forms its own E with the same rules except omission of b. Liquidity diagnostics partition the fixed selection and compare with the matching U bucket; they do not reselect names.",
    "Short-sale-ban strata use decision dates inclusively from 2023-11-06 to 2025-03-30. Capacity reports the repository p25 estimate and k times minimum selected ADV times 1%. Compounded bundle drawdown is diagnostic; the independent monthly bundles are not an executable continuous portfolio.",
    "DART listing/disclosure files are not used by this price/quarterly signal, so their source hash list is empty. Supplied offline DataFrames are identified by an input digest rather than attributed to local KRX files. File hashes are checked again before publication to detect concurrent source changes.",
]
INTERPRETATIONS = {
    "note": "Three fixed judged variants; primary performance is monthly net portfolio return minus decision-U equal-weight gross return. All other performance diagnostics are secondary.",
    "criteria": {
        "net_performance_gt": {"metric": "excess.mean", "definition": "Arithmetic mean of monthly net excess, strictly positive."},
        "core_t_stat_gt": {"metric": "excess.t_stat", "definition": "Monthly excess mean / Bartlett lag-1 Newey-West standard error; strictly above 2."},
        "core_t_stat_gt_when_trials_gt_20": {"metric": "judgement_inputs.required_t_stat", "definition": "Above 3 when this experiment's existing rows plus this run's three rows exceed 20; other experiments do not count."},
        "multiple_testing": {"metric": "judgement_inputs.holm_p_value", "definition": "One-sided standard-normal upper-tail p, Holm adjusted across all three variants; undefined variants contribute p=1; adjusted p <= 0.05."},
        "random_control_top_percent": {"metric": "random_control.share_of_controls", "definition": "Fraction of 200 full-period path means >= actual mean excess; <= 0.05, including ties."},
        "minimum_observations": {"metric": "excess.count", "definition": "At least 36 valid monthly bundles per variant; |E| < 10 is insufficient."},
        "net_performance_at_cost_multiplier_1_5_gt": {"metric": "cost_sensitivity['1.5'].top_net_excess", "definition": "Same portfolio and U, full round-trip costs x1.5 except unscaled tax; mean excess strictly positive."},
        "delisting_sensitive": {"metric": "delisting_sensitivity.verdict", "definition": "Recalculate portfolios, U, controls, NW, Holm and all pass gates with presumed delistings at -100%. Different verdicts produce delisting_sensitive and never pass."},
    },
    "metrics": {
        "cost_model_v2": common.COST_MODEL_V2_INTERPRETATION,
        "short_sale_regimes": "Reporting-only splits of primary net excess by documented FSC short-sale intervals, using decision dates; no subgroup verdicts.",
        "statistics": "count is finite monthly observations; mean is arithmetic; std is sample dispersion only. standard_error, t_stat and p_value use NW/Bartlett lag 1 with no small-sample correction. by_year uses decision years. Undefined statistics are null, except judged p=1.",
        "per_rebalance": "Decision, entry and scheduled exit dates, U/E/selected code lists and equal capital weight audit the fixed sets. gross is selected mean before costs, universe_gross is U mean before costs; net_m and excess_m subtract the indicated costs and then U. Cash retains its weight. Selected valuations show actual mark date and execution/exit flags.",
        "skipped_months": "Reasons for absent calendar/price sessions, fewer than ten E members or uncomputable fixed-weight returns; no survivor-only return is calculated.",
        "random_control": "paths contain monthly net excess in reported months for each complete path; mean_excess contains their arithmetic means. count/p05/p50/p95/share_of_controls summarize those means, not independent monthly draws or IC permutations.",
        "cost_sensitivity": "top_net is absolute selected net return; top_net_excess/excess are relative to gross U at costs 1, 1.5 and 2. Only costs 1 and 1.5 enter judgement.",
        "ic": "Monthly average-rank Spearman correlation of -X and E's forward gross return; count excludes constant-rank/undefined months; this t is secondary and is never the pass t.",
        "unadjusted_comparison": "Same rules using sum(log(1+r)) without subtracting b; per-month difference is adjusted minus unadjusted net excess, on common valid months; no extra judged variant or registry row.",
        "decomposition": "close_to_open is the unattainable adjusted decision-close to entry-open gap; open_to_exit is attainable gross return. interaction is mean(gap*holding_return); their sum gives close_to_exit. All keep assigned equal weights and cash; judgement uses only open-to-exit net excess.",
        "turnover_based": "retained_fraction is the fraction of assigned selected slots actually entered and held in the previous consecutive observed month; turnover=1-retained_fraction. Secondary net/excess waive costs only on those retained slots.",
        "strata": "Year/ban and liquidity summaries of monthly excess; liquidity partitions fixed selections and uses same-bucket U. Counts are months, not stock rows; no subgroup pass judgement.",
        "capacity": "Repository p25 per-position ADV participation estimate at 1%; capacity_by_rebalance additionally gives k*minimum ADV*1% so every equal position respects that participation assumption.",
        "bundle_drawdown": "Maximum peak-to-trough loss of the product of 1+independent-bundle net returns, including initial capital; null if any bundle loses 100% or more. Diagnostic, not trading-account drawdown.",
        "judgement_inputs": "Observed month count, mean excess, cost-1.5 mean excess, NW t/raw p, Holm p, control share and threshold including all three impending registry rows. design/validation means and their sign comparison are diagnostics only.",
        "counts": "Monthly U, computable-signal, lower-limit, volume, missing-base, profitable, E and selected counts are decision-known. Execution cash/exit flags count stock-months over U, not uniquely confirmed delisted companies; no names are dropped after selection.",
        "coverage": "Local file inventory and exchange-session coverage, planned/covered/missing months, current industry coverage and quarterly archive presence are not proof of point-in-time vintages or complete signal coverage.",
        "source_hashes": "SHA-256 of every KRX daily file read, every quarterly archive available to load_quarterly, the current company reference, and listing files if used (none here). Supplied DataFrame digest is explicitly labelled synthetic/supplied, not a file hash.",
        "performance": "Elapsed seconds before result publication and Linux process peak RSS in MiB; process memory includes imported modules.",
    },
}


def _registration(repo_root):
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    if fields["variants_planned"] != 3 or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("R001 requires three fixed variants, no holdout and no LLM")
    return fields


def price_limit(day):
    return next(rate for start, rate in reversed(PRICE_LIMITS) if pd.Timestamp(day) >= start)


def rebalance_calendar(sessions):
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
        target = entry + HORIZON - 1
        if target >= len(days):
            excluded.append({**row, "reason": "incomplete_calendar_horizon"})
            continue
        row.update(entry_date=days[entry].date().isoformat(), exit_date=days[target].date().isoformat())
        (excluded if days[target] >= CUTOFF else planned).append(
            {**row, "reason": "holdout_horizon"} if days[target] >= CUTOFF else row)
    return planned, excluded


def _quarterly_files(data_dir):
    return sorted(path for path in (Path(data_dir) / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")
                  if int(path.name[:4]) <= 2025)


def _sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _hash_files(paths):
    return {str(path.resolve()): _sha256(path) for path in paths}


def profitable_as_of(day, *, data_dir):
    """Latest consecutive standalone quarters, with the loader's CFS/OFS policy."""
    known = dart_quarterly.load_quarterly(day, data_dir=data_dir)
    if known.empty:
        return set()
    if known.duplicated(["stock_code", "fiscal_quarter"]).any():
        raise ValueError("quarterly stock/quarter identities must be unique")
    known = known.assign(period=pd.PeriodIndex(known.fiscal_quarter, freq="Q"))
    known = known.loc[known.period.dt.end_time < pd.Timestamp(day)]
    profitable = set()
    for code, group in known.groupby("stock_code", sort=True):
        latest = group.sort_values("period").tail(4)
        if len(latest) != 4:
            continue
        expected = pd.period_range(end=latest.period.iloc[-1], periods=4, freq="Q")
        if (list(latest.period) == list(expected) and latest.corp_code.nunique() == 1
                and latest.fs_div.nunique() == 1 and latest.currency.notna().all()
                and latest.currency.nunique() == 1 and np.isfinite(latest.operating_income).all()
                and latest.operating_income.sum() > 0):
            profitable.add(code)
    return profitable


def daily_benchmarks(prices, profiles, sessions, *, repo_root=PROJECT_ROOT):
    """Same-day common U membership, vectorized with exactly its ADV/filter rules."""
    common.guard_prices(prices, repo_root=repo_root)
    if prices.stock_name.isna().any() or not prices.stock_name.map(lambda name: isinstance(name, str) and bool(name.strip())).to_numpy(dtype=bool).all():
        raise ValueError("stock_name is required to exclude SPACs")
    wide = lambda name: prices[name].unstack("stock_code").reindex(sessions)
    values = wide("trading_value").where(wide("calendar_status").eq("verified"))
    adv = values.where(np.isfinite(values)).rolling(20, min_periods=20).mean()
    closes = wide("close")
    spac = wide("stock_name").apply(lambda column: column.str.contains("스팩", regex=False).fillna(False)).astype(bool)
    members = (closes.ge(1000) & np.isfinite(closes) & adv.ge(1e8)
               & wide("market").isin(("KOSPI", "KOSDAQ")) & wide("calendar_status").eq("verified")
               & ~spac)
    members.loc[:, ~members.columns.astype(str).str.endswith("0")] = False
    returns = wide("ret_1d")
    labels = pd.Series({code: profiles.get(code, industry_map.UNCLASSIFIED) for code in members.columns})

    def equal_return(mask):
        count = mask.sum(axis=1)
        valid = np.isfinite(returns) & returns.ge(-1)
        return returns.where(mask).sum(axis=1).div(count).where(count.gt(0) & (valid | ~mask).all(axis=1))

    benchmarks = {industry_map.UNCLASSIFIED: equal_return(members)}
    for label in sorted(set(labels) - {industry_map.UNCLASSIFIED}):
        benchmarks[label] = equal_return(members & labels.eq(label).reindex(members.columns).to_numpy())
    return pd.DataFrame(benchmarks)


def universe_on_decision(prices, day, profiles, *, sessions=None, benchmarks=None,
                         profitable=(), repo_root=PROJECT_ROOT):
    """U, E and selections depend only on prices through the decision close."""
    day = pd.Timestamp(day)
    sessions = common._sessions(prices) if sessions is None else signal_eval._dates(sessions)
    sessions = sessions[sessions <= day]
    ordered = prices if prices.index.is_monotonic_increasing else prices.sort_index()
    history = ordered.loc[sessions[max(0, len(sessions) - 61)]:day].copy() if len(sessions) else ordered.iloc[:0].copy()
    # The streaming loader categorizes names; common's boolean name reduction
    # expects an ordinary string/object Series (categorical all is unsupported).
    history["stock_name"] = history.stock_name.astype(object)
    universe = common.universe_filter(history.loc[history.index.get_level_values("trade_date").isin(sessions[-20:])], day, repo_root=repo_root).sort_index().copy()
    benchmarks = daily_benchmarks(history, profiles, sessions[-61:], repo_root=repo_root) if benchmarks is None else benchmarks.loc[:day]
    returns = history.ret_1d.unstack("stock_code").reindex(index=sessions[-42:], columns=universe.index)
    logs = np.log1p(returns.where(np.isfinite(returns) & returns.gt(-1)))
    for horizon in (21, 42):
        window = logs.tail(horizon)
        raw = window.sum(min_count=horizon)
        adjusted = pd.Series(np.nan, index=universe.index, dtype=float)
        for label in {profiles.get(code, industry_map.UNCLASSIFIED) for code in universe.index}:
            codes = [code for code in universe.index if profiles.get(code, industry_map.UNCLASSIFIED) == label]
            b = benchmarks[label].reindex(window.index)
            b = np.log1p(b.where(np.isfinite(b) & b.gt(-1)))
            adjusted.loc[codes] = window[codes].sub(b, axis=0).sum(min_count=horizon)
        universe[f"x{horizon}"] = adjusted
        universe[f"raw{horizon}"] = raw
    previous = history.close.unstack("stock_code").reindex(sessions[-61:]).shift(1)
    base = universe.base_price.combine_first(previous.iloc[-1].reindex(universe.index))
    has_base = np.isfinite(base) & base.gt(0)
    universe["decision_limit_down"] = has_base & universe.close.le(base * (1 - price_limit(day)) + 1e-9)
    universe["missing_decision_base"] = ~has_base
    universe["decision_volume_positive"] = np.isfinite(universe.volume) & universe.volume.gt(0)
    tradable = has_base & ~universe.decision_limit_down & universe.decision_volume_positive
    universe["profitable"] = universe.index.isin(profitable)
    for variant, column in SIGNAL_COLUMNS.items():
        eligible = tradable & np.isfinite(universe[column])
        if variant.endswith("profitable"):
            eligible &= universe.profitable
        universe[f"eligible_{variant}"] = eligible
        universe[f"selected_{variant}"] = False
        if eligible.sum() >= 10:
            size = max(10, int(eligible.sum()) // 10)
            ordered = universe.loc[eligible].reset_index().sort_values([column, "stock_code"], kind="stable")
            universe.loc[ordered.stock_code.iloc[:size], f"selected_{variant}"] = True
    return universe


def build_universe(prices, *, data_dir, profiles, sessions, repo_root=PROJECT_ROOT):
    available = common._sessions(prices)
    if not available.difference(sessions).empty:
        raise ValueError("price dates must be exchange sessions")
    benchmarks = daily_benchmarks(prices, profiles, sessions[sessions < CUTOFF], repo_root=repo_root)
    planned, excluded = rebalance_calendar(sessions)
    rows, counts = [], []
    for calendar in planned:
        day = pd.Timestamp(calendar["decision_date"])
        if day not in available:
            excluded.append({**calendar, "reason": "missing_decision_price"})
            continue
        universe = universe_on_decision(prices, day, profiles, sessions=sessions, benchmarks=benchmarks,
                                        profitable=profitable_as_of(day, data_dir=data_dir), repo_root=repo_root)
        counts.append({"decision_date": calendar["decision_date"], "universe": len(universe),
                       "unclassified": sum(profiles.get(code, industry_map.UNCLASSIFIED) == industry_map.UNCLASSIFIED for code in universe.index),
                       **{name: int(universe[name].sum()) for name in ("decision_limit_down", "missing_decision_base", "decision_volume_positive", "profitable")},
                       **{variant: {"signal_computable": int(np.isfinite(universe["x42" if variant == "r42" else "x21"]).sum()),
                                    "eligible": int(universe[f"eligible_{variant}"].sum()),
                                    "selected": int(universe[f"selected_{variant}"].sum())} for variant in VARIANTS}})
        rows.append(universe.assign(**{name: pd.Timestamp(calendar[name]) for name in ("decision_date", "entry_date", "exit_date")}).reset_index())
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(), counts, excluded


def forward_observations(prices, universe, *, sessions, repo_root=PROJECT_ROOT):
    """Fixed U slots; no post-selection deletion, raw fallback or extended exit."""
    common.guard_prices(prices, repo_root=repo_root)
    if universe.empty:
        return universe.copy()
    days = sessions[sessions < CUTOFF]
    wide = {name: prices[name].unstack("stock_code").reindex(days) for name in
            ("open", "close", "volume", "base_price", "ret_1d", "calendar_status", "market")}
    codes = wide["close"].columns
    arrays = {name: frame.to_numpy() for name, frame in wide.items()}
    closing, volume = arrays["close"].astype(float), arrays["volume"].astype(float)
    quoted = np.isfinite(closing) & (closing > 0) & (arrays["calendar_status"] == "verified")
    traded = quoted & np.isfinite(volume) & (volume > 0)
    base = wide["base_price"].combine_first(wide["close"].shift(1)).to_numpy(dtype=float)
    limits = np.array([price_limit(day) for day in days])[:, None]
    lower = np.isfinite(base) & (base > 0) & (closing <= base * (1 - limits) + 1e-9)
    last_trade = np.array([np.flatnonzero(traded[:, column])[-1] if traded[:, column].any() else -1 for column in range(len(codes))])
    available = common._sessions(prices)
    results = []
    for row in universe.itertuples():
        entry, target, stock = days.get_loc(row.entry_date), days.get_loc(row.exit_date), codes.get_loc(row.stock_code)
        result = {"gross": None, "gap": None, "valuation_date": None, "return_reason": None,
                  "cash_entry": False, "entry_reason": None, "stale_exit": False,
                  "trading_halt": False, "delisted": False, "limit_down_exit": False,
                  **{f"cost_{m}": 0.0 for m in COST_MULTIPLIERS}}
        if not days[entry:target + 1].difference(available).empty:
            result["return_reason"] = "missing_price_sessions"
            results.append(result)
            continue
        opening = float(arrays["open"][entry, stock])
        entry_base = base[entry, stock]
        if not np.isfinite(opening) or opening <= 0 or not traded[entry, stock]:
            result["entry_reason"] = "untradable_entry"
        elif not np.isfinite(entry_base) or entry_base <= 0:
            result["entry_reason"] = "missing_entry_base"
        elif opening >= entry_base * (1 + price_limit(row.entry_date)) - 1e-9:
            result["entry_reason"] = "limit_up_entry"
        if result["entry_reason"]:
            result.update(gross=0.0, gap=0.0, cash_entry=True)
            results.append(result)
            continue
        for multiplier in COST_MULTIPLIERS:
            result[f"cost_{multiplier}"] = cost_model.round_trip_cost(
                opening, arrays["market"][entry, stock], row.entry_date.date(), float(row.avg_trading_value_20d), multiplier=multiplier)
        actual = target
        result["limit_down_exit"] = bool(lower[target, stock])
        if not traded[target, stock] or lower[target, stock]:
            result["stale_exit"] = True
            result["delisted"] = bool(last_trade[stock] < target)
            result["trading_halt"] = bool(not result["delisted"] and not traded[target, stock])
            if result["delisted"]:
                actual = int(last_trade[stock])
            else:
                marks = np.flatnonzero(quoted[entry:target, stock] & ~lower[entry:target, stock])
                actual = entry + int(marks[-1]) if len(marks) else entry
        result["valuation_date"] = days[actual].date().isoformat()
        # Missing quotes during a suspension carry the mark; observed prices
        # still require adjusted returns, including zero-volume quoted sessions.
        factors = arrays["ret_1d"][entry + 1:actual + 1, stock].astype(float)
        observed = quoted[entry + 1:actual + 1, stock]
        if (not np.isfinite(factors[observed]).all() or (factors[observed] < -1).any()
                or actual < entry):
            result["return_reason"] = "missing_adjusted_return"
        else:
            result["gross"] = float(closing[entry, stock] / opening * np.prod(1 + factors[observed]) - 1)
            result["gap"] = float(opening / entry_base - 1)
        results.append(result)
    return pd.concat([universe.reset_index(drop=True), pd.DataFrame(results)], axis=1)


def newey_west(values, months=None):
    values = np.asarray(values, dtype=float)
    positions = np.arange(len(values)) if months is None else pd.PeriodIndex(months, freq="M").asi8
    valid = np.isfinite(values)
    values, positions = values[valid], positions[valid]
    count = len(values)
    mean = float(values.mean()) if count else None
    result = {"count": count, "mean": mean, "std": float(values.std(ddof=1)) if count > 1 else None,
              "standard_error": None, "t_stat": None, "p_value": 1.0,
              "kernel": "Bartlett", "lag": 1, "small_sample_correction": False}
    if count > 1 and np.ptp(values) > 0:
        residuals = values - mean
        # 2 * Bartlett(1, lag=1) = 1; both gamma denominators are n.
        lag_product = residuals[1:] * residuals[:-1] * (np.diff(positions) == 1)
        variance = float((residuals @ residuals + lag_product.sum()) / count**2)
        if variance > 0 and np.isfinite(variance):
            error = math.sqrt(variance)
            t_stat = mean / error
            result.update(standard_error=error, t_stat=t_stat, p_value=0.5 * math.erfc(t_stat / math.sqrt(2)))
    return result


def holm_adjust(p_values):
    if len(p_values) != len(VARIANTS):
        raise ValueError("Holm family must contain all three R001 variants")
    p = np.array([value if value is not None and np.isfinite(value) else 1.0 for value in p_values], dtype=float)
    if ((p < 0) | (p > 1)).any():
        raise ValueError("p values must be in [0, 1]")
    order = np.argsort(p, kind="stable")
    adjusted = np.empty(3)
    adjusted[order] = np.minimum(1.0, np.maximum.accumulate(p[order] * np.arange(3, 0, -1)))
    return adjusted.tolist()


def _statistics(rows, metric):
    result = newey_west([row[metric] for row in rows], [row["decision_date"] for row in rows])
    result["by_year"] = {year: newey_west([row[metric] for row in rows if row["decision_date"].startswith(year)],
                                         [row["decision_date"] for row in rows if row["decision_date"].startswith(year)])
                         for year in sorted({row["decision_date"][:4] for row in rows})}
    return result


def random_controls(groups):
    """Path-major draws over chronological months from code-sorted E."""
    rng = np.random.default_rng(0)
    paths = np.empty((N_CONTROLS, len(groups)))
    inputs = []
    for group, size, benchmark in groups:
        ordered = group.sort_values("stock_code", kind="stable")
        inputs.append(((ordered.gross - ordered["cost_1.0"]).to_numpy(dtype=float), size, benchmark))
    for path in range(N_CONTROLS):
        for month, (net, size, benchmark) in enumerate(inputs):
            draw = rng.choice(len(net), size=size, replace=False)
            paths[path, month] = float(net[draw].mean()) - benchmark
    return paths


def portfolio_metrics(frame, variant, *, worthless=False, controls=True):
    rows, skipped, control_groups, selections, ic_rows, turnover_rows, liquidity = [], [], [], [], [], [], {}
    previous, previous_month = set(), None
    for day, group in frame.groupby("decision_date", sort=True) if not frame.empty else []:
        group = group.copy()
        if worthless:
            group.loc[group.delisted & ~group.cash_entry & group.gross.notna(), "gross"] = -1.0
        eligible = group.loc[group[f"eligible_{variant}"]]
        chosen = group.loc[group[f"selected_{variant}"]].copy()
        calendar = {name: group[name].iloc[0].date().isoformat() for name in ("decision_date", "entry_date", "exit_date")}
        if len(eligible) < 10:
            skipped.append({**calendar, "reason": "insufficient_eligible", "eligible": len(eligible)})
            continue
        if group.gross.isna().any() or not np.isfinite(group.gross.astype(float)).all():
            skipped.append({**calendar, "reason": "uncomputable_fixed_universe_return",
                            "selected_codes": chosen.stock_code.tolist(), "stock_codes": group.loc[group.gross.isna(), "stock_code"].tolist()})
            continue
        size = len(chosen)
        benchmark = float(group.gross.mean())
        row = {**calendar, "count": size, "universe_count": len(group), "eligible_count": len(eligible),
               "selected_weight": 1 / size, "universe_codes": sorted(group.stock_code), "eligible_codes": sorted(eligible.stock_code),
               "selected_codes": chosen.sort_values([SIGNAL_COLUMNS[variant], "stock_code"]).stock_code.tolist(),
               "gross": float(chosen.gross.mean()), "universe_gross": benchmark}
        for multiplier in COST_MULTIPLIERS:
            row[f"net_{multiplier}"] = float((chosen.gross - chosen[f"cost_{multiplier}"]).mean())
            row[f"excess_{multiplier}"] = row[f"net_{multiplier}"] - benchmark
        gap = chosen.gap.astype(float)
        row.update(close_to_open=float(gap.mean()), open_to_exit=row["gross"],
                   interaction=float((gap * chosen.gross).mean()),
                   close_to_exit=float(((1 + gap) * (1 + chosen.gross) - 1).mean()))
        row["valuations"] = chosen[["stock_code", "gross", "valuation_date", "cash_entry", "entry_reason", "stale_exit", "trading_halt", "delisted", "limit_down_exit"]].to_dict("records")
        rows.append(row)
        if controls:
            control_groups.append((eligible, size, benchmark))
        score_rank, return_rank = signal_eval._rank_vector(-eligible[SIGNAL_COLUMNS[variant]]), signal_eval._rank_vector(eligible.gross)
        ic_rows.append({"decision_date": calendar["decision_date"], "ic": float(score_rank @ return_rank) if score_rank is not None and return_rank is not None else None})
        month = day.to_period("M")
        if previous_month is None or month != previous_month + 1:
            previous = set()
        retained = chosen.stock_code.isin(previous) & ~chosen.cash_entry
        turnover_rows.append({"decision_date": calendar["decision_date"], "count": size, "retained": int(retained.sum()),
                              "retained_fraction": float(retained.mean()), "turnover": float((~retained).mean()),
                              "net": float((chosen.gross - chosen["cost_1.0"].where(~retained, 0)).mean()),
                              "excess": float((chosen.gross - chosen["cost_1.0"].where(~retained, 0)).mean()) - benchmark})
        previous, previous_month = set(chosen.loc[~chosen.cash_entry, "stock_code"]), month
        selections.append(chosen[["stock_code", "avg_trading_value_20d"]].assign(trade_date=day))
        for bucket in range(4):
            selected_bucket = chosen.loc[np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, chosen.avg_trading_value_20d, side="right") == bucket]
            universe_bucket = group.loc[np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, group.avg_trading_value_20d, side="right") == bucket]
            if len(selected_bucket):
                liquidity.setdefault(str(bucket), []).append({"decision_date": calendar["decision_date"], "selected_count": len(selected_bucket),
                                                              "excess": float((selected_bucket.gross - selected_bucket["cost_1.0"]).mean() - universe_bucket.gross.mean())})
    excess = _statistics(rows, "excess_1.0")
    paths = random_controls(control_groups) if controls and rows else np.empty((0, 0))
    means = paths.mean(axis=1) if paths.size else np.array([])
    selected = pd.concat(selections, ignore_index=True) if selections else pd.DataFrame()
    net = np.array([row["net_1.0"] for row in rows])
    wealth = np.r_[1.0, np.cumprod(1 + net)]
    ban = lambda row: "inside" if "2023-11-06" <= row["decision_date"] <= "2025-03-30" else "outside"
    dated_rows = pd.Series(rows, index=[row["decision_date"] for row in rows], dtype=object)
    return {"excess": excess, "per_rebalance": rows, "skipped_months": skipped,
            "short_sale_regimes": {label: _statistics(group.tolist(), "excess_1.0")
                                    for label, group in market_regimes.split_by_regime(dated_rows).items()},
            "cost_sensitivity": {str(m): {"top_net": _statistics(rows, f"net_{m}")["mean"],
                                         "top_net_excess": _statistics(rows, f"excess_{m}")["mean"],
                                         "excess": _statistics(rows, f"excess_{m}")} for m in COST_MULTIPLIERS},
            "random_control": {**signal_eval._control_summary(means, excess["mean"]), "seed": 0,
                               "months": [row["decision_date"] for row in rows], "paths": paths.tolist(), "mean_excess": means.tolist()},
            "ic": {**_statistics(ic_rows, "ic"), "daily": ic_rows},
            "decomposition": {name: _statistics(rows, name) for name in ("close_to_open", "open_to_exit", "interaction", "close_to_exit")},
            "turnover_based": {"used_for_judgement": False, "per_rebalance": turnover_rows, "excess": _statistics(turnover_rows, "excess")},
            "liquidity_buckets": {str(bucket): {"lower_inclusive": 0.0 if bucket == 0 else cost_model.TRADING_VALUE_BUCKETS[bucket - 1],
                                               "upper_exclusive": cost_model.TRADING_VALUE_BUCKETS[bucket] if bucket < 3 else None,
                                               "excess": _statistics(liquidity.get(str(bucket), []), "excess"),
                                               "per_rebalance": liquidity.get(str(bucket), [])} for bucket in range(4)},
            "short_sale_ban": {label: _statistics([row for row in rows if ban(row) == label], "excess_1.0") for label in ("inside", "outside")},
            "capacity": capacity.strategy_capacity(selected) if len(selected) else None,
            "capacity_by_rebalance": {day.date().isoformat(): len(group) * float(group.avg_trading_value_20d.min()) * 0.01 for day, group in selected.groupby("trade_date")} if len(selected) else {},
            "bundle_drawdown": float(np.max(1 - wealth / np.maximum.accumulate(wealth))) if len(rows) and (net > -1).all() else None}


def judge_variants(results, fields, trial_count):
    criteria = fields["pass_criteria"]["design_validation"]
    adjusted = holm_adjust([results[variant]["excess"]["p_value"] for variant in VARIANTS])
    threshold = float(criteria["core_t_stat_gt_when_trials_gt_20"] if trial_count > 20 else criteria["core_t_stat_gt"])
    for variant, holm_p in zip(VARIANTS, adjusted):
        result = results[variant]
        rows = result["per_rebalance"]
        design = _statistics([row for row in rows if row["decision_date"] < "2025-01-01"], "excess_1.0")["mean"]
        validation = _statistics([row for row in rows if row["decision_date"] >= "2025-01-01"], "excess_1.0")["mean"]
        inputs = {"observations": result["excess"]["count"], "mean_excess": result["excess"]["mean"],
                  "mean_excess_at_cost_1_5": result["cost_sensitivity"]["1.5"]["top_net_excess"],
                  "t_stat": result["excess"]["t_stat"], "p_value": result["excess"]["p_value"], "holm_p_value": holm_p,
                  "random_control_share": result["random_control"]["share_of_controls"], "required_t_stat": threshold,
                  "trial_count_after_run": trial_count, "design_mean_excess": design, "validation_mean_excess": validation,
                  "validation_year_same_sign": bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None}
        missing = [f"{name} is undefined" for name in ("mean_excess", "mean_excess_at_cost_1_5", "t_stat", "random_control_share")
                   if inputs[name] is None or not np.isfinite(inputs[name])]
        if inputs["observations"] < criteria["minimum_observations"]["rebalances"]:
            missing.append("fewer than 36 valid monthly observations")
        failures = []
        if not missing:
            for failed, reason in (
                (inputs["mean_excess"] <= criteria["net_performance_gt"], "mean net excess must be positive"),
                (inputs["mean_excess_at_cost_1_5"] <= criteria["net_performance_at_cost_multiplier_1_5_gt"], "cost x1.5 mean net excess must be positive"),
                (inputs["t_stat"] <= threshold, f"Newey-West t must exceed {threshold:g}"),
                (holm_p > 0.05, "Holm(3) one-sided p must be <= 0.05"),
                (inputs["random_control_share"] > criteria["random_control_top_percent"] / 100, "random-control share must be <= 0.05"),
            ):
                if failed:
                    failures.append(reason)
        result.update(judgement_inputs=inputs, verdict="insufficient" if missing else "fail" if failures else "pass",
                      reasons=missing or failures or ["all preregistered criteria satisfied"])


def apply_delisting_sensitivity(results, sensitivity):
    for variant in VARIANTS:
        result, rerun = results[variant], sensitivity[variant]
        sensitive = result["verdict"] != rerun["verdict"]
        result["delisting_sensitivity"] = {"sensitive": sensitive, "base_verdict": result["verdict"], **rerun}
        if sensitive:
            result["verdict"] = "delisting_sensitive"
            result["reasons"] = ["last-trading-close and -100% delisting re-runs have different verdicts"]


def cost_model_v2_metrics(observations, prices):
    # Positive registered costs identify filled slots; cash and missing windows
    # retain zero costs. An empty universe has no cost columns to reprice.
    adjusted = common.reprice_costs_v2(observations, prices, "entry_date",
                                     observations["cost_1.0"].gt(0)) if not observations.empty else observations.copy()
    variants = {}
    for name in VARIANTS:
        metrics = portfolio_metrics(adjusted, name, controls=False)
        variants[name] = {key: metrics[key] for key in ("excess", "cost_sensitivity", "per_rebalance")}
    return {"used_for_judgement": False, "interpretation": common.COST_MODEL_V2_INTERPRETATION,
            "variants": variants}


def write_results(payload, *, repo_root=PROJECT_ROOT):
    """Finite JSON and an R001-specific summary with all metric interpretations."""
    _registration(repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    lines = [f"# Experiment results: {EXPERIMENT_ID}", "", f"Primary variant: r21; verdict: {payload['verdict']}",
             "Reasons: " + "; ".join(payload["reasons"]), "", "Observation unit: monthly independent investment bundle. No variant selection.", "",
             "| Variant | Months | Net excess | NW t | Holm p | Cost x1.5 excess | Control share | Verdict | -100% verdict |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for variant, result in payload["variants"].items():
        inputs = result["judgement_inputs"]
        lines.append(f"| {variant} | {inputs['observations']} | {inputs['mean_excess']} | {inputs['t_stat']} | {inputs['holm_p_value']} | "
                     f"{inputs['mean_excess_at_cost_1_5']} | {inputs['random_control_share']} | {result['verdict']} | {result['delisting_sensitivity']['verdict']} |")
    lines += ["", "## Interpretations", "", INTERPRETATIONS["note"], "", "| Criterion | Metric | Definition |", "| --- | --- | --- |"]
    lines += [f"| {name} | {item['metric']} | {item['definition']} |" for name, item in INTERPRETATIONS["criteria"].items()]
    lines += ["", "All metric groups (including secondary diagnostics):", ""]
    lines += [f"- {name}: {definition}" for name, definition in INTERPRETATIONS["metrics"].items()]
    lines += ["", "Source hashes, fixed sets, valuations, full random paths, skipped months and secondary metrics are in the accompanying JSON.", "", "## Assumptions", ""]
    lines += [f"- {assumption}" for assumption in payload["assumptions"]]
    directory = Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, summary_path = directory / f"{stamp}.json", directory / f"{stamp}.md"
    with json_path.open("x", encoding="utf-8") as json_file:
        with summary_path.open("x", encoding="utf-8") as summary_file:
            json_file.write(encoded)
            summary_file.write("\n".join(lines) + "\n")
    return json_path, summary_path


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    started = time.perf_counter()
    fields = _registration(repo_root)
    data_dir = Path(data_dir)
    if prices is not None:
        common.guard_prices(prices, repo_root=repo_root)
    sources = {"krx_daily": {}, "quarterly_archives": _hash_files(_quarterly_files(data_dir)),
               "company_profiles": _hash_files([data_dir / "reference/dart_company/companies.jsonl"]), "dart_listing_files": {}}
    if prices is None:
        files = d001._price_files(data_dir / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
        sources["krx_daily"] = _hash_files([path for _, path in files])
        prices = d001._load_prices(data_dir / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
    else:
        sources["supplied_price_frame"] = hashlib.sha256(pd.util.hash_pandas_object(prices.sort_index(), index=True).values.tobytes()).hexdigest()
    common.guard_prices(prices, repo_root=repo_root)
    if prices.empty or common._sessions(prices).max() >= CUTOFF:
        raise ValueError("R001 requires nonempty prices entirely before 2026-01-01")
    if prices.index.has_duplicates:
        raise ValueError("price keys must be unique")
    required = {"open", "close", "volume", "base_price", "ret_1d", "market", "stock_name", "trading_value", "calendar_status"}
    if not required.issubset(prices.columns):
        raise ValueError(f"missing R001 price columns: {sorted(required - set(prices.columns))}")
    prices = prices.sort_index()
    sessions = signal_eval._dates(hc001._session_calendar() if sessions is None else sessions).sort_values().unique()
    profiles = industry_map.load_industry_map(data_dir / "reference/dart_company/companies.jsonl")
    # Limit files visible to the unchanged loader, rather than opening holdout
    # fiscal archives and relying on a subsequent receipt filter.
    with TemporaryDirectory(prefix="r001-quarterly-") as temporary:
        directory = Path(temporary) / "fundamentals/dart_quarterly"
        directory.mkdir(parents=True)
        for path in sources["quarterly_archives"]:
            (directory / Path(path).name).symlink_to(path)
        universe, counts, excluded = build_universe(prices, data_dir=Path(temporary), profiles=profiles,
                                                    sessions=sessions, repo_root=repo_root)
    observations = forward_observations(prices, universe, sessions=sessions, repo_root=repo_root)
    variants = {variant: portfolio_metrics(observations, variant) for variant in VARIANTS}
    sensitivity = {variant: portfolio_metrics(observations, variant, worthless=True) for variant in VARIANTS}
    trial_count = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS)
    judge_variants(variants, fields, trial_count)
    judge_variants(sensitivity, fields, trial_count)
    apply_delisting_sensitivity(variants, sensitivity)
    for variant, raw in zip(VARIANTS, ("raw21", "raw42", "raw21_profitable")):
        comparison = portfolio_metrics(observations, raw, controls=False)
        raw_rows = {row["decision_date"]: row for row in comparison["per_rebalance"]}
        differences = [{"decision_date": row["decision_date"], "difference": row["excess_1.0"] - raw_rows[row["decision_date"]]["excess_1.0"]}
                       for row in variants[variant]["per_rebalance"] if row["decision_date"] in raw_rows]
        variants[variant]["unadjusted_comparison"] = {"used_for_judgement": False, "portfolio": comparison,
                                                     "difference": _statistics(differences, "difference"), "per_rebalance": differences}
    primary = variants["r21"]
    days = common._sessions(prices)
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": "r21", "verdict": primary["verdict"], "reasons": primary["reasons"],
               "variants": variants, "common_skipped_months": excluded, "counts": {"by_month": counts,
                   **{flag: int(observations[flag].sum()) if not observations.empty else 0 for flag in ("cash_entry", "stale_exit", "trading_halt", "delisted", "limit_down_exit")},
                   "return_reasons": observations.return_reason.dropna().value_counts().to_dict() if not observations.empty else {}},
               "cost_model_v2": cost_model_v2_metrics(observations, prices),
               "coverage": {"planned_months": len(MONTHS), "first_session": days.min().date().isoformat(), "last_session": days.max().date().isoformat(),
                            "sessions": len(days), "stocks": int(prices.index.get_level_values("stock_code").nunique()), "price_rows": len(prices),
                            "quarterly_directory_exists": (data_dir / "fundamentals/dart_quarterly").is_dir(), "quarterly_archives": len(sources["quarterly_archives"]),
                            "industry_profiles": len(profiles), "calendar_version": f"exchange-calendars:{hc001.calendars.__version__}:XKRX"},
               "source_hashes": sources, "interpretations": INTERPRETATIONS, "assumptions": ASSUMPTIONS,
               "performance": {"runtime_seconds": time.perf_counter() - started, "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    for group in ("krx_daily", "quarterly_archives", "company_profiles", "dart_listing_files"):
        if _hash_files([Path(path) for path in sources[group]]) != sources[group]:
            raise ValueError(f"R001 source files changed during evaluation: {group}")
    # Judgement inputs must be finite; other non-finite report values (for example a
    # statistic over an empty stratum) are stored as null and listed, never dropped.
    for variant in VARIANTS:
        json.dumps(variants[variant]["judgement_inputs"], allow_nan=False)
    nonfinite = []

    def _finite(value, path):
        if isinstance(value, dict):
            return {key: _finite(item, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_finite(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, float) and not math.isfinite(value):
            nonfinite.append(path)
            return None
        return value

    payload = _finite(payload, "$")
    payload["nonfinite_report_fields"] = nonfinite
    json.dumps(payload, allow_nan=False)
    for variant in VARIANTS:
        result = variants[variant]
        experiment_registry.record_trial(EXPERIMENT_ID, variant,
            {**result["judgement_inputs"], "delisting_sensitivity": result["delisting_sensitivity"]["judgement_inputs"],
             "delisting_sensitive": result["delisting_sensitivity"]["sensitive"]}, result["verdict"], repo_root=repo_root,
            note="fixed variant; monthly excess; full costs; NW(1), Holm(3); delisting sensitivity")
    write_results(payload, repo_root=repo_root)
    return payload


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    sessions = signal_eval._dates(hc001._session_calendar()).sort_values().unique()
    planned, excluded = rebalance_calendar(sessions)
    data_dir = Path(data_dir)
    files = d001._price_files(data_dir / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
    days = pd.DatetimeIndex([day for day, _ in files])
    coverage = {}
    for variant in VARIANTS:
        lookback = 42 if variant == "r42" else 21
        covered, incomplete = [], []
        for row in planned:
            position = sessions.get_loc(pd.Timestamp(row["decision_date"]))
            required = sessions[max(0, position - lookback - 18):position + HORIZON + 1]
            (covered if required.difference(days).empty else incomplete).append(row["decision_date"])
        coverage[variant] = {"covered_months": covered, "incomplete_price_months": incomplete}
    financials = _quarterly_files(data_dir)
    company = data_dir / "reference/dart_company/companies.jsonl"
    profiles = industry_map.load_industry_map(company)
    return {"experiment_id": EXPERIMENT_ID, "data_dir": str(data_dir), "variants": list(VARIANTS), "primary_variant": "r21",
            "planned_months": len(MONTHS), "guarded_months": len(planned), "excluded_months": excluded,
            "months": planned, "coverage": coverage, "price_files": len(files), "read_start": PRICE_START, "read_end": PRICE_END,
            "first_price_file": days.min().date().isoformat() if len(days) else None, "last_price_file": days.max().date().isoformat() if len(days) else None,
            "files_by_year": {str(year): int((days.year == year).sum()) for year in range(2015, 2026)},
            "quarterly_directory_exists": (data_dir / "fundamentals/dart_quarterly").is_dir(),
            "quarterly_archives": [{"file": path.name, "bytes": path.stat().st_size} for path in financials],
            "industry_profiles": len(profiles), "classified_profiles": sum(label != industry_map.UNCLASSIFIED for label in profiles.values()),
            "trial_count_after_run": experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3,
            "assumptions": ASSUMPTIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="local plan and file coverage (default)")
    mode.add_argument("--execute", action="store_true", help="evaluate and record the three fixed variants")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; months={result['variants']['r21']['excess']['count']}")
    else:
        print(f"{EXPERIMENT_ID} dry-run (no returns evaluated; no registry rows or results written)")
        print(json.dumps(dry_run(data_dir=args.data_dir), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
