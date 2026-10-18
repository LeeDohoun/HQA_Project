"""Record a reviewed mechanical update without editing frozen collector files."""
import argparse
import json
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.lh003.pins import record_amendment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--legal-source", required=True)
    parser.add_argument("--effective-date", required=True)
    parser.add_argument("--changed-at", required=True)
    args = parser.parse_args(argv)
    result = record_amendment(args.state_dir, PROJECT_ROOT, path=args.path, legal_source=args.legal_source,
                              effective_date=args.effective_date, changed_at=args.changed_at)
    print(json.dumps({key: value for key, value in result.items() if key != "after_source"}, indent=2))


if __name__ == "__main__":
    main()
