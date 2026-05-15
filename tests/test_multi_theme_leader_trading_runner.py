from __future__ import annotations

from pathlib import Path

from src.runner.multi_theme_leader_trading_runner import MultiThemeLeaderTradingRunner


class _FakeThemeRunner:
    def __init__(self):
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
