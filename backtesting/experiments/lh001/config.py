"""Experiment identities for the shared LH001/LH002 system."""
from dataclasses import dataclass
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    prompt_dir: Path
    workspace_subdir: str
    probe_version: str
    budget_cap: int = 1100

    @property
    def name(self):
        return self.experiment_id.split("_", 1)[0]


LH001 = ExperimentConfig(
    "LH001_llm_hegemony_judge",
    PROJECT_ROOT / "research/experiments/LH001_llm_hegemony_judge/prompts",
    "research/lh001", "v1",
)
LH002 = ExperimentConfig(
    "LH002_llm_hegemony_judge",
    PROJECT_ROOT / "research/experiments/LH002_llm_hegemony_judge/prompts",
    "research/lh002", "v2",
)
EXPERIMENTS = {config.name: config for config in (LH001, LH002)}
