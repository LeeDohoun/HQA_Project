from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_execute_quick_returns_quick_decision_and_final_decision(monkeypatch):
    import ai_server.app as app_module
    import src.agents as agents

    class FakeQuantAgent:
        def full_analysis(self, stock_name: str, stock_code: str):
            return SimpleNamespace(
                total_score=80,
                grade="A",
                opinion=f"{stock_name} 재무 점수 우수",
            )

        def _default_score(self, stock_name: str, reason: str):
            raise AssertionError(reason)

    class FakeChartistAgent:
        def full_analysis(self, stock_name: str, stock_code: str):
            return SimpleNamespace(
                total_score=60,
                signal="매수",
                trend_score=20,
                momentum_score=18,
                volatility_score=12,
                volume_score=10,
            )

        def _default_score(self, stock_code: str, reason: str):
            raise AssertionError(reason)

    class FakeRiskManagerAgent:
        def quick_decision(self, analyst_total: int, quant_total: int, chartist_total: int) -> str:
            assert analyst_total == 35
            assert quant_total == 80
            assert chartist_total == 60
            return "📈 매수 (점수: 70)"

    monkeypatch.setattr(agents, "QuantAgent", FakeQuantAgent)
    monkeypatch.setattr(agents, "ChartistAgent", FakeChartistAgent)
    monkeypatch.setattr(agents, "RiskManagerAgent", FakeRiskManagerAgent)

    result = app_module._execute_quick("task-quick", "삼성전자", "005930")

    assert result["mode"] == "quick"
    assert set(result["scores"]) == {"quant", "chartist", "quick_decision"}
    assert result["scores"]["quick_decision"]["total_score"] == 70
    assert result["scores"]["quick_decision"]["grade"] == "매수"
    assert result["final_decision"]["action"] == "매수"
    assert result["final_decision"]["total_score"] == 70


def test_execute_full_exposes_risk_manager_score(monkeypatch):
    import ai_server.app as app_module
    import src.agents.graph as graph

    action = SimpleNamespace(value="매수", name="BUY")
    risk_level = SimpleNamespace(value="낮음", name="LOW")
    decision = SimpleNamespace(
        action=action,
        risk_level=risk_level,
        total_score=82,
        confidence=77,
        summary="네 에이전트 의견이 매수 쪽으로 정렬되었습니다.",
        key_catalysts=["실적 개선"],
        risk_factors=["환율 변동"],
        detailed_reasoning="Analyst, Quant, Chartist 결과를 종합했습니다.",
    )

    def fake_run_stock_analysis(stock_name: str, stock_code: str, max_retries: int):
        assert stock_name == "삼성전자"
        assert stock_code == "005930"
        assert max_retries == 1
        return {
            "scores": {
                "analyst": SimpleNamespace(
                    total_score=60,
                    hegemony_grade="A",
                    final_opinion="헤게모니 우수",
                ),
                "quant": SimpleNamespace(total_score=75, grade="B", opinion="재무 양호"),
                "chartist": SimpleNamespace(total_score=70, signal="매수"),
            },
            "final_decision": decision,
            "research_quality": "good",
            "quality_warnings": [],
        }

    monkeypatch.setattr(graph, "run_stock_analysis", fake_run_stock_analysis)

    result = app_module._execute_full("task-full", "삼성전자", "005930", 1)

    assert result["mode"] == "full"
    assert set(result["scores"]) == {"analyst", "quant", "chartist", "risk_manager"}
    assert result["scores"]["risk_manager"]["total_score"] == 82
    assert result["scores"]["risk_manager"]["action"] == "매수"
    assert result["final_decision"]["summary"] == "네 에이전트 의견이 매수 쪽으로 정렬되었습니다."
