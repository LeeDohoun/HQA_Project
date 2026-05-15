from __future__ import annotations

from datetime import datetime, timedelta

from src.runner.multi_theme_scheduler import KST, MultiThemeScheduler


class _FakeRunner:
    def __init__(self):
        self.calls = 0

    def run_all(self, **kwargs):
        self.calls += 1
        execute = bool(kwargs.get("execute"))
        return {
            "selected_count": 1 if execute else 0,
            "summary": {"simulated": 1} if execute else {},
            "report_path": "dummy.json",
            "global_ranked_leaders": [
                {
                    "theme_key": "ai",
                    "theme": "AI",
                    "stock_code": "111111",
                    "stock_name": "AI-Alpha",
                    "action_code": "BUY",
                }
            ],
            "best_leader_stocks": [
                {"stock_code": "111111", "stock_name": "AI-Alpha"}
            ],
        }

    def get_holdings(self):
        return []

    def get_current_price(self, _code: str):
        return 10000


def test_scheduler_due_logic():
    runner = _FakeRunner()
    scheduler = MultiThemeScheduler(trade_runner=runner, short_interval_minutes=60)
    now = datetime(2026, 5, 14, 10, 0, tzinfo=KST)
    assert scheduler._is_due(now, None, 60) is True
    assert scheduler._is_due(now, now - timedelta(minutes=61), 60) is True
    assert scheduler._is_due(now, now - timedelta(minutes=30), 60) is False


def test_scheduler_market_hours_check():
    runner = _FakeRunner()
    scheduler = MultiThemeScheduler(trade_runner=runner)
    market_time = datetime(2026, 5, 14, 10, 0, tzinfo=KST)  # Thu
    after_close = datetime(2026, 5, 14, 16, 0, tzinfo=KST)
    weekend = datetime(2026, 5, 16, 10, 0, tzinfo=KST)  # Sat
    assert scheduler._is_market_hours(market_time) is True
    assert scheduler._is_market_hours(after_close) is False
    assert scheduler._is_market_hours(weekend) is False
    assert scheduler._is_market_hours(datetime(2026, 5, 14, 15, 30, 0, tzinfo=KST)) is False


def test_short_time_windows_and_conservative_gate():
    runner = _FakeRunner()
    scheduler = MultiThemeScheduler(trade_runner=runner)
    premarket = datetime(2026, 5, 14, 8, 10, tzinfo=KST)
    cons = datetime(2026, 5, 14, 9, 5, tzinfo=KST)
    block = datetime(2026, 5, 14, 15, 15, tzinfo=KST)

    assert scheduler._is_short_premarket_window(premarket) is True
    assert scheduler._is_conservative_entry_window(cons) is True
    assert scheduler._is_no_new_entry_window(block) is True

    ml, mc = scheduler._resolve_short_execution_gates(
        base_min_leader_score=None,
        base_min_confidence=None,
        now=cons,
    )
    assert ml == 80
    assert mc == 70


def test_scheduler_builds_long_plan_and_executes_trigger():
    runner = _FakeRunner()
    scheduler = MultiThemeScheduler(
        trade_runner=runner,
        short_interval_minutes=60,
        long_plan_time="08:00",
        long_plan_window_minutes=40,
        long_trigger_check_minutes=1,
        long_market_hours_only=False,
    )
    now = datetime(2026, 5, 14, 8, 5, tzinfo=KST)
    scheduler._build_long_plan(
        now=now,
        candidate_limit=3,
        per_theme_top_n=2,
        long_top_n=1,
        min_leader_score=None,
        min_confidence=None,
        max_risk_level=None,
        long_strategy_profile="long",
        include_theme_keys=None,
        exclude_theme_keys=None,
    )
    assert len(scheduler._long_candidates) == 1

    scheduler._check_long_triggers(
        now=now + timedelta(minutes=1),
        candidate_limit=3,
        per_theme_top_n=2,
        min_leader_score=None,
        min_confidence=None,
        max_risk_level=None,
        long_strategy_profile="long",
        include_theme_keys=None,
        exclude_theme_keys=None,
        execute=True,
    )
    # build + reevaluate + execute flow should invoke runner multiple times.
    assert runner.calls >= 3


def test_long_plan_build_window_blocks_night_execution():
    runner = _FakeRunner()
    scheduler = MultiThemeScheduler(
        trade_runner=runner,
        long_plan_time="08:00",
        long_plan_window_minutes=40,
    )
    night = datetime(2026, 5, 14, 22, 6, tzinfo=KST)
    morning = datetime(2026, 5, 14, 8, 10, tzinfo=KST)
    assert scheduler._should_build_long_plan(night) is False
    assert scheduler._should_build_long_plan(morning) is True


def test_resolve_buy_only_by_stage():
    runner = _FakeRunner()
    scheduler = MultiThemeScheduler(trade_runner=runner)
    now = datetime(2026, 5, 14, 10, 0, tzinfo=KST)
    assert scheduler._resolve_buy_only(stage="short_entry", now=now) is True
    assert scheduler._resolve_buy_only(stage="long_entry", now=now) is True
    assert scheduler._resolve_buy_only(stage="holding_review", now=now) is False
