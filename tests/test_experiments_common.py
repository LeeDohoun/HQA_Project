from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import experiment_registry
from backtesting.experiments import common


EXPERIMENT_ID = "D001_momentum_lowvol"
NET_METRICS = {"net_metric": "net_excess", "net_at_cost_1_5_metric": "net_excess_at_cost_1_5"}
REGISTRATION = Path(__file__).resolve().parents[1] / "research/experiments" / EXPERIMENT_ID / "preregistration.md"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Offline Test")
    git(tmp_path, "config", "user.email", "offline@example.invalid")
    path = tmp_path / "research/experiments" / EXPERIMENT_ID / "preregistration.md"
    path.parent.mkdir(parents=True)
    path.write_text(REGISTRATION.read_text(encoding="utf-8"), encoding="utf-8")
    git(tmp_path, "add", "--", str(path.relative_to(tmp_path)))
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "Register synthetic experiment")
    return tmp_path, path, common.load_preregistration(EXPERIMENT_ID, repo_root=tmp_path)


def prices(days=25):
    dates = pd.bdate_range("2024-01-02", periods=days)
    codes = [f"{i:06d}" for i in (10, 25, 30, 40, 50, 60, 70)]
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    frame = pd.DataFrame({"stock_name": "보통주", "market": "KOSPI", "close": 1000.0,
                          "trading_value": 1e8, "calendar_status": "verified"}, index=index)
    return frame, dates


def inputs(**changes):
    return {"observations": 36, "t_stat": 2.1, "random_control_share": 0.05, "net": 0.01,
            "net_at_cost_1_5": 0.005, "net_excess": 0.002, "net_excess_at_cost_1_5": 0.001,
            "validation_year_same_sign": True, **changes}


def result_payload():
    return {"selected_variant": "combined", "verdict": "pass", "reasons": ["검증 사유"],
            "variants": {"combined": {"ic": {"count": 36, "mean": 0.1, "t_stat": 2.1},
                                      "cost_sensitivity": {str(m): {"top_net": 0.01, "top_net_excess": 0.002}
                                                           for m in (1.0, 1.5, 2.0)},
                                      "random_control": {"share_of_controls": 0.05}, "skipped_month_count": 0,
                                      "unadjusted_fallback": 2, "delisting_exclusions": 1}},
            "interpretations": {"note": "정의는 실제 결과를 보기 전(2026-10-05)에 확정",
                                "criteria": {"net_performance_gt": {"metric": "net_excess",
                                                                      "definition": "월별 순초과수익 평균"}}},
            "performance": {"runtime_seconds": 1.0, "peak_memory_mib": 120.0}, "assumptions": ["합성 데이터"]}


def test_load_committed_preregistration(repo):
    root, _, fields = repo
    assert common.load_preregistration(EXPERIMENT_ID, repo_root=root) == fields
    assert fields["variants_planned"] == 3


@pytest.mark.parametrize("change", ["untracked", "modified", "recommitted"])
def test_registration_failure_is_not_hidden(repo, change):
    root, path, _ = repo
    if change == "untracked":
        git(root, "rm", "--cached", "--", str(path.relative_to(root)))
    else:
        path.write_text(path.read_text(encoding="utf-8") + "変更\n", encoding="utf-8")
        if change == "recommitted":
            git(root, "add", "--", str(path.relative_to(root)))
            git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "Changed plan")
    with pytest.raises(ValueError):
        common.load_preregistration(EXPERIMENT_ID, repo_root=root)
    assert not (root / experiment_registry.REGISTRY_PATH).exists()


def test_universe_common_shares_price_liquidity_and_verified_history():
    frame, dates = prices()
    frame.loc[(slice(None), "000030"), "stock_name"] = "합성스팩"
    frame.loc[(dates[-1], "000040"), "close"] = 999
    frame.loc[(slice(None), "000050"), "trading_value"] = 0.99e8
    frame.loc[(dates[-10], "000060"), "trading_value"] = np.nan
    frame.loc[(dates[-10], "000070"), "calendar_status"] = "unverified_special_session"
    actual = common.universe_filter(frame, dates[-1])
    assert actual.index.tolist() == ["000010"]
    assert actual.loc["000010", "avg_trading_value_20d"] == 1e8


def test_universe_is_point_in_time_and_requires_the_exact_decision_row():
    frame, dates = prices()
    before = common.universe_filter(frame, dates[-2])
    changed = frame.copy()
    changed.loc[(dates[-1], slice(None)), ["close", "trading_value"]] = [999, 1e15]
    changed.loc[(dates[-1], slice(None)), "stock_name"] = "미래스팩"
    pd.testing.assert_frame_equal(before, common.universe_filter(changed, dates[-2]))
    missing = frame.drop((dates[-2], "000010"))
    assert "000010" not in common.universe_filter(missing, dates[-2]).index


@pytest.mark.parametrize("days,expected", [(19, 0), (20, 6)])
def test_universe_requires_twenty_complete_sessions(days, expected):
    frame, dates = prices(days)
    assert len(common.universe_filter(frame, dates[-1])) == expected


def test_unverified_old_history_outside_adv_window_does_not_exclude():
    frame, dates = prices()
    frame.loc[(dates[0], "000010"), "calendar_status"] = "unverified_special_session"
    assert "000010" in common.universe_filter(frame, dates[-1]).index


def test_missing_universe_column_fails_clearly():
    frame, dates = prices()
    with pytest.raises(ValueError, match="stock_name"):
        common.universe_filter(frame.drop(columns="stock_name"), dates[-1])


def test_month_end_and_next_shared_session_helpers():
    days = pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-28", "2024-02-29", "2024-03-01"])
    index = pd.MultiIndex.from_product([days, ["000010"]], names=["trade_date", "stock_code"])
    frame = pd.DataFrame({"close": 1000}, index=index)
    assert common.month_end_sessions(frame, "20240101", "20240229").tolist() == [days[1], days[4]]
    assert common.next_session(frame, days[1]) == days[2]
    assert common.next_session(frame, days[-1]) is None


def test_full_price_span_guard_rejects_holdout_even_when_decision_is_earlier():
    frame, _ = prices()
    future = frame.iloc[-1:].copy()
    future.index = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-01-01"), "000070")], names=frame.index.names)
    with pytest.raises(ValueError, match="holdout"):
        common.universe_filter(pd.concat([frame, future]), pd.Timestamp("2024-01-31"))


def test_judge_pass_includes_exact_random_control_boundary(repo):
    root, _, fields = repo
    verdict, reasons = common.judge(fields, inputs(), **NET_METRICS, repo_root=root)
    assert verdict == "pass"
    assert reasons


@pytest.mark.parametrize("changes,metric", [
    ({"net_excess": -0.01}, "net_excess"),
    ({"net_excess_at_cost_1_5": -0.01}, "net_excess_at_cost_1_5"),
])
def test_positive_absolute_net_cannot_override_runner_excess_criteria(repo, changes, metric):
    root, _, fields = repo
    values = inputs(**changes)
    assert values["net"] > 0 and values["net_at_cost_1_5"] > 0
    verdict, reasons = common.judge(fields, values, **NET_METRICS, repo_root=root)
    assert verdict == "fail"
    assert len(reasons) == 1
    assert metric in reasons[0]


def test_judge_requires_the_named_metric_instead_of_falling_back_to_absolute_net(repo):
    root, _, fields = repo
    values = inputs()
    del values["net_excess"]
    with pytest.raises(KeyError, match="net_excess"):
        common.judge(fields, values, **NET_METRICS, repo_root=root)


@pytest.mark.parametrize("changes,reason", [
    ({"t_stat": 2.0}, "t_stat"), ({"random_control_share": 0.055}, "random_control_share"),
    ({"net_excess": 0.0}, "net_excess must"), ({"net_excess_at_cost_1_5": 0.0}, "cost x1.5"),
    ({"validation_year_same_sign": False}, "validation-year"),
])
def test_judge_failed_criteria(repo, changes, reason):
    root, _, fields = repo
    verdict, reasons = common.judge(fields, inputs(**changes), **NET_METRICS, repo_root=root)
    assert verdict == "fail"
    assert any(reason in message for message in reasons)


@pytest.mark.parametrize("changes", [
    {"observations": 35}, {"t_stat": None}, {"net_excess": np.nan}, {"net_excess_at_cost_1_5": None},
    {"random_control_share": None}, {"validation_year_same_sign": None},
])
def test_judge_insufficient_observations_or_unmeasured_statistics(repo, changes):
    root, _, fields = repo
    verdict, reasons = common.judge(fields, inputs(**changes), **NET_METRICS, repo_root=root)
    assert verdict == "insufficient"
    assert reasons


def test_judge_uses_registry_threshold_and_impending_batch_count(repo):
    root, _, fields = repo
    assert common.judge(fields, inputs(t_stat=2.5, trial_count_after_run=21), **NET_METRICS, repo_root=root)[0] == "fail"
    for number in range(21):
        experiment_registry.record_trial(EXPERIMENT_ID, str(number), {}, "fail", repo_root=root)
    assert common.judge(fields, inputs(t_stat=3.0), **NET_METRICS, repo_root=root)[0] == "fail"
    assert common.judge(fields, inputs(t_stat=3.01), **NET_METRICS, repo_root=root)[0] == "pass"


def test_results_are_finite_korean_and_do_not_overwrite(repo):
    root, _, _ = repo
    payload = result_payload()
    first_json, first_md = common.write_results(EXPERIMENT_ID, payload, repo_root=root)
    before = first_json.read_bytes(), first_md.read_bytes()
    second_json, second_md = common.write_results(EXPERIMENT_ID, payload, repo_root=root)
    assert first_json != second_json and first_md != second_md
    assert (first_json.read_bytes(), first_md.read_bytes()) == before
    assert json.loads(first_json.read_text(encoding="utf-8")) == payload
    summary = first_md.read_text(encoding="utf-8")
    assert "실험 결과" in summary
    assert "## interpretations" in summary
    assert payload["interpretations"]["note"] in summary
    for criterion, interpretation in payload["interpretations"]["criteria"].items():
        assert criterion in summary
        assert interpretation["metric"] in summary
        assert interpretation["definition"] in summary
    assert "절대 순수익 (보조)" in summary
    assert first_json.stem.endswith("Z")


def test_result_timestamp_collision_is_explicit_and_preserves_files(repo, monkeypatch):
    root, _, _ = repo
    class FrozenTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(common, "datetime", FrozenTime)
    json_path, md_path = common.write_results(EXPERIMENT_ID, result_payload(), repo_root=root)
    before = json_path.read_bytes(), md_path.read_bytes()
    with pytest.raises(FileExistsError):
        common.write_results(EXPERIMENT_ID, result_payload(), repo_root=root)
    assert (json_path.read_bytes(), md_path.read_bytes()) == before


def test_nonfinite_result_is_rejected_before_writing(repo):
    root, path, _ = repo
    payload = result_payload()
    payload["invalid"] = float("nan")
    with pytest.raises(ValueError):
        common.write_results(EXPERIMENT_ID, payload, repo_root=root)
    assert not (path.parent / "results").exists()
