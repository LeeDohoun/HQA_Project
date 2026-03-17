"""
Fetch KOSPI top 500 stocks by market cap.

Priority:
  1. Cached CSV (if exists and has enough rows)
  2. Naver Finance API (public, no auth required)
  3. pykrx library (if installed)
"""
import os
import logging
import math

import pandas as pd
import requests
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


def get_kospi_top_stocks(top_n: int = config.TOP_N_STOCKS) -> pd.DataFrame:
    """
    Returns a DataFrame with columns: [stock_code, krx_code, stock_name, market_cap]
    sorted by market_cap descending, limited to top_n.

    The stock_code is in Toss Invest format: 'A' + 6-digit code (e.g., 'A005930')
    """
    # Check cache first
    if os.path.exists(config.STOCK_LIST_CACHE):
        logger.info(f"Loading cached stock list from {config.STOCK_LIST_CACHE}")
        df = pd.read_csv(config.STOCK_LIST_CACHE, dtype={"stock_code": str, "krx_code": str})
        if len(df) >= top_n:
            return df.head(top_n)

    # Try Naver Finance API first (most reliable, no dependencies)
    logger.info("Fetching KOSPI stock list from Naver Finance API...")
    df = _fetch_via_naver(top_n)

    if df is not None and len(df) > 0:
        df.to_csv(config.STOCK_LIST_CACHE, index=False)
        logger.info(f"Cached {len(df)} stocks to {config.STOCK_LIST_CACHE}")
        return df

    # Fallback: pykrx
    logger.warning("Naver API failed, trying pykrx...")
    df = _fetch_via_pykrx(top_n)

    if df is not None and len(df) > 0:
        df.to_csv(config.STOCK_LIST_CACHE, index=False)
        logger.info(f"Cached {len(df)} stocks to {config.STOCK_LIST_CACHE}")
        return df

    raise RuntimeError("Failed to fetch KOSPI stock list from all sources.")


def _fetch_via_naver(top_n: int) -> pd.DataFrame | None:
    """
    Fetch KOSPI stocks sorted by market cap from Naver Finance mobile API.
    Public endpoint, no auth required.

    API: https://m.stock.naver.com/api/stocks/marketValue/KOSPI?page={page}&pageSize={size}

    Returns stocks with: itemCode, stockName, marketValue, etc.
    Only returns actual stocks (filters out ETFs and other non-stock items).
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }

        all_stocks = []
        page_size = 100
        # Fetch more than needed since we'll filter out ETFs
        pages_needed = math.ceil((top_n * 1.5) / page_size)

        for page in range(1, pages_needed + 1):
            url = (
                f"https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
                f"?page={page}&pageSize={page_size}"
            )
            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                logger.warning(f"Naver API returned {resp.status_code} on page {page}")
                break

            data = resp.json()
            stocks = data.get("stocks", [])

            if not stocks:
                break

            for s in stocks:
                # Filter: only actual stocks (stockEndType == "stock")
                if s.get("stockEndType") != "stock":
                    continue

                code = s.get("itemCode", "")
                name = s.get("stockName", "")
                market_value = s.get("marketValue", "0")

                # Parse market value (comes as formatted string like "11,649,847")
                try:
                    mv_numeric = int(str(market_value).replace(",", ""))
                except (ValueError, TypeError):
                    mv_numeric = 0

                all_stocks.append({
                    "stock_code": f"A{code.zfill(6)}",
                    "krx_code": code,
                    "stock_name": name,
                    "market_cap": mv_numeric,
                })

            logger.info(f"  Naver page {page}: got {len(stocks)} items, {len(all_stocks)} stocks total")

            if len(all_stocks) >= top_n:
                break

        if not all_stocks:
            logger.warning("Naver API returned no stock data")
            return None

        df = pd.DataFrame(all_stocks)
        # Already sorted by market cap from API, but ensure it
        df = df.sort_values("market_cap", ascending=False).head(top_n).reset_index(drop=True)
        logger.info(f"Successfully fetched top {len(df)} KOSPI stocks via Naver Finance API")
        return df

    except Exception as e:
        import traceback
        logger.error(f"Naver API error: {e}")
        logger.debug(traceback.format_exc())
        return None


def _fetch_via_pykrx(top_n: int) -> pd.DataFrame | None:
    """Use pykrx library to get KOSPI market cap data."""
    try:
        from pykrx import stock as pykrx_stock

        # Use a recent trading day
        today = datetime.now()
        date_str = today.strftime("%Y%m%d")

        # Try last 7 days in case today is not a trading day
        tickers = []
        for offset in range(7):
            try_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            tickers = pykrx_stock.get_market_ticker_list(try_date, market="KOSPI")
            if tickers:
                date_str = try_date
                break

        if not tickers:
            logger.warning("No tickers found from pykrx")
            return None

        logger.info(f"Found {len(tickers)} KOSPI tickers for date {date_str}")

        # Get market cap for all KOSPI stocks
        market_cap_df = pykrx_stock.get_market_cap_by_ticker(date_str, market="KOSPI")

        if market_cap_df.empty:
            return None

        market_cap_df = market_cap_df.reset_index()
        market_cap_df.columns = [c.strip() for c in market_cap_df.columns]

        # Rename columns
        col_map = {}
        if "티커" in market_cap_df.columns:
            col_map["티커"] = "krx_code"
        elif "index" in market_cap_df.columns:
            col_map["index"] = "krx_code"
        else:
            col_map[market_cap_df.columns[0]] = "krx_code"

        if "시가총액" in market_cap_df.columns:
            col_map["시가총액"] = "market_cap"

        market_cap_df = market_cap_df.rename(columns=col_map)

        # Get stock names
        names = {}
        for ticker in market_cap_df["krx_code"].tolist():
            try:
                name = pykrx_stock.get_market_ticker_name(ticker)
                names[ticker] = name
            except Exception:
                names[ticker] = ""

        market_cap_df["stock_name"] = market_cap_df["krx_code"].map(names)
        market_cap_df = market_cap_df.sort_values("market_cap", ascending=False)
        market_cap_df["stock_code"] = "A" + market_cap_df["krx_code"].astype(str).str.zfill(6)

        result = market_cap_df[["stock_code", "krx_code", "stock_name", "market_cap"]].head(top_n).reset_index(drop=True)
        logger.info(f"Successfully fetched top {len(result)} stocks via pykrx")
        return result

    except ImportError:
        logger.warning("pykrx not installed")
        return None
    except Exception as e:
        logger.error(f"pykrx error: {e}")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = get_kospi_top_stocks(10)
    print(df.to_string())
