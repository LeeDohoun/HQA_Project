"""Industry price indices and pre-session features from the full-market store.

Returns are decimal ret_1d, including the exchange's split-day adjustment.
Session t membership, SPAC exclusion, liquidity and cap weights use t-1 only.
A positive liquidity threshold requires 20 complete sessions of trading_value;
zero disables that filter. A row with zero volume keeps its stored return and
membership. A held member with no row contributes zero on that session, then
leaves the weights from the next session through the end of the input window.
No delisting loss is invented and same-day weights are not renormalized. For a
row with missing ret_1d, finite positive close and previous-session close supply
close / previous close - 1; otherwise the same zero-and-drop policy applies.
This raw fallback does not recover exchange corporate-action adjustments.
Result attrs contain held-member event totals members_dropped_no_row and
raw_fallback, plus dictionaries members_dropped_no_row_by_industry and
raw_fallback_by_industry (prior-session labels, without double-counting the
all-market benchmark). Index gaps with no members are not bridged.

Features for decision date t end at the last stored session strictly before t.
Relative strength defaults to the industry/market compounded growth ratio minus
one; relative_strength="difference" subtracts market cumulative return from
industry cumulative return instead.
Breadth uses ret_1d-chained price proxies, so a split cannot distort its MA.
Warm-up shortages produce NaN features, never zero signals. OpenDART industries
are current classifications, and load_prices selects latest stored revisions:
neither classification history nor historical publication vintages are recovered.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import holdout
from src.ingestion.krx_market import _date, load_prices
from src.utils.stock_codes import is_stock_code

from .industry_map import DEFAULT_COMPANIES_PATH, attach_industry


ALL_MARKET = "전체시장"
SPAC_PATTERN = r"SPAC|스팩|기업\s*인수\s*목적"


def _prepare(prices_frame, companies_path, minimum):
    if isinstance(minimum, bool) or not np.isfinite(minimum) or minimum < 0:
        raise ValueError("minimum 20-session trading value must be finite and nonnegative")
    if (not isinstance(prices_frame.index, pd.MultiIndex)
            or prices_frame.index.names != ["trade_date", "stock_code"]):
        raise ValueError("prices must be indexed by (trade_date, stock_code)")
    required = {"stock_name", "market_cap", "ret_1d"}
    if minimum > 0:
        required.add("trading_value")
    if not required.issubset(prices_frame.columns):
        raise ValueError(f"missing industry price columns: {sorted(required - set(prices_frame.columns))}")
    if prices_frame.empty:
        raise ValueError("industry prices must contain observed sessions")
    frame = prices_frame.copy()
    dates = pd.DatetimeIndex(pd.to_datetime(frame.index.get_level_values("trade_date")))
    codes = frame.index.get_level_values("stock_code")
    if (dates.hasnans or dates.tz is not None or not dates.equals(dates.normalize())
            or frame.index.has_duplicates or not all(is_stock_code(code) for code in codes)):
        raise ValueError("industry price keys must be unique valid stocks and date-only sessions")
    frame.index = pd.MultiIndex.from_arrays([dates, codes], names=frame.index.names)
    if frame.index.has_duplicates:
        raise ValueError("industry price keys must be unique after date normalization")
    frame = frame.sort_index()
    if any(not isinstance(name, str) or not name.strip() for name in frame["stock_name"]):
        raise ValueError("industry prices require observed stock names")
    for field in ("market_cap", "ret_1d", *(("trading_value",) if minimum > 0 else ())):
        frame[field] = pd.to_numeric(frame[field], errors="raise")
        values = frame[field].to_numpy(dtype=float)
        if np.isinf(values).any():
            raise ValueError(f"industry prices contain nonfinite {field}")
        if field == "market_cap" and (np.isnan(values).any() or (values <= 0).any()):
            raise ValueError("industry prices require positive market_cap on observed rows")
        if field == "ret_1d" and (values < -1).any():
            raise ValueError("industry ret_1d cannot be below -1")
        if field == "trading_value" and (values < 0).any():
            raise ValueError("industry trading_value cannot be negative")
    if "industry" not in frame:
        frame = attach_industry(frame, companies_path=companies_path)
    if any(not isinstance(label, str) or not label.strip() or label == ALL_MARKET for label in frame["industry"]):
        raise ValueError("industry labels must be nonempty and distinct from the market label")
    common = frame.index.get_level_values("stock_code").str.endswith("0")
    eligible = pd.Series(common, index=frame.index) & ~frame["stock_name"].str.contains(
        SPAC_PATTERN, case=False, regex=True)
    eligible = eligible.unstack("stock_code", fill_value=False)
    if minimum > 0:
        adv = frame["trading_value"].unstack("stock_code").rolling(20, min_periods=20).mean()
        eligible &= adv.ge(minimum)
    returns = frame["ret_1d"].unstack("stock_code")
    fallback = pd.DataFrame(False, index=returns.index, columns=returns.columns)
    if "close" in frame:
        closes = pd.to_numeric(frame["close"], errors="raise").unstack("stock_code")
        previous = closes.shift(1)
        raw_returns = closes / previous.where(previous.gt(0)) - 1
        fallback = (returns.isna() & closes.gt(0) & previous.gt(0)
                    & np.isfinite(closes) & np.isfinite(previous) & np.isfinite(raw_returns))
        returns = returns.mask(fallback, raw_returns)
        frame["ret_1d"] = returns.stack().reindex(frame.index)
    dropped = eligible.shift(1, fill_value=False) & returns.isna()
    dropped &= dropped.cumsum().eq(1)
    eligible &= ~dropped.cummax()
    held = eligible.shift(1, fill_value=False)
    fallback &= held
    labels = frame["industry"].unstack("stock_code").shift(1)
    for name, events in (("members_dropped_no_row", dropped), ("raw_fallback", fallback)):
        frame.attrs[name] = int(events.to_numpy().sum())
        frame.attrs[f"{name}_by_industry"] = {
            label: int((events & labels.eq(label)).to_numpy().sum())
            for label in sorted(frame["industry"].unique())
        }
    return frame, eligible


def _index_levels(returns):
    levels = pd.Series(np.nan, index=returns.index, dtype=float)
    valid = np.flatnonzero(returns.notna().to_numpy())
    if len(valid):
        first = int(valid[0])
        if first > 0:
            levels.iloc[first - 1] = 100.0
        levels.iloc[first:] = 100 * (1 + returns.iloc[first:]).cumprod(skipna=False)
    return levels


def build_indices(prices_frame: pd.DataFrame, *, min_trading_value_20d=0.0,
                  companies_path: str | Path = DEFAULT_COMPANIES_PATH) -> pd.DataFrame:
    """Return cap/equal returns, levels (base 100), and prior-session member counts.

    An existing industry column can supply explicit synthetic/reference labels.
    Otherwise attach current company classifications from companies_path.
    The all-market benchmark uses the same common-share and liquidity universe,
    including 미분류 stocks. The first input session is a weighting baseline.
    """
    frame, eligible = _prepare(prices_frame, companies_path, min_trading_value_20d)
    returns = frame["ret_1d"].unstack("stock_code")
    caps = frame["market_cap"].unstack("stock_code").shift(1)
    labels = frame["industry"].unstack("stock_code").shift(1)
    held = eligible.shift(1, fill_value=False)
    groups = [*sorted(frame["industry"].unique()), ALL_MARKET]
    parts = []
    for industry in groups:
        members = held if industry == ALL_MARKET else held & labels.eq(industry)
        count = members.sum(axis=1)
        capital = caps.where(members, 0.0)
        daily_returns = returns.where(members, 0.0).fillna(0.0)
        cap_return = (capital * daily_returns).sum(axis=1) / capital.sum(axis=1)
        equal_return = daily_returns.sum(axis=1) / count
        part = pd.DataFrame({"cap_return": cap_return, "equal_return": equal_return,
                             "cap_index": _index_levels(cap_return), "equal_index": _index_levels(equal_return),
                             "member_count": count})
        part["industry"] = industry
        parts.append(part.reset_index().set_index(["trade_date", "industry"]))
    result = pd.concat(parts).sort_index()
    result.attrs.update(frame.attrs)
    return result


def load_indices(from_date: str, to_date: str, data_dir: str | Path, *, min_trading_value_20d=0.0,
                 experiment_id: str | None = None) -> pd.DataFrame:
    """Read all codes from the local KRX store; callers supply baseline/warm-up dates.

    Protected dates go through the existing single-use holdout guard. Inside an
    open HoldoutSession, supply the same experiment_id to share its one claim.
    """
    holdout.guard_period(_date(from_date), _date(to_date), experiment_id=experiment_id)
    prices = load_prices(None, from_date, to_date, data_dir)
    return build_indices(prices, min_trading_value_20d=min_trading_value_20d,
                         companies_path=Path(data_dir) / "reference/dart_company/companies.jsonl")


def _stock_levels(frame):
    returns = frame["ret_1d"].unstack("stock_code")
    present = pd.Series(True, index=frame.index).unstack("stock_code", fill_value=False)
    levels = pd.DataFrame(np.nan, index=returns.index, columns=returns.columns)
    for stock in returns:
        first = int(np.flatnonzero(present[stock].to_numpy())[0])
        growth = 1 + returns[stock].iloc[first:].copy()
        growth.iloc[0] = 1.0
        levels.loc[growth.index, stock] = 100 * growth.cumprod(skipna=False)
    return levels


def industry_features(prices_frame: pd.DataFrame, decision_date: str, *, min_trading_value_20d=0.0,
                      companies_path: str | Path = DEFAULT_COMPANIES_PATH,
                      relative_strength: str = "ratio") -> pd.DataFrame:
    """Pre-session RS 20/60/120, MA20 breadth, top-five median R60, and count.

    Cap ties use ascending stock_code. With fewer than five members all members
    enter the median. Breadth/top-five features are NaN if any selected member
    lacks the required window. Relative strength uses cap-weighted industry and
    all-market daily returns; every return in each window must be observed.
    Choose "ratio" for compounded growth ratio minus one (default), or
    "difference" for industry cumulative return minus market cumulative return.
    """
    if relative_strength not in ("ratio", "difference"):
        raise ValueError("relative_strength must be 'ratio' or 'difference'")
    decision = pd.Timestamp(decision_date)
    if pd.isna(decision) or decision.tzinfo is not None or decision != decision.normalize():
        raise ValueError("decision_date must be a date-only, timezone-naive date")
    if "trade_date" not in prices_frame.index.names:
        raise ValueError("prices require trade_date in the index")
    dates = pd.to_datetime(prices_frame.index.get_level_values("trade_date"))
    history = prices_frame.loc[dates < decision]
    frame, eligible = _prepare(history, companies_path, min_trading_value_20d)
    indices = build_indices(frame, min_trading_value_20d=min_trading_value_20d)
    caps = frame["market_cap"].unstack("stock_code")
    labels = frame["industry"].unstack("stock_code")
    returns = frame["ret_1d"].unstack("stock_code")
    levels = _stock_levels(frame)
    moving_average = levels.rolling(20, min_periods=20).mean().iloc[-1]
    cutoff = returns.index[-1]
    market_returns = indices.xs(ALL_MARKET, level="industry")["cap_return"]
    rows = []
    for industry in sorted(frame["industry"].unique()):
        members = eligible.columns[eligible.iloc[-1] & labels.iloc[-1].eq(industry)]
        row = {"industry": industry, "as_of": cutoff, "member_count": len(members)}
        industry_returns = indices.xs(industry, level="industry")["cap_return"]
        for horizon in (20, 60, 120):
            window, market_window = industry_returns.tail(horizon), market_returns.tail(horizon)
            strength = np.nan
            if len(window) == horizon and window.notna().all() and market_window.notna().all():
                market_growth = (1 + market_window).prod()
                industry_growth = (1 + window).prod()
                if relative_strength == "difference":
                    strength = industry_growth - market_growth
                elif market_growth > 0:
                    strength = industry_growth / market_growth - 1
            row[f"rs_{horizon}"] = strength
        row["breadth_20"] = np.nan
        row["top5_return_60"] = np.nan
        if len(members):
            if moving_average[members].notna().all() and levels.iloc[-1][members].notna().all():
                row["breadth_20"] = float((levels.iloc[-1][members] > moving_average[members]).mean())
            top = caps.iloc[-1][members].sort_values(ascending=False, kind="stable").head(5).index
            window = returns[top].tail(60)
            if len(window) == 60 and window.notna().to_numpy().all():
                row["top5_return_60"] = float(((1 + window).prod(axis=0) - 1).median())
        rows.append(row)
    result = pd.DataFrame(rows).set_index("industry")
    result.attrs.update(frame.attrs)
    return result
