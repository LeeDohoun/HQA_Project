# 파일: src/tools/__init__.py
"""
HQA public tool surface.

External callers should use only the grouped tool namespaces:
- evidence
- finance
- chart
- realtime

Legacy wrappers and lower-level classes remain importable from their module paths,
but are not part of the primary public API of `src.tools`.
"""

def _missing_dependency(feature: str, exc: Exception):
    def _raiser(*args, **kwargs):
        raise ImportError(f"{feature} dependencies are not available: {exc}") from exc

    return _raiser


try:
    from .evidence_tool import EvidenceSearchTool, get_retriever, search_documents, search_reports
except ImportError as exc:
    EvidenceSearchTool = None
    get_retriever = _missing_dependency("evidence", exc)
    search_documents = _missing_dependency("evidence", exc)
    search_reports = _missing_dependency("evidence", exc)

try:
    from .finance_tool import (
        QuantitativeAnalysis,
        QuantitativeAnalyzer,
        analyze_financials,
        get_profitability,
        get_valuation,
    )
except ImportError as exc:
    QuantitativeAnalysis = None
    QuantitativeAnalyzer = None
    analyze_financials = _missing_dependency("finance", exc)
    get_profitability = _missing_dependency("finance", exc)
    get_valuation = _missing_dependency("finance", exc)

try:
    from .charts_tools import (
        TechnicalAnalyzer,
        TechnicalIndicators,
        analyze_stock,
        get_macd,
        get_rsi,
        is_bullish,
    )
except ImportError as exc:
    TechnicalAnalyzer = None
    TechnicalIndicators = None
    analyze_stock = _missing_dependency("chart", exc)
    get_macd = _missing_dependency("chart", exc)
    get_rsi = _missing_dependency("chart", exc)
    is_bullish = _missing_dependency("chart", exc)

try:
    from .realtime_tool import KISRealtimeTool, OrderBook, RealtimeQuote, create_realtime_tools

    _HAS_REALTIME = True
except ImportError:
    KISRealtimeTool = None
    RealtimeQuote = None
    OrderBook = None
    create_realtime_tools = None
    _HAS_REALTIME = False


class _EvidenceTools:
    SearchTool = EvidenceSearchTool

    @staticmethod
    def get_retriever():
        return get_retriever()

    @staticmethod
    def search(query: str, k: int = 3) -> str:
        return search_documents(query, k=k)

    @staticmethod
    def search_reports(query: str, k: int = 3) -> str:
        return search_reports(query, k=k)


class _FinanceTools:
    Analyzer = QuantitativeAnalyzer
    Analysis = QuantitativeAnalysis

    @staticmethod
    def analyze(stock_code: str) -> QuantitativeAnalysis:
        return analyze_financials(stock_code)

    @staticmethod
    def valuation(stock_code: str):
        return get_valuation(stock_code)

    @staticmethod
    def profitability(stock_code: str):
        return get_profitability(stock_code)


class _ChartTools:
    Analyzer = TechnicalAnalyzer
    Indicators = TechnicalIndicators

    @staticmethod
    def analyze(stock_code: str, stock_name: str = "Unknown") -> TechnicalIndicators:
        return analyze_stock(stock_code, stock_name=stock_name)

    @staticmethod
    def rsi(stock_code: str) -> float:
        return get_rsi(stock_code)

    @staticmethod
    def macd(stock_code: str):
        return get_macd(stock_code)

    @staticmethod
    def bullish(stock_code: str) -> bool:
        return is_bullish(stock_code)


class _RealtimeTools:
    Tool = KISRealtimeTool
    Quote = RealtimeQuote
    OrderBook = OrderBook

    @property
    def available(self) -> bool:
        return _HAS_REALTIME and KISRealtimeTool is not None

    @staticmethod
    def create():
        if create_realtime_tools is None:
            raise ImportError("realtime tool dependencies are not installed.")
        return create_realtime_tools()


evidence = _EvidenceTools()
finance = _FinanceTools()
chart = _ChartTools()
realtime = _RealtimeTools()


# Compatibility aliases for existing direct imports inside the project.
EvidenceSearchTool = EvidenceSearchTool
QuantitativeAnalyzer = QuantitativeAnalyzer
QuantitativeAnalysis = QuantitativeAnalysis
TechnicalAnalyzer = TechnicalAnalyzer
TechnicalIndicators = TechnicalIndicators
KISRealtimeTool = KISRealtimeTool

__all__ = [
    "evidence",
    "finance",
    "chart",
    "realtime",
]
