from __future__ import annotations

# File role:
# - Resolve a Naver theme keyword into a set of stock targets.

from dataclasses import dataclass
import os
import re
import shutil
from typing import List, Tuple

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector, USER_AGENT


def _first_existing_path(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _resolve_chrome_binary() -> str | None:
    return _first_existing_path(
        os.getenv("CHROME_BINARY"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    )


def _resolve_chromedriver() -> str | None:
    return _first_existing_path(
        os.getenv("CHROMEDRIVER"),
        shutil.which("chromedriver"),
    )


@dataclass
class ThemeStock:
    theme_name: str
    stock_name: str
    stock_code: str


class NaverThemeStockCollector(BaseCollector):
    THEME_LIST_URL = "https://finance.naver.com/sise/theme.naver"

    def _build_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"user-agent={USER_AGENT}")

        chrome_binary = _resolve_chrome_binary()
        if chrome_binary:
            options.binary_location = chrome_binary

        chromedriver = _resolve_chromedriver()
        if chromedriver:
            return webdriver.Chrome(service=Service(chromedriver), options=options)

        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    def collect(self, theme_keyword: str, max_stocks: int = 30, max_pages: int = 10) -> List[ThemeStock]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        driver = self._build_driver()
        stocks: List[ThemeStock] = []
        seen_codes = set()

        try:
            theme_links = self._find_theme_links_selenium(driver=driver, theme_keyword=theme_keyword, max_pages=max_pages)
            print(f"[DEBUG][THEME] matched theme links: {len(theme_links)}")

            for theme_name, detail_url in theme_links:
                driver.get(detail_url)
                from selenium.webdriver.support.ui import WebDriverWait

                WebDriverWait(driver, 10).until(lambda d: "code=" in d.page_source or "item/main" in d.page_source)
                soup = BeautifulSoup(driver.page_source, "html.parser")

                stock_links = soup.select("a[href*='item/main.naver?code=']") or soup.select("a[href*='item/main']") or soup.select("a[href*='code=']")
                print(f"[DEBUG][THEME DETAIL] theme={theme_name} stock link count={len(stock_links)}")

                for a_tag in stock_links:
                    href = a_tag.get("href", "")
                    code_match = re.search(r"code=(\d{6})", href)
                    if not code_match:
                        continue
                    stock_code = code_match.group(1)
                    if stock_code in seen_codes:
                        continue
                    stock_name = a_tag.get_text(" ", strip=True)
                    if not stock_name:
                        continue

                    seen_codes.add(stock_code)
                    stocks.append(ThemeStock(theme_name=theme_name, stock_name=stock_name, stock_code=stock_code))
                    print(f"[DEBUG][THEME DETAIL] stock found: {stock_name} ({stock_code})")
                    if len(stocks) >= max_stocks:
                        return stocks

            return stocks
        finally:
            driver.quit()

    def _find_theme_links_selenium(self, driver, theme_keyword: str, max_pages: int) -> List[Tuple[str, str]]:
        normalized_keyword = theme_keyword.strip().lower()
        links: List[Tuple[str, str]] = []

        for page in range(1, max_pages + 1):
            driver.get(f"{self.THEME_LIST_URL}?page={page}")
            from selenium.webdriver.support.ui import WebDriverWait

            WebDriverWait(driver, 10).until(lambda d: "테마별 시세" in d.page_source or "type=theme" in d.page_source)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            candidates = soup.select("a[href*='sise_group_detail.naver?type=theme']")
            print(f"[DEBUG][THEME] page={page} candidate count={len(candidates)}")

            page_matches = 0
            for a_tag in candidates:
                theme_name = a_tag.get_text(" ", strip=True)
                href = (a_tag.get("href") or "").strip()
                if not theme_name or not href:
                    continue
                if normalized_keyword not in theme_name.lower():
                    continue
                detail_url = f"https://finance.naver.com{href}" if href.startswith("/") else href
                print(f"[DEBUG][THEME] matched theme: {theme_name} -> {detail_url}")
                links.append((theme_name, detail_url))
                page_matches += 1

            print(f"[DEBUG][THEME] page={page} matched count={page_matches}")

        dedup = {detail_url: theme_name for theme_name, detail_url in links}
        return [(name, url) for url, name in dedup.items()]
