# Multi-Agent Validation Phase 2 Progress

- Generated at: 2026-05-18T11:51:10+0900
- Comparison center: fixed multi-agent strategy vs matching deterministic and non-LLM baselines
- Representative strategy: `short_hybrid_05` for short horizon, `long_hybrid_05` remains the long-horizon representative from phase 1
- Cost protocol: transaction 15 bps + slippage 5 bps + market impact 5 bps, round trip 50 bps
- Liquidity protocol: portfolio 30,000,000 KRW, max position 5% of average trading value

## Executive Finding

Phase 2 confirms the real multi-agent execution path across extended years and multiple themes, but it does not yet prove that the multi-agent method is better.

The compact runs used `short_hybrid_05` with `llm_rerank_top_k=3` and `top_n=3`. That means the LLM could score and reorder the same three candidates, but usually could not replace the deterministic candidate set. Because the portfolio is equal-weighted, reordering alone does not change return. These runs are therefore valid as execution and leakage/cost checks, but not enough as final performance evidence.

## Requirement Checklist

| Requirement | Status | Evidence | Remaining Work |
|---|---|---|---|
| Extend periods to 2023 and 2024 | Partial | Phase 1 extended deterministic/technical baselines to 2023/2024. Phase 2 added real AI compact runs for 2023 W35 and 2024 W01. | Full 2023/2024 `short_hybrid_05` top10 and `long_hybrid_05` still need long runs. |
| Fix representative multi-agent strategy | Done for definition | Phase 1 fixed short=`short_hybrid_05`, long=`long_hybrid_05`. | Phase 2 compact runs only covered short horizon. |
| Add realistic trading costs | Done | All phase 2 runs use 15 bps fee, 5 bps slippage, 5 bps market impact, and liquidity cap. | Add harsher stress scenarios later. |
| Check future-data leakage | Partial | Temporal RAG and backtest use as-of-date filtering; results include fallback checks. | Need official point-in-time theme membership source. |
| Run other themes | Partial | Actual real multi-agent compact runs completed for AI, 바이오, 반도체, 로봇, 전력설비, 조선. 2차전지는 2026 W01 risk-off/no-trade. | Find a tradable 2차전지 window, then run real multi-agent. |
| Stronger comparisons beyond RSI/Bollinger | Partial | Phase 1 includes RSI, Bollinger, momentum_20d, volatility-adjusted momentum, deterministic baseline, and equal-weight eligible benchmark. | Market index benchmark is still missing. |
| Loss-risk detail | Done for current runs | Reports include MDD, worst period, loss count/streak, risk-off count, position count. | Add simultaneous exposure and cash-utilization diagnostics. |

## Compact Real Multi-Agent Runs

These rows used real local Ollama multi-agent calls where positions existed. `Fallback` must be `false` to count as a real LLM run.

| Theme | Period | Strategy | Fallback | Positions | Return | Benchmark | Excess | Delta vs Deterministic | MDD | Note |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| AI | 2023 W35 | short_hybrid_05 | false | 6 | -0.07% | 0.19% | -0.27% | 0.00% | -5.25% | Real 2023 compact run |
| AI | 2024 W01 | short_hybrid_05 | false | 3 | -4.18% | 1.07% | -5.25% | 0.00% | 0.00% | Real 2024 compact run |
| 바이오 | 2026 W01 | short_hybrid_05 | false | 6 | -12.11% | -2.21% | -9.90% | 0.00% | -10.93% | Multi-theme real run |
| 반도체 | 2026 W01 | short_hybrid_05 | false | 6 | 15.94% | 0.18% | 15.76% | 0.00% | 0.00% | Re-run with explicit real cache after sandbox fallback |
| 로봇 | 2026 W01 | short_hybrid_05 | false | 6 | -0.86% | 11.43% | -12.30% | 0.00% | 0.00% | Multi-theme real run |
| 전력설비 | 2026 W01 | short_hybrid_05 | false | 6 | 0.81% | 4.23% | -3.42% | 0.00% | -0.02% | Multi-theme real run |
| 조선 | 2026 W01 | short_hybrid_05 | false | 6 | 10.58% | 8.88% | 1.70% | 0.00% | 0.00% | Multi-theme real run |

## Non-Evaluable Or Diagnostic Runs

| Theme | Period | Result | Why It Is Not Final Evidence |
|---|---|---|---|
| AI | 2023 W01 | 0 positions | Min history requirement left no eligible stocks. |
| AI | 2024 W35 | 0 positions | Risk filter blocked trading in this segment. |
| 2차전지 | 2026 W01 | 0 positions | Risk filter blocked trading in this segment. |
| AI | 2023-2024 full champion attempt | Stopped | Full top10 local-model run was too slow for one pass; only a partial deterministic artifact exists locally. |

## Current Judgment

At this point, do not claim that the project multi-agent strategy is better than RSI, Bollinger, momentum, or deterministic ranking. The honest conclusion is:

- The multi-agent pipeline now runs point-in-time with realistic costs across multiple themes.
- The compact runs did not outperform deterministic ranking because the LLM only reranked the same top 3 candidates.
- Phase 1 still shows strong non-LLM technical baselines in 2023/2024, so the bar is higher than RSI/Bollinger alone.
- A fair final comparison requires `short_hybrid_05` with candidate top10 and selected top3, plus `long_hybrid_05`, over full 2023/2024 and more themes.

## Artifacts

- AI 2023 W35: `data/backtest_results/multi_agent_validation/phase2/ai_2023_w35_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- AI 2024 W01: `data/backtest_results/multi_agent_validation/phase2/ai_2024_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- 바이오 2026 W01: `data/backtest_results/multi_agent_validation/phase2/bio_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- 반도체 2026 W01: `data/backtest_results/multi_agent_validation/phase2/semiconductor_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- 로봇 2026 W01: `data/backtest_results/multi_agent_validation/phase2/robot_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- 전력설비 2026 W01: `data/backtest_results/multi_agent_validation/phase2/power_equipment_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- 조선 2026 W01: `data/backtest_results/multi_agent_validation/phase2/shipbuilding_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
- 2차전지 diagnostic: `data/backtest_results/multi_agent_validation/phase2/battery_2026_w01_short_hybrid05_top3_realistic_costs/ai_short_long_validation_report.md`
