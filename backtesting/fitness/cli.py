"""`python -m backtesting fitness <command>`: build the replay data, run the arms, report.

Commands:
  build-data  write the point-in-time copy of the PAPER data (no network, no LLM)
  rules       run buy-and-hold, the rule leader, its stop variants and the technical baselines
  llm         replay the PAPER pipeline on decision days (subscription calls unless --dry)
  calls       list the calls a cached run made, with tokens and durations
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from backtesting.fitness import metrics
from backtesting.fitness.arms import (TECHNICAL_BASELINES, Features, buy_and_hold, entry_filters, plan_template,
                                      rule_leader, schedule, technical)
from backtesting.fitness.simulate import DecisionView, PricePanel, Simulator, plan_from_trading_plan

HOME = Path.home()
DEFAULT_LIVE_DATA = HOME / "Desktop/HQA_Project_main/.local/data"
DEFAULT_HISTORY_DATA = HOME / "Desktop/HQA_Project_main/.local/backtest-data"
FIRST_BAR_DAY = "2023-12-01"


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def rule_arms(features: Features) -> dict:
    """Name -> (decide factory, schedule kind). Factories are called once per run."""
    arms = {
        "buy_and_hold": (lambda: buy_and_hold(features), "daily"),
        "rule_m3_h20": (lambda: rule_leader(features, top_n=3, hold=20), "monthly"),
        "rule_w3_h5": (lambda: rule_leader(features, top_n=3, hold=5), "weekly"),
        "plan_template_d1": (lambda: plan_template(features), "daily"),
    }
    for stop in (0.05, 0.08, 0.10):
        arms[f"rule_m3_h20_stop{int(stop * 100)}"] = (
            lambda stop=stop: rule_leader(features, top_n=3, hold=20, stop_pct=stop), "monthly")
    arms["rule_w3_h5_stop8"] = (lambda: rule_leader(features, top_n=3, hold=5, stop_pct=0.08), "weekly")
    for baseline in TECHNICAL_BASELINES:
        arms[f"tech_{baseline}"] = (lambda baseline=baseline: technical(features, baseline), "weekly")
    return arms


def run_rules(args) -> None:
    panel = PricePanel.from_pit(Path(args.pit))
    features = Features(panel, entry_filters())
    simulator = Simulator(panel, cost_bps=args.cost_bps, initial_cash=args.cash)
    windows = {"full": (args.start, args.end)}
    if args.window:
        for item in args.window:
            name, start, end = item.split(":")
            windows[name] = (start, end)
    out = Path(args.out)
    summary = {}
    for window, (start, end) in windows.items():
        results = {}
        for name, (factory, kind) in rule_arms(features).items():
            results[name] = simulator.run(factory(), start=start, end=end,
                                          decision_days=schedule(panel, start, end, kind))
            _dump(out / window / f"{name}.json", results[name])
        bench = results["buy_and_hold"]
        rows = {}
        for name, result in results.items():
            row = {"window": metrics.summarize(result, start, end)}
            if window == "full":
                for period, (p_start, p_end) in metrics.PERIODS.items():
                    row[period] = metrics.summarize(result, max(p_start, start), min(p_end, end))
            row["ci"] = metrics.intervals(result, start, end, benchmark=None if name == "buy_and_hold" else bench)
            row["trades_ci"] = metrics.trade_bootstrap([c["return"] for c in result["closed"]])
            rows[name] = row
        summary[window] = rows
        _dump(out / window / "summary.json", rows)
    for window, rows in summary.items():
        print(f"== {window}")
        for name, row in rows.items():
            w = row["window"]
            print(f"{name:28} ret {w['total_return_pct']:8.2f}% mdd {w['mdd_pct']:7.2f}% sharpe {w['sharpe']} "
                  f"expo {w['exposure_pct']:5.1f}% trades {w['trades']:4d} hit {w['hit_rate_pct']} "
                  f"excess {row['ci'].get('excess_annualized_pp')} ci {row['ci'].get('excess_annualized_pp_ci')}")


class DecisionRelevance:
    """Declines chartist and quant calls whose answers no decision can use: a stock that is not
    held and has no dated news or DART evidence that day gets no analyst result, so it can never
    complete all three specialists or reach the RiskManager. The RiskManager's inputs, and so every
    trade, are the same as with every call made; only the per-stock score study loses rows."""

    def __init__(self, data):
        self.data, self.arm, self._day, self._known = data, None, None, {}

    def __call__(self, role: str, payload: dict) -> bool:
        if role not in {"chartist", "quant"}:
            return False
        view = self.arm.view
        code = payload["stock_code"]
        if code in view.positions:
            return False
        if self._day != view.day:
            self._day, self._known = view.day, {}
        if code not in self._known:
            from backtesting.fitness.replay import decision_time
            candidate = {"stock_code": code, "stock_name": payload["stock_name"],
                         "theme_keys": [self.arm.panel.themes[code]]}
            try:
                self._known[code] = bool(self.data.load_evidence(candidate, decision_time(view.day))["documents"])
            except ValueError:
                self._known[code] = False
        return not self._known[code]


class StopRun(RuntimeError):
    """The replay stopped at a day boundary; everything before it is cached and complete."""


# A heavy decision day in the pilot (16 completed stocks, RiskManager included); a day is started
# only when the run's limits leave this much room.
DAY_RESERVE = {"calls": 60, "tokens": 700_000, "minutes": 8}


class LLMArm:
    """The PAPER pipeline as a strategy: on each decision day the replayed cycle reviews the
    simulated account, and its accepted plans are executed by the shared simulator."""

    def __init__(self, runner, panel: PricePanel, log_path: Path, guard=None, reserve: dict | None = None):
        self.runner, self.panel, self.log_path, self.guard = runner, panel, log_path, guard
        self.reserve = reserve or DAY_RESERVE
        self.view: DecisionView | None = None
        runner.accounts.state = self.state

    def state(self, at):
        view = self.view
        holdings = []
        for code, position in sorted(view.positions.items()):
            price = view.opens.get(code) or float(self.panel.history(code, view.previous_day)["close"].iloc[-1])
            holdings.append({"stockCode": code, "stockName": self.panel.names[code], "quantity": position.quantity,
                             "sellableQuantity": position.quantity, "avgPrice": float(position.average),
                             "currentPrice": float(price), "evalAmount": float(position.quantity * price),
                             "pnlRate": round((price / position.average - 1) * 100, 2)})
        fields = {"orderableCash": float(max(0.0, view.cash)), "equity": float(view.equity_at_open),
                  "dailyPnlPct": (view.equity_at_open / view.previous_close_equity - 1) * 100,
                  "monitorSymbolCount": len(holdings)}
        return fields, holdings, dict(view.opens)

    def __call__(self, view: DecisionView):
        self.view = view
        if self.guard is not None:
            reason = self.guard.room_for_day(self.reserve["calls"], self.reserve["tokens"], self.reserve["minutes"])
            if reason:
                raise StopRun(f"stopped before {view.day}: {reason}")
            refused_before = self.guard.refused
        cycle = self.runner.run(view.day)
        if self.guard is not None and (self.guard.refused > refused_before or self.guard.stopped):
            # The live service records a refused call like any failure and carries on; a day that
            # lost calls is not a PAPER day, so it is discarded (its paid answers stay cached).
            raise StopRun(f"stopped during {view.day}: {self.guard.stopped or 'calls refused'}")
        account = cycle["accounts"][self.runner.target["userId"]]
        record = {"day": view.day, "status": account.get("status"), "reason": account.get("reason"),
                  "error": account.get("error"), "prefilter": cycle["prefilter_count"],
                  "selected": cycle["specialist_stock_count"], "completed": cycle["completed_stock_count"],
                  "leaders": cycle["global_ranked_leaders"], "errors": cycle["errors"],
                  "account_rows": account.get("global_ranked_leaders"), "plans": account.get("plans", []),
                  "rejected": account.get("rejected_plans", []), "omitted": account.get("omitted_candidates", []),
                  "reasoning": account.get("reasoning"), "holdings": sorted(view.positions),
                  "specialists": self.runner.specialists,
                  "equity_at_open": view.equity_at_open, "cash": view.cash}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        plans = []
        for plan in account.get("plans", []):
            converted = plan_from_trading_plan(plan)
            converted.meta.update(arm="llm", decided=view.day,
                                  leader_score=next((row["leader_score"] for row in account.get("global_ranked_leaders", [])
                                                     if row["stock_code"] == plan["stock_code"]), None))
            plans.append(converted)
        return plans


def run_llm(args) -> int:
    from backtesting.fitness.pit_data import ReplayAnalysisData, allow_sessions_before_first_bar
    from backtesting.fitness.replay import (CallCache, CallGuard, CallLog, CallRefused, DryModel, GuardedModel,
                                            ReplayRunner, UsageLimitStop, in_blocked_hours, paper_role_models,
                                            paper_role_settings)

    os.environ.setdefault("LLM_PROVIDER", "claude_plan")
    os.environ.setdefault("HQA_LLM_TPM", "2000000")
    os.environ.setdefault("HQA_LLM_RPM", "1000")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        os.environ.pop(name, None)
    if os.environ["LLM_PROVIDER"] != "claude_plan":
        raise SystemExit("the replay uses PAPER's provider: LLM_PROVIDER=claude_plan")
    if not args.dry and not args.allow_market_hours and in_blocked_hours(datetime.now(timezone.utc)):
        raise SystemExit("refusing to start: KRX session hours (08:30-15:45 KST on a trading day)")
    pit = Path(args.pit)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Nothing may default into the tracked data/ directory.
    os.environ["HQA_DATA_DIR"] = str(pit.resolve())
    os.environ.setdefault("HQA_LLM_BUDGET_PATH", str((out / "llm_budget.sqlite3").resolve()))
    allow_sessions_before_first_bar(FIRST_BAR_DAY)
    panel = PricePanel.from_pit(pit)
    data = ReplayAnalysisData(data_dir=str(pit), config_path="config/watchlist.yaml")
    log = CallLog()
    deadline = None
    if args.deadline:
        deadline = datetime.fromisoformat(args.deadline)
        if deadline.tzinfo is None:
            raise SystemExit("--deadline needs a timezone, e.g. 2026-10-20T03:30:00+09:00")
    guard = CallGuard(max_calls=args.max_calls, allow_market_hours=args.allow_market_hours, deadline=deadline,
                      token_budget=args.token_budget)
    if args.dry:
        settings, seen = paper_role_settings(), set()
        models = {role: DryModel(role, settings[role], log, seen) for role in settings}
    else:
        if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise SystemExit("CLAUDE_CODE_OAUTH_TOKEN is not set (load it the way run-ai.sh does)")
        cache = CallCache(Path(args.cache))
        skip = DecisionRelevance(data) if args.decision_calls_only else None
        settings, seen = paper_role_settings(), set()
        real = set(args.real_roles.split(",")) if args.real_roles else set(settings)
        models = {role: GuardedModel(model, role, cache, guard, log, salt=args.replicate, skip=skip)
                  if role in real else DryModel(role, settings[role], log, seen)
                  for role, model in paper_role_models().items()}
    runner = ReplayRunner(data=data, models=models, state=lambda at: None,
                          audit_path=None if args.dry else out / "replay_audit.sqlite3")
    days = args.days.split(",") if args.days else schedule(panel, args.start, args.end, args.schedule)
    arm = LLMArm(runner, panel, out / "decisions.jsonl", guard=None if args.dry else guard,
                 reserve={**DAY_RESERVE, "tokens": args.day_reserve_tokens})
    if not args.dry and args.decision_calls_only:
        skip.arm = arm
    (out / "decisions.jsonl").unlink(missing_ok=True)
    simulator = Simulator(panel, cost_bps=args.cost_bps, initial_cash=args.cash)
    status = "completed"
    try:
        result = simulator.run(arm, start=args.start, end=args.end, decision_days=days)
    except (StopRun, UsageLimitStop, CallRefused) as exc:
        status, result = f"stopped: {exc}", None
    finally:
        events = log.events
        kinds = {}
        for event in events:
            kinds.setdefault(event["role"], {}).setdefault(event["kind"], 0)
            kinds[event["role"]][event["kind"]] += 1
        _dump(out / "calls.json", {"status": status, "guard_calls": guard.calls, "guard_tokens": guard.tokens,
                                   "refused": guard.refused, "stopped": guard.stopped,
                                   "by_role": kinds, "events": events})
    if result is not None:
        _dump(out / "result.json", result)
    print(json.dumps({"status": status, "calls": kinds, "guard_calls": guard.calls, "guard_tokens": guard.tokens},
                     ensure_ascii=False))
    return 0 if result is not None else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backtesting fitness", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-data")
    build.add_argument("--out", default=".local/fitness/pitdata")
    build.add_argument("--live-data", default=str(DEFAULT_LIVE_DATA))
    build.add_argument("--history-data", default=str(DEFAULT_HISTORY_DATA))
    for name in ("rules", "llm"):
        command = sub.add_parser(name)
        command.add_argument("--pit", default=".local/fitness/pitdata")
        command.add_argument("--start", default="2024-08-01")
        command.add_argument("--end", default="2026-10-16")
        command.add_argument("--cost-bps", type=float, default=15.0)
        command.add_argument("--cash", type=float, default=10_000_000.0)
        command.add_argument("--out", required=True)
    sub.choices["rules"].add_argument("--window", action="append", help="name:YYYY-MM-DD:YYYY-MM-DD, repeatable")
    llm = sub.choices["llm"]
    llm.add_argument("--schedule", choices=("daily", "weekly", "monthly"), default="daily")
    llm.add_argument("--days", help="comma-separated decision days instead of a schedule")
    llm.add_argument("--max-calls", type=int, default=0, help="hard cap on paid calls in this run")
    llm.add_argument("--token-budget", type=int, help="stop before a day once paid tokens near this total")
    llm.add_argument("--day-reserve-tokens", type=int, default=DAY_RESERVE["tokens"],
                     help="start a day only with this many budget tokens left")
    llm.add_argument("--deadline", help="no new paid call after this time, e.g. 2026-10-20T03:30:00+09:00")
    llm.add_argument("--cache", default=".local/fitness/llm_cache.sqlite3")
    llm.add_argument("--dry", action="store_true", help="no LLM: uninformative answers, to count calls")
    llm.add_argument("--cache-only", action="store_true", help="replay cached answers only; never call")
    llm.add_argument("--decision-calls-only", action="store_true",
                     help="skip chartist/quant calls for stocks that cannot reach the RiskManager")
    llm.add_argument("--replicate", type=int, default=0, help="ask again for answered requests (nondeterminism)")
    llm.add_argument("--real-roles", help="comma-separated roles that call the model; the others answer dry")
    llm.add_argument("--allow-market-hours", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "build-data":
        from backtesting.fitness.pit_data import build_pit_data
        manifest = build_pit_data(out_dir=Path(args.out), live_data_dir=Path(args.live_data),
                                  history_data_dir=Path(args.history_data) if args.history_data else None)
        print(json.dumps({theme: {"bars": t["prices"]["bars"], "last": t["prices"]["last_trade_date"],
                                  "documents": t["documents"]} for theme, t in manifest["themes"].items()},
                         ensure_ascii=False))
        return 0
    if args.command == "rules":
        run_rules(args)
        return 0
    if args.command == "llm":
        if args.cache_only:
            args.max_calls = 0  # every answer must come from the cache; a missing one stops the run
        elif not args.dry and args.max_calls <= 0:
            raise SystemExit("set --max-calls for a paid run")
        return run_llm(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
