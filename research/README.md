# Research Archive

Historical experiments and exported datasets live here, separate from application code and current runtime data. This archive is not a record of the current Luna PAPER system's performance.

## Layout

| Location | Contents |
| --- | --- |
| `backtesting/results/` | Earlier backtest runs, sweeps, proof validation, and interpretation documents formerly under `data/backtest_results/` |
| `backtesting/ai_strategy_comparison/` | Historical strategy comparisons and available source runs formerly under `experiment_results/backtesting/` |
| `backtesting/reports/backtesting_2024_report/` | Standalone HTML report formerly under `artifacts/` |
| `datasets/theme_recent_month_data_20260610/` | Fixed collection export for 2026-05-10 through 2026-06-10 |
| `archive_manifest.json` | Original path, archive path, byte size, and SHA-256 for every relocated file |

## Preservation Rules

- The 2026-09-06 cleanup relocated 257 tracked files without changing their contents. Untracked files were not moved or deleted.
- Original JSON, CSV, Markdown, and HTML retain historical paths, dates, configuration, and conclusions. Use `archive_manifest.json` to translate old paths; do not interpret old paths as current output locations.
- Research claims were not recalculated or validated by this cleanup. Missing source files were not reconstructed, and no LLM calls were made.
- The active LLM cache remains at `data/backtest_results/llm_cache/` to preserve reuse. It is not part of this relocation.

## Active Interfaces

- Backtest code and execution instructions remain in [backtesting/](../backtesting/README.md).
- New runtime results still default to `data/backtest_results/`. Move only selected, reviewed experiment snapshots into this archive.
- The application reads the three published assets in [frontend/public/backtesting/](../frontend/public/backtesting/README.md). Those assets keep their existing URLs and bytes; the archive is not directly served by the application.
- Runtime collection, account, order, budget, and database files stay in `data/`, outside this archive.

## Known Provenance Gaps

At the time of relocation, the published strategy comparison listed 68 result paths, but only 18 corresponding files existed in this checkout. The remaining 50 files were already absent. The published architecture comparison's `agent_architecture_validation/uncontaminated_4agent_runs` source directory was also already absent.

The available comparison files and web assets are retained for historical reference, not presented as a complete reproducible experiment bundle. Adding missing sources requires the original outputs; their results must not be synthesized from summary numbers.
