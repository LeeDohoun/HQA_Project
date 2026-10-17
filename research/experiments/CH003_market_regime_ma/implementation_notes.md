# CH003 implementation notes

Immutable own plan: commit `1a7035989731b92b6e475f75ae27ef39d915edfa`, SHA-256
`6a1f4e29c19cc0576dd17d47b60760b854c11590ff6134228b7c5586ed987dda`. Referenced CH001: commit `b53485e` and the pinned hash below.

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
- CH003's full 103-month fixture changes exactly one name/cohort to -100%.
  Actual monthly d remains unchanged because the held loss cancels against B;
  some joint circular shifts avoid the loss. Expected shift means, control gate,
  all three NW lag p-values and combined `delisting_sensitive` are asserted
  through real computation. No Holm correction is added: CH003 preregisters a
  single variant and NW(1,6,12) gates. `variant_metrics` is not mocked.
  Another test replaces the index file after loading and confirms abortion
  retains its original index hash digest without publication.

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

1. Historical reanalysis using latest available_at per exact broad-market series/name/day. Availability is a saved-version timestamp, never a contemporaneous disclosure gate. All versions at the same timestamp must agree in close, including superseded versions. 2026 index rows are counted but excluded from every MA input.
2. MA200 uses the decision-inclusive 200 contiguous XKRX sessions with finite positive closes. A missing/invalid close disables that market's filter for reporting; any uncomputable market-month in fixed T makes primary insufficient. Equality to MA is not bearish.
3. B equal weights are fixed before entry. Bearish-market slots become zero-return/zero-cost cash; other slots retain 1/|B|. An active month needs at least one would-be-filled B name changed to cash; already-unfilled slots do not count.
4. T is exactly 2017-05..2025-11, 103 months. No gaps are dropped or compressed in primary statistics or shifted paths. Empty B with nonempty U is a valid zero; an empty U also makes the primary unavailable. Filter-off diagnostics may still be reported on valid months.
5. Every nonzero positive np.roll shift moves the two-market vector jointly, preserving contemporaneous joint bearish months. The share count/(N-1) is not a rank-test p-value; (count+1)/N includes the actual path and is reported separately. Circular wrapping creates an artificial end/start spell.
6. Auxiliary independent-month controls use one default_rng(0) per rule, path then KOSPI then KOSDAQ, drawing k computable month positions without replacement even at k=0/all. Diagnostic only; loss reruns reproduce the same draws.
7. NW uses Bartlett lags 1,6,12, divisors N, no finite-sample correction and normal upper tails. All three must meet strict t>2 (3 after 20 trials) and p<=.05; no lag selection. 36 valid AND 36 active months are required; generic 300 events adds no gate.
8. Risk-only requires nonpositive mean d, lower sample volatility, higher worst-ceil(5%*N) mean and lower stitched maximum drawdown. Year/ban splits use decision dates; market and P diagnostics rerun fixed-slot cash filtering within their own equal-weight baskets.
9. HF001 entry-date full round-trip v1 accounting and its delisting-loss costs are retained; no CH002 one-way accounting is used. x1.5 must have positive d. S and O must both pass; version/sensitivity/delisting/coverage precedence reuses CH001/AV001.
10. Spells are linear contiguous bearish runs on T, not circular, with missing regimes breaking runs. Active-month baseline mean is the full B return in those months, not a conditional sub-basket return.
11. A pass is approximate inference meeting three lag criteria, not proof of enough independent bearish events or alpha. 2025 is a stability diagnostic and forward reproduction needs a new ID.
12. Dry run audits real index computability, bounded price-file coverage and listing/date metadata without real portfolio evaluation, publication or holdout. Synthetic performance estimates are not measured execution bounds; 2026 supplement integrity hashes are not evaluation inputs.

## CH003 implementation details

- The benchmark loader is independent of runtime market_context_data; it never
  applies available_at<=as_of. Only the exact broad-market series/name pairs are
  candidates. All relevant saved timestamp versions are checked for conflicts,
  even if a later version supersedes them. Equal numeric closes at equal aware
  instants are allowed; equivalent timezone representations identify one instant.
- Rows after 2025-11-30, including all 2026 index rows, are read and counted but
  never become MA inputs. The MA itself is restricted to the 200 XKRX sessions
  ending at each actual decision, not to 200 available observations. Negative,
  zero, missing and nonfinite closes disable only that market for reporting.
- B's 1/|B| weights never change when a market goes to cash. U and P reference
  metrics retain their own HF001 selected-slot accounting. Active-month B mean
  is the full B portfolio's return in active months, not a selected-market mean.
- Execution rejects any changed/dropped/reordered calendar T. Both markets and
  U must be computable/valid throughout it. If this is not so, primary/NW are
  unavailable and judgement is insufficient. The registered missing-market-off
  calculations, full-length shifts and auxiliary results remain diagnostics;
  they do not create primary observations by compressing the missing month.
- The joint shift orientation is positive np.roll(..., shift, axis=0), enumerating
  every s=1..N-1. This includes the same set of shifts as negative orientation.
  For exact tie handling, actual and controls calculate filtered and baseline
  sorted-slot means using the same reduction, then subtract. Every shifted
  path stores its N-month mean, all three NW statistics and actual active count.
- Primary control share uses count/(N-1) with >= ties and no plus-one change.
  The separate self-inclusive (count+1)/N is labelled a rank share, not substituted
  into the gate. Auxiliary draws use ascending computable month positions and
  separate market counts, KOSPI first, including zero/all choices. They never
  affect the verdict and loss reruns assert an identical draw digest. Empty
  choice results are cast to integer indices without extra RNG calls, including
  a market with zero computable months; that case remains primary insufficient.
- NW(6/12) uses weights 1-h/(L+1), including all available pairs up to L, covariance
  and mean-variance divisors N, no finite-sample correction and the normal upper
  tail. Lag 1 is byte-for-byte HF001 arithmetic. All three strict t/p gates apply;
  no Holm correction across lags, event-count gate, or selected best lag is added.
- Linear spell lengths are reported in calendar order; missing regimes break a
  spell. Circular-control wrap-around is explicitly artificial. 36 active months
  cannot establish 36 independent bearish events. Even a pass is approximate
  inference meeting all three lag criteria, not proof of alpha or event sufficiency.
- CH003 risk_only requires improvement in all three registered risk diagnostics:
  volatility, worst-5% mean and maximum drawdown, while primary mean d<=0.
  Missing observations/statistics override this label as insufficient.

Hand fixtures cover latest/backfilled version selection and equivalent-time
conflicts (including superseded versions), duplicate identical versions, timezone
validation, exact 200-session bounds and no future input, equality to MA, every
invalid close, non-redistributed weights and unfilled-slot activity, both-market
cash, hand-calculated joint circular path means and self-inclusive share,
linear/joint bearish spells, zero/all/empty-candidate auxiliary draws, NW long-run variances
25/16, 17/28 and 17/52, isolated failures at each lag (1, 6, 12) with all other
lags passing: t below/equal to both trial-count thresholds (2 and 3), p above
0.05, and undefined standard error. Risk-only fixtures require strictly improved
maximum drawdown: equality or worsening fails with mean d<=0 even when volatility
and lower-5% mean improve. Fixtures also cover a missing market-month inside all 103
months, delisting rejudgement, unavailable O, exact one-row publication, batch
threshold crossing, provenance and oversized-row rejection before any write.

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

Compact complete evidence (full JSON under `/tmp/ch003-dry-plan.json`):

```json
{
  "experiment_id": "CH003_market_regime_ma",
  "prereg_commit": "1a7035989731b92b6e475f75ae27ef39d915edfa",
  "preregistration_sha256": "6a1f4e29c19cc0576dd17d47b60760b854c11590ff6134228b7c5586ed987dda",
  "reference_sha256": "80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f",
  "planned_months": 103,
  "price_files": 2357,
  "read_start": "2016-05-25",
  "read_end": "2025-12-30",
  "price_covered_months": 103,
  "listing_incomplete_months": 0,
  "L": 17,
  "fixture_timing": {
    "stocks": 128,
    "months": 103,
    "circular_paths": 102,
    "auxiliary_paths": 200,
    "circular_seconds": 0.3766612609997537,
    "auxiliary_seconds": 0.6343362440002238
  },
  "projection": {
    "measured_dry_run_seconds": 61.846010666999064,
    "measured_peak_rss_mib": 1016.95703125,
    "assumed_price_financial_preparation_seconds": 1800.0,
    "full_run_seconds_range": [
      2390.141801169397,
      8070.42540350819
    ],
    "peak_rss_mib_estimate": 5112.95703125,
    "basis": "Synthetic 103-month circular/auxiliary controls scaled from 128 to 4000 names, 2 rules and 2 valuations; synthetic HF001 entries scaled to U cohorts. Price loading/financial derivation has an assumed 1800s budget, not measured here. Add 4 GiB price/results reserve; heuristic only."
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
      "backtesting/experiments/ch003.py": "ac95c31a082d4e495813dc7ee68b698e0f595823d31e4415f1492e0fead26ca4",
      "backtesting/experiments/ch001.py": "29ea0a9253a28f53388b0055513f02e365a4925bbf4aaacf5da519c315383585"
    },
    "registry_sha256_unchanged": "5c066a4adf36c9ecef5f6780d83f97d72b224a8fb8c629e85c9e5a25eefa9b9a"
  },
  "listing_supplement_complete": true,
  "listing_target_companies": 464,
  "O_link_sensitivity_available": true,
  "protected_frozen_hashes_only": 120,
  "index_inventory": {
    "selected_rows": 5358,
    "versions_checked": 5358,
    "ignored": {
      "other_series_or_name": 242265,
      "after_final_decision": 410,
      "2026_rows_not_used": 368
    },
    "sha256": "85d63905c102785d1b27e653e0739a6a6bf7208de53cc283b6959e093ed37286"
  },
  "regime_summary": {
    "KOSPI": {
      "bearish_months": 50,
      "uncomputable_months": 0,
      "spell_count": 8,
      "spell_lengths": [
        1,
        11,
        7,
        3,
        17,
        2,
        1,
        8
      ],
      "max_spell_length": 17
    },
    "KOSDAQ": {
      "bearish_months": 50,
      "uncomputable_months": 0,
      "spell_count": 7,
      "spell_lengths": [
        9,
        11,
        2,
        13,
        2,
        1,
        12
      ],
      "max_spell_length": 13
    },
    "joint_bearish_months": 43
  },
  "primary_data_complete": true
}
```

Measured independent-process resources (`/tmp/ch003-dry-resources.txt`):

```text
Elapsed (wall clock) time (h:mm:ss or m:ss): 1:03.80
Maximum resident set size (kbytes): 1041364
Swaps: 0
Exit status: 0
```

The actual repository registry SHA-256 is unchanged:
`5c066a4adf36c9ecef5f6780d83f97d72b224a8fb8c629e85c9e5a25eefa9b9a`.
Both runners' referenced/source document hashes remain those above. The final
code hashes are included in the audit evidence; notes are excluded to avoid
self-reference. No real-data verdict, registry row or results file was created.
