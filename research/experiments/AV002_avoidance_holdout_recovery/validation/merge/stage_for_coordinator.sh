#!/usr/bin/env bash
# Run from a session with write access to the live worktree Git directory.
# Stage only; the coordinator reviews and commits.
set -euo pipefail
cd /home/are01/src/HQA_Project/HQA_av002
unset GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
git add -- \
  backtesting/experiment_registry.py \
  backtesting/experiments/av002.py \
  backtesting/experiments/common.py \
  tests/test_experiments_av002.py \
  tests/test_trading_calendar_overrides.py \
  research/experiments/AV002_avoidance_holdout_recovery/MERGE_REVIEW_REPORT.md \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/guard.py \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/default_path_check.py \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/default_path_check.json \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/default_path_check.stderr.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/preflight.json \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/preflight.stderr.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/preflight.time.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/reproduce.json \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/reproduce.stderr.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/reproduce.time.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/final_tests.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/final_tests.stderr.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/final_tests.time.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/final_batch_tests.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/final_batch_tests.stderr.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/append_only_final.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/append_only_final.stderr.txt \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/protected_before.json \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/final_checks.json \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/stage_for_coordinator.sh \
  research/experiments/AV002_avoidance_holdout_recovery/validation/merge/isolated_git.sh
git add -- research/experiments/AV002_avoidance_holdout_recovery/validation/merge/finish_checks.py research/experiments/AV002_avoidance_holdout_recovery/validation/merge/stage_paths.json
git diff --cached --check
test -z "$(git ls-files --unmerged)"
git status --short --untracked-files=no
