from __future__ import annotations

import json

import pytest


def test_analyst_score_is_clamped_to_100_point_scale():
    pytest.importorskip("langchain_core")
    from src.agents.analyst import AnalystAgent

    class FakeResponse:
        content = json.dumps(
            {
                "moat_score": 80,
                "moat_reason": "독점력 과대 점수 테스트",
                "growth_score": 90,
                "growth_reason": "성장성 과대 점수 테스트",
                "competitive_advantage": "강한 경쟁 우위",
                "risk_factors": "과열",
                "hegemony_grade": "A",
                "final_opinion": "테스트 의견",
                "detailed_reasoning": "테스트 판단",
            },
            ensure_ascii=False,
        )

    class FakeLLM:
        def invoke(self, prompt: str):
            return FakeResponse()

    agent = AnalystAgent()
    agent._thinking_llm = FakeLLM()
    agent._search_evidence = lambda stock_name, stock_code="": ("DART와 뉴스 근거", ["dart", "news"])
    agent._search_news = lambda stock_name, stock_code="": "뉴스와 포럼 심리"

    score = agent.full_analysis("삼성전자", "005930")

    assert score.moat_score == 50
    assert score.growth_score == 50
    assert score.total_score == 100


def test_analyst_uses_stock_code_when_collecting_rag_evidence():
    pytest.importorskip("langchain_core")
    from src.agents.analyst import AnalystAgent

    class FakeTool:
        def __init__(self, label: str):
            self.label = label
            self.calls = []

        def _run(self, query: str, stock_code: str | None = None):
            self.calls.append({"query": query, "stock_code": stock_code})
            return (
                f"=== 검색된 문서 (Canonical evidence) ===\n"
                f"[문서 1] (출처: news, source=news, score=1.000, title={self.label}, stock=삼성전자, date=2026-06-01)\n"
                f"{self.label} 본문"
            )

    agent = AnalystAgent()
    evidence_tool = FakeTool("투자 근거")
    news_tool = FakeTool("뉴스 심리")
    agent.evidence_tool_evidence = evidence_tool
    agent.evidence_tool_news = news_tool

    agent._collect_research("삼성전자", "005930")

    assert evidence_tool.calls[0]["stock_code"] == "005930"
    assert news_tool.calls[0]["stock_code"] == "005930"
