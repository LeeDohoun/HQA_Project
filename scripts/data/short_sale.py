"""Collect KRX short-sale and lending balances without broker credentials or LLM calls."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env
from src.ingestion.krx_short_sale import KrxShortSaleCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect dated KRX short-sale and lending balances")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Last requested date, before today in Korea")
    parser.add_argument("--stock-codes", nargs="*", help="Six-digit codes; omit to store every listed stock")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    load_project_env()
    data_dir = Path(args.data_dir) if args.data_dir else get_data_dir()
    path = data_dir / "market_context" / "short_sale.jsonl"
    collector = KrxShortSaleCollector(path=path)
    rows = collector.collect_range(args.from_date, args.to_date, stock_codes=args.stock_codes)
    saved = collector.save(rows)
    print(json.dumps({"collected_records": len(rows), "saved_records": saved,
                      "path": str(path), "stock_codes": args.stock_codes or "all"}))


if __name__ == "__main__":
    main()
