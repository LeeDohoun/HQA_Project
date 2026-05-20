# AI 테마 백테스트 최종 비교

- 생성 시각: 2026-05-20T14:35:11
- 비교 중심: 단타 `short_hybrid_05`, 장타 `long_hybrid_05`
- 비교 대상: deterministic 규칙기반, RSI, 볼린저밴드, 모멘텀, 변동성 조정 모멘텀
- 비용/리스크 조건: 거래비용 15bp, 슬리피지 5bp, 시장충격 5bp, 유동성/변동성/급등 필터, 15% 트레일링 스탑

## 결론 요약

- 핵심 커버리지: 8/8 완료
- multi-agent hybrid가 RSI/볼밴 최고 전략 이상이었던 경우: 4/8
- multi-agent hybrid가 deterministic 규칙기반 이상이었던 경우: 4/8
- 이 표는 수익률만 보지 않고 MDD, 샤프, 거래 발생 여부까지 같이 봅니다.

## 핵심 기간별 요약

| 기간 | 구간 | multi-agent | deterministic | RSI/볼밴 최고 | 기술전략 최고 | 최종 판정 |
|---|---|---:|---:|---|---|---|
| 2023 | 단타 | 14.92% | 13.58% | `rsi_ranked` 15.15% | `rsi_ranked` 15.15% | mixed |
| 2023 | 장타 | -0.42% | 7.64% | `bollinger_ranked` 52.69% | `bollinger_ranked` 52.69% | multi_agent_lagged |
| 2024 | 단타 | 46.99% | 38.27% | `bollinger_ranked` 167.72% | `bollinger_ranked` 167.72% | mixed |
| 2024 | 장타 | 0.08% | 16.78% | `bollinger_lower` 7.11% | `vol_adjusted_momentum` 16.02% | multi_agent_lagged |
| 2025 | 단타 | 190.16% | 216.92% | `bollinger_ranked` 49.81% | `momentum_20d` 60.08% | mixed |
| 2025 | 장타 | 103.29% | 82.78% | `rsi_ranked` 94.71% | `rsi_ranked` 94.71% | multi_agent_best_or_tied |
| 2026Q1 | 단타 | 15.40% | 24.62% | `rsi_oversold` 8.55% | `rsi_oversold` 8.55% | mixed |
| 2026Q1 | 장타 | 14.20% | 6.14% | `bollinger_ranked` -8.58% | `bollinger_ranked` -8.58% | multi_agent_best_or_tied |

## 전체 비교표

| 기간 | 구간 | 전략 | 그룹 | 수익률 | 벤치마크 | 초과수익 | MDD | 샤프 | 중심 대비 수익률 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| 2023 | 단타 | `short_hybrid_05` | multi_agent_hybrid | 14.92% | -2.94% | 17.86% | -17.34% | 0.990 | 0.00% |
| 2023 | 단타 | `short_llm_only` | multi_agent_llm_only | 8.57% | -2.94% | 11.51% | -13.17% | 0.690 | -6.35% |
| 2023 | 단타 | `deterministic_short` | deterministic | 13.58% | -2.94% | 16.52% | -17.34% | 0.920 | -1.34% |
| 2023 | 단타 | `rsi_oversold` | technical | -4.18% | -2.94% | -1.24% | -33.18% | 0.070 | -19.10% |
| 2023 | 단타 | `rsi_ranked` | technical | 15.15% | -2.94% | 18.09% | -43.10% | 0.800 | 0.23% |
| 2023 | 단타 | `bollinger_lower` | technical | -4.56% | -2.94% | -1.62% | -6.74% | -0.790 | -19.48% |
| 2023 | 단타 | `bollinger_ranked` | technical | -8.17% | -2.94% | -5.23% | -32.50% | -0.270 | -23.09% |
| 2023 | 단타 | `momentum_20d` | technical | -5.88% | -2.94% | -2.94% | -42.53% | 0.000 | -20.80% |
| 2023 | 단타 | `vol_adjusted_momentum` | technical | -34.79% | -2.94% | -31.86% | -44.43% | -1.810 | -49.71% |
| 2023 | 장타 | `long_hybrid_05` | multi_agent_hybrid | -0.42% | 31.08% | -31.49% | 0.00% | -0.030 | 0.00% |
| 2023 | 장타 | `long_llm_only` | multi_agent_llm_only | -9.94% | 31.08% | -41.02% | -8.11% | -0.820 | -9.52% |
| 2023 | 장타 | `deterministic_long` | deterministic | 7.64% | 31.08% | -23.43% | -3.34% | 0.420 | 8.06% |
| 2023 | 장타 | `rsi_oversold` | technical | 0.00% | 31.08% | -31.08% | 0.00% | 0.000 | 0.42% |
| 2023 | 장타 | `rsi_ranked` | technical | 50.39% | 31.08% | 19.31% | 0.00% | 1.230 | 50.81% |
| 2023 | 장타 | `bollinger_lower` | technical | 0.00% | 31.08% | -31.08% | 0.00% | 0.000 | 0.42% |
| 2023 | 장타 | `bollinger_ranked` | technical | 52.69% | 31.08% | 21.62% | 0.00% | 1.300 | 53.11% |
| 2023 | 장타 | `momentum_20d` | technical | 21.56% | 31.08% | -9.52% | 0.00% | 0.760 | 21.98% |
| 2023 | 장타 | `vol_adjusted_momentum` | technical | 9.72% | 31.08% | -21.35% | -8.92% | 0.380 | 10.14% |
| 2024 | 단타 | `short_hybrid_05` | multi_agent_hybrid | 46.99% | -16.38% | 63.37% | -23.56% | 1.040 | 0.00% |
| 2024 | 단타 | `short_llm_only` | multi_agent_llm_only | 64.11% | -16.38% | 80.48% | -22.75% | 1.300 | 17.12% |
| 2024 | 단타 | `deterministic_short` | deterministic | 38.27% | -16.38% | 54.65% | -21.75% | 0.910 | -8.72% |
| 2024 | 단타 | `rsi_oversold` | technical | -45.67% | -16.38% | -29.30% | -53.80% | -1.520 | -92.66% |
| 2024 | 단타 | `rsi_ranked` | technical | -22.06% | -16.38% | -5.69% | -68.20% | -0.040 | -69.05% |
| 2024 | 단타 | `bollinger_lower` | technical | -34.88% | -16.38% | -18.50% | -41.16% | -1.950 | -81.87% |
| 2024 | 단타 | `bollinger_ranked` | technical | 167.72% | -16.38% | 184.10% | -72.51% | 1.240 | 120.73% |
| 2024 | 단타 | `momentum_20d` | technical | 6.97% | -16.38% | 23.35% | -45.73% | 0.380 | -40.02% |
| 2024 | 단타 | `vol_adjusted_momentum` | technical | 24.09% | -16.38% | 40.47% | -38.21% | 0.660 | -22.90% |
| 2024 | 장타 | `long_hybrid_05` | multi_agent_hybrid | 0.08% | -40.32% | 40.40% | -22.03% | 0.080 | 0.00% |
| 2024 | 장타 | `long_llm_only` | multi_agent_llm_only | -12.33% | -40.32% | 27.98% | -27.54% | -0.190 | -12.41% |
| 2024 | 장타 | `deterministic_long` | deterministic | 16.78% | -40.32% | 57.10% | -9.97% | 0.430 | 16.70% |
| 2024 | 장타 | `rsi_oversold` | technical | 1.06% | -40.32% | 41.38% | 0.00% | 0.590 | 0.98% |
| 2024 | 장타 | `rsi_ranked` | technical | -14.31% | -40.32% | 26.01% | -15.23% | -0.800 | -14.39% |
| 2024 | 장타 | `bollinger_lower` | technical | 7.11% | -40.32% | 47.43% | -17.27% | 0.200 | 7.03% |
| 2024 | 장타 | `bollinger_ranked` | technical | -5.34% | -40.32% | 34.97% | -26.89% | -0.000 | -5.42% |
| 2024 | 장타 | `momentum_20d` | technical | 14.81% | -40.32% | 55.12% | -3.87% | 0.640 | 14.73% |
| 2024 | 장타 | `vol_adjusted_momentum` | technical | 16.02% | -40.32% | 56.33% | -2.86% | 0.700 | 15.94% |
| 2025 | 단타 | `short_hybrid_05` | multi_agent_hybrid | 190.16% | 85.25% | 104.90% | -34.44% | 2.040 | 0.00% |
| 2025 | 단타 | `short_llm_only` | multi_agent_llm_only | 153.45% | 85.25% | 68.20% | -31.83% | 1.870 | -36.71% |
| 2025 | 단타 | `deterministic_short` | deterministic | 216.92% | 85.25% | 131.67% | -26.35% | 2.310 | 26.76% |
| 2025 | 단타 | `rsi_oversold` | technical | 16.22% | 85.25% | -69.03% | -12.54% | 0.780 | -173.94% |
| 2025 | 단타 | `rsi_ranked` | technical | 15.39% | 85.25% | -69.86% | -34.27% | 0.560 | -174.77% |
| 2025 | 단타 | `bollinger_lower` | technical | 10.78% | 85.25% | -74.47% | -9.95% | 0.600 | -179.38% |
| 2025 | 단타 | `bollinger_ranked` | technical | 49.81% | 85.25% | -35.44% | -22.37% | 1.070 | -140.35% |
| 2025 | 단타 | `momentum_20d` | technical | 60.08% | 66.79% | -6.71% | -35.98% | 1.180 | -130.08% |
| 2025 | 단타 | `vol_adjusted_momentum` | technical | 34.96% | 66.79% | -31.83% | -40.18% | 0.890 | -155.20% |
| 2025 | 장타 | `long_hybrid_05` | multi_agent_hybrid | 103.29% | 110.38% | -7.10% | -14.07% | 0.760 | 0.00% |
| 2025 | 장타 | `long_llm_only` | multi_agent_llm_only | 71.22% | 110.38% | -39.16% | -13.01% | 0.630 | -32.07% |
| 2025 | 장타 | `deterministic_long` | deterministic | 82.78% | 110.38% | -27.60% | -8.42% | 0.960 | -20.51% |
| 2025 | 장타 | `rsi_oversold` | technical | 33.34% | 110.38% | -77.04% | -7.52% | 0.650 | -69.95% |
| 2025 | 장타 | `rsi_ranked` | technical | 94.71% | 110.38% | -15.68% | -22.23% | 0.960 | -8.58% |
| 2025 | 장타 | `bollinger_lower` | technical | -6.53% | 110.38% | -116.91% | -6.53% | -0.590 | -109.82% |
| 2025 | 장타 | `bollinger_ranked` | technical | 92.68% | 110.38% | -17.70% | -10.51% | 1.120 | -10.61% |
| 2026Q1 | 단타 | `short_hybrid_05` | multi_agent_hybrid | 15.40% | -3.38% | 18.79% | -30.59% | 0.980 | 0.00% |
| 2026Q1 | 단타 | `short_llm_only` | multi_agent_llm_only | 18.37% | -3.38% | 21.75% | -27.77% | 1.070 | 2.97% |
| 2026Q1 | 단타 | `deterministic_short` | deterministic | 24.62% | -3.38% | 28.01% | -21.27% | 1.540 | 9.22% |
| 2026Q1 | 단타 | `rsi_oversold` | technical | 8.55% | -3.38% | 11.93% | -15.59% | 0.900 | -6.85% |
| 2026Q1 | 단타 | `rsi_ranked` | technical | -18.18% | -3.38% | -14.80% | -31.93% | -1.230 | -33.58% |
| 2026Q1 | 단타 | `bollinger_lower` | technical | 4.42% | -3.38% | 7.80% | -5.30% | 0.870 | -10.98% |
| 2026Q1 | 단타 | `bollinger_ranked` | technical | -16.98% | -3.38% | -13.59% | -31.54% | -1.050 | -32.38% |
| 2026Q1 | 단타 | `momentum_20d` | technical | -10.57% | -6.07% | -4.51% | -35.34% | -0.230 | -25.97% |
| 2026Q1 | 단타 | `vol_adjusted_momentum` | technical | -8.04% | -6.07% | -1.98% | -32.88% | -0.090 | -23.44% |
| 2026Q1 | 장타 | `long_hybrid_05` | multi_agent_hybrid | 14.20% | -1.94% | 16.14% | 0.00% | 0.000 | 0.00% |
| 2026Q1 | 장타 | `long_llm_only` | multi_agent_llm_only | 14.20% | -1.94% | 16.14% | 0.00% | 0.000 | 0.00% |
| 2026Q1 | 장타 | `deterministic_long` | deterministic | 6.14% | -1.94% | 8.08% | 0.00% | 0.000 | -8.06% |
| 2026Q1 | 장타 | `rsi_oversold` | technical | 0.00% | -1.94% | 1.94% | 0.00% | 0.000 | -14.20% |
| 2026Q1 | 장타 | `rsi_ranked` | technical | -10.13% | -1.94% | -8.19% | 0.00% | 0.000 | -24.33% |
| 2026Q1 | 장타 | `bollinger_lower` | technical | 0.00% | -1.94% | 1.94% | 0.00% | 0.000 | -14.20% |
| 2026Q1 | 장타 | `bollinger_ranked` | technical | -8.58% | -1.94% | -6.64% | 0.00% | 0.000 | -22.78% |

## 리스크 해석

- `MDD`는 중간에 가장 크게 빠진 폭입니다. 수익률이 높아도 MDD가 깊으면 실제 운용 부담이 큽니다.
- `completed_no_trade` 행은 조건은 돌렸지만 리스크/신호 조건 때문에 실제 매수가 없었던 전략입니다.
- 이 결과는 백테스트이며, 실제 주문 지연, 호가 공백, 데이터 정정, 실시간 체결 실패는 별도 검증이 필요합니다.
- multi-agent는 3개 하위 에이전트인 analyst, quant, chartist와 상위 RiskManager 점수 조합 구조로 실행했습니다.

## 산출물

- csv: `/Users/leedohoun/Desktop/HQA_Project/experiment_results/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.csv`
- json: `/Users/leedohoun/Desktop/HQA_Project/experiment_results/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.json`
- report: `/Users/leedohoun/Desktop/HQA_Project/experiment_results/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.md`
- coverage_audit: `/Users/leedohoun/Desktop/HQA_Project/experiment_results/backtesting/ai_strategy_comparison/BACKTEST_COVERAGE_AUDIT.md`
- main_report: `/Users/leedohoun/Desktop/HQA_Project/experiment_results/backtesting/ai_strategy_comparison/AI_STRATEGY_COMPARISON_REPORT.md`
