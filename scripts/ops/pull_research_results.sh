#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' 'Usage: pull_research_results.sh [--execute] [--stage-dir dir] user@host [ssh-key-path]' \
        '       pull_research_results.sh --apply [--stage-dir dir]' \
        'Default: rsync dry run. --execute fetches staging only; --apply uses an existing staging snapshot offline.'
}
execute=false
apply=false
stage_argument=.research_pull
arguments=()
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --execute) execute=true; shift ;;
        --apply) apply=true; shift ;;
        --stage-dir) [[ "$#" -ge 2 ]] || { usage >&2; exit 2; }; stage_argument=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --*) usage >&2; exit 2 ;;
        *) arguments+=("$1"); shift ;;
    esac
done
if [[ "$apply" == true ]]; then
    [[ "$execute" == false && "${#arguments[@]}" -eq 0 ]] || { usage >&2; exit 2; }
elif [[ "${#arguments[@]}" -lt 1 || "${#arguments[@]}" -gt 2 ]]; then
    usage >&2; exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/../.." && pwd)
if [[ "$stage_argument" == /* ]]; then
    stage_directory=$stage_argument
else
    stage_directory="$project_root/$stage_argument"
fi

# This helper is local and standard-library-only. No experiment imports, env
# files or credentials are read. Validate both ledgers before copying any file.
staging() {
    python3 - "$project_root" "$stage_directory" "$1" <<'PY'
import filecmp
import fcntl
import fnmatch
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

root, stage = (Path(value).absolute() for value in sys.argv[1:3])
mode = sys.argv[3]
ledgers = ("research/experiments/registry.csv", "research/experiments/holdout_ledger.jsonl")
secret_patterns = (".env*", "*.lock", "*.pem", "*.key", ".ssh", ".codex",
                   "*token*", "*credential*", "*secret*", "*account*")

def fail(message):
    raise SystemExit(message)

def safe_path(path):
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail("Path must be inside the PC repository.")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            fail(f"Refusing symlink: {cursor}")
    if not path.resolve().is_relative_to(root.resolve()):
        fail("Path resolves outside the PC repository.")

safe_path(stage)
stage = stage.resolve()
relative_stage = stage.relative_to(root)
if not relative_stage.parts or relative_stage.parts[0] in (".git", ".agents", ".codex", "data", "research"):
    fail("Use a dedicated staging directory outside Git state and research/data paths.")
tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", relative_stage.as_posix()],
                         check=True, capture_output=True).stdout
if tracked:
    fail("Staging directory contains tracked files.")
if stage.exists() and not stage.is_dir():
    fail("Staging path is not a directory.")
if mode == "check":
    if stage.exists() and any(stage.iterdir()):
        fail("Staging directory is not empty; use --apply or a new --stage-dir for the next snapshot.")
    sys.exit(0)
if not stage.is_dir():
    fail("Staging snapshot is missing; fetch it with --execute first.")
marker = stage / ".complete"
if mode == "apply" and (marker.is_symlink() or not marker.is_file()
                        or marker.read_bytes() != b"hqa-research-results-v1\n"):
    fail("Incomplete staging snapshot; only a successful --execute fetch can be applied.")

def allowed(relative):
    parts = relative.parts
    if any(fnmatch.fnmatch(part.lower(), pattern) for part in parts for pattern in secret_patterns):
        return False
    return (relative.as_posix() in ledgers
            or (len(parts) >= 5 and parts[:2] == ("research", "experiments")
                and parts[3] in ("results", "diagnostics"))
            or (len(parts) >= 3 and parts[:2] == ("data", "research")))

files = []
for directory, subdirectories, filenames in os.walk(stage):
    for name in subdirectories + filenames:
        if (Path(directory) / name).is_symlink():
            fail("Staging snapshot contains a symlink.")
    for name in filenames:
        source = Path(directory) / name
        relative = source.relative_to(stage)
        if relative.as_posix() == ".complete":
            continue
        if not source.is_file() or not allowed(relative):
            fail(f"Unexpected staging file: {relative}")
        destination = root / relative
        safe_path(destination)
        if destination.exists() and not destination.is_file():
            fail(f"Local destination is not a file: {relative}")
        files.append((relative, source, destination))
files.sort()

def append_plan(local, remote, relative):
    for label, content in (("local", local), ("server", remote)):
        if content and not content.endswith(b"\n"):
            raise ValueError(f"{relative}: {label} file must end with a newline")
    local_lines, remote_lines = local.splitlines(keepends=True), remote.splitlines(keepends=True)
    if remote_lines[:len(local_lines)] != local_lines:
        raise ValueError(f"{relative}: local lines are not a prefix of the server file; refusing append-only merge")
    seen = set(local_lines)
    missing = []
    for line in remote_lines[len(local_lines):]:
        if line not in seen:
            missing.append(line)
            seen.add(line)
    return b"".join(missing), len(missing)

remote_ledgers = {}
for relative in ledgers:
    safe_path(root / relative)
    if (root / relative).exists() and not (root / relative).is_file():
        fail(f"Local ledger is not a regular file: {relative}")
    source = stage / relative
    remote_ledgers[relative] = source.read_bytes() if source.is_file() else b""

unchanged = 0
for relative, source, destination in files:
    if relative.as_posix() in ledgers:
        continue
    if not destination.exists():
        print(f"A {relative}")
    elif not filecmp.cmp(source, destination, shallow=False):
        print(f"M {relative}")
    else:
        unchanged += 1
conflicts = []
for relative, remote in remote_ledgers.items():
    path = root / relative
    local = path.read_bytes() if path.is_file() else b""
    try:
        pending, count = append_plan(local, remote, relative)
        print(f"{relative}: {count} new full lines")
    except ValueError as exc:
        conflicts.append(str(exc))
        print(f"CONFLICT {exc}")
print(f"Content summary: {len(files)} staged files; {unchanged} unchanged result/data files.")
if mode != "apply":
    print("Local files unchanged. Review staging, then use --apply with the same --stage-dir.")
    sys.exit(0)
if conflicts:
    fail("\n".join(conflicts))

# Use the same advisory file locks as record_trial and HoldoutSession. Recheck
# the prefixes under both locks, before copying results or appending either file.
with ExitStack() as stack:
    plans = []
    for relative, remote in remote_ledgers.items():
        path = root / relative
        if not remote and not path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = stack.enter_context(path.open("a+b"))
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        try:
            pending, count = append_plan(handle.read(), remote, relative)
        except ValueError as exc:
            fail(str(exc))
        plans.append((relative, handle, pending, count))
    for relative, source, destination in files:
        if relative.as_posix() not in ledgers:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    for relative, handle, pending, count in plans:
        if pending:
            handle.write(pending)
            handle.flush()
            os.fsync(handle.fileno())
        print(f"Applied {relative}: appended {count} full lines")
print("Applied result/diagnostic/data files; existing ledger bytes preserved.")
PY
}
if [[ "$apply" == true ]]; then
    staging apply
    exit 0
fi
host=${arguments[0]}
[[ "$host" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] || { printf '%s\n' 'Use user@host with an IPv4 address or DNS name.' >&2; exit 2; }
ssh_command='ssh -o BatchMode=yes'
if [[ "${#arguments[@]}" -eq 2 ]]; then
    ssh_key=$(realpath -e -- "${arguments[1]}")
    [[ -f "$ssh_key" && -r "$ssh_key" ]] || { printf '%s\n' 'SSH key must be readable.' >&2; exit 2; }
    quoted_key=${ssh_key//\"/\"\"}
    ssh_command+=" -o IdentitiesOnly=yes -i \"$quoted_key\""
fi
staging check
options=(-azR --checksum --itemize-changes --no-links --rsync-path='sudo -n -u hqares rsync' -e "$ssh_command")
if [[ "$execute" == false ]]; then
    options+=(-n --compare-dest="$project_root")
    printf '%s\n' 'Dry run: comparison with PC files (rsync includes metadata); no staging or local changes. Add --execute to fetch.'
else
    mkdir -p -- "$stage_directory"
fi
# /./ preserves the two allowed trees. data/ is an implied directory, so the
# server's data symlink is traversed without copying other symlinks.
rsync "${options[@]}" \
    --exclude='.env*' --exclude='*.lock' --exclude='*.key' --exclude='*.pem' \
    --exclude='.ssh/' --exclude='.codex/' --exclude='*token*' --exclude='*credential*' \
    --exclude='*secret*' --exclude='*account*' \
    --include='/research/' --include='/research/experiments/' \
    --include='/research/experiments/registry.csv' --include='/research/experiments/holdout_ledger.jsonl' \
    --include='/research/experiments/*/' \
    --include='/research/experiments/*/results/***' --include='/research/experiments/*/diagnostics/***' \
    --include='/data/' --include='/data/research/***' --exclude='*' \
    "$host:/srv/hqa-research/./research/experiments/" "$host:/srv/hqa-research/./data/research/" \
    "$stage_directory/"
if [[ "$execute" == true ]]; then
    staging inspect
    printf '%s\n' hqa-research-results-v1 > "$stage_directory/.complete"
fi
