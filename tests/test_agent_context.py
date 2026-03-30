import importlib.util
import sys
from pathlib import Path

from src.utils.prompt_loader import load_prompt


_CONTEXT_PATH = Path(__file__).resolve().parents[1] / "src" / "agents" / "context.py"
_SPEC = importlib.util.spec_from_file_location("agent_context", _CONTEXT_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

AgentContextPacket = _MODULE.AgentContextPacket
EvidenceItem = _MODULE.EvidenceItem


def test_context_packet_renders_sections():
    packet = AgentContextPacket(
        agent_name="analyst",
        stock_name="삼성전자",
        stock_code="005930",
        summary="장기 해자는 강하지만 단기 밸류 부담이 있음",
        key_points=["HBM 경쟁력 우위", "실적 가시성 양호"],
        risks=["밸류에이션 부담", "업황 변동성"],
        catalysts=["실적 개선"],
        contrarian_view="단기 조정 가능성",
        evidence=[EvidenceItem(source="rag", title="리포트", snippet="증권사 리포트 요약")],
        score=68,
        confidence=72,
        grade="B",
        next_action="risk_manager_review",
    )

    text = packet.to_prompt_block()

    assert "### analyst" in text
    assert "HBM 경쟁력 우위" in text
    assert "밸류에이션 부담" in text
    assert "증권사 리포트 요약" in text


def test_prompt_templates_render_with_new_schema():
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

    risk_prompt = load_prompt(
        "risk_manager",
        "decision",
        stock_name="삼성전자",
        stock_code="005930",
        analyst_total=60,
        analyst_grade="B",
        quant_total=70,
        chartist_total=65,
        analyst_context="### analyst\n- 요약: 장기 해자는 강함",
        quant_context="### quant\n- 요약: 재무 양호",
        chartist_context="### chartist\n- 요약: 기술적 중립",
    )

    assert "삼성전자" in analyst_prompt
    assert "QUALITY" not in analyst_prompt
    assert "### analyst" in risk_prompt
    assert "### quant" in risk_prompt
    assert "### chartist" in risk_prompt
