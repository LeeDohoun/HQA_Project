"""Read-only, receipt-exact assets and date-gated quarterly ROA."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import groupby
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research_data import dart_multi_raw as archive
from src.ingestion import dart_quarterly as quarterly
from src.ingestion.dart_financial_date_rules import FinancialVersions
from src.ingestion.storage import read_rows


def version_key(row):
    return tuple(str(row[field]) for field in archive.VERSION_FIELDS)


class AssetArchive:
    """Freeze the ready request inventory; never lock, resume or write a fetch.

    A request published before its derived file is counted as pending. Ready
    files are verified against their immutable response bytes when first read.
    Only one business year's account rows reside in memory at a time.
    """
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / "fundamentals/dart_multi_raw"
        manifest = self.directory / "_universe.json"
        if not manifest.is_file():
            raise ValueError("raw balance-sheet archive _universe.json is missing")
        self.universe = json.loads(manifest.read_bytes())["universe"]
        requests = sorted((self.directory / "requests").glob("[0-9][0-9][0-9][0-9]_110*.json"))
        self.paths = [p for p in requests if (self.directory / "derived" / (p.stem + ".jsonl")).is_file()]
        self.pending = len(requests) - len(self.paths)
        self.year = None
        self.assets, self.income = {}, {}
        self.counts = Counter(ready_requests=len(self.paths), pending_derived=self.pending)
        self.skipped_requests = {}

    def load_year(self, year):
        if self.year == int(year):
            return
        self.year, self.assets, self.income = int(year), {}, {}
        for path in self.paths:
            if int(path.stem[:4]) != self.year:
                continue
            metadata = json.loads(path.read_bytes())
            params = metadata["params"]
            job = archive.Job(int(params["bsns_year"]), params["reprt_code"], tuple(params["corp_code"].split(",")))
            if job.key != path.stem:
                raise ValueError("raw request filename/key mismatch")
            stored, payload = archive._stored(self.directory, job)
            derived = self.directory / "derived" / (job.key + ".jsonl")
            skipped = {}
            expected = archive.derive_rows(payload, job, self.universe, stored["sha256"], skipped)
            self.skipped_requests[job.key] = skipped
            if derived.read_bytes() != b"".join(archive._encoded(row) for row in expected):
                raise ValueError("derived balance-sheet archive differs from raw response")
            for row in expected:
                key = version_key(row)
                currencies = self.assets.setdefault(key, defaultdict(set))
                for account in row["accounts"]:
                    if account.get("metric") == "total_assets":
                        currencies[account.get("currency")].add(archive._amount(account.get("thstrm_amount")))
            diagnostics = {"skipped_rows": {}, "skipped_row_samples": {}}
            # Use the same request-scope policy for the income integrity scan.
            scoped = {**payload, "list": [row for row in payload.get("list", [])
                                         if archive._skip_reason(row, job) is None]}
            if payload["status"] == "000" and not scoped["list"]:
                continue
            fresh = quarterly._records(scoped, job.year, job.report, list(job.corps), self.universe, diagnostics)
            for row in fresh:
                key = version_key(row)
                if "operating_income" in row.get("conflicting_accounts", {}):
                    raise ValueError(f"OP integrity conflict in fresh response: {key}")
                account = row["raw_accounts"].get("operating_income")
                if account is not None:
                    if key in self.income and self.income[key] != account:
                        raise ValueError(f"OP integrity conflict across responses: {key}")
                    self.income[key] = account

    def asset(self, record):
        self.load_year(record["bsns_year"])
        currencies = self.assets.get(version_key(record))
        currency = record.get("currency")
        if currencies is None:
            return None, "missing_version"
        if not currencies:
            return None, "missing_total_assets"
        values = currencies.get(currency, set()) if currency else set()
        if not values:
            return None, "currency_mismatch_or_missing"
        if len(values) != 1:
            return None, "conflicting_total_assets"
        value = next(iter(values))
        if value is None:
            return None, "invalid_total_assets"
        return float(value), "matched"

    def check_income(self, record):
        self.load_year(record["bsns_year"])
        old = record["raw_accounts"].get("operating_income")
        new = self.income.get(version_key(record))
        if old is None or new is None:
            self.counts["op_not_comparable"] += 1
            return
        fields = ("thstrm_amount", "thstrm_add_amount")
        same = all(archive._amount(old.get(k)) == archive._amount(new.get(k)) for k in fields)
        same = same and old.get("currency") == new.get("currency")
        if not same:
            raise ValueError(f"OP integrity conflict with stored raw account: {version_key(record)}")
        self.counts["op_compared"] += 1


def link_coverage(data_dir, assets):
    """All saved versions, including corrections, crossed with listing proxy."""
    statuses = archive._listing_groups(data_dir)
    counts = defaultdict(Counter)
    paths = sorted((Path(data_dir) / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    if not paths:
        raise ValueError("quarterly financial archives are missing")
    for path in paths:
        if int(path.stem[:4]) > 2025:
            continue
        for row in read_rows(path):
            if (str(row["bsns_year"]) != path.stem[:4]
                    or row["available_date"] != quarterly._filing_date(row["rcept_no"])):
                raise ValueError("quarterly archive year/receipt availability mismatch")
            assets.check_income(row)
            _, reason = assets.asset(row)
            year, status = str(row["bsns_year"]), statuses.get(row["corp_code"], "unknown")
            for key in ("overall", "year:" + year, "status:" + status, year + ":" + status):
                counts[key]["total"] += 1
                counts[key][reason] += 1
    def report(counter):
        total, matched = counter["total"], counter["matched"]
        return {**counter, "fraction": matched / total if total else None}
    years = sorted({p.stem[:4] for p in paths if int(p.stem[:4]) <= 2025})
    return {"overall": report(counts["overall"]),
            "by_year": {y: report(counts["year:" + y]) for y in years},
            "by_listing_status": {s: report(counts["status:" + s]) for s in ("listed", "delisted", "unknown")},
            "by_year_and_listing_status": {y: {s: report(counts[y + ":" + s])
                for s in ("listed", "delisted", "unknown")} for y in years},
            "listing_rule": "Current profile Y/K/N=listed; E=delisted/non-exchange proxy; absent=unknown. Not historical status.",
            "op_conflicts": 0, "op_scan": dict(assets.counts),
            "raw_skipped_rows": dict(sum((Counter(counts) for counts in assets.skipped_requests.values()), Counter())),
            "raw_skipped_by_request": {key: counts for key, counts in assets.skipped_requests.items() if counts}}


class ProfitabilityVersions(FinancialVersions):
    """AV's unchanged phase loader, plus same-version assets for whole-FS ROA."""
    def __init__(self, data_dir, dates, rule, *, cutoff, sample_end, assets):
        super().__init__(data_dir, dates, rule, "design_validation", derive=False,
                         cutoff=cutoff, sample_end=sample_end)
        self.roa_rows = []
        for _, paths in groupby(self.paths, key=lambda p: p.stem[:4]):
            records = []
            for path in paths:
                for row in read_rows(path):
                    assets.check_income(row)
                    info = dates.annotate(row)
                    actual = info["actual_public_date"]
                    # The actual-date stage gate MUST precede the O backdating.
                    if actual is None or actual >= self.cutoff:
                        continue
                    amount, reason = assets.asset(row)
                    records.append({**row, "available_date": actual if rule == "S" else info["assumed_public_date"],
                                    "total_assets": amount, "asset_reason": reason})
            # The period/dependency policy is exactly dart_quarterly._derive.
            for row in quarterly._derive(records):
                self.rows.append({key: row[key] for key in (*quarterly.COLUMNS[1:], "stock_codes")})
                account = row["raw_accounts"].get("operating_income", {})
                self.roa_rows.append({key: row[key] for key in (
                    "corp_code", "fiscal_quarter", "bsns_year", "reprt_code", "fs_div", "rcept_no",
                    "available_date", "currency", "operating_income", "total_assets", "asset_reason")} | {
                        "income_currency": account.get("currency"),
                        "derivation_inputs": row["derivation_inputs"].get("operating_income")})
        self.rows.sort(key=lambda r: (r["corp_code"], r["fiscal_quarter"], r["available_date"], r["rcept_no"]))
        self._decision = None
        self._latest = {}

    def roa(self, corp, quarter, decision):
        day = pd.Timestamp(decision).date().isoformat()
        if day >= self.cutoff:
            raise ValueError("ROA decision lies outside the registered stage")
        if day != self._decision:
            self._latest = {}
            for row in self.roa_rows:
                if row["available_date"] >= day:
                    continue
                key = row["corp_code"], row["fiscal_quarter"], row["fs_div"]
                previous = self._latest.get(key)
                if previous is None or (row["available_date"], row["rcept_no"]) > (previous["available_date"], previous["rcept_no"]):
                    self._latest[key] = row
            self._decision = day
        q = pd.Period(quarter, freq="Q")
        failures = {}
        for division in ("CFS", "OFS"):
            rows = [self._latest.get((corp, str(q - offset), division)) for offset in range(5)]
            if any(row is None for row in rows):
                failures[division] = "missing_quarter"
                continue
            income = [row["operating_income"] for row in rows[:4]]
            if any(value is None or not np.isfinite(value) for value in income):
                failures[division] = "missing_quarter_income"
                continue
            currencies = {row["income_currency"] for row in rows[:4]} | {rows[0]["currency"], rows[4]["currency"]}
            if None in currencies or "" in currencies or len(currencies) != 1:
                failures[division] = "currency_mismatch_or_missing"
                continue
            endpoints = [rows[offset]["total_assets"] for offset in (0, 4)]
            if any(value is None for value in endpoints):
                failures[division] = "/".join(sorted({rows[i]["asset_reason"] for i in (0, 4) if rows[i]["total_assets"] is None}))
                continue
            average = sum(endpoints) / 2
            if average <= 0 or not np.isfinite(average):
                failures[division] = "nonpositive_average_assets"
                continue
            return {"roa": sum(income) / average, "fs_div": division, "quarter": str(q),
                    "fallback": division == "OFS", "failures": failures,
                    "receipts": [row["rcept_no"] for row in rows], "reason": None}
        return {"roa": None, "fs_div": None, "quarter": str(q), "fallback": False,
                "failures": failures, "reason": "roa_unavailable"}
