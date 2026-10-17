"""Three independent v3 repeats, one fixed re-ask, durable 320-attempt cap."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from jsonschema import ValidationError

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.lh001.arms import aggregate, jaccard
from backtesting.experiments.lh001.runner import inspect_events
from src.ingestion.storage import file_lock

from . import CALL_CAP, CODEX_VERSION, EXPERIMENT_ID, MODEL, PROMPT_DIR, Refused
from .inputs import prompt_hashes, validate
from .storage import attempts, aware, digest, file_hash, now, seal, verified


def implementation_files(repo_root):
    root = Path(repo_root)
    files = [*sorted((root / "backtesting/experiments/lh003").glob("*.py")),
             *sorted((root / "backtesting/experiments/lh001").glob("*.py")),
             *sorted((root / "research/experiments/LH003_llm_leader_forward/prompts").glob("v3*")),
             root / "backtesting/experiments/ordering.py", root / "backtesting/cost_model.py",
             *[root / ("backtesting/" + name + ".py") for name in
               ("append_only", "capacity", "experiment_registry", "holdout", "signal_eval")],
             root / "src/runner/trading_calendar.py", root / "src/runner/event_evidence.py",
             root / "src/utils/stock_codes.py",
             *[root / ("backtesting/experiments/" + name + ".py") for name in
               ("common", "d001", "hc001", "hc002", "hc003", "hf001", "av001", "av002")],
             *[root / ("src/ingestion/" + name + ".py") for name in
               ("dart_listing", "dart_financial_date_rules", "dart_filing_dates", "dart_financials",
                "dart_quarterly", "storage", "incremental_versions", "krx_benchmarks")],
             *[root / ("src/research/" + name + ".py") for name in
               ("industry_map", "industry_index", "market_regimes")],
             root / "scripts/ops/research_run.sh"]
    packages = {parent / "__init__.py" for p in files for parent in p.parents
                if parent != root and parent.is_relative_to(root)}
    return sorted(set(files) | {p for p in packages if p.is_file()})


def implementation_hashes(repo_root):
    root = Path(repo_root)
    return {p.relative_to(root).as_posix(): file_hash(p) for p in implementation_files(root)}


def mechanical_data_hashes(repo_root):
    root = Path(repo_root)
    return {name: file_hash(root / name) for name in
            ("backtesting/statutory_rates.py", "src/krx_calendar_data.py")}


def verify_pinned(root, repo_root):
    start = verified(Path(root) / "start.json")
    expected = start.get("implementation_hashes")
    if not expected or implementation_hashes(repo_root) != expected:
        raise Refused("pinned implementation hashes differ or are missing at evaluation")
    manifest = Path(root) / "model_manifest.json"
    if manifest.exists() and verified(manifest)["implementation_hashes"] != expected:
        raise Refused("model manifest differs from pinned start implementation")
    return expected


def check_committed(repo_root=PROJECT_ROOT):
    root = Path(repo_root)
    files = implementation_files(root)
    if len(files) < 7:
        raise Refused("LH003 implementation/prompts are missing")
    for path in files:
        relative = path.relative_to(root).as_posix()
        for arguments in (("ls-files", "--error-unmatch", "--", relative),
                          ("diff", "--quiet", "HEAD", "--", relative)):
            result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True,
                                    text=True, check=False, stdin=subprocess.DEVNULL)
            if result.returncode:
                raise Refused("LH003 code/prompts must be committed and unchanged before real calls: " + relative)


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


class LlmRunner:
    def __init__(self, root, *, repo_root=PROJECT_ROOT, prompt_dir=PROMPT_DIR, subprocess_call=None,
                 timeout=300, clock=now, format_trial=False):
        if timeout <= 0:
            raise Refused("call timeout must be positive")
        self.root, self.repo_root, self.prompt_dir = Path(root), Path(repo_root), Path(prompt_dir)
        self.process = subprocess.run if subprocess_call is None else subprocess_call
        self.fake = subprocess_call is not None
        self.timeout, self.clock, self.model = timeout, clock, MODEL
        self.format_trial = format_trial

    def stop(self, reason):
        path = self.root / "model_stopped.json"
        if not path.exists():
            seal(path, {"reason": reason, "at": aware(self.clock()).isoformat()})
        return reason

    def identity(self):
        if (self.root / "model_stopped.json").exists():
            raise Refused("model/version drift or unverifiable identity: " + verified(self.root / "model_stopped.json")["reason"])
        if self.model != MODEL:
            raise Refused(self.stop("model name drift"))
        if not self.fake:
            check_committed(self.repo_root)
        with tempfile.TemporaryDirectory(prefix="lh003-version-", dir="/tmp") as cwd:
            result = self.process(["codex", "--version"], cwd=cwd, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=min(self.timeout, 10), check=False)
        if result.returncode or result.stdout.strip() != CODEX_VERSION:
            raise Refused(self.stop("Codex version drift: " + result.stdout.strip()))
        identity = {"experiment_id": EXPERIMENT_ID, "model": self.model, "codex_version": CODEX_VERSION,
                    "prompt_files": prompt_hashes(self.prompt_dir), "implementation_hashes": implementation_hashes(self.repo_root),
                    "fake": self.fake}
        path = self.root / "model_manifest.json"
        if not path.exists() and attempts(self.root):
            raise Refused(self.stop("frozen model manifest missing while attempts exist"))
        if path.exists() and verified(path) != identity:
            raise Refused(self.stop("frozen model/version/prompt/implementation identity changed"))
        seal(path, identity)
        return identity

    def call(self, payload, repeat, *, week, deadline):
        """A pending reservation on restart consumes an attempt as an interruption."""
        key = digest([week, payload, repeat])
        deadline = aware(deadline)
        with file_lock(self.root / ".model.lock"):
            prior = [a for a in attempts(self.root) if a["key"] == key]
            history = []
            output = None
            for reservation in prior:
                result_path = self.root / "attempts" / f"{reservation['number']:06d}.result.json"
                if not result_path.exists():
                    seal(result_path, {"state": "interrupted", "error": "process ended after durable attempt reservation",
                                      "finished_at": aware(self.clock()).isoformat(), "stdout": "", "stderr": ""})
                result = verified(result_path)
                history.append({**reservation, **result})
                if result["state"] == "valid":
                    validate(result["output"], payload["schema"])
                    output = result["output"]
            if len(prior) > 2 or output is not None and history[-1]["state"] != "valid":
                raise Refused("repeat journal is inconsistent")
            try:
                self.identity()
            except (Refused, OSError, subprocess.TimeoutExpired) as exc:
                return {"valid": False, "reason": self.stop(str(exc)), "attempts": history, "output": None}
            if output is not None:
                return {"valid": True, "reason": None, "attempts": history, "output": output}
            while len(history) < (1 if self.format_trial else 2):
                if aware(self.clock()) >= deadline:
                    return {"valid": False, "reason": "finalisation_deadline", "attempts": history, "output": None}
                all_attempts = attempts(self.root)
                used = len(all_attempts)
                if self.format_trial and sum(a.get("synthetic_format") is True for a in all_attempts) >= 8:
                    return {"valid": False, "reason": "synthetic_format_cap_8", "attempts": history, "output": None}
                if used >= CALL_CAP:
                    return {"valid": False, "reason": "call_cap_320", "attempts": history, "output": None}
                index, number = len(history), used + 1
                prompt = payload["prompt"] + ("\n" + payload["reask"] if index else "")
                reservation = {"number": number, "key": key, "week": week, "repeat": repeat, "try": index,
                               "synthetic_format": self.format_trial,
                               "started_at": aware(self.clock()).isoformat(), "deadline": deadline.isoformat(),
                               "prompt_sha256": digest(prompt), "schema_sha256": digest(payload["schema"])}
                # This is the budget claim. No process starts before it is durable.
                seal(self.root / "attempts" / f"{number:06d}.started.json", reservation)
                started = time.perf_counter()
                record = {"state": "invalid", "stdout": "", "stderr": "", "output": None}
                interrupted = None
                with tempfile.TemporaryDirectory(prefix="lh003-artifacts-", dir="/tmp") as artifacts:
                    schema_path, output_path = Path(artifacts) / "schema.json", Path(artifacts) / "output.json"
                    schema_path.write_text(json.dumps(payload["schema"], ensure_ascii=False), encoding="utf-8")
                    command = ["codex", "exec", "-m", self.model, "-c", "model_reasoning_effort=medium",
                               "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--json",
                               "--output-schema", str(schema_path), "-o", str(output_path), "-"]
                    record["command"] = command
                    try:
                        with tempfile.TemporaryDirectory(prefix="lh003-empty-", dir="/tmp") as cwd:
                            if Path(cwd).resolve().is_relative_to(self.repo_root.resolve()) or list(Path(cwd).iterdir()):
                                raise Refused("model workspace must be empty and outside the repository")
                            result = self.process(command, cwd=cwd, input=prompt, capture_output=True, text=True,
                                                  timeout=min(self.timeout, max(0.001, (deadline - aware(self.clock())).total_seconds())),
                                                  check=False)
                        record.update(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)
                        summary, messages = inspect_events(result.stdout)
                        record["event_summary"] = summary
                        for line in result.stdout.splitlines():
                            event = json.loads(line)
                            for field, expected in (("model", MODEL), ("model_name", MODEL), ("codex_version", CODEX_VERSION)):
                                if isinstance(event, dict) and field in event and event[field] != expected:
                                    raise Refused(self.stop(field + " drift in model event"))
                        if result.returncode or summary["violations"]:
                            raise Refused("nonzero exit or forbidden/invalid events: " + str(summary["violations"]))
                        output = json.loads(output_path.read_text(encoding="utf-8")) if output_path.is_file() else None
                        if not messages or not isinstance(messages[-1], str) or json.loads(messages[-1]) != output:
                            raise Refused("missing or inconsistent final agent message")
                        validate(output, payload["schema"])
                        record.update(state="valid", output=output)
                    except subprocess.TimeoutExpired as exc:
                        record.update(error="timeout", stdout=_text(exc.stdout), stderr=_text(exc.stderr))
                    except (ValueError, ValidationError, OSError) as exc:
                        record["error"] = type(exc).__name__ + ": " + str(exc)
                    except BaseException as exc:
                        record.update(state="interrupted", error=type(exc).__name__ + ": " + str(exc))
                        interrupted = exc
                    finally:
                        record["output_raw"] = output_path.read_text(encoding="utf-8") if output_path.is_file() else None
                try:
                    self.identity()
                except (Refused, OSError, subprocess.TimeoutExpired) as exc:
                    record.update(state="invalid", output=None, error=self.stop(str(exc)))
                record.update(seconds=time.perf_counter() - started, finished_at=aware(self.clock()).isoformat())
                seal(self.root / "attempts" / f"{number:06d}.result.json", record)
                history.append({**reservation, **record})
                if interrupted is not None:
                    raise interrupted
                if (self.root / "model_stopped.json").exists():
                    return {"valid": False, "reason": "model_version_drift", "attempts": history, "output": None}
                if record["state"] == "valid":
                    return {"valid": True, "reason": None, "attempts": history, "output": record["output"]}
            return {"valid": False, "reason": "synthetic_format_invalid" if self.format_trial else "repeat_invalid_after_reask",
                    "attempts": history, "output": None}

    def decide(self, payload, *, week, deadline):
        if len(payload["id_mapping"]) < 40:
            return {"valid": False, "reasons": ["fewer_than_40_candidates"], "repeats": [], "selected": [], "avoid": []}
        repeats = [self.call(payload, repeat, week=week, deadline=deadline) for repeat in range(3)]
        if not all(row["valid"] for row in repeats):
            return {"valid": False, "reasons": sorted({row["reason"] for row in repeats if not row["valid"]}),
                    "repeats": repeats, "selected": [], "avoid": []}
        rankings = [row["output"]["ranked"] for row in repeats]
        candidates = aggregate(rankings, 30)
        bought = aggregate([r[:10] for r in rankings], 10, eligible=set(candidates))
        avoided = aggregate([r["output"]["avoid"] for r in repeats], 10,
                            eligible=set(payload["id_mapping"]) - set(bought))
        mapping = payload["id_mapping"]
        return {"valid": True, "reasons": [], "repeats": repeats, "ranked_ids": candidates,
                "selected": [mapping[x] for x in bought], "avoid": [mapping[x] for x in avoided],
                "repeat_selections": [[mapping[x] for x in r[:10]] for r in rankings], "jaccard": jaccard([r[:10] for r in rankings])}
