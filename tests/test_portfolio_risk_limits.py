from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from src.portfolio.risk_limits import AccountState, check_new_entry, check_order, kill_switch


def account(**changes):
    return replace(AccountState(1_000_000, 1_000_000, 1_000_000, {}, {}, 0), **changes)


@pytest.mark.parametrize("now,start,peak,reasons,review", [
    (1_000_000, 1_000_000, 1_000_000, (), False),
    (980_000, 1_000_000, 1_000_000, (), False),
    (979_999, 1_000_000, 1_000_000, ("daily_loss_limit",), False),
    (900_001, 900_001, 1_000_000, (), False),
    (900_000, 900_000, 1_000_000, ("mdd_stop",), False),
    (850_001, 850_001, 1_000_000, ("mdd_stop",), False),
    (850_000, 850_000, 1_000_000, ("mdd_stop", "full_review_required"), True),
    (800_000, 1_000_000, 1_000_000, ("daily_loss_limit", "mdd_stop", "full_review_required"), True),
    (0, 1_000_000, 1_000_000, ("daily_loss_limit", "mdd_stop", "full_review_required"), True),
])
def test_daily_loss_drawdown_and_review_boundaries(now, start, peak, reasons, review):
    decision = check_new_entry(account(equity_now=now, equity_start_of_day=start, equity_peak=peak))
    assert decision.allowed == (not reasons)
    assert decision.reasons == reasons
    assert decision.full_review_required == review


def test_daily_loss_uses_start_equity_and_drawdown_uses_peak():
    state = account(equity_now=970_000, equity_start_of_day=1_000_000, equity_peak=1_100_000)
    assert check_new_entry(state).reasons == ("daily_loss_limit", "mdd_stop")
    assert check_new_entry(state, daily_loss_limit=0.04, mdd_stop=0.12).allowed


def test_review_flag_is_independent_of_configured_drawdown_stop():
    decision = check_new_entry(account(equity_now=850_000, equity_start_of_day=850_000), mdd_stop=0.2)
    assert decision.full_review_required
    assert decision.reasons == ("full_review_required",)
    assert decision.allowed


@pytest.mark.parametrize("kwargs", [{"equity_now": np.nan}, {"equity_start_of_day": 0}, {"equity_peak": -1}])
def test_invalid_equity_is_not_silently_allowed(kwargs):
    with pytest.raises(ValueError):
        check_new_entry(account(**kwargs))


@pytest.mark.parametrize("kwargs", [{"daily_loss_limit": -1}, {"mdd_stop": np.nan}, {"mdd_stop": 1.1}])
def test_invalid_new_entry_limits(kwargs):
    with pytest.raises(ValueError):
        check_new_entry(account(), **kwargs)


@pytest.mark.parametrize("orders,total,reasons", [
    (4, 99, ()), (5, 99, ("max_orders_per_stock_per_day",)),
    (4, 100, ("max_orders_per_day",)),
    (5, 100, ("max_orders_per_stock_per_day", "max_orders_per_day")),
])
def test_next_order_count_boundaries(orders, total, reasons):
    state = account(orders_today_by_stock={"A": orders}, orders_today_total=total)
    decision = check_order(state, "A", order_qty=1)
    assert decision.reasons == reasons
    assert decision.allowed == (not reasons)


@pytest.mark.parametrize("orders,cancels,allowed", [(0, 0, True), (4, 2, True), (4, 3, False)])
def test_cancel_ratio_is_per_stock_and_uses_history_before_new_order(orders, cancels, allowed):
    state = account(orders_today_by_stock={"A": orders, "B": 20},
                    cancels_today_by_stock={"A": cancels, "B": 20}, orders_today_total=orders + 20)
    decision = check_order(state, "A", order_qty=1)
    assert decision.allowed == allowed
    assert decision.reasons == (() if allowed else ("max_cancel_ratio",))


@pytest.mark.parametrize("qty,volume,allowed", [(20, 100, True), (21, 100, False),
                                             (1, 0, False), (1_000, None, True)])
def test_order_size_relative_to_minute_volume(qty, volume, allowed):
    decision = check_order(account(), "A", order_qty=qty, avg_minute_volume=volume)
    assert decision.allowed == allowed
    assert decision.reasons == (() if allowed else ("max_qty_vs_minute_volume",))


def test_all_order_rejections_are_reported_and_checks_are_pure():
    state = account(orders_today_by_stock={"A": 5}, cancels_today_by_stock={"A": 4}, orders_today_total=100)
    orders, cancels = dict(state.orders_today_by_stock), dict(state.cancels_today_by_stock)
    decision = check_order(state, "A", order_qty=21, avg_minute_volume=100)
    assert decision.reasons == ("max_orders_per_stock_per_day", "max_cancel_ratio", "max_orders_per_day",
                                "max_qty_vs_minute_volume")
    check_new_entry(state)
    assert state.orders_today_by_stock == orders
    assert state.cancels_today_by_stock == cancels
    assert state.orders_today_total == 100


@pytest.mark.parametrize("kwargs", [{"order_qty": 0}, {"order_qty": 1.5}, {"order_qty": True},
                                   {"avg_minute_volume": -1}, {"avg_minute_volume": np.nan},
                                   {"max_cancel_ratio": 1.1}, {"max_orders_per_day": 0},
                                   {"max_qty_vs_minute_volume": -1}])
def test_invalid_order_inputs(kwargs):
    with pytest.raises(ValueError):
        check_order(account(), "A", **({"order_qty": 1} | kwargs))


@pytest.mark.parametrize("changes", [{"orders_today_total": -1}, {"orders_today_by_stock": {"A": -1}},
                                    {"cancels_today_by_stock": {"A": 1}},
                                    {"orders_today_by_stock": {"A": 2}, "orders_today_total": 1}])
def test_invalid_order_history(changes):
    with pytest.raises(ValueError):
        check_order(account(**changes), "A", order_qty=1)


def test_kill_switch_reads_operator_file_and_does_not_create_it(tmp_path):
    path = tmp_path / "manual-stop"
    assert not kill_switch(path)
    assert not path.exists()
    path.write_text("stop", encoding="utf-8")
    assert kill_switch(str(path))
    path.unlink()
    assert not kill_switch(path)
