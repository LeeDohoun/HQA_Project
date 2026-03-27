# 파일: src/agents/graph.py
"""
LangGraph 기반 분석 워크플로우

기존 SupervisorAgent의 _execute_stock_analysis()를 LangGraph 상태 머신으로 대체합니다.

핵심 개선점:
1. 상태 기반 실행: 각 노드가 명확한 입/출력을 가진 상태 머신
2. 조건부 라우팅: 데이터 품질에 따라 재시도/스킵 결정
3. 피드백 루프: Researcher 품질이 낮으면 다른 전략으로 재검색
4. 에러 복구: 개별 노드 실패 시 기본값으로 대체 후 계속 진행
5. 병렬 분기: Analyst / Quant / Chartist 독립 실행 (Fan-out → Fan-in)

그래프 구조:
    ┌─────────┐
    │ START   │
    └────┬────┘
         │
    ┌────▼────┐
    │ router  │  (Supervisor 쿼리 분석)
    └────┬────┘
         │
    ┌────▼─────────────────────────┐
    │  Fan-out (병렬 분기)         │
    │  ┌─────────┐ ┌──────┐ ┌────┐│
    │  │Analyst  │ │Quant │ │Chart│
    │  │(R→S)    │ │      │ │ist ││
    │  └────┬────┘ └──┬───┘ └─┬──┘│
    │       │         │       │   │
    └───────┴─────────┴───────┘   │
                │                  │
        ┌───────▼───────┐         │
        │ quality_gate  │◄────────┘
        └───────┬───────┘
                │
           ┌────▼──── (품질 D등급?)
           │         │
      Yes  ▼    No   ▼
    ┌──────────┐ ┌────────────┐
    │retry_    │ │risk_manager│
    │research  │ └────┬───────┘
    └────┬─────┘      │
         │       ┌────▼────┐
    (→ analyst)  │  END    │
                 └─────────┘
"""

import logging
import time
from typing import Dict, Any, Optional, List, TypedDict, Annotated
from dataclasses import asdict

from src.tracing.agent_tracer import AgentTracer

logger = logging.getLogger(__name__)

# LangGraph 임포트 (선택적)
try:
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False
    logger.warning(
        "langgraph 미설치 → LangGraph 워크플로우 비활성화 "
        "(pip install langgraph)"
    )


# ──────────────────────────────────────────────
# 상태 스키마
# ──────────────────────────────────────────────

class AnalysisState(TypedDict, total=False):
    """LangGraph 분석 워크플로우 상태"""
    
    # ── 입력 ──
    stock_name: str
    stock_code: str
    query: str
    
    # ── 에이전트 결과 (Any = Score dataclass) ──
    analyst_score: Any        # AnalystScore
    quant_score: Any          # QuantScore
    chartist_score: Any       # ChartistScore
    
    # ── 품질 관리 ──
    research_quality: str     # A / B / C / D
    quality_warnings: List[str]
    retry_count: int          # 재시도 횟수
    max_retries: int          # 최대 재시도 (기본 1)
    
    # ── 최종 결과 ──
    agent_scores: Any         # AgentScores
    final_decision: Any       # FinalDecision
    
    # ── 에러 추적 ──
    errors: Dict[str, str]    # {"agent_name": "error msg"}
    
    # ── 트레이싱 ──
    tracer: Any               # AgentTracer 인스턴스
    
    # ── 메타 ──
    status: str               # "running" | "completed" | "error"


# ──────────────────────────────────────────────
# 노드 함수 (에이전트별)
# ──────────────────────────────────────────────

def _analyst_node(state: AnalysisState) -> dict:
    """
    Analyst 노드: Researcher → Strategist
    
    ResearchResult의 품질 등급을 state에 기록하여
    quality_gate에서 재시도 여부를 판단합니다.
    """
    stock_name = state["stock_name"]
    stock_code = state["stock_code"]
    tracer: Optional[AgentTracer] = state.get("tracer")
    
    print(f"🔍 [LangGraph:Analyst] {stock_name} 분석 시작...")
    
    try:
        from src.agents.analyst import AnalystAgent, AnalystScore
        agent = AnalystAgent()
        
        if tracer:
            with tracer.trace_agent("analyst", f"{stock_name}({stock_code})") as span:
                # Researcher 실행
                research_result = agent.researcher.research(stock_name, stock_code)
                research_result.evaluate_quality()
                quality_grade = research_result.quality_grade
                quality_warnings = research_result.quality_warnings
                
                print(f"   📊 리서치 품질: {quality_grade}등급 ({research_result.quality_score}/100)")
                
                # Strategist 실행
                hegemony = agent.strategist.analyze_hegemony(research_result)
                
                analyst_score = AnalystScore(
                    moat_score=hegemony.moat_score,
                    growth_score=hegemony.growth_score,
                    total_score=hegemony.total_score,
                    moat_reason=hegemony.moat_analysis,
                    growth_reason=hegemony.growth_analysis,
                    report_summary=research_result.report_summary[:500],
                    image_analysis=research_result.chart_analysis[:500],
                    final_opinion=hegemony.final_opinion,
                    hegemony_grade=hegemony.hegemony_grade,
                    competitive_advantage=hegemony.competitive_advantage,
                    risk_factors=hegemony.risk_factors,
                    policy_impact=hegemony.policy_impact,
                    detailed_reasoning=hegemony.detailed_reasoning,
                )
                
                span.set_output(
                    f"{hegemony.hegemony_grade}등급 ({hegemony.total_score}/70) "
                    f"리서치품질:{quality_grade}"
                )
                span.set_reasoning(
                    hegemony.final_opinion[:200] if hegemony.final_opinion else "",
                    raw=hegemony.detailed_reasoning,
                )
                
                print(f"   ✅ Analyst 완료: {hegemony.hegemony_grade}등급 ({hegemony.total_score}/70)")
        else:
            # tracer 없이 기존 로직
            research_result = agent.researcher.research(stock_name, stock_code)
            research_result.evaluate_quality()
            quality_grade = research_result.quality_grade
            quality_warnings = research_result.quality_warnings
            hegemony = agent.strategist.analyze_hegemony(research_result)
            analyst_score = AnalystScore(
                moat_score=hegemony.moat_score,
                growth_score=hegemony.growth_score,
                total_score=hegemony.total_score,
                moat_reason=hegemony.moat_analysis,
                growth_reason=hegemony.growth_analysis,
                report_summary=research_result.report_summary[:500],
                image_analysis=research_result.chart_analysis[:500],
                final_opinion=hegemony.final_opinion,
                hegemony_grade=hegemony.hegemony_grade,
                competitive_advantage=hegemony.competitive_advantage,
                risk_factors=hegemony.risk_factors,
                policy_impact=hegemony.policy_impact,
                detailed_reasoning=hegemony.detailed_reasoning,
            )
            print(f"   ✅ Analyst 완료: {hegemony.hegemony_grade}등급 ({hegemony.total_score}/70)")
        
        return {
            "analyst_score": analyst_score,
            "research_quality": quality_grade,
            "quality_warnings": quality_warnings,
        }
        
    except Exception as e:
        logger.exception(f"Analyst 노드 오류: {e}")
        print(f"   ⚠️ Analyst 오류: {e}")
        
        if tracer:
            with tracer.trace_agent("analyst", f"{stock_name}({stock_code}) [에러복구]") as span:
                span.set_error(str(e), error_type=type(e).__name__)
        
        from src.agents.analyst import AnalystScore
        return {
            "analyst_score": AnalystScore(
                moat_score=20, growth_score=15, total_score=35,
                moat_reason="분석 오류", growth_reason="분석 오류",
                report_summary="", image_analysis="",
                final_opinion=f"오류로 인한 기본값: {str(e)[:100]}"
            ),
            "research_quality": "D",
            "quality_warnings": [f"Analyst 오류: {str(e)[:200]}"],
            "errors": {**state.get("errors", {}), "analyst": str(e)[:200]},
        }


def _quant_node(state: AnalysisState) -> dict:
    """Quant 노드: 재무 분석"""
    stock_name = state["stock_name"]
    stock_code = state["stock_code"]
    tracer: Optional[AgentTracer] = state.get("tracer")
    
    print(f"📈 [LangGraph:Quant] {stock_name} 재무 분석 시작...")
    
    try:
        from src.agents.quant import QuantAgent
        agent = QuantAgent()
        
        if tracer:
            with tracer.trace_agent("quant", f"{stock_name}({stock_code})") as span:
                quant_score = agent.full_analysis(stock_name, stock_code)
                span.set_output(
                    f"{quant_score.grade} ({quant_score.total_score}/100)"
                )
                span.set_reasoning(quant_score.opinion[:200] if quant_score.opinion else "")
        else:
            quant_score = agent.full_analysis(stock_name, stock_code)
        
        print(f"   ✅ Quant 완료: {quant_score.grade} ({quant_score.total_score}/100)")
        return {"quant_score": quant_score}
        
    except Exception as e:
        logger.exception(f"Quant 노드 오류: {e}")
        print(f"   ⚠️ Quant 오류: {e}")
        
        if tracer:
            with tracer.trace_agent("quant", f"{stock_name}({stock_code}) [에러복구]") as span:
                span.set_error(str(e), error_type=type(e).__name__)
        
        from src.agents.quant import QuantAgent
        agent = QuantAgent()
        return {
            "quant_score": agent._default_score(stock_name, str(e)),
            "errors": {**state.get("errors", {}), "quant": str(e)[:200]},
        }


def _chartist_node(state: AnalysisState) -> dict:
    """Chartist 노드: 기술적 분석"""
    stock_name = state["stock_name"]
    stock_code = state["stock_code"]
    tracer: Optional[AgentTracer] = state.get("tracer")
    
    print(f"📊 [LangGraph:Chartist] {stock_name} 기술적 분석 시작...")
    
    try:
        from src.agents.chartist import ChartistAgent
        agent = ChartistAgent()
        
        if tracer:
            with tracer.trace_agent("chartist", f"{stock_name}({stock_code})") as span:
                chartist_score = agent.full_analysis(stock_name, stock_code)
                span.set_output(
                    f"{chartist_score.signal} ({chartist_score.total_score}/100)"
                )
                span.set_reasoning(
                    f"trend:{chartist_score.trend_score} momentum:{chartist_score.momentum_score} "
                    f"volatility:{chartist_score.volatility_score} volume:{chartist_score.volume_score}"
                )
        else:
            chartist_score = agent.full_analysis(stock_name, stock_code)
        
        print(f"   ✅ Chartist 완료: {chartist_score.signal} ({chartist_score.total_score}/100)")
        return {"chartist_score": chartist_score}
        
    except Exception as e:
        logger.exception(f"Chartist 노드 오류: {e}")
        print(f"   ⚠️ Chartist 오류: {e}")
        
        if tracer:
            with tracer.trace_agent("chartist", f"{stock_name}({stock_code}) [에러복구]") as span:
                span.set_error(str(e), error_type=type(e).__name__)
        
        from src.agents.chartist import ChartistAgent
        agent = ChartistAgent()
        return {
            "chartist_score": agent._default_score(stock_code, str(e)),
            "errors": {**state.get("errors", {}), "chartist": str(e)[:200]},
        }


def _quality_gate(state: AnalysisState) -> dict:
    """
    품질 게이트: Analyst 결과 품질 검증
    
    research_quality가 'D'이고 재시도 여지가 있으면
    retry_research로 라우팅합니다.
    """
    quality = state.get("research_quality", "C")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)
    tracer: Optional[AgentTracer] = state.get("tracer")
    
    print(f"🔍 [LangGraph:QualityGate] 품질 검증: {quality}등급 (재시도: {retry_count}/{max_retries})")
    
    # 이벤트 기록
    if tracer:
        if quality == "D" and retry_count < max_retries:
            tracer.add_event(
                "quality_gate_failed",
                f"{quality}등급 → 리서치 재시도 ({retry_count + 1}/{max_retries})",
                "quality_gate",
            )
        else:
            tracer.add_event(
                "quality_gate_passed",
                f"{quality}등급 통과 (재시도: {retry_count}/{max_retries})",
                "quality_gate",
            )
    
    return {"status": "quality_checked"}


def _retry_research(state: AnalysisState) -> dict:
    """
    리서치 재시도: 다른 검색 전략으로 Analyst 재실행
    
    Plan A 실패 시 → 웹 검색 우선 + 다른 키워드 조합으로 재검색
    """
    stock_name = state["stock_name"]
    stock_code = state["stock_code"]
    retry_count = state.get("retry_count", 0) + 1
    tracer: Optional[AgentTracer] = state.get("tracer")
    
    # 이전 analyst의 agent_id 추적 (retry 흐름 연결)
    previous_analyst_id = None
    if tracer and tracer._trace:
        for at in reversed(tracer._trace.agent_traces):
            if at.agent_name == "analyst":
                previous_analyst_id = at.agent_id
                break
    
    print(f"🔄 [LangGraph:Retry] {stock_name} 리서치 재시도 ({retry_count}회차)...")
    print(f"   📎 이전 품질: {state.get('research_quality', '?')}등급")
    print(f"   📎 이전 경고: {state.get('quality_warnings', [])}")
    
    if tracer:
        tracer.add_event(
            "retry_started",
            f"리서치 재시도 {retry_count}회차 (이전 품질: {state.get('research_quality', '?')})",
            "retry_research",
        )
    
    try:
        from src.agents.analyst import AnalystAgent, AnalystScore
        agent = AnalystAgent()
        
        if tracer:
            with tracer.trace_agent("analyst_retry", f"{stock_name}({stock_code}) 재시도{retry_count}") as span:
                if previous_analyst_id:
                    span.set_retry_from(previous_analyst_id)
                
                research_result = agent.researcher.research(stock_name, stock_code)
                research_result.evaluate_quality()
                new_quality = research_result.quality_grade
                
                hegemony = agent.strategist.analyze_hegemony(research_result)
                
                analyst_score = AnalystScore(
                    moat_score=hegemony.moat_score,
                    growth_score=hegemony.growth_score,
                    total_score=hegemony.total_score,
                    moat_reason=hegemony.moat_analysis,
                    growth_reason=hegemony.growth_analysis,
                    report_summary=research_result.report_summary[:500],
                    image_analysis=research_result.chart_analysis[:500],
                    final_opinion=hegemony.final_opinion,
                    hegemony_grade=hegemony.hegemony_grade,
                    competitive_advantage=hegemony.competitive_advantage,
                    risk_factors=hegemony.risk_factors,
                    policy_impact=hegemony.policy_impact,
                    detailed_reasoning=hegemony.detailed_reasoning,
                )
                
                span.set_output(
                    f"재시도 결과: {new_quality}등급 ({hegemony.total_score}/70)"
                )
                span.set_reasoning(
                    hegemony.final_opinion[:200] if hegemony.final_opinion else "",
                    raw=hegemony.detailed_reasoning,
                )
        else:
            research_result = agent.researcher.research(stock_name, stock_code)
            research_result.evaluate_quality()
            new_quality = research_result.quality_grade
            hegemony = agent.strategist.analyze_hegemony(research_result)
            analyst_score = AnalystScore(
                moat_score=hegemony.moat_score,
                growth_score=hegemony.growth_score,
                total_score=hegemony.total_score,
                moat_reason=hegemony.moat_analysis,
                growth_reason=hegemony.growth_analysis,
                report_summary=research_result.report_summary[:500],
                image_analysis=research_result.chart_analysis[:500],
                final_opinion=hegemony.final_opinion,
                hegemony_grade=hegemony.hegemony_grade,
                competitive_advantage=hegemony.competitive_advantage,
                risk_factors=hegemony.risk_factors,
                policy_impact=hegemony.policy_impact,
                detailed_reasoning=hegemony.detailed_reasoning,
            )
        
        print(f"   📊 재시도 리서치 품질: {new_quality}등급 ({research_result.quality_score}/100)")
        
        return {
            "analyst_score": analyst_score,
            "research_quality": new_quality,
            "quality_warnings": research_result.quality_warnings,
            "retry_count": retry_count,
        }
        
    except Exception as e:
        logger.exception(f"리서치 재시도 오류: {e}")
        print(f"   ⚠️ 재시도 오류: {e}")
        
        if tracer:
            with tracer.trace_agent("analyst_retry", f"{stock_name}({stock_code}) 재시도{retry_count} [에러]") as span:
                span.set_error(str(e), error_type=type(e).__name__)
        
        return {
            "retry_count": retry_count,
            "errors": {**state.get("errors", {}), "retry_research": str(e)[:200]},
        }


def _risk_manager_node(state: AnalysisState) -> dict:
    """
    Risk Manager 노드: 최종 투자 판단
    
    3개 에이전트 결과를 종합하여 최종 결정을 내립니다.
    """
    stock_name = state["stock_name"]
    stock_code = state["stock_code"]
    tracer: Optional[AgentTracer] = state.get("tracer")
    
    analyst_score = state.get("analyst_score")
    quant_score = state.get("quant_score")
    chartist_score = state.get("chartist_score")
    
    print(f"🎯 [LangGraph:RiskManager] {stock_name} 최종 판단...")
    
    try:
        from src.agents import AgentScores, RiskManagerAgent
        
        agent_scores = AgentScores(
            analyst_moat_score=analyst_score.moat_score,
            analyst_growth_score=analyst_score.growth_score,
            analyst_total=analyst_score.total_score,
            analyst_grade=analyst_score.hegemony_grade,
            analyst_opinion=analyst_score.final_opinion,
            quant_valuation_score=quant_score.valuation_score,
            quant_profitability_score=quant_score.profitability_score,
            quant_growth_score=quant_score.growth_score,
            quant_stability_score=quant_score.stability_score,
            quant_total=quant_score.total_score,
            quant_opinion=quant_score.opinion,
            chartist_trend_score=chartist_score.trend_score,
            chartist_momentum_score=chartist_score.momentum_score,
            chartist_volatility_score=chartist_score.volatility_score,
            chartist_volume_score=chartist_score.volume_score,
            chartist_total=chartist_score.total_score,
            chartist_signal=chartist_score.signal,
        )
        
        agent = RiskManagerAgent()
        
        if tracer:
            input_detail = (
                f"Analyst:{analyst_score.total_score}/70 "
                f"Quant:{quant_score.total_score}/100 "
                f"Chartist:{chartist_score.total_score}/100"
            )
            with tracer.trace_agent("risk_manager", input_detail) as span:
                final_decision = agent.make_decision(stock_name, stock_code, agent_scores)
                span.set_output(
                    f"{final_decision.action.value} ({final_decision.total_score}/270) "
                    f"리스크:{final_decision.risk_level.value}"
                )
                span.set_reasoning(
                    final_decision.summary[:200] if final_decision.summary else "",
                    raw=final_decision.summary,
                )
        else:
            final_decision = agent.make_decision(stock_name, stock_code, agent_scores)
        
        print(f"   ✅ 최종 판단: {final_decision.action.value} "
              f"(종합 {final_decision.total_score}/270점)")
        
        return {
            "agent_scores": agent_scores,
            "final_decision": final_decision,
            "status": "completed",
        }
        
    except Exception as e:
        logger.exception(f"Risk Manager 노드 오류: {e}")
        print(f"   ⚠️ Risk Manager 오류: {e}")
        
        if tracer:
            with tracer.trace_agent("risk_manager", f"{stock_name} [에러]") as span:
                span.set_error(str(e), error_type=type(e).__name__)
        
        return {
            "status": "error",
            "errors": {**state.get("errors", {}), "risk_manager": str(e)[:200]},
        }


# ──────────────────────────────────────────────
# 조건부 라우팅
# ──────────────────────────────────────────────

def _should_retry_research(state: AnalysisState) -> str:
    """
    품질 게이트의 조건부 엣지:
    - D등급 + 재시도 여지 있음 → "retry"
    - 그 외                  → "proceed"
    """
    quality = state.get("research_quality", "C")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)
    
    if quality == "D" and retry_count < max_retries:
        print(f"   🔄 품질 D등급 → 리서치 재시도 ({retry_count + 1}/{max_retries})")
        return "retry"
    
    if quality == "D":
        print(f"   ⚠️ 품질 D등급이지만 재시도 한도 초과 → 진행")
    
    return "proceed"


# ──────────────────────────────────────────────
# 그래프 빌더
# ──────────────────────────────────────────────

def build_analysis_graph() -> Optional['StateGraph']:
    """
    종목 분석 LangGraph 워크플로우 구성
    
    Returns:
        컴파일된 StateGraph 또는 None (langgraph 미설치 시)
    """
    if not _LANGGRAPH_AVAILABLE:
        logger.warning("langgraph 미설치 → 그래프 빌드 불가")
        return None
    
    graph = StateGraph(AnalysisState)
    
    # ── 노드 등록 ──
    graph.add_node("analyst", _analyst_node)
    graph.add_node("quant", _quant_node)
    graph.add_node("chartist", _chartist_node)
    graph.add_node("quality_gate", _quality_gate)
    graph.add_node("retry_research", _retry_research)
    graph.add_node("risk_manager", _risk_manager_node)
    
    # ── 엣지: START → 병렬 분기 (3개 에이전트) ──
    # LangGraph에서 Fan-out은 START에서 여러 노드로 동시 엣지를 추가
    graph.add_edge(START, "analyst")
    graph.add_edge(START, "quant")
    graph.add_edge(START, "chartist")
    
    # ── 엣지: 3개 에이전트 → quality_gate (Fan-in) ──
    graph.add_edge("analyst", "quality_gate")
    graph.add_edge("quant", "quality_gate")
    graph.add_edge("chartist", "quality_gate")
    
    # ── 조건부 엣지: quality_gate → retry 또는 proceed ──
    graph.add_conditional_edges(
        "quality_gate",
        _should_retry_research,
        {
            "retry": "retry_research",
            "proceed": "risk_manager",
        }
    )
    
    # ── 엣지: retry_research → quality_gate (피드백 루프) ──
    graph.add_edge("retry_research", "quality_gate")
    
    # ── 엣지: risk_manager → END ──
    graph.add_edge("risk_manager", END)
    
    compiled = graph.compile()
    logger.info("LangGraph 분석 워크플로우 컴파일 완료")
    
    return compiled


# ──────────────────────────────────────────────
# 실행 헬퍼
# ──────────────────────────────────────────────

# 컴파일된 그래프 캐시 (싱글턴)
_cached_graph = None


def get_analysis_graph():
    """컴파일된 분석 그래프 반환 (캐시)"""
    global _cached_graph
    if _cached_graph is None:
        _cached_graph = build_analysis_graph()
    return _cached_graph


def run_stock_analysis(
    stock_name: str,
    stock_code: str,
    query: str = "",
    max_retries: int = 1,
    debug_trace: bool = False,
) -> Dict[str, Any]:
    """
    LangGraph 워크플로우로 종목 분석 실행
    
    LangGraph가 설치되지 않은 경우 기존 병렬 실행 방식으로 폴백합니다.
    
    Args:
        stock_name: 종목명
        stock_code: 종목코드
        query: 원본 쿼리
        max_retries: 리서치 재시도 최대 횟수
        
    Returns:
        분석 결과 딕셔너리 (supervisor.execute() 호환 형식)
    """
    # 트레이서 생성
    tracer = AgentTracer(debug=debug_trace)
    
    graph = get_analysis_graph()
    
    if graph is None:
        # LangGraph 미설치 → 폴백
        tracer.start_trace(stock_name, stock_code, "fallback_parallel", query)
        tracer.set_fallback_reason("langgraph 미설치")
        return _fallback_parallel_analysis(stock_name, stock_code, tracer=tracer)
    
    print(f"\n🚀 [LangGraph] {stock_name}({stock_code}) 분석 워크플로우 시작")
    print(f"   ⚡ Analyst / Quant / Chartist 병렬 분기")
    print(f"   🔄 품질 게이트 활성 (최대 재시도: {max_retries}회)")
    
    # 트레이스 시작
    tracer.start_trace(stock_name, stock_code, "langgraph", query)
    
    # 초기 상태
    initial_state: AnalysisState = {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "query": query,
        "retry_count": 0,
        "max_retries": max_retries,
        "errors": {},
        "status": "running",
        "quality_warnings": [],
        "tracer": tracer,
    }
    
    try:
        # 그래프 실행
        final_state = graph.invoke(initial_state)
        
        # 결과 변환 (supervisor.execute() 호환)
        result = {
            "status": "success",
            "stock": {"name": stock_name, "code": stock_code},
            "scores": {},
        }
        
        if final_state.get("analyst_score"):
            result["scores"]["analyst"] = final_state["analyst_score"]
        if final_state.get("quant_score"):
            result["scores"]["quant"] = final_state["quant_score"]
        if final_state.get("chartist_score"):
            result["scores"]["chartist"] = final_state["chartist_score"]
        if final_state.get("final_decision"):
            result["final_decision"] = final_state["final_decision"]
        
        # 품질 정보 추가
        result["research_quality"] = final_state.get("research_quality", "?")
        result["quality_warnings"] = final_state.get("quality_warnings", [])
        result["retry_count"] = final_state.get("retry_count", 0)
        
        # 에러 추적
        if final_state.get("errors"):
            result["warnings"] = final_state["errors"]
        
        # 트레이스 종료
        final_decision = final_state.get("final_decision")
        final_summary = ""
        if final_decision:
            final_summary = (
                f"{final_decision.action.value} ({final_decision.total_score}/270) "
                f"리스크:{final_decision.risk_level.value}"
            )
        
        saved_path = tracer.finish_trace(
            final_result_summary=final_summary,
            research_quality=final_state.get("research_quality", "?"),
            retry_count=final_state.get("retry_count", 0),
        )
        
        # 트레이스 결과 첨부
        result["trace"] = tracer.to_dict()
        if saved_path:
            result["trace_file"] = saved_path
            print(f"📝 [Tracer] 트레이스 저장: {saved_path}")
        
        return result
        
    except Exception as e:
        logger.exception(f"LangGraph 워크플로우 오류: {e}")
        print(f"⚠️ LangGraph 오류 → 기존 방식으로 폴백: {e}")
        
        tracer.set_fallback_reason(f"LangGraph 오류: {str(e)[:200]}")
        tracer.finish_trace(status="error")
        
        # 폴백 시 새 tracer 생성
        fallback_tracer = AgentTracer(debug=debug_trace)
        fallback_tracer.start_trace(stock_name, stock_code, "fallback_parallel", query)
        fallback_tracer.set_fallback_reason(f"LangGraph 오류: {str(e)[:100]}")
        return _fallback_parallel_analysis(stock_name, stock_code, tracer=fallback_tracer)


def _fallback_parallel_analysis(
    stock_name: str,
    stock_code: str,
    tracer: Optional[AgentTracer] = None,
) -> Dict[str, Any]:
    """
    LangGraph 미설치 시 기존 병렬 실행 방식 (폴백)
    
    src.utils.parallel.run_agents_parallel 을 사용합니다.
    tracer가 주입되면 각 에이전트 실행 전후로 타이밍을 기록합니다.
    """
    from src.utils.parallel import run_agents_parallel, is_error
    from src.agents import (
        AnalystAgent, QuantAgent, ChartistAgent,
        RiskManagerAgent, AgentScores, AnalystScore,
    )
    
    print(f"\n🚀 [Fallback] {stock_name}({stock_code}) 병렬 분석 시작...")
    
    agents = {
        "analyst": AnalystAgent(),
        "quant": QuantAgent(),
        "chartist": ChartistAgent(),
    }
    
    # Phase 1: 병렬 실행 (타이밍 측정)
    t0 = time.time()
    parallel_results = run_agents_parallel({
        "analyst":  (agents["analyst"].full_analysis,  (stock_name, stock_code)),
        "quant":    (agents["quant"].full_analysis,    (stock_name, stock_code)),
        "chartist": (agents["chartist"].full_analysis, (stock_name, stock_code)),
    })
    parallel_elapsed = time.time() - t0
    
    analyst_score = parallel_results.get("analyst")
    quant_score = parallel_results.get("quant")
    chartist_score = parallel_results.get("chartist")
    
    # 트레이싱: 병렬 실행 결과 기록
    if tracer:
        # Analyst
        if is_error(analyst_score):
            with tracer.trace_agent("analyst", f"{stock_name}({stock_code}) [fallback]") as span:
                span.set_error(str(analyst_score), error_type="parallel_error")
        else:
            with tracer.trace_agent("analyst", f"{stock_name}({stock_code}) [fallback]") as span:
                span.set_output(
                    f"{analyst_score.hegemony_grade}등급 ({analyst_score.total_score}/70)"
                )
                span.set_reasoning(
                    analyst_score.final_opinion[:200] if analyst_score.final_opinion else ""
                )
        
        # Quant
        if is_error(quant_score):
            with tracer.trace_agent("quant", f"{stock_name}({stock_code}) [fallback]") as span:
                span.set_error(str(quant_score), error_type="parallel_error")
        else:
            with tracer.trace_agent("quant", f"{stock_name}({stock_code}) [fallback]") as span:
                span.set_output(
                    f"{quant_score.grade} ({quant_score.total_score}/100)"
                )
                span.set_reasoning(quant_score.opinion[:200] if quant_score.opinion else "")
        
        # Chartist
        if is_error(chartist_score):
            with tracer.trace_agent("chartist", f"{stock_name}({stock_code}) [fallback]") as span:
                span.set_error(str(chartist_score), error_type="parallel_error")
        else:
            with tracer.trace_agent("chartist", f"{stock_name}({stock_code}) [fallback]") as span:
                span.set_output(
                    f"{chartist_score.signal} ({chartist_score.total_score}/100)"
                )
                span.set_reasoning(
                    f"trend:{chartist_score.trend_score} momentum:{chartist_score.momentum_score}"
                )
        
        tracer.set_metadata("parallel_elapsed_seconds", round(parallel_elapsed, 3))
    
    # 에러 복구
    if is_error(analyst_score):
        analyst_score = AnalystScore(
            moat_score=20, growth_score=15, total_score=35,
            moat_reason="분석 오류", growth_reason="분석 오류",
            report_summary="", image_analysis="",
            final_opinion=f"오류: {str(analyst_score)[:100]}"
        )
    if is_error(quant_score):
        quant_score = agents["quant"]._default_score(stock_name, str(quant_score))
    if is_error(chartist_score):
        chartist_score = agents["chartist"]._default_score(stock_code, str(chartist_score))
    
    results = {
        "status": "success",
        "stock": {"name": stock_name, "code": stock_code},
        "scores": {
            "analyst": analyst_score,
            "quant": quant_score,
            "chartist": chartist_score,
        },
    }
    
    # Phase 2: Risk Manager
    agent_scores = AgentScores(
        analyst_moat_score=analyst_score.moat_score,
        analyst_growth_score=analyst_score.growth_score,
        analyst_total=analyst_score.total_score,
        analyst_grade=analyst_score.hegemony_grade,
        analyst_opinion=analyst_score.final_opinion,
        quant_valuation_score=quant_score.valuation_score,
        quant_profitability_score=quant_score.profitability_score,
        quant_growth_score=quant_score.growth_score,
        quant_stability_score=quant_score.stability_score,
        quant_total=quant_score.total_score,
        quant_opinion=quant_score.opinion,
        chartist_trend_score=chartist_score.trend_score,
        chartist_momentum_score=chartist_score.momentum_score,
        chartist_volatility_score=chartist_score.volatility_score,
        chartist_volume_score=chartist_score.volume_score,
        chartist_total=chartist_score.total_score,
        chartist_signal=chartist_score.signal,
    )
    
    risk_manager = RiskManagerAgent()
    
    if tracer:
        input_detail = (
            f"Analyst:{analyst_score.total_score}/70 "
            f"Quant:{quant_score.total_score}/100 "
            f"Chartist:{chartist_score.total_score}/100"
        )
        with tracer.trace_agent("risk_manager", input_detail) as span:
            final_decision = risk_manager.make_decision(stock_name, stock_code, agent_scores)
            span.set_output(
                f"{final_decision.action.value} ({final_decision.total_score}/270) "
                f"리스크:{final_decision.risk_level.value}"
            )
            span.set_reasoning(
                final_decision.summary[:200] if final_decision.summary else ""
            )
    else:
        final_decision = risk_manager.make_decision(stock_name, stock_code, agent_scores)
    
    results["final_decision"] = final_decision
    
    # 트레이스 종료
    if tracer:
        final_summary = (
            f"{final_decision.action.value} ({final_decision.total_score}/270) "
            f"리스크:{final_decision.risk_level.value}"
        )
        saved_path = tracer.finish_trace(final_result_summary=final_summary)
        results["trace"] = tracer.to_dict()
        if saved_path:
            results["trace_file"] = saved_path
            print(f"📝 [Tracer] 트레이스 저장: {saved_path}")
    
    return results


# 사용 가능 여부 확인
def is_langgraph_available() -> bool:
    """LangGraph 설치 여부 확인"""
    return _LANGGRAPH_AVAILABLE
