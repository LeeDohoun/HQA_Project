# QP001 offline runner

The committed preregistration is authoritative. New implementation files live
only in this package; AV001, HC003 and the raw collector are imported read-only.
All four cost scenarios and both date rules have separate portfolio states.
Design and validation use different actual-public-date cutoffs, while holdings,
cash and random generators continue across the decision-month boundary.
Both stages must independently have rule-O correction-link sensitivity available.

Run from the QP001 worktree. `-B` prevents bytecode writes through the shared
virtual environment. The module sets BLAS threads to one and caps address space
at 1.40 GB. No mode uses the network or opens a holdout session.

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp001 --precheck --data-dir /home/are01/src/HQA_Project/HQA_Project/data --output backtesting/experiments/qp001/precheck_20261020.json
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp001 --reproduce-av001 --data-dir /home/are01/src/HQA_Project/HQA_Project/data
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp001 --reproduce-av001-groups --data-dir /home/are01/src/HQA_Project/HQA_Project/data
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp001 --memory-probe --output backtesting/experiments/qp001/memory_probe_20261020.json
```

Precheck derives HF001 F_vol from exactly 60 decision-inclusive verified daily
log returns in-process. It computes the decision-date universe, ROA, A_t/C_t,
and group sizes through the same selection functions as the performance run.
It never calls a forward-return loader, PriceBook, rebalance, portfolio valuation,
NAV, e_t, executability/survival checks or control performance. No external
volatility cache is required. Each of 103 months x S/O records completeness,
failure reasons, group sizes and its 60-session price hashes linked to the full
source manifest. Date-rule and actual-public-date stage gates remain applied.
Monthly selection failures remain explicit rows; incomplete checks exit 2.

The integrity scan checks raw-response hashes, balance-sheet derivations,
saved-version links, listing-status coverage and overlapping operating-income
accounts. Rows outside the request are skipped and counted using research_data's
current scope rules, including in the operating-income comparison. Ready requests
are frozen without repairing or locking the collector. Pending requests or source
changes produce an incomplete report. The stored-version link ratio and current
Y/K/N/E listing proxy are separate from monthly C_t/A_t completeness.

Both reproduction modes verify the sealed AV001 result and required price hashes
and decode only the first three published months (May-July 2017) for S/O. The
original mode checks HC003 accounting; the groups mode uses one group with alpha=1
and checks that every month passed through the new group-weight branch. Both
compare monthly e within 1e-12, read only the published window, and print to stdout.
They do not write experiment results or registry rows.

The synthetic memory probe creates 206 independent selections of 1,500 stocks,
a 103-month quote book, 16 paired cost-scenario runs and 8 sets of 200 control
paths. It keeps selections/results resident through streaming result serialization
and reports peak RSS, address-space limit, file sizes and elapsed time. It uses
no real market returns. Raw financial parsing is measured separately by precheck.

Real performance computation remains behind explicit `--execute` and requires a
new `--output` path. It was not run for round 43. After performance reads start,
failures in computation, final hashes, serialization, saving or registry append
flow through `ordering.design_attempt` and record `design_aborted`. The complete
result is streamed into a temporary file, fsynced and atomically published before
appending the registry summary. All eight control-detail streams and terminal
states are similarly saved separately with hashes; they are not retained eight
times in the result. M=167 and the pre-append registry hash remain sealed.

If the complete result was saved but its registry append failed, register the
saved result without loading prices or recomputing any experiment:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -B -m backtesting.experiments.qp001 --recover-registry backtesting/experiments/qp001/results/design_validation.json
```

Recovery verifies the preregistration and detail-file hashes and is idempotent
for the same result hash/run ID. An aborted row remains in the history. Partial
results cannot be recovered. Output files are exclusive and must stay inside
this worktree and outside the data root.

Serial offline tests:

```bash
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_experiments_qp001_financials.py tests/test_experiments_qp001_portfolio.py tests/test_experiments_qp001_judge.py tests/test_experiments_qp001_runner.py tests/test_experiments_qp001_published.py
```
