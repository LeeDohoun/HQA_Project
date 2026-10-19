"""Root admission dispatcher and interruption-safe activated BB003 worker."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtesting.experiments.bb003.deployment import DATA_ROOT, operational_lock, fw001_contract, verify_operation
from backtesting.experiments.bb003.storage import Store
from backtesting.experiments.bb003 import NotReady


def require_worker_unit(*, run=subprocess.check_output):
    from backtesting.experiments.bb003.admission import _fields
    fields = _fields(run(["systemctl", "show", "hqa-bb003-daily.service",
                         "--property=LoadState,NeedDaemonReload,MainPID"], text=True))
    if (fields.get("LoadState") != "loaded" or fields.get("NeedDaemonReload") != "no"
            or fields.get("MainPID") != str(os.getpid())):
        raise ValueError("BB003 worker requires its conflicting systemd unit; start hqa-bb003-check.service")


def dispatch(store, phase, *, run=subprocess.check_output, start=subprocess.check_call):
    from backtesting.experiments.bb003.admission import missed, morning
    from backtesting.experiments.bb003.deployment import deadline_priority, MANIFEST
    from backtesting.experiments.bb003.resources import host_limits, verify_memory
    try:
        if phase == "seal":
            morning(DATA_ROOT, store.root, clock=store.clock, run=run, verify=True)
        else:
            deadline_priority(source=DATA_ROOT, state=store.root, clock=store.clock, run=run)
        if not store.get("activation", "activation"):
            raise NotReady("BB003_not_activated")
        verify_memory(store.path("host_memory_validation.json").read_bytes(),
                      json.loads((ROOT / MANIFEST).read_bytes()), host_limits(run=run))
        # Check once more after the memory queries. The dispatcher itself has
        # no Conflicts; only an admitted transaction may start the worker.
        if phase == "seal":
            morning(DATA_ROOT, store.root, clock=store.clock, run=run, verify=True)
        else:
            deadline_priority(source=DATA_ROOT, state=store.root, clock=store.clock, run=run)
        start(["systemctl", "--no-block", "--job-mode=fail", "start", "hqa-bb003-daily.service"])
        return {"status": "worker_dispatched"}
    except ValueError as exc:
        if phase == "seal":
            missed(store.root, str(exc), clock=store.clock)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", nargs="?", choices=("worker", "trigger"), default="worker")
    args = parser.parse_args(argv)
    store = Store(DATA_ROOT)
    phase = "seal" if store.clock().hour == 8 else "prepare"
    try:
        if args.stage == "trigger":
            print(json.dumps(dispatch(store, phase)))
            return 0
        with operational_lock(phase=phase, source=DATA_ROOT, state=store.root) as guard:
            store.morning_guard = guard
            store.priority_guard = guard
            require_worker_unit()
            verify_operation(store)
            fw001_contract()
            from backtesting.experiments.bb003.recorder import activation_status, capture_prices, post_entry, prepare_day, seal_day
            from backtesting.experiments.bb003.policy import latest
            if activation_status(store) != "active":
                raise ValueError("BB003 not_started or not activated")
            with store.lock():
                policy = latest(store)
                if phase == "seal":
                    result = seal_day(store, DATA_ROOT, policy)
                else:
                    from backtesting.experiments.bb003.fetch import HttpClient
                    result = prepare_day(store, DATA_ROOT, policy, HttpClient(os.environ.get("DART_API_KEY")))
                    result["post_entry"] = post_entry(store, DATA_ROOT, policy)
                    result["capture"] = capture_prices(store, DATA_ROOT, policy)
                print(json.dumps(result, allow_nan=False))
            return 0
    except BlockingIOError:
        print(json.dumps({"status": "shared_heavy_lock_busy"}))
        return 0
    except NotReady as exc:
        print(json.dumps({"status": "data_not_ready", "reason": str(exc)}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
