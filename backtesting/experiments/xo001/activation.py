"""November plans/attestations and the irrevocable November 30 activation gate."""
from datetime import date
import importlib.metadata
from pathlib import Path
import platform
import sys

import numpy as np

from . import ACTIVATION_BY, EXPERIMENT_ID, Refused, aware
from .calendar import time_map
from .fields import Profile
from .storage import encoded, load, read, sha

CONFIG = {"pre_poll_seconds": 30, "post_poll_seconds": 5, "requests_per_second": 1,
          "pre_poll_stop_seconds_before_0859": 5,
          "priority": ["event", "matched", "baseline"], "clock_error_limit_seconds": .5,
          "snapshots": ["s0830", "s0850", "s0855", "s0858", "s0859"],
          "max_book_age_seconds": 60,
          "cleanup_inquiry_offsets_seconds": [2, 10, 30, 60],
          "websocket": "disabled; no host or port authorized by this implementation"}
OBS_CHECKS = {"paper_reception", "preopen_date_session", "KRX_exchange", "trade_print_fields", "dedicated_key_separate_from_collectors",
              "clock_error", "seal_kernel_precision", "restart_preservation", "rate_limit", "main_key"}
ORDER_CHECKS = {"market_accepted_number", "filled_price_quantity", "limit_cancel_final_zero", "flatten_confirmed"}
MAIN_CHECKS = ORDER_CHECKS | {"initial_positions_zero", "initial_open_orders_zero", "account_policy_verified", "dedicated_account_separate_from_M8"}
PREREG = "research/experiments/XO001_open_executability/preregistration.md"
NOTES = "research/experiments/XO001_open_executability/implementation_notes.md"
PREREG_COMMIT = "0f4152020383679f0ff7865ddcf80d02621834d8"
PREREG_SHA256 = "96d36b5721d88abd6f34a348d9927f8aeeb11f116eca3db3ff9949b4b002db8b"


def fingerprint(repo=None):
    root = Path(repo) if repo else Path(__file__).resolve().parents[3]
    names = set()
    for relative in ("backtesting/experiments/xo001", "scripts/xo001", "deploy/collector-xo001"):
        for path in (root / relative).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path.name != "manifest.json":
                names.add(path.relative_to(root).as_posix())
    names.update({"backtesting/__init__.py", "backtesting/experiments/__init__.py", "scripts/__init__.py",
                  "backtesting/experiment_registry.py", "backtesting/append_only.py",
                  "src/__init__.py", "src/runner/__init__.py", "src/runner/trading_calendar.py",
                  "backtesting/experiments/bb001.py", PREREG, NOTES,
                  "research/experiments/BB001_direct_buyback/preregistration.md",
                  "research/experiments/BB003_buyback_forward/preregistration.md",
                  *("backtesting/experiments/bb003/" + name + ".py" for name in ("storage", "inputs", "recorder", "policy"))})
    files = {p: sha(read(root / p)) for p in sorted(names)}
    if files[PREREG] != PREREG_SHA256:
        raise Refused("XO001 preregistration differs from its fixed registration commit")
    return {"files": files, "code_sha256": sha(encoded(files))}


def environment():
    """Pin interpreter and installed file bytes, not a mutable version label."""
    distributions = {}
    for name in ("numpy", "pandas", "exchange-calendars", "pyluach", "korean-lunar-calendar",
                 "python-dateutil", "toolz", "tzdata", "six", "PyYAML"):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            # Optional calendar dependencies differ by package version; record absence explicitly.
            distributions[name] = {"installed": False}
            continue
        files = {str(p): sha(Path(distribution.locate_file(p)).read_bytes()) for p in distribution.files or []
                 if not str(p).endswith((".pyc", ".pyo")) and Path(distribution.locate_file(p)).is_file()}
        if not files:
            raise Refused("installed dependency has no hashable file inventory")
        distributions[name] = {"version": distribution.version, "files_sha256": sha(encoded(files))}
    value = {"python": platform.python_version(), "executable_sha256": sha(Path(sys.executable).resolve().read_bytes()),
             "distributions": distributions, "numpy_version": np.__version__, "host": platform.node()}
    return {"environment": value, "environment_sha256": sha(encoded(value))}


def binding(credentials, profile, *, repo=None):
    return {**fingerprint(repo), **environment(), **credentials.identifiers(),
            "profile_sha256": sha(encoded(profile.data)), "config_sha256": sha(encoded(CONFIG))}


def seal_plan(store, calendar, data):
    if store.proof("shakedown_plan", "november") or store.keys("shakedown") or store.keys("runs"):
        raise Refused("activation tests must be fixed before the first shakedown")
    if set(data) != {"tests", "main_account", "account_policy"}:
        raise Refused("shakedown plan needs tests, main_account and account_policy")
    policy = data["account_policy"]
    if (not isinstance(policy, dict) or set(policy) != {"main_valid_through", "competition_valid_through", "replacement", "renewal", "on_expiry"}
            or policy["competition_valid_through"] != "2026-11-20" or policy["replacement"] != "forbidden"
            or policy["renewal"] != "forbidden" or policy["on_expiry"] != "disable_buys_preserve_state_cleanup_same_account"):
        raise Refused("seal account expiry/renewal rules; undefined replacement is forbidden")
    date.fromisoformat(policy["main_valid_through"])
    rows = [*data["tests"], data["main_account"]]
    if len(data["tests"]) < 3 or len({r["date"] for r in data["tests"]}) != len(data["tests"]):
        raise Refused("at least three different predetermined November test dates required")
    if data["main_account"]["date"] in {r["date"] for r in data["tests"]}:
        raise Refused("the additional main-account test needs a separate predetermined date")
    for row in rows:
        if (set(row) != {"date", "stock_code", "market_order_type", "limit_order_type", "limit_price"}
                or not "2026-11-01" <= row["date"] <= "2026-11-30"
                or row["market_order_type"] != "01" or row["limit_order_type"] != "00"
                or type(row["limit_price"]) is not int or row["limit_price"] <= 0):
            raise Refused("invalid predetermined activation order test")
        import re
        if not re.fullmatch(r"[0-9A-Z]{6}", row["stock_code"]):
            raise Refused("invalid predetermined activation stock")
        plan = calendar.day(row["date"])
        if not plan["session"] or plan["special"]:
            raise Refused("activation tests require verified normal opening days")
        if aware(store.clock()) >= time_map(plan)["roster"]:
            raise Refused("cannot preregister an activation test after its run began")
    return store.seal("shakedown_plan", "november", data)


def seal_profile(store, data, official_spec):
    if store.keys("shakedown") or store.keys("runs"):
        raise Refused("field contract must be sealed before the first shakedown")
    profile = Profile(data)
    old = store.get("field_profile", "paper")
    if old and old["profile"] != data:
        raise Refused("the sealed field contract cannot be replaced")
    if sha(official_spec) != data["official_spec_sha256"]:
        raise Refused("field contract official specification hash differs")
    store.blob(official_spec)
    return store.seal("field_profile", "paper", {"profile": profile.data})


def profile_from(store):
    record = store.get("field_profile", "paper")
    if not record:
        raise Refused("seal the PAPER field contract first")
    profile = Profile(record["profile"])
    store.bytes(profile.data["official_spec_sha256"])
    return profile


def attest(store, kind, evidence, current_binding):
    """Coordinator review, bound to local raw evidence and the tested runtime."""
    expected = OBS_CHECKS if kind == "observation" else MAIN_CHECKS if kind == "main_account" else ORDER_CHECKS
    if kind not in {"observation", "order", "main_account"} or not store.get("shakedown_plan", "november"):
        raise Refused("shakedown requires its sealed test plan")
    if (set(evidence["checks"]) != expected or any(type(v) is not bool for v in evidence["checks"].values())
            or not evidence.get("evidence_hashes") or not "2026-11-01" <= evidence["date"] <= "2026-11-30"
            or aware(store.clock()).date().isoformat() < evidence["date"]):
        raise Refused("invalid shakedown attestation")
    for digest in evidence["evidence_hashes"]:
        store.bytes(digest)
    plan = store.get("shakedown_plan", "november")
    allowed = [plan["main_account"]] if kind == "main_account" else plan["tests"]
    if kind != "observation" and evidence["date"] not in {r["date"] for r in allowed}:
        raise Refused("cannot substitute successful dates for planned tests")
    value = {**evidence, "kind": kind, "binding": current_binding}
    return store.seal("shakedown", kind + "." + evidence["date"], value, deadline=ACTIVATION_BY)


def activate(store, current_binding):
    if store.proof("activation", "activation"):
        raise Refused("XO001 activation decision cannot be replaced")
    if aware(store.clock()) > ACTIVATION_BY:
        raise Refused("not_started: activation deadline passed")
    profile_from(store)
    plan = store.get("shakedown_plan", "november")
    if not plan:
        raise Refused("sealed November test plan is missing")
    records = [store.get("shakedown", k) for k in store.keys("shakedown")]
    observations = [r for r in records if r["kind"] == "observation" and r["binding"] == current_binding
                    and all(r["checks"].values())]
    if not observations:
        raise Refused("not_started: main-period key observation validation incomplete")
    # Test keys may differ, but code, environment, field semantics and fixed config may not.
    def tested(row):
        return row and all(row["checks"].values()) and all(row["binding"][k] == current_binding[k]
            for k in ("code_sha256", "environment_sha256", "profile_sha256", "config_sha256"))
    orders = {r["date"]: r for r in records if r["kind"] == "order"}
    main = next((r for r in records if r["kind"] == "main_account" and r["binding"] == current_binding), None)
    enabled = all(tested(orders.get(r["date"])) for r in plan["tests"]) and tested(main)
    proof = store.seal("activation", "activation", {"experiment_id": EXPERIMENT_ID, "binding": current_binding,
        "preregistration_commit": PREREG_COMMIT, "preregistration_sha256": PREREG_SHA256,
        "config": CONFIG, "diagnostic_approved": bool(enabled), "account_policy": plan["account_policy"],
        "shakedown_hashes": [store.proof("shakedown", k)["record_sha256"] for k in store.keys("shakedown")]},
        deadline=ACTIVATION_BY, fixed_path="activation.json")
    if not proof["valid"]:
        raise Refused("not_started: kernel completion bound missed activation deadline")
    return proof


def status(store):
    proof = store.proof("activation", "activation")
    if proof and proof["valid"]:
        return "active"
    return "not_started" if aware(store.clock()) > ACTIVATION_BY else "not_activated"


def verify(store, current_binding):
    if status(store) != "active":
        raise Refused(status(store))
    record = store.get("activation", "activation")
    expected = record["binding"]
    for key in store.keys("bugfix"):
        proof = store.proof("bugfix", key)
        if not proof["valid"]:
            raise Refused("invalid XO001 bug-fix amendment proof")
        change = proof["record"]
        for digest in change["evidence_hashes"]:
            store.bytes(digest)
        if change["before"] != expected or not change["reason"].strip():
            raise Refused("broken XO001 bug-fix amendment chain")
        expected = change["after"]
    if expected != current_binding:
        raise Refused("sealed code/environment/key/account/profile binding differs")
    return record


def diagnostic_enabled(store, current_binding):
    if status(store) != "active":
        return False
    activation = verify(store, current_binding)
    flag = store.path("activation-control/diagnostic.enabled.json")
    if not activation["diagnostic_approved"] or not flag.is_file() or flag.is_symlink():
        return False
    status_bits = flag.stat()
    parent = flag.parent.stat()
    if status_bits.st_uid != 0 or status_bits.st_mode & 0o022 or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise Refused("diagnostic flag must be coordinator/root-owned and non-writable by the service")
    return load(flag) == {"activation_sha256": store.proof("activation", "activation")["record_sha256"]}


def bugfix(store, after, *, reason, evidence_hashes):
    record = store.get("activation", "activation")
    if not record or not reason.strip() or not evidence_hashes:
        raise Refused("bug fix requires activation, reason and validation evidence")
    keys = store.keys("bugfix")
    before = store.get("bugfix", keys[-1])["after"] if keys else record["binding"]
    for key in before:
        if key not in {"files", "code_sha256"} and after[key] != before[key]:
            raise Refused("bug fixes cannot redefine environment, identifiers, config or fields")
    changed = {p for p in set(before["files"]) | set(after["files"]) if before["files"].get(p) != after["files"].get(p)}
    if not changed or any(p != NOTES and not p.startswith(("backtesting/experiments/xo001/", "scripts/xo001/", "deploy/collector-xo001/")) for p in changed):
        raise Refused("bug-fix changes must stay inside XO001 code roots")
    for digest in evidence_hashes:
        store.bytes(digest)
    return store.seal("bugfix", f"{len(keys):06d}", {"before": before, "after": after,
        "reason": reason, "changed_files": sorted(changed), "evidence_hashes": evidence_hashes})


def runtime_verification(store):
    """Offline measurement paths verify runtime bytes without loading credentials."""
    record = store.get("activation", "activation")
    if not record:
        raise Refused(status(store))
    current = {**fingerprint(), **environment(),
        **{k: record["binding"][k] for k in ("key_sha256", "account_sha256")},
        "profile_sha256": sha(encoded(profile_from(store).data)), "config_sha256": sha(encoded(CONFIG))}
    verify(store, current)
    return {"binding": current,
        "activation_sha256": store.proof("activation", "activation")["record_sha256"],
        "approved_amendment_sha256": [store.proof("bugfix", k)["record_sha256"] for k in store.keys("bugfix")]}


def verify_unchanged(store, captured):
    if runtime_verification(store) != captured:
        raise Refused("XO001 runtime changed while measuring")
