"""What the replayed decisions say about each layer of the PAPER pipeline.

- `score_ic`: does a specialist score (or the leader score) rank the day's stocks by their next
  returns better than PAPER's rule score on the same stocks? (daily Spearman IC)
- `gate_study`: do the stocks the RiskManager buys do better than the ones it holds back?
- `null_picks`: the LLM's own BUY days, sizes and plan shapes, with the stock chosen by the rule
  ranking or at random among the RiskManager's candidates, all executed without later review.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.fitness.arms import Features
from backtesting.fitness.simulate import CLOSE_SLOT, DecisionView, Group, Plan, PricePanel, Simulator


def read_decisions(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def forward_return(panel: PricePanel, code: str, day: str, sessions: int) -> float | None:
    """From the decision day's open to the close `sessions` sessions later (the day itself is 1)."""
    end = panel.shift(day, sessions - 1)
    start, last = panel.bar(code, day), panel.bar(code, end) if end else None
    if start is None or last is None or start["volume"] <= 0 or panel.basis_break(code, day, end):
        return None
    return float(last["close"]) / float(start["open"]) - 1


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 4 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


def score_ic(decisions: list[dict], panel: PricePanel, features: Features, horizons=(1, 5, 10, 20)) -> dict:
    """Mean daily Spearman IC of each score against forward returns, next to the rule score's IC
    on exactly the same stock-days."""
    out: dict[str, Any] = {}
    for horizon in horizons:
        for name in ("analyst", "quant", "chartist", "leader"):
            ics, rule_ics, n = [], [], 0
            for row in decisions:
                view = DecisionView(row["day"], panel.shift(row["day"], -1), 0, 0, 0, {}, {})
                ranked = {r["stock_code"]: r["price_score"] for r in features.ranked(
                    DecisionView(row["day"], view.previous_day, 0, 0, 0, {}, {c: 1.0 for c in panel.names}))}
                xs, rules, ys = [], [], []
                for code, roles in (row.get("specialists") or {}).items():
                    scores = {role: value["score"] for role, value in roles.items() if "score" in value}
                    if name == "leader":
                        if len(scores) < 3:
                            continue
                        score = sum(scores.values()) / 3
                    elif name in scores:
                        score = scores[name]
                    else:
                        continue
                    future = forward_return(panel, code, row["day"], horizon)
                    if future is None or code not in ranked:
                        continue
                    xs.append(score), rules.append(ranked[code]), ys.append(future)
                ic, rule_ic = _spearman(xs, ys), _spearman(rules, ys)
                if ic is not None and rule_ic is not None:
                    ics.append(ic), rule_ics.append(rule_ic)
                    n += len(xs)
            if ics:
                diff = np.array(ics) - np.array(rule_ics)
                out[f"{name}_h{horizon}"] = {
                    "days": len(ics), "stock_days": n, "mean_ic": round(float(np.mean(ics)), 3),
                    "ic_t": round(float(np.mean(ics) / (np.std(ics, ddof=1) / math.sqrt(len(ics)))), 2) if len(ics) > 2 and np.std(ics, ddof=1) > 0 else None,
                    "positive_days_pct": round(float(np.mean(np.array(ics) > 0)) * 100, 1),
                    "rule_mean_ic": round(float(np.mean(rule_ics)), 3),
                    "minus_rule_mean": round(float(diff.mean()), 3),
                    "minus_rule_t": round(float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))), 2) if len(diff) > 2 and diff.std(ddof=1) > 0 else None}
    return out


def gate_study(decisions: list[dict], panel: PricePanel, horizons=(1, 5, 10)) -> dict:
    """Forward returns of the new stocks the RiskManager reviewed, by its action."""
    rows = []
    for row in decisions:
        held = set(row.get("holdings") or [])
        for plan in row.get("plans") or []:
            if plan["stock_code"] in held:
                continue
            rows.append({"day": row["day"], "code": plan["stock_code"], "action": plan["action"],
                         **{f"r{h}": forward_return(panel, plan["stock_code"], row["day"], h) for h in horizons}})
        for plan in row.get("rejected") or []:
            if plan["stock_code"] not in held:
                rows.append({"day": row["day"], "code": plan["stock_code"], "action": f"rejected_{plan.get('action')}",
                             **{f"r{h}": forward_return(panel, plan["stock_code"], row["day"], h) for h in horizons}})
    frame = pd.DataFrame(rows)
    out: dict[str, Any] = {"reviews": len(frame)}
    if frame.empty:
        return out
    for action, group in frame.groupby("action"):
        out[action] = {"n": len(group), **{f"mean_r{h}_pct": round(float(group[f"r{h}"].dropna().mean()) * 100, 2)
                                           for h in horizons if group[f"r{h}"].notna().any()}}
    return out


def _relative(plan: dict, panel: PricePanel, day: str) -> dict:
    """A RiskManager BUY plan as distances from its entry price and sessions to its exit."""
    entry = float(plan["entry_price"])
    rel = {"stop": float(plan["stop_loss_price"]) / entry - 1, "target": float(plan["take_profit_price"]) / entry - 1,
           "size_pct": float(plan["position_size_pct"]), "reduces": []}
    for group in plan["condition_payload"]["reduce_conditions"]:
        levels = [p["value"] for p in group["all"] if p["field"] == "current_price" and p["operator"] in (">", ">=")]
        if levels:
            rel["reduces"].append((float(levels[0]) / entry - 1, float(group["reduce_fraction"])))
    exit_day = plan["planned_exit_at"][:10]
    sessions = sum(1 for d in panel.calendar if day <= d <= exit_day)
    rel["sessions"] = max(1, sessions)
    return rel


def _template_plan(code: str, rel: dict, meta: dict) -> Plan:
    plan = Plan(code=code, action="BUY", size_pct=rel["size_pct"], stop_pct=-rel["stop"],
                take_profit_pct=rel["target"], hold_sessions=rel["sessions"], meta=meta)
    plan.meta["reduces"] = rel["reduces"]
    return plan


class _RelativeReduces(Simulator):
    """Reduce levels stated relative to the fill."""

    def _absolute(self, plan, fill, day):
        out = super()._absolute(plan, fill, day)
        out.reduces = [Group(f"tp{i}", [("current_price", ">=", fill * (1 + level))], fraction)
                       for i, (level, fraction) in enumerate(plan.meta.get("reduces", []), 1)]
        return out


def null_picks(decisions: list[dict], panel: PricePanel, features: Features, *, start: str, end: str,
               cash: float, cost_bps: float, draws: int = 500, seed: int = 3) -> dict:
    """The LLM's BUY days, counts, sizes and plan shapes, executed mechanically (no later review),
    with the stock chosen (a) by the LLM, (b) by the rule ranking, (c) at random among the
    RiskManager's new candidates that day."""
    buys: dict[str, list[tuple[str, dict]]] = {}
    candidates: dict[str, list[str]] = {}
    for row in decisions:
        held = set(row.get("holdings") or [])
        new = [p for p in row.get("plans") or [] if p["stock_code"] not in held]
        candidates[row["day"]] = [p["stock_code"] for p in new]
        buys[row["day"]] = [(p["stock_code"], _relative(p, panel, row["day"])) for p in new if p["action"] == "BUY"]
    simulator = _RelativeReduces(panel, cost_bps=cost_bps, initial_cash=cash)
    days = sorted(day for day, plans in buys.items() if plans)

    def run(choose) -> dict:
        def decide(view: DecisionView) -> list[Plan]:
            plans = []
            for index, (code, rel) in enumerate(buys.get(view.day, [])):
                pick = choose(view, index, code)
                if pick is not None and pick not in view.positions and all(p.code != pick for p in plans):
                    plans.append(_template_plan(pick, rel, {"arm": "null", "llm_pick": code}))
            return plans
        return simulator.run(decide, start=start, end=end, decision_days=days)

    def ranked_pick(view, index, code):
        ranked = [r["stock_code"] for r in features.ranked(view) if r["stock_code"] not in view.positions]
        return ranked[index] if index < len(ranked) else None

    results = {"llm_mechanical": run(lambda view, index, code: code), "rule_pick": run(ranked_pick)}
    rng = np.random.default_rng(seed)
    totals = []
    for _ in range(draws):
        choices = {day: list(rng.permutation(candidates[day])) for day in days}

        def random_pick(view, index, code, choices=choices):
            pool = [c for c in choices[view.day] if c not in view.positions]
            return pool[index] if index < len(pool) else None
        result = run(random_pick)
        totals.append(result["curve"][-1]["equity"] / cash - 1)
    llm_total = results["llm_mechanical"]["curve"][-1]["equity"] / cash - 1
    return {"buy_days": len(days), "buys": sum(len(v) for v in buys.values()), "results": results,
            "random_total_return_pct": {"mean": round(float(np.mean(totals)) * 100, 2),
                                        "p05": round(float(np.percentile(totals, 5)) * 100, 2),
                                        "p95": round(float(np.percentile(totals, 95)) * 100, 2)},
            "llm_percentile_among_random": round(float(np.mean(np.array(totals) < llm_total)) * 100, 1)}


def paper_agreement(decisions: list[dict], paper_audit: Path) -> dict:
    """Replayed specialist scores against PAPER's own answers for the same stock, role and day
    (PAPER's first answer that day). Inputs differ a little (collection times, document
    generation), so this bounds the replay's fidelity together with the model's own variation."""
    import sqlite3
    db = sqlite3.connect(str(paper_audit))  # a copy of the PAPER audit, never the live file
    requests = {}
    for rid, recorded, payload in db.execute("SELECT id, recorded_at, payload FROM paper_events WHERE kind='llm_request'"):
        body = json.loads(payload)
        if body["role"] != "risk_manager":
            local = datetime.fromisoformat(recorded).astimezone(timezone(timedelta(hours=9)))
            requests[rid] = (local.date().isoformat(), body["role"], body["payload"]["stock_code"])
    paper: dict[tuple, float] = {}
    for rid, payload in db.execute("SELECT id, payload FROM paper_events WHERE kind='llm_response' ORDER BY id"):
        body = json.loads(payload)
        key = requests.get(body["request_id"])
        if key is not None and key not in paper:
            paper[key] = float(body["output"]["score"])
    pairs = {}
    for row in decisions:
        for code, roles in (row.get("specialists") or {}).items():
            for role, value in roles.items():
                key = (row["day"], role, code)
                if "score" in value and key in paper:
                    pairs.setdefault(role, []).append((value["score"], paper[key]))
    out = {}
    for role, values in pairs.items():
        replay, live = np.array([v[0] for v in values]), np.array([v[1] for v in values])
        out[role] = {"pairs": len(values), "mean_abs_diff": round(float(np.abs(replay - live).mean()), 2),
                     "spearman": _spearman(list(replay), list(live)),
                     "replay_mean": round(float(replay.mean()), 1), "paper_mean": round(float(live.mean()), 1)}
    return out


def replicate_agreement(first: list[dict], second: list[dict]) -> dict:
    """The same decision days asked twice: specialist score differences and RiskManager actions."""
    by_day = {row["day"]: row for row in first}
    pairs, actions = {}, []
    for row in second:
        base = by_day.get(row["day"])
        if base is None:
            continue
        for code, roles in (row.get("specialists") or {}).items():
            for role, value in roles.items():
                other = (base.get("specialists") or {}).get(code, {}).get(role, {})
                if "score" in value and "score" in other:
                    pairs.setdefault(role, []).append((value["score"], other["score"]))
        mine = {p["stock_code"]: p["action"] for p in row.get("plans") or []}
        theirs = {p["stock_code"]: p["action"] for p in base.get("plans") or []}
        for code in set(mine) & set(theirs):
            actions.append((row["day"], code, theirs[code], mine[code]))
    out = {role: {"pairs": len(v), "mean_abs_diff": round(float(np.mean([abs(a - b) for a, b in v])), 2),
                  "spearman": _spearman([a for a, _ in v], [b for _, b in v])} for role, v in pairs.items()}
    out["risk_manager_actions"] = {"pairs": len(actions), "same": sum(a == b for _, _, a, b in actions),
                                   "changed": [(d, c, a, b) for d, c, a, b in actions if a != b]}
    return out


def ic_series(decisions: list[dict], panel: PricePanel, features: Features, name: str, horizon: int) -> list[tuple]:
    """(day, score IC, rule IC) per decision day, as in `score_ic`."""
    out = []
    for row in decisions:
        previous = panel.shift(row["day"], -1)
        ranked = {r["stock_code"]: r["price_score"] for r in features.ranked(
            DecisionView(row["day"], previous, 0, 0, 0, {}, {c: 1.0 for c in panel.names}))}
        xs, rules, ys = [], [], []
        for code, roles in (row.get("specialists") or {}).items():
            scores = {role: value["score"] for role, value in roles.items() if "score" in value}
            if name == "leader":
                if len(scores) < 3:
                    continue
                score = sum(scores.values()) / 3
            elif name in scores:
                score = scores[name]
            else:
                continue
            future = forward_return(panel, code, row["day"], horizon)
            if future is None or code not in ranked:
                continue
            xs.append(score), rules.append(ranked[code]), ys.append(future)
        ic, rule_ic = _spearman(xs, ys), _spearman(rules, ys)
        if ic is not None and rule_ic is not None:
            out.append((row["day"], ic, rule_ic))
    return out


def newey_west_t(values: np.ndarray, lags: int) -> float | None:
    """Mean / HAC standard error (Bartlett weights), for overlapping-horizon daily series."""
    n = len(values)
    if n < 3:
        return None
    centered = values - values.mean()
    variance = float(centered @ centered) / n
    for lag in range(1, min(lags, n - 1) + 1):
        weight = 1 - lag / (lags + 1)
        variance += 2 * weight * float(centered[lag:] @ centered[:-lag]) / n
    return float(values.mean() / math.sqrt(variance / n)) if variance > 0 else None


def ic_robust(decisions: list[dict], panel: PricePanel, features: Features, horizons=(1, 5, 10, 20)) -> dict:
    """Daily ICs overlap when the horizon is longer than a day; report HAC t-statistics and the
    non-overlapping subsample (every `horizon`-th decision day)."""
    out = {}
    for horizon in horizons:
        for name in ("analyst", "quant", "chartist", "leader"):
            series = ic_series(decisions, panel, features, name, horizon)
            if len(series) < 3:
                continue
            ic = np.array([s[1] for s in series])
            diff = np.array([s[1] - s[2] for s in series])
            sparse = ic[::horizon]
            sparse_diff = diff[::horizon]
            out[f"{name}_h{horizon}"] = {
                "days": len(ic), "mean_ic": round(float(ic.mean()), 3),
                "hac_t": None if (t := newey_west_t(ic, horizon - 1)) is None else round(t, 2),
                "minus_rule": round(float(diff.mean()), 3),
                "minus_rule_hac_t": None if (t := newey_west_t(diff, horizon - 1)) is None else round(t, 2),
                "nonoverlap_days": len(sparse), "nonoverlap_mean_ic": round(float(sparse.mean()), 3),
                "nonoverlap_minus_rule": round(float(sparse_diff.mean()), 3)}
    return out


def top5_spread(decisions: list[dict], panel: PricePanel, horizon: int = 5) -> dict:
    """Mean forward return of each day's five highest leader scores (what the RiskManager is shown)
    minus the other completed stocks, next to the same split by PAPER's rule score."""
    leader_spreads, rule_spreads = [], []
    for row in decisions:
        rows = [(r["stock_code"], r["leader_score"], r.get("price_score")) for r in row["leaders"]
                if r.get("leader_score") is not None and r.get("price_score") is not None]
        rets = {code: forward_return(panel, code, row["day"], horizon) for code, _, _ in rows}
        rows = [r for r in rows if rets[r[0]] is not None]
        if len(rows) < 7:
            continue
        for key, bucket in ((1, leader_spreads), (2, rule_spreads)):
            ordered = sorted(rows, key=lambda r: -r[key])
            top = [rets[r[0]] for r in ordered[:5]]
            rest = [rets[r[0]] for r in ordered[5:]]
            bucket.append(float(np.mean(top) - np.mean(rest)))
    leader, rule = np.array(leader_spreads), np.array(rule_spreads)
    return {"days": len(leader), "horizon": horizon,
            "leader_top5_minus_rest_pct": round(float(leader.mean()) * 100, 2),
            "leader_hac_t": None if (t := newey_west_t(leader, horizon - 1)) is None else round(t, 2),
            "rule_top5_minus_rest_pct": round(float(rule.mean()) * 100, 2),
            "rule_hac_t": None if (t := newey_west_t(rule, horizon - 1)) is None else round(t, 2)}


def gate_difference(decisions: list[dict], panel: PricePanel, horizon: int = 5, samples: int = 5000,
                    seed: int = 5) -> dict:
    """BUY minus HOLD mean forward return, with a 95% interval from resampling both groups."""
    buys, holds = [], []
    for row in decisions:
        held = set(row.get("holdings") or [])
        for plan in row.get("plans") or []:
            if plan["stock_code"] in held:
                continue
            value = forward_return(panel, plan["stock_code"], row["day"], horizon)
            if value is not None:
                (buys if plan["action"] == "BUY" else holds).append(value)
    rng = np.random.default_rng(seed)
    b, h = np.array(buys), np.array(holds)
    draws = [rng.choice(b, len(b)).mean() - rng.choice(h, len(h)).mean() for _ in range(samples)]
    return {"horizon": horizon, "buys": len(b), "holds": len(h),
            "difference_pct": round(float(b.mean() - h.mean()) * 100, 2),
            "difference_pct_ci": (round(float(np.percentile(draws, 2.5)) * 100, 2),
                                  round(float(np.percentile(draws, 97.5)) * 100, 2))}


def stops_vs_volatility(decisions: list[dict], panel: PricePanel) -> list[dict]:
    """Each BUY's stop distance against the stock's daily range before the decision."""
    out = []
    for row in decisions:
        held = set(row.get("holdings") or [])
        previous = panel.shift(row["day"], -1)
        for plan in row.get("plans") or []:
            if plan["action"] != "BUY" or plan["stock_code"] in held:
                continue
            history = panel.history(plan["stock_code"], previous).tail(21)
            daily = history["close"].pct_change().dropna()
            ranges = ((history["high"] - history["low"]) / history["close"].shift(1)).dropna()
            entry = float(plan["entry_price"])
            out.append({"day": row["day"], "code": plan["stock_code"],
                        "stop_pct": round((float(plan["stop_loss_price"]) / entry - 1) * 100, 2),
                        "daily_vol_pct": round(float(daily.std()) * 100, 2),
                        "avg_range_pct": round(float(ranges.mean()) * 100, 2)})
    return out
