# FW001 recorder and deployment preparation

The preregistration at `23e2051` and `comparison_values.json` are unchanged.
This work prepares code and deployment artifacts only: no commit, server
connection, deployment, API request, holdout session, or real-data evaluation.

## Recording contract

- `src/forward/fw001.py` calculates one decision using the latest 60-session
  price window. Missing/invalid volatility returns remain uncomputable, as
  HF001 requires; they do not trigger exclusion. The decision's last 20 price
  sessions must exist, and its daily file must contain verified KOSPI/KOSDAQ
  observations. Initial adjusted indices start before ownership, using the
  last 20 sessions; their arbitrary units do not change wealth/returns.
- `f_both` uses HC002's unchanged universe path and HF001's log volatility,
  market-specific max-tie ranks, literal rights-issue policy, frozen weights,
  cash slots for unfilled entries, exit handling, and full v1 round-trip
  charges for both filtered and unfiltered baskets. It never subtracts the
  existing HC002 shadow return.
- `av_all` uses the same universe path with actual-public-date rule S before
  quarterly derivation. Receipt conflicts and unlinked post-20160601 versions
  are excluded; pre-listing receipts use global merged-list L. Only necessary
  receipt links are retained while the full listing is streamed for L. Raw
  financial accounts are processed one business year at a time. Stored-date
  HF001 financials retain the existing quarterly derivation/CFS ranking.
- Listing coverage uses `hf001.listing_coverage` and the v3 merged reader,
  including its frozen target scan. The first known-event window starts
  2026-04-27; its receipt coverage also includes the preceding session and
  intervening calendar days. Protected KRX contents are never opened. The
  historical inventory/non-protected hashes are checked, without calculating
  historical returns. This metadata verification is distinct from the recent
  numerical price window.
- Normalised cash is 1 for the first av_all decision only. HC003's formula,
  sell-before-buy order, proportional funding, 0.1% band, frozen positions,
  dated v2 costs and adjusted-index/ADV history carry between immutable
  executions. Each decision stores per-scenario holdings/cash/close value,
  predecessor hashes and the chain state. No lost state is reset to cash 1.
- Financial batch status records last successful/attempted time, remaining
  targets and the company batch. The quarterly collector now persists
  `_batch_status.json`, including never-attempted jobs. Legacy checkpoints do
  not contain historical success times or company batch counts: these are
  explicitly null/unavailable, not inferred from mtime. Archive hashes and
  selected receipt versions identify what was used. Weekly refresh delay does
  not substitute financial values or block an otherwise valid decision.

Records under the server's data directory:

| Path below `forward/fw001/` | Meaning |
| --- | --- |
| `decisions/YYYYMM.json` | Targets, weights, source/code hashes, financial status, candidate preparation time (`finalised_at`) and state; the completion proof defines finalisation |
| `executions/YYYYMM_1.0.json`, `_1.5.json`, `_2.0.json` | Separate immutable scenario fills, quantities, cash, charges, quotes and chain |
| `missing/YYYYMM.json` | Counted omission and frozen no-trade transition inputs |
| `reruns/<sha256>.json` | Explicit reconstructed audit only |
| `cost_resolutions/YYYYMM_<multiplier>.json` | Completion of previously undefined costs/state, referencing the original execution hash |
| `price_versions/YYYYMMDD/*.json`, `snapshots/<sha256>.jsonl` | Immutable server revisions and content-addressed bytes |
| `execution_prices/YYYYMMDD.json` | Original price input shared by the three execution scenarios |
| `sealed/YYYYMM.decision/complete.json`, `YYYYMM.execution.<multiplier>/complete.json` | Completion proofs: record path, actual-byte SHA-256, preparation time, publication clock/precision, decision deadline, `reconstructed: false`; `sealed_at` is read from publication directory mtime |
| `evaluation_freeze.json` | Inventory/hashes of immutable inputs existing by the registered freeze |

Every financial archive, main and supplement listing file read for global L,
receipt provenance and rights events, reference/metadata file, and required
price window is preserved as original bytes in content-addressed snapshots.
`input_snapshots` maps original paths to blobs and hashes; `input_hashes`
identifies the original bytes. The full listing scan is a financial-date
input, so preservation is wider than the 126-session event window. Existing
blobs are verified and reused. Copies stream bounded chunks, verify hashes,
fsync, publish without overwrite and fsync their directory. Independent
recomputation materialises this inventory in a separate tree with the sealed
code and predecessor records. A regression test recomputes after destroying
all mutable sources and reproduces rules, state, chain and date provenance.

Decisions are staged, fsynced and atomically linked as candidates. Under the
same recording lock, the recorder verifies the input blobs and deadline, then
atomically links the completion proof into its own private directory and fsyncs
that directory. **That link is the sole finalisation point.** Only a valid
proof makes the candidate final. Recovery before the deadline may finish a
candidate; after the deadline an unproved candidate can only be reconstructed.
An unproved candidate may be atomically replaced by its reconstructed form;
completed records and markers are never overwritten or revoked. Equality is late.
The marker is prepared/fsynced outside its publication directory, so neither
temporary-file cleanup nor other scenario markers change its publication mtime.
Its `prepared_at` is not a sealing timestamp. Readers derive `sealed_at` from
the private directory's kernel mtime and `publication_not_after` from that time
plus the explicit **one-second precision bound**. Kernel filesystem timestamps
can lag the userspace wall clock by a tick. The entire publication interval
must be before 08:59; an uncertain last-second publication is refused. This is
conservative proof validation, not a grace period or a changed registered
deadline. The final pre-link check runs after marker fsync. A syscall delayed
across the cutoff leaves a rejected marker, including after an immediate crash;
there is no publish-then-revoke step. Late or precision-boundary evidence is
ineligible: section 6 records that month as missing/no-trade and continues with
later months and final evaluation. The candidate and marker remain unchanged
audit evidence and are never re-completed. Corrupt or inconsistent evidence
still raises a visible error.
The supported deployment is a local Linux filesystem whose publication clock
error/granularity is bounded by one second, checked on the final server tree.
Copy records with both file and directory mtimes preserved (`rsync -a` or
equivalent); never add entries to a published marker directory. A copy without
that metadata is not valid completion evidence. The previous flat marker
format is not accepted; activation has not occurred in this worktree.
Neither HC002's three-session grace nor `--backfill-label` exists in FW001.
An explicit rerun cannot create an observation execution. Scenario recovery
verifies and uses the sealed entry copy first, even if the mutable KRX archive
is deleted or corrupt. Execution proofs carry their own actual sealing time
and link to the decision deadline; execution publication after 08:59 never
backdates the proof. Reconstructed records have no completion proof. All
FW001 readers enforce the proof contract.
Final valuation selects the latest server revision existing at the registered
cutoff. The midnight timer takes that freeze; a recovery checks publication
times and refuses a price published after the cutoff when exact as-of data
cannot be proved. Selected server bytes and the freeze descriptor are hashed.

An omitted month has no f_both basket and no av_all trade. Holdings and cash
continue through the adjusted-price chain, with monthly returns retained.
If its contemporaneous U was never sealed, its primary benchmark/excess is
undefined and final av_all evaluation is `insufficient`; reconstruction does
not create an observation or a fabricated benchmark. Losing a predecessor's
state keeps subsequent av_all records insufficient.

## 2027 and other visible blockers

No 2027 sell tax was added. Buy-side charges can be calculated without a sell
tax year, including v2 session/tick validation. Sales/round trips and v2 T+2
settlements outside the known table fail. A pending execution freezes prices,
targets, ADV and pre-trade state; it does not invent cash-funded buy quantities
after an unknown sell charge. Once an operator adds a legally sourced table
amendment, a separate cost-resolution object can fill undefined fields only.
Already executed av_all quantities/cash and known HF001 charges are retained.
Resolve a blocked predecessor before the next decision so its carried state
is available. Lost/unavailable state is not reconstructed as an observation.

The deliberately failing readiness check is:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  venv/bin/python -m backtesting.experiments.fw001 --check-2027-costs
```

All sessions use `src/runner/trading_calendar.py`. Decisions request 126 prior
listing sessions, the decision, entry and 20-session exit, without requesting
an entire future year. 2026-10-30 works within the verified range: entry
2026-11-02 and f_both exit 2026-11-27. The shared July 17 closure applies.
2026-11-02 is an ordinary verified session; the pending CSAT notice applies
only to 2026-11-19. Official 2027 coverage remains unavailable. Planning fails
closed; expired unplannable months become explicit calendar omissions with no
invented session dates or fills. Known holdings/cash and their actual chain
date are retained; unavailable valuation/state makes av_all insufficient.
Official notices and sourced mechanical amendments are still needed before
2027 recording or final evaluation can run.

KOSPI/KOSDAQ same-interval index excess is explicitly unavailable without
frozen index opening/closing data. The existing stock-daily recorder and
close-only benchmark archive do not supply index opens. No stock-average
proxy or close-to-close substitute was introduced. This does not change the
two primary economic gates. Existing HC002 results are copied separately at
the final freeze when a pre-freeze evaluation is available.

## Offline evaluation

`backtesting/experiments/fw001.py` is a PC-only module and is not in the server
transfer list. Final publication requires `evaluation_freeze.json` with the
exact **2027-11-15 00:00 KST** cutoff. Data recorded later are excluded. A PC
price file is used only when its hash matches the frozen server descriptor;
otherwise the server blob is required and verified.

Primary f_both evaluation supplies sealed entry status/prices/charges to the
HF001 accounting function, with its existing exit and -100% sensitivity rules.
Primary av_all valuation carries sealed holdings and cash; latest frozen
adjusted marks and the final 2027-10-29 close determine wealth, without terminal
liquidation or re-execution. Re-trading detection for the -100% sensitivity
uses positive-close, positive-volume data through the 2027-11-15 00:00 KST
freeze; November prices do not extend October wealth returns. State and
decision-hash continuity are checked.
`repricing_audit` accepts an explicitly supplied PC freeze and hashes for a
separate reporting-only corrected-price replay; it cannot replace the primary.

The final gate uses the two sealed comparison values, at least 10 valid
decisions, positive mean, at least half the positive comparison effect, and
positive mean at 1.5x costs. av_all additionally needs all 12 calendar months
and continuous accounting, including no-trade returns. f_both averages paired
valid cohorts; the decision-count gate is separate, including a valid decision
whose phase-2 basket is empty. A nonpositive/undefined comparison gives
`not_applicable`; missing U/prices/state gives `insufficient`. A changed -100%
verdict gives `delisting_sensitive`. NW lag 1 / calendar lag 3, HF001 controls,
AV001 primary and HC003 secondary controls, and 2x costs are reporting only.
There is no t threshold, Holm adjustment or control-share pass gate.

Publication uses the shared strict registry reader with legacy large-field
support. It prevalidates the preregistration, commit consistency and both
new rows' finite/size-limited metrics before creating either result file.
It creates one canonical `results/final.json` and one registry row per judged
rule. A restarted publication can finish a missing registry row;
it cannot replace the final result. All real-data post-hoc evaluation is left
for the registered date; current validation uses fixtures only.

## Deployment procedure (operator steps; not executed here)

1. Review and seal the implementation and `deployment_manifest.json` before
   the first decision. The manifest hashes every actual server transfer file
   and separately lists PC evaluation/test implementation hashes. Its own
   hash is excluded to avoid recursion; record that SHA-256 with the seal.
   Every run (dry runs and recovery included) verifies the deployed inventory
   and hashes. PC evaluation also verifies implementation hashes. Bug fixes
   use `collector_manifest.py --reseal --reason 'specific bug fix'`: it retains
   the old seal in `manifest_history/` and links a hashed `manifest_changes/`
   document with the exact old/new file hashes, reason and actual time. Both
   audit directories are transferred and installed.
   After sealing, only documented bug fixes / legally sourced calendar or
   tax amendments are allowed. Record reason, time, old/new hashes and never
   recreate an earlier observation.
2. Ensure the server has the full main merged-list inputs needed for date
   provenance/L, the supplement plus `_state.json` with `target_scan` and
   `file_manifest`, current references, and quarterly archives. The existing
   reader also requires **every KRX path in `target_scan.price_files`** and
   matching non-protected hashes. A server with recent prices only will fail
   coverage; copying the supplement alone is insufficient. Seed that frozen
   inventory from the PC, without opening or evaluating protected returns.
   Future decisions/executions also need their recent raw price windows.
3. Plan both transfers (these dry runs contact SSH, so they were not run):

   ```bash
   bash scripts/ops/push_collector_code.sh ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
   bash scripts/ops/push_fw001_supplement.sh \
     /home/are01/src/HQA_Project/HQA_Project/data ubuntu@PUBLIC_IP ~/.ssh/ncp_hqa
   ```

   Add `--execute` only when the operator is ready. The supplement command
   includes `_state.json`, creates no PC outputs, excludes locks, transfers
   into `/var/lib/hqa/data/disclosures/dart_full/list_supplement/`, assigns
   `hqa:hqa`, and never deletes remote files. Stop relevant server jobs before
   syncing so a decision cannot see a partial supplement. Use the existing
   installer/update procedure to install the prepared systemd files.
4. On the server, run the offline manifest hash check and recorder dry run:

   ```bash
   sudo -u hqa /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/collector_manifest.py --verify
   sudo -u hqa /opt/hqa/venv/bin/python /opt/hqa/scripts/ops/fw001_daily.py \
     --data-dir /var/lib/hqa/data
   sudo systemctl daemon-reload
   sudo systemctl list-timers 'hqa-*' --all --no-pager
   ```

5. KRX retains weekday **08:40 KST** previous-day collection, with Saturday
   **08:40** and daily **18:30** catch-up for weekends/holidays. Every run
   requests through yesterday; no unsupported same-day option or 16:10 job
   remains. A successful collector run queues the dedicated FW001 service and
   HC002 shadow. A weekday month-end decision normally follows entry-morning
   08:40 collection; a Friday month-end follows Saturday collection. FW001
   checks verified KOSPI/KOSDAQ data again before deciding. A pending special
   session on the run day does not block collection of normal previous days;
   an unverified collected session is retained with explicit status and is
   still refused by FW001. Both mutable and frozen price-window readers also
   verify every used historical session and every row's calendar status;
   checking only the decision/entry date would permit an unverified 11-19
   inside a later normal day's lookback. Verify provider availability and the remaining
   entry-morning completion budget on the server before activation.
   `hqa-fw001-daily.service` has its own **15-minute timeout**, `MemoryMax=800M`
   and private networking. HC002 waits for it and has a separate timeout.
   An FW001 failure remains visible and does not consume HC002's timeout budget.
   FW001's timer checks at 16:15, 18:35, 20:30 and **08:15**, plus the registered
   2027-11-15 midnight freeze. HC002 keeps its 08:55/12:30 recovery timer.
   `/etc/hqa/fw001-activated` gates every automatic FW001 service start, and
   the installer does not enable its timer. Neither job submits orders.
6. Before enabling the first decision, measure completion time / cgroup peak
   on the actual **961 MiB** server with its DART workloads present, and
   exercise interruption/restart between scenario files. Local standalone
   RSS is not a server concurrency measurement. Verify no cgroup OOM or
   restart caused a missed cutoff. An unready/missed October decision stays
   a counted omission; do not extend the experiment or backfill it.
   The activation checklist below is mandatory before creating the activation
   file and enabling `hqa-fw001-daily.timer`.
7. Copy the immutable forward directory/blobs and freeze file to the PC.
   Preserve file **and directory** mtimes, and verify completion proofs on the
   copy; these timestamps are part of publication evidence.
   After the freeze, run the PC evaluator; publication is explicit:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
     venv/bin/python -m backtesting.experiments.fw001 \
     --data-dir /path/to/server-data-copy --pc-data-dir /path/to/PC-freeze \
     --final --output research/experiments/FW001_forward_replication/results/final.json
   ```

### Real-server activation checklist (not executed in this worktree)

- Compare the transferred manifest's SHA-256 with the independently recorded
  **second-fix-round final seal**, not the earlier measured/reviewed seal.
  Use the exact SHA-256 recorded in the second-fix-round validation below.
  Record that hash with all server results. Run `collector_manifest.py --verify`
  before and after the checks; verify transfer/install includes
  `backtesting/append_only.py`, `src/runner/trading_calendar.py`, both FW001
  units and both manifest audit directories. Use the isolated deployment
  smoke test with only manifest-listed source files, not the development repo.
- Confirm calendar notices and sell/settlement costs for any dates being
  exercised. Pending CSAT/2027 coverage must produce explicit waiting/missing
  states, never invented sessions or prices.
- On the actual 961 MiB server with ordinary DART collection/refresh workloads,
  run the read-only real-data rehearsal with `/usr/bin/time -v` and the
  service's `MemoryMax=800M` / 15-minute timeout. Record wall time, peak RSS,
  cgroup `MemoryPeak`, swap/OOM events and concurrent job status. Check disk
  capacity and separately measure first-publication snapshot I/O in a scratch
  data copy: the read-only replay does not measure blob writes/fsync latency.
- Verify the unchanged weekday 08:40 previous-day collector succeeds, and
  Saturday/daily-holiday catch-up recovers a Friday month-end. No job requests
  today. Check 2026-11-18/19/20 boundaries: the pending 11-19 must not block
  normal 11-18 catch-up; FW001 must reject unverified 11-19 data. Collector
  success queues the recorder once its KRX day is complete/verified. Measure
  entry-morning collection **plus** decision sealing comfortably before 08:59
  (operational target: by 08:45), including holiday recovery.
  Confirm FW001 and HC002 have independent timeout accounting and run in order.
- In an isolated scratch tree with the same limits and **owned positions**,
  interrupt after one scenario and before its marker, then after the 1x marker.
  Delete/corrupt the mutable entry source and restart: sealed bytes must finish
  1.5x/2x without repricing, duplicating fills or resetting holdings/cash.
  Validate each actual-byte digest and actual execution `sealed_at`. Also
  interrupt between decision publication and proof: a restart after 08:59 must
  produce reconstructed/missing status without a valid decision marker.
- Using this same final code, test the actual publication filesystem in the
  scratch tree: prove kernel directory mtime error/granularity stays within
  the declared one-second bound. Interrupt immediately after the atomic marker
  link, before directory fsync; before-deadline evidence remains valid after
  restart, while at/after-deadline or precision-ambiguous evidence fails.
  Check that no published marker is removed and `sealed_at` comes from the
  kernel publication metadata. Repeat a delayed link across 08:59. Preserve
  mtimes when copying to the PC and validate the copied proofs as well.
- Only after recording these results, create `/etc/hqa/fw001-activated` and
  enable the dedicated timer. The KRX `OnSuccess` trigger also observes this
  gate. Until then, keep the activation file absent and the timer disabled.

## Files changed for independent review 16

| Files | Purpose |
| --- | --- |
| `src/forward/fw001.py` | Shared bounded calendar, completion proofs, immutable input blobs, sealed-price recovery and explicit state/calendar omissions |
| `backtesting/experiments/fw001.py` | Proof-aware reading, freeze-length re-trading detection, registry prevalidation and mandatory seal verification |
| `scripts/ops/collector_manifest.py`, `push_collector_code.sh`, `deploy/collector/install.sh` | Complete module inventory, linked server/PC amendments, matching transfer/install and verification |
| `scripts/ops/krx_catchup.py`, KRX units | Previous-day collection with weekend/holiday catch-up and completion triggers |
| `deploy/collector/systemd/hqa-fw001-daily.*`, shadow units | Dedicated gated FW001 service, independent timeout and ordered HC002 execution |
| `tests/test_forward_fw001.py`, `test_experiments_fw001.py`, `test_ops_fw001.py`, `test_shadow_ops.py` | Interrupted-publication, input restoration, source corruption/deletion, registry, calendar and isolated deployment regressions |
| `deployment_manifest.json`, `manifest_history/`, `manifest_changes/` | Final source seal with preserved preceding seals and exact amendment hashes |
| `resource_replay_guard.py`, `dry_run_measurement.json`, deployment documentation | Read-only measurement evidence and the real-server activation checklist |

Known holdings/cash remain available for audit when monthly price history is
missing. Their original chain date is retained; an old, unvalued chain cannot
create a new execution or an invented monthly return. An unverified entry date
cannot execute. The pre-existing `venv` symlink is unchanged. No preregistration,
comparison value, experiment registry or main-checkout file was modified.

## First-fix-round validation results (superseded seal)

The requested FW001, hegemony_shadow, calendar and registry selection passed
**281 tests** (568.47 s). After final input/proof and PC-manifest hardening,
all **67 FW001 recorder/evaluator/ops tests** passed (134.26 s), including the
12-month accounting/freeze integration and isolated deployed-tree dry run.
The final stale-chain/unverified-entry guard then passed **49 recorder/ops
checks** (81.77 s), including an owned-position omission followed by partial
price recovery. These are successive validations; no single larger combined
final invocation is claimed. All evaluation/control tests use explicit local
fixtures. Hypothetical 2027 calendars are confined to accounting fixtures;
production guard tests still reject 2027 sessions.

Shell syntax and the 16:10 / registered midnight systemd calendars passed.
All **193 deployed files** and **six PC implementation files** match the
final seal, including both linked amendment documents and preceding manifest
copies. The original preregistration, comparison values and registry have no
diff. The manifest is locally sealed, with no commit, server connection,
deployment or activation. Its SHA-256 is
`fe75e1460a55ce97a7fe77494532e436f244f4029fcd6b35266e50b338c06ca4`.

## Local real-data resource measurement

The first-fix-round tree (seal `fe75e1460a55ce97a7fe77494532e436f244f4029fcd6b35266e50b338c06ca4`,
superseded by the second fix round) rehearsed **2026-09-30** and the **2026-10-01** entry
for all three cost scenarios, read-only, from
`/home/are01/src/HQA_Project/HQA_Project/data`. The auditable command is in
`dry_run_measurement.json`; `resource_replay_guard.py` invokes the regular
recorder CLI while rejecting original-data mutations, .env reads, protected
2026H1 price contents and networking. Python bytecode writes were disabled.

GNU `/usr/bin/time -v` measured **162.58 s wall time**, including startup,
and **565.46875 MiB peak RSS** (579,040 KiB). The recorder reported 161.639 s
calculation time. The run exited 0 with no swaps, filesystem outputs or socket
messages. The audit observed 28,533 data read opens and **zero** data-write,
env-read, protected-price-read or network attempts. Requested pytest processes
had finished before this replay; other host activity was not controlled.
The prior 298.744 s run had pytest co-load and the old calendar, so this is
not a same-condition speed-improvement claim.

Both universes contained **1,492** names. With the shared July 17 closure,
there were **no missing price sessions** and **no uncomputable volatilities**;
f_both selected **464** targets and av_all **465**. There were **6,444 input
files**, global listing lag L was **17**, and all three execution rehearsals
had defined costs / executed accounting. These are resource-rehearsal outputs
using currently stored versions, not an historical as-of performance estimate
or an FW001 observation. No historical experiment, control path, holdout
return or economic verdict was run on real data.

The read-only run does **not** measure snapshot publication/fsync latency,
cgroup memory under concurrent collection, or real-server restart recovery.
Those remain the mandatory real-server activation checks above. The dedicated
service retains 800M and its own 15-minute timeout; local RSS alone does not
establish server readiness.

## Second-fix-round validation (review 17)

P1-1 restores previous-day-only collection, including the original weekday
08:40 request path. Saturday 08:40 and daily 18:30 recovery also request only
previous days. There is no same-day option or evening same-day unit. Offline
tests execute the real catch-up/backfill/collector/normalisation/save path with
fake HTTP responses, including weekends and 2026-11-18/19/20.

P1-2 checks special-session coverage only for returned completed sessions.
Both mutable and frozen FW001 price windows independently reject unverified
row statuses and official-calendar gaps, including a pending historical day
inside an otherwise normal later day's lookback. The chain-accounting test
uses explicit hypothetical calendar coverage; production refusal tests do not.

P1-3 uses one atomic marker publication with kernel directory timestamp
evidence, a pre-link deadline check, and no marker revocation. Regression tests
cover interrupted candidates, native `os._exit` immediately after the link,
delayed publication past the deadline, precision-ambiguous publication,
preserved-mtime copies, and nanosecond boundaries at the registered freeze.
The one-second publication precision bound and directory-mtime preservation
are mandatory parts of the proof contract described above.

P1-4 retains the activation-file condition and disabled-by-default FW001 timer.
The checklist requires the exact final seal for server collection timing,
concurrent memory/I/O, interruption/restart and publication-clock checks.
The earlier real-data resource rehearsal belongs to its earlier recorded seal;
it does not validate this final code for activation.

The final combined invocation passed **493 tests in 294.31 seconds**:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
venv/bin/python -m pytest -q -x -p no:cacheprovider \
  tests/test_forward_fw001.py tests/test_experiments_fw001.py \
  tests/test_ops_fw001.py tests/test_ops_collector.py tests/test_shadow_ops.py \
  tests/test_research_ops.py tests/test_krx_market.py \
  tests/test_trading_calendar_overrides.py tests/test_forward_hegemony_shadow.py
```

The final sealed manifest verifies **201 deployment files and seven PC
implementation/test files**, including preserved preceding seals and linked
amendments. Its SHA-256 is
`fcc79d783063e6ee1d63cba27864cdaea1814bd8bced4b12d5a0d6f0656f4172`.
Shell syntax, systemd calendar expressions and `git diff --check` also pass.
Preregistration, comparison values and the experiment registry have no diff.

This round made no server connection, deployment, activation, real-data write,
`.env` read or project commit. Real-server verification remains pending; the
activation gate must remain in place until the final-code checklist is met.

## Third narrow fix: rejected completion timing

The shared completion reader now returns no eligible observation for an intact
marker published late or within the cutoff's precision boundary. Digest,
deadline, publication metadata and timestamp-consistency errors still raise.
The rejected candidate and marker remain unchanged audit evidence; restart
paths never re-complete them. Section 6 missing/no-trade accounting continues
through subsequent months, the data freeze and final evaluation. The evaluator
uses the shared reader's existing no-proof exclusion path.

Synthetic annual regressions cover both late and precision-boundary publication
followed by a crash, restart, normal November recording and final evaluation.
They verify unchanged candidate/marker bytes and publication mtime, no repeated
completion, October's missing/no-trade transition, 11 eligible f_both months
and 12 av_all accounting months. The absent October universe keeps av_all
`insufficient`; no benchmark is invented. Corrupt digest, deadline and precision
metadata cases fail loudly in recording and evaluation/freeze checks.

The same nine suites listed above passed in separate invocations: **51 recorder
tests**, **24 evaluator tests**, and **423 ops/adjacent regression tests**,
for **498 passing tests**. The recorder and evaluator invocations took 66.44 s
and 228.51 s; the ops invocation took 144.49 s. These are local synthetic test
timings, not server resource measurements. `git diff --check` also passes.

The third-round seal preserves the preceding manifest and links exact changes
to one deployed recorder file and two test files. A second linked amendment
corrects the annual fixture's trigger time because it preloads future entry
prices. The final manifest verifies **205 deployment files and seven PC
implementation/test files**. Its SHA-256 is
`2d9b62fa23a651716b9738e43fd8a174656696dcb10b4d5e5ab6c9b7634ae8aa`.

This round made no network connection, deployment, activation, `.env` read,
project commit or real-data write.
