# Multi-Agent Architecture Audit

- Generated at: 2026-05-18T14:22:43+0900
- Purpose: verify whether the backtest uses the project multi-agent structure before interpreting performance

## Verdict

The project structure is present in the backtest: three specialist agents feed one supervising agent.

| Layer | Project Name | Backtest Implementation | Role |
|---|---|---|---|
| Specialist 1 | `Analyst` | `_evaluate_analyst()` | Theme fit, moat, growth, catalysts, qualitative risks |
| Specialist 2 | `Quant` | `_evaluate_quant()` | Valuation, profitability, growth, stability, financial risk |
| Specialist 3 | `Chartist` | `_evaluate_chartist()` | Price trend, momentum, volatility, volume leadership |
| Supervisor | `RiskManager` | `_evaluate_risk_manager()` | Reads the three specialist outputs and creates final action, risk, summary, and final score |

Code evidence:

- `README.md` says single-stock and theme-leader flows run `Analyst`, `Quant`, `Chartist`, then `RiskManager`.
- `src/agents/theme_orchestrator.py` states that candidates are evaluated by `Analyst / Quant / Chartist` and `RiskManager` aggregates final ranking.
- `backtesting/llm_signal.py` uses `TemporalMultiAgentStockScorer`, whose metadata records `agents = ["analyst", "quant", "chartist", "risk_manager"]`.
- In the score path, the backtest calls `analyst = _evaluate_analyst(...)`, `quant = _evaluate_quant(...)`, `chartist = _evaluate_chartist(...)`, then `risk = _evaluate_risk_manager(... analyst, quant, chartist)`.

## Important Difference

The backtest uses a point-in-time safe version of the project architecture. It does not call the live operating orchestrator directly because the live path may access current data.

There is also one scoring nuance:

- `RiskManager` is actually called and its raw final score is stored as `raw_final_score`.
- The ranking score used by the backtest is the calibrated specialist-weight score stored as `calibrated_final_score`.
- In the phase 3 top10 runs checked here, `raw_final_score` and `calibrated_final_score` matched for the selected positions, so the supervisor was not being ignored in those examples.

Short-horizon weights:

- Analyst 30%
- Quant 15%
- Chartist 55%

Long-horizon weights:

- Analyst 45%
- Quant 40%
- Chartist 15%

## Current Result Summary

| Run | Candidate Set | Deterministic Picks | Multi-Agent Picks | Multi-Agent Result |
|---|---:|---|---|---|
| AI 2024 W01 | top10 -> top3 | 에이디테크놀로지/가온칩스/텔레칩스 | 마음AI/가온칩스/텔레칩스 | Better by +0.81%p return, +0.80%p excess |
| 2차전지 2025 W44 | top10 -> top3 | 천보/레몬/엠플러스 | 천보/테이팩스/코세스 | Worse by -4.54%p return and excess |

Interpretation:

- The earlier top3 compact runs were too constrained: LLM could mostly reorder the same three stocks, so returns stayed identical.
- The top10 phase 3 runs are more meaningful because multi-agent can replace deterministic picks.
- Results are mixed: AI improved in one pilot, 2차전지 worsened in one pilot.
- Therefore, it is still not valid to claim that the multi-agent method is generally better.

## What To Do Next

1. Run top10 -> top3 `short_hybrid_05` over longer windows, not one-week pilots.
2. Run `long_hybrid_05` so the long-term representative strategy is actually tested.
3. Add an explicit market index benchmark, separate from the current equal-weight eligible-stock benchmark.
4. Add a score-mode experiment comparing calibrated score vs raw `RiskManager` final score.
5. Expand top10 tests across AI, 반도체, 바이오, 로봇, 전력설비, 조선, 2차전지.

