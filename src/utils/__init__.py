# 파일: src/utils/__init__.py
"""
HQA 유틸리티 모음

- stock_mapper: 종목명 ↔ 종목코드 변환
- kis_auth: 한국투자증권 API 인증
- parallel: 병렬 실행 유틸리티
- memory: 대화형 메모리
"""

from importlib import import_module

# Importing pure helpers such as stock_codes must not load KIS or runtime code.
_EXPORTS = {
    "StockMapper": "stock_mapper",
    "StockInfo": "stock_mapper",
    "get_mapper": "stock_mapper",
    "get_stock_code": "stock_mapper",
    "get_stock_name": "stock_mapper",
    "search_stocks": "stock_mapper",
    "find_stocks_in_text": "stock_mapper",
    "KISConfig": "kis_auth",
    "KISToken": "kis_auth",
    "call_api": "kis_auth",
    "get_base_headers": "kis_auth",
    "is_api_available": "kis_auth",
    "run_agents_parallel": "parallel",
    "is_error": "parallel",
    "ConversationMemory": "memory",
    "ConversationTurn": "memory",
}


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value

__all__ = [
    # stock_mapper
    "StockMapper",
    "StockInfo",
    "get_mapper",
    "get_stock_code",
    "get_stock_name",
    "search_stocks",
    "find_stocks_in_text",
    # kis_auth
    "KISConfig",
    "KISToken",
    "call_api",
    "get_base_headers",
    "is_api_available",
    # parallel
    "run_agents_parallel",
    "is_error",
    # memory
    "ConversationMemory",
    "ConversationTurn",
]
