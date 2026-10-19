"""Fitness backtest: the PAPER analysis replayed point-in-time next to simpler strategies.

- `pit_data` writes a copy of the PAPER data in which every row carries the time PAPER would
  have observed it, and lets the live loader read it for a past decision time.
- `replay` runs the unchanged live analysis (`SharedAnalysisService`) on that copy against a
  simulated account, with cached and guarded subscription calls.
- `simulate` executes every strategy's plans with one daily-bar execution model and computes
  the comparison metrics.
"""
