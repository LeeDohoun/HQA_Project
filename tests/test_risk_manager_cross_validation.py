from types import SimpleNamespace

import pytest

from src.agents.analyst import AnalystScore
from src.agents.chartist import ChartistScore
from src.agents.quant import QuantScore
from src.agents.risk_manager import (
    AgentScores,
    InvestmentAction,
    RiskLevel,
    RiskManagerAgent,
)


class FakeLLM:
    def __init__(self, payload: str):
        self.payload = payload
        self.last_prompt = ""

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return SimpleNamespace(content=self.payload)


@pytest.fixture(autouse=True)
def offline_risk_factory(monkeypatch):
    monkeypatch.setattr("src.agents.risk_manager.get_risk_manager_llm", lambda: FakeLLM("{}"))


class StructuredRunner:
    def __init__(self, payload: dict):
        self.payload = payload

    def invoke(self, prompt: str):
        return self.payload


class StructuredFakeLLM(FakeLLM):
    def __init__(self, payload: dict):
        super().__init__("")
        self.payload = payload
        self.method = None

    def with_structured_output(self, _schema, method="json_schema"):
        self.method = method
        return StructuredRunner(self.payload)


def make_scores() -> AgentScores:
    return AgentScores(
        analyst_moat_score=32,
        analyst_growth_score=22,
        analyst_total=54,
        analyst_grade="B",
        analyst_opinion="시장 지배력은 양호하지만 밸류 부담이 있음",
        quant_valuation_score=16,
        quant_profitability_score=19,
        quant_growth_score=17,
        quant_stability_score=20,
        quant_total=72,
        quant_opinion="재무 체력은 양호",
        chartist_trend_score=20,
        chartist_momentum_score=18,
        chartist_volatility_score=13,
        chartist_volume_score=14,
        chartist_total=65,
        chartist_signal="중립",
    )


def make_raw_scores() -> AgentScores:
    analyst = AnalystScore(
        moat_score=32,
        growth_score=22,
        total_score=54,
        hegemony_grade="B",
        moat_reason="HBM 경쟁력과 고객 락인이 강하다.",
        growth_reason="AI 서버 수요로 중기 성장성이 있다.",
        evidence_summary="DART와 뉴스에서 투자 확대 근거가 확인된다.",
        final_opinion="헤게모니는 양호하지만 가격 부담은 점검이 필요하다.",
        competitive_advantage="HBM 공급 경쟁력",
        risk_factors="메모리 업황 변동성, 경쟁 심화",
        detailed_reasoning="산업 수요와 기업 경쟁력을 함께 고려하면 중기 우위가 있다.",
    )
    quant = QuantScore(
        valuation_score=16,
        profitability_score=19,
        growth_score=17,
        stability_score=20,
        total_score=72,
        valuation_analysis="PER/PBR은 과도하지 않다.",
        profitability_analysis="ROE와 영업이익률이 양호하다.",
        growth_analysis="성장성은 중립 이상이다.",
        stability_analysis="부채비율과 유동비율이 안정적이다.",
        per=12.0,
        pbr=1.2,
        eps=5800,
        bps=58000,
        roe=10.87,
        roa=8.22,
        operating_margin=13.07,
        net_margin=13.55,
        debt_ratio=29.81,
        current_ratio=200.0,
        opinion="재무 체력은 양호하다.",
        grade="B",
        quality_flags={"source": "dart_financial_snapshot+krx_fundamental"},
    )
    chartist = ChartistScore(
        trend_score=20,
        momentum_score=18,
        volatility_score=13,
        volume_score=14,
        total_score=65,
        signal="중립",
        trend_analysis="중기 추세는 유지된다.",
        momentum_analysis="모멘텀은 중립이다.",
        volatility_analysis="변동성은 보통이다.",
        volume_analysis="거래량 확인이 필요하다.",
        rsi=58.0,
        macd_histogram=12.5,
        volume_ratio=1.3,
        entry_timing="confirm_support",
        overheat_risk="medium",
        stop_loss="9,595원",
        target_price="10,850원",
        mid_term_opinion="지지 확인 후 접근",
    )
    return AgentScores(
        analyst_result=analyst,
        quant_result=quant,
        chartist_result=chartist,
    )


def build_response(action: str, total_score: int = 68, confidence: int = 72) -> str:
    return f"""
{{
    "total_score": {total_score},
    "action": "{action}",
    "confidence": {confidence},
    "risk_level": "MEDIUM",
    "risk_factors": ["밸류에이션 부담", "업황 변동성"],
    "position_size": "50%",
    "entry_strategy": "분할 매수",
    "exit_strategy": "목표 수익 구간 분할 매도",
    "stop_loss": "-8% 손절",
    "signal_alignment": "펀더멘털은 양호하지만 기술적 신호는 중립입니다.",
    "key_catalysts": ["실적 개선", "수급 회복"],
    "contrarian_view": "단기 변동성 확대 가능성",
    "summary": "조건부 매수 관점",
    "detailed_reasoning": "정량 점수와 헤게모니 평가는 준수하나 기술적 확신은 제한적입니다."
}}
"""


def test_make_decision_uses_single_risk_manager_model_result():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("STRONG_BUY", total_score=92, confidence=88))

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert decision.action == InvestmentAction.STRONG_BUY
    assert decision.total_score == 92
    assert decision.confidence == 88
    assert not hasattr(decision, "validation_status")
    assert not hasattr(decision, "validator_model")


def test_decision_parser_recovers_when_response_has_trailing_extra_data():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("BUY", total_score=74, confidence=78) + "\n{\"extra\": true}")

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert decision.action == InvestmentAction.BUY
    assert decision.total_score == 74
    assert decision.confidence == 78


def test_decision_parser_prefers_structured_output():
    agent = RiskManagerAgent()
    agent.llm = StructuredFakeLLM(
        {
            "total_score": 81,
            "action": "BUY",
            "confidence": 77,
            "risk_level": "LOW",
            "summary": "구조화 응답",
        }
    )

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert agent.llm.method == "json_schema"
    assert decision.action == InvestmentAction.BUY
    assert decision.risk_level == RiskLevel.LOW
    assert decision.summary == "구조화 응답"


def test_make_decision_raises_when_llm_cannot_return_decision_json():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM("JSON 응답을 만들 수 없습니다.")

    with pytest.raises(ValueError, match="JSON 형식 응답 없음"):
        agent.make_decision("삼성전자", "005930", make_scores())


def test_decision_prompt_includes_portfolio_position_context():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("HOLD", total_score=52, confidence=64))

    portfolio_context = {
        "available": True,
        "source": "kis_balance",
        "summary": {
            "holding_count": 1,
            "total_evaluation_amount": 198560,
            "total_profit_loss": -35440,
            "available_cash": 9760000,
        },
        "position": {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "holding_quantity": 8,
            "orderable_quantity": 8,
            "average_price": 29200,
            "current_price": 24820,
            "evaluation_amount": 198560,
            "profit_loss": -35440,
            "profit_loss_rate": -15.0,
        },
        "is_held": True,
    }

    decision = agent.make_decision("삼성전자", "005930", make_scores(), portfolio_context=portfolio_context)

    assert decision.action == InvestmentAction.HOLD
    assert "- current stock is held: yes" in agent.llm.last_prompt
    assert "- unrealized profit/loss rate: -15.0%" in agent.llm.last_prompt
    assert "- available cash: 9760000" in agent.llm.last_prompt


def test_decision_prompt_includes_investor_profile_context():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("HOLD", total_score=52, confidence=64))

    investor_profile = {
        "total_assets": 50_000_000,
        "monthly_investment": 1_000_000,
        "investment_period_months": 36,
        "target_return_rate": 12,
        "investment_goal": "ASSET_GROWTH",
        "investment_experience": "BEGINNER",
        "investment_type": "STABLE",
        "volatility_tolerance": "LOW",
        "loss_action": "SELL_IMMEDIATELY",
        "leverage_allowed": False,
        "occupation_type": "EMPLOYEE",
        "loss_tolerance": "LEVEL_1",
    }

    decision = agent.make_decision(
        "삼성전자",
        "005930",
        make_scores(),
        investor_profile=investor_profile,
    )

    assert decision.action == InvestmentAction.HOLD
    assert "## 4. 투자자 프로필" in agent.llm.last_prompt
    assert "- investment_type: STABLE" in agent.llm.last_prompt
    assert "- volatility_tolerance: LOW" in agent.llm.last_prompt
    assert "RiskManager만 사용자 적합성을 반영" in agent.llm.last_prompt


def test_decision_prompt_uses_raw_agent_results_without_context_packet():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("HOLD", total_score=60, confidence=70))

    decision = agent.make_decision("삼성전자", "005930", make_raw_scores())

    assert decision.action == InvestmentAction.HOLD
    assert "## Analyst Result" in agent.llm.last_prompt
    assert "헤게모니 총점: 54 / 100" in agent.llm.last_prompt
    assert "독점력 점수: 32 / 50" in agent.llm.last_prompt
    assert "성장성 점수: 22 / 50" in agent.llm.last_prompt
    assert "HBM 경쟁력과 고객 락인이 강하다." in agent.llm.last_prompt
    assert "## Quant Result" in agent.llm.last_prompt
    assert "PER: 12.0" in agent.llm.last_prompt
    assert "유동비율: 200.0" in agent.llm.last_prompt
    assert "## Chartist Result" in agent.llm.last_prompt
    assert "RSI: 58.0" in agent.llm.last_prompt
    assert "손절가: 9,595원" in agent.llm.last_prompt
    assert "AgentContextPacket" not in agent.llm.last_prompt
    assert "Packet" not in agent.llm.last_prompt


def test_risk_manager_exposes_only_make_decision_as_decision_entrypoint():
    agent = RiskManagerAgent()

    assert callable(agent.make_decision)
    assert not hasattr(agent, "quick_decision")
    assert not hasattr(agent, "validator_llm")
    assert not hasattr(agent, "_reconcile_decisions")
    assert not hasattr(agent, "_action_rank")
    assert not hasattr(agent, "_risk_rank")
    assert not hasattr(agent, "_max_risk_level")
    assert not hasattr(agent, "_more_conservative_position")
    assert not hasattr(agent, "_merge_unique")
    assert not hasattr(agent, "_merge_text")
    assert not hasattr(agent, "_default_decision")
    assert not hasattr(agent, "generate_report")


def test_risk_manager_uses_required_prompt_file_without_code_fallback():
    import src.agents.risk_manager as risk_manager_module

    agent = RiskManagerAgent()

    assert not hasattr(agent, "_decision_fallback_prompt")
    assert not hasattr(risk_manager_module, "load_prompt_optional")
