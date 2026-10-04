"""A2 CB overhang exclusion, applied to usable events before long selection.

A1 lock-up expiry data is still undecided and is outside M5's scope. Only
issuance and exchangeable_issuance add supply; CB/BW/EB revisions are separate.
Repurchases reduce an identified issue in proportion to its original face
amount. Missing or ambiguous matches are counted without reducing exposure.
Correction repurchases lack transaction links and are counted as unmatched.
This is potential supply, including EB treasury shares, not unsold inventory.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import PointInTimeData, _timestamp


def cb_overhang_exclusions(as_of, events: pd.DataFrame, prices: pd.DataFrame,
                           threshold_pct: float = 5.0, lookahead_days: int = 30) -> tuple[list[str], pd.DataFrame]:
    """Return sorted excluded codes and a per-stock table including skipped counts.

Windows are inclusive calendar dates. Parsed percentages take precedence over
share counts; otherwise use the latest usable session's listed_shares. Missing
windows/denominators/features are counted, never interpreted as zero exposure.
"""
    if not np.isfinite(threshold_pct) or threshold_pct <= 0:
        raise ValueError("threshold_pct must be finite and positive")
    if type(lookahead_days) is not int or lookahead_days < 0:
        raise ValueError("lookahead_days must be a nonnegative integer")
    cutoff = _timestamp(as_of)
    day = cutoff.tz_localize(None).normalize()
    data = PointInTimeData(prices, events)
    known = data.events_as_of(cutoff, None)
    columns = ["stock_code", "overhang_pct", "event_count", "unparsed_count", "matched_repurchase_count",
               "unmatched_repurchase_count", "ignored_event_count", "excluded", "reason"]
    if known.empty:
        return [], pd.DataFrame(columns=columns)
    known = known.loc[known.category == "convertible_bond"].copy()
    if not known.empty and "event_subtype" not in known:
        raise ValueError("bond events require event_subtype")
    supplies = known.loc[known.event_subtype.isin(["issuance", "exchangeable_issuance"])] if not known.empty else known
    if "bond_round" in supplies:
        linked = supplies.bond_round.notna()
        keys = ["stock_code", "event_subtype", "bond_round"]
        if "bond_type" in supplies:
            keys.append("bond_type")
        supplies = pd.concat([supplies.loc[~linked], supplies.loc[linked].drop_duplicates(keys, keep="last")])
    current = data.prices_as_of(cutoff, 1)
    shares = current.listed_shares.droplevel("trade_date") if "listed_shares" in current else pd.Series(dtype=float)
    totals = {code: {"stock_code": code, "overhang_pct": np.nan, "event_count": 0, "unparsed_count": 0,
                     "matched_repurchase_count": 0, "unmatched_repurchase_count": 0, "ignored_event_count": 0}
              for code in known.stock_code.unique()}
    pending = []
    for event in supplies.to_dict("records"):
        code = event["stock_code"]
        summary = totals[code]
        start = pd.to_datetime(event.get("conversion_start"), errors="coerce")
        end = pd.to_datetime(event.get("conversion_end"), errors="coerce")
        corrected = event.get("is_correction")
        unlinked_correction = pd.notna(corrected) and bool(corrected) and pd.isna(event.get("bond_round"))
        if pd.isna(start) or pd.isna(end) or end < start or unlinked_correction:
            summary["unparsed_count"] += 1
            continue
        if start > day + pd.Timedelta(days=lookahead_days) or end < day:
            continue
        pct = event.get("conversion_shares_pct", np.nan)
        if pd.isna(pct):
            count, listed = event.get("conversion_shares", np.nan), shares.get(code, np.nan)
            pct = count / listed * 100 if pd.notna(count) and np.isfinite(listed) and listed > 0 else np.nan
        if pd.isna(pct) or not np.isfinite(pct) or pct < 0:
            summary["unparsed_count"] += 1
            continue
        pending.append({**event, "pct": pct, "repurchased": 0.0})
        summary["event_count"] += 1
    for event in known.to_dict("records"):
        subtype, code = event["event_subtype"], event["stock_code"]
        summary = totals[code]
        if subtype in {"issuance", "exchangeable_issuance"}:
            continue
        if pd.isna(subtype) or subtype != "early_repurchase":
            summary["ignored_event_count"] += 1
            continue
        amount, round_number = event.get("repurchased_amount_krw"), event.get("bond_round")
        kind, corrected = event.get("bond_type"), event.get("is_correction")
        matches = [issue for issue in pending if issue["stock_code"] == code
                   and pd.notna(round_number) and pd.notna(issue.get("bond_round"))
                   and issue.get("bond_round") == round_number
                   and (pd.isna(kind) or pd.notna(issue.get("bond_type")) and issue.get("bond_type") == kind)]
        if (len(matches) != 1 or pd.isna(amount) or not np.isfinite(amount) or amount <= 0
                or pd.notna(corrected) and bool(corrected)):
            summary["unmatched_repurchase_count"] += 1
            continue
        issue = matches[0]
        original_amount = issue.get("issue_amount_krw")
        if (pd.isna(original_amount) or not np.isfinite(original_amount) or original_amount <= 0
                or amount > original_amount - issue["repurchased"]):
            summary["unmatched_repurchase_count"] += 1
            continue
        issue["repurchased"] += amount
        summary["matched_repurchase_count"] += 1
    # Recompute from remaining face amounts so full repurchases yield exactly zero.
    for code, summary in totals.items():
        issues = [issue for issue in pending if issue["stock_code"] == code]
        if issues:
            summary["overhang_pct"] = sum(issue["pct"] * (1 - issue["repurchased"] / issue["issue_amount_krw"])
                                          if issue["repurchased"] else issue["pct"] for issue in issues)
    rows = []
    for code, summary in sorted(totals.items()):
        excluded = bool(pd.notna(summary["overhang_pct"]) and summary["overhang_pct"] >= threshold_pct)
        if summary["event_count"]:
            reason = f"Convertible window exposure {summary['overhang_pct']:.4g}%; threshold {threshold_pct:g}%"
        else:
            reason = "No parsed convertible exposure in the requested window"
        if summary["unparsed_count"]:
            reason += f"; skipped {summary['unparsed_count']} unparsed event(s)"
        if summary["matched_repurchase_count"] or summary["unmatched_repurchase_count"]:
            reason += (f"; repurchases matched {summary['matched_repurchase_count']}, "
                       f"unmatched {summary['unmatched_repurchase_count']}")
        if summary["ignored_event_count"]:
            reason += f"; ignored {summary['ignored_event_count']} non-issuance event(s)"
        rows.append({**summary, "excluded": excluded, "reason": reason})
    reasons = pd.DataFrame(rows, columns=columns)
    return reasons.loc[reasons.excluded, "stock_code"].tolist(), reasons
