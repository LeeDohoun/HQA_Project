import asyncio
import json
import logging

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from src.agents.llm_config import get_quant_llm
from src.utils.llm_budget import LLMBudgetAccountingError, get_llm_budget
from src.utils.llm_errors import LLMInputLimitError, LLMResponseError

CLAUDE_ENV = ("HQA_CLAUDE_MODEL", "HQA_CLAUDE_FALLBACKS", "HQA_CLAUDE_QUANT_MODEL", "HQA_CLAUDE_QUANT_EFFORT",
              "HQA_CLAUDE_RISK_MANAGER_MODEL", "HQA_CLAUDE_RISK_MANAGER_EFFORT", "HQA_LLM_TIMEOUT_SECONDS",
              "HQA_LLM_QUANT_TIMEOUT_SECONDS", "HQA_LLM_QUANT_MAX_OUTPUT_TOKENS", "HQA_LLM_RISK_MANAGER_MAX_OUTPUT_TOKENS",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")


class Analysis(BaseModel):
    verdict: str


@pytest.fixture
def claude_env(monkeypatch, tmp_path):
    for name in CLAUDE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    monkeypatch.setenv("HQA_LLM_BUDGET_PATH", str(tmp_path / "spend.sqlite3"))
    monkeypatch.setenv("HQA_LLM_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("HQA_LLM_OPERATING_TARGET_USD", "90")
    return monkeypatch


@pytest.fixture
def claude(claude_env):
    return get_quant_llm()


def usage(input_tokens=100, output_tokens=30, thinking=10, **extra):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0, "output_tokens_details": {"thinking_tokens": thinking}, **extra}


def message_body(*, text='{"verdict":"HOLD"}', stop_reason="end_turn", model="claude-opus-5-5", content=None,
                 stop_details=None, usage_body=None):
    blocks = [{"type": "thinking", "thinking": "", "signature": "offline"}, {"type": "text", "text": text}]
    return {"id": "msg_offline", "type": "message", "role": "assistant", "model": model,
            "content": blocks if content is None else content, "stop_reason": stop_reason, "stop_sequence": None,
            "stop_details": stop_details, "usage": usage_body or usage()}


def attach(model, handler):
    model._client = anthropic.Anthropic(api_key="offline-test-key", base_url="https://api.anthropic.com", max_retries=0,
                                        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def recording(calls, *, counted=100, body=None, status=200):
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": counted})
        return httpx2.Response(status, json=body if body is not None else message_body())
    return handler


def sent(request):
    return json.loads(request.content)


def test_structured_output_counts_the_sent_schema_and_settles_usage(claude):
    calls = []
    attach(claude, recording(calls))
    result = claude.with_structured_output(Analysis, method="json_schema", strict=True).invoke(
        [("system", "quant instructions"), ("human", "Analyze the supplied data")])
    assert result == Analysis(verdict="HOLD")
    assert [request.url.path for request in calls] == ["/v1/messages/count_tokens", "/v1/messages"]
    count, create = sent(calls[0]), sent(calls[1])
    assert count["output_config"] == {"format": create["output_config"]["format"]}
    assert create["output_config"]["effort"] == "low" and create["max_tokens"] == 4000
    assert create["system"] == "quant instructions"
    assert create["messages"] == [{"role": "user", "content": "Analyze the supplied data"}]
    assert "thinking" not in create and "temperature" not in create
    # Opus 5.5 ships with the server-side fallback for a classifier refusal.
    assert create["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in calls[1].headers["anthropic-beta"]
    assert calls[1].headers["x-api-key"] == "offline-test-key" and "authorization" not in calls[1].headers
    snapshot = get_llm_budget().snapshot()
    assert snapshot["spent_usd"] == 0.001  # 100 x $4/M + 30 x $20/M on Claude Opus 5.5
    assert snapshot["reserved_usd"] == 0 and snapshot["models"] == ["claude-opus-5-5"]


def test_plain_invoke_returns_the_text_with_usage_and_rate_limits(claude):
    def handler(request):
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 100})
        return httpx2.Response(200, json=message_body(text="요약"), headers={
            "anthropic-ratelimit-input-tokens-limit": "30000", "anthropic-ratelimit-requests-remaining": "49",
            "request-id": "req_offline"})

    attach(claude, handler)
    message = claude.invoke("Summarize")
    assert message.content == "요약"
    assert message.usage_metadata["input_tokens"] == 100 and message.usage_metadata["output_tokens"] == 30
    assert message.usage_metadata["output_token_details"] == {"reasoning": 10}
    assert message.response_metadata["served_model"] == "claude-opus-5-5"
    assert message.response_metadata["fallback_served"] is False
    assert message.response_metadata["rate_limits"] == {"input-tokens-limit": "30000", "requests-remaining": "49"}
    assert message.response_metadata["provider_request_id"] == "req_offline"


def test_input_limit_rejects_before_generation(claude):
    calls = []
    attach(claude, recording(calls, counted=12_001))
    with pytest.raises(LLMInputLimitError):
        claude.invoke("too much input")
    assert [request.url.path for request in calls] == ["/v1/messages/count_tokens"]
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_network_timeout_is_not_retried_and_reservation_is_retained(claude):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 100})
        raise httpx2.ReadTimeout("response not received", request=request)

    attach(claude, handler)
    with pytest.raises(anthropic.APITimeoutError):
        claude.invoke("Analyze")
    assert paths.count("/v1/messages") == 1
    assert get_llm_budget().snapshot()["unresolved_requests"] == 1
    assert get_llm_budget().snapshot()["reserved_usd"] > 0


def test_output_limit_stop_is_charged_but_not_accepted(claude):
    attach(claude, recording([], body=message_body(text='{"verdict":', stop_reason="max_tokens")))
    with pytest.raises(LLMResponseError, match="output limit"):
        claude.with_structured_output(Analysis).invoke("Analyze")
    assert get_llm_budget().snapshot()["spent_usd"] > 0
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_refusal_is_charged_and_reported_with_its_category(claude):
    refusal = message_body(content=[], stop_reason="refusal", usage_body=usage(output_tokens=0, thinking=0),
                           stop_details={"type": "refusal", "category": "cyber", "explanation": None})
    attach(claude, recording([], body=refusal))
    with pytest.raises(LLMResponseError, match="declined.*cyber"):
        claude.with_structured_output(Analysis).invoke("Analyze")
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_fallback_attempts_are_priced_at_each_models_rates(claude, caplog):
    served = message_body(
        model="claude-opus-5",
        content=[{"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"},
                  "trigger": {"type": "refusal", "category": None}},
                 {"type": "text", "text": '{"verdict":"HOLD"}'}],
        usage_body=usage(iterations=[
            {"type": "message", "model": "claude-opus-5-5", "input_tokens": 100, "output_tokens": 5,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 100, "output_tokens": 30,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        ]),
    )
    attach(claude, recording([], body=served))
    with caplog.at_level(logging.WARNING, logger="src.utils.claude_chat"):
        result = claude.with_structured_output(Analysis).invoke("Analyze")
    assert result.verdict == "HOLD"
    assert "answered by claude-opus-5" in caplog.text
    # Declined attempt on Opus 5.5 (100 x $4/M + 5 x $20/M) plus the Opus 5 answer (100 x $5/M + 30 x $25/M).
    snapshot = get_llm_budget().snapshot()
    assert snapshot["spent_usd"] == 0.00175 and snapshot["reserved_usd"] == 0 and not snapshot["accounting_blocked"]


def test_unpriced_fallback_model_keeps_the_reservation_for_review(claude):
    served = message_body(model="claude-unlisted", usage_body=usage(iterations=[
        {"type": "fallback_message", "model": "claude-unlisted", "input_tokens": 100, "output_tokens": 30,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}]))
    attach(claude, recording([], body=served))
    with pytest.raises(LLMBudgetAccountingError, match="price"):
        claude.invoke("Analyze")
    assert [row["state"] for row in get_llm_budget().unresolved()] == ["unknown"]


def test_schema_validation_failure_keeps_actual_charged_usage(claude):
    attach(claude, recording([], body=message_body(text='{"unexpected":"HOLD"}')))
    with pytest.raises(ValueError):
        claude.with_structured_output(Analysis).invoke("Analyze")
    assert get_llm_budget().snapshot()["spent_usd"] > 0
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


@pytest.mark.parametrize("status, error", [(429, anthropic.RateLimitError), (529, anthropic.OverloadedError)])
def test_rejections_before_generation_are_not_retried_or_charged(claude, status, error):
    attempts = []

    def handler(request):
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 100})
        attempts.append(request.url.path)
        return httpx2.Response(status, json={"type": "error", "error": {"type": "rate_limit_error", "message": "busy"}})

    attach(claude, handler)
    with pytest.raises(error):
        claude.invoke("Analyze")
    assert len(attempts) == 1
    assert get_llm_budget().snapshot()["spent_usd"] == 0
    assert get_llm_budget().snapshot()["reserved_usd"] == 0


def test_exhausted_api_credits_are_a_budget_rejection_that_costs_nothing(claude):
    from src.utils.llm_budget import LLMBudgetExceeded

    message = ("Your credit balance is too low to access the Anthropic API. "
               "Please go to Plans & Billing to upgrade or purchase credits.")
    attach(claude, recording([], status=400, body={"type": "error",
                                                    "error": {"type": "invalid_request_error", "message": message}}))
    with pytest.raises(LLMBudgetExceeded, match="credit balance"):
        claude.invoke("Analyze")
    snapshot = get_llm_budget().snapshot()
    assert snapshot["spent_usd"] == 0 and snapshot["reserved_usd"] == 0


def test_server_error_keeps_the_reservation_until_reconciled(claude):
    attach(claude, recording([], status=500, body={"type": "error", "error": {"type": "api_error", "message": "x"}}))
    with pytest.raises(anthropic.InternalServerError):
        claude.invoke("Analyze")
    assert get_llm_budget().snapshot()["unresolved_requests"] == 1


@pytest.mark.parametrize("kwargs", [
    {"max_tokens": 4001}, {"model": "claude-haiku-5-5"}, {"tools": [{"type": "web_search_20260209"}]},
    {"extra_body": {"max_tokens": 10_000}}, {"service_tier": "priority"}, {"thinking": {"type": "disabled"}},
])
def test_binding_cannot_bypass_model_or_budget_limits(claude, kwargs):
    attach(claude, lambda request: pytest.fail("invalid configuration must not reach the API"))
    with pytest.raises(ValueError):
        claude.bind(**kwargs).invoke("Analyze")


def test_requests_must_end_with_the_user(claude):
    attach(claude, lambda request: pytest.fail("a prefill must not reach the API"))
    with pytest.raises(ValueError, match="user message"):
        claude.invoke([("human", "Analyze"), ("ai", '{"verdict":')])


def test_async_structured_output_uses_the_same_budget(claude):
    attach(claude, recording([]))
    result = asyncio.run(claude.with_structured_output(Analysis, method="json_schema", strict=True).ainvoke("Analyze"))
    assert result.verdict == "HOLD"
    assert get_llm_budget().snapshot()["spent_usd"] > 0


def test_environment_cannot_redirect_the_key_or_add_credentials(claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "unrelated-token")
    client = claude.client
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert client.auth_token is None and client.max_retries == 0


def test_haiku_runs_without_server_side_fallback(claude_env):
    claude_env.setenv("HQA_CLAUDE_QUANT_MODEL", "claude-haiku-5-5")
    model = get_quant_llm()
    calls = []
    attach(model, recording(calls, body=message_body(model="claude-haiku-5-5")))
    assert model.with_structured_output(Analysis).invoke("Analyze").verdict == "HOLD"
    create = sent(calls[1])
    assert create["model"] == "claude-haiku-5-5" and "fallbacks" not in create
    assert "anthropic-beta" not in calls[1].headers
    assert get_llm_budget().snapshot()["spent_usd"] == 0.000025  # 100 x $0.10/M + 30 x $0.50/M


def test_risk_manager_decision_round_trips_through_the_messages_api_offline(claude_env):
    """The production RiskManager request with the real AccountDecision schema: the SDK schema
    conversion accepts it, and a plan that breaks a cross-field rule comes back set aside
    instead of failing the whole decision."""
    from datetime import datetime, timedelta, timezone

    from src.agents.llm_config import get_risk_manager_llm
    from src.runner.analysis_contracts import AccountDecision

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
    attach(model, recording(calls, counted=9000, body=message_body(text=json.dumps({"plans": [held, broken], "reasoning": "r"}))))
    decision = model.with_structured_output(AccountDecision, method="json_schema", strict=True).invoke(
        [("system", "RiskManager instructions"), ("human", "{}")])
    assert [plan.stock_code for plan in decision.plans] == ["000001"]
    assert [row["stock_code"] for row in decision.invalid_plans] == ["000002"]
    request = sent(calls[1])
    assert request["max_tokens"] == 16000 and request["output_config"]["effort"] == "medium"
    schema = json.dumps(request["output_config"]["format"]["schema"])
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert not any(f'"{key}"' in schema for key in ("minLength", "maxLength", "minimum", "maximum", "pattern", "maxItems"))


@pytest.mark.parametrize("getter", ["get_analyst_llm", "get_quant_llm", "get_chartist_llm"])
def test_specialist_results_round_trip_through_the_messages_api_offline(getter, claude_env):
    from src.agents import llm_config
    from src.runner.analysis_contracts import SpecialistResult

    model = getattr(llm_config, getter)()
    result = {"stock_code": "000001", "role": "quant", "score": 61.5, "confidence": 70, "thesis": "근거 요약",
              "risks": ["수요 둔화"], "citations": [{"source_id": "fin:000001", "claim": "영업이익 증가"}], "data_gaps": []}
    calls = []
    attach(model, recording(calls, counted=3000, body=message_body(text=json.dumps(result, ensure_ascii=False))))
    parsed = model.with_structured_output(SpecialistResult, method="json_schema", strict=True).invoke(
        [("system", "specialist"), ("human", "{}")])
    assert parsed == SpecialistResult.model_validate(result)
    assert sent(calls[1])["max_tokens"] == 4000 and sent(calls[1])["output_config"]["effort"] == "low"
