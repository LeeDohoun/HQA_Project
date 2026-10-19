"""Standard-library morning admission; no analytic imports or environment hashes."""
from calendar import monthrange
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import re
import subprocess
import time as timer

from . import KST, NotReady, aware, now

# Admission caps, NOT measurements. The final server/private environment must
# validate this entire path (including verification) before activation.
SEAL_BUDGET_SECONDS, SEAL_MARGIN_SECONDS = 180, 60
FW002_TIMERS = ("hqa-fw002-decision.timer", "hqa-fw002-fill.timer")
TIMERS = ("hqa-fw001-daily.timer", "hqa-krx-daily.timer",
          "hqa-shadow-daily.timer", *FW002_TIMERS)
FW002_SERVICES = ("hqa-fw002-decision.service", "hqa-fw002-fill.service",
                  "hqa-fw002-check@decision.service", "hqa-fw002-check@fill.service")
# FW002 src/forward/fw002.py MONTHS, checked read-only at round 38.
FW002_MONTHS = tuple(f"{year}-{month:02d}" for year in range(2026, 2030)
                     for month in range(1, 13) if "2026-10" <= f"{year}-{month:02d}" <= "2029-09")


def last_start(day, budget=SEAL_BUDGET_SECONDS):
    return datetime.combine(day, time(8, 59), KST) - timedelta(seconds=budget + SEAL_MARGIN_SECONDS)


def entry_plan(policy, pos):
    entry, previous = policy.sessions[pos], policy.sessions[pos - 1]
    month = previous.strftime("%Y-%m") if entry.month != previous.month else None
    # Registered experiment periods, not worker/timer schedules. The latter
    # always come from the installed manager, including retries and KRX chains.
    return {"entry_date": entry.date().isoformat(), "previous_session": previous.date().isoformat(),
            "fw001_month": month if month and "2026-10" <= month <= "2027-09" else None,
            "fw002_month": month if month in FW002_MONTHS else None}


def _fields(raw):
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def _seconds(value):
    if value.isdigit():
        return int(value) / 1e6  # systemctl *USec properties
    parts = re.findall(r"(\d+(?:\.\d+)?)(us|ms|min|h|s|d|w)", value.replace(" ", ""))
    if not parts or "".join(n + u for n, u in parts) != value.replace(" ", ""):
        raise ValueError("BB003 cannot bound installed timer duration: " + value)
    return sum(float(n) * {"us": 1e-6, "ms": .001, "s": 1, "min": 60, "h": 3600,
                          "d": 86400, "w": 604800}[u] for n, u in parts)


def _realtime(value):
    if value in {"", "n/a", "0", "infinity", str(2**64 - 1)}:
        return None
    if value.isdigit():
        return datetime.fromtimestamp(int(value) / 1e6, KST)
    value = re.sub(r"^\w{3} ", "", value)
    for suffix, offset in ((" KST", "+09:00"), (" UTC", "+00:00")):
        if value.endswith(suffix):
            value = value[:-len(suffix)] + offset
            break
    return aware(value)


def installed_windows(stamp, *, run):
    """Effective calendars/drop-ins, delayed, Persistent and monotonic triggers.

    KRX can start FW001 immediately through OnSuccess, so every KRX window is
    also an FW001 window. Unknown metadata fails closed, never guesses a time.
    """
    windows, installed = [], set()
    for unit in TIMERS:
        fields = _fields(run(["systemctl", "show", unit,
            "--property=LoadState,NeedDaemonReload,ActiveState,TimersCalendar,TimersMonotonic,AccuracyUSec,RandomizedDelayUSec,NextElapseUSecRealtime,NextElapseUSecMonotonic,Persistent,LastTriggerUSec"], text=True))
        if unit in FW002_TIMERS and fields.get("LoadState") == "not-found":
            continue  # Check the entire installation below, including fill.
        expressions = re.findall(r"OnCalendar=(.*?)\s*;", fields.get("TimersCalendar", ""))
        monotonic = fields.get("TimersMonotonic")
        if (fields.get("LoadState") != "loaded" or fields.get("NeedDaemonReload") != "no"
                or fields.get("ActiveState") != "active" or not (expressions or monotonic)
                or fields.get("Persistent") not in {"yes", "no"}):
            raise ValueError("BB003 needs bounded installed timer metadata: " + unit)
        installed.add(unit)
        delay = _seconds(fields["AccuracyUSec"]) + _seconds(fields["RandomizedDelayUSec"])
        next_real = _realtime(fields["NextElapseUSecRealtime"])
        if (expressions and next_real is None) or delay >= 86400:
            raise ValueError("BB003 cannot bound pending installed timer: " + unit)
        last_trigger = _realtime(fields["LastTriggerUSec"])
        if next_real and next_real < stamp and (last_trigger is None or last_trigger < next_real):
            raise NotReady("pending_installed_timer_catchup:" + unit)
        if next_real:
            windows.append((next_real, next_real + timedelta(seconds=delay), unit))
        # Search from the start of the possible delay interval; this also
        # protects an elapsed calendar trigger whose job has not started yet.
        for expression in expressions:
            base = stamp - timedelta(seconds=delay + .000001)
            output = run(["systemd-analyze", "calendar", "--base-time=" + base.isoformat(),
                          "--iterations=1", expression], text=True)
            match = re.search(r"(?:Next elapse:|\(in UTC\):)\s+\w+ (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", output)
            if match:
                start = aware(match[1].replace(" ", "T") + "+00:00")
                windows.append((start, start + timedelta(seconds=delay), unit))
            elif "Next elapse: never" not in output:
                raise ValueError("BB003 cannot resolve installed timer: " + unit)
        if monotonic:
            value = fields["NextElapseUSecMonotonic"]
            if value in {"", "n/a", "0", "infinity", str(2**64 - 1)}:
                # OnBoot is one-shot. After it fires, a calendar timer normally
                # has no next monotonic elapse. Repeating/unknown rules still
                # need a next elapse, even when an OnBoot rule also exists.
                rules = re.findall(r"\b(On\w+USec)=(.*?)\s*;", monotonic)
                if (not rules or any(name != "OnBootUSec" for name, _ in rules)
                        or last_trigger is None
                        or any(timer.monotonic() <= _seconds(interval) + delay for _, interval in rules)):
                    raise ValueError("BB003 cannot bound pending monotonic timer: " + unit)
            else:
                start = stamp + timedelta(seconds=_seconds(value) - timer.monotonic())
                windows.append((start, start + timedelta(seconds=delay), unit))
    paths = run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager",
                 "--type=path", "hqa-fw002-*.path"], text=True)
    fw002 = bool(installed.intersection(FW002_TIMERS))
    for unit in FW002_SERVICES:
        fields = _fields(run(["systemctl", "show", unit, "--property=LoadState,NeedDaemonReload"], text=True))
        if fields.get("LoadState") != ("loaded" if fw002 else "not-found") or (
                fw002 and fields.get("NeedDaemonReload") != "no"):
            raise ValueError("BB003 incomplete FW002 timer/worker/dispatcher installation: " + unit)
    if (fw002 and not set(FW002_TIMERS) <= installed) or (not fw002 and paths.strip()):
        raise ValueError("BB003 incomplete FW002 decision/fill trigger installation")
    for row in paths.splitlines():
        unit = row.split()[0]
        match = re.fullmatch(r"hqa-fw002-(decision|fill)@(202[67]\d\d)\.path", unit)
        if not match or not "202610" <= match[2] <= "202709":
            raise ValueError("BB003 unknown FW002 path trigger: " + unit)
        fields = _fields(run(["systemctl", "show", unit,
            "--property=LoadState,NeedDaemonReload,ActiveState,Triggers,Paths"], text=True))
        target = "hqa-fw002-check@" + match[1] + ".service"
        expected = [f"/var/lib/hqa/data/forward/fw001/sealed/{match[2]}." + suffix for suffix in
                    (["decision"] if match[1] == "decision" else ["execution.1.0", "execution.1.5", "execution.2.0"])]
        if (fields.get("LoadState") != "loaded" or fields.get("NeedDaemonReload") != "no"
                or fields.get("ActiveState") not in {"active", "inactive"}
                or fields.get("Triggers", "").split() != [target]
                or any(p not in fields.get("Paths", "") for p in expected)):
            raise ValueError("BB003 cannot verify FW002 path trigger: " + unit)
        # Paths are asynchronous. Recheck obligations at admission/sealing;
        # memory admission reserves BB003 + one FW002 worker for later races.
        installed.add(unit)
    krx = _fields(run(["systemctl", "show", "hqa-krx-daily.service",
                       "--property=LoadState,NeedDaemonReload,OnSuccess"], text=True))
    if (krx.get("LoadState") != "loaded" or krx.get("NeedDaemonReload") != "no"
            or "hqa-fw001-daily.service" not in krx.get("OnSuccess", "").split()):
        raise ValueError("BB003 requires installed KRX OnSuccess=FW001 contract")
    return windows, installed


def refuse_active(run):
    raw = run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager",
               "--type=service", "--state=active,activating,reloading,deactivating",
               "hqa-fw001-*", "hqa-fw002-*", "hqa-shadow-daily.service", "hqa-krx-daily.service"], text=True)
    if raw.strip():
        raise NotReady("higher_priority_worker_busy:" + raw.strip())
    jobs = run(["systemctl", "list-jobs", "--no-legend", "--no-pager"], text=True)
    for line in jobs.splitlines():
        if any(part.startswith(("hqa-fw001-", "hqa-fw002-")) or part in {
                "hqa-shadow-daily.service", "hqa-krx-daily.service"} for part in line.split()):
            raise NotReady("higher_priority_worker_queued:" + line.strip())


def _marker(source, owner, month, entry, *, verify=False):
    base = Path(source) / "forward" / owner
    candidate = base / "decisions" / (month.replace("-", "") + ".json")
    marker = (base / "sealed" / (month.replace("-", "") + ".decision") / "complete.json"
              if owner == "fw001" else Path(str(candidate) + ".complete"))
    if not marker.is_file() or not candidate.is_file():
        raise NotReady(owner + "_decision_marker_missing:" + entry)
    raw = candidate.read_bytes()
    record, proof = json.loads(raw), json.loads(marker.read_bytes())
    if record["trade_date"] != entry or record["month"] != month or record["label"] != "forward":
        raise NotReady(owner + "_decision_marker_wrong_entry_or_label:" + entry)
    deadline = aware(record["deadline"])
    if deadline != datetime.combine(datetime.fromisoformat(entry).date(), time(8, 59), KST):
        raise ValueError(owner + " decision marker has a different entry deadline")
    if owner == "fw001":
        published = marker.parent.stat().st_mtime_ns
        upper = datetime.fromtimestamp((published + 1_000_000_000) / 1e9, KST)
        valid = (proof["record_path"] == candidate.relative_to(source).as_posix()
                 and proof["publication_clock"] == "private_directory_mtime_ns"
                 and proof["publication_precision_ns"] == 1_000_000_000 and proof["reconstructed"] is False
                 and {p.name for p in marker.parent.iterdir()} == {"complete.json"}
                 and published >= marker.stat().st_mtime_ns
                 and aware(record["finalised_at"]) <= upper < deadline
                 and aware(proof["prepared_at"]) <= upper and proof["deadline"] == record["deadline"])
        digest = proof["record_digest"]
    else:
        valid = (record["experiment_id"] == "FW002_investor_flow_forward" and record["record_type"] == "decision"
                 and proof["experiment_id"] == "FW002_investor_flow_forward"
                 and proof["record_type"] == "decision_completion" and proof["deadline"] == record["deadline"]
                 and proof["calendar_version"] == record["calendar_version"]
                 and aware(record["S_t"]) <= aware(record["finalised_at"]) <= aware(proof["completed_at"]) < deadline)
        digest = proof["decision_hash"]
    if not valid:
        raise NotReady(owner + "_decision_marker_unsealed:" + entry)
    if verify:
        import hashlib
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(owner + " decision bytes differ from the completion marker")
    return record, digest


def fw002_ready(source, state, stamp, installed, *, until=None, verify=False):
    """Decision eligibility and ALL unfinished fill months, not just month-end.

    The sealed BB003 official calendar supplies dates without analytic imports.
    Setup without a state/calendar still checks existing fill obligations; only
    preparation/recording supplies state and checks prospective decisions.
    """
    if not set(FW002_TIMERS) <= installed:
        return
    horizon = stamp if until is None else until
    if state is not None:
        from .storage import Store
        store = Store(Path(state).parent.parent)
        if store.root != Path(state).resolve():
            raise ValueError("BB003 invalid priority-calendar state root")
        keys = store.keys("policy")
        if not keys:
            raise NotReady("FW002_priority_calendar_missing")
        policy = store.get("policy", keys[-1])
        if policy is None:
            raise NotReady("FW002_priority_calendar_unsealed")
        data = policy["policy"]
        today = stamp.date().isoformat()
        if not data["calendar_start"] <= today <= data["calendar_through"]:
            raise NotReady("FW002_priority_calendar_unverified:" + today)
        months = {today[:7], (stamp.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")}
        for month in sorted(months):
            if month not in FW002_MONTHS:
                continue
            days = [d for d in data["sessions"] if d.startswith(month + "-")]
            end = month + f"-{monthrange(int(month[:4]), int(month[5:]))[1]:02d}"
            if data["calendar_through"] < end:
                if any(d > horizon.date().isoformat() for d in days):
                    continue  # A known later session proves month-end is not due.
                raise NotReady("FW002_priority_month_end_calendar_unverified:" + month)
            if not days:
                raise ValueError("BB003 priority calendar has no monthly sessions")
            due = datetime.fromisoformat(days[-1] + "T19:00:00+09:00")
            if horizon < due:
                continue
            entries = [d for d in data["sessions"] if d > days[-1]]
            if not entries:
                raise NotReady("FW002_priority_entry_calendar_unverified:" + month)
            deadline = aware(entries[0] + "T08:59:00+09:00")
            if stamp >= deadline:
                continue  # A missing decision cannot now be started by FW002.
            _marker(source, "fw002", month, entries[0], verify=verify)
    base = Path(source) / "forward/fw002"
    for path in sorted((base / "decisions").glob("*.json")):
        if (not re.fullmatch(r"\d{6}\.json", path.name)
                or path.stem[:4] + "-" + path.stem[4:] not in FW002_MONTHS):
            raise ValueError("BB003 unsafe/unregistered FW002 decision path")
        decision = json.loads(path.read_bytes())
        month, entry = path.stem[:4] + "-" + path.stem[4:], decision["trade_date"]
        if entry == "pending_calendar":
            raise NotReady("FW002_fill_entry_calendar_unverified:" + month)
        datetime.strptime(entry, "%Y-%m-%d")
        decision, digest = _marker(source, "fw002", month, entry, verify=verify)
        missing = []
        for multiplier in (1.0, 1.5, 2.0):
            fill = base / "fills" / f"{path.stem}_{multiplier}.json"
            if not fill.is_file():
                missing.append(multiplier)
                continue
            record = json.loads(fill.read_bytes())
            if (record["experiment_id"] != "FW002_investor_flow_forward" or record["record_type"] != "fill"
                    or record["month"] != month or record["trade_date"] != entry or record["label"] != "forward"
                    or record["decision_hash"] != digest or record["cost_multiplier"] != multiplier
                    or record["decision_calendar_version"] != decision["calendar_version"]
                    or aware(record["recorded_at"]) < aware(decision["finalised_at"])
                    or aware(record["recorded_at"]).date().isoformat() < entry):
                raise ValueError(f"BB003 invalid FW002 fill/decision linkage:{month}:{multiplier}")
        if not missing or horizon.date().isoformat() < entry:
            continue  # Complete, or the entry date has not arrived yet.
        if month <= "2027-09" and any(not (Path(source) / "forward/fw001/sealed" /
                f"{path.stem}.execution.{m}/complete.json").is_file() for m in (1.0, 1.5, 2.0)):
            # FW002's dispatcher skips this month until ALL FW001 executions
            # exist. Rechecks detect eligibility changes; a later race may
            # overlap BB003 and is included in the sealed memory budget.
            continue
        raise NotReady(f"fw002_fill_missing:{month}:{missing[0]}")


def krx_complete(source, day):
    """Stream the exact prior session, both markets; never hash at admission."""
    compact = day.replace("-", "")
    path = Path(source) / f"market/krx_daily/{day[:4]}/{compact}.jsonl"
    if not path.is_file():
        raise NotReady("previous_session_KRX_missing:" + day)
    markets, codes = set(), set()
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise NotReady("previous_session_KRX_invalid:" + day) from exc
            code = row.get("stock_code")
            if (row.get("trade_date") != day or row.get("calendar_status") != "verified"
                    or not isinstance(code, str) or not re.fullmatch(r"[0-9A-Z]{6}", code) or code in codes):
                raise NotReady("previous_session_KRX_invalid:" + day)
            codes.add(code)
            markets.add(row.get("market"))
    if not {"KOSPI", "KOSDAQ"} <= markets:
        raise NotReady("previous_session_KRX_incomplete_both_markets:" + day)


def morning(source, state, *, clock=now, run=None, verify=False, budget=SEAL_BUDGET_SECONDS):
    run = subprocess.check_output if run is None else run
    stamp = aware(clock())
    if stamp.time() < time(8, 3) or stamp > last_start(stamp.date(), budget):
        raise NotReady("morning_seal_runtime_cutoff:" + last_start(stamp.date()).isoformat())
    path = Path(state) / "morning_plan" / (stamp.date().isoformat() + ".json")
    if not path.is_file():
        raise NotReady("immutable_night_preparation_missing")
    plan = json.loads(path.read_bytes())
    if not isinstance(plan, dict) or set(plan) != {"entry_date", "previous_session", "fw001_month", "fw002_month"}:
        raise ValueError("BB003 invalid morning admission plan schema")
    if plan["entry_date"] != stamp.date().isoformat():
        raise ValueError("BB003 morning plan has a different entry day")
    previous = plan["previous_session"]
    if (not isinstance(previous, str) or not re.fullmatch(r"\d{4}-\d\d-\d\d", previous)
            or datetime.fromisoformat(previous).date() >= stamp.date()
            or "2026-01-01" <= previous <= "2026-06-30"):
        raise ValueError("BB003 unsafe or protected previous session in admission plan")
    for owner in ("fw001", "fw002"):
        month = plan[owner + "_month"]
        if month is not None and (not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d\d", month) or month != previous[:7]):
            raise ValueError("BB003 unsafe priority month in admission plan")
    if any(part.startswith(".env") for part in Path(source).parts):
        raise ValueError("BB003 refuses environment-file input paths")
    windows, installed = installed_windows(stamp, run=run)
    refuse_active(run)
    for owner in ("fw001", "fw002"):
        month = plan[owner + "_month"]
        if month and (owner == "fw001" or set(FW002_TIMERS) <= installed):
            _marker(source, owner, month, plan["entry_date"], verify=verify)
    fw002_ready(source, state, stamp, installed, until=stamp + timedelta(seconds=budget + SEAL_MARGIN_SECONDS), verify=verify)
    krx_complete(source, plan["previous_session"])
    # Queries/streaming consume wall time. Recheck the budget at the end.
    stamp = aware(clock())
    finish = stamp + timedelta(seconds=budget + SEAL_MARGIN_SECONDS)
    if stamp > last_start(stamp.date(), budget):
        raise NotReady("morning_seal_runtime_cutoff:" + last_start(stamp.date()).isoformat())
    for earliest, latest, unit in windows:
        # FW002 timers dispatch eligibility checks, including monotonic fills.
        # Decision/fill obligations, rather than these eligibility-check timer
        # ticks, determine admission. A later race may overlap both workers.
        if unit not in FW002_TIMERS and latest >= stamp and earliest <= finish:
            raise NotReady("higher_priority_trigger_within_seal_budget:" + unit + ":" + earliest.isoformat())
    fw002_ready(source, state, stamp, installed, until=finish, verify=verify)
    refuse_active(run)
    stamp = aware(clock())
    if stamp > last_start(stamp.date(), budget):
        raise NotReady("morning_seal_runtime_cutoff:" + last_start(stamp.date()).isoformat())
    return plan


def missed(state, reason, *, clock=now):
    """Cheap terminal audit, even on denial before verification/hashing.

    The next verified 09:01 run binds this receipt into each missed event.
    This is operational failure evidence, never an analytical completion seal.
    """
    stamp = aware(clock())
    if stamp >= last_start(stamp.date()):
        import os
        import pwd
        from .storage import encoded, put
        owner = None
        if os.geteuid() == 0:
            account = pwd.getpwnam("hqa")
            owner = (account.pw_uid, account.pw_gid)
        path = Path(state) / "morning_missed" / (stamp.date().isoformat() + ".json")
        if not path.exists():
            put(path, encoded({"status": "data_not_ready", "entry_date": stamp.date().isoformat(),
                              "reason": reason, "recorded_at": stamp.isoformat(), "no_retroactive_buy": True}),
                owner=owner)
