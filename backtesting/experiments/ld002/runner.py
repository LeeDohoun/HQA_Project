"""One daily decision, one fixed re-ask, durable attempts and identity stops."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.lh001.runner import inspect_events
from jsonschema import ValidationError

from . import CODEX_VERSION, END, MODEL, PROMPT_DIR, START, Refused
from .inputs import prompt_hashes, validate
from .storage import attempts, aware, digest, file_hash, lock, now, seal, verified


def implementation_hashes(repo_root=PROJECT_ROOT):
    root = Path(repo_root)
    from backtesting.experiments.lh003.runner import implementation_files
    files = set(implementation_files(root))
    files.update((root / "backtesting/experiments/ld002").glob("*.py"))
    files.update((root / "scripts/ld002").glob("*.py"))
    files.update((root / "research/experiments/LD002_llm_delegated_virtual/prompts").glob("v1*"))
    files.add(root / "research/experiments/LD002_llm_delegated_virtual/preregistration.md")
    files.add(root / "src/forward/fw001.py")
    return {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(files) if p.is_file()}


def check_committed(repo_root=PROJECT_ROOT):
    reference = "research/experiments/LD002_llm_delegated_virtual/distribution_code_reference.json"
    for relative in [*implementation_hashes(repo_root), reference]:
        for arguments in (("ls-files", "--error-unmatch", "--", relative), ("diff", "--quiet", "HEAD", "--", relative)):
            result = subprocess.run(["git", "-C", str(repo_root), *arguments], capture_output=True, text=True,
                                    check=False, stdin=subprocess.DEVNULL)
            if result.returncode:
                raise Refused("LD002 code/prompts must be committed and unchanged: " + relative)


def probe_identity(*, process=subprocess.run, executable=None, prompt_dir=PROMPT_DIR):
    executable = executable or shutil.which("codex")
    if not executable or not Path(executable).is_file():
        raise Refused("Codex executable identity unavailable")
    path = Path(executable).resolve()
    def executable_artifacts():
        files = [path]
        if path.suffix in {".js", ".mjs", ".cjs"}:
            package = path.parent.parent
            native = [*package.glob("vendor/*/codex/codex"), *package.parent.glob("codex-*/vendor/*/codex/codex")]
            if not native:
                raise Refused("Codex launcher native executable identity unavailable")
            files += native
        return {str(p.resolve()): file_hash(p) for p in sorted(set(files))}
    before = executable_artifacts()
    with tempfile.TemporaryDirectory(prefix="ld002-identity-", dir="/tmp") as cwd:
        result = process([str(path), "--version"], cwd=cwd, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=10, check=False)
    if result.returncode or result.stdout.strip() != CODEX_VERSION or before != executable_artifacts():
        raise Refused("Codex version/executable changed or cannot be verified")
    return {"model": MODEL, "codex_version": CODEX_VERSION, "executable": str(path),
            "executable_sha256": before[str(path)], "executable_artifacts": before, "artifacts": prompt_hashes(prompt_dir)}


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


class LlmRunner:
    def __init__(self, root, pinned, *, process=None, identity_probe=None, clock=now, repo_root=PROJECT_ROOT,
                 synthetic=False, prompt_dir=PROMPT_DIR):
        self.root, self.pinned, self.clock = Path(root), pinned, clock
        self.process = subprocess.run if process is None else process
        self.probe = identity_probe or (lambda: probe_identity(process=self.process, prompt_dir=prompt_dir))
        self.repo_root, self.synthetic = Path(repo_root), synthetic
        self.model = MODEL

    def stop(self, reason):
        path = self.root / "model_changed.json"
        if not path.exists():
            seal(path, {"status": "model_changed", "reason": reason, "recognized_at": aware(self.clock()).isoformat()})

    def identity(self):
        if (self.root / "model_changed.json").exists():
            raise Refused("model_changed")
        try:
            observed = self.probe()
            if self.model != MODEL or observed != self.pinned:
                raise Refused("model/CLI/executable/artifact identity drift")
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            self.stop(str(exc))
            raise Refused("model_changed: " + str(exc)) from exc
        return observed

    def decide(self, payload, *, day, deadline, format_trial=False):
        deadline = aware(deadline)
        if not format_trial and not START <= day <= END:
            raise Refused("decision outside registered dates")
        if not format_trial and not self.synthetic:
            from .calendar import Calendar
            Calendar().check(day)
        if not self.synthetic:
            check_committed(self.repo_root)
        key = digest([day, payload, format_trial])
        with lock(self.root / ".model.lock"):
            journal = attempts(self.root)
            prior = [a for a in journal if a["day"] == day and a["format_trial"] == format_trial]
            if any(a["key"] != key for a in prior):
                raise Refused("cannot replace a day's sealed input or prompt")
            history = []
            for reservation in prior:
                path = self.root / "attempts" / f"{reservation['number']:06d}.result.json"
                if not path.exists():
                    seal(path, {"state": "interrupted", "output": None, "error": "reserved attempt interrupted",
                                "finished_at": aware(self.clock()).isoformat(), "seconds": None})
                history.append({**reservation, **verified(path)})
            if len(history) > (10 if format_trial else 2):
                raise Refused("daily attempt budget is inconsistent")
            try:
                self.identity()
            except Refused:
                return {"valid": False, "reason": "model_changed", "output": None, "attempts": history}
            if any(a["state"] == "valid" for a in history):
                output = next(a["output"] for a in history if a["state"] == "valid")
                validate(output, payload["schema"])
                return {"valid": True, "reason": None, "output": output, "attempts": history}
            maximum = 1 if format_trial else 2
            while len(history) < maximum:
                if aware(self.clock()) >= deadline:
                    return {"valid": False, "reason": "decision_late", "output": None, "attempts": history}
                all_attempts = attempts(self.root)
                if format_trial and sum(a["format_trial"] for a in all_attempts) >= 10:
                    return {"valid": False, "reason": "format_cap_10", "output": None, "attempts": history}
                try:
                    self.identity()
                except Refused:
                    return {"valid": False, "reason": "model_changed", "output": None, "attempts": history}
                number = len(all_attempts) + 1
                prompt = payload["prompt"] + ("\n" + payload["reask"] if history else "")
                reservation = {"number": number, "key": key, "day": day, "format_trial": format_trial,
                    "try": len(history), "started_at": aware(self.clock()).isoformat(), "identity": self.pinned,
                    "prompt_sha256": digest(prompt), "schema_sha256": digest(payload["schema"])}
                seal(self.root / "attempts" / f"{number:06d}.started.json", reservation)
                started, interrupted = time.perf_counter(), None
                record = {"state": "invalid", "output": None, "stdout": "", "stderr": ""}
                with tempfile.TemporaryDirectory(prefix="ld002-artifacts-", dir="/tmp") as artifacts:
                    schema_path, output_path = Path(artifacts) / "schema.json", Path(artifacts) / "output.json"
                    schema_path.write_text(json.dumps(payload["schema"], ensure_ascii=False))
                    command = [self.pinned["executable"], "exec", "-m", self.model, "-c", "model_reasoning_effort=medium",
                        "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--json", "--output-schema",
                        str(schema_path), "-o", str(output_path), "-"]
                    record["command"] = command
                    try:
                        with tempfile.TemporaryDirectory(prefix="ld002-empty-", dir="/tmp") as cwd:
                            if Path(cwd).resolve().is_relative_to(self.repo_root.resolve()) or list(Path(cwd).iterdir()):
                                raise Refused("model workspace must be empty and outside the repository")
                            result = self.process(command, cwd=cwd, input=prompt, capture_output=True, text=True,
                                timeout=min(480, max(.001, (deadline - aware(self.clock())).total_seconds())), check=False)
                        record.update(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)
                        summary, messages = inspect_events(result.stdout)
                        record["event_summary"] = summary
                        for line in result.stdout.splitlines():
                            event = json.loads(line)
                            if isinstance(event, dict) and any(field in event and event[field] != expected for field, expected in
                                    (("model", MODEL), ("model_name", MODEL), ("codex_version", CODEX_VERSION))):
                                self.stop("model/CLI drift in event stream")
                                raise Refused("model_changed")
                        if result.returncode or summary["violations"]:
                            if any(term in (result.stdout + result.stderr).lower() for term in ("quota", "usage limit", "rate limit")):
                                raise Refused("usage_limit")
                            raise Refused("forbidden/invalid events or process exit: " + str(summary["violations"]))
                        output = json.loads(output_path.read_text()) if output_path.is_file() else None
                        if not messages or not isinstance(messages[-1], str) or json.loads(messages[-1]) != output:
                            raise Refused("missing or inconsistent final message")
                        validate(output, payload["schema"])
                        record.update(state="valid", output=output)
                    except subprocess.TimeoutExpired as exc:
                        record.update(error="timeout", stdout=_text(exc.stdout), stderr=_text(exc.stderr))
                    except (ValueError, ValidationError, OSError) as exc:
                        record["error"] = type(exc).__name__ + ": " + str(exc)
                    except BaseException as exc:
                        record.update(state="interrupted", error=type(exc).__name__ + ": " + str(exc))
                        interrupted = exc
                    record["output_raw"] = output_path.read_text() if output_path.is_file() else None
                try:
                    self.identity()
                except Refused:
                    record.update(state="invalid", output=None, error="model_changed")
                record.update(seconds=time.perf_counter() - started, finished_at=aware(self.clock()).isoformat())
                seal(self.root / "attempts" / f"{number:06d}.result.json", record)
                history.append({**reservation, **record})
                if interrupted:
                    raise interrupted
                if (self.root / "model_changed.json").exists():
                    return {"valid": False, "reason": "model_changed", "output": None, "attempts": history}
                if record["state"] == "valid":
                    return {"valid": True, "reason": None, "output": record["output"], "attempts": history}
            return {"valid": False, "reason": "invalid_after_reask", "output": None, "attempts": history}
