import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from threading import Barrier
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import requests

from scripts.data import dart_business_text as cli
from src.ingestion import dart_business_text as module
from src.ingestion.dart import DartDisclosureCollector
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_backfill import KST, backfill
from src.ingestion.dart_business_text import (
    EXTRACTION_VERSION, BusinessTextCollector, extract_sections,
    load_business_text, periodic_reports, select_report,
)
from src.ingestion.storage import write_rows


KEY = "fixture-private-key"
STOCK = "005930"
NOW = datetime(2026, 10, 5, 10, tzinfo=KST)
OVERVIEW = "당사는 반도체를 제조하며 신규 공정의 생산능력을 확대하고 있습니다."
PRODUCTS = "메모리 반도체와 시스템 반도체를 공급합니다."
PLAIN = f"II. 사업의 내용 1. 사업의 개요 {OVERVIEW} 2. 주요 제품 및 서비스 {PRODUCTS} 3. 원재료 및 생산설비 제외본문 III. 재무에 관한 사항 제외재무"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Must not read environment files or create real sessions")

    monkeypatch.setattr("requests.Session", denied)
    monkeypatch.setattr("src.config.settings.load_project_env", denied)
    monkeypatch.setattr(cli, "load_project_env", denied)
    monkeypatch.setattr(module, "_now", lambda: NOW)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)


def row(day, title="분기보고서 (2026.03)", number=1, stock=STOCK):
    return {"rcept_no": f"{day}{number:06d}", "corp_code": "00126380",
            "corp_name": "시험기업", "stock_code": stock, "corp_cls": "Y",
            "report_nm": title, "rcept_dt": day}


def listings(data_dir, *rows):
    by_day = {}
    for report in rows:
        by_day.setdefault(report["rcept_dt"], []).append(report)
    for day, values in by_day.items():
        write_rows(data_dir / "disclosures" / "dart_full" / "list" / day[:4] / f"{day}.jsonl", values)


def saved_path(data_dir, receipt):
    return data_dir / "disclosures" / "business_text" / f"{receipt}.json"


def save_sections(data_dir, report, overview=OVERVIEW, products=PRODUCTS, omitted=False, **changes):
    value = {"rcept_no": report["rcept_no"], "overview": overview, "products": products,
             "omitted": omitted, "extraction_version": EXTRACTION_VERSION, "status": "000",
             "raw_sha256": "a" * 64, "byte_size": 1024, "fetched_at": NOW.isoformat(), **changes}
    path = saved_path(data_dir, report["rcept_no"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def snapshot(data_dir):
    return {path.relative_to(data_dir): path.read_bytes() for path in data_dir.rglob("*") if path.is_file()}


def zip_document(text=PLAIN, encoding="utf-8", files=None):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        if files is None:
            archive.writestr("body.xml", f"<DOCUMENT><BODY>{escape(text)}</BODY></DOCUMENT>".encode(encoding))
        else:
            for name, content in files.items():
                archive.writestr(name, content)
    return output.getvalue()


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.content = value if isinstance(value, bytes) else json.dumps(value).encode()

    def json(self):
        if isinstance(self.value, bytes):
            raise ValueError("not JSON")
        return self.value

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            pytest.fail("Unexpected request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def fetch(data_dir, receipts, *responses, max_requests=100):
    session = FakeSession(*responses)
    result = BusinessTextCollector(KEY, session).fetch(receipts, data_dir, max_requests, execute=True)
    return result, session


def quota(data_dir):
    path = data_dir / "disclosures" / "dart_full" / "_quota.json"
    return json.loads(path.read_text())["days"]


def test_periodic_reports_filter_types_stock_and_exclusive_cutoff(tmp_path):
    eligible = [row("20260320", "사업보고서 (2025.12)"),
                row("20260515"), row("20260520", "[기재정정] 분기보고서 (2026.03)"),
                row("20260521", "[첨부정정]분기보고서 (2026.03)"),
                row("20260814", "반기보고서 (2026.06)")]
    excluded = [row("20260820"), row("20260821"), row("20260720", stock="123450")]
    excluded += [row("20260320", name, number=n) for n, name in enumerate([
        "연결감사보고서 (2025.12)", "[기재정정]별도감사보고서 (2025.12)",
        "주요사항보고서(전환사채권발행결정)", "사업보고서 제출기한 연장 신고서 (2025.12)"], 2)]
    listings(tmp_path, *eligible, *excluded)
    reports = periodic_reports(STOCK, "2026-08-20", tmp_path)
    assert [item["rcept_no"] for item in reports] == [item["rcept_no"] for item in eligible[::-1]]
    assert [item["period"] for item in reports] == ["2026.06", "2026.03", "2026.03", "2026.03", "2025.12"]


@pytest.mark.parametrize("decision,expected", [("20260520", "20260515"), ("2026-05-29", "20260520"),
                                              ("2026-06-02", "20260601")])
def test_corrections_use_only_the_latest_version_before_the_decision(tmp_path, decision, expected):
    reports = [row("20260515"), row("20260520", "[기재정정]분기보고서 (2026.03)"),
               row("20260601", "[첨부정정]분기보고서 (2026.03)")]
    listings(tmp_path, *reports)
    for report in reports:
        save_sections(tmp_path, report, overview=report["rcept_dt"], products=None)
    assert select_report(STOCK, decision, tmp_path)["rcept_dt"] == expected
    result = load_business_text(STOCK, decision, tmp_path)
    assert result["text"] == expected and result["rcept_dt"] == expected and not result["fallback_used"]


def test_report_period_takes_precedence_over_a_late_older_period_correction(tmp_path):
    current = row("20260814", "반기보고서 (2026.06)")
    older = row("20260819", "[기재정정]분기보고서 (2026.03)")
    listings(tmp_path, current, older)
    assert periodic_reports(STOCK, "20260820", tmp_path)[0]["rcept_no"] == older["rcept_no"]
    assert select_report(STOCK, "20260820", tmp_path)["rcept_no"] == current["rcept_no"]


def test_same_day_versions_have_a_deterministic_receipt_tiebreak(tmp_path):
    first, second = row("20260520"), row("20260520", "[기재정정]분기보고서 (2026.03)", number=2)
    listings(tmp_path, second, first)
    assert select_report(STOCK, "20260521", tmp_path)["rcept_no"] == second["rcept_no"]


@pytest.mark.parametrize("heading", ["2. 주요 제품 및 서비스", "2. 주요 제품 등의 현황",
                                    "주요 제품 및 서비스", "주요 제품 등의 현황", "2) 주요제품 등 현황"])
@pytest.mark.parametrize("chapter", ["II.", "Ⅱ."])
def test_heading_variants_and_subsection_boundaries(chapter, heading):
    text = f"I. 회사의 개요 1. 사업의 개요 무관 {chapter} 사업의 내용 1. 사업의 개요 {OVERVIEW} {heading} {PRODUCTS} 3. 원재료 및 생산설비 제외 III. 재무에 관한 사항 제외"
    assert extract_sections(text) == {"overview": OVERVIEW, "products": PRODUCTS, "omitted": False}


@pytest.mark.parametrize("xml", [True, False])
def test_markup_preserves_korean_and_plain_table_rows(xml):
    text = """<DOCUMENT><STYLE>숨겨진 스타일</STYLE><BODY>
      <TITLE>Ⅱ. 사업의 내용</TITLE><SECTION-2><TITLE>1. 사업의 개요</TITLE>
      <P>당사는   반도체를&nbsp; 제조합니다.</P><P>신규 공정을 확대합니다.</P></SECTION-2>
      <TITLE>2. 주요 제품 등의 현황</TITLE>
      <TABLE><TR><TH>제품</TH><TH>매출 비중</TH></TR>
      <TR><TD><P>메모리</P></TD><TD>80%</TD></TR><TR><TD>시스템</TD><TD>20%</TD></TR></TABLE>
      <TITLE>3. 원재료 및 생산설비</TITLE><P>제외본문</P>
      <TITLE>Ⅲ. 재무에 관한 사항</TITLE><P>제외재무</P></BODY></DOCUMENT>"""
    if xml:
        text = text.replace("&nbsp;", "&#160;")
    else:
        text = text.lower().replace("document", "html").replace("title", "h2").replace("section-2", "div")
    result = extract_sections(text)
    assert result == {"overview": "당사는 반도체를 제조합니다.\n신규 공정을 확대합니다.",
                      "products": "제품 매출 비중\n메모리 80%\n시스템 20%", "omitted": False}


def test_table_of_contents_is_not_used_as_the_business_chapter():
    toc = "II. 사업의 내용 1. 사업의 개요 -----5 2. 주요 제품 및 서비스 -----6 III. 재무에 관한 사항 -----7\n"
    assert extract_sections(toc + PLAIN) == {"overview": OVERVIEW, "products": PRODUCTS, "omitted": False}


def test_extractor_accepts_dart_py_normalized_plain_text():
    collector = object.__new__(DartDisclosureCollector)
    xml = "<DOCUMENT><TITLE>II. 사업의 내용</TITLE><TITLE>1. 사업의 개요</TITLE>" + \
          f"<P>{OVERVIEW}</P><TITLE>2. 주요 제품 및 서비스</TITLE><P>{PRODUCTS}</P></DOCUMENT>"
    flattened = collector._normalize_inner_document_text("body.xml", xml)
    assert "\n" not in flattened
    assert extract_sections(flattened) == {"overview": OVERVIEW, "products": PRODUCTS, "omitted": False}


def test_numbered_table_rows_do_not_end_a_numbered_subsection():
    xml = """<DOCUMENT><TITLE>II. 사업의 내용</TITLE><TITLE>1. 사업의 개요</TITLE>
        <P>당사는 반도체를 제조합니다.</P>
        <TABLE><TR><TD>1. 제조</TD><TD>국내</TD></TR><TR><TD>2. 판매</TD><TD>해외</TD></TR></TABLE>
        <P>신규 사업도 추진합니다.</P><TITLE>2. 주요 제품 및 서비스</TITLE>
        <TABLE><TR><TD>3. 메모리</TD><TD>80%</TD></TR></TABLE>
        <TITLE>3. 원재료 및 생산설비</TITLE><P>제외</P></DOCUMENT>"""
    assert extract_sections(xml) == {"overview": "당사는 반도체를 제조합니다.\n1. 제조 국내\n2. 판매 해외\n신규 사업도 추진합니다.",
                                     "products": "3. 메모리 80%", "omitted": False}


def test_short_table_of_contents_page_numbers_are_not_business_text():
    toc = "II. 사업의 내용 1. 사업의 개요 -----5 2. 주요 제품 및 서비스 -----6 III. 재무에 관한 사항 -----7"
    assert extract_sections(toc) == {"overview": None, "products": None, "omitted": False}


@pytest.mark.parametrize("phrase", ["기재를 생략합니다.", "사업보고서를 참조하시기 바랍니다.",
                                   "2025년 사업보고서(2025.12)를 참조하시기 바랍니다."])
def test_overview_omission_is_detected(phrase):
    result = extract_sections(f"Ⅱ. 사업의 내용 1. 사업의 개요 {phrase} 2. 주요 제품 및 서비스 제품")
    assert result["overview"] == phrase and result["omitted"]


def test_a_product_omission_does_not_discard_a_valid_overview():
    result = extract_sections(f"II. 사업의 내용 1. 사업의 개요 {OVERVIEW} 2. 주요 제품 및 서비스 기재를 생략합니다.")
    assert result == {"overview": OVERVIEW, "products": None, "omitted": False}


@pytest.mark.parametrize("text", ["", "1. 사업의 개요 다른 자료입니다.", "I. 회사의 개요 1. 사업의 개요 무관",
                                 "II. 사업의 내용 III. 재무에 관한 사항 1. 사업의 개요 무관"])
def test_missing_business_overview_is_explicit(text):
    assert extract_sections(text) == {"overview": None, "products": None, "omitted": False}


@pytest.mark.parametrize("title,overview,omitted", [
    ("분기보고서 (2026.03)", "사업보고서를 참조하시기 바랍니다.", True),
    ("반기보고서 (2026.06)", None, False),
])
def test_annual_fallback_uses_the_latest_eligible_filed_annual(tmp_path, title, overview, omitted):
    annual = row("20260320", "사업보고서 (2025.12)")
    corrected = row("20260420", "[기재정정]사업보고서 (2025.12)")
    future = row("20260901", "[첨부정정]사업보고서 (2025.12)")
    periodic = row("20260814", title)
    listings(tmp_path, annual, corrected, future, periodic)
    save_sections(tmp_path, annual, overview="구본")
    save_sections(tmp_path, corrected, overview="결정일 전 연간 정정본", products="연간 제품")
    save_sections(tmp_path, future, overview="미래본문")
    save_sections(tmp_path, periodic, overview=overview, products="분기 제품", omitted=omitted)
    before = snapshot(tmp_path)
    result = load_business_text(STOCK, "20260820", tmp_path)
    assert result["text"] == "결정일 전 연간 정정본\n[주요 제품 및 서비스]\n연간 제품"
    assert result["rcept_no"] == corrected["rcept_no"] and result["report_nm"] == corrected["report_nm"]
    assert result["period"] == "2025.12" and result["rcept_dt"] == "20260420" and result["fallback_used"]
    assert snapshot(tmp_path) == before


def test_missing_selected_cache_can_use_only_the_eligible_annual_fallback(tmp_path):
    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic)
    save_sections(tmp_path, annual)
    result = load_business_text(STOCK, "20260529", tmp_path)
    assert result["fallback_used"] and result["rcept_no"] == annual["rcept_no"]


def test_no_text_is_not_replaced_with_other_reports_or_products(tmp_path):
    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic)
    save_sections(tmp_path, periodic, overview=None, products="제품만 있는 본문")
    assert load_business_text(STOCK, "20260529", tmp_path) is None
    save_sections(tmp_path, annual, overview=None)
    assert load_business_text(STOCK, "20260529", tmp_path) is None
    assert load_business_text(STOCK, "20250101", tmp_path) is None


def test_character_limits_hash_and_metadata_cover_exact_returned_text(tmp_path):
    report = row("20260515")
    listings(tmp_path, report)
    save_sections(tmp_path, report, overview="개" * 3001, products="품" * 1001)
    expected = "개" * 3000 + "\n[주요 제품 및 서비스]\n" + "품" * 1000
    result = load_business_text(STOCK, "20260529", tmp_path)
    assert result == {"text": expected, "report_nm": report["report_nm"], "rcept_no": report["rcept_no"],
                      "rcept_dt": "20260515", "period": "2026.03", "fallback_used": False,
                      "chars": len(expected), "sha256": sha256(expected.encode()).hexdigest(),
                      "extraction_version": EXTRACTION_VERSION}


def test_no_product_section_adds_no_product_marker(tmp_path):
    report = row("20260515")
    listings(tmp_path, report)
    save_sections(tmp_path, report, products=None)
    assert load_business_text(STOCK, "20260529", tmp_path)["text"] == OVERVIEW


def test_plan_deduplicates_receipts_and_includes_potential_fallback(tmp_path):
    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic, row("20260601", "[기재정정]분기보고서 (2026.03)"))
    save_sections(tmp_path, annual)
    before = snapshot(tmp_path)
    result = BusinessTextCollector(None).plan([(STOCK, "20260529"), (STOCK, "20260528"), ("123450", "20260529")], tmp_path)
    assert (result["needed"], result["stored"], result["missing"], result["estimated_requests"]) == (2, 1, 1, 1)
    assert result["needed_rcept_nos"] == [periodic["rcept_no"], annual["rcept_no"]]
    assert result["missing_rcept_nos"] == [periodic["rcept_no"]]
    assert result["unavailable_requests"] == [{"stock_code": "123450", "decision_date": "2026-05-29"}]
    assert snapshot(tmp_path) == before


def test_plan_skips_unneeded_annual_when_selected_overview_is_stored(tmp_path):
    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic)
    save_sections(tmp_path, periodic)
    result = BusinessTextCollector(None).plan([(STOCK, "20260529")], tmp_path)
    assert result["needed_rcept_nos"] == result["stored_rcept_nos"] == [periodic["rcept_no"]]
    assert result["missing"] == result["estimated_requests"] == 0


def test_plan_applies_each_requests_cutoff_and_supports_alphanumeric_codes(tmp_path):
    original, correction = row("20260515", stock="0123A0"), row("20260601", "[기재정정]분기보고서 (2026.03)", stock="0123A0")
    listings(tmp_path, original, correction)
    result = BusinessTextCollector(None).plan([("0123A0", "20260529"), ("0123A0", "20260602")], tmp_path)
    assert result["needed_rcept_nos"] == [correction["rcept_no"], original["rcept_no"]]
    assert select_report("0123A0", "20260529", tmp_path)["rcept_no"] == original["rcept_no"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949", "euc-kr"])
def test_fetch_accepts_the_existing_dart_fixture_shape_without_truncation(tmp_path, encoding):
    receipt = row("20260515")["rcept_no"]
    overview = "신사업과 가격 결정력을 설명합니다. " * 250
    text = "I. 회사의 개요 " + "회사연혁 " * 1000 + f"II. 사업의 내용 1. 사업의 개요 {overview} 2. 주요 제품 및 서비스 {PRODUCTS} III. 재무에 관한 사항 제외"
    blob = zip_document(text, encoding)
    result, session = fetch(tmp_path, [receipt, receipt], blob)
    record = json.loads(saved_path(tmp_path, receipt).read_text())
    assert result["requests_made"] == result["saved"] == 1 and result["remaining"] == 0
    assert len(session.calls) == 1 and session.calls[0][0] == DartDisclosureCollector.DOCUMENT_URL
    assert record["overview"] == overview.strip() and len(record["overview"]) > 3000
    assert record["raw_sha256"] == sha256(blob).hexdigest() and record["byte_size"] == len(blob)
    assert record["extraction_version"] == EXTRACTION_VERSION and record["fetched_at"] == NOW.isoformat()
    assert record["products"] == PRODUCTS and record["status"] == "000"
    assert not list(tmp_path.rglob("*.zip"))
    repeated, idle = fetch(tmp_path, [receipt])
    assert repeated["requests_made"] == 0 and idle.calls == []


def test_fetch_prefers_business_body_over_toc_and_larger_financial_attachment(tmp_path):
    toc = "목 차 I. 회사의 개요 -----1 Ⅱ. 사업의 내용 -----2 Ⅲ. 재무에 관한 사항 -----3 " * 10
    business = ".xforms * { font-family: 돋움체; } " + PLAIN
    blob = zip_document(files={"toc.xml": f"<DOCUMENT>{toc}</DOCUMENT>".encode(),
                               "financial.xml": ("<DOCUMENT>재무제표 " + "매출액 " * 5000 + "</DOCUMENT>").encode(),
                               "body.html": f"<html><style>숨김 CSS</style><body>{business}</body></html>".encode("cp949"),
                               "image.png": b"not text"})
    receipt = row("20260515")["rcept_no"]
    fetch(tmp_path, [receipt], blob)
    record = json.loads(saved_path(tmp_path, receipt).read_text())
    assert record["overview"] == OVERVIEW and record["products"] == PRODUCTS
    assert "font-family" not in record["overview"]


def test_fetch_accepts_a_body_that_also_contains_a_large_table_of_contents(tmp_path):
    receipt = row("20260515")["rcept_no"]
    toc = "목 차 I. 회사의 개요 -----1 Ⅱ. 사업의 내용 -----2 Ⅲ. 재무에 관한 사항 -----3 " * 10
    fetch(tmp_path, [receipt], zip_document(toc + PLAIN))
    record = json.loads(saved_path(tmp_path, receipt).read_text())
    assert record["overview"] == OVERVIEW and record["products"] == PRODUCTS


def test_a_receipt_named_omitted_main_report_is_not_replaced_by_an_older_attachment(tmp_path):
    receipt = row("20260515")["rcept_no"]
    omitted = "II. 사업의 내용 1. 사업의 개요 기재를 생략합니다. 사업보고서를 참조하시기 바랍니다."
    blob = zip_document(files={f"{receipt}.xml": f"<DOCUMENT>{omitted}</DOCUMENT>".encode(),
                               "annual_attachment.xml": f"<DOCUMENT>{PLAIN}</DOCUMENT>".encode()})
    fetch(tmp_path, [receipt], blob)
    record = json.loads(saved_path(tmp_path, receipt).read_text())
    assert record["omitted"] and "기재를 생략" in record["overview"]


@pytest.mark.parametrize("status", ["013", "014"])
@pytest.mark.parametrize("xml", [True, False])
def test_explicit_no_data_and_missing_file_are_terminal_receipt_records(tmp_path, status, xml):
    receipt = row("20260515")["rcept_no"]
    response = f"<result><status>{status}</status><message>{KEY}</message></result>".encode() if xml else {"status": status, "message": KEY}
    result, session = fetch(tmp_path, [receipt], response)
    record = json.loads(saved_path(tmp_path, receipt).read_text())
    assert result["status"] == "ok" and result["saved"] == result["errors"] == 1
    assert record["status"] == status and record["overview"] is record["products"] is None
    assert record["raw_sha256"] == sha256(FakeResponse(response).content).hexdigest()
    assert KEY not in saved_path(tmp_path, receipt).read_text()
    again, idle = fetch(tmp_path, [receipt])
    assert again["requests_made"] == 0 and len(session.calls) == 1 and idle.calls == []


def test_no_data_receipt_can_plan_and_use_an_annual_fallback(tmp_path):
    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic)
    save_sections(tmp_path, annual)
    fetch(tmp_path, [periodic["rcept_no"]], {"status": "014"})
    plan = BusinessTextCollector(None).plan([(STOCK, "20260529")], tmp_path)
    assert plan["needed"] == plan["stored"] == 2 and plan["missing"] == 0
    assert load_business_text(STOCK, "20260529", tmp_path)["fallback_used"]


@pytest.mark.parametrize("xml", [True, False])
def test_020_stops_and_blocks_both_collectors_until_next_kst_day(tmp_path, monkeypatch, xml):
    receipts = [row("20260515", number=n)["rcept_no"] for n in (1, 2)]
    response = b"<result><status>020</status></result>" if xml else {"status": "020"}
    result, session = fetch(tmp_path, receipts, response)
    assert result["status"] == "quota_reached" and result["reason"] == "provider_status_020"
    assert result["requests_made"] == 1 and result["saved"] == 0 and result["remaining"] == 2
    assert quota(tmp_path)[NOW.date().isoformat()] == {"requests": 1, "provider_limited": True}
    blocked, idle = fetch(tmp_path, receipts)
    assert blocked["requests_made"] == 0 and idle.calls == [] and len(session.calls) == 1
    backfill_session = FakeSession()
    shared = backfill("20250101", "20250101", execute=True, api_key=KEY, stage="list", data_dir=tmp_path,
                      session=backfill_session, clock=lambda: NOW, sleeper=lambda _: None)
    assert shared["reason"] == "provider_status_020" and backfill_session.calls == []
    monkeypatch.setattr(module, "_now", lambda: NOW + timedelta(days=1))
    resumed, _ = fetch(tmp_path, receipts, zip_document(), zip_document())
    assert resumed["status"] == "ok" and resumed["requests_made"] == 2
    assert quota(tmp_path)[(NOW + timedelta(days=1)).date().isoformat()]["requests"] == 2


def test_quota_budget_is_shared_in_both_directions_with_backfill(tmp_path):
    listing_session = FakeSession({"status": "013"}, {"status": "013"})
    listed = backfill("20250101", "20250101", execute=True, api_key=KEY, stage="list", data_dir=tmp_path,
                      max_requests=3, session=listing_session, clock=lambda: NOW, sleeper=lambda _: None)
    assert listed["requests_made"] == 2
    receipts = [row("20260515", number=n)["rcept_no"] for n in (1, 2)]
    result, session = fetch(tmp_path, receipts, zip_document(), max_requests=3)
    assert result["status"] == "quota_reached" and result["requests_made"] == result["saved"] == 1
    assert len(session.calls) == 1 and result["remaining"] == 1
    assert quota(tmp_path)[NOW.date().isoformat()]["requests"] == 3
    idle = FakeSession()
    blocked = backfill("20250102", "20250102", execute=True, api_key=KEY, stage="list", data_dir=tmp_path,
                       max_requests=3, session=idle, clock=lambda: NOW, sleeper=lambda _: None)
    assert blocked["reason"] == "daily_request_budget" and idle.calls == []


def test_concurrent_fetches_respect_the_persisted_shared_cap(tmp_path):
    barrier = Barrier(2)

    def collect(number):
        barrier.wait(timeout=5)
        return fetch(tmp_path, [row("20260515", number=number)["rcept_no"]], zip_document(), max_requests=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(collect, (1, 2)))
    assert sorted(result["status"] for result, _ in results) == ["ok", "quota_reached"]
    assert sum(len(session.calls) for _, session in results) == 1
    assert quota(tmp_path)[NOW.date().isoformat()]["requests"] == 1


@pytest.mark.parametrize("response", [{"status": "010", "message": KEY}, b"<result><status>800</status></result>",
                                      b"PKinvalid", b"<result><status>", b"unexpected body"])
def test_invalid_provider_responses_fail_without_caching_or_exposing_secrets(tmp_path, response):
    receipt = row("20260515")["rcept_no"]
    with pytest.raises(DartAPIError) as error:
        fetch(tmp_path, [receipt], response)
    assert KEY not in str(error.value) and not saved_path(tmp_path, receipt).exists()
    assert quota(tmp_path)[NOW.date().isoformat()]["requests"] == 1


def test_transport_failure_counts_one_request_and_is_not_retried(tmp_path):
    receipt = row("20260515")["rcept_no"]
    session = FakeSession(requests.Timeout(f"https://provider.invalid?crtfc_key={KEY}"))
    with pytest.raises(DartAPIError, match="transport failure") as error:
        BusinessTextCollector(KEY, session).fetch([receipt], tmp_path, 10, execute=True)
    assert KEY not in str(error.value) and len(session.calls) == 1
    assert quota(tmp_path)[NOW.date().isoformat()]["requests"] == 1 and not saved_path(tmp_path, receipt).exists()


def test_dry_run_is_read_only_and_needs_no_key_or_session(tmp_path):
    data_dir = tmp_path / "uncreated"
    receipt = row("20260515")["rcept_no"]
    session = FakeSession()
    collector = BusinessTextCollector(None, session)
    assert collector.plan([], data_dir)["needed"] == 0
    result = collector.fetch([receipt], data_dir, 1)
    assert result["status"] == "dry_run" and result["estimated_requests"] == 1 and result["requests_made"] == 0
    assert session.calls == [] and not data_dir.exists()


@pytest.mark.parametrize("max_requests", [0, -1, True])
def test_invalid_fetch_budget_fails_before_requests_or_writes(tmp_path, max_requests):
    with pytest.raises(ValueError, match="positive integer"):
        BusinessTextCollector(KEY).fetch([row("20260515")["rcept_no"]], tmp_path, max_requests, execute=True)
    assert list(tmp_path.iterdir()) == []


def test_invalid_identifiers_dates_and_saved_versions_fail_clearly(tmp_path):
    for stock, day in [("5930", "20260529"), (5930, "20260529"), (STOCK, "20260230")]:
        with pytest.raises(ValueError):
            periodic_reports(stock, day, tmp_path)
    with pytest.raises(ValueError, match="receipt"):
        BusinessTextCollector(None).fetch(["../not-a-receipt"], tmp_path, 1)
    report = row("20260515")
    listings(tmp_path, report)
    save_sections(tmp_path, report, extraction_version="older-extractor")
    with pytest.raises(ValueError, match="incompatible"):
        load_business_text(STOCK, "20260529", tmp_path)


@pytest.mark.parametrize("changes", [{"status": []}, {"overview": 27}, {"omitted": "false"},
                                     {"status": "013", "overview": "잘못된 성공 본문"}])
def test_invalid_saved_sections_fail_instead_of_substituting_an_annual(tmp_path, changes):
    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic)
    save_sections(tmp_path, annual)
    save_sections(tmp_path, periodic, **changes)
    with pytest.raises(ValueError, match="incompatible"):
        load_business_text(STOCK, "20260529", tmp_path)


def test_incomplete_cache_is_not_treated_as_a_missing_overview(tmp_path):
    report = row("20260515")
    listings(tmp_path, report)
    record = save_sections(tmp_path, report)
    del record["overview"]
    saved_path(tmp_path, report["rcept_no"]).write_text(json.dumps(record))
    with pytest.raises(ValueError, match="incompatible"):
        load_business_text(STOCK, "20260529", tmp_path)


@pytest.mark.parametrize("command", ["plan", "fetch"])
def test_cli_plan_and_dry_fetch_do_not_read_environment(tmp_path, monkeypatch, capsys, command):
    def denied(*args, **kwargs):
        pytest.fail("Dry CLI must not read environment variables")

    annual, periodic = row("20260320", "사업보고서 (2025.12)"), row("20260515")
    listings(tmp_path, annual, periodic)
    requests_file = tmp_path / "requests.jsonl"
    write_rows(requests_file, [{"stock_code": STOCK, "decision_date": "2026-05-29"}])
    before = snapshot(tmp_path)
    monkeypatch.setattr(cli, "os", SimpleNamespace(getenv=denied))
    argv = ["dart_business_text", command, "--requests-file", str(requests_file), "--data-dir", str(tmp_path)]
    if command == "fetch":
        argv += ["--max-requests", "2"]
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert (result["needed"], result["stored"], result["missing"], result["estimated_requests"]) == (2, 0, 2, 2)
    assert snapshot(tmp_path) == before


def test_cli_show_prints_selected_receipt_and_500_character_preview(tmp_path, monkeypatch, capsys):
    report = row("20260515")
    listings(tmp_path, report)
    save_sections(tmp_path, report, overview="개" * 600, products=None)
    monkeypatch.setattr("sys.argv", ["dart_business_text", "show", "--stock-code", STOCK,
                                    "--date", "2026-05-29", "--data-dir", str(tmp_path)])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["selected_report"]["rcept_no"] == report["rcept_no"]
    assert result["business_text"]["text"] == "개" * 500 and result["business_text"]["chars"] == 600


def test_cli_execute_fetches_once_using_the_planned_receipts(tmp_path, monkeypatch, capsys):
    report = row("20260515")
    listings(tmp_path, report)
    requests_file = tmp_path / "requests.jsonl"
    write_rows(requests_file, [{"stock_code": STOCK, "decision_date": "20260529"}])
    loaded, session = [], FakeSession(zip_document())
    monkeypatch.setattr(cli, "load_project_env", lambda: loaded.append(True))
    monkeypatch.setattr(cli, "os", SimpleNamespace(getenv=lambda key, default=None: KEY if key == "DART_API_KEY" else default))
    monkeypatch.setattr(cli, "BusinessTextCollector", lambda key: BusinessTextCollector(key, session))
    monkeypatch.setattr("sys.argv", ["dart_business_text", "fetch", "--requests-file", str(requests_file),
                                    "--data-dir", str(tmp_path), "--max-requests", "1", "--execute"])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok" and result["requests_made"] == 1 and loaded == [True]
    assert session.calls[0][1]["params"]["rcept_no"] == report["rcept_no"]


@pytest.mark.parametrize("content", [None, "{invalid JSON}", '{}\n'])
def test_cli_missing_or_malformed_requests_are_errors(tmp_path, monkeypatch, capsys, content):
    path = tmp_path / "requests.jsonl"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr("sys.argv", ["dart_business_text", "plan", "--requests-file", str(path)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1 and json.loads(capsys.readouterr().out)["status"] == "error"
