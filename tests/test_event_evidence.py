from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import pytest

from src.runner.event_evidence import build_event_evidence, select_event_evidence


def document(source_id="doc:1", title="단일판매ㆍ공급계약체결", text="공급계약 원문 A", **overrides):
    row = {"source_id": source_id, "source_type": "dart", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1",
           "title": title, "text": text, "published_at": "2026-09-01T00:00:00+09:00",
           "available_at": "2026-09-01T10:00:00+09:00", "truncated": False,
           "source_text_hash": hashlib.sha256(text.encode()).hexdigest(),
           "metadata": {"published_at_precision": "date"}}
    return {**row, **overrides}


def test_distinct_contracts_and_same_headline_with_different_bodies_remain_distinct():
    events = build_event_evidence([document(), document("doc:2", text="공급계약 원문 B")], "005930")
    assert len(events) == 2
    assert {event["event_type"] for event in events} == {"contract"}
    assert len({event["event_id"] for event in events}) == 2


def test_exact_normalized_syndication_and_repeated_document_are_deduplicated():
    first = document(source_type="news")
    second = document("doc:2", title="단일판매ㆍ공급계약체결  ", text="공급계약   원문 A",
                      source_type="news", url="https://news.example/article/2", available_at="2026-09-01T11:00:00+09:00")
    event = build_event_evidence([first, second, deepcopy(first)], "005930")[0]
    assert event["source_ids"] == ["doc:1", "doc:2"]
    assert event["source_count"] == 2
    assert event["available_at"] == "2026-09-01T01:00:00+00:00"
    assert event["updated_at"] == "2026-09-01T02:00:00+00:00"
    assert event["published_at_precision"] == "date"
    assert event["event_id"] == build_event_evidence([second, first], "005930")[0]["event_id"]
    assert "text" not in event["sources"][0]


def test_same_body_on_different_publication_dates_is_not_syndication():
    second = document("doc:2", published_at="2026-09-02T00:00:00+09:00", available_at="2026-09-02T10:00:00+09:00")
    assert len(build_event_evidence([document(), second], "005930")) == 2


def test_correction_keeps_original_and_does_not_guess_target():
    corrected = document("doc:2", title="[기재정정]단일판매ㆍ공급계약체결", text="계약 정정 내용")
    events = build_event_evidence([document(), corrected], "005930")
    correction = next(event for event in events if event["is_correction"])
    assert len(events) == 2
    assert correction["unlinked_correction"] is True
    assert correction["supersedes_source_ids"] == []
    corrected["metadata"]["supersedes_source_ids"] = ["doc:1"]
    correction = next(event for event in build_event_evidence([document(), corrected], "005930") if event["is_correction"])
    assert correction["supersedes_source_ids"] == ["doc:1"]
    assert correction["unlinked_correction"] is False


def test_structured_facts_are_exact_provider_scalars_not_prose_inferences():
    row = document(text="매출 30% 증가, 계약 100억원이라는 본문")
    assert build_event_evidence([row], "005930")[0]["structured_facts"] == []
    row["metadata"].update(rcept_no="20260901000001", structured_rcept_no="20260901000001",
        structured_endpoint="testEndpoint", structured_row={"rcept_no": "20260901000001", "amount": "10,000", "unit": "원", "zero": 0})
    facts = build_event_evidence([row], "005930")[0]["structured_facts"][0]
    assert facts["fields"] == row["metadata"]["structured_row"]
    row["metadata"]["structured_rcept_no"] = "wrong"
    with pytest.raises(ValueError, match="receipt match"):
        build_event_evidence([row], "005930")


def test_material_events_and_explicit_risks_outrank_newer_routine_or_promotional_items():
    routine = document("routine", title="사업보고서", text="정기 보고 내용", available_at="2026-09-03T10:00:00+09:00")
    promotional = document("promo", title="영업이익 급등 기대 수혜 관련주", text="전망 기사", source_type="news")
    withdrawal = document("withdrawn", title="[철회]유상증자결정", text="철회 내용")
    selected = select_event_evidence(build_event_evidence([routine, promotional, document(), withdrawal], "005930"), 2)
    assert selected[0]["is_withdrawal"] is True
    assert selected[1]["event_type"] == "contract"
    assert all("score" not in event and "sentiment" not in event for event in selected)


def test_source_and_text_bounds_retain_actual_hashes_and_urls():
    text = "공급계약" * 1000
    documents = [document(f"doc:{index}", text=text, url=f"https://news.example/{index}") for index in range(9)]
    event = build_event_evidence(documents, "005930")[0]
    assert len(event["text"]) == 2400
    assert event["text_truncated"] is True
    assert event["source_count"] == 9 and event["omitted_sources_count"] == 5
    assert len(event["source_ids"]) == len(event["sources"]) == 4
    assert event["sources"][0]["source_text_hash"] == hashlib.sha256(text.encode()).hexdigest()
    assert event["sources"][0]["url"] == "https://news.example/0"


def test_bounded_sources_retain_both_availability_anchors_and_primary_provenance():
    documents = [document(f"doc:{index}", source_type="news", url=f"https://news.example/{index}",
                          available_at=f"2026-09-01T{hour:02d}:00:00+09:00")
                 for index, hour in enumerate((10, 11, 12, 13, 9, 14))]
    event = build_event_evidence(documents, "005930")[0]
    assert len(event["sources"]) == len(event["source_ids"]) == 4
    assert {"doc:0", "doc:4", "doc:5"} <= set(event["source_ids"])
    assert event["text_source_id"] == "doc:0"
    assert min(row["available_at"] for row in event["sources"]) == event["available_at"]
    assert max(row["available_at"] for row in event["sources"]) == event["updated_at"]
    assert event == build_event_evidence(list(reversed(documents)), "005930")[0]


def test_reobserving_an_identical_source_does_not_advance_event_update_time():
    first = document("first", source_type="news")
    second = document("second", source_type="news", available_at="2026-09-01T11:00:00+09:00")
    recollected = {**first, "available_at": "2026-09-01T12:00:00+09:00"}
    before = build_event_evidence([first, second], "005930")[0]
    assert build_event_evidence([first, second, recollected], "005930")[0] == before


def test_invalid_structured_facts_are_validated_even_when_the_source_would_be_omitted():
    documents = [document(f"doc:{index}") for index in range(5)]
    documents[-1]["metadata"].update(is_correction=True, rcept_no="receipt-A",
        structured_rcept_no="receipt-B", structured_endpoint="testEndpoint",
        structured_row={"rcept_no": "receipt-B", "amount": "999"})
    with pytest.raises(ValueError, match="receipt match"):
        build_event_evidence(documents, "005930")


def test_nonfinite_omitted_source_facts_fail_before_grouping():
    documents = [document(f"doc:{index}") for index in range(5)]
    documents[-1]["metadata"].update(rcept_no="receipt-A", structured_rcept_no="receipt-A",
        structured_endpoint="testEndpoint", structured_row={"rcept_no": "receipt-A", "amount": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        build_event_evidence(documents, "005930")


@pytest.mark.parametrize("scope", ["document", "available_fragments", "structured_fields", "summary"])
@pytest.mark.parametrize("location", ["row", "metadata"])
def test_text_scope_is_preserved_on_event_and_source_rows(scope, location):
    row = document()
    if location == "row":
        row["text_scope"] = scope
    else:
        row["metadata"]["evidence_scope"] = scope
    if scope == "structured_fields":
        row["metadata"].update(rcept_no="receipt-A", structured_rcept_no="receipt-A",
            structured_endpoint="testEndpoint", structured_row={"rcept_no": "receipt-A", "amount": "10"})
    event = build_event_evidence([row], "005930")[0]
    assert event["text_scope"] == scope
    assert event["sources"][0]["text_scope"] == scope


def test_structured_fields_require_facts_and_cannot_merge_into_document_prose():
    row = document("structured", text_scope="structured_fields")
    with pytest.raises(ValueError, match="verified provider facts"):
        build_event_evidence([row], "005930")
    row["metadata"].update(rcept_no="receipt-A", structured_rcept_no="receipt-A",
        structured_endpoint="testEndpoint", structured_row={"rcept_no": "receipt-A", "amount": "10"})
    events = build_event_evidence([document("narrative"), row], "005930")
    assert len(events) == 2
    assert {event["text_scope"] for event in events} == {"document", "structured_fields"}
    assert next(event for event in events if event["text_scope"] == "structured_fields")["text_source_id"] == "structured"


@pytest.mark.parametrize("scope", [None, True, "narrative_guess", [], {}])
def test_invalid_text_scopes_fail_explicitly(scope):
    with pytest.raises(ValueError, match="text_scope"):
        build_event_evidence([document(text_scope=scope)], "005930")


def test_explicit_row_scope_takes_precedence_over_metadata_and_default_is_document():
    row = document(text_scope="available_fragments", metadata={"evidence_scope": "document"})
    assert build_event_evidence([row], "005930")[0]["text_scope"] == "available_fragments"
    assert build_event_evidence([document()], "005930")[0]["text_scope"] == "document"


def test_truncated_identical_prefixes_do_not_merge_distinct_full_documents():
    first, second = document(truncated=True), document("doc:2", truncated=True)
    second["source_text_hash"] = hashlib.sha256(b"different unseen tail").hexdigest()
    assert len(build_event_evidence([first, second], "005930")) == 2


@pytest.mark.parametrize("field,value", [("source_id", None), ("text", ""), ("url", None),
    ("source_text_hash", "fake"), ("truncated", "false"), ("published_at", "2026-09-01"),
    ("available_at", "2026-09-01T10:00:00"), ("metadata", {"is_correction": "false"})])
def test_missing_or_ambiguous_fields_fail_explicitly(field, value):
    with pytest.raises(ValueError):
        build_event_evidence([document(**{field: value})], "005930")


def test_conflicting_same_source_identity_is_not_silently_replaced():
    with pytest.raises(ValueError, match="conflicting_source_document"):
        build_event_evidence([document(), document(text="다른 계약")], "005930")


@pytest.mark.parametrize("title,expected", [("영업(잠정)실적 공시", "earnings"),
    ("유상증자결정", "capital_raise"), ("전환사채권발행결정", "convertible_bond"),
    ("자기주식취득결정", "buyback"), ("회사합병결정", "merger"),
    ("현금ㆍ현물배당 결정", "dividend"), ("상장폐지 사유 발생", "regulatory_risk"),
    ("분기보고서", "other")])
def test_formal_corporate_event_classification(title, expected):
    assert build_event_evidence([document(title=title)], "005930")[0]["event_type"] == expected


def test_source_original_length_is_preserved_and_cannot_understate_supplied_text():
    row = document(original_characters=1000, truncated=True)
    assert build_event_evidence([row], "005930")[0]["sources"][0]["original_characters"] == 1000
    row["original_characters"] = 1
    with pytest.raises(ValueError, match="original_characters"):
        build_event_evidence([row], "005930")


def test_top_level_publication_precision_is_preserved_for_legacy_documents():
    event = build_event_evidence([document(metadata={}, published_at_precision="date")], "005930")[0]
    assert event["published_at_precision"] == "date"
    assert event["sources"][0]["published_at_precision"] == "date"


def test_original_with_a_reported_correction_is_not_itself_the_corrected_version():
    event = build_event_evidence([document(metadata={"has_correction": True})], "005930")[0]
    assert event["has_correction"] is True
    assert "subsequent_correction_reported" in event["risk_flags"]
    assert event["is_correction"] is False
    assert event["unlinked_correction"] is False
    with pytest.raises(ValueError, match="has_correction"):
        build_event_evidence([document(metadata={"has_correction": "true"})], "005930")


def test_recent_attention_window_precedes_old_materiality_without_dropping_old_context():
    old = document("old", title="[철회]유상증자결정", text="과거 철회 내용",
                   published_at="2025-09-01T00:00:00+09:00", available_at="2025-09-01T10:00:00+09:00")
    recent = document("recent", title="사업보고서", text="현재 정기 보고 내용")
    events = build_event_evidence([old, recent], "005930")
    as_of = datetime(2026, 9, 5, tzinfo=timezone.utc)
    assert select_event_evidence(events, 1, as_of=as_of)[0]["source_ids"] == ["recent"]
    assert len(select_event_evidence(events, 8, as_of=as_of)) == 2
    with pytest.raises(ValueError, match="aware datetime"):
        select_event_evidence(events, as_of=datetime(2026, 9, 5))


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_event_limit_fails(limit):
    with pytest.raises(ValueError):
        select_event_evidence([], limit)


def test_full_body_syndication_groups_headline_variants_and_retains_both_titles():
    body = "삼성전자는 공급계약을 체결했다고 발표했다. 계약 기간과 대상은 원문에 명시되어 있다. " * 6
    first = document("news:1", source_type="news", title="삼성전자 공급계약체결 발표", text=body)
    second = document("news:2", source_type="news", title="공급계약체결 알린 삼성전자", text=body)
    events = build_event_evidence([first, second], "005930")
    assert len(events) == 1
    assert events[0]["deduplication_basis"] == "full_body_syndication"
    assert {source["title"] for source in events[0]["sources"]} == {first["title"], second["title"]}
    assert events == build_event_evidence([second, first], "005930")


@pytest.mark.parametrize("title,metadata", [
    ("삼성전자 공급계약체결 정정", {}),
    ("삼성전자 공급계약체결 철회", {}),
    ("삼성전자 공급계약체결 부인", {}),
    ("삼성전자 공급계약체결 발표", {"is_correction": True}),
    ("삼성전자 공급계약체결 발표", {"is_withdrawal": True}),
    ("삼성전자 공급계약체결 발표", {"has_correction": True}),
])
def test_identical_body_does_not_merge_correction_with_original(title, metadata):
    body = "계약에 대한 원문과 구체적인 내용. " * 30
    first = document("news:1", source_type="news", title="삼성전자 공급계약체결 발표", text=body)
    second = document("news:2", source_type="news", title=title, text=body, metadata=metadata)
    assert len(build_event_evidence([first, second], "005930")) == 2


@pytest.mark.parametrize("first_amount,second_amount", [("100억원", "200억원"), ("100억원", "100만원"),
    ("100%", "100억원"), ("+100억원", "-100억원"), ("$100", "€100"), ("100억원 흑자", "100억원 적자")])
def test_identical_body_does_not_merge_conflicting_headline_amounts(first_amount, second_amount):
    body = "계약에 대한 원문과 구체적인 내용. " * 30
    first = document("news:1", source_type="news", title=f"공급계약체결 {first_amount}", text=body)
    second = document("news:2", source_type="news", title=f"공급계약체결 {second_amount}", text=body)
    assert len(build_event_evidence([first, second], "005930")) == 2


@pytest.mark.parametrize("overrides", [{"text_scope": "summary"}, {"text_scope": "available_fragments"},
                                       {"truncated": True}, {"source_type": "dart"}])
def test_headline_variant_clustering_requires_complete_news_body(overrides):
    body = "계약에 대한 원문과 구체적인 내용. " * 30
    first = document("news:1", source_type="news", title="공급계약체결 발표", text=body)
    second = document("news:2", source_type="news", title="새 공급계약체결", text=body)
    first.update(overrides)
    second.update(overrides)
    assert len(build_event_evidence([first, second], "005930")) == 2
