#!/usr/bin/env python3
from __future__ import annotations

"""Replay cached multi-agent scores to compare scoring designs without LLM calls.

Every score comes from an existing multi-agent cache. A cache miss raises inside
the scorer, the backtest records it as a warning, and the run is reported as
incomplete; no model is ever constructed or invoked.

Profiles (fixed before looking at results):
- current_4agent: the cached multi-agent llm_score, unchanged (replay check).
- analyst_text: 0.5 * Analyst catalyst_score + 0.5 * (100 - Analyst risk_score).
- random_control: values drawn from the same cache's analyst_text distribution,
  keyed by (seed, date, stock). It measures how much a score of that shape moves
  results by chance.
"""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.leader_backtest import run_leader_backtest
from backtesting.proof_validation import (
    PeriodSpec,
    StrategySpec,
    default_strategies,
    parse_periods,
    run_proof_validation,
)

PROFILES = ("current_4agent", "analyst_text", "random_control")

CacheIndex = Dict[Tuple[str, ...], Dict[str, Any]]


@dataclass(frozen=True)
class ReplaySuite:
    name: str
    theme: str
    theme_key: str
    periods: str
    cache_path: str
    top_k: int


# Cache files and run settings recorded in the 2026-09-13 backup manifests.
SUITES = (
    ReplaySuite(
        "fresh_representative", "AI", "ai",
        "validation_2023:20230101:20231231:validation,validation_2024:20240101:20241231:validation",
        "experiment_results/backtesting/agent_architecture_validation/fresh_representative_llm_runs/llm_cache/ai.multi_agent.jsonl",
        10,
    ),
    ReplaySuite(
        "fresh_representative", "반도체", "반도체",
        "validation_2023:20230101:20231231:validation,validation_2024:20240101:20241231:validation,"
        "tune_2025:20250101:20251231:tuning_reference,validation_2026q1:20260101:20260331:validation",
        "experiment_results/backtesting/agent_architecture_validation/fresh_representative_llm_runs/llm_cache/반도체.multi_agent.jsonl",
        10,
    ),
    ReplaySuite(
        "uncontaminated", "AI", "ai",
        "validation_2024:20240101:20241231:validation",
        "experiment_results/backtesting/agent_architecture_validation/uncontaminated_4agent_runs/llm_cache/ai.pure4agent.jsonl",
        0,
    ),
    ReplaySuite(
        "uncontaminated", "AI", "ai",
        "validation_2023:20230101:20231231:validation",
        "experiment_results/backtesting/agent_architecture_validation/uncontaminated_4agent_runs_ai2023/llm_cache/ai.pure4agent.jsonl",
        0,
    ),
    ReplaySuite(
        "uncontaminated", "반도체", "반도체",
        "validation_2024:20240101:20241231:validation",
        "experiment_results/backtesting/agent_architecture_validation/uncontaminated_4agent_runs_semiconductor2024/llm_cache/반도체.pure4agent.jsonl",
        0,
    ),
)

# Settings shared by every recorded suite.
BACKTEST_SETTINGS = {
    "transaction_cost_bps": 15.0,
    "slippage_bps": 5.0,
    "market_impact_bps": 5.0,
    "min_market_breadth_pct": 40.0,
    "max_volatility_20d": 1.2,
    "max_return_5d": 0.35,
    "max_return_20d": 0.9,
    "trailing_stop_pct": 15.0,
}


def _bounded(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def analyst_text_score(result: Dict[str, Any]) -> float:
    analyst = (result.get("llm_agent_scores") or {}).get("analyst") or {}
    if "catalyst_score" not in analyst or "risk_score" not in analyst:
        raise KeyError("cached result has no Analyst catalyst/risk scores")
    return _bounded(0.5 * float(analyst["catalyst_score"]) + 0.5 * (100.0 - float(analyst["risk_score"])))


def load_cache_index(path: str | Path) -> CacheIndex:
    """Index cache rows by everything except prompt version, provider and model names."""
    index: CacheIndex = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            parts = str(row.get("cache_key") or "").split("|")
            result = row.get("result")
            if len(parts) != 11 or not isinstance(result, dict):
                continue
            index[(parts[1], *parts[5:])] = result
    return index


def _row_key(horizon: str, theme_key: str, as_of_ymd: str, row: Dict[str, Any]) -> Tuple[str, ...]:
    return (
        horizon,
        theme_key,
        as_of_ymd,
        str(row.get("stock_code") or ""),
        str(round(float(row.get("leader_score") or 0.0), 2)),
        str(round(float(row.get("return_20d") or 0.0), 4)),
        str(round(float(row.get("return_60d") or 0.0), 4)),
    )


class CachedReplayScorer:
    """llm_scorer replacement for run_leader_backtest that only reads a cache."""

    def __init__(self, *, index: CacheIndex, theme_key: str, horizon: str, profile: str, seed: int = 0) -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile: {profile}")
        self.index = index
        self.theme_key = theme_key
        self.horizon = horizon
        self.profile = profile
        self.seed = seed
        self.hits = 0
        self.misses = 0
        self._control_values = sorted(
            analyst_text_score(result)
            for key, result in index.items()
            if key[0] == horizon and "analyst" in (result.get("llm_agent_scores") or {})
        )

    def metadata(self) -> Dict[str, Any]:
        return {"mode": f"replay_{self.profile}", "horizon": self.horizon, "seed": self.seed,
                "cache_rows": len(self.index), "llm_calls": 0}

    def score(self, *, as_of_ymd: str, row: Dict[str, Any]) -> Dict[str, Any]:
        result = self.index.get(_row_key(self.horizon, self.theme_key, as_of_ymd, row))
        if result is None:
            self.misses += 1
            raise KeyError(f"replay cache miss: {as_of_ymd} {row.get('stock_code')}")
        self.hits += 1
        if self.profile == "current_4agent":
            return dict(result)
        if self.profile == "analyst_text":
            score = analyst_text_score(result)
        else:
            digest = hashlib.sha256(f"{self.seed}|{as_of_ymd}|{row.get('stock_code')}".encode()).digest()
            score = self._control_values[int.from_bytes(digest[:8], "big") % len(self._control_values)]
        # No llm_agent_scores: the short-horizon Chartist floor must not alter these profiles.
        return {"llm_score": score, "llm_confidence": 50, "llm_horizon": self.horizon,
                "llm_mode": f"replay_{self.profile}"}


@dataclass(frozen=True)
class Variant:
    profile: str  # "deterministic" or one of PROFILES
    weight: float = 0.0
    seed: int = 0

    @property
    def variant_id(self) -> str:
        if self.profile == "deterministic":
            return "deterministic"
        suffix = f"_s{self.seed}" if self.profile == "random_control" else ""
        return f"{self.profile}_w{int(round(self.weight * 100)):02d}{suffix}"


def build_variants(weights: Iterable[float], control_seeds: int) -> List[Variant]:
    variants = [Variant("deterministic"), Variant("current_4agent", 0.5), Variant("current_4agent", 1.0)]
    for weight in weights:
        variants.append(Variant("analyst_text", weight))
        variants.extend(Variant("random_control", weight, seed) for seed in range(control_seeds))
    return variants


def _strategies_for(variant: Variant, top_k: int) -> List[StrategySpec]:
    base = {s.strategy_id: s for s in default_strategies(short_top_k=top_k, long_top_k=top_k)}
    if variant.profile == "deterministic":
        return [base["deterministic_short"], base["deterministic_long"]]
    return [
        replace(base[f"{horizon}_hybrid_05"], strategy_id=f"{horizon}_{variant.variant_id}",
                label=f"{horizon} {variant.variant_id}", llm_weight=variant.weight, is_baseline=False)
        for horizon in ("short", "long")
    ]


def run_variant(
    *, suite: ReplaySuite, variant: Variant, backup_root: Path, output_root: Path
) -> List[Dict[str, Any]]:
    index = load_cache_index(backup_root / suite.cache_path)
    periods: List[PeriodSpec] = parse_periods(suite.periods)
    scorers: Dict[str, CachedReplayScorer] = {}

    def runner(**kwargs: Any) -> Dict[str, Any]:
        if "llm_horizon" in kwargs:
            scorer = CachedReplayScorer(index=index, theme_key=suite.theme_key, horizon=kwargs["llm_horizon"],
                                        profile=variant.profile, seed=variant.seed)
            scorers[kwargs["task_id"]] = scorer
            kwargs = {**kwargs, "llm_scorer": scorer}
        return run_leader_backtest(**kwargs)

    period_tag = "_".join(period.name for period in periods)
    out_dir = output_root / suite.name / suite.theme_key / period_tag / variant.variant_id
    with contextlib.redirect_stdout(io.StringIO()):
        summary = run_proof_validation(
            data_dir=backup_root / "data", theme=suite.theme, theme_key=suite.theme_key, output_dir=out_dir,
            periods=periods, strategies=_strategies_for(variant, suite.top_k), runner=runner,
            resume_completed=False, **BACKTEST_SETTINGS,
        )
    rows: List[Dict[str, Any]] = []
    for row in summary["rows"]:
        scorer = scorers.get(row["task_id"])
        rows.append({
            "suite": suite.name, "theme_key": suite.theme_key, "period": row["period"],
            "horizon": row["horizon"], "variant": variant.variant_id, "profile": variant.profile,
            "weight": variant.weight, "seed": variant.seed, "status": row.get("status", ""),
            "excess_return_pct": row["excess_return_pct"], "total_return_pct": row["total_return_pct"],
            "mdd_pct": row["mdd_pct"], "sharpe": row["sharpe"], "position_count": row["position_count"],
            "cache_hits": scorer.hits if scorer else 0, "cache_misses": scorer.misses if scorer else 0,
        })
    return rows


def summarize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mean excess-return change versus the deterministic baseline, per suite and horizon."""
    baseline = {(r["suite"], r["theme_key"], r["period"], r["horizon"]): float(r["excess_return_pct"])
                for r in rows if r["profile"] == "deterministic"}
    groups: Dict[Tuple[str, str, str], List[float]] = {}
    for r in rows:
        if r["profile"] == "deterministic":
            continue
        delta = float(r["excess_return_pct"]) - baseline[(r["suite"], r["theme_key"], r["period"], r["horizon"])]
        groups.setdefault((r["suite"], r["horizon"], r["variant"]), []).append(delta)

    output: List[Dict[str, Any]] = []
    for (suite, horizon, variant), deltas in sorted(groups.items()):
        if "random_control" in variant:
            continue
        mean_delta = statistics.fmean(deltas)
        row = {"suite": suite, "horizon": horizon, "variant": variant, "cells": len(deltas),
               "mean_delta_vs_rule_pp": round(mean_delta, 2),
               "cells_better_than_rule": sum(d > 0 for d in deltas)}
        if variant.startswith("analyst_text_w"):
            weight_tag = variant.removeprefix("analyst_text_")
            controls = [statistics.fmean(v) for (s, h, name), v in groups.items()
                        if s == suite and h == horizon and name.startswith(f"random_control_{weight_tag}_")]
            if controls:
                row["control_runs"] = len(controls)
                row["control_mean_delta_pp"] = round(statistics.fmean(controls), 2)
                row["control_p05_pp"] = round(_quantile(controls, 0.05), 2)
                row["control_p95_pp"] = round(_quantile(controls, 0.95), 2)
                row["share_of_controls_at_or_above"] = round(sum(c >= mean_delta for c in controls) / len(controls), 3)
        output.append(row)
    return output


def _quantile(values: List[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields: List[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backup-root", required=True,
                        help="Extracted HQA_Project_data backup containing data/ and experiment_results/.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--weights", default="0.2,0.5", help="Comma-separated analyst_text blend weights.")
    parser.add_argument("--control-seeds", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--suites", default="", help="Comma-separated suite names; empty runs all.")
    args = parser.parse_args(argv)

    backup_root = Path(args.backup_root).resolve()
    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    selected = {name.strip() for name in args.suites.split(",") if name.strip()}
    weights = [float(w) for w in args.weights.split(",") if w.strip()]
    variants = build_variants(weights, args.control_seeds)

    jobs = [(suite, variant) for suite in SUITES if not selected or suite.name in selected for variant in variants]
    rows: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(run_variant, suite=suite, variant=variant, backup_root=backup_root,
                               output_root=output_root): (suite, variant) for suite, variant in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            suite, variant = futures[future]
            job_rows = future.result()
            rows.extend(job_rows)
            misses = sum(int(r["cache_misses"]) for r in job_rows)
            print(f"[REPLAY] {done}/{len(jobs)} {suite.name}/{suite.theme_key}/{variant.variant_id} misses={misses}",
                  flush=True)
    rows.sort(key=lambda r: (r["suite"], r["theme_key"], r["period"], r["horizon"], r["variant"]))

    summary = summarize(rows)
    _write_csv(output_root / "replay_runs.csv", rows)
    _write_csv(output_root / "replay_summary.csv", summary)
    (output_root / "replay_summary.json").write_text(
        json.dumps({"weights": weights, "control_seeds": args.control_seeds, "summary": summary,
                    "cache_misses": sum(int(r["cache_misses"]) for r in rows)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for row in summary:
        print("[REPLAY]", json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
