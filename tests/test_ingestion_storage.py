from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from src.ingestion.services import IngestionService
from src.ingestion.storage import atomic_write, read_rows
from src.ingestion.types import DocumentRecord, FinancialSnapshot, MarketRecord


def news(content="Body A", **metadata):
    return DocumentRecord(source_type="news", title="News", content=content,
        url="https://news.example/1", stock_code="005930", published_at="2026-09-05T10:00:00+09:00",
        metadata={"collected_at": "2026-09-05T10:01:00+09:00", **metadata})


def market(close="100", observed="2026-09-05T10:00:00+09:00"):
    return MarketRecord(source_type="chart", stock_name="Company", stock_code="005930",
        timestamp="2026-09-04T15:30:00+09:00", open="100", high="110", low="90", close=close,
        volume="1000", metadata={"source": "krx", "collected_at": observed})


def financial(revenue=100, observed="2026-09-05T10:00:00+09:00", fs_div="CFS"):
    return FinancialSnapshot(source_type="financials", stock_name="Company", stock_code="005930",
        corp_code="00126380", fiscal_year="2025", report_code="11011", report_name="Annual",
        revenue=revenue, as_of="2025-12-31", metadata={"fs_div": fs_div, "collected_at": observed})


@pytest.mark.parametrize("source", ["news", "chart", "financials"])
def test_archives_preserve_return_to_previous_value_as_new_observation(tmp_path, source):
    service = IngestionService()
    if source == "news":
        values = [news("A", collected_at="2026-09-05T10:00:00+09:00"),
                  news("B", collected_at="2026-09-05T11:00:00+09:00"),
                  news("A", collected_at="2026-09-05T12:00:00+09:00"),
                  news("A", collected_at="2026-09-05T13:00:00+09:00")]
        save = lambda row: service._save_raw_documents([row], str(tmp_path), source, "theme")
        field = "content"
    elif source == "chart":
        values = [market(value, f"2026-09-05T{hour}:00:00+09:00")
                  for value, hour in [("100", "10"), ("80", "11"), ("100", "12"), ("100", "13")]]
        save = lambda row: service._save_raw_market_records([row], str(tmp_path), "theme")
        field = "close"
    else:
        values = [financial(value, f"2026-09-05T{hour}:00:00+09:00")
                  for value, hour in [(100, "10"), (80, "11"), (100, "12"), (100, "13")]]
        save = lambda row: service._save_raw_financial_snapshots([row], str(tmp_path), "theme")
        field = "revenue"
    assert [save(row) for row in values] == [1, 1, 1, 0]
    stored = read_rows(tmp_path / source / "theme.jsonl")
    assert [row[field] for row in stored] == [getattr(row, field) for row in values[:3]]
    assert len({row["metadata"]["version_id"] for row in stored}) == 3
    assert stored[-1]["metadata"]["collected_at"] == "2026-09-05T12:00:00+09:00"


def test_financial_consolidated_and_separate_are_distinct_logical_records(tmp_path):
    service = IngestionService()
    rows = [financial(100, fs_div="CFS"), financial(80, fs_div="OFS"), financial(100, fs_div="CFS")]
    assert service._save_raw_financial_snapshots(rows, str(tmp_path), "theme") == 2
    assert {row["metadata"]["fs_div"] for row in read_rows(tmp_path / "financials/theme.jsonl")} == {"CFS", "OFS"}


@pytest.mark.parametrize("identical", [True, False])
def test_concurrent_writers_do_not_lose_or_duplicate_documents(tmp_path, identical):
    workers = 8
    barrier = Barrier(workers)

    def save(index):
        row = news()
        if not identical:
            row.url = f"https://news.example/{index}"
        barrier.wait(timeout=10)
        return IngestionService()._save_raw_documents([row], str(tmp_path), "news", "theme")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        counts = list(executor.map(save, range(workers)))
    expected = 1 if identical else workers
    stored = read_rows(tmp_path / "news/theme.jsonl")
    assert len(stored) == sum(counts) == expected
    assert len({row["url"] for row in stored}) == expected


@pytest.mark.parametrize("corrupt", ['{"broken":', '["not an object"]\n'])
def test_corrupt_archive_fails_without_overwriting_or_skipping_rows(tmp_path, corrupt):
    path = tmp_path / "news/theme.jsonl"
    path.parent.mkdir()
    path.write_text(corrupt, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSONL|expected JSON object"):
        IngestionService()._save_raw_documents([news()], str(tmp_path), "news", "theme")
    assert path.read_text(encoding="utf-8") == corrupt


def test_failed_atomic_publication_preserves_old_snapshot_and_removes_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.jsonl"
    original = '{"version": "old"}\n'
    path.write_text(original, encoding="utf-8")

    def fail_replace(temporary, target):
        assert Path(target).read_text(encoding="utf-8") == original
        assert Path(temporary).read_text(encoding="utf-8") == '{"version": "new"}\n'
        raise OSError("simulated publication failure")

    monkeypatch.setattr("src.ingestion.storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="publication failure"):
        atomic_write(path, '{"version": "new"}\n')
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]


def test_forum_posts_with_same_title_and_time_keep_distinct_urls(tmp_path):
    first = replace(news(), source_type="forum", url="https://forum.example/read?nid=1")
    second = replace(first, url="https://forum.example/read?nid=2")
    service = IngestionService()
    assert service._save_raw_documents([first, second], str(tmp_path), "forum", "theme") == 2
    assert len(read_rows(tmp_path / "forum/theme.jsonl")) == 2


def test_relative_estimate_changes_do_not_create_document_revisions(tmp_path):
    first = replace(news(published_at_precision="unknown", published_at_source="search_relative",
        publication_time_status="estimated", published_at_estimate="2026-09-05T09:05:00+09:00",
        raw_date_text="1시간 전"), published_at="")
    second = replace(first, metadata={**first.metadata,
        "published_at_estimate": "2026-09-05T09:10:00+09:00", "raw_date_text": "2시간 전",
        "collected_at": "2026-09-05T11:10:00+09:00"})
    service = IngestionService()
    assert service._save_raw_documents([first, second], str(tmp_path), "news", "theme") == 1


def test_summary_upgrade_and_confirmed_publication_are_real_revisions(tmp_path):
    first = replace(news(evidence_scope="summary", body_source="meta_description", body_extracted=False), published_at="")
    second = replace(first, metadata={**first.metadata, "evidence_scope": "document", "body_source": "article",
                                     "body_extracted": True})
    third = replace(second, published_at="2026-09-05T10:00:00+09:00")
    assert IngestionService()._save_raw_documents([first, second, third], str(tmp_path), "news", "theme") == 3
