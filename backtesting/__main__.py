"""Explicit entry point for historical experiments and recorded PAPER evaluation."""
from __future__ import annotations

import argparse
import importlib
import sys


COMMANDS = {
    "run": ("leader_backtest", "Run a historical strategy; LLM scoring is opt-in."),
    "sweep": ("sweep_leader_backtest", "Compare numerical strategy parameters."),
    "validate": ("proof_validation", "Compare fixed experiments; may call an LLM unless --mock-llm."),
    "build-evidence": ("build_period_evidence", "Build a historical evidence snapshot."),
    "clean-evidence": ("clean_period_evidence", "Clean a snapshot into a separate output directory."),
    "build-membership": ("build_theme_membership", "Build historical theme membership evidence."),
    "paper-runtime": ("paper_runtime", "Read PAPER audit and budget ledgers without API calls."),
    "paper-performance": ("paper_performance", "Compare recorded net equity and fills without API calls."),
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Commands:\n" + "\n".join(
            f"  {command:18} {description}" for command, (_, description) in COMMANDS.items()
        ) + "\n\nUse python -m backtesting COMMAND --help for command options.",
    )
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(argv[:1])
    module_name, _ = COMMANDS[args.command]
    command = importlib.import_module(f"backtesting.{module_name}")
    result = command.main(argv[1:])
    return 0 if result is None else result


if __name__ == "__main__":
    raise SystemExit(main())
