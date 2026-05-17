# Multi-Agent Validation Status

- Theme: AI (ai)
- Generated at: 2026-05-17T20:28:15.697703

## Fixed Champion Strategy

- Short: short_hybrid_05: weekly rebalance, top 3, hold 5 trading days, multi_agent broad rerank top 10, llm_weight 0.5
- Long: long_hybrid_05: monthly rebalance, top 3, hold 60 trading days, multi_agent broad rerank top 10, llm_weight 0.5

## Cost And Risk Protocol

| Input | Value |
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

## Champion Results With Realistic Costs

These rows rerun the fixed multi-agent champion set where cache/local-model evidence is available.

| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Deterministic | MDD | Worst Period | Loss Streak |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| tune_2025 | short | deterministic_short | completed | 188.46% | 66.79% | 121.67% | 0.00% | -27.28% | -12.60% | 4 |
| tune_2025 | short | short_hybrid_05 | completed | 164.05% | 66.79% | 97.26% | -24.41% | -35.28% | -12.60% | 6 |
| tune_2025 | long | deterministic_long | completed | 78.99% | 105.68% | -26.69% | 0.00% | -8.62% | -8.62% | 2 |
| tune_2025 | long | long_hybrid_05 | completed | 99.07% | 105.68% | -6.60% | 20.09% | -14.78% | -12.70% | 2 |
| validation_2026q1 | short | deterministic_short | completed | 21.22% | -6.07% | 27.28% | 0.00% | -22.56% | -13.82% | 2 |
| validation_2026q1 | short | short_hybrid_05 | completed | 12.23% | -6.07% | 18.29% | -8.99% | -31.88% | -13.96% | 3 |
| validation_2026q1 | long | deterministic_long | completed | 5.94% | -2.14% | 8.08% | 0.00% | 0.00% | 5.94% | 0 |
| validation_2026q1 | long | long_hybrid_05 | completed | 14.00% | -2.14% | 16.14% | 8.06% | 0.00% | 14.00% | 0 |
| recent_2026apr_may | short | deterministic_short | completed | 69.42% | 34.88% | 34.54% | 0.00% | 0.00% | 8.90% | 0 |
| recent_2026apr_may | short | short_hybrid_05 | completed | 67.43% | 34.88% | 32.56% | -1.98% | 0.00% | 10.87% | 0 |
| recent_2026apr_may | long | deterministic_long | no_evaluable_rebalance_dates | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0 |
| recent_2026apr_may | long | long_hybrid_05 | no_evaluable_rebalance_dates | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0 |

## Extended Baselines

2023/2024 are currently extended for deterministic and non-LLM baselines. Real multi-agent must still be run for those years.

| Horizon | Period | Best Non-LLM Baseline | Return | Benchmark | Excess | MDD |
|---|---|---|---:|---:|---:|---:|
| short | recent_2026apr_may | momentum_20d | 69.19% | 34.88% | 34.31% | 0.00% |
| short | tune_2025 | momentum_20d | 48.09% | 66.79% | -18.69% | -37.44% |
| short | validation_2023 | rsi_ranked | 42.84% | -2.94% | 45.78% | -17.31% |
| short | validation_2024 | bollinger_ranked | 411.16% | -16.38% | 427.54% | -29.44% |
| short | validation_2026q1 | bollinger_lower | 17.47% | -6.07% | 23.54% | 0.00% |
| long | tune_2025 | rsi_ranked | 87.05% | 105.68% | -18.63% | -24.87% |
| long | validation_2023 | bollinger_ranked | 52.69% | 31.08% | 21.62% | 0.00% |
| long | validation_2024 | vol_adjusted_momentum | 16.02% | -40.32% | 56.33% | -2.86% |
| long | validation_2026q1 | momentum_20d | 5.41% | -2.14% | 7.55% | 0.00% |

## Temporal Leakage Audit

- Corpus date range: 2023-01-02 to 2026-04-27, rows=30614
- Chart date range: 2023-01-02 to 2026-04-27, rows=64685
- Theme membership rows: 50, sources={'local_corpus_inferred': 50}
- Multi-agent cache years: {'2025': 592, '2026': 230}

Controls:
- TemporalRAG excludes documents with published date after as_of_date.
- Leader backtest computes selection features from known rows at or before as_of_date.
- Future returns are used only after selection for evaluation.

Known limitations:
- Theme membership is inferred from local corpus evidence unless an official historical membership source is supplied.
- 2023/2024 real multi-agent cache is absent, so those periods still require a local-model run before final comparison.
- Forum data starts later than price/news/DART data, so early-period sentiment evidence is sparse.

## Multi-Theme Readiness

| Theme | Corpus Rows | Corpus Range | Chart Rows | Chart Range | Membership Rows | Multi-Agent Ready |
|---|---:|---|---:|---|---:|---|
| 반도체 | 21114 | 2024-11-04..2026-04-26 | 19717 | 2025-02-04..2026-04-24 | 0 | yes |
| 로봇 | 21546 | 2024-11-04..2026-04-27 | 19917 | 2025-02-05..2026-04-27 | 0 | yes |
| 바이오 | 23784 | 2023-01-03..2026-05-06 | 51332 | 2023-01-02..2026-05-06 | 39 | yes |
| 전력설비 | 22207 | 2023-01-02..2026-05-04 | 43304 | 2023-01-02..2026-05-04 | 34 | yes |
| 조선 | 17798 | 2024-11-05..2026-04-27 | 16354 | 2025-02-05..2026-04-27 | 41 | yes |
| 2차전지 | 22224 | 2024-11-03..2026-04-26 | 6400 | 2025-02-10..2026-04-30 | 0 | yes |

## Next Work

- 2023/2024 multi-agent cache is missing; run those periods before making a final out-of-sample claim.
- Run real multi-agent champion validation for 2023 and 2024 with the same cost protocol.
- Repeat the champion protocol on semiconductor, robotics, bio, power equipment, shipbuilding, and secondary battery themes.
- Add an official point-in-time theme-membership source to reduce survivorship bias.
- Stress test higher slippage/liquidity constraints and compare max drawdown, loss streak, and worst period return.

## Artifacts

- status_json: `data/backtest_results/multi_agent_validation/phase1/multi-agent-validation-status.json`
- champion_csv: `data/backtest_results/multi_agent_validation/phase1/multi-agent-validation-status-champion.csv`
- status_md: `data/backtest_results/multi_agent_validation/phase1/multi-agent-validation-status.md`
