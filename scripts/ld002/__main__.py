"""python -B -m scripts.ld002 <command> [--execute]; defaults to a dry plan."""
import argparse
import json
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.ld002 import END, HEAVY_LOCK, START, Refused
from backtesting.experiments.ld002 import activation, data, inputs
from backtesting.experiments.ld002.evaluate import publish_evaluation
from backtesting.experiments.ld002.execution import apply_status, close_input, prior_adv, index_closes
from backtesting.experiments.ld002.runner import LlmRunner, check_committed
from backtesting.experiments.ld002.storage import aware, digest, file_hash, heavy_lock, lock, now, verified
from backtesting.experiments.ld002.workflow import ForwardRunner, book_name


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", default=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("input-seal", "decide", "ledger-finalize", "evaluate", "activation-check", "seal-holdout", "distribute", "amend"):
        sub = commands.add_parser(command)
        sub.add_argument("--state-dir", type=Path, default=Path("/var/lib/hqa-ld002"))
        mode = sub.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", default=True)
        mode.add_argument("--execute", action="store_true")
        sub.add_argument("--day")
        sub.add_argument("--distribution-dir", type=Path)
        sub.add_argument("--heavy-lock", type=Path, default=HEAVY_LOCK)
        if command in ("seal-holdout", "distribute"):
            sub.add_argument("--source-dir", type=Path, required=True)
        if command == "distribute":
            sub.add_argument("--sync-manifest", type=Path, required=True)
        if command == "input-seal":
            sub.add_argument("--fw001-dir", type=Path, required=True)
        if command == "activation-check":
            sub.add_argument("--evidence", type=Path)
        if command == "ledger-finalize":
            sub.add_argument("--status-file", type=Path, required=True)
            sub.add_argument("--audit-correction", action="store_true")
        if command == "amend":
            sub.add_argument("--path", required=True)
            sub.add_argument("--legal-source", required=True)
            sub.add_argument("--effective-date", required=True)
            sub.add_argument("--changed-at", required=True)
    return parser


def next_finalization_day(workflow, at, requested=None):
    with lock(workflow.root / ".state.lock"):
        workflow.sync_model_stop()
    if requested:
        return requested
    completed = sorted((workflow.root / "ledgers").glob("*.json"))
    last = workflow.calendar.previous(aware(at).date().isoformat())
    for candidate in completed:
        marker = candidate.with_suffix(".publication") / "complete.json"
        acknowledgement = workflow.root / "ledger_finalizations" / candidate.name
        if not marker.exists() or not acknowledgement.exists():
            return candidate.stem if candidate.stem <= min(last, END) else None
    if completed and completed[-1].stem >= END:
        return None
    first = workflow.calendar.next(completed[-1].stem) if completed else START
    return first if first <= min(last, END) else None


def run(args):
    if not args.execute:
        return {"dry_run": True, "command": args.command, "state_dir": str(args.state_dir), "day": args.day,
                "window": [START, END], "input_deadline": "10:00 Asia/Seoul", "decision_deadline": "12:00 Asia/Seoul",
                "effects": "none; no archive, holdout session, model or service opened"}
    workflow = ForwardRunner(args.state_dir)
    at = now()
    day = (next_finalization_day(workflow, at, args.day) if args.command == "ledger-finalize" else args.day or at.date().isoformat())
    if args.command in ("input-seal", "decide", "ledger-finalize") and (day is None or not START <= day <= END):
        return {"status": "no_pending_session", "day": day}
    directory = args.distribution_dir or args.state_dir / "distributions"
    if args.command == "activation-check":
        evidence = json.loads(args.evidence.read_text()) if args.evidence else None
        return activation.activate(args.state_dir, evidence, repo_root=PROJECT_ROOT)
    if args.command == "seal-holdout":
        check_committed()
        with heavy_lock(args.heavy_lock):
            return data.seal_holdout(args.source_dir, args.state_dir, repo_root=PROJECT_ROOT)
    if args.command == "amend":
        from backtesting.experiments.lh003.pins import record_amendment
        return record_amendment(args.state_dir, PROJECT_ROOT, path=args.path, legal_source=args.legal_source,
            effective_date=args.effective_date, changed_at=args.changed_at)
    if args.command == "distribute":
        check_committed()
        applied = data.code_pins(args.state_dir, PROJECT_ROOT, at=at)["implementation_hashes"]
        with heavy_lock(args.heavy_lock):
            return data.distribute(args.source_dir, directory, args.sync_manifest, code_hashes=applied,
                state_root=args.state_dir)
    if args.command == "evaluate":
        return publish_evaluation(workflow)
    if args.command == "decide":
        start = workflow.start()
        return workflow.decide(day, LlmRunner(args.state_dir, start["model_identity"]))
    if args.command == "input-seal":
        def builder(previous):
            holdout = verified(args.state_dir / "holdout.json")
            distribution = data.Distribution(directory, at=now(), code_hashes=workflow.code()["implementation_hashes"])
            snapshot = data.Snapshot(holdout["snapshot_path"], expected_hash=holdout["sha256"], distribution=distribution)
            ledger_paths = sorted((args.state_dir / "ledgers").glob("*.json"))[-20:]
            navs = [{"day": p.stem, "nav": verified(p)["books"][book_name("ld", 1.0)]["state"]["nav"]} for p in ledger_paths]
            decision_days = workflow.calendar.sessions(START, workflow.calendar.previous(day))[-5:] if day != START else []
            memos = [{"day": d, "memo": (workflow.decision(d) or {}).get("output", {}).get("memo")
                      if (workflow.decision(d) or {}).get("output") else None} for d in decision_days]
            last = verified(ledger_paths[-1])["books"][book_name("ld", 1.0)]["trades"] if ledger_paths else []
            av = inputs.latest_av(args.fw001_dir, as_of=workflow.calendar.previous(day), at=now())
            result = inputs.prepare(snapshot, day, previous, workflow.calendar, at=now(),
                disclosure_enabled=workflow.start()["disclosure_enabled"], av=av, nav_history=navs, memos=memos, last_trades=last)
            result.update(distribution_sha256=digest(distribution.manifest), holdout_sha256=holdout["sha256"])
            return result
        return workflow.input_seal(day, builder, heavy_path=args.heavy_lock)
    if args.command == "ledger-finalize":
        status_path = Path(str(args.status_file).format(day=day, stamp=day.replace("-", "")))
        distribution = data.Distribution(directory, at=at, code_hashes=workflow.code()["implementation_hashes"])
        quotes, required, hashes = close_input(distribution, day, at=at, calendar=workflow.calendar)
        status_sha256 = file_hash(status_path)
        status = json.loads(status_path.read_text())
        if file_hash(status_path) != status_sha256:
            raise Refused("official status/action sidecar changed while reading")
        quotes, actions = apply_status(quotes, status, day)
        with lock(args.state_dir / ".state.lock"):
            workflow.sync_model_stop()
        liquidation_adv, adv_hashes = (prior_adv(distribution, day, workflow.calendar)
            if workflow.termination() and workflow.input(day) is None else (None, {}))
        indices, index_hashes = index_closes(distribution, day)
        return workflow.finalize(day, quotes, required_files=required, actions=actions, audit=args.audit_correction,
            liquidation_adv20=liquidation_adv, indices=indices,
            execution_sources={**hashes, **adv_hashes, **index_hashes, str(status_path): status_sha256, "distribution_manifest": digest(distribution.manifest)})
    raise Refused("unhandled command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        value = run(args)
        # Daily book artifacts remain on disk; terminal output is bounded.
        if isinstance(value, dict) and "books" in value:
            value = {"day": value["day"], "books": len(value["books"]), "sha256": digest(value), "termination": value.get("termination")}
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0
    except BlockingIOError:
        print(json.dumps({"status": "heavy_or_state_lock_busy"}))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
