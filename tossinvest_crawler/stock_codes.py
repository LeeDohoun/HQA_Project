"""
Fetch KOSPI + KOSDAQ stocks by market cap via Naver Finance API.

- KOSPI: all stocks (0 = fetch all, or set TOP_N_KOSPI to limit)
- KOSDAQ: top N stocks by market cap (default 100)

Falls back to pykrx if Naver fails.
"""
import os
import logging
import math

import pandas as pd
import requests
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


def get_stock_list() -> pd.DataFrame:
    """
    Returns a combined DataFrame of KOSPI + KOSDAQ stocks with columns:
        [stock_code, krx_code, stock_name, market_cap, market]

    - KOSPI: all stocks (or top N if TOP_N_KOSPI > 0)
    - KOSDAQ: top TOP_N_KOSDAQ stocks
    - market column: "KOSPI" or "KOSDAQ"
    - stock_code is in Toss Invest format: 'A' + 6-digit code (e.g., 'A005930')
    """
    # Check cache first
    if os.path.exists(config.STOCK_LIST_CACHE):
        logger.info(f"Loading cached stock list from {config.STOCK_LIST_CACHE}")
        df = pd.read_csv(config.STOCK_LIST_CACHE, dtype={"stock_code": str, "krx_code": str})
        if len(df) > 0:
            return df

    # Fetch KOSPI
    logger.info("Fetching KOSPI stocks from Naver Finance API...")
    kospi_df = _fetch_via_naver("KOSPI", config.TOP_N_KOSPI)

    if kospi_df is None or len(kospi_df) == 0:
        logger.warning("Naver KOSPI failed, trying pykrx fallback...")
        kospi_df = _fetch_via_pykrx(config.TOP_N_KOSPI)

    if kospi_df is None or len(kospi_df) == 0:
        raise RuntimeError("Failed to fetch KOSPI stock list from all sources.")

    kospi_df["market"] = "KOSPI"
    logger.info(f"KOSPI: {len(kospi_df)} stocks")

    # Fetch KOSDAQ
    kosdaq_df = None
    if config.TOP_N_KOSDAQ > 0:
        logger.info(f"Fetching top {config.TOP_N_KOSDAQ} KOSDAQ stocks from Naver Finance API...")
        kosdaq_df = _fetch_via_naver("KOSDAQ", config.TOP_N_KOSDAQ)

        if kosdaq_df is not None and len(kosdaq_df) > 0:
            kosdaq_df["market"] = "KOSDAQ"
            logger.info(f"KOSDAQ: {len(kosdaq_df)} stocks")
        else:
            logger.warning("Failed to fetch KOSDAQ stocks")

    # Combine
    if kosdaq_df is not None and len(kosdaq_df) > 0:
        combined = pd.concat([kospi_df, kosdaq_df], ignore_index=True)
    else:
        combined = kospi_df

    # Cache
    combined.to_csv(config.STOCK_LIST_CACHE, index=False)
    logger.info(f"Cached {len(combined)} total stocks to {config.STOCK_LIST_CACHE}")

    return combined


# Keep old name as alias for backward compatibility
def get_kospi_top_stocks(top_n: int = None) -> pd.DataFrame:
    """Backward-compatible wrapper. Ignores top_n, uses config values."""
    return get_stock_list()


def _fetch_via_naver(market: str, top_n: int) -> pd.DataFrame | None:
    """
    Fetch stocks sorted by market cap from Naver Finance mobile API.

    Args:
        market: "KOSPI" or "KOSDAQ"
        top_n: number of stocks to fetch (0 = all)
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }

        all_stocks = []
        page_size = 100
        fetch_all = (top_n is None or top_n <= 0)
        max_pages = 100 if fetch_all else math.ceil((top_n * 1.5) / page_size)

        for page in range(1, max_pages + 1):
            url = (
                f"https://m.stock.naver.com/api/stocks/marketValue/{market}"
                f"?page={page}&pageSize={page_size}"
            )
            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                logger.warning(f"Naver API returned {resp.status_code} on {market} page {page}")
                break

            data = resp.json()
            stocks = data.get("stocks", [])

            if not stocks:
                break

            for s in stocks:
                # Filter: only actual stocks (skip ETFs, etc.)
                if s.get("stockEndType") != "stock":
                    continue

                code = s.get("itemCode", "")
                name = s.get("stockName", "")
                market_value = s.get("marketValue", "0")

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

            logger.info(
                f"  Naver {market} page {page}: got {len(stocks)} items, "
                f"{len(all_stocks)} stocks total"
            )

            if not fetch_all and len(all_stocks) >= top_n:
                break

        if not all_stocks:
            logger.warning(f"Naver API returned no {market} stock data")
            return None

        df = pd.DataFrame(all_stocks)
        df = df.sort_values("market_cap", ascending=False).reset_index(drop=True)
        if not fetch_all:
            df = df.head(top_n)
        logger.info(f"Successfully fetched {len(df)} {market} stocks via Naver Finance API")
        return df

    except Exception as e:
        import traceback
        logger.error(f"Naver API error ({market}): {e}")
        logger.debug(traceback.format_exc())
        return None


def _fetch_via_pykrx(top_n: int) -> pd.DataFrame | None:
    """Use pykrx library to get KOSPI market cap data (fallback)."""
    try:
        from pykrx import stock as pykrx_stock

        today = datetime.now()
        date_str = today.strftime("%Y%m%d")

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

        market_cap_df = pykrx_stock.get_market_cap_by_ticker(date_str, market="KOSPI")
        if market_cap_df.empty:
            return None

        market_cap_df = market_cap_df.reset_index()
        market_cap_df.columns = [c.strip() for c in market_cap_df.columns]

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

        fetch_all = (top_n is None or top_n <= 0)
        result = market_cap_df[["stock_code", "krx_code", "stock_name", "market_cap"]]
        if not fetch_all:
            result = result.head(top_n)
        result = result.reset_index(drop=True)
        logger.info(f"Successfully fetched {len(result)} stocks via pykrx")
        return result

    except ImportError:
        logger.warning("pykrx not installed")
        return None
    except Exception as e:
        logger.error(f"pykrx error: {e}")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = get_stock_list()
    print(f"\nTotal stocks: {len(df)}")
    kospi = df[df["market"] == "KOSPI"]
    kosdaq = df[df["market"] == "KOSDAQ"]
    print(f"KOSPI: {len(kospi)}, KOSDAQ: {len(kosdaq)}")
    print(f"\nTop 5 KOSPI:\n{kospi.head().to_string()}")
    print(f"\nTop 5 KOSDAQ:\n{kosdaq.head().to_string()}")
