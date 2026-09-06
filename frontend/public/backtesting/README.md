# Published Historical Backtests

These files are served at `/backtesting/` and loaded by `frontend/src/lib/backtesting.ts`. Keep the asset names stable unless the consuming routes are updated together.

| Public asset | Archived source |
| --- | --- |
| `ai-strategy-comparison.json` | [multi-agent-centered-comparison.json](../../../research/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.json) |
| `ai-strategy-comparison-report.md` | [multi-agent-centered-comparison.md](../../../research/backtesting/ai_strategy_comparison/comparison_table/multi-agent-centered-comparison.md) |
| `agent-architecture-comparison.json` | Historical publication only; its referenced source directory is absent from this checkout |

The first two assets are intentional byte-identical publication copies, not independent experiments. No checked-in generator currently rebuilds these comparison assets. New backtest runs do not automatically replace them.

The 2026-09-06 repository cleanup preserved all three public files unchanged. Embedded source paths describe the original experiment layout; [the relocation manifest](../../../research/archive_manifest.json) maps files that are available in this checkout to their archive locations.

These are older research results, not Luna PAPER observations. Of the strategy comparison's 68 referenced result files, 50 were already missing before relocation. See [the archive notes](../../../research/README.md) for provenance limits.
