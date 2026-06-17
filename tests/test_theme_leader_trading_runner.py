from __future__ import annotations

import json
from pathlib import Path

from src.agents.risk_manager import FinalDecision, InvestmentAction, RiskLevel
from src.runner.decision_adapter import (
    build_final_decision_from_payload,
    final_decision_to_payload,
)
from src.runner.theme_leader_trading_runner import ThemeLeaderTradingRunner


def _write_config(path: Path) -> None:
    path.write_text(
        """
schedule:
  enabled: false
watchlist: []
trading:
  enabled: true
  dry_run: true
  account_type: "paper"
  max_daily_buy_amount: 1000000
  max_position_ratio: 0.25
  cooldown_minutes: 0
  auto_buy_conditions:
    min_total_score: 70
    min_confidence: 60
    allowed_actions: ["BUY", "STRONG_BUY"]
    max_risk_level: "MEDIUM"
  auto_sell_conditions:
    max_total_score: 30
    allowed_actions: ["SELL", "STRONG_SELL"]
""",
        encoding="utf-8",
    )


def test_decision_payload_roundtrip_preserves_executable_codes():
    decision = FinalDecision(
        stock_name="테스트",
        stock_code="123456",
        total_score=88,
        action=InvestmentAction.BUY,
        confidence=72,
        risk_level=RiskLevel.LOW,
        risk_factors=["리스크"],
        position_size="25%",
        entry_strategy="분할",
        exit_strategy="손절",
        stop_loss="-5%",
        signal_alignment="일치",
        key_catalysts=["촉매"],
        contrarian_view="반대",
        summary="요약",
        detailed_reasoning="근거",
    )

    payload = final_decision_to_payload(decision)
    rebuilt = build_final_decision_from_payload("테스트", "123456", payload)

    assert payload["action_code"] == "BUY"
    assert payload["risk_level_code"] == "LOW"
    assert rebuilt.action is InvestmentAction.BUY
    assert rebuilt.risk_level is RiskLevel.LOW
    assert rebuilt.total_score == 88
    assert rebuilt.confidence == 72


def test_decision_adapter_accepts_display_values_for_legacy_payloads():
    rebuilt = build_final_decision_from_payload(
        "테스트",
        "123456",
        {
            "total_score": 85,
            "action": "매수",
            "confidence": 70,
            "risk_level": "낮음",
            "summary": "legacy",
        },
    )

    assert rebuilt.action is InvestmentAction.BUY
    assert rebuilt.risk_level is RiskLevel.LOW


def test_theme_leader_trading_preview_uses_leader_decision(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path)

    class FakeOrchestrator:
        def run(self, **_kwargs):
            return {
                "status": "success",
                "theme": "AI",
                "theme_key": "ai",
                "candidate_count": 1,
                "evaluated_count": 1,
                "leaders": [
                    {
                        "candidate": {"stock_name": "리더", "stock_code": "123456"},
                        "leader_score": 90,
                        "final_decision": {
                            "total_score": 88,
                            "action_code": "BUY",
                            "action": "매수",
                            "confidence": 75,
                            "risk_level_code": "LOW",
                            "risk_level": "낮음",
                        },
                    }
                ],
            }

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=FakeOrchestrator(),
    )
    monkeypatch.setattr(runner, "_get_current_price", lambda _code: 10000)

    result = runner.run_once(
        theme="AI",
        theme_key="ai",
        execute=False,
        execute_top_n=1,
        save_report=False,
    )

    assert result["summary"] == {"ready": 1}
    preview = result["trade_results"][0]["preview"]
    assert preview["status"] == "ready"
    assert preview["reason"] == "signal_candidate_ready"
    assert preview["order_owner"] == "backend"
    assert result["trade_results"][0]["decision"]["action_code"] == "BUY"


def test_theme_leader_trading_blocks_buy_without_current_price(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path)

    class FakeOrchestrator:
        def run(self, **_kwargs):
            return {
                "status": "success",
                "leaders": [
                    {
                        "candidate": {"stock_name": "리더", "stock_code": "123456"},
                        "leader_score": 90,
                        "final_decision": {
                            "total_score": 88,
                            "action_code": "BUY",
                            "confidence": 75,
                            "risk_level_code": "LOW",
                        },
                    }
                ],
            }

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=FakeOrchestrator(),
    )
    monkeypatch.setattr(runner, "_get_current_price", lambda _code: None)

    result = runner.run_once(
        theme="AI",
        theme_key="ai",
        execute=True,
        execute_top_n=1,
        save_report=False,
    )

    assert result["summary"] == {"blocked": 1}
    assert result["trade_results"][0]["reason"] == "missing_current_price_for_buy"


def test_theme_leader_runner_default_orchestrator_is_removed(tmp_path):
    config_path = tmp_path / "watchlist.yaml"
    config_path.write_text(
        """
schedule:
  enabled: false
watchlist: []
trading:
  enabled: true
  dry_run: true
  account_type: "paper"
  theme_universe_filters:
    enabled: true
    require_price_history: true
    min_history_days: 60
""",
        encoding="utf-8",
    )
    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
    )

    try:
        runner._get_orchestrator()
    except ImportError as exc:
        assert "ThemeLeaderOrchestrator" in str(exc)
    else:
        raise AssertionError("legacy default theme orchestrator should be removed")


def test_theme_leader_trading_blocks_malformed_leader_score(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path)

    class FakeOrchestrator:
        def run(self, **_kwargs):
            return {
                "status": "success",
                "leaders": [
                    {
                        "candidate": {"stock_name": "리더", "stock_code": "123456"},
                        "leader_score": "N/A",
                        "final_decision": {
                            "total_score": 88,
                            "action_code": "BUY",
                            "confidence": 75,
                            "risk_level_code": "LOW",
                        },
                    }
                ],
            }

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=FakeOrchestrator(),
    )
    monkeypatch.setattr(runner, "_get_current_price", lambda _code: 10000)

    result = runner.run_once(
        theme="AI",
        theme_key="ai",
        execute=False,
        execute_top_n=1,
        save_report=False,
    )

    assert result["summary"] == {"blocked": 1}
    assert result["trade_results"][0]["reason"] == "invalid_leader_score"


def test_theme_trade_execute_requires_paper_or_dry_run():
    from main import run_theme_trading_mode

    try:
        run_theme_trading_mode(theme="AI", execute=True, paper=False, dry_run=False)
    except ValueError as exc:
        assert "Python direct order execution has been removed" in str(exc)
    else:
        raise AssertionError("python direct execution should be rejected")


def test_run_from_report_blocks_python_direct_execution_without_rerunning_orchestrator(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path)
    report_path = tmp_path / "preview.json"
    report_path.write_text(
        json.dumps(
            {
                "theme": "AI",
                "theme_key": "ai",
                "leaders": [
                    {
                        "candidate": {"stock_name": "리더", "stock_code": "123456"},
                        "leader_score": 90,
                        "final_decision": {
                            "total_score": 88,
                            "action_code": "BUY",
                            "confidence": 75,
                            "risk_level_code": "LOW",
                        },
                    }
                ],
                "trade_results": [
                    {"rank": 1, "stock_name": "리더", "stock_code": "123456", "status": "ready"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=object(),
    )
    monkeypatch.setattr(runner, "_get_current_price", lambda _code: 10000)

    result = runner.run_from_report(
        report_path=str(report_path),
        execute_top_n=1,
        execute=True,
        save_report=False,
    )

    assert result["summary"] == {"blocked": 1}
    assert result["trade_results"][0]["trade"]["reason"] == "python_direct_order_execution_removed"
