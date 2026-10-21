"""One request/second across processes, with pending event > matched > baseline."""
import os
import time
import uuid

from . import Refused, aware, now
from .storage import load, locked, replace_private

INTERVAL_SECONDS = 1.0  # Shakedown must confirm the documented PAPER ceiling is strictly higher.
PRIORITY = {"event": 0, "matched": 1, "baseline": 2}


class RateUnavailable(Refused):
    pass


class RateManager:
    def __init__(self, root, *, clock=now, monotonic=time.monotonic, sleeper=time.sleep):
        from pathlib import Path
        self.root, self.clock, self.monotonic, self.sleep = Path(root), clock, monotonic, sleeper

    def acquire(self, priority, deadline):
        if priority not in PRIORITY:
            raise Refused("invalid XO001 request priority")
        cutoff = aware(deadline).timestamp()
        identity = uuid.uuid4().hex
        registered = False
        while True:
            current = aware(self.clock()).timestamp()
            with locked(self.root / "rate.lock"):
                path = self.root / "rate.json"
                state = load(path) if path.exists() else {"last": None, "queue": []}
                # Dead/expired waiters cannot permanently block the queue.
                queue = [q for q in state["queue"] if q["deadline"] > current and _alive(q["pid"])]
                if not registered and current < cutoff:
                    queue.append({"id": identity, "priority": PRIORITY[priority],
                                  "deadline": cutoff, "at": current, "pid": os.getpid()})
                    registered = True
                queue.sort(key=lambda q: (q["priority"], q["at"], q["id"]))
                last = state["last"]
                wait = max(0., INTERVAL_SECONDS - (current - last)) if last is not None else 0.
                # A backwards clock stalls conservatively; it never creates extra budget.
                allowed = current < cutoff and queue and queue[0]["id"] == identity and wait == 0
                if allowed:
                    queue = [q for q in queue if q["id"] != identity]
                    last = current
                if current >= cutoff:
                    queue = [q for q in queue if q["id"] != identity]
                replace_private(path, {"last": last, "queue": queue,
                                       "interval_seconds": INTERVAL_SECONDS})
            if allowed:
                return
            if current >= cutoff:
                raise RateUnavailable("unobserved:request_budget_or_deadline")
            self.sleep(min(.2, cutoff - current))


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
