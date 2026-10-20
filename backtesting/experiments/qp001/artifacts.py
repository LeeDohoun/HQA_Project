"""Exclusive, atomic and durable QP001 artifacts without whole-result strings."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from backtesting.append_only import fsync_directory


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


@contextmanager
def atomic_output(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        # link publishes a complete inode atomically and refuses replacement.
        os.link(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, payload):
    with atomic_output(path) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
        handle.write("\n")
