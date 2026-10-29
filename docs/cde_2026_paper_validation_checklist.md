# CDE 2026 Paper Validation Checklist

Generated: 2026-06-17

## Purpose

This document is the execution checklist for turning the current CDE topic into a defensible 3-page paper, poster, and 2-minute poster video.

Final paper stance:

**시점 제한형 RAG와 Agentic AI를 활용한 동적 산업 테마 후보군 검증 프레임워크**

The paper must claim a validation and decision-support framework, not a profitable automated trading model.

## Submission Deliverables

- Initial CDE submission: by 2026-06-26.
- Final modification/submission window: by 2026-07-03.
- Paper length target: about 3 pages.
- Poster: print poster presentation; use the downloaded CDE references in `cde_2026_refs/`.
- Video: about 2 minutes, mp4/YouTube upload expected, with QR code linkage when requested.
- Local templates:
  - `cde_2026_refs/2026_summer_paper_template.docx`
  - `cde_2026_refs/2026_summer_paper_template.pptx`
  - `cde_2026_refs/2026CDE_summer_poster.pdf`

## Claim Lock

Use this claim:

> 본 연구는 AI 산업 테마 내 후보군 선별 사례를 대상으로, 의사결정 시점 이전의 문서와 가격 특징만 사용하는 Temporal RAG 검증 프로토콜과 역할 분화형 Agentic AI 평가 구조를 제안한다. 핵심 기여는 수익률 우월성이 아니라 미래정보 누수를 줄인 비교 검증, 근거 추적성, 위험 인지형 의사결정 지원 구조이다.

Do not claim:

- Complete multi-theme paper-trading strategy has already been backtested end-to-end.
- Hybrid multi-agent always outperforms deterministic or technical baselines.
- Paper-trading return proves real performance.
- Temporal RAG is fully leakage-free.
- Every sub-agent is an LLM agent.

## Sequential Validation Plan

### 1. CDE Fit Validation

Status: mostly done.

Evidence:

- CDE 2026 topic fit: LLM & Agentic AI, AI & Advanced Applications, Modeling & Optimization, IoT/Bigdata Applications.
- Current topic document: `docs/cde_2026_topic_storyline.md`.
- Downloaded references: `cde_2026_refs/`.

Paper action:

- Keep CDE wording around "decision-support", "validation framework", "evidence traceability", and "Agentic AI".
- Avoid first-page wording that sounds like "stock auto-trading", "buy signal", or "profit-maximizing bot".

### 2. Scope Boundary Validation

Status: done, but must be repeated before final submission.

Evidence:

- Backtest scope: AI theme candidate selection.
- Operational scope: multi-theme paper/shadow trading pipeline.
- `config/theme_trading.yaml`
- `src/runner/multi_theme_leader_trading_runner.py`
- `src/runner/theme_paper_runner.py`

Paper action:

- Main experiment: AI industry theme only.
- Multi-theme paper trading: future work or operational feasibility only.
- Put this boundary in both the abstract and limitation section.

### 3. Temporal Leakage Audit

Status: required before paper draft freeze.

Evidence already in code:

- `backtesting/temporal_rag.py`
- `backtesting/leader_backtest.py`
- `tests/test_backtesting_temporal_rag.py`

Current execution status:

- Command attempted on 2026-06-17:

```powershell
python -m pytest tests\test_backtesting_temporal_rag.py tests\test_theme_paper_trading.py -q
```

- Result: blocked because the active Python environment does not have `pytest` installed.

Required next action:

```powershell
python -m pip install pytest
python -m pytest tests\test_backtesting_temporal_rag.py tests\test_theme_paper_trading.py -q
```

Paper artifact to create:

| Audit item | Required evidence |
|---|---|
| Document cutoff | latest retrieved `published_at` <= `as_of_date` |
| Price cutoff | latest feature date <= `as_of_date` |
| Evaluation separation | future returns used only after selection |
| Membership caveat | AI theme membership is local-corpus inferred unless official historical membership is added |

### 4. Result Provenance Table

Status: required.

Use these source files:

- `data/backtest_results/validation/README.md`
- `data/backtest_results/validation/membership_risk_sweep_w_top357_h357/sweep-ai.csv`
- `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_summary.csv`
- `experiment_results/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.csv`

Paper artifact to create:

| Period | Strategy | Rebalance | Hold | Cost assumptions | Rebalances | Positions | Source |
|---|---|---:|---:|---|---:|---:|---|
| 2023 | deterministic/risk baseline | W | 5 | transaction/slippage/impact documented | TBD | TBD | validation csv |
| 2024 | deterministic/risk baseline | W | 5 | transaction/slippage/impact documented | TBD | TBD | validation csv |
| 2025 | deterministic/risk baseline | W | 5 | transaction/slippage/impact documented | TBD | TBD | validation csv |
| 2026Q1 | deterministic/risk baseline | W | 5 | transaction/slippage/impact documented | TBD | TBD | validation csv |
| 2023-2026Q1 | hybrid multi-agent comparison | W/M | 5/60 | 15bp cost, 5bp slippage, 5bp market impact | from csv | from csv | comparison csv |

### 5. Deterministic And Risk Baseline Validation

Status: available, needs paper-ready chart.

Strongest baseline evidence:

- `W / top5 / hold5 + point-in-time membership + breadth40 + stock risk filters`
- Minimum excess return across 2023, 2024, 2025, 2026Q1: 16.54%.
- Average excess return: 40.57%.
- Worst MDD: -27.31%.

Paper use:

- Use as the stable validation floor.
- Do not hide that this deterministic/risk baseline is often stronger than LLM variants.
- This is actually useful: it shows the framework compares against non-trivial baselines.

### 6. Multi-Agent Comparison Validation

Status: available, mixed result.

Evidence:

- `data/backtest_results/proof/qwen3_gptoss/ai_short_long_validation_report.md`
- `experiment_results/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.md`

Locked result statement:

- Hybrid multi-agent is at or above deterministic in 4/8 period-horizon pairs.
- Hybrid multi-agent is at or above the best technical baseline in 4/8 period-horizon pairs.
- `data/backtest_results/proof/qwen3_gptoss` reports overall weak performance against matching deterministic baselines, with long-horizon promise in limited rows and weak short-horizon evidence.

Paper use:

- Say "regime-dependent" and "interpretable evidence".
- Do not say "superior".

### 7. Paper/Shadow Trading Operational Validation

Status: required if mentioned beyond future work.

Execution command:

```powershell
python scripts\run_theme_paper_trading.py --once
```

Expected output files:

- `data/paper_trading/decision_journal.jsonl`
- `data/paper_trading/position_snapshots.jsonl`
- `data/paper_trading/positions.json`
- order logs under the configured orders directory, written as `orders.jsonl`

Run requirement before paper:

- Run 3-5 shadow sessions.
- Summarize only operational metrics:
  - evaluated theme count,
  - loaded theme count,
  - evidence theme count,
  - order intent count,
  - guard pass count,
  - guard reject count,
  - reject reasons.

Paper use:

- Operational feasibility only.
- No return/performance claim unless a longer run log exists.

### 8. Limitation Validation

Status: must be explicit.

Mandatory limitations:

- Single-theme backtest scope: AI industry theme.
- Theme membership is inferred from local corpus unless official historical membership is added.
- Multi-agent result is mixed, not uniformly superior.
- Paper/shadow trading logs do not prove real execution quality.
- Real-time issues such as order latency, spread, data correction, and fill failure remain future work.

## Paper Structure

Target length: about 3 pages.

1. Title and Abstract
   - Use the locked claim.
   - Mention mixed result honestly.

2. Introduction
   - Problem: dynamic industry candidate evaluation uses asynchronous text and time-series evidence.
   - Risk: naive backtesting leaks future information.
   - Contribution: Temporal RAG validation protocol plus role-specialized Agentic AI comparison.

3. Method
   - Data sources: news, DART, forum, price/chart, theme membership.
   - Temporal RAG: as-of evidence filtering.
   - Agent roles: Analyst, Quant, rule/formula Chartist, Risk Manager.
   - Output: candidate ranking and risk-adjusted score.

4. Experimental Design
   - Scope: AI theme.
   - Horizons: short 5 trading days, long 60 trading days.
   - Comparators: deterministic, RSI, Bollinger Band, momentum, LLM-only, hybrid.
   - Realism: cost, slippage, market impact, breadth filter, volatility filter, trailing stop.

5. Results
   - Show deterministic/risk baseline robustness.
   - Show 8-row multi-agent comparison.
   - State mixed result clearly.

6. Discussion And Limitations
   - Explain why mixed result is useful.
   - Separate backtest layer from paper-trading layer.

7. Conclusion
   - Temporal evidence control and decision traceability are the contribution.
   - Multi-theme end-to-end validation is future work.

## Required Figures And Tables

Minimum set:

1. Architecture figure
   - data sources -> Temporal RAG -> role-specialized agents -> risk adjustment -> candidate ranking -> optional paper-trading guard.

2. Temporal validation figure
   - `as_of_date` -> past-only evidence -> selection -> future holding-period evaluation.

3. Result chart
   - 8 period-horizon pairs comparing deterministic, best technical, LLM-only, and hybrid.

4. Provenance table
   - period, strategy, cost assumptions, rebalance count, position count, artifact path.

5. Claim boundary table
   - validated backtest claim vs operational/future-work claim.

Optional:

- Shadow trading operational metric table, only if 3-5 runs are completed.

## Date-Based Execution Plan

### 2026-06-17

- Lock topic and story.
- Add this validation checklist.
- Confirm test blocker: `pytest` missing.

### 2026-06-18

- Install/run validation tests or record reproducible blocker.
- Build result provenance table.
- Build temporal leakage audit table.

### 2026-06-19

- Create architecture figure.
- Create validation protocol figure.
- Create 8-row result chart.

### 2026-06-20 to 2026-06-21

- Draft 3-page paper in CDE template.
- Keep the result section short and honest.

### 2026-06-22

- Internal review pass:
  - remove auto-trading wording,
  - remove unsupported performance wording,
  - check all numbers against artifact paths.

### 2026-06-23

- Build poster layout.
- Convert paper figures into poster figures.

### 2026-06-24

- Write 2-minute video script.
- Record draft video.

### 2026-06-25

- Final proofreading.
- Verify PDF/docx/pptx export.
- Prepare upload checklist.

### 2026-06-26

- Submit initial CDE material.

### 2026-07-03

- Final modification deadline.

## Immediate Next Files To Create

- `docs/cde_2026_result_provenance_table.md`
- `docs/cde_2026_temporal_leakage_audit.md`
- `docs/cde_2026_paper_draft.md` or CDE `.docx` from template
- `docs/cde_2026_video_script.md`

