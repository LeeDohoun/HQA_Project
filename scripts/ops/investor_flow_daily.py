"""Collect dedicated PAPER-data investor flow; default to a local-only plan."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root
from src.ingestion.kis_investor_flow import KisInvestorFlowCollector, load_stock_codes
from src.utils.stock_codes import is_stock_code


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily KIS investor flow from a dedicated PAPER data key")
    parser.add_argument("--data-dir")
    parser.add_argument("--execute", action="store_true", help="Call KIS and save observations")
    parser.add_argument("--max-stocks", type=int, help="Limit the selected universe for smoke tests")
    parser.add_argument("--codes", help="Comma-separated manual stock subset, overriding the stored KRX universe")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir or os.getenv("HQA_DATA_DIR", "./data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = get_project_root() / data_dir
    try:
        if args.max_stocks is not None and args.max_stocks < 1:
            raise ValueError("max-stocks must be positive")
        codes = ([code.strip() for code in args.codes.split(",")]
                 if args.codes is not None else load_stock_codes(data_dir))
        if not codes or any(not is_stock_code(code) for code in codes):
            raise ValueError("invalid stock subset")
        codes = list(dict.fromkeys(codes))
        if args.max_stocks is not None:
            codes = codes[:args.max_stocks]
        collector = KisInvestorFlowCollector(
            os.getenv("KIS_DATA_APP_KEY") if args.execute else None,
            os.getenv("KIS_DATA_APP_SECRET") if args.execute else None, data_dir=data_dir)
        summary = collector.collect_day(codes, execute=args.execute)
    except (ValueError, OSError, KeyError):
        # Configuration/cache text and provider exception bodies must never reach logs.
        print(json.dumps({"status": "error", "error": "Investor flow configuration or archive is invalid"}))
        raise SystemExit(1) from None
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
