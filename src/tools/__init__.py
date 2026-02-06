# 파일: src/tools/__init__.py
"""
HQA 분석 도구 모음

Tools:
- rag_tool: RAG 검색 도구 (리랭킹 포함) ⭐ 주요
- search_tool: 벡터 DB 리포트 검색 (레거시)
- finance_tool: 정량적 분석 도구 (네이버 금융 크롤링)
- realtime_tool: 실시간 시세 도구 (한국투자증권 API)
- web_search_tool: 실시간 웹/뉴스 검색
- charts_tools: 기술적 분석 도구 (RSI, MACD, 볼린저밴드 등)
"""

# RAG 검색 도구 (리랭킹 포함)
from .rag_tool import (
    RAGSearchTool,
    search_documents,
    search_reports,  # 하위 호환성
    get_retriever,
)

# 벡터 DB 검색 (리포트, 공시) - 레거시 호환성
from .search_tool import (
    StockReportSearchTool,
    MultimodalReportSearchTool,
    ReportImageSearchTool,
    stock_report_search_tool,
    multimodal_search_tool,
    image_search_tool,
)

# 정량적 분석 (네이버 금융)
from .finance_tool import (
    # 클래스
    NaverFinanceCrawler,
    QuantitativeAnalyzer,
    QuantitativeAnalysis,
    FinancialAnalysisTool,
    ValuationTool,
    ProfitabilityTool,
    FinancialHealthTool,
    # 함수
    analyze_financials,
    get_valuation,
    get_profitability,
)

# 웹 검색 (실시간 뉴스, 최신 정보)
from .web_search_tool import (
    # 클래스 (CrewAI)
    WebSearchEngine,
    WebSearchTool,
    NewsSearchTool,
    StockNewsSearchTool,
    SearchResult,
    # 함수 (직접 호출)
    search_web,
    search_news,
    search_stock_news,
)

# 기술적 분석 도구
from .charts_tools import (
    # 클래스
    TechnicalAnalyzer,
    TechnicalIndicators,
    TechnicalAnalysisTool,
    RSIAnalysisTool,
    MACDAnalysisTool,
    BollingerBandTool,
    TrendAnalysisTool,
    # 함수
    analyze_stock,
    get_rsi,
    get_macd,
    is_bullish,
)

# 실시간 시세 (한국투자증권 API)
try:
    from .realtime_tool import (
        KISRealtimeTool,
        RealtimeQuote,
        OrderBook,
        create_realtime_tools,
    )
except ImportError:
    pass  # mojito2 미설치 시 무시

# LangChain Tools (함수형)
try:
    from .web_search_tool import (
        web_search,
        news_search,
        stock_news_search,
    )
except ImportError:
    pass  # LangChain 미설치 시 무시

__all__ = [
    # RAG 검색 (리랭킹 포함)
    "RAGSearchTool",
    "search_documents",
    "search_reports",
    "get_retriever",
    
    # 레거시 검색 (하위 호환성)
    "StockReportSearchTool",
    "MultimodalReportSearchTool",
    "ReportImageSearchTool",
    "stock_report_search_tool",
    "multimodal_search_tool",
    "image_search_tool",
    
    # 정량적 분석 (네이버 금융)
    "NaverFinanceCrawler",
    "QuantitativeAnalyzer",
    "QuantitativeAnalysis",
    "FinancialAnalysisTool",
    "ValuationTool",
    "ProfitabilityTool",
    "FinancialHealthTool",
    "analyze_financials",
    "get_valuation",
    "get_profitability",
    
    # 웹 검색
    "WebSearchEngine",
    "WebSearchTool",
    "NewsSearchTool",
    "StockNewsSearchTool",
    "SearchResult",
    "search_web",
    "search_news",
    "search_stock_news",
    
    # 기술적 분석
    "TechnicalAnalyzer",
    "TechnicalIndicators",
    "TechnicalAnalysisTool",
    "RSIAnalysisTool",
    "MACDAnalysisTool",
    "BollingerBandTool",
    "TrendAnalysisTool",
    "analyze_stock",
    "get_rsi",
    "get_macd",
    "is_bullish",
    
    # 실시간 시세 (한국투자증권)
    "KISRealtimeTool",
    "RealtimeQuote",
    "OrderBook",
    "create_realtime_tools",
]
