"""Fake-only QP001 contract tests. All writes are confined to pytest tmp_path."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import Mock

import pytest
import requests

from research_data import dart_multi_raw as m
from scripts.qp001 import dart_multi_raw as cli
from src.ingestion.dart_financial_date_rules import FilingDates


CORP = "00126380"
UNIVERSE = [{"corp_code": CORP, "stock_code": "005930"}]
KEY = "fake-private-key"
NOW = datetime(2026, 10, 20, 10, tzinfo=m.KST)
RECEIPT = "20240515000001"
CORRECTION = "20240601000001"
NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("No real network, sessions, or environment reads permitted")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(requests, "Session", denied)
    monkeypatch.setattr("src.config.settings.load_project_env", denied)


class Response:
    def __init__(self, payload, http=200):
        self.content = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, indent=1).encode()
        self.status_code = http


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert self.responses, "unexpected request"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, Response) else Response(response)


def account(name="자산총계", *, receipt=RECEIPT, fs="CFS", currency="KRW", amount="1,234", **extra):
    return {"corp_code": CORP, "bsns_year": "2024", "reprt_code": "11013", "fs_div": fs,
            "rcept_no": receipt, "currency": currency, "sj_div": "BS", "account_nm": name,
            "thstrm_amount": amount, "frmtrm_amount": "1,000", **extra}


def payload(*accounts):
    return {"status": "000", "message": "정상", "list": list(accounts)}


def run(tmp_path, *responses, **kwargs):
    session = Session(*responses)
    kwargs.setdefault("clock", lambda: NOW)
    kwargs.setdefault("universe_source", UNIVERSE)
    result = m.fetch(tmp_path, execute=True, api_key=KEY, session=session,
                     from_year=2024, to_year=2024, **kwargs)
    return result, session


def root(tmp_path):
    return tmp_path / "fundamentals/dart_multi_raw"


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def quarterly_row(**changes):
    return {"corp_code": CORP, "bsns_year": 2024, "reprt_code": "11013", "fs_div": "CFS",
            "rcept_no": RECEIPT, "currency": "KRW", **changes}


def links(tmp_path, accounts, quarterly, *, profiles=()):
    run(tmp_path, payload(*accounts), *[NO_DATA] * 3)
    write_jsonl(tmp_path / "fundamentals/dart_quarterly/2024_11013.jsonl", quarterly)
    if profiles:
        write_jsonl(tmp_path / "reference/dart_company/companies.jsonl", profiles)
    return m.link_check(tmp_path, from_year=2024, to_year=2024)


def test_plan_and_default_fetch_are_read_only(tmp_path):
    clock = Mock(side_effect=AssertionError("dry-run clock"))
    options = dict(data_dir=tmp_path, universe_source=UNIVERSE)
    result = m.plan(**options)
    assert result["total_requests"] == result["remaining_requests"] == 44
    assert len(result["by_year"]) == 11
    assert all(x["total_requests"] == 4 and x["coverage"] == 0 for x in result["by_year"].values())
    dry = m.fetch(**options, clock=clock)
    assert dry["status"] == "dry_run"
    assert dry["max_requests"] == dry["daily_cap"] == 500
    clock.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_batching_params_and_013_resume(tmp_path):
    universe = [{"corp_code": f"{i:08d}", "stock_code": f"{i:06d}"} for i in reversed(range(201))]
    result, session = run(tmp_path, *[NO_DATA] * 12, universe_source=universe)
    assert result["requests_made"] == 12 and result["remaining_requests"] == 0
    for i, (url, kwargs) in enumerate(session.calls):
        assert url == m.quarterly.URL
        assert kwargs["timeout"] == 20
        assert kwargs["allow_redirects"] is False
        assert set(kwargs["params"]) == {"crtfc_key", "corp_code", "bsns_year", "reprt_code"}
        assert kwargs["params"]["crtfc_key"] == KEY
        assert kwargs["params"]["bsns_year"] == "2024"
        assert kwargs["params"]["reprt_code"] == list(m.quarterly.REPORTS)[i // 3]
        assert kwargs["params"]["corp_code"].split(",") == [f"{n:08d}" for n in range(i % 3 * 100, min(i % 3 * 100 + 100, 201))]
    before = {p.relative_to(root(tmp_path)): p.read_bytes() for p in root(tmp_path).rglob("*") if p.is_file()}
    result, session = run(tmp_path, universe_source=universe)
    assert session.calls == [] and result["requests_made"] == 0
    assert before == {p.relative_to(root(tmp_path)): p.read_bytes() for p in root(tmp_path).rglob("*") if p.is_file()}
    assert len(list((root(tmp_path) / "requests").glob("*.json"))) == 12
    assert all(p.read_bytes() == b"" for p in (root(tmp_path) / "derived").glob("*.jsonl"))


def test_quarterly_batch_checkpoint_is_verified(tmp_path):
    job = m.Job(2024, "11013", (CORP,))
    checkpoint = tmp_path / "fundamentals/dart_quarterly/_state.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({"completed_batches": [job.key]}))
    assert m.plan(tmp_path, universe_source=UNIVERSE)["total_requests"] == 44
    with pytest.raises(ValueError, match="batches differ"):
        m.plan(tmp_path, universe_source=[{"corp_code": "00000001", "stock_code": "000001"}])


def test_existing_quarterly_universe_loader_includes_supplement_and_aliases(tmp_path):
    master = tmp_path / "corp_codes.csv"
    master.write_text("corp_code,stock_code\n00126380,005930\n")
    listing = tmp_path / "disclosures/dart_full/list"
    listing.mkdir(parents=True)
    supplement = tmp_path / "disclosures/dart_full/list_supplement/2024/20240515.jsonl"
    write_jsonl(supplement, [{"corp_code": CORP, "stock_code": "005935", "rcept_dt": "20240515", "rcept_no": RECEIPT},
        {"corp_code": "00000001", "stock_code": "00088K", "rcept_dt": "20240515", "rcept_no": "20240515000002"}])
    universe = m.quarterly.load_universe(corp_codes_path=master, listing_dir=listing)
    assert universe == {"00000001": ["00088K"], CORP: ["005930", "005935"]}


def test_raw_bytes_metadata_full_accounts_and_sha(tmp_path):
    data = payload(account(), account("부채 총계", amount="500"), account("자본총계", amount="734"),
                   account("매출액", sj_div="IS"), account("유동자산", amount="200"))
    data["unrecognized_future_field"] = {"keep": [1, 2, "한글"]}
    body = json.dumps(data, ensure_ascii=False, indent=3).encode() + b"\n"
    result, _ = run(tmp_path, Response(body), *[NO_DATA] * 3)
    metadata = json.loads(next((root(tmp_path) / "requests").glob("2024_11013_*.json")).read_bytes())
    digest = hashlib.sha256(body).hexdigest()
    assert metadata["sha256"] == digest
    assert metadata["http_status"] == 200 and metadata["dart_status"] == "000"
    assert metadata["fetched_at"] == NOW.isoformat()
    assert "crtfc_key" not in metadata["params"]
    assert (root(tmp_path) / "blobs" / f"{digest}.json").read_bytes() == body
    derived = list(m._rows(next((root(tmp_path) / "derived").glob("2024_11013_*.jsonl"))))
    assert result["derived_rows"] == len(derived) == 1
    assert derived[0]["available_date"] == "2024-05-15"
    assert len(derived[0]["accounts"]) == 4
    assert {r["metric"] for r in derived[0]["accounts"]} == {"total_assets", "total_liabilities", "total_equity", None}
    assert derived[0]["accounts"][0]["frmtrm_amount"] == "1,000"
    assert not any(KEY.encode() in p.read_bytes() for p in root(tmp_path).rglob("*") if p.is_file())


def test_raw_immutable_same_key_different_bytes_refused(tmp_path):
    job = m.Job(2024, "11013", (CORP,))
    first = b'{"status":"013"}'
    stored = m.store_response(tmp_path, job, first, 200, NOW)
    again = m.store_response(tmp_path, job, first, 200, NOW + timedelta(days=1))
    assert stored == again
    with pytest.raises(ValueError, match="immutable response conflict"):
        m.store_response(tmp_path, job, b'{ "status": "013" }', 200, NOW)
    assert len(list((tmp_path / "blobs").glob("*.json"))) == 1
    assert m._stored(tmp_path, job) == stored


def test_immutable_publication_refuses_existing_different_content(tmp_path):
    path = tmp_path / "immutable.json"
    m._immutable(path, b"first")
    m._immutable(path, b"first")
    with pytest.raises(ValueError, match="immutable archive conflict"):
        m._immutable(path, b"different")
    assert path.read_bytes() == b"first"


def test_tampered_raw_fails_plan_and_fetch_before_network(tmp_path):
    run(tmp_path, NO_DATA, max_requests=1)
    blob = next((root(tmp_path) / "blobs").glob("*.json"))
    blob.write_bytes(b'{"status":"013"}')
    with pytest.raises(ValueError, match="sha256 mismatch"):
        m.plan(tmp_path, from_year=2024, to_year=2024, universe_source=UNIVERSE)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        run(tmp_path)


def test_crash_after_raw_publication_rebuilds_derived_without_request(tmp_path, monkeypatch):
    original = m._derive
    monkeypatch.setattr(m, "_derive", Mock(side_effect=OSError("simulated crash")))
    with pytest.raises(OSError):
        run(tmp_path, payload(account()), max_requests=1)
    assert len(list((root(tmp_path) / "requests").glob("*.json"))) == 1
    monkeypatch.setattr(m, "_derive", original)
    result, session = run(tmp_path, *[NO_DATA] * 3)
    assert result["requests_made"] == len(session.calls) == 3
    assert result["remaining_requests"] == 0
    assert len(list((root(tmp_path) / "derived").glob("*.jsonl"))) == 4


def test_frozen_universe_drift_refused(tmp_path):
    run(tmp_path, NO_DATA, max_requests=1)
    with pytest.raises(ValueError, match="frozen"):
        run(tmp_path, universe_source=[*UNIVERSE, {"corp_code": "00000001", "stock_code": "000001"}])


def test_per_run_max_and_daily_ledger_across_runs(tmp_path):
    result, _ = run(tmp_path, NO_DATA, max_requests=1, daily_cap=2)
    assert result["reason"] == "max_requests" and result["remaining_requests"] == 3
    result, _ = run(tmp_path, NO_DATA, max_requests=10, daily_cap=2)
    assert result["reason"] == "daily_cap" and result["requests_made"] == 1
    result, session = run(tmp_path, max_requests=10, daily_cap=2)
    assert result["reason"] == "daily_cap" and session.calls == []
    quota = json.loads((root(tmp_path) / "_quota.json").read_text())
    assert quota["days"]["2026-10-20"]["requests"] == 2


def test_kst_day_rollover_across_runs(tmp_path):
    before = datetime(2026, 10, 20, 14, 59, tzinfo=timezone.utc)
    result, _ = run(tmp_path, NO_DATA, daily_cap=1, clock=lambda: before)
    assert result["reason"] == "daily_cap"
    result, _ = run(tmp_path, NO_DATA, daily_cap=1, clock=lambda: before + timedelta(minutes=1))
    assert result["requests_made"] == 1
    days = json.loads((root(tmp_path) / "_quota.json").read_text())["days"]
    assert {k: v["requests"] for k, v in days.items()} == {"2026-10-20": 1, "2026-10-21": 1}


def test_kst_rollover_during_run_still_obeys_per_run_budget(tmp_path):
    before = datetime(2026, 10, 20, 23, 59, tzinfo=m.KST)
    moments = iter([before, before, before + timedelta(minutes=1), before + timedelta(minutes=1)])
    result, _ = run(tmp_path, NO_DATA, NO_DATA, daily_cap=1, max_requests=2, clock=lambda: next(moments))
    assert result["reason"] == "max_requests" and result["requests_made"] == 2


@pytest.mark.parametrize("status", ["020", "800"])
def test_provider_stop_preserves_attempt_not_completion(tmp_path, status):
    result, session = run(tmp_path, {"status": status, "message": "provider stop"})
    assert result["status"] == "stopped" and result["reason"] == f"provider_status_{status}"
    assert len(session.calls) == result["requests_made"] == 1
    assert result["remaining_requests"] == 4
    assert len(list((root(tmp_path) / "attempts").glob("*.json"))) == 1
    assert not list((root(tmp_path) / "requests").glob("*.json"))
    if status == "020":
        again, session = run(tmp_path)
        assert again["reason"] == "provider_status_020" and not session.calls
        again, session = run(tmp_path, NO_DATA, max_requests=1, clock=lambda: NOW + timedelta(days=1))
    else:
        again, session = run(tmp_path, NO_DATA, max_requests=1)
    assert again["requests_made"] == len(session.calls) == 1


@pytest.mark.parametrize("response", [
    Response(b"<html>unavailable</html>", 503), Response({"status": "000", "list": []}),
    Response({"status": "013", "list": [account()]}), Response({"status": "100"}),
    Response({"status": "013"}, 500), Response(NO_DATA, 301), Response(b"null"),
    Response(b'{"status": 13}'), Response(b'{"status": 100}'),
])
def test_bad_http_or_payload_is_audited_and_charged(tmp_path, response):
    with pytest.raises(ValueError, match="response failed"):
        run(tmp_path, response)
    assert len(list((root(tmp_path) / "attempts").glob("*.json"))) == 1
    assert json.loads((root(tmp_path) / "_quota.json").read_text())["days"]["2026-10-20"]["requests"] == 1
    assert not list((root(tmp_path) / "requests").glob("*.json"))


def test_transport_failure_is_charged_and_never_retried(tmp_path):
    with pytest.raises(ValueError, match="transport failure") as error:
        run(tmp_path, requests.Timeout(f"https://secret?crtfc_key={KEY}"))
    assert KEY not in str(error.value)
    result, session = run(tmp_path, daily_cap=1)
    assert not session.calls and result["reason"] == "daily_cap"


def test_malformed_receipt_fails_but_complete_raw_never_refetched(tmp_path):
    row = account(rcept_no="bad")
    with pytest.raises(ValueError):
        run(tmp_path, payload(row))
    assert m.plan(tmp_path, from_year=2024, to_year=2024, universe_source=UNIVERSE)["completed_requests"] == 1
    with pytest.raises(ValueError):
        run(tmp_path)


@pytest.mark.parametrize("field,value,reason", [("corp_code", "00000001", "corp_not_in_batch"),
    ("bsns_year", "2023", "bsns_year"), ("reprt_code", "11011", "reprt_code"), ("fs_div", "bad", "fs_div"),
    ("currency", 1, "currency_type"), ("account_nm", "", "account_nm"), ("sj_div", None, "sj_div")])
def test_out_of_request_or_unusable_rows_are_skipped_and_recorded(tmp_path, field, value, reason):
    # Same row-level rule as dart_quarterly: skip and count; the raw response stays preserved.
    run(tmp_path, payload(account(), account(**{field: value})), max_requests=1)
    assert m.plan(tmp_path, from_year=2024, to_year=2024, universe_source=UNIVERSE)["completed_requests"] == 1
    side = next((root(tmp_path) / "derived").glob("*.skipped.json"))
    assert json.loads(side.read_text(encoding="utf-8"))["skipped_rows"] == {reason: 1}


def test_corrected_filings_link_only_the_same_version_and_keep_S_O_dates(tmp_path):
    result = links(tmp_path, [account(), account(receipt=CORRECTION, amount="2,345")],
        [quarterly_row(), quarterly_row(rcept_no=CORRECTION), quarterly_row(rcept_no="20240516000001")],
        profiles=[{"corp_code": CORP, "corp_cls": "Y"}])
    assert result["overall"] == {"total": 3, "matched": 2, "missing_version": 1, "fraction": 2 / 3}
    rows = list(m._rows(next((root(tmp_path) / "derived").glob("2024_11013_*.jsonl"))))
    dates = FilingDates([{"corp_code": CORP, "rcept_no": RECEIPT, "rcept_dt": "20240515", "report_nm": "분기보고서 (2024.03)"},
        {"corp_code": CORP, "rcept_no": CORRECTION, "rcept_dt": "20240601", "report_nm": "[기재정정]분기보고서 (2024.03)"}])
    info = dates.annotate(next(row for row in rows if row["rcept_no"] == CORRECTION))
    assert info["actual_public_date"] == "2024-06-01"
    assert info["assumed_public_date"] == "2024-05-15"
    assert info["original_rcept_no"] == RECEIPT
    index = m._asset_index(root(tmp_path), "2024")
    assert index[m._version(quarterly_row())]["KRW"] == {Decimal(1234)}
    assert index[m._version(quarterly_row(rcept_no=CORRECTION))]["KRW"] == {Decimal(2345)}


def test_currency_fs_div_receipt_and_missing_currency_never_cross_link(tmp_path):
    result = links(tmp_path, [account(), account(fs="OFS", currency="USD")],
        [quarterly_row(), quarterly_row(fs_div="OFS", currency="USD"),
         quarterly_row(fs_div="OFS"), quarterly_row(currency="USD"),
         quarterly_row(currency=None), quarterly_row(rcept_no=CORRECTION)])
    assert result["overall"]["matched"] == 2
    assert result["overall"]["currency_mismatch_or_missing"] == 3
    assert result["overall"]["missing_version"] == 1


def test_multiple_currencies_in_one_version_are_preserved_separately(tmp_path):
    result = links(tmp_path, [account(), account(currency="USD", amount="900")],
                   [quarterly_row(), quarterly_row(currency="USD"), quarterly_row(currency="EUR")])
    assert result["overall"]["matched"] == 2
    assert result["overall"]["currency_mismatch_or_missing"] == 1
    rows = list(m._rows(next((root(tmp_path) / "derived").glob("2024_11013_*.jsonl"))))
    assert len(rows) == 1 and len(rows[0]["accounts"]) == 2


@pytest.mark.parametrize("amount,matched", [("0", 1), ("-1", 1), ("-", 0), (None, 0), ("NaN", 0), ("Infinity", 0)])
def test_missing_invalid_and_zero_asset_values(tmp_path, amount, matched):
    result = links(tmp_path, [account(amount=amount)], [quarterly_row()])
    assert result["overall"]["matched"] == matched


def test_conflicting_asset_values_not_silently_selected(tmp_path):
    result = links(tmp_path, [account(), account(amount="999")], [quarterly_row()])
    assert result["overall"]["matched"] == 0
    assert result["overall"]["conflicting_total_assets"] == 1


def test_missing_assets_does_not_borrow_other_bs_accounts(tmp_path):
    result = links(tmp_path, [account("부채총계")], [quarterly_row()])
    assert result["overall"]["matched"] == 0
    assert result["overall"]["missing_total_assets"] == 1


def test_listed_delisted_unknown_and_year_cross_sections(tmp_path):
    corp2, corp3 = "00000002", "00000003"
    universe = [*UNIVERSE, {"corp_code": corp2, "stock_code": "000002"}, {"corp_code": corp3, "stock_code": "000003"}]
    run(tmp_path, payload(account(), account(corp_code=corp2)), *[NO_DATA] * 3, universe_source=universe)
    write_jsonl(tmp_path / "fundamentals/dart_quarterly/2024_11013.jsonl",
                [quarterly_row(), quarterly_row(corp_code=corp2), quarterly_row(corp_code=corp3)])
    write_jsonl(tmp_path / "reference/dart_company/companies.jsonl", [
        {"corp_code": CORP, "corp_cls": "K"}, {"corp_code": corp2, "corp_cls": "E"}])
    supplement = tmp_path / "disclosures/dart_full/list_supplement/_state.json"
    supplement.parent.mkdir(parents=True)
    supplement.write_text(json.dumps({"target_corps": [corp2, corp3]}))
    report = m.link_check(tmp_path, from_year=2024, to_year=2024)
    assert report["by_year"]["2024"]["fraction"] == 2 / 3
    for label, expected in (("listed", 1), ("delisted", 1), ("unknown", 0)):
        assert report["by_listing_status"][label]["fraction"] == expected
        assert report["by_year_and_listing_status"]["2024"][label]["total"] == 1


def test_konex_is_listed_even_if_it_is_a_supplement_target(tmp_path):
    supplement = tmp_path / "disclosures/dart_full/list_supplement/_state.json"
    supplement.parent.mkdir(parents=True)
    supplement.write_text(json.dumps({"target_corps": [CORP]}))
    result = links(tmp_path, [account()], [quarterly_row()],
                   profiles=[{"corp_code": CORP, "corp_cls": "N"}])
    assert result["by_listing_status"]["listed"]["matched"] == 1
    assert result["by_listing_status"]["delisted"]["total"] == 0


def test_link_check_before_collection_reports_real_zero_coverage_without_writes(tmp_path):
    write_jsonl(tmp_path / "fundamentals/dart_quarterly/2024_11013.jsonl", [quarterly_row()])
    report = m.link_check(tmp_path, from_year=2024, to_year=2024)
    assert report["overall"]["fraction"] == 0
    assert report["by_listing_status"]["listed"]["fraction"] is None
    assert not root(tmp_path).exists()


def test_link_check_defaults_to_all_existing_years_including_2026(tmp_path):
    write_jsonl(tmp_path / "fundamentals/dart_quarterly/2024_11013.jsonl", [quarterly_row()])
    write_jsonl(tmp_path / "fundamentals/dart_quarterly/2026_11013.jsonl", [quarterly_row(bsns_year=2026)])
    result = m.link_check(tmp_path)
    assert result["overall"]["total"] == 2 and set(result["by_year"]) == {"2024", "2026"}
    assert m.link_check(tmp_path, to_year=2025)["overall"]["total"] == 1


def test_link_check_missing_or_tampered_derivation_fails_clearly(tmp_path):
    links(tmp_path, [account()], [quarterly_row()])
    path = next((root(tmp_path) / "derived").glob("2024_11013_*.jsonl"))
    original = path.read_text()
    path.write_text(original.replace('"1,234"', '"999"'))
    with pytest.raises(ValueError, match="differs from raw"):
        m.link_check(tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match="derived archive missing"):
        m.link_check(tmp_path)
    run(tmp_path)
    assert m.link_check(tmp_path)["overall"]["matched"] == 1


@pytest.mark.parametrize("kwargs", [{"max_requests": 0}, {"daily_cap": -1}, {"daily_cap": True},
    {"from_year": 2014}, {"to_year": 2026}, {"from_year": 2025, "to_year": 2024}])
def test_invalid_configuration_fails_before_writes(tmp_path, kwargs):
    with pytest.raises(ValueError):
        m.fetch(tmp_path, universe_source=UNIVERSE, **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_malformed_ledger_cannot_reset_budget(tmp_path):
    run(tmp_path, NO_DATA, max_requests=1)
    (root(tmp_path) / "_quota.json").write_text('{"days": {"2026-10-20": {"requests": -1, "provider_limited": false}}}')
    with pytest.raises(ValueError, match="invalid quota"):
        run(tmp_path)


def test_execute_needs_key_before_creating_directory(tmp_path):
    with pytest.raises(ValueError, match="API key"):
        m.fetch(tmp_path, execute=True, universe_source=UNIVERSE)
    assert not root(tmp_path).exists()


@pytest.mark.parametrize("command", ["plan", "fetch"])
def test_cli_offline_commands_do_not_load_environment(tmp_path, monkeypatch, capsys, command):
    real = m.quarterly.load_universe
    monkeypatch.setattr(m.quarterly, "load_universe", lambda **kw: real(universe_source=UNIVERSE))
    checkpoint = tmp_path / "fundamentals/dart_quarterly/_state.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({"completed_batches": [m.Job(2015, "11013", (CORP,)).key]}))
    assert cli.main([command, "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["total_requests"] == 44
    assert not root(tmp_path).exists()


def test_cli_execute_loads_env_once_and_passes_explicit_caps(tmp_path, monkeypatch, capsys):
    load = Mock()
    fetch = Mock(return_value={"status": "ok"})
    monkeypatch.setattr("src.config.settings.load_project_env", load)
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setattr(m, "fetch", fetch)
    assert cli.main(["fetch", "--execute", "--data-dir", str(tmp_path), "--max-requests", "7", "--daily-cap", "9"]) == 0
    load.assert_called_once_with()
    assert fetch.call_args.kwargs["api_key"] == KEY
    assert fetch.call_args.kwargs["max_requests"] == 7
    assert fetch.call_args.kwargs["daily_cap"] == 9
    assert fetch.call_args.kwargs["execute"] is True
    assert KEY not in capsys.readouterr().out


def test_cli_link_check_and_clear_error(tmp_path, capsys):
    assert cli.main(["link-check", "--data-dir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    write_jsonl(tmp_path / "fundamentals/dart_quarterly/2024_11013.jsonl", [quarterly_row()])
    assert cli.main(["link-check", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["overall"]["fraction"] == 0


def test_cli_import_has_no_environment_file_or_network_side_effects():
    # Install the guard before importing any project module, unlike a fixture.
    source = '''
import sys
from pathlib import Path
def guard(event, args):
    if event == "open" and isinstance(args[0], str) and Path(args[0]).name.startswith(".env"):
        raise AssertionError("environment file access is forbidden")
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("network is forbidden")
sys.addaudithook(guard)
from scripts.qp001.dart_multi_raw import main
main(["--help"])
'''
    result = subprocess.run([sys.executable, "-B", "-c", source],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "link-check" in result.stdout


def test_rows_outside_the_request_are_skipped_and_counted():
    # DART can return a later fiscal year's rows (seen 2026-10-20: a 2024 annual request returned bsns_year 2025).
    job = m.Job(2024, "11011", ("00442455",))
    base = {"corp_code": "00442455", "reprt_code": "11011", "fs_div": "CFS", "sj_div": "BS",
            "account_nm": "자산총계", "currency": "KRW", "rcept_no": "20250320000001", "thstrm_amount": "100"}
    payload = {"status": "000", "list": [dict(base, bsns_year="2024"),
                                         dict(base, bsns_year="2025", rcept_no="20260324000091"),
                                         dict(base, bsns_year="2024", corp_code="99999999")]}
    skipped = {}
    rows = m.derive_rows(payload, job, {"00442455": ["123450"]}, "0" * 64, skipped)
    assert [row["rcept_no"] for row in rows] == ["20250320000001"]
    assert skipped == {"bsns_year": 1, "corp_not_in_batch": 1}
