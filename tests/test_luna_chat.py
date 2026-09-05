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
