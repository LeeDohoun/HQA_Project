"""The PAPER order operator tool only relays to the backend, which verifies against KIS."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import paper_orders


@pytest.fixture()
def backend(monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_BASE_URL", "http://backend.invalid/")
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", " operator-token\n")
    monkeypatch.setattr(paper_orders, "load_project_env", lambda: None)
    calls = []

    def respond(method, status=200, body=None):
        def call(url, **kwargs):
            calls.append((method, url, kwargs))
            payload = body if body is not None else {"executions": []}
            return SimpleNamespace(status_code=status, text=json.dumps(payload), json=lambda: payload)
        return call

    monkeypatch.setattr(paper_orders.requests, "get", respond("GET"))
    monkeypatch.setattr(paper_orders.requests, "post", respond("POST", body={"status": "ORDER_SUBMITTED"}))
    return SimpleNamespace(calls=calls, respond=respond, monkeypatch=monkeypatch)


def test_unknown_lists_the_backends_orders_with_the_internal_token(backend, capsys):
    assert paper_orders.main(["unknown"]) == 0
    method, url, kwargs = backend.calls[0]
    assert (method, url) == ("GET", "http://backend.invalid/api/v1/internal/trading/executions/unknown")
    assert kwargs["headers"] == {"X-HQA-Internal-Token": "operator-token"}
    assert json.loads(capsys.readouterr().out) == {"executions": []}


@pytest.mark.parametrize("argv,body", [
    (["adopt", "e1", "117057", "--note", "KIS 앱 주문내역"], {"brokerOrderId": "117057", "note": "KIS 앱 주문내역"}),
    (["not-submitted", "e1", "--note", "주문내역에 없음"], {"notSubmitted": True, "note": "주문내역에 없음"}),
])
def test_resolutions_send_exactly_one_finding_with_its_note(backend, argv, body):
    assert paper_orders.main(argv) == 0
    method, url, kwargs = backend.calls[0]
    assert (method, url) == ("POST", "http://backend.invalid/api/v1/internal/trading/executions/e1/resolution")
    assert kwargs["json"] == body


def test_a_backend_refusal_is_shown_and_fails(backend):
    backend.monkeypatch.setattr(paper_orders.requests, "post", backend.respond(
        "POST", status=409, body={"message": "BROKER_LISTS_POSSIBLE_ORDER:0000117057"}))
    with pytest.raises(SystemExit, match="HTTP 409.*BROKER_LISTS_POSSIBLE_ORDER"):
        paper_orders.main(["not-submitted", "e1", "--note", "n"])


def test_a_note_is_required(backend):
    with pytest.raises(SystemExit):
        paper_orders.main(["not-submitted", "e1"])
    assert not backend.calls


def test_backend_settings_are_required(monkeypatch):
    monkeypatch.delenv("BACKEND_INTERNAL_BASE_URL", raising=False)
    monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
    monkeypatch.setattr(paper_orders, "load_project_env", lambda: None)
    with pytest.raises(SystemExit, match="BACKEND_INTERNAL_BASE_URL"):
        paper_orders.main(["unknown"])
