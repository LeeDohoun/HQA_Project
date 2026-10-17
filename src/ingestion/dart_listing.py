"""One local reader for the main DART list and the delisting supplement.

The supplement checkpoint describes a frozen target set, not absence of filings
for unfinished corporations. Research must check coverage before interpreting
missing rows as zero; forward recorders can use the rows already published.

Research coverage requires a completed main day and its file. Days through the
supplement's end_de also require a verified supplement. Later days need only
the main list: the historical backfill ended at end_de (20261009), and the
daily job collects later filings in real time while listed companies are Y/K.
This assumes that later days are never recollected with today's corp_cls filter.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .storage import read_rows


START = "20160601"
IDENTITY_FIELDS = ("corp_code", "rcept_dt", "report_nm")
CURRENT_FIELDS = ("corp_cls", "corp_name", "stock_code", "flr_nm", "rm")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    if not Path(path).is_file():
        return None
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def supplement_manifest(data_dir):
    """Hash and count nonblank JSONL rows without loading the day archives."""
    root = directory(data_dir) / "list_supplement"
    manifest = {}
    for path in sorted(root.rglob("*.jsonl")):
        checksum, count = hashlib.sha256(), 0
        with path.open("rb") as handle:
            for line in handle:
                checksum.update(line)
                count += bool(line.strip())
        manifest[path.relative_to(root).as_posix()] = {"rows": count, "sha256": checksum.hexdigest()}
    return manifest


def overlap_differences(main, supplement):
    for field in IDENTITY_FIELDS:
        if main.get(field) != supplement.get(field):
            raise ValueError(f"DART conflicting duplicate listing receipt {supplement['rcept_no']}: {field}")
    return [field for field in CURRENT_FIELDS if main.get(field) != supplement.get(field)]


def _day(value):
    stamp = str(value).replace("-", "")[:8]
    if not re.fullmatch(r"[0-9]{8}", stamp):
        raise ValueError("DART listing day must be YYYYMMDD or YYYY-MM-DD")
    datetime.strptime(stamp, "%Y%m%d")
    return stamp


def directory(data_dir):
    return Path(data_dir) / "disclosures/dart_full"


def _state(path):
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("DART listing checkpoint must be an object")
    return value


def reference_version(data_dir):
    return {name: file_hash(Path(data_dir) / "reference" / name)
            for name in ("corp_codes.csv", "dart_company/companies.jsonl")}


def validate_supplement_state(state):
    targets, completed = state.get("target_corps"), state.get("completed_corps")
    for values in (targets, completed):
        if (not isinstance(values, list)
                or any(not isinstance(c, str) or not re.fullmatch(r"[0-9]{8}", c) for c in values)
                or values != sorted(set(values))):
            raise ValueError("DART invalid supplement corporation checkpoint")
    if (state.get("start_de") != START or state.get("target_set_hash") != digest(targets)
            or not set(completed).issubset(targets) or _day(state.get("end_de")) < START):
        raise ValueError("DART invalid supplement target or range checkpoint")
    for field in ("rows_per_corp", "skipped_per_corp", "duplicates_per_corp"):
        counts = state.get(field)
        if (not isinstance(counts, dict) or set(counts) != set(completed)
                or any(type(n) is not int or n < 0 for n in counts.values())):
            raise ValueError("DART invalid supplement row counters")
    if not isinstance(state.get("reference_version"), dict):
        raise ValueError("DART missing supplement reference provenance")
    return state


def listing_paths(data_dir, start=None, end=None, *, main_dir=None):
    """Day -> representative path; all actual row reads go through listing_rows."""
    root = directory(data_dir)
    paths = {}
    for folder in (root / "list_supplement", Path(main_dir) if main_dir is not None else root / "list"):
        for path in sorted(folder.glob("*/*.jsonl")):
            if (re.fullmatch(r"[0-9]{8}", path.stem)
                    and (start is None or path.stem >= _day(start))
                    and (end is None or path.stem <= _day(end))):
                _day(path.stem)
                paths[path.stem] = path
    return dict(sorted(paths.items()))


def listing_rows(data_dir, day, *, main_dir=None, conflicts=None):
    stamp, root = _day(day), directory(data_dir)
    main = Path(main_dir) if main_dir is not None else root / "list"
    rows = {}
    for folder in (main, root / "list_supplement"):
        for row in read_rows(folder / stamp[:4] / f"{stamp}.jsonl"):
            receipt = row.get("rcept_no")
            if (not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt)
                    or row.get("rcept_dt") != stamp):
                raise ValueError("DART invalid listing receipt or archive date")
            if receipt in rows:
                if conflicts is not None and rows[receipt] != row:
                    conflicts.add(receipt)
                try:
                    overlap_differences(rows[receipt], row)
                except ValueError:
                    if conflicts is None:
                        raise
                    conflicts.add(receipt)
            if conflicts is not None and receipt in conflicts:
                continue
            if receipt not in rows:
                rows[receipt] = row
    return list(rows.values())


def supplement_paths(data_dir, days):
    root = directory(data_dir) / "list_supplement"
    return [path for day in days if (path := root / _day(day)[:4] / f"{_day(day)}.jsonl").is_file()]


def listing_version(data_dir, *, manifest=None):
    root = directory(data_dir)
    return {"reader": "dart-listing-v3-frozen-target-scan", "main_state_sha256": file_hash(root / "_state.json"),
            "supplement_state_sha256": file_hash(root / "list_supplement/_state.json"),
            "supplement_files_sha256": digest(supplement_manifest(data_dir) if manifest is None else manifest)}


def target_scan_paths(data_dir, end_de):
    return sorted(p for p in (Path(data_dir) / "market/krx_daily").glob("*/*.jsonl")
                  if re.fullmatch(r"[0-9]{8}", p.stem) and START <= p.stem <= end_de)


def freeze_target_scan(data_dir, end_de):
    """Operator collector/backfill only: scan identities and freeze the price inputs."""
    root, traded, hashes = Path(data_dir), set(), {}
    references = reference_version(root)
    paths = target_scan_paths(root, end_de)
    if not paths:
        raise ValueError("DART supplement historical KRX sessions are missing")
    for path in paths:
        before = file_hash(path)
        for row in read_rows(path):
            if str(row.get("trade_date", "")).replace("-", "") != path.stem:
                raise ValueError("KRX daily date disagrees with archive")
            code = row.get("stock_code")
            if not isinstance(code, str) or len(code) != 6:
                raise ValueError("KRX invalid daily stock code")
            traded.add(code)
        if before != file_hash(path):
            raise ValueError("KRX target scan input changed during scan")
        hashes[path.relative_to(root).as_posix()] = before
    if paths != target_scan_paths(root, end_de):
        raise ValueError("KRX target scan file list changed during scan")
    with (root / "reference/corp_codes.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"corp_code", "stock_code"}.issubset(reader.fieldnames or []):
            raise ValueError("DART invalid corporate-code CSV columns")
        corps = {}
        for row in reader:
            corp, code = row["corp_code"], row["stock_code"].strip()
            if not re.fullmatch(r"[0-9]{8}", corp):
                raise ValueError("DART invalid corporate code")
            if code in traded:
                corps.setdefault(corp, set()).add(code)
    if references != reference_version(root):
        raise ValueError("DART references changed during target scan")
    return {"start_de": START, "end_de": end_de, "price_files": hashes,
            "traded_stock_codes": sorted(traded),
            "traded_codes_per_corp": {c: sorted(codes) for c, codes in sorted(corps.items())},
            "reference_version": references}


def verified_target_scan(data_dir, state, *, price_end=None, experiment_id=None):
    """Verify the inventory; bound price hashes for a preregistered stage.

    Coverage never opens protected prices, even inside an experiment session.
    """
    from backtesting import holdout

    scan = state.get("target_scan")
    if (not isinstance(scan, dict) or scan.get("start_de") != START
            or scan.get("end_de") != state["end_de"]
            or not isinstance(scan.get("price_files"), dict)
            or not scan["price_files"]
            or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                   for h in scan["price_files"].values())
            or not isinstance(scan.get("traded_stock_codes"), list)
            or any(not isinstance(c, str) or not re.fullmatch(r"[0-9A-Za-z]{6}", c)
                   for c in scan["traded_stock_codes"])
            or scan["traded_stock_codes"] != sorted(set(scan["traded_stock_codes"]))
            or not isinstance(scan.get("traded_codes_per_corp"), dict)):
        raise ValueError("DART frozen target scan missing or invalid; run offline target-scan backfill")
    paths = target_scan_paths(data_dir, state["end_de"])
    if {p.relative_to(data_dir).as_posix() for p in paths} != set(scan["price_files"]):
        raise ValueError("DART frozen target scan KRX file list changed")
    for path in paths:
        day = datetime.strptime(path.stem, "%Y%m%d").date()
        if price_end is not None and path.stem > _day(price_end):
            continue
        if holdout.HOLDOUT_START <= day <= holdout.HOLDOUT_END:
            continue
        holdout.guard_period(day, day, experiment_id=experiment_id)
        if file_hash(path) != scan["price_files"][path.relative_to(data_dir).as_posix()]:
            raise ValueError("DART frozen target scan non-protected KRX file changed")
    return scan


def required_supplement_targets(data_dir, end_de, *, included_targets=(), frozen_scan=None):
    """Current non-Y/K, historically traded corporations absent from main."""
    root = Path(data_dir).resolve()
    sources = sorted(p for p in (directory(root) / "list").glob("*/*.jsonl")
                     if re.fullmatch(r"[0-9]{8}", p.stem) and p.stem <= end_de)
    if frozen_scan is None:  # Only the operator collector may rescan price contents.
        sources += target_scan_paths(root, end_de)
    versions = []
    for path in sources:
        stat = path.stat()
        versions.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    # Archives are atomically published. Replacements, edits, additions and
    # removals invalidate this in-process scan even if mtime or size is kept,
    # because ctime changes; ctime moves in coarse kernel ticks, so an edit in
    # the same tick as the cached scan can be missed within one process.
    source_version = (tuple(sorted(reference_version(root).items())), tuple(versions))
    traded = None if frozen_scan is None else tuple(frozen_scan["traded_stock_codes"])
    return list(_required_supplement_targets(root, end_de, tuple(sorted(included_targets)), source_version, traded))


@lru_cache(maxsize=1)
def _required_supplement_targets(data_dir, end_de, included_targets, source_version, frozen_traded):
    """Cache the metadata scan only; supplement file hashing is never cached."""
    root = Path(data_dir)
    master, profiles_path = root / "reference/corp_codes.csv", root / "reference/dart_company/companies.jsonl"
    if not master.is_file() or not profiles_path.is_file():
        raise ValueError("DART supplement current reference files are missing")
    profiles = {}
    for row in read_rows(profiles_path):
        corp = row.get("corp_code")
        if not isinstance(corp, str) or not re.fullmatch(r"[0-9]{8}", corp):
            raise ValueError("DART invalid company profile identity")
        if corp in profiles and profiles[corp] != row.get("corp_cls"):
            raise ValueError("DART conflicting current company classifications")
        profiles[corp] = row.get("corp_cls")
    candidates = {}
    included = set(included_targets)
    with master.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"corp_code", "stock_code"}.issubset(reader.fieldnames or []):
            raise ValueError("DART invalid corporate-code CSV columns")
        for row in reader:
            corp, code = row["corp_code"], row["stock_code"].strip()
            if not re.fullmatch(r"[0-9]{8}", corp):
                raise ValueError("DART invalid corporate code")
            if corp not in included and re.fullmatch(r"[0-9]{6}", code) and profiles.get(corp) not in {"Y", "K"}:
                candidates.setdefault(corp, set()).add(code)
    if not candidates:
        return ()
    for main_path in sorted((directory(data_dir) / "list").glob("*/*.jsonl")):
        # Only main rows determine whether a later-delisted company needs repair.
        if not re.fullmatch(r"[0-9]{8}", main_path.stem) or main_path.stem > end_de:
            continue
        for row in read_rows(main_path):
            candidates.pop(row.get("corp_code"), None)
        if not candidates:
            return ()
    codes = {code for values in candidates.values() for code in values}
    if frozen_traded is not None:
        traded = set(frozen_traded)
        return tuple(sorted(corp for corp, values in candidates.items() if values & traded))
    traded = set()
    paths = sorted(p for p in (root / "market/krx_daily").glob("*/*.jsonl")
                   if re.fullmatch(r"[0-9]{8}", p.stem) and START <= p.stem <= end_de)
    if not paths:
        raise ValueError("DART supplement historical KRX sessions are missing")
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if str(row.get("trade_date", "")).replace("-", "") != path.stem:
                        raise ValueError("KRX daily date disagrees with archive")
                    if row.get("stock_code") in codes:
                        traded.add(row["stock_code"])
        if codes.issubset(traded):
            break
    return tuple(sorted(corp for corp, values in candidates.items() if values & traded))


def listing_status(data_dir, *, price_end=None, experiment_id=None):
    root = directory(data_dir)
    main = _state(root / "_state.json") or {}
    days = main.get("completed_listing_days", [])
    if not isinstance(days, list) or len(set(days)) != len(days):
        raise ValueError("DART invalid main completed listing days")
    for day in days:
        if _day(day) != day:
            raise ValueError("DART invalid main completed listing day")
    state = _state(root / "list_supplement/_state.json")
    reason = "supplement_absent"
    actual = supplement_manifest(data_dir)
    missing_targets, required = [], []
    integrity = {}
    current_reference = reference_version(data_dir)
    if state is not None:
        validate_supplement_state(state)
        manifest = state.get("file_manifest")
        if state["completed_corps"] != state["target_corps"]:
            reason = "supplement_pending_corps"
        elif not isinstance(manifest, dict):
            reason = "supplement_manifest_missing"
        else:
            reason = None
            integrity = {"missing": sorted(set(manifest) - set(actual)),
                         "extra": sorted(set(actual) - set(manifest)),
                         "changed": sorted(p for p in set(actual) & set(manifest) if actual[p] != manifest[p])}
            if any(integrity.values()):
                reason = "supplement_files_mismatch"
            elif sum(item["rows"] for item in actual.values()) != sum(state["rows_per_corp"].values()):
                reason = "supplement_manifest_row_count_mismatch"
            else:
                try:
                    scan = verified_target_scan(Path(data_dir), state, price_end=price_end, experiment_id=experiment_id)
                    required = required_supplement_targets(data_dir, state["end_de"],
                        included_targets=state["target_corps"], frozen_scan=scan)
                except ValueError as exc:
                    reason = "supplement_reference_unavailable"
                    integrity["reference_error"] = str(exc)
                else:
                    missing_targets = sorted(set(required) - set(state["target_corps"]))
                    if missing_targets:
                        reason = "supplement_targets_missing"
    return {"main_completed_days": sorted(days), "supplement_present": state is not None,
            "supplement_complete": reason is None, "reason": reason,
            "start_de": state["start_de"] if state else None,
            "end_de": state["end_de"] if state else None,
            "target_set_hash": state["target_set_hash"] if state else None,
            "target_count": len(state["target_corps"]) if state else None,
            "completed_corps": len(state["completed_corps"]) if state else 0,
            "missing_target_corps": missing_targets,
            "file_integrity": integrity,
            "reference_version_at_collection": state["reference_version"] if state else None,
            "reference_version_now": current_reference,
            "coverage_rule": "main_completed_day_with_file AND (verified_supplement OR day > end_de)",
            "post_end_de_assumption": "Daily real-time collection while listed companies are Y/K; no historical corp_cls-filtered recollection after end_de.",
            "listing_version": listing_version(data_dir, manifest=actual)}


def supplement_covers(status, day):
    return bool(status["supplement_complete"] and status["start_de"] <= _day(day) <= status["end_de"])


def main_complete(data_dir, day, status):
    stamp = _day(day)
    return stamp in status["main_completed_days"] and (directory(data_dir) / "list" / stamp[:4] / f"{stamp}.jsonl").is_file()


def covered_listing_days(data_dir, days, *, status=None):
    """Return fully collected days, separately from the union of readable files."""
    status = listing_status(data_dir) if status is None else status
    return sorted({_day(day) for day in days if main_complete(data_dir, day, status)
                   and (supplement_covers(status, day)
                        or (status["end_de"] is not None and _day(day) > status["end_de"]))})


def require_supplement(data_dir, days):
    status = listing_status(data_dir)
    uncovered = sorted({_day(day) for day in days if not supplement_covers(status, day)
                        and not (status["end_de"] is not None and _day(day) > status["end_de"])})
    if uncovered:
        extent = f"; uncovered {uncovered[0]}..{uncovered[-1]}" if uncovered else ""
        raise ValueError("DART listing supplement incomplete: " + str(status["reason"] or "range") + extent
                         + "; finish scripts/data/dart_list_supplement.py before new research inputs")
    return status["listing_version"]
