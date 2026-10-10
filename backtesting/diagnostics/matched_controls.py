"""Characteristic matching and monthly cross-sectional return decomposition.

Inputs are long tables (or MultiIndexes) keyed by decision_date, stock_code.
Selection contains the selected keys. Features contain market, market_cap,
avg_trading_value_20d, volatility_60d and industry (industry_map groups).
They are snapshots known at the decision close; optional feature_date must not
follow that close. Returns contain gross, computed by HC002.forward_observations,
with NaN for its execution exclusions. This module never infers returns from
raw closes, reads files, judges a result or registers a trial.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


CELLS = ("market", "size3", "adv3", "vol3")
LABEL = "사후 진단, 판정에 쓰지 않음"
_KEYS = ["decision_date", "stock_code"]
_FEATURES = {"size": "market_cap", "adv": "avg_trading_value_20d", "vol": "volatility_60d"}


def _keyed(frame, columns, name):
    if isinstance(frame.index, pd.MultiIndex):
        frame = frame.reset_index()
    required = set(_KEYS + list(columns))
    if not required.issubset(frame.columns):
        raise ValueError(f"{name} missing columns: {sorted(required - set(frame.columns))}")
    frame = frame.copy()
    days = pd.DatetimeIndex(pd.to_datetime(frame.decision_date))
    if days.hasnans or days.tz is not None or not days.equals(days.normalize()):
        raise ValueError(f"{name} decision_date must be date-only and timezone-naive")
    frame["decision_date"] = days
    if (frame.stock_code.isna().any() or not all(isinstance(x, str) and bool(x) for x in frame.stock_code)
            or frame.duplicated(_KEYS).any()):
        raise ValueError(f"{name} keys must be unique and nonmissing")
    return frame.set_index(_KEYS).sort_index()


def assign_cells(universe_features):
    """Midrank percentiles and terciles within each decision-date/market.

    Equal characteristics stay together, so ties can make terciles uneven.
    Percentiles are (average rank - 0.5) / market size, on the 0..1 scale.
    Rank the entire close-known universe BEFORE any future-return exclusion.
    Missing characteristics fail explicitly, including incomplete 60-session vol.
    """
    frame = _keyed(universe_features, ["market", "industry", *_FEATURES.values()], "universe_features")
    if (not frame.market.isin(("KOSPI", "KOSDAQ")).all() or frame.industry.isna().any()
            or not all(isinstance(x, str) and bool(x) for x in frame.industry)):
        raise ValueError("features require KOSPI/KOSDAQ market and industry_map groups")
    frame["industry"] = frame.industry.astype(object)
    if "feature_date" in frame:
        known = pd.DatetimeIndex(pd.to_datetime(frame.feature_date))
        if (known.hasnans or known.tz is not None or not known.equals(known.normalize())
                or (known > frame.index.get_level_values("decision_date")).any()):
            raise ValueError("feature_date must be known at the decision close")
    for label, column in _FEATURES.items():
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
        invalid = ~np.isfinite(frame[column]) | (frame[column] < 0)
        if label != "vol":
            invalid |= frame[column].eq(0)
        if invalid.any():
            keys = [(day.date().isoformat(), code) for day, code in frame.index[invalid][:5]]
            raise ValueError(f"missing or invalid {column} at decision close: {keys}")
        groups = frame.groupby([pd.Grouper(level="decision_date"), "market"], observed=True)
        size = groups[column].transform("size")
        percentile = (groups[column].rank(method="average") - 0.5) / size
        frame[f"{label}_percentile"] = percentile
        frame[f"{label}3"] = np.ceil(3 * percentile).clip(1, 3).astype(int)
    return frame


def _newey_west(rows, field):
    valid = [row for row in rows if row[field] is not None]
    values = np.array([row[field] for row in valid], dtype=float)
    n = len(values)
    mean = float(values.mean()) if n else None
    result = {"count": n, "mean": mean, "std": float(values.std(ddof=1)) if n > 1 else None,
              "standard_error": None, "t_stat": None, "kernel": "Bartlett", "lag": 1,
              "small_sample_correction": False}
    if n > 1:
        residual = values - mean
        months = pd.PeriodIndex([row["decision_date"] for row in valid], freq="M").asi8
        # 2 * Bartlett(1, lag=1) = 1. Gaps are not adjacent calendar months.
        cross = residual[1:] * residual[:-1] * (np.diff(months) == 1)
        variance = float((residual @ residual + cross.sum()) / n**2)
        result["standard_error"] = float(np.sqrt(variance))
        if variance > 0 and np.ptp(values) > 0:
            result["t_stat"] = mean / result["standard_error"]
    return result


def _balance(group, weights):
    weights = np.asarray(weights, dtype=float)
    total = weights.sum()
    return {**{f"mean_{label}_percentile": float(group[f"{label}_percentile"].to_numpy() @ weights / total)
               for label in _FEATURES},
            "industry_shares": {industry: float(weights[group.industry.eq(industry)].sum() / total)
                                for industry in sorted(group.industry.unique())}}


def _mean_balance(rows):
    if not rows:
        return {}
    industries = sorted({name for row in rows for cohort in row["balance"].values()
                         for name in cohort["industry_shares"]})
    return {cohort: {
        **{f"mean_{label}_percentile": float(np.mean([row["balance"][cohort][f"mean_{label}_percentile"] for row in rows]))
           for label in _FEATURES},
        "industry_shares": {industry: float(np.mean([row["balance"][cohort]["industry_shares"].get(industry, 0.0)
                                                     for row in rows])) for industry in industries},
    } for cohort in ("selection", "universe", "matched_controls")}


def _pools(group, cells, industry):
    controls = group.loc[~group.selected]
    cache, requests, audit = {}, {}, []
    relaxations = {"industry": 0, "vol": 0}

    def candidates(columns, row):
        columns = tuple(columns)
        if columns not in cache:
            pools = {(): list(controls.index)} if not columns else {}
            if columns:
                for position, key in zip(controls.index, controls[list(columns)].itertuples(index=False, name=None)):
                    pools.setdefault(key, []).append(position)
            cache[columns] = {key: np.array(pool, dtype=int) for key, pool in pools.items()}
        key = tuple(row[column] for column in columns)
        return cache[columns].get(key, np.array([], dtype=int)), (columns, key)

    for _, row in group.loc[group.selected].iterrows():
        columns = [*cells, *(["industry"] if industry else [])]
        pool, identity = candidates(columns, row)
        relaxed = []
        if len(pool) < 5 and industry:
            columns.remove("industry")
            pool, identity = candidates(columns, row)
            relaxed.append("industry")
        if len(pool) < 5 and "vol3" in columns:
            columns.remove("vol3")
            pool, identity = candidates(columns, row)
            relaxed.append("vol")
        for name in relaxed:
            relaxations[name] += 1
        if not len(pool):
            raise ValueError(f"no non-selected matched controls after industry/vol relaxation: "
                             f"{row.decision_date.date().isoformat()} {row.stock_code}")
        if identity not in requests:
            requests[identity] = {"pool": pool, "slots": 0}
        requests[identity]["slots"] += 1
        audit.append({"stock_code": row.stock_code, "conditions": row[columns].to_dict(),
                      "candidate_count": len(pool), "relaxed": relaxed})
    # These pools are nested or disjoint: industry cell -> vol cell -> base cell.
    # Tightest pools first maximize distinct names before unavoidable reuse.
    return sorted(requests.values(), key=lambda item: len(item["pool"])), relaxations, audit


def _draw_month(group, requests, draws, rng):
    gross = group.gross.to_numpy(dtype=float)
    count = int(group.selected.sum())
    means, frequencies, replacements = np.zeros(draws), np.zeros(len(group), dtype=int), 0
    for draw in range(draws):
        used = np.zeros(len(group), dtype=bool)
        total = 0.0
        for request in requests:
            pool, slots = request["pool"], request["slots"]
            available = pool[~used[pool]]
            unique = rng.choice(available, size=min(slots, len(available)), replace=False)
            extra = slots - len(unique)
            chosen = np.concatenate([unique, rng.choice(pool, size=extra, replace=True)])
            used[chosen] = True
            frequencies += np.bincount(chosen, minlength=len(group))
            replacements += extra
            total += gross[chosen].sum()
        means[draw] = total / count
    return means, frequencies, replacements


def _regression(group, day):
    row = {"decision_date": day, "observations": len(group), "selected_count": int(group.selected.sum()),
           "phase2_coefficient": None, "reason": None}
    if group.empty:
        return {**row, "reason": "no_executable_returns"}
    industries = pd.get_dummies(group.industry, drop_first=True, dtype=float)
    nuisance = np.column_stack([np.ones(len(group)), group[[f"{label}_percentile" for label in _FEATURES]], industries])
    targets = np.column_stack([group.gross.astype(float), group.selected.astype(float)])
    coefficients, _, rank, _ = np.linalg.lstsq(nuisance, targets, rcond=None)
    residual = targets - nuisance @ coefficients
    dummy = residual[:, 1]
    denominator = float(dummy @ dummy)
    row.update(nuisance_rank=int(rank), industry_reference=sorted(group.industry.unique())[0])
    # FWL identifies the dummy even when nuisance columns themselves are collinear.
    if denominator <= np.finfo(float).eps * len(group):
        row["reason"] = "phase2_dummy_not_identified"
    else:
        row["phase2_coefficient"] = float(dummy @ residual[:, 0] / denominator)
    return row


def matched_controls(selection, universe_features, returns, *, cells=CELLS, industry=True, draws=200, seed=0):
    """Return gross matching, balance and regression diagnostics, never a verdict.

    Relaxation counts are selected stock-months, not multiplied by draws. Reuse
    is counted in draw slots, only after exhausting unused eligible names. An
    empty pool after the two permitted relaxations is an explicit error.

    The draw comparison uses the same executable-universe benchmark for actual
    selection and controls: mean(control - universe) >= mean(selection - universe).
    Equivalently, mean(selection - control) <= 0. It is an empirical diagnostic
    share, not a calibrated p-value. Balance averages months equally. NW uses
    adjacent calendar months only, divisor n, Bartlett lag 1, no correction.
    """
    cells = tuple(cells)
    if not cells or len(set(cells)) != len(cells) or not set(cells).issubset(CELLS):
        raise ValueError("cells must be distinct members of market, size3, adv3, vol3")
    if type(industry) is not bool or type(draws) is not int or draws < 1:
        raise ValueError("industry must be bool and draws must be a positive integer")
    chosen = _keyed(selection, [], "selection")
    features = assign_cells(universe_features)
    outcomes = _keyed(returns, ["gross"], "returns")
    if not chosen.index.difference(features.index).empty:
        raise ValueError("selection must belong to the decision-date universe")
    if not features.index.difference(outcomes.index).empty:
        raise ValueError("returns must include every universe key, with NaN for HC002 exclusions")
    dates = features.index.get_level_values("decision_date").unique()
    if pd.PeriodIndex(dates, freq="M").has_duplicates:
        raise ValueError("only one decision close per month is allowed")
    frame = features.join(outcomes[["gross"]])
    frame["gross"] = pd.to_numeric(frame.gross, errors="raise").astype(float)
    if np.isinf(frame.gross).any():
        raise ValueError("gross returns must be finite or NaN for execution exclusions")
    frame["selected"] = frame.index.isin(chosen.index)
    rng = np.random.default_rng(seed)
    monthly, regressions, skipped = [], [], []
    relaxations = {"industry": 0, "vol": 0}
    control_excess, actual_excess, matched_differences = [], [], []
    replacement_slots = 0
    for day, original in frame.groupby(level="decision_date", sort=True):
        group = original.loc[np.isfinite(original.gross)].reset_index()
        date = day.date().isoformat()
        regressions.append(_regression(group, date))
        if not group.selected.any():
            skipped.append({"decision_date": date, "reason": "no_executable_selection"})
            continue
        requests, relaxed, audit = _pools(group, cells, industry)
        control_gross, frequencies, reused = _draw_month(group, requests, draws, rng)
        selected_gross = float(group.loc[group.selected, "gross"].mean())
        universe_gross = float(group.gross.mean())
        mean_control = float(control_gross.mean())
        monthly.append({"decision_date": date, "selection_count": int(group.selected.sum()),
                        "universe_count": len(group), "selection_gross": selected_gross,
                        "universe_gross": universe_gross, "matched_control_gross": mean_control,
                        "difference": selected_gross - mean_control,
                        "control_gross_draws": control_gross.tolist(), "relaxations": relaxed,
                        "replacement_slots": reused, "matching": audit,
                        "control_counts": dict(zip(group.loc[frequencies > 0, "stock_code"], frequencies[frequencies > 0].tolist())),
                        "balance": {"selection": _balance(group, group.selected),
                                    "universe": _balance(group, np.ones(len(group))),
                                    "matched_controls": _balance(group, frequencies)}})
        control_excess.append(control_gross - universe_gross)
        actual_excess.append(selected_gross - universe_gross)
        matched_differences.append(selected_gross - control_gross)
        for name in relaxations:
            relaxations[name] += relaxed[name]
        replacement_slots += reused
    actual = float(np.mean(actual_excess)) if monthly else None
    control_means = np.mean(control_excess, axis=0) if monthly else np.array([])
    excluded = frame.gross.isna()
    return {"diagnostic": "characteristic_matched_controls", "label": LABEL, "used_for_judgement": False,
            "settings": {"cells": list(cells), "industry": industry, "draws": draws, "seed": seed,
                         "percentiles": "within decision-date/market midranks, 0..1; ties stay together",
                         "balance_weighting": "equal months; equal selection/control slots within month; mean over draws",
                         "relaxation_count_unit": "selected stock-months",
                         "draw_comparison": "mean(control - universe) >= mean(selection - universe)",
                         "return_path": "HC002: next open to entry-inclusive 20th session close; same execution exclusions"},
            "monthly": monthly, "difference": _newey_west(monthly, "difference"),
            "random_control": {"count": len(control_means), "actual_mean_difference": actual,
                               "mean_differences": control_means.tolist(),
                               "selection_minus_control_by_draw": np.mean(matched_differences, axis=0).tolist() if monthly else [],
                               "share_of_controls": float(np.mean(control_means >= actual)) if monthly else None},
            "relaxations": relaxations, "replacement_slots": replacement_slots, "balance": _mean_balance(monthly),
            "regression": {"model": "gross ~ intercept + phase2_dummy + size/ADV/vol percentiles + industry dummies",
                           "monthly": regressions, "phase2_coefficient": _newey_west(regressions, "phase2_coefficient")},
            "exclusions": {"universe_returns": int(excluded.sum()),
                           "selection_returns": int((excluded & frame.selected).sum()), "skipped_months": skipped}}
