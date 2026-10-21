"""LD002's forward-only virtual books. No collection or account integration."""
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.lh003 import Refused

EXPERIMENT_ID = "LD002_llm_delegated_virtual"
PREREG_COMMIT = "78174f5"
PROMPT_DIR = PROJECT_ROOT / "research/experiments" / EXPERIMENT_ID / "prompts"
MODEL = "gpt-6-luna"
CODEX_VERSION = "codex-cli 0.160.0"
START = "2026-11-16"
END = "2027-11-15"
ACTIVATE_BY = "2026-11-13T23:59:59+09:00"
FREEZE_AT = "2027-11-23T00:00:00+09:00"
INITIAL_CASH = 100_000_000.0
MULTIPLIERS = (1.0, 1.5, 2.0)
PATHS = 200
HEAVY_LOCK = Path("/run/hqa-heavy/run.lock")
