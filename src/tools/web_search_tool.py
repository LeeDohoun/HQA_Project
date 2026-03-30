# 파일: src/tools/web_search_tool.py
"""
실시간 웹 검색 도구
- Tavily Search API (추천, 월 1,000건 무료)
- DuckDuckGo Search (완전 무료, 대안)

최신 뉴스, 실시간 정보 검색에 사용
"""

import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

# CrewAI Tool 베이스
try:
    from crewai.tools import BaseTool
    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False
    BaseTool = object

# LangChain Tool 데코레이터
try:
    from langchain.tools import tool
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False


@dataclass
class SearchResult:
    """검색 결과 데이터 클래스"""
    title: str
    url: str
    content: str
    score: float = 0.0
    published_date: Optional[str] = None

    @property
    def snippet(self) -> str:
        """기존 dict 기반 호출부와의 호환용 별칭"""
        return self.content

    def to_dict(self) -> Dict[str, Any]:
        """기존 dict 기반 호출부와의 호환용 직렬화"""
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "snippet": self.snippet,
            "score": self.score,
            "published_date": self.published_date,
        }

    def get(self, key: str, default: Any = None) -> Any:
        """dict-like 접근 호환"""
        return self.to_dict().get(key, default)


class WebSearchEngine:
    """
    웹 검색 엔진 통합 클래스
    
    우선순위:
    1. Tavily API (TAVILY_API_KEY 필요)
    2. DuckDuckGo (무료, 대안)
    """
    
    def __init__(self):
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")
        self._tavily_client = None
        self._ddg_client = None
        
        # 사용 가능한 엔진 확인
        self.engine = self._detect_engine()
        print(f"🔍 웹 검색 엔진: {self.engine}")
    
    def _detect_engine(self) -> str:
        """사용 가능한 검색 엔진 감지"""
        # 1. Tavily 확인
        if self.tavily_api_key:
            try:
                from tavily import TavilyClient
                self._tavily_client = TavilyClient(api_key=self.tavily_api_key)
                return "tavily"
            except ImportError:
                print("⚠️ tavily 패키지 없음 (pip install tavily-python)")
        
        # 2. DuckDuckGo 확인
        try:
            from duckduckgo_search import DDGS
            self._ddg_client = DDGS()
            return "duckduckgo"
        except ImportError:
            print("⚠️ duckduckgo_search 패키지 없음 (pip install duckduckgo-search)")
        
        return "none"
    
    def search(
        self,
        query: str,
        max_results: int = 5,
        search_type: str = "general"  # "general", "news"
    ) -> List[SearchResult]:
        """
        웹 검색 실행
        
        Args:
            query: 검색 쿼리
            max_results: 최대 결과 수
            search_type: "general" 또는 "news"
            
        Returns:
            SearchResult 리스트
        """
        if self.engine == "tavily":
            return self._search_tavily(query, max_results, search_type)
        elif self.engine == "duckduckgo":
            return self._search_duckduckgo(query, max_results, search_type)
        else:
            return []
    
    def _search_tavily(
        self,
        query: str,
        max_results: int,
        search_type: str
    ) -> List[SearchResult]:
        """Tavily API 검색"""
        try:
            # Tavily 검색 옵션
            search_depth = "basic"  # "basic" 또는 "advanced"
            
            if search_type == "news":
                # 뉴스 검색 시 최근 결과 우선
                response = self._tavily_client.search(
                    query=query,
                    max_results=max_results,
                    search_depth=search_depth,
                    include_answer=False,
                    include_raw_content=False
                )
            else:
                response = self._tavily_client.search(
                    query=query,
                    max_results=max_results,
                    search_depth=search_depth,
                    include_answer=False
                )
            
            results = []
            for item in response.get("results", []):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=item.get("content", ""),
                    score=item.get("score", 0.0),
                    published_date=item.get("published_date")
                ))
            
            return results
            
        except Exception as e:
            print(f"⚠️ Tavily 검색 오류: {e}")
            return []
    
    def _search_duckduckgo(
        self,
        query: str,
        max_results: int,
        search_type: str
    ) -> List[SearchResult]:
        """DuckDuckGo 검색"""
        try:
            from duckduckgo_search import DDGS
            
            results = []
            
            with DDGS() as ddgs:
                if search_type == "news":
                    # 뉴스 검색
                    search_results = ddgs.news(
                        query,
                        max_results=max_results,
                        region="kr-kr"  # 한국어 결과 우선
                    )
                else:
                    # 일반 검색
                    search_results = ddgs.text(
                        query,
                        max_results=max_results,
                        region="kr-kr"
                    )
                
                for item in search_results:
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        url=item.get("href", item.get("url", "")),
                        content=item.get("body", item.get("description", "")),
                        published_date=item.get("date")
                    ))
            
            return results
            
        except Exception as e:
            print(f"⚠️ DuckDuckGo 검색 오류: {e}")
            return []
    
    def search_news(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """뉴스 전용 검색"""
        return self.search(query, max_results, search_type="news")
    
    def format_results(self, results: List[SearchResult]) -> str:
        """검색 결과를 LLM 친화적 텍스트로 변환"""
        if not results:
            return "검색 결과가 없습니다."
        
        output = []
        for i, r in enumerate(results, 1):
            date_str = f" ({r.published_date})" if r.published_date else ""
            output.append(f"[{i}] {r.title}{date_str}")
            output.append(f"    URL: {r.url}")
            output.append(f"    내용: {r.content[:300]}...")
            output.append("")
        
        return "\n".join(output)


# ============================================================
# CrewAI Tool 구현
# ============================================================

if HAS_CREWAI:
    class WebSearchTool(BaseTool):
        """실시간 웹 검색 도구 (CrewAI)"""
        name: str = "Web Search"
        description: str = (
            "Search the internet for real-time information. "
            "Useful for current events, recent news, and up-to-date information "
            "that may not be in the knowledge base. "
            "Input should be a search query string."
        )
        
        _engine: WebSearchEngine = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._engine = WebSearchEngine()
        
        def _run(self, query: str) -> str:
            """웹 검색 실행"""
            results = self._engine.search(query, max_results=5)
            return self._engine.format_results(results)
    
    
    class NewsSearchTool(BaseTool):
        """실시간 뉴스 검색 도구 (CrewAI)"""
        name: str = "News Search"
        description: str = (
            "Search for the latest news articles about a topic. "
            "Useful for finding recent news about companies, market events, "
            "or any current affairs. Returns the most recent news first. "
            "Input should be a search query string (e.g., '삼성전자 뉴스')."
        )
        
        _engine: WebSearchEngine = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._engine = WebSearchEngine()
        
        def _run(self, query: str) -> str:
            """뉴스 검색 실행"""
            results = self._engine.search_news(query, max_results=5)
            return self._engine.format_results(results)
    
    
    class StockNewsSearchTool(BaseTool):
        """주식 관련 뉴스 검색 도구 (CrewAI)"""
        name: str = "Stock News Search"
        description: str = (
            "Search for the latest news about a specific stock or company. "
            "Input should be the company name or stock code "
            "(e.g., '삼성전자', 'SK하이닉스', '005930')."
        )
        
        _engine: WebSearchEngine = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._engine = WebSearchEngine()
        
        def _run(self, company_or_code: str) -> str:
            """주식 뉴스 검색"""
            # 검색어 최적화
            query = f"{company_or_code} 주식 뉴스 최신"
            results = self._engine.search_news(query, max_results=5)
            
            if not results:
                # 다른 쿼리로 재시도
                query = f"{company_or_code} 주가 전망"
                results = self._engine.search_news(query, max_results=5)
            
            return self._engine.format_results(results)


# ============================================================
# LangChain Tool 구현 (함수형)
# ============================================================

# 전역 검색 엔진 인스턴스
_search_engine: Optional[WebSearchEngine] = None

def _get_engine() -> WebSearchEngine:
    """검색 엔진 싱글톤"""
    global _search_engine
    if _search_engine is None:
        _search_engine = WebSearchEngine()
    return _search_engine


if HAS_LANGCHAIN:
    @tool
    def web_search(query: str) -> str:
        """
        인터넷에서 실시간 정보를 검색합니다.
        최신 뉴스, 현재 이벤트, 업데이트된 정보를 찾을 때 사용합니다.
        
        Args:
            query: 검색할 내용 (예: "삼성전자 실적 발표")
        """
        engine = _get_engine()
        results = engine.search(query, max_results=5)
        return engine.format_results(results)
    
    @tool
    def news_search(query: str) -> str:
        """
        최신 뉴스 기사를 검색합니다.
        회사, 시장 이벤트, 시사 문제에 대한 최근 뉴스를 찾을 때 사용합니다.
        
        Args:
            query: 검색할 뉴스 주제 (예: "반도체 시장 전망")
        """
        engine = _get_engine()
        results = engine.search_news(query, max_results=5)
        return engine.format_results(results)
    
    @tool
    def stock_news_search(company_name: str) -> str:
        """
        특정 회사/종목의 최신 주식 뉴스를 검색합니다.
        
        Args:
            company_name: 회사명 또는 종목코드 (예: "삼성전자", "005930")
        """
        engine = _get_engine()
        query = f"{company_name} 주식 뉴스 최신"
        results = engine.search_news(query, max_results=5)
        
        if not results:
            query = f"{company_name} 주가 전망"
            results = engine.search_news(query, max_results=5)
        
        return engine.format_results(results)


# ============================================================
# 직접 사용 가능한 함수
# ============================================================

def search_web(query: str, max_results: int = 5) -> List[SearchResult]:
    """일반 웹 검색"""
    return _get_engine().search(query, max_results)


def search_news(query: str, max_results: int = 5) -> List[SearchResult]:
    """뉴스 검색"""
    return _get_engine().search_news(query, max_results)


def search_stock_news(company: str, max_results: int = 5) -> List[SearchResult]:
    """주식 뉴스 검색"""
    query = f"{company} 주식 뉴스 최신"
    return _get_engine().search_news(query, max_results)


# ============================================================
# 테스트
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🔍 웹 검색 도구 테스트")
    print("=" * 60)
    
    engine = WebSearchEngine()
    
    # 일반 검색 테스트
    print("\n[1] 일반 웹 검색: 삼성전자 HBM")
    results = engine.search("삼성전자 HBM", max_results=3)
    print(engine.format_results(results))
    
    # 뉴스 검색 테스트
    print("\n[2] 뉴스 검색: SK하이닉스")
    results = engine.search_news("SK하이닉스", max_results=3)
    print(engine.format_results(results))
