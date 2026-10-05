"""Close-known data and deterministic tables for LH001 section 4.1."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import hc001
from src.ingestion import dart_quarterly
from src.ingestion.storage import read_rows
from src.research import industry_index, industry_map
from src.runner.event_evidence import _category, _PATTERNS

from . import guard_data
from .config import LH001

GROUPS = tuple(sorted(set(industry_map.KSIC_PREFIXES.values())))
CATEGORIES = tuple(name for name, _ in _PATTERNS) + ("other",)
MARKET_COLUMNS = ("series", "return_20", "return_60", "return_120", "breadth_50")
INDUSTRY_COLUMNS = ("id", "industry", *[f"rs_{kind}_{h}" for kind in ("cap", "ew") for h in (20, 60, 120)],
                    "high_52w_share", "revenue_yoy", "operating_income_yoy", "phase2_count", "financial_coverage")
STOCK_COLUMNS = ("id", "industry", "market_cap_percentile", "trading_value", "return_20", "return_60", "return_120",
                 "return_250", "distance_52w_high", "volatility_60", "revenue_yoy", "operating_income_yoy",
                 "phase", "score", "quarters_4", *[f"disclosures_{name}" for name in CATEGORIES], "listing_coverage")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def json_value(value):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value


def load_financials(day, *, data_dir, repo_root=PROJECT_ROOT, config=LH001):
    """HC002's receipt/CFS/derivation policy, without opening future-year files."""
    day = pd.Timestamp(day)
    # Even July+ as-of requests scan earlier 2026 receipt archives.
    guard_data("2015-01-01", day, repo_root=repo_root, config=config)
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    if not directory.is_dir():
        raise ValueError("quarterly financial archive is required")
    records = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_110*.jsonl")):
        if int(path.name[:4]) > day.year:
            continue
        for row in read_rows(path):
            if row["available_date"] != dart_quarterly._filing_date(row["rcept_no"]):
                raise ValueError("quarterly receipt/availability mismatch")
            if pd.Timestamp(row["available_date"]) < day:
                records.append(row)
    latest = {}
    for row in dart_quarterly._derive(records):
        key = row["corp_code"], row["fiscal_quarter"]
        rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
        if key not in latest or rank > latest[key][0]:
            latest[key] = rank, row
    rows = [{**{field: row[field] for field in dart_quarterly.COLUMNS if field != "stock_code"}, "stock_code": stock}
            for _, row in (latest[key] for key in sorted(latest)) for stock in row["stock_codes"]]
    frame = pd.DataFrame(rows, columns=dart_quarterly.COLUMNS)
    for field in dart_quarterly.ACCOUNTS:
        frame[field] = pd.to_numeric(frame[field]).astype(float)
    return frame


def load_benchmarks(data_dir, start, end, *, repo_root=PROJECT_ROOT, config=LH001):
    """Guarded stored latest price-index rows only."""
    guard_data(start, end, repo_root=repo_root, config=config)
    path = Path(data_dir) / "market_context/benchmarks.jsonl"
    if not path.is_file():
        raise ValueError("benchmark archive is required")
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            day = pd.Timestamp(row["trade_date"])
            if pd.Timestamp(start) <= day <= pd.Timestamp(end):
                latest[day, row["series"], row["index_name"]] = float(row["close"])
    rows = [{"trade_date": day, "series": series, "index_name": name, "close": close}
            for (day, series, name), close in sorted(latest.items())]
    return pd.DataFrame(rows, columns=("trade_date", "series", "index_name", "close"))


def load_history(start, end, *, data_dir, repo_root=PROJECT_ROOT, config=LH001):
    guard_data(start, end, repo_root=repo_root, config=config)
    # Keep only research columns rather than millions of full provenance rows.
    columns = ("open", "high", "low", "close", "volume", "trading_value", "market_cap", "base_price",
               "stock_name", "market", "calendar_status")
    parts = []
    for day in pd.date_range(start, end):
        path = Path(data_dir) / "market/krx_daily" / str(day.year) / f"{day:%Y%m%d}.jsonl"
        latest = {}
        for row in read_rows(path):
            if pd.Timestamp(row["trade_date"]) != day:
                raise ValueError("KRX row date differs from file date")
            record = {field: row.get(field) for field in columns}
            record["ret_1d"] = float(row["change_rate_pct"]) / 100 if row.get("change_rate_pct") is not None else np.nan
            latest[row["stock_code"]] = record
        if latest:
            frame = pd.DataFrame.from_dict(latest, orient="index").rename_axis("stock_code")
            for field in columns[:8]:
                frame[field] = pd.to_numeric(frame[field], errors="raise")
            parts.append(frame.assign(trade_date=day).reset_index().set_index(["trade_date", "stock_code"]))
    if not parts:
        raise ValueError("no stored KRX rows in guarded span")
    prices = pd.concat(parts).sort_index()
    for field in columns[8:]:
        prices[field] = prices[field].astype("category")
    return prices


def load_last_dates(start, end, *, data_dir, repo_root=PROJECT_ROOT, config=LH001):
    """Presence dates only, within the stage's allowed price span.

    HC002 distinguishes a temporary missing exit from no later stored row. This
    bounded scan preserves that policy without loading future closes into inputs.
    """
    guard_data(start, end, repo_root=repo_root, config=config)
    latest = {}
    for path in sorted((Path(data_dir) / "market/krx_daily").glob("*/*.jsonl")):
        if len(path.stem) != 8 or not path.stem.isdigit():
            continue
        day = pd.Timestamp(path.stem)
        if not pd.Timestamp(start) <= day <= pd.Timestamp(end):
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if pd.Timestamp(row["trade_date"]) != day:
                    raise ValueError("KRX presence date differs from file date")
                latest[row["stock_code"]] = day
    return pd.Series(latest, dtype="datetime64[ns]")


def close_universe(history, day, known, profiles):
    """Identical HC002 eligibility, shared phase_signals/exclude_financials.

    common.universe_filter guards without an experiment ID; its 20-session filter
    is reproduced here so final reads can use the LH001 session rather than claim
    an unrelated holdout. Equivalence is tested against HC002 before 2026.
    """
    days = pd.DatetimeIndex(history.index.get_level_values("trade_date").unique()).sort_values()
    window = days[days <= day][-20:]
    if len(window) < 20 or day not in days:
        raise ValueError("20 decision-close price sessions are required")
    required = {"stock_name", "market", "close", "trading_value", "calendar_status"}
    if not required.issubset(history):
        raise ValueError(f"missing universe columns: {sorted(required - set(history))}")
    values = history.loc[history.index.get_level_values("trade_date").isin(window)]
    adv = values.trading_value.where(values.calendar_status.eq("verified")).unstack("stock_code").reindex(window)
    adv = adv.where(np.isfinite(adv))
    adv = adv.mean().where(adv.count() == 20)
    current = history.xs(day, level="trade_date").copy()
    if current.stock_name.isna().any() or not current.stock_name.map(lambda s: isinstance(s, str) and bool(s.strip())).all():
        raise ValueError("observed stock names are required")
    current["avg_trading_value_20d"] = adv.reindex(current.index)
    good = (current.index.str.endswith("0") & ~current.stock_name.str.contains("스팩", regex=False)
            & current.market.isin(("KOSPI", "KOSDAQ")) & current.calendar_status.eq("verified")
            & np.isfinite(current.close) & current.close.ge(1000) & current.avg_trading_value_20d.ge(1e8))
    signals = hc001.phase_signals(known).sort_values("fiscal_quarter", kind="stable").groupby("stock_code", sort=False).tail(1)
    signals = signals.set_index("stock_code")
    eligible = current.loc[good].join(signals)
    eligible = eligible.loc[eligible.computable.eq(True)]
    eligible, report = hc001.exclude_financials(eligible, profiles)
    eligible["industry"] = [profiles.get(code, industry_map.UNCLASSIFIED) for code in eligible.index]
    return eligible, report


def disclosure_counts(data_dir, sessions, codes, *, repo_root=PROJECT_ROOT, config=LH001):
    sessions = pd.DatetimeIndex(sessions)[-60:]
    if len(sessions) < 60:
        return {code: {"counts": None, "titles": [], "coverage": {"complete": False, "days": 0, "expected": 60}}
                for code in codes}
    guard_data(sessions[0], sessions[-1], repo_root=repo_root, config=config)
    expected = pd.date_range(sessions[0], sessions[-1])
    directory = Path(data_dir) / "disclosures/dart_full/list"
    completed, rows = 0, {}
    for day in expected:
        stamp = day.strftime("%Y%m%d")
        path = directory / stamp[:4] / f"{stamp}.jsonl"
        if not path.is_file():
            continue
        completed += 1
        for row in read_rows(path):
            if row.get("rcept_dt") != stamp or not isinstance(row.get("report_nm"), str):
                raise ValueError(f"invalid DART listing: {path.name}")
            if row.get("stock_code") in codes:
                rows[row["rcept_no"]] = row
    complete = completed == len(expected)
    coverage = {"complete": complete, "available": completed > 0, "days": completed, "expected": len(expected)}
    result = {}
    for code in codes:
        selected = sorted((row for row in rows.values() if row["stock_code"] == code),
                          key=lambda row: (row["rcept_dt"], row["rcept_no"]), reverse=True)
        counts = dict.fromkeys(CATEGORIES, 0) if complete else None
        if complete:
            for row in selected:
                counts[_category({"title": row["report_nm"], "source_type": "dart"})] += 1
        result[code] = {"counts": counts, "titles": [{"date": pd.Timestamp(row["rcept_dt"]).date().isoformat(),
                                                       "title": row["report_nm"]} for row in selected[:5]], "coverage": coverage}
    return result


def _growth(returns, horizon):
    window = returns.tail(horizon)
    return float((1 + window).prod() - 1) if len(window) == horizon and window.notna().all() else None


def build_inputs(prices, decision_date, *, data_dir, repo_root=PROJECT_ROOT, known=None, profiles=None, benchmarks=None, config=LH001):
    day = pd.Timestamp(decision_date)
    history = prices.loc[prices.index.get_level_values("trade_date") <= day].sort_index()
    if history.empty or history.index.has_duplicates:
        raise ValueError("unique close-known prices are required")
    days = pd.DatetimeIndex(history.index.get_level_values("trade_date").unique()).sort_values()
    guard_data(days[0], day, repo_root=repo_root, config=config)
    if known is None:
        known = load_financials(day, data_dir=data_dir, repo_root=repo_root, config=config)
    else:
        known = known.loc[pd.to_datetime(known.available_date) < day].copy()
    if profiles is None:
        profiles = hc001._industries(data_dir)
    universe, coverage = close_universe(history, day, known, profiles)
    if benchmarks is None:
        benchmarks = load_benchmarks(data_dir, days[0], day, repo_root=repo_root, config=config)
    benchmarks = benchmarks.loc[pd.to_datetime(benchmarks.trade_date) <= day]
    raw = history.copy()
    raw["industry"] = [profiles.get(code, industry_map.UNCLASSIFIED) for code in raw.index.get_level_values("stock_code")]
    returns = raw.ret_1d.where(raw.calendar_status.eq("verified")).unstack("stock_code")
    levels = industry_index._stock_levels(raw.assign(ret_1d=returns.stack().reindex(raw.index)))
    highs = (raw.high / raw.close).unstack("stock_code") * levels
    high_250 = highs.rolling(250, min_periods=250).max().iloc[-1]
    ma = levels.rolling(50, min_periods=50).mean().iloc[-1]
    breadth_members = universe.index
    breadth = None
    if len(breadth_members) and ma.reindex(breadth_members).notna().all() and levels.iloc[-1].reindex(breadth_members).notna().all():
        breadth = float((levels.iloc[-1][breadth_members] > ma[breadth_members]).mean())
    market = []
    for series, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        observed = benchmarks.loc[benchmarks.series.eq(series) & benchmarks.index_name.eq(name)].set_index("trade_date").close.reindex(days)
        changes = observed.pct_change(fill_method=None)
        market.append({"series": series, **{f"return_{h}": _growth(changes, h) for h in (20, 60, 120)}, "breadth_50": breadth})
    indices = industry_index.build_indices(raw)
    coverage["industry_index"] = indices.attrs
    aggregate = hc001.phase_signals(known)
    # Listings have receipt dates, not publication times: same-day reports may
    # have arrived after the close. Use the prior 60 completed sessions only.
    disclosures = disclosure_counts(data_dir, days[days < day], set(universe.index), repo_root=repo_root, config=config)
    stocks = []
    cap_pct = universe.market_cap.rank(pct=True, method="average") * 100
    trading_pct = universe.trading_value.rank(pct=True, method="average") * 100
    for code, row in universe.sort_index().iterrows():
        trend = []
        previous = aggregate.loc[aggregate.stock_code.eq(code)].set_index("fiscal_quarter")
        scale = abs(row.revenue) if np.isfinite(row.revenue) and row.revenue != 0 else np.nan
        period = pd.Period(row.fiscal_quarter, freq="Q")
        for offset in range(3, -1, -1):
            quarter = str(period - offset)
            item = previous.loc[quarter] if quarter in previous.index else None
            trend.append({"relative_quarter": f"q-{offset}", "quarter": quarter,
                          "revenue_ratio": item.revenue / scale if item is not None else None,
                          "income_ratio": item.operating_income / scale if item is not None else None,
                          "revenue_yoy": item.revenue_yoy if item is not None else None,
                          "operating_income_yoy": item.operating_income_yoy if item is not None else None})
        vol = returns[code].tail(60)
        record = {"stock_code": code, "name": row.stock_name, "industry": row.industry,
                  "market_cap_percentile": cap_pct[code], "adv_20": row.avg_trading_value_20d,
                  "trading_value": row.trading_value, "trading_value_percentile": trading_pct[code],
                  **{f"return_{h}": _growth(returns[code], h) for h in (20, 60, 120, 250)},
                  "distance_52w_high": levels.iloc[-1][code] / high_250[code] - 1,
                  "at_52w_high": highs.iloc[-1][code] >= high_250[code] if pd.notna(high_250[code]) else None,
                  "volatility_60": float(vol.std(ddof=1)) if len(vol) == 60 and vol.notna().all() else None,
                  "revenue_yoy": row.revenue_yoy, "operating_income_yoy": row.operating_income_yoy,
                  "phase": int(row.phase) if pd.notna(row.phase) else None, "score": row.score, "quarters_4": trend,
                  "financial_date": row.available_date, "listing_coverage": disclosures[code]["coverage"],
                  "titles": disclosures[code]["titles"]}
        record.update({f"disclosures_{kind}": disclosures[code]["counts"][kind] if disclosures[code]["counts"] is not None else None
                       for kind in CATEGORIES})
        stocks.append(record)
    industries = []
    for number, group in enumerate(GROUPS):
        members = universe.loc[universe.industry.eq(group)]
        row = {"id": f"G{chr(65 + number)}", "industry": group, "phase2_count": int(members.phase.eq(2).sum())}
        for kind, field in (("cap", "cap_return"), ("ew", "equal_return")):
            for horizon in (20, 60, 120):
                sector = _growth(indices.xs(group, level="industry")[field], horizon) if group in indices.index.get_level_values("industry") else None
                baseline = _growth(indices.xs(industry_index.ALL_MARKET, level="industry")[field], horizon)
                row[f"rs_{kind}_{horizon}"] = sector - baseline if sector is not None and baseline is not None else None
        high_flags = [stock["at_52w_high"] for stock in stocks if stock["industry"] == group]
        row["high_52w_share"] = float(np.mean(high_flags)) if high_flags and all(x is not None for x in high_flags) else None
        row["revenue_yoy"], row["operating_income_yoy"] = None, None
        relevant = aggregate.loc[aggregate.stock_code.isin(members.index)]
        matched = []
        if not relevant.empty:
            latest = relevant.fiscal_quarter.max()
            now = relevant.loc[relevant.fiscal_quarter.eq(latest)].drop_duplicates("corp_code")
            prior = relevant.loc[relevant.fiscal_quarter.eq(str(pd.Period(latest, freq="Q") - 4))].drop_duplicates("corp_code").set_index("corp_code")
            for current in now.itertuples():
                if current.corp_code in prior.index:
                    old = prior.loc[current.corp_code]
                    matched.append((current.revenue, current.operating_income, old.revenue, old.operating_income))
            if len(matched) == len(now) == members.corp_code.nunique() and matched and np.isfinite(matched).all():
                totals = np.asarray(matched).sum(axis=0)
                row["revenue_yoy"] = dart_quarterly._growth(totals[0], totals[2])
                row["operating_income_yoy"] = dart_quarterly._growth(totals[1], totals[3])
        row["financial_coverage"] = len(matched) / members.corp_code.nunique() if len(members) else None
        industries.append(row)
    return json_value({"decision_date": day, "market": market, "industries": industries, "stocks": stocks,
                       "coverage": coverage, "a_codes": universe.index[universe.phase.eq(2)].tolist(),
                       "source_window": [days[0], day]})


def id_map(bundle, anonymised):
    codes = sorted(stock["stock_code"] for stock in bundle["stocks"])
    if not anonymised:
        return dict(zip(codes, codes))
    seed = int(hashlib.sha256(bundle["decision_date"].encode()).hexdigest()[:16], 16)
    shuffled = np.random.default_rng(seed).permutation(len(codes))
    def letters(number):
        return "".join(chr(65 + (number // 26 ** power) % 26) for power in (2, 1, 0))
    return {code: "S" + letters(int(number)) for code, number in zip(codes, shuffled)}


def leak_check(text, bundle):
    assert not re.search(r"\d{4}-\d{2}-\d{2}|(?<!\d)(?:19|20)\d{6}(?!\d)", text), "absolute date leak"
    for stock in bundle["stocks"]:
        assert stock["stock_code"] not in text, "stock code leak"
        assert stock["name"] not in text, "stock name leak"


def _cell(value):
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (list, dict)):
        return _cell(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", r"\u007c").replace("<", r"\u003c").replace(">", r"\u003e")


def table(rows, columns):
    return "|".join(columns) + "\n" + "\n".join("|".join(_cell(row.get(col)) for col in columns) for row in rows)


def render(bundle, *, anonymised, codes=None, industry_only=False, texts=None, final_window=True):
    mapping = id_map(bundle, anonymised)
    chosen = set(mapping if codes is None else codes)
    rows = []
    for stock in sorted(bundle["stocks"], key=lambda row: mapping[row["stock_code"]]):
        if stock["stock_code"] not in chosen:
            continue
        row = {**stock, "id": mapping[stock["stock_code"]]}
        row["quarters_4"] = ";".join(
            ",".join([quarter["relative_quarter"] if anonymised else quarter["quarter"],
                      *[_cell(quarter[key]) for key in ("revenue_ratio", "income_ratio", "revenue_yoy", "operating_income_yoy")]])
            for quarter in stock["quarters_4"])
        if anonymised:
            row["trading_value"] = stock["trading_value_percentile"]
        row["listing_coverage"] = f"{stock['listing_coverage']['days']}/{stock['listing_coverage']['expected']};complete={stock['listing_coverage']['complete']}"
        if texts is not None:
            if anonymised:
                raise ValueError("business text is forbidden in anonymised screening")
            row["business_text"] = texts[stock["stock_code"]]["display"]
        rows.append(row)
    day = "t=0" if anonymised else bundle["decision_date"]
    parts = [f"decision={day}; returns=decimal; YoY/score=percent; trading_value={'percentile' if anonymised else 'KRW'}",
             table(bundle["market"], MARKET_COLUMNS), table(bundle["industries"], INDUSTRY_COLUMNS)]
    if not industry_only:
        columns = STOCK_COLUMNS + (() if anonymised else ("name", "financial_date"))
        if not anonymised and final_window:
            columns += ("titles",)
        if texts is not None:
            columns += ("business_text",)
        parts.append(table(rows, columns))
    rendered = "\n\n".join(parts)
    if anonymised:
        leak_check(rendered, bundle)
    return rendered, {mapping[code]: code for code in sorted(chosen)}


def business_texts(codes, decision_date, data_dir, *, loader=None, repo_root=PROJECT_ROOT, config=LH001):
    if loader is None:
        guard_data("2015-01-01", decision_date, repo_root=repo_root, config=config)
        from src.ingestion.dart_business_text import load_business_text
        loader = load_business_text
    output = {}
    for code in codes:
        result = loader(code, decision_date, data_dir)
        if result is None:
            output[code] = {"display": "본문 없음", "missing": True, "provenance": None}
            continue
        if pd.Timestamp(result["rcept_dt"]) >= pd.Timestamp(decision_date):
            raise ValueError("business text receipt must precede decision date")
        text = result["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("business text must be extracted nonempty text of at most 4000 characters")
        if hashlib.sha256(text.encode()).hexdigest() != result["sha256"] or len(text) != result["chars"]:
            raise ValueError("business text hash/length mismatch")
        provenance = {key: result[key] for key in ("report_nm", "rcept_no", "rcept_dt", "period", "fallback_used", "chars", "sha256", "extraction_version")}
        output[code] = {"display": result["report_nm"] + " " + result["rcept_dt"] + " " + json.dumps(text, ensure_ascii=False),
                        "missing": False, "provenance": provenance}
    return output
