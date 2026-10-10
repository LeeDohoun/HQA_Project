"""Research operations exercised with local fixtures and injected commands only."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "deploy/research/setup_research.sh",
    "scripts/ops/research_run.sh",
    "scripts/ops/sync_research_data.sh",
    "scripts/ops/pull_research_results.sh",
)
REGISTRY = "research/experiments/registry.csv"
LEDGER = "research/experiments/holdout_ledger.jsonl"
RESULT = "research/experiments/HC003/results/summary.json"
DATA_PATHS = (
    "market/krx_daily", "fundamentals", "reference", "disclosures/dart_full/list",
    "market_context", "research/lh001", "research/lh002",
    "disclosures/dart_buyback/structured", "disclosures/dart_buyback/documents",
    "disclosures/business_text",
)


# Absolute interpreter: injecting python3 must not recurse through this fake.
FAKE_COMMAND = r'''
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["TEST_COMMAND_LOG"], "a") as handle:
    handle.write(json.dumps([name, args]) + "\n")
root = Path(os.environ["TEST_CHECKOUT"])
if name == "id":
    if "-Gn" in args:
        print(os.environ.get("TEST_GROUPS", "hqares"))
    elif "hqares" in args:
        if not (root.parent / "account-created").exists() and os.environ.get("TEST_USER_EXISTS") != "1":
            sys.exit(1)
        print("999")
    else:
        print(os.environ.get("TEST_UID", "0"))
elif name == "getent":
    exists = (root.parent / "account-created").exists() or os.environ.get("TEST_USER_EXISTS") == "1"
    if args[0] == "group":
        exists = exists or (root.parent / "group-created").exists()
    if not exists:
        sys.exit(2)
    if args[0] == "group":
        print("hqares:x:999:")
    else:
        print(f"hqares:x:999:999::{root}:/usr/sbin/nologin")
elif name in ("useradd", "groupadd"):
    (root.parent / ("account-created" if name == "useradd" else "group-created")).touch()
elif name == "runuser":
    command = args[args.index("--") + 1:]
    child_env = dict(os.environ)
    if command[:2] == ["env", "-i"]:
        command = command[2:]
        child_env = {k: v for k, v in os.environ.items() if k.startswith("TEST_")}
        while command and "=" in command[0]:
            key, value = command.pop(0).split("=", 1)
            child_env[key] = value
        child_env["PATH"] = os.environ["PATH"]
    sys.exit(subprocess.run(command, env=child_env).returncode)
elif name == "mountpoint":
    sys.exit(0 if os.environ.get("TEST_MOUNTED", "1") == "1" else 1)
elif name == "findmnt":
    print("8:1" if "--target" in args else os.environ.get("TEST_VOLUME_DEVICE", "8:2"))
elif name == "df":
    print("Avail\n" + os.environ.get("TEST_FREE_BYTES", "20000000000"))
elif name == "git":
    if "clone" in args:
        (root / ".git/info").mkdir(parents=True)
        (root / ".git/hooks").mkdir()
        (root / ".git/info/exclude").touch()
    elif "status" in args:
        dirty = json.loads(os.environ.get("TEST_DIRTY", "[]"))
        if "--" in args:
            exclusions = args[args.index("--") + 1:]
            def excluded(path):
                if ":(exclude)data" in exclusions and (path == "data" or path.startswith("data/")):
                    return True
                for spec in exclusions:
                    if spec.startswith(":(glob,exclude)") and fnmatch.fnmatchcase(path, spec.removeprefix(":(glob,exclude)")):
                        return True
                    if spec.startswith(":(exclude)") and path == spec.removeprefix(":(exclude)"):
                        return True
                return False
            dirty = [path for path in dirty if not excluded(path)]
        for path in dirty:
            print(" M " + path)
    elif "config" in args:
        print(os.environ.get("TEST_ORIGIN", "https://github.com/LeeDohoun/HQA_Project.git"))
    elif "branch" in args:
        print(os.environ.get("TEST_BRANCH", "claude/strategy-research-system"))
    elif "ls-files" in args:
        print(os.environ.get("TEST_TRACKED_STAGE", ""), end="")
    elif "merge" in args and os.environ.get("TEST_FF_FAIL") == "1":
        sys.exit("Not possible to fast-forward")
    elif not any(command in args for command in ("sparse-checkout", "checkout", "fetch", "merge", "remote")):
        sys.exit("Unexpected git command")
elif name == "install":
    if "-d" in args:
        for path in args:
            if path.startswith("/"):
                Path(path).mkdir(parents=True, exist_ok=True)
    else:
        Path(args[-1]).write_bytes(b"")
elif name in ("apt-get", "chown", "chmod"):
    pass
elif name in ("python3", "python"):
    if args[:2] == ["-m", "venv"]:
        binary = Path(args[2]) / "bin/python"
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sys.argv[0], binary)
        binary.chmod(0o755)
    elif args[:2] != ["-m", "pip"] and args[:1] != ["-c"]:
        sys.exit("Unexpected Python command")
elif name == "free":
    print("              total used free shared buff/cache available")
    print("Mem: " + os.environ.get("TEST_TOTAL_BYTES", str(8 * 1024**3)) + " 0 0 0 0 0")
elif name == "nproc":
    print(os.environ.get("TEST_CORES", "4"))
elif name == "systemctl":
    units = json.loads(os.environ.get("TEST_UNITS", "[]"))
    if args[0] == "list-units":
        active_only = any(arg.startswith("--state=") for arg in args)
        for unit in units:
            if not active_only or unit["substate"] in ("running", "activating", "deactivating"):
                print(f'{unit["name"]} loaded {unit["state"]} {unit["substate"]} Research')
    elif args[0] == "show":
        unit = next(value for value in units if value["name"] == args[1])
        print(f'Id={unit["name"]}\nActiveState={unit["state"]}\nSubState={unit["substate"]}')
        print(f'Result={unit["result"]}\nExecMainCode=1\nExecMainStatus={unit["exit"]}')
    else:
        sys.exit("Unexpected systemctl command")
elif name == "systemd-run":
    pass
elif name == "rsync":
    remote = os.environ.get("TEST_REMOTE_ROOT")
    if remote:
        translated, index = [], 0
        while index < len(args):
            arg = args[index]
            if arg == "-e":
                index += 2
                continue
            if arg.startswith("--rsync-path="):
                index += 1
                continue
            if ":" in arg and arg.split(":", 1)[1].startswith("/srv/hqa-research/"):
                arg = remote + arg.split(":", 1)[1].removeprefix("/srv/hqa-research")
            translated.append(arg)
            index += 1
        sys.exit(subprocess.run(["/usr/bin/rsync", *translated]).returncode)
else:
    sys.exit("Unexpected command " + name)
'''


def write(path, content=b"fixture\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def ops(tmp_path):
    project = tmp_path / "PC project with spaces"
    server = tmp_path / "server"
    volume = tmp_path / "volume"
    commands = tmp_path / "commands"
    commands.mkdir()
    volume.mkdir()
    for relative in SCRIPTS:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / relative).read_text().replace("/srv/hqa-research", str(server)) if "setup_" in relative or "research_run" in relative else (ROOT / relative).read_text()
        target.write_text(text)
    env = {**os.environ, "PATH": f"{commands}:/usr/bin:/bin",
           "TEST_COMMAND_LOG": str(tmp_path / "commands.jsonl"),
           "TEST_CHECKOUT": str(server), "PYTHONDONTWRITEBYTECODE": "1"}

    def inject(*names):
        for name in names:
            command = commands / name
            command.write_text(f"#!{sys.executable}\n" + FAKE_COMMAND)
            command.chmod(0o755)

    inject("git", "rsync")

    def run(relative, *args, **settings):
        return subprocess.run(["bash", str(project / relative), *map(str, args)],
                              env={**env, **settings}, capture_output=True, text=True, timeout=15)

    def recorded(name):
        path = Path(env["TEST_COMMAND_LOG"])
        return [args for command, args in (json.loads(line) for line in path.read_text().splitlines()) if command == name] if path.exists() else []

    return project, server, volume, env, inject, run, recorded


def server_commands(ops):
    ops[4]("id", "getent", "runuser", "mountpoint", "findmnt", "df", "apt-get",
           "groupadd", "useradd", "install", "chown", "chmod", "python3",
           "free", "nproc", "systemctl", "systemd-run")


def checkout(ops):
    server = ops[1]
    (server / ".git/info").mkdir(parents=True)
    (server / ".git/hooks").mkdir()
    (server / ".git/info/exclude").touch()
    (server / "runs").mkdir()
    data = ops[2] / "hqa-research-data"
    data.mkdir()
    (server / "data").symlink_to(data, target_is_directory=True)
    python = write(server / "venv/bin/python", b"#!/usr/bin/env bash\nexit 0\n")
    python.chmod(0o755)
    ops[3]["TEST_USER_EXISTS"] = "1"
    return server


def test_setup_defaults_to_read_only_and_plans_full_history_clone(ops):
    server_commands(ops)
    result = ops[5](SCRIPTS[0], ops[2])
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout and "--no-checkout" in result.stdout
    assert "--depth" not in result.stdout and "codex login --device-auth" in result.stdout
    assert not ops[1].exists() and not list(ops[2].iterdir())
    assert not ops[6]("apt-get") and not ops[6]("git") and not ops[6]("useradd")


@pytest.mark.parametrize("settings,message", [
    ({"TEST_MOUNTED": "0"}, "Not a separate mount"),
    ({"TEST_VOLUME_DEVICE": "8:1"}, "separate from /"),
    ({"TEST_FREE_BYTES": "19999999999"}, "20 GB"),
    ({"TEST_FREE_BYTES": "invalid"}, "measure volume free space"),
    ({"TEST_UID": "1000"}, "Run as root"),
])
def test_setup_mount_space_and_root_preflight_before_mutations(ops, settings, message):
    server_commands(ops)
    result = ops[5](SCRIPTS[0], ops[2], "--execute", **settings)
    assert result.returncode != 0 and message in result.stderr
    assert not ops[6]("apt-get") and not ops[1].exists()


@pytest.mark.parametrize("dirty", ["backtesting/experiments/hc003.py", REGISTRY, RESULT])
def test_setup_refuses_any_dirty_checkout_before_fetch(ops, dirty):
    server_commands(ops)
    checkout(ops)
    result = ops[5](SCRIPTS[0], ops[2], "--execute", TEST_DIRTY=json.dumps([dirty]))
    assert result.returncode != 0 and "dirty" in result.stderr
    assert not ops[6]("apt-get") and not any("fetch" in args for args in ops[6]("git"))


def test_setup_is_idempotent_with_isolated_account_sparse_data_and_disabled_push(ops):
    server_commands(ops)
    first = ops[5](SCRIPTS[0], ops[2], "--execute")
    assert first.returncode == 0, first.stderr
    second = ops[5](SCRIPTS[0], ops[2], "--execute")
    assert second.returncode == 0, second.stderr
    assert len(ops[6]("useradd")) == 1
    assert "--system" in ops[6]("useradd")[0] and "/usr/sbin/nologin" in ops[6]("useradd")[0]
    git = ops[6]("git")
    assert sum("clone" in args for args in git) == 1
    assert any(args[-3:] == ["--no-cone", "/*", "!/data/"] for args in git)
    assert any("merge" in args and "--ff-only" in args for args in git)
    assert any(args[-4:] == ["set-url", "--push", "origin", "DISABLED"] for args in git)
    assert (ops[1] / "data").resolve() == ops[2] / "hqa-research-data"
    assert (ops[1] / ".git/info/exclude").read_text().splitlines().count("/runs/") == 1
    assert "/data" in (ops[1] / ".git/info/exclude").read_text().splitlines()
    assert "exit 1" in (ops[1] / ".git/hooks/pre-push").read_text()
    assert not any("sudo" in args for args in ops[6]("useradd"))


def test_sparse_checkout_and_local_exclusions_keep_volume_link_clean_in_real_git(ops):
    server_commands(ops)
    server = ops[1]
    # Local shared clone is read-only with respect to ROOT: no network, add or
    # commit. Checkout only .gitignore; historical data stays sparse.
    def git(*args):
        return subprocess.run(["/usr/bin/git", *map(str, args)], check=True,
                              capture_output=True, text=True).stdout

    git("clone", "--shared", "--no-checkout", "--single-branch", ROOT, server)
    git("-C", server, "sparse-checkout", "set", "--no-cone", "/.gitignore", "!/data/")
    git("-C", server, "checkout", "HEAD")
    ops[3]["TEST_USER_EXISTS"] = "1"
    result = ops[5](SCRIPTS[0], ops[2], "--execute")
    assert result.returncode == 0, result.stderr
    assert (server / "data").is_symlink()
    assert git("-C", server, "status", "--porcelain=v1", "--untracked-files=all") == ""


@pytest.mark.parametrize("settings,message", [
    ({"TEST_ORIGIN": "https://example.invalid/repo"}, "Unexpected research origin"),
    ({"TEST_BRANCH": "main"}, "Unexpected research branch"),
    ({"TEST_GROUPS": "hqares sudo"}, "no sudo"),
    ({"TEST_FF_FAIL": "1"}, "fast-forward"),
])
def test_setup_refuses_foreign_checkout_privileges_or_non_fast_forward(ops, settings, message):
    server_commands(ops)
    checkout(ops)
    result = ops[5](SCRIPTS[0], ops[2], "--execute", **settings)
    assert result.returncode != 0 and message in result.stderr


@pytest.mark.parametrize("total,cores", [(8 * 1024**3, 4), (16 * 1024**3, 8), (2 * 1024**3, 2)])
def test_run_computes_limits_and_detaches_with_account_logs_and_offline_environment(ops, total, cores):
    server_commands(ops)
    server = checkout(ops)
    result = ops[5](SCRIPTS[1], "lh001", "screening", "--experiment", "LH002",
                    TEST_TOTAL_BYTES=str(total), TEST_CORES=str(cores))
    assert result.returncode == 0, result.stderr
    args, = ops[6]("systemd-run")
    assert f"MemoryMax={total - 600 * 1024**2}" in args
    assert f"CPUQuota={(cores - 1) * 100}%" in args
    assert "User=hqares" in args and "Group=hqares" in args
    assert "RemainAfterExit=yes" in args and "--service-type=exec" in args
    assert not set(("--wait", "--pipe", "--collect")) & set(args)
    unit = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--unit="))
    assert unit.startswith("hqa-research-lh001-")
    log = server / "runs" / (unit + ".log")
    assert log.is_file() and f"StandardOutput=append:{log}" in args and f"StandardError=append:{log}" in args
    assert args[args.index("/usr/bin/env") + 1] == "-i"
    for value in ("OPENAI_API_KEY=offline-disabled", "OPENAI_BASE_URL=http://127.0.0.1:9/v1",
                  "LANGCHAIN_TRACING_V2=false", "LANGSMITH_TRACING=false",
                  f"HOME={server}", f"HQA_DATA_DIR={server}/data"):
        assert value in args
    assert args[-7:] == [str(server / "venv/bin/python"), "-m", "backtesting.experiments.lh001",
                        "--execute", "screening", "--experiment", "LH002"]
    assert "tail -f" in result.stdout


@pytest.mark.parametrize("settings,message", [
    ({"TEST_TOTAL_BYTES": str(600 * 1024**2)}, "600 MiB"),
    ({"TEST_CORES": "1"}, "two CPU cores"),
    ({"TEST_MOUNTED": "0"}, "not mounted"),
    ({"TEST_VOLUME_DEVICE": "8:1"}, "separate filesystem"),
])
def test_run_refuses_insufficient_resources_or_unmounted_data(ops, settings, message):
    server_commands(ops)
    checkout(ops)
    result = ops[5](SCRIPTS[1], "hc003", **settings)
    assert result.returncode != 0 and message in result.stderr
    assert not ops[6]("systemd-run")


@pytest.mark.parametrize("dirty,allowed", [
    ("backtesting/experiments/hc003.py", False),
    ("research/experiments/HC003/preregistration.md", False),
    ("research/experiments/LH002/prompts/v2_probe.txt", False),
    (REGISTRY, True), (LEDGER, True), (RESULT, True),
    ("research/experiments/HC003/diagnostics/costs.csv", True),
    ("data/research/lh002/calls/cache.json", True),
])
def test_run_dirty_tree_exempts_only_generated_paths(ops, dirty, allowed):
    server_commands(ops)
    checkout(ops)
    result = ops[5](SCRIPTS[1], "hc003", TEST_DIRTY=json.dumps([dirty]))
    assert (result.returncode == 0) is allowed, result.stderr
    assert bool(ops[6]("systemd-run")) is allowed


def test_run_blocks_overlapping_research_and_status_reports_finished_exit_codes(ops):
    server_commands(ops)
    checkout(ops)
    units = [
        {"name": "hqa-research-hc003-running.service", "state": "active", "substate": "running", "result": "success", "exit": 0},
        {"name": "hqa-research-hc002-done.service", "state": "active", "substate": "exited", "result": "success", "exit": 0},
        {"name": "hqa-research-hi001-failed.service", "state": "failed", "substate": "failed", "result": "exit-code", "exit": 2},
    ]
    settings = {"TEST_UNITS": json.dumps(units)}
    result = ops[5](SCRIPTS[1], "hc003", **settings)
    assert result.returncode != 0 and "already in progress" in result.stderr
    status = ops[5](SCRIPTS[1], "status", **settings)
    assert status.returncode == 0, status.stderr
    assert "ExecMainStatus=2" in status.stdout and "SubState=exited" in status.stdout
    assert all(unit["name"] in status.stdout for unit in units)
    assert not ops[6]("systemd-run")


@pytest.mark.parametrize("module", ["hc002", "hc003", "hi001", "r001", "hf001", "bb001", "lh002"])
def test_run_dispatches_supported_modules_and_preserves_arguments(ops, module):
    server_commands(ops)
    checkout(ops)
    args = ["--holdout"] if module == "hc003" else ["probe"] if module == "lh002" else []
    result = ops[5](SCRIPTS[1], module, *args)
    assert result.returncode == 0, result.stderr
    command, = ops[6]("systemd-run")
    assert command[-(3 + len(args)):] == ["-m", f"backtesting.experiments.{module}", "--execute", *args]


def remote_results(ops):
    server = ops[1]
    server.mkdir()
    research_data = ops[2] / "hqa-research-data"
    research_data.mkdir()
    (server / "data").symlink_to(research_data, target_is_directory=True)
    write(server / REGISTRY, b"header\r\nold\r\nnew\r\n")
    write(server / LEDGER, b'{"id":"old"}\n{"id":"new"}\n')
    write(server / RESULT, b'{"result":"new"}\n')
    write(server / "research/experiments/HC003/diagnostics/nested/costs.csv")
    write(server / "data/research/lh002/calls/answer.json")
    for relative in (".env", "data/market/krx_daily/secret.jsonl",
                     "research/experiments/HC003/preregistration.md",
                     "research/experiments/HC003/prompts/prompt.txt",
                     "data/research/lh002/credentials.json",
                     "data/research/lh002/.calls.lock",
                     "research/experiments/HC003/results/private.key"):
        write(server / relative, b"excluded\n")
    (server / "data/research/lh002/linked.json").symlink_to(server / ".env")
    ops[3]["TEST_REMOTE_ROOT"] = str(server)
    return server


@pytest.mark.parametrize("execute", [False, True])
def test_sync_dry_run_defaults_ignore_existing_whitelist_and_secret_exclusions(ops, execute):
    project, server = ops[0], ops[1]
    server.mkdir()
    (server / "data").mkdir()
    for relative in DATA_PATHS:
        write(project / "data" / relative / "file.jsonl")
    write(project / "data/market/krx_daily/keep.jsonl", b"PC version\n")
    write(server / "data/market/krx_daily/keep.jsonl", b"server version\n")
    for relative in ("market/krx_daily/.env", "research/lh002/.calls.lock",
                     "reference/credentials.json", "market/investor_flow/outside.json"):
        write(project / "data" / relative, b"excluded\n")
    (project / "data/reference/linked.json").symlink_to(project / "data/market/krx_daily/.env")
    key = write(project / 'SSH key with "quotes"', b"not a real key\n")
    ops[3]["TEST_REMOTE_ROOT"] = str(server)
    args = ["--execute"] if execute else []
    result = ops[5](SCRIPTS[2], "operator@192.0.2.1", key, *args)
    assert result.returncode == 0, result.stderr
    command, = ops[6]("rsync")
    assert ("-n" in command) is not execute and "--ignore-existing" in command
    assert "--delete" not in command and "--rsync-path=sudo -n -u hqares rsync" in command
    assert all(str(project / "data") + "/./" + relative + "/" in command for relative in DATA_PATHS)
    assert f'-i "{str(key).replace(chr(34), chr(34) * 2)}"' in command[command.index("-e") + 1]
    assert (server / "data/market/krx_daily/keep.jsonl").read_bytes() == b"server version\n"
    assert (server / "data/research/lh002/file.jsonl").exists() is execute
    assert not (server / "data/research/lh002/.calls.lock").exists()
    assert not (server / "data/reference/credentials.json").exists()
    assert not (server / "data/reference/linked.json").exists()
    assert not (server / "data/market/investor_flow").exists()


@pytest.mark.parametrize("execute", [False, True])
def test_pull_fetches_only_staging_and_compares_without_overwriting_local(ops, execute):
    project = ops[0]
    remote_results(ops)
    write(project / REGISTRY, b"header\r\nold\r\n")
    write(project / LEDGER, b'{"id":"old"}\n')
    write(project / RESULT, b"local result\n")
    result = ops[5](SCRIPTS[3], "operator@192.0.2.1", *(["--execute"] if execute else []))
    assert result.returncode == 0, result.stderr
    args, = ops[6]("rsync")
    assert ("-n" in args) is not execute and "--delete" not in args
    assert "--no-links" in args
    stage = project / ".research_pull"
    assert stage.exists() is execute
    assert (project / REGISTRY).read_bytes() == b"header\r\nold\r\n"
    assert (project / RESULT).read_bytes() == b"local result\n"
    if execute:
        paths = {path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file() and path.name != ".complete"}
        assert paths == {REGISTRY, LEDGER, RESULT,
                         "research/experiments/HC003/diagnostics/nested/costs.csv",
                         "data/research/lh002/calls/answer.json"}
        assert "M " + RESULT in result.stdout and "1 new full lines" in result.stdout


def test_apply_appends_only_new_full_lines_in_order_and_preserves_existing_bytes(ops):
    project, stage = ops[0], ops[0] / ".research_pull"
    write(stage / ".complete", b"hqa-research-results-v1\n")
    write(project / REGISTRY, b"header\r\nold\r\n")
    write(project / LEDGER, b'{"id":"old"}\n')
    write(project / RESULT, b"old result\n")
    write(stage / REGISTRY, b"header\r\nold\r\nnew-1\r\nold\r\nnew-2\r\nnew-1\r\n")
    write(stage / LEDGER, b'{"id":"old"}\n{"id":"new"}\n{"id":"old"}\n')
    write(stage / RESULT, b"new result\n")
    write(stage / "data/research/lh002/calls/cache.json")
    result = ops[5](SCRIPTS[3], "--apply")
    assert result.returncode == 0, result.stderr
    assert (project / REGISTRY).read_bytes() == b"header\r\nold\r\nnew-1\r\nnew-2\r\n"
    assert (project / LEDGER).read_bytes() == b'{"id":"old"}\n{"id":"new"}\n'
    assert (project / RESULT).read_bytes() == b"new result\n"
    assert (project / "data/research/lh002/calls/cache.json").exists()
    assert not ops[6]("rsync")


@pytest.mark.parametrize("relative", [REGISTRY, LEDGER])
@pytest.mark.parametrize("local,remote", [
    (b"header\nPC-only\n", b"header\nserver-only\n"),
    (b"header\none\ntwo\n", b"header\ntwo\none\n"),
    (b"header\none\ntwo\n", b"header\none\n"),
    (b"header\none", b"header\none\n"),
    (b"header\n", b"header\npartial"),
    (b"header\none\n", b""),
])
def test_apply_refuses_divergent_reordered_truncated_or_partial_ledgers_before_any_copy(ops, relative, local, remote):
    project, stage = ops[0], ops[0] / ".research_pull"
    write(stage / ".complete", b"hqa-research-results-v1\n")
    write(project / relative, local)
    if remote:
        write(stage / relative, remote)
    write(stage / RESULT, b"server result\n")
    write(project / RESULT, b"local result\n")
    result = ops[5](SCRIPTS[3], "--apply")
    assert result.returncode != 0 and ("prefix" in result.stderr or "newline" in result.stderr)
    assert (project / relative).read_bytes() == local
    assert (project / RESULT).read_bytes() == b"local result\n"
    other = LEDGER if relative == REGISTRY else REGISTRY
    assert not (project / other).exists() and not ops[6]("rsync")


def test_apply_is_repeatable_when_ledgers_are_true_prefixes(ops):
    project, stage = ops[0], ops[0] / ".research_pull"
    write(stage / ".complete", b"hqa-research-results-v1\n")
    write(stage / REGISTRY, b"header\none\ntwo\n")
    write(stage / LEDGER, b'{"id":"one"}\n')
    write(stage / RESULT)
    for _ in range(2):
        result = ops[5](SCRIPTS[3], "--apply")
        assert result.returncode == 0, result.stderr
    assert (project / REGISTRY).read_bytes() == b"header\none\ntwo\n"
    assert (project / LEDGER).read_bytes() == b'{"id":"one"}\n'


@pytest.mark.parametrize("scenario", ["outside", "tracked", "symlink", "unexpected", "existing-snapshot"])
def test_pull_refuses_unsafe_staging_paths_and_contents(ops, scenario):
    project, stage = ops[0], ops[0] / ".research_pull"
    args = ["--apply"]
    settings = {}
    if scenario == "outside":
        args = ["--stage-dir", project.parent / "outside", "operator@192.0.2.1"]
    elif scenario == "tracked":
        settings = {"TEST_TRACKED_STAGE": ".research_pull/tracked"}
        args = ["operator@192.0.2.1"]
    elif scenario == "symlink":
        write(stage / ".complete", b"hqa-research-results-v1\n")
        write(stage / RESULT)
        (stage / "data").symlink_to(project.parent)
    elif scenario == "unexpected":
        write(stage / ".complete", b"hqa-research-results-v1\n")
        write(stage / "research/experiments/HC003/preregistration.md")
    else:
        write(stage / RESULT)
        args = ["--execute", "operator@192.0.2.1"]
    result = ops[5](SCRIPTS[3], *args, **settings)
    assert result.returncode != 0 and not ops[6]("rsync")
    assert not (project / RESULT).exists()


def test_apply_refuses_an_interrupted_snapshot_without_completion_marker(ops):
    stage = ops[0] / ".research_pull"
    write(stage / RESULT)
    result = ops[5](SCRIPTS[3], "--apply")
    assert result.returncode != 0 and "Incomplete staging snapshot" in result.stderr
    assert not (ops[0] / RESULT).exists()


@pytest.mark.parametrize("relative", SCRIPTS)
def test_script_syntax_and_no_collector_secrets_or_data_permission_changes(relative):
    script = ROOT / relative
    subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True, text=True)
    text = script.read_text()
    assert "/etc/hqa/collector.env" not in text
    assert "KIS_" not in text
    assert "git push" not in text and "git add" not in text and "git commit" not in text
    assert "systemctl enable" not in text and "systemctl start" not in text
    assert "chown -R" not in text and "chmod -R" not in text


def test_research_requirements_reuse_collector_plus_jsonschema_only():
    lines = (ROOT / "deploy/research/requirements-research.txt").read_text().splitlines()
    requirements = [line for line in lines if line and not line.startswith("#")]
    assert requirements == ["-r ../collector/requirements-collector.txt", "jsonschema>=4.18.0"]
