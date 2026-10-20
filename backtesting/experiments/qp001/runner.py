"""Offline QP001 commands. Return evaluation requires explicit --execute."""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import resource
import sys
import uuid

import numpy as np
import pandas as pd

from backtesting import experiment_registry
from backtesting.experiments import av001, common, hc002, hc003, hf001, ordering
from src.ingestion import dart_buyback, dart_listing
from src.ingestion.dart_filing_dates import periodic_period
from src.ingestion.dart_financial_date_rules import FilingDates
from src.ingestion.storage import read_rows
from src.runner import trading_calendar

from . import CUTOFFS, DESIGN_END, DESIGN_START, EXPERIMENT_ID, M, RULES, VALIDATION_END, VARIANT
from .financials import AssetArchive, ProfitabilityVersions, link_coverage
from . import artifacts, judge, portfolio, published
from .artifacts import digest
from .selection import group_targets


ROOT = Path(__file__).resolve().parents[3]
END = pd.Timestamp("2025-12-30")
AV_RESULT = "research/experiments/AV001_avoidance_portfolio/results/20261017T115416329783Z.json.gz"
AV_RESULT_SHA256 = "6c5e6e54779c305cee1dbb312e20a659d43db764f42541b68d5ac822dada16f9"
HISTORY_FIELDS = {"trade_date", "stock_code", "stock_name", "market", "close", "trading_value", "calendar_status", "market_cap"}


def content_hash(value):
    checksum = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False).iterencode(value):
        checksum.update(chunk.encode("utf-8"))
    return checksum.hexdigest()


def seal_registry(repo_root):
    relative = "research/experiments/registry.csv"
    return {"file": relative, "sha256": digest(Path(repo_root) / relative), "M": M}


def input_hashes(data_dir, repo_root):
    """Seal source bytes, excluding the live collector's quota/attempt ledger."""
    data, root = Path(data_dir), Path(repo_root)
    paths = set()
    paths.update(path for path in (data / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")
                 if int(path.stem[:4]) <= 2025)
    raw = data / "fundamentals/dart_multi_raw"
    paths.add(raw / "_universe.json")
    for metadata_path in (raw / "requests").glob("[0-9][0-9][0-9][0-9]_110*.json"):
        metadata = json.loads(metadata_path.read_bytes())
        derived = raw / "derived" / (metadata_path.stem + ".jsonl")
        if derived.is_file():
            paths.update((metadata_path, derived, raw / "blobs" / (metadata["sha256"] + ".json")))
    for name in ("list", "list_supplement"):
        paths.update((data / "disclosures/dart_full" / name).glob("*/*.jsonl"))
    for relative in ("disclosures/dart_full/_state.json", "disclosures/dart_full/list_supplement/_state.json",
                     "reference/corp_codes.csv", "reference/dart_company/companies.jsonl"):
        path = data / relative
        if path.is_file():
            paths.add(path)
    paths.update(path for path in (data / "market/krx_daily").glob("*/*.jsonl")
                 if "20170101" <= path.stem <= "20251230")
    code = set((root / "backtesting/experiments/qp001").glob("*.py"))
    code.add(root / f"research/experiments/{EXPERIMENT_ID}/preregistration.md")
    for relative in ("backtesting/experiments/av001.py", "backtesting/experiments/hc003.py", "backtesting/experiments/hc002.py",
                     "backtesting/experiments/hc001.py", "backtesting/experiments/hf001.py", "backtesting/experiments/common.py",
                     "backtesting/cost_model.py", "backtesting/holdout.py", "research_data/dart_multi_raw.py",
                     "backtesting/experiment_registry.py", "backtesting/append_only.py", "backtesting/signal_eval.py",
                     "backtesting/experiments/ordering.py",
                     "src/ingestion/dart_quarterly.py", "src/ingestion/dart_financial_date_rules.py",
                     "src/ingestion/dart_filing_dates.py", "src/ingestion/dart_listing.py", "src/ingestion/dart_buyback.py",
                     "src/ingestion/krx_market.py", "src/ingestion/storage.py", "src/research/industry_map.py",
                     "src/research/market_regimes.py", "src/utils/stock_codes.py", "src/runner/trading_calendar.py"):
        code.add(root / relative)
    return {"data": {path.relative_to(data).as_posix(): digest(path) for path in sorted(paths)},
            "code": {path.relative_to(root).as_posix(): digest(path) for path in sorted(code)}}


def calendar(sessions=None):
    sessions = trading_calendar.session_calendar("2015-01-01", "2025-12-30") if sessions is None else sessions
    planned, excluded = hc003.rebalance_calendar(sessions,
        months=pd.period_range(DESIGN_START, VALIDATION_END, freq="M"), end=END)
    if excluded:
        raise ValueError(f"missing registered decision months: {excluded}")
    return sessions, planned


def filing_dates(data_dir):
    """Keep financial receipts/originals/events; compute L over the full listing."""
    required, corps = set(), set()
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    paths = sorted(directory.glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    if not paths:
        raise ValueError("quarterly financial archives are missing")
    for path in paths:
        if int(path.stem[:4]) <= 2025:
            for row in read_rows(path):
                required.add(row["rcept_no"])
                corps.add(row["corp_code"])
    conflicts, lag = set(), []
    def rows():
        maximum = None
        for day in dart_listing.listing_paths(data_dir):
            for row in dart_listing.listing_rows(data_dir, day, conflicts=conflicts):
                current = (pd.Timestamp(row["rcept_dt"]) - pd.Timestamp(row["rcept_no"][:8])).days
                maximum = current if maximum is None else max(maximum, current)
                title = row["report_nm"]
                if (row["rcept_no"] in required or row["corp_code"] in corps and periodic_period(title) is not None
                        or "유상증자결정" in title or dart_buyback.title_matches(title)):
                    yield row
        lag.append(maximum)
    dates = FilingDates(rows(), conflicts=conflicts)
    if not lag or lag[0] is None:
        raise ValueError("L is undefined in full merged listing")
    dates.L = int(lag[0])
    dates.conflicts.update(conflicts)
    for key, (_, row) in list(dates.originals.items()):
        if row["rcept_no"] in conflicts:
            del dates.originals[key]
    return dates


def buyback_events(dates, sessions):
    events = []
    for receipt, row in dates.receipts.items():
        if receipt in dates.conflicts or not dart_buyback.title_matches(row["report_nm"]):
            continue
        actual = dates.annotate(row)["actual_public_date"]
        if actual is not None:
            events.append({"corp_code": row["corp_code"], "actual_public_date": actual,
                           "known_position": int(sessions.searchsorted(pd.Timestamp(actual), side="right"))})
    return events


def load_history(data_dir, sessions, decision, *, repo_root=ROOT):
    """Only 60 decision-inclusive sessions; HF001 trailing signal, no forwards."""
    day = pd.Timestamp(decision)
    pos = sessions.get_loc(day)
    required = sessions[max(0, pos - 59):pos + 1]
    if len(required) < 60:
        raise ValueError("incomplete decision history")
    rows = []
    fields = HISTORY_FIELDS | {"change_rate_pct"}
    for date in required:
        path = Path(data_dir) / "market/krx_daily" / str(date.year) / date.strftime("%Y%m%d.jsonl")
        if not path.is_file():
            raise ValueError(f"missing decision price session: {date.date()}")
        # Match KRX latest-episode selection without load_prices' ret_1d derivation.
        latest = {}
        for row in read_rows(path):
            if pd.Timestamp(row["trade_date"]) != date:
                raise ValueError("price row date disagrees with archive date")
            latest[row["stock_code"]] = {key: value for key, value in row.items() if key in fields}
        rows.extend(latest[code] for code in sorted(latest))
    frame = pd.DataFrame(rows)
    if not fields <= set(frame):
        raise ValueError(f"missing decision fields: {sorted(fields - set(frame))}")
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    for name in ("close", "trading_value", "market_cap"):
        frame[name] = pd.to_numeric(frame[name], errors="raise")
    frame["ret_1d"] = pd.to_numeric(frame.change_rate_pct, errors="raise") / 100
    frame = frame.set_index(["trade_date", "stock_code"]).sort_index()
    common.guard_prices(frame, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    if not common._sessions(frame).equals(required):
        raise ValueError("incomplete decision price sessions")
    return frame


def select_month(history, row, versions, events, bb_events, coverage, sessions, *, data_dir, repo_root, listing_groups=None):
    day = pd.Timestamp(row["decision_date"])
    pos = sessions.get_loc(day)
    required = sessions[pos - 19:pos + 1]
    with common.guarded_reads(EXPERIMENT_ID):
        universe, report = hc002.universe_on_decision(
            history.loc[history.index.get_level_values("trade_date").isin(required)], day,
            data_dir=data_dir, repo_root=repo_root, financial_loader=versions.load)
    universe = universe.sort_index()
    current = history.xs(day, level="trade_date")
    if history.index.get_level_values("trade_date").max() > day:
        raise ValueError("selection history extends beyond the decision date")
    volatility = hf001.volatility_filter(history, universe, day, sessions) if len(universe) else pd.DataFrame(columns=["f_vol"])
    f_vol = set(volatility.index[volatility.f_vol.eq(True)])
    known = events.loc[events.known_position.between(pos - 125, pos)]
    f_raise = set(known.stock_code) & set(universe.index)
    av_selection = {"universe": universe, "F_vol": f_vol, "F_raise": f_raise}
    av_targets = av001.target_set("av_all", av_selection, ())
    excluded_corps = {event["corp_code"] for event in bb_events
                      if event["actual_public_date"] < versions.cutoff and 0 <= pos - event["known_position"] <= 62}
    targets = {code for code in av_targets if universe.loc[code, "corp_code"] not in excluded_corps}
    roa, provenance, reasons, divisions, fallback = {}, {}, Counter(), Counter(), Counter()
    listed_counts = {status: {"A_count": 0, "C_count": 0} for status in ("listed", "delisted", "unknown")}
    for code in sorted(targets):
        signal = universe.loc[code]
        status = (listing_groups or {}).get(signal.corp_code, "unknown")
        listed_counts[status]["A_count"] += 1
        value = versions.roa(signal.corp_code, signal.fiscal_quarter, day)
        if value["roa"] is None:
            for division, reason in value["failures"].items():
                reasons[division + ":" + reason] += 1
        else:
            roa[code] = value["roa"]
            provenance[code] = value
            listed_counts[status]["C_count"] += 1
            divisions[value["fs_div"]] += 1
            if value["fallback"]:
                fallback.update(value["failures"].values())
    common_set = universe.loc[sorted(roa)].copy()
    common_set["roa"] = pd.Series(roa, dtype=float)
    for field in ("market", "market_cap"):
        common_set[field] = current[field].reindex(common_set.index)
    groups, selected, weights, boundaries = group_targets(common_set)
    ratio = len(common_set) / len(targets) if targets else None
    complete = coverage["covered"] and (ratio is None or ratio >= 0.90)
    for counts in listed_counts.values():
        counts["C_over_A"] = counts["C_count"] / counts["A_count"] if counts["A_count"] else None
    return {**row, **av_selection, "A": sorted(targets), "common": common_set, "groups": groups,
            "selected": selected, "weights": weights, "boundaries": boundaries, "roa_provenance": provenance,
            "no_trade": not targets, "data_complete": complete,
            "precheck": {"month": row["month"], "decision_date": row["decision_date"],
                "U_count": len(universe), "F_vol_count": len(f_vol), "F_raise_count": len(f_raise),
                "A_count": len(targets), "C_count": len(common_set), "C_over_A": ratio,
                "data_complete": complete, "reason": None if complete else "data_incomplete",
                "listing_complete": coverage["covered"], "bb_excluded": len(av_targets) - len(targets),
                "link_failures": dict(reasons), "fs_div": dict(divisions), "OFS_fallback_reasons": dict(fallback),
                "ROA_by_listing_status": listed_counts, "universe": report,
                "listing_coverage": coverage,
                "failure_reasons": ([coverage["reason"]] if not coverage["covered"] else [])
                    + (["C_over_A_below_0.90"] if ratio is not None and ratio < 0.90 else []),
                "groups": [{"key": list(key), "C_count": len(codes), "selected_count": len(selected[key]),
                            "alpha": weights[key]} for key, codes in groups.items()]}}


def prepare_selections(data_dir, repo_root, sessions, planned, *, assets, precheck_only=False, source_hashes=None):
    manifest_hash = content_hash(source_hashes) if source_hashes is not None else None
    dates = filing_dates(data_dir)
    listings = dates.event_listings()
    listings = listings.loc[listings.rcept_dt < "20260101"]
    events, event_counts = hf001.rights_events(listings, sessions)
    bb_events = buyback_events(dates, sessions)
    from research_data.dart_multi_raw import _listing_groups
    listing_groups = _listing_groups(data_dir)
    status = dart_listing.listing_status(data_dir, experiment_id=EXPERIMENT_ID)
    files = dart_listing.listing_paths(data_dir)
    coverage = {row["month"]: hf001.listing_coverage(row["decision_date"], sessions, files, data_dir=data_dir, status=status)
                for row in planned}
    selections, reports = {rule: [] for rule in RULES}, {}
    for stage, cutoff in CUTOFFS.items():
        rows = [r for r in planned if (r["month"] <= DESIGN_END) == (stage == "design")]
        if not rows:
            continue
        for rule in RULES:
            versions = ProfitabilityVersions(data_dir, dates, rule, cutoff=cutoff,
                sample_end=rows[-1]["decision_date"], assets=assets)
            stage_events = events.loc[np.maximum(events.rcept_dt, events.rcept_no.str[:8]) < cutoff.replace("-", "")]
            for row in rows:
                try:
                    history = load_history(data_dir, sessions, row["decision_date"], repo_root=repo_root)
                    selected = select_month(history, row, versions, stage_events, bb_events, coverage[row["month"]],
                        sessions, data_dir=data_dir, repo_root=repo_root, listing_groups=listing_groups)
                except ValueError as exc:
                    if not precheck_only:
                        raise
                    selected = {"precheck": {"month": row["month"], "decision_date": row["decision_date"],
                        "data_complete": False, "reason": "selection_failed", "failure_reasons": [str(exc)],
                        "A_count": None, "C_count": None, "C_over_A": None, "groups": [], "fs_div": {}}}
                monthly = selected["precheck"]
                monthly.update(stage=stage, rule=rule, actual_public_date_cutoff=cutoff)
                if source_hashes is not None:
                    day = pd.Timestamp(row["decision_date"])
                    pos = sessions.get_loc(day)
                    price_paths = [f"market/krx_daily/{d.year}/{d:%Y%m%d}.jsonl" for d in sessions[max(0, pos - 59):pos + 1]]
                    monthly["input_hashes"] = {"source_manifest_sha256": manifest_hash,
                        "decision_prices": {name: source_hashes["data"].get(name) for name in price_paths}}
                selections[rule].append({"precheck": monthly} if precheck_only else selected)
                if precheck_only and (len(selections[rule]) % 12 == 0 or row == rows[-1]):
                    print(f"precheck: {stage}/{rule} through {row['month']}", file=sys.stderr, flush=True)
            reports[stage + ":" + rule] = versions.report()
            del versions
            gc.collect()
    sensitivity_by_stage = {stage: all(stage + ":" + rule in reports for rule in RULES)
                            and all(reports[stage + ":" + rule]["sensitivity_available"] for rule in RULES)
                            for stage in CUTOFFS}
    return {"selections": selections, "filing_rules": reports, "L": dates.L,
            "coverage": coverage, "event_counts": event_counts,
            "sensitivity_by_stage": sensitivity_by_stage,
            "sensitivity_available": all(sensitivity_by_stage.values())}


def precheck(*, data_dir, repo_root=ROOT, sessions=None):
    """No PriceBook, forward observations, NAV or return loader is reachable."""
    hashes = input_hashes(data_dir, repo_root)
    assets = AssetArchive(data_dir)
    links = link_coverage(data_dir, assets)
    result = {"experiment_id": EXPERIMENT_ID, "mode": "precheck", "returns_computed": False,
              "registry_seal": seal_registry(repo_root), "link_coverage": links, "source_hashes": hashes,
              "trailing_volatility_computed": True, "forward_prices_used": False,
              "volatility_policy": "HF001 60 verified decision-inclusive daily log returns; no post-decision prices"}
    sessions, planned = calendar(sessions)
    prepared = prepare_selections(data_dir, repo_root, sessions, planned, assets=assets,
                                  precheck_only=True, source_hashes=hashes)
    monthly = {rule: [s["precheck"] for s in prepared["selections"][rule]] for rule in RULES}
    fallback = {rule: sum(row["fs_div"].get("OFS", 0) for row in monthly[rule]) for rule in RULES}
    result.update(status="ok" if all(row["data_complete"] for rows in monthly.values() for row in rows) else "data_incomplete",
        monthly=monthly, fs_div_fallback_counts=fallback, filing_rules=prepared["filing_rules"], L=prepared["L"],
        sensitivity_available=prepared["sensitivity_available"],
        sensitivity_by_stage=prepared["sensitivity_by_stage"],
        monthly_summary={rule: {"months": len(rows), "complete": sum(r["data_complete"] for r in rows),
            "minimum_C_over_A": min((r["C_over_A"] for r in rows if r["C_over_A"] is not None), default=None),
            "incomplete_months": [r["month"] for r in rows if not r["data_complete"]]} for rule, rows in monthly.items()},
        source_snapshot_unchanged=input_hashes(data_dir, repo_root) == hashes)
    if not result["source_snapshot_unchanged"] or assets.pending:
        result.update(status="incomplete", reason="source_snapshot_changed" if not result["source_snapshot_unchanged"] else "pending_derived_files")
    result["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return result


def price_book(data_dir, repo_root, sessions, planned, *, experiment_id=EXPERIMENT_ID):
    end = pd.Timestamp(planned[-1]["end_date"])
    first_pos = sessions.get_loc(pd.Timestamp(planned[0]["trade_date"]))
    days = sessions[max(0, first_pos - 20):sessions.get_loc(end) + 1]
    rebalance_days = [r["trade_date"] for r in planned] + [r["end_date"] for r in planned if r["end_kind"] == "open"]
    # The terminal close substitutes for the last rebalance valuation. Include
    # it in disappearance detection even though no terminal trade is made.
    rebalance_days.append(end.date().isoformat())
    def windows():
        for quarter in sorted(set(days.to_period("Q"))):
            local = days[days.to_period("Q") == quarter]
            prices, _ = hc003.load_prices(data_dir, local[0], local[-1], repo_root=repo_root, experiment_id=experiment_id)
            yield prices
    # The last requested end may be an open (AV short-window replay).
    return hc003.PriceBook.from_windows(windows(), days, rebalance_days, end,
        experiment_id=experiment_id, repo_root=repo_root)


def reproduce_av001(*, data_dir, repo_root=ROOT, group_weighted=False):
    """Three published design months from cash; no QP signal or result writes."""
    path = Path(repo_root) / AV_RESULT
    if digest(path) != AV_RESULT_SHA256:
        raise ValueError("published AV001 result hash differs")
    saved = published.read_av_window(path)
    sessions = trading_calendar.session_calendar("2015-01-01", "2025-12-30")
    differences = {}
    for rule in RULES:
        rows = saved["selections"][rule]
        planned = []
        for row in rows:
            if not "2017-05" <= row["month"] <= "2023-12":
                raise ValueError("AV replay only permits already-published design months")
            universe = pd.DataFrame({"phase": [1] * len(row["U"])}, index=row["U"])
            planned.append({**row, "universe": universe, "F_vol": set(), "F_raise": set(),
                            "published_targets": set(row["H"]["av_all"])})
            if group_weighted:
                key = ("AV001", False, 0)
                planned[-1].update(groups={key: set(row["U"])}, weights={key: 1.0})
        expected = saved["observations"][rule]
        first = sessions.get_loc(pd.Timestamp(planned[0]["trade_date"]))
        for day in sessions[max(0, first - 20):sessions.get_loc(pd.Timestamp(planned[-1]["end_date"])) + 1]:
            price = Path(data_dir) / "market/krx_daily" / str(day.year) / day.strftime("%Y%m%d.jsonl")
            if digest(price) != saved["prices"].get(price.relative_to(data_dir).as_posix()):
                raise ValueError("AV001 replay price snapshot differs from the published input hash")
        book = price_book(data_dir, repo_root, sessions, planned, experiment_id=av001.EXPERIMENT_ID)
        def targets(variant, selection, held):
            return selection["published_targets"]
        actual = portfolio.simulate(book, planned, experiment_id=av001.EXPERIMENT_ID, variant="av_all", targets_fn=targets)
        if group_weighted and any(row["group_weights"] != {str(("AV001", False, 0)): 1.0} for row in actual["per_month"]):
            raise ValueError("AV001 replay did not traverse the group-weight accounting branch")
        deltas = [abs(a["excess"] - b["excess"]) for a, b in zip(actual["per_month"], expected)]
        if len(deltas) != 3 or max(deltas) > 1e-12:
            raise ValueError(f"reused accounting does not reproduce published AV001 {rule}: {deltas}")
        differences[rule] = {"months": [r["month"] for r in rows], "max_abs_monthly_e_difference": max(deltas)}
    return {"mode": "reproduce-av001-groups" if group_weighted else "reproduce-av001", "status": "ok",
            "accounting_branch": "group_weights_alpha_1" if group_weighted else "hc003",
            "published_result_sha256": AV_RESULT_SHA256, "rules": differences}


def metrics(runs, controls, selections):
    rows = runs["v2_1.0"]["strategy"]["per_month"]
    cost_rows = runs["v2_1.5"]["strategy"]["per_month"]
    design = [r for r in rows if r["month"] <= DESIGN_END]
    validation = [r for r in rows if r["month"] > DESIGN_END]
    return {"design": portfolio.summary(design), "validation": portfolio.summary(validation),
            "design_at_cost_1_5": portfolio.summary([r for r in cost_rows if r["month"] <= DESIGN_END]),
            "random_control_share": controls["share_of_controls"],
            "data_complete": all(s["data_complete"] for s in selections),
            "validation_months": [r["month"] for r in validation if r["valid"]]}


def compute(*, data_dir, repo_root, attempt, details_dir):
    """Explicit real computation; callers, never precheck, opt into this path."""
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    fields = registration.fields
    if fields["variants_planned"] != 1 or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("QP001 requires its single registered variant, no holdout and no LLM")
    registry_seal = seal_registry(repo_root)
    sessions, planned = calendar()
    assets = AssetArchive(data_dir)
    links = link_coverage(data_dir, assets)  # Pre-return integrity scan.
    if assets.pending:
        raise ValueError("raw archive has pending derived files; wait for the collector snapshot")
    hashes = input_hashes(data_dir, repo_root)
    prepared = prepare_selections(data_dir, repo_root, sessions, planned, assets=assets, source_hashes=hashes)
    attempt["source_hashes"] = hashes  # First performance read is PriceBook.
    book = price_book(data_dir, repo_root, sessions, planned)
    main, loss, rules, losses = {}, {}, {}, {}
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
            summaries = {name: {"overall": portfolio.summary(run["strategy"]["per_month"]),
                "design": portfolio.summary([r for r in run["strategy"]["per_month"] if r["month"] <= DESIGN_END]),
                "validation": portfolio.summary([r for r in run["strategy"]["per_month"] if r["month"] > DESIGN_END])}
                for name, run in runs.items()}
            by_year = {year: portfolio.summary([r for r in actual if r["month"].startswith(year)])
                       for year in sorted({r["month"][:4] for r in actual})}
            dated = pd.Series(actual, index=[r["decision_date"] for r in actual], dtype=object)
            regimes = {name: portfolio.summary(group.tolist()) for name, group in hc003.market_regimes.split_by_regime(dated).items()}
            destination[rule] = {"scenarios": runs, "random_control": controls,
                "independent_redraw": diagnostic, "metrics": statistics[rule], "scenario_summaries": summaries,
                "by_year": by_year, "short_sale_regimes": regimes,
                "groups": portfolio.subgroup_diagnostics(book, selections, actual, baseline, loss=vanished)}
    judgement = judge.judge(main, loss, sensitivity_available=prepared["sensitivity_available"])
    if input_hashes(data_dir, repo_root) != hashes:
        raise ValueError("QP001 source snapshot changed during computation; no result may be published")
    return {"experiment_id": EXPERIMENT_ID, "variant": VARIANT, "stage": "design_validation",
            "prereg_commit": registration.commit_hash, "code_commit": experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
            "registry_seal": registry_seal, "M": M, "judgement": judgement,
            "source_hashes": hashes, "library_versions": {"python": platform.python_version(),
                **{name: importlib.metadata.version(name) for name in ("numpy", "pandas", "exchange-calendars", "PyYAML")}},
            "rules": rules, "vanished": losses, "link_coverage": links,
            "monthly_precheck": {r: [s["precheck"] for s in prepared["selections"][r]] for r in RULES},
            "selections": {r: [{"month": s["month"], "A": s["A"], "C": s["common"].index.tolist(),
                "groups": [{"key": list(key), "members": sorted(codes), "selected": sorted(s["selected"][key]),
                            "alpha": s["weights"][key]} for key, codes in s["groups"].items()],
                "market_cap_boundaries": s["boundaries"], "roa": s["roa_provenance"]}
                for s in prepared["selections"][r]] for r in RULES},
            "filing_rules": prepared["filing_rules"], "L": prepared["L"],
            "sensitivity_by_stage": prepared["sensitivity_by_stage"],
            "decision_periods": {"design": [DESIGN_START, DESIGN_END], "validation": ["2024-01", VALIDATION_END]},
            "state_policy": "One continuous per-rule/per-scenario path from cash 1; design states are never rewritten in validation."}


def registry_metrics(payload):
    return {"stage": "design_validation", "run_id": payload["run_id"],
            "M": M, "registry_seal": payload["registry_seal"], "judgement": payload["judgement"],
            "rules": {rule: {"design": payload["rules"][rule]["metrics"]["design"],
                "design_at_cost_1_5": payload["rules"][rule]["metrics"]["design_at_cost_1_5"],
                "validation_mean": payload["rules"][rule]["metrics"]["validation"]["primary"]["mean"],
                "random_control_share": payload["rules"][rule]["metrics"]["random_control_share"]} for rule in RULES}}


def register_saved(payload, output, repo_root):
    """Append at most once for this exact saved result, without evaluation."""
    relative = Path(output).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    metrics = {**registry_metrics(payload), "result_file": relative, "result_sha256": digest(output)}
    experiment_registry.encode_metrics(metrics)
    for row in hc003.registry_rows(repo_root):
        if row["experiment_id"] == EXPERIMENT_ID and row["variant"] == VARIANT:
            saved = json.loads(row["metrics_json"])
            if saved.get("run_id") == payload["run_id"] or saved.get("result_file") == relative:
                if (saved != metrics or row["verdict"] != payload["judgement"]["verdict"]
                        or row["prereg_commit"] != payload["prereg_commit"]):
                    raise ValueError("saved result differs from its existing registry row")
                return False
    experiment_registry.record_trial(EXPERIMENT_ID, VARIANT, metrics, payload["judgement"]["verdict"], repo_root=repo_root,
        note="QP001 fixed q_roa50; both date rules and full -100% recomputation; candidate only")
    return True


def execute(*, data_dir, output, repo_root=ROOT):
    output = Path(output).resolve()
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, (VARIANT,), repo_root) as attempt:
            reserve = bytearray(8 * 1024 * 1024)  # Release space for the abort ledger on MemoryError.
            try:
                payload = compute(data_dir=data_dir, repo_root=repo_root, attempt=attempt,
                                  details_dir=Path(output).with_suffix(".controls"))
                payload["run_id"] = uuid.uuid4().hex
                experiment_registry.encode_metrics(registry_metrics(payload))
                artifacts.write_json(output, payload)
                register_saved(payload, output, repo_root)
                return payload
            except BaseException:
                del reserve
                gc.collect()
                raise


def recover_registry(output, *, repo_root=ROOT):
    """Save-only registration recovery. Never load prices or recompute signals."""
    with ordering.execution_lock(repo_root):
        registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
        with Path(output).open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if (payload["experiment_id"] != EXPERIMENT_ID or payload["variant"] != VARIANT
                or payload["stage"] != "design_validation" or payload["prereg_commit"] != registration.commit_hash):
            raise ValueError("recovery requires a complete saved QP001 result under its original preregistration")
        for section in ("rules", "vanished"):
            for rule in RULES:
                for name in ("random_control", "independent_redraw"):
                    detail = payload[section][rule][name]["details"]
                    path = (Path(repo_root) / detail["file"]).resolve()
                    if not path.is_relative_to(Path(repo_root).resolve()) or digest(path) != detail["sha256"]:
                        raise ValueError("saved control detail hash/path differs")
        appended = register_saved(payload, output, repo_root)
        return {"mode": "recover-registry", "status": "ok", "registry_appended": appended,
                "result_sha256": digest(output), "run_id": payload["run_id"], "recomputed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--precheck", action="store_true")
    modes.add_argument("--reproduce-av001", action="store_true")
    modes.add_argument("--reproduce-av001-groups", action="store_true")
    modes.add_argument("--recover-registry", type=Path, metavar="SAVED_RESULT")
    modes.add_argument("--memory-probe", action="store_true")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, help="Exclusive result file inside this worktree (never the data root)")
    args = parser.parse_args(argv)
    if (args.reproduce_av001 or args.reproduce_av001_groups or args.recover_registry) and args.output:
        parser.error("reproduction and registry recovery do not accept --output")
    if args.execute and not args.output:
        parser.error("--execute requires --output to preserve the complete result")
    if args.output:
        output = args.output.resolve()
        if (not output.is_relative_to(ROOT) or output.is_relative_to(args.data_dir.resolve())
                or output.is_relative_to((ROOT / "data").resolve()) or output.exists() or args.output.is_symlink()
                or any(part.startswith(".env") for part in output.parts)):
            parser.error("--output must be a new file inside this worktree and outside the read-only data root")
    if args.recover_registry:
        saved = args.recover_registry.resolve()
        if (not saved.is_relative_to(ROOT) or saved.is_relative_to(args.data_dir.resolve())
                or saved.is_relative_to((ROOT / "data").resolve()) or args.recover_registry.is_symlink()
                or any(part.startswith(".env") for part in saved.parts)):
            parser.error("recovery requires a saved result inside this worktree and outside the data root")
    # Hard virtual-address cap also bounds RSS; thread counts are set by __main__.
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = min(1_400_000_000, hard) if hard != resource.RLIM_INFINITY else 1_400_000_000
    resource.setrlimit(resource.RLIMIT_AS, (min(soft, cap) if soft != resource.RLIM_INFINITY else cap, hard))
    try:
        if args.precheck:
            payload = precheck(data_dir=args.data_dir)
        elif args.reproduce_av001 or args.reproduce_av001_groups:
            payload = reproduce_av001(data_dir=args.data_dir, group_weighted=args.reproduce_av001_groups)
        elif args.recover_registry:
            payload = recover_registry(args.recover_registry)
        elif args.memory_probe:
            from .memory_probe import measure
            payload = measure()
        else:
            payload = execute(data_dir=args.data_dir, output=args.output)
        if args.output:
            if not args.execute:
                artifacts.write_json(args.output, payload)
            print(json.dumps({"file": str(args.output), "sha256": digest(args.output),
                              "status": payload.get("status", payload.get("judgement", {}).get("verdict"))}))
        else:
            json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True, allow_nan=False)
            print()
        return 2 if args.precheck and payload["status"] != "ok" else 0
    except (ValueError, TypeError, OSError, MemoryError) as exc:
        parser.exit(2, f"QP001 stopped: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
