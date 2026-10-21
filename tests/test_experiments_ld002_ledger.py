from copy import deepcopy
import math

import pytest

from backtesting.cost_model import one_way_cost
from backtesting.experiments.ld002 import Refused
from backtesting.experiments.ld002.ledger import equal_weights, initial_state, rebalance, targets
from ld002_fixtures import CODES, free_cost, output, portfolio, quote

A, B, C = CODES[:3]


def run(state, weights, quotes, *, adv=None, eligible=None, **kwargs):
    return rebalance(state, weights, quotes, dict.fromkeys(quotes, 1e11) if adv is None else adv,
        set(quotes) if eligible is None else eligible, "2026-11-16", cost_fn=kwargs.pop("cost_fn", free_cost), **kwargs)


def test_targets_conviction_denominator_cap_and_no_redistribution():
    answer = output([A, B, C], 25)
    answer["holdings"][0]["conviction"] = 3
    target = targets(answer)
    assert target["raw"] == {A: .15, B: .05, C: .05}
    assert target["weights"] == {A: .1, B: .05, C: .05}
    assert sum(target["weights"].values()) == .2
    assert targets(output([], 0))["weights"] == {}


@pytest.mark.parametrize("weight,expected", [(.09500001, 0), (.095, 50), (.09499999, 50)])
def test_minimum_trade_strict_half_percent_boundary(weight, expected):
    record = run(portfolio({A: (1000, 10000)}, 90e6), {A: weight}, {A: quote()})
    assert sum(t["quantity"] for t in record["trades"]) == expected


def test_limits_use_t_close_nav_instead_of_previous_close():
    state = portfolio({A: (5000, 10000)}, 50e6)
    record = run(state, {B: .1}, {A: quote(20000), B: quote()})
    assert record["pre_trade_nav"] == 150e6
    assert record["deltas_before_limits"] == {A: -100e6, B: 15e6}
    assert sum(abs(v) for v in record["deltas_after_limits"].values()) == pytest.approx(22.5e6)


def test_turnover_proportional_then_adv_only_for_that_stock_no_redistribution():
    record = run(initial_state(), {A: .1, B: .1}, {A: quote(), B: quote()}, adv={A: 1e8, B: 1e11})
    assert record["turnover_scale"] == .75
    assert record["deltas_after_limits"] == {A: 1e6, B: 7.5e6}
    assert {t["code"]: t["amount"] for t in record["trades"]} == {A: 1e6, B: 7.5e6}


def test_rounding_floors_every_stock_without_spending_residual():
    record = run(initial_state(), {A: .1, B: .1}, {A: quote(3e6), B: quote(4e6)})
    assert {t["code"]: t["quantity"] for t in record["trades"]} == {A: 2, B: 1}
    assert record["state"]["cash"] == 90e6


def test_quantity_just_below_one_share_is_never_rounded_up():
    state = portfolio({}, 999999.999999)
    record = run(state, {A: .1}, {A: quote(100000)})
    assert not record["trades"] and record["state"]["cash"] == state["cash"]


def test_delta_just_below_minimum_is_not_promoted_to_a_trade_by_tolerance():
    record = run(initial_state(), {A: .005 - 1e-17}, {A: quote()})
    assert not record["trades"]


@pytest.mark.parametrize("flag,side,blocked", [("limit_up", "buy", True), ("limit_down", "sell", True),
    ("halted", "buy", True), ("halted", "sell", True), ("limit_down", "buy", False), ("limit_up", "sell", False)])
def test_execution_block_sides(flag, side, blocked):
    state = initial_state() if side == "buy" else portfolio({A: (1000, 10000)}, 90e6)
    record = run(state, {A: .1} if side == "buy" else {}, {A: quote(**{flag: True})})
    assert bool(record["trades"]) != blocked


def test_sell_then_buy_uses_actual_net_proceeds_and_uniform_cash_scale():
    state = portfolio({A: (10000, 10000)}, 0)
    def expensive(*args, **kwargs):
        return .10
    record = run(state, {B: .1, C: .1}, {A: quote(), B: quote(), C: quote()},
        constrained=False, cost_fn=expensive)
    assert record["trades"][0]["side"] == "sell"
    assert record["state"]["cash"] >= 0
    assert record["cash_scale"] == 1
    # Blocked sales cannot finance buys, even if the planned sale was large.
    blocked = run(state, {B: .1, C: .1}, {A: quote(limit_down=True), B: quote(), C: quote()}, constrained=False)
    assert blocked["cash_scale"] == 0 and not blocked["trades"]


def test_cash_scaling_includes_buy_cost_and_does_not_reallocate_rounding():
    record = run(portfolio({}, 1e6), {A: .1, B: .1}, {A: quote(10000), B: quote(30000)},
                 constrained=False, cost_fn=lambda *a, **k: 5.0)
    assert record["cash_scale"] == pytest.approx(1e6 / 1.2e6)
    assert {t["code"]: t["quantity"] for t in record["trades"]} == {A: 8, B: 2}
    assert record["state"]["cash"] == 160000


def test_outside_universe_cannot_increase_but_can_sell_or_keep():
    state = portfolio({A: (100, 10000)}, 99e6)
    record = run(state, {A: .1}, {A: quote()}, eligible=set())
    assert not record["trades"] and record["ignored_increases"][A] == 9e6
    assert run(state, {}, {A: quote()}, eligible=set())["state"]["positions"] == {}


def test_minimum_is_applied_before_scaling_and_not_reapplied_after():
    record = run(portfolio({A: (9950, 10000)}, 500000), {A: 0, B: .005}, {A: quote(), B: quote()})
    assert record["deltas_before_limits"][B] == 500000
    assert 0 < record["deltas_after_limits"][B] < 500000
    assert any(t["code"] == B for t in record["trades"])


@pytest.mark.parametrize("missing_day", [False, True])
def test_missing_row_carries_price_but_trade_block_keeps_known_close_marks(missing_day):
    state = portfolio({A: (1000, 10000)}, 90e6)
    record = run(state, {}, {} if not missing_day else {A: quote(9000)}, missing_day=missing_day)
    assert record["state"]["nav"] == (99e6 if missing_day else 100e6) and not record["trades"]
    assert record["unresolved_value"] == (0 if missing_day else 10e6)
    assert record["state"]["positions"][A]["stale_calendar_days"] == (0 if missing_day else 3)


def test_halt_marks_last_price_and_resumption_clears_unresolved_value():
    state = portfolio({A: (1000, 10000)}, 90e6)
    halted = run(state, {}, {A: quote(1, halted=True)})
    assert halted["state"]["positions"][A]["last_close"] == 10000
    restored = rebalance(halted["state"], {A: .1}, {A: quote()}, {A: 1e11}, {A}, "2026-11-17", hold=True, cost_fn=free_cost)
    assert restored["unresolved_value"] == 0


def test_liquidation_ignores_minimum_and_turnover_but_keeps_adv_and_blocks():
    state = portfolio({A: (10000, 10000)}, 0)
    record = run(state, {}, {A: quote()}, liquidate=True, adv={A: 2e9})
    assert record["turnover"] == .2 and record["state"]["positions"][A]["quantity"] == 8000
    assert not run(state, {}, {A: quote(limit_down=True)}, liquidate=True)["trades"]
    tiny = run(portfolio({A: (1, 10000)}, 99.99e6), {}, {A: quote()}, liquidate=True)
    assert tiny["trades"][0]["quantity"] == 1


def test_missing_adv_never_uses_stale_or_mock_liquidity():
    record = run(initial_state(), {A: .1}, {A: quote()}, adv={})
    assert record["blocks"][A] == "adv_unavailable" and not record["trades"]


def test_all_cost_scenarios_recompute_quantities_using_v2_unscaled_tax():
    records = [run(initial_state(), equal_weights(CODES[:10]), {c: quote(10000) for c in CODES[:10]},
        constrained=False, cost_fn=one_way_cost, multiplier=m) for m in (1, 1.5, 2)]
    assert records[0]["trades"][0]["quantity"] > records[2]["trades"][0]["quantity"]
    for m, record in zip((1, 1.5, 2), records):
        assert record["trades"][0]["rate"] == one_way_cost(10000, "KOSPI", "20261116", 1e11, "buy", multiplier=m, model_version="v2")
    rates = [one_way_cost(10000, "KOSPI", "20261116", 1e11, "sell", multiplier=m, model_version="v2") for m in (1, 2)]
    assert rates[1] - 2 * rates[0] == pytest.approx(-.002)


def test_official_actions_change_raw_shares_and_cash_once():
    state = portfolio({A: (100, 10000)}, 99e6)
    event = {"id": "split", "type": "split", "code": A, "factor": 2, "official_source": "fixture", "effective_day": "2026-11-16"}
    record = run(state, {}, {A: quote(5000)}, actions=[event], hold=True)
    assert record["state"]["positions"][A]["quantity"] == 200
    assert record["state"]["nav"] == state["nav"]
    with pytest.raises(Refused, match="duplicate"):
        rebalance(record["state"], {}, {A: quote()}, {A: 1e10}, {A}, "2026-11-17",
                  actions=[{**event, "effective_day": "2026-11-17"}], hold=True, cost_fn=free_cost)


@pytest.mark.parametrize("kind", ["dividend", "rights", "recovery"])
def test_official_cash_rights_and_delisting_recovery(kind):
    state = portfolio({A: (100, 10000)}, 99e6)
    event = {"id": kind, "type": kind, "code": A, "cash_delta": 100000, "quantity_delta": 5,
        "avg_cost_after": 9000, "official_source": "fixture", "effective_day": "2026-11-16"}
    record = run(state, {}, {A: quote()}, actions=[event], hold=True)
    assert record["state"]["cash"] == 99.1e6
    assert (A in record["state"]["positions"]) == (kind != "recovery")


@pytest.mark.parametrize("weights", [{A: .10001}, {A: -1}, {A: float("nan")}])
def test_invalid_targets_fail_instead_of_borrowing(weights):
    with pytest.raises(Refused):
        run(initial_state(), weights, {A: quote()})


@pytest.mark.parametrize("unknown", [None, 0, "false"])
def test_unknown_execution_flags_never_become_permission_to_fill(unknown):
    with pytest.raises(Refused, match="explicitly verified"):
        run(initial_state(), {A: .1}, {A: quote(limit_up=unknown)})


def test_official_zero_recovery_extinguishes_equity_and_zero_cash_is_absorbing():
    state = portfolio({A: (100, 10000)}, 0)
    event = {"id": "zero-recovery", "type": "recovery", "code": A, "cash_delta": 0,
             "official_source": "fixture", "effective_day": "2026-11-16"}
    record = run(state, {}, {}, hold=True, actions=[event])
    assert record["state"]["nav"] == 0 and record["return"] == -1 and not record["state"]["positions"]
    next_day = rebalance(record["state"], {}, {}, {}, set(), "2026-11-17", hold=True, cost_fn=free_cost)
    assert next_day["state"]["nav"] == 0 and next_day["return"] == 0
