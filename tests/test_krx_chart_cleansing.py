from datetime import datetime, timedelta, timezone

import pytest
import requests

from src.ingestion import krx_chart
from src.ingestion.krx_chart import KrxChartCollector

NOW = datetime(2026, 9, 6, 3, tzinfo=timezone.utc)


def stock_row(**changes):
    return {"ISU_CD": "005930", "ISU_NM": "Stock", "BAS_DD": "20260904",
            "TDD_OPNPRC": "100", "TDD_HGPRC": "102", "TDD_LWPRC": "99",
            "TDD_CLSPRC": "101", "ACC_TRDVOL": "1,000", **changes}


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW)


@pytest.mark.parametrize("payload", [
    {"error": "quota"}, {"OutBlock_1": None}, {"output": []}, [],
    {"OutBlock_1": [], "error": "invalid key"}, {"OutBlock_1": [None]},
    {"OutBlock_1": [{"ISU_CD": "005930"}]}, ValueError("secret-provider-payload"),
])
def test_provider_errors_and_malformed_responses_are_never_empty_success(payload):
    collector = KrxChartCollector("secret-key", Session(Response(payload)))
    with pytest.raises(ValueError) as error:
        collector._fetch_market_rows(collector.KOSPI_DAILY_URL, "20260904")
    assert "secret" not in str(error.value)
    assert not collector._daily_cache


@pytest.mark.parametrize("row", [stock_row(BAS_DD="20260903"), stock_row(TDD_CLSPRC="-"),
                                stock_row(ACC_TRDVOL=True), stock_row(TDD_OPNPRC="NaN"),
                                stock_row(ISU_NM="echo secret-key")])
def test_invalid_rows_fail_without_leaking_payload(row):
    collector = KrxChartCollector("secret-key", Session(Response({"OutBlock_1": [row]})))
    with pytest.raises(ValueError) as error:
        collector._fetch_market_rows(collector.KOSPI_DAILY_URL, "20260904")
    assert "secret-key" not in str(error.value)


@pytest.mark.parametrize("response", [Response({}, 302), Response({}, 429),
                                     requests.ConnectionError("https://example.test?AUTH_KEY=secret-key")])
def test_transport_failures_and_redirects_are_sanitized(response):
    session = Session(response)
    collector = KrxChartCollector("secret-key", session)
    with pytest.raises(requests.RequestException) as error:
        collector._fetch_market_rows(collector.KOSPI_DAILY_URL, "20260904")
    assert "secret-key" not in str(error.value) and "https://" not in str(error.value)
    assert session.calls[0][1]["allow_redirects"] is False


def test_empty_response_is_not_cached_and_later_publication_can_be_collected():
    session = Session(Response({"OutBlock_1": []}), Response({"OutBlock_1": [stock_row()]}))
    collector = KrxChartCollector("secret-key", session)
    assert collector._fetch_market_rows(collector.KOSPI_DAILY_URL, "20260904") == []
    assert collector._fetch_market_rows(collector.KOSPI_DAILY_URL, "20260904")[0]["TDD_CLSPRC"] == "101"
    assert len(session.calls) == 2


def test_market_cache_is_shared_across_stocks_but_expires_for_revisions(monkeypatch):
    session = Session(Response({"OutBlock_1": [stock_row()]}),
                      Response({"OutBlock_1": [stock_row(TDD_CLSPRC="102")]}))
    collector = KrxChartCollector("secret-key", session)
    first = collector.collect_daily("Stock", "005930", "20260904", "20260904")[0]
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW + timedelta(minutes=14))
    unchanged = collector.collect_daily("Stock", "005930", "20260904", "20260904")[0]
    assert first == unchanged and len(session.calls) == 1
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW + timedelta(minutes=15))
    revised = collector.collect_daily("Stock", "005930", "20260904", "20260904")[0]
    assert revised.close == "102" and revised.metadata["version"] != first.metadata["version"]
    assert revised.metadata["collected_at"] != first.metadata["collected_at"]
    assert first.metadata["trade_date"] == "2026-09-04"
    assert first.metadata["bar_at"] == "2026-09-04T15:30:00+09:00"
    assert first.metadata["available_at"] == NOW.isoformat()
    assert first.metadata["source_id"] == "krx-chart:" + first.metadata["version"]
    assert first.volume == "1000"


def test_content_version_does_not_change_on_unchanged_reobservation(monkeypatch):
    session = Session(*[Response({"OutBlock_1": [stock_row()]}) for _ in range(2)])
    collector = KrxChartCollector("secret-key", session)
    first = collector.collect_daily("Stock", "005930", "20260904", "20260904")[0]
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW + timedelta(minutes=15))
    second = collector.collect_daily("Stock", "005930", "20260904", "20260904")[0]
    assert first.metadata["version"] == second.metadata["version"]
    assert first.metadata["collected_at"] != second.metadata["collected_at"]


def test_collector_persists_verified_special_close_notice_not_fixed_1530():
    session = Session(Response({"OutBlock_1": [stock_row(BAS_DD="20251113")]}))
    collector = KrxChartCollector("secret-key", session)
    row = collector.collect_daily("Stock", "005930", "20251113", "20251113")[0]
    assert row.metadata["bar_at"] == "2025-11-13T16:30:00+09:00"
    assert row.metadata["calendar_notice"]["published_at"] == "2025-10-30T10:00:00+09:00"
    assert set(row.metadata["calendar_notice"]["source_urls"]) == {"KOSPI", "KOSDAQ"}


def test_conflicting_duplicate_stock_date_rows_fail():
    session = Session(Response({"OutBlock_1": [stock_row(), stock_row(TDD_CLSPRC="102")]}))
    collector = KrxChartCollector("secret-key", session)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        collector._fetch_market_rows(collector.KOSPI_DAILY_URL, "20260904")


@pytest.mark.parametrize("start,end", [("20260906", "20260906"), ("20260907", "20260907"),
                                     ("20260904", "20260903"), ("2026-09-04", "20260904")])
def test_invalid_or_uncompleted_ranges_make_no_request(start, end):
    session = Session()
    collector = KrxChartCollector("secret-key", session)
    with pytest.raises(ValueError):
        collector.collect_daily("Stock", "005930", start, end)
    assert not session.calls


def test_missing_key_fails_when_collection_is_requested(monkeypatch):
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    collector = KrxChartCollector(session=Session())
    with pytest.raises(ValueError, match="required"):
        collector.collect_daily("Stock", "005930", "20260904", "20260904")
