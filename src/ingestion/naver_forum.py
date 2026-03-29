from __future__ import annotations

import re
import time
from typing import List

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class NaverStockForumCollector(BaseCollector):
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
            url = f"https://finance.naver.com/item/board.naver?code={stock_code}&page={page}"
            response = self.get_with_retry(url, log_prefix=f"FORUM:{stock_code}:{page}")

            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select("table.type2 tr")
            page_dates: List[str] = []
            page_seen_any_date = False

            for row in rows:
                title_el = row.select_one("td.title a")
                date_el = row.select_one("td:nth-of-type(1)")
                if title_el is None:
                    continue

                title = _clean_text(title_el.get_text(" ", strip=True))
                href = title_el.get("href", "")
                full_url = f"https://finance.naver.com{href}" if href.startswith("/") else href
                raw_date = date_el.get_text(strip=True) if date_el else ""
                published_at = self.to_iso_datetime(raw_date, ["%Y.%m.%d %H:%M", "%Y.%m.%d"], default_time="00:00:00")
                if not published_at:
                    continue

                compact = published_at[:10].replace("-", "")
                page_dates.append(compact)
                page_seen_any_date = True
                if from_date and compact < from_date:
                    continue
                if to_date and compact > to_date:
                    continue

                body = self._fetch_forum_body(full_url) or title

                docs.append(
                    DocumentRecord(
                        source_type="forum",
                        title=title,
                        content=body,
                        url=full_url,
                        stock_code=stock_code,
                        published_at=published_at,
                        metadata={"page": str(page), "raw_date_text": raw_date},
                    )
                )

            if page_seen_any_date and from_date and page_dates and all(d < from_date for d in page_dates):
                break

            time.sleep(0.2)

        return docs

    def _fetch_forum_body(self, url: str) -> str:
        if BeautifulSoup is None:
            return ""

        try:
            response = self.get_with_retry(url, log_prefix="FORUM:READ")
        except Exception:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        selectors = [
            "div.view_se",
            "div#body",
            "td.view_cnt",
            "div.article",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = _clean_text(node.get_text(" ", strip=True))
                if len(text) >= 10:
                    return text[:4000]

        paragraphs = [_clean_text(p.get_text(" ", strip=True)) for p in soup.select("p")]
        paragraphs = [p for p in paragraphs if len(p) >= 10]
        if paragraphs:
            return " ".join(paragraphs[:8])[:4000]

        return ""


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