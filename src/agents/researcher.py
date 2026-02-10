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
    
    # 정보 품질 평가
    data_sources: Dict = field(default_factory=dict)
    quality_score: int = 0
    quality_warnings: List[str] = field(default_factory=list)
    
    def evaluate_quality(self):
        """정보 품질을 자동 평가하고 경고 생성"""
        score = 0
        self.quality_warnings = []
        
        empty_indicators = ["없음", "오류", "실패", "확보하지 못함", "미설치"]
        
        def _has_content(text: str) -> bool:
            if not text or not text.strip():
                return False
            return not any(ind in text for ind in empty_indicators)
        
        # 리포트 (40점) — 가장 중요
        if _has_content(self.report_summary):
            score += 40
        else:
            self.quality_warnings.append("증권사 리포트 부재 — 정성적 분석의 신뢰도가 낮을 수 있음")
        
        # 뉴스 (25점)
        if _has_content(self.news_summary):
            score += 25
        else:
            self.quality_warnings.append("최신 뉴스 부재 — 시장 센티먼트 파악 제한적")
        
        # 산업동향 (20점)
        if _has_content(self.industry_summary):
            score += 20
        else:
            self.quality_warnings.append("산업 동향 부재 — 섹터 분석 제한적")
        
        # 정책 (15점)
        if _has_content(self.policy_summary):
            score += 15
        else:
            self.quality_warnings.append("정책/규제 정보 부재")
        
        self.quality_score = score
    
    @property
    def quality_grade(self) -> str:
        """정보 품질 등급"""
        if self.quality_score >= 80:
            return "A (충분)"
        elif self.quality_score >= 60:
            return "B (양호)"
        elif self.quality_score >= 40:
            return "C (부족)"
        else:
            return "D (매우 부족)"
    
    def to_strategist_prompt(self) -> str:
        """Strategist에게 전달할 요약 프롬프트 생성 (품질 평가 포함)"""
        # 정보 품질 섹션
        quality_section = f"""## 0. 정보 품질 평가
- 품질 등급: {self.quality_grade} ({self.quality_score}/100)
- 데이터 소스: {', '.join(f'{k}={v}' for k, v in self.data_sources.items()) if self.data_sources else 'N/A'}"""
        
        if self.quality_warnings:
            quality_section += "\n- ⚠️ 경고:"
            for w in self.quality_warnings:
                quality_section += f"\n  - {w}"
            quality_section += "\n\n※ 위 경고 사항을 감안하여 분석 신뢰도를 조정해주세요."
        
        return f"""
# {self.stock_name} ({self.stock_code}) 리서치 요약

{quality_section}

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
        - 각 단계에서 결과를 검증하고, 부족하면 폴백 전략 실행
        - 최종 정보 품질을 평가하여 Strategist에게 신뢰도 전달
        
        Args:
            stock_name: 종목명 (예: 삼성전자)
            stock_code: 종목코드 (예: 005930)
            
        Returns:
            ResearchResult 데이터클래스 (품질 평가 포함)
        """
        result = ResearchResult(stock_name=stock_name, stock_code=stock_code)
        
        # 1. 증권사 리포트 검색 (RAG → 웹 폴백)
        print(f"📄 {stock_name} 리포트 검색 중...")
        result.report_summary, result.report_sources = self._search_reports(stock_name)
        
        # 2. 차트/그래프 분석 (Vision)
        print(f"📊 {stock_name} 차트 분석 중...")
        result.chart_analysis, result.chart_count = self._analyze_charts(stock_name)
        
        # 3. 뉴스 검색 (웹 → RAG 폴백)
        print(f"📰 {stock_name} 뉴스 검색 중...")
        result.news_summary = self._search_news(stock_name)
        
        # 4. 정책/규제 검색 (웹 → RAG 폴백)
        print(f"📋 {stock_name} 관련 정책 검색 중...")
        result.policy_summary = self._search_policy(stock_name)
        
        # 5. 산업 동향 검색 (RAG → 웹 폴백)
        print(f"🏭 {stock_name} 산업 동향 검색 중...")
        result.industry_summary = self._search_industry(stock_name)
        
        # 6. 데이터 소스 기록 및 정보 품질 평가
        result.data_sources = self._collect_data_sources()
        result.evaluate_quality()
        
        quality_icon = "✅" if result.quality_score >= 60 else "⚠️"
        print(f"\n{quality_icon} 정보 품질: {result.quality_grade} ({result.quality_score}/100)")
        if result.quality_warnings:
            for w in result.quality_warnings:
                print(f"   ⚠️ {w}")
        
        return result
    
    # ─── 결과 검증 헬퍼 ───
    
    def _is_empty_result(self, text: str) -> bool:
        """검색 결과가 비어있거나 유효하지 않은지 판단"""
        if not text or not text.strip():
            return True
        empty_markers = [
            "찾을 수 없습니다", "관련 문서를 찾을 수 없습니다",
            "검색 결과가 없습니다", "관련 뉴스 없음",
            "관련 정책 정보 없음", "관련 리포트를 찾을 수 없습니다",
            "오류", "실패",
        ]
        return any(marker in text for marker in empty_markers)
    
    def _collect_data_sources(self) -> Dict:
        """각 카테고리별 데이터 소스 수집"""
        return {
            "reports": getattr(self, "_last_report_source", "unknown"),
            "news": getattr(self, "_last_news_source", "unknown"),
            "policy": getattr(self, "_last_policy_source", "unknown"),
            "industry": getattr(self, "_last_industry_source", "unknown"),
        }
    
    # ─── 검색 메서드 (폴백 전략 포함) ───
    
    def _search_reports(self, stock_name: str) -> tuple[str, List[str]]:
        """
        증권사 리포트 검색 및 요약
        Plan A: RAG 검색 → Plan B: 웹 검색 폴백
        """
        self._last_report_source = "none"
        
        # ── Plan A: RAG 검색 ──
        try:
            query = f"{stock_name} 실적 전망 목표주가 투자의견"
            context = self.rag_tool._run(query)
            
            if not self._is_empty_result(context):
                self._last_report_source = "rag"
                sources = self._extract_sources(context)
                summary = self._summarize_report(stock_name, context)
                return summary, sources
            else:
                print(f"   ℹ️ RAG 리포트 결과 없음 → 웹 검색 폴백")
        except Exception as e:
            print(f"   ⚠️ RAG 리포트 오류: {e} → 웹 검색 폴백")
        
        # ── Plan B: 웹 검색 폴백 ──
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                web_query = f"{stock_name} 증권사 리포트 목표주가 투자의견"
                results = search_web(web_query, max_results=5)
                
                if results:
                    self._last_report_source = "web"
                    web_context = "\n".join([
                        f"- [{r.get('title', '')}] {r.get('snippet', '')}"
                        for r in results[:5]
                    ])
                    summary = self._summarize_report(stock_name, web_context)
                    summary += "\n\n[데이터 출처: 웹 검색 자료 — 증권사 원문 리포트 대비 정확도 제한적]"
                    web_sources = [r.get("url", "") for r in results if r.get("url")]
                    return summary, web_sources
            except Exception as e:
                print(f"   ⚠️ 웹 검색 폴백도 실패: {e}")
        
        return "증권사 리포트를 확보하지 못했습니다. (RAG/웹 모두 실패)", []
    
    def _extract_sources(self, context: str) -> List[str]:
        """컨텍스트에서 출처 정보 추출"""
        sources = []
        for line in context.split("\n"):
            if "출처:" in line:
                try:
                    source = line.split("출처:")[1].split(",")[0].strip()
                    if source and source not in sources:
                        sources.append(source)
                except Exception:
                    pass
        return sources
    
    def _summarize_report(self, stock_name: str, context: str) -> str:
        """리포트 내용을 LLM으로 요약"""
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
        return response.content
    
    def _analyze_charts(self, stock_name: str) -> tuple[str, int]:
        """차트/그래프 Vision 분석"""
        # PaddleOCR-VL이 텍스트로 변환하므로 Vision 분석은 별도 처리
        # 추후 차트 이미지가 있을 경우 Vision 도구 호출
        return "차트 분석은 PaddleOCR-VL이 텍스트로 변환하여 리포트에 포함됨", 0
    
    def _search_news(self, stock_name: str) -> str:
        """
        최신 뉴스 검색
        Plan A: 웹 뉴스 검색 → Plan B: RAG 검색 폴백
        """
        self._last_news_source = "none"
        
        # ── Plan A: 웹 뉴스 검색 ──
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_stock_news
                results = search_stock_news(stock_name, max_results=5)
                
                if results:
                    self._last_news_source = "web"
                    news_text = "\n".join([
                        f"- [{r.get('title', '')}] {r.get('snippet', '')}"
                        for r in results[:5]
                    ])
                    summary_prompt = f"다음 뉴스들을 3줄로 요약해주세요:\n{news_text}"
                    response = self.llm.invoke(summary_prompt)
                    return response.content
                else:
                    print(f"   ℹ️ 웹 뉴스 결과 없음 → RAG 폴백")
            except Exception as e:
                print(f"   ⚠️ 웹 뉴스 오류: {e} → RAG 폴백")
        
        # ── Plan B: RAG 검색 폴백 ──
        try:
            rag_query = f"{stock_name} 뉴스 시장 이슈 최근 동향"
            context = self.rag_tool._run(rag_query)
            
            if not self._is_empty_result(context):
                self._last_news_source = "rag"
                return context[:500] + "\n\n[데이터 출처: RAG 저장 문서 — 실시간 뉴스 아님]"
        except Exception as e:
            print(f"   ⚠️ RAG 뉴스 폴백도 실패: {e}")
        
        return "뉴스를 확보하지 못했습니다. (웹/RAG 모두 실패)"
    
    def _search_policy(self, stock_name: str) -> str:
        """
        정책/규제 동향 검색
        Plan A: 웹 검색 → Plan B: RAG 검색 폴백
        """
        self._last_policy_source = "none"
        
        # 산업 키워드 매핑
        industry_keywords = {
            "삼성전자": "반도체 정책 보조금",
            "SK하이닉스": "반도체 정책 HBM",
            "현대차": "전기차 보조금 정책",
            "LG에너지솔루션": "배터리 IRA 보조금",
            "네이버": "플랫폼 규제 AI",
            "카카오": "플랫폼 규제",
        }
        keyword = industry_keywords.get(stock_name, f"{stock_name} 정책 규제")
        
        # ── Plan A: 웹 검색 ──
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                results = search_web(keyword, max_results=3)
                
                if results:
                    self._last_policy_source = "web"
                    policy_text = "\n".join([
                        f"- {r.get('title', '')}: {r.get('snippet', '')}"
                        for r in results[:3]
                    ])
                    return policy_text[:500]
                else:
                    print(f"   ℹ️ 웹 정책 결과 없음 → RAG 폴백")
            except Exception as e:
                print(f"   ⚠️ 웹 정책 오류: {e} → RAG 폴백")
        
        # ── Plan B: RAG 검색 폴백 ──
        try:
            rag_query = f"{stock_name} 정책 규제 정부 법안 {keyword}"
            context = self.rag_tool._run(rag_query)
            
            if not self._is_empty_result(context):
                self._last_policy_source = "rag"
                return context[:500] + "\n\n[데이터 출처: RAG 저장 문서]"
        except Exception as e:
            print(f"   ⚠️ RAG 정책 폴백도 실패: {e}")
        
        return "정책/규제 정보를 확보하지 못했습니다. (웹/RAG 모두 실패)"
    
    def _search_industry(self, stock_name: str) -> str:
        """
        산업 동향 검색
        Plan A: RAG 검색 → Plan B: 웹 검색 폴백
        """
        self._last_industry_source = "none"
        
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
        industry_query = industry_map.get(stock_name, f"{stock_name} 산업 동향 시장")
        
        # ── Plan A: RAG 검색 ──
        try:
            query = f"{stock_name} 산업 동향 시장 전망"
            context = self.rag_tool._run(query)
            
            if not self._is_empty_result(context):
                self._last_industry_source = "rag"
                return context[:500]
            else:
                print(f"   ℹ️ RAG 산업 결과 없음 → 웹 검색 폴백")
        except Exception as e:
            print(f"   ⚠️ RAG 산업 오류: {e} → 웹 검색 폴백")
        
        # ── Plan B: 웹 검색 폴백 ──
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                results = search_web(industry_query + " 전망 2024", max_results=3)
                
                if results:
                    self._last_industry_source = "web"
                    industry_text = "\n".join([
                        f"- {r.get('title', '')}: {r.get('snippet', '')}"
                        for r in results[:3]
                    ])
                    return industry_text[:500] + "\n\n[데이터 출처: 웹 검색]"
            except Exception as e:
                print(f"   ⚠️ 웹 산업 폴백도 실패: {e}")
        
        return "산업 동향 정보를 확보하지 못했습니다. (RAG/웹 모두 실패)"
    
    def quick_search(self, query: str) -> Dict:
        """
        빠른 검색 (특정 쿼리로)
        Plan A: RAG → Plan B: 웹 검색 폴백
        """
        # Plan A: RAG
        context = self.rag_tool._run(query)
        
        if not self._is_empty_result(context):
            return {
                "query": query,
                "context": context[:1000],
                "has_results": True,
                "source": "rag",
            }
        
        # Plan B: 웹 검색 폴백
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                results = search_web(query, max_results=3)
                if results:
                    web_context = "\n".join([
                        f"- {r.get('title', '')}: {r.get('snippet', '')}"
                        for r in results[:3]
                    ])
                    return {
                        "query": query,
                        "context": web_context[:1000],
                        "has_results": True,
                        "source": "web",
                    }
            except Exception:
                pass
        
        return {
            "query": query,
            "context": "",
            "has_results": False,
            "source": "none",
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
