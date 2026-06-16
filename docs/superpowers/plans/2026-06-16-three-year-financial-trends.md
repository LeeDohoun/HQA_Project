# Three Year Financial Trend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Quant financial analysis from latest annual snapshot only to recent three-year annual financial trends.

**Architecture:** Keep DART collection annual-only for v1, but collect multiple annual `FinancialSnapshot` rows. Store the rows in existing JSONL outputs and let `QuantitativeAnalyzer` derive trend metrics from the latest three annual snapshots.

**Tech Stack:** Python dataclasses, JSONL storage, pytest.

---

### Task 1: DART Annual Series Collection

**Files:**
- Modify: `src/ingestion/dart_financials.py`
- Test: `tests/test_dart_financials.py`

- [ ] Add a failing test for `collect_annual_series(..., years=3)` returning 2025, 2024, 2023 snapshots.
- [ ] Implement `collect_annual_series` with current annual report code `11011` only.
- [ ] Keep `collect_latest_annual` backward compatible by returning the first series item.

### Task 2: Persist Multiple Financial Snapshots

**Files:**
- Modify: `src/ingestion/services.py`
- Test: `tests/test_ingestion_pipeline_sources.py`

- [ ] Add a failing test proving financial collection saves multiple snapshots.
- [ ] Replace single-snapshot collection with `collect_annual_series(..., years=3)`.
- [ ] Preserve source failure handling when no snapshots are returned.

### Task 3: Quant Three-Year Trend Metrics

**Files:**
- Modify: `src/tools/finance_tool.py`
- Modify: `src/agents/quant.py`
- Test: add or update Quant/finance tests.

- [ ] Add a failing test showing latest three snapshots produce revenue CAGR, operating profit CAGR, YoY changes, and margin trend.
- [ ] Add fields to `QuantitativeAnalysis` and pass them into `QuantScore`.
- [ ] Adjust growth score to use actual 3-year growth metrics when available, with ROE fallback retained.
- [ ] Include trend metrics in Quant prompt payload and reports.

### Verification

- [ ] Run `venv/bin/python -m pytest -q tests/test_dart_financials.py tests/test_ingestion_pipeline_sources.py tests/test_quant_financial_trends.py`.
- [ ] Run broader affected tests if available.
