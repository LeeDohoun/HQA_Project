"""Committed preregistrations and an append-only, locked experiment registry."""
from __future__ import annotations

import csv
import fcntl
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TextIO

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path("research/experiments/registry.csv")
COMMIT_MAP_PATH = Path("research/experiments/commit_map.csv")
COMMIT_MAP_COLUMNS = ("experiment_id", "old_commit", "new_commit", "prereg_blob", "reason", "recorded_at")
REQUIRED_FIELDS = ("experiment_id", "sleeve", "hypothesis", "counterparty", "data_periods",
                   "metrics", "pass_criteria", "variants_planned", "uses_llm", "uses_holdout")
REGISTRY_COLUMNS = ("recorded_at", "experiment_id", "prereg_commit", "variant", "verdict", "metrics_json", "note")


@dataclass(frozen=True)
class Preregistration:
    commit_hash: str
    commit_time: datetime
    fields: dict[str, Any]


def _validate_experiment_id(experiment_id: str) -> None:
    if (not isinstance(experiment_id, str) or not experiment_id.strip()
            or experiment_id in (".", "..") or any(c in experiment_id for c in "/\\\0\n\r")):
        raise ValueError("experiment_id must be a nonempty directory name")


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"preregistration git check failed ({' '.join(args)}): {result.stderr.strip()}")
    return result.stdout.strip()


def _own_history(root: Path, experiment_id: str, relative: str) -> list[str]:
    """Commits of this experiment's preregistration, following renames within its directory.

    ``git log --follow`` also follows a file copied or renamed from another experiment's
    directory. That source commit belongs to the other experiment, so history stops at
    the commit that brought the file into this experiment's directory.
    """
    own_dir = f"research/experiments/{experiment_id}/"
    output = _git(root, "log", "--follow", "--name-status", "--format=@@%H|%cI", "--", relative)
    commits: list[str] = []
    for block in output.split("@@")[1:]:
        lines = [line for line in block.splitlines() if line.strip()]
        commits.append(lines[0])
        statuses = [line.split("\t") for line in lines[1:]]
        if any(parts[0][:1] in {"R", "C"} and len(parts) == 3 and not parts[1].startswith(own_dir)
               for parts in statuses):
            break
    return commits


def verify_preregistration(experiment_id: str, repo_root: str | Path = PROJECT_ROOT) -> Preregistration:
    _validate_experiment_id(experiment_id)
    root = Path(repo_root).resolve()
    relative = Path("research/experiments") / experiment_id / "preregistration.md"
    path = root / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("preregistration must be a regular file inside repo_root")
    _git(root, "ls-files", "--error-unmatch", "--", relative.as_posix())
    _git(root, "diff", "--quiet", "--", relative.as_posix())
    _git(root, "diff", "--cached", "--quiet", "--", relative.as_posix())
    commits = _own_history(root, experiment_id, relative.as_posix())
    if not commits:
        raise ValueError("preregistration has not been committed")
    if len(commits) != 1:
        raise ValueError("preregistration is immutable after its first commit; a changed plan requires a new experiment_id")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("preregistration requires YAML front matter delimited by ---")
    try:
        fields = yaml.safe_load("\n".join(lines[1:lines.index("---", 1)]))
    except yaml.YAMLError as exc:
        raise ValueError("invalid preregistration YAML") from exc
    if not isinstance(fields, dict):
        raise ValueError("preregistration YAML must be a mapping")
    missing = [name for name in REQUIRED_FIELDS if name not in fields or fields[name] is None]
    if missing:
        raise ValueError(f"missing preregistration fields: {', '.join(missing)}")
    if fields["experiment_id"] != experiment_id:
        raise ValueError("preregistration experiment_id does not match its directory")
    for name in ("uses_llm", "uses_holdout"):
        if type(fields[name]) is not bool:
            raise ValueError(f"{name} must be a YAML boolean")
    if type(fields["variants_planned"]) is not int or fields["variants_planned"] < 1:
        raise ValueError("variants_planned must be a positive integer")
    commit_hash, commit_time = commits[0].split("|", 1)
    return Preregistration(commit_hash, datetime.fromisoformat(commit_time), fields)


def _resolve_path(repo_root: str | Path, path: str | Path) -> Path:
    return (Path(repo_root) / path).resolve()


@contextmanager
def _locked_append(path: Path) -> Iterator[TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8", newline="") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(size - 1)
                if handle.read(1) != "\n":
                    raise ValueError(f"append-only file must end with a newline: {path}")
            handle.seek(0)
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _registry_rows(handle: TextIO) -> list[dict[str, str]]:
    reader = csv.DictReader(handle)
    if reader.fieldnames != list(REGISTRY_COLUMNS):
        raise ValueError("registry.csv has an invalid header")
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("registry.csv has an invalid row")
    return rows


def _is_sha(value: str) -> bool:
    return len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _commit_aliases(root: Path, experiment_id: str, current: str) -> set[str]:
    """Earlier commit ids that a documented history rewrite mapped onto ``current``.

    A row is honoured only when its new commit is the preregistration's current
    commit and its blob id equals the committed preregistration's content, so the
    map can carry an unchanged plan across rewritten hashes but never admits an
    edited plan.
    """
    path = root / COMMIT_MAP_PATH
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(COMMIT_MAP_COLUMNS):
            raise ValueError("commit_map.csv has an invalid header")
        rows = [row for row in reader if row["experiment_id"] == experiment_id]
    if not rows:
        return set()
    relative = f"research/experiments/{experiment_id}/preregistration.md"
    blob = _git(root, "rev-parse", f"{current}:{relative}")
    aliases = set()
    for row in rows:
        if not (_is_sha(row["old_commit"]) and _is_sha(row["new_commit"]) and _is_sha(row["prereg_blob"])):
            raise ValueError("commit_map.csv rows require full lowercase commit and blob ids")
        if row["new_commit"] == current and row["prereg_blob"] == blob:
            aliases.add(row["old_commit"])
    return aliases


def record_trial(
    experiment_id: str,
    variant: str,
    metrics: dict[str, Any],
    verdict: str,
    *,
    note: str = "",
    repo_root: str | Path = PROJECT_ROOT,
    registry_path: str | Path = REGISTRY_PATH,
) -> None:
    preregistration = verify_preregistration(experiment_id, repo_root)
    if not isinstance(variant, str) or not variant.strip() or not isinstance(verdict, str) or not verdict.strip():
        raise ValueError("variant and verdict must be nonempty strings")
    if not isinstance(metrics, dict) or not isinstance(note, str):
        raise ValueError("metrics must be a mapping and note must be a string")
    try:
        metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("metrics must contain finite JSON values") from exc
    path = _resolve_path(repo_root, registry_path)
    with _locked_append(path) as handle:
        empty = path.stat().st_size == 0
        if not empty:
            rows = _registry_rows(handle)
            accepted = {preregistration.commit_hash} | _commit_aliases(
                Path(repo_root).resolve(), experiment_id, preregistration.commit_hash)
            if any(row["experiment_id"] == experiment_id
                   and row["prereg_commit"] not in accepted for row in rows):
                raise ValueError("registry prereg_commit differs for this experiment_id; a changed plan requires a new experiment_id")
        writer = csv.writer(handle)
        if empty:
            writer.writerow(REGISTRY_COLUMNS)
        writer.writerow((datetime.now(timezone.utc).isoformat(), experiment_id, preregistration.commit_hash,
                         variant, verdict, metrics_json, note))


def trial_count(
    experiment_id: str,
    *,
    repo_root: str | Path = PROJECT_ROOT,
    registry_path: str | Path = REGISTRY_PATH,
) -> int:
    _validate_experiment_id(experiment_id)
    path = _resolve_path(repo_root, registry_path)
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            return sum(row["experiment_id"] == experiment_id for row in _registry_rows(handle))
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def required_t_stat(
    experiment_id: str,
    *,
    repo_root: str | Path = PROJECT_ROOT,
    registry_path: str | Path = REGISTRY_PATH,
) -> float:
    return 2.0 if trial_count(experiment_id, repo_root=repo_root, registry_path=registry_path) <= 20 else 3.0
