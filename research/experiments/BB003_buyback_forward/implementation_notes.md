# BB003 implementation notes

Implementation scope: the fixed `BB003_buyback_forward` preregistration,
original commit `1e24337f962fba5e1d64cea9b22d10e301bff3ba`, SHA-256
`c5cf76a3e06f7815b455338c8e7c47f60726ae49b42da03fdacb1d4114333e87`.
The implementation worktree started at `f606fb375dc64a72ee051d14c2de3a9ed5b97748`
on `claude/bb003-recorder`. No preregistration or FW001 inventory file is edited.
See `IMPLEMENTATION_REPORT.md` for checks, local measurements and remaining
operational work. There are no new experimental variants, price downloads,
orders, LLM calls, or BB002 result inputs.

## Components and contracts

| Component | Contract |
| --- | --- |
| `backtesting/experiments/bb003/storage.py` | Content-addressed inputs, immutable candidates, no-clobber completion publication, private directory mtime with one-second upper bound, recording lock. |
| `inputs.py` | Explicit-date archive readers, completed-day listing coverage, both-market daily validation, eight-day server / 24-day PC cache, protected-period denial, first complete acquired daily vintage. |
| `admission.py` | Standard-library checks before hashing; installed timer/retry windows, entry-day completion markers, both-market KRX and remaining seal budget. |
| `resources.py` | Effective host RAM/cgroup and installed-unit contracts; final-host measured heavy-job, all-dispatcher and resident-service memory admission. |
| `fetch.py` | Own live DART client, offline ArchiveClient, original receipt row and original document only, bounded immutable receipt attempts, independent digit verification. |
| `recorder.py` | Activation, permanent seen-title/repeat clock, pre-entry decisions, data_not_ready/omission ledgers, return-free entry matching, daily acquisition. |
| `policy.py` | Explicit official sessions, calendar-derived E/V/F, BB001 cost formula with verified T+2 tax dates, data-only amendments. |
| `evaluate.py` | No-returns prevalidation, one PC evaluation after F, terminal abort and sealed-result recovery; unchanged BB001 statistics. |
| `publish.py` | Append the sealed result to the PC authoritative registry; idempotent publication recovery without another analysis. |
| `amendments.py` | Before/after manifest journal limited to reviewed BB003 bugfixes, never frozen imports or preregistrations. |
| `rehearsal.py` | Non-protected historical archive replay, fake clock, reconstructed labels, fresh root, phase timings and RSS. |
| `scripts/bb003/` | Write-free plan by default; explicit execution; no production fake-clock/window override. |
| `deploy/collector-bb003/` | Offline install, own source/environment manifest, activation, root dispatcher, conflicting worker, timer and process measurement. |

BB001 is imported unchanged for title/method/cancellation/digit rules,
tradability, empirical inverse-CDF boundaries/lower ties, HAC, entry-date
clustering, random controls and the 20-slot portfolio. The pre-entry calculation
splits BB001's nineteen known sessions from the last session; regression tests
compare features and exclusion counts directly with unchanged `bb001._pre_entry`,
including nonzero returns and missing/invalid observations. The
valuation loop is local because BB001 fixes pre-2026/Vanish dates and its cost
module cannot accept 2027+ rates. Its adjusted-return chain, stale marks and
side-specific cost subtraction are retained. Known-date tests compare both
cost versions and x1/x1.5/x2 directly with the unchanged cost module. BB002's
separate-ID/period/reader pattern informed this separation; no BB002 data or
saved outcomes are loaded.

## Daily decision and retry rules

There are two operational phases. Evening/night invocations (18:00-05:30,
every five minutes) detect filings, reserve the existing bounded attempts,
fetch original rows/documents, verify digits and apply inclusion/text/repeat
rules. They freeze the nineteen already available price sessions, validity,
liquidity observations and partial return products in `entry_history`. Each
receipt's `preparations` record binds those inputs, policy, listing coverage,
recent-title exclusions and matching rule. Changed repeat/listing evidence
before the seal creates another immutable preparation version; it never
overwrites a prior version. Identical retries do not create new versions.

The morning phase consumes the latest preparation only. It reads the newly
complete previous-session KRX file, finishes the fixed pre-entry calculation,
and seals decisions. It does not scan listings, perform HTTP, parse the other
nineteen shared price files, capture the price history or do post-entry work.
Preparation and final decisions are separate immutable records. Post-entry and
daily acquisition stay in evening/night runs. The 09:01 audit finalizes misses;
it cannot make entries whose morning window has closed.

Production receipt dates are exactly December 1, 2026 through September 30,
2028 inclusive. `rcept_dt` is the date even if the receipt-number prefix differs.
Only fully archived prior calendar days are scanned before 18:00; the completed
current day can be scanned from 18:00. Each receipt needs complete calendar-day
listing coverage from the BB001 64th clock anchor through its receipt day,
including empty weekends/holidays and the source completed-day checkpoint.
The server's main daily archive is the forward authority; exact-date supplement
rows are unioned for historical replay. No broad listing/price scan reads
protected 2026 H1 files. Bootstrap history begins at the required pre-December
repeat anchor, not January 2026.

Every title-qualified original receipt is permanently recorded in `seen`,
including decisions later excluded, unready or discovered late. Repetition uses
the latest strictly earlier `(rcept_dt, rcept_no)` for the same corporation and
an inclusive age of 0..63 sessions. Same-day receipt order uses the smallest
number. Late discovery never rewrites a prior decision; it affects only future
unsealed decisions and produces an omission for an already missed candidate.
Withdrawals do not delete positions or change scheduled exits.

There are at most four per-receipt fetch attempts at elapsed lower bounds
0, 60, 300 and 900 seconds from the first attempt. Attempts never sleep; timer
invocations admit due attempts. The bound includes errors and interrupted calls.
Before any request, `fetch_started/<receipt>.<n>` immutably reserves the
acquisition attempt number and start time. Each actual HTTP page/document
request additionally seals `http_started` with that attempt, request number,
start time and reservation hash before transport. An interrupted attempt is
consumed and never resumed under its old number. A result without a reservation
fails instead of resetting the budget. Four means four receipt acquisition
attempts; each retains the existing bounded pagination/document requests. The live client
requests one corporation/receipt day, at most ten pages of 100 rows, with a
15-second per-request timeout. Unpaged saturation, missing/changed pagination,
missing original rows and unavailable documents stay unready. Credentials and
transport exception URLs are never recorded. No shared collector/quota/archive
is mutated. Requests remain bounded by the receipt policy and service cap.

The original `document.xml` ZIP must contain a receipt-named XML/HTML body. ZIP
and decompressed selected content are bounded at 32 MiB. Encoding candidates,
HTML versus XML parsing, CSS/script removal and body normalisation reuse
BB001's unchanged `DartDisclosureCollector` helpers. Empty, mojibake, wrapper
or error-page bodies fail explicitly. Tiny stored ZIP regressions cover EUC-KR
XML, CP949 HTML with optional closing tags, and UTF-8 HTM. These are synthetic
format fixtures: no eligible real ZIP was present locally, and no network was
used to obtain one. Quantity/amount are rechecked with BB001 numeric
boundaries, never substring matches or a corrected receipt. Missing required
inputs can retry before cutoff; verified ineligible methods, instruments,
plans, document digits and liquidity are excluded with reasons. Pending reasons
remain in an immutable journal. At cutoff, missing/late completion becomes a
terminal `data_not_ready`, excludes the conditional event mean and leaves
unallocated portfolio slots in cash. A later usable document cannot buy back
into the sample. Missing listing days have their own immutable coverage audit.

The decision seals event identity, known entry date/deadline, the 21-session
horizon, event features, pre-entry control
candidates/features, recent-title exclusions, fixed matching/random rule and
all actual input hashes/snapshots. Pre-entry prices require all 20 whole-market
files; individual missing/invalid liquidity observations follow BB001's explicit
liquidity exclusions. Needed receipt/entry calendar gaps record recoverable run
`readiness` with `status=data_not_ready`, reason and `events_dropped=false` before
source reads. Archives remain available for later processing under the original
deadline/omission rules. Official exit dates are resolved during evaluation;
activation and 2026 entry decisions do not require the whole 2027 calendar.

## Atomic publication and post-entry records

Records are durable content-addressed candidates. Only linking `complete.json`
into a dedicated previously empty marker directory finalizes a record. Temp
files live outside that directory. Its kernel directory mtime is `sealed_at`;
the upper endpoint `sealed_at + 1 second` must be strictly less than the next
session's 08:59 KST decision deadline. Equality is rejected. For activation,
that endpoint may equal the explicit November 30, 23:59:59 cutoff. Publication
delayed across a deadline stays present and invalid; it is never revoked,
retimed or replaced. Hash or private-directory corruption fails visibly.
Unpublished candidate bytes remain immutable; a retry may recompute a new
candidate only before the deadline and in the absence of any completion marker.
The marker binds record kind/key/hash and forward/reconstructed mode. Backup
and PC transport must preserve marker **directory** mtimes, as in FW001.
Output-root validation also rejects the code checkout, its read-only inputs,
the main checkout, `/opt/hqa*` roots and other experiments' operational state.

After 18:00 on entry day and once a verified complete entry daily file exists,
the recorder creates a separate immutable `post_entry` record. It uses only
open, volume, base price and verification status for entry executability, plus
the sealed pre-entry features. It computes market-specific size thirds and
prior20 fifths after buyability filtering, assigns equality to lower bins,
relaxes only size if fewer than ten controls, and never recomputes boundaries.
Returns/entry closes are absent from the matching input map. Matched names and
equal weights are fixed, bound to the decision hash and the acquired daily
file hash. Subsequent daily revisions do not revise this record. This is a
forward event-study execution proxy, not an actual PAPER opening fill.

Every complete daily vintage is acquired separately into `prices` and inputs.
The first complete acquired vintage wins. Both KOSPI/KOSDAQ must be present;
missing whole-market files are distinct from an absent/suspended individual
stock. After the receipt-period end a verified run date still before the 83rd
session after the last entry does not require future V/F. Once V is known,
acquisition stops at V and after F; a calendar through V suffices even during
its following seven calendar days. Evaluator acquisition proofs are due by F.

## Calendar, tax and amendment boundary

`shared_policy` exports an unsealed template from the unchanged shared calendar
and statutory table. Initialisation requires official bytes for calendar and
tax fields, as does every later change, including changes inside 2026.
It does not guess 2027+. A policy has exactly these
JSON fields:

```json
{
  "calendar_start": "2026-07-01",
  "calendar_through": "<last officially verified date>",
  "sessions": ["<each actual verified session in ascending order>"],
  "tax_through": "<last officially verified tax date>",
  "tax": [{"from": "<effective date>", "KOSPI": 0.002, "KOSDAQ": 0.002}],
  "sources": [{"kind": "calendar", "url": "<official HTTPS KRX notice URL>",
               "sha256": "<notice bytes hash>", "published_at": "<aware timestamp>",
               "fields": ["calendar_start", "calendar_through", "sessions"],
               "effective_from": "<first covered date>",
               "effective_through": "<last covered date>"}]
}
```

The example rates illustrate shape only; supply the complete unchanged prior
table plus verified prospective rates, with `tax` sources for `tax_through`,
`tax.KOSPI` and `tax.KOSDAQ`. Baseline/amendment `changes` bind every changed
field to effective dates and notice hashes. Source intervals cover the entire
affected range, allowing consecutive notices. Numeric literals/ranges,
dates, sorted unique sessions, official hosts,
source hashes and publication times are validated. Source notice files are
offline inputs named by SHA-256 and are copied into immutable snapshots. An
amendment cannot shorten coverage or change already elapsed sessions/taxes.
The previous/after data hashes, reason, time and source bytes form an append-only
chain. Latest loads recheck that chain and all notice hashes.

This uses the LH003 pinning principle with a stricter boundary: amendments are
non-executable JSON with an exact schema, while **every Python byte remains
pinned**, rather than masking permitted nodes in mutable frozen Python ASTs.
Official authenticity is an operator verification obligation; no network check
is performed. Required unverified session/tax/settlement dates stop a run.

## Activation and evaluation

Activation is exclusively created at `forward/bb003/activation.json`, with full
implementation commit, manifest SHA-256, actual time and a snapshot of manifest
bytes. Its private completion proof must satisfy the fixed November cutoff.
Every operational function/manual CLI uses `verify_operation` before inputs:
manifest, private interpreter/installed files, activation and policy/bugfix
chains. `Store.seal` rechecks immediately before its completion link and records
the accepted `manifest_sha256` on every record. A missing/late
activation is `not_started`, with no alternate start defined by success count.
No unit is activated by installation.

The last possible entry is the first verified session strictly after the fixed
receipt end. E is its entry-inclusive 21st session; V is the 63rd session after
E; F is V + seven calendar days at 23:59:59 KST. The HAC/portfolio grid is every
verified session from T0 through E, retaining all zero-event days. Neither
observed events nor first success changes these dates. Actual future E/V/F
cannot currently be asserted without official 2027+ coverage.

Activation checks the first receipt's next entry session, not its exit, so the
official 2026 calendar can activate on November 30, 2026.

The evaluator refuses through F. It verifies code/environment, amendment chains,
every sealed record/input blob hash and decision-entry references without a
price reader **before** claiming `evaluation_started`. Damaged/missing copied
blobs do not consume the attempt. PC evaluation/publication needs a matching
private environment and is refused from the installed worker root. It uses only sealed decisions,
sealed post-entry selections and daily snapshots acquired by F, for dates <= V.
All official market files through V are required for vanish classification,
even without a stale exit. Missing post-entry/adjusted-return/essential market
data makes the entire verdict `insufficient`; entered events are retained and
no partial-sample mean is reported. Quoted suspension days carry marks, observed
adjusted returns chain after the entry close/open factor, and vanished means
no observed retrading through V, not confirmed delisting. Costs use frozen ADV,
actual entry/last-trading valuation dates, unchanged ticks/commission/slippage,
and policy tax at T+2 in v2. Tax is not multiplied at x1.5/x2; v1 is reported.

The main and vanished −100% valuations each require >=100 events, >=40 firms,
>=50 entry dates and all required finite statistics. Gates are mean excess >=
0.009366926306807792, full-grid Bartlett HAC lag-20 t > 2, x1.5 mean > 0 and
random-control share <= 5%. Random controls reuse BB001's 200 paths and
`default_rng(0)`, path-major/event-date-receipt/code order; the sensitivity uses
identical draws. Insufficiency takes precedence, then a changed pass/fail is
`delisting_sensitive`, both pass is `pass`, both fail is `fail`. No Holm or
repository-trial-count t escalation is added. The report includes both net
sides, cluster t, cost cases, cancellation tags, vanish counts and BB001's
fixed-name 20-slot monthly calendar portfolio with cash/overflow handling.

After consumption any error writes immutable `evaluation_abort.json`, referring
to the consumed attempt and its accepted manifest. This emergency failure
receipt does not assert the current implementation verified successfully.
SIGKILL/power-loss interruption is diagnosed on the next evaluate/recovery call;
the attempt cannot be recalculated. The payload is first sealed in
`evaluation_computed/final`. `recover-evaluation` only re-saves that checkpoint
and registers it, preserving original result bytes/timestamp/manifest even
after interruption between candidate write and completion link. Without the
checkpoint recovery records terminal abort and refuses recomputation.

`publish-trial` also handles the consumed attempt without a computation
checkpoint: it creates/validates the immutable abort receipt, binds its digest,
the original attempt digest and accepted manifest, and appends exactly one
`forward_d21` row with verdict `forward_aborted` and `uses_holdout:false`. The
abort receipt's status and registry `evaluation_status` are also
`forward_aborted`. There are no return statistics
or recalculation. After a durable append followed by interruption, retry finds
the same abort digest and returns `already_exported`. A different terminal
digest is refused. A computed checkpoint must instead follow normal recovery;
it cannot be published as an abort.

The final result and portable single-trial ledger are immutable. The PC CLI
also appends `forward_d21` to its authoritative research registry using the
unchanged writer and large-field reader. Failed export can retry via `publish-trial`
without recomputing any returns. Interim reporting reads only decision/entry
counts, reasons, discovery-to-decision latency, deadline slack, omissions and
coverage and run readiness; it never reads daily prices or final results.

## Morning priority and worst-case admission

Priority is FW001 > FW002 > shadow > BB003. The read-only October 19 probe on
local branch `release/collector-fw001` shows the previous session absent at
07:55 and both markets present at 08:00. This is one observation, not a future
publication guarantee.

The timer checks every minute at **08:03-08:55 KST**. Standard-library admission
runs before analytic imports, environment/source hashing or flock, and again
immediately after nonblocking flock. Admission requires all of:

- The exact previous verified session's KRX file contains both markets, unique
  stock codes and verified rows for that date.
- On a registered FW001 entry day, its matching forward decision and timely
  private completion marker exist. No `hqa-fw001-*` service is active,
  activating or queued; every service instance is checked.
- FW002's due decision has its matching forward completion. Both phases use
  the sealed official calendar to check the current/previous registered month,
  including a 19:00 month-end decision becoming due within the run budget.
  Every existing registered decision is inspected for unfinished fills,
  including older months on ordinary non-month-end days. All three cost records
  (1.0, 1.5, 2.0) must have the matching decision digest, month, entry date,
  calendar version, forward label and valid record time. A fill is explicitly
  not currently due before entry day or, in the FW001-adapted first twelve
  months, while any of the three FW001 execution markers is absent: FW002's
  dispatcher skips that month. Existing partial records are validated even
  then. Once eligible, any missing cost scenario denies admission. Own fills
  cannot use the FW001-marker exception. No FW002 worker/dispatcher is active
  or queued. These checks apply to night preparation too.
- Absent FW002 requires both timers and all decision/fill workers and dispatchers
  to be absent, with no loaded path instance. Partial installation fails closed.
- Shadow and KRX are idle without queued jobs. KRX can immediately trigger
  FW001 and shadow through installed `OnSuccess`.
- Now + remaining seal budget + margin fits before 08:59 and before every
  upcoming FW001/KRX/shadow trigger window.

Worker starts come from effective installed `TimersCalendar`, realtime and
monotonic next triggers, Accuracy/RandomizedDelay and Persistent state, resolved
with `systemd-analyze`. Both FW002 timers are inspected: fill's
`OnBootSec`/`OnUnitInactiveSec` timer needs no calendar, but its next monotonic
trigger must be bounded. Decision's elapsed, already-triggered one-shot
`OnBootSec` can have no next monotonic elapse while its calendar continues.
An absent next elapse still refuses admission for repeating, unknown,
not-yet-elapsed or never-triggered monotonic rules. Formatted durations support
weeks/days as well as hours/minutes/seconds and subsecond units; numeric USec
values are interpreted as raw microseconds. Loaded path instances are queried for
effective target and watched FW001 decision/all-three-execution paths. Paths
are asynchronous and cannot be represented by a guessed next calendar time.
FW002's dispatchers need no heavy worker when all eligible obligations are
complete or explicitly not currently due.
No 08:02/08:12/08:25/08:40 worker schedule is hard-coded in admission. Monthly
due dates use the sealed official calendar and unchanged registered FW001
October 2026-September 2027 / FW002 October 2026-September 2029 periods.
All 36 FW002 months are registered; existing fill obligations, prospective
decisions and morning entry plans use that same month set. This includes
2028-10 through 2029-09 during BB003's later price acquisition.
Unknown/unbounded manager metadata refuses admission.

`SEAL_BUDGET_SECONDS=180` and `SEAL_MARGIN_SECONDS=60` are admission caps,
**not verified server measurements**. Their mathematical last start is 08:55:00;
a delayed 08:55 tick is refused. A kernel alarm starts before admission and
limits the entire morning process, including imports, hashes, proofs, fsync and
lock release, to 180 seconds. Remaining budget decreases across checks.
BB003 retains the shared lock because this path has not been proven light;
the 600-second bulk path never runs in the morning. There is no blocking lock
wait. BB003 declares `Conflicts=` and `After=` against FW001 daily and shadow
daily only. Their effective `Type=oneshot, RemainAfterExit=no` is checked along
with the shared flock contract. Local systemd.unit(5), systemd.service(5) and
systemctl(1) confirm the job semantics: while a oneshot ExecStart is running,
its start job remains pending; BB003's `--job-mode=fail` cannot replace that
job with a stop, even if FW001/shadow starts after the last admission check.
Conversely a priority timer/default `replace` start cancels BB003's pending
oneshot start, stopping its whole cgroup before priority execution because of
the ordering dependency. Stop is bounded by five seconds. Priority starts
using `fail` instead of default `replace` would be refused during BB003; that
mode must not be used to claim priority preemption.

FW002 has no Conflicts/ordering dependency with BB003. Its `Type=exec` start
job ends at execve, so a conflict would let BB003 stop an active FW002 worker,
and the reverse FW002 `fail` start would be rejected during BB003's oneshot
job. Admission checks active/queued FW002 and every due/unsealed decision/fill
before worker submission, after memory verification, after acquiring the lock
and before each worker completion link. Morning admission checks again after
KRX streaming, so a worker becoming active during that scan is refused too.
No shared atomic start transaction is claimed: an FW002 start or newly due
path after the final check can overlap BB003. Activation reserves memory for
that overlap; final-host time measurements must include it. FW001/FW002 units
are unchanged. Completion rechecks
admission, marker byte hashes and the cheap plan against the verified official
plan immediately before linking.

The timer targets root-owned `hqa-bb003-check.service`, outside these Conflicts.
It admits before starting anything, verifies the bound host-memory evidence,
rechecks admission and submits only
`systemctl --no-block --job-mode=fail start hqa-bb003-daily.service`.
The dispatcher has a 64 MiB cap; the hqa worker has a 600 MiB cap. These are
limits, not measurements. Installed manual record/post-entry/capture commands
refuse direct execution and name the root dispatcher. Setup/policy commands
remain explicit operator actions with the shared-lock/priority gates;
they do not require the calendar they are about to initialize.
The runtime worker also requires its PID to match the manager's BB003 worker
`MainPID`; running runtime.py directly cannot bypass the conflicting cgroup.

SIGTERM/SIGKILL never publishes a partial candidate or completion proof: file
bodies are fsynced before a no-clobber link. An interruption before completion
leaves at most an immutable orphan candidate and ignored temp files outside
the marker directory. A later eligible timer run resumes from preparation and
may calculate another candidate before the cutoff. Interruption after the
completion link preserves the entire sealed record; retry reuses its digest.
If no valid seal can be made in time, 09:01 records `data_not_ready`, with no
retroactive entry. Serial tests use real SIGTERM at candidate-link, proof-link
and post-proof-link points for FW001/shadow only; their manager transaction is
simulated, not an installed-systemd execution. Separate pending-job models
exercise both oneshot start orders and both FW002 exec start orders. FW002
race tests cover admission after memory queries, eligibility becoming due,
active state changing during the last scan, and changes after candidate write
but before completion. SIGTERM tests do not establish job transaction semantics.

| BB003 schedule / bound | KST |
| --- | --- |
| Evening/night preparation | Every five minutes, 18:00-05:30; 600s kernel cap + 5s stop grace; refuse installed priority triggers within 605s |
| KRX complete 08:00, higher workers already idle/not due | First eligible tick 08:03; full seal cap ends 08:06, plus 60s margin |
| Installed KRX 08:02 / 08:12 / 08:25 / 08:40 example | Every retry is also a potential FW001 start; no BB003 interval may straddle one |
| Before those example retries | Refuse from 08:08 before 08:12; from 08:21 before 08:25; from 08:36 before 08:40; busy/queued jobs remain blocking afterward |
| Last accepted start | 08:55:00; cap ends 08:58 with 60s to 08:59 |
| Miss audit | 09:01; never retroactive entry |

For a pessimistic example, an 08:25 FW001 run lasting the review's 9m25s ends
08:34:25. If shadow is idle, 08:35 fits before the next 08:40 trigger; 08:36
does not. An 08:40 run of the same duration ends 08:49:25: absent further
priority work, 08:50 can end by 08:53. On a month-end with a subsequent FW002
360s path, completion at 08:55:25 leaves no BB003 start: record `data_not_ready`.
If both decisions are already sealed and workers idle earlier, an ordinary
safe gap is available. These are arithmetic examples, not new host timings.

At/after the last start, denial writes an immutable `morning_missed` receipt
with `data_not_ready`, reason and `no_retroactive_buy=true`, without environment
hashing. The 09:01 verified audit binds this reason into each missed event.
When root dispatches this audit it resolves the hqa uid/gid and sets ownership
on newly created directories and the temporary file before linking the record.
Directories are 0750, files 0640, independent of a restrictive root umask.
Candidate/proof mkstemp files also receive 0640 before publication. Simulated
root-to-hqa uid/gid and access-bit checks run at the publication boundary and
on the later worker read; they need no actual root account or host hqa user.
Later KRX/document arrival cannot restore an entry. Crash/overrun without a
decision completion is likewise a miss.

Activation and installed recording require `forward/bb003/seal_runtime_validation.json`
from the actual server on the final sources and private environment. It must
attest to the full morning path and lock release, bind that host, `source_files`
and `environment_sha256`, and verify worst wall <180s / RSS <600 MiB. Activation
snapshots these bytes. A historical rehearsal alone cannot satisfy this gate.
A source/environment change requires new host validation.

Activation additionally requires `forward/bb003/host_memory_validation.json`.
`resources.host_limits()` discovers the cgroup-v2 mount and hierarchy root from
`/proc/self/mountinfo`, then maps installed slice/unit `ControlGroup` paths.
It reads physical RAM, every visible slice ancestor's `memory.max`, configured
and effective manager limits, and each existing unit's actual cgroup cap.
Only a missing `memory.max` at the real hierarchy root means no cgroup limit.
Missing child/delegated-root/active-unit limits, permission/I/O failures,
ambiguous mounts and invalid metadata fail clearly. An inactive unit may have
no cgroup yet; its enforced manager limits still apply. Inactive/active unit
snapshots normalize to the same stable path and effective limit.
The aggregate limit is the minimum of RAM and all finite root/ancestor/slice
limits (conservative for separate slices); swap does not enlarge it.
Evidence binds this complete snapshot, host, exact final `source_files` and
private `environment_sha256`, and requires measured whole-cgroup peaks for
every installed heavy worker and dispatcher, including children and page cache.
Evidence scope is `bb003_plus_one_fw002_worker_dispatchers_and_resident_services`.
Reserve BB003's enforced effective cap plus the largest single FW002 worker's
enforced cap, then add ALL installed dispatcher peaks and the resident peak.
Also check the measured concurrent pair and the serial FW001/shadow peaks.
The maximum heavy requirement plus dispatcher/resident peaks must be strictly
below the aggregate limit. Each measured job must fit its own effective cap.
With both worker caps at 600 MiB the pair alone reserves 1200 MiB, so the
961 MiB NCP host is refused even if measured peaks happen to be smaller.
The synthetic 2048 MiB case reserves 1200 + 230 + 250 = 1680 MiB; its measured
500 + 500 MiB concurrent pair is also reported. If FW002 is entirely absent,
its worker/dispatchers are omitted. Other residents must include every further
permitted priority overlap beyond the explicit pair; BB003 does not change
their mutual scheduling. Its heavy peak must also cover
the independently verified morning RSS peak. Missing/unverified/stale evidence
refuses activation. Old largest-single-heavy-job evidence is rejected.
Activation snapshots the evidence bytes and seals the numerical memory budget.
Dispatcher and installed recording recheck current limits/evidence; drift needs
remeasurement. The README specifies the evidence schema. No final-host memory
evidence was generated or claimed here.

## Measurement and remaining operational work

Use the exact archive-replay command in `deploy/collector-bb003/README.md`.
`measure.py` includes fresh-process startup/exit wall time and peak RSS;
`rehearsal.py` records per-phase times and reconstructed full-pipeline samples.
Its fake wall clock advances through historical mornings/evenings. Kernel
publication metadata stays real and explicitly measurement-only; copied
rehearsal evidence is rejected by production. Rehearsal admission is restricted
to July 1–December 29, 2025 to prevent protected entry reads. Synthetic mode
exists for tests/smoke measurement and is never deployment budget evidence.

Round 38 invalidates the old source-bound rehearsal: 6.282s / 302.328 MiB are
historical figures, not measurements of this inventory. Before operation:
final reviewed commit, actual-server cold/warm loaded and
interruption/recovery measurements, offline private-environment installation,
installed FW001/shadow shared-inode verification, source-archive completeness,
credentials, filesystem timestamp behavior, backup preservation and activation
before the fixed cutoff remain operational tasks. All 2027+ calendar and tax
notice inputs remain outstanding. No local result establishes server timing,
network/provider availability, deployment success or investment profitability.
