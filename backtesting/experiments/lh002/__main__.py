"""python -m backtesting.experiments.lh002 {probe,screening,final,status}."""
from backtesting.experiments.lh001.__main__ import main as shared_main


def main(argv=None):
    return shared_main(argv, default_experiment="LH002")


if __name__ == "__main__":
    raise SystemExit(main())
