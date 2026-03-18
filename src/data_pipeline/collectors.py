from __future__ import annotations

"""네이버 뉴스 / DART / 네이버 종토방 / 네이버 테마 종목 수집기."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import re
import time

import requests
from requests import HTTPError

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class CrawledDocument:
    source_type: str
    title: str
    content: str
    url: str
    stock_name: Optional[str] = None
    stock_code: Optional[str] = None
    published_at: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ThemeStock:
    theme_name: str
    stock_name: str
    stock_code: str


class BaseCollector:
    def __init__(self, timeout: int = 10):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Connection": "keep-alive",
            }
        )
        self.timeout = timeout

    @staticmethod
    def _to_iso_datetime(
        value: str,
        in_formats: List[str],
        default_time: str = "00:00:00",
    ) -> str:
        """
        다양한 날짜 문자열을 ISO datetime(YYYY-MM-DDTHH:MM:SS)으로 변환.
        """
        if not value:
            return ""

        raw = value.strip()
        for fmt in in_formats:
            try:
                dt = datetime.strptime(raw, fmt)
                # 시간 정보가 없는 포맷이면 기본 시간 부여
                if "%H" not in fmt and "%M" not in fmt and "%S" not in fmt:
                    hh, mm, ss = map(int, default_time.split(":"))
                    dt = dt.replace(hour=hh, minute=mm, second=ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue

        return ""


class NaverNewsCollector(BaseCollector):
    SEARCH_URL = "https://search.naver.com/search.naver"

    def __init__(self, timeout: int = 10):
        super().__init__(timeout=timeout)
        self.session.headers.update({"Referer": "https://search.naver.com/"})

    def collect(
        self,
        keyword: str,
        max_items: int = 20,
        from_date: str = "",
        to_date: str = "",
        **kwargs,
    ) -> List[CrawledDocument]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[CrawledDocument] = []
        seen_urls = set()
        start = 1

        while len(docs) < max_items:
            params = {
                "where": "news",
                "query": keyword,
                "start": start,
                "sort": 1,
            }

            try:
                response = self.session.get(
                    self.SEARCH_URL,
                    params=params,
                    timeout=self.timeout,
                )

                if response.status_code == 403:
                    print(f"[WARN][NEWS] 403 blocked for keyword='{keyword}', start={start}")
                    break

                response.raise_for_status()

            except HTTPError as e:
                print(f"[WARN][NEWS] HTTP error for keyword='{keyword}', start={start}: {e}")
                break
            except requests.RequestException as e:
                print(f"[WARN][NEWS] Request failed for keyword='{keyword}', start={start}: {e}")
                break

            soup = BeautifulSoup(response.text, "html.parser")

            items = soup.select("div.news_area")
            if not items:
                items = soup.select("div.sds-comps-text.sds-comps-text-type-body1")
            if not items:
                link_nodes = soup.select(
                    "a.news_tit, a[href*='news.naver.com'], a[href*='n.news.naver.com']"
                )
                candidate_blocks = []
                for node in link_nodes:
                    parent = node.find_parent("div")
                    if parent is not None:
                        candidate_blocks.append(parent)
                items = candidate_blocks

            if not items:
                print(f"[DEBUG][NEWS] no items for keyword='{keyword}', start={start}")
                break

            before_count = len(docs)

            for item in items:
                if len(docs) >= max_items:
                    break

                title_el = (
                    item.select_one("a.news_tit")
                    or item.select_one("a[href*='news.naver.com']")
                    or item.select_one("a[href*='n.news.naver.com']")
                )

                summary_el = (
                    item.select_one("div.news_dsc")
                    or item.select_one("div.dsc_wrap")
                    or item.select_one("span.api_txt_lines.dsc_txt_wrap")
                    or item.select_one("div.api_txt_lines")
                )

                press_el = (
                    item.select_one("a.info.press")
                    or item.select_one("span.info.press")
                    or item.select_one("span.sds-comps-text-type-body2")
                )

                if title_el is None:
                    continue

                url = title_el.get("href", "").strip()
                title = title_el.get_text(" ", strip=True)

                if not url or not title or url in seen_urls:
                    continue

                info_nodes = item.select("span.info")
                raw_date_text = self._extract_news_date_text(info_nodes)

                # info에서 못 찾으면 press 문자열에서도 한 번 더 시도
                if not raw_date_text and press_el is not None:
                    press_text = press_el.get_text(" ", strip=True)
                    raw_date_text = self._extract_news_date_text_from_text(press_text)

                normalized_date = self._normalize_news_date(raw_date_text)

                # 날짜가 없으면 제외: 기간 필터 신뢰성 확보
                if not normalized_date:
                    continue

                compact = normalized_date[:10].replace("-", "")
                if from_date and compact < from_date:
                    continue
                if to_date and compact > to_date:
                    continue

                seen_urls.add(url)

                docs.append(
                    CrawledDocument(
                        source_type="news",
                        title=title,
                        content=summary_el.get_text(" ", strip=True) if summary_el else title,
                        url=url,
                        published_at=normalized_date,
                        metadata={
                            "keyword": keyword,
                            "press": press_el.get_text(" ", strip=True) if press_el else "",
                            "raw_date_text": raw_date_text,
                        },
                    )
                )

            if len(docs) == before_count:
                break

            start += 10
            time.sleep(0.8)

        return docs

    def _extract_news_date_text(self, info_nodes) -> str:
        candidates = []
        for node in info_nodes:
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            candidates.append(text)

        for text in candidates:
            extracted = self._extract_news_date_text_from_text(text)
            if extracted:
                return extracted

        return ""

    def _extract_news_date_text_from_text(self, text: str) -> str:
        if not text:
            return ""

        text = text.strip()

        patterns = [
            r"\d{4}\.\d{1,2}\.\d{1,2}\.?",
            r"\d+\s*분\s*전",
            r"\d+\s*시간\s*전",
            r"\d+\s*일\s*전",
            r"\d+\s*주\s*전",
        ]

        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(0)

        return ""

    def _normalize_news_date(self, raw: str) -> str:
        if not raw:
            return ""

        raw = raw.strip()
        now = datetime.now()

        m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?", raw)
        if m:
            y, mo, d = m.groups()
            dt = datetime(int(y), int(mo), int(d), 0, 0, 0)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        m = re.search(r"(\d+)\s*분\s*전", raw)
        if m:
            dt = now - timedelta(minutes=int(m.group(1)))
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        m = re.search(r"(\d+)\s*시간\s*전", raw)
        if m:
            dt = now - timedelta(hours=int(m.group(1)))
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        m = re.search(r"(\d+)\s*일\s*전", raw)
        if m:
            dt = now - timedelta(days=int(m.group(1)))
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        m = re.search(r"(\d+)\s*주\s*전", raw)
        if m:
            dt = now - timedelta(weeks=int(m.group(1)))
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        return ""


class DartDisclosureCollector(BaseCollector):
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"

    def __init__(self, api_key: str, timeout: int = 10):
        super().__init__(timeout=timeout)
        self.api_key = api_key

    def collect(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_count: int = 100,
    ) -> List[CrawledDocument]:
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_count": page_count,
        }

        response = self.session.get(self.LIST_URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") != "000":
            return []

        docs: List[CrawledDocument] = []
        for item in payload.get("list", []):
            title = item.get("report_nm", "")
            rcept_no = item.get("rcept_no", "")
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""

            published_at = self._to_iso_datetime(
                item.get("rcept_dt", ""),
                ["%Y%m%d"],
                default_time="00:00:00",
            )

            docs.append(
                CrawledDocument(
                    source_type="dart",
                    title=title,
                    content=f"{item.get('corp_name', '')} 공시: {title}",
                    url=url,
                    stock_code=item.get("stock_code"),
                    published_at=published_at,
                    metadata={
                        "corp_name": item.get("corp_name", ""),
                        "flr_nm": item.get("flr_nm", ""),
                        "rcept_no": rcept_no,
                    },
                )
            )

        return docs


class NaverStockForumCollector(BaseCollector):
    def collect(self, stock_code: str, pages: int = 3) -> List[CrawledDocument]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[CrawledDocument] = []

        for page in range(1, pages + 1):
            url = f"https://finance.naver.com/item/board.naver?code={stock_code}&page={page}"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select("table.type2 tr")

            for row in rows:
                title_el = row.select_one("td.title a")
                date_el = row.select_one("td:nth-of-type(1)")

                if title_el is None:
                    continue

                title = title_el.get_text(" ", strip=True)
                href = title_el.get("href", "")
                full_url = f"https://finance.naver.com{href}" if href.startswith("/") else href

                raw_date = date_el.get_text(strip=True) if date_el else ""
                published_at = self._to_iso_datetime(
                    raw_date,
                    [
                        "%Y.%m.%d %H:%M",
                        "%Y.%m.%d",
                    ],
                    default_time="00:00:00",
                )

                docs.append(
                    CrawledDocument(
                        source_type="forum",
                        title=title,
                        content=_clean_forum_title(title),
                        url=full_url,
                        stock_code=stock_code,
                        published_at=published_at,
                        metadata={
                            "page": str(page),
                            "raw_date_text": raw_date,
                        },
                    )
                )

        return docs


class NaverThemeStockCollector(BaseCollector):
    THEME_LIST_URL = "https://finance.naver.com/sise/theme.naver"

    def _build_driver(self) -> webdriver.Chrome:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"user-agent={USER_AGENT}")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver

    def collect(
        self,
        theme_keyword: str,
        max_stocks: int = 30,
        max_pages: int = 10,
    ) -> List[ThemeStock]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        driver = self._build_driver()
        stocks: List[ThemeStock] = []
        seen_codes = set()

        try:
            theme_links = self._find_theme_links_selenium(
                driver=driver,
                theme_keyword=theme_keyword,
                max_pages=max_pages,
            )
            print(f"[DEBUG][THEME] matched theme links: {len(theme_links)}")

            for theme_name, detail_url in theme_links:
                driver.get(detail_url)

                WebDriverWait(driver, 10).until(
                    lambda d: "code=" in d.page_source or "item/main" in d.page_source
                )

                soup = BeautifulSoup(driver.page_source, "html.parser")

                stock_links = soup.select("a[href*='item/main.naver?code=']")
                if not stock_links:
                    stock_links = soup.select("a[href*='item/main']")
                if not stock_links:
                    stock_links = soup.select("a[href*='code=']")

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
                    stocks.append(
                        ThemeStock(
                            theme_name=theme_name,
                            stock_name=stock_name,
                            stock_code=stock_code,
                        )
                    )

                    print(f"[DEBUG][THEME DETAIL] stock found: {stock_name} ({stock_code})")

                    if len(stocks) >= max_stocks:
                        return stocks

            return stocks

        finally:
            driver.quit()

    def _find_theme_links_selenium(
        self,
        driver: webdriver.Chrome,
        theme_keyword: str,
        max_pages: int,
    ) -> List[tuple[str, str]]:
        normalized_keyword = theme_keyword.strip().lower()
        links: List[tuple[str, str]] = []

        for page in range(1, max_pages + 1):
            url = f"{self.THEME_LIST_URL}?page={page}"
            driver.get(url)

            WebDriverWait(driver, 10).until(
                lambda d: "테마별 시세" in d.page_source or "type=theme" in d.page_source
            )

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

        dedup = {}
        for theme_name, detail_url in links:
            dedup[detail_url] = theme_name

        return [(name, url) for url, name in dedup.items()]


def _clean_forum_title(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()