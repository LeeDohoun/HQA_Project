# CH002 implementation notes

Immutable own plan: commit `1a7035989731b92b6e475f75ae27ef39d915edfa`, SHA-256
`adf3d02c47a2feebb3ba54f129f22095782aed8fc4c982326c3641e5c5ee14b7`. Referenced CH001: commit `b53485e` and the pinned hash below.

## Review fixes, 2026-10-17

This section describes the current corrections after the strategy-research merge
(including `fbcf370`). Earlier code hashes, HEAD/registration-state descriptions,
dry-run output and timings below are historical implementation evidence; those
real-data audits were not rerun in this correction session. All three committed
preregistrations are now present and remain unchanged.

- `run_experiment` now nests `ordering.design_attempt` inside
  `ordering.execution_lock(repo_root)`, following AV001. The same `repo_root`
  owns the lock, trial-count read, judgement, result publication and registry
  writes. Exceptions after the complete preflight hash snapshot record exactly
  one `design_aborted` row per registered variant, with one shared run ID and
  the original category SHA-256 digests. Exceptions before that snapshot record
  nothing. This deliberately includes preparation failures after preflight,
  even before portfolio evaluation. OS-level partial publication/append rollback
  is still not guaranteed by the existing APIs.
- Source/code hashes are fixed before price/financial preparation and evaluation,
  then checked again immediately before publication. The bounded price-file
  inventory is enumerated again, so additions/removals also change the snapshot.
  CH003 includes its benchmark file before index loading. A mismatch raises and
  records an aborted attempt without publishing a verdict. Neither pass reads
  protected January-June 2026 price contents; supplement integrity access
  retains its established distinction between frozen protected hashes and
  nonprotected integrity hashes.
- The shared wick guard now uses `abs(base - previous) > 0.005 * previous` for
  positive prices. Exact +0.5% and -0.5% pass, including 10050/10000 and
  9950/10000; both just-outside cases fail. No tolerance or criterion change
  was introduced. CH002/CH003 retain their own registered signal rules.
- Synthetic tests inject a failure after real evaluation and before hash
  completion; replace an actual financial input during preparation/evaluation;
  simulate a changed code digest; and verify the count, thresholds, publication
  and appends are all inside the actual directory lock. A preceding trial added
  on lock entry forces total count 21 and t threshold 3 rather than 2.
- CH002's 40-cohort fixture uses real forward accounting, request discovery,
  exits, costs, monthly statistics, 200 controls and Holm(2). Exactly one final
  position is presumed delisted: the strategy holds it, while some controls
  sell it before disappearance. Monthly d remains unchanged, but the control
  means and gate change pass to fail, producing combined `delisting_sensitive`.
  Expected counterfactual means include sell-date tax differences across years.
  `variant_metrics` is not mocked.

Real execution must happen in the main checkout
`/home/are01/src/HQA_Project/HQA_Project` after merge, recording into that same
checkout. This shares the directory lock and registry with other research runs;
separate worktree roots do not share directory locks.

Only the three CH runners, their three test modules and these three notes were
changed. No `.env` reads, network, real-data execution, data/venv writes, holdout
claims, real registry/result writes or commits were performed. Synthetic result
and registry writes are confined to pytest temporary repositories.

Offline validation command (the requested registry filename does not exist;
its actual module is `tests/test_backtesting_experiment_registry.py`). The shared
ordering suite is included because these runners now use its execution contract.

```bash
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q \
  tests/test_experiments_ch001.py tests/test_experiments_ch002.py \
  tests/test_experiments_ch003.py tests/test_experiments_av001.py \
  tests/test_experiments_ev001.py tests/test_experiments_hf001.py \
  tests/test_backtesting_experiment_registry.py tests/test_experiments_ordering.py
```

Result: **539 passed, 1 skipped in 226.19s (0:03:46)**, including 26 new
regression cases. The skip is the pre-existing optional historical HC003 archive
comparison (`RUN_AV001_HC003_ARCHIVE_CHECK` was not enabled).

The real registry before/after SHA-256 is unchanged:
`fae470d99bb14ff63521540ae531afc8612937f6de8a496dc2c03138013d2fcc`.
The three preregistration SHA-256 values also match the pre-correction snapshot.

## Scope and registration state

Worktree `/home/are01/src/HQA_Project/HQA_ch001`, branch `claude/ch001-runner`.
The supplied AGENTS.md instructions were read; this worktree has no root AGENTS.md.
The relevant parent/main-checkout instructions, experiment README and DATA_ISSUES
were also read. The user-specified file boundary overrides broader workspace
collaboration-file workflows. No agents, network, environment-file reads,
collectors, commits, real-data `--execute`, holdout claims, data writes, registry
appends or result publications were used in this implementation session.
Synthetic executions publish only into pytest temporary directories.

At the original implementation step, HEAD was
`b53485ec62edaf5f24ccc9cc6518dbb9ce0c8193`. The two preregistrations existed in
the local `1a7035989731b92b6e475f75ae27ef39d915edfa` Git object but were absent
from this worktree. They were read using `git show`; neither was restored,
modified or recommitted. Normal worktree CLI dry runs then exited 1 with
`ValueError: preregistration must be a regular file inside repo_root`.
The subsequent merge supplied those immutable documents; this historical
pre-merge failure does not describe the current checkout. Runtime verification
remains unchanged.

For the real-data dry-run validation only, a local shared clone under
`/tmp/hqa-ch23-registration.Znkjih` was checked out at existing commit `1a70359`,
with sparse checkout restricted to immutable preregistrations and commit_map.
No new commit was made. The runner code still came from this worktree, and
`data_dir` pointed at this worktree's actual read-only data symlinks. The normal
registration verifier ran against that committed snapshot. Thus snapshot dry-run
success does not claim the current worktree's missing registration is fixed.

Both runners hash the reference CH001 document before other registration/data
work and recheck it before publication. It must equal
`80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f`;
a mismatch is fatal. Each own registration uses the unchanged immutable-history
verifier. Both document hashes appear in results. A missing or incompatible
required input stops; the registered raw-price, ADV and filter-off policies are
the only such substitutions.

## Exact CH001 changes

1. Extract `adjusted_values(returns, statuses, present, length=...)` from
   `adjusted_index`; the original function delegates to it with identical arrays,
   reason precedence, first-return validation and A arithmetic. CH002 can reuse
   A efficiently on one stock's aligned 11-session arrays.
2. Add keyword `prepare_inputs(..., attach_charts=True)`. The default still runs
   the same three CH001 filters. With `False`, B/P are constructed using the same
   `baseline_set` assertion and unnecessary CH001 chart filters are omitted.
   The single AV001 preparation still supplies both S/O universes and provenance.
3. Add `newey_west(values, lag=1)`. Lag 1 delegates unchanged to HF001; the optional
   longer lags use the same Bartlett/divisor-N/normal-tail convention for CH003.
   Add the standard-library `math` import solely for this calculation.
4. Add keyword `holm(p_values, variants=VARIANTS)`. The default three-member path
   still delegates unchanged to HF001. The supplied-family path implements the
   identical step-down formula for CH002's exact two-member family, validating
   probabilities and rejecting a mismatched family.

No other CH001 source change was made. Reversing exactly these four changes in
memory reproduced the reviewed source SHA-256
`a627005ba983020378178ee726f6745b092140f44cc6bc89feeaf775169e2ebe`.
The original 761-line CH001 test prefix still hashes to
`4a09fe95874586566c847cdaa279ee5ac528568e11435405a0d68cc952205415`.
Only six parameterized access-trap cases were appended to that test module;
existing expectations were untouched. Its original 95 cases passed unchanged.

## Shared accounting, reporting and validation choices

- The CH001 loader, bounded calendar, AV001 S/O construction, `baseline_set`,
  `forward_observations`, filters-off assertion, summaries, source hashing,
  integrity-access inventory and 7 GB RSS check are reused by import.
  Public execution calls `prepare_inputs(attach_charts=False)` once. No AV001,
  HF001, HC002, common, cost_model, DART or registry implementation was edited.
- The same bounded price span starts with CH001's first 252-session warmup and
  ends at the last 2025 session. Latest stored stock/day rows preserve high,
  low, base_price and trading_value. No 2026 stock price is an evaluation input;
  injected 2026 spans are rejected before forward accounting or holdout claims.
- Empty U is invalid. Nonempty U with empty B is a valid cash month with d=0.
  Unfilled selected slots keep their pre-entry weight as cash. Subgroup
  diagnostics re-equal-weight within that subgroup and use nonempty subgroup U
  for validity. Year/ban splits use decision dates, with inclusive ban bounds
  2023-11-06..2025-03-30. These are diagnostics, not extra tests or selection rules.
- Lower-tail mean uses the worst ceil(5%*N) monthly returns, sample volatility
  uses ddof=1, and stitched drawdown starts at wealth 1 and stops at total loss.
  No annualization, ranking tolerance, rounding change, or 2025 sign gate is added.
- Both disclosure rules must pass; AV001's combined precedence is retained:
  both coverage-stopped -> data_incomplete; unavailable O sensitivity;
  delisting_sensitive; both pass; one-rule pass -> version_sensitive;
  data_incomplete/insufficient/risk_only; otherwise fail. Delisting comparisons
  occur only after complete per-rule control/statistical rejudgement.
- All source hashes use CH001's inventory plus the new runner and its own
  registration (and CH003's benchmark file). Code commit, Python/library versions,
  full version provenance, L, date differences, listing_version, selections,
  accounting and both disclosure-rule results are retained. Listing metadata
  after evaluation dates is provenance, not an evaluation price input.
- All finite compact rows, including a conservative result-filename allowance,
  are validated against the 32 KiB registry limit before any result is created.
  The shared exclusive result writer compresses JSON above 50 MiB; record_trial
  appends exactly the fixed variant rows. The existing registry API cannot roll
  back an OS-level partial append failure. No atomic-batch guarantee is invented.
- Dry run audits real file/session coverage and financial-date metadata with
  `derive=False`; it does not construct real U/P/B, evaluate real signals or
  forward returns, judge or publish. Synthetic fixtures exercise real accounting
  and controls. AV001's full preparation, used only on execution, still builds
  its unused PriceBook before CH001 releases it, exactly as in CH001.
- Runtime projections assume 4,000 names per rule/month, a separate 1,800-second
  price/financial-preparation allowance, and a 4 GiB memory reserve on top of
  measured metadata dry-run RSS. The allowance and 3x-plus-900-second upper
  scaling are planning assumptions, not measured bounds or confidence intervals.
  Real B may be much smaller; full preparation, serialization and full-run peak
  memory remain unmeasured because real execution was forbidden.

## Inherited data limits and scope

S uses actual saved-version public dates and O assigns the same latest corrected
numbers to assumed original public dates; O does not restore the original
financial statements. Both retain the actual-public-date cutoff before 2026.
Current industry classifications and latest backfilled KRX revisions do not
reconstruct historical vintages. W/A use stored rounded ret_1d unchanged; their
rounding effect is unmeasured. No missing return is filled with zero or replaced
by close/base-1 for signals. Registered HF001/early-sale raw substitutions remain
explicitly counted for valuations only.

CH002 tests close confirmation followed by a later sellable open, with the same
20-session maturity. It neither tests intraday stop orders nor extends holdings
or establishes reduced turnover. Sell-date v1 costs approximate tax, spread and
ADV-bucket slippage; they do not reconstruct actual order-book impact. CH003
uses price indices without dividends solely for its signal, and tests reduced
market exposure, not alpha. These are follow-up hypotheses on previously seen
periods; a forward reproduction requires a new preregistered ID.

## Runner interpretations (also retained in result JSON)

1. Historical follow-up improvement over B, not standalone profitability; 2025 sign is diagnostic and no holdout is used.
2. W1 uses verified entry-session close/open, not the unused entry-session return. Later W requires verified finite ret>-1; invalid W is permanent. Low10 independently rebuilds the CH001 11-session A window and resumes after gaps.
3. Signals use closes on days 1..19 including W0 in the peak. Requests are irrevocable; day k+1 is the first attempt, including day 20. A day-20 open sale is an early fill relative to the scheduled close.
4. Only NaN/missing bases use the preceding session close, exactly as HF001. An otherwise tradable requested open without a finite positive reference is an error. Raw open/entry fallback is allowed only when prior W is uncomputable.
5. Primary costs are v1 buy at entry and sell at actual realization date/price/market; maturity uses HF001 exit_date and that date's raw close. ADV_sell uses exactly the preceding 20 sessions, excludes the sale date, includes zeros; any negative value stops even if another value is missing. Missing/nonfinite ADV becomes 0 and is counted per realized position.
6. Primary delisting loss is exactly -1 without added costs for maturity valuations only; successful early sales retain realized returns. HF001 round-trip d is diagnostic only and retains HF001's full-cost loss convention.
7. The selected B denominator is fixed before entry; unfilled slots stay cash. Cash days count sale-open day through day 20 (21-j); unfilled entries contribute 20. Avoided post-sale gross return is G_maturity/G_sale-1, with both registered fallback policies retained.
8. Controls condition on actual first-attempt days, not fill days. One RNG per rule/variant, path then month, choice over sorted filled B followed by permutation(m), including m=0. Loss repeats the same draw stream; controls test name/date assignment, not market-wide timing.
9. 36 valid U months AND 36 months with an actual early fill; generic 300 events is not an extra gate. Empty B in nonempty U is a valid cash zero; empty U is invalid. Holm family remains exactly two with undefined/insufficient p=1.
10. Risk-only requires nonpositive d, lower sample volatility and improved worst-ceil(5%*N) mean. Drawdown starts at wealth 1. Market/liquidity diagnostics re-equal-weight inside the subset; year/ban splits use decision date.
11. S/O, coverage stops and combined verdict precedence reuse CH001/AV001. All finite registry rows are validated before publication; record_trial does not provide rollback after an OS append failure.
12. Dry run inventories real bounded price files, listing coverage and financial-date metadata, without loading evaluation prices or calculating real holdings, signals or outcomes. Synthetic timings are projections only. Nonprotected 2026 integrity hashes are distinct from evaluation input; protected price contents are never opened.

## CH002 implementation details

- Maturity gross and entry eligibility come directly from HF001; B selection and
  disabled-exit names/weights/cash/cost/net equivalence use CH001's assertion.
  The disabled-exit multiple is explicitly asserted equal to HF001 gross+1.
- W is computed in causal order. W1 does not use ret_1d_1; it does require
  verified calendar status and positive finite close. Later invalid W never
  recovers. Permanent suffix days have `after_<cause>` reason labels; after a
  request no further signal is examined. Low10 rebuilds A on each independent
  window, validates the first return too, and accepts zero-volume return days.
- First-attempt lists are formed in stock-code order of actual requests. The
  implementation samples their sorted filled-B positions, mapping back to
  codes: this is exactly the same NumPy draw stream as sampling that sorted code
  array. It then calls permutation(m). No draw is skipped at m=0 or m=all.
- Preparation evaluates baseline and actual requested exits only. Each drawn
  control code/first-attempt pair is priced on its first actual use, then cached
  for subsequent paths, both variants and loss sensitivity. An undrawn pair is
  never priced or validated; a defect in that unused counterfactual cannot stop
  the registered 200 paths. Request discovery is independent of these outcomes.
  Only the eight needed stock columns are retained in the aligned holding books.
- Empty choice results are explicitly converted to integer indices without any
  extra RNG call. This preserves choice/permutation calls for empty B or all
  unfilled entries. The fixture times a first cache-warming control run and a
  second identical cached run; their time difference estimates exit computation,
  separately from the cached control loop. Both use synthetic prices only.
- HF001's NaN-only base fallback is preserved; zero/infinite bases are not
  silently replaced. Calendar verification gates W/A but is not an added sell
  condition. A missing sell reference on an otherwise tradable requested open
  is fatal, matching HF001's reference-validation policy.
- `valuation_date` always retains HF001 exit_date, including early sales.
  `realization_date` is the sale date when filled, otherwise that valuation date;
  primary sell costs use realization_date and its raw open/close/market. The
  signal/request, first attempt, fill, scheduled close and valuation are distinct.
- ADV_sell does not add a calendar-status filter. Negative values (including
  negative infinity) are errors even if another observation is missing; any
  missing/nonfinite nonnegative observation triggers the registered ADV=0 policy.
  Actual strategy, baseline and control path incomplete-ADV counts are reported.
- Primary effective cost drag is G-1-net, reflecting the multiplicative formula.
  Delisting sensitivity uses exactly -1 with no added cost for unsold maturity
  positions. A pre-delisting early sale is not overwritten. The HF001 diagnostic
  retains its -1-minus-round-trip-cost convention and is never a robustness gate.
- Post-sale avoided gross return is G_maturity/G_sale-1 (negative means a loss was
  avoided); the inherited raw-price fallbacks remain visible. Cash exposure uses
  days j..20 for a sale at day-j open and all 20 days for an unfilled entry.
  These are slot/day weights, not a continuous marked-to-market portfolio.
- Control diagnostics retain every path's actual fill dates/counts, cash days,
  unfilled requests, raw substitutions and incomplete ADV, plus explicit mean
  control-minus-actual execution differences. Loss controls reset the RNG and
  must reproduce the draw SHA-256.
- The common summary's universe_net/universe_excess fields are null for CH002:
  its YAML does not request a U return benchmark, and no substitute is invented.
  Market/liquidity splits, year/ban, risk, execution dates and HF001 diagnostic d
  are reporting only; x1/x1.5/x2 primary one-way costs are retained in full.

Hand fixtures cover W0 peak equality, all invalid W conditions, 11-session
recovery independent of W, strict low10 comparison and pre-entry history,
zero-volume valid return days, day-19/day-20 boundaries, irrevocable delayed
limit-down/halt fills, raw fallback, unsold requests, presumed delisting and
pre-delisting sales, partial missing/zero/negative ADV, actual sale-date tax
across 2024/2025 and changed sale-price spread, unfilled B cash, zero/all/subset
request control draws, empty B/all-unfilled controls, unused counterfactuals,
exact ties, fixed Holm(2), every verdict gate, unavailable
O, exact two-row publication, batch threshold crossing, provenance and oversized
row rejection before any write.

## Tests

The final command (offline environment, no pytest/bytecode cache writes):

```bash
PYTHONDONTWRITEBYTECODE=1 OPENAI_API_KEY=offline-disabled \
OPENAI_BASE_URL=http://127.0.0.1:9/v1 LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_experiments_ch001.py tests/test_experiments_ch002.py \
  tests/test_experiments_ch003.py tests/test_experiments_av001.py \
  tests/test_experiments_hf001.py tests/test_experiments_hc002.py \
  tests/test_backtesting_experiment_registry.py
```

Result: **407 passed, 1 skipped in 143.33 seconds**. The skip is the existing
opt-in AV001/HC003 historical archive replay; no required test was skipped.
The new CH002/CH003 modules contribute 94 cases. The original CH001 suite
remains intact, with six new access-trap cases appended.

All three CH test modules have design and dry-run file-open traps using both
Path.open and builtins.open, including absent/stale frozen-scan metadata,
refreshed delisted-company references, and successful coverage. Protected
January-June 2026 prices are denied unconditionally. CH002/CH003 also assert
that provenance's evaluation prices and injected evaluation spans contain no
2026 date, including nonprotected July. Tests claim no holdout session.

## Real local-data dry-run output

The final-source registration-snapshot run completed successfully. A Python audit
hook rejected environment-file opens, network events, data writes and every
2026-01-01..2026-06-30 stock-price open. Real price loading/preparation/forward
entry points were additionally replaced with a failure trap; only the synthetic
fixture uses forward accounting. The unchanged supplement verifier may hash
nonprotected 2026 prices; its 67 files are integrity inputs only. The 120 protected
file hashes come only from frozen metadata. No performance outcome is inferred
from coverage, financial-date linkage or regime counts.

Compact complete evidence (full JSON under `/tmp/ch002-dry-plan.json`):

```json
{
  "experiment_id": "CH002_chart_exit_rules",
  "prereg_commit": "1a7035989731b92b6e475f75ae27ef39d915edfa",
  "preregistration_sha256": "adf3d02c47a2feebb3ba54f129f22095782aed8fc4c982326c3641e5c5ee14b7",
  "reference_sha256": "80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f",
  "planned_months": 103,
  "price_files": 2357,
  "read_start": "2016-05-25",
  "read_end": "2025-12-30",
  "price_covered_months": 103,
  "listing_incomplete_months": 0,
  "L": 17,
  "fixture_timing": {
    "stocks": 32,
    "forward_seconds": 0.021430761000374332,
    "exit_seconds": 0.403245326002434,
    "build_actual_exit_seconds": 0.1593761190015357,
    "cache_warm_control_seconds": 0.26177368800017575,
    "request_day_pairs_computed": 608,
    "control_seconds": 0.01790448099927744,
    "months": 1,
    "paths": 200
  },
  "projection": {
    "measured_dry_run_seconds": 56.87138499299908,
    "measured_peak_rss_mib": 1016.6953125,
    "assumed_price_financial_preparation_seconds": 1800.0,
    "full_run_seconds_range": [
      14636.44216824089,
      44809.326504722674
    ],
    "peak_rss_mib_estimate": 5112.6953125,
    "basis": "Synthetic table covering all 19 request days scaled to 4000 U/B names, 103 months and 2 rules; 8 loss/variant control evaluations. Price loading/financial derivation has an assumed 1800s budget, not measured here. Add 4 GiB prices/tables/serialization reserve; heuristic, not a measured bound."
  },
  "validation_context": {
    "registration_snapshot": "/tmp/hqa-ch23-registration.Znkjih",
    "protected_price_open_attempts": 0,
    "environment_open_attempts": 0,
    "data_write_attempts": 0,
    "network_attempts": 0,
    "evaluation_loader_calls": 0,
    "nonprotected_2026_integrity_files_opened": 67,
    "runner_source_sha256": {
      "backtesting/experiments/ch002.py": "f18c3d58031f496c8c95cfb58213041f69dfff5ff3865152b4b844a19d413585",
      "backtesting/experiments/ch001.py": "29ea0a9253a28f53388b0055513f02e365a4925bbf4aaacf5da519c315383585"
    },
    "registry_sha256_unchanged": "5c066a4adf36c9ecef5f6780d83f97d72b224a8fb8c629e85c9e5a25eefa9b9a"
  },
  "listing_supplement_complete": true,
  "listing_target_companies": 464,
  "O_link_sensitivity_available": true,
  "protected_frozen_hashes_only": 120
}
```

Measured independent-process resources (`/tmp/ch002-dry-resources.txt`):

```text
Elapsed (wall clock) time (h:mm:ss or m:ss): 0:58.86
Maximum resident set size (kbytes): 1041096
Swaps: 0
Exit status: 0
```

The actual repository registry SHA-256 is unchanged:
`5c066a4adf36c9ecef5f6780d83f97d72b224a8fb8c629e85c9e5a25eefa9b9a`.
Both runners' referenced/source document hashes remain those above. The final
code hashes are included in the audit evidence; notes are excluded to avoid
self-reference. No real-data verdict, registry row or results file was created.

## Execution provenance

Design/validation trials are executed from the git worktree
`/home/are01/src/HQA_Project/HQA_ch001` (branch `claude/ch001-runner`), not from the
main checkout, because the runner's credential preflight refuses a checkout that
contains `.env`/`.env-ai` and the worktree has none. The trial rows are appended to
that worktree's `research/experiments/registry.csv`; results are written under the
same worktree's `research/experiments/<ID>/results/`. Data is read through read-only
symlinks to the main checkout's `data/` subdirectories. The execution lock is the
worktree directory's lock; CH001-CH003 use no holdout and no ledger. After the runs,
`claude/strategy-research-system` is merged into this branch with a registry union
(existing rows first, then CH rows, every row kept byte-for-byte).
