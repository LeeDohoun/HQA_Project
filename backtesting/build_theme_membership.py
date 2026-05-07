#!/usr/bin/env python3
from __future__ import annotations

"""Build point-in-time theme membership evidence from local theme data.

This does not create official historical Naver/KRX membership.  It infers a
conservative first-seen date from documents already collected for the theme, so
backtests can avoid using stocks before they appear in local theme evidence.
"""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.temporal_rag import normalize_ymd
from src.config.settings import get_data_dir
from src.ingestion.theme_membership import ThemeMembership, ThemeMembershipStore
from src.ingestion.theme_targets import ThemeTargetStore


def build_inferred_membership(
    *,
    data_dir: str | Path,
    theme_key: str,
    theme_name: str = "",
    min_evidence_count: int = 1,
) -> List[ThemeMembership]:
    data_root = Path(data_dir)
    targets = ThemeTargetStore(data_dir=str(data_root)).load_targets(theme_key)
    target_by_code = {target.stock_code: target for target in targets}
    doc_evidence = _collect_document_evidence(data_root, theme_key)
    chart_ranges = _collect_chart_ranges(data_root, theme_key)

    rows: List[ThemeMembership] = []
    for code, target in sorted(target_by_code.items()):
        evidence = doc_evidence.get(code, {})
        dates = evidence.get("dates", [])
        source_counts: Counter[str] = evidence.get("source_counts", Counter())
        first_doc = min(dates) if dates else ""
        last_doc = max(dates) if dates else ""
        chart_range = chart_ranges.get(code, {})
        first_chart = chart_range.get("first", "")
        last_chart = chart_range.get("last", "")

        evidence_count = len(dates)
        if evidence_count < min_evidence_count and not first_chart:
            continue

        first_seen = first_doc or first_chart
        last_seen = max([item for item in [last_doc, last_chart] if item], default="")
        rows.append(
            ThemeMembership(
                theme_key=theme_key,
                stock_name=target.stock_name,
                stock_code=code,
                first_seen_at=_fmt_ymd(first_seen),
                last_seen_at="",
                last_observed_at=_fmt_ymd(last_seen),
                source="local_corpus_inferred" if first_doc else "local_chart_fallback",
                membership_confidence=_confidence(evidence_count, source_counts),
                evidence_count=evidence_count,
                evidence_source_counts=dict(source_counts),
                notes=(
                    "Inferred from local theme corpus first-seen evidence; "
                    "not official historical theme membership."
                ),
            )
        )

    store = ThemeMembershipStore(data_dir=data_root)
    store.save_memberships(
        theme_key,
        rows,
        theme_name=theme_name or theme_key,
        method="local_corpus_inferred",
    )
    return rows


def _collect_document_evidence(data_root: Path, theme_key: str) -> Dict[str, Dict[str, Any]]:
    path = data_root / "canonical_index" / theme_key / "corpus.jsonl"
    output: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"dates": [], "source_counts": Counter()})
    for row in _iter_jsonl(path):
        meta = row.get("metadata") or {}
        code = str(meta.get("stock_code") or row.get("stock_code") or "").strip()
        source = str(meta.get("source_type") or row.get("source_type") or "").strip().lower()
        published = normalize_ymd(meta.get("published_at") or row.get("published_at"))
        if not code or not published:
            continue
        output[code]["dates"].append(published)
        if source:
            output[code]["source_counts"][source] += 1
    return output


def _collect_chart_ranges(data_root: Path, theme_key: str) -> Dict[str, Dict[str, str]]:
    path = data_root / "market_data" / theme_key / "chart.jsonl"
    ranges: Dict[str, Dict[str, str]] = {}
    for row in _iter_jsonl(path):
        code = str(row.get("stock_code") or "").strip()
        ymd = normalize_ymd(row.get("timestamp"))
        if not code or not ymd:
            continue
        current = ranges.setdefault(code, {"first": ymd, "last": ymd})
        current["first"] = min(current["first"], ymd)
        current["last"] = max(current["last"], ymd)
    return ranges


def _confidence(evidence_count: int, source_counts: Counter[str]) -> float:
    source_bonus = 0.0
    if source_counts.get("news"):
        source_bonus += 0.08
    if source_counts.get("dart"):
        source_bonus += 0.08
    if source_counts.get("forum"):
        source_bonus += 0.03
    raw = 0.45 + min(0.35, math.log1p(max(0, evidence_count)) / 12.0) + source_bonus
    return round(min(0.95, raw), 3)


def _fmt_ymd(ymd: str) -> str:
    if not ymd:
        return ""
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build point-in-time theme membership evidence.")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--theme-key", default="ai")
    parser.add_argument("--theme-name", default="AI")
    parser.add_argument("--min-evidence-count", type=int, default=1)
    args = parser.parse_args()

    rows = build_inferred_membership(
        data_dir=args.data_dir,
        theme_key=args.theme_key,
        theme_name=args.theme_name,
        min_evidence_count=args.min_evidence_count,
    )
    out_path = ThemeMembershipStore(data_dir=args.data_dir).get_path(args.theme_key)
    print(f"[MEMBERSHIP] wrote {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
