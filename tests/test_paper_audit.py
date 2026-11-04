from concurrent.futures import ThreadPoolExecutor

import pytest

from src.tracing.paper_audit import PaperAudit, summarize_runtime


def test_audit_survives_restart_and_keeps_every_concurrent_observation(tmp_path):
    path = tmp_path / "audit.sqlite3"
    audit = PaperAudit(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda value: audit.append("llm_request", {"input": value}), range(50)))
    assert len(set(ids)) == 50
    assert len(PaperAudit(path).read("llm_request")) == 50


def test_audit_does_not_serialize_nonfinite_financial_results(tmp_path):
    with pytest.raises(ValueError):
        PaperAudit(tmp_path / "audit.sqlite3").append("analysis", {"price": float("nan")})


def test_evaluation_reports_rejected_work_and_missing_measurements():
    summary = summarize_runtime([])
    assert summary["analysis_p95_seconds"] is None
    assert summary["stock_completion_rate"] is None
    summary = summarize_runtime([
        {"kind": "analysis", "payload": {"specialist_stock_count": 20, "completed_stock_count": 10,
            "timings_ms": {"total": 125000}, "accounts": {"a": {"status": "completed"}, "b": {"status": "failed"}}}},
        {"kind": "monitor", "payload": {"slo_met": False, "elapsed_seconds": 35, "errors": [{"error": "stale"}]}},
    ])
    assert summary["stock_completion_rate"] == .5
    assert summary["valid_account_rate"] == .5
    assert summary["analysis_over_120_seconds"] == 1
    assert summary["monitor_slo_failures"] == 1


def test_evaluation_distinguishes_budget_rejections_and_schema_only_responses():
    summary = summarize_runtime([
        {"kind": "llm_request", "payload": {"holding_priority": True}},
        {"kind": "llm_response", "payload": {"validation": "schema_only"}},
        {"kind": "llm_failure", "payload": {"error_type": "LLMBudgetExceeded"}},
        {"kind": "llm_failure", "payload": {"error_type": "LLMResponseError"}},
    ])
    assert summary["holding_priority_requests"] == 1
    assert summary["budget_rejections"] == 1
    assert summary["llm_failures"] == 2
    assert summary["llm_schema_valid_responses"] == 1


RAG_VERSION = "hqa-fixed-dag-v8-backend-plan-rules+analyst-rag-stock-v1"


def analyst_turn(audit, stock, source_ids, score, *, items=None, cited=(), fail=False, version=None):
    """One analyst request and its response (or failure), as SharedAnalysisService records them."""
    payload = {"stock_code": stock, "stock_name": "x", "events": [], "source_ids": list(source_ids), "data_gaps": []}
    if items is not None:
        payload["retrieved_context"] = [{"source_id": item, "excerpt": "과거 수주 기사"} for item in items]
        payload["source_ids"] += list(items)
    request_id = audit.append("llm_request", {"role": "analyst", "prompt_version": version or (
        RAG_VERSION if items is not None else "hqa-fixed-dag-v8-backend-plan-rules"), "instructions": "Evaluate", "payload": payload})
    if fail:
        audit.append("llm_failure", {"request_id": request_id, "role": "analyst", "error_type": "Timeout", "error": "t"})
    else:
        citations = [{"source_id": source, "claim": "c"} for source in [*source_ids[:1], *cited]]
        audit.append("llm_response", {"request_id": request_id, "role": "analyst",
                                      "output": {"score": score, "citations": citations}})


def test_the_retrieval_report_counts_use_citations_cost_and_score_changes_on_unchanged_evidence(tmp_path):
    from scripts.paper_rag_report import load_events, main
    from src.tracing.paper_audit import summarize_analyst_retrieval

    path = tmp_path / "audit.sqlite3"
    audit = PaperAudit(path)
    analyst_turn(audit, "000001", ["event:a"], 50.0)
    analyst_turn(audit, "000002", ["event:b"], 40.0)
    analyst_turn(audit, "000002", ["event:b2"], 70.0)                    # the evidence changed: a different baseline
    audit.append("llm_request", {"role": "quant", "prompt_version": "v", "instructions": "", "payload": {}})
    analyst_turn(audit, "000001", ["event:a"], 60.0, items=["retrieved:1", "retrieved:2"], cited=["retrieved:1"])
    analyst_turn(audit, "000002", ["event:b"], 42.0, items=[])
    analyst_turn(audit, "000003", ["event:c"], 55.0, items=["retrieved:3"])   # no baseline: no pair
    analyst_turn(audit, "000004", ["event:d"], 0.0, items=["retrieved:4"], fail=True)
    audit.append("analysis", {"errors": [{"stock_code": "000005", "stage": "analyst_retrieval", "error": "x"},
                                         {"stock_code": "000005", "stage": "quant", "error": "y"}]})
    summary = summarize_analyst_retrieval(load_events(path))
    rag = summary["by_prompt_version"][RAG_VERSION]
    assert {key: rag[key] for key in ("requests", "responses", "failures", "with_items", "items", "cited_requests",
                                      "cited_items")} == {"requests": 4, "responses": 3, "failures": 1, "with_items": 3,
                                                          "items": 4, "cited_requests": 1, "cited_items": 1}
    assert rag["mean_retrieved_tokens"] > 0
    assert rag["mean_input_tokens"] > summary["by_prompt_version"]["hqa-fixed-dag-v8-backend-plan-rules"]["mean_input_tokens"]
    assert summary["score_change_vs_same_evidence"] == {
        "with_items": {"pairs": 1, "mean": 10.0, "mean_absolute": 10.0},
        "without_items": {"pairs": 1, "mean": 2.0, "mean_absolute": 2.0}}
    assert summary["retrieval_errors"] == 1
    later = summarize_analyst_retrieval(load_events(path), since="9999")
    assert later["by_prompt_version"] == {} and later["score_change_vs_same_evidence"]["with_items"]["pairs"] == 0
    assert main(["--audit", str(path)]) == 0
