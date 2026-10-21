"""Daily immutable input → decision → independent close books."""
import math
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.lh003 import pins

from . import END, FREEZE_AT, MULTIPLIERS, START, Refused
from .calendar import Calendar
from .controls import initial_controls, replace
from .inputs import payload
from .ledger import equal_weights, initial_state, rebalance, targets
from .runner import implementation_hashes
from .storage import aware, cutoff, digest, heavy_lock, lock, now, publication_time, publish, seal, timely, verified


def book_name(kind, multiplier, index=None):
    return f"{kind}{'' if index is None else ':' + str(index).zfill(3)}:{multiplier}"


def initial_books():
    return {book_name(kind, m, k): initial_state() for m in MULTIPLIERS
            for kind in ("ld", "ew", "decomposition", "av", "random")
            for k in (range(200) if kind == "random" else [None])}


class ForwardRunner:
    def __init__(self, root, *, calendar=None, clock=now, publication_clock=publication_time,
                 repo_root=PROJECT_ROOT, synthetic=False, code_verifier=None):
        self.root, self.calendar, self.clock = Path(root), calendar or Calendar(), clock
        self.publication_clock, self.repo_root, self.synthetic = publication_clock, Path(repo_root), synthetic
        if code_verifier is not None and not synthetic:
            raise Refused("injected code verifier is fixture-only")
        self.code_verifier = code_verifier

    def code(self):
        if self.code_verifier:
            return self.code_verifier()
        return pins.verify(self.root, self.repo_root, at=self.clock(), current=implementation_hashes(self.repo_root))

    def start(self):
        value = verified(self.root / "start.json")
        if value["synthetic"] and not self.synthetic:
            raise Refused("synthetic start cannot create observations")
        self.code()
        return value

    def path(self, kind, day):
        return self.root / kind / (day + ".json")

    def omission(self, day, reason):
        path = self.path("omissions", day)
        if path.exists():
            return verified(path)
        value = {"day": day, "reason": reason, "at": aware(self.clock()).isoformat()}
        seal(path, value)
        return value

    def previous(self, day):
        if day == START:
            return None
        value = self.ledger(self.calendar.previous(day))
        if value and value.get("termination") and not self.termination():
            seal(self.root / "termination.json", value["termination"])
        return value

    def ledger(self, day, *, allow_incomplete=False):
        path = self.path("ledgers", day)
        if not path.exists():
            return None
        value = verified(path)
        if not allow_incomplete and not all(value["required_files"].get(m) is True for m in ("KOSPI", "KOSDAQ")):
            return None
        marker = path.with_suffix(".publication") / "complete.json"
        acknowledgement = self.path("ledger_finalizations", day)
        if not marker.exists() or not acknowledgement.exists():
            return value if self.synthetic else None
        # Validate the receipt even for a late ledger; evaluation separately
        # excludes seals after the fixed freeze. This timestamp is the commit.
        timely(path, publication_clock=self.publication_clock)
        completion = verified(acknowledgement)
        if completion["record_sha256"] != digest(value):
            raise Refused("ledger finalization acknowledgement differs from the record")
        finalized = aware(completion["finalized_at"]).isoformat()
        value = {**value, "finalized_at": finalized, "termination": completion["termination"]}
        if value.get("pending_drawdown") and value["termination"] is None:
            raise Refused("finalized drawdown lacks an immutable termination signal")
        return value

    def acknowledge_ledger(self, day):
        path = self.path("ledger_finalizations", day)
        if not path.exists():
            # Sample only after the state and its publication marker are durable.
            # If interrupted before this acknowledgement, recovery finalizes now;
            # it cannot invent an earlier recognition time.
            record = verified(self.path("ledgers", day))
            finalized = aware(self.clock()).isoformat()
            termination = self.termination() or record["termination"]
            if record["pending_drawdown"] and termination is None:
                termination = self._termination("terminated_drawdown", finalized,
                    "LD x1 close NAV drawdown >= 25%")
            seal(path, {"record_sha256": digest(record), "finalized_at": finalized, "termination": termination})

    def input(self, day):
        path = self.path("inputs", day)
        return verified(path) if timely(path, publication_clock=self.publication_clock) else None

    def recover_publication(self, kind, day, hour):
        path = self.path(kind, day)
        if (path.exists() and not path.with_suffix(".publication").joinpath("complete.json").exists()
                and aware(self.clock()).date().isoformat() == day and aware(self.clock()) < cutoff(day, hour)):
            value = verified(path)
            if aware(value["prepared_at"]) < cutoff(day, hour):
                publish(path, value, deadline=cutoff(day, hour), clock=self.clock, publication_clock=self.publication_clock)

    def decision(self, day):
        path = self.path("decisions", day)
        if not path.exists():
            return None
        value = verified(path)
        return value if timely(path, publication_clock=self.publication_clock) else {**value, "valid": False,
            "label": "reconstructed", "reason": "decision_late"}

    def termination(self):
        path = self.root / "termination.json"
        return verified(path) if path.exists() else None

    def _termination(self, status, recognized_at, reason):
        stamp = aware(recognized_at)
        day = stamp.date().isoformat()
        sessions = self.calendar.sessions(day, day)
        first = day if sessions and stamp < cutoff(day, 12) else self.calendar.next(day)
        return {"status": status, "recognized_at": stamp.isoformat(), "liquidation_start": first, "reason": reason}

    def terminate(self, status, *, recognized_at=None, reason):
        if status not in {"terminated_drawdown", "model_changed", "forward_aborted"}:
            raise Refused("invalid termination status")
        existing = self.termination()
        if existing:
            return existing
        value = self._termination(status, self.clock() if recognized_at is None else recognized_at, reason)
        seal(self.root / "termination.json", value)
        return value

    def sync_model_stop(self):
        # Caller holds the state lock. Acknowledgements survive a crash before
        # the separate termination publication, including on the final session.
        candidates = []
        existing = self.termination()
        if existing:
            candidates.append(existing)
        for path in sorted((self.root / "ledgers").glob("*.json")):
            approval = self.path("ledger_finalizations", path.stem)
            if approval.exists():
                if verified(approval)["termination"] is None:
                    continue
            elif not self.synthetic:
                continue
            record = self.ledger(path.stem, allow_incomplete=True)
            if record and record.get("termination"):
                candidates.append(record["termination"])
        path = self.root / "model_changed.json"
        if path.exists():
            candidates.append(verified(path))
        if candidates:
            value = min(candidates, key=lambda c: aware(c["recognized_at"]))
            if "liquidation_start" not in value:
                value = self._termination(value["status"], value["recognized_at"], value["reason"])
            if existing and existing != value:
                raise Refused("published termination differs from earliest durable recognition")
            if not existing:
                seal(self.root / "termination.json", value)

    def action_for_book(self, events, name, state, day, entitlement_books):
        """Resolve official entitlements separately for each book's actual shares."""
        result = []
        for event in events:
            if event["type"] == "split":
                result.append(event)
                continue
            if event["type"] == "recovery":
                quantity = state["positions"].get(event["code"], {}).get("quantity", 0)
            else:
                entitled_day = event["entitlement_day"]
                if entitled_day >= day:
                    raise Refused("cash/share actions require a sealed earlier entitlement close")
                quantity = (entitlement_books[entitled_day][name]["state"]["positions"].get(event["code"], {}).get("quantity", 0)
                            if entitled_day >= START else 0)
            value = {**event, "entitlement_quantity": quantity, "cash_delta": quantity * event["cash_per_share"]}
            if event["type"] == "rights":
                shares = quantity * event["shares_per_share"]
                fraction = shares - math.floor(shares)
                if fraction:
                    if "fraction_cash_price" not in event:
                        raise Refused("fractional rights require an explicit official cash-in-lieu price")
                    value["cash_delta"] += fraction * event["fraction_cash_price"]
                shares = math.floor(shares)
                old = state["positions"].get(event["code"])
                value["quantity_delta"] = int(shares)
                value["avg_cost_after"] = ((old["quantity"] * old["avg_cost"] + max(0, -value["cash_delta"]))
                    / (old["quantity"] + shares)) if old and old["quantity"] + shares else max(0, -value["cash_delta"]) / shares if shares else 0
            result.append(value)
        return result

    def input_seal(self, day, builder, *, heavy_path):
        self.start()
        try:
            self.calendar.check(day)
        except ValueError as exc:
            return self.omission(day, "calendar_unverified:" + str(exc))
        if aware(self.clock()).date().isoformat() != day:
            return self.omission(day, "input_not_sealed")
        path = self.path("inputs", day)
        with lock(self.root / ".state.lock"):
            if path.exists():
                self.recover_publication("inputs", day, 10)
                return self.input(day) or self.omission(day, "input_not_sealed")
            if self.path("omissions", day).exists():
                return verified(self.path("omissions", day))
            previous = self.previous(day)
            if aware(self.clock()) >= cutoff(day, 10):
                return self.omission(day, "ledger_not_final" if day != START and previous is None else "input_not_sealed")
            if day != START and previous is None:
                return {"day": day, "reason": "ledger_not_final"}
            try:
                with heavy_lock(heavy_path):
                    snapshot = builder(initial_state() if previous is None else previous["books"][book_name("ld", 1.0)]["state"])
                    self.code()
            except BlockingIOError:
                return {"day": day, "reason": "heavy_lock_busy"}
            if snapshot["decision_day"] != day or snapshot["as_of"] != self.calendar.previous(day):
                raise Refused("input date or close-known cutoff mismatch")
            if aware(self.clock()) >= cutoff(day, 10):
                return self.omission(day, "input_not_sealed")
            value = {"day": day, "snapshot": snapshot, "payload": payload(snapshot), "code_pins": self.code(),
                     "previous_ledger_sha256": digest(previous), "prepared_at": aware(self.clock()).isoformat()}
            if not publish(path, value, deadline=cutoff(day, 10), clock=self.clock, publication_clock=self.publication_clock):
                return self.omission(day, "input_not_sealed")
            return value

    def decide(self, day, llm):
        self.start()
        self.calendar.check(day)
        with lock(self.root / ".state.lock"):
            self.sync_model_stop()
            self.recover_publication("decisions", day, 12)
            existing = self.decision(day)
            if existing:
                return existing
            inputs = self.input(day)
            previous = self.previous(day)
            reason = ("terminated" if self.termination() else "ledger_not_final" if day != START and previous is None else
                      "ledger_not_final" if inputs and inputs["previous_ledger_sha256"] != digest(previous) else
                      verified(self.path("omissions", day))["reason"] if self.path("omissions", day).exists() else
                      "input_not_sealed" if inputs is None else None)
            if aware(self.clock()).date().isoformat() != day or aware(self.clock()) >= cutoff(day, 12):
                reason = reason or "decision_late"
            result = ({"valid": False, "reason": reason, "output": None, "attempts": []} if reason else
                      llm.decide(inputs["payload"], day=day, deadline=cutoff(day, 12)))
            self.sync_model_stop()
            self.code()
            result = {**result, "day": day, "input_sha256": digest(inputs), "code_pins": self.code(),
                      "prepared_at": aware(self.clock()).isoformat(), "label": "forward",
                      "targets": targets(result["output"]) if result["valid"] else None,
                      "universe": inputs["snapshot"]["universe"] if inputs else [],
                      "adv20": inputs["snapshot"]["adv20"] if inputs else {},
                      "execution_plan": "T-close pre-trade NAV; sell then buy; quantities deferred"}
            path = self.path("decisions", day)
            publish(path, result, deadline=cutoff(day, 12), clock=self.clock, publication_clock=self.publication_clock)
            return self.decision(day)

    def _calculate(self, day, quotes, required_files, actions, *, cost_fn=None, liquidation_adv20=None):
        previous, inputs, decision = self.previous(day), self.input(day), self.decision(day)
        if day != START and previous is None:
            raise Refused("previous ledger is not final; finalize in date order")
        states = initial_books() if previous is None else {name: book["state"] for name, book in previous["books"].items()}
        control_state = initial_controls() if previous is None else previous["control_state"]
        term = self.termination()
        stopped_today = bool(term and aware(term["recognized_at"]) <= aware(self.calendar.close(day)))
        valid = bool(decision and decision["valid"] and not stopped_today)
        snapshot = inputs["snapshot"] if inputs else None
        universe, adv = (snapshot["universe"], snapshot["adv20"]) if snapshot else ([], {})
        output = decision["output"] if valid else None
        controls, plans = replace(control_state, output, universe, valid=valid)
        liquidate = bool(term and day >= term["liquidation_start"])
        missing = not all(required_files.get(m) is True for m in ("KOSPI", "KOSDAQ"))
        av_signal = previous["av_signal"] if previous else None
        new_av = snapshot["av"]["decision_sha256"] if snapshot else av_signal
        entitlement_books, entitlement_hashes = {}, {}
        for entitlement_day in sorted({e["entitlement_day"] for e in actions if e["type"] in {"dividend", "rights"}}):
            if entitlement_day >= day:
                raise Refused("cash/share actions require a sealed earlier entitlement close")
            if entitlement_day < START:
                entitlement_hashes[entitlement_day] = None  # No shares existed before the cash-only T0.
                continue
            entitlement = self.ledger(entitlement_day)
            if entitlement is None:
                raise Refused("corporate action entitlement ledger is not final")
            entitlement_books[entitlement_day], entitlement_hashes[entitlement_day] = entitlement["books"], digest(entitlement)
        books = {}
        for m in MULTIPLIERS:
            for kind in ("ld", "ew", "decomposition", "av", "random"):
                for k in (range(200) if kind == "random" else [None]):
                    name = book_name(kind, m, k)
                    hold, constrained, eligible, weights = not valid, True, set(universe), {}
                    if kind == "ld":
                        weights = decision["targets"]["weights"] if valid else {}
                        hold = not valid
                    elif kind == "decomposition":
                        weights = targets(output, level=100)["weights"] if valid else {}
                    elif kind == "ew":
                        hold, constrained = snapshot is None or not self.calendar.monthly(day), False
                        weights = equal_weights(universe)
                    elif kind == "av":
                        hold, constrained = snapshot is None or new_av == av_signal, False
                        weights = {c: min(.10, w) for c, w in snapshot["av"]["weights"].items()} if snapshot else {}
                        eligible = set(weights)
                    elif valid:
                        weights = plans[k]["weights"]
                    if kind == "ld" and liquidate:
                        hold = False
                    # Liquidation still needs prior-close ADV, even on a missing input day.
                    effective_adv = liquidation_adv20 if kind == "ld" and liquidate and liquidation_adv20 is not None else adv
                    book_actions = self.action_for_book(actions, name, states[name], day, entitlement_books)
                    books[name] = rebalance(states[name], weights, quotes, effective_adv, eligible, day, multiplier=m,
                        constrained=constrained, liquidate=kind == "ld" and liquidate, hold=hold,
                        missing_day=missing, actions=book_actions, **({"cost_fn": cost_fn} if cost_fn else {}))
        return {"day": day, "books": books, "control_state": controls, "random_plans": plans,
            "valid_decision": valid, "input_sha256": digest(inputs), "decision_sha256": digest(decision),
            "previous_sha256": digest(previous), "required_files": required_files, "price_input": quotes,
            "corporate_actions": list(actions), "av_signal": new_av if snapshot else av_signal,
            "entitlement_ledger_hashes": entitlement_hashes,
            "liquidation_adv20": liquidation_adv20,
            "sealed_adv20": adv,
            "missing_reason": None if valid else "terminated" if stopped_today else decision["reason"] if decision else "missing_decision"}

    def finalize(self, day, quotes, *, required_files, actions=(), cost_fn=None, audit=False, execution_sources=None,
                 liquidation_adv20=None, indices=None):
        self.start()
        self.calendar.check(day)
        if aware(self.clock()) <= aware(self.calendar.close(day)):
            raise Refused("cannot finalize before the decision session closes")
        if not self.synthetic and execution_sources is None:
            raise Refused("production finalization requires execution source hashes")
        path = self.path("ledgers", day)
        with lock(self.root / ".state.lock"):
            self.sync_model_stop()
            if path.exists() and not audit:
                if not all(verified(path)["required_files"].get(m) is True for m in ("KOSPI", "KOSDAQ")):
                    raise Refused("incomplete primary ledger cannot be finalized or repaired by an audit")
                marker = path.with_suffix(".publication") / "complete.json"
                if not marker.exists():
                    publish(path, verified(path), deadline=aware(FREEZE_AT), clock=self.clock,
                            publication_clock=self.publication_clock)
                self.acknowledge_ledger(day)
                value = self.ledger(day)
                if value.get("termination") and not self.termination():
                    seal(self.root / "termination.json", value["termination"])
                return value
            if audit and self.ledger(day) is None:
                raise Refused("audit correction requires an original daily seal")
            if not all(required_files.get(m) is True for m in ("KOSPI", "KOSDAQ")):
                raise Refused("ledger_not_final: both verified market close files are required")
            value = self._calculate(day, quotes, required_files, actions, cost_fn=cost_fn, liquidation_adv20=liquidation_adv20)
            stamp = aware(self.clock()).isoformat()
            ld = value["books"][book_name("ld", 1.0)]["state"]
            termination = self.termination()
            pending_drawdown = termination is None and ld["nav"] <= .75 * ld["peak_nav"]
            value.update(prepared_at=stamp, finalized_at=stamp, termination=termination,
                         pending_drawdown=pending_drawdown, code_pins=self.code(),
                         execution_sources=execution_sources or {}, indices=indices)
            if audit:
                original = self.ledger(day)
                value.update(audit_only=True, original_sha256=digest(original),
                    nav_differences={n: b["state"]["nav"] - original["books"][n]["state"]["nav"] for n, b in value["books"].items()})
                seal(self.root / "audits" / day / (digest([quotes, actions, required_files]) + ".json"), value)
            else:
                publish(path, value, deadline=aware(FREEZE_AT), clock=self.clock,
                        publication_clock=self.publication_clock)
                self.acknowledge_ledger(day)
                value = self.ledger(day)
                termination = value["termination"]
                if termination and not self.termination():
                    seal(self.root / "termination.json", termination)
            return value
