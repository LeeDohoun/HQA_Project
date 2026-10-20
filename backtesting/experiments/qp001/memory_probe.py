"""Offline full-size synthetic accounting/control memory reproduction."""
import gc
from pathlib import Path
import resource
import sys
import tempfile
import time

import numpy as np
import pandas as pd

from backtesting.experiments import hc003
from src.runner import trading_calendar
from . import EXPERIMENT_ID, RULES
from . import artifacts, portfolio
from .selection import group_targets


def synthetic_inputs(root):
    """103 independent monthly inputs, 1,500 names and all accounting quotes."""
    codes = [f"{i:06d}" for i in range(1500)]
    sessions = trading_calendar.session_calendar("2017-02-01", "2025-12-30")
    planned, excluded = hc003.rebalance_calendar(sessions,
        months=pd.period_range("2017-05", "2025-11", freq="M"), end=pd.Timestamp("2025-12-30"))
    if excluded or len(planned) != 103:
        raise ValueError("synthetic probe requires all 103 months")
    book = object.__new__(hc003.PriceBook)
    book.experiment_id, book.repo_root, book.end = EXPERIMENT_ID, root, pd.Timestamp("2025-12-30")
    book.openings, book.zero_from = {}, {}
    selections = {rule: [] for rule in RULES}
    rng = np.random.default_rng(43)
    marks = np.ones(len(codes))
    for number, row in enumerate(planned):
        day = pd.Timestamp(row["trade_date"])
        marks *= np.exp(rng.normal(.004, .05, len(codes)))
        book.openings[day] = {code: hc003.Quote(float(mark), 10000., "KOSPI" if i < 750 else "KOSDAQ",
            2e9, i % 149 != number % 149, i % 251 == number % 251, i % 257 == number % 257)
            for i, (code, mark) in enumerate(zip(codes, marks))}
        for rule in RULES:
            common = pd.DataFrame({"market": ["KOSPI"] * 750 + ["KOSDAQ"] * 750,
                "phase": np.where(np.arange(1500) % 2, 1, 2),
                "market_cap": np.arange(1, 1501, dtype=float) * 1e8,
                "roa": rng.normal(.05, .1, 1500)}, index=codes)
            groups, selected, weights, boundaries = group_targets(common)
            selections[rule].append({**row, "universe": common, "common": common.copy(),
                "groups": groups, "selected": selected, "weights": weights, "no_trade": False,
                "data_complete": True, "A": list(codes), "boundaries": boundaries,
                "roa_provenance": {code: {"roa": float(common.loc[code, "roa"]), "fs_div": "CFS",
                    "receipts": [f"{2017 + number // 12}051000000{i}" for i in range(5)]} for code in codes}})
    book.closings = dict(zip(codes, (marks * np.exp(rng.normal(.004, .05, 1500))).tolist()))
    book.zero_from = {codes[i]: pd.Timestamp(planned[40 + i]["trade_date"]) for i in range(10)}
    return book, selections


def measure():
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="qp001-memory-") as directory:
        root = Path(directory)
        book, selections = synthetic_inputs(root)
        after_inputs = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        results, detail_bytes = {}, 0
        for rule in RULES:
            for loss in (False, True):
                runs = portfolio.paired_scenarios(book, selections[rule], loss=loss)
                actual, baseline = (runs["v2_1.0"][key]["per_month"] for key in ("strategy", "baseline"))
                controls = {}
                for independent in (False, True):
                    name = f"{rule}_{loss}_{independent}"
                    path = root / (name + ".jsonl")
                    controls[name] = portfolio.controls(book, selections[rule], actual, baseline,
                        loss=loss, independent=independent, paths=200, details_path=path)
                    detail_bytes += path.stat().st_size
                results[f"{rule}_{loss}"] = {"scenarios": runs, "controls": controls,
                    "groups": portfolio.subgroup_diagnostics(book, selections[rule], actual, baseline, loss=loss)}
                print(f"memory probe: {rule}, loss={loss}, controls=2/8; peak RSS "
                      f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024:,} bytes", file=sys.stderr, flush=True)
        # Exercise the exact streaming result writer while selections/results
        # and the 103 x 1,500 quote book are still resident.
        output = root / "result.json"
        artifacts.write_json(output, {"runs": results, "selections": {r: [{"A": s["A"],
            "roa": s["roa_provenance"]} for s in rows] for r, rows in selections.items()}})
        result_bytes = output.stat().st_size
        gc.collect()
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        if peak >= 1_400_000_000:
            raise MemoryError("realistic synthetic probe exceeded the 1.4 GB RSS budget")
        return {"mode": "memory-probe", "status": "ok", "synthetic_only": True,
            "months": 103, "stocks": 1500, "paths": 200, "control_scenarios": 8,
            "paired_cost_scenarios": 16, "input_peak_rss_bytes": after_inputs, "peak_rss_bytes": peak,
            "address_space_limit_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
            "streamed_control_detail_bytes": detail_bytes, "result_bytes": result_bytes,
            "elapsed_seconds": time.perf_counter() - started,
            "scope": "Full paired accounting, 8 control sets and result serialization with 206 selections and a 103 x 1500 quote book; excludes raw financial/archive parsing (measured by precheck)."}
