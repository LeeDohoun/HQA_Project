from __future__ import annotations

# File role:
# - Collect Naver Finance securities research report PDFs.
# - Return canonical DocumentRecord rows with source_type="report".

from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .pdf_extract import PDFTextExtractor, PDFTextExtractionResult
from .types import DocumentRecord


@dataclass
class NaverReportListItem:
    title: str
    detail_url: str
    pdf_url: str
    stock_name: str
    stock_code: str
    broker: str
    published_at: str
    raw_date_text: str
    page: int


class NaverReportCollector(BaseCollector):
    """Collect stock research reports from Naver Finance.

    The stock endpoint supports item-code filtering, so this collector keeps the
    existing StockTarget-centered ingestion flow and stores report rows under
    data/raw/report/<theme_key>.jsonl through IngestionService.
    """

    BASE_URL = "https://finance.naver.com"
    COMPANY_LIST_URL = f"{BASE_URL}/research/company_list.naver"
    MIN_CONTENT_LENGTH = 500
    DISCLAIMER_TOKENS = (
        "본 자료",
        "투자자",
        "투자의견",
        "면책",
        "책임",
        "compliance notice",
        "disclaimer",
    )

    def __init__(
        self,
        timeout: int = 30,
        min_content_length: int = MIN_CONTENT_LENGTH,
        max_pdf_pages: int = 80,
    ):
        super().__init__(timeout=timeout)
        self.min_content_length = min_content_length
        self.pdf_extractor = PDFTextExtractor(max_pages=max_pdf_pages)
        self.session.headers.update(
            {
                "Referer": self.COMPANY_LIST_URL,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/pdf,*/*;q=0.8"
                ),
            }
        )

    def collect(
        self,
        stock_name: str,
        stock_code: str,
        max_items: int = 10,
        from_date: str = "",
        to_date: str = "",
        max_pages: int = 20,
    ) -> List[DocumentRecord]:
        return self.collect_by_stock(
            stock_name=stock_name,
            stock_code=stock_code,
            max_items=max_items,
            from_date=from_date,
            to_date=to_date,
            max_pages=max_pages,
        )

    def collect_by_stock(
        self,
        stock_name: str,
        stock_code: str,
        max_items: int = 10,
        from_date: str = "",
        to_date: str = "",
        max_pages: int = 20,
    ) -> List[DocumentRecord]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        docs: List[DocumentRecord] = []
        seen_pdf_urls = set()
        from_compact = self._compact_date(from_date)
        to_compact = self._compact_date(to_date)

        for page in range(1, max_pages + 1):
            if len(docs) >= max_items:
                break

            list_url = self._build_company_list_url(stock_name=stock_name, stock_code=stock_code, page=page)
            try:
                response = self.get_with_retry(
                    list_url,
                    timeout=max(12, self.timeout),
                    headers={"Referer": self.COMPANY_LIST_URL},
                    log_prefix=f"REPORT:LIST:{stock_code}:{page}",
                )
            except Exception as exc:
                print(f"[WARN][REPORT:{stock_code}] list fetch failed page={page} error={exc}")
                break

            items = self._parse_company_list_items(
                response.text,
                page=page,
                fallback_stock_name=stock_name,
                fallback_stock_code=stock_code,
            )
            if not items:
                break

            page_dates: List[str] = []
            page_seen_any_date = False
            for item in items:
                if len(docs) >= max_items:
                    break

                compact = self._compact_date(item.published_at)
                if compact:
                    page_dates.append(compact)
                    page_seen_any_date = True
                if from_compact and compact and compact < from_compact:
                    continue
                if to_compact and compact and compact > to_compact:
                    continue

                detail = self._fetch_report_detail(item.detail_url) if item.detail_url else {}
                pdf_url = detail.get("pdf_url") or item.pdf_url
                if not pdf_url:
                    print(f"[SKIP][REPORT:{stock_code}] reason=missing_pdf title={self._truncate(item.title)}")
                    continue

                pdf_url = self._absolute_url(pdf_url)
                if pdf_url in seen_pdf_urls:
                    continue
                seen_pdf_urls.add(pdf_url)

                pdf_bytes = self._download_pdf(pdf_url, referer=item.detail_url or list_url)
                extraction = self.pdf_extractor.extract(pdf_bytes)
                is_valid, invalid_reason = self._is_valid_extraction(extraction)
                if not is_valid:
                    print(
                        f"[SKIP][REPORT:{stock_code}] reason={invalid_reason} "
                        f"url={pdf_url} title={self._truncate(item.title)}"
                    )
                    continue

                title = detail.get("title") or item.title
                broker = detail.get("broker") or item.broker
                metadata = {
                    "broker": broker,
                    "analyst": detail.get("analyst", ""),
                    "rating": detail.get("rating", ""),
                    "target_price": detail.get("target_price", ""),
                    "pdf_url": pdf_url,
                    "source_page": item.detail_url,
                    "raw_date_text": item.raw_date_text,
                    "report_category": "company",
                    "content_extraction": "pymupdf",
                    "extraction_page_count": str(extraction.page_count),
                    "extraction_pages_with_text": str(extraction.extracted_pages),
                    "extraction_text_length": str(len(extraction.text)),
                    "invalid": False,
                }

                doc = DocumentRecord(
                    source_type="report",
                    title=title,
                    content=extraction.text,
                    url=pdf_url,
                    stock_name=item.stock_name or stock_name,
                    stock_code=item.stock_code or stock_code,
                    published_at=item.published_at,
                    metadata=metadata,
                )
                doc.ensure_doc_id()
                docs.append(doc)

            if page_seen_any_date and from_compact and page_dates and all(d < from_compact for d in page_dates):
                break

            time.sleep(0.5)

        return docs

    def _parse_company_list_items(
        self,
        html: str,
        page: int,
        fallback_stock_name: str,
        fallback_stock_code: str,
    ) -> List[NaverReportListItem]:
        if BeautifulSoup is None:
            return []

        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("div.box_type_m table tr")
        if not rows:
            rows = soup.select("table.type_1 tr, table tr")

        items: List[NaverReportListItem] = []
        for row in rows:
            tds = row.find_all("td")
            if not tds:
                continue

            row_text = self._clean_text(row.get_text(" ", strip=True))
            if not row_text or "제목" in row_text and "작성일" in row_text:
                continue

            read_link = self._find_read_link(row)
            pdf_url = self._extract_pdf_link(row)
            if read_link is None and not pdf_url:
                continue

            detail_url = self._absolute_url(read_link.get("href", "")) if read_link else ""
            title = self._clean_text(read_link.get_text(" ", strip=True) if read_link else "")
            texts = [self._clean_text(td.get_text(" ", strip=True)) for td in tds]
            texts = [text for text in texts if text]

            if not title:
                title = self._guess_title(texts, fallback_stock_name)
            raw_date_text = self._extract_report_date_text(row_text)
            published_at = self._normalize_report_date(raw_date_text)
            item_code = self._extract_stock_code(row, detail_url) or fallback_stock_code
            item_name = self._extract_stock_name(row, fallback_stock_name)

            broker = self._guess_broker(texts, title=title, stock_name=item_name, raw_date_text=raw_date_text)
            if fallback_stock_code and item_code and item_code != fallback_stock_code:
                continue

            if title:
                items.append(
                    NaverReportListItem(
                        title=title,
                        detail_url=detail_url,
                        pdf_url=self._absolute_url(pdf_url) if pdf_url else "",
                        stock_name=item_name or fallback_stock_name,
                        stock_code=item_code or fallback_stock_code,
                        broker=broker,
                        published_at=published_at,
                        raw_date_text=raw_date_text,
                        page=page,
                    )
                )

        return items

    def _fetch_report_detail(self, detail_url: str) -> Dict[str, str]:
        if not detail_url:
            return {}

        try:
            response = self.get_with_retry(
                detail_url,
                timeout=max(12, self.timeout),
                headers={"Referer": self.COMPANY_LIST_URL},
                log_prefix="REPORT:DETAIL",
            )
        except Exception as exc:
            print(f"[WARN][REPORT] detail fetch failed url={detail_url} error={exc}")
            return {}

        if BeautifulSoup is None:
            return {}

        soup = BeautifulSoup(response.text, "html.parser")
        detail_text = self._clean_text(soup.get_text(" ", strip=True))
        title = self._extract_detail_title(soup)
        pdf_url = self._extract_pdf_link(soup)
        metadata = self._extract_detail_metadata(detail_text)
        metadata.update(
            {
                "title": title,
                "pdf_url": self._absolute_url(pdf_url) if pdf_url else "",
            }
        )
        return metadata

    def _download_pdf(self, pdf_url: str, referer: str = "") -> bytes:
        try:
            response = self.get_with_retry(
                pdf_url,
                timeout=max(20, self.timeout),
                headers={
                    "Referer": referer or self.COMPANY_LIST_URL,
                    "Accept": "application/pdf,*/*;q=0.8",
                },
                log_prefix="REPORT:PDF",
            )
            return response.content
        except Exception as exc:
            print(f"[WARN][REPORT] pdf download failed url={pdf_url} error={exc}")
            return b""

    def _is_valid_extraction(self, extraction: PDFTextExtractionResult) -> tuple[bool, str]:
        if extraction.error:
            return False, f"pdf_extraction_failed:{extraction.error}"
        text = self._clean_text(extraction.text)
        if len(text) < self.min_content_length:
            return False, f"content_too_short<{self.min_content_length}"
        if self._looks_disclaimer_only(text, extraction.page_texts[:2]):
            return False, "disclaimer_or_cover_only"
        return True, ""

    def _looks_disclaimer_only(self, text: str, first_pages: List[str]) -> bool:
        compact = self._clean_text(text).lower()
        token_hits = sum(1 for token in self.DISCLAIMER_TOKENS if token.lower() in compact)
        if len(compact) < 1200 and token_hits >= 3:
            return True

        first_text = self._clean_text(" ".join(first_pages)).lower()
        first_hits = sum(1 for token in self.DISCLAIMER_TOKENS if token.lower() in first_text)
        return len(first_text) < 700 and first_hits >= 3

    def _build_company_list_url(self, stock_name: str, stock_code: str, page: int) -> str:
        params = urlencode(
            {
                "page": str(page),
                "searchType": "itemCode",
                "itemName": stock_name,
                "itemCode": stock_code,
            }
        )
        return f"{self.COMPANY_LIST_URL}?{params}"

    def _find_read_link(self, row: Any) -> Any:
        for link in row.find_all("a", href=True):
            href = link.get("href", "")
            if "/research/company_read.naver" in href or "company_read.naver" in href:
                return link
        for link in row.find_all("a", href=True):
            href = link.get("href", "")
            if "/research/" in href and "_read.naver" in href and ".pdf" not in href.lower():
                return link
        return None

    def _extract_pdf_link(self, node: Any) -> str:
        for link in node.find_all("a", href=True):
            href = str(link.get("href", "")).strip()
            if self._is_pdf_url(href):
                return href
        for iframe in node.find_all(["iframe", "embed"], src=True):
            src = str(iframe.get("src", "")).strip()
            if self._is_pdf_url(src):
                return src
        text = str(node)
        match = re.search(r"""https?://[^"'<> ]+\.pdf(?:\?[^"'<> ]*)?""", text, flags=re.IGNORECASE)
        return match.group(0) if match else ""

    @staticmethod
    def _is_pdf_url(url: str) -> bool:
        parsed = urlparse(url or "")
        path = parsed.path.lower()
        return path.endswith(".pdf") or ".pdf" in path

    def _extract_detail_title(self, soup: Any) -> str:
        selectors = [
            "th.view_sbj",
            "td.view_sbj",
            "div.view_info h4",
            "h4",
            "h3",
            "title",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue
            text = self._clean_text(node.get_text(" ", strip=True))
            text = re.sub(r"\s*:\s*네이버.*$", "", text)
            if text and text not in {"네이버페이 증권", "네이버 증권"}:
                return text
        return ""

    def _extract_detail_metadata(self, text: str) -> Dict[str, str]:
        metadata = {
            "broker": "",
            "analyst": "",
            "rating": "",
            "target_price": "",
        }

        broker_match = re.search(r"([가-힣A-Za-z0-9&\s]{1,20}(?:증권|투자증권|자산증권|리서치))", text)
        if broker_match:
            metadata["broker"] = self._clean_text(broker_match.group(1))

        analyst_match = re.search(r"(?:애널리스트|Analyst)\s*[:：]?\s*([가-힣A-Za-z\s]{2,20})", text, re.IGNORECASE)
        if analyst_match:
            metadata["analyst"] = self._clean_text(analyst_match.group(1))

        rating_match = re.search(r"(?:투자의견|Rating)\s*[:：]?\s*([A-Za-z가-힣 ]{2,20})", text, re.IGNORECASE)
        if rating_match:
            metadata["rating"] = self._clean_text(rating_match.group(1))

        target_match = re.search(r"(?:목표주가|Target Price)\s*[:：]?\s*([0-9,]+원?)", text, re.IGNORECASE)
        if target_match:
            metadata["target_price"] = self._clean_text(target_match.group(1))

        return metadata

    def _guess_title(self, texts: List[str], stock_name: str) -> str:
        candidates = [
            text
            for text in texts
            if text != stock_name
            and not self._extract_report_date_text(text)
            and not self._looks_like_broker(text)
            and "pdf" not in text.lower()
        ]
        if not candidates:
            return ""
        return max(candidates, key=len)

    def _guess_broker(self, texts: List[str], title: str, stock_name: str, raw_date_text: str) -> str:
        for text in texts:
            if self._looks_like_broker(text):
                return text
        for text in texts:
            if text in {title, stock_name, raw_date_text}:
                continue
            if len(text) <= 20 and not self._extract_report_date_text(text):
                return text
        return ""

    @staticmethod
    def _looks_like_broker(text: str) -> bool:
        return bool(re.search(r"(증권|투자증권|리서치|투자)$", text or ""))

    def _extract_stock_name(self, row: Any, fallback: str) -> str:
        for link in row.find_all("a", href=True):
            href = link.get("href", "")
            if "code=" in href and "/item/" in href:
                text = self._clean_text(link.get_text(" ", strip=True))
                if text:
                    return text
        return fallback

    def _extract_stock_code(self, row: Any, detail_url: str = "") -> str:
        candidates = [detail_url]
        candidates.extend(str(link.get("href", "")) for link in row.find_all("a", href=True))
        for href in candidates:
            parsed = urlparse(href)
            query = parse_qs(parsed.query)
            for key in ("itemCode", "code"):
                value = query.get(key, [""])[0]
                if re.fullmatch(r"\d{6}", value or ""):
                    return value
            match = re.search(r"(?:itemCode|code)=(\d{6})", href)
            if match:
                return match.group(1)
        return ""

    def _extract_report_date_text(self, text: str) -> str:
        if not text:
            return ""
        for pattern in (
            r"\d{4}[.-]\d{1,2}[.-]\d{1,2}",
            r"\d{2}[.-]\d{1,2}[.-]\d{1,2}",
            r"\d{8}",
        ):
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return ""

    def _normalize_report_date(self, raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip().rstrip(".")
        formats = ["%Y.%m.%d", "%y.%m.%d", "%Y-%m-%d", "%y-%m-%d", "%Y%m%d"]
        for fmt in formats:
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.strftime("%Y-%m-%dT00:00:00")
            except ValueError:
                continue
        return ""

    def _compact_date(self, value: str) -> str:
        if not value:
            return ""
        raw = value.strip()
        if re.fullmatch(r"\d{8}", raw):
            return raw
        normalized = self._normalize_report_date(raw) or raw
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", normalized)
        if match:
            return "".join(match.groups())
        return ""

    def _absolute_url(self, url: str) -> str:
        if not url:
            return ""
        return urljoin(self.BASE_URL, url)

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    @staticmethod
    def _truncate(text: str, limit: int = 60) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."
