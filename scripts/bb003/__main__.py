"""BB003 local plans, explicit recording, count-only reports and PC evaluation."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "record", "post-entry", "capture", "interim", "evaluate",
                                           "policy-init", "policy-amend", "rehearse", "publish-trial", "recover-evaluation"))
    parser.add_argument("--source-data", type=Path, default=Path("/var/lib/hqa/data"))
    parser.add_argument("--output-root", type=Path, default=Path("/var/lib/hqa/data"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-date", default="2025-07-01")
    parser.add_argument("--to-date", default="2025-12-29")
    parser.add_argument("--synthetic", action="store_true", help="rehearsal fixture, never a forward observation")
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--notices", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--registry-root", type=Path, default=Path.cwd(), help="PC checkout with authoritative registry; never the shared main checkout")
    args = parser.parse_args(argv)
    if args.execute and args.dry_run:
        parser.error("choose --execute or --dry-run")
    from backtesting.experiments.bb003.deployment import CODE_ROOT, operational_lock, operation
    from backtesting.experiments.bb003.storage import Store
    if args.command == "rehearse" and args.execute:
        from backtesting.experiments.bb003.rehearsal import rehearse
        result = rehearse(args.source_data, args.output_root, start=args.from_date, end=args.to_date, synthetic=args.synthetic)
    elif args.command == "plan" or not args.execute and args.command != "interim":
        result = {"status": "dry_run", "command": args.command, "source_read_only": str(args.source_data),
            "write_root": str(Store(args.output_root).root), "code_root": "/opt/hqa-bb003",
            "receipt_window": ["2026-12-01", "2028-09-30"], "activation_by": "2026-11-30T23:59:59+09:00",
            "decision_deadline": "next trading day, strictly before 08:59 KST, including 1s precision bound",
            "phases": ["night listings, reserved fetches, digits/rules and immutable nineteen-session preparation",
                       "08:03-08:55 minute admission, previous-session KRX and final decision seal",
                       "evening entry executability and fixed matching seal",
                       "daily vintage acquisition through V", "single PC evaluation after calendar-derived F"],
            "heavy_lock": "/run/hqa-heavy/run.lock", "network": "night record --execute only; DART only; never KRX",
            "rehearsal": {"from": args.from_date, "to": args.to_date, "synthetic": args.synthetic},
            "calendar_tax": "unverified required dates stop; data-only official-source amendments"}
    else:
        needs_lock = args.command in {"record", "post-entry", "capture", "policy-init", "policy-amend"} or (
            args.command == "interim" and Path(__file__).resolve().parents[2] == CODE_ROOT)
        phase = "seal" if args.command == "record" and Store(args.output_root).clock().hour == 8 else "prepare"
        if needs_lock and Path(__file__).resolve().parents[2] == CODE_ROOT and args.command in {"record", "post-entry", "capture"}:
            raise ValueError("Installed BB003 workers must use the root admission dispatcher: systemctl start hqa-bb003-check.service")
        context = operational_lock(phase=phase, source=args.source_data,
            state=None if args.command.startswith("policy-") else Store(args.output_root).root) if needs_lock else nullcontext()
        with context as guard:
            store = Store(args.output_root)
            store.morning_guard = guard
            mode = "setup" if args.command.startswith("policy-") else "evaluation" if args.command in {
                "evaluate", "publish-trial", "recover-evaluation"} else "pc" if args.command == "interim" else "record"
            with operation(store, mode=mode, name=args.command):
                from backtesting.experiments.bb003 import policy
                from backtesting.experiments.bb003 import recorder
                from backtesting.experiments.bb003.evaluate import evaluate, interim
                if args.command == "interim":
                    result = interim(store)
                else:
                    with store.lock():
                        if args.command in {"policy-init", "policy-amend"}:
                            if not args.policy_file or not args.notices or not args.reason:
                                parser.error(args.command + " needs --policy-file, --notices and --reason")
                            if (args.command == "policy-init") == bool(store.keys("policy")):
                                parser.error("policy-init requires an empty chain; policy-amend requires its baseline")
                            from backtesting.experiments.bb003.inputs import read_bytes
                            data = json.loads(read_bytes(args.policy_file))
                            notices = {s["sha256"]: read_bytes(args.notices / s["sha256"]) for s in data["sources"]}
                            result = policy.append(store, data, notices=notices, reason=args.reason)
                        elif args.command == "evaluate":
                            # Validate registration before claiming the sole evaluation.
                            from backtesting import experiment_registry
                            os.environ["GIT_OPTIONAL_LOCKS"] = "0"
                            experiment_registry.verify_preregistration("BB003_buyback_forward", args.registry_root)
                            result = evaluate(store, policy.latest(store))
                            from backtesting.experiments.bb003.publish import publish_trial
                            result["registry_export"] = publish_trial(store, args.registry_root)
                        elif args.command == "publish-trial":
                            from backtesting.experiments.bb003.publish import publish_trial
                            result = publish_trial(store, args.registry_root)
                        elif args.command == "recover-evaluation":
                            from backtesting.experiments.bb003.evaluate import recover_evaluation
                            from backtesting.experiments.bb003.publish import publish_trial
                            result = recover_evaluation(store)
                            result["registry_export"] = publish_trial(store, args.registry_root)
                        else:
                            if recorder.activation_status(store) != "active":
                                raise ValueError("BB003 is not activated by its fixed cutoff")
                            pinned = policy.latest(store)
                            if args.command == "record":
                                if phase == "seal":
                                    result = recorder.seal_day(store, args.source_data, pinned)
                                else:
                                    from backtesting.experiments.bb003.fetch import HttpClient
                                    result = recorder.prepare_day(store, args.source_data, pinned, HttpClient(os.environ.get("DART_API_KEY")))
                            elif args.command == "post-entry":
                                result = recorder.post_entry(store, args.source_data, pinned)
                            else:
                                result = recorder.capture_prices(store, args.source_data, pinned)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
