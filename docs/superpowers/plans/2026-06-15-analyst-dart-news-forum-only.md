# Analyst DART News Forum Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict `AnalystAgent` to use only the currently available DART, news, and forum RAG sources.

**Architecture:** Keep the existing `AnalystAgent` public API and result objects stable, but change its internal retrieval tools and research phases to stop depending on `report`, `general_news`, web fallbacks, and internal chart placeholders. Quant, Chartist, ingestion, and market-data pipelines remain untouched.

**Tech Stack:** Python, pytest, existing `EvidenceSearchTool`, canonical RAG index.

---

### Task 1: Update Analyst Source Filters

**Files:**
- Modify: `src/agents/analyst.py`
- Test: `tests/test_canonical_rag.py`

- [x] **Step 1: Write the failing test**

Update the analyst source-filter test so `evidence_tool_reports`, `evidence_tool_news`, `evidence_tool_policy`, and `evidence_tool_industry` expose only `dart`, `news`, and `forum` in combinations relevant to each phase.

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_canonical_rag.py::TestCanonicalRAG::test_report_tool_filters_sources -q`

Expected: FAIL while `report` or `general_news` still appears in analyst tool source filters.

- [x] **Step 3: Write minimal implementation**

Change `AnalystAgent.__init__`:

```python
self.evidence_tool_reports = EvidenceSearchTool(top_k=5, source_types=["dart", "news"], intent="investment")
self.evidence_tool_news = EvidenceSearchTool(top_k=5, source_types=["news", "forum"], intent="sentiment")
self.evidence_tool_policy = EvidenceSearchTool(top_k=5, source_types=["dart", "news"], intent="policy")
self.evidence_tool_industry = EvidenceSearchTool(top_k=5, source_types=["dart", "news", "forum"], intent="industry")
```

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_canonical_rag.py::TestCanonicalRAG::test_report_tool_filters_sources -q`

Expected: PASS.

### Task 2: Remove Analyst Web And Chart Fallbacks

**Files:**
- Modify: `src/agents/analyst.py`

- [x] **Step 1: Remove optional web-search dependency**

Remove `WebSearchTool`, `NewsSearchTool`, and `WEB_SEARCH_AVAILABLE` setup from `analyst.py`.

- [x] **Step 2: Make research DART/news/forum only**

Remove `_analyze_charts()` from the active `research()` flow and set chart fields to empty defaults. Rewrite `_search_reports`, `_search_news`, `_search_policy`, and `_search_industry` so each uses only its configured `EvidenceSearchTool` and returns an explicit RAG failure string when no indexed DART/news/forum result exists.

- [x] **Step 3: Keep output compatibility**

Do not remove `ResearchResult.chart_analysis`, `AnalystScore.image_analysis`, or `report_summary`; leave them as stable output fields for callers.

### Task 3: Verify And Clean References

**Files:**
- Modify: `README.md` if wording needs a small clarification
- Modify: `docs/superpowers/plans/2026-06-15-analyst-dart-news-forum-only.md`

- [x] **Step 1: Search for removed analyst dependencies**

Run: `rg -n "WEB_SEARCH_AVAILABLE|WebSearchTool|NewsSearchTool|general_news|source_types=\\[\\\"report|source_types=\\[.*report|_analyze_charts|웹 검색 폴백" src/agents/analyst.py tests/test_canonical_rag.py`

Expected: no active analyst dependency on web fallback, `general_news`, `report`, or `_analyze_charts`.

- [x] **Step 2: Run focused validation**

Run: `python -m py_compile src/agents/analyst.py`

Run: `PYTHONPATH=. pytest tests/test_canonical_rag.py tests/test_runtime_integration.py -q`

Expected: tests pass.
