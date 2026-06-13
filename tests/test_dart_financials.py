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
    assert snapshot.metadata["quality_status"] == "complete"
    assert session.requested[1]["params"]["corp_code"] == "00126380"
    assert session.requested[1]["params"]["reprt_code"] == "11011"
