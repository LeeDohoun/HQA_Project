import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ingestion.dart import DartDisclosureCollector


RECEIPT = "20260904000001"
API_KEY = "private-dart-auth-key-not-evidence"


@pytest.fixture
def collector(monkeypatch):
    instance = DartDisclosureCollector(api_key=API_KEY)
    monkeypatch.setattr("src.ingestion.dart.time.sleep", lambda _: None)
    monkeypatch.setattr(instance.session, "request", Mock(side_effect=AssertionError("No live DART requests allowed")))
    monkeypatch.setattr(instance, "_fetch_official_document_body", Mock(return_value=(
        "", "official_api", {"body_error_type": "official_fetch_failed"},
    )))
    monkeypatch.setattr(instance, "_fetch_detail_excerpt", Mock(return_value=(
        "", "viewer_fallback", {"body_error_type": "viewer_fetch_failed"},
    )))
    return instance


def collect_one(collector, rows, title="주요사항보고서(유상증자결정)", remark=""):
    listing = {"status": "000", "page_no": 1, "page_count": 100, "total_count": 1, "total_page": 1, "list": [{
        "report_nm": title, "rcept_no": RECEIPT, "rcept_dt": "20260904",
        "corp_name": "Example", "stock_code": "005930", "rm": remark,
    }]}
    structured = {"status": "000", "list": rows}
    collector.get_with_retry = Mock(side_effect=[
        SimpleNamespace(json=lambda: listing), SimpleNamespace(json=lambda: structured),
    ])
    return collector.collect("00126380", "20260901", "20260905")[0]


@pytest.mark.parametrize(("title", "endpoint"), [
    ("주요사항보고서(유상증자결정)", "piicDecsn"),
    ("[기재정정] 주요사항보고서 (유상증자 결정)", "piicDecsn"),
    ("주요사항보고서(무상증자결정)", "fricDecsn"),
    ("[기재정정] 주요사항보고서 (무상증자 결정)", "fricDecsn"),
    ("주요사항보고서(자기주식 취득 결정)", "tsstkAqDecsn"),
    ("자기주식 취득 결정", "tsstkAqDecsn"),
    ("주요사항보고서(전환사채권발행결정)", "cvbdIsDecsn"),
])
def test_parenthesized_event_type_routes_to_verified_endpoint(collector, title, endpoint):
    assert collector._is_important_report(title)
    assert collector._match_structured_endpoints(title) == [endpoint]


@pytest.mark.parametrize("title", ["주식분할 결정", "주식병합결정", "현금ㆍ현물배당결정"])
def test_price_basis_review_disclosures_do_not_get_guessed_endpoints(collector, title):
    assert collector._is_important_report(title)
    assert collector._match_structured_endpoints(title) == []


def test_bonus_issue_dates_retain_official_field_names_and_receipt(collector):
    row = {"rcept_no": RECEIPT, "nstk_asstd": "2026년 09월 15일", "nstk_lstprd": "2026-09-30",
           "nstk_dividrk": "2026-01-01", "bddd": "2026-09-04"}
    doc = collect_one(collector, [row], "주요사항보고서(무상증자결정)")
    assert doc.metadata["structured_endpoint"] == "fricDecsn"
    assert doc.metadata["structured_row"] == row
    assert doc.metadata["evidence_scope"] == "structured_fields"
    assert doc.content == DartDisclosureCollector.structured_fields_content(doc.title, doc.metadata)
    assert "ex_date" not in doc.metadata


def test_exact_provider_row_survives_excerpt_limit_without_request_secrets(collector, capsys):
    description = "Capital increase purpose and subscription terms. " * 100
    row = {"rcept_no": RECEIPT, "corp_code": "00126380", "nstk_ostk_cnt": "12,500",
           "ic_mthn": "Third-party allotment", "description": description, "crtfc_key": API_KEY,
           "nested": {"authorization": "Bearer " + API_KEY, "comment": API_KEY, "value": 0}}
    doc = collect_one(collector, [{"rcept_no": "20260903000002", "wrong": "unrelated"}, row])

    assert doc.published_at == "2026-09-04"
    assert doc.metadata["published_at_precision"] == "date"
    assert doc.metadata["published_at_source"] == "dart_list.rcept_dt"
    assert doc.metadata["rcept_dt"] == "20260904"
    assert doc.metadata["structured_endpoint"] == "piicDecsn"
    assert doc.metadata["structured_rcept_no"] == RECEIPT
    assert doc.metadata["structured_row"]["description"] == description
    assert doc.metadata["structured_row"]["nstk_ostk_cnt"] == "12,500"
    assert doc.metadata["structured_row"]["nested"] == {"comment": "[REDACTED]", "value": 0}
    assert doc.metadata["structured_body_error_type"] == "success"
    assert doc.metadata["body_source"] == "structured_fields"
    assert doc.metadata["evidence_scope"] == "structured_fields"
    assert doc.metadata["has_body"] is False
    assert doc.metadata["body_extracted"] is False
    assert doc.metadata["body_error_type"] == "narrative_body_not_extracted"
    assert len(doc.content) <= 2500
    assert API_KEY not in json.dumps(asdict(doc))
    assert API_KEY not in json.dumps(list(collector._structured_cache.values()))
    assert API_KEY not in capsys.readouterr().out
    assert "crtfc_key" not in doc.metadata["structured_row"]
    collector._fetch_official_document_body.assert_not_called()
    collector._fetch_detail_excerpt.assert_not_called()


@pytest.mark.parametrize("rows", [
    [{"rcept_no": "20260903000002", "amount": "999999999"}],
    [{"amount": "999999999"}],
    [{"rcept_no": RECEIPT, "amount": "1"}, {"rcept_no": RECEIPT, "amount": "2"}],
    [None, "malformed-row"],
])
def test_missing_or_ambiguous_receipt_never_uses_first_row(collector, rows):
    doc = collect_one(collector, rows)
    assert doc.metadata["structured_row"] is None
    assert doc.metadata["structured_rcept_no"] is None
    assert doc.metadata["structured_endpoint"] is None
    assert doc.metadata["structured_body_error_type"] == "structured_no_rcept_match"
    assert doc.metadata["has_body"] is False
    assert doc.metadata["body_error_type"] == "viewer_fetch_failed"
    assert doc.metadata["body_source"] == "title_fallback"
    assert "999999999" not in doc.content
    assert collector._find_structured_row([{"rcept_no": RECEIPT}], "") is None


def test_short_but_receipt_verified_row_is_kept_when_all_body_paths_fail(collector):
    row = {"rcept_no": RECEIPT}
    doc = collect_one(collector, [row])
    assert doc.metadata["structured_row"] == row
    assert doc.metadata["structured_endpoint"] == "piicDecsn"
    assert doc.metadata["structured_body_error_type"] == "structured_too_short"
    assert doc.metadata["has_body"] is False
    assert doc.metadata["body_error_type"] == "viewer_fetch_failed"
    assert doc.metadata["body_extracted"] is False
    assert doc.content == "Example 공시: 주요사항보고서(유상증자결정)"


def test_verified_short_fields_are_evidence_without_claiming_a_narrative_body(collector):
    row = {"rcept_no": RECEIPT, "nstk_estk_cnt": 0}
    doc = collect_one(collector, [row])
    assert doc.metadata["structured_body_error_type"] == "structured_too_short"
    assert doc.metadata["evidence_scope"] == "structured_fields"
    assert doc.metadata["body_source"] == "structured_fields"
    assert doc.metadata["has_body"] is False
    assert doc.metadata["body_extracted"] is False
    assert doc.metadata["body_error_type"] == "viewer_fetch_failed"
    assert "nstk_estk_cnt: 0" in doc.content
    assert doc.content == DartDisclosureCollector.structured_fields_content(doc.title, doc.metadata)


def test_structured_request_failure_remains_explicit_after_existing_body_fallback(collector):
    listing = {"status": "000", "page_no": 1, "page_count": 100, "total_count": 1, "total_page": 1, "list": [{
        "report_nm": "주요사항보고서(유상증자결정)", "rcept_no": RECEIPT,
        "rcept_dt": "20260904", "corp_name": "Example",
    }]}
    collector.get_with_retry = Mock(side_effect=[SimpleNamespace(json=lambda: listing), RuntimeError("Unavailable")])
    collector._fetch_official_document_body.return_value = ("Verified official document body. " * 10,
                                                           "official_api", {"body_error_type": ""})
    doc = collector.collect("00126380", "20260901", "20260905")[0]
    assert doc.metadata["structured_body_error_type"] == "structured_fetch_failed"
    assert doc.metadata["structured_row"] is None
    assert doc.metadata["has_body"] is True
    assert doc.metadata["body_source"] == "official_api"
    assert doc.metadata["body_error_type"] == "success"


@pytest.mark.parametrize(("prefix", "remark", "is_correction", "has_correction", "is_withdrawal"), [
    ("[기재정정]", "", True, False, False),
    ("[첨부정정]", "", True, False, False),
    ("[정정]", "", True, False, False),
    ("", "유정", False, True, False),
    ("[정정제출요구]", "", False, False, False),
    ("[철회]", "", False, False, True),
    ("", "유철", False, False, True),
])
def test_correction_and_withdrawal_flags_do_not_invent_original_receipt(
    collector, prefix, remark, is_correction, has_correction, is_withdrawal,
):
    doc = collect_one(collector, [{"rcept_no": RECEIPT}], prefix + "주요사항보고서(유상증자결정)", remark)
    assert doc.metadata["remark"] == remark
    assert doc.metadata["is_correction"] is is_correction
    assert doc.metadata["has_correction"] is has_correction
    assert doc.metadata["is_withdrawal"] is is_withdrawal
    assert "original_rcept_no" not in doc.metadata
    assert "supersedes_rcept_no" not in doc.metadata
