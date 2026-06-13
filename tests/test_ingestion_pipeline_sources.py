import json

from src.ingestion.services import IngestionService
from src.ingestion.types import CollectRequest, FinancialSnapshot, MarketRecord, StockTarget


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
    assert result.market_records[0].metadata["source"] == "krx"

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "chart" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["stock_code"] == "005930"
    assert rows[0]["metadata"]["source"] == "krx"


def test_financials_source_collects_dart_snapshot_to_raw_financials(tmp_path):
    service = IngestionService(financial_collector=_FakeFinancialCollector())

    result = service.collect(_request(tmp_path, ["financials"]))

    assert result.report.source_success["financials"] is True
    assert result.report.source_counts["financials"] == 1
    assert result.financial_snapshots[0].roe == 10.87

    rows = [
        json.loads(line)
        for line in (tmp_path / "raw" / "financials" / "semiconductor.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["source_type"] == "financials"
    assert rows[0]["metadata"]["source"] == "dart"

    normalized_rows = [
        json.loads(line)
        for line in (tmp_path / "market_data" / "semiconductor" / "financials.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert normalized_rows[0]["stock_code"] == "005930"
    assert normalized_rows[0]["roe"] == 10.87
