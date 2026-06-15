import src.agents as agents
import pytest


def test_agents_public_api_does_not_expose_vision_models():
    public_names = set(agents.__all__)

    assert "get_vision_llm" not in public_names
    assert "VisionAnalyzer" not in public_names


def test_agents_public_api_does_not_expose_retired_agent_names():
    public_names = set(agents.__all__)

    assert "ResearcherAgent" not in public_names
    assert "StrategistAgent" not in public_names
    assert "HegemonyScore" not in public_names
    assert not hasattr(agents, "ResearcherAgent")
    assert not hasattr(agents, "StrategistAgent")
    assert not hasattr(agents, "HegemonyScore")


def test_analyst_public_analysis_api_is_full_analysis_only():
    pytest.importorskip("langchain_core")
    from src.agents.analyst import AnalystAgent

    agent = AnalystAgent()

    assert callable(agent.full_analysis)
    assert callable(agent.analyze_stock)
    assert not hasattr(agent, "quick_research")
    assert not hasattr(agent, "research")
    assert not hasattr(agent, "analyze_hegemony")
