from datetime import timedelta
import json
import os
from pathlib import Path

import pytest

from backtesting.experiments.xo001 import Refused
from backtesting.experiments.xo001.kis import (
    ALLOW, APPROVAL, BASE, BOOK, CANCEL, INQUIRY, ORDER, PRICE, TOKEN, Client, Credentials, HTTPS, validate_request,
)
from backtesting.experiments.xo001.rate import RateManager
from backtesting.experiments.xo001.storage import Store, load, replace_private
from test_xo001_support import Clock, Transport, credentials, fake_kernel, payload


@pytest.mark.parametrize("method,path,tr", [(method, path, tr) for (method, path), trs in ALLOW.items() for tr in trs])
def test_exact_allowlist_accepts_only_documented_contract(method, path, tr):
    assert validate_request(method, BASE + path, tr) == path


@pytest.mark.parametrize("url", [
    "https://openapi.koreainvestment.com:9443" + PRICE,
    "http://openapivts.koreainvestment.com:29443" + PRICE,
    "https://openapivts.koreainvestment.com" + PRICE,
    BASE + PRICE + "?x=1", BASE + PRICE + "#fragment", BASE + PRICE + "/../inquire-price",
    BASE + PRICE + "/", BASE + "/oauth2/tokenP/../tokenP", BASE.replace(".com", ".com.evil") + PRICE,
    BASE.replace("https://", "https://x@") + PRICE, "ws://ops.koreainvestment.com:31000", "https://127.0.0.1:29443" + PRICE,
])
def test_host_scheme_path_credentials_query_and_websocket_refused(url):
    with pytest.raises(Refused):
        validate_request("GET", url, "FHKST01010100")


@pytest.mark.parametrize("method,path,tr", [
    ("POST", PRICE, "FHKST01010100"), ("GET", ORDER, "VTTC0012U"), ("POST", ORDER, "TTTC0012U"),
    ("POST", CANCEL, "TTTC0013U"), ("GET", INQUIRY, "TTTC8001R"),
    ("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R"),
    ("GET", PRICE, "FHKST01010400"), ("GET", APPROVAL, None),
])
def test_methods_live_trs_and_unlisted_trs_refused(method, path, tr):
    with pytest.raises(Refused):
        validate_request(method, BASE + path, tr)


def client_at(tmp_path, clock, responses):
    transport = Transport(responses)
    rate = RateManager(tmp_path / "control", clock=clock, monotonic=lambda: clock.mono, sleeper=clock.sleep)
    client = Client(credentials(), tmp_path, rate, transport=transport, clock=clock, monotonic=lambda: clock.mono)
    return client, transport


def token_response(**changes):
    return {"access_token": "private-issued-token", "access_token_token_expired": "2026-11-03 23:59:59", **changes}


def test_environment_has_no_general_or_live_credential_fallback(monkeypatch):
    for key in ("APP_KEY", "APP_SECRET", "CANO", "ACNT_PRDT_CD", "HTS_ID"):
        monkeypatch.delenv("XO001_KIS_PAPER_" + key, raising=False)
        monkeypatch.setenv("KIS_" + key, "must-not-use")
    with pytest.raises(Refused, match="missing required"):
        Credentials.environment()
    c = credentials()
    for key, value in zip(("APP_KEY", "APP_SECRET", "CANO", "ACNT_PRDT_CD", "HTS_ID"), (c.app_key, c.app_secret, c.cano, c.product, c.hts_id)):
        monkeypatch.setenv("XO001_KIS_PAPER_" + key, value)
    assert Credentials.environment().identifiers() == c.identifiers()
    assert c.app_secret not in repr(c)


def test_token_actual_expiry_reused_across_owner_restarts_and_midnight(tmp_path):
    clock = Clock("2026-11-03T23:59:50+09:00")
    c, transport = client_at(tmp_path, clock, [token_response(access_token_token_expired="2026-11-04 08:00:00"), payload(clock)])
    with c:
        c.quote("005930", book=True, priority="event", deadline=clock() + timedelta(seconds=20))
        cache = c.cache
    state = load(cache)
    assert state["expires_at"] == "2026-11-04T08:00:00+09:00"
    assert cache.stat().st_mode & 0o777 == 0o600
    assert cache.parent.stat().st_mode & 0o777 == 0o700
    clock.sleep(20)
    second, transport2 = client_at(tmp_path, clock, [payload(clock)])
    with second:
        second.quote("005930", book=True, priority="event", deadline=clock() + timedelta(seconds=10))
    assert all(call[1] != BASE + TOKEN for call in transport2.calls)
    assert len(transport.calls) == 2


@pytest.mark.parametrize("response", [token_response(access_token_token_expired=None, expires_in=10),
                                     OSError("private-app-secret-xo001 private-issued-token")])
def test_failed_or_short_expiry_token_cannot_reissue_inside_60_seconds(tmp_path, response):
    clock = Clock()
    client, transport = client_at(tmp_path, clock, [response, token_response()])
    with client:
        if isinstance(response, OSError):
            with pytest.raises(Refused, match="request failed"):
                client.token(priority="event", deadline=clock() + timedelta(seconds=10))
        else:
            client.token(priority="event", deadline=clock() + timedelta(seconds=10))
        clock.sleep(11)
        with pytest.raises(Refused, match="60 seconds"):
            client.token(priority="event", deadline=clock() + timedelta(seconds=10))
    clock.sleep(49)
    next_client, next_transport = client_at(tmp_path, clock, [token_response()])
    with next_client:
        next_client.token(priority="event", deadline=clock() + timedelta(seconds=10))
    assert len(transport.calls) == len(next_transport.calls) == 1


@pytest.mark.parametrize("expiry", [{}, {"expires_in": -1}, {"access_token_token_expired": "2026-11-01 00:00:00"}])
def test_no_invented_token_lifetime(tmp_path, expiry):
    clock = Clock()
    client, _ = client_at(tmp_path, clock, [{"access_token": "private-issued-token", **expiry}])
    with client, pytest.raises(Refused):
        client.token(priority="event", deadline=clock() + timedelta(seconds=10))


def test_one_owner_per_app_key_and_cache_permissions_fail_closed(tmp_path):
    clock = Clock()
    first, _ = client_at(tmp_path, clock, [])
    second, _ = client_at(tmp_path, clock, [])
    with first:
        with pytest.raises(Refused, match="owner process"):
            second.__enter__()
        replace_private(first.cache, {"last_attempt": clock().isoformat()})
        first.cache.chmod(0o644)
        with pytest.raises(Refused, match="owner-only"):
            first.token(priority="event", deadline=clock() + timedelta(seconds=10))


def test_secrets_and_tokens_only_in_owner_token_cache(tmp_path, capsys, caplog, fake_kernel):
    clock, secret = Clock(), credentials()
    reflected = {"rt_cd": "0", "msg1": secret.app_key + secret.app_secret + secret.cano + secret.hts_id + "private-issued-token another-reflected-token another-approval",
                 "access_token": "another-reflected-token", "approval_key": "another-approval"}
    client, transport = client_at(tmp_path, clock, [token_response(), reflected, {"approval_key": "approval-private-value"}])
    with client:
        result = client.quote("005930", book=True, priority="event", deadline=clock() + timedelta(seconds=10))
        approval = client.approval_probe(deadline=clock() + timedelta(seconds=10))
        store = Store(tmp_path / "records", clock=clock)
        store.seal("raw", "one", {"result": result, "approval": approval})
        cache = client.cache
    captured = capsys.readouterr()
    persisted = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file() and p != cache)
    persisted += captured.out + captured.err + caplog.text
    for value in (secret.app_key, secret.app_secret, secret.cano, secret.hts_id, "private-issued-token", "another-reflected-token", "approval-private-value"):
        assert value not in persisted
    assert all(call[1].startswith(BASE) for call in transport.calls)
    assert load(cache)["token"] == "private-issued-token"


def test_order_requests_require_consumable_intent_permit_even_with_valid_credentials(tmp_path):
    clock = Clock()
    client, transport = client_at(tmp_path, clock, [])
    with client, pytest.raises(Refused, match="OFF"):
        client.request("POST", ORDER, "VTTC0012U", body={}, deadline=clock() + timedelta(seconds=10))
    assert transport.calls == []


def test_invalid_expiry_cannot_put_response_secrets_in_exception(tmp_path):
    clock = Clock()
    client, _ = client_at(tmp_path, clock, [token_response(access_token_token_expired=credentials().app_secret)])
    with client, pytest.raises(Refused) as exc:
        client.token(priority="event", deadline=clock() + timedelta(seconds=10))
    assert credentials().app_secret not in str(exc.value)


def test_redirect_is_never_followed_and_transport_error_text_is_never_persisted(tmp_path):
    clock = Clock()
    c, transport = client_at(tmp_path, clock, [token_response(), (302, {"Location": "https://openapi.koreainvestment.com:9443"}, b"{}"),
                                             OSError(credentials().app_secret)])
    with c:
        result = c.quote("005930", book=True, priority="event", deadline=clock() + timedelta(seconds=10))
        assert result["error"] == "http_status_302"
        result2 = c.quote("005930", book=True, priority="event", deadline=clock() + timedelta(seconds=10))
        assert result2["error"] == "transport_error"
        assert credentials().app_secret not in json.dumps(result2)
    assert len(transport.calls) == 3


def test_transport_itself_also_refuses_other_hosts_before_network():
    with pytest.raises(Refused, match="allowlist"):
        HTTPS().request("GET", "https://evil.example/", headers={})


def test_auth_directory_symlink_refused_before_any_write_outside_root(tmp_path):
    root, outside = tmp_path / "own", tmp_path / "read-only-input"
    root.mkdir()
    outside.mkdir()
    (root / "auth").symlink_to(outside)
    client, transport = client_at(root, Clock(), [])
    with pytest.raises(Refused, match="symlink"):
        client.__enter__()
    assert list(outside.iterdir()) == []
    assert transport.calls == []
