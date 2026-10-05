"""Synthetic OpenDART responses only; actual provider requests are forbidden."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import requests

from src.ingestion import dart_buyback as dart
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_backfill import KST, _write_json
from src.ingestion.storage import write_rows


NOW = datetime(2026, 10, 5, 12, tzinfo=KST)
KEY, CORP = "synthetic-key-not-a-credential", "00000001"


@pytest.fixture(autouse=True)
def forbid_provider(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("BB001 tests may only use an injected fake session")
    monkeypatch.setattr(requests.Session, "get", fail)


@pytest.fixture
def short_range(monkeypatch):
    monkeypatch.setattr(dart, "START", "20160601")
    monkeypatch.setattr(dart, "END", "20160604")


def item(day="20160602", number=1, **changes):
    return {"rcept_no": day + f"{number:06d}", "rcept_dt": day, "corp_code": CORP,
            "stock_code": "000010", "corp_cls": "Y", "corp_name": "시험기업",
            "report_nm": "주요사항보고서(자기주식 취득 결정)", **changes}


def structured(listing=None, **changes):
    listing = listing or item()
    return {"rcept_no": listing["rcept_no"], "corp_code": listing["corp_code"],
            "aqpln_stk_ostk": "1,234", "aqpln_prc_ostk": "12,345,678",
            "aq_mth": "장내 직접 취득", "aqexpd_bgd": "2016.06.03", "aqexpd_edd": "2016.09.02",
            "aq_pp": "주주가치 제고 및 소각", "aq_dd": "2016.05.01", **changes}


def listing_archive(root, *rows):
    for day in sorted({row["rcept_dt"] for row in rows}):
        write_rows(root / "disclosures/dart_full/list" / day[:4] / f"{day}.jsonl",
                   [row for row in rows if row["rcept_dt"] == day])


def zip_body(text="자기주식 취득 결정 보통주 예정수량 1,234 주 예정금액 12,345,678 원 장내 취득", *, encoding="utf-8", files=None):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in (files or {"body.xml": f"<DOCUMENT><P>{text}</P></DOCUMENT>".encode(encoding)}).items():
            archive.writestr(name, value)
    return output.getvalue()


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()

    def json(self):
        if isinstance(self.value, bytes):
            raise ValueError("not JSON")
        return self.value

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs["params"]))
        value = self.handler(url, kwargs["params"])
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def fetch(root, handler, **kwargs):
    session = FakeSession(handler)
    result = dart.fetch(data_dir=root, execute=True, api_key=KEY, session=session,
                        clock=lambda: NOW, sleeper=lambda seconds: None, **kwargs)
    return result, session


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("title,expected", [
    ("주요사항보고서(자기 주식\t취득\n결정)", True), ("자기주식취득결정", True),
    ("자기주식 취득 결정 신탁", False), ("[기재정정]자기주식취득결정", False),
    ("첨부정정 자기주식취득결정", False), ("자기주식취득결정 철회", False),
    ("자기주식취득결정 취소", False), ("자사주취득", False), ("자기주식처분결정", False)])
def test_literal_title_rule_exclusions_win(title, expected):
    assert dart.title_matches(title) is expected


@pytest.mark.parametrize("value,expected", [("장 내 매 수", True), ("장내 및 장외", False),
    ("장내 공개 매수", False), ("장외", False), ("", False)])
def test_method_rule(value, expected):
    assert dart.method_matches(value) is expected


@pytest.mark.parametrize("value,expected", [("취득 후 소각", True), ("소각 계획 없음", False),
    ("소각 예정 없음", False), ("소각하지 않음", False), ("소각 여부 미정", False),
    ("소각 미정", False), ("주주가치 제고", False)])
def test_cancellation_rule_exclusions_win(value, expected):
    assert dart.cancellation_matches(value) is expected


def test_targeted_requests_and_plan_count_distinct_corporation_days(tmp_path, short_range):
    rows = [item(), item(number=2), item("20160603", stock_code=""),
            item("20160604"), item(number=9, corp_code="00000002", corp_cls="E"),
            item("20160603", number=8, corp_code="00000003", report_nm="신탁 자기주식취득결정")]
    listing_archive(tmp_path, *rows)
    candidates = [row for row in rows if dart.title_matches(row["report_nm"])]
    targets = {(row["corp_code"], row["rcept_dt"]) for row in candidates}
    plan = dart.plan(tmp_path)
    assert plan["targeted_corporation_days"] == plan["estimated_structured_requests"] == len(targets) == 4
    assert plan["estimated_document_requests"] == len(candidates) == 5
    assert plan["estimated_requests_still_needed"] == 9 and plan["range_days"] == 1

    def handler(url, params):
        if url.endswith("document.xml"):
            return zip_body()
        assert params["bgn_de"] == params["end_de"]
        return {"status": "000", "list": [structured(row) for row in candidates
            if (row["corp_code"], row["rcept_dt"]) == (params["corp_code"], params["bgn_de"])]}

    result, session = fetch(tmp_path, handler)
    requests = [(params["corp_code"], params["bgn_de"]) for url, params in session.calls
                if not url.endswith("document.xml")]
    assert len(requests) == len(targets) and set(requests) == targets
    assert result["requests_made"] == plan["estimated_requests_still_needed"]
    assert dart.plan(tmp_path)["estimated_requests_still_needed"] == 0


def test_plan_keeps_historical_and_missing_stock_identifiers_and_is_read_only(tmp_path):
    rows = [item(), item("20160603", corp_code="00000002", stock_code=""),
            item("20160604", corp_code="00000003", report_nm="신탁 자기주식취득결정")]
    listing_archive(tmp_path, *rows)
    before = snapshot(tmp_path)
    plan = dart.plan(tmp_path)
    dry = dart.fetch(data_dir=tmp_path)
    assert plan["corp_codes"] == [CORP, "00000002"]
    assert plan["counts"]["title_candidates"] == 2
    assert plan["listing_coverage"]["stored_days"] == 3
    assert plan["estimated_document_requests"] == 2
    assert plan["estimated_structured_requests"] == 2
    assert plan["estimated_requests_still_needed"] == 4
    assert dry["requests_made"] == 0 and snapshot(tmp_path) == before


@pytest.mark.parametrize("encoding", ["utf-8", "cp949", "euc-kr"])
def test_fetch_original_rows_full_plain_text_hashes_and_resume(tmp_path, short_range, encoding):
    row = item()
    listing_archive(tmp_path, row)
    blob = zip_body("주요사항 자기주식취득결정 보통주 예정수량 1,234 주 예정금액 12,345,678 원 " + "본문내용 " * 2000, encoding=encoding)
    handler = lambda url, params: blob if url.endswith("document.xml") else {"status": "000", "list": [structured(row)]}
    result, session = fetch(tmp_path, handler)
    archive = dart.structured_record(tmp_path, CORP)
    document = dart.document_record(tmp_path, row["rcept_no"])
    assert result["requests_made"] == 2 and archive["complete"]
    assert archive["rows"][row["rcept_no"]]["row_sha256"] == dart.row_digest(structured(row))
    assert document["verification"]["verified"] and len(document["text"]) > 2500
    assert document["raw_sha256"] == hashlib.sha256(blob).hexdigest()
    assert document["fetched_at"] == NOW.isoformat()
    assert session.calls[-1][1]["rcept_no"] == row["rcept_no"]
    assert not list(tmp_path.rglob("*.zip"))
    again, empty = fetch(tmp_path, lambda *args: pytest.fail("completed requests must not repeat"))
    assert again["requests_made"] == 0 and not empty.calls
    assert dart.plan(tmp_path)["estimated_requests_still_needed"] == 0


@pytest.mark.parametrize("text,quantity,amount", [
    ("보통주 1, 234 주 금액 12,345, 678 원", True, True),
    ("보통주 1,234 주 금액 12,345,679 원", True, False),
    ("보통주 91,234 주 금액 12,345,678 원", False, True),
    ("숫자 없음", False, False)])
def test_original_digit_verification_has_no_substitution(text, quantity, amount):
    result = dart.verify_values(structured(), text)
    assert result["quantity_present"] is quantity and result["amount_present"] is amount
    assert result["verified"] is (quantity and amount)


def test_document_prefers_original_receipt_over_larger_old_attachment(tmp_path, short_range):
    row = item()
    listing_archive(tmp_path, row)
    blob = zip_body(files={row["rcept_no"] + ".xml": "<DOCUMENT>원문 보통주 1,234 주 예정금액 100 원</DOCUMENT>".encode(),
        "20160601000001.xml": ("<DOCUMENT>정정 보통주 1,234 주 예정금액 12,345,678 원 " + "첨부내용 " * 200 + "</DOCUMENT>").encode()})
    fetch(tmp_path, lambda url, params: blob if url.endswith("document.xml") else {"status": "000", "list": [structured(row)]})
    document = dart.document_record(tmp_path, row["rcept_no"])
    assert "첨부내용" not in document["text"] and not document["verification"]["verified"]


@pytest.mark.parametrize("status", ["013", "014"])
@pytest.mark.parametrize("document_xml", [True, False])
def test_official_missing_statuses_are_per_item_and_not_whole_run_errors(tmp_path, short_range, status, document_xml):
    row = item()
    listing_archive(tmp_path, row)
    missing_doc = f"<result><status>{status}</status></result>".encode() if document_xml else {"status": status}
    result, session = fetch(tmp_path, lambda url, params: missing_doc if url.endswith("document.xml") else {"status": status})
    assert result["status"] == "ok" and len(session.calls) == 2
    archive = dart.structured_record(tmp_path, CORP)
    assert archive["complete"] and archive["rows"] == {}
    assert len(archive["requests"]) == 1
    request = archive["requests"][0]
    assert request["bgn_de"] == request["end_de"] == row["rcept_dt"]
    assert request["payload"]["status"] == status
    assert request["payload_sha256"] == dart.row_digest(request["payload"])
    document = dart.document_record(tmp_path, row["rcept_no"])
    assert document["status"] == status and document["text"] == "" and not document["verification"]["verified"]
    assert document["verification"]["structured_row_sha256"] is None
    again, empty = fetch(tmp_path, lambda *args: pytest.fail("recorded missing rows must not be probed again"))
    assert again["requests_made"] == 0 and not empty.calls
    assert dart.plan(tmp_path)["estimated_requests_still_needed"] == 0


@pytest.mark.parametrize("limited_stage", ["structured", "document"])
def test_020_stops_immediately_and_persists_shared_provider_limit(tmp_path, short_range, limited_stage):
    listing_archive(tmp_path, item())
    def handler(url, params):
        if limited_stage == "structured" or url.endswith("document.xml"):
            return {"status": "020"}
        return {"status": "000", "list": [structured()]}
    result, session = fetch(tmp_path, handler)
    assert result["status"] == "quota_reached" and len(session.calls) == (1 if limited_stage == "structured" else 2)
    quota = json.loads((tmp_path / "disclosures/dart_full/_quota.json").read_text())["days"]["2026-10-05"]
    assert quota["provider_limited"] and quota["requests"] == len(session.calls)
    resumed, fake = fetch(tmp_path, lambda *args: pytest.fail("020 must prevent another request today"))
    assert resumed["status"] == "quota_reached" and resumed["requests_made"] == 0 and not fake.calls


def test_shared_quota_reserved_by_backfill_limits_this_collector(tmp_path, short_range):
    listing_archive(tmp_path, item())
    _write_json(tmp_path / "disclosures/dart_full/_quota.json", {"days": {"2026-10-05": {"requests": 9, "provider_limited": False}}})
    result, session = fetch(tmp_path, lambda url, params: {"status": "000", "list": [structured()]}, max_requests=10)
    assert result["reason"] == "daily_request_budget" and len(session.calls) == 1
    resumed, session = fetch(tmp_path, lambda url, params: zip_body(), max_requests=11)
    assert resumed["requests_made"] == 1 and len(session.calls) == 1 and session.calls[0][0].endswith("document.xml")


def test_detects_and_fetches_pages_before_publishing_complete_rows(tmp_path, short_range):
    first, second = item(), item(number=2)
    listing_archive(tmp_path, first, second)
    def handler(url, params):
        if url.endswith("document.xml"):
            return zip_body()
        row = first if params["page_no"] == 1 else second
        return {"status": "000", "page_no": params["page_no"], "total_page": 2, "total_count": 2, "list": [structured(row)]}
    result, session = fetch(tmp_path, handler)
    assert result["requests_made"] == 4
    assert set(dart.structured_record(tmp_path, CORP)["rows"]) == {first["rcept_no"], second["rcept_no"]}
    assert [params["page_no"] for url, params in session.calls if not url.endswith("document.xml")] == [1, 2]


def test_rejected_exact_day_fails_without_complete_archive(tmp_path, short_range):
    row = item()
    listing_archive(tmp_path, row)
    session = FakeSession(lambda url, params: {"status": "100", "message": "조회기간 제한"})
    with pytest.raises(DartAPIError, match="status=100"):
        dart.fetch(data_dir=tmp_path, execute=True, api_key=KEY, session=session,
                   clock=lambda: NOW, sleeper=lambda seconds: None)
    assert len(session.calls) == 1
    assert session.calls[0][1]["bgn_de"] == session.calls[0][1]["end_de"] == row["rcept_dt"]
    archive = dart.structured_record(tmp_path, CORP)
    assert archive is None or not archive["complete"]


def test_unpaged_single_day_saturation_fails(tmp_path, short_range, monkeypatch):
    monkeypatch.setattr(dart, "SATURATION_LIMIT", 2)
    first, second = item(), item(number=2)
    listing_archive(tmp_path, first, second)
    with pytest.raises(DartAPIError, match="single-day"):
        fetch(tmp_path, lambda url, params: {"status": "000", "list": [structured(first), structured(second)]})
    assert not dart.structured_record(tmp_path, CORP)["complete"]


def test_other_receipt_on_target_day_is_stored_never_substituted(tmp_path, short_range):
    original = item()
    correction = item(number=2)
    listing_archive(tmp_path, original)
    def handler(url, params):
        if url.endswith("document.xml"):
            return zip_body()
        return {"status": "000", "list": [structured(correction)]}
    result, session = fetch(tmp_path, handler)
    assert result["status"] == "ok" and len(session.calls) == 2
    assert session.calls[0][1]["bgn_de"] == session.calls[0][1]["end_de"] == original["rcept_dt"]
    archive = dart.structured_record(tmp_path, CORP)
    assert archive["complete"] and set(archive["rows"]) == {correction["rcept_no"]}
    assert archive["rows"][correction["rcept_no"]]["row"] == structured(correction)
    assert dart.document_record(tmp_path, correction["rcept_no"]) is None
    verification = dart.document_record(tmp_path, original["rcept_no"])["verification"]
    assert not verification["verified"] and verification["structured_row_sha256"] is None


@pytest.mark.parametrize("bad", ["wrong_corp", "wrong_range", "invalid_receipt", "conflicting_rows", "incomplete_page", "bad_paging", "count_mismatch", "bad_status"])
def test_broken_provider_assumptions_fail_without_complete_archive(tmp_path, short_range, bad):
    listing_archive(tmp_path, item())
    def handler(url, params):
        if bad == "wrong_corp":
            return {"status": "000", "list": [structured(corp_code="00000002")]}
        if bad == "wrong_range":
            return {"status": "000", "list": [structured(rcept_dt="20160101")]}
        if bad == "invalid_receipt":
            return {"status": "000", "list": [structured(rcept_no="not-a-receipt")]}
        if bad == "conflicting_rows":
            return {"status": "000", "list": [structured(), structured(aqpln_stk_ostk="555")]}
        if bad == "incomplete_page":
            return {"status": "000", "page_no": 1, "total_page": 2, "total_count": 2, "list": [structured()]} if params["page_no"] == 1 else {"status": "013"}
        if bad == "bad_paging":
            return {"status": "000", "page_no": 2, "total_page": 2, "total_count": 1, "list": [structured()]}
        if bad == "count_mismatch":
            return {"status": "000", "page_no": 1, "total_page": 1, "total_count": 2, "list": [structured()]}
        return {"status": "010", "message": KEY}
    with pytest.raises(DartAPIError) as error:
        fetch(tmp_path, handler)
    assert KEY not in str(error.value)
    archive = dart.structured_record(tmp_path, CORP)
    assert archive is None or not archive["complete"]


def test_transport_failure_reserves_quota_and_resumes_checkpoint(tmp_path, short_range):
    listing_archive(tmp_path, item())
    with pytest.raises(DartAPIError, match="transport"):
        fetch(tmp_path, lambda url, params: TimeoutError(KEY))
    quota = json.loads((tmp_path / "disclosures/dart_full/_quota.json").read_text())["days"]["2026-10-05"]
    assert quota["requests"] == 1
    result, _ = fetch(tmp_path, lambda url, params: zip_body() if url.endswith("document.xml") else {"status": "000", "list": [structured()]})
    assert result["status"] == "ok" and result["requests_made"] == 2


@pytest.mark.parametrize("broken", ["row_hash", "payload_hash", "text_hash", "listing_date"])
def test_corrupt_saved_sources_are_not_silently_replaced(tmp_path, short_range, broken):
    row = item()
    listing_archive(tmp_path, row)
    fetch(tmp_path, lambda url, params: zip_body() if url.endswith("document.xml") else {"status": "000", "list": [structured()]})
    if broken == "listing_date":
        path = tmp_path / "disclosures/dart_full/list/2016/20160602.jsonl"
        write_rows(path, [{**row, "rcept_dt": "20160603"}])
    else:
        path = dart._path(tmp_path, "documents" if broken == "text_hash" else "structured", row["rcept_no"] if broken == "text_hash" else CORP)
        data = json.loads(path.read_text())
        if broken == "text_hash":
            data["text"] += "mutated"
        elif broken == "row_hash":
            data["rows"][row["rcept_no"]]["row"]["aqpln_stk_ostk"] = "999"
        else:
            data["requests"][0]["payload"]["message"] = "mutated"
        _write_json(path, data)
    with pytest.raises(ValueError):
        dart.plan(tmp_path)


def test_execution_requires_explicit_key_and_valid_quota(tmp_path):
    with pytest.raises(ValueError, match="key"):
        dart.fetch(data_dir=tmp_path, execute=True)
    with pytest.raises(ValueError, match="positive"):
        dart.fetch(data_dir=tmp_path, max_requests=0)


def test_receipt_number_prefix_is_not_the_publication_date(tmp_path, short_range):
    row = item(rcept_no="20160531000001")
    listing_archive(tmp_path, row)
    listings, _, counts = dart.load_listings(tmp_path)
    assert listings[0]["rcept_dt"] == "20160602"
    assert counts["receipt_prefix_differs_from_public_date"] == 1
    result, session = fetch(tmp_path, lambda url, params: zip_body() if url.endswith("document.xml") else {"status": "000", "list": [structured(row)]})
    assert result["status"] == "ok" and row["rcept_no"] in dart.structured_record(tmp_path, CORP)["rows"]
    assert session.calls[-1][1]["rcept_no"] == "20160531000001"
