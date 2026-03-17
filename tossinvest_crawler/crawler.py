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
    on_page_done: callable = None,
    resume_cursor: int | None = None,
    resume_count: int = 0,
) -> list[dict]:
    """
    Crawl all community comments for a single stock.
    If auth error occurs, calls on_auth_error() to refresh cookies
    and retries the failed page.

    Args:
        on_page_done: async callback(stock_code, last_comment_id, comment_count)
                      called after each page is written — used to save cursor progress.
        resume_cursor: if resuming a partial crawl, the lastCommentId to start from.
        resume_count: if resuming, the number of comments already collected.
    """
    if resume_cursor:
        logger.info(f"Resuming: {stock_name} ({stock_code}) ISIN={isin} from cursor={resume_cursor}, already have {resume_count} comments")
    else:
        logger.info(f"Crawling: {stock_name} ({stock_code}) ISIN={isin}")

    all_comments = []
    last_comment_id = resume_cursor
    total_collected = resume_count
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

        # Write this page to CSV immediately (async lock for concurrency safety)
        if page_parsed:
            await append_results(page_parsed, stock_code)
            all_comments.extend(page_parsed)
            total_collected += len(page_parsed)

        logger.info(
            f"  Page {page_num}: saved {len(page_parsed)} comments to CSV "
            f"(total collected: {total_collected}, total on server: {total_count})"
        )

        # Save cursor progress after each successful page
        if on_page_done and next_key is not None:
            await on_page_done(stock_code, next_key, total_collected, isin)

        if reached_date_limit:
            logger.info(f"  Reached date limit ({config.START_DATE.date()}), stopping")
            break
        if not has_next or next_key is None:
            logger.info(f"  No more pages")
            break

        last_comment_id = next_key
        auth_retries = 0  # Reset after successful page
        await human_delay()

    logger.info(f"  Done: {total_collected} comments for {stock_name}")
    return all_comments


# ============================================================
# CHECKPOINT / RESUME
# ============================================================
# Checkpoint structure:
# {
#   "completed_stocks": ["A005930", ...],
#   "in_progress": {
#       "A000660": {"last_comment_id": 12345, "comment_count": 340, "isin": "KR7000660001"}
#   },
#   "last_updated": "2026-03-17T..."
# }

def load_checkpoint() -> tuple[set, dict]:
    """
    Returns:
        (completed_stocks, in_progress)
        - completed_stocks: set of stock codes fully crawled
        - in_progress: dict of {stock_code: {last_comment_id, comment_count, isin}}
    """
    if os.path.exists(config.CHECKPOINT_FILE):
        with open(config.CHECKPOINT_FILE, "r") as f:
            data = json.load(f)
            completed = set(data.get("completed_stocks", []))
            in_progress = data.get("in_progress", {})
            return completed, in_progress
    return set(), {}


def save_checkpoint(completed_stocks: set, in_progress: dict = None):
    with open(config.CHECKPOINT_FILE, "w") as f:
        json.dump({
            "completed_stocks": list(completed_stocks),
            "in_progress": in_progress or {},
            "last_updated": datetime.now().isoformat(),
        }, f, indent=2)


# Lock for thread-safe CSV writes from concurrent tasks
_csv_lock = asyncio.Lock()


async def append_results(comments: list[dict], stock_code: str):
    """Write comments to per-stock CSV: output/by_stock/{stock_code}.csv"""
    if not comments:
        return
    async with _csv_lock:
        df = pd.DataFrame(comments)
        per_stock_path = os.path.join(config.PER_STOCK_DIR, f"{stock_code}.csv")
        per_stock_exists = os.path.exists(per_stock_path)
        df.to_csv(
            per_stock_path,
            mode="a",
            header=not per_stock_exists,
            index=False,
            encoding="utf-8-sig",
        )


# Lock for thread-safe checkpoint writes
_checkpoint_lock = asyncio.Lock()


async def save_checkpoint_async(completed_stocks: set, in_progress: dict = None):
    async with _checkpoint_lock:
        save_checkpoint(completed_stocks, in_progress)


# ============================================================
# CRAWL A SINGLE STOCK (wrapper with ISIN resolution + retries)
# ============================================================
async def crawl_one_stock(
    session: aiohttp.ClientSession,
    stock_code: str,
    stock_name: str,
    refresh_cookies: callable,
    completed: set,
    in_progress: dict,
    total_stocks: int,
) -> int:
    """
    Crawl a single stock end-to-end: resolve ISIN, fetch all comments.
    Supports resuming partial crawls via in_progress cursor tracking.
    Returns the number of comments collected.
    """
    try:
        # Check if we have a saved cursor from a previous partial crawl
        resume_info = in_progress.get(stock_code)
        resume_cursor = None
        resume_count = 0
        isin = None

        if resume_info:
            resume_cursor = resume_info.get("last_comment_id")
            resume_count = resume_info.get("comment_count", 0)
            isin = resume_info.get("isin")
            logger.info(
                f"  [{stock_code}] Resuming partial crawl: "
                f"cursor={resume_cursor}, already have {resume_count} comments"
            )

        # Resolve ISIN if we don't have one from checkpoint
        if not isin:
            for isin_attempt in range(config.MAX_RETRIES):
                isin = await resolve_isin(session, stock_code)
                if isin:
                    break
                logger.warning(f"  [{stock_code}] ISIN resolve attempt {isin_attempt + 1}/{config.MAX_RETRIES} failed")
                if isin_attempt < config.MAX_RETRIES - 1:
                    logger.info(f"  [{stock_code}] Refreshing cookies and retrying ISIN resolution...")
                    await refresh_cookies()
                    await human_delay(2, 5)

        if not isin:
            logger.error(f"  [{stock_code}] Could not resolve ISIN after {config.MAX_RETRIES} attempts, skipping")
            return 0

        await human_delay(0.5, 1.5)

        # Callback: save cursor progress after each page
        async def on_page_done(sc, cursor, count, resolved_isin):
            in_progress[sc] = {
                "last_comment_id": cursor,
                "comment_count": count,
                "isin": resolved_isin,
            }
            await save_checkpoint_async(completed, in_progress)

        # Crawl comments (with resume support)
        comments = await crawl_stock_community(
            session, stock_code, stock_name, isin,
            on_auth_error=refresh_cookies,
            on_page_done=on_page_done,
            resume_cursor=resume_cursor,
            resume_count=resume_count,
        )

        # Mark as completed, remove from in_progress
        completed.add(stock_code)
        in_progress.pop(stock_code, None)
        await save_checkpoint_async(completed, in_progress)

        count = resume_count + (len(comments) if comments else 0)
        progress = len(completed) / total_stocks * 100
        logger.info(
            f"  [{stock_code}] DONE: {count} comments | "
            f"Progress: {len(completed)}/{total_stocks} ({progress:.1f}%)"
        )
        return count

    except Exception as e:
        import traceback
        logger.error(f"  [{stock_code}] Failed: {e}")
        logger.error(traceback.format_exc())
        # in_progress cursor is already saved from on_page_done callbacks,
        # so next run will resume from the last successful page
        return 0


# ============================================================
# MAIN RUNNER (concurrent batches)
# ============================================================
async def run_crawler(stock_df: pd.DataFrame, resume: bool = True):
    """
    Main entry point:
    1. Bootstrap cookies via Playwright
    2. Create aiohttp session with those cookies
    3. Crawl stocks concurrently in batches of CONCURRENT_STOCKS
    4. On auth errors, re-bootstrap cookies and continue
    """
    completed, in_progress = load_checkpoint() if resume else (set(), {})

    # Fresh start: wipe old per-stock CSVs and checkpoint to avoid duplicates
    if not resume:
        import glob as glob_mod
        old_csvs = glob_mod.glob(os.path.join(config.PER_STOCK_DIR, "*.csv"))
        if old_csvs:
            logger.info(f"Fresh start: removing {len(old_csvs)} old per-stock CSV files")
            for f in old_csvs:
                os.remove(f)
        if os.path.exists(config.CHECKPOINT_FILE):
            os.remove(config.CHECKPOINT_FILE)

    # Stocks to crawl: not yet completed (in_progress stocks ARE included — they need to resume)
    remaining = stock_df[~stock_df["stock_code"].isin(completed)]

    resuming_count = sum(1 for sc in in_progress if sc in set(remaining["stock_code"]))
    logger.info(
        f"Total stocks: {len(stock_df)}, Already done: {len(completed)}, "
        f"Resuming partial: {resuming_count}, Remaining: {len(remaining)}"
    )

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
    try:
        resolver = aiohttp.AsyncResolver()
    except Exception:
        resolver = aiohttp.DefaultResolver()
        logger.warning("aiodns not available, using default resolver (may be slow)")
    connector = aiohttp.TCPConnector(
        limit=config.MAX_CONCURRENT_REQUESTS * 2,  # Allow enough connections
        resolver=resolver,
        ttl_dns_cache=300,
    )
    timeout = aiohttp.ClientTimeout(total=30)

    total_comments = 0
    batch_size = config.CONCURRENT_STOCKS

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        cookie_jar=cookie_jar,
    ) as session:

        # Callback for auth errors: re-bootstrap and update session cookies
        _refresh_lock = asyncio.Lock()

        async def refresh_cookies():
            nonlocal cookies
            # Only one task refreshes at a time
            async with _refresh_lock:
                new_cookies = await bootstrap_cookies()
                if new_cookies:
                    cookies = new_cookies
                    new_jar = cookies_to_jar(new_cookies)
                    session._cookie_jar = new_jar
                    logger.info("  Session cookies refreshed successfully")
                else:
                    logger.error("  Failed to refresh cookies!")

        # Process stocks in concurrent batches
        remaining_list = list(remaining.iterrows())
        for batch_start in range(0, len(remaining_list), batch_size):
            batch = remaining_list[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            total_batches = (len(remaining_list) + batch_size - 1) // batch_size

            stock_names = [row["stock_name"] for _, row in batch]
            logger.info(
                f"\n{'='*60}\n"
                f"Batch {batch_num}/{total_batches}: "
                f"crawling {len(batch)} stocks concurrently: {stock_names}\n"
                f"{'='*60}"
            )

            # Launch all stocks in this batch concurrently
            tasks = [
                crawl_one_stock(
                    session=session,
                    stock_code=row["stock_code"],
                    stock_name=row["stock_name"],
                    refresh_cookies=refresh_cookies,
                    completed=completed,
                    in_progress=in_progress,
                    total_stocks=len(stock_df),
                )
                for _, row in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Tally results
            for result in results:
                if isinstance(result, int):
                    total_comments += result
                elif isinstance(result, Exception):
                    logger.error(f"  Batch task exception: {result}")

            logger.info(
                f"Batch {batch_num} complete | "
                f"Total comments so far: {total_comments} | "
                f"Stocks done: {len(completed)}/{len(stock_df)}"
            )

            # Delay between batches
            if batch_start + batch_size < len(remaining_list):
                await human_delay(config.MIN_STOCK_DELAY, config.MAX_STOCK_DELAY)

    logger.info(f"\nCrawling complete! Total comments collected: {total_comments}")
    logger.info(f"Per-stock CSVs saved to: {config.PER_STOCK_DIR}/")
