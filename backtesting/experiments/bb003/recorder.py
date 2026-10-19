"""Daily forward decisions, omission ledgers, and separate entry-day matching."""
from collections import Counter
from datetime import datetime, time
from functools import wraps
import re
import time as timer

import pandas as pd

from backtesting.experiments import bb001
from src.ingestion import dart_buyback as dart
from src.utils.stock_codes import is_stock_code
from . import ACTIVATION_BY, END, EXPERIMENT_ID, KST, START, NotReady, aware
from .fetch import acquire
from .inputs import Listings, Prices
from .deployment import verified
from .storage import encoded, load, put, sha
from .admission import entry_plan

ALGORITHM = {
    "id": "BB001.d21.inverted_cdf.lower_ties.size_only_relax.min10.v1",
    "breakpoints": "same-market non-event buyable pool; size thirds, prior20 fifths; inverted_cdf",
    "ties": "lower bin", "relaxation": "size only if fewer than 10; never recompute boundaries",
    "minimum_controls": 10, "weights": "equal, fixed after entry",
    "random": {"generator": "numpy.default_rng", "seed": 0, "paths": 200,
               "order": "path, then (entry_date,rcept_no), then stock_code",
               "statistic": "drawn control net minus that event's fixed-control mean"},
}


@verified("activate")
def activate(store, *, commit, manifest_hash, manifest_raw=None):
    if not re.fullmatch("[0-9a-f]{40}", commit) or not re.fullmatch("[0-9a-f]{64}", manifest_hash):
        raise ValueError("BB003 activation needs full commit and manifest SHA-256")
    if store.proof("activation", "activation"):
        raise ValueError("BB003 activation already exists; it cannot be replaced")
    if manifest_raw is not None and sha(manifest_raw) != manifest_hash:
        raise ValueError("BB003 activation manifest bytes differ")
    hashes = {"manifest": store.blob(manifest_raw)} if manifest_raw is not None else {}
    from .deployment import verify_operation
    verification = verify_operation(store, mode="activate")
    for name in ("seal_runtime_validation", "host_memory_validation"):
        if name + "_sha256" in verification:
            raw = store.path(name + ".json").read_bytes()
            if sha(raw) != verification[name + "_sha256"]:
                raise ValueError("BB003 server measurement changed before activation: " + name)
            hashes[name] = store.blob(raw)
    proof = store.seal("activation", "activation", {"experiment_id": EXPERIMENT_ID,
        "commit": commit, "manifest_sha256": manifest_hash, "time": aware(store.clock()).isoformat(),
        "input_hashes": hashes, **({"host_memory_budget": verification["host_memory_budget"]}
                                  if "host_memory_budget" in verification else {})},
        deadline=ACTIVATION_BY, inclusive=True, fixed_path="activation.json")
    if proof is None or not proof["valid"]:
        raise ValueError("BB003 not_started: activation missed the fixed cutoff")
    return proof


def activation_status(store):
    proof = store.proof("activation", "activation")
    if proof and proof["valid"]:
        return "active"
    return "not_started" if aware(store.clock()) > ACTIVATION_BY else "not_activated"


def repeated(item, titles, sessions):
    """Every title-qualified decision counts, including excluded/missing inputs."""
    key = item["rcept_dt"], item["rcept_no"]
    previous = [row for row in titles if row["corp_code"] == item["corp_code"]
                and (row["rcept_dt"], row["rcept_no"]) < key]
    if not previous:
        return None
    last = max(previous, key=lambda row: (row["rcept_dt"], row["rcept_no"]))
    age = int(sessions.searchsorted(pd.Timestamp(item["rcept_dt"]), side="right")
              - sessions.searchsorted(pd.Timestamp(last["rcept_dt"]), side="right"))
    return {"rcept_no": last["rcept_no"], "sessions": age} if 0 <= age <= 63 else None


def exclusion(item, fetched):
    code, row = item.get("stock_code"), fetched["row"]
    if not is_stock_code(code):
        return "stock_code_link_failed"
    if not code.endswith("0") or "스팩" in (item.get("corp_name") or ""):
        return "not_common_share_market_or_spac"
    if not fetched["verification"]["verified"]:
        return "original_value_verification_failed"
    numbers = [dart.digits(row[k]) for k in dart.REQUIRED_FIELDS[:2]]
    if any(n is None for n in numbers):
        return "invalid_planned_digits"
    if min(map(int, numbers)) <= 0:
        return "nonpositive_plan"
    if not dart.method_matches(row["aq_mth"]):
        return "not_on_market_acquisition"
    return None


def _pending(store, receipt, reason):
    keys = [k for k in store.keys("pending") if k.startswith(receipt + ".")]
    if keys and store.get("pending", keys[-1])["reason"] == reason:
        return
    store.seal("pending", f"{receipt}.{len(keys):06d}", {"rcept_no": receipt, "reason": reason})


def _miss(store, item, planned, *, reason, late=False):
    receipt = item["rcept_no"]
    record = {"rcept_no": receipt, "corp_code": item["corp_code"], "entry_date": planned["entry_date"],
              "deadline": planned["deadline"], "reason": reason, "no_retroactive_buy": True}
    if store.get("data_not_ready", receipt) is None:
        store.seal("data_not_ready", receipt, record)
    if late and store.get("omissions", receipt) is None:
        store.seal("omissions", receipt, {**record, "reason": "late_discovered_candidate"})


def calendar_run(function):
    @wraps(function)
    def call(store, *args, **kwargs):
        try:
            return function(store, *args, **kwargs)
        except NotReady as exc:
            reason = str(exc)
            if not reason.startswith(("calendar_unverified:", "tax_unverified:")):
                raise
            key = aware(store.clock()).strftime("%Y%m%d") + "." + function.__name__ + "." + sha(reason.encode())[:16]
            store.seal("readiness", key, {"status": "data_not_ready", "reason": reason,
                "phase": function.__name__, "recoverable": True, "events_dropped": False})
            return {"status": "data_not_ready", "reason": reason, "counts": {}, "phase_seconds": {}}
    return call


@verified()
@calendar_run
def prepare_day(store, source, policy, client, *, start=START, end=END):
    """Caller serializes the entire run with Store.lock and the heavy lock.

    A needed calendar gap records recoverable run readiness before input reads.
    Pending missing inputs can retry before cutoff. Final decisions never retry.
    """
    if not store.measurement and (start, end) != (START, END):
        raise ValueError("BB003 production receipt window is fixed")
    status = activation_status(store)
    if status != "active" and not store.measurement:
        return {"status": status, "counts": {}, "phase_seconds": {}}
    begun, phases = timer.perf_counter(), Counter()
    stamp = aware(store.clock())
    if not store.measurement:
        frozen = policy.recording_freeze(stamp.date())
        if frozen and stamp > aware(frozen["F"]):
            raise ValueError("BB003 recorder is closed after F")
    first_pos = policy.entry(start)
    if first_pos < 64:
        raise NotReady("calendar_unverified:63_session_repeat_bootstrap")
    first = policy.sessions[first_pos - 64]
    available_day = pd.Timestamp(stamp.date()) - (pd.Timedelta(days=1) if stamp.hour < 18 else pd.Timedelta(0))
    last = min(available_day, pd.Timestamp(end))
    if last < first:
        return {"status": "waiting_for_receipt_window", "counts": {}, "phase_seconds": {}}
    # Only receipt/entry days are needed now. Official exits are resolved later.
    policy.require(last)
    planned_rows = {}
    for public in pd.date_range(max(pd.Timestamp(start), first), last):
        planned_rows[public.strftime("%Y%m%d")] = policy.entry_schedule(public)
    for planned in planned_rows.values():
        day = planned["entry_date"]
        if aware(planned["deadline"]) > stamp:
            plan = entry_plan(policy, planned["entry_position"])
            store.seal("morning_plan", day, plan)
            put(store.path("morning_plan/" + day + ".json"), encoded(plan))
    phase = timer.perf_counter()
    rows, covered, hashes = Listings(source, store).load(first, last)
    phases["listings"] += timer.perf_counter() - phase
    newly_seen = set()
    for row in rows:
        old = store.get("seen", row["rcept_no"])
        if old:
            if old["listing"] != row:
                raise ValueError("original receipt changed after first discovery")
        else:
            store.seal("seen", row["rcept_no"], {"listing": row})
            newly_seen.add(row["rcept_no"])
    titles = [store.get("seen", key)["listing"] for key in store.keys("seen")]
    counts, preparation = Counter(), {}
    prices = Prices(source, policy, store, as_of=stamp.date())
    for item in rows:
        receipt = item["rcept_no"]
        if not pd.Timestamp(start) <= pd.Timestamp(item["rcept_dt"]) <= pd.Timestamp(end):
            continue
        planned = planned_rows[item["rcept_dt"]]
        deadline = aware(planned["deadline"])
        if store.get("decisions", receipt):
            counts["already_sealed"] += 1
            continue
        if store.get("data_not_ready", receipt):
            counts["already_missing"] += 1
            continue
        miss = store.path("morning_missed/" + planned["entry_date"] + ".json")
        if store.proof("decisions", receipt) or aware(store.clock()) >= deadline or miss.exists():
            pending = [k for k in store.keys("pending") if k.startswith(receipt + ".")]
            reason = load(miss)["reason"] if miss.exists() else store.get("pending", pending[-1])["reason"] if pending else "decision_completion_missing_or_late"
            _miss(store, item, planned, reason=reason, late=receipt in newly_seen)
            counts["data_not_ready"] += 1
            continue
        repeat = repeated(item, titles, policy.sessions)
        reason = "repeat_0_63" if repeat else None
        # Use rcept_dt, never the receipt-number prefix, for every clock.
        if repeat:
            earlier = next(r for r in titles if r["rcept_no"] == repeat["rcept_no"])
            reason = "same_day_repeat" if earlier["rcept_dt"] == item["rcept_dt"] else "repeat_0_63"
        phase = timer.perf_counter()
        pos = planned["entry_position"]
        required = pd.date_range(policy.sessions[policy.entry(item["rcept_dt"]) - 64], pd.Timestamp(item["rcept_dt"]))
        missing = [d.strftime("%Y%m%d") for d in required if d.strftime("%Y%m%d") not in covered]
        fetched = None
        if reason is None and missing:
            _pending(store, receipt, "incomplete_listing_window:" + str(len(missing)))
            counts["pending"] += 1
            continue
        if reason is None:
            fetched, unavailable = acquire(store, item, client, deadline)
            if fetched is None:
                _pending(store, receipt, unavailable)
                phases["fetch_and_verify"] += timer.perf_counter() - phase
                if aware(store.clock()) >= deadline:
                    _miss(store, item, planned, reason=unavailable)
                counts["pending"] += 1
                continue
            reason = exclusion(item, fetched)
        phases["fetch_and_verify"] += timer.perf_counter() - phase
        event = {**item, **planned, "period": "forward"}
        input_hashes = {**hashes, "calendar_tax_policy": store.blob(encoded(policy.data))}
        if fetched:
            input_hashes.update(fetched["input_hashes"])
            quantity, amount = (dart.digits(fetched["row"][k]) for k in dart.REQUIRED_FIELDS[:2])
            event.update(structured=fetched["row"], cancel=dart.cancellation_matches(fetched["row"]["aq_pp"]),
                         planned_quantity=int(quantity) if quantity is not None else None,
                         planned_amount=int(amount) if amount is not None else None)
        phase = timer.perf_counter()
        history = None
        if reason is None:
            try:
                if pos not in preparation:
                    previous = store.proof("entry_history", planned["entry_date"])
                    preparation[pos] = previous or store.seal("entry_history", planned["entry_date"], prices.prepare_entry(pos))
                history = preparation[pos]
                input_hashes.update(history["record"]["input_hashes"])
            except NotReady as exc:
                _pending(store, receipt, str(exc))
                phases["pre_entry_features"] += timer.perf_counter() - phase
                if aware(store.clock()) >= deadline:
                    _miss(store, item, planned, reason=str(exc))
                counts["pending"] += 1
                continue
        phases["pre_entry_features"] += timer.perf_counter() - phase
        recent = sorted({row.get("stock_code") for row in titles if row.get("stock_code") is not None
            and pd.Timestamp(row["rcept_dt"]) < policy.sessions[pos]
            and 0 <= pos - policy.entry(row["rcept_dt"]) <= 63})
        record = {"event": event, "status": "excluded" if reason else "prepared", "reason": reason,
            "repeat_predecessor": repeat, "entry_history_sha256": history["record_sha256"] if history else None,
            "recent_title_codes": recent,
            "matching": ALGORITHM, "matching_sha256": sha(encoded(ALGORITHM)), "input_hashes": input_hashes}
        phase = timer.perf_counter()
        keys = [key for key in store.keys("preparations") if key.startswith(receipt + ".")]
        previous = store.get("preparations", keys[-1]) if keys else None
        if previous and all(previous.get(key) == value for key, value in record.items()):
            proof = store.proof("preparations", keys[-1])
        else:
            proof = store.seal("preparations", f"{receipt}.{len(keys):06d}", record, deadline=deadline)
        phases["preparation_seal"] += timer.perf_counter() - phase
        if proof and proof["valid"]:
            counts["prepared"] += 1
        else:
            _miss(store, item, planned, reason="atomic_completion_missing_or_late")
            counts["data_not_ready"] += 1
    # Completed calendar days without known receipts still need a coverage audit.
    for public, planned in planned_rows.items():
        if aware(store.clock()) >= aware(planned["deadline"]) and not store.get("coverage", public):
            store.seal("coverage", public, {"rcept_dt": public, "deadline": planned["deadline"],
                "status": "complete" if public in covered else "data_not_ready",
                "reason": None if public in covered else "listing_day_missing_at_deadline"})
    prices._load.cache_clear()
    phases["total"] = timer.perf_counter() - begun
    return {"status": "ok", "counts": dict(counts), "phase_seconds": dict(phases)}


@verified()
@calendar_run
def seal_day(store, source, policy):
    """Morning-only price completion; never list, fetch, match or capture here.

    Production callers hold operational_lock(phase='seal'), whose cheap gate
    precedes all hashing. Recheck that gate immediately before each completion.
    """
    begun, counts, phases = timer.perf_counter(), Counter(), Counter()
    day = aware(store.clock()).date().isoformat()
    policy.require(day)
    pos = policy.sessions.get_indexer([pd.Timestamp(day)])[0]
    if pos < 1:
        raise NotReady("morning_entry_session_unverified:" + day)
    plan = entry_plan(policy, pos)
    sealed_plan = store.get("morning_plan", day)
    if sealed_plan is None or any(sealed_plan[k] != v for k, v in plan.items()):
        raise ValueError("BB003 morning plan differs from the sealed official entry calendar")
    store.admitted_plan = plan
    guard = getattr(store, "morning_guard", None)
    if not store.measurement and guard is None:
        raise ValueError("BB003 morning sealing requires admitted operational_lock")
    prices, prepared, features = Prices(source, policy, store, as_of=day), {}, None
    for key in store.keys("preparations"):
        prepared[key.split(".")[0]] = key
    miss = store.path("morning_missed/" + day + ".json")
    for receipt, key in sorted(prepared.items()):
        prep = store.get("preparations", key)
        event = prep["event"]
        if event["entry_date"] != day:
            continue
        if store.get("decisions", receipt):
            counts["already_sealed"] += 1
            continue
        if store.get("data_not_ready", receipt):
            counts["already_missing"] += 1
            continue
        deadline = aware(event["deadline"])
        if miss.exists() or store.proof("decisions", receipt) or aware(store.clock()) >= deadline:
            _miss(store, event, event, reason=load(miss)["reason"] if miss.exists() else "decision_completion_missing_or_late")
            counts["data_not_ready"] += 1
            continue
        reason, event_features, pool, feature_counts = prep["reason"], None, {}, {}
        input_hashes = {**prep["input_hashes"], "preparation": store.blob(store.path(store.proof("preparations", key)["record_path"]).read_bytes())}
        phase = timer.perf_counter()
        if reason is None:
            history = store.proof("entry_history", day)
            if not history or history["record_sha256"] != prep["entry_history_sha256"]:
                raise ValueError("BB003 preparation refers to another price history")
            for checksum in history["record"]["input_hashes"].values():
                store.bytes(checksum)
            try:
                if features is None:
                    features = prices.finish_entry(history["record"])
                rows, feature_counts, price_hashes = features
                input_hashes.update(price_hashes)
            except NotReady as exc:
                _pending(store, receipt, str(exc))
                counts["pending"] += 1
                continue
            event_features = rows.get(event["stock_code"])
            if event_features is None:
                reason = "event_liquidity_or_identity"
            pool = {code: feat for code, feat in rows.items() if code != event["stock_code"] and code not in prep["recent_title_codes"]}
        phases["pre_entry_features"] += timer.perf_counter() - phase
        record = {"event": event, "status": "excluded" if reason else "included", "reason": reason,
            "repeat_predecessor": prep["repeat_predecessor"], "event_features": event_features,
            "pre_entry_candidates": pool, "recent_title_codes": prep["recent_title_codes"],
            "candidate_counts": feature_counts, "matching": prep["matching"],
            "matching_sha256": prep["matching_sha256"], "input_hashes": input_hashes}
        phase = timer.perf_counter()
        proof = store.seal("decisions", receipt, record, deadline=deadline)
        phases["decision_seal"] += timer.perf_counter() - phase
        if proof and proof["valid"]:
            counts[record["status"]] += 1
        else:
            _miss(store, event, event, reason="atomic_completion_missing_or_late")
            counts["data_not_ready"] += 1
    prices._load.cache_clear()
    phases["total"] = timer.perf_counter() - begun
    return {"status": "ok", "counts": dict(counts), "phase_seconds": dict(phases)}


def fixed_match(decision, daily):
    """Read only entry executability fields; no future or entry returns."""
    event, feature = decision["event"], decision["event_features"]
    code, day = event["stock_code"], pd.Timestamp(event["entry_date"])
    reason = bb001.entry_reason(daily.get(code), {"close": feature["pre_close"]}, day)
    if reason:
        return None, reason
    pool = {stock: row for stock, row in decision["pre_entry_candidates"].items()
            if row["market"] == feature["market"]
            and bb001.entry_reason(daily.get(stock), {"close": row["pre_close"]}, day) is None}
    if not pool:
        return None, "matched_controls_below_10"
    size = bb001.empirical_boundaries([f["market_cap"] for f in pool.values()], 3)
    returns = bb001.empirical_boundaries([f["prior20"] for f in pool.values()], 5)
    sb, rb = bb001.lower_bin(feature["market_cap"], size), bb001.lower_bin(feature["prior20"], returns)
    matched = sorted(c for c, f in pool.items() if bb001.lower_bin(f["market_cap"], size) == sb and bb001.lower_bin(f["prior20"], returns) == rb)
    initial, relaxed = len(matched), len(matched) < 10
    if relaxed:
        matched = sorted(c for c, f in pool.items() if bb001.lower_bin(f["prior20"], returns) == rb)
    if len(matched) < 10:
        return None, "matched_controls_below_10"
    return {**event, "event_features": feature, "control_features": {c: pool[c] for c in matched},
            "control_weight": 1 / len(matched), "size_boundaries": size, "return_boundaries": returns,
            "size_bin": sb, "return_bin": rb, "size_relaxed": relaxed, "initial_matched": initial,
            "buyable_pool": sorted(pool)}, None


@verified()
@calendar_run
def post_entry(store, source, policy):
    counts = Counter()
    frozen = policy.recording_freeze(aware(store.clock()).date()) if not store.measurement else None
    if frozen and aware(store.clock()) > aware(frozen["F"]):
        raise ValueError("BB003 post-entry recording closed after F")
    prices = Prices(source, policy, store, as_of=aware(store.clock()).date(), freeze_at=frozen["F"] if frozen else None)
    for key in store.keys("decisions"):
        decision = store.get("decisions", key)
        if not decision or decision["status"] != "included":
            continue
        if store.get("post_entry", key):
            counts["already_sealed"] += 1
            continue
        if decision["matching"] != ALGORITHM or decision["matching_sha256"] != sha(encoded(ALGORITHM)):
            raise ValueError("BB003 fixed matching algorithm changed")
        event = decision["event"]
        policy.require(event["entry_date"])
        available = datetime.combine(pd.Timestamp(event["entry_date"]).date(), time(18), KST)
        if aware(store.clock()) < available:
            counts["waiting_for_entry_daily"] += 1
            continue
        try:
            daily_record = prices.snapshot(event["entry_date"])
        except NotReady:
            counts["waiting_for_entry_daily"] += 1
            continue
        raw = store.bytes(daily_record["input_hashes"]["daily"])
        from .inputs import parse_daily
        daily = parse_daily(raw, pd.Timestamp(event["entry_date"]))
        executable = {c: {k: r[k] for k in ("open", "volume", "base_price", "calendar_status")}
                      for c, r in daily.items() if c == event["stock_code"] or c in decision["pre_entry_candidates"]}
        # JSON has no NaN; an absent base follows BB001's prior-close convention.
        for row in executable.values():
            for field in ("open", "volume", "base_price"):
                if not bb001._positive(row[field]):
                    row[field] = None
        selection, reason = fixed_match(decision, executable)
        proof = store.proof("decisions", key)
        store.seal("post_entry", key, {"rcept_no": key, "status": "excluded" if reason else "executed",
            "reason": reason, "selection": selection, "executability": executable,
            "decision_sha256": proof["record_sha256"], "matching_sha256": decision["matching_sha256"],
            "input_hashes": daily_record["input_hashes"]})
        counts["excluded" if reason else "executed"] += 1
    prices._load.cache_clear()
    return dict(counts)


@verified()
@calendar_run
def capture_prices(store, source, policy, *, start=START, end=None):
    """Preserve first complete daily vintages for all sessions, including V."""
    stamp = aware(store.clock())
    as_of = pd.Timestamp(stamp.date()) - (pd.Timedelta(days=1) if stamp.hour < 18 else pd.Timedelta(0))
    if as_of < pd.Timestamp(start):
        return {}
    frozen = policy.recording_freeze(stamp.date()) if not store.measurement else None
    if frozen:
        end = min(pd.Timestamp(end), pd.Timestamp(frozen["V"])) if end else pd.Timestamp(frozen["V"])
        if aware(store.clock()) > aware(frozen["F"]):
            raise ValueError("BB003 daily acquisition closed after F")
        as_of = min(as_of, pd.Timestamp(frozen["V"]))
    policy.require(as_of)
    prices = Prices(source, policy, store, as_of=as_of, freeze_at=frozen["F"] if frozen else None)
    counts = Counter()
    for day in policy.sessions[(policy.sessions >= start) & (policy.sessions <= min(as_of, pd.Timestamp(end) if end else as_of))]:
        if store.get("prices", day.strftime("%Y%m%d")):
            continue
        try:
            prices.snapshot(day)
            counts["captured"] += 1
        except NotReady:
            counts["missing"] += 1
    return dict(counts)
