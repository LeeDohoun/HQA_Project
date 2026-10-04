"""Offline, close-known signal evaluation on the supplied session calendar.

Price dates are exchange sessions, shared across stocks. Stale exits are valued
within the original window and never extend it. ADV uses up to 20 observations in
that trailing 20-session window (including the score date). Quantile ties are
broken by stock_code; Spearman uses average ranks. Undefined statistics are None.
Evaluators return metrics, not pass verdicts: the experiment runner is responsible
for preregistration and record_trial. Use M0 HoldoutSession around several tools
belonging to one holdout evaluation; individual calls guard their full read span.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, holdout
from backtesting.experiment_registry import PROJECT_ROOT


# Half a percentage point inside Korea's 30% daily limit allows for tick rounding.
PRICE_LIMIT_THRESHOLD = 0.295
_RETURN_FLAGS = ("unadjusted_fallback", "stale_exit", "halted_after_entry", "limit_down_exit")


def _dates(values) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(values))
    if dates.hasnans or dates.tz is not None or not dates.equals(dates.normalize()):
        raise ValueError("trade_date must contain date-only, timezone-naive session dates")
    return dates


def _price_frame(prices: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(prices.index, pd.MultiIndex) or prices.index.names != ["trade_date", "stock_code"]:
        raise ValueError("prices must be indexed by (trade_date, stock_code)")
    required = {"open", "high", "low", "close", "volume", "market"}
    if not required.issubset(prices.columns):
        raise ValueError(f"missing price columns: {sorted(required - set(prices.columns))}")
    frame = prices.copy()
    frame.index = pd.MultiIndex.from_arrays(
        [_dates(prices.index.get_level_values("trade_date")), prices.index.get_level_values("stock_code")],
        names=prices.index.names,
    )
    if frame.index.has_duplicates or frame.index.get_level_values("stock_code").isna().any():
        raise ValueError("price keys must be unique and nonmissing")
    return frame.sort_index()


def _guard(prices, *, experiment_id, repo_root, ledger_path):
    if not prices.empty:
        dates = prices.index.get_level_values("trade_date")
        holdout.guard_period(dates.min().date(), dates.max().date(), experiment_id=experiment_id,
                             repo_root=repo_root, ledger_path=ledger_path)


def _horizons(horizons):
    values = tuple(horizons)
    if not values or any(type(h) is not int or h < 1 for h in values) or len(set(values)) != len(values):
        raise ValueError("horizons must be distinct positive integers; entry must follow availability")
    return values


def _positive_int(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _wide(prices, column):
    return prices[column].unstack("stock_code")


def _adv(prices):
    value = _wide(prices, "close") * _wide(prices, "volume")
    if "trading_value" in prices:
        value = _wide(prices, "trading_value").combine_first(value)
    observed = value.to_numpy()
    if np.isinf(observed).any() or (observed < 0).any():
        raise ValueError("trading_value must be finite and nonnegative when present")
    result = value.rolling(20, min_periods=1).mean().stack().reindex(prices.index)
    result.name = "avg_trading_value_20d"
    return result


def avg_trading_value_20d(
    prices: pd.DataFrame, *, experiment_id: str | None = None,
    repo_root: str | Path = PROJECT_ROOT, ledger_path: str | Path = holdout.LEDGER_PATH,
) -> pd.Series:
    """Trailing 20-session ADV, with shorter initial history and close*volume fallback."""
    frame = _price_frame(prices)
    _guard(frame, experiment_id=experiment_id, repo_root=repo_root, ledger_path=ledger_path)
    return _adv(frame)


def _execution_policies(missing_exit, limit_fill):
    if missing_exit not in ("last_close", "drop"):
        raise ValueError("missing_exit must be last_close or drop")
    if limit_fill not in ("exclude", "assume_fill"):
        raise ValueError("limit_fill must be exclude or assume_fill")


def _last_price_dates(prices):
    # Only row dates are inspected beyond the guarded holding windows.
    return pd.Series(prices.index.get_level_values("trade_date"),
                     index=prices.index.get_level_values("stock_code")).groupby(level=0).max()


def _holding_data(prices, limit_fill, last_dates=None):
    opening = _wide(prices, "open")
    sessions, codes = opening.index, opening.columns
    closing, volume = (_wide(prices, name).to_numpy(dtype=float) for name in ("close", "volume"))
    opening = opening.to_numpy(dtype=float)
    previous = np.full_like(closing, np.nan)
    previous[1:] = closing[:-1]
    base = _wide(prices, "base_price").to_numpy(dtype=float) if "base_price" in prices else previous
    base = np.where(np.isnan(base), previous, base)
    has_base = np.isfinite(base) & (base > 0)
    limit_up = has_base & (opening >= base * (1 + PRICE_LIMIT_THRESHOLD))
    limit_down = has_base & (closing <= base * (1 - PRICE_LIMIT_THRESHOLD))
    if limit_fill == "assume_fill":
        limit_up[:] = False
        limit_down[:] = False
    positive_close = np.isfinite(closing) & (closing > 0)
    positive_volume = np.isfinite(volume) & (volume > 0)
    entry_tradable = np.isfinite(opening) & (opening > 0) & positive_volume
    exit_tradable = positive_close & positive_volume & ~limit_down
    positions = np.arange(len(sessions))[:, None]
    last_positive = np.maximum.accumulate(np.where(positive_close & ~limit_down, positions, -1), axis=0)
    last_tradable = np.maximum.accumulate(np.where(exit_tradable, positions, -1), axis=0)
    present = pd.Series(True, index=prices.index).unstack("stock_code", fill_value=False).to_numpy(dtype=bool)
    last_dates = _last_price_dates(prices) if last_dates is None else last_dates
    return {"sessions": sessions, "codes": codes, "open": opening, "close": closing,
            "market": _wide(prices, "market").to_numpy(), "present": present,
            "entry_tradable": entry_tradable, "exit_tradable": exit_tradable,
            "limit_up": limit_up, "limit_down": limit_down,
            "last_positive": last_positive, "last_tradable": last_tradable,
            "last_dates": last_dates.reindex(codes).to_numpy(dtype="datetime64[ns]"),
            "ret_1d": _wide(prices, "ret_1d").to_numpy(dtype=float) if "ret_1d" in prices else None,
            "price_basis": "adjusted_chain" if "ret_1d" in prices else "raw"}


def _holding_window(data, h, missing_exit):
    """Vectorized entry-session outcomes shared by signal, events and controls."""
    opening, closing = data["open"], data["close"]
    shape = opening.shape
    positions = np.arange(shape[0])[:, None]
    target = positions + h - 1
    safe_target = np.minimum(target, shape[0] - 1)
    columns = np.arange(shape[1])[None, :]
    reason = np.full(shape, None, dtype=object)
    reason[~data["present"]] = "missing_entry_price"
    reason[data["present"] & ~data["entry_tradable"]] = "untradable_entry"
    reason[data["entry_tradable"] & data["limit_up"]] = "limit_up_entry"
    eligible = (reason == None) & (target < shape[0])
    exit_present = data["present"][safe_target, columns]
    exit_tradable = data["exit_tradable"][safe_target, columns]
    blocked_limit = data["limit_down"][safe_target, columns]
    reason[eligible & ~exit_present] = "missing_exit_price"
    reason[eligible & exit_present & ~exit_tradable] = "untradable_exit"
    no_later = (eligible & ~exit_present
                & (data["last_dates"][None, :] < data["sessions"].to_numpy()[safe_target]))
    reason[no_later] = "no_later_price"
    actual = np.broadcast_to(safe_target, shape).copy()
    stale = np.zeros(shape, dtype=bool)
    halted = np.zeros(shape, dtype=bool)
    if missing_exit == "last_close":
        stale = eligible & ~exit_tradable & ~no_later
        last_tradable = data["last_tradable"][safe_target, columns]
        last_close = data["last_positive"][safe_target, columns]
        # Limit-down prices cannot be treated as sale fills. Other stale closes
        # are valuations, including positive closes recorded with zero volume.
        last_close = np.where(blocked_limit, last_tradable, last_close)
        halted = stale & (h > 1) & (last_close <= positions)
        last_close = np.where(halted | (last_close <= positions), positions, last_close)
        actual = np.where(stale, last_close, actual)
        marked_close = closing[actual, columns]
        usable = np.isfinite(marked_close) & (marked_close > 0)
        reason[stale & usable] = None
        stale &= usable
        halted &= stale
    reason[np.broadcast_to(target >= shape[0], shape)] = "missing_exit_session"
    valid = reason == None
    gross = np.full(shape, np.nan)
    np.divide(closing[actual, columns], opening, out=gross, where=valid)
    adjusted = np.zeros(shape, dtype=bool)
    if data["ret_1d"] is not None:
        adjusted = valid & np.isfinite(closing) & (closing > 0)
        chain = np.ones(shape)
        for offset in range(1, min(h, shape[0])):
            returns = np.full(shape, np.nan)
            returns[:-offset] = data["ret_1d"][offset:]
            needed = actual >= positions + offset
            adjusted &= ~needed | np.isfinite(returns)
            # Missing factors only yield the explicitly counted raw fallback.
            chain *= np.where(needed, 1 + returns, 1)
        gross[adjusted] = (closing / np.where(opening > 0, opening, np.nan) * chain)[adjusted]
    gross -= 1
    return {"gross": gross, "reason": reason, "exit_position": actual,
            "stale_exit": stale & valid, "halted_after_entry": halted & valid,
            "unadjusted_fallback": valid & ~adjusted, "limit_down_exit": valid & blocked_limit}


def _forward_observations(data, window, index):
    sessions, codes = data["sessions"], data["codes"]
    score_positions = sessions.get_indexer(index.get_level_values("trade_date"))
    stocks = codes.get_indexer(index.get_level_values("stock_code"))
    entries = score_positions + 1
    exists = (score_positions >= 0) & (entries < len(sessions)) & (stocks >= 0)
    result = pd.DataFrame(index=index, columns=["gross", "reason", "exit_date", *_RETURN_FLAGS])
    result["gross"] = np.nan
    result["reason"] = "missing_entry_session"
    result["exit_date"] = pd.NaT
    for name in _RETURN_FLAGS:
        result[name] = False
    if exists.any():
        selection = entries[exists], stocks[exists]
        for name in ("gross", "reason", *_RETURN_FLAGS):
            result.loc[exists, name] = window[name][selection]
        result.loc[exists, "exit_date"] = sessions.to_numpy()[window["exit_position"][selection]]
    return result


def _return_counts(frame):
    excluded = frame.reason.dropna().value_counts().to_dict()
    return {**{name: int(frame[name].sum()) for name in _RETURN_FLAGS},
            "no_later_price": int(excluded.get("no_later_price", 0)),
            "excluded_count": int(sum(excluded.values())), "excluded": excluded}


def _stale_records(frame):
    return [{"trade_date": row.trade_date.date().isoformat(), "stock_code": row.stock_code,
             "exit_date": row.exit_date.date().isoformat(), "stale_exit": True,
             "halted_after_entry": bool(row.halted_after_entry)}
            for row in frame.loc[frame.stale_exit].reset_index().itertuples()]


def _forward(prices, horizons, missing_exit="last_close", limit_fill="exclude", last_dates=None):
    data = _holding_data(prices, limit_fill, last_dates)
    result = pd.DataFrame(index=prices.index)
    metadata = {}
    for h in horizons:
        window = _holding_window(data, h, missing_exit)
        rows = _forward_observations(data, window, prices.index)
        result[h] = rows.gross
        metadata[str(h)] = {**_return_counts(rows), "stale_exits": _stale_records(rows)}
    result.attrs.update(price_basis=data["price_basis"], missing_exit=missing_exit,
                        limit_fill=limit_fill, horizons=metadata)
    return result


def forward_returns(
    prices: pd.DataFrame, horizons=(1, 5, 20), *, missing_exit="last_close", limit_fill="exclude",
    experiment_id: str | None = None,
    repo_root: str | Path = PROJECT_ROOT, ledger_path: str | Path = holdout.LEDGER_PATH,
) -> pd.DataFrame:
    """Enter at open[t+1]; chain ret_1d through t+h after entry's open-to-close.

    Missing adjusted returns use raw prices and are counted in attrs['horizons'].
    The same metadata reports stale valuations and excluded no_later_price rows.
    """
    horizons = _horizons(horizons)
    _execution_policies(missing_exit, limit_fill)
    frame = _price_frame(prices)
    _guard(frame, experiment_id=experiment_id, repo_root=repo_root, ledger_path=ledger_path)
    return _forward(frame, horizons, missing_exit, limit_fill)


def _summary(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    count = len(values)
    mean = float(values.mean()) if count else None
    std = float(values.std(ddof=1)) if count > 1 else None
    return {"mean": mean, "std": std, "count": count,
            "t_stat": mean / (std / np.sqrt(count)) if std is not None and std > 0 else None}


def _control_summary(values, actual):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    percentiles = np.quantile(values, [0.05, 0.5, 0.95]) if len(values) else [None] * 3
    return {"count": len(values), **dict(zip(("p05", "p50", "p95"), percentiles)),
            "share_of_controls": float(np.mean(values >= actual)) if len(values) and actual is not None else None}


def _rank_vector(values):
    ranked = pd.Series(values).rank(method="average").to_numpy()
    ranked = ranked - ranked.mean()
    norm = np.linalg.norm(ranked)
    return ranked / norm if norm > 0 else None


def _metrics(frame, dates, h, quantiles, min_stocks, n_controls, rng):
    daily_ic, portfolios = [], []
    controls = np.zeros(n_controls)
    ic_count = 0
    eligible_dates = set(dates[::h])
    for day, group in frame.groupby("trade_date", sort=True):
        if len(group) < min_stocks:
            continue
        score_rank = _rank_vector(group["score"])
        return_rank = _rank_vector(group["gross"])
        ic = None
        if score_rank is not None and return_rank is not None:
            ic = float(np.dot(score_rank, return_rank))
            ic_count += 1
            for draw in range(n_controls):
                controls[draw] += np.dot(rng.permutation(score_rank), return_rank)
        daily_ic.append({"trade_date": day.date().isoformat(), "ic": ic,
                         "non_overlapping": day in eligible_dates})
        # Stable stock ordering resolves ties without consulting future returns.
        group = group.sort_values(["score", "stock_code"], kind="stable").copy()
        group["quantile"] = np.arange(len(group)) * quantiles // len(group) + 1
        by_quantile = {}
        for q, members in group.groupby("quantile"):
            by_quantile[str(q)] = {"count": len(members), "gross": float(members.gross.mean()),
                                   "net": float(members.net.mean())}
        bottom, top = by_quantile["1"], by_quantile[str(quantiles)]
        universe = float(group.gross.mean())
        portfolios.append({"trade_date": day.date().isoformat(), "quantiles": by_quantile,
                           "universe_gross": universe, "top_minus_bottom_gross": top["gross"] - bottom["gross"],
                           "top_net_excess": top["net"] - universe})
    valid_ic = [row["ic"] for row in daily_ic if row["ic"] is not None]
    nonoverlap = _summary([row["ic"] for row in daily_ic if row["non_overlapping"] and row["ic"] is not None])
    ic_summary = _summary(valid_ic)
    ic_summary.update(t_stat=nonoverlap["t_stat"], non_overlapping=nonoverlap, daily=daily_ic)
    ic_summary["by_year"] = {}
    for year in sorted({row["trade_date"][:4] for row in daily_ic}):
        rows = [row for row in daily_ic if row["trade_date"].startswith(year)]
        annual = _summary([row["ic"] for row in rows if row["ic"] is not None])
        annual_nonoverlap = _summary([row["ic"] for row in rows if row["non_overlapping"] and row["ic"] is not None])
        annual.update(t_stat=annual_nonoverlap["t_stat"], non_overlapping=annual_nonoverlap)
        ic_summary["by_year"][year] = annual
    means = {str(q): {metric: _summary([row["quantiles"][str(q)][metric] for row in portfolios])["mean"]
                      for metric in ("gross", "net")} for q in range(1, quantiles + 1)}
    return {"observations": int(len(frame)), **{name: int(frame[name].sum()) for name in _RETURN_FLAGS},
            "skipped_dates": len(dates) - len(portfolios), "ic": ic_summary,
            "quantiles": {"daily": portfolios, "mean": means,
                          "top_minus_bottom_gross": _summary([r["top_minus_bottom_gross"] for r in portfolios])["mean"],
                          "top_net_excess": _summary([r["top_net_excess"] for r in portfolios])["mean"]},
            "random_control": _control_summary(controls / ic_count if ic_count else [], ic_summary["mean"])}


def evaluate_signal(
    scores: pd.DataFrame, prices: pd.DataFrame, *, horizons=(1, 5, 20), quantiles=5,
    min_stocks=10, n_controls=200, seed=0, cost_multiplier=1.0, experiment_id=None,
    missing_exit="last_close", limit_fill="exclude",
    repo_root: str | Path = PROJECT_ROOT, ledger_path: str | Path = holdout.LEDGER_PATH,
) -> dict:
    """Return JSON metrics under string horizon keys.

    IC mean/std use all valid dates; t-stat uses every h-th input scoring date,
    anchored before skipping thin dates. The same min_stocks applies per bucket.
    Costs use entry open/date/market and ADV known at the score's close. Price
    history after the latest required exit is sliced away before the guard/read.
    Later row dates identify no_later_price exclusions without reading their prices.
    Stale exits are valuations within the window, rather than assumed sale fills.
    """
    horizons = _horizons(horizons)
    _execution_policies(missing_exit, limit_fill)
    for name, value in (("quantiles", quantiles), ("min_stocks", min_stocks), ("n_controls", n_controls)):
        _positive_int(value, name)
    if min_stocks < max(2, quantiles):
        raise ValueError("min_stocks must be at least max(2, quantiles)")
    if not np.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("cost_multiplier must be finite and nonnegative")
    if not {"trade_date", "stock_code", "score"}.issubset(scores.columns):
        raise ValueError("scores require trade_date, stock_code, score")
    scores = scores[["trade_date", "stock_code", "score"]].copy()
    scores["trade_date"] = _dates(scores.trade_date)
    if scores[["trade_date", "stock_code"]].isna().any().any() or scores.duplicated(["trade_date", "stock_code"]).any():
        raise ValueError("score keys must be unique and nonmissing")
    dates = pd.DatetimeIndex(scores.trade_date.unique()).sort_values()
    frame = _price_frame(prices)
    last_dates = _last_price_dates(frame)
    sessions = frame.index.get_level_values("trade_date").unique()
    if len(dates) and len(sessions):
        end = min(sessions.searchsorted(dates[-1], side="right") - 1 + max(horizons), len(sessions) - 1)
        frame = frame.loc[frame.index.get_level_values("trade_date") <= sessions[max(0, end)]]
    elif not len(dates):
        frame = frame.iloc[:0]
    _guard(frame, experiment_id=experiment_id, repo_root=repo_root, ledger_path=ledger_path)
    holding = _holding_data(frame, limit_fill, last_dates)
    adv = _adv(frame)
    sessions = frame.index.get_level_values("trade_date").unique()
    entry_dates = dict(zip(sessions[:-1], sessions[1:]))
    entry_open = _wide(frame, "open").shift(-1).stack().reindex(frame.index)
    entry_market = _wide(frame, "market").shift(-1).stack().reindex(frame.index)
    base = scores.merge(pd.DataFrame({"adv": adv, "entry_open": entry_open, "market": entry_market}).reset_index(),
                        on=["trade_date", "stock_code"], how="left")
    base = base.loc[np.isfinite(base.score)].set_index(["trade_date", "stock_code"])
    base["cost"] = np.nan
    # Evaluate costs once for valid entries; horizon-specific exclusions follow below.
    entry_rows = holding["sessions"].get_indexer(base.index.get_level_values("trade_date")) + 1
    stock_columns = holding["codes"].get_indexer(base.index.get_level_values("stock_code"))
    known = (entry_rows > 0) & (entry_rows < len(sessions)) & (stock_columns >= 0)
    eligible = np.zeros(len(base), dtype=bool)
    if known.any():
        selection = entry_rows[known], stock_columns[known]
        eligible[known] = holding["entry_tradable"][selection] & ~holding["limit_up"][selection]
    eligible &= np.isfinite(base.adv)
    cost_rows = base.loc[eligible]
    years = pd.DatetimeIndex(cost_rows.index.get_level_values("trade_date").map(entry_dates)).year.to_numpy()
    base.loc[eligible, "cost"] = cost_model.round_trip_cost_vectorized(
        cost_rows.entry_open.to_numpy(), cost_rows.market.to_numpy(), years, cost_rows.adv.to_numpy(),
        multiplier=cost_multiplier)
    base["bucket"] = np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, base.adv, side="right")
    rng = np.random.default_rng(seed)
    output = {"price_basis": holding["price_basis"], "missing_exit": missing_exit,
              "limit_fill": limit_fill, "horizons": {}}
    for h in horizons:
        window = _holding_window(holding, h, missing_exit)
        observations = _forward_observations(holding, window, base.index)
        missing_liquidity = observations.reason.isna() & ~np.isfinite(base.adv)
        observations.loc[missing_liquidity, "reason"] = "missing_liquidity"
        observations.loc[missing_liquidity, "gross"] = np.nan
        for name in _RETURN_FLAGS:
            observations.loc[missing_liquidity, name] = False
        data = base.join(observations)
        data = data.loc[np.isfinite(data.gross)].reset_index()
        data["net"] = data.gross - data.cost
        result = _metrics(data, dates, h, quantiles, min_stocks, n_controls, rng)
        result.update(_return_counts(observations), stale_exits=_stale_records(observations))
        result["liquidity_buckets"] = {}
        lower = 0.0
        for bucket, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
            metrics = _metrics(data.loc[data.bucket == bucket], dates, h, quantiles, min_stocks, n_controls, rng)
            metrics.update(lower_inclusive=lower, upper_exclusive=float(upper) if np.isfinite(upper) else None)
            result["liquidity_buckets"][str(bucket)] = metrics
            lower = float(upper)
        output["horizons"][str(h)] = result
    return output
