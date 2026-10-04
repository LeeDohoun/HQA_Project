"""M5 strategy contract. Scores are features, never orders or position limits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from .data import PointInTimeData


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    sleeve: str
    thesis: str
    counterparty: str
    universe: str
    inputs: tuple[str, ...]
    decision_time: str
    horizon_sessions: int
    stage: str
    capacity_krw: float | None = None
    preregistration: Path | None = None


@dataclass
class TargetPosition:
    strategy_id: str
    as_of: datetime
    stock_code: str
    score: float
    reason: str
    weight: float | None = None


@runtime_checkable
class Strategy(Protocol):
    spec: StrategySpec

    def score(self, as_of: datetime, data: PointInTimeData) -> pd.DataFrame:
        """Return stock_code, score columns, with higher scores preferred."""
        ...

    def generate(self, as_of: datetime, data: PointInTimeData, top_n: int) -> list[TargetPosition]:
        """Same inputs yield the same positions; sizing belongs to M7."""
        ...


def _targets(spec: StrategySpec, as_of, scores: pd.DataFrame, top_n: int) -> list[TargetPosition]:
    if type(top_n) is not int or top_n < 0:
        raise ValueError("top_n must be a nonnegative integer")
    ranked = scores.loc[np.isfinite(scores.score)].sort_values(
        ["score", "stock_code"], ascending=[False, True], kind="stable",
    ).head(top_n)
    return [TargetPosition(spec.strategy_id, pd.Timestamp(as_of).to_pydatetime(),
                           row.stock_code, float(row.score), spec.thesis)
            for row in ranked.itertuples(index=False)]
