import src.agents as agents


def test_agents_public_api_does_not_expose_vision_models():
    public_names = set(agents.__all__)

    assert "get_vision_llm" not in public_names
    assert "get_gemini_vision_llm" not in public_names
    assert "VisionAnalyzer" not in public_names
    assert "GeminiVisionAnalyzer" not in public_names
