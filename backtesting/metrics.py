"""Numerical metrics shared by historical and observed PAPER reports."""

import numpy as np


def max_drawdown(equity_values: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity_values)
    drawdowns = equity_values / peaks - 1.0
    return float(drawdowns.min()) if len(drawdowns) else 0.0
