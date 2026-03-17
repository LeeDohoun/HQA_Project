#!/usr/bin/env python3
"""
Toss Invest Community Crawler - Main Entry Point
=================================================

Crawls 종토방 (community discussion) comments for the top 500 KOSPI stocks
from the last 1 year, using the Toss Invest API directly.

Usage:
    # Full crawl (top 500 stocks)
    python main.py

    # Crawl specific number of top stocks
    python main.py --top 50

    # Crawl a single stock (for testing)
    python main.py --stock A005930

    # Fresh start (ignore checkpoint)
    python main.py --fresh

    # Export existing data to Excel
    python main.py --export-only

    # Sort by POPULAR instead of RECENT
    python main.py --sort POPULAR
"""
import argparse
import asyncio
import logging
import os
import sys

import pandas as pd

import config
from stock_codes import get_kospi_top_stocks
from crawler import run_crawler


def setup_logging():
    """Configure logging to both file and console."""
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.LOG_LEVEL))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def export_summary():
    """Print summary of per-stock CSV files."""
    per_stock_files = [f for f in os.listdir(config.PER_STOCK_DIR) if f.endswith(".csv")]
    if not per_stock_files:
        print("No per-stock CSV files found.")
        return

    print(f"\nPer-stock CSV files: {len(per_stock_files)} in {config.PER_STOCK_DIR}/")

    total_rows = 0
    for f in sorted(per_stock_files):
        path = os.path.join(config.PER_STOCK_DIR, f)
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
            total_rows += len(df)
            print(f"  {f}: {len(df)} comments")
        except Exception:
            print(f"  {f}: (error reading)")

    print(f"\nTotal comments across all stocks: {total_rows}")
    print("Done!")


def main():
    parser = argparse.ArgumentParser(description="Toss Invest Community Crawler")
    parser.add_argument("--top", type=int, default=config.TOP_N_STOCKS,
                       help=f"Number of top KOSPI stocks to crawl (default: {config.TOP_N_STOCKS})")
    parser.add_argument("--stock", type=str, default=None,
                       help="Crawl a single stock code (e.g., A005930)")
    parser.add_argument("--fresh", action="store_true",
                       help="Ignore checkpoint and start fresh")
    parser.add_argument("--export-only", action="store_true",
                       help="Only print summary of per-stock CSV files")
    parser.add_argument("--sort", type=str, default=None,
                       choices=["POPULAR", "RECENT"],
                       help="Comment sort type (default: LATEST)")

    args = parser.parse_args()
    setup_logging()
    logger = logging.getLogger("main")

    # Override sort type if specified
    if args.sort:
        config.COMMENT_SORT_TYPE = args.sort

    # Export only mode
    if args.export_only:
        export_summary()
        return

    # Single stock mode
    if args.stock:
        stock_df = pd.DataFrame([{
            "stock_code": args.stock,
            "stock_name": args.stock,
            "market_cap": 0,
        }])
    else:
        # Fetch KOSPI top stocks
        logger.info(f"Fetching top {args.top} KOSPI stocks...")
        stock_df = get_kospi_top_stocks(args.top)
        logger.info(f"Got {len(stock_df)} stocks. Top 5:")
        logger.info(stock_df.head().to_string())

    logger.info(f"\n{'='*60}")
    logger.info(f"Starting Toss Invest Community Crawler (API mode)")
    logger.info(f"  Stocks to crawl: {len(stock_df)}")
    logger.info(f"  Date range: {config.START_DATE.date()} ~ {config.END_DATE.date()}")
    logger.info(f"  Sort type: {config.COMMENT_SORT_TYPE}")
    logger.info(f"  Output: {config.PER_STOCK_DIR}/")
    logger.info(f"{'='*60}\n")

    # Run crawler
    asyncio.run(run_crawler(stock_df, resume=not args.fresh))

    # Print summary of collected data
    logger.info("\nExport summary:")
    export_summary()

    logger.info("\nAll done!")


if __name__ == "__main__":
    main()
