"""Single-claim protected snapshot and an overlay that never rereads its sources.

Mixed financial/index archives are acquired once. Weekly reads combine only
that sealed baseline and a pinned completed non-H1 distribution.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from backtesting import holdout

from . import EXPERIMENT_ID, Refused
from .distribution import COVERAGE, DAILY, REFERENCES, protected
from .storage import aware, file_hash, safe_path, seal, verified

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
        from scripts.lh003.distribute import financial_dates
        from src.ingestion import dart_listing
        from src.ingestion.storage import read_rows
        dates = financial_dates(source, paths)
        receipts = sorted({row["rcept_no"] for path in paths if path.parent.name == "dart_quarterly"
                           for row in read_rows(path)
                           if dates.annotate(row)["actual_public_date"] is not None
                           and protected(dates.annotate(row)["actual_public_date"])})
        status = dart_listing.listing_status(source)
        days = [d for d in status["main_completed_days"] if protected(d)]
        coverage = dart_listing.covered_listing_days(source, days, status=status)
        manifest = {"experiment_id": EXPERIMENT_ID, "synthetic": synthetic, "created_at": aware(at).isoformat(),
                    "files": files, "streams": stream_info, "protected_financial_receipts": receipts,
                    "covered_listing_days": coverage}
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
    def __init__(self, root, *, expected_hash, allow_synthetic=False, distribution=None):
        from .storage import digest

        self.distribution = distribution
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

    def stream(self, relative):
        """Merge captured protected rows with the entire pinned non-H1 archive."""
        from src.ingestion.dart_filing_dates import known_date

        if self.distribution is None:
            raise Refused("a completed non-H1 distribution must be pinned before input reads")
        self.distribution.verify_code()
        baseline = self.path(relative).read_bytes() if relative in self.manifest["files"] else b""
        rows = [json.loads(line) for line in baseline.splitlines() if line.strip()]
        if relative.startswith("fundamentals/"):
            receipts = set(self.manifest["protected_financial_receipts"])
            rows = [r for r in rows if r["rcept_no"] in receipts]
        elif relative.startswith("disclosures/"):
            rows = [r for r in rows if protected(known_date(r["rcept_no"], r["rcept_dt"]))]
        else:
            rows = [r for r in rows if protected(r["trade_date"])]
        if relative in self.distribution.manifest["files"]:
            with self.distribution.path(relative).open(encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
        return b"".join((json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode() for row in rows)


@contextmanager
def input_view(data_dir, snapshot, *, end):
    """The raw data_dir is deliberately never inspected, even for inventory."""
    from .storage import digest, read

    publication = snapshot.distribution
    if publication is None:
        raise Refused("a completed non-H1 distribution must be pinned before input generation")
    publication.verify_code()
    with tempfile.TemporaryDirectory(prefix="lh003-input-view-") as directory:
        view = Path(directory)
        names = set(snapshot.manifest["files"]) | set(publication.manifest["files"])
        for relative in sorted(names):
            path = Path(relative)
            if any(relative.startswith(folder + "/") for folder in DAILY):
                if path.stem > str(end).replace("-", ""):
                    continue
                target = view / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative.startswith("market/krx_daily/"):
                    sealed = snapshot.path(relative) if protected(path.stem) else publication.path(relative)
                    target.symlink_to(sealed)
                else:
                    target.write_bytes(snapshot.stream(relative))
            elif relative.startswith("fundamentals/") or relative == "market_context/benchmarks.jsonl":
                target = view / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(snapshot.stream(relative))
        for relative in REFERENCES:
            target = view / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(publication.path(relative))
        coverage = sorted(set(snapshot.manifest["covered_listing_days"]) |
                          set(read(publication.path(COVERAGE))["covered_listing_days"]))
        (view / COVERAGE).write_text(json.dumps({"covered_listing_days": coverage,
            "listing_version": {"baseline_sha256": digest(snapshot.manifest),
                                "distribution_sha256": digest(publication.manifest)}}))
        yield view
