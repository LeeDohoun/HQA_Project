from datetime import timedelta
import json
import multiprocessing
import os
from pathlib import Path
import time

import numpy as np
import pytest

from backtesting.experiments.xo001 import Refused, aware, now
from backtesting.experiments.xo001.calendar import Calendar, time_map
from backtesting.experiments.xo001.rate import INTERVAL_SECONDS, RateManager, RateUnavailable
from backtesting.experiments.xo001.roster import BB003, available_bytes, boundaries, draw, eligibility, lower_bin, make_roster
from backtesting.experiments.xo001.storage import Store, encoded, load, replace_private, sha
from test_xo001_support import Clock, bb003_record, fake_kernel, plan


def _rate_worker(root, priority, queue):
    RateManager(root).acquire(priority, now() + timedelta(seconds=10))
    queue.put((priority, time.time()))


def test_rate_budget_is_persisted_and_prioritized_across_processes(tmp_path):
    context = multiprocessing.get_context("fork")
    root = tmp_path / "control"
    replace_private(root / "rate.json", {"last": time.time(), "queue": []})
    queue = context.Queue()
    children = []
    for priority in ("baseline", "matched", "event"):
        process = context.Process(target=_rate_worker, args=(root, priority, queue))
        process.start()
        children.append(process)
        time.sleep(.04)
    values = [queue.get(timeout=10) for _ in children]
    for child in children:
        child.join(10)
        assert child.exitcode == 0
    assert [p for p, _ in values] == ["event", "matched", "baseline"]
    assert all(b[1] - a[1] >= INTERVAL_SECONDS - .03 for a, b in zip(values, values[1:]))
    assert load(root / "rate.json")["queue"] == []
    queue.close()


def test_rate_restart_deadline_and_clock_backwards_are_conservative(tmp_path):
    clock = Clock()
    first = RateManager(tmp_path, clock=clock, sleeper=clock.sleep)
    first.acquire("event", clock() + timedelta(seconds=2))
    second = RateManager(tmp_path, clock=clock, sleeper=clock.sleep)
    with pytest.raises(RateUnavailable, match="unobserved"):
        second.acquire("baseline", clock() + timedelta(seconds=.5))
    assert load(tmp_path / "rate.json")["queue"] == []
    clock.sleep(-10)
    with pytest.raises(RateUnavailable):
        second.acquire("event", clock() + timedelta(seconds=1))
    assert load(tmp_path / "rate.json")["last"] > clock().timestamp()


@pytest.mark.parametrize("day", ["2026-06-03", "2026-07-17", "2026-11-07"])
def test_shared_corrected_calendar_closures(day):
    assert Calendar().day(day)["session"] is False


@pytest.mark.parametrize("day", ["2026-11-19", "2027-01-04"])
def test_pending_special_and_unverified_future_refuse(day):
    with pytest.raises(Refused, match="calendar_unverified"):
        Calendar().day(day)


def test_normal_day_never_requires_the_2027_calendar():
    result = Calendar().day("2026-11-03")
    assert time_map(result)["open"] == aware("2026-11-03T09:00:00+09:00")
    assert result["special"] is False
    assert len(Calendar().previous("2026-11-03", 20)) == 20


def test_special_day_needs_all_explicit_times_and_official_notice(tmp_path, fake_kernel):
    clock = Clock()
    store = Store(tmp_path / "own", clock=clock)
    calendar = Calendar(store)
    data = {"date": "2026-11-19", "session": True,
            "times": {k: (aware(v) + timedelta(days=16, hours=1)).isoformat() for k, v in plan()["times"].items()},
            "source_url": "https://kind.krx.co.kr/test-notice", "published_at": "2026-11-01T10:00:00+09:00"}
    malformed = {**data, "times": {k: v for k, v in data["times"].items() if k != "flatten"}}
    with pytest.raises(Refused, match="every explicit"):
        calendar.add_official(malformed, b"notice")
    with pytest.raises(Refused, match="official"):
        calendar.add_official({**data, "source_url": "https://evil.test/notice"}, b"notice")
    calendar.add_official(data, b"notice")
    special = calendar.day("2026-11-19")
    assert special["special"]
    assert time_map(special)["open"].hour == 10
    assert time_map(special)["flatten"].hour == 16


def history_rows():
    rows = {f"{i:05d}0": {"market": "KOSPI" if i % 2 else "KOSDAQ", "stock_name": "company",
            "calendar_status": "verified", "close": 1000., "trading_value": 1e8,
            "ret_1d": .01, "market_cap": float(i * 100_000)} for i in range(1, 31)}
    return [{code: dict(row) for code, row in rows.items()} for _ in range(20)]


def test_bb001_eligibility_thresholds_and_return_reduction():
    history = history_rows()
    history[-1]["000010"]["close"] = 999
    history[0]["000020"]["trading_value"] = 0
    history[2]["000030"]["ret_1d"] = float("nan")
    history[-1]["000040"]["stock_name"] = "a스팩"
    history[-1]["000050"]["market_cap"] = 0
    history[1]["000060"]["calendar_status"] = "unknown"
    features, counts = eligibility(history)
    assert not set(f"{i:05d}0" for i in range(1, 7)) & set(features)
    assert features["000070"]["prior20"] == float(np.prod(1 + np.array([.01] * 20)) - 1)
    assert features["000070"]["adv"] == 1e8
    assert counts["eligible"] == 24
    with pytest.raises(Refused):
        eligibility(history[:-1])


def test_draws_are_exact_sorted_seeded_and_do_not_relax():
    features = {f"{i:05d}0": {"market": "KOSPI" if i <= 60 else "KOSDAQ", "market_cap": float(i % 20),
                "prior20": float(i % 5)} for i in reversed(range(1, 121))}
    event = {"rcept_no": "20261102000001", "stock_code": "000010"}
    result = draw("2026-11-03", [event], features, {"000010", "000020"})
    repeated = draw("2026-11-03", [event], dict(reversed(list(features.items()))), {"000010", "000020"})
    assert result == repeated
    matched = result["matched"][0]
    expected = np.random.default_rng(int(event["rcept_no"])).choice(matched["candidates"], size=min(2, len(matched["candidates"])), replace=False).tolist()
    assert matched["selected"] == expected
    assert matched["candidates"] == sorted(matched["candidates"])
    assert "000020" not in matched["candidates"]
    rng = np.random.default_rng(20261103)
    for group in result["baseline"]:
        assert group["selected"] == rng.choice(group["candidates"], size=min(1, len(group["candidates"])), replace=False).tolist()
    assert [g["market"] for g in result["baseline"]] == ["KOSPI"] * 3 + ["KOSDAQ"] * 3


def test_inverted_cdf_boundaries_lower_ties_and_shortfalls():
    assert boundaries([1, 1, 1, 2, 3, 4], 3) == [1, 2]
    assert lower_bin(1, [1, 2]) == 0
    assert lower_bin(2, [1, 2]) == 1
    features = {"005930": {"market": "KOSPI", "market_cap": 1, "prior20": 0}}
    result = draw("2026-11-03", [{"stock_code": "005930", "rcept_no": "20261102000001"}], features, {"005930"})
    assert result["matched"][0]["shortfall"] == 2
    assert sum(g["shortfall"] for g in result["baseline"]) == 6
    assert all(not g["selected"] for g in result["baseline"])


def test_bb003_reader_uses_timely_preparations_and_prefilter_decisions_read_only(tmp_path, fake_kernel):
    clock = Clock("2026-11-03T08:24:00+09:00")
    source, store = tmp_path / "bb003", Store(tmp_path / "own", clock=clock)
    event = {"entry_date": "2026-11-03", "rcept_no": "20261102000001", "stock_code": "005930"}
    bb003_record(source, "preparations", event["rcept_no"] + ".000000", {"status": "prepared", "event": event}, clock,
                  deadline="2026-11-03T08:59:00+09:00")
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    reader = BB003(source, store)
    assert len(reader.candidates("2026-11-03", aware("2026-11-03T08:25:00+09:00"))) == 1
    clock.set("2026-11-03T08:58:00+09:00")
    bb003_record(source, "decisions", event["rcept_no"], {"status": "included", "event": event,
                  "event_features": {"pre_close": 10000}}, clock, deadline="2026-11-03T08:59:00+09:00")
    assert reader.candidates("2026-11-03", aware("2026-11-03T08:23:00+09:00")) == []
    assert len(reader.decisions("2026-11-03", aware("2026-11-03T08:59:00+09:00"))) == 1
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_roster_restart_preserves_original_and_late_is_labelled(tmp_path, fake_kernel):
    clock = Clock("2026-11-03T08:24:58+09:00")
    store = Store(tmp_path / "own", clock=clock)
    bb003 = BB003(tmp_path / "bb003", store)
    features, _ = eligibility(history_rows())
    original = make_roster(store, bb003, tmp_path / "prices", None, plan(), population=(features, {}, {}, set()))
    clock.sleep(100)
    restarted = make_roster(store, bb003, tmp_path / "prices", None, plan(), population=({}, {}, {}, set()))
    assert original == restarted
    assert original["label"] == "forward"
    other = Store(tmp_path / "late", clock=clock)
    late = make_roster(other, BB003(tmp_path / "bb003", other), tmp_path / "prices", None, plan(), population=({}, {}, {}, set()))
    assert late["label"] == "reconstructed"


def test_population_cannot_read_late_file_into_0825_draw(tmp_path):
    path = tmp_path / "daily.jsonl"
    path.write_text("data")
    cutoff = aware("2026-11-03T08:25:00+09:00")
    os.utime(path, (cutoff.timestamp() + 1, cutoff.timestamp() + 1))
    with pytest.raises(Refused, match="not_available"):
        available_bytes(path, cutoff)


def test_special_time_data_cannot_redefine_snapshot_offsets(tmp_path, fake_kernel):
    store = Store(tmp_path / "own", clock=Clock())
    data = {"date": "2026-11-19", "session": True,
            "times": {k: (aware(v) + timedelta(days=16, hours=1)).isoformat() for k, v in plan()["times"].items()},
            "source_url": "https://kind.krx.co.kr/notice", "published_at": "2026-11-01T10:00:00+09:00"}
    data["times"]["s0859"] = "2026-11-19T09:59:01+09:00"
    with pytest.raises(Refused, match="fixed observation/order offsets"):
        Calendar(store).add_official(data, b"official notice")
