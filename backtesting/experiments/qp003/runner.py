"""Offline QP003 runner. Real returns require an explicit --execute."""
from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import sys

import pandas as pd

from backtesting import experiment_registry
from backtesting.experiments import av001, hc003, ordering
from backtesting.experiments.qp001 import artifacts, published
from backtesting.experiments.qp001 import runner as qp001
from backtesting.experiments.qp001.artifacts import digest
from . import DESIGN_END, DESIGN_START, EXPERIMENT_ID, M, RULES, VALIDATION_END, VARIANT
from . import attempts, judge, portfolio, saved
from .selection import apply_permission, permission_summary

ROOT = Path(__file__).resolve().parents[3]
QP001_PRECHECK = "backtesting/experiments/qp001/precheck_20261020.json"
QP001_PRECHECK_SHA256 = "91660fbb1da3df1fab41d7fcf5aa2e840427257b46ea237af7e7c378ac3ea88b"


def seal_registry(repo_root):
    return {**qp001.seal_registry(repo_root), "M": M, "formula": "82 + 84 + 1 + 1 = 168"}


def input_spec(data_dir, repo_root):
    """Complete inherited data/code inventory plus QP003 sources and lineage."""
    root, data = Path(repo_root), Path(data_dir)
    lineage = root / QP001_PRECHECK
    if digest(lineage) != QP001_PRECHECK_SHA256:
        raise ValueError("QP001 precheck SHA-256 differs from the committed lineage")
    with lineage.open(encoding="utf-8") as handle:
        original = json.load(handle)
    # Losing an entire source file is an error, never a lower-coverage month.
    missing = [name for name in original["source_hashes"]["data"] if not (data / name).is_file()]
    if missing:
        raise ValueError(f"missing QP001 source files: {missing[:5]}")
    del original
    specification = qp001.input_hashes(data, root)
    extra = list((root / "backtesting/experiments/qp003").glob("*.py"))
    extra.append(root / f"research/experiments/{EXPERIMENT_ID}/preregistration.md")
    specification["code"].update({p.relative_to(root).as_posix(): digest(p) for p in sorted(extra)})
    specification["lineage"] = {QP001_PRECHECK: QP001_PRECHECK_SHA256}
    return specification


def prepare_selections(data_dir, repo_root, sessions, planned, *, assets, precheck_only=False, source_hashes=None):
    prepared = qp001.prepare_selections(data_dir, repo_root, sessions, planned, assets=assets,
                                       precheck_only=precheck_only, source_hashes=source_hashes)
    prepared["selections"] = {rule: [apply_permission(s) for s in prepared["selections"][rule]] for rule in RULES}
    return prepared


def precheck(*, data_dir, repo_root=ROOT, sessions=None):
    specification = input_spec(data_dir, repo_root)
    assets = qp001.AssetArchive(data_dir)
    links = qp001.link_coverage(data_dir, assets)
    if assets.pending:
        raise ValueError("raw archive has pending derived files; wait for the collector snapshot")
    sessions, planned = qp001.calendar(sessions)
    prepared = prepare_selections(data_dir, repo_root, sessions, planned, assets=assets,
                                  precheck_only=True, source_hashes=specification)
    monthly = {rule: [s["precheck"] for s in prepared["selections"][rule]] for rule in RULES}
    unchanged = input_spec(data_dir, repo_root) == specification
    return {"experiment_id": EXPERIMENT_ID, "mode": "precheck", "status": "ok" if unchanged else "incomplete",
            "returns_computed": False, "forward_prices_used": False, "trailing_volatility_computed": True,
            "volatility_policy": "QP001 HF001 60 verified decision-inclusive daily log returns; no forward prices",
            "M": M, "registry_seal": seal_registry(repo_root),
            "qp001_precheck": {"file": QP001_PRECHECK, "sha256": QP001_PRECHECK_SHA256},
            "input_spec": specification, "input_spec_sha256": qp001.content_hash(specification),
            "source_snapshot_unchanged": unchanged, "link_coverage": links,
            "monthly": monthly, "monthly_summary": {r: permission_summary(monthly[r]) for r in RULES},
            "fs_div_fallback_counts": {r: sum(s["fs_div"].get("OFS", 0) for s in monthly[r]) for r in RULES},
            "filing_rules": prepared["filing_rules"], "L": prepared["L"],
            "sensitivity_available": prepared["sensitivity_available"],
            "sensitivity_by_stage": prepared["sensitivity_by_stage"],
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}


def verify_precheck(path, specification):
    with Path(path).open(encoding="utf-8") as handle:
        saved = json.load(handle)
    if (saved["experiment_id"] != EXPERIMENT_ID or saved["mode"] != "precheck" or saved["status"] != "ok"
            or saved["returns_computed"] is not False or saved["forward_prices_used"] is not False
            or saved["source_snapshot_unchanged"] is not True or saved["M"] != M
            or saved["qp001_precheck"] != {"file": QP001_PRECHECK, "sha256": QP001_PRECHECK_SHA256}):
        raise ValueError("formal execution requires a successful return-free QP003 precheck")
    if saved["input_spec"] != specification or saved["input_spec_sha256"] != qp001.content_hash(specification):
        raise ValueError("QP003 input spec differs from precheck; stopped before performance computation")
    return {"sha256": digest(path), "input_spec_sha256": saved["input_spec_sha256"]}


def reproduce_av001(*, data_dir, repo_root=ROOT):
    path = Path(repo_root) / qp001.AV_RESULT
    if digest(path) != qp001.AV_RESULT_SHA256:
        raise ValueError("published AV001 result hash differs")
    saved = published.read_av_window(path)
    sessions = qp001.trading_calendar.session_calendar("2015-01-01", "2025-12-30")
    differences = {}
    for rule in RULES:
        planned = []
        for row in saved["selections"][rule]:
            if not "2017-05" <= row["month"] <= "2017-07":
                raise ValueError("AV replay only permits the first three published design months")
            codes, key = set(row["U"]), ("AV001", False, 0)
            planned.append({**row, "universe": pd.DataFrame({"phase": 1}, index=sorted(codes)),
                "published_targets": set(row["H"]["av_all"]), "groups": {key: codes}, "weights": {key: 1.0},
                "rebalance_permitted": True, "decision_status": "permitted"})
        first = sessions.get_loc(pd.Timestamp(planned[0]["trade_date"]))
        for day in sessions[max(0, first - 20):sessions.get_loc(pd.Timestamp(planned[-1]["end_date"])) + 1]:
            price = Path(data_dir) / "market/krx_daily" / str(day.year) / day.strftime("%Y%m%d.jsonl")
            if digest(price) != saved["prices"].get(price.relative_to(data_dir).as_posix()):
                raise ValueError("AV001 replay price snapshot differs from the published input hash")
        book = qp001.price_book(data_dir, repo_root, sessions, planned, experiment_id=av001.EXPERIMENT_ID)
        actual = portfolio.simulate(book, planned, experiment_id=av001.EXPERIMENT_ID, variant="av_all",
                                    targets_fn=lambda variant, selection, held: selection["published_targets"])
        expected = saved["observations"][rule]
        deltas = [abs(a["excess"] - b["excess"]) for a, b in zip(actual["per_month"], expected)]
        if len(expected) != 3 or len(deltas) != 3 or max(deltas) > 1e-12:
            raise ValueError(f"QP003 accounting does not reproduce published AV001 {rule}: {deltas}")
        if any(r["group_weights"] != {str(("AV001", False, 0)): 1.0} or not r["rebalance_permitted"]
               for r in actual["per_month"]):
            raise ValueError("AV001 replay must traverse QP003's permitted group accounting path")
        differences[rule] = {"months": [r["month"] for r in planned], "hold_months": [],
                             "max_abs_monthly_e_difference": max(deltas)}
    return {"mode": "reproduce-av001", "status": "ok", "accounting_branch": "qp003_hold_aware_group_weights_alpha_1",
            "published_result_sha256": qp001.AV_RESULT_SHA256, "rules": differences,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}


def metrics(runs, controls, selections):
    result = qp001.metrics(runs, controls, selections)
    result.update(design_months=[r["month"] for r in runs["v2_1.0"]["strategy"]["per_month"] if r["valid"] and r["month"] <= DESIGN_END],
        permitted_design_decisions=sum(s["rebalance_permitted"] for s in selections if s["month"] <= DESIGN_END),
        evaluation_complete=all([r["month"] for r in run[side]["per_month"] if r["valid"]] == [s["month"] for s in selections]
            for run in runs.values() for side in ("baseline", "strategy")))
    return result


def evaluate(book, prepared, *, repo_root, details_dir):
    """Shared accounting path for formal prices and the synthetic-price RSS probe."""
    rules, losses, main, loss = {}, {}, {}, {}
    for rule in RULES:
        selections = prepared["selections"][rule]
        for vanished, destination, statistics in ((False, rules, main), (True, losses, loss)):
            runs = portfolio.paired_scenarios(book, selections, loss=vanished)
            actual, baseline = (runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline"))
            prefix = rule + ("_vanished" if vanished else "_main")
            controls = portfolio.controls(book, selections, actual, baseline, loss=vanished,
                details_path=details_dir / (prefix + "_matched.jsonl"))
            diagnostic = portfolio.controls(book, selections, actual, baseline, loss=vanished, independent=True,
                details_path=details_dir / (prefix + "_independent.jsonl"))
            for control in (controls, diagnostic):
                control["details"]["file"] = str(Path(control["details"]["file"]).relative_to(repo_root))
            statistics[rule] = metrics(runs, controls, selections)
            dated = pd.Series(actual, index=[r["decision_date"] for r in actual], dtype=object)
            destination[rule] = {"scenarios": runs, "random_control": controls, "independent_redraw": diagnostic,
                "metrics": statistics[rule],
                "scenario_summaries": {name: {stage: portfolio.summary([r for r in run["strategy"]["per_month"]
                    if stage == "overall" or (r["month"] <= DESIGN_END) == (stage == "design")])
                    for stage in ("overall", "design", "validation")}
                    for name, run in runs.items()},
                "by_year": {year: portfolio.summary([r for r in actual if r["month"].startswith(year)])
                    for year in sorted({r["month"][:4] for r in actual})},
                "short_sale_regimes": {name: portfolio.summary(group.tolist())
                    for name, group in hc003.market_regimes.split_by_regime(dated).items()},
                "groups": portfolio.subgroup_diagnostics(book, selections, actual, baseline, loss=vanished),
                "availability_contribution": {status: {"months": sum(r["rebalance_permitted"] == permitted for r in actual),
                    "sum_e": sum(r["excess"] for r in actual if r["rebalance_permitted"] == permitted and r["excess"] is not None),
                    "mean_e": portfolio.full_mean([r["excess"] for r in actual if r["rebalance_permitted"] == permitted])}
                    for status, permitted in (("permitted", True), ("hold", False))}}
            print(f"QP003 accounting: {prefix} complete; peak RSS "
                  f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024:,} bytes",
                  file=sys.stderr, flush=True)
    judgement = judge.judge(main, loss, sensitivity_available=prepared["sensitivity_available"])
    return {"rules": rules, "vanished": losses, "judgement": judgement}


def selection_details(prepared):
    return {r: [{"month": s["month"], "A": s["A"], "C": s["common"].index.tolist(),
        "rebalance_permitted": s["rebalance_permitted"], "decision_status": s["decision_status"],
        "groups": [{"key": list(key), "members": sorted(codes), "selected": sorted(s["selected"][key]),
            "alpha": s["weights"][key]} for key, codes in s["groups"].items()],
        "market_cap_boundaries": s["boundaries"], "roa": s["roa_provenance"]}
        for s in prepared["selections"][r]] for r in RULES}


def compute(*, data_dir, repo_root, attempt, details_dir, precheck_path):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    fields = registration.fields
    if fields["variants_planned"] != 1 or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("QP003 requires one registered variant, no holdout and no LLM")
    registry_seal = seal_registry(repo_root)
    specification = input_spec(data_dir, repo_root)
    precheck_seal = verify_precheck(precheck_path, specification)
    precheck_seal["file"] = Path(precheck_path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    code_commit = experiment_registry._git(Path(repo_root), "rev-parse", "HEAD")
    attempt.update(attempts.create(repo_root, registration=registration, specification=specification,
        input_spec_sha256=qp001.content_hash(specification), registry_seal=registry_seal,
        precheck=precheck_seal, code_commit=code_commit, result_file=attempt["result_file"]))
    sessions, planned = qp001.calendar()
    assets = qp001.AssetArchive(data_dir)
    links = qp001.link_coverage(data_dir, assets)
    if assets.pending:
        raise ValueError("raw archive has pending derived files; wait for the collector snapshot")
    prepared = prepare_selections(data_dir, repo_root, sessions, planned, assets=assets, source_hashes=specification)
    del assets
    gc.collect()
    if input_spec(data_dir, repo_root) != specification:
        raise ValueError("QP003 source snapshot changed before performance computation")
    book = qp001.price_book(data_dir, repo_root, sessions, planned, experiment_id=EXPERIMENT_ID)
    evaluation = evaluate(book, prepared, repo_root=repo_root, details_dir=details_dir)
    if input_spec(data_dir, repo_root) != specification:
        raise ValueError("QP003 source snapshot changed during computation; no result may be published")
    if digest(precheck_path) != precheck_seal["sha256"]:
        raise ValueError("QP003 precheck changed during computation")
    return {"experiment_id": EXPERIMENT_ID, "variant": VARIANT, "stage": "design_validation",
        "run_id": attempt["run_id"], "start_record": attempts.reference(repo_root, attempt),
        "prereg_commit": registration.commit_hash, "code_commit": code_commit,
        "registry_seal": registry_seal, "M": M, **evaluation,
        "qp001_precheck": {"file": QP001_PRECHECK, "sha256": QP001_PRECHECK_SHA256}, "precheck": precheck_seal,
        "input_spec": specification, "input_spec_sha256": qp001.content_hash(specification),
        "library_versions": {"python": platform.python_version(),
            **{name: importlib.metadata.version(name) for name in ("numpy", "pandas", "exchange-calendars", "PyYAML")}},
        "link_coverage": links,
        "monthly_precheck": {r: [s["precheck"] for s in prepared["selections"][r]] for r in RULES},
        "monthly_summary": {r: permission_summary([s["precheck"] for s in prepared["selections"][r]]) for r in RULES},
        "selections": selection_details(prepared),
        "filing_rules": prepared["filing_rules"], "L": prepared["L"], "sensitivity_by_stage": prepared["sensitivity_by_stage"],
        "decision_periods": {"design": [DESIGN_START, DESIGN_END], "validation": ["2024-01", VALIDATION_END]},
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "state_policy": "Continuous per-rule/per-cost paths from cash 1; holds preserve units/cash, valuation and monthly e."}


def registry_metrics(payload):
    return {"stage": "design_validation", "run_id": payload["run_id"], "M": M,
        "start_record": payload["start_record"],
        "registry_seal": payload["registry_seal"], "qp001_precheck": payload["qp001_precheck"],
        "precheck": payload["precheck"], "input_spec_sha256": payload["input_spec_sha256"],
        "judgement": payload["judgement"],
        "rules": {r: {"design": payload["rules"][r]["metrics"]["design"],
            "design_at_cost_1_5": payload["rules"][r]["metrics"]["design_at_cost_1_5"],
            "validation_mean": payload["rules"][r]["metrics"]["validation"]["primary"]["mean"],
            "random_control_share": payload["rules"][r]["metrics"]["random_control_share"],
            "availability": payload["monthly_summary"][r]} for r in RULES}}


def register_saved(payload, output, repo_root):
    """QP001's idempotent saved-result registration contract, scoped to QP003."""
    start = attempts.load(repo_root, payload["run_id"])
    attempts.verify_result(repo_root, start, payload, output)
    relative = Path(output).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    receipt = saved.receipt_path(output)
    if saved.read(output) != saved.metadata(payload):
        raise ValueError("result metadata differs from its durable recovery receipt")
    metrics = {**registry_metrics(payload), "result_file": relative, "result_sha256": digest(output),
               "recovery_file": receipt.resolve().relative_to(Path(repo_root).resolve()).as_posix(),
               "recovery_sha256": digest(receipt)}
    experiment_registry.encode_metrics(metrics)
    for row in hc003.registry_rows(repo_root):
        if row["experiment_id"] == EXPERIMENT_ID and row["variant"] == VARIANT:
            recorded = json.loads(row["metrics_json"])
            if recorded.get("run_id") == payload["run_id"] or recorded.get("result_file") == relative:
                if (recorded != metrics or row["verdict"] != payload["judgement"]["verdict"]
                        or row["prereg_commit"] != payload["prereg_commit"]):
                    raise ValueError("saved result differs from its existing registry row")
                return False
    experiment_registry.record_trial(EXPERIMENT_ID, VARIANT, metrics, payload["judgement"]["verdict"], repo_root=repo_root,
        note="QP003 coverage holds; full months, both date rules and -100% recomputation; candidate only")
    return True


def execute(*, data_dir, output, precheck_path, repo_root=ROOT):
    output = Path(output).resolve()
    with ordering.execution_lock(repo_root):
        attempts.refuse_pending(repo_root)
        if output.exists():
            raise ValueError("QP003 result output already exists")
        attempt = {"result_file": output.as_posix()}
        reserve = bytearray(8 * 1024 * 1024)
        try:
            payload = compute(data_dir=data_dir, repo_root=repo_root, attempt=attempt,
                details_dir=output.with_suffix(".controls"), precheck_path=precheck_path)
            experiment_registry.encode_metrics(registry_metrics(payload))
            saved.write(output, payload)
            register_saved(payload, output, repo_root)
            return payload
        except BaseException as exc:
            del reserve
            gc.collect()
            # A complete published result must only be registered, never recomputed.
            if "run_id" in attempt and not output.exists():
                attempts.abort(repo_root, attempt, f"{type(exc).__name__}: {exc}")
            raise


def recover_registry(output, *, repo_root=ROOT):
    """Verify the start and durable artifacts, then register without recomputation."""
    with ordering.execution_lock(repo_root):
        return _recover_registry(output, repo_root=repo_root)


def _recover_registry(output, *, repo_root, run_id=None):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    payload = saved.read(output)
    if run_id is not None and payload["run_id"] != run_id:
        raise ValueError("saved result run_id differs from the requested QP003 attempt")
    if (payload["experiment_id"] != EXPERIMENT_ID or payload["variant"] != VARIANT
            or payload["stage"] != "design_validation" or payload["prereg_commit"] != registration.commit_hash
            or payload["M"] != M
            or payload["qp001_precheck"] != {"file": QP001_PRECHECK, "sha256": QP001_PRECHECK_SHA256}):
        raise ValueError("recovery requires a complete saved QP003 result under its original preregistration")
    root = Path(repo_root).resolve()
    start = attempts.load(root, payload["run_id"])
    attempts.verify_result(root, start, payload, output)
    for section in ("rules", "vanished"):
        for rule in RULES:
            for name in ("random_control", "independent_redraw"):
                detail = payload[section][rule][name]["details"]
                path = (root / detail["file"]).resolve()
                if not path.is_relative_to(root) or digest(path) != detail["sha256"]:
                    raise ValueError("saved control detail hash/path differs")
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
                artifacts.fsync_directory(path.parent)
    # Also finish durability if killed after atomic publication but before dir fsync.
    with Path(output).open("rb") as handle:
        os.fsync(handle.fileno())
    with saved.receipt_path(output).open("rb") as handle:
        os.fsync(handle.fileno())
    artifacts.fsync_directory(Path(output).parent)
    appended = register_saved(payload, output, root)
    return {"mode": "recover-registry", "status": "ok", "registry_appended": appended,
            "result_sha256": digest(output), "run_id": payload["run_id"], "recomputed": False}


def recover_attempt(run_id, *, repo_root=ROOT, abort=False):
    with ordering.execution_lock(repo_root):
        start = attempts.load(repo_root, run_id)
        output = (Path(repo_root) / start["result_file"]).resolve()
        if not output.is_relative_to(Path(repo_root).resolve()):
            raise ValueError("QP003 attempt result path escapes repo_root")
        if not abort and output.is_file():
            return _recover_registry(output, repo_root=repo_root, run_id=run_id)
        appended = attempts.abort(repo_root, start, "Interrupted QP003 start; recovery recorded design_aborted")
        return {"mode": "abort-attempt", "status": "ok", "run_id": run_id,
                "registry_appended": appended, "verdict": "design_aborted", "recomputed": False}


def safe_path(path, data_dir, *, new=False):
    resolved = Path(path).resolve()
    if (not resolved.is_relative_to(ROOT) or resolved.is_relative_to(Path(data_dir).resolve())
            or resolved.is_relative_to((ROOT / "data").resolve()) or Path(path).is_symlink()
            or any(part.startswith(".env") for part in resolved.parts) or (new and resolved.exists())):
        raise ValueError("artifacts must stay inside this worktree, outside data, and use new output files")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--precheck", action="store_true")
    modes.add_argument("--reproduce-av001", action="store_true")
    modes.add_argument("--memory-probe", action="store_true")
    modes.add_argument("--recover-registry", type=Path, metavar="SAVED_RESULT")
    modes.add_argument("--recover-attempt", metavar="RUN_ID", help="Register its stored result, or abort if none exists")
    modes.add_argument("--abort-attempt", metavar="RUN_ID", help="Record design_aborted without computation")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--precheck-file", type=Path, help="Sealed QP003 precheck; required for --execute")
    args = parser.parse_args(argv)
    if (args.reproduce_av001 or args.recover_registry or args.recover_attempt or args.abort_attempt) and args.output:
        parser.error("reproduction and registry recovery do not accept --output")
    if args.execute and (not args.output or not args.precheck_file):
        parser.error("--execute requires --output and --precheck-file")
    if args.precheck_file and not args.execute:
        parser.error("--precheck-file is only accepted with --execute")
    try:
        if args.output:
            args.output = safe_path(args.output, args.data_dir, new=True)
        if args.precheck_file:
            args.precheck_file = safe_path(args.precheck_file, args.data_dir)
        if args.recover_registry:
            args.recover_registry = safe_path(args.recover_registry, args.data_dir)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        cap = min(6_000_000_000, hard) if hard != resource.RLIM_INFINITY else 6_000_000_000
        resource.setrlimit(resource.RLIMIT_AS, (min(soft, cap) if soft != resource.RLIM_INFINITY else cap, hard))
        if args.precheck:
            payload = precheck(data_dir=args.data_dir)
        elif args.reproduce_av001:
            payload = reproduce_av001(data_dir=args.data_dir)
        elif args.recover_registry:
            payload = recover_registry(args.recover_registry)
        elif args.recover_attempt or args.abort_attempt:
            payload = recover_attempt(args.recover_attempt or args.abort_attempt, abort=bool(args.abort_attempt))
        elif args.memory_probe:
            from .memory_probe import measure
            payload = measure(data_dir=args.data_dir, repo_root=ROOT)
        else:
            payload = execute(data_dir=args.data_dir, output=args.output, precheck_path=args.precheck_file)
        if args.output:
            if not args.execute:
                artifacts.write_json(args.output, payload)
            print(json.dumps({"file": str(args.output), "sha256": digest(args.output),
                              "status": payload.get("status", payload.get("judgement", {}).get("verdict"))}))
        else:
            json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True, allow_nan=False)
            print()
        return 2 if args.precheck and payload["status"] != "ok" else 0
    except (ValueError, TypeError, KeyError, OSError, MemoryError) as exc:
        parser.exit(2, f"QP003 stopped: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
