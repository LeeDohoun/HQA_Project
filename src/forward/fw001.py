"""FW001 current-month recording. No experiment runs, controls, APIs or orders.

Writes are opt-in. Decisions and executions are separate immutable objects;
prices are content-addressed server snapshots. Missing decisions never trade.
"""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from datetime import datetime, time
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backtesting import cost_model, holdout
from backtesting.append_only import atomic_replace, fsync_directory
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, common, hc002, hc003, hf001
from src.ingestion import dart_financial_date_rules as financial_dates
from src.ingestion import dart_listing, dart_quarterly, krx_market
from src.ingestion.storage import file_lock, read_rows
from src.runner import trading_calendar
from scripts.ops import collector_manifest


EXPERIMENT_ID = "FW001_forward_replication"
PREREGISTRATION_COMMIT = "23e2051"
RESEARCH_DIR = Path("research/experiments") / EXPERIMENT_ID
RECORD_DIR = Path("forward/fw001")
MONTHS = pd.period_range("2026-10", "2027-09", freq="M")
FREEZE_AT = datetime(2027, 11, 15, tzinfo=ZoneInfo("Asia/Seoul"))
KST = ZoneInfo("Asia/Seoul")
PRICE_COLUMNS = {"trade_date", "stock_code", "stock_name", "open", "close", "volume", "market",
                 "trading_value", "calendar_status", "change_rate_pct", "base_price"}
# Local Linux filesystem metadata may use the kernel's coarse wall clock.
# Accept only publication intervals wholly before a cutoff; never round down
# a near-deadline publication into eligibility. Deployment verifies this bound.
PUBLICATION_PRECISION = pd.Timedelta(seconds=1)


def now():
    return datetime.now(KST)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def calendar(day):
    stamp = pd.Timestamp(day)
    # 126 preceding listing sessions, 60 price sessions, and the 20th exit.
    # Extend by a month only if the requested session count needs it.
    start, end = stamp - pd.DateOffset(months=7), stamp + pd.DateOffset(months=1)
    history = trading_calendar.session_calendar(start, stamp)
    while len(history) < 127:
        start -= pd.DateOffset(months=1)
        history = trading_calendar.session_calendar(start, stamp)
    future = trading_calendar.session_calendar(stamp, end)
    while len(future) < 21:
        end += pd.DateOffset(months=1)
        future = trading_calendar.session_calendar(stamp, end)
    return history[-127:].union(future[:21])


def verify_manifest(*, implementation=False):
    return collector_manifest.verify(PROJECT_ROOT, implementation=implementation)


def plan(day, *, sessions=None):
    day = pd.Timestamp(day)
    sessions = calendar(day) if sessions is None else pd.DatetimeIndex(sessions)
    monthly = sessions[sessions.to_period("M") == day.to_period("M")]
    if day.tzinfo is not None or day != day.normalize() or not len(monthly) or day != monthly[-1]:
        raise ValueError("FW001 requires the last XKRX session of the decision month")
    pos = sessions.get_loc(day)
    entry = sessions[pos + 1]
    return {"month": str(day.to_period("M")), "decision_date": day.date().isoformat(),
            "trade_date": entry.date().isoformat(),
            "deadline": datetime.combine(entry.date(), time(8, 59), KST).isoformat(),
            "f_both_exit": sessions[pos + 20].date().isoformat()}


def path_for(root, kind, month, multiplier=None):
    suffix = month.replace("-", "") + ("" if multiplier is None else f"_{multiplier}") + ".json"
    return Path(root) / RECORD_DIR / kind / suffix


def durable_directory(path):
    missing, parent = [], path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        fsync_directory(directory.parent)


def put(path, record, *, execute=False, deadline=None):
    """Fsync then atomic no-clobber link, under a caller's recording lock.

    This only publishes a candidate. The separate completion marker is the
    sole finalisation point, including when this write crosses the deadline.
    """
    path = Path(path)
    if path.exists():
        previous = load_json(path)
        if previous != record:
            raise ValueError(f"immutable FW001 record differs: {path}")
        return previous
    record = dict(record)
    if deadline is not None:
        stamp = now()
        record["finalised_at"] = stamp.isoformat()
        record["label"] = "forward" if stamp < deadline else "reconstructed"
    if not execute:
        return record
    durable_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".fw001-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        fsync_directory(path.parent)
        return record
    finally:
        os.unlink(temporary)


def marker_path(data_dir, kind, month, multiplier=None):
    suffix = "decision" if kind == "decisions" else f"execution.{float(multiplier)}"
    return Path(data_dir) / RECORD_DIR / "sealed" / (month.replace("-", "") + f".{suffix}") / "complete.json"


def publication_time(marker):
    """The private directory's last mutation is the atomic marker link.

    Never place another entry in this directory. Copies must preserve directory
    mtimes (rsync -a); the marker's file mtime only dates its preparation.
    """
    proof = load_json(marker)
    if (proof.get("publication_clock") != "private_directory_mtime_ns"
            or proof.get("publication_precision_ns") != PUBLICATION_PRECISION.value
            or {p.name for p in marker.parent.iterdir()} != {marker.name}):
        raise ValueError("FW001 invalid completion publication evidence: " + str(marker))
    published = marker.parent.stat().st_mtime_ns
    prepared = datetime.fromisoformat(proof["prepared_at"])
    sealed = pd.Timestamp(published, unit="ns", tz="UTC").tz_convert(KST)
    if (prepared.utcoffset() is None or sealed + PUBLICATION_PRECISION < prepared
            or published < marker.stat().st_mtime_ns):
        raise ValueError("FW001 inconsistent completion publication time: " + str(marker))
    return sealed


def publish_completion(marker, proof, *, deadline=None):
    """Publish once; a late link stays invalid, never published then revoked."""
    durable_directory(marker.parent)
    # Temporary creation/removal must not change the publication directory.
    fd, temporary = tempfile.mkstemp(prefix=".fw001-proof-", dir=marker.parent.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded(proof))
            handle.flush()
            os.fsync(handle.fileno())
        if deadline is not None and now() >= deadline:
            return False
        os.link(temporary, marker)
        fsync_directory(marker.parent)
        return True
    finally:
        os.unlink(temporary)


def completion_proof(data_dir, kind, month, multiplier=None, *, decision=None):
    """Return None for absent/rejected timing evidence; corrupt evidence raises.

    Rejection keeps the candidate and marker intact as audit evidence. It is
    never an observation, including after restart.
    """
    path = path_for(data_dir, kind, month, multiplier)
    marker = marker_path(data_dir, kind, month, multiplier)
    if not path.is_file() or not marker.is_file():
        return None
    record, proof = load_json(path), load_json(marker)
    if (proof["record_path"] != path.relative_to(data_dir).as_posix()
            or proof["record_digest"] != dart_listing.file_hash(path)
            or proof["reconstructed"] is not False or record["label"] != "forward"):
        raise ValueError("FW001 invalid completion proof: " + str(marker))
    sealed = publication_time(marker)
    proof["sealed_at"] = sealed.isoformat()
    latest = sealed + PUBLICATION_PRECISION
    proof["publication_not_after"] = latest.isoformat()
    if kind == "decisions":
        if (proof["deadline"] != record["deadline"]
                or latest < datetime.fromisoformat(record["finalised_at"])):
            raise ValueError("FW001 inconsistent decision completion proof")
        if latest >= datetime.fromisoformat(record["deadline"]):
            return None  # Late or precision-boundary publication: section 6 omission.
    else:
        decision = load_json(path_for(data_dir, "decisions", month)) if decision is None else decision
        linked = completion_proof(data_dir, "decisions", month)
        if (record["decision_hash"] != digest(decision)
                or proof["deadline"] != decision["deadline"]
                or latest < datetime.fromisoformat(record["recorded_at"])):
            raise ValueError("FW001 execution completion proof has no valid linked decision")
        if linked is None and marker_path(data_dir, "decisions", month).is_file():
            return None  # A rejected decision cannot support an execution observation.
        if linked is None or sealed < datetime.fromisoformat(linked["sealed_at"]):
            raise ValueError("FW001 execution completion proof has no valid linked decision")
    return proof


def complete(data_dir, kind, record, *, multiplier=None, execute=False, decision=None):
    """Caller holds .record.lock. Publish proof only after durable record/check."""
    path = path_for(data_dir, kind, record["month"], multiplier)
    if not execute or record["label"] != "forward":
        return record
    if completion_proof(data_dir, kind, record["month"], multiplier, decision=decision) is not None:
        return record
    if marker_path(data_dir, kind, record["month"], multiplier).is_file():
        return record  # Rejected publication is immutable; never re-complete it.
    deadline = record["deadline"] if kind == "decisions" else decision["deadline"]
    if kind == "decisions" and now() < datetime.fromisoformat(deadline):
        verify_input_snapshots(data_dir, record)
    elif kind == "executions":
        for name, checksum in record["input_hashes"].items():
            if dart_listing.file_hash(Path(data_dir) / name) != checksum:
                raise ValueError("FW001 execution sealed input unavailable or corrupt: " + name)
    checksum = dart_listing.file_hash(path)
    stamp = now()
    if kind == "decisions" and stamp >= datetime.fromisoformat(deadline):
        record = {**record, "label": "reconstructed", "finalised_at": stamp.isoformat()}
        atomic_replace(path, encoded(record).encode())
        return record
    proof = {"record_path": path.relative_to(data_dir).as_posix(),
             "record_digest": checksum, "prepared_at": stamp.isoformat(),
             "publication_clock": "private_directory_mtime_ns",
             "publication_precision_ns": PUBLICATION_PRECISION.value,
             "deadline": deadline, "reconstructed": False}
    marker = marker_path(data_dir, kind, record["month"], multiplier)
    if not publish_completion(marker, proof, deadline=(datetime.fromisoformat(deadline)
                                                     if kind == "decisions" else None)):
        record = {**record, "label": "reconstructed", "finalised_at": now().isoformat()}
        atomic_replace(path, encoded(record).encode())
        return record
    # Validate the kernel publication time, not a second wall-clock cutoff.
    # A link delayed across the deadline is invalid even after an immediate crash.
    completion_proof(data_dir, kind, record["month"], multiplier, decision=decision)
    return record


def snapshot_file(data_dir, source, checksum, *, execute=False):
    blob = Path(data_dir) / RECORD_DIR / "snapshots" / (checksum + source.suffix)
    if blob.is_file():
        if dart_listing.file_hash(blob) != checksum:
            raise ValueError("FW001 content-addressed snapshot is corrupt: " + str(blob))
    elif execute:
        durable_directory(blob.parent)
        fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=blob.parent)
        try:
            with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if dart_listing.file_hash(Path(temporary)) != checksum:
                raise ValueError("FW001 input changed during snapshot: " + str(source))
            os.link(temporary, blob)
            fsync_directory(blob.parent)
        finally:
            os.unlink(temporary)
    return {"sha256": checksum, "blob": blob.relative_to(data_dir).as_posix()}


def verify_input_snapshots(data_dir, decision):
    snapshots = decision["input_snapshots"]
    if set(snapshots) != set(decision["input_hashes"]):
        raise ValueError("FW001 decision input snapshot inventory differs")
    for name, item in snapshots.items():
        if (item["sha256"] != decision["input_hashes"][name]
                or dart_listing.file_hash(Path(data_dir) / item["blob"]) != item["sha256"]):
            raise ValueError("FW001 decision input snapshot unavailable or corrupt: " + name)


def verified_day(data_dir, day):
    trading_calendar.daily_session_close(pd.Timestamp(day).date().isoformat())
    rows = krx_market.load_universe(pd.Timestamp(day).date().isoformat(), data_dir)
    if (not rows or {r.get("market") for r in rows} != {"KOSPI", "KOSDAQ"}
            or any(r.get("calendar_status") != "verified"
                   or r.get("trade_date") != pd.Timestamp(day).date().isoformat() for r in rows)):
        raise ValueError(f"FW001 waits for complete verified KOSPI/KOSDAQ daily data: {day}")
    return rows


def price_window(data_dir, days):
    if len(days) == 0:
        raise ValueError("empty FW001 price window")
    holdout.guard_period(days[0].date(), days[-1].date(), experiment_id=EXPERIMENT_ID)
    prices = krx_market.load_prices(None, days[0].strftime("%Y%m%d"), days[-1].strftime("%Y%m%d"),
                                   data_dir, columns=PRICE_COLUMNS)
    if len(common._sessions(prices).difference(days)):
        raise ValueError("FW001 price window contains non-session observations")
    verify_price_calendar(prices)
    return prices


def verify_price_calendar(prices):
    if prices.empty:
        return
    if "calendar_status" not in prices or not prices["calendar_status"].eq("verified").all():
        raise ValueError("FW001 price window contains unverified calendar sessions")
    for day in common._sessions(prices):
        trading_calendar.daily_session_close(day.date().isoformat())


def recorded_price_window(data_dir, days, *, pc_dir=None, descriptors=None):
    paths = []
    for day in days:
        descriptor = path_for(data_dir, "prices", day.strftime("%Y%m%d"))
        record = descriptors.get(day.strftime("%Y%m%d")) if descriptors is not None else (
            load_json(descriptor) if descriptor.is_file() else None)
        if record is None:
            paths.append(krx_market._path(day.date(), data_dir))
            continue
        pc_path = Path(pc_dir) / record["path"] if pc_dir is not None else None
        path = pc_path if pc_path is not None and dart_listing.file_hash(pc_path) == record["sha256"] else (
            Path(data_dir) / RECORD_DIR / "snapshots" / (record["sha256"] + ".jsonl"))
        if dart_listing.file_hash(path) != record["sha256"]:
            raise ValueError("FW001 server frozen price unavailable or corrupt: " + record["day"])
        paths.append(path)
    rows = []
    for day, path in zip(days, paths):
        latest = {row["stock_code"]: row for row in read_rows(path)}
        if not latest or any(row["trade_date"] != str(day.date())
                             for row in latest.values()):
            raise ValueError("FW001 frozen price day incomplete: " + str(day.date()))
        rows.extend({key: value for key, value in row.items() if key in PRICE_COLUMNS}
                    for _, row in sorted(latest.items()))
    frame = pd.DataFrame(rows)
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    for key in ("open", "close", "volume", "trading_value", "change_rate_pct", "base_price"):
        if key in frame:
            frame[key] = pd.to_numeric(frame[key], errors="raise")
    frame["ret_1d"] = frame["change_rate_pct"] / 100 if "change_rate_pct" in frame else np.nan
    if "base_price" not in frame:
        frame["base_price"] = np.nan
    frame = frame.set_index(["trade_date", "stock_code"]).sort_index()
    verify_price_calendar(frame)
    return frame


def book_from_days(data_dir, days, *, entry=None, chain=None, price_loader=None):
    loader = price_window if price_loader is None else price_loader
    def windows():
        for start in range(0, len(days), 20):
            yield loader(data_dir, days[start:start + 20])
    return hc003.PriceBook.from_windows(windows(), days, [] if entry is None else [entry], days[-1],
        experiment_id=EXPERIMENT_ID, initial_chain=chain)


def input_paths(data_dir, days):
    root = Path(data_dir)
    paths = [p for day in days if (p := krx_market._path(day.date(), root)).is_file()]
    paths += sorted((root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    for folder in ("list", "list_supplement"):
        paths += sorted((root / "disclosures/dart_full" / folder).glob("*/*.jsonl"))
    paths += [root / p for p in ("reference/corp_codes.csv", "reference/dart_company/companies.jsonl",
        "disclosures/dart_full/_state.json", "disclosures/dart_full/list_supplement/_state.json",
        "fundamentals/dart_quarterly/_state.json", "fundamentals/dart_quarterly/_batch_status.json",
        "fundamentals/_refresh_last_run.json") if (root / p).is_file()]
    return sorted(set(paths))


def hashes(root, paths):
    return {p.relative_to(root).as_posix(): dart_listing.file_hash(p) for p in paths}


def code_hashes():
    manifest = PROJECT_ROOT / RESEARCH_DIR / "deployment_manifest.json"
    if manifest.is_file():
        paths = [PROJECT_ROOT / p for p in load_json(manifest)["files"]]
    else:
        paths = [Path(__file__), Path(av001.__file__), Path(hc003.__file__), Path(hf001.__file__),
                 Path(hc002.__file__), Path(cost_model.__file__), Path(financial_dates.__file__),
                 PROJECT_ROOT / RESEARCH_DIR / "preregistration.md"]
    result = hashes(PROJECT_ROOT, paths)
    if manifest.is_file():
        result[manifest.relative_to(PROJECT_ROOT).as_posix()] = dart_listing.file_hash(manifest)
    return result


def financial_frames(data_dir, day):
    """Same quarterly derivation/ranking, bounded raw accounts one year at a time.

    Retain only needed receipt links while scanning the full merged listing for
    L. S gates before derivation; HF001 keeps its legacy receipt-prefix dates.
    No original-date rule or design/holdout stage is used in this recorder.
    """
    paths = sorted((Path(data_dir) / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    if not paths:
        raise ValueError("FW001 requires quarterly financial archives")
    day = pd.Timestamp(day).date().isoformat()
    receipts = set()
    for path in paths:
        for row in read_rows(path):
            if row["available_date"] < day:
                receipts.add(row["rcept_no"])
    dates = financial_dates.FilingDates.from_data(data_dir, receipt_filter=receipts)
    values, corrections, counts = {"stored": [], "S": []}, set(), Counter()
    for year, files in groupby(paths, key=lambda p: p.stem[:4]):
        records = {"stored": [], "S": []}
        for path in files:
            for row in read_rows(path):
                if str(row["bsns_year"]) != year or row["available_date"] != dart_quarterly._filing_date(row["rcept_no"]):
                    raise ValueError("quarterly archive year/receipt availability mismatch")
                if row["available_date"] >= day:
                    continue
                records["stored"].append(row)
                info = dates.annotate(row)
                counts[info["date_reason"]] += 1
                actual = info["actual_public_date"]
                if actual is not None and actual < day:
                    records["S"].append({**row, "available_date": actual})
                    if info["is_correction"]:
                        corrections.add(row["rcept_no"])
        for rule in values:
            latest = {}
            for row in dart_quarterly._derive(records[rule]):
                key = row["corp_code"], row["fiscal_quarter"]
                rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
                if key not in latest or rank > latest[key][0]:
                    latest[key] = rank, row
            for key in sorted(latest):
                row = latest[key][1]
                values[rule].extend(tuple(stock if f == "stock_code" else row[f] for f in dart_quarterly.COLUMNS)
                                    for stock in row["stock_codes"])
        del records, latest
    frames = {}
    for rule, rows in values.items():
        rows.sort(key=lambda row: row[1:3])
        frame = pd.DataFrame(rows, columns=dart_quarterly.COLUMNS)
        for metric in dart_quarterly.ACCOUNTS:
            frame[metric] = pd.to_numeric(frame[metric]).astype(float)
        frame.attrs["correction_receipts"] = sorted(corrections) if rule == "S" else []
        frames[rule] = frame
    return frames, {"rule": "S", "L": dates.L, "date_reasons": dict(counts)}


def refresh_status(data_dir):
    root = Path(data_dir)
    state_path = root / "fundamentals/dart_quarterly/_state.json"
    batches_path = root / "fundamentals/dart_quarterly/_batch_status.json"
    refresh_path = root / "fundamentals/_refresh_last_run.json"
    state = load_json(state_path) if state_path.is_file() else None
    batches = load_json(batches_path) if batches_path.is_file() else {}
    completed = set(state["completed_batches"]) if state is not None else set()
    return {"batches": {key: batches[key] if key in batches else {
                "last_success_at": None, "pending_targets": None,
                "reason": "legacy_checkpoint_has_no_batch_timestamp_or_target_count"}
            for key in sorted(completed | set(batches))},
            "completed_batches": len(completed) if state is not None else None,
            "checkpoint_status": "available" if state is not None else "unavailable",
            "refresh": load_json(refresh_path) if refresh_path.is_file() else None,
            "used_archive_versions": hashes(root, sorted((root / "fundamentals/dart_quarterly").glob("*_110*.jsonl")))}


def serial_universe(u):
    fields = ("phase", "avg_trading_value_20d", "corp_code", "fiscal_quarter", "available_date",
              "rcept_no", "fs_div", "revenue_yoy", "operating_income_yoy", "score")
    return [{"stock_code": code, **{key: None if pd.isna(row[key]) else
             int(row[key]) if key == "phase" else float(row[key]) if isinstance(row[key], (float, np.floating))
             else row[key] for key in fields}} for code, row in u.sort_index().iterrows()]


def prior_execution(data_dir, month, multiplier):
    previous = pd.Period(month, freq="M") - 1
    path = path_for(data_dir, "executions", str(previous), multiplier)
    if not path.is_file():
        raise ValueError(f"FW001 av_all state unavailable: missing execution {previous} x{multiplier}")
    record = effective_execution(data_dir, str(previous), multiplier)
    if record["av_all"]["status"] != "executed":
        raise ValueError("FW001 av_all state unavailable: predecessor cost/state is blocked")
    return record, digest(record)


def effective_execution(data_dir, month, multiplier):
    original = load_json(path_for(data_dir, "executions", month, multiplier))
    if original["label"] == "forward" and completion_proof(data_dir, "executions", month, multiplier) is None:
        raise ValueError("FW001 execution is not finalised: completion proof missing")
    resolution = path_for(data_dir, "cost_resolutions", month, multiplier)
    if resolution.is_file():
        record = load_json(resolution)
        if record["resolution_of"] != digest(original):
            raise ValueError("FW001 cost resolution belongs to a different sealed execution")
        return record
    return original


def carried(data_dir, month, day, sessions):
    if pd.Period(month, freq="M") == MONTHS[0]:
        return {str(m): hc003.PortfolioState().to_dict() for m in hf001.MULTIPLIERS}, None, None, {}
    previous, references = {}, {}
    for m in hf001.MULTIPLIERS:
        previous[str(m)], references[str(m)] = prior_execution(data_dir, month, m)
    first = previous["1.0"]
    chain = first["chain"]
    states = {m: row["av_all"]["state_after"] for m, row in previous.items()}
    if not chain and all(not state["quantities"] for state in states.values()):
        return states, None, pd.Timestamp(first["chain_date"]), references
    days = sessions[(sessions > pd.Timestamp(first["chain_date"])) & (sessions <= day)]
    if len(days):
        chain = book_from_days(data_dir, days, chain=chain).chain_state
    return states, chain, pd.Timestamp(first["chain_date"]), references


def decide(decision_date, data_dir, *, execute=False, rehearsal=False, append_rerun=False):
    verify_manifest()
    day = pd.Timestamp(decision_date)
    if day.to_period("M") not in MONTHS and not rehearsal:
        raise ValueError("FW001 decision outside the twelve registered months")
    if rehearsal and execute:
        raise ValueError("FW001 historical rehearsal is strictly read-only")
    sessions = calendar(day)
    planned = plan(day, sessions=sessions)
    path = path_for(data_dir, "decisions", planned["month"])
    lock = file_lock(Path(data_dir) / RECORD_DIR / ".record.lock") if execute else nullcontext()
    listing_lock = file_lock(Path(data_dir) / "disclosures/dart_full/.backfill.lock") if execute else nullcontext()
    financial_lock = file_lock(Path(data_dir) / "fundamentals/dart_quarterly/.backfill.lock") if execute else nullcontext()
    with lock, listing_lock, financial_lock:
        if path.is_file() and not append_rerun:
            record = load_json(path)
            if completion_proof(data_dir, "decisions", planned["month"]) is not None:
                return record
            if execute and not marker_path(data_dir, "decisions", planned["month"]).is_file():
                return complete(data_dir, "decisions", record, execute=True)
            return {**record, "label": "reconstructed"}
        stamp = now()
        if stamp.date() < day.date() or stamp.date() == day.date() and stamp.time() < time(16):
            raise ValueError("FW001 decision requires decision-day close after 16:00 KST")
        pos = sessions.get_loc(day)
        days = sessions[pos - 59:pos + 1]
        input_days = days
        if not rehearsal and day.to_period("M") != MONTHS[0]:
            try:
                previous, _ = prior_execution(data_dir, planned["month"], 1.0)
                input_days = days.union(sessions[(sessions > pd.Timestamp(previous["chain_date"])) & (sessions <= day)])
            except ValueError:
                pass  # carried() records the unavailable predecessor explicitly below.
        sources = input_paths(data_dir, input_days)
        before = hashes(Path(data_dir), sources)
        status = dart_listing.listing_status(data_dir)
        coverage = hf001.listing_coverage(day, sessions, {}, data_dir=data_dir, status=status)
        if not coverage["covered"]:
            raise ValueError("FW001 incomplete merged 126-session listing window: " + encoded(coverage))
        frames, date_report = financial_frames(data_dir, day)
        verified_day(data_dir, day)
        prices = price_window(data_dir, days)
        if len(sessions[pos - 19:pos + 1].difference(common._sessions(prices))):
            raise ValueError("FW001 incomplete decision ADV sessions")
        universes, reports = {}, {}
        for name, rule in (("f_both", "stored"), ("av_all", "S")):
            loader = lambda as_of, *, data_dir, rule=rule: frames[rule]
            universes[name], reports[name] = hc002.universe_on_decision(prices, day, data_dir=data_dir,
                                                                      financial_loader=loader)
        events, event_counts = hf001.rights_events(hf001.load_listings(
            {pd.Timestamp(d): p for d, p in dart_listing.listing_paths(
                data_dir, coverage["receipt_start"], coverage["receipt_end"]).items()}, data_dir=data_dir), sessions)
        raised = set(events.loc[events.known_position.between(pos - 125, pos), "stock_code"])
        rules = {}
        for name, u in universes.items():
            vol = hf001.volatility_filter(prices, u, day, sessions) if len(u) else pd.DataFrame()
            f_vol = set(vol.index[vol.f_vol]) if len(vol) else set()
            if name == "f_both":
                p = set(u.index[u.phase.eq(2)])
                targets = p - f_vol - raised
                base, _ = hc002.universe_on_decision(prices, day, data_dir=data_dir,
                                                    financial_loader=lambda *a, **k: frames["stored"])
                if p != set(base.index[base.phase.eq(2)]):
                    raise AssertionError("FW001 HF001 P differs from HC002")
                extra = {"unfiltered_targets": sorted(p), "hf001_accounting_both_baskets": True}
            else:
                targets = av001.target_set("av_all", {"universe": u, "F_vol": f_vol,
                                                     "F_raise": raised & set(u.index)}, {})
                extra = {"financial_rule": "S"}
            rules[name] = {"universe": serial_universe(u), "targets": sorted(targets),
                "weights": {code: 1 / len(targets) for code in sorted(targets)},
                "F_vol": sorted(f_vol), "F_raise": sorted(raised & set(u.index)),
                "vol_uncomputable": int(vol.volatility_60d.isna().sum()) if len(vol) else 0,
                "universe_report": reports[name], **extra}
        state_error = None
        try:
            states, chain, _, references = carried(data_dir, planned["month"], day, sessions) if not rehearsal else (
                {str(m): hc003.PortfolioState().to_dict() for m in hf001.MULTIPLIERS}, None, None, {})
            if chain is None:
                chain = book_from_days(data_dir, days[-20:]).chain_state
            marks = {code: row["adjusted_close"] for code, row in chain.items()}
            state_values = {m: {**state, "value": hc003.portfolio_value(hc003.PortfolioState.from_dict(state), marks)}
                            for m, state in states.items()}
        except ValueError as error:
            state_error = str(error)
            state_values, chain, references = None, None, {}
        refresh = refresh_status(data_dir)
        snapshots = {name: snapshot_file(data_dir, Path(data_dir) / name, checksum, execute=execute)
                     for name, checksum in before.items()}
        if sources != input_paths(data_dir, input_days) or before != hashes(Path(data_dir), sources):
            raise ValueError("FW001 input changed during decision; retry before the deadline")
        record = {"experiment_id": EXPERIMENT_ID, "record_type": "decision", **planned,
            "preregistration_commit": PREREGISTRATION_COMMIT, "rules": rules,
            "input_hashes": before, "input_snapshots": snapshots, "code_hashes": code_hashes(), "listing": coverage,
            "listing_version": status["listing_version"], "financial_dates": date_report,
            "financial_refresh": refresh, "raise_events": event_counts,
            "calendar_version": trading_calendar.CALENDAR_VERSION,
            "missing_price_sessions": [str(d.date()) for d in days
                                       if not krx_market._path(d.date(), data_dir).is_file()],
            "states": state_values, "chain": chain, "chain_date": day.date().isoformat(),
            "predecessor_hashes": references, "state_error": state_error}
        if rehearsal:
            return {**record, "label": "reconstructed", "finalised_at": now().isoformat(), "rehearsal": True}
        if append_rerun:
            record.update(record_type="rerun", label="reconstructed", finalised_at=now().isoformat())
            return put(Path(data_dir) / RECORD_DIR / "reruns" / (digest(record) + ".json"), record, execute=execute)
        record = put(path, record, execute=execute, deadline=datetime.fromisoformat(planned["deadline"]))
        return complete(data_dir, "decisions", record, execute=execute)


def entry_slots(decision, rows, multiplier):
    day = pd.Timestamp(decision["trade_date"])
    raw = {r["stock_code"]: r for r in rows}
    slots = {}
    for selected in decision["rules"]["f_both"]["universe"]:
        code, reason = selected["stock_code"], None
        row = raw.get(selected["stock_code"])
        if row is None or row.get("open") is None:
            reason = "missing_entry_price"
        elif not np.isfinite(float(row["open"])):
            reason = "missing_entry_price"
        elif (float(row["open"]) <= 0 or row.get("volume") is None
              or not np.isfinite(float(row["volume"])) or float(row["volume"]) <= 0):
            reason = "untradable_entry"
        else:
            base = row.get("base_price")
            if base is None or not np.isfinite(float(base)):
                base = (decision["chain"] or {}).get(code, {}).get("raw_close")
            if base is None or not np.isfinite(float(base)) or float(base) <= 0:
                raise ValueError("HF001 entry price-limit reference is missing")
            if float(row["open"]) >= float(base) * (1 + hf001.price_limit(day) - 0.005):
                reason = "limit_up_entry"
        opening = (float(row["open"]) if row is not None and row.get("open") is not None
                   and np.isfinite(float(row["open"])) else None)
        cost = None
        error = None
        if reason is None:
            try:
                cost = cost_model.round_trip_cost(opening, row["market"], day.date(),
                    selected["avg_trading_value_20d"], multiplier=multiplier, model_version="v1")
            except ValueError as exc:
                if "unknown tax year" not in str(exc):
                    raise
                error = str(exc)
        else:
            cost = 0.0
        slots[code] = {"raw_open": opening, "filled": reason is None, "reason": reason,
                       "round_trip_cost": cost, "cost_error": error,
                       "market": row["market"] if row is not None else None,
                       "adv": selected["avg_trading_value_20d"]}
    return slots


def execution(decision, data_dir, *, multiplier, execute=False, rehearsal=False):
    verify_manifest()
    if decision["trade_date"] is None:
        raise ValueError("FW001 execution blocked: calendar_unverified")
    if decision["record_type"] == "rerun":
        raise ValueError("FW001 reruns cannot create observation executions")
    if rehearsal and execute:
        raise ValueError("FW001 rehearsal execution is strictly read-only")
    if multiplier not in hf001.MULTIPLIERS:
        raise ValueError("FW001 permits only cost scenarios 1x, 1.5x and 2x")
    multiplier = float(multiplier)
    lock = file_lock(Path(data_dir) / RECORD_DIR / ".record.lock") if execute else nullcontext()
    with lock:
        if decision["label"] != "forward" and not rehearsal:
            decision = missing_record(decision["decision_date"], data_dir, execute=execute)
        return _execution(decision, data_dir, multiplier=multiplier, execute=execute, rehearsal=rehearsal)


def _execution(decision, data_dir, *, multiplier, execute=False, rehearsal=False):
    if decision["record_type"] == "rerun":
        raise ValueError("FW001 reruns cannot create observation executions")
    if execute and decision["label"] == "forward":
        canonical = path_for(data_dir, "decisions", decision["month"])
        if (not canonical.is_file() or load_json(canonical) != decision
                or completion_proof(data_dir, "decisions", decision["month"]) is None):
            raise ValueError("FW001 observation execution requires an atomically finalised decision")
    path = path_for(data_dir, "executions", decision["month"], multiplier)
    if path.is_file():
        record = load_json(path)
        if record["decision_hash"] != digest(decision):
            raise ValueError("FW001 immutable execution belongs to a different decision")
        complete(data_dir, "executions", record, multiplier=multiplier, execute=execute, decision=decision)
        return resolve_costs(decision, data_dir, multiplier=multiplier, execute=execute)
    if rehearsal and execute:
        raise ValueError("FW001 rehearsal execution is strictly read-only")
    entry = pd.Timestamp(decision["trade_date"])
    trading_calendar.daily_session_close(entry.date().isoformat())
    if decision["chain"] is not None and decision["chain_date"] != decision["decision_date"]:
        raise ValueError("FW001 execution requires a sealed decision-close chain; holdings are retained without valuation")
    price_descriptor = path_for(data_dir, "execution_prices", entry.strftime("%Y%m%d"))
    if now().date() < entry.date():
        raise ValueError("FW001 execution cannot precede its planned entry session")
    if (decision["label"] == "missing" and decision["states"] is not None
            and not any(state["quantities"] for state in decision["states"].values())
            and not price_descriptor.is_file()
            and not krx_market._path(entry.date(), data_dir).is_file()):
        initial = hc003.PortfolioState.from_dict(decision["states"][str(multiplier)])
        following, report = hc003.rebalance(initial, {}, set(), entry, multiplier=multiplier, no_trade=True)
        record = {"experiment_id": EXPERIMENT_ID, "record_type": "execution", "month": decision["month"],
            "trade_date": decision["trade_date"], "decision_hash": digest(decision), "label": "missing",
            "recorded_at": now().isoformat(), "cost_multiplier": multiplier, "input_hashes": {},
            "code_hashes": code_hashes(), "f_both": {"slots": {}, "baskets": {}},
            "av_all": {"status": "executed", "state_before": initial.to_dict(), "state_after": following.to_dict(),
                "value": report["post_value"], "accounting": report, "no_trade": True}, "quotes": {},
            "chain": decision["chain"] or {}, "chain_date": decision["chain_date"],
            "price_status": "unavailable_no_owned_assets_cash_value_known"}
        return put(path, record, execute=execute)
    if not price_descriptor.is_file() and execute:
        frozen = freeze_price(data_dir, entry, execute=True)
        put(price_descriptor, frozen, execute=True)
    descriptors = None
    if price_descriptor.is_file():
        descriptors = {entry.strftime("%Y%m%d"): load_json(price_descriptor)}
        frozen_frame = recorded_price_window(data_dir, pd.DatetimeIndex([entry]), descriptors=descriptors)
        rows = frozen_frame.reset_index().to_dict("records")
        if ({row["market"] for row in rows} != {"KOSPI", "KOSDAQ"}
                or any(row["calendar_status"] != "verified" for row in rows)):
            raise ValueError("FW001 sealed entry snapshot is not complete verified KOSPI/KOSDAQ data")
    else:
        rows = verified_day(data_dir, entry)
    sources = [krx_market._path(entry.date(), data_dir)] if not price_descriptor.is_file() else [
        price_descriptor, Path(data_dir) / RECORD_DIR / "snapshots" / (
            load_json(price_descriptor)["sha256"] + ".jsonl")]
    before = hashes(Path(data_dir), sources)
    states, chain = decision["states"], decision["chain"]
    if chain is None:
        if states is not None and any(state["quantities"] for state in states.values()):
            raise ValueError("FW001 owned positions require their sealed adjusted-price chain")
        book = book_from_days(data_dir, pd.DatetimeIndex([entry]), entry=entry, price_loader=(
            lambda root, window: recorded_price_window(root, window, descriptors=descriptors)) if descriptors else None)
    else:
        book = book_from_days(data_dir, pd.DatetimeIndex([entry]), entry=entry, chain=chain, price_loader=(
            lambda root, window: recorded_price_window(root, window, descriptors=descriptors)) if descriptors else None)
    quotes = book.quotes(entry)
    slots = entry_slots(decision, rows, multiplier)
    valid = decision["label"] == "forward" and not rehearsal
    baskets = {}
    if valid or rehearsal:
        rule = decision["rules"]["f_both"]
        for name, codes in (("filtered", rule["targets"]), ("unfiltered", rule["unfiltered_targets"])):
            weight = 1 / len(codes) if codes else 0.0
            baskets[name] = {"weights": {code: weight for code in codes},
                "raw_quantities": {code: weight / slots[code]["raw_open"] for code in codes if slots[code]["filled"]},
                "cash": sum(weight for code in codes if not slots[code]["filled"]) if codes else 1.0,
                "cost": sum(weight * slots[code]["round_trip_cost"] for code in codes)
                        if all(slots[code]["round_trip_cost"] is not None for code in codes) else None}
    av = {"status": "insufficient", "reason": decision["state_error"], "state_after": None}
    if states is not None:
        initial = hc003.PortfolioState.from_dict(states[str(multiplier)])
        try:
            following, report = hc003.rebalance(initial, quotes, decision["rules"]["av_all"]["targets"],
                entry, model_version="v2", multiplier=multiplier, no_trade=not (valid or rehearsal))
            for trade in report["trades"]:
                code = trade["stock_code"]
                trade.update(adjusted_quantity=trade["amount"] / quotes[code].mark,
                             raw_quantity=trade["amount"] / quotes[code].raw_open)
            av = {"status": "executed", "state_before": initial.to_dict(), "state_after": following.to_dict(),
                  "value": report["post_value"], "accounting": report, "no_trade": not (valid or rehearsal)}
        except ValueError as exc:
            if "unknown tax year" not in str(exc):
                raise
            av = {"status": "cost_evaluation_blocked", "reason": str(exc),
                  "state_before": initial.to_dict(), "state_after": None}
    if before != hashes(Path(data_dir), sources):
        raise ValueError("FW001 execution prices changed before publication")
    quote_rows = {code: {key: getattr(q, key) if key not in ("raw_open", "adv") or np.isfinite(getattr(q, key))
                              else None for key in ("mark", "raw_open", "market", "adv", "tradable", "limit_up", "limit_down")}
                  for code, q in quotes.items()}
    record = {"experiment_id": EXPERIMENT_ID, "record_type": "execution", "month": decision["month"],
        "trade_date": decision["trade_date"], "decision_hash": digest(decision), "label": decision["label"],
        "recorded_at": now().isoformat(), "cost_multiplier": multiplier, "input_hashes": before,
        "code_hashes": code_hashes(), "f_both": {"slots": slots, "baskets": baskets}, "av_all": av,
        "quotes": quote_rows, "chain": book.chain_state, "chain_date": decision["trade_date"]}
    record = put(path, record, execute=execute)
    return complete(data_dir, "executions", record, multiplier=multiplier, execute=execute, decision=decision)


def resolve_costs(decision, data_dir, *, multiplier, execute=False):
    """Only undefined costs/fills may be completed, in a new immutable object.

    Already sealed av_all quantities/cash and known HF001 charges stay intact.
    All price, date, target and ADV inputs come from the sealed execution.
    """
    record = effective_execution(data_dir, decision["month"], multiplier)
    blocked_slots = any(slot["round_trip_cost"] is None for slot in record["f_both"]["slots"].values())
    blocked_av = record["av_all"]["status"] == "cost_evaluation_blocked"
    if not blocked_slots and not blocked_av:
        return record
    try:
        require_2027_costs()
    except ValueError:
        return record  # The explicit pending-cost record remains blocked.
    original_hash = digest(record)
    record = json.loads(encoded(record))
    for slot in record["f_both"]["slots"].values():
        if slot["round_trip_cost"] is None:
            slot["round_trip_cost"] = cost_model.round_trip_cost(slot["raw_open"], slot["market"],
                record["trade_date"], slot["adv"], multiplier=multiplier, model_version="v1")
            slot["cost_error"] = None
    for basket in record["f_both"]["baskets"].values():
        if basket["cost"] is None:
            basket["cost"] = sum(weight * record["f_both"]["slots"][code]["round_trip_cost"]
                                 for code, weight in basket["weights"].items())
    if blocked_av:
        quote_map = {code: hc003.Quote(**{**q, "raw_open": np.nan if q["raw_open"] is None else q["raw_open"],
            "adv": np.nan if q["adv"] is None else q["adv"]}) for code, q in record["quotes"].items()}
        state, report = hc003.rebalance(hc003.PortfolioState.from_dict(record["av_all"]["state_before"]),
            quote_map, decision["rules"]["av_all"]["targets"], record["trade_date"], multiplier=multiplier,
            no_trade=record["label"] != "forward")
        for trade in report["trades"]:
            quote = quote_map[trade["stock_code"]]
            trade.update(adjusted_quantity=trade["amount"] / quote.mark,
                         raw_quantity=trade["amount"] / quote.raw_open)
        record["av_all"] = {"status": "executed", "state_before": record["av_all"]["state_before"],
            "state_after": state.to_dict(), "value": report["post_value"], "accounting": report,
            "no_trade": record["label"] != "forward"}
    record.update(record_type="cost_resolution", resolution_of=original_hash, recorded_at=now().isoformat(),
                  code_hashes=code_hashes())
    return put(path_for(data_dir, "cost_resolutions", decision["month"], multiplier), record, execute=execute)


def require_2027_costs():
    """Fail until a legally sourced tax-table amendment supports 2027 settlement."""
    try:
        cost_model.one_way_cost(10_000, "KOSPI", "20270104", 2e9, "sell", model_version="v2")
    except ValueError as error:
        raise ValueError("FW001 2027 cost evaluation blocked: add a legally sourced sell/settlement rate; "
                         "see TODO in backtesting/cost_model.py") from error


def seal_evaluation_inputs(data_dir, *, execute=False):
    verify_manifest()
    if now() < FREEZE_AT:
        raise ValueError("FW001 final data freeze is 2027-11-15 00:00 KST")
    path = Path(data_dir) / RECORD_DIR / "evaluation_freeze.json"
    if path.is_file():
        return load_json(path)
    root = Path(data_dir)
    paths = []
    selected_prices = {}
    cutoff_ns = int(FREEZE_AT.timestamp()) * 1_000_000_000
    for source in sorted((root / "market/krx_daily").glob("*/*.jsonl")):
        if not "20261030" <= source.stem < FREEZE_AT.strftime("%Y%m%d"):
            continue
        stat = source.stat()
        if max(stat.st_mtime_ns, stat.st_ctime_ns) > cutoff_ns:
            raise ValueError("FW001 exact data freeze unavailable: server price published after cutoff: " + source.name)
        selected_prices[source.stem] = freeze_price(root, pd.Timestamp(source.stem), execute=execute)
    hc_path = root / "forward/hegemony_shadow/evaluation.json"
    if hc_path.is_file():
        hc = load_json(hc_path)
        if datetime.fromisoformat(hc["evaluated_at"]) <= FREEZE_AT:
            secondary = root / RECORD_DIR / "secondary_hc002.json"
            put(secondary, {"source_sha256": dart_listing.file_hash(hc_path), "evaluation": hc}, execute=execute)
            if secondary.is_file():
                paths.append(secondary)
    for folder in ("decisions", "executions", "cost_resolutions", "missing", "execution_prices", "sealed"):
        for record_path in sorted((root / RECORD_DIR / folder).rglob("*.json")):
            record = load_json(record_path)
            recorded = ((publication_time(record_path) + PUBLICATION_PRECISION).isoformat() if folder == "sealed" else
                        record.get("finalised_at", record.get("recorded_at")))
            if recorded is not None and pd.Timestamp(recorded) <= pd.Timestamp(FREEZE_AT):
                paths.append(record_path)
                if folder == "decisions":
                    verify_input_snapshots(root, record)
                    paths.extend(root / item["blob"] for item in record["input_snapshots"].values())
                if folder == "execution_prices":
                    blob = root / RECORD_DIR / "snapshots" / (record["sha256"] + ".jsonl")
                    if dart_listing.file_hash(blob) != record["sha256"]:
                        raise ValueError("FW001 missing/corrupt server frozen price blob")
                    paths.append(blob)
    for descriptor in selected_prices.values():
        blob = root / RECORD_DIR / "snapshots" / (descriptor["sha256"] + ".jsonl")
        if execute and dart_listing.file_hash(blob) != descriptor["sha256"]:
            raise ValueError("FW001 frozen evaluation blob missing or corrupt")
        if blob.is_file():
            paths.append(blob)
    return put(path, {"freeze_at": FREEZE_AT.isoformat(), "selected_prices": selected_prices,
                     "input_hashes": hashes(root, sorted(set(paths)))},
               execute=execute)


def missed_decision(day, data_dir):
    planned = plan(day)
    sessions = calendar(day)
    chain_date = str(pd.Timestamp(day).date())
    try:
        states, chain, _, references = carried(data_dir, planned["month"], pd.Timestamp(day), sessions)
        error = None
    except ValueError as exc:
        error = str(exc)
        states, chain, references = None, None, {}
        try:
            previous = {str(m): prior_execution(data_dir, planned["month"], m) for m in hf001.MULTIPLIERS}
            states = {m: item[0]["av_all"]["state_after"] for m, item in previous.items()}
            chain, chain_date = previous["1.0"][0]["chain"], previous["1.0"][0]["chain_date"]
            references = {m: item[1] for m, item in previous.items()}
        except ValueError:
            pass  # Actual lost state stays unavailable; known holdings above are retained.
    if states is not None and chain is None:
        try:
            pos = sessions.get_loc(pd.Timestamp(day))
            chain = book_from_days(data_dir, sessions[pos - 19:pos + 1]).chain_state
        except ValueError as exc:
            # Cash is known even if its unowned price history is unavailable.
            error = str(exc)
    return {"experiment_id": EXPERIMENT_ID, "record_type": "missing", "label": "missing", **planned,
            "states": states, "chain": chain, "chain_date": chain_date, "state_error": error,
            "predecessor_hashes": references, "rules": {"f_both": {"universe": [], "targets": [],
            "unfiltered_targets": []}, "av_all": {"universe": [], "targets": []}}}


def missing_record(day, data_dir, *, execute=False):
    path = path_for(data_dir, "missing", str(pd.Timestamp(day).to_period("M")))
    if path.is_file():
        return load_json(path)
    return put(path, {**missed_decision(day, data_dir), "reason": "not_finalised_before_08:59_KST",
                      "recorded_at": now().isoformat()}, execute=execute)


def calendar_omission(month, data_dir, reason, *, execute=False):
    """Keep the last known holdings without inventing unverified session dates."""
    path = path_for(data_dir, "missing", str(month))
    if path.is_file():
        return load_json(path)
    states, chain, chain_date, references = None, None, None, {}
    previous = str(month - 1)
    try:
        records = {str(m): prior_execution(data_dir, str(month), m) for m in hf001.MULTIPLIERS}
        states = {m: item[0]["av_all"]["state_after"] for m, item in records.items()}
        chain, chain_date = records["1.0"][0]["chain"], records["1.0"][0]["chain_date"]
        references = {m: item[1] for m, item in records.items()}
    except ValueError:
        previous_path = path_for(data_dir, "missing", previous)
        if previous_path.is_file():
            original = load_json(previous_path)
            states, chain, chain_date = original["states"], original["chain"], original["chain_date"]
            references = {"missing": digest(original)}
    return put(path, {"experiment_id": EXPERIMENT_ID, "record_type": "missing", "label": "missing",
        "month": str(month), "decision_date": None, "trade_date": None, "deadline": None, "f_both_exit": None,
        "reason": reason, "calendar_status": "unverified", "recorded_at": now().isoformat(),
        "states": states, "chain": chain, "chain_date": chain_date, "state_error": reason,
        "predecessor_hashes": references, "rules": {"f_both": {"universe": [], "targets": [],
        "unfiltered_targets": []}, "av_all": {"universe": [], "targets": []}}}, execute=execute)


def freeze_price(data_dir, day, *, execute=False):
    lock = file_lock(Path(data_dir) / RECORD_DIR / ".prices.lock") if execute else nullcontext()
    with lock:
        return _freeze_price(data_dir, day, execute=execute)


def _freeze_price(data_dir, day, *, execute=False):
    """Preserve revisions; the final freeze selects the latest server copy."""
    path = path_for(data_dir, "prices", pd.Timestamp(day).strftime("%Y%m%d"))
    source = krx_market._path(pd.Timestamp(day).date(), data_dir)
    stat = source.stat()
    signature = [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    versions = Path(data_dir) / RECORD_DIR / "price_versions" / pd.Timestamp(day).strftime("%Y%m%d")
    latest = sorted(versions.glob("*.json"))
    if latest:
        previous = load_json(latest[-1])
        if previous["source_signature"] == signature:
            return previous
    verified_day(data_dir, day)
    checksum = dart_listing.file_hash(source)
    snapshot_file(data_dir, source, checksum, execute=execute)
    after = source.stat()
    if [after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns] != signature:
        raise ValueError("FW001 source changed during price snapshot")
    record = {"day": str(pd.Timestamp(day).date()), "path": source.relative_to(data_dir).as_posix(),
              "sha256": checksum, "recorded_at": now().isoformat(), "source_signature": signature}
    published = max(stat.st_mtime_ns, stat.st_ctime_ns)
    put(versions / (f"{published:020d}_{checksum}.json"), record, execute=execute)
    if not path.exists():
        put(path, record, execute=execute)
    return record


def daily(data_dir, *, execute=False):
    """Resume immutable executions first, then current decision; count omissions.

    No grace/backfill option exists. A failure remains visible and is retried by
    the next collector-completion trigger; it never substitutes a portfolio.
    """
    verify_manifest()
    root = Path(data_dir)
    stamp = now()
    report = {"status": "ok" if execute else "dry_run", "decisions": [], "executions": [], "missing": []}
    lock = file_lock(root / RECORD_DIR / ".daily.lock") if execute else nullcontext()
    with lock:
        for month in MONTHS:
            if month > pd.Period(stamp.date(), freq="M"):
                break
            try:
                monthly = trading_calendar.session_calendar(month.start_time, month.end_time.normalize())
                day = monthly[-1]
                if day.date() > stamp.date() or day.date() == stamp.date() and stamp.time() < time(16):
                    break
                planned = plan(day)
            except ValueError as exc:
                if not str(exc).startswith("calendar_unverified:"):
                    raise
                report["status"] = "waiting"
                report["decisions"].append({"month": str(month), "reason": str(exc)})
                if month < pd.Period(stamp.date(), freq="M"):
                    with file_lock(root / RECORD_DIR / ".record.lock") if execute else nullcontext():
                        calendar_omission(month, root, str(exc), execute=execute)
                    report["missing"].append(str(month))
                continue
            decision_path = path_for(root, "decisions", str(month))
            decision = (load_json(decision_path) if completion_proof(root, "decisions", str(month)) is not None
                        else None)
            if (decision is None and stamp < datetime.fromisoformat(planned["deadline"])
                    and not marker_path(root, "decisions", str(month)).is_file()):
                try:
                    decision = decide(day, root, execute=execute)
                    report["decisions"].append({"month": str(month), "label": decision["label"],
                                               "state_error": decision["state_error"]})
                    if execute and completion_proof(root, "decisions", str(month)) is None:
                        decision = None
                except (ValueError, OSError) as exc:
                    report["status"] = "waiting"
                    report["decisions"].append({"month": str(month), "reason": str(exc)})
            if now() >= datetime.fromisoformat(planned["deadline"]) and (
                    decision is None or decision["label"] != "forward"):
                with file_lock(root / RECORD_DIR / ".record.lock") if execute else nullcontext():
                    # A concurrent writer may have completed while we waited.
                    if completion_proof(root, "decisions", str(month)) is not None:
                        decision = load_json(decision_path)
                    else:
                        if decision_path.is_file() and not marker_path(root, "decisions", str(month)).is_file():
                            complete(root, "decisions", load_json(decision_path), execute=execute)
                        decision = missing_record(day, root, execute=execute)
                        report["missing"].append(str(month))
            if decision is not None:
                entry_path = krx_market._path(pd.Timestamp(planned["trade_date"]).date(), root)
                sealed_entry = path_for(root, "execution_prices", pd.Timestamp(planned["trade_date"]).strftime("%Y%m%d"))
                if sealed_entry.is_file() or entry_path.is_file() or (decision["label"] == "missing" and decision["states"] is not None
                        and all(not state["quantities"] for state in decision["states"].values())):
                    for m in hf001.MULTIPLIERS:
                        record = execution(decision, root, multiplier=m, execute=execute)
                        report["executions"].append({"month": str(month), "multiplier": m,
                                                   "av_all": record["av_all"]["status"]})
        # Recovery above uses only sealed entry inputs. Entry revisions are
        # captured at the final freeze, independently of fill recovery.
        if stamp < FREEZE_AT:
            for p in sorted((root / "market/krx_daily").glob("*/*.jsonl")):
                if "20261030" <= p.stem <= min(stamp.strftime("%Y%m%d"), trading_calendar.CALENDAR_VERIFIED_THROUGH.replace("-", "")):
                    if not path_for(root, "execution_prices", p.stem).is_file():
                        freeze_price(root, pd.Timestamp(p.stem), execute=execute)
        elif execute:
            seal_evaluation_inputs(root, execute=True)
        report["missing_months"] = len(report["missing"])
    return report
