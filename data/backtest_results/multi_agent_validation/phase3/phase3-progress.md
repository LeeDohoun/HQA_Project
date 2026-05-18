# Multi-Agent Validation Phase 3 Progress

- Generated at: 2026-05-18T14:22:43+0900
- Focus: verify the actual multi-agent architecture and run fairer top10 candidate pilots

## Architecture Check

The backtest uses the intended multi-agent pattern:

- lower agents: `Analyst`, `Quant`, `Chartist`
- supervising agent: `RiskManager`

The detailed audit is in `data/backtest_results/multi_agent_validation/phase3/multi-agent-architecture-audit.md`.

## New Top10 Pilot Results

| Theme | Period | Strategy | Candidate Flow | Fallback | Return | Benchmark | Excess | Delta vs Deterministic | Result |
|---|---|---|---|---|---:|---:|---:|---:|---|
| AI | 2024 W01 | short_hybrid_05 | top10 -> top3 | false | -3.37% | 1.07% | -4.45% | +0.81%p | multi-agent improved loss vs deterministic |
| 2차전지 | 2025 W44 | short_hybrid_05 | top10 -> top3 | false | -0.42% | 1.01% | -1.43% | -4.54%p | multi-agent underperformed deterministic |

## Why This Matters

The phase 2 compact runs used top3 -> top3. That verified the real LLM execution path, but it could not properly test stock selection because the same three candidates were usually kept.

The phase 3 top10 runs are more meaningful because the multi-agent scorer can replace a deterministic pick:

- In AI 2024 W01, it replaced `에이디테크놀로지` with `마음AI`, reducing the loss.
- In 2차전지 2025 W44, it replaced stronger deterministic winners with weaker picks, hurting performance.

## Current Judgment

The project multi-agent structure is implemented and running, but the performance evidence is mixed. The honest status is:

- It can improve selection in some cases.
- It can also make selection worse.
- It needs longer top10 runs before claiming superiority over deterministic, RSI, Bollinger, momentum, or equal-weight baselines.

## Artifacts

- Architecture audit: `data/backtest_results/multi_agent_validation/phase3/multi-agent-architecture-audit.md`
- AI top10 report: `data/backtest_results/multi_agent_validation/phase3/ai_2024_w01_short_hybrid05_top10_realistic_costs/ai_short_long_validation_report.md`
- 2차전지 tradable-window scan: `data/backtest_results/multi_agent_validation/phase3/battery_tradable_window_scan/ai_short_long_validation_report.md`
- 2차전지 top10 report: `data/backtest_results/multi_agent_validation/phase3/battery_2025_w44_short_hybrid05_top10_realistic_costs/ai_short_long_validation_report.md`

