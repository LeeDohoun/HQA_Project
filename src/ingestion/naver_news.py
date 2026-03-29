from __future__ import annotations

from datetime import datetime, timedelta
import re
import time
from typing import Any, List
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class NaverNewsCollector(BaseCollector):
    SEARCH_URL = "https://search.naver.com/search.naver"
    MIN_CONTENT_LENGTH = 30

    def __init__(self, timeout: int = 20):
        super().__init__(timeout=timeout)
        self.session.headers.update(
            {
                "Referer": "https://search.naver.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

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

        for candidate in self._collect_search_candidates(
            keyword=keyword,
            from_date=from_date,
            to_date=to_date,
            max_pages=max_pages,
        ):
            if len(docs) >= max_items:
                break

            url = candidate["url"]
            if url in seen_urls:
                continue

            article = self._fetch_article_detail(url)
            final_title = article["title"] or candidate["title"]
            final_content = article["content"]

            is_valid, invalid_reason = self._is_valid_news_document(final_title, final_content)
            if not is_valid:
                print(
                    f"[SKIP][NEWS] reason={invalid_reason} url={url} "
                    f"title={truncate_for_log(final_title)} content_len={len(final_content)}"
                )
                continue

            seen_urls.add(url)
            docs.append(
                DocumentRecord(
                    source_type="news",
                    title=final_title,
                    content=final_content,
                    url=url,
                    published_at=candidate["published_at"],
                    metadata={
                        "keyword": keyword,
                        "press": candidate["press"],
                        "raw_date_text": candidate["raw_date_text"],
                        "summary": candidate["summary"],
                        "domain": urlparse(url).netloc,
                        "invalid": False,
                    },
                )
            )

        return docs

    def _collect_search_candidates(
        self,
        *,
        keyword: str,
        from_date: str,
        to_date: str,
        max_pages: int,
    ) -> List[dict[str, str]]:
        candidates: List[dict[str, str]] = []
        start = 1
        page_no = 0

        while page_no < max_pages:
            page_no += 1
            params = {"where": "news", "query": keyword, "start": start, "sort": 1}
            try:
                response = self.get_with_retry(
                    self.SEARCH_URL,
                    params=params,
                    timeout=max(10, self.timeout),
                    headers={"Referer": "https://search.naver.com/"},
                    log_prefix=f"NEWS:SEARCH:{keyword}",
                )
            except Exception as exc:
                print(f"[WARN][NEWS:SEARCH] stop keyword={keyword} page={page_no} error={exc}")
                break

            items = self._extract_search_items(response.text)
            if not items:
                break

            page_dates: List[str] = []
            page_seen_any_date = False
            for item in items:
                parsed = self._parse_search_item(item)
                if parsed is None:
                    continue
                compact = parsed["published_at"][:10].replace("-", "")
                page_dates.append(compact)
                page_seen_any_date = True
                if from_date and compact < from_date:
                    continue
                if to_date and compact > to_date:
                    continue
                candidates.append(parsed)

            if page_seen_any_date and from_date and page_dates and all(d < from_date for d in page_dates):
                break

            start += 10
            time.sleep(0.8)

        return candidates

    def _extract_search_items(self, html: str) -> List[Any]:
        if BeautifulSoup is None:
            return []
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("div.news_area")
        if not items:
            items = soup.select("div.sds-comps-text.sds-comps-text-type-body1")
        if not items:
            link_nodes = soup.select("a.news_tit, a[href*='news.naver.com'], a[href*='n.news.naver.com']")
            items = [node.find_parent("div") for node in link_nodes if node.find_parent("div") is not None]
        return items

    def _parse_search_item(self, item: Any) -> dict[str, str] | None:
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
            return None

        url = title_el.get("href", "").strip()
        title = self._clean_text(title_el.get_text(" ", strip=True))
        if not url or not title:
            return None

        info_nodes = item.select("span.info")
        raw_date_text = self._extract_news_date_text(info_nodes)
        if not raw_date_text and press_el is not None:
            raw_date_text = self._extract_news_date_text_from_text(press_el.get_text(" ", strip=True))

        normalized_date = self._normalize_news_date(raw_date_text)
        if not normalized_date:
            return None

        return {
            "url": url,
            "title": title,
            "summary": self._clean_text(summary_el.get_text(" ", strip=True) if summary_el else ""),
            "press": self._clean_text(press_el.get_text(" ", strip=True) if press_el else ""),
            "raw_date_text": raw_date_text,
            "published_at": normalized_date,
        }

    def _fetch_article_detail(self, url: str) -> dict[str, str]:
        try:
            response = self.get_with_retry(
                url,
                timeout=max(12, self.timeout),
                headers={
                    "Referer": "https://search.naver.com/search.naver?where=news",
                    "User-Agent": self.session.headers.get("User-Agent", ""),
                },
                log_prefix="NEWS:ARTICLE",
            )
        except Exception as exc:
            print(f"[SKIP][NEWS] reason=article_fetch_failed url={url} error={exc}")
            return {"title": "", "content": ""}

        if BeautifulSoup is None:
            return {"title": "", "content": ""}

        soup = BeautifulSoup(response.text, "html.parser")
        title = self._extract_article_title(soup)
        content = self._extract_article_body(soup)
        return {"title": title, "content": content}

    def _extract_article_title(self, soup: Any) -> str:
        selectors = [
            "h2#title_area span",
            "h2.media_end_head_headline span",
            "h2.end_tit",
            "h1#articleTitle",
            "h1",
            "meta[property='og:title']",
            "title",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node is None:
                continue
            if node.name == "meta":
                raw = node.get("content", "")
            else:
                raw = node.get_text(" ", strip=True)
            text = self._clean_text(raw)
            if text:
                return text
        return ""

    def _extract_article_body(self, soup: Any) -> str:
        selectors = [
            "#dic_area",
            "#newsct_article",
            "#articeBody",
            "article",
            "div#articleBodyContents",
            "div.newsct_article._article_body",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = self._clean_text(node.get_text(" ", strip=True))
                if len(text) >= self.MIN_CONTENT_LENGTH:
                    return text

        meta_desc = soup.select_one("meta[property='og:description'], meta[name='description']")
        if meta_desc and meta_desc.get("content"):
            text = self._clean_text(str(meta_desc["content"]))
            if len(text) >= self.MIN_CONTENT_LENGTH:
                return text

        paragraphs = [self._clean_text(p.get_text(" ", strip=True)) for p in soup.select("p")]
        paragraphs = [p for p in paragraphs if len(p) >= 20]
        if paragraphs:
            text = self._clean_text(" ".join(paragraphs[:6]))
            if len(text) >= self.MIN_CONTENT_LENGTH:
                return text
        return ""

    def _is_valid_news_document(self, title: str, content: str) -> tuple[bool, str]:
        t = self._clean_text(title)
        c = self._clean_text(content)
        if not t:
            return False, "empty_title"
        if not c:
            return False, "empty_content"
        if t == "네이버뉴스":
            return False, "title_placeholder"
        if c == "네이버뉴스":
            return False, "content_placeholder"
        if len(c) < self.MIN_CONTENT_LENGTH:
            return False, f"content_too_short<{self.MIN_CONTENT_LENGTH}"
        return True, ""

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

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"\s+", " ", (text or "")).strip()
        return text


def truncate_for_log(text: str, limit: int = 60) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
