import csv
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from scripts.data import dart_company as cli
from src.ingestion import dart_company as company
from src.ingestion.dart_api import DartAPIError
from src.ingestion.storage import read_rows, write_rows


KEY = "fixture-private-key"
NOW = datetime(2024, 9, 4, 10, tzinfo=company.KST)


@pytest.fixture(autouse=True)
def no_real_session(monkeypatch):
    monkeypatch.setattr(company.requests, "Session", lambda: pytest.fail("Unexpected real session"))


def references(tmp_path, stocks=("005930", "000660", "0015G0")):
    path = tmp_path / "corps.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["corp_code", "stock_code"])
        writer.writeheader()
        writer.writerows({"corp_code": f"{number:08d}", "stock_code": stock}
                         for number, stock in enumerate(stocks, 1))
    return path


def payload(number=1, stock="005930", **changes):
    return {"status": "000", "corp_code": f"{number:08d}", "corp_name": "Example",
            "stock_code": stock, "corp_cls": "Y", "induty_code": "26110",
            "est_dt": "19690113", "acc_mt": "12", **changes}


class FakeSession:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.payloads:
            pytest.fail("Unexpected extra company request")
        value = self.payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, Mock):
            return value
        return Mock(status_code=200, json=Mock(return_value=value))

    def close(self):
        self.closed = True


def archive(tmp_path):
    return tmp_path / "data/reference/dart_company/companies.jsonl"


def collector(tmp_path, session=None, **kwargs):
    return company.DartCompanyCollector(KEY, session=session, clock=lambda: NOW,
        data_dir=tmp_path / "data", sleeper=lambda _: None, **kwargs)


def test_dry_run_has_no_requests_sleeps_credentials_or_writes(tmp_path):
    path = references(tmp_path)
    session = FakeSession()
    instance = company.DartCompanyCollector(session=session, data_dir=tmp_path / "data",
        clock=lambda: NOW, sleeper=lambda _: pytest.fail("Dry run slept"))
    result = instance.collect(path, max_requests=2)
    assert result["dry_run"] and result["corps"] == result["pending"] == 3
    assert result["planned_requests"] == 2 and result["requests"] == result["saved"] == 0
    assert session.calls == [] and not (tmp_path / "data").exists()


def test_collection_skips_done_and_resumes_under_daily_quota(tmp_path):
    path = references(tmp_path)
    session = FakeSession(payload(), payload(2, "000660"))
    result = collector(tmp_path, session).collect(path, max_requests=2, execute=True)
    assert (result["saved"], result["requests"], result["remaining"], result["stop_reason"]) == (2, 2, 1, "daily_limit")
    original = archive(tmp_path).read_bytes()
    resumed = collector(tmp_path, FakeSession())
    assert resumed.collect(path, max_requests=2)["planned_requests"] == 0
    assert resumed.collect(path, max_requests=2, execute=True)["requests"] == 0
    assert archive(tmp_path).read_bytes() == original
    resumed.clock = lambda: NOW + timedelta(days=1)
    resumed.session = FakeSession(payload(3, "0015G0"))
    final = resumed.collect(path, max_requests=2, execute=True)
    assert final["completed"] == 3 and final["saved"] == 1 and final["remaining"] == 0
    assert final["stop_reason"] == "complete"
    rows = read_rows(archive(tmp_path))
    assert len(rows) == 3 and rows[0]["collected_at"] == NOW.isoformat()
    assert rows[2]["collected_at"] == (NOW + timedelta(days=1)).isoformat()
    assert resumed.collect(path, execute=True)["requests"] == 0


def test_status_020_blocks_reruns_until_next_kst_day(tmp_path):
    path = references(tmp_path)
    instance = collector(tmp_path, FakeSession(payload(), {"status": "020", "message": KEY}))
    result = instance.collect(path, execute=True)
    assert result["stop_reason"] == "provider_limit" and result["requests"] == 2
    assert result["saved"] == 1 and result["remaining"] == 2
    instance.session = FakeSession()
    assert instance.collect(path, max_requests=10000)["planned_requests"] == 0
    assert instance.collect(path, max_requests=10000, execute=True)["requests"] == 0
    instance.clock = lambda: NOW + timedelta(days=1)
    instance.session = FakeSession(payload(2, "000660"), payload(3, "0015G0"))
    assert instance.collect(path, execute=True)["saved"] == 2
    quota = json.loads(archive(tmp_path).with_name("_quota.json").read_text())
    assert quota["days"] == {"20240904": {"requests": 2, "provider_blocked": True},
                             "20240905": {"requests": 2, "provider_blocked": False}}


def test_sleeper_is_injected_and_request_and_response_contracts(tmp_path, monkeypatch):
    path = references(tmp_path, ("005930",))
    events = []
    session = FakeSession(payload(corp_name="echo " + KEY, message=KEY, ignored=KEY))
    instance = collector(tmp_path, session)
    instance.sleeper = lambda seconds: events.append((seconds, len(session.calls)))
    real_reader = company.read_dart_payload
    monkeypatch.setattr(company, "read_dart_payload", lambda response: (events.append("validate"), real_reader(response))[1])
    instance.collect(path, execute=True)
    assert events == [(0.25, 0), "validate"]
    assert session.calls == [(company.COMPANY_URL, {"params": {"crtfc_key": KEY, "corp_code": "00000001"},
                                                 "timeout": 20, "allow_redirects": False})]
    row = read_rows(archive(tmp_path))[0]
    assert row["corp_name"] == "echo [REDACTED]"
    assert KEY not in archive(tmp_path).read_text() and "ignored" not in row and "message" not in row
    assert row["source_url"] == company.COMPANY_URL and row["reference_stock_code"] == "005930"


@pytest.mark.parametrize("stock", ["", "005930", "0015G0"])
def test_delisted_blank_and_alphanumeric_stock_codes_are_preserved(tmp_path, stock):
    reference = stock or "005930"
    path = references(tmp_path, (reference,))
    collector(tmp_path, FakeSession(payload(stock=stock, corp_cls="E", induty_code="", est_dt="", acc_mt=""))).collect(
        path, execute=True)
    row = read_rows(archive(tmp_path))[0]
    assert row["stock_code"] == stock and row["reference_stock_code"] == reference
    assert row["induty_code"] == "" and row["corp_cls"] == "E"


@pytest.mark.parametrize("bad", [
    {"status": "010", "message": KEY}, {"status": "013"}, {}, [],
    payload(corp_code="00000002"), payload(stock="000660"), payload(corp_cls="bad"),
    payload(induty_code=None), payload(est_dt="20240230"), payload(acc_mt="13"),
    Mock(status_code=200, json=Mock(side_effect=ValueError(KEY))),
    Mock(status_code=302), Mock(status_code=500), RuntimeError("url?crtfc_key=" + KEY),
])
def test_invalid_provider_and_transport_failures_are_sanitized_and_counted(tmp_path, bad):
    path = references(tmp_path, ("005930",))
    session = FakeSession(bad)
    instance = collector(tmp_path, session)
    with pytest.raises(DartAPIError) as error:
        instance.collect(path, execute=True)
    assert KEY not in "".join(traceback.format_exception(error.value))
    assert not archive(tmp_path).exists() and len(session.calls) == 1
    assert instance.collect(path, max_requests=1)["planned_requests"] == 0
    assert instance.collect(path, max_requests=1, execute=True)["requests"] == 0


def test_failure_preserves_successful_companies_and_retry_is_counted(tmp_path):
    path = references(tmp_path, ("005930", "000660"))
    instance = collector(tmp_path, FakeSession(payload(), RuntimeError(KEY)))
    with pytest.raises(DartAPIError):
        instance.collect(path, max_requests=3, execute=True)
    assert len(read_rows(archive(tmp_path))) == 1
    instance.session = FakeSession(payload(2, "000660"))
    result = instance.collect(path, max_requests=3, execute=True)
    assert result["completed"] == 2 and result["requests"] == result["saved"] == 1
    assert result["quota_used"] == 3


def test_quota_day_is_kst_even_with_utc_clock_and_midnight_sleep(tmp_path):
    path = references(tmp_path, ("005930",))
    now = [datetime(2024, 9, 4, 14, 59, 59, tzinfo=timezone.utc)]
    instance = collector(tmp_path, FakeSession(payload()))
    instance.clock = lambda: now[0]
    instance.sleeper = lambda seconds: now.__setitem__(0, now[0] + timedelta(seconds=1))
    result = instance.collect(path, execute=True)
    assert result["quota_day"] == "20240905"
    assert read_rows(archive(tmp_path))[0]["collected_at"] == "2024-09-05T00:00:00+09:00"


def test_archived_progress_is_authoritative_and_duplicates_fail(tmp_path):
    path = references(tmp_path, ("005930",))
    row = {**payload(), "reference_stock_code": "005930", "collected_at": NOW.isoformat()}
    write_rows(archive(tmp_path), [row])
    assert collector(tmp_path, FakeSession()).collect(path, execute=True)["requests"] == 0
    write_rows(archive(tmp_path), [row, row])
    with pytest.raises(ValueError, match="duplicate"):
        collector(tmp_path, FakeSession()).collect(path)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limits_fail_without_requests_or_data_files(tmp_path, limit):
    path = references(tmp_path)
    with pytest.raises(ValueError, match="positive integer"):
        collector(tmp_path, FakeSession()).collect(path, max_requests=limit, execute=True)
    assert not (tmp_path / "data").exists()


def test_missing_key_fails_before_creating_data_files(tmp_path):
    path = references(tmp_path)
    with pytest.raises(ValueError, match="key is required"):
        company.DartCompanyCollector(data_dir=tmp_path / "data").collect(path, execute=True)
    assert not (tmp_path / "data").exists()


def test_invalid_reference_and_quota_fail_instead_of_resetting(tmp_path):
    path = references(tmp_path, ("bad",))
    with pytest.raises(ValueError, match="invalid"):
        collector(tmp_path, FakeSession()).collect(path)
    path = references(tmp_path, ("005930",))
    quota = archive(tmp_path).with_name("_quota.json")
    quota.parent.mkdir(parents=True)
    quota.write_text('{"days":{"20240904":{"requests":-1,"provider_blocked":false}}}')
    with pytest.raises(ValueError, match="quota"):
        collector(tmp_path, FakeSession()).collect(path)


def test_concurrent_collectors_do_not_repeat_done_corps_or_overspend(tmp_path):
    path = references(tmp_path, ("005930", "000660"))
    instances = [collector(tmp_path, FakeSession(payload(), payload(2, "000660"))) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda instance: instance.collect(path, max_requests=2, execute=True), instances))
    assert sum(result["requests"] for result in results) == 2
    assert len(read_rows(archive(tmp_path))) == 2


def test_cli_dry_run_and_execute_with_exported_key(tmp_path, monkeypatch, capsys):
    path = references(tmp_path, ("005930",))
    args = ["dart_company", "--corp-codes", str(path), "--data-dir", str(tmp_path / "data"), "--max-requests", "1"]
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.delenv("DART_API_KEY", raising=False)
    cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] and plan["planned_requests"] == 1
    assert not (tmp_path / "data").exists()
    session = FakeSession(payload())
    monkeypatch.setattr(company.requests, "Session", lambda: session)
    monkeypatch.setattr(company.time, "sleep", lambda _: None)
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setattr("sys.argv", args + ["--execute"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["saved"] == 1 and session.closed
