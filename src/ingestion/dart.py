from __future__ import annotations

import re
import time
from typing import Dict, List, Tuple
from urllib.parse import urlencode

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class DartDisclosureCollector(BaseCollector):
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"
    WRAPPER_TEXT_TOKENS = [
        "잠시만 기다려주세요",
        "현재목차",
        "본문선택",
        "첨부선택",
        "문서목차",
        "인쇄 닫기",
    ]
    DART_ERROR_PAGE_KEYWORDS = [
        "홈으로 가기",
        "서비스 이용에 불편을 드려 죄송합니다",
        "요청하신 인터넷주소(URL)를 찾을 수 없습니다",
        "DART 메인화면 바로가기",
        "Copyright Financial supervisory service",
        "Financial supervisory service",
    ]
    DART_ERROR_PAGE_MOJIBAKE_PATTERNS = [
        r"�솒.*媛�湲�",
        r"�꽌鍮꾩뒪.*遺덊렪",
        r"�슂泥��븯�떊.*URL.*李얠쓣.*�닔.*�뾾",
        r"DART.*硫붿씤�솕硫�.*諛붾줈媛�湲�",
    ]

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

            detail_excerpt, body_source, quality_meta = self._fetch_detail_excerpt(url) if url else ("", "none", {})
            wrapper_text_detected = bool(quality_meta.get("wrapper_text_detected", False))
            body_error_type = str(quality_meta.get("body_error_type", ""))
            encoding_fixed = bool(quality_meta.get("encoding_fixed", False))
            mojibake_detected = bool(quality_meta.get("mojibake_detected", False))
            body_extracted = self._is_valid_body_text(
                detail_excerpt,
                wrapper_text_detected=wrapper_text_detected,
                body_error_type=body_error_type,
                mojibake_detected=mojibake_detected,
            )
            has_body = body_extracted

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
                        "body_source": body_source if has_body else "title_fallback",
                        "body_extracted": body_extracted,
                        "wrapper_text_detected": wrapper_text_detected,
                        "body_error_type": body_error_type,
                        "encoding_fixed": encoding_fixed,
                        "mojibake_detected": mojibake_detected,
                        "importance": "high",
                    },
                )
            )

            time.sleep(0.15)

        return docs

    def _is_important_report(self, title: str) -> bool:
        return any(keyword in title for keyword in self.IMPORTANT_REPORT_KEYWORDS)

    def _fetch_detail_excerpt(self, url: str) -> Tuple[str, str, Dict[str, bool | str]]:
        quality_meta: Dict[str, bool | str] = {
            "wrapper_text_detected": False,
            "body_error_type": "",
            "encoding_fixed": False,
            "mojibake_detected": False,
        }
        if BeautifulSoup is None:
            return "", "none", quality_meta

        try:
            response = self.get_with_retry(
                url,
                timeout=self.timeout,
                log_prefix="DART:DETAIL",
                headers={"Referer": "https://dart.fss.or.kr/"},
            )
            response.raise_for_status()
        except Exception:
            quality_meta["body_error_type"] = "main_failed"
            return "", "main_failed", quality_meta

        main_html, main_encoding_fixed, main_mojibake = self._decode_response_text(response)
        quality_meta["encoding_fixed"] = bool(main_encoding_fixed)
        quality_meta["mojibake_detected"] = bool(main_mojibake)

        soup = BeautifulSoup(main_html, "html.parser")
        viewer_url = self._extract_viewer_url(main_html)
        if viewer_url:
            viewer_body, viewer_meta = self._fetch_viewer_body(viewer_url)
            quality_meta["encoding_fixed"] = bool(quality_meta["encoding_fixed"] or viewer_meta.get("encoding_fixed"))
            quality_meta["mojibake_detected"] = bool(
                quality_meta["mojibake_detected"] or viewer_meta.get("mojibake_detected")
            )
            if viewer_body:
                wrapper_detected_raw = self._contains_wrapper_tokens(viewer_body) or self._is_error_page_text(viewer_body)
                cleaned = self._sanitize_body_text(viewer_body)
                wrapper_detected = wrapper_detected_raw or self._contains_wrapper_tokens(cleaned)
                quality_meta["wrapper_text_detected"] = wrapper_detected
                if self._is_error_page_text(cleaned) or self._is_error_page_text(viewer_body):
                    quality_meta["body_error_type"] = "viewer_error_page"
                if cleaned:
                    return cleaned[:2000], "viewer", quality_meta

        candidates = []

        meta_desc = soup.select_one("meta[property='og:description'], meta[name='description']")
        if meta_desc and meta_desc.get("content"):
            text = self._sanitize_body_text(meta_desc.get("content", ""))
            if len(text) >= 30:
                candidates.append(text)

        title_node = soup.select_one("title")
        if title_node:
            text = self._sanitize_body_text(title_node.get_text(" ", strip=True))
            if len(text) >= 20:
                candidates.append(text)

        body = soup.select_one("body")
        if body:
            body_text = self._sanitize_body_text(body.get_text(" ", strip=True))
            if len(body_text) >= 80:
                candidates.append(body_text[:1500])

        if not candidates:
            quality_meta["body_error_type"] = "main_fallback_empty"
            return "", "main_fallback_empty", quality_meta

        best = max(candidates, key=len)
        best = self._sanitize_body_text(best)
        wrapper_detected = self._contains_wrapper_tokens(max(candidates, key=len)) or self._contains_wrapper_tokens(best)
        if self._is_error_page_text(best):
            quality_meta["body_error_type"] = "main_error_page"
        quality_meta["wrapper_text_detected"] = wrapper_detected or self._is_error_page_text(best)
        if len(best) < 30:
            if not quality_meta["body_error_type"]:
                quality_meta["body_error_type"] = "main_too_short"
            return "", "main_too_short", quality_meta

        return best[:1200], "main_fallback", quality_meta

    def _extract_viewer_url(self, html: str) -> str:
        m = re.search(r"viewDoc\((?P<args>.*?)\)", html, flags=re.DOTALL)
        if not m:
            return ""
        args_raw = m.group("args")
        parts = [p.strip() for p in args_raw.split(",")]
        if len(parts) < 6:
            return ""

        def _normalize(part: str) -> str:
            part = part.strip()
            if part.lower() == "null":
                return ""
            if part.startswith("'") and part.endswith("'"):
                return part[1:-1]
            return part.strip("'\"")

        params = {
            "rcpNo": _normalize(parts[0]),
            "dcmNo": _normalize(parts[1]),
            "eleId": _normalize(parts[2]),
            "offset": _normalize(parts[3]),
            "length": _normalize(parts[4]),
            "dtd": _normalize(parts[5]),
        }
        if not params["rcpNo"] or not params["dcmNo"]:
            return ""
        return f"https://dart.fss.or.kr/report/viewer.do?{urlencode(params)}"

    def _fetch_viewer_body(self, viewer_url: str) -> Tuple[str, Dict[str, bool]]:
        meta = {"encoding_fixed": False, "mojibake_detected": False}
        if BeautifulSoup is None:
            return "", meta
        try:
            response = self.get_with_retry(
                viewer_url,
                timeout=self.timeout,
                log_prefix="DART:VIEWER",
                headers={"Referer": "https://dart.fss.or.kr/"},
            )
        except Exception:
            return "", meta

        html, encoding_fixed, mojibake_detected = self._decode_response_text(response)
        meta["encoding_fixed"] = encoding_fixed
        meta["mojibake_detected"] = mojibake_detected
        soup = BeautifulSoup(html, "html.parser")

        selectors = [
            "div#XFormD1_Form0",
            "body",
            "div#viewerContents",
            "div.xforms",
            "table",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = self._clean_text(node.get_text(" ", strip=True))
                if len(text) >= 80:
                    return text, meta
        return "", meta

    def _sanitize_body_text(self, text: str) -> str:
        text = self._clean_text(text)
        text = self._remove_noise(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _contains_wrapper_tokens(self, text: str) -> bool:
        if not text:
            return False
        if any(token in text for token in self.WRAPPER_TEXT_TOKENS):
            return True
        return self._contains_mojibake_wrapper_text(text)

    def _contains_mojibake_wrapper_text(self, text: str) -> bool:
        if not text:
            return False
        return any(re.search(pattern, text) for pattern in self.DART_ERROR_PAGE_MOJIBAKE_PATTERNS)

    def _is_error_page_text(self, text: str) -> bool:
        if not text:
            return False
        normalized = self._clean_text(text)
        if any(keyword in normalized for keyword in self.DART_ERROR_PAGE_KEYWORDS):
            return True
        if self._contains_mojibake_wrapper_text(normalized):
            return True
        return False

    def _decode_response_text(self, response) -> Tuple[str, bool, bool]:
        content = response.content or b""
        response_encoding = (response.encoding or "").lower().strip()
        candidates = []
        if response_encoding:
            candidates.append(response_encoding)
        candidates.extend(["utf-8", "cp949", "euc-kr"])

        tried = set()
        decoded_variants: List[Tuple[str, str]] = []
        for enc in candidates:
            if enc in tried:
                continue
            tried.add(enc)
            try:
                decoded_variants.append((enc, content.decode(enc, errors="strict")))
            except Exception:
                continue

        if not decoded_variants:
            fallback = content.decode("utf-8", errors="replace")
            return fallback, False, self._is_mojibake_text(fallback)

        best_enc, best_text = max(
            decoded_variants,
            key=lambda item: self._decode_quality_score(item[1]),
        )
        encoding_fixed = bool(response_encoding and best_enc != response_encoding)
        mojibake_detected = self._is_mojibake_text(best_text)
        return best_text, encoding_fixed, mojibake_detected

    def _decode_quality_score(self, text: str) -> int:
        if not text:
            return -10_000
        score = 0
        score += len(re.findall(r"[가-힣]", text)) * 2
        score += len(re.findall(r"[A-Za-z0-9]", text))
        score -= text.count("�") * 20
        score -= len(re.findall(r"[ÃÂÐØþ]", text)) * 4
        return score

    def _is_mojibake_text(self, text: str) -> bool:
        if not text:
            return False
        replacement_ratio = text.count("�") / max(1, len(text))
        strange_latin = len(re.findall(r"[ÃÂÐØþ]", text))
        return replacement_ratio > 0.002 or strange_latin >= 5 or self._contains_mojibake_wrapper_text(text)

    def _is_valid_body_text(
        self,
        text: str,
        wrapper_text_detected: bool,
        body_error_type: str = "",
        mojibake_detected: bool = False,
    ) -> bool:
        if not text:
            return False
        if wrapper_text_detected:
            return False
        if body_error_type:
            return False
        if mojibake_detected:
            return False
        return len(text) >= 120

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
            r"잠시만 기다려주세요",
            r"현재목차",
            r"본문선택",
            r"첨부선택",
            r"문서목차",
            r"인쇄 닫기",
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()
