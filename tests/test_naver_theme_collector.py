from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from src.ingestion.naver_theme import NaverThemeStockCollector, ThemeStock, ThemeTargets
from src.ingestion.theme_targets import ThemeTargetStore
from src.ingestion.types import StockTarget
from scripts.data.discover import save_collected_themes


def test_extract_theme_links_supports_all_themes_without_keyword():
    html = """
    <a href="/sise/sise_group_detail.naver?type=theme&no=1">반도체</a>
    <a href="/sise/sise_group_detail.naver?type=theme&no=2">2차전지</a>
    <a href="/item/main.naver?code=005930">삼성전자</a>
    """

    links = NaverThemeStockCollector.extract_theme_links(html)

    assert links == [
        ("반도체", "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no=1"),
        ("2차전지", "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no=2"),
    ]


def test_extract_theme_stocks_dedupes_codes_and_limits_count():
    html = """
    <a href="/item/main.naver?code=005930">삼성전자</a>
    <a href="/item/main.naver?code=000660">SK하이닉스</a>
    <a href="/item/main.naver?code=005930">삼성전자</a>
    """

    stocks = NaverThemeStockCollector.extract_theme_stocks(
        html,
        theme_name="반도체",
        max_stocks=10,
    )

    assert stocks == [
        ThemeStock(theme_name="반도체", stock_name="삼성전자", stock_code="005930"),
        ThemeStock(theme_name="반도체", stock_name="SK하이닉스", stock_code="000660"),
    ]


@pytest.mark.parametrize("method", ["html", "selenium"])
@pytest.mark.parametrize("code,valid", [("0015G0", True), ("005930", True), ("0015g0", False),
                                      ("15G0", False), ("0015G0X", False), ("0015-0", False)])
def test_both_theme_parsers_validate_entire_krx_short_code(monkeypatch, method, code, valid):
    html = f'<a href="/item/main.naver?code={code}&amp;other=1#chart">Stock</a>'
    if method == "html":
        stocks = NaverThemeStockCollector.extract_theme_stocks(html, "theme")
    else:
        driver = SimpleNamespace(page_source=html, get=lambda _: None, quit=lambda: None)
        monkeypatch.setitem(sys.modules, "selenium.webdriver.support.ui", SimpleNamespace(
            WebDriverWait=lambda driver, timeout: SimpleNamespace(until=lambda condition: condition(driver))))
        collector = NaverThemeStockCollector()
        monkeypatch.setattr(collector, "_build_driver", lambda: driver)
        monkeypatch.setattr(collector, "_find_theme_links_selenium", lambda **kwargs: [("theme", "https://example.test/theme")])
        stocks = collector.collect("theme")
    assert [stock.stock_code for stock in stocks] == ([code] if valid else [])


def test_save_collected_themes_writes_theme_target_files(tmp_path):
    collected = [
        ThemeTargets(
            theme_name="반도체",
            detail_url="https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no=1",
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
