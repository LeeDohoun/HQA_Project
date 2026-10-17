# LH003 round 21 implementation validation

Worktree: `/home/are01/src/HQA_Project/HQA_lh003`, branch
`claude/lh003-runner`, starting HEAD `9b657ad5303baa55c920151a2d57725f8cf06420`.
No staging, commits, merges, stashes or other Git metadata changes were performed
in this worktree or the main checkout. Existing tests use isolated temporary Git
fixtures for preregistration/locking checks.
The two conflict files have no markers; their index entries remain unmerged until
the operator stages them. All 11 functions from main's `ordering.py` match main's
stage-3 AST exactly. The LH003 release function is additional.

Preregistration SHA-256 remains
`dc1ee3b669e515dc92b504eaf5d88061281b079f3c7f4fc5d7194dd5c37d190f`.
No actual `.env*` file was opened, no network or real model call was made, no
production registry/holdout/data was written, and the main checkout was never
written or used to run an experiment. Its existing venv was used read-only.
Existing read-side registry tests created an empty advisory `.lock` beside the
research registry; this generated file was removed after all tests exited.
Registry and ledger bytes still match the incoming main index exactly.

## Review findings

| Finding | Change | Regression evidence |
| --- | --- | --- |
| P1-1 calendar | Shared corrected sessions and special closes; verify the entire fixed horizon and nonempty required H1 and full 252-session measurement price files before session construction/claim. Missing coverage refuses the complete plan. | Election/Constitution holidays, pending special hours, 2027 refusal, missing H1/non-H1 files without content reads, and rejection of a missing whole week. |
| P1-2 ordering | LH003's mandatory gate calls main's AV002 release verifier inside the shared lock, requiring both variants and their sealed AV001 parent/result. | LH003 lock/claim regression and three AV002 missing/partial/wrong-parent cases; main's existing lock/release/append tests retained. |
| P1-3 rewrites | Quarterly and benchmark publishers emit immutable, content-addressed non-H1 versions before modifying aggregate files. Snapshot views merge these with the frozen baseline without opening mixed originals. | Actual quarterly publisher integration; original-open guard; growth/reordering/same-size/truncated aggregates; new version-only archive after publication crash; protected incremental rejection. |
| P1-4 rule S | Share the merged-listing conflict set with FilingDates; exclude conflicts in financial values and disclosure counts, retaining the strict prior-day boundary. | Main/supplement conflict exclusion and same-day scope regression. |
| P1-5 environment | Explicit `--real-format-trial` runs six synthetic calls through LlmRunner, sharing durable 320/eight-format budgets. Measure six-call wall time, whole process tree and a conservative real-parent/CLI-child capacity bound. Timing and memory gate start. | Fake injected into the actual flag path; no flag/no call; fake cannot pass readiness; eight-attempt cap; measured grandchild memory; start gate failures. No actual real trial was executed. |
| P1-6 once | Query canonical completed evaluation rows before inputs/computation. A completed row permits only its existing sealed result hash. Publication remains locked and idempotent. | Rollback with missing result, different hash rejection, existing-hash recovery, seal-to-registry crash recovery without recomputation. |
| P2-1 pins | Pin all LH001 modules, package initializers, cost/calendar algorithms and incremental publishers. Verify pins before final computation. Statutory/calendar notice data live in separate files and have separately recorded hashes. | Evaluation blocks changed LH001 evaluation, cost algorithm, LH001/LH003 initializers and shared calendar; mechanical-data changes preserve algorithm pins. |
| P2-2 memory | Each invalid policy/control scenario references its single A portfolio; official evaluation writes one week/scenario at a time. Invalid random paths also reference A. | Full `evaluate_saved` path on 500 stocks, 22 invalid weeks and all 52 decisions; 312 sealed weekly scenario files, real registry publication against a temporary fixture, bounded RSS. |
| P2-3 not_started | Recover a missing canonical row from the existing `not_started` seal and hash under the same execution lock. | Injected append failure followed by repeated recovery produces exactly one row without a new claim. |

## Validation results

Related-area verification: **514 passed**, 458.45 s pytest runtime, with the
committed merge-parent fixture source described below. This run included 103
LH003 cases and AV002, quarterly publishing, FW001 deployment/replay and isolated
strategy/calendar import tests. The last two calendar-boundary and three additional
dependency-pin cases are included in the final full-suite run.

Final focused LH003 run on the frozen implementation: **108 passed**, 122.49 s
pytest runtime (150.384 s with startup/teardown), exit status 0. Maximum individual
RSS was **233.863 MiB**, summed process-tree RSS **244.566 MiB**, and tree PSS
**227.632 MiB**, with no guard violation. The 500-stock / 22-invalid-week case
recorded 233.863 MiB peak and 76.047 MiB growth, with all 312 weekly scenario
records verified. Its sealed envelope and log are preserved in
[reports/round21_focused_500_stock_memory.json](reports/round21_focused_500_stock_memory.json)
and [reports/round21_lh003_tests.txt](reports/round21_lh003_tests.txt).
These runs overlap and are not independent additional tests.

Final frozen-code full suite: **5084 passed, 41 failed, 6 skipped**, 1131.10 s
pytest runtime (1195.682 s including process startup/teardown), exit status 1.
All **108 LH003 cases passed**. There were no collection/setup errors. The full
suite is not green; the remaining failures are listed below without changing
their research gates or enabling network access.

| Remaining failure | Count | Evidence / limitation |
| --- | --- | --- |
| `tests/test_experiments_ch004.py` | 34 | `backtesting/experiments/ch004.py:63` rejects merged EV001 source because it differs from CH004's approved `08079e2` / `f8c6845` hashes. These source files and the approved hashes were not edited for LH003. |
| `tests/test_market_context_ingestion.py` | 2 | The existing benchmark CLI refuses collection before 08:00 KST. The suite ran before that time; these tests do not freeze that clock. The time gate remains unchanged. |
| `tests/test_offline_test_safety.py` | 4 | The managed sandbox denies socket creation or AF_UNIX send before the tests can exercise their own guards. No socket permission was enabled. |
| `tests/test_shadow_ops.py::test_shadow_full_market_peak_rss_under_600_mib` | 1 | The existing 3000-stock shadow benchmark reports 1374.703 MiB against its separate 600 MiB assertion. This remains unresolved; the user-requested approximately 1.5 GB per-process limit was respected. |

The supervisor measured a maximum individual RSS of **1374.703 MiB**, aggregate
tree PSS of **1368.566 MiB**, and no limit violation. The largest summed tree RSS
was 1832.113 MiB during forked tests; shared pages appear in each child's RSS and
are counted proportionally by PSS. No process exceeded the 1450 MiB guard.

In the full-suite 500-stock / 22-invalid-week evaluation, the 10 ms process-tree
sampler recorded **483.168 MiB** peak and **66.754 MiB** growth during evaluation,
with **312** sealed weekly scenario records. The standalone final LH003 run is
recorded separately above. The fixture data and registry are under `/tmp/`.
No real-data capacity or live-model readiness is inferred from this synthetic
memory regression.

Exact counts, failure identities, frozen hashes and supervisor measurements:
[reports/round21_validation.json](reports/round21_validation.json).
Logs: [full suite](reports/round21_full_suite.txt),
[JUnit cases](reports/round21_full_suite.junit.xml),
[related tests](reports/round21_related_tests.txt), and
[original sandbox timeout](reports/round21_original_timeout.txt).
The full-suite memory envelope is preserved in
[reports/round21_full_500_stock_memory.json](reports/round21_full_500_stock_memory.json).

All pytest invocations use `venv/bin/python -m pytest -q -p no:cacheprovider`, one
pytest process at a time, one BLAS/OpenMP thread, disabled dotenv/live KIS/model
endpoints/tracing and `PYTHONDONTWRITEBYTECODE=1`. The final run also limits libc allocator arenas
and its trim threshold (`MALLOC_ARENA_MAX=2`, `MALLOC_TRIM_THRESHOLD_=131072`). An external supervisor samples
the tree every 50 ms and kills only its own test group if any individual RSS or
aggregate PSS exceeds 1450 MiB. RSS sums of forked workers count shared pages
repeatedly; PSS avoids that double-counting for the aggregate guard. A temporary
Python audit guard blocks actual environment-file reads, network/Codex execution,
and writes to the main checkout, production data/registry/ledger and Git metadata.
Only temporary fixture repositories and data are writable during tests.

The first full-suite attempt stopped in existing
`tests/test_ai_server_analysis_modes.py:21`: AnyIO's TestClient thread waited for
an event-loop wakeup. The same hang reproduced in a minimal FastAPI app without
the audit guard. A temporary `sitecustomize.py` bounded epoll waits to 50 ms;
the identical in-process ASGI probe then returned HTTP 200. The full suite was
rerun with this sandbox compatibility setting. This changes no repository or
venv file and does not permit network traffic. The original timeout stack and
the final run are preserved separately; an unmodified full-suite pass is not
claimed. For the still-uncommitted merge, the original preregistration fixture
helper could not find AV002/CH004 documents in the WIP HEAD. A temporary pytest
plugin verified all 22 current preregistrations byte-for-byte against committed
merge parent `ea3583936d39b07a4daa64abecf7a27eb9fb8295`, then selected that commit
only in newly cloned `/tmp/` fixture repositories. It never changes a source
checkout's refs or index, creates no commit/merge and checks out no environment
files. The branch tip moved during this work, so the plugin pins the commit
object rather than following the moving branch. The same plugin memoizes the
original pure lunar/solar conversion functions (immutable tuple results) to avoid
repeated identical dependency arithmetic; calendar dates, notices and verification
rules remain unchanged.

The exact supervisor, audit guard and pytest plugin used for the final run are in
[reports/round21_test_harness](reports/round21_test_harness). They are validation
artifacts, not production runner changes. An earlier compatibility rerun was stopped by an overly strict sum-RSS
supervisor threshold during existing concurrent-append fork tests (individual
peak 274.555 MiB, summed RSS 1614.719 MiB). The final supervisor retains the
individual RSS cap and uses aggregate PSS for physical-memory accounting.

## Remaining operator prerequisites

The implementation does not certify deployment or profitability. Before an
actual start: verify both AV002 terminal rows and AV001/EV001 releases; supply
2026-11-19 official hours and 2027 official calendar coverage; deploy the immutable
version publishers; explicitly run the permitted real synthetic-format trial;
provide real sync and reboot/suspend/logout evidence; commit reviewed code.
Without verified calendar coverage the whole fixed plan refuses, before a claim.
The start date and 52-week/evaluation windows are unchanged. Unknown 2027 trade
or T+2 tax dates still refuse evaluation until a legally sourced mechanical rate
update; no rate or calendar notice was invented here.

See [implementation_notes.md](implementation_notes.md) for the commands, artifact
contracts, budgets and startup sequence, and
[IMPLEMENTATION_BLOCKERS.md](IMPLEMENTATION_BLOCKERS.md) for deployment items.

## Files changed

The following 34 paths are this task's implementation, tests, documentation and
derived collector manifest changes. Existing staged merge changes and the user's
review/venv files are preserved. Validation logs, JSON summaries and the temporary
test harness are additional artifacts under `reports/`.

- `backtesting/cost_model.py`
- `backtesting/experiments/lh003/__main__.py`
- `backtesting/experiments/lh003/calendar.py`
- `backtesting/experiments/lh003/environment.py`
- `backtesting/experiments/lh003/evaluate.py`
- `backtesting/experiments/lh003/executions.py`
- `backtesting/experiments/lh003/inputs.py`
- `backtesting/experiments/lh003/resources.py`
- `backtesting/experiments/lh003/runner.py`
- `backtesting/experiments/lh003/simulation.py`
- `backtesting/experiments/lh003/snapshot.py`
- `backtesting/experiments/lh003/workflow.py`
- `backtesting/experiments/ordering.py`
- `backtesting/statutory_rates.py`
- `research/experiments/FW001_forward_replication/deployment_manifest.json`
- `research/experiments/FW001_forward_replication/manifest_changes/ca1f9c3a6dd85a9814113947487f6b614bc6e8cfc6a4522ac872156685204cc1.json`
- `research/experiments/FW001_forward_replication/manifest_history/2d9b62fa23a651716b9738e43fd8a174656696dcb10b4d5e5ab6c9b7634ae8aa.json`
- `research/experiments/LH003_llm_leader_forward/IMPLEMENTATION_BLOCKERS.md`
- `research/experiments/LH003_llm_leader_forward/implementation_notes.md`
- `research/experiments/LH003_llm_leader_forward/validation_report.md`
- `scripts/ops/collector_manifest.py`
- `scripts/ops/push_collector_code.sh`
- `scripts/ops/research_run.sh`
- `src/ingestion/dart_quarterly.py`
- `src/ingestion/incremental_versions.py`
- `src/ingestion/krx_benchmarks.py`
- `src/krx_calendar_data.py`
- `src/runner/trading_calendar.py`
- `tests/test_dart_quarterly.py`
- `tests/test_experiments_av002.py`
- `tests/test_experiments_lh003_evaluate.py`
- `tests/test_experiments_lh003_round21.py`
- `tests/test_experiments_lh003_runner.py`
- `tests/test_experiments_lh003_snapshot.py`
