from __future__ import annotations

"""네이버 뉴스 / DART / 네이버 종토방 수집기."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import re

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # optional dependency in minimal runtime
    BeautifulSoup = None


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class CrawledDocument:
    """RAG 적재 전 표준 문서 포맷."""

    source_type: str  # news | dart | forum
    title: str
    content: str
    url: str
    stock_name: Optional[str] = None
    stock_code: Optional[str] = None
    published_at: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


class BaseCollector:
    def __init__(self, timeout: int = 10):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout = timeout


class NaverNewsCollector(BaseCollector):
    """네이버 뉴스 검색 페이지 기반 수집기."""

    SEARCH_URL = "https://search.naver.com/search.naver"

    def collect(self, keyword: str, max_items: int = 20) -> List[CrawledDocument]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[CrawledDocument] = []
        start = 1

        while len(docs) < max_items:
            params = {
                "where": "news",
                "query": keyword,
                "start": start,
                "sort": 1,
            }
            response = self.session.get(self.SEARCH_URL, params=params, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select("div.news_area")
            if not items:
                break

            for item in items:
                if len(docs) >= max_items:
                    break
                title_el = item.select_one("a.news_tit")
                summary_el = item.select_one("div.news_dsc")
                press_el = item.select_one("a.info.press")

                if not title_el:
                    continue

                docs.append(
                    CrawledDocument(
                        source_type="news",
                        title=title_el.get_text(strip=True),
                        content=(summary_el.get_text(" ", strip=True) if summary_el else ""),
                        url=title_el.get("href", ""),
                        published_at=datetime.utcnow().isoformat(),
                        metadata={
                            "keyword": keyword,
                            "press": press_el.get_text(strip=True) if press_el else "",
                        },
                    )
                )
            start += 10

        return docs


class DartDisclosureCollector(BaseCollector):
    """DART Open API 공시 목록 수집기."""

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
            docs.append(
                CrawledDocument(
                    source_type="dart",
                    title=title,
                    content=f"{item.get('corp_name', '')} 공시: {title}",
                    url=url,
                    stock_code=item.get("stock_code"),
                    published_at=item.get("rcept_dt"),
                    metadata={
                        "corp_name": item.get("corp_name", ""),
                        "flr_nm": item.get("flr_nm", ""),
                    },
                )
            )
        return docs


class NaverStockForumCollector(BaseCollector):
    """네이버 종토방(종목토론실) 목록 수집기."""

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
                if not title_el:
                    continue

                title = title_el.get_text(" ", strip=True)
                href = title_el.get("href", "")
                full_url = f"https://finance.naver.com{href}" if href.startswith("/") else href

                docs.append(
                    CrawledDocument(
                        source_type="forum",
                        title=title,
                        content=_clean_forum_title(title),
                        url=full_url,
                        stock_code=stock_code,
                        published_at=(date_el.get_text(strip=True) if date_el else None),
                        metadata={"page": str(page)},
                    )
                )
        return docs


def _clean_forum_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text
