#!/usr/bin/env python3
from __future__ import annotations

"""Run cache-only profile backtests for four-agent architecture evidence.

This runner reuses saved multi-agent cache files. It is designed to compare
agent score profiles without making new LLM calls.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List


OUT_ROOT = Path("experiment_results/backtesting/agent_architecture_validation/profile_backtest_runs")
DEFAULT_PROFILES = [
    "analyst_only",
    "quant_only",
    "chartist_only",
    "risk_manager_raw_only",
    "remove_analyst",
    "remove_quant",
    "remove_chartist",
    "current_hybrid_4agent",
    "three_agent_no_risk_manager",
    "four_agent_risk_adjusted",
    "four_agent_plus_liquidity",
]
THEME_CONFIGS = {
    "AI": {
        "theme": "AI",
        "theme_key": "ai",
        "cache_path": "experiment_results/backtesting/ai_strategy_comparison/llm_cache/ai_qwen3_14b.multi_agent.jsonl",
        "periods": (
            "validation_2023:20230101:20231231:validation,"
            "validation_2024:20240101:20241231:validation"
        ),
    },
    "반도체": {
        "theme": "반도체",
        "theme_key": "반도체",
        "cache_path": (
            "experiment_results/backtesting/remaining_theme_strategy_comparison/"
            "source_multi_agent_full_top10_qwen3/반도체/llm_cache/반도체.multi_agent.jsonl"
        ),
        "periods": (
            "validation_2023:20230101:20231231:validation,"
            "validation_2024:20240101:20241231:validation,"
            "tune_2025:20250101:20251231:tuning_reference,"
            "validation_2026q1:20260101:20260331:validation"
        ),
    },
}


@dataclass
class RunRecord:
    profile: str
    theme: str
    theme_key: str
    output_dir: str
    log_path: str
    command: List[str]
    started_at: str
    finished_at: str
    elapsed_seconds: float
    returncode: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Run targeted cache-only agent profile backtests.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-root", default=str(OUT_ROOT))
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--themes", default="AI,반도체")
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--market-impact-bps", type=float, default=5.0)
    parser.add_argument("--min-market-breadth-pct", type=float, default=40.0)
    parser.add_argument("--max-volatility-20d", type=float, default=1.2)
    parser.add_argument("--max-return-5d", type=float, default=0.35)
    parser.add_argument("--max-return-20d", type=float, default=0.9)
    parser.add_argument("--trailing-stop-pct", type=float, default=15.0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_root = Path(args.output_root)
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    profiles = _split(args.profiles)
    themes = _split(args.themes)
    records: List[RunRecord] = _load_existing_records(output_root / "manifest.json")

    for profile in profiles:
        for theme_name in themes:
            config = THEME_CONFIGS.get(theme_name)
            if not config:
                raise ValueError(f"unknown theme for profile runner: {theme_name}")
            records.append(_run_one(args, profile, config, output_root, logs_dir))
            _write_manifest(output_root, args, records)

    _write_manifest(output_root, args, records)
    return 0 if all(record.returncode == 0 for record in records) else 1


def _run_one(
    args: argparse.Namespace,
    profile: str,
    config: Dict[str, str],
    output_root: Path,
    logs_dir: Path,
) -> RunRecord:
    output_dir = output_root / profile / _safe(config["theme_key"]) / "multi_agent_proof"
    log_path = logs_dir / f"{profile}-{_safe(config['theme_key'])}.log"
    command = [
        sys.executable,
        "backtesting/proof_validation.py",
        "--data-dir",
        args.data_dir,
        "--theme",
        config["theme"],
        "--theme-key",
        config["theme_key"],
        "--output-dir",
        str(output_dir),
        "--periods",
        config["periods"],
        "--champion-only",
        "--short-top-k",
        "10",
        "--long-top-k",
        "10",
        "--transaction-cost-bps",
        str(args.transaction_cost_bps),
        "--slippage-bps",
        str(args.slippage_bps),
        "--market-impact-bps",
        str(args.market_impact_bps),
        "--min-market-breadth-pct",
        str(args.min_market_breadth_pct),
        "--max-volatility-20d",
        str(args.max_volatility_20d),
        "--max-return-5d",
        str(args.max_return_5d),
        "--max-return-20d",
        str(args.max_return_20d),
        "--trailing-stop-pct",
        str(args.trailing_stop_pct),
        "--llm-cache-path",
        config["cache_path"],
    ]
    if args.no_resume:
        command.append("--no-resume")

    env = os.environ.copy()
    env["LLM_PROVIDER"] = "ollama"
    env["OLLAMA_INSTRUCT_MODEL"] = env.get("OLLAMA_INSTRUCT_MODEL", "qwen3:14b")
    env["OLLAMA_THINKING_MODEL"] = env.get("OLLAMA_THINKING_MODEL", "gpt-oss:20b")
    # The shared LLM config now selects Ollama models per role. Keep the historical
    # instruct/thinking names as the source of truth and mirror them onto the role keys
    # unless the caller already set a role-specific model.
    for _role_key in ("OLLAMA_ANALYST_MODEL", "OLLAMA_QUANT_MODEL", "OLLAMA_CHARTIST_MODEL"):
        env.setdefault(_role_key, env["OLLAMA_INSTRUCT_MODEL"])
    env.setdefault("OLLAMA_RISK_MANAGER_MODEL", env["OLLAMA_THINKING_MODEL"])
    env["AGENT_SCORE_PROFILE"] = profile
    env["AGENT_SCORE_CACHE_ONLY"] = "1"

    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.time()
    if args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path.write_text("DRY RUN\n" + " ".join(command) + "\n", encoding="utf-8")
        return RunRecord(
            profile=profile,
            theme=config["theme"],
            theme_key=config["theme_key"],
            output_dir=str(output_dir),
            log_path=str(log_path),
            command=command,
            started_at=started,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            elapsed_seconds=0.0,
            returncode=0,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=env,
            text=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            check=False,
        )

    return RunRecord(
        profile=profile,
        theme=config["theme"],
        theme_key=config["theme_key"],
        output_dir=str(output_dir),
        log_path=str(log_path),
        command=command,
        started_at=started,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        elapsed_seconds=round(time.time() - t0, 2),
        returncode=process.returncode,
    )


def _write_manifest(output_root: Path, args: argparse.Namespace, records: List[RunRecord]) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "cache_only_proof_validation_with_agent_score_profiles",
        "cache_only": True,
        "data_dir": args.data_dir,
        "output_root": str(output_root),
        "profiles": _split(args.profiles),
        "themes": _split(args.themes),
        "costs": {
            "transaction_cost_bps": args.transaction_cost_bps,
            "slippage_bps": args.slippage_bps,
            "market_impact_bps": args.market_impact_bps,
            "round_trip_cost_bps": (args.transaction_cost_bps + args.slippage_bps + args.market_impact_bps) * 2.0,
        },
        "records": [asdict(record) for record in records],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_existing_records(path: Path) -> List[RunRecord]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = []
    for raw in payload.get("records") or []:
        if not isinstance(raw, dict):
            continue
        try:
            records.append(RunRecord(**raw))
        except TypeError:
            continue
    return records


def _split(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _safe(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


if __name__ == "__main__":
    raise SystemExit(main())
