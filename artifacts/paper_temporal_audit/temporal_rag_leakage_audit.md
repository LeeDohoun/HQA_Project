# Temporal RAG Leakage Audit

This audit checks representative AI-theme rows preserved in the local evidence corpus.
A row passes when the maximum document or price date used at each `as_of_date` does not exceed that decision date.

- Document source: `experiment_results/theme_recent_month_data_20260610/ai/processed/corpus_combined.jsonl`
- Price source: `experiment_results/theme_recent_month_data_20260610/ai/processed/market_combined.jsonl`

| as_of_date | Document rows | Max document date | Price rows | Max price date | Future rows excluded (doc/price) | Pass |
|---|---:|---|---:|---|---:|---|
| 2026-05-20 | 847 | 2026-05-20 | 400 | 2026-05-20 | 6151/650 | True |
| 2026-05-31 | 2130 | 2026-05-31 | 700 | 2026-05-29 | 4868/350 | True |
| 2026-06-10 | 6998 | 2026-06-10 | 1050 | 2026-06-10 | 0/0 | True |
