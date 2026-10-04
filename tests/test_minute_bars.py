import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
import requests

from scripts.data import minute_bars as cli
from src.ingestion import minute_bars as minute
from src.ingestion.storage import read_rows

NOW = datetime(2026, 10, 4, 18, tzinfo=minute.KST)
DAY = "2026-09-30"
PAIR = ("005930", DAY)


class Response:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code = payload, status_code

    def json(self):
        return deepcopy(self.payload)


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.setattr(minute, "_now", lambda: NOW)
    for key in ("HQA_INTERNAL_TOKEN", "BACKEND_INTERNAL_BASE_URL", "BACKEND_SIGNAL_URL", "HQA_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("No environment files or real HTTP sessions may be opened")

    monkeypatch.setattr(cli, "load_project_env", forbidden)
    monkeypatch.setattr(minute.requests, "Session", forbidden)


def candle(hhmm, day=DAY, **changes):
    return {"time": int(datetime.fromisoformat(f"{day}T{hhmm}:00+09:00").timestamp()),
            "open": 100, "high": 110, "low": 90, "close": 105, "volume": 20, **changes}


def response(*bars, code="005930", day=DAY, **changes):
    return Response({"stockCode": code, "date": day, "status": "OK",
                     "candles": list(bars) if bars else [candle("09:00", day), candle("15:20", day)],
                     "source": "kis", "pages": 4, **changes})


def client(tmp_path, *responses):
    return minute.MinuteBarClient("http://backend:8000/", "test-internal", Session(*responses), data_dir=tmp_path)


@pytest.mark.parametrize("stock_code", ["005930", "0015G0", "00088K", "ABCDEF"])
def test_parses_sorts_and_posts_one_backend_request_without_kis_credentials(tmp_path, stock_code):
    collector = client(tmp_path, response(candle("15:30"), candle("09:00"), candle("12:00"), code=stock_code))
    rows = collector.collect(stock_code, DAY, "user-1")
    assert len(collector.session.calls) == 1
    url, args = collector.session.calls[0]
    assert url == "http://backend:8000/api/v1/internal/market/minute-candles"
    assert args == {"json": {"userId": "user-1", "stockCode": stock_code, "date": DAY},
                    "headers": {"X-HQA-Internal-Token": "test-internal", "Accept": "application/json"},
                    "timeout": 30, "allow_redirects": False}
    assert [row["time"][11:16] for row in rows] == ["09:00", "12:00", "15:30"]
    assert all(row["collected_at"] == row["available_at"] == NOW.isoformat() for row in rows)
    assert all(row["complete"] is True for row in rows)
    assert all(row["stock_code"] == stock_code for row in rows)
    row = rows[0]
    assert row["time"] == DAY + "T09:00:00+09:00"
    assert row["source"] == "kis_minute" and row["trade_date"] == DAY
    assert [row[field] for field in minute.PRICE_FIELDS] == [100, 110, 90, 105, 20]
    content = {key: value for key, value in row.items() if key not in {"version", "collected_at", "available_at"}}
    assert row["version"] == hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert "test-internal" not in json.dumps(rows)


@pytest.mark.parametrize("start,end", [("09:01", "15:30"), ("09:00", "15:19"), ("10:00", "14:00")])
def test_incomplete_sessions_are_saved_flagged_and_listed(tmp_path, start, end):
    collector = client(tmp_path, response(candle(start), candle(end)))
    summary = collector.backfill([PAIR], "user-1", execute=True)
    assert summary["incomplete"] == [{"stock_code": PAIR[0], "date": DAY}]
    rows = read_rows(tmp_path / "market/minute/20260930/005930.jsonl")
    assert len(rows) == 2 and all(row["complete"] is False for row in rows)
    assert summary["failures"] == []


def test_rerun_skips_complete_session_without_credentials_or_requests(tmp_path):
    collector = client(tmp_path, response())
    first = collector.backfill([PAIR, PAIR], "user-1", execute=True)
    assert first["fetched"] == 1 and first["saved_rows"] == 2
    path = tmp_path / "market/minute/20260930/005930.jsonl"
    original = path.read_bytes()
    second = minute.MinuteBarClient(data_dir=tmp_path).backfill([PAIR], "user-1", execute=True)
    assert second["skipped_existing"] == [{"stock_code": PAIR[0], "date": DAY}]
    assert second["fetched"] == 0 and second["saved_rows"] == 0 and second["failures"] == []
    assert path.read_bytes() == original


def test_incomplete_session_is_replaced_by_complete_retry(tmp_path):
    collector = client(tmp_path, response(candle("10:00"), candle("15:20")), response())
    collector.backfill([PAIR], "user-1", execute=True)
    summary = collector.backfill([PAIR], "user-1", execute=True)
    assert summary["fetched"] == 1 and summary["incomplete"] == []
    rows = read_rows(tmp_path / "market/minute/20260930/005930.jsonl")
    assert len(rows) == 2 and all(row["complete"] for row in rows)


def test_dry_run_prints_plan_with_no_session_requests_or_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    summary = minute.backfill([PAIR], "user-1")
    assert summary["planned"] == [{"stock_code": PAIR[0], "date": DAY}]
    assert summary["fetched"] == 0 and not summary["execute"]
    assert json.loads(capsys.readouterr().out) == summary
    assert list(tmp_path.iterdir()) == []


def test_throttles_attempts_including_failures_and_continues(tmp_path):
    collector = client(tmp_path, requests.ConnectionError("offline fixture"), response(code="000660"), response(code="035720"))
    sleeps = []
    summary = collector.backfill([PAIR, ("000660", DAY), ("035720", DAY)], "user-1",
                                 execute=True, requests_per_second=2, sleeper=sleeps.append)
    assert sleeps == [0.5, 0.5]
    assert summary["fetched"] == 2 and summary["saved_rows"] == 4
    assert summary["failures"] == [{"stock_code": PAIR[0], "date": DAY, "error": "ConnectionError: offline fixture"}]
    assert not (tmp_path / "market/minute/20260930/005930.jsonl").exists()


@pytest.mark.parametrize("status", ["USER_NOT_FOUND", "KIS_SECRET_MISSING", "PAPER_ACCOUNT_REQUIRED",
                                   "KIS_TOKEN_UNAVAILABLE", "MINUTE_CANDLES_UNAVAILABLE", "PAGE_LIMIT_REACHED", None])
def test_non_ok_status_never_saves_even_with_candles(tmp_path, status):
    collector = client(tmp_path, response(status=status))
    summary = collector.backfill([PAIR], "user-1", execute=True)
    assert len(summary["failures"]) == 1 and summary["saved_rows"] == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("http_status", [302, 401, 500])
def test_http_failures_are_explicit(tmp_path, http_status):
    with pytest.raises(ValueError, match="HTTP status"):
        client(tmp_path, Response({}, http_status)).collect(*PAIR, "user-1")


@pytest.mark.parametrize("changes", [{"stockCode": "000660"}, {"date": "2026-09-29"}, {"source": "demo"}, {"candles": []}])
def test_response_identity_and_empty_data_are_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        client(tmp_path, response(**changes)).collect(*PAIR, "user-1")


@pytest.mark.parametrize("bar", [candle("09:00", volume=-1), candle("09:00", open=float("nan")),
                                 candle("09:00", close=0), candle("09:00", high=99),
                                 candle("09:00", volume=1.5), candle("09:00", open=True),
                                 candle("08:59"), candle("15:31"), candle("09:00", day="2026-09-29"),
                                 candle("09:00", time="090000")])
def test_bad_candles_fail_instead_of_becoming_defaults(tmp_path, bar):
    with pytest.raises(ValueError):
        client(tmp_path, response(bar)).collect(*PAIR, "user-1")


def test_duplicate_minutes_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicated"):
        client(tmp_path, response(candle("09:00"), candle("09:00"))).collect(*PAIR, "user-1")


@pytest.mark.parametrize("code,day", [("../bad", DAY), ("00593", DAY), ("005930", "2026-10-05"),
                                     ("005930", "2025-10-02"), ("005930", "20260930"),
                                     (None, DAY), ("", DAY), ("0015g0", DAY), ("015G0", DAY), ("15G0", DAY),
                                     ("0015G0X", DAY), ("0015-0", DAY), ("００５９３０", DAY),
                                     ("0015Ｇ0", DAY)])
def test_invalid_requests_make_no_http_call(tmp_path, code, day):
    collector = client(tmp_path)
    with pytest.raises(ValueError):
        collector.collect(code, day, "user-1")
    assert collector.session.calls == []


def test_missing_token_fails_without_constructing_session(tmp_path):
    with pytest.raises(ValueError, match="HQA_INTERNAL_TOKEN"):
        minute.MinuteBarClient(data_dir=tmp_path).collect(*PAIR, "user-1")


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_invalid_rate_is_rejected(tmp_path, rate):
    with pytest.raises(ValueError, match="requests_per_second"):
        client(tmp_path).backfill([PAIR], "user-1", requests_per_second=rate)


def test_plan_uses_actual_sessions_and_deduplicates_windows():
    sessions = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-30", "2026-10-02"]
    events = [{"stock_code": "005930", "date": "2026-09-25"},
              {"stock_code": "005930", "date": "2026-09-26"}]
    assert minute.plan_event_window(events, sessions) == [
        ("005930", day) for day in sessions[:4]]
    assert minute.plan_event_window(events, sessions, 0, 0) == [("005930", "2026-09-25"), ("005930", "2026-09-28")]


def test_plan_filters_old_and_future_sessions_using_kst_today():
    cutoff = NOW.date() - timedelta(days=365)
    sessions = [(cutoff + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)]
    sessions += ["2026-10-02", "2026-10-05"]
    events = [{"stock_code": "005930", "date": cutoff.isoformat()}, {"stock_code": "000660", "date": "2026-10-02"}]
    assert minute.plan_event_window(events, sessions, 1, 1) == [
        ("000660", sessions[2]), ("000660", "2026-10-02"), ("005930", sessions[1]), ("005930", sessions[2])]


def test_plan_requires_session_coverage():
    with pytest.raises(ValueError, match="do not cover"):
        minute.plan_event_window([{"stock_code": "005930", "date": DAY}], [])


def test_cli_dry_run_preserves_leading_zeroes_without_env_loading(tmp_path, monkeypatch, capsys):
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("stock_code,date\n005930,2026-09-30\n")
    monkeypatch.setattr("sys.argv", ["minute_bars", "--pairs-file", str(pairs), "--user-id", "user-1", "--data-dir", str(tmp_path)])
    cli.main()
    assert json.loads(capsys.readouterr().out)["planned"] == [{"stock_code": "005930", "date": DAY}]


def test_cli_events_uses_supplied_sessions(tmp_path, monkeypatch, capsys):
    events, sessions = tmp_path / "events.csv", tmp_path / "sessions.csv"
    events.write_text("stock_code,date\n005930,2026-09-30\n")
    sessions.write_text("date\n2026-09-28\n2026-09-30\n2026-10-02\n")
    monkeypatch.setattr("sys.argv", ["minute_bars", "--events-file", str(events), "--sessions-file", str(sessions),
                                     "--user-id", "user-1", "--data-dir", str(tmp_path)])
    cli.main()
    assert len(json.loads(capsys.readouterr().out)["planned"]) == 3


def test_cli_execute_loads_environment_then_uses_fake_backend(tmp_path, monkeypatch, capsys):
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("stock_code,date\n005930,2026-09-30\n")
    loaded = []
    collector = client(tmp_path, response())
    monkeypatch.setattr(cli, "load_project_env", lambda: loaded.append(True))

    def make_client(**kwargs):
        assert loaded == [True]
        return collector

    monkeypatch.setattr(cli, "MinuteBarClient", make_client)
    monkeypatch.setattr("sys.argv", ["minute_bars", "--pairs-file", str(pairs), "--user-id", "user-1", "--execute"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["fetched"] == 1


def test_url_environment_conventions(tmp_path, monkeypatch):
    assert minute.MinuteBarClient(data_dir=tmp_path).base_url == "http://localhost:8000"
    monkeypatch.setenv("BACKEND_SIGNAL_URL", "http://signal/api/v1/internal/trading/signals")
    assert minute.MinuteBarClient(data_dir=tmp_path).base_url == "http://signal"
    monkeypatch.setenv("BACKEND_INTERNAL_BASE_URL", "http://internal/")
    assert minute.MinuteBarClient(data_dir=tmp_path).base_url == "http://internal"
