# BB003 separate-root recorder

This is an event-study recorder, not an order service. It reads collector DART
list and KRX daily archives and writes only `<data>/forward/bb003`. It never
edits FW001, shadow, their units, shared data or preregistrations. Installation
and activation are separate, explicit operator commands; their default is a
read-only plan. No deployment was performed during implementation.

Production code is `/opt/hqa-bb003`, with its own offline-built venv, import
closure and `deploy/collector-bb003/manifest.json`. Copied frozen imports retain
their exact bytes. Units make `/opt/hqa` inaccessible and expose one writable
path: `/var/lib/hqa/data/forward/bb003`. There is no shared venv or dotenv
loading. Supply the DART key using the operator-owned
`/etc/hqa/bb003.credentials` systemd environment file; tools never create or
print credentials. There are no KRX requests in BB003.

The service opens the existing `/run/hqa-heavy/run.lock` **read-only**, then
takes an exclusive, nonblocking Linux flock before importing the analytic
pipeline. It does not create, unlink or replace that inode. FW001 and shadow
must have the d22e231 `flock --exclusive --no-fork` and
`RuntimeDirectoryPreserve=yes` contract installed. Missing contract/lock fails;
a held lock means this invocation does no work. Later timer runs retry.
Every BB003 worker declares `Conflicts=` and stop/start ordering against
`hqa-fw001-daily.service` and `hqa-shadow-daily.service` only. Both must be
`Type=oneshot, RemainAfterExit=no`: their running start jobs cause BB003's
`fail` transaction to be rejected. A priority timer/default `replace` start
can cancel BB003's running oneshot job and stops its whole cgroup first,
with at most five seconds stop grace. A priority `fail` start cannot preempt
BB003; do not use that mode for FW001/shadow priority starts.
FW002's Type=exec workers have no Conflicts with BB003 and may overlap it
after the final admission check. FW001/FW002 units are unchanged.
The timer targets the separate root-owned `hqa-bb003-check.service`, which
admits, verifies host memory evidence, admits again and submits the worker with
`systemctl --no-block --job-mode=fail start hqa-bb003-daily.service`.
It has no Conflicts and a 64 MiB cap. The worker retains its 600 MiB cap.
Installed manual record/post-entry/capture commands name the dispatcher and
refuse direct execution. The runtime worker requires its own systemd MainPID,
so direct runtime.py execution also fails. Priority is FW001 > FW002 > shadow > BB003.
Evening/night runs every five minutes, 18:00-05:30,
prepare filings, original rows/documents, digits, inclusion/repeat rules and
nineteen-session price aggregates in immutable records. This bulk path has a
600s kernel cap and refuses starts within 605s of an installed FW001/KRX/shadow
trigger, including evening triggers. It never runs during the morning window.

Morning checks run every minute at 08:03-08:55. Before hashing/importing analytics
or taking flock, and again just after flock, they require both-market prior
KRX completion, the due entry-day FW001/FW002 forward completion markers and no
active/activating/queued priority worker. KRX and shadow must be idle too.
Effective installed calendars, realtime/monotonic retry windows, timer delay and
Persistent state determine upcoming starts; KRX's installed OnSuccess is also
checked. Missing/unbounded metadata refuses admission. Worker schedules are
never guessed from fixed trigger times. Read-only marker checks access only the
relevant monthly forward records, never return/evaluation or protected data.

Both morning and night check FW002's due decision and every registered
decision's unfinished fill months. Each of the three 1.0/1.5/2.0 fill records
must link to its decision digest, month, trade date and calendar, with a forward
label and valid timestamp. A future entry is not due yet; first-year fill is
also not currently due while any FW001 execution completion is absent, matching
FW002's dispatcher. Existing partial fills are still validated. Once eligible,
any missing cost record denies admission. Both FW002 timers, including the
monotonic-only fill timer, and all loaded decision/fill path instances are
inspected. Admission is repeated immediately before submitting the worker and
before each worker completion link. A race after that check can run both
BB003 and FW002; host admission reserves their concurrent memory and host
timing validation must exercise that overlap. All 36 FW002 months, 2026-10
through 2029-09, are covered. An exhausted already-triggered one-shot OnBoot
needs no next monotonic elapse; repeating/unknown/unfired rules without a next
elapse are refused. Formatted timer durations support long uptimes in d/w.
FW002 source/unit details were read at
`../HQA_fw002` commit `310c824`; nothing there was modified.

Interruption before a completion link leaves no sealed record and can retry
from immutable preparation. Interruption after the atomic link retains the
whole record and retry reuses it. No timer retry rewrites a completion marker.
If the remaining morning budget cannot fit, the 09:01 audit records
`data_not_ready`; there is no retroactive entry.

The morning path performs only final price completion and decision sealing;
there are no DART/listing, post-entry or capture calls. It retains the shared
lock until proven light. A separate kernel cap of **180 seconds**, including
admission, imports, private verification and release, plus **60 seconds margin**
must fit before 08:59 and every upcoming priority trigger. Last start is
08:55:00; a delayed final tick is refused. Completion rechecks admission, marker
byte hashes and the official plan immediately before linking. These are caps
pending final-host validation, not measured server maxima. Manual recording has
the same phase selection, gates and kernel cap. A final denial leaves an
immutable reason; 09:01 audits it into `data_not_ready`, without a late buy.
Root-created missed-audit directories/files receive hqa uid/gid ownership and
0750/0640 modes before record publication. Candidate/proof temporary files also
receive 0640 before linking; a restrictive root umask cannot hide the audit.
See `implementation_notes.md` for the four-trigger and month-end worst cases.

## Write-free plan and offline measurement

From a checkout containing the final implementation:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
venv/bin/python -B -m scripts.bb003 plan --output-root /tmp/bb003-plan
```

Replay real, already archived 2025 filings, with a fake receipt clock and
reconstructed labels, into a **new** throwaway root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
venv/bin/python -B deploy/collector-bb003/measure.py \
  --source-data /home/are01/src/HQA_Project/HQA_Project/data \
  --output-root /tmp/hqa-bb003-rehearsal-final-20250701-03-v2 \
  --from-date 2025-07-01 --to-date 2025-07-03
```

For an entire historical half-year, change the end to `2025-12-29` and use
a different fresh output root. December 30 receipts would require protected
2026 entry data and are deliberately outside rehearsal admission. A synthetic
smoke replay adds `--synthetic`; it is refused as installation budget evidence.
No protected daily/list/document data are read by either mode. Shared source
archives and the shared venv remain read-only.

`measurement.json` records every invocation, phase times, source hashes,
policy hash, peak RSS and counts. `process_measurement.json` additionally
captures fresh-process startup/exit wall time and child peak RSS. Rehearsal
includes fetching through the offline ArchiveClient, digit checks, repeat and
liquidity rules, snapshots, full completion markers, and post-entry matching.
It neither computes returns nor creates a forward activation or final trial.
Kernel mtimes remain real: the separate measurement mode records both the
historical deadline and a real publication deadline. Production refuses its
markers. Preserve file **and directory** mtimes when copying any evidence.

Repeat real-archive measurements cold/warm and under representative collector
load on the actual server. The installer requires a non-synthetic successful
two-phase decision/post-entry sample on exactly the candidate source inventory,
`2 * whole_process_wall + 120` rounded up to minutes <= 180 seconds, and peak
RSS < 600 MiB. This is an additional conservative replay bound, not full
production validation.
Local desktop timings are not server validation. The report's host and full
source binding are retained in the installed manifest. Read-only hashing
does not provide a signature against a malicious root operator.

## Offline install, policy initialization and activation

After a reviewer commits the final implementation elsewhere, stage local wheels
and preserve the final-source measurement bundle. No tool fetches Git or PyPI:

```bash
python3 -B deploy/collector-bb003/install.py --dry-run \
  --repo /srv/offline/hqa --commit <reviewed-full-commit> \
  --wheelhouse /srv/offline/bb003-wheels \
  --measurement-report /var/tmp/bb003-measure/process_measurement.json
```

Use the same arguments with `--execute` as root to install. Existing code,
state or unit destinations are refused. A partial failure leaves evidence
for inspection. Installation seals the original preregistration hash, full
source inventory, private interpreter/package hashes and measurement. It
installs and disables only `hqa-bb003-daily.service`, `hqa-bb003-check.service`
and `hqa-bb003-daily.timer`, reloads
systemd, and checks the shared lock contract again. It never enables them.

Before activation, measure the final private deployment on that server under
cold/warm representative maximum preparation/market load, including admission,
imports, every private verification, completion fsync and lock release. Validate
all four KRX retries, month-end FW001/FW002/shadow contention and forced
termination/restart. The operator must retain the measurements and publish
`/var/lib/hqa/data/forward/bb003/seal_runtime_validation.json` with this contract:

```json
{
  "scope": "full_morning_seal_including_verification_and_lock_release",
  "verified_worst_case": true,
  "host": "<actual final server hostname>",
  "source_files": {"<every manifest file>": "<same SHA-256>"},
  "environment_sha256": "<manifest private-environment SHA-256>",
  "worst_wall_seconds": "<measured positive number strictly below 180>",
  "peak_rss_mib": "<measured positive number strictly below 600>"
}
```

The two measurements must be JSON numbers, not the example's placeholder
strings. This file is operator evidence, not a report generated by the
historical replay. Missing, stale, wrong-host or over-budget evidence refuses
activation and installed recording. Activation snapshots the evidence bytes.
After any source/environment change, remeasure before resuming. No such server
evidence was created or claimed in this worktree.

Also retain final-host whole-cgroup memory measurements and publish
`forward/bb003/host_memory_validation.json`. Obtain the `limits` snapshot using
the installed `backtesting.experiments.bb003.resources.host_limits()`; do not
copy a desktop snapshot or guess RAM from the server's advertised size. The
schema is:

```json
{
  "scope": "bb003_plus_one_fw002_worker_dispatchers_and_resident_services",
  "verified_worst_case": true,
  "peak_metric": "cgroup_memory_peak_including_children_and_cache",
  "resident_scope": "all_other_services_and_kernel_including_permitted_priority_overlap",
  "host": "<actual final hostname>",
  "source_files": {"<every manifest file>": "<same SHA-256>"},
  "environment_sha256": "<manifest private-environment SHA-256>",
  "limits": "<complete resources.host_limits() JSON object>",
  "heavy_job_peak_bytes": {
    "hqa-fw001-daily.service": "<measured integer>",
    "hqa-shadow-daily.service": "<measured integer>",
    "hqa-fw002-decision.service": "<measured integer>",
    "hqa-fw002-fill.service": "<measured integer>",
    "hqa-bb003-daily.service": "<measured integer>"
  },
  "dispatcher_peak_bytes": {
    "hqa-bb003-check.service": "<measured integer>",
    "hqa-fw002-check@decision.service": "<measured integer>",
    "hqa-fw002-check@fill.service": "<measured integer>"
  },
  "resident_services_peak_bytes": "<measured positive integer>"
}
```

Use real JSON integers/objects in place of the example strings. The maps must
contain every installed job exactly; omit FW002 entries only when the complete
FW002 installation is absent. Measure cold/warm preparation, full morning seal,
FW001/shadow, FW002 decision/fill, BB003 concurrently with each FW002 worker,
and overlapping root dispatchers. The resident peak includes all remaining
services, OS/kernel, and any further priority overlap beyond the explicit
BB003/FW002 pair that their own units permit. BB003 does not serialize them
with each other. Use whole-cgroup peaks, including children and cache, rather
than process RSS alone. The BB003 heavy peak must cover its measured morning
RSS too.

Activation reserves BB003's enforced effective worker cap + the largest single
FW002 worker's effective cap, and adds every dispatcher peak + resident peak.
The measured concurrent pair and serial FW001/shadow peaks are checked too.
The maximum heavy requirement plus dispatchers/residents must be strictly below
the effective aggregate RAM/cgroup/slice limit. Each measured job must also fit
its own effective cgroup limit. With the two 600 MiB caps, the pair alone needs
1200 MiB: the 961 MiB NCP host is refused even with small measured peaks.
The synthetic 2048 MiB example needs 1200 + 230 + 250 = 1680 MiB.
Limits are conservatively aggregated even across separate slices; swap is
excluded. The cgroup-v2 mount/root is discovered from mountinfo, with actual
unit/slice ControlGroup paths and all visible ancestors. A real hierarchy root
may lack memory.max (no limit); missing child/delegated-root/active-unit caps
or permission/I/O errors refuse admission. Inactive units need not have a
cgroup yet and retain their enforced manager limits.
Missing, stale or incomplete evidence
fails closed. Activation snapshots these bytes and seals the numerical budget;
dispatcher and installed recording recheck limits and evidence. Remeasure on
configuration/source/environment changes. No host-memory evidence was fabricated
or activation attempted here. The process-kill regression uses real SIGTERM
with a simulated manager; final-server systemd start/stop tests remain required.

Initialize the verified 2026 policy before activation. The initial baseline
requires offline official evidence too; `shared_policy` only exports a template:

```bash
cd /opt/hqa-bb003
sudo -u hqa venv/bin/python -B -m scripts.bb003 policy-init --execute \
  --policy-file /srv/offline/bb003-2026-policy.json \
  --notices /srv/offline/notices-by-sha256 --reason '<reviewed official baseline>'
```

Inspect `venv/bin/python -B deploy/collector-bb003/activate.py --dry-run` with the private
venv interpreter; then the root operator uses `--execute`. Activation verifies
code/environment, units and the first receipt's next entry session, creates the
immutable `forward/bb003/activation.json` and completion marker, and enables
only `hqa-bb003-daily.timer`. Publication's one-second upper bound must be
<= **2026-11-30 23:59:59 KST**. Any missed activation is `not_started` permanently;
the first successful receipt cannot start or extend the experiment. A failure
enabling the timer is a visible partial activation; retain the existing marker.

The shared calendar is currently verified only through 2026, which suffices
for activation on November 30. Pre-entry records seal the known entry date,
deadline and 21-session rule. They defer exit dates to evaluation after official
calendar extension. Daily runs check only needed receipt/entry/acquisition dates;
future V/F is unnecessary while a verified run date is provably before V.
Needed unknown dates record recoverable `readiness` entries with
`status=data_not_ready` and a reason, leaving receipts available for later
processing and preserving missed-deadline/omission rules. Supply reviewed
extensions before the dates they are needed using:

```bash
venv/bin/python -B -m scripts.bb003 policy-amend --execute \
  --policy-file /srv/offline/bb003-policy.json \
  --notices /srv/offline/notices-by-sha256 --reason '<mechanical official update>'
```

The strict schema accepts only session lists, coverage bounds, tax tables and
source metadata. Notices are named by their SHA-256, with official HTTPS KRX or
law.go.kr/moef.go.kr URLs, publication times, `fields`, `effective_from` and
`effective_through`; they are snapshotted locally. Every initial or changed
field, including changes within 2026 and each market's tax rates, is bound to
official evidence covering its effective dates. Consecutive notice intervals
may cover a longer change. Latest loads recheck the evidence and amendment chain.
No Python amendment is allowed. Existing elapsed sessions/tax rates and the
hash chain are pinned. A needed unverified date stops the run, preserving
events. This is an operator-reviewed official-source assertion, not an online
authenticity check. See implementation notes for the JSON schema.

Post-seal implementation changes require an operator-reviewed **bugfix**,
new source/environment manifest and remeasurement. Preserve both manifest
files, stop BB003's own timer, and record the transition using
`bugfix.py --before ... --after ... --commit ... --reason ... --execute`.
The journal verifies the preceding manifest, freezes FW001 import hashes and
preregistrations, and records before/after hashes and time. It does not edit or
deploy code. Runtime and PC evaluation require that chain. A new feature or
changed experimental rule requires a new ID, not this tool.

All operational function and manual CLI paths use `verify_operation` before
reading inputs. Each seal repeats the same source/environment/activation and
policy/bugfix-chain checks immediately before its completion link. Every record
stores the accepted `manifest_sha256`. PC evaluation/publication requires a PC
checkout and a matching private environment; the installed worker root refuses
those operations. Setup commands also verify their manifest/environment.

## Reports and single PC evaluation

An interim command opens only count/latency/omission records:

```bash
venv/bin/python -B -m scripts.bb003 interim --output-root /var/lib/hqa/data
```

After F, on a PC checkout with the matching implementation and preserved
BB003 state (including private directory mtimes), run once:

```bash
venv/bin/python -B -m scripts.bb003 evaluate --execute \
  --output-root /srv/bb003-frozen-data --registry-root /srv/pc/hqa
```

E, V and F come from the verified calendar, independent of observed receipts.
The evaluator refuses through F, accepts daily vintage proofs only through V
available by F, and performs a no-returns integrity pass over implementation,
environment, amendment chains, records, references and input blob hashes **before**
claiming the permanent one-attempt marker. Damaged/missing copied blobs can be
restored before that marker; genuinely absent acquisition proofs still lead to
the preregistered insufficient verdict. A terminal
`insufficient` result also consumes that attempt. It seals `evaluation.json`,
a portable single-trial record, and appends the result to the PC authoritative
`research/experiments/registry.csv`, using the repository's large-field reader.
Any error after consumption writes immutable `evaluation_abort.json`, bound to
the manifest/attempt accepted at consumption; it never claims the failed current
implementation verified successfully. Abrupt SIGKILL/power-loss attempts are
recognized as consumed on restart and receive the same terminal abort receipt.

The calculation first seals `evaluation_computed/final`. If final-save/trial/
registry publication is interrupted, use:

```bash
venv/bin/python -B -m scripts.bb003 recover-evaluation --execute \
  --output-root /srv/bb003-frozen-data --registry-root /srv/pc/hqa
```

Recovery only re-saves/registers that checkpoint, retains the original result
bytes/timestamp/manifest, and opens no prices or return calculation. Without a
sealed checkpoint it records terminal abort and refuses recomputation. For a
registry-only failure, `publish-trial --execute --output-root ... --registry-root
...` remains available. The shared main checkout is always refused as
a write target. Missing post-entry or required valuation data retain entered
events and make the whole final judgement `insufficient`.

Without a computation checkpoint, `publish-trial` records the terminal abort
under the same `forward_d21` variant with verdict/status `forward_aborted`,
`uses_holdout:false`, the abort
digest, consumed-attempt digest and accepted manifest. It reads no prices and
does not recompute. Retry after a durable registry append is idempotent; another
digest is rejected. A recoverable checkpoint must use `recover-evaluation`
instead of publishing an abort.
