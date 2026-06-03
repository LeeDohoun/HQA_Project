# AI Short/Long Backtest Proof Report

- Theme: AI (ai)
- Generated at: 2026-05-20T14:32:17.995159
- Row count: 12

## Protocol

This report fixes short/long strategy definitions before comparing them against matching deterministic baselines.

| Cost/Risk Input | Value |
|---|---:|
| min_history_days | 150 |
| transaction_cost_bps | 15.0 |
| slippage_bps | 5.0 |
| market_impact_bps | 5.0 |
| round_trip_cost_bps | 50.0 |
| portfolio_value_krw | 30000000.0 |
| position_value_krw | 0.0 |
| max_position_pct_avg_trading_value | 5.0 |
| min_avg_trading_value | 0.0 |
| max_volatility_20d | 1.2 |
| max_return_5d | 0.35 |
| max_return_20d | 0.9 |
| min_trend_150d | -1.0 |
| min_market_breadth_pct | 40.0 |
| stop_loss_pct | 0.0 |
| take_profit_pct | 0.0 |
| trailing_stop_pct | 15.0 |

| Strategy | Horizon | Rebalance | Hold Days | LLM | Weight | Scope | Top K |
|---|---|---:|---:|---|---:|---|---:|
| deterministic_short | short | W | 5 | no | 0.0 |  | 0 |
| short_hybrid_05 | short | W | 5 | yes | 0.5 | broad | 10 |
| short_llm_only | short | W | 5 | yes | 1.0 | broad | 10 |
| deterministic_long | long | M | 60 | no | 0.0 |  | 0 |
| long_hybrid_05 | long | M | 60 | yes | 0.5 | broad | 10 |
| long_llm_only | long | M | 60 | yes | 1.0 | broad | 10 |

## Scorecard

| Group | Count | Win % | Avg Excess Delta | Avg Return Delta | Avg MDD Delta | Grade |
|---|---:|---:|---:|---:|---:|---|
| overall | 8 | 37.50% | -5.07% | -5.07% | -3.71% | weak |
| horizon:long | 4 | 0.00% | -17.87% | -17.86% | -7.77% | weak |
| horizon:short | 4 | 75.00% | 7.72% | 7.72% | 0.34% | strong |
| long_hybrid_05 | 2 | 0.00% | -12.38% | -12.38% | -4.36% | pilot |
| long_llm_only | 2 | 0.00% | -23.36% | -23.34% | -11.17% | pilot |
| short_hybrid_05 | 2 | 100.00% | 5.03% | 5.03% | -0.91% | pilot |
| short_llm_only | 2 | 50.00% | 10.41% | 10.41% | 1.58% | pilot |

## Period Results

| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Baseline | MDD | Worst Period | Loss Streak | Hit Rate | Picks |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| validation_2023 | short | deterministic_short | completed | 13.58% | -2.94% | 16.52% | 0.00% | -17.34% | -12.76% | 2 | 48.48% | 티로보틱스/큐렉소/유니퀘스트/큐렉소/티로보틱스/유니퀘스트/큐렉소/유니퀘스트/티로보틱스/큐렉소 |
| validation_2023 | short | short_hybrid_05 | completed | 14.92% | -2.94% | 17.86% | 1.34% | -17.34% | -12.76% | 2 | 45.45% | 큐렉소/티로보틱스/유니퀘스트/큐렉소/티로보틱스/유니퀘스트/큐렉소/유니퀘스트/티로보틱스/가온칩스 |
| validation_2023 | short | short_llm_only | completed | 8.57% | -2.94% | 11.51% | -5.01% | -13.17% | -8.88% | 2 | 45.45% | 큐렉소/티로보틱스/로보스타/큐렉소/티로보틱스/대덕전자/큐렉소/유니퀘스트/에이팩트/가온칩스 |
| validation_2023 | long | deterministic_long | completed | 7.64% | 31.08% | -23.43% | 0.00% | -3.34% | -4.46% | 1 | 33.33% | 큐렉소/티로보틱스/유니퀘스트/텔레칩스/제주반도체/에이디테크놀로지/에이디테크놀로지/텔레칩스/가온칩스 |
| validation_2023 | long | long_hybrid_05 | completed | -0.42% | 31.08% | -31.49% | -8.06% | 0.00% | -4.46% | 1 | 22.22% | 큐렉소/유니퀘스트/티로보틱스/텔레칩스/오픈엣지테크놀로지/가온칩스/텔레칩스/로보티즈/가온칩스 |
| validation_2023 | long | long_llm_only | completed | -9.94% | 31.08% | -41.02% | -17.59% | -8.11% | -8.11% | 1 | 11.11% | 큐렉소/가온칩스/로보티즈/텔레칩스/오픈엣지테크놀로지/가온칩스/텔레칩스/로보티즈/오픈엣지테크놀로지 |
| validation_2024 | short | deterministic_short | completed | 38.27% | -16.38% | 54.65% | 0.00% | -21.75% | -14.99% | 3 | 45.61% | 에이디테크놀로지/가온칩스/텔레칩스/제주반도체/가온칩스/로보티즈/가온칩스/네패스아크/에이팩트/가온칩스 |
| validation_2024 | short | short_hybrid_05 | completed | 46.99% | -16.38% | 63.37% | 8.72% | -23.56% | -14.99% | 3 | 45.61% | 마음AI/가온칩스/텔레칩스/제주반도체/로보티즈/로보스타/가온칩스/에이팩트/네패스아크/가온칩스 |
| validation_2024 | short | short_llm_only | completed | 64.11% | -16.38% | 80.48% | 25.83% | -22.75% | -13.76% | 3 | 49.12% | 마음AI/대덕전자/가온칩스/로보스타/제주반도체/로보티즈/가온칩스/에이팩트/텔레칩스/큐알티 |
| validation_2024 | long | deterministic_long | completed | 16.78% | -40.32% | 57.10% | 0.00% | -9.97% | -9.38% | 2 | 41.67% | 가온칩스/산돌/텔레칩스/에이디테크놀로지/제주반도체/에이팩트/제주반도체/가온칩스/에이팩트/에이디테크놀로지 |
| validation_2024 | long | long_hybrid_05 | completed | 0.08% | -40.32% | 40.40% | -16.70% | -22.03% | -13.36% | 2 | 33.33% | 가온칩스/텔레칩스/산돌/제주반도체/오픈엣지테크놀로지/에이디테크놀로지/가온칩스/제주반도체/에이디테크놀로지/에이디테크놀로지 |
| validation_2024 | long | long_llm_only | completed | -12.33% | -40.32% | 27.98% | -29.12% | -27.54% | -13.59% | 3 | 33.33% | 텔레칩스/가온칩스/라온피플/오픈엣지테크놀로지/로보티즈/제주반도체/가온칩스/에이디테크놀로지/제주반도체/가온칩스 |

## Interpretation Rules

- A result is useful only when it beats the matching deterministic baseline for the same horizon.
- Short-horizon evidence should improve excess return without relying on a single rebalance date.
- Long-horizon evidence should improve excess return while keeping drawdown from deteriorating materially.
- Treat pilot-grade results as directional only; use validation periods for claims.

## Artifacts

- summary_json: `experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/ai_short_long_validation_summary.json`
- summary_csv: `experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/ai_short_long_validation_summary.csv`
- raw_results_json: `experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/ai_short_long_validation_results.json`
- report_md: `experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/ai_short_long_validation_report.md`
