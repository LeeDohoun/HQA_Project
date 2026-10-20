"""Immutable fnlttMultiAcnt responses and filing-exact balance-sheet coverage.

No environment loading occurs here. Callers must explicitly authorize execution.
The endpoint cannot select a receipt: old versions no longer returned by DART
cannot be reconstructed. S/O dates belong to FilingDates, not the fetch time.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

import requests

from src.ingestion import dart_quarterly as quarterly
from src.ingestion.dart_poller import KST, _kst
from src.ingestion.storage import atomic_write, file_lock


DEFAULT_DATA_DIR = quarterly.DEFAULT_DATA_DIR
DEFAULT_CORP_CODES = quarterly.DEFAULT_CORP_CODES
DEFAULT_MAX_REQUESTS = 500
DEFAULT_DAILY_CAP = 500
VERSION_FIELDS = ("corp_code", "bsns_year", "reprt_code", "fs_div", "rcept_no")
METRICS = {"자산총계": "total_assets", "부채총계": "total_liabilities", "자본총계": "total_equity"}


@dataclass(frozen=True)
class Job:
    year: int
    report: str
    corps: tuple[str, ...]

    @property
    def key(self):
        digest = hashlib.sha256(",".join(self.corps).encode()).hexdigest()
        return f"{self.year}_{self.report}_{digest}"

    @property
    def params(self):
        # The key is deliberately omitted from persisted request parameters.
        return {"corp_code": ",".join(self.corps), "bsns_year": str(self.year), "reprt_code": self.report}


def _root(data_dir):
    return Path(data_dir) / "fundamentals/dart_multi_raw"


def _json(path):
    return json.loads(path.read_bytes())


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()


def _immutable(path, body):
    """Atomically install complete bytes without ever replacing an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != body:
                raise ValueError(f"immutable archive conflict: {path.name}") from None
    finally:
        os.unlink(temporary)


def _payload(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise ValueError("DART response is not JSON") from None
    if (not isinstance(value, dict) or not isinstance(value.get("status"), str)
            or not re.fullmatch(r"[0-9]{3}", value["status"])):
        raise ValueError("DART response has no valid status")
    return value


def _complete(http_status, payload):
    if not 200 <= http_status < 300:
        return False
    if payload["status"] == "013":
        return payload.get("list") in (None, [])
    rows = payload.get("list")
    return (payload["status"] == "000" and isinstance(rows, list) and bool(rows)
            and all(isinstance(row, dict) for row in rows))


def _stored(directory, job):
    path = directory / "requests" / f"{job.key}.json"
    if not path.exists():
        return None
    record = _json(path)
    if (record.get("request_key") != job.key or record.get("params") != job.params
            or record.get("endpoint") != quarterly.URL
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))):
        raise ValueError("invalid immutable request metadata")
    body = (directory / "blobs" / f"{record['sha256']}.json").read_bytes()
    if hashlib.sha256(body).hexdigest() != record["sha256"]:
        raise ValueError("raw response sha256 mismatch")
    payload = _payload(body)
    if not _complete(record["http_status"], payload) or record["dart_status"] != payload["status"]:
        raise ValueError("invalid completed response")
    _kst(datetime.fromisoformat(record["fetched_at"]))
    return record, payload


def store_response(directory, job, body, http_status, fetched_at):
    """Save exact response bytes, including provider/HTTP failures for audit.

    Only a complete 000/013 response claims the immutable request key. Failed
    attempts have independent immutable IDs so maintenance can later be retried.
    Never overwrite a completed key, even if the new body is also valid JSON.
    """
    directory = Path(directory)
    if type(http_status) is not int:
        raise ValueError("HTTP status must be an integer")
    checksum = hashlib.sha256(body).hexdigest()
    previous = _stored(directory, job)
    if previous:
        if previous[0]["sha256"] != checksum or previous[0]["http_status"] != http_status:
            raise ValueError(f"immutable response conflict: {job.key}")
        return previous
    try:
        payload = _payload(body)
    except ValueError:
        payload = None
    record = {"request_key": job.key, "endpoint": quarterly.URL, "params": job.params,
              "fetched_at": _kst(fetched_at).isoformat(), "http_status": http_status,
              "dart_status": payload["status"] if payload else None, "sha256": checksum}
    _immutable(directory / "blobs" / f"{checksum}.json", body)
    complete = payload is not None and _complete(http_status, payload)
    relative = Path("requests") / f"{job.key}.json" if complete else Path("attempts") / f"{job.key}_{uuid.uuid4().hex}.json"
    _immutable(directory / relative, _encoded(record))
    return record, payload


def _prepare(data_dir, from_year, to_year, corp_codes_path, listing_dir, universe_source):
    if (type(from_year) is not int or type(to_year) is not int
            or not 2015 <= from_year <= to_year <= 2025):
        raise ValueError("years must be ordered integers within 2015..2025")
    universe = quarterly.load_universe(corp_codes_path=corp_codes_path,
        listing_dir=listing_dir if listing_dir is not None else Path(data_dir) / "disclosures/dart_full/list",
        universe_source=universe_source)
    if not universe:
        raise ValueError("quarterly universe is empty")
    corps = list(universe)
    batches = [tuple(corps[i:i + 100]) for i in range(0, len(corps), 100)]
    jobs = [Job(year, report, batch) for year in range(from_year, to_year + 1)
            for report in quarterly.REPORTS for batch in batches]
    # A changed universe must not silently change the old archive's batches.
    checkpoint = Path(data_dir) / "fundamentals/dart_quarterly/_state.json"
    if checkpoint.exists():
        old = _json(checkpoint)["completed_batches"]
        if (not isinstance(old, list) or not old
                or any(not isinstance(key, str) or not re.fullmatch(r"[0-9]{4}_110(?:11|12|13|14)_[0-9a-f]{64}", key) for key in old)):
            raise ValueError("invalid quarterly batch checkpoint")
        if {key.rsplit("_", 1)[1] for key in old} != {job.key.rsplit("_", 1)[1] for job in jobs}:
            raise ValueError("universe batches differ from existing quarterly archive")
    elif universe_source is None:
        raise ValueError("existing quarterly batch checkpoint is required")
    manifest = {"universe": universe, "batch_hashes": [Job(2015, "11013", b).key.rsplit("_", 1)[1] for b in batches]}
    frozen = _root(data_dir) / "_universe.json"
    if frozen.exists() and _json(frozen) != manifest:
        raise ValueError("universe differs from frozen raw archive")
    return universe, jobs, manifest


def _coverage(directory, universe, jobs):
    years = defaultdict(Counter)
    for job in jobs:
        counts = years[str(job.year)]
        counts["total_requests"] += 1
        counts["completed_requests"] += int(_stored(directory, job) is not None)
    by_year = {}
    for year, counts in sorted(years.items()):
        total, complete = counts["total_requests"], counts["completed_requests"]
        by_year[year] = {**counts, "remaining_requests": total - complete, "coverage": complete / total}
    total = len(jobs)
    complete = sum(x["completed_requests"] for x in by_year.values())
    return {"status": "dry_run", "companies": len(universe), "batches": (len(universe) + 99) // 100,
            "total_requests": total, "completed_requests": complete, "remaining_requests": total - complete,
            "by_year": by_year, "archive_dir": str(directory)}


def plan(data_dir=DEFAULT_DATA_DIR, *, from_year=2015, to_year=2025,
         corp_codes_path=DEFAULT_CORP_CODES, listing_dir=None, universe_source=None):
    universe, jobs, _ = _prepare(data_dir, from_year, to_year, corp_codes_path, listing_dir, universe_source)
    return _coverage(_root(data_dir), universe, jobs)


def _version(row):
    return tuple(str(row[field]) for field in VERSION_FIELDS)


def _skip_reason(account, job):
    """Same row-level rules as dart_quarterly: rows DART returns outside the request are skipped and counted."""
    checks = (("corp_not_in_batch", account.get("corp_code") not in job.corps),
              ("bsns_year", str(account.get("bsns_year")) != str(job.year)),
              ("reprt_code", account.get("reprt_code") != job.report),
              ("fs_div", account.get("fs_div") not in {"CFS", "OFS"}),
              ("account_nm", not isinstance(account.get("account_nm"), str) or not account["account_nm"].strip()),
              ("sj_div", not isinstance(account.get("sj_div"), str) or not account["sj_div"].strip()),
              ("currency_type", account.get("currency") is not None and not isinstance(account["currency"], str)))
    return next((reason for reason, invalid in checks if invalid), None)


def derive_rows(payload, job, universe, checksum, skipped=None):
    if payload["status"] == "013":
        return []
    groups = {}
    for account in payload["list"]:
        reason = _skip_reason(account, job)
        if reason is not None:
            if skipped is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
            continue
        available = quarterly._filing_date(account.get("rcept_no"))
        if account.get("sj_div") != "BS":
            continue
        key = _version(account)
        if key not in groups:
            groups[key] = {**dict(zip(VERSION_FIELDS, key)), "bsns_year": job.year,
                "stock_codes": universe[account["corp_code"]],
                "fiscal_quarter": f"{job.year}Q{quarterly.REPORTS[job.report]}",
                "available_date": available, "source_sha256": checksum, "request_key": job.key, "accounts": []}
        name = re.sub(r"\s+", "", account["account_nm"])
        groups[key]["accounts"].append({**account, "metric": METRICS.get(name)})
    return [groups[key] for key in sorted(groups)]


def _derive(directory, job, universe, stored):
    record, payload = stored
    skipped = {}
    rows = derive_rows(payload, job, universe, record["sha256"], skipped)
    body = b"".join(_encoded(row) for row in rows)
    path = directory / "derived" / f"{job.key}.jsonl"
    if not path.exists() or path.read_bytes() != body:
        atomic_write(path, body.decode())
    if skipped:
        # Deterministic from the raw bytes; recorded so skipped rows stay visible in reports.
        side = directory / "derived" / f"{job.key}.skipped.json"
        content = _encoded({"request_key": job.key, "source_sha256": record["sha256"], "skipped_rows": skipped}).decode()
        if not side.exists() or side.read_text(encoding="utf-8") != content:
            atomic_write(side, content)
    return len(rows)


def _quota(directory):
    path = directory / "_quota.json"
    quota = _json(path) if path.exists() else {"days": {}}
    if not isinstance(quota, dict) or not isinstance(quota.get("days"), dict):
        raise ValueError("invalid raw archive quota ledger")
    for day, count in quota["days"].items():
        if date.fromisoformat(day).isoformat() != day or not isinstance(count, dict):
            raise ValueError("invalid quota day")
        if (type(count.get("requests")) is not int or count["requests"] < 0
                or type(count.get("provider_limited")) is not bool):
            raise ValueError("invalid quota counter")
    return quota


def fetch(data_dir=DEFAULT_DATA_DIR, *, execute=False, max_requests=DEFAULT_MAX_REQUESTS,
          daily_cap=DEFAULT_DAILY_CAP, api_key=None, session=None, clock=None,
          from_year=2015, to_year=2025, corp_codes_path=DEFAULT_CORP_CODES,
          listing_dir=None, universe_source=None):
    for name, value in (("max_requests", max_requests), ("daily_cap", daily_cap)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    universe, jobs, manifest = _prepare(data_dir, from_year, to_year, corp_codes_path, listing_dir, universe_source)
    directory = _root(data_dir)
    summary = {**_coverage(directory, universe, jobs), "max_requests": max_requests, "daily_cap": daily_cap}
    if not execute:
        return summary
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("DART API key is required for --execute")
    now = clock if clock is not None else lambda: datetime.now(KST)
    owned_session = session is None
    summary.update(status="ok", requests_made=0, derived_rows=0)
    # One lock covers quota reservation, response publication and resume checks.
    with file_lock(directory / ".fetch.lock"):
        _immutable(directory / "_universe.json", _encoded(manifest))
        quota = _quota(directory)
        try:
            for job in jobs:
                stored = _stored(directory, job)
                if stored is not None:
                    summary["derived_rows"] += _derive(directory, job, universe, stored)
                    continue
                if summary["requests_made"] >= max_requests:
                    summary.update(status="stopped", reason="max_requests")
                    break
                attempted = _kst(now())
                counter = quota["days"].setdefault(attempted.date().isoformat(), {"requests": 0, "provider_limited": False})
                if counter["provider_limited"] or counter["requests"] >= daily_cap:
                    reason = "provider_status_020" if counter["provider_limited"] else "daily_cap"
                    summary.update(status="stopped", reason=reason)
                    break
                # Reserve before sending, including failures and interrupted calls.
                counter["requests"] += 1
                atomic_write(directory / "_quota.json", _encoded(quota).decode())
                summary["requests_made"] += 1
                try:
                    if session is None:
                        session = requests.Session()
                    response = session.get(quarterly.URL, params={"crtfc_key": api_key, **job.params},
                                           timeout=20, allow_redirects=False)
                except requests.RequestException:
                    raise ValueError("DART transport failure; request attempt charged") from None
                stored = store_response(directory, job, response.content, response.status_code, _kst(now()))
                record, payload = stored
                if payload is not None and payload["status"] in {"020", "800"}:
                    if payload["status"] == "020":
                        counter["provider_limited"] = True
                        atomic_write(directory / "_quota.json", _encoded(quota).decode())
                    summary.update(status="stopped", reason=f"provider_status_{payload['status']}")
                    break
                if payload is None or not _complete(record["http_status"], payload):
                    raise ValueError("DART HTTP/provider response failed; raw attempt preserved")
                summary["derived_rows"] += _derive(directory, job, universe, stored)
        finally:
            if owned_session and session is not None:
                session.close()
        summary.update({key: value for key, value in _coverage(directory, universe, jobs).items() if key != "status"})
    return summary


def _rows(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"invalid JSONL row: {path.name}")
                yield row


def _amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _asset_index(directory, year):
    index = defaultdict(lambda: defaultdict(set))
    requests_dir = directory / "requests"
    for metadata_path in sorted(requests_dir.glob(f"{year}_110*.json")):
        metadata = _json(metadata_path)
        params = metadata["params"]
        job = Job(int(params["bsns_year"]), params["reprt_code"], tuple(params["corp_code"].split(",")))
        if metadata_path.stem != job.key:
            raise ValueError("request filename/key mismatch")
        stored = _stored(directory, job)
        derived = directory / "derived" / f"{job.key}.jsonl"
        if not derived.is_file():
            raise ValueError("derived archive missing; resume fetch to rebuild without re-requesting")
        # Verify reproducibility against raw bytes before using a derived value.
        universe = _json(directory / "_universe.json")["universe"]
        expected = derive_rows(stored[1], job, universe, stored[0]["sha256"])
        if derived.read_bytes() != b"".join(_encoded(row) for row in expected):
            raise ValueError("derived archive differs from raw response")
        for row in expected:
            currencies = index[_version(row)]
            for account in row["accounts"]:
                if account.get("metric") == "total_assets":
                    currencies[account.get("currency")].add(_amount(account.get("thstrm_amount")))
    return index


def _listing_groups(data_dir):
    groups = {}
    profiles = Path(data_dir) / "reference/dart_company/companies.jsonl"
    if profiles.exists():
        for row in _rows(profiles):
            classification = row.get("corp_cls")
            # N is KONEX, not a delisting classification (see dart_list_supplement).
            group = "listed" if classification in {"Y", "K", "N"} else "delisted" if classification == "E" else "unknown"
            corp = row["corp_code"]
            if corp in groups and groups[corp] != group:
                raise ValueError("conflicting current listing classifications")
            groups[corp] = group
    return groups


def link_check(data_dir=DEFAULT_DATA_DIR, *, from_year=None, to_year=None):
    """Count stored rows, including every correction; never choose a newer filing.

    The join adds currency to the five archive version fields. A missing currency
    cannot establish a match. Listing status is a current E/non-exchange proxy among
    historically traded firms, not a claim about status at a decision date.
    """
    for value in (from_year, to_year):
        if value is not None and (type(value) is not int or not 2015 <= value <= 9999):
            raise ValueError("link-check years must be integers from 2015 onward")
    if from_year is not None and to_year is not None and from_year > to_year:
        raise ValueError("link-check years must be ordered")
    paths = sorted((Path(data_dir) / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    paths = [path for path in paths if (from_year is None or from_year <= int(path.stem[:4]))
             and (to_year is None or int(path.stem[:4]) <= to_year)]
    if not paths:
        raise ValueError("quarterly archives are missing")
    groups = _listing_groups(data_dir)
    counters = defaultdict(Counter)
    by_year, by_status, crossed = {}, {}, {}
    for year in sorted({path.stem[:4] for path in paths}):
        assets = _asset_index(_root(data_dir), year)
        for path in (path for path in paths if path.stem[:4] == year):
            for row in _rows(path):
                if str(row["bsns_year"]) != year:
                    raise ValueError("quarterly archive year mismatch")
                currency = row.get("currency")
                version = _version(row)
                candidates = assets.get(version, {})
                values = candidates.get(currency, set()) if currency else set()
                if version not in assets:
                    reason = "missing_version"
                elif not candidates:
                    reason = "missing_total_assets"
                elif not currency or not values:
                    reason = "currency_mismatch_or_missing"
                elif len(values) > 1:
                    reason = "conflicting_total_assets"
                elif None in values:
                    reason = "invalid_total_assets"
                else:
                    reason = "matched"
                status = groups.get(row["corp_code"], "unknown")
                for label in ("overall", f"year:{year}", f"status:{status}", f"cross:{year}:{status}"):
                    counters[label]["total"] += 1
                    counters[label][reason] += 1
        for status in ("listed", "delisted", "unknown"):
            crossed.setdefault(year, {})[status] = counters[f"cross:{year}:{status}"]
        by_year[year] = counters[f"year:{year}"]
    for status in ("listed", "delisted", "unknown"):
        by_status[status] = counters[f"status:{status}"]

    def report(counts):
        total, matched = counts["total"], counts["matched"]
        return {**counts, "total": total, "matched": matched, "fraction": matched / total if total else None}

    return {"overall": report(counters["overall"]), "by_year": {k: report(v) for k, v in by_year.items()},
            "by_listing_status": {k: report(v) for k, v in by_status.items()},
            "by_year_and_listing_status": {y: {s: report(c) for s, c in v.items()} for y, v in crossed.items()},
            "listing_rule": "Current profile Y/K/N (including KONEX) = listed; E = delisted/non-exchange proxy among historical stocks; absent = unknown. Supplement membership alone does not prove delisting. Not historical status.",
            "version_rule": "Exact corp_code/bsns_year/reprt_code/fs_div/rcept_no and nonempty equal currency; all saved corrections counted.",
            "date_rule": "Use dart_financial_date_rules.FilingDates.annotate with the same receipt for S/O; fetch time is not public availability."}
