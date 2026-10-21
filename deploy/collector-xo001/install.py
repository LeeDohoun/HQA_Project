"""Coordinator-only, offline installation. Never imports/loads credential files."""
import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from backtesting.experiments.xo001.activation import fingerprint
from backtesting.experiments.xo001.storage import read, safe_root, sha

ROOT = Path("/opt/hqa-xo001")
DATA = Path("/var/lib/hqa-xo001")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    inventory = fingerprint(SOURCE)
    if not args.execute:
        print(json.dumps({"status": "dry_run", "code_root": str(ROOT), "data_root": str(DATA),
                          "files": len(inventory["files"]), "network": False}))
        return 0
    if os.geteuid() != 0 or SOURCE == ROOT or ROOT.exists():
        raise ValueError("root coordinator must install a fresh /opt/hqa-xo001; existing roots are never replaced")
    wheelhouse = args.wheelhouse.resolve(strict=True)
    safe_root(DATA)
    uid, gid = pwd.getpwnam("hqa").pw_uid, grp.getgrnam("hqa").gr_gid
    ROOT.mkdir(mode=0o755)
    for relative, digest in inventory["files"].items():
        source = SOURCE / relative
        if sha(read(source)) != digest or source.is_symlink():
            raise ValueError("source changed during private XO001 install")
        target = ROOT / relative
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755 if target.suffix == ".sh" else 0o644)
    subprocess.run([sys.executable, "-m", "venv", str(ROOT / "venv")], check=True)
    subprocess.run([str(ROOT / "venv/bin/python"), "-m", "pip", "install", "--no-index", "--only-binary=:all:",
        "--find-links", str(wheelhouse), "-r", str(ROOT / "deploy/collector-xo001/dependencies.lock")], check=True)
    DATA.mkdir(mode=0o750, exist_ok=True)
    os.chown(DATA, uid, gid)
    control = DATA / "activation-control"
    control.mkdir(mode=0o750, exist_ok=True)
    os.chown(control, 0, gid)
    control.chmod(0o750)
    inputs = DATA / "operator_input"
    inputs.mkdir(mode=0o750, exist_ok=True)
    os.chown(inputs, 0, gid)
    inputs.chmod(0o750)
    # Only our six exact unit filenames are installed. No activation or timer start.
    units = ROOT / "deploy/collector-xo001/systemd"
    expected = {f"hqa-xo001-{phase}.{suffix}" for phase in ("morning", "cleanup", "reconcile") for suffix in ("service", "timer")}
    if {p.name for p in units.iterdir()} != expected:
        raise ValueError("unexpected XO001 systemd inventory")
    for name in sorted(expected):
        target = Path("/etc/systemd/system") / name
        if target.exists():
            raise ValueError("existing XO001 unit requires a reviewed deployment change")
        shutil.copyfile(units / name, target)
        target.chmod(0o644)
    deployed = subprocess.check_output([str(ROOT / "venv/bin/python"), "-B", "-c",
        "import json; from backtesting.experiments.xo001.activation import fingerprint,environment; "
        "print(json.dumps({**fingerprint(), **environment()}))"], cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1"})
    (ROOT / "deploy/collector-xo001/manifest.json").write_bytes(deployed)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print(json.dumps({"status": "installed_timers_disabled", "code_sha256": inventory["code_sha256"],
                      "credential_file": "/etc/hqa/xo001.env; provision separately root:root 0600"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
