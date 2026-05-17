#!/usr/bin/env python3
from __future__ import annotations

"""Summarize multi-agent validation progress and remaining backtest work."""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.temporal_rag import normalize_ymd
from src.config.settings import get_data_dir


def build_status_report(
    *,
    data_dir: str | Path,
    output_dir: str | Path,
    theme: str = "AI",
    theme_key: str = "ai",
    champion_summary: str | Path,
    deterministic_summary: str | Path,
    technical_short_summary: str | Path,
    technical_long_summary: str | Path,
    extra_theme_keys: List[str] | None = None,
) -> Dict[str, Any]:
    data_root = Path(data_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    champion = _load_json(Path(champion_summary))
    deterministic = _load_json(Path(deterministic_summary))
    tech_short = _load_json(Path(technical_short_summary))
    tech_long = _load_json(Path(technical_long_summary))

    champion_rows = list(champion.get("rows") or [])
    deterministic_rows = list(deterministic.get("rows") or [])
    tech_short_rows = list(tech_short.get("rows") or [])
    tech_long_rows = list(tech_long.get("rows") or [])
    data_audit = _data_audit(data_root, theme_key)

    status = {
        "theme": theme,
        "theme_key": theme_key,
        "generated_at": datetime.now().isoformat(),
        "champion_definition": {
            "short": "short_hybrid_05: weekly rebalance, top 3, hold 5 trading days, multi_agent broad rerank top 10, llm_weight 0.5",
            "long": "long_hybrid_05: monthly rebalance, top 3, hold 60 trading days, multi_agent broad rerank top 10, llm_weight 0.5",
        },
        "cost_protocol": champion.get("protocol") or {},
        "completed": {
            "champion_realistic_cost_periods": _period_names(champion_rows),
            "extended_deterministic_periods": _period_names(deterministic_rows),
            "technical_short_rows": len(tech_short_rows),
            "technical_long_rows": len(tech_long_rows),
        },
        "data_audit": data_audit,
        "multi_theme_readiness": _multi_theme_readiness(data_root, extra_theme_keys or []),
        "champion_rows": champion_rows,
        "extended_deterministic_rows": deterministic_rows,
        "best_technical_short": _best_by_period(tech_short_rows),
        "best_technical_long": _best_by_period(tech_long_rows),
        "technical_short_rows": tech_short_rows,
        "technical_long_rows": tech_long_rows,
        "next_work": _next_work(data_audit),
    }

    json_path = out_dir / "multi-agent-validation-status.json"
    csv_path = out_dir / "multi-agent-validation-status-champion.csv"
    md_path = out_dir / "multi-agent-validation-status.md"
    status["artifacts"] = {
        "status_json": str(json_path),
        "champion_csv": str(csv_path),
        "status_md": str(md_path),
    }
    json_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, champion_rows)
    md_path.write_text(_render_markdown(status), encoding="utf-8")
    json_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"expected object json: {path}")
    return value


def _period_names(rows: Iterable[Dict[str, Any]]) -> List[str]:
    return sorted({str(row.get("period") or "") for row in rows if row.get("period")})


def _best_by_period(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        period = str(row.get("period") or "")
        if not period:
            continue
        current = best.get(period)
        if current is None or _rank(row) > _rank(current):
            best[period] = row
    return [best[key] for key in sorted(best)]


def _rank(row: Dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(row.get("excess_return_pct") or 0.0),
        float(row.get("sharpe") or 0.0),
        float(row.get("total_return_pct") or 0.0),
    )


def _data_audit(data_root: Path, theme_key: str) -> Dict[str, Any]:
    corpus_stats = _jsonl_date_stats(data_root / "canonical_index" / theme_key / "corpus.jsonl")
    chart_stats = _jsonl_date_stats(data_root / "raw" / "chart" / f"{theme_key}.jsonl")
    membership_stats = _membership_stats(data_root / "raw" / "theme_membership" / f"{theme_key}.jsonl")
    cache_stats = _multi_agent_cache_stats(data_root / "backtest_results" / "llm_cache" / theme_key)
    return {
        "corpus": corpus_stats,
        "chart": chart_stats,
        "theme_membership": membership_stats,
        "multi_agent_cache": cache_stats,
        "leakage_controls": [
            "TemporalRAG excludes documents with published date after as_of_date.",
            "Leader backtest computes selection features from known rows at or before as_of_date.",
            "Future returns are used only after selection for evaluation.",
        ],
        "known_limitations": [
            "Theme membership is inferred from local corpus evidence unless an official historical membership source is supplied.",
            "2023/2024 real multi-agent cache is absent, so those periods still require a local-model run before final comparison.",
            "Forum data starts later than price/news/DART data, so early-period sentiment evidence is sparse.",
        ],
    }


def _multi_theme_readiness(data_root: Path, theme_keys: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for theme_key in theme_keys:
        key = str(theme_key or "").strip()
        if not key:
            continue
        corpus = _jsonl_date_stats(data_root / "canonical_index" / key / "corpus.jsonl")
        chart = _jsonl_date_stats(data_root / "raw" / "chart" / f"{key}.jsonl")
        membership = _membership_stats(data_root / "raw" / "theme_membership" / f"{key}.jsonl")
        rows.append(
            {
                "theme_key": key,
                "corpus_rows": corpus.get("rows", 0),
                "corpus_min_date": corpus.get("min_date", ""),
                "corpus_max_date": corpus.get("max_date", ""),
                "chart_rows": chart.get("rows", 0),
                "chart_min_date": chart.get("min_date", ""),
                "chart_max_date": chart.get("max_date", ""),
                "membership_rows": membership.get("rows", 0),
                "ready_for_deterministic": bool(chart.get("rows", 0)),
                "ready_for_multi_agent": bool(chart.get("rows", 0) and corpus.get("rows", 0)),
                "membership_source_counts": membership.get("source_counts", {}),
            }
        )
    return rows


def _jsonl_date_stats(path: Path) -> Dict[str, Any]:
    source_counts: Counter[str] = Counter()
    years: Counter[str] = Counter()
    min_date = ""
    max_date = ""
    rows = 0
    dated = 0
    if not path.exists():
        return {"exists": False, "rows": 0}
    for row in _iter_jsonl(path):
        rows += 1
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source = str(row.get("source_type") or metadata.get("source_type") or "").strip() or "unknown"
        source_counts[source] += 1
        ymd = normalize_ymd(
            metadata.get("published_ymd")
            or metadata.get("published_at")
            or row.get("published_at")
            or metadata.get("timestamp")
            or row.get("timestamp")
            or row.get("date")
        )
        if not ymd:
            continue
        dated += 1
        years[ymd[:4]] += 1
        min_date = ymd if not min_date else min(min_date, ymd)
        max_date = ymd if not max_date else max(max_date, ymd)
    return {
        "exists": True,
        "rows": rows,
        "dated_rows": dated,
        "min_date": _fmt_ymd(min_date),
        "max_date": _fmt_ymd(max_date),
        "years": dict(sorted(years.items())),
        "source_counts": dict(sorted(source_counts.items())),
    }


def _membership_stats(path: Path) -> Dict[str, Any]:
    sources: Counter[str] = Counter()
    first_seen = []
    rows = 0
    if not path.exists():
        return {"exists": False, "rows": 0}
    for row in _iter_jsonl(path):
        rows += 1
        sources[str(row.get("source") or "unknown")] += 1
        ymd = normalize_ymd(row.get("first_seen_at"))
        if ymd:
            first_seen.append(ymd)
    return {
        "exists": True,
        "rows": rows,
        "source_counts": dict(sorted(sources.items())),
        "min_first_seen": _fmt_ymd(min(first_seen)) if first_seen else "",
        "max_first_seen": _fmt_ymd(max(first_seen)) if first_seen else "",
    }


def _multi_agent_cache_stats(cache_dir: Path) -> Dict[str, Any]:
    years: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    rows = 0
    if not cache_dir.exists():
        return {"exists": False, "rows": 0}
    for path in cache_dir.glob("*.multi_agent.jsonl"):
        for row in _iter_jsonl(path):
            rows += 1
            key = str(row.get("cache_key") or "")
            if key:
                versions[key.split("|")[0]] += 1
            ymd = _first_ymd_token(key)
            if ymd:
                years[ymd[:4]] += 1
    return {
        "exists": True,
        "rows": rows,
        "years": dict(sorted(years.items())),
        "prompt_versions": dict(sorted(versions.items())),
    }


def _first_ymd_token(text: str) -> str:
    for part in str(text or "").split("|"):
        match = re.match(r"(20\d{6})", part)
        if match:
            return match.group(1)
    return ""


def _next_work(data_audit: Dict[str, Any]) -> List[str]:
    cache_years = set((data_audit.get("multi_agent_cache") or {}).get("years") or {})
    work = [
        "Run real multi-agent champion validation for 2023 and 2024 with the same cost protocol.",
        "Repeat the champion protocol on semiconductor, robotics, bio, power equipment, shipbuilding, and secondary battery themes.",
        "Add an official point-in-time theme-membership source to reduce survivorship bias.",
        "Stress test higher slippage/liquidity constraints and compare max drawdown, loss streak, and worst period return.",
    ]
    if not {"2023", "2024"}.issubset(cache_years):
        work.insert(0, "2023/2024 multi-agent cache is missing; run those periods before making a final out-of-sample claim.")
    return work


def _render_markdown(status: Dict[str, Any]) -> str:
    lines = [
        "# Multi-Agent Validation Status",
        "",
        f"- Theme: {status['theme']} ({status['theme_key']})",
        f"- Generated at: {status['generated_at']}",
        "",
        "## Fixed Champion Strategy",
        "",
        f"- Short: {status['champion_definition']['short']}",
        f"- Long: {status['champion_definition']['long']}",
        "",
        "## Cost And Risk Protocol",
        "",
        "| Input | Value |",
        "|---|---:|",
    ]
    for key, value in (status.get("cost_protocol") or {}).items():
        lines.append(f"| {key} | {value} |")

    lines.extend(
        [
            "",
            "## Champion Results With Realistic Costs",
            "",
            "These rows rerun the fixed multi-agent champion set where cache/local-model evidence is available.",
            "",
            "| Period | Horizon | Strategy | Status | Return | Benchmark | Excess | Delta vs Deterministic | MDD | Worst Period | Loss Streak |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in status["champion_rows"]:
        lines.append(
            "| {period} | {horizon} | {strategy} | {status} | {ret:.2f}% | {bench:.2f}% | {excess:.2f}% | {delta:.2f}% | {mdd:.2f}% | {worst:.2f}% | {streak} |".format(
                period=row.get("period", ""),
                horizon=row.get("horizon", ""),
                strategy=row.get("strategy_id", ""),
                status=row.get("skip_reason") or row.get("status") or "completed",
                ret=float(row.get("total_return_pct") or 0.0),
                bench=float(row.get("benchmark_return_pct") or 0.0),
                excess=float(row.get("excess_return_pct") or 0.0),
                delta=float(row.get("excess_delta_vs_baseline_pct") or 0.0),
                mdd=float(row.get("mdd_pct") or 0.0),
                worst=float(row.get("worst_period_return_pct") or 0.0),
                streak=int(row.get("max_consecutive_loss_periods") or 0),
            )
        )

    lines.extend(
        [
            "",
            "## Extended Baselines",
            "",
            "2023/2024 are currently extended for deterministic and non-LLM baselines. Real multi-agent must still be run for those years.",
            "",
            "| Horizon | Period | Best Non-LLM Baseline | Return | Benchmark | Excess | MDD |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for horizon, rows in [("short", status["best_technical_short"]), ("long", status["best_technical_long"])]:
        for row in rows:
            lines.append(
                "| {horizon} | {period} | {strategy} | {ret:.2f}% | {bench:.2f}% | {excess:.2f}% | {mdd:.2f}% |".format(
                    horizon=horizon,
                    period=row.get("period", ""),
                    strategy=row.get("baseline", ""),
                    ret=float(row.get("total_return_pct") or 0.0),
                    bench=float(row.get("benchmark_return_pct") or 0.0),
                    excess=float(row.get("excess_return_pct") or 0.0),
                    mdd=float(row.get("mdd_pct") or 0.0),
                )
            )

    audit = status["data_audit"]
    lines.extend(
        [
            "",
            "## Temporal Leakage Audit",
            "",
            f"- Corpus date range: {audit['corpus'].get('min_date', '')} to {audit['corpus'].get('max_date', '')}, rows={audit['corpus'].get('rows', 0)}",
            f"- Chart date range: {audit['chart'].get('min_date', '')} to {audit['chart'].get('max_date', '')}, rows={audit['chart'].get('rows', 0)}",
            f"- Theme membership rows: {audit['theme_membership'].get('rows', 0)}, sources={audit['theme_membership'].get('source_counts', {})}",
            f"- Multi-agent cache years: {audit['multi_agent_cache'].get('years', {})}",
            "",
            "Controls:",
        ]
    )
    for item in audit["leakage_controls"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Known limitations:")
    for item in audit["known_limitations"]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Multi-Theme Readiness",
            "",
            "| Theme | Corpus Rows | Corpus Range | Chart Rows | Chart Range | Membership Rows | Multi-Agent Ready |",
            "|---|---:|---|---:|---|---:|---|",
        ]
    )
    for row in status.get("multi_theme_readiness") or []:
        lines.append(
            "| {theme} | {corpus_rows} | {corpus_min}..{corpus_max} | {chart_rows} | {chart_min}..{chart_max} | {membership_rows} | {ready} |".format(
                theme=row.get("theme_key", ""),
                corpus_rows=int(row.get("corpus_rows") or 0),
                corpus_min=row.get("corpus_min_date", ""),
                corpus_max=row.get("corpus_max_date", ""),
                chart_rows=int(row.get("chart_rows") or 0),
                chart_min=row.get("chart_min_date", ""),
                chart_max=row.get("chart_max_date", ""),
                membership_rows=int(row.get("membership_rows") or 0),
                ready="yes" if row.get("ready_for_multi_agent") else "no",
            )
        )

    lines.extend(["", "## Next Work", ""])
    for item in status["next_work"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Artifacts", ""])
    for key, value in status["artifacts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                yield value


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _fmt_ymd(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize multi-agent validation status.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--theme", default="AI")
    parser.add_argument("--theme-key", default="ai")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--champion-summary", required=True)
    parser.add_argument("--deterministic-summary", required=True)
    parser.add_argument("--technical-short-summary", required=True)
    parser.add_argument("--technical-long-summary", required=True)
    parser.add_argument(
        "--extra-theme-keys",
        default="반도체,로봇,바이오,전력설비,조선,2차전지",
        help="Comma-separated theme keys to audit for next multi-theme validation.",
    )
    args = parser.parse_args()

    status = build_status_report(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        theme=args.theme,
        theme_key=args.theme_key,
        champion_summary=args.champion_summary,
        deterministic_summary=args.deterministic_summary,
        technical_short_summary=args.technical_short_summary,
        technical_long_summary=args.technical_long_summary,
        extra_theme_keys=[item.strip() for item in args.extra_theme_keys.split(",") if item.strip()],
    )
    print(json.dumps(status["artifacts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
