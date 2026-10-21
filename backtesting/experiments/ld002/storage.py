"""Durable no-clobber seals and deadline publication evidence."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import os
from pathlib import Path
import tempfile

from backtesting.experiments.lh003.storage import (
    KST, attempts, aware, digest, file_hash, now, read, safe_path, seal, verified,
)
from . import HEAVY_LOCK, Refused


@contextmanager
def lock(path, *, existing=False):
    path = Path(path)
    if not existing:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("rb" if existing else "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if os.fstat(handle.fileno()).st_ino != path.stat().st_ino:
                raise Refused("lock inode changed")
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def heavy_lock(path=HEAVY_LOCK):
    return lock(path, existing=True)


def cutoff(day, hour):
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+09:00")


def publication_time(marker):
    """Private directory mtime dates the atomic final link (one-second bound)."""
    marker = Path(marker)
    if {p.name for p in marker.parent.iterdir()} != {"complete.json"}:
        raise Refused("publication directory was modified")
    stamp = marker.parent.stat().st_mtime_ns
    if stamp < marker.stat().st_mtime_ns:
        raise Refused("publication metadata is inconsistent")
    return datetime.fromtimestamp(stamp / 1e9, KST)


def publish(path, value, *, deadline, clock=now, publication_clock=publication_time):
    """No receipt after a crash means no timely observation; never reseal."""
    path = Path(path)
    seal(path, value)
    marker = path.with_suffix(".publication") / "complete.json"
    if not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".ld002-proof-", dir=marker.parent.parent) as directory:
            temporary = Path(directory) / "receipt.json"
            seal(temporary, {"record_sha256": digest(value), "prepared_at": aware(clock()).isoformat(),
                             "deadline": aware(deadline).isoformat()})
            os.link(temporary, marker)
            fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    return timely(path, publication_clock=publication_clock)


def timely(path, *, publication_clock=publication_time):
    path = Path(path)
    marker = path.with_suffix(".publication") / "complete.json"
    if not path.exists() or not marker.exists():
        return False
    value, proof = verified(path), verified(marker)
    if proof["record_sha256"] != digest(value):
        raise Refused("publication receipt differs from record")
    stamp = aware(publication_clock(marker))
    if stamp + timedelta(seconds=1) < aware(proof["prepared_at"]):
        raise Refused("publication predates preparation")
    return stamp + timedelta(seconds=1) < aware(proof["deadline"])
