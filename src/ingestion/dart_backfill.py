"""Resumable, date-only full-market DART archives; execution is opt-in."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import requests

from .dart import DartDisclosureCollector
from .dart_api import DartAPIError, read_dart_payload
from .dart_poller import KST, _kst, _validate_page, listing_params
from .storage import atomic_write, file_lock, read_rows, write_rows


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_MAX_REQUESTS = 18_999
MAX_BODY_CHARACTERS = 200_000
# Mirror event_evidence._category without importing runner's live environment.
SELECTION_PATTERNS = (
    ("regulatory_risk", r"거래정지|상장폐지|관리종목|불성실공시|횡령|배임|감사의견(?:거절|부적정)|과징금|영업정지"),
    ("convertible_bond", r"전환사채|신주인수권부사채|교환사채"),
    ("capital_raise", r"유상증자|무상증자"),
    ("buyback", r"자기주식(?:취득|처분|소각)|자사주(?:매입|소각|취득|처분)"),
    ("merger", r"합병결정|합병계약|분할결정|주식교환|영업양수|영업양도"),
    ("contract", r"단일판매[ㆍ·]?공급계약|(?:공급|수주)계약(?:체결|해지|취소)"),
    ("dividend", r"배당결정|배당락|현금[ㆍ·]?현물배당"),
    ("earnings", r"영업이익|순이익|잠정실적|실적발표|영업.*실적|매출액.*손익구조"),
    ("bond_subtype", r"전환청구권행사|전환권행사|신주인수권행사|교환청구권행사|교환권행사|"
     r"(?:만기전|조기)(?:사채)?(?:취득|상환|매수)|(?:사채|채권).*(?:재매입|재취득)|"
     r"(?:전환가액|행사가액|교환가액)(?:의)?조정"),
)


def detail_category(report_nm: str) -> str | None:
    title = re.sub(r"\s", "", report_nm)
    for name, pattern in SELECTION_PATTERNS:
        if re.search(pattern, title):
            return name
    return None


def select_report(report_nm: str) -> bool:
    return detail_category(report_nm) is not None


def _date(value: str):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("DART dates must be YYYYMMDD or YYYY-MM-DD")
    try:
        return datetime.strptime(value.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        raise ValueError("DART date is invalid") from None


def _path(directory: Path, stage: str, day: str) -> Path:
    return directory / stage / day[:4] / f"{day}.jsonl"


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("DART invalid saved progress JSON") from None
    if not isinstance(value, dict):
        raise ValueError("DART saved progress must be an object")
    return value


def _write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def _progress(directory: Path, days: list[str]) -> tuple:
    state = _read_json(directory / "_state.json",
                       {"completed_listing_days": [], "completed_detail_rcept_nos": [],
                        "skipped_missing_stock_code_rcept_nos": []})
    for values, pattern in ((state.get("completed_listing_days"), r"[0-9]{8}"),
                            (state.get("completed_detail_rcept_nos"), r"[0-9]{14}"),
                            (state.get("skipped_missing_stock_code_rcept_nos", []), r"[0-9]{14}")):
        if (not isinstance(values, list) or any(not isinstance(value, str)
                or not re.fullmatch(pattern, value) for value in values)):
            raise ValueError("DART invalid completed progress entries")
    listing_done = set(state["completed_listing_days"])
    detail_done = set(state["completed_detail_rcept_nos"])
    skipped_done = set(state.get("skipped_missing_stock_code_rcept_nos", []))
    saved_skips = set()
    for row in read_rows(directory / "skipped_missing_stock_code.jsonl"):
        if (any(not isinstance(row.get(field), str) for field in
                ("rcept_no", "corp_code", "corp_name", "corp_cls", "report_nm", "rcept_dt", "reason"))
                or not re.fullmatch(r"[0-9]{14}", row["rcept_no"])
                or not re.fullmatch(r"[0-9]{8}", row["corp_code"])
                or not re.fullmatch(r"[0-9]{8}", row["rcept_dt"])
                or row["corp_cls"] not in {"Y", "K"}
                or not row["corp_name"].strip() or not row["report_nm"].strip()
                or row["reason"] != "missing_stock_code" or row["rcept_no"] in saved_skips):
            raise ValueError("DART invalid saved skip archive")
        saved_skips.add(row["rcept_no"])
    if skipped_done - saved_skips:
        raise ValueError("DART completed skip archive is missing")
    skipped_done.update(saved_skips)
    if detail_done & skipped_done:
        raise ValueError("DART conflicting completed detail and skip receipts")
    listings = {}
    recovered_details = detail_done.copy()
    for day in days:
        path = _path(directory, "list", day)
        if not path.exists():
            if day in listing_done or _path(directory, "docs", day).exists():
                raise ValueError("DART completed listing archive is missing")
            continue
        rows = read_rows(path)
        receipts = set()
        for row in rows:
            if (not isinstance(row.get("rcept_no"), str)
                    or not re.fullmatch(r"[0-9]{14}", row["rcept_no"])
                    or row.get("rcept_dt") != day or row.get("corp_cls") not in {"Y", "K"}
                    or not isinstance(row.get("report_nm"), str) or not row["report_nm"].strip()
                    or row["rcept_no"] in receipts):
                raise ValueError("DART invalid saved listing archive")
            receipts.add(row["rcept_no"])
        selected = [row for row in rows if select_report(row["report_nm"])]
        document_path = _path(directory, "docs", day)
        if receipts & detail_done and not document_path.exists():
            raise ValueError("DART completed detail archive is missing")
        # Completed checkpoints are authoritative; load bodies only for a partial
        # day, including archive publication immediately before a crashed checkpoint.
        pending = any(row["rcept_no"] not in detail_done and row["rcept_no"] not in skipped_done
                      for row in selected)
        documents = read_rows(document_path) if pending else []
        saved = set() if pending else receipts & detail_done
        for document in documents:
            receipt = (document.get("metadata") or {}).get("rcept_no")
            if receipt not in receipts or receipt in saved or receipt in skipped_done:
                raise ValueError("DART invalid saved document receipt")
            saved.add(receipt)
        if (receipts & detail_done) - saved:
            raise ValueError("DART completed detail archive is missing")
        recovered_details.update(saved)
        listings[day] = selected
    # A crash between archive publication and checkpointing must not repeat requests.
    recovered = {"completed_listing_days": sorted(listing_done | set(listings)),
                 "completed_detail_rcept_nos": sorted(recovered_details),
                 "skipped_missing_stock_code_rcept_nos": sorted(skipped_done)}
    return state, recovered, listings


class _QuotaReached(Exception):
    pass


class _StructuredFileNotFound(Exception):
    pass


class DartBackfillCollector(DartDisclosureCollector):
    def __init__(self, api_key, directory, state, listings, *, session,
                 clock, sleeper, max_requests, request_interval, max_retries, timeout):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("DART API key is required for execution")
        # BaseCollector creates a session eagerly; the backfill creates it on first request.
        self.api_key = api_key
        self.session = session
        self._owns_session = session is None
        self.directory = directory
        self.state = state
        self.listings = listings
        self.clock = clock
        self.sleeper = sleeper
        self.max_requests = max_requests
        self.request_interval = request_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._structured_cache = {}
        self.quota = _read_json(directory / "_quota.json", {"days": {}})
        if not isinstance(self.quota.get("days"), dict):
            raise ValueError("DART invalid saved quota")
        for day, value in self.quota["days"].items():
            _date(day)
            if (not isinstance(value, dict) or type(value.get("requests")) is not int
                    or value["requests"] < 0 or type(value.get("provider_limited")) is not bool):
                raise ValueError("DART invalid saved daily quota")
        self.requests_made = 0
        self._request_day = None

    def _redacted(self, value):
        value = self._redact_api_keys(value)
        if isinstance(value, dict):
            return {key.replace(self.api_key, "[REDACTED]"): self._redacted(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._redacted(item) for item in value]
        return value

    def _request(self, url: str, params: dict):
        for attempt in range(self.max_retries):
            self.sleeper(self.request_interval)
            day = _kst(self.clock()).date().isoformat()
            counter = self.quota["days"].setdefault(day, {"requests": 0, "provider_limited": False})
            if counter["provider_limited"]:
                raise _QuotaReached("provider_status_020")
            if counter["requests"] >= self.max_requests:
                raise _QuotaReached("daily_request_budget")
            # Reserve before sending, including retries and requests with uncertain outcomes.
            counter["requests"] += 1
            _write_json(self.directory / "_quota.json", self.quota)
            self.requests_made += 1
            self._request_day = day
            try:
                if self.session is None:
                    self.session = requests.Session()
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response
            except (requests.RequestException, TimeoutError, ConnectionError) as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                transient = status is None or status in {408, 429} or status >= 500
                if not transient or attempt + 1 == self.max_retries:
                    raise DartAPIError("DART transport failure") from None
                self.sleeper(2 ** attempt)
            except Exception:
                raise DartAPIError("DART transport failure") from None

    def _payload(self, payload: dict, *, allow_file_not_found=False) -> dict:
        if isinstance(payload, dict) and payload.get("status") == "020":
            self.quota["days"][self._request_day]["provider_limited"] = True
            _write_json(self.directory / "_quota.json", self.quota)
            raise _QuotaReached("provider_status_020")
        if allow_file_not_found and isinstance(payload, dict) and payload.get("status") == "014":
            return self._redacted(payload)
        return self._redacted(read_dart_payload(SimpleNamespace(json=lambda: payload)))

    def _json_payload(self, response, *, allow_file_not_found=False) -> dict:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise DartAPIError("DART invalid JSON response") from None
        return self._payload(payload, allow_file_not_found=allow_file_not_found)

    def _listing(self, day: str) -> list[dict]:
        items = {}
        for corp_cls in ("Y", "K"):
            expected, page_no = None, 1
            while True:
                params = {**listing_params(day, self.api_key, page_no),
                          "corp_cls": corp_cls, "sort_mth": "asc"}
                payload = self._json_payload(self._request(self.LIST_URL, params))
                if payload["status"] == "013":
                    if page_no != 1:
                        raise DartAPIError("DART incomplete pagination: later page has no data")
                    break
                expected = _validate_page(payload, page_no, expected, day, items)
                if any(row["corp_cls"] != corp_cls for row in payload["list"]):
                    raise DartAPIError("DART listing market mismatch")
                if page_no == expected[1]:
                    break
                page_no += 1
        return list(items.values())

    def _structured(self, item: dict) -> dict:
        for endpoint in self._match_structured_endpoints(item["report_nm"]):
            corp_code, day = item["corp_code"], item["rcept_dt"]
            key = (endpoint, corp_code, day, day)
            if key not in self._structured_cache:
                payload = self._json_payload(self._request(
                    f"https://opendart.fss.or.kr/api/{endpoint}.json",
                    {"crtfc_key": self.api_key, "corp_code": corp_code, "bgn_de": day, "end_de": day}),
                    allow_file_not_found=True)
                if payload["status"] == "014":
                    raise _StructuredFileNotFound
                self._structured_cache[key] = payload
            payload = self._structured_cache[key]
            if payload["status"] == "013":
                continue
            rows = payload.get("list")
            if not isinstance(rows, list):
                raise DartAPIError("DART invalid structured rows")
            target = self._find_structured_row(rows, item["rcept_no"])
            if target is None:
                continue
            if target.get("corp_code") not in (None, "", corp_code):
                raise DartAPIError("DART structured corporate code mismatch")
            return {"structured_endpoint": endpoint, "structured_rcept_no": item["rcept_no"],
                    "structured_row": target}
        return {}

    def _document(self, receipt: str) -> tuple:
        response = self._request(self.DOCUMENT_URL, {"crtfc_key": self.api_key, "rcept_no": receipt})
        blob = response.content or b""
        if not blob.startswith(b"PK"):
            if blob.lstrip().startswith(b"<"):
                try:
                    root = ElementTree.fromstring(blob)
                except ElementTree.ParseError:
                    raise DartAPIError("DART invalid document status XML") from None
                statuses = [node.text for node in root.iter() if node.tag.split("}")[-1] == "status"]
                payload = self._payload({"status": statuses[0] if len(statuses) == 1 else None},
                                        allow_file_not_found=True)
            else:
                payload = self._json_payload(response, allow_file_not_found=True)
            if payload["status"] in {"013", "014"}:
                error_type = "dart_014_file_not_found" if payload["status"] == "014" else "official_no_data"
                return "", {"body_error_type": error_type, "encoding_fixed": False,
                            "mojibake_detected": False}
            raise DartAPIError("DART official document missing ZIP payload")
        try:
            with ZipFile(BytesIO(blob)) as archive:
                best_text, best_score, quality = "", -1, {}
                for name in self._select_document_inner_files(archive.namelist()):
                    decoded, encoding_fixed, mojibake = self._decode_bytes_with_candidates(archive.read(name))
                    text = self._sanitize_body_text(self._normalize_inner_document_text(name, decoded))
                    if (not text or self._looks_like_toc_only(text) or self._contains_error_page_tokens(text)
                            or self._contains_wrapper_tokens(text) or self._is_mojibake_text(text)):
                        continue
                    score = self._document_text_score(text)
                    if score > best_score:
                        best_text, best_score = text, score
                        quality = {"body_error_type": "success", "encoding_fixed": encoding_fixed,
                                   "mojibake_detected": mojibake}
        except (BadZipFile, RuntimeError, OSError, KeyError):
            raise DartAPIError("DART official document ZIP extraction failed") from None
        if not best_text:
            raise DartAPIError("DART official document has no usable body")
        return self._redacted(best_text), quality

    def _detail(self, item: dict) -> dict:
        if not item.get("stock_code"):
            raise DartAPIError("DART selected disclosure is missing stock_code")
        try:
            structured = self._structured(item)
        except _StructuredFileNotFound:
            structured = {}
            content, quality = "", {"body_error_type": "dart_014_structured_file_not_found",
                                    "encoding_fixed": False, "mojibake_detected": False}
        else:
            content, quality = self._document(item["rcept_no"])
        collected = _kst(self.clock()).isoformat()
        metadata = {"rcept_no": item["rcept_no"], "report_nm": item["report_nm"],
                    "rcept_dt": item["rcept_dt"], "corp_cls": item["corp_cls"],
                    "corp_code": item["corp_code"], "corp_name": item["corp_name"],
                    "first_seen_at": None, "published_at_precision": "date",
                    "published_at_source": "dart_list.rcept_dt", "collected_at": collected,
                    "body_source": "official_api", "has_body": bool(content),
                    "body_extracted": bool(content), **quality, **structured}
        record = {"source_type": "dart", "title": item["report_nm"], "content": content,
                  "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item['rcept_no']}",
                  "published_at": _date(item["rcept_dt"]).isoformat(), "stock_code": item["stock_code"],
                  "rcept_dt": item["rcept_dt"], "first_seen_at": None, "collected_at": collected,
                  "body_source": "official_api", "body_error_type": quality["body_error_type"],
                  "selection_category": detail_category(item["report_nm"]),
                  "metadata": metadata}
        if len(content) > MAX_BODY_CHARACTERS:
            record["content"] = content[:MAX_BODY_CHARACTERS]
            record["body_truncated_at"] = metadata["body_truncated_at"] = MAX_BODY_CHARACTERS
        return self._redacted(record)

    def run(self, days: list[str], stage: str, categories=None) -> dict:
        summary = {"status": "ok", "dry_run": False, "stage": stage,
                   "listing_days_saved": 0, "listed_rows": 0, "details_saved": 0, "bodies_unavailable": 0,
                   "skipped_missing_stock_code": 0, "documents_not_found": 0}
        try:
            if stage in {"all", "list"}:
                for day in days:
                    if day in self.listings:
                        continue
                    rows = self._listing(day)
                    write_rows(_path(self.directory, "list", day), rows)
                    self.listings[day] = [row for row in rows if select_report(row["report_nm"])]
                    self.state["completed_listing_days"] = sorted(self.listings.keys() |
                        set(self.state["completed_listing_days"]))
                    _write_json(self.directory / "_state.json", self.state)
                    summary["listing_days_saved"] += 1
                    summary["listed_rows"] += len(rows)
            if stage in {"all", "details"}:
                completed = set(self.state["completed_detail_rcept_nos"])
                skipped = set(self.state["skipped_missing_stock_code_rcept_nos"])
                skipped_path = self.directory / "skipped_missing_stock_code.jsonl"
                skipped_rows = read_rows(skipped_path)
                category_days = ((category, day) for category in categories or (None,) for day in days)
                for category, day in category_days:
                    self._structured_cache.clear()
                    pending = [item for item in self.listings.get(day, [])
                               if item["rcept_no"] not in completed and item["rcept_no"] not in skipped
                               and (category is None or detail_category(item["report_nm"]) == category)]
                    if not pending:
                        continue
                    stored = read_rows(_path(self.directory, "docs", day))
                    for item in pending:
                        if not item.get("stock_code"):
                            record = self._redacted({field: item[field] for field in
                                ("rcept_no", "corp_code", "corp_name", "corp_cls", "report_nm", "rcept_dt")})
                            record["reason"] = "missing_stock_code"
                            write_rows(skipped_path, skipped_rows + [record])
                            skipped_rows.append(record)
                            skipped.add(item["rcept_no"])
                            self.state["skipped_missing_stock_code_rcept_nos"] = sorted(skipped)
                            _write_json(self.directory / "_state.json", self.state)
                            summary["skipped_missing_stock_code"] += 1
                            continue
                        record = self._detail(item)
                        write_rows(_path(self.directory, "docs", day), stored + [record])
                        stored.append(record)
                        completed.add(item["rcept_no"])
                        self.state["completed_detail_rcept_nos"] = sorted(completed)
                        _write_json(self.directory / "_state.json", self.state)
                        summary["details_saved"] += 1
                        summary["bodies_unavailable"] += record["body_error_type"] != "success"
                        summary["documents_not_found"] += record["body_error_type"] in {
                            "dart_014_file_not_found", "dart_014_structured_file_not_found"}
        except _QuotaReached as error:
            summary.update(status="quota_reached", reason=str(error))
        except DartAPIError as error:
            summary.update(status="error", error=self._redacted(str(error)))
        finally:
            if self._owns_session and self.session is not None:
                self.session.close()
        completed = (set(self.state["completed_detail_rcept_nos"])
                     | set(self.state["skipped_missing_stock_code_rcept_nos"]))
        summary.update(requests_made=self.requests_made,
                       unlisted_days=[day for day in days if day not in self.listings],
                       known_pending_details=sum(select_report(item["report_nm"]) and
                           item["rcept_no"] not in completed
                           for rows in self.listings.values() for item in rows))
        return summary


def backfill(from_date, to_date, *, stage="all", execute=False, data_dir=DEFAULT_DATA_DIR,
             api_key=None, session=None, clock=None, sleeper=None, max_requests=DEFAULT_MAX_REQUESTS,
             request_interval=0.2, max_retries=3, timeout=20, categories=None) -> dict:
    """Print a read-only plan by default; budgets count attempts across KST-day runs.

    `all` finishes the listing stage before details. `details` only uses saved
    lists and reports unlisted dates. Explicit 013 document and 014 detail responses
    are stored as empty, labelled bodies; invalid/failed requests remain pending.
    Nonempty `categories` limit details in the given category order, then by date;
    excluded receipts remain pending. No categories preserves date-first processing.
    """
    start, end = _date(from_date), _date(to_date)
    now = clock if clock is not None else lambda: datetime.now(KST)
    if start > end or end >= _kst(now()).date():
        raise ValueError("DART historical range must be ordered and end before today in Korea")
    if stage not in {"all", "list", "details"}:
        raise ValueError("DART stage must be all, list or details")
    valid_categories = [name for name, _ in SELECTION_PATTERNS]
    categories = tuple(dict.fromkeys(categories)) if categories is not None else ()
    if any(name not in valid_categories for name in categories):
        raise ValueError(f"DART unknown detail category; valid names: {', '.join(valid_categories)}")
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("DART max_requests must be a positive integer")
    if type(max_retries) is not int or max_retries < 1:
        raise ValueError("DART max_retries must be a positive integer")
    for value in (request_interval, timeout):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("DART request interval and timeout must be positive and finite")
    days = [(start + timedelta(days=offset)).strftime("%Y%m%d") for offset in range((end - start).days + 1)]
    directory = Path(data_dir) / "disclosures" / "dart_full"
    if not execute:
        _, state, listings = _progress(directory, days)
        completed = (set(state["completed_detail_rcept_nos"])
                     | set(state["skipped_missing_stock_code_rcept_nos"]))
        pending_by_category = dict.fromkeys(valid_categories, 0)
        for rows in listings.values():
            for item in rows:
                if item["rcept_no"] not in completed:
                    pending_by_category[detail_category(item["report_nm"])] += 1
        plan = {"status": "dry_run", "dry_run": True, "stage": stage,
                "from_date": start.isoformat(), "to_date": end.isoformat(), "data_dir": str(directory),
                "days_to_list": [day for day in days if day not in listings] if stage != "details" else [],
                "unlisted_days": [day for day in days if day not in listings],
                "known_pending_details": sum(pending_by_category.values()),
                "categories": list(categories) if categories else None,
                "pending_details_by_category": pending_by_category,
                "filtered_pending_details": sum(pending_by_category[name]
                    for name in categories or valid_categories), "max_requests": max_requests,
                "skipped_missing_stock_code": 0}
        print(json.dumps(plan, ensure_ascii=False))
        return plan
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("DART API key is required for execution")
    with file_lock(directory / ".backfill.lock"):
        previous, state, listings = _progress(directory, days)
        collector = DartBackfillCollector(api_key, directory, state, listings,
            session=session, clock=now, sleeper=sleeper if sleeper is not None else time.sleep,
            max_requests=max_requests, request_interval=request_interval, max_retries=max_retries, timeout=timeout)
        if state != previous:
            _write_json(directory / "_state.json", state)
        return collector.run(days, stage, categories)
