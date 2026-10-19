"""Offline separate-root installer, dry-run by default; never activates units."""
import argparse
import json
import math
import os
from pathlib import Path
import pwd
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtesting.experiments.bb003.deployment import CODE_ROOT, DATA_ROOT, MANIFEST, PREREG, PREREG_COMMIT, PREREG_SHA256, fw001_contract, inventory
from backtesting.experiments.bb003.storage import encoded, load, put, sha
from backtesting.experiments.bb003.admission import SEAL_BUDGET_SECONDS, SEAL_MARGIN_SECONDS

ARTIFACTS = "deploy/collector-bb003"


def run(command, **kwargs):
    return subprocess.check_output(command, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1", "PIP_NO_INDEX": "1",
        "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}, **kwargs)


def candidate(repo, commit):
    commit = run(["git", "-C", str(repo), "rev-parse", "--verify", commit + "^{commit}"]).decode().strip()
    entries = run(["git", "-C", str(repo), "ls-tree", "-rz", commit]).decode().split("\0")
    modes = {r.split("\t", 1)[1]: r.split(" ", 1)[0] for r in entries if r}

    def read(name):
        if modes.get(name) not in {"100644", "100755"} or any(p.startswith(".env") for p in Path(name).parts):
            raise ValueError("BB003 inventory needs regular committed sources")
        return run(["git", "-C", str(repo), "show", commit + ":" + name])

    names = inventory(repo, names=modes, read=lambda name: read(name).decode())
    files = {name: read(name) for name in names}
    if sha(files[PREREG]) != PREREG_SHA256:
        raise ValueError("BB003 original preregistration hash differs")
    return commit, files


def measurement(path, files):
    result = load(path)
    report_raw = (path.parent / "measurement.json").read_bytes()
    expected = {name: sha(raw) for name, raw in files.items()}
    if (not result.get("two_phase") or result["synthetic"] or not result["successful"] or not result["full_seal_and_post_entry"]
            or result["measurement_sha256"] != sha(report_raw) or result["source_files"] != expected):
        raise ValueError("BB003 requires successful real-archive full-pipeline measurement on the final source inventory")
    budget = 60 * math.ceil((2 * result["process_wall_seconds"] + 120) / 60)
    if budget > 600 or result["process_peak_rss_mib"] >= 600:
        raise ValueError("BB003 measured budget exceeds 600 seconds / 600 MiB; review and remeasure")
    if budget > SEAL_BUDGET_SECONDS:
        raise ValueError("BB003 source-bound conservative measurement exceeds the morning seal cap; review and remeasure on server")
    return result, budget


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--measurement-report", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    commit, files = candidate(args.repo, args.commit)
    evidence, budget = measurement(args.measurement_report, files)
    contract = fw001_contract(run)
    units = sorted(Path(n).name for n in files if n.startswith(ARTIFACTS + "/systemd/"))
    state = DATA_ROOT / "forward/bb003"
    destinations = [CODE_ROOT, state, *(Path("/etc/systemd/system") / n for n in units)]
    if any(p.exists() or p.is_symlink() for p in destinations):
        raise ValueError("BB003 refuses existing code/state/unit destinations")
    print(json.dumps({"mode": "execute" if args.execute else "dry_run", "commit": commit,
        "code_root": str(CODE_ROOT), "write_root": str(state), "fw001_contract": contract,
        "units": units, "budget_seconds": budget, "files": {n: sha(r) for n, r in files.items()},
        "activation": "separate explicit activate.py; installer never enables or starts units"}))
    if not args.execute:
        return 0
    if os.geteuid() != 0 or not args.wheelhouse.is_dir():
        raise ValueError("execute requires root and a local offline wheelhouse")
    CODE_ROOT.mkdir(mode=0o755)
    for name, raw in files.items():
        put(CODE_ROOT / name, raw)
    run([sys.executable, "-I", "-B", "-m", "venv", str(CODE_ROOT / "venv")])
    python = str(CODE_ROOT / "venv/bin/python")
    run([python, "-I", "-B", "-m", "pip", "install", "--no-index", "--no-cache-dir", "--no-compile",
         "--find-links", str(args.wheelhouse.resolve()), "-r", str(CODE_ROOT / ARTIFACTS / "dependencies.lock")])
    environment = json.loads(run([python, "-I", "-B", str(CODE_ROOT / ARTIFACTS / "environment.py")]))
    # Bind the complete private environment, copied import closure and source measurement.
    manifest = {"experiment_id": "BB003_buyback_forward", "commit": commit, "code_root": str(CODE_ROOT),
        "preregistration_commit": PREREG_COMMIT, "preregistration_sha256": PREREG_SHA256,
        "data_root": str(DATA_ROOT), "files": {n: sha(r) for n, r in files.items()},
        "environment_sha256": sha(encoded(environment)), "measurement": evidence, "budget_seconds": budget,
        "seal_budget_seconds": SEAL_BUDGET_SECONDS, "seal_margin_seconds": SEAL_MARGIN_SECONDS}
    put(CODE_ROOT / MANIFEST, encoded(manifest))
    for path in CODE_ROOT.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    state.mkdir(parents=True, mode=0o750)
    account = pwd.getpwnam("hqa")
    os.chown(state, account.pw_uid, account.pw_gid)
    for unit in units:
        put(Path("/etc/systemd/system") / unit, files[ARTIFACTS + "/systemd/" + unit])
        (Path("/etc/systemd/system") / unit).chmod(0o644)
    run(["systemctl", "--no-reload", "disable", *units])
    run(["systemctl", "daemon-reload"])
    fw001_contract(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
