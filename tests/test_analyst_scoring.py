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
    agent._search_evidence = lambda stock_name: ("DART와 뉴스 근거", ["dart", "news"])
    agent._search_news = lambda stock_name: "뉴스와 포럼 심리"

    score = agent.full_analysis("삼성전자", "005930")

    assert score.moat_score == 50
    assert score.growth_score == 50
    assert score.total_score == 100
