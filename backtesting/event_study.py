"""Point-in-time event returns with entry-date clustered inference."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path
import re

import numpy as np
import pandas as pd

from backtesting import cost_model, holdout
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.signal_eval import (
    _RETURN_FLAGS, _adv, _control_summary, _dates, _execution_policies, _guard,
    _holding_data, _holding_window, _horizons, _last_price_dates, _positive_int, _price_frame, _summary,
)


def _availability(value):
    if type(value) is date or isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", value):
        day = pd.Timestamp(value)
        return day, False, day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    if not isinstance(value, (datetime, pd.Timestamp, str)) or pd.isna(value):
        raise ValueError("available_at must be a date or a timezone-aware datetime")
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("available_at datetime must be timezone-aware; use datetime.date for date-only")
    stamp = stamp.tz_convert("Asia/Seoul")
    day = stamp.tz_localize(None).normalize()
    return day, stamp < day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=9), stamp


def _aggregate(rows):
    flags = {name: sum(row[name] for row in rows) for name in _RETURN_FLAGS}
    if not rows:
        return {"count": 0, "mean_abnormal": None, "mean_net": None, "date_count": 0,
                "t_stat": None, "net_t_stat": None, **flags}
    frame = pd.DataFrame(rows)
    clustered = frame.groupby("entry_date")[["abnormal", "net"]].mean()
    return {"count": len(frame), "mean_abnormal": float(frame.abnormal.mean()), "mean_net": float(frame.net.mean()),
            "date_count": len(clustered), "t_stat": _summary(clustered.abnormal)["t_stat"],
            "net_t_stat": _summary(clustered.net)["t_stat"], **flags}


def _feature_groups(events, feature, n_buckets):
    if feature is None:
        return None
    if feature not in events or events[feature].isna().any():
        raise ValueError("feature must exist and have no missing values")
    values = events[feature]
    if pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values):
        if not np.isfinite(values).all():
            raise ValueError("numeric feature must be finite")
        # qcut keeps equal values together; tied boundaries can reduce bin count.
        if values.nunique() <= 1:
            return pd.Series(1, index=events.index, dtype=int)
        return pd.qcut(values, n_buckets, labels=False, duplicates="drop") + 1
    if not values.map(lambda value: isinstance(value, (str, int, float, bool))).all():
        raise ValueError("categorical feature values must be JSON scalar values")
    return values


def run_event_study(
    events: pd.DataFrame, prices: pd.DataFrame, benchmarks: pd.DataFrame, *, horizons=(1, 3, 5, 20),
    feature=None, n_buckets=5, n_controls=200, seed=0, cost_multiplier=1.0, experiment_id=None,
    missing_exit="last_close", limit_fill="exclude",
    repo_root: str | Path = PROJECT_ROOT, ledger_path: str | Path = holdout.LEDGER_PATH,
) -> dict:
    """Evaluate date-only events next session, timed pre-09:00 KST events same session.

    h counts entry as session 1. ADV ends at the session before entry. Abnormal
    returns subtract a close-to-close market proxy, so the benchmark includes the
    overnight move preceding the stock's open-to-close holding period. Controls
    sample with replacement from entry-tradable names, without exit survivorship
    filtering; missing control exits/benchmarks are counted. Clustered t-statistics
    first average events within entry date; reported means remain event-weighted.
    Numeric feature quantiles can have fewer bins when boundaries tie.
    ret_1d chains adjust ex-date returns. Raw fallbacks and stale valuations are
    counted; stale benchmarks end on the valuation date. No later row dates mean
    a missing exit is excluded as no_later_price, rather than assigning a loss.
    """
    horizons = _horizons(horizons)
    _execution_policies(missing_exit, limit_fill)
    _positive_int(n_buckets, "n_buckets")
    _positive_int(n_controls, "n_controls")
    if not np.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("cost_multiplier must be finite and nonnegative")
    if not {"event_id", "stock_code", "available_at"}.issubset(events.columns):
        raise ValueError("events require event_id, stock_code, available_at")
    events = events.reset_index(drop=True).copy()
    if events[["event_id", "stock_code", "available_at"]].isna().any().any() or events.event_id.duplicated().any():
        raise ValueError("event IDs must be unique and required event fields nonmissing")
    groups = _feature_groups(events, feature, n_buckets)
    frame = _price_frame(prices)
    last_dates = _last_price_dates(frame)
    sessions = frame.index.get_level_values("trade_date").unique()
    plans = []
    for row in events.itertuples():
        day, same_day, available = _availability(row.available_at)
        entry = int(sessions.searchsorted(day, side="left" if same_day else "right"))
        if entry < len(sessions):
            opening_time = sessions[entry].tz_localize("Asia/Seoul") + pd.Timedelta(hours=9)
            if opening_time <= available:
                raise ValueError("event entry must be strictly after availability")
        plans.append(entry)
    if plans and len(sessions):
        end = min(max(plans) + max(horizons) - 1, len(sessions) - 1)
        frame = frame.loc[frame.index.get_level_values("trade_date") <= sessions[end]]
    elif not plans:
        frame = frame.iloc[:0]
    if not {"trade_date", "market", "close"}.issubset(benchmarks.columns):
        raise ValueError("benchmarks require trade_date, market, close")
    benchmark = benchmarks[["trade_date", "market", "close"]].copy()
    benchmark["trade_date"] = _dates(benchmark.trade_date)
    if benchmark.duplicated(["trade_date", "market"]).any():
        raise ValueError("benchmark market-date keys must be unique")
    # All later price, ADV and benchmark reads lie within this one guarded span.
    _guard(frame, experiment_id=experiment_id, repo_root=repo_root, ledger_path=ledger_path)
    sessions = frame.index.get_level_values("trade_date").unique()
    if len(sessions):
        benchmark = benchmark.loc[benchmark.trade_date.between(sessions[0], sessions[-1])]
    else:
        benchmark = benchmark.iloc[:0]
    benchmark = benchmark.set_index(["trade_date", "market"])["close"]
    adv = _adv(frame)
    holding = _holding_data(frame, limit_fill, last_dates)
    stock_columns = dict(zip(holding["codes"], range(len(holding["codes"]))))
    rng = np.random.default_rng(seed)
    output = {"price_basis": holding["price_basis"], "missing_exit": missing_exit,
              "limit_fill": limit_fill, "horizons": {}}
    entry_pools = {}
    for entry in set(plans):
        if entry < len(sessions):
            eligible = holding["entry_tradable"][entry] & ~holding["limit_up"][entry]
            entry_pools[entry] = holding["codes"][eligible].tolist()

    def outcome(entry, stock, h):
        if entry >= len(sessions):
            return None, "missing_entry_session"
        exit_index = entry + h - 1
        if exit_index >= len(sessions):
            return None, "missing_exit_session"
        if stock not in stock_columns:
            return None, "missing_entry_price"
        column = stock_columns[stock]
        reason = window["reason"][entry, column]
        if reason is not None:
            return None, reason
        entry_date = sessions[entry]
        exit_date = sessions[window["exit_position"][entry, column]]
        if entry == 0:
            return None, "missing_previous_session"
        previous = sessions[entry - 1]
        market = holding["market"][entry, column]
        start_benchmark = benchmark.get((previous, market), np.nan)
        end_benchmark = benchmark.get((exit_date, market), np.nan)
        if not np.isfinite([start_benchmark, end_benchmark]).all() or min(start_benchmark, end_benchmark) <= 0:
            return None, "missing_benchmark"
        # Benchmark close-to-close is an approximation to the stock open-to-close window.
        gross = float(window["gross"][entry, column])
        abnormal = gross - float(end_benchmark / start_benchmark - 1)
        return {"entry_date": entry_date.date().isoformat(), "exit_date": exit_date.date().isoformat(),
                "scheduled_exit_date": sessions[exit_index].date().isoformat(),
                "gross": gross, "abnormal": abnormal,
                **{name: bool(window[name][entry, column]) for name in _RETURN_FLAGS},
                "price_basis": "raw" if window["unadjusted_fallback"][entry, column] else "adjusted_chain"}, None

    for h in horizons:
        window = _holding_window(holding, h, missing_exit)
        rows, excluded = [], Counter()
        included_entries = []
        cost_prices, cost_markets, cost_years, cost_adv = [], [], [], []
        for number, event in events.iterrows():
            entry = plans[number]
            result, reason = outcome(entry, event.stock_code, h)
            if reason is None:
                liquidity = adv.get((sessions[entry - 1], event.stock_code), np.nan)
                if not np.isfinite(liquidity):
                    reason = "missing_liquidity"
            if reason is not None:
                excluded[reason] += 1
                continue
            column = stock_columns[event.stock_code]
            cost_prices.append(holding["open"][entry, column])
            cost_markets.append(holding["market"][entry, column])
            cost_years.append(sessions[entry].year)
            cost_adv.append(liquidity)
            result.update(event_id=event.event_id, stock_code=event.stock_code)
            if groups is not None:
                value = groups.iloc[number]
                result["feature_value"] = value.item() if isinstance(value, np.generic) else value
            rows.append(result)
            included_entries.append(entry)
        costs = cost_model.round_trip_cost_vectorized(
            np.asarray(cost_prices, dtype=float), cost_markets, np.asarray(cost_years, dtype=int),
            np.asarray(cost_adv, dtype=float), multiplier=cost_multiplier)
        for result, cost in zip(rows, costs):
            result.update(cost=float(cost), net=result["abnormal"] - float(cost))
        aggregate = _aggregate(rows)
        aggregate["no_later_price"] = excluded["no_later_price"]
        feature_results = []
        if groups is not None and rows:
            for value in pd.Series([row["feature_value"] for row in rows]).unique():
                feature_results.append({"value": value.item() if isinstance(value, np.generic) else value,
                                        **_aggregate([row for row in rows if row["feature_value"] == value])})
        # Cache outcomes for a fixed entry/horizon; draw from entry-only eligibility.
        control_values, control_exclusions, control_flags = {}, Counter(), Counter()
        for entry in set(included_entries):
            outcomes = [outcome(entry, stock, h) for stock in entry_pools[entry]]
            control_values[entry] = outcomes
        means, counts = [], []
        for _ in range(n_controls):
            values = []
            for entry in included_entries:
                candidates = control_values[entry]
                result, reason = candidates[int(rng.integers(len(candidates)))]
                if reason is None:
                    values.append(result["abnormal"])
                    control_flags.update({name: int(result[name]) for name in _RETURN_FLAGS})
                else:
                    control_exclusions[reason] += 1
            means.append(float(np.mean(values)) if values else np.nan)
            counts.append(len(values))
        output["horizons"][str(h)] = {
            "overall": aggregate, "by_feature": feature_results, "events": rows,
            **{name: aggregate[name] for name in _RETURN_FLAGS}, "no_later_price": excluded["no_later_price"],
            "excluded_count": sum(excluded.values()), "excluded": dict(excluded),
            "random_control": {**_control_summary(means, aggregate["mean_abnormal"]),
                               **{name: control_flags[name] for name in _RETURN_FLAGS},
                               "no_later_price": control_exclusions["no_later_price"],
                               "valid_events_per_draw": counts, "excluded": dict(control_exclusions)},
        }
    return output
