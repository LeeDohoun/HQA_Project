from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_execute_quick_uses_risk_manager_make_decision(monkeypatch):
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
        def make_decision(self, stock_name: str, stock_code: str, scores):
            assert stock_name == "삼성전자"
            assert stock_code == "005930"
            assert scores.analyst_total == 50
            assert scores.quant_total == 80
            assert scores.chartist_total == 60
            return SimpleNamespace(
                action=SimpleNamespace(value="매수", name="BUY"),
                risk_level=SimpleNamespace(value="낮음", name="LOW"),
                total_score=70,
                confidence=70,
                summary="빠른 분석 기준 매수",
                key_catalysts=["재무 점수 80/100", "기술 점수 60/100"],
                risk_factors=["Analyst 정성 리서치 생략"],
                detailed_reasoning="Quant와 Chartist 결과를 Risk Manager가 종합했습니다.",
            )

    monkeypatch.setattr(agents, "QuantAgent", FakeQuantAgent)
    monkeypatch.setattr(agents, "ChartistAgent", FakeChartistAgent)
    monkeypatch.setattr(agents, "RiskManagerAgent", FakeRiskManagerAgent)

    result = app_module._execute_quick("task-quick", "삼성전자", "005930")

    assert result["mode"] == "quick"
    assert set(result["scores"]) == {"quant", "chartist", "risk_manager"}
    assert result["scores"]["risk_manager"]["total_score"] == 70
    assert result["scores"]["risk_manager"]["grade"] == "매수"
    assert result["final_decision"]["action"] == "매수"
    assert result["final_decision"]["total_score"] == 70


def test_execute_quick_publishes_agent_completion_when_each_parallel_agent_finishes(monkeypatch):
    import ai_server.app as app_module
    import src.agents as agents
    import src.utils.parallel as parallel

    published = []

    class FakeQuantAgent:
        def full_analysis(self, stock_name: str, stock_code: str):
            return SimpleNamespace(total_score=80, grade="A", opinion="재무 우수")

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
        def make_decision(self, stock_name: str, stock_code: str, scores):
            return SimpleNamespace(
                action=SimpleNamespace(value="매수", name="BUY"),
                risk_level=SimpleNamespace(value="낮음", name="LOW"),
                total_score=70,
                confidence=70,
                summary="빠른 분석 기준 매수",
                key_catalysts=[],
                risk_factors=[],
                detailed_reasoning="",
            )

    def fake_publish_progress(task_id: str, agent: str, status: str, message: str, progress: float):
        published.append((task_id, agent, status, message, progress))

    def fake_run_agents_parallel(tasks):
        quant_result = tasks["quant"][0](*tasks["quant"][1])
        assert ("task-quick", "quant", "completed", "재무: A", 1.0) in published
        chartist_result = tasks["chartist"][0](*tasks["chartist"][1])
        assert ("task-quick", "chartist", "completed", "기술: 매수", 1.0) in published
        return {"quant": quant_result, "chartist": chartist_result}

    monkeypatch.setattr(agents, "QuantAgent", FakeQuantAgent)
    monkeypatch.setattr(agents, "ChartistAgent", FakeChartistAgent)
    monkeypatch.setattr(agents, "RiskManagerAgent", FakeRiskManagerAgent)
    monkeypatch.setattr(app_module, "_publish_progress", fake_publish_progress)
    monkeypatch.setattr(parallel, "run_agents_parallel", fake_run_agents_parallel)

    app_module._execute_quick("task-quick", "삼성전자", "005930")


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

    published = []

    def fake_publish_progress(task_id: str, agent: str, status: str, message: str, progress: float):
        published.append((task_id, agent, status, message, progress))

    def fake_run_stock_analysis(stock_name: str, stock_code: str, max_retries: int, progress_callback=None):
        assert stock_name == "삼성전자"
        assert stock_code == "005930"
        assert max_retries == 1
        assert progress_callback is not None
        progress_callback("quant", "completed", "재무: B", 1.0)
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
    monkeypatch.setattr(app_module, "_publish_progress", fake_publish_progress)

    result = app_module._execute_full("task-full", "삼성전자", "005930", 1)

    assert result["mode"] == "full"
    assert set(result["scores"]) == {"analyst", "quant", "chartist", "risk_manager"}
    assert result["scores"]["risk_manager"]["total_score"] == 82
    assert result["scores"]["risk_manager"]["action"] == "매수"
    assert result["final_decision"]["summary"] == "네 에이전트 의견이 매수 쪽으로 정렬되었습니다."
    assert ("task-full", "quant", "completed", "재무: B", 1.0) in published
