# 파일: src/runner/__init__.py
"""
자율 에이전트 실행 모듈

백엔드 자동매매 대상 기반으로 테마 분석을 실행하고,
TradeSignal 저장/조건 감시 파이프라인에 필요한 결과를 생성합니다.
"""

from src.runner.analysis_scheduler import AnalysisScheduler, BackendAutoTradeTargetClient
from src.runner.multi_theme_leader_trading_runner import MultiThemeLeaderTradingRunner
from src.runner.theme_leader_trading_runner import ThemeLeaderTradingRunner

__all__ = [
    "AnalysisScheduler",
    "BackendAutoTradeTargetClient",
    "ThemeLeaderTradingRunner",
    "MultiThemeLeaderTradingRunner",
]
