from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
import pytest

from src.ingestion.services import IngestionService
from src.ingestion.storage import read_rows
from src.ingestion.types import CollectRequest, DocumentRecord, FinancialSnapshot, MarketRecord, StockTarget


def request(tmp_path, theme="first", sources=None):
    return CollectRequest(target=StockTarget("삼성전자", "005930", "00126380"), max_news=20,
        forum_pages=1, chart_pages=1, from_date="20260801", to_date="20260905",
        dart_api_key="must-not-write-this-key", theme_key=theme, enabled_sources=sources or ["news"],
        raw_output_dir=str(tmp_path / "raw"), incremental=True)


def article(content="삼성전자는 공급계약을 발표했다."):
    return DocumentRecord(source_type="news", title="삼성전자 계약", content=content,
        url="https://news.example/1", published_at="2026-09-05T10:00:00+09:00",
        metadata={"published_at_precision": "datetime", "published_at_source": "article:published_time",
                  "publication_time_status": "confirmed", "evidence_scope": "document",
                  "body_source": "article", "body_extracted": True})


def install_news(monkeypatch, batches):
    calls = []

    class News:
        def collect(self, keyword, max_items, from_date, to_date):
            calls.append((keyword, max_items, from_date, to_date))
            batch = batches[min(len(calls) - 1, len(batches) - 1)]
            if isinstance(batch, Exception):
                raise batch
            return [replace(row, metadata=dict(row.metadata)) for row in batch]

    monkeypatch.setattr("src.ingestion.services.NaverNewsCollector", News)
    return calls


def expire_cache(tmp_path):
    for path in (tmp_path / "collection_state").glob("*.json"):
        state = json.loads(path.read_text(encoding="utf-8"))
        state["completed_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(state), encoding="utf-8")


def test_two_themes_share_one_collection_and_preserve_archive_episode_identity(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [[article()]])
    service = IngestionService()
    first = service.collect(request(tmp_path))
    second = service.collect(request(tmp_path, "second"))
    assert len(calls) == 1
    assert first.report.source_success["news"] is second.report.source_success["news"] is True
    assert first.report.cache_hits["news"] is False
    assert second.report.cache_hits["news"] is True
    shared = read_rows(tmp_path / "raw/news/_shared_005930.jsonl")
    initial = read_rows(tmp_path / "raw/news/first.jsonl")
    projected = read_rows(tmp_path / "raw/news/second.jsonl")
    assert len(shared) == len(initial) == len(projected) == 1
    assert shared[0]["metadata"]["version_id"] == initial[0]["metadata"]["version_id"] == projected[0]["metadata"]["version_id"]
    assert initial[0]["metadata"]["collected_at"] == projected[0]["metadata"]["collected_at"]
    assert projected[0]["metadata"]["theme_key"] == "second"
    assert second.documents[0].metadata["theme_key"] == "second"
    assert "must-not-write-this-key" not in next((tmp_path / "collection_state").glob("*.json")).read_text()


def test_repeated_cached_projection_does_not_duplicate_episodes(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [[article()]])
    service = IngestionService()
    service.collect(request(tmp_path))
    result = service.collect(request(tmp_path))
    assert len(calls) == 1
    assert result.report.raw_saved_counts["news"] == 0
    assert len(read_rows(tmp_path / "raw/news/first.jsonl")) == 1


def test_new_theme_receives_full_revision_history_not_just_cached_latest_result(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [[article("삼성전자는 계약 A를 체결했다.")],
                                      [article("삼성전자는 계약 B를 체결했다.")],
                                      [article("삼성전자는 계약 A를 체결했다.")]])
    times = iter(["2026-09-05T01:00:00+00:00", "2026-09-05T02:00:00+00:00", "2026-09-05T03:00:00+00:00"])
    monkeypatch.setattr(IngestionService, "_utc_timestamp", staticmethod(lambda: next(times)))
    service = IngestionService()
    for _ in range(3):
        expire_cache(tmp_path)
        service.collect(request(tmp_path))
    projected = service.collect(request(tmp_path, "new_theme"))
    assert len(calls) == 3
    assert projected.report.cache_hits["news"] is True
    shared = read_rows(tmp_path / "raw/news/_shared_005930.jsonl")
    rows = read_rows(tmp_path / "raw/news/new_theme.jsonl")
    assert [row["content"] for row in rows] == ["삼성전자는 계약 A를 체결했다.", "삼성전자는 계약 B를 체결했다.", "삼성전자는 계약 A를 체결했다."]
    assert [row["metadata"]["version_id"] for row in rows] == [row["metadata"]["version_id"] for row in shared]
    assert len(set(row["metadata"]["version_id"] for row in rows)) == 3


def test_failed_collection_is_not_cached_as_no_data_or_success(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [RuntimeError("news_search_failed:page=1:TimeoutError"), [article()]])
    service = IngestionService()
    failed = service.collect(request(tmp_path))
    assert failed.report.source_success["news"] is False
    assert failed.report.source_status["news"] == "error"
    assert list((tmp_path / "collection_state").glob("*.json")) == []
    success = service.collect(request(tmp_path))
    assert len(calls) == 2
    assert success.report.source_success["news"] is True
    assert success.report.cache_hits["news"] is False


def test_provider_no_data_does_not_hide_new_data_for_fifteen_minutes(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [[], [article()]])
    service = IngestionService()
    empty = service.collect(request(tmp_path))
    assert empty.report.source_status["news"] == "no_data"
    assert list((tmp_path / "collection_state").glob("*.json")) == []
    populated = service.collect(request(tmp_path, "second"))
    assert len(calls) == 2
    assert len(populated.documents) == 1


def test_unrelated_news_is_quarantined_before_stock_assignment_and_scope_is_preserved(tmp_path, monkeypatch):
    unrelated = replace(article(), title="삼성전자서비스 계약", content="삼성전자서비스는 새로운 계약을 발표했다.")
    summary = replace(article(), metadata={**article().metadata, "evidence_scope": "summary",
                                           "body_source": "meta_description", "body_extracted": False})
    install_news(monkeypatch, [[summary, unrelated]])
    result = IngestionService().collect(replace(request(tmp_path), incremental=False))
    assert len(result.documents) == 1
    assert result.documents[0].stock_code == "005930"
    assert result.documents[0].metadata["evidence_scope"] == "summary"
    assert result.documents[0].metadata["entity_match"] == {"matched": True, "method": "canonical_name"}
    assert result.report.rejected_counts["news"] == 1
    quarantined = read_rows(tmp_path / "raw/quarantine/news/first.jsonl")
    assert len(quarantined) == 1
    assert quarantined[0]["stock_code"] is None
    assert quarantined[0]["metadata"]["requested_stock_code"] == "005930"
    assert quarantined[0]["metadata"]["quarantine_reason"] == "unverified_news_subject"


def test_chart_cursor_uses_overlap_but_new_theme_hydrates_earlier_bars(tmp_path, monkeypatch):
    calls = []

    class Chart:
        def collect_daily(self, stock_name, stock_code, from_date, to_date):
            calls.append((from_date, to_date))
            return [MarketRecord(source_type="chart", stock_name=stock_name, stock_code=stock_code,
                timestamp=date + "T15:30:00+09:00", open="100", high="110", low="90", close=close,
                volume="1000", metadata={"source": "krx"}) for date, close in
                ([('2026-08-01', '100'), ('2026-09-01', '100')] if len(calls) == 1
                 else [('2026-09-01', '101'), ('2026-09-04', '105')])]

    times = iter([f"2026-09-05T0{hour}:00:00+00:00" for hour in range(1, 5)])
    monkeypatch.setattr(IngestionService, "_utc_timestamp", staticmethod(lambda: next(times)))
    service = IngestionService(krx_chart_collector=Chart())
    first = replace(request(tmp_path, sources=["chart"]), to_date="20260901")
    service.collect(first)
    second = service.collect(request(tmp_path, "second", ["chart"]))
    assert second.report.source_success["chart"] is True
    assert calls == [("20260801", "20260901"), ("20260825", "20260905")]
    shared = read_rows(tmp_path / "raw/chart/_shared_005930.jsonl")
    projected = read_rows(tmp_path / "raw/chart/second.jsonl")
    assert len(shared) == len(projected) == 4
    assert [row["close"] for row in projected] == ["100", "100", "101", "105"]
    assert [row["metadata"]["version_id"] for row in shared] == [row["metadata"]["version_id"] for row in projected]


def test_concurrent_theme_requests_share_source_call_and_hydrate_each_theme(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [[article()]])
    barrier = Barrier(6)

    def collect(index):
        barrier.wait(timeout=10)
        return IngestionService().collect(request(tmp_path, f"theme_{index}"))

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(collect, range(6)))
    assert len(calls) == 1
    assert all(result.report.source_success["news"] for result in results)
    assert sum(not result.report.cache_hits["news"] for result in results) == 1
    identities = {read_rows(tmp_path / f"raw/news/theme_{index}.jsonl")[0]["metadata"]["version_id"]
                  for index in range(6)}
    assert len(identities) == 1


def test_source_failure_does_not_invalidate_other_sources_successful_cache(tmp_path, monkeypatch):
    news_calls = install_news(monkeypatch, [[article()]])
    chart_calls = []

    class Chart:
        def collect_daily(self, **kwargs):
            chart_calls.append(kwargs)
            raise RuntimeError("chart_provider_unavailable")

    service = IngestionService(krx_chart_collector=Chart())
    for theme in ("first", "second"):
        result = service.collect(request(tmp_path, theme, ["news", "chart"]))
        assert result.report.source_status == {"news": "success", "chart": "error"}
    assert len(news_calls) == 1
    assert len(chart_calls) == 2
    assert len(list((tmp_path / "collection_state").glob("*.json"))) == 1


def test_corrupt_cached_state_fails_clearly_without_network_or_overwrite(tmp_path, monkeypatch):
    calls = install_news(monkeypatch, [[article()]])
    service = IngestionService()
    service.collect(request(tmp_path))
    path = next((tmp_path / "collection_state").glob("*.json"))
    path.write_text('{"identity":', encoding="utf-8")
    result = service.collect(request(tmp_path))
    assert result.report.source_success["news"] is False
    assert result.report.source_status["news"] == "error"
    assert len(calls) == 1
    assert path.read_text(encoding="utf-8") == '{"identity":'


def test_financial_shared_projection_keeps_same_episodes_in_market_mirror(tmp_path):
    calls = []

    class Financial:
        def collect_annual_series(self, **kwargs):
            calls.append(kwargs)
            return [FinancialSnapshot(source_type="financials", stock_name="삼성전자", stock_code="005930",
                corp_code="00126380", fiscal_year="2025", report_code="11011", report_name="Annual",
                revenue=100, metadata={"fs_div": "CFS", "source": "dart"})]

    service = IngestionService(financial_collector=Financial())
    service.collect(request(tmp_path, "first", ["financials"]))
    result = service.collect(request(tmp_path, "second", ["financials"]))
    assert result.report.source_success["financials"] is True
    assert len(calls) == 1
    shared = read_rows(tmp_path / "raw/financials/_shared_005930.jsonl")
    projected = read_rows(tmp_path / "raw/financials/second.jsonl")
    mirror = read_rows(tmp_path / "market_data/second/financials.jsonl")
    assert projected == mirror
    assert shared[0]["metadata"]["version_id"] == projected[0]["metadata"]["version_id"]


@pytest.mark.parametrize("empty_archive", [False, True])
def test_positive_cache_cannot_publish_success_when_authoritative_archive_is_missing(tmp_path, monkeypatch, empty_archive):
    calls = install_news(monkeypatch, [[article()]])
    service = IngestionService()
    service.collect(request(tmp_path))
    archive = tmp_path / "raw/news/_shared_005930.jsonl"
    if empty_archive:
        archive.write_text("", encoding="utf-8")
    else:
        archive.unlink()
    result = service.collect(request(tmp_path, "second"))
    assert len(calls) == 1
    assert result.report.source_success["news"] is False
    assert result.report.source_status["news"] == "error"
    assert "missing_shared_archive" in result.report.failures["news"]
    assert result.documents == []
    assert not (tmp_path / "raw/news/second.jsonl").exists()
