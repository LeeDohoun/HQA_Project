"""LH003: fixed forward decisions, local archives, no trading orders."""
from backtesting.experiment_registry import PROJECT_ROOT

EXPERIMENT_ID = "LH003_llm_leader_forward"
EXPERIMENT_DIR = PROJECT_ROOT / "research/experiments" / EXPERIMENT_ID
PROMPT_DIR = EXPERIMENT_DIR / "prompts"
MODEL = "gpt-6-luna"
CODEX_VERSION = "codex-cli 0.160.0"
CALENDAR_VERSION = "4.13.2"
CALL_CAP = 320
EVALUATION_AT = "2027-12-17T00:00:00+09:00"


class Refused(ValueError):
    """A failed prerequisite, never a missing value substituted with a default."""
