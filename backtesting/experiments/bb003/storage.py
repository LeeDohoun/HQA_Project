"""No-clobber records and FW001-style private-directory completion proofs.

The marker directory is mutated exactly once, by linking complete.json. Its
kernel mtime is the publication time. The entire one-second interval must fit
before the deadline. A rejected marker is retained permanently.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from . import KST, aware, now

PRECISION_NS = 1_000_000_000
MAIN = Path("/home/are01/src/HQA_Project/HQA_Project")


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def publication_ns(marker):
    return marker.parent.stat().st_mtime_ns


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path, *, owner=None):
    path = Path(path)
    if not path.exists():
        mkdir(path.parent, owner=owner)
        path.mkdir(mode=0o750)
        if owner is not None:
            os.chown(path, *owner)
        path.chmod(0o750)
        fsync_dir(path.parent)


def put(path, raw, *, owner=None):
    """Durable exclusive publication; temporary files stay outside marker dirs."""
    path = Path(path)
    mkdir(path.parent, owner=owner)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("immutable BB003 bytes differ: " + str(path))
        return
    fd, temporary = tempfile.mkstemp(prefix=".bb003-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            if owner is not None:
                os.fchown(handle.fileno(), *owner)
            os.fchmod(handle.fileno(), 0o640)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        fsync_dir(path.parent)
    finally:
        os.unlink(temporary)


def safe_output(data_root):
    root = Path(data_root).absolute()
    target = (root / "forward/bb003").resolve()
    if (target.is_relative_to(MAIN) or MAIN.is_relative_to(target)
            or any(p.startswith(".env") for p in target.parts)):
        raise ValueError("BB003 output overlaps the main checkout or an environment file")
    if target != root.resolve() / "forward/bb003":
        raise ValueError("BB003 state must not redirect through a symlink")
    code = Path(__file__).resolve().parents[3]
    protected = (code, Path("/opt/hqa"), Path("/opt/hqa-fw002"), Path("/opt/hqa-bb003"),
        Path("/var/lib/hqa/data/shadow"), *(Path("/var/lib/hqa/data/forward") / name
        for name in ("fw001", "fw002", "shadow", "shadow_paper")))
    if any(target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target) for p in protected):
        raise ValueError("BB003 output overlaps code, read-only inputs or another experiment")
    return target


class Store:
    def __init__(self, data_root, *, clock=now, measurement=False):
        self.root = safe_output(data_root)
        self.clock, self.measurement = clock, measurement

    def path(self, relative):
        path = self.root / relative
        if (Path(relative).is_absolute() or not path.resolve().is_relative_to(self.root)
                or any(part.startswith(".env") for part in path.parts)):
            raise ValueError("unsafe BB003 relative path")
        return path

    @contextmanager
    def lock(self):
        mkdir(self.root)
        with self.path(".record.lock").open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def blob(self, raw):
        checksum = sha(raw)
        put(self.path("inputs/" + checksum), raw)
        return checksum

    def bytes(self, checksum):
        if not re.fullmatch("[0-9a-f]{64}", checksum):
            raise ValueError("invalid BB003 input hash")
        raw = self.path("inputs/" + checksum).read_bytes()
        if sha(raw) != checksum:
            raise ValueError("BB003 input snapshot hash differs")
        return raw

    def marker(self, kind, key):
        if not re.fullmatch("[a-z_]+", kind) or not re.fullmatch("[a-zA-Z0-9_.-]+", key):
            raise ValueError("invalid BB003 record identity")
        return self.path(f"sealed/{kind}/{key}/complete.json")

    def proof(self, kind, key):
        marker = self.marker(kind, key)
        if not marker.exists():
            return None
        proof = load(marker)
        if ({p.name for p in marker.parent.iterdir()} != {"complete.json"}
                or proof["publication_clock"] != "private_directory_mtime_ns"
                or proof["publication_precision_ns"] != PRECISION_NS
                or proof["measurement"] != self.measurement):
            raise ValueError("invalid BB003 private marker directory or mode")
        published = publication_ns(marker)
        prepared = int(aware(proof["prepared_at"]).timestamp() * 1e9)
        if published < marker.stat().st_mtime_ns or published + PRECISION_NS < prepared:
            raise ValueError("inconsistent BB003 kernel publication time")
        raw = self.path(proof["record_path"]).read_bytes()
        if sha(raw) != proof["record_sha256"]:
            raise ValueError("BB003 record hash differs from completion proof")
        record = json.loads(raw)
        if record.get("record_kind") != kind or record.get("record_key") != key:
            raise ValueError("BB003 completion proof record identity differs")
        if record["label"] != ("reconstructed" if self.measurement else "forward"):
            raise ValueError("BB003 record label differs from store mode")
        latest = published + PRECISION_NS
        if not self.measurement and latest < int(aware(record["recorded_at"]).timestamp() * 1e9):
            raise ValueError("BB003 completion precedes its record")
        valid = True
        if proof["deadline"] is not None:
            cutoff = int(aware(proof["deadline"]).timestamp() * 1e9)
            valid = latest <= cutoff if proof["inclusive"] else latest < cutoff
        return {**proof, "sealed_at": datetime.fromtimestamp(published / 1e9, KST).isoformat(),
                "publication_not_after": datetime.fromtimestamp(latest / 1e9, KST).isoformat(),
                "valid": valid, "record": record}

    def get(self, kind, key):
        proof = self.proof(kind, key)
        return proof["record"] if proof and proof["valid"] else None

    def keys(self, kind):
        return sorted(p.parent.name for p in self.path("sealed/" + kind).glob("*/complete.json"))

    def seal(self, kind, key, record, *, deadline=None, inclusive=False, fixed_path=None, recorded_at=None):
        """Caller holds the recording lock; retries cannot replace any marker.

        Orphaned candidates may be recalculated before cutoff. Every candidate
        is itself immutable; only one completion marker can ever be published.
        Measurement keeps the historical wall clock but uses a separately
        labelled real-kernel publication deadline, never a forward observation.
        """
        from .deployment import verify_operation
        mode = getattr(self, "_verification_mode", "record")
        verification = verify_operation(self, mode=mode)
        existing = self.proof(kind, key)
        if existing:
            return existing
        stamp = aware(self.clock())
        if deadline is not None and (stamp > deadline if inclusive else stamp >= deadline):
            return None
        for checksum in record.get("input_hashes", {}).values():
            self.bytes(checksum)
        digest = verification["manifest_sha256"]
        if "manifest_sha256" in record and record["manifest_sha256"] != digest:
            raise ValueError("BB003 record manifest differs from verified implementation")
        value = {**record, "manifest_sha256": digest, "label": "reconstructed" if self.measurement else "forward",
                 "recorded_at": recorded_at or stamp.isoformat(), "record_kind": kind, "record_key": key}
        raw = encoded(value)
        relative = fixed_path or f"records/{kind}/{key}/{sha(raw)}.json"
        path = self.path(relative)
        put(path, raw)
        marker = self.marker(kind, key)
        mkdir(marker.parent)
        prepared = now() if self.measurement else aware(self.clock())
        effective = now() + timedelta(hours=1) if self.measurement and deadline else deadline
        proof = {"record_path": relative, "record_sha256": sha(raw), "prepared_at": prepared.isoformat(),
                 "publication_clock": "private_directory_mtime_ns", "publication_precision_ns": PRECISION_NS,
                 "deadline": effective.isoformat() if effective else None, "inclusive": inclusive,
                 "measurement": self.measurement,
                 "historical_deadline": deadline.isoformat() if self.measurement and deadline else None}
        fd, temporary = tempfile.mkstemp(prefix=".proof-", dir=marker.parent.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o640)
                handle.write(encoded(proof))
                handle.flush()
                os.fsync(handle.fileno())
            if verify_operation(self, mode=mode) != verification:
                raise ValueError("BB003 implementation/environment changed before sealing")
            if kind == "decisions" and getattr(self, "morning_guard", None):
                if self.morning_guard() != self.admitted_plan:
                    raise ValueError("BB003 cheap admission plan differs from the verified official plan")
            elif getattr(self, "priority_guard", None):
                self.priority_guard()
            stamp = aware(self.clock())
            if deadline is not None and (stamp > deadline if inclusive else stamp >= deadline):
                return None
            os.link(temporary, marker)
            fsync_dir(marker.parent)
        finally:
            os.unlink(temporary)
        return self.proof(kind, key)
