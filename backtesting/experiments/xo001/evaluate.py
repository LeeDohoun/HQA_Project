"""XO001-only measurement statistics. Uses the measurement registry API; never invokes the strategy judge."""
from collections import Counter
from datetime import date, timedelta
import math
import json
from pathlib import Path

import numpy as np

from . import END, EXPERIMENT_ID, START, Refused, aware
from .activation import CONFIG, runtime_verification, verify_unchanged
from .calendar import time_map
from .recorder import clock_valid

QUANTILES = (.05, .10, .25, .50, .75, .90, .95)


def positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def order_sizes(expected_price, asks, auction_volume=None):
    if not positive(expected_price):
        return {"reason": "no_positive_0859_expected_price"}
    depth = None
    if (isinstance(asks, list) and len(asks) == 10 and all(isinstance(p, list) and len(p) == 2
            and all(type(v) in (float, int) and math.isfinite(v) and v >= 0 for v in p) for p in asks)):
        depth = sum(q for p, q in asks if 0 < p <= expected_price)
    result = {"displayed_ask_depth": depth, "interpretation": "displayed quantity; no execution guarantee"}
    for amount in (1_000_000, 10_000_000):
        quantity = math.floor(amount / expected_price)
        result[str(amount)] = {"quantity": quantity,
            "order_to_displayed_depth": quantity / depth if depth else None,
            "displayed_depth_to_order": depth / quantity if depth is not None and quantity else None,
            "order_to_auction_volume": quantity / auction_volume if positive(auction_volume) else None}
    return result


def statistics(rows):
    pairs = [r for r in rows if r["valid_pair"]]
    values = np.asarray([10_000 * (r["krx_open"] / r["expected_price"] - 1) for r in pairs], dtype=float)
    result = {"events": len(rows), "complete": sum(r["complete"] for r in rows), "valid_pairs": len(pairs),
        "coverage": sum(r["complete"] for r in rows) / len(rows) if rows else None,
        "delay_rate": sum(r["opening_category"] == "opening_delayed" for r in rows) / len(rows) if rows else None,
        "outage_rate": sum(not r["complete"] for r in rows) / len(rows) if rows else None}
    result.update({f"q{int(q * 100):02d}_bp": float(np.quantile(values, q, method="linear")) if len(values) else None for q in QUANTILES})
    result.update({f"abs_gt_{threshold}bp": float(np.mean(np.abs(values) > threshold)) if len(values) else None for threshold in (10, 25, 50)})
    return result


def bootstrap(rows):
    """Resample whole entry dates (including missing pairs), 2,000 draws, seed 0."""
    days = sorted({r["entry_date"] for r in rows})
    if not days:
        return {"replicates": 2000, "seed": 0, "cluster": "entry_date", "intervals": {}}
    clusters = [[r for r in rows if r["entry_date"] == day] for day in days]
    rng, samples = np.random.default_rng(0), {}
    for _ in range(2000):
        draw = rng.integers(0, len(days), size=len(days))
        result = statistics([row for index in draw for row in clusters[index]])
        for key, value in result.items():
            if key not in {"events", "complete", "valid_pairs"} and value is not None:
                samples.setdefault(key, []).append(value)
    return {"replicates": 2000, "seed": 0, "cluster": "entry_date", "quantile_method": "linear",
        "intervals": {k: {"lower": float(np.quantile(v, .025, method="linear")),
                          "upper": float(np.quantile(v, .975, method="linear")), "defined_replicates": len(v)}
                      for k, v in samples.items()}}


def measure(rows):
    result, table = statistics(rows), {}
    pairs = [r for r in rows if r["valid_pair"]]
    companies, days = len({r["company"] for r in pairs}), len({r["entry_date"] for r in pairs})
    complete = (len(pairs) >= 100 and companies >= 40 and days >= 50 and result["coverage"] is not None
                and result["coverage"] >= .95)
    for row in rows:
        key = str(row["daily_executable"]) + ":" + row["opening_category"]
        table[key] = table.get(key, 0) + 1
    return {"status": "measurement_complete" if complete else "measurement_insufficient",
        "coverage_first": {k: result[k] for k in ("events", "complete", "coverage", "delay_rate", "outage_rate")},
        "price_statistics": result, "valid_pair_companies": companies, "valid_pair_entry_days": days,
        "cross_table": table, "bootstrap": bootstrap(rows),
        "concentration": {"company_counts": dict(Counter(r["company"] for r in rows)),
                          "entry_date_counts": dict(Counter(r["entry_date"] for r in rows))},
        "no_price_pair": len(rows) - len(pairs), "price_denominator": len(pairs), "overall_denominator": len(rows)}


def _verified_roster(store, day, deadline):
    proof = store.proof("roster", day)
    return bool(proof and proof["valid"] and proof["record"]["label"] == "forward"
                and aware(proof["publication_not_after"]) <= aware(deadline))


def _verified_pre(store, day):
    proof = store.proof("preseal", day)
    if not proof or not proof["valid"] or not clock_valid(proof["record"]["clock"]):
        return None
    value = proof["record"]
    for key, digest in value["raw_hashes"].items():
        actual = store.proof("pre_raw", key)
        if not actual or actual["record_sha256"] != digest:
            raise Refused("pre-open raw manifest differs")
    for name, digest in value["snapshot_hashes"].items():
        actual = store.proof("snapshot", day + "." + name)
        if not actual or not actual["valid"] or actual["record_sha256"] != digest:
            raise Refused("pre-open snapshot manifest differs")
        if actual["record"].get("label") == "reconstructed":
            return None
    if set(value["snapshot_hashes"]) != set(CONFIG["snapshots"]):
        raise Refused("required snapshot manifest entries missing")
    return value


def observation(store, day, code, company, *, event=None, freeze=None, roster_deadline=None):
    roster_ok = _verified_roster(store, day, roster_deadline or day + "T08:25:00+09:00")
    pre = _verified_pre(store, day) if roster_ok else None
    post_proof, daily_proof = store.proof("postseal", day), store.proof("reconciliation", day)
    if freeze:
        post_proof = post_proof if post_proof and aware(post_proof["publication_not_after"]) <= freeze else None
        daily_proof = daily_proof if daily_proof and aware(daily_proof["publication_not_after"]) <= freeze else None
    post = post_proof["record"] if roster_ok and post_proof and post_proof["valid"] and post_proof["record"]["label"] == "forward" else None
    if post:
        for key, digest in post["raw_hashes"].items():
            proof = store.proof("post_raw", key)
            if not proof or proof["record_sha256"] != digest:
                raise Refused("post-open raw manifest differs")
    snapshots = [store.get("snapshot", day + "." + name) if pre else None for name in CONFIG["snapshots"]]
    states = [r["states"].get(code, {"state": "unknown"}) if r else {"state": "unknown"} for r in snapshots]
    last = states[-1]
    opening = (post["states"].get(code, {}) if post and clock_valid(post["clock"]) else {})
    complete = all(r["state"] in {"observed", "confirmed_absent"} for r in states) and opening.get("state") in {"observed", "confirmed_absent"}
    daily = daily_proof["record"]["rows"].get(code, {}) if daily_proof else {}
    if daily_proof and not daily_proof["valid"]:
        daily_proof, daily = None, {}
    if daily_proof:
        store.bytes(daily_proof["record"]["daily_sha256"])
    try:
        krx_open, volume = float(daily["open"]), float(daily["volume"])
    except (KeyError, TypeError, ValueError):
        krx_open, volume = None, None
    expected = last.get("expected_price")
    valid_pair = (pre is not None and last.get("state") == "observed" and last.get("date") == day
                  and last.get("exchange") == "KRX" and positive(expected) and positive(krx_open)
                  and daily.get("trade_date") == day and daily.get("calendar_status") == "verified")
    base = daily.get("base_price")
    try:
        base = float(base)
    except (TypeError, ValueError):
        base = None
    if not positive(base) and event:
        base = (event.get("event_features") or {}).get("pre_close")
    daily_numbers = all(type(v) in (float, int) and math.isfinite(v) and v >= 0 for v in (krx_open, volume))
    executable = None if not daily or not positive(base) or not daily_numbers else bool(positive(krx_open) and positive(volume) and krx_open < base * 1.30 - 1e-9)
    auction = opening.get("auction_price")
    return {"entry_date": day, "stock_code": code, "company": company, "complete": complete,
            "auxiliary_only": not roster_ok,
            "required_states": [r["state"] for r in states] + [opening.get("state", "unknown")],
            "valid_pair": bool(valid_pair), "expected_price": expected, "krx_open": krx_open,
            "daily_executable": executable, "opening_category": opening.get("opening_category", "unknown"),
            "auction_daily_reconciliation": {"auction_price": auction, "daily_open": krx_open,
                "equal": auction == krx_open if positive(auction) and positive(krx_open) else None},
            "order_size_diagnostic": order_sizes(expected, last.get("asks"), opening.get("auction_volume"))}


def evaluate(store, calendar, bb003, *, registry_root=None):
    verification = runtime_verification(store)
    if store.proof("evaluation", "final"):
        verify_unchanged(store, verification)
        register_final(store, registry_root)
        return store.get("evaluation", "final")
    # Do not request any final-calendar horizon while evaluating prematurely.
    if aware(store.clock()).date().isoformat() <= END:
        raise Refused("measurement evaluator is closed before the fixed freeze")
    freeze = calendar.freeze()
    if aware(store.clock()) <= freeze:
        raise Refused("measurement evaluator is closed before the fixed freeze")
    primary, supplemental = [], {"matched": [], "baseline": []}
    # Global BB003 inventory preserves outage days even when XO001 never ran.
    missing_candidates = []
    for key in bb003.keys("decisions"):
        decision = bb003.get("decisions", key)
        if not decision or decision["status"] != "included":
            continue
        event = decision["event"]
        receipt_day = event["rcept_dt"]
        if not START.replace("-", "") <= receipt_day <= END.replace("-", ""):
            continue
        day = event["entry_date"]
        timely = bb003.get("decisions", key, as_of=time_map(calendar.day(day))["s0859"])
        if not timely:
            continue
        row = observation(store, day, event["stock_code"], event["corp_code"], event=decision, freeze=freeze,
            roster_deadline=time_map(calendar.day(day))["roster"])
        primary.append({**row, "rcept_no": event["rcept_no"]})
    for kind in ("data_not_ready", "omissions"):
        for key in bb003.keys(kind):
            record = bb003.get(kind, key, as_of=freeze)
            if record and START <= record["entry_date"] <= calendar.last_entry():
                missing_candidates.append({"kind": kind, "rcept_no": record["rcept_no"], "reason": record["reason"]})
    overlap = []
    for key in store.keys("roster"):
        roster = store.proof("roster", key)["record"]
        if not START <= roster["date"] <= calendar.last_entry() or not _verified_roster(store, key, time_map(calendar.day(key))["roster"]):
            continue
        for role in supplemental:
            for group in roster[role]:
                for code in group["selected"]:
                    supplemental[role].append(observation(store, roster["date"], code, code, freeze=freeze,
                        roster_deadline=time_map(calendar.day(key))["roster"]))
        for group in roster["matched"]:
            post = bb003.get("post_entry", group["rcept_no"])
            final = set((post or {}).get("selection", {}).get("control_features", {})) if (post or {}).get("selection") else set()
            overlap.append({"rcept_no": group["rcept_no"], "XO001": group["selected"], "BB003": sorted(final),
                            "intersection": sorted(set(group["selected"]) & final)})
    expected_baseline, cursor = 0, date.fromisoformat(START)
    last = date.fromisoformat(calendar.last_entry())
    while cursor <= last:
        if calendar.day(cursor.isoformat())["session"]:
            expected_baseline += 6
        cursor += timedelta(days=1)
    report = {"experiment_id": EXPERIMENT_ID, **measure(primary), "freeze": freeze.isoformat(),
        "verification": verification, "primary_rows": primary, "supplemental": {k: {"statistics": statistics(v), "rows": v} for k, v in supplemental.items()},
        "baseline_scheduled_slots": expected_baseline, "baseline_selected_slots": len(supplemental["baseline"]),
        "missing_candidates": missing_candidates, "BB003_control_overlap": overlap,
        "order_diagnostic": diagnostic_summary(store, freeze),
        "diagnostic_in_primary": False, "strategy_test": False, "bootstrap_company_scope": "date clusters; company concentration reported separately"}
    verify_unchanged(store, verification)
    result = store.seal("evaluation", "final", report)["record"]
    register_final(store, registry_root)
    return result


def diagnostic_summary(store, freeze):
    """Only main-period, same-account records actually published by F."""
    activation = store.get("activation", "activation")
    account = activation["binding"].get("account_sha256")
    rows, hashes = {}, {}
    for kind in ("order_attempt", "order_result", "order_skip", "order_inquiry"):
        rows[kind] = []
        for key in store.keys(kind):
            proof = store.proof(kind, key)
            row = proof["record"]
            if (aware(proof["publication_not_after"]) <= freeze and START <= row["date"] <= freeze.date().isoformat()
                    and row["account_sha256"] == account):
                rows[kind].append(row)
                hashes[kind + "/" + key] = proof["record_sha256"]
    latest = {}
    for query in rows["order_inquiry"]:
        if query["complete"]:
            for order in query["orders"]:
                latest[(query["date"], order["order_no"])] = order
    return {"attempts": len(rows["order_attempt"]), "result_states": dict(Counter(r["state"] for r in rows["order_result"])),
        "non_submission_states": dict(Counter(r["state"] for r in rows["order_skip"])),
        "confirmed_order_states": dict(Counter(r["state"] for r in latest.values())),
        "incomplete_inquiries": sum(not r["complete"] for r in rows["order_inquiry"]),
        "page_sizes": [r["page_sizes"] for r in rows["order_inquiry"]], "source_hashes": hashes,
        "economic_execution_capacity": False, "included_in_primary": False}


def register_final(store, registry_root=None):
    """Recover seal-before-register crashes without computing measurements again."""
    from backtesting import experiment_registry as registry
    proof = store.proof("evaluation", "final")
    if not proof or not proof["valid"]:
        raise Refused("sealed final measurement required for registry")
    report = proof["record"]
    if report["status"] not in {"measurement_complete", "measurement_insufficient"}:
        raise Refused("invalid measurement registry status")
    root = Path(registry_root) if registry_root else registry.PROJECT_ROOT
    path = root / registry.REGISTRY_PATH
    metrics = {"result_sha256": proof["record_sha256"], "result_path": str(store.path(proof["record_path"])),
        "code_sha256": report["verification"]["binding"]["code_sha256"],
        "environment_sha256": report["verification"]["binding"]["environment_sha256"],
        "activation_sha256": report["verification"]["activation_sha256"],
        "approved_amendment_sha256": report["verification"]["approved_amendment_sha256"], "strategy_test": False}
    def existing():
        if not path.exists():
            return False
        with registry.file_lock(path, shared=True):
            with registry.text_buffer(path.read_bytes(), path) as handle:
                rows = [r for r in registry._registry_rows(handle) if r["experiment_id"] == EXPERIMENT_ID]
        if not rows:
            return False
        registration = registry.verify_preregistration(EXPERIMENT_ID, root)
        if (len(rows) != 1 or rows[0]["variant"] != "measurement" or rows[0]["verdict"] != report["status"]
                or rows[0]["prereg_commit"] != registration.commit_hash or json.loads(rows[0]["metrics_json"]) != metrics):
            raise Refused("XO001 registry conflicts with the sealed final measurement")
        return True
    if existing():
        return
    try:
        registry.record_trials(EXPERIMENT_ID, [("measurement", metrics, report["status"])],
            note="XO001 measurement only; sealed result hash linked; no strategy judgment", repo_root=root)
    except ValueError:
        # record_trials checks duplicate variants under its own stable registry lock.
        if not existing():
            raise
