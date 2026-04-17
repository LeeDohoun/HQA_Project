from src.agents.llm_config import get_llm_config, get_llm_info


LLM_ENV_NAMES = [
    "LLM_PROVIDER",
    "OLLAMA_BASE_URL",
    "OLLAMA_INSTRUCT_MODEL",
    "OLLAMA_THINKING_MODEL",
    "OLLAMA_THINKING_VALIDATOR_MODEL",
    "OLLAMA_VISION_MODEL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_INSTRUCT_MODEL",
    "GEMINI_THINKING_MODEL",
    "GEMINI_THINKING_VALIDATOR_MODEL",
    "GEMINI_VISION_MODEL",
]


def clear_llm_env(monkeypatch):
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_llm_config_uses_project_ollama_defaults(monkeypatch):
    clear_llm_env(monkeypatch)

    info = get_llm_info()

    assert info["provider"] == "ollama"
    assert info["base_url"] == "http://localhost:11434"
    assert info["instruct_model"] == "qwen3.5:9b"
    assert info["thinking_model"] == "gemma4:e4b"
    assert info["thinking_validator_model"] == ""
    assert info["vision_model"] == "llava:13b"


def test_llm_config_falls_back_when_gemini_key_is_missing(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    info = get_llm_info()

    assert info["requested_provider"] == "gemini"
    assert info["provider"] == "ollama"
    assert info["fallback_reason"] == "missing_google_api_key"


def test_llm_config_accepts_gemini_api_key_alias(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    config = get_llm_config()
    info = get_llm_info()

    assert config.provider == "gemini"
    assert config.requested_provider == "gemini"
    assert info["provider"] == "gemini"
    assert info["instruct_model"] == "gemini-2.5-flash-lite"
    assert info["thinking_model"] == "gemini-2.5-pro"
    assert info["vision_model"] == "gemini-2.5-flash"
    assert info["api_key_set"] is True


def test_llm_config_treats_blank_required_models_as_defaults(monkeypatch):
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_INSTRUCT_MODEL", "")
    monkeypatch.setenv("OLLAMA_THINKING_MODEL", " ")

    info = get_llm_info()

    assert info["instruct_model"] == "qwen3.5:9b"
    assert info["thinking_model"] == "gemma4:e4b"
