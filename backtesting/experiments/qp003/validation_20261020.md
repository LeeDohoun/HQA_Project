# QP003 implementation validation — 2026-10-20

Worktree: `/home/are01/src/HQA_Project/HQA_qp001`, HEAD
`21be7c8b90058d4a3ffbdf2b26ce8a07d4682199`.
Real QP003 `--execute` was **not run**. QP003 strategy returns, NAV and control
performance were computed only on synthetic test/probe inputs. The real-data
precheck reports `returns_computed=false`, `forward_prices_used=false`, and
`source_snapshot_unchanged=true`.

## Checks

- Serial offline QP003 + unchanged QP001 regression suite: **142 passed in 5.84 s**.
- Real-data `--precheck`: **ok**, all 103 monthly selections per rule completed.
- `--reproduce-av001`: **ok**, May–July 2017, both rules, no holds, using QP003's
  hold-aware simulation and the group execution branch with alpha=1.
- Tracked-file diff is empty. All task files are new QP003 package/test files;
  none intersects the FW001 deployment inventory. No staging or commit occurred.

```bash
ulimit -v 1367187
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_experiments_qp003_*.py tests/test_experiments_qp001_*.py
```

Tests cover integer permission boundaries, empty A, independent S/O permission,
fatal listing/source/integrity errors, hold-month e retention and group P&L,
first-decision cash, exact unit/cash continuity, resumption, skipped random draws
for both control types, carried control holdings, full mean denominators,
missing observations/paths, independent costs, disappearance during a hold,
all judgement boundaries and status precedence, pre-performance input rejection,
post-performance abort recording, atomic fsynced saves, and save-only recovery.

## Real-data permission counts

| Rule | Design checked | Design permitted / held | Validation checked | Validation permitted / held | Total permitted / held |
| --- | ---: | ---: | ---: | ---: | ---: |
| S | 80 | 73 / 7 | 23 | 20 / 3 | 93 / 10 |
| O | 80 | 80 / 0 | 23 | 23 / 0 | 103 / 0 |

S hold months: **2018-05, 2019-05, 2019-06, 2022-05, 2022-06, 2022-07,
2023-06, 2025-05, 2025-06, 2025-07**. All other decisions from 2017-05 through
2025-11 are permitted under S. O permits the entire range and has no holds.
Neither rule has an empty-A month. Longest consecutive hold: S=3, O=0.
Both rules exceed the 36-permitted-design-decision minimum; this is a data
availability check, not a performance verdict.

Minimum C/A: S=0.8417910447761194, O=0.9416666666666667.
Maximum U/A/C counts: S=1772/577/549; O=1774/582/573.
Asset/version links: 163,562 matched out of 163,799 (0.9985531047198091).
Rule-O correction-link sensitivity is available in both stages.
The machine-readable artifact contains every permitted/held month, counts,
failure reasons, groups, source hashes and filing-rule reports:
[precheck_20261020.json](precheck_20261020.json).

## Seals

- M: **168**, registered formula **82 + 84 + 1 + 1**.
- Registry SHA-256: `be2e516e3fa1e45871852f1a0b1c481e094c60f5373775cca404af5e6d76d905`.
- QP001 precheck SHA-256: `91660fbb1da3df1fab41d7fcf5aa2e840427257b46ea237af7e7c378ac3ea88b`.
- QP003 input specification SHA-256: `bdcbf020cfb7106e83cb1a5ca64cf7280228e224a456795a9056d9639969a6fa`.
- QP003 precheck file SHA-256: `ae6354cece254180e93561c3ef4a6bf41e102133c090fc204a9d3bf18243bbfb`.
- Specification inventory: **13,614 data files, 43 code/preregistration files,
  1 lineage file**. Registry bytes are sealed separately.

The formal runner compares the complete specification to precheck before any
performance-price loading, checks it again immediately before that loading,
and verifies it after computation before result publication. The precheck file
itself is also hashed and checked again before publication.

## Reproduction and memory

AV001 maximum absolute monthly excess-return difference:

| Rule | Months | Holds | Maximum difference |
| --- | ---: | ---: | ---: |
| S | 3 | 0 | 3.469446951953614e-18 |
| O | 3 | 0 | 2.6020852139652106e-18 |

Both are below 1e-12. Published AV001 result SHA-256:
`6c5e6e54779c305cee1dbb312e20a659d43db764f42541b68d5ac822dada16f9`.
Reproduction peak RSS: **251,174,912 bytes**.

- Real-data precheck peak RSS: **1,130,491,904 bytes**.
- Full synthetic accounting/control/serialization peak RSS: **625,860,608 bytes**.
- CLI virtual-address limit: **1,400,000,000 bytes**, with numerical threads fixed at one.
- Synthetic scale: 206 independent selections, 1,500 stocks, 103 months,
  16 paired cost scenarios, eight 200-path control sets, main and -100% paths,
  first/consecutive holds and different S/O permissions. Synthetic hold counts:
  S=24, O=13.
- Synthetic elapsed time: 641.6596604170009 s; streamed result 143,599,422 bytes,
  control details 181,704,358 bytes. Temporary full artifacts were cleaned up.

See [memory_probe_20261020.json](memory_probe_20261020.json), SHA-256
`451550fbc7573ca708197bbbff8517616941730dde8c62bd264aa970d0a69f45`.
Financial/archive parsing and full synthetic accounting were measured in
separate processes. A real performance execution was not measured or run.

## New files

- `backtesting/experiments/qp003/__init__.py`
- `backtesting/experiments/qp003/__main__.py`
- `backtesting/experiments/qp003/selection.py`
- `backtesting/experiments/qp003/portfolio.py`
- `backtesting/experiments/qp003/judge.py`
- `backtesting/experiments/qp003/runner.py`
- `backtesting/experiments/qp003/memory_probe.py`
- `backtesting/experiments/qp003/README.md`
- `backtesting/experiments/qp003/precheck_20261020.json`
- `backtesting/experiments/qp003/memory_probe_20261020.json`
- `backtesting/experiments/qp003/validation_20261020.md`
- `tests/test_experiments_qp003_selection.py`
- `tests/test_experiments_qp003_portfolio.py`
- `tests/test_experiments_qp003_judge.py`
- `tests/test_experiments_qp003_runner.py`

## Exact future execution command (not run)

From `/home/are01/src/HQA_Project/HQA_qp001`:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --execute --data-dir /home/are01/src/HQA_Project/HQA_Project/data --precheck-file backtesting/experiments/qp003/precheck_20261020.json --output backtesting/experiments/qp003/results/design_validation_20261020.json
```

A changed input specification requires a new successful precheck. A complete
result whose registry append failed can be registered using `--recover-registry`
without loading data or recomputing performance; the preceding aborted row is retained.
