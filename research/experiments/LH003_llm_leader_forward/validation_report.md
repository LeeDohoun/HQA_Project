# LH003 implementation validation

Worktree: `/home/are01/src/HQA_Project/HQA_lh003`.
Branch: `claude/lh003-runner`. HEAD remains `5db038c0acb203c8caf69d51171ae48dd26c36d9`; no worktree commit.
Preregistration is unchanged from `5db038c`; SHA-256:
`dc1ee3b669e515dc92b504eaf5d88061281b079f3c7f4fc5d7194dd5c37d190f`.

## Implemented

V3 prompts and decision-specific schemas; identifier leak checks; frozen A/common
inputs; fixed 52-week XKRX schedule and reviewed calendar hashes; three repeats
with one re-ask each; durable attempt cap and drift stop; single-claim protected
snapshot routing; crash/deadline recovery; cost-independent raw executions;
sealed v2 accounting and 200 random paths; NW(4) final policy inference; registry
publication/recovery; environment reports and CLI; ops allowlist.

The shared LH002 numerical table builder was extracted as a pure function so
LH003 can use verified snapshot sources. Existing LH001/LH002 source guards and
numerical definitions remain in place. The supplied preflight blocker document
is now a short operator/data open-items list.

## Tests

Broad offline regression: **663 passed, 1 skipped**, 114.39 s.
The existing skip is `tests/test_experiments_av001.py:850`, the optional
historical HC003 accounting comparison (no holdout).

```bash
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
venv/bin/python -m pytest -q -rs \
  tests/test_experiments_lh003_runner.py \
  tests/test_experiments_lh003_snapshot.py \
  tests/test_experiments_lh003_evaluate.py \
  tests/test_experiments_lh001.py tests/test_experiments_lh002.py \
  tests/test_experiments_ordering.py tests/test_experiments_av001.py \
  tests/test_backtesting_cost_model.py tests/test_backtesting_holdout.py
```

The final affected LH003 suites were rerun after the last snapshot/identity
checks: **71 passed**, 70.96 s. This includes an additional missing-machine-ID
check, a deterministic atime-change check and an actual suffix-change refusal.
A read changing only atime must not be mistaken for changed source content.
The content guard checks device/inode, size, mtime and ctime instead.

```bash
venv/bin/python -m pytest -q \
  tests/test_experiments_lh003_runner.py \
  tests/test_experiments_lh003_snapshot.py \
  tests/test_experiments_lh003_evaluate.py
```

`git diff --check` and `bash -n scripts/ops/research_run.sh` also passed.
These are fixture/fake tests, not strategy profitability or live-provider checks.
Regression tests use temporary fixture repositories and ledgers; no production
holdout session or LH003 claim was opened.

## Read-only dry checks

Source: `/home/are01/src/HQA_Project/HQA_Project/data`, strictly read-only.
Latest completed week: **2026-10-16**. Latest archived daily
filename: **2026-10-08**. The requested real-data input/model-fake
cycle was **skipped** because its decision-day KRX file is missing; no verified
LH003 protected snapshot was supplied. Real full-cycle runtime/peak RSS are
**not measured**. The metadata-only process metrics in its JSON report must not
be interpreted as a real input-cycle measurement.

- [Real-data availability report](reports/environment_real_data_20261017.json)
- [Synthetic full-cycle report](reports/environment_synthetic_20261017.json)

The synthetic stand-in used 252 sessions, 60 candidates, a labelled synthetic H1
snapshot and three injected Python fake calls. All sources/snapshots were under
`/tmp/`, never under `data/`, and the scratch tree was removed afterwards.

| Measured synthetic check | Value |
| --- | ---: |
| Whole command runtime | 8.639 s |
| Cycle including fixture/snapshot setup | 6.011 s |
| Peak process RSS including imports/setup | 119.207 MiB |
| Fake repeats | 3 |
| Real model calls / synthetic-format budget spent | 0 / 0 |
| Six-timeout timing bound including identity checks/setup | 1926.011 s |

The timing bound uses the pinned session-close assumption: an actual data-sync
trigger was not measured. Both environment reports have `passed=false` because
real-cycle/operational deployment evidence is incomplete. Synthetic memory and
runtime do not establish real-data resource requirements.

Both commands also ran behind an audit guard that rejected actual Codex process
creation, network access, `.env` opens, production HoldoutSession construction,
and any original source-file content open. Observed counts for all five were
zero. Source access here was directory/file metadata only. No main-checkout file
was written, no collector/service was started and no real-data prompt was sent.
The reports seal hashes of the current implementation; those hashes were checked
again when producing this validation report.

## Interpretations and remaining operator items

All implementation interpretations, the acquisition/verification bootstrap,
CLI examples, transfer-manifest fields, restart evidence, execution accounting,
calendar notes and trial-count rules are in
[implementation_notes.md](implementation_notes.md).

[IMPLEMENTATION_BLOCKERS.md](IMPLEMENTATION_BLOCKERS.md) lists only deployment,
data sync, the legally sourced 2027 tax update and the first-real-call commit gate.
2027 raw execution recording is tested without cost evaluation. Unknown 2027
trade/settlement tax dates fail closed before a statistical trial is consumed.
No rate was invented or carried forward from 2026.

## Files changed

The pre-existing untracked `venv` symlink is preserved and excluded below.
An ignored legacy `data/` tree was also already present at checkout creation
(21:32 KST, before LH003 implementation files at 21:37). It was preserved;
the requested archive checks explicitly used the main checkout's data directory.
No worktree `data/research/lh003/` state or canonical holdout ledger was created.

- `backtesting/experiments/lh001/inputs.py`
- `backtesting/experiments/lh003/__init__.py`
- `backtesting/experiments/lh003/__main__.py`
- `backtesting/experiments/lh003/calendar.py`
- `backtesting/experiments/lh003/environment.py`
- `backtesting/experiments/lh003/evaluate.py`
- `backtesting/experiments/lh003/executions.py`
- `backtesting/experiments/lh003/inputs.py`
- `backtesting/experiments/lh003/runner.py`
- `backtesting/experiments/lh003/simulation.py`
- `backtesting/experiments/lh003/snapshot.py`
- `backtesting/experiments/lh003/storage.py`
- `backtesting/experiments/lh003/workflow.py`
- `backtesting/experiments/ordering.py`
- `research/experiments/LH003_llm_leader_forward/IMPLEMENTATION_BLOCKERS.md`
- `research/experiments/LH003_llm_leader_forward/implementation_notes.md`
- `research/experiments/LH003_llm_leader_forward/prompts/README.md`
- `research/experiments/LH003_llm_leader_forward/prompts/v3_c_num.schema.json`
- `research/experiments/LH003_llm_leader_forward/prompts/v3_c_num.txt`
- `research/experiments/LH003_llm_leader_forward/prompts/v3_common.txt`
- `research/experiments/LH003_llm_leader_forward/prompts/v3_reask.txt`
- `research/experiments/LH003_llm_leader_forward/reports/environment_real_data_20261017.json`
- `research/experiments/LH003_llm_leader_forward/reports/environment_synthetic_20261017.json`
- `research/experiments/LH003_llm_leader_forward/validation_report.md`
- `scripts/ops/research_run.sh`
- `tests/test_experiments_lh003_evaluate.py`
- `tests/test_experiments_lh003_runner.py`
- `tests/test_experiments_lh003_snapshot.py`
