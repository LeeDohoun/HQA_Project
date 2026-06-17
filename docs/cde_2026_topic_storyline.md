# CDE 2026 Poster Topic And Storyline

Generated: 2026-06-17

## Decision

Use the topic below for the June 26 submission.

**Korean title**

시점 제한형 RAG와 Agentic AI를 활용한 동적 산업 테마 후보군 검증 프레임워크

**English title**

A Point-in-Time Temporal RAG Validation Framework for Agentic Industrial Theme Candidate Prioritization

**Subtitle / scope**

AI 산업 테마 내 후보군 선별 및 시계열 검증 사례

**Multi-agent validation verdict**

Four independent checks were run for this document: code/branch evidence, experimental-method validity, CDE fit, and adversarial reviewer risk. They converged on the same conclusion: keep the topic, but narrow the claim. The poster should be about a point-in-time validation and decision-support framework, not about a profitable automated trading model or a universally superior multi-agent strategy.

This is the safest scope for the current project. The verified backtests evaluate candidate selection within a single AI industry theme, while the current paper-trading pipeline extends selected leaders across multiple themes. Therefore, the poster should not claim that the full multi-theme paper-trading strategy has already been backtested. The CDE-facing title also avoids sounding like a stock-trading product.

## Why This Topic Fits CDE

The 2026 CDE summer conference lists the following relevant topics:

- LLM & Agentic AI
- AI & Advanced Applications
- Modeling & Optimization
- IoT/Bigdata Applications
- Digital Twin, Physical AI, Autonomous Manufacturing

The 2025 CDE summer and winter conference pages also include AI, LLM/Agentic AI, multi-agent systems, digital twin, smart manufacturing, and decision-making topics. One 2025 poster listing found in search results includes an LLM-RAG topic, which makes the proposed framing more natural than a finance-app framing.

Use engineering-decision wording, not trading-product wording.

## Recommended Claim

This project proposes a point-in-time temporal evidence retrieval and role-specialized Agentic AI decision framework for evaluating dynamic candidate groups inside an industry theme. The framework combines news, disclosures, forum text, and price-derived signals, restricts evidence by historical decision time to reduce future-information leakage, and compares deterministic, technical-indicator, LLM-only, and hybrid multi-perspective selection strategies under realistic cost and risk assumptions.

The strongest defensible contribution is the **temporal validation framework, evidence traceability, and decision-support architecture**, not a claim that multi-agent LLM always outperforms every baseline.

## Claim Boundary

Safe:

- The proposed module evaluates leader candidates within a given theme.
- Temporal RAG is used to restrict evidence to information available at the decision date.
- Multi-agent evaluation combines qualitative, quantitative, chart-derived, and risk perspectives.
- Backtesting supports the feasibility of temporal validation for theme-level candidate selection.
- Hybrid multi-agent results are regime-dependent: competitive in some period/horizon pairs, but not uniformly superior.
- Multi-theme paper trading is an application pipeline and operational extension.

Unsafe:

- The complete multi-theme paper-trading strategy has been backtested end-to-end.
- The system guarantees profitable trading.
- The result is validated for all market themes.
- Paper-trading return proves real trading performance.
- Multi-agent LLM beats every deterministic or technical baseline.
- All agents are LLM agents. In this project, the chart-oriented component also uses rule/formula-based price and volume features.
- Temporal RAG makes the validation completely leakage-free. The safer claim is that it reduces and audits future-information leakage by filtering evidence and price features by `as_of_date`.

## Evidence From Branch Review

**origin/main**

- Full-stack runtime exists: frontend, Spring backend, FastAPI AI server, internal trade-signal APIs, KIS/paper execution path.
- Multi-theme paper-trading configuration exists in `config/theme_trading.yaml`.
- Current multi-theme paper-trading evaluates multiple themes and then ranks selected leaders globally.
- `data/backtest_results/` exists in `origin/main` with 166 files and includes point-in-time validation artifacts.
- `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_report.md` shows that multi-agent performance is mixed:
  - Overall win rate against matching deterministic baselines is 30%.
  - 2025 and 2026Q1/recent checks do not support a broad short-horizon multi-agent superiority claim.
  - Long-horizon hybrid improves over deterministic in limited 2025/2026Q1 rows, but these are pilot/limited-period evidence.
- Therefore, use `origin/main` primarily for system integration, temporal backtest protocol, and paper-trading execution architecture.

**origin/ai-data-main**

- Richer research artifacts exist for comparison tables and technical baselines.
- Includes `backtesting/technical_baseline.py` and `backtesting/multi_agent_validation_status.py`.
- Includes 2025, 2026Q1, and recent 2026 Apr-May multi-agent runs.
- These are useful as supporting evidence, but the safest submission should treat 2025 as tuning/reference and 2026Q1/recent checks as limited-period evidence.
- `BACKTEST_COVERAGE_AUDIT.md` reports 8/8 coverage for the AI theme comparison axes: 2023, 2024, 2025, 2026Q1 crossed with short and long horizons.
- The final comparison table reports hybrid multi-agent at or above deterministic in 4/8 period-horizon pairs, and at or above the best technical baseline in 4/8 pairs.
- Additional uncontaminated four-agent validation exists, but results are not uniformly strong; use it as ablation/validation rigor rather than as a headline performance claim.

## Abstract Draft

산업 테마형 후보군에서는 뉴스, 공시, 커뮤니티 반응, 시계열 관측값이 비동기적으로 변화하므로 단일 지표만으로 동적 후보를 평가하기 어렵다. 본 연구는 동적 산업 테마 내 후보군을 대상으로 시점 제한형 검색 증강 생성(Temporal RAG)과 역할 분화형 Agentic AI 평가를 결합한 의사결정 검증 프레임워크를 제안한다. 제안 방법은 의사결정 시점 이전에 공개된 뉴스, DART 공시, 포럼 텍스트, 가격 데이터를 수집하고, Analyst, Quant, Chartist, Risk Manager 역할이 정성·정량·가격·위험 관점의 근거를 생성하도록 구성하였다. 이때 모든 역할을 LLM으로 구성하지 않고, 가격 기반 Chartist와 위험 조정 계층을 결합한 hybrid multi-perspective 구조로 설계하였다. 또한 미래 정보 누수를 줄이기 위해 각 리밸런싱 시점의 근거 문서와 가격 특징을 해당 시점 이전 데이터로 제한하였다. 실험은 AI 산업 테마를 대상으로 단기 및 장기 평가 조건을 구분하고, 결정론적 선별 모델, RSI, Bollinger Band, momentum 기반 기준모델, LLM-only 및 hybrid agent 모델과 비교하였다. 검증 결과, 제안 프레임워크는 시점별 근거 제한과 기준모델 비교를 체계화할 수 있었으며, hybrid agent 모델은 8개 기간-구간 비교 중 deterministic 기준 이상 4건, 최고 기술지표 기준 이상 4건으로 일부 국면에서 경쟁력을 보였다. 다만 모든 기간에서 일관된 우위를 보이지 않았으므로, 본 연구는 수익률 보장 모델이 아니라 동적 산업 데이터에 대한 근거 추적형, 위험 인지형 의사결정 보조 및 검증 방법론으로 제시한다. 향후 연구에서는 다중 테마 end-to-end 백테스팅과 장기 모의투자 로그 기반 운영 검증을 수행할 예정이다.

## Poster Storyline

1. **Problem**
   - Dynamic industry-theme candidate evaluation needs both text evidence and market signals.
   - Naive backtesting can leak future information.
   - Paper trading introduces execution constraints that differ from historical backtests.

2. **Proposed Framework**
   - Data: news, DART disclosures, forum text, chart/price rows, theme membership.
   - Temporal RAG: retrieve only evidence available before each decision date.
   - Hybrid role-specialized scoring: Analyst, Quant, rule/formula-based Chartist, Risk Manager.
   - Output: theme-level candidate rankings and risk-adjusted decision scores.

3. **Validation Design**
   - Scope: AI theme leader selection.
   - Horizons: short, 5 trading days; long, 60 trading days.
   - Comparators: deterministic baseline, RSI, Bollinger Band, momentum, LLM-only, hybrid.
   - Realism controls: transaction cost, slippage, market impact, volatility filter, breadth filter, trailing stop.

4. **Key Result**
   - Temporal validation and leakage control are the strongest current claims.
   - Deterministic and technical baselines establish the evaluation floor.
   - Hybrid multi-agent is competitive in some periods, but not uniformly superior:
     - at or above deterministic in 4/8 period-horizon pairs,
     - at or above the best technical baseline in 4/8 period-horizon pairs.
   - Full multi-theme results should be presented as limitations and future work.

5. **Operational Extension**
   - The current project includes a multi-theme paper-trading pipeline.
   - It evaluates multiple themes and applies order guards such as missing price, duplicate position, quantity zero, and LLM error.
   - This should be described as an execution feasibility layer, not as the primary backtested contribution.

6. **Conclusion**
   - Temporal RAG helps build a more defensible historical validation protocol.
   - Hybrid role-specialized scoring provides interpretable, regime-dependent decision evidence.
   - Full multi-theme backtesting and longer paper-trading logs remain next steps.

## Figures To Prepare

1. System architecture diagram:
   - Data sources -> Temporal RAG -> Multi-agent committee -> Risk adjustment -> Candidate ranking -> Paper-trading guard.

2. Validation protocol diagram:
   - as_of_date -> past-only evidence -> selection -> future holding-period evaluation.

3. Result chart:
   - Period/horizon comparison: deterministic vs hybrid vs LLM-only vs technical baselines.

4. Claim boundary diagram:
   - Backtest layer: theme-level selection.
   - Paper-trading layer: multi-theme operational execution.

## Work Needed Before June 26

1. Finalize abstract text for CDE text submission.
2. Create one provenance table for the existing results:
   - period,
   - strategy,
   - cost/slippage/market-impact assumption,
   - rebalance count,
   - position count,
   - artifact path.
3. Create a temporal leakage audit table for representative `as_of_date` samples:
   - latest retrieved document date <= `as_of_date`,
   - latest price feature date <= `as_of_date`,
   - future returns used only after selection.
4. Extract the 8-row period/horizon comparison table and make one clean result chart.
5. Prepare one architecture figure.
6. Run at least a few multi-theme paper/shadow sessions and summarize:
   - number of themes evaluated,
   - candidate count,
   - selected count,
   - order guard pass/reject count,
   - rejection reasons.
7. Keep paper-trading results as operational feasibility evidence, not return evidence.

## Sources

- 2026 CDE summer conference page: https://www.cde.or.kr/html/?pmode=inputList&smode=view&part=&intAcSeq=260
- 2025 CDE summer conference page: https://www.cde.or.kr/html/?pmode=inputOldList&smode=view&part=&intAcSeq=257
- 2025 CDE winter conference page: https://www.cde.or.kr/html/?pmode=inputOldList&smode=view&part=&intAcSeq=254
- Project branch evidence:
  - `origin/main`
  - `origin/ai-data-main`
  - `experiment_results/backtesting/ai_strategy_comparison/`
  - `config/theme_trading.yaml`
