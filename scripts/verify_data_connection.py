#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_data_dir, load_project_env
from src.rag.canonical_retriever import CanonicalRetriever
from src.retrieval.services import RetrievalService


def _count_jsonl(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob("*.jsonl"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify HQA data connectivity")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    parser.add_argument("--query", default="삼성전자 실적 전망")
    args = parser.parse_args()

    load_project_env()
    data_dir = Path(args.data_dir)
    retriever = CanonicalRetriever(data_dir=str(data_dir))
    state = retriever.describe_data_state()

    report = {
        "data_dir": str(data_dir),
        "state": state,
        "counts": {
            "raw_jsonl_files": _count_jsonl(data_dir / "raw"),
            "market_jsonl_files": _count_jsonl(data_dir / "market_data"),
            "canonical_themes": retriever.available_themes,
        },
    }

    if state["canonical_available"]:
        results = retriever.search(args.query, top_k=3)
        report["retrieval_path"] = "canonical"
        report["results"] = [
            {
                "source_type": row.get("source_type"),
                "title": row.get("metadata", {}).get("title", ""),
                "score": row.get("weighted_score", row.get("score")),
            }
            for row in results
        ]
    elif state["pipeline_bm25_available"] or state["pipeline_vector_available"]:
        service = RetrievalService(data_dir=str(data_dir))
        results = service.search(args.query, top_k=3)
        report["retrieval_path"] = "pipeline_fallback"
        report["results"] = [
            {
                "source_type": row.get("source_type"),
                "title": row.get("metadata", {}).get("title", ""),
                "score": row.get("rrf_score", row.get("score")),
            }
            for row in results
        ]
    elif state["raw_available"]:
        report["retrieval_path"] = "missing_indexes"
        report["error"] = (
            "raw 데이터는 있지만 retrieval 인덱스가 없습니다. "
            f"python3 scripts/build_rag.py --theme-key <theme> --data-dir {data_dir}"
        )
    else:
        report["retrieval_path"] = "missing_data"
        report["error"] = f"데이터 디렉터리 또는 retrieval 자산이 없습니다: {data_dir}"

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report.get("results"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
