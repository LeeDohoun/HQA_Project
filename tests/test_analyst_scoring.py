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


def test_analyst_passes_investment_evidence_up_to_5000_chars_without_summary():
    pytest.importorskip("langchain_core")
    from src.agents.analyst import AnalystAgent

    prefix = (
        "=== 검색된 문서 (Canonical evidence) ===\n"
        "[문서 1] (출처: dart, source=dart, score=1.000, title=공시, stock=삼성전자, date=2026-06-01)\n"
    )
    context = prefix + "A" * (5000 - len(prefix))

    class FakeTool:
        def _run(self, query: str, stock_code: str | None = None):
            return context

    agent = AnalystAgent()
    agent.evidence_tool_evidence = FakeTool()
    agent._summarize_evidence = lambda stock_name, raw_context: pytest.fail("summary should not run")

    result, sources = agent._search_evidence("삼성전자", "005930")

    assert len(context) == 5000
    assert result == context
    assert sources == ["dart"]


def test_analyst_summarizes_investment_evidence_after_5000_chars():
    pytest.importorskip("langchain_core")
    from src.agents.analyst import AnalystAgent

    prefix = (
        "=== 검색된 문서 (Canonical evidence) ===\n"
        "[문서 1] (출처: news, source=news, score=1.000, title=뉴스, stock=삼성전자, date=2026-06-01)\n"
    )
    context = prefix + "B" * (5001 - len(prefix))

    class FakeTool:
        def _run(self, query: str, stock_code: str | None = None):
            return context

    agent = AnalystAgent()
    agent.evidence_tool_evidence = FakeTool()
    calls = []

    def fake_summary(stock_name: str, raw_context: str):
        calls.append({"stock_name": stock_name, "context": raw_context})
        return "요약된 투자 근거"

    agent._summarize_evidence = fake_summary

    result, sources = agent._search_evidence("삼성전자", "005930")

    assert len(context) == 5001
    assert result == "요약된 투자 근거"
    assert sources == ["news"]
    assert calls == [{"stock_name": "삼성전자", "context": context}]


def test_analyst_truncates_news_and_forum_evidence_to_1500_chars():
    pytest.importorskip("langchain_core")
    from src.agents.analyst import AnalystAgent

    context = (
        "=== 검색된 문서 (Canonical evidence) ===\n"
        "[문서 1] (출처: forum, source=forum, score=1.000, title=토론, stock=삼성전자, date=2026-06-01)\n"
        + "C" * 2000
    )

    class FakeTool:
        def _run(self, query: str, stock_code: str | None = None):
            return context

    agent = AnalystAgent()
    agent.evidence_tool_news = FakeTool()

    result = agent._search_news("삼성전자", "005930")

    rendered_context = result.split("\n\n[데이터 출처:", 1)[0]
    assert rendered_context == context[:1500]
    assert len(rendered_context) == 1500
    assert "[데이터 출처: evidence 저장 문서" in result
