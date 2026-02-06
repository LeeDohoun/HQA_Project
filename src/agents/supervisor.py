# 파일: src/agents/supervisor.py
"""
Supervisor Agent (수퍼바이저 에이전트)

역할: 사용자 쿼리 분석 및 실행 계획 수립
- 의도 파악 (Intent Classification)
- 엔티티 추출 (종목명, 산업, 이슈 등)
- 적절한 에이전트/도구 조합 선택
- 결과 통합 및 응답 생성

모델: Instruct (빠른 처리, 복잡한 추론 불필요)
"""

import json
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from src.agents.llm_config import get_gemini_llm
from src.utils.stock_mapper import StockMapper, get_mapper
from src.utils.memory import ConversationMemory
from src.utils.parallel import run_agents_parallel, is_error


class Intent(Enum):
    """쿼리 의도 분류"""
    STOCK_ANALYSIS = "stock_analysis"      # 종목 분석 (전체 파이프라인)
    QUICK_ANALYSIS = "quick_analysis"      # 빠른 분석 (Quant + Chartist만)
    INDUSTRY_ANALYSIS = "industry"         # 산업 동향 분석
    ISSUE_ANALYSIS = "issue"               # 글로벌 이슈 분석
    REALTIME_PRICE = "price"               # 실시간 시세 조회
    COMPARISON = "comparison"              # 종목 비교
    THEME_SCREENING = "theme"              # 테마/섹터 종목 탐색
    GENERAL_QA = "general"                 # 일반 질문
    UNKNOWN = "unknown"


@dataclass
class QueryAnalysis:
    """쿼리 분석 결과"""
    original_query: str                    # 원본 쿼리
    intent: Intent                         # 의도
    
    # 추출된 엔티티
    stocks: List[Dict[str, str]] = field(default_factory=list)  # [{"name": "삼성전자", "code": "005930"}]
    industry: Optional[str] = None         # 산업명
    issue: Optional[str] = None            # 이슈/키워드
    theme: Optional[str] = None            # 테마
    
    # 실행 계획
    required_agents: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    execution_plan: List[str] = field(default_factory=list)
    
    # 메타
    confidence: float = 0.0                # 분석 신뢰도
    needs_clarification: bool = False      # 추가 질문 필요 여부
    clarification_message: str = ""        # 추가 질문 내용


class SupervisorAgent:
    """
    수퍼바이저 에이전트
    
    사용자 쿼리를 분석하고 적절한 에이전트/도구 조합을 선택하여
    실행 계획을 수립합니다.
    
    Example:
        supervisor = SupervisorAgent()
        
        # 쿼리 분석
        analysis = supervisor.analyze("삼성전자 분석해줘")
        print(analysis.intent)  # Intent.STOCK_ANALYSIS
        print(analysis.stocks)  # [{"name": "삼성전자", "code": "005930"}]
        
        # 실행
        result = supervisor.execute("삼성전자 분석해줘")
    """
    
    def __init__(self, memory: Optional[ConversationMemory] = None):
        self.llm = get_gemini_llm()
        self.stock_mapper = get_mapper()  # 분리된 StockMapper 사용
        self.memory = memory or ConversationMemory(max_turns=10)
        
        # 에이전트 지연 로딩 (순환 임포트 방지)
        self._agents = None
        self._tools = None
    
    @property
    def agents(self):
        """에이전트 지연 로딩"""
        if self._agents is None:
            from src.agents import (
                AnalystAgent, QuantAgent, 
                ChartistAgent, RiskManagerAgent
            )
            self._agents = {
                "analyst": AnalystAgent(),
                "quant": QuantAgent(),
                "chartist": ChartistAgent(),
                "risk_manager": RiskManagerAgent(),
            }
        return self._agents
    
    @property
    def tools(self):
        """도구 지연 로딩"""
        if self._tools is None:
            self._tools = {}
            
            # 실시간 시세 (선택적)
            try:
                from src.tools.realtime_tool import KISRealtimeTool
                self._tools["realtime"] = KISRealtimeTool()
            except ImportError:
                pass
            
            # 웹 검색 (선택적)
            try:
                from src.tools.web_search_tool import WebSearchTool
                self._tools["web_search"] = WebSearchTool()
            except ImportError:
                pass
        
        return self._tools
    
    def analyze(self, query: str) -> QueryAnalysis:
        """
        쿼리 분석 수행
        
        Args:
            query: 사용자 쿼리
            
        Returns:
            QueryAnalysis 데이터클래스
        """
        # 1. 규칙 기반 빠른 분석 (LLM 호출 최소화)
        quick_analysis = self._quick_analyze(query)
        if quick_analysis.confidence > 0.8:
            return quick_analysis
        
        # 2. LLM 기반 상세 분석
        return self._llm_analyze(query, quick_analysis)
    
    def _quick_analyze(self, query: str) -> QueryAnalysis:
        """규칙 기반 빠른 분석 (LLM 없이)"""
        analysis = QueryAnalysis(original_query=query, intent=Intent.UNKNOWN)
        
        # 종목 추출 (분리된 StockMapper 사용)
        analysis.stocks = self.stock_mapper.search_in_text(query)
        
        # 의도 키워드 매칭
        query_lower = query.lower()
        
        # 실시간 시세
        if any(kw in query for kw in ["가격", "시세", "얼마", "현재가", "지금"]):
            if analysis.stocks:
                analysis.intent = Intent.REALTIME_PRICE
                analysis.required_tools = ["realtime"]
                analysis.execution_plan = ["realtime_price"]
                analysis.confidence = 0.9
                return analysis
        
        # 종목 분석
        if any(kw in query for kw in ["분석", "평가", "어때", "전망", "투자"]):
            if analysis.stocks:
                if any(kw in query for kw in ["빠르게", "간단히", "요약"]):
                    analysis.intent = Intent.QUICK_ANALYSIS
                    analysis.required_agents = ["quant", "chartist"]
                    analysis.execution_plan = ["quant", "chartist", "quick_decision"]
                    analysis.confidence = 0.85
                else:
                    analysis.intent = Intent.STOCK_ANALYSIS
                    analysis.required_agents = ["analyst", "quant", "chartist", "risk_manager"]
                    analysis.execution_plan = ["analyst", "quant", "chartist", "risk_manager"]
                    analysis.confidence = 0.9
                return analysis
        
        # 비교 분석
        if any(kw in query for kw in ["비교", "vs", "VS", "어디", "뭐가 나아"]):
            if len(analysis.stocks) >= 2:
                analysis.intent = Intent.COMPARISON
                analysis.required_agents = ["quant", "chartist"]
                analysis.execution_plan = ["compare_stocks"]
                analysis.confidence = 0.85
                return analysis
        
        # 산업 분석
        industry_keywords = {
            "반도체": "반도체",
            "2차전지": "2차전지",
            "배터리": "2차전지",
            "자동차": "자동차",
            "전기차": "전기차",
            "바이오": "바이오",
            "제약": "바이오",
            "금융": "금융",
            "은행": "금융",
            "플랫폼": "플랫폼",
            "인터넷": "플랫폼",
            "게임": "게임",
            "엔터": "엔터테인먼트",
            "방산": "방산",
            "조선": "조선",
            "철강": "철강",
            "화학": "화학",
            "AI": "AI",
            "인공지능": "AI",
        }
        
        for keyword, industry in industry_keywords.items():
            if keyword in query:
                if any(kw in query for kw in ["산업", "업종", "섹터", "동향", "전망"]):
                    analysis.intent = Intent.INDUSTRY_ANALYSIS
                    analysis.industry = industry
                    analysis.required_agents = ["analyst"]  # Researcher + Strategist
                    analysis.required_tools = ["web_search"]
                    analysis.execution_plan = ["research_industry", "analyze_industry"]
                    analysis.confidence = 0.85
                    return analysis
        
        # 테마/관련주
        if any(kw in query for kw in ["관련주", "테마", "수혜주", "추천"]):
            # 테마 추출
            for keyword, industry in industry_keywords.items():
                if keyword in query:
                    analysis.intent = Intent.THEME_SCREENING
                    analysis.theme = industry
                    analysis.required_tools = ["web_search"]
                    analysis.execution_plan = ["search_theme_stocks"]
                    analysis.confidence = 0.8
                    return analysis
        
        # 글로벌 이슈
        issue_keywords = ["미중", "금리", "환율", "유가", "전쟁", "트럼프", "관세", "인플레이션"]
        for keyword in issue_keywords:
            if keyword in query:
                analysis.intent = Intent.ISSUE_ANALYSIS
                analysis.issue = keyword
                analysis.required_agents = ["analyst"]
                analysis.required_tools = ["web_search"]
                analysis.execution_plan = ["research_issue", "analyze_impact"]
                analysis.confidence = 0.8
                return analysis
        
        # 확신 없음 → LLM에 위임
        analysis.confidence = 0.3
        return analysis
    
    def _llm_analyze(self, query: str, quick_result: QueryAnalysis) -> QueryAnalysis:
        """LLM 기반 상세 분석 (대화 맥락 포함)"""
        
        # 대화 히스토리 및 맥락 힌트 주입
        history_section = ""
        history_prompt = self.memory.to_prompt()
        context_hint = self.memory.get_context_hint(query)
        
        if history_prompt:
            history_section += f"\n{history_prompt}\n"
        if context_hint:
            history_section += f"\n{context_hint}\n"
        
        prompt = f"""
사용자 쿼리를 분석하세요.
{history_section}
쿼리: "{query}"

다음 JSON 형식으로 응답하세요:
{{
    "intent": "stock_analysis | quick_analysis | industry | issue | price | comparison | theme | general",
    "stocks": [
        {{"name": "종목명", "code": "종목코드"}}
    ],
    "industry": "산업명 또는 null",
    "issue": "이슈/키워드 또는 null",
    "theme": "테마 또는 null",
    "confidence": 0.0~1.0,
    "needs_clarification": true/false,
    "clarification_message": "추가 질문 (필요시)"
}}

의도 분류 기준:
- stock_analysis: 특정 종목 심층 분석 요청
- quick_analysis: 빠른/간단한 분석 요청
- industry: 산업/업종 동향 분석
- issue: 글로벌 이슈/정책 영향 분석
- price: 실시간 가격/시세 조회
- comparison: 2개 이상 종목 비교
- theme: 테마/관련주 탐색
- general: 일반 질문

종목코드 참고 (주요 종목):
- 삼성전자: 005930
- SK하이닉스: 000660
- 현대차: 005380
- 네이버: 035420
- 카카오: 035720

JSON만 응답하세요.
"""
        
        try:
            response = self.llm.invoke(prompt)
            content = response.content
            
            # JSON 추출
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
                
                # QueryAnalysis 생성
                intent_map = {
                    "stock_analysis": Intent.STOCK_ANALYSIS,
                    "quick_analysis": Intent.QUICK_ANALYSIS,
                    "industry": Intent.INDUSTRY_ANALYSIS,
                    "issue": Intent.ISSUE_ANALYSIS,
                    "price": Intent.REALTIME_PRICE,
                    "comparison": Intent.COMPARISON,
                    "theme": Intent.THEME_SCREENING,
                    "general": Intent.GENERAL_QA,
                }
                
                analysis = QueryAnalysis(
                    original_query=query,
                    intent=intent_map.get(data.get("intent", ""), Intent.UNKNOWN),
                    stocks=data.get("stocks", quick_result.stocks),
                    industry=data.get("industry"),
                    issue=data.get("issue"),
                    theme=data.get("theme"),
                    confidence=data.get("confidence", 0.7),
                    needs_clarification=data.get("needs_clarification", False),
                    clarification_message=data.get("clarification_message", ""),
                )
                
                # 실행 계획 설정
                self._set_execution_plan(analysis)
                return analysis
                
        except Exception as e:
            print(f"⚠️ LLM 분석 오류: {e}")
        
        # 실패 시 quick_result 반환
        quick_result.confidence = max(quick_result.confidence, 0.5)
        self._set_execution_plan(quick_result)
        return quick_result
    
    def _set_execution_plan(self, analysis: QueryAnalysis):
        """의도에 따른 실행 계획 설정"""
        
        if analysis.intent == Intent.STOCK_ANALYSIS:
            analysis.required_agents = ["analyst", "quant", "chartist", "risk_manager"]
            analysis.execution_plan = [
                "1. Analyst: 헤게모니 분석 (Researcher → Strategist)",
                "2. Quant: 재무 분석",
                "3. Chartist: 기술적 분석",
                "4. Risk Manager: 최종 판단",
            ]
            
        elif analysis.intent == Intent.QUICK_ANALYSIS:
            analysis.required_agents = ["quant", "chartist", "risk_manager"]
            analysis.execution_plan = [
                "1. Quant: 재무 분석",
                "2. Chartist: 기술적 분석",
                "3. Risk Manager: 빠른 판단",
            ]
            
        elif analysis.intent == Intent.INDUSTRY_ANALYSIS:
            analysis.required_agents = ["analyst"]
            analysis.required_tools = ["web_search"]
            analysis.execution_plan = [
                "1. Researcher: 산업 뉴스/정책 검색",
                "2. Strategist: 산업 구조 분석",
            ]
            
        elif analysis.intent == Intent.ISSUE_ANALYSIS:
            analysis.required_agents = ["analyst"]
            analysis.required_tools = ["web_search"]
            analysis.execution_plan = [
                "1. Researcher: 이슈 관련 정보 검색",
                "2. Strategist: 영향도 분석",
            ]
            
        elif analysis.intent == Intent.REALTIME_PRICE:
            analysis.required_tools = ["realtime"]
            analysis.execution_plan = [
                "1. Realtime Tool: 현재가 조회",
            ]
            
        elif analysis.intent == Intent.COMPARISON:
            analysis.required_agents = ["quant", "chartist"]
            analysis.execution_plan = [
                "1. Quant: 각 종목 재무 분석",
                "2. Chartist: 각 종목 기술적 분석",
                "3. 비교 리포트 생성",
            ]
            
        elif analysis.intent == Intent.THEME_SCREENING:
            analysis.required_tools = ["web_search"]
            analysis.execution_plan = [
                "1. Web Search: 테마 관련주 검색",
                "2. 종목 리스트 정리",
            ]
    
    def execute(self, query: str) -> Dict[str, Any]:
        """
        쿼리 분석 및 실행 (메모리 컨텍스트 포함)
        
        Args:
            query: 사용자 쿼리
            
        Returns:
            실행 결과 딕셔너리
        """
        print("=" * 60)
        print(f"🎯 [Supervisor] 쿼리 분석 중...")
        print(f"   쿼리: {query}")
        if self.memory.turn_count > 0:
            print(f"   💾 대화 히스토리: {self.memory.turn_count}턴")
        print("=" * 60)
        
        # 0. 맥락 힌트 확인 (후속 질문 감지)
        context_hint = self.memory.get_context_hint(query)
        if context_hint:
            print(f"   📎 맥락 감지: 이전 대화 참조")
        
        # 1. 쿼리 분석 (맥락 포함)
        analysis = self.analyze(query)
        
        print(f"\n📊 분석 결과:")
        print(f"   의도: {analysis.intent.value}")
        print(f"   종목: {analysis.stocks}")
        print(f"   산업: {analysis.industry}")
        print(f"   이슈: {analysis.issue}")
        print(f"   신뢰도: {analysis.confidence:.0%}")
        print(f"   실행 계획: {analysis.execution_plan}")
        
        # 2. 추가 질문 필요 시
        if analysis.needs_clarification:
            return {
                "status": "need_clarification",
                "message": analysis.clarification_message,
                "analysis": analysis,
            }
        
        # 3. 의도별 실행
        if analysis.intent == Intent.STOCK_ANALYSIS:
            result = self._execute_stock_analysis(analysis)
        elif analysis.intent == Intent.QUICK_ANALYSIS:
            result = self._execute_quick_analysis(analysis)
        elif analysis.intent == Intent.REALTIME_PRICE:
            result = self._execute_realtime_price(analysis)
        elif analysis.intent == Intent.INDUSTRY_ANALYSIS:
            result = self._execute_industry_analysis(analysis)
        elif analysis.intent == Intent.ISSUE_ANALYSIS:
            result = self._execute_issue_analysis(analysis)
        elif analysis.intent == Intent.COMPARISON:
            result = self._execute_comparison(analysis)
        elif analysis.intent == Intent.THEME_SCREENING:
            result = self._execute_theme_screening(analysis)
        else:
            result = self._execute_general_qa(analysis)
        
        # 4. 메모리에 기록
        self._save_to_memory(query, result, analysis)
        
        return result
    
    def _execute_stock_analysis(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """종목 분석 실행 (전체 파이프라인 — 병렬 처리)"""
        if not analysis.stocks:
            return {"status": "error", "message": "분석할 종목을 찾을 수 없습니다."}
        
        stock = analysis.stocks[0]
        stock_name = stock["name"]
        stock_code = stock["code"]
        
        print(f"\n🚀 {stock_name}({stock_code}) 전체 분석 시작...")
        print(f"   ⚡ Analyst / Quant / Chartist 병렬 실행")
        
        results = {"status": "success", "stock": stock, "scores": {}}
        
        # ── Phase 1: Analyst, Quant, Chartist 병렬 실행 ──
        parallel_results = run_agents_parallel({
            "analyst":  (self.agents["analyst"].full_analysis,  (stock_name, stock_code)),
            "quant":    (self.agents["quant"].full_analysis,    (stock_name, stock_code)),
            "chartist": (self.agents["chartist"].full_analysis, (stock_name, stock_code)),
        })
        
        # 결과 수거 (오류 처리 포함)
        analyst_score = parallel_results.get("analyst")
        quant_score = parallel_results.get("quant")
        chartist_score = parallel_results.get("chartist")
        
        if is_error(analyst_score):
            print(f"   ⚠️ Analyst 오류: {analyst_score}")
            from src.agents.analyst import AnalystScore
            analyst_score = AnalystScore(
                moat_score=20, growth_score=15, total_score=35,
                moat_reason="분석 오류", growth_reason="분석 오류",
                report_summary="", image_analysis="",
                final_opinion="오류로 인한 기본값"
            )
        
        if is_error(quant_score):
            print(f"   ⚠️ Quant 오류: {quant_score}")
            quant_score = self.agents["quant"]._default_score(stock_name, str(quant_score))
        
        if is_error(chartist_score):
            print(f"   ⚠️ Chartist 오류: {chartist_score}")
            chartist_score = self.agents["chartist"]._default_score(stock_code, str(chartist_score))
        
        results["scores"]["analyst"] = analyst_score
        results["scores"]["quant"] = quant_score
        results["scores"]["chartist"] = chartist_score
        
        print(f"   → Analyst  헤게모니: {analyst_score.hegemony_grade} ({analyst_score.total_score}/70점)")
        print(f"   → Quant    재무등급: {quant_score.grade} ({quant_score.total_score}/100점)")
        print(f"   → Chartist 기술신호: {chartist_score.signal} ({chartist_score.total_score}/100점)")
        
        # ── Phase 2: Risk Manager 최종 판단 (3개 결과 의존) ──
        print(f"\n🎯 Risk Manager 최종 판단...")
        from src.agents import AgentScores
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
        final_decision = self.agents["risk_manager"].make_decision(
            stock_name, stock_code, agent_scores
        )
        results["final_decision"] = final_decision
        
        # 결과 요약
        results["summary"] = self._generate_summary(stock_name, results)
        
        # 분석 결과 캐시
        self.memory.cache_analysis(stock_name, {
            "total_score": final_decision.total_score,
            "action": final_decision.action.value,
            "analyst_total": analyst_score.total_score,
            "quant_total": quant_score.total_score,
            "chartist_total": chartist_score.total_score,
        })
        
        return results
    
    def _execute_quick_analysis(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """빠른 분석 실행 (Analyst 제외)"""
        if not analysis.stocks:
            return {"status": "error", "message": "분석할 종목을 찾을 수 없습니다."}
        
        stock = analysis.stocks[0]
        stock_name = stock["name"]
        stock_code = stock["code"]
        
        print(f"\n⚡ {stock_name}({stock_code}) 빠른 분석 시작...")
        
        results = {"status": "success", "stock": stock, "scores": {}}
        
        # Quant
        quant_score = self.agents["quant"].full_analysis(stock_name, stock_code)
        results["scores"]["quant"] = quant_score
        
        # Chartist
        chartist_score = self.agents["chartist"].full_analysis(stock_name, stock_code)
        results["scores"]["chartist"] = chartist_score
        
        # Quick Decision
        quick_opinion = self.agents["risk_manager"].quick_decision(
            analyst_total=35,  # 기본값
            quant_total=quant_score.total_score,
            chartist_total=chartist_score.total_score,
        )
        results["quick_opinion"] = quick_opinion
        
        return results
    
    def _execute_realtime_price(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """실시간 시세 조회"""
        if not analysis.stocks:
            return {"status": "error", "message": "조회할 종목을 찾을 수 없습니다."}
        
        if "realtime" not in self.tools or not self.tools["realtime"].is_available():
            return {"status": "error", "message": "실시간 시세 API가 설정되지 않았습니다."}
        
        stock = analysis.stocks[0]
        quote = self.tools["realtime"].get_current_price(stock["code"])
        
        if quote:
            return {
                "status": "success",
                "stock": stock,
                "quote": quote,
                "summary": f"{quote.name} 현재가: {quote.current_price:,}원 ({quote.change_rate:+.2f}%)",
            }
        else:
            return {"status": "error", "message": "시세 조회 실패"}
    
    def _execute_industry_analysis(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """산업 분석 실행"""
        industry = analysis.industry or analysis.original_query
        
        print(f"\n🏭 {industry} 산업 분석 시작...")
        
        # Researcher의 산업 분석 기능 활용
        from src.agents.researcher import ResearcherAgent
        researcher = ResearcherAgent()
        
        # 산업 관련 정보 수집
        news = researcher._search_news(industry)
        policy = researcher._search_policy(industry)
        industry_info = researcher._search_industry(industry)
        
        # Strategist로 분석
        from src.agents.strategist import StrategistAgent
        strategist = StrategistAgent()
        
        analysis_prompt = f"""
{industry} 산업에 대해 분석하세요:

[뉴스]
{news}

[정책]
{policy}

[산업 동향]
{industry_info}

다음을 포함해서 분석해주세요:
1. 산업 현황 요약
2. 주요 성장 동력
3. 리스크 요인
4. 투자 시사점
5. 관련 종목 추천
"""
        
        response = strategist.llm.invoke(analysis_prompt)
        
        return {
            "status": "success",
            "industry": industry,
            "news": news,
            "policy": policy,
            "analysis": response.content,
        }
    
    def _execute_issue_analysis(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """글로벌 이슈 분석"""
        issue = analysis.issue or analysis.original_query
        
        print(f"\n🌍 '{issue}' 이슈 분석 시작...")
        
        from src.agents.researcher import ResearcherAgent
        from src.agents.strategist import StrategistAgent
        
        researcher = ResearcherAgent()
        strategist = StrategistAgent()
        
        # 이슈 관련 정보 수집
        news = researcher._search_news(issue)
        
        # 영향 분석
        analysis_prompt = f"""
'{issue}' 이슈가 주식시장에 미치는 영향을 분석하세요:

[관련 뉴스]
{news}

다음을 포함해서 분석해주세요:
1. 이슈 요약
2. 영향받는 산업/섹터
3. 수혜주 vs 피해주
4. 단기/중기/장기 전망
5. 투자 전략 제안
"""
        
        response = strategist.llm.invoke(analysis_prompt)
        
        return {
            "status": "success",
            "issue": issue,
            "news": news,
            "analysis": response.content,
        }
    
    def _execute_comparison(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """종목 비교 분석 (병렬 처리)"""
        if len(analysis.stocks) < 2:
            return {"status": "error", "message": "비교할 종목이 2개 이상 필요합니다."}
        
        print(f"\n🔄 종목 비교 분석 시작... (병렬 처리)")
        
        stocks_to_compare = analysis.stocks[:3]  # 최대 3개
        
        # 모든 종목의 quant/chartist를 한꺼번에 병렬 실행
        parallel_tasks = {}
        for stock in stocks_to_compare:
            name, code = stock["name"], stock["code"]
            parallel_tasks[f"quant_{name}"] = (self.agents["quant"].full_analysis, (name, code))
            parallel_tasks[f"chartist_{name}"] = (self.agents["chartist"].full_analysis, (name, code))
        
        parallel_results = run_agents_parallel(parallel_tasks)
        
        results = {"status": "success", "stocks": analysis.stocks, "comparisons": []}
        
        for stock in stocks_to_compare:
            name = stock["name"]
            quant_score = parallel_results.get(f"quant_{name}")
            chartist_score = parallel_results.get(f"chartist_{name}")
            
            # 오류 처리
            if is_error(quant_score):
                quant_score = self.agents["quant"]._default_score(name, str(quant_score))
            if is_error(chartist_score):
                chartist_score = self.agents["chartist"]._default_score(stock["code"], str(chartist_score))
            
            results["comparisons"].append({
                "stock": stock,
                "quant": quant_score,
                "chartist": chartist_score,
                "total": quant_score.total_score + chartist_score.total_score,
            })
        
        # 순위 정렬
        results["comparisons"].sort(key=lambda x: x["total"], reverse=True)
        results["recommendation"] = results["comparisons"][0]["stock"]["name"]
        
        return results
    
    def _execute_theme_screening(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """테마/관련주 탐색"""
        theme = analysis.theme or analysis.original_query
        
        print(f"\n🔍 '{theme}' 관련주 탐색 중...")
        
        from src.agents.researcher import ResearcherAgent
        researcher = ResearcherAgent()
        
        # 웹 검색으로 관련주 탐색
        search_result = researcher._search_news(f"{theme} 관련주 수혜주")
        
        return {
            "status": "success",
            "theme": theme,
            "search_result": search_result,
            "message": f"'{theme}' 관련주 정보를 검색했습니다. 위 결과를 참고하세요.",
        }
    
    def _execute_general_qa(self, analysis: QueryAnalysis) -> Dict[str, Any]:
        """일반 질문 처리 (대화 맥락 포함)"""
        # 대화 히스토리가 있으면 맥락으로 주입
        history_prompt = self.memory.to_prompt()
        context_hint = self.memory.get_context_hint(analysis.original_query)
        
        full_prompt = analysis.original_query
        if history_prompt or context_hint:
            full_prompt = f"""
{history_prompt}
{context_hint}

현재 질문: {analysis.original_query}

이전 대화 맥락을 고려하여 답변하세요.
"""
        
        response = self.llm.invoke(full_prompt)
        
        return {
            "status": "success",
            "type": "general",
            "answer": response.content,
        }
    
    def _save_to_memory(
        self, query: str, result: Dict[str, Any], analysis: QueryAnalysis
    ) -> None:
        """실행 결과를 대화 메모리에 저장"""
        # 응답 요약 생성
        summary = ""
        if result.get("summary"):
            summary = result["summary"][:300]
        elif result.get("answer"):
            summary = result["answer"][:300]
        elif result.get("analysis"):
            summary = result["analysis"][:300]
        elif result.get("message"):
            summary = result["message"][:300]
        elif result.get("status") == "error":
            summary = f"오류: {result.get('message', '')}"
        else:
            summary = str(result)[:300]
        
        # 관련 종목명 목록
        stock_names = [s.get("name", "") for s in analysis.stocks] if analysis.stocks else []
        
        self.memory.add_turn(
            query=query,
            response_summary=summary,
            intent=analysis.intent.value,
            stocks=stock_names,
        )
    
    def _generate_summary(self, stock_name: str, results: Dict) -> str:
        """결과 요약 생성"""
        decision = results.get("final_decision")
        if not decision:
            return "분석 결과를 요약할 수 없습니다."
        
        return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 {stock_name} 종합 분석 결과
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 최종 판단: {decision.action.value}
📈 종합 점수: {decision.total_score}/270점
⚠️ 리스크 레벨: {decision.risk_level.value}
💰 목표가: {decision.target_price or 'N/A'}
🛑 손절가: {decision.stop_loss or 'N/A'}

💬 의견: {decision.summary}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ==========================================
# 대화형 인터페이스
# ==========================================
def chat():
    """대화형 인터페이스 (메모리 포함)"""
    memory = ConversationMemory(max_turns=10)
    supervisor = SupervisorAgent(memory=memory)
    
    print("=" * 60)
    print("🤖 HQA 주식 분석 시스템")
    print("   💾 대화 맥락 기억 | ⚡ 병렬 실행")
    print("=" * 60)
    print("질문을 입력하세요. 종료하려면 'quit' 또는 'exit'를 입력하세요.")
    print()
    print("예시 질문:")
    print("  - 삼성전자 분석해줘")
    print("  - 그럼 하이닉스는 어때? (맥락 유지)")
    print("  - 반도체 산업 전망은?")
    print("  - 삼성전자 vs SK하이닉스 비교해줘")
    print("=" * 60)
    
    while True:
        try:
            query = input("\n👤 You: ").strip()
            
            if not query:
                continue
            
            if query.lower() in ["quit", "exit", "종료", "q"]:
                print("👋 안녕히 가세요!")
                break
            
            # 실행
            result = supervisor.execute(query)
            
            # 결과 출력
            if result.get("status") == "need_clarification":
                print(f"\n🤔 {result['message']}")
            elif result.get("status") == "error":
                print(f"\n❌ {result['message']}")
            elif result.get("summary"):
                print(result["summary"])
            elif result.get("answer"):
                print(f"\n🤖 Assistant: {result['answer']}")
            elif result.get("analysis"):
                print(f"\n📝 분석 결과:\n{result['analysis']}")
            else:
                print(f"\n✅ 완료: {result}")
                
        except KeyboardInterrupt:
            print("\n\n👋 종료합니다.")
            break
        except Exception as e:
            print(f"\n❌ 오류 발생: {e}")


if __name__ == "__main__":
    chat()
