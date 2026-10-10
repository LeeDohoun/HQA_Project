"""Collect the daily VKOSPI close without broker credentials or LLM calls."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env
from src.ingestion.krx_volatility import KrxVolatilityCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect dated KRX VKOSPI closes")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Last requested date, before today in Korea")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    load_project_env()
    data_dir = Path(args.data_dir) if args.data_dir else get_data_dir()
    path = data_dir / "market_context" / "volatility_index.jsonl"
    collector = KrxVolatilityCollector(path=path)
    rows = collector.collect_daily(args.from_date, args.to_date)
    saved = collector.save(rows)
    print(json.dumps({"collected_records": len(rows), "saved_records": saved, "path": str(path)}))


if __name__ == "__main__":
    main()
