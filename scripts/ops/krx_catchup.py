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


def _unresolved_empty_dates(data_dir: str | Path) -> list[date]:
    directory = Path(data_dir) / "market" / "krx_daily"
    return sorted(datetime.strptime(day, "%Y%m%d").date()
                  for day in krx_market._empty_dates(data_dir)
                  if not (directory / day[:4] / f"{day}.jsonl").exists())


def catchup_range(data_dir: str | Path, today: date) -> tuple[str, str]:
    """Include the last saved date and unresolved empty weekdays up to 30 days old."""
    directory = Path(data_dir) / "market" / "krx_daily"
    stored = [datetime.strptime(path.stem, "%Y%m%d").date()
              for path in directory.glob("*/*.jsonl")]
    if stored and max(stored) >= today:
        raise ValueError("KRX store contains a current or future date")
    start = max(stored) if stored else today - timedelta(days=14)
    recent_empty = [day for day in _unresolved_empty_dates(data_dir)
                    if today - timedelta(days=30) <= day < today and day.weekday() < 5]
    start = min([start, *recent_empty])
    return start.strftime("%Y%m%d"), (today - timedelta(days=1)).strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Catch up full-market KRX daily data through yesterday KST")
    parser.add_argument("--data-dir")
    parser.add_argument("--execute", action="store_true", help="Make KRX requests and save observations")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir or os.getenv("HQA_DATA_DIR", "./data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = get_project_root() / data_dir
    today = datetime.now(KST).date()
    start, end = catchup_range(data_dir, today)
    stale_empty_dates = [day for day in _unresolved_empty_dates(data_dir) if day < today - timedelta(days=30)]
    # An old latest file can put stale empty dates inside the ordinary catch-up range.
    ranges = []
    next_start = datetime.strptime(start, "%Y%m%d").date()
    for day in stale_empty_dates:
        if next_start <= day:
            if next_start < day:
                ranges.append((f"{next_start:%Y%m%d}", f"{day - timedelta(days=1):%Y%m%d}"))
            next_start = day + timedelta(days=1)
    ranges.append((f"{next_start:%Y%m%d}", end))
    summary = krx_market.backfill(*ranges[0], data_dir, execute=args.execute)
    for from_date, to_date in ranges[1:]:
        part = krx_market.backfill(from_date, to_date, data_dir, execute=args.execute)
        for key, value in part.items():
            summary[key] += value
    print(json.dumps({"dry_run": not args.execute, "data_dir": str(data_dir),
                     "from_date": start, "to_date": end,
                     "stale_empty_dates_not_retried": [f"{day:%Y%m%d}" for day in stale_empty_dates],
                     **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
