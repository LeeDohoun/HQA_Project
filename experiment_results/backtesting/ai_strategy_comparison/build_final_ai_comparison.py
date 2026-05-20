#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "comparison_table"

MULTI_AGENT_SUMMARIES = [
    BASE_DIR / "source_multi_agent_runs_2023_2024" / "ai_short_long_validation_summary.csv",
    BASE_DIR / "source_multi_agent_runs" / "ai_short_long_validation_summary.csv",
]

TECHNICAL_SUMMARIES = [
    BASE_DIR / "technical_long_runs_2023_2024" / "technical-baselines-ai.csv",
    BASE_DIR / "source_comparison_build" / "technical_long" / "technical-baselines-ai.csv",
    BASE_DIR / "source_comparison_build" / "technical_short" / "technical-baselines-ai.csv",
    BASE_DIR / "technical_baseline_runs_2023_2026q1" / "technical-baselines-ai.csv",
]

MULTI_AGENT_RUN_DIRS = [
    BASE_DIR / "source_multi_agent_runs_2023_2024" / "runs",
    BASE_DIR / "source_multi_agent_runs" / "runs",
]

CENTER_STRATEGY = {
    "short": "short_hybrid_05",
    "long": "long_hybrid_05",
}

PERIOD_ALIASES = {
    "validation_2023": "2023",
    "2023": "2023",
    "validation_2024": "2024",
    "2024": "2024",
    "tune_2025": "2025",
    "2025": "2025",
    "validation_2026q1": "2026Q1",
    "2026q1": "2026Q1",
    "recent_2026apr_may": "2026 Apr-May",
}

PERIOD_ORDER = {
    "2023": 0,
    "2024": 1,
    "2025": 2,
    "2026Q1": 3,
    "2026 Apr-May": 4,
}

HORIZON_ORDER = {"short": 0, "long": 1}

STRATEGY_ORDER = {
    "multi_agent_hybrid": 0,
    "multi_agent_llm_only": 1,
    "deterministic": 2,
    "technical": 3,
}

TECHNICAL_ORDER = {
    "rsi_oversold": 0,
    "rsi_ranked": 1,
    "bollinger_lower": 2,
    "bollinger_ranked": 3,
    "momentum_20d": 4,
    "vol_adjusted_momentum": 5,
}

CORE_PERIODS = ["2023", "2024", "2025", "2026Q1"]
CORE_HORIZONS = ["short", "long"]
RSI_BOLLINGER = {"rsi_oversold", "rsi_ranked", "bollinger_lower", "bollinger_ranked"}

MULTI_AGENT_STRATEGY_DEFS = {
    "deterministic_short": {
        "label": "Short deterministic baseline",
        "horizon": "short",
        "rebalance": "W",
        "top_n": 3,
        "hold_days": 5,
        "llm_weight": 0.0,
        "llm_scope": "",
    },
    "short_hybrid_05": {
        "label": "Short multi-agent hybrid",
        "horizon": "short",
        "rebalance": "W",
        "top_n": 3,
        "hold_days": 5,
        "llm_weight": 0.5,
        "llm_scope": "broad",
    },
    "short_llm_only": {
        "label": "Short multi-agent LLM-only",
        "horizon": "short",
        "rebalance": "W",
        "top_n": 3,
        "hold_days": 5,
        "llm_weight": 1.0,
        "llm_scope": "broad",
    },
    "deterministic_long": {
        "label": "Long deterministic baseline",
        "horizon": "long",
        "rebalance": "M",
        "top_n": 3,
        "hold_days": 60,
        "llm_weight": 0.0,
        "llm_scope": "",
    },
    "long_hybrid_05": {
        "label": "Long multi-agent hybrid",
        "horizon": "long",
        "rebalance": "M",
        "top_n": 3,
        "hold_days": 60,
        "llm_weight": 0.5,
        "llm_scope": "broad",
    },
    "long_llm_only": {
        "label": "Long multi-agent LLM-only",
        "horizon": "long",
        "rebalance": "M",
        "top_n": 3,
        "hold_days": 60,
        "llm_weight": 1.0,
        "llm_scope": "broad",
    },
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = _load_all_rows()
    rows = _dedupe_rows(rows)
    rows = _filter_core_rows(rows)
    _attach_center_deltas(rows)
    rows.sort(key=_sort_key)

    csv_path = OUT_DIR / "multi-agent-centered-comparison.csv"
    json_path = OUT_DIR / "multi-agent-centered-comparison.json"
    report_path = OUT_DIR / "multi-agent-centered-comparison.md"
    audit_path = BASE_DIR / "BACKTEST_COVERAGE_AUDIT.md"
    main_report_path = BASE_DIR / "AI_STRATEGY_COMPARISON_REPORT.md"

    fieldnames = [
        "period",
        "source_period",
        "period_role",
        "horizon",
        "strategy_id",
        "strategy_group",
        "strategy_label",
        "rebalance",
        "top_n",
        "hold_days",
        "llm_weight",
        "llm_scope",
        "traded_rebalance_count",
        "position_count",
        "total_return_pct",
        "benchmark_return_pct",
        "excess_return_pct",
        "mdd_pct",
        "sharpe",
        "win_rate_pct",
        "prediction_hit_rate_pct",
        "status",
        "result_json",
        "center_multi_agent_strategy",
        "center_total_return_pct",
        "center_excess_return_pct",
        "center_mdd_pct",
        "return_delta_vs_center_multi_agent_pct",
        "excess_delta_vs_center_multi_agent_pct",
        "mdd_delta_vs_center_multi_agent_pct",
        "beats_center_return",
        "is_center_multi_agent",
    ]
    _write_csv(csv_path, rows, fieldnames)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "theme": "AI",
        "center_strategy": CENTER_STRATEGY,
        "row_count": len(rows),
        "coverage": _coverage(rows),
        "summary": _period_horizon_summary(rows),
        "artifacts": {
            "csv": str(csv_path),
            "json": str(json_path),
            "report": str(report_path),
            "coverage_audit": str(audit_path),
            "main_report": str(main_report_path),
        },
        "rows": rows,
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    report = _render_report(metadata)
    report_path.write_text(report, encoding="utf-8")
    main_report_path.write_text(report, encoding="utf-8")
    audit_path.write_text(_render_audit(metadata), encoding="utf-8")

    print(f"wrote {csv_path}")
    print(f"wrote {json_path}")
    print(f"wrote {report_path}")
    print(f"wrote {audit_path}")
    print(f"wrote {main_report_path}")
    return 0


def _load_all_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in MULTI_AGENT_SUMMARIES:
        if path.exists():
            rows.extend(_load_multi_agent_rows(path))
    for path in MULTI_AGENT_RUN_DIRS:
        if path.exists():
            rows.extend(_load_multi_agent_run_rows(path))
    for path in TECHNICAL_SUMMARIES:
        if path.exists():
            rows.extend(_load_technical_rows(path))
    return rows


def _load_multi_agent_rows(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in _read_csv(path):
        horizon = raw.get("horizon", "")
        strategy_id = raw.get("strategy_id", "")
        group = _multi_agent_group(strategy_id)
        period = _normalize_period(raw.get("period", ""))
        out.append(
            {
                "period": period,
                "source_period": raw.get("period", ""),
                "period_role": raw.get("period_role", ""),
                "horizon": horizon,
                "strategy_id": strategy_id,
                "strategy_group": group,
                "strategy_label": raw.get("strategy_label", strategy_id),
                "rebalance": raw.get("rebalance", ""),
                "top_n": _to_int(raw.get("top_n")),
                "hold_days": _to_int(raw.get("hold_days")),
                "llm_weight": _to_float(raw.get("llm_weight")),
                "llm_scope": raw.get("llm_candidate_scope", "") or raw.get("llm_scope", ""),
                "traded_rebalance_count": _to_int(raw.get("traded_rebalance_count")),
                "position_count": _to_int(raw.get("position_count")),
                "total_return_pct": _to_float(raw.get("total_return_pct")),
                "benchmark_return_pct": _to_float(raw.get("benchmark_return_pct")),
                "excess_return_pct": _to_float(raw.get("excess_return_pct")),
                "mdd_pct": _to_float(raw.get("mdd_pct")),
                "sharpe": _to_float(raw.get("sharpe")),
                "win_rate_pct": _to_float(raw.get("win_rate_pct")),
                "prediction_hit_rate_pct": _to_float(raw.get("prediction_hit_rate_pct")),
                "status": raw.get("skip_reason") or raw.get("status") or "completed",
                "result_json": raw.get("result_json", ""),
                "_source_rank": 0,
            }
        )
    return out


def _load_multi_agent_run_rows(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for result_path in sorted(path.glob("proof-ai-*.json")):
        parsed = _parse_run_filename(result_path)
        if not parsed:
            continue
        source_period, strategy_id = parsed
        strategy = MULTI_AGENT_STRATEGY_DEFS[strategy_id]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        metrics = result.get("metrics") or {}
        period_meta = result.get("period") or {}
        horizon = strategy["horizon"]
        out.append(
            {
                "period": _normalize_period(source_period),
                "source_period": source_period,
                "period_role": _period_role(source_period),
                "horizon": horizon,
                "strategy_id": strategy_id,
                "strategy_group": _multi_agent_group(strategy_id),
                "strategy_label": strategy["label"],
                "rebalance": strategy["rebalance"],
                "top_n": strategy["top_n"],
                "hold_days": strategy["hold_days"],
                "llm_weight": strategy["llm_weight"],
                "llm_scope": strategy["llm_scope"],
                "traded_rebalance_count": _to_int(metrics.get("traded_rebalance_count")),
                "position_count": _to_int(metrics.get("position_count")),
                "total_return_pct": _to_float(metrics.get("total_return_pct")),
                "benchmark_return_pct": _to_float(metrics.get("benchmark_return_pct")),
                "excess_return_pct": _to_float(metrics.get("excess_return_pct")),
                "mdd_pct": _to_float(metrics.get("mdd_pct")),
                "sharpe": _to_float(metrics.get("sharpe")),
                "win_rate_pct": _to_float(metrics.get("win_rate_pct")),
                "prediction_hit_rate_pct": _to_float(metrics.get("prediction_hit_rate_pct")),
                "status": result.get("skip_reason") or result.get("status") or "completed",
                "result_json": str(result_path),
                "_source_rank": 2,
                "_from_date": period_meta.get("from_date", ""),
                "_to_date": period_meta.get("to_date", ""),
            }
        )
    return out


def _parse_run_filename(path: Path) -> tuple[str, str] | None:
    stem = path.stem
    prefix = "proof-ai-"
    if not stem.startswith(prefix):
        return None
    rest = stem[len(prefix) :]
    for strategy_id in sorted(MULTI_AGENT_STRATEGY_DEFS, key=len, reverse=True):
        suffix = f"-{strategy_id}"
        if rest.endswith(suffix):
            return rest[: -len(suffix)], strategy_id
    return None


def _period_role(source_period: str) -> str:
    if source_period == "tune_2025":
        return "tuning_reference"
    if source_period.startswith("validation_"):
        return "validation"
    if source_period.startswith("recent_"):
        return "recent_validation"
    return "validation"


def _load_technical_rows(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    source_rank = _technical_source_rank(path)
    for raw in _read_csv(path):
        period = _normalize_period(raw.get("period", ""))
        horizon = _technical_horizon(raw)
        strategy_id = raw.get("baseline", "")
        out.append(
            {
                "period": period,
                "source_period": raw.get("period", ""),
                "period_role": "technical_baseline",
                "horizon": horizon,
                "strategy_id": strategy_id,
                "strategy_group": "technical",
                "strategy_label": raw.get("baseline_label", strategy_id),
                "rebalance": raw.get("rebalance", ""),
                "top_n": _to_int(raw.get("top_n")),
                "hold_days": _to_int(raw.get("hold_days")),
                "llm_weight": 0.0,
                "llm_scope": "",
                "traded_rebalance_count": _to_int(raw.get("traded_rebalance_count")),
                "position_count": _to_int(raw.get("position_count")),
                "total_return_pct": _to_float(raw.get("total_return_pct")),
                "benchmark_return_pct": _to_float(raw.get("benchmark_return_pct")),
                "excess_return_pct": _to_float(raw.get("excess_return_pct")),
                "mdd_pct": _to_float(raw.get("mdd_pct")),
                "sharpe": _to_float(raw.get("sharpe")),
                "win_rate_pct": _to_float(raw.get("win_rate_pct")),
                "prediction_hit_rate_pct": _to_float(raw.get("prediction_hit_rate_pct")),
                "status": "completed" if _to_int(raw.get("position_count")) > 0 else "completed_no_trade",
                "result_json": raw.get("result_json", ""),
                "_source_rank": source_rank,
            }
        )
    return out


def _read_csv(path: Path) -> Iterable[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _multi_agent_group(strategy_id: str) -> str:
    if strategy_id in {"short_hybrid_05", "long_hybrid_05"}:
        return "multi_agent_hybrid"
    if strategy_id in {"short_llm_only", "long_llm_only"}:
        return "multi_agent_llm_only"
    if strategy_id.startswith("deterministic_"):
        return "deterministic"
    return "multi_agent_other"


def _technical_horizon(row: Dict[str, str]) -> str:
    rebalance = (row.get("rebalance") or "").upper()
    hold_days = _to_int(row.get("hold_days"))
    if rebalance == "M" or hold_days >= 30:
        return "long"
    return "short"


def _technical_source_rank(path: Path) -> int:
    value = str(path)
    if "source_comparison_build" in value:
        return 0
    if "technical_long_runs_2023_2024" in value:
        return 0
    return 1


def _normalize_period(value: str) -> str:
    return PERIOD_ALIASES.get(value, value)


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for row in rows:
        key = (
            row["period"],
            row["horizon"],
            row["strategy_id"],
            row["rebalance"],
            row["hold_days"],
        )
        existing = best.get(key)
        if existing is None or row.get("_source_rank", 9) < existing.get("_source_rank", 9):
            best[key] = row
    clean_rows = []
    for row in best.values():
        row.pop("_source_rank", None)
        clean_rows.append(row)
    return clean_rows


def _filter_core_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in rows if row["period"] in CORE_PERIODS and row["horizon"] in CORE_HORIZONS]


def _attach_center_deltas(rows: List[Dict[str, Any]]) -> None:
    centers: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        expected = CENTER_STRATEGY.get(row["horizon"])
        if row["strategy_id"] == expected:
            centers[(row["period"], row["horizon"])] = row

    for row in rows:
        center_id = CENTER_STRATEGY.get(row["horizon"], "")
        center = centers.get((row["period"], row["horizon"]))
        row["center_multi_agent_strategy"] = center_id
        row["is_center_multi_agent"] = row["strategy_id"] == center_id
        if center:
            row["center_total_return_pct"] = center["total_return_pct"]
            row["center_excess_return_pct"] = center["excess_return_pct"]
            row["center_mdd_pct"] = center["mdd_pct"]
            row["return_delta_vs_center_multi_agent_pct"] = _round2(
                row["total_return_pct"] - center["total_return_pct"]
            )
            row["excess_delta_vs_center_multi_agent_pct"] = _round2(
                row["excess_return_pct"] - center["excess_return_pct"]
            )
            row["mdd_delta_vs_center_multi_agent_pct"] = _round2(row["mdd_pct"] - center["mdd_pct"])
            row["beats_center_return"] = row["total_return_pct"] > center["total_return_pct"]
        else:
            row["center_total_return_pct"] = ""
            row["center_excess_return_pct"] = ""
            row["center_mdd_pct"] = ""
            row["return_delta_vs_center_multi_agent_pct"] = ""
            row["excess_delta_vs_center_multi_agent_pct"] = ""
            row["mdd_delta_vs_center_multi_agent_pct"] = ""
            row["beats_center_return"] = False


def _coverage(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for period in CORE_PERIODS:
        for horizon in CORE_HORIZONS:
            subset = [row for row in rows if row["period"] == period and row["horizon"] == horizon]
            strategy_ids = {row["strategy_id"] for row in subset}
            has_center = CENTER_STRATEGY[horizon] in strategy_ids
            has_deterministic = f"deterministic_{horizon}" in strategy_ids
            has_rsi = bool({"rsi_oversold", "rsi_ranked"} & strategy_ids)
            has_bollinger = bool({"bollinger_lower", "bollinger_ranked"} & strategy_ids)
            status = "완료" if has_center and has_deterministic and has_rsi and has_bollinger else "미완료"
            out.append(
                {
                    "period": period,
                    "horizon": horizon,
                    "has_center_multi_agent": has_center,
                    "has_deterministic": has_deterministic,
                    "has_rsi": has_rsi,
                    "has_bollinger": has_bollinger,
                    "row_count": len(subset),
                    "status": status,
                }
            )
    return out


def _period_horizon_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for period in sorted({row["period"] for row in rows}, key=lambda x: PERIOD_ORDER.get(x, 99)):
        for horizon in CORE_HORIZONS:
            subset = [row for row in rows if row["period"] == period and row["horizon"] == horizon]
            if not subset:
                continue
            center = next((row for row in subset if row["strategy_id"] == CENTER_STRATEGY[horizon]), None)
            deterministic = next((row for row in subset if row["strategy_id"] == f"deterministic_{horizon}"), None)
            rsi_bollinger = [row for row in subset if row["strategy_id"] in RSI_BOLLINGER]
            technical = [row for row in subset if row["strategy_group"] == "technical"]
            best_basic = _best_by_return(rsi_bollinger)
            best_technical = _best_by_return(technical)
            winner = _best_by_return(subset)
            summary.append(
                {
                    "period": period,
                    "horizon": horizon,
                    "center_strategy": CENTER_STRATEGY[horizon],
                    "center_return_pct": center["total_return_pct"] if center else "",
                    "center_mdd_pct": center["mdd_pct"] if center else "",
                    "deterministic_strategy": deterministic["strategy_id"] if deterministic else "",
                    "deterministic_return_pct": deterministic["total_return_pct"] if deterministic else "",
                    "best_rsi_bollinger_strategy": best_basic["strategy_id"] if best_basic else "",
                    "best_rsi_bollinger_return_pct": best_basic["total_return_pct"] if best_basic else "",
                    "best_technical_strategy": best_technical["strategy_id"] if best_technical else "",
                    "best_technical_return_pct": best_technical["total_return_pct"] if best_technical else "",
                    "winner_strategy": winner["strategy_id"] if winner else "",
                    "winner_group": winner["strategy_group"] if winner else "",
                    "winner_return_pct": winner["total_return_pct"] if winner else "",
                    "verdict": _verdict(center, deterministic, best_basic, best_technical),
                }
            )
    return summary


def _best_by_return(rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    traded = [row for row in rows if row.get("position_count", 0) > 0]
    if not traded:
        return None
    return max(traded, key=lambda row: (float(row["total_return_pct"]), float(row["excess_return_pct"])))


def _verdict(
    center: Dict[str, Any] | None,
    deterministic: Dict[str, Any] | None,
    best_basic: Dict[str, Any] | None,
    best_technical: Dict[str, Any] | None,
) -> str:
    if not center:
        return "center_missing"
    wins = []
    losses = []
    for label, row in (
        ("deterministic", deterministic),
        ("rsi_bollinger", best_basic),
        ("best_technical", best_technical),
    ):
        if not row:
            continue
        if center["total_return_pct"] >= row["total_return_pct"]:
            wins.append(label)
        else:
            losses.append(label)
    if not losses:
        return "multi_agent_best_or_tied"
    if wins and losses:
        return "mixed"
    return "multi_agent_lagged"


def _render_report(metadata: Dict[str, Any]) -> str:
    rows = metadata["rows"]
    summary = metadata["summary"]
    coverage = metadata["coverage"]
    done_count = sum(1 for row in coverage if row["status"] == "완료")
    core_count = len(coverage)
    center_rows = [row for row in rows if row["is_center_multi_agent"] and row["period"] in CORE_PERIODS]
    basic_pairs = [row for row in summary if row["period"] in CORE_PERIODS and row["best_rsi_bollinger_strategy"]]
    basic_wins = sum(
        1
        for row in basic_pairs
        if row["center_return_pct"] != "" and row["center_return_pct"] >= row["best_rsi_bollinger_return_pct"]
    )
    deterministic_pairs = [row for row in summary if row["period"] in CORE_PERIODS and row["deterministic_strategy"]]
    deterministic_wins = sum(
        1
        for row in deterministic_pairs
        if row["center_return_pct"] != "" and row["center_return_pct"] >= row["deterministic_return_pct"]
    )

    lines = [
        "# AI 테마 백테스트 최종 비교",
        "",
        f"- 생성 시각: {metadata['generated_at']}",
        "- 비교 중심: 단타 `short_hybrid_05`, 장타 `long_hybrid_05`",
        "- 비교 대상: deterministic 규칙기반, RSI, 볼린저밴드, 모멘텀, 변동성 조정 모멘텀",
        "- 비용/리스크 조건: 거래비용 15bp, 슬리피지 5bp, 시장충격 5bp, 유동성/변동성/급등 필터, 15% 트레일링 스탑",
        "",
        "## 결론 요약",
        "",
        f"- 핵심 커버리지: {done_count}/{core_count} 완료",
        f"- multi-agent hybrid가 RSI/볼밴 최고 전략 이상이었던 경우: {basic_wins}/{len(basic_pairs)}",
        f"- multi-agent hybrid가 deterministic 규칙기반 이상이었던 경우: {deterministic_wins}/{len(deterministic_pairs)}",
        "- 이 표는 수익률만 보지 않고 MDD, 샤프, 거래 발생 여부까지 같이 봅니다.",
        "",
        "## 핵심 기간별 요약",
        "",
        "| 기간 | 구간 | multi-agent | deterministic | RSI/볼밴 최고 | 기술전략 최고 | 최종 판정 |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for row in summary:
        if row["period"] not in CORE_PERIODS:
            continue
        lines.append(
            "| {period} | {horizon} | {center} | {det} | {basic} | {tech} | {verdict} |".format(
                period=row["period"],
                horizon=_ko_horizon(row["horizon"]),
                center=_fmt_pct(row["center_return_pct"]),
                det=_fmt_pct(row["deterministic_return_pct"]),
                basic=_fmt_strategy_pct(row["best_rsi_bollinger_strategy"], row["best_rsi_bollinger_return_pct"]),
                tech=_fmt_strategy_pct(row["best_technical_strategy"], row["best_technical_return_pct"]),
                verdict=row["verdict"],
            )
        )

    lines.extend(
        [
            "",
            "## 전체 비교표",
            "",
            "| 기간 | 구간 | 전략 | 그룹 | 수익률 | 벤치마크 | 초과수익 | MDD | 샤프 | 중심 대비 수익률 |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| {period} | {horizon} | `{strategy}` | {group} | {ret} | {bench} | {excess} | {mdd} | {sharpe} | {delta} |".format(
                period=row["period"],
                horizon=_ko_horizon(row["horizon"]),
                strategy=row["strategy_id"],
                group=row["strategy_group"],
                ret=_fmt_pct(row["total_return_pct"]),
                bench=_fmt_pct(row["benchmark_return_pct"]),
                excess=_fmt_pct(row["excess_return_pct"]),
                mdd=_fmt_pct(row["mdd_pct"]),
                sharpe=_fmt_num(row["sharpe"]),
                delta=_fmt_pct(row["return_delta_vs_center_multi_agent_pct"]),
            )
        )

    lines.extend(
        [
            "",
            "## 리스크 해석",
            "",
            "- `MDD`는 중간에 가장 크게 빠진 폭입니다. 수익률이 높아도 MDD가 깊으면 실제 운용 부담이 큽니다.",
            "- `completed_no_trade` 행은 조건은 돌렸지만 리스크/신호 조건 때문에 실제 매수가 없었던 전략입니다.",
            "- 이 결과는 백테스트이며, 실제 주문 지연, 호가 공백, 데이터 정정, 실시간 체결 실패는 별도 검증이 필요합니다.",
            "- multi-agent는 3개 하위 에이전트인 analyst, quant, chartist와 상위 RiskManager 점수 조합 구조로 실행했습니다.",
            "",
            "## 산출물",
            "",
        ]
    )
    for key, value in metadata["artifacts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _render_audit(metadata: Dict[str, Any]) -> str:
    lines = [
        "# AI 테마 백테스트 커버리지 점검",
        "",
        f"- 생성 시각: {metadata['generated_at']}",
        "- 완료 기준: 같은 기간/구간에 multi-agent 대표 전략, deterministic, RSI, 볼밴 결과가 모두 있어야 완료",
        "",
        "## 핵심 커버리지",
        "",
        "| 기간 | 구간 | multi-agent 대표 | deterministic | RSI | 볼밴 | 행 수 | 상태 |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for row in metadata["coverage"]:
        lines.append(
            "| {period} | {horizon} | {center} | {det} | {rsi} | {boll} | {count} | {status} |".format(
                period=row["period"],
                horizon=_ko_horizon(row["horizon"]),
                center=_yes(row["has_center_multi_agent"]),
                det=_yes(row["has_deterministic"]),
                rsi=_yes(row["has_rsi"]),
                boll=_yes(row["has_bollinger"]),
                count=row["row_count"],
                status=row["status"],
            )
        )

    missing = [row for row in metadata["coverage"] if row["status"] != "완료"]
    lines.extend(["", "## 남은 부분", ""])
    if not missing:
        lines.append("- AI 테마의 핵심 비교 축은 완료되었습니다.")
    else:
        for row in missing:
            lines.append(f"- {row['period']} {_ko_horizon(row['horizon'])}: 일부 비교 결과가 없습니다.")

    lines.extend(
        [
            "",
            "## 바로 볼 파일",
            "",
            "- `comparison_table/multi-agent-centered-comparison.csv`",
            "- `comparison_table/multi-agent-centered-comparison.md`",
            "- `AI_STRATEGY_COMPARISON_REPORT.md`",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _sort_key(row: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        PERIOD_ORDER.get(row["period"], 99),
        HORIZON_ORDER.get(row["horizon"], 99),
        STRATEGY_ORDER.get(row["strategy_group"], 99),
        TECHNICAL_ORDER.get(row["strategy_id"], 99),
        row["strategy_id"],
    )


def _to_int(value: Any) -> int:
    try:
        if value in ("", None):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        if value in ("", None):
            return 0.0
        return _round2(float(value))
    except (TypeError, ValueError):
        return 0.0


def _round2(value: float) -> float:
    return round(float(value), 2)


def _fmt_pct(value: Any) -> str:
    if value == "":
        return ""
    return f"{float(value):.2f}%"


def _fmt_num(value: Any) -> str:
    if value == "":
        return ""
    return f"{float(value):.3f}"


def _fmt_strategy_pct(strategy: Any, value: Any) -> str:
    if not strategy:
        return ""
    return f"`{strategy}` {_fmt_pct(value)}"


def _ko_horizon(value: str) -> str:
    return {"short": "단타", "long": "장타"}.get(value, value)


def _yes(value: bool) -> str:
    return "있음" if value else "없음"


if __name__ == "__main__":
    raise SystemExit(main())
