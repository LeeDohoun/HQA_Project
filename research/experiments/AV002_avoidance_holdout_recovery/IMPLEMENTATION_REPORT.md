# AV002 implementation and read-only validation

Worktree: `/home/are01/src/HQA_Project/HQA_av002`, branch `claude/av002-runner`,
base `2f65ab346da5aef67eaedaa7b6e26c60b87b6c3c`.
The immutable preregistration is `5545e9a53479c68b4f5e10c59c316bf018111429`.

This is an implementation/validation report, not a holdout result. The requested
read-only validation is complete (2026-10-18 KST).

## Implementation

- `backtesting/experiments/av002.py`: CLI, sealed parent adapter, non-recorded
  reproduction, metadata-only preflight, fixed 2025-H1 rehearsal with artifact
  hashes, frozen abort-record inputs, code manifest and single-attempt holdout.
  The AV001 module is loaded with separate globals so its unchanged computation
  uses AV002's ID without changing AV001's running process or reducing the
  three-variant reproduction family. AV002 publishes only two holdout rows.
- `backtesting/experiments/av001.py` and `src/ingestion/dart_listing.py`: forward
  the calling experiment ID through the auxiliary non-protected price hashing
  used to verify the listing target inventory. No selection/accounting changes.
- `backtesting/experiments/ordering.py`: verify both AV002 terminal rows against
  one result file, one execution, the captured registration, the sealed parent,
  and the result's compact summaries. `sensitivity_unavailable` is terminal only
  for AV002 and remains non-pass. The lock uses the shared Git common directory
  so linked worktrees cannot bypass serialization.
- `backtesting/holdout.py`: enforce the AV002 release gate on direct successor
  claims and retain the shared lock throughout EV001/LH003/LH002 sessions.
- `backtesting/experiments/ev001.py`: check AV002's release under the execution
  lock before evaluating EV001's holdout.
- `backtesting/experiment_registry.py`: validate a complete terminal batch before
  one locked append. Interrupted batches use the registration captured at startup,
  including when the working preregistration changes during execution.
- `scripts/ops/research_run.sh`: support AV002's default and all five explicit
  modes without injecting `--execute`; permit its generated rehearsal/manifests.
- `tests/test_experiments_av002.py`: fake/fixture branch tests and real local Git
  checks, including parent integrity, all three reproduction variants, frozen
  inputs, pre-open ordering, post-claim aborts, release tampering, cross-worktree
  and cross-process locks, whole-session lock lifetime, rehearsal and CLI.
- `tests/test_research_ops.py`: AV002 dispatch and output-directory checks.
- `tests/test_trading_calendar_overrides.py`: supply the missing registration
  fixture so two existing design-preflight tests reach the intended calendar check.

The two registered calendar corrections were already present at the base commit;
`src/runner/trading_calendar.py` was not modified. LH003 has no dedicated seal
runner in this checkout. Its direct `HoldoutSession` claim and session lifetime
are guarded centrally; its preregistration and 2026-11-13 deadline are unchanged.

The current implementation is deliberately uncommitted. Section 7 requires the
implementation in a code commit before opening; `--holdout` refuses uncommitted
code after writing its pre-open code manifest. No commit was made in this
worktree; real-Git tests use disposable fixtures under `/tmp`.

## Fixed parent and inputs

All three section-1.2 file hashes match the committed preregistration:

| AV001 result file | SHA-256 |
| --- | --- |
| `20261017T115416329783Z.json.gz` | `6c5e6e54779c305cee1dbb312e20a659d43db764f42541b68d5ac822dada16f9` |
| `20261017T125256426846Z.json` | `2c6202d9f85f83e3df397ec01f38d5b3425d4c85c2bbc5c5e71d5ef631ad26ca` |
| `holdout_aborted_20261017.log` | `b7f8cb8a9a095c59b242208ba1da470943141bc8ffbf6cebafda746c8d739437` |

Passing variants are inherited as `{av_all, av_conc}` from AV001's Holm(3)
result. The full S/O three-variant state has SHA-256
`b8daf99f7f8e27d06ecd5425be4952f6b6ecd6ff8d8949cb538ab5e9b27577da`,
decision month 2025-11 and valuation date 2025-12-30. No AV002 design rows or
Holm(2) selection are introduced.

The abort record says `aborted; inputs not verified`. Its expected hashes are
not treated as evidence of a successful prior check. The runner compares actual
protected bytes immediately after AV002 session entry and again at completion;
any mismatch aborts both variants and cannot replace the frozen snapshot.

The separate read-only unprotected input audit matched 9,079 files against the
parent/abort records: 2,700 prices, 44 financial archives, 1 company file, 3,783
main listings, 2,548 supplement files and 3 coverage-metadata files.
Its audit recorded 0 protected price opens and 0 denied operations.

## Validation

All real-data commands explicitly use
`--data-dir /home/are01/src/HQA_Project/HQA_Project/data`.
Output files remain under this worktree. A temporary Python audit hook rejects
network calls, `.env` opens, source-data mutations, protected price opens and
real ledger/registry writes. Bytecode writes are disabled.

The checkout contains 24 tracked legacy `data/` files, rather than the current
experiment dataset. They were not used as experiment inputs, were unchanged,
and no new files were created there. All experiment reads used the explicit
external data directory above; no result was written under either data directory.

| Check | Result | Wall time | Peak RSS |
| --- | --- | --- | --- |
| Related regression tests | 581 passed, 1 skipped (optional pre-existing HC003 archive comparison) | 667.36 s | — |
| Final AV002 branch tests | 127 passed after the final runner/provenance changes | 279.68 s | — |
| `--preflight` | Pass: 141 calendar/file sessions, no missing/unexpected files, 0 data-content reads | 6.78 s | 98.52 MiB |
| `--reproduce` | Pass: all six monthly e series (103 entries each) and the full three-variant state match the parent | 57 min 0.96 s | 2878.07 MiB |
| `--rehearse` | Completed: S/O × all three variants, six valid months each; result/summary/manifest hashes verified | 1 h 13 min 57 s | 2184.09 MiB |

The reproduction compared 103 decision months for every combination of S/O and
`av_all`/`av_phase`/`av_conc`, with exact equality and no tolerance. The reproduced
state SHA-256 is
`b8daf99f7f8e27d06ecd5425be4952f6b6ecd6ff8d8949cb538ab5e9b27577da`.
Comparison states, design-only effects and non-code inputs also matched.
The external process measurement above includes startup and parent verification;
the computation reported 3395.4985 seconds and 2791.86 MiB before the outer
comparison/serialization. Its audit recorded 36,243 data read opens, 0 protected
price opens and 0 denied operations.

Before the rehearsal, MemAvailable was 6,877,986,816 bytes, exceeding the
5,000,000,000-byte minimum. The rehearsal started from the reconstructed
2024-11 decision state valued on 2024-12-30, traded first on 2025-01-02,
and ended on the 2025-06-30 close. Both control types used 200 paths per
rule/variant. Its audit recorded 49,711 data read opens, 0 protected price
opens and 0 denied operations.

Historical rehearsal verdicts were `pass` for av_all and av_conc under both
rules; av_phase was `delisting_sensitive` (S pass, O delisting_sensitive).
These are rehearsal diagnostics only. They do not select AV002 variants or
constitute AV002 holdout verdicts.

The result is `rehearsal/20261017T160500126481Z.json`, SHA-256
`f20060d329b408d4848d89744f186ee692402e3bfbc9e9d546b4a825c9076270`.
The result/summary hash manifest is
`rehearsal/20261017T160500126481Z.sha256.json`, SHA-256
`8a0bdfd75fed990f2ce4c17849eb064eb23321a2c4edfbbe88cbde3af63f052d`.

The related regression command covered:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q \
  tests/test_experiments_av002.py tests/test_experiments_av001.py \
  tests/test_experiments_ev001.py tests/test_experiments_ordering.py \
  tests/test_backtesting_experiment_registry.py tests/test_backtesting_holdout.py \
  tests/test_trading_calendar_overrides.py tests/test_research_ops.py \
  tests/test_dart_listing.py tests/test_dart_financial_date_rules.py --tb=short
```

After the final runner/provenance changes, `tests/test_experiments_av002.py`
was rerun separately with all 127 tests passing.

The first non-recorded reproduction was interrupted before completion to fix
the auxiliary price-hash ID propagation. It opened no holdout and contributes
no reproduction verdict. Its files are named `validation/reproduce_before_id_fix.*`.

Evidence paths:

- `validation/implementation_code.sha256.json`: 11 changed code/test files and
  28 computation/changed source fingerprints, with the uncommitted status explicit.
- `validation/tests.txt`, `validation/av002_tests.txt`: test outputs.
- `validation/unprotected_input_check.json` and `.stderr.txt`: input audit.
- `validation/preflight.*`, `validation/reproduce.*`, `validation/rehearsal.*`: final command outputs,
  external wall-time/RSS measurements and read-only audit counts.
- `validation/rehearsal_memory.json`: the pre-launch memory check.
- `validation/final_checks.json`: verified all 28 source fingerprints, all three
  parent seals, reproduction series hashes, rehearsal periods/control counts and
  artifact hashes; HEAD, preregistrations, parent files, ledger/registry and legacy
  data are unchanged.
- `rehearsal/`: the completed historical rehearsal's result, summary and SHA-256 manifest.

## Interpretation

AV002 is a technical recovery and a second approach to AV001's same holdout,
not a new independent validation. A successful reproduction establishes agreement
with the saved computation; rehearsal performance is never used to judge the
holdout. This task does not open or evaluate the real holdout, append real ledger
or registry rows, read `.env`, make network requests, or commit code.
