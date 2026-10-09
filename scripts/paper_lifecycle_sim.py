"""Drive the real SignalMonitor through price scenarios against the real Java order lifecycle.

PaperLifecycleSimulationTest (backend) serves the monitor's internal API from the real
PaperTradeLifecycle and PaperTradeStore over in-memory repositories and a simulated KIS paper
broker, and runs this script with HQA_SIM_BASE_URL set; run it from backend/ with
``HQA_SIM_PYTHON=$PWD/../venv/bin/python mvn -q -o test -Dtest=PaperLifecycleSimulationTest``.

Each poll is 20 simulated seconds. Prices are set per simulated second, and a limit order fills
once the price reaches it. Every scenario runs twice, with the backend's 20-second schedulers
aligned with the monitor's polls and 10 seconds apart. Names of scenarios given as arguments run
alone. Exits 1 when a check fails.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from src.runner.signal_monitor import BackendSignalClient, SignalMonitor  # noqa: E402

BASE = os.environ.get("HQA_SIM_BASE_URL", "").rstrip("/")


def call(path, body=None):
    response = requests.post(BASE + path, json=body or {}, timeout=60)
    response.raise_for_status()
    return response.json()


PHASE = [0]


class Run:
    def __init__(self, name, **reset):
        self.name = f"{name}@{PHASE[0]}"
        reset.setdefault("pollerPhase", PHASE[0])
        self.state = call("/sim/reset", reset)
        self.start = datetime.fromisoformat(self.state["now"])
        self.client = BackendSignalClient(base_url=BASE, internal_token="sim", timeout=60)
        self.monitor = SignalMonitor(self.client, price_provider=self.snapshot, clock=self.now,
                                     session=lambda at: "open")
        self.polls = []

    def now(self):
        return datetime.fromisoformat(self.state["now"])

    def snapshot(self, signal):
        state = self.state
        snap = {"current_price": float(state["price"]), "holding_quantity": state["held"],
                "snapshot_at": state["now"]}
        if state["held"]:
            snap["pnl_rate"] = (state["price"] / state["avg"] - 1) * 100
        return snap

    def plan(self, **body):
        return call("/sim/plan", body)

    def run(self, path, until, actions=None):
        actions = dict(actions or {})
        t = self.state["t"]
        while t < until:
            call("/sim/price", {"price": path(t)})
            if t in actions:
                actions.pop(t)(self)
            self.state = call("/sim/state")
            self.monitor.poll_once()
            report = self.monitor.last_report
            self.polls.append({"t": t, "price": self.state["price"], "held": self.state["held"],
                               "sent": report["triggered"] + report["deduplicated"], "quiet": report["quiet"],
                               "rejections": [f"{r['trigger_type']}:{r['group_id']}:{r['reason']}" for r in report["rejections"]],
                               "errors": [e["error"][:90] for e in report["errors"]],
                               "settled": [f"{s['trigger_type']}:{s['group_id']}:{s['reason']}" for s in report["settled"]]})
            self.state = call("/sim/advance", {"prices": [path(t + i) for i in range(1, 21)]})
            t = self.state["t"]
        self.final = call("/sim/state")
        return self.final

    # --- facts about the run -------------------------------------------------------------
    def events(self, kind):
        return [e for e in self.final["events"] if e["kind"] == kind]

    def orders_for(self, group):
        keys = {e["order"]: e["key"] for e in self.final["executions"] if e["order"]}
        return [o for o in self.final["orders"] if group in (keys.get(o["odno"]) or "")]

    def sold(self):
        return sum(e["qty"] for e in self.events("fill") if e["side"] == "SELL")

    def bought(self):
        return sum(e["qty"] for e in self.events("fill") if e["side"] == "BUY")

    def first_submitted(self, orders):
        """Seconds from the start to the first of these orders, or None."""
        return min(((datetime.fromisoformat(o["submittedAt"]) - self.start).total_seconds() for o in orders), default=None)


RESULTS = []


def check(run, label, ok, detail=""):
    RESULTS.append((run.name, label, bool(ok), detail))


def common(run, initial_held):
    check(run, "no server errors", not run.events("server_error"), run.events("server_error")[:2])
    check(run, "no broker refusals", not run.events("broker_refused"), run.events("broker_refused")[:2])
    check(run, "never sold more than held", run.sold() <= initial_held + run.bought(),
          f"sold={run.sold()} held0={initial_held} bought={run.bought()}")


def timeline(run):
    lines = []
    for e in run.final["events"]:
        if e["kind"] in {"order", "fill", "cancel", "trigger", "plan", "broker_refused", "cancel_refused", "server_error",
                         "rate_limited"}:
            extra = {k: v for k, v in e.items() if k not in {"t", "kind"} and v is not None}
            lines.append(f"  t={e['t']:>4} {e['kind']:<14} {json.dumps(extra, ensure_ascii=False)}")
    return "\n".join(lines)


def stop_behind_working_reduction():
    run = Run("stop_behind_working_reduction", price=106, held=10, avg=100)
    run.plan(action="HOLD", stop=90, reduce=[{"id": "trim", "fraction": 0.5, "pnl": 5}])
    # The reduction's limit order at 106 is left behind at 104, then the price falls through the stop.
    run.run(lambda t: 106 if t == 0 else 104 if t < 40 else 88, until=200)
    common(run, 10)
    trims, stops = run.orders_for(":REDUCE:trim:"), run.orders_for(":EXIT:stop:")
    check(run, "one reduction order, cancelled unfilled", len(trims) == 1 and trims[0]["filled"] == 0, trims)
    first = run.first_submitted(stops)
    check(run, "stop order within 40 s of the crossing at t=40", first is not None and first <= 80, f"t={first}")
    check(run, "position closed", run.final["held"] == 0, run.final["held"])
    return run


def planned_exit_during_working_reduction():
    run = Run("planned_exit_during_working_reduction", price=106, held=10, avg=100)
    run.plan(action="HOLD", stop=80, reduce=[{"id": "trim", "fraction": 0.5, "pnl": 5}],
             plannedExitSeconds=60, entrySeconds=30)

    def path(t):  # each poll's price is left behind one tick later, until the market steadies at t=400
        if t >= 400:
            return 106 - 400 // 20
        return 106 - t // 20 - (1 if t % 20 else 0)
    run.run(path, until=520)
    common(run, 10)
    trims = run.orders_for(":REDUCE:trim:")
    check(run, "the reduction is not re-placed while the planned exit is due", len(trims) == 1, trims)
    check(run, "position closed", run.final["held"] == 0, run.final["held"])
    exits = run.orders_for(":EXIT:planned-exit:")
    check(run, "planned exit re-quoted at most every 3 minutes", len(exits) >= 2, [(o["submittedAt"][11:19], o["limit"]) for o in exits])
    return run


def take_profit_tiers():
    run = Run("take_profit_tiers", price=100, held=10, avg=100)
    run.plan(action="HOLD", stop=90, reduce=[{"id": "tp1", "fraction": 0.5, "pnl": 5},
                                             {"id": "tp2", "fraction": 0.5, "pnl": 10}])
    run.run(lambda t: 100 if t < 40 else 106 if t < 100 else 111, until=260)
    common(run, 10)
    tp1, tp2 = run.orders_for(":REDUCE:tp1:"), run.orders_for(":REDUCE:tp2:")
    check(run, "tp1 sold 5 once", [o["filled"] for o in tp1] == [5], tp1)
    check(run, "tp2 sold 2 once", [o["filled"] for o in tp2] == [2], tp2)
    check(run, "3 shares left", run.final["held"] == 3, run.final["held"])
    return run


def entry_then_stop():
    run = Run("entry_then_stop", price=99, held=0, cash=1_000_000)
    run.plan(action="BUY", entry=100, stop=95, signalPrice=100, entrySeconds=600)
    run.run(lambda t: 99 if t < 40 else 101 if t < 120 else 94, until=240)
    common(run, 0)
    buys, stops = run.orders_for(":ENTRY:entry:"), run.orders_for(":EXIT:stop:")
    check(run, "one entry order filled", len(buys) == 1 and buys[0]["filled"] == buys[0]["qty"] > 0, buys)
    check(run, "stop sold the whole entry", sum(o["filled"] for o in stops) == run.bought() and run.final["held"] == 0,
          (stops, run.final["held"]))
    check(run, "position within the 10% target", run.bought() * 101 <= 100_000, run.bought())
    return run


def unfilled_reduction_retries():
    run = Run("unfilled_reduction_retries", price=106, held=10, avg=100)
    run.plan(action="HOLD", stop=90, reduce=[{"id": "trim", "fraction": 0.5, "pnl": 5}])
    run.run(lambda t: 106 if t == 0 else 105, until=260)
    common(run, 10)
    trims = run.orders_for(":REDUCE:trim:")
    check(run, "reduction retried after the unfilled expiry and sold once",
          len(trims) == 2 and [o["filled"] for o in trims] == [0, 5], trims)
    check(run, "5 shares left", run.final["held"] == 5, run.final["held"])
    return run


def reissued_version_does_not_repeat_reduction():
    run = Run("reissued_version_does_not_repeat_reduction", price=106, held=10, avg=100)
    run.plan(action="HOLD", stop=90, reduce=[{"id": "trim", "fraction": 0.5, "pnl": 5}], key="hold")
    reissue = {60: lambda r: r.plan(action="HOLD", stop=90, reduce=[{"id": "trim", "fraction": 0.5, "pnl": 5}],
                                    key="hold", version=2),
               120: lambda r: r.plan(action="HOLD", stop=90, reduce=[{"id": "trim", "fraction": 0.25, "pnl": 5}],
                                     key="hold", version=3)}
    run.run(lambda t: 106, until=240, actions=reissue)
    common(run, 10)
    trims = run.orders_for(":REDUCE:trim:")
    check(run, "same content runs once across versions, a new tier runs once",
          [o["filled"] for o in trims] == [5, 1], [(o["qty"], o["filled"]) for o in trims])
    return run


def stop_flicker_with_planned_exit_due():
    run = Run("stop_flicker_with_planned_exit_due", price=100, held=10, avg=100)
    run.plan(action="HOLD", stop=90, plannedExitSeconds=60, entrySeconds=30)

    def path(t):  # around the stop while the planned exit is due; orders at poll prices are left behind
        if t >= 300:
            return 91
        level = 89 if (t // 20) % 2 else 91
        return level if t % 20 == 0 else level - 2
    run.run(path, until=420)
    common(run, 10)
    check(run, "position closed", run.final["held"] == 0, run.final["held"])
    sells = [o for o in run.final["orders"] if o["side"] == "SELL"]
    check(run, "at most one sell working at a time",
          all(sum(1 for o in sells if o["submittedAt"] <= s["submittedAt"] and o["filled"] + o["cancelled"] < o["qty"]) <= 1
              for s in sells), sells)
    return run


def entry_partial_fill_then_stop():
    run = Run("entry_partial_fill_then_stop", price=99, held=0, cash=1_000_000, partial=300)
    run.plan(action="BUY", entry=100, stop=95, signalPrice=100, entrySeconds=600)
    # 300 of the entry fill at 101, the price moves above the limit, then falls through the stop.
    run.run(lambda t: 99 if t < 40 else 101 if t <= 41 else 103 if t < 100 else 94, until=220)
    common(run, 0)
    buys, stops = run.orders_for(":ENTRY:entry:"), run.orders_for(":EXIT:stop:")
    check(run, "entry remainder cancelled by the stop",
          len(buys) == 1 and buys[0]["cancelled"] > 0 and buys[0]["filled"] + buys[0]["cancelled"] == buys[0]["qty"], buys)
    check(run, "stop sold everything bought", sum(o["filled"] for o in stops) == run.bought() > 0 and run.final["held"] == 0,
          (stops, run.bought(), run.final["held"]))
    return run


def rate_limited_stop():
    run = Run("rate_limited_stop", price=100, held=10, avg=100)
    run.plan(action="HOLD", stop=90)
    run.run(lambda t: 100 if t < 40 else 88, until=140,
            actions={40: lambda r: call("/sim/control", {"rateLimited": 1})})
    common(run, 10)
    check(run, "one rate-limited attempt", len(run.events("rate_limited")) == 1, run.events("rate_limited"))
    stops = run.orders_for(":EXIT:stop:")
    check(run, "stop sent again and filled", sum(o["filled"] for o in stops) == 10 and run.final["held"] == 0,
          (stops, run.final["held"]))
    return run


def entry_fill_recorded_after_the_stop_crossed():
    run = Run("entry_fill_recorded_after_the_stop_crossed", price=99, held=0, cash=1_000_000, pollerPhase=1)
    run.plan(action="BUY", entry=100, stop=95, signalPrice=100, entrySeconds=600)
    # The entry fills at t=55 and the stop is crossed at t=56, before the backend records the fill.
    run.run(lambda t: 99 if t < 40 else 101 if t == 40 else 102 if t < 55 else 101 if t == 55 else 94, until=160)
    common(run, 0)
    stops = run.orders_for(":EXIT:stop:")
    check(run, "stop sold the whole entry", sum(o["filled"] for o in stops) == run.bought() > 0 and run.final["held"] == 0,
          (stops, run.bought(), run.final["held"]))
    first = run.first_submitted(stops)
    check(run, "stop order within 30 s of the crossing at t=56", first is not None and first <= 86, f"t={first}")
    return run


def entry_invalidated_before_it_triggers():
    run = Run("entry_invalidated_before_it_triggers", price=99, held=0, cash=1_000_000)
    run.plan(action="BUY", entry=100, stop=95, invalidation=97, signalPrice=100, entrySeconds=600)
    run.run(lambda t: 99 if t < 40 else 96, until=120)
    common(run, 0)
    check(run, "no orders", not run.final["orders"], run.final["orders"])
    check(run, "plan expired", [s["status"] for s in run.final["signals"]] == ["EXPIRED"], run.final["signals"])
    return run


def entry_expires_untriggered():
    run = Run("entry_expires_untriggered", price=99, held=0, cash=1_000_000)
    run.plan(action="BUY", entry=100, stop=95, signalPrice=100, entrySeconds=120)
    run.run(lambda t: 99, until=200)
    common(run, 0)
    check(run, "no orders", not run.final["orders"], run.final["orders"])
    check(run, "plan expired", [s["status"] for s in run.final["signals"]] == ["EXPIRED"], run.final["signals"])
    return run


def working_entry_cancelled_at_expiry():
    run = Run("working_entry_cancelled_at_expiry", price=99, held=0, cash=1_000_000)
    run.plan(action="BUY", entry=100, stop=95, signalPrice=100, entrySeconds=100)
    run.run(lambda t: 99 if t < 40 else 101 if t == 40 else 103, until=200)
    common(run, 0)
    buys = run.orders_for(":ENTRY:entry:")
    check(run, "the unfilled entry was cancelled", len(buys) == 1 and buys[0]["cancelled"] == buys[0]["qty"], buys)
    check(run, "plan expired, nothing held", [s["status"] for s in run.final["signals"]] == ["EXPIRED"]
          and run.final["held"] == 0, (run.final["signals"], run.final["held"]))
    return run


SCENARIOS = [stop_behind_working_reduction, planned_exit_during_working_reduction, take_profit_tiers,
             entry_then_stop, unfilled_reduction_retries, reissued_version_does_not_repeat_reduction,
             stop_flicker_with_planned_exit_due, entry_partial_fill_then_stop, rate_limited_stop,
             entry_fill_recorded_after_the_stop_crossed, entry_invalidated_before_it_triggers,
             entry_expires_untriggered, working_entry_cancelled_at_expiry]


def main():
    if not BASE:
        sys.exit("HQA_SIM_BASE_URL is not set; run this through PaperLifecycleSimulationTest (see the module docstring)")
    logging.disable(logging.WARNING)   # the monitor's per-refusal warnings; the report lists refusals
    only = set(sys.argv[1:])
    for PHASE[0], scenario in [(phase, s) for phase in (0, 10) for s in SCENARIOS]:
        if only and scenario.__name__ not in only:
            continue
        if scenario is entry_fill_recorded_after_the_stop_crossed and PHASE[0]:
            continue   # it sets its own scheduler phase
        try:
            run = scenario()
        except Exception as exc:  # a broken scenario is a failure, not a crash of the whole run
            state = call("/sim/state")
            RESULTS.append((scenario.__name__, "ran", False, f"{type(exc).__name__}: {exc} "
                            + json.dumps([e for e in state["events"] if e["kind"] == "server_error"], ensure_ascii=False)))
            continue
        print(f"\n=== {run.name}")
        print(timeline(run))
        errors = Counter(e for p in run.polls for e in p["errors"])
        rejections = Counter(r for p in run.polls for r in p["rejections"])
        print(f"  monitor rejections: {dict(rejections)}")
        print(f"  monitor errors: {dict(errors)}")
    print("\n=== checks")
    failed = 0
    for name, label, ok, detail in RESULTS:
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {label}" + ("" if ok else f"  -> {detail}"))
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
