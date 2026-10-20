# QP003 offline runner

The committed QP003 preregistration controls this experiment. QP001 financial
selection, group targets, execution, statistics, atomic artifacts and execution
lock are imported without changing their implementation or tests. QP003 keeps
its own durable starts and recovery policy.

Each S/O decision independently permits rebalancing exactly when A is nonempty
and `10 * len(C) >= 9 * len(A)`. Other valid decisions are `coverage_hold` or
`empty_A`. Holds preserve every portfolio's units and cash, continue valuation,
keep the resulting monthly e, and consume no control draws. Resumption uses the
carried state. Each cost scenario and the full -100% recomputation apply the
same permissions. Design and validation form one continuous path.

Precheck never loads forward returns, constructs a PriceBook, values holdings,
trades, or computes NAV/e/control performance. It does compute the inherited
60-session trailing volatility signal and all 103 rule-specific selections.
Listing, integrity and missing-source errors stop the run; they cannot become
holds. The original QP001 source inventory must still exist. The committed
QP001 precheck SHA-256 is pinned and sealed with the QP003 input file/hash
specification and registry SHA-256, fixed M=168 and its registered formula.

From `/home/are01/src/HQA_Project/HQA_qp001`:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --precheck --data-dir /home/are01/src/HQA_Project/HQA_Project/data --output backtesting/experiments/qp003/precheck_20261020_round46_final.json
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --reproduce-av001 --data-dir /home/are01/src/HQA_Project/HQA_Project/data
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --memory-probe --data-dir /home/are01/src/HQA_Project/HQA_Project/data --output backtesting/experiments/qp003/memory_probe_20261020_round46_final.json
```

Reproduction verifies the published AV001 result and price hashes, reads only
its first three published months (May-July 2017), and uses the QP003 hold-aware
simulation with all decisions permitted and one group of weight 1. It writes
no experiment result or registry row.

The memory probe builds all 206 **real** selections using the same detail-retaining
call as formal execution, then continues in that Python process with synthetic
prices for every real universe name. It runs 16 paired cost scenarios and eight
200-path control sets, including the real S/O permissions and -100%. It keeps
the full monthly selection frames and ROA provenance through streaming saves,
save-only recovery and idempotent recovery. Only synthetic prices are valued;
no real forward prices or strategy returns are read. Probe starts, complete
results, controls and the actual recovery registry live in a temporary tree;
only preregistration lookup there uses the real checkout's verified plan.
Peak RSS is measured with `getrusage`, including preparation, save and recovery,
and must stay below 1,400,000,000 bytes. The address-space limit is reported
separately. Every CLI mode sets BLAS threads to one and caps address space at
1.49 GB, below the 1.5 GB per-process limit. A lower inherited hard limit also
applies; for the full probe, use `ulimit -v 1455078` rather than the smaller test
limit below.

Real QP003 returns require this explicit command; implementation validation
does **not** run it. After transferring the unchanged QP003 package and new
precheck to the **main checkout**, run from there:

```bash
cd /home/are01/src/HQA_Project/HQA_Project
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --execute --data-dir /home/are01/src/HQA_Project/HQA_Project/data --precheck-file backtesting/experiments/qp003/precheck_20261020_round46_final.json --output research/experiments/QP003_profitability_on_av_coverage_hold/results/design_validation_20261020_round46.json
```

The formal run rejects any added/removed/changed input file or code hash
relative to precheck before the first performance-price read, and checks the
snapshot again before publishing. A code/data change requires a new precheck
artifact. Before selection or performance reads, an exclusive, atomic, file-
and directory-fsynced start is published in
`research/experiments/QP003_profitability_on_av_coverage_hold/attempts/<run_id>.start.json`.
It fixes the run ID, input-spec digest, code hashes, preregistration commit/blob,
registry digest, precheck seal and result path. Starts stay beside the checkout's
canonical registry. A start without a terminal row blocks every new execution
before input reads, including after SIGTERM or an OOM/SIGKILL.

Control details and final states are streamed to eight hash-sealed files.
The complete input-spec digest is checked before saving. A small, exclusive,
fsynced `.recovery.json` receipt then stores registration metadata, the input-spec
digest and code hashes, the complete result SHA-256 and all control-detail hashes.
Recovery reads this receipt and hashes the full result in bounded blocks; it
never expands the complete monthly result JSON. The registry seals both files.
Registration follows the durable result and receipt saves. A caught failure without
a result records `design_aborted` with the start's original run ID. If a complete
result exists but registration fails, the start stays unfinished for save-only
recovery; it does not create a second trial. Recover by the ID printed in the
refusal (substitute the actual 32-character ID):

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --recover-attempt RUN_ID
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp003 --abort-attempt RUN_ID
```

`--recover-attempt` registers the stored result if present; otherwise it records
`design_aborted`. `--abort-attempt` explicitly records that verdict, including
when a corrupt/incomplete result cannot be recovered. A kill between result and
receipt publication leaves the original start pending and requires this explicit
abort; it never permits recomputation. Recovery verifies the
requested ID, immutable start, preregistration, saved input-spec digest and
control hashes, and fsyncs the saved files before registration. It never reads
input data or recomputes and is idempotent. An aborted ID cannot later register
a result. `--recover-registry SAVED_RESULT` also verifies the matching start.

The judge requires all 80 design and 23 validation months in every scenario,
at least 36 permitted design decisions, defined design SE, t>3, p<=0.05/168,
random share<=5%, positive x1.5 mean e, design NAV end/start>1 at x1/x1.5,
and positive validation mean e under both S/O and -100%. Status precedence is
insufficient, sensitivity_unavailable, version_sensitive, delisting_sensitive,
then fail/pass. A valid coverage hold is not an insufficiency.

Serial offline regression tests:

```bash
ulimit -v 1367187
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_experiments_qp003_*.py tests/test_experiments_qp001_*.py
```
