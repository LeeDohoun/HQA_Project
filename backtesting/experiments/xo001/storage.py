"""Exclusive, fsynced records with FW001 private-directory completion proofs."""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from . import KST, Refused, aware, now

PRECISION_NS = 1_000_000_000
MAIN = Path("/home/are01/src/HQA_Project/HQA_Project")
CODE = Path(__file__).resolve().parents[3]


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    path = Path(path)
    if any(part.startswith(".env") for part in path.parts):
        raise Refused("environment files are never read by XO001")
    return path.read_bytes()


def load(path):
    return json.loads(read(path))


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path, mode=0o700):
    path = Path(path)
    if path.absolute().resolve() != path.absolute():
        raise Refused("XO001 write path follows a symlink")
    if not path.exists():
        mkdir(path.parent, mode)
        try:
            path.mkdir(mode=mode)
            fsync_dir(path.parent)
        except FileExistsError:
            pass


def put(path, raw, mode=0o600):
    """Publish once; an identical retry is allowed, replacement never is."""
    path = Path(path)
    if path.absolute().resolve() != path.absolute():
        raise Refused("XO001 write path follows a symlink")
    mkdir(path.parent)
    if path.exists():
        if path.is_symlink() or read(path) != raw:
            raise Refused("immutable XO001 record differs")
        return
    fd, temporary = tempfile.mkstemp(prefix=".xo001-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read(path) != raw:
                raise Refused("concurrent immutable XO001 record differs") from None
        fsync_dir(path.parent)
    finally:
        os.unlink(temporary)


def replace_private(path, value):
    """Only mutable control state/token caches use atomic replacement."""
    path = Path(path)
    if path.absolute().resolve() != path.absolute():
        raise Refused("XO001 write path follows a symlink")
    mkdir(path.parent)
    if path.is_symlink():
        raise Refused("mutable XO001 state is a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path, *, nonblocking=False):
    path = Path(path)
    if path.absolute().resolve() != path.absolute():
        raise Refused("XO001 lock path follows a symlink")
    mkdir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError:
            raise Refused("XO001 owner process already running") from None
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def safe_root(root, inputs=()):
    path = Path(root).absolute()
    target = path.resolve()
    protected = (MAIN, CODE, Path("/opt/hqa"), Path("/opt/hqa-bb003"),
                 Path("/opt/hqa-fw002"), Path("/var/lib/hqa/data"),
                 *(Path(p).resolve() for p in inputs))
    if (path != target or any(p.startswith(".env") for p in path.parts)
            or any(target.is_relative_to(p) or p.is_relative_to(target) for p in protected)):
        raise Refused("XO001 output overlaps code/read-only inputs or follows a symlink")
    return target


def publication_ns(marker):
    return marker.parent.stat().st_mtime_ns


class Store:
    def __init__(self, root, *, clock=now, inputs=()):
        self.root, self.clock = safe_root(root, inputs), clock

    def path(self, relative):
        value = Path(relative)
        path = self.root / value
        if (value.is_absolute() or ".." in value.parts or path.resolve() != path
                or any(part.startswith(".env") for part in value.parts)):
            raise Refused("unsafe XO001 state path")
        return path

    def lock(self):
        return locked(self.path("control/recorder.lock"), nonblocking=True)

    def blob(self, raw):
        digest = sha(raw)
        put(self.path("blobs/" + digest), raw)
        return digest

    def bytes(self, digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise Refused("invalid XO001 blob digest")
        raw = read(self.path("blobs/" + digest))
        if sha(raw) != digest:
            raise Refused("XO001 blob integrity failure")
        return raw

    def marker(self, kind, key):
        if not re.fullmatch(r"[a-z_]+", kind) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", key):
            raise Refused("invalid XO001 record identity")
        return self.path(f"sealed/{kind}/{key}/complete.json")

    def keys(self, kind):
        self.marker(kind, "check")
        return sorted(p.parent.name for p in self.path("sealed/" + kind).glob("*/complete.json"))

    def proof(self, kind, key):
        marker = self.marker(kind, key)
        if not marker.exists():
            return None
        proof = load(marker)
        if (set(p.name for p in marker.parent.iterdir()) != {"complete.json"}
                or proof["publication_clock"] != "private_directory_mtime_ns"
                or proof["publication_precision_ns"] != PRECISION_NS):
            raise Refused("invalid XO001 completion directory")
        ns = publication_ns(marker)
        upper = ns + PRECISION_NS
        if ns < marker.stat().st_mtime_ns or upper < int(aware(proof["prepared_at"]).timestamp() * 1e9):
            raise Refused("inconsistent XO001 kernel publication time")
        raw = read(self.path(proof["record_path"]))
        if sha(raw) != proof["record_sha256"]:
            raise Refused("XO001 record hash differs")
        record = json.loads(raw)
        if (record["record_kind"], record["record_key"]) != (kind, key):
            raise Refused("XO001 record identity differs")
        valid = True
        if proof["deadline"]:
            cutoff = int(aware(proof["deadline"]).timestamp() * 1e9)
            valid = upper <= cutoff if proof["inclusive"] else upper < cutoff
        return {**proof, "valid": valid, "record": record,
                "publication_not_after": datetime.fromtimestamp(upper / 1e9, KST).isoformat()}

    def get(self, kind, key):
        proof = self.proof(kind, key)
        return proof["record"] if proof and proof["valid"] else None

    def seal(self, kind, key, value, *, deadline=None, inclusive=True, fixed_path=None):
        old = self.proof(kind, key)
        if old:
            return old
        stamp = aware(self.clock())
        if deadline and (stamp > aware(deadline) if inclusive else stamp >= aware(deadline)):
            raise Refused("XO001 publication deadline passed")
        record = {**value, "record_kind": kind, "record_key": key, "recorded_at": stamp.isoformat()}
        raw = encoded(record)
        relative = fixed_path or f"records/{kind}/{key}/{sha(raw)}.json"
        put(self.path(relative), raw)
        marker = self.marker(kind, key)
        mkdir(marker.parent)
        proof = {"record_path": relative, "record_sha256": sha(raw), "prepared_at": stamp.isoformat(),
                 "publication_clock": "private_directory_mtime_ns", "publication_precision_ns": PRECISION_NS,
                 "deadline": aware(deadline).isoformat() if deadline else None, "inclusive": inclusive}
        # Staging outside the private completion directory preserves its kernel mtime.
        fd, temporary = tempfile.mkstemp(prefix=".proof-", dir=marker.parent.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded(proof))
                handle.flush()
                os.fsync(handle.fileno())
            if deadline and (aware(self.clock()) > aware(deadline) if inclusive else aware(self.clock()) >= aware(deadline)):
                raise Refused("XO001 publication deadline passed during fsync")
            os.link(temporary, marker)
            fsync_dir(marker.parent)
        finally:
            os.unlink(temporary)
        return self.proof(kind, key)

    def append(self, kind, value):
        """Call under the recorder lock. Each block is independently immutable."""
        keys = self.keys(kind)
        if keys != [f"{i:08d}" for i in range(len(keys))]:
            raise Refused("noncontiguous XO001 journal")
        return self.seal(kind, f"{len(keys):08d}", value)
