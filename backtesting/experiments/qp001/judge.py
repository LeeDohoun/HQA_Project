"""QP001 section 6 only: fixed M=167, no generic experiment judge."""
import math

import pandas as pd

from . import M, RULES, VALIDATION_END, VALIDATION_START


P_THRESHOLD = 0.05 / M
VALIDATION_MONTHS = {str(month) for month in pd.period_range(VALIDATION_START, VALIDATION_END, freq="M")}


def finite(value):
    return value is not None and math.isfinite(value)


def insufficiencies(metrics):
    reasons = []
    primary = metrics["design"]["primary"]
    if primary["count"] < 36:
        reasons.append("design_months_below_36")
    if not finite(primary["standard_error"]) or primary["standard_error"] <= 0:
        reasons.append("undefined_standard_error")
    if not metrics["data_complete"]:
        reasons.append("data_incomplete")
    if set(metrics["validation_months"]) != VALIDATION_MONTHS:
        reasons.append("missing_validation_months")
    if not finite(metrics["random_control_share"]):
        reasons.append("undefined_random_control")
    return reasons


def rule_judgement(metrics):
    missing = insufficiencies(metrics)
    if missing:
        return {"verdict": "insufficient", "reasons": missing}
    primary = metrics["design"]["primary"]
    tests = {
        "design_mean_not_positive": finite(primary["mean"]) and primary["mean"] > 0,
        "t_not_above_3": finite(primary["t_stat"]) and primary["t_stat"] > 3,
        "p_above_0.05_over_167": finite(primary["p_one_sided"]) and primary["p_one_sided"] <= P_THRESHOLD,
        "random_share_above_5_percent": metrics["random_control_share"] <= 0.05,
        "cost_1_5_mean_not_positive": finite(metrics["design_at_cost_1_5"]["primary"]["mean"])
            and metrics["design_at_cost_1_5"]["primary"]["mean"] > 0,
        "nav_x1_not_above_1": finite(metrics["design"]["nav_ratio"]) and metrics["design"]["nav_ratio"] > 1,
        "nav_x1_5_not_above_1": finite(metrics["design_at_cost_1_5"]["nav_ratio"])
            and metrics["design_at_cost_1_5"]["nav_ratio"] > 1,
        "validation_mean_not_positive": finite(metrics["validation"]["primary"]["mean"])
            and metrics["validation"]["primary"]["mean"] > 0,
    }
    reasons = [reason for reason, passed in tests.items() if not passed]
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons}


def judge(main, vanished, *, sensitivity_available):
    if set(main) != set(RULES) or set(vanished) != set(RULES):
        raise ValueError("QP001 judgement requires S and O, including the -100% recomputation")
    base = {rule: rule_judgement(main[rule]) for rule in RULES}
    loss = {rule: rule_judgement(vanished[rule]) for rule in RULES}
    p_values = [main[rule]["design"]["primary"]["p_one_sided"] for rule in RULES]
    worst_p = max(p_values) if all(finite(p) for p in p_values) else None
    loss_p = [vanished[rule]["design"]["primary"]["p_one_sided"] for rule in RULES]
    worst_loss_p = max(loss_p) if all(finite(p) for p in loss_p) else None
    # Ordered exactly as the preregistered status table. Rule passes remain
    # local; both passing is equivalent to using the worse S/O p-value.
    if any(v["verdict"] == "insufficient" for v in [*base.values(), *loss.values()]):
        verdict = "insufficient"
    elif not sensitivity_available:
        verdict = "sensitivity_unavailable"
    elif sum(v["verdict"] == "pass" for v in base.values()) == 1:
        verdict = "version_sensitive"
    elif all(v["verdict"] == "pass" for v in base.values()) and any(v["verdict"] != "pass" for v in loss.values()):
        verdict = "delisting_sensitive"
    elif all(v["verdict"] == "pass" for v in [*base.values(), *loss.values()]):
        verdict = "pass"
    else:
        verdict = "fail"
    return {"verdict": verdict, "rules": base, "vanished_rules": loss, "M": M,
            "p_threshold": P_THRESHOLD, "worse_p_one_sided": worst_p,
            "vanished_worse_p_one_sided": worst_loss_p, "sensitivity_available": sensitivity_available}
