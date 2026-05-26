from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import reset_settings_cache
from src.runner.llm_theme_decision_engine import LLMThemeDecisionEngine
from src.runner.paper_order_guard import PaperOrderGuard
from src.runner.theme_paper_runner import KST, ThemePaperRunner


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_config(path: Path, *, enabled: bool = True) -> None:
    path.write_text(
        f"""
theme_trading:
  enabled: {str(enabled).lower()}
  dry_run: true
  account_type: "paper"
  order_type: "limit"
  themes:
    - theme: "AI"
      theme_key: "ai"
      enabled: true
      max_candidates: 10
  universe_filters:
    min_avg_trading_value_20d: 1000
    max_volatility_20d: 1.2
    max_return_5d: 1.0
    max_return_20d: 1.0
    min_trend_150d: -1.0
    require_price_history: true
    require_recent_documents: false
    min_history_days: 30
  llm_decision:
    enabled: true
    mode: "multi_theme_committee"
    top_themes: 3
    top_stocks_per_theme: 3
    min_confidence_buy: 65
    min_confidence_sell: 60
    require_reason: true
    require_invalidation: true
    max_evidence_cards_per_theme: 10
  portfolio:
    total_budget: 1000000
    max_positions: 5
    max_position_ratio: 0.20
    max_theme_ratio: 0.50
    cash_buffer_ratio: 0.20
    allow_scale_in: false
    allow_rebalance: true
  schedule:
    market_hours_only: false
    scan_interval_minutes: 30
    review_interval_minutes: 30
  order_guard:
    cooldown_minutes: 30
    block_if_price_missing: true
    block_if_llm_error: true
    block_duplicate_position: true
    block_if_quantity_zero: true
""",
        encoding="utf-8",
    )


def _write_theme_data(data_dir: Path) -> None:
    _write_jsonl(
        data_dir / "raw" / "theme_targets" / "ai.jsonl",
        [{"stock_name": "AI Alpha", "stock_code": "000001", "corp_code": "001"}],
    )

    start = datetime(2025, 9, 1)
    chart_rows = []
    for idx in range(180):
        close = 9000 + idx * 10
        chart_rows.append(
            {
                "source_type": "chart",
                "stock_name": "AI Alpha",
                "stock_code": "000001",
                "timestamp": (start + timedelta(days=idx)).isoformat(),
                "open": str(close - 10),
                "high": str(close + 50),
                "low": str(close - 50),
                "close": str(close),
                "volume": "200000",
            }
        )
    _write_jsonl(data_dir / "market_data" / "ai" / "chart.jsonl", chart_rows)
    _write_jsonl(
        data_dir / "canonical_index" / "ai" / "corpus.jsonl",
        [
            {
                "text": "AI server component supply expanded",
                "metadata": {
                    "source_type": "news",
                    "stock_name": "AI Alpha",
                    "stock_code": "000001",
                    "title": "AI infrastructure order momentum",
                    "content": "AI server component supply expanded",
                    "published_at": "2026-05-18",
                },
            }
        ],
    )


def _buy_decision() -> str:
    return json.dumps(
        {
            "market_regime": "risk_on",
            "selected_themes": [
                {"theme_key": "ai", "theme": "AI", "weight": 0.4, "reason": "strong breadth"}
            ],
            "excluded_themes": [],
            "positions": [
                {
                    "theme_key": "ai",
                    "theme": "AI",
                    "stock_code": "000001",
                    "stock_name": "AI Alpha",
                    "action": "BUY",
                    "target_weight": 0.15,
                    "confidence": 80,
                    "reason": "theme directness and trading value leadership",
                    "invalidation": "theme trading value contracts",
                }
            ],
            "watch": [],
            "reject": [],
            "cash_weight": 0.2,
        },
        ensure_ascii=False,
    )


def test_theme_paper_runner_normal_buy_records_dry_run_order_and_position(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    orders_dir = tmp_path / "orders"
    config_path = tmp_path / "theme_trading.yaml"
    _write_config(config_path)
    _write_theme_data(data_dir)
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    runner = ThemePaperRunner(
        config_path=str(config_path),
        data_dir=str(data_dir),
        llm_client=lambda _prompt: _buy_decision(),
        now_provider=lambda: datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )

    result = runner.run_once()

    assert result["status"] == "success"
    assert result["orders"][0]["guard_result"]["allowed"] is True
    assert result["orders"][0]["order_result"]["status"] == "simulated"
    assert result["orders"][0]["order_result"]["metadata"]["paper_trading_mode"] == "multi_theme"

    positions = json.loads((data_dir / "paper_trading" / "positions.json").read_text(encoding="utf-8"))
    assert positions["positions"][0]["stock_code"] == "000001"
    assert positions["positions"][0]["entry_confidence"] == 80

    order_logs = list(orders_dir.glob("*/orders.jsonl"))
    assert len(order_logs) == 1
    logged = json.loads(order_logs[0].read_text(encoding="utf-8").strip())
    assert logged["status"] == "simulated"
    assert logged["metadata"]["theme_key"] == "ai"

    reset_settings_cache()


def test_theme_paper_runner_disabled_places_no_orders(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    orders_dir = tmp_path / "orders"
    config_path = tmp_path / "theme_trading.yaml"
    _write_config(config_path, enabled=False)
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    runner = ThemePaperRunner(config_path=str(config_path), data_dir=str(data_dir), llm_client=lambda _prompt: _buy_decision())

    result = runner.run_once()

    assert result["status"] == "disabled"
    assert result["orders"] == []
    assert list(orders_dir.glob("*/orders.jsonl")) == []

    reset_settings_cache()


def test_theme_paper_runner_llm_parse_failure_records_error_without_order(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    orders_dir = tmp_path / "orders"
    config_path = tmp_path / "theme_trading.yaml"
    _write_config(config_path)
    _write_theme_data(data_dir)
    monkeypatch.setenv("HQA_ORDERS_DIR", str(orders_dir))
    reset_settings_cache()

    runner = ThemePaperRunner(
        config_path=str(config_path),
        data_dir=str(data_dir),
        llm_client=lambda _prompt: "not json",
        now_provider=lambda: datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )

    result = runner.run_once()

    assert result["status"] == "llm_error"
    assert result["orders"] == []
    journal = (data_dir / "paper_trading" / "decision_journal.jsonl").read_text(encoding="utf-8")
    assert "llm_error" in journal
    assert list(orders_dir.glob("*/orders.jsonl")) == []

    reset_settings_cache()


def test_llm_engine_low_confidence_buy_becomes_watch():
    engine = LLMThemeDecisionEngine(
        {
            "llm_decision": {"enabled": True, "min_confidence_buy": 65, "require_reason": True, "require_invalidation": True},
            "portfolio": {"max_positions": 5, "cash_buffer_ratio": 0.2},
        },
        llm_client=lambda _prompt: json.dumps(
            {
                "positions": [
                    {
                        "theme_key": "ai",
                        "theme": "AI",
                        "stock_code": "000001",
                        "stock_name": "AI Alpha",
                        "action": "BUY",
                        "target_weight": 0.1,
                        "confidence": 50,
                        "reason": "weak",
                        "invalidation": "weaker",
                    }
                ]
            }
        ),
    )

    result = engine.decide({"themes": [], "portfolio_state": {}, "config": {}})

    assert result["status"] == "success"
    assert result["decision"]["positions"] == []
    assert result["decision"]["watch"][0]["action"] == "WATCH"
    assert result["decision"]["watch"][0]["validation_errors"][0].startswith("confidence_below_min_buy")


def test_paper_order_guard_blocks_core_safety_cases():
    config = {
        "theme_trading": {
            "enabled": True,
            "llm_decision": {"min_confidence_buy": 65},
            "portfolio": {
                "total_budget": 1000000,
                "max_positions": 1,
                "max_position_ratio": 0.2,
                "max_theme_ratio": 0.5,
                "allow_scale_in": False,
            },
            "schedule": {"market_hours_only": False},
            "order_guard": {
                "cooldown_minutes": 30,
                "block_if_price_missing": True,
                "block_duplicate_position": True,
                "block_if_quantity_zero": True,
                "block_if_llm_error": True,
            },
        }
    }
    base_intent = {
        "side": "BUY",
        "theme_key": "ai",
        "stock_code": "000001",
        "current_price": 10000,
        "quantity": 1,
        "order_amount": 10000,
        "confidence": 80,
    }

    duplicate_guard = PaperOrderGuard(config)
    assert duplicate_guard.validate(
        base_intent,
        {"positions_count": 1, "positions": [{"stock_code": "000001"}], "theme_values": {}},
        now=datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )["reason"] == "duplicate_position"

    missing_price_guard = PaperOrderGuard(config)
    assert missing_price_guard.validate(
        {**base_intent, "current_price": None},
        {"positions_count": 0, "positions": [], "theme_values": {}},
        now=datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )["reason"] == "missing_current_price"

    zero_quantity_guard = PaperOrderGuard(config)
    assert zero_quantity_guard.validate(
        {**base_intent, "quantity": 0},
        {"positions_count": 0, "positions": [], "theme_values": {}},
        now=datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )["reason"] == "quantity_zero"

    max_positions_guard = PaperOrderGuard(config)
    assert max_positions_guard.validate(
        base_intent,
        {"positions_count": 1, "positions": [{"stock_code": "000002"}], "theme_values": {}},
        now=datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )["reason"] == "max_positions"

    max_theme_guard = PaperOrderGuard(config)
    assert max_theme_guard.validate(
        {**base_intent, "order_amount": 20000},
        {"positions_count": 0, "positions": [], "theme_values": {"ai": 490000}},
        now=datetime(2026, 5, 18, 10, 0, tzinfo=KST),
    )["reason"] == "max_theme_ratio"
