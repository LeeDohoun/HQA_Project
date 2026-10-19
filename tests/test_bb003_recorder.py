"""Synthetic BB003 decisions; no provider, shared data, or protected-period reads."""
from datetime import datetime, timedelta
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments.bb003 import KST, NotReady
from backtesting.experiments.bb003 import deployment, fetch, inputs, policy, recorder, storage


@pytest.fixture(autouse=True)
def operation_verification(monkeypatch):
    """Unit fixtures have synthetic manifests, not a private installed venv.

    Deployment regressions also call the original verifier against a temporary
    installation; this substitution applies only to the synthetic pipeline.
    """
    original = deployment.verify_operation
    def verify(store, *, mode="record"):
        activation = store.get("activation", "activation")
        digest = getattr(store, "test_manifest_sha256", activation["manifest_sha256"] if activation else "b" * 64)
        return {"manifest_sha256": digest, "source_files": {}}
    monkeypatch.setattr(deployment, "verify_operation", verify)
    from backtesting.experiments.bb003 import evaluate
    monkeypatch.setattr(evaluate, "verify_operation", verify)
    return original


def listing(day="20261201", number=1, corp="00000001", code="000010"):
    return {"rcept_no": day + f"{number:06d}", "rcept_dt": day, "corp_code": corp,
            "stock_code": code, "corp_cls": "Y", "corp_name": "synthetic common", "report_nm": "자기주식취득결정"}


def structured(item, **changes):
    return {"rcept_no": item["rcept_no"], "corp_code": item["corp_code"], "aqpln_stk_ostk": "1,234",
        "aqpln_prc_ostk": "12,345,678", "aq_mth": "장내", "aq_pp": "소각", "aqexpd_bgd": "2026-12-02",
        "aqexpd_edd": "2027-03-01", **changes}


class FakeClient:
    def __init__(self):
        self.failure, self.changes, self.calls = None, {}, 0

    def structured(self, item, *, reserve):
        reserve("tsstkAqDecsn.json", {"corp_code": item["corp_code"]})
        self.calls += 1
        if self.failure:
            raise fetch.FetchError(self.failure)
        row = structured(item, **self.changes)
        return row, storage.encoded({"status": "000", "list": [row]})

    def document(self, item, *, reserve):
        reserve("document.xml", {"rcept_no": item["rcept_no"]})
        text = "예정수량 1,234 주 예정금액 12,345,678 원"
        return text, text.encode()


@pytest.fixture
def fake_publication(monkeypatch):
    clock = [datetime(2026, 11, 30, 12, tzinfo=KST)]
    times = {}
    monkeypatch.setattr(storage, "publication_ns", lambda p: times.setdefault(str(p), int(clock[0].timestamp() * 1e9)))
    return clock, times


@pytest.fixture
def env(tmp_path, fake_publication):
    clock, _ = fake_publication
    sessions = pd.bdate_range("2026-07-01", "2029-06-30")
    rules = policy.Policy({"calendar_start": "2026-07-01", "calendar_through": "2029-06-30",
        "sessions": sessions.strftime("%Y-%m-%d").tolist(), "tax_through": "2029-12-31",
        "tax": [{"from": "2026-01-01", "KOSPI": .002, "KOSDAQ": .002}], "sources": []})
    source, store, client = tmp_path / "source", storage.Store(tmp_path / "state", clock=lambda: clock[0]), FakeClient()
    store.morning_guard = lambda: recorder.entry_plan(rules, rules.sessions.get_loc(clock[0].date().isoformat()))
    recorder.activate(store, commit="a" * 40, manifest_hash="b" * 64)
    clock[0] = datetime(2026, 12, 2, 8, 20, tzinfo=KST)
    item = listing()
    first = sessions[rules.entry("2026-12-01") - 64]
    completed = []
    for day in pd.date_range(first, "2026-12-01"):
        key = day.strftime("%Y%m%d")
        completed.append(key)
        storage.put(source / f"disclosures/dart_full/list/{day:%Y}/{key}.jsonl",
                    storage.encoded(item) if key == item["rcept_dt"] else b"")
    storage.put(source / "disclosures/dart_full/_state.json", storage.encoded({"completed_listing_days": completed}))
    codes = [f"{i * 10:06d}" for i in range(1, 62)]
    pos = rules.entry(item["rcept_dt"])
    for day in sessions[pos - 20:pos + 1]:
        rows = [{"stock_code": code, "stock_name": "synthetic common", "market": "KOSDAQ" if code == codes[-1] else "KOSPI",
            "trade_date": day.date().isoformat(), "calendar_status": "verified", "open": 10000., "close": 10000.,
            "base_price": 10000., "volume": 10000., "trading_value": 2e9, "market_cap": 1e11,
            "change_rate_pct": 0.} for code in codes]
        storage.put(source / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl", b"".join(storage.encoded(r) for r in rows))
    return source, store, rules, client, clock, item


def run(env):
    source, store, rules, client, _, _ = env
    with store.lock():
        result = recorder.prepare_day(store, source, rules, client)
        stamp = store.clock()
        if stamp.hour == 8 and store.get("morning_plan", stamp.date().isoformat()):
            sealed = recorder.seal_day(store, source, rules)
            for key, value in sealed["counts"].items():
                result["counts"][key] = value
        return result


@pytest.mark.parametrize("second,microsecond,valid", [(58, 0, True), (58, 999999, True), (59, 0, False), (59, 999999, False)])
def test_deadline_entire_boundary_second(tmp_path, fake_publication, second, microsecond, valid):
    clock, _ = fake_publication
    clock[0] = datetime(2026, 12, 2, 8, 58, second, microsecond, KST)
    store = storage.Store(tmp_path, clock=lambda: clock[0])
    deadline = datetime(2026, 12, 2, 8, 59, tzinfo=KST)
    proof = store.seal("decisions", "receipt", {"input_hashes": {}}, deadline=deadline)
    assert proof["valid"] is valid
    assert store.marker("decisions", "receipt").is_file()
    # Moving the clock cannot revoke/re-publish a rejected proof.
    before = store.marker("decisions", "receipt").read_bytes()
    clock[0] = deadline + timedelta(days=1)
    assert store.seal("decisions", "receipt", {"different": True}, deadline=deadline)["valid"] is valid
    assert store.marker("decisions", "receipt").read_bytes() == before


def test_real_kernel_private_marker_and_blob_integrity(tmp_path):
    store = storage.Store(tmp_path)
    checksum = store.blob(b"input")
    proof = store.seal("sample", "one", {"input_hashes": {"input": checksum}}, deadline=storage.now() + timedelta(seconds=30))
    marker = store.marker("sample", "one")
    assert proof["valid"] and storage.publication_ns(marker) == marker.parent.stat().st_mtime_ns
    storage.put(marker.parent / "intruder", b"bad")
    with pytest.raises(ValueError, match="private marker"):
        store.proof("sample", "one")


def test_candidate_crash_recovers_without_replacing_bytes(tmp_path, fake_publication, monkeypatch):
    clock, _ = fake_publication
    store = storage.Store(tmp_path, clock=lambda: clock[0])
    original = storage.os.link
    def crash(source, target):
        if Path(target).name == "complete.json":
            raise OSError("interrupted")
        original(source, target)
    monkeypatch.setattr(storage.os, "link", crash)
    with pytest.raises(OSError, match="interrupted"):
        store.seal("decisions", "one", {"value": 1})
    orphans = list(store.path("records/decisions/one").glob("*.json"))
    assert len(orphans) == 1 and store.get("decisions", "one") is None
    old = orphans[0].read_bytes()
    monkeypatch.setattr(storage.os, "link", original)
    store.seal("decisions", "one", {"value": 2})
    assert orphans[0].read_bytes() == old
    assert store.get("decisions", "one")["value"] == 2


def test_final_link_race_is_retained_as_late(tmp_path, fake_publication, monkeypatch):
    clock, times = fake_publication
    store = storage.Store(tmp_path, clock=lambda: clock[0])
    deadline = clock[0] + timedelta(seconds=20)
    original = storage.os.link
    def delay(source, target):
        original(source, target)
        if Path(target).name == "complete.json":
            times[str(target)] = int(deadline.timestamp() * 1e9)
    monkeypatch.setattr(storage.os, "link", delay)
    proof = store.seal("decisions", "race", {"value": 1}, deadline=deadline)
    assert not proof["valid"] and store.marker("decisions", "race").exists()
    assert store.get("decisions", "race") is None


def test_complete_pre_entry_payload_and_hashes(env):
    _, store, _, _, _, item = env
    assert run(env)["counts"]["included"] == 1
    record = store.get("decisions", item["rcept_no"])
    assert record["event"]["entry_date"] == "2026-12-02"
    assert len(record["pre_entry_candidates"]) == 60
    assert len([key for key in record["input_hashes"] if key.startswith("daily/")]) == 20
    assert record["matching"]["random"]["paths"] == 200
    for checksum in record["input_hashes"].values():
        assert storage.sha(store.bytes(checksum)) == checksum
    assert store.get("post_entry", item["rcept_no"]) is None


def test_missing_input_then_deadline_data_not_ready_no_retroactive_buy(env):
    _, store, _, client, clock, item = env
    client.failure = "original_document_unavailable"
    assert run(env)["counts"]["pending"] == 1
    clock[0] = datetime(2026, 12, 2, 9, tzinfo=KST)
    assert run(env)["counts"]["data_not_ready"] == 1
    client.failure = None
    clock[0] += timedelta(days=1)
    run(env)
    assert store.get("decisions", item["rcept_no"]) is None
    assert "original_document_unavailable" in store.get("data_not_ready", item["rcept_no"])["reason"]


def test_fixed_bounded_immutable_fetch_versions(env):
    _, store, rules, client, clock, item = env
    deadline = storage.aware(rules.schedule(item["rcept_dt"])["deadline"])
    client.failure = "missing"
    for offset in (0, 60, 300, 900):
        clock[0] = datetime(2026, 12, 2, 8, 20, tzinfo=KST) + timedelta(seconds=offset)
        assert fetch.acquire(store, item, client, deadline)[0] is None
    before = {p: store.path(store.proof("fetch", p)["record_path"]).read_bytes() for p in store.keys("fetch")}
    client.failure = None
    assert "exhausted" in fetch.acquire(store, item, client, deadline)[1]
    assert client.calls == 4
    assert len(before) == 4
    assert all(store.path(store.proof("fetch", p)["record_path"]).read_bytes() == raw for p, raw in before.items())


def test_not_due_retry_does_not_fetch(env):
    _, store, rules, client, _, item = env
    deadline = storage.aware(rules.schedule(item["rcept_dt"])["deadline"])
    client.failure = "missing"
    fetch.acquire(store, item, client, deadline)
    assert fetch.acquire(store, item, client, deadline)[1] == "fixed_retry_not_due"
    assert client.calls == 1


def test_missing_whole_market_is_pending_not_suspension(env):
    source, store, _, _, _, item = env
    path = source / "market/krx_daily/2026/20261201.jsonl"
    path.unlink()
    assert run(env)["counts"]["pending"] == 1
    assert store.get("decisions", item["rcept_no"]) is None


def test_partial_market_not_ready(env):
    source, _, rules, _, _, _ = env
    raw = (source / "market/krx_daily/2026/20261201.jsonl").read_bytes()
    partial = b"\n".join(line for line in raw.splitlines() if b'"KOSDAQ"' not in line)
    with pytest.raises(NotReady, match="partial_whole_market"):
        inputs.parse_daily(partial, pd.Timestamp("2026-12-01"))


def test_valid_alphanumeric_KRX_codes_match_BB001_parser(env):
    source, _, _, _, _, _ = env
    raw = (source / "market/krx_daily/2026/20261201.jsonl").read_bytes()
    rows = inputs.parse_daily(raw.replace(b'"000010"', b'"00088K"'), pd.Timestamp("2026-12-01"))
    assert "00088K" in rows


def test_excluded_decision_stays_in_repeat_clock(env):
    source, store, rules, client, clock, item = env
    client.changes = {"aq_mth": "장외"}
    run(env)
    assert store.get("decisions", item["rcept_no"])["reason"] == "not_on_market_acquisition"
    second = listing("20261202", 2)
    path = source / "disclosures/dart_full/list/2026/20261202.jsonl"
    storage.put(path, storage.encoded(second))
    state_path = source / "disclosures/dart_full/_state.json"
    state = storage.load(state_path)
    state["completed_listing_days"].append("20261202")
    state_path.write_bytes(storage.encoded(state))
    clock[0] = datetime(2026, 12, 3, 8, 20, tzinfo=KST)
    client.changes = {}
    run(env)
    assert store.get("decisions", second["rcept_no"])["reason"] == "repeat_0_63"
    assert client.calls == 1


@pytest.mark.parametrize("distance,is_repeat", [(0, True), (63, True), (64, False)])
def test_repeat_inclusive_session_clock(env, distance, is_repeat):
    _, _, rules, _, _, _ = env
    past = listing(rules.sessions[20].strftime("%Y%m%d"))
    current = listing(rules.sessions[20 + distance].strftime("%Y%m%d"), 2)
    assert (recorder.repeated(current, [past], rules.sessions) is not None) is is_repeat


def test_late_smaller_same_day_receipt_never_changes_decision(env):
    source, store, _, _, clock, item = env
    run(env)
    original = store.proof("decisions", item["rcept_no"])["record_sha256"]
    late = listing(number=0)
    (source / "disclosures/dart_full/list/2026/20261201.jsonl").write_bytes(storage.encoded(late) + storage.encoded(item))
    clock[0] = datetime(2026, 12, 2, 9, tzinfo=KST)
    run(env)
    assert store.proof("decisions", item["rcept_no"])["record_sha256"] == original
    assert store.get("omissions", late["rcept_no"])["reason"] == "late_discovered_candidate"
    assert store.get("decisions", late["rcept_no"]) is None


def test_post_entry_separate_immutable_and_no_return_matching(env):
    source, store, rules, _, clock, item = env
    run(env)
    decision_hash = store.proof("decisions", item["rcept_no"])["record_sha256"]
    assert recorder.post_entry(store, source, rules) == {"waiting_for_entry_daily": 1}
    clock[0] = datetime(2026, 12, 2, 18, 30, tzinfo=KST)
    assert recorder.post_entry(store, source, rules) == {"executed": 1}
    post = store.get("post_entry", item["rcept_no"])
    assert post["decision_sha256"] == decision_hash
    assert len(post["selection"]["control_features"]) >= 10
    daily = {c: {**r, "ret_1d": 12345., "close": -123.} for c, r in post["executability"].items()}
    match, reason = recorder.fixed_match(store.get("decisions", item["rcept_no"]), daily)
    assert reason is None and match == post["selection"]
    original = store.proof("post_entry", item["rcept_no"])["record_sha256"]
    (source / "market/krx_daily/2026/20261202.jsonl").write_bytes(b"invalid later revision")
    assert recorder.post_entry(store, source, rules) == {"already_sealed": 1}
    assert store.proof("post_entry", item["rcept_no"])["record_sha256"] == original


@pytest.mark.parametrize("day", ["2026-01-01", "2026-03-01", "2026-06-30"])
def test_protected_dates_fail_before_file_read(env, day, monkeypatch):
    source, store, rules, _, _, _ = env
    reader = inputs.Prices(source, rules, store, as_of="2029-06-30")
    monkeypatch.setattr(inputs, "read_bytes", lambda *a: pytest.fail("no protected file read"))
    with pytest.raises((ValueError, NotReady)):
        reader.read(day)


def test_pre_entry_reader_rejects_future_session(env):
    source, store, rules, _, _, _ = env
    reader = inputs.Prices(source, rules, store, as_of="2026-12-01")
    with pytest.raises(ValueError, match="point-in-time"):
        reader.read("2026-12-02")


def test_numeric_boundaries_do_not_verify_larger_number(env):
    _, _, _, _, _, item = env
    row = structured(item)
    assert not fetch.dart.verify_values(row, "91234 912345678")["verified"]


def test_http_client_exact_original_row_with_fake_transport(env):
    _, _, _, _, _, item = env
    class Response:
        content = storage.encoded({"status": "000", "list": [structured(item), structured({**item, "rcept_no": "20261201000009"})]})
        def raise_for_status(self):
            pass
    class Session:
        def get(self, url, **kwargs):
            assert url.endswith("tsstkAqDecsn.json")
            assert kwargs["params"]["bgn_de"] == item["rcept_dt"]
            assert kwargs["timeout"] == 15
            return Response()
    row, _ = fetch.HttpClient("synthetic-key", session=Session()).structured(item, reserve=lambda *a: None)
    assert row["rcept_no"] == item["rcept_no"]


def test_document_original_zip_only(env):
    from io import BytesIO
    from zipfile import ZipFile
    _, _, _, _, _, item = env
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("20261201000009.xml", "<body>wrong 999</body>")
        archive.writestr(item["rcept_no"] + ".xml", "<body>1,234 <p>12,345,678</p></body>")
    assert "12,345,678" in fetch.document_text(stream.getvalue(), item["rcept_no"])
    with pytest.raises(fetch.FetchError, match="original_receipt"):
        fetch.document_text(stream.getvalue(), "20261201000000")


@pytest.mark.parametrize("stage", ["structured", "document"])
def test_interrupted_http_is_reserved_before_wire_and_counts_after_restart(env, stage):
    _, store, rules, _, clock, item = env
    calls, snapshots = [], {}
    class Response:
        content = storage.encoded({"status": "000", "list": [structured(item)]})
        def raise_for_status(self):
            pass
    class Session:
        def get(self, url, **kwargs):
            attempt = len(store.keys("fetch_started"))
            key = f"{item['rcept_no']}.{attempt:02d}"
            reservation = store.get("fetch_started", key)
            assert reservation["attempt"] == attempt and reservation["started_at"]
            request_keys = [k for k in store.keys("http_started") if k.startswith(key + ".")]
            request = store.get("http_started", request_keys[-1])
            assert request["attempt"] == attempt and request["started_at"]
            assert request["reservation_sha256"] == store.proof("fetch_started", key)["record_sha256"]
            calls.append(url.rsplit("/", 1)[-1])
            if stage == "structured" or url.endswith("document.xml"):
                raise KeyboardInterrupt("simulated process interruption")
            return Response()
    deadline = storage.aware(rules.entry_schedule(item["rcept_dt"])["deadline"])
    first = clock[0]
    for attempt, offset in enumerate(fetch.RETRY_OFFSETS, 1):
        clock[0] = first + timedelta(seconds=offset)
        restarted = storage.Store(store.root.parent.parent, clock=lambda: clock[0])
        with pytest.raises(KeyboardInterrupt):
            fetch.acquire(restarted, item, fetch.HttpClient("test-key", session=Session()), deadline)
        assert len(store.keys("fetch_started")) == attempt
        assert not store.keys("fetch")
        snapshots[attempt] = store.path(store.proof("fetch_started", f"{item['rcept_no']}.{attempt:02d}")["record_path"]).read_bytes()
    assert "exhausted:interrupted_attempt" in fetch.acquire(store, item, fetch.HttpClient("test-key", session=Session()), deadline)[1]
    assert len(calls) == (4 if stage == "structured" else 8)
    for attempt, raw in snapshots.items():
        assert store.path(store.proof("fetch_started", f"{item['rcept_no']}.{attempt:02d}")["record_path"]).read_bytes() == raw


@pytest.mark.parametrize("name", ["20250701000001.xml.zip", "20250701000002.html.zip", "20250701000003.htm.zip"])
def test_stored_real_format_zip_uses_BB001_encoding_and_body_normalisation(name):
    from io import BytesIO
    from zipfile import ZipFile
    from src.ingestion.dart import DartDisclosureCollector
    path = Path(__file__).parent / "fixtures/bb003" / name
    receipt = name.split(".")[0]
    blob = path.read_bytes()
    normalizer = object.__new__(DartDisclosureCollector)
    with ZipFile(BytesIO(blob)) as archive:
        inner = archive.namelist()[0]
        decoded, _, _ = normalizer._decode_bytes_with_candidates(archive.read(inner))
        expected = normalizer._sanitize_body_text(normalizer._normalize_inner_document_text(inner, decoded))
    text = fetch.document_text(blob, receipt)
    assert text == expected
    assert "취득예정주식" in text and "12,345,678" in text
    assert "999999999" not in text
    assert fetch.dart.verify_values(structured(listing()), text)["verified"]


def test_2026_calendar_records_entry_and_defers_unverified_exit(env):
    source, store, rules, client, clock, item = env
    bounded = policy.Policy({**rules.data, "calendar_through": "2026-12-15",
        "sessions": rules.sessions[rules.sessions <= "2026-12-15"].strftime("%Y-%m-%d").tolist()})
    with pytest.raises(NotReady, match="21st_session"):
        bounded.schedule(item["rcept_dt"])
    assert run((source, store, bounded, client, clock, item))["counts"]["included"] == 1
    decision = store.get("decisions", item["rcept_no"])
    assert decision["event"]["entry_date"] == "2026-12-02"
    assert "exit_date" not in decision["event"]
    clock[0] = datetime(2026, 12, 2, 18, 30, tzinfo=KST)
    assert recorder.post_entry(store, source, bounded)["executed"] == 1


def test_prepared_nineteen_day_features_match_unchanged_BB001(env):
    from backtesting.experiments import bb001
    source, store, rules, _, _, item = env
    generator = np.random.default_rng(17)
    pos = rules.entry(item["rcept_dt"])
    for index, day in enumerate(rules.sessions[pos - 20:pos]):
        path = source / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl"
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        for row in rows:
            row["change_rate_pct"] = float(generator.uniform(-8, 8))
            row["trading_value"] = float(generator.uniform(1e8, 3e8))
        if index == 0:
            rows = [r for r in rows if r["stock_code"] != "000010"]
            rows[1]["trading_value"] = None
            rows[2]["change_rate_pct"] = None
        if index == 19:
            rows[3]["close"] = 999.
            rows[4]["market_cap"] = None
        path.write_bytes(b"".join(storage.encoded(row) for row in rows))
    reader = inputs.Prices(source, rules, store, as_of="2026-12-02")
    expected, expected_counts = bb001._pre_entry(reader, pos)
    prepared = reader.prepare_entry(pos)
    assert len(prepared["input_hashes"]) == 19
    actual, actual_counts, hashes = reader.finish_entry(prepared)
    assert actual_counts == expected_counts
    assert actual == expected
    assert len(hashes) == 20


def test_night_preparation_is_immutable_idempotent_and_keeps_repeat_revisions(env):
    source, store, rules, client, clock, item = env
    clock[0] = storage.aware("2026-12-01T18:05:00+09:00")
    recorder.prepare_day(store, source, rules, client)
    key = store.keys("preparations")[0]
    original = store.proof("preparations", key)["record_sha256"]
    recorder.prepare_day(store, source, rules, client)
    assert store.keys("preparations") == [key] and client.calls == 1
    earlier = listing(number=0)
    path = source / "disclosures/dart_full/list/2026/20261201.jsonl"
    path.write_bytes(storage.encoded(earlier) + storage.encoded(item))
    recorder.prepare_day(store, source, rules, client)
    assert store.proof("preparations", key)["record_sha256"] == original
    latest = store.get("preparations", item["rcept_no"] + ".000001")
    assert latest["reason"] == "same_day_repeat"
    clock[0] = storage.aware("2026-12-02T08:03:00+09:00")
    recorder.seal_day(store, source, rules)
    assert store.get("decisions", item["rcept_no"])["reason"] == "same_day_repeat"


def test_capture_unknown_needed_date_records_readiness_before_source_read(env, monkeypatch):
    source, store, rules, _, clock, _ = env
    bounded = policy.Policy({**rules.data, "calendar_through": "2026-12-31",
        "sessions": rules.sessions[rules.sessions <= "2026-12-31"].strftime("%Y-%m-%d").tolist()})
    clock[0] = datetime(2027, 1, 1, 18, 30, tzinfo=KST)
    monkeypatch.setattr(inputs, "read_bytes", lambda *a: pytest.fail("must not read before needed calendar"))
    result = recorder.capture_prices(store, source, bounded)
    assert result["status"] == "data_not_ready" and "2027-01-01" in result["reason"]
    assert store.get("readiness", store.keys("readiness")[0])["recoverable"]


def test_fetch_result_without_start_reservation_fails_instead_of_resetting_budget(env):
    _, store, rules, client, _, item = env
    store.seal("fetch", item["rcept_no"] + ".01", {"status": "pending", "reason": "legacy unreserved attempt"})
    with pytest.raises(ValueError, match="no start reservation"):
        fetch.acquire(store, item, client, storage.aware(rules.entry_schedule(item["rcept_dt"])["deadline"]))
    assert client.calls == 0
