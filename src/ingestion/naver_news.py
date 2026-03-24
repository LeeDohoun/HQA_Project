from __future__ import annotations

from datetime import datetime, timedelta
import re
import time
from typing import List

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class NaverNewsCollector(BaseCollector):
    SEARCH_URL = "https://search.naver.com/search.naver"

    def __init__(self, timeout: int = 20):
        super().__init__(timeout=timeout)
        self.session.headers.update({"Referer": "https://search.naver.com/"})

    def collect(
        self,
        keyword: str,
        max_items: int = 20,
        from_date: str = "",
        to_date: str = "",
        max_pages: int = 100,
        **kwargs,
    ) -> List[DocumentRecord]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[DocumentRecord] = []
        seen_urls = set()
        start = 1
        page_no = 0

        while len(docs) < max_items and page_no < max_pages:
            page_no += 1
            params = {"where": "news", "query": keyword, "start": start, "sort": 1}

            try:
                response = self.get_with_retry(
                    self.SEARCH_URL,
                    params=params,
                    log_prefix=f"NEWS:{keyword}",
                )
            except Exception:
                break

            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select("div.news_area")
            if not items:
                items = soup.select("div.sds-comps-text.sds-comps-text-type-body1")
            if not items:
                link_nodes = soup.select("a.news_tit, a[href*='news.naver.com'], a[href*='n.news.naver.com']")
                items = [node.find_parent("div") for node in link_nodes if node.find_parent("div") is not None]
            if not items:
                break

            page_dates: List[str] = []
            page_seen_any_date = False

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
                if not raw_date_text and press_el is not None:
                    raw_date_text = self._extract_news_date_text_from_text(press_el.get_text(" ", strip=True))

                normalized_date = self._normalize_news_date(raw_date_text)
                if not normalized_date:
                    continue

                compact = normalized_date[:10].replace("-", "")
                page_dates.append(compact)
                page_seen_any_date = True

                if from_date and compact < from_date:
                    continue
                if to_date and compact > to_date:
                    continue

                seen_urls.add(url)
                docs.append(
                    DocumentRecord(
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

            if page_seen_any_date and from_date and page_dates and all(d < from_date for d in page_dates):
                break

            start += 10
            time.sleep(0.8)

        return docs

    def _extract_news_date_text(self, info_nodes) -> str:
        for node in info_nodes:
            text = node.get_text(" ", strip=True)
            extracted = self._extract_news_date_text_from_text(text)
            if extracted:
                return extracted
        return ""

    def _extract_news_date_text_from_text(self, text: str) -> str:
        if not text:
            return ""
        for pattern in [
            r"\d{4}\.\d{1,2}\.\d{1,2}\.?",
            r"\d+\s*분\s*전",
            r"\d+\s*시간\s*전",
            r"\d+\s*일\s*전",
            r"\d+\s*주\s*전",
        ]:
            m = re.search(pattern, text.strip())
            if m:
                return m.group(0)
        return ""

    def _normalize_news_date(self, raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip().rstrip(",")
        now = datetime.now()

        m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?", raw)
        if m:
            y, mo, d = m.groups()
            return datetime(int(y), int(mo), int(d), 0, 0, 0).strftime("%Y-%m-%dT%H:%M:%S")
        for unit, delta in [("분", "minutes"), ("시간", "hours"), ("일", "days"), ("주", "weeks")]:
            m = re.search(rf"(\d+)\s*{unit}\s*전", raw)
            if m:
                kwargs = {delta: int(m.group(1))}
                return (now - timedelta(**kwargs)).strftime("%Y-%m-%dT%H:%M:%S")
        return ""
