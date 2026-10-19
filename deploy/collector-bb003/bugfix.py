"""Plan/record a reviewed bugfix transition; does not deploy or edit code."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtesting.experiments.bb003.amendments import record_bugfix
from backtesting.experiments.bb003.deployment import DATA_ROOT, operational_lock, verify_operation
from backtesting.experiments.bb003.inputs import read_bytes
from backtesting.experiments.bb003.storage import Store, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    with operational_lock():
        store = Store(DATA_ROOT)
        verify_operation(store, mode="bugfix")
        before, after = read_bytes(args.before), read_bytes(args.after)
        print(json.dumps({"before_sha256": sha(before), "after_sha256": sha(after), "commit": args.commit,
                          "reason": args.reason, "mode": "execute" if args.execute else "dry_run"}))
        if args.execute:
            with store.lock():
                record_bugfix(store, before, after, reason=args.reason, commit=args.commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
