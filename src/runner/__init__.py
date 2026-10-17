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

# Exports load on first use so the signal monitor and scheduler processes do not
# import the legacy agent/LLM/KIS stack just by importing a runner submodule.
_EXPORTS = {
    "AnalysisScheduler": "src.runner.analysis_scheduler",
    "BackendAutoTradeTargetClient": "src.runner.analysis_scheduler",
    "ThemeLeaderTradingRunner": "src.runner.theme_leader_trading_runner",
    "MultiThemeLeaderTradingRunner": "src.runner.multi_theme_leader_trading_runner",
}


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
