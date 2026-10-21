"""Single H1 claim and read-only distributions; daily reads never see raw roots."""
import json
import os
from pathlib import Path
import shutil
import tempfile

from backtesting import experiment_registry, holdout
from backtesting.experiments import hc003, ordering
from backtesting.experiments.lh003.distribution import COVERAGE, DAILY, REFERENCES, protected
from backtesting.experiments.lh003.snapshot import Snapshot as SharedSnapshot, inventory as h1_inventory
from src.ingestion import dart_listing, dart_quarterly
from src.ingestion.dart_filing_dates import known_date
from src.forward import fw001
from scripts.lh003.distribute import inventory, financial_dates

from . import ACTIVATE_BY, EXPERIMENT_ID, Refused
from .storage import aware, digest, file_hash, lock, now, safe_path, seal, verified
from .runner import implementation_hashes

CODE_REFERENCE = "research/experiments/LD002_llm_delegated_virtual/distribution_code_reference.json"
# Capture before any archive access. Restoring source files cannot certify code
# already imported by this distributor; launch a fresh process after updates.
_LOADED_HASHES = implementation_hashes()


def code_pins(state_root, repo_root, *, at):
    from backtesting.experiments.lh003 import pins
    start = Path(state_root) / "start.json" if state_root is not None else None
    active = start is not None and start.exists() and aware(at) >= aware(verified(start)["at"])
    return pins.verify(state_root if active else None, repo_root, at=at,
        current=implementation_hashes(repo_root),
        reference=None if active else verified(Path(repo_root) / CODE_REFERENCE))


def verify_distribution_code(state_root, repo_root, *, at, code_hashes):
    approved = code_pins(state_root, repo_root, at=at)["implementation_hashes"]
    if approved != code_hashes or approved != _LOADED_HASHES:
        raise Refused("distribution pinned implementation differs from process-loaded code; launch a fresh process")


def canonical_repo(repo_root):
    root = Path(repo_root).resolve()
    if (root / ".git").exists():
        return (root / experiment_registry._git(root, "rev-parse", "--git-common-dir")).resolve().parent
    return root


def rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Refused("archive row must be an object")
                yield value


def _readonly(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)


def _publish_directory(temporary, destination):
    _readonly(temporary)
    os.rename(temporary, destination)
    destination.chmod(0o555)
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _discard(path):
    if path.exists():
        for folder in [path, *(p for p in path.rglob("*") if p.is_dir())]:
            folder.chmod(0o755)
        shutil.rmtree(path)


def capture_h1(source, destination, *, at, synthetic=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise Refused("never replace an H1 seal")
    if not synthetic and not any(s._open and s.experiment_id == EXPERIMENT_ID and s._pid == os.getpid()
                                for s in holdout._SESSIONS.get()):
        raise Refused("H1 acquisition requires LD002's one HoldoutSession")
    if synthetic and "data" in destination.parts:
        raise Refused("fixtures must be outside data/")
    if synthetic and verified(source / "SYNTHETIC.json")["synthetic"] is not True:
        raise Refused("synthetic acquisition requires explicitly labelled fixtures")
    paths, streams = h1_inventory(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".ld002-h1-", dir=destination.parent))
    try:
        files = {}
        for path in paths:
            relative = path.relative_to(source).as_posix()
            target = safe_path(temporary, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            checksum = file_hash(path)
            shutil.copyfile(path, target)
            if file_hash(path) != checksum or file_hash(target) != checksum:
                raise Refused("H1 source changed during acquisition")
            files[relative] = checksum
        from scripts.lh003.distribute import financial_dates
        dates = financial_dates(source, paths)
        receipts = sorted({r["rcept_no"] for p in paths if p.parent.name == "dart_quarterly" for r in rows(p)
            if dates.annotate(r)["actual_public_date"] is not None and protected(dates.annotate(r)["actual_public_date"])})
        status = dart_listing.listing_status(source)
        coverage = dart_listing.covered_listing_days(source, [d for d in status["main_completed_days"] if protected(d)], status=status)
        if paths != h1_inventory(source)[0] or any(file_hash(source / p) != h for p, h in files.items()):
            raise Refused("H1 inventory changed during acquisition")
        manifest = {"experiment_id": EXPERIMENT_ID, "synthetic": synthetic, "created_at": aware(at).isoformat(),
                    "files": files, "protected_financial_receipts": receipts, "covered_listing_days": coverage,
                    "streams": sorted(streams)}
        seal(temporary / "manifest.json", manifest)
        _publish_directory(temporary, destination)
        return manifest
    finally:
        _discard(temporary)


class Snapshot(SharedSnapshot):
    """Reuse LH003's path/stream reader and its snapshot-only input_view."""
    def __init__(self, root, *, expected_hash, allow_synthetic=False, distribution=None):
        self.root, self.distribution = Path(root).resolve(), distribution
        self.manifest = verified(self.root / "manifest.json")
        if self.manifest["experiment_id"] != EXPERIMENT_ID or digest(self.manifest) != expected_hash:
            raise Refused("H1 snapshot identity/hash differs")
        if self.manifest["synthetic"] and not allow_synthetic:
            raise Refused("synthetic H1 snapshot cannot produce observations")
        for relative in self.manifest["files"]:
            self.path(relative)

    def path(self, relative):
        path = super().path(relative)
        if path.is_symlink() or path.stat().st_mode & 0o222:
            raise Refused("H1 snapshot must be read-only regular files")
        return path


def seal_holdout(source, root, *, repo_root, clock=now, session_factory=None, acquire=capture_h1, synthetic=False):
    root, canonical = Path(root), canonical_repo(repo_root)
    with ordering.execution_lock(canonical), lock(root / ".state.lock"):
        if any((root / name).exists() for name in ("holdout_claim.json", "holdout_failed.json", "holdout.json", "not_started.json")):
            raise Refused("H1 seal already attempted; never reopen the session")
        if aware(clock()) > aware(ACTIVATE_BY):
            seal(root / "not_started.json", {"status": "not_started", "at": aware(clock()).isoformat(), "reason": "activation deadline"})
            raise Refused("activation deadline missed")
        releases = ordering.ld002_releases(hc003.registry_rows(canonical), canonical)
        if not synthetic:
            from src.runner.trading_calendar import preflight_price_sessions
            preflight_price_sessions(source, "2026-01-01", "2026-06-30")
        elif session_factory is None:
            raise Refused("synthetic claims require a fake session")
        seal(root / "holdout_claim.json", {"at": aware(clock()).isoformat(), "releases": releases})
        factory = holdout.HoldoutSession if session_factory is None else session_factory
        try:
            with factory(EXPERIMENT_ID, repo_root=canonical):
                manifest = acquire(source, root / "holdout_snapshot", at=clock(), synthetic=synthetic)
                if not synthetic:
                    from .calendar import Calendar
                    expected = Calendar().sessions("2026-01-01", "2026-06-30")
                    snapshot = Snapshot(root / "holdout_snapshot", expected_hash=digest(manifest))
                    for day in expected:
                        relative = f"market/krx_daily/2026/{day.replace('-', '')}.jsonl"
                        observations = list(rows(snapshot.path(relative)))
                        if (not {"KOSPI", "KOSDAQ"}.issubset({r["market"] for r in observations})
                                or any(r["trade_date"] != day or r["calendar_status"] != "verified" for r in observations)):
                            raise Refused("H1 snapshot requires both verified markets for every session")
                    indices = list(rows(snapshot.path("market_context/benchmarks.jsonl")))
                    for series in ("KOSPI", "KOSDAQ"):
                        if set(expected) - {r["trade_date"] for r in indices if r["series"] == series
                                and r["index_name"] == ("코스피" if series == "KOSPI" else "코스닥")}:
                            raise Refused("H1 snapshot index coverage is incomplete")
            if aware(clock()) > aware(ACTIVATE_BY):
                raise Refused("H1 sealing finished after activation deadline")
            result = {"snapshot_path": str((root / "holdout_snapshot").resolve()), "sha256": digest(manifest),
                      "synthetic": synthetic, "at": aware(clock()).isoformat(), "releases": releases}
            Snapshot(result["snapshot_path"], expected_hash=result["sha256"], allow_synthetic=synthetic)
            seal(root / "holdout.json", result)
            return result
        except BaseException as exc:
            seal(root / "holdout_failed.json", {"status": "not_started", "at": aware(clock()).isoformat(), "reason": str(exc)})
            raise


def distribute(source, directory, sync_path, *, code_hashes, clock=now, synthetic=False,
               state_root=None, repo_root=experiment_registry.PROJECT_ROOT):
    """Explicit publication after a transfer. Never called by daily input reads."""
    source, directory = Path(source).resolve(), Path(directory).resolve()
    if not synthetic:
        verify_distribution_code(state_root, repo_root, at=clock(), code_hashes=code_hashes)
    if synthetic and verified(source / "SYNTHETIC.json")["synthetic"] is not True:
        raise Refused("synthetic distribution requires labelled fixtures")
    if directory.is_relative_to(source):
        raise Refused("distribution must be outside the raw archive")
    with lock(directory / ".publish.lock"):
        number = len(list((directory / "journal").glob("*.started.json")))
        started = {"at": aware(clock()).isoformat(), "sync_sha256": file_hash(sync_path)}
        seal(directory / "journal" / f"{number:06d}.started.json", started)
        sync = json.loads(Path(sync_path).read_text())
        if aware(sync["verified_at"]) > aware(clock()) or not sync["transfer_route"]:
            raise Refused("invalid transfer verification time/route")
        paths = inventory(source)
        hashes = {p.relative_to(source).as_posix(): file_hash(p) for p in paths}
        if any(sync["files"].get(p) != h for p, h in hashes.items()):
            raise Refused("transfer manifest must hash the fixed full archive inventory")
        dates, status = financial_dates(source, paths), dart_listing.listing_status(source)
        temporary = Path(tempfile.mkdtemp(prefix=".ld002-distribution-", dir=directory))
        try:
            for path in paths:
                relative = path.relative_to(source).as_posix()
                if relative.endswith("_state.json") or relative.startswith("market/krx_daily/") and protected(path.stem):
                    continue
                target = safe_path(temporary, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if relative in REFERENCES:
                    shutil.copyfile(path, target)
                    continue
                with target.open("w", encoding="utf-8") as handle:
                    for row in rows(path):
                        if relative.startswith("fundamentals/"):
                            if row["available_date"] != dart_quarterly._filing_date(row["rcept_no"]):
                                raise Refused("financial receipt/availability mismatch")
                            day = dates.annotate(row)["actual_public_date"]
                            if day is None:
                                continue
                        elif relative.startswith("disclosures/"):
                            day = known_date(row["rcept_no"], row["rcept_dt"])
                        else:
                            day = row["trade_date"]
                        if relative.startswith("market/krx_daily/") and str(day).replace("-", "") != path.stem:
                            raise Refused("daily price filename/date mismatch")
                        if not protected(day):
                            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
            coverage = dart_listing.covered_listing_days(source, [d for d in status["main_completed_days"] if not protected(d)], status=status)
            (temporary / COVERAGE).write_text(json.dumps({"covered_listing_days": coverage}))
            if paths != inventory(source) or any(file_hash(source / p) != h for p, h in hashes.items()) or file_hash(sync_path) != started["sync_sha256"]:
                raise Refused("raw sources changed during distribution")
            files = {p.relative_to(temporary).as_posix(): file_hash(p) for p in sorted(temporary.rglob("*")) if p.is_file()}
            manifest = {"experiment_id": EXPERIMENT_ID, "scope": "all-archives-non-2026H1-v1", "synthetic": synthetic,
                "captured_at": aware(clock()).isoformat(), "sync_verified_at": sync["verified_at"], "files": files,
                "code_hashes": code_hashes, "sync_sha256": started["sync_sha256"]}
            if not synthetic:
                verify_distribution_code(state_root, repo_root, at=clock(), code_hashes=code_hashes)
            seal(temporary / "manifest.json", manifest)
            destination = directory / "snapshots" / digest(manifest)
            destination.parent.mkdir(exist_ok=True)
            _publish_directory(temporary, destination)
            if not synthetic:
                verify_distribution_code(state_root, repo_root, at=clock(), code_hashes=code_hashes)
            receipt = {"started_sha256": digest(started), "manifest_path": str((destination / "manifest.json").resolve()),
                       "manifest_sha256": digest(manifest), "finished_at": aware(clock()).isoformat()}
            seal(directory / "journal" / f"{number:06d}.complete.json", receipt)
            return receipt
        finally:
            _discard(temporary)


class Distribution:
    def __init__(self, directory, *, at, code_hashes, allow_synthetic=False):
        directory = Path(directory)
        starts = sorted((directory / "journal").glob("*.started.json"))
        if not starts or [int(p.name[:6]) for p in starts] != list(range(len(starts))):
            raise Refused("missing or noncontiguous distribution journal")
        complete = starts[-1].with_name(starts[-1].name.replace("started", "complete"))
        if not complete.exists():
            raise Refused("latest distribution incomplete; cannot use an older one")
        receipt = verified(complete)
        if receipt["started_sha256"] != digest(verified(starts[-1])) or aware(receipt["finished_at"]) > aware(at):
            raise Refused("invalid distribution completion receipt")
        self.root = Path(receipt["manifest_path"]).parent
        if not self.root.resolve().is_relative_to((directory / "snapshots").resolve()):
            raise Refused("distribution completion escapes its snapshot directory")
        self.manifest = verified(self.root / "manifest.json")
        if (digest(self.manifest) != receipt["manifest_sha256"] or self.root.name != receipt["manifest_sha256"]
                or self.manifest["experiment_id"] != EXPERIMENT_ID or self.manifest["scope"] != "all-archives-non-2026H1-v1"
                or aware(self.manifest["captured_at"]) > aware(at) or self.manifest["synthetic"] and not allow_synthetic):
            raise Refused("distribution hash, scope, date or synthetic identity differs")
        self.code_hashes = code_hashes
        self.verify_code()

    def verify_code(self):
        if self.manifest["code_hashes"] != self.code_hashes:
            raise Refused("distribution generating code differs from approved code")

    def path(self, relative):
        path = safe_path(self.root, relative)
        if (relative not in self.manifest["files"] or path.is_symlink() or not path.is_file()
                or path.stat().st_mode & 0o222 or file_hash(path) != self.manifest["files"][relative]):
            raise Refused("distribution file is missing, writable or changed: " + relative)
        return path
