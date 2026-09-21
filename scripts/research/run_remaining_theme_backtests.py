#!/usr/bin/env python3
from __future__ import annotations

"""Run remaining theme backtests with isolated outputs and per-command logs.

This script intentionally writes only under the chosen output directory. It does
not mutate source market/corpus data, and it runs one theme at a time to avoid
loading multiple local LLM jobs in parallel.
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
from typing import Iterable, List


DEFAULT_THEMES = [
    ("반도체", "반도체"),
    ("바이오", "바이오"),
    ("로봇", "로봇"),
    ("전력설비", "전력설비"),
    ("조선", "조선"),
    ("2차전지", "2차전지"),
    ("화장품", "화장품"),
]

DEFAULT_PERIODS = (
    "validation_2023:20230101:20231231:validation,"
    "validation_2024:20240101:20241231:validation,"
    "tune_2025:20250101:20251231:tuning_reference,"
    "validation_2026q1:20260101:20260331:validation,"
    "recent_2026apr_may:20260401:20260507:recent_check"
)

DEFAULT_TECH_PERIODS = (
    "full:20230101:20260331,"
    "2023:20230101:20231231,"
    "2024:20240101:20241231,"
    "2025:20250101:20251231,"
    "2026q1:20260101:20260331"
)

TECH_BASELINES = "rsi_oversold,bollinger_lower,rsi_ranked,bollinger_ranked,momentum_20d,vol_adjusted_momentum"


@dataclass
class CommandResult:
    theme: str
    theme_key: str
    stage: str
    command: List[str]
    log_path: str
    started_at: str
    finished_at: str
    returncode: int
    elapsed_seconds: float


def main() -> int:
    parser = argparse.ArgumentParser(description="Run multi-agent and technical baseline backtests for non-AI themes.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-root", default="")
    parser.add_argument(
        "--themes",
        default=",".join(f"{name}:{key}" for name, key in DEFAULT_THEMES),
        help="Comma-separated theme[:theme_key] list.",
    )
    parser.add_argument("--periods", default=DEFAULT_PERIODS)
    parser.add_argument("--technical-periods", default=DEFAULT_TECH_PERIODS)
    parser.add_argument("--short-top-k", type=int, default=10)
    parser.add_argument("--long-top-k", type=int, default=10)
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--market-impact-bps", type=float, default=5.0)
    parser.add_argument("--min-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--portfolio-value-krw", type=float, default=0.0)
    parser.add_argument("--max-position-pct-avg-trading-value", type=float, default=0.0)
    parser.add_argument("--min-market-breadth-pct", type=float, default=40.0)
    parser.add_argument("--max-volatility-20d", type=float, default=1.2)
    parser.add_argument("--max-return-5d", type=float, default=0.35)
    parser.add_argument("--max-return-20d", type=float, default=0.9)
    parser.add_argument("--trailing-stop-pct", type=float, default=15.0)
    parser.add_argument("--mock-llm", action="store_true", help="Use mock LLM for a fast plumbing check.")
    parser.add_argument(
        "--include-llm-only",
        action="store_true",
        help="Run the full AI-theme strategy set, including short_llm_only and long_llm_only. By default only champion hybrids are run.",
    )
    parser.add_argument("--skip-proof", action="store_true")
    parser.add_argument("--skip-technical", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_root = Path(args.output_root or _default_output_root())
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    if args.mock_llm:
        env["LLM_PROVIDER"] = "mock"
    else:
        env.setdefault("LLM_PROVIDER", "ollama")
        env["OLLAMA_INSTRUCT_MODEL"] = env.get("OLLAMA_INSTRUCT_MODEL", "qwen3:14b")
        env["OLLAMA_THINKING_MODEL"] = env.get("OLLAMA_THINKING_MODEL", "gpt-oss:20b")
        # The shared LLM config now selects Ollama models per role. Keep the historical
        # instruct/thinking names as the source of truth and mirror them onto the role keys
        # unless the caller already set a role-specific model.
        for _role_key in ("OLLAMA_ANALYST_MODEL", "OLLAMA_QUANT_MODEL", "OLLAMA_CHARTIST_MODEL"):
            env.setdefault(_role_key, env["OLLAMA_INSTRUCT_MODEL"])
        env.setdefault("OLLAMA_RISK_MANAGER_MODEL", env["OLLAMA_THINKING_MODEL"])

    manifest = _load_manifest(output_root / "manifest.json")
    previous_results = manifest.get("results") if isinstance(manifest.get("results"), list) else []
    manifest.update(
        {
            "generated_at": manifest.get("generated_at") or datetime.now().isoformat(timespec="seconds"),
            "data_dir": args.data_dir,
            "output_root": str(output_root),
            "themes": [{"theme": theme, "theme_key": key} for theme, key in _parse_themes(args.themes)],
        }
    )
    manifest["protocol"] = {
            "periods": args.periods,
            "technical_periods": args.technical_periods,
            "short_top_k": args.short_top_k,
            "long_top_k": args.long_top_k,
            "transaction_cost_bps": args.transaction_cost_bps,
            "slippage_bps": args.slippage_bps,
            "market_impact_bps": args.market_impact_bps,
            "round_trip_cost_bps": (args.transaction_cost_bps + args.slippage_bps + args.market_impact_bps) * 2.0,
            "min_market_breadth_pct": args.min_market_breadth_pct,
            "max_volatility_20d": args.max_volatility_20d,
            "max_return_5d": args.max_return_5d,
            "max_return_20d": args.max_return_20d,
            "trailing_stop_pct": args.trailing_stop_pct,
            "mock_llm": args.mock_llm,
            "include_llm_only": args.include_llm_only,
    }
    manifest["results"] = previous_results
    _write_json(output_root / "manifest.json", manifest)

    command_results: List[CommandResult] = [_command_result_from_dict(item) for item in previous_results if isinstance(item, dict)]
    for theme, theme_key in _parse_themes(args.themes):
        theme_dir = output_root / _safe_name(theme_key)
        theme_dir.mkdir(parents=True, exist_ok=True)

        if not args.skip_proof:
            proof_cmd = _proof_command(args, theme, theme_key, theme_dir)
            command_results.append(_run_logged(proof_cmd, env, logs_dir / f"{_safe_name(theme_key)}-multi-agent-proof.log", theme, theme_key, "multi_agent_proof", args.dry_run))
            _update_manifest(output_root, manifest, command_results)

        if not args.skip_technical:
            for stage, rebalance, hold_days in [
                ("technical_short", "W", "5"),
                ("technical_long", "M", "60"),
            ]:
                tech_cmd = _technical_command(args, theme, theme_key, theme_dir, rebalance, hold_days)
                command_results.append(_run_logged(tech_cmd, env, logs_dir / f"{_safe_name(theme_key)}-{stage}.log", theme, theme_key, stage, args.dry_run))
                _update_manifest(output_root, manifest, command_results)

    _update_manifest(output_root, manifest, command_results)
    return 0 if all(item.returncode == 0 for item in command_results) else 1


def _proof_command(args: argparse.Namespace, theme: str, theme_key: str, theme_dir: Path) -> List[str]:
    return [
        sys.executable,
        "backtesting/proof_validation.py",
        "--data-dir",
        args.data_dir,
        "--theme",
        theme,
        "--theme-key",
        theme_key,
        "--output-dir",
        str(theme_dir / "multi_agent_proof"),
        "--preset",
        "extended",
        "--periods",
        args.periods,
        "--short-top-k",
        str(args.short_top_k),
        "--long-top-k",
        str(args.long_top_k),
        "--transaction-cost-bps",
        str(args.transaction_cost_bps),
        "--slippage-bps",
        str(args.slippage_bps),
        "--market-impact-bps",
        str(args.market_impact_bps),
        "--min-avg-trading-value",
        str(args.min_avg_trading_value),
        "--portfolio-value-krw",
        str(args.portfolio_value_krw),
        "--max-position-pct-avg-trading-value",
        str(args.max_position_pct_avg_trading_value),
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
        str(theme_dir / "llm_cache" / f"{_safe_name(theme_key)}.multi_agent.jsonl"),
    ] + ([] if args.include_llm_only else ["--champion-only"]) + (["--mock-llm"] if args.mock_llm else [])


def _technical_command(args: argparse.Namespace, theme: str, theme_key: str, theme_dir: Path, rebalance: str, hold_days: str) -> List[str]:
    return [
        sys.executable,
        "backtesting/technical_baseline.py",
        "--data-dir",
        args.data_dir,
        "--theme",
        theme,
        "--theme-key",
        theme_key,
        "--output-dir",
        str(theme_dir / f"technical_{rebalance.lower()}_h{hold_days}"),
        "--periods",
        args.technical_periods,
        "--baselines",
        TECH_BASELINES,
        "--rebalance",
        rebalance,
        "--top-n",
        "3",
        "--hold-days",
        hold_days,
        "--transaction-cost-bps",
        str(args.transaction_cost_bps),
        "--slippage-bps",
        str(args.slippage_bps),
        "--market-impact-bps",
        str(args.market_impact_bps),
        "--min-avg-trading-value",
        str(args.min_avg_trading_value),
        "--portfolio-value-krw",
        str(args.portfolio_value_krw),
        "--max-position-pct-avg-trading-value",
        str(args.max_position_pct_avg_trading_value),
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
    ]


def _run_logged(
    command: List[str],
    env: dict[str, str],
    log_path: Path,
    theme: str,
    theme_key: str,
    stage: str,
    dry_run: bool,
) -> CommandResult:
    started = datetime.now()
    print(f"[RUN] {theme_key} {stage}", flush=True)
    print("[CMD] " + " ".join(command), flush=True)
    if dry_run:
        return CommandResult(theme, theme_key, stage, command, str(log_path), started.isoformat(timespec="seconds"), started.isoformat(timespec="seconds"), 0, 0.0)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n# START {started.isoformat(timespec='seconds')} {theme_key} {stage}\n")
        log.write(" ".join(command) + "\n\n")
        log.flush()
        completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        finished = datetime.now()
        elapsed = (finished - started).total_seconds()
        log.write(f"\n# END {finished.isoformat(timespec='seconds')} returncode={completed.returncode} elapsed_seconds={elapsed:.1f}\n")
    print(f"[DONE] {theme_key} {stage} rc={completed.returncode} elapsed={elapsed:.1f}s log={log_path}", flush=True)
    return CommandResult(theme, theme_key, stage, command, str(log_path), started.isoformat(timespec="seconds"), finished.isoformat(timespec="seconds"), completed.returncode, elapsed)


def _update_manifest(output_root: Path, manifest: dict, results: Iterable[CommandResult]) -> None:
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest["results"] = [asdict(item) for item in results]
    _write_json(output_root / "manifest.json", manifest)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _command_result_from_dict(payload: dict) -> CommandResult:
    return CommandResult(
        theme=str(payload.get("theme") or ""),
        theme_key=str(payload.get("theme_key") or ""),
        stage=str(payload.get("stage") or ""),
        command=[str(item) for item in payload.get("command", [])],
        log_path=str(payload.get("log_path") or ""),
        started_at=str(payload.get("started_at") or ""),
        finished_at=str(payload.get("finished_at") or ""),
        returncode=int(payload.get("returncode") or 0),
        elapsed_seconds=float(payload.get("elapsed_seconds") or 0.0),
    )


def _parse_themes(raw: str) -> List[tuple[str, str]]:
    themes: List[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            theme, theme_key = item.split(":", 1)
        else:
            theme = theme_key = item
        themes.append((theme.strip(), theme_key.strip()))
    return themes


def _safe_name(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw.strip()) or "theme"


def _default_output_root() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"data/backtest_results/theme_expansion_{stamp}"


if __name__ == "__main__":
    raise SystemExit(main())
