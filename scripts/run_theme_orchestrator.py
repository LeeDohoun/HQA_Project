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
    parser = argparse.ArgumentParser(
        description="Run theme-level multi-agent leader orchestration"
    )
    parser.add_argument("--theme", required=True, help="Theme name, for example: 2차전지")
    parser.add_argument("--theme-key", default="", help="Optional normalized theme key")
    parser.add_argument("--candidate-limit", type=int, default=5, help="How many candidates to evaluate")
    parser.add_argument("--top-n", type=int, default=3, help="How many leaders to return")
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
                    "message": f"테마 오케스트레이션에 필요한 retrieval 자산이 없습니다: {data_dir}",
                    "state": state,
                    "next_action": "python3 scripts/build_rag.py --theme-key <theme> --data-dir ./data",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    try:
        from src.agents import ThemeLeaderOrchestrator

        orchestrator = ThemeLeaderOrchestrator(data_dir=str(data_dir))
        result = orchestrator.run(
            theme=args.theme,
            theme_key=args.theme_key,
            candidate_limit=args.candidate_limit,
            top_n=args.top_n,
        )
        result["data_state"] = state
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "success" else 1
    except ImportError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "테마 오케스트레이션 런타임 의존성이 부족합니다.",
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
                    "message": "테마 오케스트레이션 실행에 실패했습니다.",
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
