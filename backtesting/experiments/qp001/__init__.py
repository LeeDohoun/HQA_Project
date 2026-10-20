"""QP001: preregistered profitability increment over AV001 av_all."""

EXPERIMENT_ID = "QP001_profitability_on_av"
VARIANT = "q_roa50"
RULES = ("S", "O")
M = 167
N_CONTROLS = 200
DESIGN_START, DESIGN_END = "2017-05", "2023-12"
VALIDATION_START, VALIDATION_END = "2024-01", "2025-11"
CUTOFFS = {"design": "2024-01-01", "validation": "2026-01-01"}
