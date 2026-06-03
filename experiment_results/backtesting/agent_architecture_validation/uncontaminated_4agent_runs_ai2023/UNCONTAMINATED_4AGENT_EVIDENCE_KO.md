# Uncontaminated 4-Agent Architecture Evidence

- generated_at: `2026-06-03T02:12:03`
- 대표 4-agent: `four_agent_supervisor_final`
- 최종 랭킹은 `short_llm_only` / `long_llm_only`, `llm_weight=1.0`입니다.
- 후보 평가는 risk-filtered universe 전체입니다. `top_k=0`이므로 규칙기반 top10 prefilter를 쓰지 않습니다.
- agent prompt에서 `deterministic_leader_score`를 제거했습니다.
- RiskManager의 ±10점 calibrated score 보정을 제거했습니다.
- LLM/fallback 오류가 나면 규칙기반 점수로 조용히 대체하지 않고 실행을 실패시킵니다.

## Overall Summary

| profile | horizon | run_count | avg_excess_return_pct | avg_excess_delta_vs_4agent_pct | avg_excess_delta_vs_rule_pct | worst_mdd_pct | avg_total_return_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| analyst_only | long | 1 | -52.68 | -1.26 | -29.25 | -16.1 | -21.6 |
| analyst_only | short | 1 | 4.42 | 19.6 | -12.1 | -18.96 | 1.48 |
| chartist_only | long | 1 | -14.82 | 36.6 | 8.61 | -3.34 | 16.26 |
| chartist_only | short | 1 | 1.74 | 16.92 | -14.78 | -18.52 | -1.2 |
| four_agent_plus_liquidity | long | 1 | -51.36 | 0.06 | -27.93 | -14.69 | -20.29 |
| four_agent_plus_liquidity | short | 1 | -24.59 | -9.41 | -41.11 | -25.41 | -27.53 |
| four_agent_supervisor_final | long | 1 | -51.42 | 0.0 | -27.99 | -17.28 | -20.35 |
| four_agent_supervisor_final | short | 1 | -15.18 | 0.0 | -31.7 | -22.77 | -18.12 |
| quant_only | long | 1 | -39.92 | 11.5 | -16.49 | -1.96 | -8.85 |
| quant_only | short | 1 | 2.41 | 17.59 | -14.11 | -19.47 | -0.52 |
| remove_analyst | long | 1 | -33.25 | 18.17 | -9.82 | -2.62 | -2.17 |
| remove_analyst | short | 1 | -3.19 | 11.99 | -19.71 | -21.84 | -6.13 |
| remove_chartist | long | 1 | -50.5 | 0.92 | -27.07 | -13.77 | -19.42 |
| remove_chartist | short | 1 | 13.29 | 28.47 | -3.23 | -18.1 | 10.35 |
| remove_quant | long | 1 | -39.49 | 11.93 | -16.06 | -5.88 | -8.41 |
| remove_quant | short | 1 | -17.1 | -1.92 | -33.62 | -21.31 | -20.04 |
| three_agent_no_risk_manager | long | 1 | -49.06 | 2.36 | -25.63 | -13.77 | -17.98 |
| three_agent_no_risk_manager | short | 1 | -15.67 | -0.49 | -32.19 | -21.31 | -18.61 |

## Theme Summary

| profile | theme_key | horizon | run_count | avg_excess_return_pct | avg_excess_delta_vs_4agent_pct | avg_excess_delta_vs_rule_pct | worst_mdd_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| analyst_only | ai | long | 1 | -52.68 | -1.26 | -29.25 | -16.1 |
| analyst_only | ai | short | 1 | 4.42 | 19.6 | -12.1 | -18.96 |
| chartist_only | ai | long | 1 | -14.82 | 36.6 | 8.61 | -3.34 |
| chartist_only | ai | short | 1 | 1.74 | 16.92 | -14.78 | -18.52 |
| four_agent_plus_liquidity | ai | long | 1 | -51.36 | 0.06 | -27.93 | -14.69 |
| four_agent_plus_liquidity | ai | short | 1 | -24.59 | -9.41 | -41.11 | -25.41 |
| four_agent_supervisor_final | ai | long | 1 | -51.42 | 0.0 | -27.99 | -17.28 |
| four_agent_supervisor_final | ai | short | 1 | -15.18 | 0.0 | -31.7 | -22.77 |
| quant_only | ai | long | 1 | -39.92 | 11.5 | -16.49 | -1.96 |
| quant_only | ai | short | 1 | 2.41 | 17.59 | -14.11 | -19.47 |
| remove_analyst | ai | long | 1 | -33.25 | 18.17 | -9.82 | -2.62 |
| remove_analyst | ai | short | 1 | -3.19 | 11.99 | -19.71 | -21.84 |
| remove_chartist | ai | long | 1 | -50.5 | 0.92 | -27.07 | -13.77 |
| remove_chartist | ai | short | 1 | 13.29 | 28.47 | -3.23 | -18.1 |
| remove_quant | ai | long | 1 | -39.49 | 11.93 | -16.06 | -5.88 |
| remove_quant | ai | short | 1 | -17.1 | -1.92 | -33.62 | -21.31 |
| three_agent_no_risk_manager | ai | long | 1 | -49.06 | 2.36 | -25.63 | -13.77 |
| three_agent_no_risk_manager | ai | short | 1 | -15.67 | -0.49 | -32.19 | -21.31 |