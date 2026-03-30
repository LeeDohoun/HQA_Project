from __future__ import annotations

import re
import time
from typing import Any, Dict, List

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class NaverStockForumCollector(BaseCollector):
    MIN_BODY_LENGTH = 20

    def collect(
        self,
        stock_code: str,
        pages: int = 3,
        from_date: str = "",
        to_date: str = "",
    ) -> List[DocumentRecord]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[DocumentRecord] = []
        for page in range(1, pages + 1):
            page_url = f"https://finance.naver.com/item/board.naver?code={stock_code}&page={page}"
            list_items = self._collect_forum_list_items(stock_code=stock_code, page=page, page_url=page_url)
            page_dates: List[str] = []
            page_seen_any_date = False

            for item in list_items:
                title = item["title"]
                full_url = item["url"]
                raw_date = item["raw_date_text"]
                published_at = item["published_at"]
                compact = published_at[:10].replace("-", "")
                page_dates.append(compact)
                page_seen_any_date = True
                if from_date and compact < from_date:
                    continue
                if to_date and compact > to_date:
                    continue

                body, body_extracted = self._fetch_forum_body(full_url, referer=page_url)
                content_source = "body" if body_extracted else "title_only"
                body_missing_reason = ""
                if not body_extracted:
                    body_missing_reason = "body_missing_or_too_short"
                    print(f"[SKIP_HINT][FORUM] body missing -> fallback title url={full_url}")
                    body = title

                docs.append(
                    DocumentRecord(
                        source_type="forum",
                        title=title,
                        content=body,
                        url=full_url,
                        stock_code=stock_code,
                        published_at=published_at,
                        metadata={
                            "page": str(page),
                            "raw_date_text": raw_date,
                            "content_source": content_source,
                            "body_extracted": body_extracted,
                            "body_missing_reason": body_missing_reason,
                        },
                    )
                )

            if page_seen_any_date and from_date and page_dates and all(d < from_date for d in page_dates):
                break

            time.sleep(0.2)

        return docs

    def _collect_forum_list_items(self, stock_code: str, page: int, page_url: str) -> List[Dict[str, str]]:
        response = self.get_with_retry(
            page_url,
            log_prefix=f"FORUM:{stock_code}:{page}",
            headers={"Referer": f"https://finance.naver.com/item/main.naver?code={stock_code}"},
        )
        if BeautifulSoup is None:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("table.type2 tr")
        items: List[Dict[str, str]] = []
        for row in rows:
            parsed = self._parse_forum_list_row(row)
            if parsed is not None:
                items.append(parsed)
        return items

    def _parse_forum_list_row(self, row: Any) -> Dict[str, str] | None:
        title_el = row.select_one("td.title a")
        date_el = row.select_one("td:nth-of-type(1)")
        if title_el is None:
            return None

        title = _clean_text(title_el.get_text(" ", strip=True))
        href = title_el.get("href", "")
        full_url = f"https://finance.naver.com{href}" if href.startswith("/") else href
        raw_date = date_el.get_text(strip=True) if date_el else ""
        published_at = self.to_iso_datetime(raw_date, ["%Y.%m.%d %H:%M", "%Y.%m.%d"], default_time="00:00:00")
        if not title or not full_url or not published_at:
            return None
        return {
            "title": title,
            "url": full_url,
            "raw_date_text": raw_date,
            "published_at": published_at,
        }

    def _fetch_forum_body(self, url: str, *, referer: str = "") -> tuple[str, bool]:
        if BeautifulSoup is None:
            return "", False

        try:
            response = self.get_with_retry(
                url,
                log_prefix="FORUM:READ",
                headers={"Referer": referer or "https://finance.naver.com/"},
            )
        except Exception:
            return "", False

        soup = BeautifulSoup(response.text, "html.parser")
        body = self._extract_forum_body_from_soup(soup)
        cleaned = self._sanitize_forum_body(body)
        if len(cleaned) < self.MIN_BODY_LENGTH:
            return "", False
        return cleaned[:4000], True

    def _extract_forum_body_from_soup(self, soup: Any) -> str:
        selectors = [
            "td.view_cnt",
            "div.view_se",
            "div#body",
            "div.article",
            "div.se-main-container",
            "div.ContentRenderer",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = _clean_text(node.get_text(" ", strip=True))
                if text:
                    return text

        paragraphs = [_clean_text(p.get_text(" ", strip=True)) for p in soup.select("p")]
        paragraphs = [p for p in paragraphs if len(p) >= 8]
        if paragraphs:
            return " ".join(paragraphs[:10])
        return ""

    def _sanitize_forum_body(self, text: str) -> str:
        cleaned = _clean_text(text)
        if not cleaned:
            return ""
        noise_patterns = [
            r"목록\s*답글\s*글쓰기",
            r"URL\s*복사",
            r"신고\s*수정\s*삭제",
            r"이전글\s*다음글",
            r"댓글\s*\d+",
            r"게시판\s*운영원칙",
        ]
        for pattern in noise_patterns:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned


class NaverStockChartCollector(BaseCollector):
    def collect(
        self,
        stock_code: str,
        pages: int = 5,
        from_date: str = "",
        to_date: str = "",
    ) -> List[DocumentRecord]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[DocumentRecord] = []
        for page in range(1, pages + 1):
            url = f"https://finance.naver.com/item/sise_day.naver?code={stock_code}&page={page}"
            response = self.get_with_retry(url, log_prefix=f"CHART:{stock_code}:{page}")

            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select("table.type2 tr")
            page_dates: List[str] = []
            page_seen_any_date = False

            for row in rows:
                cols = [c.get_text(" ", strip=True) for c in row.select("td")]
                if len(cols) < 7:
                    continue

                date_text, close_price, diff, open_price, high_price, low_price, volume = cols[:7]
                published_at = self.to_iso_datetime(date_text, ["%Y.%m.%d"], default_time="00:00:00")
                if not published_at:
                    continue

                compact = published_at[:10].replace("-", "")
                page_dates.append(compact)
                page_seen_any_date = True
                if from_date and compact < from_date:
                    continue
                if to_date and compact > to_date:
                    continue

                docs.append(
                    DocumentRecord(
                        source_type="chart",
                        title=f"{stock_code} 일봉 {date_text}",
                        content=(
                            f"날짜:{date_text} 종가:{close_price} 전일비:{diff} "
                            f"시가:{open_price} 고가:{high_price} 저가:{low_price} 거래량:{volume}"
                        ),
                        url=url,
                        stock_code=stock_code,
                        published_at=published_at,
                        metadata={
                            "page": str(page),
                            "date": date_text,
                            "close": close_price,
                            "diff": diff,
                            "open": open_price,
                            "high": high_price,
                            "low": low_price,
                            "volume": volume,
                        },
                    )
                )

            if page_seen_any_date and from_date and page_dates and all(d < from_date for d in page_dates):
                break

            time.sleep(0.2)

        return docs


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()
