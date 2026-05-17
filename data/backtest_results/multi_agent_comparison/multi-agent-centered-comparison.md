# Multi-Agent Centered Backtest Comparison

이 문서는 비교의 중심을 규칙 기반 전략이 아니라 multi-agent 전략으로 둔다.

## 비교 기준

- 중심 전략: `multi_agent` (`qwen3:14b` + `gpt-oss:20b`, `llm_mode=multi_agent`, `candidate_scope=broad`)
- 비교군: deterministic 주도주 전략, RSI, Bollinger Band
- short 조건: `W / top3 / hold5 / trailing15`
- long 조건: `M / top3 / hold60 / trailing15`
- 공통 리스크: breadth 40%, 20일 변동성 120% 초과 제외, 5일 35%/20일 90% 과열 제외, 거래비용 15bps

## 핵심 판단

- 2025 튜닝 구간 short: best multi-agent `short_hybrid_05`는 수익률 190.16%, 초과수익 104.90%.
  deterministic 대비 초과수익 차이: -26.77%p.
  최고 technical `bollinger_ranked` 대비 초과수익 차이: 140.34%p.
- 2026Q1 검증 구간 short: best multi-agent `short_llm_only`는 수익률 18.37%, 초과수익 21.75%.
  deterministic 대비 초과수익 차이: -6.26%p.
  최고 technical `rsi_oversold` 대비 초과수익 차이: 9.82%p.
- 2026년 4~5월 최근 구간 short: best multi-agent `short_hybrid_05`는 수익률 68.62%, 초과수익 32.73%.
  deterministic 대비 초과수익 차이: -2.00%p.
  최고 technical `bollinger_ranked` 대비 초과수익 차이: 29.64%p.

현재 증거는 short와 long이 다르게 보인다.

- short multi-agent는 RSI/볼밴보다 대체로 강하지만, deterministic 주도주 전략보다 일관되게 좋지는 않다.
- long multi-agent hybrid는 2025와 2026Q1에서 deterministic 대비 개선이 관측된다.
- 따라서 multi-agent는 단타 최종 랭커보다는 장기/중기 후보 검증 또는 hybrid 보조 점수 쪽이 더 유망하다.

## Short Horizon Results

| Period | Best Multi-Agent | MA Return | MA Excess | Deterministic Excess | Best RSI/BB Strategy | Best RSI/BB Excess |
|---|---|---:|---:|---:|---|---:|
| tune_2025 | short_hybrid_05 | 190.16% | 104.90% | 131.67% | bollinger_ranked | -35.44% |
| validation_2026q1 | short_llm_only | 18.37% | 21.75% | 28.01% | rsi_oversold | 11.93% |
| recent_2026apr_may | short_hybrid_05 | 68.62% | 32.73% | 34.73% | bollinger_ranked | 3.09% |

## Long Horizon Results

| Period | Best Multi-Agent | MA Return | MA Excess | Deterministic Excess | Best RSI/BB Strategy | Best RSI/BB Excess |
|---|---|---:|---:|---:|---|---:|
| tune_2025 | long_hybrid_05 | 103.29% | -7.10% | -27.60% | rsi_ranked | -15.68% |
| validation_2026q1 | long_hybrid_05 | 14.20% | 16.14% | 8.08% | bollinger_lower | 1.94% |

## 해석

1. multi-agent 중심으로 보면, “RSI/볼밴보다 나은가?”는 대체로 yes다. 특히 classic RSI/볼밴은 신호가 적거나 수익률이 약하다.
2. 더 어려운 질문은 “multi-agent가 규칙 기반보다 나은가?”다. short에서는 아직 no 또는 mixed다.
3. long에서는 hybrid multi-agent가 deterministic보다 좋아지는 구간이 있어 가장 유망하다.
4. 최종 결론은 multi-agent를 단독 매수 엔진으로 바로 쓰기보다, 후보 검증/장기 확신도/리스크 설명에 쓰는 쪽이 현재 증거와 맞다.

## 산출물

- combined_csv: `data/backtest_results/multi_agent_comparison/multi-agent-centered-comparison.csv`
- source_multi_agent: `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_summary.csv`
- source_technical_short: `data/backtest_results/multi_agent_comparison/technical_short/technical-baselines-ai.csv`
- source_technical_long: `data/backtest_results/multi_agent_comparison/technical_long/technical-baselines-ai.csv`
