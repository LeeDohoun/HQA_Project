"""EV001 filing-entry cohorts. Offline dry run by default; holdout is explicit."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import importlib.metadata
import json
import platform
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, holdout, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import av001, common, hc001, hc002, hc003, hf001, ordering
from src.ingestion import dart_financial_date_rules as financial_dates
from src.ingestion import dart_listing, dart_quarterly
from src.runner import trading_calendar
from src.ingestion.storage import read_rows


EXPERIMENT_ID = "EV001_earnings_filing_entry"
VARIANTS = ("ev_new20", "ev_new60", "ev_new20_f")
HORIZONS = dict(zip(VARIANTS, (20, 60, 20)))
RULES = ("S", "O")
N_CONTROLS = 200
MAX_RSS_BYTES = 7_000_000_000
BOUNDS = {"design_validation": (pd.Timestamp("2017-05-02"), hc003.DESIGN_END),
          "holdout": (pd.Timestamp("2026-01-02"), hc003.HOLDOUT_END)}
INTERPRETATIONS = [
    "The merged listing supplies receipt provenance and L, including later listing metadata. Frozen-target coverage checks retain the complete file inventory but hash price contents only through the stage end and never open protected price contents. Financial values are gated by actual public date before derivation, under both S and O. Latest stored revisions are not historical vintages.",
    "An event uses the preferred CFS (otherwise OFS) known on d for q; all other quarters are strictly before d. Same-day older quarters cannot supply event derivation or comparison inputs. A newly published OFS superseded by an already known CFS is not a new selected version. Aliases are separate stock events; identical stock/quarter/date events are deduplicated.",
    "The correction-link sample is stage-eligible saved versions known under at least one rule before the evaluation end. Date exclusions count saved versions by receipt year; event exclusions count stock/quarter/date candidates by public-date year, with sequential first-failure reasons. Existing quarterly same-division/currency derivation and its dependency availability at each filing are preserved.",
    "V_tau is frozen at the final exchange session of the preceding calendar month using unchanged HC002 eligibility, industry coverage policy and strict financial availability. Event-time financial information never updates V_tau.",
    "HF001 volatility uses exactly 60 verified returns ending at the last session <=d, market-specific max-tie ranks in V_tau, and leaves missing volatility unfiltered with counts. Raise coverage is HF001's full calendar-day window evaluated at tau, so same-day/weekend filings effective at tau are included. Missing listing coverage excludes only that event from ev_new20_f.",
    "Entry requires HC003 3.1 price/base validity, positive volume and no upper-limit open. A lower-limit open alone is not an EV001 entry exclusion. Missing required execution market/ADV is an explicit error, never a stale cost substitute.",
    "HF001 permissible close valuations exclude lower-limit closes. Resumption/delisting classification stops at the stage end; sale costs use the valuation day's close/market/prior-20-session ADV. Overlap lasts through the scheduled exit close, including a missing-exit valuation. No holding period is extended.",
    "Controls reset default_rng(0) per variant and rule, draw path then entry date from sorted executable V_tau excluding that date's final event stocks and path holdings, and retain positions through scheduled exits. Baseline and -100% use the same stored draws. A shortage makes the entire control uncomputable, without partial resampling.",
    "Monthly observations weight events equally within entry month, then months equally. Calendar NW(3) preserves gaps, divides covariance by valid N and has no finite-sample correction. Design-only reference effects use entry months 2017-05..2024-12, including in-bound exits in 2025.",
    "Holm always has three variants per rule; fewer than 300 events/36 event months or undefined SE supplies p=1. Existing EV001 rows plus this run's three rows set the strict t threshold. The 2025 sign and all subgroups are diagnostics.",
    "After separate baseline/-100% rule verdicts, a changed verdict is delisting_sensitive. Combined priority is sensitivity_unavailable, delisting_sensitive, one-rule pass/version_sensitive, insufficient, fail. Both rule passes are required. Holdout uses its own minimum and effect gate; t, costs x1.5 and random controls are reports only.",
    "Secondary gross paths use each variant's final event set, omit horizons outside the stage boundary and do not alter primary overlap selection. An unavailable member outcome makes the entire date/horizon diagnostic unavailable without shrinking its benchmark. Same-day maintenance is restricted to final new-entrant entry dates before independent entry/filter/overlap selection; all-period maintenance is labelled separately. Both are report-only. Capacity is the 1%-ADV limit for an equal-weight entry-date cohort, constrained by both execution sides.",
    "Reproduction checks content hashes and deterministic results, excluding HEAD and performance. Holdout freezes all inputs before opening, verifies protected price hashes only inside one EV001 session, then re-inventories/re-hashes after computation under the shared ordering lock. An unavailable O sensitivity is not a registered holdout terminal release; stop and report without inventing one.",
]


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (not registration.commit_hash.startswith("7acd752") or registration.fields["variants_planned"] != 3
            or not registration.fields["uses_holdout"] or registration.fields["uses_llm"]):
        raise ValueError("EV001 requires the immutable 7acd752 preregistration and its three fixed variants")
    for name in (hc002.EXPERIMENT_ID, hc003.EXPERIMENT_ID, hf001.EXPERIMENT_ID):
        experiment_registry.verify_preregistration(name, repo_root)
    return registration


def _check_memory():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    if peak > MAX_RSS_BYTES:
        raise MemoryError(f"EV001 exceeded its 7 GB RSS budget: {peak} bytes")


class Counts:
    def __init__(self):
        self.years = defaultdict(Counter)

    def add(self, day, reason, count=1):
        self.years[str(day)[:4]][reason] += count

    def report(self):
        total = Counter()
        for counts in self.years.values():
            total.update(counts)
        return {"total": dict(sorted(total.items())),
                "by_year": {year: dict(sorted(counts.items())) for year, counts in sorted(self.years.items())}}


def _preferred(rows):
    selected = {}
    for row in rows:
        key = row["fiscal_quarter"]
        rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
        if key not in selected or rank > selected[key][0]:
            selected[key] = rank, row
    return {key: value[1] for key, value in selected.items()}


def filing_events(versions, dates, sessions, *, bounds=None):
    """Build event-time phases with the newly published q and strictly older inputs."""
    first, end = BOUNDS[versions.stage] if bounds is None else bounds
    cutoff = versions.cutoff
    companies, audit = defaultdict(list), Counts()
    for path in versions.paths:
        for raw in read_rows(path):
            info = dates.annotate(raw)
            actual = info["actual_public_date"]
            if actual is not None and actual < cutoff:
                public = actual if versions.rule == "S" else info["assumed_public_date"]
                companies[raw["corp_code"]].append({**raw, "available_date": public, "provenance": info})
    events = []
    for corp, records in sorted(companies.items()):
        inputs, candidates = [], []
        keys = sorted({(row["fiscal_quarter"], row["available_date"]) for row in records}, key=lambda x: (x[1], x[0]))
        for quarter, day in keys:
            selected = _preferred(row for row in records if row["fiscal_quarter"] == quarter and row["available_date"] <= day)[quarter]
            aliases = selected["stock_codes"]
            pos = sessions.searchsorted(pd.Timestamp(day), side="right")
            if pos == len(sessions) or sessions[pos] < first or sessions[pos] > end:
                audit.add(day, "entry_outside_stage", len(aliases))
                continue
            if selected["available_date"] != day:
                audit.add(day, "superseded_by_CFS", len(aliases))
                continue
            older = [row for row in records if row["available_date"] < day]
            if any(row["fiscal_quarter"] > quarter for row in older):
                audit.add(day, "not_latest_quarter", len(aliases))
                continue
            # _derive retains same-division/currency checks; same-day other
            # quarters have already been removed from its entire dependency graph.
            derived = _preferred(dart_quarterly._derive([*older, selected]))
            token = f"{corp}:{len(candidates)}"
            q = pd.Period(quarter, freq="Q")
            for period, row in derived.items():
                if q - 6 <= pd.Period(period, freq="Q") <= q:
                    inputs.append({key: (token if key == "corp_code" else aliases[0] if key == "stock_code" else row[key])
                                   for key in dart_quarterly.COLUMNS})
            candidates.append((token, q, day, sessions[pos], selected))
        if not inputs:
            continue
        frame = pd.DataFrame(inputs, columns=dart_quarterly.COLUMNS)
        for metric in dart_quarterly.ACCOUNTS:
            frame[metric] = pd.to_numeric(frame[metric]).astype(float)
        signals = hc001.phase_signals(frame).set_index(["corp_code", "fiscal_quarter"])
        for token, q, day, entry, selected in candidates:
            current = signals.loc[(token, str(q))]
            prior_key = token, str(q - 1)
            prior = signals.loc[prior_key] if prior_key in signals.index else None
            if not current.computable:
                reason = "current_phase_uncomputable"
            elif pd.isna(current.phase) or current.phase != 2:
                reason = "current_not_phase2"
            elif prior is None or not prior.computable:
                reason = "prior_phase_uncomputable"
            else:
                prior_phase = int(prior.phase) if pd.notna(prior.phase) else "none"
                reason = "maintenance" if prior_phase == 2 else "new_entrant"
            audit.add(day, reason, len(selected["stock_codes"]))
            if reason not in ("maintenance", "new_entrant"):
                continue
            for code in selected["stock_codes"]:
                events.append({"stock_code": code, "corp_code": corp, "fiscal_quarter": str(q),
                    "public_date": day, "entry_date": entry.date().isoformat(),
                    "entry_month": str(entry.to_period("M")), "prior_phase": prior_phase,
                    "kind": reason, "fs_div": selected["fs_div"], "rcept_no": selected["rcept_no"],
                    **selected["provenance"]})
        _check_memory()
    unique = {}
    for event in events:
        key = event["stock_code"], event["fiscal_quarter"], event["public_date"]
        if key in unique and unique[key] != event:
            raise ValueError("conflicting stock-quarter-date event")
        unique[key] = event
    return sorted(unique.values(), key=lambda e: (e["entry_date"], e["stock_code"], e["fiscal_quarter"], e["public_date"])), audit.report()


def decision_date(entry, sessions):
    month = pd.Timestamp(entry).to_period("M") - 1
    days = sessions[sessions.to_period("M") == month]
    if not len(days):
        raise ValueError(f"missing previous month-end for {entry}")
    return days[-1]


class EventPrices:
    """Compact per-stock numeric arrays; bounded raw windows, no quote objects."""
    FIELDS = ("open", "close", "volume", "base_price", "ret_1d", "trading_value")

    def __init__(self, sessions, end, repo_root):
        self.days = sessions[sessions <= end]
        self.end, self.repo_root = pd.Timestamp(end), repo_root
        self.raw, self.market, self.verified = {}, {}, {}
        self.adjusted, self.adv, self.last_close, self.last_trade = {}, {}, {}, {}
        self.counts = Counter()

    def add(self, prices):
        common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=self.repo_root)
        if prices.index.has_duplicates or not set(self.FIELDS).issubset(prices.columns):
            raise ValueError("EV001 requires unique price keys and all accounting fields")
        for code, group in prices.groupby(level="stock_code", observed=True, sort=True):
            frame = group.droplevel("stock_code")
            positions = self.days.get_indexer(frame.index)
            if (positions < 0).any():
                raise ValueError("price outside stage calendar")
            if code not in self.raw:
                self.raw[code] = np.full((len(self.days), len(self.FIELDS)), np.nan)
                self.market[code] = np.zeros(len(self.days), dtype=np.int8)
                self.verified[code] = np.zeros(len(self.days), dtype=bool)
            self.raw[code][positions] = frame[list(self.FIELDS)].to_numpy(dtype=float)
            self.market[code][positions] = frame.market.map({"KOSPI": 1, "KOSDAQ": 2}).fillna(0).to_numpy(dtype=np.int8)
            self.verified[code][positions] = frame.calendar_status.eq("verified").to_numpy()
        _check_memory()

    def finish(self):
        thresholds = np.array([hf001.price_limit(day) - .005 for day in self.days])
        positions = np.arange(len(self.days), dtype=np.int32)
        for code, raw in self.raw.items():
            opening, closing, volume, bases, returns, value = raw.T
            bases[:] = np.where(np.isnan(bases), np.r_[np.nan, closing[:-1]], bases)
            valid = np.isfinite(closing) & (closing > 0) & np.isfinite(bases) & (bases > 0)
            if np.isinf(returns).any() or (returns[np.isfinite(returns)] < -1).any():
                raise ValueError(f"invalid adjusted return for {code}")
            fallback = valid & np.isnan(returns)
            factors = np.ones(len(raw))
            factors[valid] = 1 + returns[valid]
            factors[fallback] = closing[fallback] / bases[fallback]
            first = np.flatnonzero(np.isfinite(closing) & (closing > 0))
            if not len(first):
                self.adjusted[code] = np.full(len(raw), np.nan)
            else:
                factors[:first[0] + 1] = 1.0
                adjusted = np.cumprod(factors)
                adjusted[:first[0]] = np.nan
                self.adjusted[code] = adjusted
                self.counts["unadjusted_fallback"] += int(fallback[first[0] + 1:].sum())
            values = np.where(self.verified[code] & np.isfinite(value) & (value >= 0), value, np.nan)
            self.adv[code] = pd.Series(values).shift(1).rolling(20, min_periods=20).mean().to_numpy()
            down = np.isfinite(bases) & (bases > 0) & (closing <= bases * (1 - thresholds))
            trades = np.isfinite(closing) & (closing > 0) & np.isfinite(volume) & (volume > 0)
            permissible = valid & ~down
            self.last_close[code] = np.maximum.accumulate(np.where(permissible, positions, -1))
            self.last_trade[code] = np.maximum.accumulate(np.where(permissible & trades, positions, -1))
            self.counts["missing_base"] += int((np.isfinite(closing) & ~np.isfinite(bases)).sum())
        _check_memory()

    def entry_reason(self, code, pos):
        if code not in self.raw:
            return "missing_entry_price"
        opening, closing, volume, base = self.raw[code][pos, :4]
        if not np.isfinite(opening) or opening <= 0:
            return "missing_entry_price"
        if not np.isfinite(volume) or volume <= 0:
            return "untradable_entry"
        if not np.isfinite(closing) or closing <= 0 or not np.isfinite(base) or base <= 0:
            return "missing_entry_close_or_base"
        if opening >= base * (1 + (hf001.price_limit(self.days[pos]) - .005)):
            return "limit_up_entry"
        return None

    def volatility(self, codes, pos):
        sigma, markets = [], []
        for code in codes:
            if code not in self.raw or pos < 59:
                sigma.append(np.nan)
                markets.append(None)
                continue
            returns = self.raw[code][pos - 59:pos + 1, 4]
            valid = self.verified[code][pos - 59:pos + 1] & np.isfinite(returns) & (returns > -1)
            sigma.append(float(np.log1p(returns).std(ddof=1)) if valid.all() else np.nan)
            markets.append({1: "KOSPI", 2: "KOSDAQ"}.get(int(self.market[code][pos])))
        frame = pd.DataFrame({"volatility_60d": sigma, "market": markets}, index=codes)
        frame["percentile"] = frame.groupby("market").volatility_60d.rank(method="max", pct=True)
        frame["f_vol"] = frame.percentile.gt(.80)
        return frame

    def outcomes(self, codes, entry, horizon, *, costs=True, reporting=False):
        target = entry + horizon - 1
        if entry < 0 or target >= len(self.days):
            raise ValueError("outcome horizon exceeds stage boundary")
        rows, unavailable = [], []
        for code in codes:
            if self.entry_reason(code, entry) is not None:
                raise ValueError("outcomes require an executable entry")
            raw = self.raw[code]
            opening, closing, volume, base = raw.T[:4]
            trades = np.isfinite(closing) & (closing > 0) & np.isfinite(volume) & (volume > 0)
            resumed = bool(trades[target + 1:].any())
            delisted = not trades[target] and not resumed
            actual = int((self.last_trade if delisted else self.last_close)[code][target])
            if actual < entry:
                if reporting:
                    unavailable.append({"stock_code": code, "reason": "no_permissible_exit_on_or_after_entry"})
                    continue
                raise ValueError(f"entered stock lacks a permissible exit: {code}")
            adjusted_open = self.adjusted[code][entry] * opening[entry] / closing[entry]
            gross = self.adjusted[code][actual] / adjusted_open - 1
            if not np.isfinite(gross):
                if reporting:
                    unavailable.append({"stock_code": code, "reason": "undefined_adjusted_open_to_close_return"})
                    continue
                raise ValueError("undefined adjusted open-to-close return")
            row = {"stock_code": code, "gross": float(gross), "gross_loss": -1.0 if delisted else float(gross),
                   "exit_position": actual, "delisted": bool(delisted),
                   "halted_exit": bool(not trades[target] and resumed), "stale_exit": actual != target,
                   "limit_down_exit": bool(closing[target] <= base[target] * (1 - (hf001.price_limit(self.days[target]) - .005)))}
            if costs:
                rates = {}
                try:
                    for scenario, (version, multiplier) in hc003.SCENARIOS.items():
                        sides = []
                        for side, pos, price in (("buy", entry, opening[entry]), ("sell", actual, closing[actual])):
                            market = {1: "KOSPI", 2: "KOSDAQ"}.get(int(self.market[code][pos]))
                            adv = float(self.adv[code][pos])
                            if market is None or not np.isfinite(adv):
                                raise ValueError(f"missing {side} cost market/prior-20-session ADV: {code} {self.days[pos].date()}")
                            sides.append(cost_model.one_way_cost(float(price), market, self.days[pos].date(), adv, side,
                                                                model_version=version, multiplier=multiplier))
                        buy, sell = sides
                        rates[scenario] = (buy, sell)
                        row[scenario] = (1 + gross) * (1 - sell) / (1 + buy) - 1
                        row[scenario + "_loss"] = (1 + row["gross_loss"]) * (1 - sell) / (1 + buy) - 1
                except ValueError as exc:
                    if not reporting:
                        raise
                    unavailable.append({"stock_code": code, "reason": "cost_inputs_unavailable", "detail": str(exc)})
                    continue
                row.update(buy_cost=rates["v2_1.0"][0], sell_cost=rates["v2_1.0"][1],
                           entry_adv=float(self.adv[code][entry]), exit_adv=float(self.adv[code][actual]),
                           market={1: "KOSPI", 2: "KOSDAQ"}[int(self.market[code][entry])])
            rows.append(row)
        table = pd.DataFrame(rows).set_index("stock_code") if rows else pd.DataFrame()
        if unavailable:
            table.attrs["unavailable"] = unavailable
        return table


def prepare(*, data_dir, repo_root, sessions, stage, prices=None, dry=False, bounds=None):
    first, end = BOUNDS[stage] if bounds is None else bounds
    status = dart_listing.listing_status(data_dir, price_end=end)
    if not dry and not status["supplement_complete"]:
        raise ValueError("DART listing supplement incomplete: " + str(status["reason"]))
    dates = financial_dates.FilingDates.from_data(data_dir)
    versions, events, event_counts = {}, {}, {}
    for rule in RULES:
        versions[rule] = financial_dates.FinancialVersions(data_dir, dates, rule, stage, sample_end=end, cutoff=None if bounds is None else end + pd.Timedelta(days=1))
        events[rule], event_counts[rule] = filing_events(versions[rule], dates, sessions, bounds=(first, end))
    decisions = {decision_date(e["entry_date"], sessions) for r in RULES for e in events[r]}
    universes, universe_counts = {r: {} for r in RULES}, {r: [] for r in RULES}
    book = EventPrices(sessions, end, repo_root)
    available, tail, seen = [], None, pd.DatetimeIndex([])
    if prices is not None:
        if prices.empty or common._sessions(prices).max() > end:
            raise ValueError("supplied prices exceed the registered stage end")
        common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
    for quarter in sorted(set(book.days.to_period("Q"))):
        days = book.days[book.days.to_period("Q") == quarter]
        if prices is None:
            frame, files = hc003.load_prices(data_dir, days[0], days[-1], repo_root=repo_root, experiment_id=EXPERIMENT_ID)
            available.extend(files)
        else:
            frame = prices.loc[prices.index.get_level_values("trade_date").isin(days)]
        actual = common._sessions(frame)
        if not actual.equals(days):
            raise ValueError("missing whole-market price sessions; cannot classify stock disappearances")
        seen = seen.append(actual)
        history = frame if tail is None else pd.concat([tail, frame]).sort_index()
        for day in sorted(decisions.intersection(set(days))):
            pos = sessions.get_loc(day)
            required = sessions[max(0, pos - 19):pos + 1]
            if pos < 19 or len(required.difference(common._sessions(history))):
                raise ValueError("incomplete HC002 decision history")
            local = history.loc[history.index.get_level_values("trade_date").isin(required)]
            for rule in RULES:
                with common.guarded_reads(EXPERIMENT_ID):
                    u, report = hc002.universe_on_decision(local, day, data_dir=data_dir, repo_root=repo_root,
                                                          financial_loader=versions[rule].load)
                universes[rule][day.date().isoformat()] = u.sort_index()
                universe_counts[rule].append({"decision_date": day.date().isoformat(), **report})
        book.add(frame)
        tail = history.loc[history.index.get_level_values("trade_date").isin(common._sessions(history)[-20:])].copy()
    if not seen.equals(book.days):
        raise ValueError("incomplete stage price coverage")
    book.finish()
    listings = dates.event_listings()
    listings = listings.loc[listings.rcept_dt < end.strftime("%Y%m%d")]
    raised, raise_counts = hf001.rights_events(listings, sessions)
    coverage = {}
    files = dart_listing.listing_paths(data_dir)
    for rule in RULES:
        last_key, volatility = None, None
        for event in events[rule]:
            entry = pd.Timestamp(event["entry_date"])
            decision = decision_date(entry, sessions).date().isoformat()
            event["decision_date"] = decision
            pos = sessions.searchsorted(pd.Timestamp(event["public_date"]), side="right") - 1
            key = decision, pos
            if key != last_key:
                volatility = book.volatility(universes[rule][decision].index, pos)
                last_key = key
            code = event["stock_code"]
            row = volatility.loc[code] if code in volatility.index else None
            event["f_vol"] = bool(row.f_vol) if row is not None else False
            event["volatility_uncomputable"] = row is None or pd.isna(row.volatility_60d)
            event["volatility_60d"] = float(row.volatility_60d) if row is not None and pd.notna(row.volatility_60d) else None
            event["volatility_percentile"] = float(row.percentile) if row is not None and pd.notna(row.percentile) else None
            entry_pos = sessions.get_loc(entry)
            if event["entry_date"] not in coverage:
                coverage[event["entry_date"]] = hf001.listing_coverage(entry, sessions, files, data_dir=data_dir, status=status)
            event["raise_covered"] = coverage[event["entry_date"]]["covered"]
            event["f_raise"] = bool(((raised.stock_code == code) & raised.known_position.between(entry_pos - 125, entry_pos)).any())
    return {"book": book, "versions": versions, "dates": dates, "events": events,
            "universes": universes, "universe_counts": universe_counts, "event_counts": event_counts,
            "coverage": coverage, "listing_status": status, "raise_counts": raise_counts, "price_paths": available}


def select_events(prepared, rule, variant, *, kind="new_entrant", entry_dates=None):
    book, horizon = prepared["book"], HORIZONS[variant]
    selected, held_until, counts = [], {}, Counts()
    for event in prepared["events"][rule]:
        if event["kind"] != kind:
            continue
        if entry_dates is not None and event["entry_date"] not in entry_dates:
            continue
        code, entry = event["stock_code"], book.days.get_loc(pd.Timestamp(event["entry_date"]))
        target = entry + horizon - 1
        counts.add(event["public_date"], "candidates")
        u = prepared["universes"][rule][event["decision_date"]]
        reason = None
        if target >= len(book.days):
            reason = "scheduled_exit_outside_stage"
        elif code not in u.index:
            reason = "outside_frozen_universe"
        else:
            reason = book.entry_reason(code, entry)
        if reason is None and variant == "ev_new20_f":
            if event["volatility_uncomputable"]:
                counts.add(event["public_date"], "volatility_uncomputable")
            if not event["raise_covered"]:
                reason = "listing_coverage_unusable"
            elif event["f_vol"] or event["f_raise"]:
                reason = "filtered_vol_and_raise" if event["f_vol"] and event["f_raise"] else "filtered_vol" if event["f_vol"] else "filtered_raise"
        if reason is None and code in held_until and entry <= held_until[code]:
            reason = "holding_overlap"
        if reason is not None:
            counts.add(event["public_date"], reason)
            continue
        held_until[code] = target
        selected.append({**event, "scheduled_exit_date": book.days[target].date().isoformat()})
        counts.add(event["public_date"], "selected")
    return selected, counts.report()


def monthly_summary(rows, key="excess"):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["entry_month"]].append(row[key])
    months = [{"month": month, "events": len(values), "mean": float(np.mean(values))}
              for month, values in sorted(grouped.items())]
    return {"event_count": len(rows), "entry_dates": len({row["entry_date"] for row in rows}),
            "primary": av001.newey_west([row["month"] for row in months], [row["mean"] for row in months]),
            "per_month": months}


def unavailable_path(table, day, horizon, events, benchmark_members):
    missing = table.attrs.get("unavailable", [])
    if not missing:
        return None
    return {"status": "unavailable", "used_for_judgement": False, "entry_date": day, "horizon": horizon,
            "reason": "benchmark_member_outcome_unavailable", "members": missing,
            "counts": {"events": events, "benchmark_members": benchmark_members,
                       "unavailable_members": len(missing),
                       "by_reason": dict(Counter(row["reason"] for row in missing))}}


def outcome_tables(prepared, rule, selected, horizon, *, reporting=False):
    book = prepared["book"]
    tables = {}
    event_counts = Counter(event["entry_date"] for event in selected)
    for event in selected:
        day = event["entry_date"]
        if day in tables:
            continue
        entry = book.days.get_loc(pd.Timestamp(day))
        u = prepared["universes"][rule][event["decision_date"]]
        codes = [code for code in u.index if book.entry_reason(code, entry) is None]
        table = book.outcomes(codes, entry, horizon, reporting=reporting)
        missing = unavailable_path(table, day, horizon, event_counts[day], len(codes))
        if missing is not None:
            # Preserve the benchmark: no mean over its available subset.
            tables[day] = {"unavailable": missing}
            continue
        if table.empty:
            raise ValueError("event benchmark has no executable members")
        tables[day] = {"frame": table, "benchmark": float(table.gross.mean()),
                       "benchmark_loss": float(table.gross_loss.mean()),
                       "dropped_members": len(u) - len(codes)}
    return tables


def observations(book, selected, tables):
    rows = []
    for event in selected:
        table = tables[event["entry_date"]]
        if "unavailable" in table:
            continue
        result = table["frame"].loc[event["stock_code"]].to_dict()
        result["exit_date"] = book.days[int(result.pop("exit_position"))].date().isoformat()
        row = {**event, **result, "benchmark": table["benchmark"], "benchmark_loss": table["benchmark_loss"],
               "benchmark_dropped_members": table["dropped_members"]}
        for scenario in hc003.SCENARIOS:
            row["excess_" + scenario] = row[scenario] - row["benchmark"]
            row["excess_" + scenario + "_loss"] = row[scenario + "_loss"] - row["benchmark_loss"]
        row["excess"] = row["excess_v2_1.0"]
        row["excess_loss"] = row["excess_v2_1.0_loss"]
        rows.append(row)
    return rows


def random_controls(book, selected, tables, horizon, actual_mean, actual_loss_mean):
    grouped = defaultdict(list)
    for event in selected:
        grouped[event["entry_date"]].append(event["stock_code"])
    code_table = sorted({code for table in tables.values() for code in table["frame"].index})
    indices = {code: pos for pos, code in enumerate(code_table)}
    dates = sorted(grouped)
    rng, draws, means, loss_means, shortages = np.random.default_rng(0), [], [], [], []
    # Arrays/index maps are per entry date, shared by all 200 paths.
    arrays = {}
    for day in dates:
        table = tables[day]
        frame = table["frame"]
        codes = frame.index.tolist()
        arrays[day] = (codes, {code: pos for pos, code in enumerate(codes)},
                       frame["v2_1.0"].to_numpy() - table["benchmark"],
                       frame["v2_1.0_loss"].to_numpy() - table["benchmark_loss"])
    for path in range(N_CONTROLS):
        held, path_draws, monthly, monthly_loss = {}, [], defaultdict(list), defaultdict(list)
        for day in dates:
            entry = book.days.get_loc(pd.Timestamp(day))
            held = {code: exit_pos for code, exit_pos in held.items() if exit_pos >= entry}
            codes, positions, values, losses = arrays[day]
            excluded = set(grouped[day]) | set(held)
            candidates = [code for code in codes if code not in excluded]
            count = len(grouped[day])
            if len(candidates) < count:
                shortages.append({"path": path, "entry_date": day, "required": count, "available": len(candidates)})
                break
            chosen = rng.choice(candidates, size=count, replace=False).tolist()
            path_draws.append([indices[code] for code in chosen])
            positions_chosen = [positions[code] for code in chosen]
            month = day[:7]
            monthly[month].extend(values[positions_chosen].tolist())
            monthly_loss[month].extend(losses[positions_chosen].tolist())
            held.update({code: entry + horizon - 1 for code in chosen})
        draws.append(path_draws)
        complete = len(path_draws) == len(dates) and bool(dates)
        means.append(float(np.mean([np.mean(v) for _, v in sorted(monthly.items())])) if complete else None)
        loss_means.append(float(np.mean([np.mean(v) for _, v in sorted(monthly_loss.items())])) if complete else None)
    def result(values, actual):
        complete = bool(dates) and all(value is not None for value in values)
        return {"computable": complete, "path_means": values,
                "share_of_controls": float(np.mean(np.array(values) >= actual)) if complete and actual is not None else None,
                "shortages": shortages, "seed": 0, "paths": N_CONTROLS, "draw_order": "path/entry_date/code",
                "reason": "candidate_shortage" if shortages else "no_events" if not dates else None}
    main, loss = result(means, actual_mean), result(loss_means, actual_loss_mean)
    main.update(draws={"code_table": code_table, "entry_dates": dates, "paths": draws,
                       "encoding": "paths[path][entry_date_index] contains indices into code_table"})
    loss["draws_reference"] = "same variant/rule primary random_control.draws"
    return main, loss


def variant_metrics(prepared, rule, variant, selected):
    book = prepared["book"]
    tables = outcome_tables(prepared, rule, selected, HORIZONS[variant])
    rows = observations(book, selected, tables)
    controls = random_controls(book, selected, tables, HORIZONS[variant],
                               monthly_summary(rows)["primary"]["mean"], monthly_summary(rows, "excess_loss")["primary"]["mean"])
    results = []
    for loss, control in zip((False, True), controls):
        suffix = "_loss" if loss else ""
        result = monthly_summary(rows, "excess" + suffix)
        result.update(random_control=control,
            cost_sensitivity={str(m): monthly_summary(rows, f"excess_v2_{m}" + suffix) for m in (1.0, 1.5, 2.0)},
            cost_model_v1={"used_for_judgement": False, **monthly_summary(rows, "excess_v1_1.0" + suffix)})
        for label, start, end in (("design", "2017-05", "2024-12"), ("validation", "2025-01", "2025-12")):
            result[label] = monthly_summary([r for r in rows if start <= r["entry_month"] <= end], "excess" + suffix)
        design, validation = result["design"]["primary"]["mean"], result["validation"]["primary"]["mean"]
        result["validation_year_same_sign"] = bool(np.sign(design) == np.sign(validation)) if design is not None and validation is not None else None
        result["validation_sign_used_for_judgement"] = False
        results.append(result)
    results[0]["events"] = rows
    _check_memory()
    return tuple(results)


def judge_variants(variants, fields, trials):
    criteria = fields["pass_criteria"]["design_validation"]
    minimum = criteria["minimum_observations"]
    threshold = criteria["core_t_stat_gt_when_trials_gt_20"] if trials > 20 else criteria["core_t_stat_gt"]
    sufficient = {name: r["event_count"] >= minimum["events"] and r["primary"]["count"] >= minimum["rebalances"]
                  and r["primary"]["standard_error"] is not None and r["primary"]["standard_error"] > 0
                  and r["primary"]["t_stat"] is not None and not r.get("unavailable") for name, r in variants.items()}
    p_values = {name: variants[name]["primary"]["p_one_sided"] if sufficient[name] else 1.0 for name in VARIANTS}
    adjusted = hc003.holm(dict(zip(hc003.VARIANTS, (p_values[name] for name in VARIANTS))))
    corrected = dict(zip(VARIANTS, (adjusted[name] for name in hc003.VARIANTS)))
    for name, result in variants.items():
        primary, failures = result["primary"], []
        mean15 = result["cost_sensitivity"]["1.5"]["primary"]["mean"]
        share = result["random_control"]["share_of_controls"]
        result.update(holm_p=corrected[name], judgement_inputs={"p_for_holm": p_values[name],
            "t_threshold": threshold, "trial_count_after_run": trials, "events": result["event_count"],
            "months": primary["count"], "mean_at_cost_1_5": mean15, "random_share": share})
        if result.get("unavailable"):
            continue
        if not sufficient[name]:
            result.update(verdict="insufficient", reasons=["300 events, 36 event months and defined positive SE required"])
            continue
        for failed, reason in ((primary["mean"] <= 0, "mean net excess must exceed zero"),
                (primary["t_stat"] <= threshold, f"NW t must exceed {threshold}"),
                (corrected[name] > .05, "Holm(3) one-sided p must be <=0.05"),
                (share is None or share > .05, "random control uncomputable or share >0.05"),
                (mean15 is None or mean15 <= 0, "cost x1.5 net excess must exceed zero")):
            if failed:
                failures.append(reason)
        result.update(verdict="fail" if failures else "pass", reasons=failures or ["all per-rule EV001 criteria satisfied"])
    return variants


def unavailable_metrics(status, reason):
    return {**monthly_summary([]), "unavailable": status, "verdict": status, "reasons": [reason],
            "cost_sensitivity": {str(m): monthly_summary([]) for m in (1.0, 1.5, 2.0)},
            "random_control": {"share_of_controls": None}, "design": monthly_summary([]), "validation": monthly_summary([])}


def secondary_metrics(prepared, rule, variant, result):
    rows, book = result["events"], prepared["book"]
    for label, key in (("by_year", "entry_month"), ("prior_phases", "prior_phase"),
                       ("markets", "market"), ("corrections", "is_correction")):
        groups = defaultdict(list)
        for row in rows:
            value = row[key][:4] if label == "by_year" else str(row[key])
            groups[value].append(row)
        result[label] = {value: monthly_summary(group) for value, group in sorted(groups.items())}
    seasons, buckets = defaultdict(list), defaultdict(list)
    for row in rows:
        seasons[str(pd.Timestamp(row["public_date"]).to_period("Q"))].append(row)
        bucket = int(np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, row["entry_adv"], side="right"))
        buckets[str(bucket)].append(row)
    result["filing_seasons"] = {value: monthly_summary(group) for value, group in sorted(seasons.items())}
    result["liquidity_buckets"] = {value: monthly_summary(group) for value, group in sorted(buckets.items())}
    dated = pd.Series(rows, index=[row["entry_date"] for row in rows], dtype=object)
    result["short_sale_regimes"] = {label: monthly_summary(group.tolist())
                                    for label, group in hc003.market_regimes.split_by_regime(dated).items()}
    groups = defaultdict(list)
    for row in rows:
        groups[row["entry_date"]].append(row)
    capacity = []
    path_exclusions, unavailable = Counter(), []
    for day, group in sorted(groups.items()):
        entry = book.days.get_loc(pd.Timestamp(day))
        u = prepared["universes"][rule][group[0]["decision_date"]]
        codes = [code for code in u.index if book.entry_reason(code, entry) is None]
        limits = [min(row["entry_adv"], row["exit_adv"] / (1 + row["gross"]) if row["gross"] > -1 else np.inf)
                  * .01 * len(group) for row in group]
        capacity.append({"entry_date": day, "events": len(group), "aum_at_1pct_adv": float(min(limits))})
        for row in group:
            row["gross_excess_path"] = {}
        for horizon in (1, 5, 10, 20, 40, 60):
            if entry + horizon - 1 >= len(book.days):
                path_exclusions[str(horizon)] += len(group)
                continue
            outcomes = book.outcomes(codes, entry, horizon, costs=False, reporting=True)
            missing = unavailable_path(outcomes, day, horizon, len(group), len(codes))
            if missing is not None:
                unavailable.append(missing)
                continue
            benchmark = float(outcomes.gross.mean())
            for row in group:
                row["gross_excess_path"][str(horizon)] = float(outcomes.loc[row["stock_code"], "gross"] - benchmark)
    result["capacity"] = {"per_entry_date": capacity, "minimum_aum_at_1pct_adv": min(
        (row["aum_at_1pct_adv"] for row in capacity), default=None)}
    result["gross_excess_paths"] = {str(h): monthly_summary(
        [{**row, "gross_path": row["gross_excess_path"][str(h)]} for row in rows if str(h) in row["gross_excess_path"]],
        "gross_path") for h in (1, 5, 10, 20, 40, 60)}
    result["path_boundary_exclusions"] = dict(path_exclusions)
    if unavailable:
        result["path_unavailable"] = unavailable
    for label, dates in (("maintenance", set(groups)), ("maintenance_all_period", None)):
        selected, counts = select_events(prepared, rule, variant, kind="maintenance", entry_dates=dates)
        tables = outcome_tables(prepared, rule, selected, HORIZONS[variant], reporting=True)
        maintenance = observations(book, selected, tables)
        result[label] = {**monthly_summary(maintenance), "used_for_judgement": False,
                         "date_scope": "final_new_entrant_entry_dates" if dates is not None else "all_period",
                         "events": maintenance, "counts": counts}
        missing = [table["unavailable"] for table in tables.values() if "unavailable" in table]
        if missing:
            result[label]["unavailable_paths"] = missing


def input_price_paths(data_dir, stage):
    end = BOUNDS[stage][1].strftime("%Y%m%d")
    return sorted((pd.Timestamp(p.stem), p) for p in (Path(data_dir) / "market/krx_daily").glob("*/*.jsonl")
                  if len(p.stem) == 8 and p.stem.isdigit() and "20150101" <= p.stem <= end)


def source_hashes(data_dir, *, stage, repo_root, prices=None, frozen_prices=None):
    root = Path(data_dir)
    groups = {"financials": sorted(p for p in (root / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")
                                  if int(p.stem[:4]) <= (2025 if stage == "design_validation" else 2026)),
        "companies": [root / "reference/dart_company/companies.jsonl"],
        "main_listing": sorted((root / "disclosures/dart_full/list").glob("*/*.jsonl")),
        "supplement": sorted((root / "disclosures/dart_full/list_supplement").rglob("*.jsonl")),
        "coverage_metadata": [root / "reference/corp_codes.csv", root / "disclosures/dart_full/_state.json",
                              root / "disclosures/dart_full/list_supplement/_state.json"]}
    result = {name: {p.relative_to(root).as_posix(): hc003._digest(p) for p in paths if p.is_file()}
              for name, paths in groups.items()}
    result["prices"] = {}
    if prices is None:
        for day, path in input_price_paths(data_dir, stage):
            key = path.relative_to(root).as_posix()
            if holdout.HOLDOUT_START <= day.date() <= holdout.HOLDOUT_END:
                if frozen_prices is not None:
                    if key not in frozen_prices:
                        raise ValueError("protected price absent from frozen target scan: " + key)
                    result["prices"][key] = frozen_prices[key]
                    continue
                if not any(s._open and s.experiment_id == EXPERIMENT_ID and s.repo_root == Path(repo_root).resolve()
                           for s in holdout._SESSIONS.get()):
                    raise ValueError("protected price hashing requires an open EV001 HoldoutSession")
            holdout.guard_period(day.date(), day.date(), experiment_id=EXPERIMENT_ID, repo_root=repo_root)
            result["prices"][key] = hc003._digest(path)
    else:
        if common._sessions(prices).max() > BOUNDS[stage][1]:
            raise ValueError("in-memory prices exceed stage boundary")
        common.guard_prices(prices, experiment_id=EXPERIMENT_ID, repo_root=repo_root)
        result["in_memory_prices"] = hc003.source_hashes(data_dir, [], {}, prices=prices)["in_memory_prices"]
    sources = (Path(__file__), Path(av001.__file__), Path(hc001.__file__), Path(hc002.__file__), Path(hc003.__file__),
        Path(hf001.__file__), Path(common.__file__), Path(ordering.__file__), Path(financial_dates.__file__),
        Path(dart_listing.__file__), Path(dart_quarterly.__file__), Path(cost_model.__file__), Path(holdout.__file__),
        Path(experiment_registry.__file__), Path(signal_eval.__file__), Path(hc003.market_regimes.__file__),
        Path(hc003.krx_market.__file__), Path(hc001.industry_map.__file__), Path(hc003.capacity.__file__),
        PROJECT_ROOT / "src/ingestion/dart_filing_dates.py", PROJECT_ROOT / "src/ingestion/storage.py",
        PROJECT_ROOT / "src/utils/stock_codes.py", PROJECT_ROOT / "src/runner/__init__.py",
        PROJECT_ROOT / "src/runner/trading_calendar.py")
    result["code"] = {p.relative_to(PROJECT_ROOT).as_posix(): hc003._digest(p) for p in sources}
    return result


def holdout_input_hashes(data_dir, repo_root, *, before_open=False):
    frozen = None
    if before_open:
        state = dart_listing._state(dart_listing.directory(data_dir) / "list_supplement/_state.json")
        if state is None:
            raise ValueError("holdout requires a frozen listing target scan")
        frozen = dart_listing.verified_target_scan(Path(data_dir), state, price_end=hc003.DESIGN_END)["price_files"]
    return source_hashes(data_dir, stage="holdout", repo_root=repo_root, frozen_prices=frozen)


def replay_hash(payload):
    return hc003._content_hash({key: value for key, value in payload.items()
                               if key not in ("code_commit", "performance", "replay_sha256")})


def evaluate(prepared, fields, *, trials=None, passed=VARIANTS, design_effects=None):
    rules, losses, selection_counts = {}, {}, {}
    available = prepared["versions"]["S"].sensitivity_available
    for rule in RULES:
        rules[rule], losses[rule], selection_counts[rule] = {}, {}, {}
        for variant in passed:
            selected, counts = select_events(prepared, rule, variant)
            selection_counts[rule][variant] = counts
            if rule == "O" and not available:
                reason = "corrections exist in the stage sample with zero original-filing links"
                rules[rule][variant] = unavailable_metrics("sensitivity_unavailable", reason)
                losses[rule][variant] = unavailable_metrics("sensitivity_unavailable", reason)
                continue
            result, loss = variant_metrics(prepared, rule, variant, selected)
            if design_effects is not None:
                for target in (result, loss):
                    verdict, reasons = holdout_verdict(target, design_effects[rule][variant])
                    target.update(verdict=verdict, reasons=reasons, design_mean_excess=design_effects[rule][variant])
            rules[rule][variant], losses[rule][variant] = result, loss
            secondary_metrics(prepared, rule, variant, result)
        if design_effects is None:
            judge_variants(rules[rule], fields, trials)
            judge_variants(losses[rule], fields, trials)
        hc003.apply_delisting_verdict(rules[rule], losses[rule])
    combined = {v: {"verdict": av001.combine_verdict(rules["S"][v]["verdict"], rules["O"][v]["verdict"],
                                                   sensitivity_available=available),
                    "rule_verdicts": {r: rules[r][v]["verdict"] for r in RULES}} for v in passed}
    return {"rules": rules, "variants": combined, "delisting_sensitivity": losses,
            "selection_counts": selection_counts,
            "sensitivity_unavailable_reasons": av001.sensitivity_reasons(prepared["versions"])}


def provenance(prepared):
    return {"L": prepared["dates"].L, "filing_rules": {r: prepared["versions"][r].report() for r in RULES},
        "version_provenance": prepared["versions"]["S"].annotations,
        "listing_version": prepared["listing_status"]["listing_version"], "listing_status": prepared["listing_status"],
        "coverage": prepared["coverage"],
        "counts": {"event_phases": prepared["event_counts"], "universe": prepared["universe_counts"],
                   "rights_events": prepared["raise_counts"], "prices": dict(prepared["book"].counts),
                   "financial_signal_missing_by_year": {r: hc003._financial_ratios(prepared["universe_counts"][r]) for r in RULES}},
        "frozen_universes": {r: {day: u.index.tolist() for day, u in prepared["universes"][r].items()} for r in RULES}}


def compute_design(*, data_dir, repo_root, sessions=None, prices=None, trials=None, _attempt=None):
    started = time.perf_counter()
    registration = _registration(repo_root)
    sessions = signal_eval._dates(hc003.session_calendar() if sessions is None else sessions).sort_values().unique()
    trading_calendar.preflight_price_sessions(data_dir,
        sessions[0] if prices is None else common._sessions(prices).min(),
        hc003.DESIGN_END if prices is None else common._sessions(prices).max(),
        price_days=None if prices is None else common._sessions(prices))
    hashes = source_hashes(data_dir, stage="design_validation", repo_root=repo_root, prices=prices)
    if _attempt is not None:
        _attempt["source_hashes"] = hashes
    prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, stage="design_validation", prices=prices)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3 if trials is None else trials
    payload = evaluate(prepared, registration.fields, trials=trials)
    payload.update(experiment_id=EXPERIMENT_ID, stage="design_validation", selected_variant="ev_new20",
        verdict=payload["variants"]["ev_new20"]["verdict"], reasons=["Each variant must pass both S and O."],
        prereg_commit=registration.commit_hash, **provenance(prepared),
        design_effects={r: {v: payload["rules"][r][v]["design"]["primary"]["mean"] for v in VARIANTS} for r in RULES},
        source_hashes=hashes, code_commit=experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
        library_versions={"python": platform.python_version(), **{name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "scipy", "exchange-calendars", "PyYAML")}},
        trial_count_after_run=trials, interpretations=INTERPRETATIONS)
    if hashes != source_hashes(data_dir, stage="design_validation", repo_root=repo_root, prices=prices):
        raise ValueError("inputs changed during design computation")
    payload["replay_sha256"] = replay_hash(payload)
    _check_memory()
    payload["performance"] = {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return payload


def registry_metrics(payload, *, design_result_file=None):
    rows = {}
    for variant, combined in payload["variants"].items():
        rows[variant if payload["stage"] == "design_validation" else "holdout_" + variant] = {
            "stage": payload["stage"], "combined_verdict": combined["verdict"],
            "design_result_file": design_result_file, "replay_sha256": payload.get("replay_sha256"), "L": payload["L"],
            "rules": {r: {"verdict": payload["rules"][r][variant]["verdict"],
                **{key: payload["rules"][r][variant][key] for key in ("primary", "event_count", "entry_dates")},
                "holm_p": payload["rules"][r][variant].get("holm_p"),
                "random_share": payload["rules"][r][variant]["random_control"]["share_of_controls"]} for r in RULES}}
    if "abort" in payload:
        for row in rows.values():
            row.update(payload["abort"])
    return rows


def publish(payload, *, repo_root, design_result_file=None, aborted_registration=None):
    names = set(payload["variants"])
    if (payload["stage"] not in ("design_validation", "holdout") or not names or not names.issubset(VARIANTS)
            or payload["stage"] == "design_validation" and names != set(VARIANTS)
            or payload["stage"] == "holdout" and not design_result_file
            or set(payload["rules"]) != set(RULES)
            or any(set(payload["rules"][r]) != names for r in RULES)):
        raise ValueError("publication requires all three design variants or eligible holdout variants with matching rules")
    metrics = registry_metrics(payload, design_result_file=design_result_file)
    for row in metrics.values():
        experiment_registry.encode_metrics(row)
    # Validate the whole result before creating any files or registry rows.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    lines = [f"# {EXPERIMENT_ID}: {payload['stage']}", "", f"Verdict: {payload['verdict']}", "",
             "| Variant | S | O | Combined |", "| --- | --- | --- | --- |"]
    for name, result in payload["variants"].items():
        lines.append(f"| {name} | {result['rule_verdicts']['S']} | {result['rule_verdicts']['O']} | {result['verdict']} |")
    lines += ["", *INTERPRETATIONS]
    paths = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root, summary_lines=lines,
                                 aborted_registration=aborted_registration)
    result_file, checksum = paths[0].relative_to(repo_root).as_posix(), hc003._digest(paths[0])
    for row in metrics.values():
        row.update(result_file=result_file, result_sha256=checksum)
        experiment_registry.encode_metrics(row)
    for name, row in metrics.items():
        if aborted_registration is not None:
            experiment_registry.record_aborted_trial(EXPERIMENT_ID, name, row, row["combined_verdict"],
                preregistration=aborted_registration, repo_root=repo_root,
                note=f"EV001 {payload['stage']}; aborted attempt; compact summary")
        else:
            experiment_registry.record_trial(EXPERIMENT_ID, name, row, row["combined_verdict"], repo_root=repo_root,
                note=f"EV001 {payload['stage']}; S/O calendar NW(3) Holm(3); compact summary")
    return payload


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, sessions=None, prices=None):
    with ordering.execution_lock(repo_root):
        with ordering.design_attempt(EXPERIMENT_ID, VARIANTS, repo_root) as attempt:
            result = compute_design(data_dir=data_dir, repo_root=repo_root, sessions=sessions, prices=prices, _attempt=attempt)
            return publish(result, repo_root=repo_root)


def saved_design(rows, *, repo_root, registration):
    run = ordering.design_run(rows, EXPERIMENT_ID, VARIANTS)
    path = (Path(repo_root) / run["result_file"]).resolve()
    directory = (Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results").resolve()
    if not path.is_relative_to(directory):
        raise ValueError("saved design path is outside EV001 results")
    checksum = hc003._digest(path)
    relevant = [json.loads(row["metrics_json"]) for row in rows if row["experiment_id"] == EXPERIMENT_ID
                and row["variant"] in VARIANTS and json.loads(row["metrics_json"]).get("result_file") == run["result_file"]]
    if any(row.get("result_sha256") != checksum for row in relevant):
        raise ValueError("saved design hash differs from registry rows")
    design = common.read_result_json(path)
    if (design["stage"] != "design_validation" or design["experiment_id"] != EXPERIMENT_ID
            or design["prereg_commit"] != registration.commit_hash or design["replay_sha256"] != replay_hash(design)
            or any(row.get("replay_sha256") != design["replay_sha256"] for row in relevant)):
        raise ValueError("saved design preregistration or deterministic replay hash differs")
    passed = [v for v in VARIANTS if design["variants"][v]["verdict"] == "pass"]
    if passed != run["passing_variants"] or any(design["rules"][r][v]["verdict"] != "pass" for r in RULES for v in passed):
        raise ValueError("holdout eligibility requires the same passing set under both rules")
    for rule in RULES:
        for variant in passed:
            events = design["rules"][rule][variant]["events"]
            effect = monthly_summary([row for row in events if "2017-05" <= row["entry_month"] <= "2024-12"])["primary"]["mean"]
            if effect != design["design_effects"][rule][variant]:
                raise ValueError("saved comparison value is not the rule/variant design-only effect")
    return design, run


def verify_non_holdout(design, *, data_dir, repo_root, sessions):
    replay = compute_design(data_dir=data_dir, repo_root=repo_root, sessions=sessions, trials=design["trial_count_after_run"])
    if replay["source_hashes"].get("code") != design["source_hashes"].get("code"):
        raise ValueError("non-holdout reproducibility mismatch: code hashes differ; "
                         "run a new design/validation trial with this code first; protected evaluation remains unopened")
    if replay["source_hashes"] != design["source_hashes"] or replay["replay_sha256"] != design["replay_sha256"]:
        raise ValueError("non-holdout reproducibility mismatch; protected evaluation remains unopened")


def holdout_verdict(result, design_effect):
    if result["event_count"] < 30 or sum(row["events"] >= 10 for row in result["per_month"]) < 2:
        return "holdout_insufficient", ["30 events and two entry months with >=10 events each required"]
    mean = result["primary"]["mean"]
    if design_effect is None or not np.isfinite(design_effect) or design_effect <= 0:
        return "fail", ["saved rule/variant design-only effect is undefined or nonpositive"]
    if mean is None or not np.isfinite(mean) or mean <= 0 or mean < .5 * design_effect:
        return "fail", ["positive holdout effect in the design direction and >=half its size required"]
    return "pass", ["all per-rule EV001 holdout criteria satisfied; t/controls report only"]


def terminal_payload(status, reason, passed, *, design, registration):
    return {"experiment_id": EXPERIMENT_ID, "stage": "holdout", "selected_variant": "passed_design_variants",
        "prereg_commit": registration.commit_hash, "verdict": status, "reasons": [reason], "L": design["L"],
        "rules": {r: {v: unavailable_metrics(status, reason) for v in passed} for r in RULES},
        "variants": {v: {"verdict": status, "rule_verdicts": {r: status for r in RULES}} for v in passed},
        "design_effects": design["design_effects"], "interpretations": INTERPRETATIONS}


def run_holdout(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    with ordering.execution_lock(repo_root):
        registration = _registration(repo_root)
        used, rows = hc003.ledger_experiments(repo_root), hc003.registry_rows(repo_root)
        design, run = saved_design(rows, repo_root=repo_root, registration=registration)
        passed = run["passing_variants"]
        if not passed:
            return {"stage": "holdout", "verdict": "not_applicable", "variants": {},
                    "reasons": ["No variant passed both rules; holdout remains unopened."]}
        terminal = [row for row in rows if row["experiment_id"] == EXPERIMENT_ID
                    and json.loads(row["metrics_json"]).get("stage") == "holdout"]
        if EXPERIMENT_ID in used or terminal:
            raise ValueError("EV001 holdout used or terminally blocked; cannot reopen")
        if "LH002_llm_hegemony_judge" in used:
            payload = terminal_payload("holdout_contaminated", "LH002 ledger entry; no protected returns read", passed,
                                       design=design, registration=registration)
            return publish(payload, repo_root=repo_root, design_result_file=run["result_file"])
        sessions = signal_eval._dates(hc003.session_calendar()).sort_values().unique()
        verify_non_holdout(design, data_dir=data_dir, repo_root=repo_root, sessions=sessions)
        dates = financial_dates.FilingDates.from_data(data_dir)
        audit = financial_dates.FinancialVersions(data_dir, dates, "O", "holdout", derive=False, sample_end=hc003.HOLDOUT_END)
        if not audit.sensitivity_available:
            raise ValueError("sensitivity_unavailable: holdout has corrections but zero original links; section 7 has no "
                             "terminal release for this state. Session remains unopened; LH002 remains blocked.")
        trading_calendar.preflight_price_sessions(data_dir, sessions[0], hc003.HOLDOUT_END)
        hashes = holdout_input_hashes(data_dir, repo_root, before_open=True)
        started = time.perf_counter()
        try:
            with holdout.HoldoutSession(EXPERIMENT_ID, repo_root=repo_root):
                if holdout_input_hashes(data_dir, repo_root) != hashes:
                    raise ValueError("holdout inputs changed between freeze and session entry")
                prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, stage="holdout")
                result = evaluate(prepared, registration.fields, passed=passed, design_effects=design["design_effects"])
                if any(v["verdict"] == "sensitivity_unavailable" for v in result["variants"].values()):
                    raise ValueError("original-link availability changed after holdout freeze")
                if holdout_input_hashes(data_dir, repo_root) != hashes:
                    raise ValueError("holdout inputs changed during computation")
                if dart_listing.listing_status(data_dir, price_end=hc003.HOLDOUT_END) != prepared["listing_status"]:
                    raise ValueError("holdout listing coverage changed during computation")
                _check_memory()
            headline = "ev_new20" if "ev_new20" in passed else passed[0]
            result.update(experiment_id=EXPERIMENT_ID, stage="holdout", selected_variant="passed_design_variants",
                verdict=result["variants"][headline]["verdict"], reasons=["Both rule-specific holdout verdicts must pass."],
                prereg_commit=registration.commit_hash, design_effects=design["design_effects"], **provenance(prepared),
                source_hashes=hashes, input_hash_verification="pre_open == session_before == session_after",
                code_commit=experiment_registry._git(Path(repo_root), "rev-parse", "HEAD"),
                library_versions=design["library_versions"], interpretations=INTERPRETATIONS,
                performance={"runtime_seconds": time.perf_counter() - started,
                             "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024})
            return publish(result, repo_root=repo_root, design_result_file=run["result_file"])
        except BaseException as exc:
            if EXPERIMENT_ID not in hc003.ledger_experiments(repo_root):
                raise
            payload = terminal_payload("holdout_aborted", f"{type(exc).__name__}: {exc}", passed,
                                       design=design, registration=registration)
            payload.update(source_hashes=hashes, input_hash_verification="aborted; inputs not verified")
            payload["abort"] = ordering.abort_details(hashes, exc)
            publish(payload, repo_root=repo_root, design_result_file=run["result_file"], aborted_registration=registration)
            raise


def rehearse_holdout(start="2025-01-01", end="2025-06-30", *,
                     data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    """Shared holdout preparation/evaluation on historical data, without publication."""
    start, end, sessions = av001.rehearsal_window(start, end)
    registration = _registration(repo_root)
    preflight = trading_calendar.preflight_price_sessions(data_dir, sessions[0], end)
    prior_close = sessions[sessions < start][-1]
    training = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions,
                       stage="design_validation", bounds=(BOUNDS["design_validation"][0], prior_close))
    design = evaluate(training, registration.fields, trials=3)
    effects = {r: {v: design["rules"][r][v]["primary"]["mean"] for v in VARIANTS} for r in RULES}
    del training, design
    prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions,
                       stage="design_validation", bounds=(start, end))
    result = evaluate(prepared, registration.fields, passed=VARIANTS, design_effects=effects)
    result.update(experiment_id=EXPERIMENT_ID, stage="holdout_rehearsal", start=start.date().isoformat(),
                  end=end.date().isoformat(), preflight=preflight, design_effects=effects)
    return result


def fixture_timing(*, stocks=64, entries=12):
    days = signal_eval._dates(hc003.session_calendar())
    days = days[(days >= "2024-01-01") & (days <= "2025-06-30")]
    codes = [f"{pos:06d}" for pos in range(stocks)]
    index = pd.MultiIndex.from_product([days, codes], names=["trade_date", "stock_code"])
    prices = pd.DataFrame({"open": 10000., "close": 10000., "volume": 1000., "base_price": 10000.,
        "ret_1d": 0., "trading_value": 2e9, "market": "KOSPI", "calendar_status": "verified"}, index=index)
    book = EventPrices(days, days[-1], PROJECT_ROOT)
    book.add(prices)
    book.finish()
    selected, tables = [], {}
    started = time.perf_counter()
    for pos in range(entries):
        entry = 65 + pos * 10
        day = days[entry].date().isoformat()
        tables[day] = {"frame": book.outcomes(codes, entry, 20), "benchmark": 0., "benchmark_loss": 0.}
        selected.append({"stock_code": codes[0], "entry_date": day})
    outcome_seconds = time.perf_counter() - started
    started = time.perf_counter()
    random_controls(book, selected, tables, 20, 0., 0.)
    return {"outcome_seconds": outcome_seconds, "control_seconds": time.perf_counter() - started,
            "stocks": stocks, "entry_dates": entries, "paths": N_CONTROLS,
            "numeric_price_mib": sum(a.nbytes for a in book.raw.values()) / 1024**2}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    started = time.perf_counter()
    registration = _registration(repo_root)
    sessions = signal_eval._dates(hc003.session_calendar()).sort_values().unique()
    prepared = prepare(data_dir=data_dir, repo_root=repo_root, sessions=sessions, stage="design_validation", dry=True)
    counts, outcome_units, draw_count = {}, 0, 0
    for rule in RULES:
        counts[rule] = {}
        for variant in VARIANTS:
            selected, report = select_events(prepared, rule, variant)
            counts[rule][variant] = report
            by_day = {row["entry_date"]: row for row in selected}
            units = sum(len(prepared["universes"][rule][row["decision_date"]]) for row in by_day.values())
            outcome_units += units
            draw_count += len(selected) * N_CONTROLS
    preparation_seconds = time.perf_counter() - started
    timing = fixture_timing()
    projected = (preparation_seconds + timing["outcome_seconds"] * outcome_units / (timing["stocks"] * timing["entry_dates"])
                 + timing["control_seconds"] * outcome_units / (timing["stocks"] * timing["entry_dates"]))
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    # Retained draw indices/JSON plus one variant's outcome tables and diagnostics.
    projected_rss = peak + draw_count * 48 + outcome_units * 240 + 512 * 1024**2
    _check_memory()
    return {"experiment_id": EXPERIMENT_ID, "prereg_commit": registration.commit_hash,
        "variants": VARIANTS, "rules": RULES, "read_end": "2025-12-30", "price_files": len(prepared["price_paths"]),
        "L": prepared["dates"].L, "listing_status": prepared["listing_status"],
        "financial_dates": {r: prepared["versions"][r].report() for r in RULES},
        "phase_counts": prepared["event_counts"], "event_counts": counts,
        "listing_incomplete_entry_dates": sum(not row["covered"] for row in prepared["coverage"].values()),
        "fixture_timing": timing, "performance": {"preparation_seconds": preparation_seconds, "peak_rss_mib": peak / 1024**2},
        "projection": {"full_run_seconds_range": [projected, projected * 3], "peak_rss_mib": projected_rss / 1024**2,
            "rss_limit_bytes": MAX_RSS_BYTES,
            "basis": "Measured real signal/price preparation plus synthetic per-name two-sided costs and 200-path draws, scaled to planned entry dates/universe sizes. Upper runtime allows secondary paths/maintenance and serialization. RSS includes draw/JSON/table reserves; not a full-run measurement."},
        "interpretations": INTERPRETATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: plan, event counts and synthetic timing only")
    mode.add_argument("--execute", action="store_true", help="evaluate design/validation and append three compact rows")
    mode.add_argument("--holdout", action="store_true", help="reproduce design then open one protected EV001 session")
    mode.add_argument("--rehearse-holdout", nargs="*", metavar="DATE",
                      help="read-only holdout rehearsal; default 2025-01-01 2025-06-30")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.rehearse_holdout is not None:
        if len(args.rehearse_holdout) not in (0, 2):
            parser.error("--rehearse-holdout takes START END or no dates")
        result = rehearse_holdout(*args.rehearse_holdout, data_dir=args.data_dir)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    elif args.execute or args.holdout:
        result = (run_holdout if args.holdout else run_experiment)(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} {result['stage']}: {result['verdict']}")
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run; variants={','.join(VARIANTS)}; rules=S,O")
        print(f"Preregistration: {plan['prereg_commit']}; price files={plan['price_files']}; reads end={plan['read_end']}")
        status = plan["listing_status"]
        print(f"Listing supplement complete={status['supplement_complete']}; reason={status['reason']}; completed={status['completed_corps']}/{status['target_count']}")
        print(f"Listing windows incomplete on {plan['listing_incomplete_entry_dates']} entry dates; those ev_new20_f events are unusable.")
        print(f"Actual-public-date L={plan['L']} days; financial date counts: " + json.dumps(plan["financial_dates"], ensure_ascii=False))
        print("Event phase counts per rule/year/reason: " + json.dumps(plan["phase_counts"], ensure_ascii=False))
        print("Final event counts per rule/variant/year/reason: " + json.dumps(plan["event_counts"], ensure_ascii=False))
        print("Fixture timing: " + json.dumps(plan["fixture_timing"]))
        print("Preparation/RSS: " + json.dumps(plan["performance"]))
        print("Projected full runtime/RSS: " + json.dumps(plan["projection"]))
        print("Plan: next-session open -> entry-inclusive 20/60th-session close; two-sided v2 costs; calendar NW(3); Holm(3) per rule; 200 path/date controls.")
        print("No local event returns evaluated; no registry/results/data writes or holdout session.")
        if not status["supplement_complete"]:
            print("EXECUTION BLOCKED: complete and verify the listing supplement before --execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
