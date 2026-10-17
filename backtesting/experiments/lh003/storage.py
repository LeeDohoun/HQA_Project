"""Atomic, hash-checked immutable artifacts and a durable attempt journal."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backtesting.experiments.lh001.inputs import digest
from src.ingestion.dart_listing import file_hash as file_hash

from . import Refused

KST = ZoneInfo("Asia/Seoul")


def now():
    return datetime.now(KST)


def aware(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise Refused("an aware timestamp is required")
    return stamp.astimezone(KST)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def immutable(path, value):
    """Publish once by link, fsync the file and parent; identical retries are safe."""
    path = Path(path)
    content = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".lh003-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != content:
                raise Refused(f"immutable artifact already exists with different content: {path}") from None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.unlink(temporary)
    return value


def seal(path, value):
    return immutable(path, {"sha256": digest(value), "data": value})


def verified(path):
    envelope = read(path)
    if set(envelope) != {"sha256", "data"} or digest(envelope["data"]) != envelope["sha256"]:
        raise Refused(f"sealed artifact hash mismatch: {path}")
    return envelope["data"]


def safe_path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
        raise Refused("archive path escapes its root")
    return path


def attempts(root):
    paths = sorted((Path(root) / "attempts").glob("*.started.json"))
    if [int(p.name.split(".")[0]) for p in paths] != list(range(1, len(paths) + 1)):
        raise Refused("attempt journal is not contiguous; refuse a budget reset")
    if any(int(p.name.split(".")[0]) > len(paths) for p in (Path(root) / "attempts").glob("*.result.json")):
        raise Refused("attempt result lacks a durable reservation; refuse a budget reset")
    return [verified(path) for path in paths]
