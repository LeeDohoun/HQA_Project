# 파일: src/agents/analyst.py
"""
Analyst Agent (애널리스트 에이전트) - 통합 래퍼

구조:
- Researcher (Instruct): 정보 수집/요약 (빠름)
- Strategist (Thinking): 헤게모니 판단 (깊은 추론)

역할:
- 증권사 리포트 RAG 검색
- Vision으로 차트/그래프 분석
- 산업 구조, 정책, 경쟁 분석
- 독점력(Moat) + 성장성(Growth) 평가
"""

from typing import Dict, Optional, List
from dataclasses import dataclass

# 하위 에이전트 임포트
from src.agents.researcher import ResearcherAgent, ResearchResult
from src.agents.strategist import StrategistAgent, HegemonyScore


# 하위 호환성을 위해 AnalystScore 유지
@dataclass
class AnalystScore:
    """애널리스트 분석 점수 (하위 호환성)"""
    moat_score: int  # 독점력 (0-40점)
    growth_score: int  # 성장성 (0-30점)
    total_score: int  # 총점 (0-70점)
    moat_reason: str
    growth_reason: str
    report_summary: str
    image_analysis: str  # Vision 분석 결과
    final_opinion: str
    
    # 추가 필드 (Strategist에서)
    hegemony_grade: str = "C"
    competitive_advantage: str = ""
    risk_factors: str = ""
    policy_impact: str = ""
    detailed_reasoning: str = ""


class AnalystAgent:
    """
    애널리스트 에이전트 (통합 래퍼)
    
    내부적으로 Researcher + Strategist를 조합:
    1. Researcher (Instruct): 정보 수집, 요약 → 빠름
    2. Strategist (Thinking): 헤게모니 판단 → 깊은 추론
    """
    
    def __init__(self):
        self.researcher = ResearcherAgent()
        self.strategist = StrategistAgent()
    
    def analyze_stock(self, stock_name: str, stock_code: str) -> str:
        """
        종목 분석 수행 (보고서 형식 반환)
        
        Args:
            stock_name: 종목명 (예: 삼성전자)
            stock_code: 종목코드 (예: 005930)
            
        Returns:
            분석 보고서 문자열
        """
        # 통합 분석 수행
        score = self.full_analysis(stock_name, stock_code)
        
        # 보고서 생성
        return self.strategist.generate_report(
            HegemonyScore(
                moat_score=score.moat_score,
                growth_score=score.growth_score,
                total_score=score.total_score,
                moat_analysis=score.moat_reason,
                growth_analysis=score.growth_reason,
                competitive_advantage=score.competitive_advantage,
                risk_factors=score.risk_factors,
                policy_impact=score.policy_impact,
                hegemony_grade=score.hegemony_grade,
                final_opinion=score.final_opinion,
                detailed_reasoning=score.detailed_reasoning
            ),
            stock_name
        )
    
    def full_analysis(self, stock_name: str, stock_code: str) -> AnalystScore:
        """
        전체 분석 수행 (Researcher → Strategist)
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            AnalystScore 데이터클래스
        """
        # 1. Researcher: 정보 수집 (Instruct - 빠름)
        print(f"🔍 [Researcher] {stock_name} 정보 수집 중...")
        research_result = self.researcher.research(stock_name, stock_code)
        
        # 2. Strategist: 헤게모니 판단 (Thinking - 깊은 추론)
        print(f"🧠 [Strategist] {stock_name} 헤게모니 분석 중...")
        hegemony = self.strategist.analyze_hegemony(research_result)
        
        # 3. AnalystScore로 변환 (하위 호환성)
        return AnalystScore(
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
            detailed_reasoning=hegemony.detailed_reasoning
        )
    
    def quick_research(self, stock_name: str, stock_code: str) -> ResearchResult:
        """
        빠른 리서치 (정보 수집만, 판단 없음)
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            ResearchResult 데이터클래스
        """
        return self.researcher.research(stock_name, stock_code)
    
    def quick_search(self, query: str) -> Dict:
        """
        빠른 검색 (특정 쿼리로)
        
        Args:
            query: 검색 쿼리
            
        Returns:
            검색 결과 딕셔너리
        """
        return self.researcher.quick_search(query)


# 사용 예시
if __name__ == "__main__":
    agent = AnalystAgent()
    
    print("=" * 60)
    print("삼성전자 헤게모니 분석 (Researcher + Strategist)")
    print("=" * 60)
    
    # 전체 분석
    score = agent.full_analysis("삼성전자", "005930")
    
    print(f"\n📊 분석 결과:")
    print(f"   헤게모니 등급: {score.hegemony_grade}")
    print(f"   독점력: {score.moat_score}/40점")
    print(f"   성장성: {score.growth_score}/30점")
    print(f"   총점: {score.total_score}/70점")
    print(f"\n💡 총평: {score.final_opinion}")
    print(f"\n🛡️ 경쟁 우위: {score.competitive_advantage}")
    print(f"⚠️ 리스크: {score.risk_factors}")