# Recent One-Month Theme Data Export (2026-06-10)

This folder contains a filtered export of the collected theme datasets for the one-month window from `2026-05-10` through `2026-06-10` inclusive.

Included themes: `ai`, `반도체`, `전력설비`, `2차전지`, `로봇`, `화장품`, `조선`, `바이오`.

Each theme folder contains:

- `theme_targets.jsonl`: theme membership used for the collection.
- `raw/news.jsonl`, `raw/dart.jsonl`, `raw/forum.jsonl`, `raw/chart.jsonl`: source-level rows filtered to the date window.
- `processed/corpus_combined.jsonl`: processed RAG corpus rows filtered to the date window.
- `processed/market_combined.jsonl`: processed market rows filtered to the date window.
- `summary.json`: row counts, coverage counts, and min/max dates per file.

Notes:

- News and DART counts can be lower than target counts when a stock had no recent article or disclosure in the window.
- All rows are filtered to avoid dates after `2026-06-10`.
- Generated from local `data/` files in `HQA_Project`.
