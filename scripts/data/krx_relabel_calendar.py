"""Relabel stored KRX calendar metadata offline; dry run unless --execute is given."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_project_root
from src.ingestion.krx_market import _daily_metadata
from src.ingestion.storage import atomic_write, file_lock


def _replace_metadata(line: str, updates: dict) -> str:
    """Replace only top-level metadata tokens, preserving all other bytes."""
    decoder, whitespace = json.JSONDecoder(), re.compile(r"\s*")
    edits, present = [], set()
    position = whitespace.match(line, line.index("{") + 1).end()
    while line[position] != "}":
        key, end = decoder.raw_decode(line, position)
        start = whitespace.match(line, whitespace.match(line, end).end() + 1).end()
        _, end = decoder.raw_decode(line, start)
        if key in updates:
            edits.append((start, end, json.dumps(updates[key], ensure_ascii=False)))
            present.add(key)
        position = whitespace.match(line, end).end()
        if line[position] == "}":
            break
        position = whitespace.match(line, position + 1).end()
    missing = {key: value for key, value in updates.items() if key not in present}
    if missing:
        edits.append((position, position, ", " + json.dumps(missing, ensure_ascii=False)[1:-1]))
    for start, end, replacement in reversed(edits):
        line = line[:start] + replacement + line[end:]
    return line


def _relabel_file(path: Path, *, execute: bool) -> tuple[int, int]:
    # Use save_day's lock; dry runs must not even create a lock file.
    with file_lock(path.with_suffix(path.suffix + ".lock")) if execute else nullcontext():
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
        changed, unverified = 0, 0
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"expected JSON object:{path}:{index + 1}")
            if row.get("calendar_status") == "verified":
                continue
            day = row["trade_date"].replace("-", "")
            if day != path.stem or day[:4] != path.parent.name:
                raise ValueError(f"KRX row trade date does not match file:{path}:{index + 1}")
            metadata = _daily_metadata({"BAS_DD": row["trade_date"], "_collected_at": row["collected_at"]})
            if metadata["calendar_status"] != "verified":
                unverified += 1
                continue
            updates = {key: metadata[key] for key in ("calendar_status", "bar_at", "available_at")}
            lines[index] = _replace_metadata(line, updates)
            changed += 1
        if execute and changed:
            mode = stat.S_IMODE(path.stat().st_mode)
            atomic_write(path, "".join(lines))
            path.chmod(mode)
        return changed, unverified


def relabel_calendar(data_dir: str | Path, *, execute: bool = False) -> list[dict]:
    data_dir = Path(data_dir).expanduser()
    backup = "HQA_data_backup_20260913"
    if backup in data_dir.parts or backup in data_dir.resolve().parts:
        raise ValueError(f"Refusing to relabel backup directory:{backup}")
    root = data_dir / "market" / "krx_daily"
    if not root.is_dir():
        raise FileNotFoundError(f"KRX daily directory does not exist:{root}")
    summaries = {}
    pattern = "[0-9]" * 4 + "/" + "[0-9]" * 8 + ".jsonl"
    for path in sorted(root.glob(pattern)):
        year = path.parent.name
        summary = summaries.setdefault(year, {"year": int(year), "files": 0,
                                               "rows_changed": 0, "rows_still_unverified": 0})
        changed, unverified = _relabel_file(path, execute=execute)
        summary["files"] += 1
        summary["rows_changed"] += changed
        summary["rows_still_unverified"] += unverified
    return list(summaries.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.getenv("HQA_DATA_DIR", "./data"))
    parser.add_argument("--execute", action="store_true", help="Rewrite verified calendar metadata in place")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser()
    if not data_dir.is_absolute():
        data_dir = get_project_root() / data_dir
    for summary in relabel_calendar(data_dir, execute=args.execute):
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
