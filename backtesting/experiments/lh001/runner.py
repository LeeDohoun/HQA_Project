"""Tool-free Codex calls, immutable inputs, resumable cache and real-call budget."""
from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from backtesting.experiment_registry import PROJECT_ROOT
from src.ingestion.storage import atomic_write, file_lock

from . import CODEX_VERSION, MODEL, PROMPT_DIR, PROMPT_VERSION, workspace
from .config import LH001
from .inputs import digest


class StageStopped(RuntimeError):
    pass


def save_json(path, value):
    atomic_write(Path(path), json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prompt_hashes(prompt_dir=PROMPT_DIR):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(prompt_dir).glob("v[12]*")) if path.is_file()}


def check_committed(prompt_dir, repo_root):
    for path in sorted(Path(prompt_dir).glob("v[12]*")):
        relative = path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        for arguments in (("ls-files", "--error-unmatch", "--", relative),
                          ("diff", "--quiet", "HEAD", "--", relative)):
            check = subprocess.run(["git", "-C", str(repo_root), *arguments], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, check=False)
            if check.returncode:
                raise StageStopped(f"prompt/schema must be committed and unchanged before real calls: {path.name}")


def inspect_events(stream):
    counts, violations, messages = Counter(), [], []
    completed = False
    known_events = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error",
                    "item.started", "item.updated", "item.completed"}
    for line in stream.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            violations.append("malformed_json_event")
            continue
        kind = event.get("type") if isinstance(event, dict) else None
        counts[str(kind)] += 1
        if kind not in known_events:
            violations.append(f"unknown_event:{kind}")
        if kind in {"turn.failed", "error"}:
            violations.append(kind)
        if kind == "turn.completed":
            completed = True
        if kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            counts[f"item:{item_type}"] += 1
            if item_type not in {"reasoning", "agent_message"}:
                violations.append(f"forbidden_or_unknown_item:{item_type}")
            if item_type == "agent_message" and kind == "item.completed":
                messages.append(item.get("text"))
    if not completed:
        violations.append("missing_turn_completed")
    return {"counts": dict(counts), "violations": sorted(set(violations)), "completed": completed}, messages


class LlmRunner:
    def __init__(self, *, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, prompt_dir=None,
                 subprocess_call=None, timeout=300, enforce_committed=True, config=LH001):
        self.config = config
        self.directory = workspace(data_dir, config)
        self.repo_root, self.prompt_dir = Path(repo_root), Path(config.prompt_dir if prompt_dir is None else prompt_dir)
        self.subprocess_call = subprocess.run if subprocess_call is None else subprocess_call
        self.timeout, self.enforce_committed = timeout, enforce_committed
        self.model = MODEL
        self.version = None
        self.hashes = prompt_hashes(self.prompt_dir)

    def budget(self):
        path = self.directory / "budget.json"
        if not path.exists() and any((self.directory / "calls").glob("*.json")):
            raise StageStopped("persistent budget is missing while call records exist; refuse to reset it")
        return read_json(path) if path.exists() else {"used": 0, "cap": self.config.budget_cap}

    def _identity(self, cwd):
        if (self.directory / "invalidated.json").exists():
            raise StageStopped("experiment identity was invalidated; do not resume under this experiment ID")
        if self.model != MODEL or prompt_hashes(self.prompt_dir) != self.hashes:
            raise StageStopped("model or prompt/schema changed mid-run")
        result = self.subprocess_call(["codex", "--version"], cwd=cwd, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=self.timeout, check=False)
        version = result.stdout.strip()
        if result.returncode or version != CODEX_VERSION or (self.version is not None and version != self.version):
            raise StageStopped(f"Codex version changed or differs from preregistration: {version}")
        self.version = version
        manifest = {"model": self.model, "codex_version": version, "prompt_version": PROMPT_VERSION, "hashes": self.hashes}
        if self.config != LH001:
            manifest.update(experiment_id=self.config.experiment_id, probe_version=self.config.probe_version)
        path = self.directory / "manifest.json"
        if path.exists() and read_json(path) != manifest:
            raise StageStopped("model/version/prompts differ from frozen experiment manifest")
        if not path.exists():
            save_json(path, manifest)

    def call(self, template, rendered_input, schema_name, repeat_index, *, validate=None, stage=None):
        schema = read_json(self.prompt_dir / schema_name)
        Draft202012Validator.check_schema(schema)
        probe_v2 = self.config.probe_version == "v2" and template == "v2_probe.txt"
        instructions = (self.prompt_dir / template).read_text(encoding="utf-8")
        if not probe_v2:
            instructions = (self.prompt_dir / "v1_common.txt").read_text(encoding="utf-8") + "\n" + instructions
        prompt = instructions + "\n<UNTRUSTED_DATA>\n" + rendered_input + "\n</UNTRUSTED_DATA>"
        # The prompt goes through stdin ("-"): execve limits each argument to
        # MAX_ARG_STRLEN, and business-text inputs exceed it. Never truncate.
        version = (self.config.probe_version if probe_v2 else PROMPT_VERSION) + "/" + template + "/" + digest(instructions)
        identity = [version, schema, rendered_input, repeat_index]
        key = digest(identity if self.config == LH001 else [self.config.experiment_id, *identity])
        input_hash = digest(rendered_input)
        cache_path = self.directory / "calls" / f"{key}.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        with file_lock(self.directory / ".calls.lock"):
            if self.enforce_committed:
                check_committed(self.prompt_dir, self.repo_root)
            with tempfile.TemporaryDirectory(prefix=self.config.name.lower() + "-work-", dir="/tmp") as cwd:
                if Path(cwd).resolve().is_relative_to(self.repo_root.resolve()):
                    raise StageStopped("Codex work directory must be outside repository")
                self._identity(cwd)
                stored = read_json(cache_path) if cache_path.exists() else {
                    "key": key, "input_hash": input_hash, "input_file": f"inputs/{input_hash}.txt",
                    "prompt_version": version, "schema_hash": digest(schema), "repeat_index": repeat_index,
                    "model": self.model, "codex_version": self.version, "stage": stage, "attempts": [], "valid": False, "output": None}
                if stored["input_hash"] != input_hash or stored["key"] != key:
                    raise StageStopped("call cache identity mismatch")
                input_path = self.directory / stored["input_file"]
                if input_path.exists() and input_path.read_text(encoding="utf-8") != rendered_input:
                    raise StageStopped("stored rendered input changed")
                if not input_path.exists():
                    atomic_write(input_path, rendered_input)
                if stored["valid"]:
                    Draft202012Validator(schema).validate(stored["output"])
                    if validate is not None:
                        validate(stored["output"])
                    return {**stored, "cache_hit": True}
                while sum(attempt["state"] == "invalid" for attempt in stored["attempts"]) < 3:
                    budget = self.budget()
                    if budget["cap"] != self.config.budget_cap or type(budget["used"]) is not int or not 0 <= budget["used"] < self.config.budget_cap:
                        raise StageStopped(f"hard real-call budget cap ({self.config.budget_cap}) reached or budget is invalid")
                    budget["used"] += 1
                    save_json(self.directory / "budget.json", budget)
                    started = time.perf_counter()
                    attempt = {"call_number": budget["used"], "state": "invalid", "seconds": None, "event_summary": None}
                    with tempfile.TemporaryDirectory(prefix=self.config.name.lower() + "-artifacts-", dir="/tmp") as artifacts:
                        schema_path, output_path = Path(artifacts) / "schema.json", Path(artifacts) / "output.json"
                        save_json(schema_path, schema)
                        command = ["codex", "exec", "-m", self.model, "-c", "model_reasoning_effort=medium",
                                   "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--json",
                                   "--output-schema", str(schema_path), "-o", str(output_path),
                                   "-"]
                        try:
                            with tempfile.TemporaryDirectory(prefix=self.config.name.lower() + "-attempt-", dir="/tmp") as attempt_cwd:
                                result = self.subprocess_call(command, cwd=attempt_cwd, input=prompt,
                                                              capture_output=True, text=True, timeout=self.timeout, check=False)
                            summary, messages = inspect_events(result.stdout)
                            attempt["event_summary"] = summary
                            # An execution/quota stop is durable but is not silently retried.
                            failed = result.returncode or any(kind in summary["violations"] for kind in ("turn.failed", "error"))
                            if failed and any(term in (result.stdout + result.stderr).lower() for term in
                                              ("usage limit", "quota", "rate limit", "rate_limit_exceeded")):
                                attempt["state"] = "interrupted"
                                raise StageStopped("Codex quota/usage interruption; resume with unchanged inputs")
                            if result.returncode:
                                summary["violations"].append("nonzero_exit")
                            output = read_json(output_path) if output_path.is_file() else None
                            if not messages or not isinstance(messages[-1], str) or json.loads(messages[-1]) != output:
                                summary["violations"].append("missing_or_inconsistent_final_message")
                            if not summary["violations"]:
                                Draft202012Validator(schema).validate(output)
                                if validate is not None:
                                    validate(output)
                                attempt["state"] = "valid"
                                stored.update(valid=True, output=output)
                        except (ValueError, ValidationError, subprocess.TimeoutExpired) as exc:
                            attempt["error"] = type(exc).__name__ + ": " + str(exc)[:200]
                        except StageStopped:
                            attempt["seconds"] = time.perf_counter() - started
                            stored["attempts"].append(attempt)
                            save_json(cache_path, stored)
                            raise
                        except OSError as exc:
                            attempt.update(state="interrupted", seconds=time.perf_counter() - started,
                                           error=type(exc).__name__ + ": " + str(exc))
                            stored["attempts"].append(attempt)
                            save_json(cache_path, stored)
                            raise StageStopped(f"Codex process could not execute: {exc}") from exc
                    attempt["seconds"] = time.perf_counter() - started
                    stored["attempts"].append(attempt)
                    save_json(cache_path, stored)
                    try:
                        self._identity(cwd)
                    except StageStopped:
                        stored.update(valid=False, output=None, invalidated_stage="identity changed")
                        save_json(cache_path, stored)
                        save_json(self.directory / "invalidated.json", {"reason": "identity changed mid-run", "call_key": key})
                        raise
                    if stored["valid"]:
                        break
                return {**stored, "cache_hit": False}
