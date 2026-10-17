"""LH002 numeric features using S dates and snapshot-only protected sources."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator

from backtesting.experiments import hc001
from backtesting.experiments.lh001 import inputs as shared
from src.ingestion import dart_listing, dart_quarterly
from src.ingestion.dart_financial_date_rules import FilingDates
from src.ingestion.storage import read_rows
from src.runner.event_evidence import _category

from . import PROMPT_DIR, Refused
from .calendar import exchange, session_close
from .snapshot import input_view
from .storage import aware, digest, file_hash, read


def price_path(data_dir, day):
    stamp = pd.Timestamp(day).strftime("%Y%m%d")
    return Path(data_dir) / "market/krx_daily" / stamp[:4] / (stamp + ".jsonl")


def verify_day(data_dir, day, *, at, sync_manifest):
    path = price_path(data_dir, day)
    if not path.is_file():
        raise Refused(f"decision-day KRX data missing: {day}; data_dir={data_dir}")
    if aware(at) < session_close(day):
        raise Refused("decision session has not closed on the pinned XKRX calendar")
    if sync_manifest is None:
        raise Refused("decision trigger requires a data-sync hash manifest")
    manifest = read(sync_manifest)
    relative = path.relative_to(data_dir).as_posix()
    checksum = file_hash(path)
    verified_at = aware(manifest["verified_at"])
    if verified_at > aware(at) or verified_at < session_close(day):
        raise Refused("data-sync verification must follow the decision close and precede the trigger")
    if manifest["files"].get(relative) != checksum:
        raise Refused("data-sync hash mismatch for decision-day KRX data")
    rows = read_rows(path)
    if not rows or any(pd.Timestamp(row["trade_date"]) != pd.Timestamp(day)
                       or row.get("calendar_status") != "verified" for row in rows):
        raise Refused("decision-day KRX rows are empty, unverified or have a different date")
    if not {"KOSPI", "KOSDAQ"}.issubset({row.get("market") for row in rows}):
        raise Refused("verified decision-day data must cover both KOSPI and KOSDAQ")
    if file_hash(path) != checksum:
        raise Refused("decision-day data changed during verification")
    return {"path": relative, "sha256": checksum, "verified_at": verified_at.isoformat(),
            "sync_manifest_sha256": file_hash(sync_manifest), "rows": len(rows)}


def prices_from_view(view, sessions):
    parts = []
    columns = ("open", "high", "low", "close", "volume", "trading_value", "market_cap", "base_price",
               "stock_name", "market", "calendar_status")
    for day in sessions:
        session_close(day)
        path = price_path(view, day)
        records = {}
        if not path.is_file():
            raise Refused("required price session missing: " + day.date().isoformat())
        for row in read_rows(path):
            if pd.Timestamp(row["trade_date"]) != day or row.get("calendar_status") != "verified":
                raise Refused("price date/status differs from verified session")
            records[row["stock_code"]] = {**{key: row.get(key) for key in columns},
                 "ret_1d": float(row["change_rate_pct"]) / 100 if row.get("change_rate_pct") is not None else np.nan}
        if not records:
            raise Refused("required price session is empty: " + day.date().isoformat())
        frame = pd.DataFrame.from_dict(records, orient="index").rename_axis("stock_code")
        for field in columns[:8]:
            frame[field] = pd.to_numeric(frame[field], errors="raise")
        parts.append(frame.assign(trade_date=day).reset_index().set_index(["trade_date", "stock_code"]))
    return pd.concat(parts).sort_index()


def financials(view, day):
    conflicts = set()
    listings = (row for stamp in dart_listing.listing_paths(view, end=pd.Timestamp(day) - pd.Timedelta(days=1))
                for row in dart_listing.listing_rows(view, stamp, conflicts=conflicts))
    dates = FilingDates(listings, conflicts=conflicts)
    records, annotations = [], []
    for path in sorted((Path(view) / "fundamentals/dart_quarterly").glob("*_110*.jsonl")):
        for row in read_rows(path):
            if row["available_date"] != dart_quarterly._filing_date(row["rcept_no"]):
                raise Refused("financial receipt/availability mismatch")
            info = dates.annotate(row)
            actual = info["actual_public_date"]
            annotations.append({"rcept_no": row["rcept_no"], **info})
            if actual is not None and actual < pd.Timestamp(day).date().isoformat():
                records.append({**row, "available_date": actual})
    latest = {}
    for row in dart_quarterly._derive(records):
        key = row["corp_code"], row["fiscal_quarter"]
        rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
        if key not in latest or rank > latest[key][0]:
            latest[key] = rank, row
    values = [{key: stock if key == "stock_code" else row[key] for key in dart_quarterly.COLUMNS}
              for _, row in (latest[key] for key in sorted(latest)) for stock in row["stock_codes"]]
    frame = pd.DataFrame(values, columns=dart_quarterly.COLUMNS)
    for metric in dart_quarterly.ACCOUNTS:
        frame[metric] = pd.to_numeric(frame[metric]).astype(float)
    return frame, {"rule": "S", "L": dates.L, "annotations": annotations}


def disclosures(view, sessions, codes, status):
    prior = sessions[-60:]
    if len(prior) != 60:
        raise Refused("60 completed sessions are required for the disclosure window")
    required = pd.date_range(prior[0], prior[-1])
    covered = set(dart_listing.covered_listing_days(view, required, status=status))
    complete = len(covered) == len(required)
    counts = {code: dict.fromkeys(shared.CATEGORIES, 0) if complete else None for code in codes}
    if complete:
        for stamp in sorted(covered):
            conflicts = set()
            merged = dart_listing.listing_rows(view, stamp, conflicts=conflicts)
            for row in merged:
                if row["rcept_no"] in conflicts:
                    continue
                if row.get("stock_code") in counts:
                    counts[row["stock_code"]][_category({"title": row["report_nm"], "source_type": "dart"})] += 1
    return {code: {"counts": counts[code], "titles": [], "coverage": {
        "complete": complete, "days": len(covered), "expected": len(required)}} for code in codes}


def prepare(data_dir, snapshot, day):
    day = pd.Timestamp(day)
    sessions = exchange().sessions
    pos = sessions.get_loc(day)
    window = sessions[pos - 251:pos + 1]
    if len(window) != 252:
        raise Refused("252 close-known price sessions are required")
    with input_view(data_dir, snapshot, end=day.strftime("%Y%m%d")) as view:
        # Hash before and after every file the view exposes; changes never silently
        # enter a decision. The source protected paths do not enter this view.
        def hashes():
            return {p.relative_to(view).as_posix(): file_hash(p) for p in sorted(view.rglob("*")) if p.is_file()}
        before = hashes()
        history = prices_from_view(view, window)
        known, date_report = financials(view, day)
        profiles = hc001._industries(view)
        universe, coverage = shared.close_universe(history, day, known, profiles)
        status = dart_listing.listing_status(view, price_end=day)
        counts = disclosures(view, sessions[:pos], set(universe.index), status)
        latest = {}
        for row in read_rows(view / "market_context/benchmarks.jsonl"):
            date = pd.Timestamp(row["trade_date"])
            if window[0] <= date <= day:
                latest[date, row["series"], row["index_name"]] = float(row["close"])
        benchmarks = pd.DataFrame([{"trade_date": date, "series": series, "index_name": name, "close": value}
            for (date, series, name), value in latest.items()], columns=("trade_date", "series", "index_name", "close"))
        for series, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
            index = benchmarks.loc[benchmarks.series.eq(series) & benchmarks.index_name.eq(name), "trade_date"]
            if len(window.difference(pd.DatetimeIndex(index))):
                raise Refused("required market-index history missing: " + series)
        bundle = shared.build_tables(history, day, known=known, profiles=profiles, benchmarks=benchmarks,
                                     universe=universe, coverage=coverage, disclosures=counts, listing_version=status["listing_version"])
        if before != hashes():
            raise Refused("input source changed during preparation")
        return {"bundle": bundle, "source_hashes": before, "financial_dates": date_report,
                "snapshot_sha256": digest(snapshot.manifest)}


def prompt_hashes(directory=PROMPT_DIR):
    return {path.name: file_hash(path) for path in sorted(Path(directory).glob("v3*"))}


def render(bundle, directory=PROMPT_DIR):
    # Cap only the shown pool. A retains every eligible phase-2 stock.
    eligible = set(bundle["a_codes"])
    stocks = sorted((s for s in bundle["stocks"] if s["stock_code"] in eligible),
                    key=lambda s: (-s["adv_20"], s["stock_code"]))[:300]
    codes = [stock["stock_code"] for stock in stocks]
    table, mapping = shared.render(bundle, anonymised=True, codes=codes)
    directory = Path(directory)
    schema = read(directory / "v3_c_num.schema.json")
    for key in ("ranked", "avoid"):
        schema["properties"][key]["items"]["enum"] = sorted(mapping)
    Draft202012Validator.check_schema(schema)
    reask = (directory / "v3_reask.txt").read_text(encoding="utf-8")
    prompt = "\n".join((directory.joinpath("v3_common.txt").read_text(encoding="utf-8"),
                         directory.joinpath("v3_c_num.txt").read_text(encoding="utf-8"),
                         "<UNTRUSTED_DATA>", table, "</UNTRUSTED_DATA>"))
    # The schema intentionally has no $schema URI: its calendar-like draft name
    # would itself trip the date-token leak checker. Validator is explicit.
    for text in (prompt, json.dumps(schema, ensure_ascii=False), reask, prompt + "\n" + reask):
        shared.leak_check(text, bundle)
    return {"prompt": prompt, "schema": schema, "reask": reask, "id_mapping": mapping,
            "candidate_codes": codes, "prompt_sha256": digest(prompt), "schema_sha256": digest(schema),
            "reask_sha256": digest(reask), "prompt_files": prompt_hashes(directory)}


def validate(output, schema):
    Draft202012Validator(schema).validate(output)
    if set(output["ranked"]) & set(output["avoid"]):
        raise Refused("ranked and avoid overlap")
