"""Past-archive recorder replay with a fake clock and a fresh throwaway root."""
from collections import Counter
from datetime import datetime, time
from pathlib import Path
import resource
import time as timer

import pandas as pd

from src.ingestion import dart_buyback as dart
from . import KST
from .deployment import inventory, rehearsal_output
from .fetch import ArchiveClient
from .inputs import guard
from .policy import shared_policy
from .recorder import capture_prices, post_entry, prepare_day, seal_day
from .storage import Store, encoded, put, sha


def synthetic_source(directory, policy, start, end):
    """Dated 2025 fakes for smoke measurement, never evidence of server budget."""
    codes = [f"{i * 10:06d}" for i in range(1, 62)]
    events = []
    for i, day in enumerate(pd.date_range(start, end)[:3]):
        corp = 1 if i == 0 else 2
        events.append({"rcept_no": day.strftime("%Y%m%d") + f"{i + 1:06d}", "rcept_dt": day.strftime("%Y%m%d"),
            "corp_code": f"{corp:08d}", "stock_code": codes[corp - 1], "corp_cls": "Y",
            "corp_name": "synthetic common", "report_nm": "자기주식취득결정"})
    archive_end = policy.sessions[policy.entry(end)]
    completed = []
    for day in pd.date_range(policy.start, archive_end):
        guard(day)
        key = day.strftime("%Y%m%d")
        completed.append(key)
        put(directory / f"disclosures/dart_full/list/{day:%Y}/{key}.jsonl",
            b"".join(encoded(e) for e in events if e["rcept_dt"] == key))
        if day in policy.sessions:
            rows = [{"trade_date": day.date().isoformat(), "stock_code": code, "stock_name": "synthetic common",
                "market": "KOSDAQ" if code == codes[-1] else "KOSPI", "calendar_status": "verified",
                "open": 10000., "close": 10000., "base_price": 10000., "volume": 10000.,
                "trading_value": 2e9, "market_cap": 1e11, "change_rate_pct": 0.} for code in codes]
            put(directory / f"market/krx_daily/{day:%Y}/{key}.jsonl", b"".join(encoded(r) for r in rows))
    put(directory / "disclosures/dart_full/_state.json", encoded({"completed_listing_days": completed}))
    for corp in sorted({e["corp_code"] for e in events}):
        values = {}
        for i, event in enumerate(events):
            if event["corp_code"] != corp:
                continue
            row = {"rcept_no": event["rcept_no"], "corp_code": corp, "aqpln_stk_ostk": "1,234",
                "aqpln_prc_ostk": "12,345,678", "aq_mth": "장외" if i == 1 else "장내",
                "aqexpd_bgd": start, "aqexpd_edd": end, "aq_pp": "소각"}
            values[event["rcept_no"]] = {"row": row, "row_sha256": dart.row_digest(row), "response_sha256": []}
            text = "예정수량 1,234 주 예정금액 12,345,678 원"
            document = {"version": dart.VERSION, "rcept_no": event["rcept_no"], "status": "000", "text": text,
                "text_sha256": sha(text.encode()), "raw_sha256": "a" * 64, "verification": dart.verify_values(row, text)}
            put(directory / f"disclosures/dart_buyback/documents/{event['rcept_no']}.json", encoded(document))
        put(directory / f"disclosures/dart_buyback/structured/{corp}.json", encoded({"version": dart.VERSION,
            "corp_code": corp, "from_date": dart.START, "to_date": dart.END, "complete": True,
            "requests": [], "rows": values}))


def rehearse(source, output, *, start="2025-07-01", end="2025-12-29", synthetic=False):
    if not "2025-07-01" <= start <= end <= "2025-12-29":
        raise ValueError("rehearsal receipt dates must be in 2025 H2, ending by Dec 29 to avoid protected entry reads")
    repo = Path(__file__).resolve().parents[3]
    output = rehearsal_output(output, source, repo)
    begun = timer.perf_counter()
    # Entry dates suffice for this return-free replay; exits are resolved at evaluation.
    policy = shared_policy("2025-01-01", "2026-12-31")
    output.mkdir(mode=0o750)
    if synthetic:
        source = output / "synthetic-source"
        # Do not materialize any protected dates, even though the policy has
        # future verified sessions for scheduled exits.
        synthetic_source(source, policy, start, end)
    clock = [datetime.combine(pd.Timestamp(start).date(), time(8, 20), KST)]
    store = Store(output, clock=lambda: clock[0], measurement=True)
    client, samples, phases, counts = ArchiveClient(source), [], Counter(), Counter()
    last_entry = policy.sessions[policy.entry(end)]
    for day in pd.date_range(start, last_entry):
        guard(day)
        for hour, minute in ((8, 20), (18, 30)):
            clock[0] = datetime.combine(day.date(), time(hour, minute), KST)
            tick = timer.perf_counter()
            with store.lock():
                if hour == 8:
                    if not store.get("morning_plan", day.date().isoformat()):
                        continue
                    report = seal_day(store, source, policy)
                    post, capture = {}, {}
                else:
                    report = prepare_day(store, source, policy, client, start=start, end=end)
                    mark = timer.perf_counter()
                    post = post_entry(store, source, policy)
                    phases["post_entry"] += timer.perf_counter() - mark
                    mark = timer.perf_counter()
                    capture = capture_prices(store, source, policy, start=start, end=end)
                    phases["daily_vintage_capture"] += timer.perf_counter() - mark
                phases.update(report["phase_seconds"])
                counts.update(report["counts"])
            samples.append({"fake_time": clock[0].isoformat(), "phase": "seal" if hour == 8 else "prepare",
                            "wall_seconds": timer.perf_counter() - tick,
                            "counts": report["counts"], "post_entry": post, "capture": capture})
    sources = {name: sha((repo / name).read_bytes()) for name in inventory(repo)}
    result = {"measurement": True, "label": "reconstructed", "source": str(source), "output_root": str(output),
        "synthetic": synthetic, "receipt_window": [start, end], "fake_clock": True, "network_calls": 0,
        "wall_seconds": timer.perf_counter() - begun, "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "phase_seconds": dict(phases), "counts": dict(counts), "samples": samples,
        "source_files": sources, "policy_sha256": policy.digest,
        "two_phase": True, "server_budget_validated": False,
        "seal_max_seconds": max((s["wall_seconds"] for s in samples if s["phase"] == "seal"), default=0),
        "measurement_scope": "full decision + input snapshots + completion + post-entry + daily acquisition; no returns/evaluation"}
    put(output / "measurement.json", encoded(result))
    return {k: v for k, v in result.items() if k not in {"samples", "source_files"}}
