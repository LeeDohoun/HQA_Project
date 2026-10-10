#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


RAW_DOCUMENT_SOURCES = ("news", "dart", "forum")
RAW_MARKET_SOURCES = ("chart",)


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def row_hash(*parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] in "-./":
        return text[:10].replace(".", "-").replace("/", "-")
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def date_key(value: Any) -> str:
    return normalize_date(value).replace("-", "")[:8]


def _theme_list(row: Dict[str, Any], fallback_theme: str) -> List[str]:
    raw = row.get("source_theme_keys") or row.get("theme_keys") or []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = []
    theme = str(row.get("source_theme_key") or row.get("theme_key") or fallback_theme).strip()
    if theme:
        values.append(theme)
    return sorted({item for item in values if item})


def with_theme_metadata(row: Dict[str, Any], *, theme: str, combined_key: str) -> Dict[str, Any]:
    out = dict(row)
    source_themes = _theme_list(out, theme)
    out["source_theme_key"] = theme
    out["source_theme_keys"] = source_themes
    out["theme_key"] = combined_key
    meta = out.get("metadata")
    if isinstance(meta, dict):
        meta = dict(meta)
        meta["source_theme_key"] = theme
        meta["source_theme_keys"] = source_themes
        meta["theme_key"] = combined_key
        out["metadata"] = meta
    return out


def merge_theme_lists(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    themes = set(_theme_list(existing, "")) | set(_theme_list(incoming, ""))
    merged = sorted(theme for theme in themes if theme)
    existing["source_theme_keys"] = merged
    existing["theme_keys"] = merged
    meta = existing.get("metadata")
    if isinstance(meta, dict):
        meta = dict(meta)
        meta["source_theme_keys"] = merged
        existing["metadata"] = meta


def target_key(row: Dict[str, Any]) -> str:
    return str(row.get("stock_code") or "").strip()


def raw_document_key(source: str, row: Dict[str, Any]) -> str:
    code = str(row.get("stock_code") or "").strip()
    if source == "dart":
        receipt = str(row.get("rcept_no") or row.get("receipt_no") or "").strip()
        if receipt:
            return f"dart:{code}:{receipt}"
    url = str(row.get("url") or "").strip()
    if url:
        return f"{source}:{code}:{url}"
    return f"{source}:{code}:{row_hash(row.get('title'), row.get('published_at'), row.get('content'))}"


def market_key(row: Dict[str, Any]) -> str:
    return f"{row.get('stock_code')}:{date_key(row.get('timestamp') or row.get('date'))}"


def canonical_key(row: Dict[str, Any]) -> str:
    meta = row.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    doc_id = meta.get("doc_id") or row.get("doc_id")
    chunk = meta.get("chunk_index") if "chunk_index" in meta else row.get("chunk_index")
    if doc_id is not None and chunk is not None:
        return f"{doc_id}:{chunk}"
    return row_hash(
        meta.get("source_type"),
        meta.get("stock_code"),
        meta.get("published_at"),
        meta.get("title"),
        row.get("text"),
    )


def combine_targets(data_dir: Path, themes: List[str], combined_key: str) -> Tuple[int, int]:
    by_code: Dict[str, Dict[str, Any]] = {}
    duplicate_count = 0
    for theme in themes:
        path = data_dir / "raw" / "theme_targets" / f"{theme}.jsonl"
        for row in iter_jsonl(path):
            code = target_key(row)
            if not code:
                continue
            row = with_theme_metadata(row, theme=theme, combined_key=combined_key)
            if code in by_code:
                duplicate_count += 1
                merge_theme_lists(by_code[code], row)
                if not by_code[code].get("corp_code") and row.get("corp_code"):
                    by_code[code]["corp_code"] = row.get("corp_code")
                continue
            by_code[code] = row
    rows = sorted(by_code.values(), key=lambda item: str(item.get("stock_code") or ""))
    count = write_jsonl(data_dir / "raw" / "theme_targets" / f"{combined_key}.jsonl", rows)
    return count, duplicate_count


def combine_raw_sources(data_dir: Path, themes: List[str], combined_key: str) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    for source in (*RAW_DOCUMENT_SOURCES, *RAW_MARKET_SOURCES):
        by_key: Dict[str, Dict[str, Any]] = {}
        input_count = 0
        for theme in themes:
            path = data_dir / "raw" / source / f"{theme}.jsonl"
            for row in iter_jsonl(path):
                input_count += 1
                row = with_theme_metadata(row, theme=theme, combined_key=combined_key)
                key = market_key(row) if source in RAW_MARKET_SOURCES else raw_document_key(source, row)
                if key in by_key:
                    merge_theme_lists(by_key[key], row)
                    continue
                by_key[key] = row
        rows = sorted(
            by_key.values(),
            key=lambda item: (
                str(item.get("stock_code") or ""),
                str(item.get("timestamp") or item.get("published_at") or ""),
                str(item.get("title") or ""),
            ),
        )
        output_count = write_jsonl(data_dir / "raw" / source / f"{combined_key}.jsonl", rows)
        stats[source] = {"input_rows": input_count, "output_rows": output_count, "deduped_rows": input_count - output_count}
    return stats


def combine_canonical(data_dir: Path, themes: List[str], combined_key: str) -> Dict[str, int]:
    by_key: Dict[str, Dict[str, Any]] = {}
    input_count = 0
    for theme in themes:
        path = data_dir / "canonical_index" / theme / "corpus.jsonl"
        for row in iter_jsonl(path):
            input_count += 1
            row = with_theme_metadata(row, theme=theme, combined_key=combined_key)
            key = canonical_key(row)
            if key in by_key:
                merge_theme_lists(by_key[key], row)
                continue
            by_key[key] = row
    rows = sorted(
        by_key.values(),
        key=lambda item: (
            str((item.get("metadata") or {}).get("stock_code") or ""),
            str((item.get("metadata") or {}).get("published_at") or ""),
            str((item.get("metadata") or {}).get("source_type") or ""),
        ),
    )
    index_dir = data_dir / "canonical_index" / combined_key
    output_count = write_jsonl(index_dir / "corpus.jsonl", rows)
    return {"input_rows": input_count, "output_rows": output_count, "deduped_rows": input_count - output_count}


def combine_market(data_dir: Path, themes: List[str], combined_key: str) -> Dict[str, int]:
    by_key: Dict[str, Dict[str, Any]] = {}
    input_count = 0
    for theme in themes:
        path = data_dir / "market_data" / theme / "chart.jsonl"
        for row in iter_jsonl(path):
            input_count += 1
            row = with_theme_metadata(row, theme=theme, combined_key=combined_key)
            key = market_key(row)
            if key in by_key:
                merge_theme_lists(by_key[key], row)
                continue
            by_key[key] = row
    rows = sorted(by_key.values(), key=lambda item: (str(item.get("stock_code") or ""), str(item.get("timestamp") or "")))
    market_dir = data_dir / "market_data" / combined_key
    chart_count = write_jsonl(market_dir / "chart.jsonl", rows)
    combined_count = write_jsonl(market_dir / "combined.jsonl", rows)
    return {
        "input_rows": input_count,
        "chart_rows": chart_count,
        "combined_rows": combined_count,
        "deduped_rows": input_count - chart_count,
    }


def combine_membership(data_dir: Path, themes: List[str], combined_key: str) -> Dict[str, int]:
    grouped: Dict[str, Dict[str, Any]] = {}
    input_count = 0
    for theme in themes:
        path = data_dir / "raw" / "theme_membership" / f"{theme}.jsonl"
        for row in iter_jsonl(path):
            input_count += 1
            code = str(row.get("stock_code") or "").strip()
            if not code:
                continue
            current = grouped.setdefault(
                code,
                {
                    "theme_key": combined_key,
                    "stock_name": row.get("stock_name") or "",
                    "stock_code": code,
                    "first_seen_at": row.get("first_seen_at") or "",
                    "last_seen_at": row.get("last_seen_at") or "",
                    "last_observed_at": row.get("last_observed_at") or "",
                    "source": "combined_local_corpus_inferred",
                    "membership_confidence": float(row.get("membership_confidence") or 0.0),
                    "evidence_count": 0,
                    "evidence_source_counts": {},
                    "source_theme_keys": [],
                    "notes": "Combined theme membership inferred from component theme memberships; not official historical theme membership.",
                },
            )
            first = normalize_date(row.get("first_seen_at"))
            if first and (not current["first_seen_at"] or first < current["first_seen_at"]):
                current["first_seen_at"] = first
            observed = normalize_date(row.get("last_observed_at"))
            if observed and (not current["last_observed_at"] or observed > current["last_observed_at"]):
                current["last_observed_at"] = observed
            last_seen = normalize_date(row.get("last_seen_at"))
            if last_seen and (not current["last_seen_at"] or last_seen > current["last_seen_at"]):
                current["last_seen_at"] = last_seen
            current["membership_confidence"] = max(
                float(current.get("membership_confidence") or 0.0),
                float(row.get("membership_confidence") or 0.0),
            )
            current["evidence_count"] += int(row.get("evidence_count") or 0)
            counts = row.get("evidence_source_counts") or {}
            if isinstance(counts, dict):
                for source, count in counts.items():
                    current["evidence_source_counts"][source] = current["evidence_source_counts"].get(source, 0) + int(count or 0)
            themes_seen = set(current["source_theme_keys"])
            themes_seen.add(theme)
            current["source_theme_keys"] = sorted(themes_seen)

    rows = sorted(grouped.values(), key=lambda item: str(item.get("stock_code") or ""))
    output_count = write_jsonl(data_dir / "raw" / "theme_membership" / f"{combined_key}.jsonl", rows)
    return {"input_rows": input_count, "output_rows": output_count, "deduped_rows": input_count - output_count}


def build_combined(data_dir: Path, themes: List[str], combined_key: str, combined_name: str) -> Dict[str, Any]:
    target_count, target_duplicates = combine_targets(data_dir, themes, combined_key)
    raw_stats = combine_raw_sources(data_dir, themes, combined_key)
    canonical_stats = combine_canonical(data_dir, themes, combined_key)
    market_stats = combine_market(data_dir, themes, combined_key)
    membership_stats = combine_membership(data_dir, themes, combined_key)
    report = {
        "theme_name": combined_name,
        "theme_key": combined_key,
        "source_themes": themes,
        "target_count": target_count,
        "target_duplicate_rows": target_duplicates,
        "raw_sources": raw_stats,
        "canonical": canonical_stats,
        "market": market_stats,
        "membership": membership_stats,
    }
    report_dir = data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    with (report_dir / f"{combined_key}_universe_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    write_markdown_report(report_dir / f"{combined_key}_universe_report.md", report)
    return report


def write_markdown_report(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        f"# Combined Theme Universe: {report['theme_name']}",
        "",
        f"- theme_key: `{report['theme_key']}`",
        f"- source_themes: {', '.join(report['source_themes'])}",
        f"- unique_targets: {report['target_count']}",
        f"- target_duplicate_rows: {report['target_duplicate_rows']}",
        "",
        "## Rows",
        "",
        "| Area | Input | Output | Deduped |",
        "|---|---:|---:|---:|",
    ]
    for source, stats in report["raw_sources"].items():
        lines.append(
            f"| raw/{source} | {stats['input_rows']} | {stats['output_rows']} | {stats['deduped_rows']} |"
        )
    canonical = report["canonical"]
    lines.append(f"| canonical/corpus | {canonical['input_rows']} | {canonical['output_rows']} | {canonical['deduped_rows']} |")
    market = report["market"]
    lines.append(f"| market/chart | {market['input_rows']} | {market['chart_rows']} | {market['deduped_rows']} |")
    membership = report["membership"]
    lines.append(f"| membership | {membership['input_rows']} | {membership['output_rows']} | {membership['deduped_rows']} |")
    lines.extend(
        [
            "",
            "## Backtest Usage",
            "",
            f"- Use `--theme-key {report['theme_key']}` with existing leader backtest scripts.",
            "- This is a synthetic point-in-time universe built from local theme data, not an official historical theme index.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a synthetic combined theme universe for backtesting.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--theme-key", required=True, help="Combined output theme key, e.g. all_themes")
    parser.add_argument("--theme-name", default="", help="Human-readable combined theme name")
    parser.add_argument("--themes", required=True, help="Comma-separated source theme keys")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    themes = [item.strip() for item in args.themes.split(",") if item.strip()]
    if not themes:
        raise SystemExit("--themes must not be empty")
    report = build_combined(
        data_dir=Path(args.data_dir),
        themes=themes,
        combined_key=args.theme_key,
        combined_name=args.theme_name or args.theme_key,
    )
    print(f"[COMBINED] theme_key={report['theme_key']} targets={report['target_count']}")
    print(f"[COMBINED] report=data/reports/{report['theme_key']}_universe_report.md")


if __name__ == "__main__":
    main()
