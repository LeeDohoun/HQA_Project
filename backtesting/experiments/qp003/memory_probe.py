"""One-process real selection, synthetic-price accounting, save and recovery RSS."""
from contextlib import ExitStack
import gc
import os
from pathlib import Path
import resource
import sys
import tempfile
import time
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtesting.experiments import hc003
from . import EXPERIMENT_ID, M, RULES, VARIANT
from . import attempts, runner
from .selection import permission_summary


def synthetic_prices(root, planned, selections):
    """Synthetic quotes for every real universe name; never load forward prices."""
    codes = sorted(set().union(*(set(s["universe"].index) for rows in selections.values() for s in rows)))
    book = object.__new__(hc003.PriceBook)
    book.experiment_id, book.repo_root, book.end = EXPERIMENT_ID, root, pd.Timestamp(planned[-1]["end_date"])
    book.openings, book.zero_from = {}, {}
    rng, marks = np.random.default_rng(43), np.ones(len(codes))
    for number, row in enumerate(planned):
        marks *= np.exp(rng.normal(.004, .05, len(codes)))
        book.openings[pd.Timestamp(row["trade_date"])] = {
            code: hc003.Quote(float(mark), 10000., "KOSPI" if i % 2 else "KOSDAQ", 2e9,
                i % 149 != number % 149, i % 251 == number % 251, i % 257 == number % 257)
            for i, (code, mark) in enumerate(zip(codes, marks))}
    book.closings = dict(zip(codes, (marks * np.exp(rng.normal(.004, .05, len(codes)))).tolist()))
    book.zero_from = {codes[i]: pd.Timestamp(planned[40 + i]["trade_date"]) for i in range(min(10, len(codes)))}
    return book


def measure(*, data_dir, repo_root):
    started = time.perf_counter()
    stages = {}
    def checkpoint(name):
        stages[name] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        print(f"QP003 mixed memory probe: {name}; peak RSS {stages[name]:,} bytes",
              file=sys.stderr, flush=True)
        if stages[name] >= 1_400_000_000:
            raise MemoryError("QP003 mixed probe exceeded the 1.4 GB RSS budget")

    specification = runner.input_spec(data_dir, repo_root)
    registration = runner.experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    registry_seal = runner.seal_registry(repo_root)
    code_commit = runner.experiment_registry._git(Path(repo_root), "rev-parse", "HEAD")
    sessions, planned = runner.qp001.calendar()
    assets = runner.qp001.AssetArchive(data_dir)
    links = runner.qp001.link_coverage(data_dir, assets)
    if assets.pending:
        raise ValueError("raw archive has pending derived files; wait for the collector snapshot")
    # The same call as formal execution: retain full monthly frames and provenance.
    select_month, selected_count = runner.qp001.select_month, 0
    def logged_selection(*args, **kwargs):
        nonlocal selected_count
        selected = select_month(*args, **kwargs)
        selected_count += 1
        if selected_count % 12 == 0 or selected_count == 206:
            checkpoint(f"real_selection_{selected_count}_of_206")
        return selected
    with patch.object(runner.qp001, "select_month", logged_selection):
        prepared = runner.prepare_selections(data_dir, repo_root, sessions, planned, assets=assets,
                                             source_hashes=specification)
    del assets
    gc.collect()
    checkpoint("real_selections_retained")
    if any(len(rows) != 103 for rows in prepared["selections"].values()):
        raise ValueError("mixed probe requires all 103 real selections per rule")
    if runner.input_spec(data_dir, repo_root) != specification:
        raise ValueError("real selection input snapshot changed")

    with tempfile.TemporaryDirectory(prefix="qp003-mixed-memory-") as directory:
        root = Path(directory)
        (root / attempts.DIRECTORY.parent).mkdir(parents=True)
        output = root / "result.json"
        spec_hash = runner.qp001.content_hash(specification)
        # This is explicitly a synthetic-price probe, never a formal precheck/result.
        precheck = {"mode": "memory-probe", "synthetic_prices_only": True, "input_spec_sha256": spec_hash}
        start = attempts.create(root, registration=registration, specification=specification,
            input_spec_sha256=spec_hash, registry_seal=registry_seal, precheck=precheck,
            code_commit=code_commit, result_file=output)
        book = synthetic_prices(root, planned, prepared["selections"])
        checkpoint("synthetic_prices_with_real_selections")
        evaluation = runner.evaluate(book, prepared, repo_root=root, details_dir=root / "controls")
        for section in ("rules", "vanished"):
            for rule in RULES:
                for name in ("random_control", "independent_redraw"):
                    control = evaluation[section][rule][name]
                    if control["paths"] != 200 or control["details"]["state_rows"] != 200:
                        raise AssertionError("mixed probe requires 200 paths in every control scenario")
        checkpoint("all_eight_200_path_scenarios_retained")
        monthly = {r: [s["precheck"] for s in prepared["selections"][r]] for r in RULES}
        payload = {"experiment_id": EXPERIMENT_ID, "variant": VARIANT, "stage": "design_validation",
            "synthetic_prices_only": True, "run_id": start["run_id"], "start_record": attempts.reference(root, start),
            "prereg_commit": registration.commit_hash, "code_commit": code_commit, "registry_seal": registry_seal,
            "M": M, "precheck": precheck,
            "qp001_precheck": {"file": runner.QP001_PRECHECK, "sha256": runner.QP001_PRECHECK_SHA256},
            "input_spec": specification, "input_spec_sha256": spec_hash, **evaluation,
            "selections": runner.selection_details(prepared), "monthly_precheck": monthly,
            "monthly_summary": {r: permission_summary(monthly[r]) for r in RULES},
            "link_coverage": links, "filing_rules": prepared["filing_rules"], "L": prepared["L"],
            "sensitivity_by_stage": prepared["sensitivity_by_stage"]}
        runner.saved.write(output, payload)
        checkpoint("fsynced_result_with_real_selections")
        saved_hash = runner.digest(output)

        # Only preregistration lookup uses the real checkout's captured plan.
        # All actual save/recovery/registry operations target the temporary tree.
        def isolated_registration(experiment_id, repo_root):
            if experiment_id != EXPERIMENT_ID or Path(repo_root).resolve() != root:
                raise AssertionError("probe registration escaped its temporary repository")
            return registration
        def forbidden(*a, **k):
            raise AssertionError("save-only recovery read inputs or recomputed")
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner.experiment_registry, "verify_preregistration", isolated_registration))
            for module, names in ((runner, ("compute", "input_spec", "prepare_selections", "verify_precheck")),
                                  (runner.qp001, ("price_book", "load_history", "AssetArchive"))):
                for name in names:
                    stack.enter_context(patch.object(module, name, forbidden))
            recovered = runner.recover_attempt(start["run_id"], repo_root=root)
            checkpoint("save_only_recovery_with_real_selections")
            again = runner.recover_attempt(start["run_id"], repo_root=root)
            checkpoint("idempotent_recovery_with_real_selections")
        if (not recovered["registry_appended"] or again["registry_appended"]
                or runner.digest(output) != saved_hash or recovered["recomputed"]):
            raise AssertionError("synthetic-price save/recovery contract failed")
        snapshot_unchanged = runner.input_spec(data_dir, repo_root) == specification
        if not snapshot_unchanged:
            raise ValueError("real input snapshot changed during the mixed probe")
        checkpoint("finished")
        return {"mode": "memory-probe", "status": "ok", "single_process": True, "process_id": os.getpid(),
            "real_selections": True, "monthly_selection_details_retained": True,
            "real_forward_prices_used": False, "real_performance_computed": False, "synthetic_prices_only": True,
            "months": 103, "rules": list(RULES), "stocks": len(book.closings), "paths": 200,
            "control_scenarios": 8, "paired_cost_scenarios": 16,
            "hold_months": {r: sum(not s["rebalance_permitted"] for s in rows)
                            for r, rows in prepared["selections"].items()},
            "monthly_roa_details": {r: sum(len(s["roa_provenance"]) for s in rows)
                                    for r, rows in prepared["selections"].items()},
            "stage_peak_rss_bytes": stages, "peak_rss_bytes": max(stages.values()), "rss_limit_bytes": 1_400_000_000,
            "address_space_limit_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
            "streamed_control_detail_bytes": sum(p.stat().st_size for p in (root / "controls").glob("*.jsonl")),
            "result_bytes": output.stat().st_size, "recovery_receipt_bytes": runner.saved.receipt_path(output).stat().st_size,
            "recovery": recovered, "idempotent_recovery": again,
            "temporary_registry_rows": len(hc003.registry_rows(root)), "input_spec_sha256": spec_hash,
            "source_snapshot_unchanged": snapshot_unchanged, "elapsed_seconds": time.perf_counter() - started}
