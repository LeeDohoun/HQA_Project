import json
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


def test_news_raw_save_appends_only_new_urls(tmp_path):
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
    assert service._save_raw_documents(next_docs, raw_dir, "news", "semiconductor") == 1

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "news" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert [row["url"] for row in rows] == [
        "https://news.example.com/a",
        "https://news.example.com/b",
    ]


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
    ) == "forum|005930|토론글|2025-01-01T00:00:00"
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
