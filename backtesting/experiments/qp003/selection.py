"""QP001 selection with QP003's integer, per-rule permission test."""


def apply_permission(selection):
    monthly = selection["precheck"]
    if monthly.get("reason") == "selection_failed" or not monthly["listing_complete"]:
        raise ValueError(f"QP003 selection/listing error in {monthly['month']}: {monthly.get('failure_reasons')}")
    a, c = monthly["A_count"], monthly["C_count"]
    if type(a) is not int or type(c) is not int or not 0 <= c <= a:
        raise ValueError("QP003 requires valid A_t and C_t counts; source errors cannot become holds")
    permitted = a > 0 and 10 * c >= 9 * a
    reason = None if permitted else "coverage_hold" if a else "empty_A"
    monthly = {**monthly, "rebalance_permitted": permitted, "decision_status": reason or "permitted",
               "data_complete": True, "reason": reason, "failure_reasons": []}
    return {**selection, "precheck": monthly, "rebalance_permitted": permitted,
            "decision_status": monthly["decision_status"], "no_trade": not permitted, "data_complete": True}


def permission_summary(rows):
    longest = current = 0
    for row in rows:
        current = 0 if row["rebalance_permitted"] else current + 1
        longest = max(longest, current)
    return {"months": len(rows),
            "permitted_months": [r["month"] for r in rows if r["rebalance_permitted"]],
            "hold_months": [r["month"] for r in rows if not r["rebalance_permitted"]],
            "coverage_hold_months": [r["month"] for r in rows if r["decision_status"] == "coverage_hold"],
            "empty_A_months": [r["month"] for r in rows if r["decision_status"] == "empty_A"],
            "permitted_design_decisions": sum(r["rebalance_permitted"] for r in rows if r["stage"] == "design"),
            "longest_consecutive_hold": longest,
            "minimum_C_over_A": min((r["C_over_A"] for r in rows if r["C_over_A"] is not None), default=None)}
