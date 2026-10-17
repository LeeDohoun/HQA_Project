"""Offline PC evaluation of sealed FW001 observations (sections 4-5).

No design rerun, protected data, holdout claim, model call or server job. The
primary carries sealed fills; repricing is a separately labelled audit only.
"""
from __future__ import annotations

import argparse
import gzip
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.experiments import av001, hc003, hf001
from backtesting import experiment_registry
from backtesting.experiment_registry import PROJECT_ROOT
from src.forward import fw001 as recorder
from src.ingestion import dart_listing
from src.ingestion.storage import file_lock


def comparison_values():
    path = PROJECT_ROOT / recorder.RESEARCH_DIR / "comparison_values.json"
    return recorder.load_json(path), dart_listing.file_hash(path)


def original_hf_comparison(comparisons, *, repo_root=PROJECT_ROOT):
    path = Path(repo_root) / "research/experiments/HF001_hegemony_filters" / comparisons["f_both"]["secondary_original_hf001"]
    field = "variants.f_both.design.primary.mean"
    if not path.is_file():
        return {"status": "unavailable", "path": str(path), "field": field, "used_for_judgement": False}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    for key in field.split("."):
        value = value[key]
    return {"value": value, "path": path.relative_to(repo_root).as_posix(),
            "sha256": dart_listing.file_hash(path), "field": field, "used_for_judgement": False}


def judge_rule(name, rows, comparison, *, valid_decisions, continuous=True):
    """Exactly FW001 section 5; t/control share/cost x2 never enter this gate."""
    if name not in ("f_both", "av_all"):
        raise ValueError("FW001 judges exactly f_both and av_all")
    key = "difference" if name == "f_both" else "excess"
    means = {}
    for m in hf001.MULTIPLIERS:
        values = [row["costs"][str(m)][key] for row in rows]
        means[str(m)] = float(np.mean(values)) if values and all(
            v is not None and np.isfinite(v) for v in values) else None
    if comparison is None or not np.isfinite(comparison) or comparison <= 0:
        verdict = "not_applicable"
    elif valid_decisions < 10 or means["1.0"] is None or means["1.5"] is None or (
            name == "av_all" and (len(rows) != 12 or not continuous)):
        verdict = "insufficient"
    else:
        verdict = "pass" if (means["1.0"] > 0 and means["1.0"] >= comparison / 2
                              and means["1.5"] > 0) else "fail"
    values = [row["costs"]["1.0"][key] for row in rows]
    finite = [(row["month"], v) for row, v in zip(rows, values) if v is not None and np.isfinite(v)]
    statistic = hf001.newey_west([v for _, v in finite]) if name == "f_both" else av001.newey_west(
        [month for month, _ in finite], [v for _, v in finite])
    return {"verdict": verdict, "valid_decisions": valid_decisions, "monthly_observations": len(finite),
            "mean_by_cost_multiplier": means, "sealed_comparison": comparison,
            "half_comparison": comparison / 2 if comparison is not None else None,
            "continuous_accounting": continuous if name == "av_all" else None,
            "newey_west": {**statistic, "used_for_judgement": False},
            "cost_2_used_for_judgement": False, "controls_used_for_judgement": False}


def apply_sensitivity(main, sensitivity):
    result = {**main, "base_verdict": main["verdict"], "delisting_loss_verdict": sensitivity["verdict"]}
    result["delisting_sensitive"] = main["verdict"] != sensitivity["verdict"]
    if result["delisting_sensitive"]:
        result["verdict"] = "delisting_sensitive"
    return result


def read_records(data_dir, *, final=False):
    recorder.verify_manifest(implementation=True)
    root = Path(data_dir)
    freeze_path = root / recorder.RECORD_DIR / "evaluation_freeze.json"
    if final:
        if recorder.now() < recorder.FREEZE_AT or not freeze_path.is_file():
            raise ValueError("FW001 final evaluation requires the sealed 2027-11-15 00:00 KST data freeze")
        freeze = recorder.load_json(freeze_path)
        if freeze["freeze_at"] != recorder.FREEZE_AT.isoformat():
            raise ValueError("FW001 data freeze instant differs from preregistration")
        inputs = {**freeze["input_hashes"], freeze_path.relative_to(root).as_posix(): dart_listing.file_hash(freeze_path)}
        for name, checksum in inputs.items():
            if dart_listing.file_hash(root / name) != checksum:
                raise ValueError("FW001 frozen input changed: " + name)
    else:
        paths = [p for kind in ("decisions", "executions", "cost_resolutions", "missing", "prices", "sealed")
                 for p in (root / recorder.RECORD_DIR / kind).rglob("*.json")]
        inputs = recorder.hashes(root, sorted(paths))
    groups = {kind: {} for kind in ("decisions", "executions", "cost_resolutions", "missing", "prices")}
    for name in inputs:
        path = Path(name)
        if path.parent.name in groups:
            record = recorder.load_json(root / path)
            if record.get("label") == "forward" and path.parent.name in ("decisions", "executions"):
                kind = path.parent.name
                multiplier = record.get("cost_multiplier")
                proof_path = recorder.marker_path(root, kind, record["month"], multiplier)
                if final and proof_path.relative_to(root).as_posix() not in inputs:
                    continue  # A post-freeze marker cannot complete an old candidate.
                if recorder.completion_proof(root, kind, record["month"], multiplier) is None:
                    continue
            groups[path.parent.name][path.stem] = record
    if final:
        groups["prices"] = freeze["selected_prices"]
    else:
        for directory in sorted((root / recorder.RECORD_DIR / "price_versions").glob("*")):
            versions = sorted(directory.glob("*.json"))
            if versions:
                groups["prices"][directory.name] = recorder.load_json(versions[-1])
    for key, resolution in groups["cost_resolutions"].items():
        if key not in groups["executions"]:
            raise ValueError("FW001 cost resolution has no finalised execution")
        if resolution["resolution_of"] != recorder.digest(groups["executions"][key]):
            raise ValueError("FW001 cost resolution does not match the sealed execution")
        groups["executions"][key] = resolution
    for decision in groups["decisions"].values():
        if decision["label"] != "forward":
            continue
        planned = recorder.plan(decision["decision_date"])
        if any(decision[key] != planned[key] for key in ("month", "trade_date", "deadline", "f_both_exit")):
            raise ValueError("FW001 sealed decision dates differ from the official session plan")
        if decision["label"] == "forward" and datetime.fromisoformat(decision["finalised_at"]) >= datetime.fromisoformat(planned["deadline"]):
            raise ValueError("FW001 late decision cannot be a forward observation")
    return groups, inputs


def price_frame(data_dir, records, *, pc_dir=None):
    days = pd.DatetimeIndex(sorted(pd.Timestamp(row["day"]) for row in records["prices"].values()))
    if days.empty:
        index = pd.MultiIndex.from_arrays([[], []], names=["trade_date", "stock_code"])
        return pd.DataFrame(index=index), days
    return recorder.recorded_price_window(data_dir, days, pc_dir=pc_dir, descriptors=records["prices"]), days


def f_observations(records, prices, days, *, sealed=True):
    selections, fills = [], {}
    if days.empty:
        return pd.DataFrame()
    for decision in records["decisions"].values():
        if decision["label"] != "forward" or pd.Timestamp(decision["f_both_exit"]) > days[-1]:
            continue
        rule = decision["rules"]["f_both"]
        removed = set(rule["unfiltered_targets"]) - set(rule["targets"])
        executions = {str(m): records["executions"].get(decision["month"].replace("-", "") + f"_{m}")
                      for m in hf001.MULTIPLIERS}
        if any(row is None for row in executions.values()):
            continue  # No paired valid cohort; the final count excludes this decision.
        for row in rule["universe"]:
            code = row["stock_code"]
            slot = executions["1.0"]["f_both"]["slots"][code]
            costs = {m: exe["f_both"]["slots"][code]["round_trip_cost"] for m, exe in executions.items()}
            if sealed and any(v is None for v in costs.values()):
                raise ValueError("FW001 f_both sealed execution costs blocked; legally sourced tax rate required")
            fills[decision["trade_date"], code] = {**slot, "costs": costs}
            selections.append({**row, "decision_date": pd.Timestamp(decision["decision_date"]),
                "trade_date": pd.Timestamp(decision["trade_date"]), "raise_covered": True,
                "f_both": code in removed})
    if not selections:
        return pd.DataFrame()
    sessions = recorder.trading_calendar.session_calendar(
        min(pd.Timestamp(row["decision_date"]) for row in selections), days[-1])
    return hf001.forward_observations(prices, pd.DataFrame(selections), sessions[sessions <= days[-1]],
        experiment_id=recorder.EXPERIMENT_ID, evaluation_end="2027-11-01", executions=fills if sealed else None)


def f_months(observations, *, loss=False):
    if observations.empty:
        return [], []
    rows, groups = hf001._monthly(observations, "f_both", loss=loss)
    for row in rows:
        row["month"] = row["decision_date"][:7]
    return rows, groups


def quotes(record):
    return {code: hc003.Quote(**{**row, "raw_open": np.nan if row["raw_open"] is None else row["raw_open"],
                                "adv": np.nan if row["adv"] is None else row["adv"]})
            for code, row in record["quotes"].items()}


def valuation_book(records, prices, days):
    if days.empty:
        return None
    candidates = [record for folder in ("decisions", "missing") for record in records[folder].values()
                  if record.get("chain")]
    if not candidates:
        return None
    seed = min(candidates, key=lambda row: row["chain_date"])
    # Last-trade detection includes re-trading through the registered freeze.
    # Monthly wealth still ends at the explicit 2027-10-29 close in av_months.
    end = min(days[-1], pd.Timestamp(recorder.FREEZE_AT.date()) - pd.Timedelta(days=1))
    period = recorder.trading_calendar.session_calendar(pd.Timestamp(seed["chain_date"]) + pd.Timedelta(days=1), end)
    if period.empty:
        return None
    entries = sorted({row["trade_date"] for row in records["executions"].values()
                      if pd.Timestamp(seed["chain_date"]) < pd.Timestamp(row["trade_date"]) <= end})
    def windows():
        for start in range(0, len(period), 20):
            yield prices.loc[prices.index.get_level_values("trade_date").isin(period[start:start + 20])]
    return hc003.PriceBook.from_windows(windows(), period, entries, end,
        experiment_id=recorder.EXPERIMENT_ID, initial_chain=seed["chain"],
        close_dates=[min(end, pd.Timestamp("2027-10-29"))])


def av_months(records, data_dir, days, *, pc_dir=None, book=None):
    """Use recorded quantities/cash; never rebalance the primary retrospectively."""
    rows = []
    continuity = True
    previous_states = {}
    for month in recorder.MONTHS:
        key = str(month).replace("-", "")
        decision = records["decisions"].get(key)
        executions = {str(m): records["executions"].get(key + f"_{m}") for m in hf001.MULTIPLIERS}
        if any(r is None or r["av_all"]["status"] != "executed" for r in executions.values()):
            continuity = False
            continue
        costs = {}
        u = decision["rules"]["av_all"]["universe"] if decision is not None and decision["label"] == "forward" else []
        for m, exe in executions.items():
            state = hc003.PortfolioState.from_dict(exe["av_all"]["state_after"])
            if m in previous_states and exe["av_all"]["state_before"] != previous_states[m]:
                raise ValueError("FW001 av_all sealed holdings/cash chain is broken")
            source_decision = decision if decision is not None and decision["label"] == "forward" else records["missing"].get(key)
            if source_decision is not None and exe["decision_hash"] != recorder.digest(source_decision):
                raise ValueError("FW001 execution belongs to a different decision")
            previous_states[m] = state.to_dict()
            next_key = str(month + 1).replace("-", "") + f"_{float(m)}"
            following = records["executions"].get(next_key)
            if month == recorder.MONTHS[-1]:
                end = pd.Timestamp("2027-10-29")
                if len(days) and days[-1] < end:
                    continue
                end_marks = book.marks(end, "close") if book is not None else {}
            elif following is not None:
                end = pd.Timestamp(following["trade_date"])
                end_marks = book.marks(end) if book is not None and end in book.openings else {
                    code: q.mark for code, q in quotes(following).items()}
            else:
                continue
            start_marks = book.marks(exe["trade_date"]) if book is not None and pd.Timestamp(exe["trade_date"]) in book.openings else {
                code: q.mark for code, q in quotes(exe).items()}
            before_state = hc003.PortfolioState.from_dict(exe["av_all"]["state_before"])
            beginning = hc003.portfolio_value(before_state, start_marks) if set(before_state.quantities) <= set(start_marks) else None
            ending = hc003.portfolio_value(state, end_marks) if set(state.quantities) <= set(end_marks) else None
            benchmark_known = u and all(r["stock_code"] in end_marks and r["stock_code"] in start_marks for r in u)
            benchmark = float(np.mean([end_marks[r["stock_code"]] / start_marks[r["stock_code"]] - 1
                                      if start_marks[r["stock_code"]] > 0 else 0.0 for r in u])) if benchmark_known else None
            ret = ending / beginning - 1 if beginning is not None and beginning > 0 and ending is not None else None
            costs[m] = {"return": ret, "benchmark": benchmark,
                        "excess": ret - benchmark if ret is not None and benchmark is not None else None,
                        "end_value": ending, "accounting": exe["av_all"]["accounting"]}
        if len(costs) == len(hf001.MULTIPLIERS):
            rows.append({"month": str(month), "costs": costs})
    return rows, continuity and len(rows) == 12 and all(
        row["costs"]["1.0"]["excess"] is not None for row in rows)


def av_book_and_selections(records, prices, days):
    decisions = [records["decisions"].get(str(month).replace("-", "")) for month in recorder.MONTHS]
    first = decisions[0]
    if first is None or first["chain"] is None:
        raise ValueError("FW001 controls/sensitivity unavailable: initial chain is missing")
    selected = []
    for number, (month, decision) in enumerate(zip(recorder.MONTHS, decisions)):
        if decision is None:
            raise ValueError("FW001 controls/sensitivity unavailable: missing decision U")
        planned = recorder.plan(decision["decision_date"])
        following = decisions[number + 1] if number + 1 < len(decisions) else None
        if number + 1 < len(decisions) and following is None:
            raise ValueError("FW001 controls/sensitivity unavailable: missing next decision")
        end = "2027-10-29" if number == 11 else following["trade_date"]
        rule = decision["rules"]["av_all"]
        u = pd.DataFrame(rule["universe"]).set_index("stock_code")
        selected.append({"month": str(month), "decision_date": decision["decision_date"],
            "trade_date": planned["trade_date"], "end_date": end, "end_kind": "close" if number == 11 else "open",
            "universe": u, "F_vol": set(rule["F_vol"]), "F_raise": set(rule["F_raise"]),
            "no_trade": decision["label"] != "forward", "blocked_buys": set(), "filter_covered": True})
    book = valuation_book(records, prices, days)
    return book, selected


def av_sensitivity(book, selections, records):
    monthly = {}
    for m in hf001.MULTIPLIERS:
        state = hc003.PortfolioState()
        for selected in selections:
            exe = records["executions"][selected["month"].replace("-", "") + f"_{m}"]
            original = quotes(exe)
            loss_quotes = {code: hc003.Quote(0.0, q.raw_open, q.market, q.adv, False, q.limit_up, q.limit_down)
                if code in book.zero_from and book.zero_from[code] <= pd.Timestamp(selected["trade_date"]) else q
                for code, q in original.items()}
            beginning = hc003.portfolio_value(state, book.marks(selected["trade_date"], loss=True))
            state, _ = hc003.rebalance(state, loss_quotes, av001.target_set("av_all", selected, state.quantities),
                selected["trade_date"], multiplier=m, no_trade=selected["no_trade"])
            ending = hc003.portfolio_value(state, book.marks(selected["end_date"], selected["end_kind"], loss=True))
            ret = ending / beginning - 1 if beginning > 0 else None
            benchmark = hc003.benchmark_return(book, selected, loss=True)
            monthly.setdefault(selected["month"], {"month": selected["month"], "costs": {}})["costs"][str(m)] = {
                "excess": ret - benchmark if ret is not None and benchmark is not None else None,
                "return": ret, "benchmark": benchmark}
    return list(monthly.values())


def av_controls(book, selections, records, *, secondary=False):
    rng = np.random.default_rng(0)
    states = [hc003.PortfolioState() for _ in range(av001.N_CONTROLS)]
    values = [[] for _ in states]
    diagnostics = []
    for selected in selections:
        exe = records["executions"][selected["month"].replace("-", "") + "_1.0"]
        actual_h = av001.target_set("av_all", selected, {})
        retained = len(actual_h & set(exe["av_all"]["state_before"]["quantities"]))
        for path, state in enumerate(states):
            beginning = hc003.portfolio_value(state, book.marks(selected["trade_date"]))
            if secondary:
                targets = hc003.random_target(selected["universe"].index, state.quantities,
                    len(actual_h), exe["av_all"]["accounting"]["retained_count"], rng)
                adjustment, shortage = 0, len(actual_h) - len(targets)
            else:
                targets, adjustment, shortage = av001.random_target(selected["universe"].index,
                    state.quantities, len(actual_h), retained, rng)
            states[path], accounting = hc003.rebalance(state, quotes(exe), targets,
                selected["trade_date"], no_trade=selected["no_trade"],
                allocation_n=exe["av_all"]["accounting"]["n"] if secondary else None)
            ending = hc003.portfolio_value(states[path], book.marks(selected["end_date"], selected["end_kind"]))
            benchmark = hc003.benchmark_return(book, selected)
            values[path].append(ending / beginning - 1 - benchmark)
            diagnostics.append({"month": selected["month"], "path": path, "adjustment": adjustment,
                "shortage": shortage, **{key: accounting[key] for key in
                ("buy_turnover", "sell_turnover", "cost_fraction", "cash_weight",
                 "band_skipped_buy_value", "band_skipped_sell_value")}})
    means = [float(np.mean(path)) for path in values]
    return {"seed": 0, "paths": 200, "path_means": means, "diagnostics": diagnostics,
            "used_for_judgement": False}


def repricing_audit(records, prices, days, input_hashes):
    """Explicit PC frozen-data audit; never replaces primary sealed fills.

    The caller must provide the PC snapshot taken at the registered freeze and
    its hashes. No current PC file is silently treated as that old snapshot.
    """
    if not input_hashes:
        raise ValueError("FW001 repricing audit requires frozen PC input hashes")
    f_rows, _ = f_months(f_observations(records, prices, days, sealed=False))
    book, selected = av_book_and_selections(records, prices, days)
    av_rows = hc003.simulate(book, selected, "av_all", experiment_id=recorder.EXPERIMENT_ID,
        variant_names=("av_all",), target_set_fn=av001.target_set,
        observation_validity=av001.observation_validity)["per_month"]
    return {"used_for_judgement": False, "audit": "corrected-price re-execution",
            "input_hashes": input_hashes, "f_both": f_rows, "av_all": av_rows}


def evaluate(data_dir, *, pc_dir=None, final=False, controls=True):
    records, inputs = read_records(data_dir, final=final)
    comparisons, comparison_hash = comparison_values()
    prices, days = price_frame(data_dir, records, pc_dir=pc_dir)
    observations = f_observations(records, prices, days)
    f_rows, groups = f_months(observations)
    f_loss, _ = f_months(observations, loss=True)
    book, selections = valuation_book(records, prices, days), None
    av_rows, continuous = av_months(records, data_dir, days, pc_dir=pc_dir, book=book)
    valid = {name: sum(r["label"] == "forward" and bool(r["rules"][name]["universe"])
                       for r in records["decisions"].values()) for name in ("f_both", "av_all")}
    valid["f_both"] = sum(r["label"] == "forward" and bool(r["rules"]["f_both"]["universe"])
        and all(r["month"].replace("-", "") + f"_{m}" in records["executions"] for m in hf001.MULTIPLIERS)
        for r in records["decisions"].values())
    f_main = judge_rule("f_both", f_rows, comparisons["f_both"]["value"], valid_decisions=valid["f_both"])
    f_sensitive = judge_rule("f_both", f_loss, comparisons["f_both"]["value"], valid_decisions=valid["f_both"])
    av_main = judge_rule("av_all", av_rows, comparisons["av_all"]["value"], valid_decisions=valid["av_all"], continuous=continuous)
    av_loss, av_random = [], {"status": "unavailable", "reason": "twelve-month inputs not yet complete"}
    if continuous:
        recorder.require_2027_costs()
        _, selections = av_book_and_selections(records, prices, days)
        av_loss = av_sensitivity(book, selections, records)
        if controls:
            av_random = av_controls(book, selections, records)
            actual = av_main["mean_by_cost_multiplier"]["1.0"]
            av_random["share_of_controls"] = float(np.mean(np.array(av_random["path_means"]) >= actual))
            av_random["secondary_HC003"] = av_controls(book, selections, records, secondary=True)
    av_sensitive = judge_rule("av_all", av_loss, comparisons["av_all"]["value"], valid_decisions=valid["av_all"],
                              continuous=continuous and len(av_loss) == 12)
    result = {"experiment_id": recorder.EXPERIMENT_ID, "stage": "final" if final else "interim",
        "freeze_at": recorder.FREEZE_AT.isoformat(), "input_hashes": inputs,
        "comparison_values_sha256": comparison_hash, "comparisons": comparisons,
        "secondary_original_hf001": original_hf_comparison(comparisons),
        "evaluation_code_hashes": {"backtesting/experiments/fw001.py": dart_listing.file_hash(Path(__file__)),
                                   **recorder.code_hashes()},
        "rules": {"f_both": {**apply_sensitivity(f_main, f_sensitive), "per_month": f_rows,
            "delisting_loss_months": f_loss, "random_control": {
                **hf001.random_controls(groups, f_rows), "used_for_judgement": False} if controls else None},
            "av_all": {**apply_sensitivity(av_main, av_sensitive), "per_month": av_rows,
                        "delisting_loss_months": av_loss, "random_control": av_random}},
        "missing_months": sorted((set(map(str, recorder.MONTHS)) if final else
            {r["month"] for r in records["missing"].values()}) -
            {r["month"] for r in records["decisions"].values() if r["label"] == "forward"}),
        "missing_execution_months": sorted(str(month) for month in recorder.MONTHS if any(
            str(month).replace("-", "") + f"_{m}" not in records["executions"] for m in hf001.MULTIPLIERS)),
        "price_policy": "Use a matching PC hash or the server content-addressed frozen copy. Sealed fills are never repriced.",
        "secondary_index_excess": {"status": "unavailable", "reason": "KOSPI/KOSDAQ index snapshots are not supplied by the stock-daily recorder"}}
    hc_path = Path(data_dir) / recorder.RECORD_DIR / "secondary_hc002.json"
    result["secondary_hc002_base"] = {"status": "unavailable", "used_for_judgement": False}
    if hc_path.relative_to(data_dir).as_posix() in inputs:
        saved = recorder.load_json(hc_path)
        result["secondary_hc002_base"] = {"used_for_judgement": False, "source_sha256": saved["source_sha256"],
            "per_month": [row for row in saved["evaluation"]["decisions"] if
                "2026-10-30" <= row["decision_date"] <= "2027-09-30"]}
    if not final:
        for rule in result["rules"].values():
            rule["verdict"] = "pending_final_freeze"
    return result


def publish(result, output, *, repo_root=PROJECT_ROOT):
    """One canonical final result, then exactly one registry row per judged rule.

    A restart after publication can finish a missing registry row. It cannot
    evaluate again or replace the canonical result.
    """
    recorder.verify_manifest(implementation=True)
    with file_lock(Path(repo_root) / recorder.RESEARCH_DIR / ".evaluation.lock"):
        return _publish(result, output, repo_root=repo_root)


def _publish(result, output, *, repo_root):
    if result["stage"] != "final" or recorder.now() < recorder.FREEZE_AT:
        raise ValueError("FW001 publishes only after the registered final freeze")
    try:
        result_hash = recorder.digest(result)
    except (TypeError, ValueError) as exc:
        raise ValueError("FW001 result must contain finite JSON values") from exc
    canonical = Path(repo_root) / recorder.RESEARCH_DIR / "results/final.json"
    rows = hc003.registry_rows(repo_root)
    registration = experiment_registry.verify_preregistration(recorder.EXPERIMENT_ID, repo_root)
    accepted = {registration.commit_hash} | experiment_registry._commit_aliases(
        Path(repo_root), recorder.EXPERIMENT_ID, registration.commit_hash)
    if any(row["experiment_id"] == recorder.EXPERIMENT_ID and row["prereg_commit"] not in accepted for row in rows):
        raise ValueError("FW001 registry prereg_commit differs before publication")
    trials = []
    for name, rule in result["rules"].items():
        if any(row["experiment_id"] == recorder.EXPERIMENT_ID and row["variant"] == name
               and json.loads(row["metrics_json"]).get("stage") == "forward_final" for row in rows):
            continue
        metrics = {key: rule[key] for key in ("valid_decisions", "monthly_observations", "mean_by_cost_multiplier",
                    "sealed_comparison", "base_verdict", "delisting_loss_verdict")}
        metrics.update(stage="forward_final", result_file=canonical.relative_to(repo_root).as_posix(),
                       result_sha256=result_hash, missing_months=result["missing_months"])
        experiment_registry.encode_metrics(metrics)
        if not isinstance(rule["verdict"], str) or not rule["verdict"].strip():
            raise ValueError("FW001 registry verdict must be a nonempty string")
        trials.append((name, metrics, rule["verdict"]))
    recorder.put(canonical, result, execute=True)
    recorder.put(output, result, execute=True)
    for name, metrics, verdict in trials:
        experiment_registry.record_trial(recorder.EXPERIMENT_ID, name, metrics, verdict,
            repo_root=repo_root, note="FW001 first-year forward replication; economic gate, reporting-only statistics")


def main(argv=None):
    recorder.verify_manifest(implementation=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--pc-data-dir", type=Path)
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-2027-costs", action="store_true")
    args = parser.parse_args(argv)
    if args.check_2027_costs:
        recorder.require_2027_costs()
        return 0
    if args.data_dir is None:
        parser.error("--data-dir is required")
    canonical = PROJECT_ROOT / recorder.RESEARCH_DIR / "results/final.json"
    result = recorder.load_json(canonical) if args.final and canonical.is_file() else evaluate(
        args.data_dir, pc_dir=args.pc_data_dir, final=args.final)
    if args.output is not None:
        if not args.final:
            parser.error("--output publishes only the one final evaluation")
        publish(result, args.output)
    else:
        print(recorder.encoded(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
