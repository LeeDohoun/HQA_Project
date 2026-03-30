from __future__ import annotations

import re
import time
from io import BytesIO
from typing import Dict, List, Tuple
from urllib.parse import urlencode
from zipfile import BadZipFile, ZipFile

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class DartDisclosureCollector(BaseCollector):
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"
    DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
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
        "URL이 변경되었거나 삭제되었을 수 있습니다",
        "DART 메인화면 바로가기",
        "Copyright Financial supervisory service",
        "Financial supervisory service",
        "dart@fss.or.kr",
    ]
    DART_ERROR_PAGE_MOJIBAKE_PATTERNS = [
        r"�솒.*媛�湲�",
        r"�꽌鍮꾩뒪.*遺덊렪",
        r"�슂泥��븯�떊.*URL.*李얠쓣.*�닔.*�뾾",
        r"DART.*硫붿씤�솕硫�.*諛붾줈媛�湲�",
    ]
    # report_nm 정규화 후 매핑: endpoint 이름은 OpenDART 가이드 기준 사용
    STRUCTURED_ENDPOINT_MAP = [
        ("유형자산취득결정", ["tgastInhDecsn"]),
        ("유형자산양도결정", ["tgastTrfDecsn"]),
        ("전환사채", ["cvbdIsDecsn"]),
        ("신주인수권부사채", ["bdwtIsDecsn"]),
        ("교환사채", ["exbdIsDecsn"]),
        ("타법인주식및출자증권취득결정", ["otcprStkInvscrInhDecsn"]),
        ("타법인주식및출자증권양도결정", ["otcprStkInvscrTrfDecsn"]),
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
        self._structured_cache: Dict[Tuple[str, str, str, str], Dict] = {}

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

            detail_excerpt, body_source, quality_meta = self._fetch_structured_body(
                corp_code=corp_code,
                bgn_de=bgn_de,
                end_de=end_de,
                rcept_no=rcept_no,
                report_nm=title,
            )
            if not detail_excerpt:
                detail_excerpt, body_source, quality_meta = self._fetch_official_document_body(rcept_no)
            if not detail_excerpt and url:
                detail_excerpt, body_source, quality_meta = self._fetch_detail_excerpt(url)
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
            if has_body and not body_error_type:
                body_error_type = "success"

            # raw/event 용으로는 남기되, 본문 성공 여부를 표시
            content = detail_excerpt if has_body else f"{corp_name} 공시: {title}"
            final_source = body_source if has_body else "title_fallback"
            print(
                f"[DART][PATH] rcept_no={rcept_no} "
                f"structured_candidate={bool(self._match_structured_endpoints(title))} "
                f"final_source={final_source} has_body={has_body} body_error_type={body_error_type}"
            )

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
                        "body_source": final_source,
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

    def _fetch_structured_body(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        rcept_no: str,
        report_nm: str,
    ) -> Tuple[str, str, Dict[str, bool | str]]:
        quality_meta: Dict[str, bool | str] = {
            "wrapper_text_detected": False,
            "body_error_type": "no_structured_match",
            "encoding_fixed": False,
            "mojibake_detected": False,
        }
        endpoints = self._match_structured_endpoints(report_nm)
        print(f"[DART][STRUCTURED] rcept_no={rcept_no} endpoints={endpoints}")
        if not endpoints:
            return "", "structured_api", quality_meta

        for endpoint in endpoints:
            cache_key = (endpoint, corp_code, bgn_de, end_de)
            payload = self._structured_cache.get(cache_key)
            if payload is None:
                try:
                    print(f"[DART][STRUCTURED] API hit endpoint={endpoint} corp_code={corp_code}")
                    response = self.get_with_retry(
                        f"https://opendart.fss.or.kr/api/{endpoint}.json",
                        params={
                            "crtfc_key": self.api_key,
                            "corp_code": corp_code,
                            "bgn_de": bgn_de,
                            "end_de": end_de,
                        },
                        timeout=self.timeout,
                        log_prefix=f"DART:STRUCTURED:{endpoint}",
                    )
                    payload = response.json()
                except Exception:
                    quality_meta["body_error_type"] = "structured_fetch_failed"
                    continue
                self._structured_cache[cache_key] = payload

            if not isinstance(payload, dict) or payload.get("status") != "000":
                quality_meta["body_error_type"] = "structured_empty"
                continue

            rows = payload.get("list", [])
            if not isinstance(rows, list) or not rows:
                quality_meta["body_error_type"] = "structured_empty"
                continue

            target = self._find_structured_row(rows=rows, rcept_no=rcept_no)
            if target is None:
                print(f"[DART][STRUCTURED] rcept_no match failed endpoint={endpoint} target={rcept_no}")
                quality_meta["body_error_type"] = "structured_no_rcept_match"
                continue
            print(f"[DART][STRUCTURED] rcept_no match success endpoint={endpoint} target={rcept_no}")

            text = self._structured_row_to_text(report_nm=report_nm, endpoint=endpoint, row=target)
            cleaned = self._sanitize_body_text(text)
            if not cleaned:
                quality_meta["body_error_type"] = "structured_empty"
                continue
            if self._contains_error_page_tokens(cleaned):
                quality_meta["wrapper_text_detected"] = True
                quality_meta["body_error_type"] = "structured_error_page"
                continue
            quality_meta["mojibake_detected"] = self._is_mojibake_text(cleaned)
            if quality_meta["mojibake_detected"]:
                quality_meta["body_error_type"] = "structured_mojibake"
                continue
            if len(cleaned) < 120:
                quality_meta["body_error_type"] = "structured_too_short"
                continue
            quality_meta["body_error_type"] = ""
            return cleaned[:2500], "structured_api", quality_meta

        return "", "structured_api", quality_meta

    def _match_structured_endpoints(self, report_nm: str) -> List[str]:
        normalized = self._normalize_report_name(report_nm)
        for keyword, endpoints in self.STRUCTURED_ENDPOINT_MAP:
            if keyword in normalized:
                return endpoints
        return []

    def _normalize_report_name(self, report_nm: str) -> str:
        normalized = self._clean_text(report_nm)
        normalized = re.sub(r"\(.*?\)", "", normalized)
        normalized = normalized.replace(" ", "")
        return normalized

    def _find_structured_row(self, rows: List[Dict], rcept_no: str) -> Dict | None:
        if not rows:
            return None
        if rcept_no:
            for row in rows:
                if str(row.get("rcept_no", "")).strip() == str(rcept_no).strip():
                    return row
        return rows[0]

    def _structured_row_to_text(self, report_nm: str, endpoint: str, row: Dict) -> str:
        lines = [f"[공시유형] {report_nm}", f"[structured_endpoint] {endpoint}"]
        for key, value in row.items():
            if key in {"status", "message"}:
                continue
            val = self._clean_text(str(value or ""))
            if not val:
                continue
            lines.append(f"{key}: {val}")
        return "\n".join(lines)

    def _fetch_official_document_body(self, rcept_no: str) -> Tuple[str, str, Dict[str, bool | str]]:
        quality_meta: Dict[str, bool | str] = {
            "wrapper_text_detected": False,
            "body_error_type": "",
            "encoding_fixed": False,
            "mojibake_detected": False,
        }
        if not rcept_no:
            quality_meta["body_error_type"] = "missing_rcept_no"
            return "", "official_api", quality_meta
        try:
            response = self.get_with_retry(
                self.DOCUMENT_URL,
                params={"crtfc_key": self.api_key, "rcept_no": rcept_no},
                timeout=self.timeout,
                log_prefix="DART:DOCUMENT",
            )
        except Exception:
            quality_meta["body_error_type"] = "official_api_failed"
            return "", "official_api", quality_meta

        content_bytes = response.content or b""
        if not content_bytes:
            quality_meta["body_error_type"] = "official_empty"
            return "", "official_api", quality_meta
        if not content_bytes.startswith(b"PK"):
            quality_meta["body_error_type"] = "zip_signature_missing"
            return "", "official_api", quality_meta

        try:
            with ZipFile(BytesIO(content_bytes)) as zf:
                names = zf.namelist()
                print(f"[DART:DOCUMENT] zip entries count={len(names)} sample={names[:5]}")
                candidates = self._select_document_inner_files(names)
                if not candidates:
                    quality_meta["body_error_type"] = "no_valid_inner_file"
                    return "", "official_api", quality_meta

                best_text = ""
                best_score = -10**9
                encoding_fixed_any = False
                mojibake_any = False
                for name in candidates:
                    try:
                        inner_bytes = zf.read(name)
                    except Exception:
                        continue
                    decoded, encoding_fixed, mojibake = self._decode_bytes_with_candidates(inner_bytes)
                    encoding_fixed_any = encoding_fixed_any or encoding_fixed
                    mojibake_any = mojibake_any or mojibake
                    normalized = self._normalize_inner_document_text(name=name, text=decoded)
                    cleaned = self._sanitize_body_text(normalized)
                    if not cleaned:
                        continue
                    score = self._document_text_score(cleaned)
                    if score > best_score:
                        best_score = score
                        best_text = cleaned

                quality_meta["encoding_fixed"] = encoding_fixed_any
                quality_meta["mojibake_detected"] = mojibake_any
                if not best_text:
                    quality_meta["body_error_type"] = "decode_failed"
                    return "", "official_api", quality_meta
                if self._contains_error_page_tokens(best_text):
                    quality_meta["wrapper_text_detected"] = True
                    quality_meta["body_error_type"] = "official_error_page"
                    return "", "official_api", quality_meta
                if self._is_mojibake_text(best_text):
                    quality_meta["mojibake_detected"] = True
                    quality_meta["body_error_type"] = "official_mojibake"
                    return "", "official_api", quality_meta
                if len(best_text) < 120:
                    quality_meta["body_error_type"] = "cleaned_too_short"
                    return "", "official_api", quality_meta
                quality_meta["body_error_type"] = ""
                return best_text[:2500], "official_api", quality_meta
        except BadZipFile:
            quality_meta["body_error_type"] = "zip_open_failed"
            return "", "official_api", quality_meta

    def _select_document_inner_files(self, names: List[str]) -> List[str]:
        scored: List[Tuple[int, str]] = []
        for name in names:
            lname = name.lower()
            score = -1
            if lname.endswith((".xml", ".xhtml")):
                score = 50
            elif lname.endswith((".html", ".htm")):
                score = 40
            elif lname.endswith(".txt"):
                score = 30
            if "xbrl" in lname:
                score += 5
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [name for _, name in scored[:10]]

    def _decode_bytes_with_candidates(self, blob: bytes) -> Tuple[str, bool, bool]:
        encodings = ["utf-8", "utf-8-sig", "cp949", "euc-kr"]
        variants: List[Tuple[str, str]] = []
        for enc in encodings:
            try:
                variants.append((enc, blob.decode(enc, errors="strict")))
            except Exception:
                continue
        if not variants:
            fallback = blob.decode("utf-8", errors="replace")
            return fallback, False, self._is_mojibake_text(fallback)

        best_enc, best_text = max(variants, key=lambda x: self._decode_quality_score(x[1]))
        encoding_fixed = best_enc not in {"utf-8", "utf-8-sig"}
        return best_text, encoding_fixed, self._is_mojibake_text(best_text)

    def _normalize_inner_document_text(self, name: str, text: str) -> str:
        if BeautifulSoup is None:
            return text
        lname = name.lower()
        if lname.endswith((".xml", ".xhtml")):
            soup = BeautifulSoup(text, "xml")
            return soup.get_text(" ", strip=True)
        if lname.endswith((".html", ".htm")) or "<" in text:
            soup = BeautifulSoup(text, "html.parser")
            return soup.get_text(" ", strip=True)
        return text

    def _document_text_score(self, text: str) -> int:
        score = len(text)
        score += len(re.findall(r"[가-힣]", text)) * 2
        if self._contains_error_page_tokens(text):
            score -= 2000
        if self._is_mojibake_text(text):
            score -= 1500
        if "재무제표" in text or "주요사항" in text or "이사회" in text:
            score += 200
        return score


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
                wrapper_detected_raw = self._contains_wrapper_tokens(viewer_body) or self._contains_error_page_tokens(viewer_body)
                cleaned = self._sanitize_body_text(viewer_body)
                wrapper_detected = wrapper_detected_raw or self._contains_wrapper_tokens(cleaned)
                quality_meta["wrapper_text_detected"] = wrapper_detected
                if self._contains_error_page_tokens(cleaned) or self._contains_error_page_tokens(viewer_body):
                    quality_meta["body_error_type"] = "viewer_error_page"
                    return "", "viewer_fallback", quality_meta
                if cleaned:
                    return cleaned[:2000], "viewer_fallback", quality_meta

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
        if self._contains_error_page_tokens(best):
            quality_meta["body_error_type"] = "main_error_page"
        quality_meta["wrapper_text_detected"] = wrapper_detected or self._contains_error_page_tokens(best)
        if len(best) < 30:
            if not quality_meta["body_error_type"]:
                quality_meta["body_error_type"] = "main_too_short"
            return "", "main_too_short", quality_meta

        return best[:1200], "viewer_fallback", quality_meta

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
                headers={
                    "Referer": "https://dart.fss.or.kr/",
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
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
                    if self._contains_error_page_tokens(text):
                        return "", meta
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
        return self._contains_error_page_tokens(text)

    def _contains_error_page_tokens(self, text: str) -> bool:
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
        score -= len(re.findall(r"(?:í|ì|ë|ê){4,}", text)) * 8
        return score

    def _is_mojibake_text(self, text: str) -> bool:
        if not text:
            return False
        replacement_ratio = text.count("�") / max(1, len(text))
        strange_latin = len(re.findall(r"[ÃÂÐØþ]", text))
        mojibake_jamo_like = len(re.findall(r"(?:í|ì|ë|ê){2,}", text))
        return (
            replacement_ratio > 0.002
            or strange_latin >= 5
            or mojibake_jamo_like >= 3
            or self._contains_mojibake_wrapper_text(text)
        )

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
