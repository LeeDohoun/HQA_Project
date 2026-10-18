# LH003 astra round 24 validation

Workdir: `/home/are01/src/HQA_Project/HQA_lh003`, starting HEAD `66f5893`.
The interrupted run's uncommitted implementation was reviewed and retained;
two additional pre-start regressions were added, and the modified producer test
now checks its captured identity without replacing it. No commit or Git metadata
write was performed.

## Round 24 P1: seal the distribution producer

`scripts/lh003/distribute.py` verifies the full existing implementation/dependency
pin inventory and the allowed mechanical amendment chain before raw archive
inventory/reads and again before writing a completion receipt. It also compares
against its import-time hashes, including the executing script's own `__file__`.
A drifted producer leaves an incomplete latest publication, so readers cannot
fall back to an older completion.

Every manifest carries `code_pins`: all implementation hashes, the original
mechanical source-baseline hash, and ordered applied amendment hashes.
`distribution.py` compares that complete receipt with the reader's independently
approved reference at capture time. `snapshot.py` rechecks before stream merges
and input-view construction. Environment, weekly and execution readers pass their
approved state directory explicitly. Old manifests lacking producer provenance
refuse and require republication.

The fixed pre-start authority is the checked-in sealed
`distribution_code_reference.json`, containing **68** implementation/dependency
hashes and both mechanical baseline source texts. Its envelope digest is
`227006be5b3d74ef7c4913d2d26b6d06629f653c12547ea3beaf75dc69ed526d`.
The reference is not generated or approved by publishing data and is excluded
from its own implementation inventory to avoid a circular hash. After start,
publications use the existing start/baseline/amendment chain; pre-start captures
continue to use the fixed reference even when read after start or amendments.
The distributor CLI now requires `--state-dir`; see `implementation_notes.md`.

Regression coverage includes modified producer/dependency rejection before raw
inventory, code drift before completion, a copied modified distributor, actual
publication by a changed distributor followed by code restoration (with and
without injected H1 benchmark rows), both allowed tax/calendar amendments,
missing pre-start reference, historical pre-start reads, legacy manifests and
snapshot merge rechecks. All archive fixtures and model processes are synthetic.

## Round 24 test results

**148 passed, 0 failed, 0 errors, 0 skipped**, across all **6 LH003 files**
run serially in fresh supervised processes. The new round 24 file contributes
**15** cases. Total supervised runtime: **248.914 s**;
maximum individual RSS: **232.273 MiB**;
maximum process-tree PSS: **208.862 MiB**.
All processes remained below the 1400 MiB supervisor cap (below 1.5 GB).
Each selected test ID has exactly one completed setup/call/teardown journal.
The initial 13-case diagnostic pass preceded the two additional regressions and
is superseded by this final run.

| File | Passed | Supervised seconds | Peak individual RSS MiB |
| --- | ---: | ---: | ---: |
| `tests/test_experiments_lh003_evaluate.py` | 19 | 6.059 | 152.633 |
| `tests/test_experiments_lh003_round21.py` | 33 | 62.217 | 232.273 |
| `tests/test_experiments_lh003_round23.py` | 29 | 49.109 | 157.262 |
| `tests/test_experiments_lh003_round24.py` | 15 | 51.915 | 145.129 |
| `tests/test_experiments_lh003_runner.py` | 30 | 21.634 | 153.477 |
| `tests/test_experiments_lh003_snapshot.py` | 22 | 57.980 | 154.004 |

The tests use the existing read-only venv symlink, disabled bytecode/dotenv/live
endpoints/tracing, one BLAS/OpenMP thread and no pytest cache. The test guard
blocks environment-file access, protected path writes, and real network/model
subprocesses. The inherited sandbox selector shim and pure lunar-conversion cache
are archived with the exact harness. No model or network call was made.
Before/after hashes match for **230 monitored files**, including all FW001-listed
backtesting files, its manifest, and LH003 preregistration. No preregistration,
FW001 collector-inventory file, `.env*`, main checkout or Git metadata was changed.
The user-preserved untracked venv symlink remains unchanged.

Evidence: [test log](reports/round24_lh003_tests.txt),
[JUnit](reports/round24_lh003_tests.junit.xml),
[counts, per-case journals, memory and frozen-file audit](reports/round24_validation.json),
[reconstructible test harness](reports/round24_test_harness.json).
The repository-wide suite was not rerun in this task. Its round 23 results below
are historical. Live data transfer, actual model timing/capacity and deployment
recovery evidence remain operator prerequisites; these tests do not establish
execution readiness or profitability.

## Round 24 changed files

**16 task paths relative to HEAD**: 7 production, 2 tests, 1 fixed reference,
2 documentation files and 4 validation artifacts. This includes the interrupted
run's retained edits and excludes the pre-existing venv symlink.

- `backtesting/experiments/lh003/distribution.py`
- `backtesting/experiments/lh003/environment.py`
- `backtesting/experiments/lh003/executions.py`
- `backtesting/experiments/lh003/pins.py`
- `backtesting/experiments/lh003/snapshot.py`
- `backtesting/experiments/lh003/workflow.py`
- `scripts/lh003/distribute.py`
- `tests/test_experiments_lh003_snapshot.py`
- `tests/test_experiments_lh003_round24.py`
- `research/experiments/LH003_llm_leader_forward/distribution_code_reference.json`
- `research/experiments/LH003_llm_leader_forward/implementation_notes.md`
- `research/experiments/LH003_llm_leader_forward/validation_report.md`
- `research/experiments/LH003_llm_leader_forward/reports/round24_test_harness.json`
- `research/experiments/LH003_llm_leader_forward/reports/round24_lh003_tests.txt`
- `research/experiments/LH003_llm_leader_forward/reports/round24_lh003_tests.junit.xml`
- `research/experiments/LH003_llm_leader_forward/reports/round24_validation.json`

---

## Historical round 23 record (not rerun)

Worktree: `/home/are01/src/HQA_Project/HQA_lh003`, branch `claude/lh003-runner`,
starting HEAD `de8ca4c` (main + round 21 WIP merge). The user-referenced later
main `31ef9bf` was not merged or copied into this worktree. Round 21 reports
predate the collector reverts and are not current pass evidence.

No staging, commit, stash, merge, collector deployment, FW001 reseal, real model
call, network call, official holdout claim or experiment run was performed.
The main checkout and its symlinked venv/data were read-only. The test audit guard
rejects `.env*` reads (including temporary files), network/Codex subprocesses,
and writes to the main checkout, production data/registry/ledger, preregistration,
Git metadata and frozen inventory. Existing tests use isolated temporary fixtures.

## Implemented contracts

| Finding | Final behavior | Regression evidence |
| --- | --- | --- |
| P1-3 distribution | Separate `scripts.lh003.distribute` process on the research machine after every sync. Fixed full-archive/all-stock scope; trade dates and rule S public dates remove H1. No raw checkpoints, H1 rows, returns or counters are published. Read-only content-addressed snapshot, file hashes and post-publication completion receipt. | Entire scope and stocks; later receipt/list date; newly captured financial and benchmark rows; new H1 excluded; missing/future/failed/inconsistent publication refused; mutation during capture refused. |
| Research boundary | Weekly input and execution capture pin the latest completed manifest for that sync. Weekly views use only the single baseline plus this full publication, including benchmarks. They never inventory or open mixed originals. Existence at an earlier time does not establish capture; missing intermediate publications do not reconstruct historical decisions. | Raw-open and raw-glob traps; same-size/growth/truncation/corrections cannot alter a pinned view; new archive survives raw deletion after publication; workflow refuses missing distribution before A. |
| B actual-module pins | Pin the loaded `backtesting/cost_model.py` and `src/runner/trading_calendar.py`. Amendments require source URL, effective date, change time and before/after hashes. Validate strict data shapes; all remaining AST must match. Allow only SELL_TAX and calendar data, verified-through/version suffix and the `_calendar()` min-year boundary. Fresh process required after updating source. | Both real-source copies accept recorded data-only changes and preserve model identity; executable data, cost/guard logic changes and partial boundary updates refuse; loaded/source mismatch and corrupted chain refuse. |
| B at all stages | One verifier in environment, pre-A input generation, model identity, execution capture and final evaluation. Decisions and attempts record applied code/amendment hashes; final evaluation checks decisions against their seal-time amendment history. | Unamended changes refuse before each stage; approved amendment reaches environment, execution and final evaluation; code drift before A leaves no input or fallback; decision receipts match input receipts. |
| Calendar start | Keep all 52 fixed W-FRI slot identities. Start resolves only the first slot; weekly execution resolves only its needed decision/entry/twentieth-session dates. Needed unverified dates stop. No dropped slot or extended evaluation window. | Start succeeds with 2026-only coverage and without future special-hours data; unverified needed year/exit refuses; new weekly scope needs no unrelated correction note; missing week and insufficient twentieth-session horizon still refuse. |

The three abandoned files were deleted as explicitly requested:
`src/ingestion/incremental_versions.py`, `backtesting/statutory_rates.py`,
`src/krx_calendar_data.py`. No collector replacement or manifest reseal is needed.
New production code is only under `backtesting/experiments/lh003/` and
`scripts/lh003/`. The existing LH003 ordering additions were left untouched.

## Test results

Final focused LH003: **133 passed**, 171.95 s pytest runtime
(177.590 s supervised), exit 0. Maximum individual RSS
**235.355 MiB**, summed tree RSS
**247.273 MiB**, tree PSS
**207.853 MiB**; no limit violation.
The round 23 file contributes **29** cases, including missing/malformed trade-date
publication failures; the remaining 104 cover runner, snapshot, evaluation and
retained round 21 behavior. The initial 131-pass development run predates that
final date-validation change and is superseded by this run.

Complete suite coverage across serial bounded processes: **5067 passed,
83 failed, 6 skipped, 0 errors**, covering all **5156** collected cases across
**138 files**. All 133 final LH003 cases passed. Final selected-segment maximum
individual RSS was **1265.164 MiB**; no limit violation.
Each test ID has one selected completion record. One existing DART ZIP fixture
embeds its collection-time DOS timestamp in its parameter ID; its single
collected/executed alias is explicitly recorded, while all other raw IDs match.
The focused run is included in this total, not an additional independent sample.

| Remaining failure | Count | Verified cause |
| --- | --- | --- |
| `tests/test_experiments_ch004.py` | 34 | The current EV001 source hash is outside CH004's two reviewed hashes. The hermetic fixture changes in `31ef9bf` are available locally but are not ancestors of `de8ca4c`; they were not merged, copied or emulated. The source allowlist remains intact. |
| `tests/test_experiments_fw001.py` | 12 | Incoming checkout differs from the sealed collector manifest at eight paths. |
| `tests/test_forward_fw001.py` | 30 | The same incoming sealed-manifest mismatch stops the recorder before its synthetic downstream cases. |
| `tests/test_ops_fw001.py` | 3 | The same mismatch blocks ops replay/deployment-fixture verification. |
| `tests/test_offline_test_safety.py` | 4 | The managed sandbox denies socket creation/socketpair operations before those tests can exercise their own guards. No network permission was enabled. |

The separate one-process full-suite attempt aborted at approximately 61% with
native **SIGSEGV**, after 2475.372 s, with measured individual RSS
**1375.574 MiB**, tree PSS **1370.346 MiB**, and no supervisor violation. Its
partial timeout dump ends in `typing.py`; the underlying native cause remains
undetermined. The implicated LH003 round 21 file passed all 33 cases in a fresh
process. The complete suite was then executed with fresh bounded processes and
durable per-case journals; the final LH003 and harness-recovery groups replace
their earlier diagnostic attempts. The original abort is preserved in
[reports/round23_full_process_crash.txt](reports/round23_full_process_crash.txt).
A completed one-process full-suite pass is not claimed.

The first per-file journal used `Path.open` during tests; filesystem-trap
fixtures correctly blocked that instrumentation in AV002/CH001. It now writes
through its own descriptor opened before tests, preserving the audit guard.
Those two files were rerun: **253 passed**. Shadow's existing 600 MiB check also
passed in its isolated file process (supervisor peak **516.664 MiB**); no shadow
code or memory assertion was changed. All attempts remained below the requested
approximately 1.5 GB individual-process limit.

Full evidence: [log](reports/round23_full_suite.txt),
[JUnit](reports/round23_full_suite.junit.xml). Per-case completion records, all attempted
process metrics and the selected completion mapping are recorded in the JSON
summary and reconstructible harness.

The first root-wide pytest collection stopped with **1 collection error**: the
user-preserved `reports/reverted_round2/tests/test_dart_quarterly.py` collided
with `tests/test_dart_quarterly.py`. Its log is retained in
[reports/round23_root_collection.txt](reports/round23_root_collection.txt).
The backup was not modified. The complete actual suite is run as `pytest tests`;
all non-backup Python test files are under that directory. This is a collection
scope correction, not omission of active tests.

Development reruns found a directory-publication permission error and six
boundary/fixture expectations, then one missing `commit_hash` in a new test's
fake registration. These were fixed before the 131-pass run. Earlier failures
are not presented as current passes.

## Test conditions and artifacts

All pytest processes run serially using the read-only symlinked venv,
`PYTHONDONTWRITEBYTECODE=1`, disabled dotenv/live KIS/model endpoints/tracing,
one BLAS/OpenMP thread, `MALLOC_ARENA_MAX=2` and
`MALLOC_TRIM_THRESHOLD_=131072`. An external supervisor samples every 50 ms
and terminates its test group if an individual RSS or aggregate proportional
memory exceeds 1450 MiB. Summed fork RSS may double-count shared pages.

The temporary sandbox compatibility shim bounds epoll waits to 50 ms; it grants
no network access. A test-only plugin caches the original pure lunar/solar
conversion functions without changing calendar dates, notices or verification.
No merge-parent Git-fixture plugin or research-source allowlist override is used. Reproducible harness sources are
archived as JSON in [reports/round23_test_harness.json](reports/round23_test_harness.json).
Machine-readable counts, failure identities, commands, memory and code/frozen
hashes are in [reports/round23_validation.json](reports/round23_validation.json).
Focused evidence: [log](reports/round23_lh003_tests.txt),
[JUnit](reports/round23_lh003_tests.junit.xml).

## Frozen inventory and readiness

Before/after audit: **213 existing files**, **210 unchanged**, and only the
three explicitly requested deletions. Preregistration remains
`dc1ee3b669e515dc92b504eaf5d88061281b079f3c7f4fc5d7194dd5c37d190f`. The FW001 manifest itself remains
`2d9b62fa23a651716b9738e43fd8a174656696dcb10b4d5e5ab6c9b7634ae8aa`. No frozen file was changed to make tests pass.

The incoming checkout already differed from its FW001 seal at these eight paths:

- `backtesting/experiment_registry.py`
- `backtesting/experiments/av001.py`
- `backtesting/experiments/common.py`
- `backtesting/experiments/ordering.py`
- `backtesting/holdout.py`
- `scripts/data/dart_buyback.py`
- `scripts/ops/research_run.sh`
- `src/ingestion/dart_listing.py`

The existing LH003 ordering additions are the operator's explicit exception.
All eight incoming hashes are preserved; the JSON records both sealed and
incoming/current hashes. The 45 FW001 failures therefore remain visible without
resealing or editing collector code.

Tests use generated archives and fake model processes only. Official notices,
2027 statutory rates, real machine capacity/timing, real authentication,
transfer/reboot/suspend/logout evidence and AV001/EV001/AV002 release gates remain
operator prerequisites. The first week does not require all future calendar
coverage; each later run must verify the dates it actually needs. No real-data
readiness or profitability is inferred from these tests.
See [implementation_notes.md](implementation_notes.md) and
[IMPLEMENTATION_BLOCKERS.md](IMPLEMENTATION_BLOCKERS.md) for the operator contract.

## Files changed

Production implementation (12 files):

- `backtesting/experiments/lh003/__main__.py`
- `backtesting/experiments/lh003/calendar.py`
- `backtesting/experiments/lh003/distribution.py` (new)
- `backtesting/experiments/lh003/environment.py`
- `backtesting/experiments/lh003/evaluate.py`
- `backtesting/experiments/lh003/executions.py`
- `backtesting/experiments/lh003/inputs.py`
- `backtesting/experiments/lh003/pins.py` (new)
- `backtesting/experiments/lh003/runner.py`
- `backtesting/experiments/lh003/simulation.py`
- `backtesting/experiments/lh003/snapshot.py`
- `backtesting/experiments/lh003/workflow.py`

Research-machine commands (3 new files):

- `scripts/lh003/__init__.py`
- `scripts/lh003/distribute.py`
- `scripts/lh003/amend.py`

Tests (5 files):

- `tests/test_experiments_lh003_evaluate.py`
- `tests/test_experiments_lh003_round21.py`
- `tests/test_experiments_lh003_round23.py` (new)
- `tests/test_experiments_lh003_runner.py`
- `tests/test_experiments_lh003_snapshot.py`

Documentation (3 files under `research/experiments/LH003_llm_leader_forward/`):

- `implementation_notes.md`
- `validation_report.md`
- `IMPLEMENTATION_BLOCKERS.md`

Deletions (3 files):

- `src/ingestion/incremental_versions.py`
- `backtesting/statutory_rates.py`
- `src/krx_calendar_data.py`

This is **26 implementation/test/documentation/deletion paths**. Additional
validation artifacts are listed in `reports/round23_changed_paths.json`.
The supplied round 23 review, reverted copies and venv symlink are preserved.
