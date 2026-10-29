"""News candidates found by Claude's web search on the user's Claude subscription.

Only the search step changes: Claude (the logged-in Claude CLI, as for LLM_PROVIDER=claude_plan)
lists recent articles about the stock, and NaverNewsCollector then fetches each article page
itself for the body and the publication time. Claude's own dates count as estimates, so an
article whose page does not state its time is rejected by the evidence loader instead of being
trusted, and an invented URL simply fails to fetch.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from typing import Any, Iterator

from .naver_news import NaverNewsCollector

ARTICLE_SCHEMA = {
    "type": "object",
    "required": ["articles"],
    "properties": {"articles": {"type": "array", "maxItems": 10, "items": {
        "type": "object",
        "required": ["url", "title", "press", "published_date", "summary"],
        "properties": {
            "url": {"type": "string"}, "title": {"type": "string"}, "press": {"type": "string"},
            "published_date": {"type": "string", "description": "YYYY-MM-DD as the article states it"},
            "summary": {"type": "string"},
        },
    }}},
}
SYSTEM_PROMPT = ("You find recent news articles about one Korean listed company with web search. List only "
                 "articles on news publishers' own pages, never portal search pages, blogs, forums or "
                 "brokerage PDFs, and never invent a URL or a date.")


class ClaudeNewsCollector(NaverNewsCollector):
    def __init__(self, timeout: int = 20, call_timeout: int = 300):
        super().__init__(timeout=timeout)
        self.model = os.getenv("HQA_CLAUDE_NEWS_MODEL", "claude-sonnet-5-5")
        self.cli_path = os.getenv("HQA_CLAUDE_CLI", "claude")
        self.call_timeout = call_timeout

    def collect(self, keyword: str, max_items: int = 20, from_date: str = "", to_date: str = "",
                max_pages: int = 100):
        # A page without its own publication time would only be rejected later as unverified.
        return [doc for doc in super().collect(keyword, max_items, from_date, to_date, max_pages)
                if doc.metadata.get("publication_time_status") == "confirmed"]

    def _collect_search_candidates(self, *, keyword: str, from_date: str, to_date: str,
                                   max_pages: int) -> Iterator[dict[str, Any]]:
        for article in self._search(keyword, from_date, to_date):
            url = article["url"].strip()
            day = article["published_date"].strip()
            if not url.startswith(("https://", "http://")):
                continue
            yield {"url": url, "title": article["title"].strip(), "summary": article["summary"].strip(),
                   "press": article["press"].strip(), "raw_date_text": day,
                   "published_at": f"{day}T00:00:00+09:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else "",
                   "date_is_estimated": True}

    def _search(self, keyword: str, from_date: str, to_date: str) -> list[dict[str, Any]]:
        from src.utils.claude_plan_chat import _cli_env

        # The analysis reads news from the last seven days only; a year-long collection window
        # would spend the search on stale articles.
        if to_date:
            week_ago = (datetime.strptime(to_date, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")
            from_date = max(from_date or week_ago, week_ago)
        period = f"{from_date or '최근 7일'} ~ {to_date or '오늘'} (KST)"
        prompt = (f"웹검색으로 다음 종목의 최근 뉴스 기사를 찾아라: {keyword}\n기간: {period}\n"
                  "언론사 기사 페이지 URL만, 최신순으로 최대 8건. 각 기사의 제목, 언론사, 기사에 적힌 발행일"
                  "(YYYY-MM-DD), 실적·수주·규제·공시·수급 등 투자 판단에 관련된 내용을 2~3문장으로 요약. "
                  "찾지 못하면 빈 목록.")
        command = [self.cli_path, "-p", "--output-format", "json", "--model", self.model, "--effort", "low",
                   "--system-prompt", SYSTEM_PROMPT, "--tools", "WebSearch", "--allowedTools", "WebSearch",
                   "--safe-mode", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
                   "--disable-slash-commands", "--json-schema", json.dumps(ARTICLE_SCHEMA, ensure_ascii=False)]
        with tempfile.TemporaryDirectory(prefix="hqa-claude-news-") as workdir:
            completed = subprocess.run(command, input=prompt, capture_output=True, text=True, cwd=workdir,
                                       timeout=self.call_timeout,
                                       env=_cli_env(os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or None))
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError:
            detail = ((completed.stderr or "").strip().splitlines() or [""])[-1][:200]
            raise RuntimeError(f"claude_news_cli_exit_{completed.returncode}:{detail}") from None
        if data.get("is_error") or data.get("subtype") != "success":
            raise RuntimeError(f"claude_news_failed:{str(data.get('result') or data.get('subtype'))[:200]}")
        articles = (data.get("structured_output") or {}).get("articles")
        if not isinstance(articles, list):
            raise RuntimeError("claude_news_no_structured_output")
        return [row for row in articles if isinstance(row, dict)
                and all(isinstance(row.get(key), str) for key in ARTICLE_SCHEMA["properties"]["articles"]["items"]["required"])]
