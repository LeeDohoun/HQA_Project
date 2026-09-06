import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.runner.corporate_actions import assess_price_basis, build_corporate_action_context


AS_OF = datetime(2026, 9, 5, 3, tzinfo=timezone.utc)


def document(source_id="original", receipt="20260904000001", **metadata):
    text = "Verified disclosure narrative."
    return {"source_id": source_id, "source_type": "dart",
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            "title": "\uc8fc\uc694\uc0ac\ud56d\ubcf4\uace0\uc11c(\ubb34\uc0c1\uc99d\uc790\uacb0\uc815)",
            "text": text, "source_text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "truncated": False, "published_at": "2026-09-04T00:00:00+09:00",
            "published_at_precision": "date", "available_at": "2026-09-04T10:00:00+09:00",
            "metadata": {"rcept_no": receipt, "structured_rcept_no": receipt,
                "structured_endpoint": "fricDecsn", "structured_body_error_type": "structured_too_short",
                "structured_row": {"rcept_no": receipt, "nstk_asstd": "2026-09-15",
                                   "nstk_lstprd": "2026-09-30"}, **metadata}}


def codes(context):
    return {risk["code"] for risk in context["risks"]}


def test_official_fields_are_distinct_from_ex_dates_with_full_provenance():
    doc = document()
    doc["metadata"]["structured_row"].update(nstk_dlprd="2026.09.29", bddd="20260904",
        nstk_dividrk="2026\ub144 1\uc6d4 1\uc77c", nstk_ascnt_ps_ostk="1.0", ex_date="2026-09-14")
    before = deepcopy(doc)
    context = build_corporate_action_context([doc], AS_OF)
    assert doc == before
    dates = {event["date_kind"]: event["date"] for event in context["events"]}
    assert dates == {"record_date": "2026-09-15", "new_share_delivery_date": "2026-09-29",
                     "expected_listing_date": "2026-09-30", "board_decision_date": "2026-09-04",
                     "dividend_accrual_date": "2026-01-01"}
    assert context["coverage"] == "disclosed_events_only"
    assert context["price_adjustment_status"] == "unverified"
    assert len(context["upcoming_events"]) == 3
    assert "as_of" not in context
    for event in context["events"]:
        assert event["status"] == "disclosed"
        assert event["action_type"] == "bonus_issue"
        assert event["source_ids"] == ["original"]
        assert event["sources"] == [{key: doc[key] for key in (
            "source_id", "url", "published_at_precision")} | {"published_at": "2026-09-03T15:00:00+00:00",
                "available_at": "2026-09-04T01:00:00+00:00", "rcept_no": doc["metadata"]["rcept_no"]}]
    assert "price_basis_review" in codes(context)
    assert all("adjustment_factor" not in event for event in context["events"])


@pytest.mark.parametrize("value", [20260915, True, "20260915.0", "2026-02-30", "2026-9-5",
    "09/15", "2026-09-15T09:00:00+09:00", "2026-09-15 ~ 2026-09-17", "2026-09-15 (\uc608\uc815)",
    "2026-09-15\uc6d0", {"date": "2026-09-15"}, None, "-", ""])
def test_dates_do_not_accept_amount_units_partial_dates_or_inferred_ranges(value):
    doc = document()
    doc["metadata"]["structured_row"] = {"rcept_no": doc["metadata"]["rcept_no"],
                                         "nstk_asstd": value, "nstk_ostk_cnt": "1,000"}
    context = build_corporate_action_context([doc], AS_OF)
    assert context["events"] == []
    assert "calendar_dates_unavailable" in codes(context)
    assert "corporate_action_dates_unavailable:original" in context["data_gaps"]


@pytest.mark.parametrize("changed", ["rcept_no", "structured_rcept_no", "row_receipt", "structured_endpoint"])
def test_mismatched_structured_evidence_cannot_supply_dates(changed):
    doc = document()
    if changed == "row_receipt":
        doc["metadata"]["structured_row"]["rcept_no"] = "20260903000002"
    else:
        doc["metadata"][changed] = "piicDecsn" if changed == "structured_endpoint" else "20260903000002"
    context = build_corporate_action_context([doc], AS_OF)
    assert context["events"] == []
    assert "invalid_structured_evidence" in codes(context)


@pytest.mark.parametrize("title,endpoint", [
    ("\uc8fc\uc694\uc0ac\ud56d\ubcf4\uace0\uc11c(\uc720\uc0c1\uc99d\uc790\uacb0\uc815)", "piicDecsn"),
    ("\uc8fc\uc2dd\ubd84\ud560\uacb0\uc815", None), ("\uc8fc\uc2dd\ubcd1\ud569\uacb0\uc815", None),
    ("\ud604\uae08\u318d\ud604\ubb3c\ubc30\ub2f9\uacb0\uc815", None),
])
def test_unsupported_calendar_fields_and_prose_are_review_only(title, endpoint):
    doc = document(structured_endpoint=endpoint)
    doc["title"] = title
    doc["text"] = "\ubc30\ub2f9\ub77d 2026-09-15; listing 2026-09-30"
    context = build_corporate_action_context([doc], AS_OF)
    assert context["events"] == []
    assert "calendar_dates_unavailable" in codes(context)


def test_future_documents_are_excluded_even_when_action_date_is_past():
    doc = document()
    doc["published_at"] = doc["available_at"] = (AS_OF + timedelta(days=1)).isoformat()
    doc["metadata"]["structured_row"]["nstk_asstd"] = "2026-09-01"
    context = build_corporate_action_context([doc], AS_OF)
    assert context["events"] == context["risks"] == []


def test_as_of_must_be_aware_and_invalid_publication_order_is_not_hidden():
    with pytest.raises(ValueError, match="aware"):
        build_corporate_action_context([], AS_OF.replace(tzinfo=None))
    doc = document()
    doc["published_at"] = (AS_OF + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="before publication"):
        build_corporate_action_context([doc], AS_OF)


def test_linked_correction_replaces_changed_date_and_resolves_original_flag():
    original = document(has_correction=True)
    correction = document("correction", "20260905000002", is_correction=True,
                          supersedes_source_ids=["original"])
    correction["published_at"] = correction["available_at"] = "2026-09-05T09:00:00+09:00"
    correction["metadata"]["structured_row"]["nstk_asstd"] = "2026-09-21"
    context = build_corporate_action_context([original, correction], AS_OF)
    records = [event for event in context["events"] if event["date_kind"] == "record_date"]
    assert [(event["date"], event["status"]) for event in records] == [
        ("2026-09-15", "superseded"), ("2026-09-21", "disclosed")]
    assert all(event["source_ids"] == ["correction"] for event in context["upcoming_events"])
    assert "subsequent_correction_unresolved" not in codes(context)


@pytest.mark.parametrize("flag", ["is_correction", "is_withdrawal", "has_correction"])
def test_unlinked_changes_do_not_leave_original_calendar_authoritative(flag):
    original = document()
    changed = document("change", "20260905000002", **{flag: True})
    changed["published_at"] = changed["available_at"] = "2026-09-05T09:00:00+09:00"
    context = build_corporate_action_context([original, changed], AS_OF)
    assert context["upcoming_events"] == []
    assert all(event["status"] in {"unresolved", "withdrawn"} for event in context["events"])


def test_old_unlinked_correction_cannot_clear_a_pending_action_by_age_or_later_report():
    old = document("old-correction", "20260101000001", is_correction=True)
    old["published_at"] = old["available_at"] = "2026-01-01T09:00:00+09:00"
    later = document("later", "20260904000003", is_correction=True,
                     supersedes_source_ids=["old-correction"])
    context = build_corporate_action_context([old, document(), later], AS_OF)
    assert "unlinked_correction" in codes(context)
    assert context["upcoming_events"] == []
    risk = next(risk for risk in context["risks"] if risk["code"] == "unlinked_correction")
    assert risk["source_ids"] == ["old-correction"]
    assert risk["available_at"] == "2026-01-01T00:00:00+00:00"


def test_linked_withdrawal_removes_target_and_own_dates_from_upcoming():
    withdrawal = document("withdrawal", "20260905000002", is_withdrawal=True,
                          supersedes_source_ids=["original"])
    withdrawal["published_at"] = withdrawal["available_at"] = "2026-09-05T09:00:00+09:00"
    context = build_corporate_action_context([document(), withdrawal], AS_OF)
    assert context["upcoming_events"] == []
    assert {event["status"] for event in context["events"]} == {"withdrawn"}


def test_latest_known_revision_of_same_receipt_wins_without_mutating_input():
    first = document()
    changed = deepcopy(first)
    changed["source_id"] = "new-version"
    changed["available_at"] = "2026-09-05T09:00:00+09:00"
    changed["metadata"]["structured_row"]["nstk_asstd"] = "2026-09-21"
    context = build_corporate_action_context([changed, first], AS_OF)
    assert {event["source_ids"][0] for event in context["events"]} == {"new-version"}
    assert first["metadata"]["structured_row"]["nstk_asstd"] == "2026-09-15"


def test_all_documents_are_used_without_an_eight_event_cap():
    documents = [document(str(index), f"20260904{index:06d}") for index in range(12)]
    context = build_corporate_action_context(documents, AS_OF)
    assert len(context["events"]) == 24
    assert context == build_corporate_action_context(documents[::-1], AS_OF)


def test_duplicate_source_id_cannot_silently_replace_a_different_receipt():
    with pytest.raises(ValueError, match="unique receipts"):
        build_corporate_action_context([document(), document(receipt="20260904000002")], AS_OF)


def test_context_cache_content_only_changes_when_upcoming_status_changes():
    doc = document()
    context = build_corporate_action_context([doc], AS_OF)
    assert context == build_corporate_action_context([doc], AS_OF + timedelta(minutes=15))
    at_record_date_end = datetime(2026, 9, 15, 15, tzinfo=timezone.utc)
    later = build_corporate_action_context([doc], at_record_date_end)
    assert len(later["upcoming_events"]) == 1


def history(start="2026-08-03", end="2026-09-04"):
    return [{"available_at": day + "T15:30:00+09:00", "close": 100,
             "price_basis": "unadjusted", "source": "krx"} for day in (start, end)]


@pytest.mark.parametrize("title", ["\ubb34\uc0c1\uc99d\uc790\uacb0\uc815", "\uc8fc\uc2dd\ubd84\ud560\uacb0\uc815", "\uc8fc\uc2dd\ubcd1\ud569\uacb0\uc815"])
def test_mechanical_disclosure_in_price_window_requires_entry_review(title):
    doc = document()
    doc["title"] = title
    context = build_corporate_action_context([doc], AS_OF)
    result = assess_price_basis(history(), context, AS_OF)
    assert result["entry_block_reasons"] == ["unverified_corporate_action_price_basis"]
    assert result["status"] == "review_required"
    assert result["price_basis"] == "raw"
    assert result["source_ids"] == ["original"]


@pytest.mark.parametrize("title,endpoint", [("\uc720\uc0c1\uc99d\uc790\uacb0\uc815", "piicDecsn"), ("\ubc30\ub2f9\uacb0\uc815", None)])
def test_cash_dividend_and_paid_capital_alone_are_not_blanket_entry_blocks(title, endpoint):
    doc = document(structured_endpoint=endpoint)
    doc["title"] = title
    result = assess_price_basis(history(), build_corporate_action_context([doc], AS_OF), AS_OF)
    assert result["entry_block_reasons"] == []
    assert result["status"] == "unverified"
    assert result["warnings"]


def test_pre_window_disclosure_with_future_dates_is_not_historical_exposure():
    doc = document()
    doc["published_at"] = doc["available_at"] = "2026-01-01T09:00:00+09:00"
    context = build_corporate_action_context([doc], AS_OF)
    result = assess_price_basis(history(), context, AS_OF)
    assert result["entry_block_reasons"] == []
    assert len(context["upcoming_events"]) == 2
    assert result["status"] == "unverified"


def test_known_action_date_in_window_blocks_even_with_older_publication():
    doc = document()
    doc["published_at"] = doc["available_at"] = "2026-01-01T09:00:00+09:00"
    doc["metadata"]["structured_row"]["nstk_asstd"] = "2026-08-20"
    result = assess_price_basis(history(), build_corporate_action_context([doc], AS_OF), AS_OF)
    assert result["source_ids"] == ["original"]
    assert result["entry_block_reasons"]


def test_board_decision_and_dividend_accrual_are_not_price_adjustment_dates():
    doc = document()
    doc["published_at"] = doc["available_at"] = "2026-01-01T09:00:00+09:00"
    doc["metadata"]["structured_row"] = {"rcept_no": doc["metadata"]["rcept_no"],
                                         "bddd": "2026-08-20", "nstk_dividrk": "2026-08-20"}
    result = assess_price_basis(history(), build_corporate_action_context([doc], AS_OF), AS_OF)
    assert result["entry_block_reasons"] == []
    assert result["status"] == "unverified"


def test_old_unlinked_correction_does_not_clear_exposure_on_subsequent_link():
    old = document("unlinked", "20260903000001", is_correction=True)
    old["published_at"] = old["available_at"] = "2026-08-04T09:00:00+09:00"
    later = document("later", "20260904000002", is_correction=True, supersedes_source_ids=["unlinked"])
    later["published_at"] = later["available_at"] = "2026-09-05T09:00:00+09:00"
    context = build_corporate_action_context([old, later], AS_OF)
    result = assess_price_basis(history(), context, AS_OF)
    assert "unlinked" in result["source_ids"]
    assert result["entry_block_reasons"]


@pytest.mark.parametrize("with_old_disclosure", [False, True])
def test_unknown_calendar_is_never_certified_safe_or_blanket_blocked(with_old_disclosure):
    doc = document()
    doc["published_at"] = doc["available_at"] = "2026-01-01T09:00:00+09:00"
    doc["metadata"]["structured_row"] = None
    context = build_corporate_action_context([doc] if with_old_disclosure else [], AS_OF)
    result = assess_price_basis(history(), context, AS_OF)
    assert result["status"] == "unverified"
    assert result["entry_block_reasons"] == []
    assert "complete_corporate_action_calendar_unavailable" in result["data_gaps"]


@pytest.mark.parametrize("basis,source", [(None, "krx"), ("adjusted", "krx"), ("unadjusted", None),
                                       ("unadjusted", True), ("unadjusted", " ")])
def test_unverified_or_missing_adjustment_provenance_is_not_certified(basis, source):
    bars = history()
    bars[0].update(price_basis=basis, source=source)
    result = assess_price_basis(bars, build_corporate_action_context([], AS_OF), AS_OF)
    assert result["price_basis"] == "unknown"
    assert result["status"] == "unverified"
    assert "price_basis_provenance_unverified" in result["data_gaps"]


def test_no_price_history_fails_clearly_and_helper_does_not_add_query_clock():
    context = build_corporate_action_context([], AS_OF)
    with pytest.raises(ValueError, match="observed price history"):
        assess_price_basis([], context, AS_OF)
    result = assess_price_basis(history(), context, AS_OF)
    assert result == assess_price_basis(history(), context, AS_OF + timedelta(minutes=15))
