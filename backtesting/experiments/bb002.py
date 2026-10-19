"""BB002 historical confirmation; metadata precheck, unprotected rehearsal, one execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import experiment_registry, holdout
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import bb001, common, hc003, ordering
from src.ingestion import dart_buyback as dart
from src.runner import trading_calendar


EXPERIMENT_ID = "BB002_buyback_2026h1_check"
FORWARD_ID = "BB003_buyback_forward"
DESIGN_EFFECT = 0.018733852613615584
MINIMUM_EFFECT = 0.009366926306807792
COMPARISON_FILE = "research/experiments/BB001_direct_buyback/results/20261017T192254039053Z.json.gz"
COMPARISON_SHA256 = "7a56e0fb0026090c8d8587cde96327f40d509a982bf7a1d5e0698c471cee00ef"
INTERPRETATION = (
    "Post-exposure, hypothesis-specific historical confirmation on a shared period; "
    "not an independent holdout. BB001 design/validation are not rejudged. "
    "Only d21 is judged. HAC t, random controls and all strata are report-only. "
    "Reform dates are labels pending official-text verification. "
    "A vanished stock means no retrading through the fixed horizon, not confirmed delisting."
)


@dataclass(frozen=True)
class Window:
    start: str
    end: str
    vanish_end: str
    structured_range: tuple[str, str]


CONFIRMATION = Window("2026-01-02", "2026-06-30", "2026-09-30", ("20260102", "20260630"))
REHEARSAL = Window("2025-01-02", "2025-06-30", "2025-09-30", (dart.START, dart.END))


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    fields = registration.fields
    if fields["variants_planned"] != 1 or not fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("BB002 requires only d21, one protected session and no LLM")
    forward = experiment_registry.verify_preregistration(FORWARD_ID, repo_root)
    if forward.commit_hash != registration.commit_hash:
        raise ValueError("BB002 and BB003 preregistrations must share their original commit")
    return registration


def comparison_reference(repo_root=PROJECT_ROOT):
    """Verify concatenated compressed bytes without opening the saved observations."""
    checksum, parts = hashlib.sha256(), {}
    for suffix in (".part00", ".part01"):
        path = Path(repo_root) / (COMPARISON_FILE + suffix)
        part = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(block)
                part.update(block)
        parts[str(path)] = part.hexdigest()
    if checksum.hexdigest() != COMPARISON_SHA256:
        raise ValueError("BB001 concatenated comparison file SHA-256 mismatch")
    return {"file": COMPARISON_FILE, "sha256": checksum.hexdigest(), "part_hashes": parts,
            "design_mean": DESIGN_EFFECT, "minimum_mean": MINIMUM_EFFECT}


def _calendar(window, sessions=None):
    days = bb001._days(trading_calendar.session_calendar("2015-01-01", "2026-12-31")
                       if sessions is None else sessions)
    start, end, vanish = map(pd.Timestamp, (window.start, window.end, window.vanish_end))
    expected = trading_calendar.session_calendar(start - pd.Timedelta(days=200), vanish)
    if not days[(days >= expected[0]) & (days <= vanish)].equals(expected):
        raise ValueError("BB002 requires the complete shared official-session calendar through " + window.vanish_end)
    first = int(days.searchsorted(start, side="right"))
    last = int(days.searchsorted(end, side="right"))
    if first < 64 or last + 20 >= len(days) or days[last + 20] > vanish:
        raise ValueError("BB002 calendar lacks repeat, liquidity or d21 observation windows")
    grid = days[(days >= start) & (days <= days[last + 20])]
    return days, grid, days[first - 64], days[first - 20]


def _guard(day, repo_root):
    day = pd.Timestamp(day).date()
    if holdout.HOLDOUT_START <= day <= holdout.HOLDOUT_END:
        root = Path(repo_root).resolve()
        if not any(s._open and s._pid == os.getpid() and s.experiment_id == EXPERIMENT_ID
                   and s.repo_root == root and s.ledger_path == root / holdout.LEDGER_PATH
                   for s in holdout._SESSIONS.get()):
            raise ValueError("protected prices require an open BB002 HoldoutSession")
    holdout.guard_period(day, day, experiment_id=EXPERIMENT_ID, repo_root=repo_root)


class PriceReader(bb001.PriceReader):
    """BB001 parser, bounded cache, BB002 ID on every lookup and hash recheck."""

    def __init__(self, data_dir, sessions, *, window=CONFIRMATION, prices=None, repo_root=PROJECT_ROOT):
        self.data_dir, self.sessions, self.repo_root = Path(data_dir), bb001._days(sessions), repo_root
        self.window, self.prices, self.source_hashes = window, prices, {}
        _, _, _, self.price_start = _calendar(window, self.sessions)
        self._last_trades = None
        if prices is not None:
            days = common._sessions(prices)
            if prices.index.has_duplicates or not days.difference(self.sessions).empty:
                raise ValueError("supplied BB002 prices require unique official-session observations")
            if len(days) and (days.min() < self.price_start or days.max() > pd.Timestamp(window.vanish_end)):
                raise ValueError("supplied BB002 prices exceed the fixed read window")
            for day in days:
                _guard(day, repo_root)

    def read(self, day):
        day = pd.Timestamp(day)
        if day not in self.sessions or not self.price_start <= day <= pd.Timestamp(self.window.vanish_end):
            raise ValueError("BB002 price lookup outside the fixed official-session window")
        _guard(day, self.repo_root)
        return self._load(day)

    @lru_cache(maxsize=24)
    def _load(self, day):
        return bb001.PriceReader._load.__wrapped__(self, day)

    def last_trades(self):
        if self._last_trades is None:
            last = {}
            for day in self.sessions[(self.sessions >= self.price_start) & (self.sessions <= self.window.vanish_end)]:
                for code, row in self.require(day).items():
                    if bb001._traded(row):
                        last[code] = day
            self._last_trades = last
        return self._last_trades


def construct_events(listings, files, sessions, data_dir, *, window=CONFIRMATION, status=None):
    if status is None:
        status = dart.listing_status(data_dir, price_end=window.end, experiment_id=EXPERIMENT_ID)
    return bb001.construct_events(listings, files, sessions, data_dir,
        start=pd.Timestamp(window.start), end=pd.Timestamp(window.end), period="confirmation",
        structured_range=window.structured_range, status=status)


def schedule(event, sessions):
    return bb001.schedule(event, 21, sessions, cutoff=None)


def _code_hashes():
    names = ("backtesting/experiments/bb002.py", "backtesting/experiments/bb001.py",
             "backtesting/experiments/common.py", "backtesting/experiments/ordering.py",
             "backtesting/experiments/hc003.py", "backtesting/experiments/hc001.py",
             "backtesting/cost_model.py", "backtesting/holdout.py", "backtesting/experiment_registry.py",
             "backtesting/append_only.py", "src/ingestion/dart_buyback.py", "src/ingestion/dart_listing.py",
             "src/ingestion/dart.py", "src/ingestion/dart_backfill.py", "src/ingestion/dart_api.py",
             "src/ingestion/storage.py", "src/utils/stock_codes.py", "src/runner/trading_calendar.py")
    return {str(PROJECT_ROOT / name): bb001._sha(PROJECT_ROOT / name) for name in names}


def _input_hashes(data_dir, repo_root, files, listings, window, reference):
    paths = list(files.values()) + dart.supplement_paths(data_dir, files)
    for relative in ("disclosures/dart_full/_state.json", "disclosures/dart_full/list_supplement/_state.json",
                     "reference/corp_codes.csv", "reference/dart_company/companies.jsonl"):
        paths.append(Path(data_dir) / relative)
    for experiment in (EXPERIMENT_ID, FORWARD_ID, bb001.EXPERIMENT_ID):
        paths.append(Path(repo_root) / "research/experiments" / experiment / "preregistration.md")
    for row in listings:
        paths += [dart._path(data_dir, "structured", row["corp_code"],
            from_date=window.structured_range[0], to_date=window.structured_range[1]),
            dart._path(data_dir, "documents", row["rcept_no"])]
    return {**{str(path): bb001._sha(path) for path in dict.fromkeys(paths) if path.is_file()},
            **reference["part_hashes"]}


def _prepare(*, data_dir, repo_root, window, sessions=None):
    registration = _registration(repo_root)
    days, grid, listing_start, price_start = _calendar(window, sessions)
    status = dart.listing_status(data_dir, price_end=window.end, experiment_id=EXPERIMENT_ID)
    listings, files, counts = dart.load_listings(data_dir, candidates_only=True,
        from_date=listing_start.strftime("%Y%m%d"), to_date=window.end)
    required = pd.date_range(listing_start, window.end)
    covered = dart.covered_listing_days(data_dir, required, status=status)
    missing_listings = sorted(set(required.strftime("%Y%m%d")) - set(covered))
    expected = days[(days >= price_start) & (days <= window.vanish_end)]
    inventory = []
    for day in expected:
        path = Path(data_dir) / "market/krx_daily" / f"{day:%Y}/{day:%Y%m%d}.jsonl"
        inventory.append({"date": day.date().isoformat(), "path": str(path),
                          "size": path.stat().st_size if path.is_file() else None})
    reference = comparison_reference(repo_root)
    code = _code_hashes()
    hashes = _input_hashes(data_dir, repo_root, files, listings, window, reference)
    events, construction = construct_events(listings, files, days, data_dir, window=window, status=status)
    missing_prices = [row["date"] for row in inventory if not row["size"]]
    sample = [row for row in listings if pd.Timestamp(window.start) <= pd.Timestamp(row["rcept_dt"]) <= pd.Timestamp(window.end)]
    corporations = {row["corp_code"] for row in sample}
    archives = [dart.structured_record(data_dir, corp, from_date=window.structured_range[0],
                to_date=window.structured_range[1]) for corp in corporations]
    collection = {"structured_corporations": len(corporations),
                  "structured_corporations_complete": sum(bool(archive and archive["complete"]) for archive in archives),
                  "documents_missing": sum(dart.document_record(data_dir, row["rcept_no"]) is None for row in sample)}
    collection["complete"] = (collection["structured_corporations_complete"] == len(corporations)
                              and collection["documents_missing"] == 0)
    ready = not missing_prices and not missing_listings and collection["complete"]
    report = {"experiment_id": EXPERIMENT_ID, "stage": "precheck", "status": "ready" if ready else "insufficient",
        "ready": ready, "prereg_commit": registration.commit_hash, "window": vars(window),
        "calendar_version": trading_calendar.CALENDAR_VERSION,
        "hac_grid": {"start": grid[0].date().isoformat(), "end": grid[-1].date().isoformat(),
                     "sessions": len(grid), "lag": 20, "includes_no_event_days": True},
        "prices": {"inventory": inventory, "missing_sessions": missing_prices,
                   "content_checked": False, "vanish_end": window.vanish_end},
        "listing_coverage": {"start": listing_start.date().isoformat(), "end": window.end,
                             "required_days": len(required), "missing_days": missing_listings},
        "counts": {"listings": counts, "construction": construction},
        "collection": collection,
        "dart_plan": dart.plan(data_dir, from_date=window.start, to_date=window.end, status=status),
        "listing_version": status["listing_version"], "comparison": reference,
        "code_hashes": code, "source_hashes": hashes, "interpretation": INTERPRETATION}
    return {"report": report, "registration": registration, "sessions": days, "grid": grid,
            "listings": listings, "files": files, "events": events}


def precheck(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, sessions=None):
    return _prepare(data_dir=data_dir, repo_root=repo_root, window=CONFIRMATION, sessions=sessions)["report"]


def _require_ready(prepared):
    if not prepared["report"]["ready"]:
        raise ValueError("BB002 insufficient required listing/official-session prices/buyback archives; no session opened: "
                         + json.dumps({"prices": prepared["report"]["prices"]["missing_sessions"],
                                       "listings": prepared["report"]["listing_coverage"]["missing_days"],
                                       "collection": prepared["report"]["collection"]}))


def _metrics(rows, grid, *, worthless=False):
    mean = lambda values: float(np.mean(values)) if len(values) else None
    return {"primary": bb001._summary(rows, grid, 21, worthless=worthless),
        "dates": len({row["entry_date"] for row in rows}), "corporations": len({row["corp_code"] for row in rows}),
        "cost_sensitivity": {key: bb001._summary(rows, grid, 21, key=key, worthless=worthless)
                             for key in ("1.0", "1.5", "2.0", "v1")},
        "random_control": bb001.random_controls(rows, worthless=worthless),
        "entry_date_cluster_t": bb001.cluster_t([bb001.event_excess(row, worthless=worthless) for row in rows],
                                                [row["entry_date"] for row in rows]),
        "event_net_mean": mean([bb001._net(row["stock"], worthless=worthless) for row in rows]),
        "controls_net_mean": mean([mean([bb001._net(p, worthless=worthless) for p in row["controls"]]) for row in rows]),
        "vanished_counts": {"events": sum(row["stock"]["vanished"] for row in rows),
                            "controls": sum(p["vanished"] for row in rows for p in row["controls"])},
        "valuation": "vanished_gross_minus_100pct" if worthless else "last_trading_close"}


def _judge_one(result, required_data_complete):
    mean, stressed = result["primary"]["mean"], result["cost_sensitivity"]["1.5"]["mean"]
    inputs = {"observations": result["primary"]["count"], "corporations": result["corporations"],
              "entry_dates": result["dates"], "mean_excess": mean, "mean_excess_at_cost_1_5": stressed,
              "minimum_mean_excess": MINIMUM_EFFECT, "required_data_complete": required_data_complete}
    missing = (not required_data_complete or inputs["observations"] < 30 or inputs["corporations"] < 20
               or inputs["entry_dates"] < 20 or any(value is None or not np.isfinite(value) for value in (mean, stressed)))
    failures = []
    if not missing:
        if mean < MINIMUM_EFFECT:
            failures.append("mean excess must be >= half the BB001 design effect")
        if stressed <= 0:
            failures.append("cost x1.5 mean excess must be >0")
    return {"verdict": "insufficient" if missing else "fail" if failures else "pass",
            "reasons": ["requires complete essential data, >=30 events, >=20 firms and >=20 entry dates"] if missing
                       else failures or ["BB002 section-3 criteria satisfied"], "judgement_inputs": inputs}


def judge(result, sensitivity, *, required_data_complete=True):
    base = _judge_one(result, required_data_complete)
    loss = _judge_one(sensitivity, required_data_complete)
    sensitivity.update(loss)
    result.update(base)
    changed = base["verdict"] != loss["verdict"]
    result["delisting_sensitivity"] = {"base_verdict": base["verdict"], "verdict": loss["verdict"], "sensitive": changed,
                                      "judgement_inputs": loss["judgement_inputs"]}
    if "insufficient" in (base["verdict"], loss["verdict"]):
        result.update(verdict="insufficient", reasons=["minimum observations or essential data unavailable in a required valuation"])
    elif changed:
        result.update(verdict="delisting_sensitive", reasons=["last-close and vanished -100% valuations change the section-3 verdict"])
    return result


def _secondary(rows, grid):
    summary = lambda group: bb001._summary(group, grid, 21)
    intensity = {}
    for year in sorted({row["public_date"][:4] for row in rows}):
        group = [row for row in rows if row["public_date"].startswith(year)]
        finite = [row["intensity"] for row in group if row["intensity"] is not None]
        boundaries = bb001.empirical_boundaries(finite, 3) if finite else []
        intensity[year] = {"boundaries": boundaries, "undefined": len(group) - len(finite),
            "groups": {str(bucket): summary([row for row in group if row["intensity"] is not None
                        and bb001.lower_bin(row["intensity"], boundaries) == bucket]) for bucket in range(3)}}
    return {"used_for_judgement": False,
        "receipt_pre_2026_03_06": summary([row for row in rows if row["public_date"] < "2026-03-06"]),
        "receipt_on_or_after_2026_03_06": summary([row for row in rows if row["public_date"] >= "2026-03-06"]),
        "holding_includes_2026_03_06": sum(row["entry_date"] <= "2026-03-06" <= row["exit_date"] for row in rows),
        "cancel_tag": {str(tag): summary([row for row in rows if row["cancel"] == tag]) for tag in (True, False)},
        "gap_mean": float(np.mean([row["stock"]["gap"] for row in rows])) if rows else None,
        "intensity_terciles_by_receipt_year": intensity}


def _recheck(prepared, reader):
    report = prepared["report"]
    for row in report["prices"]["inventory"]:
        path = Path(row["path"])
        if not path.is_file() or not row["size"] or path.stat().st_size != row["size"]:
            raise ValueError("BB002 required official-session price inventory changed: " + row["date"])
    for path, expected in {**report["code_hashes"], **report["source_hashes"], **reader.source_hashes}.items():
        if path in reader.source_hashes:
            _guard(pd.Timestamp(Path(path).stem), reader.repo_root)
        if bb001._sha(path) != expected:
            raise ValueError("BB002 source changed during evaluation: " + path)
    if _code_hashes() != report["code_hashes"]:
        raise ValueError("BB002 code changed during evaluation")
    listing = dart.listing_status(reader.data_dir, price_end=reader.window.end, experiment_id=EXPERIMENT_ID)
    if listing["listing_version"] != report["listing_version"]:
        raise ValueError("BB002 listing version changed during evaluation")
    current = dart.listing_files(reader.data_dir, from_date=report["listing_coverage"]["start"], to_date=reader.window.end)
    if current != prepared["files"]:
        raise ValueError("BB002 listing inventory changed during evaluation")


def _calculate(prepared, *, data_dir, repo_root, window, prices=None):
    _require_ready(prepared)
    started = time.perf_counter()
    reader = PriceReader(data_dir, prepared["sessions"], window=window, prices=prices, repo_root=repo_root)
    prepared["price_hashes"] = reader.source_hashes
    frame_hash = lambda: hashlib.sha256(pd.util.hash_pandas_object(prices.sort_index(), index=True).values.tobytes()).hexdigest()
    supplied = frame_hash() if prices is not None else None
    try:
        # Validate/hash every official file through the vanish horizon inside
        # the session, even when no selected position has a missing exit quote.
        reader.last_trades()
        selected, filters = bb001.select_events(prepared["events"], reader, prepared["listings"], 21, cutoff=None)
        rows = bb001.observations(selected, reader, cutoff=None)
        result, loss = (_metrics(rows, prepared["grid"], worthless=worthless) for worthless in (False, True))
        judge(result, loss)
        result.update(secondary=_secondary(rows, prepared["grid"]), per_event=rows)
        _recheck(prepared, reader)
        if supplied is not None and supplied != frame_hash():
            raise ValueError("BB002 supplied price frame changed during evaluation")
        report = prepared["report"]
        payload = {"experiment_id": EXPERIMENT_ID, "stage": "holdout" if window == CONFIRMATION else "rehearsal",
            "selected_variant": "d21", "verdict": result["verdict"], "reasons": result["reasons"],
            "variants": {"d21": result}, "delisting_sensitivity": {"d21": loss},
            "prereg_commit": prepared["registration"].commit_hash, "comparison": report["comparison"],
            "window": vars(window), "hac_grid": report["hac_grid"], "calendar_version": trading_calendar.CALENDAR_VERSION,
            "counts": {**report["counts"], "entry_filters": filters}, "listing_version": report["listing_version"],
            "code_hashes": report["code_hashes"], "source_hashes": {**report["source_hashes"], **reader.source_hashes},
            "supplied_price_frame_sha256": supplied, "interpretation": INTERPRETATION,
            "performance": {"runtime_seconds": time.perf_counter() - started,
                            "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
        json.dumps(payload, allow_nan=False)
        return payload
    finally:
        reader._load.cache_clear()


def _write(payload, directory):
    lines = [f"# {EXPERIMENT_ID}", "", f"{payload['stage']}: {payload['verdict']}", "",
             INTERPRETATION, "", "Reasons: " + "; ".join(payload["reasons"])]
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    return common.write_result_files(directory, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"), encoded, lines)


def rehearse(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, sessions=None, prices=None):
    prepared = _prepare(data_dir=data_dir, repo_root=repo_root, window=REHEARSAL, sessions=sessions)
    payload = _calculate(prepared, data_dir=data_dir, repo_root=repo_root, window=REHEARSAL, prices=prices)
    path, _ = _write(payload, Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "rehearsal")
    return {"experiment_id": EXPERIMENT_ID, "stage": "rehearsal", "passed": True,
            "result_file": str(path), "result_sha256": bb001._sha(path), "code_hashes": payload["code_hashes"]}


def _unused(repo_root):
    if (EXPERIMENT_ID in hc003.ledger_experiments(repo_root)
            or any(row["experiment_id"] == EXPERIMENT_ID for row in hc003.registry_rows(repo_root))):
        raise ValueError("BB002 already used or terminally recorded; cannot reopen")


def _publish(payload, *, repo_root, registration, aborted=False):
    metrics = {"stage": "holdout", "run_id": payload["run_id"], "combined_verdict": payload["verdict"],
               "comparison_sha256": COMPARISON_SHA256}
    if aborted:
        metrics["abort"] = payload["abort"]
        experiment_registry.verify_captured_preregistration(EXPERIMENT_ID, registration, repo_root)
    else:
        _registration(repo_root)
        metrics.update(payload["variants"]["d21"]["judgement_inputs"])
        metrics["delisting_sensitivity"] = payload["variants"]["d21"]["delisting_sensitivity"]
    experiment_registry.encode_metrics(metrics)
    path, _ = _write(payload, Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results")
    metrics.update(result_file=str(path.relative_to(repo_root)), result_sha256=bb001._sha(path))
    experiment_registry.record_trials(EXPERIMENT_ID, [("holdout_d21", metrics, payload["verdict"])], repo_root=repo_root,
        aborted_registration=registration if aborted else None,
        note="BB002 shared-period historical confirmation; not an independent holdout; d21 only")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, sessions=None, prices=None):
    with ordering.execution_lock(repo_root):
        registration = _registration(repo_root)
        _unused(repo_root)
        prepared = _prepare(data_dir=data_dir, repo_root=repo_root, window=CONFIRMATION, sessions=sessions)
        _require_ready(prepared)  # In particular, all KRX sessions through Sep 30.
        rehearsal = rehearse(data_dir=data_dir, repo_root=repo_root, sessions=sessions)
        if rehearsal.get("passed") is not True or rehearsal["code_hashes"] != prepared["report"]["code_hashes"]:
            raise ValueError("BB002 requires a full successful rehearsal with the same code before opening")
        # Rehearsal may take time. Recheck frozen non-price inputs and price
        # paths/sizes immediately before claiming; protected hashes wait inside.
        _recheck(prepared, PriceReader(data_dir, prepared["sessions"], repo_root=repo_root))
        run_id = uuid.uuid4().hex
        _unused(repo_root)
        try:
            with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=repo_root):
                payload = _calculate(prepared, data_dir=data_dir, repo_root=repo_root, window=CONFIRMATION, prices=prices)
            payload.update(run_id=run_id, pre_open_rehearsal=rehearsal)
            return _publish(payload, repo_root=repo_root, registration=registration)
        except BaseException as exc:
            if EXPERIMENT_ID not in hc003.ledger_experiments(repo_root):
                raise
            report = prepared["report"]
            hashes = {**report["source_hashes"], **prepared.get("price_hashes", {})}
            payload = {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "d21",
                "verdict": "holdout_aborted", "reasons": [f"{type(exc).__name__}: {exc}"], "run_id": run_id,
                "prereg_commit": registration.commit_hash, "comparison": report["comparison"],
                "code_hashes": report["code_hashes"], "source_hashes": hashes,
                "pre_open_rehearsal": rehearsal, "interpretation": INTERPRETATION,
                "abort": ordering.abort_details({"code": report["code_hashes"], "sources": hashes}, exc)}
            _publish(payload, repo_root=repo_root, registration=registration, aborted=True)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    for mode in ("precheck", "rehearsal", "execute"):
        modes.add_argument("--" + mode, action="store_true")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        payload = run_experiment(data_dir=args.data_dir)
        result = {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "verdict": payload["verdict"]}
    elif args.rehearsal:
        result = rehearse(data_dir=args.data_dir)
    else:
        result = precheck(data_dir=args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
