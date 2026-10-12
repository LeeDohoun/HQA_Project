import pytest

from src.agents.llm_config import get_llm_config, get_llm_info


LLM_ENV_NAMES = [
    "LLM_PROVIDER",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "HQA_CLAUDE_CLI",
    "HQA_CLAUDE_MODEL",
    "HQA_CLAUDE_FALLBACKS",
    "HQA_CLAUDE_ANALYST_MODEL",
    "HQA_CLAUDE_RISK_MANAGER_MODEL",
    "HQA_CLAUDE_RISK_MANAGER_EFFORT",
    "HQA_LLM_TIMEOUT_SECONDS",
    "HQA_LLM_ANALYST_TIMEOUT_SECONDS",
    "HQA_LLM_RISK_MANAGER_TIMEOUT_SECONDS",
    "HQA_LLM_RISK_MANAGER_MAX_OUTPUT_TOKENS",
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


def test_ollama_context_window_is_configurable_for_long_specialist_inputs(monkeypatch):
    import pytest
    from src.agents import llm_config

    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    model = llm_config._create_ollama_llm("qwen3:14b", base_url="http://127.0.0.1:9")
    assert model.num_ctx == 16384
    monkeypatch.setenv("OLLAMA_NUM_CTX", "1024")
    with pytest.raises(ValueError, match="OLLAMA_NUM_CTX"):
        llm_config._create_ollama_llm("qwen3:14b", base_url="http://127.0.0.1:9")


def test_long_output_roles_get_their_own_timeout(monkeypatch):
    from src.agents import llm_config

    monkeypatch.setenv("HQA_LLM_TIMEOUT_SECONDS", "45")
    monkeypatch.delenv("HQA_LLM_RISK_MANAGER_TIMEOUT_SECONDS", raising=False)
    assert llm_config._role_timeout("analyst") == 45
    assert llm_config._role_timeout("risk_manager") == 180
    monkeypatch.setenv("HQA_LLM_RISK_MANAGER_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("HQA_LLM_ANALYST_TIMEOUT_SECONDS", "60")
    assert llm_config._role_timeout("risk_manager") == 240
    assert llm_config._role_timeout("analyst") == 60


def test_claude_provider_defaults_to_opus_with_role_efforts_and_limits(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude")

    info = get_llm_info()

    assert info["provider"] == "anthropic"
    assert info["base_url"] == "https://api.anthropic.com" and not info["api_key_set"]
    assert set(info["agent_models"].values()) == {"claude-opus-5-5"}
    assert info["efforts"] == {"analyst": "low", "quant": "low", "chartist": "low",
                               "risk_manager": "medium", "summary": "low"}
    assert set(info["server_side_fallback"].values()) == {True}
    assert info["role_token_limits"]["analyst"] == {"input": 12_000, "output_including_thinking": 4_000}
    assert info["role_token_limits"]["risk_manager"] == {"input": 128_000, "output_including_thinking": 16_000}


def test_claude_role_models_and_efforts_are_configurable_and_validated(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HQA_CLAUDE_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("HQA_CLAUDE_ANALYST_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("HQA_CLAUDE_RISK_MANAGER_EFFORT", "high")

    info = get_llm_info()

    assert info["agent_models"] == {"analyst": "claude-haiku-5-5", "summary": "claude-sonnet-5-5",
                                    "quant": "claude-sonnet-5-5", "chartist": "claude-sonnet-5-5",
                                    "risk_manager": "claude-sonnet-5-5"}
    assert info["efforts"]["risk_manager"] == "high"
    assert info["server_side_fallback"]["analyst"] is False  # Haiku 5.5 has no server-side fallback
    monkeypatch.setenv("HQA_CLAUDE_RISK_MANAGER_MODEL", "claude-3-opus")
    with pytest.raises(ValueError, match="not supported"):
        get_llm_config()
    monkeypatch.delenv("HQA_CLAUDE_RISK_MANAGER_MODEL")
    monkeypatch.setenv("HQA_CLAUDE_RISK_MANAGER_EFFORT", "none")
    with pytest.raises(ValueError, match="EFFORT"):
        get_llm_config()


def test_claude_factory_requires_key_only_when_constructed(monkeypatch):
    from src.agents.llm_config import get_analyst_llm

    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert get_llm_config().provider == "anthropic"
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        get_analyst_llm()


@pytest.mark.parametrize("role, effort, output, timeout", [
    ("analyst", "low", 4_000, 120), ("quant", "low", 4_000, 120), ("chartist", "low", 4_000, 120),
    ("summary", "low", 2_000, 120), ("risk_manager", "medium", 16_000, 300),
])
def test_claude_factory_settings(monkeypatch, role, effort, output, timeout):
    from src.agents import llm_config

    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    model = getattr(llm_config, f"get_{role}_llm")()
    assert model.model_name == "claude-opus-5-5" and model.reasoning_effort == effort
    assert model.max_tokens == output and model.hqa_input_limit == llm_config.get_role_limits(role).input_tokens
    assert model.request_timeout == timeout and model.fallbacks is True and model.cache is False
    assert "offline-test-key" not in repr(model)
    monkeypatch.setenv("HQA_CLAUDE_FALLBACKS", "off")
    assert getattr(llm_config, f"get_{role}_llm")().fallbacks is False


def test_claude_timeouts_follow_the_shared_overrides(monkeypatch):
    from src.agents import llm_config

    clear_llm_env(monkeypatch)
    assert llm_config._role_timeout("analyst", "anthropic") == 120
    assert llm_config._role_timeout("risk_manager", "anthropic") == 300
    monkeypatch.setenv("HQA_LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("HQA_LLM_RISK_MANAGER_TIMEOUT_SECONDS", "420")
    assert llm_config._role_timeout("analyst", "anthropic") == 90
    assert llm_config._role_timeout("risk_manager", "anthropic") == 420


def test_claude_plan_provider_reports_the_subscription_settings(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude-plan")

    info = get_llm_info()

    assert info["provider"] == "claude_plan" and info["billing"] == "claude_subscription"
    assert info["cli"] == "claude" and info["oauth_token_set"] is False
    assert set(info["agent_models"].values()) == {"claude-opus-5-5"}
    assert info["efforts"]["risk_manager"] == "medium"
    assert info["role_token_limits"]["risk_manager"] == {"input": 128_000, "output_including_thinking": 16_000}
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "offline-plan-token")
    assert get_llm_info()["oauth_token_set"] is True
