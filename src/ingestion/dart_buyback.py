"""BB001 original-receipt buyback archives; local plans, opt-in downloads.

Execution shares dart_backfill's lock and KST daily quota. Structured responses
are checkpointed per corporation/request; documents retain text and hashes, not
ZIPs. Nothing in this module infers identifiers from current listed companies.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from .dart_api import DartAPIError, DartProviderMaintenance
from .dart_backfill import (DEFAULT_DATA_DIR, DEFAULT_MAX_REQUESTS, KST,
                            DartBackfillCollector, _QuotaReached, _date, _write_json)
from .storage import file_lock, read_rows


START, END = "20160601", "20251231"
VERSION = "bb001-buyback-v1"
REQUIRED_FIELDS = ("aqpln_stk_ostk", "aqpln_prc_ostk", "aq_mth",
                   "aqexpd_bgd", "aqexpd_edd", "aq_pp")
SATURATION_LIMIT = 100


def compact(value):
    return re.sub(r"\s", "", value) if isinstance(value, str) else ""


def title_matches(title):
    title = compact(title)
    return "자기주식취득결정" in title and not any(word in title for word in ("신탁", "정정", "철회", "취소"))


def method_matches(value):
    value = compact(value)
    return "장내" in value and not any(word in value for word in ("장외", "공개매수"))


def cancellation_matches(value):
    value = compact(value)
    return "소각" in value and not any(word in value for word in
        ("소각계획없", "소각예정없", "소각하지않", "소각여부미정", "소각미정"))


def digits(value):
    value = compact(value).replace(",", "")
    return value if re.fullmatch(r"[0-9]+", value) else None


def row_digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def verify_values(row, text):
    text = compact(text).replace(",", "")
    quantity, amount = (digits((row or {}).get(field)) for field in REQUIRED_FIELDS[:2])
    present = lambda number: bool(number and re.search(r"(?<![0-9])" + number + r"(?![0-9])", text))
    return {"quantity_digits": quantity, "amount_digits": amount,
            "quantity_present": present(quantity), "amount_present": present(amount),
            "verified": present(quantity) and present(amount),
            "structured_row_sha256": row_digest(row) if row is not None else None}


def listing_files(data_dir):
    directory = Path(data_dir) / "disclosures/dart_full/list"
    return {path.stem: path for path in sorted(directory.glob("*/*.jsonl"))
            if re.fullmatch(r"[0-9]{8}", path.stem) and START <= path.stem <= END}


def load_listings(data_dir, *, candidates_only=False):
    files, receipts = listing_files(data_dir), {}
    identities = {} if candidates_only else receipts
    raw_count = prefix_differs = withdrawals = title_count = 0
    for day, path in files.items():
        _date(day)
        for row in read_rows(path):
            raw_count += 1
            receipt = row.get("rcept_no")
            if (row.get("rcept_dt") != day or not isinstance(receipt, str)
                    or not re.fullmatch(r"[0-9]{14}", receipt)
                    or not isinstance(row.get("report_nm"), str)
                    or not isinstance(row.get("corp_code"), str)
                    or not re.fullmatch(r"[0-9]{8}", row["corp_code"])):
                raise ValueError(f"invalid DART listing identity: {path.name}")
            # Observation metadata can differ; event identity cannot.
            item = {name: row.get(name) for name in ("rcept_no", "rcept_dt", "report_nm", "corp_code",
                                                     "stock_code", "corp_name", "corp_cls")}
            # Keep compact identities for all receipts, but only candidate rows
            # in collector plans. Full listings remain available to research callers.
            identity = int(receipt) if candidates_only else receipt
            digest = bytes.fromhex(row_digest(item)) if candidates_only else item
            if identity in identities and identities[identity] != digest:
                raise ValueError("conflicting DART listing receipt")
            if identity in identities:
                continue
            identities[identity] = digest
            matches = title_matches(item["report_nm"])
            title_count += matches
            prefix_differs += receipt[:8] != day
            title = compact(item["report_nm"])
            withdrawals += "자기주식" in title and any(word in title for word in ("철회", "취소"))
            if not candidates_only or matches:
                receipts[receipt] = item
    rows = sorted(receipts.values(), key=lambda row: (row["rcept_dt"], row["rcept_no"]))
    return rows, files, {"raw_rows": raw_count, "duplicate_receipts": raw_count - len(identities),
                        "unique_receipts": len(identities), "title_candidates": title_count,
                        "receipt_prefix_differs_from_public_date": prefix_differs,
                        "withdrawal_titles": withdrawals}


def _path(data_dir, kind, identity):
    return Path(data_dir) / "disclosures/dart_buyback" / kind / f"{identity}.json"


def _read_record(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError(f"invalid buyback archive: {path.name}")
    return value


def structured_record(data_dir, corp_code):
    value = _read_record(_path(data_dir, "structured", corp_code))
    if value is not None:
        if (value.get("corp_code") != corp_code or value.get("from_date") != START
                or value.get("to_date") != END or not isinstance(value.get("requests"), list)
                or type(value.get("complete")) is not bool or not isinstance(value.get("rows"), dict)):
            raise ValueError("invalid buyback corporate archive")
        for request in value["requests"]:
            if request.get("payload_sha256") != row_digest(request["payload"]):
                raise ValueError("buyback structured payload hash mismatch")
        if value["requests"] and value.get("sha256") != row_digest([item["payload"] for item in value["requests"]]):
            raise ValueError("buyback corporate response hash mismatch")
        for receipt, entry in value["rows"].items():
            if entry["row"].get("rcept_no") != receipt or entry["row_sha256"] != row_digest(entry["row"]):
                raise ValueError("buyback structured row hash mismatch")
    return value


def document_record(data_dir, receipt):
    value = _read_record(_path(data_dir, "documents", receipt))
    if value is not None:
        if (value.get("rcept_no") != receipt or value.get("status") not in ("000", "013", "014")
                or not isinstance(value.get("text"), str)
                or value.get("text_sha256") != hashlib.sha256(value["text"].encode()).hexdigest()
                or not re.fullmatch(r"[0-9a-f]{64}", value.get("raw_sha256", ""))):
            raise ValueError("invalid buyback original document or hash")
    return value


def plan(data_dir=DEFAULT_DATA_DIR):
    listings, files, counts = load_listings(data_dir, candidates_only=True)
    candidates = [row for row in listings if title_matches(row["report_nm"])]
    corporations = sorted({row["corp_code"] for row in candidates})
    saved = {corp: structured_record(data_dir, corp) for corp in corporations}
    complete = [corp for corp, record in saved.items() if record and record["complete"]]
    missing = [row["rcept_no"] for row in candidates if document_record(data_dir, row["rcept_no"]) is None]
    targets = {(row["corp_code"], row["rcept_dt"]) for row in candidates}
    requests = sum(not any(request["bgn_de"] == request["end_de"] == day
                           and request["page_no"] == 1 for request in (saved[corp] or {}).get("requests", []))
                   for corp, day in targets if corp not in complete)
    required_days = (_date(END) - _date(START)).days + 1
    return {"status": "dry_run", "dry_run": True, "from_date": START, "to_date": END,
            "listing_coverage": {"required_calendar_days": required_days, "stored_days": len(files),
                "missing_days": required_days - len(files), "first_day": min(files) if files else None,
                "last_day": max(files) if files else None,
                "days_by_year": {str(year): sum(day.startswith(str(year)) for day in files) for year in range(2016, 2026)}},
            "counts": counts, "corp_codes": corporations, "structured_corporations_complete": len(complete),
            "original_documents_stored": len(candidates) - len(missing), "original_documents_missing": len(missing),
            "estimated_requests_still_needed": requests + len(missing),
            "estimated_structured_requests": requests, "estimated_document_requests": len(missing),
            "estimate_note": "One exact-day initial request per distinct candidate corporation/receipt date plus missing documents; lower bound, paging can add requests. Missing listing days can add corporations/events.",
            "range_days": 1, "targeted_corporation_days": len(targets)}


def _merge_rows(rows, items, corp, start, end, source_hash):
    if not isinstance(items, list):
        raise DartAPIError("DART invalid buyback rows")
    for row in items:
        receipt = row.get("rcept_no") if isinstance(row, dict) else None
        if (not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt)
                or (row.get("rcept_dt") is not None and not start <= row["rcept_dt"] <= end)
                or row.get("corp_code") not in (None, "", corp)):
            raise DartAPIError("DART buyback receipt/range/corporation mismatch")
        if receipt in rows and rows[receipt]["row"] != row:
            raise DartAPIError("DART conflicting structured original receipt")
        entry = rows.setdefault(receipt, {"row": row, "row_sha256": row_digest(row), "response_sha256": []})
        if source_hash not in entry["response_sha256"]:
            entry["response_sha256"].append(source_hash)


def _fetch_structured(collector, corp, candidates, data_dir):
    path = _path(data_dir, "structured", corp)
    record = structured_record(data_dir, corp) or {"version": VERSION, "corp_code": corp,
        "from_date": START, "to_date": END, "complete": False, "requests": [], "rows": {}}
    if record["complete"]:
        return record
    cached = {(item["bgn_de"], item["end_de"], item["page_no"]): item for item in record["requests"]}

    def request(start, end, page):
        key = start, end, page
        if key in cached:
            return cached[key]
        response = collector._request("https://opendart.fss.or.kr/api/tsstkAqDecsn.json",
            {"crtfc_key": collector.api_key, "corp_code": corp, "bgn_de": start,
             "end_de": end, "page_no": page, "page_count": SATURATION_LIMIT})
        try:
            raw = response.json()
        except (ValueError, TypeError):
            raise DartAPIError("DART invalid buyback JSON") from None
        payload = collector._payload(raw, allow_file_not_found=True)
        item = {"bgn_de": start, "end_de": end, "page_no": page,
                "fetched_at": collector.clock().isoformat(), "sha256": hashlib.sha256(response.content).hexdigest(),
                "payload": payload, "payload_sha256": row_digest(payload)}
        record["requests"].append(item)
        cached[key] = item
        record.update(fetched_at=item["fetched_at"], sha256=row_digest([request["payload"] for request in record["requests"]]))
        _write_json(path, record)
        return item

    def collect(start, end):
        rows, expected, page = {}, None, 1
        while True:
            item = request(start, end, page)
            payload = item["payload"]
            if payload["status"] in ("013", "014"):
                if page != 1:
                    raise DartAPIError("DART incomplete buyback pagination")
                return rows
            items = payload.get("list")
            _merge_rows(rows, items, corp, start, end, item["sha256"])
            keys = ("page_no", "total_page", "total_count")
            if any(key in payload for key in keys):
                try:
                    current, pages, count = (int(payload[key]) for key in keys)
                except (KeyError, TypeError, ValueError):
                    raise DartAPIError("DART invalid buyback pagination metadata") from None
                if (current != page or pages < page or pages > 10000 or count < len(rows)
                        or (expected is not None and expected != (pages, count)) or not items):
                    raise DartAPIError("DART inconsistent buyback pagination")
                expected = pages, count
                if page == pages:
                    if len(rows) != count:
                        raise DartAPIError("DART unresolved single-day structured limit")
                    return rows
                page += 1
            elif len(items) >= SATURATION_LIMIT:
                raise DartAPIError("DART unresolved single-day structured limit")
            else:
                return rows

    rows = {}
    # API dates are first-receipt dates, not prefixes inferred from rcept_no.
    for day in sorted({item["rcept_dt"] for item in candidates}):
        for entry in collect(day, day).values():
            for digest in entry["response_sha256"]:
                _merge_rows(rows, [entry["row"]], corp, day, day, digest)
    record.update(rows=rows, complete=True, fetched_at=collector.clock().isoformat(),
                  sha256=row_digest([request["payload"] for request in record["requests"]]))
    _write_json(path, record)
    return record


def _fetch_document(collector, receipt, row):
    response = collector._request(collector.DOCUMENT_URL, {"crtfc_key": collector.api_key, "rcept_no": receipt})
    blob, text, status = response.content or b"", "", "000"
    if not blob.startswith(b"PK"):
        if blob.lstrip().startswith(b"<"):
            try:
                root = ElementTree.fromstring(blob)
                statuses = [node.text for node in root.iter() if node.tag.split("}")[-1] == "status"]
            except ElementTree.ParseError:
                raise DartAPIError("DART invalid document status XML") from None
            payload = collector._payload({"status": statuses[0] if len(statuses) == 1 else None}, allow_file_not_found=True)
        else:
            payload = collector._json_payload(response, allow_file_not_found=True)
        status = payload["status"]
        if status not in ("013", "014"):
            raise DartAPIError("DART buyback document missing ZIP")
    else:
        try:
            with ZipFile(BytesIO(blob)) as archive:
                # Prefer the receipt-named main document, never an older attachment.
                candidates = []
                for name in collector._select_document_inner_files(archive.namelist()):
                    if re.fullmatch(r"[0-9]{14}", Path(name).stem) and Path(name).stem != receipt:
                        continue
                    decoded, _, _ = collector._decode_bytes_with_candidates(archive.read(name))
                    plain = collector._sanitize_body_text(collector._normalize_inner_document_text(name, decoded))
                    if (not plain or collector._looks_like_toc_only(plain) or collector._is_mojibake_text(plain)
                            or collector._contains_error_page_tokens(plain) or collector._contains_wrapper_tokens(plain)):
                        continue
                    candidates.append(((Path(name).stem == receipt, collector._document_text_score(plain)), plain))
                if not candidates:
                    raise DartAPIError("DART buyback document has no usable original body")
                text = collector._redacted(max(candidates, key=lambda item: item[0])[1])
        except (BadZipFile, RuntimeError, OSError, KeyError):
            raise DartAPIError("DART buyback ZIP extraction failed") from None
    collector._provider_status(status)
    return {"version": VERSION, "rcept_no": receipt, "status": status, "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "raw_sha256": hashlib.sha256(blob).hexdigest(),
            "byte_size": len(blob), "fetched_at": collector.clock().isoformat(), "verification": verify_values(row, text)}


def fetch(*, data_dir=DEFAULT_DATA_DIR, execute=False, api_key=None, session=None,
          max_requests=DEFAULT_MAX_REQUESTS, clock=None, sleeper=None, skip_documents=False):
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("DART max_requests must be a positive integer")
    summary = {**plan(data_dir), "requests_made": 0, "structured_saved": 0, "documents_saved": 0}
    summary["stages"] = {
        "structured": {"status": "dry_run", "requests_made": 0, "saved": 0},
        "documents": {"status": "skipped" if skip_documents else "dry_run", "requests_made": 0, "saved": 0}}
    if not execute:
        return summary
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("DART API key is required for execution")
    directory = Path(data_dir) / "disclosures/dart_full"
    with file_lock(directory / ".backfill.lock"):
        listings, _, _ = load_listings(data_dir, candidates_only=True)
        candidates = [item for item in listings if title_matches(item["report_nm"])]
        collector = DartBackfillCollector(api_key, directory, {}, {}, session=session,
            clock=clock or (lambda: datetime.now(KST)), sleeper=sleeper or time.sleep,
            max_requests=max_requests, request_interval=0.2, max_retries=1, timeout=20)
        summary.update(status="ok", dry_run=False)
        stage = "structured"
        stage_start = 0
        summary["stages"][stage]["status"] = "ok"
        if not skip_documents:
            summary["stages"]["documents"]["status"] = "not_run"
        try:
            for corp in sorted({item["corp_code"] for item in candidates}):
                items = [item for item in candidates if item["corp_code"] == corp]
                previous = structured_record(data_dir, corp)
                _fetch_structured(collector, corp, items, data_dir)
                summary["structured_saved"] += not previous or not previous["complete"]
            summary["stages"][stage].update(requests_made=collector.requests_made, saved=summary["structured_saved"])
            stage = "documents"
            stage_start = collector.requests_made
            if not skip_documents:
                summary["stages"][stage]["status"] = "ok"
                for item in candidates:
                    receipt = item["rcept_no"]
                    if document_record(data_dir, receipt) is not None:
                        continue
                    archive = structured_record(data_dir, item["corp_code"])
                    row = archive["rows"].get(receipt, {}).get("row")
                    _write_json(_path(data_dir, "documents", receipt), _fetch_document(collector, receipt, row))
                    summary["documents_saved"] += 1
        except _QuotaReached as error:
            summary.update(status="quota_reached", reason=str(error))
            summary["stages"][stage]["status"] = "quota_reached"
        except DartProviderMaintenance:
            summary.update(status="provider_maintenance", reason="provider_status_800")
            summary["stages"][stage]["status"] = "provider_maintenance"
        finally:
            summary["stages"][stage].update(requests_made=collector.requests_made - stage_start,
                saved=summary["structured_saved" if stage == "structured" else "documents_saved"])
            summary["requests_made"] = collector.requests_made
            if collector._owns_session and collector.session is not None:
                collector.session.close()
    remaining = plan(data_dir)
    for key in ("estimated_requests_still_needed", "estimated_structured_requests", "estimated_document_requests",
                "structured_corporations_complete", "original_documents_stored", "original_documents_missing"):
        summary[key] = remaining[key]
    return summary
