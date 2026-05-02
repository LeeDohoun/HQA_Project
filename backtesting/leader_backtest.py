from __future__ import annotations

"""Point-in-time AI-theme leader backtest.

This module is intentionally deterministic and fast.  It uses only data known
as of each rebalance date for selection, then evaluates the subsequent holding
period return.  The output payload is shaped for ``POST /backtest/results``.
"""

import argparse
import bisect
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.temporal_rag import normalize_ymd
from src.config.settings import get_data_dir


DEFAULT_LOOKBACK_BY_SOURCE: Dict[str, Optional[int]] = {
    "news": 365,
    "general_news": 365,
    "forum": 90,
    "dart": None,
}


@dataclass(frozen=True)
class StockTarget:
    stock_name: str
    stock_code: str


@dataclass(frozen=True)
class DocumentSignal:
    stock_code: str
    source_type: str
    published_ymd: str


def run_leader_backtest(
    *,
    data_dir: str | Path | None = None,
    theme: str = "AI",
    theme_key: str = "ai",
    from_date: str = "20250101",
    to_date: str = "20251231",
    rebalance: str = "M",
    top_n: int = 3,
    hold_days: int = 20,
    min_history_days: int = 150,
    transaction_cost_bps: float = 15.0,
    output_dir: str | Path | None = None,
    task_id: str = "",
    submit_url: str = "",
) -> Dict[str, Any]:
    data_root = Path(data_dir) if data_dir else get_data_dir()
    from_ymd = _require_ymd(from_date, "from_date")
    to_ymd = _require_ymd(to_date, "to_date")
    run_id = task_id or _default_task_id(theme_key, from_ymd, to_ymd, top_n, hold_days)

    targets = load_targets(data_root, theme_key)
    prices = load_price_history(data_root, theme_key)
    docs = load_document_signals(data_root, theme_key)
    warnings: List[str] = []

    if not targets:
        warnings.append(f"theme_targets not found or empty: {theme_key}")
        targets = [
            StockTarget(stock_name=name, stock_code=code)
            for code, name in sorted(_stock_names_from_prices(prices).items())
        ]

    if not prices:
        raise ValueError(f"price history not found: theme_key={theme_key}")

    target_by_code = {target.stock_code: target for target in targets}
    common_calendar = _build_common_calendar(prices, from_ymd, to_ymd, hold_days)
    rebalance_dates = _select_rebalance_dates(common_calendar, rebalance)
    if not rebalance_dates:
        raise ValueError(f"no rebalance dates in period: {from_ymd}..{to_ymd}")

    doc_index = _index_docs(docs)
    positions: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    period_rows: List[Dict[str, Any]] = []
    equity = 1.0
    benchmark_equity = 1.0
    transaction_cost = transaction_cost_bps / 10000.0

    for as_of_ymd in rebalance_dates:
        scored = _score_universe(
            as_of_ymd=as_of_ymd,
            target_by_code=target_by_code,
            prices=prices,
            doc_index=doc_index,
            hold_days=hold_days,
            min_history_days=min_history_days,
        )
        eligible = [row for row in scored if row.get("eligible")]
        if len(eligible) < top_n:
            warnings.append(f"{as_of_ymd}: eligible stocks {len(eligible)} < top_n {top_n}")
            continue

        ranked = sorted(eligible, key=lambda row: row["leader_score"], reverse=True)
        selected = ranked[:top_n]
        selected_return = float(np.mean([row["realized_return"] for row in selected]))
        selected_net_return = selected_return - (transaction_cost * 2)
        benchmark_return = float(np.mean([row["realized_return"] for row in eligible]))
        benchmark_net_return = benchmark_return - (transaction_cost * 2)

        equity *= 1.0 + selected_net_return
        benchmark_equity *= 1.0 + benchmark_net_return

        period_rows.append(
            {
                "as_of_date": _fmt_ymd(as_of_ymd),
                "entry_date": selected[0]["entry_date"],
                "exit_date": selected[0]["exit_date"],
                "selected_count": len(selected),
                "eligible_count": len(eligible),
                "portfolio_return_pct": _pct(selected_net_return),
                "benchmark_return_pct": _pct(benchmark_net_return),
                "excess_return_pct": _pct(selected_net_return - benchmark_net_return),
                "top_symbols": [
                    {
                        "stock_name": row["stock_name"],
                        "stock_code": row["stock_code"],
                        "leader_score": row["leader_score"],
                        "realized_return_pct": _pct(row["realized_return"]),
                    }
                    for row in selected
                ],
            }
        )
        equity_curve.append(
            {
                "date": selected[0]["exit_date"],
                "equity": round(equity, 6),
                "benchmark_equity": round(benchmark_equity, 6),
                "period_return_pct": _pct(selected_net_return),
                "benchmark_return_pct": _pct(benchmark_net_return),
            }
        )

        for rank, row in enumerate(selected, start=1):
            position = dict(row)
            position["rank"] = rank
            position["weight"] = round(1.0 / top_n, 6)
            position["realized_return_pct"] = _pct(position.pop("realized_return"))
            position["predicted_return_pct"] = _pct(position.pop("predicted_return"))
            position["target_price"] = round(position["target_price"], 2)
            position["entry_price"] = round(position["entry_price"], 2)
            position["exit_price"] = round(position["exit_price"], 2)
            position.pop("eligible", None)
            positions.append(position)

    metrics = _compute_metrics(
        equity_curve=equity_curve,
        positions=positions,
        from_ymd=from_ymd,
        to_ymd=to_ymd,
        hold_days=hold_days,
    )
    leaders = _aggregate_leaders(positions)
    predictions = [
        {
            key: row[key]
            for key in [
                "as_of_date",
                "entry_date",
                "exit_date",
                "stock_name",
                "stock_code",
                "rank",
                "leader_score",
                "predicted_return_pct",
                "target_price",
                "realized_return_pct",
            ]
        }
        for row in positions
    ]

    result = {
        "task_id": run_id,
        "mode": "backtest",
        "result_type": "backtest",
        "status": "completed",
        "theme": theme,
        "theme_key": theme_key,
        "period": {
            "from_date": _fmt_ymd(from_ymd),
            "to_date": _fmt_ymd(to_ymd),
            "rebalance": rebalance,
            "rebalance_count": len(period_rows),
            "hold_days": hold_days,
        },
        "strategy": {
            "name": "ai_theme_leader_momentum_v1",
            "top_n": top_n,
            "min_history_days": min_history_days,
            "transaction_cost_bps": transaction_cost_bps,
            "selection_inputs": [
                "20d momentum",
                "60d momentum",
                "150d trend",
                "20d volume ratio",
                "news/dart/forum document signal",
                "20d volatility penalty",
            ],
            "prediction_model": "momentum_price_forecast_v1",
        },
        "metrics": metrics,
        "leaders": leaders,
        "predictions": predictions,
        "positions": positions,
        "trades": _positions_to_trades(positions),
        "periods": period_rows,
        "equity_curve": equity_curve,
        "artifacts": {},
        "warnings": warnings + _default_warnings(),
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "data_dir": _display_path(data_root),
            "target_count": len(targets),
            "price_stock_count": len(prices),
            "document_signal_count": len(docs),
        },
    }

    out_path = _write_result(result, output_dir or data_root / "backtest_results", run_id)
    result["artifacts"]["result_json"] = _display_path(out_path)
    _write_result(result, output_dir or data_root / "backtest_results", run_id)

    if submit_url:
        submit_status = submit_result(result, submit_url)
        result["artifacts"]["submit_status"] = submit_status
        _write_result(result, output_dir or data_root / "backtest_results", run_id)

    return result


def load_targets(data_dir: Path, theme_key: str) -> List[StockTarget]:
    path = data_dir / "raw" / "theme_targets" / f"{theme_key}.jsonl"
    targets: Dict[str, StockTarget] = {}
    for row in _iter_jsonl(path):
        code = str(row.get("stock_code") or "").strip()
        name = str(row.get("stock_name") or "").strip()
        if code and name:
            targets[code] = StockTarget(stock_name=name, stock_code=code)
    return list(targets.values())


def load_price_history(data_dir: Path, theme_key: str) -> Dict[str, pd.DataFrame]:
    path = data_dir / "market_data" / theme_key / "chart.jsonl"
    rows: List[Dict[str, Any]] = []
    for row in _iter_jsonl(path):
        code = str(row.get("stock_code") or "").strip()
        timestamp = str(row.get("timestamp") or "").strip()
        if not code or not timestamp:
            continue
        open_ = _to_float(row.get("open"))
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        volume = _to_float(row.get("volume"))
        if None in {open_, high, low, close}:
            continue
        if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
            continue
        if high < max(open_, close, low) or low > min(open_, close, high):
            continue
        rows.append(
            {
                "Date": pd.to_datetime(timestamp),
                "stock_code": code,
                "stock_name": str(row.get("stock_name") or "").strip(),
                "Open": open_,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume or 0.0,
            }
        )

    if not rows:
        return {}

    df = pd.DataFrame.from_records(rows)
    output: Dict[str, pd.DataFrame] = {}
    for code, group in df.groupby("stock_code"):
        stock_df = group.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
        stock_df = stock_df.set_index("Date")
        output[str(code)] = stock_df
    return output


def load_document_signals(data_dir: Path, theme_key: str) -> List[DocumentSignal]:
    path = data_dir / "canonical_index" / theme_key / "corpus.jsonl"
    signals: List[DocumentSignal] = []
    for row in _iter_jsonl(path):
        meta = row.get("metadata") or {}
        code = str(meta.get("stock_code") or "").strip()
        source = str(meta.get("source_type") or "").strip().lower()
        published = normalize_ymd(meta.get("published_at") or row.get("published_at"))
        if code and source and published:
            signals.append(DocumentSignal(stock_code=code, source_type=source, published_ymd=published))
    return signals


def submit_result(result: Dict[str, Any], submit_url: str) -> Dict[str, Any]:
    import requests

    try:
        response = requests.post(submit_url, json=result, timeout=10)
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "body": response.text[:500],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _score_universe(
    *,
    as_of_ymd: str,
    target_by_code: Dict[str, StockTarget],
    prices: Dict[str, pd.DataFrame],
    doc_index: Dict[str, Dict[str, List[str]]],
    hold_days: int,
    min_history_days: int,
) -> List[Dict[str, Any]]:
    raw_rows: List[Dict[str, Any]] = []
    for code, target in target_by_code.items():
        df = prices.get(code)
        if df is None or df.empty:
            continue
        row = _features_for_stock(
            code=code,
            name=target.stock_name,
            df=df,
            as_of_ymd=as_of_ymd,
            doc_index=doc_index,
            hold_days=hold_days,
            min_history_days=min_history_days,
        )
        if row:
            raw_rows.append(row)

    eligible = [row for row in raw_rows if row["eligible"]]
    if not eligible:
        return raw_rows

    for key, rank_key, ascending in [
        ("return_60d", "return_60d_rank", True),
        ("return_20d", "return_20d_rank", True),
        ("trend_150d", "trend_150d_rank", True),
        ("volume_ratio_20d", "volume_rank", True),
        ("doc_signal", "doc_rank", True),
        ("volatility_20d", "low_vol_rank", False),
    ]:
        values = pd.Series([row[key] for row in eligible], dtype="float64")
        ranks = values.rank(pct=True, ascending=ascending).fillna(0.5).tolist()
        for row, rank in zip(eligible, ranks):
            row[rank_key] = float(rank)

    for row in eligible:
        leader_score = 100.0 * (
            0.25 * row["return_60d_rank"]
            + 0.20 * row["return_20d_rank"]
            + 0.20 * row["trend_150d_rank"]
            + 0.15 * row["doc_rank"]
            + 0.10 * row["volume_rank"]
            + 0.10 * row["low_vol_rank"]
        )
        predicted_return = _clip(
            0.50 * row["return_20d"] + 0.30 * row["return_60d"] + 0.20 * row["trend_150d"],
            -0.35,
            0.55,
        )
        row["leader_score"] = round(leader_score)
        row["predicted_return"] = predicted_return
        row["target_price"] = row["entry_price"] * (1.0 + predicted_return)

    return raw_rows


def _features_for_stock(
    *,
    code: str,
    name: str,
    df: pd.DataFrame,
    as_of_ymd: str,
    doc_index: Dict[str, Dict[str, List[str]]],
    hold_days: int,
    min_history_days: int,
) -> Optional[Dict[str, Any]]:
    ymd_index = pd.Series(df.index.strftime("%Y%m%d"), index=df.index)
    known = df.loc[ymd_index <= as_of_ymd]
    if known.empty:
        return None
    entry_pos = len(known) - 1
    if entry_pos < min_history_days - 1:
        return _ineligible_row(code, name, as_of_ymd, "insufficient_history")

    full_pos = df.index.get_loc(known.index[-1])
    if isinstance(full_pos, slice):
        full_pos = full_pos.stop - 1
    exit_pos = int(full_pos) + hold_days
    if exit_pos >= len(df):
        return _ineligible_row(code, name, as_of_ymd, "insufficient_future")

    entry = df.iloc[int(full_pos)]
    exit_ = df.iloc[exit_pos]
    closes = known["Close"]
    returns = closes.pct_change().dropna()
    close = float(entry["Close"])
    ret20 = _period_return(closes, 20)
    ret60 = _period_return(closes, 60)
    ma150 = float(closes.tail(150).mean())
    trend150 = close / ma150 - 1.0 if ma150 > 0 else 0.0
    vol20 = float(returns.tail(20).std() * math.sqrt(252)) if len(returns) >= 20 else 1.0
    vol_ma20 = float(known["Volume"].tail(20).mean())
    volume_ratio = float(entry["Volume"]) / vol_ma20 if vol_ma20 > 0 else 0.0
    docs = _count_recent_docs(doc_index.get(code, {}), as_of_ymd)
    doc_signal = (
        math.log1p(docs.get("news", 0)) * 1.2
        + math.log1p(docs.get("dart", 0)) * 1.5
        + math.log1p(docs.get("forum", 0)) * 0.5
        + math.log1p(docs.get("general_news", 0)) * 0.6
    )
    realized = float(exit_["Close"]) / close - 1.0

    return {
        "eligible": True,
        "as_of_date": _fmt_ymd(as_of_ymd),
        "entry_date": known.index[-1].strftime("%Y-%m-%d"),
        "exit_date": df.index[exit_pos].strftime("%Y-%m-%d"),
        "stock_name": name,
        "stock_code": code,
        "entry_price": close,
        "exit_price": float(exit_["Close"]),
        "return_20d": ret20,
        "return_60d": ret60,
        "trend_150d": trend150,
        "volatility_20d": vol20,
        "volume_ratio_20d": volume_ratio,
        "doc_signal": doc_signal,
        "doc_counts": docs,
        "realized_return": realized,
    }


def _ineligible_row(code: str, name: str, as_of_ymd: str, reason: str) -> Dict[str, Any]:
    return {
        "eligible": False,
        "as_of_date": _fmt_ymd(as_of_ymd),
        "stock_name": name,
        "stock_code": code,
        "reason": reason,
    }


def _count_recent_docs(source_index: Dict[str, List[str]], as_of_ymd: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    as_of_dt = datetime.strptime(as_of_ymd, "%Y%m%d")
    for source, dates in source_index.items():
        lookback = DEFAULT_LOOKBACK_BY_SOURCE.get(source, 365)
        lower = ""
        if lookback is not None:
            lower = (as_of_dt - pd.Timedelta(days=int(lookback))).strftime("%Y%m%d")
        left = bisect.bisect_left(dates, lower) if lower else 0
        right = bisect.bisect_right(dates, as_of_ymd)
        counts[source] = max(0, right - left)
    return counts


def _compute_metrics(
    *,
    equity_curve: List[Dict[str, Any]],
    positions: List[Dict[str, Any]],
    from_ymd: str,
    to_ymd: str,
    hold_days: int,
) -> Dict[str, Any]:
    if not equity_curve:
        return {
            "rebalance_count": 0,
            "total_return_pct": 0.0,
            "benchmark_return_pct": 0.0,
            "excess_return_pct": 0.0,
            "mdd_pct": 0.0,
            "sharpe": 0.0,
            "win_rate_pct": 0.0,
            "prediction_hit_rate_pct": 0.0,
        }

    period_returns = np.array([row["period_return_pct"] / 100.0 for row in equity_curve], dtype=float)
    benchmark_returns = np.array([row["benchmark_return_pct"] / 100.0 for row in equity_curve], dtype=float)
    equity_values = np.array([row["equity"] for row in equity_curve], dtype=float)
    benchmark_values = np.array([row["benchmark_equity"] for row in equity_curve], dtype=float)
    periods_per_year = 252.0 / max(1, hold_days)
    std = float(period_returns.std(ddof=1)) if len(period_returns) > 1 else 0.0
    sharpe = (float(period_returns.mean()) / std * math.sqrt(periods_per_year)) if std > 0 else 0.0
    start_dt = datetime.strptime(from_ymd, "%Y%m%d")
    end_dt = datetime.strptime(to_ymd, "%Y%m%d")
    years = max((end_dt - start_dt).days / 365.25, 1 / 365.25)
    total_return = float(equity_values[-1] - 1.0)
    benchmark_return = float(benchmark_values[-1] - 1.0)
    cagr = (float(equity_values[-1]) ** (1.0 / years)) - 1.0
    benchmark_cagr = (float(benchmark_values[-1]) ** (1.0 / years)) - 1.0

    realized = [row["realized_return_pct"] for row in positions]
    predicted = [row["predicted_return_pct"] for row in positions]
    hit_rate = 0.0
    if realized:
        hits = sum(1 for p, r in zip(predicted, realized) if (p >= 0 and r >= 0) or (p < 0 and r < 0))
        hit_rate = hits / len(realized)

    return {
        "rebalance_count": len(equity_curve),
        "position_count": len(positions),
        "total_return_pct": _pct(total_return),
        "benchmark_return_pct": _pct(benchmark_return),
        "excess_return_pct": _pct(total_return - benchmark_return),
        "cagr_pct": _pct(cagr),
        "benchmark_cagr_pct": _pct(benchmark_cagr),
        "mdd_pct": _pct(_max_drawdown(equity_values)),
        "benchmark_mdd_pct": _pct(_max_drawdown(benchmark_values)),
        "sharpe": round(sharpe, 3),
        "avg_period_return_pct": _pct(float(period_returns.mean())),
        "avg_benchmark_period_return_pct": _pct(float(benchmark_returns.mean())),
        "win_rate_pct": _pct(float((period_returns > 0).mean())),
        "prediction_hit_rate_pct": _pct(hit_rate),
        "avg_position_return_pct": round(float(np.mean(realized)), 2) if realized else 0.0,
        "median_position_return_pct": round(float(np.median(realized)), 2) if realized else 0.0,
    }


def _aggregate_leaders(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in positions:
        grouped[row["stock_code"]].append(row)

    leaders: List[Dict[str, Any]] = []
    for code, rows in grouped.items():
        returns = [row["realized_return_pct"] for row in rows]
        scores = [row["leader_score"] for row in rows]
        leaders.append(
            {
                "stock_name": rows[0]["stock_name"],
                "stock_code": code,
                "selection_count": len(rows),
                "avg_leader_score": round(float(np.mean(scores)), 1),
                "avg_realized_return_pct": round(float(np.mean(returns)), 2),
                "win_rate_pct": _pct(float(np.mean([ret > 0 for ret in returns]))),
            }
        )
    leaders.sort(key=lambda row: (row["selection_count"], row["avg_realized_return_pct"]), reverse=True)
    return leaders


def _positions_to_trades(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    trades: List[Dict[str, Any]] = []
    for row in positions:
        trades.append(
            {
                "date": row["entry_date"],
                "stock_name": row["stock_name"],
                "stock_code": row["stock_code"],
                "side": "BUY",
                "price": row["entry_price"],
                "weight": row["weight"],
                "reason": f"rank {row['rank']} leader_score {row['leader_score']}",
            }
        )
        trades.append(
            {
                "date": row["exit_date"],
                "stock_name": row["stock_name"],
                "stock_code": row["stock_code"],
                "side": "SELL",
                "price": row["exit_price"],
                "weight": row["weight"],
                "realized_return_pct": row["realized_return_pct"],
                "reason": "holding period exit",
            }
        )
    return trades


def _build_common_calendar(
    prices: Dict[str, pd.DataFrame],
    from_ymd: str,
    to_ymd: str,
    hold_days: int,
) -> List[str]:
    dates = set()
    for df in prices.values():
        for idx in df.index[:-hold_days] if len(df) > hold_days else []:
            ymd = idx.strftime("%Y%m%d")
            if from_ymd <= ymd <= to_ymd:
                dates.add(ymd)
    return sorted(dates)


def _select_rebalance_dates(calendar: List[str], rebalance: str) -> List[str]:
    if rebalance.upper() in {"D", "DAILY"}:
        return list(calendar)
    if rebalance.upper() in {"W", "WEEKLY"}:
        grouped: Dict[str, str] = {}
        for ymd in calendar:
            dt = datetime.strptime(ymd, "%Y%m%d")
            grouped[f"{dt.isocalendar().year}-{dt.isocalendar().week:02d}"] = ymd
        return list(grouped.values())

    grouped = {}
    for ymd in calendar:
        grouped[ymd[:6]] = ymd
    return list(grouped.values())


def _index_docs(docs: Iterable[DocumentSignal]) -> Dict[str, Dict[str, List[str]]]:
    output: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    for doc in docs:
        output[doc.stock_code][doc.source_type].append(doc.published_ymd)
    for by_source in output.values():
        for dates in by_source.values():
            dates.sort()
    return output


def _write_result(result: Dict[str, Any], output_dir: str | Path, task_id: str) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{task_id}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path


def _display_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _stock_names_from_prices(prices: Dict[str, pd.DataFrame]) -> Dict[str, str]:
    names = {}
    for code, df in prices.items():
        if "stock_name" in df and not df.empty:
            names[code] = str(df.iloc[-1].get("stock_name") or code)
    return names


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _period_return(closes: pd.Series, days: int) -> float:
    if len(closes) <= days:
        return 0.0
    base = float(closes.iloc[-days - 1])
    last = float(closes.iloc[-1])
    return last / base - 1.0 if base > 0 else 0.0


def _max_drawdown(equity_values: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity_values)
    drawdowns = equity_values / peaks - 1.0
    return float(drawdowns.min()) if len(drawdowns) else 0.0


def _pct(value: float) -> float:
    return round(float(value) * 100.0, 2)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _fmt_ymd(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _require_ymd(value: str, label: str) -> str:
    ymd = normalize_ymd(value)
    if not ymd:
        raise ValueError(f"invalid {label}: {value}")
    return ymd


def _default_task_id(theme_key: str, from_ymd: str, to_ymd: str, top_n: int, hold_days: int) -> str:
    return f"bt-{theme_key}-{from_ymd}-{to_ymd}-top{top_n}-h{hold_days}"


def _default_warnings() -> List[str]:
    return [
        "theme_targets has no point-in-time membership date; historical theme membership may include survivorship/future-membership bias.",
        "selection uses deterministic price/document features, not the full LLM ThemeLeaderOrchestrator.",
        "future returns are used only for evaluation after each as_of_date signal.",
    ]


def _print_summary(result: Dict[str, Any]) -> None:
    metrics = result["metrics"]
    print(f"[BACKTEST] task_id={result['task_id']}")
    print(f"[BACKTEST] theme={result['theme']} period={result['period']['from_date']}..{result['period']['to_date']}")
    print(
        "[BACKTEST] "
        f"return={metrics['total_return_pct']}% "
        f"benchmark={metrics['benchmark_return_pct']}% "
        f"excess={metrics['excess_return_pct']}% "
        f"mdd={metrics['mdd_pct']}% "
        f"sharpe={metrics['sharpe']}"
    )
    print(f"[BACKTEST] result_json={result['artifacts'].get('result_json', '')}")
    print("[BACKTEST] leaders:")
    for row in result["leaders"][:10]:
        print(
            f"  - {row['stock_name']}({row['stock_code']}): "
            f"selected={row['selection_count']} avg_return={row['avg_realized_return_pct']}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run point-in-time AI theme leader backtest.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--theme", default="AI")
    parser.add_argument("--theme-key", default="ai")
    parser.add_argument("--from-date", default="20250101")
    parser.add_argument("--to-date", default="20251231")
    parser.add_argument("--rebalance", default="M", choices=["D", "W", "M", "daily", "weekly"])
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--hold-days", type=int, default=20)
    parser.add_argument("--min-history-days", type=int, default=150)
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--submit-url", default="")
    args = parser.parse_args()

    result = run_leader_backtest(
        data_dir=args.data_dir,
        theme=args.theme,
        theme_key=args.theme_key,
        from_date=args.from_date,
        to_date=args.to_date,
        rebalance=args.rebalance,
        top_n=args.top_n,
        hold_days=args.hold_days,
        min_history_days=args.min_history_days,
        transaction_cost_bps=args.transaction_cost_bps,
        output_dir=args.output_dir or None,
        task_id=args.task_id,
        submit_url=args.submit_url,
    )
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
