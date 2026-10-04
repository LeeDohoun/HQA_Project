from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.portfolio.allocator import StrategyStats, allocate, decay_action


def strategy(code, *, vol=0.2, capacity=10_000_000, group=None, stage="small_live", ic=0.02, percentile=50, months=0):
    return StrategyStats(code, stage, vol, capacity, group or code, ic, percentile, months)


def check_caps(result, stats, capital, *, stock_cap=0.4, group_cap=0.6, target_vol=0.15):
    table = result["allocations"].set_index("strategy_id")
    risk = pd.Series({item.strategy_id: table.loc[item.strategy_id, "allocation_krw"] * item.realized_vol
                      for item in stats})
    assert table.allocation_krw.min() >= 0
    assert table.allocation_krw.sum() + result["unallocated_capital_krw"] == pytest.approx(capital)
    assert result["unallocated_capital_krw"] >= 0
    for item in stats:
        assert table.loc[item.strategy_id, "allocation_krw"] <= item.capacity_krw
    budget = capital * target_vol
    shares = risk / budget if budget else risk * 0
    assert risk.sum() <= budget + 1e-7
    assert shares.max() <= stock_cap + 1e-10
    for group in {item.correlation_group for item in stats}:
        assert sum(shares[item.strategy_id] for item in stats if item.correlation_group == group) <= group_cap + 1e-10
    assert table.risk_share.to_dict() == pytest.approx(shares.to_dict())
    assert result["total_risk_used_share"] == pytest.approx(shares.sum())


@pytest.mark.parametrize("capacity,amount,reason", [(10_000_000, 300_000, "max_risk_share"),
                                                  (100_000, 100_000, "capacity_krw")])
def test_one_strategy_uses_its_fixed_risk_budget_cap_and_leaves_cash(capacity, amount, reason):
    stats = [strategy("A", capacity=capacity)]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([amount])
    assert result["allocations"].risk_share.tolist() == pytest.approx([amount * 0.2 / 150_000])
    assert result["unallocated_capital_krw"] == pytest.approx(1_000_000 - amount)
    assert result["reasons"] == (reason,)
    check_caps(result, stats, 1_000_000)


def test_two_strategies_each_use_forty_percent_of_account_risk_budget():
    stats = [strategy("A"), strategy("B")]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([300_000, 300_000])
    assert result["allocations"].risk_share.tolist() == pytest.approx([0.4, 0.4])
    assert result["total_risk_used_share"] == pytest.approx(0.8)
    assert result["unallocated_capital_krw"] == pytest.approx(400_000)
    assert result["reasons"] == ("max_risk_share",)
    check_caps(result, stats, 1_000_000)


def test_five_uncapped_strategies_share_full_risk_budget_equally():
    stats = [strategy(code) for code in "ABCDE"]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([150_000] * 5)
    assert result["allocations"].risk_share.tolist() == pytest.approx([0.2] * 5)
    assert result["total_risk_used_share"] == pytest.approx(1)
    assert result["unallocated_capital_krw"] == pytest.approx(250_000)
    assert result["reasons"] == ("budget_fully_used",)
    check_caps(result, stats, 1_000_000)


def test_equal_risk_means_inverse_volatility_capital_weights():
    stats = [strategy("A", vol=0.1), strategy("B", vol=0.2), strategy("C", vol=0.4)]
    result = allocate(stats, 700_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([350_000, 175_000, 87_500])
    assert result["allocations"].risk_share.tolist() == pytest.approx([1 / 3] * 3)
    assert result["unallocated_capital_krw"] == pytest.approx(87_500)
    assert result["reasons"] == ("budget_fully_used",)
    check_caps(result, stats, 700_000)


@pytest.mark.parametrize("stage", ["hypothesis", "preregistered", "validation", "holdout", "shadow", "paper", "retired"])
def test_non_paper_passed_stages_never_receive_capital(stage):
    stats = [strategy("A"), strategy("B", stage="scaled"), strategy("C"), strategy("D", stage=stage, vol=0)]
    result = allocate(stats, 900_000)
    table = result["allocations"].set_index("strategy_id")
    assert table.loc["D", "allocation_krw"] == 0
    assert table.loc["D", "reasons"] == ("stage_not_eligible",)
    assert table.loc[["A", "B", "C"], "allocation_krw"].tolist() == pytest.approx([225_000] * 3)


def test_risk_cap_keeps_account_budget_denominator_after_capacity_reductions():
    stats = [strategy("A", capacity=100_000), strategy("B"), strategy("C")]
    result = allocate(stats, 1_000_000)
    table = result["allocations"]
    assert table.allocation_krw.tolist() == pytest.approx([100_000, 300_000, 300_000])
    assert table.risk_share.tolist() == pytest.approx([2 / 15, 0.4, 0.4])
    assert "max_risk_share" in table.reasons.iloc[1]
    assert result["unallocated_capital_krw"] == pytest.approx(300_000)
    assert result["reasons"] == ("max_risk_share", "capacity_krw")
    check_caps(result, stats, 1_000_000)


def test_group_cap_redistributes_risk_to_other_group():
    stats = [strategy("A", group="same"), strategy("B", group="same"), strategy("C")]
    result = allocate(stats, 1_000_000)
    table = result["allocations"]
    assert table.allocation_krw.tolist() == pytest.approx([225_000, 225_000, 300_000])
    assert table.risk_share.tolist() == pytest.approx([0.3, 0.3, 0.4])
    assert table.reasons.iloc[0] == ("group_risk_cap",)
    assert "max_risk_share" in table.reasons.iloc[2]
    check_caps(result, stats, 1_000_000)


def test_single_correlation_group_uses_group_cap_and_leaves_cash():
    stats = [strategy(code, group="same") for code in "ABC"]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([150_000] * 3)
    assert result["allocations"].risk_share.tolist() == pytest.approx([0.2] * 3)
    assert result["total_risk_used_share"] == pytest.approx(0.6)
    assert result["unallocated_capital_krw"] == pytest.approx(550_000)
    assert result["reasons"] == ("group_risk_cap",)
    check_caps(result, stats, 1_000_000)


def test_capacity_cap_redistributes_while_preserving_risk_caps():
    stats = [strategy("A", capacity=100_000), strategy("B"), strategy("C"), strategy("D")]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([100_000, 650_000 / 3, 650_000 / 3, 650_000 / 3])
    assert result["allocations"].reasons.iloc[0] == ("capacity_krw",)
    assert result["total_risk_used_share"] == pytest.approx(1)
    assert result["unallocated_capital_krw"] == pytest.approx(250_000)
    check_caps(result, stats, 1_000_000)


def test_all_capacity_exhausted_leaves_cash():
    stats = [strategy("A", capacity=100_000), strategy("B", capacity=100_000), strategy("C", capacity=100_000)]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([100_000] * 3)
    assert result["unallocated_capital_krw"] == pytest.approx(700_000)
    assert result["reasons"] == ("capacity_krw",)
    check_caps(result, stats, 1_000_000)


def test_combined_group_capacity_and_different_volatilities():
    stats = [strategy("A", vol=0.1, capacity=100_000, group="G"),
             strategy("B", vol=0.2, capacity=400_000, group="G"),
             strategy("C", vol=0.3, capacity=900_000, group="H"),
             strategy("D", vol=0.4, capacity=800_000, group="H"),
             strategy("E", vol=0.5, capacity=700_000, group="I")]
    result = allocate(stats, 1_000_000)
    check_caps(result, stats, 1_000_000)
    assert result["total_risk_used_share"] == pytest.approx(1)
    assert result["unallocated_capital_krw"] > 0


def test_zero_capacity_does_not_dilute_eligible_strategies_risk_budget():
    stats = [strategy("A", capacity=0), strategy("B"), strategy("C")]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([0, 300_000, 300_000])
    assert result["allocations"].reasons.iloc[0] == ("capacity_krw",)
    assert result["total_risk_used_share"] == pytest.approx(0.8)
    check_caps(result, stats, 1_000_000)


def test_explicitly_loosened_caps_allow_one_strategy():
    result = allocate([strategy("A")], 1_000_000, max_risk_share=1, group_risk_cap=1)
    assert result["allocations"].allocation_krw.iloc[0] == pytest.approx(750_000)
    assert result["allocations"].risk_share.iloc[0] == 1


@pytest.mark.parametrize("target_vol", [0.10, 0.25, 1.0])
def test_explicit_portfolio_vol_changes_risk_budget(target_vol):
    stats = [strategy("A", vol=1)]
    result = allocate(stats, 1_000_000, target_portfolio_vol=target_vol)
    assert result["allocations"].allocation_krw.iloc[0] == pytest.approx(0.4 * 1_000_000 * target_vol)
    assert result["total_risk_used_share"] == pytest.approx(0.4)
    check_caps(result, stats, 1_000_000, target_vol=target_vol)


def test_capital_limit_stops_equal_risk_filling_for_tiny_volatilities():
    stats = [strategy("A", vol=0.001), strategy("B", vol=0.002), strategy("C", vol=0.004)]
    result = allocate(stats, 700_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([400_000, 200_000, 100_000])
    assert result["allocations"].risk_share.tolist() == pytest.approx([400 / 105_000] * 3)
    assert result["total_risk_used_share"] == pytest.approx(1_200 / 105_000)
    assert result["unallocated_capital_krw"] == pytest.approx(0, abs=1e-7)
    assert result["reasons"] == ("capital_limit",)
    check_caps(result, stats, 700_000)


def test_capital_limit_after_capacity_cap_preserves_equal_uncapped_risk():
    stats = [strategy("A", vol=0.001, capacity=100_000), strategy("B", vol=0.001),
             strategy("C", vol=0.002)]
    result = allocate(stats, 1_000_000)
    assert result["allocations"].allocation_krw.tolist() == pytest.approx([100_000, 600_000, 300_000])
    assert result["unallocated_capital_krw"] == pytest.approx(0, abs=1e-7)
    assert "capital_limit" in result["reasons"]
    check_caps(result, stats, 1_000_000)


@pytest.mark.parametrize("stats", [[], [strategy("A", stage="paper")], [strategy("A", capacity=0)]])
def test_no_eligible_capacity_returns_explicit_cash_reason(stats):
    result = allocate(stats, 1_000_000)
    assert result["unallocated_capital_krw"] == 1_000_000
    assert result["reasons"] == ("no_eligible_capacity",)
    assert result["total_risk_used_share"] == 0


def test_zero_capital_and_input_order_do_not_change_stats():
    stats = [strategy("D", vol=0.4), strategy("C", group="same"),
             strategy("A", capacity=100_000, group="same"), strategy("B", group="same")]
    before = list(stats)
    first, second = allocate(stats, 1_000_000), allocate(list(reversed(stats)), 1_000_000)
    pd.testing.assert_frame_equal(first["allocations"], second["allocations"])
    assert first["unallocated_capital_krw"] == second["unallocated_capital_krw"]
    assert first["reasons"] == second["reasons"]
    assert first["total_risk_used_share"] == second["total_risk_used_share"]
    assert first["allocations"].strategy_id.tolist() == ["A", "B", "C", "D"]
    assert stats == before
    zero = allocate(stats, 0)
    assert zero["allocations"].allocation_krw.eq(0).all()
    assert zero["unallocated_capital_krw"] == 0
    assert zero["total_risk_used_share"] == 0
    assert zero["allocations"].risk_share.eq(0).all()


@pytest.mark.parametrize("ic,percentile,months,expected", [
    (-0.01, 50, 0, "halve"), (0.01, 4.99, 11, "halve"), (-0.01, 50, 12, "retire"),
    (0.01, 4.99, 12, "retire"), (-0.01, 1, 13, "retire"),
    (0, 5, 0, "keep"), (0.02, 50, 12, "keep"),
])
def test_decay_thresholds_and_consecutive_month_boundary(ic, percentile, months, expected):
    assert decay_action(strategy("A", ic=ic, percentile=percentile, months=months)) == expected


@pytest.mark.parametrize("kwargs", [{"vol": 0}, {"vol": np.nan}, {"capacity": -1}, {"group": " "}])
def test_invalid_strategy_inputs(kwargs):
    with pytest.raises(ValueError):
        allocate([strategy("A", **kwargs)], 1_000_000)


@pytest.mark.parametrize("kwargs", [{"total_capital_krw": -1}, {"max_risk_share": 0},
                                   {"max_risk_share": 1.1}, {"group_risk_cap": np.nan},
                                   {"target_portfolio_vol": 0}, {"target_portfolio_vol": -0.1},
                                   {"target_portfolio_vol": 1.1}, {"target_portfolio_vol": np.nan},
                                   {"target_portfolio_vol": np.inf}, {"target_portfolio_vol": True},
                                   {"target_portfolio_vol": "0.15"}])
def test_invalid_allocator_parameters(kwargs):
    with pytest.raises(ValueError):
        allocate([strategy("A")], **({"total_capital_krw": 1_000_000} | kwargs))


def test_duplicate_strategy_ids_are_rejected():
    with pytest.raises(ValueError):
        allocate([strategy("A"), strategy("A")], 1_000_000)


@pytest.mark.parametrize("changes", [{"rolling_ic_6m": np.nan}, {"realized_vs_expected_percentile": -1},
                                    {"realized_vs_expected_percentile": 101}, {"months_in_decay": -1},
                                    {"months_in_decay": 1.5}])
def test_invalid_decay_inputs(changes):
    with pytest.raises(ValueError):
        decay_action(replace(strategy("A"), **changes))
