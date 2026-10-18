"""Run after every research-machine sync: python -m scripts.lh003.distribute."""
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from src.ingestion import dart_listing, dart_quarterly
from src.ingestion.dart_financial_date_rules import FilingDates
from src.ingestion.dart_filing_dates import known_date
from src.ingestion.storage import file_lock

from backtesting.experiments.lh003 import EXPERIMENT_ID, Refused
from backtesting.experiments.lh003.distribution import COVERAGE, DAILY, REFERENCES, SCOPE, protected
from backtesting.experiments.lh003.storage import aware, digest, file_hash, now, read, safe_path, seal


def archive_rows(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def inventory(root):
    paths = {p for folder in DAILY for p in (root / folder).glob("*/*.jsonl")}
    paths |= set((root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    paths |= {root / name for name in (*REFERENCES, "market_context/benchmarks.jsonl",
                                       "disclosures/dart_full/_state.json")}
    supplement = root / "disclosures/dart_full/list_supplement/_state.json"
    if supplement.exists():
        paths.add(supplement)
    if not any(p.parent.name == "dart_quarterly" for p in paths):
        raise Refused("distribution requires quarterly financial archives")
    return sorted(paths)


def financial_dates(root, paths):
    # Keep only financial receipt identities in memory; L still uses the full,
    # fixed listing scope. No H1 lag, counts or other summaries are published.
    receipts = {r["rcept_no"] for p in paths if p.parent.name == "dart_quarterly" for r in archive_rows(p)}
    conflicts = set()
    rows = (r for day in dart_listing.listing_paths(root)
            for r in dart_listing.listing_rows(root, day, conflicts=conflicts))
    return FilingDates(rows, conflicts=conflicts, receipt_filter=receipts)


def publish(*, data_dir, directory, sync_manifest, clock=now, synthetic=False):
    """Operational mechanical reads only; never called by the research runner."""
    source, directory = Path(data_dir).resolve(), Path(directory).resolve()
    if directory.is_relative_to(source):
        raise Refused("distribution output must be separate from mixed raw archives")
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(directory / ".publication.lock"):
        at = aware(clock())
        sync_hash = file_hash(sync_manifest)
        starts = sorted((directory / "publications").glob("*.started.json"))
        number = len(starts)
        started = {"at": at.isoformat(), "sync_manifest_sha256": sync_hash}
        seal(directory / "publications" / f"{number:06d}.started.json", started)
        sync = read(sync_manifest)
        if aware(sync["verified_at"]) > at:
            raise Refused("distribution sync verification is from the future")
        paths = inventory(source)
        hashes = {p.relative_to(source).as_posix(): file_hash(p) for p in paths}
        if any(h is None or sync["files"].get(name) != h for name, h in hashes.items()):
            raise Refused("distribution sync must hash the entire fixed archive scope")
        dates = financial_dates(source, paths)
        status = dart_listing.listing_status(source)
        completed = [d for d in status["main_completed_days"] if not protected(d)]
        covered = dart_listing.covered_listing_days(source, completed, status=status)
        with tempfile.TemporaryDirectory(prefix=".publication-", dir=directory) as scratch:
            target = Path(scratch) / "snapshot"
            target.mkdir()
            for path in paths:
                relative = path.relative_to(source).as_posix()
                if relative.endswith("_state.json"):
                    continue
                out = safe_path(target, relative)
                if relative.startswith("market/krx_daily/") and protected(path.stem):
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                if relative in REFERENCES:
                    shutil.copyfile(path, out)
                elif relative.startswith("market/krx_daily/"):
                    if any(str(r["trade_date"]).replace("-", "") != path.stem for r in archive_rows(path)):
                        raise Refused("price archive date mismatch")
                    shutil.copyfile(path, out)
                else:
                    with out.open("w", encoding="utf-8") as handle:
                        for row in archive_rows(path):
                            if relative.startswith("fundamentals/"):
                                if row["available_date"] != dart_quarterly._filing_date(row["rcept_no"]):
                                    raise Refused("financial receipt/availability mismatch")
                                day = dates.annotate(row)["actual_public_date"]
                            elif relative.startswith("disclosures/"):
                                day = known_date(row["rcept_no"], row["rcept_dt"])
                            else:
                                day = row["trade_date"]
                            if day is None and relative.startswith("fundamentals/"):
                                continue
                            if not protected(day):
                                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
            # Coverage comes from the established listing rules. Raw checkpoints
            # include H1 counters/target scans and never cross this boundary.
            (target / COVERAGE).write_text(json.dumps({"covered_listing_days": covered}), encoding="utf-8")
            if paths != inventory(source) or any(file_hash(source / p) != h for p, h in hashes.items()) or file_hash(sync_manifest) != sync_hash:
                raise Refused("archive inventory or contents changed during distribution")
            files = {}
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    with path.open("rb") as handle:
                        os.fsync(handle.fileno())
                    path.chmod(0o444)
                    files[path.relative_to(target).as_posix()] = file_hash(path)
            manifest = {"experiment_id": EXPERIMENT_ID, "scope": SCOPE, "synthetic": synthetic,
                        "captured_at": at.isoformat(), "sync_verified_at": aware(sync["verified_at"]).isoformat(),
                        "sync_manifest_sha256": sync_hash, "files": files}
            checksum = digest(manifest)
            seal(target / "manifest.json", manifest)
            destination = directory / "snapshots" / checksum
            destination.parent.mkdir(exist_ok=True)
            for folder in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
                folder.chmod(0o555)
            if destination.exists():
                from backtesting.experiments.lh003.distribution import Distribution
                Distribution(destination / "manifest.json", expected_hash=checksum, at=clock(), allow_synthetic=synthetic)
            else:
                os.rename(target, destination)
                destination.chmod(0o555)
            descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        receipt = {"started_sha256": digest(started), "manifest_sha256": checksum,
                   "manifest_path": (destination / "manifest.json").relative_to(directory).as_posix(),
                   "publication_finished_at": aware(clock()).isoformat()}
        seal(directory / "publications" / f"{number:06d}.complete.json", receipt)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--distribution-dir", type=Path, required=True)
    parser.add_argument("--sync-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    result = publish(data_dir=args.data_dir, directory=args.distribution_dir, sync_manifest=args.sync_manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
