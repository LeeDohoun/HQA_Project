import json

import anthropic
import httpx2

from scripts import claude_check


def model_info(model_id, *, lifecycle="active", fallbacks=("claude-opus-5", "claude-opus-4-8"), structured=True):
    return {"type": "model", "id": model_id, "display_name": model_id, "created_at": "2026-10-01T00:00:00Z",
            "lifecycle": lifecycle, "max_input_tokens": 1_000_000, "max_tokens": 128_000,
            "allowed_fallback_models": list(fallbacks),
            "capabilities": {"structured_outputs": {"supported": structured}}}


def client_for(handler):
    return anthropic.Anthropic(api_key="offline-test-key", base_url="https://api.anthropic.com", max_retries=0,
                               http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_model_check_requires_active_models_with_structured_outputs_and_priced_fallbacks(capsys):
    infos = {"claude-opus-5-5": model_info("claude-opus-5-5")}
    client = client_for(lambda request: httpx2.Response(200, json=infos[request.url.path.rsplit("/", 1)[-1]]))
    models = {"quant": "claude-opus-5-5", "risk_manager": "claude-opus-5-5"}
    assert claude_check.check_models(client, models) is True
    assert "OK   claude-opus-5-5 (quant, risk_manager)" in capsys.readouterr().out
    infos["claude-opus-5-5"] = model_info("claude-opus-5-5", fallbacks=("claude-unlisted",))
    assert claude_check.check_models(client, models) is False
    assert "UNPRICED=['claude-unlisted']" in capsys.readouterr().out
    infos["claude-opus-5-5"] = model_info("claude-opus-5-5", lifecycle="deprecated")
    assert claude_check.check_models(client, models) is False


def test_model_check_reports_a_rejected_key_without_printing_it(capsys):
    client = client_for(lambda request: httpx2.Response(
        401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    assert claude_check.check_models(client, {"quant": "claude-opus-5-5"}) is False
    output = capsys.readouterr().out
    assert "key rejected" in output and "offline-test-key" not in output


def test_counting_flags_a_request_above_its_role_limit(capsys, monkeypatch):
    monkeypatch.setattr(claude_check.time, "sleep", lambda seconds: None)
    counts = {"short": 9_000, "long": 12_500}

    def handler(request):
        body = json.loads(request.content)
        assert body["output_config"]["format"]["type"] == "json_schema"
        return httpx2.Response(200, json={"input_tokens": counts[body["messages"][0]["content"]]})

    captured = [("quant", "000001", [("system", "quant"), ("human", "short")]),
                ("chartist", "000001", [("system", "chartist"), ("human", "long")])]
    models = {"quant": "claude-opus-5-5", "chartist": "claude-opus-5-5"}
    assert claude_check.count_requests(client_for(handler), models, captured) is False
    output = capsys.readouterr().out
    assert "OK   000001 quant" in output and "FAIL 000001 chartist  claude= 12500 limit=12000" in output


def test_check_needs_a_key(capsys, monkeypatch):
    monkeypatch.setattr(claude_check, "load_project_env", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert claude_check.main([]) == 1
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().out
