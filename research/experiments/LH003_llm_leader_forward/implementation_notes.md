# LH003 implementation and operator contract

The preregistration is unchanged (SHA-256 `dc1ee3b669e515dc92b504eaf5d88061281b079f3c7f4fc5d7194dd5c37d190f`). Implementation:
`backtesting/experiments/lh003/`. Development and tests are offline.

## Commands

```bash
python -m backtesting.experiments.lh003 --dry-run --data-dir /path/to/archive
python -m backtesting.experiments.lh003 --verify-environment --data-dir /path/to/archive \
  --sync-manifest /path/to/transfer.json --restart-evidence /path/to/restart.json \
  --report research/experiments/LH003_llm_leader_forward/reports/preflight.json
python -m backtesting.experiments.lh003 --verify-environment --synthetic \
  --data-dir /path/to/archive --report /tmp/lh003-synthetic-report.json
```

The default is a read-only dry check. A real synthetic-format trial is a separate,
explicit operator action, never performed by this validation:

```bash
python -m backtesting.experiments.lh003 --verify-environment --real-format-trial \
  --data-dir /path/to/archive --state-dir /path/to/lh003 \
  --sync-manifest /path/to/transfer.json --restart-evidence /path/to/restart.json \
  --report /path/to/preflight.json
```

The flag requires `--verify-environment` and a durable `--state-dir`. Six calls use
the production CLI, event/schema validation and identity checks with entirely
synthetic inputs. Their durable reservations share the official 320-call journal;
format calls also have an eight-attempt cap. Completed measurements are reused
without new calls; an interrupted measurement cannot reset its budget. A fake
cannot certify this gate. No real-data prompt is sent by the format trial.

`--synthetic` alone generates labelled sources
and an H1 stand-in in a temporary directory outside `data/`, runs a fake cycle,
then removes scratch files. It cannot establish real-data readiness, create
observations, consume the holdout or spend model budget. Only the separate explicit
real-format flag spends calls. Production actions:

```bash
python -m backtesting.experiments.lh003 --seal-holdout --data-dir /path/to/archive \
  --state-dir /path/to/lh003 --environment-report /path/to/preflight.json
python -m backtesting.experiments.lh003 --run-week --data-dir /path/to/archive \
  --state-dir /path/to/lh003 --decision-day 2026-11-13 --sync-manifest /path/to/transfer.json
python -m backtesting.experiments.lh003 --record-executions --data-dir /path/to/archive \
  --state-dir /path/to/lh003 --sync-manifest /path/to/evaluation-transfer.json
python -m backtesting.experiments.lh003 --evaluate --state-dir /path/to/lh003
```

Default state is `<data-dir>/research/lh003`; `--state-dir` separates writable
state from a read-only archive. `--snapshot-dir` configures acquisition or
verification. `--call-timeout` defaults to the existing LH runner's 300 s; the
production timeout must equal the verified start budget. `--operator-note`
records calendar corrections or machine replacement reasons. No action sources
`.env`, fetches data, installs services or sends orders. `research_run.sh`
allows `lh003` and preserves action flags, defaulting to a dry run.

## Startup and protected inputs

1. Under the shared checkout lock, verify the immutable preregistration, AV001
   and EV001's latest complete three-row designs, and terminal holdouts for every
   passing variant. Also verify both AV002 terminal variants, one sealed execution,
   result hash and AV001 parent link through main's release verifier. Missing/deleted
   registrations cannot bypass a gate. Calendar coverage and nonempty H1 price-file
   inventory, plus all 252 price sessions needed by the pre-start measurement, are
   verified before constructing a session or consuming its claim.
2. Preflight the machine, transfer hashes, restart evidence, measured real synthetic-format
   path, process-tree memory and six-attempt timing budget. Failures before the claim can be retried before the fixed start.
3. Enter the single LH003 session. Copy H1 daily prices/listings, and complete
   mixed financial/index archives, into a hash-verified read-only snapshot. Mixed
   files contain H1 versions plus the other historical versions required for
   quarter derivation. No H1 return is evaluated.
4. Within that same claim, measure one real-input cycle using the newly sealed
   snapshot and a fake model. This avoids requiring a snapshot before the only
   permitted acquisition. Verify the complete section 8 report and publish
   `start.json` before the first decision close. Any post-claim failure, including
   failed measurement or a crash without `start.json`, consumes the claim and
   produces `not_started`; no second session is opened.

Weekly reads verify the start hash and snapshot files. Protected daily paths
always resolve to the snapshot. Mixed financial/index archives are never reopened,
including for hashes, byte offsets or size checks. Collectors publish content-addressed,
read-only `.versions/<sha256>.json` records before rewriting quarterly aggregates
or appending benchmarks. They omit H1 dates. Weekly views merge the sealed baseline
with these independently verified non-H1 records and deduplicate financial filing
identities. Full-file reordering, growth, same-size rewrites and truncation of an
original cannot alter the frozen baseline or lose a published new version. A new
archive requires its immutable versions; new-version feeds remain discoverable even if a crash prevents creating the mutable
aggregate. Protected/undated incremental injections,
changed hashes/modes and conflicting filing values refuse. No H1 backfill/export is
performed by the runner. Operators must deploy the updated collectors before start.

Transfer manifests are JSON with aware `verified_at`, the actual `transfer_route`,
and `files` mapping archive-relative paths to SHA-256. A decision requires both
markets' verified rows, its exact file hash and verification after the pinned
session close. Missing decision-day data refuses; another historical week is
never substituted. Input-view hashes are checked before/after generation and
recorded in the seal.

## Interpretations fixed in code

| Topic | Implementation |
| --- | --- |
| Schedule | Fixed 52 W-FRI slots, 2026-11-13 through 2027-11-05, each week's last XKRX session. Next-session entry; entry-inclusive twentieth-session exit. No extension. |
| Calendar | Pinned exchange-calendars 4.13.2 through the shared corrected calendar and official special-close guard. All 52 fixed slots and the full execution horizon must be verified before start. Pending 2026-11-19 hours and unverified 2027 coverage fail closed; no week is dropped and no window is extended. Store session/execution hashes; reviewed calendar-data updates require an operator note. Missing calendar state after start refuses. |
| Finalisation | Seal inputs and A first, with an A fallback and publication receipt before calls. A proposal needs its post-publication receipt strictly before next-session 08:59 KST. Late proposals are reconstructed; a timely fallback remains the invalid-week observation. An A first sealed late is reconstructed and never observed. |
| Resume | Reuse sealed inputs, schema, ID map and attempts. A reservation without a result counts as interrupted. No refill calls, source rereads or duplicate completed publications. |
| Calls | Three independent ephemeral runs, first plus one fixed re-ask each. All three must validate. Tool/unknown events invalidate an attempt. Durable reservations count before process creation, including timeout/interruptions. Maximum 320 for the ID. |
| Identity | Pin model argument, CLI version, artifacts and implementation hashes; check before/after calls and persist a stop on drift or unverifiable identity. Later policies use A. All LH001 modules, package initializers, cost/calendar algorithms and direct date, industry, disclosure and ledger dependencies are pinned, including at final evaluation. Only `backtesting/statutory_rates.py` and `src/krx_calendar_data.py` hold permitted mechanical updates; their hashes are recorded separately. Internal weight changes not exposed by the provider cannot be detected from CLI identifiers. |
| Aggregation | LH002 count, mean appearance rank, lexical ID. Aggregate 30 ranks, buy-ten appearances within those ranks, avoid-ten appearances excluding final buys. Disjoint arrays are required per model output. Aggregated rank diagnostics may overlap avoid diagnostics; bought/avoided portfolios are disjoint, as in LH002. |
| A/candidates | A retains every eligible HC002 phase-2 stock. Display/random pool alone is capped to 300 by close-known ADV, then code. Fewer than 40 means no call and A. |
| Features | Reuse LH002 pure numerical tables, industry table and anonymisation, including four-quarter ratios, daily-value percentiles, complete return chains and 250-session highs. Require 252 close-known sessions; no future prices enter prompts. |
| Financial dates | AV001 rule S: later receipt/list date, strict prior-date publication, pre-2016 unlinked receipt plus observed L, later unlinked/conflicting versions unusable. Pass the shared merged-listing conflict set and exclude every conflicting filing version, retaining strict pre-decision listing scope. Derive quarters and apply the existing CFS/OFS ranking. |
| Disclosures | Merged original/delisting supplement. Prior 60 completed sessions and intervening calendar days; exclude same-day date-only filings. Any incomplete day makes category counts unavailable, never zero. |
| Execution | Separate overlapping cohorts. Remove limit-up/untradable entries and give the executable remainder equal entry budgets. All unfilled means cash. Fractional quantities represent normalized budgets, not orders. |
| Exits | HF001 permissible last close, halt-resumption check, actual last trading close for presumed disappearance, raw-price fallback for unavailable return chains, separate -100% sensitivity. Presence ends on registered price date 2027-12-03. Presumed disappearance is not independently certified delisting. |
| Costs | v2 one-way costs at each actual raw price, market, date and strictly preceding 20-session ADV. Net `(1+g)*(1-s)/(1+b)-1`; buy budget includes costs. Multipliers leave tax unscaled. All three scenarios and both disappearance valuations seal quantities, costs and cash per week. Invalid policy/control portfolios reference that scenario's single A record instead of duplicating it 201 times; the same sharing applies to random-draw records. |
| 2027 | Decisions/raw executions never evaluate costs. Unknown 2027 trade or T+2 tax dates refuse evaluation with a statutory-source message; no 2026 rate is carried forward. A legally sourced mechanical update can evaluate frozen executions later. |
| Random paths | Seed 0; calendar week, path, sorted candidate code. 200 ten-name draws on valid weeks before inspecting quotes, no filtering/refill. Invalid weeks use exactly A and consume no draws. |
| Inference | All 52 weekly differences; invalid weeks exactly zero. Calendar-week Bartlett NW lag 4, no finite-sample correction, standard-normal one-sided p. Zero variance is undefined t and insufficient. Never zero-fill or omit unreproducible A/mandatory evaluation records. |
| Verdict | At least 30 valid decisions and 300 bought stock/decision observations; positive mean, t strictly above 2 (3 after >20 LH003 trial rows), control share <=5%, positive x1.5 mean. x2 reports only. No LH002 half-effect/YAML holdout performance condition. Base insufficient stays insufficient; another verdict changed by loss sensitivity becomes delisting_sensitive. |
| Trial | One policy row per statistical evaluation attempt, including insufficient and evaluation_aborted; terminal not_started is also recorded. Unknown statutory years are checked before a statistical trial starts. Weekly calls are attempts within a trial. Other IDs do not raise LH003's threshold. Before any computation, the canonical registry blocks a second completed evaluation, even after state rollback. Only restoration of its existing sealed result hash is accepted. Publication recovery and missing `not_started` rows reuse the existing seal hash under the shared lock. |
| As of | Final evaluation only after 2027-12-17 00:00 KST. Raw execution manifests need that exact `as_of` and source hashes. Corrected prices only create audit records through `--audit-price-corrections`, never overwrite official executions/verdict. |

## Deployment evidence

Reports sample the whole living process tree every 10 ms, including CLI children
and grandchildren, and record six measured call durations plus the six-timeout
bound with version checks. For the post-claim real-input cycle, reused pre-claim
format measurements provide a conservative parent-plus-CLI-child capacity bound.
The measured tree peaks and capacity bound must fit 1536 MiB. Timing, the real
format path and memory are mandatory start gates. A fake real-input cycle alone
cannot pass. Reports also contain a restart plan. They do not certify a
reboot/suspend/logout without evidence. The evidence file identifies
`service_unit` and true observations for `reboot`, `suspend_resume`, `session_exit`,
`automatic_resume`, `same_input_hash`, `same_A_hash`, `attempts_preserved`,
`no_duplicate_decisions` and `weekend_holiday_sync`. Preserve actual test logs.

The operator installs a persistent research-account service/timer retrying the
same state directory and arranges weekend/holiday collection and transfer before
the deadline. The runner verifies their outputs; it does not modify collector
accounts or enable network collection. Operational checks stay pending until
actually performed.

## Round 21 validation artifacts

`validation_report.md` records the focused/full-suite outcomes, memory bounds,
remaining environment/merge blockers and complete changed-file list.
`reports/round21_validation.json` records the algorithm/data hashes and measured
process limits. `reports/round21_test_harness/` preserves the offline guard and
sandbox/temporary-fixture compatibility settings. These checks use stand-ins;
they do not establish live CLI authentication, actual-input capacity or returns.
The older 2026-10-17 environment reports remain historical and cannot satisfy the
new real-format, implementation-identity and process-tree start gates.

Frozen-code verification: 108/108 LH003 tests passed; the related-area run passed
514 tests. The full suite finished with 5084 passed, 41 failed and 6 skipped.
The remaining CH004 source-pin, time-dependent ingestion, sandbox socket and
shadow-memory failures are detailed in `validation_report.md`; their existing
guards were preserved. All runs were serial and used `-p no:cacheprovider` with
the archived offline/sandbox fixture harness. Maximum individual RSS in the full
suite was 1374.703 MiB; no process crossed the supervisor limit.
