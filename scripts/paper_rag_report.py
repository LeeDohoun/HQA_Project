"""Analyst retrieval report from the PAPER audit log (`HQA_ANALYST_RAG`).

How often the analyst's input carried retrieved documents, how often it cited them, what they
added to its input, and whether they moved its scores on unchanged evidence. The audit database
is opened read-only, so the report can run while PAPER writes to it.

Example: python -m scripts.paper_rag_report --audit .local/paper_audit.sqlite3 --since 2026-11-09
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from src.config.settings import get_data_dir
from src.tracing.paper_audit import summarize_analyst_retrieval


def load_events(path: Path) -> list[dict]:
    """The analyst's requests, responses and failures, and each cycle's errors, oldest first."""
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT id, recorded_at, kind, payload FROM paper_events"
            " WHERE kind IN ('llm_request', 'llm_response', 'llm_failure') AND json_extract(payload, '$.role') = 'analyst'"
            " UNION ALL SELECT id, recorded_at, kind, json_object('errors', json(json_extract(payload, '$.errors')))"
            " FROM paper_events WHERE kind = 'analysis' ORDER BY id").fetchall()
    finally:
        db.close()
    return [{"id": row[0], "recorded_at": row[1], "kind": row[2], "payload": json.loads(row[3])} for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit", help="audit database (default: HQA_PAPER_AUDIT_PATH or <data dir>/paper_audit.sqlite3)")
    parser.add_argument("--since", help="count requests recorded from this UTC time on (ISO, e.g. 2026-11-09)")
    args = parser.parse_args(argv)
    path = Path(args.audit or os.getenv("HQA_PAPER_AUDIT_PATH") or get_data_dir() / "paper_audit.sqlite3")
    if not path.exists():
        raise SystemExit(f"audit database not found: {path}")
    print(json.dumps(summarize_analyst_retrieval(load_events(path), args.since), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
