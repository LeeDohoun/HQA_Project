#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List


DEFAULT_THEMES = ["ai", "반도체", "바이오", "로봇", "전력설비", "조선", "2차전지", "화장품"]
RAW_SOURCES = ["theme_targets", "news", "dart", "forum", "chart"]


def normalize_ymd(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(20\d{2})[-/.]?(\d{2})[-/.]?(\d{2})", text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                yield {"__invalid_json__": True, "line_no": line_no, "error": str(exc)}
                continue
            if isinstance(value, dict):
                yield value


def pct(part: int | float, total: int | float) -> float:
    if not total:
        return 0.0
    return round(float(part) * 100.0 / float(total), 2)


def number_stats(values: List[int | float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "min": 0, "max": 0, "mean": 0, "median": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(mean(values), 2),
        "median": round(median(values), 2),
    }


def date_stats(rows: Iterable[Dict[str, Any]], date_keys: List[str]) -> Dict[str, Any]:
    dates: List[str] = []
    missing = 0
    years: Counter[str] = Counter()
    total = 0
    for row in rows:
        total += 1
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        ymd = ""
        for key in date_keys:
            ymd = normalize_ymd(row.get(key) or meta.get(key))
            if ymd:
                break
        if not ymd:
            missing += 1
            continue
        dates.append(ymd)
        years[ymd[:4]] += 1
    return {
        "rows": total,
        "dated_rows": len(dates),
        "missing_date_rows": missing,
        "min_date": min(dates) if dates else "",
        "max_date": max(dates) if dates else "",
        "years": dict(sorted(years.items())),
    }


def raw_document_stats(path: Path, source: str) -> Dict[str, Any]:
    rows = list(iter_jsonl(path))
    invalid_json = sum(1 for row in rows if row.get("__invalid_json__"))
    valid_rows = [row for row in rows if not row.get("__invalid_json__")]
    lengths: List[int] = []
    title_eq_content = 0
    placeholder = 0
    low_quality = 0
    dart_body_missing = 0
    dart_wrapper = 0
    forum_title_only = 0
    body_extracted = 0
    stocks = set()

    for row in valid_rows:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        title = str(row.get("title") or "").strip()
        content = str(row.get("content") or row.get("text") or "").strip()
        code = str(row.get("stock_code") or meta.get("stock_code") or "").strip()
        if code:
            stocks.add(code)
        lengths.append(len(content))
        if title and title == content:
            title_eq_content += 1
        if content in {"네이버뉴스", "Naver News"} or title in {"네이버뉴스", "Naver News"}:
            placeholder += 1
        if len(content) < 30:
            low_quality += 1
        if source == "dart":
            if not bool(meta.get("has_body", row.get("has_body", False))):
                dart_body_missing += 1
            if bool(meta.get("wrapper_text_detected", row.get("wrapper_text_detected", False))):
                dart_wrapper += 1
        if source == "forum":
            if title and title == content:
                forum_title_only += 1
            if bool(meta.get("body_extracted", row.get("body_extracted", False))):
                body_extracted += 1

    date_keys = ["published_at", "timestamp", "date", "collected_at"]
    stats = date_stats(valid_rows, date_keys)
    stats.update(
        {
            "exists": path.exists(),
            "invalid_json_rows": invalid_json,
            "unique_stock_codes": len(stocks),
            "content_length": number_stats(lengths),
            "title_eq_content_rows": title_eq_content,
            "title_eq_content_pct": pct(title_eq_content, len(valid_rows)),
            "placeholder_rows": placeholder,
            "placeholder_pct": pct(placeholder, len(valid_rows)),
            "low_quality_rows": low_quality,
            "low_quality_pct": pct(low_quality, len(valid_rows)),
        }
    )
    if source == "dart":
        stats.update(
            {
                "dart_body_missing_rows": dart_body_missing,
                "dart_body_missing_pct": pct(dart_body_missing, len(valid_rows)),
                "dart_wrapper_rows": dart_wrapper,
                "dart_wrapper_pct": pct(dart_wrapper, len(valid_rows)),
            }
        )
    if source == "forum":
        stats.update(
            {
                "forum_title_only_rows": forum_title_only,
                "forum_title_only_pct": pct(forum_title_only, len(valid_rows)),
                "forum_body_extracted_rows": body_extracted,
                "forum_body_extracted_pct": pct(body_extracted, len(valid_rows)),
            }
        )
    return stats


def target_stats(path: Path) -> Dict[str, Any]:
    rows = [row for row in iter_jsonl(path) if not row.get("__invalid_json__")]
    codes = {str(row.get("stock_code") or "").strip() for row in rows if row.get("stock_code")}
    with_corp_code = sum(1 for row in rows if str(row.get("corp_code") or "").strip())
    return {
        "exists": path.exists(),
        "rows": len(rows),
        "unique_stock_codes": len(codes),
        "with_corp_code_rows": with_corp_code,
        "with_corp_code_pct": pct(with_corp_code, len(rows)),
    }


def canonical_stats(path: Path) -> Dict[str, Any]:
    rows = [row for row in iter_jsonl(path) if not row.get("__invalid_json__")]
    source_counts: Counter[str] = Counter()
    quality_scores: List[float] = []
    credibility_scores: List[float] = []
    freshness_scores: List[float] = []
    stocks = set()
    dated_rows: List[Dict[str, Any]] = []

    for row in rows:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source = str(row.get("source_type") or meta.get("source_type") or "unknown")
        source_counts[source] += 1
        code = str(row.get("stock_code") or meta.get("stock_code") or "").strip()
        if code:
            stocks.add(code)
        for key, target in [
            ("content_quality_score", quality_scores),
            ("credibility_score", credibility_scores),
            ("freshness_score", freshness_scores),
        ]:
            value = meta.get(key)
            if isinstance(value, (int, float)):
                target.append(float(value))
        dated_rows.append(row)

    stats = date_stats(dated_rows, ["published_at", "timestamp", "date", "collected_at"])
    stats.update(
        {
            "exists": path.exists(),
            "unique_stock_codes": len(stocks),
            "source_counts": dict(sorted(source_counts.items())),
            "content_quality_score": number_stats(quality_scores),
            "credibility_score": number_stats(credibility_scores),
            "freshness_score": number_stats(freshness_scores),
        }
    )
    return stats


def membership_stats(path: Path) -> Dict[str, Any]:
    rows = [row for row in iter_jsonl(path) if not row.get("__invalid_json__")]
    sources: Counter[str] = Counter()
    first_seen: List[str] = []
    confidence: List[float] = []
    stocks = set()
    for row in rows:
        sources[str(row.get("source") or "unknown")] += 1
        ymd = normalize_ymd(row.get("first_seen_at"))
        if ymd:
            first_seen.append(ymd)
        code = str(row.get("stock_code") or "").strip()
        if code:
            stocks.add(code)
        value = row.get("membership_confidence")
        if isinstance(value, (int, float)):
            confidence.append(float(value))
    return {
        "exists": path.exists(),
        "rows": len(rows),
        "unique_stock_codes": len(stocks),
        "source_counts": dict(sorted(sources.items())),
        "min_first_seen": min(first_seen) if first_seen else "",
        "max_first_seen": max(first_seen) if first_seen else "",
        "confidence": number_stats(confidence),
    }


def audit_theme(data_dir: Path, theme_key: str) -> Dict[str, Any]:
    raw = {
        "theme_targets": target_stats(data_dir / "raw" / "theme_targets" / f"{theme_key}.jsonl"),
        "news": raw_document_stats(data_dir / "raw" / "news" / f"{theme_key}.jsonl", "news"),
        "dart": raw_document_stats(data_dir / "raw" / "dart" / f"{theme_key}.jsonl", "dart"),
        "forum": raw_document_stats(data_dir / "raw" / "forum" / f"{theme_key}.jsonl", "forum"),
        "chart": raw_document_stats(data_dir / "raw" / "chart" / f"{theme_key}.jsonl", "chart"),
    }
    corpora = {
        "combined_rows": sum(1 for _ in iter_jsonl(data_dir / "corpora" / theme_key / "combined.jsonl")),
    }
    canonical = canonical_stats(data_dir / "canonical_index" / theme_key / "corpus.jsonl")
    market = raw_document_stats(data_dir / "market_data" / theme_key / "combined.jsonl", "chart")
    membership = membership_stats(data_dir / "raw" / "theme_membership" / f"{theme_key}.jsonl")
    readiness = readiness_flags(raw, canonical, market, membership)
    issues = detect_issues(raw, canonical, market, membership)
    return {
        "theme_key": theme_key,
        "raw": raw,
        "corpora": corpora,
        "canonical": canonical,
        "market": market,
        "theme_membership": membership,
        "readiness": readiness,
        "issues": issues,
    }


def readiness_flags(raw: Dict[str, Any], canonical: Dict[str, Any], market: Dict[str, Any], membership: Dict[str, Any]) -> Dict[str, bool]:
    chart_min = normalize_ymd(raw["chart"].get("min_date"))
    chart_max = normalize_ymd(raw["chart"].get("max_date"))
    canonical_min = normalize_ymd(canonical.get("min_date"))
    canonical_max = normalize_ymd(canonical.get("max_date"))
    membership_min = normalize_ymd(membership.get("min_first_seen"))
    covers_2023_price = bool(chart_min and chart_min <= "2023-01-10" and chart_max >= "2023-12-20")
    covers_2024_price = bool(chart_min and chart_min <= "2024-01-10" and chart_max >= "2024-12-20")
    covers_2023_corpus = bool(canonical_min and canonical_min <= "2023-01-10" and canonical_max >= "2023-12-20")
    covers_2024_corpus = bool(canonical_min and canonical_min <= "2024-01-10" and canonical_max >= "2024-12-20")
    covers_2023_membership = bool(membership_min and membership_min <= "2023-01-10")
    covers_2024_membership = bool(membership_min and membership_min <= "2024-01-10")
    return {
        "has_targets": raw["theme_targets"].get("rows", 0) > 0,
        "has_price_data": raw["chart"].get("rows", 0) > 0 or market.get("rows", 0) > 0,
        "has_corpus": canonical.get("rows", 0) > 0,
        "has_point_in_time_membership": membership.get("rows", 0) > 0,
        "covers_2023_price": covers_2023_price,
        "covers_2024_price": covers_2024_price,
        "covers_2023_corpus": covers_2023_corpus,
        "covers_2024_corpus": covers_2024_corpus,
        "covers_2023_membership": covers_2023_membership,
        "covers_2024_membership": covers_2024_membership,
        "ready_for_2023_full_backtest": covers_2023_price and covers_2023_corpus and covers_2023_membership,
        "ready_for_2024_full_backtest": covers_2024_price and covers_2024_corpus and covers_2024_membership,
    }


def detect_issues(raw: Dict[str, Any], canonical: Dict[str, Any], market: Dict[str, Any], membership: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if raw["theme_targets"].get("rows", 0) == 0:
        issues.append("theme_targets missing")
    if raw["chart"].get("rows", 0) == 0 and market.get("rows", 0) == 0:
        issues.append("price/chart data missing")
    if canonical.get("rows", 0) == 0:
        issues.append("canonical corpus missing")
    if membership.get("rows", 0) == 0:
        issues.append("point-in-time membership missing")
    if raw["news"].get("placeholder_pct", 0) >= 20:
        issues.append(f"news placeholder ratio high: {raw['news']['placeholder_pct']}%")
    if raw["dart"].get("dart_body_missing_pct", 0) >= 50:
        issues.append(f"DART body missing ratio high: {raw['dart']['dart_body_missing_pct']}%")
    if raw["forum"].get("forum_title_only_pct", 0) >= 35:
        issues.append(f"forum title-only ratio high: {raw['forum']['forum_title_only_pct']}%")
    readiness = readiness_flags(raw, canonical, market, membership)
    if not readiness["covers_2023_price"]:
        issues.append("2023 full-year price coverage not ready")
    if not readiness["covers_2024_price"]:
        issues.append("2024 full-year price coverage not ready")
    if not readiness["covers_2023_corpus"]:
        issues.append("2023 full-year corpus coverage not ready")
    if not readiness["covers_2024_corpus"]:
        issues.append("2024 full-year corpus coverage not ready")
    if not readiness["covers_2023_membership"]:
        issues.append("2023 point-in-time membership starts too late")
    if not readiness["covers_2024_membership"]:
        issues.append("2024 point-in-time membership starts too late")
    return issues


def build_report(data_dir: Path, theme_keys: List[str]) -> Dict[str, Any]:
    themes = [audit_theme(data_dir, theme_key) for theme_key in theme_keys]
    aggregate = {
        "theme_count": len(themes),
        "total_targets": sum(t["raw"]["theme_targets"].get("rows", 0) for t in themes),
        "total_canonical_rows": sum(t["canonical"].get("rows", 0) for t in themes),
        "total_market_rows": sum(t["market"].get("rows", 0) for t in themes),
        "themes_with_membership": sum(1 for t in themes if t["theme_membership"].get("rows", 0) > 0),
        "themes_covering_2023_price": sum(1 for t in themes if t["readiness"]["covers_2023_price"]),
        "themes_covering_2024_price": sum(1 for t in themes if t["readiness"]["covers_2024_price"]),
        "themes_ready_for_2023_full_backtest": sum(1 for t in themes if t["readiness"]["ready_for_2023_full_backtest"]),
        "themes_ready_for_2024_full_backtest": sum(1 for t in themes if t["readiness"]["ready_for_2024_full_backtest"]),
    }
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_dir": str(data_dir),
        "aggregate": aggregate,
        "themes": themes,
        "notes": [
            "theme_membership rows are local corpus inferred unless an official historical source is supplied.",
            "covers_2023/2024 flags require near full-year price/corpus ranges and are conservative.",
        ],
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Theme Data Audit",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Data dir: `{report['data_dir']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in report["aggregate"].items():
        lines.append(f"| {key} | {value} |")

    lines.extend(
        [
            "",
            "## Theme Readiness",
            "",
            "| Theme | Targets | News | DART | Forum | Chart | Canonical | Market | Membership | 2023 Ready | 2024 Ready | Issues |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for theme in report["themes"]:
        raw = theme["raw"]
        ready = theme["readiness"]
        lines.append(
            "| {theme} | {targets} | {news} | {dart} | {forum} | {chart} | {canonical} | {market} | {membership} | {p2023} | {p2024} | {issues} |".format(
                theme=theme["theme_key"],
                targets=raw["theme_targets"].get("rows", 0),
                news=raw["news"].get("rows", 0),
                dart=raw["dart"].get("rows", 0),
                forum=raw["forum"].get("rows", 0),
                chart=raw["chart"].get("rows", 0),
                canonical=theme["canonical"].get("rows", 0),
                market=theme["market"].get("rows", 0),
                membership=theme["theme_membership"].get("rows", 0),
                p2023="yes" if ready["ready_for_2023_full_backtest"] else "no",
                p2024="yes" if ready["ready_for_2024_full_backtest"] else "no",
                issues="<br>".join(theme["issues"]) if theme["issues"] else "-",
            )
        )

    lines.extend(
        [
            "",
            "## Quality Signals",
            "",
            "| Theme | News Placeholder | DART Body Missing | Forum Title Only | Canonical Quality Mean | Membership First Seen |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for theme in report["themes"]:
        raw = theme["raw"]
        canonical_quality = theme["canonical"].get("content_quality_score", {}).get("mean", 0)
        membership = theme["theme_membership"]
        first_seen = f"{membership.get('min_first_seen', '')}..{membership.get('max_first_seen', '')}"
        lines.append(
            "| {theme} | {news:.2f}% | {dart:.2f}% | {forum:.2f}% | {quality:.2f} | {first_seen} |".format(
                theme=theme["theme_key"],
                news=float(raw["news"].get("placeholder_pct", 0)),
                dart=float(raw["dart"].get("dart_body_missing_pct", 0)),
                forum=float(raw["forum"].get("forum_title_only_pct", 0)),
                quality=float(canonical_quality or 0),
                first_seen=first_seen,
            )
        )

    lines.extend(["", "## Notes", ""])
    for note in report["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit collected theme data for backtesting readiness.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--themes", default=",".join(DEFAULT_THEMES))
    parser.add_argument("--output-json", default="data/reports/theme_data_audit.json")
    parser.add_argument("--output-md", default="data/reports/theme_data_audit.md")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    theme_keys = [item.strip() for item in args.themes.split(",") if item.strip()]
    report = build_report(data_dir, theme_keys)

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"[AUDIT] wrote {output_json}")
    print(f"[AUDIT] wrote {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
