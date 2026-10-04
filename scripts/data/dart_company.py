"""Plan company snapshots without requests, credentials, or .env loading."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import load_project_env
from src.ingestion.dart_company import DEFAULT_DATA_DIR, DartCompanyCollector


DEFAULT_CORP_CODES = DEFAULT_DATA_DIR.parent.parent / "HQA_data_backup_20260913" / "corp_codes.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect OpenDART company industry classifications")
    parser.add_argument("--corp-codes", type=Path, default=DEFAULT_CORP_CODES)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--max-requests", type=int, default=3000, help="Per-KST-day request ceiling across reruns")
    parser.add_argument("--execute", action="store_true", help="Load environment and call DART")
    args = parser.parse_args()
    if args.execute:
        load_project_env()
    collector = DartCompanyCollector(os.getenv("DART_API_KEY") if args.execute else None,
                                     data_dir=args.data_dir)
    print(json.dumps(collector.collect(args.corp_codes, max_requests=args.max_requests,
                                       execute=args.execute), ensure_ascii=False))


if __name__ == "__main__":
    main()
