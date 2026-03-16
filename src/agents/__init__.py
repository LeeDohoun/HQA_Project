# 파일: src/agents/__init__.py
"""
HQA 에이전트 모음

구조:
┌─────────────────────────────────────────────────────────────┐
│  SupervisorAgent        ──→  쿼리 분석 & 라우팅 (입구)       │
├─────────────────────────────────────────────────────────────┤
│  Researcher (Instruct)  ──→  정보 수집/요약 (빠름)           │
│  Strategist (Thinking)  ──→  헤게모니 판단 (깊은 추론)       │
│  AnalystAgent           ──→  Researcher + Strategist 통합    │
│                                                             │
│  QuantAgent (Instruct)  ──→  재무 지표 분석                  │
│  ChartistAgent (Instruct)──→ 기술적 지표 분석                │
│                                                             │
│  RiskManagerAgent (Thinking) ──→ 최종 투자 판단 ✅           │
└─────────────────────────────────────────────────────────────┘

LLM Config (Provider 패턴 — Ollama / Gemini 전환 가능):
- get_instruct_llm: Instruct (빠름) → Ollama 또는 Gemini
- get_thinking_llm: Thinking (깊은 추론) → Ollama 또는 Gemini
- get_vision_llm: 이미지 분석 → Ollama (llava) 또는 Gemini
- VisionAnalyzer: Vision 헬퍼 → 자동 Provider 선택
"""

# Supervisor (쿼리 분석 & 라우팅)
from .supervisor import (
    SupervisorAgent,
    QueryAnalysis,
    Intent,
)

# 메인 에이전트
from .analyst import AnalystAgent, AnalystScore

# 하위 에이전트 (Analyst 내부)
from .researcher import ResearcherAgent, ResearchResult
from .strategist import StrategistAgent, HegemonyScore

# 다른 에이전트
from .quant import QuantAgent, QuantScore
from .chartist import ChartistAgent, ChartistScore

# 최종 판단 에이전트
from .risk_manager import (
    RiskManagerAgent,
    AgentScores,
    FinalDecision,
    InvestmentAction,
    RiskLevel,
)

# LangGraph 워크플로우
from .graph import (
    run_stock_analysis,
    is_langgraph_available,
    AnalysisState,
)

# LLM 설정
from .llm_config import (
    get_instruct_llm,
    get_thinking_llm,
    get_vision_llm,
    VisionAnalyzer,
    get_llm_info,
    # 하위 호환 별칭
    get_gemini_llm,
    get_gemini_vision_llm,
    GeminiVisionAnalyzer,
)

__all__ = [
    # Supervisor (입구)
    "SupervisorAgent",
    "QueryAnalysis",
    "Intent",
    
    # 메인 에이전트
    "AnalystAgent",
    "AnalystScore",
    
    # 하위 에이전트
    "ResearcherAgent",
    "ResearchResult",
    "StrategistAgent",
    "HegemonyScore",
    
    # 다른 에이전트
    "QuantAgent",
    "QuantScore",
    "ChartistAgent",
    "ChartistScore",
    
    # 리스크 매니저
    "RiskManagerAgent",
    "AgentScores",
    "FinalDecision",
    "InvestmentAction",
    "RiskLevel",
    
    # LLM (Provider 패턴)
    "get_instruct_llm",
    "get_thinking_llm",
    "get_vision_llm",
    "VisionAnalyzer",
    "get_llm_info",
    # LLM (하위 호환)
    "get_gemini_llm",
    "get_gemini_vision_llm",
    "GeminiVisionAnalyzer",
    
    # LangGraph
    "run_stock_analysis",
    "is_langgraph_available",
    "AnalysisState",
]
