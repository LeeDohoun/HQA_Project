# QP003 round 46 validation — 2026-10-20

Worktree: `/home/are01/src/HQA_Project/HQA_qp001`, HEAD
`21be7c8b90058d4a3ffbdf2b26ce8a07d4682199`, with uncommitted QP003 implementation.
Both round-46 findings are addressed. Real QP003 `--execute` was **not run**.
Real financial/selection data were built; all new performance calculations used
synthetic prices. No main-checkout writes, preregistration edits, `.env*` access,
network requests, staging or commits were performed. No changed source/test path
intersects the FW001 inventory; tracked and staged diffs remain empty.

## P1: durable start and one-ID recovery

- `attempts.py:49` exclusively publishes an atomic, file- and directory-fsynced
  start under `research/experiments/QP003_profitability_on_av_coverage_hold/attempts/`.
  The record fixes `run_id`, input-spec SHA-256, code hashes, preregistration
  commit/blob and captured plan, code commit, registry SHA-256, precheck seal and
  result path. `runner.py:196` does this before selection and performance reads.
- `runner.py:281` refuses every new execution if a start lacks a terminal registry
  row, before reading inputs. The record is rooted in the same checkout as its
  canonical registry, including when the package is run from main.
- `--recover-attempt RUN_ID` only registers its stored result or records
  `design_aborted` when no result exists. `--abort-attempt RUN_ID` explicitly
  records an abort. Both retain the original ID; aborts and registrations are
  idempotent. Aborted IDs cannot subsequently register results.
- A caught failure before result publication records the original ID as aborted.
  Registry failure after a complete save leaves that start pending for save-only
  recovery, without creating another trial.
- `saved.py:27` validates the complete input-spec digest and durably saves the
  full result, followed by a small immutable `.recovery.json` receipt. Recovery
  verifies the original start, result SHA-256 and all eight control-detail hashes
  while hashing the full result in 1 MiB blocks. The registry seals the result
  and receipt hashes. It never expands the full monthly result JSON.
- If killed between result and receipt publication, the start still blocks
  recomputation; the incomplete package requires explicit `--abort-attempt`.

Actual subprocess tests send **SIGTERM and SIGKILL**, each during synthetic
performance/control computation and after durable save before registration
(four kill cases). SIGKILL simulates the termination behavior of an OOM kill;
no real OOM was induced. Restart refuses recomputation, then resolves the same
ID by stored-result registration or abort. Additional tests cover exclusive
creation, fsync ordering before prices, ID mismatches, missing receipts, result
and control tampering, and idempotence. Recovery tests prohibit full-result
`json.load` and all input/performance functions.

## P2: measured one-process pipeline

[Final probe](memory_probe_20261020_round46_final.json),
[full timing/stage log](memory_probe_20261020_round46_final.log).
One Python process built all **206 real monthly selections** using formal
`precheck_only=False` preparation, keeping their DataFrames and ROA provenance
resident throughout accounting, save and recovery. It then used synthetic prices
for **2,425 real universe names**, 103 months, 16 paired cost scenarios,
and **eight control sets of 200 paths** (S/O, main/−100%, matched/independent).
All real S/O permissions were preserved: S 10 holds; O 0 holds.

ROA details retained: S **37,153**, O **39,232**.
Full result: **64,889,341 bytes**; streamed control details:
**124,725,336 bytes**; recovery receipt:
**20,902 bytes**. Complete temporary artifacts were removed
after verification. Only preregistration lookup in that temporary tree reused
this checkout's verified plan; actual result, receipt, start, file/hash checks
and registry append/recovery ran against the temporary tree.

| Stage (cumulative OS peak RSS) | Bytes |
| --- | ---: |
| real_selections_retained | 1,376,235,520 |
| synthetic_prices_with_real_selections | 1,376,235,520 |
| all_eight_200_path_scenarios_retained | 1,376,235,520 |
| fsynced_result_with_real_selections | 1,376,235,520 |
| save_only_recovery_with_real_selections | 1,376,235,520 |
| idempotent_recovery_with_real_selections | 1,376,235,520 |
| finished | 1,376,235,520 |

Peak RSS measured by `getrusage(RUSAGE_SELF)` is **1,376,235,520 bytes**
(**1.376235520 GB**), below **1,400,000,000 bytes** by
**23,764,480 bytes**. Independent `/usr/bin/time -v`
agrees: **1,343,980 KiB**, exit 0, no swaps; full process wall time **34:45.95**.
The address-space cap is separate: **1,489,999,872 bytes**
(inherited 1.49 GB hard limit), below the user's 1.5 GB per-process limit.

First recovery appended exactly one temporary registry row; second recovery
appended none. Both report `recomputed=false`; the full result SHA-256 stayed
unchanged. Probe and final precheck share input-spec SHA-256
`967d08447b3c43a6d7b008570b383ef41edf81abd2808c0d649dfdd9b96b1c0a`. Real forward prices and real performance were not
loaded or computed. Real forward-price loading itself was not exercised.

Earlier failures are retained, rather than treated as passing evidence:

- [1.40 GB AS attempt](memory_probe_20261020_round46_as140_failure.log): allocation
  failure during real selection preparation; measured RSS 1,306,374,144 bytes.
- [1.49 GB AS attempt before receipt fix](memory_probe_20261020_round46_as149_recovery_failure.log):
  all selections, eight controls and result save completed, but whole-result
  recovery loading failed; measured RSS 1,391,382,528 bytes. This led to the
  small durable receipt and bounded hash reader. The final pipeline above passed.

## Final tests and return-free precheck

Serial QP003 plus unchanged QP001 regression tests: **151 passed in 7.68s**.
[Test output](tests_20261020_round46_final.log).

```bash
ulimit -v 1455078
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_experiments_qp003_*.py tests/test_experiments_qp001_*.py
```

[Final precheck](precheck_20261020_round46_final.json),
[timing/stage log](precheck_20261020_round46_final.log): **ok**,
`returns_computed=false`, `forward_prices_used=false`,
`source_snapshot_unchanged=true`. All 103 selections per rule completed.
S: design 73 permitted / 7 held, validation 20 / 3, total 93 / 10.
O: design 80 / 0, validation 23 / 0, total 103 / 0.
Precheck RSS: **1,137,647,616 bytes**. These are availability counts,
not an investment-performance verdict.

- Input specification: **13,614 data files, 45 code/preregistration files, 1 lineage file**.
- Input-spec SHA-256: `967d08447b3c43a6d7b008570b383ef41edf81abd2808c0d649dfdd9b96b1c0a`.
- Final precheck file SHA-256: `e44aa2d14983591d6da317036b0e102b9f4de58a847e58d990a6727f2eaa38ab`.
- Final memory artifact SHA-256: `3133c96b7bb1dc73dbfe4e17f5fe1b03eea878a01de0f5c33e2661d40b5387f3`.
- Registry SHA-256 (main and worktree unchanged): `be2e516e3fa1e45871852f1a0b1c481e094c60f5373775cca404af5e6d76d905`.
- QP001 precheck SHA-256: `91660fbb1da3df1fab41d7fcf5aa2e840427257b46ea237af7e7c378ac3ea88b`.
- M remains **168**, formula **82 + 84 + 1 + 1**.

The previous `precheck_20261020.json` and intermediate
`precheck_20261020_round46.json` are retained for history and do **not** seal the
final code. Use only the `_round46_final.json` precheck for this implementation.

## Reproduction commands used

From this worktree, with numerical threads fixed at one by the QP003 entrypoint:

```bash
ulimit -v 1455078
PYTHONDONTWRITEBYTECODE=1 /usr/bin/time -v venv/bin/python -B -m backtesting.experiments.qp003 --memory-probe --data-dir /home/are01/src/HQA_Project/HQA_Project/data --output backtesting/experiments/qp003/memory_probe_20261020_round46_final.json
PYTHONDONTWRITEBYTECODE=1 /usr/bin/time -v venv/bin/python -B -m backtesting.experiments.qp003 --precheck --data-dir /home/are01/src/HQA_Project/HQA_Project/data --output backtesting/experiments/qp003/precheck_20261020_round46_final.json
```

The independent precheck ran alongside the one-process memory pipeline. The
memory proof does not combine RSS or preparation results from those processes.
Outputs are exclusive; these exact output filenames already exist.

## Exact MAIN execution command — NOT RUN

Main is still at `21be7c8b90058d4a3ffbdf2b26ce8a07d4682199` and has no QP003
package yet. Its **36 inherited code/preregistration hashes** and registry hash
were verified against the final precheck. First transfer these **9 identical
QP003 source files** plus `precheck_20261020_round46_final.json` to their matching
paths in main; this task did not write there:

- `backtesting/experiments/qp003/__init__.py`
- `backtesting/experiments/qp003/__main__.py`
- `backtesting/experiments/qp003/attempts.py`
- `backtesting/experiments/qp003/judge.py`
- `backtesting/experiments/qp003/memory_probe.py`
- `backtesting/experiments/qp003/portfolio.py`
- `backtesting/experiments/qp003/runner.py`
- `backtesting/experiments/qp003/saved.py`
- `backtesting/experiments/qp003/selection.py`

Then the exact command uses main's own module, data, registry and attempts tree:

```bash
cd /home/are01/src/HQA_Project/HQA_Project
ulimit -v 1455078
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 \
  --execute \
  --data-dir /home/are01/src/HQA_Project/HQA_Project/data \
  --precheck-file backtesting/experiments/qp003/precheck_20261020_round46_final.json \
  --output research/experiments/QP003_profitability_on_av_coverage_hold/results/design_validation_20261020_round46.json
```
