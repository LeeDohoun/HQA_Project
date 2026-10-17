#!/usr/bin/env python3
from __future__ import annotations

"""Fresh LLM representative reruns for agent architecture evidence.

This runner makes new LLM calls once per theme to create a fresh multi-agent
cache, then reuses that same raw cache to compare single-agent, leave-one-out,
current four-agent, and added-agent score profiles.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


OUT_ROOT = Path("experiment_results/backtesting/agent_architecture_validation/fresh_representative_llm_runs")
SUMMARY_CSV = "ai_short_long_validation_summary.csv"

DEFAULT_PROFILES = [
    "current_hybrid_4agent",
    "analyst_only",
    "quant_only",
    "chartist_only",
    "risk_manager_raw_only",
    "remove_analyst",
    "remove_quant",
    "remove_chartist",
    "three_agent_no_risk_manager",
    "four_agent_plus_liquidity",
]

THEME_CONFIGS = {
    "AI": {
        "theme": "AI",
        "theme_key": "ai",
        "theme_existing_periods": (
            "validation_2023:20230101:20231231:validation,"
            "validation_2024:20240101:20241231:validation"
        ),
    },
    "반도체": {
        "theme": "반도체",
        "theme_key": "반도체",
        "theme_existing_periods": (
            "validation_2023:20230101:20231231:validation,"
            "validation_2024:20240101:20241231:validation,"
            "tune_2025:20250101:20251231:tuning_reference,"
            "validation_2026q1:20260101:20260331:validation"
        ),
    },
}

COMMON_PERIODS = {
    "representative_2024": "validation_2024:20240101:20241231:validation",
    "validation_2023_2024": (
        "validation_2023:20230101:20231231:validation,"
        "validation_2024:20240101:20241231:validation"
    ),
}


@dataclass
class RunRecord:
    stage: str
    profile: str
    theme: str
    theme_key: str
    periods: str
    cache_path: str
    output_dir: str
    log_path: str
    command: List[str]
    started_at: str
    finished_at: str
    elapsed_seconds: float
    returncode: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh representative LLM agent-architecture checks.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-root", default=str(OUT_ROOT))
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--themes", default="AI,반도체")
    parser.add_argument(
        "--period-scope",
        choices=["representative_2024", "validation_2023_2024", "theme_existing"],
        default="representative_2024",
        help="Use representative_2024 for a faster fresh sanity check, or theme_existing for each theme's full saved scope.",
    )
    parser.add_argument("--periods", default="", help="Optional custom name:from:to[:role] period list for all themes.")
    parser.add_argument("--short-top-k", type=int, default=10)
    parser.add_argument("--long-top-k", type=int, default=10)
    parser.add_argument("--transaction-cost-bps", type=float, default=15.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--market-impact-bps", type=float, default=5.0)
    parser.add_argument("--min-market-breadth-pct", type=float, default=40.0)
    parser.add_argument("--max-volatility-20d", type=float, default=1.2)
    parser.add_argument("--max-return-5d", type=float, default=0.35)
    parser.add_argument("--max-return-20d", type=float, default=0.9)
    parser.add_argument("--trailing-stop-pct", type=float, default=15.0)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.seed_only and args.profile_only:
        raise ValueError("--seed-only and --profile-only cannot be used together")

    output_root = Path(args.output_root)
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    records = _load_existing_records(output_root / "manifest.json")
    profiles = _split(args.profiles)
    themes = _split(args.themes)

    for theme_name in themes:
        config = THEME_CONFIGS.get(theme_name)
        if not config:
            raise ValueError(f"unknown theme: {theme_name}")
        periods = _periods_for(args, config)
        cache_path = output_root / "llm_cache" / f"{_safe(config['theme_key'])}.multi_agent.jsonl"

        if not args.profile_only:
            records.append(
                _run_one(
                    args=args,
                    config=config,
                    periods=periods,
                    cache_path=cache_path,
                    output_root=output_root,
                    logs_dir=logs_dir,
                    stage="fresh_seed",
                    profile="current_hybrid_4agent",
                    cache_only=False,
                )
            )
            _write_manifest(output_root, args, records)
            _write_summaries(output_root)

        if args.seed_only:
            continue

        for profile in profiles:
            records.append(
                _run_one(
                    args=args,
                    config=config,
                    periods=periods,
                    cache_path=cache_path,
                    output_root=output_root,
                    logs_dir=logs_dir,
                    stage="profile_compare",
                    profile=profile,
                    cache_only=True,
                )
            )
            _write_manifest(output_root, args, records)
            _write_summaries(output_root)

    _write_manifest(output_root, args, records)
    _write_summaries(output_root)
    return 0 if all(record.returncode == 0 for record in records) else 1


def _run_one(
    *,
    args: argparse.Namespace,
    config: Dict[str, str],
    periods: str,
    cache_path: Path,
    output_root: Path,
    logs_dir: Path,
    stage: str,
    profile: str,
    cache_only: bool,
) -> RunRecord:
    theme_key_safe = _safe(config["theme_key"])
    output_dir = (
        output_root / "raw_llm_seed" / theme_key_safe / "multi_agent_proof"
        if stage == "fresh_seed"
        else output_root / "profiles" / profile / theme_key_safe / "multi_agent_proof"
    )
    log_path = logs_dir / f"{stage}-{profile}-{theme_key_safe}.log"
    command = _proof_command(args, config, periods, output_dir, cache_path)

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
    if cache_only:
        env["AGENT_SCORE_CACHE_ONLY"] = "1"
    else:
        env.pop("AGENT_SCORE_CACHE_ONLY", None)

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.time()

    if args.dry_run:
        log_path.write_text("DRY RUN\n" + " ".join(command) + "\n", encoding="utf-8")
        return RunRecord(
            stage=stage,
            profile=profile,
            theme=config["theme"],
            theme_key=config["theme_key"],
            periods=periods,
            cache_path=str(cache_path),
            output_dir=str(output_dir),
            log_path=str(log_path),
            command=command,
            started_at=started,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            elapsed_seconds=0.0,
            returncode=0,
        )

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
        stage=stage,
        profile=profile,
        theme=config["theme"],
        theme_key=config["theme_key"],
        periods=periods,
        cache_path=str(cache_path),
        output_dir=str(output_dir),
        log_path=str(log_path),
        command=command,
        started_at=started,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        elapsed_seconds=round(time.time() - t0, 2),
        returncode=process.returncode,
    )


def _proof_command(
    args: argparse.Namespace,
    config: Dict[str, str],
    periods: str,
    output_dir: Path,
    cache_path: Path,
) -> List[str]:
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
        periods,
        "--champion-only",
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
        str(cache_path),
    ]
    if args.no_resume:
        command.append("--no-resume")
    return command


def _write_summaries(output_root: Path) -> None:
    run_rows = _load_profile_rows(output_root)
    summary_rows = _build_summary(run_rows, group_keys=("profile", "horizon"))
    theme_rows = _build_summary(run_rows, group_keys=("profile", "theme", "theme_key", "horizon"))
    _write_csv(output_root / "fresh-representative-profile-run-results.csv", run_rows)
    _write_csv(output_root / "fresh-representative-profile-summary.csv", summary_rows)
    _write_csv(output_root / "fresh-representative-profile-theme-summary.csv", theme_rows)
    (output_root / "FRESH_REPRESENTATIVE_LLM_VALIDATION_KO.md").write_text(
        _render_report(summary_rows, theme_rows),
        encoding="utf-8",
    )


def _load_profile_rows(output_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((output_root / "profiles").glob(f"*/*/multi_agent_proof/{SUMMARY_CSV}")):
        profile = path.parts[-4]
        theme_key = path.parts[-3]
        for raw in _read_csv(path):
            strategy_id = str(raw.get("strategy_id") or "")
            if strategy_id not in {"deterministic_short", "short_hybrid_05", "deterministic_long", "long_hybrid_05"}:
                continue
            is_baseline = _bool(raw.get("is_baseline"))
            rows.append(
                {
                    "profile": profile,
                    "theme": raw.get("task_id", "").split("-")[1] if raw.get("task_id") else theme_key,
                    "theme_key": theme_key,
                    "period": raw.get("period", ""),
                    "horizon": raw.get("horizon", ""),
                    "strategy_id": strategy_id,
                    "is_baseline": is_baseline,
                    "total_return_pct": _float(raw.get("total_return_pct")),
                    "benchmark_return_pct": _float(raw.get("benchmark_return_pct")),
                    "excess_return_pct": _float(raw.get("excess_return_pct")),
                    "mdd_pct": _float(raw.get("mdd_pct")),
                    "sharpe": _float(raw.get("sharpe")),
                    "win_rate_pct": _float(raw.get("win_rate_pct")),
                    "return_delta_vs_baseline_pct": _float(raw.get("return_delta_vs_baseline_pct")),
                    "excess_delta_vs_baseline_pct": _float(raw.get("excess_delta_vs_baseline_pct")),
                    "result_json": raw.get("result_json", ""),
                    "source_summary_csv": str(path),
                }
            )
    rows = _attach_current_deltas(rows)
    return rows


def _attach_current_deltas(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    current = {
        (
            row["theme_key"],
            row["period"],
            row["horizon"],
            row["strategy_id"],
            row["is_baseline"],
        ): row
        for row in rows
        if row["profile"] == "current_hybrid_4agent"
    }
    output = []
    for row in rows:
        key = (row["theme_key"], row["period"], row["horizon"], row["strategy_id"], row["is_baseline"])
        ref = current.get(key)
        updated = dict(row)
        updated["excess_delta_vs_current_pct"] = (
            round(row["excess_return_pct"] - ref["excess_return_pct"], 2) if ref else ""
        )
        updated["return_delta_vs_current_pct"] = (
            round(row["total_return_pct"] - ref["total_return_pct"], 2) if ref else ""
        )
        updated["mdd_delta_vs_current_pct"] = round(row["mdd_pct"] - ref["mdd_pct"], 2) if ref else ""
        output.append(updated)
    return output


def _build_summary(rows: List[Dict[str, Any]], *, group_keys: Iterable[str]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["is_baseline"]:
            continue
        if row["strategy_id"] not in {"short_hybrid_05", "long_hybrid_05"}:
            continue
        groups[tuple(row[key] for key in group_keys)].append(row)

    output: List[Dict[str, Any]] = []
    for key, grouped in sorted(groups.items()):
        base = dict(zip(group_keys, key))
        deltas = [_float(row["excess_delta_vs_current_pct"]) for row in grouped if row["excess_delta_vs_current_pct"] != ""]
        base.update(
            {
                "run_count": len(grouped),
                "theme_count": len({row["theme_key"] for row in grouped}),
                "period_count": len({(row["theme_key"], row["period"]) for row in grouped}),
                "avg_total_return_pct": round(_mean(row["total_return_pct"] for row in grouped), 2),
                "avg_benchmark_return_pct": round(_mean(row["benchmark_return_pct"] for row in grouped), 2),
                "avg_excess_return_pct": round(_mean(row["excess_return_pct"] for row in grouped), 2),
                "worst_mdd_pct": round(min(_float(row["mdd_pct"]) for row in grouped), 2),
                "avg_mdd_pct": round(_mean(row["mdd_pct"] for row in grouped), 2),
                "avg_sharpe": round(_mean(row["sharpe"] for row in grouped), 3),
                "avg_win_rate_pct": round(_mean(row["win_rate_pct"] for row in grouped), 2),
                "avg_excess_delta_vs_current_pct": round(_mean(deltas), 2) if deltas else 0.0,
            }
        )
        output.append(base)
    return output


def _render_report(summary_rows: List[Dict[str, Any]], theme_rows: List[Dict[str, Any]]) -> str:
    generated_at = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Fresh Representative LLM Validation",
        "",
        f"- generated_at: `{generated_at}`",
        "- 목적: 저장 캐시 재조합만이 아니라, 새 LLM 원점수를 만든 뒤 같은 조건에서 에이전트 단독/제거/추가 구조를 비교합니다.",
        "- 새 LLM 호출은 `fresh_seed` 단계에서 1회 수행하고, 이후 프로필 비교는 같은 원점수를 재사용합니다.",
        "- 비교 중심: 현재 4-agent, AI 하나만 쓰는 방식, 하나씩 제거한 방식, 에이전트 1개 추가 방식.",
        "",
        "## Overall Summary",
        "",
    ]
    lines.extend(_markdown_table(summary_rows, ["profile", "horizon", "run_count", "avg_excess_return_pct", "avg_excess_delta_vs_current_pct", "worst_mdd_pct", "avg_total_return_pct"]))
    lines.extend(
        [
            "",
            "## Theme Summary",
            "",
        ]
    )
    lines.extend(_markdown_table(theme_rows, ["profile", "theme_key", "horizon", "run_count", "avg_excess_return_pct", "avg_excess_delta_vs_current_pct", "worst_mdd_pct"]))
    lines.extend(
        [
            "",
            "## 읽는 법",
            "",
            "- `avg_excess_return_pct`: 시장 벤치마크 대비 평균 초과수익률입니다. 높을수록 좋습니다.",
            "- `avg_excess_delta_vs_current_pct`: 현재 4-agent 대비 초과수익률 차이입니다. 양수면 현재 4-agent보다 좋고, 음수면 나쁩니다.",
            "- `worst_mdd_pct`: 가장 나빴던 최대낙폭입니다. 0에 가까울수록 손실 방어가 좋습니다.",
            "- 단독/제거/추가 프로필은 모두 같은 fresh LLM 원점수를 사용하므로, 구조 차이만 비교하기 위한 실험입니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_table(rows: List[Dict[str, Any]], columns: List[str]) -> List[str]:
    if not rows:
        return ["_아직 완료된 프로필 비교 결과가 없습니다._"]
    output = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return output


def _write_manifest(output_root: Path, args: argparse.Namespace, records: List[RunRecord]) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "fresh_llm_seed_then_cache_only_profile_comparison",
        "fresh_seed_makes_new_llm_calls": True,
        "profile_comparison_cache_only": True,
        "data_dir": args.data_dir,
        "output_root": str(output_root),
        "profiles": _split(args.profiles),
        "themes": _split(args.themes),
        "period_scope": args.period_scope,
        "custom_periods": args.periods,
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


def _periods_for(args: argparse.Namespace, config: Dict[str, str]) -> str:
    if args.periods:
        return args.periods
    if args.period_scope == "theme_existing":
        return config["theme_existing_periods"]
    return COMMON_PERIODS[args.period_scope]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: List[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _split(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _safe(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


def _float(value: Any) -> float:
    try:
        if value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mean(values: Iterable[Any]) -> float:
    parsed = [_float(value) for value in values]
    return sum(parsed) / len(parsed) if parsed else 0.0


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


if __name__ == "__main__":
    raise SystemExit(main())
