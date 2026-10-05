"""Plan or read LH001 business text offline; fetching requires --execute."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root, load_project_env
from src.ingestion.dart_api import DartAPIError
from src.ingestion.dart_backfill import DEFAULT_DATA_DIR
from src.ingestion.dart_business_text import BusinessTextCollector, load_business_text, select_report
from src.ingestion.storage import read_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Point-in-time DART business overview text for LH001")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "fetch", "show"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--data-dir", default=None, help="Data root (default: checkout/data)")
        if command in {"plan", "fetch"}:
            subparser.add_argument("--requests-file", required=True, help="JSONL: stock_code, decision_date")
        if command == "fetch":
            subparser.add_argument("--max-requests", required=True, type=int, help="Combined cap per KST day")
            subparser.add_argument("--execute", action="store_true", help="Load environment and download ZIPs")
        if command == "show":
            subparser.add_argument("--stock-code", required=True)
            subparser.add_argument("--date", required=True, help="Exclusive decision date, YYYY-MM-DD")
    args = parser.parse_args()
    execute = args.command == "fetch" and args.execute
    api_key, data_dir = None, Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    if execute:
        load_project_env()
        api_key = os.getenv("DART_API_KEY")
        if args.data_dir is None:
            data_dir = Path(os.getenv("HQA_DATA_DIR", str(DEFAULT_DATA_DIR)))
    data_dir = data_dir.expanduser()
    if not data_dir.is_absolute():
        data_dir = get_project_root() / data_dir
    try:
        if args.command == "show":
            text = load_business_text(args.stock_code, args.date, data_dir)
            result = {"selected_report": select_report(args.stock_code, args.date, data_dir),
                      "business_text": {**text, "text": text["text"][:500]} if text else None}
        else:
            path = Path(args.requests_file)
            if not path.is_file():
                raise ValueError("DART requests file is missing")
            rows = read_rows(path)
            if any("stock_code" not in row or "decision_date" not in row for row in rows):
                raise ValueError("DART requests require stock_code and decision_date")
            collector = BusinessTextCollector(api_key)
            result = collector.plan([(row["stock_code"], row["decision_date"]) for row in rows], data_dir)
            if args.command == "fetch":
                result = collector.fetch(result["needed_rcept_nos"], data_dir, args.max_requests, execute=execute)
    except (ValueError, DartAPIError, OSError):
        print(json.dumps({"status": "error", "error": "DART business text configuration, archive or request is invalid"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
