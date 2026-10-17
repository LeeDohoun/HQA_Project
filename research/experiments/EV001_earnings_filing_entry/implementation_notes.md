# EV001 runner implementation and verification

The runner implements the immutable preregistration from `7acd752` (SHA-256
`383f306d52b74df71946ce0da08e335e95f1f5f5db947a9ec64dd8f81998874f`).
Default invocation is a dry run. Real design execution and holdout evaluation
were not run during implementation. No commit was made.

## Changed files

- `backtesting/experiments/ev001.py`: event construction, variants, event returns,
  controls/draws, inference, diagnostics, publication, replay and holdout CLI.
- `src/ingestion/dart_listing.py`: optional stage end for frozen-scan price-content
  verification. The full frozen file inventory remains checked. Existing callers
  retain their behavior.
- `src/runner/__init__.py`: lazy public exports so an offline calendar/cost import
  does not initialize trading clients or load private environment files.
- `scripts/ops/research_run.sh`: EV001 allowlist and explicit mode dispatch.
- `tests/test_experiments_ev001.py` and `tests/test_research_ops.py`: synthetic rule,
  boundary, inference, ordering, holdout, publication and dispatch coverage.
- This implementation note.

## Verification

The final combined regression run passed **1,069 tests, with 1 optional AV001
archive replay skipped, in 123.19 seconds**. It includes all 77 EV001 fixtures
and every requested existing suite. The strengthened runner-import audit also
passed separately (1 passed, 76 deselected, 3.07 seconds). `git diff --check`
passed. No changes appeared in any protected path or preregistration.

```bash
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
venv/bin/python -m pytest -q \
  tests/test_experiments_ev001.py tests/test_experiments_av001.py \
  tests/test_experiments_ordering.py tests/test_dart_listing.py \
  tests/test_dart_financial_date_rules.py tests/test_experiments_hc003.py \
  tests/test_experiments_hf001.py tests/test_experiments_lh002.py \
  tests/test_research_ops.py tests/test_dart_filing_dates.py \
  tests/test_dart_quarterly.py tests/test_dart_list_supplement.py \
  tests/test_backtesting_holdout.py tests/test_backtesting_experiment_registry.py \
  tests/test_backtesting_cost_model.py tests/test_price_calendar_cleansing.py \
  tests/test_legacy_runner_cleanup.py
```

All pytest invocations used `venv/bin/python -m pytest -q`, with
`PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1`, disabled OpenAI endpoints
and disabled tracing. Fixture result, registry and ledger writes are confined to
pytest temporary repositories. The offline calendar/cost test installs file-open
and socket audit traps and verifies that runtime runner modules are not imported.

## Real-data dry run: blocked by the supplied workspace

Command attempted (stdout/stderr retained in `/tmp/ev001-dry-run.log`):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 venv/bin/python -m backtesting.experiments.ev001 --dry-run
```

It exited 1 with:

```text
ValueError: L is undefined: the merged listing has no dated rows
```

Observed workspace metadata: `data/` is a directory, rather than the symlink
described in the task; `data/disclosures/dart_full/` is absent. `venv/` is the
provided symlink. No data link was changed, no substitute data was used and no
other checkout was inspected. A correct read-only data path is required to
complete real event counts, listing status/L, and a data-based full-run projection.

## Performance

A fresh-process synthetic fixture used 64 stocks, 12 entry dates and 200 control
paths. Four cost scenarios with separate buy/sell inputs took **0.6964 seconds**;
control generation/scoring took **0.0583 seconds**. Numeric raw price arrays used
**1.061 MiB** and process peak RSS was **99.04 MiB**. These measurements use
synthetic prices and cannot establish full-data memory or runtime.

The dry-run projection scales the measured per-name/date cost and control work
to both rules and all three variants, adds observed preparation time, and reports
a 1x..3x runtime range. RSS estimates add draw/serialization/table reserves to the
measured preparation peak. Raw prices stream by calendar quarter into compact
numeric per-stock arrays; only one variant's outcome tables survive at a time.
Peak RSS is checked against **7,000,000,000 bytes** during construction/evaluation.
Full-data compliance remains unverified until the missing input path is supplied.

## Interpretations

These are the complete interpretations recorded by the runner. They preserve
the preregistration; unavailable inputs fail explicitly. The executable source's
`INTERPRETATIONS` list is also included in each result artifact.

1. The merged listing supplies receipt provenance and L, including later listing metadata. Frozen-target coverage checks retain the complete file inventory but hash price contents only through the stage end and never open protected price contents. Financial values are gated by actual public date before derivation, under both S and O. Latest stored revisions are not historical vintages.
2. An event uses the preferred CFS (otherwise OFS) known on d for q; all other quarters are strictly before d. Same-day older quarters cannot supply event derivation or comparison inputs. A newly published OFS superseded by an already known CFS is not a new selected version. Aliases are separate stock events; identical stock/quarter/date events are deduplicated.
3. The correction-link sample is stage-eligible saved versions known under at least one rule before the evaluation end. Date exclusions count saved versions by receipt year; event exclusions count stock/quarter/date candidates by public-date year, with sequential first-failure reasons. Existing quarterly same-division/currency derivation and its dependency availability at each filing are preserved.
4. V_tau is frozen at the final exchange session of the preceding calendar month using unchanged HC002 eligibility, industry coverage policy and strict financial availability. Event-time financial information never updates V_tau.
5. HF001 volatility uses exactly 60 verified returns ending at the last session <=d, market-specific max-tie ranks in V_tau, and leaves missing volatility unfiltered with counts. Raise coverage is HF001's full calendar-day window evaluated at tau, so same-day/weekend filings effective at tau are included. Missing listing coverage excludes only that event from ev_new20_f.
6. Entry requires HC003 3.1 price/base validity, positive volume and no upper-limit open. A lower-limit open alone is not an EV001 entry exclusion. Missing required execution market/ADV is an explicit error, never a stale cost substitute.
7. HF001 permissible close valuations exclude lower-limit closes. Resumption/delisting classification stops at the stage end; sale costs use the valuation day's close/market/prior-20-session ADV. Overlap lasts through the scheduled exit close, including a missing-exit valuation. No holding period is extended.
8. Controls reset default_rng(0) per variant and rule, draw path then entry date from sorted executable V_tau excluding that date's final event stocks and path holdings, and retain positions through scheduled exits. Baseline and -100% use the same stored draws. A shortage makes the entire control uncomputable, without partial resampling.
9. Monthly observations weight events equally within entry month, then months equally. Calendar NW(3) preserves gaps, divides covariance by valid N and has no finite-sample correction. Design-only reference effects use entry months 2017-05..2024-12, including in-bound exits in 2025.
10. Holm always has three variants per rule; fewer than 300 events/36 event months or undefined SE supplies p=1. Existing EV001 rows plus this run's three rows set the strict t threshold. The 2025 sign and all subgroups are diagnostics.
11. After separate baseline/-100% rule verdicts, a changed verdict is delisting_sensitive. Combined priority is sensitivity_unavailable, delisting_sensitive, one-rule pass/version_sensitive, insufficient, fail. Both rule passes are required. Holdout uses its own minimum and effect gate; t, costs x1.5 and random controls are reports only.
12. Secondary gross paths use each variant's final event set, omit horizons outside the stage boundary and do not alter primary overlap selection. An unavailable member outcome makes the entire date/horizon diagnostic unavailable without shrinking its benchmark. Same-day maintenance is restricted to final new-entrant entry dates before independent entry/filter/overlap selection; all-period maintenance is labelled separately. Both are report-only. Capacity is the 1%-ADV limit for an equal-weight entry-date cohort, constrained by both execution sides.
13. Reproduction checks content hashes and deterministic results, excluding HEAD and performance. Holdout freezes all inputs before opening, verifies protected price hashes only inside one EV001 session, then re-inventories/re-hashes after computation under the shared ordering lock. An unavailable O sensitivity is not a registered holdout terminal release; stop and report without inventing one.

## Review follow-up

- P1-1: reporting outcomes explicitly report a missing permissible exit or an
  undefined adjusted return. If any executable benchmark member is unavailable,
  the entire entry-date/horizon diagnostic is unavailable; no available-subset
  benchmark is substituted. `path_unavailable` and maintenance
  `unavailable_paths` include date, horizon, reason, member codes and counts.
  Primary outcome valuation remains strict. Tests cover an entry-day limit-down
  followed by recovery, for an event stock and a benchmark-only stock, and an
  unavailable maintenance valuation.
- P1-2: EV001 and AV001 design execution record `design_aborted` for every
  variant on a failure after registration/calendar/input-hash preflight,
  including preparation, evaluation, interruption and publication failures.
  Compact rows use normal registry validation and share a run ID, input-category
  SHA-256 digests (sorted JSON of each original category), exception type/reason
  and failure file/function/line. Aborted rows count toward trial thresholds and
  block LH002 release; a previous complete run cannot substitute. Existing
  holdout-aborted rows now carry the same provenance. The bookkeeping is absent
  from successful results and read-only computations/replays do not append
  trials. Both runners' successful deterministic payloads are compared as bytes,
  excluding existing runtime/peak-memory measurements.
- P2-1: `maintenance` restricts candidates to final new-entrant entry dates
  before entry/filter/overlap selection. `maintenance_all_period` separately
  retains the independent full-period diagnostic. Both label their date scope
  and remain excluded from judgement.

The five requested suites (`test_experiments_ev001`, `test_experiments_av001`,
`test_experiments_ordering`, `test_dart_listing`, `test_research_ops`) passed
**287 tests, with 1 optional AV001 archive replay skipped**. `git diff --check`
passed. Tests use synthetic temporary repositories; no real design/holdout was
executed and no commit was made. No existing dry-run process was stopped or
restarted. The supplied real data path is
`/home/are01/src/HQA_Project/HQA_Project/data`, read-only and permitted only via
`--data-dir` for dry runs; this follow-up did not start another real-data dry run.
