import hashlib
import json
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests

from scripts.data import krx_market as cli
from scripts.data import krx_relabel_calendar as relabel
from src.ingestion import krx_chart, krx_market as market
from src.ingestion.storage import read_rows

NOW = datetime(2026, 9, 8, 3, tzinfo=timezone.utc)


class Response:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code = payload, status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
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
def clock_and_environment(monkeypatch):
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW)
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    monkeypatch.delenv("KRX_API_KEY", raising=False)

    def no_env(*args, **kwargs):
        pytest.fail("Tests must not load .env files")

    # Importing the existing trading calendar pulls in runner utilities which
    # call this loader. Stub those imports without reading any environment file.
    monkeypatch.setattr("src.config.settings.load_project_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "load_project_env", no_env)


def raw(code="005930", day="20260904", **changes):
    return {"ISU_CD": code, "ISU_NM": "Stock " + code, "BAS_DD": day,
            "TDD_OPNPRC": "1,000", "TDD_HGPRC": "1,020", "TDD_LWPRC": "990",
            "TDD_CLSPRC": "1,010", "ACC_TRDVOL": "10,000", **changes}


def response(*rows):
    return Response({"OutBlock_1": list(rows)})


def collect(*rows, day="20260904"):
    return market.KrxMarketCollector("fixture-key", Session(response(*rows), response())).collect_day(day)


def day_path(tmp_path, day="20260904"):
    return tmp_path / "market" / "krx_daily" / day[:4] / f"{day}.jsonl"


def test_both_markets_keep_all_rows_and_normalize_metadata():
    session = Session(response(raw(), raw("000660")), response(raw("035720")))
    rows = market.KrxMarketCollector("fixture-key", session).collect_day("20260904")
    assert [row["stock_code"] for row in rows] == ["000660", "005930", "035720"]
    assert [row["market"] for row in rows] == ["KOSPI", "KOSPI", "KOSDAQ"]
    assert [call[0] for call in session.calls] == [krx_chart.KrxChartCollector.KOSPI_DAILY_URL,
                                                krx_chart.KrxChartCollector.KOSDAQ_DAILY_URL]
    assert all(call[1] == {"params": {"basDd": "20260904"}, "headers": {"AUTH_KEY": "fixture-key"},
                           "timeout": 20, "allow_redirects": False} for call in session.calls)
    row = rows[0]
    assert [row[field] for field in market.PRICE_FIELDS] == ["1000", "1020", "990", "1010", "10000"]
    assert row["trade_date"] == "2026-09-04"
    assert row["bar_at"] == "2026-09-04T15:30:00+09:00"
    assert row["collected_at"] == row["available_at"] == NOW.isoformat()
    assert row["price_basis"] == "unadjusted"
    assert row["calendar_status"] == "verified"
    assert row["source_url"] == session.calls[0][0] + "?basDd=20260904"
    content = {key: value for key, value in row.items() if key not in {"version", "collected_at", "available_at"}}
    assert row["version"] == hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert "fixture-key" not in json.dumps(rows)


def test_optional_fields_are_cleaned_when_present_and_absent_when_missing():
    full, missing = collect(raw(ACC_TRDVAL="10,100,000", MKTCAP="1,000,000,000", LIST_SHRS="0"), raw("035720"))
    assert {field: full[field] for field in market.OPTIONAL_FIELDS} == {
        "trading_value": "10100000", "market_cap": "1000000000", "listed_shares": "0"}
    assert not set(market.OPTIONAL_FIELDS) & missing.keys()


@pytest.mark.parametrize("field", ["ACC_TRDVAL", "MKTCAP", "LIST_SHRS"])
@pytest.mark.parametrize("value", [None, "-", "NaN", True, "1,2"])
def test_invalid_present_optional_fields_fail_instead_of_becoming_zero(field, value):
    with pytest.raises(ValueError):
        collect(raw(**{field: value}))


def test_weekday_empty_is_recorded_and_retried_without_a_daily_file(tmp_path):
    session = Session(response(), response(), response(), response())
    collector = market.KrxMarketCollector("fixture-key", session)
    expected = {"planned": ["20260904"], "skipped_existing": [], "fetched": 1,
                "empty": ["20260904"], "saved_rows": 0, "calendar_unverified": []}
    assert collector.backfill("20260904", "20260906", tmp_path, execute=True) == expected
    assert not day_path(tmp_path).exists()
    state = tmp_path / "market/krx_daily/_state.json"
    assert json.loads(state.read_text()) == {"empty_dates": ["20260904"]}
    assert collector.backfill("20260904", "20260906", tmp_path)["empty"] == ["20260904"]
    assert len(session.calls) == 2
    assert collector.backfill("20260904", "20260906", tmp_path, execute=True) == expected
    assert json.loads(state.read_text()) == {"empty_dates": ["20260904"]}
    assert len(session.calls) == 4


def test_later_publication_clears_empty_state_and_rerun_skips_date(tmp_path):
    session = Session(response(), response(), response(raw()), response(raw("035720")))
    collector = market.KrxMarketCollector("fixture-key", session)
    collector.backfill("20260904", "20260904", tmp_path, execute=True)
    result = collector.backfill("20260904", "20260904", tmp_path, execute=True)
    assert result["saved_rows"] == 2 and result["empty"] == []
    assert json.loads((tmp_path / "market/krx_daily/_state.json").read_text()) == {"empty_dates": []}
    assert collector.backfill("20260904", "20260904", tmp_path, execute=True) == {
        "planned": [], "skipped_existing": ["20260904"], "fetched": 0, "empty": [], "saved_rows": 0, "calendar_unverified": []}
    assert len(session.calls) == 4


def test_backfill_only_fetches_missing_weekdays(tmp_path):
    market.save_day(collect(raw()), tmp_path)
    session = Session(response(raw(day="20260907")), response())
    summary = market.KrxMarketCollector("fixture-key", session).backfill("20260904", "20260907", tmp_path, execute=True)
    assert summary == {"planned": ["20260907"], "skipped_existing": ["20260904"],
                       "fetched": 1, "empty": [], "saved_rows": 1, "calendar_unverified": []}
    assert len(session.calls) == 2


def test_reobservations_skip_identical_content_but_keep_revision_episodes(tmp_path, monkeypatch):
    original = collect(raw())[0]
    assert market.save_day([original], tmp_path) == 1
    original_bytes = day_path(tmp_path).read_bytes()
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW + timedelta(hours=1))
    same = collect(raw())[0]
    assert same["version"] == original["version"] and same["collected_at"] != original["collected_at"]
    assert market.save_day([same], tmp_path) == 0
    assert day_path(tmp_path).read_bytes() == original_bytes
    changed = collect(raw(TDD_CLSPRC="1,015"))[0]
    assert market.save_day([changed], tmp_path) == 1
    assert market.load_universe("20260904", tmp_path)[0]["close"] == "1015"
    monkeypatch.setattr(krx_chart, "_now", lambda: NOW + timedelta(hours=2))
    assert market.save_day(collect(raw()), tmp_path) == 1
    stored = read_rows(day_path(tmp_path))
    assert [row["close"] for row in stored] == ["1010", "1015", "1010"]
    assert len({row["metadata"]["version_id"] for row in stored}) == 3
    assert stored[0]["available_at"] == original["available_at"]
    assert market.load_universe("20260904", tmp_path) == [stored[-1]]


@pytest.mark.parametrize("changes", [{"ISU_NM": "Renamed"}, {"ACC_TRDVAL": "1"}, {"MKTCAP": "2"}, {"LIST_SHRS": "3"}])
def test_name_and_optional_content_changes_create_versions(changes):
    assert collect(raw())[0]["version"] != collect(raw(**changes))[0]["version"]


def test_dry_run_never_creates_a_session_requests_or_files(tmp_path, monkeypatch):
    def no_session():
        pytest.fail("Dry run must not create a session")

    monkeypatch.setattr(krx_chart.requests, "Session", no_session)
    expected = {"planned": ["20260904", "20260907"], "skipped_existing": [],
                "fetched": 0, "empty": [], "saved_rows": 0, "calendar_unverified": []}
    assert market.backfill("20260904", "20260907", tmp_path) == expected
    session = Session()
    assert market.KrxMarketCollector(session=session).backfill("20260904", "20260907", tmp_path) == expected
    assert session.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("explicit_dir", [False, True])
def test_cli_defaults_to_dry_run_without_loading_env(tmp_path, monkeypatch, capsys, explicit_dir):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    args = ["krx_market", "--from-date", "2026-09-04", "--to-date", "2026-09-07"]
    if explicit_dir:
        args.extend(["--data-dir", str(tmp_path)])
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(krx_chart.requests, "Session", lambda: pytest.fail("Unexpected session"))
    cli.main()
    assert json.loads(capsys.readouterr().out)["planned"] == ["20260904", "20260907"]
    assert list(tmp_path.iterdir()) == []


def test_cli_execute_loads_environment_before_fake_collection(tmp_path, monkeypatch, capsys):
    events = []
    session = Session(response(raw()), response())

    def fake_session():
        assert events == ["env"]
        return session

    session.close = lambda: events.append("closed")
    monkeypatch.setattr(cli, "load_project_env", lambda: events.append("env"))
    monkeypatch.setattr(krx_chart.requests, "Session", fake_session)
    monkeypatch.setattr("sys.argv", ["krx_market", "--from-date", "20260904", "--to-date", "20260904",
                                    "--data-dir", str(tmp_path), "--execute"])
    monkeypatch.setenv("KRX_OPEN_API_KEY", "fixture-key")
    cli.main()
    assert json.loads(capsys.readouterr().out)["saved_rows"] == 1
    assert events == ["env", "closed"]


def test_historical_universe_keeps_later_delisted_stock_and_prices_are_indexed(tmp_path):
    market.save_day(collect(raw(), raw("000001", ACC_TRDVAL="10,000")), tmp_path)
    market.save_day(collect(raw(day="20260907"), day="20260907"), tmp_path)
    assert {row["stock_code"] for row in market.load_universe("20260904", tmp_path)} == {"000001", "005930"}
    assert {row["stock_code"] for row in market.load_universe("20260907", tmp_path)} == {"005930"}
    frame = market.load_prices(None, "20260904", "20260907", tmp_path)
    assert frame.index.names == ["trade_date", "stock_code"] and frame.index.is_unique
    assert len(frame) == 3
    assert frame.loc[(pd.Timestamp("2026-09-04"), "000001"), "trading_value"] == 10000
    assert pd.isna(frame.loc[(pd.Timestamp("2026-09-04"), "005930"), "trading_value"])
    assert market.load_prices(["000001"], "20260904", "20260907", tmp_path)["close"].tolist() == [1010]
    assert market.load_prices([], "20260904", "20260907", tmp_path).empty
    assert market.load_universe("20260903", tmp_path) == []


@pytest.mark.parametrize("field", ["ISU_CD", "ISU_NM"])
def test_credential_echo_rejection_applies_to_both_markets(tmp_path, field):
    session = Session(response(raw()), response(raw("035720", **{field: "echo fixture-key"})))
    with pytest.raises(ValueError, match="credentials") as error:
        market.KrxMarketCollector("fixture-key", session).backfill("20260904", "20260904", tmp_path, execute=True)
    assert "fixture-key" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", [{"error": "fixture-key"}, {"OutBlock_1": None},
                                     {"OutBlock_1": [raw(BAS_DD="20260903")]},
                                     {"OutBlock_1": [raw(TDD_CLSPRC="-")]}, ValueError("fixture-key")])
def test_shared_response_validation_fails_without_saving_partial_day(tmp_path, payload):
    session = Session(response(raw()), Response(payload))
    with pytest.raises(ValueError) as error:
        market.KrxMarketCollector("fixture-key", session).backfill("20260904", "20260904", tmp_path, execute=True)
    assert "fixture-key" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", [Response({}, status_code=302), requests.Timeout("AUTH_KEY=fixture-key")])
def test_transport_errors_remain_sanitized(failure):
    with pytest.raises(requests.RequestException) as error:
        market.KrxMarketCollector("fixture-key", Session(failure)).collect_day("20260904")
    assert "fixture-key" not in str(error.value)


def test_duplicate_rows_and_conflicts_use_existing_validation():
    assert len(collect(raw(), raw())) == 1
    with pytest.raises(ValueError, match="conflicting duplicate"):
        collect(raw(), raw(TDD_CLSPRC="1015"))
    with pytest.raises(ValueError, match="duplicate"):
        market.KrxMarketCollector("fixture-key", Session(response(raw()), response(raw()))).collect_day("20260904")


def test_special_session_close_uses_shared_calendar():
    assert collect(raw(day="20251113"), day="20251113")[0]["bar_at"] == "2025-11-13T16:30:00+09:00"


@pytest.mark.parametrize("start,end", [("20260908", "20260908"), ("20260909", "20260909"),
                                     ("20260907", "20260904"), ("2026-9-4", "20260904")])
def test_invalid_ranges_fail_before_requests_or_writes(tmp_path, start, end):
    session = Session()
    with pytest.raises(ValueError):
        market.KrxMarketCollector("fixture-key", session).backfill(start, end, tmp_path, execute=True)
    assert session.calls == [] and list(tmp_path.iterdir()) == []


def test_missing_key_fails_without_request():
    session = Session()
    with pytest.raises(ValueError, match="required"):
        market.KrxMarketCollector(session=session).collect_day("20260904")
    assert session.calls == []


def test_save_day_rejects_mixed_dates_before_writing(tmp_path):
    rows = collect(raw()) + collect(raw(day="20260907"), day="20260907")
    with pytest.raises(ValueError, match="one trade date"):
        market.save_day(rows, tmp_path)
    assert market.save_day([], tmp_path) == 0
    assert list(tmp_path.iterdir()) == []


def test_concurrent_identical_saves_do_not_duplicate_or_lose_stocks(tmp_path):
    rows = collect(raw(), raw("000001"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        counts = list(executor.map(lambda _: market.save_day(rows, tmp_path), range(4)))
    assert sum(counts) == len(read_rows(day_path(tmp_path))) == 2


@pytest.mark.parametrize("day,status", [
    ("20261102", "unverified_special_session"),
    ("20260906", "exchange_calendar_mismatch"),
])
def test_historical_calendar_issues_keep_rows_without_inventing_close(day, status, monkeypatch):
    monkeypatch.setattr(krx_chart, "_now", lambda: datetime(2026, 12, 1, tzinfo=timezone.utc))
    row = collect(raw(day=day, FLUC_RT="-1.25", CMPPREVDD_PRC="-10", ACC_TRDVAL="10,000"), day=day)[0]
    assert row["calendar_status"] == status and "bar_at" not in row
    assert row["trade_date"] == datetime.strptime(day, "%Y%m%d").date().isoformat()
    assert row["collected_at"] == row["available_at"] == "2026-12-01T00:00:00+00:00"
    assert row["stock_code"] == "005930" and row["market"] == "KOSPI"
    assert [row[field] for field in market.PRICE_FIELDS] == ["1000", "1020", "990", "1010", "10000"]
    assert row["trading_value"] == "10000" and row["base_price"] == "1020"


@pytest.mark.parametrize("error", ["calendar_special_session_coverage_unverified", "nontrading_price_date"])
@pytest.mark.parametrize("observed,allowed", [
    ("2026-09-04T14:59:59+00:00", False),  # Still the trade date in KST.
    ("2026-09-04T15:00:00+00:00", True),   # Next calendar day in KST.
    ("2026-09-03T15:00:00+00:00", False),
    ("2026-09-05T00:00:00", False),
])
def test_calendar_exception_requires_aware_next_day_observation(monkeypatch, error, observed, allowed):
    from src.runner import trading_calendar

    def calendar_error(day):
        raise ValueError(error + ":" + day)

    def fetched_rows(self, url, day):
        return [{**raw(day=day), "_collected_at": observed}] if url == self.KOSPI_DAILY_URL else []

    monkeypatch.setattr(trading_calendar, "daily_session_close", calendar_error)
    monkeypatch.setattr(krx_chart.KrxChartCollector, "_fetch_market_rows", fetched_rows)
    if allowed:
        assert "bar_at" not in collect(raw())[0]
    else:
        with pytest.raises(ValueError, match=error):
            collect(raw())


def test_unrelated_calendar_value_error_propagates(monkeypatch):
    from src.runner import trading_calendar

    def invalid_calendar(day):
        raise ValueError("XKRX_calendar_year_out_of_supported_range")

    monkeypatch.setattr(trading_calendar, "daily_session_close", invalid_calendar)
    with pytest.raises(ValueError, match="XKRX_calendar_year_out_of_supported_range"):
        collect(raw())


@pytest.mark.parametrize("changes", [{"TDD_CLSPRC": "-1"}, {"ISU_NM": ""}, {"MKTCAP": "-1"},
                                     {"FLUC_RT": "NaN"}, {"CMPPREVDD_PRC": "--1"}])
def test_unverified_calendar_does_not_bypass_row_validation(changes, monkeypatch):
    monkeypatch.setattr(krx_chart, "_now", lambda: datetime(2026, 12, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        collect(raw(day="20261102", **changes), day="20261102")


def test_backfill_saves_verified_november_and_continues_to_december(tmp_path):
    session = Session(response(raw(day="20231130")), response(raw("035720", day="20231130")),
                      response(raw(day="20231201")), response())
    summary = market.KrxMarketCollector("fixture-key", session).backfill(
        "20231130", "20231201", tmp_path, execute=True)
    assert summary["calendar_unverified"] == []
    assert summary["fetched"] == 2 and summary["saved_rows"] == 3
    assert all(row["calendar_status"] == "verified" and row["bar_at"] == "2023-11-30T15:30:00+09:00"
               for row in market.load_universe("20231130", tmp_path))
    assert market.load_universe("20231201", tmp_path)[0]["calendar_status"] == "verified"


@pytest.mark.parametrize("failure,exception", [
    (requests.Timeout("fixture-key"), requests.RequestException),
    (Response({}, status_code=500), requests.RequestException),
    (Response(ValueError("fixture-key")), ValueError),
])
def test_backfill_request_failure_stops_immediately_and_preserves_completed_days(tmp_path, failure, exception):
    session = Session(response(raw(day="20231129")), response(),
                      response(raw(day="20231130")), failure,
                      response(raw(day="20231201")), response())
    with pytest.raises(exception):
        market.KrxMarketCollector("fixture-key", session).backfill(
            "20231129", "20231201", tmp_path, execute=True)
    assert len(session.calls) == 4
    assert len(market.load_universe("20231129", tmp_path)) == 1
    assert not day_path(tmp_path, "20231130").exists()
    assert not day_path(tmp_path, "20231201").exists()


def test_split_day_uses_exchange_change_rate_instead_of_raw_close_return(tmp_path):
    market.save_day(collect(raw(TDD_OPNPRC="2000", TDD_HGPRC="2020", TDD_LWPRC="1980",
                               TDD_CLSPRC="2000")), tmp_path)
    split = collect(raw(day="20260907", TDD_CLSPRC="987.50", TDD_LWPRC="980",
                        FLUC_RT="-1.25", CMPPREVDD_PRC="-12.50"), day="20260907")[0]
    assert split["change_rate_pct"] == "-1.25" and split["change_vs_base"] == "-12.50"
    assert split["base_price"] == "1000.00"
    market.save_day([split], tmp_path)
    frame = market.load_prices(["005930"], "20260904", "20260907", tmp_path)
    assert frame["ret_1d"].iloc[-1] == pytest.approx(-0.0125)
    assert frame["base_price"].iloc[-1] == 1000
    assert frame["close"].iloc[-1] / frame["close"].iloc[0] - 1 < -0.5
    assert pd.isna(frame["ret_1d"].iloc[0]) and pd.isna(frame["base_price"].iloc[0])


@pytest.mark.parametrize("rate,change,base", [("1.25", "1,234", "7766"), ("-1.25", "-1,234", "10234"),
                                           ("0", "0", "9000")])
def test_change_fields_allow_signed_numbers(rate, change, base):
    row = collect(raw(TDD_CLSPRC="9,000", FLUC_RT=rate, CMPPREVDD_PRC=change))[0]
    assert row["change_rate_pct"] == rate
    assert row["change_vs_base"] == change.replace(",", "") and row["base_price"] == base


@pytest.mark.parametrize("field", ["FLUC_RT", "CMPPREVDD_PRC"])
@pytest.mark.parametrize("value", [None, "-", "NaN", True, "1,2", "--1", "-inf"])
def test_invalid_present_change_fields_fail(field, value):
    with pytest.raises(ValueError):
        collect(raw(**{field: value}))


@pytest.mark.parametrize("changes,expected", [({}, {}), ({"FLUC_RT": "1"}, {"change_rate_pct": "1"}),
    ({"CMPPREVDD_PRC": "-10"}, {"change_vs_base": "-10", "base_price": "1020"})])
def test_missing_change_fields_are_not_invented(tmp_path, changes, expected):
    row = collect(raw(**changes))[0]
    assert {key: row[key] for key in (*market.CHANGE_FIELDS, "base_price") if key in row} == expected
    assert "change_rate" not in row and "ret_1d" not in row
    market.save_day([row], tmp_path)
    frame = market.load_prices(None, "20260904", "20260904", tmp_path)
    if "change_rate_pct" not in expected:
        assert pd.isna(frame["ret_1d"].iloc[0])
    if "base_price" not in expected:
        assert pd.isna(frame["base_price"].iloc[0])
    empty = market.load_prices([], "20260904", "20260904", tmp_path)
    assert empty.empty and {"ret_1d", "base_price"} <= set(empty.columns)


@pytest.mark.parametrize("day,hour", [
    ("20211118", "16"), ("20221117", "16"), ("20231116", "16"),
    ("20211101", "15"), ("20221101", "15"), ("20231101", "15"),
])
def test_relabel_dry_run_execute_and_idempotence_preserve_nonmetadata_bytes(tmp_path, day, hour):
    row = collect(raw(day=day, FLUC_RT="-1.25", CMPPREVDD_PRC="-10", ACC_TRDVAL="10,000"), day=day)[0]
    verified = {**row, "stock_code": "000001"}
    row["calendar_status"] = "unverified_special_session"
    row.pop("bar_at")
    row["available_at"] = "2026-09-09T00:00:00+00:00"
    market.save_day([row, verified], tmp_path)
    path = day_path(tmp_path, day)
    # Preserve even noncanonical JSON tokens and nested metadata byte-for-byte.
    original = path.read_bytes().replace(b'"open": "1000"', b'"open" : "\\u0031000"', 1)
    path.write_bytes(original)
    path.chmod(0o640)
    mode = stat.S_IMODE(path.stat().st_mode)
    path.with_suffix(".jsonl.lock").unlink()

    still_unverified = {**row, "trade_date": "2026-09-06", "collected_at": NOW.isoformat()}
    market.save_day([still_unverified], tmp_path)
    still_path = day_path(tmp_path, "20260906")
    still_bytes = still_path.read_bytes()
    snapshot = {entry: entry.read_bytes() for entry in tmp_path.rglob("*") if entry.is_file()}
    expected = [{"year": int(day[:4]), "files": 1, "rows_changed": 1, "rows_still_unverified": 0},
                {"year": 2026, "files": 1, "rows_changed": 0, "rows_still_unverified": 1}]
    assert relabel.relabel_calendar(tmp_path) == expected
    assert {entry: entry.read_bytes() for entry in tmp_path.rglob("*") if entry.is_file()} == snapshot
    assert stat.S_IMODE(path.stat().st_mode) == mode

    assert relabel.relabel_calendar(tmp_path, execute=True) == expected
    updated = path.read_bytes()
    changed = read_rows(path)[0]
    assert changed["calendar_status"] == "verified"
    assert changed["bar_at"] == row["trade_date"] + f"T{hour}:30:00+09:00"
    assert changed["available_at"] == row["collected_at"]
    assert {key: value for key, value in changed.items() if key not in {"calendar_status", "bar_at", "available_at"}} == {
        key: value for key, value in json.loads(original.splitlines()[0]).items()
        if key not in {"calendar_status", "bar_at", "available_at"}}
    assert updated.splitlines(keepends=True)[1] == original.splitlines(keepends=True)[1]
    for field in (*market.PRICE_FIELDS, *market.OPTIONAL_FIELDS, *market.CHANGE_FIELDS, "base_price", "version"):
        pattern = rb'"' + field.encode() + rb'"\s*:\s*"[^"\r\n]*"'
        assert re.findall(pattern, updated) == re.findall(pattern, original)
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert still_path.read_bytes() == still_bytes

    expected[0]["rows_changed"] = 0
    assert relabel.relabel_calendar(tmp_path, execute=True) == expected
    assert path.read_bytes() == updated and still_path.read_bytes() == still_bytes


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("symlink", [False, True])
def test_relabel_refuses_backup_directory_including_aliases(tmp_path, execute, symlink):
    backup = tmp_path / "HQA_data_backup_20260913"
    backup.mkdir()
    target = backup
    if symlink:
        target = tmp_path / "alias"
        target.symlink_to(backup, target_is_directory=True)
    with pytest.raises(ValueError, match="Refusing.*backup"):
        relabel.relabel_calendar(target, execute=execute)
    assert list(backup.iterdir()) == []


def test_relabel_cli_defaults_to_dry_run_without_network_or_env(tmp_path, monkeypatch, capsys):
    row = collect(raw(day="20231116"), day="20231116")[0]
    row["calendar_status"] = "unverified_special_session"
    row.pop("bar_at")
    market.save_day([row], tmp_path)
    before = day_path(tmp_path, "20231116").read_bytes()
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["krx_relabel_calendar"])
    monkeypatch.setattr(krx_chart.requests, "Session", lambda: pytest.fail("Unexpected network session"))
    relabel.main()
    assert json.loads(capsys.readouterr().out) == {
        "year": 2023, "files": 1, "rows_changed": 1, "rows_still_unverified": 0}
    assert day_path(tmp_path, "20231116").read_bytes() == before


def test_relabel_invalid_observation_fails_without_rewriting(tmp_path):
    row = collect(raw(day="20231116"), day="20231116")[0]
    row.update(calendar_status="unverified_special_session", collected_at="2023-11-16T16:00:00+09:00")
    market.save_day([row], tmp_path)
    path = day_path(tmp_path, "20231116")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="completed market close"):
        relabel.relabel_calendar(tmp_path, execute=True)
    assert path.read_bytes() == before
