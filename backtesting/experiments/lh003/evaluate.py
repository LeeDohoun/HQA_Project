"""Offline fixed-denominator policy evaluation; no LLM or source acquisition."""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import ordering
from backtesting.experiments.lh001.evaluate import newey_west

from . import EVALUATION_AT, EXPERIMENT_ID, Refused
from .calendar import WEEKS
from .executions import MULTIPLIERS, portfolio, priced_quotes
from .storage import attempts, aware, digest, file_hash, now, seal, verified
from .workflow import load_week


def statistics(values, positions=None):
    stats = newey_west(values, lag=4, positions=positions)
    stats["p_one_sided"] = 0.5 * math.erfc(stats["t_stat"] / math.sqrt(2)) if stats["t_stat"] is not None else None
    return stats


def random_paths(decisions):
    """Draw from sealed candidates before looking at quotes; never refill."""
    rng, paths = np.random.default_rng(0), {}
    for decision in sorted(decisions, key=lambda row: row["index"]):
        if decision["valid"]:
            candidates = sorted(decision["candidate_codes"])
            if len(candidates) < 40:
                raise Refused("valid decision has fewer than 40 sealed candidates")
            paths[decision["index"]] = [rng.choice(candidates, 10, replace=False).tolist() for _ in range(200)]
        else:
            paths[decision["index"]] = [list(decision["a_codes"]) for _ in range(200)]
    return paths


def verdict(result, *, trials):
    primary = result["primary"]
    required = (primary["mean"], primary["t_stat"], result["random_control"]["share_of_controls"],
                result["cost_sensitivity"]["1.5"]["mean"])
    if result["valid_decisions"] < 30 or result["bought_observations"] < 300 or any(
            value is None or not np.isfinite(value) for value in required):
        return "insufficient", ["requires 30 valid decisions, 300 bought observations and defined statistics"]
    threshold = 3 if trials > 20 else 2
    failures = []
    if primary["mean"] <= 0:
        failures.append("mean policy minus A must be positive")
    if primary["t_stat"] <= threshold:
        failures.append(f"calendar-week NW(4) t must exceed {threshold}")
    if result["random_control"]["share_of_controls"] > 0.05:
        failures.append("share of 200 controls >= policy must be at most 5%")
    if result["cost_sensitivity"]["1.5"]["mean"] <= 0:
        failures.append("policy minus A must be positive at cost x1.5")
    return ("fail", failures) if failures else ("pass", ["all LH003 preregistered criteria satisfied"])


def _measure(decisions, scenarios, paths, *, loss, trials, retain_executions):
    differences = {str(m): [] for m in MULTIPLIERS}
    rows, control_rows, sealed = [], [], {}
    events = 0
    for decision in decisions:
        index = decision["index"]
        row = {"index": index, "decision_date": decision["decision_date"], "valid": decision["valid"]}
        for multiplier in MULTIPLIERS:
            quotes = scenarios[index][str(multiplier)]
            a = portfolio(quotes, decision["a_codes"], loss=loss)
            policy = portfolio(quotes, decision["policy_codes"], loss=loss) if decision["valid"] else a
            difference = policy["net"] - a["net"] if decision["valid"] else 0.0
            differences[str(multiplier)].append(difference)
            controls = [portfolio(quotes, codes, loss=loss) for codes in paths[index]] if retain_executions or multiplier == 1 else None
            if retain_executions:
                sealed.setdefault(str(multiplier), []).append({"index": index, "decision_sha256": digest(decision),
                    "policy": policy, "a": a, "controls": controls, "delisting_loss": loss})
            if multiplier != 1:
                continue
            gross = portfolio(quotes, decision["universe_codes"], loss=loss)["gross"]
            control_rows.append([control["net"] - a["net"] for control in controls])
            events += policy["count"] if decision["valid"] else 0
            avoid = portfolio(quotes, decision["avoid"], loss=loss) if decision["valid"] else None
            row.update(d=difference, policy_net=policy["net"], a_net=a["net"], universe_gross=gross,
                       policy_vs_universe=policy["net"] - gross, a_vs_universe=a["net"] - gross,
                       cash_on_invalid_d=difference if decision["valid"] else -a["net"],
                       avoid_d=avoid["net"] - a["net"] if avoid else None,
                       bought_observations=policy["count"] if decision["valid"] else 0)
            row["liquidity"] = {}
            for bucket in range(4):
                def members(codes):
                    return [c for c in codes if quotes[c]["filled"] and int(np.searchsorted(
                        (1e8, 1e9, 1e10, math.inf), quotes[c]["entry_adv_20"], side="right")) == bucket]
                selected, reference = members(decision["policy_codes"]), members(decision["a_codes"])
                row["liquidity"][str(bucket)] = {
                    "policy_count": len(selected), "a_count": len(reference),
                    "d": portfolio(quotes, selected, loss=loss)["net"] - portfolio(quotes, reference, loss=loss)["net"]
                    if selected and reference else None}
        rows.append(row)
    positions = [WEEKS[d["index"]].ordinal for d in decisions]
    primary = statistics(differences["1.0"], positions)
    means = np.mean(control_rows, axis=0)
    result = {"primary": primary, "valid_decisions": sum(d["valid"] for d in decisions), "bought_observations": events,
              "cost_sensitivity": {key: statistics(values, positions) for key, values in differences.items()},
              "random_control": {"seed": 0, "paths": 200, "mean_excess": means.tolist(),
                  "share_of_controls": float(np.mean(means >= primary["mean"])),
                  "draw_order": "valid calendar week, path, sorted sealed candidate; invalid week uses A"},
              "per_decision": rows, "valid_only": statistics([r["d"] for r in rows if r["valid"]],
                  [p for p, r in zip(positions, rows) if r["valid"]]),
              "cash_on_invalid": statistics([r["cash_on_invalid_d"] for r in rows], positions),
              "absolute_returns": {key: float(np.mean([r[key] for r in rows])) for key in
                  ("policy_net", "a_net", "universe_gross", "policy_vs_universe", "a_vs_universe")},
              "avoid": statistics([r["avoid_d"] for r in rows if r["avoid_d"] is not None],
                                  [p for p, r in zip(positions, rows) if r["avoid_d"] is not None]),
              "monthly": {str(month): statistics([r["d"] for r in rows if pd.Timestamp(r["decision_date"]).to_period("M") == month],
                      [p for p, r in zip(positions, rows) if pd.Timestamp(r["decision_date"]).to_period("M") == month])
                  for month in sorted({pd.Timestamp(r["decision_date"]).to_period("M") for r in rows})}}
    result["verdict"], result["reasons"] = verdict(result, trials=trials)
    return result, sealed


def evaluate(decisions, raw, *, trials=1, cost_fn=None, retain_executions=False):
    reasons = []
    if len(decisions) != 52 or any(d is None for d in decisions):
        reasons.append("all 52 fixed decisions and reproducible A records are required")
    else:
        if sorted(d["index"] for d in decisions) != list(range(52)):
            reasons.append("duplicate or missing calendar-week decisions")
        if any(not d["observation"] or d.get("reconstructed") for d in decisions):
            reasons.append("reconstructed/late decisions are never observations; A-unreproducible week")
    if reasons:
        return {"verdict": "insufficient", "reasons": reasons, "primary": statistics([])}, {}
    decisions = sorted(decisions, key=lambda d: d["index"])
    # Draw before any quote filtering or execution inspection.
    paths = random_paths(decisions)
    if set(raw) != set(range(52)):
        return {"verdict": "insufficient", "reasons": ["mandatory execution/evaluation data missing for a fixed week"],
                "primary": statistics([])}, {}
    scenarios = {}
    for decision in decisions:
        record = raw[decision["index"]]
        if record.get("decision_sha256") != digest(decision) or record.get("audit_only", False):
            raise Refused("raw executions are not sealed for this decision, or are audit only")
        needed = set(decision["universe_codes"]) | set(decision["a_codes"]) | set(decision["candidate_codes"])
        if not needed.issubset(record["quotes"]):
            return {"verdict": "insufficient", "reasons": ["mandatory quote universe incomplete"], "primary": statistics([])}, {}
        scenarios[decision["index"]] = {str(m): priced_quotes(record, m, cost_fn=cost_fn) for m in MULTIPLIERS}
    main, executions = _measure(decisions, scenarios, paths, loss=False, trials=trials, retain_executions=retain_executions)
    sensitivity, loss_executions = _measure(decisions, scenarios, paths, loss=True, trials=trials, retain_executions=retain_executions)
    main["delisting_sensitivity"] = sensitivity
    if main["verdict"] != "insufficient" and main["verdict"] != sensitivity["verdict"]:
        main["base_verdict"] = main["verdict"]
        main.update(verdict="delisting_sensitive", reasons=["verdict changes under -100% disappeared-stock sensitivity; not a pass"])
    main.update(trial_count=trials, t_threshold=3 if trials > 20 else 2,
                invalid_reasons=dict(Counter(reason for d in decisions if not d["valid"] for reason in d["reasons"])),
                reasks=sum(max(0, len(r["attempts"]) - 1) for d in decisions for r in d.get("repeats", [])),
                mean_jaccard=float(np.mean([d["jaccard"] for d in decisions if d.get("jaccard") is not None]))
                if any(d.get("jaccard") is not None for d in decisions) else None,
                screening_effect_fraction_applicable=False)
    return main, {"main": executions, "delisting_loss": loss_executions, "random_draws": paths}


def compact(result, *, path, trials):
    return {"stage": "forward_evaluation", "verdict": result["verdict"], "reasons": result["reasons"],
            "primary": result["primary"], "valid_decisions": result.get("valid_decisions"),
            "bought_observations": result.get("bought_observations"), "trial_count_after_run": trials,
            "random_share": result.get("random_control", {}).get("share_of_controls"),
            "cost_1_5_mean": result.get("cost_sensitivity", {}).get("1.5", {}).get("mean"),
            "cost_2_mean": result.get("cost_sensitivity", {}).get("2.0", {}).get("mean"),
            "delisting_verdict": result.get("delisting_sensitivity", {}).get("verdict"), "result_file": str(path)}


def call_audit(root):
    reservations = attempts(root)
    states, reasons = Counter(), Counter()
    for attempt in reservations:
        path = Path(root) / "attempts" / f"{attempt['number']:06d}.result.json"
        result = verified(path) if path.exists() else {"state": "interrupted", "error": "unfinished reservation"}
        states[result["state"]] += 1
        if result["state"] == "valid":
            continue
        error = result.get("error", "").lower()
        violations = result.get("event_summary", {}).get("violations", [])
        if result["state"] == "interrupted":
            reason = "interrupted"
        elif "timeout" in error:
            reason = "timeout"
        elif any(v.startswith("forbidden_or_unknown_item") for v in violations):
            reason = "tool_or_unknown_item"
        elif "not unique" in error:
            reason = "duplicate_ids"
        elif "not one of" in error:
            reason = "unknown_id"
        elif "overlap" in error:
            reason = "ranked_avoid_overlap"
        elif "validationerror" in error:
            reason = "schema"
        elif "drift" in error or "identity" in error or "manifest" in error:
            reason = "model_version_or_prompt_identity"
        else:
            reason = "output_or_execution_protocol"
        reasons[reason] += 1
    return {"used": len(reservations), "states": dict(states), "invalid_by_reason": dict(reasons),
            "reasks": sum(a["try"] == 1 for a in reservations)}


def preflight_costs(raw):
    """Statutory availability is a prerequisite, before a statistical trial starts."""
    seen = set()
    for record in raw.values():
        for code, quote in record["quotes"].items():
            if not quote["filled"]:
                continue
            key = tuple(quote[k] for k in ("entry_date", "entry_market", "exit_date", "exit_market"))
            if key not in seen:
                priced_quotes({"quotes": {code: quote}}, 1.0)
                seen.add(key)


def evaluate_saved(*, root, repo_root=PROJECT_ROOT, clock=now):
    root = Path(root)
    with ordering.execution_lock(repo_root):
        experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
        if aware(clock()) < aware(EVALUATION_AT):
            raise Refused("one final verdict is allowed only after 2027-12-17 00:00 KST")
        start = verified(root / "start.json")
        if start["synthetic"] or (root / "not_started.json").exists():
            raise Refused("synthetic/not_started run cannot be evaluated as LH003")
        result_path = root / "evaluation.json"
        if result_path.exists():
            publish_evaluation(root=root, repo_root=repo_root)
            return verified(result_path)
        decisions = [load_week(root, i) for i in range(52)]
        if any(d and d.get("synthetic") for d in decisions):
            raise Refused("synthetic decision cannot enter the official evaluation")
        raw = {i: verified(path) for i in range(52) if (path := root / "executions/raw" / f"{i:02d}.json").exists()}
        if any(r.get("data_asof") != EVALUATION_AT for r in raw.values()):
            raise Refused("raw executions must reference the registered data-as-of cutoff")
        if all(d and d["observation"] for d in decisions) and len(raw) == 52:
            preflight_costs(raw)
        trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 1
        try:
            result, execution_scenarios = evaluate(decisions, raw, trials=trials, retain_executions=True)
            audit = call_audit(root)
            cost_hash = file_hash(cost_model.__file__)
            raw_hash = digest(raw)
            result.update(experiment_id=EXPERIMENT_ID, data_asof=EVALUATION_AT, at=aware(clock()).isoformat(),
                          trial_count=trials, call_audit=audit, reasks=audit["reasks"],
                          decisions_sha256=digest(decisions), raw_executions_sha256=raw_hash, calls_used=audit["used"],
                          cost_model_source_sha256=cost_hash, execution_record_hashes={})
            for sensitivity in ("main", "delisting_loss"):
                for scenario, rows in execution_scenarios.get(sensitivity, {}).items():
                    value = {"model_version": "v2", "multiplier": float(scenario), "sensitivity": sensitivity,
                             "cost_model_source_sha256": cost_hash, "raw_executions_sha256": raw_hash, "records": rows}
                    name = f"{sensitivity}/v2_{scenario}.json"
                    seal(root / "executions" / name, value)
                    result["execution_record_hashes"][name] = digest(value)
            if execution_scenarios:
                seal(root / "executions/random_draws.json", execution_scenarios["random_draws"])
            metrics = compact(result, path=result_path, trials=trials)
            experiment_registry.encode_metrics(metrics)
            seal(result_path, result)
            # A result-to-registry crash recovers without another statistical trial.
            publish_evaluation(root=root, repo_root=repo_root)
            return result
        except BaseException as exc:
            if not result_path.exists():
                details = ordering.abort_details({"decisions": digest(decisions), "raw_executions": digest(raw)}, exc)
                experiment_registry.record_trial(EXPERIMENT_ID, "policy", {
                    "stage": "forward_evaluation", "status": "aborted", "trial_count_after_run": trials, **details},
                    "evaluation_aborted", repo_root=repo_root, note="LH003 aborted statistical/publication attempt; no final verdict sealed")
            raise


def publish_evaluation(*, root, repo_root=PROJECT_ROOT):
    """Recover a result-to-registry crash without evaluating a second time."""
    from backtesting.experiments.hc003 import registry_rows
    path = Path(root) / "evaluation.json"
    result = verified(path)
    checksum = digest(result)
    rows = [r for r in registry_rows(repo_root) if r["experiment_id"] == EXPERIMENT_ID]
    import json
    if any(json.loads(r["metrics_json"]).get("result_sha256") == checksum for r in rows):
        return
    metrics = {**compact(result, path=path, trials=result["trial_count"] if "trial_count" in result else len(rows) + 1),
               "result_sha256": checksum}
    experiment_registry.record_trial(EXPERIMENT_ID, "policy", metrics, result["verdict"], repo_root=repo_root,
                                     note="LH003 fixed 52-week policy; NW calendar lag 4; no LH002 half-effect condition")
