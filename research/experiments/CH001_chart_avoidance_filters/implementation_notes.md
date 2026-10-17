# CH001 implementation notes

The immutable plan is `preregistration.md` at `b53485e`, SHA-256
`80646531a49d1620a5cbb3f12abd36dc61c2b25c2e0373395af8010d8439381f`.
Worktree: `/home/are01/src/HQA_Project/HQA_ch001`, branch `claude/ch001-runner`.
The supplied AGENTS.md instructions apply; no on-disk AGENTS.md was present at
the root or the relevant ancestor directories inside this worktree.

Only the runner, its test module and these notes are created. Existing experiment
code, registrations, registry, results, `.agents/` and data are not edited. The
relevant data subdirectories and `venv` resolve to the main checkout and remain
read-only. No `.env`, network, real `--execute`, holdout session or worktree commit
is used. Tests execute only synthetic experiments in temporary repositories.

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
- CH001's 40-month fixture changes exactly one retained name/cohort to -100%.
  It asserts hand-calculated monthly d and all 200 control means, identical RNG
  draws, the real Holm(3) values and pass-to-fail sensitivity, ending in the
  combined `delisting_sensitive` verdict. `variant_metrics` is not mocked.

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

## Implementation and interpretations

1. `av001.prepare(stage="design_validation")` is called once and supplies both
   rules' U, HC001-priority phase, F_vol and F_raise. Its unused HC003 PriceBook is
   discarded. CH001 does not reuse HC003 accounting or its adjusted-index
   substitutes. Every monthly B is independently constructed as phase-2 U minus
   the two avoidance sets and asserted equal to `av001.target_set("av_conc", ...,
   set())`, without any further phase/holding restriction.
2. HC002's month-end/next-session/20-session calendar supplies preparation dates.
   Preparation `end_date/end_kind` metadata describe the 20th-session close and
   do not affect AV001 selection. Prices start at the first decision's 252-day
   XKRX window and end at the last session of 2025. HF001 can inspect subsequent
   sessions inside that span to classify resumption. No whole-market session may
   be missing. A missing individual stock session makes the relevant signal
   uncomputable rather than extending its window.
3. The CH001 loader copies D001's last saved row per stock/date and numeric
   conversion rules, adding high/low. It preserves base_price, volume and
   trading_value and derives ret_1d only from the saved change_rate_pct. Missing
   bases remain missing for chart filters. A starts at 1; its first day's return
   must still pass the validity test even though only later returns multiply A.
4. RSI uses exactly 14 seed deltas and 45 updates. The RSI and percentile tests
   compare unrounded floating-point values directly, without a tolerance. A
   displayed 70 may therefore be slightly below 70. Flat RSI is uncomputable;
   zero-volume return days remain eligible for heat/anchor.
5. Wick's explicit candidate conditions override the common return-day rule:
   no ret_1d/calendar_status requirement is added. Each candidate requires a
   22-session span: its 21 bases and their preceding closes. The five spans need
   at most 26 sessions. All failed candidate reasons are retained, so reason
   counts can overlap. A partial-uncomputable name is evaluable whether or not a
   valid candidate is a spike; no valid candidate means the name is kept and
   excluded from E. No missing-base substitution is made.
6. Anchor ranks are recomputed in each rule's full evaluable U, separately by
   decision-date market, before B/P/subset intersections. Ties use maximum rank.
   Inclusive boundary counts (RSI 69–71, percentile .29–.31) are provided for U,
   P and B, without changing any judgement.
7. A month with nonempty U and complete market/listing coverage is valid even
   when B is empty: both portfolios are cash and d=0. Empty U is retained as an
   invalid observation, not manufactured as a valid zero-effect month. Each
   selected slot has equal weight before entry checks; unfilled slots remain
   cash. Filtered-empty portfolios have zero returns and costs. The filters-off
   assertion compares names, equal weights, cash and all three cost/net scenarios
   against a B snapshot obtained through AV001's target path.
8. HF001's forward_observations is called unchanged, including its registered
   entry, price-limit, stale-exit, halt, presumed-delisting and raw-price-return
   policies. Those established accounting policies do not enter chart signals.
   Full v1 costs are charged at x1/x1.5/x2; the shared fixed-observation v2 repricer
   supplies a reporting-only diagnostic with the same names/weights/cash/gross.
9. Universe excess uses U equal-weight **net** as in HF001, including unfilled
   slots and full costs. Removed-minus-remaining is undefined unless both
   selected baskets are nonempty. Market/liquidity diagnostics rerun equal-weight
   accounting inside each subset with full-U chart ranks frozen; empty subset U
   is invalid. ADV buckets use the existing cost model's boundaries. Year and
   short-sale splits use decision dates, including the inclusive registered ban
   dates. P-reference d and overlaps with each avoidance set are reporting only;
   an overlap share has a null denominator when no P name was removed.
10. Controls use one default_rng(0) per rule/variant outside the path loop and
    traverse path → every decision month → sorted E. `choice` is called for zero
    and all removals as well. Uncomputable names remain. The removal count is
    fixed from the actual strategy. All 200 path means are retained; actual and
    control baskets use the same sorted-name mean reduction, and ties count as
    greater-or-equal without a plus-one adjustment. Loss reruns reset the same
    generator and assert identical draw-stream SHA-256.
11. HF001's NW implementation matches Bartlett lag 1 with covariance divisor N,
    variance-of-mean divisor N and no finite-sample correction. It uses ordered
    valid cohorts without inventing missing observations and an upper standard
    normal tail. Holm maps the fixed CH001 family to HF001's three fixed slots.
    Undefined/insufficient tests supply p=1. The explicit monthly rule requires
    both 36 valid and 36 actual-removal months; the generic YAML 300-event entry
    is not an additional/substitute gate. Existing CH001 rows plus this run's
    three rows determine the strict t>2/t>3 threshold. 2025 sign is diagnostic.
12. Risk-only requires mean d<=0, lower sample volatility and a higher mean of
    the worst ceil(.05*N) cohorts on the same valid months. Drawdown starts at
    wealth 1 and is a stitched-cohort diagnostic. Both S and O must pass. Combined
    non-pass precedence follows AV001: data_incomplete for both rules, unavailable
    O sensitivity, delisting_sensitive, one-rule pass/version_sensitive,
    insufficient, risk_only, fail. Loss controls and rule-specific Holm are
    recomputed before comparing verdicts. Listing coverage stops every variant
    before forward observations; unavailable O is never evaluated as a pass.
13. Every row includes compact S/O summaries and the combined variant verdict.
    All three rows and the full finite JSON are validated before publication or
    any append. The existing `record_trial` API is used once per variant; it does
    not promise atomic rollback after an operating-system append failure. The
    shared exclusive writer compresses JSON above 50 MiB. HF001 local recorded
    JSONs, when present, are compared separately by rule, including month counts,
    means, common-month net differences and symmetric name differences; their
    hashes identify the comparison inputs.
14. Evaluation provenance uses AV001's complete source inventory plus CH001,
    D001 and relevant registration hashes: price files, stage-bounded financials,
    companies, corporate codes, all main/supplement listings and coverage state.
    Code commit/content, libraries, L, date-rule counts and listing_version are
    retained. Supplement verification may hash July–October 2026 prices; those
    verified frozen hashes are reported separately. January–June price hashes
    come only from the frozen target-scan metadata, and their files are never
    opened by CH001. Later listing metadata is scanned for L/date provenance, as
    required by AV001; actual-public-date cutoffs gate financial values.
15. Dry run computes real selections and filter computability without calling
    real forward observations, portfolio evaluation, verdicts, HF001 performance
    comparisons or publication. AV001 preparation internally computes unused
    price indices; this is disclosed rather than misrepresented as a mere file
    inventory. Synthetic filter/entry/control timings and measured full
    preparation provide runtime/RSS estimates, not a measured full experiment.
16. The unchanged v2 settlement-calendar import can invoke the existing project
    environment loader. CH001 checks only file existence and refuses to start if
    `.env` or `.env-ai` exists in the code checkout, before any file is opened.
    This enforces the explicit no-credential-read constraint instead of changing
    the shared calendar/cost implementation. Both files are absent here.

## Validation

The requested seven suites run with bytecode and pytest cache writes disabled,
`OPENAI_API_KEY=offline-disabled`, `OPENAI_BASE_URL=http://127.0.0.1:9/v1`,
`LANGCHAIN_TRACING_V2=false` and `LANGSMITH_TRACING=false`:

```text
venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_experiments_ch001.py tests/test_experiments_av001.py \
  tests/test_experiments_hf001.py tests/test_experiments_hc002.py \
  tests/test_backtesting_experiment_registry.py tests/test_dart_listing.py \
  tests/test_dart_financial_date_rules.py
```

Result: **350 passed, 1 skipped**, 100.11 seconds. The skip is the existing
opt-in saved HC003 archive replay, not a failing test. CH001 contributes 95
cases. The fixtures cover exact rule boundaries, invalid/short histories,
candidate/stock reason counts, full-U market ranks and ties, cash accounting,
HF001 execution equivalence, golden path-major random draws including zero/all
choices, loss draw replay, each verdict gate, trial batches crossing 20,
source provenance, protected-file access rejection, v2 fixed-position repricing,
compressed publication and validation of every compact row before any write.

The environment-file preflight also has fixtures proving that an existing file
causes a stop before any open. No environment loader or existing cost/calendar
module was modified.

## Real-data dry run

The requested real-data CLI dry run completed with exit status 0. Its process
ran for **27m 29.59s**, including **1,645.11 seconds** of preparation. Maximum RSS
was **2,613.42 MiB = 2.740 GB**, with zero swaps. These are measured dry-run
resources, not measurements of a full return evaluation.

- Decision months: 103; evaluation price span: 2016-05-25 through 2025-12-30.
- Price inputs: 2,357 files, 5,679,900 latest-version stock/session rows.
- Listing supplement: complete, 464/464 companies; zero incomplete windows.
- L: 17 calendar days. O sensitivity is available.
- Each rule's date audit: 163,799 stored versions, 157,556 stage eligible,
  1,077 unusable unlinked versions, 5,166 excluded by the stage cutoff, and
  6,040 pre-listing versions using the registered +L rule. In the correction
  sample, original links succeeded for 15,472 and failed for 317 (97.99% success).
- Supplement integrity: 67 nonprotected 2026 files hashed; 120 protected files
  represented only by their frozen hashes, without opening their contents.

The following counts are **stock/month cohorts within B**, not distinct firms
or forward-return observations:

| Rule | Filter | B cohorts | Evaluable | Uncomputable | Partial | Removed |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| S | heat | 40,163 | 40,159 | 4 | 0 | 1,708 |
| S | wick | 40,163 | 40,007 | 156 | 218 | 4,049 |
| S | anchor | 40,163 | 40,141 | 22 | 0 | 7,696 |
| O | heat | 40,567 | 40,563 | 4 | 0 | 1,725 |
| O | wick | 40,567 | 40,414 | 153 | 224 | 4,103 |
| O | anchor | 40,567 | 40,545 | 22 | 0 | 7,691 |

RSI boundary counts are 557 (S) / 558 (O); anchor percentile boundary counts
are 705 (S) / 716 (O). No performance judgement follows from these counts.

Synthetic fixture measurements: 128 stocks, 300 sessions, 12 control cohorts,
200 paths. Three filters took 0.02404 s, HF001 per-stock forward accounting took
0.07088 s, and controls took 0.08710 s. No real forward returns were evaluated.

The runner's conservative scaling projects a full execution at **50.9–103.8
minutes** and **4,277.02 MiB = 4.485 GB peak RSS**, below the 7 GB target. The
projection combines measured full preparation, cohort/session-scaled synthetic
forward accounting and 12 rule/variant/loss control evaluations. The one-cohort
forward fixture can overstate costs when scaled across months because HF001
caches per-stock arrays; this heuristic range is not a confidence interval or a
validated time bound. Full-run time, publication memory and real portfolio
outcomes remain unmeasured because real execution was explicitly forbidden.

The full-data job started before the final fixture-driven reporting/preflight
refinements were completed. Later changes add stock-level wick reason counts,
credential-file and evaluation-hash preflights, v2 monthly reporting and summary
formatting, plus the finite-ratio overflow guard. Final-code tests cover those
paths. A separate final-code, price-only audit checks the latter guard on the
same bounded real input snapshot; its outcome is recorded below. It performs
no financial selection or forward portfolio evaluation.

No real registry row, CH001 result file, holdout session or commit was created.
The registry SHA-256 remained
`5c066a4adf36c9ecef5f6780d83f97d72b224a8fb8c629e85c9e5a25eefa9b9a`;
the preregistration SHA-256 remained the value at the top of these notes.

Full dry-run stdout (including per-receipt-year date counts):

```text
CH001_chart_avoidance_filters dry-run; variants=ch_heat,ch_wick,ch_anchor; rules=S,O
Preparing real U/P/B and chart computability; no forward portfolio outcomes or publication.
Preregistration: b53485ec62edaf5f24ccc9cc6518dbb9ce0c8193; months=103
Evaluation price span: 2016-05-25..2025-12-30; files=2357; rows=5679900
Listing supplement complete=True; reason=None; completed=464/464; incomplete months=0
Actual-public-date L=17 days; O sensitivity available=True
Financial date counts: {"S": {"rule": "S", "stage": "design_validation", "counts": {"unlinked_pre_listing_plus_L": 6040, "unknown": 7117, "stage_eligible": 157556, "linked": 156682, "failure": 317, "not_correction": 139825, "success": 16540, "unlinked_since_listing_start": 1077, "unusable": 1077, "stage_cutoff": 5166}, "sample_end_exclusive": "2025-11-28", "stored_versions": 163799, "by_receipt_year": {"2015": {"unlinked_pre_listing_plus_L": 70, "unknown": 70, "stage_eligible": 70}, "2016": {"unlinked_pre_listing_plus_L": 5970, "unknown": 5970, "stage_eligible": 12321, "linked": 6351, "failure": 80, "not_correction": 5961, "success": 310}, "2017": {"linked": 13052, "failure": 106, "stage_eligible": 13052, "not_correction": 11897, "success": 1049, "unlinked_since_listing_start": 2, "unknown": 2, "unusable": 2}, "2018": {"linked": 13883, "failure": 45, "stage_eligible": 13883, "success": 1551, "not_correction": 12287, "unlinked_since_listing_start": 28, "unknown": 28, "unusable": 28}, "2019": {"linked": 14519, "failure": 23, "stage_eligible": 14519, "success": 1492, "unlinked_since_listing_start": 77, "unknown": 77, "unusable": 77, "not_correction": 13004}, "2020": {"linked": 15196, "failure": 15, "stage_eligible": 15196, "success": 1320, "unlinked_since_listing_start": 78, "unknown": 78, "unusable": 78, "not_correction": 13861}, "2021": {"linked": 16197, "failure": 26, "stage_eligible": 16197, "success": 1998, "not_correction": 14173, "unlinked_since_listing_start": 88, "unknown": 88, "unusable": 88}, "2022": {"linked": 16652, "failure": 12, "stage_eligible": 16652, "success": 2142, "unlinked_since_listing_start": 75, "unknown": 75, "unusable": 75, "not_correction": 14498}, "2023": {"linked": 17450, "failure": 10, "stage_eligible": 17450, "success": 1826, "not_correction": 15614, "unlinked_since_listing_start": 122, "unknown": 122, "unusable": 122}, "2024": {"linked": 18892, "success": 1927, "stage_eligible": 18892, "not_correction": 16965, "unlinked_since_listing_start": 252, "unknown": 252, "unusable": 252}, "2025": {"linked": 19324, "success": 1863, "stage_eligible": 19324, "unlinked_since_listing_start": 270, "unknown": 270, "unusable": 270, "not_correction": 17461}, "2026": {"linked": 5166, "success": 1062, "stage_cutoff": 5166, "not_correction": 4104, "unlinked_since_listing_start": 85, "unknown": 85, "unusable": 85}}, "sample_correction_links": {"success": 15472, "failure": 317, "success_rate": 0.9799227310152638}, "sensitivity_available": true}, "O": {"rule": "O", "stage": "design_validation", "counts": {"unlinked_pre_listing_plus_L": 6040, "unknown": 7117, "stage_eligible": 157556, "linked": 156682, "failure": 317, "not_correction": 139825, "success": 16540, "unlinked_since_listing_start": 1077, "unusable": 1077, "stage_cutoff": 5166}, "sample_end_exclusive": "2025-11-28", "stored_versions": 163799, "by_receipt_year": {"2015": {"unlinked_pre_listing_plus_L": 70, "unknown": 70, "stage_eligible": 70}, "2016": {"unlinked_pre_listing_plus_L": 5970, "unknown": 5970, "stage_eligible": 12321, "linked": 6351, "failure": 80, "not_correction": 5961, "success": 310}, "2017": {"linked": 13052, "failure": 106, "stage_eligible": 13052, "not_correction": 11897, "success": 1049, "unlinked_since_listing_start": 2, "unknown": 2, "unusable": 2}, "2018": {"linked": 13883, "failure": 45, "stage_eligible": 13883, "success": 1551, "not_correction": 12287, "unlinked_since_listing_start": 28, "unknown": 28, "unusable": 28}, "2019": {"linked": 14519, "failure": 23, "stage_eligible": 14519, "success": 1492, "unlinked_since_listing_start": 77, "unknown": 77, "unusable": 77, "not_correction": 13004}, "2020": {"linked": 15196, "failure": 15, "stage_eligible": 15196, "success": 1320, "unlinked_since_listing_start": 78, "unknown": 78, "unusable": 78, "not_correction": 13861}, "2021": {"linked": 16197, "failure": 26, "stage_eligible": 16197, "success": 1998, "not_correction": 14173, "unlinked_since_listing_start": 88, "unknown": 88, "unusable": 88}, "2022": {"linked": 16652, "failure": 12, "stage_eligible": 16652, "success": 2142, "unlinked_since_listing_start": 75, "unknown": 75, "unusable": 75, "not_correction": 14498}, "2023": {"linked": 17450, "failure": 10, "stage_eligible": 17450, "success": 1826, "not_correction": 15614, "unlinked_since_listing_start": 122, "unknown": 122, "unusable": 122}, "2024": {"linked": 18892, "success": 1927, "stage_eligible": 18892, "not_correction": 16965, "unlinked_since_listing_start": 252, "unknown": 252, "unusable": 252}, "2025": {"linked": 19324, "success": 1863, "stage_eligible": 19324, "unlinked_since_listing_start": 270, "unknown": 270, "unusable": 270, "not_correction": 17461}, "2026": {"linked": 5166, "success": 1062, "stage_cutoff": 5166, "not_correction": 4104, "unlinked_since_listing_start": 85, "unknown": 85, "unusable": 85}}, "sample_correction_links": {"success": 15472, "failure": 317, "success_rate": 0.9799227310152638}, "sensitivity_available": true}}
Filter computability S/ch_heat: {"names": 40163, "evaluable": 40159, "uncomputable": 4, "partial_uncomputable": 0, "removed": 1708, "boundary_rsi_69_71": 557, "boundary_percentile_029_031": 0, "uncomputable_by_reason": {"missing_session": 4}}
Filter computability S/ch_wick: {"names": 40163, "evaluable": 40007, "uncomputable": 156, "partial_uncomputable": 218, "removed": 4049, "boundary_rsi_69_71": 0, "boundary_percentile_029_031": 0, "uncomputable_by_reason": {"no_valid_candidate": 156}}
Filter computability S/ch_anchor: {"names": 40163, "evaluable": 40141, "uncomputable": 22, "partial_uncomputable": 0, "removed": 7696, "boundary_rsi_69_71": 0, "boundary_percentile_029_031": 705, "uncomputable_by_reason": {"missing_session": 22}}
Filter computability O/ch_heat: {"names": 40567, "evaluable": 40563, "uncomputable": 4, "partial_uncomputable": 0, "removed": 1725, "boundary_rsi_69_71": 558, "boundary_percentile_029_031": 0, "uncomputable_by_reason": {"missing_session": 4}}
Filter computability O/ch_wick: {"names": 40567, "evaluable": 40414, "uncomputable": 153, "partial_uncomputable": 224, "removed": 4103, "boundary_rsi_69_71": 0, "boundary_percentile_029_031": 0, "uncomputable_by_reason": {"no_valid_candidate": 153}}
Filter computability O/ch_anchor: {"names": 40567, "evaluable": 40545, "uncomputable": 22, "partial_uncomputable": 0, "removed": 7691, "boundary_rsi_69_71": 0, "boundary_percentile_029_031": 716, "uncomputable_by_reason": {"missing_session": 22}}
Supplement integrity only: hashed 67 nonprotected 2026 price files; 120 protected files use frozen hashes only (never opened).
Fixture timing: {"stocks": 128, "sessions": 300, "control_months": 12, "paths": 200, "filter_seconds": 0.0240384400003677, "forward_seconds": 0.07087575900004595, "control_seconds": 0.08709756400003243}
Projected runtime/RSS: {"measured_preparation_seconds": 1645.1134692120004, "measured_peak_rss_mib": 2613.421875, "full_run_seconds_range": [3054.733624651043, 6229.467249302086], "peak_rss_mib_estimate": 4277.020080566406, "target_rss_bytes": 7000000000, "basis": "Actual full S/O preparation and filters, synthetic forward scaled by stock-cohorts/session span, 12 rule/variant/loss controls; extra RSS allows 160 bytes per name/session plus 512 MiB serialization. Estimates, not measured execution."}
No real forward portfolio returns evaluated; no registry/results writes; no holdout mode.
```

## Final-code price-only audit

The final runner's registration preflight and bounded loader were separately
executed against all 2,357 real price files, under a Python audit hook that
rejects data writes, network connections/name resolution, `.env`/`.env-ai`
opens and protected January–June 2026 price-file opens. Exit status was 0;
measured elapsed time was 2m 04.50s and peak RSS 1,506,696 KiB.

The minimum positive close is 1 and the maximum finite positive base is
2,861,000. Consequently every finite-positive base/previous-close ratio is
bounded above by 2,861,000, and cannot overflow. The final overflow-guard
refinement therefore cannot change the completed full-data dry-run selections.
Underlying-reason stock counts are an additional report; they do not change
E or exclusions. The v2/publication refinements were validated only on fixtures,
as required by the prohibition on real execution.

```json
{"price_files": 2357, "rows": 5679900, "minimum_positive_close": 1.0, "maximum_finite_positive_base": 2861000.0, "upper_bound_on_finite_positive_base_close_ratio": 2861000.0, "audit": {"data_read_opens": 2357, "network_attempts": 0, "environment_read_attempts": 0, "data_write_attempts": 0, "protected_price_read_attempts": 0}, "interpretation": "No finite-positive base/previous-close ratio can overflow on this snapshot; the final wick guard refinement cannot alter the completed dry-run decisions. No forward returns evaluated."}
```

Final reviewed source hashes (notes excluded to avoid self-reference):

- `backtesting/experiments/ch001.py`: `a627005ba983020378178ee726f6745b092140f44cc6bc89feeaf775169e2ebe`
- `tests/test_experiments_ch001.py`: `4a09fe95874586566c847cdaa279ee5ac528568e11435405a0d68cc952205415`

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
