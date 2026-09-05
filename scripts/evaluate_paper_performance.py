"""Evaluate observed PAPER strategy and baseline JSON; never call models or brokers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.tracing.paper_performance import compare_performance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as handle:
        report = compare_performance(json.load(handle))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
