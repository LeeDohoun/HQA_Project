# XO001 implementation and coordinator runbook

Registration: `0f4152020383679f0ff7865ddcf80d02621834d8`.
The preregistration SHA-256 is
`96d36b5721d88abd6f34a348d9927f8aeeb11f116eca3db3ff9949b4b002db8b`.
This implementation does not edit registration, BB001, BB003, or the FW001 inventory.
No live provider qualification or deployment was performed during implementation.

## Fixed collection contract

REST only; WebSocket is disabled. No WebSocket host, port, TR or subscription is
authorized by this implementation. Approval is a capability probe whose key is
redacted immediately; it does not enable a WebSocket or qualify the recorder.

The only network origin is `https://openapivts.koreainvestment.com:29443`.
The client and the underlying HTTPS transport both reject other origins,
credentials in URLs, fragments, query strings in endpoint URLs, path aliases,
redirects, wrong methods and unlisted TRs. Request parameters are encoded
separately. The transport ignores proxy environment settings and uses ordinary
TLS certificate verification. There are no provider retries or live-domain
substitutions.

| Method/path suffix | TR |
| --- | --- |
| POST `/oauth2/tokenP` | OAuth only |
| POST `/oauth2/Approval` | Approval probe only |
| GET `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` |
| GET `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` | `FHKST01010200` |
| POST `/uapi/domestic-stock/v1/trading/order-cash` | `VTTC0012U` buy, `VTTC0011U` sell |
| POST `/uapi/domestic-stock/v1/trading/order-rvsecncl` | `VTTC0013U` |
| GET `/uapi/domestic-stock/v1/trading/inquire-daily-ccld` | `VTTC8001R` |

`VTTC0081R` is not implemented as a fallback. The coordinator must verify the
chosen `VTTC8001R` contract, pagination flags, fields and PAPER support in the
sealed official specification and shakedown. An unsupported TR remains a failed
capability; the software never changes TRs automatically.

Credentials come only from the five `XO001_KIS_PAPER_` environment variables:
`APP_KEY`, `APP_SECRET`, `CANO`, `ACNT_PRDT_CD`, `HTS_ID`. Systemd loads
`/etc/hqa/xo001.env`, provisioned separately by the coordinator as root:root 0600.
Application code never opens an environment file or imports project settings.
Use a dedicated key and an account different from M8; the coordinator attests to
these separations without copying M8/collector credentials into XO001.

A lifetime flock grants one owner process per app-key hash in the single private
data root. Owner-only `auth/<key-hash>.json` contains the token, response expiry
and last issuance attempt. The parent is 0700 and the cache is 0600; symlinks and
incorrect ownership/modes fail before authentication. Absolute response expiry
is preferred; an explicitly returned numeric `expires_in` uses request time plus
that duration conservatively. Missing/invalid expiry fails. A persisted attempt
timestamp, written before transmission and updated at actual transmission,
enforces at least 60 seconds between issuances, including failed/crashed calls.
Tokens, Approval keys, credentials, account numbers and HTS IDs are redacted from
response objects and raw response text. Auth response bodies are never recorded.
Provider/transport exception text is not logged. Only identifier hashes are public.

All HTTP calls, including authentication, use a flock-backed persisted rate
manager: **one grant per second**. Shakedown must confirm the documented PAPER
ceiling is strictly greater than this fixed budget. Pending priority is event,
matched, baseline; within each capture class the order is stock code, book then
price. Already transmitted requests cannot be preempted. Identical symbols
across roles share a request at their highest priority; role denominators retain
each planned selection. Expired slots/deadline failures are recorded, never
replaced by another stock. Multiple processes cannot reset the budget.

Normal-day schedule, also pinned in `activation.CONFIG`:

| Time | Action |
| --- | --- |
| 08:23 | Service starts; preload prior-day population only |
| by 08:25 | Seal the available candidates, frozen draws and source hashes with a kernel publication bound at or before 08:25 |
| 08:25 + 30-second slots | Book and current-price REST polls, fixed priority |
| 08:30 / 08:50 / 08:55 / 08:58 / 08:59 | Latest received book as of each cutoff |
| 08:58:55 | End pre-open HTTP calls; retain fixed order/seal time budget |
| 08:58:57 | If enabled, seal order intents from already completed BB003 decisions |
| 08:59 | Read the entire timely BB003 pre-executability denominator and late events |
| by 08:59:30 | Publish 08:59 snapshot and pre-open hash manifest atomically |
| 08:59:05 to before 08:59:30 | Conditional, one-attempt PAPER buys only |
| 09:00 through before 09:05 | Last-print REST polls in fixed five-second slots |
| from 09:01 | Query pending orders, cancel known remaining quantities, query confirmation |
| 09:05 | Close separate post-open record |
| from 15:20 | Query unresolved orders; fixed-code-order confirmed-quantity market sells |
| 15:20 + 2/10/30/60 seconds | Fixed inquiry opportunities; stop when fully cleared |
| 09:30 and 18:15 | Retry every pending observation day for separate KRX daily reconciliation and BB003 omission acquisition |

Slots never borrow future budget. Lower-priority or excess symbols can remain
unobserved; a five-second slot cannot cover an unlimited roster at one request
per second. No priority or polling rate changes in response to prices or results.
Post-open data are **sampled individual prints**, not an exhaustive trade tape.
The record contains provider time, price, quantity, receive time, the earliest
observed print and first-minute sampled prints. It never sums sampled last-print
responses into market volume. `first_60s_total_volume` remains unavailable.
The earliest sampled print is not asserted to be the exchange's first print.
`first_print` is populated only by a provider event with verified date, KRX venue,
event time, market status and either a verified opening-auction indication or an
explicit official first-trade indication. An ordinary last-price REST sample
cannot complete the post-open observation. A confirmed delayed first trade at
09:02 within the capture window completes this component; its provider market
state establishes opening delay, independently of the receive time. Official
halt/no-trade evidence yields `confirmed_absent`; collection failure or missing
first-trade evidence yields `unknown`. Samples remain separately available in
`earliest_sampled_print` and `first_60s_sampled_prints`.
Only a verified official auction flag/volume definition permits auction price
and auction volume; otherwise they are unavailable. Receive delay alone does
not establish opening delay. NXT or unverified venue records cannot supply KRX
measurement pairs.

## Field contract and live qualification

There is **no supplied live field-profile default**. A price snapshot or
`acml_vol` cannot substitute for a timestamped individual trade. In particular,
the two allowed REST quotation TRs may not expose all required fields in PAPER.
If the live response cannot prove date/session/venue or trade time/quantity,
keep the relevant checks false and do not activate. The preregistered outcome
is `not_started` if main-key observation qualification misses November 30.
Adding a feed would require an explicit reviewed implementation/specification
and the permitted PAPER host/port/TR qualification before use; this code never
enables a feed implicitly.

Prepare a JSON field profile against actual official documentation and sanitized
PAPER samples. Its exact top-level keys are:

```text
book, trade, preopen_values, krx_values, halt_values, delay_values,
auction_values, auction_volume_definition, official_spec_sha256
```

`book` and `trade` map semantic keys to dotted JSON object paths, without code,
constants, inferred dates, or fallback fields. `book` requires `date`, `exchange`,
`session`, `provider_time`, `expected_price`, `expected_volume`, `expected_code`,
`status`, `vi`, `upper_limit`, `lower_limit`, `total_ask`, `total_bid`, and
`ask_price_1..10`, `ask_quantity_1..10`, `bid_price_1..10`, `bid_quantity_1..10`.
`trade` requires `date`, `exchange`, `provider_time`, `price`, `quantity`, `status`;
optional `auction_flag` and `auction_volume` require the official definition.
An optional `trade.first_trade_flag` mapping and top-level `first_trade_values`
list may identify an officially documented first-trade event. Neither is
supplied by default; missing evidence stays unknown. The first-trade indication
requires a nonempty provider market status as well as event time and venue.
Dates must be `YYYYMMDD` or `YYYY-MM-DD`; event time must be valid `HHMMSS`.
Enumerations are exact provider strings verified by the coordinator. Missing
fields yield `unknown`, not zero or suspension. A current-date KRX official halt
is `confirmed_absent`; no-response is `unknown`.

The profile includes the SHA-256 of supplied official-specification bytes. Seal
those exact bytes with `profile-seal`. The profile and test plan cannot be
replaced after the first shakedown. Coordinator attestations are reviewed
claims bound to immutable sanitized evidence blobs and the exact tested
code/environment/key/account/profile/config hashes; they are not automatically
inferred from a successful HTTP response.

## Roster, BB003 and source availability

`BB003` is a reader, never a BB003 `Store` or writer. It checks forward labels,
original kind/key, payload hashes, private-directory completion markers,
kernel publication time plus the full one-second bound, deadline inclusivity
and the requested as-of time. Inputs are snapshotted only inside XO001 blobs.

Before the 08:25 publication boundary, the latest timely `preparations/<receipt>.<version>` with status
`prepared` supplies candidates. Prior 20 KRX daily sessions supply the BB001
pre-entry rules exactly: common-share code ending in 0, KOSPI/KOSDAQ, nonempty
name excluding 스팩, all 20 verified rows, positive previous close at least 1,000,
nonnegative complete trading values with mean at least 100 million won, complete
adjusted returns no less than -1, positive market cap. Reduction order matches
BB001; a test executes its frozen pure AST functions for parity without loading
its settings. No entry-day price/limit/volume filter enters these populations.

The prior 64 session dates implement BB001 receipt age 0..63. Daily DART listing
files (including non-session days) and the completed-day checkpoint must prove
coverage; exact BB001 title matching excludes 신탁/정정/철회/취소. Known event
codes are also excluded from the non-event pool. A partial population is not
sampled. Input size/mtime must be unchanged across a read and available before
the roster cutoff including the conservative one-second bound.

Market-specific boundaries use the sorted eligible non-event pool,
`numpy.quantile(..., method="inverted_cdf")`, size thirds and prior20 fifths;
boundary equality goes to the lower bin. Each event draws up to two same-market,
same-size, same-return-bin stocks without replacement using
`default_rng(int(rcept_no))`. There is no BB001 size relaxation. Baseline draws
use one `default_rng(int(YYYYMMDD))`, KOSPI then KOSDAQ, small/medium/large,
one draw per cell, without replacement within that draw. Empty cells do not
consume a substitute draw. Candidate codes are sorted; RNG output order is
preserved. NumPy's version is in both roster and environment binding.

The morning worker finishes roster selection before the boundary (normally
begins the final selection at 08:24:58), leaving room for fsync and the full
one-second kernel precision bound. The evaluator independently checks the
roster's actual kernel `publication_not_after <= 08:25` (shifted for special
opens), even for legacy records without a deadline. A forward label alone is
insufficient. Late rosters and reconstructed snapshots remain auxiliary; their
primary events stay in the denominator with incomplete observation and no
primary price pair. No late recovery replaces the original roster.

The 08:25 roster never becomes the main denominator. All **included BB003
decisions completed before 08:59, before entry-day executability**, supply it.
Late events get separate records and post-open observation where possible;
the original roster and draws are never replaced. Late order candidates absent
from the sealed intent set are not submitted. BB003 `data_not_ready`/omissions
have an additional XO001 omission ledger. Final evaluation independently scans
the global timely BB003 decision inventory, so an entire XO001 outage day
cannot disappear. BB003 post-entry exclusions never remove primary events.

## Records, clocks and recovery

All state is in `/var/lib/hqa-xo001`; shared BB003/KRX/DART roots are read-only.
Code is `/opt/hqa-xo001`; runtime refuses a shared code root, shared venv or
alternate live output root. No heavy lock is used. All services have 384M
MemoryMax, no swap, low CPU/I/O weight and one numerical-library thread.

`records/<kind>/<key>/<hash>.json` and `sealed/<kind>/<key>/complete.json` are
no-clobber files. Completion is a single hard link from an fsynced staging file
outside the private marker directory, followed by directory fsync. Only the
completion marker may be in that private directory. Its kernel mtime plus one
second must fit the deadline; a too-late marker stays invalid permanently.
`activation.json` is a fixed-path record with the same separate completion
proof. No invalid marker or completed record is replaced on restart.

Raw blocks contain UTC request/receive time, both monotonic times, provider
fields/time, wire sequence, per-request REST connection ID, raw sanitized body,
parsed body, HTTP/provider/transport errors and kernel clock evidence. Each
block and each fixed-slot attempt is durable before progressing. Restart has a
new connection ID, preserves old blocks/markers and does not resend an already
attempted slot or order. An attempt without a known outcome remains explicitly
unobserved/UNKNOWN. Raw request budget/deadline failures are also recorded.

Linux `adjtimex` supplies synchronization status, estimated offset and maximum
error. The pinned acceptable error bound is 0.5 seconds. Unverified clocks make
observations unknown and prohibit diagnostic orders. Pre-open receives after
08:59 and late recovered blocks are separate `reconstructed` records; they
never supplement the 08:59 manifest. Post-open collation recovered more than
30 seconds after 09:05 is labelled reconstructed and excluded from completion.

## Calendar and order state

The corrected shared XKRX calendar is used for needed verified dates only,
including the project corrections for 2026-06-03 and 2026-07-17. Pending special
hours, such as 2026-11-19 before an official notice, fail closed. No future
calendar horizon is required at startup. Add data-only official KRX records
for required dates with `calendar-add`: `date`, `session`, `times` for open
days, `source_url`, `published_at`, plus exact notice bytes. A future normal day
uses the normal times explicitly; a special day supplies every key in
`calendar.TIME_KEYS`. Data updates are immutable and source-bound.

Special observation times shift explicitly. Conditional primary buys are
restricted to normal days; special-day order policy is no new buys, cleanup at
the explicitly supplied flatten time. The cleanup dispatcher waits until that
time. Unverified days record missing data and send no orders. Scheduled baseline
slots include verified special sessions. The final evaluator alone requires the
full completed observation calendar and the first session after 2028-09-30.

The normal diagnostic gate requires both a successful sealed decision and
`activation-control/diagnostic.enabled.json`, installed **only by the coordinator**
as root:hqa 0640 inside a root:hqa 0750 directory mounted read-only to services.
The CLI never creates that flag. No flag means OFF. A failed November diagnostic
decision cannot be turned on during the main period, even by adding a flag.

Test plan schema: `tests` (at least three distinct verified normal November
dates), `main_account` (a separate additional predetermined normal November date), and
`account_policy`. Every test has `date`, `stock_code`, `market_order_type="01"`,
`limit_order_type="00"`, positive integer `limit_price`. Choose a genuinely
predetermined unfilled limit test; do not fill a success quota by substituting
dates. Policy keys: actual `main_valid_through`, `competition_valid_through`
exactly `2026-11-20`, `replacement="forbidden"`, `renewal="forbidden"`,
`on_expiry="disable_buys_preserve_state_cleanup_same_account"`. Expiry blocks
new buys; there is no account replacement or reset. General tests may use
competition or main credentials; the separate additional main-account test
must use main credentials. Main observation qualification uses the main key.

The strict API allowlist has **no balance TR**. The coordinator must supply a
sanitized, verified account screen/export; daily-order inquiry cannot substitute
for holdings/cash verification. `account-evidence` requires exactly:

```text
date, as_of, account_sha256, positions (code -> integer shares), open_orders,
cash, buying_power, margin_per_share (code -> verified positive won),
settlement_basis, source_sha256, coordinator_verified=true
```

`as_of` must lie between the day's roster time and 08:59, and not in the future.
The account hash must match the active credentials. Hashes are in the run binding;
they never reveal the account. Use an empty positions object and empty open-order
list only when the actual account has neither. The source is an imported,
sanitized evidence blob. `import-evidence` and `account-evidence` can run while
the recorder holds its separate recording lock, using disjoint no-clobber paths.

No account proof, cash, buying power or verified margin means an explicit
non-submission. Existing positions, UNKNOWN orders and prior open orders block
buys. Previous unresolved orders are reconciled before new intents. An intent
and attempt precede a single HTTP submission; a provably untransmitted request
is non-submitted/late, provider refusal is rejected, and ambiguous transmission
is UNKNOWN. No arbitrary resends occur. Acceptance never means fill.
Daily inquiry consumes all pages, records raw fields/page sizes, validates
identity/quantity and keeps incomplete inquiry unknown. Cancel acceptance is
not cancellation: final state and zero remaining quantity must confirm it.
Flatten sells only confirmed holdings, in code order, at the fixed time, and
does not duplicate a pending/UNKNOWN sell. Fully cleared requires zero holdings
and no unresolved orders. Residuals block buys and use the same cleanup next
verified day, on the same account. These records never affect BB003 or signals.

## Measurement evaluator

Run only after the first session after receipt END plus seven calendar days,
23:59:59 KST. This resolves the preregistration's seven-calendar-day freeze at
the end of that seventh day, consistent with its date-based collection period.
Before END the evaluator refuses without asking for the future calendar.
Reconcile and evaluate verify the current source inventory, interpreter and
dependency bytes, host, field contract, fixed configuration, activation proof
and approved bug-fix amendment chain before reading measurement inputs, and
repeat verification immediately before sealing. These offline paths do not
load credentials; the activation's identifier hashes remain linked. Every
reconciliation and final report includes `verification.binding`, the activation
record hash and the ordered approved-amendment record hashes. Invalid or changed
bindings refuse the operation instead of publishing results.

One immutable final evaluation is published. It is followed by exactly one
`measurement` variant row in `research/experiments/registry.csv`, using the
existing `backtesting.experiment_registry.record_trials` API and a
`measurement_complete` or `measurement_insufficient` verdict. Compact registry
metrics link the sealed result hash/path, code/environment hashes, activation
hash and approved amendment hashes. The full statistics remain in the sealed
result. No result is sent to the strategy judge or profit tests.

Recovery verifies the current runtime, reads the existing sealed result and
registers it without rereading BB003/calendar inputs or recomputing statistics.
The API rejects duplicate variants under its registry lock; recovery validates
an existing matching row and refuses conflicting rows. A crash before append
or after append is safe to retry. Final CLI execution requires an explicit
`--registry-root` pointing to the authoritative local Git checkout, where the
registry API verifies the immutable preregistration. The deployment wrapper
makes only that checkout's `research/experiments` directory writable for this
final command. The checkout must be outside ProtectHome's hidden directories;
`/srv/hqa-research` below is an operator-supplied example, not a created copy.
No registry writes were performed on this implementation checkout.
Frozen main-account order-flow counts, inquiry completeness/page sizes and
source hashes are separate secondary outputs; November records do not enter them.

Completion requires a timely kernel-verified roster, all five timely, hash-verified
forward snapshots and a confirmed provider first-trade event within the post-open
capture window or confirmed official absence. Unknown segments do not count.
`confirmed_absent` counts as observed, but never creates a price pair. A valid
pair requires a positive verified same-day KRX 08:59 pre-open expected price
and the matching verified KRX daily open. It does not require the daily
executability proxy to be true. Primary paired companies/days alone supply the
40-company/50-day thresholds; matched/baseline and orders do not contribute.
The other thresholds are 100 pairs and coverage at least 0.95, all required.

Coverage, delay and outage rates over the whole primary denominator are reported
first. Price statistics retain both paired and whole-event denominators:
`10000 * (KRX_open / expected_0859 - 1)`, median, 5/10/25/75/90/95 percentiles
using NumPy linear, and strict absolute exceedance of 10/25/50 bp. Whole entry
dates are resampled 2,000 times with `default_rng(0)`; percentile 95% intervals
also use linear quantiles. Company/date counts disclose concentration; these
intervals specifically model date clustering and do not claim independent
company observations across dates.

The daily proxy uses open presence, positive volume and BB001's 30% boundary
against valid base price or the sealed pre-close. It is cross-tabulated with
auction-confirmed/delayed/official-halt/unknown observations, never used to
remove a primary event. Reconciled auction/daily prices are a data-consistency
comparison. One/ten-million-won quantities use floor; depth is the sum of all
ten displayed asks with positive price no greater than expected price. Ratios
to zero/unavailable denominators remain unavailable. No displayed quantity is
represented as guaranteed executable liquidity. Auxiliary reports include
matched/baseline data and BB003 final-control intersection, and preserve
baseline planned-slot shortfalls.

## Exact coordinator commands (not run during implementation)

Use an offline wheelhouse containing the exact versions in `dependencies.lock`.
The installer copies only the explicit manifest inventory into a fresh private
root, creates a private venv, installs with `--no-index --only-binary=:all:`,
installs only the six XO001 units, and leaves timers disabled. It never opens
credential files, changes existing experiment units or uses the heavy lock.

```bash
sudo env PYTHONDONTWRITEBYTECODE=1 /home/are01/src/HQA_Project/HQA_xo001/venv/bin/python -B /home/are01/src/HQA_Project/HQA_xo001/deploy/collector-xo001/install.py --wheelhouse /srv/hqa-wheelhouse --execute
```

Provision the five environment variables separately through the coordinator's
secret management. Put reviewed, sanitized input files in
`/var/lib/hqa-xo001/operator_input/`, root:hqa 0640; no secret exports go there.
The wrapper uses a transient XO001 unit and systemd EnvironmentFile loading.

Read-only live capability smoke; it creates immutable sanitized evidence,
does not submit orders, and is insufficient for activation:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh smoke --code 005930 --execute
```

Before the **first November run**, seal the complete official field contract
and predetermined test plan. For example, November 2/3/4 and an additional
November 5 are verified normal dates in the shared calendar; the coordinator
must fix the actual symbols, limit prices and real account expiry before any
test. These dates are examples, not an automatically created plan.

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh profile-seal --file /var/lib/hqa-xo001/operator_input/profile.json --spec /var/lib/hqa-xo001/operator_input/official-spec.txt --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh plan-seal --file /var/lib/hqa-xo001/operator_input/november-plan.json --execute
```

For observation-only November sessions, start by 08:23 with the main key:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh morning --shakedown --execute
```

For the three planned competition-account order tests, the coordinator alone
installs the separate test flag. Repeat the run on **each planned date**, never
on substitute dates. Test worker starts before 08:25; submit-window behavior is
automatic. During the window, use the account-evidence commands below.

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh flag-template --shakedown --competition-account > /tmp/xo001-test-flag.json
sudo install -o root -g hqa -m 0640 /tmp/xo001-test-flag.json /var/lib/hqa-xo001/activation-control/shakedown.orders.enabled.json
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh test-orders --competition-account --execute
```

After those tests, provision the main credentials through secret management.
On the separate planned main-account verification day, install its flag and
run the additional test. A competition success never replaces this test:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh flag-template --shakedown --main-account > /tmp/xo001-main-test-flag.json
sudo install -o root -g hqa -m 0640 /tmp/xo001-main-test-flag.json /var/lib/hqa-xo001/activation-control/shakedown.orders.enabled.json
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh test-orders --main-account --execute
```

On **every enabled order day**, while the morning worker is running, capture
the actual account between 08:25 and before 08:58:50. Import the sanitized
source; place the returned digest and actual credential account hash in the
reviewed account JSON, then seal it. No missing field is filled automatically.

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh import-evidence --file /var/lib/hqa-xo001/operator_input/account-source.json --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh account-evidence --file /var/lib/hqa-xo001/operator_input/account.json --execute
```

Main-period recurring account evidence must also be supplied; without it all
planned order attempts remain explicit non-submissions. The measurement
recorder itself needs no account screen or order flag.

Shakedown cleanup at the planned date's fixed flatten time:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh cleanup --shakedown --competition-account --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh cleanup --shakedown --main-account --execute
```

Use the first command only on competition-test dates and the second only on
the additional main-account date. General tests using main credentials omit
both account-stage options in flag/run/cleanup commands.

Verify restart preservation during a sealed observation session by stopping
only its running XO001 transient unit, then repeating the same `morning
--shakedown` command with the same key before 09:05. Inspect old markers/hashes,
new connection/run evidence, missed slots and clock bounds. Do not stop BB003,
FW001, FW002 or shared collectors. A stop during order submission leaves an
attempt UNKNOWN and must never be used to obtain a replacement submission.

After each actual test, import reviewed sanitized evidence files and record an
attestation. Each attestation JSON contains `date`, all the exact boolean check
names from `activation.OBS_CHECKS`/`ORDER_CHECKS`/`MAIN_CHECKS`, and a nonempty
`evidence_hashes` list. The import command returns the blob digest. Preserve
failed checks as false. Attestations are first-record-wins and cannot be
replaced by later success on that date.

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh import-evidence --file /var/lib/hqa-xo001/operator_input/reviewed-evidence.json --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh attest --kind order --file /var/lib/hqa-xo001/operator_input/order-attestation.json --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh attest --kind main_account --file /var/lib/hqa-xo001/operator_input/main-account-attestation.json --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh attest --kind observation --file /var/lib/hqa-xo001/operator_input/observation-attestation.json --execute
```

Order attestations must be sealed **under the key/account actually tested**,
before replacing competition credentials. The main-account and main-key
observation attestations use main credentials. Code/environment/profile/config
must match the activation binding; identifier differences are permitted only
for the three general shakedown order tests. At least three planned successes
plus the additional main-account verification are required to approve orders.

By **2026-11-30 23:59:59 KST**, with main credentials and enough margin for the
full one-second publication bound, seal activation. Failed observation checks
refuse activation. Failed order checks seal primary activation with orders OFF
permanently. The following flag operation succeeds only if orders were approved:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh activate --execute
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh flag-template > /tmp/xo001-diagnostic-flag.json
sudo install -o root -g hqa -m 0640 /tmp/xo001-diagnostic-flag.json /var/lib/hqa-xo001/activation-control/diagnostic.enabled.json
sudo systemctl enable --now hqa-xo001-morning.timer hqa-xo001-cleanup.timer hqa-xo001-reconcile.timer
```

If orders were not approved, omit both diagnostic flag commands and enable the
same timers for primary measurement. Missing activation terminates `not_started`:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh status --execute
```

Later official calendar data update (one required date at a time):

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh calendar-add --file /var/lib/hqa-xo001/operator_input/calendar-day.json --spec /var/lib/hqa-xo001/operator_input/krx-notice.txt --execute
```

Post-activation code changes require a reviewed `bugfix` chain with before/after
source hashes, reason and validation blob hashes; identifiers, environment,
field contract and fixed config cannot be redefined. Official calendar data
updates use the separate calendar records. Stop only XO001 workers while making
a coordinated code amendment; there is no silent deployment replacement tool.

Daily reconciliation persists immutable `reconcile_due/<date>` entries. It
recovers observation days from roster/run/denominator/missing-day records and
included BB003 decisions, so process restarts and complete recorder outages do
not lose pending event days. Both timed invocations retry all pending dates;
`--date YYYY-MM-DD` adds a date without dropping older pending dates. Production
KRX collection writes the previous session the next morning. Its source is one
combined `market/krx_daily/YYYY/YYYYMMDD.jsonl` file: reconciliation remains
pending until both KOSPI and KOSDAQ rows are present, and never treats a partial
market file as a halt. Missing or partially collected sources stay pending;
malformed/inconsistent inputs fail explicitly. Closed official dates are marked
separately. Complete reconciliations are immutable and not reacquired. Acquisition
stops at the calendar-derived freeze, leaving unresolved dates explicitly pending.
The reconcile timer runs at 09:30 after morning collection and again at 18:15.

Immediately after DNS/TCP/TLS connection, the order transport checks the Linux
clock synchronization/error bound and the permit/request deadline again, before
writing HTTP. The clock error bound must fit entirely inside the submission
window. Only then is the one-use permit consumed. A slow connect crossing
08:59:30 or an invalid clock closes the connection without an HTTP write, with
`late`/`not_submitted` and the refusal reason stored in `order_result`. A durable
attempt prevents automatic resubmission. Diagnostics remain OFF by default.

After the actual calendar-derived freeze, run once (retry the identical command
to recover registry registration after a crash):

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh evaluate --registry-root /srv/hqa-research --execute
```

Offline implementation validation (serial; no data-root or venv writes):

```bash
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_xo001_*.py
```
