# 파일: src/runner/__init__.py
"""
자율 에이전트 실행 모듈

백엔드 자동매매 대상 기반으로 테마 분석을 실행하고,
TradeSignal 저장/조건 감시 파이프라인에 필요한 결과를 생성합니다.
"""

from importlib import import_module

__all__ = [
    "AnalysisScheduler",
    "BackendAutoTradeTargetClient",
    "ThemeLeaderTradingRunner",
    "MultiThemeLeaderTradingRunner",
]


def __getattr__(name):
    # Offline calendar users must not initialize trading clients/configuration.
    modules = {
        "AnalysisScheduler": "analysis_scheduler",
        "BackendAutoTradeTargetClient": "analysis_scheduler",
        "ThemeLeaderTradingRunner": "theme_leader_trading_runner",
        "MultiThemeLeaderTradingRunner": "multi_theme_leader_trading_runner",
    }
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(f"{__name__}.{modules[name]}"), name)
    globals()[name] = value
    return value
