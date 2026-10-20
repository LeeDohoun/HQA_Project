"""Integer coverage permission, rule independence and source-error boundaries."""
import copy

import pytest

from backtesting.experiments.qp003 import runner
from backtesting.experiments.qp003.selection import apply_permission, permission_summary


def selected(a, c, **kwargs):
    return {"precheck": {"month": "2023-12", "stage": "design", "A_count": a, "C_count": c,
        "C_over_A": c / a if a else None, "listing_complete": True,
        "data_complete": c == a, "link_failures": {"CFS:missing_quarter": a - c}, **kwargs}}


@pytest.mark.parametrize("a,c,permission,status", [(10, 9, True, "permitted"), (10, 8, False, "coverage_hold"),
    (1, 0, False, "coverage_hold"), (0, 0, False, "empty_A"), (100, 90, True, "permitted")])
def test_integer_threshold_empty_A_and_coverage_are_valid_data(a, c, permission, status):
    source = selected(a, c)
    before = copy.deepcopy(source)
    result = apply_permission(source)
    assert result["rebalance_permitted"] is permission
    assert result["no_trade"] is not permission
    assert result["decision_status"] == status
    assert result["data_complete"] and result["precheck"]["data_complete"]
    assert result["precheck"]["link_failures"] == before["precheck"]["link_failures"]
    assert source == before


def test_S_and_O_permissions_are_independent_and_not_intersected(tmp_path, monkeypatch):
    inputs = {"S": [selected(10, 8)], "O": [selected(10, 9)]}
    monkeypatch.setattr(runner.qp001, "prepare_selections", lambda *a, **k: {"selections": inputs})
    result = runner.prepare_selections(tmp_path, tmp_path, [], [], assets=None)
    assert result["selections"]["S"][0]["no_trade"]
    assert not result["selections"]["O"][0]["no_trade"]
    inputs = {"S": [selected(10, 9)], "O": [selected(10, 8)]}
    result = runner.prepare_selections(tmp_path, tmp_path, [], [], assets=None)
    assert not result["selections"]["S"][0]["no_trade"]
    assert result["selections"]["O"][0]["no_trade"]


@pytest.mark.parametrize("failure", ["listing", "selection", "invalid_counts", "integrity", "missing_source"])
def test_errors_abort_and_never_become_holds(tmp_path, monkeypatch, failure):
    row = selected(10, 8)
    if failure == "listing":
        row["precheck"]["listing_complete"] = False
    elif failure == "selection":
        row["precheck"].update(reason="selection_failed", failure_reasons=["source missing"])
    elif failure == "invalid_counts":
        row["precheck"]["C_count"] = None
    def prepare(*a, **k):
        if failure in ("integrity", "missing_source"):
            raise ValueError(failure)
        return {"selections": {r: [row] for r in ("S", "O")}}
    monkeypatch.setattr(runner.qp001, "prepare_selections", prepare)
    with pytest.raises(ValueError):
        runner.prepare_selections(tmp_path, tmp_path, [], [], assets=None)


def test_permission_summary_counts_longest_run_and_design_minimum_separately():
    rows = []
    for i, permitted in enumerate((True, False, False, True, False)):
        row = apply_permission(selected(10, 10 if permitted else 8))["precheck"]
        row.update(month=f"2023-{i + 1:02d}", stage="design" if i < 3 else "validation")
        rows.append(row)
    summary = permission_summary(rows)
    assert summary["permitted_design_decisions"] == 1
    assert summary["hold_months"] == ["2023-02", "2023-03", "2023-05"]
    assert summary["permitted_months"] == ["2023-01", "2023-04"]
    assert summary["longest_consecutive_hold"] == 2
