"""XO001 CLI. Network operations require --execute and the private installed root."""
import argparse
from contextlib import nullcontext
from datetime import timedelta
import json
from pathlib import Path
import sys

from backtesting.experiments.xo001 import Refused, aware
from backtesting.experiments.xo001 import activation
from backtesting.experiments.xo001.calendar import Calendar, time_map
from backtesting.experiments.xo001.kis import Client, Credentials
from backtesting.experiments.xo001.rate import RateManager
from backtesting.experiments.xo001.roster import BB003
from backtesting.experiments.xo001.storage import Store, load, read

CODE_ROOT = Path("/opt/hqa-xo001")
DATA_ROOT = Path("/var/lib/hqa-xo001")
LIVE = {"smoke", "morning", "cleanup", "test-orders"}
COMMANDS = ("plan", "status", "smoke", "morning", "cleanup", "reconcile", "plan-seal", "profile-seal",
            "attest", "activate", "flag-template", "account-evidence", "calendar-add", "evaluate", "test-orders", "bugfix", "import-evidence")


def require_installed(store):
    if (Path(__file__).resolve().parents[2] != CODE_ROOT or store.root != DATA_ROOT
            or Path(sys.prefix) != CODE_ROOT / "venv" or (CODE_ROOT / "venv").is_symlink()):
        raise Refused("live XO001 requires /opt/hqa-xo001, its private venv and /var/lib/hqa-xo001")


def shakedown_gate(store, binding, *, main_account=False, competition_account=False):
    def gate(*_):
        plan = store.get("shakedown_plan", "november")
        if not plan or not "2026-11-01" <= aware(store.clock()).date().isoformat() <= "2026-11-30":
            return False
        expiry = plan["account_policy"]["competition_valid_through" if competition_account else "main_valid_through"]
        if aware(store.clock()).date().isoformat() > expiry:
            return False
        flag = store.path("activation-control/shakedown.orders.enabled.json")
        if not flag.exists():
            return False
        if (flag.is_symlink() or flag.stat().st_uid != 0 or flag.stat().st_mode & 0o022
                or flag.parent.stat().st_uid != 0 or flag.parent.stat().st_mode & 0o022):
            raise Refused("shakedown order flag must be coordinator/root-owned")
        return load(flag) == {"plan_sha256": store.proof("shakedown_plan", "november")["record_sha256"],
                              "key_sha256": binding["key_sha256"], "main_account": main_account,
                              "competition_account": competition_account}
    return gate


def execute(args, store):
    from backtesting.experiments.xo001.recorder import clock_evidence, clock_valid, reconcile_pending, run_morning
    from backtesting.experiments.xo001.diagnostic import Diagnostic
    calendar, bb003 = Calendar(store), BB003(args.bb003_root, store)
    if args.command == "plan":
        return {"experiment_id": "XO001_open_executability", "config": activation.CONFIG,
            "code_root": str(CODE_ROOT), "data_root": str(store.root), "diagnostic": "OFF until sealed approval and coordinator flag",
            "freeze": "last possible BB003 entry date + 7 calendar days, 23:59:59 KST", "network": "PAPER allowlist only; --execute required"}
    if args.command == "morning" and not args.shakedown and activation.status(store) != "active":
        state = activation.status(store)
        if state == "not_started":
            store.seal("terminal", "not_started", {"status": state, "reason": "activation deadline missed"})
        return {"status": state, "orders": False}
    if args.command == "cleanup" and not args.shakedown and activation.status(store) != "active":
        return {"status": "OFF"}
    if args.command == "status":
        result = activation.status(store)
        if args.execute and result == "not_started":
            store.seal("terminal", "not_started", {"status": result, "reason": "no valid activation by 2026-11-30 23:59:59 KST"})
        return {"status": result}
    if args.command == "plan-seal":
        return store_result(activation.seal_plan(store, calendar, load(required(args.file, "--file"))))
    if args.command == "profile-seal":
        return store_result(activation.seal_profile(store, load(required(args.file, "--file")), read(required(args.spec, "--spec"))))
    if args.command == "calendar-add":
        return store_result(calendar.add_official(load(required(args.file, "--file")), read(required(args.spec, "--spec"))))
    if args.command == "import-evidence":
        # The input must already be sanitized. Credentials/tokens are never imported.
        raw = read(required(args.file, "--file"))
        credentials = Credentials.environment()
        from backtesting.experiments.xo001.kis import Redactor
        redactor = Redactor(credentials)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise Refused("import-evidence requires reviewed sanitized JSON") from None
        cleaned = redactor.clean(value)
        if cleaned != value or redactor.text(raw.decode("utf-8")) != raw.decode("utf-8"):
            raise Refused("evidence must contain no credentials, tokens or raw auth fields")
        return {"sha256": store.blob(raw)}
    if args.command == "evaluate":
        from backtesting.experiments.xo001.evaluate import evaluate
        report = evaluate(store, calendar, bb003, registry_root=required(args.registry_root, "--registry-root"))
        return {**{k: report[k] for k in ("status", "experiment_id", "verification")},
                **store_result(store.proof("evaluation", "final"))}
    if args.command == "reconcile":
        return reconcile_pending(store, args.source_data, calendar, bb003, day=args.date)
    if args.command == "flag-template" and not args.shakedown:
        record = store.get("activation", "activation")
        if not record or not record["diagnostic_approved"]:
            raise Refused("the sealed decision keeps order diagnostics OFF for the entire main period")
        return {"activation_sha256": store.proof("activation", "activation")["record_sha256"]}
    credentials = Credentials.environment()
    if args.command == "flag-template":
        proof = store.proof("shakedown_plan", "november")
        if not proof:
            raise Refused("test plan missing")
        return {"plan_sha256": proof["record_sha256"], **{k: v for k, v in credentials.identifiers().items() if k == "key_sha256"},
                "main_account": args.main_account, "competition_account": args.competition_account}
    profile = activation.profile_from(store) if args.command != "smoke" else None
    binding = activation.binding(credentials, profile) if profile else {
        **activation.fingerprint(), **activation.environment(), **credentials.identifiers()}
    if args.command == "attest":
        return store_result(activation.attest(store, required(args.kind, "--kind"), load(required(args.file, "--file")), binding))
    if args.command == "activate":
        return store_result(activation.activate(store, binding))
    if args.command == "bugfix":
        data = load(required(args.file, "--file"))
        return store_result(activation.bugfix(store, binding, reason=data["reason"], evidence_hashes=data["evidence_hashes"]))
    if args.command == "account-evidence":
        # This distinct immutable namespace may be written while the recorder owns its lock.
        diagnostic = Diagnostic(store, type("AccountContext", (), {"credentials": credentials})(), binding)
        return store_result(diagnostic.account(calendar.day(args.date or aware(store.clock()).date().isoformat()), load(required(args.file, "--file"))))
    rate = RateManager(store.path("control"), clock=store.clock)
    with Client(credentials, store.root, rate, clock=store.clock) as client:
        if args.command == "smoke":
            deadline = aware(store.clock()) + timedelta(seconds=30)
            results = [client.quote(required(args.code, "--code"), book=book, priority="event", deadline=deadline) for book in (True, False)]
            results.append(client.approval_probe(deadline=deadline))
            proof = store.append("smoke", {"binding": binding, "envelopes": results, "clock": clock_evidence(), "activates_nothing": True})
            return {**store_result(proof), "errors": [r["error"] for r in results]}
        if args.command in {"morning", "test-orders"}:
            diagnostic = None
            if args.command == "test-orders":
                gate = shakedown_gate(store, binding, main_account=args.main_account, competition_account=args.competition_account)
                diagnostic = Diagnostic(store, client, binding, gate=gate)
                if not diagnostic.enabled():
                    raise Refused("coordinator shakedown order flag is OFF")
                diagnostic.seal_test_intents(calendar.day(aware(store.clock()).date().isoformat()), main_account=args.main_account)
            elif not args.shakedown:
                diagnostic = Diagnostic(store, client, binding)
            return run_morning(store, client, bb003, args.source_data, calendar, binding,
                shakedown=args.shakedown or args.command == "test-orders", diagnostic=diagnostic)
        if args.command == "cleanup":
            if args.shakedown:
                gate = shakedown_gate(store, binding, main_account=args.main_account, competition_account=args.competition_account)
                diagnostic = Diagnostic(store, client, binding, gate=gate)
            else:
                diagnostic = Diagnostic(store, client, binding)
            day = aware(store.clock()).date().isoformat()
            try:
                plan = calendar.day(day)
                if not plan["session"]:
                    return {"status": "closed"}
                times = time_map(plan)
            except Refused as exc:
                store.seal("missing_cleanup", day, {"date": day, "reason": str(exc), "orders": False})
                return {"status": "calendar_unverified", "orders": False}
            if not diagnostic.enabled():
                return {"status": "OFF"}
            if not clock_valid(clock_evidence()):
                raise Refused("no orders with an unverified clock")
            import time
            while aware(store.clock()) < times["flatten"]:
                time.sleep(.2)
            if not clock_valid(clock_evidence()):
                raise Refused("no cleanup orders with an unverified clock")
            for order_day in diagnostic.pending_dates():
                diagnostic.inquire(order_day, deadline=times["close"])
            diagnostic.cancel(plan)
            result = diagnostic.flatten(plan)
            for offset in activation.CONFIG["cleanup_inquiry_offsets_seconds"]:
                check_at = times["flatten"] + timedelta(seconds=offset)
                while aware(store.clock()) < check_at:
                    time.sleep(.2)
                if aware(store.clock()) >= times["close"]:
                    break
                for order_day in diagnostic.pending_dates():
                    diagnostic.inquire(order_day, deadline=times["close"])
                if diagnostic.cleared():
                    break
            return {**result, "confirmed_positions": diagnostic.positions(), "flattened": diagnostic.cleared()}
    raise Refused("unhandled XO001 command")


def required(value, option):
    if value is None:
        raise Refused(option + " is required")
    return value


def store_result(proof):
    return {"record_sha256": proof["record_sha256"], "valid": proof["valid"], "record_path": proof["record_path"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--bb003-root", type=Path, default=Path("/var/lib/hqa/data/forward/bb003"))
    parser.add_argument("--source-data", type=Path, default=Path("/var/lib/hqa/data"))
    parser.add_argument("--file", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--code")
    parser.add_argument("--kind", choices=("observation", "order", "main_account"))
    parser.add_argument("--shakedown", action="store_true")
    parser.add_argument("--main-account", action="store_true")
    parser.add_argument("--competition-account", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.main_account and args.competition_account:
        parser.error("the additional main-account verification cannot use the competition account")
    store = Store(args.data_root, inputs=(args.bb003_root, args.source_data))
    if args.command not in {"plan", "status", "flag-template"} and not args.execute:
        result = {"status": "dry_run", "command": args.command, "network": False, "writes": False}
    else:
        if args.command in LIVE:
            require_installed(store)
        context = nullcontext() if args.command in {"plan", "status", "flag-template", "account-evidence", "import-evidence"} else store.lock()
        try:
            with context:
                result = execute(args, store)
        except (Refused, FileNotFoundError) as exc:
            # All exceptions exposed here use fixed safe messages; never dump credentials/transport objects.
            result = {"status": "refused", "reason": str(exc) if isinstance(exc, Refused) else "required_input_file_missing"}
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
