"""
Fetch KOSPI top 500 stocks by market cap using pykrx (Korea Exchange data).
Falls back to manual KRX API if pykrx is unavailable.
"""
import os
import logging
import pandas as pd
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


def get_kospi_top_stocks(top_n: int = config.TOP_N_STOCKS) -> pd.DataFrame:
    """
    Returns a DataFrame with columns: [stock_code, stock_name, market_cap]
    sorted by market_cap descending, limited to top_n.

    The stock_code is in Toss Invest format: 'A' + 6-digit code (e.g., 'A005930')
    """
    # Check cache first
    if os.path.exists(config.STOCK_LIST_CACHE):
        logger.info(f"Loading cached stock list from {config.STOCK_LIST_CACHE}")
        df = pd.read_csv(config.STOCK_LIST_CACHE, dtype={"stock_code": str, "krx_code": str})
        if len(df) >= top_n:
            return df.head(top_n)

    logger.info("Fetching KOSPI stock list from KRX via pykrx...")
    df = _fetch_via_pykrx(top_n)

    if df is not None and len(df) > 0:
        # Cache the result
        df.to_csv(config.STOCK_LIST_CACHE, index=False)
        logger.info(f"Cached {len(df)} stocks to {config.STOCK_LIST_CACHE}")
        return df

    logger.warning("pykrx failed, trying fallback KRX API method...")
    df = _fetch_via_krx_api(top_n)

    if df is not None and len(df) > 0:
        df.to_csv(config.STOCK_LIST_CACHE, index=False)
        logger.info(f"Cached {len(df)} stocks to {config.STOCK_LIST_CACHE}")
        return df

    raise RuntimeError("Failed to fetch KOSPI stock list from all sources.")


def _fetch_via_pykrx(top_n: int) -> pd.DataFrame | None:
    """Use pykrx library to get KOSPI market cap data."""
    try:
        from pykrx import stock as pykrx_stock

        # Use a recent trading day
        today = datetime.now()
        date_str = today.strftime("%Y%m%d")

        # Try last 7 days in case today is not a trading day
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

        # market_cap_df index = ticker code, columns include '시가총액'
        market_cap_df = market_cap_df.reset_index()
        market_cap_df.columns = [c.strip() for c in market_cap_df.columns]

        # Rename columns
        col_map = {}
        if "티커" in market_cap_df.columns:
            col_map["티커"] = "krx_code"
        elif "index" in market_cap_df.columns:
            col_map["index"] = "krx_code"
        else:
            # First column is usually the ticker
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

        # Sort by market cap descending
        market_cap_df = market_cap_df.sort_values("market_cap", ascending=False)

        # Create Toss Invest stock code format: 'A' + 6-digit code
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


def _fetch_via_krx_api(top_n: int) -> pd.DataFrame | None:
    """
    Fallback: Fetch KOSPI stock list directly from KRX Open API.
    This uses the publicly available KRX data API.
    """
    try:
        import requests

        # KRX market cap endpoint
        url = "http://data.krx.co.kr/comm/bldAttend498/getJsonData.cmd"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        today = datetime.now()
        # Try recent trading days
        for offset in range(7):
            try_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            payload = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
                "locale": "ko_KR",
                "mktId": "STK",  # KOSPI
                "trdDd": try_date,
                "share": "1",
                "money": "1",
                "csvxls_is498No": "0",
            }

            resp = requests.post(url, data=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if "OutBlock_1" in data and data["OutBlock_1"]:
                    rows = data["OutBlock_1"]
                    break
        else:
            logger.warning("KRX API returned no data")
            return None

        df = pd.DataFrame(rows)

        # Map KRX column names
        rename_map = {
            "ISU_SRT_CD": "krx_code",
            "ISU_ABBRV": "stock_name",
            "MKTCAP": "market_cap",
        }
        df = df.rename(columns=rename_map)
        df["market_cap"] = pd.to_numeric(df["market_cap"].str.replace(",", ""), errors="coerce")
        df = df.sort_values("market_cap", ascending=False)
        df["stock_code"] = "A" + df["krx_code"].astype(str).str.zfill(6)

        result = df[["stock_code", "krx_code", "stock_name", "market_cap"]].head(top_n).reset_index(drop=True)
        logger.info(f"Successfully fetched top {len(result)} stocks via KRX API")
        return result

    except Exception as e:
        logger.error(f"KRX API error: {e}")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = get_kospi_top_stocks(10)
    print(df.to_string())
