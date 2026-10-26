"""Unattended replay nights: run a plan's budgeted segments, resume from the cache after any restart.

A plan (JSON) lists segments, each a window [start, deadline) with a token budget, and jobs, each a
`fitness llm` run. The runner works out where it is from durable state only, so it can be killed
and restarted at any moment without spending anything twice:

- a segment's spending is the paid answers the cache recorded inside its window, so a restarted
  runner gives the next replay only what is left of that segment's budget;
- a replay replays every finished day from the cache without calls and continues from there;
- a job is done when one of its runs completed; a usage-limit error blocks every later call until
  someone clears STATUS.json.

No segment may reach into 03:30-15:45 KST (PAPER's hours, and windows that would still be open at
09:00); the replay's own guard refuses calls there too. STATUS.json carries a heartbeat.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, time as clock, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
NO_CALLS_FROM, NO_CALLS_UNTIL = clock(3, 30), clock(15, 45)
HEARTBEAT_SECONDS = 30
BLOCKED = 10  # exit code: a usage limit was hit; the supervisor stops
MAX_FAILURES_PER_SEGMENT = 3


def _at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"plan times need a timezone: {value}")
    return parsed


def in_no_call_hours(at: datetime) -> bool:
    local = at.astimezone(KST).time()
    return NO_CALLS_FROM <= local < NO_CALLS_UNTIL


def load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    for segment in plan["segments"]:
        start, deadline = _at(segment["start"]), _at(segment["deadline"])
        if deadline <= start:
            raise ValueError(f"segment {segment['id']} ends before it starts")
        probe = start
        while probe < deadline:  # a segment may not touch 03:30-15:45 KST
            if in_no_call_hours(probe):
                raise ValueError(f"segment {segment['id']} reaches into 03:30-15:45 KST")
            probe += timedelta(minutes=5)
        if in_no_call_hours(deadline - timedelta(seconds=1)):
            raise ValueError(f"segment {segment['id']} reaches into 03:30-15:45 KST")
    if len({s["id"] for s in plan["segments"]}) != len(plan["segments"]):
        raise ValueError("segment ids must be unique")
    return plan


def spent(cache: Path, start: datetime, end: datetime) -> tuple[int, int]:
    """Paid answers the cache recorded inside a window (imported or copied answers carry no usage)."""
    if not cache.exists():
        return 0, 0
    db = sqlite3.connect(str(cache), timeout=30)
    try:
        row = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(json_extract(meta,'$.usage.input_tokens')"
            " + json_extract(meta,'$.usage.output_tokens')), 0) FROM calls"
            " WHERE created_at >= ? AND created_at < ? AND json_extract(meta,'$.usage.input_tokens') IS NOT NULL",
            (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat())).fetchone()
    finally:
        db.close()
    return int(row[0]), int(row[1])


class Status:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.data.setdefault("jobs", {})
        self.data.setdefault("segments", {})

    def save(self, **fields) -> None:
        self.data.update(fields, heartbeat=datetime.now(KST).isoformat(timespec="seconds"), runner_pid=os.getpid())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def _progress(out: Path) -> tuple[int, str | None]:
    path = out / "decisions.jsonl"
    if not path.exists():
        return 0, None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return len(lines), (json.loads(lines[-1])["day"] if lines else None)


def _replay_running() -> bool:
    found = subprocess.run(["pgrep", "-f", "backtesting fitness llm"], capture_output=True, text=True)
    return bool(found.stdout.strip())


def run(plan_path: Path, status_path: Path, root: Path, python: str) -> int:
    plan = load_plan(plan_path)
    cache = root / plan["cache"]
    status = Status(status_path)
    failures: dict[str, int] = {}
    while True:
        plan = load_plan(plan_path)  # edits to the plan take effect without a restart
        if status.data.get("blocked"):
            status.save(state="blocked")
            return BLOCKED
        pending = [job for job in plan["jobs"] if not status.data["jobs"].get(job["name"], {}).get("done")]
        if not pending:
            status.save(state="done", next_start=None)
            return 0
        now = datetime.now(KST)
        week_calls, week_tokens = spent(cache, _at(plan["week_start"]), now + timedelta(seconds=1))
        status.data["week"] = {"start": plan["week_start"], "paid_calls": week_calls, "tokens": week_tokens,
                               "cap": plan["week_budget_tokens"]}
        if week_tokens + plan["day_reserve_tokens"] > plan["week_budget_tokens"]:
            status.save(state="weekly token cap reached", next_start=None)
            return 0
        open_segments = [s for s in plan["segments"]
                         if _at(s["start"]) <= now < _at(s["deadline"])
                         and not status.data["segments"].get(s["id"], {}).get("exhausted")]
        if not open_segments:
            later = sorted((s for s in plan["segments"] if _at(s["start"]) > now
                            and not status.data["segments"].get(s["id"], {}).get("exhausted")),
                           key=lambda s: s["start"])
            if not later:
                status.save(state="idle: no segment left in the plan", next_start=None, segment=None)
                return 0
            status.save(state="waiting", next_start=later[0]["start"], segment=None)
            time.sleep(max(1.0, min(HEARTBEAT_SECONDS, (_at(later[0]["start"]) - now).total_seconds())))
            continue
        segment = open_segments[0]
        record = status.data["segments"].setdefault(segment["id"], {})
        start, deadline = _at(segment["start"]), _at(segment["deadline"])
        calls, tokens = spent(cache, start, deadline)
        record.update(paid_calls=calls, tokens=tokens, budget=segment["budget_tokens"])
        remaining = segment["budget_tokens"] - tokens
        if remaining < plan["day_reserve_tokens"]:
            record["exhausted"] = "budget"
            status.save(state="segment budget spent")
            continue
        if _replay_running():  # e.g. a replay left behind by a killed runner; never run two at once
            status.save(state="waiting for a running replay to finish", segment=segment["id"])
            time.sleep(HEARTBEAT_SECONDS)
            continue
        job = pending[0]
        stamp = datetime.now(KST).strftime("%m%d-%H%M%S")
        out = root / plan["results"] / f"{job['name']}-{segment['id']}-{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        command = [python, "-m", "backtesting", "fitness", "llm", *job["args"], "--max-calls", "5000",
                   "--token-budget", str(remaining), "--day-reserve-tokens", str(plan["day_reserve_tokens"]),
                   "--deadline", deadline.isoformat(), "--out", str(out)]
        with (out.parent / f"{out.name}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            while process.poll() is None:
                done, last = _progress(out)
                calls, tokens = spent(cache, start, deadline)
                record.update(paid_calls=calls, tokens=tokens)
                job_record = status.data["jobs"].setdefault(job["name"], {})
                if job["name"] == plan.get("main_job"):
                    status.data["days_done"] = max(status.data.get("days_done", 0), done)
                    status.data["last_replayed_day"] = max(filter(None, [status.data.get("last_replayed_day"), last]),
                                                           default=None)
                job_record.update(last_out=str(out))
                status.save(state="running", night=segment.get("night"), segment=segment["id"], job=job["name"])
                time.sleep(HEARTBEAT_SECONDS)
        summary = {}
        if (out / "calls.json").exists():
            summary = json.loads((out / "calls.json").read_text(encoding="utf-8"))
        outcome = str(summary.get("status") or f"exit {process.returncode} without calls.json")
        calls, tokens = spent(cache, start, deadline)
        record.update(paid_calls=calls, tokens=tokens, last_outcome=outcome[:300])
        done, last = _progress(out)
        if job["name"] == plan.get("main_job"):
            status.data["days_done"] = max(status.data.get("days_done", 0), done)
        if "usage limit" in outcome or "usage limit" in str(summary.get("stopped") or ""):
            status.data["blocked"] = f"usage limit at {datetime.now(KST).isoformat(timespec='seconds')}: {outcome[:200]}"
            status.save(state="blocked", last_error=status.data["blocked"])
            return BLOCKED
        if process.returncode == 0 and outcome == "completed":
            status.data["jobs"][job["name"]] = {"done": True, "out": str(out), "finished": datetime.now(KST).isoformat()}
            status.save(state="job finished", last_replayed_day=last if job["name"] == plan.get("main_job")
                        else status.data.get("last_replayed_day"))
            continue
        if any(reason in outcome for reason in ("token budget", "call cap", "deadline", "KRX session hours")):
            record["exhausted"] = outcome[:120]
            status.save(state="segment ended")
            continue
        failures[segment["id"]] = failures.get(segment["id"], 0) + 1
        if failures[segment["id"]] >= MAX_FAILURES_PER_SEGMENT:
            record["exhausted"] = f"{failures[segment['id']]} failures"
        status.save(state="replay failed; retrying", last_error=outcome[:300])
        time.sleep(60)


def status_line(path: Path) -> str:
    """One line for operators and scheduled checks."""
    if not path.exists():
        return "no STATUS.json yet"
    s = json.loads(path.read_text(encoding="utf-8"))
    segment = s.get("segment") or "-"
    record = s.get("segments", {}).get(segment, {})
    return (f"state={s.get('state')} blocked={bool(s.get('blocked'))} segment={segment} "
            f"days={s.get('days_done', 0)}/72 last_day={s.get('last_replayed_day')} "
            f"segment_tokens={record.get('tokens')} week_tokens={s.get('week', {}).get('tokens')} "
            f"next={s.get('next_start')} heartbeat={s.get('heartbeat')} "
            f"error={s.get('blocked') or s.get('last_error')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan")
    parser.add_argument("--status", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--line", action="store_true", help="print the one-line status and exit")
    args = parser.parse_args(argv)
    if args.line:
        print(status_line(Path(args.status)))
        return 0
    if not args.plan:
        parser.error("--plan is required to run")
    return run(Path(args.plan), Path(args.status), Path(args.root).resolve(), sys.executable)


if __name__ == "__main__":
    sys.exit(main())
