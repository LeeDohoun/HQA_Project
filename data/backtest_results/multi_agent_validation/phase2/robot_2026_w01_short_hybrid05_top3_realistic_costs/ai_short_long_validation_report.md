# AI Short/Long Backtest Proof Report

- Theme: 로봇 (로봇)
- Generated at: 2026-05-18T11:16:17.104342
- Row count: 2

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
| short_hybrid_05 | short | W | 5 | yes | 0.5 | broad | 3 |

## Scorecard

| Group | Count | Win % | Avg Excess Delta | Avg Return Delta | Avg MDD Delta | Grade |
|---|---:|---:|---:|---:|---:|---|
| overall | 1 | 0.00% | 0.00% | 0.00% | 0.00% | pilot |
| horizon:short | 1 | 0.00% | 0.00% | 0.00% | 0.00% | pilot |
| short_hybrid_05 | 1 | 0.00% | 0.00% | 0.00% | 0.00% | pilot |

## Period Results

| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Baseline | MDD | Worst Period | Loss Streak | Hit Rate | Picks |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| validation_2026_w01 | short | deterministic_short | completed | -0.86% | 11.43% | -12.30% | 0.00% | 0.00% | -11.34% | 1 | 50.00% | HL만도/LS티라유텍/디아이씨/한국피아이엠/삼현/에스비비테크 |
| validation_2026_w01 | short | short_hybrid_05 | completed | -0.86% | 11.43% | -12.30% | 0.00% | 0.00% | -11.34% | 1 | 50.00% | HL만도/LS티라유텍/디아이씨/한국피아이엠/에스비비테크/삼현 |

## Interpretation Rules

- A result is useful only when it beats the matching deterministic baseline for the same horizon.
- Short-horizon evidence should improve excess return without relying on a single rebalance date.
- Long-horizon evidence should improve excess return while keeping drawdown from deteriorating materially.
- Treat pilot-grade results as directional only; use validation periods for claims.

## Artifacts

- summary_json: `data/backtest_results/multi_agent_validation/phase2/robot_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_summary.json`
- summary_csv: `data/backtest_results/multi_agent_validation/phase2/robot_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_summary.csv`
- raw_results_json: `data/backtest_results/multi_agent_validation/phase2/robot_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_results.json`
- report_md: `data/backtest_results/multi_agent_validation/phase2/robot_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
