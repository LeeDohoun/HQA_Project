# XO001 independent-review fixes

Worktree: `/home/are01/src/HQA_Project/HQA_xo001`.
All six findings are addressed in the existing untracked implementation files,
plus one new regression-test file. No pre-existing tracked file was modified;
nothing was staged or committed. Order diagnostics remain **OFF by default**.
No network calls, `.env` reads, `/etc/hqa` secret reads, live orders, deployment
or authoritative registry writes were performed.

## Changes and regression evidence

| Finding | Implementation (`file:line`) | Regression coverage |
| --- | --- | --- |
| P1-1 | `backtesting/experiments/xo001/recorder.py:245`, `:276`; `scripts/xo001/__main__.py:95`; `deploy/collector-xo001/systemd/hqa-xo001-reconcile.timer:5` | Persistent immutable due-day records; discovery from recorder and BB003 records; evening absence, next-morning restart, partial-market retry, complete-day idempotence, outage-day retention and freeze cutoff. Timer runs at **09:30 and 18:15**. Production has one combined daily file; both KOSPI and KOSDAQ must be present before reconciliation completes. |
| P1-2 | `backtesting/experiments/xo001/activation.py:242`, `:256`; `backtesting/experiments/xo001/recorder.py:245`; `backtesting/experiments/xo001/evaluate.py:165` | Reconcile/evaluate reject altered code, environment or amendment chain before touching measurement inputs; drift during calculation refuses sealing. Offline CLI paths never load credentials. Sealed results link the full binding, activation hash and ordered amendment hashes. Approved amendment evidence blobs are verified. |
| P1-3 | `backtesting/experiments/xo001/kis.py:113`, `:179`; `backtesting/experiments/xo001/diagnostic.py:176` | DNS/TCP/TLS completes before the final clock/deadline/permit check, immediately before HTTP writing. Slow connection, skew reaching the deadline and lost synchronization close without sending; persistent results retain the reason and prohibit resend. A healthy connection test verifies connect → clock check → permit consumed → HTTP write. Direct unguarded order transport refuses. |
| P1-4 | `backtesting/experiments/xo001/roster.py:231`; `backtesting/experiments/xo001/evaluate.py:88`, `:114` | Roster seals carry the 08:25 deadline; the evaluator independently checks the full-second kernel publication bound, including legacy forward-labelled records without deadlines. Late/reconstructed rosters and reconstructed snapshots cannot supply primary completion or price pairs. Main events remain in the denominator as unobserved. |
| P1-5 | `backtesting/experiments/xo001/fields.py:104`; `backtesting/experiments/xo001/recorder.py:180` | Ordinary REST samples remain samples, without asserting a first trade. Completion requires verified provider first-trade/auction evidence with event time and market status, or official confirmed absence. A confirmed delayed first trade at 09:02 counts; both explicit-first and delayed-auction cases retain the delayed category. Official halt and collection failure remain distinct. |
| P2-6 | `backtesting/experiments/xo001/evaluate.py:260`; `deploy/collector-xo001/coordinator.sh:6`; `scripts/xo001/__main__.py:90` | Existing `experiment_registry.record_trials` API appends one `measurement` row with `measurement_complete`/`measurement_insufficient`, linking the sealed result hash/path and runtime hashes. Crashes before or after append recover without calendar/BB003 reads or recomputation. Conflicting rows refuse. No strategy judge is called. |

The dedicated regressions are in `tests/test_xo001_review_regressions.py:1`.
Existing capture fixtures/assertions were updated for the strict roster boundary
and the corrected distinction between an arbitrary sample and confirmed first
trade. Shared registry/append-only source files are included as read-only code
inventory dependencies; PyYAML is pinned and environment-hashed for that API.

## Validation

Round 57 follow-up: `backtesting/experiments/xo001/recorder.py:212` also
classifies a confirmed first trade after the official open as `opening_delayed`
using provider event time, including when the current status is NORMAL.
`tests/test_xo001_review_regressions.py:283` adds four cases covering explicit
first-trade and auction confirmation: 09:02 events are delayed; 09:00 events
received at 09:02 retain their non-delayed category and both timestamps,
recording the 120-second receive latency without treating it as opening delay.

The exact requested command was run:

```bash
venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_xo001_*.py
```

**147 passed**: 114 existing cases plus 33 added regression cases. All tests are
offline; HTTP connections, provider messages and collection clocks are synthetic.
Registry API tests write only under pytest temporary directories.

Also checked: Python AST parsing of all 22 implementation/test Python files,
`bash -n deploy/collector-xo001/coordinator.sh`, offline installer dry-run,
CLI `plan`, empty tracked and staged diffs, and unchanged preregistration hash:

```text
96d36b5721d88abd6f34a348d9927f8aeeb11f116eca3db3ff9949b4b002db8b
```

There are 33 new implementation/test/documentation files in total, excluding
the pre-existing untracked `venv` link. The private deployment inventory contains
41 files. Before the round 57 follow-up, its source hash, excluding this report
and generated manifest, was:

```text
7571dca0ed57c844de35dd6573233a6b313cce58db366f5f8112380345f2bc1f
```

## Operational handoff and remaining limits

Updated rules and coordinator commands are in
`research/experiments/XO001_open_executability/implementation_notes.md:1`.
Final evaluation requires the authoritative local Git checkout explicitly:

```bash
sudo /opt/hqa-xo001/deploy/collector-xo001/coordinator.sh evaluate --registry-root /srv/hqa-research --execute
```

`/srv/hqa-research` is an operator-supplied example path, not a created copy. It
must contain the registered Git history and permit the service to append its
research registry. The wrapper exposes that checkout read-only and makes its
`research/experiments` directory writable only for the explicit final command.
Repeat the same command after an interrupted registry append.

No review item remains unresolved in code. Live PAPER field semantics, actual
collector/timer ordering, systemd deployment and production registry permissions
were not exercised under the required offline scope. If the allowed PAPER feed
cannot establish first-trade evidence, post-open completion stays `unknown`;
no substitute data is invented. Existing activation/shakedown requirements and
the fixed observation/freeze period remain in force.
