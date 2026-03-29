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


class DartDisclosureCollector(BaseCollector):
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"

    IMPORTANT_REPORT_KEYWORDS = [
        "사업보고서",
        "반기보고서",
        "분기보고서",
        "주요사항보고서",
        "증권신고서",
        "투자설명서",
        "유상증자결정",
        "무상증자결정",
        "전환사채",
        "신주인수권부사채",
        "교환사채",
        "타법인주식및출자증권취득결정",
        "유형자산취득결정",
        "단일판매ㆍ공급계약체결",
        "매출액또는손익구조",
        "영업실적",
    ]

    def __init__(self, api_key: str, timeout: int = 20):
        super().__init__(timeout=timeout)
        self.api_key = api_key
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    def collect(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_count: int = 100,
    ) -> List[DocumentRecord]:
        response = self.get_with_retry(
            self.LIST_URL,
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_count": page_count,
            },
            timeout=self.timeout,
            log_prefix=f"DART:{corp_code}",
        )
        payload = response.json()

        if payload.get("status") != "000":
            return []

        docs: List[DocumentRecord] = []

        for item in payload.get("list", []):
            title = self._clean_text(item.get("report_nm", ""))
            if not title:
                continue

            # 중요 공시만 대상으로 삼음
            if not self._is_important_report(title):
                continue

            rcept_no = item.get("rcept_no", "")
            corp_name = self._clean_text(item.get("corp_name", ""))
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
            published_at = self.to_iso_datetime(
                item.get("rcept_dt", ""),
                ["%Y%m%d"],
                default_time="00:00:00",
            )

            detail_excerpt = self._fetch_detail_excerpt(url) if url else ""
            has_body = bool(detail_excerpt)

            # raw/event 용으로는 남기되, 본문 성공 여부를 표시
            content = detail_excerpt if has_body else f"{corp_name} 공시: {title}"

            docs.append(
                DocumentRecord(
                    source_type="dart",
                    title=title,
                    content=content,
                    url=url,
                    stock_code=item.get("stock_code"),
                    published_at=published_at,
                    metadata={
                        "corp_name": corp_name,
                        "flr_nm": self._clean_text(item.get("flr_nm", "")),
                        "rcept_no": rcept_no,
                        "report_nm": title,
                        "has_body": has_body,
                        "body_source": "dart_detail" if has_body else "title_fallback",
                        "importance": "high",
                    },
                )
            )

            time.sleep(0.15)

        return docs

    def _is_important_report(self, title: str) -> bool:
        return any(keyword in title for keyword in self.IMPORTANT_REPORT_KEYWORDS)

    def _fetch_detail_excerpt(self, url: str) -> str:
        if BeautifulSoup is None:
            return ""

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except Exception:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        candidates = []

        meta_desc = soup.select_one("meta[property='og:description'], meta[name='description']")
        if meta_desc and meta_desc.get("content"):
            text = self._clean_text(meta_desc.get("content", ""))
            if len(text) >= 30:
                candidates.append(text)

        title_node = soup.select_one("title")
        if title_node:
            text = self._clean_text(title_node.get_text(" ", strip=True))
            if len(text) >= 20:
                candidates.append(text)

        body = soup.select_one("body")
        if body:
            body_text = self._clean_text(body.get_text(" ", strip=True))
            body_text = self._remove_noise(body_text)
            if len(body_text) >= 80:
                candidates.append(body_text[:1500])

        if not candidates:
            return ""

        best = max(candidates, key=len)
        best = self._clean_text(best)
        if len(best) < 30:
            return ""

        return best[:1200]

    @staticmethod
    def _remove_noise(text: str) -> str:
        noise_patterns = [
            r"공시정보 .*? 관련사이트",
            r"정정신고서 제출일",
            r"메뉴 바로가기",
            r"본문 바로가기",
            r"이전 다음",
            r"검색어 입력",
            r"다운로드",
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()