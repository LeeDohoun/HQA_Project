#!/usr/bin/env python3
from __future__ import annotations

"""Run fixed short/long proof backtests and write a validation report."""

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.leader_backtest import run_leader_backtest
from src.config.settings import get_data_dir


BacktestRunner = Callable[..., Dict[str, Any]]


@dataclass(frozen=True)
class PeriodSpec:
    name: str
    from_date: str
    to_date: str
    role: str = "validation"


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    label: str
    horizon: str
    rebalance: str
    top_n: int
    hold_days: int
    llm_enabled: bool
    llm_weight: float = 0.0
    llm_mode: str = "multi_agent"
    llm_candidate_scope: str = "broad"
    llm_rerank_top_k: int = 10
    llm_context_docs: int = 3
    is_baseline: bool = False


def run_proof_validation(
    *,
    data_dir: str | Path,
    theme: str,
    theme_key: str,
    output_dir: str | Path,
    periods: List[PeriodSpec],
    strategies: List[StrategySpec],
    min_history_days: int = 150,
    transaction_cost_bps: float = 15.0,
    slippage_bps: float = 0.0,
    market_impact_bps: float = 0.0,
    portfolio_value_krw: float = 0.0,
    position_value_krw: float = 0.0,
    max_position_pct_avg_trading_value: float = 0.0,
    min_avg_trading_value: float = 0.0,
    max_volatility_20d: float = 1.2,
    max_return_5d: float = 0.35,
    max_return_20d: float = 0.9,
    min_trend_150d: float = -1.0,
    min_market_breadth_pct: float = 40.0,
    stop_loss_pct: float = 0.0,
    take_profit_pct: float = 0.0,
    trailing_stop_pct: float = 15.0,
    submit_url: str = "",
    resume_completed: bool = True,
    runner: BacktestRunner = run_leader_backtest,
) -> Dict[str, Any]:
    out_dir = Path(output_dir)
    runs_dir = out_dir / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    total = len(periods) * len(strategies)
    completed = 0

    for period in periods:
        for strategy in strategies:
            completed += 1
            task_id = _task_id(theme_key, period, strategy)
            result_path = runs_dir / f"{task_id}.json"
            if resume_completed:
                cached_result = _load_completed_result(result_path, task_id)
                if cached_result is not None:
                    print(f"[PROOF] {completed}/{total} {task_id} RESUME", flush=True)
                    results.append(cached_result)
                    rows.append(_result_row(cached_result, period=period, strategy=strategy))
                    continue

            print(f"[PROOF] {completed}/{total} {task_id} RUN", flush=True)
            kwargs = _backtest_kwargs(
                data_dir=data_dir,
                theme=theme,
                theme_key=theme_key,
                period=period,
                strategy=strategy,
                output_dir=runs_dir,
                task_id=task_id,
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
                submit_url=submit_url,
            )
            try:
                result = runner(**kwargs)
            except ValueError as exc:
                if "no rebalance dates in period" not in str(exc):
                    raise
                result = _skipped_no_rebalance_result(
                    data_dir=data_dir,
                    theme=theme,
                    theme_key=theme_key,
                    period=period,
                    strategy=strategy,
                    task_id=task_id,
                    result_path=result_path,
                    error=str(exc),
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
                )
            results.append(result)
            rows.append(_result_row(result, period=period, strategy=strategy))

    rows = _attach_baseline_deltas(rows)
    summary = _build_summary(
        theme=theme,
        theme_key=theme_key,
        data_dir=data_dir,
        output_dir=out_dir,
        periods=periods,
        strategies=strategies,
        rows=rows,
        protocol={
            "min_history_days": min_history_days,
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "market_impact_bps": market_impact_bps,
            "round_trip_cost_bps": (transaction_cost_bps + slippage_bps + market_impact_bps) * 2.0,
            "portfolio_value_krw": portfolio_value_krw,
            "position_value_krw": position_value_krw,
            "max_position_pct_avg_trading_value": max_position_pct_avg_trading_value,
            "min_avg_trading_value": min_avg_trading_value,
            "max_volatility_20d": max_volatility_20d,
            "max_return_5d": max_return_5d,
            "max_return_20d": max_return_20d,
            "min_trend_150d": min_trend_150d,
            "min_market_breadth_pct": min_market_breadth_pct,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
        },
    )
    _write_artifacts(summary, rows, results, out_dir)
    return summary


def _load_completed_result(path: Path, task_id: str) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    if result.get("task_id") != task_id:
        return None
    if not isinstance(result.get("metrics"), dict):
        return None
    return result


def _skipped_no_rebalance_result(
    *,
    data_dir: str | Path,
    theme: str,
    theme_key: str,
    period: PeriodSpec,
    strategy: StrategySpec,
    task_id: str,
    result_path: Path,
    error: str,
    min_history_days: int,
    transaction_cost_bps: float,
    slippage_bps: float,
    market_impact_bps: float,
    portfolio_value_krw: float,
    position_value_krw: float,
    max_position_pct_avg_trading_value: float,
    min_avg_trading_value: float,
    max_volatility_20d: float,
    max_return_5d: float,
    max_return_20d: float,
    min_trend_150d: float,
    min_market_breadth_pct: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
) -> Dict[str, Any]:
    result = {
        "task_id": task_id,
        "mode": "backtest",
        "result_type": "backtest",
        "status": "skipped",
        "skip_reason": "no_evaluable_rebalance_dates",
        "theme": theme,
        "theme_key": theme_key,
        "prediction_model": "skipped_no_evaluable_rebalance",
        "period": {
            "from_date": _fmt_period_date(period.from_date),
            "to_date": _fmt_period_date(period.to_date),
            "rebalance": strategy.rebalance,
            "rebalance_count": 0,
            "hold_days": strategy.hold_days,
        },
        "strategy": {
            "name": "ai_theme_leader_momentum_v1",
            "top_n": strategy.top_n,
            "min_history_days": min_history_days,
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "market_impact_bps": market_impact_bps,
            "round_trip_cost_bps": (transaction_cost_bps + slippage_bps + market_impact_bps) * 2.0,
            "risk_filters": {
                "min_avg_trading_value": min_avg_trading_value,
                "portfolio_value_krw": portfolio_value_krw,
                "position_value_krw": position_value_krw,
                "max_position_pct_avg_trading_value": max_position_pct_avg_trading_value,
                "max_volatility_20d": max_volatility_20d,
                "max_return_5d": max_return_5d,
                "max_return_20d": max_return_20d,
                "min_trend_150d": min_trend_150d,
                "min_market_breadth_pct": min_market_breadth_pct,
            },
            "exit_rules": {
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "trailing_stop_pct": trailing_stop_pct,
            },
            "llm_rerank": {
                "enabled": strategy.llm_enabled,
                "top_k": strategy.llm_rerank_top_k if strategy.llm_enabled else 0,
                "weight": strategy.llm_weight if strategy.llm_enabled else 0.0,
                "context_docs": strategy.llm_context_docs if strategy.llm_enabled else 0,
                "mode": strategy.llm_mode if strategy.llm_enabled else "",
                "candidate_scope": strategy.llm_candidate_scope if strategy.llm_enabled else "",
                "horizon": strategy.horizon if strategy.llm_enabled else "",
                "top_k_meaning": "deterministic_prefilter" if strategy.llm_enabled else "",
            },
            "selection_inputs": [],
            "prediction_model": "skipped_no_evaluable_rebalance",
        },
        "metrics": _empty_metrics(),
        "leaders": [],
        "predictions": [],
        "positions": [],
        "trades": [],
        "periods": [],
        "equity_curve": [],
        "risk": {"filters": {}, "reject_counts": {}},
        "execution": {
            "exit_rules": {
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "trailing_stop_pct": trailing_stop_pct,
            },
            "costs": {
                "transaction_cost_bps": transaction_cost_bps,
                "slippage_bps": slippage_bps,
                "market_impact_bps": market_impact_bps,
                "round_trip_cost_bps": (transaction_cost_bps + slippage_bps + market_impact_bps) * 2.0,
            },
            "exit_counts": {},
        },
        "artifacts": {"result_json": str(result_path)},
        "warnings": [error],
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "data_dir": str(data_dir),
            "llm": {},
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def _empty_metrics() -> Dict[str, Any]:
    return {
        "rebalance_count": 0,
        "risk_off_count": 0,
        "traded_rebalance_count": 0,
        "position_count": 0,
        "total_return_pct": 0.0,
        "benchmark_return_pct": 0.0,
        "excess_return_pct": 0.0,
        "cagr_pct": 0.0,
        "benchmark_cagr_pct": 0.0,
        "mdd_pct": 0.0,
        "benchmark_mdd_pct": 0.0,
        "sharpe": 0.0,
        "avg_period_return_pct": 0.0,
        "avg_benchmark_period_return_pct": 0.0,
        "win_rate_pct": 0.0,
        "prediction_hit_rate_pct": 0.0,
        "avg_position_return_pct": 0.0,
        "median_position_return_pct": 0.0,
        "loss_period_count": 0,
        "max_consecutive_loss_periods": 0,
        "worst_period_return_pct": 0.0,
        "best_period_return_pct": 0.0,
    }


def _fmt_period_date(value: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _backtest_kwargs(
    *,
    data_dir: str | Path,
    theme: str,
    theme_key: str,
    period: PeriodSpec,
    strategy: StrategySpec,
    output_dir: Path,
    task_id: str,
    min_history_days: int,
    transaction_cost_bps: float,
    slippage_bps: float,
    market_impact_bps: float,
    portfolio_value_krw: float,
    position_value_krw: float,
    max_position_pct_avg_trading_value: float,
    min_avg_trading_value: float,
    max_volatility_20d: float,
    max_return_5d: float,
    max_return_20d: float,
    min_trend_150d: float,
    min_market_breadth_pct: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
    submit_url: str,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "data_dir": data_dir,
        "theme": theme,
        "theme_key": theme_key,
        "from_date": period.from_date,
        "to_date": period.to_date,
        "rebalance": strategy.rebalance,
        "top_n": strategy.top_n,
        "hold_days": strategy.hold_days,
        "min_history_days": min_history_days,
        "transaction_cost_bps": transaction_cost_bps,
        "slippage_bps": slippage_bps,
        "market_impact_bps": market_impact_bps,
        "portfolio_value_krw": portfolio_value_krw,
        "position_value_krw": position_value_krw,
        "max_position_pct_avg_trading_value": max_position_pct_avg_trading_value,
        "min_avg_trading_value": min_avg_trading_value,
        "max_volatility_20d": max_volatility_20d,
        "max_return_5d": max_return_5d,
        "max_return_20d": max_return_20d,
        "min_trend_150d": min_trend_150d,
        "min_market_breadth_pct": min_market_breadth_pct,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "output_dir": output_dir,
        "task_id": task_id,
        "submit_url": submit_url,
    }
    if strategy.llm_enabled:
        kwargs.update(
            {
                "llm_rerank_top_k": strategy.llm_rerank_top_k,
                "llm_weight": strategy.llm_weight,
                "llm_context_docs": strategy.llm_context_docs,
                "llm_mode": strategy.llm_mode,
                "llm_candidate_scope": strategy.llm_candidate_scope,
                "llm_horizon": strategy.horizon,
            }
        )
    return kwargs


def _result_row(result: Dict[str, Any], *, period: PeriodSpec, strategy: StrategySpec) -> Dict[str, Any]:
    metrics = result.get("metrics") or {}
    positions = result.get("positions") or []
    periods = result.get("periods") or []
    selected_names = [str(row.get("stock_name") or "") for row in positions if row.get("stock_name")]
    selected_codes = [str(row.get("stock_code") or "") for row in positions if row.get("stock_code")]
    return {
        "period": period.name,
        "period_role": period.role,
        "from_date": result.get("period", {}).get("from_date", period.from_date),
        "to_date": result.get("period", {}).get("to_date", period.to_date),
        "strategy_id": strategy.strategy_id,
        "strategy_label": strategy.label,
        "horizon": strategy.horizon,
        "is_baseline": strategy.is_baseline,
        "task_id": result.get("task_id", ""),
        "prediction_model": result.get("prediction_model", ""),
        "rebalance": strategy.rebalance,
        "top_n": strategy.top_n,
        "hold_days": strategy.hold_days,
        "llm_enabled": strategy.llm_enabled,
        "llm_weight": strategy.llm_weight if strategy.llm_enabled else 0.0,
        "llm_mode": strategy.llm_mode if strategy.llm_enabled else "",
        "llm_candidate_scope": strategy.llm_candidate_scope if strategy.llm_enabled else "",
        "llm_rerank_top_k": strategy.llm_rerank_top_k if strategy.llm_enabled else 0,
        "rebalance_count": metrics.get("rebalance_count", 0),
        "traded_rebalance_count": metrics.get("traded_rebalance_count", 0),
        "risk_off_count": metrics.get("risk_off_count", 0),
        "position_count": metrics.get("position_count", 0),
        "total_return_pct": metrics.get("total_return_pct", 0.0),
        "benchmark_return_pct": metrics.get("benchmark_return_pct", 0.0),
        "excess_return_pct": metrics.get("excess_return_pct", 0.0),
        "mdd_pct": metrics.get("mdd_pct", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "win_rate_pct": metrics.get("win_rate_pct", 0.0),
        "prediction_hit_rate_pct": metrics.get("prediction_hit_rate_pct", 0.0),
        "avg_position_return_pct": metrics.get("avg_position_return_pct", 0.0),
        "median_position_return_pct": metrics.get("median_position_return_pct", 0.0),
        "loss_period_count": metrics.get("loss_period_count", 0),
        "max_consecutive_loss_periods": metrics.get("max_consecutive_loss_periods", 0),
        "worst_period_return_pct": metrics.get("worst_period_return_pct", 0.0),
        "best_period_return_pct": metrics.get("best_period_return_pct", 0.0),
        "selected_names": "/".join(selected_names[:10]),
        "selected_codes": "/".join(selected_codes[:10]),
        "period_count": len(periods),
        "result_json": result.get("artifacts", {}).get("result_json", ""),
        "status": result.get("status", ""),
        "skip_reason": result.get("skip_reason", ""),
        "evaluable": int(metrics.get("rebalance_count") or 0) > 0,
    }


def _attach_baseline_deltas(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    baseline_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if row.get("is_baseline"):
            baseline_by_key[(str(row["period"]), str(row["horizon"]))] = row

    output: List[Dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        baseline = baseline_by_key.get((str(row["period"]), str(row["horizon"])))
        if baseline and baseline is not row and row.get("evaluable", True) and baseline.get("evaluable", True):
            current["baseline_strategy_id"] = baseline["strategy_id"]
            current["return_delta_vs_baseline_pct"] = _round2(
                float(row["total_return_pct"]) - float(baseline["total_return_pct"])
            )
            current["excess_delta_vs_baseline_pct"] = _round2(
                float(row["excess_return_pct"]) - float(baseline["excess_return_pct"])
            )
            current["mdd_delta_vs_baseline_pct"] = _round2(float(row["mdd_pct"]) - float(baseline["mdd_pct"]))
            current["win_vs_baseline"] = current["excess_delta_vs_baseline_pct"] > 0
        else:
            current["baseline_strategy_id"] = ""
            current["return_delta_vs_baseline_pct"] = 0.0
            current["excess_delta_vs_baseline_pct"] = 0.0
            current["mdd_delta_vs_baseline_pct"] = 0.0
            current["win_vs_baseline"] = False
        output.append(current)
    return output


def _build_summary(
    *,
    theme: str,
    theme_key: str,
    data_dir: str | Path,
    output_dir: Path,
    periods: List[PeriodSpec],
    strategies: List[StrategySpec],
    rows: List[Dict[str, Any]],
    protocol: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    scorecard = _scorecard(rows)
    return {
        "theme": theme,
        "theme_key": theme_key,
        "generated_at": datetime.now().isoformat(),
        "data_dir": str(data_dir),
        "output_dir": str(output_dir),
        "periods": [asdict(period) for period in periods],
        "strategies": [asdict(strategy) for strategy in strategies],
        "protocol": protocol or {},
        "row_count": len(rows),
        "scorecard": scorecard,
        "best_by_period_horizon": _best_by_period_horizon(rows),
        "rows": rows,
        "artifacts": {},
    }


def _scorecard(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    compared = [
        row
        for row in rows
        if not row.get("is_baseline") and row.get("baseline_strategy_id") and row.get("evaluable", True)
    ]
    by_strategy: Dict[str, List[Dict[str, Any]]] = {}
    by_horizon: Dict[str, List[Dict[str, Any]]] = {}
    for row in compared:
        by_strategy.setdefault(str(row["strategy_id"]), []).append(row)
        by_horizon.setdefault(str(row["horizon"]), []).append(row)

    return {
        "overall": _group_score(compared),
        "by_horizon": {key: _group_score(value) for key, value in sorted(by_horizon.items())},
        "by_strategy": {key: _group_score(value) for key, value in sorted(by_strategy.items())},
    }


def _group_score(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "comparison_count": 0,
            "win_vs_baseline_count": 0,
            "win_vs_baseline_pct": 0.0,
            "avg_excess_delta_vs_baseline_pct": 0.0,
            "avg_return_delta_vs_baseline_pct": 0.0,
            "avg_mdd_delta_vs_baseline_pct": 0.0,
            "avg_total_return_pct": 0.0,
            "avg_excess_return_pct": 0.0,
            "worst_mdd_pct": 0.0,
            "evidence_grade": "insufficient",
        }

    win_count = sum(1 for row in rows if row.get("win_vs_baseline"))
    avg_excess_delta = _mean(row["excess_delta_vs_baseline_pct"] for row in rows)
    avg_return_delta = _mean(row["return_delta_vs_baseline_pct"] for row in rows)
    avg_mdd_delta = _mean(row["mdd_delta_vs_baseline_pct"] for row in rows)
    grade = _evidence_grade(len(rows), win_count, avg_excess_delta, avg_mdd_delta)
    return {
        "comparison_count": len(rows),
        "win_vs_baseline_count": win_count,
        "win_vs_baseline_pct": _round2(win_count / len(rows) * 100.0),
        "avg_excess_delta_vs_baseline_pct": avg_excess_delta,
        "avg_return_delta_vs_baseline_pct": avg_return_delta,
        "avg_mdd_delta_vs_baseline_pct": avg_mdd_delta,
        "avg_total_return_pct": _mean(row["total_return_pct"] for row in rows),
        "avg_excess_return_pct": _mean(row["excess_return_pct"] for row in rows),
        "worst_mdd_pct": min(float(row["mdd_pct"]) for row in rows),
        "evidence_grade": grade,
    }


def _evidence_grade(count: int, win_count: int, avg_excess_delta: float, avg_mdd_delta: float) -> str:
    if count < 3:
        return "pilot"
    win_rate = win_count / count
    if win_rate >= 0.67 and avg_excess_delta > 0 and avg_mdd_delta >= -5.0:
        return "strong"
    if win_rate >= 0.50 and avg_excess_delta > 0:
        return "promising"
    if avg_excess_delta > 0:
        return "mixed_positive"
    return "weak"


def _best_by_period_horizon(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = f"{row['period']}|{row['horizon']}"
        current = best.get(key)
        if current is None or _rank_tuple(row) > _rank_tuple(current):
            best[key] = row
    return best


def _rank_tuple(row: Dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row.get("excess_return_pct") or 0.0),
        float(row.get("sharpe") or 0.0),
        float(row.get("total_return_pct") or 0.0),
        float(row.get("win_rate_pct") or 0.0),
    )


def _write_artifacts(
    summary: Dict[str, Any],
    rows: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    output_dir: Path,
) -> None:
    summary_path = output_dir / "ai_short_long_validation_summary.json"
    csv_path = output_dir / "ai_short_long_validation_summary.csv"
    raw_results_path = output_dir / "ai_short_long_validation_results.json"
    report_path = output_dir / "ai_short_long_validation_report.md"

    summary["artifacts"] = {
        "summary_json": str(summary_path),
        "summary_csv": str(csv_path),
        "raw_results_json": str(raw_results_path),
        "report_md": str(report_path),
    }
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with raw_results_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    _write_csv(csv_path, rows)
    report_path.write_text(_render_report(summary), encoding="utf-8")
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def _render_report(summary: Dict[str, Any]) -> str:
    rows = summary["rows"]
    lines = [
        "# AI Short/Long Backtest Proof Report",
        "",
        f"- Theme: {summary['theme']} ({summary['theme_key']})",
        f"- Generated at: {summary['generated_at']}",
        f"- Row count: {summary['row_count']}",
        "",
        "## Protocol",
        "",
        "This report fixes short/long strategy definitions before comparing them against matching deterministic baselines.",
        "",
        "| Cost/Risk Input | Value |",
        "|---|---:|",
    ]
    for key, value in (summary.get("protocol") or {}).items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
        "| Strategy | Horizon | Rebalance | Hold Days | LLM | Weight | Scope | Top K |",
        "|---|---|---:|---:|---|---:|---|---:|",
        ]
    )
    for strategy in summary["strategies"]:
        lines.append(
            "| {strategy_id} | {horizon} | {rebalance} | {hold_days} | {llm} | {weight} | {scope} | {top_k} |".format(
                strategy_id=strategy["strategy_id"],
                horizon=strategy["horizon"],
                rebalance=strategy["rebalance"],
                hold_days=strategy["hold_days"],
                llm="yes" if strategy["llm_enabled"] else "no",
                weight=strategy["llm_weight"] if strategy["llm_enabled"] else 0.0,
                scope=strategy["llm_candidate_scope"] if strategy["llm_enabled"] else "",
                top_k=strategy["llm_rerank_top_k"] if strategy["llm_enabled"] else 0,
            )
        )

    lines.extend(
        [
            "",
            "## Scorecard",
            "",
            "| Group | Count | Win % | Avg Excess Delta | Avg Return Delta | Avg MDD Delta | Grade |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    lines.append(_scorecard_line("overall", summary["scorecard"]["overall"]))
    for horizon, card in summary["scorecard"]["by_horizon"].items():
        lines.append(_scorecard_line(f"horizon:{horizon}", card))
    for strategy_id, card in summary["scorecard"]["by_strategy"].items():
        lines.append(_scorecard_line(strategy_id, card))

    lines.extend(
        [
            "",
            "## Period Results",
            "",
        "| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Baseline | MDD | Worst Period | Loss Streak | Hit Rate | Picks |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {period} | {horizon} | {strategy} | {status} | {ret:.2f}% | {bench:.2f}% | {excess:.2f}% | {delta:.2f}% | {mdd:.2f}% | {worst:.2f}% | {streak} | {hit:.2f}% | {picks} |".format(
                period=row["period"],
                horizon=row["horizon"],
                strategy=row["strategy_id"],
                status=row.get("skip_reason") or row.get("status") or "completed",
                ret=float(row["total_return_pct"]),
                bench=float(row["benchmark_return_pct"]),
                excess=float(row["excess_return_pct"]),
                delta=float(row["excess_delta_vs_baseline_pct"]),
                mdd=float(row["mdd_pct"]),
                worst=float(row.get("worst_period_return_pct") or 0.0),
                streak=int(row.get("max_consecutive_loss_periods") or 0),
                hit=float(row["prediction_hit_rate_pct"]),
                picks=str(row.get("selected_names") or "")[:120],
            )
        )

    lines.extend(
        [
            "",
            "## Interpretation Rules",
            "",
            "- A result is useful only when it beats the matching deterministic baseline for the same horizon.",
            "- Short-horizon evidence should improve excess return without relying on a single rebalance date.",
            "- Long-horizon evidence should improve excess return while keeping drawdown from deteriorating materially.",
            "- Treat pilot-grade results as directional only; use validation periods for claims.",
            "",
            "## Artifacts",
            "",
        ]
    )
    for key, value in summary["artifacts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _scorecard_line(label: str, card: Dict[str, Any]) -> str:
    return (
        f"| {label} | {card['comparison_count']} | {card['win_vs_baseline_pct']:.2f}% | "
        f"{card['avg_excess_delta_vs_baseline_pct']:.2f}% | "
        f"{card['avg_return_delta_vs_baseline_pct']:.2f}% | "
        f"{card['avg_mdd_delta_vs_baseline_pct']:.2f}% | {card['evidence_grade']} |"
    )


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _task_id(theme_key: str, period: PeriodSpec, strategy: StrategySpec) -> str:
    return f"proof-{theme_key}-{period.name}-{strategy.strategy_id}"


def _mean(values: Iterable[Any]) -> float:
    parsed = [float(value or 0.0) for value in values]
    return _round2(sum(parsed) / len(parsed)) if parsed else 0.0


def _round2(value: float) -> float:
    return round(float(value), 2)


def default_strategies(*, short_top_k: int = 10, long_top_k: int = 10) -> List[StrategySpec]:
    return [
        StrategySpec(
            strategy_id="deterministic_short",
            label="Short deterministic baseline",
            horizon="short",
            rebalance="W",
            top_n=3,
            hold_days=5,
            llm_enabled=False,
            is_baseline=True,
        ),
        StrategySpec(
            strategy_id="short_hybrid_05",
            label="Short multi-agent hybrid",
            horizon="short",
            rebalance="W",
            top_n=3,
            hold_days=5,
            llm_enabled=True,
            llm_weight=0.5,
            llm_rerank_top_k=short_top_k,
        ),
        StrategySpec(
            strategy_id="short_llm_only",
            label="Short multi-agent LLM-only",
            horizon="short",
            rebalance="W",
            top_n=3,
            hold_days=5,
            llm_enabled=True,
            llm_weight=1.0,
            llm_rerank_top_k=short_top_k,
        ),
        StrategySpec(
            strategy_id="deterministic_long",
            label="Long deterministic baseline",
            horizon="long",
            rebalance="M",
            top_n=3,
            hold_days=60,
            llm_enabled=False,
            is_baseline=True,
        ),
        StrategySpec(
            strategy_id="long_hybrid_05",
            label="Long multi-agent hybrid",
            horizon="long",
            rebalance="M",
            top_n=3,
            hold_days=60,
            llm_enabled=True,
            llm_weight=0.5,
            llm_rerank_top_k=long_top_k,
        ),
        StrategySpec(
            strategy_id="long_llm_only",
            label="Long multi-agent LLM-only",
            horizon="long",
            rebalance="M",
            top_n=3,
            hold_days=60,
            llm_enabled=True,
            llm_weight=1.0,
            llm_rerank_top_k=long_top_k,
        ),
    ]


def champion_strategies(*, short_top_k: int = 10, long_top_k: int = 10) -> List[StrategySpec]:
    return select_strategies(
        default_strategies(short_top_k=short_top_k, long_top_k=long_top_k),
        "deterministic_short,short_hybrid_05,deterministic_long,long_hybrid_05",
    )


def _apply_long_hold_days(strategies: List[StrategySpec], hold_days: int) -> List[StrategySpec]:
    output: List[StrategySpec] = []
    for strategy in strategies:
        if strategy.horizon != "long":
            output.append(strategy)
            continue
        output.append(StrategySpec(**{**asdict(strategy), "hold_days": hold_days}))
    return output


def default_periods(preset: str) -> List[PeriodSpec]:
    if preset == "smoke":
        return [PeriodSpec(name="smoke_2026jan", from_date="20260101", to_date="20260116", role="smoke")]
    if preset == "proof":
        return [
            PeriodSpec(name="tune_2025", from_date="20250101", to_date="20251231", role="tuning_reference"),
            PeriodSpec(name="validation_2026q1", from_date="20260101", to_date="20260331", role="validation"),
            PeriodSpec(name="recent_2026apr_may", from_date="20260401", to_date="20260507", role="recent_check"),
        ]
    if preset == "extended":
        return [
            PeriodSpec(name="validation_2023", from_date="20230101", to_date="20231231", role="validation"),
            PeriodSpec(name="validation_2024", from_date="20240101", to_date="20241231", role="validation"),
            PeriodSpec(name="tune_2025", from_date="20250101", to_date="20251231", role="tuning_reference"),
            PeriodSpec(name="validation_2026q1", from_date="20260101", to_date="20260331", role="validation"),
            PeriodSpec(name="recent_2026apr_may", from_date="20260401", to_date="20260507", role="recent_check"),
        ]
    raise ValueError(f"invalid preset: {preset}")


def parse_periods(raw: str) -> List[PeriodSpec]:
    periods: List[PeriodSpec] = []
    for item in raw.split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) not in {3, 4} or not all(parts[:3]):
            raise ValueError(f"invalid period spec: {item}")
        role = parts[3] if len(parts) == 4 and parts[3] else "validation"
        periods.append(PeriodSpec(name=parts[0], from_date=parts[1], to_date=parts[2], role=role))
    return periods


def select_strategies(strategies: List[StrategySpec], raw: str) -> List[StrategySpec]:
    if not raw:
        return strategies
    selected = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = selected - {strategy.strategy_id for strategy in strategies}
    if unknown:
        raise ValueError(f"unknown strategies: {sorted(unknown)}")
    return [strategy for strategy in strategies if strategy.strategy_id in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fixed short/long proof validation backtests.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--theme", default="AI")
    parser.add_argument("--theme-key", default="ai")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--preset", choices=["smoke", "proof", "extended"], default="smoke")
    parser.add_argument(
        "--periods",
        default="",
        help="Comma-separated name:from:to[:role]. Overrides --preset periods.",
    )
    parser.add_argument(
        "--strategies",
        default="",
        help="Comma-separated strategy IDs. Empty runs the fixed proof set.",
    )
    parser.add_argument("--short-top-k", type=int, default=10)
    parser.add_argument("--long-top-k", type=int, default=10)
    parser.add_argument("--long-hold-days", type=int, default=60)
    parser.add_argument(
        "--champion-only",
        action="store_true",
        help="Run the fixed project representative set: short_hybrid_05 and long_hybrid_05 plus matching deterministic baselines.",
    )
    parser.add_argument("--min-history-days", type=int, default=150)
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--market-impact-bps", type=float, default=0.0)
    parser.add_argument("--portfolio-value-krw", type=float, default=0.0)
    parser.add_argument("--position-value-krw", type=float, default=0.0)
    parser.add_argument("--max-position-pct-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--min-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--max-volatility-20d", type=float, default=1.2)
    parser.add_argument("--max-return-5d", type=float, default=0.35)
    parser.add_argument("--max-return-20d", type=float, default=0.9)
    parser.add_argument("--min-trend-150d", type=float, default=-1.0)
    parser.add_argument("--min-market-breadth-pct", type=float, default=40.0)
    parser.add_argument("--stop-loss-pct", type=float, default=0.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.0)
    parser.add_argument("--trailing-stop-pct", type=float, default=15.0)
    parser.add_argument("--submit-url", default="")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run tasks even when runs/{task_id}.json already exists.",
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Use LLM_PROVIDER=mock for fast runner smoke tests without Ollama/Gemini calls.",
    )
    args = parser.parse_args()

    if args.mock_llm:
        os.environ["LLM_PROVIDER"] = "mock"

    output_dir = args.output_dir or str(Path(args.data_dir) / "backtest_results" / "proof" / args.preset)
    strategies = (
        champion_strategies(short_top_k=args.short_top_k, long_top_k=args.long_top_k)
        if args.champion_only
        else default_strategies(short_top_k=args.short_top_k, long_top_k=args.long_top_k)
    )
    strategies = _apply_long_hold_days(strategies, args.long_hold_days)
    strategies = select_strategies(strategies, args.strategies)
    periods = parse_periods(args.periods) if args.periods else default_periods(args.preset)

    summary = run_proof_validation(
        data_dir=args.data_dir,
        theme=args.theme,
        theme_key=args.theme_key,
        output_dir=output_dir,
        periods=periods,
        strategies=strategies,
        min_history_days=args.min_history_days,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
        market_impact_bps=args.market_impact_bps,
        portfolio_value_krw=args.portfolio_value_krw,
        position_value_krw=args.position_value_krw,
        max_position_pct_avg_trading_value=args.max_position_pct_avg_trading_value,
        min_avg_trading_value=args.min_avg_trading_value,
        max_volatility_20d=args.max_volatility_20d,
        max_return_5d=args.max_return_5d,
        max_return_20d=args.max_return_20d,
        min_trend_150d=args.min_trend_150d,
        min_market_breadth_pct=args.min_market_breadth_pct,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        trailing_stop_pct=args.trailing_stop_pct,
        submit_url=args.submit_url,
        resume_completed=not args.no_resume,
    )
    print("[PROOF] summary_json=", summary["artifacts"]["summary_json"])
    print("[PROOF] report_md=", summary["artifacts"]["report_md"])
    print("[PROOF] overall=", summary["scorecard"]["overall"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
