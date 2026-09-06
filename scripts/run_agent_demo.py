#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_data_dir, load_project_env, reset_settings_cache
from src.evidence.retriever import EvidenceRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect retrieval results for a query")
    parser.add_argument("--query", required=True, help="Query to search in canonical retrieval assets")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    args = parser.parse_args()

    load_project_env()
    data_dir = Path(args.data_dir)
    os.environ["HQA_DATA_DIR"] = str(data_dir)
    reset_settings_cache()
    retriever = EvidenceRetriever(data_dir=str(data_dir))
    state = retriever.describe_data_state()

    if not state["retrieval_assets_available"]:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": f"retrieval 자산이 없습니다: {data_dir}",
                    "state": state,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    try:
        results = retriever.search(args.query, top_k=5)
        if not results:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "retrieval 결과가 1건 이상 필요합니다.",
                        "state": state,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        payload = {
            "status": "ok",
            "query": args.query,
            "data_dir": str(data_dir),
            "retrieved_hits": [
                {
                    "source": row.get("source_type"),
                    "title": (row.get("metadata") or {}).get("title", ""),
                    "score": row.get("weighted_score", row.get("score")),
                }
                for row in results
            ],
            "context_excerpt": retriever.search_for_context(args.query, top_k=3),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except ImportError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "에이전트 런타임 의존성이 부족합니다.",
                    "error": str(exc),
                    "next_action": "python3 -m pip install -r requirements.txt",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "retrieval 점검에 실패했습니다.",
                    "error": str(exc),
                    "state": state,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
