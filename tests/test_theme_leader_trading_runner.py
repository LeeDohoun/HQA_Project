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


def _write_real_config(path: Path) -> None:
    path.write_text(
        """
schedule:
  enabled: false
watchlist: []
trading:
  enabled: false
  dry_run: true
  account_type: "real"
  allow_real_trading: true
  max_daily_buy_amount: 1000000
  max_position_ratio: 0.25
  cooldown_minutes: 0
  auto_buy_conditions:
    min_total_score: 70
    min_confidence: 60
    allowed_actions: ["BUY", "STRONG_BUY"]
    max_risk_level: "MEDIUM"
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
    preview_calls = []

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

    class FakeExecutor:
        def get_runtime_config(self):
            return {"enabled": True, "dry_run": True, "account_type": "paper"}

        def preview_decision(self, **kwargs):
            preview_calls.append(kwargs)
            return {"status": "ready", "reason": "buy_conditions_met"}

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=FakeOrchestrator(),
        executor=FakeExecutor(),
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
    assert preview_calls[0]["stock_code"] == "123456"
    assert preview_calls[0]["current_price"] == 10000
    assert preview_calls[0]["decision"].action is InvestmentAction.BUY


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

    class FakeExecutor:
        def get_runtime_config(self):
            return {"enabled": True}

        def preview_decision(self, **_kwargs):
            raise AssertionError("missing price buy should not reach executor")

        def execute_decision(self, **_kwargs):
            raise AssertionError("missing price buy should not reach executor")

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=FakeOrchestrator(),
        executor=FakeExecutor(),
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


def test_account_type_override_forces_paper_runtime(tmp_path):
    config_path = tmp_path / "watchlist.yaml"
    _write_real_config(config_path)

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        dry_run_override=False,
        trading_enabled_override=True,
        account_type_override="paper",
        orchestrator=object(),
    )

    runtime = runner._executor.get_runtime_config()
    assert runtime["enabled"] is True
    assert runtime["dry_run"] is False
    assert runtime["account_type"] == "paper"
    assert runner._is_paper_account() is True


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

    class FakeExecutor:
        def get_runtime_config(self):
            return {"enabled": True}

        def preview_decision(self, **_kwargs):
            raise AssertionError("malformed score should be blocked before preview")

        def execute_decision(self, **_kwargs):
            raise AssertionError("malformed score should be blocked before execution")

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=FakeOrchestrator(),
        executor=FakeExecutor(),
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
        assert "--paper or --dry-run" in str(exc)
    else:
        raise AssertionError("execute without paper or dry-run should be rejected")


def test_run_from_report_executes_ready_preview_without_rerunning_orchestrator(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path)
    report_path = tmp_path / "preview.json"
    execute_calls = []
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

    class FakeExecutor:
        def get_runtime_config(self):
            return {"enabled": True, "dry_run": False, "account_type": "paper"}

        def execute_decision(self, **kwargs):
            execute_calls.append(kwargs)
            return {"status": "simulated", "reason": "ok"}

    runner = ThemeLeaderTradingRunner(
        config_path=str(config_path),
        data_dir=str(tmp_path),
        orchestrator=object(),
        executor=FakeExecutor(),
    )
    monkeypatch.setattr(runner, "_get_current_price", lambda _code: 10000)

    result = runner.run_from_report(
        report_path=str(report_path),
        execute_top_n=1,
        execute=True,
        save_report=False,
    )

    assert result["summary"] == {"simulated": 1}
    assert execute_calls[0]["stock_code"] == "123456"
    assert execute_calls[0]["decision"].action is InvestmentAction.BUY
