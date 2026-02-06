# 파일: src/agents/researcher.py
"""
Researcher Agent (리서처 에이전트)

역할: 정보 수집 및 요약
- RAG 검색 (증권사 리포트) → rag_tool.py 도구 호출
- 웹 검색 (뉴스, 정책, 산업 동향)
- Vision 분석 (차트/그래프 읽기)
- 수집된 정보를 요약하여 Strategist에게 전달

모델: Instruct (빠름) + Vision (이미지)
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from crewai import Agent, Task, Crew, Process

from src.agents.llm_config import get_gemini_llm, GeminiVisionAnalyzer

# RAG 검색 도구 (리랭킹 포함)
from src.tools.rag_tool import RAGSearchTool, search_documents, get_retriever

# 웹 검색 도구 (선택적)
try:
    from src.tools.web_search_tool import WebSearchTool, NewsSearchTool
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False


@dataclass
class ResearchResult:
    """리서치 결과 데이터 클래스"""
    stock_name: str
    stock_code: str
    
    # 리포트 분석
    report_summary: str = ""
    report_sources: List[str] = field(default_factory=list)
    
    # 차트/이미지 분석
    chart_analysis: str = ""
    chart_count: int = 0
    
    # 뉴스/정책 정보
    news_summary: str = ""
    policy_summary: str = ""
    
    # 산업 동향
    industry_summary: str = ""
    
    # 메타데이터
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_strategist_prompt(self) -> str:
        """Strategist에게 전달할 요약 프롬프트 생성"""
        return f"""
# {self.stock_name} ({self.stock_code}) 리서치 요약

## 1. 증권사 리포트 요약
{self.report_summary or "리포트 정보 없음"}

## 2. 차트/그래프 분석
{self.chart_analysis or "차트 데이터 없음"}
- 분석된 차트 수: {self.chart_count}개

## 3. 최신 뉴스
{self.news_summary or "뉴스 정보 없음"}

## 4. 정책/규제 동향
{self.policy_summary or "정책 정보 없음"}

## 5. 산업 동향
{self.industry_summary or "산업 정보 없음"}

---
리서치 시점: {self.timestamp}
"""


class ResearcherAgent:
    """
    리서처 에이전트
    - 정보 수집 전문
    - Instruct 모델로 빠르게 처리
    - Vision으로 차트 읽기
    - RAG 도구 호출로 리포트 검색
    """
    
    def __init__(self):
        self.llm = get_gemini_llm()
        self.vision_analyzer = GeminiVisionAnalyzer()
        
        # RAG 도구 인스턴스
        self.rag_tool = RAGSearchTool(top_k=5)
    
    def research(self, stock_name: str, stock_code: str) -> ResearchResult:
        """
        종목에 대한 종합 리서치 수행
        
        Args:
            stock_name: 종목명 (예: 삼성전자)
            stock_code: 종목코드 (예: 005930)
            
        Returns:
            ResearchResult 데이터클래스
        """
        result = ResearchResult(stock_name=stock_name, stock_code=stock_code)
        
        # 1. 증권사 리포트 검색 및 요약
        print(f"📄 {stock_name} 리포트 검색 중...")
        result.report_summary, result.report_sources = self._search_reports(stock_name)
        
        # 2. 차트/그래프 분석 (Vision)
        print(f"📊 {stock_name} 차트 분석 중...")
        result.chart_analysis, result.chart_count = self._analyze_charts(stock_name)
        
        # 3. 뉴스 검색
        print(f"📰 {stock_name} 뉴스 검색 중...")
        result.news_summary = self._search_news(stock_name)
        
        # 4. 정책/규제 검색
        print(f"📋 {stock_name} 관련 정책 검색 중...")
        result.policy_summary = self._search_policy(stock_name)
        
        # 5. 산업 동향 검색
        print(f"🏭 {stock_name} 산업 동향 검색 중...")
        result.industry_summary = self._search_industry(stock_name)
        
        return result
    
    def _search_reports(self, stock_name: str) -> tuple[str, List[str]]:
        """증권사 리포트 검색 및 요약 (도구 호출)"""
        try:
            # RAG 도구 호출 (리랭킹 포함)
            query = f"{stock_name} 실적 전망 목표주가 투자의견"
            context = self.rag_tool._run(query)
            
            if "찾을 수 없습니다" in context:
                return "관련 리포트를 찾을 수 없습니다.", []
            
            # 소스 추출 (컨텍스트에서 파싱)
            sources = []
            for line in context.split("\n"):
                if "출처:" in line:
                    try:
                        source = line.split("출처:")[1].split(",")[0].strip()
                        if source and source not in sources:
                            sources.append(source)
                    except:
                        pass
            
            # LLM으로 요약
            summary_prompt = f"""
다음은 '{stock_name}'에 대한 증권사 리포트 내용입니다.
핵심 내용을 5줄 이내로 요약해주세요.

[리포트 내용]
{context[:3000]}

[요약 포인트]
- 투자의견 (매수/중립/매도)
- 목표주가
- 핵심 실적 전망
- 주요 리스크
"""
            response = self.llm.invoke(summary_prompt)
            return response.content, sources
            
        except Exception as e:
            return f"리포트 검색 오류: {str(e)}", []
    
    def _analyze_charts(self, stock_name: str) -> tuple[str, int]:
        """차트/그래프 Vision 분석"""
        # PaddleOCR-VL이 텍스트로 변환하므로 Vision 분석은 별도 처리
        # 추후 차트 이미지가 있을 경우 Vision 도구 호출
        return "차트 분석은 PaddleOCR-VL이 텍스트로 변환하여 리포트에 포함됨", 0
    
    def _search_news(self, stock_name: str) -> str:
        """최신 뉴스 검색"""
        if not WEB_SEARCH_AVAILABLE:
            return "웹 검색 도구 미설치"
        
        try:
            from src.tools.web_search_tool import search_stock_news
            results = search_stock_news(stock_name, max_results=5)
            
            if not results:
                return "관련 뉴스 없음"
            
            # 뉴스 요약
            news_text = "\n".join([
                f"- [{r.get('title', '')}] {r.get('snippet', '')}"
                for r in results[:5]
            ])
            
            summary_prompt = f"""
다음 뉴스들을 3줄로 요약해주세요:
{news_text}
"""
            response = self.llm.invoke(summary_prompt)
            return response.content
            
        except Exception as e:
            return f"뉴스 검색 오류: {str(e)}"
    
    def _search_policy(self, stock_name: str) -> str:
        """정책/규제 동향 검색"""
        if not WEB_SEARCH_AVAILABLE:
            return "웹 검색 도구 미설치"
        
        try:
            from src.tools.web_search_tool import search_web
            
            # 산업 키워드 추출 (간단한 매핑)
            industry_keywords = {
                "삼성전자": "반도체 정책 보조금",
                "SK하이닉스": "반도체 정책 HBM",
                "현대차": "전기차 보조금 정책",
                "LG에너지솔루션": "배터리 IRA 보조금",
                "네이버": "플랫폼 규제 AI",
                "카카오": "플랫폼 규제",
            }
            
            keyword = industry_keywords.get(stock_name, f"{stock_name} 정책 규제")
            results = search_web(keyword, max_results=3)
            
            if not results:
                return "관련 정책 정보 없음"
            
            policy_text = "\n".join([
                f"- {r.get('title', '')}: {r.get('snippet', '')}"
                for r in results[:3]
            ])
            
            return policy_text[:500]
            
        except Exception as e:
            return f"정책 검색 오류: {str(e)}"
    
    def _search_industry(self, stock_name: str) -> str:
        """산업 동향 검색"""
        # 1. RAG 도구로 산업 리포트 검색
        try:
            query = f"{stock_name} 산업 동향 시장 전망"
            context = self.rag_tool._run(query)
            
            if context and "찾을 수 없습니다" not in context:
                return context[:500]
        except:
            pass
        
        # 2. 웹 검색 폴백
        if not WEB_SEARCH_AVAILABLE:
            return "웹 검색 도구 미설치"
        
        try:
            from src.tools.web_search_tool import search_web
            
            # 산업 매핑
            industry_map = {
                "삼성전자": "반도체 메모리 파운드리 시장",
                "SK하이닉스": "HBM AI 반도체 시장",
                "현대차": "전기차 자율주행 시장",
                "LG에너지솔루션": "배터리 전기차 시장",
                "네이버": "검색 AI 클라우드 시장",
                "카카오": "메신저 플랫폼 시장",
                "셀트리온": "바이오시밀러 시장",
            }
            
            query = industry_map.get(stock_name, f"{stock_name} 산업 동향 시장")
            results = search_web(query + " 전망 2024", max_results=3)
            
            if not results:
                return "산업 동향 정보 없음"
            
            industry_text = "\n".join([
                f"- {r.get('title', '')}"
                for r in results[:3]
            ])
            
            return industry_text[:500]
            
        except Exception as e:
            return f"산업 검색 오류: {str(e)}"
    
    def quick_search(self, query: str) -> Dict:
        """빠른 검색 (특정 쿼리로) - 도구 호출"""
        context = self.rag_tool._run(query)
        
        return {
            "query": query,
            "context": context[:1000],
            "has_results": "찾을 수 없습니다" not in context
        }


# 사용 예시
if __name__ == "__main__":
    researcher = ResearcherAgent()
    
    print("=" * 60)
    print("삼성전자 리서치")
    print("=" * 60)
    
    result = researcher.research("삼성전자", "005930")
    
    print("\n" + "=" * 60)
    print("Strategist에게 전달할 요약:")
    print("=" * 60)
    print(result.to_strategist_prompt())
