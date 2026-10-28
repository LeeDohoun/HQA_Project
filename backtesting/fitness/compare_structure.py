"""The baseline against replayed review structures (`RISK_REVIEW_STRUCTURES`), from saved outputs only.

Next to each arm's account numbers and random-pick percentile (`compare_variant.arm_summary`) it
measures what each structural fix is meant to change:

- the stocks shown: the forward return of the new stocks the RiskManager reviewed each day minus
  the other completed stocks (the baseline shows the five best specialist averages, v9 the five
  best rule scores);
- the plan shape: each BUY's stop against the stock's mean daily range, BUYs refused for a stop
  inside it, and positions stopped out on the day they were bought;
- the holding reviews: held plans that tried to lower the stop (the simulator kept the old plan;
  v9 rejects them before publishing);
- what each arm's paid calls cost.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from backtesting.fitness import analysis
from backtesting.fitness.arms import Features, entry_filters
from backtesting.fitness.compare_variant import WINDOW, arm_summary, flips, paid_tokens
from backtesting.fitness.simulate import PricePanel

HORIZONS = (1, 5, 10)


def shown_spread(decisions: list[dict], panel: PricePanel, horizon: int) -> dict:
    """Mean of (new stocks the RiskManager reviewed minus the other completed stocks), per day."""
    spreads, shown_returns = [], []
    for row in decisions:
        held = set(row.get("holdings") or [])
        shown = [r["stock_code"] for r in row.get("account_rows") or [] if r["stock_code"] not in held]
        completed = [r["stock_code"] for r in row.get("leaders") or [] if r.get("leader_score") is not None
                     and r["stock_code"] not in held]
        returns = {code: analysis.forward_return(panel, code, row["day"], horizon) for code in completed}
        top = [returns[code] for code in shown if returns.get(code) is not None]
        rest = [value for code, value in returns.items() if code not in shown and value is not None]
        if not top or not rest:
            continue
        shown_returns.extend(top)
        spreads.append(float(np.mean(top) - np.mean(rest)))
    values = np.array(spreads)
    t = analysis.newey_west_t(values, horizon - 1) if len(values) > horizon else None
    return {"horizon": horizon, "days": len(values),
            "shown_minus_rest_pct": round(float(values.mean()) * 100, 2) if len(values) else None,
            "hac_t": None if t is None else round(t, 2),
            "shown_mean_pct": round(float(np.mean(shown_returns)) * 100, 2) if shown_returns else None}


def reason_kind(reason: str) -> str:
    if "inside the daily-range floor" in reason:
        return "stop_floor"
    if "below the active stop" in reason:
        return "held_stop_lowered"
    return reason.split(":", 1)[0][:60]


def structure_metrics(decisions: list[dict], result: dict, panel: PricePanel) -> dict:
    buys, held_reviews, rejected, lowered_rejected = [], 0, Counter(), 0
    for row in decisions:
        held = set(row.get("holdings") or [])
        for plan in row.get("plans") or []:
            if plan["stock_code"] in held:
                held_reviews += 1
            elif plan["action"] == "BUY":
                buys.append((row["day"], plan["stock_code"]))
        for item in row.get("rejected") or []:
            kind = reason_kind(str(item.get("reason")))
            rejected[kind] += 1
            if item["stock_code"] in held:
                held_reviews += 1
                lowered_rejected += kind == "held_stop_lowered"
    events = Counter(event["event"] for event in result["events"])
    stops = analysis.stops_vs_volatility(decisions, panel)
    closed = result["closed"]
    filled = {(trade["day"], trade["code"]) for trade in result["trades"] if trade["side"] == "BUY"}
    returns = [c["return"] for c in closed]
    return {
        "buy_plans": len(buys), "filled_buys": len(filled),
        "unfilled": {key: value for key, value in events.items() if key.startswith("buy_")},
        "rejected_by_kind": dict(rejected),
        "stops": stops,
        "stops_inside_mean_range": sum(1 for s in stops if -s["stop_pct"] < s["avg_range_pct"]),
        "stop_pct_median": float(np.median([s["stop_pct"] for s in stops])) if stops else None,
        "closed": len(closed), "same_day_losses": sum(1 for c in closed if c["entry_day"] == c["exit_day"]
                                                      and c["return"] < 0),
        "per_trade_mean_pct": round(float(np.mean(returns)) * 100, 2) if returns else None,
        "win_rate_pct": round(100 * sum(r > 0 for r in returns) / len(returns), 1) if returns else None,
        "held_reviews": held_reviews,
        "held_stop_lowering": {"kept_old_plan_in_simulator": events.get("plan_update_kept_old_stop", 0),
                               "rejected_before_publishing": lowered_rejected},
        "shown": [shown_spread(decisions, panel, h) for h in HORIZONS],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pit", default=".local/fitness/pitdata")
    parser.add_argument("--arm", action="append", required=True, help="name=replay directory; the first is the baseline")
    parser.add_argument("--calls", action="append", default=[], help="name=calls.json of a paid run, repeatable")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    panel = PricePanel.from_pit(Path(args.pit))
    features = Features(panel, entry_filters())
    arms = dict(item.split("=", 1) for item in args.arm)
    report: dict = {"window": WINDOW, "arms": {}}
    decisions = {}
    for name, directory in arms.items():
        summary = arm_summary(Path(directory), panel, features)
        decisions[name] = summary.pop("_decisions")
        result = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
        report["arms"][name] = {**summary, "structure": structure_metrics(decisions[name], result, panel)}
    baseline = next(iter(arms))
    report["flips_vs_baseline"] = {name: flips(decisions[baseline], decisions[name]) for name in arms if name != baseline}
    paid: dict = {}
    for item in args.calls:
        name, path = item.split("=", 1)
        for role, row in paid_tokens(Path(path)).items():
            total = paid.setdefault(name, {}).setdefault(role, {"calls": 0, "tokens": 0})
            total["calls"] += row["calls"]
            total["tokens"] += row["tokens"]
    report["paid"] = paid
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
