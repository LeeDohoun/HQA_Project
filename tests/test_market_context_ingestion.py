import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import collect_market_context
from src.ingestion.krx_chart import KrxChartCollector
from src.runner.analysis_data import price_features

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


def prices():
    return [{"timestamp": (NOW - timedelta(days=149 - index)).date().isoformat(),
             "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
            for index in range(150)]


def provenance(market="KOSPI"):
    return {"source": "krx", "market": market, "price_basis": "unadjusted",
            "source_url": KrxChartCollector.KOSPI_DAILY_URL if market == "KOSPI" else KrxChartCollector.KOSDAQ_DAILY_URL}


@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
def test_stock_market_identity_comes_from_actual_collector_endpoint(monkeypatch, market):
    collector = KrxChartCollector("fixture-key")
    raw = {"ISU_CD": "005930", "BAS_DD": "20260904", "TDD_OPNPRC": "100", "TDD_HGPRC": "101",
           "TDD_LWPRC": "99", "TDD_CLSPRC": "100", "ACC_TRDVOL": "1000"}
    endpoint = provenance(market)["source_url"]
    monkeypatch.setattr(collector, "_fetch_market_rows", lambda url, day: [raw] if url == endpoint else [])
    record = collector.collect_daily("Stock", "005930", "20260904", "20260904")[0]
    assert record.metadata == {**provenance(market), "raw_date": "20260904"}
    assert "_source_market" not in raw


@pytest.mark.parametrize("reverse", [False, True])
def test_same_prices_across_legacy_and_verified_themes_preserve_provenance(reverse):
    legacy = prices()
    verified = [{**row, "metadata": provenance()} for row in legacy]
    _, normalized = price_features(verified + legacy if reverse else legacy + verified, NOW)
    assert len(normalized) == 150
    assert all(all(row[key] == value for key, value in provenance().items()) for row in normalized)


def test_legacy_prices_do_not_acquire_a_guessed_market():
    _, normalized = price_features(prices(), NOW)
    assert all("market" not in row and "source_url" not in row for row in normalized)


@pytest.mark.parametrize("change", [{"source_url": "https://example.org"}, {"price_basis": "adjusted"}])
def test_price_market_requires_matching_endpoint_and_raw_basis(change):
    rows = [{**row, "metadata": {**provenance(), **change}} for row in prices()]
    with pytest.raises(ValueError, match="provenance"):
        price_features(rows, NOW)


def test_conflicting_verified_markets_are_not_merged():
    rows = prices()
    rows += [{**rows[-1], "metadata": provenance(market)} for market in ("KOSPI", "KOSDAQ")]
    with pytest.raises(ValueError, match="conflicting OHLCV"):
        price_features(rows, NOW)


def test_cli_uses_shared_archive_and_selected_series(monkeypatch, tmp_path, capsys):
    from src.ingestion.krx_benchmarks import _record
    row = _record("KOSPI", "index-fixture", NOW.date(), 100.0, NOW)

    class Collector:
        def collect_daily(self, start, end, series):
            assert (start, end, series) == ("20260904", "20260904", ("KOSPI",))
            return [row]

    monkeypatch.setattr(collect_market_context, "KrxBenchmarkCollector", Collector)
    monkeypatch.setattr(collect_market_context, "load_project_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["collect_market_context", "--from-date", "20260904", "--to-date", "20260904",
                                    "--series", "KOSPI", "--data-dir", str(tmp_path)])
    collect_market_context.main()
    output = json.loads(capsys.readouterr().out)
    assert output["collected_records"] == output["saved_records"] == 1
    assert json.loads((tmp_path / "market_context" / "benchmarks.jsonl").read_text()) == row


def test_cli_missing_credentials_fails_without_creating_data(monkeypatch, tmp_path):
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    monkeypatch.setattr(collect_market_context, "load_project_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["collect_market_context", "--from-date", "20260904", "--to-date", "20260904",
                                    "--data-dir", str(tmp_path)])
    with pytest.raises(ValueError, match="required"):
        collect_market_context.main()
    assert not (tmp_path / "market_context").exists()
