"""Every number of the fitness report, computed from saved outputs only (no model or broker calls).

Inputs: the main replay's out directory (decisions.jsonl, result.json), the replicate runs, the
rule-arm results, the point-in-time copy and a copy of the PAPER audit. Output: one JSON file.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from backtesting.fitness import analysis, metrics
from backtesting.fitness.arms import Features, entry_filters
from backtesting.fitness.simulate import PricePanel

KST = timezone(timedelta(hours=9))
MONTHS = {"2026-07": ("2026-07-01", "2026-07-31"), "2026-08": ("2026-08-01", "2026-08-31"),
          "2026-09": ("2026-09-01", "2026-09-30"), "2026-10": ("2026-10-01", "2026-10-16")}


def _plan_shape(plan: dict, panel: PricePanel, day: str) -> dict:
    entry = float(plan["entry_price"])
    exit_day = plan["planned_exit_at"][:10]
    return {"day": day, "code": plan["stock_code"], "size_pct": plan["position_size_pct"],
            "confidence": plan["confidence"], "risk": plan["risk_level"],
            "stop_pct": round((float(plan["stop_loss_price"]) / entry - 1) * 100, 2),
            "target_pct": round((float(plan["take_profit_price"]) / entry - 1) * 100, 2),
            "reduces": len(plan["condition_payload"]["reduce_conditions"]),
            "exit_sessions": sum(1 for d in panel.calendar if day <= d <= exit_day) or None,
            "exit_calendar_days": (datetime.fromisoformat(exit_day) - datetime.fromisoformat(day)).days}


def decision_stats(decisions: list[dict], panel: PricePanel) -> dict:
    by_month = defaultdict(lambda: Counter())
    buys, holds_on_held, errors, rejected = [], Counter(), Counter(), []
    for row in decisions:
        month = row["day"][:7]
        held = set(row.get("holdings") or [])
        by_month[month]["days"] += 1
        by_month[month]["completed"] += row["completed"]
        for plan in row.get("plans") or []:
            if plan["stock_code"] in held:
                holds_on_held[plan["action"]] += 1
                continue
            by_month[month]["new_reviews"] += 1
            if plan["action"] == "BUY":
                by_month[month]["buys"] += 1
                buys.append(_plan_shape(plan, panel, row["day"]))
        for item in row.get("rejected") or []:
            rejected.append({"day": row["day"], "code": item["stock_code"], "action": item["action"],
                             "reason": item["reason"][:160]})
        for item in row.get("errors") or []:
            if "cannot change a decision" not in item["error"]:
                errors[f"{item['stage']}: {item['error'][:80]}"] += 1
        if row.get("status") != "completed":
            errors[f"account: {row.get('status')} {str(row.get('error'))[:80]}"] += 1
    months = {m: {"days": c["days"], "avg_completed": round(c["completed"] / c["days"], 1),
                  "new_reviews": c["new_reviews"], "buys": c["buys"]} for m, c in sorted(by_month.items())}
    total_reviews = sum(m["new_reviews"] for m in months.values())
    return {"months": months, "new_reviews": total_reviews, "buys": len(buys),
            "buy_rate_pct": round(len(buys) / total_reviews * 100, 2) if total_reviews else None,
            "buy_plans": buys, "held_reviews": dict(holds_on_held), "rejected": rejected, "errors": dict(errors)}


def paper_record(audit: Path, panel: PricePanel) -> dict:
    """PAPER's own RiskManager decisions and account path from a copy of its audit."""
    db = sqlite3.connect(str(audit))
    out: dict = {"users": {}}
    for recorded, payload in db.execute("SELECT recorded_at, payload FROM paper_events WHERE kind='analysis' ORDER BY id"):
        cycle = json.loads(payload)
        day = datetime.fromisoformat(recorded).astimezone(KST).date().isoformat()
        for user, account in (cycle.get("accounts") or {}).items():
            record = out["users"].setdefault(user, {"days": Counter(), "cycles": 0, "completed_cycles": 0,
                                                    "new_reviews": 0, "buys": [], "held_actions": Counter()})
            record["cycles"] += 1
            record["days"][day] += 1
            record["completed_cycles"] += account.get("status") == "completed"
            for plan in account.get("plans") or []:
                if plan["holding_quantity"] > 0:
                    record["held_actions"][plan["action"]] += 1
                    continue
                record["new_reviews"] += 1
                if plan["action"] == "BUY":
                    record["buys"].append(_plan_shape(plan, panel, day) if plan["entry_price"] else {"day": day})
    for (payload,) in db.execute("SELECT payload FROM paper_events WHERE kind='llm_request' ORDER BY id DESC LIMIT 400"):
        request = json.loads(payload)
        if request["role"] == "risk_manager":
            account = request["payload"]["account"]
            user = out["users"].setdefault(account["userId"], {})
            user.setdefault("latest_account", {"captured": account["capturedAt"], "equity": account["equity"],
                                               "holdings": [(h["stockCode"], h["quantity"], h["avgPrice"])
                                                            for h in account["holdings"]]})
    for user, record in out["users"].items():
        if "days" in record:
            record["days"] = dict(sorted(record["days"].items()))
            record["held_actions"] = dict(record["held_actions"])
            record["buy_rate_pct"] = (round(len(record["buys"]) / record["new_reviews"] * 100, 2)
                                      if record["new_reviews"] else None)
    return out


def replicate_day(main: list[dict], replicate: list[dict]) -> dict:
    """The replicated day only: the last day of a replicate run against the main path's same day."""
    last = replicate[-1]
    base = next(row for row in main if row["day"] == last["day"])
    result = analysis.replicate_agreement([base], [last])
    result["day"] = last["day"]
    result["main_plans"] = [(p["stock_code"], p["action"], p.get("position_size_pct")) for p in base["plans"]]
    result["replicate_plans"] = [(p["stock_code"], p["action"], p.get("position_size_pct")) for p in last["plans"]]
    scores = []
    for code, roles in (last.get("specialists") or {}).items():
        for role, value in roles.items():
            other = (base.get("specialists") or {}).get(code, {}).get(role, {})
            if "score" in value and "score" in other:
                scores.append((role, code, other["score"], value["score"]))
    result["score_pairs"] = scores
    leaders = lambda row: [r["stock_code"] for r in row["leaders"] if r.get("leader_score") is not None][:5]
    result["top5_main"], result["top5_replicate"] = leaders(base), leaders(last)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit", default=".local/fitness/pitdata")
    parser.add_argument("--main", required=True)
    parser.add_argument("--replicates", nargs="*", default=[])
    parser.add_argument("--rules-small", default=".local/fitness/rules")
    parser.add_argument("--rules-large", default=".local/fitness/rules-1e9")
    parser.add_argument("--paper-audit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    panel = PricePanel.from_pit(Path(args.pit))
    features = Features(panel, entry_filters())
    main_dir = Path(args.main)
    decisions = analysis.read_decisions(main_dir / "decisions.jsonl")
    result = json.loads((main_dir / "result.json").read_text(encoding="utf-8"))
    start, end = "2026-07-01", "2026-10-16"
    small = {name: json.loads((Path(args.rules_small) / "clean" / f"{name}.json").read_text(encoding="utf-8"))
             for name in ("buy_and_hold", "plan_template_d1", "rule_m3_h20", "rule_m3_h20_stop10")}
    report: dict = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "window": [start, end]}
    report["llm"] = {"window": metrics.summarize(result, start, end),
                     "months": {m: metrics.summarize(result, a, b) for m, (a, b) in MONTHS.items()},
                     "vs_buy_and_hold_1e7": metrics.intervals(result, start, end, benchmark=small["buy_and_hold"]),
                     "vs_plan_template_1e7": metrics.intervals(result, start, end, benchmark=small["plan_template_d1"]),
                     "trades_ci": metrics.trade_bootstrap([c["return"] for c in result["closed"]]),
                     "closed": [{k: c[k] for k in ("code", "entry_day", "exit_day", "reason", "return")} for c in result["closed"]],
                     "open_positions": result["open_positions"], "events": Counter(e["event"] for e in result["events"]),
                     "final_equity": result["curve"][-1]["equity"]}
    report["small_account_arms"] = {name: {"window": metrics.summarize(r, start, end),
                                           "months": {m: metrics.summarize(r, a, b)["total_return_pct"]
                                                      for m, (a, b) in MONTHS.items()}}
                                    for name, r in small.items()}
    report["decisions"] = decision_stats(decisions, panel)
    report["score_ic"] = analysis.score_ic(decisions, panel, features, horizons=(1, 5, 10, 20))
    report["gate"] = analysis.gate_study(decisions, panel, horizons=(1, 5, 10))
    report["gate_difference"] = [analysis.gate_difference(decisions, panel, horizon) for horizon in (1, 5, 10)]
    report["score_ic_robust"] = analysis.ic_robust(decisions, panel, features, horizons=(1, 5, 10, 20))
    report["top5_spread"] = [analysis.top5_spread(decisions, panel, horizon) for horizon in (1, 5, 10)]
    report["stops_vs_volatility"] = analysis.stops_vs_volatility(decisions, panel)
    large_bh = json.loads((Path(args.rules_large) / "clean" / "buy_and_hold.json").read_text(encoding="utf-8"))
    report["llm"]["vs_buy_and_hold_1e9"] = metrics.intervals(result, start, end, benchmark=large_bh)
    report["kept_old_plans"] = [e for e in result["events"] if e["event"] == "plan_update_kept_old_stop"]
    nulls = analysis.null_picks(decisions, panel, features, start=start, end=end, cash=10_000_000.0, cost_bps=15.0,
                                draws=500)
    report["nulls"] = {"buy_days": nulls["buy_days"], "buys": nulls["buys"],
                       "random_total_return_pct": nulls["random_total_return_pct"],
                       "llm_percentile_among_random": nulls["llm_percentile_among_random"],
                       **{name: metrics.summarize(r, start, end) for name, r in nulls["results"].items()}}
    report["replicates"] = [replicate_day(decisions, analysis.read_decisions(Path(path) / "decisions.jsonl"))
                            for path in args.replicates]
    report["paper_agreement"] = analysis.paper_agreement(
        [row for row in decisions if row["day"] >= "2026-10-12"], Path(args.paper_audit))
    report["paper"] = paper_record(Path(args.paper_audit), panel)
    report["replay_days_on_paper_dates"] = [
        {"day": row["day"], "completed": row["completed"], "holdings": row["holdings"],
         "plans": [(p["stock_code"], p["action"], p.get("position_size_pct")) for p in row["plans"]]}
        for row in decisions if row["day"] >= "2026-10-12"]
    rules = {}
    for window in ("full", "clean"):
        rules[window] = json.loads((Path(args.rules_large) / window / "summary.json").read_text(encoding="utf-8"))
    report["rules_1e9"] = rules
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
