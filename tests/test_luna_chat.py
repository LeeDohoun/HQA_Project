import asyncio
import json

import httpx2
import openai
import pytest
from pydantic import BaseModel

from src.agents.llm_config import get_quant_llm
from src.utils.llm_budget import get_llm_budget
from src.utils.luna_chat import LLMInputLimitError, LLMResponseError


class Analysis(BaseModel):
    verdict: str


@pytest.fixture
def luna(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setenv("HQA_LLM_BUDGET_PATH", str(tmp_path / "spend.sqlite3"))
    monkeypatch.setenv("HQA_LLM_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("HQA_LLM_OPERATING_TARGET_USD", "90")
    return get_quant_llm()


def response_body(*, text='{"verdict":"HOLD"}', status="completed"):
    return {
        "id": "resp_offline", "object": "response", "created_at": 1,
        "model": "gpt-5.6-luna", "status": status, "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{"id": "msg_offline", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}],
        "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                  "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 30},
                  "output_tokens_details": {"reasoning_tokens": 10}},
        "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
    }


def attach_transport(model, handler):
    transport = httpx2.MockTransport(handler)
    model.root_client = openai.OpenAI(api_key="offline-test-key", max_retries=0, http_client=httpx2.Client(transport=transport))
    model.root_async_client = openai.AsyncOpenAI(api_key="offline-test-key", max_retries=0, http_client=httpx2.AsyncClient(transport=transport))


def test_structured_output_counts_exact_schema_and_settles_usage(luna):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx2.Response(200, json=response_body())

    attach_transport(luna, handler)
    result = luna.with_structured_output(Analysis, method="json_schema", strict=True).invoke("Analyze the supplied data")
    assert result == Analysis(verdict="HOLD")
    assert [path for path, _ in calls] == ["/v1/responses/input_tokens", "/v1/responses"]
    assert calls[0][1]["text"]["format"] == calls[1][1]["text"]["format"]
    assert calls[1][1]["max_output_tokens"] == 1200
    assert calls[1][1]["reasoning"] == {"effort": "low"}
    assert get_llm_budget().snapshot()["spent_usd"] == 0.0000539
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_input_limit_rejects_before_generation(luna):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 12_001})

    attach_transport(luna, handler)
    with pytest.raises(LLMInputLimitError):
        luna.invoke("too much input")
    assert paths == ["/v1/responses/input_tokens"]
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_network_timeout_is_not_retried_and_reservation_is_retained(luna):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        raise httpx2.ReadTimeout("response not received", request=request)

    attach_transport(luna, handler)
    with pytest.raises(openai.APITimeoutError):
        luna.invoke("Analyze")
    assert paths.count("/v1/responses") == 1
    assert get_llm_budget().snapshot()["unresolved_requests"] == 1
    assert get_llm_budget().snapshot()["reserved_usd"] > 0


def test_incomplete_result_is_charged_but_not_accepted(luna):
    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx2.Response(200, json=response_body(status="incomplete"))

    attach_transport(luna, handler)
    with pytest.raises(LLMResponseError, match="incomplete"):
        luna.invoke("Analyze")
    assert get_llm_budget().snapshot()["spent_usd"] > 0
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_async_structured_output_uses_the_same_budget(luna):
    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx2.Response(200, json=response_body())

    attach_transport(luna, handler)
    result = asyncio.run(luna.with_structured_output(Analysis, method="json_schema", strict=True).ainvoke("Analyze"))
    assert result.verdict == "HOLD"
    assert get_llm_budget().snapshot()["spent_usd"] > 0


def test_schema_parse_failure_keeps_actual_charged_usage(luna):
    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx2.Response(200, json=response_body(text='{"unexpected":"HOLD"}'))

    attach_transport(luna, handler)
    with pytest.raises(ValueError):
        luna.with_structured_output(Analysis, method="json_schema", strict=True).invoke("Analyze")
    assert get_llm_budget().snapshot()["spent_usd"] > 0
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_provider_rate_rejection_is_not_retried_or_charged(luna):
    attempts = []

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        attempts.append(request.url.path)
        return httpx2.Response(429, json={"error": {"message": "rate limit", "type": "rate_limit_error"}})

    attach_transport(luna, handler)
    with pytest.raises(openai.RateLimitError):
        luna.invoke("Analyze")
    assert len(attempts) == 1
    assert get_llm_budget().snapshot()["spent_usd"] == 0
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


@pytest.mark.parametrize("kwargs", [
    {"max_output_tokens": 2000}, {"model": "other-model"},
    {"service_tier": "priority"}, {"tools": [{"type": "web_search"}]},
    {"extra_body": {"max_output_tokens": 10_000}},
])
def test_binding_cannot_bypass_model_or_budget_limits(luna, kwargs):
    attach_transport(luna, lambda request: pytest.fail("invalid configuration must not reach the API"))
    with pytest.raises(ValueError):
        luna.bind(**kwargs).invoke("Analyze")


def test_risk_manager_decision_round_trips_through_the_responses_api_offline(monkeypatch, tmp_path):
    """The production RiskManager request with the real AccountDecision schema: the strict
    schema conversion and the wrapper's request checks accept it, and a plan that breaks a
    cross-field rule comes back set aside instead of failing the whole decision."""
    from datetime import datetime, timedelta, timezone

    from src.agents.llm_config import get_risk_manager_llm
    from src.runner.analysis_contracts import AccountDecision

    for name, value in {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5.6-luna", "OPENAI_API_KEY": "offline-test-key",
                        "HQA_LLM_BUDGET_PATH": str(tmp_path / "spend.sqlite3")}.items():
        monkeypatch.setenv(name, value)
    model = get_risk_manager_llm()
    now = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)
    held = {"stock_code": "000001", "stock_name": "Stock 000001", "action": "HOLD", "holding_quantity": 4,
            "confidence": 70, "risk_level": "MEDIUM", "position_size_pct": 0.0, "entry_price": None,
            "stop_loss_price": 95.0, "take_profit_price": None,
            "entry_valid_until": (now + timedelta(minutes=10)).isoformat(), "planned_exit_at": (now + timedelta(days=5)).isoformat(),
            "condition_payload": {"schema_version": 2, "entry_conditions": [], "reduce_conditions": [], "invalidation_conditions": [],
                                  "exit_conditions": [{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": 95.0}]}]},
            "citations": [{"source_id": "quote:000001", "claim": "current quote"}], "reasoning": "keep the stop"}
    broken = {**held, "stock_code": "000002", "stock_name": "Stock 000002", "holding_quantity": 0,
              "planned_exit_at": held["entry_valid_until"]}
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 9000})
        return httpx2.Response(200, json=response_body(text=json.dumps({"plans": [held, broken], "reasoning": "r"})))

    attach_transport(model, handler)
    decision = model.with_structured_output(AccountDecision, method="json_schema", strict=True).invoke(
        [("system", "RiskManager instructions"), ("human", "{}")])
    assert [plan.stock_code for plan in decision.plans] == ["000001"]
    assert [row["stock_code"] for row in decision.invalid_plans] == ["000002"]
    request = calls[1][1]
    assert request["text"]["format"]["type"] == "json_schema" and request["text"]["format"]["strict"] is True
    assert request["max_output_tokens"] == 12000 and request["reasoning"] == {"effort": "medium"}


@pytest.mark.parametrize("getter", ["get_analyst_llm", "get_quant_llm", "get_chartist_llm"])
def test_specialist_results_round_trip_through_the_responses_api_offline(getter, monkeypatch, tmp_path):
    from src.agents import llm_config
    from src.runner.analysis_contracts import SpecialistResult

    for name, value in {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5.6-luna", "OPENAI_API_KEY": "offline-test-key",
                        "HQA_LLM_BUDGET_PATH": str(tmp_path / "spend.sqlite3")}.items():
        monkeypatch.setenv(name, value)
    model = getattr(llm_config, getter)()
    result = {"stock_code": "000001", "role": "quant", "score": 61.5, "confidence": 70, "thesis": "근거 요약",
              "risks": ["수요 둔화"], "citations": [{"source_id": "fin:000001", "claim": "영업이익 증가"}], "data_gaps": []}
    calls = []

    def handler(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 3000})
        return httpx2.Response(200, json=response_body(text=json.dumps(result, ensure_ascii=False)))

    attach_transport(model, handler)
    parsed = model.with_structured_output(SpecialistResult, method="json_schema", strict=True).invoke(
        [("system", "specialist"), ("human", "{}")])
    assert parsed == SpecialistResult.model_validate(result)
    assert calls[1][1]["max_output_tokens"] == 1200 and calls[1][1]["reasoning"] == {"effort": "low"}
