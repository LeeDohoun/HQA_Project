"""Date provenance for saved financial versions, without rewriting archives.

The original-date rule is a sensitivity assumption: saved corrected *values*
are assigned to the original filing date. It does not recover original values.
"""
from __future__ import annotations

import re
from datetime import datetime

from .dart_listing import listing_paths, listing_rows


_PERIOD = re.compile(r"(분기보고서|반기보고서|사업보고서)\s*\(\s*(\d{4})\.\s*(\d{2})\s*\)")


def periodic_period(title):
    if not isinstance(title, str):
        return None
    match = _PERIOD.search(title)
    if not match:
        return None
    kind, year, month = match.groups()
    datetime.strptime(year + month + "01", "%Y%m%d")
    return kind, year + "." + month


def known_date(receipt, listed=None):
    if not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt):
        raise ValueError("DART invalid financial receipt number")
    prefix = datetime.strptime(receipt[:8], "%Y%m%d").date()
    official = datetime.strptime(listed, "%Y%m%d").date() if listed is not None else prefix
    return max(prefix, official).isoformat()


def provenance_index(data_dir, *, listings=None):
    if listings is None:
        listings = (row for day in listing_paths(data_dir) for row in listing_rows(data_dir, day))
    receipts, originals = {}, {}
    for row in listings:
        receipt = row["rcept_no"]
        # A linked receipt supplies its date/correction flag even when its
        # title has no parsable periodic-report period. Keep compact identities
        # for the full list; original-period matching uses only periodic rows.
        record = {name: row.get(name) for name in ("corp_code", "rcept_no", "rcept_dt", "report_nm")}
        if receipt in receipts and receipts[receipt] != record:
            raise ValueError("DART conflicting periodic receipt provenance")
        receipts[receipt] = record
        period = periodic_period(record["report_nm"])
        if period is None:
            continue
        if "정정" not in row["report_nm"]:
            key = row["corp_code"], *period
            if key not in originals or (row["rcept_dt"], receipt) < (originals[key]["rcept_dt"], originals[key]["rcept_no"]):
                originals[key] = record
    return receipts, originals


def annotate_versions(records, *, data_dir=None, index=None):
    """Return copies of every stored CFS/OFS version with receipt-linked dates."""
    receipts, originals = provenance_index(data_dir) if index is None else index
    result = []
    for record in records:
        receipt = record["rcept_no"]
        stored = receipts.get(receipt)
        if stored is not None and stored.get("corp_code") != record["corp_code"]:
            raise ValueError("DART financial/listing corporation mismatch")
        listed = stored["rcept_dt"] if stored is not None else None
        correction = "정정" in stored["report_nm"] if stored is not None else None
        period = periodic_period(stored["report_nm"]) if stored is not None else None
        original = originals.get((record["corp_code"], *period)) if period else None
        date_stored = known_date(receipt, listed)
        date_original = known_date(original["rcept_no"], original["rcept_dt"]) if correction and original else date_stored
        result.append({**record, "list_rcept_dt": listed, "is_correction": correction,
                       "report_type": period[0] if period else None, "report_period": period[1] if period else None,
                       "original_rcept_no": original["rcept_no"] if original else None,
                       "original_rcept_dt": original["rcept_dt"] if original else None,
                       "known_date_stored": date_stored, "known_date_original": date_original})
    return result
