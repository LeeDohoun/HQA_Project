"""Historical strategy experiments and offline PAPER evaluation utilities."""

__all__ = ["TemporalPriceLoader", "TemporalEvidence", "run_leader_backtest"]


def __getattr__(name):
    if name == "run_leader_backtest":
        from .leader_backtest import run_leader_backtest

        return run_leader_backtest
    if name in {"TemporalPriceLoader", "TemporalEvidence"}:
        from . import temporal_evidence

        return getattr(temporal_evidence, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
