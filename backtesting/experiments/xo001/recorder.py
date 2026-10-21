"""Fixed REST polling, immutable raw blocks, cutoff snapshots and KRX reconciliation."""
from datetime import timedelta, timezone
import ctypes
import json
import math
from pathlib import Path
import struct
import time

from . import END, START, Refused, aware
from .activation import CONFIG, profile_from, verify, runtime_verification, verify_unchanged
from .calendar import time_map
from .roster import make_roster, prepare_population
from .storage import read


def clock_evidence():
    """Linux kernel NTP status/error bound, without issuing a network request."""
    buffer = ctypes.create_string_buffer(512)
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.adjtimex(ctypes.byref(buffer))
    if result < 0:
        return {"synchronized": False, "error_bound_seconds": None, "source": "adjtimex_failed"}
    # Linux struct timex: modes(uint), aligned long offset/freq/maxerror/esterror, status(int).
    offset, _, maximum, estimated = struct.unpack_from("@llll", buffer.raw, 8)
    flags = struct.unpack_from("@i", buffer.raw, 40)[0]
    bound = max(maximum, estimated) / 1e6
    return {"synchronized": result != 5 and not flags & 0x40,
            "offset_seconds": offset / (1e9 if flags & 0x2000 else 1e6),
            "error_bound_seconds": bound, "kernel_state": result, "source": "Linux_adjtimex"}


def clock_valid(evidence):
    error = evidence.get("error_bound_seconds")
    return (evidence.get("synchronized") is True and type(error) in (float, int)
            and math.isfinite(error) and 0 <= error <= CONFIG["clock_error_limit_seconds"])


class Recorder:
    def __init__(self, store, client, profile, plan, *, clock_status=clock_evidence):
        self.store, self.client, self.profile, self.plan = store, client, profile, plan
        self.times, self.day, self.clock_status = time_map(plan), plan["date"], clock_status
        self.connection = client.connection_id
        self.latest = {}
        # Reconstruct in-memory state from immutable blocks. Never replace them.
        for key in self._keys("pre_raw"):
            record = store.get("pre_raw", key)
            self.latest[(record["stock_code"], record["endpoint"])] = record

    def _keys(self, kind):
        return [k for k in self.store.keys(kind) if k.startswith(self.day + ".")]

    def _next(self, kind):
        keys = self._keys(kind)
        if keys != [self.day + f".{i:08d}" for i in range(len(keys))]:
            raise Refused("noncontiguous per-day raw journal")
        return self.day + f".{len(keys):08d}"

    def items(self):
        proof = self.store.proof("roster", self.day)
        if not proof:
            raise Refused("roster must be sealed before capture")
        roster = proof["record"]
        result = {}
        for e in roster["events"]:
            result[e["stock_code"]] = "event"
        for key in self._keys("late_event"):
            result[self.store.get("late_event", key)["event"]["stock_code"]] = "event"
        for group in roster["matched"]:
            for code in group["selected"]:
                result.setdefault(code, "matched")
        for group in roster["baseline"]:
            for code in group["selected"]:
                result.setdefault(code, "baseline")
        priorities = {p: i for i, p in enumerate(CONFIG["priority"])}
        return sorted(result.items(), key=lambda item: (priorities[item[1]], item[0]))

    def discover(self, bb003, *, cutoff=None):
        cutoff = min(aware(cutoff or self.store.clock()), self.times["s0859"])
        known = {e["rcept_no"] for e in self.store.proof("roster", self.day)["record"]["events"]}
        for row in bb003.decisions(self.day, cutoff):
            event = row["event"]
            if event["rcept_no"] not in known:
                self.store.seal("late_event", self.day + "." + event["rcept_no"],
                    {"event": event, "input_sha256": row["xo001_input_sha256"], "roster_replaced": False})

    def block(self, code, priority, *, book, phase, deadline, poll_key=None):
        if phase not in {"pre", "post"}:
            raise Refused("invalid capture phase")
        at = aware(self.store.clock())
        start, end = (self.times["pre_start"], self.times["s0859"]) if phase == "pre" else (self.times["open"], self.times["post_end"])
        if (self.store.proof("preseal" if phase == "pre" else "postseal", self.day)
                or not start <= at < end):
            raise Refused("immutable capture segment is closed")
        key = self._next(phase + "_raw")
        began_mono = self.client.monotonic()
        try:
            envelope = self.client.quote(code, book=book, priority=priority, deadline=min(aware(deadline), end))
        except Refused as exc:
            envelope = {"data": None, "raw": "", "error": "request_unobserved",
                "reason": self.client.redactor.text(str(exc)), "requested_utc": at.astimezone(timezone.utc).isoformat(),
                "received_utc": aware(self.store.clock()).astimezone(timezone.utc).isoformat(),
                "requested_monotonic": began_mono, "received_monotonic": self.client.monotonic(),
                "connection_id": self.connection, "sequence": None}
        normalized = self.profile.normalize(envelope, self.day, book=book)
        clock = self.clock_status()
        if not clock_valid(clock):
            normalized.update(state="unknown", reason="clock_unverified")
        received = aware(envelope["received_utc"])
        if normalized.get("provider_at") and aware(normalized["provider_at"]) > received + timedelta(seconds=CONFIG["clock_error_limit_seconds"]):
            normalized.update(state="unknown", reason="provider_event_in_future")
        value = {"date": self.day, "stock_code": code, "priority": priority, "phase": phase,
                 "endpoint": "book" if book else "price", "envelope": envelope, "normalized": normalized,
                 "clock": clock, "sequence": key, "connection_id": envelope["connection_id"], "label": "forward", "poll_key": poll_key}
        if received > end or aware(self.store.clock()) > end:
            # Never put a late receive/publication into the closed pre-open segment.
            value["label"] = "reconstructed"
            return self.store.seal("reconstructed", self._next("reconstructed"), value)["record"]
        proof = self.store.seal(phase + "_raw", key, value)
        if phase == "pre":
            self.latest[(code, value["endpoint"])] = proof["record"]
        return proof["record"]

    def unobserved(self, code, priority, phase, scheduled_at, reason):
        return self.store.seal("unobserved", self._next("unobserved"),
            {"date": self.day, "stock_code": code, "priority": priority, "phase": phase,
             "scheduled_at": aware(scheduled_at).isoformat(), "reason": reason})

    def snapshot(self, name):
        if name not in CONFIG["snapshots"]:
            raise Refused("unknown fixed snapshot")
        old = self.store.get("snapshot", self.day + "." + name)
        if old:
            return old
        if self.store.proof("preseal", self.day):
            raise Refused("no additions after the pre-open manifest")
        cutoff = self.times[name]
        if aware(self.store.clock()) < cutoff:
            raise Refused("snapshot cannot be published before its cutoff")
        states = {}
        for code, priority in self.items():
            candidates = []
            for key in self._keys("pre_raw"):
                proof = self.store.proof("pre_raw", key)
                row = proof["record"]
                if (row["stock_code"] == code and row["endpoint"] == "book"
                        and aware(row["envelope"]["received_utc"]) <= cutoff
                        and aware(proof["publication_not_after"]) <= self.times["publish"]):
                    candidates.append((key, row))
            if not candidates:
                states[code] = {"state": "unknown", "reason": "no_received_book", "priority": priority}
                continue
            key, latest = candidates[-1]
            state = {**latest["normalized"], "priority": priority, "raw_key": key,
                     "last_received_utc": latest["envelope"]["received_utc"],
                     "last_provider_at": latest["normalized"].get("provider_at")}
            age = (cutoff - aware(latest["envelope"]["received_utc"])).total_seconds()
            if age > CONFIG["max_book_age_seconds"]:
                state.update(state="unknown", reason="stale_book")
            states[code] = state
        return self.store.seal("snapshot", self.day + "." + name,
            {"date": self.day, "as_of": cutoff.isoformat(), "states": states,
             "label": "forward" if self.store.proof("roster", self.day)["valid"]
                 and self.store.proof("roster", self.day)["record"]["label"] == "forward" else "reconstructed"}, deadline=self.times["publish"])["record"]

    def seal_pre(self):
        old = self.store.proof("preseal", self.day)
        if old:
            return old
        if aware(self.store.clock()) < self.times["s0859"]:
            raise Refused("pre-open seal cannot precede 08:59")
        for name in CONFIG["snapshots"]:
            self.snapshot(name)
        raw = {key: self.store.proof("pre_raw", key)["record_sha256"] for key in self._keys("pre_raw")}
        snapshots = {name: self.store.proof("snapshot", self.day + "." + name)["record_sha256"] for name in CONFIG["snapshots"]}
        return self.store.seal("preseal", self.day, {"date": self.day,
            "as_of": self.times["s0859"].isoformat(), "raw_hashes": raw, "snapshot_hashes": snapshots,
            "clock": self.clock_status(), "closed": True}, deadline=self.times["publish"])

    def seal_post(self):
        old = self.store.proof("postseal", self.day)
        if old:
            return old
        if aware(self.store.clock()) < self.times["post_end"]:
            raise Refused("post-open record cannot close before 09:05")
        states, hashes = {}, {}
        for key in self._keys("post_raw"):
            proof = self.store.proof("post_raw", key)
            hashes[key] = proof["record_sha256"]
        for code, priority in self.items():
            prints, absent, delayed = {}, False, False
            for key in hashes:
                record = self.store.get("post_raw", key)
                if record["stock_code"] != code:
                    continue
                value = record["normalized"]
                if not value.get("provider_at"):
                    continue
                event = aware(value["provider_at"])
                if not self.times["open"] <= event < self.times["post_end"] or not clock_valid(record["clock"]):
                    continue
                absent |= value["state"] == "confirmed_absent"
                delayed |= value.get("opening_delayed", False)
                if value["state"] == "observed" and record["endpoint"] == "price":
                    identity = (value["provider_at"], value["price"], value["quantity"])
                    prints.setdefault(identity, {**value, "received_utc": record["envelope"]["received_utc"], "raw_key": key})
            ordered = sorted(prints.values(), key=lambda p: (p["provider_at"], p["received_utc"]))
            first_minute = [p for p in ordered if aware(p["provider_at"]) < self.times["first_minute_end"]
                            and aware(p["received_utc"]) < self.times["first_minute_end"]]
            auction = next((p for p in ordered if p["auction_confirmed"]), None)
            first = next((p for p in ordered if p.get("first_trade_confirmed")), None)
            if first and aware(first["provider_at"]) > self.times["open"]:
                delayed = True
            state = "observed" if first else "confirmed_absent" if absent and not ordered else "unknown"
            states[code] = {"state": state, "priority": priority, "first_print": first,
                "earliest_sampled_print": ordered[0] if ordered else None,
                "first_60s_sampled_prints": first_minute, "first_60s_total_volume": None,
                "total_volume_reason": "REST sampling is not a complete trade tape",
                "opening_category": "official_halt" if absent and not ordered else "opening_delayed" if delayed else "auction_confirmed" if auction else "unknown",
                "auction_price": auction["price"] if auction else None,
                "auction_volume": auction["auction_volume"] if auction else None}
        return self.store.seal("postseal", self.day, {"date": self.day, "states": states, "raw_hashes": hashes,
            "clock": self.clock_status(), "sampling": "REST individual last-print samples; not exhaustive tape",
            "label": "forward" if aware(self.store.clock()) <= self.times["post_end"] + timedelta(seconds=30) else "reconstructed"})

    def denominator(self, bb003):
        decisions = bb003.decisions(self.day, self.times["s0859"])
        value = {"date": self.day, "events": [{"event": d["event"], "event_features": d["event_features"],
                  "decision_sha256": d["xo001_input_sha256"]} for d in decisions], "before_entry_executability": True}
        return self.store.seal("denominator", self.day, value)


def record_omissions(store, bb003, day):
    for kind in ("data_not_ready", "omissions"):
        for key in bb003.keys(kind):
            row = bb003.get(kind, key)
            if row and row["entry_date"] == day:
                store.seal("omission", day + "." + kind + "." + key,
                    {"date": day, "rcept_no": row["rcept_no"], "reason": row["reason"],
                     "source_kind": kind, "input_sha256": row["xo001_input_sha256"], "events_dropped": False})


class DailyPending(Refused):
    """The whole-market source has not finished collecting both markets."""


def reconcile(store, source, calendar, bb003, day):
    verification = runtime_verification(store)
    old = store.proof("reconciliation", day)
    if old:
        verify_unchanged(store, verification)
        return old
    plan = calendar.day(day)
    if not plan["session"] or aware(store.clock()) < time_map(plan)["close"]:
        raise Refused("KRX daily reconciliation requires a completed verified session")
    freeze = calendar.freeze() if aware(store.clock()).date().isoformat() > END else None
    if freeze and aware(store.clock()) > freeze:
        raise Refused("KRX reconciliation acquisition is closed after freeze")
    path = Path(source) / f"market/krx_daily/{day[:4]}/{day.replace('-', '')}.jsonl"
    raw, rows = read(path), {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("trade_date") != day or row.get("calendar_status") != "verified" or row["stock_code"] in rows:
            raise Refused("invalid KRX reconciliation row")
        rows[row["stock_code"]] = row
    if not {"KOSPI", "KOSDAQ"} <= {r.get("market") for r in rows.values()}:
        raise DailyPending("partial_whole_market_daily_is_not_a_suspension")
    digest = store.blob(raw)
    if bb003 is not None:
        record_omissions(store, bb003, day)
    verify_unchanged(store, verification)
    return store.seal("reconciliation", day, {"date": day, "daily_sha256": digest, "rows": rows,
        "verification": verification}, deadline=freeze)


def reconcile_pending(store, source, calendar, bb003, *, day=None):
    """Immutable due records survive restarts; only complete two-market days close."""
    verification = runtime_verification(store)
    def complete(due):
        proof = store.proof("reconciliation", due)
        return bool(proof and proof["valid"] or store.proof("reconcile_closed", due))

    days = {day} if day else set()
    for kind in ("roster", "runs", "denominator", "missing_day"):
        days.update(store.proof(kind, key)["record"]["date"] for key in store.keys(kind))
    for key in bb003.keys("decisions"):
        row = bb003.get("decisions", key)
        if row and row["status"] == "included":
            days.add(row["event"]["entry_date"])
    for due in sorted(days):
        if due <= aware(store.clock()).date().isoformat():
            store.seal("reconcile_due", due, {"date": due})
    if aware(store.clock()).date().isoformat() > END and aware(store.clock()) > calendar.freeze():
        verify_unchanged(store, verification)
        return {"status": "frozen", "verification": verification,
            "pending": [k for k in store.keys("reconcile_due") if not complete(k)]}
    completed, pending = [], []
    for due in store.keys("reconcile_due"):
        if complete(due):
            continue
        plan = calendar.day(due)
        if not plan["session"]:
            store.seal("reconcile_closed", due, {"date": due, "reason": "official_non_session"})
            continue
        if aware(store.clock()) < time_map(plan)["close"]:
            pending.append(due)
            continue
        try:
            proof = reconcile(store, source, calendar, bb003, due)
        except (FileNotFoundError, DailyPending):
            pending.append(due)
        else:
            (completed if proof["valid"] else pending).append(due)
    verify_unchanged(store, verification)
    return {"status": "reconciled", "completed": completed, "pending": pending, "verification": verification}


def run_morning(store, client, bb003, source, calendar, binding, *, shakedown=False,
                diagnostic=None, sleeper=time.sleep, clock_status=clock_evidence):
    day = aware(store.clock()).date().isoformat()
    try:
        plan = calendar.day(day)
    except Refused as exc:
        store.seal("missing_day", day, {"date": day, "reason": str(exc), "orders": False})
        return {"status": "calendar_unverified", "date": day}
    if not plan["session"]:
        return {"status": "closed", "date": day}
    if shakedown:
        if not "2026-11-01" <= day <= "2026-11-30" or not store.get("shakedown_plan", "november"):
            raise Refused("November shakedown requires the predetermined test plan")
    else:
        verify(store, binding)
        if day < START or day > END and day > calendar.last_entry():
            return {"status": "outside_fixed_observation_period"}
    times = time_map(plan)
    population = None
    if aware(store.clock()) < times["roster"]:
        try:
            population = prepare_population(source, calendar, day, store)
        except (OSError, Refused):
            # make_roster will record the explicit unavailable input, with no partial draw.
            pass
    store.seal("reconcile_due", day, {"date": day})
    # Reserve the full one-second kernel evidence bound before the roster cutoff.
    while aware(store.clock()) < times["roster"] - timedelta(seconds=2):
        sleeper(min(.2, (times["roster"] - timedelta(seconds=2) - aware(store.clock())).total_seconds()))
    roster = make_roster(store, bb003, source, calendar, plan, population=population)
    while aware(store.clock()) < times["roster"]:
        sleeper(min(.2, (times["roster"] - aware(store.clock())).total_seconds()))
    recorder = Recorder(store, client, profile_from(store), plan, clock_status=clock_status)
    run_key = day + "." + client.connection_id
    prior = [k for k in store.keys("runs") if k.startswith(day + ".")]
    store.seal("runs", run_key, {"date": day, "binding": binding, "shakedown": shakedown,
        "restart_recovered": bool(prior), "prior_connections": prior, "clock": clock_status(), "plan": plan})
    if diagnostic is not None and diagnostic.enabled() and clock_valid(clock_status()) and not store.get("preorder_reconciliation", day):
        cutoff = times["s0859"] - timedelta(seconds=CONFIG["pre_poll_stop_seconds_before_0859"])
        if aware(store.clock()) < cutoff:
            unresolved_days = diagnostic.pending_dates()
            for order_day in unresolved_days:
                diagnostic.inquire(order_day, deadline=cutoff)
            store.seal("preorder_reconciliation", day, {"date": day, "order_days": unresolved_days})
    pre_index = post_index = 0
    queue, snapshots = [], set()
    while aware(store.clock()) < times["post_end"]:
        at = aware(store.clock())
        pre_stop = times["s0859"] - timedelta(seconds=CONFIG["pre_poll_stop_seconds_before_0859"])
        phase = "pre" if at < pre_stop else "post" if at >= times["open"] else "gap"
        if at >= times["s0859"] and not store.proof("denominator", day):
            recorder.discover(bb003, cutoff=times["s0859"])
            recorder.denominator(bb003)
            if diagnostic is not None:
                diagnostic.record_omitted_targets(plan, bb003.decisions(day, times["s0859"]))
        for name in CONFIG["snapshots"]:
            if at >= times[name] and name not in snapshots and not store.proof("preseal", day):
                try:
                    recorder.snapshot(name)
                except Refused:
                    store.seal("missing_preseal", day, {"date": day, "reason": "08:59:30_completion_not_proven"})
                snapshots.add(name)
        if at >= times["s0859"] and not store.proof("preseal", day) and not store.get("missing_preseal", day):
            try:
                recorder.seal_pre()
            except Refused:
                store.seal("missing_preseal", day, {"date": day, "reason": "08:59:30_completion_not_proven"})
        if diagnostic is not None and clock_valid(clock_status()):
            if times["s0859"] - timedelta(seconds=3) <= at < times["s0859"]:
                diagnostic.seal_intents(plan, bb003.decisions(day, at))
            elif times["submit"] <= at < times["open"]:
                diagnostic.submit(plan)
            elif at >= times["cancel"] and not store.get("cancel_run", day):
                for order_day in diagnostic.pending_dates():
                    diagnostic.inquire(order_day, deadline=times["post_end"])
                diagnostic.cancel(plan)
                for order_day in diagnostic.pending_dates():
                    diagnostic.inquire(order_day, deadline=times["post_end"])
                store.seal("cancel_run", day, {"date": day})
        if phase != "gap":
            anchor, interval = (times["pre_start"], CONFIG["pre_poll_seconds"]) if phase == "pre" else (times["open"], CONFIG["post_poll_seconds"])
            index = int((at - anchor).total_seconds() // interval)
            previous = pre_index if phase == "pre" else post_index
            if index >= previous:
                if phase == "pre":
                    recorder.discover(bb003)
                    pre_index = index + 1
                else:
                    post_index = index + 1
                for code, priority, book, scheduled, deadline, old_phase in queue:
                    recorder.unobserved(code, priority, old_phase, scheduled, "fixed_poll_slot_expired")
                scheduled = anchor + timedelta(seconds=index * interval)
                end = min(scheduled + timedelta(seconds=interval), pre_stop if phase == "pre" else times["post_end"])
                queue = [(code, priority, book, scheduled, end, phase) for code, priority in recorder.items()
                         for book in ((True, False) if phase == "pre" else (False,))]
            if queue:
                code, priority, book, scheduled, end, queued_phase = queue.pop(0)
                if at >= end:
                    recorder.unobserved(code, priority, queued_phase, scheduled, "fixed_poll_slot_expired")
                else:
                    poll_key = day + "." + queued_phase + f".{index:05d}." + code + (".book" if book else ".price")
                    if not store.proof("poll_attempt", poll_key):
                        store.seal("poll_attempt", poll_key, {"date": day, "phase": queued_phase,
                            "stock_code": code, "book": book, "scheduled_at": scheduled.isoformat()})
                        record = recorder.block(code, priority, book=book, phase=queued_phase, deadline=end, poll_key=poll_key)
                        store.seal("poll_outcome", poll_key, {"date": day, "raw_sequence": record["sequence"], "label": record["label"]})
                    elif not store.proof("poll_outcome", poll_key):
                        recorder.unobserved(code, priority, queued_phase, scheduled, "restart_after_attempt_outcome_unknown")
                        store.seal("poll_outcome", poll_key, {"date": day, "raw_sequence": None, "label": "unknown"})
                continue
        sleeper(.2)
    for code, priority, book, scheduled, deadline, phase in queue:
        recorder.unobserved(code, priority, phase, scheduled, "fixed_poll_slot_expired")
    recorder.seal_post()
    if diagnostic is not None:
        diagnostic.finish_submissions(plan, clock_verified=clock_valid(clock_status()))
    recorder.denominator(bb003)
    record_omissions(store, bb003, day)
    return {"status": "shakedown_recorded" if shakedown else "recorded", "date": day,
            "roster_events": len(roster["events"]), "preseal_valid": bool((store.proof("preseal", day) or {}).get("valid")),
            "restart_recovered": bool(prior)}
