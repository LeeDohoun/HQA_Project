"""Single-claim protected snapshot and an overlay that never rereads its sources.

Mixed financial/index JSONL archives are frozen in full. Subsequent reads use
that prefix from the snapshot, and seek directly to newly appended source bytes.
Destructive archive rewrites fail closed; the collector must preserve episodes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from backtesting import holdout

from . import EXPERIMENT_ID, Refused
from .storage import aware, file_hash, safe_path, seal, verified

FIRST, LAST = "20260101", "20260630"


def protected(day):
    return FIRST <= str(day).replace("-", "")[:8] <= LAST


def inventory(data_dir):
    root = Path(data_dir)
    paths = []
    for folder in ("market/krx_daily", "disclosures/dart_full/list", "disclosures/dart_full/list_supplement"):
        paths += [p for p in (root / folder).glob("*/*.jsonl") if protected(p.stem)]
    streams = sorted((root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    benchmark = root / "market_context/benchmarks.jsonl"
    if not streams or not benchmark.is_file():
        raise Refused("financial versions and market indices are required before the holdout claim")
    paths += [*streams, benchmark]
    return sorted(set(paths)), {p.relative_to(root).as_posix() for p in (*streams, benchmark)}


def publish_snapshot(data_dir, destination, *, at, synthetic=False):
    """Called only inside the production session, or on explicit synthetic fixtures."""
    source, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if destination.exists():
        raise Refused("snapshot destination already exists; never reseal")
    if synthetic and "data" in destination.parts:
        raise Refused("synthetic snapshots must never be written under data/")
    if not synthetic:
        sessions = [s for s in holdout._SESSIONS.get() if s._open and s.experiment_id == EXPERIMENT_ID
                    and s._pid == os.getpid()]
        if not sessions:
            raise Refused("protected snapshot acquisition requires the single LH003 session")
    paths, streams = inventory(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".lh003-seal-", dir=destination.parent))
    files, stream_info = {}, {}
    try:
        for path in paths:
            relative = path.relative_to(source).as_posix()
            target = safe_path(temporary, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            before = file_hash(path)
            stat = path.stat()
            shutil.copyfile(path, target)
            if file_hash(path) != before or file_hash(target) != before:
                raise Refused("source changed while sealing: " + relative)
            with target.open("rb") as handle:
                os.fsync(handle.fileno())
            target.chmod(0o444)
            files[relative] = before
            if relative in streams:
                with target.open("rb") as handle:
                    handle.seek(max(0, stat.st_size - 1))
                    if stat.st_size and handle.read() != b"\n":
                        raise Refused("stream archive must end with a newline: " + relative)
                stream_info[relative] = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        if [p.relative_to(source).as_posix() for p in inventory(source)[0]] != list(files):
            raise Refused("protected input inventory changed while sealing")
        manifest = {"experiment_id": EXPERIMENT_ID, "synthetic": synthetic, "created_at": aware(at).isoformat(),
                    "files": files, "streams": stream_info}
        seal(temporary / "manifest.json", manifest)
        # Read-only contents; parent publication is atomic and never replaces a seal.
        for folder in sorted((p for p in temporary.rglob("*") if p.is_dir()), reverse=True):
            folder.chmod(0o555)
        temporary.chmod(0o555)
        os.rename(temporary, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return manifest
    except BaseException:
        if temporary.exists():
            for folder in [temporary, *(p for p in temporary.rglob("*") if p.is_dir())]:
                folder.chmod(0o755)
            shutil.rmtree(temporary)
        raise


class Snapshot:
    def __init__(self, root, *, expected_hash, allow_synthetic=False):
        from .storage import digest

        self.root = Path(root).resolve()
        self.manifest = verified(self.root / "manifest.json")
        if self.manifest["experiment_id"] != EXPERIMENT_ID or digest(self.manifest) != expected_hash:
            raise Refused("holdout snapshot identity/hash differs from the start record")
        if self.manifest["synthetic"] and not allow_synthetic:
            raise Refused("synthetic snapshot cannot be used for experiment observations")
        for relative, checksum in self.manifest["files"].items():
            path = safe_path(self.root, relative)
            if path.is_symlink() or not path.is_file() or file_hash(path) != checksum or path.stat().st_mode & 0o222:
                raise Refused("snapshot is changed, writable or missing: " + relative)

    def path(self, relative):
        if relative not in self.manifest["files"]:
            raise Refused("protected input missing from snapshot: " + relative)
        path = safe_path(self.root, relative)
        if file_hash(path) != self.manifest["files"][relative]:
            raise Refused("snapshot hash mismatch: " + relative)
        return path

    def stream(self, data_dir, relative):
        """Protected prefixes are read exclusively from the verified snapshot."""
        metadata = self.manifest["streams"].get(relative)
        source = safe_path(data_dir, relative)
        if metadata is None:
            # New business-year files with no possible H1 publication. Earlier
            # new files need an operator export, rather than a protected reread.
            match = re.fullmatch(r"fundamentals/dart_quarterly/(\d{4})_(110\d{2})\.jsonl", relative)
            if not match or int(match[1]) < 2026 or int(match[1]) == 2026 and match[2] not in {"11011", "11014"}:
                raise Refused("new mixed archive could contain unsealed protected versions: " + relative)
            return source.read_bytes()
        baseline = self.path(relative).read_bytes()
        stat = source.stat()
        if stat.st_size < metadata["bytes"]:
            raise Refused("mixed archive was truncated; preserve its sealed prefix: " + relative)
        if stat.st_size == metadata["bytes"]:
            if stat.st_mtime_ns != metadata["mtime_ns"]:
                raise Refused("mixed archive rewritten without an appended version: " + relative)
            return baseline
        with source.open("rb") as handle:
            handle.seek(metadata["bytes"])
            tail = handle.read()
        if not tail.endswith(b"\n"):
            raise Refused("mixed archive has an incomplete appended episode: " + relative)
        for line in tail.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            day = row.get("trade_date", row.get("available_date"))
            if day is None or protected(day):
                raise Refused("new tail contains a protected or undated version; snapshot cannot be reopened")
        after = source.stat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if tuple(getattr(after, field) for field in fields) != tuple(getattr(stat, field) for field in fields):
            raise Refused("mixed archive changed during suffix acquisition")
        return baseline + tail


@contextmanager
def input_view(data_dir, snapshot, *, end):
    """A disposable filesystem view for the established merged-listing reader."""
    source = Path(data_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="lh003-input-view-") as directory:
        view = Path(directory)
        for folder in ("market/krx_daily", "disclosures/dart_full/list", "disclosures/dart_full/list_supplement"):
            relatives = {p.relative_to(source).as_posix() for p in (source / folder).glob("*/*.jsonl")
                         if p.stem.isdigit() and p.stem <= str(end).replace("-", "")}
            relatives |= {p for p in snapshot.manifest["files"] if p.startswith(folder + "/")}
            for relative in sorted(relatives):
                original = safe_path(source, relative)
                day = Path(relative).stem
                target = snapshot.path(relative) if protected(day) else original
                link = view / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)
        for relative in ("reference/corp_codes.csv", "reference/dart_company/companies.jsonl",
                         "disclosures/dart_full/_state.json", "disclosures/dart_full/list_supplement/_state.json"):
            path = safe_path(source, relative)
            if path.is_file():
                link = view / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(path)
        streams = set(snapshot.manifest["streams"]) | {
            p.relative_to(source).as_posix() for p in (source / "fundamentals/dart_quarterly").glob("*_110*.jsonl")
            if int(p.name[:4]) <= pd.Timestamp(end).year}
        for relative in sorted(streams):
            path = view / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(snapshot.stream(source, relative))
        yield view
