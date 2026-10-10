from __future__ import annotations

# File role:
# - Shared HTTP session, retry logic, and date normalization utilities.

from datetime import datetime
import time
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BaseCollector:
    def __init__(self, timeout: int = 20, max_retries: int = 3, backoff_seconds: float = 1.0):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Connection": "keep-alive",
            }
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def get_with_retry(
        self,
        url: str,
        *,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        timeout: Optional[int] = None,
        log_prefix: str = "COLLECT",
    ) -> requests.Response:
        parsed = urlsplit(url)
        safe_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        response = None
        last_status = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout or self.timeout,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                last_status = _http_status(e)
                status_text = f" status={last_status}" if last_status else ""
                print(
                    f"[WARN][{log_prefix}] GET failed "
                    f"attempt={attempt + 1}/{self.max_retries} url={safe_url} error={type(e).__name__}{status_text}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff_seconds * (attempt + 1))

        status_text = f" status={last_status}" if last_status else ""
        raise RetryExhaustedError(f"[{log_prefix}] GET failed after retries: {safe_url}{status_text}",
                                  last_status) from None

    @staticmethod
    def to_iso_datetime(
        value: str,
        in_formats: List[str],
        default_time: str = "00:00:00",
    ) -> str:
        if not value:
            return ""

        raw = value.strip()
        for fmt in in_formats:
            try:
                dt = datetime.strptime(raw, fmt)
                if "%H" not in fmt and "%M" not in fmt and "%S" not in fmt:
                    hh, mm, ss = map(int, default_time.split(":"))
                    dt = dt.replace(hour=hh, minute=mm, second=ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue

        return ""


class RetryExhaustedError(requests.RequestException):
    """Every attempt failed; ``status`` is the last attempt's HTTP status (None for transport errors)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def http_status_suffix(error: BaseException) -> str:
    """" status=NNN" for an HTTP failure, so wrapped final errors keep rate limits detectable."""
    status = getattr(error, "status", None)
    if type(status) is not int:
        status = _http_status(error)
    return f" status={status}" if status else ""


def _http_status(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if type(status) is int else None
