# AI Short/Long Backtest Proof Report

- Theme: AI (ai)
- Generated at: 2026-05-09T12:18:37.389927
- Row count: 18

## Protocol

This report fixes short/long strategy definitions before comparing them against matching deterministic baselines.

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
| overall | 10 | 30.00% | -9.69% | -9.69% | -3.96% | weak |
| horizon:long | 4 | 75.00% | 6.27% | 6.27% | -2.56% | strong |
| horizon:short | 6 | 0.00% | -20.33% | -20.33% | -4.90% | weak |
| long_hybrid_05 | 2 | 100.00% | 14.28% | 14.29% | -2.83% | pilot |
| long_llm_only | 2 | 50.00% | -1.75% | -1.75% | -2.29% | pilot |
| short_hybrid_05 | 3 | 0.00% | -12.66% | -12.66% | -5.80% | weak |
| short_llm_only | 3 | 0.00% | -28.00% | -28.00% | -3.99% | weak |

## Period Results

| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Baseline | MDD | Hit Rate | Picks |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| tune_2025 | short | deterministic_short | completed | 216.92% | 85.25% | 131.67% | 0.00% | -26.35% | 50.69% | 로보티즈/칩스앤미디어/큐렉소/로보티즈/미래컴퍼니/로보스타/칩스앤미디어/마음AI/로보스타/로보티즈 |
| tune_2025 | short | short_hybrid_05 | completed | 190.16% | 85.25% | 104.90% | -26.77% | -34.44% | 50.69% | 로보티즈/칩스앤미디어/미래컴퍼니/로보티즈/미래컴퍼니/로보스타/마음AI/칩스앤미디어/로보스타/휴림로봇 |
| tune_2025 | short | short_llm_only | completed | 153.45% | 85.25% | 68.20% | -63.47% | -31.83% | 50.00% | 로보티즈/칩스앤미디어/미래컴퍼니/미래컴퍼니/로보스타/로보티즈/마음AI/칩스앤미디어/로보스타/휴림로봇 |
| tune_2025 | long | deterministic_long | completed | 82.78% | 110.38% | -27.60% | 0.00% | -8.42% | 48.48% | 칩스앤미디어/마음AI/로보스타/태성/제주반도체/퀄리타스반도체/더블유에스아이/퀄리타스반도체/산돌/유니퀘스트 |
| tune_2025 | long | long_hybrid_05 | completed | 103.29% | 110.38% | -7.10% | 20.50% | -14.07% | 54.55% | 마음AI/로보스타/태성/태성/로보티즈/에이디테크놀로지/더블유에스아이/퀄리타스반도체/로보로보/더블유에스아이 |
| tune_2025 | long | long_llm_only | completed | 71.22% | 110.38% | -39.16% | -11.56% | -13.01% | 45.45% | 태성/로보스타/마음AI/태성/로보티즈/가온칩스/퀄리타스반도체/유일로보틱스/더블유에스아이/바이브컴퍼니 |
| validation_2026q1 | short | deterministic_short | completed | 24.62% | -3.38% | 28.01% | 0.00% | -21.27% | 52.38% | 로보티즈/에이팩트/더블유에스아이/제주반도체/더블유에스아이/에이팩트/제주반도체/고영/대덕전자/제주반도체 |
| validation_2026q1 | short | short_hybrid_05 | completed | 15.40% | -3.38% | 18.79% | -9.22% | -30.59% | 45.24% | 에이팩트/큐알티/더블유에스아이/더블유에스아이/제주반도체/가온칩스/대덕전자/제주반도체/티로보틱스/유일로보틱스 |
| validation_2026q1 | short | short_llm_only | completed | 18.37% | -3.38% | 21.75% | -6.26% | -27.77% | 47.62% | 큐알티/에이팩트/고영/더블유에스아이/제주반도체/가온칩스/대덕전자/티로보틱스/에이팩트/유일로보틱스 |
| validation_2026q1 | long | deterministic_long | completed | 6.14% | -1.94% | 8.08% | 0.00% | 0.00% | 33.33% | 태성/더블유에스아이/고영 |
| validation_2026q1 | long | long_hybrid_05 | completed | 14.20% | -1.94% | 16.14% | 8.06% | 0.00% | 66.67% | 태성/더블유에스아이/제주반도체 |
| validation_2026q1 | long | long_llm_only | completed | 14.20% | -1.94% | 16.14% | 8.06% | 0.00% | 66.67% | 태성/제주반도체/더블유에스아이 |
| recent_2026apr_may | short | deterministic_short | completed | 70.61% | 35.88% | 34.73% | 0.00% | 0.00% | 100.00% | 한화에어로스페이스/삼성전기/대덕전자/삼성전기/대덕전자/리노공업/삼성전기/대덕전자/네패스아크/삼성전기 |
| recent_2026apr_may | short | short_hybrid_05 | completed | 68.62% | 35.88% | 32.73% | -2.00% | 0.00% | 100.00% | 삼성전기/한화에어로스페이스/대덕전자/삼성전기/대덕전자/큐알티/삼성전기/고영/네패스아크/삼성전기 |
| recent_2026apr_may | short | short_llm_only | completed | 56.34% | 35.88% | 20.46% | -14.27% | 0.00% | 100.00% | 삼성전기/한화에어로스페이스/산돌/큐알티/삼성전기/대덕전자/고영/삼성전기/오브젠/SK하이닉스 |
| recent_2026apr_may | long | deterministic_long | no_evaluable_rebalance_dates | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |  |
| recent_2026apr_may | long | long_hybrid_05 | no_evaluable_rebalance_dates | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |  |
| recent_2026apr_may | long | long_llm_only | no_evaluable_rebalance_dates | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |  |

## Interpretation Rules

- A result is useful only when it beats the matching deterministic baseline for the same horizon.
- Short-horizon evidence should improve excess return without relying on a single rebalance date.
- Long-horizon evidence should improve excess return while keeping drawdown from deteriorating materially.
- Treat pilot-grade results as directional only; use validation periods for claims.

## Artifacts

- summary_json: `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_summary.json`
- summary_csv: `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_summary.csv`
- raw_results_json: `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_results.json`
- report_md: `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_report.md`
