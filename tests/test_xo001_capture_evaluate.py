from datetime import timedelta
import json
from pathlib import Path

import numpy as np
import pytest

from backtesting.experiments.xo001 import Refused, aware, now
from backtesting.experiments.xo001.activation import CONFIG
from backtesting.experiments.xo001.evaluate import bootstrap, evaluate, measure, observation, order_sizes, statistics
from backtesting.experiments.xo001.fields import Profile
from backtesting.experiments.xo001.recorder import Recorder, clock_valid, reconcile
from backtesting.experiments.xo001.roster import BB003
from backtesting.experiments.xo001.storage import MAIN, Store, encoded, load, put, safe_root, sha
from test_xo001_support import Clock, Quotes, bb003_record, fake_kernel, good_clock, payload, plan, profile_data, simple_roster, fake_runtime


def recorder_at(tmp_path, clock=None, transform=None):
    clock = clock or Clock()
    store = Store(tmp_path / "own", clock=clock)
    simple_roster(store)
    return store, Recorder(store, Quotes(clock, transform), Profile(profile_data()), plan(), clock_status=good_clock), clock


def full_capture(tmp_path, *, transform=None):
    store, recorder, clock = recorder_at(tmp_path, transform=transform)
    for name in CONFIG["snapshots"]:
        cutoff = aware(plan()["times"][name])
        clock.set(cutoff - timedelta(seconds=10))
        recorder.block("005930", "event", book=True, phase="pre", deadline=cutoff)
        clock.set(cutoff)
        recorder.snapshot(name)
    recorder.seal_pre()
    clock.set("2026-11-03T09:00:01+09:00")
    recorder.block("005930", "event", book=False, phase="post", deadline=clock() + timedelta(seconds=3))
    clock.set("2026-11-03T09:05:00+09:00")
    recorder.seal_post()
    return store, recorder, clock


def test_real_kernel_completion_no_clobber_and_marker_tampering(tmp_path):
    store = Store(tmp_path / "own")
    proof = store.seal("proof", "one", {"x": 1}, deadline=now() + timedelta(seconds=10))
    assert proof["valid"]
    original = store.path(proof["record_path"]).read_bytes()
    assert store.seal("proof", "one", {"x": 2})["record_sha256"] == proof["record_sha256"]
    assert store.path(proof["record_path"]).read_bytes() == original
    with pytest.raises(Refused, match="immutable"):
        put(store.path(proof["record_path"]), b"different")
    (store.marker("proof", "one").parent / "extra").write_text("tamper")
    with pytest.raises(Refused, match="directory"):
        store.get("proof", "one")


@pytest.mark.parametrize("root", [MAIN, MAIN / "data/xo001", "/opt/hqa/xo001", "/var/lib/hqa/data/forward/xo001"])
def test_output_never_overlaps_main_or_shared_inputs(root):
    with pytest.raises(Refused):
        safe_root(root)


def test_output_and_input_symlink_and_env_paths_refused(tmp_path):
    (tmp_path / "linked").symlink_to(tmp_path / "target")
    with pytest.raises(Refused):
        Store(tmp_path / "linked")
    store = Store(tmp_path / "own")
    for relative in ("../other", "/tmp/other", ".env.private", "a/.env-any"):
        with pytest.raises(Refused):
            store.path(relative)
    with pytest.raises(Refused):
        Store(tmp_path / "input/output", inputs=(tmp_path / "input",))


def test_five_snapshots_final_manifest_and_no_future_additions(tmp_path, fake_kernel):
    store, recorder, clock = full_capture(tmp_path)
    pre = store.get("preseal", "2026-11-03")
    assert set(pre["snapshot_hashes"]) == set(CONFIG["snapshots"])
    assert len(pre["raw_hashes"]) == 5
    original = encoded(pre)
    with pytest.raises(Refused, match="closed"):
        recorder.block("005930", "event", book=True, phase="pre", deadline=clock() + timedelta(seconds=10))
    assert encoded(recorder.seal_pre()["record"]) == original
    assert store.get("snapshot", "2026-11-03.s0859")["states"]["005930"]["expected_price"] == 10000
    post = store.get("postseal", "2026-11-03")["states"]["005930"]
    assert post["state"] == "observed"
    assert post["auction_price"] == 10020
    assert post["auction_volume"] == 100
    assert post["first_60s_total_volume"] is None


def test_full_second_publication_bound_rejects_close_to_085930_and_cannot_repair(tmp_path, fake_kernel):
    store, recorder, clock = recorder_at(tmp_path)
    clock.set("2026-11-03T08:59:29.500000+09:00")
    proof = recorder.seal_pre()
    assert proof["valid"] is False
    raw = store.path(proof["record_path"]).read_bytes()
    clock.set("2026-11-03T09:00:00+09:00")
    assert recorder.seal_pre()["record_sha256"] == proof["record_sha256"]
    assert store.path(proof["record_path"]).read_bytes() == raw
    other, later, clock2 = recorder_at(tmp_path / "late")
    clock2.set("2026-11-03T08:59:31+09:00")
    with pytest.raises(Refused, match="deadline"):
        later.seal_pre()


def test_latest_received_by_cutoff_not_later_state(tmp_path, fake_kernel):
    store, recorder, clock = recorder_at(tmp_path)
    clock.set("2026-11-03T08:29:50+09:00")
    recorder.block("005930", "event", book=True, phase="pre", deadline=clock() + timedelta(seconds=3))
    clock.set("2026-11-03T08:30:05+09:00")
    recorder.client.transform = lambda value: {**value, "book": {**value["book"], "expected_price": "11000"}}
    recorder.block("005930", "event", book=True, phase="pre", deadline=clock() + timedelta(seconds=3))
    snapshot = recorder.snapshot("s0830")
    assert snapshot["states"]["005930"]["expected_price"] == 10000
    assert snapshot["states"]["005930"]["last_provider_at"] == "2026-11-03T08:29:50+09:00"


def test_restart_preserves_blocks_snapshots_and_connection_gaps(tmp_path, fake_kernel):
    store, recorder, clock = full_capture(tmp_path)
    before = {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    client = Quotes(clock)
    client.connection_id = "new-connection"
    restart = Recorder(store, client, Profile(profile_data()), plan(), clock_status=good_clock)
    assert restart.latest[("005930", "book")]["normalized"]["expected_price"] == 10000
    restart.seal_pre()
    restart.seal_post()
    assert before == {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("field,value", [("date", "20261102"), ("exchange", "NXT"), ("session", "AFTER"),
                                        ("provider_time", None), ("ask_price_10", None)])
def test_wrong_day_venue_session_or_missing_depth_is_unknown(field, value):
    clock = Clock()
    data = payload(clock, **{field: value})
    result = Profile(profile_data()).normalize({"data": data, "error": None}, "2026-11-03", book=True)
    assert result["state"] == "unknown"


def test_first_receive_is_not_assumed_to_be_auction_and_receive_delay_is_not_open_delay(tmp_path, fake_kernel):
    def no_auction(value):
        if "trade" in value:
            value["trade"].update(auction_flag="", provider_time="090001")
        return value
    store, recorder, clock = recorder_at(tmp_path, transform=no_auction)
    clock.set("2026-11-03T09:00:55+09:00")
    recorder.block("005930", "event", book=False, phase="post", deadline=clock() + timedelta(seconds=3))
    clock.set("2026-11-03T09:05:00+09:00")
    opening = recorder.seal_post()["record"]["states"]["005930"]
    assert opening["opening_category"] == "unknown"
    assert opening["auction_volume"] is None
    assert opening["state"] == "unknown"


def test_unknown_vs_confirmed_absent_coverage_and_pairs(tmp_path, fake_kernel):
    def halted(value):
        value["book" if "book" in value else "trade"]["status"] = "HALT"
        return value
    store, _, _ = full_capture(tmp_path, transform=halted)
    result = observation(store, "2026-11-03", "005930", "company")
    assert result["complete"] is True
    assert result["valid_pair"] is False
    assert result["required_states"] == ["confirmed_absent"] * 6
    assert result["opening_category"] == "official_halt"
    missing = observation(store, "2026-11-03", "000660", "other")
    assert missing["complete"] is False
    assert missing["opening_category"] == "unknown"


def test_clock_failure_records_unknown_and_no_missing_response_as_halt(tmp_path, fake_kernel):
    store, recorder, clock = recorder_at(tmp_path)
    recorder.clock_status = lambda: {"synchronized": False, "error_bound_seconds": 5}
    recorder.block("005930", "event", book=True, phase="pre", deadline=clock() + timedelta(seconds=3))
    row = store.get("pre_raw", store.keys("pre_raw")[0])
    assert row["normalized"]["state"] == "unknown"
    assert not clock_valid({"synchronized": True, "error_bound_seconds": None})


def test_floor_sizes_ten_level_depth_and_strict_threshold_quantiles():
    asks = [[9000, 2], [10000, 3], [10001, 500], *[[0, 0] for _ in range(7)]]
    result = order_sizes(10000, asks, 200)
    assert result["1000000"]["quantity"] == 100
    assert result["displayed_ask_depth"] == 5
    assert result["10000000"]["order_to_displayed_depth"] == 200
    assert result["1000000"]["order_to_auction_volume"] == .5
    assert order_sizes(2_000_000, asks)["1000000"]["quantity"] == 0
    assert order_sizes(10000, asks[:-1])["displayed_ask_depth"] is None
    rows = [{"valid_pair": True, "krx_open": x, "expected_price": 10000, "complete": True,
             "opening_category": "unknown"} for x in (10000, 10020, 10040, 10080)]
    result = statistics(rows)
    differences = np.array([0, 20, 40, 80])
    assert result["q25_bp"] == pytest.approx(np.quantile(differences, .25, method="linear"))
    assert result["q50_bp"] == pytest.approx(30)


def measurement_rows(count=100):
    return [{"entry_date": f"2027-01-{i // 2 + 1:02d}", "company": str(i % 40), "complete": True,
             "valid_pair": True, "krx_open": 10000 + i, "expected_price": 10000,
             "opening_category": "auction_confirmed", "daily_executable": True} for i in range(count)]


def test_measurement_status_requires_all_four_thresholds_and_date_bootstrap_is_reproducible():
    rows = measurement_rows()
    complete = measure(rows)
    assert complete["status"] == "measurement_complete"
    assert complete["bootstrap"] == bootstrap(rows)
    assert complete["bootstrap"]["replicates"] == 2000
    for change in ("pairs", "companies", "days", "coverage"):
        other = [dict(r) for r in rows]
        if change == "pairs":
            other[0]["valid_pair"] = False
        elif change == "companies":
            for row in other:
                row["company"] = "one"
        elif change == "days":
            for row in other:
                row["entry_date"] = "2027-01-01"
        else:
            for row in other[:6]:
                row["complete"] = False
        assert measure(other)["status"] == "measurement_insufficient"
    assert measure([])["status"] == "measurement_insufficient"


def test_evaluator_refuses_before_freeze_without_needing_future_calendar(tmp_path, fake_runtime):
    store = Store(tmp_path / "own", clock=Clock())
    class NoFuture:
        def freeze(self):
            pytest.fail("future calendar must not be needed before END")
    with pytest.raises(Refused, match="before the fixed freeze"):
        evaluate(store, NoFuture(), BB003(tmp_path / "bb003", store))


def test_later_daily_reconciliation_is_immutable_and_not_a_price_filter(tmp_path, fake_kernel, fake_runtime):
    store, recorder, clock = full_capture(tmp_path)
    source = tmp_path / "krx"
    path = source / "market/krx_daily/2026/20261103.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{"trade_date": "2026-11-03", "calendar_status": "verified", "stock_code": "005930", "market": "KOSPI",
             "open": 10020, "volume": 1, "base_price": 10000},
            {"trade_date": "2026-11-03", "calendar_status": "verified", "stock_code": "000660", "market": "KOSDAQ",
             "open": 13000, "volume": 0, "base_price": 10000}]
    path.write_bytes(b"".join(encoded(row) for row in rows))
    clock.set("2026-11-03T18:00:00+09:00")
    class FakeCalendar:
        def day(self, day):
            return plan(day)
    proof = reconcile(store, source, FakeCalendar(), None, "2026-11-03")
    value = observation(store, "2026-11-03", "005930", "company")
    assert value["valid_pair"]
    assert value["daily_executable"] is True
    assert value["auction_daily_reconciliation"]["equal"]
    path.unlink()
    assert reconcile(store, source, FakeCalendar(), None, "2026-11-03")["record_sha256"] == proof["record_sha256"]
