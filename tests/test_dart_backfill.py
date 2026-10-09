import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from threading import Barrier
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
import requests

from scripts.data import dart_backfill as cli
from src.ingestion import dart_backfill as module
from src.ingestion.dart_backfill import KST, MAX_BODY_CHARACTERS, backfill, detail_category, select_report
from src.ingestion.storage import read_rows


KEY = "fixture-private-key"
DAY = "20230103"
START = datetime(2026, 9, 4, 10, tzinfo=KST)
BODY = "주요사항 이사회 결의에 따른 공시 본문입니다. 계약금액(원) 100,000,000 매출액대비(%) 12.5 " * 80
CATEGORY_REPORTS = (
    ("regulatory_risk", "불성실공시법인지정"),
    ("convertible_bond", "주요사항보고서(전환사채권발행결정)"),
    ("capital_raise", "주요사항보고서(유상증자결정)"),
    ("buyback", "자사주소각"),
    ("merger", "회사합병결정"),
    ("contract", "단일판매ㆍ공급계약체결"),
    ("dividend", "현금ㆍ현물배당결정"),
    ("earnings", "영업(잠정)실적(공정공시)"),
    ("bond_subtype", "전환청구권행사"),
)


@pytest.fixture(autouse=True)
def no_env_or_real_session(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Tests must not read env files or create real sessions")

    monkeypatch.setattr("src.config.settings.load_project_env", denied)
    monkeypatch.setattr(cli, "load_project_env", denied)
    monkeypatch.setattr("requests.Session", denied)


def row(number, day=DAY, corp_cls="Y", title="기타공시", **changes):
    return {"rcept_no": f"{day}{number:06d}", "rcept_dt": day, "report_nm": title,
            "corp_code": "00126380" if corp_cls == "Y" else "00987654", "corp_name": "시험기업",
            "stock_code": "005930" if corp_cls == "Y" else "123450", "corp_cls": corp_cls,
            "flr_nm": "시험기업", "rm": "", **changes}


def page(number, rows, total=None, **changes):
    total = len(rows) if total is None else total
    return {"status": "000", "page_no": number, "page_count": 100, "total_count": total,
            "total_page": (total + 99) // 100, "list": rows, **changes}


def document(text=BODY, *, encoding="utf-8", files=None):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        if files is None:
            archive.writestr("body.xml", f"<DOCUMENT><BODY>{escape(text)}</BODY></DOCUMENT>".encode(encoding))
        else:
            for name, blob in files.items():
                archive.writestr(name, blob)
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
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.payloads:
            pytest.fail("Unexpected DART request")
        value = self.payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


class FakeClock:
    def __init__(self, now=START):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def run(tmp_path, *payloads, clock=None, **kwargs):
    clock = FakeClock() if clock is None else clock
    session = FakeSession(*payloads)
    summary = backfill(kwargs.pop("from_date", DAY), kwargs.pop("to_date", DAY),
        execute=True, api_key=KEY, data_dir=tmp_path, session=session,
        clock=clock, sleeper=clock.sleep, **kwargs)
    return summary, session


def root(tmp_path):
    return tmp_path / "disclosures" / "dart_full"


def archive(tmp_path, stage="list", day=DAY):
    return root(tmp_path) / stage / day[:4] / f"{day}.jsonl"


def state(tmp_path):
    return json.loads((root(tmp_path) / "_state.json").read_text())


def quota(tmp_path):
    return json.loads((root(tmp_path) / "_quota.json").read_text())["days"]


def snapshot(tmp_path):
    return {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_all_pages_for_both_markets_are_saved_without_theme_or_corp_filter(tmp_path):
    y_rows = [row(n) for n in range(101)]
    k_rows = [row(n + 101, corp_cls="K") for n in range(101)]
    summary, session = run(tmp_path, page(1, y_rows[:100], 101), page(2, y_rows[100:], 101),
        page(1, k_rows[:100], 101), page(2, k_rows[100:], 101), stage="list")
    assert summary["status"] == "ok"
    assert summary["listed_rows"] == 202
    assert read_rows(archive(tmp_path)) == y_rows + k_rows
    assert state(tmp_path) == {"completed_listing_days": [DAY], "completed_detail_rcept_nos": [],
                               "skipped_missing_stock_code_rcept_nos": []}
    assert [(call[1]["params"]["corp_cls"], call[1]["params"]["page_no"]) for call in session.calls] == [
        ("Y", 1), ("Y", 2), ("K", 1), ("K", 2)]
    for url, kwargs in session.calls:
        assert url == module.DartBackfillCollector.LIST_URL
        params = kwargs["params"]
        assert "corp_code" not in params
        assert params["bgn_de"] == params["end_de"] == DAY
        assert params["page_count"] == 100 and params["last_reprt_at"] == "N"


def test_alphanumeric_listing_day_is_saved_and_selected_details_proceed(tmp_path):
    day = "20230322"
    y_rows = [row(1, day=day, title="단일판매ㆍ공급계약체결", stock_code="00088K"),
              row(2, day=day, title="단일판매ㆍ공급계약체결")]
    k_rows = [row(3, day=day, corp_cls="K", corp_name="그린광학", stock_code="0015G0"),
              row(4, day=day, corp_cls="K", corp_name="그린광학", stock_code="0015G0",
                  title="단일판매ㆍ공급계약체결")]
    summary, session = run(tmp_path, page(1, y_rows), page(1, k_rows),
                           document(), document(), document(), from_date=day, to_date=day)
    assert summary["status"] == "ok" and summary["listing_days_saved"] == 1
    assert summary["listed_rows"] == 4 and read_rows(archive(tmp_path, day=day)) == y_rows + k_rows
    assert summary["details_saved"] == 3 and summary["known_pending_details"] == 0
    assert summary["skipped_missing_stock_code"] == 0
    assert len(session.calls) == summary["requests_made"] == 5
    selected = y_rows + k_rows[1:]
    docs = read_rows(archive(tmp_path, "docs", day))
    assert {doc["metadata"]["rcept_no"]: doc["stock_code"] for doc in docs} == {
        item["rcept_no"]: item["stock_code"] for item in selected}
    assert all(doc["content"] == BODY.strip() for doc in docs)
    assert state(tmp_path)["completed_listing_days"] == [day]
    assert state(tmp_path)["completed_detail_rcept_nos"] == [item["rcept_no"] for item in selected]


def test_empty_weekend_days_are_completed_and_not_repeated(tmp_path):
    summary, session = run(tmp_path, *[{"status": "013"}] * 4, from_date="2023-01-01",
                           to_date="20230102", stage="list")
    assert summary["listing_days_saved"] == 2 and len(session.calls) == 4
    assert archive(tmp_path, day="20230101").read_bytes() == b""
    assert archive(tmp_path, day="20230102").read_bytes() == b""
    before = snapshot(tmp_path)
    summary, session = run(tmp_path, from_date="20230101", to_date="20230102", stage="list")
    assert summary["requests_made"] == 0 and session.calls == []
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("second", [
    {"status": "013"}, page(2, [], 101), page(1, [row(100)], 101),
    page(2, [row(100), row(101)], 102), page(2, [row(100, report_nm="")], 101),
    page(2, [row(0, title="충돌한 제목")], 101), page(2, [row(100)], 101, total_page=True),
    page(2, [row(100, rcept_dt="20230104")], 101),
])
def test_invalid_later_page_never_publishes_partial_day(tmp_path, second):
    summary, session = run(tmp_path, page(1, [row(n) for n in range(100)], 101), second, stage="list")
    assert summary["status"] == "error" and len(session.calls) == 2
    assert not archive(tmp_path).exists()
    assert not (root(tmp_path) / "_state.json").exists()


def test_second_market_failure_discards_first_market_rows(tmp_path):
    summary, _ = run(tmp_path, page(1, [row(1)]), page(1, [row(2)]), stage="list")
    assert summary["status"] == "error" and "market mismatch" in summary["error"]
    assert not archive(tmp_path).exists()


def test_identical_duplicate_receipts_keep_one_record(tmp_path):
    summary, _ = run(tmp_path, page(1, [row(1), row(1)]), {"status": "013"}, stage="list")
    assert summary["listed_rows"] == 1
    assert read_rows(archive(tmp_path)) == [row(1)]


@pytest.mark.parametrize("title", [
    "단일판매ㆍ공급계약체결", "[기재정정]단일판매·공급계약해지", "공급계약취소",
    "주요사항보고서(유상증자결정)", "주요사항보고서(무상증자결정)",
    "주요사항보고서(전환사채권발행결정)", "주요사항보고서(신주인수권부사채권발행결정)",
    "주요사항보고서(교환사채권발행결정)", "주요사항보고서(자기주식취득신탁계약체결결정)",
    "자기주식처분결정", "자사주소각", "주요사항보고서(회사합병결정)",
    "주요사항보고서(회사분할결정)", "주식교환결정", "영업양수결정",
    "현금ㆍ현물배당결정", "배당락", "영업(잠정)실적(공정공시)",
    "매출액 또는 손익구조 30%(대규모법인은 15%)이상 변경", "실적발표",
    "불성실공시법인지정", "상장폐지관련안내", "감사의견부적정", "횡령ㆍ배임혐의발생",
    "전환청구권행사", "신주인수권행사", "교환청구권행사", "교환권행사",
    "[기재정정]전환사채(해외전환사채포함)발행후만기전사채취득",
    "사채만기전취득", "사채 조기 상환", "채권재매입", "전환가액의조정", "신주인수권행사가액의조정",
])
def test_selects_strategy_events_and_bond_lifecycle_reports(title):
    assert select_report(title)


@pytest.mark.parametrize("title", [
    "사업보고서 (2023.12)", "분기보고서 (2023.03)", "주주총회소집공고", "임원ㆍ주요주주특정증권등소유상황보고서",
    "주식매수선택권행사", "기업설명회(IR)개최", "유형자산취득결정", "증권발행결과(자율공시)",
])
def test_leaves_unrelated_reports_in_listing_only(title):
    assert not select_report(title)


@pytest.mark.parametrize("title,category", [(title, name) for name, title in CATEGORY_REPORTS] + [
    ("[기재정정]전환사채(해외전환사채포함)발행후만기전사채취득", "convertible_bond"),
    ("불성실공시법인지정(전환사채)", "regulatory_risk"),
    ("전환청구권행사에 따른 유상증자", "capital_raise"),
    (" 영업 (잠정) 실적 (공정공시) ", "earnings"),
    ("사업보고서 (2023.12)", None),
])
def test_detail_category_uses_the_first_selection_match(title, category):
    assert detail_category(title) == category
    assert select_report(title) == (category is not None)


def test_every_detail_record_stores_its_selection_category(tmp_path):
    rows = [row(number, title=title) for number, (_, title) in enumerate(CATEGORY_REPORTS, 1)]
    payloads = [page(1, rows), {"status": "013"}]
    for category, _ in CATEGORY_REPORTS:
        if category in {"convertible_bond", "capital_raise"}:
            payloads.append({"status": "013"})
        payloads.append(b"<result><status>013</status></result>" if category == "earnings" else document())
    summary, _ = run(tmp_path, *payloads)
    assert summary["status"] == "ok" and summary["details_saved"] == len(rows)
    assert summary["bodies_unavailable"] == 1
    docs = read_rows(archive(tmp_path, "docs"))
    assert {doc["metadata"]["rcept_no"]: doc["selection_category"] for doc in docs} == {
        item["rcept_no"]: category for item, (category, _) in zip(rows, CATEGORY_REPORTS)}


@pytest.mark.parametrize("stage", ["all", "details"])
def test_category_filter_leaves_other_receipts_pending_without_skipping_them(tmp_path, stage):
    rows = [row(1, title="영업(잠정)실적(공정공시)"), row(2, title="단일판매ㆍ공급계약체결"),
            row(3, title="주요사항보고서(전환사채권발행결정)"), row(4, title="전환청구권행사"),
            row(5, title="주요사항보고서(유상증자결정)"),
            row(6, title="불성실공시법인지정", stock_code="")]
    categories = ["contract", "convertible_bond", "bond_subtype", "capital_raise"]
    summary, session = run(tmp_path, page(1, rows), {"status": "013"}, stage="list", categories=categories)
    assert summary["listed_rows"] == len(rows) and summary["known_pending_details"] == len(rows)
    assert len(session.calls) == 2 and read_rows(archive(tmp_path)) == rows
    listing_bytes = archive(tmp_path).read_bytes()
    summary, session = run(tmp_path, document(), {"status": "013"}, document(), document(),
                           {"status": "013"}, document(), stage=stage, categories=categories)
    assert summary["status"] == "ok" and summary["details_saved"] == 4
    assert summary["known_pending_details"] == 2 and summary["skipped_missing_stock_code"] == 0
    assert len(session.calls) == 6
    assert state(tmp_path)["completed_detail_rcept_nos"] == [item["rcept_no"] for item in rows[1:5]]
    assert state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == []
    assert not (root(tmp_path) / "skipped_missing_stock_code.jsonl").exists()
    assert archive(tmp_path).read_bytes() == listing_bytes
    assert [doc["selection_category"] for doc in read_rows(archive(tmp_path, "docs"))] == categories
    before = snapshot(tmp_path)
    summary, session = run(tmp_path, stage=stage, categories=categories)
    assert summary["status"] == "ok" and summary["known_pending_details"] == 2
    assert summary["details_saved"] == 0 and session.calls == [] and snapshot(tmp_path) == before
    summary, session = run(tmp_path, document(), stage="details", categories=["earnings"])
    assert summary["details_saved"] == 1 and summary["known_pending_details"] == 1
    assert session.calls[0][1]["params"]["rcept_no"] == rows[0]["rcept_no"]
    assert state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == []


def test_category_priority_precedes_date_order_across_days(tmp_path):
    next_day = "20230104"
    first_rows = [row(1, title="단일판매ㆍ공급계약체결"),
                  row(2, title="현금ㆍ현물배당결정"), row(3, title="실적발표")]
    next_rows = [row(1, day=next_day, title="현금ㆍ현물배당결정"),
                 row(2, day=next_day, title="실적발표"),
                 row(3, day=next_day, title="단일판매ㆍ공급계약체결")]
    run(tmp_path, page(1, first_rows), {"status": "013"}, page(1, next_rows), {"status": "013"},
        stage="list", to_date=next_day)
    summary, session = run(tmp_path, *[document()] * 6, stage="details", to_date=next_day,
                           categories=["earnings", "contract", "dividend"])
    assert summary["status"] == "ok" and summary["details_saved"] == 6
    assert [call[1]["params"]["rcept_no"] for call in session.calls] == [
        item["rcept_no"] for item in (first_rows[2], next_rows[1], first_rows[0], next_rows[2],
                                      first_rows[1], next_rows[0])]


@pytest.mark.parametrize("categories", [None, [], (), set()])
def test_no_category_filter_preserves_existing_detail_order(tmp_path, categories):
    rows = [row(1, title="실적발표"), row(2, title="단일판매ㆍ공급계약체결")]
    summary, session = run(tmp_path, page(1, rows), {"status": "013"}, document(), document(),
                           categories=categories)
    assert summary["status"] == "ok" and summary["details_saved"] == 2
    assert [call[1]["params"]["rcept_no"] for call in session.calls[2:]] == [
        item["rcept_no"] for item in rows]


@pytest.mark.parametrize("execute", [False, True])
def test_unknown_category_lists_valid_names_before_requests_or_writes(tmp_path, execute):
    session = FakeSession()
    with pytest.raises(ValueError, match="unknown detail category") as error:
        backfill(DAY, DAY, execute=execute, api_key=KEY, session=session, clock=FakeClock(),
                 data_dir=tmp_path, categories=["contract", "unknown_category"])
    assert "valid names:" in str(error.value)
    assert all(name in str(error.value) for name, _ in module.SELECTION_PATTERNS)
    assert session.calls == [] and list(tmp_path.iterdir()) == []


def test_structured_rows_match_receipts_and_cache_while_every_document_is_requested(tmp_path):
    rows = [row(n, title="주요사항보고서(전환사채권발행결정)") for n in (1, 2)]
    structured = [{"rcept_no": row(99)["rcept_no"], "bd_tm": "wrong receipt"},
                  {"rcept_no": rows[0]["rcept_no"], "bd_tm": "1", "full_field": "전체필드" * 1000},
                  {"rcept_no": rows[1]["rcept_no"], "bd_tm": "2"}]
    summary, session = run(tmp_path, page(1, rows), {"status": "013"},
        {"status": "000", "list": structured}, document(), document())
    assert summary["details_saved"] == 2 and summary["requests_made"] == 5
    docs = read_rows(archive(tmp_path, "docs"))
    for index, doc in enumerate(docs):
        assert doc["metadata"]["structured_endpoint"] == "cvbdIsDecsn"
        assert doc["metadata"]["structured_row"] == structured[index + 1]
        assert doc["metadata"]["structured_rcept_no"] == rows[index]["rcept_no"]
        assert doc["content"] == BODY.strip()
    assert len(docs[0]["metadata"]["structured_row"]["full_field"]) > 2500
    assert len([url for url, _ in session.calls if url.endswith("cvbdIsDecsn.json")]) == 1
    assert len([url for url, _ in session.calls if url.endswith("document.xml")]) == 2


@pytest.mark.parametrize("structured", [
    {"status": "013"}, {"status": "000", "list": [{"rcept_no": row(2)["rcept_no"], "amount": "wrong"}]},
    {"status": "000", "list": [{"rcept_no": row(1)["rcept_no"]}] * 2},
])
def test_missing_or_ambiguous_structured_match_never_substitutes_another_receipt(tmp_path, structured):
    summary, _ = run(tmp_path, page(1, [row(1, title="주요사항보고서(유상증자결정)")]),
                     {"status": "013"}, structured, document())
    assert summary["status"] == "ok"
    doc = read_rows(archive(tmp_path, "docs"))[0]
    assert "structured_row" not in doc["metadata"] and doc["content"] == BODY.strip()


def test_full_body_and_date_only_document_are_compatible_with_parser(tmp_path):
    from src.strategies.disclosures import parse_disclosures

    text = BODY + "계약상대 시험상대방 4. 계약기간 시작일 2023-01-03 종료일 2025-12-31 최종본문표식"
    summary, _ = run(tmp_path, page(1, [row(1, title="단일판매ㆍ공급계약체결")]),
                     {"status": "013"}, document(text))
    assert summary["status"] == "ok"
    doc = read_rows(archive(tmp_path, "docs"))[0]
    assert len(doc["content"]) > 2500 and doc["content"] == text
    assert doc["content"].endswith("최종본문표식") and "body_truncated_at" not in doc
    assert doc["published_at"] == "2023-01-03" and doc["rcept_dt"] == DAY
    assert doc["first_seen_at"] is None and doc["metadata"]["first_seen_at"] is None
    assert datetime.fromisoformat(doc["collected_at"]).utcoffset() == timedelta(hours=9)
    parsed = parse_disclosures([doc])
    assert parsed.iloc[0]["event_id"] == row(1)["rcept_no"]
    assert parsed.iloc[0]["category"] == "contract"
    assert parsed["first_seen_at"].isna().all()


def test_body_cap_is_explicit_at_two_hundred_thousand_characters(tmp_path):
    text = "이사회 결의에 따른 계약 공시의 본문입니다. " * 11000
    summary, _ = run(tmp_path, page(1, [row(1, title="단일판매ㆍ공급계약체결")]),
                     {"status": "013"}, document(text))
    assert summary["status"] == "ok"
    doc = read_rows(archive(tmp_path, "docs"))[0]
    assert doc["content"] == text.strip()[:MAX_BODY_CHARACTERS]
    assert doc["body_truncated_at"] == doc["metadata"]["body_truncated_at"] == 200_000


def test_reuses_inner_file_selection_and_decoding_without_toc_or_css(tmp_path):
    toc = "목 차 I. 회사의 개요 -----1 Ⅱ. 사업의 내용 -----2 Ⅲ. 재무에 관한 사항 -----3 " * 10
    text = ".xforms * { font-family: 돋움체; } " + BODY + " 인코딩복구본문끝"
    blob = document(files={"toc.xml": f"<DOCUMENT>{toc}</DOCUMENT>".encode(),
                           "body.html": f"<html><style>CSS 숨김</style><body>{text}</body></html>".encode("cp949"),
                           "image.png": b"not a text document"})
    summary, _ = run(tmp_path, page(1, [row(1, title="단일판매ㆍ공급계약체결")]), {"status": "013"}, blob)
    assert summary["status"] == "ok"
    doc = read_rows(archive(tmp_path, "docs"))[0]
    assert doc["content"].endswith("인코딩복구본문끝")
    assert "목 차" not in doc["content"] and "font-family" not in doc["content"]
    assert doc["metadata"]["encoding_fixed"]


def test_quota_during_listing_stores_nothing_and_resumes_on_next_kst_day(tmp_path):
    summary, session = run(tmp_path, page(1, [row(1)]), stage="list", max_requests=1)
    assert summary["status"] == "quota_reached" and len(session.calls) == 1
    assert not archive(tmp_path).exists() and not (root(tmp_path) / "_state.json").exists()
    summary, session = run(tmp_path, stage="list", max_requests=1)
    assert summary["status"] == "quota_reached" and session.calls == []
    next_clock = FakeClock(START + timedelta(days=1))
    summary, session = run(tmp_path, page(1, [row(1)]), {"status": "013"},
                           clock=next_clock, stage="list", max_requests=2)
    assert summary["status"] == "ok" and len(session.calls) == 2
    assert quota(tmp_path)["2026-09-04"]["requests"] == 1
    assert quota(tmp_path)["2026-09-05"]["requests"] == 2


def test_quota_resumes_only_unfinished_details_and_rerun_is_idempotent(tmp_path):
    rows = [row(n, title="단일판매ㆍ공급계약체결") for n in (1, 2)]
    summary, _ = run(tmp_path, page(1, rows), {"status": "013"}, document(), max_requests=3)
    assert summary["status"] == "quota_reached" and summary["details_saved"] == 1
    assert summary["known_pending_details"] == 1
    first_bytes = archive(tmp_path, "docs").read_bytes()
    assert state(tmp_path)["completed_detail_rcept_nos"] == [rows[0]["rcept_no"]]
    summary, session = run(tmp_path, max_requests=3)
    assert summary["status"] == "quota_reached" and session.calls == []
    assert archive(tmp_path, "docs").read_bytes() == first_bytes
    summary, session = run(tmp_path, document(), max_requests=3, clock=FakeClock(START + timedelta(days=1)))
    assert summary["status"] == "ok" and len(session.calls) == 1
    assert session.calls[0][1]["params"]["rcept_no"] == rows[1]["rcept_no"]
    assert archive(tmp_path, "docs").read_bytes().startswith(first_bytes)
    before = snapshot(tmp_path)
    summary, session = run(tmp_path, max_requests=3, clock=FakeClock(START + timedelta(days=1)))
    assert summary["status"] == "ok" and summary["requests_made"] == 0 and session.calls == []
    assert snapshot(tmp_path) == before


def test_category_quota_stop_resumes_mid_category_after_completing_earlier_categories(tmp_path):
    next_day = "20230104"
    first_rows = [row(1, title="실적발표"), row(2, title="단일판매ㆍ공급계약체결")]
    next_rows = [row(1, day=next_day, title="실적발표"),
                 row(2, day=next_day, title="단일판매ㆍ공급계약체결"),
                 row(3, day=next_day, title="불성실공시법인지정", stock_code="")]
    run(tmp_path, page(1, first_rows), {"status": "013"}, page(1, next_rows), {"status": "013"},
        stage="list", to_date=next_day)
    options = {"stage": "details", "to_date": next_day, "max_requests": 3,
               "categories": ["contract", "earnings"]}
    clock = FakeClock(START + timedelta(days=1))
    summary, session = run(tmp_path, document(), document(), document(), clock=clock, **options)
    assert summary["status"] == "quota_reached" and summary["details_saved"] == 3
    assert summary["known_pending_details"] == 2 and summary["skipped_missing_stock_code"] == 0
    completed = [first_rows[1]["rcept_no"], next_rows[1]["rcept_no"], first_rows[0]["rcept_no"]]
    assert [call[1]["params"]["rcept_no"] for call in session.calls] == completed
    assert state(tmp_path)["completed_detail_rcept_nos"] == sorted(completed)
    assert state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == []
    before = snapshot(tmp_path)
    summary, session = run(tmp_path, clock=clock, **options)
    assert summary["status"] == "quota_reached" and session.calls == []
    assert snapshot(tmp_path) == before
    clock = FakeClock(START + timedelta(days=2))
    summary, session = run(tmp_path, document(), clock=clock, **options)
    assert summary["status"] == "ok" and summary["details_saved"] == 1
    assert summary["known_pending_details"] == 1 and summary["skipped_missing_stock_code"] == 0
    assert len(session.calls) == 1 and session.calls[0][1]["params"]["rcept_no"] == next_rows[0]["rcept_no"]
    assert state(tmp_path)["completed_detail_rcept_nos"] == sorted(completed + [next_rows[0]["rcept_no"]])
    assert state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == []
    before = snapshot(tmp_path)
    summary, session = run(tmp_path, clock=clock, **options)
    assert summary["status"] == "ok" and session.calls == [] and snapshot(tmp_path) == before


@pytest.mark.parametrize("where", ["listing", "structured", "document"])
def test_status_020_stops_cleanly_and_blocks_same_day_resume(tmp_path, where):
    selected = row(1, title="주요사항보고서(전환사채권발행결정)" if where == "structured" else "단일판매ㆍ공급계약체결")
    error = {"status": "020", "message": f"https://provider.invalid?crtfc_key={KEY}"}
    if where == "listing":
        payloads = [error]
    elif where == "structured":
        payloads = [page(1, [selected]), {"status": "013"}, error]
    else:
        payloads = [page(1, [selected]), {"status": "013"},
                    f"<result><status>020</status><message>{KEY}</message></result>".encode()]
    summary, _ = run(tmp_path, *payloads)
    assert summary["status"] == "quota_reached" and summary["reason"] == "provider_status_020"
    assert not archive(tmp_path, "docs").exists()
    if where != "listing":
        assert state(tmp_path)["completed_detail_rcept_nos"] == []
    assert quota(tmp_path)["2026-09-04"]["provider_limited"]
    summary, session = run(tmp_path)
    assert summary["status"] == "quota_reached" and session.calls == []
    resume = ([page(1, [selected]), {"status": "013"}] if where == "listing" else [])
    if where == "structured":
        resume.append({"status": "013"})
    summary, _ = run(tmp_path, *resume, document(), clock=FakeClock(START + timedelta(days=1)))
    assert summary["status"] == "ok" and summary["details_saved"] == 1


@pytest.mark.parametrize("where", ["listing", "structured", "document"])
@pytest.mark.parametrize("status", ["010", "011", "012", "021", "100", "101", "800", "900"])
def test_provider_errors_stop_in_every_stage_without_marking_details_done(tmp_path, where, status, capsys):
    selected = row(1, title="주요사항보고서(교환사채권발행결정)" if where == "structured" else "단일판매ㆍ공급계약체결")
    error = {"status": status, "message": KEY}
    payloads = ([error] if where == "listing" else [page(1, [selected, row(2, title=selected["report_nm"])]), {"status": "013"},
        error if where == "structured" else f"<result><status>{status}</status><message>{KEY}</message></result>".encode()])
    summary, session = run(tmp_path, *payloads)
    if status == "800":
        assert summary["status"] == "provider_maintenance" and summary["reason"] == "provider_status_800"
    else:
        assert summary["status"] == "error" and f"status={status}" in summary["error"]
    assert len(session.calls) == summary["requests_made"] == (1 if where == "listing" else 3)
    assert quota(tmp_path)["2026-09-04"]["requests"] == summary["requests_made"]
    assert summary["documents_not_found"] == 0
    assert not archive(tmp_path, "docs").exists()
    output = capsys.readouterr()
    assert KEY not in json.dumps(summary) + output.out + output.err
    if where != "listing":
        assert state(tmp_path)["completed_detail_rcept_nos"] == []
        assert summary["known_pending_details"] == 2


def test_status_014_in_listing_still_stops_the_run(tmp_path):
    summary, session = run(tmp_path, {"status": "014"}, stage="list")
    assert summary["status"] == "error" and "status=014" in summary["error"]
    assert summary["documents_not_found"] == 0
    assert len(session.calls) == summary["requests_made"] == 1
    assert not archive(tmp_path).exists()


@pytest.mark.parametrize("where", ["document_xml", "document_json", "structured"])
def test_status_014_saves_empty_detail_continues_and_is_not_retried(tmp_path, where, capsys):
    title = "주요사항보고서(전환사채권발행결정)" if where == "structured" else "단일판매ㆍ공급계약체결"
    rows = [row(number, title=title) for number in (1, 2)]
    run(tmp_path, page(1, rows), {"status": "013"}, stage="list")
    error = {"status": "014", "message": KEY}
    payloads = [f"<result><status>014</status><message>{KEY}</message></result>".encode()
                if where == "document_xml" else error]
    if where == "structured":
        payloads.append({"status": "000", "list": [{"rcept_no": rows[1]["rcept_no"], "bd_tm": "2"}]})
    payloads.append(document())
    summary, session = run(tmp_path, *payloads, stage="details")
    assert summary["status"] == "ok" and summary["details_saved"] == 2
    assert summary["documents_not_found"] == summary["bodies_unavailable"] == 1
    assert summary["known_pending_details"] == 0
    assert len(session.calls) == summary["requests_made"] == (3 if where == "structured" else 2)
    assert quota(tmp_path)["2026-09-04"] == {
        "requests": 2 + summary["requests_made"], "provider_limited": False}
    docs = read_rows(archive(tmp_path, "docs"))
    error_type = "dart_014_structured_file_not_found" if where == "structured" else "dart_014_file_not_found"
    assert docs[0]["content"] == ""
    assert docs[0]["body_error_type"] == docs[0]["metadata"]["body_error_type"] == error_type
    assert not docs[0]["metadata"]["has_body"] and not docs[0]["metadata"]["body_extracted"]
    assert docs[1]["content"] == BODY.strip() and docs[1]["body_error_type"] == "success"
    document_calls = [kwargs["params"]["rcept_no"] for url, kwargs in session.calls
                      if url.endswith("document.xml")]
    assert document_calls == [item["rcept_no"] for item in (rows[1:] if where == "structured" else rows)]
    assert state(tmp_path)["completed_detail_rcept_nos"] == [item["rcept_no"] for item in rows]
    output = capsys.readouterr()
    assert KEY not in archive(tmp_path, "docs").read_text() + json.dumps(summary) + output.out + output.err
    before = snapshot(tmp_path)
    summary, session = run(tmp_path, stage="details", clock=FakeClock(START + timedelta(days=1)))
    assert summary["status"] == "ok" and session.calls == []
    assert summary["details_saved"] == summary["documents_not_found"] == summary["bodies_unavailable"] == 0
    assert snapshot(tmp_path) == before


def test_status_014_counts_toward_quota_and_resume_only_requests_pending_receipt(tmp_path):
    rows = [row(number, title="단일판매ㆍ공급계약체결") for number in (1, 2)]
    run(tmp_path, page(1, rows), {"status": "013"}, stage="list")
    summary, session = run(tmp_path, {"status": "014"}, stage="details", max_requests=3)
    assert summary["status"] == "quota_reached"
    assert summary["details_saved"] == summary["documents_not_found"] == summary["known_pending_details"] == 1
    assert len(session.calls) == summary["requests_made"] == 1
    assert quota(tmp_path)["2026-09-04"]["requests"] == 3
    assert state(tmp_path)["completed_detail_rcept_nos"] == [rows[0]["rcept_no"]]
    summary, session = run(tmp_path, document(), stage="details", max_requests=3,
                           clock=FakeClock(START + timedelta(days=1)))
    assert summary["status"] == "ok" and summary["details_saved"] == 1
    assert summary["documents_not_found"] == summary["known_pending_details"] == 0
    assert len(session.calls) == 1 and session.calls[0][1]["params"]["rcept_no"] == rows[1]["rcept_no"]


@pytest.mark.parametrize("payload", [{}, [], {"status": 0}, {"status": "013", "list": [row(1)]}])
def test_invalid_status_is_not_treated_as_no_data(tmp_path, payload):
    summary, _ = run(tmp_path, payload, stage="list")
    assert summary["status"] == "error" and not archive(tmp_path).exists()


def test_explicit_document_no_data_is_stored_as_empty_without_fabricated_body(tmp_path):
    summary, _ = run(tmp_path, page(1, [row(1, title="단일판매ㆍ공급계약체결")]), {"status": "013"},
                     b"<result><status>013</status><message>no data</message></result>")
    assert summary["status"] == "ok" and summary["bodies_unavailable"] == 1
    assert summary["documents_not_found"] == 0
    doc = read_rows(archive(tmp_path, "docs"))[0]
    assert doc["content"] == "" and doc["body_error_type"] == "official_no_data"
    assert not doc["metadata"]["has_body"]


@pytest.mark.parametrize("blob", [b"PKbroken ZIP", document(files={"image.png": b"image"}),
                                  b"<result><status>000</status></result>"])
def test_malformed_document_does_not_become_a_completed_title_fallback(tmp_path, blob):
    summary, _ = run(tmp_path, page(1, [row(1, title="단일판매ㆍ공급계약체결")]), {"status": "013"}, blob)
    assert summary["status"] == "error" and state(tmp_path)["completed_detail_rcept_nos"] == []
    assert not archive(tmp_path, "docs").exists()


def test_selected_missing_stock_code_is_skipped_once_across_reruns(tmp_path):
    selected = row(1, title="주요사항보고서(전환사채권발행결정)", stock_code="")
    summary, session = run(tmp_path, page(1, [selected]), {"status": "013"})
    assert summary["status"] == "ok" and summary["skipped_missing_stock_code"] == 1
    assert summary["details_saved"] == summary["known_pending_details"] == 0
    assert len(session.calls) == summary["requests_made"] == 2
    skipped_path = root(tmp_path) / "skipped_missing_stock_code.jsonl"
    assert read_rows(skipped_path) == [{
        "rcept_no": selected["rcept_no"], "corp_code": selected["corp_code"],
        "corp_name": selected["corp_name"], "corp_cls": selected["corp_cls"],
        "report_nm": selected["report_nm"], "rcept_dt": DAY, "reason": "missing_stock_code"}]
    assert state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == [selected["rcept_no"]]
    assert state(tmp_path)["completed_detail_rcept_nos"] == []
    assert not archive(tmp_path, "docs").exists()
    before = snapshot(tmp_path)
    summary, session = run(tmp_path)
    assert summary["status"] == "ok" and summary["skipped_missing_stock_code"] == 0
    assert summary["known_pending_details"] == summary["requests_made"] == 0
    assert session.calls == [] and snapshot(tmp_path) == before


def test_missing_stock_code_does_not_stop_later_receipts_or_days(tmp_path):
    skipped = row(1, title="주요사항보고서(전환사채권발행결정)", stock_code="", corp_name=KEY)
    later = row(2, title="단일판매ㆍ공급계약체결")
    next_day = row(3, day="20230104", title="단일판매ㆍ공급계약체결")
    summary, session = run(tmp_path, page(1, [skipped, later]), {"status": "013"},
        page(1, [next_day]), {"status": "013"}, document(), document(), to_date="20230104")
    assert summary["status"] == "ok" and summary["skipped_missing_stock_code"] == 1
    assert summary["listing_days_saved"] == summary["details_saved"] == 2
    assert summary["known_pending_details"] == 0
    assert len(session.calls) == summary["requests_made"] == 6
    assert read_rows(archive(tmp_path, "docs"))[0]["metadata"]["rcept_no"] == later["rcept_no"]
    assert read_rows(archive(tmp_path, "docs", next_day["rcept_dt"]))[0]["metadata"]["rcept_no"] == next_day["rcept_no"]
    assert state(tmp_path)["completed_detail_rcept_nos"] == [later["rcept_no"], next_day["rcept_no"]]
    assert all(KEY not in value.decode() for value in snapshot(tmp_path).values())


def test_skip_archive_published_before_checkpoint_is_recovered_once(tmp_path, monkeypatch):
    selected = row(1, title="전환청구권행사", stock_code="")
    run(tmp_path, page(1, [selected]), {"status": "013"}, stage="list")
    write = module._write_json

    def interrupt_checkpoint(path, value):
        if path.name == "_state.json":
            raise OSError("fixture checkpoint interruption")
        return write(path, value)

    monkeypatch.setattr(module, "_write_json", interrupt_checkpoint)
    with pytest.raises(OSError, match="checkpoint interruption"):
        run(tmp_path, stage="details")
    skipped_path = root(tmp_path) / "skipped_missing_stock_code.jsonl"
    saved = read_rows(skipped_path)
    assert len(saved) == 1 and state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == []
    monkeypatch.setattr(module, "_write_json", write)
    summary, session = run(tmp_path, stage="details")
    assert summary["status"] == "ok" and session.calls == []
    assert summary["skipped_missing_stock_code"] == summary["known_pending_details"] == 0
    assert state(tmp_path)["skipped_missing_stock_code_rcept_nos"] == [selected["rcept_no"]]
    assert read_rows(skipped_path) == saved


def test_completed_skip_archive_missing_on_disk_fails_clearly(tmp_path):
    run(tmp_path, page(1, [row(1, title="전환청구권행사", stock_code="")]), {"status": "013"})
    (root(tmp_path) / "skipped_missing_stock_code.jsonl").unlink()
    with pytest.raises(ValueError, match="completed skip archive is missing"):
        run(tmp_path)


@pytest.mark.parametrize("stock_code", [None, "00593", "      ", "0015g0", "015G0", "15G0",
                                       "0015G0X", "0015-0", "００５９３０", "0015Ｇ0"])
def test_invalid_stock_code_is_not_treated_as_a_missing_code_skip(tmp_path, stock_code):
    summary, session = run(tmp_path, page(1, [row(1, title="전환청구권행사", stock_code=stock_code)]))
    assert summary["status"] == "error" and summary["skipped_missing_stock_code"] == 0
    assert len(session.calls) == 1 and not archive(tmp_path).exists()
    assert not (root(tmp_path) / "skipped_missing_stock_code.jsonl").exists()


def test_api_key_is_redacted_from_listing_structured_values_keys_body_and_output(tmp_path, capsys):
    selected = row(1, title=f"주요사항보고서(전환사채권발행결정) {KEY}", corp_name=KEY,
                   api_key=KEY, extra={"CRTFC_KEY": KEY, "note": KEY, f"field-{KEY}": KEY})
    target = {"rcept_no": selected["rcept_no"], "apiKey": KEY, "amount": "100",
              "note": f"https://provider.invalid?crtfc_key={KEY}", "nested": {"authorization": KEY}}
    summary, _ = run(tmp_path, page(1, [selected]), {"status": "013"},
                     {"status": "000", "list": [target], "message": KEY}, document(BODY + KEY))
    assert summary["status"] == "ok"
    output = capsys.readouterr()
    assert KEY not in json.dumps(summary) + output.out + output.err
    assert all(KEY not in value.decode() for value in snapshot(tmp_path).values())
    saved = read_rows(archive(tmp_path))[0]
    assert "api_key" not in saved and "CRTFC_KEY" not in saved["extra"]
    doc = read_rows(archive(tmp_path, "docs"))[0]
    assert doc["content"].endswith("[REDACTED]")
    assert "apiKey" not in doc["metadata"]["structured_row"]


def test_transient_transport_retry_is_throttled_and_every_attempt_counts(tmp_path):
    clock = FakeClock()
    summary, session = run(tmp_path, requests.Timeout(f"https://provider.invalid?crtfc_key={KEY}"),
                           page(1, [row(1)]), {"status": "013"}, clock=clock, stage="list")
    assert summary["status"] == "ok" and len(session.calls) == summary["requests_made"] == 3
    assert clock.sleeps == [0.2, 1, 0.2, 0.2]
    assert quota(tmp_path)["2026-09-04"]["requests"] == 3


def test_exhausted_transport_errors_are_sanitized_and_remain_pending(tmp_path, capsys):
    error = requests.ConnectionError(f"https://provider.invalid?crtfc_key={KEY}")
    summary, session = run(tmp_path, error, error, error, stage="list")
    assert summary["status"] == "error" and len(session.calls) == 3
    output = capsys.readouterr()
    assert KEY not in json.dumps(summary) + output.out + output.err
    assert not archive(tmp_path).exists()


def test_quota_also_stops_retry_before_another_attempt(tmp_path):
    summary, session = run(tmp_path, requests.Timeout(KEY), stage="list", max_requests=1)
    assert summary["status"] == "quota_reached" and len(session.calls) == 1
    assert quota(tmp_path)["2026-09-04"]["requests"] == 1


@pytest.mark.parametrize("http_status, retries", [(400, 1), (503, 3)])
def test_http_errors_retry_only_transient_statuses(tmp_path, http_status, retries):
    error = requests.HTTPError(KEY, response=SimpleNamespace(status_code=http_status))
    summary, session = run(tmp_path, *[error] * retries, stage="list")
    assert summary["status"] == "error" and len(session.calls) == retries


def test_quota_day_is_kst_even_when_clock_returns_utc(tmp_path):
    clock = FakeClock(datetime(2026, 9, 4, 15, 30, tzinfo=timezone.utc))
    summary, _ = run(tmp_path, {"status": "013"}, {"status": "013"}, clock=clock, stage="list")
    assert summary["status"] == "ok" and set(quota(tmp_path)) == {"2026-09-05"}


def test_budget_rolls_over_when_throttle_crosses_kst_midnight(tmp_path):
    clock = FakeClock(START.replace(hour=23, minute=59, second=59, microsecond=650000))
    summary, _ = run(tmp_path, {"status": "013"}, {"status": "013"}, clock=clock, stage="list", max_requests=1)
    assert summary["status"] == "ok"
    assert [value["requests"] for value in quota(tmp_path).values()] == [1, 1]


def test_dry_run_prints_plan_without_env_session_requests_or_files(tmp_path, capsys):
    directory = tmp_path / "new-data"
    session = FakeSession()
    plan = backfill("2023-01-01", "20230103", data_dir=directory, api_key=KEY,
                    session=session, clock=FakeClock())
    assert plan["status"] == "dry_run" and plan["days_to_list"] == ["20230101", "20230102", "20230103"]
    assert plan["known_pending_details"] == 0
    assert plan["pending_details_by_category"] == dict.fromkeys((name for name, _ in CATEGORY_REPORTS), 0)
    assert plan["filtered_pending_details"] == 0
    assert plan["skipped_missing_stock_code"] == 0
    assert not directory.exists() and session.calls == []
    output = capsys.readouterr()
    assert json.loads(output.out) == plan and KEY not in output.out + output.err


@pytest.mark.parametrize("stock_code", ["005930", ""])
def test_dry_run_counts_saved_pending_details_without_writes(tmp_path, capsys, stock_code):
    rows = [row(1, title="전환청구권행사", stock_code=stock_code), row(2, title="기타공시")]
    run(tmp_path, page(1, rows), {"status": "013"}, stage="list")
    before = snapshot(tmp_path)
    session = FakeSession()
    plan = backfill(DAY, "20230104", stage="details", data_dir=tmp_path, clock=FakeClock(), session=session)
    assert plan["days_to_list"] == [] and plan["known_pending_details"] == 1
    assert plan["unlisted_days"] == ["20230104"]
    assert plan["skipped_missing_stock_code"] == 0 and session.calls == []
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("categories,filtered_count", [
    (None, 9), ([], 9), (["contract", "convertible_bond", "bond_subtype", "capital_raise"], 4),
    (["contract"], 0),
])
def test_dry_run_counts_all_categories_and_filter_without_requests(tmp_path, capsys, categories, filtered_count):
    rows = [row(number, title=title, stock_code="" if category == "buyback" else "005930")
            for number, (category, title) in enumerate(CATEGORY_REPORTS, 1)]
    rows += [row(10, title="단일판매ㆍ공급계약체결"), row(11, title="실적발표"),
             row(12, title="전환청구권행사", stock_code="")]
    run(tmp_path, page(1, rows), {"status": "013"}, stage="list")
    run(tmp_path, document(), document(), stage="details", categories=["contract"])
    run(tmp_path, stage="details", categories=["buyback"])
    before = snapshot(tmp_path)
    session = FakeSession()
    plan = backfill(DAY, "20230104", stage="details", categories=categories, data_dir=tmp_path,
                    clock=FakeClock(), session=session)
    expected = dict.fromkeys((name for name, _ in CATEGORY_REPORTS), 1)
    expected.update(contract=0, buyback=0, earnings=2, bond_subtype=2)
    assert plan["pending_details_by_category"] == expected
    assert plan["known_pending_details"] == sum(expected.values()) == 9
    assert plan["filtered_pending_details"] == filtered_count
    assert plan["categories"] == (categories or None)
    assert plan["unlisted_days"] == ["20230104"] and plan["days_to_list"] == []
    assert session.calls == [] and snapshot(tmp_path) == before
    assert json.loads(capsys.readouterr().out) == plan


def test_details_stage_only_reads_existing_listings(tmp_path):
    summary, session = run(tmp_path, stage="details")
    assert summary["status"] == "ok" and summary["unlisted_days"] == [DAY]
    assert session.calls == []


@pytest.mark.parametrize("stage", ["list", "details"])
def test_archive_published_before_checkpoint_is_recovered_without_repeating_request(tmp_path, monkeypatch, stage):
    selected = row(1, title="단일판매ㆍ공급계약체결")
    if stage == "details":
        run(tmp_path, page(1, [selected]), {"status": "013"}, stage="list")
    write = module._write_json

    def interrupt_checkpoint(path, value):
        if path.name == "_state.json":
            raise OSError("fixture checkpoint interruption")
        return write(path, value)

    monkeypatch.setattr(module, "_write_json", interrupt_checkpoint)
    with pytest.raises(OSError, match="checkpoint interruption"):
        run(tmp_path, *([page(1, [selected]), {"status": "013"}] if stage == "list" else [document()]), stage=stage)
    monkeypatch.setattr(module, "_write_json", write)
    summary, session = run(tmp_path, stage=stage)
    assert summary["status"] == "ok" and session.calls == []
    assert state(tmp_path)["completed_listing_days"] == [DAY]
    if stage == "details":
        assert state(tmp_path)["completed_detail_rcept_nos"] == [selected["rcept_no"]]


def test_completed_archive_missing_on_disk_fails_clearly(tmp_path):
    run(tmp_path, {"status": "013"}, {"status": "013"}, stage="list")
    archive(tmp_path).unlink()
    with pytest.raises(ValueError, match="archive is missing"):
        run(tmp_path, stage="list")


def test_completed_days_do_not_reload_full_bodies_on_resume(tmp_path, monkeypatch):
    run(tmp_path, page(1, [row(1, title="단일판매ㆍ공급계약체결")]), {"status": "013"}, document())
    read = module.read_rows

    def listing_only(path):
        if path.parent.parent.name == "docs":
            pytest.fail("Completed bodies must not be loaded on restart")
        return read(path)

    monkeypatch.setattr(module, "read_rows", listing_only)
    summary, session = run(tmp_path)
    assert summary["status"] == "ok" and session.calls == []


def test_concurrent_runs_share_the_persisted_daily_cap(tmp_path):
    barrier = Barrier(2)

    def collect(day):
        barrier.wait(timeout=5)
        return run(tmp_path, {"status": "013"}, {"status": "013"},
                   from_date=day, to_date=day, stage="list", max_requests=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(collect, ["20230103", "20230104"]))
    assert sorted(summary["status"] for summary, _ in results) == ["ok", "quota_reached"]
    assert sum(len(session.calls) for _, session in results) == 3
    assert quota(tmp_path)["2026-09-04"]["requests"] == 3
    assert len(state(tmp_path)["completed_listing_days"]) == 1


def test_cli_dry_run_does_not_even_read_environment_variables(tmp_path, monkeypatch, capsys):
    def denied(*args, **kwargs):
        pytest.fail("Dry-run CLI must not read environment variables")

    monkeypatch.setattr(cli, "os", SimpleNamespace(getenv=denied))
    monkeypatch.setattr("sys.argv", ["dart_backfill", "--from-date", DAY, "--to-date", DAY,
                                    "--stage", "list", "--data-dir", str(tmp_path / "cli-data"),
                                    "--categories", "contract, convertible_bond,bond_subtype,capital_raise"])
    cli.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "dry_run" and summary["days_to_list"] == [DAY]
    assert summary["categories"] == ["contract", "convertible_bond", "bond_subtype", "capital_raise"]
    assert not (tmp_path / "cli-data").exists()


def test_cli_execute_loads_env_once_and_prints_one_json_summary(tmp_path, monkeypatch, capsys):
    loaded = []
    monkeypatch.setattr(cli, "load_project_env", lambda: loaded.append(True))
    monkeypatch.setattr(cli, "os", SimpleNamespace(getenv=lambda key, default=None:
        {"DART_API_KEY": KEY, "HQA_DATA_DIR": str(tmp_path)}.get(key, default)))
    session, clock = FakeSession({"status": "013"}, {"status": "013"}), FakeClock()

    def injected(*args, **kwargs):
        return backfill(*args, **kwargs, session=session, clock=clock, sleeper=clock.sleep)

    monkeypatch.setattr(cli, "backfill", injected)
    monkeypatch.setattr("sys.argv", ["dart_backfill", "--from-date", DAY, "--to-date", DAY,
                                    "--stage", "list", "--max-requests", "2", "--execute"])
    cli.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ok" and summary["requests_made"] == 2 and loaded == [True]


@pytest.mark.parametrize("changes", [
    {"from_date": "20230104", "to_date": DAY}, {"from_date": "20230230"},
    {"to_date": "2026-09-04"}, {"stage": "invalid"}, {"max_requests": True},
    {"max_requests": 0}, {"max_retries": 0}, {"request_interval": float("nan")},
])
def test_invalid_options_fail_before_requests_or_writes(tmp_path, changes):
    with pytest.raises(ValueError):
        run(tmp_path, **changes)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("xml", [True, False])
def test_maintenance_stops_details_without_item_completion_or_retries(tmp_path, xml):
    items = [row(n, title="실적발표") for n in (1, 2)]
    module.write_rows(archive(tmp_path), items)
    maintenance = b"<result><status>800</status><message>maintenance</message></result>" if xml else {"status": "800", "message": KEY}
    clock = FakeClock()
    result, session = run(tmp_path, maintenance, stage="details", clock=clock, max_retries=3)
    assert result["status"] == "provider_maintenance" and len(session.calls) == 1
    assert result["details_saved"] == result["documents_not_found"] == result["bodies_unavailable"] == 0
    assert result["known_pending_details"] == 2
    assert state(tmp_path)["completed_detail_rcept_nos"] == []
    assert not archive(tmp_path, "docs").exists()
    assert quota(tmp_path)[START.date().isoformat()] == {"requests": 1, "provider_limited": False}
    observed = json.loads((root(tmp_path) / "_provider_maintenance.json").read_text())["endpoints"]["document.xml"]
    assert observed["first_seen_at"] == observed["last_seen_at"] == clock().isoformat()
    clock.now += timedelta(days=1)
    repeated, second = run(tmp_path, maintenance, stage="details", clock=clock)
    assert repeated["known_pending_details"] == 2 and len(second.calls) == 1
    updated = json.loads((root(tmp_path) / "_provider_maintenance.json").read_text())["endpoints"]["document.xml"]
    assert updated["first_seen_at"] == observed["first_seen_at"] and updated["last_seen_at"] == clock().isoformat()
    resumed, recovered = run(tmp_path, document(), document(), stage="details", clock=clock)
    assert resumed["status"] == "ok" and resumed["details_saved"] == 2 and len(recovered.calls) == 2
    assert not json.loads((root(tmp_path) / "_provider_maintenance.json").read_text())["endpoints"]["document.xml"]["active"]


def test_structured_maintenance_does_not_complete_a_detail(tmp_path):
    module.write_rows(archive(tmp_path), [row(1, title="주요사항보고서(전환사채권발행결정)")])
    result, session = run(tmp_path, {"status": "800"}, stage="details")
    assert result["status"] == "provider_maintenance" and len(session.calls) == 1
    assert not session.calls[0][0].endswith("document.xml")
    assert result["known_pending_details"] == 1 and state(tmp_path)["completed_detail_rcept_nos"] == []
    assert not archive(tmp_path, "docs").exists()


def test_backfill_cli_maintenance_exits_zero(tmp_path, monkeypatch, capsys):
    module.write_rows(archive(tmp_path), [row(1, title="실적발표")])
    session, clock = FakeSession(b"<result><status>800</status></result>"), FakeClock()
    monkeypatch.setattr(cli, "load_project_env", lambda: None)
    monkeypatch.setattr(cli, "os", SimpleNamespace(getenv=lambda key, default=None: KEY if key == "DART_API_KEY" else default))
    monkeypatch.setattr(cli, "backfill", lambda *args, **kwargs: backfill(*args, **kwargs,
        session=session, clock=clock, sleeper=clock.sleep))
    monkeypatch.setattr("sys.argv", ["dart_backfill", "--from-date", DAY, "--to-date", DAY,
        "--stage", "details", "--data-dir", str(tmp_path), "--execute"])
    assert cli.main() is None
    assert json.loads(capsys.readouterr().out)["status"] == "provider_maintenance"


def test_successful_listing_does_not_clear_document_maintenance(tmp_path):
    module.write_rows(archive(tmp_path), [row(1, title="실적발표")])
    maintenance = '<result><status>800</status><message>시스템 점검으로 인한 서비스가 중지 중입니다</message></result>'.encode()
    result, _ = run(tmp_path, maintenance, stage="details")
    assert result["status"] == "provider_maintenance"
    listed, _ = run(tmp_path, {"status": "013"}, {"status": "013"}, stage="list",
                    from_date="20230104", to_date="20230104")
    assert listed["status"] == "ok"
    observed = json.loads((root(tmp_path) / "_provider_maintenance.json").read_text())
    assert observed["endpoints"]["document.xml"]["active"]
    assert state(tmp_path)["completed_detail_rcept_nos"] == []
