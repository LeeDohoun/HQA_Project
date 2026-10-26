"""Unit tests must not reach data providers, model APIs, or local services."""

import os
from pathlib import Path
import socket

import pytest


@pytest.fixture(autouse=True)
def fresh_llm_admission_window(monkeypatch):
    """Each test starts with an empty LLM admission window; otherwise the tokens earlier tests'
    model calls admitted make later tests wait for the 60-second window to roll over."""
    from src.utils import llm_queue

    queue = llm_queue._GLOBAL_QUEUE
    monkeypatch.setattr(llm_queue, "_GLOBAL_QUEUE", llm_queue.LLMInvocationQueue(
        queue.max_concurrency, requests_per_minute=queue.requests_per_minute,
        tokens_per_minute=queue.tokens_per_minute, max_wait_seconds=queue.max_wait_seconds,
        window_seconds=queue.window_seconds))


@pytest.fixture(autouse=True)
def block_outbound_network(monkeypatch, request):
    live_kis_module = Path(__file__).with_name("test_kis_paper_trading.py").resolve()
    if request.node.path.resolve() == live_kis_module and os.getenv("RUN_KIS_LIVE_TESTS") == "1":
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def denied(*args, **kwargs):
        pytest.fail("Outbound network is disabled in unit tests; mock the provider explicitly.", pytrace=False)

    def connect(sock, address):
        if sock.family == socket.AF_UNIX:
            return original_connect(sock, address)
        denied()

    def connect_ex(sock, address):
        if sock.family == socket.AF_UNIX:
            return original_connect_ex(sock, address)
        denied()

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
