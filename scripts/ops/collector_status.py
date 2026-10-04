"""Report local collector files and free space without loading environment secrets."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
LOW_DISK_BYTES = 1_000_000_000


def _read_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError(f"expected state object: {path.name}")
    return state


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"expected JSONL object: {path.name}")
                yield row


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("collector timestamps require a timezone")
    return parsed.astimezone(KST)


def collect_status(data_dir: str | Path, *, clock=None) -> dict:
    data_dir = Path(data_dir)
    now = _timestamp((clock() if clock is not None else datetime.now(KST)).isoformat())
    today = now.date()
    dart_dir = data_dir / "disclosures" / "first_seen"
    dart_state = _read_state(dart_dir / "_state.json")
    completed_date = (datetime.strptime(dart_state["last_completed_date"], "%Y%m%d").date()
                      if dart_state is not None else None)
    dart_files = sorted(dart_dir.glob("*.jsonl"))
    latest_dart = dart_files[-1] if dart_files else None
    first_seen = poll_completed = None
    if latest_dart is not None:
        for row in _rows(latest_dart):
            seen, completed = _timestamp(row["first_seen_at"]), _timestamp(row["poll_completed_at"])
            first_seen = seen if first_seen is None else max(first_seen, seen)
            poll_completed = completed if poll_completed is None else max(poll_completed, completed)
    polled_today = completed_date == today or (poll_completed is not None and poll_completed.date() == today)
    poller_stale = today.weekday() < 5 and now.time() >= time(10) and not polled_today

    krx_dir = data_dir / "market" / "krx_daily"
    krx_state = _read_state(krx_dir / "_state.json")
    empty_dates = sorted(krx_state["empty_dates"]) if krx_state is not None else []
    for day in empty_dates:
        datetime.strptime(day, "%Y%m%d")
    krx_files = sorted(krx_dir.glob("*/*.jsonl"))
    stored_dates = [datetime.strptime(path.stem, "%Y%m%d").date() for path in krx_files]
    newest_krx = max(stored_dates) if stored_dates else None
    if newest_krx is not None and newest_krx >= today:
        raise ValueError("KRX store contains a current or future date")
    expected = today - timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    lag_days = (sum((newest_krx + timedelta(days=offset)).weekday() < 5
                    for offset in range(1, (expected - newest_krx).days + 1))
                if newest_krx is not None else None)
    calendar_unverified = []
    for path in krx_files:
        if any(row["calendar_status"] != "verified" for row in _rows(path)):
            calendar_unverified.append(path.stem)
    disk_free = shutil.disk_usage(data_dir).free
    low_disk = disk_free < LOW_DISK_BYTES
    flags = [name for name, flagged in (
        ("poller_stale", poller_stale), ("krx_store_empty", newest_krx is None),
        ("krx_lag_days", lag_days is not None and lag_days > 1),
        ("empty_dates", bool(empty_dates)), ("calendar_unverified_dates", bool(calendar_unverified)),
        ("low_disk", low_disk)) if flagged]
    return {"generated_at": now.isoformat(), "data_dir": str(data_dir),
            "dart_last_completed_date": completed_date.isoformat() if completed_date is not None else None,
            "dart_latest_file": str(latest_dart.relative_to(data_dir)) if latest_dart is not None else None,
            "newest_first_seen_at": first_seen.isoformat() if first_seen is not None else None,
            "newest_poll_completed_at": poll_completed.isoformat() if poll_completed is not None else None,
            "poller_stale": poller_stale,
            "krx_newest_date": newest_krx.isoformat() if newest_krx is not None else None,
            "krx_expected_date": expected.isoformat(), "krx_lag_days": lag_days,
            "empty_dates": empty_dates, "calendar_unverified_dates": calendar_unverified,
            "disk_free_bytes": disk_free, "low_disk": low_disk, "flags": flags}


def write_status(data_dir: str | Path, status: dict) -> str:
    path = Path(data_dir) / "ops" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(status, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".status.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return text


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write a local-only daily collector status report")
    parser.add_argument("--data-dir")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir or os.getenv("HQA_DATA_DIR", "./data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = Path(__file__).resolve().parents[2] / data_dir
    print(write_status(data_dir, collect_status(data_dir)), end="")


if __name__ == "__main__":
    main()
