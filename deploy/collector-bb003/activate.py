"""Verify and seal BB003 activation, then enable only the BB003 timer."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtesting.experiments.bb003.deployment import DATA_ROOT, MANIFEST, fw001_contract, operational_lock, verify_operation
from backtesting.experiments.bb003.storage import Store, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    command = ["systemctl", "enable", "--now", "hqa-bb003-daily.timer"]
    with operational_lock():
        store = Store(DATA_ROOT)
        verify_operation(store, mode="activate")
        manifest = json.loads((ROOT / MANIFEST).read_bytes())
        contract = fw001_contract()
        print(json.dumps({"mode": "execute" if args.execute else "dry_run", "commit": manifest["commit"],
            "manifest_sha256": sha((ROOT / MANIFEST).read_bytes()), "activation": str(DATA_ROOT / "forward/bb003/activation.json"),
            "fw001_contract": contract, "enable": command}), flush=True)
        if args.execute:
            if os.geteuid() != 0:
                raise ValueError("activation execution requires root")
            from backtesting.experiments.bb003.recorder import activate
            from backtesting.experiments.bb003.policy import latest
            with store.lock():
                policy = latest(store)
                policy.entry_schedule("2026-12-01")
                subprocess.check_call(["systemd-analyze", "verify", *map(str,
                    sorted((ROOT / "deploy/collector-bb003/systemd").glob("hqa-bb003-*")))])
                raw = (ROOT / MANIFEST).read_bytes()
                activate(store, commit=manifest["commit"], manifest_hash=sha(raw), manifest_raw=raw)
            # Activation artifacts are subsequently maintained by the service account.
            import pwd
            account = pwd.getpwnam("hqa")
            for path in store.root.rglob("*"):
                if not path.is_symlink():
                    os.chown(path, account.pw_uid, account.pw_gid)
            subprocess.check_call(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
