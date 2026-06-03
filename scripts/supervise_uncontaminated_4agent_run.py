#!/usr/bin/env python3
from __future__ import annotations

"""Watch and restart the uncontaminated 4-agent run until it completes."""

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = Path(
    "experiment_results/backtesting/agent_architecture_validation/"
    "uncontaminated_4agent_runs"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervise the uncontaminated 4-agent backtest.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--cache-file", default="")
    parser.add_argument("--stale-seconds", type=int, default=1800)
    parser.add_argument("--max-restarts", type=int, default=20)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--theme", default="AI")
    parser.add_argument("--theme-key", default="")
    parser.add_argument("--period-scope", default="representative_2024")
    parser.add_argument("--periods", default="")
    args = parser.parse_args()

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    theme_key = _safe(args.theme_key or _theme_key(args.theme))
    cache_file = Path(args.cache_file) if args.cache_file else output_root / "llm_cache" / f"{theme_key}.pure4agent.jsonl"
    log_path = output_root / "supervisor.log"
    state_path = output_root / "supervisor-state.json"

    restarts = 0
    while restarts <= args.max_restarts:
        command = [
            "caffeinate",
            "-dimsu",
            ".venv/bin/python",
            "scripts/run_uncontaminated_4agent_backtests.py",
            "--output-root",
            str(output_root),
            "--period-scope",
            args.period_scope,
            "--themes",
            args.theme,
            "--no-resume",
        ]
        if args.periods:
            command.extend(["--periods", args.periods])
        env = os.environ.copy()
        env["LLM_SCHEMA_RETRIES"] = env.get("LLM_SCHEMA_RETRIES", "2")
        env["LLM_SCHEMA_TIMEOUT_SECONDS"] = env.get("LLM_SCHEMA_TIMEOUT_SECONDS", "600")
        _append_log(log_path, f"START restart={restarts} cache_lines={_line_count(cache_file)} command={' '.join(command)}")
        with (output_root / "supervisor-child.log").open("ab") as child_log:
            process = subprocess.Popen(
                command,
                cwd=Path.cwd(),
                env=env,
                stdout=child_log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )

        last_cache_lines = _line_count(cache_file)
        last_progress_at = time.time()
        while True:
            returncode = process.poll()
            cache_lines = _line_count(cache_file)
            if cache_lines != last_cache_lines:
                last_cache_lines = cache_lines
                last_progress_at = time.time()
            no_progress_seconds = int(time.time() - last_progress_at)
            _write_state(
                state_path,
                {
                    "updated_at": _now(),
                    "pid": process.pid,
                    "returncode": returncode,
                    "restart_count": restarts,
                    "theme": args.theme,
                    "period_scope": args.period_scope,
                    "periods": args.periods,
                    "cache_lines": cache_lines,
                    "no_progress_seconds": no_progress_seconds,
                    "stale_seconds": args.stale_seconds,
                },
            )
            if returncode is not None:
                _append_log(log_path, f"EXIT rc={returncode} cache_lines={cache_lines}")
                if returncode == 0:
                    return 0
                break
            if no_progress_seconds > args.stale_seconds:
                _append_log(log_path, f"STALE kill pid={process.pid} cache_lines={cache_lines} no_progress={no_progress_seconds}s")
                _terminate_process_group(process.pid)
                _restart_ollama_runner()
                break
            time.sleep(max(5, args.poll_seconds))

        restarts += 1
        time.sleep(10)

    _append_log(log_path, f"FAILED max_restarts={args.max_restarts} cache_lines={_line_count(cache_file)}")
    return 1


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{_now()} {message}\n")


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _cache_age_seconds(path: Path) -> int | None:
    try:
        return int(time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _theme_key(theme: str) -> str:
    aliases = {
        "AI": "ai",
        "ai": "ai",
        "반도체": "반도체",
    }
    return aliases.get(str(theme), str(theme))


def _safe(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(5)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _restart_ollama_runner() -> None:
    subprocess.run(["pkill", "-f", "ollama runner"], check=False)
    time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
