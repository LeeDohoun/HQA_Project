# CDE 2026 Poster Topic And Storyline

Generated: 2026-06-17

## Decision

Use the topic below for the June 26 submission.

**Korean title**

동적 산업 테마 분석을 위한 Temporal RAG 기반 LLM 다중 에이전트 의사결정 프레임워크

**English title**

A Temporal RAG-Based Multi-Agent LLM Decision Framework for Dynamic Industry Theme Analysis

**Subtitle / scope**

AI 테마 내 주도 후보군 선별 및 시계열 검증 사례

This is the safest scope for the current project. The verified backtests evaluate candidate selection within a single theme, while the current paper-trading pipeline extends the selected leaders across multiple themes. Therefore, the poster should not claim that the full multi-theme paper-trading strategy has already been backtested. The CDE-facing title also avoids sounding like a stock-trading product.

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

This project proposes a temporal evidence retrieval and multi-agent decision framework for evaluating dynamic candidate groups inside an industry theme. The framework combines news, disclosures, forum text, and price-derived signals, restricts evidence by historical decision time to reduce future-information leakage, and compares deterministic, technical-indicator, LLM-only, and hybrid multi-agent selection strategies under realistic cost and risk assumptions.

The strongest defensible contribution is the **temporal validation framework and decision-support architecture**, not a claim that multi-agent LLM always outperforms every baseline.

## Claim Boundary

Safe:

- The proposed module evaluates leader candidates within a given theme.
- Temporal RAG is used to restrict evidence to information available at the decision date.
- Multi-agent evaluation combines qualitative, quantitative, chart-derived, and risk perspectives.
- Backtesting supports the feasibility of temporal validation for theme-level candidate selection.
- Some periods show multi-agent/hybrid competitiveness, but results are not uniformly superior.
- Multi-theme paper trading is an application pipeline and operational extension.

Unsafe:

- The complete multi-theme paper-trading strategy has been backtested end-to-end.
- The system guarantees profitable trading.
- The result is validated for all market themes.
- Paper-trading return proves real trading performance.
- Multi-agent LLM beats every deterministic or technical baseline.
- All agents are LLM agents. In this project, the chart-oriented component also uses rule/formula-based price and volume features.

## Evidence From Branch Review

**origin/main**

- Full-stack runtime exists: frontend, Spring backend, FastAPI AI server, internal trade-signal APIs, KIS/paper execution path.
- Multi-theme paper-trading configuration exists in `config/theme_trading.yaml`.
- Current multi-theme paper-trading evaluates multiple themes and then ranks selected leaders globally.
- `data/backtest_results/` exists in `origin/main` with 166 files and includes point-in-time validation artifacts.
- `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_report.md` shows that multi-agent performance is mixed:
  - 2025 and 2026Q1/recent checks do not support a broad short-horizon multi-agent superiority claim.
  - Long-horizon hybrid improves over deterministic in limited 2025/2026Q1 rows, but these are pilot/limited-period evidence.
- Therefore, use `origin/main` primarily for system integration, temporal backtest protocol, and paper-trading execution architecture.

**origin/ai-data-main**

- Richer research artifacts exist for comparison tables and technical baselines.
- Includes `backtesting/technical_baseline.py` and `backtesting/multi_agent_validation_status.py`.
- Includes 2025, 2026Q1, and recent 2026 Apr-May multi-agent runs.
- These are useful as supporting evidence, but the safest submission should treat 2025 as tuning/reference and 2026Q1/recent checks as limited-period evidence.
- `BACKTEST_COVERAGE_AUDIT.md` reports 8/8 coverage for the AI theme comparison axes: 2023, 2024, 2025, 2026Q1 crossed with short and long horizons.
- Additional uncontaminated four-agent validation exists, but results are not uniformly strong; use it as ablation/validation rigor rather than as a headline performance claim.

## Abstract Draft

산업 테마형 후보군에서는 뉴스, 공시, 커뮤니티 반응, 가격 추세가 동시에 변화하므로 단일 기술지표만으로 동적 후보를 평가하기 어렵다. 본 연구는 특정 산업 테마 내 후보군을 대상으로 시점 제한형 검색 증강 생성(Temporal RAG)과 다중 에이전트 평가를 결합한 의사결정 프레임워크를 제안한다. 제안 방법은 의사결정 시점 이전에 공개된 뉴스, DART 공시, 포럼 텍스트, 차트 데이터를 수집하고, Analyst, Quant, Chartist, Risk Manager 역할의 평가 모듈이 정성·정량·가격·위험 관점의 근거를 생성하도록 구성하였다. 또한 미래 정보 누수를 줄이기 위해 각 리밸런싱 시점의 근거 문서와 가격 특징을 해당 시점 이전 데이터로 제한하였다. 실험은 AI 테마를 대상으로 단기 및 장기 보유 조건을 구분하고, 결정론적 선별 모델, RSI, Bollinger Band, momentum 기반 기준모델, LLM-only 및 hybrid 다중 에이전트 모델과 비교하였다. 검증 결과, temporal validation 프로토콜은 시점별 근거 제한과 기준모델 비교를 체계화할 수 있었으며, 다중 에이전트 모델은 일부 기간에서 경쟁력 있는 성능과 해석 가능한 의사결정 근거를 제공하였다. 다만 모든 기간과 보유 조건에서 일관된 우위를 보이지는 않아, 본 연구는 다중 에이전트 구조를 수익률 보장 모델이 아닌 위험 인지형 의사결정 보조 및 검증 프레임워크로 제시한다. 향후 연구에서는 다중 테마 전체를 대상으로 한 end-to-end 백테스팅과 장기 모의투자 로그 기반 운영 검증을 수행할 예정이다.

## Poster Storyline

1. **Problem**
   - Dynamic industry-theme candidate evaluation needs both text evidence and market signals.
   - Naive backtesting can leak future information.
   - Paper trading introduces execution constraints that differ from historical backtests.

2. **Proposed Framework**
   - Data: news, DART disclosures, forum text, chart/price rows, theme membership.
   - Temporal RAG: retrieve only evidence available before each decision date.
   - Multi-agent scoring: Analyst, Quant, Chartist, Risk Manager.
   - Output: theme-level candidate rankings and risk-adjusted decision scores.

3. **Validation Design**
   - Scope: AI theme leader selection.
   - Horizons: short, 5 trading days; long, 60 trading days.
   - Comparators: deterministic baseline, RSI, Bollinger Band, momentum, LLM-only, hybrid.
   - Realism controls: transaction cost, slippage, market impact, volatility filter, breadth filter, trailing stop.

4. **Key Result**
   - Temporal validation and leakage control are the strongest current claims.
   - Deterministic and technical baselines establish the evaluation floor.
   - Multi-agent variants are competitive in some periods, but not uniformly superior.
   - Full multi-theme results should be presented as limitations and future work.

5. **Operational Extension**
   - The current project includes a multi-theme paper-trading pipeline.
   - It evaluates multiple themes and applies order guards such as missing price, duplicate position, quantity zero, and LLM error.
   - This should be described as an execution feasibility layer, not as the primary backtested contribution.

6. **Conclusion**
   - Temporal RAG helps build a more defensible historical validation protocol.
   - Multi-agent LLM scoring is promising for short-horizon theme leader selection.
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
2. Extract 2023-2024 short-horizon table and make one clean chart.
3. Add a mixed-result table from `origin/main` and `origin/ai-data-main` so the poster does not overclaim multi-agent superiority.
4. Prepare one architecture figure.
5. Run at least a few multi-theme paper/shadow sessions and summarize:
   - number of themes evaluated,
   - candidate count,
   - selected count,
   - order guard pass/reject count,
   - rejection reasons.
6. Keep paper-trading results as operational feasibility evidence, not return evidence.

## Sources

- 2026 CDE summer conference page: https://www.cde.or.kr/html/?pmode=inputList&smode=view&part=&intAcSeq=260
- 2025 CDE summer conference page: https://www.cde.or.kr/html/?pmode=inputOldList&smode=view&part=&intAcSeq=257
- 2025 CDE winter conference page: https://www.cde.or.kr/html/?pmode=inputOldList&smode=view&part=&intAcSeq=254
- Project branch evidence:
  - `origin/main`
  - `origin/ai-data-main`
  - `experiment_results/backtesting/ai_strategy_comparison/`
  - `config/theme_trading.yaml`
