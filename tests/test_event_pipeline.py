import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.data_pipeline.evidence_corpus_builder import EvidenceCorpusBuilder
from src.evidence.index_builder import EvidenceIndexBuilder
from src.ingestion.dart import DartDisclosureCollector
from src.ingestion.services import IngestionService
from src.ingestion.types import DocumentRecord
from src.runner.analysis_data import LocalAnalysisData
from src.runner.corporate_actions import assess_price_basis

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)
CANDIDATE = {"stock_code": "005930", "theme_keys": ["semiconductor"]}


def document(identifier="one", *, title="Quarterly report", body="Verified document body. " * 100,
             available=None, metadata=None):
    return DocumentRecord(source_type="dart", title=title, content=body,
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + identifier,
        stock_code="005930", published_at=(NOW - timedelta(days=2)).isoformat(),
        metadata={"has_body": True, "collected_at": (available or NOW - timedelta(days=1)).isoformat(),
                  "published_at_precision": "date", **(metadata or {})})


def write_corpus(tmp_path, documents):
    path = tmp_path / "canonical_index" / "semiconductor" / "corpus.jsonl"
    records = EvidenceCorpusBuilder(chunk_size=100, chunk_overlap=20).build_records(documents)
    EvidenceCorpusBuilder().save_jsonl(records, str(path))
    return path, records


def write_ingested_corpus(tmp_path, documents, theme="semiconductor"):
    IngestionService()._save_raw_documents(documents, str(tmp_path / "raw"), "dart", theme)
    index = EvidenceIndexBuilder(data_dir=str(tmp_path))
    loaded, _ = index._load_raw_documents(theme)
    records = index._dedupe_records(EvidenceCorpusBuilder().build_records(loaded))
    path = tmp_path / "canonical_index" / theme / "corpus.jsonl"
    EvidenceCorpusBuilder().save_jsonl(records, str(path))
    return records


def test_many_chunks_form_one_full_document_without_using_up_event_slots(tmp_path):
    long = document(body="Verified annual content. " * 700)
    second = document("two", title="Another quarterly report", body="Other reported facts.")
    _, records = write_corpus(tmp_path, [long, second])
    assert len(records) > 100
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    assert len(evidence["events"]) == len(evidence["documents"]) == 2
    result = next(row for row in evidence["documents"] if row["url"] == long.url)
    assert result["source_text_hash"] == hashlib.sha256(long.content.encode()).hexdigest()
    assert result["original_characters"] == len(long.content)
    assert result["text_scope"] == "document"
    assert result["truncated"] is True


def test_corporate_action_guard_uses_documents_beyond_eight_event_limit(tmp_path):
    documents = [document(f"2026090200000{index}", title="\ubb34\uc0c1\uc99d\uc790\uacb0\uc815",
                          body=f"Distinct disclosed bonus issue terms {index}",
                          available=NOW - timedelta(hours=12 - index),
                          metadata={"rcept_no": f"2026090200000{index}"}) for index in range(9)]
    write_corpus(tmp_path, documents)
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    assert len(evidence["events"]) == 8
    selected = {source for event in evidence["events"] for source in event["source_ids"]}
    risks = evidence["corporate_actions"]["risks"]
    guarded = {source for risk in risks if risk["code"] == "price_basis_review" for source in risk["source_ids"]}
    assert len(guarded) == 9
    assert guarded - selected


@pytest.mark.parametrize("flag,risk_code", [("is_correction", "unlinked_correction"),
    ("has_correction", "subsequent_correction_unresolved"), ("is_withdrawal", "unlinked_withdrawal")])
def test_aged_unresolved_corporate_action_survives_actual_loader_and_attention_cap(tmp_path, flag, risk_code):
    receipt = "20250701000001"
    old = document(receipt, title="\uc8fc\uc694\uc0ac\ud56d\ubcf4\uace0\uc11c(\ubb34\uc0c1\uc99d\uc790 \uacb0\uc815)",
                   available=NOW - timedelta(days=420), body="Verified historical corporate action. " * 10,
                   metadata={"rcept_no": receipt, flag: True,
                       "structured_endpoint": "fricDecsn", "structured_rcept_no": receipt,
                       "structured_body_error_type": "success",
                       "structured_row": {"rcept_no": receipt, "nstk_asstd": "2026-09-03"}})
    old.published_at = (NOW - timedelta(days=430)).isoformat()
    recent = []
    for index in range(9):
        new_receipt = f"2026090200000{index}"
        recent.append(document(new_receipt, title="\ubb34\uc0c1\uc99d\uc790\uacb0\uc815",
            body=f"Distinct future bonus issue plan {index}. " * 10,
            metadata={"rcept_no": new_receipt, "structured_rcept_no": new_receipt,
                      "structured_endpoint": "fricDecsn", "structured_body_error_type": "success",
                      "structured_row": {"rcept_no": new_receipt, "nstk_asstd": "2026-09-15"}}))
    write_corpus(tmp_path, [old, *recent])
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    context = evidence["corporate_actions"]
    risk = next(row for row in context["risks"] if row["code"] == risk_code)
    assert risk["sources"][0]["rcept_no"] == receipt
    assert risk["available_at"] == old.metadata["collected_at"]
    assert context["upcoming_events"] == []
    assert len(evidence["events"]) == 8
    selected_ids = {source for event in evidence["events"] for source in event["source_ids"]}
    assert not selected_ids.intersection(risk["source_ids"])
    history = [{"available_at": day + "T15:30:00+09:00", "close": 100,
                "price_basis": "unadjusted", "source": "krx"} for day in ("2026-09-02", "2026-09-03")]
    safety = assess_price_basis(history, context, NOW)
    assert safety["entry_block_reasons"] == ["unverified_corporate_action_price_basis"]
    assert set(risk["source_ids"]) <= set(safety["source_ids"])


def test_age_exception_is_only_for_dart_corporate_actions(tmp_path):
    old_action = document("20250701000001", title="\uc8fc\uc2dd\ubcd1\ud569\uacb0\uc815",
                          available=NOW - timedelta(days=420), metadata={"rcept_no": "20250701000001"})
    old_annual = document("20250701000002", title="\uc0ac\uc5c5\ubcf4\uace0\uc11c", available=NOW - timedelta(days=420))
    old_news = document("old-news", title="\uc8fc\uc2dd\ubcd1\ud569\uacb0\uc815", available=NOW - timedelta(days=420))
    old_news.source_type = "news"
    for row in (old_action, old_annual, old_news):
        row.published_at = (NOW - timedelta(days=430)).isoformat()
    write_corpus(tmp_path, [old_action, old_annual, old_news])
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    assert {row["url"] for row in evidence["documents"]} == {old_action.url}
    assert {risk["action_type"] for risk in evidence["corporate_actions"]["risks"]} == {"reverse_split"}


def test_age_exception_does_not_admit_a_future_observed_corporate_action(tmp_path):
    future = document("20250701000001", title="\uc8fc\uc2dd\ubd84\ud560\uacb0\uc815",
                      available=NOW + timedelta(seconds=1), metadata={"rcept_no": "20250701000001"})
    future.published_at = (NOW - timedelta(days=430)).isoformat()
    current = document("current")
    write_corpus(tmp_path, [future, current])
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    assert {row["url"] for row in evidence["documents"]} == {current.url}
    assert evidence["corporate_actions"]["events"] == evidence["corporate_actions"]["risks"] == []


def test_chunk_only_corpus_uses_all_available_fragments_and_conservative_timestamp(tmp_path):
    path, records = write_corpus(tmp_path, [document(body="ab" * 80)])
    for index, row in enumerate(records):
        del row["metadata"]["content"]
        row["metadata"]["collected_at"] = (NOW - timedelta(hours=3 - index)).isoformat()
    path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    assert len(evidence["documents"]) == 1
    result = evidence["documents"][0]
    assert result["text_scope"] == "available_fragments"
    assert "[fragment]" in result["text"]
    assert result["available_at"] == records[-1]["metadata"]["collected_at"]


def test_future_document_does_not_change_current_event_packet(tmp_path):
    current = document()
    write_corpus(tmp_path, [current])
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    before = loader.load_evidence(CANDIDATE, NOW)
    write_corpus(tmp_path, [current, document("future", available=NOW + timedelta(days=1))])
    assert loader.load_evidence(CANDIDATE, NOW) == before


def test_latest_known_revision_changes_source_identity_without_moving_first_observation(tmp_path):
    old = document(body="Original reported amount.")
    revised = document(body="Revised reported amount.", available=NOW - timedelta(hours=1))
    write_corpus(tmp_path, [old, revised])
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    before = loader.load_evidence(CANDIDATE, NOW - timedelta(hours=2))["documents"][0]
    after = loader.load_evidence(CANDIDATE, NOW)["documents"][0]
    assert before["text"] == old.content
    assert after["text"] == revised.content
    assert before["source_id"] != after["source_id"]
    assert before["available_at"] == old.metadata["collected_at"]


def test_unchanged_text_with_new_correction_flag_is_a_distinct_known_revision(tmp_path):
    original = document()
    corrected = document(available=NOW - timedelta(hours=1), metadata={"has_correction": True})
    write_corpus(tmp_path, [original, corrected])
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    before = loader.load_evidence(CANDIDATE, NOW - timedelta(hours=2))
    after = loader.load_evidence(CANDIDATE, NOW)
    assert before["documents"][0]["source_text_hash"] == after["documents"][0]["source_text_hash"]
    assert before["documents"][0]["source_id"] != after["documents"][0]["source_id"]
    assert after["events"][0]["has_correction"] is True
    assert after["events"][0]["is_correction"] is False
    assert "subsequent_correction_reported" in after["events"][0]["risk_flags"]


def test_exact_provider_fields_survive_real_corpus_builder(tmp_path):
    receipt = "20260902000001"
    doc = document(receipt, title="\uc8fc\uc694\uc0ac\ud56d\ubcf4\uace0\uc11c(\uc720\uc0c1\uc99d\uc790\uacb0\uc815)", metadata={
        "rcept_no": receipt, "structured_rcept_no": receipt, "structured_endpoint": "piicDecsn",
        "structured_row": {"rcept_no": receipt, "nstk_ostk_cnt": "12,500", "ic_mthn": "third-party"}})
    write_corpus(tmp_path, [doc])
    event = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)["events"][0]
    assert event["event_type"] == "capital_raise"
    assert event["published_at_precision"] == "date"
    assert event["structured_facts"][0]["fields"]["nstk_ostk_cnt"] == "12,500"


def test_conflicting_revisions_at_same_available_time_fail_instead_of_picking_one(tmp_path):
    write_corpus(tmp_path, [document(body="First amount"), document(body="Conflicting amount")])
    with pytest.raises(ValueError, match="conflicting evidence revisions"):
        LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)


def test_known_bad_body_cannot_become_an_event(tmp_path):
    write_corpus(tmp_path, [document(metadata={"has_body": False, "body_source": "title_fallback"})])
    with pytest.raises(ValueError, match="DART body quality"):
        LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)


def test_conflicting_legacy_fragment_cannot_leave_old_text_citable(tmp_path):
    path, records = write_corpus(tmp_path, [document(body="Original reported amount.")])
    for row in records:
        del row["metadata"]["content"]
    changed = copy.deepcopy(records[0])
    changed["text"] = "Changed reported amount."
    changed["metadata"]["collected_at"] = (NOW - timedelta(hours=1)).isoformat()
    path.write_text("\n".join(json.dumps(row) for row in [*records, changed]), encoding="utf-8")
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    before = loader.load_evidence(CANDIDATE, NOW - timedelta(hours=2))
    assert before["documents"][0]["text"] == "Original reported amount."
    with pytest.raises(ValueError, match="conflicting canonical chunks"):
        loader.load_evidence(CANDIDATE, NOW)


def test_raw_to_canonical_reversion_preserves_all_known_observation_episodes(tmp_path):
    first = document(body="Original verified amount. " * 12, available=NOW - timedelta(hours=6))
    changed = document(body="Changed verified amount. " * 12, available=NOW - timedelta(hours=4))
    reverted = document(body=first.content, available=NOW - timedelta(hours=2))
    duplicate = document(body=first.content, available=NOW - timedelta(hours=1))
    records = write_ingested_corpus(tmp_path, [first, changed, reverted, duplicate])
    assert len({row["metadata"]["version_id"] for row in records}) == 3
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    observed = [loader.load_evidence(CANDIDATE, NOW - timedelta(hours=hours))["documents"][0]
                for hours in (5, 3, 0)]
    assert [row["text"] for row in observed] == [first.content, changed.content, first.content]
    assert len({row["source_id"] for row in observed}) == 3
    assert observed[-1]["available_at"] == reverted.metadata["collected_at"]


def test_raw_to_canonical_keeps_metadata_only_correction_revision(tmp_path):
    receipt = "20260902000001"
    first = document(receipt, metadata={"rcept_no": receipt, "has_correction": False})
    changed = document(receipt, available=NOW - timedelta(hours=1),
                       metadata={"rcept_no": receipt, "has_correction": True})
    write_ingested_corpus(tmp_path, [first, changed])
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    before = loader.load_evidence(CANDIDATE, NOW - timedelta(hours=2))["events"][0]
    after = loader.load_evidence(CANDIDATE, NOW)["events"][0]
    assert before["has_correction"] is False
    assert after["has_correction"] is True
    assert before["source_ids"] != after["source_ids"]


def test_repeated_evidence_in_another_theme_keeps_first_usable_time_and_cache_identity(tmp_path):
    first = document()
    repeated = document(available=NOW - timedelta(hours=1))
    write_ingested_corpus(tmp_path, [first])
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    before = loader.load_evidence(CANDIDATE, NOW)
    write_ingested_corpus(tmp_path, [repeated], theme="electronics")
    multi_theme = {**CANDIDATE, "theme_keys": ["electronics", "semiconductor"]}
    assert loader.load_evidence(multi_theme, NOW) == before


def test_cross_theme_coalescing_does_not_hide_conflicting_observation_at_same_instant(tmp_path):
    first = document()
    repeated = document(available=NOW - timedelta(hours=1))
    changed = document(body="Conflicting observed amount. " * 12, available=NOW - timedelta(hours=1))
    write_ingested_corpus(tmp_path, [first])
    write_ingested_corpus(tmp_path, [repeated], theme="electronics")
    write_ingested_corpus(tmp_path, [changed], theme="export")
    candidate = {**CANDIDATE, "theme_keys": ["electronics", "semiconductor", "export"]}
    with pytest.raises(ValueError, match="conflicting evidence revisions"):
        LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(candidate, NOW)


def structured_document():
    receipt = "20260902000001"
    title = "\uc8fc\uc694\uc0ac\ud56d\ubcf4\uace0\uc11c(\uc720\uc0c1\uc99d\uc790\uacb0\uc815)"
    metadata = {"rcept_no": receipt, "structured_rcept_no": receipt,
                "structured_endpoint": "piicDecsn", "structured_body_error_type": "structured_too_short",
                "structured_row": {"rcept_no": receipt, "nstk_ostk_cnt": "12,500"},
                "evidence_scope": "structured_fields", "body_source": "structured_fields",
                "has_body": False, "body_extracted": False}
    body = DartDisclosureCollector.structured_fields_content(title, metadata)
    assert body
    return document(receipt, title=title, body=body, metadata=metadata)


def test_verified_structured_only_fields_reach_events_without_becoming_narrative(tmp_path):
    doc = structured_document()
    write_ingested_corpus(tmp_path, [doc])
    evidence = LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
    event = evidence["events"][0]
    assert evidence["documents"][0]["metadata"]["has_body"] is False
    assert event["text_scope"] == "structured_fields"
    assert event["sources"][0]["text_scope"] == "structured_fields"
    assert event["structured_facts"][0]["fields"]["nstk_ostk_cnt"] == "12,500"


@pytest.mark.parametrize("changed", ["receipt", "body"])
def test_structured_only_label_cannot_bypass_verification(tmp_path, changed):
    doc = structured_document()
    if changed == "receipt":
        doc.metadata["structured_rcept_no"] = "20260902000999"
    else:
        doc.content = "Unsupported claim instead of verified structured fields."
    write_corpus(tmp_path, [doc])
    with pytest.raises(ValueError, match="structured fields do not match"):
        LocalAnalysisData(data_dir=str(tmp_path)).load_evidence(CANDIDATE, NOW)
