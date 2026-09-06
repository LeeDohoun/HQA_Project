from pathlib import Path
import runpy
import socket

import dotenv
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_outbound_sockets_are_rejected(method):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        with pytest.raises(pytest.fail.Exception, match="Outbound network is disabled"):
            getattr(connection, method)(("203.0.113.1", 443))


def test_dns_resolution_is_rejected():
    with pytest.raises(pytest.fail.Exception, match="Outbound network is disabled"):
        socket.getaddrinfo("provider.invalid", 443)


def test_kis_opt_in_does_not_enable_network_in_other_test_modules(monkeypatch):
    monkeypatch.setenv("RUN_KIS_LIVE_TESTS", "1")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        with pytest.raises(pytest.fail.Exception, match="Outbound network is disabled"):
            connection.connect(("203.0.113.1", 443))


def test_local_socketpair_and_in_process_api_client_still_work():
    first, second = socket.socketpair()
    with first, second:
        first.sendall(b"offline")
        assert second.recv(7) == b"offline"

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_kis_module_import_preserves_explicit_environment_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / "fixture.env"
    env_file.write_text("OPENAI_API_KEY=fixture-local-key\n", encoding="utf-8")
    load_dotenv = dotenv.load_dotenv

    def load_fixture_env(path, **kwargs):
        return load_dotenv(env_file, **kwargs)

    monkeypatch.setattr(dotenv, "load_dotenv", load_fixture_env)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-disabled")
    for key in ("KIS_PAPER_APP_KEY", "KIS_PAPER_APP_SECRET", "KIS_PAPER_ACCOUNT_NO"):
        monkeypatch.setenv(key, "")

    runpy.run_path(str(Path(__file__).with_name("test_kis_paper_trading.py")))

    import os

    assert os.environ["OPENAI_API_KEY"] == "offline-disabled"
