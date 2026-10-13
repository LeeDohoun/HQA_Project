"""Plan the local delisting supplement; --execute alone loads DART settings."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root, load_project_env
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_list_supplement import DEFAULT_DATA_DIR, DEFAULT_MAX_REQUESTS, supplement


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    data_dir, key = args.data_dir or DEFAULT_DATA_DIR, None
    if args.execute:
        load_project_env()
        key = os.getenv("DART_API_KEY")
        if args.data_dir is None:
            data_dir = Path(os.getenv("HQA_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
            if not data_dir.is_absolute():
                data_dir = get_project_root() / data_dir
    try:
        result = supplement(data_dir=data_dir, execute=args.execute, api_key=key, max_requests=args.max_requests)
    except (ValueError, DartAPIError):
        print(json.dumps({"status": "error", "error": "DART supplement configuration, archive or provider response invalid"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
