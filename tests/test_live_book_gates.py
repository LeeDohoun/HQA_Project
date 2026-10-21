from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src.live import book, gates, shadow
from test_live_fixtures import REPO, make_repo, write_ledger_entry, write_registry

REAL_BOOK_TEXT = (REPO / "config/strategy_book.yaml").read_text(encoding="utf-8")
_OVERRIDE = ('    requested_stage: paper\n    early_paper:\n      decided_by: "user"\n'
             '      decided_at: "2026-10-21T18:30:00+09:00"\n'
             '      reason: "모의투자이므로 섀도 20거래일·승인 없이 PAPER로 시작 (2026-10-21 사용자 결정)"\n')
assert REAL_BOOK_TEXT.count(_OVERRIDE) == 2
BOOK_TEXT = REAL_BOOK_TEXT.replace(_OVERRIDE, "    requested_stage: shadow\n")


def _book(tmp_path, text):
    path = tmp_path / "book.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_real_book_and_approvals_load():
    entries = {e.strategy_id: e for e in book.load_book(REPO / book.BOOK_PATH)}
    assert set(entries) == {"hf001_f_both", "av001_av_all", "pf001"}
    pf = entries.pop("pf001")
    hf, av = entries["hf001_f_both"], entries["av001_av_all"]
    assert (hf.execution, hf.cost_model_version, hf.band, hf.decision_rule) == ("basket_20", "v1", None, "f_both")
    assert hf.holdout_experiment_ids == ("HF001_hegemony_filters",) and hf.holdout_variants == ("holdout_f_both",)
    assert (av.execution, av.cost_model_version, av.band, av.decision_rule) == ("continuous", "v2", 0.001, "av_all")
    assert av.holdout_experiment_ids == ("AV002_avoidance_holdout_recovery", "AV001_avoidance_portfolio")
    assert all(e.requested_stage == "paper" and e.shadow_nav_krw == 100_000_000 and e.account_user_id is None
               and e.early_paper["decided_by"] == "user" and e.early_paper["decided_at"] == "2026-10-21T18:30:00+09:00"
               for e in entries.values())
    for entry in entries.values():
        assert (REPO / entry.preregistration).is_file()
    assert book.load_approvals(REPO / book.APPROVALS_PATH) == ()
    # pf001: the preregistration lives on the research branch; its path is a reference only.
    assert (pf.experiment_id, pf.variant, pf.execution, pf.t0) == (
        "OP001_combined_paper_portfolio", "pf001", "continuous_intraday", "2026-11-02")
    assert pf.preregistration == "research/experiments/OP001_combined_paper_portfolio/preregistration.md"
    assert pf.holdout_experiment_ids == () and pf.holdout_variants == ()
    assert [(s.name, s.weight, s.source) for s in pf.sleeves] == [
        ("av", 0.8, {"kind": "fw001", "rule": "av_all"}), ("bb", 0.2, None)]
    assert pf.traded_sleeve.name == "av" and pf.limits == {"max_adv_share": 0.01, "max_name_weight": 0.02}
    assert (pf.band, pf.cost_model_version, pf.requested_stage, pf.account_user_id) == (0.001, "v2", "paper", "are01")
    assert pf.early_paper == {"decided_by": "user", "decided_at": "2026-10-21T19:00:00+09:00",
                              "reason": "결합 포트폴리오 AV80+BB20, 섀도 없이 PAPER (사용자 결정)"}


@pytest.mark.parametrize("old, new, message", [
    ("    execution: basket_20\n", "    execution: basket_20\n    surprise: 1\n", "unknown keys"),
    ("requested_stage: shadow\n    shadow_nav_krw: 100000000\n    account_user_id: null\n    preregistration: "
     "research/experiments/HF001", "requested_stage: live\n    shadow_nav_krw: 100000000\n    account_user_id: null\n"
     "    preregistration: research/experiments/HF001", "unknown requested_stage"),
    ("execution: basket_20", "execution: daily", "unknown execution"),
    ("execution: basket_20\n", "execution: basket_20\n    band: 0.001\n", "band applies only"),
    ("    band: 0.001\n", "", "requires band"),
    ("cost_model_version: v1", "cost_model_version: v3", "unknown cost_model_version"),
    ("shadow_nav_krw: 100000000\n    account_user_id: null\n    preregistration: research/experiments/HF001",
     "shadow_nav_krw: -1\n    account_user_id: null\n    preregistration: research/experiments/HF001",
     "shadow_nav_krw"),
    ("rule: f_both", "rule: f_vol", "unknown decision_source.rule"),
    ("kind: fw001\n      rule: f_both", "kind: fw002\n      rule: f_both", "unknown decision_source.kind"),
    ("  av001_av_all:", "  hf001_f_both:", "duplicate key"),
])
def test_book_rejects_invalid_entries(tmp_path, old, new, message):
    assert old in BOOK_TEXT
    with pytest.raises(book.BookError, match=message):
        book.load_book(_book(tmp_path, BOOK_TEXT.replace(old, new, 1)))


def test_book_rejects_missing_file_and_wrong_top_level(tmp_path):
    with pytest.raises(book.BookError, match="missing file"):
        book.load_book(tmp_path / "absent.yaml")
    with pytest.raises(book.BookError, match="exactly one key"):
        book.load_book(_book(tmp_path, "strategies: {}\nextra: 1\n"))
    with pytest.raises(book.BookError, match="invalid YAML"):
        book.load_book(_book(tmp_path, "strategies: [\n"))


def test_approvals_validation(tmp_path):
    good = ("approvals:\n  - strategy_id: hf001_f_both\n    stage: paper\n    approved_by: tester\n"
            "    approved_at: '2026-12-01T09:00:00+09:00'\n    evidence: ledger ok\n")
    path = tmp_path / "a.yaml"
    path.write_text(good, encoding="utf-8")
    (approval,) = book.load_approvals(path)
    assert approval.strategy_id == "hf001_f_both" and approval.approved_at.utcoffset() is not None
    for bad, message in [(good.replace("+09:00", ""), "timezone"), (good.replace("stage: paper", "stage: shadow"),
                         "unknown stage"), (good.replace("evidence: ledger ok", "evidence: ok\n    note: x"), "keys"),
                         (good.replace("'2026-12-01T09:00:00+09:00'", "2026-12-01"), "time and timezone")]:
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(book.BookError, match=message):
            book.load_approvals(path)


def _entries():
    """The real book's strategies without the early-PAPER override, for the gate rules themselves."""
    return {e.strategy_id: dataclasses.replace(e, requested_stage="shadow", early_paper=None)
            for e in book.load_book(REPO / book.BOOK_PATH)}


def test_current_reality_both_strategies_observe(tmp_path):
    """Real registry (read-only): no strategy has passed its holdout as of 2026-10-17."""
    results = {sid: gates.evaluate_stage(entry, repo_root=REPO, data_dir=tmp_path)
               for sid, entry in _entries().items()}
    hf, av = results["hf001_f_both"], results["av001_av_all"]
    assert (hf.requested, hf.effective, hf.allowed) == ("shadow", "observe", "observe")
    assert hf.reasons == ("holdout_not_passed:HF001_hegemony_filters/holdout_f_both:none",)
    assert hf.evidence["design"]["verdict"] == "pass"
    assert av.effective == "observe"
    assert av.reasons == ("holdout_not_passed:AV001_avoidance_portfolio/holdout_av_all:holdout_aborted",)


def test_design_failure_and_missing_registry_fail_closed(tmp_path):
    entry = _entries()["hf001_f_both"]
    root = make_repo(tmp_path, rows=[("2026-10-17T00:00:00+00:00", "HF001_hegemony_filters", "f_both", "pass"),
                                     ("2026-10-18T00:00:00+00:00", "HF001_hegemony_filters", "f_both", "fail"),
                                     ("2026-10-19T00:00:00+00:00", "HF001_hegemony_filters", "holdout_f_both",
                                      "pass")])
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=tmp_path / "data")
    assert result.effective == "observe"
    assert result.reasons == ("design_not_passed:HF001_hegemony_filters/f_both:fail",)
    (root / gates.REGISTRY_PATH).unlink()
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=tmp_path / "data")
    assert result.effective == "observe" and result.reasons[0].startswith("registry_unreadable:")
    (root / gates.REGISTRY_PATH).write_text("garbage,header\n1,2\n", encoding="utf-8")
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=tmp_path / "data")
    assert result.effective == "observe" and result.reasons[0].startswith("registry_unreadable:")


def test_holdout_latest_row_across_experiments(tmp_path):
    entry = _entries()["av001_av_all"]
    design = ("2026-10-17T11:54:26+00:00", "AV001_avoidance_portfolio", "av_all", "pass")
    root = make_repo(tmp_path, rows=[design,
                                     ("2026-10-17T12:52:56+00:00", "AV001_avoidance_portfolio", "holdout_av_all",
                                      "holdout_aborted"),
                                     ("2026-10-20T00:00:00+00:00", "AV002_avoidance_holdout_recovery",
                                      "holdout_av_all", "pass")])
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=tmp_path / "data")
    assert (result.allowed, result.effective, result.reasons) == ("shadow", "shadow", ())
    assert result.evidence["holdout"]["experiment_id"] == "AV002_avoidance_holdout_recovery"
    write_registry(root, [design, ("2026-10-20T00:00:00+00:00", "AV002_avoidance_holdout_recovery",
                                   "holdout_av_all", "pass"),
                          ("2026-10-21T00:00:00+00:00", "AV001_avoidance_portfolio", "holdout_av_all", "fail")])
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=tmp_path / "data")
    assert result.reasons == ("holdout_not_passed:AV001_avoidance_portfolio/holdout_av_all:fail",)


APPROVAL = ("approvals:\n  - strategy_id: hf001_f_both\n    stage: paper\n    approved_by: tester\n"
            "    approved_at: '2026-12-01T09:00:00+09:00'\n    evidence: ledger ok\n")
PASSING = [("2026-10-17T00:00:00+00:00", "HF001_hegemony_filters", "f_both", "pass"),
           ("2026-10-18T00:00:00+00:00", "HF001_hegemony_filters", "holdout_f_both", "pass")]


def _paper_setup(tmp_path, sessions=21, approvals=APPROVAL, start="2026-11-02", **kwargs):
    entry = dataclasses.replace(_entries()["hf001_f_both"], requested_stage="paper")
    root = make_repo(tmp_path, rows=PASSING, approvals=approvals)
    data = tmp_path / "data"
    days = shadow.sessions_between(start, "2026-12-30")[:sessions]
    for day in days:
        write_ledger_entry(data, entry.strategy_id, day, **kwargs)
    return entry, root, data, days


def test_paper_allowed_with_full_evidence(tmp_path):
    entry, root, data, days = _paper_setup(tmp_path)
    assert (days[0], days[-1]) == ("2026-11-02", "2026-11-30")
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=data)
    assert (result.allowed, result.effective, result.reasons) == ("paper", "paper", ())
    assert result.evidence["shadow"] == {"sessions": 21, "first_date": "2026-11-02", "last_date": "2026-11-30",
                                         "calendar_days": 28, "incidents": 0, "mismatched_entries": 0,
                                         "independent_failed": 0, "independent_unavailable": 0}


def test_paper_needs_twenty_sessions_and_28_days(tmp_path):
    entry, root, data, days = _paper_setup(tmp_path, sessions=20)
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=data)
    assert result.effective == "shadow"
    assert result.reasons == ("shadow_calendar_days_insufficient:25/28",)
    entry, root, data, _ = _paper_setup(tmp_path / "b", sessions=19)
    reasons = gates.evaluate_stage(entry, repo_root=root, data_dir=data).reasons
    assert "shadow_sessions_insufficient:19/20" in reasons


def test_paper_blocked_by_incident_mismatch_approval_and_gap(tmp_path):
    entry, root, data, days = _paper_setup(tmp_path, incidents=["price_file_missing:x"])
    assert gates.evaluate_stage(entry, repo_root=root, data_dir=data).reasons == ("shadow_incidents:21",)
    entry, root, data, _ = _paper_setup(tmp_path / "m", match=False)
    assert gates.evaluate_stage(entry, repo_root=root, data_dir=data).reasons == ("shadow_signal_mismatch:21",)
    entry, root, data, _ = _paper_setup(tmp_path / "a", approvals="approvals: []\n")
    assert gates.evaluate_stage(entry, repo_root=root, data_dir=data).reasons == ("paper_approval_missing",)
    entry, root, data, _ = _paper_setup(tmp_path / "g")
    (shadow.ledger_path(data, entry.strategy_id, days[10])).unlink()
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=data)
    assert result.effective == "shadow" and result.evidence["shadow"]["sessions"] == 10
    entry, root, data, _ = _paper_setup(tmp_path / "o")
    write_ledger_entry(data, entry.strategy_id, days[5], mode="observe")
    assert gates.evaluate_stage(entry, repo_root=root, data_dir=data).evidence["shadow"]["sessions"] == 15
    entry, root, data, _ = _paper_setup(tmp_path / "i")
    shadow.record_incident(data, entry.strategy_id, days[3], "ledger_conflict", "differs")
    assert gates.evaluate_stage(entry, repo_root=root, data_dir=data).reasons == ("shadow_incidents:1",)


def test_garbled_ledger_or_approvals_fail_closed(tmp_path):
    entry, root, data, days = _paper_setup(tmp_path)
    shadow.ledger_path(data, entry.strategy_id, days[0]).write_text("{", encoding="utf-8")
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=data)
    assert result.effective == "shadow" and result.reasons[0].startswith("shadow_ledger_unreadable:")
    entry, root, data, _ = _paper_setup(tmp_path / "b", approvals="approvals: {}\n")
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=data)
    assert result.effective == "shadow" and result.reasons[0].startswith("approvals_unreadable:")


def test_requested_observe_has_no_reasons(tmp_path):
    entry = dataclasses.replace(_entries()["hf001_f_both"], requested_stage="observe")
    result = gates.evaluate_stage(entry, repo_root=REPO, data_dir=tmp_path)
    assert (result.effective, result.reasons) == ("observe", ())


def test_registry_reader_is_strict(tmp_path):
    path = write_registry(tmp_path, [("2026-10-17T00:00:00", "X", "v", "pass")])
    with pytest.raises(ValueError, match="timezone"):
        gates.read_registry(path)
    assert Path(path).is_file()


@pytest.mark.parametrize("check, reason", [
    (True, None), (False, "shadow_independent_check_failed:1"),
    ("unavailable", "shadow_independent_check_unavailable:1"), ("absent", "shadow_independent_check_unavailable:1"),
    ("not_applicable", None),
])
def test_paper_requires_independent_fw001_checks_on_trading_entries(tmp_path, check, reason):
    entry, root, data, days = _paper_setup(tmp_path)
    path = shadow.ledger_path(data, entry.strategy_id, days[0])
    record = json.loads(path.read_text(encoding="utf-8"))
    record["decision_hashes"] = ["h" * 64]
    if check != "absent":
        record["match"]["independent_check"] = check
    path.write_text(json.dumps(record), encoding="utf-8")
    result = gates.evaluate_stage(entry, repo_root=root, data_dir=data)
    assert result.reasons == (() if reason is None else (reason,))
    assert result.effective == ("paper" if reason is None else "shadow")


def test_early_paper_override_reaches_paper_and_keeps_unmet_gates_visible(tmp_path):
    """The real book (2026-10-21 user decision): PAPER before the holdout and shadow evidence."""
    entries = {e.strategy_id: e for e in book.load_book(REPO / book.BOOK_PATH)}
    hf = gates.evaluate_stage(entries["hf001_f_both"], repo_root=REPO, data_dir=tmp_path)
    assert (hf.requested, hf.allowed, hf.effective) == ("paper", "paper", "paper")
    assert hf.reasons[0] == "early_paper_override"
    assert "gate_bypassed:holdout_not_passed:HF001_hegemony_filters/holdout_f_both:none" in hf.reasons
    assert "gate_bypassed:paper_approval_missing" in hf.reasons
    assert hf.evidence["early_paper"]["reason"].startswith("모의투자")
    av = gates.evaluate_stage(entries["av001_av_all"], repo_root=REPO, data_dir=tmp_path)
    assert av.effective == "paper" and any(r.startswith("gate_bypassed:holdout_not_passed:AV001") for r in av.reasons)


@pytest.mark.parametrize("old, new, message", [
    ('      decided_by: "user"\n', "", "early_paper must have exactly"),
    ('"2026-10-21T18:30:00+09:00"', '"2026-10-21T18:30:00"', "timezone"),
    ('"2026-10-21T18:30:00+09:00"', '"yesterday"', "ISO 8601"),
    ("    requested_stage: paper\n    early_paper:", "    requested_stage: shadow\n    early_paper:",
     "requires requested_stage paper"),
])
def test_early_paper_record_is_strict(tmp_path, old, new, message):
    assert old in REAL_BOOK_TEXT
    with pytest.raises(book.BookError, match=message):
        book.load_book(_book(tmp_path, REAL_BOOK_TEXT.replace(old, new, 1)))


def test_full_evidence_without_override_has_no_override_marks(tmp_path):
    entry = _entries()["hf001_f_both"]
    decision = gates.compute_stage(dataclasses.replace(entry, requested_stage="observe"), registry_rows=[], ledger=[],
                                   approvals=())
    assert "early_paper_override" not in decision.reasons and "early_paper" not in decision.evidence
