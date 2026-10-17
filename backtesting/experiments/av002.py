"""AV002: the single registered technical recovery of AV001's holdout."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import resource
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtesting import experiment_registry, holdout
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, common, hc003, ordering
from src.runner import trading_calendar


EXPERIMENT_ID = "AV002_avoidance_holdout_recovery"
PREREG_COMMIT = "5545e9a53479c68b4f5e10c59c316bf018111429"
VARIANTS = ("av_all", "av_conc")
STATE_SHA256 = "b8daf99f7f8e27d06ecd5425be4952f6b6ecd6ff8d8949cb538ab5e9b27577da"
PARENT_DIRECTORY = "research/experiments/AV001_avoidance_portfolio/results/"
PARENT_FILES = {
    PARENT_DIRECTORY + "20261017T115416329783Z.json.gz": "6c5e6e54779c305cee1dbb312e20a659d43db764f42541b68d5ac822dada16f9",
    PARENT_DIRECTORY + "20261017T125256426846Z.json": "2c6202d9f85f83e3df397ec01f38d5b3425d4c85c2bbc5c5e71d5ef631ad26ca",
    PARENT_DIRECTORY + "holdout_aborted_20261017.log": "b7f8cb8a9a095c59b242208ba1da470943141bc8ffbf6cebafda746c8d739437",
}
PARENT_RESULT, ABORT_RESULT, _ = PARENT_FILES
DESIGN_EFFECTS = {
    "S": {"av_all": 0.0031823698965281247, "av_conc": 0.0031122233147791323},
    "O": {"av_all": 0.003263526225324951, "av_conc": 0.003157741155254764},
}
INPUT_GROUPS = {"financials", "companies", "main_listing", "supplement", "coverage_metadata", "prices"}
RUNNER_SOURCE = "backtesting/experiments/av002.py"
INTERPRETATIONS = [
    "Technical recovery, second access to the same AV001 holdout; not a new independent validation.",
    "AV001's Holm(3) passing set, design-only effects and all-three-variant November states are inherited unchanged. No AV002 design trials or reselection.",
    "Rehearsal returns are execution checks only and never used for the holdout verdict.",
    "The aborted parent's input hashes are expected values, not proof that its inputs were verified. Protected bytes are verified only inside the AV002 session, at entry and completion.",
    "Both S and O must pass AV001 section 8 for a variant to pass. sensitivity_unavailable is a non-pass terminal status for AV002.",
]


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (registration.commit_hash != PREREG_COMMIT or registration.fields["variants_planned"] != 2
            or not registration.fields["uses_holdout"] or registration.fields["uses_llm"]):
        raise ValueError("AV002 requires the immutable 5545e9a preregistration and its two fixed variants")
    av001._registration(repo_root)
    return registration


def _computation():
    # Separate globals let every unchanged AV001 function use AV002's ID,
    # without mutating AV001 or changing its three-variant reproduction family.
    spec = importlib.util.spec_from_file_location("backtesting.experiments._av002_computation", av001.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EXPERIMENT_ID = EXPERIMENT_ID
    module._registration = av001._registration  # Parent criteria, never AV002 design rows.
    return module


computation = _computation()


def parent_link():
    return {"experiment_id": av001.EXPERIMENT_ID, "result_file": PARENT_RESULT,
            "result_sha256": PARENT_FILES[PARENT_RESULT], "state_sha256": STATE_SHA256,
            "passing_variants": list(VARIANTS), "sealed_files": PARENT_FILES}


def load_parent(repo_root=PROJECT_ROOT):
    root = Path(repo_root).resolve()
    for relative, expected in PARENT_FILES.items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root) or hc003._digest(path) != expected:
            raise ValueError("sealed AV001 parent file hash/path mismatch: " + relative)
    design = common.read_result_json(root / PARENT_RESULT)
    aborted = common.read_result_json(root / ABORT_RESULT)
    passed = [v for v in av001.VARIANTS if design["variants"][v]["verdict"] == "pass"]
    if (design["experiment_id"] != av001.EXPERIMENT_ID or design["stage"] != "design_validation"
            or passed != list(VARIANTS) or set(design["variants"]) != set(av001.VARIANTS)
            or design["state_decision_month"] != "2025-11" or design["state_valuation_date"] != "2025-12-30"
            or design["state_sha256"] != STATE_SHA256
            or hc003._content_hash(design["design_states"]) != STATE_SHA256
            or any(set(design["design_states"][r]) != set(av001.VARIANTS) for r in av001.RULES)
            or any(design["rules"][r][v]["verdict"] != "pass" or design["design_effects"][r][v] != DESIGN_EFFECTS[r][v]
                   for r in av001.RULES for v in VARIANTS)):
        raise ValueError("sealed AV001 passing set, effects or three-variant state differs from AV002 section 2")
    if (aborted["experiment_id"] != av001.EXPERIMENT_ID or aborted["stage"] != "holdout"
            or aborted["verdict"] != "holdout_aborted" or set(aborted["variants"]) != set(VARIANTS)
            or aborted["state_sha256"] != STATE_SHA256
            or aborted["input_hash_verification"] != "aborted; inputs not verified"):
        raise ValueError("sealed AV001 abort/input verification record differs")
    hashes = aborted["source_hashes"]
    if set(hashes) != INPUT_GROUPS | {"code"}:
        raise ValueError("AV001 abort input inventory is incomplete")
    for group, files in hashes.items():
        digest = hashlib.sha256(json.dumps(files, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if digest != aborted["abort"]["input_hashes"][group]:
            raise ValueError("AV001 abort input category digest differs: " + group)
        for relative, checksum in files.items():
            if (Path(relative).is_absolute() or ".." in Path(relative).parts or len(checksum) != 64
                    or any(c not in "0123456789abcdef" for c in checksum)):
                raise ValueError("invalid AV001 frozen input path/hash: " + relative)
    return design, aborted


def _check_expected(actual, expected):
    for group, files in actual.items():
        if group != "code" and files != expected[group]:
            raise ValueError("frozen AV001 input hash/inventory mismatch: " + group)


def reproduce(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    design, aborted = load_parent(repo_root)
    runner_sha256 = hc003._digest(Path(__file__))
    replay = computation.compute_design(data_dir=data_dir, repo_root=repo_root,
                                        trials=design["trial_count_after_run"])
    if runner_sha256 != hc003._digest(Path(__file__)):
        raise ValueError("AV002 runner code changed during reproduction; holdout unopened")
    e_hashes = {r: {} for r in av001.RULES}
    for rule in av001.RULES:
        for variant in av001.VARIANTS:
            actual = [(row["month"], row["valid"], row["excess"])
                      for row in replay["rules"][rule][variant]["per_month"]]
            expected = [(row["month"], row["valid"], row["excess"])
                        for row in design["rules"][rule][variant]["per_month"]]
            if actual != expected:
                raise ValueError(f"AV002 reproduction monthly e mismatch: {rule}/{variant}; holdout unopened")
            e_hashes[rule][variant] = hc003._content_hash(actual)
    if (replay["state_sha256"] != STATE_SHA256 or hc003._content_hash(replay["design_states"]) != STATE_SHA256
            or replay["comparison_states"] != design["comparison_states"]
            or replay["design_effects"] != design["design_effects"]):
        raise ValueError("AV002 reproduction state/effects mismatch; holdout unopened")
    _check_expected(replay["source_hashes"], design["source_hashes"])
    for group, files in replay["source_hashes"].items():
        if group != "code" and any(aborted["source_hashes"][group].get(p) != sha for p, sha in files.items()):
            raise ValueError("AV002 reproduction differs from abort input hashes: " + group)
    return {"experiment_id": EXPERIMENT_ID, "stage": "reproduction", "passed": True,
            "parent": parent_link(), "state_sha256": replay["state_sha256"], "state_matches_parent": True,
            "monthly_e_sha256": e_hashes,
            "months_compared": {r: {v: len(replay["rules"][r][v]["per_month"])
                                    for v in av001.VARIANTS} for r in av001.RULES},
            "performance": replay["performance"], "library_versions": library_versions(),
            "code_hashes": {**replay["source_hashes"]["code"], RUNNER_SOURCE: runner_sha256},
            "interpretations": INTERPRETATIONS}


def preflight(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    report = trading_calendar.preflight_price_sessions(data_dir, "2025-12-01", "2026-06-30")
    files, dates = {}, set()
    for path in sorted((Path(data_dir) / "market/krx_daily").glob("*/*.jsonl")):
        if len(path.stem) != 8 or not path.stem.isdigit() or not "20251201" <= path.stem <= "20260630":
            continue
        if path.stem in dates or path.parent.name != path.stem[:4] or not path.stat().st_size:
            raise ValueError("calendar_price_session_mismatch:duplicate/misplaced/empty price file: " + str(path))
        dates.add(path.stem)
        files[path.relative_to(data_dir).as_posix()] = path.stat().st_size
    return {"experiment_id": EXPERIMENT_ID, "stage": "preflight", "passed": True, **report,
            "price_file_sizes": files, "inventory_sha256": hc003._content_hash(files),
            "contents_read": False}


def available_memory_bytes():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable is unavailable; rehearsal memory cannot be checked")


def library_versions():
    return {"python": platform.python_version(),
            **{name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy", "exchange-calendars", "PyYAML")}}


def source_hashes(*args, **kwargs):
    hashes = computation.source_hashes(*args, **kwargs)
    hashes["code"][RUNNER_SOURCE] = hc003._digest(Path(__file__))
    return hashes


def _output_directory(repo_root, name):
    root = Path(repo_root).resolve()
    path = root / "research/experiments" / EXPERIMENT_ID / name
    if not path.resolve().is_relative_to(root / "research/experiments" / EXPERIMENT_ID):
        raise ValueError("AV002 output directory escapes the worktree experiment directory")
    return path


def _save(payload, directory):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = common.write_result_files(directory, stamp, json.dumps(payload, ensure_ascii=False, allow_nan=False),
                                     [f"# {EXPERIMENT_ID}: {payload['stage']}", "", *INTERPRETATIONS])
    hashes = {p.name: hc003._digest(p) for p in paths}
    manifest = directory / (stamp + ".sha256.json")
    manifest.write_text(json.dumps(hashes, indent=2) + "\n")
    return {"files": {str(p): sha for p, sha in ((directory / name, sha) for name, sha in hashes.items())},
            "hash_manifest": str(manifest), "hash_manifest_sha256": hc003._digest(manifest)}


def rehearse(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    load_parent(repo_root)
    available = available_memory_bytes()
    if available < 5_000_000_000:
        raise MemoryError(f"AV002 rehearsal requires >= 5 GB MemAvailable; available={available}")
    started = time.perf_counter()
    paths = [(day, path) for day, path in computation.input_price_paths(data_dir, "design_validation")
             if day <= pd.Timestamp("2025-06-30")]
    hashes = source_hashes(data_dir, paths, stage="design_validation", repo_root=repo_root)
    result = computation.rehearse_holdout(data_dir=data_dir, repo_root=repo_root)
    if hashes != source_hashes(data_dir, paths, stage="design_validation", repo_root=repo_root):
        raise ValueError("AV002 rehearsal inputs changed during computation")
    if any(result["rules"][r][v]["primary"]["count"] != 6 for r in av001.RULES for v in av001.VARIANTS):
        raise ValueError("AV002 rehearsal failed to produce all six observations for all variants/rules")
    result.update(passed=True, parent=parent_link(), source_hashes=hashes,
                  code_commit=experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
                  library_versions=library_versions(), interpretations=INTERPRETATIONS,
                  performance={"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024})
    outputs = _save(result, _output_directory(repo_root, "rehearsal"))
    return {"experiment_id": EXPERIMENT_ID, "stage": "holdout_rehearsal", "passed": True,
            "start": result["start"], "end": result["end"], "variants": result["variants"],
            "performance": result["performance"], "outputs": outputs, "interpretations": INTERPRETATIONS}


def frozen_inputs(aborted, *, data_dir, repo_root, before_open):
    expected = {k: v for k, v in aborted["source_hashes"].items() if k != "code"}
    actual = source_hashes(data_dir, computation.input_price_paths(data_dir, "holdout"),
        stage="holdout", repo_root=repo_root, frozen_prices=expected["prices"] if before_open else None)
    _check_expected(actual, expected)
    return actual


def code_manifest(*, repo_root, parent, run_id):
    root = Path(repo_root).resolve()
    changed = set(experiment_registry._git(root, "diff", "--name-only", parent["code_commit"], "--").splitlines())
    changed.update(experiment_registry._git(root, "ls-files", "--others", "--exclude-standard").splitlines())
    changed = sorted(p for p in changed if Path(p).suffix in (".py", ".sh") and not p.startswith("data/"))
    sources = sorted(set(parent["source_hashes"]["code"]) | set(changed) | {"backtesting/experiments/av002.py"})
    files = {}
    for relative in sources:
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("AV002 code manifest requires a regular source file: " + relative)
        files[relative] = hc003._digest(path)
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "code_change_manifest", "run_id": run_id,
               "parent": parent_link(), "parent_code_commit": parent["code_commit"],
               "code_commit": experiment_registry._git(root, "rev-parse", "HEAD"),
               "changed_code_files": {p: files[p] for p in changed}, "source_hashes": files}
    dirty = set(experiment_registry._git(root, "diff", "HEAD", "--name-only", "--", *sources).splitlines())
    dirty.update(experiment_registry._git(root, "ls-files", "--others", "--exclude-standard", "--", *sources).splitlines())
    payload["uncommitted_code_files"] = sorted(dirty)
    outputs = _save(payload, _output_directory(repo_root, "code_manifests"))
    path, checksum = next(iter(outputs["files"].items()))
    return {"file": str(Path(path).relative_to(root)), "sha256": checksum, "source_hashes": files,
            "code_commit": payload["code_commit"], "committed": not dirty}


def _verify_code(manifest, repo_root):
    root = Path(repo_root)
    if hc003._digest(root / manifest["file"]) != manifest["sha256"]:
        raise ValueError("AV002 sealed code manifest changed")
    if any(hc003._digest(root / p) != sha for p, sha in manifest["source_hashes"].items()):
        raise ValueError("AV002 code changed after the pre-open manifest")


def publish(payload, *, repo_root, registration, aborted=False):
    if (payload["experiment_id"] != EXPERIMENT_ID or payload["stage"] != "holdout"
            or set(payload["variants"]) != set(VARIANTS) or set(payload["rules"]) != set(av001.RULES)
            or any(v["verdict"] not in ordering.AV002_TERMINAL for v in payload["variants"].values())
            or any(set(payload["rules"][r]) != set(VARIANTS) for r in av001.RULES)):
        raise ValueError("AV002 publication requires exactly two matching holdout variants and both rules")
    _output_directory(repo_root, "results")
    metrics = computation.registry_metrics(payload, design_result_file=PARENT_RESULT)
    for row in metrics.values():
        row.update(parent=parent_link(), run_id=payload["run_id"], code_manifest=payload.get("code_manifest"))
        experiment_registry.encode_metrics(row)
    lines = [f"# {EXPERIMENT_ID}: holdout", "", f"Verdict: {payload['verdict']}", "",
             "| Variant | S | O | Combined |", "| --- | --- | --- | --- |"]
    for variant, result in payload["variants"].items():
        lines.append(f"| {variant} | {result['rule_verdicts']['S']} | {result['rule_verdicts']['O']} | {result['verdict']} |")
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root,
        summary_lines=[*lines, "", *INTERPRETATIONS],
        aborted_registration=registration if aborted else None)
    result_file, checksum = str(paths[0].relative_to(repo_root)), hc003._digest(paths[0])
    trials = []
    for variant, row in metrics.items():
        row.update(result_file=result_file, result_sha256=checksum)
        trials.append((variant, row, row["combined_verdict"]))
    experiment_registry.record_trials(EXPERIMENT_ID, trials, repo_root=repo_root,
        aborted_registration=registration if aborted else None,
        note="AV002 technical recovery, second access to AV001 holdout; no independent validation")
    return payload


def _unused(repo_root):
    used = hc003.ledger_experiments(repo_root)
    if (EXPERIMENT_ID in used or any(row["experiment_id"] == EXPERIMENT_ID for row in hc003.registry_rows(repo_root))):
        raise ValueError("AV002 holdout already used or terminally blocked; no reopening or AV003 recovery")
    return used


def run_holdout(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    with ordering.execution_lock(repo_root):
        registration = _registration(repo_root)  # Captured before any post-claim failure.
        design, aborted = load_parent(repo_root)
        used = _unused(repo_root)
        run_id = uuid.uuid4().hex
        if "LH002_llm_hegemony_judge" in used:
            payload = computation.terminal_payload("holdout_contaminated", "LH002 ledger entry; no protected prices read",
                VARIANTS, design=design, registration=registration)
            payload.update(parent=parent_link(), run_id=run_id, code_manifest=None, pre_open={},
                           source_hashes=aborted["source_hashes"], interpretations=INTERPRETATIONS,
                           code_commit=experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
                           library_versions=library_versions())
            return publish(payload, repo_root=repo_root, registration=registration)
        if used & {"EV001_earnings_filing_entry", "LH003_llm_leader_forward"}:
            raise ValueError("AV002 ordering violation: EV001/LH003 used the holdout first; no session opened")
        checks = {}
        for name, step in (("reproduction", reproduce), ("preflight", preflight), ("rehearsal", rehearse)):
            checks[name] = step(data_dir=data_dir, repo_root=repo_root)
            if checks[name].get("passed") is not True:
                raise ValueError("AV002 pre-open check did not pass: " + name)
        hashes = frozen_inputs(aborted, data_dir=data_dir, repo_root=repo_root, before_open=True)
        sessions = hc003.session_calendar()
        planned, excluded = hc003.rebalance_calendar(sessions, holdout_mode=True)
        if len(planned) != 6 or excluded:
            raise ValueError("AV002 requires the six registered holdout observations")
        _, coverage = computation.coverage_plan(data_dir, planned, sessions)
        if any(not row["listing"]["covered"] for row in coverage):
            raise ValueError("data_incomplete: AV002 holdout listing coverage; no session opened")
        manifest = code_manifest(repo_root=repo_root, parent=design, run_id=run_id)
        _verify_code(manifest, repo_root)
        if any(manifest["source_hashes"].get(path) != sha for path, sha in checks["reproduction"]["code_hashes"].items()):
            raise ValueError("AV002 code differs from the completed reproduction; holdout unopened")
        if not manifest["committed"]:
            raise ValueError("AV002 section 7 requires the implementation in a code commit before opening; manifest sealed, holdout unopened")
        # This is deliberately repeated immediately before the claim, under
        # the same lock as every successor's ordering check and holdout claim.
        used = _unused(repo_root)
        if "LH002_llm_hegemony_judge" in used:
            payload = computation.terminal_payload("holdout_contaminated", "LH002 ledger entry; no protected prices read",
                VARIANTS, design=design, registration=registration)
            payload.update(parent=parent_link(), run_id=run_id, code_manifest=manifest, pre_open=checks,
                           source_hashes=hashes, code_commit=manifest["code_commit"],
                           library_versions=library_versions(), interpretations=INTERPRETATIONS)
            return publish(payload, repo_root=repo_root, registration=registration)
        if used & {"EV001_earnings_filing_entry", "LH003_llm_leader_forward"}:
            raise ValueError("AV002 ordering violation: EV001/LH003 used the holdout first; no session opened")
        started = time.perf_counter()
        try:
            with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=repo_root):
                frozen_inputs(aborted, data_dir=data_dir, repo_root=repo_root, before_open=False)
                _verify_code(manifest, repo_root)
                if computation.coverage_plan(data_dir, planned, sessions)[1] != coverage:
                    raise ValueError("AV002 listing coverage changed after input fixing")
                prepared = computation.prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions,
                                               planned=planned, stage="holdout")
                rules, losses, variants = computation.evaluate_holdout(prepared, design, VARIANTS)
                frozen_inputs(aborted, data_dir=data_dir, repo_root=repo_root, before_open=False)
                _verify_code(manifest, repo_root)
                if computation.coverage_plan(data_dir, planned, sessions)[1] != coverage:
                    raise ValueError("AV002 listing coverage changed during computation")
            payload = {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "av_all",
                "prereg_commit": registration.commit_hash, "verdict": variants["av_all"]["verdict"],
                "variants": variants, "rules": rules, "delisting_sensitivity": losses,
                "design_effects": design["design_effects"], "state_sha256": STATE_SHA256,
                "L": prepared["dates"].L, "filing_rules": {r: prepared["versions"][r].report() for r in av001.RULES},
                "sensitivity_unavailable_reasons": computation.sensitivity_reasons(prepared["versions"]),
                "version_provenance": prepared["versions"]["S"].annotations,
                "date_rule_differences": computation._date_differences(prepared),
                "listing_version": prepared["listing_status"]["listing_version"], "coverage": coverage,
                "counts": {"by_rule": prepared["counts"], "rights_events": prepared["event_counts"], **prepared["book"].counts},
                "parent": parent_link(), "run_id": run_id, "pre_open": checks, "code_manifest": manifest,
                "source_hashes": hashes, "input_hash_verification": "non-code: abort_record == pre_open == session_before == session_after; code: sealed manifest verified",
                "code_commit": manifest["code_commit"], "library_versions": library_versions(),
                "interpretations": INTERPRETATIONS,
                "performance": {"runtime_seconds": time.perf_counter() - started,
                                "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
            return publish(payload, repo_root=repo_root, registration=registration)
        except BaseException as exc:
            if EXPERIMENT_ID not in hc003.ledger_experiments(repo_root):
                raise
            payload = computation.terminal_payload("holdout_aborted", f"{type(exc).__name__}: {exc}", VARIANTS,
                                                   design=design, registration=registration)
            payload.update(parent=parent_link(), run_id=run_id, code_manifest=manifest, pre_open=checks,
                           source_hashes=hashes, input_hash_verification="aborted; inputs not verified",
                           code_commit=manifest["code_commit"], library_versions=library_versions(),
                           interpretations=INTERPRETATIONS)
            payload["abort"] = {**ordering.abort_details(hashes, exc), "run_id": run_id}
            publish(payload, repo_root=repo_root, registration=registration, aborted=True)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    for name in ("dry-run", "preflight", "reproduce", "rehearse", "holdout"):
        modes.add_argument("--" + name, action="store_true")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.holdout:
        result = run_holdout(data_dir=args.data_dir)
        result = {"experiment_id": EXPERIMENT_ID, "stage": result["stage"], "verdict": result["verdict"],
                  "variants": result["variants"], "interpretations": INTERPRETATIONS}
    elif args.reproduce:
        result = reproduce(data_dir=args.data_dir)
    elif args.rehearse:
        result = rehearse(data_dir=args.data_dir)
    else:
        result = preflight(data_dir=args.data_dir)
        if not args.preflight:
            load_parent()
            result.update(stage="dry_run", parent=parent_link(), interpretations=INTERPRETATIONS)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
