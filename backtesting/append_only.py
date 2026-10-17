"""Stable locks and durable replacement for the research append-only files."""
from __future__ import annotations

import fcntl
import io
import os
import shlex
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class TornTailError(ValueError):
    def __init__(self, path: Path, kind: str, text: str, start: int, line: int, reason: str):
        self.offset = len(text[:start].encode("utf-8", errors="surrogateescape"))
        self.line = line
        command = f"python -m backtesting.repair_append_only --kind {kind} {shlex.quote(str(path))}"
        super().__init__(f"{reason} on line {line} of {path}; possible torn tail. "
                         f"Inspect with: {command} (dry-run; add --write to quarantine and truncate)")


@contextmanager
def file_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: replacement changes the target's inode.
    with path.with_name(path.name + ".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def text_buffer(data: bytes, path: Path) -> io.StringIO:
    # Preserve invalid tail bytes for diagnostics and explicit quarantine.
    handle = io.StringIO(data.decode("utf-8", errors="surrogateescape"), newline="")
    handle.name = str(path)
    return handle


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_replace(path: Path, content: bytes) -> None:
    """Caller holds file_lock; failures before replace leave the target intact."""
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if path.exists():
                os.fchmod(handle.fileno(), stat.S_IMODE(path.stat().st_mode))
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked_append(path: Path) -> Iterator[io.StringIO]:
    with file_lock(path):
        original = path.read_bytes() if path.exists() else b""
        with text_buffer(original, path) as handle:
            yield handle
            content = handle.getvalue().encode("utf-8")
        if not content.startswith(original):
            raise ValueError(f"append must preserve existing bytes: {path}")
        atomic_replace(path, content)
