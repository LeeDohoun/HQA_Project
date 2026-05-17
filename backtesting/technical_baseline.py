#!/usr/bin/env python3
from __future__ import annotations

"""Run simple technical-indicator baselines against the theme leader strategy."""

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.leader_backtest import (
    ExitConfig,
    RiskConfig,
    StockTarget,
    _add_counts,
    _aggregate_leaders,
    _apply_risk_filters,
    _active_target_by_code,
    _build_common_calendar,
    _compute_metrics,
    _clip,
    _default_warnings,
    _display_path,
    _effective_position_value,
    _exit_counts,
    _fmt_ymd,
    _index_docs,
    _latest_exit_date,
    _market_breadth_pct,
    _market_filter_reason,
    _pct,
    _positions_to_trades,
    _require_ymd,
    _round_trip_cost_bps,
    _round_trip_cost_return,
    _score_universe,
    _select_rebalance_dates,
    _stock_names_from_prices,
    _write_result,
    load_document_signals,
    load_price_history,
    load_theme_memberships,
    load_targets,
)
from src.config.settings import get_data_dir


BASELINE_SPECS: Dict[str, Dict[str, Any]] = {
    "rsi_oversold": {
        "label": "RSI(14) <= 30",
        "description": "Long the most oversold RSI(14) names.",
        "score_key": "rsi_score",
        "sort_reverse": True,
    },
    "bollinger_lower": {
        "label": "Close <= Bollinger lower band",
        "description": "Long names touching or breaking the 20-day lower Bollinger band.",
        "score_key": "bollinger_score",
        "sort_reverse": True,
    },
    "rsi_ranked": {
        "label": "Lowest RSI(14), no threshold",
        "description": "Force-rank the lowest RSI(14) names even when RSI is not oversold.",
        "score_key": "rsi_score",
        "sort_reverse": True,
    },
    "bollinger_ranked": {
        "label": "Lowest Bollinger %B, no threshold",
        "description": "Force-rank the lowest Bollinger %B names even without a lower-band touch.",
        "score_key": "bollinger_score",
        "sort_reverse": True,
    },
    "momentum_20d": {
        "label": "Highest 20-day momentum",
        "description": "Long the strongest 20-day price momentum names after the same risk filters.",
        "score_key": "momentum_score",
        "sort_reverse": True,
    },
    "vol_adjusted_momentum": {
        "label": "Volatility-adjusted momentum",
        "description": "Long the strongest 20-day momentum per unit of 20-day volatility.",
        "score_key": "vol_adjusted_momentum_score",
        "sort_reverse": True,
    },
}


def run_technical_baseline(
    *,
    data_dir: str | Path | None = None,
    theme: str = "AI",
    theme_key: str = "ai",
    from_date: str = "20230101",
    to_date: str = "20260331",
    rebalance: str = "W",
    top_n: int = 3,
    hold_days: int = 5,
    baseline: str = "rsi_oversold",
    min_history_days: int = 150,
    transaction_cost_bps: float = 15.0,
    slippage_bps: float = 0.0,
    market_impact_bps: float = 0.0,
    portfolio_value_krw: float = 0.0,
    position_value_krw: float = 0.0,
    max_position_pct_avg_trading_value: float = 0.0,
    min_avg_trading_value: float = 0.0,
    max_volatility_20d: float = 0.0,
    max_return_5d: float = 0.0,
    max_return_20d: float = 0.0,
    min_trend_150d: float = -1.0,
    min_market_breadth_pct: float = 0.0,
    stop_loss_pct: float = 0.0,
    take_profit_pct: float = 0.0,
    trailing_stop_pct: float = 0.0,
    output_dir: str | Path | None = None,
    task_id: str = "",
) -> Dict[str, Any]:
    if baseline not in BASELINE_SPECS:
        raise ValueError(f"unknown baseline: {baseline}")

    data_root = Path(data_dir) if data_dir else get_data_dir()
    from_ymd = _require_ymd(from_date, "from_date")
    to_ymd = _require_ymd(to_date, "to_date")
    run_id = task_id or f"bt-{theme_key}-{from_ymd}-{to_ymd}-{baseline}-top{top_n}-h{hold_days}"
    risk_config = RiskConfig(
        min_avg_trading_value=min_avg_trading_value,
        position_value_krw=_effective_position_value(
            portfolio_value_krw=portfolio_value_krw,
            position_value_krw=position_value_krw,
            top_n=top_n,
        ),
        max_position_pct_avg_trading_value=max_position_pct_avg_trading_value,
        max_volatility_20d=max_volatility_20d,
        max_return_5d=max_return_5d,
        max_return_20d=max_return_20d,
        min_trend_150d=min_trend_150d,
        min_market_breadth_pct=min_market_breadth_pct,
    )
    exit_config = ExitConfig(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
    )

    targets = load_targets(data_root, theme_key)
    prices = load_price_history(data_root, theme_key)
    docs = load_document_signals(data_root, theme_key)
    memberships = load_theme_memberships(data_root, theme_key)
    if not targets:
        targets = [
            StockTarget(stock_name=name, stock_code=code)
            for code, name in sorted(_stock_names_from_prices(prices).items())
        ]
    if not prices:
        raise ValueError(f"price history not found: theme_key={theme_key}")

    target_by_code = {target.stock_code: target for target in targets}
    if memberships:
        membership_codes = {row.stock_code for row in memberships}
        target_by_code = {code: target for code, target in target_by_code.items() if code in membership_codes}
    common_calendar = _build_common_calendar(prices, from_ymd, to_ymd, hold_days)
    rebalance_dates = _select_rebalance_dates(common_calendar, rebalance)
    doc_index = _index_docs(docs)
    if not rebalance_dates:
        raise ValueError(f"no rebalance dates in period: {from_ymd}..{to_ymd}")

    positions: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    period_rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    risk_reject_counts: Dict[str, int] = defaultdict(int)
    signal_skip_counts: Dict[str, int] = defaultdict(int)
    equity = 1.0
    benchmark_equity = 1.0
    round_trip_cost = _round_trip_cost_return(
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        market_impact_bps=market_impact_bps,
    )

    for as_of_ymd in rebalance_dates:
        active_targets = _active_target_by_code(target_by_code, memberships, as_of_ymd)
        if len(active_targets) < top_n:
            warnings.append(f"{as_of_ymd}: active theme universe {len(active_targets)} < top_n {top_n}")
            continue

        scored = _score_universe(
            as_of_ymd=as_of_ymd,
            target_by_code=active_targets,
            prices=prices,
            doc_index=doc_index,
            hold_days=hold_days,
            min_history_days=min_history_days,
            exit_config=exit_config,
        )
        eligible = [row for row in scored if row.get("eligible")]
        if len(eligible) < top_n:
            warnings.append(f"{as_of_ymd}: eligible stocks {len(eligible)} < top_n {top_n}")
            continue

        benchmark_return = float(np.mean([row["realized_return"] for row in eligible]))
        benchmark_net_return = benchmark_return - round_trip_cost
        market_breadth_pct = _market_breadth_pct(eligible)
        filtered, rejected = _apply_risk_filters(eligible, risk_config)
        _add_counts(risk_reject_counts, rejected)
        risk_off_reason = _market_filter_reason(eligible, risk_config)
        if not risk_off_reason and len(filtered) < top_n:
            risk_off_reason = f"risk_filters eligible stocks {len(filtered)} < top_n {top_n}"

        if not risk_off_reason:
            candidates = _technical_candidates(
                baseline=baseline,
                rows=filtered,
                prices=prices,
                as_of_ymd=as_of_ymd,
            )
            if len(candidates) < top_n:
                signal_skip_counts["insufficient_signal"] += 1
                risk_off_reason = f"{baseline} signal stocks {len(candidates)} < top_n {top_n}"
        else:
            candidates = []

        if risk_off_reason:
            warnings.append(f"{as_of_ymd}: {risk_off_reason}")
            benchmark_equity *= 1.0 + benchmark_net_return
            period_rows.append(
                _period_payload(
                    as_of_ymd=as_of_ymd,
                    selected=[],
                    eligible=eligible,
                    filtered=filtered,
                    market_breadth_pct=market_breadth_pct,
                    benchmark_net_return=benchmark_net_return,
                    risk_off_reason=risk_off_reason,
                    active_target_count=len(active_targets),
                )
            )
            equity_curve.append(
                {
                    "date": _fmt_ymd(as_of_ymd),
                    "equity": round(equity, 6),
                    "benchmark_equity": round(benchmark_equity, 6),
                    "period_return_pct": 0.0,
                    "benchmark_return_pct": _pct(benchmark_net_return),
                    "risk_off_reason": risk_off_reason,
                }
            )
            continue

        selected = candidates[:top_n]
        selected_return = float(np.mean([row["realized_return"] for row in selected]))
        selected_net_return = selected_return - round_trip_cost
        portfolio_exit_date = _latest_exit_date(selected)
        equity *= 1.0 + selected_net_return
        benchmark_equity *= 1.0 + benchmark_net_return

        period_rows.append(
            _period_payload(
                as_of_ymd=as_of_ymd,
                selected=selected,
                eligible=eligible,
                filtered=filtered,
                market_breadth_pct=market_breadth_pct,
                benchmark_net_return=benchmark_net_return,
                risk_off_reason="",
                active_target_count=len(active_targets),
                selected_net_return=selected_net_return,
                portfolio_exit_date=portfolio_exit_date,
            )
        )
        equity_curve.append(
            {
                "date": portfolio_exit_date,
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
            position["leader_score"] = round(float(position.pop("technical_score")))
            position["predicted_return"] = _technical_predicted_return(position, baseline)
            position["target_price"] = position["entry_price"] * (1.0 + position["predicted_return"])
            position["realized_return_pct"] = _pct(position.pop("realized_return"))
            position["predicted_return_pct"] = _pct(position.pop("predicted_return"))
            position["target_price"] = round(float(position["target_price"]), 2)
            position["entry_price"] = round(float(position["entry_price"]), 2)
            position["exit_price"] = round(float(position["exit_price"]), 2)
            position.pop("eligible", None)
            positions.append(position)

    metrics = _compute_metrics(
        equity_curve=equity_curve,
        positions=positions,
        from_ymd=from_ymd,
        to_ymd=to_ymd,
        hold_days=hold_days,
    )
    spec = BASELINE_SPECS[baseline]
    result = {
        "task_id": run_id,
        "mode": "backtest",
        "result_type": "backtest",
        "status": "completed",
        "theme": theme,
        "theme_key": theme_key,
        "prediction_model": f"{baseline}_technical_baseline_v1",
        "period": {
            "from_date": _fmt_ymd(from_ymd),
            "to_date": _fmt_ymd(to_ymd),
            "rebalance": rebalance,
            "rebalance_count": len(period_rows),
            "hold_days": hold_days,
        },
        "strategy": {
            "name": baseline,
            "label": spec["label"],
            "description": spec["description"],
            "top_n": top_n,
            "min_history_days": min_history_days,
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "market_impact_bps": market_impact_bps,
            "round_trip_cost_bps": _round_trip_cost_bps(
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
                market_impact_bps=market_impact_bps,
            ),
            "risk_filters": risk_config.to_dict(),
            "exit_rules": exit_config.to_dict(),
            "selection_inputs": _selection_inputs(baseline),
            "prediction_model": f"{baseline}_technical_baseline_v1",
        },
        "metrics": metrics,
        "leaders": _aggregate_leaders(positions),
        "predictions": [
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
            | _optional_technical_payload(row)
            for row in positions
        ],
        "positions": positions,
        "trades": _positions_to_trades(positions),
        "periods": period_rows,
        "equity_curve": equity_curve,
        "risk": {
            "filters": risk_config.to_dict(),
            "reject_counts": dict(risk_reject_counts),
        },
        "execution": {
            "exit_rules": exit_config.to_dict(),
            "costs": {
                "transaction_cost_bps": transaction_cost_bps,
                "slippage_bps": slippage_bps,
                "market_impact_bps": market_impact_bps,
                "round_trip_cost_bps": _round_trip_cost_bps(
                    transaction_cost_bps=transaction_cost_bps,
                    slippage_bps=slippage_bps,
                    market_impact_bps=market_impact_bps,
                ),
            },
            "exit_counts": _exit_counts(positions),
            "same_day_ohlc_policy": "stop_or_trailing_stop_before_take_profit",
        },
        "artifacts": {},
        "warnings": warnings + _default_warnings(bool(memberships)),
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "data_dir": _display_path(data_root),
            "target_count": len(targets),
            "membership_count": len(memberships),
            "point_in_time_universe": bool(memberships),
            "membership_sources": sorted({row.source for row in memberships}),
            "price_stock_count": len(prices),
            "document_signal_count": len(docs),
            "signal_skip_counts": dict(signal_skip_counts),
        },
    }
    out_path = _write_result(result, output_dir or data_root / "backtest_results" / "technical_baselines", run_id)
    result["artifacts"]["result_json"] = _display_path(out_path)
    _write_result(result, output_dir or data_root / "backtest_results" / "technical_baselines", run_id)
    return result


def run_baseline_sweep(
    *,
    data_dir: str | Path | None = None,
    theme: str = "AI",
    theme_key: str = "ai",
    output_dir: str | Path | None = None,
    periods: List[Dict[str, str]] | None = None,
    baselines: List[str] | None = None,
    rebalance: str = "W",
    top_n: int = 3,
    hold_days: int = 5,
    min_history_days: int = 150,
    transaction_cost_bps: float = 15.0,
    slippage_bps: float = 0.0,
    market_impact_bps: float = 0.0,
    portfolio_value_krw: float = 0.0,
    position_value_krw: float = 0.0,
    max_position_pct_avg_trading_value: float = 0.0,
    min_avg_trading_value: float = 0.0,
    max_volatility_20d: float = 0.0,
    max_return_5d: float = 0.0,
    max_return_20d: float = 0.0,
    min_trend_150d: float = -1.0,
    min_market_breadth_pct: float = 0.0,
    stop_loss_pct: float = 0.0,
    take_profit_pct: float = 0.0,
    trailing_stop_pct: float = 0.0,
) -> Dict[str, Any]:
    data_root = Path(data_dir) if data_dir else get_data_dir()
    out_dir = Path(output_dir) if output_dir else data_root / "backtest_results" / "technical_baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    periods = periods or _default_periods()
    baselines = baselines or ["rsi_oversold", "bollinger_lower"]

    rows: List[Dict[str, Any]] = []
    for period in periods:
        for baseline in baselines:
            task_id = f"bt-{theme_key}-{period['name']}-{baseline}-{rebalance.lower()}-top{top_n}-h{hold_days}"
            print(f"[BASELINE] {task_id}", flush=True)
            result = run_technical_baseline(
                data_dir=data_root,
                theme=theme,
                theme_key=theme_key,
                from_date=period["from_date"],
                to_date=period["to_date"],
                rebalance=rebalance,
                top_n=top_n,
                hold_days=hold_days,
                baseline=baseline,
                min_history_days=min_history_days,
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
                market_impact_bps=market_impact_bps,
                portfolio_value_krw=portfolio_value_krw,
                position_value_krw=position_value_krw,
                max_position_pct_avg_trading_value=max_position_pct_avg_trading_value,
                min_avg_trading_value=min_avg_trading_value,
                max_volatility_20d=max_volatility_20d,
                max_return_5d=max_return_5d,
                max_return_20d=max_return_20d,
                min_trend_150d=min_trend_150d,
                min_market_breadth_pct=min_market_breadth_pct,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                trailing_stop_pct=trailing_stop_pct,
                output_dir=out_dir,
                task_id=task_id,
            )
            metrics = result["metrics"]
            rows.append(
                {
                    "task_id": task_id,
                    "baseline": baseline,
                    "baseline_label": result["strategy"]["label"],
                    "period": period["name"],
                    "from_date": result["period"]["from_date"],
                    "to_date": result["period"]["to_date"],
                    "rebalance": rebalance,
                    "top_n": top_n,
                    "hold_days": hold_days,
                    "rebalance_count": metrics.get("rebalance_count", 0),
                    "risk_off_count": metrics.get("risk_off_count", 0),
                    "traded_rebalance_count": metrics.get("traded_rebalance_count", 0),
                    "position_count": metrics.get("position_count", 0),
                    "total_return_pct": metrics.get("total_return_pct", 0.0),
                    "benchmark_return_pct": metrics.get("benchmark_return_pct", 0.0),
                    "excess_return_pct": metrics.get("excess_return_pct", 0.0),
                    "mdd_pct": metrics.get("mdd_pct", 0.0),
                    "sharpe": metrics.get("sharpe", 0.0),
                    "win_rate_pct": metrics.get("win_rate_pct", 0.0),
                    "prediction_hit_rate_pct": metrics.get("prediction_hit_rate_pct", 0.0),
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct,
                    "trailing_stop_pct": trailing_stop_pct,
                    "slippage_bps": slippage_bps,
                    "market_impact_bps": market_impact_bps,
                    "round_trip_cost_bps": _round_trip_cost_bps(
                        transaction_cost_bps=transaction_cost_bps,
                        slippage_bps=slippage_bps,
                        market_impact_bps=market_impact_bps,
                    ),
                    "result_json": result["artifacts"].get("result_json", ""),
                }
            )

    rows.sort(key=lambda row: (row["period"], row["baseline"]))
    summary = {
        "theme": theme,
        "theme_key": theme_key,
        "generated_at": datetime.now().isoformat(),
        "baselines": baselines,
        "periods": periods,
        "rebalance": rebalance,
        "top_n": top_n,
        "hold_days": hold_days,
        "risk_filters": RiskConfig(
            min_avg_trading_value=min_avg_trading_value,
            position_value_krw=_effective_position_value(
                portfolio_value_krw=portfolio_value_krw,
                position_value_krw=position_value_krw,
                top_n=top_n,
            ),
            max_position_pct_avg_trading_value=max_position_pct_avg_trading_value,
            max_volatility_20d=max_volatility_20d,
            max_return_5d=max_return_5d,
            max_return_20d=max_return_20d,
            min_trend_150d=min_trend_150d,
            min_market_breadth_pct=min_market_breadth_pct,
        ).to_dict(),
        "exit_rules": ExitConfig(
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
        ).to_dict(),
        "execution_costs": {
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "market_impact_bps": market_impact_bps,
            "round_trip_cost_bps": _round_trip_cost_bps(
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
                market_impact_bps=market_impact_bps,
            ),
        },
        "rows": rows,
    }
    json_path = out_dir / f"technical-baselines-{theme_key}.json"
    csv_path = out_dir / f"technical-baselines-{theme_key}.csv"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    _write_csv(csv_path, rows)
    summary["artifacts"] = {
        "summary_json": _display_path(json_path),
        "summary_csv": _display_path(csv_path),
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def _technical_candidates(
    *,
    baseline: str,
    rows: List[Dict[str, Any]],
    prices: Dict[str, pd.DataFrame],
    as_of_ymd: str,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        df = prices.get(str(row.get("stock_code") or ""))
        if df is None or df.empty:
            continue
        features = _technical_features(df, as_of_ymd)
        if not features:
            continue
        current = dict(row)
        current.update(features)
        if not _passes_signal(baseline, current):
            continue
        current["technical_score"] = _technical_score(baseline, current)
        candidates.append(current)

    score_key = "technical_score"
    candidates.sort(
        key=lambda item: (
            float(item.get(score_key) or 0.0),
            -float(item.get("volatility_20d") or 0.0),
            float(item.get("avg_trading_value_20d") or 0.0),
        ),
        reverse=True,
    )
    return candidates


def _technical_features(df: pd.DataFrame, as_of_ymd: str) -> Dict[str, float]:
    ymd_index = df["YMD"] if "YMD" in df else pd.Series(df.index.strftime("%Y%m%d"), index=df.index)
    known = df.loc[ymd_index <= as_of_ymd]
    if len(known) < 20:
        return {}
    closes = known["Close"].astype(float)
    close = float(closes.iloc[-1])
    rsi = _rsi(closes, period=14)
    middle = float(closes.tail(20).mean())
    std = float(closes.tail(20).std(ddof=0))
    upper = middle + 2.0 * std
    lower = middle - 2.0 * std
    width = upper - lower
    percent_b = (close - lower) / width if width > 0 else 0.5
    return {
        "rsi_14": rsi,
        "bb_middle_20": middle,
        "bb_upper_20": upper,
        "bb_lower_20": lower,
        "bb_percent_b": percent_b,
    }


def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0).tail(period).mean()
    loss = (-delta.clip(upper=0)).tail(period).mean()
    if not math.isfinite(float(loss)) or float(loss) <= 0:
        return 100.0
    rs = float(gain) / float(loss)
    return 100.0 - (100.0 / (1.0 + rs))


def _passes_signal(baseline: str, row: Dict[str, Any]) -> bool:
    if baseline == "rsi_oversold":
        return float(row.get("rsi_14") or 100.0) <= 30.0
    if baseline == "bollinger_lower":
        return float(row.get("bb_percent_b") or 1.0) <= 0.0
    if baseline in {"rsi_ranked", "bollinger_ranked", "momentum_20d", "vol_adjusted_momentum"}:
        return True
    raise ValueError(f"unknown baseline: {baseline}")


def _technical_score(baseline: str, row: Dict[str, Any]) -> float:
    if baseline.startswith("rsi"):
        rsi = float(row.get("rsi_14") or 100.0)
        return max(0.0, 100.0 - rsi)
    if baseline.startswith("bollinger"):
        percent_b = float(row.get("bb_percent_b") or 1.0)
        return max(0.0, (1.0 - percent_b) * 100.0)
    if baseline == "momentum_20d":
        return float(row.get("return_20d") or 0.0) * 100.0
    if baseline == "vol_adjusted_momentum":
        volatility = max(0.05, float(row.get("volatility_20d") or 0.0))
        return float(row.get("return_20d") or 0.0) / volatility * 100.0
    raise ValueError(f"unknown baseline: {baseline}")


def _technical_predicted_return(row: Dict[str, Any], baseline: str) -> float:
    if baseline.startswith("rsi"):
        rsi = float(row.get("rsi_14") or 50.0)
        return min(0.15, max(0.0, (30.0 - rsi) / 100.0))
    if baseline.startswith("bollinger"):
        percent_b = float(row.get("bb_percent_b") or 0.5)
        return min(0.15, max(0.0, -percent_b * 0.2))
    if baseline == "vol_adjusted_momentum":
        volatility = max(0.05, float(row.get("volatility_20d") or 0.0))
        return _clip(float(row.get("return_20d") or 0.0) / volatility, -0.2, 0.2)
    return _clip(float(row.get("return_20d") or 0.0), -0.2, 0.2)


def _period_payload(
    *,
    as_of_ymd: str,
    selected: List[Dict[str, Any]],
    eligible: List[Dict[str, Any]],
    filtered: List[Dict[str, Any]],
    market_breadth_pct: float,
    benchmark_net_return: float,
    risk_off_reason: str,
    active_target_count: int,
    selected_net_return: float = 0.0,
    portfolio_exit_date: str = "",
) -> Dict[str, Any]:
    if not selected:
        return {
            "as_of_date": _fmt_ymd(as_of_ymd),
            "entry_date": "",
            "exit_date": "",
            "selected_count": 0,
            "active_target_count": active_target_count,
            "eligible_count": len(eligible),
            "risk_eligible_count": len(filtered),
            "risk_reject_count": len(eligible) - len(filtered),
            "market_breadth_pct": market_breadth_pct,
            "portfolio_return_pct": 0.0,
            "benchmark_return_pct": _pct(benchmark_net_return),
            "excess_return_pct": _pct(-benchmark_net_return),
            "risk_off_reason": risk_off_reason,
            "top_symbols": [],
        }
    return {
        "as_of_date": _fmt_ymd(as_of_ymd),
        "entry_date": selected[0]["entry_date"],
        "exit_date": portfolio_exit_date,
        "selected_count": len(selected),
        "active_target_count": active_target_count,
        "eligible_count": len(eligible),
        "risk_eligible_count": len(filtered),
        "risk_reject_count": len(eligible) - len(filtered),
        "market_breadth_pct": market_breadth_pct,
        "portfolio_return_pct": _pct(selected_net_return),
        "benchmark_return_pct": _pct(benchmark_net_return),
        "excess_return_pct": _pct(selected_net_return - benchmark_net_return),
        "top_symbols": [_top_symbol_payload(row) for row in selected],
    }


def _top_symbol_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stock_name": row["stock_name"],
        "stock_code": row["stock_code"],
        "technical_score": round(float(row.get("technical_score") or 0.0), 2),
        "rsi_14": round(float(row.get("rsi_14") or 0.0), 2),
        "bb_percent_b": round(float(row.get("bb_percent_b") or 0.0), 4),
        "realized_return_pct": _pct(row["realized_return"]),
    }


def _optional_technical_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: row[key]
        for key in [
            "rsi_14",
            "bb_middle_20",
            "bb_upper_20",
            "bb_lower_20",
            "bb_percent_b",
        ]
        if key in row
    }


def _selection_inputs(baseline: str) -> List[str]:
    if baseline.startswith("rsi"):
        return ["RSI(14)", "same point-in-time AI universe", "same risk filters"]
    if baseline.startswith("bollinger"):
        return ["20-day Bollinger Bands (2 std)", "same point-in-time AI universe", "same risk filters"]
    if baseline == "vol_adjusted_momentum":
        return ["20-day return / 20-day annualized volatility", "same point-in-time AI universe", "same risk filters"]
    return ["20-day return momentum", "same point-in-time AI universe", "same risk filters"]


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _parse_periods(raw: str) -> List[Dict[str, str]]:
    periods: List[Dict[str, str]] = []
    for item in raw.split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"invalid period spec: {item}")
        periods.append({"name": parts[0], "from_date": parts[1], "to_date": parts[2]})
    return periods


def _default_periods() -> List[Dict[str, str]]:
    return [
        {"name": "full", "from_date": "20230101", "to_date": "20260331"},
        {"name": "2023", "from_date": "20230101", "to_date": "20231231"},
        {"name": "2024", "from_date": "20240101", "to_date": "20241231"},
        {"name": "2025", "from_date": "20250101", "to_date": "20251231"},
        {"name": "2026q1", "from_date": "20260101", "to_date": "20260331"},
    ]


def _parse_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RSI and Bollinger Band technical baselines.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--theme", default="AI")
    parser.add_argument("--theme-key", default="ai")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--baseline", choices=sorted(BASELINE_SPECS), default="")
    parser.add_argument("--baselines", default="rsi_oversold,bollinger_lower")
    parser.add_argument("--from-date", default="20230101")
    parser.add_argument("--to-date", default="20260331")
    parser.add_argument("--periods", default="")
    parser.add_argument("--rebalance", default="W")
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--min-history-days", type=int, default=150)
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--market-impact-bps", type=float, default=0.0)
    parser.add_argument("--portfolio-value-krw", type=float, default=0.0)
    parser.add_argument("--position-value-krw", type=float, default=0.0)
    parser.add_argument("--max-position-pct-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--min-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--max-volatility-20d", type=float, default=0.0)
    parser.add_argument("--max-return-5d", type=float, default=0.0)
    parser.add_argument("--max-return-20d", type=float, default=0.0)
    parser.add_argument("--min-trend-150d", type=float, default=-1.0)
    parser.add_argument("--min-market-breadth-pct", type=float, default=0.0)
    parser.add_argument("--stop-loss-pct", type=float, default=0.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.0)
    parser.add_argument("--trailing-stop-pct", type=float, default=0.0)
    parser.add_argument("--task-id", default="")
    args = parser.parse_args()

    out_dir = args.output_dir or str(Path(args.data_dir) / "backtest_results" / "technical_baselines")
    common_kwargs = {
        "data_dir": args.data_dir,
        "theme": args.theme,
        "theme_key": args.theme_key,
        "rebalance": args.rebalance,
        "top_n": args.top_n,
        "hold_days": args.hold_days,
        "min_history_days": args.min_history_days,
        "transaction_cost_bps": args.transaction_cost_bps,
        "slippage_bps": args.slippage_bps,
        "market_impact_bps": args.market_impact_bps,
        "portfolio_value_krw": args.portfolio_value_krw,
        "position_value_krw": args.position_value_krw,
        "max_position_pct_avg_trading_value": args.max_position_pct_avg_trading_value,
        "min_avg_trading_value": args.min_avg_trading_value,
        "max_volatility_20d": args.max_volatility_20d,
        "max_return_5d": args.max_return_5d,
        "max_return_20d": args.max_return_20d,
        "min_trend_150d": args.min_trend_150d,
        "min_market_breadth_pct": args.min_market_breadth_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "take_profit_pct": args.take_profit_pct,
        "trailing_stop_pct": args.trailing_stop_pct,
        "output_dir": out_dir,
    }
    if args.baseline:
        result = run_technical_baseline(
            **common_kwargs,
            baseline=args.baseline,
            from_date=args.from_date,
            to_date=args.to_date,
            task_id=args.task_id,
        )
        print(json.dumps({"task_id": result["task_id"], "metrics": result["metrics"]}, ensure_ascii=False, indent=2))
        return 0

    summary = run_baseline_sweep(
        **common_kwargs,
        periods=_parse_periods(args.periods) if args.periods else _default_periods(),
        baselines=_parse_list(args.baselines),
    )
    print(json.dumps(summary["artifacts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
