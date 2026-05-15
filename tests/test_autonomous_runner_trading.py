from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runner.autonomous_runner import AutonomousRunner
from src.runner.autonomous_runner import KST
from src.tools import realtime_tool
from datetime import datetime, timedelta


class _EnumValue:
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


class _Decision:
    total_score = 20
    confidence = 80
    action = _EnumValue("SELL", "매도")
    risk_level = _EnumValue("LOW", "낮음")


def _write_config(path: Path, *, account_type: str = "paper") -> None:
    path.write_text(
        f"""
schedule:
  enabled: false
watchlist: []
trading:
  enabled: true
  dry_run: false
  account_type: "{account_type}"
  allow_real_trading: true
  auto_buy_conditions:
    allowed_actions: ["BUY"]
  auto_sell_conditions:
    allowed_actions: ["SELL"]
""",
        encoding="utf-8",
    )


def test_runner_current_price_uses_account_mode_and_stockprice_object(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path, account_type="paper")
    calls = []

    class _Price:
        current_price = 12345

    class _FakeTool:
        is_available = True

        def __init__(self, paper: bool = False):
            calls.append(paper)

        def get_current_price(self, stock_code: str):
            assert stock_code == "005930"
            return _Price()

    monkeypatch.setattr("src.tools.realtime_tool.KISRealtimeTool", _FakeTool)

    runner = AutonomousRunner(config_path=str(config_path))

    assert runner._get_current_price("005930") == 12345
    assert calls == [True]


def test_runner_sell_path_uses_kis_orderable_quantity(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path, account_type="real")
    tool_calls = []
    sell_calls = []

    class _FakeTool:
        is_available = True

        def __init__(self, paper: bool = False):
            tool_calls.append(paper)

        def get_current_price(self, stock_code: str):
            return {"stck_prpr": 50000}

        def get_holding_quantity(self, stock_code: str, *, orderable: bool = True):
            assert stock_code == "005930"
            assert orderable is True
            return 7

    class _FakeExecutor:
        is_dry_run = False
        is_enabled = True

        def should_buy(self, decision):
            return False

        def should_sell(self, decision):
            return True

        def execute_sell(self, stock_name, stock_code, decision, quantity=0, current_price=None):
            sell_calls.append(
                {
                    "stock_name": stock_name,
                    "stock_code": stock_code,
                    "quantity": quantity,
                    "current_price": current_price,
                }
            )
            return {"status": "submitted"}

    monkeypatch.setattr("src.tools.realtime_tool.KISRealtimeTool", _FakeTool)

    runner = AutonomousRunner(config_path=str(config_path))
    runner._executor = _FakeExecutor()

    result = runner._evaluate_trade("삼성전자", "005930", _Decision())

    assert result == {"status": "submitted"}
    assert sell_calls == [
        {
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "quantity": 7,
            "current_price": 50000,
        }
    ]
    assert tool_calls == [False, False]


def test_kis_holdings_use_balance_tr_id_and_parse_quantities(monkeypatch):
    calls = []

    monkeypatch.setattr(realtime_tool, "is_trading_api_available", lambda paper=False: True)
    monkeypatch.setattr(
        realtime_tool.KISConfig,
        "get_account",
        staticmethod(lambda paper=False: ("12345678", "01")),
    )

    def _fake_call_api(method, path, tr_id, params=None, paper=False, **kwargs):
        calls.append(
            {
                "method": method,
                "path": path,
                "tr_id": tr_id,
                "params": params,
                "paper": paper,
            }
        )
        return {
            "rt_cd": "0",
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "10",
                    "ord_psbl_qty": "7",
                    "prpr": "50000",
                    "evlu_amt": "500000",
                    "evlu_pfls_amt": "12000",
                    "evlu_pfls_rt": "2.40",
                }
            ],
        }

    monkeypatch.setattr(realtime_tool, "call_api", _fake_call_api)

    holdings = realtime_tool.get_domestic_stock_holdings(paper=False)

    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/uapi/domestic-stock/v1/trading/inquire-balance"
    assert calls[0]["tr_id"] == "TTTC8434R"
    assert calls[0]["paper"] is False
    assert calls[0]["params"]["CANO"] == "12345678"
    assert holdings[0].stock_code == "005930"
    assert holdings[0].holding_quantity == 10
    assert holdings[0].orderable_quantity == 7


def test_long_plan_builds_trigger_at_analysis_time(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    config_path.write_text(
        """
strategy_schedule:
  short:
    enabled: false
  long:
    enabled: true
    analysis_time: "08:00"
    buy_trigger_pct: -1.0
watchlist:
  - name: "삼성전자"
    code: "005930"
    mode: "full"
    strategy: "long"
trading:
  enabled: true
  dry_run: true
  account_type: "paper"
  auto_buy_conditions:
    allowed_actions: ["BUY", "STRONG_BUY"]
""",
        encoding="utf-8",
    )

    runner = AutonomousRunner(config_path=str(config_path))

    decision = _Decision()
    decision.action = _EnumValue("BUY", "매수")
    decision.total_score = 85
    decision.confidence = 70
    monkeypatch.setattr(runner, "_analyze_stock", lambda *_args, **_kwargs: {"final_decision": decision})
    monkeypatch.setattr(runner, "_get_current_price", lambda _code: 100000)

    now = datetime(2026, 5, 14, 8, 5, tzinfo=KST)
    runner._build_long_strategy_plan_if_due(now=now, analysis_time="08:00", plan_ttl_hours=24)

    assert len(runner._long_pending_triggers) == 1
    trigger = runner._long_pending_triggers[0]
    assert trigger["stock_code"] == "005930"
    assert trigger["target_price"] == 99000
    assert trigger["comparator"] == "lte"


def test_long_trigger_executes_when_price_hits(tmp_path, monkeypatch):
    config_path = tmp_path / "watchlist.yaml"
    _write_config(config_path, account_type="paper")
    runner = AutonomousRunner(config_path=str(config_path))

    decision = _Decision()
    decision.action = _EnumValue("BUY", "매수")
    decision.total_score = 88
    decision.confidence = 75

    runner._long_pending_triggers = [
        {
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "decision": decision,
            "target_price": 99000,
            "comparator": "lte",
            "expires_at": datetime.now(KST) + timedelta(hours=2),
            "created_at": datetime.now(KST),
        }
    ]

    monkeypatch.setattr(runner, "_get_current_price", lambda _code: 98500)

    calls = []

    class _FakeExecutor:
        is_enabled = True
        is_dry_run = True

        def execute_buy(self, stock_name, stock_code, decision, current_price=None):
            calls.append((stock_name, stock_code, current_price))
            return {"status": "simulated"}

        def execute_sell(self, *args, **kwargs):
            raise AssertionError("sell should not be called")

    runner._executor = _FakeExecutor()
    now = datetime.now(KST)
    runner._execute_long_strategy_triggers_if_due(
        now=now,
        check_interval_minutes=1,
        market_hours_only=False,
    )

    assert calls == [("삼성전자", "005930", 98500)]
    assert runner._long_pending_triggers == []
