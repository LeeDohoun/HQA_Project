# AV001 runner review fixes

The runner remains dry-run by default. These changes do not authorize a design
execution or a holdout evaluation and do not change the preregistration.

## Completed supplement migration

New supplement completions freeze `target_scan` in
`disclosures/dart_full/list_supplement/_state.json`: the full KRX file list and
SHA-256 hashes over 20160601..end_de, traded stock codes, traded stock codes per
corporation, and the reference hashes used to map those identities. Supplement
JSONL rows and `file_manifest` retain their existing meaning. `listing_version`
includes the state hash and identifies the v3 reader.

The existing completed supplement lacks this field. From the repository root:

```bash
# Offline plan; no protected price contents are opened and no files are written.
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m scripts.data.backfill_listing_target_scan --data-dir data

# Operator migration; scans local price identities, adds only target_scan to
# the state, preserves rows/manifest/counters, and uses the collector's lock.
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m scripts.data.backfill_listing_target_scan --data-dir data --write
```

The migration is idempotent: an existing frozen scan is verified, never replaced.
It calls no API, loads no `.env`, evaluates no returns, and opens no experiment
HoldoutSession or ledger. The operator write command reads protected price files
solely to freeze collection metadata, like the supplement collector. It was not
run against the real checkout during this fix.

Observed dry-run output on 2026-10-17:

```json
{"adds_field": "target_scan", "end_de": "20261009", "krx_files": 2539, "protected_files": 120, "protected_price_contents_opened": false, "start_de": "20160601", "state_changed": false, "status": "dry_run", "supplement_files": 2548, "supplement_rows": 152626, "supplement_rows_and_manifest_unchanged": true, "target_corps": 464}
```

Coverage/status refuses an absent scan, changed KRX file list, or changed
non-protected price contents. It never opens 2026H1 prices, including for hashes.
Reference refreshes are checked by mapping current corporate aliases and
classifications against the frozen traded codes, then excluding corporations
already represented by the main list. Protected contents are checked inside
the experiment session, rather than by status.

## Holdout and reproduction

Before opening a session the runner records all price, financial, company,
main/supplement listing and coverage-metadata paths and content hashes, plus
computation source hashes. Protected price hashes come from the frozen scan;
all other hashes come from guarded local reads. Inside the session, the runner
re-inventories and re-hashes before preparing inputs and after all computation,
and rechecks coverage. A difference consumes the session and publishes
`holdout_aborted`; verified results attach the input hashes and verification
status. No protected pre-open content read is needed.

Reproduction compares code content hashes, input content hashes and
deterministic results. HEAD is retained as provenance but excluded from the
replay hash, as are timing and memory measurements. Committing a result alone
therefore does not invalidate reproduction. An added/changed listing input
still requires the original snapshot to reproduce; it is never silently ignored.

`sensitivity_unavailable` remains outside the terminal release list. Results
record the reason under rule O and in `sensitivity_unavailable_reasons`.
The CLI explicitly prints `USER DECISION REQUIRED`, states that LH002 remains
blocked, and states that the consumed holdout cannot be reopened. Deciding how
to resolve that state requires a user decision; no release rule was invented.

## Band and historical regression diagnostics

Every strategy month reports `band_skipped_buy_value` and
`band_skipped_sell_value`. Primary and secondary control reports include their
per-month means and `band_skipped_by_path` arrays. These are desired notionals
before cost scaling, in portfolio value units (initial cash = 1), split by side.
Full exits bypass the band. Frozen positions and otherwise blocked buys do not
count as trades skipped because of the band. These diagnostics do not change
orders or performance.

The optional committed-HC003 regression is offline and read-only:

```bash
PYTHON_DOTENV_DISABLED=1 OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false PYTHONDONTWRITEBYTECODE=1 \
RUN_AV001_HC003_ARCHIVE_CHECK=1 venv/bin/python -m pytest -q -s \
  tests/test_experiments_av001.py -k optional_committed_HC003
```

It pins each price hash to the committed
`HC003_operating_leverage_turnover/results/20261010T021152472126Z.json.gz`,
replays saved U/P selections with a full price book for three months and with
AV001's quarterly `PriceBook.from_windows` streaming path for all 103 months,
and prints the maximum absolute monthly-e difference. File-open assertions
restrict price reads to 2017-2025. This checks accounting against the archive;
it does not rerun financial selection or claim a new experiment result.

2026-10-17 comparison: full book, 3 months, max absolute monthly-e difference
`2.3418766925686896e-16`; quarterly streaming, 103 months, max difference
`4.475586568020162e-16`. The optional test passed in 243.00 seconds.

An additional local AV001 dry-run passed file-open traps for protected prices
and `.env`, with 103 planned/price-covered months. All listing windows remained
incomplete because the completed real supplement still lacks `target_scan`.
This check isolated the `src.runner` package initializer while using its real
calendar: an unisolated attempt was stopped by the `.env` trap when the existing
cost-model calendar import eagerly imported runtime LLM configuration. That
runtime import behavior was outside this change. The backfill CLI needs no such
isolation.

The experiment and ingestion regression command (the optional archive replay is
run separately) is:

```bash
PYTHON_DOTENV_DISABLED=1 OPENAI_API_KEY=offline-disabled \
OPENAI_BASE_URL=http://127.0.0.1:9/v1 LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q \
  tests/test_experiments_*.py tests/test_dart_financial_date_rules.py \
  tests/test_research_ops.py tests/test_backtesting_experiment_registry.py \
  tests/test_dart_listing.py tests/test_dart_filing_dates.py \
  tests/test_dart_quarterly.py tests/test_dart_list_supplement.py \
  tests/test_backtesting_holdout.py
```

Final verification on 2026-10-17: **1240 passed, 1 skipped in 183.40s**.
The skipped optional archive comparison passed separately (1 passed, 243.00s).
The focused AV001/listing/supplement check passed 126 tests with that optional
comparison skipped. `git diff --check` passed. No real AV001 execution, holdout,
network call, data migration, result/registry write or commit was performed.
