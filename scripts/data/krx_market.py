"""Plan or explicitly execute full-market KRX daily collection."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root, load_project_env
from src.ingestion.krx_market import backfill


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect historical daily prices for both KRX markets")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="Last requested date, before today in Korea")
    parser.add_argument("--data-dir")
    parser.add_argument("--execute", action="store_true", help="Make KRX requests and save observations")
    args = parser.parse_args()
    if args.execute:
        load_project_env()
    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser()
    else:
        data_dir = Path(os.getenv("HQA_DATA_DIR", "./data")).expanduser()
        if not data_dir.is_absolute():
            data_dir = get_project_root() / data_dir
    print(json.dumps(backfill(args.from_date, args.to_date, data_dir, execute=args.execute)))


if __name__ == "__main__":
    main()
