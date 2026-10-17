"""Explicit synthetic archives and a local fake process; never a model request."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.ingestion import dart_listing
from src.ingestion.storage import atomic_write, write_rows

from . import CODEX_VERSION, Refused
from .calendar import exchange
from .inputs import prepare, price_path, render
from .runner import LlmRunner
from .storage import aware, digest, seal


class FakeCodex:
    """Only the injected Python callable executes. No subprocess or network."""
    def __init__(self):
        self.execs = 0

    def __call__(self, command, **kwargs):
        if command == ["codex", "--version"]:
            return SimpleNamespace(stdout=CODEX_VERSION + "\n", stderr="", returncode=0)
        self.execs += 1
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        ids = schema["properties"]["ranked"]["items"]["enum"]
        output = {"ranked": ids[:30], "avoid": ids[30:40]}
        Path(command[command.index("-o") + 1]).write_text(json.dumps(output))
        events = [{"type": "thread.started"}, {"type": "turn.started"},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(output)}},
                  {"type": "turn.completed"}]
        return SimpleNamespace(stdout="\n".join(json.dumps(e) for e in events), stderr="", returncode=0)


def synthetic_archive(root, *, day="2026-10-16", stocks=60):
    """A labelled stand-in with H1 prices, indices, S-date versions and coverage."""
    root = Path(root)
    if "data" in root.resolve().parts:
        raise Refused("synthetic archives must never be written under data/")
    calendar = exchange()
    day = pd.Timestamp(day)
    pos = calendar.sessions.get_loc(day)
    # Include the preceding completed week's 252-day pre-start cycle as well.
    sessions = calendar.sessions[pos - 271:pos + 1]
    codes = [f"{10 * (index + 1):06d}" for index in range(stocks)]
    for number, session in enumerate(sessions):
        rows = []
        for index, code in enumerate(codes):
            change = 0.0002 + index * 0.000001
            previous = 10000 * (1 + change) ** number
            close = previous * (1 + change)
            rows.append({"trade_date": session.date().isoformat(), "stock_code": code, "stock_name": "합성기업" + code,
                         "open": previous, "close": close, "high": close * 1.01, "low": previous * 0.99,
                         "volume": 200000, "trading_value": 2e9 + index * 1e7, "market_cap": 2e10 + index * 1e8,
                         "base_price": previous, "change_rate_pct": change * 100,
                         "market": "KOSPI" if index % 2 == 0 else "KOSDAQ", "calendar_status": "verified"})
        write_rows(price_path(root, session), rows)
    write_rows(root / "market_context/benchmarks.jsonl", [
        {"trade_date": session.date().isoformat(), "series": series, "index_name": name, "close": 1000 * 1.0001 ** index}
        for index, session in enumerate(sessions) for series, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))])
    profiles = [{"corp_code": f"{i + 1:08d}", "stock_code": code, "corp_cls": "Y" if i % 2 == 0 else "K",
                 "induty_code": ("26", "29", "20")[i % 3]} for i, code in enumerate(codes)]
    write_rows(root / "reference/dart_company/companies.jsonl", profiles)
    with (root / "reference/corp_codes.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("corp_code", "stock_code"))
        writer.writerows((p["corp_code"], p["stock_code"]) for p in profiles)
    listing_rows = defaultdict(list)
    for year in (2024, 2025, 2026):
        for quarter, report in ((1, "11013"), (2, "11012"), (3, "11014"), (4, "11011")):
            publication = pd.Timestamp(f"{year + 1}-03-15" if quarter == 4 else f"{year}-{(5, 8, 11)[quarter - 1]:02d}-15")
            if publication >= day:
                continue
            stamp = publication.strftime("%Y%m%d")
            rows = []
            for index, code in enumerate(codes):
                receipt, corp = stamp + f"{index + 1:06d}", f"{index + 1:08d}"
                revenue, income = 100 * 1.2 ** (year - 2024), 10 * 2 ** (year - 2024)
                accounts = {}
                for key, value in (("revenue", revenue), ("operating_income", income), ("net_income", income / 2)):
                    accounts[key] = {"thstrm_amount": str(value * 4 if quarter == 4 else value),
                                     "thstrm_add_amount": str(value * quarter) if quarter in (2, 3) else None,
                                     "currency": "KRW"}
                rows.append({"corp_code": corp, "stock_codes": [code], "bsns_year": year, "reprt_code": report,
                             "fiscal_quarter": f"{year}Q{quarter}", "available_date": publication.date().isoformat(),
                             "rcept_no": receipt, "fs_div": "CFS", "currency": "KRW", "raw_accounts": accounts})
                kind = "사업보고서" if quarter == 4 else "반기보고서" if quarter == 2 else "분기보고서"
                listing_rows[stamp].append({"corp_code": corp, "stock_code": code, "rcept_no": receipt,
                    "rcept_dt": stamp, "report_nm": f"{kind} ({year}.{quarter * 3:02d})"})
            write_rows(root / "fundamentals/dart_quarterly" / f"{year}_{report}.jsonl", rows)
    completed = []
    for date in pd.date_range(min(pd.Timestamp(s) for s in listing_rows), day - pd.Timedelta(days=1)):
        stamp = date.strftime("%Y%m%d")
        write_rows(root / "disclosures/dart_full/list" / stamp[:4] / (stamp + ".jsonl"), listing_rows[stamp])
        completed.append(stamp)
    atomic_write(root / "disclosures/dart_full/_state.json", json.dumps({"completed_listing_days": completed}))
    end = min(day.strftime("%Y%m%d"), "20261009")
    # Synthetic target inventory is built only from synthetic prices.
    state = {"start_de": dart_listing.START, "end_de": end, "target_corps": [], "completed_corps": [],
             "target_set_hash": digest([]), "reference_version": dart_listing.reference_version(root),
             "rows_per_corp": {}, "skipped_per_corp": {}, "duplicates_per_corp": {}, "file_manifest": {},
             "target_scan": dart_listing.freeze_target_scan(root, end)}
    atomic_write(root / "disclosures/dart_full/list_supplement/_state.json", json.dumps(state))
    seal(root / "SYNTHETIC.json", {"synthetic": True, "purpose": "LH003 offline fixture; never experiment data"})
    return root


def fake_cycle(*, data_dir, snapshot, day, scratch, at, timeout=300):
    """Exercise real input preparation, sealing, prompt validation and all repeats."""
    prepared = prepare(data_dir, snapshot, day)
    scratch = Path(scratch)
    seal(scratch / "input.json", prepared)
    seal(scratch / "A.json", {"a_codes": prepared["bundle"]["a_codes"], "input_sha256": digest(prepared)})
    payload = render(prepared["bundle"])
    seal(scratch / "model_input.json", payload)
    process = FakeCodex()
    runner = LlmRunner(scratch, subprocess_call=process, timeout=timeout, clock=lambda: aware(at))
    days = exchange().sessions
    next_day = days[days.get_loc(pd.Timestamp(day)) + 1]
    deadline = next_day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=8, minutes=59)
    result = runner.decide(payload, week="environment_fake_cycle", deadline=deadline.isoformat())
    seal(scratch / "fake_result.json", {**result, "synthetic_model_output": True, "observations": False})
    return {"completed": True, "fake_calls": process.execs, "valid": result["valid"],
            "candidates": len(payload["candidate_codes"]), "input_sha256": digest(prepared),
            "deadline": deadline.isoformat(), "source_snapshot_synthetic": snapshot.manifest["synthetic"]}
