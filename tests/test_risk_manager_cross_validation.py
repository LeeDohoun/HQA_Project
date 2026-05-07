from types import SimpleNamespace

from src.agents.risk_manager import (
    AgentScores,
    InvestmentAction,
    RiskLevel,
    RiskManagerAgent,
)


class FakeLLM:
    def __init__(self, payload: str, should_fail: bool = False):
        self.payload = payload
        self.should_fail = should_fail

    def invoke(self, prompt: str):
        if self.should_fail:
            raise RuntimeError("validator unavailable")
        return SimpleNamespace(content=self.payload)


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


def test_cross_validation_passes_when_models_align():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("BUY", total_score=74, confidence=78))
    agent.validator_llm = FakeLLM(build_response("BUY", total_score=70, confidence=74))
    agent.primary_model_name = "primary-test"
    agent.validator_model_name = "validator-test"

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert decision.action == InvestmentAction.BUY
    assert decision.validation_status == "passed"
    assert decision.primary_model == "primary-test"
    assert decision.validator_model == "validator-test"
    assert decision.validator_action == InvestmentAction.BUY.value


def test_cross_validation_uses_conservative_result_on_large_disagreement():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("STRONG_BUY", total_score=92, confidence=88))
    agent.validator_llm = FakeLLM(build_response("REDUCE", total_score=42, confidence=61))

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert decision.action == InvestmentAction.REDUCE
    assert decision.validation_status == "warning"
    assert decision.total_score == 42
    assert decision.confidence < 61


def test_cross_validation_falls_back_when_validator_fails():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("HOLD", total_score=58, confidence=66))
    agent.validator_llm = FakeLLM("", should_fail=True)

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert decision.action == InvestmentAction.HOLD
    assert decision.validation_status == "unavailable"
    assert "보조 모델 검증 실패" in decision.validation_summary
    assert decision.risk_level == RiskLevel.MEDIUM


def test_decision_parser_recovers_when_response_has_trailing_extra_data():
    agent = RiskManagerAgent()
    agent.llm = FakeLLM(build_response("BUY", total_score=74, confidence=78) + "\n{\"extra\": true}")
    agent.validator_llm = None

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
    agent.validator_llm = None

    decision = agent.make_decision("삼성전자", "005930", make_scores())

    assert agent.llm.method == "json_schema"
    assert decision.action == InvestmentAction.BUY
    assert decision.risk_level == RiskLevel.LOW
    assert decision.summary == "구조화 응답"
