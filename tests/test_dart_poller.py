import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock

import pytest

from scripts.data import dart_poller as cli
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_poller import DartListingPoller, KST, LIST_URL
from src.ingestion.storage import read_rows


KEY = "fixture-private-key"
DAY = "20260904"
START = datetime(2026, 9, 4, 10, tzinfo=KST)


@pytest.fixture(autouse=True)
def no_env_or_real_session(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Tests must not load env files or create real sessions")

    monkeypatch.setattr("src.config.settings.load_project_env", denied)
    monkeypatch.setattr(cli, "load_project_env", denied)
    monkeypatch.setattr("requests.Session", denied)


def row(number, day=DAY, **changes):
    return {"rcept_no": f"{day}{number:06d}", "rcept_dt": day, "report_nm": "Other filing",
            "corp_code": "00126380", "corp_name": "Example", "stock_code": "005930",
            "corp_cls": "Y", "flr_nm": "Example", "rm": "", **changes}


def page(number, rows, total=None, **changes):
    total = len(rows) if total is None else total
    return {"status": "000", "page_no": number, "page_count": 100, "total_count": total,
            "total_page": (total + 99) // 100, "list": rows, **changes}


class FakeSession:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.payloads:
            pytest.fail("Unexpected extra page requested")
        value = self.payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        return Mock(json=Mock(return_value=value), raise_for_status=Mock())


class FakeClock:
    def __init__(self, now=START):
        self.now = now
        self.calls = []
        self.sleeps = []

    def __call__(self):
        now = self.now
        self.calls.append(now)
        self.now += timedelta(seconds=1)
        return now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def poller_with(tmp_path, *payloads, clock=None, **kwargs):
    clock = clock if clock is not None else FakeClock()
    return DartListingPoller(KEY, session=FakeSession(*payloads), clock=clock,
        sleeper=clock.sleep, data_dir=tmp_path, **kwargs)


def archive(tmp_path, day=DAY):
    return tmp_path / "disclosures" / "first_seen" / f"{day}.jsonl"


def state_path(tmp_path):
    return archive(tmp_path).parent / "_state.json"


def test_first_poll_stores_all_pages_and_kst_timestamps(tmp_path):
    clock = FakeClock(START.astimezone(timezone.utc))
    rows = [row(n) for n in range(100)] + [row(100, stock_code="", corp_cls="E")]
    poller = poller_with(tmp_path, page(1, rows[:100], 101), page(2, rows[100:], 101), clock=clock)
    summary = poller.poll_once()
    stored = read_rows(archive(tmp_path))
    assert summary["new"] == len(stored) == 101
    assert summary["pages"] == 2
    assert summary["catch_up_dates"] == []
    assert json.loads(state_path(tmp_path).read_text()) == {"last_completed_date": DAY}
    assert len(clock.calls) == 2
    assert not stored[-1]["listed"] and stored[-1]["stock_code"] == ""
    for item in stored:
        assert item["source"] == "dart_list_poll"
        assert item["catch_up"] is False
        assert item["revision"] == 1 and len(item["content_hash"]) == 64
        assert item["first_seen_at"] == item["poll_started_at"] == START.isoformat()
        assert item["available_at"] == item["poll_completed_at"]
        for field in ("first_seen_at", "observed_at", "poll_started_at", "poll_completed_at", "available_at"):
            assert datetime.fromisoformat(item[field]).utcoffset() == timedelta(hours=9)
    for number, (url, kwargs) in enumerate(poller.session.calls, 1):
        assert url == LIST_URL
        assert kwargs["params"] == {"crtfc_key": KEY, "bgn_de": DAY, "end_de": DAY,
            "sort": "date", "sort_mth": "desc", "page_count": 100, "page_no": number,
            "last_reprt_at": "N"}


def test_second_poll_and_restart_preserve_original_bytes_and_add_only_new(tmp_path):
    poller_with(tmp_path, page(1, [row(1)])).poll_once()
    original = archive(tmp_path).read_bytes()
    poller = poller_with(tmp_path, page(1, [row(2), row(1)]),
                         clock=FakeClock(START + timedelta(minutes=1)))
    assert poller.poll_once()["new"] == 1
    assert archive(tmp_path).read_bytes().startswith(original)
    stored = read_rows(archive(tmp_path))
    assert [item["first_seen_at"] for item in stored] == [START.isoformat(),
        (START + timedelta(minutes=1)).isoformat()]
    previous = archive(tmp_path).read_bytes()
    assert poller_with(tmp_path, page(1, [row(2), row(1)])).poll_once()["new"] == 0
    assert archive(tmp_path).read_bytes() == previous


def test_all_known_page_stops_early_but_still_records_revision(tmp_path):
    rows = [row(n) for n in range(100)]
    poller_with(tmp_path, page(1, rows)).poll_once()
    rows[0] = row(0, rm="정")
    poller = poller_with(tmp_path, page(1, rows, 201))
    summary = poller.poll_once()
    assert summary["early_stop"] and summary["revisions"] == 1
    assert len(poller.session.calls) == 1


def test_mixed_page_continues_even_if_every_later_page_receipt_was_seen_in_poll(tmp_path):
    poller_with(tmp_path, page(1, [row(1)])).poll_once()
    poller = poller_with(tmp_path, page(1, [row(n) for n in range(100)], 201),
        page(2, [row(n) for n in range(100)], 201), page(3, [row(200)], 201))
    summary = poller.poll_once()
    assert summary["pages"] == 3 and summary["new"] == 100
    assert len(read_rows(archive(tmp_path))) == 101


def test_no_data_does_not_publish_or_change_archive(tmp_path):
    poller = poller_with(tmp_path, {"status": "013"}, page(1, [row(1)]), {"status": "013"})
    assert poller.poll_once()["new"] == 0
    assert not archive(tmp_path).exists()
    poller.poll_once()
    original = archive(tmp_path).read_bytes()
    assert poller.poll_once()["seen"] == 0
    assert archive(tmp_path).read_bytes() == original


@pytest.mark.parametrize("second", [
    RuntimeError(f"https://provider.invalid?crtfc_key={KEY}"),
    {"status": "013"}, page(2, [], 101), page(1, [row(100)], 101),
    page(2, [row(100), row(101)], 102), page(2, [row(100, report_nm="")], 101),
    page(2, [row(1, report_nm="Changed within poll")], 101),
])
@pytest.mark.parametrize("existing", [False, True])
def test_midway_failure_publishes_nothing(tmp_path, second, existing):
    if existing:
        poller_with(tmp_path, page(1, [row(1)])).poll_once()
    original = archive(tmp_path).read_bytes() if existing else None
    original_state = state_path(tmp_path).read_bytes() if existing else None
    poller = poller_with(tmp_path, page(1, [row(n) for n in range(100)], 101), second)
    with pytest.raises(DartAPIError):
        poller.poll_once()
    if existing:
        assert archive(tmp_path).read_bytes() == original
        assert state_path(tmp_path).read_bytes() == original_state
    else:
        assert not archive(tmp_path).exists()
        assert not state_path(tmp_path).exists()


def test_next_morning_catches_evening_filing_after_window_without_early_stop(tmp_path):
    evening = START.replace(day=3, hour=19, minute=30)
    previous_day = evening.strftime("%Y%m%d")
    known = [row(n, day=previous_day) for n in range(100)]
    poller_with(tmp_path, page(1, known), clock=FakeClock(evening)).poll_once()
    original = archive(tmp_path, previous_day).read_bytes()
    poller = poller_with(tmp_path, page(1, known, 101),
        page(2, [row(100, day=previous_day)], 101), {"status": "013"})
    assert not poller.should_poll(evening + timedelta(hours=1))
    summary = poller.poll_once()
    assert summary["catch_up_dates"] == [previous_day]
    assert summary["pages"] == 3 and summary["new"] == 1
    assert archive(tmp_path, previous_day).read_bytes().startswith(original)
    late = read_rows(archive(tmp_path, previous_day))[-1]
    assert late["catch_up"] is True
    assert late["first_seen_at"] == late["observed_at"] == START.isoformat()
    assert late["available_at"] == summary["poll_completed_at"]
    assert json.loads(state_path(tmp_path).read_text()) == {
        "last_completed_date": DAY, "last_closed_date": previous_day}
    # Restart on the same day must not repeat the closed sweep.
    repeat = poller_with(tmp_path, {"status": "013"})
    assert repeat.poll_once()["catch_up_dates"] == []
    assert len(repeat.session.calls) == 1


@pytest.mark.parametrize("gap", [3, 4])
def test_weekend_and_additional_closed_day_are_swept(tmp_path, gap):
    poller_with(tmp_path, {"status": "013"}).poll_once()
    dates = [(START + timedelta(days=n)).strftime("%Y%m%d") for n in range(gap)]
    resumed = START + timedelta(days=gap)
    poller = poller_with(tmp_path, *(page(1, [row(1, day=day)]) for day in dates),
        {"status": "013"}, clock=FakeClock(resumed))
    summary = poller.poll_once()
    assert summary["catch_up_dates"] == dates
    assert summary["new"] == gap
    assert [call[1]["params"]["bgn_de"] for call in poller.session.calls] == [
        *dates, resumed.strftime("%Y%m%d")]
    for day in dates:
        stored = read_rows(archive(tmp_path, day))
        assert len(stored) == 1 and stored[0]["catch_up"] is True
        assert stored[0]["first_seen_at"] == resumed.isoformat()


@pytest.mark.parametrize("failure_at", ["catch_up_page", "catch_up_date", "today"])
def test_catch_up_failure_preserves_all_archives_and_state(tmp_path, failure_at):
    poller_with(tmp_path, page(1, [row(1)])).poll_once()
    original = archive(tmp_path).read_bytes()
    original_state = state_path(tmp_path).read_bytes()
    if failure_at == "catch_up_page":
        payloads = [page(1, [row(n) for n in range(100)], 101)]
    else:
        payloads = [page(1, [row(1, rm="정"), row(2)])]
        if failure_at == "today":
            payloads += [page(1, [row(3, day="20260905")]), {"status": "013"}]
    poller = poller_with(tmp_path, *payloads, RuntimeError("request failed"),
        clock=FakeClock(START + timedelta(days=3)))
    with pytest.raises(DartAPIError):
        poller.poll_once()
    assert archive(tmp_path).read_bytes() == original
    assert state_path(tmp_path).read_bytes() == original_state
    assert sorted(path.name for path in archive(tmp_path).parent.iterdir()) == [
        ".poll.lock", f"{DAY}.jsonl", "_state.json"]


def test_large_gap_sweeps_oldest_ten_dates_per_poll_and_resumes_after_restart(tmp_path):
    poller_with(tmp_path, {"status": "013"}).poll_once()
    resumed = START + timedelta(days=23)
    for first, stop in [(0, 10), (10, 20), (20, 23), (23, 23)]:
        dates = [(START + timedelta(days=n)).strftime("%Y%m%d") for n in range(first, stop)]
        poller = poller_with(tmp_path, *([{"status": "013"}] * (len(dates) + 1)),
            clock=FakeClock(resumed))
        summary = poller.poll_once()
        assert summary["catch_up_dates"] == dates
        assert summary["pages"] == len(dates) + 1
        assert [call[1]["params"]["bgn_de"] for call in poller.session.calls] == [
            *dates, resumed.strftime("%Y%m%d")]
        assert json.loads(state_path(tmp_path).read_text()) == {
            "last_completed_date": resumed.strftime("%Y%m%d"),
            "last_closed_date": (START + timedelta(days=stop - 1)).strftime("%Y%m%d")}
    # An ordinary next-day poll must close yesterday after the backlog is gone.
    next_day = poller_with(tmp_path, {"status": "013"}, {"status": "013"},
        clock=FakeClock(resumed + timedelta(days=1)))
    assert next_day.poll_once()["catch_up_dates"] == [resumed.strftime("%Y%m%d")]


def test_catch_up_revision_keeps_original_first_seen(tmp_path):
    poller_with(tmp_path, page(1, [row(1)])).poll_once()
    poller = poller_with(tmp_path, page(1, [row(1, rm="정")]), {"status": "013"},
        clock=FakeClock(START + timedelta(days=1)))
    assert poller.poll_once()["revisions"] == 1
    original, revision = read_rows(archive(tmp_path))
    assert revision["first_seen_at"] == original["first_seen_at"] == START.isoformat()
    assert revision["catch_up"] is True and revision["revision"] == 2


@pytest.mark.parametrize("bad", [
    None, [], {"rcept_no": "invalid"}, row(1, rcept_no="invalid"), row(1, report_nm=" "),
    row(1, rcept_dt="20260230"), row(1, rcept_dt="20260903"), row(1, corp_code=""),
    row(1, stock_code=None), row(1, stock_code="123"), row(1, corp_cls="?"), row(1, rm=None),
])
def test_malformed_row_rejected(tmp_path, bad):
    with pytest.raises(DartAPIError, match="malformed"):
        poller_with(tmp_path, page(1, [bad])).poll_once()
    assert not archive(tmp_path).exists()


@pytest.mark.parametrize("changes", [
    {"page_no": True}, {"page_no": "bad"}, {"page_count": 10}, {"total_count": 0},
    {"total_page": 2}, {"list": {}}, {"list": []},
])
def test_pagination_contract(tmp_path, changes):
    with pytest.raises(DartAPIError):
        poller_with(tmp_path, page(1, [row(1)], **changes)).poll_once()


def test_revisions_preserve_first_seen_and_return_to_previous_content(tmp_path):
    poller = poller_with(tmp_path, *(page(1, [row(1, rm=remark)]) for remark in ("", "정", "정", "")))
    assert [poller.poll_once()["revisions"] for _ in range(4)] == [0, 1, 0, 1]
    stored = read_rows(archive(tmp_path))
    assert [item["revision"] for item in stored] == [1, 2, 3]
    assert {item["first_seen_at"] for item in stored} == {START.isoformat()}
    assert stored[1]["observed_at"] == (START + timedelta(seconds=2)).isoformat()
    assert stored[0]["content_hash"] == stored[2]["content_hash"] != stored[1]["content_hash"]


@pytest.mark.parametrize("payload", [
    RuntimeError(f"https://provider.invalid?crtfc_key={KEY}"),
    {"status": "020", "message": KEY}, {"status": KEY}, [], {},
    {"status": "013", "list": [row(1)]}, page(1, [row(1, rcept_no=KEY)]),
])
def test_errors_redact_api_key_including_traceback(tmp_path, payload):
    with pytest.raises(DartAPIError) as error:
        poller_with(tmp_path, payload).poll_once()
    rendered = "".join(traceback.format_exception(error.type, error.value, error.tb))
    assert KEY not in rendered and "https://provider.invalid" not in rendered


@pytest.mark.parametrize("kind", ["http", "json"])
def test_response_failures_redact_api_key(tmp_path, kind):
    response = Mock()
    if kind == "http":
        response.raise_for_status.side_effect = RuntimeError(KEY)
    else:
        response.json.side_effect = ValueError(KEY)
    poller = poller_with(tmp_path)
    poller.session = Mock(get=Mock(return_value=response))
    with pytest.raises(DartAPIError) as error:
        poller.poll_once()
    assert KEY not in "".join(traceback.format_exception(error.type, error.value, error.tb))


def test_provider_echoed_credentials_are_not_stored(tmp_path):
    poller_with(tmp_path, page(1, [row(1, report_nm=f"Filing {KEY}", crtfc_key=KEY)])).poll_once()
    assert KEY not in archive(tmp_path).read_text()


@pytest.mark.parametrize("now, expected", [
    ("2026-09-04T06:59:59+09:00", False), ("2026-09-04T07:00:00+09:00", True),
    ("2026-09-04T19:30:00+09:00", True), ("2026-09-04T19:30:01+09:00", False),
    ("2026-09-05T10:00:00+09:00", False), ("2026-09-06T10:00:00+09:00", False),
    ("2026-09-03T22:00:00+00:00", True), ("2026-09-04T22:00:00+00:00", False),
])
def test_poll_window_and_weekends(tmp_path, now, expected):
    assert poller_with(tmp_path).should_poll(datetime.fromisoformat(now)) is expected


def test_configurable_window_and_naive_clock_rejection(tmp_path):
    poller = poller_with(tmp_path, window_start=time(9), window_end=time(10))
    assert not poller.should_poll(START.replace(hour=8))
    assert poller.should_poll(START)
    with pytest.raises(ValueError, match="timezone-aware"):
        poller.should_poll(START.replace(tzinfo=None))
    poller.clock = lambda: START.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        poller.poll_once()
    assert not poller.session.calls


def test_backward_clock_fails_without_publication(tmp_path):
    poller = poller_with(tmp_path, page(1, [row(1)]))
    poller.clock = Mock(side_effect=[START, START - timedelta(seconds=1)])
    with pytest.raises(ValueError, match="backwards"):
        poller.poll_once()
    assert not archive(tmp_path).exists()


def test_midnight_uses_poll_start_kst_date(tmp_path):
    clock = FakeClock(START.replace(hour=23, minute=59, second=59))
    poller_with(tmp_path, page(1, [row(1)]), clock=clock).poll_once()
    stored = read_rows(archive(tmp_path))[0]
    assert stored["first_seen_at"].startswith("2026-09-04T23:59:59")
    assert stored["poll_completed_at"].startswith("2026-09-05T00:00:00")


def test_loop_backoff_cap_recovery_and_json_logs(tmp_path, capsys):
    poller = poller_with(tmp_path, *([RuntimeError(KEY)] * 6), {"status": "013"}, {"status": "013"})
    poller.run_loop(max_polls=8)
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["consecutive_failures"] for item in summaries] == [1, 2, 3, 4, 5, 6, 0, 0]
    assert poller.clock.sleeps == [60, 120, 240, 480, 600, 600, 60]
    assert KEY not in json.dumps(summaries)


def test_loop_waits_outside_window_without_request(tmp_path):
    clock = FakeClock(START.replace(hour=6, minute=59, second=59))
    poller = poller_with(tmp_path, {"status": "013"}, clock=clock)
    poller.run_loop(max_polls=1)
    assert clock.sleeps == [60] and len(poller.session.calls) == 1


def test_subsecond_interval_backoff_reaches_ten_minute_cap(tmp_path, capsys):
    poller = poller_with(tmp_path, *([RuntimeError(KEY)] * 15))
    poller.run_loop(interval_seconds=0.25, max_polls=15)
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["next_poll_seconds"] for item in summaries] == [min(600, 0.25 * 2 ** n) for n in range(15)]


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan"), True])
def test_invalid_interval_fails_before_polling(tmp_path, interval):
    poller = poller_with(tmp_path)
    with pytest.raises(ValueError, match="interval"):
        poller.run_loop(interval_seconds=interval, max_polls=1)
    assert not poller.session.calls


@pytest.mark.parametrize("mode", [[], ["--once"], ["--loop", "--interval", "30"]])
def test_cli_dry_run_creates_no_session_reads_no_env_writes_no_file(tmp_path, monkeypatch, capsys, mode):
    target = tmp_path / "not-created"
    monkeypatch.setattr("sys.argv", ["dart_poller", *mode, "--data-dir", str(target)])
    cli.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["dry_run"] and planned["params"]["crtfc_key"] == "[REDACTED]"
    assert "corp_code" not in planned["params"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("loop", [False, True])
def test_cli_execute_dispatch_uses_mocked_env_and_poller(tmp_path, monkeypatch, capsys, loop):
    load_env = Mock()
    poller = Mock(poll_once=Mock(return_value={"status": "ok"}))
    constructor = Mock(return_value=poller)
    monkeypatch.setattr(cli, "load_project_env", load_env)
    monkeypatch.setattr(cli, "DartListingPoller", constructor)
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setattr("sys.argv", ["dart_poller", "--execute", "--loop" if loop else "--once",
                                    "--interval", "30", "--data-dir", str(tmp_path)])
    cli.main()
    load_env.assert_called_once_with()
    constructor.assert_called_once_with(KEY, data_dir=tmp_path)
    if loop:
        poller.run_loop.assert_called_once_with(interval_seconds=30)
        poller.poll_once.assert_not_called()
    else:
        poller.poll_once.assert_called_once_with()
        assert json.loads(capsys.readouterr().out) == {"status": "ok"}


@pytest.mark.parametrize("configured", [None, "custom/data", "absolute", " "])
def test_cli_default_directory_matches_execute_using_settings(tmp_path, monkeypatch, capsys, configured):
    from src.config import settings

    if configured == "absolute":
        configured = str(tmp_path / "absolute-data")
    if configured is None:
        monkeypatch.delenv("HQA_DATA_DIR", raising=False)
        expected = tmp_path / "data"
    else:
        monkeypatch.setenv("HQA_DATA_DIR", configured)
        expected = tmp_path / configured.strip()
    monkeypatch.setattr(settings, "get_project_root", lambda: tmp_path)
    # get_data_dir requires the environment loader; replace it without reading secrets.
    load_env = Mock()
    monkeypatch.setattr(settings, "load_project_env", load_env)
    monkeypatch.setattr(cli, "load_project_env", load_env)
    monkeypatch.setenv("DART_API_KEY", KEY)
    constructor = Mock(return_value=Mock(poll_once=Mock(return_value={"status": "ok"})))
    monkeypatch.setattr(cli, "DartListingPoller", constructor)
    settings.get_settings.cache_clear()
    try:
        monkeypatch.setattr("sys.argv", ["dart_poller"])
        cli.main()
        planned = json.loads(capsys.readouterr().out)
        assert planned["data_dir"] == str(expected)
        load_env.assert_called_once_with()
        constructor.assert_not_called()
        assert not list(tmp_path.iterdir())
        settings.get_settings.cache_clear()
        monkeypatch.setattr("sys.argv", ["dart_poller", "--execute"])
        cli.main()
        constructor.assert_called_once_with(KEY, data_dir=expected)
    finally:
        settings.get_settings.cache_clear()


def test_concurrent_polls_keep_one_original_and_all_new_receipts(tmp_path):
    barrier = Barrier(2)

    def run(number):
        poller = poller_with(tmp_path, page(1, [row(0), row(number)]))
        barrier.wait(timeout=5)
        return poller.poll_once()["new"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(run, [1, 2]))
    stored = read_rows(archive(tmp_path))
    assert sum(counts) == len(stored) == 3
    assert len({item["rcept_no"] for item in stored}) == 3


def test_atomic_publication_failure_preserves_existing_observations(tmp_path, monkeypatch):
    poller_with(tmp_path, page(1, [row(1)])).poll_once()
    original = archive(tmp_path).read_bytes()
    original_state = state_path(tmp_path).read_bytes()
    monkeypatch.setattr("src.ingestion.storage.os.replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        poller_with(tmp_path, page(1, [row(2), row(1, rm="정")])).poll_once()
    assert archive(tmp_path).read_bytes() == original
    assert state_path(tmp_path).read_bytes() == original_state
    assert sorted(path.name for path in archive(tmp_path).parent.iterdir()) == [
        ".poll.lock", f"{DAY}.jsonl", "_state.json"]
