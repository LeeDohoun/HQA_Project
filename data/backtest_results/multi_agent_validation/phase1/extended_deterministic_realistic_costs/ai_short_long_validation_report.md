# AI Short/Long Backtest Proof Report

- Theme: AI (ai)
- Generated at: 2026-05-17T19:59:06.854028
- Row count: 10

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
| deterministic_long | long | M | 60 | no | 0.0 |  | 0 |

## Scorecard

| Group | Count | Win % | Avg Excess Delta | Avg Return Delta | Avg MDD Delta | Grade |
|---|---:|---:|---:|---:|---:|---|
| overall | 0 | 0.00% | 0.00% | 0.00% | 0.00% | insufficient |

## Period Results

| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Baseline | MDD | Worst Period | Loss Streak | Hit Rate | Picks |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| validation_2023 | short | deterministic_short | completed | 13.58% | -2.94% | 16.52% | 0.00% | -17.34% | -12.76% | 2 | 48.48% | 티로보틱스/큐렉소/유니퀘스트/큐렉소/티로보틱스/유니퀘스트/큐렉소/유니퀘스트/티로보틱스/큐렉소 |
| validation_2023 | long | deterministic_long | completed | 7.64% | 31.08% | -23.43% | 0.00% | -3.34% | -4.46% | 1 | 33.33% | 큐렉소/티로보틱스/유니퀘스트/텔레칩스/제주반도체/에이디테크놀로지/에이디테크놀로지/텔레칩스/가온칩스 |
| validation_2024 | short | deterministic_short | completed | 38.27% | -16.38% | 54.65% | 0.00% | -21.75% | -14.99% | 3 | 45.61% | 에이디테크놀로지/가온칩스/텔레칩스/제주반도체/가온칩스/로보티즈/가온칩스/네패스아크/에이팩트/가온칩스 |
| validation_2024 | long | deterministic_long | completed | 16.78% | -40.32% | 57.10% | 0.00% | -9.97% | -9.38% | 2 | 41.67% | 가온칩스/산돌/텔레칩스/에이디테크놀로지/제주반도체/에이팩트/제주반도체/가온칩스/에이팩트/에이디테크놀로지 |
| tune_2025 | short | deterministic_short | completed | 188.46% | 66.79% | 121.67% | 0.00% | -27.28% | -12.60% | 4 | 50.69% | 로보티즈/칩스앤미디어/큐렉소/로보티즈/미래컴퍼니/로보스타/칩스앤미디어/마음AI/로보스타/로보티즈 |
| tune_2025 | long | deterministic_long | completed | 78.99% | 105.68% | -26.69% | 0.00% | -8.62% | -8.62% | 2 | 48.48% | 칩스앤미디어/마음AI/로보스타/태성/제주반도체/퀄리타스반도체/더블유에스아이/퀄리타스반도체/산돌/유니퀘스트 |
| validation_2026q1 | short | deterministic_short | completed | 21.22% | -6.07% | 27.28% | 0.00% | -22.56% | -13.82% | 2 | 52.38% | 로보티즈/에이팩트/더블유에스아이/제주반도체/더블유에스아이/에이팩트/제주반도체/고영/대덕전자/제주반도체 |
| validation_2026q1 | long | deterministic_long | completed | 5.94% | -2.14% | 8.08% | 0.00% | 0.00% | 5.94% | 0 | 33.33% | 태성/더블유에스아이/고영 |
| recent_2026apr_may | short | deterministic_short | completed | 69.42% | 34.88% | 34.54% | 0.00% | 0.00% | 8.90% | 0 | 100.00% | 한화에어로스페이스/삼성전기/대덕전자/삼성전기/대덕전자/리노공업/삼성전기/대덕전자/네패스아크/삼성전기 |
| recent_2026apr_may | long | deterministic_long | no_evaluable_rebalance_dates | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0 | 0.00% |  |

## Interpretation Rules

- A result is useful only when it beats the matching deterministic baseline for the same horizon.
- Short-horizon evidence should improve excess return without relying on a single rebalance date.
- Long-horizon evidence should improve excess return while keeping drawdown from deteriorating materially.
- Treat pilot-grade results as directional only; use validation periods for claims.

## Artifacts

- summary_json: `data/backtest_results/multi_agent_validation/phase1/extended_deterministic_realistic_costs/ai_short_long_validation_summary.json`
- summary_csv: `data/backtest_results/multi_agent_validation/phase1/extended_deterministic_realistic_costs/ai_short_long_validation_summary.csv`
- raw_results_json: `data/backtest_results/multi_agent_validation/phase1/extended_deterministic_realistic_costs/ai_short_long_validation_results.json`
- report_md: `data/backtest_results/multi_agent_validation/phase1/extended_deterministic_realistic_costs/ai_short_long_validation_report.md`
