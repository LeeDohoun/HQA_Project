#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_env_status, get_settings, load_project_env
from src.rag.canonical_retriever import CanonicalRetriever


def _module_status(module_name: str) -> dict:
    try:
        importlib.import_module(module_name)
        return {"module": module_name, "ok": True}
    except Exception as exc:
        return {"module": module_name, "ok": False, "error": str(exc)}


def main() -> int:
    load_project_env()
    settings = get_settings()
    env_status = get_env_status()
    retriever = CanonicalRetriever(data_dir=str(settings.data_dir))

    checks = {
        "python": sys.version.split()[0],
        "project_root": str(settings.project_root),
        "data_dir": str(settings.data_dir),
        "data_dir_exists": settings.data_dir.exists(),
        "env_loaded": env_status.loaded,
        "env_file": str(env_status.path) if env_status.path else None,
        "env_message": env_status.message,
        "canonical_state": retriever.describe_data_state(),
        "dependencies": [
            _module_status("dotenv"),
            _module_status("langchain_core"),
            _module_status("langgraph"),
            _module_status("rank_bm25"),
        ],
    }

    print(json.dumps(checks, ensure_ascii=False, indent=2))

    dep_failures = [row for row in checks["dependencies"] if not row["ok"]]
    if dep_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
