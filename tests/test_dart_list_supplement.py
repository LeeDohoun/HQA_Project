import csv
import json
from datetime import datetime, timedelta

import pytest

from scripts.data import dart_list_supplement as cli
from src.ingestion import dart_list_supplement as module
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_listing import directory, listing_rows, listing_status
from src.ingestion.storage import write_rows


CORP, OTHER, KEY = "00000001", "00000002", "fake-private-key"
NOW = datetime(2026, 10, 13, 10, tzinfo=module.KST)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, *payloads):
        self.payloads, self.calls = list(payloads), []

    def get(self, url, **kwargs):
        self.calls.append(kwargs["params"])
        assert self.payloads, "unexpected provider request"
        return Response(self.payloads.pop(0))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a, **k):
        pytest.fail("real session or environment access")
    monkeypatch.setattr("requests.Session", denied)
    monkeypatch.setattr(cli, "load_project_env", denied)
    monkeypatch.setattr("src.config.settings.load_project_env", denied)


@pytest.fixture
def data(tmp_path):
    master = tmp_path / "reference/corp_codes.csv"
    master.parent.mkdir()
    with master.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("corp_code", "stock_code"))
        writer.writeheader()
        writer.writerows([{"corp_code": CORP, "stock_code": "000010"},
                         {"corp_code": OTHER, "stock_code": "000020"},
                         {"corp_code": "00000003", "stock_code": "000030"},
                         {"corp_code": "00000004", "stock_code": "000040"}])
    write_rows(tmp_path / "reference/dart_company/companies.jsonl",
               [{"corp_code": CORP, "corp_cls": "E"}, {"corp_code": "00000003", "corp_cls": "Y"},
                {"corp_code": "00000004", "corp_cls": "N"}])
    for day in ("20160602", "20181231"):
        write_rows(tmp_path / "market/krx_daily" / day[:4] / f"{day}.jsonl",
                   [{"stock_code": c, "trade_date": day} for c in ("000010", "000020", "000030", "999990", "00088K")])
    path = directory(tmp_path) / "_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"completed_listing_days": ["20160601", "20181231"]}))
    return tmp_path


def row(number=1, day="20160602", corp=CORP, stock="000010"):
    return {"corp_code": corp, "corp_name": "과거상장", "stock_code": stock, "corp_cls": "E",
            "report_nm": "분기보고서 (2016.03)", "rcept_no": day + f"{number:06d}",
            "flr_nm": "회사", "rcept_dt": day, "rm": ""}


def page(rows, *, total=None, number=1):
    total = len(rows) if total is None else total
    return {"status": "000", "page_no": number, "page_count": 100, "total_count": total,
            "total_page": (total + 99) // 100, "list": rows}


def run(data, *payloads, now=NOW, **kwargs):
    session = Session(*payloads)
    result = module.supplement(data_dir=data, execute=True, api_key=KEY, session=session,
        clock=lambda: now, sleeper=lambda _: None, **kwargs)
    return result, session


def test_target_scope_unmapped_counts_dry_run_without_writes_or_requests(data, monkeypatch, capsys):
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    result = module.supplement(data_dir=data)
    assert result["target_corps"] == 2
    assert result["unmapped_stock_days"] == {"00088K": 2, "999990": 2}
    assert result["estimated_calls"]["minimum_long_range_calls"] == 2
    assert result["estimated_calls"]["year_chunk_first_page_calls_if_all_rejected"] == 8
    assert result["current_class_counts"] == {"E": 1, "unknown": 1}
    assert cli.main(["--data-dir", str(data)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    assert before == {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}


def test_strict_pagination_raw_storage_and_idempotent_resume(data):
    rows = [row(i) for i in range(1, 102)]
    result, session = run(data, page(rows[:100], total=101), page(rows[100:], total=101, number=2), {"status": "013"})
    assert result["status"] == "complete" and len(session.calls) == 3
    assert session.calls[0]["corp_code"] == CORP and session.calls[0]["last_reprt_at"] == "N"
    assert "corp_cls" not in session.calls[0]
    assert listing_rows(data, "20160602") == rows
    assert result["state"]["rows_per_corp"] == {CORP: 101, OTHER: 0}
    from src.ingestion.dart_listing import supplement_manifest
    assert result["state"]["file_manifest"] == supplement_manifest(data)
    assert result["state"]["file_manifest"]["2016/20160602.jsonl"]["rows"] == 101
    assert listing_status(data)["supplement_complete"]
    resumed, session = run(data)
    assert session.calls == [] and resumed["state"] == result["state"]
    assert KEY not in json.dumps(result)


def test_parameter_range_error_falls_back_to_calendar_years_with_pagination(data):
    result, session = run(data, {"status": "100", "message": "range"}, page([row()]),
                          {"status": "013"}, page([row(2, "20181231")]), {"status": "013"})
    assert result["state"]["range_mode_per_corp"][CORP] == "calendar_years"
    assert [(p["bgn_de"], p["end_de"]) for p in session.calls[:4]] == [
        ("20160601", "20181231"), ("20160601", "20161231"), ("20170101", "20171231"), ("20180101", "20181231")]
    assert result["state"]["rows_per_corp"][CORP] == 2


@pytest.mark.parametrize("status", ["010", "014", "900", "021"])
def test_other_statuses_fail_without_completing_corp(data, status):
    with pytest.raises(DartAPIError, match="status=" + status):
        run(data, {"status": status, "message": KEY})
    assert json.loads((directory(data) / "list_supplement/_state.json").read_text())["completed_corps"] == []


@pytest.mark.parametrize("change", ["wrong_corp", "outside_range", "bad_day", "duplicate", "metadata", "no_data_later", "changed_total", "duplicate_later"])
def test_page_contract_fails_before_any_partial_corp_publication(data, change):
    rows, rest = [row(i) for i in range(1, 101)], []
    first = page(rows)
    if change == "wrong_corp":
        rows[0]["corp_code"] = OTHER
    elif change == "outside_range":
        rows[0]["rcept_dt"] = "20150501"
    elif change == "bad_day":
        rows[0]["rcept_dt"] = "20160230"
    elif change == "duplicate":
        rows[1] = rows[0].copy()
    elif change == "metadata":
        first["total_page"] = 3
    else:
        first = page(rows, total=101)
        rest = [{"status": "013"}] if change == "no_data_later" else [page([row(101)], total=102 if change == "changed_total" else 101, number=2)]
        if change == "duplicate_later":
            rest[0]["list"] = [rows[0]]
    with pytest.raises(DartAPIError):
        run(data, first, *rest)
    assert not list((directory(data) / "list_supplement").glob("*/*.jsonl"))


def test_year_chunk_errors_do_not_turn_into_empty_data(data):
    with pytest.raises(DartAPIError, match="status=100"):
        run(data, {"status": "100"}, {"status": "100"})


def test_020_budget_and_800_stop_pending_and_resume(data):
    result, session = run(data, page([row()]), {"status": "020"})
    assert result["status"] == "quota_reached" and result["state"]["completed_corps"] == [CORP]
    stopped, session = run(data)
    assert stopped["status"] == "quota_reached" and not session.calls
    result, session = run(data, {"status": "800"}, now=NOW + timedelta(days=1))
    assert result["status"] == "provider_maintenance" and result["state"]["completed_corps"] == [CORP]
    result, session = run(data, {"status": "013"}, now=NOW + timedelta(days=1))
    assert result["status"] == "complete" and len(session.calls) == 1


def test_local_daily_budget_is_resumable(data):
    result, _ = run(data, {"status": "013"}, max_requests=1)
    assert result["status"] == "quota_reached" and result["state"]["completed_corps"] == [CORP]
    result, _ = run(data, {"status": "013"}, max_requests=1, now=NOW + timedelta(days=1))
    assert result["status"] == "complete"


def test_main_receipt_is_counted_once_with_current_field_differences(data):
    existing = {**row(), "corp_cls": "Y", "corp_name": "이전명", "stock_code": "000011",
                "flr_nm": "이전제출인", "rm": "이전비고"}
    write_rows(directory(data) / "list/2016/20160602.jsonl", [existing])
    result, _ = run(data, page([row()]), {"status": "013"})
    assert result["state"]["duplicates_per_corp"][CORP] == 1
    assert result["state"]["rows_per_corp"][CORP] == 0
    assert listing_rows(data, "20160602") == [existing]
    assert result["state"]["overlap_differences_per_corp"][CORP] == dict.fromkeys(module.CURRENT_FIELDS, 1)


@pytest.mark.parametrize("field,value", [("corp_code", OTHER), ("rcept_dt", "20160603"),
                                        ("report_nm", "다른 보고서")])
def test_main_overlap_identity_mismatch_is_hard_error(data, field, value):
    existing = {**row(), field: value}
    day = existing["rcept_dt"]
    write_rows(directory(data) / "list" / day[:4] / f"{day}.jsonl", [existing])
    with pytest.raises(ValueError, match=field):
        run(data, page([row()]))
    state = json.loads((directory(data) / "list_supplement/_state.json").read_text())
    assert CORP not in state["completed_corps"]


def test_resume_keeps_frozen_range_and_collection_reference_after_weekly_refresh(data):
    result, _ = run(data, page([row()]), {"status": "013"})
    profile = data / "reference/dart_company/companies.jsonl"
    rows = [json.loads(line) for line in profile.read_text().splitlines()]
    write_rows(profile, [{**r, "corp_name": "주간갱신"} for r in rows])
    # A company delisted after backfill is represented in main and needs no repair.
    write_rows(profile, [*rows, {"corp_code": OTHER, "corp_cls": "E"}])
    write_rows(directory(data) / "list/2018/20181231.jsonl", [row(day="20181231", corp=OTHER, stock="000020")])
    (directory(data) / "_state.json").write_text(json.dumps({"completed_listing_days": ["20160601", "20181231", "20190101"]}))
    resumed, session = run(data)
    assert not session.calls and resumed["state"] == result["state"]
    assert resumed["end_de"] == "20181231"
    status = listing_status(data)
    assert status["supplement_complete"]
    assert status["reference_version_now"] != status["reference_version_at_collection"]


def test_resume_recovers_atomic_publication_without_requesting_completed_rows(data, monkeypatch):
    original = module._write_json
    def crash(path, value):
        if path.name == "_state.json" and value.get("completed_corps") == [CORP]:
            raise OSError("simulated crash")
        original(path, value)
    monkeypatch.setattr(module, "_write_json", crash)
    with pytest.raises(OSError):
        run(data, page([row()]))
    assert (directory(data) / "list_supplement/_pending.json").exists()
    assert module.supplement(data_dir=data)["estimated_calls"]["minimum_long_range_calls"] == 1
    monkeypatch.setattr(module, "_write_json", original)
    result, session = run(data, {"status": "013"})
    assert [p["corp_code"] for p in session.calls] == [OTHER]
    assert result["state"]["rows_per_corp"][CORP] == 1
    assert len(listing_rows(data, "20160602")) == 1


def test_checkpoint_source_changes_and_missing_saved_rows_fail(data):
    run(data, page([row()]), {"status": "013"})
    (directory(data) / "list_supplement/2016/20160602.jsonl").unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        run(data)
    write_rows(directory(data) / "list_supplement/2016/20160602.jsonl", [row()])
    master = data / "reference/corp_codes.csv"
    master.write_text(master.read_text() + "00000006,999990\n")
    with pytest.raises(ValueError, match="supplement_targets_missing"):
        module.supplement(data_dir=data)


def test_mapped_alphanumeric_codes_are_reported_as_mapped_but_not_numeric_targets(data):
    master = data / "reference/corp_codes.csv"
    master.write_text(master.read_text() + "00000005,00088K\n")
    result = module.supplement(data_dir=data)
    assert result["unmapped_stock_days"] == {"999990": 2}
    assert "00000005" not in result["state"]["target_corps"]


def test_historically_traded_current_konex_corp_is_target(data):
    path = data / "market/krx_daily/2016/20160602.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    write_rows(path, rows + [{"stock_code": "000040", "trade_date": "20160602"}])
    result = module.supplement(data_dir=data)
    assert result["state"]["target_corps"] == [CORP, OTHER, "00000004"]
    assert result["current_class_counts"] == {"E": 1, "N": 1, "unknown": 1}


@pytest.mark.parametrize("status,exit_code", [("800", 0), ("020", 0), ("010", 1)])
def test_cli_execute_uses_fake_key_loader_redacted_errors_and_clean_stop(data, monkeypatch, capsys, status, exit_code):
    monkeypatch.setattr(cli, "load_project_env", lambda: None)
    monkeypatch.setenv("DART_API_KEY", KEY)
    actual = module.supplement
    monkeypatch.setattr(cli, "supplement", lambda **k: actual(**k, session=Session({"status": status, "message": KEY}),
        clock=lambda: NOW, sleeper=lambda _: None))
    assert cli.main(["--data-dir", str(data), "--execute"]) == exit_code
    output = capsys.readouterr().out
    assert KEY not in output
    result = json.loads(output)
    assert result["status"] == {"800": "provider_maintenance", "020": "quota_reached", "010": "error"}[status]
