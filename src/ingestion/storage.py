"""Locked observation archives and atomic publication of derived data."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise ValueError(f"invalid JSONL:{path.name}:{number}") from None
                if not isinstance(row, dict):
                    raise ValueError(f"expected JSON object:{path.name}:{number}")
                rows.append(row)
    return rows


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_rows(path: Path, rows: list[dict]) -> int:
    atomic_write(path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows))
    return len(rows)


def save_episodes(path: Path, rows: list[dict], identity, revision) -> int:
    """Only consecutive identical content is redundant; A -> B -> A is history."""
    if not rows:
        return 0
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        stored = read_rows(path)
        latest = {identity(row): revision(row) for row in stored}
        added = []
        for row in rows:
            key, version = identity(row), revision(row)
            if latest.get(key) == version:
                continue
            row = {**row, "metadata": {**(row.get("metadata") or {}), "version_id": uuid.uuid4().hex}}
            latest[key] = version
            added.append(row)
        if added:
            write_rows(path, stored + added)
        return len(added)
