from __future__ import annotations

import importlib.util


def test_legacy_direct_order_runner_modules_are_removed():
    removed_modules = [
        "src.runner.autonomous_runner",
        "src.runner.theme_paper_runner",
        "src.runner.trade_executor",
        "src.runner.paper_order_guard",
        "src.runner.paper_portfolio_manager",
        "src.runner.paper_position_store",
        "src.runner.llm_theme_decision_engine",
        "src.runner.theme_evidence_builder",
        "src.runner.multi_theme_scheduler",
    ]

    for module_name in removed_modules:
        assert importlib.util.find_spec(module_name) is None


def test_runner_package_exports_only_signal_pipeline_components():
    import src.runner as runner

    assert "AutonomousRunner" not in runner.__all__
    assert "TradeExecutor" not in runner.__all__
    assert runner.__all__ == [
        "AnalysisScheduler",
        "BackendAutoTradeTargetClient",
        "ThemeLeaderTradingRunner",
        "MultiThemeLeaderTradingRunner",
    ]
