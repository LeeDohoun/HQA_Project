#!/usr/bin/env python3
from __future__ import annotations

"""Run multiple AI-theme leader backtest configurations and rank outcomes."""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

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
    _default_task_id,
    _default_warnings,
    _display_path,
    _exit_counts,
    _fmt_ymd,
    _index_docs,
    _latest_exit_date,
    _market_breadth_pct,
    _market_filter_reason,
    _pct,
    _positions_to_trades,
    _require_ymd,
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


def run_sweep(
    *,
    data_dir: str,
    theme: str,
    theme_key: str,
    output_dir: str,
    periods: List[Dict[str, str]],
    top_ns: List[int],
    hold_days_list: List[int],
    rebalances: List[str],
    min_history_days: int,
    transaction_cost_bps: float,
    risk_config: RiskConfig | None = None,
    exit_config: ExitConfig | None = None,
) -> Dict[str, Any]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_root = Path(data_dir)
    targets = load_targets(data_root, theme_key)
    prices = load_price_history(data_root, theme_key)
    docs = load_document_signals(data_root, theme_key)
    memberships = load_theme_memberships(data_root, theme_key)
    risk_config = risk_config or RiskConfig()
    exit_config = exit_config or ExitConfig()
    if not targets:
        targets = [
            StockTarget(stock_name=name, stock_code=code)
            for code, name in sorted(_stock_names_from_prices(prices).items())
        ]
    doc_index = _index_docs(docs)

    rows: List[Dict[str, Any]] = []
    total = len(periods) * len(top_ns) * len(hold_days_list) * len(rebalances)
    completed = 0

    for period in periods:
        period_name = period["name"]
        for rebalance in rebalances:
            for top_n in top_ns:
                for hold_days in hold_days_list:
                    completed += 1
                    task_id = (
                        f"bt-{theme_key}-{period_name}-"
                        f"{rebalance.lower()}-top{top_n}-h{hold_days}"
                    )
                    print(f"[SWEEP] {completed}/{total} {task_id}", flush=True)
                    result = _run_loaded_backtest(
                        data_root=data_root,
                        targets=targets,
                        prices=prices,
                        memberships=memberships,
                        doc_index=doc_index,
                        document_signal_count=len(docs),
                        theme=theme,
                        theme_key=theme_key,
                        from_date=period["from_date"],
                        to_date=period["to_date"],
                        rebalance=rebalance,
                        top_n=top_n,
                        hold_days=hold_days,
                        min_history_days=min_history_days,
                        transaction_cost_bps=transaction_cost_bps,
                        risk_config=risk_config,
                        exit_config=exit_config,
                        output_dir=out_dir,
                        task_id=task_id,
                    )
                    metrics = result["metrics"]
                    rows.append(
                        {
                            "task_id": task_id,
                            "period": period_name,
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
                            "stop_loss_pct": exit_config.stop_loss_pct,
                            "take_profit_pct": exit_config.take_profit_pct,
                            "trailing_stop_pct": exit_config.trailing_stop_pct,
                            "result_json": result["artifacts"].get("result_json", ""),
                        }
                    )

    rows.sort(
        key=lambda row: (
            row["period"],
            row["excess_return_pct"],
            row["sharpe"],
            row["total_return_pct"],
        ),
        reverse=True,
    )

    summary = {
        "theme": theme,
        "theme_key": theme_key,
        "generated_at": datetime.now().isoformat(),
        "config_count": len(rows),
        "periods": periods,
        "top_ns": top_ns,
        "hold_days_list": hold_days_list,
        "rebalances": rebalances,
        "risk_filters": risk_config.to_dict(),
        "exit_rules": exit_config.to_dict(),
        "point_in_time_universe": bool(memberships),
        "membership_count": len(memberships),
        "rows": rows,
        "best_by_period": _best_by_period(rows),
    }
    json_path = out_dir / f"sweep-{theme_key}.json"
    csv_path = out_dir / f"sweep-{theme_key}.csv"
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


def _run_loaded_backtest(
    *,
    data_root: Path,
    targets: List[StockTarget],
    prices: Dict[str, Any],
    memberships: List[Any],
    doc_index: Dict[str, Dict[str, List[str]]],
    document_signal_count: int,
    theme: str,
    theme_key: str,
    from_date: str,
    to_date: str,
    rebalance: str,
    top_n: int,
    hold_days: int,
    min_history_days: int,
    transaction_cost_bps: float,
    risk_config: RiskConfig,
    exit_config: ExitConfig,
    output_dir: Path,
    task_id: str,
) -> Dict[str, Any]:
    from_ymd = _require_ymd(from_date, "from_date")
    to_ymd = _require_ymd(to_date, "to_date")
    run_id = task_id or _default_task_id(theme_key, from_ymd, to_ymd, top_n, hold_days)
    target_by_code = {target.stock_code: target for target in targets}
    if memberships:
        membership_codes = {row.stock_code for row in memberships}
        target_by_code = {code: target for code, target in target_by_code.items() if code in membership_codes}
    common_calendar = _build_common_calendar(prices, from_ymd, to_ymd, hold_days)
    rebalance_dates = _select_rebalance_dates(common_calendar, rebalance)

    positions: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    period_rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    risk_reject_counts: Dict[str, int] = defaultdict(int)
    equity = 1.0
    benchmark_equity = 1.0
    transaction_cost = transaction_cost_bps / 10000.0

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
        benchmark_net_return = benchmark_return - (transaction_cost * 2)
        market_breadth_pct = _market_breadth_pct(eligible)
        filtered, rejected = _apply_risk_filters(eligible, risk_config)
        _add_counts(risk_reject_counts, rejected)
        risk_off_reason = _market_filter_reason(eligible, risk_config)
        if not risk_off_reason and len(filtered) < top_n:
            risk_off_reason = f"risk_filters eligible stocks {len(filtered)} < top_n {top_n}"

        if risk_off_reason:
            warnings.append(f"{as_of_ymd}: {risk_off_reason}")
            benchmark_equity *= 1.0 + benchmark_net_return
            period_rows.append(
                {
                    "as_of_date": _fmt_ymd(as_of_ymd),
                    "entry_date": "",
                    "exit_date": "",
                    "selected_count": 0,
                    "active_target_count": len(active_targets),
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

        ranked = sorted(filtered, key=lambda row: row["leader_score"], reverse=True)
        selected = ranked[:top_n]
        selected_return = float(np.mean([row["realized_return"] for row in selected]))
        selected_net_return = selected_return - (transaction_cost * 2)
        portfolio_exit_date = _latest_exit_date(selected)
        equity *= 1.0 + selected_net_return
        benchmark_equity *= 1.0 + benchmark_net_return

        period_rows.append(
            {
                "as_of_date": _fmt_ymd(as_of_ymd),
                "entry_date": selected[0]["entry_date"],
                "exit_date": portfolio_exit_date,
                "selected_count": len(selected),
                "active_target_count": len(active_targets),
                "eligible_count": len(eligible),
                "risk_eligible_count": len(filtered),
                "risk_reject_count": len(eligible) - len(filtered),
                "market_breadth_pct": market_breadth_pct,
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
            "risk_filters": risk_config.to_dict(),
            "exit_rules": exit_config.to_dict(),
            "prediction_model": "momentum_price_forecast_v1",
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
            "document_signal_count": document_signal_count,
        },
    }
    out_path = _write_result(result, output_dir, run_id)
    result["artifacts"]["result_json"] = _display_path(out_path)
    _write_result(result, output_dir, run_id)
    return result


def _best_by_period(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        period = row["period"]
        if period not in best:
            best[period] = row
            continue
        current = best[period]
        if (
            row["excess_return_pct"],
            row["sharpe"],
            row["total_return_pct"],
        ) > (
            current["excess_return_pct"],
            current["sharpe"],
            current["total_return_pct"],
        ):
            best[period] = row
    return best


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _parse_ints(raw: str) -> List[int]:
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _parse_strings(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


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
        {"name": "2023", "from_date": "20230101", "to_date": "20231231"},
        {"name": "2024", "from_date": "20240101", "to_date": "20241231"},
        {"name": "2025", "from_date": "20250101", "to_date": "20251231"},
        {"name": "2026q1", "from_date": "20260101", "to_date": "20260331"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep AI-theme leader backtest configs.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--theme", default="AI")
    parser.add_argument("--theme-key", default="ai")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--top-ns", default="1,3,5")
    parser.add_argument("--hold-days", default="5,10,20,60")
    parser.add_argument("--rebalances", default="M,W")
    parser.add_argument(
        "--periods",
        default="",
        help="Comma-separated name:from_date:to_date specs. Defaults to 2023,2024,2025,2026q1.",
    )
    parser.add_argument("--min-history-days", type=int, default=150)
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--min-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--max-volatility-20d", type=float, default=0.0)
    parser.add_argument("--max-return-5d", type=float, default=0.0)
    parser.add_argument("--max-return-20d", type=float, default=0.0)
    parser.add_argument("--min-trend-150d", type=float, default=-1.0)
    parser.add_argument("--min-market-breadth-pct", type=float, default=0.0)
    parser.add_argument("--stop-loss-pct", type=float, default=0.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.0)
    parser.add_argument("--trailing-stop-pct", type=float, default=0.0)
    args = parser.parse_args()

    output_dir = args.output_dir or str(Path(args.data_dir) / "backtest_results" / "sweeps")
    summary = run_sweep(
        data_dir=args.data_dir,
        theme=args.theme,
        theme_key=args.theme_key,
        output_dir=output_dir,
        periods=_parse_periods(args.periods) if args.periods else _default_periods(),
        top_ns=_parse_ints(args.top_ns),
        hold_days_list=_parse_ints(args.hold_days),
        rebalances=_parse_strings(args.rebalances),
        min_history_days=args.min_history_days,
        transaction_cost_bps=args.transaction_cost_bps,
        risk_config=RiskConfig(
            min_avg_trading_value=args.min_avg_trading_value,
            max_volatility_20d=args.max_volatility_20d,
            max_return_5d=args.max_return_5d,
            max_return_20d=args.max_return_20d,
            min_trend_150d=args.min_trend_150d,
            min_market_breadth_pct=args.min_market_breadth_pct,
        ),
        exit_config=ExitConfig(
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
        ),
    )

    print("[SWEEP] summary_json=", summary["artifacts"]["summary_json"])
    print("[SWEEP] summary_csv=", summary["artifacts"]["summary_csv"])
    for period, row in sorted(summary["best_by_period"].items()):
        print(
            f"[SWEEP] best {period}: {row['task_id']} "
            f"excess={row['excess_return_pct']}% "
            f"return={row['total_return_pct']}% "
            f"mdd={row['mdd_pct']}% "
            f"sharpe={row['sharpe']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
