"""Fixed-window NW(5), normal upper tail, with no path replay in sensitivity."""
import json
import math
from pathlib import Path

import numpy as np

from backtesting import experiment_registry
from backtesting.experiments import hc003, ordering
from backtesting.experiments.lh001.evaluate import newey_west

from . import END, EXPERIMENT_ID, FREEZE_AT, INITIAL_CASH, MULTIPLIERS, START, Refused
from .data import canonical_repo
from .storage import attempts as reserved_attempts, aware, digest, lock, now, seal, verified
from .workflow import book_name


def statistics(values, lag=5):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise Refused("missing/nonfinite daily returns cannot be deleted or zero-filled")
    result = newey_west(values, lag=lag)
    result["p_one_sided"] = .5 * math.erfc(result["t_stat"] / math.sqrt(2)) if result["t_stat"] is not None else None
    result["small_sample_correction"] = False
    return result


def _risk(navs):
    values = np.array([INITIAL_CASH, *navs], dtype=float)
    peaks = np.maximum.accumulate(values)
    drawdowns = values / peaks - 1
    longest, current = 0, 0
    for value, peak in zip(values, peaks):
        current = current + 1 if value < peak else 0
        longest = max(longest, current)
    return {"net_return": float(values[-1] / INITIAL_CASH - 1), "max_drawdown": float(drawdowns.min()),
            "longest_underwater_sessions": longest, "unrecovered_at_end": bool(values[-1] < peaks[-1])}


def compact(record):
    """Keep bounded scalar summaries; never retain a year of full position trees."""
    return {"day": record["day"], "valid_decision": record["valid_decision"], "required_files": record["required_files"],
        "missing_reason": record["missing_reason"], "indices": record.get("indices"), "books": {name: {
            "nav": book["state"]["nav"], "cash": book["state"]["cash"], "holding_count": len(book["state"]["positions"]),
            **{key: book[key] for key in ("unresolved_value", "cost", "turnover", "turnover_scale")}}
            for name, book in record["books"].items()}}


def _nav(book):
    return book["nav"] if "nav" in book else book["state"]["nav"]


def _measure(records, *, sensitivity=False):
    scenario, controls, absolute = {}, {}, {}
    per_book = {}
    for m in MULTIPLIERS:
        names = [book_name(kind, m) for kind in ("ld", "ew", "decomposition", "av")]
        names += [book_name("random", m, k) for k in range(200)]
        returns = {}
        for name in names:
            sequence, navs, previous_nav = [], [], INITIAL_CASH
            for record in records:
                book = record["books"][name]
                nav = _nav(book)
                if sensitivity and record["day"] == END:
                    nav -= book["unresolved_value"]
                if nav < -1e-5 or previous_nav < 0 or previous_nav == 0 and nav != 0:
                    raise Refused("invalid NAV series")
                sequence.append(nav / previous_nav - 1 if previous_nav else 0.0)
                navs.append(nav)
                previous_nav = nav
            returns[name] = np.array(sequence)
            if not name.startswith("random"):
                per_book[name] = {**_risk(navs), "cost": sum(r["books"][name]["cost"] for r in records),
                    "turnover": sum(r["books"][name]["turnover"] for r in records),
                    "turnover_bound_days": sum(r["books"][name]["turnover_scale"] < 1 for r in records),
                    "final_cash_share": (records[-1]["books"][name]["cash"] if "nav" in records[-1]["books"][name]
                        else records[-1]["books"][name]["state"]["cash"]) / navs[-1] if navs[-1] > 0 else None,
                    "final_holdings": records[-1]["books"][name]["holding_count"] if "nav" in records[-1]["books"][name]
                        else len(records[-1]["books"][name]["state"]["positions"]), "nav_series": navs}
        differences = returns[book_name("ld", m)] - returns[book_name("ew", m)]
        scenario[str(m)] = {"primary": statistics(differences),
                            "lag10": statistics(differences, 10), "lag20": statistics(differences, 20)}
        mean_controls = [float(np.mean(returns[book_name("random", m, k)] - returns[book_name("ew", m)])) for k in range(200)]
        controls[str(m)] = {"mean_excess": mean_controls,
            "share_at_least_actual": float(np.mean(np.asarray(mean_controls) >= scenario[str(m)]["primary"]["mean"]))}
        absolute[str(m)] = per_book[book_name("ld", m)]["net_return"]
    return {"primary": scenario["1.0"]["primary"], "scenarios": scenario, "random_control": controls,
            "ld_net_return": absolute, "books": per_book,
            "sensitivity": "unresolved residual values written to zero at 2027-11-15 close only" if sensitivity else "sealed NAVs"}


def judgement(result, valid_days, *, complete):
    primary = result["primary"]
    required = (primary["mean"], primary["t_stat"], result["random_control"]["1.0"]["share_at_least_actual"],
                result["scenarios"]["1.5"]["primary"]["mean"], result["ld_net_return"]["1.0"])
    if not complete or valid_days < 150 or any(v is None or not math.isfinite(v) for v in required):
        return "insufficient"
    return "pass" if (primary["mean"] > 0 and primary["t_stat"] > 3
        and result["random_control"]["1.0"]["share_at_least_actual"] <= .05
        and result["scenarios"]["1.5"]["primary"]["mean"] > 0 and result["ld_net_return"]["1.0"] > 0) else "fail"


def evaluate(records, expected_days, *, status, required_file_coverage=None):
    statuses = {"completed", "terminated_drawdown", "model_changed", "not_started", "forward_aborted"}
    if status not in statuses:
        raise Refused("unknown operating status")
    days = [r["day"] for r in records]
    if days != sorted(set(days)) or any(not START <= d <= END for d in days):
        raise Refused("ledger dates must be unique, sorted and within the fixed window")
    coverage = {} if required_file_coverage is None else required_file_coverage
    required_complete = days == list(expected_days) and all(all(r["required_files"].get(m) is True
        for m in ("KOSPI", "KOSDAQ")) for r in records)
    valid_days = sum(r["valid_decision"] for r in records)
    value = {"status": status, "verdict": None, "valid_decisions": valid_days, "days": len(records),
             "expected_days": len(expected_days), "required_data_complete": required_complete,
             "missing_days": sorted(set(expected_days) - set(days)),
             "recovered_file_days": sorted(day for day in days if day in coverage),
             "assumptions": ["virtual close fills; no market impact or queue", "same-close realized sale proceeds reused",
                "conditional ID replacement controls, not independent delegation", "price indices exclude dividends"]}
    if records and days == list(expected_days):
        value["base"], value["loss_sensitivity"] = _measure(records), _measure(records, sensitivity=True)
        value["investment_level_contribution"] = {
            "ld_minus_fixed100_net_return": value["base"]["books"][book_name("ld", 1.0)]["net_return"]
                - value["base"]["books"][book_name("decomposition", 1.0)]["net_return"],
            "interpretation": "allocation-level contribution including constraints and costs, not a pure timing effect"}
    elif records:
        value["descriptive_only"] = "Incomplete fixed window: paired statistics are not computed on a shortened series"
    if status == "completed":
        base = judgement(value["base"], valid_days, complete=required_complete) if "base" in value else "insufficient"
        loss = judgement(value["loss_sensitivity"], valid_days, complete=required_complete) if "base" in value else "insufficient"
        value["base_verdict"], value["sensitivity_verdict"] = base, loss
        value["verdict"] = "delisting_sensitive" if base == "pass" and loss != "pass" else base
    value["invalid_reasons"] = {reason: sum(r["missing_reason"] == reason for r in records)
        for reason in sorted({r["missing_reason"] for r in records if r["missing_reason"] is not None})}
    value["price_indices"] = {}
    for series in ("KOSPI", "KOSDAQ"):
        levels = [(r.get("indices") or {}).get(series) for r in records]
        complete = required_complete and levels and all(v is not None and v > 0 for v in levels)
        value["price_indices"][series] = {"dividends_included": False,
            "entry": "T0 close", "net_return": levels[-1] / levels[0] - 1 if complete else None,
            "missing_days": [r["day"] for r, level in zip(records, levels) if level is None]}
    return value


def publish_evaluation(workflow, *, clock=now):
    if aware(clock()) < aware(FREEZE_AT):
        raise Refused("final evaluation is fixed at 2027-11-23 or later")
    root, canonical = workflow.root, canonical_repo(workflow.repo_root)
    with ordering.execution_lock(canonical), lock(root / ".state.lock"):
        workflow.sync_model_stop()
        rows = [r for r in hc003.registry_rows(canonical) if r["experiment_id"] == EXPERIMENT_ID]
        path = root / "evaluation.json"
        if rows:
            if len(rows) != 1 or not path.exists() or json.loads(rows[0]["metrics_json"])["result_sha256"] != digest(verified(path)):
                raise Refused("single LD002 registry row already exists; cannot recompute after rollback")
            return verified(path)
        if path.exists():
            result = verified(path)
        else:
            terminal = workflow.termination()
            late_terminal = terminal if terminal and aware(terminal["recognized_at"]) > aware(FREEZE_AT) else None
            if late_terminal:
                terminal = None
            not_started = any((root / name).exists() for name in ("not_started.json", "holdout_failed.json"))
            status = terminal["status"] if terminal else "not_started" if not_started or not (root / "start.json").exists() else "completed"
            if status != "not_started":
                workflow.start()
            expected = [] if status == "not_started" else workflow.calendar.sessions(START, END)
            records, ledger_hashes, late_hashes, conviction_counts = [], {}, {}, {"1": 0, "2": 0, "3": 0}
            previous_hash = digest(None)
            for ledger_path in sorted((root / "ledgers").glob("*.json")):
                # Legacy incomplete primaries remain descriptive evidence and
                # force insufficient even if a later audit has both markets.
                record = workflow.ledger(ledger_path.stem, allow_incomplete=True)
                if record is None:
                    continue
                checksum = digest(record)
                if aware(record["finalized_at"]) > aware(FREEZE_AT):
                    late_hashes[record["day"]] = checksum
                    continue
                if record["previous_sha256"] != previous_hash:
                    raise Refused("sealed daily ledger hash chain differs")
                previous_hash = checksum
                ledger_hashes[record["day"]] = checksum
                decision = workflow.decision(record["day"])
                if record["decision_sha256"] != digest(decision) or record["input_sha256"] != digest(workflow.input(record["day"])):
                    raise Refused("ledger differs from the sealed input/decision publications")
                if record["valid_decision"]:
                    for holding in decision["output"]["holdings"]:
                        conviction_counts[str(holding["conviction"])] += 1
                records.append(compact(record))
            audit_records, coverage = {}, {}
            for audit_path in sorted((root / "audits").glob("*/*.json")):
                audit = verified(audit_path)
                audit_records[str(audit_path.relative_to(root))] = {
                    "sha256": digest(audit), "nav_differences": audit["nav_differences"]}
                if (aware(audit["finalized_at"]) <= aware(FREEZE_AT)
                        and audit["original_sha256"] == ledger_hashes.get(audit["day"])):
                    for market in ("KOSPI", "KOSDAQ"):
                        if audit["required_files"].get(market) is True:
                            coverage.setdefault(audit["day"], {})[market] = True
            result = evaluate(records, expected, status=status, required_file_coverage=coverage)
            result.update(experiment_id=EXPERIMENT_ID, frozen_at=FREEZE_AT,
                ledger_hashes=ledger_hashes, termination=terminal, late_termination=late_terminal)
            result["late_ledger_hashes"] = late_hashes
            result["conviction_counts"] = conviction_counts
            result["audit_corrections"] = audit_records
            history = []
            for reservation in reserved_attempts(root):
                attempt_path = root / "attempts" / f"{reservation['number']:06d}.result.json"
                attempt = verified(attempt_path) if attempt_path.exists() else {
                    "state": "interrupted", "seconds": None, "error": "reserved without result at freeze"}
                history.append({**reservation, **attempt})
            result["calls"] = {"attempts": len(history), "reasks": sum(a["try"] > 0 for a in history),
                "format_attempts": sum(a["format_trial"] for a in history), "seconds": [a["seconds"] for a in history],
                "usage_limit_attempts": sum("usage_limit" in a.get("error", "") for a in history),
                "usage_limit_days": len({a["day"] for a in history if not a["format_trial"] and "usage_limit" in a.get("error", "")}),
                "interrupted_attempts": sum(a["state"] == "interrupted" for a in history)}
            seal(path, result)
        metrics = {"stage": "forward", "status": result["status"], "final_verdict": result["verdict"],
                   "valid_decisions": result["valid_decisions"], "result_file": str(path.resolve()), "result_sha256": digest(result)}
        if "base" in result:
            metrics.update(primary=result["base"]["primary"], ld_net_return=result["base"]["ld_net_return"]["1.0"],
                mean_at_cost_1_5=result["base"]["scenarios"]["1.5"]["primary"]["mean"],
                random_share=result["base"]["random_control"]["1.0"]["share_at_least_actual"])
        experiment_registry.record_trials(EXPERIMENT_ID, [("policy", metrics, result["verdict"] or result["status"])],
            repo_root=canonical, note="LD002 one fixed forward virtual path; status and verdict are separate")
        return result
