"""
Configuration for Toss Invest Community Crawler
"""
import os
from datetime import datetime, timedelta

# ============================================================
# API ENDPOINTS (discovered from browser network inspection)
# ============================================================
# Comments API — the main endpoint for community posts
COMMENTS_API = "https://wts-cert-api.tossinvest.com/api/v4/comments"

# Stock info API — resolves stock code (A005930) to ISIN (KR7005930003)
STOCK_INFO_API = "https://wts-info-api.tossinvest.com/api/v2/stock-infos/code-or-symbol/{stock_code}"

# Web URL (used as Referer header)
BASE_URL = "https://www.tossinvest.com"
COMMUNITY_URL_TEMPLATE = BASE_URL + "/stocks/{stock_code}/community"

# ============================================================
# DATE RANGE
# ============================================================
END_DATE = datetime.now()
START_DATE = datetime(2025, 3, 1)  # Crawl back to March 1, 2025

# ============================================================
# KOSPI TOP-N SETTINGS
# ============================================================
TOP_N_STOCKS = 500  # Number of top KOSPI stocks by market cap

# ============================================================
# COMMENT SORT TYPE
# ============================================================
# POPULAR = sorted by popularity, LATEST = sorted by time
COMMENT_SORT_TYPE = "RECENT"  # POPULAR or RECENT (not LATEST)

# ============================================================
# ANTI-BLOCKING SETTINGS
# ============================================================
# Random delay between API requests (seconds)
MIN_DELAY = 1.0
MAX_DELAY = 3.0

# Delay between stocks (seconds)
MIN_STOCK_DELAY = 3.0
MAX_STOCK_DELAY = 8.0

# Max consecutive errors before pausing
MAX_CONSECUTIVE_ERRORS = 5
ERROR_PAUSE_SECONDS = 120  # 2-min cooldown after hitting error threshold

# Max retries per request
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 10  # seconds, exponential backoff

# ============================================================
# CONCURRENCY
# ============================================================
CONCURRENT_STOCKS = 10  # Number of stocks to crawl simultaneously
MAX_CONCURRENT_REQUESTS = 20  # Max parallel HTTP connections (>= CONCURRENT_STOCKS)

# ============================================================
# OUTPUT
# ============================================================
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Per-stock CSV directory: output/by_stock/A005930.csv, etc.
PER_STOCK_DIR = os.path.join(OUTPUT_DIR, "by_stock")
os.makedirs(PER_STOCK_DIR, exist_ok=True)

# (Per-stock CSVs only — no combined output file)
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")
STOCK_LIST_CACHE = os.path.join(OUTPUT_DIR, "kospi_top500_stocks.csv")

# ============================================================
# LOGGING
# ============================================================
LOG_FILE = os.path.join(OUTPUT_DIR, "crawler.log")
LOG_LEVEL = "DEBUG"  # Set to "INFO" for production
