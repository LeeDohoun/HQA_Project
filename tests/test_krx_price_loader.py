from src.data_pipeline import price_loader
from src.ingestion.types import MarketRecord


class _FakeKrxChartCollector:
    def collect_recent_daily(self, stock_name: str, stock_code: str, days: int):
        assert stock_code == "005930"
        return [
            MarketRecord(
                source_type="chart",
                stock_name="삼성전자",
                stock_code="005930",
                timestamp="2026-06-11T00:00:00",
                open="70000",
                high="71000",
                low="69000",
                close="70500",
                volume="1234567",
                metadata={"source": "krx"},
            ),
            MarketRecord(
                source_type="chart",
                stock_name="삼성전자",
                stock_code="005930",
                timestamp="2026-06-12T00:00:00",
                open="70500",
                high="72000",
                low="70000",
                close="71500",
                volume="2345678",
                metadata={"source": "krx"},
            ),
        ]


def test_price_loader_prefers_krx_when_api_key_is_configured(monkeypatch, tmp_path):
    local_chart = tmp_path / "market_data" / "semiconductor" / "chart.jsonl"
    local_chart.parent.mkdir(parents=True)
    local_chart.write_text(
        '{"source_type":"chart","stock_name":"삼성전자","stock_code":"005930",'
        '"timestamp":"2026-06-12T00:00:00","open":"300000","high":"310000",'
        '"low":"290000","close":"305000","volume":"1","metadata":{"source":"naver"}}\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("KRX_OPEN_API_KEY", "krx-key")
    monkeypatch.setattr(price_loader, "KrxChartCollector", _FakeKrxChartCollector)

    df = price_loader.PriceLoader(data_dir=str(tmp_path)).get_stock_data("005930", days=300)

    assert len(df) == 2
    assert df["Close"].iloc[-1] == 71500
