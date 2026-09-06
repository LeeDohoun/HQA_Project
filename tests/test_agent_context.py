from src.utils.prompt_loader import load_prompt


def test_prompt_templates_render_without_agent_context_packet():
    analyst_prompt = load_prompt(
        "analyst",
        "analysis",
        stock_name="삼성전자",
        stock_code="005930",
        research_summary="요약",
        quality_grade="B",
        quality_score=72,
        quality_warnings="- 없음",
    )

    chartist_prompt = load_prompt(
        "chartist",
        "chartist",
        stock_name="삼성전자",
        stock_code="005930",
        technical_indicators='{"technical_indicators": {"rsi": 55}}',
        recent_candles='{"recent_candles": []}',
        price_snapshot_context='{"price_snapshot": null}',
    )

    risk_prompt = load_prompt(
        "risk_manager",
        "risk_manager",
        stock_name="삼성전자",
        stock_code="005930",
        analyst_total=60,
        analyst_grade="B",
        quant_total=70,
        chartist_total=65,
        analyst_result="## Analyst Result\n- 최종 의견: 장기 해자는 강함",
        quant_result="## Quant Result\n- 최종 의견: 재무 양호",
        chartist_result="## Chartist Result\n- 신호: 기술적 중립",
        portfolio_context="포트폴리오 정보 없음",
        investor_profile="투자자 프로필 없음",
    )

    assert "삼성전자" in analyst_prompt
    assert "QUALITY" not in analyst_prompt
    assert "가격, 거래량, 변동성만 분석" in chartist_prompt
    assert '"trend_score"' in chartist_prompt
    assert '"technical_invalid_price"' in chartist_prompt
    assert "산업 전망" in chartist_prompt
    assert "분석하지 마세요" in chartist_prompt or "판단하지 마세요" in chartist_prompt
    assert "## Analyst Result" in risk_prompt
    assert "Analyst 총점: 60 / 100점" in risk_prompt
    assert "Analyst 총점: 60 / 70점" not in risk_prompt
    assert "## Quant Result" in risk_prompt
    assert "## Chartist Result" in risk_prompt
    assert "Packet" not in risk_prompt
    assert "Analyst는 산업/기업 헤게모니" in risk_prompt
    assert "Quant는 투자 논리가 재무제표와 밸류에이션으로" in risk_prompt
    assert "Chartist는 지금 진입 가능한 자리인지" in risk_prompt
    assert "세 에이전트 점수를 기계적으로 평균내지 마세요" in risk_prompt
    assert "무효화 조건" in risk_prompt
    assert "매수하지 않을 조건" in risk_prompt
    assert "추가 매수 조건" in risk_prompt
    assert "비중 축소 조건" in risk_prompt
    assert "total_score는 단순 평균이 아니라" in risk_prompt
