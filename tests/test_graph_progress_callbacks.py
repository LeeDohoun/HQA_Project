from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_run_stock_analysis_passes_progress_callback_to_initial_state(monkeypatch):
    import src.agents.graph as graph_module

    captured = {}

    class FakeGraph:
        def invoke(self, state):
            captured["state"] = state
            return {
                **state,
                "research_quality": "A",
                "quality_warnings": [],
                "retry_count": 0,
            }

    def callback(agent: str, status: str, message: str, progress: float):
        raise AssertionError("callback should only be stored in state by this test")

    monkeypatch.setattr(graph_module, "get_analysis_graph", lambda: FakeGraph())

    graph_module.run_stock_analysis(
        "삼성전자",
        "005930",
        max_retries=1,
        progress_callback=callback,
    )

    assert captured["state"]["progress_callback"] is callback


def test_quant_node_emits_started_and_completed_progress(monkeypatch):
    import src.agents.graph as graph_module
    import src.agents.quant as quant_module

    events = []

    class FakeQuantAgent:
        def full_analysis(self, stock_name: str, stock_code: str):
            return SimpleNamespace(
                total_score=88,
                grade="A",
                opinion="재무 우수",
                analysis_packet={},
            )

    monkeypatch.setattr(quant_module, "QuantAgent", FakeQuantAgent)

    result = graph_module._quant_node(
        {
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "progress_callback": lambda agent, status, message, progress: events.append(
                (agent, status, message, progress)
            ),
        }
    )

    assert result["quant_score"].grade == "A"
    assert events[0][0:2] == ("quant", "started")
    assert events[-1][0:2] == ("quant", "completed")
