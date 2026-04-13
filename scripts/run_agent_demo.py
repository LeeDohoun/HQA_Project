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
from src.rag.canonical_retriever import CanonicalRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a retrieval-grounded agent demo")
    parser.add_argument("--query", required=True, help="Question to ask the agent")
    parser.add_argument("--data-dir", default=str(get_data_dir()))
    args = parser.parse_args()

    load_project_env()
    data_dir = Path(args.data_dir)
    os.environ["HQA_DATA_DIR"] = str(data_dir)
    reset_settings_cache()
    retriever = CanonicalRetriever(data_dir=str(data_dir))
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
        from src.tools.rag_tool import reset_retriever_cache
        from src.agents.analyst import AnalystAgent

        reset_retriever_cache(str(data_dir))
        agent = AnalystAgent()
        search = agent.quick_search(args.query)
        if not search["has_results"] or search["source"] != "rag":
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "RAG retrieval 결과가 1건 이상 필요합니다.",
                        "search": search,
                        "state": state,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        answer = agent.answer_question(args.query)
        payload = {
            "status": "ok",
            "query": args.query,
            "data_dir": str(data_dir),
            "retrieved_hits": answer["retrieved_hits"],
            "context_excerpt": answer["context_excerpt"],
            "answer": answer["answer"],
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
                    "message": "에이전트 답변 생성에 실패했습니다.",
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
