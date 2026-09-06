import json
from dataclasses import replace
from typing import Any, get_type_hints

import pytest

from src.ingestion import types as ingestion_types
from src.ingestion.services import IngestionService
from src.ingestion.types import CollectRequest, DocumentRecord, FinancialSnapshot, MarketRecord, StockTarget


class _FakeKrxCollector:
    def collect_daily(self, stock_name: str, stock_code: str, from_date: str, to_date: str):
        assert stock_name == "삼성전자"
        assert stock_code == "005930"
        assert from_date == "20250101"
        assert to_date == "20251231"
        return [
            MarketRecord(
                source_type="chart",
                stock_name=stock_name,
                stock_code=stock_code,
                timestamp="2025-12-30T00:00:00",
                open="70000",
                high="71000",
                low="69000",
                close="70500",
                volume="1234567",
                metadata={"source": "krx", "raw_date": "20251230"},
            )
        ]


class _FakeFinancialCollector:
    def collect_annual_series(self, stock_name: str, stock_code: str, corp_code: str, from_date: str, to_date: str, years: int = 3):
        assert corp_code == "00126380"
        assert years == 3
        return [
            FinancialSnapshot(
                source_type="financials",
                stock_name=stock_name,
                stock_code=stock_code,
                corp_code=corp_code,
                fiscal_year=str(year),
                report_code="11011",
                report_name="사업보고서",
                revenue=float(revenue),
                operating_profit=float(profit),
                roe=10.0,
                operating_margin=10.0,
                debt_ratio=30.0,
                currency="KRW",
                as_of=f"{year}-12-31",
                metadata={"source": "dart"},
            )
            for year, revenue, profit in [
                (2025, 150, 30),
                (2024, 120, 20),
                (2023, 100, 10),
            ]
        ]

    def collect_latest_annual(self, stock_name: str, stock_code: str, corp_code: str, from_date: str, to_date: str):
        assert corp_code == "00126380"
        return FinancialSnapshot(
            source_type="financials",
            stock_name=stock_name,
            stock_code=stock_code,
            corp_code=corp_code,
            fiscal_year="2025",
            report_code="11011",
            report_name="사업보고서",
            revenue=333605900000000.0,
            operating_profit=43601000000000.0,
            net_income=45206800000000.0,
            assets=550000000000000.0,
            liabilities=124000000000000.0,
            equity=416000000000000.0,
            roe=10.87,
            operating_margin=13.07,
            net_margin=13.55,
            debt_ratio=29.81,
            currency="KRW",
            as_of="2025-12-31",
            metadata={"source": "dart"},
        )


def _request(tmp_path, enabled_sources):
    return CollectRequest(
        target=StockTarget("삼성전자", "005930", "00126380"),
        max_news=0,
        forum_pages=0,
        chart_pages=0,
        from_date="20250101",
        to_date="20251231",
        dart_api_key="dart-key",
        theme_key="semiconductor",
        enabled_sources=enabled_sources,
        raw_output_dir=str(tmp_path / "raw"),
    )


def test_chart_source_collects_krx_rows_to_raw_chart(tmp_path):
    service = IngestionService(krx_chart_collector=_FakeKrxCollector())

    result = service.collect(_request(tmp_path, ["chart"]))

    assert result.report.source_success["chart"] is True
    assert result.report.source_counts["chart"] == 1
    assert result.report.raw_saved_counts["chart"] == 1
    assert result.report.skipped_counts["chart"] == 0
    assert result.market_records[0].metadata["source"] == "krx"

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "chart" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["stock_code"] == "005930"
    assert rows[0]["metadata"]["source"] == "krx"


def test_chart_source_dedupes_raw_rows_by_stock_and_timestamp(tmp_path):
    service = IngestionService(krx_chart_collector=_FakeKrxCollector())

    first = service.collect(_request(tmp_path, ["chart"]))
    second = service.collect(_request(tmp_path, ["chart"]))

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "chart" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(rows) == 1
    assert first.report.raw_saved_counts["chart"] == 1
    assert first.report.skipped_counts["chart"] == 0
    assert second.report.raw_saved_counts["chart"] == 0
    assert second.report.skipped_counts["chart"] == 1


def test_financials_source_collects_dart_snapshots_to_raw_financials(tmp_path):
    service = IngestionService(financial_collector=_FakeFinancialCollector())

    result = service.collect(_request(tmp_path, ["financials"]))

    assert result.report.source_success["financials"] is True
    assert result.report.source_counts["financials"] == 3
    assert result.report.raw_saved_counts["financials"] == 3
    assert result.report.skipped_counts["financials"] == 0
    assert [snapshot.fiscal_year for snapshot in result.financial_snapshots] == ["2025", "2024", "2023"]

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "financials" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["fiscal_year"] for row in rows] == ["2025", "2024", "2023"]
    assert rows[0]["source_type"] == "financials"
    assert rows[0]["metadata"]["source"] == "dart"

    normalized_rows = [
        json.loads(line)
        for line in (tmp_path / "market_data" / "semiconductor" / "financials.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["fiscal_year"] for row in normalized_rows] == ["2025", "2024", "2023"]
    assert normalized_rows[0]["stock_code"] == "005930"
    assert normalized_rows[0]["revenue"] == 150.0


def test_financials_source_dedupes_raw_snapshots_by_stock_year_and_report(tmp_path):
    service = IngestionService(financial_collector=_FakeFinancialCollector())

    first = service.collect(_request(tmp_path, ["financials"]))
    second = service.collect(_request(tmp_path, ["financials"]))

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "financials" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(rows) == 3
    assert sorted((row["stock_code"], row["fiscal_year"], row["report_code"]) for row in rows) == [
        ("005930", "2023", "11011"),
        ("005930", "2024", "11011"),
        ("005930", "2025", "11011"),
    ]
    assert first.report.raw_saved_counts["financials"] == 3
    assert first.report.skipped_counts["financials"] == 0
    assert second.report.raw_saved_counts["financials"] == 0
    assert second.report.skipped_counts["financials"] == 3


def test_news_raw_save_retains_changed_title_at_same_url(tmp_path):
    service = IngestionService()
    raw_dir = str(tmp_path / "raw")
    first_docs = [
        DocumentRecord(
            source_type="news",
            title="기존 뉴스",
            content="기존 뉴스 본문입니다.",
            url="https://news.example.com/a",
            stock_code="005930",
            published_at="2025-01-01T00:00:00",
        )
    ]
    next_docs = [
        DocumentRecord(
            source_type="news",
            title="기존 뉴스 재수집",
            content="기존 뉴스 본문입니다.",
            url="https://news.example.com/a",
            stock_code="005930",
            published_at="2025-01-01T00:00:00",
        ),
        DocumentRecord(
            source_type="news",
            title="신규 뉴스",
            content="신규 뉴스 본문입니다.",
            url="https://news.example.com/b",
            stock_code="005930",
            published_at="2025-01-02T00:00:00",
        ),
    ]

    assert service._save_raw_documents(first_docs, raw_dir, "news", "semiconductor") == 1
    assert service._save_raw_documents(next_docs, raw_dir, "news", "semiconductor") == 2

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "news" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert [row["url"] for row in rows] == [
        "https://news.example.com/a",
        "https://news.example.com/a",
        "https://news.example.com/b",
    ]


def _read_documents(tmp_path, source="news"):
    return [json.loads(line) for line in (tmp_path / "raw" / source / "semiconductor.jsonl")
            .read_text(encoding="utf-8").splitlines()]


def test_raw_documents_dedupe_first_batch_and_preserve_first_observation(tmp_path):
    service = IngestionService()
    first = DocumentRecord("news", "Headline", "Same factual body", "https://news.example.com/a",
                           stock_code="005930", published_at="2026-09-04T10:00:00+09:00",
                           metadata={"collected_at": "2026-09-04T10:01:00+09:00", "theme_key": "first",
                                     "freshness_score": 1.0, "credibility_score": 0.9, "content_quality_score": 0.8})
    recollected = replace(first, metadata={"collected_at": "2026-09-04T10:20:00+09:00", "theme_key": "second",
                                         "freshness_score": 0.5, "credibility_score": 0.8, "content_quality_score": 0.9})
    args = (str(tmp_path / "raw"), "news", "semiconductor")
    assert service._save_raw_documents([first, first, recollected], *args) == 1
    assert service._save_raw_documents([recollected], *args) == 0
    rows = _read_documents(tmp_path)
    assert len(rows) == 1
    assert rows[0]["metadata"]["collected_at"] == "2026-09-04T10:01:00+09:00"
    assert rows[0]["metadata"]["theme_key"] == "first"


def test_raw_news_retains_body_revisions_and_distinct_stock_associations(tmp_path):
    service = IngestionService()
    first = DocumentRecord("news", "Headline", "Original body", "https://news.example.com/a", stock_code="005930",
                           metadata={"collected_at": "2026-09-04T10:01:00+09:00"})
    revision = replace(first, content="Corrected body", metadata={"collected_at": "2026-09-04T10:02:00+09:00"})
    second_stock = replace(first, stock_code="000660", metadata={"collected_at": "2026-09-04T10:03:00+09:00"})
    args = (str(tmp_path / "raw"), "news", "semiconductor")
    assert service._save_raw_documents([first], *args) == 1
    assert service._save_raw_documents([revision, second_stock, revision, second_stock], *args) == 2
    rows = _read_documents(tmp_path)
    assert [(row["stock_code"], row["content"]) for row in rows] == [
        ("005930", "Original body"), ("005930", "Corrected body"), ("000660", "Original body"),
    ]
    assert [row["metadata"]["collected_at"] for row in rows] == [
        "2026-09-04T10:01:00+09:00", "2026-09-04T10:02:00+09:00", "2026-09-04T10:03:00+09:00",
    ]


@pytest.mark.parametrize(("field", "value"), [
    ("structured_row", {"rcept_no": "20260904000001", "nstk_ostk_cnt": "20,000"}),
    ("structured_endpoint", "piicDecsn"),
    ("has_correction", True),
    ("is_withdrawal", True),
    ("published_at_precision", "datetime"),
    ("supersedes_source_ids", ["dart:20260903000001"]),
])
def test_dart_event_metadata_changes_keep_their_own_first_available_observation(tmp_path, field, value):
    service = IngestionService()
    metadata = {"rcept_no": "20260904000001", "published_at_precision": "date", "has_correction": False,
                "collected_at": "2026-09-04T10:01:00+09:00"}
    first = DocumentRecord("dart", "Report", "Same body", "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260904000001",
                           stock_code="005930", published_at="2026-09-04", metadata=metadata)
    revision = replace(first, metadata={**metadata, field: value, "collected_at": "2026-09-04T11:00:00+09:00"})
    recollected = replace(revision, metadata={**revision.metadata, "collected_at": "2026-09-04T12:00:00+09:00"})
    args = (str(tmp_path / "raw"), "dart", "semiconductor")
    assert service._save_raw_documents([first], *args) == 1
    assert service._save_raw_documents([revision, recollected], *args) == 1
    assert service._save_raw_documents([recollected], *args) == 0
    rows = _read_documents(tmp_path, "dart")
    assert len(rows) == 2
    assert rows[0]["metadata"]["collected_at"] == "2026-09-04T10:01:00+09:00"
    assert rows[1]["metadata"]["collected_at"] == "2026-09-04T11:00:00+09:00"
    assert rows[1]["metadata"][field] == value


def test_revision_hash_ignores_metadata_key_order_but_keeps_corrected_publication_time(tmp_path):
    service = IngestionService()
    first = DocumentRecord("dart", "Report", "Body", "https://dart.example/a", stock_code="005930",
                           published_at="2026-09-04", metadata={"rcept_no": "20260904000001",
                           "structured_row": {"rcept_no": "20260904000001", "amount": "10"}})
    reordered = replace(first, metadata={"structured_row": {"amount": "10", "rcept_no": "20260904000001"},
                                        "rcept_no": "20260904000001"})
    corrected_time = replace(first, published_at="2026-09-04T14:00:00+09:00")
    args = (str(tmp_path / "raw"), "dart", "semiconductor")
    assert service._save_raw_documents([first, reordered, corrected_time], *args) == 2


@pytest.mark.parametrize("one_batch", [True, False])
def test_raw_reappearance_creates_new_episode_and_unchanged_repeat_keeps_first_observation(tmp_path, one_batch):
    service = IngestionService()
    first = DocumentRecord("news", "Headline", "Content A", "https://news.example.com/a", stock_code="005930",
                           metadata={"collected_at": "2026-09-04T10:00:00+09:00"})
    changed = replace(first, content="Content B", metadata={"collected_at": "2026-09-04T11:00:00+09:00"})
    returned = replace(first, metadata={"collected_at": "2026-09-04T12:00:00+09:00"})
    repeat = replace(returned, metadata={"collected_at": "2026-09-04T13:00:00+09:00"})
    args = (str(tmp_path / "raw"), "news", "semiconductor")
    if one_batch:
        assert service._save_raw_documents([first, changed, returned, repeat], *args) == 3
    else:
        assert [service._save_raw_documents([doc], *args) for doc in [first, changed, returned, repeat]] == [1, 1, 1, 0]
    assert service._save_raw_documents([repeat], *args) == 0
    rows = _read_documents(tmp_path)
    assert [row["content"] for row in rows] == ["Content A", "Content B", "Content A"]
    assert len({row["metadata"]["version_id"] for row in rows}) == 3
    assert rows[-1]["metadata"]["collected_at"] == "2026-09-04T12:00:00+09:00"
    assert "version_id" not in first.metadata
    assert "version_id" not in returned.metadata


def test_source_dedupe_keys_are_explicit():
    assert IngestionService._document_dedupe_key(
        DocumentRecord(
            source_type="news",
            title="뉴스",
            content="본문",
            url="https://news.example.com/a",
            stock_code="005930",
            published_at="2025-01-01T00:00:00",
        ),
        "news",
    ) == "news|https://news.example.com/a"
    assert IngestionService._document_dedupe_key(
        DocumentRecord(
            source_type="dart",
            title="공시",
            content="본문",
            url="https://dart.example.com/a",
            stock_code="005930",
            metadata={"rcept_no": "20250101000001"},
        ),
        "dart",
    ) == "dart|20250101000001"
    assert IngestionService._document_dedupe_key(
        DocumentRecord(
            source_type="forum",
            title="토론글",
            content="본문",
            url="https://forum.example.com/a",
            stock_code="005930",
            published_at="2025-01-01T00:00:00",
        ),
        "forum",
    ) == "forum|https://forum.example.com/a"
    assert IngestionService._market_record_key(
        MarketRecord(
            source_type="chart",
            stock_name="삼성전자",
            stock_code="005930",
            timestamp="2025-12-30T00:00:00",
            open="70000",
            high="71000",
            low="69000",
            close="70500",
            volume="1234567",
        )
    ) == "chart|005930|2025-12-30T00:00:00|daily"


def test_metadata_types_allow_non_string_values():
    assert get_type_hints(ingestion_types.DocumentRecord)["metadata"] == dict[str, Any]
    assert get_type_hints(ingestion_types.MarketRecord)["metadata"] == dict[str, Any]
    assert get_type_hints(ingestion_types.FinancialSnapshot)["metadata"] == dict[str, Any]


@pytest.mark.parametrize(
    ("from_date", "to_date"),
    [
        ("2025-01-01", "20251231"),
        ("20250230", "20251231"),
        ("20251231", "20250101"),
    ],
)
def test_collect_rejects_invalid_date_ranges(tmp_path, from_date, to_date):
    request = _request(tmp_path, [])
    request.from_date = from_date
    request.to_date = to_date

    with pytest.raises(ValueError, match="YYYYMMDD|from_date"):
        IngestionService().collect(request)
