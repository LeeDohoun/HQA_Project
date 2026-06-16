from __future__ import annotations

from copy import deepcopy

from src.reports.risk_report_writer import render_risk_report_markdown


def test_risk_report_writer_renders_markdown_without_mutating_decision():
    decision = {
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "action_code": "BUY",
        "confidence": 76,
        "risk_level_code": "MEDIUM",
        "position_size": "10%",
        "summary": "조건부 매수",
        "trade_plan": {"strategy": "breakout"},
        "entry_conditions": [{"description": "72,000원 돌파"}],
        "exit_conditions": [{"description": "78,000원 도달"}],
        "reduce_conditions": [],
        "invalidation_conditions": [{"description": "68,000원 이탈"}],
    }
    original = deepcopy(decision)

    markdown = render_risk_report_markdown(decision)

    assert decision == original
    assert "# 삼성전자 (005930) RiskManager 리포트" in markdown
    assert "- 판단: BUY" in markdown
    assert "72,000원 돌파" in markdown
    assert "68,000원 이탈" in markdown
