from src.agents.llm_config import get_llm_config, get_llm_info


LLM_ENV_NAMES = [
    "LLM_PROVIDER",
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


def test_llm_config_uses_project_ollama_defaults(monkeypatch):
    clear_llm_env(monkeypatch)

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


def test_llm_config_falls_back_for_unsupported_provider(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "remote")

    info = get_llm_info()

    assert info["requested_provider"] == "remote"
    assert info["provider"] == "ollama"
    assert info["fallback_reason"] == "unsupported_provider:remote"


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
