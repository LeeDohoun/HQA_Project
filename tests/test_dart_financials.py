from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_financials import DartFinancialStatementCollector


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.requested = None

    def get(self, url, **kwargs):
        self.requested = (url, kwargs)
        return _Response(self.payload)


def test_dart_financial_statement_collector_builds_snapshot_from_major_accounts():
    payload = {
        "status": "000",
        "list": [
            {"fs_div": "CFS", "account_nm": "매출액", "thstrm_amount": "333,605,900,000,000", "currency": "KRW", "thstrm_dt": "2025.12.31"},
            {"fs_div": "CFS", "account_nm": "영업이익", "thstrm_amount": "43,601,000,000,000"},
            {"fs_div": "CFS", "account_nm": "당기순이익", "thstrm_amount": "45,206,800,000,000"},
            {"fs_div": "CFS", "account_nm": "자산총계", "thstrm_amount": "550,000,000,000,000"},
            {"fs_div": "CFS", "account_nm": "부채총계", "thstrm_amount": "124,000,000,000,000"},
            {"fs_div": "CFS", "account_nm": "자본총계", "thstrm_amount": "416,000,000,000,000"},
            {"fs_div": "CFS", "account_nm": "유동자산", "thstrm_amount": "220,000,000,000,000"},
            {"fs_div": "CFS", "account_nm": "유동부채", "thstrm_amount": "110,000,000,000,000"},
        ],
    }
    session = _Session(payload)
    collector = DartFinancialStatementCollector(api_key="dart-key")
    collector.session = session

    snapshot = collector.collect_annual("삼성전자", "005930", "00126380", "2025")

    assert snapshot is not None
    assert snapshot.stock_code == "005930"
    assert snapshot.report_code == "11011"
    assert snapshot.revenue == 333605900000000.0
    assert snapshot.operating_margin == 13.07
    assert snapshot.net_margin == 13.55
    assert snapshot.debt_ratio == 29.81
    assert snapshot.roe == 10.87
    assert snapshot.roa == 8.22
    assert snapshot.current_ratio == 200.0
    assert snapshot.metadata["quality_status"] == "complete"
    assert snapshot.metadata["collected_at"]
    assert snapshot.metadata["published_at"] is None
    assert snapshot.metadata["fs_div"] == "CFS"
    assert snapshot.metadata["currency_verified"] is True
    assert snapshot.metadata["version"]
    assert session.requested[1]["params"]["corp_code"] == "00126380"
    assert session.requested[1]["params"]["reprt_code"] == "11011"


def test_dart_financial_statement_collector_collects_recent_annual_series():
    collector = DartFinancialStatementCollector(api_key="dart-key")
    calls = []

    def fake_collect_annual(stock_name, stock_code, corp_code, fiscal_year):
        calls.append(fiscal_year)
        if fiscal_year == "2024":
            return None
        return type(
            "Snapshot",
            (),
            {
                "stock_name": stock_name,
                "stock_code": stock_code,
                "corp_code": corp_code,
                "fiscal_year": fiscal_year,
            },
        )()

    collector.collect_annual = fake_collect_annual

    snapshots = collector.collect_annual_series(
        stock_name="삼성전자",
        stock_code="005930",
        corp_code="00126380",
        from_date="20220101",
        to_date="20251231",
        years=3,
    )

    assert [snapshot.fiscal_year for snapshot in snapshots] == ["2025", "2023", "2022"]
    assert calls == ["2025", "2024", "2023", "2022"]


def test_incremental_event_window_does_not_exclude_previous_annual_reports():
    collector = DartFinancialStatementCollector(api_key="fixture-key")
    collector.collect_annual = Mock(side_effect=lambda **kwargs: (
        None if kwargs["fiscal_year"] == "2026" else SimpleNamespace(fiscal_year=kwargs["fiscal_year"])))
    snapshots = collector.collect_annual_series("Example", "005930", "00126380", "20260901", "20260905")
    assert [value.fiscal_year for value in snapshots] == ["2025", "2024", "2023"]
    assert [call.kwargs["fiscal_year"] for call in collector.collect_annual.call_args_list] == ["2026", "2025", "2024", "2023"]


@pytest.mark.parametrize("payload", [
    {"status": "020", "message": "fixture-key"}, {"status": "010"}, {"status": "014"},
    {"status": "800"}, {"status": "000", "list": []}, {"status": "000", "list": [None]},
    {"status": "000", "list": {}}, {}, [],
])
def test_financial_business_or_schema_errors_do_not_trigger_older_year_fallback(payload):
    collector = DartFinancialStatementCollector(api_key="fixture-key")
    collector.get_with_retry = Mock(return_value=SimpleNamespace(json=lambda: payload))
    with pytest.raises(DartAPIError) as error:
        collector.collect_annual_series("Example", "005930", "00126380", "20260901", "20260905")
    assert collector.get_with_retry.call_count == 1
    assert "fixture-key" not in str(error.value)


def test_financial_genuine_no_data_is_not_an_error():
    collector = DartFinancialStatementCollector(api_key="fixture-key")
    collector.get_with_retry = Mock(return_value=SimpleNamespace(json=lambda: {"status": "013"}))
    assert collector.collect_annual("Example", "005930", "00126380", "2025") is None


def test_financial_missing_configuration_and_transport_error_are_explicit():
    with pytest.raises(ValueError, match="required"):
        DartFinancialStatementCollector().collect_annual("Example", "005930", "00126380", "2025")
    collector = DartFinancialStatementCollector(api_key="fixture-key")
    collector.get_with_retry = Mock(side_effect=RuntimeError("https://example.invalid?crtfc_key=fixture-key"))
    with pytest.raises(DartAPIError, match="transport failure") as error:
        collector.collect_annual("Example", "005930", "00126380", "2025")
    assert "fixture-key" not in str(error.value)
