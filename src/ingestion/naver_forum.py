from __future__ import annotations

# File role:
# - Collect Naver stock forum posts and Naver chart rows.
# - Forum collection prefers JSON endpoints and falls back to Playwright when needed.

import json as _json
import re
import time
from typing import Any, Dict, List, Optional

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from .base import BaseCollector
from .types import DocumentRecord


class NaverStockForumCollector(BaseCollector):
    MIN_BODY_LENGTH = 20

    # Naver Pay 증권 모바일 종목토론실 (Next.js SSR — __NEXT_DATA__ JSON 포함)
    _BOARD_URL_TEMPLATE = "https://m.stock.naver.com/domestic/stock/{code}/discuss?page={page}"
    _POST_URL_TEMPLATE = "https://m.stock.naver.com/domestic/stock/{code}/discuss/{post_id}"

    def collect(
        self,
        stock_code: str,
        pages: int = 3,
        from_date: str = "",
        to_date: str = "",
    ) -> List[DocumentRecord]:
        if BeautifulSoup is None:
            raise ImportError("beautifulsoup4가 필요합니다. pip install beautifulsoup4")

        # 종목별 API URL/채널 캐시 초기화
        self._cached_channel_id = ""
        self._cached_api_url_template = None
        self._last_offset: str = ""  # offset 기반 페이지네이션용

        docs: List[DocumentRecord] = []
        for page in range(1, pages + 1):
            page_url = self._BOARD_URL_TEMPLATE.format(code=stock_code, page=page)
            list_items = self._collect_forum_list_items(stock_code=stock_code, page=page, page_url=page_url)
            first_date = list_items[0]["published_at"][:10] if list_items else "N/A"
            print(f"[DEBUG][FORUM:{stock_code}:{page}] items_parsed={len(list_items)} first_date={first_date}")
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

                # 본문이 이미 추출된 경우 재요청 불필요
                prefetched_body = item.get("body", "")
                if prefetched_body and len(prefetched_body) >= self.MIN_BODY_LENGTH:
                    body, body_extracted = prefetched_body[:4000], True
                else:
                    body, body_extracted = self._fetch_forum_body(full_url, referer=page_url)

                content_source = "body" if body_extracted else "title_only"
                body_missing_reason = ""
                if not body_extracted:
                    body_missing_reason = "body_missing_or_too_short"
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

            time.sleep(0.3)

        return docs

    def _collect_forum_list_items(self, stock_code: str, page: int, page_url: str) -> List[Dict[str, str]]:
        # page=1: REST API 시도 → 실패 시 Playwright fallback
        if page == 1:
            channel_id = self._extract_channel_id("", stock_code)
            self._cached_channel_id = channel_id

        channel_id = getattr(self, "_cached_channel_id", "")
        cached = getattr(self, "_cached_api_url_template", None)

        # REST API가 이미 실패 확정된 경우 → Playwright로 전환
        if cached == self._API_FAILED_SENTINEL:
            return self._fetch_with_playwright(stock_code, page)

        posts = self._fetch_opentalk_posts_api(stock_code, channel_id, page)
        if not posts and getattr(self, "_cached_api_url_template", None) == self._API_FAILED_SENTINEL:
            # 방금 실패 확정됨 → page=1 Playwright 시도
            return self._fetch_with_playwright(stock_code, page)
        return posts

    def _fetch_with_playwright(self, stock_code: str, page: int) -> List[Dict[str, str]]:
        """Playwright 브라우저로 discuss 페이지 렌더링 → 네트워크 응답 인터셉트.

        Naver 종목토론실은 완전 CSR — JS 실행 없이는 데이터 접근 불가.
        Playwright가 설치되어 있지 않으면 빈 리스트 반환.

        설치:  pip install playwright && playwright install chromium
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            if page == 1:
                print(
                    "[WARN][FORUM] Playwright 미설치 - Naver 종목토론실 수집 불가.\n"
                    "        설치: pip install playwright && playwright install chromium"
                )
            return []

        from .base import USER_AGENT

        url = self._BOARD_URL_TEMPLATE.format(code=stock_code, page=page)
        captured_posts: List[Dict] = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 390, "height": 844},
                    locale="ko-KR",
                )
                pg = ctx.new_page()
                pg.set_extra_http_headers({
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
                })

                def _on_response(response):
                    url_lower = response.url.lower()
                    if not any(kw in url_lower for kw in ("discuss", "opentalk", "board", "post", "talk")):
                        return
                    try:
                        data = response.json()
                        posts = self._find_post_list_in_data(data)
                        if posts:
                            captured_posts.extend(posts)
                            print(f"[DEBUG][FORUM:{stock_code}] Playwright API captured {len(posts)} posts: {response.url}")
                    except Exception:
                        pass

                pg.on("response", _on_response)
                try:
                    pg.goto(url, wait_until="networkidle", timeout=30_000)
                except Exception:
                    pg.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # 목록이 없으면 스크롤로 lazy load 유도
                if not captured_posts:
                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    pg.wait_for_timeout(2000)

                browser.close()
        except Exception as e:
            print(f"[WARN][FORUM:{stock_code}] Playwright 오류: {e!r}")
            return []

        if page == 1:
            print(f"[DEBUG][FORUM:{stock_code}] Playwright total captured={len(captured_posts)}")
        return [p for p in (self._parse_next_data_post(post, stock_code) for post in captured_posts) if p]

    def _extract_channel_id(self, _html: str, stock_code: str) -> str:
        """channelInfo API 직접 호출로 OpenTalk channelId 추출.

        SSR __NEXT_DATA__의 opentalk/channelInfo 쿼리는 비로그인 상태에서
        data=null — front-api를 직접 호출해야 channelId를 얻을 수 있음.
        """
        api_headers = {
            "Referer": f"https://m.stock.naver.com/domestic/stock/{stock_code}/discuss",
            "Accept": "application/json, text/plain, */*",
        }
        channel_info_urls = [
            # front-api 없는 직접 경로 (queryKey 패턴 근거)
            f"https://m.stock.naver.com/opentalk/channelInfo?code={stock_code}",
            f"https://m.stock.naver.com/opentalk/channel?code={stock_code}",
            # front-api 경로 (이전 시도)
            f"https://m.stock.naver.com/front-api/v2/opentalk/channelInfo?code={stock_code}",
            f"https://m.stock.naver.com/front-api/v1/opentalk/channelInfo?code={stock_code}",
        ]
        for url in channel_info_urls:
            try:
                resp = self.session.get(url, headers=api_headers, timeout=self.timeout)
                print(f"[DEBUG][FORUM:{stock_code}] channelInfo {resp.status_code}: {url}")
                if resp.status_code == 200:
                    data = resp.json()
                    result = data if isinstance(data, dict) else {}
                    # result 키 중첩 여부 확인
                    if "result" in result:
                        result = result["result"]
                    channel_id = str(result.get("channelId", result.get("id", result.get("channelCode", ""))))
                    print(f"[DEBUG][FORUM:{stock_code}] channelInfo result keys={list(result.keys())[:10]} channelId={channel_id!r}")
                    if channel_id:
                        return channel_id
            except Exception as e:
                print(f"[DEBUG][FORUM:{stock_code}] channelInfo error: {e!r}: {url}")
        return ""

    # page 1에서 모든 후보 실패 확정 시 마킹 — 이후 페이지 재시도 방지
    _API_FAILED_SENTINEL = "__FAILED__"

    def _fetch_opentalk_posts_api(self, stock_code: str, channel_id: str, page: int) -> List[Dict[str, str]]:
        """Naver Pay 증권 discuss 게시글을 직접 API 호출로 수집.

        page=1에서 성공한 URL 템플릿을 캐시, 이후 페이지는 재사용.
        page=1에서 전부 실패 시 sentinel을 저장해 이후 페이지 재시도 생략.
        """
        api_headers = {
            "Referer": f"https://m.stock.naver.com/domestic/stock/{stock_code}/discuss",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }

        cached = getattr(self, "_cached_api_url_template", None)

        # page=1 실패 확정 → 이후 페이지 모두 빈 결과 반환
        if cached == self._API_FAILED_SENTINEL:
            return []

        # 성공 URL 캐시 있으면 바로 사용
        if cached:
            url = cached.format(stock_code=stock_code, channel_id=channel_id or stock_code, page=page)
            # offset 기반 엔드포인트(front-api/discussion/list)는 page 파라미터 대신 lastOffset 사용
            if self._last_offset and "lastOffset" not in url and "offset" not in url:
                url = url + f"&offset={self._last_offset}"
            return self._call_api_and_parse(url, stock_code, api_headers, log=False)

        # page=1 최초 시도: 후보 URL 순서대로 탐색
        # queryKey 패턴 '/opentalk/channelInfo' 에서 기저 URL 추론:
        #   m.stock.naver.com 직접 경로 (front-api 없이) 우선 시도
        candidates: List[str] = []
        if channel_id:
            candidates += [
                f"https://m.stock.naver.com/opentalk/messages?channelId={channel_id}&page={{page}}&size=20",
                f"https://m.stock.naver.com/opentalk/posts?channelId={channel_id}&page={{page}}&size=20",
            ]
        candidates += [
            # front-api/discussion/list — Playwright 인터셉트로 확인된 실제 엔드포인트 (우선 시도)
            f"https://m.stock.naver.com/front-api/discussion/list?discussionType=domesticStock&itemCode={{stock_code}}&pageSize=50&isHolderOnly=false&excludesItemNews=false&isItemNewsOnly=false",
            # apis.naver.com — serviceCode 파라미터 추가 (error_code 응답 분석 기반)
            f"https://apis.naver.com/stock/v2/discuss/list?code={{stock_code}}&serviceCode=STOCK&page={{page}}&size=20",
            f"https://apis.naver.com/stock/v2/discuss/list?code={{stock_code}}&serviceCode=STOCK&pageNo={{page}}&pageSize=20",
            f"https://apis.naver.com/stock/v2/discuss/list?objectId={{stock_code}}&serviceCode=STOCK&page={{page}}&size=20",
            f"https://apis.naver.com/stock/v2/discuss/list?code={{stock_code}}&page={{page}}&size=20",
            # Next.js API 라우트 (/api/ prefix)
            f"https://m.stock.naver.com/api/opentalk/channelInfo?code={{stock_code}}",
            f"https://m.stock.naver.com/api/discuss/list?code={{stock_code}}&page={{page}}&size=20",
            f"https://m.stock.naver.com/api/opentalk/messages?code={{stock_code}}&page={{page}}&size=20",
            # m.stock.naver.com 직접 경로 (front-api 없음)
            f"https://m.stock.naver.com/opentalk/messages?code={{stock_code}}&page={{page}}&size=20",
            f"https://m.stock.naver.com/discuss/list?code={{stock_code}}&page={{page}}&size=20",
        ]

        # 실제 요청 시 page=1 고정 (템플릿 placeholder가 있으므로)
        for tmpl in candidates:
            url = tmpl.format(stock_code=stock_code, channel_id=channel_id or stock_code, page=1)
            posts = self._call_api_and_parse(url, stock_code, api_headers, log=True)
            if posts:
                self._cached_api_url_template = tmpl
                print(f"[INFO][FORUM:{stock_code}] API 성공 템플릿: {tmpl}")
                return posts

        print(f"[WARN][FORUM:{stock_code}] 모든 API 후보 실패 - forum 수집 불가")
        self._cached_api_url_template = self._API_FAILED_SENTINEL
        return []

    def _call_api_and_parse(
        self,
        url: str,
        stock_code: str,
        headers: Dict,
        *,
        log: bool,
    ) -> List[Dict[str, str]]:
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code != 200:
                if log:
                    print(f"[DEBUG][FORUM:{stock_code}] API {resp.status_code}: {url}")
                return []
            try:
                data = resp.json()
            except Exception:
                if log:
                    print(f"[DEBUG][FORUM:{stock_code}] API 200 non-JSON ({resp.encoding}): {url}")
                return []
            if isinstance(data, dict) and ("error_code" in data or "errorCode" in data):
                ec = data.get("error_code", data.get("errorCode", ""))
                msg = data.get("message", data.get("msg", ""))
                if log:
                    print(f"[DEBUG][FORUM:{stock_code}] API 200 ERROR error_code={ec!r} message={msg!r}: {url}")
                return []
            if log:
                top_keys = list(data.keys()) if isinstance(data, dict) else "list"
                print(f"[DEBUG][FORUM:{stock_code}] API 200 keys={top_keys}: {url}")
            # offset 기반 페이지네이션: result.lastOffset 저장
            result_obj = data.get("result", {}) if isinstance(data, dict) else {}
            last_offset = str(result_obj.get("lastOffset", "")).strip() if isinstance(result_obj, dict) else ""
            if last_offset:
                self._last_offset = last_offset
            post_list = self._find_post_list_in_data(data)
            if post_list and log:
                print(f"[DEBUG][FORUM:{stock_code}] post_list count={len(post_list)} sample_keys={list(post_list[0].keys())}")
            return [p for p in (self._parse_next_data_post(post, stock_code) for post in post_list) if p]
        except Exception as e:
            if log:
                print(f"[DEBUG][FORUM:{stock_code}] API 오류: {e!r}: {url}")
            return []

    def _find_post_list_in_data(self, data: Any) -> List[Dict]:
        """API 응답 JSON에서 게시글 목록 재귀 탐색."""
        if not data:
            return []
        if isinstance(data, list):
            return data if (data and isinstance(data[0], dict)) else []
        if not isinstance(data, dict):
            return []

        # useInfiniteQuery 형태: {"pages": [...]}
        if "pages" in data and isinstance(data["pages"], list):
            for page_item in data["pages"]:
                found = self._find_post_list_in_data(page_item)
                if found:
                    return found

        for key in ("list", "items", "posts", "messages", "discussions", "boardList", "data", "result", "content"):
            val = data.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
            if isinstance(val, dict):
                found = self._find_post_list_in_data(val)
                if found:
                    return found
        return []

    def _parse_next_data_post(self, post: Dict, stock_code: str) -> Optional[Dict[str, str]]:
        """단일 게시글 dict → 표준 item dict 변환."""
        # 제목 필드명 후보
        title = ""
        for f in ("title", "subject", "postTitle"):
            v = post.get(f, "")
            if v:
                title = _clean_text(str(v))
                break

        # 게시글 ID / URL
        post_id = str(post.get("postId", post.get("id", post.get("nid", ""))))
        if post_id:
            full_url = self._POST_URL_TEMPLATE.format(code=stock_code, post_id=post_id)
        else:
            full_url = ""

        # 날짜 필드명 후보 (ISO 또는 yyyyMMdd HH:mm:ss 등)
        raw_date = ""
        for f in ("writtenAt", "regDate", "writeDate", "date", "createdAt", "datetime", "registDate"):
            v = str(post.get(f, "")).strip()
            if v:
                raw_date = v
                break

        published_at = self.to_iso_datetime(
            raw_date,
            [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y.%m.%d %H:%M:%S",
                "%Y.%m.%d %H:%M",
                "%Y%m%d%H%M%S",
                "%Y%m%d",
            ],
            default_time="00:00:00",
        )

        # 본문이 이미 포함되어 있으면 같이 반환
        body = ""
        for f in ("contentSwReplacedButImg", "contentSwReplaced", "content", "body", "contentText", "text"):
            v = post.get(f, "")
            if v:
                body = _clean_text(str(v))
                break

        if not title or not published_at:
            return None
        return {
            "title": title,
            "url": full_url,
            "raw_date_text": raw_date,
            "published_at": published_at,
            "body": body,
        }

    def _fetch_forum_body(self, url: str, *, referer: str = "") -> tuple[str, bool]:
        if BeautifulSoup is None or not url:
            return "", False

        try:
            response = self.get_with_retry(
                url,
                log_prefix="FORUM:READ",
                headers={
                    "Referer": referer or "https://m.stock.naver.com/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
        except Exception:
            return "", False

        html = response.content.decode("utf-8", errors="replace")

        # __NEXT_DATA__에서 본문 추출 시도
        soup = BeautifulSoup(html, "html.parser")
        next_el = soup.select_one("script#__NEXT_DATA__")
        if next_el:
            try:
                data = _json.loads(next_el.string or "{}")
                page_props = data.get("props", {}).get("pageProps", {})
                post = page_props.get("post", page_props.get("discuss", page_props.get("detail", {})))
                for f in ("content", "body", "contentText", "text"):
                    v = post.get(f, "") if isinstance(post, dict) else ""
                    if v:
                        cleaned = self._sanitize_forum_body(_clean_text(str(v)))
                        if len(cleaned) >= self.MIN_BODY_LENGTH:
                            return cleaned[:4000], True
            except Exception:
                pass

        # fallback: HTML 직접 파싱
        body = self._extract_forum_body_from_soup(soup)
        cleaned = self._sanitize_forum_body(body)
        if len(cleaned) < self.MIN_BODY_LENGTH:
            return "", False
        return cleaned[:4000], True

    def _extract_forum_body_from_soup(self, soup: Any) -> str:
        selectors = [
            "div.discuss_content",
            "div.post_content",
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
            response = self.get_with_retry(
                url,
                log_prefix=f"CHART:{stock_code}:{page}",
                headers={"Referer": f"https://finance.naver.com/item/main.naver?code={stock_code}"},
            )

            # Naver sise_day 는 CP949/EUC-KR 인코딩 — requests가 ISO-8859-1로 잘못 감지할 수 있어 명시 디코딩
            html = response.content.decode("cp949", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table.type2 tr")
            if not rows:
                print(f"[WARN][CHART:{stock_code}:{page}] table.type2 tr 행 없음 - HTML 구조 변경 가능성")
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
