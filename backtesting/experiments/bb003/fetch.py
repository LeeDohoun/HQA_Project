"""BB003-only fetcher; no writes to DART/FW001 shared archives.

Fixed per-receipt attempts at offsets 0/60/300/900 seconds, at most four in
total, and never at/after the decision deadline. Each attempt is immutable.
The timer retries the pipeline; this module never sleeps or silently retries.
"""
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

import requests

from src.ingestion import dart_buyback as dart
from src.ingestion.dart import DartDisclosureCollector
from . import NotReady, aware
from .deployment import verified
from .inputs import read_bytes
from .storage import encoded, sha

RETRY_OFFSETS = (0, 60, 300, 900)
TIMEOUT_SECONDS, MAX_PAGES, MAX_DOCUMENT_BYTES = 15, 10, 32 * 1024 * 1024


class FetchError(NotReady):
    pass


def document_text(blob, receipt):
    if len(blob) > MAX_DOCUMENT_BYTES:
        raise FetchError("document_size_bound")
    try:
        with ZipFile(BytesIO(blob)) as archive:
            entries = [p for p in archive.infolist() if not p.is_dir()
                       and Path(p.filename).suffix.lower() in {".xml", ".xhtml", ".html", ".htm"}]
            if sum(p.file_size for p in entries) > MAX_DOCUMENT_BYTES:
                raise FetchError("document_uncompressed_size_bound")
            exact = [p for p in entries if Path(p.filename).stem == receipt]
            if not exact:
                raise FetchError("original_receipt_document_missing")
            normalizer = object.__new__(DartDisclosureCollector)
            text = []
            for entry in exact:
                raw = archive.read(entry)
                decoded, _, mojibake = normalizer._decode_bytes_with_candidates(raw)
                plain = normalizer._sanitize_body_text(normalizer._normalize_inner_document_text(entry.filename, decoded))
                if (mojibake or not plain or normalizer._looks_like_toc_only(plain)
                        or normalizer._contains_error_page_tokens(plain) or normalizer._contains_wrapper_tokens(plain)):
                    raise FetchError("original_document_body_invalid")
                text.append(plain)
            return "\n".join(text)
    except (BadZipFile, RuntimeError, OSError, KeyError):
        raise FetchError("document_zip_invalid") from None


class HttpClient:
    """Only explicitly executing the live CLI constructs this client."""
    def __init__(self, api_key, *, session=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("DART_API_KEY is required; dotenv is never loaded")
        self.key, self.session = api_key, session or requests.Session()

    def _get(self, endpoint, params, reserve):
        reserve(endpoint, params)
        try:
            response = self.session.get("https://opendart.fss.or.kr/api/" + endpoint,
                params={"crtfc_key": self.key, **params}, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            # Provider URLs and exception strings may contain credentials.
            raise FetchError("dart_transport_or_http_error") from None

    def structured(self, item, *, reserve):
        rows, payloads, expected = {}, [], None
        for page in range(1, MAX_PAGES + 1):
            raw = self._get("tsstkAqDecsn.json", {"corp_code": item["corp_code"],
                "bgn_de": item["rcept_dt"], "end_de": item["rcept_dt"], "page_no": page, "page_count": 100}, reserve)
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                raise FetchError("structured_json_invalid") from None
            payloads.append(payload)
            if not isinstance(payload, dict) or not re.fullmatch("[0-9]{3}", str(payload.get("status", ""))):
                raise FetchError("structured_status_invalid")
            if payload.get("status") != "000":
                raise FetchError("structured_status_" + str(payload.get("status")))
            items = payload.get("list")
            if not isinstance(items, list):
                raise FetchError("structured_list_invalid")
            for row in items:
                receipt = row.get("rcept_no")
                if (not isinstance(receipt, str) or row.get("corp_code") not in (None, "", item["corp_code"])
                        or row.get("rcept_dt") not in (None, item["rcept_dt"])):
                    raise FetchError("structured_identity_invalid")
                if receipt in rows and rows[receipt] != row:
                    raise FetchError("structured_original_conflict")
                rows[receipt] = row
            fields = ("page_no", "total_page", "total_count")
            if not any(k in payload for k in fields):
                if page != 1 or len(items) >= 100:
                    raise FetchError("structured_unpaged_saturation")
                break
            try:
                current, pages, total = (int(payload[k]) for k in fields)
            except (ValueError, TypeError, KeyError):
                raise FetchError("structured_pagination_invalid") from None
            if (current != page or not page <= pages <= MAX_PAGES or not items
                    or expected not in (None, (pages, total))):
                raise FetchError("structured_pagination_inconsistent")
            expected = pages, total
            if page == pages:
                if len(rows) != total:
                    raise FetchError("structured_pagination_count")
                break
        else:
            raise FetchError("structured_page_bound")
        row = rows.get(item["rcept_no"])
        if row is None:
            raise FetchError("structured_original_row_missing")
        return row, encoded(payloads)

    def document(self, item, *, reserve):
        raw = self._get("document.xml", {"rcept_no": item["rcept_no"]}, reserve)
        return document_text(raw, item["rcept_no"]), raw


class ArchiveClient:
    """Replay saved BB001 original receipts only; never invokes a provider."""
    def __init__(self, source):
        self.source = Path(source)
        self.rows = {}

    def structured(self, item, *, reserve):
        archive = dart.structured_record(self.source, item["corp_code"])
        if not archive or not archive["complete"] or item["rcept_no"] not in archive["rows"]:
            raise FetchError("structured_original_row_missing")
        row = archive["rows"][item["rcept_no"]]["row"]
        self.rows[item["rcept_no"]] = row
        path = dart._path(self.source, "structured", item["corp_code"])
        return row, read_bytes(path)

    def document(self, item, *, reserve):
        document = dart.document_record(self.source, item["rcept_no"])
        if not document or document["status"] != "000" or not document["text"]:
            raise FetchError("original_document_unavailable")
        if document.get("verification", {}).get("structured_row_sha256") != dart.row_digest(self.rows[item["rcept_no"]]):
            raise FetchError("archived_document_row_digest_mismatch")
        return document["text"], read_bytes(dart._path(self.source, "documents", item["rcept_no"]))


@verified()
def acquire(store, item, client, deadline):
    receipt, stamp = item["rcept_no"], aware(store.clock())
    keys = [k for k in store.keys("fetch_started") if k.startswith(receipt + ".")]
    finished = [k for k in store.keys("fetch") if k.startswith(receipt + ".")]
    if not set(finished) <= set(keys):
        raise ValueError("BB003 fetch result has no start reservation")
    if len(keys) > len(RETRY_OFFSETS) or keys != [f"{receipt}.{i + 1:02d}" for i in range(len(keys))]:
        raise ValueError("BB003 fetch reservation sequence differs")
    attempts = [store.get("fetch", key) for key in keys]
    for key, record in zip(keys, attempts):
        if record and record["reservation_sha256"] != store.proof("fetch_started", key)["record_sha256"]:
            raise ValueError("BB003 fetch result refers to another reservation")
        if record and record["status"] == "ready":
            for checksum in record["input_hashes"].values():
                store.bytes(checksum)
            return record, None
    if stamp >= deadline:
        return None, "fetch_deadline"
    if len(keys) >= len(RETRY_OFFSETS):
        return None, "bounded_retries_exhausted:" + (attempts[-1]["reason"] if attempts[-1] else "interrupted_attempt")
    first = aware(store.get("fetch_started", keys[0])["started_at"]) if keys else stamp
    if stamp < first + timedelta(seconds=RETRY_OFFSETS[len(keys)]):
        return None, "fixed_retry_not_due"
    hashes = {}
    row, text = None, None
    attempt, key = len(keys) + 1, f"{receipt}.{len(keys) + 1:02d}"
    reservation = store.seal("fetch_started", key, {"rcept_no": receipt, "attempt": attempt,
        "started_at": stamp.isoformat(), "status": "reserved"})
    request_number = 0

    def reserve(endpoint, params):
        nonlocal request_number
        if aware(store.clock()) >= deadline:
            raise FetchError("fetch_deadline")
        request_number += 1
        store.seal("http_started", key + f".{request_number:02d}", {"rcept_no": receipt,
            "attempt": attempt, "request": request_number, "endpoint": endpoint, "params": params,
            "started_at": aware(store.clock()).isoformat(), "reservation_sha256": reservation["record_sha256"]})
        if aware(store.clock()) >= deadline:
            raise FetchError("fetch_deadline")

    try:
        row, structured_raw = client.structured(item, reserve=reserve)
        if row.get("rcept_no") != receipt or row.get("corp_code") not in (None, "", item["corp_code"]):
            raise FetchError("structured_original_identity_mismatch")
        hashes["structured_response"] = store.blob(structured_raw)
        hashes["original_row"] = store.blob(encoded(row))
        if any(dart.compact(row.get(k)) in ("", "-") for k in dart.REQUIRED_FIELDS):
            raise FetchError("required_field_missing")
        text, document_raw = client.document(item, reserve=reserve)
        hashes["original_document"] = store.blob(document_raw)
        hashes["document_text"] = store.blob(text.encode())
        reason = None
    except FetchError as exc:
        reason = str(exc)
    record = {"rcept_no": receipt, "attempt": attempt, "started_at": stamp.isoformat(),
              "reservation_sha256": reservation["record_sha256"], "status": "pending" if reason else "ready",
              "reason": reason, "row": row, "verification": dart.verify_values(row, text) if text is not None else None,
              "input_hashes": hashes}
    store.seal("fetch", key, record)
    if aware(store.clock()) >= deadline:
        return None, "inputs_completed_after_deadline"
    return (None, reason) if reason else (record, None)
