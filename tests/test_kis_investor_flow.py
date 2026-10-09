import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

from src.ingestion import kis_investor_flow as flow
from src.ingestion.storage import read_rows

APP_KEY = "fixture-private-data-key"
APP_SECRET = "fixture-private-data-secret"
TOKEN = "fixture-private-token"
NOW = datetime(2026, 9, 8, 19, tzinfo=flow.KST)


class Clock:
    def __init__(self, now=NOW):
        self.now, self.delays = now, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += timedelta(seconds=seconds)


def row(day="20260907", **changes):
    return {"stck_bsop_date": day, "prsn_ntby_qty": "-7", "frgn_ntby_qty": "5", "orgn_ntby_qty": "2",
            "prsn_ntby_tr_pbmn": "-0.700", "frgn_ntby_tr_pbmn": "+0.50", "orgn_ntby_tr_pbmn": "0.20",
            "stck_clpr": "70,000", **changes}


def output(*rows):
    return {"rt_cd": "0", "output": list(rows or [row()])}


class Response:
    def __init__(self, payload=None, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, clock, stocks=None, tokens=None):
        self.clock = clock
        self.stocks = deepcopy(stocks or {})
        self.tokens = list(tokens) if tokens is not None else None
        self.posts, self.gets = [], []

    def post(self, url, **kwargs):
        self.posts.append((self.clock(), url, deepcopy(kwargs)))
        if self.tokens is None:
            return Response({"access_token": TOKEN, "expires_in": 86400})
        assert self.tokens, "unexpected token issuance"
        response = self.tokens.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        self.gets.append((self.clock(), url, deepcopy(kwargs)))
        code = kwargs["params"]["FID_INPUT_ISCD"]
        if code not in self.stocks:
            return Response(output())
        assert self.stocks[code], "unexpected quotation retry"
        response = self.stocks[code].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.setattr("src.config.settings.load_project_env", lambda *a, **k: pytest.fail("Unexpected env read"))
    # Tests run as the developer; the production owner check permits only root/hqa.
    monkeypatch.setattr(flow, "_cache_owner_uids", lambda: {0, os.geteuid()})
    monkeypatch.setattr(flow.requests, "Session", lambda: pytest.fail("Unexpected provider session"))


def collector(data_dir, *, clock=None, session=None):
    clock = clock or Clock()
    session = session or Session(clock)
    return flow.KisInvestorFlowCollector(APP_KEY, APP_SECRET, session=session, clock=clock,
                                        sleeper=clock.sleep, data_dir=data_dir), session, clock


def cache_file(data_dir):
    return data_dir / ".kis_tokens" / f"{hashlib.sha256(APP_KEY.encode()).hexdigest()}.json"


@pytest.mark.parametrize("method,url", [
    ("GET", "https://openapi.koreainvestment.com:9443" + flow.INVESTOR_PATH),
    ("POST", "https://openapi.koreainvestment.com:9443" + flow.TOKEN_PATH),
    ("GET", flow.PAPER_BASE_URL + "/uapi/domestic-stock/v1/trading/order-cash"),
    ("POST", flow.PAPER_BASE_URL + "/uapi/domestic-stock/v1/trading/order-cash"),
    ("GET", flow.PAPER_BASE_URL + "/uapi/domestic-stock/v1/trading/inquire-balance"),
    ("POST", flow.PAPER_BASE_URL + flow.INVESTOR_PATH),
    ("GET", flow.PAPER_BASE_URL + flow.TOKEN_PATH),
    ("GET", flow.PAPER_BASE_URL + flow.INVESTOR_PATH + "/../trading/order-cash"),
    ("GET", flow.PAPER_BASE_URL + flow.INVESTOR_PATH + "?CANO=forbidden"),
    ("GET", "http://openapivts.koreainvestment.com:29443" + flow.INVESTOR_PATH),
    ("GET", "https://openapivts.koreainvestment.com:29443.evil.example" + flow.INVESTOR_PATH),
])
def test_allowlist_rejects_every_other_request(method, url):
    with pytest.raises(ValueError):
        flow.validate_request(method, url)


def test_allowlist_permits_only_quotation_and_token_exception(tmp_path):
    flow.validate_request("GET", flow.PAPER_BASE_URL + flow.INVESTOR_PATH)
    flow.validate_request("POST", flow.PAPER_BASE_URL + flow.TOKEN_PATH)
    instance, session, _ = collector(tmp_path)
    with pytest.raises(ValueError):
        instance._request("GET", "/uapi/domestic-stock/v1/trading/inquire-balance")
    assert session.posts == session.gets == []
    assert list(tmp_path.iterdir()) == []


def test_real_domain_is_rejected_before_any_request(tmp_path, monkeypatch):
    instance, session, _ = collector(tmp_path)
    monkeypatch.setattr(flow, "PAPER_BASE_URL", "https://openapi.koreainvestment.com:9443")
    with pytest.raises(ValueError, match="PAPER domain"):
        instance._request("POST", flow.TOKEN_PATH)
    assert session.posts == session.gets == []


def test_request_contract_secure_cache_and_no_logged_credentials(tmp_path, caplog, capsys):
    instance, session, clock = collector(tmp_path)
    report = instance.collect_day(["005930", "0009K0"], execute=True)
    assert (report["stocks_attempted"], report["stocks_ok"], report["stocks_failed"], report["rows_saved"]) == (2, 2, 0, 2)
    assert session.posts[0][1:] == (flow.PAPER_BASE_URL + flow.TOKEN_PATH, {
        "timeout": 30, "allow_redirects": False, "headers": {"content-type": "application/json"},
        "json": {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}})
    for _, url, options in session.gets:
        assert url == flow.PAPER_BASE_URL + flow.INVESTOR_PATH
        assert options["headers"] == {"authorization": "Bearer " + TOKEN, "appkey": APP_KEY,
                                      "appsecret": APP_SECRET, "tr_id": "FHKST01010900", "custtype": "P"}
        assert options["params"]["FID_COND_MRKT_DIV_CODE"] == "J"
        assert options["allow_redirects"] is False
    assert all((later[0] - earlier[0]).total_seconds() >= 0.5
               for earlier, later in zip(session.posts + session.gets, (session.posts + session.gets)[1:]))
    path = cache_file(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    state = json.loads(path.read_text())
    assert state["requests"] == 1 and state["request_day"] == "2026-09-08"
    assert state["access_token"] == TOKEN
    saved = read_rows(tmp_path / "market/investor_flow/2026/20260907.jsonl")
    assert saved[0]["individual_net_quantity"] == "-7" and saved[0]["individual_net_value"] == "-0.7"
    assert saved[0]["close"] == "70000" and saved[0]["net_value_unit"] == "provider_reported_unverified"
    captured = capsys.readouterr()
    public = json.dumps(report) + caplog.text + captured.out + captured.err
    public += "".join(file.read_text() for file in (tmp_path / "market").rglob("*.*") if file.is_file())
    assert all(secret not in public for secret in (APP_KEY, APP_SECRET, TOKEN))
    assert APP_KEY not in path.read_text() and APP_SECRET not in path.read_text()


def test_token_reused_across_restarts_and_kst_midnight_until_expiry(tmp_path):
    clock = Clock(datetime(2026, 9, 8, 23, 59, tzinfo=flow.KST))
    first, session, _ = collector(tmp_path, clock=clock)
    first.collect_day(["005930"], execute=True)
    second, _, _ = collector(tmp_path, clock=clock, session=session)
    second.collect_day(["005930"], execute=True)
    clock.now += timedelta(minutes=2)
    third, _, _ = collector(tmp_path, clock=clock, session=session)
    third.collect_day(["005930"], execute=True)
    assert len(session.posts) == 1 and len(session.gets) == 3


def test_expiry_cannot_trigger_second_normal_issuance_in_same_kst_day(tmp_path):
    clock = Clock()
    session = Session(clock, tokens=[Response({"access_token": TOKEN, "expires_in": 10}),
                                    Response({"access_token": TOKEN, "expires_in": 86400})])
    first, _, _ = collector(tmp_path, clock=clock, session=session)
    first.collect_day(["005930"], execute=True)
    clock.now += timedelta(seconds=11)
    restarted, _, _ = collector(tmp_path, clock=clock, session=session)
    report = restarted.collect_day(["005930"], execute=True)
    assert report["failures"][0]["reason"] == "token_daily_limit" and len(session.posts) == 1
    clock.now = datetime(2026, 9, 9, 0, tzinfo=flow.KST)
    assert restarted.collect_day(["005930"], execute=True)["stocks_ok"] == 1
    assert len(session.posts) == 2


def test_token_request_wait_crossing_midnight_reserves_new_kst_day(tmp_path):
    clock = Clock(datetime(2026, 9, 8, 23, 59, 59, 900000, tzinfo=flow.KST))
    session = Session(clock, tokens=[Response({"access_token": TOKEN, "expires_in": 10})])
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    instance._last_request_at = clock.now
    instance.collect_day(["005930"], execute=True)
    assert session.posts[0][0].date().isoformat() == "2026-09-09"
    state = json.loads(cache_file(tmp_path).read_text())
    assert state["request_day"] == "2026-09-09" and state["requests"] == 1
    clock.now += timedelta(seconds=11)
    assert instance.collect_day(["005930"], execute=True)["failures"][0]["reason"] == "token_daily_limit"
    assert len(session.posts) == 1


def test_daily_token_budget_is_per_app_key(tmp_path):
    instance, session, clock = collector(tmp_path)
    instance.collect_day(["005930"], execute=True)
    other = flow.KisInvestorFlowCollector("other-fixture-key", APP_SECRET, session=session,
        clock=clock, sleeper=clock.sleep, data_dir=tmp_path)
    other.collect_day(["000660"], execute=True)
    restarted, _, _ = collector(tmp_path, clock=clock, session=session)
    restarted.collect_day(["005930"], execute=True)
    assert len(session.posts) == 2 and len(list((tmp_path / ".kis_tokens").glob("*.json"))) == 2


def test_token_rejection_refresh_failed_post_still_spends_extra_daily_request(tmp_path):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response({}, 401)]},
        tokens=[Response({"access_token": TOKEN, "expires_in": 86400}), Response({}, 503)])
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "000660"], execute=True)
    assert report["stocks_failed"] == 2 and len(session.posts) == 2 and len(session.gets) == 1
    state = json.loads(cache_file(tmp_path).read_text())
    assert state["requests"] == 2 and state["rejection_refresh_used"]
    restarted, _, _ = collector(tmp_path, clock=clock, session=session)
    restarted.collect_day(["005930"], execute=True)
    assert len(session.posts) == 2 and len(session.gets) == 1


def test_failed_token_request_is_counted_before_post_and_never_retried_that_day(tmp_path, caplog, capsys):
    clock = Clock()
    session = Session(clock, tokens=[requests.Timeout(APP_KEY + APP_SECRET + TOKEN)])
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "000660"], execute=True)
    assert report["stocks_failed"] == 2 and len(session.posts) == 1 and not session.gets
    assert json.loads(cache_file(tmp_path).read_text())["requests"] == 1
    restarted, _, _ = collector(tmp_path, clock=clock, session=session)
    restarted.collect_day(["005930"], execute=True)
    assert len(session.posts) == 1
    captured = capsys.readouterr()
    assert all(secret not in json.dumps(report) + caplog.text + captured.out + captured.err
               for secret in (APP_KEY, APP_SECRET, TOKEN))


@pytest.mark.parametrize("payload", [
    {"access_token": TOKEN}, {"access_token": TOKEN, "expires_in": 0},
    {"access_token": TOKEN, "expires_in": "not-a-number"},
    {"access_token": TOKEN, "access_token_token_expired": "expired"},
])
def test_token_requires_a_valid_stated_expiry_without_default(tmp_path, payload):
    clock = Clock()
    session = Session(clock, tokens=[Response(payload)])
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930"], execute=True)
    assert report["failures"][0]["reason"] == "invalid_token_expiry"
    assert len(session.posts) == 1 and not session.gets


def test_absolute_token_expiry_wins_over_longer_expires_in(tmp_path):
    clock = Clock()
    session = Session(clock, tokens=[Response({"access_token": TOKEN, "expires_in": 86400,
                                              "access_token_token_expired": "2026-09-08 19:00:10"})])
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    instance.collect_day(["005930"], execute=True)
    assert json.loads(cache_file(tmp_path).read_text())["expires_at"] == "2026-09-08T19:00:10+09:00"


@pytest.mark.parametrize("rejection", [Response({}, 401),
    Response({"rt_cd": "1", "msg_cd": "EGW00121", "msg1": APP_KEY + TOKEN}),
    Response({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": APP_SECRET + TOKEN})])
def test_rejected_token_refreshed_only_once_and_invalidated_for_later_runs(tmp_path, rejection):
    clock = Clock()
    session = Session(clock, stocks={"005930": [rejection, Response(output())], "000660": [rejection]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "000660"], execute=True)
    assert (report["stocks_ok"], report["stocks_failed"]) == (1, 1)
    assert report["failures"][0]["reason"] == "token_daily_limit"
    assert len(session.posts) == 2 and len(session.gets) == 3
    state = json.loads(cache_file(tmp_path).read_text())
    assert state["requests"] == 2 and state["rejection_refresh_used"] and state["rejected"]
    restarted, _, _ = collector(tmp_path, clock=clock, session=session)
    restarted.collect_day(["012450"], execute=True)
    assert len(session.posts) == 2 and len(session.gets) == 3


@pytest.mark.parametrize("failure,reason", [
    (Response({"msg1": APP_KEY + TOKEN}, 503), "http_error"),
    (Response({"rt_cd": "0", "output": []}), "empty_output"),
    (Response({}, 429), "rate_limited"),
    (Response({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": APP_SECRET}), "rate_limited"),
    (requests.Timeout(APP_KEY + APP_SECRET + TOKEN), "network_error"),
])
def test_stock_failures_retry_with_backoff_and_continue(tmp_path, failure, reason, caplog, capsys):
    clock = Clock()
    session = Session(clock, stocks={"005930": [failure, failure, failure]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "000660"], execute=True)
    assert (report["stocks_attempted"], report["stocks_ok"], report["stocks_failed"], report["rows_saved"]) == (2, 1, 1, 1)
    assert report["failures"] == [{"stock_code": "005930", "attempts": 3, "reason": reason}]
    assert len(session.gets) == 4 and len(session.posts) == 1
    assert 1 in clock.delays and 2 in clock.delays
    assert json.loads((tmp_path / "market/investor_flow/_last_run.json").read_text()) == report
    if reason == "rate_limited":
        assert instance.requests_per_second == 0.25
        assert (session.gets[-1][0] - session.gets[-2][0]).total_seconds() >= 4
    captured = capsys.readouterr()
    assert all(secret not in json.dumps(report) + caplog.text + captured.out + captured.err
               for secret in (APP_KEY, APP_SECRET, TOKEN))


def test_retry_can_recover_without_failure_record(tmp_path):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response({"rt_cd": "0", "output": []}),
                                               Response({}, 500), Response(output())]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "005930"], execute=True)
    assert report["stocks_ok"] == report["stocks_attempted"] == report["rows_saved"] == 1
    assert report["failures"] == [] and len(session.gets) == 3


@pytest.mark.parametrize("now,saved,skipped", [
    ("2026-09-08T18:29:59+09:00", 1, 1), ("2026-09-08T18:30:00+09:00", 2, 0),
    ("2026-09-08T09:29:59+00:00", 1, 1), ("2026-09-08T09:30:00+00:00", 2, 0),
])
def test_provisional_today_is_excluded_before_1830_kst(tmp_path, now, saved, skipped):
    clock = Clock(datetime.fromisoformat(now))
    # Preload a token so issuance/throttling cannot move the test across the boundary.
    instance, session, _ = collector(tmp_path, clock=clock)
    instance._access_token()
    session.stocks = {"005930": [Response(output(row(), row("20260908")))]}
    report = instance.collect_day(["005930"], execute=True)
    assert report["rows_saved"] == saved and report["provisional_rows_skipped"] == skipped
    assert (tmp_path / "market/investor_flow/2026/20260908.jsonl").exists() is (saved == 2)


def test_revisions_skip_identical_content_and_preserve_a_b_a_history(tmp_path):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response(output(row())),
        Response(output(row(stck_clpr="70000.00"))), Response(output(row(frgn_ntby_qty="6"))), Response(output(row()))]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    counts = [instance.collect_day(["005930"], execute=True)["rows_saved"] for _ in range(4)]
    assert counts == [1, 0, 1, 1]
    stored = read_rows(tmp_path / "market/investor_flow/2026/20260907.jsonl")
    assert len(stored) == 3 and stored[0]["version"] == stored[2]["version"] != stored[1]["version"]
    assert len({record["metadata"]["version_id"] for record in stored}) == 3
    assert stored[0]["collected_at"] < stored[1]["collected_at"] < stored[2]["collected_at"]


@pytest.mark.parametrize("bad", [
    row(stck_bsop_date="20260230"), row(stck_bsop_date="20260909"), row(stck_bsop_date="2026-09-07"),
    row(stck_clpr="0"), row(prsn_ntby_qty="1.5"),
    {"stck_bsop_date": "20260907"}, None,
])
def test_invalid_stock_rows_fail_clearly_without_partial_save(tmp_path, bad):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response({"rt_cd": "0", "output": [row("20260904"), bad]})]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "000660"], execute=True)
    assert report["failures"] == [{"stock_code": "005930", "attempts": 1, "reason": "invalid_row"}]
    assert report["rows_saved"] == 1 and not (tmp_path / "market/investor_flow/2026/20260904.jsonl").exists()


@pytest.mark.parametrize("source", flow.FLOW_FIELDS.values())
@pytest.mark.parametrize("value", ["", "  ", "-", "not-a-number", "NaN", "Infinity", "1,23", None, True])
def test_invalid_numeric_rows_are_skipped_without_discarding_valid_days(tmp_path, source, value):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response(output(
        row("20260904"), row(**{source: value}), row("20260908")))]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930"], execute=True)
    assert (report["stocks_ok"], report["stocks_failed"], report["rows_saved"]) == (1, 0, 2)
    assert report["rows_skipped_invalid"] == 1
    assert report["stocks_no_valid_rows"] == 0 and report["no_valid_rows"] == report["failures"] == []
    assert len(session.gets) == 1
    directory = tmp_path / "market/investor_flow/2026"
    assert not (directory / "20260907.jsonl").exists()
    assert read_rows(directory / "20260904.jsonl")[0]["individual_net_quantity"] == "-7"
    assert read_rows(directory / "20260908.jsonl")[0]["close"] == "70000"


def test_all_blank_stock_is_no_valid_rows_without_retries(tmp_path):
    clock = Clock()
    blank = {source: "" for source in flow.FLOW_FIELDS.values()}
    session = Session(clock, stocks={"000300": [Response(output(row(**blank), row("20260904", **blank)))]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["000300"], execute=True)
    assert (report["stocks_attempted"], report["stocks_ok"], report["stocks_failed"], report["rows_saved"]) == (1, 0, 0, 0)
    assert report["rows_skipped_invalid"] == 2 and report["stocks_no_valid_rows"] == 1
    assert report["no_valid_rows"] == [{"stock_code": "000300", "attempts": 1, "reason": "no_valid_rows"}]
    assert report["failures"] == [] and len(session.gets) == 1
    assert 1 not in clock.delays and 2 not in clock.delays
    assert not list((tmp_path / "market/investor_flow").glob("*/*.jsonl"))
    assert json.loads((tmp_path / "market/investor_flow/_last_run.json").read_text()) == report


def test_invalid_numeric_fields_do_not_hide_malformed_date(tmp_path):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response(output(row(), row("20260230", stck_clpr="")))]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930"], execute=True)
    assert report["failures"] == [{"stock_code": "005930", "attempts": 1, "reason": "invalid_row"}]
    assert report["rows_saved"] == 0 and report["stocks_no_valid_rows"] == 0
    assert len(session.gets) == 1


@pytest.mark.parametrize("second", [row(frgn_ntby_qty="6"), row(stck_clpr="")])
def test_conflicting_duplicate_dates_fail_without_partial_save(tmp_path, second):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response(output(row("20260904"), row(), second))]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930"], execute=True)
    assert report["failures"] == [{"stock_code": "005930", "attempts": 1, "reason": "invalid_row"}]
    assert report["rows_saved"] == 0 and len(session.gets) == 1


def test_identical_duplicate_dates_are_stored_once(tmp_path):
    clock = Clock()
    session = Session(clock, stocks={"005930": [Response(output(row(), row(stck_clpr="70000.00")))]})
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930"], execute=True)
    assert report["stocks_ok"] == report["rows_saved"] == 1
    assert report["rows_skipped_invalid"] == 0 and report["failures"] == []


def test_summary_counts_valid_skipped_empty_and_failed_stocks(tmp_path):
    clock = Clock()
    session = Session(clock, stocks={
        "005930": [Response(output(row(), row("20260904", stck_clpr="")))],
        "000300": [Response(output(row(stck_clpr=""), row("20260904", prsn_ntby_qty="-")))],
        "001470": [Response(output(row(stck_clpr="not-a-number")))],
        "001570": [Response(output(row("20260230")))],
    })
    instance, _, _ = collector(tmp_path, clock=clock, session=session)
    report = instance.collect_day(["005930", "000300", "001470", "001570", "000660"], execute=True)
    assert (report["stocks_planned"], report["stocks_attempted"], report["stocks_ok"], report["stocks_failed"],
            report["stocks_no_valid_rows"], report["rows_saved"], report["rows_skipped_invalid"]) == (5, 5, 2, 1, 2, 2, 4)
    assert [item["stock_code"] for item in report["no_valid_rows"]] == ["000300", "001470"]
    assert report["failures"] == [{"stock_code": "001570", "attempts": 1, "reason": "invalid_row"}]
    assert len(session.gets) == 5
    assert json.loads((tmp_path / "market/investor_flow/_last_run.json").read_text()) == report


def test_dry_run_needs_no_credentials_session_or_cache(tmp_path):
    instance = flow.KisInvestorFlowCollector(None, None, clock=lambda: NOW, data_dir=tmp_path)
    report = instance.collect_day(["005930"])
    assert report["dry_run"] and report["stocks_planned"] == 1
    assert report["stocks_attempted"] == report["rows_saved"] == 0
    assert report["rows_skipped_invalid"] == report["stocks_no_valid_rows"] == 0
    assert report["no_valid_rows"] == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key,secret", [(None, None), (APP_KEY, None), (None, APP_SECRET), (" ", APP_SECRET)])
def test_execute_fails_fast_for_missing_dedicated_keys(tmp_path, key, secret):
    instance = flow.KisInvestorFlowCollector(key, secret, clock=lambda: NOW, data_dir=tmp_path)
    with pytest.raises(ValueError, match="KIS_DATA"):
        instance.collect_day(["005930"], execute=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["mode", "symlink", "corrupt", "owner"])
def test_insecure_or_corrupt_token_cache_is_refused_without_request(tmp_path, monkeypatch, kind):
    instance, session, _ = collector(tmp_path)
    instance._access_token()
    path = cache_file(tmp_path)
    if kind == "mode":
        path.chmod(0o644)
    elif kind == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    elif kind == "corrupt":
        path.write_text("{broken: " + TOKEN)
    else:
        monkeypatch.setattr(flow, "_cache_owner_uids", lambda: {os.geteuid() + 10000})
    with pytest.raises(ValueError):
        instance.collect_day(["005930"], execute=True)
    assert len(session.posts) == 1 and not session.gets


def test_latest_krx_universe_wins_and_manual_list_is_fallback(tmp_path):
    assert flow.load_stock_codes(tmp_path, ["005930"]) == ["005930"]
    for day, records in (("20260904", [{"stock_code": "012450"}]),
                         ("20260907", [{"stock_code": "005930"}, {"stock_code": "0009K0"}, {"stock_code": "005930"}])):
        path = tmp_path / "market/krx_daily" / day[:4] / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
    assert flow.load_stock_codes(tmp_path, ["012450"]) == ["0009K0", "005930"]


def test_missing_or_empty_universe_fails_without_placeholder_codes(tmp_path):
    with pytest.raises(ValueError, match="no stored KRX"):
        flow.load_stock_codes(tmp_path)
    path = tmp_path / "market/krx_daily/2026/20260907.jsonl"
    path.parent.mkdir(parents=True)
    path.touch()
    with pytest.raises(ValueError, match="stock codes"):
        flow.load_stock_codes(tmp_path, ["005930"])
