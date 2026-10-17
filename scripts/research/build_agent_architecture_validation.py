#!/usr/bin/env python3
from __future__ import annotations

"""Build offline evidence for the four-agent architecture.

The script does not call any LLM. It reuses saved top-10 candidate rankings and
the multi-agent cache to test whether Analyst/Quant/Chartist/RiskManager
combinations would have selected better top-3 candidates.
"""

import csv
import json
import math
import sys
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


OUT_DIR = Path("experiment_results/backtesting/agent_architecture_validation")
SOURCE_ROOT = Path(".")
# A run enters agent comparisons only when this share of its candidate rows has
# exactly joined multi-agent scores; rows without scores are never filled in.
MIN_AGENT_COVERAGE = 0.9
PROFILE_RUN_ROOT = OUT_DIR / "profile_backtest_runs"
RUN_SOURCES = [
    # Archived copies kept in the repository (see research/README.md).
    Path("research/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/runs"),
    # Original experiment roots (local, not tracked by git).
    Path("experiment_results/backtesting/remaining_theme_strategy_comparison/source_multi_agent_full_top10_qwen3/반도체/multi_agent_proof/runs"),
    Path("experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/runs"),
    Path("experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs/runs"),
]
STRATEGY_IDS = {"short_hybrid_05", "long_hybrid_05"}
CORE_VARIANTS = {
    "current_hybrid_4agent",
    "three_agent_no_risk_manager",
    "four_agent_risk_adjusted",
    "four_agent_plus_liquidity",
    "remove_chartist",
    "deterministic_only",
    "risk_manager_raw_only",
}
ALL_CANDIDATE_THEME_KEY = "반도체"
ALL_CANDIDATE_PROFILES = {
    "analyst_only",
    "quant_only",
    "chartist_only",
    "risk_manager_raw_only",
    "remove_analyst",
    "remove_quant",
    "remove_chartist",
    "three_agent_no_risk_manager",
    "current_hybrid_4agent",
    "four_agent_raw_blend",
    "four_agent_risk_adjusted",
    "four_agent_plus_liquidity",
}
REPRESENTATIVE_PROFILES = {
    "analyst_only",
    "quant_only",
    "chartist_only",
    "risk_manager_raw_only",
    "remove_analyst",
    "remove_quant",
    "remove_chartist",
    "three_agent_no_risk_manager",
    "current_hybrid_4agent",
    "four_agent_risk_adjusted",
    "four_agent_plus_liquidity",
}
REPRESENTATIVE_THEME_KEYS = {"ai", "반도체"}
ABLATION_VARIANTS = {
    "deterministic_only": {"agent_count": 0, "label": "규칙 기반", "group": "baseline"},
    "analyst_only": {"agent_count": 1, "label": "Analyst 단독", "group": "single"},
    "quant_only": {"agent_count": 1, "label": "Quant 단독", "group": "single"},
    "chartist_only": {"agent_count": 1, "label": "Chartist 단독", "group": "single"},
    "risk_manager_raw_only": {"agent_count": 1, "label": "RiskManager 단독", "group": "single"},
    "analyst_quant": {"agent_count": 2, "label": "Analyst+Quant", "group": "pair"},
    "analyst_chartist": {"agent_count": 2, "label": "Analyst+Chartist", "group": "pair"},
    "quant_chartist": {"agent_count": 2, "label": "Quant+Chartist", "group": "pair"},
    "three_agent_no_risk_manager": {"agent_count": 3, "label": "3-agent", "group": "three_agent"},
    # Variants with a rule-based factor have no agent count: the hybrid is the run's own
    # deterministic+LLM leader_score, and the liquidity variant adds a liquidity factor.
    "current_hybrid_4agent": {"agent_count": None, "label": "현재 하이브리드(규칙+4-agent)", "group": "with_rule_factor"},
    "four_agent_raw_blend": {"agent_count": 4, "label": "4-agent 원점수 혼합", "group": "four_agent"},
    "four_agent_risk_adjusted": {"agent_count": 4, "label": "4-agent 위험보정", "group": "four_agent"},
    "four_agent_plus_liquidity": {"agent_count": None, "label": "3-agent+유동성 요인", "group": "with_rule_factor"},
}
# (full variant, reduced variant, agent). The hybrid ranking actually used by the
# runs mixes in the deterministic score, so it is not a baseline for agent removal:
# comparing it with the 3-agent blend measured removing the deterministic score.
LEAVE_ONE_OUT_VARIANTS = [
    ("three_agent_no_risk_manager", "remove_analyst", "Analyst"),
    ("three_agent_no_risk_manager", "remove_quant", "Quant"),
    ("three_agent_no_risk_manager", "remove_chartist", "Chartist"),
    ("four_agent_raw_blend", "three_agent_no_risk_manager", "RiskManager"),
]
SHORT_WEIGHTS = {"analyst": 0.30, "quant": 0.15, "chartist": 0.55}
LONG_WEIGHTS = {"analyst": 0.45, "quant": 0.40, "chartist": 0.15}


@dataclass(frozen=True)
class RunContext:
    path: Path
    theme: str
    theme_key: str
    period: str
    horizon: str
    strategy_id: str
    cache_path: Path | None
    round_trip_cost_pct: float
    llm_identity: tuple = ()
    regime: tuple = ()


def main(argv: List[str] | None = None) -> int:
    global OUT_DIR, PROFILE_RUN_ROOT, RUN_SOURCES, SOURCE_ROOT, MIN_AGENT_COVERAGE
    import argparse

    parser = argparse.ArgumentParser(description="Aggregate saved multi-agent runs into agent ablation evidence.")
    parser.add_argument("--source-root", default=".", help="Directory that contains experiment_results/ (default: cwd)")
    parser.add_argument("--output-dir", default="", help="Where to write the evidence (default: <source-root>/" + str(OUT_DIR) + ")")
    parser.add_argument("--min-agent-coverage", type=float, default=MIN_AGENT_COVERAGE)
    args = parser.parse_args(argv)
    SOURCE_ROOT = Path(args.source_root)
    RUN_SOURCES = [path if path.is_absolute() else SOURCE_ROOT / path for path in RUN_SOURCES]
    PROFILE_RUN_ROOT = SOURCE_ROOT / PROFILE_RUN_ROOT if not PROFILE_RUN_ROOT.is_absolute() else PROFILE_RUN_ROOT
    OUT_DIR = Path(args.output_dir) if args.output_dir else SOURCE_ROOT / OUT_DIR
    MIN_AGENT_COVERAGE = args.min_agent_coverage
    run_paths = _discover_runs()
    if not run_paths:
        print("ERROR: no source runs found under " + ", ".join(str(path) for path in RUN_SOURCES), file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_rows: List[Dict[str, Any]] = []
    period_rows: List[Dict[str, Any]] = []
    coverage_rows: List[Dict[str, Any]] = []

    seen_tasks = set()
    for run_path in run_paths:
        result = _load_json(run_path)
        task_id = str(result.get("task_id") or run_path.stem)
        if task_id in seen_tasks:
            continue  # an archived copy and its original are one run (archive listed first)
        seen_tasks.add(task_id)
        ctx = _run_context(run_path, result)
        cache = _load_agent_cache(ctx.cache_path)
        periods = list(result.get("periods") or [])
        ablated = _evaluate_run(ctx, periods, cache)
        run_rows.extend(ablated["run_rows"])
        period_rows.extend(ablated["period_rows"])
        coverage_rows.append(ablated["coverage"])

    summary_rows = _build_summary(run_rows)
    profile_run_rows = _load_profile_run_rows(PROFILE_RUN_ROOT)
    profile_summary_rows = _build_profile_summary(profile_run_rows)
    profile_theme_summary_rows = _build_profile_theme_summary(profile_run_rows)
    ablation_count_rows = _build_ablation_count_summary(summary_rows)
    ablation_leave_one_out_rows = _build_leave_one_out_summary(summary_rows)
    exploration_rows = _filter_profile_theme_summary(
        profile_theme_summary_rows,
        profiles=ALL_CANDIDATE_PROFILES,
        theme_key=ALL_CANDIDATE_THEME_KEY,
    )
    representative_rows = _filter_profile_summary(
        profile_summary_rows,
        profiles=REPRESENTATIVE_PROFILES,
        required_theme_keys=REPRESENTATIVE_THEME_KEYS,
    )

    _write_csv(OUT_DIR / "agent-architecture-run-results.csv", run_rows)
    _write_csv(OUT_DIR / "agent-architecture-period-results.csv", period_rows)
    _write_csv(OUT_DIR / "agent-architecture-summary.csv", summary_rows)
    _write_csv(OUT_DIR / "profile-backtest-run-results.csv", profile_run_rows)
    _write_csv(OUT_DIR / "profile-backtest-summary.csv", profile_summary_rows)
    _write_csv(OUT_DIR / "profile-theme-summary.csv", profile_theme_summary_rows)
    _write_csv(OUT_DIR / "exploration-all-candidates-semiconductor.csv", exploration_rows)
    _write_csv(OUT_DIR / "representative-two-theme-summary.csv", representative_rows)
    _write_csv(OUT_DIR / "agent-count-ablation-summary.csv", ablation_count_rows)
    _write_csv(OUT_DIR / "leave-one-out-agent-summary.csv", ablation_leave_one_out_rows)
    _write_csv(OUT_DIR / "agent-architecture-coverage.csv", coverage_rows)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "offline_candidate_rerank_from_saved_candidate_rankings_and_multi_agent_cache",
        "sources": [str(path) for path in RUN_SOURCES],
        "run_count": len({row["run_id"] for row in run_rows}),
        "period_count": len(period_rows),
        "summary_rows": summary_rows,
        "profile_summary_rows": profile_summary_rows,
        "profile_theme_summary_rows": profile_theme_summary_rows,
        "exploration_rows": exploration_rows,
        "representative_rows": representative_rows,
        "ablation_count_rows": ablation_count_rows,
        "ablation_leave_one_out_rows": ablation_leave_one_out_rows,
        "coverage_rows": coverage_rows,
        "limitations": [
            "This is an offline rerank experiment, not a fresh LLM re-run.",
            "Only saved top-10 candidate_rankings are considered, so candidates outside the saved audit pool cannot be selected.",
            "AI 2025/2026 runs without preserved cache are kept in coverage but excluded from agent-combination reranking.",
            "RiskManager raw score is tested as a ranking signal; the current production backtest mainly uses calibrated Analyst/Quant/Chartist weights.",
        ],
    }
    (OUT_DIR / "agent-architecture-validation.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT_DIR / "AGENT_ABLATION_EVIDENCE_KO.md").write_text(
        _render_agent_ablation_report(
            summary_rows,
            ablation_count_rows,
            ablation_leave_one_out_rows,
            profile_summary_rows,
            coverage_rows,
            exploration_rows,
            representative_rows,
        ),
        encoding="utf-8",
    )

    print(f"wrote {OUT_DIR / 'AGENT_ABLATION_EVIDENCE_KO.md'}")
    print(f"wrote {OUT_DIR / 'exploration-all-candidates-semiconductor.csv'}")
    print(f"wrote {OUT_DIR / 'representative-two-theme-summary.csv'}")
    included = sum(1 for row in coverage_rows if row["included_for_agent_ablation"])
    if not included:
        print(f"ERROR: no run reached the {MIN_AGENT_COVERAGE:.0%} agent-score coverage; see agent-architecture-coverage.csv",
              file=sys.stderr)
        return 3
    return 0


def _discover_runs() -> List[Path]:
    paths: List[Path] = []
    for source in RUN_SOURCES:
        if not source.exists():
            continue
        for path in sorted(source.glob("proof-*-*.json")):
            if any(path.name.endswith(f"{strategy}.json") for strategy in STRATEGY_IDS):
                paths.append(path)
    return paths


def _run_context(path: Path, result: Dict[str, Any]) -> RunContext:
    strategy_id = str((result.get("strategy") or {}).get("strategy_id") or _strategy_from_name(path.name))
    horizon = str((result.get("strategy") or {}).get("horizon") or _horizon_from_strategy(strategy_id))
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    llm_meta = metadata.get("llm") if isinstance(metadata.get("llm"), dict) else {}
    raw_cache = str(llm_meta.get("cache_path") or "")
    cache_path = (SOURCE_ROOT / raw_cache if raw_cache and raw_cache != "llm_cache_not_preserved_experiment_result_only"
                  else None)
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    costs = execution.get("costs") if isinstance(execution.get("costs"), dict) else {}
    # An explicit 0 bps cost is a real setting; only a missing value falls back to 50 bps.
    raw_cost = costs.get("round_trip_cost_bps")
    round_trip_cost_pct = (float(raw_cost) if raw_cost is not None else 50.0) / 100.0
    identity_fields = ("prompt_version", "provider", "model_name", "thinking_model_name")
    llm_identity = (tuple(str(llm_meta.get(name) or "") for name in identity_fields)
                    if all(llm_meta.get(name) for name in identity_fields) else ())
    return RunContext(
        regime=_expected_regime(llm_meta),
        path=path,
        theme=str(result.get("theme") or ""),
        theme_key=str(result.get("theme_key") or ""),
        period=_period_from_task_id(str(result.get("task_id") or path.stem), strategy_id),
        horizon=horizon,
        strategy_id=strategy_id,
        cache_path=cache_path,
        round_trip_cost_pct=round_trip_cost_pct,
        llm_identity=llm_identity,
    )


def _expected_regime(llm_meta: Dict[str, Any]) -> tuple:
    """The cache-key settings suffix a run's settings produce, as in
    TemporalMultiAgentStockScorer._regime_parts. Keys before prompt v4 carry none, so
    those runs cannot be checked against their settings (reported as a limitation)."""
    if _prompt_generation(llm_meta) < 4:
        return ()
    parts = []
    if llm_meta.get("pure_features"):
        parts.append("pure_features=1")
    if llm_meta.get("free_risk_manager"):
        parts.append("free_risk_manager=1")
    if llm_meta.get("context_docs") is not None and int(llm_meta["context_docs"]) != 5:
        parts.append(f"context_docs={int(llm_meta['context_docs'])}")
    return tuple(parts)


def _prompt_generation(llm_meta: Dict[str, Any]) -> int:
    match = re.search(r"multi_agent_v(\d+)", str(llm_meta.get("prompt_version") or ""))
    return int(match.group(1)) if match else 0


def _load_agent_cache(path: Path | None) -> Dict[tuple, List[tuple]]:
    """Index cache rows by (prompt version, horizon, provider, model, thinking model, theme,
    as_of, stock) with the key's score and return parts kept for an exact match."""
    cache: Dict[tuple, List[tuple]] = defaultdict(list)
    if path is None or not path.exists():
        return cache
    for row in _iter_jsonl(path):
        key = str(row.get("cache_key") or "")
        result = row.get("result")
        parts = key.split("|")
        if not key or not isinstance(result, dict) or len(parts) < 11:
            continue
        try:
            numbers = (float(parts[8]), float(parts[9]), float(parts[10]))
        except ValueError:
            continue
        cache[tuple(parts[:8])].append((numbers, tuple(parts[11:]), result))
    return cache


def _lookup_agent_scores(ctx: RunContext, as_of_ymd: str, raw: Dict[str, Any],
                         cache: Dict[tuple, List[tuple]]) -> tuple[Dict[str, Any], str]:
    """Return (cached result, status) where status is joined, missing or ambiguous."""
    if not ctx.llm_identity:
        return {}, "missing"
    version, provider, model, thinking = ctx.llm_identity
    prefix = (version, ctx.horizon, provider, model, thinking, ctx.theme_key, as_of_ymd, str(raw.get("stock_code") or ""))
    try:
        wanted = (float(raw.get("deterministic_leader_score")), float(raw.get("return_20d_pct")) / 100.0,
                  float(raw.get("return_60d_pct")) / 100.0)
    except (TypeError, ValueError):
        return {}, "missing"
    matches = [result for numbers, regime, result in cache.get(prefix, [])
               if regime == ctx.regime and abs(numbers[0] - wanted[0]) < 0.01
               and abs(numbers[1] - wanted[1]) < 1.01e-4 and abs(numbers[2] - wanted[2]) < 1.01e-4]
    if len(matches) > 1 and any(match != matches[0] for match in matches[1:]):
        return {}, "ambiguous"
    return (matches[0], "joined") if matches else ({}, "missing")


def _evaluate_run(
    ctx: RunContext,
    periods: List[Dict[str, Any]],
    cache: Dict[tuple[str, str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    variants = _variants(ctx.horizon) if cache else {}
    run_rows: List[Dict[str, Any]] = []
    period_rows: List[Dict[str, Any]] = []
    coverage = {
        "run_id": ctx.path.stem,
        "theme": ctx.theme,
        "period": ctx.period,
        "horizon": ctx.horizon,
        "strategy_id": ctx.strategy_id,
        "cache_path": str(ctx.cache_path or ""),
        "cache_available": bool(cache),
        "prompt_version": ctx.llm_identity[0] if ctx.llm_identity else "",
        "cache_key_settings_checked": bool(ctx.llm_identity) and _prompt_generation(
            {"prompt_version": ctx.llm_identity[0]}) >= 4,
        "periods_total": len(periods),
        "periods_with_candidates": 0,
        "candidate_rows": 0,
        "candidate_rows_with_agent_scores": 0,
        "ambiguous_cache_joins": 0,
        "agent_score_coverage": 0.0,
        "periods_compared": 0,
        "periods_skipped_insufficient_agent_rows": 0,
        "included_for_agent_ablation": False,
        "source_json": str(ctx.path),
    }
    per_variant_returns: Dict[str, List[float]] = {name: [] for name in variants}
    per_variant_benchmarks: Dict[str, List[float]] = {name: [] for name in variants}
    per_variant_selected: Dict[str, int] = {name: 0 for name in variants}

    evaluated_periods = []
    for period in periods:
        candidates = _candidate_rows(ctx, period, cache)
        if candidates:
            coverage["periods_with_candidates"] += 1
            coverage["candidate_rows"] += len(candidates)
            coverage["candidate_rows_with_agent_scores"] += sum(1 for row in candidates if row.get("agent_scores_available"))
            coverage["ambiguous_cache_joins"] += sum(1 for row in candidates if row.get("cache_join") == "ambiguous")
        evaluated_periods.append((period, candidates))
    if coverage["candidate_rows"]:
        coverage["agent_score_coverage"] = round(
            coverage["candidate_rows_with_agent_scores"] / coverage["candidate_rows"], 4)
    coverage["included_for_agent_ablation"] = bool(variants) and coverage["agent_score_coverage"] >= MIN_AGENT_COVERAGE
    if not coverage["included_for_agent_ablation"]:
        variants = {}
        per_variant_returns, per_variant_benchmarks, per_variant_selected = {}, {}, {}

    for period, all_candidates in evaluated_periods:
        benchmark = _float(period.get("benchmark_return_pct"))
        top_n = int(period.get("selected_count") or 3)
        # Every variant ranks the same rows: those with joined agent scores. A period
        # without enough of them is skipped for all variants instead of being filled.
        candidates = [row for row in all_candidates if row.get("agent_scores_available")]
        if variants and all_candidates and len(candidates) < top_n:
            coverage["periods_skipped_insufficient_agent_rows"] += 1
            continue
        if variants:
            coverage["periods_compared"] += 1

        for variant_name, scorer in variants.items():
            if not candidates:
                period_return = 0.0
                selected = []
            else:
                scored = []
                for row in candidates:
                    score = scorer(row)
                    if score is None or math.isnan(score):
                        continue
                    scored.append({**row, "variant_score": round(score, 4)})
                selected, gross_return = _select_top(scored, top_n)
                period_return = gross_return - ctx.round_trip_cost_pct if selected else 0.0
            per_variant_returns[variant_name].append(period_return)
            per_variant_benchmarks[variant_name].append(benchmark)
            per_variant_selected[variant_name] += len(selected)
            period_rows.append(
                {
                    "run_id": ctx.path.stem,
                    "theme": ctx.theme,
                    "period": ctx.period,
                    "horizon": ctx.horizon,
                    "strategy_id": ctx.strategy_id,
                    "as_of_date": str(period.get("as_of_date") or ""),
                    "variant": variant_name,
                    "portfolio_return_pct": round(period_return, 2),
                    "benchmark_return_pct": round(benchmark, 2),
                    "excess_return_pct": round(period_return - benchmark, 2),
                    "selected_count": len(selected),
                    "selected_names": "/".join(str(row.get("stock_name") or "") for row in selected),
                    "agent_rows_available": sum(1 for row in candidates if row.get("agent_scores_available")),
                    "candidate_count": len(candidates),
                }
            )

    for variant_name in variants:
        returns = per_variant_returns[variant_name]
        benchmarks = per_variant_benchmarks[variant_name]
        if not returns:
            continue
        total = _compound_return(returns)
        benchmark_total = _compound_return(benchmarks)
        run_rows.append(
            {
                "run_id": ctx.path.stem,
                "theme": ctx.theme,
                "period": ctx.period,
                "horizon": ctx.horizon,
                "strategy_id": ctx.strategy_id,
                "variant": variant_name,
                "total_return_pct": round(total, 2),
                "benchmark_return_pct": round(benchmark_total, 2),
                "excess_return_pct": round(total - benchmark_total, 2),
                "mdd_pct": round(_mdd(returns), 2),
                "avg_period_return_pct": round(_mean(returns), 2),
                "win_rate_pct": round(sum(1 for value in returns if value > 0) / len(returns) * 100.0, 2),
                "period_count": len(returns),
                "selected_count": per_variant_selected[variant_name],
                "source_json": str(ctx.path),
            }
        )

    return {"run_rows": run_rows, "period_rows": period_rows, "coverage": coverage}


def _candidate_rows(
    ctx: RunContext,
    period: Dict[str, Any],
    cache: Dict[tuple[str, str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    as_of_date = str(period.get("as_of_date") or "")
    as_of_ymd = re.sub(r"[^0-9]", "", as_of_date)
    output: List[Dict[str, Any]] = []
    for raw in period.get("candidate_rankings") or []:
        cached, join_status = _lookup_agent_scores(ctx, as_of_ymd, raw, cache)
        agent_scores = cached.get("llm_agent_scores") if isinstance(cached.get("llm_agent_scores"), dict) else {}
        # No fallback values: a row without all four agents' scores is not comparable.
        complete = all(isinstance(agent_scores.get(name), dict)
                       for name in ("analyst", "quant", "chartist", "risk_manager"))
        output.append(
            {
                **raw,
                "as_of_date": as_of_date,
                "cache_join": join_status,
                "agent_scores": agent_scores,
                "agent_scores_available": complete,
                "analyst_total": _agent_total(agent_scores, "analyst", float("nan")),
                "quant_total": _agent_total(agent_scores, "quant", float("nan")),
                "chartist_total": _agent_total(agent_scores, "chartist", float("nan")),
                "risk_raw_total": _risk_total(agent_scores, "raw_final_score", float("nan")),
                "risk_calibrated_total": _risk_total(agent_scores, "calibrated_final_score", float("nan")),
                "risk_score": _risk_total(agent_scores, "risk_score", float("nan")),
                "risk_confidence": _risk_total(agent_scores, "confidence", float("nan")),
                "liquidity_score": _liquidity_score(raw),
            }
        )
    return output


def _select_top(scored: List[Dict[str, Any]], top_n: int) -> tuple[List[Dict[str, Any]], float]:
    """Top-n rows by variant score and their mean return. Candidates tied at the cut-off
    share the remaining slots equally (the expected return of a random tie-break), so no
    other score, such as the deterministic one, decides an agent-only variant."""
    ranked = sorted(scored, key=lambda row: (-row["variant_score"], str(row.get("stock_code") or "")))
    if len(ranked) <= top_n:
        return ranked, _mean(_float(row.get("realized_return_pct")) for row in ranked)
    cutoff = ranked[top_n - 1]["variant_score"]
    above = [row for row in ranked if row["variant_score"] > cutoff]
    tied = [row for row in ranked if row["variant_score"] == cutoff]
    total = (sum(_float(row.get("realized_return_pct")) for row in above)
             + (top_n - len(above)) * _mean(_float(row.get("realized_return_pct")) for row in tied))
    return ranked[:top_n], total / top_n


def _variants(horizon: str):
    weights = SHORT_WEIGHTS if horizon == "short" else LONG_WEIGHTS

    def weighted(row: Dict[str, Any], selected: Iterable[str] = ("analyst", "quant", "chartist")) -> float:
        selected = tuple(selected)
        denom = sum(weights[name] for name in selected)
        if denom <= 0:
            return 0.0
        return sum(weights[name] / denom * _float(row[f"{name}_total"]) for name in selected)

    def blend(a, b, aw=0.70):
        return lambda row: aw * a(row) + (1.0 - aw) * b(row)

    three_agent = lambda row: weighted(row, ("analyst", "quant", "chartist"))
    risk_raw = lambda row: _float(row.get("risk_raw_total"))
    current_hybrid = lambda row: _float(row.get("leader_score"))
    risk_adjusted = lambda row: three_agent(row) - max(0.0, _float(row.get("risk_score")) - 60.0) * 0.25
    # Same definition as the scorer's four_agent_plus_liquidity profile (backtesting/llm_signal.py).
    plus_liquidity = lambda row: 0.85 * three_agent(row) + 0.15 * _float(row.get("liquidity_score"))

    return {
        "current_hybrid_4agent": current_hybrid,
        "deterministic_only": lambda row: _float(row.get("deterministic_leader_score")),
        "analyst_only": lambda row: _float(row.get("analyst_total")),
        "quant_only": lambda row: _float(row.get("quant_total")),
        "chartist_only": lambda row: _float(row.get("chartist_total")),
        "analyst_quant": lambda row: weighted(row, ("analyst", "quant")),
        "analyst_chartist": lambda row: weighted(row, ("analyst", "chartist")),
        "quant_chartist": lambda row: weighted(row, ("quant", "chartist")),
        "three_agent_no_risk_manager": three_agent,
        "remove_analyst": lambda row: weighted(row, ("quant", "chartist")),
        "remove_quant": lambda row: weighted(row, ("analyst", "chartist")),
        "remove_chartist": lambda row: weighted(row, ("analyst", "quant")),
        "risk_manager_raw_only": risk_raw,
        "four_agent_raw_blend": blend(three_agent, risk_raw, 0.70),
        "four_agent_risk_adjusted": risk_adjusted,
        "four_agent_plus_liquidity": plus_liquidity,
    }


def _build_summary(run_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        groups[(str(row["horizon"]), str(row["variant"]))].append(row)

    output: List[Dict[str, Any]] = []
    current_by_run = {
        row["run_id"]: row
        for row in run_rows
        if row["variant"] == "current_hybrid_4agent"
    }
    for (horizon, variant), rows in sorted(groups.items()):
        deltas = []
        wins = 0
        for row in rows:
            current = current_by_run.get(row["run_id"])
            if current:
                delta = _float(row["excess_return_pct"]) - _float(current["excess_return_pct"])
                deltas.append(delta)
                if delta > 0:
                    wins += 1
        output.append(
            {
                "horizon": horizon,
                "variant": variant,
                "run_count": len(rows),
                "avg_total_return_pct": round(_mean(row["total_return_pct"] for row in rows), 2),
                "avg_excess_return_pct": round(_mean(row["excess_return_pct"] for row in rows), 2),
                "avg_mdd_pct": round(_mean(row["mdd_pct"] for row in rows), 2),
                "avg_delta_vs_current_excess_pct": round(_mean(deltas), 2) if deltas else 0.0,
                "win_count_vs_current": wins,
                "win_rate_vs_current_pct": round(wins / len(deltas) * 100.0, 2) if deltas else 0.0,
            }
        )
    return output


def _build_decision(summary_rows: List[Dict[str, Any]], coverage_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_key = {(row["horizon"], row["variant"]): row for row in summary_rows}
    decisions = {}
    for horizon in ["short", "long"]:
        current = by_key.get((horizon, "current_hybrid_4agent"), {})
        three = by_key.get((horizon, "three_agent_no_risk_manager"), {})
        raw4 = by_key.get((horizon, "four_agent_raw_blend"), {})
        det = by_key.get((horizon, "deterministic_only"), {})
        decisions[horizon] = {
            "current_vs_deterministic_excess_delta_pct": round(
                _float(current.get("avg_excess_return_pct")) - _float(det.get("avg_excess_return_pct")), 2
            ),
            "four_agent_raw_blend_vs_three_agent_delta_pct": round(
                _float(raw4.get("avg_excess_return_pct")) - _float(three.get("avg_excess_return_pct")), 2
            ),
            "current_avg_excess_pct": current.get("avg_excess_return_pct", 0.0),
            "three_agent_avg_excess_pct": three.get("avg_excess_return_pct", 0.0),
            "four_agent_raw_blend_avg_excess_pct": raw4.get("avg_excess_return_pct", 0.0),
        }
    decisions["coverage"] = {
        "included_runs": sum(1 for row in coverage_rows if row["included_for_agent_ablation"]),
        "total_runs": len(coverage_rows),
        "candidate_rows_with_agent_scores": sum(int(row["candidate_rows_with_agent_scores"]) for row in coverage_rows),
    }
    return decisions


def _load_profile_run_rows(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.glob("*/*/multi_agent_proof/ai_short_long_validation_summary.csv")):
        rel = path.relative_to(root).parts
        if len(rel) < 3:
            continue
        profile = rel[0]
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                strategy_id = str(raw.get("strategy_id") or "")
                if strategy_id not in {"deterministic_short", "deterministic_long", "short_hybrid_05", "long_hybrid_05"}:
                    continue
                rows.append(
                    {
                        "profile": profile,
                        "theme": str(raw.get("theme") or raw.get("theme_key") or rel[1]),
                        "theme_key": rel[1],
                        "period": str(raw.get("period") or ""),
                        "horizon": str(raw.get("horizon") or ""),
                        "strategy_id": strategy_id,
                        "is_baseline": str(raw.get("is_baseline") or "").lower() == "true",
                        "total_return_pct": _float(raw.get("total_return_pct")),
                        "benchmark_return_pct": _float(raw.get("benchmark_return_pct")),
                        "excess_return_pct": _float(raw.get("excess_return_pct")),
                        "mdd_pct": _float(raw.get("mdd_pct")),
                        "excess_delta_vs_baseline_pct": _float(raw.get("excess_delta_vs_baseline_pct")),
                        "win_vs_baseline": str(raw.get("win_vs_baseline") or "").lower() == "true",
                        "result_json": str(raw.get("result_json") or ""),
                        "summary_csv": str(path),
                    }
                )
    return rows


def _build_profile_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hybrid_rows = [
        row
        for row in rows
        if row["strategy_id"] in {"short_hybrid_05", "long_hybrid_05"} and not row["is_baseline"]
    ]
    current_by_key = {
        (row["theme_key"], row["period"], row["horizon"]): row
        for row in hybrid_rows
        if row["profile"] == "current_hybrid_4agent"
    }
    groups: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in hybrid_rows:
        groups[(row["profile"], row["horizon"])].append(row)

    output: List[Dict[str, Any]] = []
    for (profile, horizon), grouped in sorted(groups.items()):
        deltas = []
        for row in grouped:
            current = current_by_key.get((row["theme_key"], row["period"], row["horizon"]))
            if current:
                deltas.append(_float(row["excess_return_pct"]) - _float(current["excess_return_pct"]))
        output.append(
            {
                "profile": profile,
                "horizon": horizon,
                "run_count": len(grouped),
                "themes": "/".join(sorted({str(row["theme_key"]) for row in grouped})),
                "avg_total_return_pct": round(_mean(row["total_return_pct"] for row in grouped), 2),
                "avg_excess_return_pct": round(_mean(row["excess_return_pct"] for row in grouped), 2),
                "avg_mdd_pct": round(_mean(row["mdd_pct"] for row in grouped), 2),
                "avg_excess_delta_vs_baseline_pct": round(
                    _mean(row["excess_delta_vs_baseline_pct"] for row in grouped), 2
                ),
                "win_rate_vs_baseline_pct": round(
                    sum(1 for row in grouped if row["win_vs_baseline"]) / len(grouped) * 100.0, 2
                )
                if grouped
                else 0.0,
                "avg_delta_vs_current_excess_pct": round(_mean(deltas), 2) if deltas else 0.0,
                "profile_backtest": True,
            }
        )
    return output


def _build_profile_theme_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hybrid_rows = [
        row
        for row in rows
        if row["strategy_id"] in {"short_hybrid_05", "long_hybrid_05"} and not row["is_baseline"]
    ]
    current_by_key = {
        (row["theme_key"], row["period"], row["horizon"]): row
        for row in hybrid_rows
        if row["profile"] == "current_hybrid_4agent"
    }
    groups: Dict[tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in hybrid_rows:
        groups[(row["profile"], row["horizon"], row["theme_key"])].append(row)

    output: List[Dict[str, Any]] = []
    for (profile, horizon, theme_key), grouped in sorted(groups.items()):
        deltas = []
        for row in grouped:
            current = current_by_key.get((row["theme_key"], row["period"], row["horizon"]))
            if current:
                deltas.append(_float(row["excess_return_pct"]) - _float(current["excess_return_pct"]))
        output.append(
            {
                "profile": profile,
                "horizon": horizon,
                "theme_key": theme_key,
                "run_count": len(grouped),
                "avg_total_return_pct": round(_mean(row["total_return_pct"] for row in grouped), 2),
                "avg_excess_return_pct": round(_mean(row["excess_return_pct"] for row in grouped), 2),
                "avg_mdd_pct": round(_mean(row["mdd_pct"] for row in grouped), 2),
                "avg_excess_delta_vs_baseline_pct": round(
                    _mean(row["excess_delta_vs_baseline_pct"] for row in grouped), 2
                ),
                "win_rate_vs_baseline_pct": round(
                    sum(1 for row in grouped if row["win_vs_baseline"]) / len(grouped) * 100.0, 2
                )
                if grouped
                else 0.0,
                "avg_delta_vs_current_excess_pct": round(_mean(deltas), 2) if deltas else 0.0,
                "profile_backtest": True,
            }
        )
    return output


def _filter_profile_summary(
    rows: List[Dict[str, Any]],
    *,
    profiles: set[str],
    required_theme_keys: set[str],
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for row in rows:
        row_profiles = str(row.get("profile") or "")
        row_themes = {item for item in str(row.get("themes") or "").split("/") if item}
        if row_profiles not in profiles:
            continue
        if not required_theme_keys.issubset(row_themes):
            continue
        output.append(row)
    return sorted(output, key=lambda row: (str(row["horizon"]), str(row["profile"])))


def _filter_profile_theme_summary(
    rows: List[Dict[str, Any]],
    *,
    profiles: set[str],
    theme_key: str,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("profile") or "") not in profiles:
            continue
        if str(row.get("theme_key") or "") != theme_key:
            continue
        output.append(row)
    return sorted(output, key=lambda row: (str(row["horizon"]), str(row["profile"])))


def _build_ablation_count_summary(summary_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in summary_rows:
        meta = ABLATION_VARIANTS.get(str(row.get("variant") or ""))
        if not meta or meta["agent_count"] is None:
            continue
        enriched = {**row, **meta}
        groups[(str(row["horizon"]), int(meta["agent_count"]))].append(enriched)

    output: List[Dict[str, Any]] = []
    for (horizon, agent_count), rows in sorted(groups.items()):
        best = max(rows, key=lambda row: _float(row["avg_excess_return_pct"]))
        output.append(
            {
                "horizon": horizon,
                "agent_count": agent_count,
                "variant_count": len(rows),
                "variants": "/".join(str(row["variant"]) for row in rows),
                "best_variant": best["variant"],
                "best_label": best["label"],
                "best_avg_total_return_pct": best["avg_total_return_pct"],
                "best_avg_excess_return_pct": best["avg_excess_return_pct"],
                "best_avg_mdd_pct": best["avg_mdd_pct"],
                "avg_excess_return_pct": round(_mean(row["avg_excess_return_pct"] for row in rows), 2),
            }
        )
    return output


def _build_leave_one_out_summary(summary_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key = {(row["horizon"], row["variant"]): row for row in summary_rows}
    output: List[Dict[str, Any]] = []
    for horizon in ["short", "long"]:
        for full_variant, variant, removed_agent in LEAVE_ONE_OUT_VARIANTS:
            current = by_key.get((horizon, full_variant))
            reduced = by_key.get((horizon, variant))
            if not current or not reduced:
                continue
            delta = _float(current["avg_excess_return_pct"]) - _float(reduced["avg_excess_return_pct"])
            output.append(
                {
                    "horizon": horizon,
                    "removed_agent": removed_agent,
                    "current_variant": full_variant,
                    "reduced_variant": variant,
                    "current_avg_excess_return_pct": current["avg_excess_return_pct"],
                    "reduced_avg_excess_return_pct": reduced["avg_excess_return_pct"],
                    "excess_loss_when_removed_pct": round(delta, 2),
                    "usefulness_interpretation": _usefulness_label(delta),
                    "current_avg_mdd_pct": current["avg_mdd_pct"],
                    "reduced_avg_mdd_pct": reduced["avg_mdd_pct"],
                }
            )
    return output


def _usefulness_label(delta: float) -> str:
    if delta >= 5.0:
        return "strong_positive_when_present"
    if delta >= 1.0:
        return "positive_when_present"
    if delta > -1.0:
        return "neutral_or_not_proven"
    return "reduced_version_better"


def _render_report(
    summary_rows: List[Dict[str, Any]],
    coverage_rows: List[Dict[str, Any]],
    decision: Dict[str, Any],
) -> str:
    lines = [
        "# Four-Agent Architecture Validation",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        "- Method: saved candidate top-10 reranking + saved multi-agent cache. No new LLM calls.",
        "- Center question: whether Analyst/Quant/Chartist/RiskManager is more useful than fewer agents or simple baselines.",
        "",
        "## Coverage",
        "",
        "| Theme | Period | Horizon | Cache | Candidate Rows | Agent Rows | Included |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in coverage_rows:
        lines.append(
            "| {theme} | {period} | {horizon} | {cache} | {cand} | {agent} | {included} |".format(
                theme=row["theme"],
                period=row["period"],
                horizon=row["horizon"],
                cache="yes" if row["cache_available"] else "no",
                cand=row["candidate_rows"],
                agent=row["candidate_rows_with_agent_scores"],
                included="yes" if row["included_for_agent_ablation"] else "no",
            )
        )

    lines.extend(
        [
            "",
            "## Summary By Horizon",
            "",
            "| Horizon | Variant | Runs | Avg Return | Avg Excess | Avg MDD | Delta vs Current | Win vs Current |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary_rows:
        lines.append(
            "| {horizon} | {variant} | {run_count} | {ret:.2f}% | {excess:.2f}% | {mdd:.2f}% | {delta:.2f}% | {win:.2f}% |".format(
                horizon=row["horizon"],
                variant=row["variant"],
                run_count=row["run_count"],
                ret=float(row["avg_total_return_pct"]),
                excess=float(row["avg_excess_return_pct"]),
                mdd=float(row["avg_mdd_pct"]),
                delta=float(row["avg_delta_vs_current_excess_pct"]),
                win=float(row["win_rate_vs_current_pct"]),
            )
        )

    lines.extend(
        [
            "",
            "## Decision Notes",
            "",
            f"- Short current vs deterministic excess delta: {decision['short']['current_vs_deterministic_excess_delta_pct']}%",
            f"- Long current vs deterministic excess delta: {decision['long']['current_vs_deterministic_excess_delta_pct']}%",
            f"- Short four-agent raw blend vs three-agent delta: {decision['short']['four_agent_raw_blend_vs_three_agent_delta_pct']}%",
            f"- Long four-agent raw blend vs three-agent delta: {decision['long']['four_agent_raw_blend_vs_three_agent_delta_pct']}%",
            "",
            "## Interpretation",
            "",
            "- `three_agent_no_risk_manager` tests Analyst/Quant/Chartist without RiskManager.",
            "- `risk_manager_raw_only` tests whether RiskManager's independent final score is useful by itself.",
            "- `four_agent_raw_blend` tests a practical 4-agent form where RiskManager raw score affects ranking.",
            "- `remove_*` rows test whether removing one specialist hurts the result.",
            "- `four_agent_plus_liquidity` is an added-agent proxy using saved liquidity/volume features.",
            "",
            "## Limitations",
            "",
            "- This is an offline rerank, not a fresh LLM backtest.",
            "- It only reorders saved top-10 candidates from each rebalance date.",
            "- AI runs without preserved cache cannot be used for agent-combination reranking.",
            "- If this report identifies a strong winner, that small set should be rerun as a true LLM backtest.",
            "",
            "## Artifacts",
            "",
            "- `agent-architecture-run-results.csv`",
            "- `agent-architecture-period-results.csv`",
            "- `agent-architecture-summary.csv`",
            "- `four-agent-core-summary.csv`",
            "- `profile-backtest-summary.csv`",
            "- `FOUR_AGENT_EVIDENCE_KO.md`",
            "- `AGENT_ABLATION_EVIDENCE_KO.md`",
            "- `agent-count-ablation-summary.csv`",
            "- `leave-one-out-agent-summary.csv`",
            "- `FOUR_AGENT_TRUE_RERUN_PLAN.md`",
            "- `agent-architecture-coverage.csv`",
            "- `agent-architecture-validation.json`",
            "",
        ]
    )
    return "\n".join(lines)


def _render_korean_evidence_report(
    core_summary_rows: List[Dict[str, Any]],
    coverage_rows: List[Dict[str, Any]],
    decision: Dict[str, Any],
    profile_summary_rows: List[Dict[str, Any]],
) -> str:
    included_runs = decision["coverage"]["included_runs"]
    total_runs = decision["coverage"]["total_runs"]
    agent_rows = decision["coverage"]["candidate_rows_with_agent_scores"]
    lines = [
        "# 4개 에이전트 구조 유효성 검증 보고서",
        "",
        "## 결론",
        "",
        "- 현재 증거로는 '4개 에이전트가 모든 구간에서 무조건 최고'라고 말하면 안 됩니다.",
        "- 현재 4-agent와 RiskManager 제거 결과가 같게 나오는 구간이 있어, 지금 구현만으로는 4번째 에이전트의 효과를 충분히 증명하지 못합니다.",
        "- 장타는 RiskManager 위험보정 또는 Analyst/Quant 중심 조합이 현재 방식보다 좋게 나와, 장타 점수 결합을 바꿀 근거가 있습니다.",
        "- 단타는 현재 방식이 비교적 방어적이고, 유동성 추가만 소폭 개선되었습니다. 단타에서는 Chartist를 유지하는 쪽이 더 안전합니다.",
        "- 따라서 핵심 결론은 '4개 역할 구조는 유지하되, RiskManager가 실제 랭킹에 영향을 주도록 구현을 수정한 뒤 다시 검증해야 한다'입니다.",
        "",
        "## 현재 구조",
        "",
        "- AnalystAgent: 뉴스, 공시, 테마 적합성, 성장성, 촉매를 보는 역할입니다.",
        "- QuantAgent: 재무, 밸류에이션, 수익성, 안정성을 보는 역할입니다.",
        "- ChartistAgent: 가격, 추세, 변동성, 거래량을 보는 역할입니다.",
        "- RiskManagerAgent: 위 세 에이전트의 결과를 받아 위험과 최종 판단을 조정하는 상위 에이전트입니다.",
        "- 모델 구조는 앞의 Analyst/Quant는 qwen 계열 instruct 모델, 마지막 RiskManager는 gpt-oss 계열 thinking 모델을 쓰는 구조입니다. Chartist는 LLM이 아니라 규칙 기반 점수입니다.",
        "",
        "## 실험 방법",
        "",
        "- 새 LLM 호출 없이 저장된 multi-agent top10 후보와 캐시를 사용했습니다.",
        "- 각 리밸런싱 날짜마다 저장된 top10 후보 안에서 top3를 다시 고르는 방식입니다.",
        "- 비교 중심은 multi-agent입니다. 기본 비교군은 현재 4-agent, RiskManager 제거, 특정 에이전트 제거, RiskManager 직접 반영, 유동성 추가입니다.",
        f"- 사용 가능 범위는 {included_runs}/{total_runs}개 실행, 에이전트 점수 후보 {agent_rows}개입니다.",
        "- AI는 2023/2024 캐시만 에이전트 조합 검증에 사용 가능했고, 반도체는 2023/2024/2025/2026Q1 대부분 사용 가능했습니다.",
        "",
        "## 오프라인 조합 비교",
        "",
        "| 구간 | 조합 | 실행수 | 평균 수익률 | 평균 초과수익 | 평균 MDD | 현재 대비 초과수익 차이 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in core_summary_rows:
        lines.append(
            "| {horizon} | {variant} | {runs} | {ret} | {excess} | {mdd} | {delta} |".format(
                horizon=_ko_horizon(row["horizon"]),
                variant=_variant_ko(row["variant"]),
                runs=row["run_count"],
                ret=_fmt_pct(row["avg_total_return_pct"]),
                excess=_fmt_pct(row["avg_excess_return_pct"]),
                mdd=_fmt_pct(row["avg_mdd_pct"]),
                delta=_fmt_pct(row["avg_delta_vs_current_excess_pct"]),
            )
        )

    lines.extend(
        [
            "",
            "## 해석",
            "",
            f"- 단타 현재 4-agent는 deterministic 대비 초과수익이 {decision['short']['current_vs_deterministic_excess_delta_pct']}%p 높았습니다.",
            f"- 장타 현재 4-agent는 deterministic 대비 초과수익이 {decision['long']['current_vs_deterministic_excess_delta_pct']}%p 낮았습니다.",
            "- 오프라인 재정렬에서는 장타에서 Chartist 또는 Analyst를 제거했을 때 성과가 크게 낮아져 역할 분리 근거가 있었습니다.",
            "- 다만 실제 백테스트 엔진 재실행에서는 장타 `four_agent_risk_adjusted`와 `remove_chartist`가 현재 방식보다 좋았습니다. 즉 장타 Chartist 가중치는 더 낮추거나 별도 검증해야 합니다.",
            "- 단타는 실제 재실행 기준으로 `four_agent_plus_liquidity`만 현재보다 소폭 좋았고, `four_agent_risk_adjusted`와 `remove_chartist`는 나빠졌습니다.",
            "- 특히 현재 구현은 RiskManager의 원점수가 최종 랭킹에 강하게 반영되지 않았습니다. 이 부분이 4번째 에이전트의 가치를 약하게 보이게 만든 핵심 리스크입니다.",
        ]
    )

    if profile_summary_rows:
        lines.extend(
            [
                "",
                "## 캐시 기반 재실행 결과",
                "",
                "아래는 저장된 LLM 캐시만 사용해서 실제 백테스트 엔진을 다시 돌린 결과입니다. 새 LLM 호출은 없습니다.",
                "",
                "| 구간 | 프로필 | 실행수 | 평균 수익률 | 평균 초과수익 | 평균 MDD | 기본전략 대비 초과수익 | 현재 대비 초과수익 |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in profile_summary_rows:
            lines.append(
                "| {horizon} | {profile} | {runs} | {ret} | {excess} | {mdd} | {baseline_delta} | {current_delta} |".format(
                    horizon=_ko_horizon(row["horizon"]),
                    profile=_variant_ko(row["profile"]),
                    runs=row["run_count"],
                    ret=_fmt_pct(row["avg_total_return_pct"]),
                    excess=_fmt_pct(row["avg_excess_return_pct"]),
                    mdd=_fmt_pct(row["avg_mdd_pct"]),
                    baseline_delta=_fmt_pct(row["avg_excess_delta_vs_baseline_pct"]),
                    current_delta=_fmt_pct(row["avg_delta_vs_current_excess_pct"]),
                )
            )
    else:
        lines.extend(
            [
                "",
                "## 캐시 기반 재실행 결과",
                "",
                "- 아직 `profile_backtest_runs/` 재실행 결과가 없습니다.",
                "- 실행 명령은 `FOUR_AGENT_TRUE_RERUN_PLAN.md`에 정리되어 있습니다.",
            ]
        )

    lines.extend(
        [
            "",
            "## 다음 판단 기준",
            "",
            "- 장타: `four_agent_risk_adjusted`를 대표 후보로 올리고, Chartist 비중 축소 실험을 추가해야 합니다.",
            "- 단타: 현재 4-agent를 유지하되 `four_agent_plus_liquidity`를 후보로 추가 검증하는 것이 안전합니다.",
            "- 최종 주장은 '4개 에이전트가 항상 좋다'가 아니라 'RiskManager가 랭킹에 실제로 반영되는 4개 역할 구조가 어느 구간에서 유효한가'로 써야 안전합니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _computed_findings(leave_one_out_rows: List[Dict[str, Any]], coverage_rows: List[Dict[str, Any]]) -> List[str]:
    """Findings derived only from the computed tables (no fixed conclusions)."""
    included = [row for row in coverage_rows if row["included_for_agent_ablation"]]
    lines = [f"- 비교에 포함된 실행: {len(included)}/{len(coverage_rows)}개 (에이전트 점수 결합률 {MIN_AGENT_COVERAGE:.0%} 이상), "
             f"비교한 기간 {sum(int(row['periods_compared']) for row in included)}개, "
             f"점수 부족으로 제외한 기간 {sum(int(row['periods_skipped_insufficient_agent_rows']) for row in included)}개."]
    if not included or not leave_one_out_rows:
        lines.append("- 비교 가능한 실행이 없어 에이전트별 결론을 내리지 않습니다.")
        return lines
    for row in leave_one_out_rows:
        verb = "추가" if row["removed_agent"] == "RiskManager" else "유지"
        lines.append(
            f"- {_ko_horizon(row['horizon'])} {row['removed_agent']}: {row['current_variant']}와 {row['reduced_variant']}의 "
            f"평균 초과수익 차이 {_fmt_pct(row['excess_loss_when_removed_pct'])} ({verb} 시 기준, "
            f"{_usefulness_ko(row['usefulness_interpretation'])}).")
    unchecked = sum(1 for row in included if not row.get("cache_key_settings_checked"))
    if unchecked:
        lines.append(f"- 포함된 실행 중 {unchecked}개는 설정을 담지 않은 v4 이전 캐시 키를 써서, 결합된 점수가 "
                     "그 실행의 설정(pure_features, context_docs 등)으로 만들어졌는지 검증할 수 없습니다.")
    lines.append("- 차이는 기간별 표본 평균이며 통계적 유의성 검정이나 다중 비교 보정을 거치지 않았습니다.")
    return lines


def _render_agent_ablation_report(
    summary_rows: List[Dict[str, Any]],
    count_rows: List[Dict[str, Any]],
    leave_one_out_rows: List[Dict[str, Any]],
    profile_summary_rows: List[Dict[str, Any]],
    coverage_rows: List[Dict[str, Any]],
    exploration_rows: List[Dict[str, Any]],
    representative_rows: List[Dict[str, Any]],
) -> str:
    included_runs = sum(1 for row in coverage_rows if row["included_for_agent_ablation"])
    agent_rows = sum(int(row["candidate_rows_with_agent_scores"]) for row in coverage_rows)
    lines = [
        "# 에이전트 축소/추가 유효성 검증",
        "",
        "## 목적",
        "",
        "이 보고서의 목적은 현재 프로젝트의 4-agent 구성이 유용한지 검증하는 것입니다.",
        "접근 방식은 에이전트를 하나씩 줄였을 때 성과가 어떻게 변하는지 보고, 각 역할이 필요한지 확인하는 것입니다.",
        "규칙 기반 요인(실행의 하이브리드 점수, 유동성)을 섞은 변형은 에이전트 수 비교에서 빼고 별도로 표시합니다.",
        "",
        "## 실험 설계",
        "",
        "- 1단계 탐색: 반도체 테마에서 가능한 후보를 모두 비교했습니다.",
        "- 2단계 검증: 대표 후보를 AI와 반도체 두 테마에서 비교했습니다.",
        "- 모든 비교는 multi-agent 구조를 중심으로 합니다.",
        "- 새 LLM 호출 없이 저장된 multi-agent 캐시를 사용해 조합별 백테스트를 재실행했습니다.",
        "",
        "## 계산된 결과",
        "",
        *_computed_findings(leave_one_out_rows, coverage_rows),
        "",
        "## 사용 데이터",
        "",
        f"- 오프라인 축소 실험: 포함 실행 {included_runs}개, 에이전트 점수 후보 {agent_rows}개",
        f"- 포함된 실행의 테마·기간: {_included_scope(coverage_rows)}",
        "- 모든 비교의 중심은 multi-agent 후보 조합입니다.",
        f"- 반도체 전체 후보 탐색 행 수: {len(exploration_rows)}",
        f"- AI+반도체 대표 후보 검증 행 수: {len(representative_rows)}",
        "",
        "## 1단계: 반도체 전체 후보 탐색",
        "",
        "전체 후보 탐색은 한 테마에서 후보를 넓게 훑어 어떤 조합이 볼 만한지 고르는 단계입니다.",
        "",
        "| 구간 | 후보 | 평균 초과수익 | 현재 대비 | 평균 MDD | 실행수 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in exploration_rows:
        lines.append(
            "| {horizon} | {profile} | {excess} | {delta} | {mdd} | {runs} |".format(
                horizon=_ko_horizon(row["horizon"]),
                profile=_variant_ko(row["profile"]),
                excess=_fmt_pct(row["avg_excess_return_pct"]),
                delta=_fmt_pct(row["avg_delta_vs_current_excess_pct"]),
                mdd=_fmt_pct(row["avg_mdd_pct"]),
                runs=row["run_count"],
            )
        )

    lines.extend(
        [
            "",
            "## 2단계: AI+반도체 대표 후보 검증",
            "",
            "대표 후보 검증은 한 테마에만 우연히 맞은 결과인지 확인하는 단계입니다.",
            "",
            "| 구간 | 대표 후보 | 평균 초과수익 | 현재 대비 | 평균 MDD | 실행수 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in representative_rows:
        lines.append(
            "| {horizon} | {profile} | {excess} | {delta} | {mdd} | {runs} |".format(
                horizon=_ko_horizon(row["horizon"]),
                profile=_variant_ko(row["profile"]),
                excess=_fmt_pct(row["avg_excess_return_pct"]),
                delta=_fmt_pct(row["avg_delta_vs_current_excess_pct"]),
                mdd=_fmt_pct(row["avg_mdd_pct"]),
                runs=row["run_count"],
            )
        )

    lines.extend(
        [
            "",
            "## 에이전트 수별 오프라인 비교",
            "",
            "| 구간 | 에이전트 수 | 후보 조합 수 | 최고 조합 | 최고 평균 초과수익 | 최고 평균 수익률 | 최고 MDD | 조합 평균 초과수익 |",
            "|---|---:|---:|---|---:|---:|---:|---:|",
        ]
    )
    for row in count_rows:
        lines.append(
            "| {horizon} | {count} | {variants} | {best} | {excess} | {ret} | {mdd} | {avg_excess} |".format(
                horizon=_ko_horizon(row["horizon"]),
                count=row["agent_count"],
                variants=row["variant_count"],
                best=_variant_ko(row["best_variant"]),
                excess=_fmt_pct(row["best_avg_excess_return_pct"]),
                ret=_fmt_pct(row["best_avg_total_return_pct"]),
                mdd=_fmt_pct(row["best_avg_mdd_pct"]),
                avg_excess=_fmt_pct(row["avg_excess_return_pct"]),
            )
        )

    lines.extend(
        [
            "",
            "## 하나씩 제거했을 때",
            "",
            "양수는 해당 에이전트를 제거했더니 초과수익이 낮아졌다는 뜻입니다. 즉 해당 역할이 유용하다는 방향의 증거입니다.",
            "",
            "| 구간 | 제거한 에이전트 | 비교 기준 | 기준 초과수익 | 제거 후 초과수익 | 제거 시 손실 | 해석 |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in leave_one_out_rows:
        lines.append(
            "| {horizon} | {agent} | {baseline} | {current} | {reduced} | {loss} | {label} |".format(
                horizon=_ko_horizon(row["horizon"]),
                agent=row["removed_agent"],
                baseline=_variant_ko(row["current_variant"]),
                current=_fmt_pct(row["current_avg_excess_return_pct"]),
                reduced=_fmt_pct(row["reduced_avg_excess_return_pct"]),
                loss=_fmt_pct(row["excess_loss_when_removed_pct"]),
                label=_usefulness_ko(row["usefulness_interpretation"]),
            )
        )

    lines.extend(
        [
            "",
            "## 전체 캐시 백테스트 참고",
            "",
            "아래 표는 현재 저장된 모든 캐시 기반 프로필 결과입니다. 핵심 판단은 위의 1단계/2단계 표를 우선합니다.",
            "",
            "| 구간 | 조합 | 실행수 | 평균 초과수익 | 현재 대비 | 평균 MDD |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in profile_summary_rows:
        lines.append(
            "| {horizon} | {profile} | {runs} | {excess} | {delta} | {mdd} |".format(
                horizon=_ko_horizon(row["horizon"]),
                profile=_variant_ko(row["profile"]),
                runs=row["run_count"],
                excess=_fmt_pct(row["avg_excess_return_pct"]),
                delta=_fmt_pct(row["avg_delta_vs_current_excess_pct"]),
                mdd=_fmt_pct(row["avg_mdd_pct"]),
            )
        )

    lines.extend(
        [
            "",
            "## 주장 구조",
            "",
            "1. 단독 에이전트가 특정 구간에서 좋게 나와도, 그것만으로 충분하다고 보지 않습니다. 구간이 바뀌면 역할이 바뀔 수 있기 때문입니다.",
            "2. 에이전트를 하나씩 제거했을 때 성과가 떨어지는 역할은 유지 후보로 봅니다.",
            "3. 제거했는데 성과가 좋아지는 역할은 버리는 것이 아니라, 해당 구간에서 가중치를 낮추거나 조건부로 써야 합니다.",
            "4. RiskManager를 더했는데 3-agent와 같다면 4번째 에이전트의 구현 효과가 약하다는 뜻입니다.",
            "",
            "## 다음 실험 방향 (위 계산에서 도출)",
            "",
            *_next_steps(leave_one_out_rows),
            "",
        ]
    )
    return "\n".join(lines)


def _included_scope(coverage_rows: List[Dict[str, Any]]) -> str:
    scope = sorted({f"{row['theme']} {row['period']}" for row in coverage_rows if row["included_for_agent_ablation"]})
    return ", ".join(scope) if scope else "없음"


def _next_steps(leave_one_out_rows: List[Dict[str, Any]]) -> List[str]:
    """Directions taken from the leave-one-out table instead of fixed recommendations."""
    lines = []
    for horizon in ("short", "long"):
        rows = [row for row in leave_one_out_rows if row["horizon"] == horizon]
        if not rows:
            continue
        keep = [row["removed_agent"] for row in rows if _float(row["excess_loss_when_removed_pct"]) > 0]
        lower = [row["removed_agent"] for row in rows if _float(row["excess_loss_when_removed_pct"]) < 0]
        lines.append(f"- {_ko_horizon(horizon)}: 제거 시 손실이 난 역할(유지 후보) {', '.join(keep) or '없음'}; "
                     f"제거하니 좋아진 역할(가중치 축소·조건부 후보) {', '.join(lower) or '없음'}.")
    return lines or ["- 비교 가능한 실행이 없어 방향을 정하지 않습니다."]


def _usefulness_ko(value: Any) -> str:
    mapping = {
        "strong_positive_when_present": "강한 유용성",
        "positive_when_present": "유용성 있음",
        "neutral_or_not_proven": "중립/미증명",
        "reduced_version_better": "제거한 쪽이 더 좋음",
    }
    return mapping.get(str(value), str(value))


def _render_true_rerun_plan() -> str:
    return "\n".join(
        [
            "# Four-Agent True Rerun Plan",
            "",
            "## Purpose",
            "",
            "Validate whether the 3 specialist agents plus RiskManager architecture improves short/long backtests.",
            "",
            "## Core Profiles",
            "",
            "- `current_hybrid_4agent`: current project representative score.",
            "- `three_agent_no_risk_manager`: Analyst/Quant/Chartist only.",
            "- `four_agent_risk_adjusted`: Analyst/Quant/Chartist score with RiskManager risk penalty.",
            "- `four_agent_plus_liquidity`: current role score with liquidity proxy.",
            "- `remove_chartist`: Analyst/Quant only, used to test whether Chartist weight is helpful in long-horizon scoring.",
            "",
            "## Commands",
            "",
            "```bash",
            "venv/bin/python scripts/research/run_agent_architecture_profile_backtests.py",
            "venv/bin/python scripts/research/build_agent_architecture_validation.py",
            "```",
            "",
            "The runner sets `AGENT_SCORE_CACHE_ONLY=1`, so it uses saved multi-agent cache and avoids new LLM calls.",
            "",
            "## Fresh LLM Rerun Rule",
            "",
            "Only after the cache-only profile result identifies 1-2 finalists should a fresh qwen3:14b + gpt-oss:20b run be started.",
            "",
        ]
    )


def _variant_ko(value: Any) -> str:
    mapping = {
        "current_hybrid_4agent": "현재 4-agent",
        "deterministic_only": "규칙 기반만",
        "three_agent_no_risk_manager": "RiskManager 제거",
        "four_agent_raw_blend": "4-agent 원점수 혼합",
        "four_agent_risk_adjusted": "4-agent 위험보정",
        "four_agent_plus_liquidity": "4-agent+유동성",
        "risk_manager_raw_only": "RiskManager 단독",
        "remove_analyst": "Analyst 제거",
        "remove_quant": "Quant 제거",
        "remove_chartist": "Chartist 제거",
        "analyst_only": "Analyst 단독",
        "quant_only": "Quant 단독",
        "chartist_only": "Chartist 단독",
    }
    return mapping.get(str(value), str(value))


def _ko_horizon(value: Any) -> str:
    return "단타" if str(value) == "short" else "장타"


def _fmt_pct(value: Any) -> str:
    return f"{_float(value):.2f}%"


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _strategy_from_name(name: str) -> str:
    for strategy in STRATEGY_IDS:
        if name.endswith(f"{strategy}.json"):
            return strategy
    return ""


def _horizon_from_strategy(strategy_id: str) -> str:
    return "short" if strategy_id.startswith("short") else "long"


def _period_from_task_id(task_id: str, strategy_id: str) -> str:
    prefix = "proof-"
    raw = task_id[:-len(strategy_id)].rstrip("-") if strategy_id and task_id.endswith(strategy_id) else task_id
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    parts = raw.split("-", 1)
    return parts[1] if len(parts) > 1 else raw


def _agent_total(agent_scores: Dict[str, Any], agent: str, default: Any = 50.0) -> float:
    payload = agent_scores.get(agent) if isinstance(agent_scores.get(agent), dict) else {}
    return _float(payload.get("total_score"), default)


def _risk_total(agent_scores: Dict[str, Any], key: str, default: Any = 50.0) -> float:
    payload = agent_scores.get("risk_manager") if isinstance(agent_scores.get("risk_manager"), dict) else {}
    return _float(payload.get(key), default)


def _liquidity_score(row: Dict[str, Any]) -> float:
    volume_ratio = _float(row.get("volume_ratio_20d"), 0.0)
    volatility = _float(row.get("volatility_20d"), 0.0)
    score = 35.0 + min(max(volume_ratio, 0.0), 3.0) * 18.0 - max(0.0, volatility - 0.8) * 20.0
    return max(0.0, min(100.0, score))


def _float(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _mean(values: Iterable[Any]) -> float:
    parsed = [_float(value) for value in values]
    return sum(parsed) / len(parsed) if parsed else 0.0


def _compound_return(period_returns_pct: Iterable[Any]) -> float:
    equity = 1.0
    for value in period_returns_pct:
        equity *= 1.0 + _float(value) / 100.0
    return (equity - 1.0) * 100.0


def _mdd(period_returns_pct: Iterable[Any]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in period_returns_pct:
        equity *= 1.0 + _float(value) / 100.0
        peak = max(peak, equity)
        if peak > 0:
            worst = min(worst, equity / peak - 1.0)
    return worst * 100.0


if __name__ == "__main__":
    raise SystemExit(main())
