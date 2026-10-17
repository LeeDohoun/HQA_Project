"""AV001 section 7 / EV001 section 6 financial-version date rules.

The full merged listing is used only for receipt/date provenance and L. Actual
dates gate the stage before any financial values are derived. O is an explicit
assumption about corrected values, not reconstruction of historical accounts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
from itertools import groupby
from pathlib import Path

import pandas as pd

from .dart_filing_dates import known_date, periodic_period
from .dart_listing import START, listing_paths, listing_rows
from .storage import read_rows


STAGE_CUTOFFS = {"design_validation": "2026-01-01", "holdout": "2026-07-01"}


class FilingDates:
    def __init__(self, listings, *, conflicts=()):
        self.receipts, self.originals = {}, {}
        self.conflicts = conflicts if isinstance(conflicts, set) else set(conflicts)
        lags = []
        fingerprints = {}
        for row in listings:
            receipt = row["rcept_no"]
            actual = known_date(receipt, row["rcept_dt"])
            lags.append((pd.Timestamp(row["rcept_dt"]) - pd.Timestamp(receipt[:8])).days)
            record = {key: row.get(key) for key in
                      ("corp_code", "rcept_no", "rcept_dt", "report_nm", "stock_code")}
            fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).digest()
            if receipt in fingerprints and fingerprints[receipt] != fingerprint:
                self.conflicts.add(receipt)
            fingerprints[receipt] = fingerprint
            self.receipts[receipt] = record
        if not lags:
            raise ValueError("L is undefined: the merged listing has no dated rows")
        self.L = int(max(lags))
        for receipt, row in self.receipts.items():
            period = periodic_period(row["report_nm"])
            if receipt in self.conflicts or period is None or "정정" in row["report_nm"]:
                continue
            key = (row["corp_code"], *period)
            rank = known_date(receipt, row["rcept_dt"]), receipt
            if key not in self.originals or rank < self.originals[key][0]:
                self.originals[key] = rank, row

    @classmethod
    def from_data(cls, data_dir):
        conflicts = set()

        def rows():
            for day in listing_paths(data_dir):
                yield from listing_rows(data_dir, day, conflicts=conflicts)

        result = cls(rows(), conflicts=conflicts)
        # The generator discovers conflicts while being consumed.
        result.conflicts.update(conflicts)
        for key, (_, row) in list(result.originals.items()):
            if row["rcept_no"] in result.conflicts:
                del result.originals[key]
        return result

    def annotate(self, record):
        receipt = record["rcept_no"]
        prefix = known_date(receipt)
        linked = self.receipts.get(receipt)
        correction = "정정" in linked["report_nm"] if linked is not None else None
        period = periodic_period(linked["report_nm"]) if linked is not None else None
        if receipt in self.conflicts or linked is not None and linked["corp_code"] != record["corp_code"]:
            actual, reason = None, "conflicting_link"
        elif linked is not None:
            actual, reason = known_date(receipt, linked["rcept_dt"]), "linked"
        elif receipt[:8] < START:
            actual = (date.fromisoformat(prefix) + timedelta(days=self.L)).isoformat()
            reason = "unlinked_pre_listing_plus_L"
        else:
            actual, reason = None, "unlinked_since_listing_start"
        original = self.originals.get((record["corp_code"], *period)) if correction and period else None
        assumed = original[0][0] if original is not None and actual is not None else actual
        return {"actual_public_date": actual, "assumed_public_date": assumed,
                "date_reason": reason, "is_correction": correction,
                "correction_link": "success" if correction and original is not None and actual is not None
                                   else "failure" if correction else "unknown" if linked is None else "not_correction",
                "original_rcept_no": original[1]["rcept_no"] if original is not None else None}

    def event_listings(self):
        return pd.DataFrame([row for receipt, row in self.receipts.items() if receipt not in self.conflicts],
                            columns=("rcept_no", "rcept_dt", "stock_code", "report_nm"))


class FinancialVersions:
    """Derive one business year at a time, keeping only compact usable outputs.

    This uses the existing quarterly derivation/ranking, including CFS priority
    and OFS fallback. Decisions filter the same stored versions under S or O.
    """
    def __init__(self, data_dir, dates, rule, stage, *, derive=True, sample_end=None, cutoff=None):
        from . import dart_quarterly as quarterly

        if rule not in ("S", "O") or stage not in STAGE_CUTOFFS:
            raise ValueError("financial rules require S/O and an explicit registered stage")
        self.rule, self.stage = rule, stage
        self.rows, self.annotations, self.correction_receipts = [], [], set()
        self.counts = Counter()
        self.by_year = defaultdict(Counter)
        self.sample_counts = Counter()
        registered_cutoff = STAGE_CUTOFFS[stage]
        cutoff = registered_cutoff if cutoff is None else pd.Timestamp(cutoff).date().isoformat()
        if cutoff > registered_cutoff:
            raise ValueError("financial cutoff exceeds the registered stage")
        self.cutoff = cutoff
        self.sample_end = cutoff if sample_end is None else pd.Timestamp(sample_end).date().isoformat()
        if self.sample_end > cutoff:
            raise ValueError("correction sample end exceeds the registered stage")
        paths = sorted((Path(data_dir) / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
        # Archive year is an additional bound; actual public date is authoritative.
        last_year = 2025 if stage == "design_validation" else 2026
        paths = [p for p in paths if int(p.stem[:4]) <= last_year]
        if not paths:
            raise ValueError("quarterly financial archives are missing")
        self.paths = paths
        for year, files in groupby(paths, key=lambda p: p.stem[:4]):
            records = []
            for path in files:
                for record in read_rows(path):
                    if str(record["bsns_year"]) != year or record["available_date"] != quarterly._filing_date(record["rcept_no"]):
                        raise ValueError("quarterly archive year/receipt availability mismatch")
                    info = dates.annotate(record)
                    actual = info["actual_public_date"]
                    eligible = actual is not None and actual < cutoff
                    reason = info["date_reason"]
                    receipt_year = record["rcept_no"][:4]
                    for label in (reason, info["correction_link"], "stage_eligible" if eligible else
                                  "stage_cutoff" if actual is not None else "unusable"):
                        self.counts[label] += 1
                        self.by_year[receipt_year][label] += 1
                    # The registered sample consists of stage-eligible versions
                    # available under at least one rule by the last decision.
                    if (eligible and min(actual, info["assumed_public_date"]) < self.sample_end
                            and info["is_correction"]):
                        self.sample_counts[info["correction_link"]] += 1
                    self.annotations.append({"corp_code": record["corp_code"], "fs_div": record["fs_div"],
                        "fiscal_quarter": record["fiscal_quarter"], "rcept_no": record["rcept_no"],
                        "stock_codes": record["stock_codes"], **info})
                    if eligible and info["is_correction"]:
                        self.correction_receipts.add(record["rcept_no"])
                    if eligible and derive:
                        available = actual if rule == "S" else info["assumed_public_date"]
                        records.append({**record, "available_date": available})
            if derive:
                for row in quarterly._derive(records):
                    self.rows.append({key: row[key] for key in (*quarterly.COLUMNS[1:], "stock_codes")})
        self.rows.sort(key=lambda row: (row["corp_code"], row["fiscal_quarter"], row["available_date"], row["rcept_no"]))

    @property
    def sensitivity_available(self):
        return not self.sample_counts["failure"] or self.sample_counts["success"] > 0

    def report(self):
        successes, failures = self.sample_counts["success"], self.sample_counts["failure"]
        return {"rule": self.rule, "stage": self.stage, "counts": dict(self.counts),
                "sample_end_exclusive": self.sample_end, "stored_versions": len(self.annotations),
                "by_receipt_year": {year: dict(counts) for year, counts in sorted(self.by_year.items())},
                "sample_correction_links": {"success": successes, "failure": failures,
                    "success_rate": successes / (successes + failures) if successes + failures else None},
                "sensitivity_available": self.sensitivity_available}

    def load(self, as_of, *, data_dir=None):
        from . import dart_quarterly as quarterly

        stamp = pd.Timestamp(as_of)
        if pd.isna(stamp):
            raise ValueError("financial decision session is required")
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(quarterly.KST)
        day = stamp.date().isoformat()
        if day >= self.cutoff:
            raise ValueError("financial decision lies outside the registered stage")
        latest = {}
        for row in self.rows:
            if row["available_date"] >= day:
                continue
            key = row["corp_code"], row["fiscal_quarter"]
            rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
            if key not in latest or rank > latest[key][0]:
                latest[key] = rank, row
        values = [tuple(stock if key == "stock_code" else row[key] for key in quarterly.COLUMNS)
                  for _, row in (latest[key] for key in sorted(latest)) for stock in row["stock_codes"]]
        frame = pd.DataFrame(values, columns=quarterly.COLUMNS)
        for metric in quarterly.ACCOUNTS:
            frame[metric] = pd.to_numeric(frame[metric]).astype(float)
        frame.attrs["correction_receipts"] = sorted(self.correction_receipts)
        return frame
