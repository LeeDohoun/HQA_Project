# CH004 runner implementation and verification

The runner implements the immutable `08079e2` preregistration (SHA-256
`649848533003559651e541e4be69bf002402e253eadb4ac92b2df23378583f33`).
EV001's `7acd752` and CH001's `b53485e` documents are checked for immutable
history and their full content hashes. EV001 source is restricted to the two
approved versions described below.

## EV001 code version

The coordinator approved exactly these two `ev001.py` SHA-256 values:

- `08079e2` (CH004 registration):
  `1063af57c51f07af9f28f96b523aede7ae10d44ed9317d3f03705f708be605e6`.
- `f8c6845` (merged runner):
  `e101b5ac04c8c56d588f7d3fbea8ad8a4a879b416520f81337e7c83ae50884db`.

Any other hash stops before input preparation. The actual hash appears in
`ev001_code_sha256` and `source_hashes.code["backtesting/experiments/ev001.py"]`.
This decision follows the coordinator's review of
`git diff 08079e2 HEAD -- backtesting/experiments/ev001.py src/ingestion/dart_financial_date_rules.py`:
the changes add an optional `bounds` argument (default `None` preserves the
registered bounds), `FinancialVersions(cutoff=None)` defaulting to the registered
stage cutoff and refusing later cutoffs, calendar preflight, aborted-publication
plumbing, and a holdout rehearsal. The design-stage `ev_new20` definition is
unchanged; the `c4617de` regression test confirms unchanged 2015–2025 XKRX sessions.
CH004's section 2.x assertion still compares its frozen baseline against EV001
computed in-process with the same code, including both rules and all scenarios.
It does not compare saved EV001 results or claim a cross-version result replay.

## Files and scope

- `backtesting/experiments/ch004.py`: EV001 baseline freezing/replay assertions,
  chart ranks, monthly differences, controls, inference, diagnostics, provenance,
  publication and default dry-run CLI.
- `tests/test_experiments_ch004.py`: synthetic event, inference, access-boundary,
  execution-lock, failure-recording and publication fixtures.
- `tests/test_experiments_ch001.py`, `tests/test_experiments_ch002.py`,
  `tests/test_experiments_ch003.py`: real temporary Git registration fixtures and
  captured-registration abort assertions.
- This note.

CH001–CH003 runner sources remain unchanged; their test fixtures were updated.
EV001, AV001 and HF001 are reused without source edits. No preregistration, data, real registry,
real result file, holdout ledger or symlink was modified. No commit was made.
No real-data dry run, design execution or holdout evaluation was run. EV001
saved results and real EV001 registry rows were not read. Existing regression
tests may create/read their own EV001 fixtures in temporary repositories.

## Implementation decisions

1. Check the same bounded listing status that EV001 checks before preparation.
   An incomplete supplement returns `data_incomplete` before financial, event,
   price-book or chart preparation, so another missing input cannot obscure the
   original stop reason. For complete inputs, call unchanged EV001 preparation
   once for both S/O, preserving its full, pre-entry `prepared["universes"]`.
   Other preparation errors propagate. O's unavailable original-filing
   sensitivity is propagated without evaluating O.
2. For each available rule, call EV001 `select_events(..., "ev_new20")`,
   `outcome_tables(..., 20)` and `observations`. Copy the final events, all frozen
   universe memberships and all cost/delisting scenarios. Repeat EV001's own
   computation and assert exact event keys, public/entry/scheduled-exit dates,
   decision dates, universe membership, benchmarks and every scenario's `x_i`.
   Complete both rules' freezes/replays before the first chart calculation.
   The original snapshot remains unchanged in `frozen_baselines`; its recorded
   hash is directly reproducible. `chart_events` joins annotations by event key.
3. Avoid retaining another full price DataFrame: use the session-aligned raw
   return, verification and market arrays already held by `EV001.EventPrices`.
   Call CH001 `adjusted_values` directly for each 252/21-session window. The
   book's verification mask makes both missing and unverified stock sessions
   invalid; because it does not preserve their distinction, report their common
   reason as `unverified_or_missing_session`. This changes no validity decision.
   No price, return, market or calendar value is filled or substituted.
4. Market ranks include every eligible member of the original `V_tau` with a
   computable metric and valid market at `s`, including non-executable and
   non-event names. Missing market is explicitly `market_unavailable`. Market
   denominators, metric, percentile, session, exclusion and reason are stored
   for each event/filter. Ties use maximum rank. Invalid events stay in the
   full monthly denominator and cannot be control removal candidates.
5. Sum removed event excess returns in canonical event order, divide by all
   baseline events in that entry month, and negate. Do not rerun holding overlap
   or selection. Keep zero-removal event months; never manufacture empty months.
   Level diagnostics use equal-weight event means within nonempty baskets,
   followed by equal-weight month means; an empty remaining/removed basket is
   reported as undefined, never zero.
6. Reuse EV001's calendar-month Bartlett NW(3) via `av001.newey_west` and the
   existing generalized CH001 Holm helper with exactly two variants. Required
   counts/statistics, cost x1.5, control share and strict t thresholds follow the
   CH004 YAML. The two rows being published, and all previous CH004 rows
   including aborted attempts, count toward the threshold.
7. Each baseline/loss rule/variant control calculation creates one RNG with
   seed 0 and consumes the full 200-path stream. `choice` runs for zero/all
   exclusions too. Drawn indices are sorted only for summation, after sampling,
   so actual/control ties use identical floating-point reduction order. The
   stream digest is checked between baseline and loss reruns; both independently
   recompute controls, Holm and verdicts. S/O combination reuses EV001's AV001
   precedence, without adding `risk_only` or a new verdict.
8. All YAML secondary metrics are present: remaining/removed monthly levels;
   exclusion, computability/reason and removal-month counts; filter overlap and
   CH001 relationship; full/remaining gross paths at 1/5/10/20 sessions; v1,
   v2 x1/1.5/2 and delisting sensitivity; year, short-sale regime, entry market
   and prior-20-session ADV bucket splits. Paths use the unchanged executable
   EV001 benchmark. Any unavailable benchmark member disables the entire
   date/horizon report, with reasons, rather than shrinking the benchmark.
   2025 sign is diagnostic; nothing selects a preferred variant.
9. Hash all EV001 sources plus CH001/D001/CH004 code, the three pinned plans,
   prerequisite plans, README and DATA_ISSUES before preparation/evaluation.
   Re-inventory and rehash at completion before publication. Source price hashes
   and evaluation reads stop at 2025-12-30. The bounded supplement check may
   inventory 2026 paths, but never opens their contents. Copied frozen protected
   and nonprotected 2026 hashes are separately labelled, not claimed as newly
   verified evaluation data. Nonprotected 2026 content-hash access is empty.
10. Hold one directory execution lock across preflight, hash freezing,
    preparation, CH004 trial counting, evaluation and publication. After hashes
    are fixed, `ordering.design_attempt` records two compact `design_aborted`
    rows on an exception, then propagates it. Before-hash errors write no row.
    Validate both compact rows and finite full JSON before writing results or
    appending either successful row. The shared append API has no OS-level
    rollback across two appends; the runner does not claim that guarantee.

Abort accounting depends on the shared fix `13bfb85`: `ordering.design_attempt`
captures the verified registration before evaluation, and
`experiment_registry.record_aborted_trial` verifies its committed path/blob
against Git history even if the working-tree preregistration changes mid-run.
The CH001–CH004 fixtures use `tests/preregistration_fixtures.py` to create local,
disposable Git clones with real preregistration history. They assert exactly one
`design_aborted` row per variant against the captured registration, no rows for
errors before source hashes, and preserved abort recording after a mid-run
working-tree preregistration change. Registry/results writes stay in `/tmp`.

The P3 integration test injects `NaN`, `-1`, and `inf` into source `ret_1d` on the
public session, before actual EV001 preparation. An `inf` stops in
`EventPrices.finish`; `-1` with valid close/base zeroes the adjusted series and
stops baseline outcomes with an undefined adjusted open-to-close return. Neither
path reaches chart calculation or successful publication; both record two abort
rows after hashes. With `NaN` and valid close/base, EV001's existing close/base
fallback permits a finite baseline while the stored raw return remains invalid
for both charts. With `-1` and a missing close on that pre-entry session, EV001's
existing invalid-price factor handling also leaves the later baseline finite.
In these two chart-only cases both S/O replay assertions pass, the event remains
in the monthly denominator, both filters report `invalid_return`, and neither
removes it. These are existing EV001 behaviors, not new filling logic in CH004.

## Verification

The requested regression command uses these environment settings:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_experiments_ch001.py tests/test_experiments_ch002.py \
  tests/test_experiments_ch003.py tests/test_experiments_ch004.py \
  tests/test_experiments_ordering.py \
  tests/test_backtesting_experiment_registry.py
```

The CH004 complete-pipeline fixtures run the actual EV001 preparation and
accounting with small synthetic financial/listing/price data. One fixture writes
daily synthetic price archives, including frozen 2026-01, 2026-03, 2026-06 and
2026-07 files, then installs traps on both `builtins.open` and `io.open` before
the actual bounded loader, hash freeze/reverification, evaluation and dry-run
paths. Any protected price-file open, other out-of-bound price-file open, private
environment-file open or data write fails the test. These paths complete with
zero protected opens. The suite's autouse socket/DNS guards prohibit network.

Other fixtures check exact boundary ties, inclusive/invalid first returns,
missing/unverified sessions, zero volume, overflow, pre-entry rank populations,
market-at-s changes, full S/O replay order and deliberate mismatch failures,
golden 200-path random streams including zero/all removals, actual calendar
gaps in NW, all observation gates, the 20/21-trial boundary, loss-control verdict
changes, missing benchmark-member diagnostics, deterministic replay, input
mutation, lock contention, abort accounting and all-row publication validation.

The follow-up six-file command above passed **424 tests in 534.77 seconds
(8m 54s)**, with no failures or skips. It ran serially with the listed offline
environment settings, bytecode and pytest cache writes disabled. The full pytest
log is `/tmp/ch_fix3_pytest.log`. The earlier seven-file **494 passed / 193.18 seconds** and
CH004-only **67 passed / 40.47 seconds** figures describe the original runner
validation, not this follow-up. Whitespace checks pass, all 15 documented
interpretations match the source, and CH001–CH003/EV001/ingestion sources remain
unchanged. No commit, network access, environment-file read, real-data execution,
or EV001 saved-result read was performed.

Final reviewed source SHA-256 (this note excluded to avoid self-reference):

- `backtesting/experiments/ch004.py`:
  `88fd995362211a6d578fc026e8fdef577115c6d6bc86916ba3b6cb685813904b`
- `tests/test_experiments_ch004.py`:
  `7e2bb20854645ae838f5bc40e78a75b7f3d52f656e3ad297247c58dff549529b`

## Fixture performance and full-run estimate

A fresh Python process installed audit hooks rejecting real `data/`, `.env`,
`.env-ai`, socket and DNS access, then ran only `fixture_timing()`. All prices
were synthetic, on actual XKRX session dates so v2 settlement dates were valid.
Measured on this machine while the other experiment remained running:

| Fixture work | Size | Seconds |
| --- | --- | ---: |
| Both chart filters | 64 names, one event cutoff | 0.00474 |
| Four-scenario EV001 outcomes | 64 names x 12 entry dates | 0.97497 |
| Gross paths, all four horizons | Same name/entry-date units | 0.12326 |
| One 200-path removal control | 64 events x 12 event months | 0.03675 |

Total fixture elapsed time, including book construction and calendar setup, was
3.501 seconds; process peak RSS was 99.46 MiB. The compact book's retained arrays
used 1.355 MiB for 64 names x 300 sessions (74 bytes per name/session). These are
fixture measurements, not measured full-data preparation or execution.

Let `T_EV`/`R_EV` be the elapsed time/peak MiB of the unchanged, single EV001
S/O preparation. Let `U` be the sum of full `V_tau` sizes over distinct entry
dates in both final rule samples, `C` the sum over distinct chart cutoffs, and
`G` the sum of event-month counts across both rules. A fixture-based point
estimate is:

```text
seconds ~= T_EV + 0.00007403*C + 0.00269949*U + 0.01225147*G
peak MiB ~= R_EV + 240*U/1048576 + 512
```

Thus for 100,000 universe/date units, 100,000 chart/name units and 206 rule-months,
the incremental estimate is about 4.7 minutes and 535 MiB on top of EV001
preparation. This includes both exact baseline computations, all four gross
horizons and baseline/loss controls for both variants. The memory reserve covers
outcome tables, event metadata and serialization; it is a heuristic, not a bound.
No extra full chart-price frame is loaded. The 7,000,000,000-byte peak-RSS guard
is retained through EV001 construction/evaluation checks.

The authorized documents do not provide a successful measured full EV001
preparation elapsed/RSS: its committed notes record a blocked real-data dry run
and synthetic timings. That missing measurement was requested separately; no
number is invented or inferred from EV001 performance results. Consequently a
single absolute full-data runtime/RSS cannot be substantiated here. The
implemented dry-run projection adds its own measured preparation/chart cost to
these fixture-scaled units, with a heuristic 1x..3x runtime range. It was tested
on fixtures only. Full-data hashing/serialization overhead and full-data memory
compliance remain unmeasured; no heavy dry run was launched to resolve them.

## Execution provenance

Implementation and fixture validation use the git worktree
`/home/are01/src/HQA_Project/HQA_ch001`, branch `claude/ch001-runner`, as in the
CH001 notes. This worktree has no `.env`/`.env-ai`; the credential preflight checks
existence and refuses a checkout containing either, without reading it.
No real design/validation trial was executed during this task. All test result
and registry writes were confined to temporary fixture repositories under `/tmp`.

For an eventual separately authorized execution in this worktree, trial rows
would append to its `research/experiments/registry.csv`, and results would be
written under its `research/experiments/CH004_event_chart_filters/results/`.
Data is accessed through the existing read-only symlinks to the main checkout's
`data/` subdirectories. The execution lock is this worktree directory's lock;
CH004 uses no holdout or ledger. A later integration with
`claude/strategy-research-system` must preserve registry history as in CH001:
existing rows first, then CH rows, every row kept byte-for-byte. No merge,
registry union or execution was performed here.

## Interpretations recorded in every result

1. Exploratory event-selection information, not an executable replacement portfolio or demonstrated profitability. Removed slots earn the unchanged universe benchmark without additional costs. Forward reproduction requires a new ID; no holdout is used.
2. EV001 prepare/select_events/outcome_tables/observations supply both rules' final ev_new20 sets, pre-entry-feasibility V_tau and all four cost scenarios, including their -100% delisting counterparts. These are copied and recomputed/asserted before any chart filter; overlap selection is never rerun after filtering.
3. EV001 source is restricted to the two approved SHA-256 versions at 08079e2 and f8c6845; the used hash is recorded in provenance. CH004, EV001 (7acd752) and CH001 (b53485e) preregistrations are immutable and content-pinned. No EV001 saved results or EV001 registry verdicts are consulted.
4. s is the last XKRX session <= public_date d. CH001 adjusted_values applies to exactly 252/21 session-aligned stored returns, including a valid first return, without price substitutes or filling. EV001's compact book does not distinguish an absent stock session from an unverified one; that combined invalidity is reported as unverified_or_missing_session.
5. Ranks use the full frozen V_tau, before executable-entry restriction, and each stock's market at s. Only valid metrics with KOSPI/KOSDAQ at s enter market-specific max-tie ranks. Missing/out-of-scope markets are market_unavailable; uncomputable events stay and cannot be randomly removed.
6. Anchor removes percentile<=.30 of A_s/max(A_252); runup removes percentile>.80 of A_s/A_(s-20)-1. Runup includes d's return when d is a session. Anchor shares CH001 C_anchor's percentile rule but uses event-date s and EV001 V_tau rather than CH001's decision-date U/B.
7. d_m is minus the canonically ordered sum of removed x_i divided by every E_r event in that entry month. Months without removals remain zero observations; months without events are absent. Remaining/removed level summaries omit empty baskets and report their monthly means separately.
8. Each rule/variant starts one default_rng(0) outside the 200-path loop; draws traverse path then ascending entry month over evaluable events sorted by entry date, stock code and event quarter. choice is called even for zero/all removals. Sorting drawn indices before summation makes exact ties count as >=. Loss reruns reproduce the draw digest.
9. Calendar-month Bartlett NW(3), no finite-sample correction, and the normal upper tail reuse EV001's monthly inference. Holm always has two variants per rule; insufficient/unavailable variants supply p=1. At least 300 events, 36 event months and 36 removal months plus defined required statistics are necessary. Strict t>2 becomes t>3 when existing CH004 rows plus these two rows exceed 20.
10. Costs are EV001's separately priced buy/sell v2 x1/1.5/2 and v1 x1, never a multiplier on x_i or sell tax. Positive x1.5 d is required but is mechanically implied by the fixed-set replacement interpretation, not evidence of strategy cost resilience.
11. Both S and O must pass. EV001's unavailable O sensitivity and listing-supplement failure remain sensitivity_unavailable/data_incomplete. Last-close and -100% scenarios independently rerun controls and Holm; a changed per-rule verdict is delisting_sensitive. Combined precedence reuses AV001.
12. All YAML secondary metrics are reporting only: remaining/removed monthly excess levels, computability/reasons, overlap, 1/5/10/20-session gross excess paths, v1/cost/delisting sensitivity, year/short-sale-regime/entry-market/liquidity splits and 2025 sign. A missing gross-path benchmark member disables the entire date/horizon diagnostic without shrinking V_tau's executable benchmark.
13. All evaluation price reads and hashes stop at 2025-12-30. Listing metadata and L may include later filings. Bounded supplement verification inventories later price files but does not open them; frozen 2026 hashes are labelled separately and are not independently reverified or evaluation inputs.
14. Execution holds ordering.execution_lock over preflight, hashes, preparation, counting, judgement and publication. Hashes are frozen before evaluation and reinventoried before publication; exceptions after freezing record two design_aborted rows. Successful compact rows and the full finite JSON are all validated before writes. The shared registry API cannot roll back an OS failure between appends.
15. Dry run prepares EV001 selections and chart computability only, never event outcomes, inference or publication. Fixture timing and scaled runtime/RSS are estimates, not full-data execution measurements. The unchanged cost dependency is protected by refusing a code checkout containing .env/.env-ai, without reading either file.
