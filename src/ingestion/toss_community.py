"""Toss Securities community comments for social-momentum counting.

Collects the same shape as NaverStockForumCollector so both feed one `forum`
corpus. Community text is retail chatter: it carries a low credibility score and
is used for mention volume, never as a disclosure-grade claim.

Endpoints are the Toss web app's own JSON calls, verified against live responses:
there is no documented public API, so a shape change surfaces as an explicit
failure rather than silently producing zero posts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .base import BaseCollector
from .types import DocumentRecord

KST = timezone(timedelta(hours=9))
MIN_BODY_LENGTH = 10
PAGE_SIZE = 50
# Retail community chatter: unverified author, unverified claim.
TOSS_CREDIBILITY = 0.30
TOSS_CONTENT_QUALITY = 0.40


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = text.replace("​", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


class TossCommunityCollector(BaseCollector):
    """Collects Toss Securities stock community comments, newest first."""

    _STOCK_INFO_URL = "https://wts-info-api.tossinvest.com/api/v2/stock-infos/A{code}"
    _COMMENTS_URL = "https://wts-cert-api.tossinvest.com/api/v3/comments"
    _PAGE_URL_TEMPLATE = "https://tossinvest.com/stocks/A{code}/community"

    def __init__(self, timeout: int = 20, max_retries: int = 3, backoff_seconds: float = 1.0):
        super().__init__(timeout=timeout, max_retries=max_retries, backoff_seconds=backoff_seconds)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://tossinvest.com/",
            "Origin": "https://tossinvest.com",
        })

    def _resolve_subject_id(self, stock_code: str) -> str:
        """Comments are keyed by ISIN, not by the six-digit KRX code."""
        response = self.get_with_retry(self._STOCK_INFO_URL.format(code=stock_code),
                                       log_prefix=f"TOSS:{stock_code}")
        result = (response.json() or {}).get("result")
        if not isinstance(result, dict):
            raise ValueError("unexpected Toss stock-info response")
        isin = result.get("isinCode")
        if not isinstance(isin, str) or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", isin):
            raise ValueError(f"Toss ISIN unavailable for {stock_code}")
        return isin

    def _fetch_comments(self, subject_id: str, before_id: Optional[int]) -> Dict[str, Any]:
        body: Dict[str, Any] = {"subjectId": subject_id, "subjectType": "STOCK",
                                "commentSortType": "RECENT", "size": PAGE_SIZE}
        if before_id is not None:
            body["commentId"] = before_id
        # This endpoint only answers POST; a GET returns 405.
        for attempt in range(self.max_retries):
            try:
                response = self.session.post(self._COMMENTS_URL, json=body, timeout=self.timeout)
                response.raise_for_status()
                break
            except Exception as exc:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[WARN][TOSS:{subject_id}] POST failed "
                      f"attempt={attempt + 1}/{self.max_retries} error={type(exc).__name__}")
        result = (response.json() or {}).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("comments"), dict):
            raise ValueError("unexpected Toss comments response")
        return result

    @staticmethod
    def _parse_comment(comment: Dict[str, Any], stock_code: str, page_url: str) -> Optional[Dict[str, str]]:
        if not isinstance(comment, dict):
            return None
        if comment.get("status") not in (None, "", "NORMAL", "ACTIVE"):
            return None
        body = _clean_text(comment.get("message") or comment.get("title") or "")
        if len(body) < MIN_BODY_LENGTH:
            return None
        comment_id = comment.get("id")
        if not isinstance(comment_id, int):
            return None
        # Toss exposes updatedAt only; an edited post carries its edit time.
        raw_date = str(comment.get("updatedAt") or "").strip()
        if not raw_date:
            return None
        try:
            stamp = datetime.fromisoformat(raw_date)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        return {"comment_id": comment_id, "body": body, "raw_date_text": raw_date,
                "published_at": stamp.astimezone(KST).replace(tzinfo=None).isoformat(),
                "edited": bool(comment.get("edited")),
                "is_reply": bool(comment.get("isReply")),
                "url": f"{page_url}#comment-{comment_id}"}

    def collect(self, stock_code: str, stock_name: str = "", pages: int = 3,
                from_date: str = "", to_date: str = "") -> List[DocumentRecord]:
        if not re.fullmatch(r"[0-9]{6}", str(stock_code)):
            raise ValueError("stock_code must contain six digits")
        if not isinstance(pages, int) or pages < 1:
            raise ValueError("pages must be a positive integer")

        subject_id = self._resolve_subject_id(stock_code)
        page_url = self._PAGE_URL_TEMPLATE.format(code=stock_code)
        collected_at = datetime.now(timezone.utc).isoformat()
        docs: List[DocumentRecord] = []
        seen: set[int] = set()
        before_id: Optional[int] = None

        for page in range(pages):
            result = self._fetch_comments(subject_id, before_id)
            comments = result["comments"].get("body") or []
            if not isinstance(comments, list):
                raise ValueError("invalid Toss comment list")
            print(f"[DEBUG][TOSS:{stock_code}:{page + 1}] comments={len(comments)}")
            if not comments:
                break

            stop = False
            for comment in comments:
                item = self._parse_comment(comment, stock_code, page_url)
                if item is None or item["comment_id"] in seen:
                    continue
                compact = item["published_at"][:10].replace("-", "")
                if to_date and compact > to_date:
                    continue
                if from_date and compact < from_date:
                    stop = True  # RECENT ordering: later pages are older still.
                    break
                seen.add(item["comment_id"])
                docs.append(DocumentRecord(
                    source_type="forum",
                    title=item["body"][:60],
                    content=item["body"],
                    url=item["url"],
                    stock_name=stock_name or "",
                    stock_code=stock_code,
                    published_at=item["published_at"],
                    metadata={
                        "source": "toss",
                        "platform": "toss_community",
                        "raw_date_text": item["raw_date_text"],
                        "collected_at": collected_at,
                        "content_source": "body",
                        "body_extracted": True,
                        "credibility_score": TOSS_CREDIBILITY,
                        "content_quality_score": TOSS_CONTENT_QUALITY,
                        "author_verified": False,
                        "is_reply": item["is_reply"],
                        "edited": item["edited"],
                        # updatedAt is an edit time, so an edited post's timestamp
                        # is not its original publication time.
                        "publication_time_status": "estimated" if item["edited"] else "observed",
                        "entity_match": {"matched": True, "basis": "platform_stock_board"},
                    },
                ))
            if stop or not result["comments"].get("hasNext"):
                break
            last = min(seen) if seen else result.get("lastCommentId")
            if not isinstance(last, int) or last == before_id:
                break
            before_id = last

        for doc in docs:
            doc.ensure_doc_id()
        print(f"[INFO][TOSS:{stock_code}] collected={len(docs)}")
        return docs
