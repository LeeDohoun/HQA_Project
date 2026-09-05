from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.data_pipeline.evidence_corpus_builder import EvidenceCorpusBuilder
from src.evidence.index_builder import EvidenceIndexBuilder
from src.ingestion.dart import DartDisclosureCollector
from src.ingestion.services import IngestionService
from src.ingestion.types import DocumentRecord


RECEIPT = "20260904000001"
TITLE = "주요사항보고서(유상증자결정)"


def structured_doc():
    metadata = {"rcept_no": RECEIPT, "structured_rcept_no": RECEIPT, "structured_endpoint": "piicDecsn",
                "structured_row": {"rcept_no": RECEIPT, "nstk_ostk_cnt": "12500"},
                "structured_body_error_type": "structured_too_short", "evidence_scope": "structured_fields",
                "body_source": "structured_fields", "has_body": False, "body_extracted": False,
                "body_error_type": "viewer_fetch_failed", "published_at_precision": "date",
                "collected_at": "2026-09-04T10:00:00+09:00"}
    return DocumentRecord("dart", TITLE, DartDisclosureCollector.structured_fields_content(TITLE, metadata),
                          "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + RECEIPT,
                          stock_code="005930", published_at="2026-09-04", metadata=metadata)


def raw_to_records(tmp_path, docs, source="dart"):
    IngestionService()._save_raw_documents(docs, str(tmp_path / "raw"), source, "test")
    index = EvidenceIndexBuilder(str(tmp_path))
    loaded, stats = index._load_raw_documents("test")
    builder = EvidenceCorpusBuilder(chunk_size=700, chunk_overlap=100)
    records = index._dedupe_records(builder.build_records(loaded))
    output = tmp_path / "canonical_index" / "test" / "corpus.jsonl"
    builder.save_jsonl(records, str(output))
    return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()], stats


def test_collector_structured_only_fields_survive_raw_loading_and_corpus(tmp_path, monkeypatch):
    collector = DartDisclosureCollector("not-a-live-key")
    listing = {"status": "000", "list": [{"report_nm": TITLE, "rcept_no": RECEIPT,
                                           "rcept_dt": "20260904", "stock_code": "005930"}]}
    response = {"status": "000", "list": [{"rcept_no": RECEIPT, "nstk_ostk_cnt": "12500"}]}
    monkeypatch.setattr(collector, "get_with_retry", Mock(side_effect=[
        SimpleNamespace(json=lambda: listing), SimpleNamespace(json=lambda: response)]))
    monkeypatch.setattr(collector, "_fetch_official_document_body", Mock(return_value=(
        "", "official_api", {"body_error_type": "official_fetch_failed"})))
    monkeypatch.setattr(collector, "_fetch_detail_excerpt", Mock(return_value=(
        "", "viewer_fallback", {"body_error_type": "wrapper_only", "wrapper_text_detected": True})))
    monkeypatch.setattr("src.ingestion.dart.time.sleep", lambda _: None)
    doc = collector.collect("00126380", "20260901", "20260905")[0]
    doc.metadata["collected_at"] = "2026-09-04T10:00:00+09:00"
    records, stats = raw_to_records(tmp_path, [doc])
    assert stats["skipped_invalid_count_by_source"]["dart"] == 0
    assert len(records) == 1
    metadata = records[0]["metadata"]
    assert metadata["evidence_scope"] == metadata["body_source"] == "structured_fields"
    assert metadata["has_body"] is False and metadata["body_extracted"] is False
    assert metadata["published_at_precision"] == "date"
    assert metadata["published_at"] == "2026-09-04"
    assert metadata["collected_at"] == "2026-09-04T10:00:00+09:00"
    assert metadata["structured_row"]["nstk_ostk_cnt"] == "12500"
    assert "nstk_ostk_cnt: 12500" in records[0]["text"]


@pytest.mark.parametrize("change", [
    {"rcept_no": "20260904000002"}, {"structured_rcept_no": "20260904000002"},
    {"structured_row": {"rcept_no": "20260904000002", "nstk_ostk_cnt": "12500"}},
    {"structured_row": {"rcept_no": RECEIPT}}, {"structured_endpoint": "tsstkAqDecsn"},
    {"structured_body_error_type": "structured_mojibake"}, {"structured_body_error_type": []},
    {"body_source": "title_fallback"}, {"evidence_scope": "body"},
    {"has_body": True}, {"body_extracted": True},
    {"structured_row": {"rcept_no": RECEIPT, "note": "잠시만 기다려주세요"}},
])
def test_structured_scope_does_not_bypass_receipt_or_body_validation(tmp_path, change):
    doc = structured_doc()
    doc.metadata.update(change)
    records, stats = raw_to_records(tmp_path, [doc])
    assert records == []
    assert stats["skipped_invalid_count_by_source"]["dart"] == 1


def test_valid_structured_metadata_cannot_admit_different_unverified_prose(tmp_path):
    doc = replace(structured_doc(), content="Unverified narrative statement. " * 20)
    assert raw_to_records(tmp_path, [doc])[0] == []


def test_generic_dart_wrapper_remains_invalid_without_structured_scope(tmp_path):
    doc = replace(structured_doc(), content="Invalid narrative body. " * 20,
                  metadata={"has_body": True, "wrapper_text_detected": True})
    assert raw_to_records(tmp_path, [doc])[0] == []


def test_news_a_b_a_episodes_survive_raw_corpus_and_chunk_deduplication(tmp_path):
    first = DocumentRecord("news", "Headline", "News content A. " * 5, "https://news.example/a", stock_code="005930",
                           metadata={"collected_at": "2026-09-04T10:00:00+09:00"})
    changed = replace(first, content="News content B. " * 5, metadata={"collected_at": "2026-09-04T11:00:00+09:00"})
    returned = replace(first, metadata={"collected_at": "2026-09-04T12:00:00+09:00"})
    repeated = replace(returned, metadata={"collected_at": "2026-09-04T13:00:00+09:00"})
    records, _ = raw_to_records(tmp_path, [first, changed, returned, repeated], "news")
    assert len(records) == 3
    assert [row["metadata"]["collected_at"] for row in records] == [
        "2026-09-04T10:00:00+09:00", "2026-09-04T11:00:00+09:00", "2026-09-04T12:00:00+09:00"]
    assert len({row["metadata"]["version_id"] for row in records}) == 3
    assert records[0]["text"] == records[2]["text"] != records[1]["text"]


def test_structured_metadata_revisions_and_stock_associations_survive_canonical_deduplication(tmp_path):
    first = structured_doc()
    correction = replace(first, metadata={**first.metadata, "has_correction": True,
                                         "collected_at": "2026-09-04T11:00:00+09:00"})
    other_stock = replace(first, stock_code="000660")
    records, _ = raw_to_records(tmp_path, [first, correction, other_stock, other_stock])
    assert len(records) == 3
    assert records[1]["metadata"]["has_correction"] is True
    assert records[2]["metadata"]["stock_code"] == "000660"


def test_legacy_same_version_recollection_without_episode_id_retains_first_metadata():
    index = EvidenceIndexBuilder()
    doc = structured_doc()
    rows = EvidenceCorpusBuilder().build_records([doc, replace(doc, metadata={**doc.metadata,
        "collected_at": "2026-09-04T11:00:00+09:00", "freshness_score": 0.4})])
    kept = index._dedupe_records(rows)
    assert len(kept) == 1
    assert kept[0]["metadata"]["collected_at"] == "2026-09-04T10:00:00+09:00"


def test_episode_id_is_shared_by_all_chunks_in_one_document(tmp_path):
    doc = DocumentRecord("news", "Headline", "Long factual report. " * 100, "https://news.example/a", stock_code="005930",
                         metadata={"collected_at": "2026-09-04T10:00:00+09:00"})
    records, _ = raw_to_records(tmp_path, [doc, doc], "news")
    assert len(records) > 1
    assert len({row["metadata"]["version_id"] for row in records}) == 1
    assert len({row["metadata"]["chunk_index"] for row in records}) == len(records)
