"""Receipt-specific, point-in-time business sections for LH001 section 4.2.

Readers only use local listings and extracted sections. Fetching is opt-in and
shares dart_backfill's KST daily quota and execution lock. Raw ZIPs are not saved.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from src.utils.stock_codes import is_stock_code

from .dart import BeautifulSoup, DartDisclosureCollector
from .dart_api import DartAPIError, DartProviderMaintenance
from .dart_backfill import DartBackfillCollector, KST, _date, _QuotaReached, _write_json
from .storage import file_lock, read_rows


EXTRACTION_VERSION = "dart-business-text-v1"
_REPORT = re.compile(
    r"^(?:\[(?:기재정정|첨부정정)\]\s*)*"
    r"(사업보고서|반기보고서|분기보고서)\s*\(\s*(\d{4})\.\s*(\d{2})\s*\)$"
)
_BUSINESS = re.compile(r"(?<!\S)(?:II|Ⅱ)\s*[.．]?\s*사업의\s*내용", re.IGNORECASE)
_CHAPTER = re.compile(r"(?<!\S)[IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]\s*[가-힣]", re.IGNORECASE)
_NUMBERED = re.compile(r"(?<!\S)\d{1,2}\s*[.．)]\s*[가-힣]")
_OVERVIEW = re.compile(r"(?<!\S)1\s*[.．)]\s*사업의\s*개요")
_PRODUCTS = re.compile(
    r"(?<!\S)(?:\d{1,2}\s*[.．)]\s*)?주요\s*제품"
    r"(?:\s*및\s*서비스|\s*등(?:의)?\s*현황)"
)
_OMITTED = re.compile(r"기재\s*(?:를|는)?\s*생략|사업보고서[\s\S]{0,120}?(?:참조|참고)")


def _report_title(title: str) -> tuple[str, str] | None:
    if not isinstance(title, str):
        raise ValueError("DART report name must be a string")
    match = _REPORT.fullmatch(DartDisclosureCollector._clean_text(title))
    if not match:
        return None
    kind, year, month = match.groups()
    _date(f"{year}{month}01")
    return kind, f"{year}.{month}"


def _validate_stock(stock_code) -> None:
    if not is_stock_code(stock_code):
        raise ValueError("DART stock code must be six uppercase ASCII letters or digits")


def _reports_by_stock(stock_codes: set[str], before, data_dir) -> dict[str, list[dict]]:
    reports = {code: {} for code in stock_codes}
    directory = Path(data_dir) / "disclosures" / "dart_full" / "list"
    for path in sorted(directory.glob("*/*.jsonl")):
        archive_day = _date(path.stem)
        if archive_day >= before:
            continue
        for row in read_rows(path):
            code = row.get("stock_code")
            if code not in stock_codes:
                continue
            title = _report_title(row.get("report_nm"))
            if title is None:
                continue
            filed = _date(row.get("rcept_dt"))
            if filed != archive_day:
                raise ValueError("DART listing date does not match its archive")
            receipt = row.get("rcept_no")
            if not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt):
                raise ValueError("DART invalid receipt number")
            report = {**row, "period": title[1]}
            if receipt in reports[code] and reports[code][receipt] != report:
                raise ValueError("DART conflicting periodic report listings")
            reports[code][receipt] = report
    return {code: sorted(rows.values(), key=lambda row: (row["rcept_dt"], row["rcept_no"]), reverse=True)
            for code, rows in reports.items()}


def periodic_reports(stock_code, before, data_dir) -> list[dict]:
    """Return periodic listing rows in filing order, with period as YYYY.MM.

    The cutoff is exclusive; no filing on the decision date is eligible.
    """
    _validate_stock(stock_code)
    return _reports_by_stock({stock_code}, _date(before), data_dir)[stock_code]


def _select(reports: list[dict]) -> dict | None:
    return max(reports, key=lambda row: (row["period"], row["rcept_dt"], row["rcept_no"]), default=None)


def select_report(stock_code, decision_date, data_dir) -> dict | None:
    """Choose the newest period, then its latest eligible filed version."""
    return _select(periodic_reports(stock_code, decision_date, data_dir))


def _annual(reports: list[dict]) -> dict | None:
    # Fallback is the most recently filed annual report, including corrections.
    return next((row for row in reports if _report_title(row["report_nm"])[0] == "사업보고서"), None)


def _clean_rows(text: str) -> str:
    return "\n".join(cleaned for line in text.splitlines()
                     if (cleaned := DartDisclosureCollector._clean_text(line)))


def _plain_text(text: str) -> tuple[str, set[str]]:
    headings = set()
    if re.search(r"<\s*[A-Za-z][^>]*>", text):
        if BeautifulSoup is None:
            raise ValueError("BeautifulSoup is required to extract DART markup")
        parser = "xml" if re.search(r"<\?xml|<DOCUMENT\b", text, re.IGNORECASE) else "html.parser"
        soup = BeautifulSoup(text, parser)
        for tag in soup.find_all(lambda tag: tag.name.lower() in {"style", "script"}):
            tag.decompose()
        headings = {DartDisclosureCollector._clean_text(tag.get_text(" ", strip=True))
                    for tag in soup.find_all(lambda tag: tag.name.lower() in
                                             {"title", "h1", "h2", "h3", "h4", "h5", "h6"})}
        for table in reversed(soup.find_all(lambda tag: tag.name.lower() == "table")):
            rows = [" ".join(DartDisclosureCollector._clean_text(cell.get_text(" ", strip=True))
                             for cell in row.find_all(lambda tag: tag.name.lower() in {"td", "th", "te", "tu"}))
                    for row in table.find_all(lambda tag: tag.name.lower() == "tr")]
            table.replace_with("\n" + "\n".join(rows) + "\n")
        blocks = {"p", "div", "title", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                  "section-1", "section-2", "section-3"}
        for tag in soup.find_all(lambda tag: tag.name.lower() in blocks):
            tag.insert_before("\n")
            tag.insert_after("\n")
        text = soup.get_text(" ")
    return _clean_rows(DartDisclosureCollector._strip_leading_css(text)), headings


def extract_sections(document_text) -> dict:
    """Extract from the business chapter; retain paragraph/table-row newlines.

    The final business-chapter heading avoids the earlier table of contents.
    `omitted` describes the overview, not an independently omitted product table.
    Both raw XML/HTML and dart.py's flattened plain text are accepted.
    """
    text, headings = _plain_text(document_text)
    if not any(_OVERVIEW.match(label) or _PRODUCTS.match(label) for label in headings):
        headings = set()

    def next_heading(pattern, text, start):
        return next((match for match in pattern.finditer(text, start)
                     if not headings or text[match.start():].split("\n", 1)[0].strip() in headings), None)

    chapters = list(_BUSINESS.finditer(text))
    if not chapters:
        return {"overview": None, "products": None, "omitted": False}
    start = chapters[-1].end()
    end = next_heading(_CHAPTER, text, start)
    chapter = text[start:end.start() if end else len(text)]
    overview_heading, products_heading = _OVERVIEW.search(chapter), _PRODUCTS.search(chapter)

    def section(heading):
        if heading is None:
            return None
        following = next_heading(_NUMBERED, chapter, heading.end())
        end = following.start() if following else len(chapter)
        if products_heading and heading is overview_heading and products_heading.start() > heading.end():
            end = min(end, products_heading.start())
        body = _clean_rows(chapter[heading.end():end])
        return None if not body or re.fullmatch(r"[-.·…\s]*[0-9]+", body) else body

    overview, products = section(overview_heading), section(products_heading)
    omitted = bool(_OMITTED.search(overview if overview is not None else chapter))
    if products and _OMITTED.search(products):
        products = None
    return {"overview": overview, "products": products, "omitted": omitted}


def _sections_path(data_dir, receipt: str) -> Path:
    return Path(data_dir) / "disclosures" / "business_text" / f"{receipt}.json"


def _read_sections(data_dir, receipt: str) -> dict | None:
    path = _sections_path(data_dir, receipt)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("DART invalid saved business sections JSON") from None
    if (not isinstance(record, dict) or record.get("rcept_no") != receipt
            or record.get("extraction_version") != EXTRACTION_VERSION
            or not {"overview", "products"}.issubset(record)
            or record.get("status") not in ("000", "013", "014")
            or type(record.get("omitted")) is not bool
            or any(record.get(key) is not None and not isinstance(record[key], str)
                   for key in ("overview", "products"))
            or (record["status"] != "000" and
                (record["overview"] is not None or record["products"] is not None))):
        raise ValueError("DART invalid or incompatible saved business sections")
    return record


def _usable(record) -> bool:
    return record is not None and bool((record.get("overview") or "").strip()) and not record["omitted"]


def load_business_text(stock_code, decision_date, data_dir) -> dict | None:
    """Read an eligible overview or annual fallback; never fetch or write files.

    An unavailable selected extraction can only use the specified annual fallback.
    Metadata identifies the receipt that actually supplied the text.
    """
    reports = periodic_reports(stock_code, decision_date, data_dir)
    selected = _select(reports)
    if selected is None:
        return None
    source, record = selected, _read_sections(data_dir, selected["rcept_no"])
    if not _usable(record):
        source = _annual(reports)
        if source is None:
            return None
        record = _read_sections(data_dir, source["rcept_no"])
        if not _usable(record):
            return None
    text = record["overview"][:3000]
    if record.get("products"):
        text += "\n[주요 제품 및 서비스]\n" + record["products"][:1000]
    return {"text": text, **{key: source[key] for key in ("report_nm", "rcept_no", "rcept_dt", "period")},
            "fallback_used": source["rcept_no"] != selected["rcept_no"], "chars": len(text),
            "sha256": sha256(text.encode("utf-8")).hexdigest(), "extraction_version": EXTRACTION_VERSION}


def _receipt_plan(receipts, data_dir) -> dict:
    needed = list(dict.fromkeys(receipts))
    if any(not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt) for receipt in needed):
        raise ValueError("DART invalid receipt number")
    stored = [receipt for receipt in needed if _read_sections(data_dir, receipt) is not None]
    stored_set = set(stored)
    missing = [receipt for receipt in needed if receipt not in stored_set]
    return {"needed": len(needed), "stored": len(stored), "missing": len(missing),
            "estimated_requests": len(missing), "needed_rcept_nos": needed,
            "stored_rcept_nos": stored, "missing_rcept_nos": missing}


def _now():
    return datetime.now(KST)


def _download_sections(collector, receipt: str) -> dict:
    response = collector._request(collector.DOCUMENT_URL, {"crtfc_key": collector.api_key, "rcept_no": receipt})
    blob = response.content or b""
    record = {"rcept_no": receipt, "raw_sha256": sha256(blob).hexdigest(), "byte_size": len(blob),
              "extraction_version": EXTRACTION_VERSION, "fetched_at": _now().isoformat()}
    if not blob.startswith(b"PK"):
        if blob.lstrip().startswith(b"<"):
            try:
                root = ElementTree.fromstring(blob)
            except ElementTree.ParseError:
                raise DartAPIError("DART invalid document status XML") from None
            statuses = [node.text for node in root.iter() if node.tag.split("}")[-1] == "status"]
            payload = collector._payload({"status": statuses[0] if len(statuses) == 1 else None},
                                         allow_file_not_found=True)
        else:
            payload = collector._json_payload(response, allow_file_not_found=True)
        if payload["status"] not in {"013", "014"}:
            raise DartAPIError("DART official document missing ZIP payload")
        return {**record, "overview": None, "products": None, "omitted": False,
                "status": payload["status"], "error": "official_no_data" if payload["status"] == "013"
                else "dart_014_file_not_found"}
    best, best_score = None, None
    try:
        with ZipFile(BytesIO(blob)) as archive:
            for name in collector._select_document_inner_files(archive.namelist()):
                decoded, _, _ = collector._decode_bytes_with_candidates(archive.read(name))
                plain = collector._normalize_inner_document_text(name, decoded)
                if (collector._contains_error_page_tokens(plain) or collector._contains_wrapper_tokens(plain)
                        or collector._is_mojibake_text(plain)):
                    continue
                sections = extract_sections(decoded)
                if (collector._looks_like_toc_only(plain) and not sections["omitted"]
                        and not re.search(r"[가-힣A-Za-z]", sections["overview"] or "")):
                    continue
                # Prefer the report's business chapter over a longer financial attachment.
                # A receipt-named main file also takes precedence over older attachments.
                score = (Path(name).stem == receipt, bool(_BUSINESS.search(plain)),
                         bool(sections["overview"]), bool(sections["products"]),
                         collector._document_text_score(plain))
                if best_score is None or score > best_score:
                    best, best_score = sections, score
    except (BadZipFile, RuntimeError, OSError, KeyError):
        raise DartAPIError("DART official document ZIP extraction failed") from None
    if best is None:
        raise DartAPIError("DART official document has no usable body")
    collector._provider_status("000")
    return {**record, **best, "status": "000"}


class BusinessTextCollector:
    def __init__(self, api_key, session=None):
        self.api_key, self.session = api_key, session

    def plan(self, requests, data_dir) -> dict:
        decisions = []
        for stock_code, decision_date in requests:
            _validate_stock(stock_code)
            decisions.append((stock_code, _date(decision_date)))
        if not decisions:
            return {**_receipt_plan([], data_dir), "unavailable_requests": []}
        reports = _reports_by_stock({code for code, _ in decisions}, max(day for _, day in decisions), data_dir)
        needed, unavailable = set(), []
        for code, day in decisions:
            eligible = [row for row in reports[code] if _date(row["rcept_dt"]) < day]
            selected = _select(eligible)
            if selected is None:
                unavailable.append({"stock_code": code, "decision_date": day.isoformat()})
                continue
            needed.add(selected["rcept_no"])
            if not _usable(_read_sections(data_dir, selected["rcept_no"])):
                annual = _annual(eligible)
                if annual is not None:
                    needed.add(annual["rcept_no"])
        return {**_receipt_plan(sorted(needed, reverse=True), data_dir), "unavailable_requests": unavailable}

    def fetch(self, rcept_nos, data_dir, max_requests, execute=False) -> dict:
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("DART max_requests must be a positive integer")
        plan = _receipt_plan(rcept_nos, data_dir)
        summary = {**plan, "status": "dry_run" if not execute else "ok", "dry_run": not execute,
                   "max_requests": max_requests, "requests_made": 0, "saved": 0, "errors": 0,
                   "remaining": plan["missing"]}
        if not execute:
            return summary
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("DART API key is required for execution")
        if not plan["missing"]:
            return summary
        directory = Path(data_dir) / "disclosures" / "dart_full"
        with file_lock(directory / ".backfill.lock"):
            collector = DartBackfillCollector(self.api_key, directory, {}, {}, session=self.session,
                clock=_now, sleeper=time.sleep, max_requests=max_requests, request_interval=0.2,
                max_retries=1, timeout=20)
            try:
                missing = _receipt_plan(plan["missing_rcept_nos"], data_dir)["missing_rcept_nos"]
                summary["remaining"] = len(missing)
                for receipt in missing:
                    record = _download_sections(collector, receipt)
                    _write_json(_sections_path(data_dir, receipt), record)
                    summary["saved"] += 1
                    summary["errors"] += record["status"] in {"013", "014"}
                    summary["remaining"] -= 1
            except _QuotaReached as error:
                summary.update(status="quota_reached", reason=str(error))
            except DartProviderMaintenance:
                summary.update(status="provider_maintenance", reason="provider_status_800")
            finally:
                summary["requests_made"] = collector.requests_made
                if collector._owns_session and collector.session is not None:
                    collector.session.close()
        return summary
