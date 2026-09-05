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
