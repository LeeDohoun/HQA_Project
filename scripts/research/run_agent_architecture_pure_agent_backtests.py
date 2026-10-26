#!/usr/bin/env python3
from __future__ import annotations

"""Pure agent architecture ablation backtests from saved multi-agent caches.

This runner is intentionally cache-only. It reuses raw Analyst/Quant/Chartist/
RiskManager scores from the fresh representative LLM run, then compares score
profiles with llm_weight=1.0. Rule-based logic is kept only as the candidate
universe and risk/safety filter, not mixed into the final ranking score.
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


FRESH_CACHE_ROOT = Path(
    "experiment_results/backtesting/agent_architecture_validation/"
    "fresh_representative_llm_runs/llm_cache"
)
OUT_ROOT = Path(
    "experiment_results/backtesting/agent_architecture_validation/"
    "pure_agent_ablation_runs"
)
SUMMARY_CSV = "ai_short_long_validation_summary.csv"
LLM_ONLY_STRATEGIES = {"short_llm_only", "long_llm_only"}
BASELINE_STRATEGIES = {"deterministic_short", "deterministic_long"}

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
    "four_agent_raw_blend",
    "four_agent_risk_adjusted",
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
    parser = argparse.ArgumentParser(description="Run pure LLM-only agent architecture ablations.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-root", default=str(OUT_ROOT))
    parser.add_argument("--source-cache-root", default=str(FRESH_CACHE_ROOT))
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--themes", default="AI,반도체")
    parser.add_argument(
        "--period-scope",
        choices=["representative_2024", "validation_2023_2024", "theme_existing"],
        default="theme_existing",
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
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_root = Path(args.output_root)
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    records: List[RunRecord] = []
    profiles = _split(args.profiles)
    themes = _split(args.themes)

    for theme_name in themes:
        config = THEME_CONFIGS.get(theme_name)
        if not config:
            raise ValueError(f"unknown theme: {theme_name}")
        periods = _periods_for(args, config)
        cache_path = Path(args.source_cache_root) / f"{_safe(config['theme_key'])}.multi_agent.jsonl"
        if not cache_path.exists():
            raise FileNotFoundError(f"missing source cache: {cache_path}")

        for profile in profiles:
            records.append(
                _run_one(
                    args=args,
                    config=config,
                    periods=periods,
                    cache_path=cache_path,
                    output_root=output_root,
                    logs_dir=logs_dir,
                    profile=profile,
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
    profile: str,
) -> RunRecord:
    theme_key_safe = _safe(config["theme_key"])
    output_dir = output_root / "profiles" / profile / theme_key_safe / "multi_agent_proof"
    log_path = logs_dir / f"pure_agent-{profile}-{theme_key_safe}.log"
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
    env["AGENT_SCORE_CACHE_ONLY"] = "1"
    env["AGENT_DISABLE_SHORT_CHARTIST_FLOOR"] = "1"

    output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.time()

    if args.dry_run:
        log_path.write_text("DRY RUN\n" + " ".join(command) + "\n", encoding="utf-8")
        return RunRecord(
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
        "--strategies",
        "deterministic_short,short_llm_only,deterministic_long,long_llm_only",
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
    theme_rows = _build_summary(run_rows, group_keys=("profile", "theme_key", "horizon"))
    _write_csv(output_root / "pure-agent-profile-run-results.csv", run_rows)
    _write_csv(output_root / "pure-agent-profile-summary.csv", summary_rows)
    _write_csv(output_root / "pure-agent-profile-theme-summary.csv", theme_rows)
    (output_root / "PURE_AGENT_ABLATION_EVIDENCE_KO.md").write_text(
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
            if strategy_id not in LLM_ONLY_STRATEGIES | BASELINE_STRATEGIES:
                continue
            rows.append(
                {
                    "profile": profile,
                    "theme_key": theme_key,
                    "period": raw.get("period", ""),
                    "horizon": raw.get("horizon", ""),
                    "strategy_id": strategy_id,
                    "is_baseline": _bool(raw.get("is_baseline")),
                    "llm_weight": _float(raw.get("llm_weight")),
                    "llm_rerank_top_k": _float(raw.get("llm_rerank_top_k")),
                    "rebalance_count": _float(raw.get("rebalance_count")),
                    "traded_rebalance_count": _float(raw.get("traded_rebalance_count")),
                    "position_count": _float(raw.get("position_count")),
                    "total_return_pct": _float(raw.get("total_return_pct")),
                    "benchmark_return_pct": _float(raw.get("benchmark_return_pct")),
                    "excess_return_pct": _float(raw.get("excess_return_pct")),
                    "mdd_pct": _float(raw.get("mdd_pct")),
                    "sharpe": _float(raw.get("sharpe")),
                    "win_rate_pct": _float(raw.get("win_rate_pct")),
                    "loss_period_count": _float(raw.get("loss_period_count")),
                    "max_consecutive_loss_periods": _float(raw.get("max_consecutive_loss_periods")),
                    "return_delta_vs_baseline_pct": _float(raw.get("return_delta_vs_baseline_pct")),
                    "excess_delta_vs_baseline_pct": _float(raw.get("excess_delta_vs_baseline_pct")),
                    "mdd_delta_vs_baseline_pct": _float(raw.get("mdd_delta_vs_baseline_pct")),
                    "win_vs_baseline": _bool(raw.get("win_vs_baseline")),
                    "result_json": raw.get("result_json", ""),
                    "source_summary_csv": str(path),
                }
            )
    return _attach_current_deltas(rows)


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
        if row["strategy_id"] not in LLM_ONLY_STRATEGIES:
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
                "avg_return_delta_vs_rule_pct": round(_mean(row["return_delta_vs_baseline_pct"] for row in grouped), 2),
                "avg_excess_delta_vs_rule_pct": round(_mean(row["excess_delta_vs_baseline_pct"] for row in grouped), 2),
                "worst_mdd_pct": round(min(_float(row["mdd_pct"]) for row in grouped), 2),
                "avg_mdd_pct": round(_mean(row["mdd_pct"] for row in grouped), 2),
                "avg_mdd_delta_vs_rule_pct": round(_mean(row["mdd_delta_vs_baseline_pct"] for row in grouped), 2),
                "avg_sharpe": round(_mean(row["sharpe"] for row in grouped), 3),
                "avg_win_rate_pct": round(_mean(row["win_rate_pct"] for row in grouped), 2),
                "win_vs_rule_count": sum(1 for row in grouped if row["win_vs_baseline"]),
                "avg_excess_delta_vs_current_pct": round(_mean(deltas), 2) if deltas else 0.0,
            }
        )
        output.append(base)
    return output


def _render_report(summary_rows: List[Dict[str, Any]], theme_rows: List[Dict[str, Any]]) -> str:
    generated_at = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Pure Agent Architecture Ablation Evidence",
        "",
        f"- generated_at: `{generated_at}`",
        "- 목적: 현재 4-agent 구성이 왜 필요한지 보기 위해 AI 하나만 쓰는 방식, 하나씩 제거한 방식, 에이전트를 추가한 방식을 비교합니다.",
        "- 핵심 조건: `short_llm_only` / `long_llm_only`, `llm_weight=1.0`입니다. 즉 최종 순위에는 규칙기반 점수를 섞지 않습니다.",
        "- 규칙기반은 후보군 top10 생성과 리스크 필터에만 남겨 두었습니다.",
        "- 단타 실험에서는 기존 `Chartist floor` 보정을 꺼서 특정 에이전트 점수가 몰래 끌어올리는 효과를 제거했습니다.",
        "- LLM 호출은 새로 하지 않고 fresh representative run의 동일 원점수 캐시를 재사용했습니다.",
        "",
        "## Overall Summary",
        "",
    ]
    lines.extend(
        _markdown_table(
            summary_rows,
            [
                "profile",
                "horizon",
                "run_count",
                "avg_excess_return_pct",
                "avg_excess_delta_vs_current_pct",
                "avg_excess_delta_vs_rule_pct",
                "worst_mdd_pct",
                "avg_total_return_pct",
            ],
        )
    )
    lines.extend(["", "## Theme Summary", ""])
    lines.extend(
        _markdown_table(
            theme_rows,
            [
                "profile",
                "theme_key",
                "horizon",
                "run_count",
                "avg_excess_return_pct",
                "avg_excess_delta_vs_current_pct",
                "avg_excess_delta_vs_rule_pct",
                "worst_mdd_pct",
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 읽는 법",
            "",
            "- `avg_excess_return_pct`: 시장 벤치마크 대비 평균 초과수익률입니다. 높을수록 좋습니다.",
            "- `avg_excess_delta_vs_current_pct`: 현재 4-agent 점수 프로필 대비 차이입니다. 음수면 현재 구성이 더 좋았다는 뜻입니다.",
            "- `avg_excess_delta_vs_rule_pct`: 규칙기반 baseline 대비 차이입니다. 양수면 순수 agent 순위가 규칙기반보다 좋았습니다.",
            "- `worst_mdd_pct`: 가장 나빴던 최대낙폭입니다. 0에 가까울수록 손실 방어가 좋습니다.",
            "- 이 결과는 구조 비교용입니다. 실제 운용 판단은 이후 완전 fresh LLM 재실행과 더 넓은 테마 검증을 붙여야 합니다.",
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
        "method": "cache_only_pure_agent_ablation_no_rule_mixing_no_short_chartist_floor",
        "fresh_seed_makes_new_llm_calls": False,
        "profile_comparison_cache_only": True,
        "final_ranking_llm_weight": 1.0,
        "rule_based_role": "candidate_top10_and_risk_filter_only",
        "short_chartist_floor_disabled": True,
        "data_dir": args.data_dir,
        "output_root": str(output_root),
        "source_cache_root": args.source_cache_root,
        "profiles": _split(args.profiles),
        "themes": _split(args.themes),
        "period_scope": args.period_scope,
        "custom_periods": args.periods,
        "records": [asdict(record) for record in records],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
