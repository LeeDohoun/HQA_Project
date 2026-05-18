from __future__ import annotations

from pathlib import Path

from src.runner.multi_theme_leader_trading_runner import MultiThemeLeaderTradingRunner


class _FakeThemeRunner:
    def __init__(self):
        self._config = {"trading": {}}
        self._executor = type("Executor", (), {"get_runtime_config": lambda self: {"enabled": True, "dry_run": True, "account_type": "paper"}})()

    def run_once(self, **kwargs):
        theme_key = kwargs["theme_key"]
        if theme_key == "ai":
            leaders = [
                {
                    "candidate": {"stock_name": "AI-Alpha", "stock_code": "111111"},
                    "leader_score": 88,
                    "final_decision": {"action_code": "BUY", "action": "매수", "confidence": 80, "risk_level_code": "LOW", "risk_level": "낮음"},
                },
                {
                    "candidate": {"stock_name": "AI-Beta", "stock_code": "111112"},
                    "leader_score": 70,
                    "final_decision": {"action_code": "BUY", "action": "매수", "confidence": 62, "risk_level_code": "MEDIUM", "risk_level": "보통"},
                },
            ]
        elif theme_key == "robot":
            leaders = [
                {
                    "candidate": {"stock_name": "RB-Prime", "stock_code": "222221"},
                    "leader_score": 86,
                    "final_decision": {"action_code": "BUY", "action": "매수", "confidence": 84, "risk_level_code": "LOW", "risk_level": "낮음"},
                }
            ]
        else:
            leaders = [
                {
                    "candidate": {"stock_name": "BN-Weak", "stock_code": "333331"},
                    "leader_score": 55,
                    "final_decision": {"action_code": "HOLD", "action": "관망", "confidence": 50, "risk_level_code": "HIGH", "risk_level": "높음"},
                }
            ]
        return {
            "status": "success",
            "theme": theme_key.upper(),
            "theme_key": theme_key,
            "candidate_count": 3,
            "evaluated_count": len(leaders),
            "leaders": leaders,
        }

    def _preview_or_execute_leader(self, *, leader, rank, execute, min_leader_score):
        candidate = leader["candidate"]
        mode = "trade" if execute else "preview"
        return {
            "rank": rank,
            "stock_name": candidate["stock_name"],
            "stock_code": candidate["stock_code"],
            "leader_score": int(leader["leader_score"]),
            "mode": mode,
            "status": "ready" if not execute else "filled",
            "price": 10000,
            "preview": {"status": "ready"} if not execute else None,
            "trade": {"status": "filled"} if execute else None,
        }


def _touch_theme_files(root: Path) -> None:
    theme_dir = root / "raw" / "theme_targets"
    theme_dir.mkdir(parents=True, exist_ok=True)
    for key in ("ai", "robot", "battery"):
        (theme_dir / f"{key}.jsonl").write_text("", encoding="utf-8")


def test_multi_theme_runner_selects_top_n_with_thresholds(tmp_path):
    _touch_theme_files(tmp_path)
    runner = MultiThemeLeaderTradingRunner(
        data_dir=str(tmp_path),
        theme_runner=_FakeThemeRunner(),
    )

    result = runner.run_all(
        candidate_limit=3,
        per_theme_top_n=2,
        top_n=3,
        execute=False,
        min_leader_score=75,
        min_confidence=65,
        max_risk_level="MEDIUM",
        save_report=False,
    )

    assert result["status"] == "success"
    assert result["selected_count"] == 2
    assert result["best_theme"] in {"AI", "ROBOT"}
    assert len(result["best_leader_stocks"]) == 2
    assert all(row["leader_score"] >= 75 for row in result["best_leader_stocks"])
    assert all(row["confidence"] >= 65 for row in result["best_leader_stocks"])
    assert result["summary"] == {"ready": 2}


def test_multi_theme_runner_execute_returns_trade_status(tmp_path):
    _touch_theme_files(tmp_path)
    runner = MultiThemeLeaderTradingRunner(
        data_dir=str(tmp_path),
        theme_runner=_FakeThemeRunner(),
    )

    result = runner.run_all(
        candidate_limit=3,
        per_theme_top_n=2,
        top_n=1,
        execute=True,
        min_leader_score=75,
        min_confidence=65,
        max_risk_level="MEDIUM",
        save_report=False,
    )

    assert result["selected_count"] == 1
    assert len(result["trade_results"]) == 1
    assert result["trade_results"][0]["status"] == "filled"
    assert result["summary"] == {"filled": 1}


def test_multi_theme_runner_without_thresholds_can_select_buy_candidates(tmp_path):
    _touch_theme_files(tmp_path)
    runner = MultiThemeLeaderTradingRunner(
        data_dir=str(tmp_path),
        theme_runner=_FakeThemeRunner(),
    )

    short_result = runner.run_all(
        candidate_limit=3,
        per_theme_top_n=2,
        top_n=3,
        execute=False,
        strategy_profile="short",
        save_report=False,
    )
    assert short_result["strategy_profile"] == "short"
    assert short_result["thresholds"]["min_leader_score"] is None
    assert short_result["thresholds"]["min_confidence"] is None
    assert short_result["thresholds"]["max_risk_level"] is None
    assert short_result["selected_count"] >= 2


def test_multi_theme_runner_quality_shadow_payload_and_penalty_reason(tmp_path, monkeypatch):
    _touch_theme_files(tmp_path)
    theme_runner = _FakeThemeRunner()
    theme_runner._config = {
        "trading": {
            "signal_quality": {
                "enabled": True,
                "mode": "shadow_penalty",
                "apply_scopes": ["short", "long"],
                "report_only": False,
                "penalty_weights": {"low_trading_value": 20.0},
                "thresholds": {"min_avg_trading_value_20d": 1000000000.0},
            }
        }
    }
    runner = MultiThemeLeaderTradingRunner(
        data_dir=str(tmp_path),
        theme_runner=theme_runner,
    )

    monkeypatch.setattr(
        "src.runner.signal_quality_filter.SignalQualityFilter.evaluate",
        lambda self, **kwargs: {
            "violations": ["avg_trading_value_20d_below_min"],
            "penalty": 20.0 if kwargs.get("stock_code") == "111111" else 0.0,
            "metrics_snapshot": {
                "avg_trading_value_20d": 100.0,
                "volatility_20d": 0.01,
                "return_5d": 0.01,
                "return_20d": 0.02,
                "trend_150d": 1.02,
            },
            "breadth_state": {"state": "broad", "ratio": 0.6, "threshold": 0.45},
        },
    )
    monkeypatch.setattr(
        "src.runner.multi_theme_leader_trading_runner.MultiThemeLeaderTradingRunner._compute_breadth_ratio",
        lambda _self, _rows: 0.6,
    )

    result = runner.run_all(
        candidate_limit=3,
        per_theme_top_n=2,
        top_n=3,
        execute=False,
        min_leader_score=75,
        min_confidence=60,
        max_risk_level="MEDIUM",
        save_report=False,
    )

    assert result["signal_quality"]["enabled"] is True
    assert result["selection_reason_aggregation"]["zero_selection_reasons"]["quality_penalty"] >= 1
    assert result["selected_count"] >= 1
    assert "risk_filter_shadow" in result["global_ranked_leaders"][0]
    assert "violations" in result["global_ranked_leaders"][0]["risk_filter_shadow"]


def test_multi_theme_runner_execute_state_and_preview_state(tmp_path):
    _touch_theme_files(tmp_path)
    runner = MultiThemeLeaderTradingRunner(
        data_dir=str(tmp_path),
        theme_runner=_FakeThemeRunner(),
    )

    preview_result = runner.run_all(
        candidate_limit=3,
        per_theme_top_n=2,
        top_n=1,
        execute=False,
        min_leader_score=75,
        min_confidence=65,
        max_risk_level="MEDIUM",
        save_report=False,
    )
    assert preview_result["trade_results"][0]["execution_state"] == "preview_ready"

    execute_result = runner.run_all(
        candidate_limit=3,
        per_theme_top_n=2,
        top_n=1,
        execute=True,
        min_leader_score=75,
        min_confidence=65,
        max_risk_level="MEDIUM",
        save_report=False,
    )
    assert execute_result["trade_results"][0]["execution_state"] == "execute_sent"
