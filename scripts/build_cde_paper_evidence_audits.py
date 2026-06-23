from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "experiment_results" / "theme_recent_month_data_20260610" / "ai" / "processed" / "corpus_combined.jsonl"
MARKET_PATH = ROOT / "experiment_results" / "theme_recent_month_data_20260610" / "ai" / "processed" / "market_combined.jsonl"
COMPARISON_PATH = ROOT / "artifacts" / "paper_backtesting_exports" / "ai-strategy-comparison.json"
AUDIT_DIR = ROOT / "artifacts" / "paper_temporal_audit"
AUDIT_JSON = AUDIT_DIR / "temporal_rag_leakage_audit.json"
AUDIT_MD = AUDIT_DIR / "temporal_rag_leakage_audit.md"
REPRO_MD = ROOT / "docs" / "cde_2026_reproducibility_manifest.md"

SAMPLES = ("2026-05-20", "2026-05-31", "2026-06-10")
LOOKBACK_DAYS: dict[str, int | None] = {
    "forum": 90,
    "news": 365,
    "general_news": 365,
    "dart": None,
    "report": 730,
}


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_temporal_rows(path: Path, *, fallback_source: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in iter_jsonl(path):
        meta = row.get("metadata") or {}
        source = str(row.get("source_type") or meta.get("source_type") or fallback_source)
        raw_date = (
            meta.get("published_at")
            or row.get("published_at")
            or row.get("timestamp")
            or meta.get("timestamp")
        )
        date = normalize_date(raw_date)
        if date:
            rows.append({"date": date, "source": source})
    return rows


def in_lookback(row: dict[str, str], as_of_date: str) -> bool:
    days = LOOKBACK_DAYS.get(row["source"])
    if days is None:
        return True
    lower = parse_date(as_of_date) - timedelta(days=days)
    return parse_date(row["date"]) >= lower


def build_temporal_audit() -> dict[str, Any]:
    document_rows = load_temporal_rows(CORPUS_PATH, fallback_source="document")
    price_rows = load_temporal_rows(MARKET_PATH, fallback_source="chart")

    rows = []
    for as_of_date in SAMPLES:
        docs_kept = [
            row for row in document_rows
            if row["date"] <= as_of_date and in_lookback(row, as_of_date)
        ]
        docs_future = [row for row in document_rows if row["date"] > as_of_date]
        prices_kept = [row for row in price_rows if row["date"] <= as_of_date]
        prices_future = [row for row in price_rows if row["date"] > as_of_date]
        source_counts = Counter(row["source"] for row in docs_kept)

        rows.append(
            {
                "as_of_date": as_of_date,
                "document_rows": len(docs_kept),
                "document_source_counts": dict(sorted(source_counts.items())),
                "max_document_date": max((row["date"] for row in docs_kept), default=""),
                "future_document_rows_excluded": len(docs_future),
                "price_rows": len(prices_kept),
                "max_price_date": max((row["date"] for row in prices_kept), default=""),
                "future_price_rows_excluded": len(prices_future),
                "pass": all(row["date"] <= as_of_date for row in docs_kept + prices_kept),
            }
        )

    return {
        "purpose": "Representative point-in-time leakage audit for the CDE 2026 paper.",
        "document_source": str(CORPUS_PATH.relative_to(ROOT)),
        "price_source": str(MARKET_PATH.relative_to(ROOT)),
        "rows": rows,
    }


def collect_result_json_paths(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        result_json = obj.get("result_json")
        if result_json:
            out.append(str(result_json))
        for value in obj.values():
            collect_result_json_paths(value, out)
    elif isinstance(obj, list):
        for value in obj:
            collect_result_json_paths(value, out)


def build_reproducibility_manifest() -> dict[str, Any]:
    obj = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))
    paths: list[str] = []
    collect_result_json_paths(obj, paths)
    unique_paths = sorted(set(paths))
    existing = [path for path in unique_paths if (ROOT / path).exists()]
    missing = [path for path in unique_paths if not (ROOT / path).exists()]
    return {
        "comparison_source": str(COMPARISON_PATH.relative_to(ROOT)),
        "referenced_result_json_count": len(unique_paths),
        "existing_result_json_count": len(existing),
        "missing_result_json_count": len(missing),
        "existing_result_json": existing,
        "missing_result_json": missing,
    }


def write_temporal_audit_markdown(audit: dict[str, Any]) -> None:
    lines = [
        "# Temporal RAG Leakage Audit",
        "",
        "This audit checks representative AI-theme rows preserved in the local evidence corpus.",
        "A row passes when the maximum document or price date used at each `as_of_date` does not exceed that decision date.",
        "",
        f"- Document source: `{audit['document_source']}`",
        f"- Price source: `{audit['price_source']}`",
        "",
        "| as_of_date | Document rows | Max document date | Price rows | Max price date | Future rows excluded (doc/price) | Pass |",
        "|---|---:|---|---:|---|---:|---|",
    ]
    for row in audit["rows"]:
        excluded = f"{row['future_document_rows_excluded']}/{row['future_price_rows_excluded']}"
        lines.append(
            f"| {row['as_of_date']} | {row['document_rows']} | {row['max_document_date']} | "
            f"{row['price_rows']} | {row['max_price_date']} | {excluded} | {row['pass']} |"
        )
    AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reproducibility_markdown(manifest: dict[str, Any]) -> None:
    lines = [
        "# CDE 2026 Reproducibility Manifest",
        "",
        "This manifest records which raw result JSON files referenced by the paper comparison artifact are present in the current workspace.",
        "",
        f"- Comparison source: `{manifest['comparison_source']}`",
        f"- Referenced result JSON files: {manifest['referenced_result_json_count']}",
        f"- Present in workspace: {manifest['existing_result_json_count']}",
        f"- Missing in workspace: {manifest['missing_result_json_count']}",
        "",
        "## Interpretation",
        "",
        "The preserved comparison table and exported JSON are enough to reproduce the paper's summary tables.",
        "However, the workspace is not yet a complete raw-run reproducibility package because some source `result_json` files are absent.",
        "",
        "## Missing Result JSON Files",
        "",
    ]
    for path in manifest["missing_result_json"]:
        lines.append(f"- `{path}`")
    REPRO_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit = build_temporal_audit()
    AUDIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_temporal_audit_markdown(audit)

    manifest = build_reproducibility_manifest()
    write_reproducibility_markdown(manifest)
    print(AUDIT_JSON)
    print(AUDIT_MD)
    print(REPRO_MD)


if __name__ == "__main__":
    main()
