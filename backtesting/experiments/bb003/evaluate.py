"""PC-only single evaluation after F. No selection from returns or live files."""
import math
import json

import numpy as np
import pandas as pd

from backtesting.experiments import bb001
from . import END, EXPERIMENT_ID, MINIMUM_EFFECT, START, NotReady, aware
from .inputs import Prices
from .recorder import activation_status
from .deployment import verified, verify_operation
from .storage import encoded, put


def implementation_check(store):
    return verify_operation(store, mode="evaluation")


def metrics(rows, grid, *, worthless=False):
    mean = lambda xs: float(np.mean(xs)) if xs else None
    return {"primary": bb001._summary(rows, grid, 21, worthless=worthless),
        "corporations": len({r["corp_code"] for r in rows}), "dates": len({r["entry_date"] for r in rows}),
        "cost_sensitivity": {key: bb001._summary(rows, grid, 21, key=key, worthless=worthless)
                             for key in ("1.0", "1.5", "2.0", "v1")},
        "random_control": bb001.random_controls(rows, worthless=worthless),
        "entry_date_cluster_t": bb001.cluster_t([bb001.event_excess(r, worthless=worthless) for r in rows],
                                                [r["entry_date"] for r in rows]),
        "event_net_mean": mean([bb001._net(r["stock"], worthless=worthless) for r in rows]),
        "controls_net_mean": mean([mean([bb001._net(p, worthless=worthless) for p in r["controls"]]) for r in rows]),
        "vanished_counts": {"events": sum(r["stock"]["vanished"] for r in rows),
                            "controls": sum(p["vanished"] for r in rows for p in r["controls"])}}


def judge_one(result, *, required_data_complete):
    values = (result["primary"]["mean"], result["primary"]["t_stat"],
              result["cost_sensitivity"]["1.5"]["mean"], result["random_control"]["share_of_controls"])
    missing = (not required_data_complete or result["primary"]["count"] < 100
               or result["corporations"] < 40 or result["dates"] < 50
               or any(v is None or not math.isfinite(v) for v in values))
    gates = [] if missing else [values[0] >= MINIMUM_EFFECT, values[1] > 2, values[2] > 0, values[3] <= .05]
    return {"verdict": "insufficient" if missing else "pass" if all(gates) else "fail",
            "gates": dict(zip(("effect_at_least_half_design", "HAC_t_gt_2", "cost_1_5_positive", "random_share_le_5pct"), gates)),
            "minimum_events": 100, "minimum_corporations": 40, "minimum_entry_dates": 50,
            "minimum_effect": MINIMUM_EFFECT, "required_data_complete": required_data_complete,
            "multiplicity": "single primary; no Holm adjustment"}


def judge(result, sensitivity, *, required_data_complete=True):
    base, loss = (judge_one(r, required_data_complete=required_data_complete) for r in (result, sensitivity))
    if "insufficient" in (base["verdict"], loss["verdict"]):
        verdict = "insufficient"
    elif base["verdict"] != loss["verdict"]:
        verdict = "delisting_sensitive"
    else:
        verdict = base["verdict"]
    return {"verdict": verdict, "base": base, "vanished_minus_100pct": loss}


def value_position(reader, event, code, feature):
    first, target = event["entry_position"], event["exit_position"]
    rows = [reader.require(day).get(code) for day in reader.sessions[first:target + 1]]
    if not bb001._traded(rows[0]) or not bb001._positive(rows[0].get("open")):
        raise NotReady("missing_fixed_position_entry_quote:" + code)
    actual = max(i for i, row in enumerate(rows) if bb001._traded(row))
    opening, mark = rows[0]["open"], rows[actual]["close"]
    factors, cumulative = [], rows[0]["close"] / opening
    for i, row in enumerate(rows):
        if i and i <= actual and bb001._quoted(row):
            if not np.isfinite(row["ret_1d"]) or row["ret_1d"] < -1:
                raise NotReady("missing_adjusted_return_on_fixed_position:" + code)
            cumulative *= 1 + row["ret_1d"]
        factors.append(float(cumulative - 1))
    mark_day = reader.sessions[first + actual]
    stale = actual < target - first
    vanished = bool(stale and reader.last_trades().get(code, mark_day) <= mark_day)
    costs, sides = {}, {}
    for version, multiplier in [("v2", m) for m in (1., 1.5, 2.)] + [("v1", 1.)]:
        buy = reader.policy.cost(opening, feature["market"], reader.sessions[first], feature["adv"], "buy", multiplier=multiplier, version=version)
        sell = reader.policy.cost(mark, feature["market"], mark_day, feature["adv"], "sell", multiplier=multiplier, version=version)
        key = str(multiplier) if version == "v2" else "v1"
        costs[key], sides[key] = buy + sell, {"buy": buy, "sell": sell}
    return {"stock_code": code, "gross": factors[-1], "gross_path": factors, "costs": costs, "side_costs": sides,
        "valuation_date": mark_day.date().isoformat(), "scheduled_exit_date": event["exit_date"],
        "stale_exit": stale, "vanished": vanished, "gap": float(opening / feature["pre_close"] - 1)}


@verified("pc")
def interim(store):
    """Only counts, completion latency and omissions. Never opens daily/results."""
    decisions, latency, slack = [], [], []
    invalid = 0
    for key in store.keys("decisions"):
        proof = store.proof("decisions", key)
        if not proof["valid"]:
            invalid += 1
            continue
        record = proof["record"]
        decisions.append(record)
        first = store.get("seen", key)
        latency.append((aware(record["recorded_at"]) - aware(first["recorded_at"])).total_seconds())
        slack.append((aware(record["event"]["deadline"]) - aware(proof["publication_not_after"])).total_seconds())
    posts = [store.get("post_entry", k) for k in store.keys("post_entry")]
    misses = [store.get("data_not_ready", k) for k in store.keys("data_not_ready")]
    coverage = [store.get("coverage", k) for k in store.keys("coverage")]
    readiness = [store.get("readiness", k) for k in store.keys("readiness")]
    from collections import Counter
    stats = lambda xs: {"count": len(xs), "median_seconds": float(np.median(xs)) if xs else None,
                        "maximum_seconds": max(xs) if xs else None}
    return {"experiment_id": EXPERIMENT_ID, "activation": activation_status(store),
        "title_receipts_seen_including_bootstrap": len(store.keys("seen")),
        "decisions": dict(Counter(r["status"] for r in decisions)),
        "decision_exclusion_reasons": dict(Counter(r["reason"] for r in decisions if r["reason"])),
        "entry_records": dict(Counter(r["status"] for r in posts)),
        "data_not_ready": {"count": len(misses), "reasons": dict(Counter(r["reason"] for r in misses))},
        "run_data_not_ready": {"count": len(readiness), "reasons": dict(Counter(r["reason"] for r in readiness))},
        "omissions": len(store.keys("omissions")), "rejected_completion_markers": invalid,
        "listing_day_coverage": dict(Counter(r["status"] for r in coverage)),
        "discovery_to_decision": stats(latency), "deadline_slack": stats(slack),
        "returns_included": False}


def prevalidate(store, policy):
    """Verify all sealed bytes and references without constructing a price reader."""
    implementation = implementation_check(store)
    activation = store.get("activation", "activation")
    allowed = {implementation["manifest_sha256"]}
    if activation:
        allowed.add(activation["manifest_sha256"])
    for key in store.keys("bugfixes"):
        record = store.get("bugfixes", key)
        allowed.update((record["before_sha256"], record["after_sha256"]))
    sealed = store.path("sealed")
    for directory in sorted(sealed.iterdir()) if sealed.exists() else []:
        for key in store.keys(directory.name):
            proof = store.proof(directory.name, key)
            record = proof["record"]
            if record["manifest_sha256"] not in allowed:
                raise ValueError("BB003 record used an unapproved manifest: " + directory.name + "/" + key)
            for checksum in record.get("input_hashes", {}).values():
                store.bytes(checksum)
    if store.keys("policy"):
        from .policy import latest
        if latest(store).digest != policy.digest:
            raise ValueError("BB003 evaluator policy differs from the sealed amendment chain")
    for key in store.keys("post_entry"):
        record = store.get("post_entry", key)
        decision = store.proof("decisions", key)
        if record and (not decision or record["decision_sha256"] != decision["record_sha256"]):
            raise ValueError("BB003 entry record refers to another decision")
        if record and record["status"] == "executed":
            selected = record["selection"]
            planned = policy.schedule(selected["public_date"])
            if any(selected[k] != planned[k] for k in selected.keys() & planned.keys()):
                raise ValueError("BB003 sealed entry differs from the final official calendar")
    return implementation


def abort_evaluation(store, started, error, *, phase="evaluation"):
    """Emergency immutable terminal receipt, including when verification fails.

    This records the manifest accepted when the attempt was consumed. It does
    not attest that the failing/current implementation passed verification.
    No return reader or recomputation is involved.
    """
    path = store.path("evaluation_abort.json")
    if path.exists():
        return json.loads(path.read_bytes())
    record = {"record_kind": "evaluation_abort", "record_key": "final", "status": "forward_aborted", "terminal": True,
        "uses_holdout": False,
        "phase": phase, "reason": "evaluation_failed_after_consumption", "error_type": type(error).__name__,
        "recorded_at": aware(store.clock()).isoformat(), "label": "forward",
        "manifest_sha256": started["record"]["manifest_sha256"],
        "evaluation_started_sha256": started["record_sha256"], "recomputation_allowed": False}
    put(path, encoded(record))
    return record


def save_computed(store):
    checkpoint = store.get("evaluation_computed", "final")
    if not checkpoint:
        raise ValueError("BB003 has no computed and sealed result to recover")
    started = store.proof("evaluation_started", "final")
    if not started or checkpoint["evaluation_started_sha256"] != started["record_sha256"]:
        raise ValueError("BB003 computed result belongs to another evaluation attempt")
    if implementation_check(store)["manifest_sha256"] != checkpoint["manifest_sha256"]:
        raise ValueError("BB003 recovery manifest differs from the computed result")
    payload = checkpoint["result"]
    proof = store.seal("evaluation", "final", payload, fixed_path="evaluation.json", recorded_at=checkpoint["recorded_at"])
    if any(proof["record"].get(k) != v for k, v in payload.items()):
        raise ValueError("BB003 saved evaluation differs from the sealed computation")
    store.seal("trials", "d21", {"experiment_id": EXPERIMENT_ID, "variant": "forward_d21",
        "verdict": payload["verdict"], "evaluation_sha256": proof["record_sha256"], "freeze": payload["freeze"]})
    return payload


@verified("evaluation")
def recover_evaluation(store):
    started = store.proof("evaluation_started", "final")
    if not started:
        raise ValueError("BB003 evaluation was never consumed")
    try:
        return save_computed(store)
    except BaseException as exc:
        abort_evaluation(store, started, exc, phase="recovery")
        raise


@verified("evaluation")
def evaluate(store, policy):
    """Exactly one attempted evaluation, including a terminal insufficient result.

    All data must already have BB003 acquisition proofs no later than F. Missing
    evaluation data do not remove entered events or permit another final trial.
    """
    if store.measurement:
        raise ValueError("BB003 reconstructed rehearsal records cannot be evaluated as forward")
    frozen = policy.freeze()
    if aware(store.clock()) <= aware(frozen["F"]):
        raise ValueError("BB003 evaluation refuses before/at F")
    previous = store.proof("evaluation_started", "final")
    if previous:
        if not store.proof("evaluation", "final"):
            abort_evaluation(store, previous, RuntimeError("interrupted evaluation"))
        raise ValueError("BB003 evaluation already attempted; cannot rerun")
    implementation = prevalidate(store, policy)
    public_freeze = {k: v for k, v in frozen.items() if k != "grid"}
    started = store.seal("evaluation_started", "final", {"experiment_id": EXPERIMENT_ID,
        "freeze": public_freeze, "policy_sha256": policy.digest})
    try:
        payload = compute(store, policy, frozen, implementation)
        store.seal("evaluation_computed", "final", {"result": payload,
            "evaluation_started_sha256": started["record_sha256"]})
        return save_computed(store)
    except BaseException as exc:
        abort_evaluation(store, started, exc)
        raise


def compute(store, policy, frozen, implementation):
    public_freeze = {k: v for k, v in frozen.items() if k != "grid"}
    payload = {"experiment_id": EXPERIMENT_ID, "freeze": public_freeze, "counts": interim(store),
               "HAC": {"lag": 20, "grid_start": str(frozen["grid"][0].date()),
                       "grid_end": str(frozen["grid"][-1].date()), "grid_sessions": len(frozen["grid"]),
                       "keeps_zero_event_days": True}, "selected_variant": "d21"}
    if activation_status(store) != "active":
        payload.update(verdict="not_started", reasons=["activation not sealed by 2026-11-30 23:59:59 KST"])
    else:
        selections, missing = [], []
        payload["implementation"] = implementation
        for key in store.keys("decisions"):
            decision = store.get("decisions", key)
            if not decision or decision["status"] != "included":
                continue
            proof = store.proof("post_entry", key)
            if not proof or not proof["valid"] or aware(proof["publication_not_after"]) > aware(frozen["F"]):
                missing.append("entry_record_not_available_by_F:" + key)
                continue
            post = proof["record"]
            if post["decision_sha256"] != store.proof("decisions", key)["record_sha256"]:
                raise ValueError("BB003 entry record refers to another decision")
            for checksum in decision["input_hashes"].values():
                store.bytes(checksum)
            if post["status"] == "executed":
                selected = post["selection"]
                planned = policy.schedule(selected["public_date"])
                if any(selected[k] != planned[k] for k in selected.keys() & planned.keys()):
                    missing.append("sealed_and_final_official_calendar_differ:" + key)
                selections.append({**selected, **planned})
        selections.sort(key=lambda r: (r["entry_date"], r["rcept_no"]))
        rows = []
        reader = Prices(store.root, policy, store, as_of=frozen["V"], frozen=True,
                        freeze_at=frozen["F"], vanish_end=frozen["V"])
        try:
            # Complete market archives through V, even if no stock has vanished.
            reader.last_trades()
            for event in selections:
                stock = value_position(reader, event, event["stock_code"], event["event_features"])
                controls = [value_position(reader, event, code, feature) for code, feature in event["control_features"].items()]
                rows.append({**event, "stock": stock, "controls": controls})
        except NotReady as exc:
            missing.append(str(exc))
        finally:
            reader._load.cache_clear()
            reader._frozen_load.cache_clear()
        if missing:
            payload.update(verdict="insufficient", reasons=missing,
                           executed_events=len(selections), metrics=None, per_event=None,
                           missing_entered_events_are_never_deleted=True)
        else:
            base, loss = (metrics(rows, frozen["grid"], worthless=flag) for flag in (False, True))
            payload.update(judgement=judge(base, loss), metrics=base, vanish_sensitivity=loss,
                per_event=rows, executed_events=len(selections),
                calendar_portfolio=bb001.calendar_portfolio(rows, frozen["grid"]),
                cancel_tags={str(tag): bb001._summary([r for r in rows if r["cancel"] == tag], frozen["grid"], 21)
                             for tag in (True, False)}, price_hashes=reader.source_hashes)
            payload["verdict"] = payload["judgement"]["verdict"]
        if "implementation" in payload and implementation_check(store) != payload["implementation"]:
            raise ValueError("BB003 PC implementation changed during evaluation")
    return payload
