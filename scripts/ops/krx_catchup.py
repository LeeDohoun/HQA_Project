"""Plan missing KRX weekdays through yesterday; API calls require --execute."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root
from src.ingestion import krx_market

KST = ZoneInfo("Asia/Seoul")


def catchup_range(data_dir: str | Path, today: date) -> tuple[str, str]:
    """Include the last saved date; backfill skips existing files and weekends."""
    directory = Path(data_dir) / "market" / "krx_daily"
    stored = [datetime.strptime(path.stem, "%Y%m%d").date()
              for path in directory.glob("*/*.jsonl")]
    if stored and max(stored) >= today:
        raise ValueError("KRX store contains a current or future date")
    start = max(stored) if stored else today - timedelta(days=14)
    return start.strftime("%Y%m%d"), (today - timedelta(days=1)).strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Catch up full-market KRX daily data through yesterday KST")
    parser.add_argument("--data-dir")
    parser.add_argument("--execute", action="store_true", help="Make KRX requests and save observations")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir or os.getenv("HQA_DATA_DIR", "./data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = get_project_root() / data_dir
    start, end = catchup_range(data_dir, datetime.now(KST).date())
    summary = krx_market.backfill(start, end, data_dir, execute=args.execute)
    print(json.dumps({"dry_run": not args.execute, "data_dir": str(data_dir),
                     "from_date": start, "to_date": end, **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
