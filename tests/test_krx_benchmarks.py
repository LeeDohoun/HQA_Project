import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import threading

import pytest
import requests

from src.ingestion import krx_benchmarks as benchmarks


NOW = datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, payload, error=None, status_code=200):
        self.payload, self.error = payload, error
        self.status_code = status_code
        self.json_calls = 0

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        self.json_calls += 1
        return deepcopy(self.payload)


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def clock_and_keys(monkeypatch):
    monkeypatch.setattr(benchmarks, "_now", lambda: NOW)
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    monkeypatch.delenv("KRX_API_KEY", raising=False)


def raw(name="KOSPI", close="2,500.25", day="20260904"):
    return {"BAS_DD": day, "IDX_CLSS": "KOSPI", "IDX_NM": name, "CLSPRC_IDX": close}


def collected(*rows, series=("KOSPI",)):
    session = Session(Response({"OutBlock_1": list(rows or [raw()])}))
    return benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "2026-09-04", series)


def observed(row, timestamp, close=None):
    result = deepcopy(row)
    result["available_at"] = timestamp
    if close is not None:
        result = benchmarks._record(result["series"], result["index_name"],
                                    benchmarks._date(result["trade_date"]), close,
                                    benchmarks._aware(timestamp))
    return result


def test_all_market_and_industry_rows_use_one_request_per_series_date():
    session = Session(Response({"OutBlock_1": [raw(), raw("Electrical equipment", "1500")]}),
                      Response({"OutBlock_1": [raw("KOSDAQ", "900")]}))
    rows = benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "20260904")
    assert len(rows) == 3
    assert {row["index_name"] for row in rows} == {"KOSPI", "Electrical equipment", "KOSDAQ"}
    assert [call[0] for call in session.calls] == list(benchmarks.ENDPOINTS.values())
    assert all(call[1] == {"params": {"basDd": "20260904"}, "headers": {"AUTH_KEY": "fixture-key"},
                           "timeout": 20, "allow_redirects": False} for call in session.calls)
    row = next(row for row in rows if row["index_name"] == "KOSPI")
    assert row["close"] == 2500.25
    assert row["bar_at"] == "2026-09-04T15:30:00+09:00"
    assert row["available_at"] == NOW.isoformat()
    assert row["price_basis"] == "price_index"
    assert row["schema_version"] == 1
    assert "fixture-key" not in json.dumps(rows)
    assert row["source_url"] == benchmarks.ENDPOINTS["KOSPI"] + "?basDd=20260904"


def test_empty_holiday_response_is_not_cached_across_collection_runs():
    session = Session(Response({"OutBlock_1": []}), Response({"OutBlock_1": [raw()]}))
    collector = benchmarks.KrxBenchmarkCollector("fixture-key", session)
    assert collector.collect_daily("20260904", "20260904", ("KOSPI",)) == []
    assert len(collector.collect_daily("20260904", "20260904", ("KOSPI",))) == 1
    assert len(session.calls) == 2


def test_weekend_dates_are_requested_once_and_empty_is_valid():
    session = Session(Response({"OutBlock_1": []}), Response({"OutBlock_1": []}))
    rows = benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260829", "20260830", ("KOSPI",))
    assert rows == []
    assert [call[1]["params"]["basDd"] for call in session.calls] == ["20260829", "20260830"]


def test_available_at_uses_response_completion_not_request_start(monkeypatch):
    times = iter([NOW, datetime(2026, 9, 5, 1, 1, tzinfo=timezone.utc)])
    monkeypatch.setattr(benchmarks, "_now", lambda: next(times))
    assert collected()[0]["available_at"] == "2026-09-05T01:01:00+00:00"


def test_index_names_are_preserved_exactly_and_order_does_not_change_versions():
    first = collected(raw("Index  name "), raw("Another"))
    second = collected(raw("Another"), raw("Index  name "))
    assert first == second
    assert first[1]["index_name"] == "Index  name "


def test_identical_duplicates_are_deduplicated_and_conflicts_fail():
    assert len(collected(raw(), raw())) == 1
    with pytest.raises(ValueError, match="conflicting duplicate"):
        collected(raw(), raw(close="2501"))


@pytest.mark.parametrize("payload", [None, [], {}, {"output": []}, {"OutBlock_1": None},
                                     {"OutBlock_1": {}}, {"OutBlock_1": [], "error": "denied"},
                                     {"OutBlock_1": [None]}, {"OutBlock_1": [{}]}])
def test_invalid_response_shapes_fail_without_fallback(payload):
    session = Session(Response(payload))
    with pytest.raises(ValueError):
        benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "20260904", ("KOSPI",))
    assert len(session.calls) == 1


@pytest.mark.parametrize("value", ["", "-", None, True, False, float("nan"), float("inf"),
                                   0, -1, "0", "1,2", "1e3", [], {}])
def test_missing_or_invalid_close_never_becomes_zero(value):
    with pytest.raises(ValueError):
        collected(raw(close=value))


@pytest.mark.parametrize("field,value", [("IDX_NM", ""), ("IDX_NM", None), ("BAS_DD", "20260903"),
                                        ("BAS_DD", 20260904), ("BAS_DD", "2026-09-04")])
def test_invalid_provider_identity_fails(field, value):
    row = raw()
    row[field] = value
    with pytest.raises(ValueError):
        collected(row)


@pytest.mark.parametrize("start,end", [("20260905", "20260905"), ("20260904", "20260906"),
                                     ("20260904", "20260903"), ("20100103", "20100104"),
                                     ("2026-9-4", "20260904"), (True, "20260904")])
def test_invalid_or_incomplete_range_fails_before_network(start, end):
    session = Session()
    with pytest.raises(ValueError):
        benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily(start, end)
    assert session.calls == []


def test_today_after_close_and_before_publication_hour_fail(monkeypatch):
    session = Session()
    collector = benchmarks.KrxBenchmarkCollector("fixture-key", session)
    monkeypatch.setattr(benchmarks, "_now", lambda: datetime(2026, 9, 4, 8, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="current and future"):
        collector.collect_daily("20260904", "20260904")
    monkeypatch.setattr(benchmarks, "_now", lambda: datetime(2026, 9, 4, 22, 59, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="08:00"):
        collector.collect_daily("20260904", "20260904")
    assert session.calls == []


@pytest.mark.parametrize("series", [(), ("KOSPI", "KOSPI"), ("KRX",), "KOSPI", (True,), ({},)])
def test_invalid_series_fails_before_network(series):
    session = Session()
    with pytest.raises(ValueError):
        benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "20260904", series)
    assert session.calls == []


def test_missing_key_fails_at_construction_and_env_aliases_work(monkeypatch):
    with pytest.raises(ValueError, match="KRX_OPEN_API_KEY"):
        benchmarks.KrxBenchmarkCollector(session=Session())
    monkeypatch.setenv("KRX_API_KEY", "alias-fixture")
    assert benchmarks.KrxBenchmarkCollector(session=Session()).api_key == "alias-fixture"
    monkeypatch.setenv("KRX_OPEN_API_KEY", "primary-fixture")
    assert benchmarks.KrxBenchmarkCollector(session=Session()).api_key == "primary-fixture"
    with pytest.raises(ValueError):
        benchmarks.KrxBenchmarkCollector("", Session())


@pytest.mark.parametrize("failure", [requests.Timeout("fixture timeout"), requests.HTTPError("fixture 403")])
def test_network_errors_propagate_without_retry(failure):
    response = Response(None, failure) if isinstance(failure, requests.HTTPError) else failure
    session = Session(response)
    with pytest.raises(type(failure)):
        benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "20260904", ("KOSPI",))
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 500])
def test_redirect_or_failed_http_response_never_parses_even_valid_json(status):
    response = Response({"OutBlock_1": [raw()]}, status_code=status)
    session = Session(response)
    with pytest.raises(requests.HTTPError, match=f"HTTP {status}"):
        benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "20260904", ("KOSPI",))
    assert response.json_calls == 0
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


def test_request_credentials_are_not_exported_in_errors_or_provider_identity(capsys):
    key = "private-benchmark-auth-key"
    session = Session(requests.Timeout("request authorization " + key))
    with pytest.raises(requests.Timeout) as caught:
        benchmarks.KrxBenchmarkCollector(key, session).collect_daily("20260904", "20260904", ("KOSPI",))
    assert key not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    session = Session(Response({"OutBlock_1": [raw("index " + key)]}))
    with pytest.raises(ValueError, match="credentials") as caught:
        benchmarks.KrxBenchmarkCollector(key, session).collect_daily("20260904", "20260904", ("KOSPI",))
    assert key not in str(caught.value)
    assert key not in capsys.readouterr().out


def test_invalid_json_error_does_not_export_response_text():
    class InvalidJsonResponse(Response):
        def json(self):
            raise ValueError("response echoed fixture-key")

    session = Session(InvalidJsonResponse(None))
    with pytest.raises(ValueError, match="not valid JSON") as caught:
        benchmarks.KrxBenchmarkCollector("fixture-key", session).collect_daily("20260904", "20260904", ("KOSPI",))
    assert "fixture-key" not in str(caught.value)


def test_observation_clock_before_close_is_rejected_not_fabricated(monkeypatch):
    times = iter([NOW, datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)])
    monkeypatch.setattr(benchmarks, "_now", lambda: next(times))
    with pytest.raises(ValueError, match="precedes the completed market close"):
        collected()


def test_overflowing_numeric_provider_close_is_an_explicit_validation_failure():
    with pytest.raises(ValueError, match="finite"):
        collected(raw(close=10 ** 1000))


def test_save_preserves_a_b_a_episodes_and_first_unchanged_availability(tmp_path):
    path = tmp_path / "context" / "benchmarks.jsonl"
    a = collected()[0]
    same = observed(a, "2026-09-05T02:00:00+00:00")
    b = observed(a, "2026-09-05T03:00:00+00:00", 2550.0)
    again = observed(a, "2026-09-05T04:00:00+00:00")
    assert benchmarks.save_benchmark_records([a], path) == 1
    assert benchmarks.save_benchmark_records([same], path) == 0
    assert benchmarks.save_benchmark_records([b, again], path) == 2
    saved = [json.loads(line) for line in path.read_text().splitlines()]
    assert saved == [a, b, again]
    assert saved[0]["version"] == saved[2]["version"] != saved[1]["version"]
    assert saved[0]["available_at"] != saved[2]["available_at"]


def test_save_separates_same_name_across_series_and_dates(tmp_path):
    a = collected()[0]
    b = benchmarks._record("KOSDAQ", a["index_name"], benchmarks._date(a["trade_date"]), a["close"], NOW)
    c = benchmarks._record("KOSPI", a["index_name"], benchmarks._date("20260903"), a["close"], NOW)
    assert benchmarks.save_benchmark_records([a, b, c], tmp_path / "bars.jsonl") == 3


@pytest.mark.parametrize("field,value", [("version", "bad"), ("source_id", "bad"), ("close", True),
                                        ("close", float("nan")), ("schema_version", True),
                                        ("bar_at", "2026-09-04T15:30:00"),
                                        ("available_at", "2026-09-04T01:00:00+00:00"),
                                        ("price_basis", "total_return")])
def test_save_validates_all_rows_before_any_write(tmp_path, field, value):
    path = tmp_path / "bars.jsonl"
    a = collected()[0]
    invalid = {**a, field: value}
    with pytest.raises(ValueError):
        benchmarks.save_benchmark_records([a, invalid], path)
    assert not path.exists()


def test_save_rejects_out_of_order_or_same_time_conflicting_observations(tmp_path):
    path = tmp_path / "bars.jsonl"
    a = collected()[0]
    benchmarks.save_benchmark_records([a], path)
    for timestamp in ["2026-09-05T00:00:00+00:00", a["available_at"]]:
        with pytest.raises(ValueError):
            benchmarks.save_benchmark_records([observed(a, timestamp, 2550.0)], path)
    assert len(path.read_text().splitlines()) == 1


def test_save_rejects_corrupt_storage_without_overwriting_it(tmp_path):
    path = tmp_path / "bars.jsonl"
    path.write_text("{broken\n")
    with pytest.raises(ValueError):
        benchmarks.save_benchmark_records(collected(), path)
    assert path.read_text() == "{broken\n"


def test_save_rejects_ambiguous_existing_revisions(tmp_path):
    path = tmp_path / "bars.jsonl"
    a = collected()[0]
    b = observed(a, a["available_at"], 2550.0)
    existing = "".join(json.dumps(row) + "\n" for row in [a, b])
    path.write_text(existing)
    with pytest.raises(ValueError, match="stored benchmark revisions"):
        benchmarks.save_benchmark_records([observed(a, "2026-09-05T02:00:00+00:00")], path)
    assert path.read_text() == existing


def test_empty_save_does_not_create_local_data(tmp_path):
    path = tmp_path / "bars.jsonl"
    assert benchmarks.save_benchmark_records([], path) == 0
    assert not path.exists()


def test_late_batch_conflict_does_not_partially_append_valid_earlier_rows(tmp_path):
    path = tmp_path / "bars.jsonl"
    original = collected()[0]
    benchmarks.save_benchmark_records([original], path)
    before = path.read_bytes()
    changed = observed(original, "2026-09-05T02:00:00+00:00", 2550.0)
    conflicting = observed(original, "2026-09-05T02:00:00+00:00", 2600.0)
    with pytest.raises(ValueError, match="share an availability timestamp"):
        benchmarks.save_benchmark_records([changed, conflicting], path)
    assert path.read_bytes() == before


def test_parallel_savers_keep_one_unchanged_episode_under_file_lock(tmp_path):
    path = tmp_path / "bars.jsonl"
    row = collected()[0]
    barrier = threading.Barrier(8)

    def save():
        barrier.wait(timeout=5)
        return benchmarks.save_benchmark_records([row], path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        writes = list(pool.map(lambda _: save(), range(8)))
    assert sum(writes) == 1
    assert [json.loads(line) for line in path.read_text().splitlines()] == [row]


def test_unterminated_valid_existing_record_is_not_concatenated_with_new_json(tmp_path):
    path = tmp_path / "bars.jsonl"
    row = collected()[0]
    existing = json.dumps(row)
    path.write_text(existing)
    with pytest.raises(ValueError, match="unterminated"):
        benchmarks.save_benchmark_records([observed(row, "2026-09-05T02:00:00+00:00", 2600.0)], path)
    assert path.read_text() == existing
