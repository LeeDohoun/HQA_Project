"""
Toss Invest Community Crawler (API-based + Cookie Bootstrap)
=============================================================
Uses Playwright once to grab session cookies from the browser,
then hits the API directly with aiohttp for speed.

On auth errors (400/401/403), automatically re-bootstraps cookies
via Playwright and retries.

API endpoint:
  GET https://wts-cert-api.tossinvest.com/api/v4/comments
  Params: subjectType=STOCK, subjectId={ISIN}, commentSortType=LATEST
  Pagination: lastCommentId={result.key} when result.hasNext=True
"""
import asyncio
import json
import logging
import os
import random
from datetime import datetime
from dateutil import parser as dateparser
from http.cookies import SimpleCookie

import aiohttp
import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

import config

logger = logging.getLogger(__name__)

# Realistic Chrome user-agents
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Shared state: picked during cookie bootstrap
# (must match between Playwright and aiohttp)
_current_ua: str = USER_AGENTS[0]
_xsrf_token: str = ""
_extra_headers: dict = {}  # Any additional headers discovered from browser


# ============================================================
# COOKIE BOOTSTRAP VIA PLAYWRIGHT
# ============================================================
async def bootstrap_cookies(stock_code: str = "A005930") -> list[dict]:
    """
    Launch a headless browser, visit the Toss Invest community page,
    wait for it to load, capture cookies AND request headers the browser
    sends to the comments API.
    """
    global _current_ua, _xsrf_token, _extra_headers
    _current_ua = random.choice(USER_AGENTS)

    logger.info(f"Bootstrapping cookies via Playwright (visiting {stock_code} community)...")

    cookies = []
    captured_request_headers = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_current_ua,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        # Apply stealth
        stealth = Stealth()
        await stealth.apply_stealth_async(context)

        page = await context.new_page()

        # Intercept REQUEST headers to the comments API
        async def capture_request(route, request):
            url = request.url
            if "wts-cert-api" in url and "comments" in url:
                captured_request_headers.update(dict(request.headers))
                logger.info(f"  Captured request headers for: {url}")
            await route.continue_()

        await page.route("**/*", capture_request)

        try:
            url = config.COMMUNITY_URL_TEMPLATE.format(stock_code=stock_code)
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            # Give extra time for cookies and API calls
            await asyncio.sleep(3)

            # Grab all cookies
            cookies = await context.cookies()
            logger.info(f"  Got {len(cookies)} cookies from browser")
            cookie_names = [c["name"] for c in cookies]
            logger.info(f"  Cookie names: {cookie_names}")

            # Extract XSRF token
            for c in cookies:
                if c["name"] == "XSRF-TOKEN":
                    _xsrf_token = c["value"]
                    logger.info(f"  XSRF-TOKEN found (length={len(_xsrf_token)})")
                    break

            # Store captured request headers
            if captured_request_headers:
                _extra_headers = captured_request_headers
                logger.info(f"  Captured browser request headers: {list(captured_request_headers.keys())}")
            else:
                logger.warning("  No request headers captured from comments API call")

        except Exception as e:
            logger.error(f"  Cookie bootstrap failed: {e}")
        finally:
            await context.close()
            await browser.close()

    return cookies


def cookies_to_jar(cookies: list[dict]) -> aiohttp.CookieJar:
    """Convert Playwright cookies to an aiohttp CookieJar."""
    jar = aiohttp.CookieJar(unsafe=True)  # unsafe=True to allow cross-domain

    for cookie in cookies:
        morsel = SimpleCookie()
        name = cookie["name"]
        value = cookie["value"]
        morsel[name] = value

        if cookie.get("domain"):
            morsel[name]["domain"] = cookie["domain"]
        if cookie.get("path"):
            morsel[name]["path"] = cookie["path"]
        if cookie.get("secure"):
            morsel[name]["secure"] = True

        jar.update_cookies(morsel)

    return jar


def _get_headers(stock_code: str = "A005930") -> dict:
    """
    Build request headers matching what the real browser sends.

    If we captured actual browser request headers during bootstrap,
    use those as the base (most accurate). Otherwise fall back to
    a manually constructed set.
    """
    if _extra_headers:
        # Use the real browser headers as base, override a few
        headers = dict(_extra_headers)
        headers["Referer"] = config.COMMUNITY_URL_TEMPLATE.format(stock_code=stock_code)
        # IMPORTANT: Remove 'cookie' — aiohttp sends cookies via its cookie jar.
        # Having both causes duplicate/malformed cookie headers.
        headers.pop("cookie", None)
        headers.pop("Cookie", None)
        # Make sure XSRF token header is set
        if _xsrf_token:
            headers["X-XSRF-TOKEN"] = _xsrf_token
            headers["x-xsrf-token"] = _xsrf_token
        return headers

    # Fallback: manually constructed headers
    headers = {
        "User-Agent": _current_ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://www.tossinvest.com",
        "Referer": config.COMMUNITY_URL_TEMPLATE.format(stock_code=stock_code),
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "Connection": "keep-alive",
    }

    # Add XSRF token header (critical for auth)
    if _xsrf_token:
        headers["X-XSRF-TOKEN"] = _xsrf_token
        headers["x-xsrf-token"] = _xsrf_token

    return headers


async def human_delay(min_s: float = config.MIN_DELAY, max_s: float = config.MAX_DELAY):
    """Sleep for a random human-like interval with jitter."""
    delay = random.uniform(min_s, max_s) + random.uniform(0, 0.5)
    await asyncio.sleep(delay)


# ============================================================
# ISIN RESOLUTION
# ============================================================
async def resolve_isin(session: aiohttp.ClientSession, stock_code: str) -> str | None:
    """Resolve stock code (A005930) to ISIN (KR7005930003)."""
    # Deterministic fallback pattern
    digits = stock_code.lstrip("A")
    isin_guess = f"KR7{digits.zfill(6)}003"

    url = config.STOCK_INFO_API.format(stock_code=stock_code)
    try:
        async with session.get(url, headers=_get_headers(stock_code), timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data is None:
                    logger.warning(f"  Stock info API returned null for {stock_code}")
                    return isin_guess
                result = data.get("result") or {}
                isin = result.get("isinCode") or result.get("guid")
                if isin:
                    return isin
    except Exception as e:
        logger.warning(f"  ISIN API lookup failed for {stock_code}: {e}")

    logger.info(f"  Using deterministic ISIN for {stock_code}: {isin_guess}")
    return isin_guess


# ============================================================
# COMMENT FETCHING (with auth-error detection)
# ============================================================
AUTH_ERROR_CODES = {401, 403}  # True auth errors — 400 is bad request, handled separately


async def fetch_comments_page(
    session: aiohttp.ClientSession,
    isin: str,
    stock_code: str,
    last_comment_id: int | None = None,
    sort_type: str = None,
) -> tuple[dict | None, bool]:
    """
    Fetch one page of comments from the API.

    Returns:
        (result_dict, needs_reauth)
        - result_dict: the API result, or None on failure
        - needs_reauth: True if we got an auth error and need fresh cookies
    """
    if sort_type is None:
        sort_type = config.COMMENT_SORT_TYPE

    params = {
        "subjectType": "STOCK",
        "subjectId": isin,
        "commentSortType": sort_type,
    }
    if last_comment_id is not None:
        params["lastCommentId"] = last_comment_id

    for attempt in range(config.MAX_RETRIES):
        try:
            headers = _get_headers(stock_code)
            async with session.get(
                config.COMMENTS_API,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data is None:
                        logger.warning(f"  API returned null body for {stock_code}")
                        return None, False
                    logger.debug(f"  API 200 OK, keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                    return data.get("result", {}), False

                elif resp.status in AUTH_ERROR_CODES:
                    # Log the response body for debugging
                    try:
                        error_body = await resp.text()
                        logger.warning(
                            f"  Auth error ({resp.status}) for {stock_code} — "
                            f"response body: {error_body[:500]}"
                        )
                        # Also log request headers we sent (redact cookie values)
                        sent_headers = {k: (v[:20] + '...' if k.lower() == 'cookie' else v)
                                       for k, v in headers.items()}
                        logger.debug(f"  Request headers sent: {sent_headers}")
                    except Exception:
                        logger.warning(f"  Auth error ({resp.status}) for {stock_code}")
                    return None, True  # Signal: needs re-auth

                elif resp.status == 429:
                    wait = config.RETRY_BACKOFF_BASE * (2 ** attempt) + random.uniform(1, 5)
                    logger.warning(f"  Rate limited (429). Waiting {wait:.1f}s...")
                    await asyncio.sleep(wait)

                else:
                    try:
                        error_body = await resp.text()
                        logger.warning(
                            f"  HTTP {resp.status} for {stock_code}, attempt {attempt + 1} — "
                            f"body: {error_body[:500]}"
                        )
                    except Exception:
                        logger.warning(f"  HTTP {resp.status} for {stock_code}, attempt {attempt + 1}")
                    await asyncio.sleep(config.RETRY_BACKOFF_BASE * (attempt + 1))

        except asyncio.TimeoutError:
            logger.warning(f"  Timeout for {stock_code}, attempt {attempt + 1}")
            await asyncio.sleep(config.RETRY_BACKOFF_BASE * (attempt + 1))
        except Exception as e:
            logger.error(f"  Request error for {stock_code}: {e}, attempt {attempt + 1}")
            await asyncio.sleep(config.RETRY_BACKOFF_BASE * (attempt + 1))

    return None, False


def parse_comment(comment: dict, stock_code: str, stock_name: str) -> dict:
    """Parse a single comment into a flat dict for CSV output."""
    # Use `or {}` instead of default={} because fields may exist with value None
    author = comment.get("author") or {}
    message = comment.get("message") or {}
    statistic = comment.get("statistic") or {}
    holding = comment.get("holding") or {}

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "comment_id": comment.get("commentId"),
        "type": comment.get("type"),
        "parent_id": comment.get("parentId"),
        # Author
        "author_id": author.get("userProfileId"),
        "author_nickname": author.get("nickname"),
        "author_badge": (author.get("badge") or {}).get("title"),
        "author_type": author.get("type"),
        # Content
        "title": message.get("title", ""),
        "message": message.get("message", ""),
        "represent_emoji": message.get("representEmoji", ""),
        # Stats
        "like_count": statistic.get("likeCount", 0),
        "reply_count": statistic.get("replyCount", 0),
        "repost_count": statistic.get("repostCount", 0),
        "read_count": statistic.get("readCount", 0),
        "follower_count": statistic.get("followerCount", 0),
        # Metadata
        "holding_status": (holding or {}).get("shareHoldingStatus"),
        "edited": comment.get("edited", False),
        "is_repost": comment.get("isRepost", False),
        "access_level": comment.get("accessLevel"),
        "created_at": comment.get("createdAt"),
        "updated_at": comment.get("updatedAt"),
        # Image
        "has_image": bool((comment.get("image") or {}).get("commentPictureUrl")),
        "image_url": (comment.get("image") or {}).get("commentPictureUrl"),
        # Raw JSON
        "raw_json": json.dumps(comment, ensure_ascii=False, default=str),
    }


def is_before_start_date(created_at_str: str) -> bool:
    """Check if a comment's date is before our crawl start date."""
    if not created_at_str:
        return False
    try:
        dt = dateparser.parse(created_at_str)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt < config.START_DATE
    except Exception:
        return False


# ============================================================
# CRAWL ONE STOCK (with auth retry)
# ============================================================
async def crawl_stock_community(
    session: aiohttp.ClientSession,
    stock_code: str,
    stock_name: str,
    isin: str,
    on_auth_error: callable,
) -> list[dict]:
    """
    Crawl all community comments for a single stock.
    If auth error occurs, calls on_auth_error() to refresh cookies
    and retries the failed page.
    """
    logger.info(f"Crawling: {stock_name} ({stock_code}) ISIN={isin}")

    all_comments = []
    last_comment_id = None
    page_num = 0
    reached_date_limit = False
    auth_retries = 0
    max_auth_retries = 3

    while True:
        page_num += 1
        result, needs_reauth = await fetch_comments_page(
            session, isin, stock_code, last_comment_id
        )

        # Handle auth error: refresh cookies and retry this page
        if needs_reauth and auth_retries < max_auth_retries:
            auth_retries += 1
            logger.info(f"  Auth retry {auth_retries}/{max_auth_retries}: refreshing cookies...")
            await on_auth_error()
            page_num -= 1  # Retry same page
            await human_delay(2, 4)
            continue
        elif needs_reauth:
            logger.error(f"  Max auth retries reached for {stock_code}, giving up")
            break

        if result is None:
            logger.warning(f"  Failed to fetch page {page_num} for {stock_code}")
            break

        comments = result.get("results", [])
        has_next = result.get("hasNext", False)
        next_key = result.get("key")
        total_count = result.get("totalCount", "?")

        if not comments:
            logger.info(f"  No comments on page {page_num}")
            break

        # Parse and filter by date
        page_parsed = []
        for comment in comments:
            created_at = comment.get("createdAt", "")
            if is_before_start_date(created_at):
                reached_date_limit = True
                break
            parsed = parse_comment(comment, stock_code, stock_name)
            page_parsed.append(parsed)

        # Write this page to CSV immediately
        if page_parsed:
            append_results(page_parsed)
            all_comments.extend(page_parsed)

        logger.info(
            f"  Page {page_num}: saved {len(page_parsed)} comments to CSV "
            f"(total collected: {len(all_comments)}, total on server: {total_count})"
        )

        if reached_date_limit:
            logger.info(f"  Reached date limit ({config.START_DATE.date()}), stopping")
            break
        if not has_next or next_key is None:
            logger.info(f"  No more pages")
            break

        last_comment_id = next_key
        auth_retries = 0  # Reset after successful page
        await human_delay()

    logger.info(f"  Done: {len(all_comments)} comments for {stock_name}")
    return all_comments


# ============================================================
# CHECKPOINT / RESUME
# ============================================================
def load_checkpoint() -> set:
    if os.path.exists(config.CHECKPOINT_FILE):
        with open(config.CHECKPOINT_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("completed_stocks", []))
    return set()


def save_checkpoint(completed_stocks: set):
    with open(config.CHECKPOINT_FILE, "w") as f:
        json.dump({
            "completed_stocks": list(completed_stocks),
            "last_updated": datetime.now().isoformat(),
        }, f, indent=2)


def append_results(comments: list[dict]):
    if not comments:
        return
    df = pd.DataFrame(comments)
    file_exists = os.path.exists(config.OUTPUT_CSV)
    df.to_csv(
        config.OUTPUT_CSV,
        mode="a",
        header=not file_exists,
        index=False,
        encoding="utf-8-sig",
    )


# ============================================================
# MAIN RUNNER
# ============================================================
async def run_crawler(stock_df: pd.DataFrame, resume: bool = True):
    """
    Main entry point:
    1. Bootstrap cookies via Playwright
    2. Create aiohttp session with those cookies
    3. Crawl all stocks using the API
    4. On auth errors, re-bootstrap cookies and continue
    """
    completed = load_checkpoint() if resume else set()
    remaining = stock_df[~stock_df["stock_code"].isin(completed)]

    logger.info(f"Total stocks: {len(stock_df)}, Already done: {len(completed)}, Remaining: {len(remaining)}")

    if remaining.empty:
        logger.info("All stocks already crawled! Delete checkpoint.json to re-crawl.")
        return

    # Step 1: Bootstrap cookies
    cookies = await bootstrap_cookies()
    if not cookies:
        logger.error("Failed to bootstrap cookies. Cannot proceed.")
        return

    # Step 2: Create session with cookies
    cookie_jar = cookies_to_jar(cookies)
    # Use AsyncResolver (aiodns) to fix DNS timeout issues on macOS
    try:
        resolver = aiohttp.AsyncResolver()
    except Exception:
        resolver = aiohttp.DefaultResolver()
        logger.warning("aiodns not available, using default resolver (may be slow)")
    connector = aiohttp.TCPConnector(
        limit=config.MAX_CONCURRENT_REQUESTS,
        resolver=resolver,
        ttl_dns_cache=300,  # Cache DNS for 5 minutes
    )
    timeout = aiohttp.ClientTimeout(total=30)

    consecutive_errors = 0
    total_comments = 0

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        cookie_jar=cookie_jar,
    ) as session:

        # Callback for auth errors: re-bootstrap and update session cookies
        async def refresh_cookies():
            nonlocal cookies
            new_cookies = await bootstrap_cookies()
            if new_cookies:
                cookies = new_cookies
                # Update the session's cookie jar
                new_jar = cookies_to_jar(new_cookies)
                session._cookie_jar = new_jar
                logger.info("  Session cookies refreshed successfully")
            else:
                logger.error("  Failed to refresh cookies!")

        for idx, row in remaining.iterrows():
            stock_code = row["stock_code"]
            stock_name = row["stock_name"]

            # Error threshold cooldown
            if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    f"Hit {consecutive_errors} consecutive errors. "
                    f"Refreshing cookies and pausing {config.ERROR_PAUSE_SECONDS}s..."
                )
                await refresh_cookies()
                await asyncio.sleep(config.ERROR_PAUSE_SECONDS)
                consecutive_errors = 0

            try:
                # Resolve ISIN (with retries + cookie refresh)
                isin = None
                for isin_attempt in range(config.MAX_RETRIES):
                    isin = await resolve_isin(session, stock_code)
                    if isin:
                        break
                    logger.warning(f"  ISIN resolve attempt {isin_attempt + 1}/{config.MAX_RETRIES} failed for {stock_code}")
                    if isin_attempt < config.MAX_RETRIES - 1:
                        logger.info("  Refreshing cookies and retrying ISIN resolution...")
                        await refresh_cookies()
                        await human_delay(2, 5)

                if not isin:
                    logger.error(f"  Could not resolve ISIN for {stock_code} after {config.MAX_RETRIES} attempts, skipping")
                    consecutive_errors += 1
                    continue

                await human_delay(0.5, 1.5)

                # Crawl comments (with auth retry callback)
                comments = await crawl_stock_community(
                    session, stock_code, stock_name, isin,
                    on_auth_error=refresh_cookies,
                )

                if comments:
                    # CSV already written per-page inside crawl_stock_community
                    total_comments += len(comments)
                    consecutive_errors = 0
                else:
                    logger.warning(f"  No comments found for {stock_name}")

                # Mark as completed
                completed.add(stock_code)
                save_checkpoint(completed)

                progress = len(completed) / len(stock_df) * 100
                logger.info(
                    f"Progress: {len(completed)}/{len(stock_df)} ({progress:.1f}%) "
                    f"| Total comments: {total_comments}"
                )

            except Exception as e:
                import traceback
                logger.error(f"Failed to crawl {stock_code} ({stock_name}): {e}")
                logger.error(traceback.format_exc())
                consecutive_errors += 1

            # Delay between stocks
            await human_delay(config.MIN_STOCK_DELAY, config.MAX_STOCK_DELAY)

    logger.info(f"Crawling complete! Total comments collected: {total_comments}")
    logger.info(f"Data saved to: {config.OUTPUT_CSV}")
