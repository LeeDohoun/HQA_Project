"""Immutable non-H1 versions, published before mutable aggregate archives."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def protected(day):
    return "20260101" <= str(day).replace("-", "")[:8] <= "20260630"


def version_directory(path):
    path = Path(path)
    return path.with_suffix(".versions")


def publish_versions(path, rows, *, date_field):
    """Content addressing makes retries idempotent; no existing version is replaced."""
    directory = version_directory(path)
    for row in rows:
        if protected(row[date_field]):
            continue
        content = (json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()
        checksum = hashlib.sha256(content).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (checksum + ".json")
        fd, temporary = tempfile.mkstemp(prefix=".version-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o444)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or target.stat().st_mode & 0o222 or target.read_bytes() != content:
                    raise ValueError("immutable incremental version differs: " + str(target)) from None
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.unlink(temporary)


def read_versions(path, *, date_field):
    rows = []
    for version in sorted(version_directory(path).glob("*.json")):
        if version.is_symlink() or version.stat().st_mode & 0o222:
            raise ValueError("immutable incremental version hash/mode differs: " + str(version))
        content = version.read_bytes()
        if hashlib.sha256(content).hexdigest() != version.stem:
            raise ValueError("immutable incremental version hash/mode differs: " + str(version))
        row = json.loads(content)
        if not row.get(date_field) or protected(row[date_field]):
            raise ValueError("incremental version contains protected or undated input")
        rows.append(row)
    return rows
