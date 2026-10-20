"""Fixed QP001 family and the ordered status table."""
import copy
import math

import pytest

from backtesting.experiments import av001
from backtesting.experiments.qp001 import M
from backtesting.experiments.qp001 import judge, portfolio


def passing():
    return {"design": {"primary": {"count": 80, "mean": .01, "standard_error": .002,
        "t_stat": 5., "p_one_sided": .5 * math.erfc(5 / math.sqrt(2))}, "nav_ratio": 1.1},
        "design_at_cost_1_5": {"primary": {"mean": .009}, "nav_ratio": 1.05},
        "validation": {"primary": {"mean": .005}}, "random_control_share": .05,
        "data_complete": True, "validation_months": sorted(judge.VALIDATION_MONTHS)}


def pairs():
    main = {rule: passing() for rule in ("S", "O")}
    return main, copy.deepcopy(main)


def test_M_fixed_and_worse_p_sealed_without_holm_or_trial_count():
    main, loss = pairs()
    main["O"]["design"]["primary"]["p_one_sided"] = judge.P_THRESHOLD
    result = judge.judge(main, loss, sensitivity_available=True)
    assert result["verdict"] == "pass"
    assert M == result["M"] == 167
    assert result["p_threshold"] == .05 / 167
    assert result["worse_p_one_sided"] == judge.P_THRESHOLD
    main["O"]["design"]["primary"]["p_one_sided"] = math.nextafter(judge.P_THRESHOLD, 1)
    assert judge.judge(main, loss, sensitivity_available=True)["verdict"] == "version_sensitive"


@pytest.mark.parametrize("criterion", ["t", "mean", "p", "random", "cost", "nav1", "nav1_5", "validation"])
def test_each_strict_and_inclusive_pass_boundary(criterion):
    metrics = passing()
    if criterion == "t":
        metrics["design"]["primary"]["t_stat"] = 3
    elif criterion in ("mean", "p"):
        metrics["design"]["primary"]["mean" if criterion == "mean" else "p_one_sided"] = 0 if criterion == "mean" else .001
    elif criterion == "random":
        metrics["random_control_share"] = .050001
    elif criterion == "cost":
        metrics["design_at_cost_1_5"]["primary"]["mean"] = 0
    elif criterion in ("nav1", "nav1_5"):
        metrics["design" if criterion == "nav1" else "design_at_cost_1_5"]["nav_ratio"] = 1
    else:
        metrics["validation"]["primary"]["mean"] = 0
    assert judge.rule_judgement(metrics)["verdict"] == "fail"


@pytest.mark.parametrize("condition", ["months", "se", "coverage", "validation"])
def test_insufficient_precedes_unavailable_version_and_delisting(condition):
    main, loss = pairs()
    metric = main["S"]
    if condition == "months":
        metric["design"]["primary"]["count"] = 35
    elif condition == "se":
        metric["design"]["primary"]["standard_error"] = None
    elif condition == "coverage":
        metric["data_complete"] = False
    else:
        metric["validation_months"].pop()
    assert judge.judge(main, loss, sensitivity_available=False)["verdict"] == "insufficient"
    if condition == "coverage":
        assert "data_incomplete" in judge.rule_judgement(metric)["reasons"]


def test_sensitivity_unavailable_precedes_version_and_delisting():
    main, loss = pairs()
    main["O"]["validation"]["primary"]["mean"] = -1
    assert judge.judge(main, loss, sensitivity_available=False)["verdict"] == "sensitivity_unavailable"
    assert judge.judge(main, loss, sensitivity_available=True)["verdict"] == "version_sensitive"


def test_vanished_recomputation_must_keep_all_five_criteria_and_both_rules():
    main, loss = pairs()
    loss["O"]["random_control_share"] = .055
    result = judge.judge(main, loss, sensitivity_available=True)
    assert result["verdict"] == "delisting_sensitive"
    main["S"]["design"]["primary"]["mean"] = -.01
    main["O"]["design"]["primary"]["mean"] = -.01
    assert judge.judge(main, loss, sensitivity_available=True)["verdict"] == "fail"


def test_nav_criterion_is_end_over_start_not_positive_arithmetic_mean():
    rows = [{"month": f"2023-{month:02d}", "valid": True, "return": ret, "benchmark": 0,
             "excess": ret, "return_denominator": beginning, "end_value": ending}
            for month, ret, beginning, ending in ((1, .6, 1., 1.6), (2, -.5, 1.6, .8))]
    result = portfolio.summary(rows)
    assert result["mean_return"] > 0
    assert result["nav_ratio"] == .8
    values = passing()
    values["design"]["nav_ratio"] = result["nav_ratio"]
    assert "nav_x1_not_above_1" in judge.rule_judgement(values)["reasons"]


def test_calendar_NW_does_not_compress_missing_months_or_apply_small_sample_correction():
    result = av001.newey_west(["2023-01", "2023-03", "2023-04", "2023-08"], [.1, -.1, .2, .05])
    assert result["lag_pair_counts"] == {"1": 1, "2": 1, "3": 1}
    assert result["small_sample_correction"] is False
