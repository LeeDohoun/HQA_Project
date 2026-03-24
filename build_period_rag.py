from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

from src.rag.dedupe import make_record_id
from src.rag.source_registry import is_document_source
from src.rag.vector_store import SourceRAGBuilder


def load_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []

    rows: List[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def save_jsonl(rows: List[Dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def normalize_date(value: str) -> str:
    """
    다양한 날짜 포맷을 YYYYMMDD로 정규화.
    예:
    - 20251219
    - 2025-12-19
    - 2026.03.18 14:17
    """
    if not value:
        return ""

    v = value.strip()

    # 20251219
    if len(v) == 8 and v.isdigit():
        return v

    # 2025-12-19
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10].replace("-", "")

    # 2026.03.18 14:17 / 2026.03.18
    if "." in v:
        import re
        m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", v)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}{int(mo):02d}{int(d):02d}"

    return ""


def match_period(
    row: Dict,
    from_date: str,
    to_date: str,
) -> bool:
    published_at = row.get("metadata", {}).get("published_at") or row.get("published_at", "")
    norm = normalize_date(str(published_at))

    if not norm:
        return False

    if from_date and norm < from_date:
        return False
    if to_date and norm > to_date:
        return False

    return True


def match_theme(row: Dict, theme_key: str) -> bool:
    if not theme_key:
        return True
    return row.get("metadata", {}).get("theme_key", "") == theme_key


def match_source(row: Dict, source_type: str) -> bool:
    if not source_type:
        return True
    return row.get("metadata", {}).get("source_type", "") == source_type


def collect_corpus_rows(
    corpora_root: Path,
    theme_key: Optional[str] = "",
) -> List[Dict]:
    rows: List[Dict] = []

    if theme_key:
        target = corpora_root / theme_key / "combined.jsonl"
        return load_jsonl(target)

    for theme_dir in corpora_root.iterdir():
        if not theme_dir.is_dir():
            continue
        combined = theme_dir / "combined.jsonl"
        if combined.exists():
            rows.extend(load_jsonl(combined))

    return rows


def dedupe_rows(rows: List[Dict]) -> List[Dict]:
    seen = set()
    out = []

    for row in rows:
        key = make_record_id(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="기간 기준 RAG 재구성")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--theme-key", default="", help="예: 2차전지, 정유. 비우면 전체 테마")
    parser.add_argument("--from-date", required=True, help="YYYYMMDD")
    parser.add_argument("--to-date", required=True, help="YYYYMMDD")
    parser.add_argument("--source-type", default="")
    parser.add_argument("--output-name", default="period_rag")
    parser.add_argument("--build-vector", action="store_true")

    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    corpora_root = data_dir / "corpora"

    all_rows = collect_corpus_rows(
        corpora_root=corpora_root,
        theme_key=args.theme_key,
    )

    filtered = [
        row for row in all_rows
        if match_period(row, args.from_date, args.to_date)
        and match_theme(row, args.theme_key)
        and match_source(row, args.source_type)
    ]

    filtered = dedupe_rows(filtered)

    out_dir = data_dir / "period_rag" / args.output_name
    out_dir.mkdir(parents=True, exist_ok=True)

    combined_path = out_dir / "combined.jsonl"
    save_jsonl(filtered, combined_path)

    grouped: Dict[str, List[Dict]] = {}
    for row in filtered:
        source_type = str(row.get("metadata", {}).get("source_type", "")).strip().lower()
        if not is_document_source(source_type):
            continue
        grouped.setdefault(source_type, []).append(row)

    for source, rows in grouped.items():
        save_jsonl(rows, out_dir / f"{source}.jsonl")

    print(f"[PERIOD RAG] total rows: {len(filtered)}")
    print(f"[PERIOD RAG] output: {combined_path}")

    if args.build_vector:
        vector_dir = out_dir / "vector_stores"
        builder = SourceRAGBuilder()
        stats = builder.upsert_by_source(
            records=filtered,
            output_dir=str(vector_dir),
            mode="overwrite",
            theme_key="",  # 기간 RAG는 별도 출력 폴더에 새로 생성하므로 theme_key 불필요
        )
        print("[PERIOD RAG] vector stats:")
        for source, count in sorted(stats.items()):
            print(f"  - {source}: {count}")


if __name__ == "__main__":
    main()
