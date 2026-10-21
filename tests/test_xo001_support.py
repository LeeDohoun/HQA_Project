"""Offline XO001 test fixtures; no provider or project configuration imports."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from backtesting.experiments.xo001 import KST, aware
from backtesting.experiments.xo001 import storage, roster
from backtesting.experiments.xo001.calendar import NORMAL, TIME_KEYS
from backtesting.experiments.xo001.fields import BOOK_FIELDS, TRADE_FIELDS, Profile
from backtesting.experiments.xo001.kis import Credentials, Redactor

SPEC = b"synthetic official specification; tests only"


class Clock:
    def __init__(self, at="2026-11-03T08:25:00+09:00"):
        self.at, self.mono = aware(at), 100.

    def __call__(self):
        return self.at

    def set(self, value):
        self.at = aware(value)

    def sleep(self, seconds):
        self.at += timedelta(seconds=seconds)
        self.mono += seconds


def kernel_time(marker):
    proof = json.loads(marker.read_bytes())
    return int(aware(proof["prepared_at"]).timestamp() * 1e9) + 10_000_000


@pytest.fixture
def fake_kernel(monkeypatch):
    monkeypatch.setattr(storage, "publication_ns", kernel_time)
    monkeypatch.setattr(roster, "publication_ns", kernel_time)


def plan(day="2026-11-03"):
    return {"date": day, "session": True, "special": False, "source": "fake_verified_calendar",
            "times": {k: day + "T" + v + "+09:00" for k, v in zip(TIME_KEYS, NORMAL)}}


def credentials():
    return Credentials("private-app-key-xo001", "private-app-secret-xo001", "12345678", "01", "private-hts-xo001")


def profile_data():
    book = {key: "book." + key for key in BOOK_FIELDS}
    for side in ("ask", "bid"):
        for kind in ("price", "quantity"):
            for i in range(1, 11):
                book[f"{side}_{kind}_{i}"] = f"book.{side}_{kind}_{i}"
    return {"book": book, "trade": {**{k: "trade." + k for k in TRADE_FIELDS},
                                   "auction_flag": "trade.auction_flag", "auction_volume": "trade.auction_volume"},
        "preopen_values": ["PRE"], "krx_values": ["KRX"], "halt_values": ["HALT"], "delay_values": ["DELAY"],
        "auction_values": ["AUCTION"], "auction_volume_definition": "verified auction matched shares, not cumulative volume",
        "official_spec_sha256": storage.sha(SPEC)}


def payload(clock, *, book=True, **changes):
    day, hour = clock().strftime("%Y%m%d"), clock().strftime("%H%M%S")
    if book:
        value = {"date": day, "exchange": "KRX", "session": "PRE", "provider_time": hour,
                 "expected_price": "10000", "expected_volume": "100", "expected_code": "PRE", "status": "NORMAL",
                 "vi": "N", "upper_limit": "13000", "lower_limit": "7000", "total_ask": "1000", "total_bid": "1000"}
        for side in ("ask", "bid"):
            for i in range(1, 11):
                value[f"{side}_price_{i}"] = str(9900 + i * 10)
                value[f"{side}_quantity_{i}"] = str(i)
    else:
        value = {"date": day, "exchange": "KRX", "provider_time": hour, "price": "10020", "quantity": "3",
                 "status": "NORMAL", "auction_flag": "AUCTION", "auction_volume": "100"}
    value.update(changes)
    return {"rt_cd": "0", "book" if book else "trade": value}


class Transport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        if kwargs.get("before_send"):
            kwargs["before_send"]()
        self.calls.append((method, url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, tuple):
            return value
        return 200, {"content-type": "application/json"}, storage.encoded(value)


class Quotes:
    def __init__(self, clock, transform=None):
        self.clock, self.transform = clock, transform
        self.connection_id, self.sequence = "fake-connection", 0
        self.credentials, self.redactor = credentials(), Redactor(credentials())

    def monotonic(self):
        return self.clock.mono

    def quote(self, code, *, book, priority, deadline):
        self.sequence += 1
        value = payload(self.clock, book=book)
        if self.transform:
            value = self.transform(value)
        at = self.clock().astimezone(timezone.utc).isoformat()
        return {"data": value, "raw": storage.encoded(value).decode(), "error": None,
                "requested_utc": at, "received_utc": at, "requested_monotonic": self.clock.mono,
                "received_monotonic": self.clock.mono, "sequence": self.sequence,
                "connection_id": self.connection_id, "response_headers": {}}


def good_clock():
    return {"synchronized": True, "error_bound_seconds": .01}


def simple_roster(store, day="2026-11-03", codes=("005930",)):
    value = {"date": day, "events": [{"rcept_no": "20261102000001", "stock_code": c, "corp_code": c,
              "entry_date": day, "rcept_dt": "20261102"} for c in codes],
             "matched": [], "baseline": [], "label": "forward"}
    original = store.clock
    store.clock = lambda: aware(day + "T08:24:58+09:00")
    try:
        return store.seal("roster", day, value, deadline=day + "T08:25:00+09:00")["record"]
    finally:
        store.clock = original


def bb003_record(root, kind, key, value, clock, deadline=None):
    record = {**value, "record_kind": kind, "record_key": key, "label": "forward", "recorded_at": clock().isoformat()}
    raw = storage.encoded(record)
    relative = f"records/{kind}/{key}.json"
    storage.put(Path(root) / relative, raw)
    marker = Path(root) / f"sealed/{kind}/{key}/complete.json"
    proof = {"record_path": relative, "record_sha256": storage.sha(raw), "prepared_at": clock().isoformat(),
             "publication_clock": "private_directory_mtime_ns", "publication_precision_ns": storage.PRECISION_NS,
             "deadline": deadline, "inclusive": False, "measurement": False}
    storage.put(marker, storage.encoded(proof))
    return record


@pytest.fixture
def fake_runtime(monkeypatch):
    """Only legacy capture tests bypass activation; integrity tests use real verify."""
    evidence = {"binding": {"code_sha256": "a" * 64, "environment_sha256": "b" * 64},
                "activation_sha256": "c" * 64, "approved_amendment_sha256": []}
    for module in ("recorder", "evaluate"):
        monkeypatch.setattr("backtesting.experiments.xo001." + module + ".runtime_verification", lambda _: evidence)
        monkeypatch.setattr("backtesting.experiments.xo001." + module + ".verify_unchanged", lambda *_: None)
    return evidence
