"""python -m backtesting.experiments.lh001 [--experiment LH001|LH002] {probe,screening,final,status}."""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import exchange_calendars as calendars
import pandas as pd

from backtesting import experiment_registry, holdout
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001, hc002

from . import FINAL_END, MODEL, VARIANTS, experiment_assumptions, workspace
from . import arms, evaluate, inputs, probe, probe_v2
from .config import EXPERIMENTS, LH001
from .runner import LlmRunner, StageStopped, check_committed, prompt_hashes, read_json, save_json


def session_calendar():
    return calendars.get_calendar("XKRX", start="2015-01-01", end="2026-11-30").sessions


def _probe(config):
    return probe_v2 if config.probe_version == "v2" else probe


def _registration(repo_root, config=LH001):
    fields = common.load_preregistration(config.experiment_id, repo_root=repo_root)
    if fields["variants_planned"] != 5 or not fields["uses_holdout"] or not fields["uses_llm"]:
        raise ValueError(f"{config.name} requires exactly five variants, LLM and holdout")
    return fields


def _check_runner(runner, config):
    if isinstance(runner, LlmRunner) and runner.config != config:
        raise ValueError("runner experiment configuration differs from the requested stage")


def price_days(data_dir):
    return pd.DatetimeIndex(sorted(pd.Timestamp(path.stem) for path in
                                  (Path(data_dir) / "market/krx_daily").glob("*/*.jsonl")
                                  if len(path.stem) == 8 and path.stem.isdigit() and path.stat().st_size))


def weekly_calendar(sessions, clean_start):
    days = pd.DatetimeIndex(sessions).sort_values().unique()
    weekly = pd.Series(days, index=days.to_period("W-FRI")).groupby(level=0).max()
    planned, excluded = [], []
    for day in weekly:
        # October is an exit-price-only forward window (preregistration header).
        if not pd.Timestamp(clean_start) <= day <= pd.Timestamp("2026-09-30"):
            continue
        position = days.get_loc(day)
        row = {"decision_date": day.date().isoformat()}
        if position + 20 >= len(days):
            excluded.append({**row, "reason": "incomplete_calendar_horizon"})
            continue
        row.update(trade_date=days[position + 1].date().isoformat(), exit_date=days[position + 20].date().isoformat())
        (planned if days[position + 20] <= pd.Timestamp(FINAL_END) else excluded).append(
            row if days[position + 20] <= pd.Timestamp(FINAL_END) else {**row, "reason": "exit_after_final_end"})
    return planned, excluded


def status(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, config=LH001):
    _registration(repo_root, config)
    root = workspace(data_dir, config)
    days = price_days(data_dir)
    budget = LlmRunner(data_dir=data_dir, repo_root=repo_root, config=config).budget()
    calls = [read_json(path) for path in sorted((root / "calls").glob("*.json"))]
    states = {}
    for stage in ("probe", "screening", "final"):
        path = root / f"{stage}.json"
        states[stage] = {"verdict": read_json(path).get("verdict"), "smoke": read_json(path).get("smoke", False)} if path.exists() else "missing"
    ledger = Path(repo_root) / holdout.LEDGER_PATH
    claimed = any(row["experiment_id"] == config.experiment_id for row in
                  [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] if row) if ledger.exists() else False
    return {"experiment_id": config.experiment_id, "model": MODEL, "budget": {**budget, "remaining": budget["cap"] - budget["used"]},
            "cached_valid_calls": sum(call["valid"] for call in calls), "cached_calls": len(calls), "stages": states,
            "holdout_claimed": claimed, "latest_krx_day": days[-1].date().isoformat() if len(days) else None,
            "final_data_ready": bool(len(days) and days[-1] >= pd.Timestamp(FINAL_END)),
            "prompt_hashes": prompt_hashes(config.prompt_dir), "note": "Read-only status; no model, strategy returns or protected file contents read."}


def dry_run(stage, *, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, limit_decisions=None, sessions=None, config=LH001):
    info = status(data_dir=data_dir, repo_root=repo_root, config=config)
    sessions = session_calendar() if sessions is None else sessions
    root = workspace(data_dir, config)
    exclusions = []
    if stage == "probe":
        decisions = [str(month) for month in _probe(config).MONTHS]
        calls_per, diagnostics = 1, 0
        span = "month-end facts only: 2024-12..2026-09; dedicated contamination reader"
        if config.probe_version == "v2":
            span = "month-end facts only: 2023-12..2024-06 positive-control anchors + 2024-12..2026-09; dedicated contamination reader; eight questions per month"
    elif stage == "screening":
        planned, exclusions = hc002.rebalance_calendar(sessions)
        decisions = [row["decision_date"] for row in planned]
        calls_per = 3
        diagnostics = sum("2023-01-01" <= day <= "2025-11-30" for day in decisions)
        span = "price files strictly <=2025-12-31; decisions 2017-05..2025-11"
    else:
        saved = read_json(root / "probe.json") if (root / "probe.json").exists() else None
        clean_start = saved.get("clean_start") if saved is not None and saved.get("verdict") == "clean" else None
        planned, exclusions = weekly_calendar(sessions, clean_start or "2026-01-01")
        decisions = [row["decision_date"] for row in planned]
        calls_per, diagnostics = 15, 0
        span = "one HoldoutSession; start from probe; exits <=2026-10-30; January start shown provisionally if probe missing"
    if limit_decisions is not None:
        decisions = decisions[:limit_decisions]
        diagnostics = sum("2023-01-01" <= day <= "2025-11-30" for day in decisions) if stage == "screening" else 0
    calls = len(decisions) * calls_per + diagnostics
    cached = [read_json(path) for path in sorted((root / "calls").glob("*.json"))]
    hits = sum(call["valid"] and call.get("stage") in ({"screening", "memory_diagnostic"} if stage == "screening" else {stage}) for call in cached)
    result = {"stage": stage, "dry_run": True, "smoke": limit_decisions is not None, "decisions": len(decisions),
            "first_decision": decisions[0] if decisions else None, "last_decision": decisions[-1] if decisions else None,
            "estimated_calls": calls, "estimated_retry_upper_bound": calls * 3, "cache_hits": min(hits, calls),
            "cache_hits_note": "Upper bound from stored valid stage calls; exact input-dependent hits are resolved on execute.",
            "estimated_remaining_calls_lower_bound": max(0, calls - hits), "budget": info["budget"],
            "budget_fits_without_retries": calls - min(hits, calls) <= info["budget"]["remaining"],
            "memory_diagnostic_decisions": diagnostics, "exclusions": exclusions,
            "holdout_horizon_exclusions": sum(row["reason"] == "holdout_horizon" for row in exclusions),
            "read_plan": span, "latest_krx_day": info["latest_krx_day"], "final_data_ready": info["final_data_ready"],
            "probe_status": info["stages"]["probe"], "note": "No calls, source price/financial/protected benchmark reads, output writes, registry rows or holdout claims."}
    if config != LH001:
        result["experiment_id"] = config.experiment_id
    return result


def _payload(stage, variants, started, *, assumptions=None, config=LH001, **extra):
    verdicts = [variants[arm]["verdict"] for arm in VARIANTS[:4]]
    verdict = "pass" if "pass" in verdicts else "fail" if any(v in ("fail", "screening_fail") for v in verdicts) else "insufficient"
    return {"experiment_id": config.experiment_id, "stage": stage, "selected_variant": "all_fixed_variants", "verdict": verdict,
            "reasons": ["Each fixed variant is reported separately; aggregate pass means at least one LLM variant passed."],
            "variants": variants, "interpretations": evaluate.INTERPRETATIONS,
            "assumptions": list(experiment_assumptions(config) if assumptions is None else assumptions),
            "performance": {"runtime_seconds": time.perf_counter() - started,
                            "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}, **extra}


def _unmeasured(verdict, reason):
    return {"verdict": verdict, "reasons": [reason], "primary": evaluate.newey_west([])}


def _publish(payload, data_dir, repo_root, smoke, config=LH001):
    value, paths = evaluate.publish(payload, repo_root=repo_root, smoke=smoke, config=config)
    # Atomic official checkpoint only after successful result/registry publication.
    if not smoke:
        save_json(workspace(data_dir, config) / f"{payload['stage']}.json", value)
    value["result_files"] = [str(path) for path in paths]
    return value


def _completed(stage, data_dir, limit_decisions, config=LH001):
    path = workspace(data_dir, config) / f"{stage}.json"
    if limit_decisions is not None or not path.exists():
        return None
    saved = read_json(path)
    if saved.get("stage") != stage or saved.get("smoke") or saved.get("interruption"):
        raise StageStopped("official checkpoint is not a completed stage")
    return {**saved, "completed_stage_reused": True}


def execute_probe(*, data_dir, repo_root=PROJECT_ROOT, limit_decisions=None, runner=None, sessions=None, facts_reader=None, config=LH001):
    _registration(repo_root, config)
    _check_runner(runner, config)
    completed = _completed("probe", data_dir, limit_decisions, config)
    if completed is not None:
        return completed
    if runner is None:
        check_committed(config.prompt_dir, repo_root)
    started = time.perf_counter()
    measurement = _probe(config)
    months = list(measurement.MONTHS[:limit_decisions] if limit_decisions is not None else measurement.MONTHS)
    reader = measurement.read_contamination_facts if facts_reader is None else facts_reader
    facts = reader(months, data_dir=data_dir, sessions=session_calendar() if sessions is None else sessions)
    quizzes = [measurement.make_questions(month, facts[str(month)]) for month in months]
    runner = runner or LlmRunner(data_dir=data_dir, repo_root=repo_root, config=config)
    scores, calls = [], []
    for quiz in quizzes:
        call = runner.call(f"{config.probe_version}_probe.txt", measurement.render_questions(quiz),
                           f"{config.probe_version}_probe.schema.json", 0, validate=measurement.validate_answers, stage="probe")
        calls.append(call["key"])
        if call["valid"]:
            scores.append(measurement.score(quiz, call["output"]))
    clean = measurement.clean_window(scores)
    variants = {arm: _unmeasured(clean["verdict"] if clean["verdict"] in ("insufficient_clean_window", "probe_insensitive") else "insufficient",
                                "probe stage only; no strategy evaluated") for arm in VARIANTS}
    payload = _payload("probe", variants, started, config=config, scores=scores, calls=calls, probe=clean, probe_read_note=measurement.READ_NOTE,
                       questions=quizzes, facts_hash=inputs.digest(facts), prompt_hashes=runner.hashes)
    published = _publish(payload, data_dir, repo_root, limit_decisions is not None, config)
    if limit_decisions is None:
        save_json(workspace(data_dir, config) / "probe.json", {**published, **clean})
    return published


def _prepare_decisions(planned, *, data_dir, repo_root, sessions, with_text, text_loader=None, price_end=None, config=LH001):
    prepared, frames, excluded = [], [], []
    available = price_days(data_dir)
    if not planned:
        return {"prepared": [], "observations": [], "exclusions": []}
    first = pd.Timestamp(planned[0]["decision_date"])
    start = sessions[max(0, sessions.get_loc(first) - 251)]
    end = price_end or (FINAL_END if first.year == 2026 else "2025-12-31")
    last_dates = inputs.load_last_dates(start, end, data_dir=data_dir, repo_root=repo_root, config=config)
    for row in planned:
        day = pd.Timestamp(row["decision_date"])
        position = sessions.get_loc(day)
        required = sessions[max(0, position - 251):position + 21]
        missing = required.difference(available)
        if position < 251 or len(missing):
            excluded.append({**row, "reason": "missing_price_sessions", "missing_sessions": [date.date().isoformat() for date in missing]})
            continue
        prices = inputs.load_history(required[0], required[-1], data_dir=data_dir, repo_root=repo_root, config=config)
        bundle = inputs.build_inputs(prices, day, data_dir=data_dir, repo_root=repo_root, config=config)
        if not bundle["stocks"]:
            excluded.append({**row, "reason": "no_eligible_signals"})
            continue
        universe = pd.DataFrame([{"decision_date": day, "stock_code": stock["stock_code"],
                                 "phase": stock["phase"], "avg_trading_value_20d": stock["adv_20"]} for stock in bundle["stocks"]])
        frames.append(evaluate.observations(prices, universe, repo_root=repo_root, last_dates=last_dates, config=config))
        texts = {}
        if with_text:
            if text_loader is None:
                from src.ingestion.dart_business_text import load_business_text
                text_loader = load_business_text
            # Freeze all possible candidates inside the one source-read session;
            # later model choices and quota resumes only access this snapshot.
            for stock in bundle["stocks"]:
                texts[stock["stock_code"]] = text_loader(stock["stock_code"], row["decision_date"], data_dir)
        prepared.append({"bundle": bundle, "texts": texts})
    records = pd.concat(frames, ignore_index=True).to_dict("records") if frames else []
    return {"prepared": prepared, "observations": inputs.json_value(records), "exclusions": excluded}


def _snapshot(path, prepare, *, metadata):
    if path.exists():
        stored = read_json(path)
        if stored["metadata"] != metadata or stored["sha256"] != inputs.digest(stored["data"]):
            raise StageStopped("frozen snapshot changed or stage parameters differ")
        return stored["data"]
    data = prepare()
    save_json(path, {"metadata": metadata, "data": data, "sha256": inputs.digest(data)})
    return data


def _frame(snapshot):
    frame = pd.DataFrame(snapshot["observations"])
    if frame.empty:
        return pd.DataFrame(columns=["decision_date", "stock_code", "gross", "phase", "bucket", "reason", "exit_date", "stale_exit",
                                     "unadjusted_fallback", "halted_after_entry", "limit_down_exit", *[f"cost_{m}" for m in evaluate.MULTIPLIERS]])
    for field in ("decision_date", "entry_date", "exit_date"):
        frame[field] = pd.to_datetime(frame[field])
    return frame


def _run_snapshot(snapshot, runner, *, final, data_dir, config=LH001):
    decisions, diagnostics = [], []
    for prepared in snapshot["prepared"]:
        bundle = prepared["bundle"]
        loader = lambda code, day, directory: prepared["texts"][code]
        selected = arms.run_decision(runner, bundle, final=final, text_loader=loader, config=config)
        decisions.append({"decision_date": bundle["decision_date"], "arms": selected})
        if not final and "2023-01-01" <= bundle["decision_date"] <= "2025-11-30":
            named = arms.run_decision(runner, bundle, final=False, anonymised=False, c_only=True, stage="memory_diagnostic", config=config)
            diagnostics.append({"decision_date": bundle["decision_date"], "arms": named})
    return decisions, diagnostics


def execute_screening(*, data_dir, repo_root=PROJECT_ROOT, limit_decisions=None, runner=None, sessions=None, config=LH001):
    _registration(repo_root, config)
    _check_runner(runner, config)
    completed = _completed("screening", data_dir, limit_decisions, config)
    if completed is not None:
        return completed
    if runner is None:
        check_committed(config.prompt_dir, repo_root)
    started = time.perf_counter()
    sessions = session_calendar() if sessions is None else sessions
    planned, excluded = hc002.rebalance_calendar(sessions)
    if limit_decisions is not None:
        planned = planned[:limit_decisions]
    if any(pd.Timestamp(row["exit_date"]) >= pd.Timestamp("2026-01-01") for row in planned):
        raise ValueError("screening horizons must not cross 2026-01-01")
    root = workspace(data_dir, config)
    snapshot = _snapshot(root / ("screening_smoke_snapshot.json" if limit_decisions is not None else "screening_snapshot.json"),
                         lambda: _prepare_decisions(planned, data_dir=data_dir, repo_root=repo_root, sessions=sessions, with_text=False, config=config),
                         metadata={"stage": "screening", "limit": limit_decisions, "planned": planned})
    runner = runner or LlmRunner(data_dir=data_dir, repo_root=repo_root, config=config)
    decisions, diagnostics = _run_snapshot(snapshot, runner, final=False, data_dir=data_dir, config=config)
    frame = _frame(snapshot)
    result = evaluate.metrics(frame, decisions, cadence="monthly")
    variants = result.pop("variants")
    for arm in ("b_num", "c_num"):
        verdict, reasons = evaluate.screening_verdict(variants[arm])
        variants[arm].update(verdict=verdict, reasons=reasons)
    for arm in ("b_text", "c_text"):
        variants[arm] = _unmeasured("insufficient", "text is not evaluated in screening")
    variants["hc002_holdout"] = _unmeasured("not_applicable", "no holdout opened in screening")
    named = evaluate.metrics(frame, diagnostics, cadence="monthly")["variants"]["c_num"]
    anonymous = {row["decision_date"]: row for row in variants["c_num"]["per_decision"]}
    memory_pairs = [{"decision_date": row["decision_date"], "effect": row["net_1.0"] - anonymous[row["decision_date"]]["net_1.0"]}
                    for row in named["per_decision"] if row["decision_date"] in anonymous]
    memory = {**evaluate.paired_stats(memory_pairs, "effect", "monthly"), "per_decision": memory_pairs, "named": named}
    payload = _payload("screening", variants, started, config=config, **result, memory_dependence=memory, decisions=decisions,
                       exclusions=excluded + snapshot["exclusions"], holdout_horizon_exclusions=sum(row["reason"] == "holdout_horizon" for row in excluded),
                       snapshot_hash=inputs.digest(snapshot), prompt_hashes=runner.hashes)
    return _publish(payload, data_dir, repo_root, limit_decisions is not None, config)


def _prepare_hc002(*, data_dir, repo_root, sessions, config=LH001):
    passing = evaluate.hc002_passing_row(repo_root)
    if passing is None:
        return _unmeasured("not_applicable", "HC002 has no passing registry row")
    planned = []
    for month in pd.period_range("2026-01", "2026-05", freq="M"):
        day = sessions[sessions.to_period("M") == month][-1]
        position = sessions.get_loc(day)
        if sessions[position + 20] <= pd.Timestamp("2026-06-30"):
            planned.append({"decision_date": day.date().isoformat(), "trade_date": sessions[position + 1].date().isoformat(),
                            "exit_date": sessions[position + 20].date().isoformat()})
    snapshot = _prepare_decisions(planned, data_dir=data_dir, repo_root=repo_root, sessions=sessions,
                                  with_text=False, price_end="2026-06-30", config=config)
    frame = _frame(snapshot)
    frame["trade_date"] = frame.decision_date
    primary = hc001.portfolio_metrics(frame)
    verdict, reasons = evaluate.hc002_verdict(primary, passing)
    return {**primary, "primary": primary["excess"], "verdict": verdict, "reasons": reasons,
            "passing_registry_row": passing, "exclusions": snapshot["exclusions"], "linked_session": config.experiment_id}


def execute_final(*, data_dir, repo_root=PROJECT_ROOT, limit_decisions=None, runner=None, sessions=None, text_loader=None, config=LH001):
    fields = _registration(repo_root, config)
    _check_runner(runner, config)
    completed = _completed("final", data_dir, limit_decisions, config)
    if completed is not None:
        return completed
    root = workspace(data_dir, config)
    probe_path = root / "probe.json"
    if not probe_path.is_file():
        raise ValueError("final requires a stored completed probe result")
    saved_probe = read_json(probe_path)
    if saved_probe.get("smoke") or saved_probe.get("verdict") != "clean":
        raise ValueError(f"final refuses {saved_probe.get('verdict')} or smoke probe")
    recomputed = _probe(config).clean_window(saved_probe["scores"])
    checked = ("verdict", "clean_start", "pooled_accuracy")
    if config.probe_version == "v2":
        checked += ("pooled_p_value", "positive_control")
    if any(saved_probe.get(key) != recomputed[key] for key in checked):
        raise ValueError("stored probe clean-window result does not match its scores")
    days = price_days(data_dir)
    if not len(days) or days[-1] < pd.Timestamp(FINAL_END):
        raise ValueError("final requires latest stored KRX day >=2026-10-30")
    screening_path = root / "screening.json"
    if not screening_path.is_file() or read_json(screening_path).get("smoke"):
        raise ValueError("final requires completed official numeric screening")
    screening = read_json(screening_path)
    if runner is None:
        check_committed(config.prompt_dir, repo_root)
    started = time.perf_counter()
    sessions = session_calendar() if sessions is None else sessions
    planned, excluded = weekly_calendar(sessions, saved_probe["clean_start"])
    if limit_decisions is not None:
        planned = planned[:limit_decisions]
    snapshot_path = root / ("final_smoke_snapshot.json" if limit_decisions is not None else "final_snapshot.json")
    def prepare():
        # First protected source read occurs only after this durable single claim.
        with holdout.HoldoutSession(config.experiment_id, repo_root=repo_root):
            return {**_prepare_decisions(planned, data_dir=data_dir, repo_root=repo_root, sessions=sessions, with_text=True, text_loader=text_loader, config=config),
                    "hc002_holdout": _prepare_hc002(data_dir=data_dir, repo_root=repo_root, sessions=sessions, config=config)}
    snapshot = _snapshot(snapshot_path, prepare, metadata={"stage": "final", "probe_hash": inputs.digest(saved_probe),
                                                         "screening_hash": inputs.digest(screening), "limit": limit_decisions, "planned": planned})
    runner = runner or LlmRunner(data_dir=data_dir, repo_root=repo_root, config=config)
    decisions, _ = _run_snapshot(snapshot, runner, final=True, data_dir=data_dir, config=config)
    result = evaluate.metrics(_frame(snapshot), decisions)
    variants = result.pop("variants")
    trials = experiment_registry.trial_count(config.experiment_id, repo_root=repo_root) + len(VARIANTS)
    for arm in VARIANTS[:4]:
        verdict, reasons = evaluate.final_verdict(arm, variants[arm], fields, screening=screening["variants"].get(arm), trial_count=trials)
        variants[arm].update(verdict=verdict, reasons=reasons,
                             screening_fraction_applicable=arm.endswith("num"))
    variants["hc002_holdout"] = snapshot["hc002_holdout"]
    payload = _payload("final", variants, started, config=config, **result, decisions=decisions, probe=saved_probe,
                       exclusions=excluded + snapshot["exclusions"], snapshot_hash=inputs.digest(snapshot), prompt_hashes=runner.hashes,
                       source_reads=f"single {config.name} HoldoutSession; resume uses frozen source snapshot only")
    return _publish(payload, data_dir, repo_root, limit_decisions is not None, config)


def main(argv=None, *, default_experiment="LH001"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=tuple(EXPERIMENTS), default=default_experiment)
    parser.add_argument("stage", choices=("probe", "screening", "final", "status"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="default: metadata-only plan")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--limit-decisions", type=int, help="smoke only; never records registry rows")
    args = parser.parse_args(argv)
    config = EXPERIMENTS[args.experiment]
    if args.limit_decisions is not None and args.limit_decisions < 1:
        parser.error("--limit-decisions must be positive")
    started = time.perf_counter()
    try:
        if args.stage == "status":
            result = status(data_dir=args.data_dir, config=config)
        elif not args.execute:
            result = dry_run(args.stage, data_dir=args.data_dir, limit_decisions=args.limit_decisions, config=config)
        else:
            check_committed(config.prompt_dir, PROJECT_ROOT)
            result = {"probe": execute_probe, "screening": execute_screening, "final": execute_final}[args.stage](
                data_dir=args.data_dir, limit_decisions=args.limit_decisions, config=config)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (StageStopped, ValueError, OSError) as exc:
        if args.execute:
            state = "insufficient_clean_window" if "insufficient_clean_window" in str(exc) else "insufficient"
            if config.probe_version == "v2" and "probe_insensitive" in str(exc):
                state = "probe_insensitive"
            variants = {arm: _unmeasured(state, str(exc)) for arm in VARIANTS}
            payload = _payload(args.stage, variants, started, config=config, interruption=str(exc))
            # Preserve completed official stage state on interruption.
            evaluate.publish(payload, smoke=args.limit_decisions is not None, config=config)
        print(json.dumps({"stage": args.stage, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
