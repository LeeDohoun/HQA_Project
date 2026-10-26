"""Baseline against a replayed variant: every number of the comparison, from saved outputs only.

Both directories hold a replay (decisions.jsonl, result.json); the variant's may also hold its
replay audit (for retrieved-context use and citations) and calls.json (for its paid tokens).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np

from backtesting.fitness import analysis, metrics
from backtesting.fitness.arms import Features, entry_filters
from backtesting.fitness.simulate import PricePanel

WINDOW = ("2026-07-01", "2026-10-16")


def arm_summary(directory: Path, panel: PricePanel, features: Features) -> dict:
    decisions = analysis.read_decisions(directory / "decisions.jsonl")
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    nulls = analysis.null_picks(decisions, panel, features, start=WINDOW[0], end=WINDOW[1], cash=result["initial_cash"],
                                cost_bps=15.0, draws=500)
    buys = [(row["day"], plan["stock_code"]) for row in decisions for plan in row.get("plans") or []
            if plan["action"] == "BUY" and plan["stock_code"] not in (row.get("holdings") or [])]
    return {"window": metrics.summarize(result, *WINDOW),
            "closed": [{k: c[k] for k in ("code", "entry_day", "exit_day", "reason", "return")} for c in result["closed"]],
            "buys": buys, "ic": analysis.ic_robust(decisions, panel, features, horizons=(1, 5, 10)),
            "top5": [analysis.top5_spread(decisions, panel, h) for h in (1, 5, 10)],
            "gate": [analysis.gate_difference(decisions, panel, h) for h in (1, 5, 10)],
            "nulls": {"llm_percentile_among_random": nulls["llm_percentile_among_random"],
                      "random_total_return_pct": nulls["random_total_return_pct"],
                      "llm_mechanical": metrics.summarize(nulls["results"]["llm_mechanical"], *WINDOW)["total_return_pct"]},
            "_decisions": decisions}


def flips(base: list[dict], variant: list[dict]) -> dict:
    """RiskManager actions on the same new stock and day, and how often the reviewed set changed."""
    by_day = {row["day"]: row for row in variant}
    same_set = changed = 0
    pairs, flipped = Counter(), []
    for row in base:
        other = by_day.get(row["day"])
        if other is None:
            continue
        held_a, held_b = set(row.get("holdings") or []), set(other.get("holdings") or [])
        a = {p["stock_code"]: p["action"] for p in row.get("plans") or [] if p["stock_code"] not in held_a}
        b = {p["stock_code"]: p["action"] for p in other.get("plans") or [] if p["stock_code"] not in held_b}
        same_set += set(a) == set(b)
        changed += set(a) != set(b)
        for code in set(a) & set(b):
            pairs[(a[code], b[code])] += 1
            if a[code] != b[code]:
                flipped.append((row["day"], code, a[code], b[code]))
    return {"days_same_reviewed_set": same_set, "days_changed_set": changed,
            "action_pairs": {f"{k[0]}->{k[1]}": v for k, v in pairs.items()}, "flipped": flipped}


def analyst_scores(base: list[dict], variant: list[dict]) -> dict:
    by_day = {row["day"]: row for row in variant}
    pairs = []
    for row in base:
        other = by_day.get(row["day"], {})
        for code, roles in (row.get("specialists") or {}).items():
            a = roles.get("analyst", {}).get("score")
            b = (other.get("specialists") or {}).get(code, {}).get("analyst", {}).get("score")
            if a is not None and b is not None:
                pairs.append((a, b))
    if not pairs:
        return {"pairs": 0}
    a, b = np.array(pairs).T
    return {"pairs": len(pairs), "baseline_mean": round(float(a.mean()), 2), "variant_mean": round(float(b.mean()), 2),
            "mean_abs_diff": round(float(np.abs(a - b).mean()), 2),
            "spearman": analysis._spearman(list(a), list(b))}


def retrieval_use(audit: Path) -> dict:
    """From the variant's replay audit: analyst requests with retrieved items, items shown,
    omissions for budget, and answers citing a retrieved item."""
    db = sqlite3.connect(str(audit))
    requests, responses = {}, []
    for rid, body in db.execute("SELECT id, payload FROM paper_events WHERE kind='llm_request'"):
        request = json.loads(body)
        if request["role"] == "analyst":
            requests[rid] = request["payload"]
    for (body,) in db.execute("SELECT payload FROM paper_events WHERE kind='llm_response'"):
        response = json.loads(body)
        if response["request_id"] in requests:
            responses.append(response)
    shown = [len(p.get("retrieved_context") or []) for p in requests.values()]
    omitted = [int(gap.rsplit(":", 1)[1]) for p in requests.values() for gap in p.get("data_gaps") or []
               if gap.startswith("retrieved_context_omitted_for_input_budget:")]
    kinds = Counter(item["context_kind"] for p in requests.values() for item in p.get("retrieved_context") or [])
    citing = [r for r in responses if any(c["source_id"].startswith("retrieved:") for c in r["output"]["citations"])]
    citations = [c["source_id"] for r in responses for c in r["output"]["citations"]]
    return {"analyst_requests": len(requests), "with_retrieved": sum(1 for n in shown if n),
            "items_shown_mean": round(float(np.mean(shown)), 2) if shown else None, "items_by_kind": dict(kinds),
            "requests_with_omissions": len(omitted), "items_omitted": sum(omitted),
            "answers": len(responses), "answers_citing_retrieved": len(citing),
            "citation_share_retrieved": round(sum(c.startswith("retrieved:") for c in citations) / len(citations), 3)
            if citations else None}


def paid_tokens(calls: Path) -> dict:
    events = json.loads(calls.read_text(encoding="utf-8"))["events"]
    out: dict = {}
    for event in events:
        if event["kind"] != "paid":
            continue
        usage = event.get("usage") or {}
        row = out.setdefault(event["role"], {"calls": 0, "tokens": 0})
        row["calls"] += 1
        row["tokens"] += (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit", default=".local/fitness/pitdata")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--variant-calls", nargs="*", default=[], help="calls.json of every paid variant run")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    panel = PricePanel.from_pit(Path(args.pit))
    features = Features(panel, entry_filters())
    base = arm_summary(Path(args.baseline), panel, features)
    variant = arm_summary(Path(args.variant), panel, features)
    report = {"window": WINDOW, "baseline": base, "variant": variant,
              "flips": flips(base["_decisions"], variant["_decisions"]),
              "analyst_scores": analyst_scores(base["_decisions"], variant["_decisions"])}
    audit = Path(args.variant) / "replay_audit.sqlite3"
    if audit.exists():
        report["retrieval_use"] = retrieval_use(audit)
    totals: dict = {}
    for path in args.variant_calls:
        for role, row in paid_tokens(Path(path)).items():
            total = totals.setdefault(role, {"calls": 0, "tokens": 0})
            total["calls"] += row["calls"]
            total["tokens"] += row["tokens"]
    report["variant_paid"] = totals
    for arm in ("baseline", "variant"):
        report[arm].pop("_decisions")
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
