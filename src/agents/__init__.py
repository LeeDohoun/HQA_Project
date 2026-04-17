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
        HegemonyScore,
        ResearcherAgent,
        StrategistAgent,
    )
except Exception as exc:  # pragma: no cover
    AnalystAgent = _missing_dependency("AnalystAgent", exc)
    AnalystScore = None
    ResearchResult = None
    HegemonyScore = None
    ResearcherAgent = _missing_dependency("ResearcherAgent", exc)
    StrategistAgent = _missing_dependency("StrategistAgent", exc)

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
    from .context import AgentContextPacket, EvidenceItem
except Exception as exc:  # pragma: no cover
    AgentContextPacket = None
    EvidenceItem = None

try:
    from .candidate_filter import (
        CandidateFilter,
        CandidateFilterConfig,
        CandidateFilterResult,
    )
except Exception as exc:  # pragma: no cover
    CandidateFilter = _missing_dependency("CandidateFilter", exc)
    CandidateFilterConfig = None
    CandidateFilterResult = None

try:
    from .conflict_detector import ConflictDetector, ConflictReport
except Exception as exc:  # pragma: no cover
    ConflictDetector = _missing_dependency("ConflictDetector", exc)
    ConflictReport = None

try:
    from .graph import run_stock_analysis, is_langgraph_available, AnalysisState
except Exception as exc:  # pragma: no cover
    run_stock_analysis = _missing_dependency("run_stock_analysis", exc)
    is_langgraph_available = _missing_dependency("is_langgraph_available", exc)
    AnalysisState = None

try:
    from .llm_config import (
        get_instruct_llm,
        get_thinking_llm,
        get_vision_llm,
        VisionAnalyzer,
        get_llm_info,
        get_gemini_llm,
        get_gemini_vision_llm,
        GeminiVisionAnalyzer,
    )
except Exception as exc:  # pragma: no cover
    get_instruct_llm = _missing_dependency("get_instruct_llm", exc)
    get_thinking_llm = _missing_dependency("get_thinking_llm", exc)
    get_vision_llm = _missing_dependency("get_vision_llm", exc)
    VisionAnalyzer = _missing_dependency("VisionAnalyzer", exc)
    get_llm_info = _missing_dependency("get_llm_info", exc)
    get_gemini_llm = _missing_dependency("get_gemini_llm", exc)
    get_gemini_vision_llm = _missing_dependency("get_gemini_vision_llm", exc)
    GeminiVisionAnalyzer = _missing_dependency("GeminiVisionAnalyzer", exc)

try:
    from .theme_orchestrator import ThemeLeaderOrchestrator, ThemeCandidate
except Exception as exc:  # pragma: no cover
    ThemeLeaderOrchestrator = _missing_dependency("ThemeLeaderOrchestrator", exc)
    ThemeCandidate = None


__all__ = [
    "SupervisorAgent",
    "QueryAnalysis",
    "Intent",
    "AnalystAgent",
    "AnalystScore",
    "ResearchResult",
    "HegemonyScore",
    "ResearcherAgent",
    "StrategistAgent",
    "QuantAgent",
    "QuantScore",
    "ChartistAgent",
    "ChartistScore",
    "RiskManagerAgent",
    "AgentScores",
    "FinalDecision",
    "InvestmentAction",
    "RiskLevel",
    "AgentContextPacket",
    "EvidenceItem",
    "CandidateFilter",
    "CandidateFilterConfig",
    "CandidateFilterResult",
    "ConflictDetector",
    "ConflictReport",
    "get_instruct_llm",
    "get_thinking_llm",
    "get_vision_llm",
    "VisionAnalyzer",
    "get_llm_info",
    "get_gemini_llm",
    "get_gemini_vision_llm",
    "GeminiVisionAnalyzer",
    "ThemeLeaderOrchestrator",
    "ThemeCandidate",
    "run_stock_analysis",
    "is_langgraph_available",
    "AnalysisState",
]
