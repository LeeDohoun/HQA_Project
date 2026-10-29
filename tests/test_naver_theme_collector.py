"""Theme discovery reads Naver's mobile JSON API; the fixtures copy its response shape."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.ingestion.naver_theme import NaverThemeApiError, NaverThemeStockCollector, ThemeStock, ThemeTargets
from src.ingestion.theme_targets import ThemeTargetStore
from src.ingestion.types import StockTarget
from scripts.data.discover import save_collected_themes

API = NaverThemeStockCollector.API_URL
FIXTURES = Path(__file__).with_name("fixtures") / "naver_theme"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Response:
    def __init__(self, status, payload=None, text=None):
        self.status_code, self.payload, self.text = status, payload, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        if self.text is not None:
            raise requests.exceptions.JSONDecodeError("Expecting value", self.text, 0)
        return self.payload


class FakeNaver:
    """The theme API over the fixtures: pages of at most 100 rows, 404 past the last page.

    ``shifted_reads`` moves a theme from page 2 to page 1 between the two requests of a read, as the
    live change-rate order can during market hours.
    """

    def __init__(self, shifted_reads=0):
        self.bodies = {API: fixture("theme.json")}
        for no in (64, 362, 373, 579):
            self.bodies[f"{API}/{no}"] = fixture(f"theme_{no}.json")
        self.failures = {}
        self.shifted_reads = shifted_reads
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(SimpleNamespace(url=url, params=dict(params), headers=dict(headers or {}),
                                          timeout=timeout))
        failure = self.failures.get(url)
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return failure
        page, size = params["page"], params["pageSize"]
        if size > 100:
            return Response(400)
        if url not in self.bodies:  # An unknown theme number answers 200 with nothing in it.
            return Response(200, {"stocks": [], "groupInfo": {"no": 0, "totalCount": 0}, "totalCount": 0,
                                  "page": page, "pageSize": size})
        body = self.bodies[url]
        key = "groups" if url == API else "stocks"
        if key not in body:
            return Response(200, body)
        rows = body[key][(page - 1) * size:page * size]
        if url == API and page == 2 and self.shifted_reads:
            self.shifted_reads -= 1
            rows = [body[key][1], body[key][3]]  # The second theme slid down; the third moved up unseen.
        if page > 1 and not rows:
            return Response(404)
        return Response(200, {**body, key: rows, "page": page, "pageSize": size})


class Clock:
    """Monotonic time that moves only when the collector sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_collector(fake, clock=None, page_size=None):
    clock = clock or Clock()
    collector = NaverThemeStockCollector(session=fake, sleep=clock.sleep, clock=lambda: clock.now,
                                         backoff_seconds=0)
    if page_size:
        collector.PAGE_SIZE = page_size
    return collector


def test_discovery_reads_every_theme_and_member_from_the_json_api():
    fake, clock = FakeNaver(), Clock()
    themes = make_collector(fake, clock).collect_all_themes()

    assert [(theme.theme_name, theme.detail_url) for theme in themes] == [
        ("2차전지", f"{API}/64"), ("자율주행차", f"{API}/362"),
        ("5G(5세대 이동통신)", f"{API}/373"), ("2차전지(나트륨이온)", f"{API}/579")]
    assert themes[0].stocks == [
        ThemeStock(theme_name="2차전지", stock_name="LG에너지솔루션", stock_code="373220"),
        ThemeStock(theme_name="2차전지", stock_name="삼성SDI", stock_code="006400"),
        ThemeStock(theme_name="2차전지", stock_name="에코프로비엠", stock_code="247540"),
        ThemeStock(theme_name="2차전지", stock_name="유진테크놀로지", stock_code="240600"),
    ]
    assert [stock.stock_code for stock in themes[1].stocks] == ["000660", "005380", "012330"]
    assert [call.url for call in fake.calls] == [API] + [f"{API}/{no}" for no in (64, 362, 373, 579)]
    for call in fake.calls:
        assert call.params == {"page": 1, "pageSize": 100}
        assert call.headers == {"Accept": "application/json"} and call.timeout == 20
    assert clock.sleeps == [pytest.approx(0.4)] * (len(fake.calls) - 1)


def test_pages_are_read_up_to_the_reported_total_and_never_past_it():
    fake = FakeNaver()
    themes = make_collector(fake, page_size=2).collect_all_themes()

    assert [len(theme.stocks) for theme in themes] == [4, 3, 2, 2]
    assert [(call.url.removeprefix(API), call.params["page"]) for call in fake.calls] == [
        ("", 1), ("", 2), ("/64", 1), ("/64", 2), ("/64", 3), ("/362", 1), ("/362", 2), ("/373", 1), ("/579", 1)]
    assert {call.params["pageSize"] for call in fake.calls} == {2}


def test_a_keyword_takes_the_exact_theme_first_and_members_by_market_value_not_returns(capsys):
    fake = FakeNaver()
    stocks = make_collector(fake).collect("2차전지", max_stocks=3)

    # Naver lists 2차전지(나트륨이온) first and, inside 2차전지, the day's +30% small cap first.
    assert [(stock.theme_name, stock.stock_code) for stock in stocks] == [
        ("2차전지", "373220"), ("2차전지", "006400"), ("2차전지", "247540")]
    assert [call.url for call in fake.calls] == [API, f"{API}/64"]
    assert "matched themes: ['2차전지', '2차전지(나트륨이온)']" in capsys.readouterr().out


def test_a_keyword_merges_every_matching_theme_without_repeating_a_stock():
    stocks = make_collector(FakeNaver()).collect("2차전지")
    assert [(stock.theme_name, stock.stock_code) for stock in stocks] == [
        ("2차전지", "373220"), ("2차전지", "006400"), ("2차전지", "247540"), ("2차전지", "240600"),
        ("2차전지(나트륨이온)", "005070")]


def test_keyword_matching_ignores_case_and_an_unknown_keyword_finds_nothing():
    stocks = make_collector(FakeNaver()).collect(" 5g ")
    assert [(stock.theme_name, stock.stock_code) for stock in stocks] == [
        ("5G(5세대 이동통신)", "017670"), ("5G(5세대 이동통신)", "010170")]
    fake = FakeNaver()
    assert make_collector(fake).collect("우주항공") == []
    assert [call.url for call in fake.calls] == [API]


def test_alphanumeric_krx_codes_are_reported_not_silently_dropped(capsys):
    stocks = make_collector(FakeNaver()).collect("2차전지")
    assert "0011A0" not in [stock.stock_code for stock in stocks]
    assert "skipped alphanumeric KRX code 0011A0 in theme 2차전지" in capsys.readouterr().out


def _server_error(fake):
    fake.failures[API] = Response(503)


def _html_instead_of_json(fake):
    # What finance.naver.com sends since the move: a page that the browser fills in later.
    fake.failures[API] = Response(200, text="<!DOCTYPE html><html><div id=\"__next\"></div></html>")


def _transport_error(fake):
    fake.failures[API] = requests.ConnectionError("connection reset")


def _no_group_list(fake):
    fake.bodies[API] = {"code": "INVALID", "message": "changed"}


def _no_total(fake):
    del fake.bodies[API]["totalCount"]


def _no_themes(fake):
    fake.bodies[API] = {**fake.bodies[API], "groups": [], "totalCount": 0}


def _theme_without_name(fake):
    fake.bodies[API]["groups"][1]["name"] = " "


def _removed_theme(fake):
    del fake.bodies[f"{API}/64"]


def _member_without_code(fake):
    del fake.bodies[f"{API}/64"]["stocks"][0]["itemCode"]


def _member_with_odd_code(fake):
    fake.bodies[f"{API}/64"]["stocks"][0]["itemCode"] = "A240600"


@pytest.mark.parametrize("break_api,message", [
    (_server_error, "theme list page 1 request failed status=503"),
    (_html_instead_of_json, "theme list page 1 did not answer with a JSON object"),
    (_transport_error, "theme list page 1 request failed"),
    (_no_group_list, "theme list page 1 has no 'groups' list or totalCount"),
    (_no_total, "theme list page 1 has no 'groups' list or totalCount"),
    (_no_themes, "listed no themes"),
    (_theme_without_name, "theme without a number or a name"),
    (_removed_theme, "answered theme 0 for theme 64"),
    (_member_without_code, "member without a six-character code or a name"),
    (_member_with_odd_code, "itemCode='A240600'"),
])
@pytest.mark.parametrize("read", ["catalog", "keyword"])
def test_api_failures_raise_instead_of_returning_no_themes(break_api, message, read):
    fake = FakeNaver()
    break_api(fake)
    collector = make_collector(fake)
    with pytest.raises(NaverThemeApiError, match=re.escape(message)):
        collector.collect_all_themes() if read == "catalog" else collector.collect("2차전지")


def test_http_failures_are_retried_before_they_raise():
    fake = FakeNaver()
    _server_error(fake)
    with pytest.raises(NaverThemeApiError):
        make_collector(fake).collect_all_themes()
    assert len(fake.calls) == 3


def test_rows_that_move_between_pages_are_read_again(capsys):
    fake = FakeNaver(shifted_reads=1)
    themes = make_collector(fake, page_size=2).collect_all_themes()

    assert len(themes) == 4
    assert ("theme list read 3 of 4 rows; the pages follow the live change rate (attempt 1/3)"
            in capsys.readouterr().out)
    assert [call.params["page"] for call in fake.calls if call.url == API] == [1, 2, 1, 2]


def test_a_listing_that_never_settles_fails_loudly():
    with pytest.raises(NaverThemeApiError, match=r"theme list stayed incomplete after 3 reads \(3 of 4 rows\)"):
        make_collector(FakeNaver(shifted_reads=99), page_size=2).collect_all_themes()


def test_max_pages_reads_only_that_many_list_pages_and_says_so(capsys):
    themes = make_collector(FakeNaver(), page_size=2).collect_all_themes(max_pages=1)

    assert [theme.theme_name for theme in themes] == ["2차전지", "2차전지(나트륨이온)"]
    assert "max_pages=1 read 2 of 4 themes" in capsys.readouterr().out


def test_a_theme_larger_than_the_member_page_limit_fails_loudly():
    collector = make_collector(FakeNaver(), page_size=2)
    collector.MAX_MEMBER_PAGES = 2
    with pytest.raises(NaverThemeApiError, match="theme 64 lists 5 members, more than 2 pages"):
        collector.collect_all_themes()


def test_limits_below_one_are_refused():
    collector = make_collector(FakeNaver())
    for call in (lambda: collector.collect("2차전지", max_stocks=0),
                 lambda: collector.collect("2차전지", max_pages=0),
                 lambda: collector.collect_all_themes(max_stocks_per_theme=0)):
        with pytest.raises(ValueError):
            call()


def test_discover_catalog_mode_saves_every_theme_and_leaves_the_targets_alone(tmp_path, monkeypatch):
    from scripts.data import discover

    fake = FakeNaver()
    monkeypatch.setattr(discover, "NaverThemeStockCollector", lambda: make_collector(fake))
    monkeypatch.setattr(sys, "argv", ["discover", "--data-dir", str(tmp_path),
                                      "--summary-path", str(tmp_path / "summary.json")])
    discover.main()

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert (summary["saved_theme_count"], summary["saved_stock_count"]) == (4, 11)
    rows = [json.loads(line) for line in
            (tmp_path / "theme_catalog" / "2차전지.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["stock_code"] for row in rows] == ["373220", "006400", "247540", "240600"]
    assert {row["detail_url"] for row in rows} == {f"{API}/64"}
    assert not (tmp_path / "raw").exists()


def test_refresh_targets_saves_the_keyword_members_and_reports_api_failures(tmp_path, monkeypatch):
    from scripts.data import collect as theme_pipeline

    fake = FakeNaver()
    monkeypatch.setattr(theme_pipeline, "NaverThemeStockCollector", lambda: make_collector(fake))
    monkeypatch.setattr(sys, "argv", ["collect", "--theme", "2차전지", "--data-dir", str(tmp_path),
                                      "--refresh-targets", "--save-only", "--theme-max-stocks", "3",
                                      "--enabled-sources", "news"])
    assert theme_pipeline.main() == 0
    assert ThemeTargetStore(str(tmp_path)).load_targets("2차전지") == [
        StockTarget(stock_name="LG에너지솔루션", stock_code="373220", corp_code=""),
        StockTarget(stock_name="삼성SDI", stock_code="006400", corp_code=""),
        StockTarget(stock_name="에코프로비엠", stock_code="247540", corp_code=""),
    ]

    monkeypatch.setattr(sys, "argv", ["collect", "--theme", "우주항공", "--data-dir", str(tmp_path),
                                      "--refresh-targets", "--save-only", "--enabled-sources", "news"])
    assert theme_pipeline.main() == 1
    report = json.loads((tmp_path / "reports" / "우주항공_ingestion_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "error" and "이름에 이 키워드가 들어간 Naver 테마가 없거나" in report["reason"]

    monkeypatch.setattr(sys, "argv", ["collect", "--theme", "2차전지", "--data-dir", str(tmp_path),
                                      "--refresh-targets", "--save-only", "--enabled-sources", "news"])
    _server_error(fake)
    assert theme_pipeline.main() == 1
    report = json.loads((tmp_path / "reports" / "2차전지_ingestion_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "error" and "request failed status=503" in report["reason"]
    assert len(ThemeTargetStore(str(tmp_path)).load_targets("2차전지")) == 3


def test_save_collected_themes_writes_theme_target_files(tmp_path):
    collected = [
        ThemeTargets(
            theme_name="반도체",
            detail_url=f"{API}/1",
            stocks=[
                ThemeStock(theme_name="반도체", stock_name="삼성전자", stock_code="005930"),
                ThemeStock(theme_name="반도체", stock_name="SK하이닉스", stock_code="000660"),
            ],
        )
    ]

    summary = save_collected_themes(collected, data_dir=str(tmp_path), overwrite=True)

    assert summary["saved_theme_count"] == 1
    store = ThemeTargetStore(data_dir=str(tmp_path))
    loaded = store.load_targets("반도체")
    assert loaded == [
        StockTarget(stock_name="삼성전자", stock_code="005930", corp_code=""),
        StockTarget(stock_name="SK하이닉스", stock_code="000660", corp_code=""),
    ]
    meta = json.loads(store.get_meta_path("반도체").read_text(encoding="utf-8"))
    assert meta["theme_name"] == "반도체"
