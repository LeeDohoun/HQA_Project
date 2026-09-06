import pytest

from src.agents.llm_config import get_llm_config, get_llm_info


LLM_ENV_NAMES = [
    "LLM_PROVIDER",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_ANALYST_MODEL",
    "OLLAMA_SUMMARY_MODEL",
    "OLLAMA_QUANT_MODEL",
    "OLLAMA_CHARTIST_MODEL",
    "OLLAMA_RISK_MANAGER_MODEL",
]


def clear_llm_env(monkeypatch):
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_llm_config_defaults_to_luna_without_requiring_key_at_import(monkeypatch):
    clear_llm_env(monkeypatch)

    info = get_llm_info()

    assert info["provider"] == "openai"
    assert not info["api_key_set"]
    assert set(info["agent_models"].values()) == {"gpt-5.6-luna"}
    assert info["reasoning_efforts"] == {
        "analyst": "low", "quant": "low", "chartist": "low",
        "risk_manager": "medium", "summary": "none",
    }


def test_llm_config_preserves_explicit_ollama_defaults(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    info = get_llm_info()

    assert info["provider"] == "ollama"
    assert info["base_url"] == "http://localhost:11435"
    assert info["agent_models"] == {
        "analyst": "gemma4:12b",
        "summary": "gemma4:e4b",
        "quant": "gemma4:12b",
        "chartist": "qwen3.5:9b",
        "risk_manager": "gemma4:12b",
    }
    assert "vision_model" not in info


def test_llm_config_rejects_unsupported_provider_without_fallback(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "remote")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        get_llm_info()


def test_llm_config_maps_test_alias_to_mock(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "test")

    config = get_llm_config()
    info = get_llm_info()

    assert config.provider == "mock"
    assert config.requested_provider == "mock"
    assert info["provider"] == "mock"
    assert info["agent_models"] == {
        "analyst": "mock",
        "summary": "mock",
        "quant": "mock",
        "chartist": "mock",
        "risk_manager": "mock",
    }


def test_llm_config_treats_blank_required_models_as_defaults(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_ANALYST_MODEL", "")
    monkeypatch.setenv("OLLAMA_CHARTIST_MODEL", " ")

    info = get_llm_info()

    assert info["agent_models"]["analyst"] == "gemma4:12b"
    assert info["agent_models"]["summary"] == "gemma4:e4b"
    assert info["agent_models"]["chartist"] == "qwen3.5:9b"


def test_llm_config_allows_agent_model_overrides(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_ANALYST_MODEL", "analyst-model")
    monkeypatch.setenv("OLLAMA_SUMMARY_MODEL", "summary-model")
    monkeypatch.setenv("OLLAMA_QUANT_MODEL", "quant-model")
    monkeypatch.setenv("OLLAMA_CHARTIST_MODEL", "chartist-model")
    monkeypatch.setenv("OLLAMA_RISK_MANAGER_MODEL", "risk-model")

    config = get_llm_config()
    info = get_llm_info()

    assert config.ollama_analyst_model == "analyst-model"
    assert config.ollama_summary_model == "summary-model"
    assert config.ollama_quant_model == "quant-model"
    assert config.ollama_chartist_model == "chartist-model"
    assert config.ollama_risk_manager_model == "risk-model"
    assert info["agent_models"] == {
        "analyst": "analyst-model",
        "summary": "summary-model",
        "quant": "quant-model",
        "chartist": "chartist-model",
        "risk_manager": "risk-model",
    }


def test_luna_factory_requires_key_only_when_constructed(monkeypatch):
    from src.agents.llm_config import get_analyst_llm

    clear_llm_env(monkeypatch)
    assert get_llm_config().provider == "openai"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_analyst_llm()


def test_openai_model_override_cannot_change_pinned_model(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "another-model")
    with pytest.raises(ValueError, match="gpt-5.6-luna"):
        get_llm_config()


@pytest.mark.parametrize("role", ["analyst", "quant", "chartist", "risk_manager", "summary"])
def test_luna_factory_uses_responses_and_disables_retries(monkeypatch, role):
    from src.agents import llm_config

    clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    model = getattr(llm_config, f"get_{role}_llm")()
    assert model.model_name == "gpt-5.6-luna"
    assert model.use_responses_api is True
    assert model.max_retries == 0
    assert model.disable_streaming is True
    assert model.service_tier == "default"
    assert model.store is False
    assert model.reasoning["effort"] == llm_config.get_role_limits(role).reasoning_effort
    assert model.max_tokens == llm_config.get_role_limits(role).output_tokens
