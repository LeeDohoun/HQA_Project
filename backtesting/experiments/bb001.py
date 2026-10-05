"""BB001 direct-buyback event study; guarded local data, dry-run by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import resource
import time
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, holdout
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc001
from src.ingestion import dart_buyback as dart
from src.research import market_regimes
from src.utils.stock_codes import is_stock_code


EXPERIMENT_ID = "BB001_direct_buyback"
VARIANTS = ("d21", "d63", "d21_cancel")
HORIZONS = {"d21": 21, "d63": 63, "d21_cancel": 21}
CUTOFF, VANISH_END = pd.Timestamp("2026-01-01"), pd.Timestamp("2025-12-30")
DESIGN_START, DESIGN_END = pd.Timestamp("2016-09-01"), pd.Timestamp("2024-12-31")
END = pd.Timestamp("2025-12-31")
MULTIPLIERS, N_CONTROLS = (1.0, 1.5, 2.0), 200
ASSUMPTIONS = [
    "The explicit BB001 request supersedes the stale M0 entry in .agents/task.md; only the five requested source/test files are created.",
    "All calendar-day listing archives, including empty weekends/holidays, must cover the 63-session receipt clock through the day before publication. Min/max dates alone do not prove completeness.",
    "Receipt age is the number of exchange sessions in (earlier receipt day, later receipt day]. The inclusive 0..63 clock counts every title-qualified decision, even decisions excluded later. Controls conservatively use the same inclusive clock at the last completed session, and all receipts strictly before entry.",
    "DART rcept_dt is the publication date even when the receipt number's eight-digit prefix differs. The original receipt identifier is never rewritten or substituted.",
    "Stored structured corporate responses must be complete. Original receipt rows are immutable; documents must have a matching row digest and independently reverified common-share digits. Digit matches require numeric boundaries, so a larger unrelated number cannot verify a plan.",
    "Collection initially splits the registered range into at most 90 calendar days. Status 100 for a multi-day range causes subdivision; pagination is validated; unpaged responses with >=100 rows are split. Missing listed original receipts receive exact-day probes. Unresolved single-day limits fail clearly; request estimates are lower bounds.",
    "Pre-entry liquidity requires 20 verified session rows, finite ret_1d >= -1, positive finite market cap, common shares and a known non-SPAC name. Missing market cap cannot form a size bin and is excluded with a count. No current-company code mapping is used.",
    "Empirical breakpoints use inverse empirical CDF (numpy inverted_cdf), separately by market after non-event and buyable-entry filtering. Values equal to a breakpoint go to the lower bin. Only size is relaxed, never returns or market.",
    "Daily positive volume proxies entry tradability; no intraday opening-volume archive exists. An unavailable base blocks entry; missing base_price uses the immediately preceding session close, the established repository convention. Limits are exact 15%/30%, without a tolerance.",
    "Holding returns chain adjusted ret_1d after entry's close/open factor. Unquoted suspension days carry the last mark. A missing adjusted return on an observed positive close is a data error, never a raw-close fallback or a removed holding.",
    "Both sides use frozen pre-entry ADV. Buy costs use entry date/open; sell costs use the actual last-trading valuation date/close, including liquidation trading. All costs are subtracted as return fractions, as in existing runners; x1.5/x2 do not scale sell tax. Vanished -100% gross returns retain the same costs.",
    "Missing scheduled quotes use the last positive-volume verified close. Lower-limit closes are valuations without an execution guarantee; no extra lower-limit exit rule is added. Vanished classification requires complete market archives through 2025-12-30, and only changes the sensitivity valuation, never selection.",
    "Random draws are path-major, then event (entry_date, rcept_no), then code-sorted matched controls; default_rng(0) restarts per variant. The -100% rerun makes the same draws. Random values are control-minus-control-mean, never event-minus-drawn-control.",
    "The design-period >=300 event requirement governs insufficiency; YAML's monthly-rebalance placeholder is not an additional gate. The 2025 sign check is reporting-only. d21 is the fixed primary; there is no best-variant selection.",
    "Secondary 1/5/63-session estimates apply the same entry filters and their own pre-read holdout exclusion. Intensity B uses planned-period inclusive exchange sessions; invalid/zero or calendar-incomplete periods yield an explicitly undefined B without deleting a primary event. B tercile boundaries use pooled finite event B and lower-bin ties.",
    "Literature CAR uses fixed matched controls and adjusted close returns, no costs: [0,21] includes disclosure-session ret_1d through session +21, [1,21] excludes disclosure-session ret_1d. Non-session receipts have no disclosure close and are reported undefined; forbidden horizons are excluded before reading. These are unattainable diagnostics.",
    "Calendar-time diagnostics allocate the lowest free of 20 initially equal capital slots in (entry_date, rcept_no) order, discard overflow without replacement, and free a slot after scheduled close. Each slot reinvests its own proceeds on its next event; names and control weights stay fixed within a holding. Empty slots retain cash. Monthly excess is event-book minus matched-book monthly return.",
    "Continuous reversal adjustment is OLS e on intercept and pre-entry 20-session event-minus-control return. The intercept uses a trading-grid Bartlett sandwich with the primary lag and no finite-sample correction. Singular designs are reported undefined.",
    "Backfilled latest KRX revisions and literal original-document digit verification do not reconstruct historical data vintages or establish causal effects. 2025 is a stability diagnostic after B001 exploration; no holdout prices are read.",
]
INTERPRETATIONS = {
    "note": "Follow-up exploration, conditional matched excess, not a causal buyback effect. Fixed primary d21; all three variants judged. No LLM or holdout.",
    "criteria": {
        "net_performance_gt": "Event-equal-weight mean(event net minus fixed matched-control equal-weight net) > 0.",
        "minimum_observations": ">=300 design-period events after every filter, separately for each variant; dates and corporations are also reported.",
        "core_t_stat_gt": "Full design+validation trading-grid residual-sum Bartlett HAC, lag=holding sessions-1, no finite-sample correction; strict t>2, or >3 if this experiment's rows including this run's three exceed 20.",
        "multiple_testing": "Standard-normal one-sided p, Holm over exactly three variants; insufficient or undefined variants contribute p=1; adjusted p<=0.05 AND the repository t gate.",
        "random_control_top_percent": "Of 200 path means(control draw minus that event's same-control mean), share >= actual mean <=0.05, including ties.",
        "net_performance_at_cost_multiplier_1_5_gt": "Same fixed events/controls, side-specific v2 costs x1.5 with unscaled sell tax; excess mean >0.",
        "delisting_sensitive": "Recompute both sides, random controls, HAC, Holm and every gate at vanished gross=-100%; changed verdict never passes.",
    },
    "metrics": {
        "primary": "count/mean/std are event observations; std is sample dispersion. Residual sums retain zero-event dates on the full grid, variance is Bartlett quadratic sum/N^2. Undefined t has p=1.",
        "per_event": "Original corp/stock/receipt, public/entry/scheduled exit dates, pre-entry features, empirical boundaries and bins, fixed matched codes and weights, side costs, actual marks and vanished flags audit the calculation.",
        "cluster_t": "Comparison only: first average excess within entry dates, then mean/(sample std/sqrt(date count)), exactly event_study; not the pass t.",
        "secondary_horizons": "Independent 1/5/63-session evaluations using the same event and matching rules; each horizon pre-excludes holdout exits. No extra judged variant or registry trial.",
        "literature_car": "Event minus fixed-control mean of adjusted close cumulative returns for [0,21] and [1,21], no costs, unattainable and reporting-only; null reasons explicit.",
        "gap": "Entry open / last pre-entry close -1, event mean; unattainable overnight component, not part of the primary.",
        "reversal_balance": "Per-event pre-entry compounded ret_1d over 5/20 sessions, matched-control mean, difference, group means and HAC summaries; no post-entry values enter matching.",
        "continuous_adjustment": "OLS intercept/slope for e versus 20-session pre-entry return imbalance; grid HAC sandwich for intercept t. Singular fits are null.",
        "intensity_terciles": "B=planned common-share amount/(inclusive planned-period sessions * pre-entry ADV); pooled empirical terciles, lower-bin ties, and primary excess summaries, no subgroup judgement.",
        "calendar_time_portfolio": "20 independent capital slots with fixed holdings and matched weights, idle cash, deterministic overflow counts, daily NAVs and monthly event/matched net returns and difference; reporting only.",
        "strata": "Receipt-year, documented short-sale receipt-date regime and entry-known cost-model ADV bucket summaries; no subgroup judgement. Post-2025 buyback reforms are labels only, no prices.",
        "cost_sensitivity": "Fixed sets at v2 costs x1/x1.5/x2; v1 side-date costs are diagnostic only. Costs on both sides; no tax multiplier.",
        "availability": "Listing calendar coverage, title and sequential filter counts, original structured and document availability, receipt-specific verification and quota/request plan; absence is never no-event evidence.",
        "source_hashes": "SHA-256 of listing, structured and original-document archives, actual guarded price files read, preregistration and used code; supplied price frames have a separate digest. Rechecked before publication.",
    },
}


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _registration(repo_root):
    registration = experiment_registry.verify_preregistration(EXPERIMENT_ID, repo_root)
    if (registration.fields["variants_planned"] != 3 or registration.fields["uses_holdout"]
            or registration.fields["uses_llm"]):
        raise ValueError("BB001 requires three fixed variants, no holdout and no LLM")
    return registration


def _days(sessions=None):
    days = pd.DatetimeIndex(hc001._session_calendar() if sessions is None else sessions).sort_values().unique()
    if days.hasnans or days.tz is not None or not days.equals(days.normalize()):
        raise ValueError("sessions must be date-only and timezone-naive")
    return days


def price_limit(day):
    return 0.15 if pd.Timestamp(day) < pd.Timestamp("2015-06-15") else 0.30


def listing_coverage(public, sessions, files):
    public = pd.Timestamp(public)
    end = sessions.searchsorted(public, side="right")
    if end < 64:
        return {"covered": False, "reason": "incomplete_63_session_calendar", "missing_days": None}
    required = pd.date_range(sessions[end - 64], public - pd.Timedelta(days=1))
    available = pd.DatetimeIndex(pd.to_datetime(list(files)))
    missing = required.difference(available)
    return {"covered": not len(missing), "required_days": len(required), "available_days": len(required) - len(missing),
            "missing_days": len(missing), "start": required[0].date().isoformat(), "end": required[-1].date().isoformat(),
            "reason": "incomplete_listing_window" if len(missing) else None}


def _filter_steps(total, counts, reasons):
    result, remaining = [], total
    for reason in reasons:
        excluded = counts.get(reason, 0)
        result.append({"filter": reason, "before": remaining, "excluded": excluded, "after": remaining - excluded})
        remaining -= excluded
    return result


def construct_events(listings, files, sessions, data_dir):
    """Repeat clock is updated before *every* inclusion test; no price reads."""
    candidates = sorted((row for row in listings if dart.title_matches(row["report_nm"])),
                        key=lambda row: (row["rcept_dt"], row["rcept_no"]))
    events, audit, counts, previous, structured = [], [], Counter(), {}, {}
    availability = Counter()
    for item in candidates:
        corp, receipt = item["corp_code"], item["rcept_no"]
        public = pd.Timestamp(item["rcept_dt"])
        clock = int(sessions.searchsorted(public, side="right"))
        old = previous.get(corp)
        previous[corp] = (clock, item["rcept_dt"])
        repeated = old is not None and 0 <= clock - old[0] <= 63
        if corp not in structured:
            structured[corp] = dart.structured_record(data_dir, corp)
        archive = structured[corp]
        row = archive["rows"].get(receipt, {}).get("row") if archive and archive["complete"] else None
        document = dart.document_record(data_dir, receipt)
        complete = row is not None and all(dart.compact(row.get(field)) not in ("", "-") for field in dart.REQUIRED_FIELDS)
        verified = bool(row is not None and document and document["status"] == "000" and document["text"]
                        and document.get("verification", {}).get("structured_row_sha256") == dart.row_digest(row)
                        and dart.verify_values(row, document["text"])["verified"])
        availability.update(structured_original_rows=int(row is not None), required_fields_complete=int(complete),
            original_documents_stored=int(document is not None), original_documents_available=int(bool(document and document["status"] == "000" and document["text"])),
            original_values_verified=int(verified))
        coverage = listing_coverage(public, sessions, files)
        code = item.get("stock_code")
        reason = None
        if repeated:
            reason = "same_day_repeat" if old[1] == item["rcept_dt"] else "repeat_0_63"
        elif not DESIGN_START <= public <= END:
            reason = "outside_design_validation"
        elif not coverage["covered"]:
            reason = "incomplete_listing_window"
        elif not is_stock_code(code):
            reason = "stock_code_link_failed"
        elif item.get("corp_cls") not in ("Y", "K") or not code.endswith("0") or "스팩" in (item.get("corp_name") or ""):
            reason = "not_common_share_market_or_spac"
        elif row is None:
            reason = "structured_original_row_missing"
        elif not complete:
            reason = "required_field_missing"
        elif not verified:
            reason = "original_document_unavailable" if not document or document["status"] != "000" or not document["text"] else "original_value_verification_failed"
        elif not dart.digits(row["aqpln_stk_ostk"]) or not dart.digits(row["aqpln_prc_ostk"]):
            reason = "invalid_planned_digits"
        elif int(dart.digits(row["aqpln_stk_ostk"])) <= 0 or int(dart.digits(row["aqpln_prc_ostk"])) <= 0:
            reason = "nonpositive_plan"
        elif not dart.method_matches(row["aq_mth"]):
            reason = "not_on_market_acquisition"
        counts[reason or "included"] += 1
        audit.append({"rcept_no": receipt, "corp_code": corp, "stock_code": code, "reason": reason,
                      "coverage": coverage, "repeated": repeated})
        if reason is None:
            events.append({**item, "public_date": public.date().isoformat(), "period": "design" if public <= DESIGN_END else "validation",
                           "cancel": dart.cancellation_matches(row["aq_pp"]), "structured": row,
                           "planned_amount": int(dart.digits(row["aqpln_prc_ostk"])),
                           "planned_quantity": int(dart.digits(row["aqpln_stk_ostk"]))})
    steps = _filter_steps(len(candidates), counts, ("same_day_repeat", "repeat_0_63", "outside_design_validation",
        "incomplete_listing_window", "stock_code_link_failed", "not_common_share_market_or_spac", "structured_original_row_missing",
        "required_field_missing", "original_document_unavailable", "original_value_verification_failed", "invalid_planned_digits",
        "nonpositive_plan", "not_on_market_acquisition"))
    return events, {"title_candidates": len(candidates), "filter_counts": dict(counts), "sequential_filters": steps,
                    "availability": dict(availability), "audit": audit,
                    "stock_code_link_failed_all_candidates": sum(not is_stock_code(row.get("stock_code")) for row in candidates)}


def schedule(event, horizon, sessions):
    entry = int(sessions.searchsorted(pd.Timestamp(event["public_date"]), side="right"))
    exit_position = entry + horizon - 1
    if exit_position >= len(sessions):
        return None, "incomplete_calendar_horizon"
    if sessions[exit_position] >= CUTOFF:
        return None, "holdout_horizon"
    return {**event, "entry_date": sessions[entry].date().isoformat(), "exit_date": sessions[exit_position].date().isoformat(),
            "entry_position": entry, "exit_position": exit_position, "horizon": horizon}, None


class PriceReader:
    """Guard every lookup, including cache hits; open only requested daily files."""

    def __init__(self, data_dir, sessions, *, prices=None, repo_root=PROJECT_ROOT):
        self.data_dir, self.sessions, self.repo_root = Path(data_dir), _days(sessions), repo_root
        self.prices, self.source_hashes = prices, {}
        self._last_trades = None
        if prices is not None:
            common.guard_prices(prices, repo_root=repo_root)
            if prices.index.has_duplicates or (len(prices) and common._sessions(prices).max() >= CUTOFF):
                raise ValueError("supplied prices must be unique and entirely pre-2026")

    def read(self, day):
        day = pd.Timestamp(day)
        holdout.guard_period(day.date(), day.date(), repo_root=self.repo_root)
        if day >= CUTOFF:
            raise ValueError("BB001 cannot read prices in or after 2026")
        return self._load(day)

    @lru_cache(maxsize=96)
    def _load(self, day):
        if self.prices is not None:
            if day not in self.prices.index.get_level_values("trade_date"):
                return {}
            rows = self.prices.xs(day, level="trade_date").reset_index().to_dict("records")
        else:
            path = self.data_dir / "market/krx_daily" / day.strftime("%Y") / f"{day:%Y%m%d}.jsonl"
            if not path.is_file():
                return {}
            blob = path.read_bytes()
            digest = hashlib.sha256(blob).hexdigest()
            if str(path) in self.source_hashes and self.source_hashes[str(path)] != digest:
                raise ValueError("price source changed during evaluation")
            self.source_hashes[str(path)] = digest
            rows = [json.loads(line) for line in blob.decode("utf-8").splitlines() if line.strip()]
            if any(row.get("trade_date") != day.date().isoformat() for row in rows):
                raise ValueError("price row date does not match archive")
        result = {}
        for row in rows:
            code = row.get("stock_code")
            if not is_stock_code(code):
                raise ValueError("invalid price stock code")
            value = dict(row)
            for key in ("open", "close", "volume", "trading_value", "market_cap", "ret_1d", "base_price"):
                value[key] = float(row[key]) if row.get(key) not in (None, "") else math.nan
            if self.prices is None:
                value["ret_1d"] = float(row["change_rate_pct"]) / 100 if row.get("change_rate_pct") not in (None, "") else math.nan
            result[code] = value
        return result

    def require(self, day):
        rows = self.read(day)
        if not rows:
            raise ValueError(f"missing whole-market price archive: {pd.Timestamp(day).date()}")
        return rows

    def last_trades(self):
        if self._last_trades is None:
            if not len(self.sessions) or self.sessions.max() < VANISH_END:
                raise ValueError("vanished classification requires a calendar through 2025-12-30")
            last = {}
            for day in self.sessions[(self.sessions >= pd.Timestamp(dart.START)) & (self.sessions <= VANISH_END)]:
                for code, row in self.require(day).items():
                    if _traded(row):
                        last[code] = day
            self._last_trades = last
        return self._last_trades


def _positive(value):
    return value is not None and np.isfinite(value) and value > 0


def _quoted(row):
    return bool(row and row.get("calendar_status") == "verified" and _positive(row.get("close")))


def _traded(row):
    return _quoted(row) and _positive(row.get("volume"))


def entry_reason(row, previous, day):
    if not row or not _positive(row.get("open")) or not _positive(row.get("volume")) or row.get("calendar_status") != "verified":
        return "untradable_entry"
    base = row.get("base_price")
    if not _positive(base):
        base = previous.get("close") if previous else None
    if not _positive(base):
        return "missing_entry_base"
    if row["open"] >= base * (1 + price_limit(day)) - 1e-9:
        return "limit_up_entry"
    return None


def _pre_entry(reader, entry_position):
    if entry_position < 20:
        return {}, {"incomplete_20_session_calendar": 1}
    history = [reader.read(day) for day in reader.sessions[entry_position - 20:entry_position]]
    features, counts = {}, Counter()
    for code, current in history[-1].items():
        if (not code.endswith("0") or current.get("market") not in ("KOSPI", "KOSDAQ")
                or not isinstance(current.get("stock_name"), str) or not current["stock_name"].strip()
                or "스팩" in current["stock_name"]):
            counts["not_common_share_market_or_spac"] += 1
            continue
        rows = [daily.get(code) for daily in history]
        if any(not row or row.get("calendar_status") != "verified" for row in rows):
            counts["incomplete_verified_liquidity_window"] += 1
            continue
        adv = [row["trading_value"] for row in rows]
        returns = np.array([row["ret_1d"] for row in rows])
        if not _positive(current["close"]) or current["close"] < 1000:
            counts["price_below_1000"] += 1
        elif not np.isfinite(adv).all() or min(adv) < 0 or np.mean(adv) < 1e8:
            counts["adv_below_100m_or_missing"] += 1
        elif not np.isfinite(returns).all() or (returns < -1).any():
            counts["incomplete_adjusted_returns"] += 1
        elif not _positive(current["market_cap"]):
            counts["missing_market_cap"] += 1
        else:
            features[code] = {"market": current["market"], "market_cap": current["market_cap"],
                "pre_close": current["close"], "adv": float(np.mean(adv)),
                "prior20": float(np.prod(1 + returns) - 1), "prior5": float(np.prod(1 + returns[-5:]) - 1)}
    counts["eligible"] = len(features)
    return features, dict(counts)


def empirical_boundaries(values, parts):
    return np.quantile(np.asarray(values, dtype=float), np.arange(1, parts) / parts, method="inverted_cdf").tolist()


def lower_bin(value, boundaries):
    return int(np.searchsorted(boundaries, value, side="left"))


def match_controls(event, reader, listings):
    """No exit prices/returns are inspected to create or relax this fixed set."""
    pos = event["entry_position"]
    features, counts = _pre_entry(reader, pos)
    code = event["stock_code"]
    if code not in features:
        return None, "event_liquidity_or_identity", counts
    entry, previous = reader.read(reader.sessions[pos]), reader.read(reader.sessions[pos - 1])
    reason = entry_reason(entry.get(code), previous.get(code), reader.sessions[pos])
    if reason:
        return None, reason, counts
    recent = set()
    for row in listings:
        if dart.title_matches(row["report_nm"]) and pd.Timestamp(row["rcept_dt"]) < reader.sessions[pos]:
            age = pos - int(reader.sessions.searchsorted(pd.Timestamp(row["rcept_dt"]), side="right"))
            if 0 <= age <= 63:
                recent.add(row.get("stock_code"))
    pool = {stock: feature for stock, feature in features.items() if stock != code and stock not in recent
            and entry_reason(entry.get(stock), previous.get(stock), reader.sessions[pos]) is None}
    counts["non_event_buyable_candidates"] = len(pool)
    market = features[code]["market"]
    market_pool = {stock: feature for stock, feature in pool.items() if feature["market"] == market}
    counts["same_market_candidates"] = len(market_pool)
    if not market_pool:
        return None, "matched_controls_below_10", counts
    size = empirical_boundaries([row["market_cap"] for row in market_pool.values()], 3)
    returns = empirical_boundaries([row["prior20"] for row in market_pool.values()], 5)
    event_size, event_return = lower_bin(features[code]["market_cap"], size), lower_bin(features[code]["prior20"], returns)
    matched = [stock for stock, row in market_pool.items() if lower_bin(row["market_cap"], size) == event_size
               and lower_bin(row["prior20"], returns) == event_return]
    before_relax = len(matched)
    relaxed = before_relax < 10
    if relaxed:
        matched = [stock for stock, row in market_pool.items() if lower_bin(row["prior20"], returns) == event_return]
    if len(matched) < 10:
        return None, "matched_controls_below_10", {**counts, "initial_matched": before_relax, "relaxed_matched": len(matched)}
    return {"event_features": features[code], "control_features": {stock: pool[stock] for stock in sorted(matched)},
            "size_boundaries": size, "return_boundaries": returns, "size_bin": event_size,
            "return_bin": event_return, "size_relaxed": relaxed, "initial_matched": before_relax,
            "control_weight": 1 / len(matched)}, None, counts


def select_events(events, reader, listings, horizon, *, cancel=False):
    selections, exclusions, counts = [], [], Counter()
    for event in events:
        if cancel and not event["cancel"]:
            counts["not_cancellation_purpose"] += 1
            continue
        planned, reason = schedule(event, horizon, reader.sessions)
        # Forbidden horizons are removed before even a pre-entry price read.
        if reason is None:
            match, reason, detail = match_controls(planned, reader, listings)
        else:
            detail = {}
        counts[reason or "included"] += 1
        if reason is None:
            selections.append({**planned, **match, "candidate_counts": detail})
        else:
            exclusions.append({"rcept_no": event["rcept_no"], "reason": reason, "candidate_counts": detail})
    return sorted(selections, key=lambda row: (row["entry_date"], row["rcept_no"])), {"filter_counts": dict(counts),
        "sequential_filters": _filter_steps(len(events), counts, ("not_cancellation_purpose", "incomplete_calendar_horizon", "holdout_horizon",
            "event_liquidity_or_identity", "untradable_entry", "missing_entry_base", "limit_up_entry", "matched_controls_below_10")),
        "excluded": exclusions}


def value_position(reader, event, code, feature):
    first, target = event["entry_position"], event["exit_position"]
    if reader.sessions[target] >= CUTOFF:
        raise ValueError("holdout horizon must be excluded before valuation")
    rows = [reader.require(day).get(code) for day in reader.sessions[first:target + 1]]
    if not _traded(rows[0]):
        raise ValueError("entered stock must have an entry close")
    actual = max(i for i, row in enumerate(rows) if _traded(row))
    opening, mark = rows[0]["open"], rows[actual]["close"]
    factors, cumulative = [], rows[0]["close"] / opening
    for i, row in enumerate(rows):
        if i and i <= actual and _quoted(row):
            if not np.isfinite(row["ret_1d"]) or row["ret_1d"] < -1:
                raise ValueError("missing adjusted return on a fixed entered position")
            cumulative *= 1 + row["ret_1d"]
        factors.append(float(cumulative - 1))
    mark_date = reader.sessions[first + actual]
    stale = actual < target - first
    vanished = bool(stale and reader.last_trades().get(code, mark_date) <= mark_date)
    costs, sides = {}, {}
    for version, multiplier in [("v2", m) for m in MULTIPLIERS] + [("v1", 1.0)]:
        buy = cost_model.one_way_cost(opening, feature["market"], reader.sessions[first].date(), feature["adv"],
                                      "buy", multiplier=multiplier, model_version=version)
        sell = cost_model.one_way_cost(mark, feature["market"], mark_date.date(), feature["adv"],
                                       "sell", multiplier=multiplier, model_version=version)
        key = str(multiplier) if version == "v2" else "v1"
        costs[key], sides[key] = buy + sell, {"buy": buy, "sell": sell}
    return {"stock_code": code, "gross": factors[-1], "gross_path": factors, "costs": costs, "side_costs": sides,
            "valuation_date": mark_date.date().isoformat(), "scheduled_exit_date": event["exit_date"],
            "stale_exit": stale, "vanished": vanished,
            "gap": float(opening / feature["pre_close"] - 1)}


def _field_date(value):
    digits = "".join(re.findall(r"[0-9]", str(value)))
    try:
        return pd.Timestamp(datetime.strptime(digits, "%Y%m%d")) if len(digits) == 8 else None
    except ValueError:
        return None


def observations(selections, reader):
    results = []
    for event in selections:
        stock = value_position(reader, event, event["stock_code"], event["event_features"])
        controls = [value_position(reader, event, code, feature) for code, feature in event["control_features"].items()]
        start, end = (_field_date(event["structured"][key]) for key in ("aqexpd_bgd", "aqexpd_edd"))
        complete_period = (start is not None and end is not None and start <= end and len(reader.sessions)
                           and start >= reader.sessions.min() and end <= reader.sessions.max())
        periods = int(((reader.sessions >= start) & (reader.sessions <= end)).sum()) if complete_period else 0
        b = event["planned_amount"] / (periods * event["event_features"]["adv"]) if periods else None
        results.append({**event, "stock": stock, "controls": controls, "intensity": b, "planned_period_sessions": periods,
                        "intensity_reason": None if periods else "invalid_zero_or_incomplete_planned_period"})
    return results


def hac(values, entry_dates, grid, lag):
    values = np.asarray(values, dtype=float)
    dates, grid = pd.DatetimeIndex(entry_dates), _days(grid)
    if len(values) != len(dates) or not np.isfinite(values).all() or not dates.difference(grid).empty or lag < 0:
        raise ValueError("HAC requires finite event values and entry dates on the trading grid")
    n = len(values)
    mean = float(values.mean()) if n else None
    result = {"count": n, "mean": mean, "std": float(values.std(ddof=1)) if n > 1 else None,
              "standard_error": None, "t_stat": None, "p_value": 1.0,
              "lag": lag, "kernel": "Bartlett", "grid_sessions": len(grid), "small_sample_correction": False}
    if n > 1:
        sums = np.zeros(len(grid))
        np.add.at(sums, grid.get_indexer(dates), values - mean)
        variance = float(sums @ sums)
        for distance in range(1, min(lag, len(grid) - 1) + 1):
            variance += 2 * (1 - distance / (lag + 1)) * float(sums[distance:] @ sums[:-distance])
        variance /= n**2
        if np.isfinite(variance) and variance > 0:
            error = math.sqrt(variance)
            t = mean / error
            result.update(standard_error=error, t_stat=t, p_value=0.5 * math.erfc(t / math.sqrt(2)))
    return result


def cluster_t(values, dates):
    values = np.asarray(values, dtype=float)
    groups = pd.Series(values, index=pd.DatetimeIndex(dates)).groupby(level=0).mean() if len(values) else pd.Series(dtype=float)
    if len(groups) < 2:
        return None
    error = float(groups.std(ddof=1) / math.sqrt(len(groups)))
    return float(groups.mean() / error) if error > 0 else None


def _net(position, key="1.0", worthless=False):
    return (-1.0 if worthless and position["vanished"] else position["gross"]) - position["costs"][key]


def event_excess(row, key="1.0", worthless=False):
    return _net(row["stock"], key, worthless) - float(np.mean([_net(position, key, worthless) for position in row["controls"]]))


def random_controls(rows, *, worthless=False):
    ordered = sorted(rows, key=lambda row: (row["entry_date"], row["rcept_no"]))
    nets = [np.array([_net(position, worthless=worthless) for position in sorted(row["controls"], key=lambda value: value["stock_code"])]) for row in ordered]
    rng, paths = np.random.default_rng(0), np.empty((N_CONTROLS, len(nets)))
    for path in range(N_CONTROLS):
        for event, net in enumerate(nets):
            paths[path, event] = net[rng.integers(len(net))] - net.mean()
    means = paths.mean(axis=1) if nets else np.array([])
    actual = float(np.mean([event_excess(row, worthless=worthless) for row in ordered])) if rows else None
    return {"count": len(means), "seed": 0, "paths": paths.tolist(), "path_means": means.tolist(),
            "event_order": [row["rcept_no"] for row in ordered], "actual": actual,
            "share_of_controls": float(np.mean(means >= actual)) if rows else None,
            "mean": float(means.mean()) if rows else None,
            **{name: float(np.quantile(means, q)) if rows else None for name, q in (("p05", .05), ("p50", .5), ("p95", .95))}}


def _summary(rows, grid, horizon, *, key="1.0", worthless=False):
    return hac([event_excess(row, key, worthless) for row in rows], [row["entry_date"] for row in rows], grid, horizon - 1)


def variant_metrics(rows, grid, horizon, *, worthless=False):
    primary = _summary(rows, grid, horizon, worthless=worthless)
    design, validation = ([row for row in rows if row["period"] == period] for period in ("design", "validation"))
    design_mean = _summary(design, grid, horizon, worthless=worthless)["mean"]
    validation_mean = _summary(validation, grid, horizon, worthless=worthless)["mean"]
    return {"primary": primary, "valuation": "vanished_gross_minus_100pct" if worthless else "last_trading_close",
            "design_events": len(design), "dates": len({row["entry_date"] for row in rows}),
            "corporations": len({row["corp_code"] for row in rows}), "design_mean": design_mean, "validation_mean": validation_mean,
            "validation_year_same_sign": bool(np.sign(design_mean) == np.sign(validation_mean)) if design_mean is not None and validation_mean is not None else None,
            "validation_sign_used_for_judgement": False,
            "cost_sensitivity": {key: _summary(rows, grid, horizon, key=key, worthless=worthless) for key in ("1.0", "1.5", "2.0", "v1")},
            "random_control": random_controls(rows, worthless=worthless),
            "entry_date_cluster_t": cluster_t([event_excess(row, worthless=worthless) for row in rows], [row["entry_date"] for row in rows]),
            "vanished_counts": {"events": sum(row["stock"]["vanished"] for row in rows),
                                "controls": sum(position["vanished"] for row in rows for position in row["controls"]),
                                "unique_event_stocks": len({row["stock_code"] for row in rows if row["stock"]["vanished"]}),
                                "unique_control_stocks": len({position["stock_code"] for row in rows for position in row["controls"] if position["vanished"]})},
            "per_event": rows}


def holm(p_values):
    if set(p_values) != set(VARIANTS) or any(not np.isfinite(p) or not 0 <= p <= 1 for p in p_values.values()):
        raise ValueError("BB001 Holm requires exactly three finite probabilities")
    ordered = sorted(VARIANTS, key=lambda name: (p_values[name], VARIANTS.index(name)))
    corrected, previous = {}, 0.0
    for rank, name in enumerate(ordered):
        previous = max(previous, min(1.0, (3 - rank) * p_values[name]))
        corrected[name] = previous
    return corrected


def judge_variants(results, fields, trial_count_after_run, *, repository_threshold=2.0):
    criteria = fields["pass_criteria"]["design_validation"]
    minimum = criteria["minimum_observations"]["events"]
    missing = {name: results[name]["design_events"] < minimum or any(results[name]["primary"][key] is None for key in ("mean", "t_stat"))
               or results[name]["random_control"]["share_of_controls"] is None or results[name]["cost_sensitivity"]["1.5"]["mean"] is None for name in VARIANTS}
    adjusted = holm({name: 1.0 if missing[name] else results[name]["primary"]["p_value"] for name in VARIANTS})
    threshold = max(repository_threshold, criteria["core_t_stat_gt_when_trials_gt_20"] if trial_count_after_run > 20 else criteria["core_t_stat_gt"])
    for name in VARIANTS:
        result = results[name]
        primary, stressed, share = result["primary"], result["cost_sensitivity"]["1.5"]["mean"], result["random_control"]["share_of_controls"]
        failures = []
        if not missing[name]:
            for failed, reason in ((primary["mean"] <= criteria["net_performance_gt"], "mean net excess must be positive"),
                (stressed <= criteria["net_performance_at_cost_multiplier_1_5_gt"], "cost x1.5 mean excess must be positive"),
                (primary["t_stat"] <= threshold, f"HAC t must exceed {threshold:g}"),
                (adjusted[name] > .05, "Holm(3) one-sided p must be <=0.05"),
                (share > min(.05, criteria["random_control_top_percent"] / 100), "random-control share must be <=0.05")):
                if failed:
                    failures.append(reason)
        result.update(verdict="insufficient" if missing[name] else "fail" if failures else "pass",
            reasons=[f"requires >= {minimum} design events and defined primary/cost/control statistics"] if missing[name] else failures or ["all preregistered criteria satisfied"],
            judgement_inputs={"design_events": result["design_events"], "observations": primary["count"], "mean_excess": primary["mean"],
                "mean_excess_at_cost_1_5": stressed, "t_stat": primary["t_stat"], "holm_input_p": 1.0 if missing[name] else primary["p_value"],
                "holm_p": adjusted[name], "required_t_stat": float(threshold), "trial_count_after_run": trial_count_after_run,
                "random_control_share": share})
    return results


def apply_delisting_sensitivity(results, sensitivity):
    for name in VARIANTS:
        result, rerun = results[name], sensitivity[name]
        different = result["verdict"] != rerun["verdict"]
        result["delisting_sensitivity"] = {"sensitive": different, "base_verdict": result["verdict"],
                                           "verdict": rerun["verdict"], "judgement_inputs": rerun["judgement_inputs"]}
        if different:
            result.update(verdict="delisting_sensitive", reasons=["last-close and vanished -100% reruns have different verdicts"])


def reversal_adjustment(rows, grid, horizon):
    table = []
    for row in rows:
        record = {"rcept_no": row["rcept_no"], "entry_date": row["entry_date"], "e": event_excess(row)}
        for days in (5, 20):
            event = row["event_features"][f"prior{days}"]
            control = float(np.mean([value[f"prior{days}"] for value in row["control_features"].values()]))
            record.update({f"event_{days}": event, f"controls_{days}": control, f"difference_{days}": event - control})
        table.append(record)
    mean = lambda key: float(np.mean([row[key] for row in table])) if table else None
    balance = {"table": table, "means": {key: mean(key) for key in ("event_5", "controls_5", "difference_5", "event_20", "controls_20", "difference_20")},
               "differences": {str(days): hac([row[f"difference_{days}"] for row in table], [row["entry_date"] for row in table], grid, horizon - 1) for days in (5, 20)}}
    regression = {"count": len(table), "intercept": None, "slope": None, "intercept_standard_error": None, "intercept_t": None,
                  "lag": horizon - 1, "used_for_judgement": False, "reason": "insufficient_or_singular_design"}
    if len(table) >= 3:
        x = np.column_stack([np.ones(len(table)), [row["difference_20"] for row in table]])
        y = np.array([row["e"] for row in table])
        if np.linalg.matrix_rank(x) == 2:
            beta = np.linalg.lstsq(x, y, rcond=None)[0]
            scores = np.zeros((len(grid), 2))
            np.add.at(scores, pd.DatetimeIndex(grid).get_indexer(pd.DatetimeIndex([row["entry_date"] for row in table])), x * (y - x @ beta)[:, None])
            meat = scores.T @ scores
            for lag in range(1, min(horizon - 1, len(grid) - 1) + 1):
                product = scores[lag:].T @ scores[:-lag]
                meat += (1 - lag / horizon) * (product + product.T)
            bread = np.linalg.inv(x.T @ x)
            variance = float((bread @ meat @ bread)[0, 0])
            error = math.sqrt(variance) if variance > 0 else None
            regression.update(intercept=float(beta[0]), slope=float(beta[1]), intercept_standard_error=error,
                              intercept_t=float(beta[0] / error) if error else None, reason=None)
    return balance, regression


def literature_car(row, reader):
    public = pd.Timestamp(row["public_date"])
    result = {"rcept_no": row["rcept_no"], "car_0_21": None, "car_1_21": None, "reason": None}
    if public not in reader.sessions:
        return {**result, "reason": "non_session_receipt_no_disclosure_close"}
    first = reader.sessions.get_loc(public)
    last = first + 21
    if last >= len(reader.sessions) or reader.sessions[last] >= CUTOFF:
        return {**result, "reason": "holdout_or_incomplete_literature_horizon"}
    returns = {}
    for code in [row["stock_code"], *row["control_features"]]:
        values = []
        for day in reader.sessions[first:last + 1]:
            quote = reader.require(day).get(code)
            if not _quoted(quote) or not np.isfinite(quote["ret_1d"]) or quote["ret_1d"] < -1:
                return {**result, "reason": "missing_literature_adjusted_return"}
            values.append(quote["ret_1d"])
        returns[code] = [float(np.prod(1 + np.array(values)) - 1), float(np.prod(1 + np.array(values[1:])) - 1)]
    for index, key in enumerate(("car_0_21", "car_1_21")):
        result[key] = returns[row["stock_code"]][index] - float(np.mean([returns[code][index] for code in row["control_features"]]))
    return result


def calendar_portfolio(rows, grid):
    ordered = sorted(rows, key=lambda row: (row["entry_date"], row["rcept_no"]))
    entries = {}
    for row in ordered:
        entries.setdefault(pd.Timestamp(row["entry_date"]), []).append(row)
    actual, control = np.full(20, .05), np.full(20, .05)
    active, daily, accepted, overflow = {}, [], [], []
    for day in grid:
        for row in entries.get(day, []):
            free = next((slot for slot in range(20) if slot not in active), None)
            if free is None:
                overflow.append(row["rcept_no"])
                continue
            active[free] = (row, float(actual[free]), float(control[free]), 0)
            accepted.append({"rcept_no": row["rcept_no"], "slot": free, "entry_date": row["entry_date"], "exit_date": row["exit_date"]})
        closed = []
        for slot, (row, event_base, control_base, age) in list(active.items()):
            final = age == row["horizon"] - 1
            def wealth(position):
                side = position["side_costs"]["1.0"]
                return 1 + position["gross_path"][age] - side["buy"] - (side["sell"] if final else 0)
            actual[slot] = event_base * wealth(row["stock"])
            control[slot] = control_base * float(np.mean([wealth(position) for position in row["controls"]]))
            active[slot] = (row, event_base, control_base, age + 1)
            if final:
                closed.append(slot)
        daily.append({"date": day.date().isoformat(), "event_nav": float(actual.sum()), "control_nav": float(control.sum()), "active_slots": len(active)})
        for slot in closed:
            del active[slot]
    monthly, before_event, before_control = [], 1.0, 1.0
    for month in sorted({row["date"][:7] for row in daily}):
        last = [row for row in daily if row["date"].startswith(month)][-1]
        event_return = last["event_nav"] / before_event - 1 if before_event > 0 else None
        control_return = last["control_nav"] / before_control - 1 if before_control > 0 else None
        monthly.append({"month": month, "event_net": event_return, "controls_net": control_return,
                        "net_excess": event_return - control_return if event_return is not None and control_return is not None else None})
        before_event, before_control = last["event_nav"], last["control_nav"]
    return {"max_slots": 20, "accepted": accepted, "overflow_rcept_nos": overflow, "daily": daily,
            "monthly": monthly, "monthly_net_excess_mean": float(np.mean([row["net_excess"] for row in monthly if row["net_excess"] is not None])) if monthly else None,
            "used_for_judgement": False}


def secondary_metrics(rows, reader, grid, horizon):
    balance, regression = reversal_adjustment(rows, grid, horizon)
    car = [literature_car(row, reader) for row in rows]
    valid_b = [row["intensity"] for row in rows if row["intensity"] is not None]
    boundaries = empirical_boundaries(valid_b, 3) if valid_b else []
    mean_car = lambda key: float(np.mean([row[key] for row in car if row[key] is not None])) if any(row[key] is not None for row in car) else None
    return {"gap_mean": float(np.mean([row["stock"]["gap"] for row in rows])) if rows else None,
            "reversal_balance": balance, "continuous_adjustment": regression,
            "literature_car": {"per_event": car, "car_0_21_mean": mean_car("car_0_21"), "car_1_21_mean": mean_car("car_1_21")},
            "intensity_terciles": {"boundaries": boundaries, "undefined": len(rows) - len(valid_b),
                "groups": {str(bucket): _summary([row for row in rows if row["intensity"] is not None and lower_bin(row["intensity"], boundaries) == bucket], grid, horizon) for bucket in range(3)}},
            "by_year": {year: _summary([row for row in rows if row["public_date"].startswith(year)], grid, horizon) for year in sorted({row["public_date"][:4] for row in rows})},
            "short_sale_regimes": {label: _summary([row for row in rows if market_regimes.regime_label(row["public_date"])["short_sale"] == label], grid, horizon) for label in sorted({market_regimes.regime_label(row["public_date"])["short_sale"] for row in rows})},
            "liquidity_buckets": {str(bucket): _summary([row for row in rows if np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, row["event_features"]["adv"], side="right") == bucket], grid, horizon) for bucket in range(4)},
            "calendar_time_portfolio": calendar_portfolio(rows, grid)}


def _grid(sessions):
    return sessions[(sessions >= DESIGN_START) & (sessions <= END)]


def _input_hashes(data_dir, repo_root, files, listings):
    paths = list(files.values())
    paths += [Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "preregistration.md"]
    paths += [Path(__file__), Path(dart.__file__), Path(cost_model.__file__), Path(holdout.__file__),
              Path(experiment_registry.__file__), Path(common.__file__), Path(market_regimes.__file__), Path(hc001.__file__)]
    paths += [PROJECT_ROOT / relative for relative in ("src/ingestion/dart.py", "src/ingestion/dart_backfill.py",
        "src/ingestion/dart_api.py", "src/ingestion/storage.py", "src/utils/stock_codes.py", "src/runner/trading_calendar.py")]
    for corp in sorted({row["corp_code"] for row in listings if dart.title_matches(row["report_nm"])}):
        path = dart._path(data_dir, "structured", corp)
        if path.is_file():
            paths.append(path)
    for row in listings:
        if dart.title_matches(row["report_nm"]):
            path = dart._path(data_dir, "documents", row["rcept_no"])
            if path.is_file():
                paths.append(path)
    return {str(path): _sha(path) for path in dict.fromkeys(paths)}


def write_results(payload, *, repo_root=PROJECT_ROOT):
    _registration(repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    lines = [f"# {EXPERIMENT_ID}", "", f"Fixed primary d21; verdict: {payload['verdict']}", "",
             "Reasons: " + "; ".join(payload["reasons"]), "",
             "| Variant | Design events | All events | Excess | HAC t | Holm p | x1.5 excess | Controls >= actual | Verdict |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for name, result in payload["variants"].items():
        p, j = result["primary"], result["judgement_inputs"]
        lines.append(f"| {name} | {result['design_events']} | {p['count']} | {p['mean']} | {p['t_stat']} | {j['holm_p']} | {j['mean_excess_at_cost_1_5']} | {j['random_control_share']} | {result['verdict']} |")
    lines += ["", "## Interpretations", "", INTERPRETATIONS["note"], ""]
    for section in ("criteria", "metrics"):
        lines += [f"- {name}: {definition}" for name, definition in INTERPRETATIONS[section].items()]
    lines += ["", "## Assumptions", "", *[f"- {value}" for value in payload["assumptions"]]]
    directory = Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = directory / f"{stamp}.json", directory / f"{stamp}.md"
    with paths[0].open("x", encoding="utf-8") as jf:
        with paths[1].open("x", encoding="utf-8") as mf:
            jf.write(encoded)
            mf.write("\n".join(lines) + "\n")
    return paths


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    started, registration = time.perf_counter(), _registration(repo_root)
    sessions = _days(sessions)
    listings, files, listing_counts = dart.load_listings(data_dir)
    hashes = _input_hashes(data_dir, repo_root, files, listings)
    events, construction = construct_events(listings, files, sessions, data_dir)
    reader, grid = PriceReader(data_dir, sessions, prices=prices, repo_root=repo_root), _grid(sessions)
    supplied = hashlib.sha256(pd.util.hash_pandas_object(prices.sort_index(), index=True).values.tobytes()).hexdigest() if prices is not None else None
    selections, filtering, observed = {}, {}, {}
    for name in VARIANTS:
        selections[name], filtering[name] = select_events(events, reader, listings, HORIZONS[name], cancel=name == "d21_cancel")
        observed[name] = observations(selections[name], reader)
    trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3
    threshold = experiment_registry.required_t_stat(EXPERIMENT_ID, repo_root=repo_root)
    variants = judge_variants({name: variant_metrics(observed[name], grid, HORIZONS[name]) for name in VARIANTS}, registration.fields, trials, repository_threshold=threshold)
    sensitivity = judge_variants({name: variant_metrics(observed[name], grid, HORIZONS[name], worthless=True) for name in VARIANTS}, registration.fields, trials, repository_threshold=threshold)
    apply_delisting_sensitivity(variants, sensitivity)
    for name in VARIANTS:
        variants[name]["secondary"] = secondary_metrics(observed[name], reader, grid, HORIZONS[name])
        variants[name]["secondary_horizons"] = {}
        for horizon in (1, 5, 63):
            selected, excluded = select_events(events, reader, listings, horizon, cancel=name == "d21_cancel")
            additional = observations(selected, reader)
            variants[name]["secondary_horizons"][str(horizon)] = {"primary": _summary(additional, grid, horizon), "filters": excluded,
                "entry_date_cluster_t": cluster_t([event_excess(row) for row in additional], [row["entry_date"] for row in additional])}
    if prices is not None:
        if supplied != hashlib.sha256(pd.util.hash_pandas_object(prices.sort_index(), index=True).values.tobytes()).hexdigest():
            raise ValueError("BB001 supplied price frame changed during evaluation")
    all_hashes = {**hashes, **reader.source_hashes}
    for path, digest in all_hashes.items():
        if path in reader.source_hashes:
            day = pd.Timestamp(Path(path).stem)
            holdout.guard_period(day.date(), day.date(), repo_root=repo_root)
        if _sha(path) != digest:
            raise ValueError("BB001 source changed during evaluation")
    primary = variants["d21"]
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": "d21", "verdict": primary["verdict"], "reasons": primary["reasons"],
        "prereg_commit": registration.commit_hash, "variants": variants, "delisting_sensitivity": sensitivity,
        "counts": {"listings": listing_counts, "construction": construction, "variants": filtering},
        "coverage": {"listings": dart.plan(data_dir)["listing_coverage"], "calendar_version": f"exchange-calendars:{hc001.calendars.__version__}:XKRX"},
        "source_hashes": all_hashes, "supplied_price_frame_sha256": supplied,
        "interpretations": INTERPRETATIONS, "assumptions": ASSUMPTIONS,
        "regime_markers": {"2024-12-31": "buyback disclosure strengthening", "2025-12-30": "1% disclosure threshold",
                           "2026-03-06": "mandatory cancellation principle; holdout, not evaluated", "2026-06-30": "trust disposal ban; holdout, not evaluated"},
        "performance": {"runtime_seconds": time.perf_counter() - started, "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    json.dumps(payload, allow_nan=False)
    for name in VARIANTS:
        experiment_registry.record_trial(EXPERIMENT_ID, name,
            {**variants[name]["judgement_inputs"], "delisting_sensitivity": variants[name]["delisting_sensitivity"]},
            variants[name]["verdict"], repo_root=repo_root, note="fixed BB001 variant; grid HAC, Holm(3), side-specific v2 costs; no holdout")
    write_results(payload, repo_root=repo_root)
    return payload


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prices=None, sessions=None):
    registration, sessions = _registration(repo_root), _days(sessions)
    listings, files, listing_counts = dart.load_listings(data_dir)
    events, construction = construct_events(listings, files, sessions, data_dir)
    reader, variants = PriceReader(data_dir, sessions, prices=prices, repo_root=repo_root), {}
    for name in VARIANTS:
        selected, filters = select_events(events, reader, listings, HORIZONS[name], cancel=name == "d21_cancel")
        variants[name] = {"selected_before_returns": len(selected), "design_events_before_returns": sum(row["period"] == "design" for row in selected),
                          "filter_counts": filters["filter_counts"], "sequential_filters": filters["sequential_filters"],
                          "candidate_counts": [{"rcept_no": row["rcept_no"], **row["candidate_counts"]} for row in selected]}
    directory = Path(data_dir) / "market/krx_daily"
    price_files = [path for path in directory.glob("*/*.jsonl") if path.stem.isdigit() and len(path.stem) == 8 and dart.START <= path.stem <= dart.END and path.stat().st_size]
    return {"experiment_id": EXPERIMENT_ID, "status": "dry_run", "data_dir": str(data_dir), "primary_variant": "d21",
            "prereg_commit": registration.commit_hash, "listings": listing_counts, "construction": construction,
            "variants": variants, "dart_plan": dart.plan(data_dir), "price_files": len(price_files),
            "price_files_by_year": {str(year): sum(path.stem.startswith(str(year)) for path in price_files) for year in range(2016, 2026)},
            "minimum_design_events": registration.fields["pass_criteria"]["design_validation"]["minimum_observations"]["events"],
            "trial_count_after_run": experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + 3,
            "note": "No returns, random paths, API calls, registry rows or result files. Exits crossing 2026 excluded before any price read.",
            "assumptions": ASSUMPTIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; d21 events={result['variants']['d21']['primary']['count']}")
    else:
        print(json.dumps(dry_run(data_dir=args.data_dir), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
