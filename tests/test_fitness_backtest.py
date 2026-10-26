"""Fitness backtest: point-in-time copy, shared execution rules and guarded subscription calls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backtesting.fitness import metrics
from backtesting.fitness.pit_data import (_document_observed, _financial_observed, _write_jsonl,
                                          frozen_analysis_now, merge_prices, observe_documents)
from backtesting.fitness.replay import (CallCache, CallGuard, CallLog, CallRefused, GuardedModel, UsageLimitStop,
                                        in_blocked_hours)
from backtesting.fitness.simulate import (CLOSE_SLOT, OPEN_SLOT, Group, Plan, PricePanel, Simulator,
                                          plan_from_trading_plan)
from src.runner import analysis_data
from src.runner.analysis_contracts import SpecialistResult

KST = timezone(timedelta(hours=9))
DAYS = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07"]


def panel(bars: dict[str, list[tuple]]) -> PricePanel:
    values = {(code, day): tuple(map(float, bar)) for code, rows in bars.items() for day, bar in zip(DAYS, rows)}
    return PricePanel(values, {code: code for code in bars}, {code: "t" for code in bars})


def buy_once(plan: Plan):
    state = {"done": False}

    def decide(view):
        if state["done"]:
            return []
        state["done"] = True
        return [plan]

    return decide


def run(bars, plan, *, cost_bps=0.0, cash=1_000_000.0, decide=None, days=None):
    simulator = Simulator(panel(bars), cost_bps=cost_bps, initial_cash=cash)
    return simulator.run(decide or buy_once(plan), start=DAYS[0], end=DAYS[-1], decision_days=days or [DAYS[0]])


def flat(price, n=5, volume=1000):
    return [(price, price, price, price, volume)] * n


def test_buy_fills_at_the_open_inside_its_band_and_stops_out_at_the_stop_price():
    bars = {"000001": [(100, 101, 99, 100, 1000), (100, 100, 94, 95, 1000)] + flat(95, 3)}
    plan = Plan("000001", "BUY", size_pct=20.0, entry=[Group("e", [("current_price", "<=", 101), ("current_price", ">=", 99)])],
                exits=[Group("stop", [("current_price", "<=", 96.0)])], hard_stop=96.0)
    result = run(bars, plan)
    assert [(t["day"], t["side"], t["price"], t["quantity"]) for t in result["trades"]] == [
        ("2026-07-01", "BUY", 100.0, 2000), ("2026-07-02", "SELL", 96.0, 2000)]
    assert result["closed"][0]["return"] == pytest.approx(-0.04)


def test_a_gap_through_the_stop_fills_at_the_open():
    bars = {"000001": [(100, 100, 100, 100, 1000), (90, 92, 88, 91, 1000)] + flat(91, 3)}
    result = run(bars, Plan("000001", "BUY", size_pct=10.0, exits=[Group("stop", [("current_price", "<=", 95.0)])]))
    assert result["trades"][-1]["price"] == 90.0 and result["trades"][-1]["reason"] == "exit:stop:open"


def test_stop_comes_before_target_on_one_bar_unless_the_open_is_past_the_target():
    both = {"000001": [(100, 100, 100, 100, 1000), (100, 111, 94, 105, 1000)] + flat(105, 3)}
    plan = Plan("000001", "BUY", size_pct=10.0, exits=[Group("stop", [("current_price", "<=", 95.0)]),
                                                     Group("tp", [("current_price", ">=", 110.0)])])
    assert run(both, plan)["trades"][-1]["price"] == 95.0
    gap_up = {"000001": [(100, 100, 100, 100, 1000), (112, 113, 94, 100, 1000)] + flat(100, 3)}
    assert run(gap_up, plan)["trades"][-1]["price"] == 112.0


def test_partial_take_profit_then_planned_exit_at_the_close():
    bars = {"000001": [(100, 100, 100, 100, 1000), (100, 106, 99, 104, 1000), (104, 104, 103, 103, 1000)] + flat(103, 2)}
    plan = Plan("000001", "BUY", size_pct=20.0, reduces=[Group("tp1", [("current_price", ">=", 105.0)], 0.5)],
                exit_at=("2026-07-03", CLOSE_SLOT))
    trades = run(bars, plan)["trades"]
    assert [(t["day"], t["quantity"], t["price"], t["reason"]) for t in trades[1:]] == [
        ("2026-07-02", 1000, 105.0, "reduce:tp1"), ("2026-07-03", 1000, 103.0, "planned_exit")]


def test_an_open_outside_the_entry_band_leaves_the_plan_unfilled():
    bars = {"000001": [(105, 106, 98, 100, 1000)] + flat(100, 4)}
    plan = Plan("000001", "BUY", size_pct=10.0, entry=[Group("e", [("current_price", "<=", 101)])])
    result = run(bars, plan)
    assert result["trades"] == [] and result["events"][0]["event"] == "buy_open_outside_entry"


def test_sizing_caps_at_twenty_percent_whole_shares_and_charges_costs():
    bars = {"000001": flat(300)}
    result = run(bars, Plan("000001", "BUY", size_pct=35.0), cost_bps=15.0)
    buy = result["trades"][0]
    assert buy["quantity"] == 666  # floor(200,000 / 300)
    assert result["curve"][0]["cash"] == pytest.approx(1_000_000 - 666 * 300 * 1.0015)


def test_a_held_plan_never_lowers_its_stop_or_postpones_its_exit():
    bars = {"000001": [(100, 100, 100, 100, 1000), (100, 100, 100, 100, 1000), (100, 100, 92, 93, 1000)] + flat(93, 2)}
    first = Plan("000001", "BUY", size_pct=10.0, exits=[Group("stop", [("current_price", "<=", 95.0)])], hard_stop=95.0,
                 exit_at=("2026-07-06", CLOSE_SLOT))
    lower = Plan("000001", "HOLD", exits=[Group("stop", [("current_price", "<=", 90.0)])], hard_stop=90.0,
                 exit_at=("2026-07-07", CLOSE_SLOT))
    plans = iter([[first], [lower]])
    result = run(bars, None, decide=lambda view: next(plans, []), days=DAYS[:2])
    assert result["events"][0]["event"] == "plan_update_kept_old_stop"
    assert result["trades"][-1]["price"] == 95.0


def test_rm_plan_times_map_to_daily_bar_slots():
    plan = {"stock_code": "000001", "action": "BUY", "position_size_pct": 5.0, "confidence": 50, "risk_level": "HIGH",
            "entry_price": 100.0, "stop_loss_price": 93.0, "take_profit_price": 110.0,
            "planned_exit_at": "2026-10-23T06:15:00Z",
            "condition_payload": {"entry_conditions": [], "reduce_conditions": [],
                                  "exit_conditions": [{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": 93.0}]}],
                                  "invalidation_conditions": [{"id": "x", "all": [{"field": "current_price", "operator": "<=", "value": 94.0}]}]}}
    converted = plan_from_trading_plan(plan)
    assert converted.exit_at == ("2026-10-23", CLOSE_SLOT) and converted.hard_stop == 94.0
    assert plan_from_trading_plan({**plan, "planned_exit_at": "2026-10-23T08:00:00Z"}).exit_at == ("2026-10-24", OPEN_SLOT)
    assert plan_from_trading_plan({**plan, "planned_exit_at": "2026-10-22T23:30:00Z"}).exit_at == ("2026-10-23", OPEN_SLOT)


def kis_bar(day: str, close: float, collected: str) -> dict:
    return {"stock_code": "000001", "timestamp": f"{day}T00:00:00", "open": close, "high": close, "low": close,
            "close": close, "volume": 10,
            "metadata": {"source": "kis", "price_basis": "unadjusted", "trade_date": day,
                         "bar_at": f"{day}T15:30:00+09:00", "collected_at": collected}}


def test_backfilled_bars_are_seen_at_their_close_and_daily_bars_keep_their_real_revisions():
    rows, report = merge_prices([[kis_bar("2025-03-04", 100, "2026-10-11T00:00:00+00:00"),
                                  kis_bar("2026-10-13", 4445, "2026-10-13T15:17:00+00:00"),
                                  kis_bar("2026-10-13", 4165, "2026-10-14T15:22:00+00:00")]])
    seen = {(r["metadata"]["trade_date"], r["close"]): r["metadata"]["collected_at"] for r in rows}
    assert seen[("2025-03-04", 100)] == "2025-03-04T06:30:00+00:00"
    assert seen[("2026-10-13", 4445)] == "2026-10-13T15:17:00+00:00"
    assert seen[("2026-10-13", 4165)] == "2026-10-14T15:22:00+00:00"
    assert report["realtime_observed_bars"] == 1 and len(report["revised_bars"]) == 1


def test_documents_and_statements_are_seen_the_day_after_their_date():
    news = _document_observed({"published_at": "2026-07-01T08:00:00+09:00", "metadata": {}})
    assert news["metadata"]["collected_at"] == "2026-07-02T00:30:00+09:00"  # not at 07-01 09:15
    dart = _document_observed({"published_at": "2026-07-01", "metadata": {"published_at": "2026-07-01"}})
    assert dart["collected_at"] == "2026-07-02T00:30:00+09:00"
    statement = _financial_observed({"metadata": {"rcept_no": "20250311001085"}})["metadata"]
    assert (statement["published_at"], statement["collected_at"]) == ("2025-03-11T00:00:00+09:00",
                                                                      "2025-03-12T00:30:00+09:00")


def test_the_loader_clock_is_frozen_only_inside_the_call():
    at = datetime(2025, 3, 4, 0, 15, tzinfo=timezone.utc)
    original = analysis_data.datetime
    with frozen_analysis_now(at):
        assert analysis_data.datetime.now(timezone.utc) == at
    assert analysis_data.datetime is original


class FakeInner:
    model_name, reasoning_effort, max_tokens, hqa_input_limit = "claude-opus-5-5", "low", 4000, 12000

    def __init__(self, error=None):
        self.calls, self.error = 0, error

    def with_structured_output(self, schema, include_raw=False, method=None, strict=None):
        inner = self

        class Runnable:
            def invoke(self, messages):
                inner.calls += 1
                if inner.error:
                    raise inner.error
                parsed = SpecialistResult(stock_code="000001", role="chartist", score=61.0, confidence=55, thesis="t",
                                          risks=[], data_gaps=[], citations=[{"source_id": "s", "claim": "c"}])
                raw = SimpleNamespace(content=parsed.model_dump_json(), usage_metadata={"input_tokens": 9},
                                      response_metadata={"duration_ms": 5})
                return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return Runnable()


MESSAGES = [("system", "You are the chartist specialist"), ("human", json.dumps({"stock_code": "000001"}))]


def test_a_repeated_request_is_answered_from_the_cache(tmp_path):
    inner, guard = FakeInner(), CallGuard(max_calls=5, allow_market_hours=True)
    model = GuardedModel(inner, "chartist", CallCache(tmp_path / "c.sqlite3"), guard, CallLog())
    first = model.with_structured_output(SpecialistResult).invoke(MESSAGES)
    again = GuardedModel(inner, "chartist", CallCache(tmp_path / "c.sqlite3"), guard, CallLog())
    assert again.with_structured_output(SpecialistResult).invoke(MESSAGES) == first
    assert inner.calls == 1 and guard.calls == 1


def test_a_usage_limit_stops_every_later_call(tmp_path):
    class LLMBudgetExceeded(Exception):
        pass

    inner, guard = FakeInner(LLMBudgetExceeded("Claude plan usage limit reached")), CallGuard(5, allow_market_hours=True)
    model = GuardedModel(inner, "chartist", CallCache(tmp_path / "c.sqlite3"), guard, CallLog())
    with pytest.raises(UsageLimitStop):
        model.with_structured_output(SpecialistResult).invoke(MESSAGES)
    inner.error = None
    with pytest.raises(CallRefused):
        model.with_structured_output(SpecialistResult).invoke([MESSAGES[0], ("human", "{}")])
    assert inner.calls == 1


def test_calls_are_refused_in_krx_hours_and_beyond_the_cap(tmp_path):
    assert in_blocked_hours(datetime(2026, 10, 19, 10, 0, tzinfo=KST))
    assert not in_blocked_hours(datetime(2026, 10, 19, 16, 0, tzinfo=KST))
    assert not in_blocked_hours(datetime(2026, 10, 18, 10, 0, tzinfo=KST))  # Sunday
    guard = CallGuard(max_calls=1, allow_market_hours=True)
    guard.admit("chartist")
    with pytest.raises(CallRefused):
        guard.admit("chartist")


def test_summary_metrics_on_a_known_curve():
    curve = [{"day": d, "equity": e, "invested": 0.0, "cash": e, "positions": 0}
             for d, e in zip(DAYS, [100.0, 110.0, 99.0, 99.0, 108.9])]
    summary = metrics.summarize({"curve": curve, "closed": [], "trades": [], "initial_cash": 100.0}, DAYS[0], DAYS[-1])
    assert summary["total_return_pct"] == pytest.approx(8.9)
    assert summary["mdd_pct"] == pytest.approx(-10.0)


def test_a_round_trip_on_the_first_day_counts_from_the_starting_cash():
    # Bought at the first open and stopped out the same day: the run ends below its starting cash
    # although its first close and its last close are equal.
    bars = {"000001": [(100, 100, 90, 92, 1000)] + flat(92, 4)}
    plan = Plan("000001", "BUY", size_pct=20.0, exits=[Group("stop", [("current_price", "<=", 95.0)])])
    result = run(bars, plan, cost_bps=15.0)
    final = result["curve"][-1]["equity"]
    assert final == result["curve"][0]["equity"] < result["initial_cash"]
    summary = metrics.summarize(result, DAYS[0], DAYS[-1])
    expected = (final / result["initial_cash"] - 1) * 100
    assert summary["total_return_pct"] == pytest.approx(round(expected, 2))
    assert summary["mdd_pct"] == pytest.approx(round(expected, 2))
    assert summary["trades"] == 1 and summary["sharpe"] is not None


def test_later_revisions_keep_their_collection_time_so_they_never_conflict():
    rows = observe_documents([
        {"source_type": "news", "url": "u", "published_at": "2026-07-01T08:00:00+09:00",
         "metadata": {"collected_at": "2026-09-27T09:00:00+00:00"}},
        {"source_type": "news", "url": "u", "published_at": "2026-07-01T08:00:00+09:00",
         "metadata": {"collected_at": "2026-10-10T09:00:00+00:00"}}])
    assert [row["metadata"]["collected_at"] for row in rows] == ["2026-07-02T00:30:00+09:00",
                                                                 "2026-10-10T09:00:00+00:00"]


def test_written_rows_keep_their_key_order_for_dart_structured_field_checks(tmp_path):
    path = tmp_path / "rows.jsonl"
    _write_jsonl(path, [{"metadata": {"structured_row": {"z": 1, "a": 2}}}])
    assert list(json.loads(path.read_text())["metadata"]["structured_row"]) == ["z", "a"]


def test_a_day_that_lost_calls_is_discarded_and_a_day_without_room_is_not_started():
    from backtesting.fitness.cli import LLMArm, StopRun

    guard = CallGuard(max_calls=100, allow_market_hours=True, token_budget=1_000_000)

    class Runner:
        accounts = SimpleNamespace(state=None)
        target = {"userId": "dohoon"}
        specialists = {}

        def run(self, day):
            guard.refused += 1  # a call refused inside the live service, which swallows it
            return {}

    arm = LLMArm(Runner(), panel({"000001": flat(100)}), None, guard=guard)
    view = SimpleNamespace(day=DAYS[0], positions={}, opens={})
    with pytest.raises(StopRun, match="during"):
        arm(view)
    guard.tokens = 900_000
    with pytest.raises(StopRun, match="before"):
        arm(view)
    assert guard.room_for_day(1, 10, 1) is None
    guard.deadline = datetime.now(KST) - timedelta(minutes=1)
    with pytest.raises(CallRefused, match="deadline"):
        guard.admit("chartist")


def test_equal_payloads_in_any_key_order_share_one_cache_entry(tmp_path):
    from backtesting.fitness.replay import request_key

    settings = {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 16000, "input_limit": 128000}
    first = [("system", "s"), ("human", json.dumps({"specialists": {"analyst": 1, "quant": 2}}))]
    second = [("system", "s"), ("human", json.dumps({"specialists": {"quant": 2, "analyst": 1}}))]
    assert request_key("risk_manager", settings, SpecialistResult, first) == request_key(
        "risk_manager", settings, SpecialistResult, second)
    # Answers stored under the first keys (text as sent) are still found and copied over.
    inner, guard = FakeInner(), CallGuard(max_calls=5, allow_market_hours=True)
    cache = CallCache(tmp_path / "c.sqlite3")
    legacy = request_key("chartist", {"model": "claude-opus-5-5", "effort": "low", "max_tokens": 4000,
                                      "input_limit": 12000}, SpecialistResult, MESSAGES, canonical=False)
    answer = SpecialistResult(stock_code="000001", role="chartist", score=42.0, confidence=50, thesis="t", risks=[],
                              data_gaps=[], citations=[{"source_id": "s", "claim": "c"}])
    cache.put(legacy, "chartist", answer.model_dump_json(), {})
    model = GuardedModel(inner, "chartist", cache, guard, CallLog())
    assert model.with_structured_output(SpecialistResult).invoke(MESSAGES).score == 42.0 and inner.calls == 0


def test_a_plan_segment_may_not_reach_into_paper_hours(tmp_path):
    from backtesting.fitness.nightly import load_plan

    path = tmp_path / "plan.json"
    segment = {"id": "s", "start": "2026-10-21T00:30:00+09:00", "deadline": "2026-10-21T03:30:00+09:00",
               "budget_tokens": 1}
    path.write_text(json.dumps({"segments": [segment], "jobs": []}))
    assert load_plan(path)["segments"][0]["id"] == "s"
    for start, deadline in (("2026-10-21T02:00:00+09:00", "2026-10-21T03:31:00+09:00"),
                            ("2026-10-21T15:00:00+09:00", "2026-10-21T17:00:00+09:00")):
        path.write_text(json.dumps({"segments": [{**segment, "start": start, "deadline": deadline}], "jobs": []}))
        with pytest.raises(ValueError, match="03:30-15:45"):
            load_plan(path)


def test_a_segment_counts_only_paid_answers_recorded_inside_its_window(tmp_path):
    from backtesting.fitness.nightly import spent

    cache = CallCache(tmp_path / "c.sqlite3")
    rows = [("a", {"usage": {"input_tokens": 100, "output_tokens": 10}}, "2026-10-20T10:30:00+00:00"),
            ("b", {"usage": {"input_tokens": 200, "output_tokens": 20}}, "2026-10-20T11:30:00+00:00"),
            ("c", {"imported_from": "audit"}, "2026-10-20T10:40:00+00:00")]
    import sqlite3
    with sqlite3.connect(tmp_path / "c.sqlite3") as db:
        for key, meta, created in rows:
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?)", (key, "chartist", created, "{}", json.dumps(meta)))
    start = datetime(2026, 10, 20, 19, 15, tzinfo=KST)
    assert spent(tmp_path / "c.sqlite3", start, start + timedelta(hours=1)) == (1, 110)
