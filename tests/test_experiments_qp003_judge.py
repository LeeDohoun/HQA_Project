"""Full-month minimums, pass boundaries and preregistered status precedence."""
import copy
import math

import pytest

from backtesting.experiments.qp003 import M, judge


def passing():
    return {"design": {"primary": {"count": 80, "mean": .01, "standard_error": .002,
        "t_stat": 5., "p_one_sided": .5 * math.erfc(5 / math.sqrt(2))}, "nav_ratio": 1.1},
        "design_at_cost_1_5": {"primary": {"mean": .009}, "nav_ratio": 1.05},
        "validation": {"primary": {"count": 23, "mean": .005}}, "random_control_share": .05,
        "design_months": list(judge.DESIGN_MONTHS), "validation_months": list(judge.VALIDATION_MONTHS),
        "permitted_design_decisions": 36, "evaluation_complete": True}


def pairs():
    main = {r: passing() for r in ("S", "O")}
    return main, copy.deepcopy(main)


def test_exact_36_permissions_and_44_holds_pass_and_fixed_M_worse_p():
    main, loss = pairs()
    main["O"]["design"]["primary"]["p_one_sided"] = judge.P_THRESHOLD
    result = judge.judge(main, loss, sensitivity_available=True)
    assert result["verdict"] == "pass"
    assert result["M"] == M == 168
    assert result["p_threshold"] == result["worse_p_one_sided"] == .05 / 168
    main["O"]["design"]["primary"]["p_one_sided"] = math.nextafter(judge.P_THRESHOLD, 1)
    assert judge.judge(main, loss, sensitivity_available=True)["verdict"] == "version_sensitive"


@pytest.mark.parametrize("criterion", ["t", "mean", "p", "random", "cost", "nav1", "nav1_5", "validation"])
def test_each_strict_and_inclusive_pass_boundary(criterion):
    metric = passing()
    if criterion == "t":
        metric["design"]["primary"]["t_stat"] = 3
    elif criterion in ("mean", "p"):
        metric["design"]["primary"]["mean" if criterion == "mean" else "p_one_sided"] = 0 if criterion == "mean" else .001
    elif criterion == "random":
        metric["random_control_share"] = .050001
    elif criterion == "cost":
        metric["design_at_cost_1_5"]["primary"]["mean"] = 0
    elif criterion in ("nav1", "nav1_5"):
        metric["design" if criterion == "nav1" else "design_at_cost_1_5"]["nav_ratio"] = 1
    else:
        metric["validation"]["primary"]["mean"] = 0
    assert judge.rule_judgement(metric)["verdict"] == "fail"


@pytest.mark.parametrize("condition", ["design", "design_count", "design_duplicate", "validation", "validation_count",
                                       "permissions", "se", "scenario", "control"])
@pytest.mark.parametrize("vanished", [False, True])
def test_insufficient_precedes_every_other_status(condition, vanished):
    main, loss = pairs()
    metric = (loss if vanished else main)["S"]
    if condition in ("design", "validation"):
        metric[condition + "_months"].pop()
    elif condition in ("design_count", "validation_count"):
        metric[condition.removesuffix("_count")]["primary"]["count"] -= 1
    elif condition == "design_duplicate":
        metric["design_months"][-1] = metric["design_months"][0]
    elif condition == "permissions":
        metric["permitted_design_decisions"] = 35
    elif condition == "se":
        metric["design"]["primary"]["standard_error"] = None
    elif condition == "scenario":
        metric["evaluation_complete"] = False
    else:
        metric["random_control_share"] = None
    assert judge.judge(main, loss, sensitivity_available=False)["verdict"] == "insufficient"


def test_sensitivity_version_delisting_fail_precedence_and_worse_loss_p():
    main, loss = pairs()
    main["O"]["validation"]["primary"]["mean"] = -1
    loss["S"]["design"]["primary"]["p_one_sided"] = .01
    assert judge.judge(main, loss, sensitivity_available=False)["verdict"] == "sensitivity_unavailable"
    assert judge.judge(main, loss, sensitivity_available=True)["verdict"] == "version_sensitive"
    main["O"] = passing()
    result = judge.judge(main, loss, sensitivity_available=True)
    assert result["verdict"] == "delisting_sensitive"
    assert result["vanished_worse_p_one_sided"] == .01
    main["S"]["design"]["primary"]["mean"] = -1
    main["O"]["design"]["primary"]["mean"] = -1
    assert judge.judge(main, loss, sensitivity_available=True)["verdict"] == "fail"
    with pytest.raises(ValueError, match="S and O"):
        judge.judge(main, {"S": loss["S"]}, sensitivity_available=True)
