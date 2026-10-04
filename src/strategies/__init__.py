"""Deterministic research strategies; importing this package starts no runners."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path


@lru_cache(maxsize=2)
def _runner_module(name: str):
    # runner/__init__.py imports live analysis and environment loaders. Load only
    # these existing, side-effect-free leaves, retaining their rules unchanged.
    path = Path(__file__).resolve().parents[1] / "runner" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"hqa_strategy_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from .base import Strategy, StrategySpec, TargetPosition
from .data import PointInTimeData

__all__ = ["Strategy", "StrategySpec", "TargetPosition", "PointInTimeData"]
