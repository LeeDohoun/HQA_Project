# Data Collection and Cleansing

The collection path is deterministic and does not need an LLM. Run
`python -m scripts.data.collect --theme <theme>` for collection and build, or
`python -m scripts.data.build --theme-key <theme>` to rebuild existing data.
Model analysis is a separate service operation, not a collection CLI option.
Do not enable the analysis scheduler while validating data credentials.
Collection does not submit broker orders. See [command migration](repository-layout.md).

## Credentials

- `DART_API_KEY`: disclosures, financial statements and the corporate-code master.
- `KRX_OPEN_API_KEY` (alias `KRX_API_KEY`): stock daily data and separately approved
  market/industry index services. An issued key alone does not prove service access.
- News/forum currently use crawlers, not a Naver API integration.
- `OPENAI_API_KEY`, KIS credentials and internal service secrets are not needed to
  run collection-only commands. Never put credential values in example files,
  source records, exception messages or reports.

Authentication, transport, schema failure and valid no-data are different states.
OpenDART `013` alone represents no-data; other business statuses fail. KRX requires
the documented response schema. Empty KRX results are not proof of a holiday and
are not cached. Partial/error batches retain per-stock source failures, return a
nonzero exit code and do not build or analyze that batch.

## Source Contracts

| Source | Cleansing contract |
| --- | --- |
| Security master | Validate six-digit stock/eight-digit DART codes; backfill saved targets; missing required or conflicting mappings fail before collection. Theme membership is not company identity or sector classification. |
| DART | Validate and finish pagination before title filtering; dedupe receipts; retain corrections/withdrawals and verified receipt-matched fields. Provider messages cannot become successful empty results. |
| Financials | Fiscal-year lookback is independent of the incremental disclosure window. Keep CFS/OFS, original units, receipt and actual observation time. Select the latest known observation, including A -> B -> A reversions. |
| News | Prefer actual publisher time. Relative search times remain estimates, never authoritative timestamps. Distinguish document, summary and available fragments. Require an explicit company-name/code mention before attaching a stock; unrelated results are quarantined. A mention is not proof that the company is the economic subject of every claim. |
| Events | Merge only exact full-body syndication with compatible date/category/headline facts. Preserve corrections, withdrawals, opposing claims and different numbers. This is not general semantic clustering or consensus extraction. |
| Stock OHLCV | Validate provider schema and numeric fields; retain actual collection time and content/observation versions; check calendar-session coverage before computing period factors. Do not fill missing prices or infer trading suspensions. |
| Benchmarks | Keep the existing versioned index archive; choose a mapping valid for each event comparison window. A classification change inside the window prevents a spliced comparison. |
| Forum | Optional, excluded from default collection and the current fixed analysis DAG. Use post URL identity rather than title/time collisions. |

Unknown publication times remain raw evidence but cannot anchor timed events.
Summary/snippet scope remains attached through runtime evidence. Missing or invalid
legacy price observation times are copied to `quarantine/chart/<theme>.jsonl` and
excluded from new derived price files; the original archive is not deleted.
Fresh, dated recollection is required to satisfy price completeness.

## Reuse and Publication

Default CLI runs use a rolling 400-day bootstrap window ending yesterday in Korea.
With no explicit date options they enable shared incremental collection:

- A source/stock/request identity has a file lock and a 15-minute reuse window in
  `collection_state/`. This coalesces simultaneous requests across themes and CLI
  processes; it does not start a periodic scheduler.
- Successful nonempty DART and stock-price requests advance the completed range.
  Subsequent requests revisit seven calendar days to observe recent corrections.
  Authentication/schema errors and empty results do not advance coverage.
- `_shared_<stock>.jsonl` files under each raw source are common observation archives.
  Theme files are compatibility projections; a newly added theme receives the full
  available shared history, not just the latest incremental response.
- News remains item-bounded, and financials independently recheck annual periods.
  Older price revisions outside the overlap require explicit dated recollection.
  Supplying either date option disables shared incremental mode for deliberate
  backfills. It never makes newly collected data historically available.
- Unchanged consecutive content retains its first observation. A -> B -> A creates
  three observation episodes, not two globally unique hashes. Archive writes are
  locked and atomic; malformed existing files fail rather than being silently
  repaired or truncated.
- Unchanged raw inputs reuse completed index builds. Full documents are stored once
  in `documents.jsonl`; retrieval chunks no longer repeat the full document body.
  `--update-mode overwrite` replaces derived indexes only, not raw history.
- The fixed runtime captures a published price/document generation per theme.
  New generations become visible through an atomic `current.json` pointer only
  after the build completes; an interrupted build cannot replace the last published
  generation. Financial observations remain a separately dated, as-of-filtered
  archive. Legacy RAG compatibility files are individually atomic, not one combined
  cross-file transaction. Old generations are retained, not deleted automatically.
  Legacy RAG vector-store identities still collapse document revisions; the fixed
  DAG does not use those stores. Do not use that legacy retrieval path to evaluate
  correction-sensitive trading decisions without a separate migration.

The XKRX session dependency is pinned in `requirements.txt`; its coverage is not a
substitute for a live exchange calendar feed. See the source-backed special-session
notes in `src/runner/trading_calendar.py`. Missing bars fail completeness checks,
and absence of a corporate-action warning still does not certify adjusted prices.
The current verified special-session coverage expires on **2026-11-01**. Dates
from then onward, and unverified November sessions in 2021-2023, fail explicitly
until official schedules are reviewed; no future CSAT close time is guessed.

## Offline Verification

The regression suite uses synthetic provider responses and temporary directories.
The following command also points any accidentally unmocked OpenAI client at a
closed loopback port, rather than a paid endpoint:

```bash
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  venv/bin/python -m pytest -q
```

No live credentials are needed for those tests. Live KIS tests remain separately
gated. Before a broad data refresh, validate DART and each approved KRX service with
a small read-only request. Do not retry authentication failures across a large
universe. No model-quality, trading-return or live throughput claim follows from
passing offline contracts.
