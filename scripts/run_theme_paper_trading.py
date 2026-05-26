#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runner.theme_paper_runner import ThemePaperRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multi-theme LLM paper trading.")
    parser.add_argument("--config", default="config/theme_trading.yaml", help="Path to theme trading config YAML.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle.")
    parser.add_argument("--loop", action="store_true", help="Run continuously with configured interval.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = ThemePaperRunner(config_path=args.config)
    if args.loop:
        runner.run_loop()
        return 0

    result = runner.run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
