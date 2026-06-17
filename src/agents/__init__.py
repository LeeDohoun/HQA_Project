# 파일: src/agents/__init__.py
"""
HQA 에이전트 public surface.

선택 의존성이 없는 에이전트/헬퍼는 바로 import하고,
누락된 의존성이 있는 모듈은 지연 실패(명확한 ImportError)로 노출합니다.
"""


def _missing_dependency(symbol: str, exc: Exception):
    def _raiser(*args, **kwargs):
        raise ImportError(f"{symbol} 사용에 필요한 의존성이 없습니다: {exc}") from exc

    return _raiser


try:
    from .supervisor import SupervisorAgent, QueryAnalysis, Intent
except Exception as exc:  # pragma: no cover - optional import surface
    SupervisorAgent = _missing_dependency("SupervisorAgent", exc)
    QueryAnalysis = None
    Intent = None

try:
    from .analyst import (
        AnalystAgent,
        AnalystScore,
        ResearchResult,
    )
except Exception as exc:  # pragma: no cover
    AnalystAgent = _missing_dependency("AnalystAgent", exc)
    AnalystScore = None
    ResearchResult = None

try:
    from .quant import QuantAgent, QuantScore
except Exception as exc:  # pragma: no cover
    QuantAgent = _missing_dependency("QuantAgent", exc)
    QuantScore = None

try:
    from .chartist import ChartistAgent, ChartistScore
except Exception as exc:  # pragma: no cover
    ChartistAgent = _missing_dependency("ChartistAgent", exc)
    ChartistScore = None

try:
    from .risk_manager import (
        RiskManagerAgent,
        AgentScores,
        FinalDecision,
        InvestmentAction,
        RiskLevel,
    )
except Exception as exc:  # pragma: no cover
    RiskManagerAgent = _missing_dependency("RiskManagerAgent", exc)
    AgentScores = None
    FinalDecision = None
    InvestmentAction = None
    RiskLevel = None

try:
    from .llm_config import (
        get_instruct_llm,
        get_thinking_llm,
        get_analyst_llm,
        get_summary_llm,
        get_quant_llm,
        get_chartist_llm,
        get_risk_manager_llm,
        get_llm_info,
        get_llm_config,
    )
except Exception as exc:  # pragma: no cover
    get_instruct_llm = _missing_dependency("get_instruct_llm", exc)
    get_thinking_llm = _missing_dependency("get_thinking_llm", exc)
    get_analyst_llm = _missing_dependency("get_analyst_llm", exc)
    get_summary_llm = _missing_dependency("get_summary_llm", exc)
    get_quant_llm = _missing_dependency("get_quant_llm", exc)
    get_chartist_llm = _missing_dependency("get_chartist_llm", exc)
    get_risk_manager_llm = _missing_dependency("get_risk_manager_llm", exc)
    get_llm_info = _missing_dependency("get_llm_info", exc)
    get_llm_config = _missing_dependency("get_llm_config", exc)

__all__ = [
    "SupervisorAgent",
    "QueryAnalysis",
    "Intent",
    "AnalystAgent",
    "AnalystScore",
    "ResearchResult",
    "QuantAgent",
    "QuantScore",
    "ChartistAgent",
    "ChartistScore",
    "RiskManagerAgent",
    "AgentScores",
    "FinalDecision",
    "InvestmentAction",
    "RiskLevel",
    "get_instruct_llm",
    "get_thinking_llm",
    "get_analyst_llm",
    "get_summary_llm",
    "get_quant_llm",
    "get_chartist_llm",
    "get_risk_manager_llm",
    "get_llm_info",
    "get_llm_config",
]
