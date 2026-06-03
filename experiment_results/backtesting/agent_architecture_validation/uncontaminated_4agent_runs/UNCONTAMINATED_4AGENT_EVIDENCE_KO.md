# Uncontaminated 4-Agent Architecture Evidence

- generated_at: `2026-06-01T00:33:36`
- 대표 4-agent: `four_agent_supervisor_final`
- 최종 랭킹은 `short_llm_only` / `long_llm_only`, `llm_weight=1.0`입니다.
- 후보 평가는 risk-filtered universe 전체입니다. `top_k=0`이므로 규칙기반 top10 prefilter를 쓰지 않습니다.
- agent prompt에서 `deterministic_leader_score`를 제거했습니다.
- RiskManager의 ±10점 calibrated score 보정을 제거했습니다.
- LLM/fallback 오류가 나면 규칙기반 점수로 조용히 대체하지 않고 실행을 실패시킵니다.

## Overall Summary

| profile | horizon | run_count | avg_excess_return_pct | avg_excess_delta_vs_4agent_pct | avg_excess_delta_vs_rule_pct | worst_mdd_pct | avg_total_return_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| analyst_only | long | 1 | 10.57 | -36.14 | -46.53 | -25.23 | -29.74 |
| analyst_only | short | 1 | -6.99 | -49.99 | -61.64 | -24.42 | -23.37 |
| chartist_only | long | 1 | 47.47 | 0.76 | -9.63 | -12.91 | 7.16 |
| chartist_only | short | 1 | 35.48 | -7.52 | -19.17 | -25.46 | 19.1 |
| four_agent_plus_liquidity | long | 1 | 17.2 | -29.51 | -39.9 | -25.23 | -23.11 |
| four_agent_plus_liquidity | short | 1 | 21.5 | -21.5 | -33.15 | -25.09 | 5.12 |
| four_agent_supervisor_final | long | 1 | 46.71 | 0.0 | -10.39 | -18.5 | 6.4 |
| four_agent_supervisor_final | short | 1 | 43.0 | 0.0 | -11.65 | -17.28 | 26.63 |
| quant_only | long | 1 | 19.17 | -27.54 | -37.93 | -26.84 | -21.15 |
| quant_only | short | 1 | -14.17 | -57.17 | -68.82 | -33.82 | -30.55 |
| remove_analyst | long | 1 | 35.18 | -11.53 | -21.92 | -22.13 | -5.13 |
| remove_analyst | short | 1 | 20.98 | -22.02 | -33.67 | -24.58 | 4.6 |
| remove_chartist | long | 1 | 17.2 | -29.51 | -39.9 | -25.23 | -23.11 |
| remove_chartist | short | 1 | -14.36 | -57.36 | -69.01 | -29.11 | -30.74 |
| remove_quant | long | 1 | 20.72 | -25.99 | -36.38 | -25.23 | -19.6 |
| remove_quant | short | 1 | 24.81 | -18.19 | -29.84 | -26.73 | 8.43 |
| three_agent_no_risk_manager | long | 1 | 17.2 | -29.51 | -39.9 | -25.23 | -23.11 |
| three_agent_no_risk_manager | short | 1 | 22.54 | -20.46 | -32.11 | -26.73 | 6.16 |

## Theme Summary

| profile | theme_key | horizon | run_count | avg_excess_return_pct | avg_excess_delta_vs_4agent_pct | avg_excess_delta_vs_rule_pct | worst_mdd_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| analyst_only | ai | long | 1 | 10.57 | -36.14 | -46.53 | -25.23 |
| analyst_only | ai | short | 1 | -6.99 | -49.99 | -61.64 | -24.42 |
| chartist_only | ai | long | 1 | 47.47 | 0.76 | -9.63 | -12.91 |
| chartist_only | ai | short | 1 | 35.48 | -7.52 | -19.17 | -25.46 |
| four_agent_plus_liquidity | ai | long | 1 | 17.2 | -29.51 | -39.9 | -25.23 |
| four_agent_plus_liquidity | ai | short | 1 | 21.5 | -21.5 | -33.15 | -25.09 |
| four_agent_supervisor_final | ai | long | 1 | 46.71 | 0.0 | -10.39 | -18.5 |
| four_agent_supervisor_final | ai | short | 1 | 43.0 | 0.0 | -11.65 | -17.28 |
| quant_only | ai | long | 1 | 19.17 | -27.54 | -37.93 | -26.84 |
| quant_only | ai | short | 1 | -14.17 | -57.17 | -68.82 | -33.82 |
| remove_analyst | ai | long | 1 | 35.18 | -11.53 | -21.92 | -22.13 |
| remove_analyst | ai | short | 1 | 20.98 | -22.02 | -33.67 | -24.58 |
| remove_chartist | ai | long | 1 | 17.2 | -29.51 | -39.9 | -25.23 |
| remove_chartist | ai | short | 1 | -14.36 | -57.36 | -69.01 | -29.11 |
| remove_quant | ai | long | 1 | 20.72 | -25.99 | -36.38 | -25.23 |
| remove_quant | ai | short | 1 | 24.81 | -18.19 | -29.84 | -26.73 |
| three_agent_no_risk_manager | ai | long | 1 | 17.2 | -29.51 | -39.9 | -25.23 |
| three_agent_no_risk_manager | ai | short | 1 | 22.54 | -20.46 | -32.11 | -26.73 |