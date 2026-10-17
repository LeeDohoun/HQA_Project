"""Restore historical filings excluded by today's DART corp_cls filter.

list.json applies Y/K at query time: later-delisted (E), KONEX (N), and unknown
companies disappear even from years when they traded on KRX. Main real-time
listing collection is unchanged. This separate, opt-in archive queries each
historically traded corporation without corp_cls and preserves provider rows.
"""
from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from src.utils.stock_codes import is_stock_code

from .dart_api import DartAPIError, DartProviderMaintenance
from .dart_backfill import (DEFAULT_DATA_DIR, DEFAULT_MAX_REQUESTS, DartBackfillCollector,
                            _QuotaReached, _date, _read_json, _write_json)
from .dart_listing import (START, CURRENT_FIELDS, digest, directory, reference_version,
                           validate_supplement_state, supplement_manifest, overlap_differences,
                           required_supplement_targets, freeze_target_scan)
from .dart_poller import KST, _validate_page
from .storage import file_lock, read_rows, write_rows


def targets(data_dir, *, end_de=None):
    """Read local KRX sessions once, counting unique stock-days, never guessing IDs."""
    root = DEFAULT_DATA_DIR if data_dir is None else data_dir
    root = Path(root)
    master = root / "reference/corp_codes.csv"
    if not master.is_file():
        raise ValueError("DART supplement corporate-code CSV is missing")
    aliases = defaultdict(set)
    with master.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"corp_code", "stock_code"}.issubset(reader.fieldnames or []):
            raise ValueError("DART invalid corporate-code CSV columns")
        for row in reader:
            code, corp = row["stock_code"].strip(), row["corp_code"]
            if not re.fullmatch(r"[0-9]{8}", corp):
                raise ValueError("DART invalid corporate code")
            if is_stock_code(code):
                aliases[code].add(corp)
    profiles = {}
    for row in read_rows(root / "reference/dart_company/companies.jsonl"):
        corp = row.get("corp_code")
        if not isinstance(corp, str) or not re.fullmatch(r"[0-9]{8}", corp):
            raise ValueError("DART invalid company profile identity")
        # Company archives retain the latest observation per corporation.
        if corp in profiles and profiles[corp] != row.get("corp_cls"):
            raise ValueError("DART conflicting current company classifications")
        profiles[corp] = row.get("corp_cls")
    stock_days, yearly = Counter(), {}
    paths = sorted(p for p in (root / "market/krx_daily").glob("*/*.jsonl")
                   if re.fullmatch(r"[0-9]{8}", p.stem) and p.stem >= START
                   and (end_de is None or p.stem <= end_de))
    if not paths:
        raise ValueError("DART supplement historical KRX sessions are missing")
    for path in paths:
        _date(path.stem)
        codes = set()
        for row in read_rows(path):
            code = row.get("stock_code")
            if not isinstance(code, str) or len(code) != 6:
                raise ValueError("KRX invalid daily stock code")
            if str(row.get("trade_date", "")).replace("-", "") != path.stem:
                raise ValueError("KRX daily date disagrees with archive")
            codes.add(code)
        stock_days.update(codes)
        group = yearly.setdefault(path.stem[:4], Counter())
        group.update(codes)
    selected = defaultdict(set)
    for code in stock_days:
        if not re.fullmatch(r"[0-9]{6}", code):
            continue
        for corp in aliases.get(code, ()):
            if profiles.get(corp) not in {"Y", "K"}:
                selected[corp].add(code)
    affected = {code for code in stock_days if set(aliases.get(code, ())) & set(selected)}
    by_year = {}
    for year, counts in yearly.items():
        observed = set(counts) & affected
        corps = {corp for code in observed for corp in aliases[code] if corp in selected}
        total, affected_days = sum(counts.values()), sum(counts[c] for c in observed)
        by_year[year] = {"affected_corps": len(corps), "affected_stock_codes": len(observed),
                         "krx_stock_days": total, "affected_stock_days": affected_days,
                         "affected_share": affected_days / total if total else None}
    unmapped = {c: n for c, n in sorted(stock_days.items()) if c not in aliases}
    return {"corps": {c: sorted(selected[c]) for c in sorted(selected)},
            "affected_stock_codes": sorted(affected),
            "current_class_counts": dict(Counter(profiles.get(c) or "unknown" for c in selected)),
            "latest_krx_session": paths[-1].stem, "unmapped_stock_days": unmapped,
            "by_year": by_year, "reference_version": reference_version(root)}


class _RangeRejected(Exception):
    pass


class SupplementCollector(DartBackfillCollector):
    def range_rows(self, corp, start, end, *, allow_range_error=False):
        items, expected, page = {}, None, 1
        while True:
            response = self._request(self.LIST_URL, {"crtfc_key": self.api_key, "corp_code": corp,
                "bgn_de": start, "end_de": end, "last_reprt_at": "N", "page_count": 100,
                "page_no": page, "sort": "date", "sort_mth": "asc"})
            try:
                raw = response.json()
            except (ValueError, TypeError):
                raise DartAPIError("DART invalid JSON response") from None
            # 100 is the parameter/range error. Authentication and all other
            # errors still pass through the normal fail-loudly status handler.
            if allow_range_error and page == 1 and isinstance(raw, dict) and raw.get("status") == "100":
                raise _RangeRejected()
            payload = self._payload(raw)
            if payload["status"] == "013":
                if page != 1:
                    raise DartAPIError("DART incomplete pagination: later page has no data")
                return []
            rows = payload.get("list")
            if not isinstance(rows, list):
                raise DartAPIError("DART invalid supplement page rows")
            for row in rows:
                day = row.get("rcept_dt") if isinstance(row, dict) else None
                if (not isinstance(day, str) or not start <= day <= end
                        or row.get("corp_code") != corp):
                    raise DartAPIError("DART supplement range or corporation mismatch")
                try:
                    _date(day)
                except ValueError:
                    raise DartAPIError("DART invalid supplement receipt date") from None
                if row.get("rcept_no") in items:
                    raise DartAPIError("DART duplicate receipt during pagination")
                # Reuse all field/pagination validation, substituting only the
                # single-day assertion for the explicitly checked range above.
            validated = {}
            probe = {**payload, "list": [{**r, "rcept_dt": start} for r in rows]}
            expected = _validate_page(probe, page, expected, start, validated)
            if len(validated) != len(rows):
                raise DartAPIError("DART duplicate receipt within page")
            items.update((row["rcept_no"], row) for row in rows)
            if page == expected[1]:
                return list(items.values())
            page += 1

    def corp_rows(self, corp, end):
        try:
            return self.range_rows(corp, START, end, allow_range_error=True), "long_range"
        except _RangeRejected:
            rows = []
            for year in range(int(START[:4]), int(end[:4]) + 1):
                rows.extend(self.range_rows(corp, max(START, f"{year}0101"), min(end, f"{year}1231")))
            return rows, "calendar_years"


def _publish(root, state, pending, main_receipts):
    corp, rows = pending["corp_code"], pending["rows"]
    if corp in state["completed_corps"]:
        return
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["rcept_dt"]].append(row)
    stored = skipped = duplicates = 0
    differences = dict.fromkeys(CURRENT_FIELDS, 0)
    for day, new in grouped.items():
        path = root / "list_supplement" / day[:4] / f"{day}.jsonl"
        previous = read_rows(path)
        merged = {r["rcept_no"]: r for r in previous}
        for row in new:
            receipt = row["rcept_no"]
            if receipt in main_receipts:
                # Current corp_cls/name may differ from a real-time main row;
                # keep the authoritative main row, count the overlap.
                for field in overlap_differences(main_receipts[receipt], row):
                    differences[field] += 1
                duplicates += 1
                continue
            if receipt in merged and merged[receipt] != row:
                raise ValueError("DART conflicting supplement receipt on resume")
            merged[receipt] = row
            stored += 1
        if list(merged.values()) != previous:
            write_rows(path, sorted(merged.values(), key=lambda r: r["rcept_no"]))
    state["completed_corps"] = sorted(state["completed_corps"] + [corp])
    state["rows_per_corp"][corp] = stored
    state["skipped_per_corp"][corp] = skipped
    state["duplicates_per_corp"][corp] = duplicates
    state.setdefault("overlap_differences_per_corp", {})[corp] = differences
    state.setdefault("range_mode_per_corp", {})[corp] = pending["range_mode"]
    _write_json(root / "list_supplement/_state.json", state)


def supplement(*, data_dir=DEFAULT_DATA_DIR, execute=False, api_key=None, session=None,
               clock=None, sleeper=None, max_requests=DEFAULT_MAX_REQUESTS,
               request_interval=0.25, max_retries=3, timeout=20):
    root = directory(data_dir)
    state_path = root / "list_supplement/_state.json"
    existing = _read_json(state_path, None)
    if existing is not None:
        validate_supplement_state(existing)
    main = _read_json(root / "_state.json", {})
    days = main.get("completed_listing_days", [])
    if not days or any(not isinstance(d, str) or _date(d).strftime("%Y%m%d") != d for d in days):
        raise ValueError("DART main completed_listing_days required for supplement end_de")
    end = existing["end_de"] if existing is not None else max(days)
    if end < START:
        raise ValueError("DART supplement end_de precedes its start")
    selection = targets(data_dir, end_de=end)
    corps = existing["target_corps"] if existing is not None else sorted(selection["corps"])
    initial = {"start_de": START, "end_de": end, "target_corps": corps,
               "target_set_hash": digest(corps), "reference_version": selection["reference_version"],
               "latest_krx_session": selection["latest_krx_session"], "completed_corps": [],
               "rows_per_corp": {}, "skipped_per_corp": {}, "duplicates_per_corp": {}}
    def progress():
        state = validate_supplement_state(_read_json(state_path, initial.copy()))
        for field in ("end_de", "target_set_hash"):
            if state.get(field) != initial[field]:
                raise ValueError("DART supplement target sources/range changed; use a separate data snapshot")
        missing = required_supplement_targets(data_dir, end, included_targets=corps)
        if missing:
            raise ValueError("DART supplement_targets_missing: " + ", ".join(missing))
        return state
    state = progress()
    remaining = len(corps) - len(state["completed_corps"])
    journal = root / "list_supplement/_pending.json"
    pending = _read_json(journal, {}) if journal.exists() else None
    if pending is not None:
        if (pending.get("corp_code") not in corps or pending.get("target_set_hash") != digest(corps)
                or pending.get("end_de") != end or pending.get("rows_sha256") != digest(pending.get("rows"))):
            raise ValueError("DART invalid pending supplement publication")
        remaining -= pending["corp_code"] not in state["completed_corps"]
    estimate = {"minimum_long_range_calls": remaining,
                "year_chunk_first_page_calls_if_all_rejected": remaining * (int(end[:4]) - 2016 + 2),
                "note": "Lower bounds: add pagination, retries; year fallback includes rejected long request."}
    summary = {"status": "dry_run", "target_corps": len(corps), "target_set_hash": digest(corps),
               "current_class_counts": selection["current_class_counts"], "end_de": end,
               "unmapped_codes": len(selection["unmapped_stock_days"]),
               "unmapped_stock_days_total": sum(selection["unmapped_stock_days"].values()),
               "unmapped_stock_days": selection["unmapped_stock_days"], "estimated_calls": estimate,
               "state": state, "requests_made": 0}
    if not execute:
        return summary
    if type(max_requests) is not int or max_requests < 1 or type(max_retries) is not int or max_retries < 1:
        raise ValueError("DART request budgets must be positive integers")
    for n in (request_interval, timeout):
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 < n < float("inf"):
            raise ValueError("DART timing values must be positive and finite")
    with file_lock(root / ".backfill.lock"):
        state = progress()
        collector = SupplementCollector(api_key, root, state, {}, session=session,
            clock=clock or (lambda: datetime.now(KST)), sleeper=sleeper or time.sleep,
            max_requests=max_requests, request_interval=request_interval, max_retries=max_retries, timeout=timeout)
        try:
            main_receipts = {}
            for path in sorted((root / "list").glob("*/*.jsonl")):
                for row in read_rows(path):
                    receipt = row["rcept_no"]
                    if receipt in main_receipts:
                        overlap_differences(main_receipts[receipt], row)
                    main_receipts[receipt] = row
            if "file_manifest" in state and state["file_manifest"] != supplement_manifest(data_dir):
                raise ValueError("DART completed supplement archive is missing or changed")
            saved_counts = Counter()
            for path in sorted((root / "list_supplement").glob("*/*.jsonl")):
                receipts = set()
                for row in read_rows(path):
                    if (row.get("corp_code") not in corps or row.get("rcept_dt") != path.stem
                            or row.get("rcept_no") in receipts):
                        raise ValueError("DART invalid saved supplement rows")
                    receipts.add(row["rcept_no"])
                    saved_counts[row["corp_code"]] += 1
            if any(saved_counts[c] != state["rows_per_corp"][c] for c in state["completed_corps"]):
                raise ValueError("DART completed supplement archive is missing or changed")
            if not state_path.exists():
                _write_json(state_path, state)
            if journal.exists():
                pending = _read_json(journal, {})
                if pending.get("corp_code") not in corps or pending.get("target_set_hash") != digest(corps) or pending.get("end_de") != end:
                    raise ValueError("DART supplement pending publication belongs to another target set")
                if pending.get("rows_sha256") != digest(pending.get("rows")):
                    raise ValueError("DART supplement pending publication checksum changed")
                _publish(root, state, pending, main_receipts)
                journal.unlink()
            for corp in corps:
                if corp in state["completed_corps"]:
                    continue
                rows, mode = collector.corp_rows(corp, end)
                pending = {"corp_code": corp, "rows": rows, "range_mode": mode,
                           "target_set_hash": digest(corps), "end_de": end, "rows_sha256": digest(rows)}
                _write_json(journal, pending)
                _publish(root, state, pending, main_receipts)
                journal.unlink()
            state["file_manifest"] = supplement_manifest(data_dir)
            if "target_scan" not in state:
                state["target_scan"] = freeze_target_scan(data_dir, end)
            _write_json(state_path, state)
            status = "complete"
        except _QuotaReached:
            status = "quota_reached"
        except DartProviderMaintenance:
            status = "provider_maintenance"
        finally:
            if collector._owns_session and collector.session is not None:
                collector.session.close()
        return {**summary, "status": status, "state": state, "requests_made": collector.requests_made}
