from __future__ import annotations

import json
from pathlib import Path


def test_proof_validation_writes_summary_csv_and_report(tmp_path):
    from backtesting.proof_validation import PeriodSpec, StrategySpec, run_proof_validation

    def fake_runner(**kwargs):
        task_id = kwargs["task_id"]
        llm_enabled = "llm_weight" in kwargs
        is_short = kwargs["hold_days"] == 5
        total_return = 12.0 if llm_enabled and is_short else 10.0 if is_short else 4.0
        benchmark_return = 5.0 if is_short else 3.0
        return {
            "task_id": task_id,
            "prediction_model": "fake_llm" if llm_enabled else "momentum_price_forecast_v1",
            "period": {
                "from_date": "2026-01-01",
                "to_date": "2026-01-16",
                "rebalance": kwargs["rebalance"],
                "rebalance_count": 1,
                "hold_days": kwargs["hold_days"],
            },
            "metrics": {
                "rebalance_count": 1,
                "traded_rebalance_count": 1,
                "risk_off_count": 0,
                "position_count": 1,
                "total_return_pct": total_return,
                "benchmark_return_pct": benchmark_return,
                "excess_return_pct": total_return - benchmark_return,
                "mdd_pct": -1.0,
                "sharpe": 1.0,
                "win_rate_pct": 100.0,
                "prediction_hit_rate_pct": 100.0,
                "avg_position_return_pct": total_return,
                "median_position_return_pct": total_return,
            },
            "positions": [
                {
                    "stock_name": "Alpha",
                    "stock_code": "000001",
                    "rank": 1,
                    "realized_return_pct": total_return,
                }
            ],
            "periods": [{"as_of_date": "2026-01-02"}],
            "artifacts": {"result_json": f"runs/{task_id}.json"},
        }

    summary = run_proof_validation(
        data_dir=tmp_path,
        theme="AI",
        theme_key="ai",
        output_dir=tmp_path / "proof",
        periods=[PeriodSpec("smoke", "20260101", "20260116", "smoke")],
        strategies=[
            StrategySpec(
                strategy_id="deterministic_short",
                label="Short baseline",
                horizon="short",
                rebalance="W",
                top_n=1,
                hold_days=5,
                llm_enabled=False,
                is_baseline=True,
            ),
            StrategySpec(
                strategy_id="short_hybrid_05",
                label="Short hybrid",
                horizon="short",
                rebalance="W",
                top_n=1,
                hold_days=5,
                llm_enabled=True,
                llm_weight=0.5,
            ),
        ],
        runner=fake_runner,
    )

    artifacts = summary["artifacts"]
    assert summary["row_count"] == 2
    assert summary["scorecard"]["overall"]["win_vs_baseline_count"] == 1
    assert summary["rows"][1]["excess_delta_vs_baseline_pct"] == 2.0
    assert Path(artifacts["summary_json"]).exists()
    assert Path(artifacts["summary_csv"]).exists()
    assert Path(artifacts["report_md"]).exists()
    assert "short_hybrid_05" in Path(artifacts["report_md"]).read_text(encoding="utf-8")


def test_proof_validation_resumes_completed_runs(tmp_path):
    from backtesting.proof_validation import PeriodSpec, StrategySpec, run_proof_validation

    output_dir = tmp_path / "proof"
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True)
    task_id = "proof-ai-smoke-deterministic_short"
    completed_result = {
        "task_id": task_id,
        "prediction_model": "momentum_price_forecast_v1",
        "period": {
            "from_date": "2026-01-01",
            "to_date": "2026-01-16",
            "rebalance": "W",
            "rebalance_count": 1,
            "hold_days": 5,
        },
        "metrics": {
            "rebalance_count": 1,
            "traded_rebalance_count": 1,
            "risk_off_count": 0,
            "position_count": 1,
            "total_return_pct": 10.0,
            "benchmark_return_pct": 5.0,
            "excess_return_pct": 5.0,
            "mdd_pct": -1.0,
            "sharpe": 1.0,
            "win_rate_pct": 100.0,
            "prediction_hit_rate_pct": 100.0,
            "avg_position_return_pct": 10.0,
            "median_position_return_pct": 10.0,
        },
        "positions": [{"stock_name": "Alpha", "stock_code": "000001"}],
        "periods": [{"as_of_date": "2026-01-02"}],
        "artifacts": {"result_json": str(runs_dir / f"{task_id}.json")},
    }
    (runs_dir / f"{task_id}.json").write_text(json.dumps(completed_result), encoding="utf-8")

    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs["task_id"])
        return {
            **completed_result,
            "task_id": kwargs["task_id"],
            "prediction_model": "fake_llm",
            "metrics": {
                **completed_result["metrics"],
                "total_return_pct": 12.0,
                "excess_return_pct": 7.0,
            },
            "artifacts": {"result_json": str(runs_dir / f"{kwargs['task_id']}.json")},
        }

    summary = run_proof_validation(
        data_dir=tmp_path,
        theme="AI",
        theme_key="ai",
        output_dir=output_dir,
        periods=[PeriodSpec("smoke", "20260101", "20260116", "smoke")],
        strategies=[
            StrategySpec(
                strategy_id="deterministic_short",
                label="Short baseline",
                horizon="short",
                rebalance="W",
                top_n=1,
                hold_days=5,
                llm_enabled=False,
                is_baseline=True,
            ),
            StrategySpec(
                strategy_id="short_hybrid_05",
                label="Short hybrid",
                horizon="short",
                rebalance="W",
                top_n=1,
                hold_days=5,
                llm_enabled=True,
                llm_weight=0.5,
            ),
        ],
        runner=fake_runner,
    )

    assert calls == ["proof-ai-smoke-short_hybrid_05"]
    assert summary["row_count"] == 2
    assert summary["rows"][0]["task_id"] == task_id
    assert summary["rows"][1]["excess_delta_vs_baseline_pct"] == 2.0


def test_proof_validation_skips_no_rebalance_periods(tmp_path):
    from backtesting.proof_validation import PeriodSpec, StrategySpec, run_proof_validation

    def fake_runner(**kwargs):
        raise ValueError("no rebalance dates in period: 20260401..20260507")

    summary = run_proof_validation(
        data_dir=tmp_path,
        theme="AI",
        theme_key="ai",
        output_dir=tmp_path / "proof",
        periods=[PeriodSpec("recent", "20260401", "20260507", "recent_check")],
        strategies=[
            StrategySpec(
                strategy_id="deterministic_long",
                label="Long baseline",
                horizon="long",
                rebalance="M",
                top_n=1,
                hold_days=60,
                llm_enabled=False,
                is_baseline=True,
            ),
            StrategySpec(
                strategy_id="long_hybrid_05",
                label="Long hybrid",
                horizon="long",
                rebalance="M",
                top_n=1,
                hold_days=60,
                llm_enabled=True,
                llm_weight=0.5,
            ),
        ],
        runner=fake_runner,
    )

    assert summary["row_count"] == 2
    assert summary["scorecard"]["overall"]["comparison_count"] == 0
    assert summary["rows"][0]["status"] == "skipped"
    assert summary["rows"][0]["skip_reason"] == "no_evaluable_rebalance_dates"
    assert summary["rows"][0]["evaluable"] is False
    assert Path(summary["rows"][0]["result_json"]).exists()
    assert Path(summary["artifacts"]["report_md"]).exists()
