#!/usr/bin/env python3
from __future__ import annotations

"""Create a cleaner period RAG snapshot for backtest retrieval.

The cleaner is intentionally conservative:
- DART chunks are kept, except exact repeated text.
- News keeps a few chunks per same stock/date/title so long articles can survive.
- Forum removes exact repeated text, short link-only noise, and repeated chunks for
  the same stock/date/title.
"""

import argparse
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtesting.temporal_rag import save_jsonl


def _metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _source(row: Dict[str, Any]) -> str:
    meta = _metadata(row)
    return str(row.get("source_type") or meta.get("source_type") or "").strip().lower()


def _stock_code(row: Dict[str, Any]) -> str:
    meta = _metadata(row)
    return str(row.get("stock_code") or meta.get("stock_code") or "").strip()


def _published_ymd(row: Dict[str, Any]) -> str:
    meta = _metadata(row)
    raw = meta.get("published_at") or row.get("published_at") or meta.get("timestamp") or row.get("timestamp")
    text = str(raw or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    match = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}{int(month):02d}{int(day):02d}"


def _title(row: Dict[str, Any]) -> str:
    meta = _metadata(row)
    return str(row.get("title") or meta.get("title") or "").strip()


def _text(row: Dict[str, Any]) -> str:
    meta = _metadata(row)
    return str(row.get("text") or meta.get("content") or row.get("content") or "")


def _norm_text(value: str) -> str:
    text = html.unescape(str(value or "")).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_title(value: str) -> str:
    text = html.unescape(str(value or "")).lower()
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _quality_key(row: Dict[str, Any]) -> Tuple[int, int, int]:
    """Prefer complete, longer, earlier chunks when collapsing duplicates."""
    meta = _metadata(row)
    has_url = 1 if str(meta.get("url") or row.get("url") or "").strip() else 0
    text_len = len(_norm_text(_text(row)))
    try:
        chunk_index = int(meta.get("chunk_index", 999999))
    except (TypeError, ValueError):
        chunk_index = 999999
    return (has_url, min(text_len, 700), -chunk_index)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _keep_best(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if limit <= 0 or len(rows) <= limit:
        return rows
    return sorted(rows, key=_quality_key, reverse=True)[:limit]


def clean_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    forum_min_normalized_chars: int = 20,
    max_news_chunks_per_title: int = 3,
    max_forum_chunks_per_title: int = 1,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    input_rows = list(rows)
    dropped = Counter()

    # 1) Remove low-signal forum rows.
    stage1: List[Dict[str, Any]] = []
    for row in input_rows:
        source = _source(row)
        normalized = _norm_text(_text(row))
        if source == "forum" and len(normalized) < forum_min_normalized_chars:
            dropped["forum_short_text"] += 1
            continue
        stage1.append(row)

    # 2) Remove exact repeated normalized text within the same source/stock.
    exact_best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in stage1:
        source = _source(row)
        normalized = _norm_text(_text(row))
        key = (source, _stock_code(row), normalized)
        prev = exact_best.get(key)
        if prev is None:
            exact_best[key] = row
            continue
        dropped[f"{source or 'unknown'}_exact_text_duplicate"] += 1
        if _quality_key(row) > _quality_key(prev):
            exact_best[key] = row
    stage2 = list(exact_best.values())

    # 3) Cap repeated article/post chunks by source+stock+date+title.
    grouped: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    passthrough: List[Dict[str, Any]] = []
    for row in stage2:
        source = _source(row)
        title_key = _norm_title(_title(row))
        if source in {"news", "general_news", "forum"} and title_key:
            grouped[(source, _stock_code(row), _published_ymd(row), title_key)].append(row)
        else:
            passthrough.append(row)

    capped: List[Dict[str, Any]] = list(passthrough)
    for (source, _code, _date, _title_key), group in grouped.items():
        limit = max_forum_chunks_per_title if source == "forum" else max_news_chunks_per_title
        kept = _keep_best(group, limit)
        capped.extend(kept)
        dropped[f"{source}_same_title_chunk_cap"] += len(group) - len(kept)

    # 4) Stable output order by source/date/stock/title/chunk.
    def sort_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, int]:
        meta = _metadata(row)
        try:
            chunk = int(meta.get("chunk_index", 0))
        except (TypeError, ValueError):
            chunk = 0
        return (_source(row), _published_ymd(row), _stock_code(row), _norm_title(_title(row)), chunk)

    cleaned = sorted(capped, key=sort_key)
    source_counts = Counter(_source(row) for row in cleaned)
    input_source_counts = Counter(_source(row) for row in input_rows)

    report = {
        "input_count": len(input_rows),
        "output_count": len(cleaned),
        "dropped_count": len(input_rows) - len(cleaned),
        "input_source_counts": dict(sorted(input_source_counts.items())),
        "output_source_counts": dict(sorted(source_counts.items())),
        "drop_reasons": dict(sorted(dropped.items())),
        "rules": {
            "forum_min_normalized_chars": forum_min_normalized_chars,
            "max_news_chunks_per_title": max_news_chunks_per_title,
            "max_forum_chunks_per_title": max_forum_chunks_per_title,
            "dart_title_chunks_preserved": True,
        },
    }
    return cleaned, report


def write_snapshot(rows: List[Dict[str, Any]], output_dir: Path, report: Dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    save_jsonl(rows, output_dir / "combined.jsonl")

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_source(row)].append(row)
    for source, source_rows in grouped.items():
        if source:
            save_jsonl(source_rows, output_dir / f"{source}.jsonl")

    with (output_dir / "clean_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean a period RAG snapshot without modifying the original.")
    parser.add_argument("--input-dir", required=True, help="Directory containing combined.jsonl")
    parser.add_argument("--output-dir", required=True, help="Directory to write the cleaned snapshot")
    parser.add_argument("--forum-min-normalized-chars", type=int, default=20)
    parser.add_argument("--max-news-chunks-per-title", type=int, default=3)
    parser.add_argument("--max-forum-chunks-per-title", type=int, default=1)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    rows = _load_jsonl(input_dir / "combined.jsonl")
    cleaned, report = clean_rows(
        rows,
        forum_min_normalized_chars=args.forum_min_normalized_chars,
        max_news_chunks_per_title=args.max_news_chunks_per_title,
        max_forum_chunks_per_title=args.max_forum_chunks_per_title,
    )
    write_snapshot(cleaned, output_dir, report)

    print(f"[CLEAN RAG] input: {input_dir}")
    print(f"[CLEAN RAG] output: {output_dir}")
    print(f"[CLEAN RAG] rows: {report['input_count']} -> {report['output_count']} (-{report['dropped_count']})")
    print(f"[CLEAN RAG] sources: {report['output_source_counts']}")
    print(f"[CLEAN RAG] drop_reasons: {report['drop_reasons']}")


if __name__ == "__main__":
    main()
