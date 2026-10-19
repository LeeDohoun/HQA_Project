# BB003 implementation report — astra round 38

Implemented in `/home/are01/src/HQA_Project/HQA_bb003` at `f606fb3` plus the
pre-existing uncommitted BB003 work. This round updates **11 files**, adds none
and deletes none relative to the starting worktree. FW002 was consulted
read-only. No preregistration, FW001 inventory, main checkout, other worktree,
review file or protected input was modified; no staging/commit was performed.

**505 tests passed: 377 BB003 + 128 BB001/BB002.** This work is an offline
implementation fix. Actual-host systemd transactions, permissions,
concurrent timing and memory measurements remain deployment prerequisites.

## Round 38 behavior

| Finding | Implemented behavior |
| --- | --- |
| P1-1: FW002 job races | Removed both FW002 workers from BB003 Conflicts/After. Active/queued FW002 and due/unsealed decision/fill obligations deny admission, repeated after memory queries and before submission, after acquiring the lock, and before worker completion links. Morning rechecks after KRX streaming too. No shared atomic transaction is claimed; a later FW002 start may coexist with BB003. |
| FW001/shadow job contract | Only these two workers remain conflicting and ordered. Effective Type=oneshot and RemainAfterExit=no are verified with the existing flock contract. A running oneshot retains its start job, so BB003 job-mode=fail cannot replace it with stop. A priority timer/default replace start can cancel BB003's oneshot job; stop precedes start with at most five seconds stop grace. A priority fail start would instead be refused. |
| Concurrent memory | Activation reserves BB003's enforced effective cap plus the largest single FW002 worker's effective cap, then adds all installed dispatcher peaks and the resident peak. Measured concurrent peaks and serial FW001/shadow peaks are checked too; each measurement must fit its own unit/ancestor cap. Two 600 MiB caps reserve 1200 MiB before dispatchers/residents, refusing the 961 MiB NCP host even with smaller measured peaks. Synthetic 2048 MiB evidence requires 1200 + 230 + 250 = 1680 MiB. |
| P1-2: root audit ownership | Root missed() resolves hqa uid/gid, chowns newly created directories and the mkstemp inode, and applies 0750/0640 before linking the audit. All candidate/proof mkstemp files receive 0640 before publication. Tests simulate root ownership and hqa access at link time and the subsequent read, including umask 077. |
| P1-3: timer metadata | An elapsed, previously triggered OnBoot one-shot may have no next monotonic elapse while its calendar continues. Unknown/repeating/not-yet-elapsed/unfired rules without a next elapse still refuse admission. Formatted durations include d/w; raw numeric USec values remain supported. |
| P1-4: cgroup hierarchy | Discover the real v2 mount/root via mountinfo and unit/slice ControlGroup paths. Calculate physical RAM, configured/effective limits and all visible ancestor/unit memory.max limits. Missing memory.max only at the real hierarchy root means unlimited. Missing child/delegated-root/active-unit limits and permission/I/O/metadata errors fail clearly. Inactive units may have no cgroup yet; stable normalized snapshots still use their enforced manager limits. |
| P1-5: full registration | One FW002_MONTHS set covers all 36 months, 2026-10 through 2029-09, for entry plans, prospective decisions and all existing fill obligations. Out-of-registration files still fail. Tests exercise every month's partial/complete fills and entry plan, plus third-year decision gates through the final month. |

The source-bound memory evidence scope is now
`bb003_plus_one_fw002_worker_dispatchers_and_resident_services`; old
largest-single-heavy-job evidence is refused. Evidence still binds the final
host, current stable limits, exact manifest sources and private environment.
Activation snapshots it and seals the numerical budget. Dispatcher and installed
recording recheck it. Resident measurements must include any further permitted
priority overlap beyond the explicitly reserved pair. Full morning runtime
measurements must exercise simultaneous FW002 execution too.

Fixed receipt dates, activation cutoff, 08:59 decision deadline, experimental
rules and the previously corrected forward_aborted/uses_holdout:false behavior
are unchanged. No investment-performance or actual-host fit claim is made.

## Changed files

Counts compare with the preserved initial uncommitted files in
`/tmp/hqa-bb003-round38-before`, since these BB003 files are still untracked.

| Directory | Updated files | Count |
| --- | --- | ---: |
| `backtesting/experiments/bb003/` | `admission.py`, `deployment.py`, `resources.py`, `storage.py` | 4 |
| `deploy/collector-bb003/` | `README.md`, `runtime.py`, `systemd/hqa-bb003-daily.service` | 3 |
| `tests/` | `test_bb003_deployment.py`, `test_bb003_resources.py` | 2 |
| `research/experiments/BB003_buyback_forward/` | `implementation_notes.md`, `IMPLEMENTATION_REPORT.md` | 2 |
| **Total** | **11 updated; none added/deleted** | **11** |

All four supplied review files are unchanged. The BB003 preregistration SHA-256
remains `c5cf76a3e06f7815b455338c8e7c47f60726ae49b42da03fdacb1d4114333e87`.
No .env files, protected data or network were used. BB001/BB002 code/tests and
FW001/FW002 units remain unchanged.

## Verification

Pytest runs serially without xdist, bytecode or cache, under
`ulimit -v 1400000` (below 1.5 GB per process and inherited by test children).
Network is blocked by the existing test fixture. Commands:

```bash
ulimit -v 1400000
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 GIT_OPTIONAL_LOCKS=0 \
venv/bin/python -B -m pytest -q --tb=short -p no:cacheprovider \
  tests/test_bb003_recorder.py tests/test_bb003_evaluation.py \
  tests/test_bb003_deployment.py tests/test_bb003_resources.py
# 377 passed in 97.72s

PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 GIT_OPTIONAL_LOCKS=0 \
venv/bin/python -B -m pytest -q --tb=short -p no:cacheprovider \
  tests/test_experiments_bb001.py tests/test_experiments_bb002.py
# 128 passed in 29.96s
```

The final runs above passed **505 cases**. There are **91 new BB003 cases** and
12 obsolete FW002-stop cases removed, for **79 net additional cases** relative
to round 37. Earlier targeted deployment/resource runs also passed, including
the final 53 resource cases with FW002's escaped default template slice.

Job semantics were checked against the installed systemd.unit(5),
systemd.service(5) and systemctl(1) manuals (Conflicts ordering, Type start-job
completion and fail/replace reversal). Tests model those transaction rules and
both FW002 start orders. They do not run a host manager. The remaining **12**
real SIGTERM cases cover FW001/shadow only: two units x three link stages x two
retry times. The former 12 FW002-stop scenarios were removed because that
preemption contract is invalid for exec workers.

All **21** BB003 implementation/CLI/deployment Python files parse. The source
inventory still contains **125 files**. All **3** BB003 units pass native
systemd-analyze verify in a throwaway root with dependency/executable stubs;
no installed manager was queried. Tracked/staged diffs remain empty, and
git diff --check plus whitespace checks of all 11 changed untracked files pass.

## Remaining host work

Install the reviewed sources/private environment offline in the separate BB003
root. Measure full morning wall time below 180s and RSS below 600 MiB under
cold/warm concurrent FW002 load, and capture whole-cgroup worker/dispatcher and
resident memory with current RAM/hierarchy/unit limits. An undersized or
unmeasured host remains inactive; the existing 961 MiB host cannot pass with
the two 600 MiB worker caps. Validate real systemd job races, root-to-hqa file
access, interruption/retry and final-cutoff behavior without changing
FW001/FW002 units. Seal the deployment, calendar/tax and measurement evidence,
then activate before **2026-11-30 23:59:59 KST** or retain not_started.

Historical 6.282s / 302.328 MiB replay figures are incompatible with this source
inventory. No installation, activation or live systemd mutation was performed.
