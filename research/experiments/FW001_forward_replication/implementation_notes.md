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
| `decisions/YYYYMM.json` | Targets, weights, source/code hashes, financial status, finalisation time and state |
| `executions/YYYYMM_1.0.json`, `_1.5.json`, `_2.0.json` | Separate immutable scenario fills, quantities, cash, charges, quotes and chain |
| `missing/YYYYMM.json` | Counted omission and frozen no-trade transition inputs |
| `reruns/<sha256>.json` | Explicit reconstructed audit only |
| `cost_resolutions/YYYYMM_<multiplier>.json` | Completion of previously undefined costs/state, referencing the original execution hash |
| `price_versions/YYYYMMDD/*.json`, `snapshots/<sha256>.jsonl` | Immutable server revisions and content-addressed bytes |
| `execution_prices/YYYYMMDD.json` | Original price input shared by the three execution scenarios |
| `evaluation_freeze.json` | Inventory/hashes of immutable inputs existing by the registered freeze |

Decisions are staged, fsynced and atomically linked without replacing an
existing file. The clock is checked after all calculation and after durable
publication. Crossing **entry-session 08:59 KST** withdraws the candidate under
the recorder lock and publishes it as `reconstructed`. Equality is late.
Neither HC002's three-session grace nor `--backfill-label` exists in FW001.
An explicit rerun cannot create an observation execution. Scenario recovery
uses the same frozen entry copy, even if the mutable KRX archive changes.
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

The existing `src/runner/trading_calendar.py` refuses official special-session
verification from **2026-11-01**. Therefore the first **2026-11-02 execution**
cannot pass verified-day checks until the operator records official KRX notice
sources/effective dates and mechanically updates that guard. This work does
not promote unverified prices or invent official hours/holidays. The bundled
XKRX session plan currently gives 2026-11-02 first entry and 2027-11-01 last
f_both close, agreeing with FW001.

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
liquidation or re-execution. State and decision-hash continuity are checked.
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

Publication creates one canonical `results/final.json` and one registry row
per judged rule. A restarted publication can finish a missing registry row;
it cannot replace the final result. All real-data post-hoc evaluation is left
for the registered date; current validation uses fixtures only.

## Deployment procedure (operator steps; not executed here)

1. Review and seal the implementation and `deployment_manifest.json` before
   the first decision. The manifest hashes every actual server transfer file
   and separately lists PC evaluation/test implementation hashes. Its own
   hash is excluded to avoid recursion; record that SHA-256 with the seal.
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

5. The KRX timer retains weekday 08:40 and adds **Saturday 08:40** and **daily
   18:30 KST**. Catch-up still requests through yesterday and handles empty
   holiday/weekend days without inventing bars. Successful KRX completion
   triggers shadow via `OnSuccess`; `After` waits for a running KRX job. The
   existing 08:55/12:30 shadow timer remains a recovery check, not evidence of
   completion. A dated 2027-11-15 00:00 timer seals the evaluation inputs.
   Verified daily data is checked again by FW001. The oneshot
   runs FW001 first, then existing HC002 in a separate process, retaining
   `MemoryMax=800M`, private networking and existing protection. Its explicit
   10-minute timeout accommodates the measured decision instead of systemd's
   usual 90-second start timeout. FW001 failure
   remains visible in JSON/traceback/journal; its prefixed systemd command lets
   HC002 continue. Neither job submits orders.
6. Before enabling the first decision, measure completion time / cgroup peak
   on the actual **961 MiB** server with its DART workloads present, and
   exercise interruption/restart between scenario files. Local standalone
   RSS is not a server concurrency measurement. Verify no cgroup OOM or
   restart caused a missed cutoff. An unready/missed October decision stays
   a counted omission; do not extend the experiment or backfill it.
7. Copy the immutable forward directory/blobs and freeze file to the PC.
   After the freeze, run the PC evaluator; publication is explicit:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
     venv/bin/python -m backtesting.experiments.fw001 \
     --data-dir /path/to/server-data-copy --pc-data-dir /path/to/PC-freeze \
     --final --output research/experiments/FW001_forward_replication/results/final.json
   ```

## Changed files

| Files | Purpose |
| --- | --- |
| `src/forward/fw001.py` | Current decision/state, immutable executions, omissions, revisions and freeze |
| `backtesting/experiments/fw001.py` | PC evaluation, reporting controls/sensitivities and final publication |
| `scripts/forward/fw001.py`, `scripts/ops/fw001_daily.py` | Explicit read-only rehearsal and scheduled recording |
| `backtesting/experiments/hf001.py`, `backtesting/experiments/hc003.py` | Reuse sealed HF001 fills and carried HC003 chain/no-trade state |
| `backtesting/cost_model.py` | Buy-only forward support; unknown sell/settlement years still fail |
| `src/ingestion/dart_financial_date_rules.py`, `src/ingestion/dart_quarterly.py`, `scripts/ops/fundamentals_refresh.py` | Bounded receipt provenance, batch status and distinct request/status clocks |
| `scripts/ops/collector_manifest.py`, `scripts/ops/push_collector_code.sh`, `scripts/ops/push_fw001_supplement.sh` | Hashed code inventory, transfer modules and supplement sync |
| `deploy/collector/install.sh` | Matching server module/artifact installation |
| `deploy/collector/systemd/hqa-krx-daily.service`, `hqa-krx-daily.timer` | Completion trigger and weekend/holiday catch-up schedule |
| `deploy/collector/systemd/hqa-shadow-daily.service`, `hqa-shadow-daily.timer` | Sequential FW001/HC002, timeout and final freeze timer |
| `docs/collector-server.md`, this file | Deployment prerequisites, interpretations and blockers |
| `tests/test_forward_fw001.py`, `tests/test_experiments_fw001.py`, `tests/test_ops_fw001.py` | New recording, evaluation and deployment fixtures |
| `tests/test_backtesting_cost_model.py`, `tests/test_dart_quarterly.py`, `tests/test_shadow_ops.py` | Changed cost, batch-status and transfer expectations |
| `deployment_manifest.json`, `dry_run_measurement.json` | Preparation seal candidate and final real-data resource evidence |

The pre-existing `venv` symlink is unchanged. No preregistration, sealed
comparison value, experiment registry or main-checkout file was modified.

## Validation results

The complete requested/touched selection ran **864 tests: 862 passed, one
optional archive check skipped, one weekly-refresh midnight regression
failed**. The failure was fixed by reusing the request-admission timestamp
and separating the success-status clock from the midnight API guard. The
subsequent **195-test regression selection passed**, including the formerly
failing test, quarterly collection, shadow operations, financial dates and
FW001 recorder/deployment fixtures. Thus all exercised test cases pass after
the fix; a second complete invocation was not claimed. Evaluation runs used
synthetic fixtures only. The optional committed-HC003 archive check remains
opt-in and was not enabled.

Shell syntax, the exact dated systemd calendar, untouched preregistration /
comparison diffs, all **186 deployed file hashes** and four PC implementation
hashes were checked. The 2027 cost-readiness command deliberately exits 1
with the legally-sourced-rate TODO. Manifest status remains
`prepared_for_sealing`; it has not been committed or deployed.

## Local real-data resource measurement

One read-only process rehearsed the latest available month end **2026-09-30**,
then **2026-10-01** open for all three cost scenarios, from
`/home/are01/src/HQA_Project/HQA_Project/data`. It calculated no historical
experiment, control path, holdout return or FW001 pass verdict. Audit hooks
blocked writes to that data root, reads of `.env`/`.env-ai`, all protected
2026H1 price contents and outbound networking; Python bytecode writes were
disabled. `/usr/bin/time -v` reported **zero filesystem output** and no socket
messages. The successful run measured **564.324 MiB peak RSS** and **148.609 s
calculation time** (149.70 s including startup), below the 800M service limit.
This precedes the final manifest and small recording/recovery refinements;
the final source measurement is recorded in `dry_run_measurement.json`.

Both universes had 1,492 names; f_both had 586 targets and av_all 588. Global L
was 17. The 60-session window contains a missing **2026-07-17** file, so
volatility is uncomputable instead of filled; this is a resource rehearsal,
not an October observation or evidence of investment performance. No missing
bar, holiday or financial batch timestamp was invented.
