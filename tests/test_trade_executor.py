from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import reset_settings_cache
from src.runner.trade_executor import KST, TradeExecutor


class _DummyEnumValue:
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


class _DummyDecision:
    def __init__(
        self,
        *,
        total_score: int = 85,
        confidence: int = 75,
        action_name: str = "BUY",
        action_value: str = "매수",
        risk_name: str = "LOW",
        risk_value: str = "낮음",
    ):
        self.total_score = total_score
        self.confidence = confidence
        self.action = _DummyEnumValue(action_name, action_value)
        self.risk_level = _DummyEnumValue(risk_name, risk_value)


def _base_trading_config() -> dict:
    return {
        "enabled": True,
        "dry_run": True,
        "account_type": "paper",
        "max_daily_buy_amount": 1_000_000,
        "max_position_ratio": 0.25,
        "cooldown_minutes": 30,
        "auto_buy_conditions": {
            "min_total_score": 70,
            "min_confidence": 60,
            "allowed_actions": ["BUY", "STRONG_BUY"],
            "max_risk_level": "MEDIUM",
        },
        "auto_sell_conditions": {
            "max_total_score": 30,
            "allowed_actions": ["SELL", "STRONG_SELL"],
        },
    }


def test_trade_executor_restores_state_and_blocks_cooldown(tmp_path, monkeypatch):
    orders_dir = tmp_path / "orders"
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    today = datetime.now(KST).strftime("%Y-%m-%d")
    save_dir = orders_dir / today
    save_dir.mkdir(parents=True, exist_ok=True)
    existing_order = {
        "timestamp": datetime.now(KST).isoformat(),
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "action": "BUY",
        "quantity": 2,
        "price": 100000,
        "amount": 200000,
        "decision_score": 85,
        "decision_confidence": 75,
        "decision_action": "매수",
        "dry_run": True,
        "status": "simulated",
    }
    (save_dir / "orders.jsonl").write_text(
        json.dumps(existing_order, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    executor = TradeExecutor(_base_trading_config())
    summary = executor.get_daily_summary()
    assert summary["total_spent"] == 200000
    assert summary["order_count"] == 1

    preview = executor.preview_decision(
        stock_name="삼성전자",
        stock_code="005930",
        decision=_DummyDecision(),
        current_price=100000,
    )
    assert preview["status"] == "blocked"
    assert "쿨다운" in preview["reason"]

    reset_settings_cache()


def test_execute_buy_error_does_not_consume_budget(tmp_path, monkeypatch):
    orders_dir = tmp_path / "orders"
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    config = _base_trading_config()
    config["dry_run"] = False
    executor = TradeExecutor(config)
    monkeypatch.setattr(executor, "_send_kis_order", lambda *_args, **_kwargs: "error: fail")

    result = executor.execute_buy(
        stock_name="삼성전자",
        stock_code="005930",
        decision=_DummyDecision(),
        current_price=100000,
    )
    assert result["status"].startswith("error:")
    assert executor.get_daily_summary()["total_spent"] == 0

    reset_settings_cache()


def test_execute_buy_submits_to_kis_paper_without_real_trading_flag(tmp_path, monkeypatch):
    orders_dir = tmp_path / "orders"
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    config = _base_trading_config()
    config["dry_run"] = False
    config["account_type"] = "paper"
    config["cooldown_minutes"] = 0
    executor = TradeExecutor(config)

    calls = []

    class _FakeTool:
        is_available = True

        def __init__(self, paper: bool = False):
            self.paper = paper

        def place_order(self, **kwargs):
            calls.append({"paper": self.paper, **kwargs})
            return {
                "rt_cd": "0",
                "msg1": "정상처리 되었습니다.",
                "output": {"ODNO": "1234567890", "ORD_TMD": "090001"},
            }

    monkeypatch.setattr("src.tools.realtime_tool.KISRealtimeTool", _FakeTool)

    result = executor.execute_buy(
        stock_name="삼성전자",
        stock_code="005930",
        decision=_DummyDecision(),
        current_price=100000,
    )

    assert result["status"] == "submitted"
    assert result["dry_run"] is False
    assert result["kis_order_no"] == "1234567890"
    assert calls == [
        {
            "paper": True,
            "stock_code": "005930",
            "side": "BUY",
            "quantity": 2,
            "price": 100000,
            "order_division": "00",
        }
    ]
    assert executor.get_daily_summary()["total_spent"] == 250000

    reset_settings_cache()


def test_execute_buy_blocks_real_account_without_explicit_flag(tmp_path, monkeypatch):
    orders_dir = tmp_path / "orders"
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    config = _base_trading_config()
    config["dry_run"] = False
    config["account_type"] = "real"
    executor = TradeExecutor(config)

    result = executor.execute_buy(
        stock_name="삼성전자",
        stock_code="005930",
        decision=_DummyDecision(),
        current_price=100000,
    )

    assert result["status"].startswith("error:")
    assert "실전 주문" in result["status"]
    assert executor.get_daily_summary()["total_spent"] == 0

    reset_settings_cache()
